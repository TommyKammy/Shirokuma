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
             'events': []},
            {'profile': 'local-experiment', 'observed_at': stamp, 'ready': True,
             'components': {name: {'ready': True, 'status': 'ready'} for name in contract.COMPONENTS}})


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

    def test_omitted_platform_remains_unknown_and_requires_its_own_read(self):
        evidence = contract.build_evidence(*observations()[:3])
        self.assertEqual(evidence['health'], 'unknown')
        self.assertEqual(evidence['observations']['platform'],
                         {'observed_at': None, 'completeness': 'unknown', 'count': 0})
        self.assertEqual([(row['code'], row['subject'], row['allowed_tools']) for row in evidence['findings']],
                         [('OBSERVATION_UNKNOWN', 'platform', ['local_platform_status'])])
        contract.validate_triage(triage(evidence, {'tool': 'local_platform_status', 'finding_ids': ['f01']}), evidence)

    def test_runtime_not_ready_is_critical_and_unknown_does_not_assert_health(self):
        for status, code, severity, health in [('not-ready', 'RUNTIME_NOT_READY', 'critical', 'attention-required'),
                                             ('unknown', 'RUNTIME_STATUS_UNKNOWN', 'warning', 'unknown')]:
            data = observations()
            data[3]['ready'] = False
            data[3]['components']['superset'] = {'ready': False, 'status': status}
            evidence = contract.build_evidence(*data)
            self.assertEqual(evidence['health'], health)
            self.assertEqual([(x['code'], x['subject'], x['severity'], x['allowed_tools']) for x in evidence['findings']],
                             [(code, 'superset', severity, ['local_platform_status'])])
            for bad_health in ('healthy-observed-scope', 'attention-required' if health == 'unknown' else 'unknown'):
                bad = dict(evidence, health=bad_health)
                with self.assertRaises(contract.DiagnosticError):
                    contract.validate_evidence(bad)

    def test_runtime_projection_rejects_wrong_scope_components_and_conflicting_readiness(self):
        edits = [lambda x: x.update(profile='production'), lambda x: x.update(ready=False),
                 lambda x: x.update(ready=1), lambda x: x.update(observed_at='private date text'),
                 lambda x: x['components'].pop('loki'),
                 lambda x: x['components'].update(unrelated={'ready': True, 'status': 'ready'}),
                 lambda x: x['components']['loki'].update(ready=False),
                 lambda x: x['components']['loki'].update(status='unknown'),
                 lambda x: x['components']['loki'].update(ready='true'),
                 lambda x: x['components']['loki'].update(status=['private']),
                 lambda x: x['components']['loki'].update(reason='private reason')]
        for edit in edits:
            data = observations()
            edit(data[3])
            with self.subTest(edit=edit), self.assertRaises(contract.DiagnosticError) as error:
                contract.build_evidence(*data)
            self.assertNotIn('private', str(error.exception))

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

    def test_all_runtime_and_pod_critical_findings_require_both_reads_within_budget(self):
        data = observations()
        data[1]['pods'][0]['ready_containers'] = 0
        data[3]['ready'] = False
        for component in ('superset', 'metadata'):
            data[3]['components'][component] = {'ready': False, 'status': 'not-ready'}
        evidence = contract.build_evidence(*data)
        runtime_ids = [row['id'] for row in evidence['findings'] if row['code'] == 'RUNTIME_NOT_READY']
        pod_ids = [row['id'] for row in evidence['findings'] if row['code'] in contract.POD_CRITICAL]
        runtime = {'tool': 'local_platform_status', 'finding_ids': runtime_ids}
        pods = {'tool': 'k8s_read_pods', 'finding_ids': pod_ids}
        contract.validate_triage(triage(evidence, runtime, pods), evidence)
        for checks in ([runtime], [pods], [dict(runtime, finding_ids=runtime_ids[:1]), pods],
                       [dict(runtime, tool='shirokuma_doctor'), pods]):
            with self.subTest(checks=checks), self.assertRaises(contract.DiagnosticError):
                contract.validate_triage(triage(evidence, *checks), evidence)

    def test_runtime_subject_cannot_select_unrelated_tool_or_invent_component(self):
        data = observations()
        data[3]['ready'] = False
        data[3]['components']['loki'] = {'ready': False, 'status': 'unknown'}
        evidence = contract.build_evidence(*data)
        with self.assertRaises(contract.DiagnosticError):
            contract.validate_triage(triage(evidence, {'tool': 'k8s_read_pods', 'finding_ids': ['f01']}), evidence)
        evidence['findings'][0]['subject'] = 'arbitrary-service'
        with self.assertRaises(contract.DiagnosticError):
            contract.validate_evidence(evidence)


