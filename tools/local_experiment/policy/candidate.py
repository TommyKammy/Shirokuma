"""Pure, bounded input for one advisory Pod admission check.

This accepts a deliberately small subset of Kubernetes Pod JSON, not arbitrary
manifests. Quantities must be JSON strings. CPU quantities are positive integer
millicores (up to six digits) or decimal cores (up to three integer and three
fractional digits). Memory quantities are positive integer bytes,
Ki/Mi/Gi or k/M/G (at most 18 integer digits and 2**63-1 bytes). Valid Kubernetes
forms outside those subsets are rejected. Resource fields may be absent (P04);
malformed or inconsistent values may not. A limit without its corresponding
request is valid-but-unsupported: Kubernetes would default that request, hiding
the original missing field. We never supply resource quantities ourselves.
See https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/#requests-and-limits

For hostNetwork=true, a declared container port must have an explicit equal
hostPort. Missing/zero hostPort is valid-but-unsupported because Kubernetes
defaults it to containerPort, otherwise concealing a P05 violation.
See https://github.com/kubernetes/kubernetes/blob/v1.33.4/pkg/apis/core/v1/defaults.go

Input image references use lowercase ASCII repository components, optional
registry ports 1..65535, optional ASCII tags, and optional lowercase SHA256
digests. Bare/tagged references remain supported P06 violation candidates.
Kubernetes names here are lowercase DNS labels up to 63 characters; broader
valid Kubernetes names and unlisted manifest fields are intentionally rejected.

Only the dry-run identity, namespace, restartPolicy=Never,
automountServiceAccountToken=false and imagePullPolicy=Never are supplied by the
host. Explicit conflicting fixed values are rejected. Policy violations are
preserved, never repaired. A model receives candidate(), never receipt(), which
also contains the independent host answer. Original input and request bodies
remain in memory; receipt() contains only hashes, fixed IDs and safe features.

No filesystem, subprocess, network, model, or persistence operation is performed.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

MAX_BYTES = 16 * 1024
NAMESPACE = 'shirokuma-dev'
NAME_PREFIX = 'shirokuma-policy-candidate-'
TOKEN_PATTERN = r'[0-9a-f]{32}'
DNS_LABEL_PATTERN = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
# Exact predicate in the fixed native require-image-digest manifest, including
# its explicit repository path and optional registry port requirements.
IMAGE_DIGEST_PATTERN = r'^[a-zA-Z0-9._-]+(:[0-9]+)?(/[a-zA-Z0-9._-]+)+@sha256:[0-9a-f]{64}$'
CPU_PATTERN = r'(?:[1-9][0-9]{0,5}m|(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,3})?)'
MEMORY_PATTERN = r'([1-9][0-9]{0,17})(Ki|Mi|Gi|k|M|G)?'
ERROR_CODES = frozenset({'INPUT_TOO_LARGE', 'INPUT_ENCODING_INVALID', 'INPUT_JSON_INVALID',
                         'CANDIDATE_INVALID', 'RUN_TOKEN_INVALID'})
TRANSFORM_IDS = ('RENAME_FOR_DRY_RUN', 'SET_BASELINE_NAMESPACE', 'SET_RESTART_NEVER',
                 'DISABLE_SERVICE_ACCOUNT_TOKEN', 'SET_IMAGE_PULL_NEVER')


class CandidateError(ValueError):
    """The message and code are fixed; no candidate value is interpolated."""

    def __init__(self, code='CANDIDATE_INVALID'):
        self.code = code if code in ERROR_CODES else 'CANDIDATE_INVALID'
        super().__init__(self.code)


def require(condition, code='CANDIDATE_INVALID'):
    if not condition:
        raise CandidateError(code)


def _fields(value, required=(), optional=()):
    require(type(value) is dict and set(required) <= set(value) <= set(required) | set(optional))


def _label(value):
    require(type(value) is str and re.fullmatch(DNS_LABEL_PATTERN, value) is not None)


def _boolean(value):
    require(type(value) is bool)


def _image(value):
    # Reject URLs, credentials, whitespace, alternate digests and unbounded
    # text. Bare/tagged references and tagged digests are supported P06 inputs.
    require(type(value) is str and 1 <= len(value) <= 256 and value.isascii())
    parts = value.split('@')
    require(len(parts) <= 2)
    if len(parts) == 2:
        require(re.fullmatch(r'sha256:[0-9a-f]{64}', parts[1]) is not None)
    components = parts[0].split('/')
    require(1 <= len(components) <= 8 and all(components))
    if ':' in components[-1]:
        tail = components[-1].split(':')
        require(len(tail) == 2 and re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', tail[1]) is not None)
        components[-1] = tail[0]
    if len(components) > 1 and ':' in components[0]:
        registry = components[0].split(':')
        require(len(registry) == 2 and re.fullmatch(r'[1-9][0-9]{0,4}', registry[1]) is not None)
        require(int(registry[1]) <= 65535)
        components[0] = registry[0]
    require(all(re.fullmatch(r'[a-z0-9]+(?:[._-][a-z0-9]+)*', item) is not None for item in components))


def _quantity(value, resource):
    require(type(value) is str)
    if resource == 'cpu':
        require(re.fullmatch(CPU_PATTERN, value) is not None)
        # Integer millicores avoid both binary floats and ambient Decimal
        # precision/rounding settings. The supported grammar is always finite.
        if value.endswith('m'):
            amount = int(value[:-1])
        else:
            whole, _, fraction = value.partition('.')
            amount = int(whole) * 1000 + int(fraction.ljust(3, '0'))
        require(amount > 0)
        return amount
    matched = re.fullmatch(MEMORY_PATTERN, value)
    require(matched is not None)
    factor = {None: 1, 'Ki': 1024, 'Mi': 1024 ** 2, 'Gi': 1024 ** 3,
              'k': 1000, 'M': 1000 ** 2, 'G': 1000 ** 3}[matched[2]]
    amount = int(matched[1]) * factor
    require(amount <= 2 ** 63 - 1)
    return amount


def _resources(value):
    _fields(value, optional=('requests', 'limits'))
    amounts = {}
    for kind, fields in value.items():
        _fields(fields, optional=('cpu', 'memory'))
        for resource, raw in fields.items():
            amounts[(kind, resource)] = _quantity(raw, resource)
    for resource in ('cpu', 'memory'):
        request, limit = amounts.get(('requests', resource)), amounts.get(('limits', resource))
        # API defaulting would otherwise make a purported P04 case compliant.
        require(limit is None or request is not None)
        if request is not None and limit is not None:
            require(request <= limit)


def _container(value):
    _fields(value, ('name', 'image'), ('imagePullPolicy', 'securityContext', 'resources', 'ports'))
    _label(value['name'])
    _image(value['image'])
    if 'imagePullPolicy' in value:
        require(value['imagePullPolicy'] == 'Never')
    if 'securityContext' in value:
        context = value['securityContext']
        _fields(context, optional=('privileged', 'allowPrivilegeEscalation', 'readOnlyRootFilesystem'))
        for flag in context.values():
            _boolean(flag)
        # Kubernetes rejects this contradictory pair before policy evaluation.
        require(not (context.get('privileged') is True and context.get('allowPrivilegeEscalation') is False))
    if 'resources' in value:
        _resources(value['resources'])
    if 'ports' in value:
        require(type(value['ports']) is list and len(value['ports']) <= 1)
        for port in value['ports']:
            _fields(port, ('containerPort',), ('hostPort',))
            require(type(port['containerPort']) is int and 1 <= port['containerPort'] <= 65535)
            if 'hostPort' in port:
                require(type(port['hostPort']) is int and 0 <= port['hostPort'] <= 65535)


def validate(value):
    """Validate the supported Pod subset, returning an independent memory copy."""
    _fields(value, ('apiVersion', 'kind', 'metadata', 'spec'))
    require(value['apiVersion'] == 'v1' and value['kind'] == 'Pod')
    metadata, spec = value['metadata'], value['spec']
    _fields(metadata, ('name',), ('namespace',))
    _label(metadata['name'])
    if 'namespace' in metadata:
        require(metadata['namespace'] == NAMESPACE)
    _fields(spec, ('containers',), ('restartPolicy', 'automountServiceAccountToken', 'securityContext',
                                  'hostNetwork', 'hostIPC', 'hostPID', 'volumes'))
    require(type(spec['containers']) is list and len(spec['containers']) == 1)
    _container(spec['containers'][0])
    if 'restartPolicy' in spec:
        require(spec['restartPolicy'] == 'Never')
    if 'automountServiceAccountToken' in spec:
        require(spec['automountServiceAccountToken'] is False)
    for field in ('hostNetwork', 'hostIPC', 'hostPID'):
        if field in spec:
            _boolean(spec[field])
    if 'securityContext' in spec:
        context = spec['securityContext']
        _fields(context, optional=('runAsNonRoot', 'runAsUser', 'seccompProfile'))
        if 'runAsNonRoot' in context:
            require(context['runAsNonRoot'] is True)
        if 'runAsUser' in context:
            require(type(context['runAsUser']) is int and context['runAsUser'] == 10000)
        if 'seccompProfile' in context:
            _fields(context['seccompProfile'], ('type',))
            require(context['seccompProfile']['type'] == 'RuntimeDefault')
    if 'volumes' in spec:
        require(type(spec['volumes']) is list and len(spec['volumes']) <= 1)
        for volume in spec['volumes']:
            _fields(volume, ('name', 'hostPath'))
            _label(volume['name'])
            _fields(volume['hostPath'], ('path',))
            require(volume['hostPath']['path'] == '/tmp')
    if spec.get('hostNetwork') is True:
        for port in spec['containers'][0].get('ports', []):
            require(port.get('hostPort') == port['containerPort'])
    return copy.deepcopy(value)


def _pairs(pairs):
    result = {}
    for name, value in pairs:
        require(name not in result, 'INPUT_JSON_INVALID')
        result[name] = value
    return result


def _non_integer(_):
    raise CandidateError('INPUT_JSON_INVALID')


def _integer(value):
    require(len(value) <= 10, 'INPUT_JSON_INVALID')
    return int(value)


def parse(raw):
    """Accept one bounded UTF-8 JSON document; never accept a path or YAML."""
    require(type(raw) is bytes, 'INPUT_JSON_INVALID')
    require(len(raw) <= MAX_BYTES, 'INPUT_TOO_LARGE')
    try:
        text = raw.decode('utf-8', errors='strict')
    except UnicodeError:
        raise CandidateError('INPUT_ENCODING_INVALID') from None
    try:
        value = json.loads(text, object_pairs_hook=_pairs, parse_int=_integer,
                           parse_float=_non_integer, parse_constant=_non_integer)
    except CandidateError:
        raise
    except (ValueError, TypeError, RecursionError):
        raise CandidateError('INPUT_JSON_INVALID') from None
    return validate(value)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def _hash(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _candidate(body):
    spec, container = body['spec'], body['spec']['containers'][0]
    resources = container.get('resources', {})
    flags = {'privileged': container.get('securityContext', {}).get('privileged', False),
             'cpu_request_present': 'cpu' in resources.get('requests', {}),
             'memory_request_present': 'memory' in resources.get('requests', {}),
             'cpu_limit_present': 'cpu' in resources.get('limits', {}),
             'memory_limit_present': 'memory' in resources.get('limits', {}),
             'nonzero_host_port': any(port.get('hostPort', 0) != 0 for port in container.get('ports', [])),
             'image_digest_syntax': re.fullmatch(IMAGE_DIGEST_PATTERN, container['image']) is not None}
    return {'id': 'c01', 'kind': 'Pod', 'namespace_scope': 'protected', 'operation': 'server_dry_run',
            'features': {'containers': [flags], 'host_network': spec.get('hostNetwork', False),
                         'host_ipc': spec.get('hostIPC', False), 'host_pid': spec.get('hostPID', False),
                         'host_path': bool(spec.get('volumes'))}}


def _truth(candidate):
    features = candidate['features']
    container = features['containers'][0]
    predicates = (container['privileged'], any(features[k] for k in ('host_network', 'host_ipc', 'host_pid')),
                  features['host_path'], not all(container[k] for k in ('cpu_request_present', 'memory_request_present',
                    'cpu_limit_present', 'memory_limit_present')), container['nonzero_host_port'],
                  not container['image_digest_syntax'])
    return {'candidates': [{'id': 'c01', 'violation_ids': [f'P{i:02}' for i, violation in enumerate(predicates, 1) if violation],
                            'scope_limit_ids': []}], 'boundaries': []}


class PreparedCandidate:
    """Private in-memory carrier; serialize only receipt(), never the carrier."""

    __slots__ = ('_original', '_body', '_receipt')

    def __init__(self, raw, run_token):
        require(type(run_token) is str and re.fullmatch(TOKEN_PATTERN, run_token) is not None, 'RUN_TOKEN_INVALID')
        original = parse(raw)
        body, transformations = copy.deepcopy(original), []
        name = NAME_PREFIX + run_token
        if body['metadata']['name'] != name:
            transformations.append('RENAME_FOR_DRY_RUN')
        body['metadata']['name'] = name
        if 'namespace' not in body['metadata']:
            transformations.append('SET_BASELINE_NAMESPACE')
            body['metadata']['namespace'] = NAMESPACE
        spec = body['spec']
        for key, default, transform in [('restartPolicy', 'Never', 'SET_RESTART_NEVER'),
                ('automountServiceAccountToken', False, 'DISABLE_SERVICE_ACCOUNT_TOKEN')]:
            if key not in spec:
                transformations.append(transform)
                spec[key] = default
        container = spec['containers'][0]
        if 'imagePullPolicy' not in container:
            transformations.append('SET_IMAGE_PULL_NEVER')
            container['imagePullPolicy'] = 'Never'
        candidate = _candidate(body)
        self._original, self._body = original, body
        self._receipt = {'schema_version': 1, 'input_sha256': hashlib.sha256(raw).hexdigest(),
            'canonical_input_sha256': _hash(original), 'body_sha256': _hash(body), 'transform_ids': transformations,
            'candidate': candidate, 'host_truth': _truth(candidate)}

    def __repr__(self):
        return '<PreparedCandidate private>'

    def original(self):
        return copy.deepcopy(self._original)

    def body(self):
        return copy.deepcopy(self._body)

    def candidate(self):
        return copy.deepcopy(self._receipt['candidate'])

    def host_truth(self):
        return copy.deepcopy(self._receipt['host_truth'])

    def receipt(self):
        return copy.deepcopy(self._receipt)


def prepare(raw, run_token):
    return PreparedCandidate(raw, run_token)
