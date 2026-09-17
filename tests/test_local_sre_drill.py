from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

HERE = Path(__file__).resolve().parents[1] / 'tools/local_experiment/bears'
sys.path.insert(0, str(HERE))
import drill_sre as D

RUN_ID = 'a' * 32


def deployment():
    return {'apiVersion': 'apps/v1', 'kind': 'Deployment',
            'metadata': {'name': 'trino-local', 'namespace': D.NAMESPACE},
            'spec': {'template': {'spec': {'securityContext': {'runAsUser': 10000}, 'containers': [
                {'name': 'trino', 'image': 'shirokuma.local/trino@sha256:' + 'a' * 64}]}}}}


def config():
    value = json.loads((HERE.parent / 'llm/providers.example.json').read_text())
    value['active_provider'] = 'local'
    value['providers']['local'].update(enabled=True, model='qwen3.8-flash-next')
    return value


def created_pod(run_id=RUN_ID):
    value = D.manifest(deployment(), run_id)
    value['metadata'].update(uid='owned-pod-uid', resourceVersion='10')
    value['status'] = {'phase': 'Failed', 'containerStatuses': [
        {'name': 'fail-once', 'state': {'terminated': {'exitCode': 1}}}]}
    return value


def receipt(run_id=RUN_ID):
    return {'run_id': run_id, 'name': D.identity(run_id)['name'], 'namespace': D.NAMESPACE, 'uid': 'owned-pod-uid'}


def container_observation():
    return {'status': 'complete', 'scope': 'application-containers', 'containers': [{
        'name': 'fail-once', 'ready': False, 'restarts': 0,
        'state': {'kind': 'terminated', 'reason': 'Error', 'exit_code': 1,
                  'started_at': '2026-09-17T02:59:59.000000Z',
                  'finished_at': '2026-09-17T03:00:00.000000Z'},
        'last_termination': None}]}


def proof(creation):
    stamp = '2026-09-17T03:00:00.000000Z'
    initial = {
        'shirokuma_doctor': {'profile': 'local-experiment', 'context': D.CONTEXT, 'observed_at': stamp,
            'checks': [{'name': 'cluster', 'status': 'healthy'}]},
        'k8s_read_pods': {'namespace': D.NAMESPACE, 'observed_at': stamp, 'truncated': False, 'pods': [
            {'name': creation['name'], 'uid': creation['uid'], 'phase': 'Failed',
             'ready_containers': 0, 'total_containers': 1, 'restarts': 0}]},
        'k8s_read_events': {'namespace': D.NAMESPACE, 'observed_at': stamp, 'truncated': False, 'events': []},
        'local_platform_status': {'profile': 'local-experiment', 'observed_at': stamp, 'ready': True,
            'components': {name: {'ready': True, 'status': 'ready'} for name in
                ('lakehouse', 'superset', 'metadata', 'agenthouse', 'loki')}}}
    initial['k8s_read_pods']['pods'][0]['container_observation'] = container_observation()
    evidence = D.build_evidence(initial['shirokuma_doctor'], initial['k8s_read_pods'],
                                initial['k8s_read_events'], initial['local_platform_status'])
    after = copy.deepcopy(initial['k8s_read_pods'])
    after['observed_at'] = '2026-09-17T03:00:01.000000Z'
    observations = [{'tool': tool, 'phase': 'initial', 'snapshot': snapshot} for tool, snapshot in initial.items()]
    observations.append({'tool': 'k8s_read_pods', 'phase': 'followup', 'snapshot': after})
    report = {'result': 'passed', 'checkpoint': 'completed-in-memory', 'observations_file': 'observations.json',
        'task_id': 'workflow-' + 'b' * 32, 'trace_id': 'c' * 32,
        'evidence': evidence,
        'triage': {'checks': [{'tool': 'k8s_read_pods',
                              'finding_ids': [item['id'] for item in evidence['findings'] if item['severity'] == 'critical']}]},
        'initial_sha256': hashlib.sha256(json.dumps(initial, sort_keys=True).encode()).hexdigest(),
        'followup_sha256': {'k8s_read_pods': hashlib.sha256(json.dumps(after, sort_keys=True).encode()).hexdigest()},
        'comparisons': {'sources': {'k8s_read_pods': {'status': 'no-change-observed'}}}}
    return report, observations


