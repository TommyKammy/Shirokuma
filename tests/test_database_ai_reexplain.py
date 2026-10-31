"""Saved-plan inference with isolated SQLite and no source/runtime execution."""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
from email.message import Message
from http.client import HTTPResponse
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools/local_experiment'))
with tempfile.TemporaryDirectory() as _bootstrap:
    with patch.dict(os.environ, {'SHIROKUMA_STATE_DIR': _bootstrap}):
        import test_database_ai as a1
        import test_database_ai_plan_views as a3
        import db_ai as ai
        import db_ai_plan as plan
        import db_ai_reexplain as saved
        import control_store as store
        import ai_routes
        import query_service as qs
        from query_context import QueryError


class SavedPlanTests(unittest.TestCase):
    def setUp(self):
        # Genuine logical identities, grants and schema ACLs; never run A2.
        self.fixture = a3.ViewPlanTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f = self.fixture.f
        self.principal, self.context = self.f.principal, self.f.context
        self.owners = []
        self.addCleanup(lambda: [o.close() for o in self.owners])
        self.tripwires = []
        for target in ('db_ai_plan.get', 'db_ai_plan.history', 'db_ai_plan._recover',
                'db_ai_plan.cancel', 'db_ai_plan._run', 'db_ai_plan.check_metadata',
                'db_ai_plan_views.capture', 'database_inventory.catalog_api',
                'database_inventory.resolve_object', 'database_inventory.object_detail',
                'query_service.cancel', 'query_service.submit_explain', 'query_service._transport',
                'logical_registry.identity_credentials', 'trino_auth.prepare_backend_v2'):
            self.tripwires.append(self.patch(target, side_effect=AssertionError(target)))
        self.source_id = self.source()

    def patch(self, target, **kwargs):
        p = patch(target, **kwargs)
        result = p.start()
        self.addCleanup(p.stop)
        return result

    def source(self, *, state='SUCCEEDED', view=False, sql=None):
        ident, query = str(uuid.uuid4()), str(uuid.uuid4())
        sql = sql or ("SELECT * FROM raw.saved_view WHERE id = 73123 -- PRIVATE literal" if view else
            "WITH x AS (SELECT customer_id, SUM(amount) n FROM raw.orders WHERE amount > 73123 GROUP BY customer_id) SELECT * FROM x")
        now = ai._now()
        columns = [dict(name='payload', type=ai._type(dict(type='struct', fields=[
            dict(name='items', type=dict(type='list', element='long'))])))]
        def pin(schema, table, kind, direct):
            schema_id = self.f.schemas[self.context.database_id][schema].id
            return dict(schema=schema, table=table, kind=kind, schema_id=schema_id, schema_revision=1,
                ref=dict(database_id=self.context.database_id, schema_id=schema_id, object_id=str(uuid.uuid4()),
                    incarnation=1, binding_revision=1, generation_id=self.context.generation_id),
                columns=deepcopy(columns), **({'direct': direct} if view else {}))
        pins = [pin('raw', 'orders', 'TABLE', True)]
        if view:
            pins = [pin('raw', 'saved_view', 'VIEW', True), pin('sandbox', 'nested_view', 'VIEW', False),
                pin('marts', 'orders', 'TABLE', False)]
            for p, child in zip(pins, [('sandbox', 'nested_view'), ('marts', 'orders')]):
                p.update(view_uuid=str(uuid.uuid4()), view_version=3, view_schema_id=2, security='INVOKER',
                    defaults_digest='a'*64, definition_digest='b'*64, canonical_definition_digest='c'*64,
                    head_digest='d'*64, dependencies=[list(child)])
            pins[-1]['snapshots'] = [dict(snapshot_id=731234567891, schema_fingerprint='e'*64)]
        def public(p):
            return {k: deepcopy(p[k]) for k in ('schema', 'table', 'kind', 'columns')}
        metadata = dict(captured_at=now, relations=[public(pins[0])])
        if view:
            metadata['view_dependencies'] = [public(p) for p in pins[1:]]
        record = dict(id=ident, kind=plan.KIND, state=state, actor=self.principal.principal_id,
            context=self.context.public_metadata(), input=dict(sql=sql, language='ja'),
            metadata=metadata, text='EXCLUDED SOURCE MODEL TEXT', model={'name': 'old-private'},
            created_at=now, updated_at=now, sql_status='NOT_EXECUTED', cancel_requested=False,
            inference_state=state, plan_status='COLLECTED', error_code=None,
            plan=dict(format='TRINO_DISTRIBUTED_JSON', query_id=query, query_state='FINISHED',
                captured_at=now, operators=plan.project(json.dumps({'71': dict(name='Aggregate',
                    descriptor={'type': 'FINAL'}, children=[dict(name='TableScan')])})),
                backend_query_id='PRIVATE_BACKEND', execution_closure=None, error_code=None))
        with store.connect() as db:
            db.execute('INSERT INTO query_records VALUES (?,?,?,?,?,?,?,?)', (query, self.principal.principal_id,
                self.context.database_id, self.context.schema_id, self.context.warehouse_id,
                self.context.generation_id, 'FINISHED', now))
            db.execute('INSERT INTO query_execution_contexts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (query, self.context.selected_role, 'read-only', self.context.sql_catalog, self.context.sql_schema,
                 self.context.binding_revision, self.context.grant_revision, 'PRIVATE_USER', 'PRIVATE_CREDENTIAL',
                 'PRIVATE_BACKEND', ident, 'ai-plan-'+ident,
                 'EXPLAIN (TYPE DISTRIBUTED, FORMAT JSON) ' + qs._backend_statement(sql)))
            db.execute('INSERT INTO ai_explanations VALUES (?,?,?,?,?,?,?)', (ident, self.principal.principal_id,
                'source-'+ident, '{}', ai._json(self.context.public_metadata()),
                ai._json(dict(relations=pins, control={'PRIVATE': True})), ai._json(record)))
        return ident

    def row(self, ident):
        return json.loads(ai._row(ident)['record'])

    def mutate(self, ident=None, change=None, private=None):
        ident = ident or self.source_id
        with store.connect() as db:
            row = db.execute('SELECT * FROM ai_explanations WHERE id=?', (ident,)).fetchone()
            record, pins = json.loads(row['record']), json.loads(row['pins'])
            if change:
                change(record)
            if private:
                private(pins)
            db.execute('UPDATE ai_explanations SET record=?,pins=? WHERE id=?',
                (ai._json(record), ai._json(pins), ident))

    def count(self):
        with store.connect() as db:
            return db.execute("SELECT count(*) FROM ai_explanations WHERE json_extract(record,'$.kind')=?",
                (saved.KIND,)).fetchone()[0]

    def accept(self, source=None, key='saved-key', **kw):
        with patch.object(saved.threading, 'Thread') as worker:
            result = saved.submit(kw.pop('principal', self.principal), kw.pop('context', self.context),
                key=key, source_explanation_id=source or self.source_id, language=kw.pop('language', 'en'), **kw)
            if worker.called:
                self.args = worker.call_args.kwargs['args']
                self.owners.append(self.args[-1])
        return result

    def finish(self):
        saved._run(*self.args)
        return saved.get(self.principal, self.context, self.args[2])

    def error(self, code, fn, status=None):
        with self.assertRaises(QueryError) as raised:
            fn()
        self.assertEqual(raised.exception.code, code)
        if status:
            self.assertEqual(raised.exception.status, status)

    def test_all_terminal_source_states_copy_and_privacy(self):
        for state in ('SUCCEEDED', 'FAILED', 'CANCELED', 'UNKNOWN'):
            source = self.source(state=state, view=True)
            accepted = self.accept(source, key=state)
            self.assertEqual((accepted['state'], accepted['inference_state'], accepted['sql_status']),
                ('QUEUED', 'NOT_STARTED', 'NOT_EXECUTED'))
            snapshot = accepted['source_snapshot']
            self.assertEqual(set(snapshot), {'explanation_id','query_id','state','query_state','format',
                'captured_at','sql','operators'})
            self.assertEqual(snapshot['state'], state)
            self.assertEqual(accepted['input'], dict(source_explanation_id=source, language='en'))
            def complete(config, sql, relations, language, **extras):
                wire = ai._json(dict(sql=sql, relations=relations, **extras))
                for secret in ('73123', 'PRIVATE', 'EXCLUDED', 'snapshot_id', 'digest', 'view_uuid'):
                    self.assertNotIn(secret, wire)
                self.assertIn('view_dependencies', extras)
                # Prove provider executes outside a writer transaction.
                with store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                return 'copied plan explanation'
            self.f.complete.side_effect = complete
            done = self.finish()
            self.assertEqual(done['state'], 'SUCCEEDED')
            self.assertEqual(self.row(source)['state'], state)
            for absent in ('plan', 'plan_status', 'backend_query_id', 'execution_closure', 'control'):
                self.assertNotIn(absent, done)
            self.assertNotIn('EXCLUDED', ai._json(done))
        self.assertTrue(all(not m.called for m in self.tripwires))

    def test_table_old_pins_arbitrary_sql_and_source_independence(self):
        accepted = self.accept()
        original = deepcopy(accepted['source_snapshot'])
        with store.connect() as db:
            db.execute('DELETE FROM ai_explanations WHERE id=?', (self.source_id,))
            db.execute('DELETE FROM object_bindings')
        done = self.finish()
        self.assertEqual(done['state'], 'SUCCEEDED')
        self.assertEqual(done['source_snapshot'], original)
        self.assertEqual(saved.history(self.principal, self.context), [done])
        self.assertEqual(self.accept()['id'], done['id'])
        self.assertEqual(self.f.complete.call_count, 1)

    def test_rejection_before_insert_or_provider(self):
        bad = [lambda r: r.update(state='RUNNING'), lambda r: r.update(state='QUEUED'),
            lambda r: r.update(plan_status='NOT_COLLECTED'),
            lambda r: r['plan'].update(operators=None),
            lambda r: r['plan'].update(captured_at='yesterday'),
            lambda r: r['plan'].update(captured_at='2026-01-01T00:00:00'),
            lambda r: r['plan'].update(query_state='UNKNOWN'),
            lambda r: r['plan']['operators'].update(projection_version=True),
            lambda r: r['plan']['operators']['fragments'][0]['root'].update(raw='PRIVATE'),
            lambda r: r['plan']['operators']['fragments'][0]['root']['attributes'].update(phase='PRIVATE'),
            lambda r: r['metadata']['relations'][0].update(table='other'),
            lambda r: r['metadata'].update(raw='PRIVATE')]
        baseline = self.row(self.source_id)
        for change in bad:
            with self.subTest(change=change):
                self.mutate(change=lambda r: r.update(deepcopy(baseline)))
                self.mutate(change=change)
                self.error('AI_SAVED_PLAN_UNAVAILABLE', self.accept)
                self.assertEqual(self.count(), 0)
        self.assertFalse(self.f.complete.called)

    def test_projection_size_depth_and_private_public_mismatch(self):
        for change in (lambda p: p['relations'][0].update(columns=[]),
                lambda p: p['relations'][0]['ref'].update(database_id='wrong'),
                lambda p: p['relations'].append(deepcopy(p['relations'][0]))):
            source = self.source()
            self.mutate(source, private=change)
            self.error('AI_SAVED_PLAN_UNAVAILABLE', lambda: self.accept(source))
        source = self.source(view=True)
        self.mutate(source, private=lambda p: p['relations'][-1]['snapshots'][0].update(snapshot_id=True))
        self.error('AI_SAVED_PLAN_UNAVAILABLE', lambda: self.accept(source))
        source = self.source(view=True)
        self.mutate(source, private=lambda p: p['relations'][1].update(dependencies=[['raw', 'saved_view']]))
        self.error('AI_SAVED_PLAN_UNAVAILABLE', lambda: self.accept(source))
        for value in ([], [dict(id='f0', root=dict(operator='Values', children=[], remote_sources=[], attributes={}))]*65):
            source = self.source()
            self.mutate(source, change=lambda r: r['plan']['operators'].update(fragments=value))
            self.error('AI_SAVED_PLAN_UNAVAILABLE', lambda: self.accept(source))
        self.assertEqual(self.count(), 0)

    def test_wrong_child_link_actor_context_kind_and_chain(self):
        for field, value in (('request_id', 'wrong'), ('worksheet_id', 'wrong'), ('mode', 'internal'),
                ('sql', 'SELECT * FROM orders'), ('grant_revision', 2), ('backend_username', 'ignored')):
            source = self.source()
            query = self.row(source)['plan']['query_id']
            with store.connect() as db:
                db.execute('UPDATE query_execution_contexts SET '+field+'=? WHERE query_id=?', (value, query))
            if field == 'backend_username':
                continue
            self.error('AI_EXPLANATION_NOT_FOUND', lambda: self.accept(source))
        self.error('AI_EXPLANATION_NOT_FOUND', lambda: self.accept(principal=self.f.bob))
        self.error('AI_EXPLANATION_NOT_FOUND', lambda: self.accept(principal=self.f.owner))
        for field, value in self.context.public_metadata().items():
            changed = replace(self.context, **{field: value+1 if type(value) is int else 'different'})
            self.error('AI_EXPLANATION_NOT_FOUND', lambda: self.accept(context=changed))
        accepted = self.accept()
        self.error('AI_EXPLANATION_NOT_FOUND', lambda: self.accept(accepted['id'], key='chain'))
        self.mutate(change=lambda r: r.update(kind='sql_explanation'))
        self.error('AI_EXPLANATION_NOT_FOUND', lambda: self.accept(key='wrong-kind'))

    def test_actual_child_must_be_finished(self):
        query = self.row(self.source_id)['plan']['query_id']
        with store.connect() as db:
            db.execute("UPDATE query_records SET state='UNKNOWN' WHERE id=?", (query,))
        self.error('AI_SAVED_PLAN_UNAVAILABLE', self.accept)
        self.assertEqual(self.count(), 0)

    def test_source_and_authority_errors_precede_invalid_local_config(self):
        self.f.config['api_url'] = 'https://invalid.example/v1'
        self.error('AI_EXPLANATION_NOT_FOUND', lambda: self.accept(principal=self.f.bob), 404)
        self.error('AI_EXPLANATION_NOT_FOUND', lambda: self.accept(
            context=replace(self.context, grant_revision=self.context.grant_revision + 1)), 404)
        self.mutate(change=lambda r: r.update(kind='sql_explanation'))
        self.error('AI_EXPLANATION_NOT_FOUND', self.accept, 404)
        for change in (lambda r: r.update(state='RUNNING'),
                lambda r: r.update(plan_status='NOT_COLLECTED'),
                lambda r: r['plan'].update(operators=None)):
            source = self.source()
            self.mutate(source, change=change)
            self.error('AI_SAVED_PLAN_UNAVAILABLE', lambda: self.accept(source), 409)
        source = self.source(view=True)
        with store.connect() as db:
            db.execute("UPDATE schemas SET purpose='restricted' WHERE id=?",
                (self.f.schemas[self.context.database_id]['marts'].id,))
        self.error('SQL_PERMISSION_DENIED', lambda: self.accept(source), 403)
        self.assertEqual(self.count(), 0)
        self.assertFalse(self.f.complete.called)

    def test_valid_source_invalid_local_config_does_not_insert(self):
        self.f.config['api_url'] = 'https://invalid.example/v1'
        self.error('AI_PROVIDER_UNAVAILABLE', self.accept, 503)
        self.assertEqual(self.count(), 0)
        self.assertFalse(self.f.complete.called)

    def frozen_graph(self, layers):
        source = self.source(view=True)
        private = json.loads(ai._row(source)['pins'])['relations']
        view, table = private[0], private[-1]
        pins = []
        for level, names in enumerate(layers):
            children = layers[level + 1] if level + 1 < len(layers) else ['orders']
            for name in names:
                p = deepcopy(view)
                p.update(table=name, direct=level == 0,
                    dependencies=[['raw', child] for child in children])
                pins.append(p)
        table.update(schema='raw', schema_id=self.context.schema_id, direct=False)
        table['ref']['schema_id'] = self.context.schema_id
        pins.append(table)
        return pins

    def validate_graph(self, pins):
        public = lambda p: {k: deepcopy(p[k]) for k in ('schema', 'table', 'kind', 'columns')}
        metadata = dict(captured_at=ai._now(), relations=[public(p) for p in pins if p['direct']],
            view_dependencies=[public(p) for p in pins if not p['direct']])
        saved._metadata(self.context, metadata, pins,
            [(p['schema'], p['table']) for p in pins if p['direct']])

    def test_shared_dag_validation_has_bounded_work_at_sixteen_views(self):
        pins = self.frozen_graph([['root']] + [[f'a{i}', f'b{i}'] for i in range(15)])
        # Count executed validator lines: deterministic work budget, independent of wall time.
        # This closed 32-node DAG represents over 32,000 distinct root-to-table paths.
        work = 0
        def budget(frame, event, arg):
            nonlocal work
            if event == 'line' and frame.f_code.co_filename == saved.__file__:
                work += 1
                if work > 20000:
                    raise AssertionError('shared DAG exceeded validation work budget')
            return budget
        previous = sys.gettrace()
        try:
            sys.settrace(budget)
            self.validate_graph(pins)
        finally:
            sys.settrace(previous)
        self.assertLess(work, 20000)

    def test_cached_shared_view_height_rejects_longer_path(self):
        for length in (14, 15):
            pins = self.frozen_graph([['root']] + [[f'chain{i}'] for i in range(length)] + [['shared']])
            # Validate shared first at depth two, then reach it through the longer chain.
            pins[0]['dependencies'].insert(0, ['raw', 'shared'])
            if length == 14:
                self.validate_graph(pins)
            else:
                self.error('AI_SAVED_PLAN_UNAVAILABLE', lambda: self.validate_graph(pins), 409)

    def test_original_metadata_envelopes_near_64k_and_overflow(self):
        for view in (False, True):
            for size in (65536, 65537):
                with self.subTest(view=view, size=size):
                    source = self.source(view=view)
                    record = self.row(source)
                    pins = json.loads(ai._row(source)['pins'])['relations']
                    metadata = record['metadata']
                    column = metadata['relations'][0]['columns'][0]
                    column['name'] = ''
                    envelope = ({k: metadata[k] for k in ('relations', 'view_dependencies')}
                        if view else metadata['relations'])
                    column['name'] = 'x' * (size - len(ai._json(envelope).encode()))
                    pins[0]['columns'] = deepcopy(metadata['relations'][0]['columns'])
                    self.assertEqual(len(ai._json(envelope).encode()), size)
                    self.assertGreater(len(ai._json(metadata).encode()), 65536)
                    self.mutate(source, change=lambda r: r.update(metadata=metadata),
                        private=lambda p: p.update(relations=pins))
                    if size == 65536:
                        accepted = self.accept(source, key=f'boundary-{view}')
                        self.assertEqual(accepted['metadata'], metadata)
                    else:
                        self.error('AI_SAVED_PLAN_UNAVAILABLE', lambda: self.accept(source), 409)
        self.assertEqual(self.count(), 2)
        self.assertFalse(self.f.complete.called)

    def test_replay_conflicts_crosskind_and_distinct_intent(self):
        first = self.accept()
        self.assertEqual(self.accept()['id'], first['id'])
        self.error('IDEMPOTENCY_CONFLICT', lambda: self.accept(language='ja'))
        self.error('IDEMPOTENCY_CONFLICT', lambda: self.accept(self.source()))
        self.error('IDEMPOTENCY_CONFLICT', lambda: self.accept(key='source-'+self.source_id))
        second = self.accept(key='independent')
        self.assertNotEqual(first['id'], second['id'])
        self.assertNotEqual(self.owners[0].name, self.owners[-1].name)
        self.assertFalse(self.f.complete.called)

    def test_owner_loss_and_provider_uncertainty_never_auto_retry(self):
        for code in ('AI_PROVIDER_TIMEOUT', 'AI_PROVIDER_OUTCOME_UNKNOWN'):
            accepted = self.accept(key=code)
            self.f.complete.side_effect = QueryError(code, 504)
            done = self.finish()
            self.assertEqual((done['state'], done['inference_state']), ('UNKNOWN', 'UNKNOWN'))
            calls = self.f.complete.call_count
            self.assertEqual(self.accept(key=code)['id'], accepted['id'])
            saved.history(self.principal, self.context)
            self.assertEqual(self.f.complete.call_count, calls)
        accepted = self.accept(key='lost')
        self.owners[-1].close()
        done = saved.get(self.principal, self.context, accepted['id'])
        self.assertEqual((done['state'], done['error_code']), ('UNKNOWN', 'AI_OWNER_LOST'))

    def test_cancel_only_self_and_late_provider_output(self):
        accepted = self.accept()
        source = deepcopy(self.row(self.source_id))
        def complete(*args, **kwargs):
            result = saved.cancel(self.principal, self.context, accepted['id'])
            self.assertEqual(result['state'], 'CANCELED')
            return 'LATE TEXT'
        self.f.complete.side_effect = complete
        done = self.finish()
        self.assertEqual((done['state'], done['text']), ('CANCELED', None))
        self.assertEqual(self.row(self.source_id), source)
        queued = self.accept(key='cancel-queued')
        saved.cancel(self.principal, self.context, queued['id'])
        count = self.f.complete.call_count
        self.finish()
        self.assertEqual(self.f.complete.call_count, count)
        self.assertTrue(all(not m.called for m in self.tripwires))

    def revoke_identity(self):
        with store.connect() as db:
            db.execute("UPDATE logical_backend_identities SET state='REVOKED' WHERE principal_id='alice'")

    def test_recursive_rights_before_provider_and_get_cancel(self):
        source = self.source(view=True)
        accepted = self.accept(source)
        with store.connect() as db:
            db.execute("UPDATE schemas SET purpose='restricted' WHERE id=?",
                (self.f.schemas[self.context.database_id]['marts'].id,))
        self.finish_denied(accepted)
        self.assertFalse(self.f.complete.called)
        for fn in (lambda: saved.get(self.principal, self.context, accepted['id']),
                lambda: saved.cancel(self.principal, self.context, accepted['id'])):
            self.error('SQL_PERMISSION_DENIED', fn)
        self.assertEqual(saved.history(self.principal, self.context), [])

    def finish_denied(self, accepted):
        saved._run(*self.args)
        self.assertEqual(self.row(accepted['id'])['state'], 'FAILED')
        self.assertIsNone(self.row(accepted['id'])['text'])

    def test_revocation_after_provider_and_final_writer(self):
        accepted = self.accept()
        def complete(*args, **kwargs):
            self.revoke_identity()
            return 'DENIED TEXT'
        self.f.complete.side_effect = complete
        self.finish_denied(accepted)

    def test_final_writer_rechecks_authority(self):
        accepted = self.accept()
        real = ai._transaction_authority
        calls = []
        def check(db, principal, context, pins):
            calls.append(1)
            if len(calls) == 2:
                db.execute("UPDATE schemas SET purpose='restricted' WHERE id=?", (context.schema_id,))
            return real(db, principal, context, pins)
        with patch.object(ai, '_transaction_authority', side_effect=check):
            self.finish_denied(accepted)
        self.assertEqual(len(calls), 2)

    def test_source_change_after_acceptance_does_not_change_history(self):
        accepted = self.accept(self.source(view=True, state='UNKNOWN'))
        source = accepted['input']['source_explanation_id']
        self.mutate(source, change=lambda r: r.update(state='FAILED', metadata={}, text='CHANGED'))
        self.f.objects.clear()
        done = self.finish()
        self.assertEqual(done['state'], 'SUCCEEDED')
        self.assertEqual(done['source_snapshot'], accepted['source_snapshot'])
        self.assertEqual(saved.history(self.principal, self.context)[0], done)

    def test_same_key_concurrent_acceptance_calls_provider_once(self):
        from concurrent.futures import ThreadPoolExecutor
        barrier = threading.Barrier(2)
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        real_run = saved._run
        def submit():
            barrier.wait(3)
            return saved.submit(self.principal, self.context, key='race',
                source_explanation_id=self.source_id, language='en')
        def complete(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(3))
            return 'one result'
        def run(*args):
            try:
                real_run(*args)
            finally:
                finished.set()
        self.f.complete.side_effect = complete
        with patch.object(saved, '_run', side_effect=run):
            with ThreadPoolExecutor(2) as pool:
                futures = [pool.submit(submit) for _ in range(2)]
                results = [f.result(5) for f in futures]
        self.assertEqual(results[0]['id'], results[1]['id'])
        self.assertTrue(entered.wait(3))
        release.set()
        self.assertTrue(finished.wait(3))
        self.assertEqual(saved.get(self.principal, self.context, results[0]['id'])['state'], 'SUCCEEDED')
        self.assertEqual(self.f.complete.call_count, 1)
        self.assertEqual(self.count(), 1)

    def test_admission_checks_recursive_grant_and_current_identity(self):
        source = self.source(view=True)
        with store.connect() as db:
            db.execute("UPDATE schemas SET purpose='restricted' WHERE id=?",
                (self.f.schemas[self.context.database_id]['sandbox'].id,))
        self.error('SQL_PERMISSION_DENIED', lambda: self.accept(source))
        with store.connect() as db:
            db.execute("UPDATE schemas SET purpose='sandbox' WHERE id=?",
                (self.f.schemas[self.context.database_id]['sandbox'].id,))
        self.revoke_identity()
        self.error('DB_ACCESS_REVOKED', self.accept)
        self.assertEqual(self.count(), 0)
        self.assertFalse(self.f.complete.called)

    def test_admin_identity_is_required_even_for_owned_source(self):
        context = self.f.ctx(role='admin', principal=self.f.owner)
        old_principal, old_context = self.principal, self.context
        self.principal, self.context = self.f.owner, context
        source = self.source(view=True)
        with store.connect() as db:
            db.execute("UPDATE logical_backend_identities SET state='REVOKED' WHERE principal_id='owner'")
        self.error('DB_ACCESS_REVOKED', lambda: self.accept(source))
        self.assertEqual(self.count(), 0)
        self.principal, self.context = old_principal, old_context

    def test_both_http_families_strict_wire_without_socket_bind(self):
        import workbench_server as wb
        import warehouse_resident as wr
        import runtime_profile
        import request_auth as auth
        handlers = [wb.WorkbenchHandler]
        class Capture:
            def __init__(self, address, handler):
                handlers.append(handler)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def handle_request(self):
                pass
        with ExitStack() as stack:
            stack.enter_context(patch('warehouse_resident._profile_active', return_value=True))
            stack.enter_context(patch('logical_creations.enabled', return_value=False))
            stack.enter_context(patch('http.server.ThreadingHTTPServer', Capture))
            stack.enter_context(patch('database_creation_bridge.configure'))
            stack.enter_context(patch.object(wr, 'warehouse', return_value='LOCAL'))
            wr.serve_endpoint(once=True)
        class Connection:
            def __init__(self, data):
                self.incoming, self.outgoing = io.BytesIO(data), io.BytesIO()
            def makefile(self, *args):
                return self.incoming
            def sendall(self, data):
                self.outgoing.write(data)
        base = '/api/v1/bears/saved-plan-explanations'
        body = json.dumps(dict(context=self.context.public_metadata(), source_explanation_id=self.source_id, language='en'))
        cases = [(base, body, 'Idempotency-Key: http-key\r\n', 202),
            (base, body, '', 400),
            (base, body, 'Idempotency-Key: k\r\nIdempotency-Key: k\r\n', 400),
            (base, body, 'Idempotency-Key: k\r\nOrigin: http://evil.invalid\r\n', 403),
            (base, body, 'Idempotency-Key: k\r\nTransfer-Encoding: chunked\r\n', 400),
            (base, body[:-1]+',"sql":"SELECT 1"}', 'Idempotency-Key: k\r\n', 400),
            (base, body[:-1]+',"language":"ja"}', 'Idempotency-Key: k\r\n', 400),
            (base+'?extra=1', body, 'Idempotency-Key: k\r\n', 400),
            (base, body.replace('data_engineer', 'admin'), 'Idempotency-Key: k\r\n', 400)]
        for handler in handlers:
            for path, payload, extra, expected in cases:
                with self.subTest(surface=handler.__name__, path=path, expected=expected):
                    data = ('POST '+path+' HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n'
                        'X-Shirokuma-Role: data_engineer\r\nContent-Type: application/json\r\nContent-Length: '
                        +str(len(payload.encode()))+'\r\n'+extra+'\r\n'+payload).encode()
                    conn = Connection(data)
                    with patch.object(runtime_profile, 'active', return_value=True), \
                            patch.object(auth, 'authenticate_principal', return_value=self.principal), \
                            patch.object(saved, 'submit', return_value={'id':'new'}) as submit, \
                            patch.object(handler, 'log_message'):
                        handler(conn, ('127.0.0.1', 1), SimpleNamespace())
                    response = HTTPResponse(Connection(conn.outgoing.getvalue()))
                    response.begin()
                    reply = json.loads(response.read())
                    self.assertEqual(response.status, expected, reply)
                    self.assertEqual(submit.call_count, int(expected == 202))

    def test_strict_route_and_unchanged_a1_a2_dispatch(self):
        context = self.context.public_metadata()
        class Handler:
            path = '/api/v1/bears/saved-plan-explanations'
            headers = Message()
            def database_body(h):
                return deepcopy(h.body)
            def respond(h, value, status):
                h.response = value, status
        h = Handler()
        h.headers['Content-Type'] = 'application/json'
        h.headers['Idempotency-Key'] = 'route'
        h.body = dict(context=context, source_explanation_id=self.source_id, language='ja')
        with patch.object(saved, 'submit', return_value={'id': 'new'}) as submit:
            ai_routes.dispatch(h, 'POST', h.path, {}, self.principal, self.context.selected_role)
            self.assertEqual(h.response, (dict(context=context, explanation={'id':'new'}), 202))
            self.assertEqual(submit.call_args.kwargs, dict(key='route', source_explanation_id=self.source_id, language='ja'))
            for field in ('sql', 'operators', 'plan'):
                h.body[field] = 'forbidden'
                with self.assertRaises(ValueError):
                    ai_routes.dispatch(h, 'POST', h.path, {}, self.principal, self.context.selected_role)
                del h.body[field]
        for family, service in (('explanations', ai), ('plan-explanations', plan)):
            h.path = '/api/v1/bears/'+family
            h.body = dict(context=context, sql='SELECT 42', language='en')
            with patch.object(service, 'submit', return_value={}) as submit:
                ai_routes.dispatch(h, 'POST', h.path, {}, self.principal, self.context.selected_role)
                self.assertEqual(submit.call_args.kwargs, dict(key='route', sql='SELECT 42', language='en'))
        h.path = '/api/v1/bears/saved-plan-explanations'
        with patch.object(saved, 'history', return_value=[]) as history:
            ai_routes.dispatch(h, 'GET', h.path, {'context':[json.dumps(context)]}, self.principal, self.context.selected_role)
            self.assertEqual(h.response[0], dict(context=context, explanations=[]))
        with self.assertRaises(ValueError):
            ai_routes.dispatch(h, 'GET', h.path, {}, self.principal, self.context.selected_role)
        h.path += '#bad'
        with self.assertRaises(ValueError):
            ai_routes.dispatch(h, 'GET', '/api/v1/bears/saved-plan-explanations', {}, self.principal, self.context.selected_role)


if __name__ == '__main__':
    unittest.main()
