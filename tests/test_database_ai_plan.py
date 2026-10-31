"""Plan acquisition against real isolated SQLite, synthetic catalog and transports."""
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from dataclasses import replace
from email.message import Message
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools/local_experiment'))
# Isolate import-time legacy configuration as well as all handler/store work.
with tempfile.TemporaryDirectory() as _bootstrap:
    with patch.dict(os.environ, {'SHIROKUMA_STATE_DIR': _bootstrap}):
        import test_database_ai as a1
        import db_ai as ai
        import db_ai_plan as plan
        import query_service as qs
        import request_auth as auth
        import control_store as store
        import warehouse_admission as admission
        import ai_routes
from query_context import QueryError


def synthetic_plan():
    return {'17': dict(name='Output', children=[dict(name='Aggregate',
        descriptor={'type': 'FINAL', 'table': 's3://SECRET', 'rows': 987654},
        children=[dict(name='RemoteSource', descriptor={'sourceFragmentIds': '[29]'})])]),
        '29': dict(name='LocalExchange', descriptor={'partitioning': 'HASH'},
            children=[dict(name='Aggregate', descriptor={'type': 'PARTIAL'}, children=[
                dict(name='ScanFilterProject', details='SECRET filter 12345 /private/file',
                    outputs=['SECRET'], estimates=[{'rows': 987654}],
                    descriptor={'table': 's3://SECRET', 'filterPredicate': 'SECRET'})])])}


class ProjectionTests(unittest.TestCase):
    def test_fragment_order_and_links_are_independent_of_map_insertion_order(self):
        fragments = synthetic_plan()
        fragments['2'] = dict(name='Values', details='PRIVATE')
        expected = plan.project(json.dumps(fragments))
        self.assertEqual(expected, plan.project(json.dumps(dict(reversed(list(fragments.items()))))))
        self.assertEqual([f['root']['operator'] for f in expected['fragments']],
            ['Values', 'Output', 'LocalExchange'])
        self.assertEqual(expected['fragments'][1]['root']['children'][0]['children'][0]['remote_sources'], ['f2'])
        self.assertNotIn('PRIVATE', json.dumps(expected))

    def error(self, raw, code):
        with self.assertRaises(QueryError) as error:
            plan.project(raw)
        self.assertEqual(error.exception.code, code)

    def test_fragment_links_enums_and_privacy(self):
        value = plan.project(json.dumps(synthetic_plan()))
        self.assertEqual(value['projection_version'], 1)
        self.assertEqual([f['id'] for f in value['fragments']], ['f0', 'f1'])
        aggregate = value['fragments'][0]['root']['children'][0]
        self.assertEqual(aggregate['attributes'], {'phase': 'FINAL'})
        self.assertEqual(aggregate['children'][0]['remote_sources'], ['f1'])
        text = json.dumps(value)
        for secret in ('SECRET', '12345', '987654', '/private', 's3://', 'estimates', 'outputs', '17', '29'):
            self.assertNotIn(secret, text)

    def test_unknown_names_and_unrecognized_enum_values_are_never_echoed(self):
        value = plan.project(json.dumps({'3': dict(name='SECRET operator /host/path',
            descriptor={'type': 'SECRET', 'distribution': 'SECRET'},
            children=[dict(name='Join', descriptor={'type': 'LEFT', 'distribution': 'REPLICATED'}),
                dict(name='Aggregate', descriptor={'type': 'FINAL SECRET'})])}))
        root = value['fragments'][0]['root']
        self.assertEqual(root['operator'], 'OtherOperator')
        self.assertEqual(root['attributes'], {})
        self.assertEqual(root['children'][0]['attributes'], {'join_type': 'LEFT', 'distribution': 'REPLICATED'})
        self.assertEqual(root['children'][1]['attributes'], {})
        self.assertNotIn('SECRET', json.dumps(value))

    def test_bad_links_and_non_json_numeric_strings_fail_closed(self):
        for links in ('[99]', '[true]', '["1"]', '__import__("os")', '[1,]', '[1.0]', '{}'):
            self.error(json.dumps({'0': dict(name='RemoteSource', descriptor={'sourceFragmentIds': links}),
                '1': dict(name='Values')}), 'AI_PLAN_INVALID')
        self.error('{"0":{"name":"Values"},"0":{"name":"Output"}}', 'AI_PLAN_INVALID')

    def test_cell_fragment_node_depth_bounds(self):
        self.error(' ' * (plan.MAX_CELL + 1), 'AI_PLAN_TOO_LARGE')
        self.error(json.dumps({str(i): dict(name='Values') for i in range(65)}), 'AI_PLAN_TOO_LARGE')
        node = dict(name='Values')
        for _ in range(32):
            node = dict(name='Project', children=[node])
        self.error(json.dumps({'0': node}), 'AI_PLAN_TOO_LARGE')
        self.error(json.dumps({'0': dict(name='Output', children=[dict(name='Values') for _ in range(512)])}),
            'AI_PLAN_TOO_LARGE')
        for raw in ('[]', '{}', 'null', '{"secret":{"name":"Values"}}', '{"0":{"name":42}}'):
            self.error(raw, 'AI_PLAN_INVALID')

    def test_unicode_failure_is_fixed_but_escaped_private_unicode_is_ignored(self):
        self.error('{"0":{"name":"Values","details":"\ud800"}}', 'AI_PLAN_INVALID')
        private = synthetic_plan()
        private['17']['details'] = '\ud800 PRIVATE'
        self.assertEqual(plan.project(json.dumps(private)), plan.project(json.dumps(synthetic_plan())))


