"""Real local OTel and fake backend/controller boundaries; no runtime calls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.warehouse_lifecycle import query_telemetry as Q
from tools.local_experiment.warehouse_lifecycle import controller as K
import test_local_lifecycle_telemetry as L
import test_local_warehouse_lifecycle_client as F
import test_local_warehouse_query as W

TOKEN = F.TOKEN
REQUEST = W.REQUEST


def projection():
    return dict(warehouse='XS', request_id=REQUEST, case='xs_caller',
        query_id='20260918_000000_00001_abcde',
        source='shirokuma-wl-' + TOKEN + '-xs_caller-' + REQUEST.replace('-', ''),
        user='shirokuma-wl-' + TOKEN + '-worker', sql_sha256=Q.digest(Q.SELECT.encode()),
        rows_observed=25, rows_sha256=Q.digest(Q.expected_rows(TOKEN, 'xs')),
        post_count=1, response_complete=True, matched=True, wake_verified=True,
        terminal_verified=True, all_results_consumed=True, wake_wait_ms=200, query_client_ms=80)


def completed_controller():
    clock = F.Clock()
    actions, queries = [], []
    with patch.object(K.time, 'monotonic', clock.monotonic), patch.object(K.time, 'sleep', clock.sleep):
        client = W.FakeQueryClient()
        env = F.Environment(client)
        controller = K.Controller(client, guard=env.guard, scale=env.scale, reconnect=env.reconnect,
            progress=lambda value: None, decision=lambda *args: actions.append(copy.deepcopy(args)))
        controller.prepare()
        controller.chill()
        controller.query(REQUEST, audit=lambda *args: queries.append(copy.deepcopy(args)))
        observations = controller.receipt()
    return observations, actions, queries[0]


class QueryProjectionTests(unittest.TestCase):
    def test_fixed_query_projection_is_independent_and_keeps_phase_durations(self):
        original = projection()
        value = Q.project_query(original, 300, TOKEN)
        self.assertEqual(value, original)
        value['source'] = 'foreign'
        self.assertNotEqual(value, original)
        self.assertEqual(set(original), Q.QUERY_KEYS)

    def test_exact_identity_source_and_logical_oracle_are_required(self):
        changes = [('request_id', REQUEST.upper()), ('request_id', None), ('warehouse', 'S'),
            ('case', 'xs_after'), ('query_id', 'private SQL'), ('source', 'foreign-source'),
            ('user', 'foreign-user'), ('sql_sha256', '0' * 64), ('rows_sha256', '0' * 64),
            ('raw_sql', Q.SELECT)]
        for key, value in changes:
            with self.subTest(key=key):
                candidate = projection(); candidate[key] = value
                with self.assertRaises(Q.T.TelemetryError): Q.project_query(candidate, 300, TOKEN)
        candidate = projection(); del candidate['source']
        with self.assertRaises(Q.T.TelemetryError): Q.project_query(candidate, 300, TOKEN)

    def test_result_requires_exact_types_and_all_terminal_proofs(self):
        changes = [('rows_observed', True), ('rows_observed', 25.0), ('rows_observed', 24),
            ('post_count', True), ('post_count', 2)]
        changes += [(key, value) for key in ('response_complete', 'matched', 'wake_verified',
            'terminal_verified', 'all_results_consumed') for value in (False, 1, None)]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                candidate = projection(); candidate[key] = value
                with self.assertRaises(Q.T.TelemetryError): Q.project_query(candidate, 300, TOKEN)

    def test_duration_exact_integer_bounds_and_disjoint_phase_sum(self):
        for value in (None, True, -1, 300.0, 2**64):
            with self.subTest(duration=value), self.assertRaises(Q.T.TelemetryError):
                Q.project_query(projection(), value, TOKEN)
        for key in ('wake_wait_ms', 'query_client_ms'):
            for value in (None, True, -1, 1.0, 301):
                candidate = projection(); candidate[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(Q.T.TelemetryError):
                    Q.project_query(candidate, 300, TOKEN)
        with self.assertRaises(Q.T.TelemetryError): Q.project_query(projection(), 279, TOKEN)
        self.assertEqual(Q.project_query(projection(), 280, TOKEN), projection())


@unittest.skipUnless(L.HAS_OTEL, 'Run in the pinned LLM venv for real OTel coverage')
class QueryTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name).resolve() / TOKEN; self.output.mkdir(mode=0o700)
        self.backend = L.FakeBackend()
        self.observations, self.actions, self.query_args = completed_controller()

    def telemetry(self, label=None, backend=None):
        output = self.output
        if label:
            output = self.output / label / TOKEN
            output.parent.mkdir(mode=0o700); output.mkdir(mode=0o700)
        return Q.LifecycleQueryTelemetry(output, TOKEN, backend=backend or self.backend)

    def decisions(self, audit):
        for args in self.actions: audit.decision(*args)

    def receipt(self):
        return dict(run_token=TOKEN, result='verified', pending=None,
            observations=copy.deepcopy(self.observations), cleanup=copy.deepcopy(L.CLEANUP))

    def saved(self, audit):
        return json.loads((audit.output / 'telemetry.json').read_text())

    def spans(self, audit):
        return [json.loads(line) for line in audit.spans.read_text().splitlines()]

    def ready_to_finish(self, audit):
        self.decisions(audit); audit.query(*self.query_args)

    def test_complete_real_controller_proof_delivers_five_correlated_real_spans(self):
        audit = self.telemetry()
        with audit:
            self.ready_to_finish(audit); audit.finish(self.receipt())
        saved = self.saved(audit); spans = self.spans(audit)
        self.assertEqual(saved['result'], 'passed'); self.assertTrue(saved['saved_spans_verified'])
        self.assertEqual((len(self.backend.events), len(self.backend.logs), len(spans)), (5, 5, 5))
        self.assertEqual([e['action_type'] for e in self.backend.events],
            ['warehouse.lifecycle.' + a for a in (*Q.T.ACTIONS, 'query', 'run')])
        self.assertTrue(all(e['model_provider'] == 'none' and e['prompt_tokens'] is None
            and e['completion_tokens'] is None and e['inference_cost_estimate'] is None for e in self.backend.events))
        query = next(s for s in spans if s['name'] == 'warehouse.lifecycle.audit.query')
        self.assertEqual(query['parent_span_id'], saved['root_span_id'])
        self.assertEqual(query['trace_id'], saved['trace_id'])
        self.assertEqual(query['attributes']['projection_sha256'], Q.digest(self.query_args[0]))
        self.assertEqual(query['attributes']['controller_duration_ms'], self.query_args[1])
        self.assertTrue(query['attributes']['includes_wake_probes'])
        self.assertEqual(query['attributes']['duration_kind'], 'audit-delivery-not-controller-decision')
        self.assertEqual(spans[-1]['name'], 'warehouse.lifecycle.run')
        self.assertTrue(spans[-1]['attributes']['cleanup_verified'])
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in audit.output.iterdir()))
        self.assertNotIn(Q.SELECT, json.dumps(saved)); self.assertNotIn('ALGERIA', json.dumps(saved))

    def test_query_order_and_duplicate_refused_before_another_delivery(self):
        for prefix in range(3):
            backend = L.FakeBackend(); audit = self.telemetry(str(prefix), backend)
            with self.subTest(prefix=prefix), self.assertRaises(Q.T.TelemetryError):
                with audit:
                    for args in self.actions[:prefix]: audit.decision(*args)
                    audit.query(*self.query_args)
            self.assertNotIn('warehouse.lifecycle.query', [e['action_type'] for e in backend.events])
        audit = self.telemetry()
        with audit:
            self.ready_to_finish(audit)
            with self.assertRaises(Q.T.TelemetryError): audit.query(*self.query_args)
            audit.finish(self.receipt())
        self.assertEqual(sum(e['action_type'] == 'warehouse.lifecycle.query' for e in self.backend.events), 1)

    def test_query_refuses_unverified_prior_decision(self):
        audit = self.telemetry()
        with self.assertRaises(Q.T.TelemetryError), audit:
            self.decisions(audit)
            audit.journal['records'][-1]['loki_verified'] = False
            audit.query(*self.query_args)
        self.assertNotIn('warehouse.lifecycle.query', [e['action_type'] for e in self.backend.events])

    def test_query_intent_and_exact_event_must_be_durable_before_backend_write(self):
        for phase in ('intent', 'event'):
            backend = L.FakeBackend(); audit = self.telemetry(phase, backend)
            original = audit.save; failures = []
            def flaky():
                record = audit.journal['records'][-1] if audit.journal['records'] else None
                reject = (audit.journal['pending_action'] == 'query' if phase == 'intent' else
                    bool(record and record['event']['action_type'] == 'warehouse.lifecycle.query'))
                if reject and not failures: failures.append(True); raise OSError('PRIVATE disk')
                original()
            with self.subTest(phase=phase), self.assertRaises(OSError):
                with audit:
                    self.decisions(audit)
                    with patch.object(audit, 'save', side_effect=flaky): audit.query(*self.query_args)
            self.assertNotIn('warehouse.lifecycle.query', [e['action_type'] for e in backend.events])
            self.assertEqual(self.saved(audit)['result'], 'failed')

    def test_each_query_AH_Loki_delivery_failure_is_durable_and_never_passes(self):
        for stage in ('insert', 'verify', 'push_log', 'verify_log'):
            backend = L.FakeBackend(); audit = self.telemetry(stage, backend)
            with self.subTest(stage=stage), self.assertRaises(Q.T.TelemetryError):
                with audit:
                    self.decisions(audit); backend.fail = stage
                    try: audit.query(*self.query_args)
                    finally: audit.record_cleanup(L.CLEANUP)
            saved = self.saved(audit); query = saved['records'][3]
            self.assertEqual(saved['result'], 'failed'); self.assertTrue(saved['cleanup_verified'])
            self.assertEqual(query['delivery'], 'failed'); self.assertIsNone(saved['pending_action'])
            self.assertEqual(saved['records'][-1]['event']['status'], 'failed')
            self.assertNotIn('PRIVATE', json.dumps(saved))
            self.assertLessEqual(sum(e['action_type'] == 'warehouse.lifecycle.query' for e in backend.events), 1)

    def test_query_original_cancellation_survives_secondary_journal_failure(self):
        audit = self.telemetry(); interruption = KeyboardInterrupt('PRIVATE cancellation')
        original = audit.save
        def outage():
            if audit.journal['result'] == 'failed': raise OSError('PRIVATE disk')
            original()
        with self.assertRaises(KeyboardInterrupt) as caught:
            with audit:
                self.decisions(audit)
                with patch.object(self.backend, 'push_log', side_effect=interruption), patch.object(audit, 'save', side_effect=outage):
                    audit.query(*self.query_args)
        self.assertIs(caught.exception, interruption); self.assertEqual(audit.receipt()['result'], 'failed')

    def test_missing_query_or_incomplete_controller_cannot_finish(self):
        audit = self.telemetry()
        with self.assertRaises(Q.T.TelemetryError), audit:
            self.decisions(audit); audit.finish(self.receipt())
        for label, mutate in [
            ('missing-case', lambda v: v['observations']['client']['requests'].pop()),
            ('incomplete', lambda v: v['observations'].update(result='running')),
            ('pending', lambda v: v.update(pending={'operation': 'query'})),
            ('cleanup', lambda v: v['cleanup'].update(verified=False, deferred=['owned']))]:
            audit = self.telemetry(label, L.FakeBackend()); value = self.receipt(); mutate(value)
            with self.subTest(label=label), self.assertRaises(Exception), audit:
                self.ready_to_finish(audit); audit.finish(value)
            self.assertEqual(self.saved(audit)['result'], 'failed')

    def test_finish_caller_is_exactly_bound_to_delivered_query(self):
        for label, mutate in [
            ('request', lambda c: c.update(request_id='00000000-0000-4000-8000-000000000000')),
            ('float-wake', lambda c: c.update(wake_wait_ms=float(c['wake_wait_ms']))),
            ('float-query', lambda c: c.update(query_client_ms=float(c['query_client_ms']))),
            ('duration', lambda c: c.update(duration_ms=c['duration_ms'] + 1)),
            ('public', lambda c: c['query'].update(rows_sha256='0' * 64)),
            ('projection', lambda c: c['projection'].update(source='foreign')),
            ('extra', lambda c: c.update(raw='PRIVATE')),
            ('post-bool', lambda c: c.update(post_intents=True)),
            ('stage', lambda c: c.update(stage='audit-pending'))]:
            audit = self.telemetry(label, L.FakeBackend()); value = self.receipt(); mutate(value['observations']['caller'])
            with self.subTest(label=label), self.assertRaises(Exception), audit:
                self.ready_to_finish(audit); audit.finish(value)
            self.assertEqual(self.saved(audit)['result'], 'failed')

    def test_finish_requires_new_XS_identity_fixed_scales_and_drained_history(self):
        for label, mutate in [
            ('same-xs', lambda v: v['identity']['final']['xs'].update(
                pod_uid=v['identity']['initial']['xs']['pod_uid'])),
            ('changed-s', lambda v: v['identity']['final']['s'].update(
                pod_uid=v['identity']['final']['xs']['pod_uid'])),
            ('changed-deployment', lambda v: v['identity']['final']['xs'].update(
                deployment_uid=v['identity']['initial']['s']['deployment_uid'])),
            ('wrong-scale', lambda v: v['scale_operations'][0].update(to_replicas=1)),
            ('scale-identity', lambda v: v['scale_operations'][1]['after'].update(
                pod_uid=v['identity']['initial']['xs']['pod_uid'])),
            ('pending-handle', lambda v: v['client']['ledger'].update(pending_handles=1)),
            ('uncaptured', lambda v: v['client']['ledger'].update(uncaptured_requests=1)),
            ('unconsumed', lambda v: v['client']['ledger'].update(all_results_consumed=False)),
            ('incomplete-history', lambda v: v['client']['query_snapshots'][-1].update(complete=False))]:
            audit = self.telemetry(label, L.FakeBackend()); value = self.receipt()
            mutate(value['observations'])
            with self.subTest(label=label), self.assertRaises(Exception), audit:
                self.ready_to_finish(audit); audit.finish(value)
            self.assertEqual(self.saved(audit)['result'], 'failed')

    def test_final_journal_failure_never_returns_passed(self):
        audit = self.telemetry(); original = audit.save
        def outage():
            if audit.journal['result'] == 'passed': raise OSError('PRIVATE final')
            original()
        with self.assertRaises(Q.T.TelemetryError):
            with patch.object(audit, 'save', side_effect=outage), audit:
                self.ready_to_finish(audit); audit.finish(self.receipt())
        self.assertEqual(audit.receipt()['result'], 'failed'); self.assertNotEqual(self.saved(audit)['result'], 'passed')

    def test_query_saved_span_tamper_rejected_and_verified_flag_false(self):
        for label, mutate in [
            ('wake-ms', lambda s: s['attributes'].update(wake_wait_ms=-1)),
            ('float-wake', lambda s: s['attributes'].update(wake_wait_ms=float(s['attributes']['wake_wait_ms']))),
            ('float-query', lambda s: s['attributes'].update(query_client_ms=float(s['attributes']['query_client_ms']))),
            ('includes', lambda s: s['attributes'].update(includes_wake_probes=False)),
            ('parent', lambda s: s.update(parent_span_id='0' * 16)),
            ('name', lambda s: s.update(name='foreign')),
            ('projection', lambda s: s['attributes'].update(projection_sha256='0' * 64))]:
            audit = self.telemetry(label, L.FakeBackend()); verify = audit._verify_spans
            def corrupt_then_verify():
                rows = self.spans(audit)
                mutate(next(s for s in rows if s['name'] == 'warehouse.lifecycle.audit.query'))
                audit.spans.write_text(''.join(json.dumps(row) + '\n' for row in rows))
                verify()
            with self.subTest(label=label), self.assertRaises(Q.T.TelemetryError):
                with patch.object(audit, '_verify_spans', side_effect=corrupt_then_verify), audit:
                    self.ready_to_finish(audit); audit.finish(self.receipt())
            saved = self.saved(audit)
            self.assertEqual(saved['result'], 'failed'); self.assertFalse(saved['saved_spans_verified'])
            self.assertEqual(saved['records'][-1]['event']['status'], 'failed')


if __name__ == '__main__': unittest.main()
