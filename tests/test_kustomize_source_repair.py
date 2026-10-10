"""Offline Python fixtures only: no network, Go, retained scopes or model calls."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment import kustomize_source_repair as repair


MOD = (f'module {repair.MODULE}\n\ngo 1.26.0\n\nrequire (\n'
       '\tgolang.org/x/crypto v0.53.0 // indirect\n'
       '\tgoogle.golang.org/grpc v1.81.1 // indirect\n'
       '\texample.org/unchanged v1.2.3 // indirect\n)\n').encode()
SUM = b'fixture retained original sums only\n'


def receipt(name, data):
    return {'schema_version': 1, 'outcome': 'retrieved',
            'url': repair.BASE_URL + name, 'revision': repair.REVISION,
            'bytes': len(data), 'sha256': repair.digest(data),
            'started_unix': 1.0, 'finished_unix': 1.1, 'publisher_verified': False}


class Response:
    status = 200

    def __init__(self, data=MOD, headers=None, url=None):
        self.data = data
        self.headers = headers or {}
        self.url = url or repair.BASE_URL + 'go.mod'
        self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(
            settimeout=lambda remaining: None)))

    def geturl(self):
        return self.url

    def isclosed(self):
        return False

    def read1(self, size):
        result, self.data = self.data[:size], self.data[size:]
        return result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.temp.cleanup)
        self.scope = Path(self.temp.name)
        self.scope.chmod(0o700)
        hashes = {'go.mod': repair.digest(MOD), 'go.sum': repair.digest(SUM)}
        self.hash_patch = patch.object(repair, 'SOURCE_HASHES', hashes)
        self.hash_patch.start()
        self.addCleanup(self.hash_patch.stop)
        self.receipts = {'go.mod': receipt('go.mod', MOD), 'go.sum': receipt('go.sum', SUM)}

    def test_exact_two_line_patch_and_unchanged_sums(self):
        mod, diff = repair.patch_source(MOD, SUM)
        self.assertEqual(mod, MOD.replace(b'v0.53.0', b'v0.55.0').replace(b'v1.81.1', b'v1.83.2'))
        self.assertIn(b'--- a/go.mod\n+++ b/go.mod\n', diff)
        record = repair.provenance(MOD, SUM, mod, diff, self.receipts)
        repair.validate_provenance(record, MOD, SUM, mod, diff, self.receipts)
        self.assertEqual(record['status'], repair.UNRESOLVED)
        self.assertEqual(record['go_sum_unchanged_sha256'], repair.digest(SUM))

    def test_source_tampering(self):
        for mod, sums in ((MOD + b'\n', SUM), (MOD, SUM + b'x')):
            with self.subTest(mod=mod), self.assertRaisesRegex(repair.RepairError, 'HASH'):
                repair.patch_source(mod, sums)

    def test_invalid_module_lines_even_if_hash_attested(self):
        for modified in (
            MOD.replace(repair.MODULE.encode(), b'example.org/wrong'),
            MOD.replace(b'v0.53.0', b'v0.54.0'),
            MOD.replace(b' // indirect', b''),
            MOD.replace(b'\tgolang.org/x/crypto', b' golang.org/x/crypto'),
            MOD.replace(b')\n', b'\tgolang.org/x/crypto v0.53.0 // indirect\n)\n'),
            MOD.replace(b'\n', b'\r\n'), MOD.rstrip(),
        ):
            with self.subTest(modified=modified), patch.object(
                repair, 'SOURCE_HASHES', {'go.mod': repair.digest(modified), 'go.sum': repair.digest(SUM)}
            ), self.assertRaises(repair.RepairError):
                repair.patch_source(modified, SUM)

    def test_extra_patch_changes_reject(self):
        mod, diff = repair.patch_source(MOD, SUM)
        for changed_mod, changed_diff in ((mod + b'\n', diff), (mod, diff + b'\n'),
                                          (mod.replace(b'v1.2.3', b'v1.2.4'), diff)):
            with self.subTest(changed_mod=changed_mod), self.assertRaisesRegex(
                repair.RepairError, 'PATCH_MISMATCH'
            ):
                repair.provenance(MOD, SUM, changed_mod, changed_diff, self.receipts)

    def test_receipt_identity_and_time_tampering(self):
        mod, diff = repair.patch_source(MOD, SUM)
        for field, value in (('schema_version', True), ('revision', 'main'), ('url', 'https://example.org/go.mod'),
                             ('bytes', 0), ('sha256', '0' * 64), ('outcome', 'unknown'),
                             ('publisher_verified', True), ('finished_unix', 99),
                             ('started_unix', 2), ('started_unix', True)):
            receipts = copy.deepcopy(self.receipts)
            receipts['go.mod'][field] = value
            with self.subTest(field=field), self.assertRaises(repair.RepairError):
                repair.provenance(MOD, SUM, mod, diff, receipts)

    def test_provenance_claim_inflation_or_omission_rejects(self):
        mod, diff = repair.patch_source(MOD, SUM)
        original = repair.provenance(MOD, SUM, mod, diff, self.receipts)
        for key in repair.UNRESOLVED:
            record = copy.deepcopy(original)
            record['status'][key] = 'resolved'
            with self.subTest(key=key), self.assertRaisesRegex(repair.RepairError, 'PROVENANCE'):
                repair.validate_provenance(record, MOD, SUM, mod, diff, self.receipts)
        record = copy.deepcopy(original)
        del record['source']
        with self.assertRaises(repair.RepairError):
            repair.validate_provenance(record, MOD, SUM, mod, diff, self.receipts)
        record = copy.deepcopy(original)
        record['schema_version'] = True
        with self.assertRaises(repair.RepairError):
            repair.validate_provenance(record, MOD, SUM, mod, diff, self.receipts)

    def test_exclusive_private_writes(self):
        repair.write_once(self.scope, 'receipt.json', b'{}')
        self.assertEqual((self.scope / 'receipt.json').stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            repair.write_once(self.scope, 'receipt.json', b'new')
        self.assertEqual((self.scope / 'receipt.json').read_bytes(), b'{}')

    def test_symlink_and_hardlink_reject(self):
        (self.scope / 'link').symlink_to(self.scope / 'absent')
        with self.assertRaises(repair.RepairError):
            repair.budget(self.scope)
        with self.assertRaises(FileExistsError):
            repair.write_once(self.scope, 'link', b'new')
        alias = self.scope / 'alias'
        alias.symlink_to(self.scope, target_is_directory=True)
        with self.assertRaisesRegex(repair.RepairError, 'SYMLINK'):
            repair.write_once(alias, 'other', b'new')

    def test_hardlink_budget_rejects(self):
        repair.write_once(self.scope, 'a', b'fixture')
        os.link(self.scope / 'a', self.scope / 'b')
        with self.assertRaisesRegex(repair.RepairError, 'UNSAFE'):
            repair.budget(self.scope)

    def test_filename_and_size_reject(self):
        for name in ('../go.mod', 'a/b', ''):
            with self.subTest(name=name), self.assertRaises(repair.RepairError):
                repair.write_once(self.scope, name, b'x')
        with self.assertRaises(repair.RepairError):
            repair.write_once(self.scope, 'large', b'x' * (repair.MAX_RESPONSE + 1))

    def test_private_scope_required(self):
        self.scope.chmod(0o755)
        with self.assertRaisesRegex(repair.RepairError, 'PRIVATE'):
            repair.write_once(self.scope, 'a', b'x')

    def test_budget_limits(self):
        with patch.object(repair.os, 'fstatvfs', return_value=SimpleNamespace(
            f_bavail=repair.FREE_MIN + repair.SCOPE_LIMIT, f_frsize=1
        )):
            repair.budget(self.scope)
            with patch.object(repair, 'PARENT_RETAINED_BYTES', repair.COMBINED_LIMIT):
                with self.assertRaisesRegex(repair.RepairError, 'COMBINED'):
                    repair.budget(self.scope)
        with patch.object(repair.os, 'fstatvfs', return_value=SimpleNamespace(f_bavail=0, f_frsize=1)):
            with self.assertRaisesRegex(repair.RepairError, 'FREE'):
                repair.budget(self.scope)
        with patch.object(repair, 'SCOPE_LIMIT', 0):
            with self.assertRaisesRegex(repair.RepairError, 'SCOPE'):
                repair.budget(self.scope)

    def mocked_fetch(self, response):
        opener = SimpleNamespace(open=lambda request, timeout: response)
        with patch.object(repair, 'budget', return_value={}), patch.object(
            repair.urllib.request, 'build_opener', return_value=opener
        ) as mocked:
            result = repair.fetch(self.scope, 'go.mod')
            self.assertEqual(mocked.call_count, 1)
            return result

    def test_fetch_success_and_no_replay(self):
        self.assertEqual(self.mocked_fetch(Response())['sha256'], repair.digest(MOD))
        with patch.object(repair, 'budget', return_value={}), patch.object(
            repair.urllib.request, 'build_opener'
        ) as network, self.assertRaises(FileExistsError):
            repair.fetch(self.scope, 'go.mod')
        network.assert_not_called()

    def test_unknown_started_fetch_no_replay(self):
        repair.write_once(self.scope, 'go.mod.fetch-started.json', b'{}')
        with patch.object(repair, 'budget', return_value={}), patch.object(
            repair.urllib.request, 'build_opener'
        ) as network, self.assertRaises(FileExistsError):
            repair.fetch(self.scope, 'go.mod')
        network.assert_not_called()

    def test_oversize_response(self):
        with self.assertRaisesRegex(repair.RepairError, 'TOO_LARGE'):
            self.mocked_fetch(Response(b'x' * (repair.MAX_RESPONSE + 1)))
        self.assertFalse((self.scope / 'go.mod').exists())
        self.assertTrue((self.scope / 'go.mod.fetch-held.json').exists())

    def test_truncated_response(self):
        with self.assertRaisesRegex(repair.RepairError, 'TRUNCATED'):
            self.mocked_fetch(Response(headers={'Content-Length': '999'}))

    def test_wrong_final_url(self):
        with self.assertRaisesRegex(repair.RepairError, 'HTTP_IDENTITY'):
            self.mocked_fetch(Response(url='https://example.org/go.mod'))

    def test_timeout_no_success_receipt(self):
        with patch.object(repair.time, 'monotonic', side_effect=[0, 31]), self.assertRaisesRegex(
            repair.RepairError, 'TIMEOUT'
        ):
            self.mocked_fetch(Response())
        self.assertFalse((self.scope / 'go.mod.fetch-finished.json').exists())

    def test_only_two_fixed_filenames(self):
        for name in ('api/go.mod', 'go.zip', '../go.sum', 'main/go.mod'):
            with self.subTest(name=name), self.assertRaises(repair.RepairError):
                repair.fetch(self.scope, name)

    def test_redirect_denied(self):
        with self.assertRaisesRegex(repair.RepairError, 'REDIRECT'):
            repair.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.org')

    def seed(self):
        for name, data in (('go.mod', MOD), ('go.sum', SUM)):
            repair.write_once(self.scope, name, data)
            repair.write_once(self.scope, name + '.fetch-finished.json', repair.encode(self.receipts[name]))
            repair.write_once(self.scope, name + '.fetch-started.json', repair.encode({
                'schema_version': 1, 'revision': repair.REVISION, 'url': repair.BASE_URL + name,
                'started_unix': 1.0, 'timeout_seconds': 30, 'max_response_bytes': repair.MAX_RESPONSE}))

    def test_materialize_and_disk_validation(self):
        self.seed()
        record = repair.materialize(self.scope)
        self.assertEqual(repair.validate_scope(self.scope), record)
        with self.assertRaises(FileExistsError):
            repair.materialize(self.scope)
        # Fixture corruption is not permitted against retained real evidence.
        (self.scope / 'module.patch').write_bytes(b'corrupt')
        with self.assertRaisesRegex(repair.RepairError, 'PATCH'):
            repair.validate_scope(self.scope)

    def test_start_receipt_mismatch(self):
        self.seed()
        start = self.scope / 'go.mod.fetch-started.json'
        record = json.loads(start.read_bytes())
        record['revision'] = 'main'
        start.write_bytes(repair.encode(record))
        with self.assertRaisesRegex(repair.RepairError, 'START'):
            repair.materialize(self.scope)

    def test_existing_deadline_is_not_overridden(self):
        with patch.object(repair.signal, 'getitimer', return_value=(5.0, 0.0)), patch.object(
            repair.signal, 'setitimer'
        ) as timer, self.assertRaisesRegex(repair.RepairError, 'ACTIVE_TIMER'):
            with repair.fetch_deadline():
                self.fail('existing timer must hold')
        timer.assert_not_called()

    def test_read_rejects_nonprivate_and_symlink(self):
        repair.write_once(self.scope, 'data', b'fixture')
        (self.scope / 'data').chmod(0o644)
        with self.assertRaisesRegex(repair.RepairError, 'PRIVATE_ARTIFACT'):
            repair.read_private(self.scope, 'data')
        (self.scope / 'alias').symlink_to(self.scope / 'data')
        with self.assertRaises(OSError):
            repair.read_private(self.scope, 'alias')


if __name__ == '__main__':
    unittest.main()
