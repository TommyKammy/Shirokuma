"""Canonical failed-ingest SQLite graphs; physical lifecycle IO is simulated."""
import io
import json
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import catalog_admin
import control_store
import dataops_service as ds
import ingest_execution_recovery as recovery
import logical_execution_recovery as logical
import query_service as queries
from query_context import QueryError
import warehouse_admission as admission
import test_manual_ingest as fixtures
import test_dbt_execution_recovery as physical_fixtures


class IngestRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ManualIngestTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.b = self.f.fixture
        self.principal = self.b.fixture.principal('owner')
        with control_store.connect() as db:
            db.execute("UPDATE warehouse_config_generations SET warehouse_id='LOCAL'")
        self.context = queries.resolve_context(self.principal, self.b.databases[0],
            self.b.schemas[self.b.databases[0]]['raw'], 'admin', warehouse_id='LOCAL', expected_binding_revision=1)
        self.f.principal = self.principal
        self.b.context = lambda **kwargs: self.context
        self.loads = 0
        def fault(sql):
            if sql.startswith('INSERT INTO'):
                self.loads += 1
                if self.loads == 2:
                    return dict(id='load_failed', error=dict(message='failed load'))
            if sql.startswith('DROP TABLE'):
                raise OSError('old cleanup response lost')
        self.f.fault = fault
        # Historical cleanup used SQL DROP. Construct it through the real
        # statement service without changing the new nonpurge cleanup route.
        def historical_cleanup(execution, ref):
            try:
                execution.cleanup(ref, step='failure-cleanup')
            finally:
                with control_store.connect() as db:
                    receipt = json.loads(db.execute('SELECT result FROM dataops_operations WHERE id=?', (execution.id,)).fetchone()[0])
                    receipt['cleanup'] = 'unknown'
                    db.execute('UPDATE dataops_operations SET result=? WHERE id=?', (ds.canonical_json(receipt), execution.id))
        with patch.object(ds.Execution, 'cleanup_failed_ingest_stage', historical_cleanup):
            mode = getattr(self, 'mode', 'create')
            result = self.f.run_ingest(context=self.context, mode=mode, batch_size=1,
                                      atomic_append=True, key_columns=['id'])
        self.ident = result['id']
        self.assertEqual(result['state'], 'UNKNOWN')
        for live in ds._live.values():
            live[2].close()
        with control_store.connect() as db:
            db.execute("UPDATE warehouse_config_generations SET state='APPLIED' WHERE id=?", (self.context.generation_id,))
            db.execute("UPDATE warehouse_controls SET applied_generation_id=? WHERE warehouse_id='LOCAL'", (self.context.generation_id,))
            self.original = recovery._snapshot(db, self.ident)
        self.query_id = self.original['query_ids'][0]
        for target, name, value in ((catalog_admin, 'authenticate_headers', (self.principal, 'admin')),
            (logical.logical_creations, 'compute_blockers', {}), (logical.warehouse_lifecycle, 'runtime_blockers', [])):
            p = patch.object(target, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        self.operator = recovery.IngestRecovery.__new__(recovery.IngestRecovery)
        self.operator.actor = self.principal.principal_id
        self.operator.config = dict(auth_headers='/private/headers', runtime_profile='/private/profile')
        self.operator.seal = dict(version=1)
        self.physical = physical_fixtures.Physical()
        self.operator.adapter = Mock(return_value=self.physical)

    def plan(self, key='abandon-ingest'):
        return self.operator.plan(self.ident, key)

    def apply(self, plan, **kwargs):
        return self.operator.apply(plan['operation_id'], plan['revision'], expected_intent_digest=plan['intent_digest'], **kwargs)

    def mutate(self, sql, args=()):
        with control_store.connect() as db:
            db.execute(sql, args)

    def test_closure_preserves_history_claim_and_results(self):
        plan = self.plan()
        self.assertEqual(self.plan(), plan)
        with patch.object(queries, '_transport', side_effect=AssertionError('original SQL replay')):
            closed = self.apply(plan)
            self.assertEqual(self.apply(closed), closed)
        self.assertEqual(closed['phase'], 'OPEN')
        self.assertEqual(closed['execution_closure'], recovery.CLOSURE)
        self.physical.stop.assert_called_once()
        with control_store.connect() as db:
            after = recovery._snapshot(db, self.ident)
            self.operator._closed_graph(db, closed['operation_id'], self.original)
            for key in ('queries', 'executions', 'query_links', 'owned_objects', 'stage_objects', 'claims', 'binding'):
                self.assertEqual(after[key], self.original[key])
            self.assertEqual(after['operation']['state'], 'FAILED')
            for key in ('result', 'committed'):
                self.assertEqual(after['operation'][key], self.original['operation'][key])
            self.assertEqual(json.loads(after['operation']['cleanup_error'])['code'], 'INGEST_EXECUTION_ABANDONED')
            self.assertFalse(admission.blockers(db))
            self.assertEqual(next(q for q in after['queries'] if q['id'] == self.query_id)['state'], 'UNKNOWN')
            projection = db.execute('SELECT before_after FROM operations WHERE id=?', (closed['operation_id'],)).fetchone()[0]
            self.assertEqual(json.loads(projection), [['execution_closure', recovery.CLOSURE]])
        next_op = ds.begin(self.principal, self.context, route='manual-ingest', idempotency_key='next', payload={})
        self.assertEqual(next_op.ingest_claim(self.b.locator('fresh')), self.b.locator('fresh'))
        next_op.fail()

    def test_digest_revision_and_idempotency(self):
        plan = self.plan()
        for revision, digest, code in ((plan['revision'], None, 'RECOVERY_INTENT_MISMATCH'),
                (999, plan['intent_digest'], 'REVISION_CONFLICT'),
                (plan['revision'], '0' * 64, 'RECOVERY_INTENT_MISMATCH')):
            with self.subTest(code=code), self.assertRaises(QueryError) as error:
                self.operator.apply(plan['operation_id'], revision, expected_intent_digest=digest)
            self.assertEqual(error.exception.code, code)
        with self.assertRaises(QueryError) as error:
            self.operator.plan('00000000-0000-0000-0000-000000000000', 'abandon-ingest')
        self.assertEqual(error.exception.code, 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(self.apply(plan)['phase'], 'OPEN')

    def test_ineligible_graph_shapes(self):
        cases = [
            ("UPDATE dataops_queries SET step='publish' WHERE query_id=?", (self.query_id,)),
            ("UPDATE dataops_queries SET action='ingest_commit' WHERE query_id=?", (self.query_id,)),
            ("UPDATE dataops_queries SET commit_result='{}' WHERE query_id=?", (self.query_id,)),
            ("UPDATE query_execution_contexts SET sql='DROP TABLE other' WHERE query_id=?", (self.query_id,)),
            ("UPDATE query_execution_contexts SET credential_snapshot_ref='foreign' WHERE query_id=?", (self.query_id,)),
            ("UPDATE query_execution_contexts SET request_id='unlinked' WHERE query_id=?", (self.query_id,)),
            ("UPDATE query_records SET actor='foreign' WHERE id=?", (self.query_id,)),
            ("UPDATE dataops_owned_objects SET purpose='backup' WHERE operation_id=?", (self.ident,)),
            ("UPDATE object_bindings SET backend_identity='malformed' WHERE id=?", (self.original['stage_objects'][0]['id'],)),
            ("UPDATE dataops_target_claims SET table_name='foreign' WHERE operation_id=?", (self.ident,)),
            ("UPDATE dataops_operations SET result='[]' WHERE id=?", (self.ident,)),
            ("UPDATE dataops_operations SET fingerprint='bad' WHERE id=?", (self.ident,)),
        ]
        for sql, args in cases:
            with self.subTest(sql=sql), control_store.connect() as db:
                db.execute('SAVEPOINT mutation')
                db.execute(sql, args)
                with self.assertRaises(QueryError) as error:
                    self.operator.graph(db, self.ident)
                self.assertEqual(error.exception.code, 'RECOVERY_INELIGIBLE')
                db.execute('ROLLBACK TO mutation')

    def test_graph_cas_before_stop(self):
        plan = self.plan()
        self.mutate("UPDATE dataops_operations SET cleanup_error='changed' WHERE id=?", (self.ident,))
        with self.assertRaises(QueryError) as error:
            self.apply(plan)
        self.assertEqual(error.exception.code, 'RECOVERY_GRAPH_CHANGED')
        self.physical.stop.assert_not_called()

    def test_extra_admission_and_unlinked_execution_rejected(self):
        with control_store.connect() as db:
            for kind in ('admission', 'execution'):
                with self.subTest(kind=kind):
                    db.execute('SAVEPOINT mutation')
                    if kind == 'admission':
                        row = dict(self.original['admissions'][0], id=str(uuid.uuid4()),
                                   subject_id='extra', parent_id=self.original['parent_id'])
                        table = 'warehouse_admissions'
                    else:
                        q = dict(self.original['queries'][0], id=str(uuid.uuid4()))
                        db.execute('INSERT INTO query_records (' + ','.join(q) + ') VALUES (' +
                                   ','.join('?' for _ in q) + ')', tuple(q.values()))
                        row = dict(self.original['executions'][0], query_id=q['id'])
                        table = 'query_execution_contexts'
                    db.execute('INSERT INTO ' + table + ' (' + ','.join(row) + ') VALUES (' +
                               ','.join('?' for _ in row) + ')', tuple(row.values()))
                    with self.assertRaises(QueryError) as error:
                        self.operator.graph(db, self.ident)
                    self.assertEqual(error.exception.code, 'RECOVERY_INELIGIBLE')
                    db.execute('ROLLBACK TO mutation')

    def test_terminal_orphan_and_exact_known_closures(self):
        with control_store.connect() as db:
            q = dict(self.original['queries'][0], id=str(uuid.uuid4()), state='FINISHED')
            db.execute('INSERT INTO query_records (' + ','.join(q) + ') VALUES (' +
                       ','.join('?' for _ in q) + ')', tuple(q.values()))
            self.operator.graph(db, self.ident)
            db.execute("UPDATE query_records SET state='UNKNOWN' WHERE id=?", (q['id'],))
            with self.assertRaises(QueryError) as error:
                self.operator.graph(db, self.ident)
            self.assertEqual(error.exception.code, 'UNRELATED_UNRESOLVED_WORK')
            a = dict(next(a for a in self.original['admissions'] if a['kind'] == 'query'),
                     id=str(uuid.uuid4()), subject_id=q['id'], parent_id=None, state='UNKNOWN',
                     evidence_ref='prior-recovery', execution_closure='FENCED_STAGE_CLEANUP')
            db.execute('INSERT INTO warehouse_admissions (' + ','.join(a) + ') VALUES (' +
                       ','.join('?' for _ in a) + ')', tuple(a.values()))
            for closure in ('FENCED_READ_ONLY', 'FENCED_STAGE_CLEANUP'):
                db.execute('UPDATE warehouse_admissions SET execution_closure=? WHERE id=?', (closure, a['id']))
                self.operator.graph(db, self.ident)
            self.assertTrue(admission.execution_unresolved(dict(a, execution_closure='arbitrary')))

    def test_actor_route_and_current_binding_isolation(self):
        plan = self.plan()
        self.operator.actor = 'foreign'
        with self.assertRaises(QueryError):
            self.operator.inspect(plan['operation_id'])
        self.operator.actor = self.principal.principal_id
        self.operator.route = 'recover-query'
        with self.assertRaises(QueryError) as error:
            self.apply(plan)
        self.assertEqual(error.exception.code, 'RECOVERY_SUBJECT_MISMATCH')

    def test_claim_and_result_cas_after_close(self):
        closed = self.apply(self.plan())
        for table, column, value in (('dataops_operations', 'result', '{}'),
                                     ('dataops_target_claims', 'table_name', 'changed')):
            with self.subTest(table=table), control_store.connect() as db:
                db.execute('SAVEPOINT mutation')
                key = 'id' if table == 'dataops_operations' else 'operation_id'
                db.execute('UPDATE ' + table + ' SET ' + column + '=? WHERE ' + key + '=?', (value, self.ident))
                with self.assertRaises(QueryError) as error:
                    self.operator._closed_graph(db, closed['operation_id'], self.original)
                self.assertEqual(error.exception.code, 'RECOVERY_GRAPH_CHANGED')
                db.execute('ROLLBACK TO mutation')

    def test_membership_or_missing_proof_cannot_close(self):
        plan = self.plan()
        self.physical.membership = False
        with self.assertRaises(QueryError):
            self.apply(plan)
        self.physical.stop.assert_not_called()
        current = self.operator.inspect(plan['operation_id'])
        self.assertEqual(current['phase'], 'STOP_INTENT')
        self.physical.membership = True
        with patch.object(self.physical, 'proof', return_value=None), self.assertRaises(QueryError):
            self.apply(current)
        with control_store.connect() as db:
            self.assertEqual(recovery._snapshot(db, self.ident), self.original)
        self.assertEqual(self.apply(self.operator.inspect(plan['operation_id']))['phase'], 'OPEN')

    def test_reconcile_keeps_drain_until_explicit_health_apply(self):
        plan = self.plan()
        self.assertEqual(self.apply(plan, reconcile=True), plan)
        self.physical.healthy.side_effect = OSError('replacement unavailable')
        with self.assertRaises(OSError):
            self.apply(plan)
        closed = self.operator.inspect(plan['operation_id'])
        self.assertEqual(closed['phase'], 'CLOSED')
        self.assertEqual(self.apply(closed, reconcile=True), closed)
        self.physical.healthy.side_effect = None
        self.assertEqual(self.apply(closed)['phase'], 'OPEN')

    def test_unlinked_execution_after_close_cannot_reopen(self):
        plan = self.plan()
        self.physical.healthy.side_effect = OSError('replacement unavailable')
        with self.assertRaises(OSError):
            self.apply(plan)
        closed = self.operator.inspect(plan['operation_id'])
        self.assertEqual(closed['phase'], 'CLOSED')
        with control_store.connect() as db:
            q = dict(self.original['queries'][0], id=str(uuid.uuid4()), state='FINISHED')
            db.execute('INSERT INTO query_records (' + ','.join(q) + ') VALUES (' +
                       ','.join('?' for _ in q) + ')', tuple(q.values()))
            execution = dict(self.original['executions'][0], query_id=q['id'], request_id=self.ident)
            db.execute('INSERT INTO query_execution_contexts (' + ','.join(execution) + ') VALUES (' +
                       ','.join('?' for _ in execution) + ')', tuple(execution.values()))
            self.assertFalse(db.execute('SELECT 1 FROM dataops_queries WHERE query_id=?', (q['id'],)).fetchone())
        self.physical.healthy.side_effect = None
        self.physical.healthy.reset_mock()
        for reconcile in (False, True):
            with self.subTest(reconcile=reconcile), self.assertRaises(QueryError) as error:
                self.apply(closed, reconcile=reconcile)
            self.assertIn(error.exception.code, ('RECOVERY_GRAPH_CHANGED', 'RECOVERY_INELIGIBLE'))
            with control_store.connect() as db:
                self.assertEqual(admission.control(db)['state'], 'DRAINING')
            self.assertEqual(self.operator.inspect(plan['operation_id'])['phase'], 'CLOSED')
        self.physical.healthy.assert_not_called()

    def test_private_inspection_exposes_only_scope_and_proof_evidence(self):
        plan = self.plan()
        self.assertEqual(self.operator.inspect(plan['operation_id']), plan)
        self.assertIsNone(plan['proof'])
        self.physical.healthy.side_effect = OSError('replacement unavailable')
        with self.assertRaises(OSError):
            self.apply(plan)
        proved = self.operator.inspect(plan['operation_id'])
        self.assertEqual(proved['phase'], 'CLOSED')
        self.assertIsNotNone(proved['proof'])
        with control_store.connect() as db:
            row = db.execute('SELECT intent,proof FROM execution_recovery_requests WHERE operation_id=?',
                             (plan['operation_id'],)).fetchone()
        scope = json.loads(row['intent'])['scope']
        self.assertEqual(plan['scope'], scope)
        self.assertEqual(proved['scope'], scope)
        self.assertEqual(proved['proof'], json.loads(row['proof']))
        for projection in (plan, proved):
            encoded = json.dumps(projection)
            self.assertFalse(set(projection) & {'sql', 'result', 'context', 'credential_snapshot_ref'})
            for execution in self.original['executions']:
                self.assertNotIn(execution['sql'], encoded)
                self.assertNotIn(execution['credential_snapshot_ref'], encoded)
            self.assertNotIn(self.original['operation']['context'], encoded)
            self.assertNotIn(self.original['operation']['result'], encoded)
            for private_path in self.operator.config.values():
                self.assertNotIn(private_path, encoded)

    def test_final_membership_and_graph_cas_before_closure(self):
        plan = self.plan()
        def lose_membership(scope):
            self.physical._remove(scope)
            self.physical.membership = False
        self.physical.remove.side_effect = lose_membership
        with self.assertRaises(QueryError):
            self.apply(plan)
        with control_store.connect() as db:
            self.assertEqual(recovery._snapshot(db, self.ident), self.original)
        self.physical.membership = True
        receipt = json.loads(self.original['operation']['result'])
        receipt['error'] = 'concurrent bookkeeping'
        self.mutate('UPDATE dataops_operations SET result=? WHERE id=?', (ds.canonical_json(receipt), self.ident))
        with self.assertRaises(QueryError) as error:
            self.apply(self.operator.inspect(plan['operation_id']))
        self.assertEqual(error.exception.code, 'RECOVERY_GRAPH_CHANGED')
        with control_store.connect() as db:
            self.assertTrue(admission.blockers(db))
            self.assertTrue(all(not a['execution_closure'] for a in recovery._snapshot(db, self.ident)['admissions']))

    def test_query_mutations_blocked_by_closure(self):
        self.apply(self.plan())
        observer = Mock(side_effect=AssertionError('backend observation'))
        with patch.object(queries, '_transport', side_effect=AssertionError('backend request')):
            for result in (queries.cancel(self.principal, self.query_id, selected_role='admin'),
                    queries.reconcile_query(self.principal, self.query_id, selected_role='admin', observer=observer)):
                self.assertEqual(result['state'], 'UNKNOWN')
                self.assertEqual(result['execution_closure'], recovery.CLOSURE)
            with self.assertRaises(QueryError) as error:
                queries.fetch(self.principal, self.query_id, 'old-page', selected_role='admin')
            self.assertEqual(error.exception.code, 'EXECUTION_CLOSED')
        observer.assert_not_called()

    def test_cli_and_generic_inspection(self):
        plan = self.plan()
        with patch.object(recovery, 'IngestRecovery', return_value=self.operator), patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(catalog_admin.main(['recover-ingest-inspect', '--config', '/private/config', '--operation-id', plan['operation_id']]), 0)
            self.assertEqual(json.loads(output.getvalue()), plan)
        public = catalog_admin.inspect_operation(self.principal, 'admin', plan['operation_id'])
        self.assertEqual(public['ingest_operation_id'], self.ident)
        self.assertEqual(public['intent_digest'], plan['intent_digest'])
        self.assertFalse(set(public) & {'sql', 'result', 'context', 'scope', 'proof', 'credential_snapshot_ref'})
        self.apply(plan)
        self.assertEqual(catalog_admin.inspect_operation(self.principal, 'admin', plan['operation_id'])['execution_closure'], recovery.CLOSURE)

    def test_cli_plan_apply_reconcile_arguments(self):
        with patch.object(recovery, 'IngestRecovery') as operator, patch('sys.stdout', new_callable=io.StringIO):
            operator.return_value.plan.return_value = {}
            operator.return_value.apply.return_value = {}
            self.assertEqual(catalog_admin.main(['recover-ingest-plan', '--config', '/private/config',
                '--ingest-operation-id', self.ident, '--idempotency-key', 'key']), 0)
            operator.return_value.plan.assert_called_once_with(self.ident, 'key')
            for command in ('apply', 'reconcile'):
                self.assertEqual(catalog_admin.main(['recover-ingest-' + command, '--config', '/private/config',
                    '--operation-id', self.ident, '--expected-revision', '2', '--expected-intent-digest', 'a' * 64]), 0)
                operator.return_value.apply.assert_called_with(self.ident, 2,
                    expected_intent_digest='a' * 64, reconcile=command == 'reconcile')


class AtomicModeTests(unittest.TestCase):
    def test_existing_target_atomic_modes(self):
        for mode in ('replace', 'append', 'upsert'):
            with self.subTest(mode=mode):
                fixture = IngestRecoveryTests()
                fixture.mode = mode
                try:
                    fixture.setUp()
                    plan = fixture.plan()
                    self.assertEqual(fixture.apply(plan)['phase'], 'OPEN')
                    with control_store.connect() as db:
                        after = recovery._snapshot(db, fixture.ident)
                        self.assertEqual(after['target_object'], fixture.original['target_object'])
                        self.assertEqual(after['operation']['result'], fixture.original['operation']['result'])
                finally:
                    fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
