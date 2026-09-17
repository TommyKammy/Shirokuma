"""One in-memory Pod candidate, one guarded server dry-run; no retry or deletion."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid

from . import gatekeeper as G

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
    if type(stderr) is not str or len(stderr.encode()) > MAX_RESPONSE:
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
        self._receipt = dict(schema_version=1, result='not-started', server_dry_run=True,
            authority_granted=False, persisted_workloads=0, request_count=0, pending=None,
            admission='not-observed', expected_violation_ids=copy.deepcopy(self._truth['candidates'][0]['violation_ids']),
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
        require(type(ns) is dict and ns.get('kind') == 'Namespace' and ns.get('apiVersion') == 'v1', 'NAMESPACE_UNKNOWN')
        metadata = ns.get('metadata', {})
        labels = metadata.get('labels')
        require(metadata.get('name') == M.NAMESPACE and identity(metadata.get('uid'))
                and type(labels) is dict and labels.get('kubernetes.io/metadata.name') == M.NAMESPACE
                and metadata.get('deletionTimestamp') is None, 'NAMESPACE_IDENTITY_INVALID')
        limits = self.kube.json_request(['-n', M.NAMESPACE, 'get', 'limitranges', '-o', 'json'])
        require(type(limits) is dict and limits.get('apiVersion') == 'v1'
                and limits.get('kind') in {'List', 'LimitRangeList'} and limits.get('items') == [],
                'LIMIT_RANGE_UNSUPPORTED')
        rows = []
        for expected in self._items:
            current = self.kube.get(expected['kind'], expected['metadata']['name'])
            require(type(current) is dict, 'BASELINE_RESOURCE_MISSING')
            M.assert_owned(current, expected)
            meta = current['metadata']; generation = meta.get('generation')
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
            self._items = M.load_manifest(); self._sources = G._source_hashes()
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
            require(type(response.stdout) is str and type(response.stderr) is str
                    and len(response.stdout.encode()) <= MAX_RESPONSE and len(response.stderr.encode()) <= MAX_RESPONSE,
                    'DRY_RUN_RESPONSE_OVERSIZE')
            expected = self._receipt['expected_violation_ids']
            admission, reported = 'unknown', []
            if type(response.returncode) is int and response.returncode == 0:
                try:
                    value = json.loads(response.stdout)
                except (TypeError, ValueError):
                    value = None
                if (type(value) is dict and value.get('apiVersion') == 'v1' and value.get('kind') == 'Pod'
                        and value.get('metadata', {}).get('name') == self._body['metadata']['name']
                        and value['metadata'].get('namespace') == M.NAMESPACE and not response.stderr.strip()):
                    admission = 'allowed'
            elif type(response.returncode) is int and response.returncode > 0:
                reported = reported_denials(response.stderr, self._body['metadata']['name'], self._items)
                if reported and set(reported) <= set(expected):
                    admission = 'denied'
            self._receipt.update(admission=admission, reported_violation_ids=reported)
            self._receipt['absence_after'] = self._absent()
            self._receipt['baseline_after'] = self._guard()
            self._receipt['baseline_stable'] = True
            require(admission != 'unknown', 'DRY_RUN_UNKNOWN')
            require((admission == 'allowed') == (not expected), 'ADMISSION_EXPECTATION_MISMATCH')
            self._receipt.update(result='passed', pending=None, matched_host_expectation=True)
            progress(self.receipt())
        except BaseException as error:
            self._receipt.update(result='interrupted' if not isinstance(error, Exception) else 'failed',
                matched_host_expectation=False,
                error_code=error.code if isinstance(error, CandidateObservationError) else 'DRY_RUN_OBSERVATION_FAILED')
            if self._receipt['pending'] is not None:
                self._receipt['admission'] = 'unknown'
            if not sent:
                self._receipt['pending'] = None
            try:
                progress(self.receipt())
            except BaseException:
                pass
            if not isinstance(error, Exception):
                raise
            raise CandidateObservationError(self._receipt['error_code']) from None
        return self.receipt()
