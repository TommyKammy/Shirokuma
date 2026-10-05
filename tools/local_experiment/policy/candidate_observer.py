"""One in-memory Pod candidate, one guarded server dry-run; no retry or deletion."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import uuid

from . import candidate as P, gatekeeper as G

M = G.MANAGE
MAX_RESPONSE = 2 * 1024 * 1024


class CandidateObservationError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def require(condition, code):
    if not condition:
        raise CandidateObservationError(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest()


def unique_object(pairs):
    """Do not let JSON's last-key-wins decoding hide a competing Pod claim."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate response key')
        result[key] = value
    return result


def parse_allowed_response(raw):
    """Decode a dry-run success without accepting Python-only numeric values."""
    def finite_float(text):
        value = float(text)
        mantissa = re.split('[eE]', text, maxsplit=1)[0]
        if not math.isfinite(value) or (value == 0 and re.search('[1-9]', mantissa)):
            raise ValueError('non-representable response number')
        return value

    def invalid_constant(_):
        raise ValueError('non-JSON response constant')

    return json.loads(raw, object_pairs_hook=unique_object,
                      parse_float=finite_float, parse_constant=invalid_constant)


def identity(value):
    try:
        return type(value) is str and str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def reported_denials(stderr, name, items):
    """Recognize only complete known policy denials, including Invalid/422.

    The pinned server defaults an unspecified validation reason to Invalid and
    reports the first denial as a cause with an empty field. kubectl renders it
    as ``The pods "name" is invalid: : <cause>``; schema errors are not denials.
    https://raw.githubusercontent.com/kubernetes/apiserver/v0.33.4/pkg/admission/plugin/policy/validating/dispatcher.go
    https://raw.githubusercontent.com/kubernetes/kubectl/v0.36.2/pkg/cmd/util/helpers.go
    """
    try:
        bounded = type(stderr) is str and len(stderr.encode('utf-8')) <= MAX_RESPONSE
    except UnicodeError:
        bounded = False
    if not bounded:
        return []
    clauses = {}
    invalid_clauses = {}
    for item in items:
        if item['kind'] == M.POLICY and G.RULE_IDS[item['metadata']['name']] in {f'P{i:02}' for i in range(1, 7)}:
            policy = item['metadata']['name']
            message = item['spec']['validations'][0]['message']
            for quote in ("'", '"'):
                clause = f'ValidatingAdmissionPolicy {quote}{policy}{quote} with binding {quote}{policy}{quote} denied request: {message}'
                clauses[clause] = G.RULE_IDS[policy]
                if quote == "'":
                    invalid_clauses[clause] = G.RULE_IDS[policy]
    invalid_prefix = r'The pods "' + re.escape(name) + r'" is invalid: : '
    invalid = re.fullmatch(invalid_prefix + r'(.+?)(?:\r?\n)?', stderr)
    if invalid:
        rule = invalid_clauses.get(invalid.group(1))
        return [rule] if rule is not None else []
    prefix = r'Error from server \(Forbidden\): (?:error when creating "(?:STDIN|-)": )?pods "' + re.escape(name) + r'" is forbidden: '
    matched = re.fullmatch(prefix + r'(.+?)\s*', stderr, flags=re.DOTALL)
    if not matched:
        return []
    rest = matched.group(1)
    if rest.startswith('[') and rest.endswith(']'):
        rest = rest[1:-1]
    found = []
    while rest:
        clause = next((text for text in clauses if rest.startswith(text)), None)
        if clause is None or clauses[clause] in found:
            return []
        found.append(clauses[clause]); rest = rest[len(clause):]
        if rest:
            separator = next((s for s in (', ', '\r\n', '\n') if rest.startswith(s)), None)
            if separator is None:
                return []
            rest = rest[len(separator):]
            if not rest:
                return []
    return sorted(found)