class ComparisonTests(unittest.TestCase):
    def test_runtime_status_change_is_bounded_without_identity_or_recovery_claim(self):
        before = snapshots()
        before['local_platform_status']['ready'] = False
        before['local_platform_status']['components']['superset'] = {'ready': False, 'status': 'not-ready'}
        after = reread(before, 'local_platform_status')
        after['local_platform_status']['ready'] = True
        after['local_platform_status']['components']['superset'] = {'ready': True, 'status': 'ready'}
        result = contract.compare_observations(before, after)['sources']['local_platform_status']
        self.assertEqual(result['status'], 'changed')
        self.assertFalse(result['before_ready'])
        self.assertTrue(result['after_ready'])
        self.assertEqual(result['changes'], [{'kind': 'runtime-status-changed', 'subject': 'superset',
            'before': {'ready': False, 'status': 'not-ready'}, 'after': {'ready': True, 'status': 'ready'}}])
        self.assertIn('component-identity-not-observed', result['limitations'])
        self.assertIn('no-recovery-claim', result['limitations'])
        self.assertEqual(contract.compare_observations(before, {})['sources']['local_platform_status']['status'], 'not-reobserved')

    def test_runtime_unknown_or_missing_reversed_time_cannot_claim_no_change(self):
        before = snapshots()
        after = reread(before, 'local_platform_status')
        self.assertEqual(contract.compare_observations(before, after)['sources']['local_platform_status']['status'], 'no-change-observed')
        for stamp in (None, '2026-09-17T01:59:59Z'):
            after['local_platform_status']['observed_at'] = stamp
            result = contract.compare_observations(before, after)['sources']['local_platform_status']
            self.assertEqual(result['status'], 'incomparable')
        before['local_platform_status']['ready'] = False
        before['local_platform_status']['components']['loki'] = {'ready': False, 'status': 'unknown'}
        after = reread(before, 'local_platform_status')
        result = contract.compare_observations(before, after)['sources']['local_platform_status']
        self.assertEqual(result['status'], 'incomparable')
        self.assertIn('unknown-runtime-status', result['limitations'])

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


def container_state(kind='running', reason=None, exit_code=None):
    stamp = '2026-09-17T01:00:00.000000Z'
    return dict(kind=kind, reason=reason, exit_code=exit_code,
                started_at=stamp if kind in ('running', 'terminated') else None,
                finished_at=stamp if kind == 'terminated' else None)


def container_details(state=None, *, prior=None, status='complete', restarts=0):
    state = state or container_state()
    return {'status': status, 'scope': 'application-containers', 'containers': [
        dict(name='application', ready=state['kind'] == 'running', restarts=restarts,
             state=state, last_termination=prior)]}


