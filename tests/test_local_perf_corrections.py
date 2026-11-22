"""Focused review regressions; stdlib only, no extension downloads or sockets."""
import json
import errno
import http.client
import io
import sqlite3
import tempfile
import time
import threading
from decimal import Decimal, localcontext
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import MagicMock, patch

from benchmarks.local_perf.capacity import Campaign
from benchmarks.local_perf.dataset import generate, packaged_column_binding, qualify_packaged_tpch
from benchmarks.local_perf.engine import load_extension
from benchmarks.local_perf.protocol import LIMIT, plan, protocol, settings, validate
from benchmarks.local_perf.results import Writer, cell, column, compare
from benchmarks.local_perf.runner import DuckDBAdapter, TrinoAdapter, reconcile, run, sql_identity
from tests.test_local_perf_runner import ScriptedTrino
from tests.local_perf_host import setUpModule, tearDownModule  # noqa: F401


class ReviewTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.c = Campaign.create(self.root / "campaign")

    def tearDown(self):
        self.tmp.cleanup()

    def test_sf1000_evidence_plan_without_generation(self):
        cfg = settings("performance", 1000, 1_500_000_000_000, 900, 1,
                       "SF1 pilot measured source/export/spill/results; extrapolated peak 1.5TB with headroom")
        self.assertEqual(cfg["logical_bytes"], 1_000_000_000_000)
        self.assertEqual(cfg["peak_bytes"], 1_500_000_000_000)
        self.assertGreater(cfg["default_peak_estimate_bytes"], LIMIT)
        self.assertIn("pilot", cfg["peak_evidence"])
        for kwargs in (dict(scale=1000), dict(scale=2001, peak=1), dict(scale=1, peak=LIMIT+1),
                       dict(scale=1, peak=0), dict(scale=1, peak=1, peak_evidence="")):
            with self.assertRaises(ValueError):
                settings(mode="performance", timeout=1, repetitions=1, **kwargs)
        p = protocol()
        p["host_ram_reserves_gib"]["generator"] = 12
        with patch("benchmarks.local_perf.protocol.protocol", return_value=p):
            planned = plan()
        self.assertEqual(planned["remaining_host_ram_gib"], 512-192-84)
        self.assertEqual(planned["remaining_vm_cpu"], 8)
        self.assertEqual(planned["remaining_vm_ram_gib"], 64)
        for value in (0, -1, float("inf"), float("nan"), True):
            p = protocol()
            p["profiles"]["L1"]["worker_cpu"] = value
            with self.assertRaises(ValueError):
                validate(p)

    def test_reject_multi_volume_before_campaign_created(self):
        image = self.root / "image"
        image.touch()
        original = Path.stat
        def stat(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path == image:
                fake = MagicMock(st_dev=result.st_dev + 1)
                return fake
            return result
        with patch.object(Path, "stat", stat):
            with self.assertRaisesRegex(ValueError, "multi-volume"):
                Campaign.create(self.root / "other", images=[image])
        self.assertFalse((self.root / "other").exists())

    def test_signed_official_cache_and_fresh_core_bootstrap(self):
        for source, installed, expected_install in (("core", True, False),
                                                    ("https://extensions.duckdb.org", True, False),
                                                    (None, False, True), ("community", True, True)):
            with self.subTest(source=source):
                con = MagicMock()
                con.execute.return_value.fetchone.side_effect = [(installed, source), ("v1.4.3", "core")]
                directory = self.root / ("ext-" + str(source).replace("/", "_").replace(":", "_"))
                load_extension(con, "tpch", directory)
                sql = [c.args[0] for c in con.execute.call_args_list]
                self.assertEqual("INSTALL tpch FROM core" in sql, expected_install)
                self.assertIn("LOAD tpch", sql)
                self.assertFalse(any("https://" in x for x in sql))
        with self.assertRaises(ValueError):
            load_extension(MagicMock(), "unsigned", self.root)
        con = MagicMock()
        con.execute.return_value.fetchone.return_value = (False, None)
        def fail(sql, *args):
            if sql.startswith("INSTALL"):
                raise OSError("offline")
            return con.execute.return_value
        con.execute.side_effect = fail
        with self.assertRaises(OSError):
            load_extension(con, "tpch", self.root / "offline")
        self.assertFalse(any(c.args[0] == "LOAD tpch" for c in con.execute.call_args_list))

    def test_generator_extension_dbgen_export_failures_cleanup(self):
        for stage in ("extension", "dbgen", "export"):
            con = MagicMock()
            def execute(sql, *args):
                if sql.startswith("CALL dbgen") and stage == "dbgen":
                    raise RuntimeError("generator failed")
                if sql.startswith("COPY"):
                    raise RuntimeError("export failed")
                cursor = MagicMock()
                if "duckdb_functions" in sql:
                    cursor.fetchall.return_value = []
                elif "tpch_queries" in sql:
                    cursor.fetchall.return_value = [(n, "SELECT 1") for n in range(1, 23)]
                elif sql == "SHOW TABLES":
                    cursor.fetchall.return_value = [("t" + str(i),) for i in range(8)]
                elif sql.startswith("DESCRIBE"):
                    cursor.fetchall.return_value = [("x", "INTEGER")]
                else:
                    cursor.fetchone.return_value = (1,)
                return cursor
            con.execute.side_effect = execute
            con.extract_statements.return_value = [object()]
            def connect(path, work, **kwargs):
                path.touch()
                work.mkdir()
                (work / "temp").write_text("owned")
                return con
            with patch("benchmarks.local_perf.dataset.connect", side_effect=connect), patch(
                    "benchmarks.local_perf.dataset.load_extension", side_effect=RuntimeError("extension") if stage == "extension" else None, return_value={"extension_version":"v1.4.3","installed_from":"core"}):
                with self.assertRaises(RuntimeError):
                    generate(self.c, stage)
            out = self.c.root / stage
            meta = json.loads((out / "dataset.json").read_text())
            self.assertEqual(meta["status"], "FAILED")
            self.assertEqual(self.c.state["owned"][stage]["status"], "FAILED")
            con.close.assert_called_once()
            self.assertFalse((out / "source.duckdb").exists())
            self.assertFalse((out / "spill").exists())
            self.assertFalse((out / "queries.json").exists())

    def test_scripted_transport_absolute_header_deadline_and_timer_cleanup(self):
        conn = MagicMock()
        stopped = threading.Event()
        conn.sock.shutdown.side_effect = lambda *args: stopped.set()
        def headers():
            self.assertTrue(stopped.wait(1))
            raise OSError("socket interrupted during headers")
        conn.getresponse.side_effect = headers
        timers = []
        original = threading.Timer
        def timer(*args):
            value = original(*args)
            timers.append(value)
            return value
        with patch("benchmarks.local_perf.runner.http.client.HTTPConnection", return_value=conn), patch(
                "benchmarks.local_perf.runner.threading.Timer", side_effect=timer):
            start = time.monotonic()
            with self.assertRaises(TimeoutError):
                TrinoAdapter("http://127.0.0.1:1234").request("GET", "http://127.0.0.1:1234/page", start+.05)
            self.assertLess(time.monotonic()-start,.4)
        conn.close.assert_called_once()
        self.assertFalse(timers[0].is_alive())

    def test_scripted_transport_incomplete_length_and_page_bound(self):
        for chunks, length, message in (([b"{}",b""],9,"truncated"), ([b"x"*(16*1024**2+1)],None,"bounded")):
            conn = MagicMock()
            response = conn.getresponse.return_value
            response.status, response.length = 200, length
            response.isclosed.return_value = False
            response.read1.side_effect = chunks
            with patch("benchmarks.local_perf.runner.http.client.HTTPConnection", return_value=conn):
                with self.assertRaisesRegex(ValueError,message):
                    TrinoAdapter("http://127.0.0.1:1234").request("GET","http://127.0.0.1:1234/page",time.monotonic()+1)
            conn.close.assert_called_once()

    def test_scripted_transport_complete_body_closes_socket_immediately(self):
        class ClosingSocket:
            closed = False

            def makefile(self, mode):
                return ClosingBody(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")

            def settimeout(self, timeout):
                if self.closed:
                    raise OSError(errno.EBADF, "socket closed after complete body")

            def shutdown(self, how):
                self.closed = True

        sock = ClosingSocket()

        class ClosingBody(io.BytesIO):
            def read1(self, size=-1):
                data = super().read1(size)
                if self.tell() == len(self.getvalue()):
                    sock.closed = True
                return data

        # Use the real standard response API (length/isclosed/read1), with a
        # socket whose final body read immediately invalidates settimeout.
        response = http.client.HTTPResponse(sock)
        response.begin()
        conn = MagicMock()
        conn.sock = sock
        conn.getresponse.return_value = response
        try:
            with patch("benchmarks.local_perf.runner.http.client.HTTPConnection", return_value=conn):
                result = TrinoAdapter("http://127.0.0.1:1234").request(
                    "GET", "http://127.0.0.1:1234/page", time.monotonic()+1)
            self.assertEqual(result, {})
            self.assertEqual(response.length, 0)
            self.assertTrue(sock.closed)
            conn.close.assert_called_once()
        finally:
            response.close()

    def test_packaged_tpch_known_answers_all22_typed_and_distinct_provenance(self):
        dataset = self.root / "dataset"
        dataset.mkdir()
        (dataset / "dataset.json").write_text(json.dumps(dict(status="SUCCESS",suite="tpch",scale=.01,timeout_s=10)))
        queries = [{"id":str(n),"sql":"SELECT " + str(n)} for n in range(1,23)]
        manifest = dict(format=1,dataset_id="d",queries=queries)
        (dataset / "queries.json").write_text(json.dumps(manifest))
        reference = self.root / "reference"
        for q in queries:
            out = reference / (q["id"] + "-0")
            out.mkdir(parents=True)
            columns = ([column("avg_yearly", "double")] if q["id"] == "17" else
                       [column("s", "varchar")] if q["id"] == "18" else
                       [column("d","decimal(38,8)"),column("sum(f)" if q["id"] == "19" else "f","double")])
            w = Writer(out / "result.jsonl", columns,
                       dict(dataset_id="d",query_id=q["id"],sql_sha256=sql_identity(q["sql"])))
            w.row([None] if q["id"] == "17" else [""] if q["id"] == "18" else
                  [Decimal("12345678901234567890.1234"),1.000000001]);w.finish()
        con = MagicMock()
        canonical = [(int(q["id"]), q["sql"]) for q in queries]
        def execute(sql, *args):
            cursor = MagicMock()
            cursor.fetchall.return_value = (canonical if "tpch_queries" in sql else
                                           [(n, "avg_yearly\n\n" if n == 17 else "s\n\n" if n == 18 else
                                             "d|SUM\n12345678901234567890.12340000|1.0\n" if n == 19 else
                                             "d|f\n12345678901234567890.12340000|1.0\n") for n in range(1,23)])
            return cursor
        con.execute.side_effect = execute
        with patch("benchmarks.local_perf.dataset.connect",return_value=con), patch(
                "benchmarks.local_perf.dataset.load_extension",return_value={"extension_version":"v1.4.3","installed_from":"core"}):
            out = qualify_packaged_tpch(self.c,dataset,reference,"known")
        record = json.loads((out / "qualification.json").read_text())
        self.assertEqual(record["status"],"SUCCESS")
        self.assertEqual(len(record["queries"]),22)
        self.assertIn("precomputed",record["source"])
        self.assertFalse(record["independent_cross_engine"])
        self.assertEqual(record["queries"][18]["column_binding"], [
            dict(position=0, packaged="d", result="d"),
            dict(position=1, packaged="SUM", result="sum(f)")])
        con.close.assert_called_once()
        # A blank scalar numeric row is one NULL, never zero rows; blank
        # VARCHAR is an empty string, never NULL. Both mismatches must fail.
        for qid, rows in (("17", []), ("18", [[None]])):
            q = queries[int(qid)-1]
            columns = [column("avg_yearly", "double")] if qid == "17" else [column("s", "varchar")]
            (reference / (qid + "-0/result.jsonl")).unlink()
            w = Writer(reference / (qid + "-0/result.jsonl"), columns,
                       dict(dataset_id="d",query_id=qid,sql_sha256=sql_identity(q["sql"])))
            for row in rows:
                w.row(row)
            w.finish()
            # Restore Q17 after its zero-row rejection so Q18 is exercised.
            with patch("benchmarks.local_perf.dataset.connect", return_value=con), patch(
                    "benchmarks.local_perf.dataset.load_extension", return_value={}):
                with self.assertRaises(ValueError):
                    qualify_packaged_tpch(self.c, dataset, reference, "mismatch-" + qid)
            failed = json.loads((self.c.root / ("mismatch-" + qid) / "qualification.json").read_text())
            self.assertEqual(failed["status"], "FAILED")
            self.assertEqual(len(failed["queries"]), int(qid)-1)
            (reference / (qid + "-0/result.jsonl")).unlink()
            w = Writer(reference / (qid + "-0/result.jsonl"), columns,
                       dict(dataset_id="d",query_id=qid,sql_sha256=sql_identity(q["sql"])))
            w.row([None] if qid == "17" else [""])
            w.finish()

        queries[0]["sql"] = "SELECT 99"
        (dataset / "queries.json").write_text(json.dumps(manifest))
        with patch("benchmarks.local_perf.dataset.connect", return_value=con), patch(
                "benchmarks.local_perf.dataset.load_extension", return_value={}):
            with self.assertRaisesRegex(ValueError, "canonical extension SQL"):
                qualify_packaged_tpch(self.c, dataset, reference, "noncanonical")

    def test_packaged_aggregate_label_binding_and_strict_ordinary_compare(self):
        for label, name in (("SUM", "sum(l_quantity)"), ("avg", "AVG(price)"),
                            ("min", "min(coalesce(price, 0))"), ("max", 'max("a)b")'),
                            ("count", "count(*)")):
            with self.subTest(label=label):
                self.assertEqual(packaged_column_binding(["key", label],
                                 [column("key", "varchar"), column(name, "double")]),
                                 [dict(position=0, packaged="key", result="key"),
                                  dict(position=1, packaged=label, result=name)])
        for labels, names in ((["total"], ["sum(x)"]), (["sum"], ["avg(x)"]),
                              (["sum"], ["sum_total"]), (["sum"], ["sum(x) + max(y)"]),
                              (["sum"], ["sum(x) AS total"]), (["sum"], ["sum()"]),
                              (["SUM"], ["sum"]), (["x", "sum"], ["sum(y)", "x"]),
                              (["sum"], ["sum(x)", "extra"])):
            with self.subTest(labels=labels, names=names):
                with self.assertRaisesRegex(ValueError, "column mismatch"):
                    packaged_column_binding(labels, [column(n, "double") for n in names])
        a, b = self.root / "bare.jsonl", self.root / "expression.jsonl"
        for path, name in ((a, "sum"), (b, "sum(l_quantity)")):
            w = Writer(path, [column(name, "double")], {})
            w.row([305.0])
            w.finish()
        with self.assertRaises(ValueError):
            compare(a, b, scratch=self.root)

    def test_types_ranges_and_context_independent_decimal(self):
        for kind in ("decimal(0,0)", "decimal(39,0)", "decimal(3,4)"):
            with self.assertRaises(ValueError):
                column("x", kind)
        for kind, bits in (("tinyint",8), ("smallint",16), ("integer",32), ("bigint",64), ("hugeint",128), ("ubigint",64)):
            low, high = (0, 2**bits-1) if kind == "ubigint" else (-2**(bits-1), 2**(bits-1)-1)
            for good in (low, high):
                self.assertEqual(cell(good, kind), ["i", str(good)])
            for bad in (low-1, high+1, True):
                with self.assertRaises(ValueError):
                    cell(bad, kind)
        for kind in ("double", "real"):
            with self.assertRaises(ValueError):
                cell(True, kind)
        with localcontext() as ctx:
            ctx.prec = 3
            value = "12345678901234567890123456789012345678"
            self.assertEqual(cell(Decimal(value), "decimal(38,0)"), ["d",value])
            self.assertEqual(cell(Decimal("1.2300E+2"), "decimal(38,8)"), ["d","123"])

    def test_sqlite_progress_samples_guard_at_200ms_and_after_check_completion(self):
        a, b = self.root / "a", self.root / "b"
        for path in (a, b):
            w = Writer(path, [column("x", "integer")], {})
            w.row([1])
            w.finish()
        state = {"now": 0.0, "callback": False}
        calls = []
        test = self

        def check():
            calls.append((state["callback"], state["now"]))
            # A later slow guard proves the next interval starts on completion.
            if state["callback"] and state["now"] == 0.4:
                state["now"] = 0.45

        class Connection(sqlite3.Connection):
            def set_progress_handler(self, callback, operations):
                test.assertEqual(operations, 10000)
                def invoke():
                    state["callback"] = True
                    try:
                        test.assertEqual(callback(), 0)
                    finally:
                        state["callback"] = False
                for _ in range(10000):
                    invoke()
                test.assertEqual(calls, [(False, 0.0), (True, 0.0)])
                for now, expected in ((0.199, 1), (0.2, 2), (0.399, 2),
                                      (0.4, 3), (0.6, 3), (0.649, 3), (0.65, 4)):
                    state["now"] = now
                    invoke()
                    test.assertEqual(sum(callback for callback, _ in calls), expected)
                return super().set_progress_handler(callback, operations)

        original = sqlite3.connect
        def connect(path):
            return original(path, factory=Connection)
        with patch("benchmarks.local_perf.results.sqlite3.connect", side_effect=connect), patch(
                "benchmarks.local_perf.results.time.monotonic", side_effect=lambda: state["now"]):
            self.assertTrue(compare(a, b, scratch=self.root, check=check))
        self.assertEqual([now for callback, now in calls if callback], [0.0, 0.2, 0.4, 0.65])
        self.assertEqual(calls[-1], (False, 0.65))  # Explicit final check during cooldown.
        self.assertEqual(list(self.root.glob("compare-*")), [])

    def test_final_explicit_guard_errors_are_not_skipped_during_progress_cooldown(self):
        a, b = self.root / "a", self.root / "b"
        for path in (a, b):
            w = Writer(path, [column("x", "integer")], {})
            w.row([1])
            w.finish()
        original = sqlite3.connect
        test = self
        for ordered in (False, True):
            for error_type in (TimeoutError, RuntimeError):
                with self.subTest(ordered=ordered, error=error_type):
                    state = {"callback": False, "sampled": False, "calls": 0}
                    error = error_type("final deadline/capacity")
                    def check():
                        state["calls"] += 1
                        if state["callback"]:
                            state["sampled"] = True
                        elif state["calls"] > 1:
                            if not ordered:
                                test.assertTrue(state["sampled"])
                            raise error
                    class Connection(sqlite3.Connection):
                        def set_progress_handler(self, callback, operations):
                            state["callback"] = True
                            try:
                                test.assertEqual(callback(), 0)
                                test.assertEqual(callback(), 0)
                            finally:
                                state["callback"] = False
                            return super().set_progress_handler(callback, operations)
                    def connect(path):
                        return original(path, factory=Connection)
                    with patch("benchmarks.local_perf.results.sqlite3.connect", side_effect=connect), patch(
                            "benchmarks.local_perf.results.time.monotonic", return_value=0.0):
                        with self.assertRaises(error_type) as caught:
                            compare(a, b, ordered=ordered, scratch=self.root, check=check)
                    self.assertIs(caught.exception, error)
                    self.assertIsNone(caught.exception.__cause__)
                    self.assertEqual(state["calls"], 2 if ordered else 3)
                    self.assertEqual(list(self.root.glob("compare-*")), [])

    def test_sqlite_progress_interrupts_group_by_deadline_and_capacity_preserving_cause_and_temp(self):
        a, b = self.root / "a", self.root / "b"
        for path in (a,b):
            w = Writer(path, [column("x","integer")], {})
            for i in range(10000):
                w.row([i])
            w.finish()
        original = sqlite3.connect
        state = {"group":False, "progress":False, "now":0.0}
        class Connection:
            def __init__(self, path):
                self.db = original(path)
                self.sql = []
            def execute(self, sql, *args):
                self.sql.append(sql)
                if "GROUP BY" in sql:
                    state["group"] = True
                    state["now"] = 0.2
                return self.db.execute(sql, *args)
            def __getattr__(self, name):
                return getattr(self.db, name)
        connections = []
        def connect(path):
            self.assertTrue(Path(path).is_relative_to(self.root))
            c = Connection(path)
            connections.append(c)
            return c
        for error_type in (TimeoutError, RuntimeError):
            state.update(group=False,progress=False,now=0.0)
            error = error_type("validation SQL deadline/capacity")
            def check():
                if state["group"]:
                    state["progress"] = True
                    raise error
            with patch("benchmarks.local_perf.results.sqlite3.connect", side_effect=connect), patch(
                    "benchmarks.local_perf.results.time.monotonic", side_effect=lambda: state["now"]):
                with self.assertRaisesRegex(error_type, "validation SQL deadline/capacity") as caught:
                    compare(a,b,scratch=self.root,check=check)
            self.assertTrue(state["progress"])
            self.assertIs(caught.exception, error)
            self.assertIsInstance(caught.exception.__cause__, sqlite3.OperationalError)
        self.assertTrue(any("temp_store_directory" in sql and str(self.root) in sql for sql in connections[0].sql))
        self.assertEqual(list(self.root.glob("compare-*")), [])

    def test_freeze_sql_identity_before_dispatch_and_failfast_denominator(self):
        manifest = {"format":1,"dataset_id":"d", "queries":[{"id":"1","sql":"SELECT 1"},{"id":"2","sql":"SELECT 2"}]}
        def before():
            frozen = json.loads((self.c.root / "r/queries.json").read_text())
            self.assertEqual(frozen["queries"][0]["sql_sha256"], sql_identity("SELECT 1"))
            manifest["queries"][0]["sql"] = "SELECT 99"
        adapter = ScriptedTrino("lost",before)
        with self.assertRaises(OSError):
            run(self.c,"r",manifest,adapter,reference_label="test only")
        report = json.loads((self.c.root / "r/run.json").read_text())
        self.assertEqual(report["planned_attempts"],2)
        self.assertEqual(report["not_run_attempts"],1)
        self.assertEqual(report["pending_attempts"],0)
        self.assertEqual(json.loads((self.c.root / "r/1-0/attempt.json").read_text())["sql"], "SELECT 1")

    def test_cancel_rechecks_original_identity_without_network(self):
        a = ScriptedTrino("lost")
        a.before = lambda: a.headers.update({"X-Trino-User":"changed"})
        manifest = {"format":1,"dataset_id":"d","queries":[{"id":"1","sql":"SELECT 1"}]}
        with patch.object(a,"cancel_reconcile") as cancel:
            with self.assertRaises(OSError):
                run(self.c,"r",manifest,a,reference_label="test only")
            cancel.assert_not_called()
        intent = json.loads((self.c.root / "r/1-0/attempt.json").read_text())
        self.assertEqual(intent["status"],"UNKNOWN")
        self.assertEqual(intent["adapter_identity"]["user"],"local-perf")

    def test_reconcile_handle_appends_evidence_and_completed_attempt_is_immutable(self):
        manifest = {"format":1,"dataset_id":"d","queries":[{"id":"1","sql":"SELECT 1"}]}
        a = ScriptedTrino("cancel-fails")
        with self.assertRaises(TimeoutError):
            run(self.c,"r",manifest,a,reference_label="test only")
        path = self.c.root / "r/1-0/attempt.json"
        original = path.read_bytes()
        a.case = "ok"
        resolved = reconcile(path,a)
        self.assertEqual(resolved["status"],"FAILED")
        self.assertEqual(path.read_bytes(),original)
        self.assertTrue((path.parent / "reconciliation.json").exists())
        result = run(self.c,"next",manifest,a,reference_label="test only")
        self.assertEqual(a.posts,2)
        complete = result / "1-0/attempt.json"
        original = complete.read_bytes()
        with patch.object(a,"cancel_reconcile") as cancel:
            self.assertEqual(reconcile(complete,a)["status"],"SUCCESS")
            cancel.assert_not_called()
        self.assertEqual(complete.read_bytes(),original)

    def test_reference_changed_sql_rejected_before_post(self):
        ref = self.root / "ref/1-0"
        ref.mkdir(parents=True)
        w = Writer(ref / "result.jsonl", [column("x","integer")],
                   {"dataset_id":"d", "query_id":"1", "sql_sha256":sql_identity("SELECT 1")})
        w.row([1]); w.finish()
        a = ScriptedTrino("ok")
        manifest = {"format":1,"dataset_id":"d","queries":[{"id":"1","sql":"SELECT 2"}]}
        with self.assertRaisesRegex(ValueError,"identity"):
            run(self.c,"r",manifest,a,references=ref.parent)
        self.assertEqual(a.posts,0)
        self.assertFalse((self.c.root / "r").exists())

    def test_reconcile_rejects_foreign_wrong_adapter_user_catalog_and_origin(self):
        a = ScriptedTrino("lost")
        manifest = {"format":1,"dataset_id":"d","queries":[{"id":"1","sql":"SELECT 1"}]}
        with self.assertRaises(OSError):
            run(self.c,"r",manifest,a,reference_label="test only")
        path = self.c.root / "r/1-0/attempt.json"
        original = path.read_bytes()
        for wrong in (DuckDBAdapter(self.root), TrinoAdapter(a.url,user="different"),
                      TrinoAdapter(a.url,catalog="different"), TrinoAdapter("http://127.0.0.1:4321")):
            with patch.object(wrong,"cancel_reconcile") as cancel:
                with self.assertRaises(ValueError):
                    reconcile(path,wrong)
                cancel.assert_not_called()
            self.assertEqual(path.read_bytes(),original)
        foreign = self.c.root / "foreign/1-0/attempt.json"
        foreign.parent.mkdir(parents=True)
        foreign.write_bytes(original)
        with self.assertRaises(ValueError):
            reconcile(foreign,a)
        resolved = reconcile(path,a)
        self.assertEqual(resolved["status"],"UNKNOWN")
        self.assertEqual(path.read_bytes(),original)
        with self.assertRaises(RuntimeError):
            run(self.c,"next",manifest,a,reference_label="test only")


if __name__ == "__main__":
    main()
