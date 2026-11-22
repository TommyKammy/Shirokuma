"""Cross-feature contracts over genuine accepted records and isolated SQLite."""
from contextlib import contextmanager, ExitStack
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools/local_experiment'))
with tempfile.TemporaryDirectory() as _bootstrap:
    with patch.dict(os.environ, {'SHIROKUMA_STATE_DIR': _bootstrap}):
        import test_database_ai_sql_proposals as sql_tests
        import test_database_ai_catalog as catalog_tests
        import test_database_ai_reexplain as saved_tests
        import db_ai as ai
        import db_ai_sql_proposal as sql
        import db_ai_catalog as catalog
        import db_ai_reexplain as saved
        import control_store as store
        from query_context import QueryError

CASES = ((sql, sql_tests.ProposalTests),
         (catalog, catalog_tests.CatalogTests),
         (saved, saved_tests.SavedPlanTests))


class RecordContracts(unittest.TestCase):
    @contextmanager
    def feature(self, module, fixture_type, *, terminal=True):
        fixture = fixture_type()
        try:
            fixture.setUp()
            record = fixture.accept()
            if terminal:
                record = fixture.finish()
                self.assertEqual(record['state'], 'SUCCEEDED', record)
            with ExitStack() as guards:
                for target in ('_complete', '_capture', '_config'):
                    guards.enter_context(patch.object(ai, target, side_effect=AssertionError(target)))
                guards.enter_context(patch.object(module, '_run', side_effect=AssertionError('reinference')))
                yield fixture, record
        finally:
            fixture.doCleanups()

    def denied(self, fn, code='AI_EXPLANATION_NOT_FOUND', status=404):
        with self.assertRaises(QueryError) as raised:
            fn()
        self.assertEqual((raised.exception.code, raised.exception.status), (code, status))

    def clones(self, fixture, record, count):
        with store.connect() as db:
            row = db.execute('SELECT * FROM ai_explanations WHERE id=?', (record['id'],)).fetchone()
            db.execute('DELETE FROM ai_explanations')
            for i in range(count):
                clone = deepcopy(record)
                clone.update(id=f'contract-{i:03}', created_at='2026-01-01T00:00:00+00:00')
                db.execute('INSERT INTO ai_explanations VALUES (?,?,?,?,?,?,?)',
                    (clone['id'], row['actor'], 'key-'+clone['id'], row['payload'],
                     row['context'], row['pins'], ai._json(clone)))
        return [f'contract-{i:03}' for i in reversed(range(count))]

    def test_actor_full_context_and_kind_are_private_before_authority(self):
        for module, fixture_type in CASES:
            with self.subTest(feature=module.KIND), self.feature(module, fixture_type) as (f, record):
                ident = record['id']
                for principal in (f.f.bob, f.f.owner):
                    self.denied(lambda: module.get(principal, f.context, ident))
                for field, value in f.context.public_metadata().items():
                    wrong = replace(f.context, **{field: value+1 if type(value) is int else 'wrong'})
                    self.denied(lambda: module.get(f.principal, wrong, ident))
                with store.connect() as db:
                    db.execute("UPDATE ai_explanations SET record=json_set(record,'$.kind','other') WHERE id=?", (ident,))
                self.denied(lambda: module.get(f.principal, f.context, ident))
                with store.connect() as db:
                    db.execute("UPDATE ai_explanations SET context='broken',record='broken',pins='broken' WHERE id=?", (ident,))
                # A foreign actor must not parse private JSON, even as platform admin.
                for principal in (f.f.bob, f.f.owner):
                    self.denied(lambda: module.get(principal, f.context, ident))

    def test_current_rights_required_for_get_and_history(self):
        for module, fixture_type in CASES:
            with self.subTest(feature=module.KIND), self.feature(module, fixture_type) as (f, record):
                with store.connect() as db:
                    db.execute("UPDATE schemas SET purpose='restricted' WHERE id=?", (f.context.schema_id,))
                for read in (lambda: module.get(f.principal, f.context, record['id']),
                             lambda: module.history(f.principal, f.context)):
                    self.denied(read, 'SQL_PERMISSION_DENIED', 403)

    def test_history_fills_fifty_visible_and_uses_feature_get_in_order(self):
        for module, fixture_type in CASES:
            with self.subTest(feature=module.KIND), self.feature(module, fixture_type) as (f, record):
                self.clones(f, record, 57)
                # Newest IDs sort below both older groups; ties still use ID DESC.
                groups = (('2026-01-03T00:00:00+00:00', range(18, -1, -1)),
                          ('2026-01-02T00:00:00+00:00', range(56, 37, -1)),
                          ('2026-01-01T00:00:00+00:00', range(37, 18, -1)))
                ids = []
                with store.connect() as db:
                    for created_at, numbers in groups:
                        for number in numbers:
                            ident = f'contract-{number:03}'
                            ids.append(ident)
                            db.execute("UPDATE ai_explanations SET record=json_set(record,'$.created_at',?) WHERE id=?",
                                       (created_at, ident))
                original = module.get
                rejected = set(ids[:4])
                def observe(principal, context, ident):
                    if ident in rejected:
                        raise QueryError('AI_EXPLANATION_NOT_FOUND', 404)
                    return original(principal, context, ident)
                with patch.object(module, 'get', side_effect=observe) as get:
                    results = module.history(f.principal, f.context)
                self.assertEqual([r['id'] for r in results], ids[4:54])
                self.assertEqual([c.args[2] for c in get.call_args_list], ids[:54])
                with patch.object(module, 'get', side_effect=RuntimeError('unexpected')):
                    with self.assertRaisesRegex(RuntimeError, 'unexpected'):
                        module.history(f.principal, f.context)

    def test_history_filters_actor_canonical_full_context_and_kind(self):
        for module, fixture_type in CASES:
            with self.subTest(feature=module.KIND), self.feature(module, fixture_type) as (f, record):
                ids = self.clones(f, record, 5)
                with store.connect() as db:
                    db.execute("UPDATE ai_explanations SET actor='bob' WHERE id=?", (ids[0],))
                    changed = dict(f.context.public_metadata(), grant_revision=999)
                    db.execute('UPDATE ai_explanations SET context=? WHERE id=?', (ai._json(changed), ids[1]))
                    db.execute("UPDATE ai_explanations SET record=json_set(record,'$.kind','other') WHERE id=?", (ids[2],))
                    db.execute('UPDATE ai_explanations SET context=? WHERE id=?',
                        (json.dumps(f.context.public_metadata(), indent=2), ids[3]))
                with patch.object(module, 'get', wraps=module.get) as get:
                    self.assertEqual([r['id'] for r in module.history(f.principal, f.context)], ids[4:])
                self.assertEqual([c.args[2] for c in get.call_args_list], ids[4:])

    def test_history_rechecks_saved_pins_after_later_get(self):
        for module, fixture_type in CASES:
            with self.subTest(feature=module.KIND), self.feature(module, fixture_type) as (f, record):
                ids = self.clones(f, record, 2)
                original = module.get
                def observe(principal, context, ident):
                    result = original(principal, context, ident)
                    if ident == ids[-1]:
                        with store.connect() as db:
                            pins = json.loads(db.execute('SELECT pins FROM ai_explanations WHERE id=?', (ids[0],)).fetchone()[0])
                            pins[0].update(schema='hidden', schema_id=f.f.schemas[context.database_id]['hidden'].id)
                            db.execute('UPDATE ai_explanations SET pins=? WHERE id=?', (ai._json(pins), ids[0]))
                    return result
                with patch.object(module, 'get', side_effect=observe) as get:
                    self.denied(lambda: module.history(f.principal, f.context), 'SQL_PERMISSION_DENIED', 403)
                self.assertEqual(get.call_count, 2)

    def test_empty_history_checks_context_authority_before_and_after(self):
        for module, fixture_type in CASES:
            with self.subTest(feature=module.KIND), self.feature(module, fixture_type) as (f, record):
                with store.connect() as db:
                    db.execute('DELETE FROM ai_explanations')
                authority_module = ai if module is saved else module
                original = authority_module._authority
                def revoke_after_check(*args):
                    original(*args)
                    with store.connect() as db:
                        db.execute("UPDATE schemas SET purpose='restricted' WHERE id=?", (f.context.schema_id,))
                with patch.object(authority_module, '_authority', side_effect=revoke_after_check) as authority:
                    self.denied(lambda: module.history(f.principal, f.context), 'SQL_PERMISSION_DENIED', 403)
                self.assertEqual(authority.call_count, 2)

    def test_history_revalidates_every_result_then_final_context(self):
        for module, fixture_type in CASES:
            with self.subTest(feature=module.KIND), self.feature(module, fixture_type) as (f, record):
                ids = self.clones(f, record, 3)
                events = []
                authority_module = ai if module is saved else module
                authority, get = authority_module._authority, module.get
                def check(principal, context, pins=()):
                    authority(principal, context, pins)
                    events.append(('authority', [p['schema'] for p in pins]))
                def observe(*args):
                    result = get(*args)
                    events.append(('get', result['id']))
                    return result
                with store.connect() as db:
                    original_pins = json.loads(db.execute('SELECT pins FROM ai_explanations WHERE id=?', (ids[0],)).fetchone()[0])
                    for ident, schema in zip(ids, ('raw', 'sandbox', 'marts')):
                        pins = deepcopy(original_pins)
                        pins[0].update(schema=schema, schema_id=f.f.schemas[f.context.database_id][schema].id)
                        db.execute('UPDATE ai_explanations SET pins=? WHERE id=?', (ai._json(pins), ident))
                with patch.object(authority_module, '_authority', side_effect=check), \
                        patch.object(module, 'get', side_effect=observe):
                    self.assertEqual([r['id'] for r in module.history(f.principal, f.context)], ids)
                self.assertEqual(events[-5:], [('get', ids[-1]), ('authority', ['raw']),
                    ('authority', ['sandbox']), ('authority', ['marts']), ('authority', [])])

    def test_history_revocation_after_earlier_get_denies_whole_response(self):
        for module, fixture_type in CASES:
            with self.subTest(feature=module.KIND), self.feature(module, fixture_type) as (f, record):
                ids = self.clones(f, record, 2)
                schema_id = f.f.schemas[f.context.database_id]['sandbox'].id
                with store.connect() as db:
                    pins = json.loads(db.execute('SELECT pins FROM ai_explanations WHERE id=?', (ids[0],)).fetchone()[0])
                    pins[0].update(schema='sandbox', schema_id=schema_id)
                    db.execute('UPDATE ai_explanations SET pins=? WHERE id=?', (ai._json(pins), ids[0]))
                get = module.get
                def observe(*args):
                    result = get(*args)
                    if result['id'] == ids[-1]:
                        with store.connect() as db:
                            db.execute("UPDATE schemas SET purpose='restricted' WHERE id=?", (schema_id,))
                    return result
                with patch.object(module, 'get', side_effect=observe) as observed:
                    self.denied(lambda: module.history(f.principal, f.context), 'SQL_PERMISSION_DENIED', 403)
                self.assertEqual(observed.call_count, 2)

    def test_active_snapshot_terminal_commit_before_owner_preserves_output(self):
        for module, fixture_type in CASES:
            with self.subTest(feature=module.KIND), self.feature(module, fixture_type) as (f, terminal):
                ident = terminal['id']
                active = dict(terminal, state='RUNNING', inference_state='RUNNING')
                with store.connect() as db:
                    db.execute('UPDATE ai_explanations SET record=? WHERE id=?', (ai._json(active), ident))
                acquire = ai._owner
                owners = []
                def commit_then_acquire(key):
                    with store.connect() as db:
                        db.execute('UPDATE ai_explanations SET record=? WHERE id=?', (ai._json(terminal), ident))
                    owner = acquire(key)
                    owners.append(owner)
                    return owner
                settle = module._settle
                def settling(*args):
                    self.assertFalse(owners[-1].closed)
                    return settle(*args)
                with patch.object(ai, '_owner', side_effect=commit_then_acquire) as owner, \
                        patch.object(module, '_settle', side_effect=settling) as recovery:
                    self.assertEqual(module.get(f.principal, f.context, ident), terminal)
                owner.assert_called_once_with(ident)
                self.assertTrue(owners[0].closed)
                recovery.assert_called_once_with(f.principal, f.context, ident, 'UNKNOWN', 'AI_OWNER_LOST')
                self.assertEqual(json.loads(ai._row(ident)['record']), terminal)

    def test_busy_owner_still_rereads_without_settle(self):
        for module, fixture_type in CASES:
            with self.subTest(feature=module.KIND), self.feature(module, fixture_type, terminal=False) as (f, record):
                terminal = dict(record, state='UNKNOWN', inference_state='UNKNOWN', error_code='AI_PROVIDER_TIMEOUT')
                def busy(key):
                    with store.connect() as db:
                        db.execute('UPDATE ai_explanations SET record=? WHERE id=?', (ai._json(terminal), key))
                    raise BlockingIOError()
                with patch.object(ai, '_owner', side_effect=busy), patch.object(module, '_settle') as settle:
                    self.assertEqual(module.get(f.principal, f.context, record['id']), terminal)
                settle.assert_not_called()

    def test_recovery_failure_always_closes_owner(self):
        for module, fixture_type in CASES:
            with self.subTest(feature=module.KIND), self.feature(module, fixture_type, terminal=False) as (f, record):
                f.owners[-1].close()
                owner = ai._owner(record['id'])
                try:
                    with patch.object(ai, '_owner', return_value=owner), \
                            patch.object(module, '_settle', side_effect=QueryError('SQL_PERMISSION_DENIED', 403)):
                        self.denied(lambda: module.get(f.principal, f.context, record['id']), 'SQL_PERMISSION_DENIED', 403)
                    self.assertTrue(owner.closed)
                finally:
                    owner.close()

    def test_nested_get_and_history_results_are_isolated(self):
        for module, fixture_type in CASES:
            with self.subTest(feature=module.KIND), self.feature(module, fixture_type) as (f, record):
                for result in (module.get(f.principal, f.context, record['id']),
                               module.history(f.principal, f.context)[0]):
                    result['input']['language'] = 'changed'
                    result['metadata'].clear()
                    result['context']['database_id'] = 'changed'
                self.assertEqual(module.get(f.principal, f.context, record['id']), record)
                self.assertEqual(json.loads(ai._row(record['id'])['record']), record)

    def test_feature_save_uses_only_caller_transaction_and_dynamic_clock(self):
        for module, fixture_type in CASES:
            with self.subTest(feature=module.KIND), self.feature(module, fixture_type) as (f, record):
                changed = deepcopy(record)
                changed['input']['language'] = 'changed'
                with store.connect() as writer, store.connect() as reader:
                    before = reader.execute('SELECT record FROM ai_explanations WHERE id=?', (record['id'],)).fetchone()[0]
                    writer.execute('BEGIN IMMEDIATE')
                    with patch.object(ai, '_now', return_value='contract-clock'), \
                            patch.object(store, 'connect', side_effect=AssertionError('hidden connection')):
                        module._save(writer, changed)
                    self.assertEqual(changed['updated_at'], 'contract-clock')
                    self.assertEqual(writer.execute('SELECT record FROM ai_explanations WHERE id=?', (record['id'],)).fetchone()[0], ai._json(changed))
                    self.assertEqual(reader.execute('SELECT record FROM ai_explanations WHERE id=?', (record['id'],)).fetchone()[0], before)
                    writer.rollback()
                    self.assertEqual(writer.execute('SELECT record FROM ai_explanations WHERE id=?', (record['id'],)).fetchone()[0], before)


if __name__ == '__main__':
    unittest.main()