class ContainerEvidenceTests(unittest.TestCase):
    def data(self, details):
        data = observations()
        pod = data[1]['pods'][0]
        pod['container_observation'] = details
        if details['containers'] and not details['containers'][0]['ready']:
            pod['ready_containers'] = 0
        return data

    def test_legacy_missing_details_explicitly_not_observed_and_old_saved_evidence_valid(self):
        evidence = contract.build_evidence(*observations())
        self.assertEqual(evidence['observations']['pods']['container_details'],
            dict(scope='application-containers', complete=0, truncated=0, unknown=0, not_observed=1))
        self.assertEqual(evidence['health'], 'healthy-observed-scope')
        del evidence['observations']['pods']['container_details']
        contract.validate_evidence(evidence)
        retained = contract.project_observations(snapshots())
        self.assertNotIn('container_observation', retained['k8s_read_pods']['pods'][0])

    def test_exit137_without_reported_oom_is_nonzero_only_with_exact_current_context(self):
        details = container_details(container_state('terminated', 'Error', 137))
        evidence = contract.build_evidence(*self.data(details))
        finding = next(x for x in evidence['findings'] if x['code'] == 'CONTAINER_EXIT_NONZERO')
        self.assertEqual(finding['severity'], 'critical')
        self.assertEqual(finding['subject'], 'seaweedfs-0')
        self.assertEqual(finding['container'], dict(name='application', observation='current', status='complete',
            ready=False, restarts=0, state=details['containers'][0]['state']))
        self.assertFalse(any('OOM' in x['code'] for x in evidence['findings']))

    def test_reported_oom_uses_reason_not_exit_code_and_no_inferred_cause(self):
        for code in (137, 1, 0):
            evidence = contract.build_evidence(*self.data(container_details(container_state('terminated', 'OOMKilled', code))))
            findings = [x for x in evidence['findings'] if x['code'].startswith('CONTAINER_')]
            self.assertEqual([x['code'] for x in findings], ['CONTAINER_OOM_REPORTED'])
            reads = {'tool': 'k8s_read_pods', 'finding_ids': [x['id'] for x in evidence['findings'] if x['severity'] == 'critical']}
            contract.validate_triage(triage(evidence, reads), evidence)
            with self.assertRaises(contract.DiagnosticError):
                contract.validate_triage(dict(triage(evidence, reads), root_cause='memory-limit'), evidence)

    def test_image_pull_and_crashloop_wait_are_current_critical_other_waits_not_invented(self):
        for reason, expected in [('ErrImagePull', 'CONTAINER_IMAGE_PULL_WAIT'),
                                 ('ImagePullBackOff', 'CONTAINER_IMAGE_PULL_WAIT'),
                                 ('CrashLoopBackOff', 'CONTAINER_BACKOFF_WAIT'),
                                 ('ContainerCreating', None)]:
            evidence = contract.build_evidence(*self.data(container_details(container_state('waiting', reason))))
            findings = [x for x in evidence['findings'] if 'container' in x]
            self.assertEqual([x['code'] for x in findings], [expected] if expected else [])

    def test_prior_termination_only_is_warning_with_running_current_not_current_failure(self):
        for reason, expected in [('Error', 'CONTAINER_PREVIOUS_EXIT_NONZERO'), ('OOMKilled', 'CONTAINER_PREVIOUS_OOM_REPORTED')]:
            details = container_details(prior=container_state('terminated', reason, 137), restarts=2)
            evidence = contract.build_evidence(*self.data(details))
            self.assertEqual([(x['code'], x['severity']) for x in evidence['findings']], [(expected, 'warning')])
            self.assertEqual(evidence['findings'][0]['container']['observation'], 'last-termination')

    def test_current_and_prior_different_facts_remain_separate_and_all_current_critical_mandatory(self):
        details = container_details(container_state('waiting', 'CrashLoopBackOff'),
                                    prior=container_state('terminated', 'OOMKilled', 137), restarts=3)
        evidence = contract.build_evidence(*self.data(details))
        self.assertIn('CONTAINER_PREVIOUS_OOM_REPORTED', [x['code'] for x in evidence['findings']])
        readiness = [x['id'] for x in evidence['findings'] if x['code'] == 'WORKLOAD_NOT_READY']
        with self.assertRaisesRegex(contract.DiagnosticError, 'critical'):
            contract.validate_triage(triage(evidence, {'tool': 'k8s_read_pods', 'finding_ids': readiness}), evidence)
        ids = [x['id'] for x in evidence['findings'] if x['severity'] == 'critical']
        contract.validate_triage(triage(evidence, {'tool': 'k8s_read_pods', 'finding_ids': ids}), evidence)

    def test_unknown_and_truncated_details_do_not_assert_health_or_missing_states(self):
        for status in ('unknown', 'truncated'):
            details = container_details(status=status)
            if status == 'unknown':
                details['containers'] = []
            else:
                details['containers'] = [dict(copy.deepcopy(details['containers'][0]), name=f'app-{i}') for i in range(8)]
            evidence = contract.build_evidence(*self.data(details))
            self.assertEqual(evidence['health'], 'unknown')
            self.assertEqual([x['code'] for x in evidence['findings']], ['CONTAINER_OBSERVATION_' + status.upper()])
            self.assertEqual(evidence['observations']['pods']['container_details'][status], 1)
            evidence['findings'] = []
            evidence['health'] = 'healthy-observed-scope'
            with self.assertRaises(contract.DiagnosticError):
                contract.validate_evidence(evidence)

    def test_unknown_reason_keeps_nonzero_fact_without_guessing_cause(self):
        evidence = contract.build_evidence(*self.data(container_details(container_state('terminated', 'unknown', 1), status='unknown')))
        self.assertEqual({x['code'] for x in evidence['findings']},
            {'CONTAINER_EXIT_NONZERO', 'CONTAINER_OBSERVATION_UNKNOWN', 'WORKLOAD_NOT_READY'})

    def test_malformed_or_raw_container_details_fail_before_model_projection(self):
        changes = [lambda x: x.update(message='private message'),
                   lambda x: x['containers'][0].update(container_id='private runtime ID'),
                   lambda x: x['containers'][0]['state'].update(reason='private unknown reason'),
                   lambda x: x['containers'][0].update(restarts=True),
                   lambda x: x.update(status='truncated')]
        for mutate in changes:
            details = container_details()
            mutate(details)
            data = self.data(details)
            for operation in (lambda: contract.build_evidence(*data),
                              lambda: contract.project_observations(dict(zip(contract.TOOLS, data)))):
                with self.assertRaises(contract.DiagnosticError) as error:
                    operation()
                self.assertNotIn('private', str(error.exception))

    def test_context_cannot_relabel_history_severity_exit_or_oom_and_must_be_exact(self):
        evidence = contract.build_evidence(*self.data(container_details(container_state('terminated', 'Error', 137))))
        index = next(i for i, x in enumerate(evidence['findings']) if 'container' in x)
        changes = [lambda x: x['container'].update(observation='last-termination'),
                   lambda x: x['container']['state'].update(reason='OOMKilled'),
                   lambda x: x['container']['state'].update(exit_code=0),
                   lambda x: x['container'].update(raw_message='private'),
                   lambda x: x.update(severity='warning'), lambda x: x.pop('container')]
        for mutate in changes:
            changed = copy.deepcopy(evidence)
            mutate(changed['findings'][index])
            with self.assertRaises(contract.DiagnosticError):
                contract.validate_evidence(changed)

    def test_full_container_projection_replays_without_aliasing_input(self):
        data = self.data(container_details(container_state('waiting', 'CrashLoopBackOff'),
                        prior=container_state('terminated', 'Error', 1), restarts=4))
        raw = dict(zip(contract.TOOLS, data))
        projected = contract.project_observations(raw)
        self.assertEqual(contract.build_evidence(*data), contract.build_evidence(*(projected[t] for t in contract.TOOLS)))
        projected['k8s_read_pods']['pods'][0]['container_observation']['containers'][0]['restarts'] = 9
        self.assertEqual(data[1]['pods'][0]['container_observation']['containers'][0]['restarts'], 4)