def allowed_pod_matches(value, body):
    """Check the effective dry-run Pod, not just its name; allow API defaults.

    This is deliberately not a claim that no other admission mutation occurred.
    Unsupported changes to the constrained policy features fail closed.
    """
    if (type(value) is not dict or set(value) - {'apiVersion', 'kind', 'metadata', 'spec', 'status'} or
            value.get('apiVersion') != 'v1' or
            value.get('kind') != 'Pod' or
            ('status' in value and value['status'] != {})):
        return False
    meta, spec = value.get('metadata'), value.get('spec')
    if type(meta) is not dict or type(spec) is not dict or any(
            meta.get(key) != body['metadata'][key] for key in ('name', 'namespace')):
        return False
    # A server dry-run must not be mistaken for a persisted Pod. Accept only
    # empty API identity defaults, never a live UID/version or managed owner.
    if (set(meta) - {'name', 'namespace', 'uid', 'resourceVersion', 'generation',
                     'creationTimestamp', 'managedFields', 'ownerReferences',
                     'labels', 'annotations', 'finalizers', 'generateName',
                     'deletionTimestamp', 'deletionGracePeriodSeconds'} or
            meta.get('uid', '') != '' or meta.get('resourceVersion', '') != '' or
            type(meta.get('generation', 0)) is not int or meta.get('generation', 0) != 0 or
            meta.get('creationTimestamp') is not None or
            meta.get('managedFields') not in (None, [])):
        return False
    # A controller owner can change lifecycle; an injected pull secret changes
    # the credentials used to fetch the image even when image text is unchanged.
    if (meta.get('ownerReferences') not in (None, []) or
            meta.get('labels') not in (None, {}) or
            meta.get('annotations') not in (None, {}) or
            meta.get('finalizers') not in (None, []) or
            meta.get('generateName', '') != '' or
            meta.get('deletionTimestamp') is not None or
            meta.get('deletionGracePeriodSeconds') is not None or
            spec.get('imagePullSecrets') not in (None, [])):
        return False
    original = body['spec']; containers = spec.get('containers')
    # Reject newly introduced Pod controls by default. Explicitly handle
    # harmless API defaults below instead of silently trusting new fields.
    if set(spec) - {
            'containers', 'securityContext', 'serviceAccountName', 'serviceAccount',
            'nodeName', 'nodeSelector', 'tolerations', 'affinity', 'schedulerName',
            'runtimeClassName', 'priorityClassName', 'priority', 'preemptionPolicy',
            'shareProcessNamespace', 'hostAliases', 'topologySpreadConstraints',
            'readinessGates', 'overhead', 'activeDeadlineSeconds', 'dnsPolicy',
            'dnsConfig', 'hostname', 'subdomain', 'enableServiceLinks',
            'setHostnameAsFQDN', 'initContainers', 'ephemeralContainers', 'volumes',
            'hostNetwork', 'hostIPC', 'hostPID', 'automountServiceAccountToken',
            'restartPolicy', 'imagePullSecrets', 'terminationGracePeriodSeconds',
            'hostUsers', 'resourceClaims'}:
        return False
    if (type(spec.get('terminationGracePeriodSeconds', 30)) is not int or
            spec.get('terminationGracePeriodSeconds', 30) != 30 or
            spec.get('hostUsers', True) is not True or
            spec.get('resourceClaims') not in (None, [])):
        return False
    # Pod-level identity/security settings are separate from the container
    # securityContext. Never turn a mutated runAsUser or seccomp profile into an
    # allowed response merely because the container itself is unchanged.
    pod_context = spec.get('securityContext')
    submitted_context = original.get('securityContext')
    if submitted_context is None:
        if pod_context not in (None, {}):
            return False
    elif (type(pod_context) is not dict or
          json.dumps(pod_context, sort_keys=True) != json.dumps(submitted_context, sort_keys=True)):
        return False
    # The submitted subset cannot choose an identity or placement. An allowed
    # response must not silently acquire either, even when policy CEL passes.
    if (spec.get('serviceAccountName', 'default') != 'default' or
            spec.get('serviceAccount', 'default') != 'default' or
            spec.get('nodeName', '') != '' or
            spec.get('nodeSelector') not in (None, {}) or
            spec.get('tolerations') not in (None, []) or
            spec.get('affinity') not in (None, {}) or
            spec.get('schedulerName', 'default-scheduler') != 'default-scheduler' or
            spec.get('runtimeClassName', '') != '' or
            spec.get('priorityClassName', '') != '' or
            type(spec.get('priority', 0)) is not int or spec.get('priority', 0) != 0 or
            spec.get('preemptionPolicy', 'PreemptLowerPriority') != 'PreemptLowerPriority'):
        return False
    # Sharing the process namespace exposes sibling processes; host aliases
    # can silently redirect names resolved by the candidate's container.
    if (spec.get('shareProcessNamespace', False) is not False or
            spec.get('hostAliases') not in (None, [])):
        return False
    # None of these scheduling/lifecycle controls is submitted by the candidate.
    # An injected topology rule, readiness gate, runtime overhead or deadline
    # changes the effective Pod even when the policy CEL checks still pass.
    if (spec.get('topologySpreadConstraints') not in (None, []) or
            spec.get('readinessGates') not in (None, []) or
            spec.get('overhead') not in (None, {}) or
            spec.get('activeDeadlineSeconds') is not None):
        return False
    # The submitted Pod cannot select DNS or host identity. Allow only the
    # inert API defaults, not a resolver, service-link or FQDN mutation.
    if (spec.get('dnsPolicy', 'ClusterFirst') != 'ClusterFirst' or
            spec.get('dnsConfig') not in (None, {}) or
            spec.get('hostname', '') != '' or spec.get('subdomain', '') != '' or
            spec.get('enableServiceLinks', True) is not True or
            spec.get('setHostnameAsFQDN', False) is not False):
        return False
    if (type(containers) is not list or len(containers) != 1 or
            any(spec.get(key) not in (None, []) for key in ('initContainers', 'ephemeralContainers', 'volumes')) or
            any(spec.get(key, False) is not False for key in ('hostNetwork', 'hostIPC', 'hostPID')) or
            spec.get('automountServiceAccountToken') is not False or spec.get('restartPolicy') != 'Never'):
        return False
    container, submitted = containers[0], original['containers'][0]
    if type(container) is not dict or any(container.get(key) != submitted[key] for key in ('name', 'image', 'imagePullPolicy')):
        return False
    # Kubernetes may spell out these inert defaults. Other container fields
    # (including lifecycle hooks and workingDir) are not in the submitted Pod
    # subset and must not silently gain execution semantics on an allow.
    if (set(container) - {'name', 'image', 'imagePullPolicy', 'securityContext',
                          'resources', 'ports', 'env', 'envFrom', 'command', 'args',
                          'volumeMounts', 'terminationMessagePath', 'terminationMessagePolicy',
                          'stdin', 'stdinOnce', 'tty', 'workingDir'} or
            container.get('terminationMessagePath', '/dev/termination-log') != '/dev/termination-log' or
            container.get('terminationMessagePolicy', 'File') != 'File' or
            container.get('workingDir', '') != '' or
            any(container.get(key, False) is not False for key in ('stdin', 'stdinOnce', 'tty'))):
        return False
    context, resources = container.get('securityContext', {}), container.get('resources', {})
    if (type(context) is not dict or type(resources) is not dict or
            set(resources) != {'requests', 'limits'} or
            context.get('privileged', False) is not False):
        return False
    submitted_security = submitted.get('securityContext', {})
    # Only the harmless explicit false default may appear beyond the submitted
    # container context. Other injected settings can change execution identity
    # or privileges even when the named policy checks still pass.
    if (any(key not in context or
            json.dumps(context[key], sort_keys=True) != json.dumps(raw, sort_keys=True)
            for key, raw in submitted_security.items()) or
            any(key not in submitted_security and (key != 'privileged' or value is not False)
                for key, value in context.items()) or
            any(container.get(key) not in (None, []) for key in ('env', 'envFrom', 'command', 'args', 'volumeMounts'))):
        return False
    requests, limits = resources.get('requests'), resources.get('limits')
    if type(requests) is not dict or type(limits) is not dict:
        return False
    expected = submitted['resources']
    try:
        for kind, observed in (('requests', requests), ('limits', limits)):
            if set(observed) != set(expected[kind]) or any(
                    P._quantity(observed[key], key) != P._quantity(raw, key)
                    for key, raw in expected[kind].items()):
                return False
    except (P.CandidateError, KeyError, TypeError, ValueError):
        return False
    ports = container.get('ports', [])
    if type(ports) is not list or len(ports) != len(submitted.get('ports', [])):
        return False
    for current, source in zip(ports, submitted.get('ports', [])):
        if (type(current) is not dict or
                set(current) - {'containerPort', 'hostPort', 'protocol', 'name', 'hostIP'} or
                type(current.get('containerPort')) is not int or
                current['containerPort'] != source['containerPort'] or
                type(current.get('hostPort', 0)) is not int or
                current.get('hostPort', 0) != source.get('hostPort', 0) or
                current.get('protocol', 'TCP') != 'TCP' or
                current.get('name', '') != '' or current.get('hostIP', '') != ''):
            return False
    return True


