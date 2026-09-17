#!/usr/bin/env python3
"""Detect one newly created Failed Pod with local SRE inference, then remove its exact UID."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import uuid

from run_sre_llm import run as run_sre, module, TOOLS
from sre_contract import build_evidence
from providers import load_config, validate_config
from evaluation import private_write

HERE = Path(__file__).resolve().parent
LOCAL = HERE.parent
ROOT = HERE.parents[2]
CONTEXT = 'colima-mac-studio-solo'
NAMESPACE = 'shirokuma-dev'
OWNER_LABEL = 'shirokuma.dev/sre-drill'
RUN_LABEL = 'shirokuma.dev/run-id'


class DrillError(RuntimeError):
    pass


def identity(run_id: str) -> dict:
    if not isinstance(run_id, str) or not re.fullmatch(r'[0-9a-f]{32}', run_id):
        raise DrillError('invalid drill run identity')
    return {'name': 'sre-drill-' + run_id, 'namespace': NAMESPACE,
            'labels': {OWNER_LABEL: 'disposable', RUN_LABEL: run_id,
                       'shirokuma.dev/scope': 'local-experiment', 'app.kubernetes.io/part-of': 'shirokuma'}}


def image_identity(deployment: dict) -> tuple[str, int]:
    if (deployment.get('kind') != 'Deployment' or deployment.get('metadata', {}).get('name') != 'trino-local'
            or deployment.get('metadata', {}).get('namespace') != NAMESPACE):
        raise DrillError('image source is not the local Trino Deployment')
    spec = deployment['spec']['template']['spec']
    containers = [item for item in spec['containers'] if item.get('name') == 'trino']
    if len(containers) != 1:
        raise DrillError('expected the local Trino container')
    container = containers[0]
    image = container.get('image')
    if not isinstance(image, str) or not re.fullmatch(r'shirokuma\.local/trino@sha256:[0-9a-f]{64}', image):
        raise DrillError('drill requires the existing fixed local image digest')
    uid = container.get('securityContext', {}).get('runAsUser', spec.get('securityContext', {}).get('runAsUser'))
    if type(uid) is not int or not 0 < uid <= 2**31 - 1:
        raise DrillError('Trino must specify an existing non-root numeric user')
    return image, uid


def manifest(deployment: dict, run_id: str) -> dict:
    image, uid = image_identity(deployment)
    return {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': identity(run_id), 'spec': {
        'automountServiceAccountToken': False, 'enableServiceLinks': False, 'restartPolicy': 'Never',
        'activeDeadlineSeconds': 60, 'terminationGracePeriodSeconds': 1,
        'securityContext': {'runAsNonRoot': True, 'runAsUser': uid, 'seccompProfile': {'type': 'RuntimeDefault'}},
        'containers': [{'name': 'fail-once', 'image': image, 'imagePullPolicy': 'Never',
            'command': ['/bin/sh', '-c', 'exit 1'],
            'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                'capabilities': {'drop': ['ALL']}},
            'resources': {'requests': {'cpu': '50m', 'memory': '32Mi'},
                          'limits': {'cpu': '50m', 'memory': '32Mi'}}}]}}


def owned(pod: dict, receipt: dict, *, require_uid: bool = True) -> dict:
    expected = identity(receipt['run_id'])
    metadata = pod.get('metadata', {})
    if (pod.get('kind') != 'Pod' or metadata.get('name') != expected['name']
            or metadata.get('namespace') != NAMESPACE or receipt.get('name') != expected['name']
            or receipt.get('namespace') != NAMESPACE
            or any(metadata.get('labels', {}).get(key) != value for key, value in expected['labels'].items())):
        raise DrillError('Pod ownership differs from this drill')
    uid = metadata.get('uid')
    if not isinstance(uid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', uid):
        raise DrillError('Pod creation did not provide a bounded UID')
    if require_uid and uid != receipt.get('uid'):
        raise DrillError('Pod UID differs from this drill receipt')
    return metadata


class Kubernetes:
    def __init__(self):
        self.scope = module('sre_drill_scope', LOCAL / 'local_experiment.py')

    def request(self, args: list[str], body: dict | None = None) -> dict | None:
        self.scope.check_context()  # Exact context, loopback API, single ARM64 k3s node.
        try:
            result = subprocess.run(['rtk', 'proxy', 'kubectl', '--context', CONTEXT,
                '--request-timeout=10s', '-n', NAMESPACE, *args], cwd=ROOT,
                input=json.dumps(body) if body is not None else None, text=True,
                capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            raise DrillError('scoped Kubernetes request did not complete') from None
        if result.returncode or len(result.stdout) > 1024 * 1024:
            raise DrillError('scoped Kubernetes request failed or exceeded the response limit')
        return json.loads(result.stdout) if result.stdout.strip() else None

    def deployment(self) -> dict:
        return self.request(['get', 'deployment', 'trino-local', '-o', 'json'])

    def create(self, pod: dict) -> dict:
        return self.request(['create', '-f', '-', '-o', 'json'], pod)

    def get(self, name: str) -> dict | None:
        if not re.fullmatch(r'sre-drill-[0-9a-f]{32}', name):
            raise DrillError('read is outside disposable drill names')
        return self.request(['get', 'pod', name, '--ignore-not-found', '-o', 'json'])

    def delete(self, receipt: dict, current: dict) -> None:
        metadata = owned(current, receipt)
        version = metadata.get('resourceVersion')
        if not isinstance(version, str) or not re.fullmatch(r'[0-9]{1,128}', version):
            raise DrillError('cleanup requires the current resource version')
        options = {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'gracePeriodSeconds': 1,
                   'preconditions': {'uid': receipt['uid'], 'resourceVersion': version}}
        # kubectl rawhttp.RawDelete sends -f - as the DELETE body. A normal
        # name-only kubectl delete does NOT enforce these API preconditions.
        # https://github.com/kubernetes/kubectl/blob/master/pkg/rawhttp/raw.go
        self.request(['delete', '--raw=/api/v1/namespaces/' + NAMESPACE + '/pods/' + receipt['name'],
                      '-f', '-'], options)


async def wait_failed(kube, receipt: dict, *, clock=time.monotonic, pause=asyncio.sleep) -> dict:
    deadline = clock() + 90
    while True:
        pod = kube.get(receipt['name'])
        if pod is None:
            raise DrillError('created Pod disappeared before observation')
        owned(pod, receipt)
        status = pod.get('status', {})
        if status.get('phase') == 'Failed':
            containers = status.get('containerStatuses', [])
            exit_code = containers[0].get('state', {}).get('terminated', {}).get('exitCode') if len(containers) == 1 else None
            if (len(containers) != 1 or containers[0].get('name') != 'fail-once'
                    or type(exit_code) is not int or exit_code != 1):
                raise DrillError('Pod failed without the intended one-shot exit status')
            return {'phase': 'Failed', 'uid': receipt['uid'], 'exit_code': 1}
        if clock() >= deadline:
            raise DrillError('disposable Pod did not reach the expected Failed state')
        await pause(1)


async def cleanup(kube, receipt: dict, *, clock=time.monotonic, pause=asyncio.sleep) -> dict:
    try:
        current = kube.get(receipt['name'])
        if current is None:
            return {'status': 'already_absent', 'uid': receipt['uid']}
        owned(current, receipt)
        kube.delete(receipt, current)
        deadline = clock() + 30
        while True:
            current = kube.get(receipt['name'])
            if current is None:
                return {'status': 'deleted', 'uid': receipt['uid']}
            observed_uid = current.get('metadata', {}).get('uid')
            if not isinstance(observed_uid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', observed_uid):
                raise DrillError('deletion readback did not provide a Pod UID')
            if observed_uid != receipt['uid']:
                return {'status': 'original_uid_absent', 'uid': receipt['uid'], 'replacement_preserved': True}
            if clock() >= deadline:
                raise DrillError('Pod deletion has not completed')
            await pause(1)
    except Exception:
        return {'status': 'deferred', 'uid': receipt['uid'], 'reason': 'ownership_request_or_deletion_unconfirmed'}


def verify_detection(report: dict, observations: list, receipt: dict) -> dict:
    if report.get('result') != 'passed' or report.get('checkpoint') != 'completed-in-memory':
        raise DrillError('SRE workflow did not complete with verified audits')
    findings = [finding for finding in report['evidence']['findings']
                if finding.get('subject') == receipt['name'] and finding.get('severity') == 'critical'
                and finding.get('code') == 'WORKLOAD_FAILED']
    if len(findings) != 1:
        raise DrillError('SRE did not identify the disposable Failed Pod as critical')
    selected = [check for check in report['triage']['checks'] if check.get('tool') == 'k8s_read_pods']
    if len(selected) != 1 or findings[0]['id'] not in selected[0]['finding_ids']:
        raise DrillError('SRE did not select the required Pod re-observation')
    if not isinstance(observations, list) or not len(TOOLS) + 1 <= len(observations) <= len(TOOLS) + 2:
        raise DrillError('missing bounded workflow observations')
    initial = {item['tool']: item['snapshot'] for item in observations if item.get('phase') == 'initial'}
    if set(initial) != set(TOOLS) or sum(item.get('phase') == 'initial' for item in observations) != len(TOOLS):
        raise DrillError('initial workflow observations differ')
    if hashlib.sha256(json.dumps(initial, sort_keys=True).encode()).hexdigest() != report['initial_sha256']:
        raise DrillError('initial workflow observation digest differs')
    try:
        rebuilt = build_evidence(initial['shirokuma_doctor'], initial['k8s_read_pods'],
                                 initial['k8s_read_events'], initial['local_platform_status'])
    except (ValueError, KeyError, TypeError):
        raise DrillError('saved observations cannot reconstruct the SRE evidence') from None
    if rebuilt != report['evidence']:
        raise DrillError('SRE evidence differs from the saved observations')
    container_findings = [finding for finding in rebuilt['findings']
                          if finding['subject'] == receipt['name']
                          and finding['code'] == 'CONTAINER_EXIT_NONZERO'
                          and finding['severity'] == 'critical']
    if (len(container_findings) != 1
            or container_findings[0]['id'] not in selected[0]['finding_ids']):
        raise DrillError('SRE did not select the observed container exit for re-observation')
    followups = [item['snapshot'] for item in observations
                 if item.get('tool') == 'k8s_read_pods' and item.get('phase') == 'followup']
    if len(followups) != 1:
        raise DrillError('missing exact Pod follow-up observation')
    if hashlib.sha256(json.dumps(followups[0], sort_keys=True).encode()).hexdigest() != report['followup_sha256']['k8s_read_pods']:
        raise DrillError('Pod follow-up digest differs')
    terminations = []
    for snapshot in (initial['k8s_read_pods'], followups[0]):
        rows = [pod for pod in snapshot['pods'] if pod.get('name') == receipt['name']]
        if (snapshot.get('namespace') != NAMESPACE or snapshot.get('truncated') is not False
                or len(rows) != 1 or rows[0].get('uid') != receipt['uid'] or rows[0].get('phase') != 'Failed'):
            raise DrillError('SRE snapshots did not retain the same Failed Pod UID')
        detail = rows[0].get('container_observation')
        if (not isinstance(detail, dict) or detail.get('status') != 'complete'
                or detail.get('scope') != 'application-containers'
                or not isinstance(detail.get('containers'), list) or len(detail['containers']) != 1):
            raise DrillError('SRE snapshots lack the complete application container observation')
        container = detail['containers'][0]
        state = container.get('state') if isinstance(container, dict) else None
        if (not isinstance(state, dict) or container.get('name') != 'fail-once'
                or container.get('ready') is not False or type(container.get('restarts')) is not int
                or container['restarts'] != 0 or container.get('last_termination') is not None
                or state.get('kind') != 'terminated' or state.get('reason') != 'Error'
                or type(state.get('exit_code')) is not int or state['exit_code'] != 1
                or not state.get('started_at') or not state.get('finished_at')):
            raise DrillError('SRE snapshots did not retain the intended current container exit')
        terminations.append(detail)
    if terminations[0] != terminations[1]:
        raise DrillError('container termination changed between the same-UID observations')
    comparison = report['comparisons']['sources']['k8s_read_pods']
    if comparison.get('status') not in {'changed', 'no-change-observed'}:
        raise DrillError('Pod re-observation was not comparable')
    return {'critical_finding_id': findings[0]['id'], 'pod_uid': receipt['uid'],
            'container_finding_id': container_findings[0]['id'],
            'container_name': 'fail-once', 'container_state': terminations[0]['containers'][0]['state'],
            'initial_and_followup_phase': 'Failed', 'selected_tool': 'k8s_read_pods',
            'comparison_status': comparison['status'], 'workflow_task_id': report['task_id'],
            'workflow_trace_id': report['trace_id']}


async def run_drill(config: dict, output: Path, *, kube=None, runner=run_sre) -> dict:
    validate_config(config)
    if config['active_provider'] != 'local':
        raise DrillError('drill requires the explicitly configured real local model')
    output.mkdir(parents=True, mode=0o700)
    kube = kube if kube is not None else Kubernetes()
    run_id = uuid.uuid4().hex
    report = {'result': 'failed', 'scope': 'one-new-disposable-pod-local-sre-drill', 'run_id': run_id,
              'context': CONTEXT, 'namespace': NAMESPACE, 'retained_workloads_modified': False,
              'cleanup': {'status': 'not_created'}}
    receipt, create_attempted, stage = None, False, 'prepare'
    try:
        pod = manifest(kube.deployment(), run_id)
        private_write(output / 'creation-intent.json', pod)
        stage, create_attempted = 'create', True
        created = kube.create(pod)
        provisional = {'run_id': run_id, 'name': pod['metadata']['name'], 'namespace': NAMESPACE}
        metadata = owned(created, provisional, require_uid=False)
        provisional.update(uid=metadata['uid'], image=pod['spec']['containers'][0]['image'],
                           run_as_user=pod['spec']['securityContext']['runAsUser'])
        stage = 'persist_creation_receipt'
        private_write(output / 'created.json', provisional)
        receipt = provisional  # Cleanup authority exists only after the receipt was saved.
        report['created'] = receipt
        stage = 'wait_for_failed'
        report['failed_pod'] = await wait_failed(kube, receipt)
        stage = 'sre_workflow'
        workflow_dir = output / 'workflow'
        sre_result = await runner(config, workflow_dir)
        stage = 'verify_detection'
        if sre_result.get('observations_file') != 'observations.json':
            raise DrillError('SRE did not retain the expected fixed observation file')
        observations = json.loads((workflow_dir / sre_result['observations_file']).read_text())
        report['detection'] = verify_detection(sre_result, observations, receipt)
        report['result'] = 'passed'
    except Exception:
        report['failure_stage'] = stage
    finally:
        if receipt is not None:
            report['cleanup'] = await cleanup(kube, receipt)
        elif create_attempted:
            report['cleanup'] = {'status': 'deferred', 'reason': 'creation_outcome_or_receipt_unconfirmed'}
        if report['cleanup']['status'] == 'deferred':
            report['result'] = 'failed'
        private_write(output / 'summary.json', report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=LOCAL / 'llm/providers.qwen-local.json')
    args = parser.parse_args()
    output = ROOT / 'tmp/local-experiment/sre-drill' / uuid.uuid4().hex
    try:
        report = asyncio.run(run_drill(load_config(args.config), output))
    except Exception:
        report = {'result': 'failed', 'failure_stage': 'configuration_or_setup'}
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_write(output / 'summary.json', report)
    print(json.dumps({**report, 'evidence_path': str(output)}))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
