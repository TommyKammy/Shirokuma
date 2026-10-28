"""P4-1 synthetic backend receipts and temporary real SQLite transactions only."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import control_store as store
import data_management as dm
import grant_management as grants
import request_auth as auth
import warehouse_operations as operations


class GrantTests(unittest.TestCase):
    def test_admin_target_ceiling_checked_before_any_mutation_and_management_stays_platform_only(self):
        for accounts in ({}, {'alice': {'role': 'bi_viewer'}}, {'alice': {'role': 'data_engineer'}},
                         {'alice': {'role': 'unknown'}}):
            with patch.object(auth, '_principal_accounts', return_value=accounts):
                with self.assertRaises(operations.OperationError) as error:
                    self.change('admin')
                self.assertEqual(error.exception.code, 'ADMIN_ACCOUNT_REQUIRED')
            with store.connect() as db:
                for table in ('grants', 'effective_grants', 'operations', 'grant_requests', 'grant_audit'):
                    self.assertEqual(db.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)
        with patch.object(auth, '_principal_accounts', return_value={'alice': {'role': 'admin'}}):
            result = self.change('admin')
        self.assertEqual(result['state'], 'AWAITING_APPLY')
        self.assertEqual(self.projection()['maximum_role'], 'revoked')
        scoped = auth.AuthenticatedPrincipal('alice', 'admin', False)
        for action in (lambda: self.change('admin', 1, 'other', actor=scoped),
                       lambda: grants.list_grants(scoped, 'admin', self.database)):
            with self.assertRaises(PermissionError):
                action()

    def test_invalid_private_verification_receipt_cannot_activate_admin(self):
        with patch.object(auth, '_principal_accounts', return_value={'alice': {'role': 'admin'}}):
            self.change('admin')
        with store.connect() as db:
            desired = dict(db.execute('SELECT * FROM grants').fetchone())
            with self.assertRaises(operations.OperationError) as error:
                grants.promote(db, {'version': 2, 'grant_revisions': [desired]}, 'unverified',
                    'apply', 'operator', '')
            self.assertEqual(error.exception.code, 'INVALID_VERIFICATION_RECEIPT')
            self.assertIsNone(grants.migration_proof(db, desired))
        self.assertEqual(self.projection()['maximum_role'], 'revoked')

    def test_database_admin_migration_is_idempotent_and_rank_downgrades_are_immediate(self):
        self.change()
        with store.connect() as db:
            # Reconstruct the pre-14 CHECK while preserving the ledger row.
            for name in ('grant_insert', 'grant_update', 'grant_delete'):
                db.execute('DROP TRIGGER ' + name)
            definition = db.execute("SELECT sql FROM sqlite_master WHERE name='effective_grants'").fetchone()[0]
            db.execute(definition.replace('effective_grants', 'legacy_effective').replace(
                ",'admin'", ''))
            db.execute('INSERT INTO legacy_effective SELECT * FROM effective_grants')
            db.execute('DROP TABLE effective_grants')
            db.execute('ALTER TABLE legacy_effective RENAME TO effective_grants')
            before = grants.effective(db, 'alice', self.database)
            audit = list(db.execute('SELECT * FROM grant_audit'))
            for _ in range(2):
                grants.migrate_database_admin(db)
                self.assertEqual(grants.effective(db, 'alice', self.database), before)
                self.assertEqual(list(db.execute('SELECT * FROM grant_audit')), audit)
            dm.update_grant(db, 'alice', self.database, 1, 'admin')
            self.assertEqual(grants.effective(db, 'alice', self.database)['maximum_role'], 'revoked')
            db.execute("UPDATE effective_grants SET maximum_role='admin' WHERE principal_id='alice'")
            for revision, role in ((2, 'data_engineer'), (3, 'bi_viewer'), (4, 'revoked')):
                epoch = grants.effective(db, 'alice', self.database)['epoch']
                dm.update_grant(db, 'alice', self.database, revision, role)
                self.assertEqual(grants.effective(db, 'alice', self.database)['maximum_role'], role)
                self.assertGreater(grants.effective(db, 'alice', self.database)['epoch'], epoch)

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, SHIROKUMA_STATE_DIR=temp.name)
        env.start()
        self.addCleanup(env.stop)
        self.admin = auth.AuthenticatedPrincipal('operator', 'admin', True)
        with store.connect() as db:
            self.database = dm.create_database(db, 'research', 'Research', '', 'test').id

    def change(self, role='bi_viewer', revision=0, key='key', principal='alice', actor=None, database=None):
        return grants.mutate(actor or self.admin, 'admin', database or self.database,
            dict(principal_id=principal, maximum_role=role, expected_revision=revision), key)

    def projection(self):
        with store.connect() as db:
            return grants.effective(db, 'alice', self.database)

    def test_pending_cas_idempotency_actor_database_and_atomic_audit(self):
        first = self.change()
        self.assertEqual(first, self.change())
        self.assertEqual(self.projection()['maximum_role'], 'revoked')
        for action in (lambda: self.change('data_engineer'), lambda: self.change(key='different'),
                       lambda: self.change(actor=auth.AuthenticatedPrincipal('other', 'admin', True)),
                       lambda: self.change(database='unknown')):
            with self.assertRaises(operations.OperationError):
                action()
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM operations').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM grant_audit').fetchone()[0], 1)
            with self.assertRaises(operations.OperationError):
                grants.inspect(db, first['id'], 'other')
        with patch.object(dm, 'record_operation', side_effect=RuntimeError('synthetic rollback')):
            with self.assertRaises(RuntimeError):
                self.change('revoked', 1, 'rollback')
        with store.connect() as db:
            self.assertEqual(dm.get_grant(db, 'alice', self.database).revision, 1)
        for payload in (dict(principal_id='alice',maximum_role='admin',expected_revision=1),
                        dict(principal_id='alice',maximum_role='revoked',expected_revision=True)):
            with self.assertRaises(operations.OperationError):
                grants.mutate(self.admin, 'admin', self.database, payload, 'invalid')
        for principal, role in ((auth.AuthenticatedPrincipal('operator','admin',False), 'admin'),
                                (self.admin,'bi_viewer')):
            with self.assertRaises(PermissionError):
                grants.list_grants(principal, role, self.database)

    def test_restriction_regrant_delete_tombstone_and_restart(self):
        self.change('data_engineer')
        # Explicit simulated verified full-apply commit (no network).
        with store.connect() as db:
            generation = dm.WarehouseConfigGeneration('g','LOCAL','g',(),(),(),'APPLIED')
            dm.record_generation(db, generation)
            spec = {'grant_revisions':[vars(dm.get_grant(db,'alice',self.database))]}
            op = dm.Operation('apply','operator','WAREHOUSE_CONFIG_APPLY',(),None,'APPLIED',(),'g',(),())
            dm.record_operation(db,op)
            grants.promote(db,spec,'g','apply','operator','receipt:verified')
        promoted = self.projection()
        self.change('bi_viewer',1,'down')
        down = self.projection()
        self.assertEqual(down['maximum_role'],'bi_viewer')
        self.assertGreater(down['epoch'],promoted['epoch'])
        self.change('revoked',2,'revoke')
        revoked = self.projection()
        self.change('data_engineer',3,'regrant')
        self.assertEqual(self.projection()['maximum_role'],'revoked')
        self.assertGreater(self.projection()['epoch'],revoked['epoch'])
        with store.connect() as db:
            db.execute('DELETE FROM grants')
        tombstone = self.projection()
        self.change('bi_viewer',0,'new')
        self.assertGreater(self.projection()['epoch'],tombstone['epoch'])
        self.assertEqual(self.projection()['maximum_role'],'revoked')

    def test_actor_and_database_keys_are_independent_and_older_edits_superseded(self):
        first = self.change()
        second = self.change('data_engineer',1,'key',actor=auth.AuthenticatedPrincipal('other','admin',True))
        self.assertNotEqual(second['id'],first['id'])
        with store.connect() as db:
            self.assertEqual(grants.inspect(db,first['id'],'operator')['state'],'SUPERSEDED')
            other_db = dm.create_database(db,'finance','Finance','','test').id
        third = self.change(database=other_db)
        self.assertNotEqual(third['id'],first['id'])
        self.assertEqual(len(grants.list_grants(self.admin,'admin',other_db)['grants']),1)

    def test_malformed_role_values_return_400_without_mutation(self):
        for role in ([], {}, ['bi_viewer'], {'role':'bi_viewer'}, None, True, 1, 1.5):
            with self.subTest(role=role), self.assertRaises(operations.OperationError) as error:
                self.change(role)
            self.assertEqual(error.exception.status,400)
        with store.connect() as db:
            for table in ('grants','effective_grants','operations','grant_requests','grant_audit'):
                self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)

    def test_superseded_replay_conflict_and_failed_new_mutation_are_atomic(self):
        first = self.change()
        second = self.change('data_engineer',1,'newer')
        before = self.projection()
        replay = self.change()
        self.assertEqual((replay['id'],replay['state']),(first['id'],'SUPERSEDED'))
        with self.assertRaises(operations.OperationError) as error:
            self.change('revoked')
        self.assertEqual(error.exception.code,'IDEMPOTENCY_CONFLICT')
        with patch.object(dm,'record_operation',side_effect=RuntimeError('synthetic rollback')):
            with self.assertRaises(RuntimeError):
                self.change('revoked',2,'third')
        with store.connect() as db:
            self.assertEqual(dm.get_grant(db,'alice',self.database).revision,2)
            self.assertEqual(grants.effective(db,'alice',self.database),before)
            self.assertEqual(grants.inspect(db,second['id'],'operator')['state'],'AWAITING_APPLY')
            self.assertEqual(grants.inspect(db,first['id'],'operator')['state'],'SUPERSEDED')
            for table in ('operations','grant_requests','grant_audit'):
                self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],2)
        third = self.change('revoked',2,'third')
        self.assertEqual(third,self.change('revoked',2,'third'))
        with store.connect() as db:
            self.assertEqual(grants.inspect(db,second['id'],'operator')['state'],'SUPERSEDED')


class ApplyAndMigrationTests(unittest.TestCase):
    def test_admin_full_apply_uses_existing_final_verified_receipt_boundary(self):
        import database_generation as generation
        f = self.fixture
        with store.connect() as db:
            dm.update_grant(db, 'reader', 'opaque_0', 1, 'admin')
        value = copy.deepcopy(f.spec)
        value['version'] = 2
        value['profile']['template_fingerprint'] = generation.profile_fingerprint(version=2)
        value['grant_revisions'][0].update(maximum_role='admin', revision=2)
        f.bundle = generation.render(value)
        plan = f.plan()
        self.assertEqual(self.projection()['maximum_role'], 'revoked')
        def private_verified_callback(plan):
            # Unit contract stub, matching existing full-apply tests. Mandatory
            # real admin SQL/proof coverage is in test_warehouse_operator.
            self.assertEqual(self.projection()['maximum_role'], 'revoked')
            return dict(verified=True, evidence_ref='test:final-verified-admin-contract')
        self.assertEqual(f.apply(plan, verify=private_verified_callback)['state'], 'APPLIED')
        current = self.projection()
        self.assertEqual(current['maximum_role'], 'admin')
        self.assertEqual(current['applied_generation_id'], plan['generation_id'])
        with store.connect() as db:
            grant = dict(db.execute('SELECT * FROM grants').fetchone())
            self.assertEqual(grants.migration_proof(db, grant), plan['generation_id'])
            dm.update_grant(db, 'reader', 'opaque_0', 2, 'revoked')
        self.assertEqual(self.projection()['maximum_role'], 'revoked')
        with store.connect() as db:
            dm.update_grant(db, 'reader', 'opaque_0', 3, 'admin')
        self.assertEqual(self.projection()['maximum_role'], 'revoked')

    def test_cancel_never_applied_pair_retains_ledger_and_survives_next_full(self):
        import catalog_admin
        import database_generation
        f = self.fixture
        base = f.apply(f.plan())
        self.assertEqual(base['state'], 'APPLIED')
        admin = auth.AuthenticatedPrincipal('admin', 'admin', True)
        def change(role, revision, key):
            return grants.mutate(admin, 'admin', 'opaque_0',
                dict(principal_id='unrelated', maximum_role=role, expected_revision=revision), key)
        pending = change('bi_viewer', 0, 'new-unrelated')
        canceled = change('revoked', 1, 'cancel-unrelated')
        self.assertEqual(canceled['state'], 'CANCELED')
        self.assertEqual(canceled, change('revoked', 1, 'cancel-unrelated'))
        with self.assertRaisesRegex(operations.OperationError, 'IDEMPOTENCY_CONFLICT'):
            change('bi_viewer', 1, 'cancel-unrelated')
        with store.connect() as db:
            held = grants.effective(db, 'unrelated', 'opaque_0')
            audits = [tuple(r) for r in db.execute("SELECT * FROM grant_audit WHERE principal_id='unrelated'")]
            self.assertEqual(held['maximum_role'], 'revoked')
            self.assertIsNone(held['applied_revision'])
            self.assertIsNone(held['applied_generation_id'])
            self.assertEqual(grants.inspect(db, pending['id'], 'admin')['state'], 'SUPERSEDED')
            self.assertEqual(db.execute("SELECT count(*) FROM grants WHERE principal_id='unrelated'").fetchone()[0], 1)
        exported = catalog_admin.export_spec('after-cancel', base['manifest']['spec'])
        self.assertEqual(exported['grant_revisions'], base['manifest']['spec']['grant_revisions'])
        bundle = database_generation.render(exported)
        f.observations = [dict(ref, exists=True, uid='uid-'+ref['name'], resource_version='1', immutable=True)
            for ref in bundle['tofu']['bootstrap_secret_refs'] + bundle['tofu']['deferred_secret_refs']]
        applied = f.apply(f.plan('after-cancel', bundle))
        self.assertEqual(applied['state'], 'APPLIED')
        self.assertEqual(catalog_admin.export_spec('after-full', exported)['grant_revisions'], exported['grant_revisions'])
        with store.connect() as db:
            self.assertEqual(grants.effective(db, 'unrelated', 'opaque_0'), held)
            self.assertEqual([tuple(r) for r in db.execute("SELECT * FROM grant_audit WHERE principal_id='unrelated'")], audits)
            self.assertEqual(grants.inspect(db, canceled['id'], 'admin')['state'], 'CANCELED')

    def same_generation_cancellation(self):
        f = self.fixture
        base = f.apply(f.plan())
        self.assertEqual(base['state'], 'APPLIED')
        admin = auth.AuthenticatedPrincipal('admin', 'admin', True)
        for role, revision in (('bi_viewer', 0), ('revoked', 1)):
            result = grants.mutate(admin, 'admin', 'opaque_0',
                dict(principal_id='unrelated', maximum_role=role, expected_revision=revision), 'intent-'+str(revision))
        self.assertEqual(result['state'], 'CANCELED')
        with store.connect() as db:
            marker = db.execute('SELECT before_after FROM operations WHERE id=?', (result['id'],)).fetchone()[0]
            held = grants.effective(db, 'unrelated', 'opaque_0')
            audit = [tuple(r) for r in db.execute("SELECT * FROM grant_audit WHERE principal_id='unrelated'")]
        second = operations.create_plan('other-admin', '/warehouse/configuration', 'second-full', f.bundle, f.observations)
        self.assertEqual(f.apply(second, actor='other-admin')['state'], 'APPLIED')
        return base, second, result, marker, held, audit

    def test_same_generation_full_preserves_original_cancellation_proof(self):
        import catalog_admin
        base, second, canceled, marker, held, audit = self.same_generation_cancellation()
        with store.connect() as db:
            with patch.object(auth, '_principal_accounts', side_effect=AssertionError('historical actor lookup')):
                self.assertFalse(any(g['principal_id']=='unrelated' for g in grants.desired_projection(db)))
            self.assertEqual(db.execute('SELECT before_after FROM operations WHERE id=?', (canceled['id'],)).fetchone()[0], marker)
            self.assertEqual(grants.effective(db, 'unrelated', 'opaque_0'), held)
            self.assertEqual([tuple(r) for r in db.execute("SELECT * FROM grant_audit WHERE principal_id='unrelated'")], audit)
            self.assertIsNone(held['applied_revision'])
            self.assertIsNone(held['applied_generation_id'])
            operations._check_registry(db, base['manifest']['spec'])
        exported = catalog_admin.export_spec('next-create-target', base['manifest']['spec'])
        self.assertEqual(exported['grant_revisions'], base['manifest']['spec']['grant_revisions'])
        self.assertEqual(self.fixture.plan('normal-next-full')['state'], 'AWAITING_APPLY')
        import database_creations as creations
        import database_creation_auth as creation_auth
        import database_creation_preparation as preparation
        config = creations.Enrollment('test-environment', base['manifest']['spec']['warehouse_id'],
            'test-enrollment', 1, 'executor')
        admin = auth.AuthenticatedPrincipal('admin', 'admin', True)
        with patch.object(creations, '_enrollment', config), patch.object(creations, '_readiness'), \
                patch.object(creations, '_gate'), patch.object(creation_auth, 'current_authority'), \
                patch.object(auth, '_principal_accounts', return_value={'admin': dict(role='admin', platform_admin=True)}):
            ack, created = creations.create(admin, 'admin', dict(sql_catalog='nextdatabase'), 'next-create')
        self.assertTrue(created)
        public = catalog_admin.export_spec('next-create-candidate', base['manifest']['spec'])
        self.assertFalse(any(g['principal_id']=='unrelated' for g in public['grant_revisions']))
        with store.connect() as db:
            root = dict(db.execute('SELECT * FROM db_creations WHERE id=?', (ack['creation_id'],)).fetchone())
            preparation.target_only(db, base['manifest']['spec'], public, root, 'bootstrap')

    def test_same_generation_full_all_candidates_and_original_binding_fail_closed(self):
        base, second, canceled, marker, held, audit = self.same_generation_cancellation()
        cases = [
            ("UPDATE operations SET actor='wrong' WHERE id=?", (second['id'],)),
            ("UPDATE operation_requests SET actor='wrong' WHERE operation_id=?", (second['id'],)),
            ("UPDATE operation_requests SET payload='{}' WHERE operation_id=?", (second['id'],)),
            ("UPDATE operation_requests SET payload=json_set(payload,'$.manifest.delivery_digest','bad') WHERE operation_id=?", (second['id'],)),
            ("UPDATE operations SET apply_id='wrong' WHERE id=?", (second['id'],)),
            ("UPDATE operation_requests SET phase='bootstrap' WHERE operation_id=?", (second['id'],)),
            ("UPDATE operation_requests SET predecessor_id=? WHERE operation_id=?", (base['id'], second['id'])),
            ("UPDATE operation_steps SET state='UNKNOWN' WHERE operation_id=? AND step='publish_snapshot'", (second['id'],)),
            ("DELETE FROM operation_steps WHERE operation_id=? AND step='apply_secrets'", (second['id'],)),
            ("UPDATE operation_steps SET evidence_ref='' WHERE operation_id=? AND step='verify'", (second['id'],)),
            ("UPDATE operation_steps SET evidence_ref='valid:changed-original' WHERE operation_id=? AND step='verify'", (base['id'],)),
            ("UPDATE operations SET state='UNKNOWN' WHERE id=?", (base['id'],)),
            ("DELETE FROM operation_steps WHERE operation_id=? AND step='verify'", (base['id'],)),
            ("DELETE FROM operation_requests WHERE operation_id=?", (base['id'],)),
        ]
        with store.connect() as db:
            for sql, params in cases:
                with self.subTest(sql=sql):
                    db.execute('SAVEPOINT proof')
                    db.execute(sql, params)
                    self.assertTrue(any(g['principal_id']=='unrelated' for g in grants.desired_projection(db)))
                    self.assertEqual(grants.effective(db, 'unrelated', 'opaque_0'), held)
                    db.execute('ROLLBACK TO proof')
                    db.execute('RELEASE proof')
            # A closed unrelated UNKNOWN operation is not proof of prior authority.
            db.execute("UPDATE operations SET state='UNKNOWN' WHERE id=?", (second['id'],))
            self.assertFalse(any(g['principal_id']=='unrelated' for g in grants.desired_projection(db)))

    def test_same_generation_new_cancellation_uses_deterministic_verified_anchor(self):
        import catalog_admin
        import database_creations as creations
        import database_creation_auth as creation_auth
        import database_creation_preparation as preparation
        f = self.fixture
        with patch.object(operations.uuid, 'uuid4', return_value='20000000-0000-4000-8000-000000000000'):
            first = f.plan()
        base = f.apply(first)
        self.assertEqual(base['state'], 'APPLIED')
        with patch.object(operations.uuid, 'uuid4', return_value='10000000-0000-4000-8000-000000000000'):
            second = operations.create_plan('other-admin', '/warehouse/configuration', 'second-full', f.bundle, f.observations)
        self.assertEqual(f.apply(second, actor='other-admin')['state'], 'APPLIED')
        self.assertEqual(second['generation_id'], base['generation_id'])
        self.assertEqual(second['manifest'], base['manifest'])
        self.assertEqual(second['delivery_digest'], base['delivery_digest'])
        admin = auth.AuthenticatedPrincipal('admin', 'admin', True)
        pending = grants.mutate(admin, 'admin', 'opaque_0',
            dict(principal_id='another', maximum_role='bi_viewer', expected_revision=0), 'another-0')
        with store.connect() as db:
            old_row = tuple(db.execute("SELECT * FROM grants WHERE principal_id='reader'").fetchone())
            old_effective = grants.effective(db, 'reader', 'opaque_0')
            old_audits = [tuple(r) for r in db.execute("SELECT * FROM grant_audit WHERE principal_id!='another'")]
            pending_audits = [tuple(r) for r in db.execute("SELECT * FROM grant_audit WHERE principal_id='another'")]
            cases = [
                ("UPDATE operations SET actor='wrong' WHERE id=?", (second['id'],)),
                ("UPDATE operation_requests SET payload='{}' WHERE operation_id=?", (second['id'],)),
                ("UPDATE operation_requests SET payload=json_set(payload,'$.manifest.delivery_digest','bad') WHERE operation_id=?", (second['id'],)),
                ("UPDATE operation_steps SET state='UNKNOWN' WHERE operation_id=? AND step='publish_snapshot'", (second['id'],)),
                ("DELETE FROM operation_steps WHERE operation_id=? AND step='verify'", (second['id'],)),
            ]
            for sql, params in cases:
                with self.subTest(sql=sql):
                    db.execute('SAVEPOINT candidate')
                    db.execute(sql, params)
                    fallback = grants._mutate(admin, 'admin', 'opaque_0',
                        dict(principal_id='another', maximum_role='revoked', expected_revision=1), 'invalid-candidate', connection=db)
                    self.assertEqual(fallback['state'], 'AWAITING_APPLY')
                    self.assertTrue(any(g['principal_id']=='another' for g in grants.desired_projection(db)))
                    self.assertEqual(grants.effective(db, 'another', 'opaque_0')['maximum_role'], 'revoked')
                    db.execute('ROLLBACK TO candidate')
                    db.execute('RELEASE candidate')
        with patch.object(auth, '_principal_accounts', side_effect=AssertionError('historical actor lookup')):
            result = grants.mutate(admin, 'admin', 'opaque_0',
                dict(principal_id='another', maximum_role='revoked', expected_revision=1), 'another-1')
        self.assertEqual(result['state'], 'CANCELED')
        with store.connect() as db:
            marker = db.execute('SELECT before_after FROM operations WHERE id=?', (result['id'],)).fetchone()[0]
            anchor = json.loads(json.loads(marker)[2][1])['base']
            verify_ref = db.execute("SELECT evidence_ref FROM operation_steps WHERE operation_id=? AND step='verify'", (second['id'],)).fetchone()[0]
            self.assertEqual(anchor, dict(generation_id=base['generation_id'], delivery_digest=base['delivery_digest'],
                operation_id=min(first['id'], second['id']), verify_ref=verify_ref))
            row = dict(db.execute("SELECT * FROM grants WHERE principal_id='another'").fetchone())
            held = grants.effective(db, 'another', 'opaque_0')
            audits = [tuple(r) for r in db.execute("SELECT * FROM grant_audit WHERE principal_id='another'")]
            self.assertEqual(audits[:len(pending_audits)], pending_audits)
            self.assertEqual(len(audits), len(pending_audits)+1)
            self.assertEqual(row['maximum_role'], 'revoked')
            self.assertEqual(row['revision'], 2)
            self.assertEqual(held['maximum_role'], 'revoked')
            self.assertEqual(held['epoch'], 2)
            self.assertIsNone(held['applied_revision'])
            self.assertIsNone(held['applied_generation_id'])
            self.assertEqual(grants.inspect(db, pending['id'], 'admin')['state'], 'SUPERSEDED')
            self.assertEqual(tuple(db.execute("SELECT * FROM grants WHERE principal_id='reader'").fetchone()), old_row)
            self.assertEqual(grants.effective(db, 'reader', 'opaque_0'), old_effective)
            self.assertEqual([tuple(r) for r in db.execute("SELECT * FROM grant_audit WHERE principal_id!='another'")], old_audits)
        self.assertEqual(catalog_admin.export_spec('after-cancel', base['manifest']['spec'])['grant_revisions'],
            base['manifest']['spec']['grant_revisions'])
        with patch.object(operations.uuid, 'uuid4', return_value='00000000-0000-4000-8000-000000000000'):
            third = operations.create_plan('later-admin', '/warehouse/configuration', 'third-full', f.bundle, f.observations)
        self.assertLess(third['id'], anchor['operation_id'])
        self.assertEqual(f.apply(third, actor='later-admin')['state'], 'APPLIED')
        with store.connect() as db:
            self.assertFalse(any(g['principal_id']=='another' for g in grants.desired_projection(db)))
            self.assertEqual(db.execute('SELECT before_after FROM operations WHERE id=?', (result['id'],)).fetchone()[0], marker)
            self.assertEqual(dict(db.execute("SELECT * FROM grants WHERE principal_id='another'").fetchone()), row)
            self.assertEqual(grants.effective(db, 'another', 'opaque_0'), held)
            self.assertEqual([tuple(r) for r in db.execute("SELECT * FROM grant_audit WHERE principal_id='another'")], audits)
            self.assertEqual(tuple(db.execute("SELECT * FROM grants WHERE principal_id='reader'").fetchone()), old_row)
            self.assertEqual(grants.effective(db, 'reader', 'opaque_0'), old_effective)
            self.assertEqual([tuple(r) for r in db.execute("SELECT * FROM grant_audit WHERE principal_id!='another' ORDER BY id")][:len(old_audits)], old_audits)
            operations._check_registry(db, base['manifest']['spec'])
        config = creations.Enrollment('test-environment', base['manifest']['spec']['warehouse_id'],
            'test-enrollment', 1, 'executor')
        with patch.object(creations, '_enrollment', config), patch.object(creations, '_readiness'), \
                patch.object(creations, '_gate'), patch.object(creation_auth, 'current_authority'), \
                patch.object(auth, '_principal_accounts', return_value={'admin': dict(role='admin', platform_admin=True)}):
            ack, created = creations.create(admin, 'admin', dict(sql_catalog='nextdatabase'), 'next-create')
        self.assertTrue(created)
        public = catalog_admin.export_spec('next-create-candidate', base['manifest']['spec'])
        self.assertEqual(public['grant_revisions'], base['manifest']['spec']['grant_revisions'])
        with store.connect() as db:
            root = dict(db.execute('SELECT * FROM db_creations WHERE id=?', (ack['creation_id'],)).fetchone())
            preparation.target_only(db, base['manifest']['spec'], public, root, 'bootstrap')

    def test_cancel_negative_history_always_revokes_and_keeps_desired_row(self):
        import warehouse_admission
        f = self.fixture
        base = f.apply(f.plan())
        admin = auth.AuthenticatedPrincipal('admin', 'admin', True)
        grants.mutate(admin, 'admin', 'opaque_0', dict(principal_id='unrelated',maximum_role='data_engineer',expected_revision=0), 'pending-one')
        pending = grants.mutate(admin, 'admin', 'opaque_0', dict(principal_id='unrelated',maximum_role='bi_viewer',expected_revision=1), 'pending-two')
        cases = [
            ("UPDATE operation_steps SET evidence_ref='' WHERE step='verify'",()),
            ("UPDATE operation_steps SET state='UNKNOWN' WHERE step='publish_snapshot'",()),
            ("DELETE FROM operation_steps WHERE step='apply_secrets'",()),
            ("UPDATE generation_plans SET manifest='{}'",()),
            ("UPDATE generation_plans SET delivery_digest='bad'",()),
            ("UPDATE operations SET state='UNKNOWN' WHERE action='WAREHOUSE_CONFIG_APPLY'",()),
            ("UPDATE operation_requests SET payload='{}'",()),
            ("UPDATE operation_requests SET actor='wrong'",()),
            ("UPDATE operations SET expected_revision=1 WHERE action='WAREHOUSE_CONFIG_APPLY'",()),
            ("UPDATE catalog_bindings SET applied_revision=0 WHERE database_id='opaque_1'",()),
            ("UPDATE warehouse_controls SET applied_generation_id=NULL",()),
            ("UPDATE effective_grants SET applied_revision=1 WHERE principal_id='unrelated'",()),
            ("UPDATE effective_grants SET applied_generation_id=? WHERE principal_id='unrelated'",(base['generation_id'],)),
            ("UPDATE effective_grants SET maximum_role='bi_viewer' WHERE principal_id='unrelated'",()),
            ("UPDATE grant_audit SET effective_epoch=9 WHERE principal_id='unrelated' AND desired_revision=1",()),
            ("UPDATE operations SET actor='wrong' WHERE id=?",(pending['id'],)),
            ("UPDATE grant_audit SET receipt='prior:promotion' WHERE principal_id='unrelated' AND desired_revision=1",()),
            ("DELETE FROM grant_requests WHERE operation_id=?",(pending['id'],)),
            ("UPDATE grants SET revision=9 WHERE principal_id='unrelated'",()),
            ("UPDATE warehouse_controls SET operation_id=?",(pending['id'],)),
            ("UPDATE warehouse_controls SET state='UNKNOWN'",()),
        ]
        with store.connect() as db:
            for sql, params in cases:
                with self.subTest(sql=sql):
                    db.execute('SAVEPOINT negative')
                    db.execute(sql, params)
                    revision = dm.get_grant(db, 'unrelated', 'opaque_0').revision
                    result = grants._mutate(admin, 'admin', 'opaque_0',
                        dict(principal_id='unrelated',maximum_role='revoked',expected_revision=revision), 'negative', connection=db)
                    self.assertEqual(result['state'], 'AWAITING_APPLY')
                    self.assertEqual(grants.effective(db,'unrelated','opaque_0')['maximum_role'], 'revoked')
                    self.assertTrue(any(g['principal_id']=='unrelated' for g in grants.desired_projection(db)))
                    with self.assertRaisesRegex(operations.OperationError, 'STALE_REGISTRY'):
                        operations._check_registry(db, base['manifest']['spec'])
                    db.execute('ROLLBACK TO negative')
                    db.execute('RELEASE negative')
            for kind, closure, expected in (('query','', 'AWAITING_APPLY'),('query','FENCED_READ_ONLY','CANCELED'),('job','', 'AWAITING_APPLY')):
                with self.subTest(kind=kind,closure=closure):
                    db.execute('SAVEPOINT admission')
                    handle = warehouse_admission.admit(db,actor='admin',kind=kind,subject_id='active')
                    db.execute("UPDATE warehouse_admissions SET state='UNKNOWN',execution_closure=? WHERE id=?",(closure,handle.id))
                    result = grants._mutate(admin,'admin','opaque_0',dict(principal_id='unrelated',maximum_role='revoked',expected_revision=2),'admission',connection=db)
                    self.assertEqual(result['state'],expected)
                    db.execute('ROLLBACK TO admission')
                    db.execute('RELEASE admission')

    def test_cancellation_marker_drift_never_hides_and_stales_old_plan(self):
        import catalog_admin
        f = self.fixture
        base = f.apply(f.plan())
        admin = auth.AuthenticatedPrincipal('admin','admin',True)
        def change(role, revision, key):
            return grants.mutate(admin,'admin','opaque_0',dict(principal_id='unrelated',maximum_role=role,expected_revision=revision),key)
        change('bi_viewer',0,'pending')
        canceled = change('revoked',1,'cancel')
        self.assertEqual(canceled['state'],'CANCELED')
        exported = catalog_admin.export_spec('frozen-after-cancel',base['manifest']['spec'])
        with store.connect() as db:
            for sql,params in (
                ("UPDATE effective_grants SET epoch=epoch+1 WHERE principal_id='unrelated'",()),
                ("UPDATE grants SET revision=revision+1 WHERE principal_id='unrelated'",()),
                ("UPDATE grants SET maximum_role='bi_viewer' WHERE principal_id='unrelated'",()),
                ("UPDATE operations SET before_after='[]' WHERE id=?",(canceled['id'],)),
                ("UPDATE operations SET before_after='invalid' WHERE id=?",(canceled['id'],)),
                ("UPDATE operation_steps SET evidence_ref='' WHERE step='verify'",()),
                ("UPDATE effective_grants SET maximum_role='bi_viewer' WHERE principal_id='unrelated'",()),
            ):
                with self.subTest(sql=sql):
                    db.execute('SAVEPOINT drift')
                    db.execute(sql,params)
                    self.assertTrue(any(g['principal_id']=='unrelated' for g in grants.desired_projection(db)))
                    with self.assertRaisesRegex(operations.OperationError,'STALE_REGISTRY'):
                        operations._check_registry(db,exported)
                    db.execute('ROLLBACK TO drift')
                    db.execute('RELEASE drift')
        change('bi_viewer',2,'regrant')
        self.assertTrue(any(g['principal_id']=='unrelated' for g in catalog_admin.export_spec('after-regrant',exported)['grant_revisions']))

    def test_admin_schema_migration_preserves_applied_proof_and_authority(self):
        plan = self.fixture.plan()
        self.assertEqual(self.fixture.apply(plan)['state'], 'APPLIED')
        with store.connect() as db:
            tables = ('effective_grants', 'grant_audit', 'generation_plans', 'operation_steps')
            before = {t: [tuple(r) for r in db.execute('SELECT * FROM ' + t)] for t in tables}
            grants.migrate_database_admin(db)
            grants.migrate_database_admin(db)
            self.assertEqual({t: [tuple(r) for r in db.execute('SELECT * FROM ' + t)] for t in tables}, before)
            grant = dict(db.execute('SELECT * FROM grants').fetchone())
            self.assertEqual(grants.migration_proof(db, grant), plan['generation_id'])

    def setUp(self):
        from test_warehouse_operations import OperationTests
        self.fixture = OperationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def projection(self):
        with store.connect() as db:
            return grants.effective(db,'reader','opaque_0')

    def test_full_apply_promotes_only_after_verify_and_stale_edit_never_promotes(self):
        f = self.fixture
        def verify(plan):
            self.assertEqual(self.projection()['maximum_role'],'revoked')
            with store.connect() as db:
                dm.update_grant(db,'reader','opaque_0',1,'data_engineer')
            return dict(verified=True,evidence_ref='verify:ok')
        plan = f.plan()
        result = f.apply(plan,verify=verify)
        self.assertNotEqual(result['state'],'APPLIED')
        self.assertEqual(self.projection()['maximum_role'],'revoked')

    def test_failed_unknown_and_reconciliation_do_not_promote(self):
        f = self.fixture
        plan = f.plan()
        result = f.apply(plan,verify=lambda plan: dict(verified=False,evidence_ref='verify:failed'))
        self.assertEqual(result['state'],'UNKNOWN')
        self.assertEqual(self.projection()['maximum_role'],'revoked')
        result = operations.reconcile(plan['id'],actor='admin',evidence=operations.ReconciliationEvidence(
            'verify','SUCCEEDED','verify:observed','observed_configuration'))
        self.assertEqual(result['state'],'AWAITING_APPLY')
        self.assertEqual(self.projection()['maximum_role'],'revoked')
        result = f.apply(result)
        self.assertEqual(result['state'],'APPLIED')
        self.assertEqual(self.projection()['maximum_role'],'bi_viewer')

    def test_migration_current_verified_exact_tuple_and_negative_evidence(self):
        f = self.fixture
        plan = f.plan()
        self.assertEqual(f.apply(plan)['state'],'APPLIED')
        with store.connect() as db:
            grant = dict(db.execute('SELECT * FROM grants').fetchone())
            self.assertEqual(grants.migration_proof(db,grant),plan['generation_id'])
            cases = [
                ("UPDATE grants SET revision=9",()),
                ("UPDATE grants SET maximum_role='data_engineer'",()),
                ("UPDATE grants SET principal_id='other'",()),
                ("UPDATE warehouse_config_generations SET state='PLANNED'",()),
                ("UPDATE operations SET state='PREPARED'",()),
                ("UPDATE operation_requests SET phase='bootstrap'",()),
                ("UPDATE operation_steps SET evidence_ref='' WHERE step='verify'",()),
                ("UPDATE operation_steps SET state='UNKNOWN' WHERE step='verify'",()),
                ("DELETE FROM operation_steps WHERE step='verify'",()),
                ("DELETE FROM operation_requests",()),
                ("UPDATE warehouse_config_generations SET warehouse_id='other'",()),
                ("UPDATE catalog_bindings SET applied_revision=0",()),
                ("UPDATE catalog_bindings SET applied_generation_id=NULL",()),
                ("UPDATE generation_plans SET manifest='{}'",()),
                ("UPDATE generation_plans SET manifest='not-json'",()),
            ]
            for sql,params in cases:
                db.execute('SAVEPOINT negative')
                db.execute(sql,params)
                current = dict(db.execute('SELECT * FROM grants').fetchone())
                self.assertIsNone(grants.migration_proof(db,current),sql)
                db.execute('ROLLBACK TO negative')
                db.execute('RELEASE negative')
            # Reopen an actual old-version ledger, retaining full applied proof.
            from test_control_store import drop_recovery_schema
            drop_recovery_schema(db)
            for trigger in ('grant_insert','grant_update','grant_delete'):
                db.execute('DROP TRIGGER '+trigger)
            for table in ('db_job_requests','db_job_operations','db_job_steps','db_job_runs','db_jobs',
                          'metadata_audit','metadata_requests','grant_audit','grant_requests','effective_grants'):
                db.execute('DROP TABLE '+table)
            db.execute('PRAGMA user_version=9')
        with store.connect() as db:
            effective = grants.effective(db,'reader','opaque_0')
            self.assertEqual(effective['maximum_role'],'bi_viewer')
            self.assertEqual(effective['epoch'],grant['revision'])
            self.assertEqual(effective['applied_generation_id'],plan['generation_id'])

    def test_desired_revision_nine_applied_revision_one_migrates_pending(self):
        f = self.fixture
        plan = f.plan()
        self.assertEqual(f.apply(plan)['state'],'APPLIED')
        with store.connect() as db:
            db.execute('UPDATE grants SET revision=9')
            from test_control_store import drop_recovery_schema
            drop_recovery_schema(db)
            for trigger in ('grant_insert','grant_update','grant_delete'):
                db.execute('DROP TRIGGER '+trigger)
            for table in ('db_job_requests','db_job_operations','db_job_steps','db_job_runs','db_jobs',
                          'metadata_audit','metadata_requests','grant_audit','grant_requests','effective_grants'):
                db.execute('DROP TABLE '+table)
            db.execute('PRAGMA user_version=9')
        with store.connect() as db:
            effective = grants.effective(db,'reader','opaque_0')
            self.assertEqual(effective['maximum_role'],'revoked')
            self.assertGreaterEqual(effective['epoch'],9)


if __name__ == '__main__':
    unittest.main()
