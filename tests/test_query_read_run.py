from contextlib import contextmanager
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.query_read import run as R
from test_query_read_contract import metadata, request, schema, SNAPSHOT

TOKEN = 'a' * 32


class Client:
    def __init__(self, state):
        self.state, self.records, self.progress = state, [], lambda: None

    def query(self, sql, label):
        self.state['order'].append(label)
        record = {'label': label, 'query_id': None, 'sql_sha256': R.hashlib.sha256(sql.encode()).hexdigest(),
                  'response_complete': False, 'post_count': 1}
        self.records.append(record)
        self.progress()
        if self.state.get('query_error') and label == 'select':
            raise self.state['query_error']
        if label == 'inventory':
            value = {'columns': ['table_name'], 'column_types': ['varchar'], 'rows': [['fixture_v1']]}
        elif label in ('schema_before', 'schema_after'):
            value = {'columns': ['column_name', 'data_type', 'ordinal_position', 'is_nullable'],
                     'column_types': ['varchar', 'varchar', 'bigint', 'varchar'],
                     'rows': [[c['name'], c['type'], i, 'YES' if c['nullable'] else 'NO']
                              for i, c in enumerate(schema(), 1)]}
            if label == 'schema_after' and self.state.get('schema_drift'):
                value['rows'][1][0] = 'changed'
        else:
            value = {'columns': ['id', 'name'], 'column_types': ['bigint', 'varchar'],
                     'rows': [[1, 'private-row-sentinel'], [2, 'second-row']]}
        record.update(query_id='20260918_000000_0000' + str(len(self.records)) + '_abcde', response_complete=True)
        self.progress()
        return value


class Catalog:
    def __init__(self, state):
        self.state, self.loads, self.authentications = state, 0, 0
        self.progress = lambda: None

    def authenticate(self):
        self.authentications += 1

    def receipt(self):
        return {'loads': self.loads, 'authentications': self.authentications}

    def load(self, value):
        self.loads += 1
        result = metadata()
        if self.loads == 2 and self.state.get('head_drift'):
            result['metadata']['current-snapshot-id'] = SNAPSHOT - 1
            result['metadata']['snapshots'].append({'snapshot-id': SNAPSHOT - 1})
            result['metadata']['refs']['main']['snapshot-id'] = SNAPSHOT - 1
        self.progress()
        return result


class OwnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name).resolve()
        self.output.chmod(0o700)
        self.state = {'order': []}
        self.worker, self.control = Client(self.state), Client(self.state)
        self.catalog = Catalog(self.state)
        self.drain = Mock(side_effect=self.finish_queries)
        self.source_patch = patch.object(R, 'sources', return_value={'fixed.py': 'b' * 64})
        self.source_patch.start()
        self.addCleanup(self.source_patch.stop)

    def finish_queries(self, *_):
        self.state['order'].append('drain')
        return {'verified': True, 'active': []}

    @contextmanager
    def runtime(self, token, progress):
        self.assertEqual(token, TOKEN)
        progress({'state': 'running', 'cleanup': {'verified': False}})
        try:
            yield self.worker, self.control, self.catalog
        finally:
            self.state['order'].append('close')
            progress({'state': 'verified', 'cleanup': {'verified': True}})
            if self.state.get('exit_error'):
                raise self.state['exit_error']

    def execute(self, value=None):
        self.owner = R.Owner(TOKEN, value or request(), self.output)
        return self.owner.execute(self.runtime, self.drain)

    def test_select_releases_explicit_typed_rows_only_after_unchanged_identity_and_cleanup(self):
        report, public = self.execute()
        self.assertEqual(report['result'], 'passed')
        self.assertEqual(public['snapshot_id'], str(SNAPSHOT))
        self.assertEqual(public['rows'][0], [{'type': 'bigint', 'value': '1'},
                                           {'type': 'varchar', 'value': 'private-row-sentinel'}])
        self.assertEqual(self.state['order'], ['inventory', 'schema_before', 'select', 'schema_after', 'drain', 'close'])
        saved = (self.output / 'summary.json').read_text()
        for value in ('private-row-sentinel', 'second-row', 'never-persist-credential', 'SELECT ', 'storage-credentials'):
            self.assertNotIn(value, saved)
        self.assertNotIn('rows', report['observation'])
        self.assertEqual(report['observation']['rows_sha256'], public['rows_sha256'])
        self.assertTrue(report['identity_unchanged'])
        self.assertEqual(self.catalog.authentications, 1)
        self.assertEqual(self.catalog.loads, 2)
        self.assertEqual((self.output / 'summary.json').stat().st_mode & 0o777, 0o600)

    def test_tables_does_not_authenticate_or_read_metadata(self):
        report, public = self.execute(R.C.request('tables', schema='smoke'))
        self.assertEqual(report['result'], 'passed')
        self.assertEqual(public['tables'], ['smoke.fixture_v1'])
        self.assertEqual(self.state['order'], ['inventory', 'drain', 'close'])
        self.assertEqual(self.catalog.authentications, 0)
        self.assertEqual(self.catalog.loads, 0)

    def test_describe_rechecks_identity_and_never_selects_rows(self):
        report, public = self.execute(R.C.request('describe', table='smoke.fixture_v1'))
        self.assertEqual(report['result'], 'passed')
        self.assertEqual(public['columns'], schema())
        self.assertEqual(public['current_snapshot_id'], str(SNAPSHOT))
        self.assertNotIn('select', self.state['order'])

    def test_limit_plus_one_is_explicitly_truncated(self):
        report, public = self.execute(request(limit=1))
        self.assertEqual(report['result'], 'passed')
        self.assertEqual(public['row_count'], 1)
        self.assertEqual(public['fetched_row_count'], 2)
        self.assertTrue(public['truncated'])

    def test_missing_table_never_reads_schema_or_data(self):
        report, public = self.execute(R.C.request('select', table='smoke.absent', columns=['id'], limit=1))
        self.assertEqual(report['error_code'], 'TABLE_NOT_DISCOVERED')
        self.assertIsNone(public)
        self.assertEqual(self.state['order'], ['inventory', 'drain', 'close'])

    def test_missing_column_never_selects_or_authenticates(self):
        report, public = self.execute(request(['missing']))
        self.assertEqual(report['error_code'], 'COLUMN_NOT_DISCOVERED')
        self.assertIsNone(public)
        self.assertNotIn('select', self.state['order'])
        self.assertEqual(self.catalog.authentications, 0)

    def test_head_drift_after_select_does_not_release_rows(self):
        self.state['head_drift'] = True
        report, public = self.execute()
        self.assertEqual(report['error_code'], 'IDENTITY_CHANGED')
        self.assertIsNone(public)
        self.assertEqual(self.state['order'].count('select'), 1)

    def test_schema_drift_after_select_does_not_release_rows(self):
        self.state['schema_drift'] = True
        report, public = self.execute()
        self.assertEqual(report['result'], 'failed')
        self.assertIsNone(public)

    def test_unknown_post_has_no_retry_and_raw_error_is_not_saved(self):
        self.state['query_error'] = TimeoutError('private server response')
        report, public = self.execute()
        self.assertEqual(report['result'], 'failed')
        self.assertIsNone(public)
        self.assertEqual(self.state['order'].count('select'), 1)
        self.assertNotIn('private server response', (self.output / 'summary.json').read_text())

    def test_failed_drain_withholds_successful_rows(self):
        self.drain.side_effect = lambda *_: {'verified': False}
        report, public = self.execute()
        self.assertEqual(report['result'], 'failed')
        self.assertFalse(report['cleanup']['verified'])
        self.assertIsNone(public)

    def test_teardown_failure_withholds_successful_rows(self):
        self.state['exit_error'] = RuntimeError('private teardown')
        report, public = self.execute()
        self.assertEqual(report['result'], 'failed')
        self.assertIsNone(public)

    def test_cancel_survives_drain_teardown_and_final_save_failures(self):
        original = KeyboardInterrupt()
        self.state.update(query_error=original, exit_error=RuntimeError('secondary exit'))
        self.drain.side_effect = OSError('secondary drain')
        write = R.FS.write
        def outage(path, value):
            if value['result'] == 'interrupted':
                raise OSError('secondary save')
            write(path, value)
        with patch.object(R.FS, 'write', side_effect=outage), self.assertRaises(KeyboardInterrupt) as caught:
            self.execute()
        self.assertIs(caught.exception, original)
        self.assertEqual(self.state['order'].count('select'), 1)
        self.assertIsNone(self.owner.public)

    def test_first_cancel_during_cleanup_replaces_earlier_ordinary_failure(self):
        self.state['query_error'] = RuntimeError('first failure')
        original = KeyboardInterrupt()
        self.drain.side_effect = original
        with self.assertRaises(KeyboardInterrupt) as caught:
            self.execute()
        self.assertIs(caught.exception, original)

    def test_final_journal_outage_cannot_return_passed_data(self):
        write = R.FS.write
        def outage(path, value):
            if value['result'] == 'passed':
                write(path, value)
                raise OSError('directory sync failed after rename')
            write(path, value)
        with patch.object(R.FS, 'write', side_effect=outage):
            report, public = self.execute()
        self.assertEqual(report['result'], 'failed')
        self.assertIsNone(public)
        self.assertEqual(json.loads((self.output / 'summary.json').read_bytes())['result'], 'failed')

    def test_source_change_prevents_public_result(self):
        with patch.object(R, 'sources', side_effect=[{'fixed.py': 'a' * 64}, {'fixed.py': 'b' * 64}]):
            report, public = self.execute()
        self.assertEqual(report['error_code'], 'READ_SOURCE_CHANGED')
        self.assertIsNone(public)

    def test_second_execute_is_forbidden_without_a_new_query(self):
        self.execute()
        count = len(self.worker.records)
        with self.assertRaisesRegex(R.C.ContractError, 'READ_RETRY_FORBIDDEN'):
            self.owner.execute(self.runtime, self.drain)
        self.assertEqual(len(self.worker.records), count)

    def test_real_main_and_owner_return_data_without_persisting_rows_or_reusing_token(self):
        output = io.StringIO()
        def invoke(token, value, emit):
            return R.invoke(token, value, emit, runtime_factory=self.runtime, drain=self.drain)
        arguments = ['select', '--evidence-token', TOKEN, '--request-stdin']
        with patch.object(R, 'STATE', self.output / 'evidence'):
            status = R.main(arguments, stdout=output, stdin=io.BytesIO(R.C.canonical(request())), invoker=invoke)
            self.assertEqual(status, 0)
            messages = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual([v['event'] for v in messages], ['allocated', 'result'])
            self.assertEqual(messages[-1]['observation']['rows'][0][1]['value'], 'private-row-sentinel')
            path = R.STATE / TOKEN / 'summary.json'
            before, info = path.read_bytes(), path.stat()
            self.assertNotIn(b'private-row-sentinel', before)
            retry = R.main(arguments, stdout=io.StringIO(), stdin=io.BytesIO(R.C.canonical(request())), invoker=invoke)
            self.assertEqual(retry, 1)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(path.stat().st_mtime_ns, info.st_mtime_ns)
        self.assertEqual(self.state['order'].count('select'), 1)

    def test_cancel_during_final_event_survives_secondary_receipt_failure(self):
        original = KeyboardInterrupt()
        write = R.FS.write
        def outage(path, value):
            if value['result'] == 'interrupted':
                raise OSError('secondary')
            write(path, value)
        with patch.object(R, 'STATE', self.output / 'evidence'), patch.object(R, 'event', side_effect=original), \
                patch.object(R.FS, 'write', side_effect=outage), self.assertRaises(KeyboardInterrupt) as caught:
            R.invoke(TOKEN, request(), lambda _: None, runtime_factory=self.runtime, drain=self.drain)
        self.assertIs(caught.exception, original)
        self.assertEqual(self.state['order'].count('select'), 1)

    def test_actual_owner_transport_contract_and_drain_with_fake_http(self):
        from test_query_read_client import Response, Opener, page
        Q = R.Q
        query_ids = ['20260918_010101_%05d_abc12' % i for i in range(1, 6)]
        schema_rows = [[c['name'], c['type'], i, 'YES' if c['nullable'] else 'NO']
                       for i, c in enumerate(schema(), 1)]
        schema_columns = list(zip(['column_name', 'data_type', 'ordinal_position', 'is_nullable'],
                                  ['varchar', 'varchar', 'bigint', 'varchar']))
        discovery = page(qid=query_ids[0], columns=[('table_name', 'varchar')], rows=[['fixture_v1']])
        schema_before = page(qid=query_ids[1], columns=schema_columns, rows=schema_rows)
        selected = page(qid=query_ids[2], columns=[(c['name'], c['type']) for c in schema()],
                        rows=[[9007199254740993, 'Unicode日本語', '__decimal__', '2026-09-18', True]])
        raw = json.dumps(selected).replace('"__decimal__"', '12345678901234567890123456789012.123456').encode()
        schema_after = page(qid=query_ids[3], columns=schema_columns, rows=schema_rows)
        worker_http = Opener(Response(discovery), Response(schema_before), Response(raw=raw), Response(schema_after))
        self.worker = Q.QueryClient('http://127.0.0.1:28080', TOKEN, opener=worker_http)
        states = [[qid, 'FINISHED', self.worker.user, self.worker.source] for qid in query_ids[:4]]
        control_http = Opener(Response(page(qid=query_ids[4], columns=[('query_id', 'varchar'), ('state', 'varchar'),
                                              ('user', 'varchar'), ('source', 'varchar')], rows=states)))
        self.control = Q.QueryClient('http://127.0.0.1:28080', TOKEN, control=True, opener=control_http)
        catalog_http = Opener(Response(metadata()), Response(metadata()))
        self.catalog = Q.CatalogRead(lambda: None, opener=catalog_http)
        def authenticate():
            self.catalog._auth.realm = 'POLARIS'
            self.catalog._auth.token = 'never-persist-credential'
        self.catalog._auth.authenticate = authenticate
        self.drain = Q.drain
        report, public = self.execute(request([c['name'] for c in schema()], limit=2))
        self.assertEqual(report['result'], 'passed', report['error_code'])
        self.assertEqual(public['rows'][0][0]['value'], '9007199254740993')
        self.assertEqual(public['rows'][0][2]['value'], '12345678901234567890123456789012.123456')
        self.assertEqual([r.get_method() for r in worker_http.requests], ['POST'] * 4)
        self.assertEqual([r.get_method() for r in control_http.requests], ['POST'])
        self.assertEqual([r.get_method() for r in catalog_http.requests], ['GET', 'GET'])
        text = (self.output / 'summary.json').read_text()
        for value in ('never-persist-credential', 'Unicode', '12345678901234567890123456789012', 'SELECT '):
            self.assertNotIn(value, text)


