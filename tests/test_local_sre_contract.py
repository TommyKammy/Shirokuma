"""Contracts for evidence-linked reads; no model, subprocess or cluster calls."""
import copy
import importlib.util
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parents[1] / 'tools/local_experiment/bears'
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location('sre_contract', HERE / 'sre_contract.py')
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)


def observations():
    stamp = '2026-09-17T02:00:00.000000Z'
    return ({'profile': 'local-experiment', 'context': 'colima-mac-studio-solo',
             'observed_at': stamp,
             'checks': [{'name': 'storage', 'status': 'healthy', 'summary': 'private raw summary'},
                        {'name': 'flux', 'status': 'informational', 'summary':
                         'root Kustomization/flux-system/flux-system is intentionally suspended for local-experiment'}]},
            {'namespace': 'shirokuma-dev', 'observed_at': stamp, 'truncated': False,
             'pods': [{'name': 'seaweedfs-0', 'uid': 'pod-old', 'phase': 'Running',
                       'ready_containers': 1, 'total_containers': 1, 'restarts': 0}]},
            {'namespace': 'shirokuma-dev', 'observed_at': stamp, 'truncated': False,
             'events': []})


def warning_event(uid='event-1', count=1):
    return {'uid': uid, 'object_uid': 'pod-old', 'type': 'Warning', 'reason': 'Unhealthy',
            'object_kind': 'Pod', 'object_name': 'seaweedfs-0', 'count': count,
            'last_observed_at': '2026-09-17T01:50:00.000000Z', 'message': 'private event message'}


def snapshots():
    return dict(zip(contract.TOOLS, observations()))


def reread(before, *tools):
    after = {tool: copy.deepcopy(before[tool]) for tool in tools}
    for value in after.values():
        value['observed_at'] = '2026-09-17T02:00:02.000000Z'
    return after


def triage(evidence, *checks):
    return {'assessment': evidence['health'], 'root_cause': 'undetermined', 'checks': list(checks)}


class EvidenceTests(unittest.TestCase):
    def test_healthy_intentional_pause_not_an_anomaly_and_no_raw_text(self):
        evidence = contract.build_evidence(*observations())
        self.assertEqual(evidence['health'], 'healthy-observed-scope')
        self.assertTrue(evidence['intentional_flux_suspension'])
        self.assertEqual(evidence['findings'], [])
        self.assertNotIn('private', str(evidence))
        self.assertIs(contract.validate_evidence(evidence), evidence)
        contract.validate_triage(triage(evidence), evidence)

    def test_incomplete_events_do_not_prove_health_or_no_warnings(self):
        data = observations()
        data[2]['truncated'] = True
        evidence = contract.build_evidence(*data)
        self.assertEqual(evidence['health'], 'unknown')
        self.assertEqual(evidence['findings'][0]['code'], 'EVENT_OBSERVATION_TRUNCATED')
        with self.assertRaises(contract.DiagnosticError):
            contract.validate_triage(triage(evidence), evidence)

    def test_missing_time_or_truncation_is_unknown_and_requires_read(self):
        for source_index, field in [(0, 'observed_at'), (1, 'truncated'), (2, 'observed_at')]:
            data = observations()
            del data[source_index][field]
            evidence = contract.build_evidence(*data)
            self.assertEqual(evidence['health'], 'unknown')
            self.assertEqual(evidence['findings'][0]['code'], 'OBSERVATION_UNKNOWN')

    def test_empty_pod_list_is_not_namespace_readiness(self):
        data = observations()
        data[1]['pods'] = []
        evidence = contract.build_evidence(*data)
        self.assertEqual(evidence['health'], 'attention-required')
        self.assertEqual(evidence['observations']['pods']['completeness'], 'empty')

    def test_cap_retains_all_critical_and_reports_omitted_warnings(self):
        data = observations()
        data[1]['pods'][0]['ready_containers'] = 0
        data[2]['events'] = [warning_event(f'event-{i}') for i in range(30)]
        evidence = contract.build_evidence(*data)
        self.assertEqual(len(evidence['findings']), 24)
        self.assertEqual(evidence['omitted_findings'], 7)
        self.assertEqual(evidence['findings'][0]['code'], 'WORKLOAD_NOT_READY')
        self.assertNotIn('private', str(evidence))
        data[1]['pods'] = [dict(data[1]['pods'][0], name=f'pod-{i}') for i in range(25)]
        with self.assertRaisesRegex(contract.DiagnosticError, 'capacity'):
            contract.build_evidence(*data)

    def test_provider_boundary_rejects_widened_or_downgraded_evidence(self):
        data = observations()
        data[1]['pods'][0]['ready_containers'] = 0
        evidence = contract.build_evidence(*data)
        edits = [lambda x: x.update(raw_message='private'), lambda x: x.update(health='unknown'),
                 lambda x: x['findings'][0].update(allowed_tools=['shell']),
                 lambda x: x['findings'][0].update(severity='warning'),
                 lambda x: x['findings'][0].update(code=[]),
                 lambda x: x['observations']['pods'].update(observed_at='yesterday')]
        for edit in edits:
            bad = copy.deepcopy(evidence)
            edit(bad)
            with self.assertRaises(contract.DiagnosticError):
                contract.validate_evidence(bad)


