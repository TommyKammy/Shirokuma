"""Entry/drain contracts using temporary ledgers and synthetic backends only."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from unittest.mock import Mock

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'tools/local_experiment')]
import control_store
import warehouse_admission as admission
import warehouse_resident as wr
import query_service as qs
import test_query_corrections as query_tests


def drain(warehouse='LOCAL'):
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        # Synthetic operation; no configuration or runtime is touched.
        db.execute("INSERT OR IGNORE INTO operations VALUES ('drain','test','test','[]',NULL,'PENDING','[]',NULL,'{}','{}')")
        admission.begin_drain(db, warehouse_id=warehouse, operation_id='drain')


class QueryEntries(unittest.TestCase):
    setUp = query_tests.QueryCorrections.setUp
    context = query_tests.QueryCorrections.context
    backend = query_tests.QueryCorrections.backend
    submit = query_tests.QueryCorrections.submit
    running = query_tests.QueryCorrections.running

    def test_finished_stats_with_next_uri_keeps_exact_admission_until_last_page(self):
        def transport(url, method, pin, body=None):
            return dict(self.running(), stats={'state': 'FINISHED'}) if method == 'POST' else {
                'id': 'q', 'stats': {'state': 'FINISHED'}}
        with self.backend(transport) as context:
            page = self.submit(context)
            self.assertEqual(page['state'], 'RUNNING')
            with control_store.connect() as db:
                saved = admission.get_for_subject(db, kind='query', subject_id=page['id'], warehouse_id='w')
                self.assertEqual([row['id'] for row in admission.blockers(db, 'w')], [saved.id])
            drain('w')
            with patch.object(wr, 'status', return_value={'active_queries': 0}):
                with self.assertRaises(qs.QueryError):
                    self.submit(context)
            token = page['next_uri'].rsplit('/', 1)[1]
            qs.fetch(self.principal, page['id'], token, selected_role='bi_viewer')
            with control_store.connect() as db:
                self.assertFalse(admission.blockers(db, 'w'))
                self.assertEqual(db.execute('SELECT state FROM warehouse_admissions WHERE id=?', (saved.id,)).fetchone()[0], 'FINISHED')

    def test_denied_query_rolls_back_and_never_sends(self):
        with self.backend(lambda *args: self.fail('backend called')) as context:
            drain(context.warehouse_id)
            with self.assertRaises(qs.QueryError) as error:
                self.submit(context)
            self.assertEqual((error.exception.code, error.exception.status), ('WAREHOUSE_MAINTENANCE', 503))
        with control_store.connect() as db:
            for table in ('query_records', 'query_execution_contexts', 'warehouse_admissions'):
                self.assertEqual(db.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)

    def test_between_pages_drain_continues_and_terminal_releases(self):
        calls = []
        def transport(url, method, pin, body=None):
            calls.append(method)
            self.assertEqual(admission.current(), pin.admission)
            return self.running() if method == 'POST' else {'id': 'q', 'data': [[1]]}
        with self.backend(transport) as context:
            page = self.submit(context)
            drain(context.warehouse_id)
            with control_store.connect() as db:
                self.assertEqual(len(admission.blockers(db, context.warehouse_id)), 1)
            with self.assertRaises(qs.QueryError):
                self.submit(context)
            token = page['next_uri'].rsplit('/', 1)[1]
            self.assertEqual(qs.fetch(self.principal, page['id'], token, selected_role='bi_viewer')['state'], 'FINISHED')
            with control_store.connect() as db:
                self.assertFalse(admission.blockers(db, context.warehouse_id))
        self.assertEqual(calls, ['POST', 'GET'])

    def test_unknown_get_denied_delete_ack_resolves_and_pin_loss_preserves(self):
        def transport(url, method, pin, body=None):
            return self.running() if method == 'POST' else {}
        with self.backend(transport) as context:
            page = self.submit(context)
            token = page['next_uri'].rsplit('/', 1)[1]
            qs._state(page['id'], 'UNKNOWN')
            drain(context.warehouse_id)
            with self.assertRaises(qs.QueryError) as error:
                qs.fetch(self.principal, page['id'], token, selected_role='bi_viewer')
            self.assertEqual(error.exception.code, 'ADMISSION_CONFLICT')
            with patch.object(qs, '_transport', side_effect=OSError):
                with self.assertRaises(qs.QueryError):
                    qs.cancel(self.principal, page['id'], selected_role='bi_viewer')
            with control_store.connect() as db:
                self.assertEqual(admission.blockers(db, context.warehouse_id)[0]['state'], 'UNKNOWN')
            pin = qs._pins.pop(page['id'])
            with self.assertRaises(qs.QueryError) as error:
                qs.cancel(self.principal, page['id'], selected_role='bi_viewer')
            self.assertEqual(error.exception.code, 'QUERY_CONTEXT_LOST')
            qs._pins[page['id']] = pin
            self.assertEqual(qs.cancel(self.principal, page['id'], selected_role='bi_viewer')['state'], 'CANCELED')
            with control_store.connect() as db:
                self.assertFalse(admission.blockers(db, context.warehouse_id))
                self.assertTrue(db.execute('SELECT evidence_ref FROM warehouse_admissions').fetchone()[0])

    def test_unknown_rejects_even_cached_page_without_backend_get(self):
        calls = []
        def transport(url, method, pin, body=None):
            calls.append(method)
            return self.running()
        with self.backend(transport) as context:
            first = self.submit(context)
            token = first['next_uri'].rsplit('/', 1)[1]
            qs.fetch(self.principal, first['id'], token, selected_role='bi_viewer')
            qs._state(first['id'], 'UNKNOWN')
            with self.assertRaises(qs.QueryError) as error:
                qs.fetch(self.principal, first['id'], token, selected_role='bi_viewer')
            self.assertEqual(error.exception.code, 'ADMISSION_CONFLICT')
            self.assertEqual(calls, ['POST', 'GET'])


class QueryReconciliation(unittest.TestCase):
    setUp = query_tests.QueryCorrections.setUp
    context = query_tests.QueryCorrections.context
    backend = query_tests.QueryCorrections.backend
    submit = query_tests.QueryCorrections.submit
    running = query_tests.QueryCorrections.running

    def unknown(self, known=True):
        def transport(url, method, pin, body=None):
            if not known:
                raise qs.QueryError('QUERY_BACKEND_FAILED', 502)
            return self.running()
        with self.backend(transport) as context:
            if known:
                page = self.submit(context)
                qs._state(page['id'], 'UNKNOWN')
                query_id = page['id']
            else:
                with self.assertRaises(qs.QueryError):
                    self.submit(context)
                with control_store.connect() as db:
                    query_id = db.execute('SELECT id FROM query_records ORDER BY rowid DESC LIMIT 1').fetchone()[0]
        return qs._row(query_id)

    def observed(self, row, **changes):
        return dict(dict(id=row['backend_query_id'] or 'backend-unique',
            source='shirokuma-query-' + row['id'], user=row['backend_username'],
            sql=row['sql'], state='FINISHED'), **changes)

    def reconcile(self, row, observed, principal=None, role='bi_viewer'):
        return qs.reconcile_query(principal or self.principal, row['id'], selected_role=role,
                                  observer=lambda expected: observed)

    def test_lost_post_response_retains_server_source_and_unique_terminal_match_attaches_id(self):
        row = self.unknown(known=False)
        self.assertIsNone(row['backend_query_id'])
        self.assertEqual(qs._pins[row['id']].headers['X-Trino-Source'], 'shirokuma-query-' + row['id'])
        expected = []
        def observer(value):
            expected.append(value)
            return [self.observed(row)]
        result = qs.reconcile_query(self.principal, row['id'], selected_role='bi_viewer', observer=observer)
        self.assertEqual(set(expected[0]), {'query_id', 'backend_query_id', 'warehouse_id', 'backend_username', 'source', 'sql'})
        self.assertEqual(result, dict(id=row['id'], state='FINISHED', backend_query_id='backend-unique'))
        with control_store.connect() as db:
            self.assertFalse(admission.blockers(db, 'w'))
            self.assertTrue(db.execute('SELECT evidence_ref FROM warehouse_admissions').fetchone()[0])
            self.assertNotIn('Authorization', repr([tuple(item) for item in db.execute('SELECT * FROM query_execution_contexts')]))

    def test_known_exact_terminal_outcomes_release_atomically_and_are_idempotent(self):
        for state in ('FINISHED', 'FAILED', 'CANCELED'):
            row = self.unknown()
            result = self.reconcile(row, [self.observed(row, state=state)])
            self.assertEqual(result['state'], state)
            observer = Mock(side_effect=AssertionError('terminal observation repeated'))
            self.assertEqual(qs.reconcile_query(self.principal, row['id'], selected_role='bi_viewer', observer=observer), result)
            observer.assert_not_called()
            with control_store.connect() as db:
                self.assertEqual(db.execute('SELECT state FROM warehouse_admissions WHERE subject_id=?', (row['id'],)).fetchone()[0], state)

    def test_wrong_or_ambiguous_or_missing_evidence_never_releases_or_attaches(self):
        row = self.unknown(known=False)
        matching = self.observed(row)
        cases = [[self.observed(row, user='wrong')], [self.observed(row, source='wrong')],
            [self.observed(row, sql='SELECT other')], [matching, matching], [], None, [{}],
            [self.observed(row, state='RUNNING')], [self.observed(row, id='../unsafe')]]
        for records in cases:
            self.assertEqual(self.reconcile(row, records)['state'], 'UNKNOWN')
            self.assertIsNone(qs._row(row['id'])['backend_query_id'])
        known = self.unknown()
        self.assertEqual(self.reconcile(known, [self.observed(known, id='wrong')])['state'], 'UNKNOWN')
        with control_store.connect() as db:
            self.assertEqual(len(admission.blockers(db, 'w')), 2)

    def test_owner_cleanup_after_grant_loss_and_max_downgrade_keeps_saved_context(self):
        row = self.unknown()
        with control_store.connect() as db:
            db.execute("DELETE FROM grants WHERE principal_id='alice'")
        self.fixture.users['alice']['role'] = 'bi_viewer'
        self.fixture.save()
        self.assertEqual(self.reconcile(row, [self.observed(row)], role='admin')['state'], 'FINISHED')
        saved = qs._row(row['id'])
        self.assertEqual(saved['selected_role'], row['selected_role'])
        self.assertEqual(saved['sql_catalog'], row['sql_catalog'])

    def test_unauthorized_and_downgraded_platform_admin_other_owner_are_denied(self):
        import request_auth
        row = self.unknown()
        admin = self.fixture.principal('owner')
        observer = Mock(return_value=[self.observed(row)])
        downgraded = request_auth.AuthenticatedPrincipal('owner', 'bi_viewer', True)
        for principal, role in [(object(), 'admin'), (admin, 'bi_viewer'), (downgraded, 'admin')]:
            with self.assertRaises(qs.QueryError):
                qs.reconcile_query(principal, row['id'], selected_role=role, observer=observer)
        self.fixture.users['owner'].update(role='bi_viewer', platform_admin=False)
        self.fixture.save()
        with self.assertRaises(qs.QueryError):
            qs.reconcile_query(admin, row['id'], selected_role='admin', observer=observer)
        observer.assert_not_called()
        with self.assertRaises(qs.QueryError):
            qs.reconcile_query(self.principal, row['id'], selected_role='viewer', observer=observer)

    def test_current_platform_admin_can_observe_other_owner(self):
        row = self.unknown()
        self.assertEqual(self.reconcile(row, [self.observed(row)], self.fixture.principal('owner'), 'admin')['state'], 'FINISHED')

    def test_live_pinned_pagination_cannot_be_reconciled_but_lost_pin_can(self):
        with self.backend(lambda *args: self.running()) as context:
            page = self.submit(context)
        row = qs._row(page['id'])
        observer = Mock(return_value=[self.observed(row)])
        with self.assertRaises(qs.QueryError) as error:
            qs.reconcile_query(self.principal, row['id'], selected_role='bi_viewer', observer=observer)
        self.assertEqual(error.exception.status, 409)
        observer.assert_not_called()
        self.assertEqual(qs._row(row['id'])['state'], 'RUNNING')
        with control_store.connect() as db:
            self.assertEqual(admission.blockers(db, 'w')[0]['state'], 'ACTIVE')
        qs._close_query_owner(qs._pins.pop(row['id']))
        self.assertEqual(qs.reconcile_query(self.principal, row['id'], selected_role='bi_viewer', observer=observer)['state'], 'FINISHED')

    def lost_pin(self, state='RUNNING'):
        with self.backend(lambda *args: dict(self.running(), stats={'state': state})) as context:
            page = self.submit(context)
        qs._close_query_owner(qs._pins.pop(page['id']))
        return qs._row(page['id'])

    def test_second_process_cannot_bypass_live_client_owner(self):
        def transport(url, method, pin, body=None):
            self.assertIsNotNone(pin.owner)
            with self.assertRaises(qs.QueryError) as error:
                qs._acquire_query_owner(pin.admission.subject_id)
            self.assertEqual(error.exception.status, 409)
            return dict(self.running(), stats={'state': 'FINISHED'})
        with self.backend(transport) as context:
            page = self.submit(context)
        query_id = page['id']
        self.assertEqual(page['state'], 'RUNNING')
        self.assertTrue(page['next_uri'])
        pin = qs._pins[query_id]
        path = next((control_store.root() / 'runtime/query-owners').iterdir())
        inode = path.stat().st_ino
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.stat().st_size, 0)
        script = '''
import json, sys
sys.path.insert(0, sys.argv[1])
import query_service as qs
import request_auth, control_store, warehouse_admission as admission
principal = request_auth.AuthenticatedPrincipal('alice', 'data_engineer', False)
query_id, mode = sys.argv[2:]
calls = []
def observer(expected):
    calls.append(1)
    if mode == 'absent':
        return []
    return [dict(id=expected['backend_query_id'], source=expected['source'],
                 user=expected['backend_username'], sql=expected['sql'], state='FINISHED')]
result = dict(pins=len(qs._pins))
try:
    result.update(qs.reconcile_query(principal, query_id, selected_role='bi_viewer', observer=observer))
except qs.QueryError as error:
    result.update(error=error.code, status=error.status)
with control_store.connect() as db:
    result['query_state'] = db.execute('SELECT state FROM query_records WHERE id=?', (query_id,)).fetchone()[0]
    handle = admission.get_for_subject(db, kind='query', subject_id=query_id, warehouse_id='w')
    result['token_state'] = db.execute('SELECT state FROM warehouse_admissions WHERE id=?', (handle.id,)).fetchone()[0]
result['observer_calls'] = len(calls)
print(json.dumps(result))
'''
        def child(mode):
            completed = subprocess.run([sys.executable, '-c', script, str(Path(qs.__file__).parent), query_id, mode],
                env=dict(os.environ), capture_output=True, text=True, check=True, timeout=15)
            return json.loads(completed.stdout)
        blocked = child('terminal')
        self.assertEqual((blocked['pins'], blocked['status'], blocked['observer_calls']), (0, 409, 0))
        self.assertEqual((blocked['query_state'], blocked['token_state']), ('RUNNING', 'ACTIVE'))
        # Keep an old pin reference deliberately; true process loss must close
        # its descriptor, rather than merely removing the dictionary entry.
        lease = pin.owner
        qs._close_query_owner(qs._pins.pop(query_id))
        self.assertTrue(lease.closed)
        unknown = child('absent')
        self.assertEqual((unknown['observer_calls'], unknown['query_state'], unknown['token_state']), (1, 'UNKNOWN', 'UNKNOWN'))
        terminal = child('terminal')
        self.assertEqual((terminal['observer_calls'], terminal['query_state'], terminal['token_state']), (1, 'FINISHED', 'FINISHED'))
        repeated = child('terminal')
        self.assertEqual((repeated['observer_calls'], repeated['query_state']), (0, 'FINISHED'))
        self.assertTrue(path.exists())
        self.assertEqual(path.stat().st_ino, inode)
        self.assertEqual(path.stat().st_size, 0)
        with qs._acquire_query_owner(query_id):
            pass

    def test_failed_submit_transaction_closes_owner_before_pin_insertion(self):
        acquired = []
        acquire = qs._acquire_query_owner
        def capture(query_id):
            owner = acquire(query_id)
            acquired.append((query_id, owner))
            return owner
        with self.backend(lambda *args: self.fail('POST after failed transaction')) as context, \
                patch.object(qs, '_acquire_query_owner', side_effect=capture), \
                patch.object(qs.registry, 'record_query_execution', side_effect=RuntimeError('synthetic failure')):
            with self.assertRaises(RuntimeError):
                self.submit(context)
        self.assertEqual(len(acquired), 1)
        query_id, owner = acquired[0]
        self.assertTrue(owner.closed)
        self.assertNotIn(query_id, qs._pins)
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM query_records').fetchone()[0], 0)
            self.assertFalse(admission.blockers(db, 'w'))
        with acquire(query_id):
            pass

    def test_owner_retained_on_failed_terminal_commit_then_closed_on_unknown(self):
        with self.backend(lambda *args: self.running()) as context:
            page = self.submit(context)
        pin = qs._pins[page['id']]
        owner = pin.owner
        with patch.object(admission, 'finish', side_effect=admission.AdmissionConflict('synthetic rollback')):
            with self.assertRaises(admission.AdmissionConflict):
                qs._state(page['id'], 'FINISHED')
        self.assertFalse(owner.closed)
        self.assertEqual(qs._row(page['id'])['state'], 'RUNNING')
        with self.assertRaises(qs.QueryError):
            qs._acquire_query_owner(page['id'])
        qs._state(page['id'], 'UNKNOWN')
        self.assertTrue(owner.closed)
        self.assertIsNone(pin.owner)
        with qs._acquire_query_owner(page['id']):
            with control_store.connect() as db:
                self.assertEqual(admission.blockers(db, 'w')[0]['state'], 'UNKNOWN')

    def test_terminal_commit_and_idempotent_cancel_reconcile_close_local_owner(self):
        with self.backend(lambda *args: self.running()) as context:
            page = self.submit(context)
        pin = qs._pins[page['id']]
        initial = pin.owner
        qs._state(page['id'], 'FINISHED')
        self.assertTrue(initial.closed)
        for cleanup in ('cancel', 'reconcile'):
            retained = pin.owner = qs._acquire_query_owner(page['id'])
            with patch.object(qs, '_transport') as transport:
                if cleanup == 'cancel':
                    result = qs.cancel(self.principal, page['id'], selected_role='bi_viewer')
                else:
                    observer = Mock(side_effect=AssertionError('terminal observation repeated'))
                    result = qs.reconcile_query(self.principal, page['id'], selected_role='bi_viewer', observer=observer)
                    observer.assert_not_called()
                transport.assert_not_called()
            self.assertEqual(result['state'], 'FINISHED')
            self.assertTrue(retained.closed)
            self.assertIsNone(pin.owner)

    def test_pin_lost_nonterminal_without_proof_becomes_unknown_atomically(self):
        for state in ('RUNNING', 'QUEUED'):
            for kind in ('absent', 'nonterminal', 'malformed'):
                with self.subTest(state=state, evidence=kind):
                    row = self.lost_pin(state)
                    observed = {'absent': [], 'nonterminal': [self.observed(row, state='RUNNING')],
                                'malformed': [{}]}[kind]
                    with patch.object(qs, '_transport') as transport:
                        self.assertEqual(self.reconcile(row, observed)['state'], 'UNKNOWN')
                        transport.assert_not_called()
                    with control_store.connect() as db:
                        self.assertEqual(db.execute('SELECT state FROM query_records WHERE id=?', (row['id'],)).fetchone()[0], 'UNKNOWN')
                        token = next(item for item in admission.blockers(db, 'w') if item['subject_id'] == row['id'])
                        self.assertEqual(token['state'], 'UNKNOWN')
                        self.assertIsNone(token['evidence_ref'])
                    self.assertEqual(qs._row(row['id'])['backend_query_id'], row['backend_query_id'])

    def test_pin_loss_owner_reads_and_unauthorized_reconcile_do_not_mutate(self):
        row = self.lost_pin()
        observer = Mock(return_value=[])
        with self.assertRaises(qs.QueryError):
            qs.reconcile_query(object(), row['id'], selected_role='admin', observer=observer)
        observer.assert_not_called()
        qs.inspect_query(self.principal, row['id'], selected_role='bi_viewer')
        qs.history(self.principal, database_id=row['database_id'], selected_role='bi_viewer')
        with self.assertRaises(qs.QueryError) as error:
            qs.fetch(self.principal, row['id'], 'missing', selected_role='bi_viewer')
        self.assertEqual(error.exception.code, 'QUERY_CONTEXT_LOST')
        self.assertEqual(qs._row(row['id'])['state'], 'RUNNING')
        with control_store.connect() as db:
            self.assertEqual(admission.blockers(db, 'w')[0]['state'], 'ACTIVE')
            db.execute("DELETE FROM grants WHERE principal_id='alice'")
        # Explicit owner cleanup remains permitted after losing the DB grant.
        result = qs.reconcile_query(self.principal, row['id'], selected_role='admin', observer=observer)
        self.assertEqual(result['state'], 'UNKNOWN')
        observer.assert_called_once()

    def test_pin_lost_observer_failure_persists_unknown_before_safe_error(self):
        row = self.lost_pin()
        with self.assertRaises(qs.QueryError) as error:
            qs.reconcile_query(self.principal, row['id'], selected_role='bi_viewer',
                observer=Mock(side_effect=OSError('private backend details')))
        self.assertEqual(error.exception.code, 'QUERY_BACKEND_FAILED')
        self.assertNotIn('private', str(error.exception))
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM query_records WHERE id=?', (row['id'],)).fetchone()[0], 'UNKNOWN')
            self.assertEqual(admission.blockers(db, 'w')[0]['state'], 'UNKNOWN')

    def test_pin_lost_concurrent_terminal_completion_wins_over_missing_proof(self):
        row = self.lost_pin()
        def observer(expected):
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                handle = admission.get_for_subject(db, kind='query', subject_id=row['id'], warehouse_id='w')
                qs.registry.set_query_state(db, row['id'], 'FINISHED')
                admission.finish(db, handle, state='FINISHED')
            return []
        result = qs.reconcile_query(self.principal, row['id'], selected_role='bi_viewer', observer=observer)
        self.assertEqual(result['state'], 'FINISHED')
        with control_store.connect() as db:
            self.assertFalse(admission.blockers(db, 'w'))
        repeated = Mock(side_effect=AssertionError('terminal observation repeated'))
        self.assertEqual(qs.reconcile_query(self.principal, row['id'], selected_role='bi_viewer', observer=repeated), result)
        repeated.assert_not_called()

    def test_concurrent_terminal_completion_is_not_rewound(self):
        row = self.unknown()
        def observer(expected):
            qs._state(row['id'], 'FINISHED', evidence_ref='synthetic-terminal-observation')
            return [self.observed(row, state='FAILED')]
        self.assertEqual(qs.reconcile_query(self.principal, row['id'], selected_role='bi_viewer', observer=observer)['state'], 'FINISHED')
        with control_store.connect() as db:
            self.assertFalse(admission.blockers(db, 'w'))

    @contextmanager
    def metadata_backend(self, responses):
        import trino_auth
        path = self.fixture.root / 'synthetic-v2.json'
        config = dict(version=2, platform_admin=dict(username='shirokuma_admin', password='synthetic-admin-secret'),
            databases={self.fixture.sales: dict(sql_catalog='sales', generation_id='synthetic',
                roles={role: dict(username=trino_auth.scoped_username(self.fixture.sales, role), password='synthetic-' + role)
                       for role in ('bi_viewer', 'data_engineer')})})
        path.write_text(json.dumps(config))
        path.chmod(0o600)
        @contextmanager
        def forward():
            yield 'https://trusted:8443'
        with patch.dict(os.environ, SHIROKUMA_TRINO_AUTH_FILE=str(path), SHIROKUMA_TRINO_AUTH_MODE='password'), \
                patch('trino_auth.backend_ssl_context', return_value=None), \
                patch('warehouse_resident.persistent_trino', forward), \
                patch.object(qs.http.client, 'HTTPSConnection') as connection:
            sequence = []
            for status, payload in responses:
                response = Mock(status=status)
                response.read.return_value = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                sequence.append(response)
            connection.return_value.getresponse.side_effect = sequence
            yield connection

    def metadata_record(self, row, **changes):
        item = self.observed(row, **changes)
        return dict(queryId=item['id'], state=item['state'], query=item['sql'],
                    session=dict(source=item['source'], user=item['user']))

    def test_backend_observer_uses_only_get_and_current_manifest_admin_credentials(self):
        import base64
        row = self.unknown(known=False)
        record = self.metadata_record(row)
        with self.metadata_backend([(200, [record]), (200, record)]) as connection:
            self.assertEqual(qs.reconcile_query_from_backend(self.principal, row['id'], selected_role='bi_viewer')['state'], 'FINISHED')
            calls = connection.return_value.request.call_args_list
            self.assertEqual([call.args for call in calls], [('GET', '/v1/query'), ('GET', '/v1/query/backend-unique')])
            expected = 'Basic ' + base64.b64encode(b'shirokuma_admin:synthetic-admin-secret').decode()
            self.assertTrue(all(call.kwargs['headers']['Authorization'] == expected for call in calls))
            self.assertTrue(all('body' not in call.kwargs for call in calls))

    def test_backend_list_ignores_unrelated_untagged_query(self):
        row = self.unknown(known=False)
        unrelated = self.metadata_record(row, id='untagged', source=None, user='other', sql='SELECT unrelated')
        target = self.metadata_record(row)
        with self.metadata_backend([(200, [unrelated, target]), (200, target)]) as connection:
            result = qs.reconcile_query_from_backend(self.principal, row['id'], selected_role='bi_viewer')
            self.assertEqual(result['state'], 'FINISHED')
            self.assertEqual(result['backend_query_id'], 'backend-unique')
            self.assertEqual(connection.return_value.request.call_count, 2)
        with control_store.connect() as db:
            self.assertFalse(admission.blockers(db, 'w'))

    def test_backend_list_duplicate_or_malformed_matching_targets_stay_unknown(self):
        row = self.unknown(known=False)
        unrelated = self.metadata_record(row, id='untagged', source=None)
        target = self.metadata_record(row)
        malformed = dict(target, queryId='../unsafe')
        for matches in ([target, target], [malformed], [target, malformed], [dict(target, state=None)]):
            with self.subTest(matches=len(matches)), self.metadata_backend([(200, [unrelated] + matches)]) as connection:
                result = qs.reconcile_query_from_backend(self.principal, row['id'], selected_role='bi_viewer')
                self.assertEqual(result['state'], 'UNKNOWN')
                self.assertIsNone(result['backend_query_id'])
                connection.return_value.request.assert_called_once()
        with control_store.connect() as db:
            self.assertEqual(admission.blockers(db, 'w')[0]['state'], 'UNKNOWN')

    def test_observer_not_found_expired_auth_transport_redirect_and_malformed_are_safe(self):
        row = self.unknown()
        for status, payload in [(404, {}), (410, {}), (200, {}), (200, {'queryId': 'q'})]:
            with self.metadata_backend([(status, payload)]) as connection:
                self.assertEqual(qs.reconcile_query_from_backend(self.principal, row['id'], selected_role='bi_viewer')['state'], 'UNKNOWN')
                self.assertEqual(connection.return_value.request.call_args.args, ('GET', '/v1/query/q'))
        for status, payload in [(401, {'password': 'private'}), (403, {}), (302, {}), (200, b'private invalid JSON')]:
            with self.metadata_backend([(status, payload)]), self.assertRaises(qs.QueryError) as error:
                qs.reconcile_query_from_backend(self.principal, row['id'], selected_role='bi_viewer')
            self.assertEqual((error.exception.code, error.exception.status), ('QUERY_BACKEND_FAILED', 502))
            self.assertNotIn('private', str(error.exception))
        with self.metadata_backend([]) as connection:
            connection.return_value.getresponse.side_effect = OSError('private credential text')
            with self.assertRaises(qs.QueryError) as error:
                qs.reconcile_query_from_backend(self.principal, row['id'], selected_role='bi_viewer')
            self.assertNotIn('private', str(error.exception))
        with control_store.connect() as db:
            self.assertEqual(admission.blockers(db, 'w')[0]['state'], 'UNKNOWN')

    def test_observer_validates_id_before_constructing_url(self):
        with patch('trino_auth.load_database_credentials') as credentials, self.assertRaises(qs.QueryError):
            qs.observe_query_backend(dict(backend_query_id='../unsafe'))
        credentials.assert_not_called()
