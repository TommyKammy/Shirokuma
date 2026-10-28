"""P2-5 core contracts, with only synthetic public inputs and temporary ledgers."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tests.test_database_generation import spec, ROOT
sys.path.insert(0, str(ROOT / 'tools/local_experiment'))
import control_store as store
import data_management as dm
import database_generation as generation
import warehouse_admission as admission
import warehouse_operations as operations


class OperationTests(unittest.TestCase):

    def test_readonly_eligibility_rejects_bootstrap_partial_owner_and_p5_associations(self):
        plan = self.apply(self.plan(), verify=lambda p: (_ for _ in ()).throw(RuntimeError('lost')))
        self.assertEqual(plan['state'], 'UNKNOWN')
        with store.connect() as db:
            operations._readonly_eligible(db, plan)
        for field, value in (('phase', 'bootstrap'), ('predecessor_id', 'predecessor'), ('actor', 'other')):
            changed = dict(plan, **{field:value})
            with store.connect() as db, self.assertRaises(operations.OperationError):
                operations._readonly_eligible(db, changed)
        changed = copy.deepcopy(plan)
        changed['steps'][0]['state'] = 'UNKNOWN'
        with store.connect() as db, self.assertRaises(operations.OperationError):
            operations._readonly_eligible(db, changed)
        with store.connect() as db:
            db.execute("UPDATE warehouse_controls SET state='OPEN'")
            with self.assertRaises(operations.OperationError):
                operations._readonly_eligible(db, plan)
            db.execute("UPDATE warehouse_controls SET state='UNKNOWN'")
            db.execute('''INSERT INTO db_creations
                (id, environment_id, warehouse_id, actor, enrollment_id, enrollment_revision, database_id,
                 create_operation_id, intent_json, child_ids_json, revision, reservation_active,
                 terminal_outcome, created_at, updated_at) VALUES
                ('creation','environment','LOCAL','admin','enrollment',1,'opaque_0',?, '{}','{}',1,0,NULL,'now','now')''', (plan['id'],))
            for purpose in ('APPLY', 'VERIFY_RECOVERY'):
                db.execute('''INSERT INTO db_creation_phase_plans
                    (id,creation_id,phase,purpose,sequence,operation_id,generation_id,delivery_digest,private_ref,public_summary)
                    VALUES ('phase','creation','full',?,1,?,?,?,'private','{}')''',
                    (purpose, plan['id'], plan['generation_id'], plan['delivery_digest']))
                with self.assertRaises(operations.OperationError):
                    operations._readonly_eligible(db, plan)
                db.execute("DELETE FROM db_creation_phase_plans WHERE id='phase'")
            db.execute("UPDATE db_creations SET reservation_active=1")
            with self.assertRaises(operations.OperationError):
                operations._readonly_eligible(db, plan)


    def test_retire_before_apply_start_rejects_old_revision_and_keeps_physical_spec(self):
        plan = self.plan()
        physical = copy.deepcopy(plan['manifest']['spec']['databases'])
        with store.connect() as db:
            bindings = [tuple(r) for r in db.execute('SELECT * FROM catalog_bindings ORDER BY database_id')]
            db.execute("UPDATE databases SET state='retiring',revision=revision+1 WHERE id='opaque_0'")
        with self.assertRaises(operations.OperationError):
            self.apply(plan)
        self.assertFalse(self.calls)
        with store.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM databases WHERE id='opaque_0'").fetchone()[0], 'retiring')
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM catalog_bindings ORDER BY database_id')], bindings)
        self.assertEqual(operations.inspect(plan['id'], actor='admin')['manifest']['spec']['databases'], physical)

    def test_retirement_during_verify_cannot_restore_ready_even_without_revision_bump(self):
        for lifecycle in ('retiring', 'retired'):
            with self.subTest(lifecycle=lifecycle):
                # Separate fixtures keep warehouse attempt ownership independent.
                fixture = OperationTests()
                fixture.setUp()
                try:
                    plan = fixture.plan()
                    def verify(plan):
                        with store.connect() as db:
                            db.execute('UPDATE databases SET state=? WHERE id=?', (lifecycle, 'opaque_0'))
                        return dict(verified=True, evidence_ref='verify:retire-race')
                    result = fixture.apply(plan, verify=verify)
                    self.assertNotEqual(result['state'], 'APPLIED')
                    with store.connect() as db:
                        self.assertEqual(db.execute("SELECT state FROM databases WHERE id='opaque_0'").fetchone()[0], lifecycle)
                        self.assertEqual(db.execute('SELECT COUNT(*) FROM catalog_bindings').fetchone()[0], len(fixture.spec['databases']))
                        self.assertEqual({r[0] for r in db.execute('SELECT applied_generation_id FROM catalog_bindings')}, {None})
                    self.assertEqual(result['manifest']['spec']['databases'], plan['manifest']['spec']['databases'])
                finally:
                    fixture.doCleanups()

    def test_replan_with_retired_database_preserves_physical_entries_and_lifecycle(self):
        with store.connect() as db:
            db.execute("UPDATE databases SET state='retired' WHERE id='opaque_0'")
        plan = self.plan()
        result = self.apply(plan)
        self.assertEqual(result['state'], 'APPLIED')
        with store.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM databases WHERE id='opaque_0'").fetchone()[0], 'retired')
            self.assertIsNotNone(dm.get_binding(db, 'opaque_0'))
        self.assertIn('opaque_0', result['manifest']['spec']['databases'])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        env = patch.dict(os.environ, SHIROKUMA_STATE_DIR=str(self.directory),
                         PYTHONPATH=str(ROOT / 'tools/local_experiment'))
        env.start()
        self.addCleanup(env.stop)
        self.spec = spec()
        self.spec['grant_revisions'] = [dict(principal_id='reader', database_id='opaque_0',
                                           maximum_role='bi_viewer', revision=1)]
        self.bundle = generation.render(self.spec)
        with store.connect() as db:
            for key, value in self.spec['databases'].items():
                db.execute('INSERT INTO databases VALUES (?,?,?,?,?,?,?)',
                           (key, value['sql_catalog'], key, '', 'user', 'READY', 1))
                dm.create_binding(db, key, **{k: v for k, v in value['binding'].items()
                                             if k != 'desired_revision'})
                for schema in value['schemas']:
                    db.execute('INSERT INTO schemas VALUES (?,?,?,?,?,?,?)',
                               (schema['id'], key, schema['sql_name'], schema['sql_name'],
                                schema['purpose'], 'user', schema['revision']))
            dm.create_grant(db, 'reader', 'opaque_0', 'bi_viewer')
        refs = self.bundle['tofu']['bootstrap_secret_refs'] + self.bundle['tofu']['deferred_secret_refs']
        self.observations = [dict(ref, exists=True, uid='uid-'+ref['name'],
                                  resource_version='1', immutable=True) for ref in refs]
        self.calls = []

    def plan(self, key='key', bundle=None):
        return operations.create_plan('admin', '/warehouse/configuration', key,
                                      bundle or self.bundle, self.observations)

    def execute(self, step, plan):
        # A separate writer proves callback boundaries do not hold a SQLite transaction.
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
        self.calls.append(step)
        return dict(evidence_ref='ack:'+step, private='never-store-this-secret')

    def apply(self, plan, **kwargs):
        arguments = dict(actor='admin', expected_revision=plan['revision'],
            delivery_digest=plan['delivery_digest'], authorize=lambda actor: True,
            observe_secrets=lambda plan: copy.deepcopy(self.observations),
            execute_step=self.execute, verify=lambda plan: dict(verified=True, evidence_ref='verify:ok'))
        arguments.update(kwargs)
        return operations.apply(plan['id'], **arguments)

    def test_idempotency_generation_seal_and_public_shape(self):
        plan = self.plan()
        self.assertEqual(self.plan(), plan)
        changed = copy.deepcopy(self.bundle)
        changed['manifest']['spec']['generation_id'] = 'different'
        changed = generation.render(changed['manifest']['spec'])
        with self.assertRaises(operations.OperationError) as error:
            self.plan(bundle=changed)
        self.assertEqual(error.exception.status, 409)
        value = copy.deepcopy(self.spec)
        value['databases']['opaque_0']['secret_refs']['jwt'].update(name='new-jwt-v1')
        changed = generation.render(value)
        refs = changed['tofu']['bootstrap_secret_refs'] + changed['tofu']['deferred_secret_refs']
        observations = [dict(ref, exists=False, uid=None, resource_version=None, immutable=None) for ref in refs]
        with self.assertRaises(operations.OperationError):
            operations.create_plan('admin', '/other', 'other', changed, observations)
        self.assertEqual(set(plan), {'id', 'actor', 'action', 'state', 'revision', 'generation_id',
            'delivery_digest', 'warehouse_id', 'manifest', 'secret_observations', 'steps', 'phase', 'predecessor_id'})
        with self.assertRaises(operations.OperationError):
            operations.inspect(plan['id'], actor='other')
        self.assertEqual(operations.inspect(plan['id'], actor='other', is_admin=True), plan)

    def test_exact_bundle_and_secret_descriptor_validation(self):
        mutations = [lambda b: b['files'].update(extra=b'public'),
                     lambda b: b['tofu'].update(private='do-not-save'),
                     lambda b: b['manifest'].update(extra='do-not-save')]
        for mutate in mutations:
            bundle = copy.deepcopy(self.bundle)
            mutate(bundle)
            with self.assertRaises(operations.OperationError):
                self.plan(bundle=bundle)
        for mutate in (lambda o: o.pop(), lambda o: o[0].update(data='do-not-save'),
                       lambda o: o[0].update(immutable=False), lambda o: o[0].update(uid=''),
                       lambda o: o[0].update(exists=False)):
            observations = copy.deepcopy(self.observations)
            mutate(observations)
            with self.assertRaises(operations.OperationError):
                operations.create_plan('admin', '/apply', 'key', self.bundle, observations)

    def test_registry_and_secret_stale_before_any_send(self):
        plan = self.plan()
        self.observations[0]['resource_version'] = '2'
        with self.assertRaises(operations.OperationError):
            self.apply(plan)
        self.assertEqual(self.calls, [])
        self.observations[0]['resource_version'] = '1'
        with store.connect() as db:
            db.execute('UPDATE catalog_bindings SET desired_revision=2 WHERE database_id=?', ('opaque_0',))
        with self.assertRaises(operations.OperationError):
            self.apply(operations.inspect(plan['id'], actor='admin'))
        self.assertEqual(self.calls, [])

    def test_display_metadata_stales_frozen_plan_until_explicit_replan(self):
        import display_management
        import request_auth
        plan = self.plan('before-display')
        admin = request_auth.AuthenticatedPrincipal('admin', 'admin', True)
        with store.connect() as db:
            binding_before = dm.get_binding(db, 'opaque_0')
        display_management.mutate(admin, 'admin', 'database', 'opaque_0',
            dict(display_name='New label', description='', expected_revision=1), 'display')
        with self.assertRaises(operations.OperationError) as raised:
            self.apply(plan)
        self.assertEqual(raised.exception.code, 'STALE_REGISTRY')
        self.assertEqual(self.calls, [])
        with store.connect() as db:
            self.assertEqual(dm.get_binding(db, 'opaque_0'), binding_before)
        value = copy.deepcopy(self.spec)
        value['generation_id'] = 'explicit-display-replan'
        value['databases']['opaque_0']['database_revision'] = 2
        bundle = generation.render(value)
        replanned = self.plan('after-display', bundle)
        self.assertEqual(replanned['manifest']['spec']['databases']['opaque_0']['database_revision'], 2)

    def test_schema_grant_and_physical_binding_are_exact(self):
        for sql in ("UPDATE schemas SET purpose='changed' WHERE id='opaque_0_raw'",
                    "UPDATE grants SET maximum_role='data_engineer'",
                    "UPDATE catalog_bindings SET polaris_realm='OTHER' WHERE database_id='opaque_0'"):
            with store.connect() as db:
                db.execute('SAVEPOINT changed')
                db.execute(sql)
                with self.assertRaises(operations.OperationError):
                    operations._check_registry(db, self.bundle['manifest']['spec'])
                db.execute('ROLLBACK TO changed')

    def test_omitting_registered_binding_is_rejected_even_for_valid_p4_bundle(self):
        value = copy.deepcopy(self.spec)
        del value['databases']['opaque_1']
        value['shared_secret_refs']['polaris_bootstrap']['keys'] = [generation.bootstrap_key('opaque_0')]
        bundle = generation.render(value)
        refs = bundle['tofu']['bootstrap_secret_refs'] + bundle['tofu']['deferred_secret_refs']
        observations = [dict(ref, exists=False, uid=None, resource_version=None, immutable=None) for ref in refs]
        with self.assertRaises(operations.OperationError) as error:
            operations.create_plan('admin', '/warehouse/configuration', 'omitted', bundle, observations)
        self.assertEqual(error.exception.code, 'STALE_REGISTRY')
        self.assertEqual(self.calls, [])

    def test_durable_action_handoff_idempotency_and_original_operation_unchanged(self):
        plan = self.plan()
        route = '/api/v1/operations/' + plan['id'] + '/apply'
        payload = dict(expected_revision=plan['revision'], delivery_digest=plan['delivery_digest'])
        def request(value=payload):
            return operations.request_action(plan['id'], actor='admin', route=route,
                                             idempotency_key='apply-request', payload=value)
        saved = request()
        self.assertEqual(set(saved), {'id', 'operation_id', 'actor', 'route', 'payload', 'state', 'created_at', 'requires_operator'})
        self.assertEqual((saved['state'], saved['requires_operator']), ('REQUESTED', True))
        self.assertEqual(request(), saved)
        self.assertEqual(operations.inspect(plan['id'], actor='admin'), plan)
        self.assertEqual(operations.inspect_actions(plan['id'], actor='admin'), (saved,))
        self.assertEqual(self.calls, [])
        with store.connect() as db:
            db.execute('UPDATE operation_requests SET revision=revision+1 WHERE operation_id=?', (plan['id'],))
        self.assertEqual(request(), saved)
        with self.assertRaises(operations.OperationError) as error:
            request(dict(payload, expected_revision=plan['revision'] + 1))
        self.assertEqual(error.exception.status, 409)
        script = '''import json,sys,warehouse_operations as o
p=o.inspect(sys.argv[1],actor='admin')
r=o.request_action(p['id'],actor='admin',route=sys.argv[2],idempotency_key='apply-request',
 payload={'expected_revision':1,'delivery_digest':p['delivery_digest']})
print(json.dumps(r,sort_keys=True))
'''
        run = subprocess.run([sys.executable, '-c', script, plan['id'], route], capture_output=True, text=True, timeout=5)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), saved)

    def test_action_handoff_exact_payload_owner_admin_and_revision(self):
        plan = self.plan()
        base = '/api/v1/operations/' + plan['id']
        payload = dict(expected_revision=plan['revision'], delivery_digest=plan['delivery_digest'])
        invalid = [dict(payload, password='never-save-action-secret'), dict(payload, expected_revision=True),
                   dict(payload, expected_revision='1'), dict(expected_revision=1),
                   dict(payload, delivery_digest=42)]
        for value in invalid:
            with self.assertRaises(operations.OperationError):
                operations.request_action(plan['id'], actor='admin', route=base+'/apply', idempotency_key='bad', payload=value)
        for route, value in ((base+'/other', payload), (base+'/reconcile', payload),
                             (base+'/apply', dict(payload, expected_revision=2)),
                             (base+'/apply', dict(payload, delivery_digest='wrong'))):
            with self.assertRaises(operations.OperationError):
                operations.request_action(plan['id'], actor='admin', route=route, idempotency_key='bad', payload=value)
        for admin in (False, 1, 'true'):
            with self.assertRaises(operations.OperationError):
                operations.request_action(plan['id'], actor='other', is_admin=admin,
                    route=base+'/reconcile', idempotency_key='other', payload=dict(expected_revision=1))
            with self.assertRaises(operations.OperationError):
                operations.inspect_actions(plan['id'], actor='other', is_admin=admin)
        saved = operations.request_action(plan['id'], actor='other', is_admin=True,
            route=base+'/reconcile', idempotency_key='other', payload=dict(expected_revision=1))
        self.assertEqual(saved['actor'], 'other')
        self.assertEqual(operations.inspect_actions(plan['id'], actor='admin'), (saved,))
        self.assertEqual(operations.inspect(plan['id'], actor='admin'), plan)
        self.assertNotIn(b'never-save-action-secret', (self.directory/'jobs.sqlite3').read_bytes())

    def test_success_idempotent_and_unprovisioned_database_stays_unavailable(self):
        with store.connect() as db:
            db.execute("UPDATE databases SET state='registered' WHERE id='opaque_1'")
            # CAS metadata is deliberately independent of desired revision.
            db.execute('UPDATE catalog_bindings SET revision=8')
        plan = self.plan()
        result = self.apply(plan)
        self.assertEqual(result['state'], 'APPLIED')
        self.assertEqual(self.calls, list(operations.STEPS))
        self.assertEqual(self.apply(result), result)
        self.assertEqual(self.calls, list(operations.STEPS))
        with store.connect() as db:
            self.assertEqual(admission.control(db)['applied_generation_id'], plan['generation_id'])
            self.assertEqual(dict(db.execute('SELECT id,state FROM databases')), {'opaque_0': 'READY', 'opaque_1': 'registered'})
            self.assertEqual({r[0] for r in db.execute('SELECT desired_revision FROM catalog_bindings')}, {1})
        self.assertNotIn(b'never-store-this-secret', (self.directory/'jobs.sqlite3').read_bytes())

    def bootstrap(self):
        with store.connect() as db:
            db.execute("UPDATE databases SET state='registered' WHERE id='opaque_1'")
        plan = operations.create_plan('admin', '/warehouse/configuration', 'bootstrap',
            self.bundle, self.observations, phase='bootstrap')
        result = self.apply(plan)
        self.assertEqual(result['state'], 'PREPARED')
        self.assertNotIn('reconcile_trino', self.calls)
        with store.connect() as db:
            self.assertEqual(admission.control(db)['state'], 'APPLYING')
            self.assertEqual({r[0] for r in db.execute('SELECT applied_revision FROM catalog_bindings')}, {0})
            self.assertEqual(dict(db.execute('SELECT id,state FROM databases')),
                             {'opaque_0': 'reconciling', 'opaque_1': 'registered'})
        return result

    def test_bootstrap_fresh_full_same_generation_transfers_closed_gate_and_marks_ready(self):
        prepared = self.bootstrap()
        self.observations[0]['resource_version'] = '2'
        full = operations.create_plan('admin', '/warehouse/configuration', 'full', self.bundle,
            self.observations, phase='full', predecessor_id=prepared['id'])
        self.assertEqual(full['manifest'], prepared['manifest'])
        self.assertNotEqual(full['secret_observations'], prepared['secret_observations'])
        def execute(step, plan):
            with store.connect() as db:
                control = admission.control(db)
                self.assertNotEqual(control['state'], 'OPEN')
                self.assertEqual(control['operation_id'], full['id'])
            return self.execute(step, plan)
        result = self.apply(full, execute_step=execute)
        self.assertEqual(result['state'], 'APPLIED')
        with store.connect() as db:
            self.assertEqual({r[0] for r in db.execute('SELECT state FROM databases')}, {'READY'})
            self.assertEqual(admission.control(db)['state'], 'OPEN')

    def test_bootstrap_full_failed_verify_and_gate_timeout_never_open_or_ready(self):
        prepared = self.bootstrap()
        full = operations.create_plan('admin', '/warehouse/configuration', 'full', self.bundle,
            self.observations, phase='full', predecessor_id=prepared['id'])
        with patch.object(admission, 'lifecycle_gate', side_effect=admission.GateTimeout):
            timed = self.apply(full)
        self.assertEqual(timed['state'], 'AWAITING_APPLY')
        with store.connect() as db:
            self.assertNotEqual(admission.control(db)['state'], 'OPEN')
        # A failed verifier on explicit retry still holds the inherited reservation.
        failed = self.apply(timed, verify=lambda plan: dict(verified=False, evidence_ref='verify:failed'))
        self.assertEqual(failed['state'], 'UNKNOWN')
        with store.connect() as db:
            self.assertNotEqual(admission.control(db)['state'], 'OPEN')
            self.assertNotIn('READY', {r[0] for r in db.execute('SELECT state FROM databases')})

    def test_phase_and_predecessor_are_bound_to_idempotency_and_registry(self):
        prepared = self.bootstrap()
        with self.assertRaises(operations.OperationError):
            operations.create_plan('admin', '/warehouse/configuration', 'bootstrap', self.bundle,
                self.observations, phase='full')
        changed = copy.deepcopy(self.spec)
        changed['generation_id'] = 'other-generation'
        resealed = operations.create_plan('admin', '/warehouse/configuration', 'changed', generation.render(changed),
            self.observations, phase='full', predecessor_id=prepared['id'])
        self.assertEqual(resealed['generation_id'], 'other-generation')

    def undispatched_full(self, *, drain=False):
        prepared = self.bootstrap()
        full = operations.create_plan('admin', '/warehouse/configuration', 'timed-full', self.bundle,
            self.observations, predecessor_id=prepared['id'])
        if drain:
            with patch.object(admission, 'blockers', return_value=['accepted-job']):
                timed = self.apply(full, drain_timeout=0)
        else:
            with patch.object(admission, 'lifecycle_gate', side_effect=admission.GateTimeout):
                timed = self.apply(full)
        self.assertEqual(timed['state'], 'AWAITING_APPLY')
        self.assertEqual([row['state'] for row in timed['steps']], ['SUCCEEDED'] + ['PENDING'] * 5)
        self.calls.clear()
        return prepared, timed

    def assert_closed_owner(self, owner):
        with store.connect() as db:
            self.assertEqual(admission.control(db)['operation_id'], owner['id'])
            self.assertNotEqual(admission.control(db)['state'], 'OPEN')

    def fresh_full(self, prepared, key='fresh-full', **kwargs):
        arguments = dict(phase='full', predecessor_id=prepared['id'])
        arguments.update(kwargs)
        return operations.create_plan('admin', '/warehouse/configuration', key, self.bundle,
            self.observations, **arguments)

    def assert_undispatched_drift_replan(self, *, drain):
        prepared, timed = self.undispatched_full(drain=drain)
        self.observations[0]['resource_version'] = '2'
        with self.assertRaisesRegex(operations.OperationError, 'STALE_SECRETS'):
            self.apply(timed)
        with store.connect() as db:
            dm.update_grant(db, 'reader', 'opaque_0', 1, 'revoked')
        with self.assertRaisesRegex(operations.OperationError, 'STALE_REGISTRY'):
            self.apply(timed)
        value = copy.deepcopy(self.spec)
        value['generation_id'] = 'fresh-after-timeout'
        value['grant_revisions'][0].update(maximum_role='revoked', revision=2)
        self.bundle = generation.render(value)
        fresh = self.fresh_full(prepared)
        self.assert_closed_owner(timed)
        self.assertEqual(operations.inspect(timed['id'], actor='admin'), timed)
        def execute(step, plan):
            self.assert_closed_owner(fresh)
            self.assertEqual(operations.inspect(timed['id'], actor='admin')['state'], 'REPLAN_REQUIRED')
            return self.execute(step, plan)
        self.assertEqual(self.apply(fresh, execute_step=execute)['state'], 'APPLIED')
        self.assertEqual(self.calls, list(operations.STEPS))
        with store.connect() as db:
            self.assertEqual(admission.control(db)['state'], 'OPEN')
            self.assertEqual({r[0] for r in db.execute('SELECT state FROM databases')}, {'READY'})
            self.assertEqual(dm.get_grant(db, 'reader', 'opaque_0').maximum_role, 'revoked')

    def test_undispatched_gate_timeout_drift_allows_explicit_fresh_full(self):
        self.assert_undispatched_drift_replan(drain=False)

    def test_undispatched_drain_timeout_drift_allows_explicit_fresh_full(self):
        self.assert_undispatched_drift_replan(drain=True)

    def test_undispatched_handoff_rejects_uncertain_leaves_publication_and_active_owner(self):
        prepared, timed = self.undispatched_full()
        invalid = [(step, state, 'ack:step') for step in operations._ALL_STEPS[1:]
                   for state in ('RUNNING', 'UNKNOWN', 'SUCCEEDED')]
        invalid += [('publish_snapshot', state, receipt) for state, receipt in
                    (('RUNNING', None), ('UNKNOWN', None), ('SUCCEEDED', None), ('SUCCEEDED', 'bad receipt'))]
        for step, state, receipt in invalid:
            with self.subTest(step=step, state=state, receipt=receipt), store.connect() as db:
                db.execute('SAVEPOINT changed')
                db.execute('UPDATE operation_steps SET state=?,evidence_ref=? WHERE operation_id=? AND step=?',
                           (state, receipt, timed['id'], step))
                prior = operations._view(db, prepared['id'], 'admin')
                with self.assertRaisesRegex(operations.OperationError, 'INVALID_PREPARED_PREDECESSOR'):
                    operations._prepared_reservation(db, prior, self.bundle['manifest'], 'admin')
                db.execute('ROLLBACK TO changed')
        with store.connect() as db:
            db.execute("UPDATE operation_requests SET attempt_id='active' WHERE operation_id=?", (timed['id'],))
        with self.assertRaisesRegex(operations.OperationError, 'INVALID_PREPARED_PREDECESSOR'):
            self.fresh_full(prepared)
        self.assert_closed_owner(timed)

    def test_undispatched_pending_publication_allows_fresh_plan(self):
        prepared, timed = self.undispatched_full()
        # Trusted reconciliation can durably confirm a publication was not sent.
        with store.connect() as db:
            db.execute("UPDATE operation_steps SET state='PENDING',evidence_ref='dispatch:not-sent' WHERE operation_id=? AND step='publish_snapshot'",
                       (timed['id'],))
        fresh = self.fresh_full(prepared)
        self.assert_closed_owner(timed)
        self.assertEqual(self.apply(fresh)['state'], 'APPLIED')

    def test_undispatched_handoff_preserves_actor_predecessor_and_physical_identity(self):
        prepared, timed = self.undispatched_full()
        with self.assertRaisesRegex(operations.OperationError, 'OPERATION_NOT_FOUND'):
            operations.create_plan('other', '/warehouse/configuration', 'other', self.bundle,
                self.observations, predecessor_id=prepared['id'])
        for field, value in (('predecessor_id', timed['id']), ('actor', 'other')):
            with self.subTest(field=field), store.connect() as db:
                db.execute('SAVEPOINT changed')
                table = 'operation_requests' if field == 'predecessor_id' else 'operations'
                db.execute(f'UPDATE {table} SET {field}=? WHERE ' + ('operation_id=?' if field == 'predecessor_id' else 'id=?'),
                           (value, timed['id']))
                with self.assertRaises(operations.OperationError):
                    operations._prepared_reservation(db, prepared, self.bundle['manifest'], 'admin')
                db.execute('ROLLBACK TO changed')
        changed = copy.deepcopy(self.spec)
        changed['generation_id'] = 'changed-physical-after-timeout'
        changed['databases']['opaque_0']['binding']['polaris_service_ref'] = 'different-service'
        with store.connect() as db:
            db.execute("UPDATE catalog_bindings SET polaris_service_ref='different-service' WHERE database_id='opaque_0'")
        with self.assertRaisesRegex(operations.OperationError, 'PREPARED_PHYSICAL_CHANGE_REQUIRES_RECOVERY'):
            operations.create_plan('admin', '/warehouse/configuration', 'physical-timeout', generation.render(changed),
                self.observations, predecessor_id=prepared['id'])
        self.assert_closed_owner(timed)

    def test_undispatched_transfer_checks_authorization_registry_and_secrets_first(self):
        prepared, timed = self.undispatched_full()
        fresh = self.fresh_full(prepared)
        with self.assertRaisesRegex(operations.OperationError, 'OPERATION_FORBIDDEN'):
            self.apply(fresh, authorize=lambda actor: False)
        self.assert_closed_owner(timed)
        self.observations[0]['resource_version'] = '2'
        with self.assertRaisesRegex(operations.OperationError, 'STALE_SECRETS'):
            self.apply(fresh)
        self.assert_closed_owner(timed)
        self.observations[0]['resource_version'] = '1'
        def registry_drift(plan):
            with store.connect() as db:
                dm.update_grant(db, 'reader', 'opaque_0', 1, 'revoked')
            return copy.deepcopy(self.observations)
        with self.assertRaisesRegex(operations.OperationError, 'STALE_REGISTRY'):
            self.apply(fresh, observe_secrets=registry_drift)
        self.assert_closed_owner(timed)
        self.assertEqual(self.calls, [])

    def test_undispatched_transfer_rechecks_active_owner_after_preflight(self):
        prepared, timed = self.undispatched_full()
        fresh = self.fresh_full(prepared)
        def concurrent_owner(plan):
            with store.connect() as db:
                db.execute("UPDATE operation_requests SET attempt_id='concurrent-attempt' WHERE operation_id=?", (timed['id'],))
            return copy.deepcopy(self.observations)
        with self.assertRaisesRegex(operations.OperationError, 'INVALID_PREPARED_PREDECESSOR'):
            self.apply(fresh, observe_secrets=concurrent_owner)
        self.assert_closed_owner(timed)
        self.assertEqual(self.calls, [])

    def test_undispatched_superseded_operation_cannot_reclaim_new_owner(self):
        prepared, timed = self.undispatched_full()
        fresh = self.fresh_full(prepared)
        with patch.object(admission, 'lifecycle_gate', side_effect=admission.GateTimeout):
            replacement = self.apply(fresh)
        self.assert_closed_owner(replacement)
        self.calls.clear()
        with self.assertRaises(operations.OperationError):
            self.apply(timed)
        superseded = operations.inspect(timed['id'], actor='admin')
        self.assertEqual(superseded['state'], 'REPLAN_REQUIRED')
        with self.assertRaisesRegex(operations.OperationError, 'RECONCILIATION_REQUIRED'):
            self.apply(superseded)
        self.assert_closed_owner(replacement)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.apply(replacement)['state'], 'APPLIED')

    def test_undispatched_old_apply_preflight_cannot_race_explicit_transfer(self):
        prepared, timed = self.undispatched_full()
        fresh = self.fresh_full(prepared)
        def transfer_during_observation(plan):
            # Model another explicit apply winning while the old caller is
            # outside its transaction. The production operator lock serializes
            # operators too; the durable revision must independently reject it.
            with patch.object(admission, 'lifecycle_gate', side_effect=admission.GateTimeout):
                replacement = operations._apply(fresh['id'], actor='admin', expected_revision=fresh['revision'],
                    delivery_digest=fresh['delivery_digest'], authorize=lambda actor: True,
                    observe_secrets=lambda p: copy.deepcopy(self.observations), execute_step=self.execute,
                    verify=lambda p: dict(verified=True, evidence_ref='verify:ok'), drain_timeout=0, gate_timeout=0)
            self.assert_closed_owner(replacement)
            return copy.deepcopy(self.observations)
        with self.assertRaisesRegex(operations.OperationError, 'REVISION_CONFLICT'):
            self.apply(timed, observe_secrets=transfer_during_observation)
        self.assert_closed_owner(fresh)
        self.assertEqual(self.calls, ['publish_snapshot'])

    def test_prepared_grant_desired_and_secret_drift_explicit_full_replan_keeps_closed(self):
        prepared = self.bootstrap()
        stale = operations.create_plan('admin', '/warehouse/configuration', 'stale-full', self.bundle,
            self.observations, predecessor_id=prepared['id'])
        with store.connect() as db:
            db.execute("UPDATE catalog_bindings SET desired_revision=2 WHERE database_id='opaque_0'")
            dm.update_grant(db, 'reader', 'opaque_0', 1, 'revoked')
        with self.assertRaisesRegex(operations.OperationError, 'STALE_REGISTRY'):
            self.apply(stale)
        value = copy.deepcopy(self.spec)
        value['generation_id'] = 'approved-after-drift'
        value['databases']['opaque_0']['binding']['desired_revision'] = 2
        value['grant_revisions'][0].update(maximum_role='revoked', revision=2)
        value['databases']['opaque_0']['secret_refs']['jwt'].update(name='replacement-jwt-v2', version='v2')
        bundle = generation.render(value)
        refs = bundle['tofu']['bootstrap_secret_refs']+bundle['tofu']['deferred_secret_refs']
        self.observations = [dict(ref, exists=True, uid='current-'+ref['name'], resource_version='9', immutable=True) for ref in refs]
        full = operations.create_plan('admin', '/warehouse/configuration', 'approved-full', bundle,
            self.observations, predecessor_id=prepared['id'])
        failed = self.apply(full, verify=lambda p: dict(verified=False, evidence_ref='verify:failed'))
        self.assertEqual(failed['state'], 'UNKNOWN')
        with store.connect() as db:
            self.assertNotEqual(admission.control(db)['state'], 'OPEN')
            self.assertEqual(dm.get_grant(db, 'reader', 'opaque_0').maximum_role, 'revoked')
        with self.assertRaises(operations.OperationError):
            operations.create_plan('admin', '/warehouse/configuration', 'cannot-take-unknown', bundle,
                self.observations, predecessor_id=prepared['id'])
        resumed = operations.reconcile(failed['id'], actor='admin', evidence=operations.ReconciliationEvidence(
            'verify', 'SUCCEEDED', 'verify:trusted-observation', 'backend_ack'))
        self.assertEqual(self.apply(resumed)['state'], 'APPLIED')
        with store.connect() as db:
            self.assertEqual(dm.get_binding(db, 'opaque_0').applied_revision, 2)
            self.assertEqual(dm.get_grant(db, 'reader', 'opaque_0').maximum_role, 'revoked')

    def test_prepared_handoff_rejects_physical_change_with_actionable_code(self):
        prepared = self.bootstrap()
        value = copy.deepcopy(self.spec)
        value['generation_id'] = 'physical-change'
        value['databases']['opaque_0']['binding']['polaris_service_ref'] = 'changed-service'
        with store.connect() as db:
            db.execute("UPDATE catalog_bindings SET polaris_service_ref='changed-service' WHERE database_id='opaque_0'")
        with self.assertRaisesRegex(operations.OperationError, 'PREPARED_PHYSICAL_CHANGE_REQUIRES_RECOVERY'):
            operations.create_plan('admin', '/warehouse/configuration', 'physical', generation.render(value),
                self.observations, predecessor_id=prepared['id'])
        with store.connect() as db:
            self.assertEqual(admission.control(db)['operation_id'], prepared['id'])
            self.assertNotEqual(admission.control(db)['state'], 'OPEN')

    def test_prepared_full_post_verify_drift_keeps_closed_until_explicit_fresh_full(self):
        prepared = self.bootstrap()
        full = operations.create_plan('admin', '/warehouse/configuration', 'first-full', self.bundle,
            self.observations, predecessor_id=prepared['id'])
        def stale_verify(plan):
            with store.connect() as db:
                db.execute("UPDATE catalog_bindings SET desired_revision=2 WHERE database_id='opaque_0'")
                dm.update_grant(db, 'reader', 'opaque_0', 1, 'revoked')
            return dict(verified=True, evidence_ref='verify:positive-before-drift')
        stale = self.apply(full, verify=stale_verify)
        self.assertEqual(stale['state'], 'VERIFIED_STALE')
        with store.connect() as db:
            self.assertEqual(admission.control(db)['state'], 'APPLYING')
            self.assertEqual(admission.control(db)['operation_id'], full['id'])
            self.assertNotIn('READY', {r[0] for r in db.execute('SELECT state FROM databases')})
        value = copy.deepcopy(self.spec)
        value['generation_id'] = 'fresh-after-post-verify-drift'
        value['databases']['opaque_0']['binding']['desired_revision'] = 2
        value['grant_revisions'][0].update(maximum_role='revoked', revision=2)
        next_full = operations.create_plan('admin', '/warehouse/configuration', 'fresh-full', generation.render(value),
            self.observations, predecessor_id=prepared['id'])
        self.assertEqual(self.apply(next_full)['state'], 'APPLIED')
        with store.connect() as db:
            self.assertEqual(admission.control(db)['state'], 'OPEN')
            self.assertEqual(dm.get_grant(db, 'reader', 'opaque_0').maximum_role, 'revoked')

    def test_arbitrary_third_create_exports_identity_and_keeps_existing_tables_bindings_refs(self):
        import catalog_admin
        import request_auth
        admin = request_auth.AuthenticatedPrincipal('admin', 'admin', True)
        payload = dict(sql_catalog='customer_research_2026', profile_id=generation.PROFILE_ID)
        with store.connect() as db:
            table = dm.observe_object(db, 'opaque_0_raw', 'old_table', 'TABLE', 'stable-backend-id', 'Old table')
            before = [tuple(r) for r in db.execute('SELECT * FROM catalog_bindings ORDER BY database_id')]
        with patch.dict(sys.modules, yaml=None, boto3=None):
            created = catalog_admin.create_database(admin, 'admin', payload, 'third')
            self.assertEqual(created, catalog_admin.create_database(admin, 'admin', payload, 'third'))
            inspected = catalog_admin.inspect_operation(admin, 'admin', created['operation_id'])
            self.assertEqual(inspected['database_id'], created['database_id'])
        with self.assertRaises(operations.OperationError):
            catalog_admin.create_database(admin, 'admin', dict(payload, sql_catalog='changed'), 'third')
        for bad in (dict(payload, endpoint='https://example.invalid'), dict(payload, secrets={}), dict(payload, profile_id='other'),
                    dict(payload, sql_catalog='iceberg')):
            with self.assertRaises(operations.OperationError):
                catalog_admin.create_database(admin, 'admin', bad, 'bad')
        exported = catalog_admin.export_spec('third-generation', self.spec)
        self.assertEqual(exported, catalog_admin.export_spec('third-generation', self.spec))
        for key, old in self.spec['databases'].items():
            self.assertEqual(exported['databases'][key], generation.validate_spec(self.spec)['databases'][key])
        with store.connect() as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM catalog_bindings WHERE database_id != ? ORDER BY database_id',
                (created['database_id'],))], before)
            self.assertEqual(dm.get_object(db, table.id), table)
            self.assertEqual(db.execute('SELECT count(*) FROM schemas WHERE database_id=?', (created['database_id'],)).fetchone()[0], 4)
            self.assertEqual(db.execute('SELECT count(*) FROM database_create_requests').fetchone()[0], 1)
        bundle = generation.render(exported)
        refs = bundle['tofu']['bootstrap_secret_refs']+bundle['tofu']['deferred_secret_refs']
        observations = [dict(r, exists=False, uid=None, resource_version=None, immutable=None) for r in refs]
        with self.assertRaisesRegex(operations.OperationError, 'PREPARED_PREDECESSOR_REQUIRED'):
            operations.create_plan('admin', '/warehouse/configuration', 'skip-bootstrap', bundle, observations)

    def test_create_failure_rolls_back_all_identity_and_operation_rows(self):
        import catalog_admin
        import request_auth
        before = None
        with store.connect() as db:
            before = db.execute('SELECT count(*) FROM databases').fetchone()[0]
        with patch.object(dm, 'create_schema', side_effect=ValueError('failure')):
            with self.assertRaises(ValueError):
                catalog_admin.create_database(request_auth.AuthenticatedPrincipal('admin', 'admin', True), 'admin',
                    dict(sql_catalog='rollback_database', profile_id=generation.PROFILE_ID), 'rollback')
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM databases').fetchone()[0], before)
            self.assertEqual(db.execute('SELECT count(*) FROM database_create_requests').fetchone()[0], 0)

    def test_timeout_reopens_and_publication_is_not_replayed(self):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            parent = admission.admit(db, actor='jobber', kind='job', subject_id='whole-job')
        plan = self.plan()
        result = self.apply(plan, drain_timeout=0)
        self.assertEqual(result['state'], 'AWAITING_APPLY')
        self.assertEqual(self.calls, ['publish_snapshot'])
        with store.connect() as db:
            self.assertEqual(admission.control(db)['state'], 'OPEN')
            db.execute('BEGIN IMMEDIATE')
            admission.finish(db, parent, state='FINISHED')
        result = self.apply(result)
        self.assertEqual(result['state'], 'APPLIED')
        self.assertEqual(self.calls.count('publish_snapshot'), 1)

    def test_drain_allows_admitted_whole_operation_to_finish(self):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            parent = admission.admit(db, actor='jobber', kind='job', subject_id='whole-job')
        completed = []
        def continue_job():
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with store.connect() as db:
                    draining = admission.control(db)['state'] == 'DRAINING'
                if draining:
                    with admission.accepted(parent):
                        with store.connect() as db:
                            db.execute('BEGIN IMMEDIATE')
                            child = admission.admit(db, actor='jobber', kind='query', subject_id='next-step')
                            admission.finish(db, child, state='FINISHED')
                            admission.finish(db, parent, state='FINISHED')
                    completed.append(True)
                    return
                time.sleep(.01)
        thread = threading.Thread(target=continue_job)
        thread.start()
        try:
            self.assertEqual(self.apply(self.plan(), drain_timeout=5)['state'], 'APPLIED')
        finally:
            thread.join(6)
        self.assertEqual(completed, [True])

    def test_gate_timeout_is_bounded_and_reopens_before_mutation(self):
        script = ('import sys,time,warehouse_admission as a\n'
                  'with a.lifecycle_gate():\n print("locked",flush=True)\n sys.stdin.read()')
        process = subprocess.Popen([sys.executable, '-c', script], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'locked')
            result = self.apply(self.plan(), gate_timeout=.02)
            self.assertEqual(result['state'], 'AWAITING_APPLY')
            self.assertEqual(self.calls, ['publish_snapshot'])
            with store.connect() as db:
                self.assertEqual(admission.control(db)['state'], 'OPEN')
        finally:
            process.communicate('', timeout=5)

    def test_uncertain_callback_never_reposts_and_trusted_reconciliation_resumes(self):
        def lost(step, plan):
            if step == 'reconcile_storage':
                raise OSError('private response body must not be stored')
            return self.execute(step, plan)
        result = self.apply(self.plan(), execute_step=lost)
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertEqual(result['steps'][2]['state'], 'UNKNOWN')
        with self.assertRaises(operations.OperationError):
            self.apply(result)
        for evidence in ({'step': 'reconcile_storage', 'outcome': 'SUCCEEDED'},
                         operations.ReconciliationEvidence('reconcile_storage', 'NOT_EXECUTED', 'notfound', 'backend_ack'),
                         operations.ReconciliationEvidence('reconcile_storage', 'SUCCEEDED', 'notfound', 'not_found')):
            with self.assertRaises(operations.OperationError):
                operations.reconcile(result['id'], actor='admin', evidence=evidence)
        result = operations.reconcile(result['id'], actor='admin', evidence=operations.ReconciliationEvidence(
            'reconcile_storage', 'SUCCEEDED', 'observed:storage', 'observed_configuration'))
        self.assertEqual(result['state'], 'AWAITING_APPLY')
        with store.connect() as db:
            self.assertNotEqual(admission.control(db)['state'], 'OPEN')
        result = self.apply(result)
        self.assertEqual(result['state'], 'APPLIED')
        self.assertEqual(self.calls, ['publish_snapshot', 'apply_secrets', 'reconcile_polaris', 'reconcile_trino'])

    def test_confirmed_not_dispatched_retries_only_that_step(self):
        result = self.apply(self.plan(), execute_step=lambda step, plan: None)
        result = operations.reconcile(result['id'], actor='admin', evidence=operations.ReconciliationEvidence(
            'publish_snapshot', 'NOT_EXECUTED', 'dispatch:never-sent', 'confirmed_not_dispatched'))
        self.assertEqual(self.apply(result)['state'], 'APPLIED')
        self.assertEqual(self.calls, list(operations.STEPS))

    def test_desired_drift_during_callbacks_never_ready(self):
        def verify(plan):
            with store.connect() as db:
                db.execute("UPDATE catalog_bindings SET desired_revision=2 WHERE database_id='opaque_0'")
            return dict(verified=True, evidence_ref='verify:observed')
        result = self.apply(self.plan(), verify=verify)
        self.assertEqual(result['state'], 'VERIFIED_STALE')
        with store.connect() as db:
            self.assertIsNone(admission.control(db)['applied_generation_id'])
            self.assertEqual(admission.control(db)['state'], 'OPEN')
            self.assertIsNone(admission.control(db)['operation_id'])
            self.assertNotIn('READY', [r[0] for r in db.execute('SELECT state FROM databases')])
            self.assertEqual(db.execute("SELECT evidence_ref FROM operation_steps WHERE step='verify'").fetchone()[0], 'verify:observed')

    def test_grant_drift_after_dispatch_never_ready(self):
        def verify(plan):
            with store.connect() as db:
                dm.update_grant(db, 'reader', 'opaque_0', 1, 'data_engineer')
            return dict(verified=True, evidence_ref='verify:observed')
        self.assertEqual(self.apply(self.plan(), verify=verify)['state'], 'VERIFIED_STALE')
        with store.connect() as db:
            self.assertNotIn('READY', [r[0] for r in db.execute('SELECT state FROM databases')])

    def test_false_verify_and_ambiguous_results_are_unknown(self):
        result = self.apply(self.plan(), verify=lambda plan: dict(verified=False, evidence_ref='verify:no'))
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertEqual(result['steps'][-1]['state'], 'UNKNOWN')
        result = operations.reconcile(result['id'], actor='admin', evidence=operations.ReconciliationEvidence(
            'verify', 'SUCCEEDED', 'observe:ready', 'backend_ack'))
        self.assertEqual(result['state'], 'AWAITING_APPLY')
        verified = []
        result = self.apply(result, verify=lambda p: (verified.append(True) or dict(verified=True, evidence_ref='verify:fresh')))
        self.assertEqual((result['state'], verified), ('APPLIED', [True]))

    def test_new_secrets_may_appear_after_own_success_without_changing_plan_snapshot(self):
        for item in self.observations:
            item.update(exists=False, uid=None, resource_version=None, immutable=None)
        plan = self.plan()
        def execute(step, plan):
            if step == 'apply_secrets':
                for item in self.observations:
                    item.update(exists=True, uid='new-'+item['name'], resource_version='1', immutable=True)
            return self.execute(step, plan)
        result = self.apply(plan, execute_step=execute)
        self.assertEqual(result['state'], 'APPLIED')
        self.assertEqual(result['secret_observations'], plan['secret_observations'])

    def test_own_secret_resource_version_update_is_captured_once(self):
        plan = self.plan()
        def execute(step, plan):
            if step == 'apply_secrets':
                self.observations[0]['resource_version'] = '2'
            return self.execute(step, plan)
        result = self.apply(plan, execute_step=execute)
        self.assertEqual(result['state'], 'APPLIED')
        self.assertEqual(result['secret_observations'], plan['secret_observations'])
        with store.connect() as db:
            baseline = json.loads(db.execute('SELECT secret_after FROM operation_requests WHERE operation_id=?', (plan['id'],)).fetchone()[0])
        self.assertIn('2', [row['resource_version'] for row in baseline])

    def test_own_secret_changed_uid_is_not_allowed(self):
        def execute(step, plan):
            if step == 'apply_secrets':
                self.observations[0]['uid'] = 'replacement-uid'
            return self.execute(step, plan)
        result = self.apply(self.plan(), execute_step=execute)
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertEqual(result['steps'][-1]['state'], 'UNKNOWN')
        self.assertEqual(self.calls, ['publish_snapshot', 'apply_secrets'])
        with store.connect() as db:
            self.assertEqual(admission.control(db)['state'], 'UNKNOWN')

    def test_later_secret_creation_or_resource_version_drift_is_rejected(self):
        plan = self.plan()
        def execute(step, plan):
            if step in ('apply_secrets', 'reconcile_storage'):
                self.observations[0]['resource_version'] = '2' if step == 'apply_secrets' else '3'
            return self.execute(step, plan)
        result = self.apply(plan, execute_step=execute)
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertEqual(result['steps'][-1]['state'], 'UNKNOWN')
        self.assertEqual(self.calls, ['publish_snapshot', 'apply_secrets', 'reconcile_storage'])

    def test_new_secret_late_creation_and_missing_at_verify_are_rejected(self):
        for item in self.observations:
            item.update(exists=False, uid=None, resource_version=None, immutable=None)
        def execute(step, plan):
            if step == 'reconcile_storage':
                self.observations[0].update(exists=True, uid='late', resource_version='1', immutable=True)
            return self.execute(step, plan)
        result = self.apply(self.plan(), execute_step=execute)
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertEqual(result['steps'][-1]['state'], 'UNKNOWN')
        self.assertNotIn('reconcile_polaris', self.calls)

    def test_missing_new_secret_cannot_pass_verifier(self):
        for item in self.observations:
            item.update(exists=False, uid=None, resource_version=None, immutable=None)
        result = self.apply(self.plan())
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertEqual(result['steps'][-1]['state'], 'UNKNOWN')
        with store.connect() as db:
            self.assertNotEqual(admission.control(db)['state'], 'OPEN')
            self.assertIsNone(admission.control(db)['applied_generation_id'])

    def _assert_observation_handoff(self, result):
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertEqual(result['steps'][-1]['state'], 'UNKNOWN')
        original_steps = result['steps'][:-1]
        unknown = operations.ReconciliationEvidence('verify', 'UNKNOWN', 'observe:uncertain', 'observed_configuration')
        result = operations.reconcile(result['id'], actor='admin', evidence=unknown)
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertEqual(result['steps'][:-1], original_steps)
        for evidence in ({'step': 'verify', 'outcome': 'SUCCEEDED', 'basis': 'observed_configuration'},
                         operations.ReconciliationEvidence('verify', 'NOT_EXECUTED', 'observe:not-sent', 'confirmed_not_dispatched'),
                         operations.ReconciliationEvidence('verify', 'SUCCEEDED', 'ack:dispatch-only', 'backend_ack')):
            with self.assertRaises(operations.OperationError):
                operations.reconcile(result['id'], actor='admin', evidence=evidence)
        with store.connect() as db:
            self.assertEqual(admission.control(db)['state'], 'UNKNOWN')
            self.assertEqual(admission.control(db)['operation_id'], result['id'])
        observed = operations.ReconciliationEvidence('verify', 'SUCCEEDED', 'observe:actual-configuration', 'observed_configuration')
        with self.assertRaises(operations.OperationError):
            operations.reconcile(result['id'], actor='reviewer', evidence=observed)
        result = operations.reconcile(result['id'], actor='reviewer', is_admin=True, evidence=observed)
        self.assertEqual(result['state'], 'VERIFIED_STALE')
        self.assertEqual(result['steps'][:-1], original_steps)
        self.assertEqual(result['steps'][-1]['evidence_ref'], 'observe:actual-configuration')
        with store.connect() as db:
            self.assertIsNone(admission.control(db)['operation_id'])
            self.assertIsNone(admission.control(db)['applied_generation_id'])
            self.assertEqual(admission.control(db)['state'], 'OPEN')
            self.assertEqual(dm.get_generation(db, result['generation_id']).state, 'VERIFIED_STALE')
            self.assertEqual({row[0] for row in db.execute('SELECT applied_revision FROM catalog_bindings')}, {0})
            self.assertEqual({row[0] for row in db.execute('SELECT applied_generation_id FROM catalog_bindings')}, {None})
            self.assertEqual(dict(db.execute('SELECT id,state FROM databases')), {'opaque_0': 'reconciling', 'opaque_1': 'registered'})
        value = copy.deepcopy(self.spec)
        value['generation_id'] = 'observed-replan'
        plan = self.plan('observed-replan', generation.render(value))
        calls_before = list(self.calls)
        def execute(step, plan):
            if step == 'apply_secrets':
                for item in self.observations:
                    if not item['exists']:
                        item.update(exists=True, uid='new-'+item['name'], resource_version='1', immutable=True)
            return self.execute(step, plan)
        self.assertEqual(self.apply(plan, execute_step=execute)['state'], 'APPLIED')
        self.assertEqual(self.calls[:len(calls_before)], calls_before)
        with store.connect() as db:
            self.assertEqual(dict(db.execute('SELECT id,state FROM databases')), {'opaque_0': 'READY', 'opaque_1': 'registered'})
            self.assertEqual(admission.control(db)['applied_generation_id'], 'observed-replan')

    def test_post_step_uid_drift_can_handoff_only_after_trusted_configuration_observation(self):
        with store.connect() as db:
            db.execute("UPDATE databases SET state='registered' WHERE id='opaque_1'")
        def execute(step, plan):
            if step == 'apply_secrets':
                self.observations[0]['uid'] = 'replacement-uid'
            return self.execute(step, plan)
        result = self.apply(self.plan(), execute_step=execute)
        self.assertEqual(self.calls, ['publish_snapshot', 'apply_secrets'])
        self._assert_observation_handoff(result)

    def test_later_rv_drift_can_handoff_without_claiming_unfinished_steps_succeeded(self):
        with store.connect() as db:
            db.execute("UPDATE databases SET state='registered' WHERE id='opaque_1'")
        def execute(step, plan):
            if step == 'reconcile_storage':
                self.observations[0]['resource_version'] = '2'
            return self.execute(step, plan)
        result = self.apply(self.plan(), execute_step=execute)
        self.assertEqual(self.calls, ['publish_snapshot', 'apply_secrets', 'reconcile_storage'])
        self._assert_observation_handoff(result)

    def test_missing_secret_after_positive_verifier_still_requires_trusted_handoff(self):
        with store.connect() as db:
            db.execute("UPDATE databases SET state='registered' WHERE id='opaque_1'")
        for item in self.observations:
            item.update(exists=False, uid=None, resource_version=None, immutable=None)
        result = self.apply(self.plan())
        self.assertEqual(self.calls, list(operations.STEPS))
        self._assert_observation_handoff(result)

    def test_reconciled_apply_secrets_captures_baseline_once_without_replay(self):
        def execute(step, plan):
            if step == 'apply_secrets':
                self.observations[0]['resource_version'] = '2'
                return None
            return self.execute(step, plan)
        result = self.apply(self.plan(), execute_step=execute)
        self.assertEqual(result['state'], 'UNKNOWN')
        with store.connect() as db:
            self.assertIsNone(db.execute('SELECT secret_after FROM operation_requests WHERE operation_id=?', (result['id'],)).fetchone()[0])
        result = operations.reconcile(result['id'], actor='admin', evidence=operations.ReconciliationEvidence(
            'apply_secrets', 'SUCCEEDED', 'observed:secrets', 'observed_configuration'))
        self.assertEqual(self.apply(result)['state'], 'APPLIED')
        self.assertNotIn('apply_secrets', self.calls)

    def test_verified_stale_replan_restores_only_known_provisioned_database(self):
        with store.connect() as db:
            db.execute("UPDATE databases SET state='registered' WHERE id='opaque_1'")
        def verify(plan):
            with store.connect() as db:
                db.execute("UPDATE catalog_bindings SET desired_revision=2 WHERE database_id='opaque_0'")
            return dict(verified=True, evidence_ref='verify:observed')
        plan = self.plan()
        result = self.apply(plan, verify=verify)
        self.assertEqual(result['state'], 'VERIFIED_STALE')
        with store.connect() as db:
            self.assertIsNone(admission.control(db)['operation_id'])
            self.assertIsNone(admission.control(db)['applied_generation_id'])
            self.assertEqual(db.execute('SELECT state FROM warehouse_config_generations WHERE id=?', (plan['generation_id'],)).fetchone()[0], 'VERIFIED_STALE')
            self.assertEqual({row[0] for row in db.execute('SELECT applied_revision FROM catalog_bindings')}, {0})
            self.assertEqual({row[0] for row in db.execute('SELECT applied_generation_id FROM catalog_bindings')}, {None})
            self.assertNotIn('READY', [row[0] for row in db.execute('SELECT state FROM databases')])
        value = copy.deepcopy(self.spec)
        value['generation_id'] = 'generation-two'
        value['databases']['opaque_0']['binding']['desired_revision'] = 2
        next_plan = self.plan('replan', generation.render(value))
        self.assertEqual(self.apply(next_plan)['state'], 'APPLIED')
        with store.connect() as db:
            self.assertEqual(dict(db.execute('SELECT id,state FROM databases')), {'opaque_0': 'READY', 'opaque_1': 'registered'})
            self.assertEqual(admission.control(db)['applied_generation_id'], 'generation-two')

    def test_observation_exception_after_mutation_is_unknown_without_private_error(self):
        count = []
        def observe(plan):
            count.append(True)
            if len(count) == 3:
                raise OSError('secret-observation-private-message')
            return copy.deepcopy(self.observations)
        result = self.apply(self.plan(), observe_secrets=observe)
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertEqual(result['steps'][1]['state'], 'SUCCEEDED')
        self.assertEqual(result['steps'][-1]['state'], 'UNKNOWN')
        result = operations.reconcile(result['id'], actor='admin', evidence=operations.ReconciliationEvidence(
            'verify', 'NOT_EXECUTED', 'dispatch:verify-not-sent', 'confirmed_not_dispatched'))
        self.assertEqual(self.apply(result)['state'], 'APPLIED')
        self.assertEqual(self.calls.count('apply_secrets'), 1)
        self.assertNotIn(b'secret-observation-private-message', (self.directory/'jobs.sqlite3').read_bytes())

    def test_partial_resume_gate_timeout_stays_closed(self):
        def uncertain(step, plan):
            if step == 'reconcile_storage':
                return None
            return self.execute(step, plan)
        result = self.apply(self.plan(), execute_step=uncertain)
        result = operations.reconcile(result['id'], actor='admin', evidence=operations.ReconciliationEvidence(
            'reconcile_storage', 'NOT_EXECUTED', 'dispatch:never-sent', 'confirmed_not_dispatched'))
        script = ('import sys,warehouse_admission as a\nwith a.lifecycle_gate():\n'
                  ' print("locked",flush=True)\n sys.stdin.read()')
        process = subprocess.Popen([sys.executable, '-c', script], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'locked')
            result = self.apply(result, gate_timeout=.02)
            self.assertEqual(result['state'], 'AWAITING_APPLY')
            with store.connect() as db:
                self.assertEqual(admission.control(db)['state'], 'UNKNOWN')
        finally:
            process.communicate('', timeout=5)
        self.assertEqual(self.apply(result)['state'], 'APPLIED')

    def test_authorization_is_rechecked_at_final_boundary(self):
        checks = []
        def authorize(actor):
            checks.append(actor)
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
            return len(checks) == 1
        with self.assertRaises(operations.OperationError):
            self.apply(self.plan(), authorize=authorize)
        self.assertEqual(self.calls, ['publish_snapshot'])

    def test_two_process_apply_reservation_and_maintenance_race(self):
        plan = self.plan()
        other = self.plan('other')
        script = '''import sys,warehouse_operations as o
p=o.inspect(sys.argv[1],actor='admin')
def send(step,plan):
 if step=='publish_snapshot':
  print('publication-reserved',flush=True)
  sys.stdin.readline()
 return {'evidence_ref':'ack:'+step}
r=o.apply(p['id'],actor='admin',expected_revision=p['revision'],delivery_digest=p['delivery_digest'],
 authorize=lambda actor:True,observe_secrets=lambda plan:p['secret_observations'],execute_step=send,
 verify=lambda plan:{'verified':True,'evidence_ref':'verify:ok'})
print(r['state'],flush=True)
'''
        first = subprocess.Popen([sys.executable, '-c', script, plan['id']], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(first.stdout.readline().strip(), 'publication-reserved')
            race = '''import sys,warehouse_operations as o,warehouse_admission as a,control_store as s
p=o.inspect(sys.argv[1],actor='admin')
try:
 o.apply(p['id'],actor='admin',expected_revision=p['revision'],delivery_digest=p['delivery_digest'],
  authorize=lambda actor:True,observe_secrets=lambda plan:p['secret_observations'],
  execute_step=lambda *args:print('UNSAFE-DISPATCH'),verify=lambda p:None)
except o.OperationError as e: print(e.code)
try:
 with s.connect() as db:
  db.execute('BEGIN IMMEDIATE')
  a.begin_drain(db,operation_id=p['id'])
except a.AdmissionConflict: print('maintenance-blocked')
with s.connect() as db:
 db.execute('BEGIN IMMEDIATE')
 h=a.admit(db,actor='query-user',kind='query',subject_id='during-publication')
 a.finish(db,h,state='FINISHED')
 print('admission-open')
'''
            second = subprocess.run([sys.executable, '-c', race, other['id']], text=True, capture_output=True, timeout=10)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(second.stdout.splitlines(), ['WAREHOUSE_OPERATOR_BUSY', 'maintenance-blocked', 'admission-open'])
            with self.assertRaises(operations.OperationError) as error:
                operations.reconcile(plan['id'], actor='admin', evidence=operations.ReconciliationEvidence(
                    'publish_snapshot', 'SUCCEEDED', 'untrusted:live-result', 'backend_ack'))
            self.assertEqual(error.exception.code, 'WAREHOUSE_OPERATOR_BUSY')
            observer_calls = []
            witness = self.directory / 'observer-receipt-witness'
            def observer(current):
                observer_calls.append(current['id'])
                witness.write_text('synthetic-observer-receipt')
                return operations.ReconciliationEvidence('publish_snapshot', 'SUCCEEDED', 'observe:live', 'backend_ack')
            with self.assertRaises(operations.OperationError) as error:
                operations.reconcile_from_observer(plan['id'], actor='admin', observer=observer)
            self.assertEqual(error.exception.code, 'WAREHOUSE_OPERATOR_BUSY')
            self.assertEqual(observer_calls, [])
            self.assertFalse(witness.exists())
            live = operations.inspect(plan['id'], actor='admin')
            self.assertEqual(live['steps'][0]['state'], 'RUNNING')
            self.assertEqual(live['state'], 'PREPARING')
            out, err = first.communicate('\n', timeout=10)
            self.assertEqual((first.returncode, out.strip()), (0, 'APPLIED'), err)
        finally:
            if first.poll() is None:
                first.kill()
                first.communicate(timeout=5)

    def test_reconcile_observer_is_not_called_for_invalid_owner(self):
        result = self.apply(self.plan(), execute_step=lambda step, plan: None)
        calls = []
        witness = self.directory / 'unauthorized-observer-witness'
        def observer(current):
            calls.append(current['id'])
            witness.write_text('synthetic-observer-receipt')
            return operations.ReconciliationEvidence('publish_snapshot', 'SUCCEEDED', 'observe:unauthorized', 'backend_ack')
        with self.assertRaises(operations.OperationError) as error:
            operations.reconcile_from_observer(result['id'], actor='other', observer=observer)
        self.assertEqual(error.exception.status, 404)
        self.assertEqual(calls, [])
        self.assertFalse(witness.exists())
        self.assertEqual(operations.inspect(result['id'], actor='admin'), result)

    def test_reconcile_observer_receives_latest_plan_without_sqlite_transaction(self):
        initial = self.plan()
        result = self.apply(initial, execute_step=lambda step, plan: None)
        self.assertGreater(result['revision'], initial['revision'])
        snapshots = []
        def observer(current):
            snapshots.append(copy.deepcopy(current))
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                self.assertEqual(db.execute('SELECT revision FROM operation_requests WHERE operation_id=?',
                                           (current['id'],)).fetchone()[0], current['revision'])
            return operations.ReconciliationEvidence('publish_snapshot', 'NOT_EXECUTED',
                                                     'observe:confirmed-not-sent', 'confirmed_not_dispatched')
        reconciled = operations.reconcile_from_observer(result['id'], actor='reviewer', is_admin=True, observer=observer)
        self.assertEqual(snapshots, [result])
        self.assertEqual(reconciled['state'], 'AWAITING_APPLY')
        self.assertEqual(reconciled['steps'][0]['state'], 'PENDING')
        self.assertEqual(reconciled['steps'][0]['evidence_ref'], 'observe:confirmed-not-sent')
        with store.connect() as db:
            self.assertEqual(admission.control(db)['state'], 'UNKNOWN')
        self.assertEqual(self.apply(reconciled)['state'], 'APPLIED')

    def test_reconcile_observer_exception_and_untyped_result_are_failclosed(self):
        result = self.apply(self.plan(), execute_step=lambda step, plan: None)
        def broken(current):
            raise OSError('synthetic-private-observer-error')
        for observer in (broken, lambda current: {'step': 'publish_snapshot', 'outcome': 'SUCCEEDED'}):
            with self.subTest(observer=observer), self.assertRaises(operations.OperationError) as error:
                operations.reconcile_from_observer(result['id'], actor='admin', observer=observer)
            self.assertNotIn('synthetic-private-observer-error', str(error.exception))
            self.assertEqual(operations.inspect(result['id'], actor='admin'), result)
            with store.connect() as db:
                self.assertEqual(admission.control(db)['state'], 'UNKNOWN')
                self.assertEqual(admission.control(db)['operation_id'], result['id'])
        # An observer failure released the OS locks, not the durable reservation.
        reconciled = operations.reconcile(result['id'], actor='admin', evidence=operations.ReconciliationEvidence(
            'publish_snapshot', 'NOT_EXECUTED', 'observe:not-dispatched', 'confirmed_not_dispatched'))
        self.assertEqual(reconciled['state'], 'AWAITING_APPLY')

    def test_subprocess_crash_and_response_loss_require_explicit_observation(self):
        for mode in ('crash', 'response-loss'):
            with self.subTest(mode=mode):
                plan = self.plan(mode)
                script = '''import os,sys,warehouse_operations as o
p=o.inspect(sys.argv[1],actor='admin')
def send(step,plan):
 print('sent:'+step,flush=True)
 if step=='reconcile_storage':
  if sys.argv[2]=='crash': os._exit(17)
  raise OSError('do-not-persist-private-error-body')
 return {'evidence_ref':'ack:'+step}
r=o.apply(p['id'],actor='admin',expected_revision=p['revision'],delivery_digest=p['delivery_digest'],
 authorize=lambda actor:True,observe_secrets=lambda plan:p['secret_observations'],execute_step=send,
 verify=lambda plan:{'verified':True,'evidence_ref':'verify:ok'})
print(r['state'])
'''
                run = subprocess.run([sys.executable, '-c', script, plan['id'], mode], text=True, capture_output=True, timeout=10)
                self.assertEqual(run.returncode, 17 if mode == 'crash' else 0, run.stderr)
                observed = operations.inspect(plan['id'], actor='admin')
                self.assertEqual(observed['steps'][2]['state'], 'RUNNING' if mode == 'crash' else 'UNKNOWN')
                retry = subprocess.run([sys.executable, '-c', script, plan['id'], mode], text=True, capture_output=True, timeout=10)
                self.assertNotEqual(retry.returncode, 0)
                self.assertNotIn('sent:', retry.stdout)
                with store.connect() as db:
                    self.assertNotEqual(admission.control(db)['state'], 'OPEN')
                    db.execute('BEGIN IMMEDIATE')
                    with self.assertRaises(admission.AdmissionDenied):
                        admission.admit(db, actor='new', kind='query', subject_id=mode)
                observed = operations.reconcile(plan['id'], actor='admin', evidence=operations.ReconciliationEvidence(
                    'reconcile_storage', 'SUCCEEDED', 'observed:storage', 'backend_ack'))
                calls_before = len(self.calls)
                self.assertEqual(self.apply(observed)['state'], 'APPLIED')
                self.assertEqual(self.calls[calls_before:], ['reconcile_polaris', 'reconcile_trino'])
                self.assertNotIn(b'do-not-persist-private-error-body', (self.directory/'jobs.sqlite3').read_bytes())

    def test_import_is_stdlib_only_and_python39_syntax(self):
        script = ('import sys\n'
                  'class NoYaml:\n def find_spec(self,fullname,*args):\n'
                  '  if fullname=="yaml": raise RuntimeError("yaml imported")\n'
                  'sys.meta_path.insert(0,NoYaml())\nimport warehouse_operations\nprint("ok")')
        result = subprocess.run([sys.executable, '-c', script], text=True, capture_output=True)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, 'ok'), result.stderr)


if __name__ == '__main__':
    unittest.main()
