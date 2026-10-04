#!/usr/bin/env python3
"""Namespace-scoped native admission bridge; no Kyverno controller autogeneration.

Seven repository CEL policies become eight typed native policies and bindings:
the public-exposure rule is split into Service and Ingress matches, preserving
its predicates while allowing Kubernetes to check each resource's actual schema.
Rollback removes owned bindings first and retains the inert policy definitions.
Smoke requests are server dry-runs; no Pod, Service or Ingress is persisted.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / 'policies/kyverno/baseline.yaml'
MANIFEST = ROOT / 'deploy/local-experiment/policy/native-baseline.json'
STATE = ROOT / 'tmp/local-experiment/policy'
CONTEXT = 'colima-mac-studio-solo'
NAMESPACE = 'shirokuma-dev'
API = 'admissionregistration.k8s.io/v1'
POLICY = 'ValidatingAdmissionPolicy'
BINDING = 'ValidatingAdmissionPolicyBinding'
RESOURCES = {POLICY: 'validatingadmissionpolicies', BINDING: 'validatingadmissionpolicybindings'}
BASE_NAMES = ('disallow-privileged-containers', 'disallow-host-namespaces',
              'disallow-host-path', 'require-pod-resources', 'disallow-host-ports',
              'require-image-digest', 'deny-public-service')
NAMES = tuple('shirokuma-local-' + name for name in (*BASE_NAMES, 'deny-ingress'))
PUBLIC_SERVICE = 'shirokuma-local-deny-public-service'
INGRESS = 'shirokuma-local-deny-ingress'
LABELS = {'app.kubernetes.io/part-of': 'shirokuma', 'shirokuma.dev/scope': 'local-experiment',
          'shirokuma.dev/policy-owner': 'native-baseline-v1'}
SELECTOR = {'matchLabels': {'kubernetes.io/metadata.name': NAMESPACE}}


class PolicyError(RuntimeError):
    pass


def source_policies() -> list[dict]:
    # kubectl's local YAML decoder needs no CRD, cluster, or third-party Python dependency.
    try:
        value = subprocess.run(['rtk', 'proxy', 'kubectl', 'patch', '--local=true', '--type=merge',
            '-p', '{}', '-f', str(SOURCE), '-o', 'json'], cwd=ROOT, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        raise PolicyError('local source decoding did not complete') from None
    if value.returncode or len(value.stdout) > 256 * 1024:
        raise PolicyError('local source decoding failed')
    remaining, result, decoder = value.stdout, [], json.JSONDecoder()
    while remaining.strip():
        item, end = decoder.raw_decode(remaining.lstrip())
        result.append(item)
        remaining = remaining.lstrip()[end:]
    if ([x.get('metadata', {}).get('name') for x in result] != list(BASE_NAMES)
            or any(x.get('kind') != 'ValidatingPolicy' for x in result)):
        raise PolicyError('unexpected repository policy source inventory')
    return result


def render_native(source: list[dict]) -> dict:
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    items = []
    expanded = []
    for original in source:
        if original['metadata']['name'] != 'deny-public-service':
            expanded.append((original, False))
            continue
        service, ingress = copy.deepcopy(original), copy.deepcopy(original)
        rules = original['spec']['matchConstraints']['resourceRules']
        service['spec']['matchConstraints']['resourceRules'] = [copy.deepcopy(x) for x in rules if x['resources'] == ['services']]
        ingress['metadata']['name'] = 'deny-ingress'
        ingress['spec']['matchConstraints']['resourceRules'] = [copy.deepcopy(x) for x in rules if x['resources'] == ['ingresses']]
        # Keep the original full predicate on Service, where every field exists.
        # For the Ingress-only match, its original ternary's else branch applies.
        ingress['spec']['validations'][0]['expression'] = "object.kind != 'Ingress'"
        expanded.extend(((service, True), (ingress, True)))
    for original, adapted in expanded:
        name = 'shirokuma-local-' + original['metadata']['name']
        metadata = {'name': name, 'labels': copy.deepcopy(LABELS), 'annotations': {
            'shirokuma.dev/source': str(SOURCE.relative_to(ROOT)), 'shirokuma.dev/source-sha256': digest,
            'shirokuma.dev/coverage': 'pods-services-ingresses-no-controller-autogen'}}
        if adapted:
            metadata['annotations'].update({'shirokuma.dev/source-policy': 'deny-public-service',
                'shirokuma.dev/native-adaptation': 'split-service-ingress-types-preserving-source-predicates'})
        constraints = copy.deepcopy(original['spec']['matchConstraints'])
        constraints.update(namespaceSelector=copy.deepcopy(SELECTOR), matchPolicy='Equivalent')
        for rule in constraints['resourceRules']:
            rule['scope'] = 'Namespaced'
        spec = {'failurePolicy': 'Fail', 'matchConstraints': constraints,
                'validations': copy.deepcopy(original['spec']['validations'])}
        if 'variables' in original['spec']:
            spec['variables'] = copy.deepcopy(original['spec']['variables'])
        items.append({'apiVersion': API, 'kind': POLICY, 'metadata': metadata, 'spec': spec})
        items.append({'apiVersion': API, 'kind': BINDING, 'metadata': copy.deepcopy(metadata), 'spec': {
            'policyName': name, 'validationActions': ['Deny'],
            'matchResources': {'namespaceSelector': copy.deepcopy(SELECTOR), 'matchPolicy': 'Equivalent'}}})
    return {'apiVersion': 'v1', 'kind': 'List', 'items': items}


def load_manifest() -> list[dict]:
    value = json.loads(MANIFEST.read_text())
    if value != render_native(source_policies()):
        raise PolicyError('dedicated manifest differs from the exact repository CEL projection')
    return value['items']


def canonical_spec(spec: dict) -> dict:
    result = copy.deepcopy(spec)
    # API-server empty selector defaults do not change the selected namespace.
    for key in ('matchConstraints', 'matchResources'):
        match = result.get(key, {})
        if match.get('objectSelector') == {}:
            match.pop('objectSelector')
        if match.get('excludeResourceRules') == []:
            match.pop('excludeResourceRules')
    return result


def assert_owned(current: dict, expected: dict) -> None:
    metadata = current.get('metadata', {})
    desired = expected['metadata']
    if (current.get('apiVersion') != API or current.get('kind') != expected['kind']
            or metadata.get('name') != desired['name']
            or any(metadata.get('labels', {}).get(key) != value for key, value in LABELS.items())
            or any(metadata.get('annotations', {}).get(key) != value for key, value in desired['annotations'].items())
            or canonical_spec(current.get('spec', {})) != canonical_spec(expected['spec'])):
        raise PolicyError('existing dedicated policy identity, owner or content differs; refusing overwrite')


def typecheck(current: dict) -> str:
    status, metadata = current.get('status', {}), current.get('metadata', {})
    checked = status.get('typeChecking')
    generation = metadata.get('generation')
    if (not isinstance(checked, dict) or type(generation) is not int or generation < 1
            or type(status.get('observedGeneration')) is not int or status['observedGeneration'] != generation):
        return 'pending'
    warnings = checked.get('expressionWarnings', [])
    if not isinstance(warnings, list) or warnings:
        return 'warnings'
    return 'complete'


class Kubernetes:
    def __init__(self):
        path = ROOT / 'tools/local_experiment/local_experiment.py'
        spec = importlib.util.spec_from_file_location('local_policy_scope', path)
        self.scope = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.scope)
        self.expected = {(x['kind'], x['metadata']['name']): x for x in load_manifest()}

    def request(self, args: list[str], body: dict | None = None):
        self.scope.check_context()
        try:
            return subprocess.run(['rtk', 'proxy', 'kubectl', '--context', CONTEXT,
                '--request-timeout=15s', *args], cwd=ROOT, input=json.dumps(body) if body is not None else None,
                capture_output=True, text=True, timeout=25)
        except (OSError, subprocess.SubprocessError):
            raise PolicyError('scoped Kubernetes request did not complete') from None

    def json_request(self, args: list[str], body: dict | None = None):
        result = self.request(args, body)
        if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
            raise PolicyError('scoped Kubernetes request failed or exceeded its response limit')
        def unique_keys(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise PolicyError('scoped Kubernetes response contains duplicate JSON keys')
                value[key] = item
            return value

        if not result.stdout.strip():
            return None  # kubectl --ignore-not-found emits no object for a missing resource.
        def invalid_constant(value):
            raise PolicyError('scoped Kubernetes response contains non-JSON numeric constant')

        def finite_float(raw):
            value = float(raw)
            if not math.isfinite(value):
                raise PolicyError('scoped Kubernetes response contains non-finite JSON number')
            return value

        value = json.loads(result.stdout, object_pairs_hook=unique_keys,
                           parse_constant=invalid_constant, parse_float=finite_float)
        if value is None:
            raise PolicyError('scoped Kubernetes response contains explicit JSON null')
        return value

    def get(self, kind: str, name: str):
        if kind not in RESOURCES or name not in NAMES:
            raise PolicyError('policy read is outside the dedicated inventory')
        return self.json_request(['get', RESOURCES[kind], name, '--ignore-not-found', '-o', 'json'])

    def create(self, value: dict, *, dry_run=False):
        key = (value.get('kind'), value.get('metadata', {}).get('name'))
        if key not in self.expected or value != self.expected[key]:
            raise PolicyError('policy creation is outside the dedicated inventory')
        args = ['create', '-f', '-', '-o', 'json']
        if dry_run:
            args.append('--dry-run=server')
        return self.json_request(args, value)

    def delete_binding(self, current: dict, expected: dict):
        key = (expected.get('kind'), expected.get('metadata', {}).get('name'))
        if expected.get('kind') != BINDING or self.expected.get(key) != expected:
            raise PolicyError('rollback only removes dedicated bindings')
        assert_owned(current, expected)
        uid, version = current['metadata'].get('uid'), current['metadata'].get('resourceVersion')
        if (not isinstance(uid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', uid)
                or not isinstance(version, str) or not re.fullmatch(r'[0-9]{1,128}', version)):
            raise PolicyError('binding deletion requires a current UID and resource version')
        path = '/apis/' + API + '/' + RESOURCES[BINDING] + '/' + expected['metadata']['name']
        options = {'apiVersion': 'v1', 'kind': 'DeleteOptions',
                   'preconditions': {'uid': uid, 'resourceVersion': version}}
        self.json_request(['delete', '--raw=' + path, '-f', '-'], options)

    def image(self) -> str:
        deployment = self.json_request(['-n', NAMESPACE, 'get', 'deployment', 'trino-local', '-o', 'json'])
        images = [item['image'] for item in deployment['spec']['template']['spec']['containers'] if item['name'] == 'trino']
        if len(images) != 1 or not re.fullmatch(r'shirokuma\.local/trino@sha256:[0-9a-f]{64}', images[0]):
            raise PolicyError('smoke requires the existing local immutable Trino image')
        return images[0]

    def smoke_request(self, value: dict):
        namespace = value.get('metadata', {}).get('namespace')
        if (value.get('kind') not in {'Pod', 'Service', 'Ingress'}
                or namespace not in {NAMESPACE, 'default'}
                or not re.fullmatch(r'shirokuma-policy-smoke-[0-9a-f]{12}', value.get('metadata', {}).get('name', ''))):
            raise PolicyError('smoke request escaped its disposable namespace scope')
        if namespace == 'default' and (value['kind'] != 'Pod' or len(value.get('spec', {}).get('containers', [])) != 1
                or value['spec']['containers'][0].get('image') != 'shirokuma.local/trino:dryrun-only'):
            raise PolicyError('default namespace is limited to the mutable-image dry-run control')
        return self.request(['-n', namespace, 'create', '--dry-run=server', '-f', '-', '-o', 'json'], value)


def inventory_status(kube, items: list[dict]) -> dict:
    rows = []
    for expected in items:
        current = kube.get(expected['kind'], expected['metadata']['name'])
        row = {'kind': expected['kind'], 'name': expected['metadata']['name'], 'present': current is not None}
        if current is not None:
            assert_owned(current, expected)
            row['uid'] = current['metadata'].get('uid')
            if expected['kind'] == POLICY:
                row['type_check'] = typecheck(current)
        rows.append(row)
    ready = all(x['present'] and x.get('type_check', 'complete') == 'complete' for x in rows)
    return {'ready': ready, 'resources': rows, 'namespace': NAMESPACE,
            'controller_template_autogen': False, 'strict_image_attestation': False}


def apply_policies(kube, items: list[dict], *, record=lambda event: None, clock=time.monotonic, pause=time.sleep):
    # Preflight every existing name before creating even an inert definition.
    missing = []
    for expected in items:
        current = kube.get(expected['kind'], expected['metadata']['name'])
        if current is not None:
            assert_owned(current, expected)
        else:
            missing.append(expected)
    for expected in missing:
        kube.create(expected, dry_run=True)
    for kind in (POLICY, BINDING):
        selected = [item for item in items if item['kind'] == kind]
        missing = []
        for expected in selected:
            current = kube.get(kind, expected['metadata']['name'])
            if current is None:
                missing.append(expected)
            else:
                assert_owned(current, expected)
        for expected in missing:
            current = kube.create(expected)
            assert_owned(current, expected)
            record({'action': 'created', 'kind': kind, 'name': expected['metadata']['name'], 'uid': current['metadata'].get('uid')})
        if kind == POLICY:
            deadline = clock() + 45
            while True:
                states = []
                for expected in selected:
                    current = kube.get(POLICY, expected['metadata']['name'])
                    if current is None:
                        raise PolicyError('policy disappeared before binding activation')
                    assert_owned(current, expected)
                    states.append(typecheck(current))
                if 'warnings' in states:
                    raise PolicyError('policy type checking reported warnings; bindings were not activated')
                if all(state == 'complete' for state in states):
                    break
                if clock() >= deadline:
                    raise PolicyError('policy type checking is not confirmed; bindings were not activated')
                pause(1)
            record({'action': 'type_check_complete', 'policies': len(selected)})
    result = inventory_status(kube, items)
    if not result['ready']:
        raise PolicyError('dedicated admission inventory is not ready')
    return result


def rollback_bindings(kube, items: list[dict], *, record=lambda event: None, clock=time.monotonic, pause=time.sleep):
    bindings = [x for x in items if x['kind'] == BINDING]
    for expected in bindings:
        current = kube.get(BINDING, expected['metadata']['name'])
        if current is not None:
            assert_owned(current, expected)
    for expected in bindings:
        current = kube.get(BINDING, expected['metadata']['name'])
        if current is None:
            continue
        assert_owned(current, expected)
        uid = current['metadata'].get('uid')
        kube.delete_binding(current, expected)
        deadline = clock() + 30
        while True:
            remaining = kube.get(BINDING, expected['metadata']['name'])
            if remaining is None:
                break
            if remaining['metadata'].get('uid') != uid:
                raise PolicyError('replacement binding was preserved; rollback is incomplete')
            if clock() >= deadline:
                raise PolicyError('binding deletion has not been confirmed')
            pause(1)
        record({'action': 'binding_removed', 'name': expected['metadata']['name'], 'uid': uid})
    return {'bindings_removed': True, 'policy_definitions_retained': True, 'namespace': NAMESPACE}


def smoke_cases(image: str) -> list[dict]:
    metadata = {'name': 'shirokuma-policy-smoke-' + uuid.uuid4().hex[:12], 'namespace': NAMESPACE,
                'labels': {'shirokuma.dev/scope': 'local-experiment'}}
    pod = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': metadata, 'spec': {
        'restartPolicy': 'Never', 'automountServiceAccountToken': False,
        'securityContext': {'runAsNonRoot': True, 'runAsUser': 10000, 'seccompProfile': {'type': 'RuntimeDefault'}},
        'containers': [{'name': 'smoke', 'image': image, 'imagePullPolicy': 'Never', 'command': ['/bin/sh', '-c', 'exit 0'],
            'resources': {'requests': {'cpu': '50m', 'memory': '32Mi'}, 'limits': {'cpu': '100m', 'memory': '64Mi'}},
            'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}}}]}}
    service = {'apiVersion': 'v1', 'kind': 'Service', 'metadata': copy.deepcopy(metadata),
               'spec': {'type': 'ClusterIP', 'ports': [{'port': 8080, 'targetPort': 8080}]}}
    result = [{'case': 'allow-pod', 'object': pod, 'deny_policy': None},
              {'case': 'allow-clusterip', 'object': service, 'deny_policy': None}]
    changes = [
        ('privileged', 'disallow-privileged-containers', lambda p: p['spec']['containers'][0]['securityContext'].update(privileged=True, allowPrivilegeEscalation=True)),
        ('host-network', 'disallow-host-namespaces', lambda p: p['spec'].update(hostNetwork=True)),
        ('host-ipc', 'disallow-host-namespaces', lambda p: p['spec'].update(hostIPC=True)),
        ('host-pid', 'disallow-host-namespaces', lambda p: p['spec'].update(hostPID=True)),
        ('host-path', 'disallow-host-path', lambda p: p['spec'].update(volumes=[{'name': 'host', 'hostPath': {'path': '/tmp'}}])),
        ('missing-resources', 'require-pod-resources', lambda p: p['spec']['containers'][0].update(resources={})),
        ('host-port', 'disallow-host-ports', lambda p: p['spec']['containers'][0].update(ports=[{'containerPort': 8080, 'hostPort': 8080}])),
        ('mutable-image', 'require-image-digest', lambda p: p['spec']['containers'][0].update(image='shirokuma.local/trino:dryrun-only')),
    ]
    for name, policy, mutate in changes:
        value = copy.deepcopy(pod)
        mutate(value)
        result.append({'case': 'deny-' + name, 'object': value, 'deny_policy': 'shirokuma-local-' + policy})
    for name, fields in [('nodeport', {'type': 'NodePort'}), ('loadbalancer', {'type': 'LoadBalancer'}),
                         ('external-ip', {'externalIPs': ['192.0.2.1']})]:
        value = copy.deepcopy(service)
        value['spec'].update(fields)
        result.append({'case': 'deny-' + name, 'object': value, 'deny_policy': PUBLIC_SERVICE})
    ingress = {'apiVersion': 'networking.k8s.io/v1', 'kind': 'Ingress', 'metadata': copy.deepcopy(metadata),
               'spec': {'defaultBackend': {'service': {'name': 'dryrun-only', 'port': {'number': 8080}}}}}
    result.append({'case': 'deny-ingress', 'object': ingress, 'deny_policy': INGRESS})
    control = copy.deepcopy(next(x['object'] for x in result if x['case'] == 'deny-mutable-image'))
    control['metadata']['namespace'] = 'default'
    result.append({'case': 'allow-outside-namespace-scope', 'object': control, 'deny_policy': None})
    return result


def own_denial(stderr: str, name: str, message: str) -> bool:
    if name not in NAMES or not isinstance(message, str) or not message:
        return False
    pattern = (r"ValidatingAdmissionPolicy ['\"]" + re.escape(name) + r"['\"] with binding ['\"]"
               + re.escape(name) + r"['\"] denied request: " + re.escape(message) + r'(?:\r?\n|$)')
    return bool(re.search(pattern, stderr))


def smoke(kube, items: list[dict], *, record=lambda event: None) -> dict:
    if not inventory_status(kube, items)['ready']:
        raise PolicyError('smoke requires all dedicated policies and bindings to be ready')
    rows = []
    messages = {value['metadata']['name']: value['spec']['validations'][0]['message']
                for value in items if value['kind'] == POLICY}
    for case in smoke_cases(kube.image()):
        response = kube.smoke_request(case['object'])
        denied = case['deny_policy']
        if denied:
            passed = response.returncode != 0 and own_denial(response.stderr, denied, messages[denied])
        else:
            try:
                value = json.loads(response.stdout)
            except (ValueError, TypeError):
                value = {}
            passed = (response.returncode == 0 and value.get('kind') == case['object']['kind']
                      and value.get('metadata', {}).get('namespace') == case['object']['metadata']['namespace']
                      and value.get('metadata', {}).get('name') == case['object']['metadata']['name'])
        row = {'case': case['case'], 'result': 'passed' if passed else 'failed',
               'expected_policy': denied, 'server_dry_run': True,
               'namespace': case['object']['metadata']['namespace'],
               'input_sha256': hashlib.sha256(json.dumps(case['object'], sort_keys=True).encode()).hexdigest()}
        rows.append(row)
        record(row)
        if not passed:
            raise PolicyError('server dry-run result did not match the expected dedicated policy')
    return {'cases': rows, 'passed_cases': len(rows), 'persisted_workloads': 0,
            'namespace_cases': len(rows) - 1, 'outside_namespace_scope_controls': 1,
            'ephemeral_container_live_probe': False, 'controller_template_autogen': False}


def private_write(path: Path, value: dict):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['status', 'apply', 'smoke', 'rollback-bindings'])
    args = parser.parse_args()
    output = STATE / uuid.uuid4().hex
    output.mkdir(parents=True, mode=0o700)
    report = {'result': 'running', 'command': args.command, 'context': CONTEXT, 'namespace': NAMESPACE,
              'started_at': datetime.now(timezone.utc).isoformat(), 'events': [], 'evidence_path': str(output)}
    def record(value):
        report['events'].append(value)
        private_write(output / 'summary.json', report)
    lock = None
    try:
        items, kube = load_manifest(), Kubernetes()
        report.update(source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
                      manifest_sha256=hashlib.sha256(MANIFEST.read_bytes()).hexdigest())
        if args.command in {'apply', 'rollback-bindings'}:
            descriptor = os.open(STATE / '.mutation.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            lock = os.fdopen(descriptor, 'r+')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        private_write(output / 'summary.json', report)
        if args.command == 'apply':
            result = apply_policies(kube, items, record=record)
        elif args.command == 'smoke':
            result = smoke(kube, items, record=record)
        elif args.command == 'rollback-bindings':
            result = rollback_bindings(kube, items, record=record)
        else:
            result = inventory_status(kube, items)
        report.update(result='passed' if result.get('ready', True) else 'failed', details=result)
    except Exception:
        report.update(result='failed', error_code='LOCAL_POLICY_OPERATION_FAILED')
    finally:
        if lock is not None:
            lock.close()
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        private_write(output / 'summary.json', report)
    print(json.dumps(report))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
