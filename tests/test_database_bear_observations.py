"""Read-only observation contract with isolated stores and real HTTP parsers."""
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from http.client import HTTPResponse
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools/local_experiment'))
# Handler imports may initialize legacy configuration; isolate before imports.
with tempfile.TemporaryDirectory() as _bootstrap:
    with patch.dict(os.environ, {'SHIROKUMA_STATE_DIR': _bootstrap}):
        import test_database_ai as fixtures
        import test_database_ai_sources as source_fixtures
        import db_bear_observations as observations
        import audit_service as audit
        import control_store as store
        import data_management as dm
        import request_auth as auth
        import workbench_server as wb
        import warehouse_resident as wr
        import runtime_profile
from query_context import QueryError

NOW = '2026-11-01T01:00:00Z'


class ObservationTests(unittest.TestCase):
    patch = fixtures.DatabaseAITests.patch
    ctx = fixtures.DatabaseAITests.ctx
    metadata = staticmethod(fixtures.DatabaseAITests.metadata)
    catalog = fixtures.DatabaseAITests.catalog
    record = source_fixtures.SourceTests.record
    snapshot = source_fixtures.SourceTests.snapshot

    def setUp(self):
        fixtures.DatabaseAITests.setUp(self)
        for target in ('dataops_routes.current_context', 'query_context.resolve_context',
                'query_service.history', 'query_service.inspect_query', 'db_ai.get',
                'db_ai.history', 'db_ai._owner', 'db_ai._config', 'db_ai._capture',
                'db_ai._complete', 'database_inventory.catalog_api',
                'database_inventory.resolve_object', 'database_inventory.object_detail',
                'warehouse_lifecycle.ensure_awake', 'logical_registry._credentials',
                'bears_resident.resolve_api_key', 'bears_resident.sre', 'bears_resident.policy'):
            self.patch(patch(target, side_effect=AssertionError(target)))

    def observe(self, context=None, principal=None):
        return observations.observe(principal or self.principal, context or self.context)

    def error(self, code, fn):
        with self.assertRaises(QueryError) as raised:
            fn()
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def ai(self, ident='ai', *, context=None, actor='alice', changes=None,
           raw=None, saved=None):
        c = (context or self.context).public_metadata()
        record = dict(id=ident, actor=actor, context=c, kind='sql_explanation',
            state='SUCCEEDED', created_at=NOW, updated_at=NOW,
            input={'sql': 'HOSTILE SQL'}, error_code='PRIVATE ERROR',
            metadata={'secret': 'PRIVATE METADATA'}, pins=['PRIVATE PINS'],
            model={'name': 'PRIVATE MODEL'}, text='PRIVATE INFERENCE')
        record.update(changes or {})
        with store.connect() as db:
            db.execute('INSERT INTO ai_explanations VALUES (?,?,?,?,?,?,?)',
                (ident, actor, ident, '{}', saved if saved is not None else json.dumps(c),
                 '["PRIVATE PINS"]', raw if raw is not None else json.dumps(record)))
        return record

    def closed(self, reply):
        self.assertEqual(set(reply), {'context', 'observed_at', 'coverage', 'sre', 'policy', 'platform'})
        self.assertEqual(reply['coverage'], dict(kind='latest_persisted_own_context',
            limit_per_source=20, full_history=False, live_runtime=False))
        self.assertEqual(set(reply['sre']), {'queries', 'ai'})
        for source in reply['sre'].values():
            self.assertEqual(set(source), {'items', 'has_more', 'omitted'})
            self.assertIs(type(source['has_more']), bool)
            self.assertIs(type(source['omitted']), int)
            self.assertLessEqual(len(source['items']) + source['omitted'], 20)
            for item in source['items']:
                self.assertEqual(set(item), {'id', 'kind', 'state', 'created_at', 'updated_at'})
        self.assertEqual(set(reply['policy']), {'context_authorized', 'schema_permissions',
            'sql_modes', 'statement_approval', 'shared_local'})
        self.assertEqual(set(reply['platform']), {'allowed', 'code'})
        self.assertEqual(set(reply['policy']['schema_permissions']), {'browse', 'query', 'write'})

    def test_closed_projection_states_stored_times_and_zero_writes(self):
        for i, state in enumerate(observations.QUERY_STATES):
            self.record('q' + str(i), state=state)
        for i, state in enumerate(observations.AI_STATES):
            self.ai('a' + str(i), changes={'state': state})
        before = self.snapshot()
        with patch.object(store, 'connect', side_effect=AssertionError('write-capable connect')), \
                patch.object(store, 'root', side_effect=AssertionError('root')), \
                patch('urllib.request.urlopen', side_effect=AssertionError('network')), \
                patch('subprocess.run', side_effect=AssertionError('subprocess')):
            reply = self.observe()
        self.closed(reply)
        self.assertEqual(reply['context'], self.context.public_metadata())
        self.assertEqual({i['state'] for i in reply['sre']['queries']['items']}, observations.QUERY_STATES)
        self.assertEqual({i['state'] for i in reply['sre']['ai']['items']}, observations.AI_STATES)
        self.assertTrue(all(i['updated_at'] is None for i in reply['sre']['queries']['items']))
        self.assertTrue(all(i['created_at'] == NOW and i['updated_at'] == NOW
                            for i in reply['sre']['ai']['items']))
        for marker in ('HOSTILE', 'PRIVATE', 'SECRET', 'sql_status', 'inference_state', 'error_code'):
            self.assertNotIn(marker, json.dumps(reply))
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.catalog_calls)

    def test_snapshot_is_query_only_in_both_new_transactions(self):
        original = audit.snapshot
        connections = []
        @contextmanager
        def checked():
            with original() as db:
                self.assertEqual(db.execute('PRAGMA query_only').fetchone()[0], 1)
                self.assertTrue(db.in_transaction)
                with self.assertRaises(sqlite3.OperationalError):
                    db.execute("UPDATE query_records SET state='FAILED'")
                connections.append(db)
                yield db
        with patch.object(audit, 'snapshot', checked):
            self.observe()
        self.assertEqual(len(connections), 2)
        self.assertIsNot(connections[0], connections[1])

    def test_actor_two_database_schema_role_isolation(self):
        self.record()
        self.ai()
        for context, principal in ((self.ctx(database=self.databases[1]), self.principal),
                (self.ctx(schema='sandbox'), self.principal), (self.ctx('admin'), self.principal),
                (self.context, self.bob), (self.ctx('admin', principal=self.owner), self.owner)):
            with self.subTest(context=context, actor=principal.principal_id):
                reply = self.observe(context, principal)
                self.assertEqual(reply['sre'], {k: dict(items=[], has_more=False, omitted=0)
                                               for k in ('queries', 'ai')})

    def test_all_nine_saved_fields_and_typed_matching(self):
        self.record()
        self.ai()
        for key in observations.FIELDS:
            changed = 999 if key.endswith('revision') else 'other'
            table = 'query_records' if key in ('database_id', 'schema_id', 'warehouse_id', 'generation_id') else 'query_execution_contexts'
            original = getattr(self.context, key)
            with self.subTest(key=key):
                altered = dict(self.context.public_metadata(), **{key: changed})
                with store.connect() as db:
                    db.execute('PRAGMA foreign_keys=OFF')
                    db.execute(f'UPDATE {table} SET {key}=?', (changed,))
                    db.execute('UPDATE ai_explanations SET context=?', (json.dumps(altered),))
                reply = self.observe()
                self.assertEqual(reply['sre']['queries']['items'], [])
                self.assertEqual(reply['sre']['ai']['items'], [])
                with store.connect() as db:
                    db.execute('PRAGMA foreign_keys=OFF')
                    db.execute(f'UPDATE {table} SET {key}=?', (original,))
                    db.execute('UPDATE ai_explanations SET context=?', (json.dumps(self.context.public_metadata()),))
        for val in (True, '1', 1.0):
            with store.connect() as db:
                altered = dict(self.context.public_metadata(), binding_revision=val)
                db.execute('UPDATE ai_explanations SET context=?', (json.dumps(altered),))
            self.assertEqual(self.observe()['sre']['ai']['items'], [])

    def test_null_grant_is_explicit_and_missing_query_context_is_excluded(self):
        c = self.ctx('admin', principal=self.owner)
        self.record(actor='owner', context=c)
        self.ai(actor='owner', context=c)
        self.assertIsNone(c.grant_revision)
        self.assertEqual(len(self.observe(c, self.owner)['sre']['ai']['items']), 1)
        saved = c.public_metadata()
        del saved['grant_revision']
        with store.connect() as db:
            db.execute('UPDATE ai_explanations SET context=?', (json.dumps(saved),))
            db.execute('DELETE FROM query_execution_contexts')
        self.assertEqual(self.observe(c, self.owner)['sre'], {
            k: dict(items=[], has_more=False, omitted=0) for k in ('queries', 'ai')})

    def test_current_roles_permissions_platform_flags_and_primary_schema(self):
        with store.connect() as db:
            for database in self.databases:
                dm.create_grant(db, 'owner', database.id, 'admin')
                db.execute("UPDATE effective_grants SET maximum_role='admin',applied_revision=1,applied_generation_id=? WHERE principal_id='owner' AND database_id=?",
                    ('g-' + database.sql_catalog, database.id))
        for role, schema, modes in (('bi_viewer', 'marts', ['read-only']),
                ('data_engineer', 'raw', ['read-only', 'sandbox-write']),
                ('admin', 'raw', ['read-only', 'sandbox-write', 'admin-write'])):
            for principal in (self.principal, self.owner):
                with self.subTest(role=role, actor=principal.principal_id):
                    reply = self.observe(self.ctx(role, schema, principal), principal)
                    self.assertEqual(reply['policy']['sql_modes'], modes)
                    allowed = principal is self.owner and role == 'admin'
                    self.assertEqual(reply['platform'], dict(allowed=allowed,
                        code='PLATFORM_OBSERVATION_DEFERRED' if allowed else 'PLATFORM_ADMIN_REQUIRED'))
        self.error('SQL_PERMISSION_DENIED', lambda: self.observe(replace(self.context, selected_role='bi_viewer')))
        with store.connect() as db:
            db.execute("UPDATE schemas SET purpose='hidden' WHERE id=?", (self.context.schema_id,))
        self.error('SQL_PERMISSION_DENIED', self.observe)

    def test_disabled_revoked_and_role_ceiling(self):
        for field, value in (('enabled', False), ('disabled', True), ('role', 'bi_viewer')):
            with self.subTest(field=field):
                old = dict(self.users['alice'])
                self.users['alice'][field] = value
                self.error('NOT_FOUND', self.observe)
                self.users['alice'] = old
        self.error('NOT_FOUND', lambda: self.observe(principal=replace(self.principal, maximum_role='bi_viewer')))
        with store.connect() as db:
            db.execute("UPDATE effective_grants SET maximum_role='revoked' WHERE principal_id='alice'")
        self.error('NOT_FOUND', self.observe)

    def test_final_fresh_fence_account_platform_grant_binding_schema_changes(self):
        original = audit.snapshot
        mutations = ('account', 'platform', 'grant', 'binding', 'schema', 'database', 'generation')
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                before = self.snapshot()
                users = json.loads(json.dumps(self.users))
                calls = 0
                @contextmanager
                def changed():
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        if mutation == 'account':
                            self.users['alice']['enabled'] = False
                        elif mutation == 'platform':
                            self.users['alice']['platform_admin'] = True
                        else:
                            with store.connect() as db:
                                if mutation == 'grant':
                                    db.execute("UPDATE effective_grants SET epoch=epoch+1 WHERE principal_id='alice'")
                                elif mutation == 'binding':
                                    db.execute('UPDATE catalog_bindings SET desired_revision=desired_revision+1')
                                elif mutation == 'schema':
                                    db.execute("UPDATE schemas SET purpose='hidden' WHERE id=?", (self.context.schema_id,))
                                elif mutation == 'generation':
                                    db.execute('PRAGMA foreign_keys=OFF')
                                    db.execute("UPDATE warehouse_config_generations SET warehouse_id='OTHER'")
                                else:
                                    db.execute("UPDATE databases SET state='FAILED'")
                    with original() as db:
                        yield db
                with patch.object(audit, 'snapshot', changed), self.assertRaises(QueryError):
                    self.observe()
                self.assertEqual(calls, 2)
                self.users.clear()
                self.users.update(users)
                # Each case restores only its deliberately mutated fixture.
                with store.connect() as db:
                    if mutation == 'grant': db.execute("UPDATE effective_grants SET epoch=epoch-1 WHERE principal_id='alice'")
                    if mutation == 'binding': db.execute('UPDATE catalog_bindings SET desired_revision=desired_revision-1')
                    if mutation == 'schema': db.execute("UPDATE schemas SET purpose='raw' WHERE id=?", (self.context.schema_id,))
                    if mutation == 'database': db.execute("UPDATE databases SET state='READY'")
                    if mutation == 'generation':
                        db.execute('PRAGMA foreign_keys=OFF')
                        db.execute("UPDATE warehouse_config_generations SET warehouse_id='LOCAL'")
                self.assertEqual(before, self.snapshot())

    def test_fixed_twenty_window_sentinel_no_refill_and_rowid_ties(self):
        for i in range(23):
            self.record(f'q{i:02}', state='INVALID' if i >= 20 else 'FINISHED')
            self.ai(f'a{i:02}', changes={'kind': 'unsupported'} if i >= 20 else None)
        reply = self.observe()
        for key, prefix in (('queries', 'q'), ('ai', 'a')):
            source = reply['sre'][key]
            self.assertEqual(source['has_more'], True)
            self.assertEqual(source['omitted'], 3)
            self.assertEqual([i['id'] for i in source['items']], [f'{prefix}{i:02}' for i in range(19, 2, -1)])
        with store.connect() as db:
            db.execute("DELETE FROM query_execution_contexts WHERE query_id IN ('q00','q01','q02')")
            db.execute("DELETE FROM query_records WHERE id IN ('q00','q01','q02')")
            db.execute("DELETE FROM ai_explanations WHERE id IN ('a00','a01','a02')")
        self.assertFalse(self.observe()['sre']['ai']['has_more'])

    def test_invalid_utf8_query_state_is_omitted_inside_window_without_refill(self):
        for i in range(21):
            self.record(f'q{i:02}', state='FINISHED' if i % 2 else 'UNKNOWN', created=NOW)
        with store.connect() as db:
            db.execute('UPDATE query_records SET state=CAST(? AS TEXT) WHERE id=?',
                       (b'\xff', 'q20'))
            row = db.execute('SELECT typeof(state),CAST(state AS BLOB) FROM query_records WHERE id=?',
                             ('q20',)).fetchone()
            self.assertEqual(tuple(row), ('text', b'\xff'))
        path = self.root / 'jobs.sqlite3'
        before = path.read_bytes()
        with patch.object(store, 'connect', side_effect=AssertionError('write-capable connect')):
            source = self.observe()['sre']['queries']
        self.assertEqual(source['omitted'], 1)
        self.assertTrue(source['has_more'])
        self.assertEqual([item['id'] for item in source['items']],
                         [f'q{i:02}' for i in range(19, 0, -1)])
        self.assertEqual([item['state'] for item in source['items']],
                         ['FINISHED' if i % 2 else 'UNKNOWN' for i in range(19, 0, -1)])
        self.assertEqual(path.read_bytes(), before)

    def test_order_is_actual_instant_then_rowid(self):
        self.record('z', created='2026-11-01T01:00:00Z')
        self.record('a', created='2026-11-01T02:00:00+01:00')
        self.record('older', created='2026-11-01T02:30:00+02:00')
        self.assertEqual([i['id'] for i in self.observe()['sre']['queries']['items']], ['a', 'z', 'older'])

    def test_ai_only_twenty_bounded_candidates_are_decoded_and_sentinel_is_not_fetched(self):
        for i in range(21):
            self.ai(str(i))
        decode = observations._decode
        decoded = []
        def bounded(raw, cap):
            self.assertLessEqual(len(raw), cap)
            decoded.append(cap)
            return decode(raw, cap)
        with patch.object(observations, '_decode', side_effect=bounded):
            source = self.observe()['sre']['ai']
        self.assertEqual(decoded, [8192, 262144] * 20)
        self.assertEqual(len(source['items']), 20)
        self.assertTrue(source['has_more'])
        self.assertNotIn('0', [i['id'] for i in source['items']])

    def test_invalid_utf8_records_and_printable_id_caps_are_omitted(self):
        self.ai('invalid-utf8')
        self.ai('x' * 257)
        self.ai('bad\nidentifier')
        self.record('q' * 257)
        with store.connect() as db:
            raw = json.dumps(dict(id='invalid-utf8', actor='alice', context=self.context.public_metadata(),
                kind='sql_explanation', state='SUCCEEDED', created_at=NOW, updated_at=NOW))
            db.execute('UPDATE ai_explanations SET record=CAST(? AS TEXT) WHERE id=?',
                ((raw[:-1] + ',"private":"').encode() + b'\xff"}', 'invalid-utf8'))
        reply = self.observe()['sre']
        self.assertEqual(reply['ai'], dict(items=[], has_more=False, omitted=3))
        self.assertEqual(reply['queries'], dict(items=[], has_more=False, omitted=1))

    def test_all_supported_ai_kinds_are_preserved(self):
        for kind in observations.AI_KINDS:
            self.ai(kind, changes={'kind': kind})
        self.assertEqual({i['kind'] for i in self.observe()['sre']['ai']['items']}, observations.AI_KINDS)

    def test_ai_invalid_candidates_are_omitted_without_diagnostics(self):
        cases = [dict(kind='query'), dict(state='FINISHED'), dict(id='other'),
            dict(actor='bob'), dict(updated_at=None), dict(updated_at='2026-02-30T00:00:00Z'),
            dict(context=dict(self.context.public_metadata(), grant_revision=True)),
            dict(metadata={'deep': [[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]})]
        for i, changes in enumerate(cases):
            self.ai(str(i), changes=changes)
        base = self.ai('good')
        self.ai('duplicate', raw=json.dumps(dict(base, id='duplicate'))[:-1] + ',"state":"FAILED"}')
        self.ai('overflow', raw=json.dumps(dict(base, id='overflow'))[:-1] + ',"extra":1e999}')
        reply = self.observe()['sre']['ai']
        self.assertEqual([i['id'] for i in reply['items']], ['good'])
        self.assertEqual(reply['omitted'], len(cases) + 2)
        self.assertFalse(reply['has_more'])

    def test_malformed_oversized_missing_context_and_untimed_rows_are_ineligible(self):
        saved = self.context.public_metadata()
        contexts = ['{', json.dumps(dict(saved, extra='x')), json.dumps(dict(saved, padding='あ' * 5000)),
                    json.dumps(saved).replace('"grant_revision": 1', '"grant_revision": null'),
                    json.dumps(saved)[:-1] + ',"schema_id":"duplicate"}',
                    json.dumps(dict(saved, schema_id=[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]))]
        for i, c in enumerate(contexts):
            self.ai(str(i), saved=c)
        self.ai('malformed', raw='{')
        self.ai('oversized-record', changes={'metadata': 'あ' * 90000})
        # JSON-invalid records have no safe SQL timestamp and are ineligible.
        self.ai('nonfinite', raw='{"created_at":"' + NOW + '","extra":NaN}')
        self.ai('untimed', changes={'created_at': 'bad'})
        self.record('untimed-query', created='bad')
        self.assertEqual(self.observe()['sre'], {k: dict(items=[], has_more=False, omitted=0)
                                               for k in ('queries', 'ai')})

    def test_record_json_sql_only_receives_exact_context_byte_bounded_records(self):
        self.ai('valid')
        self.ai('foreign-small', context=self.ctx(database=self.databases[1]),
                changes={'metadata': 'FOREIGN-RECORD-TRIPWIRE'})
        for name, context in (('same-large', self.context),
                              ('foreign-large', self.ctx(database=self.databases[1]))):
            record = self.ai(name, context=context, changes={'metadata': 'あ' * 88000})
            # Character count is below the cap; UTF-8 byte count is above it.
            raw = json.dumps(record, ensure_ascii=False)
            self.assertLess(len(raw), 262144)
            self.assertGreater(len(raw.encode('utf-8')), 262144)
            with store.connect() as db:
                db.execute('UPDATE ai_explanations SET record=? WHERE id=?', (raw, name))
        self.ai('malformed-record', raw='{unparseable')
        original = audit.snapshot
        parsed = []
        violations = []
        with sqlite3.connect(':memory:') as parser:
            def spy(function, raw, *args):
                if raw is not None:
                    encoded = raw.encode('utf-8') if isinstance(raw, str) else raw
                    if (len(encoded) > 262144 or 'FOREIGN-RECORD-TRIPWIRE' in str(raw)):
                        violations.append(function)
                        raise AssertionError('Unscoped or oversized record reached JSON SQL')
                    if 'created_at' in str(raw):
                        parsed.append((function, json.loads(raw)['id']))
                placeholders = ','.join('?' for _ in (raw, *args))
                return parser.execute(f'SELECT {function}({placeholders})', (raw, *args)).fetchone()[0]
            @contextmanager
            def checked():
                with original() as db:
                    db.create_function('json_valid', 1, lambda raw: spy('json_valid', raw))
                    db.create_function('json_extract', 2, lambda raw, path: spy('json_extract', raw, path))
                    yield db
            with patch.object(audit, 'snapshot', checked):
                source = self.observe()['sre']['ai']
        self.assertEqual(violations, [])
        self.assertTrue(parsed)
        self.assertEqual({ident for _, ident in parsed}, {'valid'})
        self.assertEqual(source['omitted'], 0)
        self.assertFalse(source['has_more'])
        self.assertEqual([i['id'] for i in source['items']], ['valid'])

    def test_unsafe_timestamps_are_excluded_before_both_candidate_windows(self):
        invalid = ('now', '2026-11-01', '2026-11-01T01:00:00',
                   '2026-11-01T01:00:00+0900', '2026-11-01 01:00:00Z',
                   '2026-11-01T01:00:00.Z', '2026-11-01T01:00:00.+09:00',
                   '2026-11-01T01:00:00.1xZ', '2026-11-01T01:00:00+00:00Z')
        self.record('q-valid')
        self.ai('a-valid')
        # More than a window of ineligible rows cannot displace valid evidence
        # or manufacture the 21st-candidate sentinel.
        for i in range(27):
            value = invalid[i % len(invalid)]
            self.record(f'q-invalid-{i}', created=value)
            self.ai(f'a-invalid-{i}', changes={'created_at': value})
        reply = self.observe()['sre']
        for key, ident in (('queries', 'q-valid'), ('ai', 'a-valid')):
            self.assertEqual([i['id'] for i in reply[key]['items']], [ident])
            self.assertEqual(reply[key]['omitted'], 0)
            self.assertFalse(reply[key]['has_more'])

    def test_canonical_offsets_and_long_fractions_are_preserved(self):
        values = ('2026-11-01T01:00:00Z', '2026-11-01T10:00:00+09:00',
                  '2026-10-31T20:00:00-05:00', '2026-11-01T01:00:00.123456789Z',
                  '2026-11-01T10:00:00.12345678901234567890+09:00',
                  '2026-11-01T01:00:00.' + '1' * 43 + 'Z')
        for i, value in enumerate(values):
            self.assertLessEqual(len(value), 64)
            self.record(f'q{i}', created=value)
            self.ai(f'a{i}', changes={'created_at': value, 'updated_at': value})
        reply = self.observe()
        for key in ('queries', 'ai'):
            self.assertEqual({i['created_at'] for i in reply['sre'][key]['items']}, set(values))
            self.assertEqual(reply['sre'][key]['omitted'], 0)
        self.assertEqual({i['updated_at'] for i in reply['sre']['ai']['items']}, set(values))
        self.assertTrue(reply['observed_at'].endswith('Z'))

    def test_safe_timestamp_candidates_stay_finite_with_no_refill(self):
        for i in range(23):
            self.record(f'q{i:02}', created='2026-02-30T01:00:00Z' if i >= 20 else '2026-02-01T01:00:00Z')
            self.ai(f'a{i:02}', changes={'created_at': '2026-02-01T01:00:00Z'})
        with store.connect() as db:
            for i in (20, 21, 22):
                row = db.execute('SELECT record FROM ai_explanations WHERE id=?', (f'a{i:02}',)).fetchone()
                raw = row[0]
                if i == 20:
                    raw = raw[:-1] + ',"state":"FAILED"}'
                elif i == 21:
                    record = json.loads(raw)
                    record['kind'] = 'unsupported'
                    raw = json.dumps(record)
                else:
                    record = json.loads(raw)
                    record['created_at'] = '2026-02-30T01:00:00Z'
                    raw = json.dumps(record)
                db.execute('UPDATE ai_explanations SET record=? WHERE id=?', (raw, f'a{i:02}'))
        # These rows cannot supply safe creation time, so they consume no slots.
        self.ai('oversized-new', changes={'metadata': 'あ' * 90000,
                                        'created_at': '2099-01-01T00:00:00Z'})
        self.ai('malformed-new', raw='{')
        reply = self.observe()['sre']
        for key, prefix in (('queries', 'q'), ('ai', 'a')):
            self.assertEqual(reply[key]['omitted'], 3)
            self.assertTrue(reply[key]['has_more'])
            self.assertEqual([i['id'] for i in reply[key]['items']],
                             [f'{prefix}{i:02}' for i in range(19, 2, -1)])

    def test_input_rejects_exact_types_duplicates_nonfinite_depth_unicode_and_nonlocal(self):
        value = self.context.public_metadata()
        raw = json.dumps(value)
        invalid = [raw[:-1] + ',"schema_id":"duplicate"}', raw[:-1] + ',"x":NaN}',
            raw[:-1] + ',"x":' + '[' * 40 + '0' + ']' * 40 + '}',
            json.dumps(dict(value, schema_id='\ud800')), '{', '[]',
            json.dumps(dict(value, binding_revision=True)), json.dumps(dict(value, grant_revision=False)),
            json.dumps(dict(value, warehouse_id='XS')), json.dumps(dict(value, warehouse_id='S')),
            json.dumps({k: v for k, v in value.items() if k != 'grant_revision'})]
        for item in invalid:
            with self.subTest(raw=item[:50]):
                self.error('INVALID_INPUT', lambda: observations.parse_context(item, 'data_engineer'))
        self.error('INVALID_INPUT', lambda: observations.parse_context(raw, 'admin'))
        self.assertEqual(observations.parse_context(raw, 'data_engineer'), self.context)

    def test_missing_mismatched_or_incomplete_store_has_fixed_failure(self):
        path = self.root / 'jobs.sqlite3'
        for change in ('version', 'source'):
            with self.subTest(change=change):
                with store.connect() as db:
                    if change == 'version': db.execute('PRAGMA user_version=0')
                    else: db.execute('ALTER TABLE ai_explanations RENAME TO absent_ai')
                error = self.error('AUDIT_STORE_UNAVAILABLE', self.observe)
                self.assertEqual(error.status, 503)
                self.assertNotIn('sqlite', str(error))
                with sqlite3.connect(path) as db:
                    if change == 'version': db.execute(f'PRAGMA user_version={store.VERSION}')
                    else: db.execute('ALTER TABLE absent_ai RENAME TO ai_explanations')
        with tempfile.TemporaryDirectory() as missing, patch.dict(os.environ, {'SHIROKUMA_STATE_DIR': missing}):
            self.error('AUDIT_STORE_UNAVAILABLE', self.observe)
            self.assertFalse((Path(missing) / 'jobs.sqlite3').exists())

    def test_both_real_http_families_closed_payload_and_strict_framing(self):
        handlers = [wb.WorkbenchHandler]
        class Capture:
            def __init__(self, address, handler): handlers.append(handler)
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def handle_request(self): pass
        with ExitStack() as stack:
            stack.enter_context(patch('warehouse_resident._profile_active', return_value=True))
            stack.enter_context(patch('logical_creations.enabled', return_value=False))
            stack.enter_context(patch('http.server.ThreadingHTTPServer', Capture))
            stack.enter_context(patch('database_creation_bridge.configure'))
            stack.enter_context(patch.object(wr, 'warehouse', return_value='LOCAL'))
            wr.serve_endpoint(once=True)
        self.assertEqual(len(handlers), 2)
        class Connection:
            def __init__(self, raw): self.incoming, self.outgoing = io.BytesIO(raw), io.BytesIO()
            def makefile(self, *args): return self.incoming
            def sendall(self, raw): self.outgoing.write(raw)
        self.record()
        self.ai()
        query = '?' + urlencode({'context': json.dumps(self.context.public_metadata())})
        cases = [('GET', query, '', '', 200), ('GET', query, 'Content-Length: 0\r\n', '', 200),
            ('GET', query, 'Content-Length: 1\r\n', 'x', 400),
            ('GET', query, 'Content-Length: nope\r\n', '', 400),
            ('GET', query, 'Content-Length: 0\r\nContent-Length: 0\r\n', '', 400),
            ('GET', query, 'Transfer-Encoding: chunked\r\n', '0\r\n\r\n', 400),
            ('GET', query, 'Transfer-Encoding: \r\n', '', 400),
            ('GET', query + '&context=%7B%7D', '', '', 400),
            ('GET', query + '&source=query', '', '', 400), ('GET', '', '', '', 400),
            ('GET', query.replace('raw', '%FF'), '', '', 400),
            ('GET', query + '#fragment', '', '', 400),
            ('POST', query, 'Content-Length: 0\r\n', '', 404)]
        before = self.snapshot()
        for handler in handlers:
            for method, suffix, extra, body, status in cases:
                with self.subTest(surface=handler.__name__, method=method, extra=extra, suffix=suffix[-20:]):
                    raw = (method + ' /api/v1/bears/context-observations' + suffix + ' HTTP/1.1\r\n'
                        'Host: 127.0.0.1\r\nConnection: close\r\nX-Shirokuma-Role: data_engineer\r\n'
                        + extra + '\r\n' + body).encode()
                    conn = Connection(raw)
                    with patch.object(runtime_profile, 'active', return_value=True), \
                            patch.object(auth, 'authenticate_principal', return_value=self.principal), \
                            patch.object(handler, 'log_message'):
                        handler(conn, ('127.0.0.1', 1), SimpleNamespace())
                    response = HTTPResponse(Connection(conn.outgoing.getvalue()))
                    response.begin()
                    reply = json.loads(response.read())
                    self.assertEqual(response.status, status, reply)
                    if status == 200:
                        self.closed(reply)
                        self.assertEqual(reply['sre']['queries']['items'][0]['state'], 'FINISHED')
                    else:
                        self.assertNotIn('sre', reply)
        self.assertEqual(before, self.snapshot())


class LogicalObservationTests(unittest.TestCase):
    def test_current_identity_and_new_snapshot_identity_revoke(self):
        import test_logical_registry as logical_fixtures
        import logical_registry as logical
        fixture = logical_fixtures.LogicalRegistryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.setup_ready()
        fixture.db.execute("UPDATE warehouse_config_generations SET database_revisions='[]',grant_revisions='[]',configuration_refs='[]'")
        fixture.grant()
        fixture.activate(fixture.candidate())
        fixture.db.execute("INSERT INTO schemas VALUES ('marts','db','marts','Marts','marts','test',1)")
        fixture.db.commit()
        with tempfile.TemporaryDirectory() as root:
            with sqlite3.connect(Path(root) / 'jobs.sqlite3') as target:
                fixture.db.backup(target)
            with patch.dict(os.environ, {'SHIROKUMA_STATE_DIR': root}, clear=True), \
                    patch.object(auth, '_principal_accounts', return_value={
                        'alice': dict(role='admin', platform_admin=False)}), \
                    patch.object(logical, '_credentials', side_effect=AssertionError('credentials')):
                principal = auth.AuthenticatedPrincipal('alice', 'admin', False)
                context = observations.ResolvedQueryContext('db', 'marts', 'LOCAL', 'db', 'marts',
                                                           'bi_viewer', 1, 'foundation', 1)
                self.assertTrue(observations.observe(principal, context)['policy']['context_authorized'])
                original = audit.snapshot
                calls = 0
                @contextmanager
                def revoke():
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        with sqlite3.connect(Path(root) / 'jobs.sqlite3') as db:
                            db.execute("UPDATE logical_backend_identities SET state='REVOKED'")
                    with original() as db:
                        yield db
                with patch.object(audit, 'snapshot', revoke), self.assertRaises(QueryError):
                    observations.observe(principal, context)
                self.assertEqual(calls, 2)
                with self.assertRaises(QueryError):
                    observations.observe(principal, context)


if __name__ == '__main__':
    unittest.main()
