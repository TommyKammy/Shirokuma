"""AI audit summaries validate before paging, without lifecycle or store writes."""
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from email.message import Message
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

# Even imports must never see the workstation's default state directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
_import_state = tempfile.TemporaryDirectory()
with patch.dict(os.environ, SHIROKUMA_STATE_DIR=_import_state.name):
    import audit_service as audit
    import audit_ai
    import audit_routes
    import control_store
    import test_audit_service as fixtures


class AuditAI(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.AuditTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def add(self, ident='ai', *, actor='alice', role='data_engineer', schema='raw',
            database=None, context=None, record=None, **changes):
        c = context if context is not None else self.f.context(database, role, schema)
        if isinstance(c, dict):
            c = c | dict(warehouse_id='LOCAL')
        r = dict(id=ident, actor=actor, context=c, kind='sql_explanation', state='SUCCEEDED',
                 created_at=fixtures.NOW, updated_at=fixtures.NOW,
                 input={'sql': fixtures.SECRET}, model=fixtures.SECRET, text=fixtures.SECRET,
                 output=fixtures.SECRET, metadata=fixtures.SECRET, error_message=fixtures.SECRET)
        r.update(changes)
        self.f.insert('ai_explanations', id=ident, actor=actor, idempotency_key=ident,
                      payload=fixtures.SECRET, pins=fixtures.SECRET, context=c,
                      record=r if record is None else record)
        return r

    def listing(self, **kw):
        return self.f.listing(**(dict(source='ai') | kw))

    def update(self, ident, column, value, *, blob_text=False):
        with control_store.connect() as db:
            db.execute('UPDATE ai_explanations SET ' + column +
                       ('=CAST(? AS TEXT)' if blob_text else '=?') + ' WHERE id=?',
                       (json.dumps(value) if isinstance(value, dict) else value, ident))

    def test_all_kinds_states_exact_wire_and_no_private_search_or_mutation(self):
        kinds = ('sql_explanation', 'sql_plan_explanation', 'saved_plan_explanation',
                 'sql_proposal', 'catalog_proposal')
        states = ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELED', 'UNKNOWN')
        for k in kinds:
            for s in states:
                self.add(k + '-' + s, kind=k, state=s)
        before = (self.f.f.root / 'state/jobs.sqlite3').read_bytes()
        # Runtime dependencies and store mutation must not be reached by reads.
        with ExitStack() as stack:
            for module in ('db_ai', 'db_ai_plan', 'db_ai_reexplain', 'db_ai_sql_proposal', 'db_ai_catalog'):
                import importlib
                m = importlib.import_module(module)
                for name in ('get', 'history', 'recover', 'propose', 'explain', 'submit',
                             'cancel', '_recover', '_run', '_capture', '_refresh', '_complete', '_owner'):
                    if hasattr(m, name):
                        stack.enter_context(patch.object(m, name, side_effect=AssertionError('AI lifecycle')))
            stack.enter_context(patch.object(control_store, 'connect', side_effect=AssertionError('store write')))
            stack.enter_context(patch('socket.create_connection', side_effect=AssertionError('network')))
            stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('runtime')))
            result = self.listing(limit=100)
            self.assertEqual(len(result['items']), 30)
            for event in result['items']:
                self.assertEqual(set(event), set(audit.FIELDS) | {'id', 'effective_role', 'links'})
                self.assertEqual(event['action'], event['source_id'].rsplit('-', 1)[0])
                self.assertEqual(event['links'], [])
                self.assertIsNone(event['effective_role'])
                for key in ('revision', 'committed', 'has_cleanup_error', 'finished_at'):
                    self.assertIsNone(event[key])
                self.assertEqual(self.listing(event_id=event['id'])['event'], event)
            self.assertEqual(self.listing(search=fixtures.SECRET)['items'], [])
            for s in states:
                self.assertEqual(len(self.listing(state=s)['items']), 5)
        self.assertNotIn(fixtures.SECRET, json.dumps(result))
        self.assertEqual(before, (self.f.f.root / 'state/jobs.sqlite3').read_bytes())

    def test_scope_before_record_decoding_and_platform_exclusion(self):
        self.add('own')
        self.add('other', actor='other')
        self.add('viewer', role='bi_viewer', schema='marts')
        self.add('private', schema='private')
        self.add('foreign', database=self.f.finance)
        original = audit_ai.project
        seen = []
        def project(ident, *args):
            seen.append(ident.decode())
            return original(ident, *args)
        with patch.object(audit_ai, 'project', side_effect=project):
            self.assertEqual([r['source_id'] for r in self.listing()['items']], ['own'])
        self.assertEqual(seen, ['own'])
        seen.clear()
        with patch.object(audit_ai, 'project', side_effect=project):
            self.assertEqual([r['source_id'] for r in self.listing(role='bi_viewer')['items']], ['viewer'])
        self.assertEqual(seen, ['viewer'])
        rows = self.f.listing(
            self.f.admin, 'admin', scope='database', source='ai')['items']
        self.assertEqual({r['source_id'] for r in rows}, {'own', 'other', 'viewer', 'private'})
        with patch.object(audit_ai, 'project', side_effect=AssertionError('platform AI')):
            self.assertEqual(self.f.listing(self.f.platform, 'admin', scope='platform',
                                           database_id=None, source='ai')['items'], [])
        detail = self.listing()['items'][0]['id']
        seen.clear()
        with patch.object(audit_ai, 'project', side_effect=project):
            self.listing(event_id=detail)
        self.assertEqual(seen, ['own'])

    def test_full9_internal_consistency_types_and_historical_revisions(self):
        c = self.f.context() | dict(warehouse_id='LOCAL', binding_revision=999, grant_revision=None,
                                   generation_id='historical-generation')
        r = self.add(context=c)
        self.assertEqual(len(self.listing()['items']), 1)
        bad = [c | {'extra': 'x'}, {k:v for k,v in c.items() if k != 'sql_schema'}]
        for key in c:
            bad.append(c | {key: True})
            bad.append(c | {key: ''})
        bad.extend(c | {key: value} for key in ('binding_revision', 'grant_revision')
                   for value in (0, -1, 1.0, '1'))
        bad += [c | {'grant_revision': 1}, c | {'binding_revision': 1},
                c | {'warehouse_id': 'other'}, c | {'selected_role': 'unknown'}]
        for value in bad:
            with self.subTest(value=value):
                self.update('ai', 'record', r | {'context': value})
                self.assertEqual(self.listing()['items'], [])
        self.update('ai', 'record', r)
        self.assertEqual(len(self.listing()['items']), 1)
        # Same invalid values on both copies must still fail type validation.
        for key, value in (('binding_revision', True), ('grant_revision', False), ('schema_id', '\n')):
            invalid = c | {key:value}
            self.update('ai', 'context', invalid)
            self.update('ai', 'record', r | {'context': invalid})
            self.assertEqual(self.listing()['items'], [])

    def test_malformed_records_silently_absent_before_any_pagination(self):
        r = self.add('0-valid')
        raw = json.dumps(r)
        malformed = [raw[:-1] + ',"id":"0-valid"}', raw[:-1] + ',"x":NaN}',
                     raw[:-1] + ',"x":Infinity}', raw[:-1] + ',"x":1e999}',
                     raw[:-1] + ',"x":"\\ud800"}',
                     raw[:-1] + ',"x":' + '['*33 + '0' + ']'*33 + '}',
                     b'\xff', '{', '[]', raw + ' '*262144]
        for key, values in {'id':['wrong', '', '\n', 'x'*257], 'actor':['other'],
                            'kind':['unknown', [], None], 'state':['FINISHED', {}, None],
                            'created_at':['2026-02-30T10:00:00Z', 'now', None, '\n',
                                          '2026-10-29T24:00:00Z', '2026-10-29T10:00:00+25:00',
                                          '0001-01-01T00:00:00+23:59'],
                            'updated_at':['invalid', None], 'finished_at':['private']}.items():
            malformed.extend([json.dumps(r | {'id': 'bad-' + str(len(malformed) + i)} | {key:v})
                              for i, v in enumerate(values)])
        for i, value in enumerate(malformed):
            ident = 'bad-' + str(i)
            self.add(ident)
            if isinstance(value, str):
                value = value.replace('"id": "0-valid"', '"id": ' + json.dumps(ident))
            self.update(ident, 'record', value, blob_text=isinstance(value, bytes))
        # More than C2A's twenty candidates must not starve a valid page.
        self.assertGreater(len(malformed), 20)
        self.assertEqual([e['source_id'] for e in self.listing(limit=1)['items']], ['0-valid'])
        self.assertEqual(len(self.listing(search='valid', state='SUCCEEDED', **{'from':fixtures.NOW})['items']), 1)
        self.assertEqual(self.listing(search=fixtures.SECRET)['items'], [])

    def test_byte_guards_and_invalid_context_before_record(self):
        r = self.add()
        with patch.object(audit_ai, 'project', side_effect=AssertionError('record decoded')):
            for value in (' '*8193, b'\xff', '{', json.dumps(r['context'])[:-1] + ',"sql_schema":"x"}',
                          json.dumps(r['context'])[:-1] + ',"extra":NaN}'):
                self.update('ai', 'context', value, blob_text=isinstance(value, bytes))
                self.assertEqual(self.listing()['items'], [])
        self.update('ai', 'context', r['context'])
        self.update('ai', 'record', 'é'*131073)
        with patch.object(audit_ai, 'project', side_effect=AssertionError('overbyte record')):
            self.assertEqual(self.listing()['items'], [])
        self.update('ai', 'context', 'é'*4097)
        with patch.object(audit_ai, 'safe_context', side_effect=AssertionError('overbyte context')):
            self.assertEqual(self.listing()['items'], [])

    def test_ties_ceilings_replay_current_state_and_stale_cursor(self):
        a = self.add('a')
        self.add('b')
        self.add('c')
        first = self.listing(limit=1)
        self.assertEqual(first['items'][0]['source_id'], 'c')
        self.add('z')
        second = self.listing(limit=1, cursor=first['next_cursor'])
        third = self.listing(limit=1, cursor=second['next_cursor'])
        self.assertEqual([second['items'][0]['source_id'], third['items'][0]['source_id']], ['b','a'])
        self.assertIsNone(third['next_cursor'])
        self.update('a', 'record', a | {'state':'UNKNOWN'})
        self.assertEqual(self.listing(limit=1, cursor=second['next_cursor'])['items'][0]['state'], 'UNKNOWN')
        self.assertEqual(self.listing(limit=1, cursor=first['page_cursor'])['items'][0]['source_id'], 'c')
        self.f.error(lambda: self.listing(limit=1, cursor=first['page_cursor']+'x'), 'STALE_CURSOR')
        with control_store.connect() as db:
            db.execute("UPDATE effective_grants SET epoch=epoch+1 WHERE principal_id='alice'")
        self.f.error(lambda: self.listing(limit=1, cursor=first['page_cursor']), 'STALE_CURSOR')

    def test_download_and_detail_share_projection(self):
        self.add()
        event = self.listing()['items'][0]
        for suffix in ('', '/download'):
            path = '/api/v1/audit/events/' + event['id'] + suffix + '?database_id=' + self.f.sales
            url = urlsplit(path)
            handler = SimpleNamespace(path=path, headers=Message(), respond=Mock(), send_response=Mock(),
                                      send_header=Mock(), end_headers=Mock(), wfile=io.BytesIO())
            handler.headers['X-Shirokuma-Role'] = 'data_engineer'
            audit_routes.dispatch(handler, 'GET', url.path, parse_qs(url.query), self.f.alice, 'data_engineer')
            reply = json.loads(handler.wfile.getvalue()) if suffix else handler.respond.call_args.args[0]
            self.assertEqual(reply['event'], event)
            self.assertNotIn(fixtures.SECRET, json.dumps(reply))

    def test_final_fence_account_grant_binding_on_list_and_detail(self):
        self.add()
        ident = self.listing()['items'][0]['id']
        original = audit._fence
        for case in ('account', 'grant', 'binding'):
            for detail in (None, ident):
                def revoke(*args):
                    if case == 'account':
                        self.f.f.users['alice']['disabled'] = True
                        self.f.f.save()
                    else:
                        with control_store.connect() as db:
                            db.execute("UPDATE effective_grants SET epoch=epoch+1 WHERE principal_id='alice'" if case == 'grant'
                                       else 'UPDATE catalog_bindings SET revision=revision+1')
                    return original(*args)
                with self.subTest(case=case, detail=detail), patch.object(audit, '_fence', side_effect=revoke):
                    self.f.error(lambda: self.listing(event_id=detail))
                self.f.f.users['alice'].pop('disabled', None)
                self.f.f.save()


if __name__ == '__main__':
    unittest.main()
