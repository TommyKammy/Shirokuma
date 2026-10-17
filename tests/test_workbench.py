"""Workbench HTTP contracts, with no resident lab required."""
import http.client
from contextlib import contextmanager
from http.server import HTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/local_experiment"))
import workbench_server as wb


class WorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), wb.WorkbenchHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, path, body=None, method=None, headers=None):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        payload = json.dumps(body) if body is not None else None
        conn.request(method or ("POST" if body is not None else "GET"), path, payload,
                     headers or {"Content-Type": "application/json"})
        response = conn.getresponse()
        raw = response.read()
        result = (response.status, dict(response.getheaders()), raw)
        conn.close()
        return result

    def json_request(self, path, body=None, method=None):
        status, headers, raw = self.request(path, body, method)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        return status, json.loads(raw)

    def test_static(self):
        for path in ("/", "/index.html"):
            with self.subTest(path=path):
                status, headers, body = self.request(path)
                self.assertEqual(status, 200)
                self.assertIn("text/html", headers["Content-Type"])
                self.assertIn(b"Warehouse &amp; SQL", body)
                self.assertIn(b"AgentHouse Pawprints", body)

    @patch.object(wb.warehouse_resident, "kubectl")
    @patch.object(wb.warehouse_resident, "status")
    def test_status(self, status, kubectl):
        status.return_value = {"state": "chilled", "replicas": 0, "ready_replicas": 0}
        kubectl.return_value = json.dumps({"items": [{"metadata": {"name": "polaris"},
            "spec": {"replicas": 1}, "status": {"readyReplicas": 1}}]})
        code, result = self.json_request("/api/status")
        self.assertEqual(code, 200)
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["warehouse"]["state"], "chilled")
        self.assertEqual(result["components"]["polaris"]["ready_replicas"], 1)
        status.assert_called_once_with("LOCAL")

    @patch.object(wb, "components", side_effect=OSError("cluster unavailable"))
    @patch.object(wb.warehouse_resident, "status", side_effect=OSError("no cluster"))
    def test_status_unavailable(self, *_):
        code, result = self.json_request("/api/status")
        self.assertEqual(code, 200)
        self.assertEqual(result["status"], "degraded")
        self.assertIn("error", result["warehouse"])

    @patch.object(wb.warehouse_resident, "transition")
    def test_chill_wake(self, transition):
        for action in ("chill", "wake"):
            transition.return_value = {"action": action, "result": "verified"}
            code, result = self.json_request("/api/warehouse/" + action, {})
            self.assertEqual(code, 200)
            self.assertEqual(result["action"], action)
            transition.assert_called_with(action, "LOCAL")

    @patch.object(wb.warehouse_resident, "query")
    def test_query(self, query):
        report = dict(columns=[{"name": "x", "type": "bigint"}], rows=[[42]], truncated=True,
                      query_id="query-42", telemetry="recorded", duration_ms=12)
        query.return_value = report
        code, result = self.json_request("/api/warehouse/query", {"sql": "SELECT 42", "warehouse": "xs", "max_rows": 1})
        self.assertEqual((code, result), (200, report))
        query.assert_called_once_with("XS", "SELECT 42", catalog="iceberg", schema="marts", max_rows=1)

    @patch.object(wb.bears_resident, "catalog")
    def test_catalog_lineage_and_columns(self, catalog):
        catalog.return_value = {"metadata": {"marts": [["fct_orders"]]}}
        for path, action, table in [("/api/catalog", "search", None), ("/api/lineage", "lineage", None),
                                    ("/api/catalog?table=marts.fct_orders", "describe", "marts.fct_orders")]:
            with self.subTest(path=path):
                code, result = self.json_request(path)
                self.assertEqual((code, result), (200, catalog.return_value))
                args = catalog.call_args.args[0]
                self.assertEqual((args.bear, args.action, args.table), ("catalog", action, table))

    @patch.object(wb.warehouse_resident, 'query', return_value={'rows': []})
    def test_query_modes(self, query):
        for mode in ('read-only', 'sandbox-write', 'admin-write'):
            with self.subTest(mode=mode):
                self.assertEqual(self.json_request('/api/warehouse/query', {'sql': 'SELECT 1', 'mode': mode})[0], 200)
                query.assert_called_with('LOCAL', 'SELECT 1', catalog='iceberg', schema='marts', max_rows=1000, mode=mode)
        query.reset_mock()
        for mode in ('unknown', '', None, [], True):
            with self.subTest(mode=mode):
                self.assertEqual(self.json_request('/api/warehouse/query', {'sql': 'SELECT 1', 'mode': mode})[0], 400)
        query.assert_not_called()

    @patch.object(wb.lake_ingest, 'ingest', return_value={'audit': {'row_count': 12}})
    def test_lake_ingest(self, ingest):
        self.assertEqual(self.json_request('/api/lake/ingest', {'file': 'data/orders.csv'}),
                         (200, ingest.return_value))
        ingest.assert_called_once_with('data/orders.csv', table=None, mode='replace', format='auto', batch_size=500)
        self.assertEqual(self.json_request('/api/lake/ingest', {'file': 'data/events.jsonl', 'table': 'raw.events',
            'mode': 'append', 'format': 'jsonl', 'batch_size': 1})[0], 200)
        ingest.assert_called_with('data/events.jsonl', table='raw.events', mode='append', format='jsonl', batch_size=1)

    @patch.object(wb.lake_ingest, 'ingest')
    def test_lake_ingest_rejects_invalid_fields(self, ingest):
        cases = [{}, {'file': ''}, {'file': '   '}, {'file': 42}]
        cases += [dict(file='data.csv', **{key: value}) for key, values in (
            ('table', [None, [], 3]), ('mode', [None, {}, 3]), ('format', [None, True]),
            ('batch_size', [None, 0, -1, True, '500', 1.5])) for value in values]
        for body in cases:
            with self.subTest(body=body):
                self.assertEqual(self.json_request('/api/lake/ingest', body)[0], 400)
        ingest.assert_not_called()

    @patch.object(wb.bears_resident, 'run', return_value={'valid': True})
    def test_data_wap_actions(self, run):
        cases = [({'action': 'generate', 'sql': 'SELECT 1'}, 'sql', 'SELECT 1'),
                 ({'action': 'stage', 'sql': 'SELECT 1', 'table': 'summary'}, 'stage_table', 'summary'),
                 ({'action': 'stage', 'request': 'Count orders', 'stage_table': 'custom', 'table': 'other'}, 'stage_table', 'custom'),
                 ({'action': 'publish', 'table': 'summary'}, 'publish', 'summary'),
                 ({'action': 'publish', 'stage_table': 'summary'}, 'publish', 'summary'),
                 ({'action': 'publish', 'publish': 'custom', 'table': 'other'}, 'publish', 'custom'),
                 ({'action': 'rollback', 'target': 'marts.summary', 'snapshot_id': '9007199254740993'}, 'rollback', 'marts.summary'),
                 ({'action': 'rollback', 'table': 'marts.summary'}, 'rollback', 'marts.summary'),
                 ({'action': 'rollback', 'rollback': 'marts.custom', 'table': 'other'}, 'rollback', 'marts.custom')]
        for fields, key, expected in cases:
            with self.subTest(fields=fields):
                self.assertEqual(self.json_request('/api/bears/run', dict(bear='data', **fields))[0], 200)
                args = run.call_args.args[0]
                self.assertEqual(getattr(args, key), expected)
                if 'snapshot_id' in fields:
                    self.assertEqual(args.snapshot_id, fields['snapshot_id'])
        run.reset_mock()
        for fields in ({'action': 'publish'}, {'action': 'rollback'}, {'request': ' ', 'sql': '\n'},
                       {'sql': 'SELECT 1', 'stage_table': []}, {'publish': 3}, {'rollback': None}, {'snapshot_id': 42}):
            with self.subTest(fields=fields):
                code, result = self.json_request('/api/bears/run', dict(bear='data', **fields))
                self.assertEqual(code, 400)
                if fields.get('action') in ('publish', 'rollback'):
                    self.assertEqual(result['error'], 'data bear requires a request, sql, publish, or rollback')
        run.assert_not_called()

    @patch.object(wb.bears_resident, 'catalog')
    def test_snapshot_ids_preserve_digits_for_browser_rollback(self, catalog):
        catalog.return_value = {'metadata': {'snapshots': {'rows': [[9007199254740993, None, '2026-10-17']]}}}
        code, result = self.json_request('/api/catalog?table=marts.summary')
        self.assertEqual(code, 200)
        self.assertEqual(result['metadata']['snapshots']['rows'], [['9007199254740993', None, '2026-10-17']])

    def test_lineage_from_actual_models(self):
        code, result = self.json_request("/api/lineage")
        self.assertEqual(code, 200)
        self.assertIn("stg_tpch_orders", result["metadata"]["lineage"]["fct_orders"])
        self.assertIn("stg_tpch_customers", result["metadata"]["lineage"]["dim_customers"])

    @patch.object(wb.bears_resident, "run")
    def test_all_bears(self, run):
        for bear in ("sre", "query", "data", "catalog", "policy"):
            with self.subTest(bear=bear):
                run.return_value = {"bear": bear, "telemetry": "recorded"}
                payload = dict(bear=bear, sql="SELECT 1", request="Count customers", manifest="example.yaml", target="latest")
                code, result = self.json_request("/api/bears/run", payload)
                self.assertEqual((code, result), (200, run.return_value))
                args = run.call_args.args[0]
                for key, value in payload.items():
                    self.assertEqual(getattr(args, key), value)
                self.assertEqual(args.table, "latest" if bear == "catalog" else None)

    @patch.object(wb.manage, "Client")
    def test_pawprints(self, client):
        rows = [{"timestamp": "2026-10-17", "action_type": "warehouse.query", "duration_ms": 3, "status": "success"}]
        client.return_value.query.return_value = json.dumps({"data": rows})
        self.assertEqual(self.json_request("/api/pawprints"), (200, rows))
        statement = client.return_value.query.call_args.args[0]
        self.assertIn("agenthouse.pawprints", statement)
        self.assertIn("ORDER BY timestamp DESC LIMIT 100", statement)

    @patch.object(wb.warehouse_resident, "query")
    @patch.object(wb.bears_resident, "run")
    def test_invalid_requests_never_call_backends(self, run, query):
        cases = [("/api/warehouse/query", {}), ("/api/warehouse/query", {"sql": "SELECT 1", "max_rows": True}),
                 ("/api/warehouse/query", {"sql": "SELECT 1", "warehouse": "XL"}),
                 ("/api/bears/run", {"bear": "__builtins__"}), ("/api/bears/run", {"bear": "data"}),
                 ("/api/bears/run", {"bear": "policy", "action": "apply"}),
                 ("/api/bears/run", {"bear": "query", "sql": []}), ("/api/warehouse/query", [])]
        for path, body in cases:
            with self.subTest(body=body):
                self.assertEqual(self.json_request(path, body)[0], 400)
        run.assert_not_called()
        query.assert_not_called()

    @patch.object(wb.manage, "Client", side_effect=RuntimeError("ClickHouse unavailable"))
    def test_backend_errors(self, _):
        self.assertEqual(self.json_request("/api/pawprints"), (502, {"error": "ClickHouse unavailable"}))

    def test_unknown_routes(self):
        self.assertEqual(self.json_request("/missing")[0], 404)
        self.assertEqual(self.json_request("/missing", {})[0], 404)

    @patch.object(wb.warehouse_resident, "transition")
    def test_cors(self, transition):
        origin = "http://%s:%s" % self.server.server_address
        code, headers, _ = self.request("/api/status", method="OPTIONS", headers={"Origin": origin})
        self.assertEqual(code, 204)
        self.assertEqual(headers["Access-Control-Allow-Origin"], origin)
        self.assertEqual(self.request("/api/warehouse/wake", {}, headers={"Origin": "https://example.com"})[0], 403)
        transition.assert_not_called()

    def test_malformed_json_and_content_type(self):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        conn.request("POST", "/api/warehouse/query", "{", {"Content-Type": "application/json"})
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()
        self.assertEqual(self.request("/api/warehouse/query", {}, headers={"Content-Type": "text/plain"})[0], 400)

    @patch.object(wb, 'HTTPServer')
    def test_server_owns_persistent_tunnel_and_serves_when_unavailable(self, server):
        for unavailable in (False, True):
            events = []
            @contextmanager
            def tunnel():
                if unavailable:
                    raise OSError('Trino offline')
                events.append('open')
                try:
                    yield 'http://trino'
                finally:
                    events.append('close')
            def serve():
                self.assertEqual(events, [] if unavailable else ['open'])
                raise KeyboardInterrupt()
            server.return_value.__enter__.return_value.serve_forever.side_effect = serve
            with self.subTest(unavailable=unavailable), patch.object(wb.warehouse_resident, 'persistent_trino', tunnel), \
                 patch('builtins.print'):
                wb.main(['--port', '0'])
            self.assertEqual(events, [] if unavailable else ['open', 'close'])


if __name__ == "__main__":
    unittest.main()
