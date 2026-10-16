import importlib.util
import io
from contextlib import nullcontext
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location(
    "warehouse_resident", Path(__file__).resolve().parents[1] / "tools/local_experiment/warehouse_resident.py")
w = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(w)


class ResidentWarehouseTests(unittest.TestCase):
    def test_sizes_and_states(self):
        for size in ("xs", "s", "local"):
            self.assertEqual(w.warehouse(size), size.upper())
        with self.assertRaises(ValueError):
            w.warehouse("XL")
        for replicas, ready, state in [(0, 0, "chilled"), (1, 0, "starting"), (2, 1, "active")]:
            with patch.object(w, "deployment", return_value={
                "spec": {"replicas": replicas}, "status": {"readyReplicas": ready}
            }):
                self.assertEqual(w.status("xs"), dict(warehouse="XS", backend="trino-local", deployment="trino-local",
                    namespace="shirokuma-dev", state=state, replicas=replicas, ready_replicas=ready))

    @patch.object(w, "telemetry", return_value="skipped")
    def test_transitions_and_noops(self, telemetry):
        for action, replicas, state in [("chill", 1, "active"), ("wake", 0, "chilled")]:
            with patch.object(w, "status", return_value={"replicas": replicas, "state": state}), \
                 patch.object(w, "kubectl") as kube, \
                 patch.object(w, "deployment", side_effect=[
                     {"spec": {"replicas": 0}, "status": {"replicas": 1}},
                     {"spec": {"replicas": 0}, "status": {}}]), \
                 patch.object(w.time, "sleep"), patch.object(w, "trino", return_value=nullcontext()):
                report = w.transition(action, "XS")
                self.assertEqual(report["result"], "verified")
                self.assertEqual(report["backend"], "trino-local")
                kube.assert_any_call("scale", "deployment/trino-local", f"--replicas={1-replicas}")
                self.assertEqual(report["after_replicas"], 1-replicas)
        for action, replicas, state in [("chill", 0, "chilled"), ("wake", 1, "active")]:
            with patch.object(w, "status", return_value={"replicas": replicas, "state": state}), \
                 patch.object(w, "kubectl") as kube:
                self.assertEqual(w.transition(action, "S")["result"], "noop")
                kube.assert_not_called()

    @patch.object(w, "telemetry", return_value="recorded")
    def test_arbitrary_sql_pagination_and_auto_wake(self, telemetry):
        sql = "SELECT name FROM arbitrary_table WHERE amount > 17"
        with patch.object(w, "status", return_value={"replicas": 0}), \
             patch.object(w, "transition") as wake, \
             patch.object(w, "trino", return_value=nullcontext("http://localhost:1234")), \
             patch.object(w, "http_json", side_effect=[
                 {"id": "query1", "nextUri": "http://localhost:1234/next"},
                 {"columns": [{"name": "name"}], "data": [["a"], ["b"]]}]) as http:
            report = w.query("local", sql, "iceberg", "public")
            wake.assert_called_once_with("wake", "LOCAL")
            self.assertEqual(report["rows"], [["a"], ["b"]])
            self.assertEqual(report["row_count"], 2)
            self.assertEqual(report["backend"], "trino-local")
            self.assertFalse(report["truncated"])
            self.assertTrue(report["auto_woken"])
            self.assertEqual(report["query_id"], "query1")
            self.assertEqual(http.call_args_list[0].args[1], sql.encode())
            headers = http.call_args_list[0].args[2]
            self.assertEqual(headers["X-Trino-Catalog"], "iceberg")
            self.assertEqual(headers["X-Trino-Schema"], "public")
            self.assertEqual(headers["X-Trino-Source"], "shirokuma-warehouse-LOCAL")

    def test_query_rejections_and_trino_error(self):
        with self.assertRaises(ValueError):
            w.query("XS", "  ")
        with self.assertRaises(ValueError):
            w.query("LOCAL", "SELECT 1", max_rows=0)
        with patch.object(w, "status", return_value={"replicas": 0}), \
             patch.object(w, "transition") as wake:
            with self.assertRaises(ValueError):
                w.query("XS", "SELECT 1", no_auto_wake=True)
            wake.assert_not_called()
        for failure in ({"error": {"message": "bad SQL"}}, OSError("HTTP failed"), TimeoutError()):
            with patch.object(w, "status", return_value={"replicas": 1}), \
                 patch.object(w, "trino", return_value=nullcontext("http://localhost:1234")), \
                 patch.object(w, "http_json", side_effect=[failure]), patch.object(w, "telemetry") as telemetry:
                with self.assertRaises((RuntimeError, OSError)):
                    w.query("S", "invalid SQL")
                self.assertEqual(telemetry.call_args.args[0], "query")
                self.assertIsInstance(telemetry.call_args.args[1], int)
                self.assertEqual(telemetry.call_args.kwargs, {"status": "failed"})

    def test_default_local(self):
        for action in ("status", "chill", "wake", "query"):
            argv = ["warehouse", action] + (["--sql", "SELECT 1"] if action == "query" else [])
            with patch.object(w.sys, "argv", argv), patch.object(w, "status", return_value={}) as status, \
                 patch.object(w, "transition", return_value={}) as transition, \
                 patch.object(w, "query", return_value={}) as query, patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(w.main(), 0)
                call = {"status": status, "query": query}.get(action, transition).call_args
                self.assertEqual(call.args[1 if action in ("chill", "wake") else 0], "LOCAL")
                if action == "query":
                    self.assertEqual(call.args[-1], 1000)

    @patch.object(w, "telemetry", return_value="skipped")
    def test_bounded_rows_and_best_effort_cancel(self, telemetry):
        for count, next_uri, cancel_error in [(3, "http://localhost/next", False),
                                             (2, "http://localhost/next", True), (3, None, False), (2, None, False)]:
            with patch.object(w, "status", return_value={"replicas": 1}), \
                 patch.object(w, "trino", return_value=nullcontext("http://localhost")), \
                 patch.object(w, "http_json", return_value={"data": [[i] for i in range(count)],
                              "nextUri": next_uri}) as http, \
                 patch.object(w.urllib.request, "urlopen", side_effect=OSError() if cancel_error else None) as cancel:
                report = w.query("LOCAL", "SELECT arbitrary", max_rows=2)
                self.assertEqual(report["rows"], [[0], [1]])
                self.assertEqual(report["truncated"], count > 2 or bool(next_uri))
                http.assert_called_once()
                self.assertEqual(cancel.call_count, int(bool(next_uri)))
                if next_uri:
                    self.assertEqual(cancel.call_args.args[0].get_method(), "DELETE")
                    self.assertEqual(cancel.call_args.args[0].full_url, next_uri)

    def test_port_forward_cleanup_on_failure_and_success(self):
        for failure in (False, True):
            process = Mock()
            process.poll.return_value = None
            with patch.object(w.subprocess, "Popen", return_value=process) as popen, \
                 patch.object(w.socket, "socket") as socket, \
                 patch.object(w, "http_json", return_value={"starting": False}):
                socket.return_value.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 1234)
                try:
                    with w.trino() as url:
                        self.assertTrue(url.startswith("http://127.0.0.1:"))
                        if failure:
                            raise RuntimeError("query failed")
                except RuntimeError:
                    pass
                process.terminate.assert_called_once()
                process.wait.assert_called_once()
                self.assertIn("svc/trino-local", popen.call_args.args[0])

    def test_uninitialized_telemetry_is_best_effort(self):
        with patch.dict("sys.modules", {"agenthouse.manage": None}):
            self.assertEqual(w.telemetry("query", 12), "skipped")

    def test_failed_pawprint_has_only_action_and_timing(self):
        manage = Mock()
        with patch.dict("sys.modules", {"agenthouse.manage": manage}):
            self.assertEqual(w.telemetry("query", 12, status="failed"), "recorded")
        fields = manage.event.call_args.kwargs
        self.assertEqual(fields["action"], "warehouse.query")
        self.assertEqual(fields["status"], "failed")
        self.assertEqual(set(fields), {"action", "target", "status", "agent_id", "task_id", "trace_id", "duration_ms"})


if __name__ == "__main__":
    unittest.main()