class TriageTests(unittest.TestCase):
    def setUp(self):
        data = observations()
        data[1]['pods'][0].update(ready_containers=0, restarts=2)
        data[2]['events'] = [warning_event()]
        self.evidence = contract.build_evidence(*data)
        self.pods = {'tool': 'k8s_read_pods', 'finding_ids': ['f01']}

    def test_selects_grounded_critical_read_and_optional_warning_followup(self):
        response = triage(self.evidence, self.pods, {'tool': 'k8s_read_events', 'finding_ids': ['f02']})
        self.assertIs(contract.validate_triage(response, self.evidence), response)

    def test_rejects_critical_omission_even_when_events_are_related(self):
        response = triage(self.evidence, {'tool': 'k8s_read_events', 'finding_ids': ['f01']})
        with self.assertRaisesRegex(contract.DiagnosticError, 'critical'):
            contract.validate_triage(response, self.evidence)

    def test_rejects_fabricated_ids_args_cause_assessment_and_duplicate_reads(self):
        good = triage(self.evidence, self.pods)
        edits = [lambda x: x['checks'][0].update(finding_ids=['invented']),
                 lambda x: x['checks'][0].update(args={'namespace': 'kube-system'}),
                 lambda x: x['checks'][0].update(tool='shell'),
                 lambda x: x.update(root_cause='OOM'),
                 lambda x: x.update(assessment='healthy-observed-scope'),
                 lambda x: x['checks'].append(copy.deepcopy(x['checks'][0]))]
        for edit in edits:
            bad = copy.deepcopy(good)
            edit(bad)
            with self.assertRaises(contract.DiagnosticError):
                contract.validate_triage(bad, self.evidence)

    def test_doctor_only_warning_cannot_request_unrelated_pods(self):
        data = observations()
        data[0]['checks'][0]['status'] = 'degraded'
        evidence = contract.build_evidence(*data)
        with self.assertRaisesRegex(contract.DiagnosticError, 'unrelated'):
            contract.validate_triage(triage(evidence, self.pods), evidence)


