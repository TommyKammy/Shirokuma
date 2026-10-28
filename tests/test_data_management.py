"""Typed registry persistence without an engine, credentials, or runtime services."""
from dataclasses import asdict, FrozenInstanceError, replace
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / 'tools/local_experiment'
sys.path.insert(0, str(TOOLS))
import control_store as store
import data_management as dm


class DataManagementTests(unittest.TestCase):
    def test_grant_cas_tombstone_and_regrant(self):
        database = self.database()
        with store.connect() as db:
            grant = dm.create_grant(db, 'alice', database.id, 'bi_viewer')
            self.assertEqual(grant.revision, 1)
            for role in ('superuser', 'unknown', None, ''):
                with self.assertRaises(ValueError):
                    dm.create_grant(db, 'bob', database.id, role)
                self.assertIsNone(dm.get_grant(db, 'bob', database.id))
                with self.assertRaises(ValueError):
                    dm.update_grant(db, 'alice', database.id, 1, role)
                self.assertEqual(dm.get_grant(db, 'alice', database.id), grant)
            revoked = dm.update_grant(db, 'alice', database.id, 1, 'revoked')
            with self.assertRaises(dm.RevisionConflict):
                dm.update_grant(db, 'alice', database.id, 1, 'data_engineer')
            self.assertEqual(dm.get_grant(db, 'alice', database.id), revoked)
            self.assertEqual(dm.update_grant(db, 'alice', database.id, 2, 'data_engineer').revision, 3)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        env = patch.dict(os.environ, SHIROKUMA_STATE_DIR=directory.name)
        env.start()
        self.addCleanup(env.stop)

    def database(self, name='research'):
        with store.connect() as db:
            return dm.create_database(db, name, 'Research', 'A description', 'operator')

    def schema(self, database):
        with store.connect() as db:
            return dm.create_schema(db, database.id, 'raw', 'Raw', 'Landing', 'operator')

    def binding(self, database, catalog='physical.catalog'):
        with store.connect() as db:
            return dm.create_binding(db, database.id, 'polaris-local', 'realm', catalog,
                                     'storage-ref', 'credential-ref', 's3://bucket/base')

    def test_names_and_server_quoting(self):
        for name in ('a', 'a' * 31):
            self.assertEqual(dm.validate_sql_name(name), name)
        for name in ('a' * 32, '', 'A', '1a', 'a-b', 'a.b', 'a\n',
                     'system', 'information_schema', 'tpch', 'tpcds', None):
            with self.subTest(name=name), self.assertRaises(ValueError):
                dm.validate_sql_name(name)
        self.assertEqual(dm.quote_identifier('physical.catalog'), '"physical.catalog"')
        self.assertEqual(dm.quote_identifier('a"b.c'), '"a""b.c"')

    def test_contexts_are_frozen_without_nested_mutability(self):
        for value in (dm.QueryContext('db', 'schema', 'xs', 'gen'),
                      dm.ResourceRef('db', 'schema', 'object', 1),
                      dm.DestinationLocator('db', 'schema', 'new.table')):
            with self.assertRaises(FrozenInstanceError):
                value.database_id = 'changed'
        with self.assertRaises(ValueError):
            dm.QueryContext([], 'schema', 'xs', 'gen')

    def test_create_get_display_cas_and_new_interpreter(self):
        database = self.database()
        schema = self.schema(database)
        binding = self.binding(database)
        self.assertEqual((binding.desired_revision, binding.applied_revision, binding.revision), (1, 0, 1))
        with store.connect() as db:
            updated = dm.update_database_display(db, database.id, 1, 'Renamed', 'New description')
            changed = dm.update_schema_display(db, schema.id, 1, 'Bronze')
            self.assertEqual(changed.purpose, schema.purpose)
            applied = dm.update_binding(db, database.id, 1, 1, None)
            self.assertEqual((updated.id, updated.sql_catalog, updated.revision),
                             (database.id, 'research', 2))
            self.assertEqual((changed.id, changed.sql_name, changed.revision), (schema.id, 'raw', 2))
            self.assertEqual((applied.desired_revision, applied.applied_revision, applied.revision), (1, 1, 2))
            self.assertEqual(replace(applied, applied_revision=0, revision=1), binding)
            with self.assertRaises(dm.RevisionConflict):
                dm.update_database_display(db, database.id, 1, 'lost', 'lost')
            self.assertEqual(dm.get_database(db, database.id), updated)
            with self.assertRaises(dm.RevisionConflict):
                dm.update_schema_display(db, schema.id, 1, 'lost')
            with self.assertRaises(dm.RevisionConflict):
                dm.update_binding(db, database.id, 1, 0, None)
            self.assertEqual(dm.get_schema(db, schema.id), changed)
            self.assertEqual(dm.get_binding(db, database.id), applied)
        code = '''import json, control_store as s, data_management as d
from dataclasses import asdict
with s.connect() as c:
 print(json.dumps([asdict(d.get_database(c, %r)), asdict(d.get_schema(c, %r)), asdict(d.get_binding(c, %r))]))
''' % (database.id, schema.id, database.id)
        result = subprocess.run([sys.executable, '-c', code], env=dict(os.environ, PYTHONPATH=str(TOOLS)),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [vars(updated), vars(changed), vars(applied)])

    def test_invalid_applied_revision_rolls_back(self):
        database = self.database()
        binding = self.binding(database)
        for applied_revision in (-1, 2):
            with self.subTest(applied_revision=applied_revision):
                with self.assertRaises(sqlite3.IntegrityError), store.connect() as db:
                    dm.update_database_display(db, database.id, 1, 'rolled back', 'rolled back')
                    dm.update_binding(db, database.id, 1, applied_revision, None)
                with store.connect() as db:
                    self.assertEqual(dm.get_binding(db, database.id), binding)
                    self.assertEqual(dm.get_database(db, database.id), database)

    def test_two_connections_same_revision_only_one_wins(self):
        database = self.database()
        schema = self.schema(database)
        self.binding(database)
        updates = (
            lambda db, name: dm.update_database_display(db, database.id, 1, name, name).revision,
                lambda db, name: dm.update_schema_display(db, schema.id, 1, name).revision,
            lambda db, name: dm.update_binding(db, database.id, 1, 1, None).revision)
        for change in updates:
            barrier = threading.Barrier(2)
            def update(name):
                with store.connect() as db:
                    barrier.wait(timeout=5)
                    try:
                        return change(db, name)
                    except dm.RevisionConflict:
                        return 'conflict'
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertCountEqual(list(pool.map(update, ('first', 'second'))), [2, 'conflict'])
        with store.connect() as db:
            row = dm.get_database(db, database.id)
            self.assertEqual(row.display_name, row.description)
            self.assertEqual(row.revision, 2)

    def test_uniqueness_foreign_keys_and_cross_parent_integrity(self):
        database, other = self.database(), self.database('another')
        schema = self.schema(database)
        self.binding(database)
        for action in (lambda c: dm.create_database(c, 'research', 'x', '', 'operator'),
                       lambda c: dm.create_schema(c, database.id, 'raw', 'x', '', 'operator'),
                       lambda c: dm.create_schema(c, 'missing', 'raw', 'x', '', 'operator'),
                       lambda c: dm.create_binding(c, other.id, 'polaris-local', 'realm',
                                                  'physical.catalog', 's', 'c', 's3://b')):
            with self.assertRaises(sqlite3.IntegrityError), store.connect() as db:
                action(db)
        with store.connect() as db:
            dm.record_grant(db, dm.Grant('alice', database.id, 'bi_viewer', 1))
        with self.assertRaises(sqlite3.IntegrityError), store.connect() as db:
            dm.record_grant(db, dm.Grant('alice', database.id, 'admin', 2))
        with self.assertRaises(sqlite3.IntegrityError), store.connect() as db:
            dm.record_query(db, dm.QueryRecord('q', 'actor',
                dm.QueryContext(other.id, schema.id, 'xs', None), 'queued', 'now'))

    def test_object_identity_recreation_and_stale_reference(self):
        database = self.database()
        schema = self.schema(database)
        with store.connect() as db:
            first = dm.observe_object(db, schema.id, 'orders.2026', 'TABLE', 'backend-1', 'Original')
            same = dm.observe_object(db, schema.id, 'orders.2026', 'TABLE', 'backend-1', 'New label')
            self.assertEqual((first.id, first.incarnation), (same.id, same.incarnation))
            self.assertEqual(same.display_name, 'New label')
            new = dm.observe_object(db, schema.id, 'orders.2026', 'TABLE', 'backend-2', 'Replacement')
            self.assertNotEqual(first.id, new.id)
            self.assertEqual(new.incarnation, first.incarnation + 1)
            self.assertIsNone(dm.get_object(db, first.id))
            self.assertEqual(dm.get_object(db, new.id), new)
            # A failed replacement must not leave the previous object retired,
            # even when the caller catches the exception and commits other work.
            with self.assertRaises(sqlite3.IntegrityError):
                dm.observe_object(db, schema.id, 'orders.2026', 'TABLE', 'backend-3', None)
            self.assertEqual(dm.get_object(db, new.id), new)
            self.assertEqual(db.execute('SELECT backend_identity FROM object_bindings WHERE id=?',
                                        (first.id,)).fetchone()[0], 'backend-1')
            with self.assertRaises(dm.RevisionConflict):
                dm.observe_object(db, schema.id, 'orders.2026', 'TABLE', 'backend-1', 'Stale')
            with self.assertRaises(ValueError):
                dm.observe_object(db, schema.id, 'no_identity', 'TABLE', '', 'Missing')
            self.assertEqual(dm.get_object(db, new.id), new)
        code = '''import control_store as s, data_management as d
with s.connect() as c:
 assert d.get_object(c, %r) is None
 current = d.get_object(c, %r)
 assert current.backend_identity == 'backend-2' and current.incarnation == 2
''' % (first.id, new.id)
        result = subprocess.run([sys.executable, '-c', code], env=dict(os.environ, PYTHONPATH=str(TOOLS)),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_snapshot_fields_reject_resource_refs_and_non_scalar_values(self):
        ref = dm.ResourceRef('database', 'schema', 'object', 1)
        generation = dm.WarehouseConfigGeneration('g', 'xs', 'apply', (), (), (), 'draft')
        operation = dm.Operation('op', 'actor', 'observe', (ref,), 1, 'recorded', (), None, (), ())
        for record, fields in (
                (generation, ('database_revisions', 'grant_revisions', 'configuration_refs')),
                (operation, ('query_ids', 'before_after', 'timestamps'))):
            for field in fields:
                for invalid in ((ref,), ((ref,),), ([],), ({},), (set(),), (object(),), ((['nested'],),)):
                    with self.subTest(record=type(record).__name__, field=field, invalid=invalid):
                        with self.assertRaises(ValueError):
                            replace(record, **{field: invalid})
        for invalid_targets in ([ref], ((ref,),), ('object',), ({'object': 'id'},)):
            with self.subTest(targets=invalid_targets), self.assertRaises(ValueError):
                replace(operation, targets=invalid_targets)

    def test_nested_scalar_snapshots_and_resource_targets_round_trip(self):
        nested = (('label', (1, True, None, ('leaf',))),)
        ref = dm.ResourceRef('database', 'schema', 'object', 1)
        generation = dm.WarehouseConfigGeneration('nested-g', 'xs', 'apply', nested, nested, nested, 'draft')
        operation = dm.Operation('nested-op', 'actor', 'observe', (ref,), 1, 'recorded',
                                 nested, None, nested, nested)
        with store.connect() as db:
            dm.record_generation(db, generation)
            dm.record_operation(db, operation)
        with store.connect() as db:
            self.assertEqual(dm.get_generation(db, 'nested-g'), generation)
            self.assertEqual(dm.get_operation(db, 'nested-op'), operation)
        code = '''import control_store as s, data_management as d
with s.connect() as c:
 generation = d.get_generation(c, 'nested-g')
 operation = d.get_operation(c, 'nested-op')
 for snapshot in (generation.database_revisions, generation.grant_revisions,
                  generation.configuration_refs, operation.query_ids,
                  operation.before_after, operation.timestamps):
  assert snapshot == %r
 assert operation.targets == (d.ResourceRef('database', 'schema', 'object', 1),)
''' % (nested,)
        result = subprocess.run([sys.executable, '-c', code], env=dict(os.environ, PYTHONPATH=str(TOOLS)),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_persistent_snapshots_query_admission_and_parent_constraints(self):
        database = self.database()
        other = self.database('another')
        schema = self.schema(database)
        with store.connect() as db:
            obj = dm.observe_object(db, schema.id, 't', 'TABLE', 'backend', 'Table')
            ref = dm.ResourceRef(database.id, schema.id, obj.id, obj.incarnation)
            generation = dm.WarehouseConfigGeneration('g', 'xs', 'apply',
                ((database.id, 1),), (('alice', database.id, 1),), ('storage-ref',), 'draft')
            operation = dm.Operation('op', 'actor', 'observe', (ref,), 1, 'recorded',
                                     ('q',), 'apply', (('before', '{}'), ('after', '{}')),
                                     (('created_at', 'now'),))
            grant = dm.Grant('alice', database.id, 'bi_viewer', 1)
            query = dm.QueryRecord('q', 'actor', dm.QueryContext(database.id, schema.id, 'xs', 'g'),
                                   'recorded', 'now')
            admission = dm.AdmissionRecord('a', 'q', ref, 'recorded', 'now')
            dm.record_generation(db, generation)
            dm.record_operation(db, operation)
            dm.record_grant(db, grant)
            dm.record_query(db, query)
            dm.record_admission(db, admission)
        with store.connect() as db:
            self.assertEqual(dm.get_generation(db, 'g'), generation)
            self.assertEqual(dm.get_operation(db, 'op'), operation)
            self.assertEqual(dm.get_grant(db, 'alice', database.id), grant)
            self.assertEqual(dm.get_query(db, 'q'), query)
            self.assertEqual(dm.get_admission(db, 'a'), admission)
        for bad in (replace(ref, database_id=other.id), replace(ref, incarnation=99)):
            with self.assertRaises(sqlite3.IntegrityError), store.connect() as db:
                dm.record_admission(db, replace(admission, id='bad', resource=bad))
        with self.assertRaises(sqlite3.IntegrityError), store.connect() as db:
            dm.record_query(db, replace(query, id='bad', context=replace(query.context, warehouse_id='s')))
        with self.assertRaises(ValueError):
            replace(generation, configuration_refs=['mutable'])
        code = '''import json, control_store as s, data_management as d
from dataclasses import asdict
with s.connect() as c:
 print(json.dumps([asdict(d.get_generation(c, 'g')), asdict(d.get_operation(c, 'op')),
                   asdict(d.get_query(c, 'q')), asdict(d.get_admission(c, 'a'))]))
'''
        result = subprocess.run([sys.executable, '-c', code], env=dict(os.environ, PYTHONPATH=str(TOOLS)),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = [asdict(record) for record in (generation, operation, query, admission)]
        self.assertEqual(json.loads(result.stdout), json.loads(json.dumps(expected)))


if __name__ == '__main__':
    unittest.main()
