"""Real filesystem/lock boundaries; the pure projector is separately tested."""
import asyncio
import copy
import fcntl
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.local_experiment.query_diagnose_report import run as R
from tools.local_experiment.query_diagnose_report import contract as REAL_CONTRACT

TOKEN = "1" * 32
SECOND = "2" * 32


class FilesystemContract:
    """Keep filesystem tests independent of the producer's rich payload schema."""
    MAX_RUNS = 128
    MAX_SUMMARY_BYTES = 2 * 1024 * 1024
    MAX_SPANS_BYTES = 1024 * 1024
    MAX_TOTAL_BYTES = 32 * 1024 * 1024
    MAX_PUBLIC_BYTES = 256 * 1024

    @staticmethod
    def project_run(token, summary, spans, *, read_state):
        if read_state == "stable":
            read_state = "passed" if summary is not None and spans is not None else "incomplete"
        return {"evidence_token": token, "state": read_state}

    @staticmethod
    def build_report(entries, *, requested_token, inventory):
        entries = sorted(entries, key=lambda item: item["evidence_token"])
        complete = inventory["complete"] and not inventory["unrecognized_entries"] and all(
            item["state"] in ("passed", "failed", "interrupted", "active") for item in entries)
        return {"schema_version": 1, "command": "query diagnose report",
                "selection": {"mode": "all" if requested_token is None else "single", "token": requested_token},
                "command_result": "complete" if complete else "incomplete",
                "inventory": dict(inventory), "runs": entries, "groups": [], "limitations": []}

    @staticmethod
    def validate_public(report):
        return copy.deepcopy(report)


class ReportFilesystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "saved"
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(R, "STATE", self.state).start()
        mock.patch.object(R, "C", FilesystemContract).start()

    def fixture(self, token=TOKEN):
        self.state.mkdir(mode=0o700, exist_ok=True)
        folder = self.state / token
        folder.mkdir(mode=0o700)
        for name, data in (("run.lock", b""), ("summary.json", b'{"saved":true}'),
                           ("spans.jsonl", b'{"span":1}\n')):
            self.write(folder / name, data)
        return folder

    def write(self, path, data):
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as file:
            file.write(data)

    def marks(self):
        return {str(p.relative_to(self.root)): (R.fingerprint(p.stat()), p.read_bytes() if p.is_file() else None)
                for p in self.root.rglob("*")}

    def cli(self, request=None, args=None):
        output = io.StringIO()
        body = json.dumps({"token": request}).encode()
        code = R.main(["report", "--request-stdin"] if args is None else args,
                      stdin=io.BytesIO(body), stdout=output)
        return code, json.loads(output.getvalue()), output.getvalue()

    def test_absent_all_empty_complete_single_missing_and_no_creation(self):
        code, report, _ = self.cli()
        self.assertEqual((code, report["inventory"]["root"], report["runs"]), (0, "absent", []))
        code, report, _ = self.cli(TOKEN)
        self.assertEqual((code, report["runs"][0]["state"]), (1, "missing"))
        self.assertFalse(self.state.exists())

    def test_all_single_repeat_are_equal_and_do_not_change_any_file(self):
        self.fixture()
        self.fixture(SECOND)
        before = self.marks()
        all_report = R.collect()
        single = R.collect(TOKEN)
        self.assertEqual(all_report["runs"][0], single["runs"][0])
        self.assertEqual(R.collect(), all_report)
        self.assertEqual(self.marks(), before)

    def test_busy_is_active_and_payload_is_never_read(self):
        folder = self.fixture()
        fd = os.open(folder / "run.lock", os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with mock.patch.object(R, "read_file", side_effect=AssertionError("payload read")):
                report = R.collect(TOKEN)
            self.assertEqual(report["runs"][0]["state"], "active")
            self.assertEqual(report["command_result"], "complete")
        finally:
            os.close(fd)

    def test_busy_old_lock_inode_cannot_authenticate_replaced_lock_path(self):
        folder = self.fixture()
        fd = os.open(folder / "run.lock", os.O_RDONLY)
        real_flock = fcntl.flock
        try:
            real_flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            def changed(target, flags):
                if flags == fcntl.LOCK_SH | fcntl.LOCK_NB:
                    (folder / "run.lock").unlink()
                    self.write(folder / "run.lock", b"")
                return real_flock(target, flags)
            with mock.patch.object(R.fcntl, "flock", side_effect=changed), mock.patch.object(R, "read_file", side_effect=AssertionError("payload read")):
                report = R.collect(TOKEN)
            self.assertEqual(report["runs"][0]["state"], "invalid")
            replacement = os.open(folder / "run.lock", os.O_RDONLY)
            try:
                real_flock(replacement, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(replacement)
        finally:
            os.close(fd)

    def test_busy_writer_payload_changes_remain_active_without_reading(self):
        folder = self.fixture()
        fd = os.open(folder / "run.lock", os.O_RDONLY)
        real_flock = fcntl.flock
        try:
            real_flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            def changed(target, flags):
                if flags == fcntl.LOCK_SH | fcntl.LOCK_NB:
                    (folder / "summary.json").unlink()
                    self.write(folder / "temporary-private-artifact", b"never read")
                return real_flock(target, flags)
            with mock.patch.object(R.fcntl, "flock", side_effect=changed), mock.patch.object(R, "read_file", side_effect=AssertionError("payload read")):
                self.assertEqual(R.collect(TOKEN)["runs"][0]["state"], "active")
        finally:
            os.close(fd)

    def test_missing_lock_or_payload_is_incomplete_and_not_created(self):
        for name in ("run.lock", "summary.json", "spans.jsonl"):
            with self.subTest(name=name):
                folder = self.fixture(str(("run.lock", "summary.json", "spans.jsonl").index(name) + 3) * 32)
                (folder / name).unlink()
                before = self.marks()
                self.assertEqual(R.collect(folder.name)["runs"][0]["state"], "incomplete")
                self.assertEqual(before, self.marks())

    def test_root_and_parent_symlinks_are_not_followed(self):
        actual = self.root / "actual"
        actual.mkdir(mode=0o700)
        self.state.symlink_to(actual, target_is_directory=True)
        self.assertEqual(R.collect()["inventory"]["root"], "not-observed")
        self.state.unlink()
        parent = self.root / "link"
        parent.symlink_to(actual, target_is_directory=True)
        with mock.patch.object(R, "STATE", parent / "missing"):
            self.assertEqual(R.collect()["inventory"]["root"], "not-observed")

    def test_token_symlink_and_regular_file_are_invalid(self):
        folder = self.fixture()
        (self.state / SECOND).symlink_to(folder, target_is_directory=True)
        self.assertEqual(R.collect(SECOND)["runs"][0]["state"], "invalid")
        (self.state / SECOND).unlink()
        self.write(self.state / SECOND, b"do not read")
        self.assertEqual(R.collect(SECOND)["runs"][0]["state"], "invalid")

    def test_each_artifact_symlink_or_hardlink_is_invalid(self):
        for i, name in enumerate(("summary.json", "spans.jsonl", "run.lock")):
            folder = self.fixture(str(i + 3) * 32)
            external = self.root / ("external" + str(i))
            self.write(external, b"private sentinel")
            (folder / name).unlink()
            (folder / name).symlink_to(external)
            self.assertEqual(R.collect(folder.name)["runs"][0]["state"], "invalid")
            (folder / name).unlink()
            os.link(external, folder / name)
            self.assertEqual(R.collect(folder.name)["runs"][0]["state"], "invalid")

    def test_root_token_and_file_modes_are_strict(self):
        folder = self.fixture()
        for path in (self.state, folder, folder / "run.lock", folder / "summary.json", folder / "spans.jsonl"):
            original = path.stat().st_mode & 0o777
            path.chmod(0o755 if path.is_dir() else 0o644)
            result = R.collect(TOKEN)
            self.assertEqual(result["command_result"], "incomplete")
            path.chmod(original)

    def test_unknown_names_are_counted_but_never_published(self):
        self.fixture()
        secret = "UNTRUSTED_ENTRY_SENTINEL"
        self.write(self.state / secret, b"secret")
        code, report, output = self.cli()
        self.assertEqual(code, 1)
        self.assertEqual(report["inventory"]["unrecognized_entries"], 1)
        self.assertNotIn(secret, output)

    def test_unexpected_artifact_is_invalid_without_exposing_name(self):
        folder = self.fixture()
        self.write(folder / "UNTRUSTED_ARTIFACT", b"secret")
        _, report, output = self.cli(TOKEN)
        self.assertEqual(report["runs"][0]["state"], "invalid")
        self.assertNotIn("UNTRUSTED_ARTIFACT", output)

    def test_duplicate_nan_bad_json_and_deep_json_are_invalid(self):
        folder = self.fixture()
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'not json', b'[' * 2000 + b']' * 2000):
            self.write(folder / "summary.json", raw)
            self.assertEqual(R.collect(TOKEN)["runs"][0]["state"], "invalid")
        self.write(folder / "summary.json", b"{}")
        self.write(folder / "spans.jsonl", b'{"a":1,"a":2}\n')
        self.assertEqual(R.collect(TOKEN)["runs"][0]["state"], "invalid")

    def test_same_size_bytes_changed_with_restored_mtime_are_invalid(self):
        folder = self.fixture()
        original = FilesystemContract.project_run
        def mutation(token, summary, spans, *, read_state):
            if read_state == "stable":
                path = folder / "summary.json"
                stamp = path.stat()
                self.write(path, b'{"saved":null}')
                os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            return original(token, summary, spans, read_state=read_state)
        with mock.patch.object(FilesystemContract, "project_run", side_effect=mutation):
            self.assertEqual(R.collect(TOKEN)["runs"][0]["state"], "invalid")

    def test_replaced_lock_identity_is_invalid(self):
        folder = self.fixture()
        original = FilesystemContract.project_run
        def mutation(token, summary, spans, *, read_state):
            if read_state == "stable":
                (folder / "run.lock").unlink()
                self.write(folder / "run.lock", b"")
            return original(token, summary, spans, read_state=read_state)
        with mock.patch.object(FilesystemContract, "project_run", side_effect=mutation):
            self.assertEqual(R.collect(TOKEN)["runs"][0]["state"], "invalid")

    def test_directory_listing_change_makes_inventory_incomplete(self):
        self.fixture()
        original = FilesystemContract.project_run
        def mutation(token, summary, spans, *, read_state):
            if read_state == "stable":
                self.write(self.state / "new-entry", b"")
            return original(token, summary, spans, read_state=read_state)
        with mock.patch.object(FilesystemContract, "project_run", side_effect=mutation):
            self.assertFalse(R.collect()["inventory"]["complete"])

    def test_file_and_total_byte_limits_are_not_silent_success(self):
        self.fixture()
        with mock.patch.object(FilesystemContract, "MAX_SUMMARY_BYTES", 3):
            self.assertEqual(R.collect()["runs"][0]["state"], "incomplete")
        with mock.patch.object(FilesystemContract, "MAX_TOTAL_BYTES", 25):
            self.assertEqual(R.collect()["runs"][0]["state"], "incomplete")

    def test_run_and_directory_limits_are_incomplete(self):
        for token in (TOKEN, SECOND, "3" * 32):
            self.fixture(token)
        with mock.patch.object(FilesystemContract, "MAX_RUNS", 2):
            report = R.collect()
            self.assertEqual(len(report["runs"]), 2)
            self.assertEqual(report["command_result"], "incomplete")
        with mock.patch.object(R, "MAX_DIRECTORY_ENTRIES", 2):
            self.assertFalse(R.collect()["inventory"]["complete"])

    def test_locks_are_held_through_projection_but_released_before_stdout(self):
        folder = self.fixture()
        original = FilesystemContract.build_report
        def build(*args, **kwargs):
            fd = os.open(folder / "run.lock", os.O_RDONLY)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)
            return original(*args, **kwargs)
        class Output(io.StringIO):
            def write(output, value):
                fd = os.open(folder / "run.lock", os.O_RDONLY)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(fd)
                return super().write(value)
        with mock.patch.object(FilesystemContract, "build_report", side_effect=build):
            self.assertEqual(R.main(["report", "--request-stdin"], stdin=io.BytesIO(b'{"token":null}'), stdout=Output()), 0)

    def test_first_cancel_survives_secondary_close_failure(self):
        self.fixture()
        signal = asyncio.CancelledError("original")
        real_close = os.close
        def close(fd):
            real_close(fd)
            raise OSError("secondary")
        patcher = mock.patch.object(R.os, "close", side_effect=close)
        def interrupted(*args):
            patcher.start()
            raise signal
        try:
            # Every cleanup close now fails while the same original signal unwinds.
            with mock.patch.object(R, "payload", side_effect=interrupted):
                with self.assertRaises(asyncio.CancelledError) as caught:
                    R.collect()
                self.assertIs(caught.exception, signal)
        finally:
            patcher.stop()
        fd = os.open(self.state / TOKEN / "run.lock", os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)

    def test_main_cancel_returns_one_incomplete_frame_and_130(self):
        with mock.patch.object(R, "collect", side_effect=KeyboardInterrupt("private")):
            code, report, output = self.cli()
        self.assertEqual(code, 130)
        self.assertEqual(report["command_result"], "incomplete")
        self.assertEqual(len(output.splitlines()), 1)
        self.assertNotIn("private", output)

    def test_invalid_request_and_arguments_never_scan(self):
        cases = [b'{"token":"BAD"}', b'{"token":1}', b'{"token":null,"extra":1}',
                 b'{"token":null,"token":null}', b'[]', b' ' * 1025]
        with mock.patch.object(R, "collect", side_effect=AssertionError("must not scan")) as collect:
            for raw in cases:
                output = io.StringIO()
                self.assertEqual(R.main(["report", "--request-stdin"], stdin=io.BytesIO(raw), stdout=output), 1)
                self.assertEqual(json.loads(output.getvalue())["inventory"]["root"], "not-observed")
            self.assertEqual(self.cli(args=["--arbitrary"])[0], 1)
            collect.assert_not_called()

    def test_every_open_is_read_only_and_never_creates_a_file(self):
        self.fixture()
        real_open = os.open
        calls = []
        def opened(path, flags, *args, **kwargs):
            calls.append(flags)
            self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            return real_open(path, flags, *args, **kwargs)
        with mock.patch.object(R.os, "open", side_effect=opened):
            self.assertEqual(self.cli()[0], 0)
        self.assertTrue(calls)

    def test_actual_contract_absent_single_invalid_and_busy_protocol(self):
        with mock.patch.object(R, "C", REAL_CONTRACT):
            code, report, _ = self.cli()
            self.assertEqual(code, 0)
            REAL_CONTRACT.validate_public(report)
            code, report, _ = self.cli(TOKEN)
            self.assertEqual((code, report["inventory"]["inspected_entries"], report["runs"][0]["state"]), (1, 1, "missing"))
            REAL_CONTRACT.validate_public(report)
            folder = self.fixture()
            code, report, _ = self.cli(TOKEN)
            self.assertEqual((code, report["runs"][0]["state"]), (1, "invalid"))
            fd = os.open(folder / "run.lock", os.O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                code, report, _ = self.cli(TOKEN)
                self.assertEqual((code, report["runs"][0]["state"]), (0, "active"))
                REAL_CONTRACT.validate_public(report)
            finally:
                os.close(fd)

    def test_actual_contract_request_root_and_read_limit_codes(self):
        with mock.patch.object(R, "C", REAL_CONTRACT):
            code, report, _ = self.cli(args=["unknown"])
            self.assertEqual((code, report["inventory"]["error_code"]), (1, "REPORT_REQUEST_INVALID"))
            self.fixture()
            self.state.chmod(0o755)
            code, report, _ = self.cli(TOKEN)
            self.assertEqual((code, report["inventory"]["error_code"]), (1, "REPORT_ROOT_INVALID"))
            REAL_CONTRACT.validate_public(report)
            self.state.chmod(0o700)
            with mock.patch.object(REAL_CONTRACT, "MAX_TOTAL_BYTES", 1):
                code, report, _ = self.cli(TOKEN)
            self.assertEqual((code, report["inventory"]["error_code"]), (1, "REPORT_READ_LIMIT"))
            REAL_CONTRACT.validate_public(report)

    def full_fixture(self, summary, spans):
        folder = self.fixture(summary["evidence_token"])
        self.write(folder / "summary.json", json.dumps(summary, ensure_ascii=True).encode())
        self.write(folder / "spans.jsonl", b"".join(json.dumps(span).encode() + b"\n" for span in spans))
        return folder

    def test_actual_contract_terminal_v1_v2_and_failure_without_original_sources(self):
        from test_query_diagnose_report_contract import documents, failed_documents
        pair1 = documents(profile=1, identity=TOKEN)
        pair2 = documents(profile=2, identity=SECOND)
        failure = failed_documents()
        for pair in (pair1, pair2, failure):
            self.full_fixture(*pair)
        before = self.marks()
        with mock.patch.object(R, "C", REAL_CONTRACT):
            code, all_report, _ = self.cli()
            self.assertEqual(code, 0)
            self.assertEqual(all_report["inventory"]["counts"]["passed"], 2)
            self.assertEqual(all_report["inventory"]["counts"]["failed"], 1)
            self.assertEqual({x["source_profile"] for x in all_report["runs"]}, {"reader-v1", "snapshot-reader-v2"})
            for row in all_report["runs"]:
                single_code, single, _ = self.cli(row["evidence_token"])
                self.assertEqual(single_code, 0)
                self.assertEqual(single["runs"], [row])
                REAL_CONTRACT.validate_public(single)
        self.assertEqual(before, self.marks())
        self.assertEqual(set(self.root.iterdir()), {self.state})

    def test_actual_public_limit_yields_fixed_bounded_incomplete_frame(self):
        from test_query_diagnose_report_contract import documents
        self.full_fixture(*documents(identity=TOKEN))
        with mock.patch.object(R, "C", REAL_CONTRACT):
            full = R.collect(TOKEN)
            small = R.fallback(TOKEN, "REPORT_PUBLIC_LIMIT")
            size = lambda x: len(json.dumps(x, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()) + 1
            cap = (size(full) + size(small)) // 2
            self.assertLess(size(small), cap)
            with mock.patch.object(REAL_CONTRACT, "MAX_PUBLIC_BYTES", cap):
                code, report, output = self.cli(TOKEN)
                REAL_CONTRACT.validate_public(report)
            self.assertEqual((code, report["command_result"], report["inventory"]["error_code"]),
                             (1, "incomplete", "REPORT_PUBLIC_LIMIT"))
            self.assertLessEqual(len(output.encode()), cap)
            self.assertEqual(len(output.splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