class ContainerComparisonTests(unittest.TestCase):
    def before(self):
        before = snapshots()
        before['k8s_read_pods']['pods'][0]['container_observation'] = container_details(
            container_state('waiting', 'CrashLoopBackOff'), prior=container_state('terminated', 'Error', 1), restarts=4)
        return before

    def test_same_uid_name_preserves_state_history_and_restart_decrease_without_recovery(self):
        before = self.before()
        after = reread(before, 'k8s_read_pods')
        after['k8s_read_pods']['pods'][0]['container_observation'] = container_details(restarts=1)
        result = contract.compare_observations(before, after)
        observed = result['sources']['k8s_read_pods']
        self.assertEqual(observed['status'], 'changed')
        self.assertEqual(observed['changes'][0]['kind'], 'container-fields-changed')
        self.assertEqual(observed['changes'][0]['changed_fields'], ['ready', 'restarts', 'state', 'last_termination'])
        self.assertIn('container-restart-count-decreased', observed['limitations'])
        self.assertIn('no-recovery-claim', observed['limitations'])
        self.assertEqual(result['root_cause'], 'undetermined')
        self.assertEqual(result, contract.compare_observations(contract.project_observations(before), contract.project_observations(after)))

    def test_replaced_pod_does_not_compare_same_named_container_history(self):
        before = self.before()
        after = reread(before, 'k8s_read_pods')
        after['k8s_read_pods']['pods'][0].update(uid='new-pod', container_observation=container_details())
        observed = contract.compare_observations(before, after)['sources']['k8s_read_pods']
        self.assertEqual([x['kind'] for x in observed['changes']], ['pod-replaced'])

    def test_incomplete_equal_detail_lists_cannot_claim_no_change(self):
        before = self.before()
        before['k8s_read_pods']['pods'][0]['container_observation'] = dict(status='unknown', scope='application-containers', containers=[])
        after = reread(before, 'k8s_read_pods')
        observed = contract.compare_observations(before, after)['sources']['k8s_read_pods']
        self.assertEqual(observed['status'], 'incomparable')
        self.assertIn('incomplete-container-details', observed['limitations'])

    def test_unknown_to_complete_records_new_observation_without_recovery_claim(self):
        before = self.before()
        before['k8s_read_pods']['pods'][0]['container_observation'] = dict(status='unknown', scope='application-containers', containers=[])
        after = reread(before, 'k8s_read_pods')
        after['k8s_read_pods']['pods'][0]['container_observation'] = container_details()
        observed = contract.compare_observations(before, after)['sources']['k8s_read_pods']
        self.assertEqual([x['kind'] for x in observed['changes']],
                         ['container-observation-status-changed', 'container-first-observed'])
        self.assertIn('no-recovery-claim', observed['limitations'])


if __name__ == '__main__':
    unittest.main()