class FakeKubernetes:
    def __init__(self, output=None, *, uncertain_create=False, delete_conflict=False):
        self.output = output
        self.pod = None
        self.created = []
        self.deleted = []
        self.uncertain_create = uncertain_create
        self.delete_conflict = delete_conflict

    def deployment(self):
        return deployment()

    def create(self, pod):
        self.created.append(copy.deepcopy(pod))
        self.pod = created_pod(pod['metadata']['labels'][D.RUN_LABEL])
        if self.uncertain_create:
            raise D.DrillError('private-create-request-error')
        return copy.deepcopy(self.pod)

    def get(self, name):
        if self.output is not None:
            assert (self.output / 'created.json').is_file(), 'read preceded durable creation receipt'
        if self.pod is not None:
            assert name == self.pod['metadata']['name']
        return copy.deepcopy(self.pod)

    def delete(self, creation, current):
        D.owned(current, creation)
        self.deleted.append((copy.deepcopy(creation), copy.deepcopy(current)))
        if self.delete_conflict:
            raise D.DrillError('simulated API UID/resourceVersion conflict')
        self.pod = None


async def fake_sre(config_value, output):
    creation = json.loads((output.parent / 'created.json').read_text())
    report, observations = proof(creation)
    output.mkdir()
    D.private_write(output / 'observations.json', observations)
    return report


