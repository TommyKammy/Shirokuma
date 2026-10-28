"""Retirement CLI transport and strict public response tests, no real profiles."""
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import database_retirement_client as client
import dataops_client


def operation():
    return dict(id='op', action='DATABASE_RETIRE', actor='owner', database_id='db', revision=2, state='RETIRING',
                database=dict(id='db', sql_catalog='sales', state='retiring', revision=4), dependencies=[],
                blockers=[], stopped_schedule_ids=['schedule'])


class RetirementClientTests(unittest.TestCase):
    def setUp(self):
        self.reply = operation()
        self.response = Mock()
        self.opener = Mock()
        self.opener.open.return_value.__enter__ = Mock(return_value=self.response)
        self.opener.open.return_value.__exit__ = Mock(return_value=False)
        self.auth = dict(username='owner', password='synthetic', selected_role='admin')
        self.profile = SimpleNamespace(client_auth_file='synthetic-client-auth')
        for p in (patch.object(client, 'configured_profile', return_value=self.profile),
                  patch.object(dataops_client.runtime_profile, 'private_json', side_effect=lambda _: self.auth),
                  patch.object(dataops_client, 'build_opener', return_value=self.opener)):
            p.start()
            self.addCleanup(p.stop)
        self.raw(self.reply)

    def raw(self, value, literal=False):
        raw = value if literal else json.dumps(value).encode()
        self.response.read.return_value = raw
        self.response.headers = {'Content-Length': str(len(raw))}

    def test_default_and_native_exact_auth_role_and_payload(self):
        for role in ('admin', 'bi_viewer', 'data_engineer'):
            self.auth['selected_role'] = role
            for base in (None, 'http://127.0.0.1:8080'):
                args = ['retire', '--database-id', 'db', '--expected-revision', '3', '--idempotency-key', 'frozen']
                if base:
                    args += ['--base-url', base]
                self.assertEqual(client.main(args), self.reply)
                request = self.opener.open.call_args.args[0]
                self.assertEqual(request.full_url, (base or 'http://127.0.0.1:8888')+'/api/v1/admin/databases/db/retire')
                self.assertEqual(request.get_method(), 'POST')
                self.assertEqual(json.loads(request.data), {'expected_revision': 3})
                headers = dict((k.lower(), v) for k, v in request.header_items())
                self.assertEqual(headers['x-shirokuma-role'], role)
                self.assertEqual(headers['authorization'], 'Basic b3duZXI6c3ludGhldGlj')
                self.assertEqual(headers['idempotency-key'], 'frozen')

    def test_preview_status_and_reconcile(self):
        preview = dict(database=dict(id='db', sql_catalog='sales', state='READY', revision=3),
                       dependencies=[], blockers=[], stopped_schedule_ids=[], operation=None)
        self.raw(preview)
        self.assertEqual(client.main(['retire-preview', '--database-id', 'db']), preview)
        self.assertIsNone(self.opener.open.call_args.args[0].data)
        self.raw(self.reply)
        self.assertEqual(client.main(['retire-status', '--operation-id', 'op']), self.reply)
        self.assertEqual(self.opener.open.call_args.args[0].full_url, 'http://127.0.0.1:8888/api/v1/operations/op')
        client.main(['retire-reconcile', '--operation-id', 'op', '--expected-revision', '2', '--idempotency-key', 'key'])
        request = self.opener.open.call_args.args[0]
        self.assertTrue(request.full_url.endswith('/op/reconcile'))
        self.assertEqual(json.loads(request.data), {'expected_revision': 2})

    def test_reopened_retiring_preview_preserves_actual_stopped_ids(self):
        self.reply['stopped_schedule_ids'] = ['schedule-a', 'schedule-b']
        preview = dict(database=self.reply['database'], dependencies=[], blockers=[],
                       stopped_schedule_ids=list(self.reply['stopped_schedule_ids']), operation=self.reply)
        self.raw(preview)
        for _ in range(2):
            self.assertEqual(client.main(['retire-preview', '--database-id', 'db']), preview)
            request = self.opener.open.call_args.args[0]
            self.assertEqual(request.get_method(), 'GET')
            self.assertIsNone(request.data)

    def test_preview_stopped_ids_require_exact_valid_operation_evidence(self):
        preview = dict(database=self.reply['database'], dependencies=[], blockers=[],
                       stopped_schedule_ids=['schedule'], operation=self.reply)
        invalid = [[], ['other'], ['schedule', 'schedule'], [''], [None], [True], [1], 'schedule', None, {}]
        for stopped in invalid:
            with self.subTest(stopped=stopped):
                with self.assertRaises(ValueError):
                    client.preview(dict(preview, stopped_schedule_ids=stopped), 'db')
                if stopped in ([], ['other']):
                    continue
                with self.assertRaises(ValueError):
                    client.preview(dict(preview, stopped_schedule_ids=stopped,
                                        operation=dict(self.reply, stopped_schedule_ids=stopped)), 'db')
        with self.assertRaises(ValueError):
            client.preview(dict(preview, operation=None), 'db')
        with self.assertRaises(ValueError):
            client.preview(dict(preview, stopped_schedule_ids=['b', 'a'],
                                operation=dict(self.reply, stopped_schedule_ids=['a', 'b'])), 'db')
        initial = dict(preview, operation=None, stopped_schedule_ids=[])
        self.assertEqual(client.preview(initial, 'db'), initial)

    def test_incomplete_malformed_wrong_type_and_result_fields_rejected(self):
        bad = [None, [], {}, dict(self.reply, revision=True), dict(self.reply, revision=0),
               dict(self.reply, action='DATABASE_APPLY'), dict(self.reply, database_id='other'),
               dict(self.reply, state='READY'), dict(self.reply, database=dict(self.reply['database'], state='READY')),
               dict(self.reply, actor=None), dict(self.reply, dependencies={}),
               dict(self.reply, blockers=[dict(kind='run', id='run', database_id='db', state='UNKNOWN')]),
               dict(self.reply, result_url='private'), dict(self.reply, stopped_schedule_ids=[True])]
        for value in bad:
            self.raw(value)
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'outcome may be unknown'):
                client.main(['retire-status', '--operation-id', 'op'])
        for raw in (b'{', b'{"id":"op","id":"other"}', b'{"revision":NaN}'):
            self.raw(raw, True)
            with self.assertRaises(ValueError):
                client.main(['retire-status', '--operation-id', 'op'])
        self.raw(self.reply)
        self.response.headers['Content-Length'] = '1'
        with self.assertRaises(ValueError):
            client.main(['retire-status', '--operation-id', 'op'])

    def test_safe_http_errors_and_no_automatic_retry(self):
        raw = json.dumps(dict(code='REVISION_CONFLICT', message='private-password', details={'sql': 'private'})).encode()
        self.opener.open.side_effect = HTTPError('http://synthetic', 409, 'secret', {}, io.BytesIO(raw))
        with self.assertRaisesRegex(ValueError, '^REVISION_CONFLICT;') as raised:
            client.main(['retire-status', '--operation-id', 'op'])
        self.assertNotIn('private', str(raised.exception))
        self.opener.open.assert_called_once()

    def test_frozen_dependency_links_survive_operation_and_preview(self):
        rows = [dict(kind='stage_scan', id='scan', state='UNKNOWN', database_id='db',
                     stage_id='stage', scan_id='scan', run_id='run', operation_id='scan-op',
                     schedule_id='schedule', subject_id='stage', subject_kind='stage', parent_id='run'),
                dict(kind='mart_run', id='mart-run', state='RUNNING', database_id='db',
                     job_id='job', mart_id='mart', model_id='model', target_id='model', target_kind='model')]
        self.reply.update(dependencies=rows, blockers=[dict(rows[0], code='SCAN_UNKNOWN')])
        self.raw(self.reply)
        self.assertEqual(client.main(['retire-status', '--operation-id', 'op']), self.reply)
        preview = dict(database=self.reply['database'], dependencies=rows,
                       blockers=self.reply['blockers'], stopped_schedule_ids=self.reply['stopped_schedule_ids'], operation=self.reply)
        self.raw(preview)
        self.assertEqual(client.main(['retire-preview', '--database-id', 'db']), preview)

    def test_optional_link_fields_are_exact_nonempty_strings(self):
        base = dict(kind='run', id='run', state='UNKNOWN', database_id='db')
        links = ('job_id', 'stage_id', 'mart_id', 'model_id', 'operation_id', 'run_id', 'scan_id',
                 'schedule_id', 'subject_id', 'subject_kind', 'target_id', 'target_kind', 'parent_id')
        forbidden = ('unknown', 'sql', 'results', 'result_url', 'path', 'query_id', 'export_id',
                     'parent_kind', 'parent_run_id', 'target_model_id')
        invalid = [(name, 'synthetic-forbidden') for name in forbidden]
        invalid += [(name, value) for name in links for value in ('', None, 1, False, [], {})]
        for field in ('dependencies', 'blockers'):
            for name, value in invalid:
                row = dict(base, **({ 'code': 'RUN_UNKNOWN'} if field == 'blockers' else {}))
                row[name] = value
                reply = dict(operation(), **{field: [row]})
                self.raw(reply)
                with self.subTest(field=field, name=name, value=value), self.assertRaisesRegex(ValueError, 'outcome may be unknown'):
                    client.main(['retire-status', '--operation-id', 'op'])
        for field in ('kind', 'id', 'state', 'database_id'):
            with self.subTest(required=field), self.assertRaises(ValueError):
                client.dependencies([{k: v for k, v in base.items() if k != field}], 'db')
        with self.assertRaises(ValueError):
            client.dependencies([dict(base, database_id='other')], 'db')

    def test_resource_ref_canonical_identity_survives_operation_and_preview(self):
        binding_id = 'b125d560-a904-4c22-aafa-dd04aeb60676'
        row = dict(kind='resource_ref', id=binding_id, state='RETAINED', database_id='db',
                   schema_id='b292e623-4f82-48af-8381-f7776395433d', object_id=binding_id, incarnation=2)
        self.reply['dependencies'] = [row]
        self.raw(self.reply)
        self.assertEqual(client.main(['retire-status', '--operation-id', 'op']), self.reply)
        preview = dict(database=self.reply['database'], dependencies=[row], blockers=[],
                       stopped_schedule_ids=self.reply['stopped_schedule_ids'], operation=self.reply)
        self.raw(preview)
        self.assertEqual(client.main(['retire-preview', '--database-id', 'db']), preview)

    def test_resource_ref_rejects_malformed_identity_and_blockers(self):
        binding_id = 'b125d560-a904-4c22-aafa-dd04aeb60676'
        row = dict(kind='resource_ref', id=binding_id, state='RETAINED', database_id='db',
                   schema_id='b292e623-4f82-48af-8381-f7776395433d', object_id=binding_id, incarnation=2)
        invalid = [{k: v for k, v in row.items() if k != name} for name in row]
        invalid += [dict(row, incarnation=v) for v in (True, False, 0, -1, 1.5, 2.0, '2', None, [], {})]
        invalid += [dict(row, **{name: v}) for name in ('schema_id', 'object_id')
                    for v in ('', None, 1, False, [], {})]
        invalid += [dict(row, state='UNKNOWN'), dict(row, object_id='another-binding'),
                    dict(row, database_id='other')]
        invalid += [dict(row, **{name: 'forbidden'}) for name in ('job_id', 'code', 'sql', 'results', 'path', 'unknown')]
        base = dict(kind='run', id='run', state='UNKNOWN', database_id='db')
        other_kind = [dict(base, **{name: row[name]}) for name in ('schema_id', 'object_id', 'incarnation')]
        invalid += other_kind
        blockers = [row, dict(row, code='RESOURCE_RETAINED')] + [dict(bad, code='RUN_UNKNOWN') for bad in other_kind]
        for field, rows in (('dependencies', invalid), ('blockers', blockers)):
            for bad in rows:
                with self.subTest(field=field, row=bad):
                    with self.assertRaises(ValueError):
                        client.operation(dict(operation(), **{field: [bad]}), 'db')
                    with self.assertRaises(ValueError):
                        client.preview(dict(database=operation()['database'], dependencies=[], blockers=[],
                                            stopped_schedule_ids=[], operation=None) | {field: [bad]}, 'db')

    def test_validation_help_and_missing_flags_never_load_profile(self):
        cases = [(['retire', '--help'], SystemExit), (['retire'], SystemExit),
                 (['retire', '--database-id', 'db', '--expected-revision', '0', '--idempotency-key', 'k'], ValueError),
                 (['retire-preview', '--database-id', '../db'], ValueError)]
        with patch.object(client, 'configured_profile') as profile, patch('sys.stdout', new=io.StringIO()), patch('sys.stderr', new=io.StringIO()):
            for args, error in cases:
                with self.assertRaises(error):
                    client.main(args)
            profile.assert_not_called()


if __name__ == '__main__':
    unittest.main()
