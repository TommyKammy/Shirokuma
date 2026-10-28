"""Recovery contracts: real ledger/flocks, mocked Docker/Kubernetes only."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import copy
import io
import json
import os
import select
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import catalog_admin
import control_store
import data_management as dm
import dataops_service as ds
import db_jobs
import execution_recovery as recovery
import grant_management as grants
import query_service as qs
from query_context import QueryError
import warehouse_admission as admission
import test_db_jobs as fixtures


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DatabaseJobsTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.c = replace(self.f.c, warehouse_id='LOCAL')
        with control_store.connect() as db:
            db.execute("UPDATE warehouse_config_generations SET warehouse_id='LOCAL',state='APPLIED'")
        self.admin = self.f.f.fixture.principal('owner')
        auth = patch.object(catalog_admin, 'authenticate_headers', return_value=(self.admin, 'admin'))
        auth.start()
        self.addCleanup(auth.stop)
        self.operator = recovery.Recovery.__new__(recovery.Recovery)
        self.operator.actor = self.admin.principal_id
        self.operator.config = dict(auth_headers='/private/headers', runtime_profile='/private/profile')
        self.operator.seal = dict(version=1)
        self.backend = Mock()
        self.scope = dict(engine_id='engine-one', container_id='a' * 64,
                          binding_basis='current operator-established managed scope; Engine ID was not recorded at admission')
        self.backend.scope.return_value = self.scope
        self.backend.engine.return_value = dict(engine_id='engine-one')
        self.backend.old_present.return_value = False
        self.backend.proof.return_value = dict(kind='stopped-inspect', receipt={'Running': False})
        self.operator.adapter = Mock(return_value=self.backend)
        # A missing mock must fail before any external command can run.
        external = patch.object(subprocess, 'run', side_effect=AssertionError('unmocked subprocess'))
        external.start()
        self.addCleanup(external.stop)

    def unknown(self, *, steps=None, first_committed=False, accepted=False):
        definition = self.f.definition(steps=steps)
        job = self.f.save(definition)
        run = self.f.submit(job)
        calls = 0
        original = self.f.f.transport
        def transport(*args):
            nonlocal calls
            calls += 1
            if first_committed and calls == 1:
                return original(*args)
            if accepted and args[1] == 'POST':
                return dict(id='original_backend',
                    nextUri='http://127.0.0.1:1234/v1/statement/original_backend/1')
            raise OSError('lost response')
        with patch.object(qs, '_transport', side_effect=transport):
            self.f.finish(run)
        for live in ds._live.values():
            live[2].close()
        self.run_id = run['id']
        with control_store.connect() as db:
            db.execute('UPDATE warehouse_controls SET applied_generation_id=? WHERE warehouse_id=?',
                       (self.f.c.generation_id, 'LOCAL'))
            self.query_id = db.execute("SELECT id FROM query_records WHERE state='UNKNOWN'").fetchone()[0]
        return run

    def plan(self):
        return self.operator.plan(self.run_id, 'recover-once')

    def apply(self, plan, **kw):
        return self.operator.apply(plan['operation_id'], plan['revision'], **kw)

    def snapshot(self):
        with control_store.connect() as db:
            return (dict(db.execute('SELECT * FROM query_records WHERE id=?', (self.query_id,)).fetchone()),
                    admission.control(db),
                    dict(db.execute('SELECT * FROM db_job_runs WHERE id=?', (self.run_id,)).fetchone()))

    def assert_code(self, code, function):
        with self.assertRaises(QueryError) as caught:
            function()
        self.assertEqual(caught.exception.code, code)

    def terminal_children(self, state='CANCELED', *, prior=False, alert=False):
        steps = ([dict(name='committed', action='sql', mode='sandbox-write',
                       sql='CREATE TABLE sandbox.kept AS SELECT 1')] if prior else [])
        steps += [dict(name='lost', action='alert' if alert else 'sql', mode='read-only',
                       sql='SELECT true' if alert else 'SELECT 17'),
                  dict(name='never', action='sql', mode='read-only', sql='SELECT 999')]
        self.unknown(steps=steps, first_committed=prior, accepted=True)
        pin = qs._pins[self.query_id]
        with control_store.connect() as db:
            desired = dm.get_grant(db, self.f.p.principal_id, self.f.c.database_id)
        for offset, role in enumerate(('revoked', 'data_engineer')):
            grants.mutate(self.admin, 'admin', self.f.c.database_id,
                dict(principal_id=self.f.p.principal_id, expected_revision=desired.revision + offset,
                     maximum_role=role), 'terminal-owner-' + role)
        if state == 'CANCELED':
            def acknowledge(url, method, original_pin, body=None):
                self.assertIs(original_pin, pin)
                self.assertEqual((method, body, pin.headers['Authorization']),
                                 ('DELETE', None, 'Basic original-secret'))
                return {}
            with patch.object(qs, '_transport', side_effect=acknowledge) as transport:
                result = qs.cancel(self.f.p, self.query_id, selected_role='data_engineer')
                self.assertEqual(result['state'], state)
                transport.assert_called_once()
        else:
            def observe(expected):
                self.assertEqual(expected['backend_query_id'], 'original_backend')
                return [dict(id=expected['backend_query_id'], source=expected['source'],
                             user=expected['backend_username'], sql=expected['sql'], state=state)]
            result = qs.reconcile_query(self.admin, self.query_id, selected_role='admin', observer=observe)
            self.assertEqual(result['state'], state)
        with control_store.connect() as db:
            if prior:
                db.execute("UPDATE dataops_operations SET committed=1,result=? WHERE state='FINISHED'",
                           ('{"kept":17}',))
            frozen = recovery.graph(db, self.run_id)
            self.assertEqual(frozen['query_ids'], [])
            self.assertTrue(frozen['operation_ids'])
            qa = admission.get_for_subject(db, kind='query', subject_id=self.query_id)
            row = db.execute('SELECT * FROM warehouse_admissions WHERE id=?', (qa.id,)).fetchone()
            self.assertTrue(row['evidence_ref'])  # real UNKNOWN -> terminal path
            self.assertTrue(row['finished_at'])
        return frozen

    def restart_operator(self):
        original = self.operator
        self.operator = recovery.Recovery.__new__(recovery.Recovery)
        for name in ('actor', 'config', 'seal', 'adapter'):
            setattr(self.operator, name, getattr(original, name))

    def assert_no_terminal_backend_actions(self):
        for name in ('proof', 'stop', 'remove', 'old_present', 'healthy'):
            getattr(self.backend, name).assert_not_called()

    def assert_terminal_preserved(self, frozen):
        with control_store.connect() as db:
            self.operator._closed_graph(db, self.plan()['operation_id'], frozen, mode=recovery.TERMINAL_PARENT)
            self.assertEqual(admission.blockers(db), ())
            self.assertEqual(admission.control(db)['state'], 'OPEN')
            self.assertEqual({r[0] for r in db.execute('SELECT execution_closure FROM warehouse_admissions')}, {''})

    def test_terminal_original_cancel_after_revoke_pending_regrant_closes_parents_only(self):
        frozen = self.terminal_children()
        with control_store.connect() as db:
            effective = grants.effective(db, self.f.p.principal_id, self.f.c.database_id)
            grants_before = [tuple(r) for r in db.execute('SELECT * FROM grant_audit ORDER BY id')]
            # Persisted pending steps are skipped even if the crashed worker did not settle them.
            db.execute("UPDATE db_job_steps SET status='pending' WHERE position=2")
            frozen = recovery.graph(db, self.run_id)
        self.assertEqual(effective['maximum_role'], 'revoked')
        self.assertGreater(effective['epoch'], self.f.c.grant_revision)
        pin = qs._pins[self.query_id]
        with patch.object(qs, '_transport') as transport, patch.object(qs, 'observe_query_backend') as observer, \
             patch.object(db_jobs, '_start') as start, \
             patch.object(grants, 'promote', side_effect=AssertionError('no grant promotion')):
            plan = self.plan()
            self.assertEqual((plan['mode'], plan['outcome'], plan['execution_closure']),
                             (recovery.TERMINAL_PARENT, 'TERMINAL_CHILDREN_PRESERVED', ''))
            result = self.apply(plan)
            self.assertEqual((result['phase'], result['parent_closure']), ('OPEN', recovery.PARENT_CLOSURE))
            self.assertEqual(result['execution_closure'], '')
            self.assertEqual(result['proof']['kind'], 'canonical-terminal-children')
            self.assertEqual(result['proof']['queries'][0]['state'], 'CANCELED')
            self.assert_code('DB_ACCESS_REVOKED', lambda: qs.fetch(self.f.p, self.query_id, 'old', selected_role='data_engineer'))
            self.assert_code('QUERY_NOT_FOUND', lambda: qs.inspect_query(self.admin, self.query_id, selected_role='admin'))
            transport.assert_not_called()
            observer.assert_not_called()
            start.assert_not_called()
        self.assertIs(qs._pins[self.query_id], pin)
        self.assertEqual(pin.context, self.f.c)
        self.assert_terminal_preserved(frozen)
        self.assert_no_terminal_backend_actions()
        with control_store.connect() as db:
            self.assertEqual(grants.effective(db, self.f.p.principal_id, self.f.c.database_id), effective)
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM grant_audit ORDER BY id')], grants_before)
            self.assertEqual({r[0] for r in db.execute("SELECT state FROM operations WHERE action='GRANT_CHANGE'")},
                             {'SUPERSEDED', 'AWAITING_APPLY'})
            self.assertEqual(db.execute('SELECT status FROM db_job_steps WHERE position=2').fetchone()[0], 'skipped')
            summary = json.loads(db.execute('SELECT summary FROM db_job_steps WHERE position=1').fetchone()[0])
            self.assertEqual(summary['code'], recovery.PARENT_CLOSURE)
            self.assertNotIn('execution_closure', summary)
        encoded = json.dumps(result)
        for private in ('SELECT', 'original-secret', 'credential_snapshot_ref', 'grant_revision', 'data'):
            self.assertNotIn(private, encoded)

    def test_terminal_reconcile_failed_with_known_prior_commit_preserves_mixed_outcomes(self):
        frozen = self.terminal_children('FAILED', prior=True)
        self.assertEqual({q['state'] for q in frozen['queries']}, {'FINISHED', 'FAILED'})
        prior = next(a for a in frozen['admissions'] if a['kind'] == 'query' and a['state'] == 'FINISHED')
        self.assertIsNone(prior['evidence_ref'])  # normal ACTIVE -> terminal is valid without it
        self.apply(self.plan())
        self.assert_terminal_preserved(frozen)
        self.assert_no_terminal_backend_actions()

    def test_terminal_reconcile_finished_boolean_alert_does_not_claim_job_success(self):
        frozen = self.terminal_children('FINISHED', alert=True)
        result = self.apply(self.plan())
        self.assertEqual(result['outcome'], 'TERMINAL_CHILDREN_PRESERVED')
        self.assertEqual(self.snapshot()[2]['status'], 'failed')
        self.assert_terminal_preserved(frozen)
        self.assert_no_terminal_backend_actions()

    def test_terminal_legacy_null_admissions_keep_exact_canonical_scope(self):
        self.terminal_children()
        with control_store.connect() as db:
            db.execute('UPDATE warehouse_admissions SET database_id=NULL')
            frozen = recovery.graph(db, self.run_id)
        self.apply(self.plan())
        self.assert_terminal_preserved(frozen)
        with control_store.connect() as db:
            self.assertEqual({r[0] for r in db.execute('SELECT database_id FROM warehouse_admissions')}, {None})
        self.assert_no_terminal_backend_actions()

    def test_terminal_plan_cannot_switch_to_fence_when_child_changes(self):
        self.terminal_children()
        plan = self.plan()
        self.backend.reset_mock()
        with control_store.connect() as db:
            db.execute("UPDATE query_records SET state='UNKNOWN'")
            db.execute("UPDATE warehouse_admissions SET state='UNKNOWN',finished_at=NULL WHERE kind='query'")
        self.assert_code('RECOVERY_GRAPH_CHANGED', lambda: self.apply(plan))
        self.assertEqual(self.snapshot()[1]['state'], 'OPEN')
        self.assertEqual(self.backend.mock_calls, [])

    def test_terminal_all_query_leases_block_plan_and_apply_including_known_prior_query(self):
        frozen = self.terminal_children(prior=True)
        for q in frozen['queries']:
            with self.subTest(query=q['id']), qs._acquire_query_owner(q['id']):
                self.assert_code('CONTEXT_MISMATCH', self.plan)
        plan = self.plan()
        self.backend.reset_mock()
        for q in frozen['queries']:
            with self.subTest(query=q['id']), qs._acquire_query_owner(q['id']):
                self.assert_code('CONTEXT_MISMATCH', lambda: self.apply(plan))
        self.assertEqual(self.snapshot()[1]['state'], 'OPEN')
        self.assertEqual(self.backend.mock_calls, [])

    def test_fence_also_leases_already_terminal_prior_children(self):
        self.unknown(steps=[dict(name=n, action='sql', mode='read-only', sql='SELECT 1')
                            for n in ('known', 'lost')], first_committed=True)
        with control_store.connect() as db:
            prior = db.execute("SELECT id FROM query_records WHERE state='FINISHED'").fetchone()[0]
        with qs._acquire_query_owner(prior):
            self.assert_code('CONTEXT_MISMATCH', self.plan)
        plan = self.plan()
        with qs._acquire_query_owner(prior):
            self.assert_code('CONTEXT_MISMATCH', lambda: self.apply(plan))
        self.backend.stop.assert_not_called()

    def test_terminal_rejects_wrong_scope_links_sql_commit_and_unresolved_children(self):
        self.terminal_children()
        mutations = [("UPDATE warehouse_admissions SET database_id=? WHERE kind=?", (self.f.f.databases[1], kind))
                     for kind in ('job', 'dataops', 'query')]
        mutations += [(sql, ()) for sql in (
            "UPDATE query_execution_contexts SET sql='SELECT 999'",
            "UPDATE query_execution_contexts SET selected_role='bi_viewer'",
            "UPDATE query_execution_contexts SET sql_catalog='foreign'",
            "UPDATE query_execution_contexts SET sql_schema='marts'",
            "UPDATE query_execution_contexts SET binding_revision=binding_revision+1",
            "UPDATE query_execution_contexts SET grant_revision=grant_revision+1",
            "UPDATE query_execution_contexts SET backend_username='foreign'",
            "UPDATE query_execution_contexts SET credential_snapshot_ref='foreign'",
            "UPDATE query_execution_contexts SET backend_query_id=NULL",
            "UPDATE query_execution_contexts SET backend_query_id='invalid/id'",
            "UPDATE warehouse_admissions SET finished_at=NULL WHERE kind='query'",
            "UPDATE dataops_queries SET commit_result='{}'",
            "UPDATE dataops_operations SET committed=1",
            "UPDATE dataops_operations SET context='{}'",
            "UPDATE dataops_operations SET actor='foreign'",
            "UPDATE dataops_operations SET backend_username='foreign'",
            "UPDATE dataops_operations SET credential_snapshot_ref='foreign'",
            "UPDATE dataops_operations SET fingerprint='wrong'",
            "UPDATE dataops_operations SET route='export'",
            "UPDATE dataops_queries SET action='ingest'",
            "UPDATE query_execution_contexts SET request_id='foreign'",
            "UPDATE warehouse_admissions SET state='ACTIVE' WHERE kind='query'",
            "UPDATE warehouse_admissions SET state='FINISHED' WHERE kind='query'",
            "UPDATE warehouse_admissions SET kind='foreign' WHERE kind='query'",
            "UPDATE query_records SET state='RUNNING'",
            "DELETE FROM dataops_queries")]
        for sql, parameters in mutations:
            with self.subTest(sql=sql, parameters=parameters), control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute(sql, parameters)
                with self.assertRaises(QueryError):
                    recovery.graph(db, self.run_id)
                db.rollback()
        self.operator.adapter.assert_not_called()

    def test_terminal_proof_commits_before_close_and_interrupted_close_rolls_back(self):
        frozen = self.terminal_children()
        plan = self.plan()
        original = self.operator._close
        def interrupted(db, *args, **kwargs):
            with control_store.connect() as persisted:
                row = persisted.execute('SELECT phase,proof FROM execution_recovery_requests').fetchone()
                self.assertEqual(row['phase'], 'PROVED')
                self.assertEqual(json.loads(row['proof']), recovery._terminal_proof(frozen))
                self.assertEqual(admission.control(persisted)['state'], 'DRAINING')
            original(db, *args, **kwargs)
            raise KeyboardInterrupt()
        with patch.object(self.operator, '_close', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.apply(plan)
        self.assertEqual(self.snapshot()[2]['status'], 'unknown')
        self.restart_operator()
        pending = self.plan()
        self.assertEqual((pending['operation_id'], pending['phase']), (plan['operation_id'], 'PROVED'))
        self.assert_code('REVISION_CONFLICT', lambda: self.apply(plan))
        with control_store.connect() as db:
            proof = db.execute('SELECT proof FROM execution_recovery_requests').fetchone()[0]
            db.execute('UPDATE execution_recovery_requests SET proof=NULL')
        self.assert_code('RECOVERY_GRAPH_CHANGED', lambda: self.apply(pending, reconcile=True))
        self.assertEqual((self.snapshot()[1]['state'], self.snapshot()[2]['status']), ('DRAINING', 'unknown'))
        with control_store.connect() as db:
            db.execute('UPDATE execution_recovery_requests SET proof=?', (proof,))
        self.assertEqual(self.apply(pending, reconcile=True)['phase'], 'OPEN')
        self.assert_terminal_preserved(frozen)
        self.assert_no_terminal_backend_actions()

    def test_terminal_closed_restart_reconcile_only_reopens_same_backend(self):
        frozen = self.terminal_children()
        plan = self.plan()
        self.assertEqual(self.apply(plan, reconcile=True), plan)
        self.assertEqual(self.snapshot()[1]['state'], 'OPEN')
        with patch.object(self.operator, '_closed_graph', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.apply(plan)
        self.assertEqual((self.snapshot()[1]['state'], self.snapshot()[2]['status']), ('DRAINING', 'failed'))
        self.restart_operator()
        pending = self.plan()
        self.assertEqual(pending['phase'], 'CLOSED')
        with patch.object(self.operator, '_close', side_effect=AssertionError('no close replay')):
            result = self.apply(pending, reconcile=True)
        self.assertEqual(result['phase'], 'OPEN')
        self.assertEqual(self.plan(), result)
        self.assertEqual(self.apply(result, reconcile=True), result)
        self.assert_terminal_preserved(frozen)
        self.assert_no_terminal_backend_actions()

    def test_legacy_intent_without_mode_replays_fence_with_identical_graph(self):
        self.unknown()
        with control_store.connect() as db:
            frozen = recovery.graph(db, self.run_id)
        encode = recovery._json
        def legacy(value):
            if isinstance(value, dict) and 'graph' in value and 'planned_at' in value:
                value = {k: v for k, v in value.items() if k != 'mode'}
            return encode(value)
        with patch.object(recovery, '_json', side_effect=legacy):
            plan = self.plan()
        with control_store.connect() as db:
            intent = json.loads(db.execute('SELECT intent FROM execution_recovery_requests').fetchone()[0])
            self.assertNotIn('mode', intent)
            self.assertEqual(intent['graph'], frozen)
            self.assertNotIn('mode', frozen)
        self.restart_operator()
        self.assertEqual(self.plan(), plan)
        self.assertEqual(self.apply(plan, reconcile=True), plan)
        result = self.apply(plan)
        self.assertEqual((result['mode'], result['outcome'], result['execution_closure']),
                         ('FENCE', 'UNKNOWN', recovery.CLOSURE))
        self.assertEqual(self.snapshot()[0]['state'], 'UNKNOWN')

    def test_terminal_plan_and_closed_resume_reject_current_operator_loss(self):
        self.terminal_children()
        plan = self.plan()
        for role in ('data_engineer', 'admin'):
            with self.subTest(role=role):
                users = self.f.f.fixture.users
                users['owner']['platform_admin'] = False
                self.f.f.fixture.save()
                with patch.object(catalog_admin, 'authenticate_headers', return_value=(self.admin, role)):
                    with self.assertRaises((PermissionError, QueryError)):
                        self.plan()
                    with self.assertRaises((PermissionError, QueryError)):
                        self.apply(plan)
                users['owner']['platform_admin'] = True
                self.f.f.fixture.save()
        with patch.object(self.operator, '_closed_graph', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.apply(plan)
        pending = self.operator.inspect(plan['operation_id'])
        self.backend.reset_mock()
        del self.f.f.fixture.users['owner']
        self.f.f.fixture.save()
        self.assert_code('RECOVERY_ADMIN_REQUIRED', lambda: self.apply(pending, reconcile=True))
        self.assertEqual(self.backend.mock_calls, [])
        self.assertEqual((self.snapshot()[1]['state'], self.snapshot()[2]['status']), ('DRAINING', 'failed'))

    def test_terminal_runtime_profile_drift_and_mode_are_not_caller_authority(self):
        self.terminal_children()
        plan = self.plan()
        self.operator.adapter = recovery.Recovery.adapter.__get__(self.operator)
        with patch.object(recovery.runtime_profile, 'private_binding', return_value=dict(changed=True)):
            self.assert_code('RECOVERY_RUNTIME_CHANGED', lambda: self.apply(plan))
        with control_store.connect() as db:
            intent = json.loads(db.execute('SELECT intent FROM execution_recovery_requests').fetchone()[0])
        self.assertEqual(recovery._mode(intent), recovery.TERMINAL_PARENT)
        for value in (dict(intent, mode='FENCE'), dict(intent, mode='caller-success'),
                      {k: v for k, v in intent.items() if k != 'mode'}):
            self.assert_code('RECOVERY_INELIGIBLE', lambda: recovery._mode(value))
        self.assertEqual(self.snapshot()[1]['state'], 'OPEN')
        self.assert_no_terminal_backend_actions()

    def test_terminal_operator_loss_during_final_runtime_check_keeps_closed_drain(self):
        self.terminal_children()
        plan = self.plan()
        self.operator.adapter.reset_mock()
        def adapter(frozen):
            if self.operator.adapter.call_count == 2:
                self.f.f.fixture.users['owner']['platform_admin'] = False
                self.f.f.fixture.save()
            return self.backend
        self.operator.adapter.side_effect = adapter
        self.assert_code('RECOVERY_ADMIN_REQUIRED', lambda: self.apply(plan))
        self.assertEqual((self.snapshot()[1]['state'], self.snapshot()[2]['status']), ('DRAINING', 'failed'))
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT phase FROM execution_recovery_requests').fetchone()[0], 'CLOSED')
        self.assert_no_terminal_backend_actions()

    def test_terminal_binding_runtime_generation_scope_and_control_conflicts_keep_drain(self):
        self.terminal_children()
        plan = self.plan()
        # Scope must match before entering drain, without trying a replacement.
        self.backend.scope.return_value = dict(self.scope, container_id='b' * 64)
        self.assert_code('RECOVERY_SCOPE_CHANGED', lambda: self.apply(plan))
        self.assertEqual(self.snapshot()[1]['state'], 'OPEN')
        self.backend.scope.return_value = self.scope
        with patch.object(self.operator, '_closed_graph', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.apply(plan)
        pending = self.operator.inspect(plan['operation_id'])
        original_seal = self.operator.seal
        self.operator.seal = dict(changed=True)
        self.assert_code('RECOVERY_RUNTIME_CHANGED', lambda: self.apply(pending, reconcile=True))
        self.operator.seal = original_seal
        self.backend.scope.return_value = dict(self.scope, engine_id='changed')
        self.assert_code('RECOVERY_SCOPE_CHANGED', lambda: self.apply(pending, reconcile=True))
        self.backend.scope.return_value = self.scope
        with control_store.connect() as db:
            binding = dm.get_binding(db, self.f.c.database_id)
            control = admission.control(db)
            db.execute('UPDATE catalog_bindings SET desired_revision=desired_revision+1 WHERE database_id=?',
                       (self.f.c.database_id,))
        self.assert_code('RECOVERY_BINDING_CHANGED', lambda: self.apply(pending, reconcile=True))
        with control_store.connect() as db:
            db.execute('UPDATE catalog_bindings SET desired_revision=? WHERE database_id=?',
                       (binding.desired_revision, self.f.c.database_id))
            db.execute('UPDATE warehouse_controls SET applied_generation_id=?', ('g-' + self.f.f.databases[1],))
        self.assert_code('RECOVERY_BINDING_CHANGED', lambda: self.apply(pending, reconcile=True))
        with control_store.connect() as db:
            db.execute('UPDATE warehouse_controls SET applied_generation_id=?,revision=revision+1',
                       (control['applied_generation_id'],))
        self.assert_code('REVISION_CONFLICT', lambda: self.apply(pending, reconcile=True))
        self.assertEqual(self.snapshot()[1]['state'], 'DRAINING')
        self.assert_no_terminal_backend_actions()

    def test_terminal_closed_graph_change_and_unrelated_work_prevent_reopen(self):
        self.terminal_children()
        plan = self.plan()
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            extra = admission.admit(db, actor='unrelated', kind='query', subject_id='foreign')
        self.assert_code('UNRELATED_UNRESOLVED_WORK', lambda: self.apply(plan))
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            admission.finish(db, extra, state='CANCELED')
        with patch.object(self.operator, '_closed_graph', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.apply(plan)
        pending = self.operator.inspect(plan['operation_id'])
        with control_store.connect() as db:
            db.execute("UPDATE query_execution_contexts SET backend_username='foreign' WHERE query_id=?", (self.query_id,))
        self.assert_code('RECOVERY_GRAPH_CHANGED', lambda: self.apply(pending, reconcile=True))
        self.assertEqual(self.snapshot()[1]['state'], 'DRAINING')
        self.assert_no_terminal_backend_actions()

    def test_positive_closure_preserves_unknown_and_never_starts_next_step(self):
        self.unknown(steps=[dict(name=n, action='sql', mode='read-only', sql='SELECT 1') for n in ('read', 'never')])
        before = self.snapshot()[0]
        plan = self.plan()
        result = self.apply(plan)
        self.assertEqual((result['phase'], result['execution_closure']), ('OPEN', recovery.CLOSURE))
        after, control, run = self.snapshot()
        self.assertEqual(after, before)
        self.assertEqual((run['status'], control['state']), ('failed', 'OPEN'))
        with control_store.connect() as db:
            self.assertEqual(admission.blockers(db), ())
            self.assertEqual(db.execute('SELECT state FROM dataops_operations').fetchone()[0], 'FAILED')
            summary = json.loads(db.execute('SELECT summary FROM db_job_steps WHERE position=1').fetchone()[0])
            self.assertEqual(summary['code'], recovery.ABANDONED)
            self.assertEqual(db.execute('SELECT status FROM db_job_steps WHERE position=2').fetchone()[0], 'skipped')
        self.backend.stop.assert_not_called()  # already positive stopped receipt
        self.assertEqual(len(self.f.f.calls), 0)

    def test_known_terminal_prior_write_and_result_are_untouched(self):
        steps = [dict(name='committed', action='sql', mode='sandbox-write', sql='CREATE TABLE sandbox.kept AS SELECT 1'),
                 dict(name='lost', action='sql', mode='read-only', sql='SELECT 17')]
        self.unknown(steps=steps, first_committed=True)
        with control_store.connect() as db:
            prior = db.execute("SELECT id FROM dataops_operations WHERE state='FINISHED'").fetchone()[0]
            db.execute('UPDATE dataops_operations SET committed=1,result=? WHERE id=?', ('{"kept":17}', prior))
            before = dict(db.execute('SELECT * FROM dataops_operations WHERE id=?', (prior,)).fetchone())
        self.apply(self.plan())
        with control_store.connect() as db:
            self.assertEqual(dict(db.execute('SELECT * FROM dataops_operations WHERE id=?', (prior,)).fetchone()), before)

    def test_wrong_typed_database_on_each_admission_rejected_before_backend(self):
        self.unknown()
        for kind in ('job', 'dataops', 'query'):
            with self.subTest(kind=kind):
                with control_store.connect() as db:
                    db.execute('UPDATE warehouse_admissions SET database_id=? WHERE kind=?',
                               (self.f.f.databases[1], kind))
                before = self.snapshot()
                self.assert_code('RECOVERY_INELIGIBLE', self.plan)
                self.assertEqual(self.snapshot(), before)
                self.operator.adapter.assert_not_called()
                self.assertEqual(self.backend.mock_calls, [])
                with control_store.connect() as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM execution_recovery_requests').fetchone()[0], 0)
                    db.execute('UPDATE warehouse_admissions SET database_id=? WHERE kind=?',
                               (self.f.c.database_id, kind))

    def test_legacy_null_database_requires_canonical_links_and_stays_null(self):
        self.unknown()
        with control_store.connect() as db:
            db.execute('UPDATE warehouse_admissions SET database_id=NULL')
        # NULL is not a bypass for the persisted full context/link checks.
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE query_execution_contexts SET request_id='foreign'")
            with self.assertRaises(QueryError):
                recovery.graph(db, self.run_id)
            db.rollback()
        before = self.snapshot()[0]
        result = self.apply(self.plan())
        self.assertEqual((result['phase'], result['execution_closure']), ('OPEN', recovery.CLOSURE))
        self.assertEqual(self.snapshot()[0], before)
        with control_store.connect() as db:
            self.assertEqual({r[0] for r in db.execute('SELECT database_id FROM warehouse_admissions')}, {None})
            self.assertEqual(admission.blockers(db), ())

    def test_no_proof_keeps_draining_and_reconcile_never_replays_stop(self):
        self.unknown()
        before = self.snapshot()[0]
        plan = self.plan()
        self.backend.proof.return_value = None
        self.assert_code('RECOVERY_STOP_UNPROVED', lambda: self.apply(plan))
        self.backend.stop.assert_called_once()
        pending = self.operator.inspect(plan['operation_id'])
        self.assertEqual((pending['phase'], self.snapshot()[1]['state']), ('STOP_INTENT', 'DRAINING'))
        self.backend.scope.side_effect = QueryError('RECOVERY_BACKEND_UNAVAILABLE', 409)
        self.assertEqual(self.apply(pending, reconcile=True)['phase'], 'STOP_INTENT')
        self.backend.stop.assert_called_once()
        self.assertEqual(self.snapshot()[2]['status'], 'unknown')
        self.assertEqual(self.snapshot()[0], before)
        with control_store.connect() as db:
            with self.assertRaises(admission.AdmissionDenied):
                admission.check_open(db)

    def test_unavailable_stop_proof_does_not_invent_terminal_query_info(self):
        self.unknown()
        before = self.snapshot()[0]
        plan = self.plan()
        self.backend.proof.side_effect = QueryError('RECOVERY_BACKEND_UNAVAILABLE', 409)
        with patch.object(qs, 'observe_query_backend', side_effect=OSError('terminal record unavailable')) as observer, \
             patch.object(qs, '_transport') as transport:
            self.assert_code('RECOVERY_BACKEND_UNAVAILABLE', lambda: self.apply(plan))
            pending = self.operator.inspect(plan['operation_id'])
            self.assert_code('RECOVERY_BACKEND_UNAVAILABLE', lambda: self.apply(pending, reconcile=True))
            observer.assert_not_called()
            transport.assert_not_called()
        self.assertEqual(self.snapshot()[0], before)
        self.assertEqual((self.snapshot()[1]['state'], self.snapshot()[2]['status']), ('DRAINING', 'unknown'))
        with control_store.connect() as db:
            self.assertTrue(admission.blockers(db))
            self.assertEqual(db.execute("SELECT execution_closure FROM warehouse_admissions WHERE kind='query'").fetchone()[0], '')
        self.backend.stop.assert_not_called()
        self.backend.remove.assert_not_called()

    def test_health_interruption_closes_once_and_reconcile_opens_without_destruction(self):
        self.unknown()
        plan = self.plan()
        self.backend.healthy.side_effect = OSError('replacement not ready')
        with self.assertRaises(OSError):
            self.apply(plan)
        pending = self.operator.inspect(plan['operation_id'])
        self.assertEqual((pending['phase'], self.snapshot()[1]['state']), ('CLOSED', 'DRAINING'))
        self.backend.stop.assert_not_called()
        self.backend.proof.reset_mock()
        self.backend.healthy.side_effect = None
        self.assertEqual(self.apply(pending, reconcile=True)['phase'], 'OPEN')
        self.backend.proof.assert_not_called()
        self.backend.stop.assert_not_called()

    def test_interrupt_after_stop_before_proof_retained_event_resumes(self):
        self.unknown()
        plan = self.plan()
        self.backend.proof.return_value = None
        def stop(scope):
            self.backend.proof.return_value = dict(kind='exact-event', receipt={'Action': 'die'})
            raise KeyboardInterrupt()
        self.backend.stop.side_effect = stop
        with self.assertRaises(KeyboardInterrupt):
            self.apply(plan)
        pending = self.operator.inspect(plan['operation_id'])
        self.assertEqual(pending['phase'], 'STOP_INTENT')
        self.assertEqual(self.apply(pending, reconcile=True)['phase'], 'OPEN')
        self.backend.stop.assert_called_once()

    def test_interrupt_after_proof_before_finalization(self):
        self.unknown()
        plan = self.plan()
        with patch.object(self.operator, '_close', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.apply(plan)
        pending = self.operator.inspect(plan['operation_id'])
        self.assertEqual(pending['phase'], 'PROVED')
        self.backend.proof.reset_mock()
        self.assertEqual(self.apply(pending, reconcile=True)['phase'], 'OPEN')
        self.backend.proof.assert_not_called()

    def test_plan_replay_cas_graph_and_engine_conflicts(self):
        self.unknown()
        plan = self.plan()
        self.assertEqual(self.plan(), plan)
        self.assert_code('REVISION_CONFLICT', lambda: self.operator.apply(plan['operation_id'], 999))
        self.operator.seal = dict(changed=True)
        self.assert_code('IDEMPOTENCY_CONFLICT', self.plan)
        self.operator.seal = dict(version=1)
        self.backend.scope.return_value = dict(self.scope, engine_id='changed')
        self.assert_code('RECOVERY_SCOPE_CHANGED', lambda: self.apply(plan))
        self.assertEqual(self.snapshot()[1]['state'], 'OPEN')
        self.backend.scope.return_value = self.scope
        with control_store.connect() as db:
            db.execute("UPDATE query_execution_contexts SET sql='SELECT 99' WHERE query_id=?", (self.query_id,))
        with self.assertRaises(QueryError):
            self.apply(plan)
        self.backend.stop.assert_not_called()

    def test_live_job_or_query_owner_rejected(self):
        self.unknown()
        lock = db_jobs.legacy._lock()
        try:
            self.assert_code('JOB_BUSY', self.plan)
        finally:
            lock.close()
        with qs._acquire_query_owner(self.query_id):
            self.assert_code('CONTEXT_MISMATCH', self.plan)

    def test_unresolved_write_or_typed_child_rejected(self):
        self.unknown()
        for sql, mode, action in [('DELETE FROM raw.keep', 'admin-write', 'sql'),
                                  ('SELECT 1', 'read-only', 'table_export'),
                                  ('SELECT 1', 'sandbox-write', 'sql')]:
            with control_store.connect() as db:
                original = db.execute('SELECT definition FROM db_job_runs').fetchone()[0]
                changed = json.loads(original)
                changed['steps'][0].update(sql=sql, mode=mode, action=action)
                db.execute('UPDATE db_job_runs SET definition=?', (json.dumps(changed),))
            with self.assertRaises(QueryError):
                self.plan()
            with control_store.connect() as db:
                db.execute('UPDATE db_job_runs SET definition=?', (original,))
        self.backend.scope.assert_not_called()

    def test_missing_foreign_and_ambiguous_children_fail_closed(self):
        self.unknown()
        mutations = ["UPDATE dataops_queries SET action='ingest'",
                     "UPDATE dataops_operations SET route='export'",
                     "UPDATE query_execution_contexts SET request_id='foreign'",
                     "UPDATE query_execution_contexts SET mode='read-only'",
                     "UPDATE warehouse_admissions SET kind='untyped' WHERE kind='query'",
                     "DELETE FROM dataops_queries"]
        for sql in mutations:
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute(sql)
                with self.assertRaises(QueryError):
                    recovery.graph(db, self.run_id)
                db.rollback()

    def test_unrelated_unknown_write_remains_blocking(self):
        self.unknown()
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            extra = admission.admit(db, actor='unrelated', kind='ingest', subject_id='other-write')
            admission.finish(db, extra, state='UNKNOWN')
        self.assert_code('UNRELATED_UNRESOLVED_WORK', self.plan)
        self.backend.stop.assert_not_called()

    def test_current_admin_downgrade_rejected_at_plan_and_finalize(self):
        self.unknown()
        plan = self.plan()
        users = self.f.f.fixture.users
        users['owner']['platform_admin'] = False
        self.f.f.fixture.save()
        self.assert_code('RECOVERY_ADMIN_REQUIRED', self.plan)
        self.assert_code('RECOVERY_ADMIN_REQUIRED', lambda: self.apply(plan))
        self.backend.stop.assert_not_called()

    def test_current_operator_account_revocation_rejected(self):
        self.unknown()
        plan = self.plan()
        self.backend.reset_mock()
        del self.f.f.fixture.users['owner']
        self.f.f.fixture.save()
        before = self.snapshot()
        self.assert_code('RECOVERY_ADMIN_REQUIRED', self.plan)
        self.assert_code('RECOVERY_ADMIN_REQUIRED', lambda: self.apply(plan))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.backend.mock_calls, [])

    def test_closed_unknown_late_state_fetch_cancel_inspect_reconcile(self):
        self.unknown()
        pin = qs._pins[self.query_id]
        pin.tokens['cached'] = ('http://127.0.0.1:1234/v1/statement/old/1', frozenset(('GET','DELETE')))
        pin.responses['cached'] = dict(data=[['must never escape']], state='FINISHED')
        self.apply(self.plan())
        with patch.object(qs, '_transport') as transport, patch.object(qs, 'observe_query_backend') as observer:
            for state in ('FINISHED', 'CANCELED', 'FAILED', 'RUNNING', 'UNKNOWN'):
                self.assert_code('EXECUTION_CLOSED', lambda: qs._state(self.query_id, state))
            self.assert_code('EXECUTION_CLOSED', lambda: qs.fetch(self.f.p, self.query_id, 'cached', selected_role='data_engineer'))
            self.assertEqual(qs.cancel(self.f.p, self.query_id, selected_role='data_engineer')['state'], 'UNKNOWN')
            self.assertEqual(qs.inspect_query(self.f.p, self.query_id, selected_role='data_engineer')['execution_closure'], recovery.CLOSURE)
            reconciled = qs.reconcile_query_from_backend(self.f.p, self.query_id, selected_role='data_engineer')
            self.assertEqual(reconciled['state'], 'UNKNOWN')
            self.assertEqual(reconciled['execution_closure'], recovery.CLOSURE)
            transport.assert_not_called()
            observer.assert_not_called()
        with self.assertRaises(admission.AdmissionConflict), admission.accepted(pin.admission, allow_unknown=True):
            pass

    def test_transport_rechecks_after_shared_gate_queue(self):
        self.unknown()
        pin = qs._pins[self.query_id]
        plan = self.plan()
        @contextmanager
        def delayed_gate():
            self.apply(plan)
            yield
        # Use the actual transport even though the surrounding fixture mocks it.
        self.f.f.transport_patch.stop()
        with patch('warehouse_resident.warehouse_activity', delayed_gate), patch('http.client.HTTPConnection') as http:
            self.assert_code('EXECUTION_CLOSED', lambda: qs._transport(pin.origin + '/v1/statement', 'POST', pin, b'SELECT 1'))
            http.assert_not_called()

    def test_cross_process_accepted_post_waiting_behind_fence_is_never_sent(self):
        self.unknown()
        plan = self.plan()
        script = '''
import json,sys
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
import query_service as q, control_store as s, warehouse_admission as a
from query_context import ResolvedQueryContext
with s.connect() as db:
    row=db.execute('SELECT context FROM db_job_runs WHERE id=?',(sys.argv[2],)).fetchone()
    context=ResolvedQueryContext(**json.loads(row[0]))
    handle=a.get_for_subject(db,kind='query',subject_id=sys.argv[3])
pin=q._Pin(context,'http://127.0.0.1:1234',{},None,admission=handle)
with a.accepted(handle,allow_unknown=True):
    print('ACCEPTED',flush=True)
    with patch('warehouse_resident.warehouse_activity',a.lifecycle_gate), patch('http.client.HTTPConnection') as http:
        try: q._transport(pin.origin+'/v1/statement','POST',pin,b'SELECT 1')
        except Exception as e: print(getattr(e,'code',type(e).__name__),flush=True)
        print('HTTP='+str(http.call_count),flush=True)
'''
        with admission.lifecycle_gate(exclusive=True):
            child = subprocess.Popen([sys.executable,'-c',script,
                str(Path(recovery.__file__).parent),self.run_id,self.query_id], stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            self.addCleanup(lambda: child.poll() is None and child.kill())
            self.assertTrue(select.select([child.stdout],[],[],5)[0], 'child did not reach admission preflight')
            self.assertEqual(child.stdout.readline().strip(),'ACCEPTED')
            self.apply(plan)
        stdout,stderr = child.communicate(timeout=10)
        self.assertEqual(child.returncode,0,stderr)
        self.assertIn('EXECUTION_CLOSED',stdout)
        self.assertIn('HTTP=0',stdout)

    def test_stop_receipt_is_persisted_before_atomic_close(self):
        self.unknown()
        plan = self.plan()
        self.backend.proof.side_effect = [None,dict(kind='stopped-inspect',receipt={'Running':False})]
        original = self.operator._close
        def check(db,*args):
            row = db.execute('SELECT phase,proof FROM execution_recovery_requests').fetchone()
            self.assertEqual(row['phase'],'PROVED')
            self.assertIsNotNone(row['proof'])
            original(db,*args)
        with patch.object(self.operator,'_close',side_effect=check):
            self.apply(plan)
        self.backend.stop.assert_called_once_with(self.scope)

    def test_interruption_before_stop_can_resume_only_exact_running_original(self):
        self.unknown()
        plan = self.plan()
        self.backend.proof.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt): self.apply(plan)
        pending = self.operator.inspect(plan['operation_id'])
        self.assertEqual(pending['phase'],'STOP_INTENT')
        self.backend.stop.assert_not_called()
        self.backend.proof.side_effect = [None,dict(kind='stopped-inspect',receipt={'Running':False})]
        self.assertEqual(self.apply(pending,reconcile=True)['phase'],'STOP_INTENT')
        self.backend.stop.assert_not_called()
        self.backend.proof.side_effect = [None,dict(kind='stopped-inspect',receipt={'Running':False})]
        self.assertEqual(self.apply(pending)['phase'],'OPEN')
        self.backend.stop.assert_called_once()

    def test_engine_change_reconciliation_never_stops_replacement(self):
        self.unknown()
        plan = self.plan()
        self.backend.proof.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt): self.apply(plan)
        pending = self.operator.inspect(plan['operation_id'])
        self.backend.engine.return_value = dict(engine_id='different-engine')
        self.assert_code('RECOVERY_ENGINE_CHANGED',lambda:self.apply(pending,reconcile=True))
        self.backend.stop.assert_not_called()

    def test_normal_job_admission_opens_after_valid_health_only(self):
        self.unknown()
        plan = self.plan()
        self.backend.healthy.side_effect = OSError('not ready')
        with self.assertRaises(OSError): self.apply(plan)
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaises(admission.AdmissionDenied):
                admission.admit(db,actor=self.f.p.principal_id,kind='job',subject_id='new')
        self.backend.healthy.side_effect = None
        self.apply(self.operator.inspect(plan['operation_id']),reconcile=True)
        fresh = self.f.submit(key='fresh-normal-job')
        self.assertEqual(fresh['status'],'queued')

    def test_cleanup_allows_revoked_old_owner_without_result_authority(self):
        self.unknown()
        with control_store.connect() as db:
            before = db.execute('SELECT context FROM db_job_runs').fetchone()[0]
            db.execute('DELETE FROM grants WHERE principal_id=?',(self.f.p.principal_id,))
        self.apply(self.plan())
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT context FROM db_job_runs').fetchone()[0],before)
        self.assert_code('QUERY_NOT_FOUND',lambda:qs.inspect_query(self.admin,self.query_id,selected_role='admin'))
        self.assert_code('DB_ACCESS_REVOKED',lambda:qs.inspect_query(self.f.p,self.query_id,selected_role='data_engineer'))

    def owner_epoch_loss(self, *, regrant):
        self.unknown(steps=[dict(name=n, action='sql', mode='read-only', sql='SELECT 1')
                            for n in ('lost', 'never')])
        self.assertNotEqual(self.admin.principal_id, self.f.p.principal_id)
        self.operator.authenticate()  # distinct, current platform admin with selected admin
        before, _, original_run = self.snapshot()
        pin = qs._pins[self.query_id]
        with control_store.connect() as db:
            frozen = recovery.graph(db, self.run_id)
            old = grants.effective(db, self.f.p.principal_id, self.f.c.database_id)
            desired = dm.get_grant(db, self.f.p.principal_id, self.f.c.database_id)
        self.assertEqual(old['epoch'], self.f.c.grant_revision)
        change = grants.mutate(self.admin, 'admin', self.f.c.database_id,
            dict(principal_id=self.f.p.principal_id, expected_revision=desired.revision,
                 maximum_role='revoked' if regrant else 'data_engineer'), 'owner-epoch-loss')
        self.assertEqual(change['state'], 'AWAITING_APPLY')
        plan = self.plan()  # owner epoch loss does not revoke this operator's cleanup authority
        if regrant:
            change = grants.mutate(self.admin, 'admin', self.f.c.database_id,
                dict(principal_id=self.f.p.principal_id, expected_revision=desired.revision + 1,
                     maximum_role='data_engineer'), 'owner-pending-regrant')
            self.assertEqual(change['state'], 'AWAITING_APPLY')
        with control_store.connect() as db:
            effective = grants.effective(db, self.f.p.principal_id, self.f.c.database_id)
            self.assertGreater(effective['epoch'], old['epoch'])
            self.assertEqual(effective['maximum_role'], 'revoked' if regrant else 'data_engineer')
            self.assertEqual(recovery.graph(db, self.run_id), frozen)
            grant_rows = [tuple(r) for r in db.execute('SELECT * FROM grants ORDER BY principal_id,database_id')]
            audit_rows = [tuple(r) for r in db.execute('SELECT * FROM grant_audit ORDER BY id')]
        with patch.object(qs, '_transport') as transport, patch.object(qs, 'observe_query_backend') as observer, \
             patch.object(db_jobs, '_start') as start, \
             patch.object(grants, 'promote', side_effect=AssertionError('recovery cannot promote grants')):
            result = self.apply(plan)
            self.assert_code('DB_ACCESS_REVOKED', lambda: qs.inspect_query(
                self.f.p, self.query_id, selected_role='data_engineer'))
            self.assert_code('DB_ACCESS_REVOKED', lambda: qs.fetch(
                self.f.p, self.query_id, 'cached', selected_role='data_engineer'))
            self.assert_code('QUERY_NOT_FOUND', lambda: qs.inspect_query(
                self.admin, self.query_id, selected_role='admin'))
            self.assert_code('DB_ACCESS_REVOKED' if regrant else 'CONTEXT_MISMATCH', lambda: db_jobs.submit_run(
                self.f.p, self.f.c, original_run['job_id'], expected_revision=1,
                arguments={}, key='old-context-resubmit'))
            transport.assert_not_called()
            observer.assert_not_called()
            start.assert_not_called()
        self.assertEqual((result['phase'], result['outcome'], result['execution_closure']),
                         ('OPEN', 'UNKNOWN', recovery.CLOSURE))
        after, control, run = self.snapshot()
        self.assertEqual(after, before)
        self.assertEqual(run, dict(original_run, status='failed', finished_at=run['finished_at']))
        self.assertEqual(control['applied_generation_id'], self.f.c.generation_id)
        self.assertIs(qs._pins[self.query_id], pin)
        self.assertEqual(pin.context, self.f.c)
        with control_store.connect() as db:
            self.assertEqual(grants.effective(db, self.f.p.principal_id, self.f.c.database_id), effective)
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM grants ORDER BY principal_id,database_id')], grant_rows)
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM grant_audit ORDER BY id')], audit_rows)
            self.assertEqual(grants.inspect(db, change['id'], self.admin.principal_id)['state'], 'AWAITING_APPLY')
            self.assertEqual(dict(db.execute('SELECT * FROM catalog_bindings WHERE database_id=?',
                                            (self.f.c.database_id,)).fetchone()), frozen['binding'])
            self.assertEqual([dict(r) for r in db.execute('SELECT * FROM query_execution_contexts')], frozen['executions'])
            self.assertEqual(db.execute('SELECT count(*) FROM db_job_runs').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM dataops_operations').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM query_records').fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT state,execution_closure FROM warehouse_admissions WHERE kind='query'").fetchone()[:],
                             ('UNKNOWN', recovery.CLOSURE))
            self.assertEqual({r[0] for r in db.execute("SELECT state FROM warehouse_admissions WHERE kind IN ('job','dataops')")}, {'FAILED'})
            self.assertEqual(db.execute('SELECT status FROM db_job_steps WHERE position=2').fetchone()[0], 'skipped')
            self.assertEqual(admission.blockers(db), ())
        self.assertEqual(self.f.f.calls, [])

    def test_owner_epoch_change_keeps_original_context_and_unknown(self):
        self.owner_epoch_loss(regrant=False)

    def test_owner_revoke_and_pending_regrant_are_not_promoted_by_recovery(self):
        self.owner_epoch_loss(regrant=True)

    def test_downgrade_after_proof_keeps_draining(self):
        self.unknown()
        plan = self.plan()
        def downgrade(*args):
            self.f.f.fixture.users['owner']['platform_admin'] = False
            self.f.f.fixture.save()
        self.backend.healthy.side_effect = downgrade
        self.assert_code('RECOVERY_ADMIN_REQUIRED',lambda:self.apply(plan))
        self.assertEqual(self.snapshot()[1]['state'],'DRAINING')

    def test_binding_or_graph_change_while_waiting_for_health_rejected(self):
        self.unknown()
        plan = self.plan()
        self.backend.healthy.side_effect = OSError('wait')
        with self.assertRaises(OSError):self.apply(plan)
        pending = self.operator.inspect(plan['operation_id'])
        self.backend.healthy.side_effect = None
        with control_store.connect() as db:
            db.execute("UPDATE db_job_runs SET backend_username='changed'")
        self.assert_code('RECOVERY_GRAPH_CHANGED',lambda:self.apply(pending,reconcile=True))
        self.assertEqual(self.snapshot()[1]['state'],'DRAINING')

    def test_draining_intent_blocks_old_unknown_transport_even_before_proof(self):
        self.unknown()
        plan = self.plan()
        self.backend.proof.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):self.apply(plan)
        pin = qs._pins[self.query_id]
        with self.assertRaises(admission.AdmissionDenied),admission.accepted(pin.admission,allow_unknown=True):
            pass
        self.f.f.transport_patch.stop()
        with patch('http.client.HTTPConnection') as http:
            with self.assertRaises(admission.AdmissionDenied):
                qs._transport(pin.origin+'/v1/statement','POST',pin,b'SELECT 1')
            http.assert_not_called()

    def test_minimal_private_config_shape_and_absolute_paths(self):
        config = dict(version=1,auth_headers='/private/auth-headers.json',runtime_profile='/private/runtime-profile.json',
                      docker_context='colima-mac-studio-solo',kube_context='colima-mac-studio-solo')
        profile = SimpleNamespace(state_dir=str(control_store.root()),auth_file=str(self.f.f.fixture.accounts))
        with patch.object(recovery.runtime_profile,'private_json',return_value=config), \
             patch.object(recovery.runtime_profile,'load',return_value=profile), \
             patch.object(recovery.runtime_profile,'private_binding',return_value=self.operator.seal):
            self.assertEqual(recovery.Recovery('/private/recovery.json').actor,self.admin.principal_id)
            for change in (dict(proof={}),dict(container_id='a'*64),dict(docker_context='default'),
                           dict(kube_context='other'),dict(auth_headers='relative.json'),dict(version=True)):
                with patch.object(recovery.runtime_profile,'private_json',return_value=dict(config,**change)):
                    with self.assertRaises(QueryError): recovery.Recovery('/private/recovery.json')

    def test_selected_admin_role_and_actor_cannot_be_switched(self):
        self.unknown()
        with patch.object(catalog_admin,'authenticate_headers',return_value=(self.admin,'data_engineer')):
            with self.assertRaises(PermissionError):self.plan()
        with patch.object(catalog_admin,'authenticate_headers',return_value=(self.f.p,'data_engineer')):
            with self.assertRaises(PermissionError):self.plan()

    def test_cli_command_shapes_dispatch_without_runtime(self):
        commands = [('plan',['--run-id','a-run','--idempotency-key','a-key']),
                    ('apply',['--operation-id','an-operation','--expected-revision','3']),
                    ('reconcile',['--operation-id','an-operation','--expected-revision','4']),
                    ('inspect',['--operation-id','an-operation'])]
        for name,args in commands:
            with patch.object(recovery,'run',return_value=dict(phase='TEST_ONLY')) as run, patch('sys.stdout',new_callable=io.StringIO):
                self.assertEqual(catalog_admin.main(['recover-job-'+name,'--config','/private/recovery.json']+args),0)
                parsed = run.call_args.args[0]
                self.assertEqual(parsed.command,'recover-job-'+name)
                self.assertEqual(parsed.config,Path('/private/recovery.json'))

    def test_intent_is_immutable_and_operation_has_generic_inspection(self):
        self.unknown()
        plan = self.plan()
        with control_store.connect() as db:
            for field in ('actor','intent','payload','idempotency_key'):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute('UPDATE execution_recovery_requests SET '+field+"='changed'")
        result = catalog_admin.inspect_operation(self.admin,'admin',plan['operation_id'])
        self.assertEqual(result['phase'],'PLANNED')
        self.assertNotIn('sql',result)
        self.assertNotIn('result',result)

    def test_reconcile_planned_and_proved_are_observer_only_apply_removes(self):
        self.unknown()
        plan = self.plan()
        self.assertEqual(self.apply(plan,reconcile=True),plan)
        self.backend.stop.assert_not_called()
        self.backend.remove.assert_not_called()
        self.backend.old_present.return_value = True
        self.backend.remove.side_effect = lambda scope:self.backend.old_present.configure_mock(return_value=False)
        with patch.object(self.operator,'_close',side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):self.apply(plan)
        pending = self.operator.inspect(plan['operation_id'])
        self.assertEqual(pending['phase'],'PROVED')
        # Simulate crash before removal rather than the later close boundary.
        self.backend.old_present.return_value = True
        self.backend.remove.reset_mock()
        self.assertEqual(self.apply(pending,reconcile=True)['phase'],'PROVED')
        self.backend.remove.assert_not_called()
        self.backend.stop.reset_mock()
        self.assertEqual(self.apply(pending)['phase'],'OPEN')
        self.backend.remove.assert_called_once_with(self.scope)
        self.backend.stop.assert_not_called()

    def test_crash_after_proof_before_remove_resumes_without_stop(self):
        self.unknown()
        plan = self.plan()
        self.backend.old_present.return_value = True
        def interrupted(scope):
            with control_store.connect() as db:
                row=db.execute('SELECT phase,proof FROM execution_recovery_requests').fetchone()
                self.assertEqual(row['phase'],'PROVED')
                self.assertIsNotNone(row['proof'])
            raise KeyboardInterrupt()
        self.backend.remove.side_effect=interrupted
        with self.assertRaises(KeyboardInterrupt):self.apply(plan)
        pending=self.operator.inspect(plan['operation_id'])
        self.backend.stop.reset_mock()
        self.backend.remove.side_effect=lambda scope:self.backend.old_present.configure_mock(return_value=False)
        self.assertEqual(self.apply(pending)['phase'],'OPEN')
        self.backend.stop.assert_not_called()

    def test_direct_setter_and_revoked_cached_cleanup_preserve_closed_unknown(self):
        self.unknown()
        self.apply(self.plan())
        with control_store.connect() as db:
            with self.assertRaises(dm.RevisionConflict):dm.set_query_state(db,self.query_id,'FINISHED')
        with control_store.connect() as db:
            db.execute('DELETE FROM grants WHERE principal_id=?',(self.f.p.principal_id,))
        with patch.object(qs,'_transport') as transport:
            self.assert_code('DB_ACCESS_REVOKED',lambda:qs.fetch(self.f.p,self.query_id,'cached',selected_role='data_engineer'))
            qs._best_cancel(self.query_id)
            transport.assert_not_called()
        q,control,run=self.snapshot()
        self.assertEqual((q['state'],run['status']),('UNKNOWN','failed'))

    def test_minimal_proof_persisted_and_exposed_without_inspect_secrets(self):
        self.unknown()
        labels={k:'managed' for k in recovery.MANAGED_LABELS}
        scope=dict(engine_id='engine',container_id='b'*64,labels=labels,image='image',started_at='2026-01-01T00:00:00Z')
        row=dict(Id=scope['container_id'],Image='image',Config=dict(Labels=dict(labels,unrelated='SENTINEL'),Env=['SENTINEL']),
                 Mounts=['SENTINEL'],HostConfig=dict(RestartPolicy=dict(Name='no')),
                 State=dict(Running=False,Status='exited',StartedAt=scope['started_at'],FinishedAt='2026-01-02T00:00:00Z',ExitCode=0,Error='SENTINEL'))
        adapter=recovery.DockerAdapter.__new__(recovery.DockerAdapter)
        adapter.engine=Mock(return_value=dict(engine_id='engine'))
        adapter._docker=Mock(return_value=[row])
        proof=adapter.proof(scope,'2026-01-01T12:00:00Z')
        self.backend.proof.return_value=proof
        result=self.apply(self.plan())
        with control_store.connect() as db:
            saved=db.execute('SELECT proof FROM execution_recovery_requests').fetchone()[0]
        self.assertNotIn('SENTINEL',saved)
        self.assertNotIn('SENTINEL',json.dumps(result))
        self.assertNotIn('SENTINEL',json.dumps(catalog_admin.inspect_operation(self.admin,'admin',result['operation_id'])))

    def test_minimal_event_proof_ledger_and_inspect_exclude_secret_attributes(self):
        self.unknown()
        labels={k:'managed' for k in recovery.MANAGED_LABELS}
        scope=dict(engine_id='engine',container_id='b'*64,labels=labels)
        event=dict(Type='container',Action='destroy',timeNano=2_000_000_000,
                   Actor=dict(ID=scope['container_id'],Attributes=dict(labels,secret='EVENT_SENTINEL')),
                   unrelated='EVENT_SENTINEL')
        adapter=recovery.DockerAdapter.__new__(recovery.DockerAdapter)
        adapter.engine=Mock(return_value=dict(engine_id='engine'))
        adapter._docker=Mock(side_effect=QueryError('RECOVERY_BACKEND_UNAVAILABLE',409))
        adapter._run=Mock(return_value=json.dumps(event))
        self.backend.proof.return_value=adapter.proof(scope,'1970-01-01T00:00:01Z')
        result=self.apply(self.plan())
        with control_store.connect() as db:
            saved=db.execute('SELECT proof FROM execution_recovery_requests').fetchone()[0]
        self.assertNotIn('EVENT_SENTINEL',saved)
        self.assertNotIn('EVENT_SENTINEL',json.dumps(result))
        self.assertNotIn('EVENT_SENTINEL',json.dumps(self.operator.inspect(result['operation_id'])))


class StandaloneQueryRecoveryTests(unittest.TestCase):
    """Real standalone admission and auth-file reads; external adapter only is replaced."""
    def setUp(self, *, accepted=False, sql=None):
        self.f = fixtures.DatabaseJobsTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.accounts = self.f.f.fixture
        self.admin = self.accounts.principal('owner')
        with control_store.connect() as db:
            db.execute("UPDATE warehouse_config_generations SET warehouse_id='LOCAL',state='APPLIED'")
        self.f.c = replace(self.f.c, warehouse_id='LOCAL')
        self.context = qs.resolve_context(self.admin, self.f.c.database_id, self.f.c.schema_id,
            'admin', warehouse_id='LOCAL', expected_binding_revision=self.f.c.binding_revision)
        with control_store.connect() as db:
            admission._ensure(db, 'LOCAL')
            db.execute("UPDATE warehouse_controls SET applied_generation_id=? WHERE warehouse_id='LOCAL'",
                       (self.context.generation_id,))
        self.headers = self.accounts.root / 'recovery-headers.json'
        self.headers.write_text(json.dumps({**dict(self.accounts.headers('owner')), 'X-Shirokuma-Role': 'admin'}))
        self.headers.chmod(0o600)
        self.operator = recovery.QueryRecovery.__new__(recovery.QueryRecovery)
        self.operator.config = dict(auth_headers=str(self.headers), runtime_profile='/private/offline-profile')
        self.operator.seal = dict(version=1)
        self.operator.actor = self.admin.principal_id
        self.backend = Mock()
        self.scope = dict(engine_id='engine-one', container_id='a' * 64)
        self.backend.scope.return_value = self.scope
        self.backend.engine.return_value = dict(engine_id='engine-one')
        self.backend.old_present.return_value = False
        self.backend.proof.return_value = dict(kind='stopped-inspect', receipt={'Running': False})
        self.operator.adapter = Mock(return_value=self.backend)
        external = patch.object(subprocess, 'run', side_effect=AssertionError('unmocked subprocess'))
        external.start()
        self.addCleanup(external.stop)
        self.query_id = self.unknown(accepted=accepted, **({'sql': sql} if sql is not None else {}))

    def unknown(self, principal=None, context=None, *, accepted=False, sql='SELECT 17 AS preserved'):
        query_ids = []
        def transport(url, method, pin, body=None):
            query_ids.append(pin.admission.subject_id)
            if accepted and method == 'POST':
                return dict(id='original_backend', nextUri='http://127.0.0.1:1234/v1/statement/original_backend/1')
            raise OSError('lost response')
        with patch.object(qs, '_transport', side_effect=transport):
            try:
                page = qs.submit(principal or self.admin, context or self.context, sql,
                    mode='read-only', max_rows=37, request_id='standalone-request', worksheet_id='sheet')
                qs.fetch(principal or self.admin, page['id'], page['next_uri'].rsplit('/', 1)[-1],
                         selected_role=(context or self.context).selected_role)
            except QueryError as error:
                self.assertEqual(error.code, 'QUERY_BACKEND_FAILED')
        self.assertTrue(query_ids)
        self.assertEqual(len(set(query_ids)), 1)
        return query_ids[0]

    def plan(self, key='standalone-recovery'):
        return self.operator.plan(self.query_id, key)

    def apply(self, plan, **kw):
        return self.operator.apply(plan['operation_id'], plan['revision'],
            expected_intent_digest=plan['intent_digest'], **kw)

    def snapshot(self):
        with control_store.connect() as db:
            return {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                    for table in ('query_records', 'query_execution_contexts', 'db_job_runs',
                                  'db_job_steps', 'db_job_operations', 'dataops_operations', 'dataops_queries')}

    def test_terminal_delimiter_recovery_preserves_original_sql_and_graph(self):
        import warehouse_resident
        cases = [
            ('SELECT 1; -- trailing comment', 'SELECT 1 -- trailing comment'),
            ("SELECT ';' AS preserved /* ; inside */; -- trailing ; comment\n",
             "SELECT ';' AS preserved /* ; inside */ -- trailing ; comment\n"),
        ]
        for original, wire in cases:
            with self.subTest(sql=original):
                self.doCleanups()
                self.setUp(sql=original)
                before = self.snapshot()
                self.assertEqual(qs._row(self.query_id)['state'], 'UNKNOWN')
                with patch.object(warehouse_resident, 'check_sql_policy',
                                  wraps=warehouse_resident.check_sql_policy) as policy, \
                     patch.object(qs, '_transport') as transport, \
                     patch.object(qs, 'observe_query_backend') as observe:
                    plan = self.plan()
                    with control_store.connect() as db:
                        intent = db.execute('SELECT intent FROM execution_recovery_requests WHERE operation_id=?',
                                            (plan['operation_id'],)).fetchone()[0]
                    self.assertEqual(json.loads(intent)['graph']['executions'][0]['sql'], original)
                    self.assertEqual(self.snapshot(), before)
                    result = self.apply(plan)
                    self.assertEqual((result['phase'], result['execution_closure']), ('OPEN', recovery.CLOSURE))
                    self.assertEqual(self.snapshot(), before)
                    with control_store.connect() as db:
                        self.assertEqual(db.execute('SELECT sql FROM query_execution_contexts WHERE query_id=?',
                                                    (self.query_id,)).fetchone()[0], original)
                        self.assertEqual(db.execute('SELECT intent FROM execution_recovery_requests WHERE operation_id=?',
                                                    (plan['operation_id'],)).fetchone()[0], intent)
                    self.assertTrue(policy.call_args_list)
                    for call in policy.call_args_list:
                        self.assertEqual(call.args, (wire, 'read-only', self.context.selected_role))
                    transport.assert_not_called()
                    observe.assert_not_called()

    def test_invalid_sql_denied_before_backend_or_recovery_adapter(self):
        for sql in ('SELECT 1; SELECT 2', '', "SELECT 'unterminated", 'SELECT 1 /* unterminated'):
            with self.subTest(sql=sql), patch.object(qs, '_transport') as transport, \
                 patch.object(qs, 'observe_query_backend') as observe:
                with self.assertRaises(QueryError):
                    qs.submit(self.admin, self.context, sql, mode='read-only', max_rows=37,
                              request_id='invalid', worksheet_id='sheet')
                # Saved invalid SQL must also fail before any recovery observation.
                with control_store.connect() as db:
                    db.execute('UPDATE query_execution_contexts SET sql=? WHERE query_id=?',
                               (sql, self.query_id))
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    self.plan()
                self.assertEqual(self.snapshot(), before)
                self.operator.adapter.assert_not_called()
                self.assertEqual(self.backend.mock_calls, [])
                transport.assert_not_called()
                observe.assert_not_called()

    def test_null_backend_fences_only_admission_preserving_original_and_jobs(self):
        # A completed unrelated Job must remain byte-for-byte intact too.
        with patch.object(qs, '_transport', side_effect=self.f.f.transport):
            job = self.f.submit()
            self.f.finish(job)
        before, pin = self.snapshot(), qs._pins[self.query_id]
        pin_before = (pin.context, dict(pin.tokens), list(pin.cancel_urls), dict(pin.headers), pin.origin)
        with control_store.connect() as db:
            q = qs._row(self.query_id)
            self.assertEqual((q['state'], q['backend_query_id']), ('UNKNOWN', None))
            a = admission.get_for_subject(db, kind='query', subject_id=self.query_id)
            self.assertIsNone(db.execute('SELECT parent_id FROM warehouse_admissions WHERE id=?', (a.id,)).fetchone()[0])
        with patch.object(qs, '_transport') as transport, patch.object(qs, 'observe_query_backend') as observe:
            plan = self.plan()
            self.assertEqual(plan['query_id'], self.query_id)
            self.assertNotIn('run_id', plan)
            self.assertEqual(len(plan['intent_digest']), 64)
            result = self.apply(plan)
            self.assertEqual((result['phase'], result['execution_closure']), ('OPEN', recovery.CLOSURE))
            self.assertEqual(self.snapshot(), before)
            self.assertIs(qs._pins[self.query_id], pin)
            self.assertEqual((pin.context, pin.tokens, pin.cancel_urls, pin.headers, pin.origin), pin_before)
            self.apply(result)
            transport.assert_not_called()
            observe.assert_not_called()
        self.backend.stop.assert_not_called()  # already positive stopped proof
        with control_store.connect() as db:
            row = db.execute("SELECT * FROM warehouse_admissions WHERE kind='query' AND subject_id=?",
                             (self.query_id,)).fetchone()
            self.assertEqual((row['state'], row['execution_closure'], row['evidence_ref']),
                             ('UNKNOWN', recovery.CLOSURE, plan['operation_id']))
        generic = catalog_admin.inspect_operation(self.admin, 'admin', plan['operation_id'])
        self.assertEqual(generic['query_id'], self.query_id)
        self.assertNotIn('SELECT', json.dumps(generic))

    def test_graph_rejects_links_write_actor_stale_scope_and_other_work(self):
        mutations = [
            "UPDATE query_execution_contexts SET mode='sandbox-write'",
            "UPDATE query_records SET actor='alice'",
            "UPDATE warehouse_admissions SET actor='alice'",
            "UPDATE warehouse_admissions SET database_id=NULL",
            "UPDATE query_records SET generation_id=(SELECT id FROM warehouse_config_generations WHERE id!=generation_id LIMIT 1)",
            "UPDATE query_execution_contexts SET binding_revision=999",
            "UPDATE query_execution_contexts SET grant_revision=999",
            "UPDATE query_execution_contexts SET sql_schema='stale'",
            "UPDATE query_execution_contexts SET backend_username='foreign'",
            "UPDATE query_records SET state='FINISHED'",
            "UPDATE warehouse_admissions SET execution_closure='FENCED_READ_ONLY'",
            "UPDATE warehouse_admissions SET parent_id=id",
            "UPDATE warehouse_admissions SET kind='dataops'",
            "UPDATE schemas SET sql_name='changed' WHERE sql_name='raw'",
            "UPDATE catalog_bindings SET desired_revision=desired_revision+1",
            "INSERT INTO runs (id,job_name,actor,role,definition,arguments,status,created_at) VALUES ('legacy','legacy','owner','admin','{}','{}','running','now')",
            "INSERT INTO warehouse_admissions (id,warehouse_id,actor,kind,subject_id,parent_id,generation_id,state,created_at,finished_at,evidence_ref,database_id,execution_closure) SELECT 'extra',warehouse_id,actor,kind,'extra-query',id,generation_id,'FINISHED',created_at,created_at,NULL,database_id,'' FROM warehouse_admissions",
            "INSERT INTO warehouse_admissions (id,warehouse_id,actor,kind,subject_id,parent_id,generation_id,state,created_at,finished_at,evidence_ref,database_id,execution_closure) SELECT 'extra',warehouse_id,actor,'dataops',subject_id,NULL,generation_id,'FINISHED',created_at,created_at,NULL,database_id,'' FROM warehouse_admissions",
        ]
        for sql in mutations:
            with self.subTest(sql=sql), control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute(sql)
                # Commit is needed for the real query_service._owned read path.
                db.commit()
                with self.assertRaises((QueryError, PermissionError)):
                    self.plan()
            # Each subcase uses an independent authentic fixture.
            self.doCleanups()
            self.setUp()
        self.unknown()
        with self.assertRaises(QueryError):
            self.plan()

    def test_actual_foreign_actor_and_dataops_link_are_not_standalone(self):
        self.query_id = self.unknown(self.f.p, self.f.c)
        with self.assertRaises(QueryError):
            self.plan()
        self.backend.scope.assert_not_called()
        self.doCleanups()
        self.setUp()
        with patch.object(qs, '_transport', side_effect=self.f.f.transport):
            self.f.finish(self.f.submit())
        with control_store.connect() as db:
            op = db.execute('SELECT id FROM dataops_operations').fetchone()[0]
            db.execute("INSERT INTO dataops_queries VALUES (?,'foreign',?,'job_sql',NULL)", (op, self.query_id))
        with self.assertRaises(QueryError):
            self.plan()
        self.backend.scope.assert_not_called()

    def test_original_next_uri_known_backend_pin_and_sql_survive_closure(self):
        self.doCleanups()
        self.setUp(accepted=True)
        pin = qs._pins[self.query_id]
        self.assertTrue(pin.tokens)
        self.assertTrue(pin.cancel_urls)
        self.assertEqual(qs._row(self.query_id)['backend_query_id'], 'original_backend')
        before, frozen_pin = self.snapshot(), recovery._query_pin(self.query_id)
        result = self.apply(self.plan())
        self.assertEqual(result['phase'], 'OPEN')
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(recovery._query_pin(self.query_id), frozen_pin)

    def test_query_owner_and_pin_changes_hold_without_backend_actions(self):
        with qs._acquire_query_owner(self.query_id):
            with self.assertRaises(QueryError):
                self.plan()
        plan = self.plan()
        qs._pins[self.query_id].cancel_urls.append('http://127.0.0.1:1234/v1/statement/changed/1')
        with self.assertRaises(QueryError):
            self.apply(plan)
        self.backend.proof.assert_not_called()
        self.backend.stop.assert_not_called()

    def test_prior_fenced_unknown_does_not_block_another_exact_root(self):
        self.apply(self.plan())
        first = self.query_id
        self.query_id = self.unknown()
        self.assertNotEqual(self.query_id, first)
        result = self.apply(self.plan('second-root'))
        self.assertEqual(result['phase'], 'OPEN')
        with control_store.connect() as db:
            self.assertEqual(admission.blockers(db), ())
            self.assertEqual({r[0] for r in db.execute('SELECT state FROM query_records')}, {'UNKNOWN'})

    def docker_probe(self):
        probe = DockerAdapterTests()
        probe.setUp()
        self.addCleanup(probe.doCleanups)
        for resource in (probe.deployment, probe.pod):
            resource['metadata']['annotations']['shirokuma.dev/database-generation'] = self.context.generation_id
        def kube(resource, *args):
            result = probe.kube_read(resource, *args)
            if resource == 'service':
                result['metadata']['annotations']['shirokuma.dev/database-generation'] = self.context.generation_id
            return result
        probe.adapter._kube.side_effect = kube
        self.operator.adapter = Mock(return_value=probe.adapter)
        return probe

    def test_query_scope_reuses_real_docker_guard_and_rejects_containerd(self):
        probe = self.docker_probe()
        plan = self.plan()
        self.assertEqual(plan['scope']['container_id'], probe.cid)
        probe.pod['status']['containerStatuses'][0]['containerID'] = 'containerd://' + probe.cid
        with self.assertRaises(QueryError):
            self.plan('unsupported-kind')
        with control_store.connect() as db:
            self.assertEqual(admission.control(db)['state'], 'OPEN')

    def test_real_adapter_absence_expired_events_never_closes_standalone(self):
        probe = self.docker_probe()
        plan = self.plan()
        removed = False
        def stop(scope):
            nonlocal removed
            removed = True
        def docker(*args):
            if args[0] == 'inspect' and removed:
                return []
            return probe.docker_read(*args)
        with patch.object(probe.adapter, 'stop', side_effect=stop) as stopped, \
             patch.object(probe.adapter, '_docker', side_effect=docker):
            with self.assertRaises(QueryError) as error:
                self.apply(plan)
            self.assertEqual(error.exception.code, 'RECOVERY_STOP_UNPROVED')
            pending = self.operator.inspect(plan['operation_id'])
            self.assertEqual(pending['phase'], 'STOP_INTENT')
            self.apply(pending, reconcile=True)
            stopped.assert_called_once()
            with control_store.connect() as db:
                self.assertEqual(admission.control(db)['state'], 'DRAINING')
                self.assertEqual(admission.query_closure(db, self.query_id), '')

    def test_accepted_post_waiting_behind_query_fence_is_never_sent(self):
        plan = self.plan()
        script = '''
import json,sys
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
import query_service as q,control_store as s,warehouse_admission as a
from query_context import ResolvedQueryContext
with s.connect() as db:
    handle=a.get_for_subject(db,kind='query',subject_id=sys.argv[2])
pin=q._Pin(ResolvedQueryContext(**json.loads(sys.argv[3])),'http://127.0.0.1:1234',{},None,admission=handle)
with a.accepted(handle,allow_unknown=True):
    print('ACCEPTED',flush=True)
    with patch('warehouse_resident.warehouse_activity',a.lifecycle_gate),patch('http.client.HTTPConnection') as http:
        try:q._transport(pin.origin+'/v1/statement','POST',pin,b'SELECT 1')
        except Exception as e:print(getattr(e,'code',type(e).__name__),flush=True)
        print('HTTP='+str(http.call_count),flush=True)
'''
        with admission.lifecycle_gate(exclusive=True):
            child = subprocess.Popen([sys.executable, '-c', script, str(Path(recovery.__file__).parent),
                self.query_id, json.dumps(self.context.public_metadata())],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.addCleanup(lambda: child.poll() is None and child.kill())
            self.assertTrue(select.select([child.stdout], [], [], 5)[0])
            self.assertEqual(child.stdout.readline().strip(), 'ACCEPTED')
            self.apply(plan)
        stdout, stderr = child.communicate(timeout=10)
        self.assertEqual(child.returncode, 0, stderr)
        self.assertIn('EXECUTION_CLOSED', stdout)
        self.assertIn('HTTP=0', stdout)

    def test_review_digest_revision_competing_plan_and_route_are_exact(self):
        plan = self.plan()
        self.assertEqual(self.plan(), plan)
        with self.assertRaises(QueryError):
            self.operator.apply(plan['operation_id'], plan['revision'], expected_intent_digest='0' * 64)
        with self.assertRaises(QueryError):
            self.operator.apply(plan['operation_id'], 999, expected_intent_digest=plan['intent_digest'])
        with self.assertRaises(QueryError):
            self.operator.apply(plan['operation_id'], plan['revision'])
        second = self.plan('competing')
        self.apply(plan)
        with self.assertRaises(QueryError):
            self.apply(second)
        job_operator = recovery.Recovery.__new__(recovery.Recovery)
        for name in ('config', 'seal', 'actor'):
            setattr(job_operator, name, getattr(self.operator, name))
        with self.assertRaises(QueryError):
            job_operator.inspect(plan['operation_id'])

    def test_real_account_loss_before_apply_and_resume_holds_drain(self):
        for phase in ('PLANNED', 'STOP_INTENT', 'CLOSED'):
            for loss in ('role', 'removed'):
                with self.subTest(phase=phase, loss=loss):
                    plan = self.plan()
                    if phase == 'STOP_INTENT':
                        self.backend.proof.return_value = None
                        with self.assertRaises(QueryError):
                            self.apply(plan)
                    elif phase == 'CLOSED':
                        self.backend.healthy.side_effect = KeyboardInterrupt()
                        with self.assertRaises(KeyboardInterrupt):
                            self.apply(plan)
                    pending = self.operator.inspect(plan['operation_id'])
                    if loss == 'role':
                        self.accounts.users['owner']['role'] = 'data_engineer'
                    else:
                        del self.accounts.users['owner']
                    self.accounts.save()
                    with self.assertRaises((QueryError, PermissionError)):
                        self.apply(pending, reconcile=True)
                    with control_store.connect() as db:
                        self.assertEqual(admission.control(db)['state'], 'OPEN' if phase == 'PLANNED' else 'DRAINING')
                self.doCleanups()
                self.setUp()

    def test_missing_proof_durable_stop_intent_no_reconcile_replay_then_positive_resume(self):
        before = self.snapshot()
        plan = self.plan()
        self.backend.proof.return_value = None
        with self.assertRaises(QueryError):
            self.apply(plan)
        pending = self.operator.inspect(plan['operation_id'])
        self.assertEqual(pending['phase'], 'STOP_INTENT')
        self.backend.stop.assert_called_once()
        self.apply(pending, reconcile=True)
        self.backend.stop.assert_called_once()
        self.backend.engine.return_value = dict(engine_id='changed')
        with self.assertRaises(QueryError):
            self.apply(pending, reconcile=True)
        self.assertEqual(self.snapshot(), before)
        self.backend.engine.return_value = dict(engine_id='engine-one')
        self.backend.proof.return_value = dict(kind='exact-event', receipt=dict(action='die'))
        result = self.apply(pending, reconcile=True)
        self.assertEqual(result['phase'], 'OPEN')
        self.backend.stop.assert_called_once()
        self.assertEqual(self.snapshot(), before)

    def test_closed_health_resume_and_current_permission_recheck(self):
        plan = self.plan()
        self.backend.healthy.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.apply(plan)
        closed = self.operator.inspect(plan['operation_id'])
        self.assertEqual(closed['phase'], 'CLOSED')
        with control_store.connect() as db:
            db.execute('UPDATE schemas SET sql_name=\'changed\' WHERE id=?', (self.context.schema_id,))
        self.backend.healthy.side_effect = None
        with self.assertRaises(QueryError):
            self.apply(closed, reconcile=True)
        self.backend.stop.assert_not_called()

    def test_cli_query_shapes_require_review_digest_only_for_query(self):
        for name, args in [('plan', ['--query-id', self.query_id, '--idempotency-key', 'fresh']),
                           ('inspect', ['--operation-id', 'op']),
                           ('apply', ['--operation-id', 'op', '--expected-revision', '1', '--expected-intent-digest', 'a' * 64]),
                           ('reconcile', ['--operation-id', 'op', '--expected-revision', '2', '--expected-intent-digest', 'b' * 64])]:
            with patch.object(recovery, 'run', return_value={}) as run, patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(catalog_admin.main(['recover-query-' + name, '--config', '/private/offline'] + args), 0)
                self.assertEqual(run.call_args.args[0].command, 'recover-query-' + name)

    def test_private_query_config_selects_profile_auth_inputs_without_runtime_setup(self):
        config = self.accounts.root / 'query-recovery.json'
        config.write_text(json.dumps(dict(version=1, auth_headers=str(self.headers),
            runtime_profile='/private/offline-profile', docker_context='colima-mac-studio-solo',
            kube_context='colima-mac-studio-solo')))
        config.chmod(0o600)
        profile = SimpleNamespace(state_dir=os.environ['SHIROKUMA_STATE_DIR'],
            auth_file=str(self.accounts.accounts), trino_auth_file='/private/offline-backend',
            trino_ca_file='/private/offline-ca')
        with patch.object(recovery.runtime_profile, 'load', return_value=profile), \
             patch.object(recovery.runtime_profile, 'private_binding', return_value=self.operator.seal), \
             patch.object(recovery.runtime_profile, 'configure_process', side_effect=AssertionError('no runtime setup')):
            operator = recovery.QueryRecovery(config)
        self.assertEqual(operator.actor, self.admin.principal_id)
        self.assertEqual(os.environ['SHIROKUMA_TRINO_AUTH_MODE'], 'password')
        self.assertEqual(os.environ['SHIROKUMA_TRINO_AUTH_FILE'], profile.trino_auth_file)
        self.assertEqual(os.environ['SHIROKUMA_TRINO_CA_FILE'], profile.trino_ca_file)


class MigrationTests(unittest.TestCase):
    def test_v12_backup_upgrade_preserves_rows_and_defaults_closure(self):
        from test_control_store import drop_recovery_schema
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SHIROKUMA_STATE_DIR=directory):
            with control_store.connect() as db:
                db.execute("INSERT INTO jobs VALUES ('kept','owner','{}','past','past')")
                db.execute("INSERT INTO warehouse_controls VALUES ('LOCAL','OPEN',NULL,1,NULL)")
                db.execute("""INSERT INTO warehouse_admissions
                    (id,warehouse_id,actor,kind,subject_id,state,created_at)
                    VALUES ('kept','LOCAL','owner','query','old','UNKNOWN','past')""")
                drop_recovery_schema(db)
                db.execute('PRAGMA user_version=12')
            backup = sqlite3.connect(':memory:')
            with sqlite3.connect(Path(directory) / 'jobs.sqlite3') as db:
                db.backup(backup)
            with control_store.connect() as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], control_store.VERSION)
                self.assertEqual(tuple(db.execute('SELECT state,execution_closure FROM warehouse_admissions').fetchone()), ('UNKNOWN',''))
                self.assertEqual(db.execute('SELECT * FROM jobs').fetchall()[0][:], backup.execute('SELECT * FROM jobs').fetchall()[0])
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("UPDATE warehouse_admissions SET execution_closure='FINISHED'")
            backup.close()


class HistoricalQueryRecoveryTests(unittest.TestCase):
    setUp = StandaloneQueryRecoveryTests.setUp
    apply = StandaloneQueryRecoveryTests.apply
    snapshot = StandaloneQueryRecoveryTests.snapshot

    def test_b_only_selector_is_no_longer_sufficient(self):
        path = self.accounts.root / 'incomplete-selector.json'
        path.write_text('{}')
        path.chmod(0o600)
        self.backend.historical.return_value = dict(historical_scope={}, replacement_scope={})
        with self.assertRaises(QueryError):
            self.operator.plan(self.query_id, 'incomplete', historical_pod_snapshot=path,
                               historical_container_id='b' * 64)

    def unknown(self, principal=None, context=None, *, accepted=False):
        index = len(qs._pins)
        self.old_paths = [self.accounts.root / ('pod-' + role + '-' + str(index) + '.json') for role in ('A', 'B')]
        for path in self.old_paths:
            path.write_text('{}')
            path.chmod(0o600)
        self.original_intent = self.accounts.root / ('original-' + str(index) + '.intent.json')
        self.original_result = self.accounts.root / ('original-' + str(index) + '.result.json')
        c = context or self.context
        self.original_intent.write_text(json.dumps(dict(actor=(principal or self.admin).principal_id, calls=1,
            at=datetime.now(timezone.utc).isoformat(), argv=['rtk', 'proxy', '/offline/shirokuma', 'warehouse', 'query', 'LOCAL',
                '--catalog', c.sql_catalog, '--schema', c.sql_schema, '--mode', 'read-only', '--max-rows', '37',
                '--no-auto-wake', '--sql', 'SELECT 17 AS preserved'])))
        self.original_intent.chmod(0o600)
        result = StandaloneQueryRecoveryTests.unknown(self, principal, context, accepted=accepted)
        self.original_result.write_text(json.dumps(dict(exit=1, stdout='',
            stderr='resident warehouse failed: Authenticated SQL endpoint request failed\n\n')))
        self.original_result.chmod(0o600)
        return result

    def historical(self, key='historical'):
        self.selector = self.old_paths[1]
        selection = dict(members=[recovery._snapshot(p, cid * 64) for p, cid in zip(self.old_paths, ('c', 'b'))],
            producer=dict(intent=recovery._historical_file(self.original_intent)[0],
                          result=recovery._historical_file(self.original_result)[0]))
        with control_store.connect() as db:
            graph = self.operator.graph(db, self.query_id)
        producer = recovery._producer(graph, selection)
        lower, upper = producer['window_start_ns'], producer['window_end_ns']
        digest = 'sha256:' + 'd' * 64
        cm_facts = [dict(name='trino-local-config-589cfhfttm',current_uid='cm-uid',generator='trino-local-config',
            name_hash='589cfhfttm',current_data_digest='d' * 64,current_created_at='2026-01-01T00:00:00Z')]
        members = []
        for index, (cid, selected) in enumerate(zip(('c', 'b'), selection['members'])):
            labels = dict(zip(recovery.MANAGED_LABELS, ('pod-' + cid, 'shirokuma-dev', 'trino-' + cid, 'trino')))
            scope = dict(container_id=cid * 64, pod_uid='pod-' + cid, replica_set_uid='rs-' + cid, deployment_uid='deployment', labels=labels,
                image=digest, pinned_image='docker.io/trinodb/trino@' + digest, configmaps=cm_facts,
                namespace='shirokuma-dev',generation_id=graph['generation']['id'],pod_created_at='2026-01-02T00:00:00Z',
                role='possible-predecessor' if index == 0 else 'historical-original')
            saved_ns = selected['stat']['mtime_ns'] - 1_000_000
            times = ([saved_ns-1000000,saved_ns,saved_ns+10000,saved_ns+20000,saved_ns+30000] if index == 0 else
                     [lower-50000,lower-40000,upper+1,upper+2,upper+3])
            events = [dict(action=action,time_nano=t,container_id=cid * 64,labels=labels,image=digest,
                           exit_code='143' if action == 'die' else None)
                      for action,t in zip(('create','start','stop','die','destroy'),times)]
            members.append(dict(scope=scope,snapshot=selected,events=events,
                events_digest=recovery.hashlib.sha256(recovery._json(events).encode()).hexdigest()))
            if index == 0:
                members[-1]['saved_start'] = dict(kind='pod-running-startedAt',snapshot_sha256=selected['sha256'],
                    container_id=scope['container_id'],pod_uid=scope['pod_uid'],
                    started_at=datetime.fromtimestamp(saved_ns / 1e9,timezone.utc).isoformat())
        self.observation = dict(kind='historical-exact-events', version=2, members=members,
            engine_id='engine-one', historical_scope=dict(members[1]['scope'], engine_id='engine-one'),
            replacement_scope=dict(self.scope, role='replacement', pod_uid='pod-a', deployment_uid='deployment',
                configmaps=cm_facts,namespace='shirokuma-dev',generation_id=graph['generation']['id'],
                labels=dict(zip(recovery.MANAGED_LABELS,('pod-a','shirokuma-dev','trino-a','trino'))),
                image=digest,pinned_image='docker.io/trinodb/trino@' + digest,replica_set_uid='rs-a',pod_created_at='2026-01-03T00:00:00Z', started_at=(datetime.fromtimestamp(upper / 1e9,timezone.utc)+timedelta(milliseconds=1)).isoformat()),
            producer=producer)
        self.backend.historical.return_value = self.observation
        self.current_observation = dict(engine_id=self.observation['engine_id'],
            old_container_ids_absent=[m['scope']['container_id'] for m in members],
            replacement_scope=self.observation['replacement_scope'],producer=producer)
        self.backend.consume_historical.return_value = self.current_observation
        self.plan_kwargs = dict(historical_pod_snapshot=self.selector,
            historical_container_id='b' * 64, historical_predecessor_pod_snapshot=self.old_paths[0],
            historical_predecessor_container_id='c' * 64, historical_query_intent=self.original_intent,
            historical_query_result=self.original_result)
        return self.operator.plan(self.query_id, key, **self.plan_kwargs)

    def test_historical_observation_only_exact_closure_and_replay(self):
        before = self.snapshot()
        plan = self.historical()
        self.assertEqual(plan['mode'], 'HISTORICAL_STOP_FENCE')
        with patch.object(qs, '_transport', side_effect=AssertionError('no SQL')):
            result = self.apply(plan)
            self.assertEqual(result['phase'], 'OPEN')
            self.assertEqual(self.apply(result)['phase'], 'OPEN')
        self.assertEqual(before, self.snapshot())
        for method in ('stop', 'remove', 'proof', 'old_present', 'healthy'):
            getattr(self.backend, method).assert_not_called()
        with control_store.connect() as db:
            rows = db.execute("SELECT * FROM warehouse_admissions WHERE execution_closure!=''").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual((rows[0]['subject_id'], rows[0]['evidence_ref']), (self.query_id, plan['operation_id']))
        import database_retirement
        preview = database_retirement.preview(self.admin, 'admin', self.context.database_id)
        self.assertFalse(preview['blockers'])  # Exact historical closure removes active retirement blockers.

    def test_historical_drift_leaves_durable_observe_intent(self):
        plan = self.historical()
        self.backend.consume_historical.return_value = dict(self.current_observation, engine_id='foreign')
        with self.assertRaises(QueryError):
            self.apply(plan)
        pending = self.operator.inspect(plan['operation_id'])
        self.assertEqual(pending['phase'], 'PLANNED')
        self.assertEqual(pending['proof']['kind'], 'historical-verification-intent')
        with control_store.connect() as db:
            self.assertEqual(admission.control(db)['state'], 'DRAINING')
        self.backend.consume_historical.return_value = self.current_observation
        self.assertEqual(self.apply(pending, reconcile=True)['phase'], 'OPEN')

    def test_historical_same_key_snapshot_bytes_are_immutable(self):
        plan = self.historical()
        self.assertEqual(self.historical()['intent_digest'], plan['intent_digest'])
        self.selector.write_text('{"changed":true}')
        with self.assertRaises(QueryError):
            self.historical()
        with self.assertRaises(QueryError):
            self.apply(plan)

    def test_historical_current_auth_digest_revision_and_graph_cas(self):
        plan = self.historical()
        with self.assertRaises(QueryError):
            self.operator.apply(plan['operation_id'], plan['revision'], expected_intent_digest='0' * 64)
        with self.assertRaises(QueryError):
            self.operator.apply(plan['operation_id'], 999, expected_intent_digest=plan['intent_digest'])
        # Current auth-file/account read must apply even after a plan exists.
        self.accounts.users['owner']['role'] = 'data_engineer'
        self.accounts.save()
        with self.assertRaises((QueryError, PermissionError)):
            self.apply(plan)
        self.accounts.users['owner']['role'] = 'admin'
        self.accounts.save()
        original = self.current_observation
        def race(*args):
            with control_store.connect() as db:
                db.execute('UPDATE execution_recovery_requests SET revision=revision+1 WHERE operation_id=?',
                           (plan['operation_id'],))
            return original
        self.backend.consume_historical.side_effect = race
        with self.assertRaises(QueryError):
            self.apply(plan)
        with control_store.connect() as db:
            self.assertEqual(admission.query_closure(db, self.query_id), '')
            self.assertEqual(admission.control(db)['state'], 'DRAINING')

    def test_historical_other_unfinished_and_grant_drift_block_apply(self):
        plan = self.historical()
        with control_store.connect() as db:
            db.execute('UPDATE query_execution_contexts SET grant_revision=999 WHERE query_id=?',
                       (self.query_id,))
        with self.assertRaises(QueryError):
            self.apply(plan)
        with control_store.connect() as db:
            db.execute('UPDATE query_execution_contexts SET grant_revision=NULL WHERE query_id=?',
                       (self.query_id,))
        self.unknown()
        with self.assertRaises(QueryError):
            self.apply(plan)
        with control_store.connect() as db:
            self.assertEqual(admission.query_closure(db, self.query_id), '')

    def test_historical_three_prior_closed_records_and_original_pin_survive(self):
        for index in range(3):
            self.apply(self.operator.plan(self.query_id, 'prior-' + str(index)))
            self.query_id = self.unknown()
        with control_store.connect() as db:
            old = [dict(r) for r in db.execute("SELECT * FROM warehouse_admissions WHERE execution_closure!=''")]
        before, pin = self.snapshot(), recovery._query_pin(self.query_id)
        plan = self.historical()
        self.assertEqual(self.apply(plan)['phase'], 'OPEN')
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(recovery._query_pin(self.query_id), pin)
        with control_store.connect() as db:
            for row in old:
                self.assertEqual(dict(db.execute('SELECT * FROM warehouse_admissions WHERE id=?', (row['id'],)).fetchone()), row)

    def test_historical_interruption_after_close_requires_fresh_current_verification(self):
        plan = self.historical()
        self.backend.consume_historical.side_effect = [self.current_observation, KeyboardInterrupt()]
        with self.assertRaises(KeyboardInterrupt):
            self.apply(plan)
        pending = self.operator.inspect(plan['operation_id'])
        self.assertEqual(pending['phase'], 'CLOSED')
        self.backend.consume_historical.side_effect = QueryError('RECOVERY_STOP_UNPROVED', 409)
        with self.assertRaises(QueryError):
            self.apply(pending, reconcile=True)
        with control_store.connect() as db:
            self.assertEqual(admission.control(db)['state'], 'DRAINING')
        self.backend.consume_historical.side_effect = None
        self.assertEqual(self.apply(pending, reconcile=True)['phase'], 'OPEN')

    def test_historical_profile_seal_and_actor_loss_during_reads(self):
        plan = self.historical()
        mocked_adapter = self.operator.adapter
        self.operator.adapter = lambda graph: recovery.Recovery.adapter(self.operator, graph)
        with patch.object(recovery.runtime_profile, 'private_binding', return_value=dict(changed=True)):
            with self.assertRaises(QueryError):
                self.apply(plan)
        pending = self.operator.inspect(plan['operation_id'])
        self.assertEqual(pending['proof']['kind'], 'historical-verification-intent')
        self.operator.adapter = mocked_adapter
        def revoke(*args):
            self.accounts.users['owner']['platform_admin'] = False
            self.accounts.save()
            return self.current_observation
        self.backend.consume_historical.side_effect = revoke
        with self.assertRaises((QueryError, PermissionError)):
            self.apply(pending, reconcile=True)
        with control_store.connect() as db:
            self.assertEqual(admission.query_closure(db, self.query_id), '')
            self.assertEqual(admission.control(db)['state'], 'DRAINING')

    def test_historical_cli_flags_select_private_branch(self):
        plan = self.historical()
        with patch.object(recovery, 'run', return_value=plan) as run, patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(catalog_admin.main(['recover-query-plan', '--config', '/private/offline',
                '--query-id', self.query_id, '--idempotency-key', 'selected',
                '--historical-pod-snapshot', str(self.selector), '--historical-container-id', 'b' * 64,
                '--historical-predecessor-pod-snapshot', str(self.old_paths[0]),
                '--historical-predecessor-container-id', 'c' * 64,
                '--historical-query-intent', str(self.original_intent),
                '--historical-query-result', str(self.original_result)]), 0)
            args = run.call_args.args[0]
        self.assertEqual(args.historical_pod_snapshot, self.selector)
        self.assertEqual(args.historical_container_id, 'b' * 64)
        self.assertEqual(args.historical_predecessor_pod_snapshot,self.old_paths[0])
        self.assertEqual(args.historical_query_intent,self.original_intent)
        self.assertEqual(args.historical_query_result,self.original_result)

    def test_completed_claim_survives_retention_profile_and_generation_changes(self):
        plan = self.historical()
        completed = self.apply(plan)
        for path in self.old_paths + [self.original_intent, self.original_result]:
            path.unlink()
        self.backend.consume_historical.side_effect = AssertionError('completed closure must not read events')
        self.operator.adapter = Mock(side_effect=AssertionError('completed closure must not read runtime'))
        self.operator.seal = dict(new_profile=True)
        with control_store.connect() as db:
            dm.record_generation(db,dm.WarehouseConfigGeneration('later-head','LOCAL','later-op',(),(),(),'APPLIED'))
            db.execute("UPDATE warehouse_controls SET applied_generation_id='later-head'")
            db.execute("UPDATE catalog_bindings SET applied_generation_id='later-head'")
        with patch.object(qs, '_transport', side_effect=AssertionError('SQL0')):
            self.assertEqual(self.apply(completed), completed)
        self.operator.adapter.assert_not_called()


    def test_completed_saved_proof_and_exact_admission_claim_remain_required(self):
        completed = self.apply(self.historical())
        with control_store.connect() as db:
            db.execute("UPDATE execution_recovery_requests SET proof='{}' WHERE operation_id=?", (completed['operation_id'],))
        self.operator.adapter = Mock(side_effect=AssertionError('no runtime replay'))
        with self.assertRaises(QueryError):
            self.apply(completed)
        self.operator.adapter.assert_not_called()

    def test_original_sql_actor_and_late_file_are_rejected_before_reads(self):
        plan = self.historical()
        original = self.original_intent.read_bytes()
        data = json.loads(original)
        data['argv'][-1] = 'SELECT 999'
        self.original_intent.write_text(json.dumps(data))
        with self.assertRaises(QueryError):
            self.operator.plan(self.query_id, 'changed-sql', **self.plan_kwargs)
        with self.assertRaises(QueryError):
            self.apply(plan)
        self.backend.historical.reset_mock()
        self.original_intent.write_bytes(original)
        with self.assertRaises(QueryError):
            self.operator.plan(self.query_id, 'backfilled', **self.plan_kwargs)
        self.backend.historical.assert_not_called()


    def test_current_replacement_drift_at_apply_requires_same_operation_observe(self):
        plan = self.historical()
        changed = copy.deepcopy(self.current_observation)
        changed['replacement_scope']['container_id'] = 'f' * 64
        self.backend.consume_historical.return_value = changed
        with self.assertRaises(QueryError):
            self.apply(plan)
        pending = self.operator.inspect(plan['operation_id'])
        with control_store.connect() as db:
            self.assertEqual(admission.query_closure(db,self.query_id),'')
            self.assertEqual(admission.control(db)['state'],'DRAINING')
        self.backend.consume_historical.return_value = self.current_observation
        resumed = self.apply(pending,reconcile=True)
        self.assertEqual((resumed['operation_id'],resumed['phase']),(plan['operation_id'],'OPEN'))


    def test_shared_completed_predicate_open_closed_is_read_only(self):
        completed = self.apply(self.historical())
        with control_store.connect() as db:
            def check():
                row = db.execute('SELECT * FROM execution_recovery_requests WHERE operation_id=?',
                                 (completed['operation_id'],)).fetchone()
                before = db.total_changes
                result = recovery.completed_historical_query(db,row,json.loads(row['intent']),json.loads(row['proof']))
                self.assertEqual(db.total_changes,before)
                self.assertEqual(result,db.execute("SELECT id FROM warehouse_admissions WHERE kind='query' AND subject_id=?",
                                                  (self.query_id,)).fetchone()[0])
            check()
            row = dict(db.execute('SELECT * FROM execution_recovery_requests WHERE operation_id=?',
                                  (completed['operation_id'],)).fetchone())
            intent = json.loads(row['intent'])
            fresh = copy.deepcopy(intent['observation'])
            fresh['members'][0]['events'] = fresh['members'][0]['events'][-1:]
            fresh['members'][0]['events_digest'] = recovery.hashlib.sha256(recovery._json(fresh['members'][0]['events']).encode()).hexdigest()
            for legacy in (intent['observation'],recovery._historical_proof(row,intent,fresh)):
                db.execute('UPDATE execution_recovery_requests SET proof=? WHERE operation_id=?',
                           (recovery._json(legacy),completed['operation_id']))
                check()
            db.execute('UPDATE execution_recovery_requests SET proof=? WHERE operation_id=?',
                       (row['proof'],completed['operation_id']))
            db.execute("UPDATE execution_recovery_requests SET phase='CLOSED' WHERE operation_id=?",(completed['operation_id'],))
            db.execute("UPDATE operations SET state='CLOSED' WHERE id=?",(completed['operation_id'],))
            check()

    def test_shared_completed_predicate_rejects_actor_type_route_and_old_proof(self):
        completed = self.apply(self.historical())
        mutations = [
            ("UPDATE operations SET actor='foreign' WHERE id=?",()),
            ("UPDATE operations SET action='JOB_EXECUTION_RECOVERY' WHERE id=?",()),
            ("UPDATE execution_recovery_requests SET proof='{}' WHERE operation_id=?",()),
            ("UPDATE warehouse_admissions SET evidence_ref='foreign' WHERE subject_id=?",(self.query_id,))]
        with control_store.connect() as db:
            actual = db.execute('SELECT * FROM execution_recovery_requests WHERE operation_id=?',
                                (completed['operation_id'],)).fetchone()
            for field,value in [('actor','foreign'),('route','recover-job')]:
                forged = dict(actual);forged[field] = value
                with self.assertRaises(QueryError):
                    recovery.completed_historical_query(db,forged,json.loads(actual['intent']),json.loads(actual['proof']))
            for sql, parameters in mutations:
                with self.subTest(sql=sql):
                    db.execute('SAVEPOINT invalid_claim')
                    db.execute(sql,parameters or (completed['operation_id'],))
                    row = db.execute('SELECT * FROM execution_recovery_requests WHERE operation_id=?',
                                     (completed['operation_id'],)).fetchone()
                    with self.assertRaises(QueryError):
                        recovery.completed_historical_query(db,row,json.loads(row['intent']),json.loads(row['proof']))
                    db.execute('ROLLBACK TO invalid_claim')
                    db.execute('RELEASE invalid_claim')

    def test_shared_completed_predicate_rejects_duplicate_actual_closed_claim(self):
        plan = self.historical('first')
        second = self.historical('second')
        completed = self.apply(plan)
        with control_store.connect() as db:
            row = db.execute('SELECT * FROM execution_recovery_requests WHERE operation_id=?',
                             (completed['operation_id'],)).fetchone()
            db.execute("UPDATE execution_recovery_requests SET phase='CLOSED',proof=? WHERE operation_id=?",
                       (row['proof'],second['operation_id']))
            db.execute("UPDATE operations SET state='CLOSED' WHERE id=?",(second['operation_id'],))
            with self.assertRaises(QueryError) as caught:
                recovery.completed_historical_query(db,row,json.loads(row['intent']),json.loads(row['proof']))
            self.assertEqual(caught.exception.code,'RECOVERY_CLAIM_AMBIGUOUS')

    def test_shared_completed_predicate_requires_true_plan_after_terminal(self):
        completed = self.apply(self.historical())
        with control_store.connect() as db:
            row = db.execute('SELECT * FROM execution_recovery_requests WHERE operation_id=?',
                             (completed['operation_id'],)).fetchone()
            intent = json.loads(row['intent'])
            # Rewriting planned_at cannot turn an event after the real plan into a saved closure fact.
            intent['planned_at'] = intent['graph']['queries'][0]['created_at']
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('UPDATE execution_recovery_requests SET intent=? WHERE operation_id=?',
                           (recovery._json(intent),completed['operation_id']))
            with self.assertRaises(QueryError):
                recovery._historical_claim(intent)
            with self.assertRaises(QueryError):
                recovery.completed_historical_query(db,row,intent,json.loads(row['proof']))


    def test_official_full_plan_consumed_with_no_event_history_or_replay(self):
        self.apply(self.operator.plan(self.query_id,'prior-native-query'))
        probe = HistoricalAdapterTests()
        probe.setUp()
        self.addCleanup(probe.doCleanups)
        generation = self.context.generation_id
        for pod in probe.pods.values():
            pod['metadata']['annotations']['shirokuma.dev/database-generation'] = generation
        for rs in probe.rs.values():
            rs['spec']['template']['metadata']['annotations']['shirokuma.dev/database-generation'] = generation
        probe.deployment['metadata']['annotations']['shirokuma.dev/database-generation'] = generation
        probe.adapter.expected['metadata']['annotations']['shirokuma.dev/database-generation'] = generation
        for index,role in enumerate(('A','B')):
            path = Path(probe.selector['members'][index]['path'])
            probe.private(path,dict(kind='PodList',items=[probe.pods[role]]))
            probe.selector['members'][index] = recovery._snapshot(path,probe.cid[role])
        with patch.object(admission,'_now',side_effect=lambda:datetime.now(timezone.utc).isoformat()):
            self.query_id = self.unknown()
        with control_store.connect() as db:
            frozen = self.operator.graph(db,self.query_id)
        probe.graph = frozen
        probe.selector['producer'] = dict(intent=recovery._historical_file(self.original_intent)[0],
                                          result=recovery._historical_file(self.original_result)[0])
        original_kube = probe.kube
        def qualified_kube(resource,*args):
            value = original_kube(resource,*args)
            if resource == 'service':
                value['metadata']['annotations']['shirokuma.dev/database-generation'] = generation
            elif resource == 'services':
                for service in value['items']:
                    service['metadata']['annotations']['shirokuma.dev/database-generation'] = generation
            return value
        probe.adapter._kube.side_effect = qualified_kube
        self.operator.adapter = Mock(return_value=probe.adapter)
        flags = dict(historical_predecessor_pod_snapshot=Path(probe.selector['members'][0]['path']),
            historical_predecessor_container_id=probe.cid['A'],
            historical_pod_snapshot=Path(probe.selector['members'][1]['path']),historical_container_id=probe.cid['B'],
            historical_query_intent=self.original_intent,historical_query_result=self.original_result)
        before = self.snapshot()
        with patch.object(qs,'_transport',side_effect=AssertionError('SQL0')):
            plan = self.operator.plan(self.query_id,'original-full-plan',**flags)
            competing = self.operator.plan(self.query_id,'competing-plan',**flags)
            with control_store.connect() as db:
                immutable = dict(db.execute('SELECT * FROM execution_recovery_requests WHERE operation_id=?',
                                           (plan['operation_id'],)).fetchone())
            self.assertEqual(len(json.loads(immutable['intent'])['observation']['members'][0]['events']),5)
            original_command = probe.command
            inventory_extra = []
            def no_events(program,*args,**kwargs):
                if program == 'docker' and args[0] == 'events':
                    raise AssertionError('APPLY must consume official PLAN facts without events')
                if program == 'docker' and args[:2] == ('ps','-a'):
                    return '\n'.join([row['Id'] for row in probe.running] + inventory_extra)
                return original_command(program,*args,**kwargs)
            probe.adapter._run.side_effect = no_events
            probe.events = {}
            # Create the same durable r2 verification intent via an inventory contradiction.
            inventory_extra.append(probe.cid['A'])
            with self.assertRaises(QueryError): self.apply(plan)
            inventory_extra.clear()
            pending = self.operator.inspect(plan['operation_id'])
            self.assertEqual((pending['phase'],pending['revision']),('PLANNED',2))
            self.assertEqual(pending['proof']['kind'],'historical-verification-intent')
            original_started = probe.running[0]['State']['StartedAt']
            original_adapter = self.operator.adapter
            for failure in ('old-A-stopped','old-B-stopped','daemon','replacement','profile','role','grant','digest','reference','raw-stop','context','claim'):
                with self.subTest(failure=failure):
                    reference = dict(immutable)
                    with control_store.connect() as db:
                        reference = dict(db.execute('SELECT * FROM execution_recovery_requests WHERE operation_id=?',
                                                   (plan['operation_id'],)).fetchone())
                    if failure.startswith('old-'):
                        inventory_extra.append(probe.cid['A' if failure == 'old-A-stopped' else 'B'])
                    elif failure == 'daemon': probe.engine_id = 'different-engine'
                    elif failure == 'replacement':
                        probe.running[0]['State']['StartedAt'] = (recovery._time(original_started)+timedelta(seconds=1)).isoformat()
                    elif failure == 'profile':
                        self.operator.adapter = lambda graph: recovery.Recovery.adapter(self.operator,graph)
                    elif failure == 'role':
                        self.accounts.users['owner']['role'] = 'data_engineer';self.accounts.save()
                    elif failure == 'grant':
                        with control_store.connect() as db:
                            db.execute('UPDATE query_execution_contexts SET grant_revision=999 WHERE query_id=?',(self.query_id,))
                    elif failure == 'claim':
                        with control_store.connect() as db:
                            db.execute("UPDATE execution_recovery_requests SET phase='CLOSED' WHERE operation_id=?",(competing['operation_id'],))
                            db.execute("UPDATE operations SET state='CLOSED' WHERE id=?",(competing['operation_id'],))
                    try:
                        if failure == 'digest':
                            with self.assertRaises(QueryError):
                                self.operator.apply(pending['operation_id'],pending['revision'],expected_intent_digest='0'*64,reconcile=True)
                        elif failure in ('reference','raw-stop','context'):
                            if failure == 'reference': reference['intent'] = '{}'
                            elif failure == 'raw-stop': reference['proof'] = recovery._json(dict(stopped=True))
                            graph = dict(frozen,query_ids=['foreign']) if failure == 'context' else frozen
                            with self.assertRaises(QueryError): probe.adapter.consume_historical(graph,probe.selector,reference)
                        elif failure == 'profile':
                            with patch.object(recovery.runtime_profile,'private_binding',return_value=dict(changed=True)):
                                with self.assertRaises(QueryError): self.apply(pending,reconcile=True)
                        else:
                            with self.assertRaises((QueryError,PermissionError)): self.apply(pending,reconcile=True)
                    finally:
                        inventory_extra.clear();probe.engine_id = 'same-engine'
                        probe.running[0]['State']['StartedAt'] = original_started
                        self.operator.adapter = original_adapter
                        if failure == 'role':
                            self.accounts.users['owner']['role'] = 'admin';self.accounts.save()
                        elif failure == 'grant':
                            with control_store.connect() as db:
                                db.execute('UPDATE query_execution_contexts SET grant_revision=NULL WHERE query_id=?',(self.query_id,))
                        elif failure == 'claim':
                            with control_store.connect() as db:
                                db.execute("UPDATE execution_recovery_requests SET phase='PLANNED' WHERE operation_id=?",(competing['operation_id'],))
                                db.execute("UPDATE operations SET state='PLANNED' WHERE id=?",(competing['operation_id'],))
                    with control_store.connect() as db:
                        self.assertEqual(admission.control(db)['state'],'DRAINING')
                        self.assertEqual(admission.query_closure(db,self.query_id),'')
            # Ordinary PLAN acquisition still requires actual positive daemon events.
            with self.assertRaises(AssertionError): probe.adapter.historical(frozen,probe.selector)
            completed = self.apply(pending,reconcile=True)
            self.assertEqual(completed['phase'],'OPEN')
            proof = completed['proof']
            self.assertEqual(proof['version'],4)
            self.assertEqual(proof['terminal_basis'],'official PLAN-time daemon observations consumed')
            self.assertFalse(proof['event_history_reacquired'])
            self.assertEqual(proof['reference']['intent_digest'],plan['intent_digest'])
            with control_store.connect() as db:
                saved = dict(db.execute('SELECT * FROM execution_recovery_requests WHERE operation_id=?',
                                       (plan['operation_id'],)).fetchone())
                self.assertEqual((saved['intent'],saved['payload']),(immutable['intent'],immutable['payload']))
                self.assertEqual(recovery.completed_historical_query(db,saved,json.loads(saved['intent']),json.loads(saved['proof'])),
                                 frozen['admissions'][0]['id'])
                dm.record_generation(db,dm.WarehouseConfigGeneration('destroy-later-head','LOCAL','later-op',(),(),(),'APPLIED'))
                db.execute("UPDATE warehouse_controls SET applied_generation_id='destroy-later-head'")
                db.execute("UPDATE catalog_bindings SET applied_generation_id='destroy-later-head'")
            self.assertEqual(self.snapshot(),before)
            self.operator.adapter = Mock(side_effect=AssertionError('completed proof must not observe backend'))
            probe.events[probe.cid['A']],probe.events[probe.cid['B']] = [],[]
            self.assertEqual(self.apply(completed),completed)
        self.operator.adapter.assert_not_called()
        for call in probe.adapter._run.call_args_list:
            self.assertNotIn(call.args[1],('stop','rm','restart','start'))



class RenderedAdapterTests(unittest.TestCase):
    def test_real_frozen_generation_render_initializes_adapter_and_config_scope(self):
        from test_warehouse_operations import OperationTests
        fixture = OperationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        bundle = recovery.warehouse_operations._render(fixture.bundle['manifest'])
        base = 'deploy/local-experiment/trino/'
        config = dict(docker_context='colima-mac-studio-solo', kube_context='colima-mac-studio-solo')
        with patch.object(recovery.DockerAdapter, '_run', side_effect=AssertionError('no runtime calls')) as external:
            adapter = recovery.DockerAdapter(config, Mock(), bundle)
            external.assert_not_called()
        deployment = json.loads(bundle['files'][base + 'deployment.yaml'])
        self.assertEqual(adapter.expected, deployment)
        self.assertEqual((adapter.deployment, adapter.namespace), ('trino-local', 'shirokuma-dev'))
        secure = json.loads(bundle['files'][base + 'secure/kustomization.yaml'])
        self.assertEqual(secure['resources'], ['../deployment.yaml', '../service.yaml', '../networkpolicy.yaml'])
        trino = next(c for c in adapter.expected['spec']['template']['spec']['containers'] if c['name'] == 'trino')
        self.assertIn('@sha256:', trino['image'])
        self.assertEqual(trino['command'], ['/usr/lib/trino/bin/run-trino'])
        mount = next(m for m in trino['volumeMounts'] if m['mountPath'] == '/etc/trino')
        volume = next(v for v in adapter.expected['spec']['template']['spec']['volumes'] if v['name'] == mount['name'])
        generator = next(g for g in secure['configMapGenerator'] if g['name'] == volume['configMap']['name'])
        self.assertIn('../config.properties', generator['files'])
        properties = bundle['files'][base + 'config.properties'].decode()
        self.assertIn('coordinator=true', properties)
        self.assertIn('node-scheduler.include-coordinator=true', properties)
        self.assertIn('discovery.uri=http://localhost:8080', properties)

        observed = DockerAdapterTests()
        observed.cid = 'a' * 64
        observed.engine_id = 'rendered-engine'
        observed.endpoint = 'unix://' + str(Path.home() / '.colima/mac-studio-solo/docker.sock')
        observed.events = ''
        observed.properties = properties
        observed.deployment = copy.deepcopy(deployment)
        observed.deployment['metadata']['uid'] = 'deployment-uid'
        observed.pod = copy.deepcopy(deployment['spec']['template'])
        observed.pod['metadata'].update(uid='pod-uid', name='rendered-trino-pod', namespace=adapter.namespace,
            ownerReferences=[dict(uid='rs-uid', name='rendered-trino-rs', kind='ReplicaSet', controller=True)])
        observed.pod['status'] = dict(conditions=[dict(type='Ready', status='True')],
            containerStatuses=[dict(name=c['name'], ready=True,
                containerID='docker://' + (observed.cid if c['name'] == 'trino' else 'b' * 64))
                for c in observed.pod['spec']['containers']])
        observed.rs = dict(metadata=dict(uid='rs-uid', ownerReferences=[
            dict(uid='deployment-uid', kind='Deployment', controller=True)]))
        accepted = datetime.now(timezone.utc)
        observed.inspected = dict(Id=observed.cid, Image='sha256:' + 'c' * 64,
            Config=dict(Image=trino['image'], Labels={
                'io.kubernetes.pod.uid': 'pod-uid', 'io.kubernetes.pod.name': 'rendered-trino-pod',
                'io.kubernetes.pod.namespace': adapter.namespace, 'io.kubernetes.container.name': 'trino'}),
            HostConfig=dict(RestartPolicy=dict(Name='no')), State=dict(Running=True, Status='running',
                StartedAt=(accepted - timedelta(hours=1)).isoformat(), FinishedAt='0001-01-01T00:00:00Z', ExitCode=0))
        frozen = dict(generation=dict(id=bundle['manifest']['generation_id']),
            run=dict(created_at=accepted.isoformat()), queries=[dict(created_at=accepted.isoformat())])
        service = json.loads(bundle['files'][base + 'service.yaml'])

        def kube(resource, *args):
            if resource == 'service':
                self.assertEqual(args, (adapter.deployment,))
                return service
            if resource == 'configmap':
                self.assertEqual(args, (volume['configMap']['name'],))
            return observed.kube_read(resource, *args)

        with patch.object(adapter, '_docker', side_effect=observed.docker_read), \
             patch.object(adapter, '_kube', side_effect=kube), \
             patch.object(adapter, '_run', side_effect=observed.command_read), \
             patch.object(subprocess, 'run', side_effect=AssertionError('external command forbidden')), \
             patch.object(subprocess, 'Popen', side_effect=AssertionError('external process forbidden')):
            scope = adapter.scope(frozen)
            self.assertEqual((scope['container_id'], scope['engine_id'], scope['pod_uid'], scope['deployment_uid']),
                (observed.cid, observed.engine_id, 'pod-uid', 'deployment-uid'))
            observed.properties = properties + '\n# observed config differs from frozen generation\n'
            with self.assertRaises(QueryError):
                adapter.scope(frozen)


class DockerAdapterTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.start = (self.now - timedelta(hours=1)).isoformat()
        self.since = self.now.isoformat()
        self.cid = 'a' * 64
        self.labels = {'io.kubernetes.pod.uid':'pod-uid', 'io.kubernetes.pod.name':'trino-random',
                       'io.kubernetes.pod.namespace':'shirokuma-dev', 'io.kubernetes.container.name':'trino'}
        self.config = dict(docker_context='colima-mac-studio-solo', kube_context='colima-mac-studio-solo')
        self.properties = 'coordinator=true\nnode-scheduler.include-coordinator=true\ndiscovery.uri=http://localhost:8080\n'
        self.bundle = dict(files={'deploy/local-experiment/trino/config.properties':self.properties})
        self.image = 'trinodb/trino@sha256:' + 'd' * 64
        container = dict(name='trino', image=self.image, command=['/usr/lib/trino/bin/run-trino'],
                         volumeMounts=[dict(name='config', mountPath='/etc/trino')])
        self.deployment = dict(metadata=dict(uid='deployment-uid',name='trino-local',namespace='shirokuma-dev', annotations={'shirokuma.dev/database-generation':'g1'}),
            spec=dict(replicas=1, selector=dict(matchLabels={'app':'trino-local'}),
                      template=dict(spec=dict(containers=[container]))))
        self.pod = dict(metadata=dict(uid='pod-uid', name='trino-random', annotations={'shirokuma.dev/database-generation':'g1'},
            ownerReferences=[dict(uid='rs-uid', name='trino-rs', kind='ReplicaSet', controller=True)]),
            spec=dict(containers=[container], volumes=[dict(name='config', configMap=dict(name='managed-config'))]),
            status=dict(conditions=[dict(type='Ready',status='True')],containerStatuses=[dict(name='trino', ready=True, containerID='docker://' + self.cid)]))
        self.rs = dict(metadata=dict(uid='rs-uid', ownerReferences=[dict(uid='deployment-uid', kind='Deployment', controller=True)]))
        self.inspected = dict(Id=self.cid, Image='sha256:image', Config=dict(Labels=self.labels, Env=['SECRET=private']),
            HostConfig=dict(RestartPolicy=dict(Name='no')), State=dict(Running=True, Status='running', ExitCode=0, StartedAt=self.start,
                                                                      FinishedAt='0001-01-01T00:00:00Z'))
        self.graph = dict(generation=dict(id='g1'), run=dict(created_at=self.since), queries=[dict(created_at=self.since)])
        self.bundle['files']['deploy/local-experiment/trino/deployment.yaml'] = json.dumps(self.deployment)
        self.bundle['files']['deploy/local-experiment/trino/secure/kustomization.yaml'] = json.dumps(dict(namespace='shirokuma-dev'))
        self.adapter = recovery.DockerAdapter(self.config, Mock(), self.bundle)
        self.events = ''
        self.engine_id = 'engine-one'
        self.endpoint = 'unix://' + str(Path.home() / '.colima/mac-studio-solo/docker.sock')
        self.docker = patch.object(self.adapter, '_docker', side_effect=self.docker_read)
        self.kube = patch.object(self.adapter, '_kube', side_effect=self.kube_read)
        self.command = patch.object(self.adapter, '_run', side_effect=self.command_read)
        for p in (self.docker, self.kube, self.command):
            p.start()
            self.addCleanup(p.stop)

    def docker_read(self, *args):
        if args[0] == 'context':
            return [dict(Name='colima-mac-studio-solo', Endpoints={'docker':dict(Host=self.endpoint)})]
        if args[0] == 'info':
            return self.engine_id
        if args[0] == 'inspect':
            return [copy.deepcopy(self.inspected)]
        raise AssertionError(args)

    def kube_read(self, resource, *args):
        if resource == 'deployment': return self.deployment
        if resource == 'pods': return dict(items=[self.pod])
        if resource == 'replicaset': return self.rs
        if resource == 'configmap': return dict(data={'config.properties':self.properties})
        if resource == 'endpoints': return dict(subsets=[dict(addresses=[dict(targetRef=dict(uid='pod-uid'))])])
        if resource == 'service': return dict(metadata=dict(annotations={'shirokuma.dev/database-generation':'g1'}),
                                              spec=dict(selector={'app':'trino-local'}))
        raise AssertionError((resource, args))

    def command_read(self, program, *args, **kw):
        if program == 'kubectl' and args[:2] == ('config','view'):
            return json.dumps(dict(clusters=[dict(cluster=dict(server='https://127.0.0.1:6443'))]))
        if program == 'docker' and args[0] == 'top':
            return 'PID COMMAND ARGS\n123 java java io.trino.server.TrinoServer\n'
        if program == 'docker' and args[0] == 'ps':
            return self.cid+'\n'
        if program == 'docker' and args[0] == 'events':
            return self.events
        raise AssertionError((program, args))

    def test_exact_scope_and_honest_current_engine_binding(self):
        scope = self.adapter.scope(self.graph)
        self.assertEqual((scope['engine_id'],scope['container_id'],scope['pod_uid']), ('engine-one',self.cid,'pod-uid'))
        self.assertIn('not recorded at admission', scope['binding_basis'])

    def test_scope_top_requests_pid_for_target_and_other_managed_containers(self):
        other = dict(Id='b' * 64, Image='sha256:opa',
            Config=dict(Labels={'io.kubernetes.pod.namespace': 'shirokuma-dev'}))
        top_calls = []
        def docker(*args):
            if args == ('inspect', self.cid, other['Id']):
                return [copy.deepcopy(self.inspected), other]
            return self.docker_read(*args)
        def command(program, *args, **kwargs):
            if program == 'docker' and args[0] == 'ps':
                return self.cid + '\n' + other['Id'] + '\n'
            if program == 'docker' and args[0] == 'top':
                self.assertEqual(args[2:], ('-eo', 'pid,comm,args'))
                top_calls.append(args[1])
                if args[1] == other['Id']:
                    return 'PID COMMAND ARGS\n456 opa opa run --server\n'
            return self.command_read(program, *args, **kwargs)
        with patch.object(self.adapter, '_docker', side_effect=docker), \
             patch.object(self.adapter, '_run', side_effect=command):
            self.adapter.scope(self.graph)
        self.assertEqual(top_calls, [self.cid, other['Id']])

    def test_changed_topology_identity_start_and_restart_rejected(self):
        cases = [lambda: self.deployment['spec'].update(replicas=2),
                 lambda: self.pod['status']['conditions'][0].update(status='False'),
                 lambda: self.pod['status']['containerStatuses'][0].update(ready=False),
                 lambda: self.deployment['metadata']['annotations'].update({'shirokuma.dev/database-generation':'other'}),
                 lambda: self.inspected['HostConfig']['RestartPolicy'].update(Name='always'),
                 lambda: self.inspected.update(Id='b' * 64),
                 lambda: self.inspected['Config']['Labels'].update({'io.kubernetes.pod.uid':'foreign'}),
                 lambda: self.inspected['State'].update(StartedAt=(self.now + timedelta(seconds=1)).isoformat()),
                 lambda: self.pod['spec']['containers'].append(dict(name='worker', image=self.image)),
                 lambda: self.rs['metadata']['ownerReferences'][0].update(uid='foreign'),
                 lambda: self.pod['status']['containerStatuses'][0].update(containerID='docker://short')]
        original = copy.deepcopy((self.deployment,self.pod,self.inspected,self.rs))
        for change in cases:
            with self.subTest(change=change):
                change()
                with self.assertRaises(QueryError): self.adapter.scope(self.graph)
                self.deployment,self.pod,self.inspected,self.rs = copy.deepcopy(original)

    def test_remote_or_other_docker_socket_rejected(self):
        for endpoint in ('tcp://127.0.0.1:2375','unix:///var/run/docker.sock','ssh://foreign'):
            self.endpoint = endpoint
            with self.assertRaises(QueryError): self.adapter.engine()

    def test_config_worker_and_foreign_endpoint_rejected(self):
        with patch.object(self.adapter, '_kube', wraps=self.kube_read) as kube:
            base = self.kube_read
            def extra(resource,*args):
                if resource == 'pods' and args == ('--all-namespaces',):
                    return dict(items=[self.pod,dict(metadata=dict(uid='worker'),spec=dict(containers=[dict(image=self.image)]))])
                return base(resource,*args)
            kube.side_effect = extra
            with self.assertRaises(QueryError): self.adapter.scope(self.graph)
        self.properties = 'coordinator=true\nnode-scheduler.include-coordinator=false\n'
        with self.assertRaises(QueryError): self.adapter.scope(self.graph)
        self.properties = self.bundle['files']['deploy/local-experiment/trino/config.properties']
        def foreign(resource,*args):
            if resource == 'endpoints': return dict(subsets=[dict(addresses=[dict(targetRef=dict(uid='foreign'))])])
            return self.kube_read(resource,*args)
        with patch.object(self.adapter,'_kube',side_effect=foreign):
            with self.assertRaises(QueryError): self.adapter.scope(self.graph)

    def test_stopped_inspection_positive_receipt_excludes_credentials(self):
        scope = self.adapter.scope(self.graph)
        self.inspected['State'].update(Running=False, Status='exited', FinishedAt=(self.now + timedelta(seconds=2)).isoformat())
        proof = self.adapter.proof(scope,self.since)
        self.assertEqual(proof['kind'],'stopped-inspect')
        self.assertNotIn('SECRET',json.dumps(proof))

    def test_absence_expired_events_and_wrong_engine_never_prove_stop(self):
        scope = self.adapter.scope(self.graph)
        self.inspected['State'].update(Running=False, Status='exited')
        self.assertIsNone(self.adapter.proof(scope,self.since))
        self.engine_id = 'new-engine'
        with self.assertRaises(QueryError): self.adapter.proof(scope,self.since)

    def test_exact_event_recovers_missing_container_but_wrong_old_event_does_not(self):
        scope = self.adapter.scope(self.graph)
        event = dict(Type='container',Action='destroy',Actor=dict(ID=self.cid,Attributes=self.labels),
                     timeNano=int((self.now + timedelta(seconds=1)).timestamp()*1e9))
        self.events = json.dumps(event)
        base = self.docker_read
        def absent(*args):
            if args[0]=='inspect': raise QueryError('RECOVERY_BACKEND_UNAVAILABLE',409)
            return base(*args)
        with patch.object(self.adapter,'_docker',side_effect=absent):
            self.assertEqual(self.adapter.proof(scope,self.since)['kind'],'exact-event')
            for change in (dict(Action='start'),dict(Actor=dict(ID='b'*64,Attributes=self.labels)),dict(timeNano=1)):
                self.events = json.dumps(dict(event,**change))
                self.assertIsNone(self.adapter.proof(scope,self.since))

    def test_running_original_never_uses_stale_die_event(self):
        scope = self.adapter.scope(self.graph)
        self.events = json.dumps(dict(Type='container',Action='die',Actor=dict(ID=self.cid,Attributes=self.labels),
                                     timeNano=int((self.now + timedelta(seconds=1)).timestamp()*1e9)))
        self.assertIsNone(self.adapter.proof(scope,self.since))

    def test_event_restart_after_die_is_not_execution_stop_proof(self):
        scope = self.adapter.scope(self.graph)
        base = self.docker_read
        event = dict(Type='container',Action='die',Actor=dict(ID=self.cid,Attributes=self.labels),
                     timeNano=int((self.now+timedelta(seconds=1)).timestamp()*1e9))
        self.events = json.dumps(event)+'\n'+json.dumps(dict(event,Action='start',timeNano=event['timeNano']+1000))
        def absent(*args):
            if args[0]=='inspect':raise QueryError('RECOVERY_BACKEND_UNAVAILABLE',409)
            return base(*args)
        with patch.object(self.adapter,'_docker',side_effect=absent):
            self.assertIsNone(self.adapter.proof(scope,self.since))

    def test_adapter_commands_use_rtk_dedicated_context_and_deadline(self):
        self.command.stop()
        with patch.object(subprocess,'run',return_value=Mock(returncode=0,stdout='{}')) as command:
            self.adapter._run('docker','inspect',self.cid)
            args,kw = command.call_args
            self.assertEqual(args[0][:5],['rtk','proxy','docker','--context','colima-mac-studio-solo'])
            self.assertEqual(kw['timeout'],20)
            self.adapter._run('kubectl','get','pods')
            self.assertEqual(command.call_args.args[0][:5],['rtk','proxy','kubectl','--context','colima-mac-studio-solo'])

    def test_tls_service_tunnel_health_with_real_manifest_shape_no_normal_server(self):
        import trino_auth
        import warehouse_resident as wr
        manifest=dict(version=2,platform_admin=dict(username='shirokuma_admin',password='admin-secret'),databases={
            'db':dict(sql_catalog='sales',generation_id='g1',roles={r:dict(username=trino_auth.scoped_username('db',r),password=r+'-secret')
                      for r in ('bi_viewer','data_engineer')})})
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'auth.json'
            path.write_text(json.dumps(manifest));path.chmod(0o600)
            self.adapter.profile=SimpleNamespace(trino_auth_file=str(path),trino_ca_file='/private/ca.pem',
                                                  sql_endpoint_url='http://127.0.0.1:8080')
            @contextmanager
            def tunnel():
                self.assertEqual(wr.KUBECTL,['rtk','proxy','kubectl','--context','colima-mac-studio-solo','-n','shirokuma-dev'])
                yield dict(url='https://127.0.0.1:19876')
            scope=dict(engine_id='engine-one',container_id='b'*64,deployment_uid='deployment-uid')
            response=Mock(status=200);response.read.return_value=b'{"starting":false,"coordinator":true}'
            previous=wr.KUBECTL
            with patch.object(self.adapter,'scope',return_value=scope),patch.object(wr,'_trino_connection',tunnel), \
                 patch.object(wr,'warehouse_activity',side_effect=AssertionError('exclusive gate cannot nest activity')), \
                 patch('trino_auth.backend_ssl_context',return_value='verified-tls') as tls, \
                 patch('http.client.HTTPSConnection') as http, patch.dict(os.environ):
                http.return_value.getresponse.return_value=response
                self.assertEqual(self.adapter.healthy(self.graph,dict(scope,container_id=self.cid)),scope)
                http.assert_called_once_with('127.0.0.1',19876,context='verified-tls',timeout=10)
                request=http.return_value.request.call_args
                self.assertEqual(request.args,('GET','/v1/info'))
                self.assertTrue(request.kwargs['headers']['Authorization'].startswith('Basic '))
                tls.assert_called_once()
            self.assertIs(wr.KUBECTL,previous)

    def test_exact_stopped_removal_has_no_force_or_volume_flags(self):
        scope=self.adapter.scope(self.graph)
        self.inspected['State'].update(Running=False,Status='exited',FinishedAt=self.since)
        calls=[]
        base=self.command_read
        removed=False
        def command(program,*args,**kw):
            nonlocal removed
            calls.append((program,args))
            if program=='docker' and args[0]=='rm':removed=True;return self.cid
            if program=='docker' and args[0]=='ps':return '' if removed else self.cid+'\n'
            return base(program,*args,**kw)
        with patch.object(self.adapter,'_run',side_effect=command):
            self.adapter.remove(scope)
        self.assertEqual([args for program,args in calls if program=='docker' and args[0]=='rm'],[('rm',self.cid)])

    def test_remove_rejects_restarted_or_changed_stopped_identity(self):
        scope=self.adapter.scope(self.graph)
        original=copy.deepcopy(self.inspected)
        for change in (lambda:self.inspected['State'].update(Running=True),
                       lambda:self.inspected['State'].update(StartedAt=self.since),
                       lambda:self.inspected.update(Image='changed'),
                       lambda:self.inspected['HostConfig']['RestartPolicy'].update(Name='always')):
            self.inspected=copy.deepcopy(original)
            self.inspected['State'].update(Running=False,Status='exited',FinishedAt=self.since)
            change()
            with self.assertRaises(QueryError):self.adapter.remove(scope)

    def test_present_changed_identity_cannot_fall_back_to_old_die(self):
        scope=self.adapter.scope(self.graph)
        self.inspected['State'].update(Running=False,Status='exited',FinishedAt=self.since)
        self.events=json.dumps(dict(Type='container',Action='die',Actor=dict(ID=self.cid,Attributes=self.labels),
                                    timeNano=int((self.now+timedelta(seconds=1)).timestamp()*1e9)))
        original=copy.deepcopy(self.inspected)
        for change in (lambda:self.inspected.update(Image='new-image'),
                       lambda:self.inspected['State'].update(StartedAt=self.since),
                       lambda:self.inspected['Config']['Labels'].update({'io.kubernetes.pod.uid':'new-pod'})):
            change()
            self.assertIsNone(self.adapter.proof(scope,self.since))
            self.inspected=copy.deepcopy(original)

    def test_digest_only_default_command_worker_rejected_by_engine(self):
        worker=dict(Id='b'*64,Image='sha256:image',Config=dict(Labels={'io.kubernetes.pod.namespace':'shirokuma-dev'}))
        base=self.command_read
        def command(program,*args,**kw):
            if program=='docker' and args[0]=='ps':return self.cid+'\n'+worker['Id']+'\n'
            return base(program,*args,**kw)
        docker=self.docker_read
        def read(*args):
            if args[0]=='inspect' and len(args)==3:return [copy.deepcopy(self.inspected),worker]
            return docker(*args)
        with patch.object(self.adapter,'_run',side_effect=command),patch.object(self.adapter,'_docker',side_effect=read):
            with self.assertRaises(QueryError):self.adapter.scope(self.graph)

    def test_exact_event_receipt_excludes_unrelated_secret_attributes(self):
        scope=self.adapter.scope(self.graph)
        self.events=json.dumps(dict(Type='container',Action='destroy',secret='SENTINEL',
            Actor=dict(ID=self.cid,Attributes=dict(self.labels,secret='SENTINEL')),
            timeNano=int((self.now+timedelta(seconds=1)).timestamp()*1e9)))
        docker=self.docker_read
        def absent(*args):
            if args[0]=='inspect':raise QueryError('RECOVERY_BACKEND_UNAVAILABLE',409)
            return docker(*args)
        with patch.object(self.adapter,'_docker',side_effect=absent):
            proof=self.adapter.proof(scope,self.since)
        self.assertEqual(proof['kind'],'exact-event')
        self.assertNotIn('SENTINEL',json.dumps(proof))


class HistoricalAdapterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.now = datetime.now(timezone.utc)
        self.cid = {'A':'a' * 64, 'B':'b' * 64, 'C':'c' * 64}
        self.digest = 'sha256:' + 'd' * 64
        self.image = 'docker.io/trinodb/trino@' + self.digest
        self.namespace = 'shirokuma-dev'
        self.cm_name = 'trino-local-config-589cfhfttm'
        self.properties = 'coordinator=true\nnode-scheduler.include-coordinator=true\ndiscovery.uri=http://localhost:8080\n'
        container = dict(name='trino', image=self.image, command=['/usr/lib/trino/bin/run-trino'],
                         volumeMounts=[dict(name='config', mountPath='/etc/trino', readOnly=True)])
        canonical_volumes = [dict(name='config', configMap=dict(name='trino-local-config',
            items=[dict(key='config.properties', path='config.properties')]))]
        volumes = copy.deepcopy(canonical_volumes)
        volumes[0]['configMap'].update(name=self.cm_name, defaultMode=420)
        self.pods, self.rs = {}, {}
        for role, offset in [('A',300), ('B',200), ('C',0)]:
            labels = {'app':'trino-local', 'pod-template-hash':role}
            meta = dict(name='trino-' + role, uid='pod-' + role, namespace=self.namespace, labels=labels,
                creationTimestamp=(self.now - timedelta(seconds=offset)).isoformat(),
                annotations={'shirokuma.dev/database-generation':'g1'},
                ownerReferences=[dict(uid='rs-' + role, name='rs-' + role, kind='ReplicaSet', controller=True)])
            spec = dict(containers=[copy.deepcopy(container)], volumes=copy.deepcopy(volumes))
            self.pods[role] = dict(metadata=meta, spec=spec, status=dict(conditions=[dict(type='Ready', status='True')],
                containerStatuses=[dict(name='trino', containerID='docker://' + self.cid[role],
                    imageID='docker-pullable://trinodb/trino@' + self.digest, ready=True)]))
            self.rs['rs-' + role] = dict(metadata=dict(uid='rs-' + role, namespace=self.namespace,
                ownerReferences=[dict(uid='deployment', kind='Deployment', controller=True)]),
                spec=dict(selector=dict(matchLabels=labels), template=dict(metadata=dict(labels=labels,
                    annotations=meta['annotations']), spec=copy.deepcopy(spec))))
        self.deployment = dict(metadata=dict(uid='deployment', namespace=self.namespace, annotations={'shirokuma.dev/database-generation':'g1'}),
            spec=dict(replicas=1, selector=dict(matchLabels={'app':'trino-local'}),
                      template=copy.deepcopy(self.rs['rs-C']['spec']['template'])))
        expected = copy.deepcopy(self.deployment)
        expected['metadata']['name'] = 'trino-local'
        expected['spec']['template']['spec']['volumes'] = canonical_volumes
        bundle = dict(files={'deploy/local-experiment/trino/deployment.yaml':json.dumps(expected),
            'deploy/local-experiment/trino/config.properties':self.properties,
            'deploy/local-experiment/trino/secure/kustomization.yaml':json.dumps(dict(namespace=self.namespace,resources=['../deployment.yaml'],
                configMapGenerator=[dict(name='trino-local-config', files=['../config.properties'])]))})
        self.adapter = recovery.DockerAdapter(dict(), Mock(), bundle)
        self.cm = dict(metadata=dict(name=self.cm_name, uid='cm-uid', namespace=self.namespace,
            creationTimestamp=(self.now - timedelta(seconds=400)).isoformat()), data={'config.properties':self.properties})
        self.pods['A']['status']['containerStatuses'][0]['state'] = dict(running=dict(
            startedAt=(self.now-timedelta(seconds=280)).isoformat()))
        paths = []
        for role in ('A', 'B'):
            path = self.root / ('pods-' + role + '.json')
            self.private(path, dict(kind='PodList', items=[self.pods[role]]))
            paths.append(path)
        intent_path, result_path = self.root / 'original.intent.json', self.root / 'original.result.json'
        self.private(intent_path, dict(actor='owner', calls=1, at=datetime.now(timezone.utc).isoformat(),
            argv=['rtk', 'proxy', '/offline/shirokuma', 'warehouse', 'query', 'LOCAL', '--catalog', 'q',
                  '--schema', 'raw', '--mode', 'read-only', '--max-rows', '37', '--no-auto-wake', '--sql', 'SELECT 17']))
        created = datetime.now(timezone.utc).isoformat()
        self.private(result_path, dict(exit=1, stdout='', stderr='resident warehouse failed: Authenticated SQL endpoint request failed\n\n'))
        self.selector = dict(members=[recovery._snapshot(p, self.cid[role]) for p, role in zip(paths, ('A', 'B'))],
            producer=dict(intent=recovery._historical_file(intent_path)[0], result=recovery._historical_file(result_path)[0]))
        self.graph = dict(subject='query', generation=dict(id='g1'), queries=[dict(actor='owner', created_at=created)],
            admissions=[dict(created_at=created)], executions=[dict(sql='SELECT 17', sql_catalog='q', sql_schema='raw')])
        end = max(self.selector['producer']['result']['stat'][k] for k in ('mtime_ns','ctime_ns'))
        self.started = datetime.fromtimestamp(end / 1e9, timezone.utc) + timedelta(seconds=1)
        self.events = {}
        for role, offsets in [('A',[-290,-280,-250,-249,-248]), ('B',[-190,-180,2,3,4])]:
            labels = dict(zip(recovery.MANAGED_LABELS, ('pod-' + role, self.namespace, 'trino-' + role, 'trino')))
            self.events[self.cid[role]] = [dict(Type='container', Action=action,
                timeNano=recovery._nano((self.now + timedelta(seconds=offset)).isoformat()),
                Actor=dict(ID=self.cid[role], Attributes=dict(labels, image=self.digest,
                    **({'exitCode':'143'} if action == 'die' else {}))))
                for action, offset in zip(('create', 'start', 'stop', 'die', 'destroy'), offsets)]
        self.running = [dict(Id=self.cid['C'], Image=self.digest, Config=dict(Labels=dict(zip(recovery.MANAGED_LABELS,
            ('pod-C',self.namespace,'trino-C','trino')))), State=dict(Running=True, StartedAt=self.started.isoformat()),
            HostConfig=dict(RestartPolicy=dict(Name='no'))),
            dict(Id='e' * 64, Image=self.digest, Config=dict(Labels={'io.kubernetes.pod.namespace':'separate-clean'}))]
        self.engine_id = 'same-engine'
        self.current_pods = [self.pods['C'], dict(metadata=dict(namespace='separate-clean'), spec=dict(containers=[dict(name='trino',image=self.image)]))]
        for method, side_effect in [('_kube', self.kube), ('_docker', self.docker), ('_run', self.command)]:
            p = patch.object(self.adapter, method, side_effect=side_effect)
            p.start()
            self.addCleanup(p.stop)
        for target, value in [('engine', lambda:dict(engine_id=self.engine_id, socket='unix://trusted')),
                              ('historical_tls', lambda:None)]:
            p = patch.object(self.adapter, target, side_effect=value)
            p.start()
            self.addCleanup(p.stop)
        clock = patch.object(admission, '_now', return_value=(self.now + timedelta(seconds=10)).isoformat())
        clock.start()
        self.addCleanup(clock.stop)
        p = patch.object(subprocess, 'run', side_effect=AssertionError('no live calls'))
        p.start()
        self.addCleanup(p.stop)

    def private(self, path, data):
        path.write_text(json.dumps(data))
        path.chmod(0o600)

    def kube(self, resource, *args):
        if resource == 'deployment': return self.deployment
        if resource == 'deployments': return dict(items=[self.deployment])
        if resource == 'services': return dict(items=[self.kube('service')])
        if resource == 'replicaset': return self.rs[args[0]]
        if resource == 'configmap': return self.cm
        if resource == 'pods': return dict(items=self.current_pods)
        if resource == 'service': return dict(metadata=dict(name='trino-local',uid='service-uid',namespace=self.namespace,
            annotations={'shirokuma.dev/database-generation':'g1'}),spec=dict(selector={'app':'trino-local'}))
        if resource == 'endpoints': return dict(subsets=[dict(addresses=[dict(targetRef=dict(uid='pod-C'))])])
        raise AssertionError((resource, args))

    def docker(self, *args):
        if args[:2] == ('image','inspect'): return [dict(RepoDigests=[self.image.removeprefix('docker.io/')])]
        if args[0] == 'inspect': return self.running
        raise AssertionError(args)

    def command(self, program, *args, **kw):
        if program == 'kubectl' and args[0] == 'kustomize':
            raise AssertionError('historical scope must not invoke an offline renderer')
        if program == 'docker' and args[0] == 'events':
            cid = args[args.index('--filter') + 1].split('=',1)[1]
            return '\n'.join(json.dumps(e) for e in self.events[cid])
        if program == 'docker' and args[0] == 'ps': return '\n'.join(row['Id'] for row in self.running)
        if program == 'docker' and args[0] == 'top':
            self.assertEqual(args[1], self.cid['C'])  # unrelated namespace is excluded by identity
            return 'PID COMMAND ARGS\n1 java java io.trino.server.TrinoServer\n'
        raise AssertionError((program,args))

    def observe(self):
        return self.adapter.historical(self.graph, self.selector)

    def test_actual_image_and_mutable_hashed_cm_two_member_positive(self):
        result = self.observe()
        self.assertEqual((result['kind'], result['version']), ('historical-exact-events',2))
        self.assertEqual([m['scope']['container_id'] for m in result['members']], [self.cid['A'], self.cid['B']])
        self.assertIn('no captured historical ConfigMap', result['limitations'])
        self.assertEqual(result['replacement_scope']['role'], 'replacement')
        for call in self.adapter._run.call_args_list:
            self.assertNotIn(call.args[1], ('stop','rm','restart'))

    def test_terminal_members_and_active_full_lifecycle_are_required(self):
        original = copy.deepcopy(self.events)
        for role in ('A','B'):
            for index in (range(2,5) if role == 'A' else range(5)):
                with self.subTest(role=role, index=index):
                    self.events = copy.deepcopy(original)
                    self.events[self.cid[role]].pop(index)
                    with self.assertRaises(QueryError): self.observe()
        self.events = copy.deepcopy(original)
        self.events[self.cid['A']].append(copy.deepcopy(self.events[self.cid['A']][-1]))
        with self.assertRaises(QueryError): self.observe()
        self.events = copy.deepcopy(original)
        self.events[self.cid['B']][1]['Action'] = 'restart'
        with self.assertRaises(QueryError): self.observe()
        self.selector['members'].pop(0)
        with self.assertRaises(QueryError): self.observe()

    def test_cm_creation_data_name_mount_and_rs_image_mismatches(self):
        originals = copy.deepcopy((self.cm,self.rs,self.deployment,self.running))
        changes = [lambda:self.cm['metadata'].update(creationTimestamp=self.now.isoformat()),
            lambda:self.cm['data'].update({'config.properties':'foreign'}),
            lambda:self.cm['metadata'].update(name='wrong-hash'),
            lambda:self.cm['metadata'].update(uid=''),
            lambda:self.cm.update(binaryData={}),
            lambda:self.rs['rs-A']['metadata'].update(uid='foreign'),
            lambda:self.rs['rs-B']['metadata']['ownerReferences'][0].update(uid='foreign'),
            lambda:self.rs['rs-A']['spec']['template']['spec']['containers'][0].update(image='foreign'),
            lambda:self.running[0].update(Image='sha256:' + 'e' * 64),
            lambda:self.deployment['spec'].update(replicas=2)]
        for change in changes:
            with self.subTest(change=change):
                self.cm,self.rs,self.deployment,self.running = copy.deepcopy(originals)
                change()
                with self.assertRaises(QueryError): self.observe()

    def test_image_repository_tag_digest_and_config_id_are_not_interchangeable(self):
        for value in ('docker.io/foreign/trino@' + self.digest, 'trinodb/trino:latest', 'sha256:' + 'f' * 64):
            with self.subTest(value=value):
                self.events[self.cid['A']][0]['Actor']['Attributes']['image'] = value
                with self.assertRaises((QueryError,ValueError)): self.observe()
        self.events[self.cid['A']][0]['Actor']['Attributes']['image'] = self.digest
        with patch.object(self.adapter, '_docker', side_effect=lambda *args: [dict(RepoDigests=['foreign/trino@' + self.digest])]):
            with self.assertRaises(QueryError): self.observe()

    def test_original_artifacts_actor_sql_window_and_birthtime_are_required(self):
        for section, key, value in [('intent','actor','foreign'), ('intent','calls',2), ('result','exit',0)]:
            with self.subTest(section=section,key=key):
                binding = self.selector['producer'][section]
                path = Path(binding['path'])
                original = path.read_bytes()
                data = json.loads(original);data[key] = value
                self.private(path,data)
                self.selector['producer'][section] = recovery._historical_file(path)[0]
                with self.assertRaises(QueryError): self.observe()
                path.write_bytes(original)
                self.selector['producer'][section] = recovery._historical_file(path)[0]
        # A rewritten intent is late even if its JSON 'at' was backdated.
        with self.assertRaises(QueryError): self.observe()

    def test_replacement_before_completed_window_census_and_tls_failure(self):
        before_result = recovery._time(self.graph['queries'][0]['created_at']) + timedelta(microseconds=1)
        self.assertLess(recovery._nano(before_result.isoformat()),self.selector['producer']['result']['stat']['birth_ns'])
        self.running[0]['State']['StartedAt'] = before_result.isoformat()
        with self.assertRaises(QueryError): self.observe()
        self.running[0]['State']['StartedAt'] = self.started.isoformat()
        with patch.object(self.adapter, 'historical_tls', side_effect=QueryError('TLS',409)):
            with self.assertRaises(QueryError): self.observe()
        extra = copy.deepcopy(self.pods['C']);extra['metadata']['uid'] = 'extra'
        self.current_pods.append(extra)
        with self.assertRaises(QueryError): self.observe()

    def test_same_daemon_and_snapshot_bytes_are_rechecked(self):
        self.adapter.engine.side_effect = [dict(engine_id='first'),dict(engine_id='changed')]
        with self.assertRaises(QueryError): self.observe()
        self.adapter.engine.side_effect = lambda:dict(engine_id=self.engine_id)
        Path(self.selector['members'][0]['path']).write_text('{}')
        with self.assertRaises(QueryError): self.observe()

    def test_captured_name_and_original_birthtime_are_not_inferred(self):
        # A valid-looking suffix cannot replace the exact captured mount chain.
        self.rs['rs-A']['spec']['template']['spec']['volumes'][0]['configMap']['name'] = 'trino-local-config-bbbbbbbbbb'
        with self.assertRaises(QueryError): self.observe()
        path = Path(self.selector['producer']['intent']['path'])
        info = path.stat()
        absent_birth = SimpleNamespace(st_mode=info.st_mode,st_uid=info.st_uid,st_birthtime=0)
        with patch.object(Path, 'lstat', return_value=absent_birth):
            with self.assertRaises(QueryError): recovery._historical_file(path)

    def test_normal_job_and_query_lower_start_guard_is_unchanged(self):
        probe = DockerAdapterTests()
        probe.setUp()
        self.addCleanup(probe.doCleanups)
        probe.inspected['State']['StartedAt'] = (probe.now + timedelta(seconds=1)).isoformat()
        for graph in (probe.graph, dict(generation=probe.graph['generation'],queries=probe.graph['queries'])):
            with self.subTest(graph=graph):
                with self.assertRaises(QueryError): probe.adapter.scope(graph)
        self.assertEqual(probe.adapter.scope(probe.graph,replacement=True)['container_id'],probe.cid)

    def test_unproven_service_deployment_topology_and_event_drift_fail_closed(self):
        base = self.kube
        def extra(resource,*args):
            value = base(resource,*args)
            if resource in ('services','deployments'):
                value['items'].append(copy.deepcopy(value['items'][0]))
            return value
        with patch.object(self.adapter, '_kube', side_effect=extra):
            with self.assertRaises(QueryError): self.observe()
        before = self.observe()
        self.events[self.cid['B']][-1]['timeNano'] += 1
        after = self.observe()
        self.assertNotEqual(before,after)
        self.assertNotEqual(before['members'][1]['events_digest'],after['members'][1]['events_digest'])

    def test_predecessor_terminal_only_actual_retention_is_positive(self):
        terminal = self.events[self.cid['A']][2:]
        kill = copy.deepcopy(terminal[0]);kill.update(Action='kill',timeNano=kill['timeNano']-1)
        self.events[self.cid['A']] = [kill] + terminal
        result = self.observe()
        self.assertEqual([e['action'] for e in result['members'][0]['events']], ['stop','die','destroy'])
        self.assertEqual(result['members'][0]['saved_start']['kind'], 'pod-running-startedAt')
        self.assertEqual(result['members'][0]['saved_start']['started_at'],
                         self.pods['A']['status']['containerStatuses'][0]['state']['running']['startedAt'])

    def test_predecessor_saved_start_source_and_terminal_negatives(self):
        self.events[self.cid['A']] = self.events[self.cid['A']][2:]
        observed = self.observe()
        original = copy.deepcopy(self.events[self.cid['A']])
        for index in range(3):
            self.events[self.cid['A']] = copy.deepcopy(original)
            self.events[self.cid['A']].pop(index)
            with self.assertRaises(QueryError): self.observe()
        self.events[self.cid['A']] = copy.deepcopy(original)
        lower = observed['producer']['window_start_ns']
        for index,event in enumerate(self.events[self.cid['A']]): event['timeNano'] = lower + index + 1
        with self.assertRaises(QueryError): self.observe()
        self.events[self.cid['A']] = copy.deepcopy(original)
        self.events[self.cid['A']][0]['Actor']['ID'] = self.cid['B']
        with self.assertRaises(QueryError): self.observe()
        self.events[self.cid['A']] = copy.deepcopy(original)
        restart = copy.deepcopy(original[-1]);restart.update(Action='restart',timeNano=restart['timeNano']+1)
        self.events[self.cid['A']].append(restart)
        with self.assertRaises(QueryError): self.observe()
        for field,value in [('snapshot_sha256','0' * 64),('pod_uid','foreign'),
                            ('started_at','2099-01-01T00:00:00Z'),('started_at','1970-01-01T00:00:00Z')]:
            member = copy.deepcopy(observed['members'][0]);member['saved_start'][field] = value
            with self.subTest(field=field,value=value):
                with self.assertRaises(QueryError):
                    recovery._historical_member(member,0,observed['producer'],(self.now+timedelta(seconds=10)).isoformat())
        # A resupplied wrong source snapshot never replaces the captured immutable bytes.
        path = Path(self.selector['members'][0]['path'])
        wrong = json.loads(path.read_text());wrong['items'][0]['status']['containerStatuses'][0]['state'] = {}
        path.write_text(json.dumps(wrong))
        with self.assertRaises(QueryError): self.observe()

    def test_predecessor_optional_start_events_must_match_saved_start(self):
        original = copy.deepcopy(self.events[self.cid['A']])
        for indices in ([0,1,2,3,4],[1,2,3,4],[0,2,3,4],[2,3,4]):
            self.events[self.cid['A']] = [copy.deepcopy(original[i]) for i in indices]
            self.observe()
        self.events[self.cid['A']] = copy.deepcopy(original)
        self.events[self.cid['A']][1]['timeNano'] += 1_000_000_000
        with self.assertRaises(QueryError): self.observe()

    def test_parent_relative_sealed_resources_need_no_offline_renderer(self):
        secure = json.loads(self.adapter.bundle['files']['deploy/local-experiment/trino/secure/kustomization.yaml'])
        self.assertIn('../deployment.yaml',secure['resources'])
        result = self.observe()
        self.assertEqual(result['kind'],'historical-exact-events')
        self.assertFalse(any(c.args[0] == 'kubectl' and c.args[1] == 'kustomize'
                             for c in self.adapter._run.call_args_list))

    def test_predecessor_cleanup_overlap_requires_execution_stop_before_active_start(self):
        base = recovery._nano(self.now.isoformat()) - 180_000_000_000
        a, b = self.events[self.cid['A']], self.events[self.cid['B']]
        for event, offset in zip(a[2:],(491327648,493387120,1279107957)):
            event['timeNano'] = base + offset
        b[0]['timeNano'], b[1]['timeNano'] = base + 1068654698, base + 1121134418
        execution_stop = max(e['timeNano'] for e in a if e['Action'] in ('stop','die'))
        self.assertLess(execution_stop,b[1]['timeNano'])
        self.assertEqual(a[-1]['timeNano']-b[0]['timeNano'],210453259)
        observed = self.observe()
        intent = dict(graph=self.graph,selector=self.selector,observation=observed,
            scope=observed['historical_scope'],replacement_scope=observed['replacement_scope'],
            planned_at=(self.now+timedelta(seconds=10)).isoformat())
        recovery._historical_claim(intent)
        for delta in (0,-1):
            with self.subTest(start_delta=delta):
                # Keep B's internal create/start order valid to isolate the cross-member guard.
                b[0]['timeNano'] = execution_stop - 1_000_000
                b[1]['timeNano'] = execution_stop + delta
                with self.assertRaises(QueryError): self.observe()
                saved = copy.deepcopy(intent)
                member = saved['observation']['members'][1]
                member['events'][0]['time_nano'], member['events'][1]['time_nano'] = b[0]['timeNano'], b[1]['timeNano']
                member['events_digest'] = recovery.hashlib.sha256(recovery._json(member['events']).encode()).hexdigest()
                recovery._historical_member(member,1,saved['observation']['producer'],saved['planned_at'])
                with self.assertRaises(QueryError): recovery._historical_claim(saved)
