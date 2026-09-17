"""Container facts preserve unknowns and exclude raw Kubernetes content."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('container_test_gateway', ROOT / 'tools/local_experiment/gateway/core.py')
GATEWAY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATEWAY)
C = GATEWAY.CONTAINER_STATUS
START = '2026-09-17T03:00:00.000000Z'
END = '2026-09-17T03:00:01.000000Z'


def pod(state=None, *, ready=False, last=None):
    row = {'name': 'app', 'ready': ready, 'restartCount': 0,
           'state': state if state is not None else {'terminated': {'reason': 'Error', 'exitCode': 7,
                     'startedAt': START, 'finishedAt': END}}}
    if last is not None:
        row['lastState'] = last
    return {'metadata': {'name': 'pod', 'uid': 'known-pod'},
            'spec': {'containers': [{'name': 'app'}]},
            'status': {'phase': 'Failed', 'containerStatuses': [row]}}


def project(value):
    return C.project_container_observation(value, timestamp=GATEWAY.safe_timestamp)


class ContainerStatusTests(unittest.TestCase):
    def test_terminated_facts_keep_exit_and_times_with_fixed_shape_without_mutating_input(self):
        value = pod()
        before = copy.deepcopy(value)
        result = project(value)
        self.assertEqual(value, before)
        self.assertEqual(result, {'scope': 'application-containers', 'status': 'complete', 'containers': [{
            'name': 'app', 'ready': False, 'restarts': 0, 'last_termination': None,
            'state': {'kind': 'terminated', 'reason': 'Error', 'exit_code': 7,
                      'started_at': START, 'finished_at': END}}]})
        self.assertEqual(C.validate_container_observation(result), result)

    def test_running_and_waiting_have_no_borrowed_termination_fields(self):
        running = project(pod({'running': {'startedAt': '2026-09-17T12:00:00.1+09:00'}}, ready=True))
        self.assertEqual(running['status'], 'complete')
        self.assertEqual(running['containers'][0]['state'], {'kind': 'running', 'reason': None,
            'exit_code': None, 'started_at': '2026-09-17T03:00:00.100000Z', 'finished_at': None})
        for reason in sorted(C.WAITING_REASONS):
            waiting = project(pod({'waiting': {'reason': reason, 'message': 'PRIVATE MESSAGE'}}))
            self.assertEqual(waiting['status'], 'complete')
            self.assertEqual(waiting['containers'][0]['state'], {'kind': 'waiting', 'reason': reason,
                'exit_code': None, 'started_at': None, 'finished_at': None})

    def test_zero_is_known_and_optional_unreported_fields_stay_null(self):
        result = project(pod({'terminated': {'exitCode': 0, 'reason': 'Completed'}}))
        self.assertEqual(result['status'], 'complete')
        state = result['containers'][0]['state']
        self.assertEqual(state['exit_code'], 0)
        self.assertIsNone(state['started_at'])
        self.assertIsNone(state['finished_at'])
        self.assertEqual(result['containers'][0]['restarts'], 0)

    def test_previous_oom_is_separate_from_current_running(self):
        result = project(pod({'running': {'startedAt': END}}, ready=True,
            last={'terminated': {'exitCode': 137, 'reason': 'OOMKilled', 'finishedAt': START}}))
        self.assertEqual(result['status'], 'complete')
        row = result['containers'][0]
        self.assertEqual(row['state']['kind'], 'running')
        self.assertIsNone(row['state']['reason'])
        self.assertEqual(row['last_termination']['kind'], 'terminated')
        self.assertEqual(row['last_termination']['reason'], 'OOMKilled')
        self.assertEqual(row['last_termination']['exit_code'], 137)

    def test_unknown_reason_never_retains_raw_text_or_claims_complete(self):
        for kind in ('waiting', 'terminated'):
            for reason in ('PRIVATE-CUSTOM-REASON', {'token': 'PRIVATE'}, 1, True, []):
                raw = {'reason': reason, **({'exitCode': 1} if kind == 'terminated' else {})}
                result = project(pod({kind: raw}))
                self.assertEqual(result['status'], 'unknown')
                self.assertEqual(result['containers'][0]['state']['reason'], 'unknown')
                self.assertNotIn('PRIVATE', json.dumps(result))

    def test_missing_empty_or_ambiguous_current_state_remains_unknown(self):
        for state in (None, {}, [], {'running': {}}, {'waiting': {}},
                      {'waiting': {'reason': 'CrashLoopBackOff'}, 'running': {'startedAt': START}},
                      {'other': {'PRIVATE': 'VALUE'}}):
            value = pod()
            value['status']['containerStatuses'][0]['state'] = state
            result = project(value)
            self.assertEqual(result['status'], 'unknown')
            self.assertEqual(result['containers'][0]['state'], C._unknown_state())

    def test_missing_specs_or_status_names_do_not_invent_empty_complete_observation(self):
        values = [None, {}, {'spec': {}, 'status': {}}, pod()]
        values[-1]['status'].pop('containerStatuses')
        for section, field in (('spec', 'containers'), ('status', 'containerStatuses')):
            for replacement in (None, [], {}, [{'name': 'other'}], [{'name': 'PRIVATE name'}],
                                [{'name': 'app'}, {'name': 'app'}], ['PRIVATE']):
                value = pod()
                value[section][field] = replacement
                values.append(value)
        for value in values:
            result = project(value)
            self.assertEqual(result, {'status': 'unknown', 'scope': 'application-containers', 'containers': []})

    def test_nullable_invalid_types_do_not_turn_into_zero_and_inconsistent_ready_is_unknown(self):
        for field, values in (('ready', (1, 'true', None)),
                              ('restartCount', (True, -1, 2**31, '2', 2.0, None))):
            for bad in values:
                value = pod()
                value['status']['containerStatuses'][0][field] = bad
                result = project(value)
                self.assertEqual(result['status'], 'unknown')
                self.assertIsNone(result['containers'][0]['ready' if field == 'ready' else 'restarts'])
        for bad in (True, 1.0, '7', 2**31, -(2**31)-1, None):
            result = project(pod({'terminated': {'exitCode': bad, 'reason': 'Error'}}))
            self.assertEqual(result['status'], 'unknown')
            self.assertIsNone(result['containers'][0]['state']['exit_code'])
        self.assertEqual(project(pod(ready=True))['status'], 'unknown')

    def test_malformed_or_nonterminated_history_cannot_be_complete(self):
        for last in (None, [], {'running': {'startedAt': START}}, {'terminated': {}},
                     {'terminated': {'exitCode': 0}, 'waiting': {'reason': 'ContainerCreating'}}):
            value = pod()
            value['status']['containerStatuses'][0]['lastState'] = last
            result = project(value)
            self.assertEqual(result['status'], 'unknown')
            self.assertIsNone(result['containers'][0]['last_termination'])
        value = pod()
        value['status']['containerStatuses'][0]['lastState'] = {}
        self.assertEqual(project(value)['status'], 'complete')

    def test_invalid_or_reversed_timestamps_are_unknown_and_not_raw_text(self):
        for started, finished in (('PRIVATE TIMESTAMP', END), (END, START), (START, '2026-02-30T01:00:00Z')):
            result = project(pod({'terminated': {'exitCode': 7, 'reason': 'Error',
                                                 'startedAt': started, 'finishedAt': finished}}))
            self.assertEqual(result['status'], 'unknown')
            self.assertNotIn('PRIVATE', json.dumps(result))
        result = project(pod({'running': {'startedAt': START}}, ready=True,
                             last={'terminated': {'exitCode': 1, 'finishedAt': END}}))
        self.assertEqual(result['status'], 'unknown')

    def test_more_than_eight_complete_rows_are_truncated_and_invalid_tail_overrides_truncation(self):
        value = pod()
        template = value['status']['containerStatuses'][0]
        names = ['app-' + str(i) for i in reversed(range(9))]
        value['spec']['containers'] = [{'name': name} for name in names]
        value['status']['containerStatuses'] = [{**copy.deepcopy(template), 'name': name} for name in names]
        result = project(value)
        self.assertEqual(result['status'], 'truncated')
        self.assertEqual([row['name'] for row in result['containers']], sorted(names)[:8])
        value['status']['containerStatuses'][0]['state'] = {}
        result = project(value)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(len(result['containers']), 8)

    def test_only_application_states_are_included_and_all_sensitive_fields_are_omitted(self):
        value = pod()
        value['metadata']['annotations'] = {'PRIVATE ANNOTATION': 'PRIVATE'}
        value['spec']['containers'][0].update(env=[{'name': 'PRIVATE_ENV', 'value': 'PRIVATE'}],
            command=['PRIVATE_COMMAND'], image='PRIVATE_IMAGE')
        value['spec']['initContainers'] = [{'name': 'private-init'}]
        value['status']['initContainerStatuses'] = [{'name': 'private-init', 'message': 'PRIVATE'}]
        value['status']['ephemeralContainerStatuses'] = [{'name': 'private-ephemeral'}]
        value['status']['containerStatuses'][0].update(image='PRIVATE_IMAGE', containerID='PRIVATE_ID')
        value['status']['containerStatuses'][0]['state']['terminated'].update(message='PRIVATE_MESSAGE',
            containerID='PRIVATE_ID')
        result = project(value)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(len(result['containers']), 1)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('private-', json.dumps(result))

    def test_host_validator_rejects_raw_fields_invalid_numbers_false_complete_and_duplicates(self):
        good = project(pod())
        edits = [lambda x: x.update(message='PRIVATE'),
                 lambda x: x.update(scope='all-containers'),
                 lambda x: x.update(status='ready'),
                 lambda x: x['containers'][0].update(message='PRIVATE'),
                 lambda x: x['containers'][0].update(restarts=True),
                 lambda x: x['containers'][0].update(ready='true'),
                 lambda x: x['containers'][0]['state'].update(message='PRIVATE'),
                 lambda x: x['containers'][0]['state'].update(exit_code=True),
                 lambda x: x['containers'][0]['state'].update(started_at='2026-09-17T03:00:00Z'),
                 lambda x: x['containers'][0]['state'].update(reason='PRIVATE'),
                 lambda x: x['containers'][0]['state'].update(reason='unknown'),
                 lambda x: x['containers'][0]['state'].update(kind='unknown'),
                 lambda x: x['containers'].append(copy.deepcopy(x['containers'][0])),
                 lambda x: x.update(containers=[]), lambda x: x.update(status='truncated')]
        for edit in edits:
            value = copy.deepcopy(good)
            edit(value)
            with self.assertRaises(C.ContainerObservationError):
                C.validate_container_observation(value)

    def test_gateway_keeps_original_readiness_aggregates_and_adds_bounded_projection(self):
        value = pod({'running': {'startedAt': START}}, ready=True)
        value['status']['phase'] = 'Running'
        value['status']['containerStatuses'][0]['restartCount'] = 2
        backend = GATEWAY.Backends.__new__(GATEWAY.Backends)
        backend.command_json = Mock(return_value={'items': [value]})
        snapshot = backend.k8s_read_pods(GATEWAY.NAMESPACE, 5)
        row = snapshot['pods'][0]
        self.assertEqual({key: item for key, item in row.items() if key != 'container_observation'}, {
            'name': 'pod', 'uid': 'known-pod', 'phase': 'Running',
            'ready_containers': 1, 'total_containers': 1, 'restarts': 2})
        self.assertEqual(row['container_observation'], project(value))
        self.assertEqual(snapshot['namespace'], GATEWAY.NAMESPACE)
        self.assertFalse(snapshot['truncated'])
        self.assertEqual(set(GATEWAY.TOOLS['k8s_read_pods'][2]['properties']['namespace']['enum']), {'shirokuma-dev'})


if __name__ == '__main__':
    unittest.main()
