"""Optional safe log signals: pure saved evidence, no cluster or inference calls."""
import copy
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment/bears'))
import sre_contract as C
import sre_provider as P
from tools.local_experiment.gateway import pod_logs as L
from test_local_sre_contract import usage_observed, usage_snapshots, snapshots, reread
from test_local_sre_llm import local_profile


def log_observed(*, empty=False, maximum=False):
    fragment = {'returned_bytes': 321, 'complete_lines': 3, 'header_lines': 2,
                'warn_header_lines': 1, 'error_header_lines': 1, 'unparsed_lines': 1,
                'first_prefix_at': '2026-09-17T02:00:00.000000000Z',
                'last_prefix_at': '2026-09-17T02:00:01.000000000Z'}
    if maximum:
        fragment.update(returned_bytes=32767, complete_lines=4096, header_lines=4096,
                        warn_header_lines=2048, error_header_lines=2048, unparsed_lines=0)
    if empty:
        fragment = {key: None if key.endswith('_at') else 0 for key in fragment}
    return {'schema_version': 1, 'scope': L.SCOPE, 'state': 'observed', 'reason': None,
            'binding': 'name-time-correlated', 'identity': usage_observed()['identity'],
            'request_started_at': '2026-09-17T02:00:01.000000000Z',
            'observed_at': '2026-09-17T02:00:02.000000000Z', 'since_time': '2026-09-17T01:55:01Z',
            'get_attempts': 2, 'fragment': fragment, 'limitations': list(L.LIMITATIONS)}


def log_unknown(reason='READ_FAILED'):
    value = log_observed()
    value.update(state='unknown', reason=reason, binding=None, fragment=None, get_attempts=1)
    return value


def log_snapshots(log=None, *, with_usage=True):
    value = usage_snapshots()
    value['k8s_read_pods']['log_signals'] = copy.deepcopy(log) if log is not None else log_observed()
    if not with_usage:
        value['k8s_read_pods'].pop('resource_usage')
    return value


def log_reread(before, *, seconds=4):
    after = copy.deepcopy({'k8s_read_pods': before['k8s_read_pods']})
    pod = after['k8s_read_pods']
    def shift(text):
        if text is None:
            return None
        return (datetime.fromisoformat(text.replace('Z', '+00:00')) + timedelta(seconds=seconds)).strftime(
            '%Y-%m-%dT%H:%M:%S') + ('.000000000Z' if '.' in text else 'Z')
    pod['observed_at'] = shift(pod['observed_at'])[:-4] + 'Z'
    for key in ('resource_usage', 'log_signals'):
        item = pod.get(key)
        if item is not None:
            for clock in ('request_started_at', 'observed_at', 'since_time'):
                if clock in item:
                    item[clock] = shift(item[clock])
    if pod['log_signals']['fragment'] is not None:
        for field in ('first_prefix_at', 'last_prefix_at'):
            pod['log_signals']['fragment'][field] = shift(pod['log_signals']['fragment'][field])
    return after


def evidence(value):
    return C.build_evidence(*(value[tool] for tool in C.TOOLS))