class SREDrillTests(unittest.TestCase):
    def test_manifest_reuses_fixed_local_image_without_credentials_volumes_or_privilege(self):
        pod = D.manifest(deployment(), RUN_ID)
        spec, container = pod['spec'], pod['spec']['containers'][0]
        self.assertEqual(pod['metadata'], D.identity(RUN_ID))
        self.assertEqual(container['image'], 'shirokuma.local/trino@sha256:' + 'a' * 64)
        self.assertEqual(container['imagePullPolicy'], 'Never')
        self.assertEqual(container['command'], ['/bin/sh', '-c', 'exit 1'])
        self.assertEqual(spec['restartPolicy'], 'Never')
        self.assertEqual(spec['activeDeadlineSeconds'], 60)
        self.assertFalse(spec['automountServiceAccountToken'])
        self.assertFalse(spec['enableServiceLinks'])
        self.assertEqual(spec['securityContext']['runAsUser'], 10000)
        self.assertTrue(spec['securityContext']['runAsNonRoot'])
        self.assertTrue(container['securityContext']['readOnlyRootFilesystem'])
        self.assertFalse(container['securityContext']['allowPrivilegeEscalation'])
        self.assertEqual(container['securityContext']['capabilities']['drop'], ['ALL'])
        self.assertEqual(container['resources']['limits'], {'cpu': '50m', 'memory': '32Mi'})
        for field in ('volumes', 'hostNetwork', 'hostPID', 'hostIPC', 'nodeName'):
            self.assertNotIn(field, spec)
        for field in ('env', 'envFrom', 'volumeMounts', 'ports'):
            self.assertNotIn(field, container)

    def test_unpinned_external_image_root_uid_and_wrong_deployment_are_rejected(self):
        for image in ('shirokuma.local/trino:latest', 'trino:483', 'external/trino@sha256:' + 'a' * 64):
            value = deployment()
            value['spec']['template']['spec']['containers'][0]['image'] = image
            with self.assertRaises(D.DrillError):
                D.manifest(value, RUN_ID)
        for uid in (0, -1, True, None, '10000'):
            value = deployment()
            value['spec']['template']['spec']['securityContext']['runAsUser'] = uid
            with self.assertRaises(D.DrillError):
                D.manifest(value, RUN_ID)
        value = deployment()
        value['metadata']['namespace'] = 'kube-system'
        with self.assertRaises(D.DrillError):
            D.manifest(value, RUN_ID)

    def test_backend_checks_owned_local_context_before_any_command(self):
        kube = D.Kubernetes.__new__(D.Kubernetes)
        kube.scope = Mock()
        kube.scope.check_context.side_effect = RuntimeError('not local')
        with patch.object(D.subprocess, 'run') as command:
            with self.assertRaises(RuntimeError):
                kube.deployment()
            command.assert_not_called()
        kube.scope.check_context.side_effect = None
        with patch.object(D.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='{}')) as command:
            kube.deployment()
        self.assertEqual(command.call_args.args[0][:8],
            ['rtk', 'proxy', 'kubectl', '--context', D.CONTEXT, '--request-timeout=10s', '-n', D.NAMESPACE])

    def test_delete_sends_api_uid_and_current_resource_version_preconditions(self):
        kube = D.Kubernetes.__new__(D.Kubernetes)
        kube.request = Mock(return_value={'status': 'Success'})
        kube.delete(receipt(), created_pod())
        args, body = kube.request.call_args.args
        self.assertEqual(args, ['delete', '--raw=/api/v1/namespaces/shirokuma-dev/pods/' + receipt()['name'], '-f', '-'])
        self.assertEqual(body['kind'], 'DeleteOptions')
        self.assertEqual(body['preconditions'], {'uid': 'owned-pod-uid', 'resourceVersion': '10'})
        self.assertEqual(body['gracePeriodSeconds'], 1)
        self.assertNotIn('--force', args)

    def test_cleanup_never_deletes_replacement_changed_labels_or_invalid_version(self):
        for mutate in (lambda p: p['metadata'].update(uid='retained-replacement'),
                       lambda p: p['metadata']['labels'].update({D.RUN_LABEL: 'b' * 32}),
                       lambda p: p['metadata'].update(namespace='other-namespace')):
            kube = FakeKubernetes()
            kube.pod = created_pod()
            mutate(kube.pod)
            result = asyncio.run(D.cleanup(kube, receipt()))
            self.assertEqual(result['status'], 'deferred')
            self.assertEqual(kube.deleted, [])
            self.assertIsNotNone(kube.pod)
        kube = D.Kubernetes.__new__(D.Kubernetes)
        kube.request = Mock()
        pod = created_pod()
        pod['metadata'].pop('resourceVersion')
        with self.assertRaises(D.DrillError):
            kube.delete(receipt(), pod)
        kube.request.assert_not_called()

    def test_cleanup_api_conflict_is_deferred_without_retry(self):
        kube = FakeKubernetes(delete_conflict=True)
        kube.pod = created_pod()
        result = asyncio.run(D.cleanup(kube, receipt()))
        self.assertEqual(result['status'], 'deferred')
        self.assertEqual(len(kube.deleted), 1)
        self.assertIsNotNone(kube.pod)

    def test_failed_pod_requires_intended_exit_one_and_same_uid(self):
        for code in (0, True, 137, None):
            kube = FakeKubernetes()
            kube.pod = created_pod()
            kube.pod['status']['containerStatuses'][0]['state']['terminated']['exitCode'] = code
            with self.assertRaises(D.DrillError):
                asyncio.run(D.wait_failed(kube, receipt()))
        kube.pod = created_pod()
        kube.pod['metadata']['uid'] = 'different-pod'
        with self.assertRaises(D.DrillError):
            asyncio.run(D.wait_failed(kube, receipt()))

    def test_failed_state_wait_is_bounded(self):
        kube = FakeKubernetes()
        kube.pod = created_pod()
        kube.pod['status'] = {'phase': 'Pending'}
        with self.assertRaises(D.DrillError):
            asyncio.run(D.wait_failed(kube, receipt(), clock=Mock(side_effect=[0, 91]), pause=AsyncMock()))

    def test_verified_sre_detection_requires_same_uid_and_saved_snapshot_hashes(self):
        report, observations = proof(receipt())
        self.assertEqual(D.verify_detection(report, observations, receipt())['pod_uid'], receipt()['uid'])
        for mutation in ('uid', 'phase', 'hash', 'critical', 'selection', 'audit_result'):
            current_report, current_observations = copy.deepcopy((report, observations))
            after = current_observations[-1]['snapshot']
            if mutation == 'uid':
                after['pods'][0]['uid'] = 'replacement'
                current_report['followup_sha256']['k8s_read_pods'] = hashlib.sha256(json.dumps(after, sort_keys=True).encode()).hexdigest()
            elif mutation == 'phase':
                after['pods'][0]['phase'] = 'Running'
                current_report['followup_sha256']['k8s_read_pods'] = hashlib.sha256(json.dumps(after, sort_keys=True).encode()).hexdigest()
            elif mutation == 'hash':
                current_report['initial_sha256'] = '0' * 64
            elif mutation == 'critical':
                current_report['evidence']['findings'] = []
            elif mutation == 'selection':
                current_report['triage']['checks'] = []
            else:
                current_report['result'] = 'failed'
            with self.subTest(mutation=mutation), self.assertRaises(D.DrillError):
                D.verify_detection(current_report, current_observations, receipt())

    def test_success_saves_uid_before_observation_and_cleans_only_created_pod(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'drill'
            kube = FakeKubernetes(output)
            result = asyncio.run(D.run_drill(config(), output, kube=kube, runner=fake_sre))
            creation = json.loads((output / 'created.json').read_text())
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((output / 'created.json').stat().st_mode), 0o600)
            self.assertEqual(json.loads((output / 'summary.json').read_text()), result)
        self.assertEqual(result['result'], 'passed')
        self.assertEqual(result['cleanup']['status'], 'deleted')
        self.assertEqual(len(kube.created), 1)
        self.assertEqual(len(kube.deleted), 1)
        self.assertEqual(kube.deleted[0][0]['uid'], creation['uid'])
        self.assertIsNone(kube.pod)

    def test_detection_rejects_incomplete_historical_or_changed_container_evidence(self):
        report, observations = proof(receipt())
        for mutation in ('missing', 'incomplete', 'history_only', 'exit_code', 'boolean_exit',
                         'reason', 'container_name', 'timestamp', 'restarted'):
            current, snapshots = copy.deepcopy((report, observations))
            after = snapshots[-1]['snapshot']
            pod = after['pods'][0]
            detail = pod['container_observation']
            container = detail['containers'][0]
            if mutation == 'missing':
                del pod['container_observation']
            elif mutation == 'incomplete':
                detail['status'] = 'unknown'
            elif mutation == 'history_only':
                container['last_termination'] = container['state']
                container['state'] = {'kind': 'running', 'reason': None, 'exit_code': None,
                                      'started_at': '2026-09-17T03:00:01.000000Z', 'finished_at': None}
            elif mutation in {'exit_code', 'boolean_exit'}:
                container['state']['exit_code'] = 7 if mutation == 'exit_code' else True
            elif mutation == 'reason':
                container['state']['reason'] = 'OOMKilled'
            elif mutation == 'container_name':
                container['name'] = 'other'
            elif mutation == 'timestamp':
                container['state']['finished_at'] = '2026-09-17T03:00:00.500000Z'
            else:
                container['restarts'] = 1
            current['followup_sha256']['k8s_read_pods'] = hashlib.sha256(
                json.dumps(after, sort_keys=True).encode()).hexdigest()
            with self.subTest(mutation=mutation), self.assertRaises(D.DrillError):
                D.verify_detection(current, snapshots, receipt())

    def test_detection_requires_the_container_finding_in_model_read_selection(self):
        report, observations = proof(receipt())
        finding = next(item for item in report['evidence']['findings']
                       if item['code'] == 'CONTAINER_EXIT_NONZERO')
        report['triage']['checks'][0]['finding_ids'].remove(finding['id'])
        with self.assertRaises(D.DrillError):
            D.verify_detection(report, observations, receipt())

    def test_detection_binds_model_finding_to_saved_current_termination(self):
        report, observations = proof(receipt())
        result = D.verify_detection(report, observations, receipt())
        self.assertEqual(result['container_name'], 'fail-once')
        self.assertEqual(result['container_state'], container_observation()['containers'][0]['state'])
        changed = copy.deepcopy(report)
        finding = next(item for item in changed['evidence']['findings']
                       if item['code'] == 'CONTAINER_EXIT_NONZERO')
        finding['code'] = 'CONTAINER_OOM_REPORTED'
        with self.assertRaises(D.DrillError):
            D.verify_detection(changed, observations, receipt())

    def test_uncertain_create_never_acquires_cleanup_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'drill'
            kube, runner = FakeKubernetes(uncertain_create=True), AsyncMock()
            result = asyncio.run(D.run_drill(config(), output, kube=kube, runner=runner))
            self.assertTrue((output / 'creation-intent.json').is_file())
            self.assertFalse((output / 'created.json').exists())
        self.assertEqual(result['result'], 'failed')
        self.assertEqual(result['cleanup']['status'], 'deferred')
        self.assertEqual(kube.deleted, [])
        self.assertIsNotNone(kube.pod)
        runner.assert_not_awaited()
        self.assertNotIn('private-create-request-error', json.dumps(result))

    def test_receipt_write_failure_defers_cleanup_and_workflow(self):
        original_write = D.private_write
        def fail_receipt(path, value):
            if path.name == 'created.json':
                raise OSError('private-storage-failure')
            original_write(path, value)
        with tempfile.TemporaryDirectory() as directory, patch.object(D, 'private_write', side_effect=fail_receipt):
            kube, runner = FakeKubernetes(), AsyncMock()
            result = asyncio.run(D.run_drill(config(), Path(directory) / 'drill', kube=kube, runner=runner))
        self.assertEqual(result['cleanup']['status'], 'deferred')
        self.assertEqual(result['failure_stage'], 'persist_creation_receipt')
        self.assertEqual(kube.deleted, [])
        runner.assert_not_awaited()

    def test_workflow_failure_still_cleans_owned_pod_but_cannot_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            kube = FakeKubernetes()
            runner = AsyncMock(side_effect=RuntimeError('private-workflow-error'))
            result = asyncio.run(D.run_drill(config(), Path(directory) / 'drill', kube=kube, runner=runner))
        self.assertEqual(result['result'], 'failed')
        self.assertEqual(result['failure_stage'], 'sre_workflow')
        self.assertEqual(result['cleanup']['status'], 'deleted')
        self.assertEqual(len(kube.deleted), 1)
        self.assertNotIn('private-workflow-error', json.dumps(result))

    def test_mock_or_cloud_config_is_rejected_before_pod_creation(self):
        value = json.loads((HERE.parent / 'llm/providers.example.json').read_text())
        for active in ('mock', 'cloud'):
            value['active_provider'] = active
            kube = Mock()
            with tempfile.TemporaryDirectory() as directory, self.assertRaises((D.DrillError, RuntimeError)):
                asyncio.run(D.run_drill(value, Path(directory) / 'drill', kube=kube))
            kube.deployment.assert_not_called()
            kube.create.assert_not_called()


if __name__ == '__main__':
    unittest.main()
