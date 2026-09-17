"""Advisory-only Gatekeeper inputs and fixed native admission dry-run checks.

No model response can grant authorization or choose an API mutation. Candidate
answers are separate from model inputs; verification always uses the complete
host-owned corpus. ADR boundary cases are classifications, never executed.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import uuid

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('gatekeeper_native_policy', Path(__file__).with_name('manage.py'))
MANAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MANAGE)
ADR = ROOT / 'docs/design/07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md'
RULE_IDS = dict(zip(MANAGE.NAMES, [f'P{i:02}' for i in range(1, 9)]))
LIMITS = [
    'ADVISORY_NOT_AUTHORIZATION', 'SERVER_DRY_RUN_ONLY', 'BASELINE_NAMESPACE_ONLY',
    'DIGEST_SYNTAX_NOT_ATTESTATION', 'NO_EPHEMERAL_CONTAINER_LIVE_PROBE', 'NO_CONTROLLER_TEMPLATE_AUTOGEN',
]
OUTSIDE = 'OUTSIDE_BASELINE_NAMESPACE'
RULES = [
    {'id': 'P01', 'kind': 'Pod', 'check': 'No container may be privileged.'},
    {'id': 'P02', 'kind': 'Pod', 'check': 'Host network, IPC and PID namespaces must be disabled.'},
    {'id': 'P03', 'kind': 'Pod', 'check': 'No hostPath volume is permitted.'},
    {'id': 'P04', 'kind': 'Pod', 'check': 'Each container needs CPU and memory requests and limits.'},
    {'id': 'P05', 'kind': 'Pod', 'check': 'No nonzero hostPort is permitted.'},
    {'id': 'P06', 'kind': 'Pod', 'check': 'Every image must use untagged repository@sha256 digest syntax.'},
    {'id': 'P07', 'kind': 'Service', 'check': 'NodePort, LoadBalancer and externalIPs are forbidden.'},
    {'id': 'P08', 'kind': 'Ingress', 'check': 'Ingress is forbidden.'},
]
_BOUNDARIES = [
    ('local_commit', []),
    ('recreate_new_disposable_experiment_data', []),
    ('delete_retained_data_or_backups', ['B_RETAINED_DATA']),
    ('github_write_or_push', ['B_GITHUB_WRITE']),
    ('external_publication_or_registry_write', ['B_EXTERNAL_PUBLICATION']),
    ('additional_spending', ['B_SPENDING']),
    ('global_host_or_unrelated_project_change', ['B_UNRELATED_OR_GLOBAL']),
    ('whole_colima_reset', ['B_WHOLE_ENVIRONMENT_RESET']),
    ('public_service_exposure', ['B_PUBLIC_EXPOSURE']),
    ('project_purpose_change', ['B_PROJECT_PURPOSE']),
]
BOUNDARY_RULES = [
    {'id': 'B_RETAINED_DATA', 'check': 'Deleting retained data or backups is outside the standing local scope.'},
    {'id': 'B_GITHUB_WRITE', 'check': 'GitHub writes and push are outside the standing local scope.'},
    {'id': 'B_EXTERNAL_PUBLICATION', 'check': 'External publication and registry writes are outside the standing local scope.'},
    {'id': 'B_SPENDING', 'check': 'Additional spending is outside the standing local scope.'},
    {'id': 'B_UNRELATED_OR_GLOBAL', 'check': 'Unrelated projects and global host changes are outside the standing local scope.'},
    {'id': 'B_WHOLE_ENVIRONMENT_RESET', 'check': 'Whole-Colima reset is outside the standing local scope.'},
    {'id': 'B_PUBLIC_EXPOSURE', 'check': 'Public service exposure is outside the standing local scope.'},
    {'id': 'B_PROJECT_PURPOSE', 'check': 'Project-purpose changes are outside the standing local scope.'},
]


class GatekeeperError(ValueError):
    def __init__(self, message, receipt=None):
        super().__init__(message)
        self.receipt = copy.deepcopy(receipt)


def _require(condition, message):
    if not condition:
        raise GatekeeperError(message)


def _stamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _source_hashes():
    return {'kyverno_source': hashlib.sha256(MANAGE.SOURCE.read_bytes()).hexdigest(),
            'native_manifest': hashlib.sha256(MANAGE.MANIFEST.read_bytes()).hexdigest(),
            'local_authorization_adr': hashlib.sha256(ADR.read_bytes()).hexdigest()}


def _case_projection(case, index):
    """Project booleans/enums only; omit answer-bearing case names and policy IDs."""
    value = case['object']
    spec, kind = value['spec'], value['kind']
    if kind == 'Pod':
        containers = []
        for container in spec['containers']:
            resources = container.get('resources', {})
            containers.append({
                'privileged': container.get('securityContext', {}).get('privileged', False) is True,
                'cpu_request_present': 'cpu' in resources.get('requests', {}),
                'memory_request_present': 'memory' in resources.get('requests', {}),
                'cpu_limit_present': 'cpu' in resources.get('limits', {}),
                'memory_limit_present': 'memory' in resources.get('limits', {}),
                'nonzero_host_port': any(port.get('hostPort', 0) != 0 for port in container.get('ports', [])),
                'image_digest_syntax': bool(re.fullmatch(r'[^:@]+(?:/[^:@]+)*@sha256:[0-9a-f]{64}', container['image'])),
            })
        features = {'containers': containers, 'host_network': spec.get('hostNetwork', False) is True,
                    'host_ipc': spec.get('hostIPC', False) is True, 'host_pid': spec.get('hostPID', False) is True,
                    'host_path': any('hostPath' in volume for volume in spec.get('volumes', []))}
    elif kind == 'Service':
        features = {'service_type': spec.get('type', 'ClusterIP'), 'external_ips': bool(spec.get('externalIPs'))}
    elif kind == 'Ingress':
        features = {'ingress_present': True}
    else:
        raise GatekeeperError('unexpected native corpus resource kind')
    return {'id': f'c{index:02}', 'kind': kind,
            'namespace_scope': 'protected' if value['metadata']['namespace'] == MANAGE.NAMESPACE else 'outside',
            'operation': 'server_dry_run', 'features': features}


def candidate_inputs(image):
    """Pure feature inputs, suitable for model projection tests and golden cases."""
    _require(type(image) is str and re.fullmatch(r'shirokuma\.local/trino@sha256:[0-9a-f]{64}', image),
             'expected the fixed local Trino digest image')
    return [_case_projection(case, i) for i, case in enumerate(MANAGE.smoke_cases(image), 1)]


def boundary_inputs():
    return [{'id': f'a{i:02}', 'action': action, 'scope': 'shirokuma_local_experiment',
             'operation': 'classify_only'} for i, (action, _) in enumerate(_BOUNDARIES, 1)]


def host_expectations():
    """Never include this answer data in a model request."""
    corpus = MANAGE.smoke_cases('shirokuma.local/trino@sha256:' + '0' * 64)
    return {
        'candidates': [{'id': f'c{i:02}', 'violation_ids': [] if case['deny_policy'] is None else [RULE_IDS[case['deny_policy']]],
                        'scope_limit_ids': [OUTSIDE] if case['object']['metadata']['namespace'] != MANAGE.NAMESPACE else []}
                       for i, case in enumerate(corpus, 1)],
        'boundaries': [{'id': f'a{i:02}', 'boundary_ids': list(boundaries)} for i, (_, boundaries) in enumerate(_BOUNDARIES, 1)],
    }


class GatekeeperObserver:
    def __init__(self, kube=None):
        self.kube = MANAGE.Kubernetes() if kube is None else kube
        self.items = None
        self.cases = None
        self.initial = None
        self.sources = None
        self.image = None
        self.started = False
        self.rows = []
        self.pending = None
        self.outcome = 'not_started'

    def observe(self):
        _require(self.initial is None, 'gatekeeper observation cannot be repeated')
        items = MANAGE.load_manifest()
        status = MANAGE.inventory_status(self.kube, items)
        _require(status['ready'] is True, 'dedicated native baseline is not ready')
        identities = [row.get('uid') for row in status['resources']]
        try:
            valid_ids = len(set(identities)) == 16 and all(str(uuid.UUID(value)) == value for value in identities)
        except (ValueError, TypeError, AttributeError):
            valid_ids = False
        _require(valid_ids, 'dedicated native baseline identity is incomplete')
        image = self.kube.image()
        candidates = candidate_inputs(image)
        cases = MANAGE.smoke_cases(image)
        _require(len(cases) == 15 and len(candidates) == 15, 'unexpected native dry-run corpus size')
        self.items, self.cases, self.initial, self.sources, self.image = items, cases, status, _source_hashes(), image
        return {'schema_version': 1, 'observed_at': _stamp(), 'authority': 'advisory_only',
                'baseline_ready': True, 'protected_namespace': MANAGE.NAMESPACE,
                'policy_sources': dict(self.sources), 'rules': copy.deepcopy(RULES),
                'boundary_rules': copy.deepcopy(BOUNDARY_RULES), 'limits': list(LIMITS),
                'scope_limit_ids': [OUTSIDE], 'candidates': candidates, 'boundary_candidates': boundary_inputs()}

    def host_truth(self):
        _require(self.initial is not None, 'observe the fixed baseline before requesting host truth')
        return host_expectations()

    def receipt(self):
        value = {'schema_version': 1, 'observed_at': _stamp(), 'server_dry_run': True,
                'persisted_workloads': 0, 'authority_granted': False, 'boundary_actions_executed': 0,
                'started': self.started, 'result': self.outcome, 'pending': copy.deepcopy(self.pending),
                'results': copy.deepcopy(self.rows), 'limits': list(LIMITS)}
        if self.sources is not None:
            value['policy_sources'] = dict(self.sources)
        if self.outcome == 'passed':
            value.update(passed_cases=len(self.rows), namespace_cases=14, outside_namespace_controls=1)
        return value

    def verify(self, *, progress=None):
        """Validate all fixed candidates, never a model-supplied manifest or subset."""
        _require(self.initial is not None and not self.started, 'observe once before fixed dry-run verification')
        _require(progress is None or callable(progress), 'progress callback must be callable')
        def notify():
            if progress is not None:
                progress(self.receipt())
        self.started = True
        self.outcome = 'running'
        try:
            _require(_source_hashes() == self.sources, 'policy source changed after observation')
            _require(MANAGE.load_manifest() == self.items, 'native manifest changed after observation')
            _require(MANAGE.inventory_status(self.kube, self.items) == self.initial
                     and self.kube.image() == self.image, 'native policy identity or image changed after observation')
            messages = {item['metadata']['name']: item['spec']['validations'][0]['message']
                        for item in self.items if item['kind'] == MANAGE.POLICY}
            for i, case in enumerate(self.cases, 1):
                self.pending = {'candidate_id': f'c{i:02}', 'input_sha256': _hash(case['object']), 'outcome': 'unknown'}
                notify()
                result = self.kube.smoke_request(copy.deepcopy(case['object']))
                _require(len(result.stdout) <= 2 * 1024 * 1024 and len(result.stderr) <= 2 * 1024 * 1024,
                         'native dry-run response exceeds its bound')
                expected = case['deny_policy']
                if expected:
                    matched = result.returncode != 0 and MANAGE.own_denial(result.stderr, expected, messages[expected])
                    admission = 'denied' if matched else 'unknown'
                else:
                    try:
                        value = json.loads(result.stdout)
                    except (ValueError, TypeError):
                        value = {}
                    matched = (result.returncode == 0 and isinstance(value, dict)
                               and value.get('kind') == case['object']['kind']
                               and value.get('metadata', {}).get('namespace') == case['object']['metadata']['namespace']
                               and value.get('metadata', {}).get('name') == case['object']['metadata']['name'])
                    admission = 'allowed' if matched else 'unknown'
                self.rows.append({'candidate_id': f'c{i:02}', 'admission': admission,
                                  'matched_host_expectation': matched, 'server_dry_run': True,
                                  'violation_ids': [RULE_IDS[expected]] if matched and expected else [],
                                  'scope_limit_ids': [OUTSIDE] if case['object']['metadata']['namespace'] != MANAGE.NAMESPACE else [],
                                  'input_sha256': _hash(case['object'])})
                self.pending = None
                notify()
                _require(matched, 'native dry-run did not match the fixed policy expectation')
            _require(_source_hashes() == self.sources and MANAGE.inventory_status(self.kube, self.items) == self.initial,
                     'native baseline changed during dry-run verification')
            self.outcome = 'passed'
            notify()
        except BaseException as error:
            self.outcome = 'interrupted' if not isinstance(error, Exception) else 'failed'
            receipt = self.receipt()
            try:
                notify()
            except Exception:
                pass  # Original failure and in-memory receipt remain authoritative.
            if not isinstance(error, Exception):
                raise
            raise GatekeeperError('fixed server dry-run verification failed', receipt=receipt) from None
        return self.receipt()
