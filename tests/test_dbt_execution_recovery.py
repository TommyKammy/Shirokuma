"""Canonical native publication graphs and durable recovery; physical IO mocked."""
from copy import deepcopy
from dataclasses import replace
import io
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import catalog_admin
import control_store
import dataops_service as ds
import data_management as registry
import db_dbt as dbt
import dbt_execution_recovery as recovery
import execution_recovery
import logical_execution_recovery as logical
import query_service as queries
from query_context import QueryError
import warehouse_admission as admission
import test_db_dbt as fixtures
import test_database_dbt_graph_publication_views as view_fixtures
import test_database_dbt_graph_publication as graph_fixtures
import database_inventory
import database_dbt_graph


class Physical:
    """Test cutset: positive membership plus every original stop is mandatory."""
    reconcile_read_only = True

    def __init__(self):
        engine = dict(engine_id='engine', socket='unix:///colima/docker.sock', node_uid='node-uid', node_name='node')
        self.scope_value = dict(kind='logical-current-managed-scope-v1', **engine,
            foundation_id='foundation', revision=1, pins={'namespace': 'namespace-uid'},
            material_digest='sealed-material', service_uid='service-uid', members=[
            dict(**engine, container_id=('a' if index == 0 else 'b') * 64,
                labels=dict(zip(execution_recovery.MANAGED_LABELS,
                    ('pod-' + name, 'logical-ns', name, 'trino'))), image='sha256:fixed-image',
                started_at='2026-01-01T00:00:00Z', pod_uid='pod-' + name,
                pod_created_at='2025-12-01T00:00:00Z', restart_count=0,
                deployment='trino-local' if index == 0 else 'trino-local-worker',
                deployment_uid='deployment-' + name, replica_set_uid='rs-' + name)
            for index, name in enumerate(('coordinator', 'worker'))])
        self.stopped = False
        self.present = True
        self.membership = True
        self.scope = Mock(return_value=self.scope_value)
        self.stop = Mock(side_effect=self._stop)
        self.remove = Mock(side_effect=self._remove)
        self.healthy = Mock()

    def engine(self):
        return dict(engine_id='engine')

    def _stop(self, scope):
        self.stopped = True

    def _remove(self, scope):
        self.present = False

    def old_present(self, scope):
        return self.present

    def proof(self, scope, since):
        if self.stopped:
            return dict(kind='logical-all-members-stopped-v1', engine_id=scope['engine_id'],
                scope_digest=hashlib.sha256(execution_recovery._json(scope).encode()).hexdigest(),
                members=[dict(kind='exact-event', engine_id=scope['engine_id'], receipt=dict(
                    container_id=m['container_id'], labels=m['labels'], action='die',
                    time_nano=execution_recovery._nano(since) + 1)) for m in scope['members']])

    def validate_stop_scope(self, graph, scope, since):
        execution_recovery._require(self.membership, 'RECOVERY_MEMBERSHIP_UNPROVED')

    def validate_proof(self, scope, proof, since):
        execution_recovery._require(self.membership and proof == self.proof(scope, since), 'RECOVERY_STOP_UNPROVED')