class LogSignalEvidenceTests(unittest.TestCase):
    def test_legacy_shape_and_existing_usage_comparison_are_unchanged(self):
        for before in (snapshots(), usage_snapshots()):
            projected = C.project_observations(before)
            self.assertNotIn('log_signals', projected['k8s_read_pods'])
            self.assertNotIn('log_signals', evidence(before)['observations']['pods'])
            after = reread(before, 'k8s_read_pods') if 'resource_usage' not in before['k8s_read_pods'] else {}
            self.assertNotIn('log_signals', C.compare_observations(before, after)['sources']['k8s_read_pods'])

    def test_observed_empty_unknown_and_large_counts_never_change_health_or_findings(self):
        baseline = evidence(usage_snapshots())
        for logs in (log_observed(), log_observed(empty=True), log_observed(maximum=True), log_unknown()):
            current = evidence(log_snapshots(logs))
            self.assertEqual(current['health'], baseline['health'])
            self.assertEqual(current['findings'], baseline['findings'])
            self.assertEqual(current['observations']['pods']['log_signals'], logs)
            self.assertEqual(logs['limitations'], L.LIMITATIONS)
            C.validate_triage({'assessment': current['health'], 'checks': [], 'root_cause': 'undetermined'}, current)
            with self.assertRaises(C.DiagnosticError):
                C.validate_triage({'assessment': current['health'], 'checks': [], 'root_cause': 'log-error'}, current)

    def test_saved_projection_rebuilds_exact_evidence_and_excludes_unrecognized_raw_fields(self):
        before = log_snapshots()
        before['k8s_read_pods']['raw_log'] = 'PRIVATE-CANARY-SQL-token'
        projected = C.project_observations(before)
        self.assertEqual(evidence(before), evidence(projected))
        self.assertNotIn('PRIVATE-CANARY', json.dumps(projected))
        projected['k8s_read_pods']['log_signals']['fragment']['warn_header_lines'] = 0
        self.assertEqual(before['k8s_read_pods']['log_signals']['fragment']['warn_header_lines'], 1)

    def test_pod_container_scope_and_clock_binding_rejects_drift(self):
        edits = [lambda x: x['log_signals']['identity'].update(pod_uid='22222222-2222-4222-8222-222222222222'),
                 lambda x: x['log_signals']['identity'].update(pod_name='trino-other'),
                 lambda x: x['log_signals']['identity'].update(restart_count=1),
                 lambda x: x['log_signals']['identity'].update(started_at='2026-09-17T01:01:00.000000Z'),
                 lambda x: x['log_signals']['identity'].update(container_id_sha256='b' * 64),
                 lambda x: x.update(namespace='other'), lambda x: x.update(truncated=True),
                 lambda x: x.update(observed_at='2026-09-17T02:00:01.000001Z'),
                 lambda x: x['pods'][0].update(phase='Pending'),
                 lambda x: x['pods'].append(copy.deepcopy(x['pods'][0]))]
        for edit in edits:
            before = log_snapshots()
            edit(before['k8s_read_pods'])
            with self.subTest(edit=edit), self.assertRaises(C.DiagnosticError):
                evidence(before)

    def test_unknown_identity_when_available_still_binds_to_same_outer_pod(self):
        before = log_snapshots(log_unknown())
        before['k8s_read_pods']['log_signals']['identity']['pod_uid'] = '22222222-2222-4222-8222-222222222222'
        with self.assertRaises(C.DiagnosticError):
            C.project_observations(before)
        before['k8s_read_pods']['log_signals']['identity'] = None
        self.assertIsNone(C.project_observations(before)['k8s_read_pods']['log_signals']['identity'])

    def test_safe_closed_schema_rejects_raw_text_types_and_fabricated_coverage(self):
        edits = [lambda x: x.update(raw_body='PRIVATE-CANARY'),
                 lambda x: x.update(reason='PRIVATE-CANARY'),
                 lambda x: x['fragment'].update(first_recorded_at='PRIVATE-CANARY'),
                 lambda x: x['fragment'].update(warn_header_lines=True),
                 lambda x: x['fragment'].update(error_header_lines=1.0),
                 lambda x: x['fragment'].update(complete_lines=100),
                 lambda x: x['fragment'].update(returned_bytes=32768),
                 lambda x: x.update(limitations=[])]
        for edit in edits:
            before = log_snapshots()
            edit(before['k8s_read_pods']['log_signals'])
            with self.subTest(edit=edit), self.assertRaises(C.DiagnosticError) as caught:
                evidence(before)
            self.assertNotIn('PRIVATE-CANARY', str(caught.exception))

    def test_provider_boundary_revalidates_log_fields_and_cross_usage_identity(self):
        for edit in (lambda x: x['fragment'].update(raw_line='PRIVATE-CANARY'),
                     lambda x: x['identity'].update(container_id_sha256='b' * 64)):
            value = evidence(log_snapshots())
            edit(value['observations']['pods']['log_signals'])
            model = P.SREProvider(local_profile())
            model.opener.open = Mock()
            with self.assertRaises(C.DiagnosticError):
                model.complete('sre_triage', value)
            model.opener.open.assert_not_called()