class CandidateObserver:
    def __init__(self, prepared, kube=None):
        self.prepared = prepared
        self.kube = M.Kubernetes() if kube is None else kube
        self._body = prepared.body()
        self._body_sha = digest(self._body)
        self._candidate = prepared.candidate()
        self._truth = prepared.host_truth()
        self._initial = None
        self._observe_attempted = False
        self._items = None
        self._sources = None
        self._receipt = dict(schema_version=2, result='not-started', server_dry_run=True,
            authority_granted=False, persisted_workloads=0, request_count=0, pending=None,
            admission='not-observed', response_content_check='not-checked',
            expected_violation_ids=copy.deepcopy(self._truth['candidates'][0]['violation_ids']),
            reported_violation_ids=[], matched_host_expectation=False, absence_before=None,
            absence_after=None, baseline_stable=None, error_code=None, body_sha256=self._body_sha,
            baseline_before=None, baseline_after=None, policy_sources=None)

    def receipt(self):
        return copy.deepcopy(self._receipt)

    def _unchanged_body(self):
        require(digest(self._body) == self._body_sha and self.prepared.body() == self._body
                and self.prepared.candidate() == self._candidate and self.prepared.host_truth() == self._truth
                and self.prepared.receipt()['body_sha256'] == self._body_sha,
                'CANDIDATE_CHANGED')
        metadata = self._body.get('metadata', {})
        require(self._body.get('apiVersion') == 'v1' and self._body.get('kind') == 'Pod'
                and metadata.get('namespace') == M.NAMESPACE
                and re.fullmatch(r'shirokuma-policy-candidate-[0-9a-f]{32}', metadata.get('name', '')),
                'CANDIDATE_SCOPE_INVALID')

    def _baseline(self):
        ns = self.kube.json_request(['get', 'namespace', M.NAMESPACE, '-o', 'json'])
        require(type(ns) is dict and ns.get('kind') == 'Namespace' and ns.get('apiVersion') == 'v1'
                and not any(type(key) is str and key.lower() in {'kind', 'apiversion', 'metadata'}
                            and key not in {'kind', 'apiVersion', 'metadata'} for key in ns),
                'NAMESPACE_UNKNOWN')
        metadata = ns.get('metadata', {})
        labels = metadata.get('labels')
        require(type(metadata) is dict
                and not any(type(key) is str and key.lower() in
                            {'name', 'uid', 'labels', 'deletiontimestamp'}
                            and key not in {'name', 'uid', 'labels', 'deletionTimestamp'} for key in metadata)
                and metadata.get('name') == M.NAMESPACE and identity(metadata.get('uid'))
                and type(labels) is dict and labels.get('kubernetes.io/metadata.name') == M.NAMESPACE
                and not any(type(key) is str and key.lower() == 'kubernetes.io/metadata.name'
                            and key != 'kubernetes.io/metadata.name' for key in labels)
                and metadata.get('deletionTimestamp') is None, 'NAMESPACE_IDENTITY_INVALID')
        limits = self.kube.json_request(['-n', M.NAMESPACE, 'get', 'limitranges', '-o', 'json'])
        limit_metadata = limits.get('metadata', {}) if type(limits) is dict else None
        require(type(limits) is dict and limits.get('apiVersion') == 'v1'
                and limits.get('kind') in {'List', 'LimitRangeList'} and limits.get('items') == []
                and not any(type(key) is str and key.lower() in
                            {'apiversion', 'kind', 'items', 'metadata'}
                            and key not in {'apiVersion', 'kind', 'items', 'metadata'} for key in limits)
                and type(limit_metadata) is dict
                and not any(type(key) is str and key.lower() in {'continue', 'remainingitemcount'}
                            and key not in {'continue', 'remainingItemCount'} for key in limit_metadata)
                and type(limit_metadata.get('continue', '')) is str
                and limit_metadata.get('continue', '') == ''
                and (('remainingItemCount' not in limit_metadata)
                     or (type(limit_metadata['remainingItemCount']) is int
                         and limit_metadata['remainingItemCount'] == 0)),
                'LIMIT_RANGE_UNSUPPORTED')
        rows = []
        for expected in self._items:
            current = self.kube.get(expected['kind'], expected['metadata']['name'])
            require(type(current) is dict, 'BASELINE_RESOURCE_MISSING')
            envelope_keys = {'apiversion', 'kind', 'metadata', 'spec', 'status'}
            canonical_envelope = {'apiVersion', 'kind', 'metadata', 'spec', 'status'}
            require(not any(type(key) is str and key.lower() in envelope_keys
                            and key not in canonical_envelope for key in current),
                    'BASELINE_IDENTITY_INVALID')
            resource_meta = current.get('metadata')
            identity_keys = {'name', 'uid', 'generation', 'labels', 'annotations', 'deletiontimestamp'}
            canonical_keys = {'name', 'uid', 'generation', 'labels', 'annotations', 'deletionTimestamp'}
            require(type(resource_meta) is dict and not any(
                type(key) is str and key.lower() in identity_keys and key not in canonical_keys
                for key in resource_meta), 'BASELINE_IDENTITY_INVALID')
            M.assert_owned(current, expected)
            meta = resource_meta; generation = meta.get('generation')
            require(identity(meta.get('uid')) and type(generation) is int and generation > 0
                    and meta.get('deletionTimestamp') is None, 'BASELINE_IDENTITY_INVALID')
            if expected['kind'] == M.POLICY:
                require(M.typecheck(current) == 'complete', 'BASELINE_NOT_READY')
            rows.append(dict(kind=expected['kind'], name=expected['metadata']['name'], uid=meta['uid'],
                generation=generation, spec_sha256=digest(M.canonical_spec(current['spec'])),
                owner_sha256=digest({'labels':meta.get('labels', {}), 'annotations':meta.get('annotations', {})})))
        require(len(rows) == 16 and len({row['uid'] for row in rows}) == 16, 'BASELINE_IDENTITY_INVALID')
        return dict(namespace_uid=metadata['uid'], namespace_labels_sha256=digest(labels),
                    namespace_selector={'kubernetes.io/metadata.name':M.NAMESPACE},
                    limit_ranges=[], resources=rows)

    def _absent(self):
        value = self.kube.json_request(['-n', M.NAMESPACE, 'get', 'pod', self._body['metadata']['name'],
                                        '--ignore-not-found', '-o', 'json'])
        require(value is None, 'CANDIDATE_NAME_PRESENT')
        return True

    def observe(self):
        require(not self._observe_attempted, 'OBSERVATION_ALREADY_ATTEMPTED')
        self._observe_attempted = True
        try:
            self._unchanged_body()
            self._items, manifest_digest = M.load_manifest_snapshot()
            self._sources = G._source_hashes(manifest_digest)
            self._initial = self._baseline()
            self._receipt.update(baseline_before=self._initial, policy_sources=self._sources)
            self._receipt['absence_before'] = self._absent()
            self._receipt['result'] = 'ready'
        except BaseException as error:
            self._receipt.update(result='failed' if isinstance(error,Exception) else 'interrupted',
                error_code=error.code if isinstance(error,CandidateObservationError) else 'BASELINE_OBSERVATION_FAILED')
            raise
        return copy.deepcopy(dict(schema_version=1, authority='advisory_only', candidate=self._candidate,
                    baseline=self._initial, policy_sources=self._sources, body_sha256=self._body_sha))

    def _guard(self):
        self._unchanged_body()
        require(M.load_manifest() == self._items and G._source_hashes() == self._sources, 'POLICY_SOURCE_CHANGED')
        current = self._baseline()
        require(current == self._initial, 'BASELINE_CHANGED')
        return current

    def verify(self, *, progress):
        require(callable(progress) and self._receipt['result'] == 'ready', 'VERIFICATION_NOT_READY')
        self._receipt['result'] = 'running'
        sent = False
        try:
            self._guard(); self._absent()
            self._receipt['pending'] = dict(body_sha256=self._body_sha, outcome='unknown')
            progress(self.receipt())  # Caller must durably fsync this intent before returning.
            sent = True; self._receipt['request_count'] = 1
            response = self.kube.request(['-n', M.NAMESPACE, 'create', '--dry-run=server', '-f', '-', '-o', 'json'],
                                         copy.deepcopy(self._body))
            try:
                bounded = (type(response.stdout) is str and type(response.stderr) is str
                           and len(response.stdout.encode('utf-8')) <= MAX_RESPONSE
                           and len(response.stderr.encode('utf-8')) <= MAX_RESPONSE)
            except UnicodeError:
                bounded = False
            require(bounded, 'DRY_RUN_RESPONSE_OVERSIZE')
            expected = self._receipt['expected_violation_ids']
            admission, reported = 'unknown', []
            if type(response.returncode) is int and response.returncode == 0:
                try:
                    value = parse_allowed_response(response.stdout)
                except (TypeError, ValueError):
                    value = None
                if response.stderr == '' and allowed_pod_matches(value, self._body):
                    admission = 'allowed'
            elif type(response.returncode) is int and response.returncode > 0 and response.stdout == '':
                # A kubectl denial has no successful object on stdout. A mixed
                # success/error stream cannot establish the server's outcome.
                reported = reported_denials(response.stderr, self._body['metadata']['name'], self._items)
                if reported and set(reported) <= set(expected):
                    admission = 'denied'
            self._receipt.update(admission=admission, reported_violation_ids=reported,
                response_content_check='matched' if admission == 'allowed' else
                    'not-applicable' if admission == 'denied' else 'not-checked')
            self._receipt['absence_after'] = self._absent()
            self._receipt['baseline_after'] = self._guard()
            self._receipt['baseline_stable'] = True
            require(admission != 'unknown', 'DRY_RUN_UNKNOWN')
            require((admission == 'allowed') == (not expected), 'ADMISSION_EXPECTATION_MISMATCH')
            self._receipt.update(result='passed', pending=None, matched_host_expectation=True)
            progress(self.receipt())
        except BaseException as error:
            # The final progress callback can fail after the dry-run was sent.
            # Do not let the tentative passed receipt erase the durable intent
            # or advertise a known admission when final persistence is unknown.
            if sent and self._receipt['pending'] is None:
                self._receipt['pending'] = dict(body_sha256=self._body_sha, outcome='unknown')
            self._receipt.update(result='interrupted' if not isinstance(error, Exception) else 'failed',
                matched_host_expectation=False,
                response_content_check='not-checked',
                error_code=error.code if isinstance(error, CandidateObservationError) else 'DRY_RUN_OBSERVATION_FAILED')
            if not sent:
                self._receipt['pending'] = None
                self._receipt['admission'] = 'not-observed'
            elif self._receipt['pending'] is not None:
                self._receipt['admission'] = 'unknown'
            try:
                progress(self.receipt())
            except BaseException:
                pass
            if not isinstance(error, Exception):
                raise
            raise CandidateObservationError(self._receipt['error_code']) from None
        return self.receipt()