class ComparisonTests(unittest.TestCase):
    def test_unselected_sources_are_not_claimed_unchanged(self):
        before = snapshots()
        result = contract.compare_observations(before, reread(before, 'k8s_read_pods'))
        self.assertEqual(result['sources']['k8s_read_pods']['status'], 'no-change-observed')
        self.assertEqual(result['sources']['k8s_read_events']['status'], 'not-reobserved')
        self.assertEqual(result['root_cause'], 'undetermined')

    def test_same_name_new_uid_is_replacement_not_restart_recovery(self):
        before = snapshots()
        before['k8s_read_pods']['pods'][0]['restarts'] = 9
        after = reread(before, 'k8s_read_pods')
        after['k8s_read_pods']['pods'][0].update(uid='pod-new', restarts=0)
        changes = contract.compare_observations(before, after)['sources']['k8s_read_pods']['changes']
        self.assertEqual([x['kind'] for x in changes], ['pod-replaced'])
        self.assertEqual(changes[0]['before_uid'], 'pod-old')

    def test_event_increment_matches_uid_and_does_not_treat_expiry_as_recovery(self):
        before = snapshots()
        before['k8s_read_events']['events'] = [warning_event()]
        after = reread(before, 'k8s_read_events')
        after['k8s_read_events']['events'][0]['count'] = 3
        result = contract.compare_observations(before, after)['sources']['k8s_read_events']
        self.assertEqual(result['changes'][0]['count_delta'], 2)
        after['k8s_read_events']['events'] = []
        change = contract.compare_observations(before, after)['sources']['k8s_read_events']['changes'][0]
        self.assertEqual(change['kind'], 'events-no-longer-observed')

    def test_missing_uid_or_time_is_incomparable(self):
        for tool, source, field in [('k8s_read_pods', 'pods', 'uid'), ('k8s_read_events', 'events', 'uid')]:
            before = snapshots()
            before['k8s_read_events']['events'] = [warning_event()]
            after = reread(before, tool)
            after[tool][source][0][field] = None
            self.assertEqual(contract.compare_observations(before, after)['sources'][tool]['status'], 'incomparable')
        before = snapshots()
        after = reread(before, 'shirokuma_doctor')
        after['shirokuma_doctor']['observed_at'] = None
        self.assertEqual(contract.compare_observations(before, after)['sources']['shirokuma_doctor']['status'], 'incomparable')

    def test_truncated_equal_lists_cannot_claim_no_change(self):
        before = snapshots()
        after = reread(before, 'k8s_read_events')
        after['k8s_read_events']['truncated'] = True
        self.assertEqual(contract.compare_observations(before, after)['sources']['k8s_read_events']['status'], 'incomparable')

    def test_doctor_compares_status_without_private_summary(self):
        before = snapshots()
        after = reread(before, 'shirokuma_doctor')
        after['shirokuma_doctor']['checks'][0]['status'] = 'degraded'
        result = contract.compare_observations(before, after)
        self.assertEqual(result['sources']['shirokuma_doctor']['changes'][0]['kind'], 'doctor-changed')
        self.assertNotIn('private', str(result))

    def test_reversed_time_is_incomparable_and_cross_namespace_is_rejected(self):
        before = snapshots()
        after = reread(before, 'k8s_read_pods')
        after['k8s_read_pods']['observed_at'] = '2026-09-17T01:00:00Z'
        self.assertEqual(contract.compare_observations(before, after)['sources']['k8s_read_pods']['status'], 'incomparable')
        after['k8s_read_pods']['namespace'] = 'kube-system'
        with self.assertRaises(contract.DiagnosticError):
            contract.compare_observations(before, after)