class MainTests(unittest.TestCase):
    def test_only_known_typed_errors_can_become_public_codes(self):
        self.assertEqual(R.code(RuntimeError('TOPSECRET')), 'READ_FAILED')
        self.assertEqual(R.code(RuntimeError('IDENTITY_CHANGED')), 'READ_FAILED')
        self.assertEqual(R.code(R.C.ContractError('TOPSECRET')), 'READ_FAILED')
        self.assertEqual(R.code(R.C.ContractError('IDENTITY_CHANGED')), 'IDENTITY_CHANGED')

    def arguments(self):
        return ['tables', '--evidence-token', TOKEN, '--request-stdin']

    def test_invalid_input_never_calls_invoker(self):
        invoke, output = Mock(), io.StringIO()
        status = R.main(self.arguments(), stdin=io.BytesIO(b'{"command":"tables","schema":"system"}'),
                        stdout=output, invoker=invoke)
        self.assertEqual(status, 1)
        invoke.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())['result'], 'failed')

    def test_action_mismatch_never_calls_invoker(self):
        invoke = Mock()
        status = R.main(self.arguments(), stdin=io.BytesIO(R.C.canonical(request())),
                        stdout=io.StringIO(), invoker=invoke)
        self.assertEqual(status, 1)
        invoke.assert_not_called()

    def test_cli_first_cancellation_is_typed_and_sanitized(self):
        output = io.StringIO()
        status = R.main(self.arguments(), stdin=io.BytesIO(b'{"command":"tables","schema":"smoke"}'),
                        stdout=output, invoker=Mock(side_effect=KeyboardInterrupt('private')))
        self.assertEqual(status, 130)
        value = json.loads(output.getvalue())
        self.assertEqual(value['error_code'], 'INTERRUPTED')
        self.assertIsNone(value['observation'])
        self.assertNotIn('private', output.getvalue())

    def test_workflow_system_exit_zero_is_not_a_successful_read(self):
        output = io.StringIO()
        status = R.main(self.arguments(), stdin=io.BytesIO(b'{"command":"tables","schema":"smoke"}'),
                        stdout=output, invoker=Mock(side_effect=SystemExit(0)))
        self.assertEqual(status, 130)
        self.assertEqual(json.loads(output.getvalue())['result'], 'interrupted')

    def test_output_failure_cannot_be_success(self):
        stream = Mock()
        stream.write.side_effect = BrokenPipeError()
        value = R.event(TOKEN, 'tables', 'passed', {'tables': []}, None, True)
        status = R.main(self.arguments(), stdin=io.BytesIO(b'{"command":"tables","schema":"smoke"}'),
                        stdout=stream, invoker=Mock(return_value=value))
        self.assertEqual(status, 1)

    def test_first_cancel_while_reporting_failure_controls_exit(self):
        stream = Mock()
        stream.write.side_effect = KeyboardInterrupt()
        status = R.main(self.arguments(), stdin=io.BytesIO(b'{"command":"tables","schema":"smoke"}'),
                        stdout=stream, invoker=Mock(side_effect=RuntimeError('ordinary failure')))
        self.assertEqual(status, 130)

    def test_envelope_limit_includes_protocol_fields(self):
        with patch.object(R.C, 'MAX_PUBLIC_BYTES', 400):
            with self.assertRaisesRegex(R.C.ContractError, 'PUBLIC_RESULT_LIMIT'):
                R.event(TOKEN, 'select', 'passed', {'rows': ['a' * 300]}, None, True)


if __name__ == '__main__':
    unittest.main()