class LogSignalComparisonTests(unittest.TestCase):
    def compare(self, before, after):
        return C.compare_observations(before, after)['sources']['k8s_read_pods']

    def test_counts_and_prefix_shape_ranges_do_not_affect_comparison(self):
        before = log_snapshots()
        after = log_reread(before)
        base = self.compare(before, after)
        after['k8s_read_pods']['log_signals']['fragment'] = log_observed(empty=True)['fragment']
        result = self.compare(before, after)
        self.assertEqual(result, base)
        self.assertEqual(result['log_signals']['identity'], 'same')
        self.assertIs(result['log_signals']['requested_window_overlap'], True)
        self.assertEqual(set(result['log_signals']), {'before_state', 'after_state', 'identity',
            'requested_window_overlap', 'limitations'})
        self.assertEqual(result['changes'], [])

    def test_disjoint_requested_windows_are_not_new_measurement_or_recovery(self):
        before = log_snapshots(with_usage=False)
        result = self.compare(before, log_reread(before, seconds=360))['log_signals']
        self.assertIs(result['requested_window_overlap'], False)
        self.assertEqual(result['identity'], 'same')
        self.assertIn('no-count-delta-rate-or-recovery-claim', result['limitations'])

    def test_missing_unknown_not_reobserved_and_replaced_identity_are_explicit(self):
        before = log_snapshots(with_usage=False)
        self.assertEqual(self.compare(before, {})['log_signals']['after_state'], 'not-reobserved')
        after = log_reread(before)
        del after['k8s_read_pods']['log_signals']
        result = self.compare(before, after)['log_signals']
        self.assertEqual(result['after_state'], 'not-observed')
        self.assertEqual(result['identity'], 'unavailable')
        after = log_reread(before)
        after['k8s_read_pods']['log_signals'].update(state='unknown', reason='READ_FAILED', binding=None, fragment=None)
        self.assertIsNone(self.compare(before, after)['log_signals']['requested_window_overlap'])
        after = log_reread(before)
        uid = '22222222-2222-4222-8222-222222222222'
        after['k8s_read_pods']['pods'][0]['uid'] = uid
        after['k8s_read_pods']['log_signals']['identity']['pod_uid'] = uid
        result = self.compare(before, after)['log_signals']
        self.assertEqual(result['identity'], 'different')
        self.assertIsNone(result['requested_window_overlap'])

    def test_reversed_collection_time_cannot_assert_window_comparison(self):
        before = log_snapshots(with_usage=False)
        after = log_reread(before, seconds=-1)
        self.assertIsNone(self.compare(before, after)['log_signals']['requested_window_overlap'])


