"""Workbench HTTP contracts, with no resident lab required."""
import http.client
import base64
import io
from email.message import Message
from contextlib import contextmanager
from http.server import HTTPServer
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/local_experiment"))
import workbench_server as wb


class WorkbenchHTML(HTMLParser):
    """Inspect the served document, including panel ownership and tab labels."""

    def __init__(self, html):
        super().__init__()
        self.nodes = []
        self.stack = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = dict(attrs)
        node.update(tag=tag, text='', ancestors=tuple(self.stack))
        self.nodes.append(node)
        if tag not in {'input', 'meta', 'link', 'br', 'hr', 'img', 'source', 'wbr'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]['tag'] == tag:
                del self.stack[i:]
                break

    def handle_data(self, text):
        for node in self.stack:
            node['text'] += text

    def by_id(self, identifier):
        return next(n for n in self.nodes if n.get('id') == identifier)


class WorkbenchTests(unittest.TestCase):
    def workbench_document(self):
        status, _, body = self.request('/')
        self.assertEqual(status, 200)
        document = WorkbenchHTML(body.decode())
        ids = [n['id'] for n in document.nodes if 'id' in n]
        self.assertEqual(len(ids), len(set(ids)), 'HTML IDs must remain unique')
        return document

    def test_snowsight_navigation_contract(self):
        doc = self.workbench_document()
        navigation = next(n for n in doc.nodes if n['tag'] == 'nav')
        headings = [n['text'] for n in doc.nodes
                    if n['tag'] == 'h2' and navigation in n['ancestors']]
        self.assertEqual(headings, ['Work with Data', 'Catalog', 'Manage & Monitor'])
        expected = [
            ('warehouse', 'SQL Worksheets · Warehouse & SQL'),
            ('ingestion', 'Ingestion · File Upload'),
            ('transformation', 'Transformation · dbt & WAP'),
            ('bears', 'Autonomous Bears'),
            ('catalog', 'Database Explorer · Catalog'),
            ('pawprints', 'AgentHouse Pawprints'),
            ('compute', 'Compute & Governance'),
        ]
        tabs = [n for n in doc.nodes if 'data-tab' in n]
        self.assertEqual([(n['data-tab'], n['text']) for n in tabs], expected)
        for name, _ in expected:
            with self.subTest(tab=name):
                tab, panel = doc.by_id('tab-' + name), doc.by_id(name)
                self.assertEqual(tab['role'], 'tab')
                self.assertEqual(tab['aria-controls'], name)
                self.assertEqual(panel['role'], 'tabpanel')
                self.assertEqual(panel['aria-labelledby'], tab['id'])
                self.assertEqual('hidden' in panel, name != 'warehouse')
        for identifier in ('quick-warehouse-state', 'quick-wake', 'quick-chill'):
            self.assertTrue(any(n['tag'] == 'header' for n in doc.by_id(identifier)['ancestors']))

    def test_workbench_controls_belong_to_their_workspaces(self):
        doc = self.workbench_document()
        controls = {
            'warehouse': '''warehouse-state replicas chill wake refresh-status components status-error
                sql query-warehouse query-mode max-rows run-query query-meta query-error query-results
                worksheet-explorer worksheet-tree-filter worksheet-tabs add-worksheet-tab query-history
                copilot-request copilot-generate copilot-explain copilot-stage-table copilot-stage
                copilot-publish result-filter export-csv export-json copy-tsv''',
            'ingestion': '''ingest-form ingest-file ingest-table ingest-mode ingest-key run-ingest
                ingest-state ingest-error ingest-result ingest-upload-input ingest-dropzone
                preview-ingest ingest-preview''',
            'transformation': '''refresh-models run-dbt-build run-dbt-test dbt-models wap-models
                model-lineage model-sql-preview open-model-sql refresh-wap-model''',
            'catalog': '''refresh-catalog catalog-error catalog-tree catalog-filter table-title
                table-columns lineage open-table-sql drop-table-btn table-preview table-ddl
                table-snapshots snapshot-preview table-lineage''',
            'bears': 'bear-cards',
            'pawprints': '''refresh-pawprints pawprint-meta pawprint-error pawprint-results
                pawprint-filter pawprint-status-filter''',
            'compute': 'compute-warehouse compute-components compute-sre-check compute-policy-check',
        }
        for section, identifiers in controls.items():
            for identifier in identifiers.split():
                with self.subTest(control=identifier):
                    self.assertIn(doc.by_id(section), doc.by_id(identifier)['ancestors'])
        upload = doc.by_id('ingest-upload-input')
        self.assertEqual(upload['type'], 'file')
        self.assertEqual(upload['accept'], '.csv,.tsv,.jsonl,.ndjson,.json,.parquet')
        for identifier, label in [('export-csv', 'Download CSV'), ('export-json', 'Download JSON'),
                                  ('copy-tsv', 'Copy TSV'), ('preview-ingest', 'Preview Schema & Sample Rows')]:
            self.assertEqual(doc.by_id(identifier)['text'], label)

    def test_table_inspector_contract(self):
        doc = self.workbench_document()
        expected = [('columns', 'Columns'), ('preview', 'Data Preview'),
                    ('profile', 'Profile & NULL Stats'), ('ddl', 'DDL'),
                    ('snapshots', 'Snapshots & Time Travel'), ('lineage', 'Table Lineage')]
        tabs = [n for n in doc.nodes if 'data-inspect' in n]
        self.assertEqual([(n['data-inspect'], n['text']) for n in tabs], expected)
        for name, _ in expected:
            tab, panel = doc.by_id('inspect-tab-' + name), doc.by_id('inspect-' + name)
            self.assertEqual(tab['role'], 'tab')
            self.assertEqual(tab['aria-controls'], panel['id'])
            self.assertEqual(panel['role'], 'tabpanel')
            self.assertEqual(panel['aria-labelledby'], tab['id'])
            self.assertEqual('hidden' in panel, name != 'columns')
        self.assertIn('hidden', doc.by_id('drop-table-btn'))

    @patch.object(wb.bears_resident, 'catalog')
    def test_catalog_full_detail_and_snapshot_preview(self, catalog):
        catalog.return_value = {'metadata': {'profile': {'snapshot_id': 9223372036854775807},
                                           'snapshots': {'rows': [[9223372036854775807, 9007199254740993, 'date']]}}}
        code, result = self.json_request('/api/catalog?table=raw.orders&detail=full')
        self.assertEqual(code, 200)
        self.assertEqual(catalog.call_args.args[0].detail, 'full')
        self.assertEqual(result['metadata']['profile']['snapshot_id'], '9223372036854775807')
        self.assertEqual(result['metadata']['snapshots']['rows'][0], ['9223372036854775807', '9007199254740993', 'date'])
        catalog.return_value = {'action': 'snapshot_preview', 'snapshot_id': '9007199254740993'}
        self.assertEqual(self.json_request('/api/catalog?table=raw.orders&snapshot_id=9007199254740993'),
                         (200, catalog.return_value))
        self.assertEqual(catalog.call_args.args[0].snapshot_id, '9007199254740993')
        catalog.reset_mock()
        self.assertEqual(self.json_request('/api/catalog?snapshot_id=1'),
                         (400, {'error': 'table is required for snapshot_id'}))
        catalog.assert_not_called()
        catalog.side_effect = ValueError('snapshot_id must be digits-only')
        self.assertEqual(self.json_request('/api/catalog?table=raw.orders&snapshot_id=bad')[0], 400)

    def test_preview_and_ingest_browser_uploads(self):
        for endpoint in ('preview', 'ingest'):
            with patch.object(wb.lake_ingest, endpoint, return_value={'action': endpoint}) as call:
                for binary in (False, True):
                    body = {'filename': 'orders.csv'}
                    body.update({'content_base64': base64.b64encode(b'id\n1').decode()} if binary else {'content': 'id\n1'})
                    self.assertEqual(self.json_request('/api/lake/' + endpoint, body), (200, {'action': endpoint}))
                    expected = dict(filename='orders.csv', content=b'id\n1' if binary else 'id\n1', format='auto')
                    expected.update(dict(limit=10) if endpoint == 'preview' else dict(table=None, mode='replace', batch_size=500))
                    call.assert_called_with(None, **expected)
        with patch.object(wb.lake_ingest, 'preview', return_value={}) as preview:
            self.assertEqual(self.json_request('/api/lake/preview', {'file': 'orders.csv', 'limit': 2, 'format': 'csv'})[0], 200)
            preview.assert_called_once_with('orders.csv', format='csv', limit=2)

    def test_invalid_browser_uploads(self):
        cases = [{}, {'filename': 'x.csv'}, {'content': 'id\n1'}, {'filename': 1, 'content': ''},
                 {'filename': ' ', 'content': ''}, {'filename': 'x', 'content': None},
                 {'filename': 'x', 'content_base64': '!!'}, {'filename': 'x', 'content_base64': 'é'},
                 {'filename': 'x', 'content': '', 'content_base64': ''},
                 {'file': 'x.csv', 'filename': 'x.csv', 'content': ''},
                 {'file': 'x.csv', 'format': 'unknown'}]
        for endpoint in ('preview', 'ingest'):
            with patch.object(wb.lake_ingest, endpoint) as call:
                for body in cases:
                    with self.subTest(endpoint=endpoint, body=body):
                        self.assertEqual(self.json_request('/api/lake/' + endpoint, body)[0], 400)
                call.assert_not_called()
        for limit in (0, -1, True, '1', None, 1.5):
            self.assertEqual(self.json_request('/api/lake/preview', {'file': 'x.csv', 'limit': limit})[0], 400)

    def test_models_routes_and_drop(self):
        with patch.object(wb.bears_resident, 'models_inventory', return_value={'dbt_models': [], 'wap_models': [], 'lineage': {}}) as inventory:
            self.assertEqual(self.json_request('/api/models'), (200, inventory.return_value))
            inventory.assert_called_once_with()
        with patch.object(wb.bears_resident, 'drop_table', return_value={'dropped': True}) as drop:
            self.assertEqual(self.json_request('/api/catalog/drop', {'table': 'raw.x'}), (200, drop.return_value))
            drop.assert_called_once_with('raw.x')
            drop.reset_mock()
            for table in (None, '', ' ', 1, []):
                self.assertEqual(self.json_request('/api/catalog/drop', {'table': table})[0], 400)
            drop.assert_not_called()
            drop.side_effect = ValueError('drop is only allowed')
            self.assertEqual(self.json_request('/api/catalog/drop', {'table': 'marts.x'})[0], 400)
        with patch.dict(sys.modules, {'lake_pipeline': Mock()}) as modules:
            run = modules['lake_pipeline'].run
            run.return_value = {'status': 'ok'}
            for body in ({}, {'action': 'build'}, {'action': 'test'}, {'action': 'status'}):
                self.assertEqual(self.json_request('/api/models/build', body), (200, {'status': 'ok'}))
                run.assert_called_with(body.get('action', 'build'))
            run.reset_mock()
            for action in ('publish', None, [], 1):
                self.assertEqual(self.json_request('/api/models/build', {'action': action})[0], 400)
            run.assert_not_called()

    def test_upload_body_limit(self):
        handler = object.__new__(wb.WorkbenchHandler)
        handler.headers = Message()
        handler.headers['Content-Type'] = 'application/json'
        for size in (1024 * 1024 + 1, 10 * 1024 * 1024):
            payload = b'{"content":"' + b'x' * (size - 14) + b'"}'
            payload += b' ' * (size - len(payload))
            self.assertEqual(len(payload), size)
            handler.headers['Content-Length'] = str(len(payload))
            handler.rfile = io.BytesIO(payload)
            self.assertEqual(len(handler.body()['content']), size - 14)
            del handler.headers['Content-Length']
        for size in (0, 10 * 1024 * 1024 + 1):
            handler.headers['Content-Length'] = str(size)
            with self.assertRaisesRegex(ValueError, '10 MiB'):
                handler.body()
            del handler.headers['Content-Length']

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

    @patch.object(wb.lake_ingest, 'ingest', return_value={'key': ['order_id'], 'rows_ingested': 2})
    def test_lake_ingest_upsert(self, ingest):
        self.assertEqual(self.json_request('/api/lake/ingest', {
            'file': 'data/orders.csv', 'mode': 'upsert', 'key': 'order_id'}), (200, ingest.return_value))
        ingest.assert_called_once_with('data/orders.csv', table=None, mode='upsert', format='auto',
                                       batch_size=500, key='order_id')

    @patch.object(wb.lake_ingest, 'ingest')
    def test_lake_ingest_rejects_invalid_fields(self, ingest):
        cases = [{}, {'file': ''}, {'file': '   '}, {'file': 42}]
        cases += [dict(file='data.csv', **{key: value}) for key, values in (
            ('table', [None, [], 3]), ('mode', [None, {}, 3]), ('format', [None, True]),
            ('key', [None, [], 3, True]),
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
                 ({'action': 'refresh', 'table': 'summary'}, 'refresh', 'summary'),
                 ({'action': 'refresh', 'stage_table': 'summary'}, 'refresh', 'summary'),
                 ({'action': 'refresh', 'refresh': 'custom', 'table': 'other'}, 'refresh', 'custom'),
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
        for fields in ({'action': 'publish'}, {'action': 'rollback'}, {'action': 'refresh'}, {'refresh': 42}, {'request': ' ', 'sql': '\n'},
                       {'sql': 'SELECT 1', 'stage_table': []}, {'publish': 3}, {'rollback': None}, {'snapshot_id': 42}):
            with self.subTest(fields=fields):
                code, result = self.json_request('/api/bears/run', dict(bear='data', **fields))
                self.assertEqual(code, 400)
                if fields.get('action') in ('publish', 'rollback', 'refresh'):
                    self.assertEqual(result['error'], 'data bear requires a request, sql, publish, rollback, or refresh')
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