class DbtRecoveryTests(unittest.TestCase):
    fixture_type = graph_fixtures.GraphPublicationTests

    def setUp(self):
        self.f = self.fixture_type()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        if self.fixture_type is view_fixtures.ViewPublicationTests:
            self.f.artifact_patch.stop()
            (self.f.project / 'models/extra_result.sql').write_text("SELECT id, label FROM {{ ref('final_result') }}")
            self.f.save_plan()
        # Shared DataOps harness defaults to warehouse 'w'; this recovery is
        # explicitly the single LOCAL logical foundation boundary.
        with control_store.connect() as db:
            db.execute("UPDATE warehouse_config_generations SET warehouse_id='LOCAL'")
            for row in db.execute('SELECT id,record FROM dbt_runs').fetchall():
                record = json.loads(row['record'])
                record['context']['warehouse_id'] = 'LOCAL'
                db.execute('UPDATE dbt_runs SET record=? WHERE id=?', (ds._json(record), row['id']))
        def context(database=None, schema='raw', principal=None, role='data_engineer'):
            database = database or self.f.b.databases[0]
            return queries.resolve_context(principal or self.f.b.principal, database,
                self.f.b.schemas[database][schema], role, warehouse_id='LOCAL', expected_binding_revision=1)
        self.f.b.context = context
        self.f.context = context(schema='sandbox')
        self.source_run = self.f.candidate()
        self.expected = self.f.preview(self.source_run)['expected_target']
        self.ack = self.f.accept(self.source_run, self.expected)
        self.principal, self.context = self.f.publisher()
        with patch.object(queries, '_transport', side_effect=OSError('response lost')):
            self.f.finish(self.ack)
        self.ident = self.ack['id']
        for live in ds._live.values():
            live[2].close()
        with control_store.connect() as db:
            db.execute("UPDATE warehouse_config_generations SET state='APPLIED' WHERE id=?", (self.context.generation_id,))
            db.execute('UPDATE warehouse_controls SET applied_generation_id=? WHERE warehouse_id=?',
                (self.context.generation_id, self.context.warehouse_id))
            self.query_id = db.execute("SELECT q.id FROM query_records q JOIN dataops_queries d ON d.query_id=q.id "
                "JOIN dbt_run_operations l ON l.operation_id=d.operation_id WHERE l.run_id=?", (self.ident,)).fetchone()[0]
            self.original = recovery._snapshot(db, self.ident)
        for target, name, value in ((catalog_admin, 'authenticate_headers', (self.principal, 'admin')),
            (logical.logical_creations, 'compute_blockers', {}),
            (logical.warehouse_lifecycle, 'runtime_blockers', [])):
            p = patch.object(target, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        self.operator = recovery.DbtAuditRecovery.__new__(recovery.DbtAuditRecovery)
        self.operator.actor = self.principal.principal_id
        self.operator.config = dict(auth_headers='/private/headers', runtime_profile='/private/profile')
        self.operator.seal = dict(version=1)
        self.physical = Physical()
        self.operator.adapter = Mock(return_value=self.physical)

    def plan(self, key='abandon-audit'):
        return self.operator.plan(self.ident, key)

    def apply(self, plan, **kwargs):
        return self.operator.apply(plan['operation_id'], plan['revision'],
            expected_intent_digest=plan['intent_digest'], **kwargs)

    def mutate(self, sql, args=()):
        with control_store.connect() as db:
            db.execute(sql, args)

    def test_unknown_outcomes_preserved_and_physical_parents_close(self):
        # A previous independently fenced UNKNOWN is historical, not a blocker.
        with control_store.connect() as db:
            for table, original, key in (('query_records', self.original['queries'][0], 'id'),
                ('query_execution_contexts', self.original['executions'][0], 'query_id')):
                saved = dict(original, **{key: 'older-fenced-query'})
                if table == 'query_execution_contexts':
                    saved.update(request_id='older-request')
                db.execute('INSERT INTO ' + table + ' (' + ','.join(saved) + ') VALUES (' +
                    ','.join('?' for _ in saved) + ')', tuple(saved.values()))
            a = dict(self.original['admissions'][2], id='older-admission', subject_id='older-fenced-query',
                parent_id=None, execution_closure='FENCED_READ_ONLY', evidence_ref='previous-recovery')
            db.execute('INSERT INTO warehouse_admissions (' + ','.join(a) + ') VALUES (' +
                ','.join('?' for _ in a) + ')', tuple(a.values()))
        plan = self.plan()
        self.assertEqual(self.plan(), plan)
        closed = self.apply(plan)
        self.assertEqual(closed['phase'], 'OPEN')
        self.physical.stop.assert_called_once()
        with control_store.connect() as db:
            after = recovery._snapshot(db, self.ident)
            recovery.verified_closure(db, after['publication'])
            self.assertEqual(after['queries'], self.original['queries'])
            self.assertEqual(after['executions'], self.original['executions'])
            self.assertIsNone(after['executions'][0]['backend_query_id'])
            self.assertEqual(after['source'], self.original['source'])
            self.assertEqual(after['query_links'], self.original['query_links'])
            self.assertEqual(after['operations'][0]['result'], self.original['operations'][0]['result'])
            self.assertFalse(after['operations'][0]['committed'])
            summary = json.loads(after['operations'][0]['cleanup_error'])
            self.assertEqual(summary['code'], 'OPERATOR_ABANDONED_READ_ONLY')
            self.assertEqual(summary['query_outcome'], 'UNKNOWN')
            self.assertEqual([a['state'] for a in after['admissions']], ['FAILED', 'FAILED', 'UNKNOWN'])
            self.assertEqual(after['publication']['state'], 'UNKNOWN')
            self.assertEqual(after['publication']['error_code'], self.original['publication']['error_code'])
            self.assertIsNone(after['publication'].get('native_audit'))
            self.assertFalse(after['publication']['committed'])
            self.assertFalse(admission.blockers(db))
        with patch.object(queries, '_transport', side_effect=AssertionError('SQL replay')):
            public = dbt.get_publication(self.principal, self.context, self.ident)
            self.assertEqual(public['execution_closure'], 'FENCED_READ_ONLY')
            self.assertEqual(public['execution_recovery'], dict(operation_id=closed['operation_id'],
                phase='OPEN', query_outcome='UNKNOWN'))
            self.assertNotIn('recovery', public)
            self.assertEqual(dbt.list_publications(self.principal, self.context), [public])
            self.assertEqual(self.f.accept(self.source_run, self.expected), public)
            dbt.reconcile_run(self.principal, self.context, self.source_run['id'], key='observe-closed')
            self.assertEqual(dbt.get_publication(self.principal, self.context, self.ident), public)
            execution = self.f.b.begin(key='unrelated-after-open', context=self.context, principal=self.principal)
            self.assertEqual(execution._row()['state'], 'ACTIVE')
            execution.fail()
            self.assertEqual(dbt.get_publication(self.principal, self.context, self.ident), public)
        with control_store.connect() as db:
            old = db.execute("SELECT state FROM query_records WHERE id='older-fenced-query'").fetchone()[0]
            self.assertEqual(old, 'UNKNOWN')
        self.assertEqual(self.apply(closed), closed)
        self.physical.stop.assert_called_once()

    def test_digest_revision_runtime_and_key_compare_and_swap(self):
        plan = self.plan()
        for change, code in ((dict(expected_revision=999), 'REVISION_CONFLICT'),
                             (dict(expected_intent_digest='wrong'), 'RECOVERY_INTENT_MISMATCH')):
            args = dict(expected_revision=plan['revision'], expected_intent_digest=plan['intent_digest'], **{})
            args.update(change)
            with self.assertRaises(QueryError) as caught:
                self.operator.apply(plan['operation_id'], **args)
            self.assertEqual(caught.exception.code, code)
        self.operator.seal = dict(changed=True)
        with self.assertRaises(QueryError):
            self.plan()
        self.physical.stop.assert_not_called()

    def test_inspect_reconcile_and_crash_boundaries_never_repeat_stop(self):
        self.mutate('UPDATE query_execution_contexts SET backend_query_id=? WHERE query_id=?',
                    ('original_backend_id', self.query_id))
        plan = self.plan()
        self.assertEqual(self.apply(plan, reconcile=True), plan)
        self.physical.stop.assert_not_called()
        with patch.object(self.operator, '_close', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.apply(plan)
        proved = self.operator.inspect(plan['operation_id'])
        self.assertEqual(proved['phase'], 'PROVED')
        # A new process retains only durable rows, without resident credentials.
        queries._pins.pop(self.query_id, None)
        self.physical.stop.assert_called_once()
        self.assertEqual(self.apply(proved, reconcile=True)['phase'], 'CLOSED')
        closed = self.operator.inspect(plan['operation_id'])
        with patch.object(queries, '_transport', side_effect=AssertionError('SQL')):
            public = dbt.get_publication(self.principal, self.context, self.ident)
            self.assertEqual(public['execution_closure'], 'FENCED_READ_ONLY')
            self.assertEqual(public['execution_recovery'], dict(operation_id=closed['operation_id'],
                phase='CLOSED', query_outcome='UNKNOWN'))
        with patch.object(self.physical, 'healthy', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.apply(closed)
        self.assertEqual(self.operator.inspect(plan['operation_id']), closed)
        self.assertEqual(self.apply(closed)['phase'], 'OPEN')
        self.physical.stop.assert_called_once()
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT backend_query_id FROM query_execution_contexts WHERE query_id=?',
                (self.query_id,)).fetchone()[0], 'original_backend_id')

    def test_marker_forgery_and_wrong_recovery_id_fail_closed(self):
        plan = self.plan()
        other = self.plan(key='second-real-receipt')
        self.apply(plan)
        self.f.mutate(self.ident, lambda p: p['_execution_recovery'].update(operation_id=other['operation_id']))
        with self.assertRaises(QueryError):
            dbt.get_publication(self.principal, self.context, self.ident)
        self.f.mutate(self.ident, lambda p: p.update(_execution_recovery='forged'))
        with patch.object(dbt, '_settle', side_effect=AssertionError('settlement bypass')):
            with self.assertRaises(QueryError):
                dbt.get_publication(self.principal, self.context, self.ident)

    def test_mutations_of_sql_links_results_actor_and_context_are_rejected(self):
        with control_store.connect() as db:
            admission.control(db, 'foreign')
            registry.record_generation(db, registry.WarehouseConfigGeneration(
                'foreign-generation', 'foreign', 'foreign-apply', (), (), (), 'APPLIED'))
            legacy = dict(dbt._row(db, self.source_run['plan_id'])[1],
                execution_closure='FENCED_READ_ONLY', execution_recovery={'operation_id': 'forged'},
                recovery={'query_outcome': 'UNKNOWN'})
            public = dbt._public(db, legacy)
            self.assertTrue(all(key not in public for key in ('execution_closure', 'execution_recovery', 'recovery')))
        changes = [
            ("UPDATE query_execution_contexts SET sql='DROP TABLE unsafe' WHERE query_id=?", (self.query_id,)),
            ("UPDATE query_execution_contexts SET mode='read-only' WHERE query_id=?", (self.query_id,)),
            ("UPDATE query_execution_contexts SET sql=sql || ' ' WHERE query_id=?", (self.query_id,)),
            ("UPDATE query_execution_contexts SET grant_revision=99 WHERE query_id=?", (self.query_id,)),
            ("UPDATE query_execution_contexts SET binding_revision=99 WHERE query_id=?", (self.query_id,)),
            ("UPDATE query_execution_contexts SET selected_role='data_engineer' WHERE query_id=?", (self.query_id,)),
            ("UPDATE query_execution_contexts SET sql_catalog='foreign' WHERE query_id=?", (self.query_id,)),
            ("UPDATE query_execution_contexts SET sql_schema='marts' WHERE query_id=?", (self.query_id,)),
            ("UPDATE query_records SET warehouse_id='foreign',generation_id='foreign-generation' WHERE id=?", (self.query_id,)),
            ("UPDATE query_records SET database_id=?,schema_id=? WHERE id=?", (self.f.b.databases[1],
                self.f.b.schemas[self.f.b.databases[1]]['sandbox'], self.query_id)),
            ("UPDATE query_records SET schema_id=? WHERE id=?", (self.f.b.schemas[self.context.database_id]['marts'], self.query_id)),
            ("UPDATE query_records SET generation_id=? WHERE id=?", ('g-' + self.f.b.databases[1], self.query_id)),
            ("UPDATE query_execution_contexts SET backend_username='foreign' WHERE query_id=?", (self.query_id,)),
            ("UPDATE query_execution_contexts SET credential_snapshot_ref='foreign' WHERE query_id=?", (self.query_id,)),
            ("UPDATE dataops_operations SET credential_snapshot_ref='foreign' WHERE id=?", (self.original['operations'][0]['id'],)),
            ("UPDATE query_records SET actor='foreign' WHERE id=?", (self.query_id,)),
            ("UPDATE dataops_queries SET action='wap_publish' WHERE query_id=?", (self.query_id,)),
            ("UPDATE dataops_queries SET commit_result='{}' WHERE query_id=?", (self.query_id,)),
            ("UPDATE dbt_run_operations SET phase='publish' WHERE run_id=?", (self.ident,)),
            ("UPDATE dataops_operations SET committed=1 WHERE id=?", (self.original['operations'][0]['id'],)),
            ("UPDATE dataops_operations SET result='{}' WHERE id=?", (self.original['operations'][0]['id'],)),
            ("UPDATE dataops_operations SET result=? WHERE id=?", (ds._json(dict(audit=dict(count=1, passed=True))),
                self.original['operations'][0]['id'])),
            ("UPDATE dbt_runs SET record=json_set(record,'$.kind','plan') WHERE id=?", (self.source_run['id'],)),
            ("UPDATE dbt_runs SET record=json_set(record,'$.actor','foreign') WHERE id=?", (self.source_run['plan_id'],)),
            ("UPDATE dbt_runs SET record=json_set(record,'$.context.sql_schema','foreign') WHERE id=?", (self.source_run['plan_id'],)),
        ]
        for sql, args in changes:
            with self.subTest(sql=sql), control_store.connect() as db:
                db.execute('SAVEPOINT mutation')
                db.execute(sql, args)
                with self.assertRaises((QueryError, KeyError, StopIteration)):
                    self.operator.graph(db, self.ident)
                db.execute('ROLLBACK TO mutation')
        self.physical.stop.assert_not_called()

    def test_foreign_authority_and_unrelated_unresolved_rows_are_rejected(self):
        for principal, role in ((self.principal, 'data_engineer'), (self.f.principal, 'data_engineer')):
            with patch.object(catalog_admin, 'authenticate_headers', return_value=(principal, role)):
                with self.assertRaises((QueryError, PermissionError)):
                    self.plan()
        account_reader = execution_recovery.request_auth._principal_accounts
        accounts = deepcopy(account_reader())
        accounts[self.principal.principal_id]['platform_admin'] = False
        with patch.object(execution_recovery.request_auth, '_principal_accounts', return_value=accounts):
            with self.assertRaises(QueryError):
                self.plan()
        # Orphan DBT work is disallowed even if its admission link is absent.
        with control_store.connect() as db:
            db.execute('SAVEPOINT orphan')
            row = self.original['publication_row']
            record = dict(self.original['publication'], id='orphan-publication')
            db.execute('INSERT INTO dbt_runs VALUES (?,?,?,NULL)',
                ('orphan-publication', row['database_id'], ds._json(record)))
            with self.assertRaises(QueryError):
                self.operator.graph(db, self.ident)
            db.execute('ROLLBACK TO orphan')
            for kind in ('query', 'dataops'):
                db.execute('SAVEPOINT extra')
                a = dict(self.original['admissions'][2 if kind == 'query' else 1],
                    id='extra-admission', subject_id='unlinked-extra', parent_id=self.original['admissions'][0]['id'], kind=kind)
                db.execute('INSERT INTO warehouse_admissions (' + ','.join(a) + ') VALUES (' +
                    ','.join('?' for _ in a) + ')', tuple(a.values()))
                with self.assertRaises(QueryError):
                    self.operator.graph(db, self.ident)
                db.execute('ROLLBACK TO extra')
            for table, original, key in (('query_records', self.original['queries'][0], 'id'),
                    ('dataops_operations', self.original['operations'][0], 'id')):
                db.execute('SAVEPOINT orphan')
                saved = dict(original, **{key: 'orphan-' + table})
                if table == 'dataops_operations':
                    saved['idempotency_key'] = 'orphan-key'
                db.execute('INSERT INTO ' + table + ' (' + ','.join(saved) + ') VALUES (' +
                    ','.join('?' for _ in saved) + ')', tuple(saved.values()))
                with self.assertRaises(QueryError):
                    self.operator.graph(db, self.ident)
                db.execute('ROLLBACK TO orphan')
            db.execute('SAVEPOINT orphan_context')
            q = dict(self.original['queries'][0], id='orphan-context-query', state='FINISHED')
            e = dict(self.original['executions'][0], query_id=q['id'])
            for table, saved in (('query_records', q), ('query_execution_contexts', e)):
                db.execute('INSERT INTO ' + table + ' (' + ','.join(saved) + ') VALUES (' +
                    ','.join('?' for _ in saved) + ')', tuple(saved.values()))
            with self.assertRaises(QueryError):
                self.operator.graph(db, self.ident)
            db.execute('ROLLBACK TO orphan_context')
            db.execute('SAVEPOINT owned')
            ref = self.original['publication']['_native_intent']['candidate']['ref']
            db.execute('INSERT INTO dataops_owned_objects VALUES (?,?,?,?)',
                (self.original['operations'][0]['id'], ref['object_id'], ref['incarnation'], 'stage'))
            with self.assertRaises(QueryError):
                self.operator.graph(db, self.ident)
            db.execute('ROLLBACK TO owned')
            db.execute('SAVEPOINT publish_child')
            saved = dict(self.original['operations'][0], id='additional-publish',
                route='dbt-native-publish', idempotency_key=self.ident + ':publish', result=None)
            db.execute('INSERT INTO dataops_operations (' + ','.join(saved) + ') VALUES (' +
                ','.join('?' for _ in saved) + ')', tuple(saved.values()))
            db.execute('INSERT INTO dbt_run_operations VALUES (?,?,?,?)',
                (self.ident, 'publish', self.original['publication']['node_id'], saved['id']))
            with self.assertRaises(QueryError):
                self.operator.graph(db, self.ident)
            db.execute('ROLLBACK TO publish_child')
        self.physical.stop.assert_not_called()

    def test_graph_mutation_before_and_after_stop_prevents_closure(self):
        plan = self.plan()
        original_sql = self.original['executions'][0]['sql']
        self.mutate("UPDATE query_execution_contexts SET sql='SELECT 1' WHERE query_id=?", (self.query_id,))
        with self.assertRaises(QueryError):
            self.apply(plan)
        self.physical.stop.assert_not_called()
        self.mutate('UPDATE query_execution_contexts SET sql=? WHERE query_id=?', (original_sql, self.query_id))
        def changed(scope):
            self.physical.stopped = True
            self.mutate("UPDATE query_execution_contexts SET sql='SELECT 1' WHERE query_id=?", (self.query_id,))
        self.physical.stop.side_effect = changed
        with self.assertRaises(QueryError):
            self.apply(plan)
        self.assertEqual(self.f.record(self.ident)['state'], 'UNKNOWN')
        with control_store.connect() as db:
            self.assertTrue(admission.blockers(db))

    def test_missing_membership_or_positive_stop_proof_never_closes(self):
        plan = self.plan()
        self.physical.membership = False
        with self.assertRaises(QueryError):
            self.apply(plan)
        self.physical.stop.assert_not_called()
        self.physical.membership = True
        self.physical.stop.side_effect = lambda scope: None
        pending = self.operator.inspect(plan['operation_id'])
        with self.assertRaises(QueryError):
            self.apply(pending)
        self.assertEqual(self.operator.inspect(plan['operation_id'])['phase'], 'STOP_INTENT')
        self.assertNotIn(recovery.MARKER, self.f.record(self.ident))

    def test_parent_owner_lock_excludes_observer(self):
        plan = self.plan()
        with queries._acquire_query_owner('dbt-' + self.ident):
            with self.assertRaises(QueryError):
                self.apply(plan)
        self.physical.stop.assert_not_called()

    def test_observer_rereads_closure_after_acquiring_parent_owner(self):
        plan = self.plan()
        acquire = queries._acquire_query_owner
        recovered = False
        def recover_before_owner(subject):
            nonlocal recovered
            if subject == 'dbt-' + self.ident and not recovered:
                recovered = True  # Recovery itself acquires this same owner.
                self.assertEqual(self.apply(plan)['phase'], 'OPEN')
            return acquire(subject)
        with patch.object(queries, '_acquire_query_owner', side_effect=recover_before_owner), \
                patch.object(dbt, '_settle', side_effect=AssertionError('stale observer settlement')) as settle, \
                patch.object(queries, '_transport', side_effect=AssertionError('SQL')):
            public = dbt.get_publication(self.principal, self.context, self.ident)
            settle.assert_not_called()
        self.assertTrue(recovered)
        self.assertEqual(public['state'], 'UNKNOWN')
        self.assertEqual(public['execution_closure'], 'FENCED_READ_ONLY')
        self.assertEqual(public['execution_recovery'], dict(operation_id=plan['operation_id'],
            phase='OPEN', query_outcome='UNKNOWN'))
        self.physical.stop.assert_called_once()
        with control_store.connect() as db:
            after = recovery._publication_snapshot(db, self.ident)
            self.assertEqual(recovery.verified_closure(db, after['publication']), public['execution_recovery'])
            self.assertEqual(after['queries'], self.original['queries'])
            self.assertEqual(after['executions'], self.original['executions'])
            self.assertEqual([a['state'] for a in after['admissions']], ['FAILED', 'FAILED', 'UNKNOWN'])
            self.assertEqual(db.execute('SELECT phase FROM execution_recovery_requests WHERE operation_id=?',
                (plan['operation_id'],)).fetchone()[0], 'OPEN')

    def test_closed_and_open_graph_drift_and_corrupt_receipt_are_rejected(self):
        plan = self.plan()
        with patch.object(self.physical, 'healthy', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.apply(plan)
        closed = self.operator.inspect(plan['operation_id'])
        marker = self.f.record(self.ident)['_execution_recovery']
        self.f.mutate(self.ident, lambda p: p.update(error_code='changed'))
        with self.assertRaises(QueryError):
            self.apply(closed)
        self.mutate('UPDATE dbt_runs SET record=? WHERE id=?',
            (ds._json(dict(self.original['publication'], _execution_recovery=marker)), self.ident))
        opened = self.apply(closed)
        self.mutate("UPDATE execution_recovery_requests SET proof='{}' WHERE operation_id=?", (plan['operation_id'],))
        with self.assertRaises(QueryError):
            dbt.get_publication(self.principal, self.context, self.ident)
        self.f.mutate(self.ident, lambda p: p.update(committed=True))
        with self.assertRaises(QueryError):
            self.apply(opened)
        self.physical.stop.assert_called_once()

    def test_marker_seal_rejects_tampered_durable_receipts_without_settlement(self):
        plan = self.plan()
        self.apply(plan)
        with control_store.connect() as db, \
                patch.object(dbt, '_settle', side_effect=AssertionError('settlement')), \
                patch.object(queries, '_transport', side_effect=AssertionError('SQL')):
            # Deliberately simulate corruption beyond normal immutable-column
            # triggers, to prove the independently saved publication seal.
            db.execute('DROP TRIGGER recovery_immutable')
            receipt = dict(db.execute('SELECT * FROM execution_recovery_requests WHERE operation_id=?',
                (plan['operation_id'],)).fetchone())
            changed_intent = json.loads(receipt['intent'])
            changed_intent['scope']['members'][0]['container_id'] = 'f' * 64
            mutations = [
                ('execution_recovery_requests', 'intent', execution_recovery._json(changed_intent)),
                ('execution_recovery_requests', 'payload', ds._json(dict(publication_id='foreign'))),
                ('execution_recovery_requests', 'proof', '{}'),
                ('execution_recovery_requests', 'route', 'recover-query'),
                ('execution_recovery_requests', 'phase', 'PROVED'),
                ('operations', 'action', 'QUERY_EXECUTION_RECOVERY'),
                ('operations', 'state', 'PLANNED'),
                ('operations', 'query_ids', '["foreign"]'),
            ]
            for table, field, value in mutations:
                with self.subTest(table=table, field=field):
                    db.execute('SAVEPOINT corrupt')
                    key = 'operation_id' if table == 'execution_recovery_requests' else 'id'
                    db.execute('UPDATE ' + table + ' SET ' + field + '=? WHERE ' + key + '=?',
                        (value, plan['operation_id']))
                    with self.assertRaises(QueryError):
                        recovery.verified_closure(db, dbt._row(db, self.ident)[1])
                    db.execute('ROLLBACK TO corrupt')
            record = dbt._row(db, self.ident)[1]
            for field in ('intent_digest', 'proof_digest'):
                marker = dict(record['_execution_recovery'], **{field: '0' * 64})
                with self.assertRaises(QueryError):
                    recovery.verified_closure(db, dict(record, _execution_recovery=marker))


class ViewGraphRecoveryTests(DbtRecoveryTests):
    fixture_type = view_fixtures.ViewPublicationTests

    # Exercise graph pins, native build/test receipts and frozen VIEW evidence
    # through the same recovery; avoid duplicating every generic rejection.
    def test_frozen_view_and_source_evidence_cannot_drift(self):
        plan = self.plan()
        self.f.mutate(self.source_run['id'], lambda r: r['models'][1]['native_receipt']['candidate'].update(version='changed'))
        with self.assertRaises(QueryError):
            self.apply(plan)
        self.physical.stop.assert_not_called()

    def test_closed_native_view_build_proof_remains_immutable(self):
        self.apply(self.plan())
        def reservation(record, value):
            next(m for m in record['models'] if m['unique_id'] == self.f.uid)['publication_id'] = value
        self.f.mutate(self.source_run['id'], lambda r: reservation(r, 'different-publication'))
        with self.assertRaises(QueryError):
            dbt.get_publication(self.principal, self.context, self.ident)
        self.f.mutate(self.source_run['id'], lambda r: reservation(r, self.ident))
        view = next(m for m in self.f.record(self.source_run['id'])['models'] if m['materialized'] == 'view')
        self.mutate("UPDATE dataops_operations SET result='{}' WHERE id=?", (view['operation_ids'][0],))
        with patch.object(dbt, '_settle', side_effect=AssertionError('settlement')), \
                patch.object(queries, '_transport', side_effect=AssertionError('SQL')):
            with self.assertRaises(QueryError):
                dbt.get_publication(self.principal, self.context, self.ident)

    def test_historical_closure_survives_source_cancel_and_other_model_publication(self):
        opened = self.apply(self.plan())
        public = dbt.get_publication(self.principal, self.context, self.ident)
        self.assertNotIn('execution_recovery', self.source_run)
        selected = self.f.uid
        self.f.uid = 'model.general_native.extra_result'
        other = self.f.accept(self.source_run, self.f.preview(self.source_run)['expected_target'], key='other-table')
        self.f.uid = selected
        self.assertEqual(other['state'], 'QUEUED')
        self.f.mutate(self.source_run['id'], lambda r: r.update(cancel_requested=True, state='CANCELED',
            native_verified=False, publication_allowed=False))
        with patch.object(queries, '_transport', side_effect=AssertionError('SQL')):
            dbt.reconcile_run(self.principal, self.context, self.source_run['id'], key='source-read-only')
        with patch.object(queries, '_transport', side_effect=AssertionError('SQL')), \
                patch.object(database_inventory, 'catalog_api', side_effect=AssertionError('Catalog')), \
                patch.object(database_dbt_graph, '_run_subprocess', side_effect=AssertionError('SDK')), \
                patch.object(dbt, '_native_publisher', side_effect=AssertionError('source authority')):
            self.assertEqual(dbt.get_publication(self.principal, self.context, self.ident), public)
            self.assertIn(public, dbt.list_publications(self.principal, self.context))
            self.assertEqual(self.f.accept(self.source_run, self.expected), public)
        # Current VIEW identity/head and source authority can retire while the
        # saved native proof and original publisher's receipt remain historical.
        view = next(m for m in self.f.record(self.source_run['id'])['models'] if m['materialized'] == 'view')
        with control_store.connect() as db:
            db.execute('UPDATE object_bindings SET current=0 WHERE id=?', (view['candidate']['ref']['object_id'],))
            db.execute('DELETE FROM grants WHERE principal_id=? AND database_id=?',
                (self.f.principal.principal_id, self.context.database_id))
            db.execute('UPDATE catalog_bindings SET desired_revision=desired_revision+1,applied_revision=applied_revision+1 '
                'WHERE database_id=?', (self.context.database_id,))
        current_context = replace(self.context, binding_revision=self.context.binding_revision + 1)
        for key in list(self.f.b.objects):
            if key[3] == 'views':
                del self.f.b.objects[key]
        with patch.object(queries, '_transport', side_effect=AssertionError('SQL')), \
                patch.object(database_inventory, 'catalog_api', side_effect=AssertionError('Catalog')) , \
                patch.object(database_dbt_graph, '_run_subprocess', side_effect=AssertionError('SDK')), \
                patch.object(dbt, '_native_publisher', side_effect=AssertionError('source authority')):
            self.assertEqual(dbt.get_publication(self.principal, current_context, self.ident), public)
            self.assertIn(public, dbt.list_publications(self.principal, current_context))
            # Replay checks its original payload/context; verify historical
            # closure directly after the permitted binding revision advances.
            with control_store.connect() as db:
                self.assertEqual(recovery.verified_closure(db, self.f.record(self.ident)),
                    dict(operation_id=opened['operation_id'], phase='OPEN', query_outcome='UNKNOWN'))
                db.execute("UPDATE schemas SET sql_name='SANDBOX' WHERE id=?", (self.context.schema_id,))
                self.assertEqual(recovery.verified_closure(db, self.f.record(self.ident))['phase'], 'OPEN')


class UppercaseSchemaTests(unittest.TestCase):
    fixture_type = graph_fixtures.GraphPublicationTests
    setUp = DbtRecoveryTests.setUp

    def test_registered_uppercase_sandbox_uses_identity_and_purpose(self):
        # Recase only the publisher's canonical pending request/COUNT context;
        # retain genuine original source SDK build/test proof without mocking
        # _native_frozen_graph or passing the preexisting public-name guards.
        with control_store.connect() as db:
            p = dbt._row(db, self.ident)[1]
            p['context']['sql_schema'] = 'SANDBOX'
            p['_native_intent']['context']['sql_schema'] = 'SANDBOX'
            db.execute("UPDATE schemas SET sql_name='SANDBOX' WHERE id=?", (self.context.schema_id,))
            db.execute('UPDATE dbt_runs SET record=? WHERE id=?', (ds._json(p), self.ident))
            op = self.original['operations'][0]
            payload = ds._json(dict(context=p['context'], payload=dbt._native_child_payload(p, 'audit')))
            pending = json.loads(op['result'])
            pending['intent'] = p['_native_intent']
            db.execute('UPDATE dataops_operations SET context=?,payload=?,fingerprint=?,result=? WHERE id=?',
                (ds._json(p['context']), payload, hashlib.sha256(payload.encode()).hexdigest(), ds._json(pending), op['id']))
            sql = self.original['executions'][0]['sql'].replace('"sandbox".', '"SANDBOX".')
            db.execute("UPDATE query_execution_contexts SET sql_schema='SANDBOX',sql=? WHERE query_id=?", (sql, self.query_id))
            request = self.original['requests'][0]
            body = json.loads(request['payload'])
            body['context'] = p['context']
            db.execute('UPDATE dbt_requests SET payload=? WHERE actor=? AND route=? AND idempotency_key=?',
                (ds._json(body), request['actor'], request['route'], request['idempotency_key']))
        queries._pins.pop(self.query_id, None)
        plan = self.operator.plan(self.ident, 'uppercase-schema')
        result = self.operator.apply(plan['operation_id'], plan['revision'],
            expected_intent_digest=plan['intent_digest'])
        self.assertEqual(result['phase'], 'OPEN')
        with control_store.connect() as db:
            self.assertEqual(recovery.verified_closure(db, self.f.record(self.ident))['query_outcome'], 'UNKNOWN')


class CliTests(unittest.TestCase):
    def test_private_cli_shapes(self):
        for command, flags in [('plan', ['--publication-id', 'publication', '--idempotency-key', 'key']),
            ('inspect', ['--operation-id', 'recovery']),
            ('apply', ['--operation-id', 'recovery', '--expected-revision', '1', '--expected-intent-digest', 'digest']),
            ('reconcile', ['--operation-id', 'recovery', '--expected-revision', '2', '--expected-intent-digest', 'digest'])]:
            with patch.object(recovery, 'run', return_value={}) as run, patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(catalog_admin.main(['recover-dbt-audit-' + command, '--config', '/private/config'] + flags), 0)
                self.assertEqual(run.call_args.args[0].config, Path('/private/config'))