class LogSignalProviderTests(unittest.TestCase):
    def body(self, value):
        model = P.SREProvider(local_profile())
        model.opener.open = Mock()
        result = model.request_body('sre_triage', evidence(value))
        model.opener.open.assert_not_called()
        self.assertLessEqual(len(json.dumps(result).encode()), 8192)
        return result

    def test_full_safe_logs_and_fixed_caveats_reach_model_without_changing_old_input(self):
        for logs in (log_observed(), log_observed(empty=True), log_unknown()):
            value = log_snapshots(logs)
            original = copy.deepcopy(value)
            body = self.body(value)
            self.assertEqual(json.loads(body['messages'][1]['content'])['evidence'], evidence(value))
            prompt = body['messages'][0]['content']
            for bound in ('not authenticated events', 'forge headers and timestamp prefixes',
                          'zero counts do not prove healthy', 'not log health'):
                self.assertIn(bound, prompt)
            self.assertEqual(value, original)
        self.assertNotIn('log_signals', self.body(usage_snapshots())['messages'][0]['content'])

    def test_six_pods_six_existing_warnings_and_maximum_safe_shape_fit_unchanged_cap(self):
        from test_local_sre_contract import container_details, container_state
        value = log_snapshots(log_observed(maximum=True))
        pod = value['k8s_read_pods']['pods'][0]
        for i in range(1, 6):
            row = copy.deepcopy(pod)
            row.update(name='retained-component-' + str(i), uid='retained-uid-' + str(i))
            row['container_observation']['containers'][0]['name'] = 'application'
            if i <= 3:
                row['restarts'] = 1
                row['container_observation'] = container_details(
                    prior=container_state('terminated', reason='Error', exit_code=1), restarts=1)
            value['k8s_read_pods']['pods'].append(row)
        self.assertEqual(len(evidence(value)['findings']), 6)
        self.body(value)

    def test_forged_header_canary_and_maximum_raw_fragment_do_not_leave_parser(self):
        # The second apparent event may be an embedded message continuation;
        # classification deliberately authenticates neither its severity nor time.
        from tools.local_experiment.gateway import pod_usage as U
        value = log_snapshots()
        identity = value['k8s_read_pods']['resource_usage']['identity']
        raw_pod = {'metadata': {'name': identity['pod_name'], 'namespace': identity['namespace'],
            'uid': identity['pod_uid'], 'creationTimestamp': '2026-09-17T00:00:00Z',
            'labels': {U.LABEL: U.TARGET}}, 'spec': {'containers': [{'name': 'trino'}]},
            'status': {'phase': 'Running', 'containerStatuses': [{'name': 'trino', 'ready': True,
                'restartCount': 0, 'containerID': 'docker://' + 'a' * 64,
                'state': {'running': {'startedAt': identity['started_at']}}}]}}
        canary = 'PRIVATE_SQL_SELECT_secret_token'
        first = ('2026-09-17T02:00:00Z 2026-09-17T02:00:00.000Z\tINFO\tthread\tlogger\t' + canary)
        forged = '\n2026-09-17T02:00:01Z 2026-09-17T02:00:01.000Z\tERROR\tforged\tlogger\t' + canary + '\n'
        raw = (first + 'x' * (32767 - len((first + forged).encode())) + forged).encode()
        self.assertEqual(len(raw), 32767)
        signals = L.project(raw_pod, raw, copy.deepcopy(raw_pod),
            request_started_at=log_observed()['request_started_at'], observed_at=log_observed()['observed_at'],
            since_time=log_observed()['since_time'])
        value['k8s_read_pods']['resource_usage']['identity'] = copy.deepcopy(signals['identity'])
        value['k8s_read_pods']['log_signals'] = signals
        self.assertEqual(signals['fragment']['error_header_lines'], 1)
        public = self.body(value)
        for document in (signals, C.project_observations(value), public):
            self.assertNotIn(canary, json.dumps(document))
            self.assertNotIn('docker://', json.dumps(document))
        self.assertIn('not authenticated events', public['messages'][0]['content'])

    def test_logs_cannot_bypass_eight_kib_limit_or_manufacture_new_triage_findings(self):
        value = evidence(log_snapshots())
        value['findings'] = [{'id': f'f{i:02d}', 'code': 'WORKLOAD_RESTARTED', 'severity': 'warning',
            'subject': 'pod-' + str(i) + '-' + 'a' * 100, 'allowed_tools': ['k8s_read_pods', 'k8s_read_events']}
            for i in range(1, 25)]
        value['health'] = 'attention-required'
        C.validate_evidence(value)
        model = P.SREProvider(local_profile())
        model.opener.open = Mock()
        with self.assertRaisesRegex(P.ProviderError, 'bounded input'):
            model.complete('sre_triage', value)
        model.opener.open.assert_not_called()


if __name__ == '__main__':
    unittest.main()