class ProjectionTests(unittest.TestCase):
    def test_fixed_projection_retains_replayable_evidence_and_drops_free_text(self):
        before = snapshots()
        before['shirokuma_doctor']['checks'][1]['summary'] += ' private suffix'
        before['shirokuma_doctor']['checks'][0]['details'] = 'private diagnostic details'
        before['k8s_read_pods']['pods'][0].update(ready_containers=0, restarts=2, env='private credentials')
        before['k8s_read_events']['events'] = [warning_event()]
        for report in before.values():
            report['raw_output'] = 'private command output'
        projected = contract.project_observations(before)
        self.assertEqual(contract.build_evidence(*(before[tool] for tool in contract.TOOLS)),
                         contract.build_evidence(*(projected[tool] for tool in contract.TOOLS)))
        self.assertNotIn('private', str(projected))
        self.assertEqual(projected['shirokuma_doctor']['checks'][1]['summary'], contract.INTENTIONAL_FLUX_MARKER)
        self.assertNotIn('summary', projected['shirokuma_doctor']['checks'][0])
        self.assertEqual(projected['k8s_read_pods']['pods'][0]['uid'], 'pod-old')
        self.assertEqual(projected['k8s_read_events']['events'][0]['last_observed_at'], warning_event()['last_observed_at'])
        self.assertEqual(contract.project_observations(projected), projected)

    def test_partial_projection_replays_selected_changes_and_no_change(self):
        before = snapshots()
        before['k8s_read_events']['events'] = [warning_event()]
        after = reread(before, 'k8s_read_pods', 'k8s_read_events')
        after['k8s_read_pods']['pods'][0].update(uid='pod-new', restarts=0)
        after['k8s_read_events']['events'][0]['count'] = 3
        self.assertEqual(contract.compare_observations(before, after), contract.compare_observations(
            contract.project_observations(before), contract.project_observations(after)))
        unchanged = reread(before, 'shirokuma_doctor')
        self.assertEqual(contract.compare_observations(before, unchanged), contract.compare_observations(
            contract.project_observations(before), contract.project_observations(unchanged)))
        self.assertEqual(set(contract.project_observations(after)), set(after))
        self.assertEqual(contract.project_observations({}), {})

    def test_missing_fields_stay_unknown_and_cannot_become_complete(self):
        before = snapshots()
        before['k8s_read_pods']['pods'][0]['uid'] = None
        del before['k8s_read_events']['truncated']
        del before['shirokuma_doctor']['observed_at']
        projected = contract.project_observations(before)
        self.assertEqual(contract.build_evidence(*(before[tool] for tool in contract.TOOLS)),
                         contract.build_evidence(*(projected[tool] for tool in contract.TOOLS)))
        after = reread(before, 'k8s_read_pods')
        self.assertEqual(contract.compare_observations(before, after), contract.compare_observations(
            projected, contract.project_observations(after)))
        self.assertIsNone(projected['k8s_read_events']['truncated'])
        self.assertIsNone(projected['shirokuma_doctor']['observed_at'])

    def test_only_recognized_informational_flux_check_gets_fixed_marker(self):
        for name, status, summary in [('storage', 'informational', contract.INTENTIONAL_FLUX_MARKER),
                                      ('flux', 'degraded', contract.INTENTIONAL_FLUX_MARKER),
                                      ('flux', 'informational', 'private unrecognized suspension')]:
            before = snapshots()
            before['shirokuma_doctor']['checks'] = [{'name': name, 'status': status, 'summary': summary}]
            projected = contract.project_observations(before)
            self.assertNotIn('summary', projected['shirokuma_doctor']['checks'][0])
            self.assertEqual(contract.build_evidence(*(before[tool] for tool in contract.TOOLS)),
                             contract.build_evidence(*(projected[tool] for tool in contract.TOOLS)))

    def test_rejects_out_of_scope_and_invalid_fields_without_leaking_them(self):
        with self.assertRaises(contract.DiagnosticError):
            contract.project_observations({'shell': {'command': 'private'}})
        for mutate in [lambda x: x['k8s_read_pods'].update(namespace='kube-system'),
                       lambda x: x['shirokuma_doctor'].update(context='production'),
                       lambda x: x['k8s_read_events']['events'][0].update(count=True),
                       lambda x: x['k8s_read_pods']['pods'][0].update(uid='private secret'),
                       lambda x: x['k8s_read_pods']['pods'][0].update(ready_containers=2),
                       lambda x: x['shirokuma_doctor']['checks'][0].update(status=['private'])]:
            before = snapshots()
            before['k8s_read_events']['events'] = [warning_event()]
            mutate(before)
            with self.assertRaises(contract.DiagnosticError) as error:
                contract.project_observations(before)
            self.assertNotIn('private', str(error.exception))


if __name__ == '__main__':
    unittest.main()