class PlanTests(unittest.TestCase):
    def setUp(self):
        real_admit = admission.admit
        self.f = a1.DatabaseAITests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        # Keep A1's runtime tripwires; enable only normal query admission here.
        self.f.forbidden[1].side_effect = real_admit
        self.principal, self.context = self.f.principal, self.f.context
        self.calls, self.owners = [], []
        self.addCleanup(self.cleanup)
        self.patch('warehouse_lifecycle.applicable', return_value=False)
        self.patch('trino_auth.prepare_backend_v2', side_effect=self.prepare)
        self.patch('trino_auth.backend_ssl_context', return_value=None)
        self.patch('trino_auth.validate_backend_url')
        self.patch('warehouse_resident._borrowable_trino_connection', return_value=None)
        self.patch('warehouse_resident.persistent_trino', self.backend)
        self.patch('warehouse_resident.warehouse_activity', side_effect=lambda: nullcontext())
        self.real_transport = qs._transport
        self.transport = self.patch('query_service._transport', side_effect=self.send)
        self.pages = []

    def patch(self, target, *args, **kwargs):
        p = patch(target, *args, **kwargs)
        result = p.start()
        self.addCleanup(p.stop)
        return result

    def cleanup(self):
        for owner in self.owners:
            owner.close()
        for pin in qs._pins.values():
            qs._close_query_owner(pin)
        qs._pins.clear()
        qs._origin = None

    @contextmanager
    def backend(self):
        yield 'http://127.0.0.1:1234'

    def prepare(self, *, principal, database_id, selected_role):
        decision = auth.authorize_database(principal, database_id, selected_role,
            expected_generation_id='g-' + ('sales' if database_id == self.f.databases[0].id else 'finance'))
        return decision, {'X-Trino-User': 'synthetic-user', 'Authorization': 'Basic PRIVATE'}

    def send(self, url, method, pin, body=None):
        self.calls.append((method, body))
        if method == 'POST':
            # Independent SQLite reader proves the link and admission are committed before POST.
            _, parent, private = plan._owned(self.principal, pin.context, pin.plan.explanation_id)
            query_id = parent['plan']['query_id']
            self.assertTrue(query_id)
            self.assertEqual(qs._row(query_id)['request_id'], parent['id'])
            self.assertEqual(qs._row(query_id)['mode'], 'read-only')
            self.assertIsNone(pin.max_rows)
            with store.connect() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM warehouse_admissions WHERE subject_id=? AND state='ACTIVE'",
                    (query_id,)).fetchone()[0], 1)
                db.execute('BEGIN IMMEDIATE')
                plan.dispatch_claim(db, pin.plan, pin.context, claim=True)
            pin.initial_attempted = True
        if method == 'DELETE':
            return {}
        return self.pages.pop(0) if self.pages else dict(id='synthetic_engine',
            columns=[{'name': 'SECRET', 'type': 'varchar'}], stats={'processedRows': 987654},
            data=[[json.dumps(synthetic_plan())]])

    def accept(self, sql='SELECT * FROM orders', key='plan-key', context=None, principal=None):
        # Keep a real stable parent lease, but let tests control the acquisition phase.
        with patch.object(plan.threading, 'Thread') as worker:
            result = plan.submit(principal or self.principal, context or self.context,
                key=key, sql=sql, language='ja')
            if worker.called:
                self.args = worker.call_args.kwargs['args']
                self.owners.append(self.args[-1])
        return result

    def run_parent(self):
        plan._run(*self.args)
        return plan.get(self.args[0], self.args[1], self.args[2])

    def record(self, ident):
        return json.loads(ai._row(ident)['record'])

    def error(self, code, call):
        with self.assertRaises(QueryError) as error:
            call()
        self.assertEqual(error.exception.code, code)

    def test_general_cte_join_aggregate_original_sql_and_safe_snapshot_before_model(self):
        sql = "-- SECRET COMMENT\nWITH totals AS (SELECT customer_id, SUM(amount) n FROM orders WHERE amount>12345 GROUP BY customer_id) SELECT c.id,t.n FROM marts.customers c JOIN totals t ON c.id=t.customer_id;"
        parent = self.accept(sql)
        def complete(config, prompt, relations, language, *, plan):
            current = self.record(parent['id'])
            self.assertEqual(current['plan_status'], 'COLLECTED')
            self.assertEqual(current['plan']['query_state'], 'FINISHED')
            self.assertEqual(current['inference_state'], 'RUNNING')
            self.assertNotIn('SECRET', prompt)
            self.assertNotIn('12345', prompt)
            self.assertNotIn('SECRET', json.dumps(plan))
            return '説明'
        self.f.complete.side_effect = complete
        result = self.run_parent()
        self.assertEqual((result['state'], result['inference_state'], result['plan_status']),
            ('SUCCEEDED', 'SUCCEEDED', 'COLLECTED'))
        self.assertEqual(result['input']['sql'], sql)
        self.assertEqual(result['sql_status'], 'NOT_EXECUTED')
        self.assertEqual(self.calls, [('POST', ('EXPLAIN (TYPE DISTRIBUTED, FORMAT JSON) ' + sql.replace(';', '')).encode())])
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('control', json.dumps(result))
        self.assertEqual(len(result['metadata']['relations']), 2)

    def test_no_reference_select_and_same_key_never_replans_with_provider_down(self):
        parent = self.accept('SELECT 42')
        result = self.run_parent()
        self.assertEqual(result['metadata']['relations'], [])
        with patch.object(ai, '_config', side_effect=AssertionError('no config on replay')):
            replay = self.accept('SELECT 42')
            self.assertEqual(replay, result)
        again = qs.submit_explain(self.principal, self.context, explanation_id=parent['id'])
        self.assertEqual(again['state'], 'FINISHED')
        self.assertEqual(len(self.calls), 1)
        self.f.complete.assert_called_once()

    def test_legacy_view_rejected_before_admission_credentials_or_model(self):
        # A compatible definition still cannot use legacy/shared credentials.
        metadata = self.f.objects[('sales', 'raw', 'order_view', 'VIEW')]
        metadata['view-uuid'] = 'c97b7f6a-4d08-4d85-a416-d6330578c5eb'
        metadata['properties'] = {}
        metadata['versions'][0]['representations'] = [dict(type='sql', dialect='trino',
            sql='SELECT * FROM sales.raw.orders')]
        import logical_registry
        with store.connect() as db:
            self.assertEqual(logical_registry.creation_mode(db, self.context.database_id), 'legacy')
        self.error('AI_PLAN_VIEW_UNSUPPORTED', lambda: self.accept('SELECT * FROM order_view'))
        self.assertEqual(self.calls, [])
        self.f.complete.assert_not_called()
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM query_records').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM warehouse_admissions').fetchone()[0], 0)

    def test_user_explain_write_and_cross_database_rejected(self):
        for sql in ('EXPLAIN SELECT 1', 'EXPLAIN ANALYZE SELECT 1', 'DELETE FROM orders',
                'SELECT * FROM finance.raw.orders', 'SELECT * FROM system.runtime.queries'):
            self.error('AI_INPUT_INVALID', lambda: self.accept(sql))
        self.assertEqual(self.calls, [])

    def test_actor_context_role_and_history_kind_isolation(self):
        first = self.accept()
        self.run_parent()
        for actor, context in ((self.f.bob, self.f.ctx(principal=self.f.bob)),
                (self.f.owner, self.f.ctx(role='admin', principal=self.f.owner)),
                (self.principal, self.f.ctx(role='admin')),
                (self.principal, self.f.ctx(database=self.f.databases[1]))):
            self.error('AI_EXPLANATION_NOT_FOUND', lambda: plan.get(actor, context, first['id']))
            self.assertEqual(plan.history(actor, context), [])
        self.assertEqual([r['id'] for r in plan.history(self.principal, self.context)], [first['id']])
        self.assertEqual(ai.history(self.principal, self.context), [])
        self.error('AI_EXPLANATION_NOT_FOUND', lambda: ai.get(self.principal, self.context, first['id']))
        self.error('IDEMPOTENCY_CONFLICT', lambda: ai.submit(self.principal, self.context,
            key='plan-key', sql='SELECT * FROM orders', language='ja'))
        with patch.object(ai.threading, 'Thread') as worker:
            one = ai.submit(self.principal, self.context, key='a1', sql='SELECT 1', language='ja')
            owner = worker.call_args.kwargs['args'][-1]
            self.owners.append(owner)
        self.error('IDEMPOTENCY_CONFLICT', lambda: self.accept('SELECT 1', key='a1'))
        self.assertEqual(plan.history(self.principal, self.context)[0]['id'], first['id'])

    def test_bi_role_all_schema_permission_checked_before_admission(self):
        viewer = self.f.ctx(role='bi_viewer', schema='marts')
        self.error('SQL_PERMISSION_DENIED', lambda: self.accept('SELECT * FROM marts.orders JOIN raw.orders r ON TRUE', context=viewer))
        self.error('SQL_PERMISSION_DENIED', lambda: self.accept('SELECT 1', context=self.f.ctx(role='bi_viewer', schema='raw')))
        self.assertEqual(self.calls, [])
        parent = self.accept('SELECT * FROM orders', context=viewer)
        self.assertEqual(self.run_parent()['state'], 'SUCCEEDED')

    def test_pages_drained_even_one_row_with_next_and_generic_caches_are_safe(self):
        parent = self.accept()
        self.pages = [dict(id='synthetic_engine', columns=[{'name': 'SECRET'}],
            data=[[json.dumps(synthetic_plan())]], stats={'processedRows': 987654},
            nextUri='http://127.0.0.1:1234/v1/statement/synthetic_engine/1'),
            dict(id='synthetic_engine', data=[])]
        page = qs.submit_explain(self.principal, self.context, explanation_id=parent['id'])
        self.assertEqual(page['data'], [])
        self.assertEqual(page['columns'], [])
        self.assertEqual(page['stats'], {'state': 'RUNNING'})
        self.assertEqual(len(self.calls), 1)
        token = page['next_uri'].rsplit('/', 1)[1]
        last = qs.fetch(self.principal, page['id'], token, selected_role=self.context.selected_role)
        self.assertEqual(last['state'], 'FINISHED')
        self.assertEqual(last['data'], [])
        self.assertEqual(qs.fetch(self.principal, page['id'], token, selected_role=self.context.selected_role), last)
        self.assertEqual([m for m, _ in self.calls], ['POST', 'GET'])
        pin = qs._pins[page['id']]
        self.assertIsNone(pin.plan.raw)
        self.assertNotIn('SECRET', json.dumps(pin.responses))
        self.assertEqual(self.record(parent['id'])['plan_status'], 'COLLECTED')

    def test_invalid_projection_or_extra_cell_keeps_known_finished_query_closed(self):
        for i, data in enumerate(([[json.dumps({'0': {'name': 'RemoteSource', 'descriptor': {'sourceFragmentIds': '[4]'}}})]],
                [['not json']], [[json.dumps(synthetic_plan())], ['extra']], [[1]], [])):
            parent = self.accept(key=str(i))
            self.pages = [dict(id='synthetic_engine', data=data)]
            result = self.run_parent()
            self.assertEqual((result['state'], result['plan_status']), ('FAILED', 'FAILED'))
            self.assertEqual(result['plan']['query_state'], 'FINISHED')
            self.assertEqual(qs._row(result['plan']['query_id'])['state'], 'FINISHED')
            with store.connect() as db:
                self.assertFalse(admission.blockers(db, 'LOCAL'))
        self.f.complete.assert_not_called()

    def test_unexpected_terminal_projection_and_metadata_errors_keep_finished_closure(self):
        for phase in ('projection', 'metadata', 'authority'):
            with self.subTest(phase=phase):
                parent = self.accept(key=phase)
                original = plan.check_metadata
                authority = ai._transaction_authority
                def metadata(*args):
                    if self.calls:
                        raise RuntimeError('PRIVATE metadata diagnostic')
                    return original(*args)
                def revalidate(*args):
                    query_id = self.record(parent['id'])['plan']['query_id']
                    pin = qs._pins.get(query_id)
                    if pin is not None and pin.plan.cells:
                        raise RuntimeError('PRIVATE authority diagnostic')
                    return authority(*args)
                target, effect = {
                    'projection': ((plan, 'project'), RuntimeError('PRIVATE projection diagnostic')),
                    'metadata': ((plan, 'check_metadata'), metadata),
                    'authority': ((ai, '_transaction_authority'), revalidate),
                }[phase]
                self.calls.clear()
                with patch.object(*target, side_effect=effect):
                    result = self.run_parent()
                self.assertEqual((result['state'], result['plan_status'], result['error_code']),
                    ('FAILED', 'FAILED', 'AI_PLAN_INVALID'))
                self.assertEqual(result['plan']['query_state'], 'FINISHED')
                self.assertEqual(result['plan']['error_code'], 'AI_PLAN_INVALID')
                self.assertEqual(qs._row(result['plan']['query_id'])['state'], 'FINISHED')
                with store.connect() as db:
                    self.assertEqual(db.execute('SELECT state FROM warehouse_admissions WHERE subject_id=?',
                        (result['plan']['query_id'],)).fetchone()[0], 'FINISHED')
                    self.assertFalse(admission.blockers(db, 'LOCAL'))
                self.assertIsNone(result['plan']['operators'])
                self.assertIsNone(result['text'])
                self.assertNotIn('PRIVATE', json.dumps(result))
        self.f.complete.assert_not_called()

    def test_terminal_unicode_cell_preserves_finished_and_hides_diagnostics(self):
        self.accept()
        self.pages = [dict(id='synthetic_engine', data=[['PRIVATE\ud800']])]
        result = self.run_parent()
        self.assertEqual((result['state'], result['error_code'], result['plan']['query_state']),
            ('FAILED', 'AI_PLAN_INVALID', 'FINISHED'))
        query_id = result['plan']['query_id']
        self.assertEqual(qs._row(query_id)['state'], 'FINISHED')
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM warehouse_admissions WHERE subject_id=?',
                (query_id,)).fetchone()[0], 'FINISHED')
            self.assertFalse(admission.blockers(db, 'LOCAL'))
        self.assertIsNone(qs._pins[query_id].plan.raw)
        self.assertIsNone(result['plan']['operators'])
        self.assertIsNone(result['text'])
        for private in ('PRIVATE', 'UnicodeEncodeError', 'surrogates'):
            self.assertNotIn(private, json.dumps(result))
        self.f.complete.assert_not_called()

    def test_unexpected_capture_error_keeps_each_proven_terminal(self):
        for state in ('FINISHED', 'FAILED', 'CANCELED'):
            with self.subTest(state=state):
                parent = self.accept(key=state)
                self.pages = [dict(id='synthetic_engine',
                    nextUri='http://127.0.0.1:1234/v1/statement/synthetic_engine/1')]
                page = qs.submit_explain(self.principal, self.context, explanation_id=parent['id'])
                pin = qs._pins[page['id']]
                with patch.object(plan.Capture, 'consume', side_effect=RuntimeError('PRIVATE capture diagnostic')):
                    terminal = qs._plan_page(page['id'], pin, qs._row(page['id']),
                        'synthetic_engine', state, None, [])
                result = self.record(parent['id'])
                self.assertEqual(terminal['state'], state)
                self.assertEqual(result['plan']['query_state'], state)
                self.assertEqual(qs._row(page['id'])['state'], state)
                with store.connect() as db:
                    self.assertEqual(db.execute('SELECT state FROM warehouse_admissions WHERE subject_id=?',
                        (page['id'],)).fetchone()[0], state)
                    self.assertFalse(admission.blockers(db, 'LOCAL'))
                self.assertEqual(result['state'], 'FAILED' if state == 'FINISHED' else state)
                self.assertIsNone(pin.plan.raw)
                self.assertNotIn('PRIVATE', json.dumps(result))
        self.f.complete.assert_not_called()

    def test_nonterminal_unexpected_capture_error_retains_unknown_blocker(self):
        parent = self.accept()
        self.pages = [dict(id='synthetic_engine', data=[[json.dumps(synthetic_plan())]],
            nextUri='http://127.0.0.1:1234/v1/statement/synthetic_engine/1')]
        with patch.object(plan.Capture, 'consume', side_effect=RuntimeError('PRIVATE active diagnostic')):
            self.error('QUERY_BACKEND_FAILED', lambda: qs.submit_explain(
                self.principal, self.context, explanation_id=parent['id']))
        result = self.record(parent['id'])
        self.assertEqual((result['state'], result['plan']['query_state']), ('UNKNOWN', 'UNKNOWN'))
        query_id = result['plan']['query_id']
        self.assertEqual(qs._row(query_id)['state'], 'UNKNOWN')
        with store.connect() as db:
            self.assertEqual(len(admission.blockers(db, 'LOCAL')), 1)
        self.assertIsNone(result['plan']['execution_closure'])
        self.assertIsNone(qs._pins[query_id].plan.raw)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.f.complete.assert_not_called()

    def test_terminal_failure_survives_expired_capture_deadline_and_page_cap(self):
        send = self.send
        for budget in ('deadline', 'pages'):
            with self.subTest(budget=budget):
                parent = self.accept(key=budget)
                self.pages = [dict(id='synthetic_engine', error={'message': 'PRIVATE FAILURE'})]
                def late_response(url, method, pin, body=None):
                    response = send(url, method, pin, body)
                    if budget == 'deadline':
                        pin.plan.deadline = time.monotonic() - 1
                    else:
                        pin.plan.pages = plan.MAX_PAGES
                    return response
                self.transport.side_effect = late_response
                result = self.run_parent()
                query_id = result['plan']['query_id']
                self.assertEqual((result['state'], result['plan_status'], result['plan']['query_state']),
                    ('FAILED', 'FAILED', 'FAILED'))
                self.assertEqual(qs._row(query_id)['state'], 'FAILED')
                with store.connect() as db:
                    self.assertEqual(db.execute('SELECT state FROM warehouse_admissions WHERE subject_id=?',
                        (query_id,)).fetchone()[0], 'FAILED')
                    self.assertFalse(admission.blockers(db, 'LOCAL'))
                self.assertIsNone(result['text'])
                self.assertNotIn('PRIVATE FAILURE', json.dumps(result))
        self.f.complete.assert_not_called()

    def test_terminal_canceled_page_survives_capture_page_cap(self):
        parent = self.accept()
        self.pages = [dict(id='synthetic_engine', nextUri='http://127.0.0.1:1234/v1/statement/synthetic_engine/1')]
        page = qs.submit_explain(self.principal, self.context, explanation_id=parent['id'])
        pin = qs._pins[page['id']]
        pin.plan.pages = plan.MAX_PAGES
        result = qs._plan_page(page['id'], pin, qs._row(page['id']), 'synthetic_engine', 'CANCELED', None, [])
        self.assertEqual(result['state'], 'CANCELED')
        self.assertEqual(self.record(parent['id'])['state'], 'CANCELED')
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM warehouse_admissions WHERE subject_id=?',
                (page['id'],)).fetchone()[0], 'CANCELED')
        self.f.complete.assert_not_called()

    def test_unexpected_running_model_failure_is_unknown_and_replay_never_retries(self):
        parent = self.accept()
        def broken_complete(*args, **kwargs):
            saved = self.record(parent['id'])
            self.assertEqual(saved['inference_state'], 'RUNNING')
            self.assertEqual(qs._row(saved['plan']['query_id'])['state'], 'FINISHED')
            raise RuntimeError('PRIVATE MODEL OUTCOME')
        self.f.complete.side_effect = broken_complete
        result = self.run_parent()
        self.assertEqual((result['state'], result['inference_state'], result['plan_status']),
            ('UNKNOWN', 'UNKNOWN', 'COLLECTED'))
        self.assertTrue(result['plan']['operators'])
        self.assertIsNone(result['text'])
        self.assertEqual(result['error_code'], 'AI_PLAN_OUTCOME_UNKNOWN')
        calls = list(self.calls)
        self.assertEqual(self.accept(), result)
        self.assertEqual(plan.history(self.principal, self.context), [result])
        self.assertEqual(self.calls, calls)
        self.f.complete.assert_called_once()

    def test_known_pre_dispatch_validation_failure_is_failed_without_inference(self):
        self.accept()
        with patch.object(plan, 'check_metadata', side_effect=QueryError('AI_METADATA_CHANGED', 409)):
            result = self.run_parent()
        self.assertEqual((result['state'], result['inference_state']), ('FAILED', 'NOT_STARTED'))
        self.assertFalse(self.calls)
        self.f.complete.assert_not_called()

    def fenced_parent(self):
        parent = self.accept()
        self.pages = [dict(id='synthetic_engine', nextUri='http://127.0.0.1:1234/v1/statement/synthetic_engine/1')]
        page = qs.submit_explain(self.principal, self.context, explanation_id=parent['id'])
        qs._state(page['id'], 'UNKNOWN')
        with store.connect() as db:
            _, saved, private = plan._owned(self.principal, self.context, parent['id'], db)
            saved['inference_state'] = 'UNKNOWN'
            saved['plan']['backend_query_id'] = None  # Older parent snapshot lacks later local evidence.
            plan._save(db, parent['id'], saved, private)
            db.execute("UPDATE warehouse_admissions SET execution_closure='FENCED_READ_ONLY' WHERE subject_id=?",
                (page['id'],))
        return self.record(parent['id']), page['id']

    def test_get_history_cancel_refresh_only_local_original_fenced_child(self):
        saved, query_id = self.fenced_parent()
        with patch.object(qs, '_transport', side_effect=AssertionError('engine call')), \
                patch.object(qs, 'cancel', side_effect=AssertionError('closed child cancel')), \
                patch.object(qs, 'reconcile_query_from_backend', side_effect=AssertionError('observer call')), \
                patch.object(a1.inv, 'catalog_api', side_effect=AssertionError('catalog call')):
            results = [plan.get(self.principal, self.context, saved['id']),
                plan.history(self.principal, self.context)[0],
                plan.cancel(self.principal, self.context, saved['id'])]
        for result in results:
            self.assertEqual((result['state'], result['inference_state']), ('UNKNOWN', 'UNKNOWN'))
            self.assertEqual(result['plan']['execution_closure'], 'FENCED_READ_ONLY')
            self.assertEqual(result['plan']['query_id'], query_id)
            self.assertEqual(result['plan']['backend_query_id'], 'synthetic_engine')
            for field in ('metadata', 'input', 'model', 'text', 'plan_status'):
                self.assertEqual(result[field], saved[field])
        persisted = self.record(saved['id'])
        self.assertEqual(persisted['plan'], saved['plan'])
        self.assertEqual((persisted['state'], persisted['inference_state']), ('UNKNOWN', 'UNKNOWN'))
        self.assertIsNone(persisted['plan']['execution_closure'])
        self.assertEqual([m for m, _ in self.calls], ['POST'])
        self.f.complete.assert_not_called()

    def test_cancel_uses_current_closed_child_without_rewriting_unknown_parent(self):
        saved, query_id = self.fenced_parent()
        with store.connect() as db:
            db.execute("UPDATE query_records SET state='FINISHED' WHERE id=?", (query_id,))
            db.execute("UPDATE warehouse_admissions SET state='FINISHED',execution_closure='' WHERE subject_id=?",
                (query_id,))
        with patch.object(qs, 'cancel', side_effect=AssertionError('closed child cancel')), \
                patch.object(qs, '_transport', side_effect=AssertionError('engine call')):
            result = plan.cancel(self.principal, self.context, saved['id'])
        self.assertEqual((result['state'], result['inference_state']), ('UNKNOWN', 'UNKNOWN'))
        self.assertEqual(result['plan']['query_state'], 'FINISHED')
        self.assertIsNone(result['plan']['execution_closure'])
        self.assertEqual(self.record(saved['id'])['plan'], saved['plan'])

    def test_parent_link_to_another_genuine_child_never_exposes_its_evidence(self):
        saved, original_id = self.fenced_parent()
        other = self.accept(key='other-parent')
        other_result = self.run_parent()
        other_id = other_result['plan']['query_id']
        self.assertNotEqual(original_id, other_id)
        with store.connect() as db:
            _, record, private = plan._owned(self.principal, self.context, saved['id'], db)
            record['plan']['query_id'] = other_id
            plan._save(db, saved['id'], record, private)
        with patch.object(qs, 'cancel', side_effect=AssertionError('cross-child cancel')), \
                patch.object(qs, '_transport', side_effect=AssertionError('engine call')):
            self.error('AI_EXPLANATION_NOT_FOUND', lambda: plan.get(self.principal, self.context, saved['id']))
            self.assertEqual([r['id'] for r in plan.history(self.principal, self.context)], [other['id']])
            self.error('AI_EXPLANATION_NOT_FOUND', lambda: plan.cancel(self.principal, self.context, saved['id']))
        self.assertFalse(self.record(saved['id'])['cancel_requested'])
        self.assertEqual(qs._row(other_id)['state'], 'FINISHED')

    def test_link_mismatch_denies_local_evidence_and_cancel_before_mutation(self):
        saved, query_id = self.fenced_parent()
        cases = [('query_records', 'actor', 'other-actor')]
        cases += [('query_execution_contexts', key, value) for key, value in (
            ('request_id', 'other-parent'), ('worksheet_id', 'other-worksheet'),
            ('mode', 'read-write'), ('sql', 'SELECT 1'), ('sql_schema', 'other-schema'),
            ('binding_revision', 999), ('grant_revision', 999), ('selected_role', 'admin'))]
        with patch.object(qs, 'cancel', side_effect=AssertionError('cross-child cancel')), \
                patch.object(qs, '_transport', side_effect=AssertionError('engine call')):
            for table, column, value in cases:
                with self.subTest(column=column), store.connect() as db:
                    key = 'id' if table == 'query_records' else 'query_id'
                    original = db.execute(f'SELECT {column} FROM {table} WHERE {key}=?', (query_id,)).fetchone()[0]
                    db.execute(f'UPDATE {table} SET {column}=? WHERE {key}=?', (value, query_id))
                    db.commit()
                    self.error('AI_EXPLANATION_NOT_FOUND', lambda: plan.get(self.principal, self.context, saved['id']))
                    self.assertEqual(plan.history(self.principal, self.context), [])
                    self.error('AI_EXPLANATION_NOT_FOUND', lambda: plan.cancel(self.principal, self.context, saved['id']))
                    self.assertEqual(self.record(saved['id']), saved)
                    db.execute(f'UPDATE {table} SET {column}=? WHERE {key}=?', (original, query_id))

    def test_metadata_change_during_terminal_capture_is_closed_failure(self):
        parent = self.accept()
        real = plan.check_metadata
        def check(principal, context, ident, pins):
            if len(self.calls):
                raise QueryError('AI_METADATA_CHANGED', 409)
            return real(principal, context, ident, pins)
        with patch.object(plan, 'check_metadata', side_effect=check):
            result = self.run_parent()
        self.assertEqual(result['plan']['query_state'], 'FINISHED')
        self.assertEqual(result['plan']['error_code'], 'AI_METADATA_CHANGED')
        self.f.complete.assert_not_called()

    def test_revoke_in_terminal_transaction_drops_projection_but_commits_closure(self):
        self.accept()
        real = plan.Capture.terminal
        def terminal(capture, context):
            result = real(capture, context)
            with store.connect() as db:
                db.execute("DELETE FROM grants WHERE principal_id='alice' AND database_id=?", (context.database_id,))
            return result
        with patch.object(plan.Capture, 'terminal', terminal):
            plan._run(*self.args)
        result = self.record(self.args[2])
        self.assertEqual((result['state'], result['plan']['query_state']), ('FAILED', 'FINISHED'))
        self.assertIsNone(result['plan']['operators'])
        self.f.complete.assert_not_called()

    def test_provider_failure_retains_plan_and_replay_does_not_reinfer(self):
        parent = self.accept()
        self.f.complete.side_effect = QueryError('AI_PROVIDER_UNAVAILABLE', 502)
        result = self.run_parent()
        self.assertEqual((result['state'], result['plan_status'], result['inference_state']),
            ('FAILED', 'COLLECTED', 'FAILED'))
        self.assertIsNotNone(result['plan']['operators'])
        self.assertEqual(self.accept(), result)
        self.assertEqual(len(self.calls), 1)
        self.f.complete.assert_called_once()

    def test_provider_unknown_retains_plan_and_independent_explicit_key_is_allowed(self):
        self.accept()
        self.f.complete.side_effect = QueryError('AI_PROVIDER_TIMEOUT', 504)
        first = self.run_parent()
        self.assertEqual((first['state'], first['plan_status'], first['inference_state']),
            ('UNKNOWN', 'COLLECTED', 'UNKNOWN'))
        self.f.complete.side_effect = None
        second = self.accept(key='explicit-new-key')
        self.assertNotEqual(second['id'], first['id'])
        self.assertEqual(self.run_parent()['state'], 'SUCCEEDED')

    def test_completed_get_and_history_do_not_call_metadata_or_provider(self):
        parent = self.accept()
        result = self.run_parent()
        with patch.object(ai, '_capture', side_effect=AssertionError('historical metadata request')), \
                patch.object(ai, '_config', side_effect=AssertionError('config')):
            self.assertEqual(plan.get(self.principal, self.context, parent['id']), result)
            self.assertEqual(plan.history(self.principal, self.context), [result])
            self.assertEqual(self.accept(), result)

    def test_cancel_before_admission_and_repeated_cancel_never_submits(self):
        parent = self.accept()
        result = plan.cancel(self.principal, self.context, parent['id'])
        self.assertEqual((result['state'], result['plan_status'], result['plan']['query_state']),
            ('CANCELED', 'CANCELED', 'NOT_SUBMITTED'))
        self.run_parent()
        again = plan.cancel(self.principal, self.context, parent['id'])
        self.assertEqual(again['state'], 'CANCELED')
        self.assertEqual(self.calls, [])
        self.f.complete.assert_not_called()

    def test_cancel_during_inference_discards_late_text_preserves_plan(self):
        parent = self.accept()
        def complete(*args, **kwargs):
            result = plan.cancel(self.principal, self.context, parent['id'])
            self.assertEqual(result['state'], 'CANCELED')
            return 'LATE PRIVATE TEXT'
        self.f.complete.side_effect = complete
        result = self.run_parent()
        self.assertIsNone(result['text'])
        self.assertEqual((result['state'], result['plan_status'], result['inference_state']),
            ('CANCELED', 'COLLECTED', 'CANCELED'))
        self.assertEqual([m for m, _ in self.calls], ['POST'])

    def test_active_cancel_ack_unknown_is_durable_at_most_one_delete(self):
        parent = self.accept()
        self.pages = [dict(id='synthetic_engine', data=[],
            nextUri='http://127.0.0.1:1234/v1/statement/synthetic_engine/1')]
        page = qs.submit_explain(self.principal, self.context, explanation_id=parent['id'])
        real = self.send
        def send(url, method, pin, body=None):
            if method == 'DELETE':
                self.calls.append((method, body))
                raise QueryError('QUERY_BACKEND_FAILED', 502)
            return real(url, method, pin, body)
        self.transport.side_effect = send
        first = plan.cancel(self.principal, self.context, parent['id'])
        self.assertEqual((first['state'], first['plan']['query_state']), ('UNKNOWN', 'UNKNOWN'))
        plan.cancel(self.principal, self.context, parent['id'])
        qs._best_cancel(page['id'])
        self.assertEqual([m for m, _ in self.calls], ['POST', 'DELETE'])
        with store.connect() as db:
            self.assertEqual(len(admission.blockers(db, 'LOCAL')), 1)
        self.assertIsNone(first['plan']['execution_closure'])

    def test_cancel_active_ack_closes_and_preserves_query_evidence(self):
        parent = self.accept()
        self.pages = [dict(id='synthetic_engine', data=[],
            nextUri='http://127.0.0.1:1234/v1/statement/synthetic_engine/1')]
        page = qs.submit_explain(self.principal, self.context, explanation_id=parent['id'])
        result = plan.cancel(self.principal, self.context, parent['id'])
        self.assertEqual((result['state'], result['plan']['query_state']), ('CANCELED', 'CANCELED'))
        self.assertEqual(result['plan']['query_id'], page['id'])
        self.assertEqual(result['plan']['backend_query_id'], 'synthetic_engine')
        plan.cancel(self.principal, self.context, parent['id'])
        self.assertEqual([m for m, _ in self.calls], ['POST', 'DELETE'])

    def test_cancel_after_wake_wait_before_dispatch_prevents_post(self):
        parent = self.accept()
        def awake(principal, query_id):
            # Cancellation wins while dispatch waits, after the admission transaction.
            plan.cancel(principal, self.context, parent['id'])
            return False
        with patch.object(qs, '_ensure_query_awake', side_effect=awake):
            page = qs.submit_explain(self.principal, self.context, explanation_id=parent['id'])
            self.assertEqual(page['state'], 'CANCELED')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.record(parent['id'])['plan']['query_state'], 'CANCELED')

    def test_parent_owner_loss_never_steals_live_query_lease_then_marks_unknown(self):
        parent = self.accept()
        self.pages = [dict(id='synthetic_engine', data=[],
            nextUri='http://127.0.0.1:1234/v1/statement/synthetic_engine/1')]
        page = qs.submit_explain(self.principal, self.context, explanation_id=parent['id'])
        self.args[-1].close()
        self.assertIn(plan.get(self.principal, self.context, parent['id'])['state'], ai._ACTIVE)
        self.assertEqual(qs._row(page['id'])['state'], 'RUNNING')
        qs._close_query_owner(qs._pins[page['id']])
        result = plan.get(self.principal, self.context, parent['id'])
        self.assertEqual((result['state'], result['plan']['query_state']), ('UNKNOWN', 'UNKNOWN'))
        self.assertEqual(result['error_code'], 'AI_OWNER_LOST')
        self.assertIsNone(result['plan']['execution_closure'])
        with store.connect() as db:
            self.assertEqual(len(admission.blockers(db, 'LOCAL')), 1)
        self.assertEqual([m for m, _ in self.calls], ['POST'])

    def test_orphan_reread_does_not_downgrade_completed_parent(self):
        parent = self.accept()
        self.run_parent()
        result = plan.get(self.principal, self.context, parent['id'])
        with patch.object(ai, '_owner', side_effect=AssertionError('completed lease')):
            self.assertEqual(plan.get(self.principal, self.context, parent['id']), result)

    def test_revoke_during_model_never_stores_text(self):
        parent = self.accept()
        def complete(*args, **kwargs):
            with store.connect() as db:
                db.execute("DELETE FROM grants WHERE principal_id='alice' AND database_id=?", (self.context.database_id,))
            return 'PRIVATE LATE'
        self.f.complete.side_effect = complete
        plan._run(*self.args)
        result = self.record(parent['id'])
        self.assertIsNone(result['text'])
        self.assertEqual(result['plan_status'], 'COLLECTED')
        self.assertEqual(result['state'], 'FAILED')
        self.error('DB_ACCESS_REVOKED', lambda: plan.get(self.principal, self.context, parent['id']))

    def test_cold_pending_pages_do_not_count_backend_pages_and_remain_private(self):
        parent = self.accept()
        connection = Mock()
        response = connection.getresponse.return_value
        response.status = 200
        response.read1.side_effect = [json.dumps(dict(id='synthetic_engine',
            data=[[json.dumps(synthetic_plan())]])).encode(), b'']
        self.transport.side_effect = self.real_transport
        entered, release = threading.Event(), threading.Event()
        def wake(*args):
            entered.set()
            self.assertTrue(release.wait(3))
            return True
        with patch('warehouse_lifecycle.applicable', return_value=True), \
                patch('warehouse_lifecycle.requires_wake', return_value=True), \
                patch.object(qs, '_ensure_query_awake', side_effect=wake), \
                patch.object(qs.http.client, 'HTTPConnection', return_value=connection):
            page = qs.submit_explain(self.principal, self.context, explanation_id=parent['id'])
            self.assertTrue(entered.wait(2))
            pin = qs._pins[page['id']]
            try:
                for _ in range(3):
                    page = qs.fetch(self.principal, page['id'], page['next_uri'].rsplit('/', 1)[1],
                        selected_role=self.context.selected_role)
                    self.assertEqual((page['state'], page['columns'], page['data']), ('QUEUED', [], []))
                self.assertEqual(pin.plan.pages, 0)
                connection.request.assert_not_called()
            finally:
                release.set()
                self.assertTrue(pin.initial_done.wait(3))
            final = qs.fetch(self.principal, page['id'], page['next_uri'].rsplit('/', 1)[1],
                selected_role=self.context.selected_role)
            self.assertEqual(final['state'], 'FINISHED')
            self.assertEqual(final['data'], [])
            self.assertEqual(pin.plan.pages, 1)
            self.assertNotIn('SECRET', json.dumps(pin.responses))
            connection.request.assert_called_once()
            self.assertTrue(json.loads(ai._row(parent['id'])['pins'])['control']['dispatch_claimed'])

    def test_cold_wait_cancel_prevents_post_and_wake_completion_cannot_revive(self):
        parent = self.accept()
        connection = Mock()
        self.transport.side_effect = self.real_transport
        entered, release = threading.Event(), threading.Event()
        def wake(*args):
            entered.set()
            self.assertTrue(release.wait(3))
            return True
        with patch('warehouse_lifecycle.applicable', return_value=True), \
                patch('warehouse_lifecycle.requires_wake', return_value=True), \
                patch.object(qs, '_ensure_query_awake', side_effect=wake), \
                patch.object(qs.http.client, 'HTTPConnection', return_value=connection):
            page = qs.submit_explain(self.principal, self.context, explanation_id=parent['id'])
            self.assertTrue(entered.wait(2))
            pin = qs._pins[page['id']]
            try:
                result = plan.cancel(self.principal, self.context, parent['id'])
                self.assertEqual(result['plan']['query_state'], 'CANCELED')
            finally:
                release.set()
                self.assertTrue(pin.initial_done.wait(3))
            connection.request.assert_not_called()
            self.assertEqual(qs._row(page['id'])['state'], 'CANCELED')

    def test_concurrent_typed_submissions_link_once_and_send_one_post(self):
        parent = self.accept()
        barrier = threading.Barrier(2)
        original = qs._capture_dataops
        def capture(*args):
            result = original(*args)
            barrier.wait(3)
            return result
        results, errors = [], []
        def submit():
            try:
                results.append(qs.submit_explain(self.principal, self.context, explanation_id=parent['id']))
            except Exception as error:
                errors.append(error)
        with patch.object(qs, '_capture_dataops', side_effect=capture):
            workers = [threading.Thread(target=submit) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(5)
                self.assertFalse(worker.is_alive())
        self.assertFalse(errors, errors)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['id'], results[1]['id'])
        self.assertEqual([m for m, _ in self.calls], ['POST'])
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM query_records').fetchone()[0], 1)

    def test_real_transport_bounded_response_safe_projection_and_distinct_model_payload(self):
        self.accept()
        connection = Mock()
        response = connection.getresponse.return_value
        response.status = 200
        wire = json.dumps(dict(id='transport_engine', data=[[json.dumps(synthetic_plan())]],
            stats={'processedRows': 987654})).encode()
        response.read1.side_effect = [wire, b'']
        self.transport.side_effect = self.real_transport
        with patch.object(qs.http.client, 'HTTPConnection', return_value=connection):
            result = self.run_parent()
        self.assertEqual(result['state'], 'SUCCEEDED')
        self.assertEqual(connection.request.call_args.args[:2], ('POST', '/v1/statement'))
        self.assertNotIn('SECRET', json.dumps(self.f.complete.call_args.kwargs['plan']))
        self.assertIsNone(qs._pins[result['plan']['query_id']].plan.raw)
        self.assertTrue(json.loads(ai._row(result['id'])['pins'])['control']['dispatch_claimed'])

    def test_transport_cap_precedes_json_parse_and_keeps_uncertain_query_blocker(self):
        parent = self.accept()
        connection = Mock()
        response = connection.getresponse.return_value
        response.status = 200
        response.read1.side_effect = [b'x' * (plan.MAX_RESPONSE + 1)]
        self.transport.side_effect = self.real_transport
        with patch.object(qs.http.client, 'HTTPConnection', return_value=connection):
            result = self.run_parent()
        self.assertEqual((result['state'], result['plan']['query_state']), ('UNKNOWN', 'UNKNOWN'))
        self.assertEqual(result['plan']['error_code'], 'AI_PLAN_TOO_LARGE')
        self.assertEqual(connection.request.call_count, 1)
        self.f.complete.assert_not_called()
        with store.connect() as db:
            self.assertEqual(len(admission.blockers(db, 'LOCAL')), 1)
        self.assertIsNone(qs._pins[result['plan']['query_id']].plan.raw)

    def test_slow_stream_is_bounded_by_overall_deadline(self):
        parent = self.accept()
        connection = Mock()
        response = connection.getresponse.return_value
        response.status = 200
        def trickle(size):
            time.sleep(.06)
            return b' '
        response.read1.side_effect = trickle
        real_capture = plan.Capture
        def capture(*args):
            result = real_capture(*args)
            result.deadline = time.monotonic() + .04
            return result
        self.transport.side_effect = self.real_transport
        with patch.object(qs.http.client, 'HTTPConnection', return_value=connection), \
                patch.object(plan, 'Capture', side_effect=capture):
            started = time.monotonic()
            result = self.run_parent()
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(result['plan_status'], 'UNKNOWN')
        self.assertEqual(result['plan']['error_code'], 'AI_PLAN_TIMEOUT')
        self.f.complete.assert_not_called()

    def test_late_metadata_change_after_model_and_same_transaction_revoke_fence(self):
        parent = self.accept()
        def complete(*args, **kwargs):
            self.f.objects[('sales', 'raw', 'orders', 'TABLE')]['schemas'][0]['fields'].append(
                {'name': 'added', 'type': 'string'})
            return 'MUST NOT STORE'
        self.f.complete.side_effect = complete
        result = self.run_parent()
        self.assertEqual(result['state'], 'FAILED')
        self.assertEqual(result['error_code'], 'AI_METADATA_CHANGED')
        self.assertIsNone(result['text'])
        self.assertEqual(result['plan_status'], 'COLLECTED')

    def test_parent_recovery_rereads_after_owner_acquisition(self):
        parent = self.accept()
        self.args[-1].close()
        original = ai._owner
        def acquire(ident):
            owner = original(ident)
            with store.connect() as db:
                record = self.record(ident)
                record.update(state='SUCCEEDED', inference_state='SUCCEEDED', text='already complete')
                db.execute('UPDATE ai_explanations SET record=? WHERE id=?', (ai._json(record), ident))
            return owner
        with patch.object(ai, '_owner', side_effect=acquire):
            result = plan.get(self.principal, self.context, parent['id'])
        self.assertEqual(result['state'], 'SUCCEEDED')
        self.assertEqual(result['text'], 'already complete')
        self.assertFalse(self.calls)

    def test_last_dispatch_claim_observes_cancel_after_shared_gate_wait(self):
        parent = self.accept()
        @contextmanager
        def gate():
            # Another request can commit cancel_requested while this sender waits for the gate.
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                _, record, private = plan._owned(self.principal, self.context, parent['id'], db)
                record['cancel_requested'] = True
                plan._save(db, parent['id'], record, private)
            yield
        connection = Mock()
        self.transport.side_effect = self.real_transport
        with patch('warehouse_resident.warehouse_activity', gate), \
                patch.object(qs.http.client, 'HTTPConnection', return_value=connection):
            result = self.run_parent()
        connection.request.assert_not_called()
        self.assertEqual(result['plan']['query_state'], 'CANCELED')
        self.assertFalse(json.loads(ai._row(parent['id'])['pins'])['control'].get('dispatch_claimed'))

    def test_metadata_io_never_holds_sqlite_writer(self):
        original = self.f.catalog
        @contextmanager
        def catalog(*args):
            with store.connect() as db:
                db.execute('PRAGMA busy_timeout=10')
                db.execute('BEGIN IMMEDIATE')
            with original(*args) as api:
                yield api
        with patch.object(a1.inv, 'catalog_api', catalog):
            self.accept()
            self.assertEqual(self.run_parent()['state'], 'SUCCEEDED')

    def test_final_text_transaction_rechecks_authority_after_last_metadata_check(self):
        parent = self.accept()
        original = plan.check_metadata
        def check(principal, context, ident, pins):
            original(principal, context, ident, pins)
            if self.record(ident)['inference_state'] == 'RUNNING':
                with store.connect() as db:
                    db.execute("DELETE FROM grants WHERE principal_id='alice' AND database_id=?", (context.database_id,))
        with patch.object(plan, 'check_metadata', side_effect=check):
            plan._run(*self.args)
        result = self.record(parent['id'])
        self.assertEqual(result['state'], 'FAILED')
        self.assertEqual(result['error_code'], 'DB_ACCESS_REVOKED')
        self.assertIsNone(result['text'])
        self.assertEqual(result['plan_status'], 'COLLECTED')

    def test_known_finished_recovery_gap_is_unavailable_without_reacquisition(self):
        parent = self.accept()
        page = qs.submit_explain(self.principal, self.context, explanation_id=parent['id'])
        with store.connect() as db:
            _, record, private = plan._owned(self.principal, self.context, parent['id'], db)
            record.update(state='RUNNING', plan_status='NOT_COLLECTED')
            record['plan'].update(operators=None, captured_at=None)
            plan._save(db, parent['id'], record, private)
        self.args[-1].close()
        result = plan.get(self.principal, self.context, parent['id'])
        self.assertEqual((result['plan_status'], result['plan']['query_state']), ('FAILED', 'FINISHED'))
        self.assertEqual(result['plan']['error_code'], 'AI_PLAN_UNAVAILABLE')
        self.assertEqual(result['plan']['query_id'], page['id'])
        self.assertEqual([m for m, _ in self.calls], ['POST'])
        self.f.complete.assert_not_called()

    def test_cancel_wire_exact_body_and_sibling_history(self):
        parent = self.accept()
        headers = Message()
        headers['Content-Type'] = 'application/json'
        handler = Mock(path='/api/v1/bears/plan-explanations/' + parent['id'] + '/cancel', headers=headers)
        handler.database_body.return_value = {'context': self.context.public_metadata()}
        ai_routes.dispatch(handler, 'POST', handler.path, {}, self.principal, self.context.selected_role)
        body, status = handler.respond.call_args.args
        self.assertEqual(status, 200)
        self.assertEqual(body['explanation']['state'], 'CANCELED')
        handler.database_body.return_value = {'context': self.context.public_metadata(), 'query_id': 'no'}
        with self.assertRaises(ValueError):
            ai_routes.dispatch(handler, 'POST', handler.path, {}, self.principal, self.context.selected_role)


if __name__ == '__main__':
    unittest.main()
