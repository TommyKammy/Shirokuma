#!/usr/bin/env python3
"""Read-only current-name inventory for one archived, completed XS session.

Neither the saved owner's cleanup claim nor an empty current inventory proves
when cleanup occurred. Never replay an unknown request or mutate a resource.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import unicodedata
import uuid

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.local_experiment.local_experiment import ExperimentError as ContextError, check_context
from tools.local_experiment.warehouse_lifecycle import manifest as M, status as S, status_completion as C
from tools.local_experiment.warehouse_lifecycle import support as W

KINDS = {
    'NetworkPolicy': ('networkpolicies', ('-net-deny', '-net-gateway', '-net-xs', '-net-s')),
    'ConfigMap': ('configmaps', ('-config',)),
    'Secret': ('secrets', ('-secret',)),
    'Service': ('services', ('-xs', '-s')),
    'Pod': ('pods', ('-gateway',)),
    'Deployment': ('deployments', ('-xs', '-s')),
}
LABELLED = 'networkpolicies,configmaps,secrets,services,pods,deployments,replicasets,endpointslices'
MAX_SECONDS = 90
OWNER = M.OWNER
DNS_LABEL = re.compile(r'(?:[a-z0-9]|[a-z0-9][a-z0-9-]{0,61}[a-z0-9])\Z')
# kubectl fetches objects internally, but its stdout contains *only* these
# metadata fields: never emit Secret.data or Pod specs to this process/log.
ROW = ('{{printf "%s\\t%s\\t%s\\t%s\\t" .kind .metadata.name '
       '.metadata.namespace .metadata.uid}}'
       '{{with .metadata.labels}}{{with index . "' + OWNER + '"}}{{.}}{{end}}{{end}}'
       '{{printf "\\t"}}'
       '{{with .metadata.labels}}{{with index . "kubernetes.io/service-name"}}{{.}}{{end}}{{end}}'
       '{{printf "\\n"}}')
# A successful named GET with an object but no metadata is malformed, not an
# absent resource. --ignore-not-found prints nothing only for a missing object.
ONE = '{{if .}}{{if .metadata}}' + ROW + '{{else}}INVALID{{end}}{{end}}'
MANY = ('{{printf "%s\\t%s\\t%d\\t" .apiVersion .kind (len .items)}}'
             '{{if or (eq (printf "%T" .items) "<nil>") (eq (printf "%T" .metadata) "<nil>") (not .metadata.resourceVersion)}}INVALID{{else}}'
           '{{if or .metadata.continue .metadata.remainingItemCount}}TRUNCATED{{else}}COMPLETE{{end}}{{end}}'
         '{{printf "\\t%s" .metadata.resourceVersion}}'
         '{{printf "\\n"}}{{range .items}}' + ROW + '{{end}}')


class RuntimeStatusError(ValueError):
    pass


def require(ok):
    if not ok:
        raise RuntimeStatusError('RUNTIME_OBSERVATION_UNVERIFIED')


def identity(value, *, kind=None, name=None, label=None):
    require(type(value) is dict and type(value.get('kind')) is str and
            type(value.get('metadata')) is dict)
    meta = value['metadata']
    uid = meta.get('uid')
    require(type(uid) is str and canonical_uuid(uid) and
            meta.get('namespace') == W.NAMESPACE and
             type(meta.get('name')) is str and DNS_LABEL.fullmatch(meta['name']) is not None and
            type(meta.get('labels')) is dict)
    require(kind is None or value['kind'] == kind)
    require(name is None or meta['name'] == name)
    require(label is None or meta['labels'].get(M.OWNER) == label)
    return (value['kind'], meta['name'], uid)


def canonical_uuid(value):
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def row(line):
    fields = line.split('\t')
    require(len(fields) == 6)
    kind, name, namespace, uid, owner, service = fields
    require(owner == '' or re.fullmatch(r'[0-9a-f]{32}', owner) is not None)
    require(service == '' or DNS_LABEL.fullmatch(service) is not None)
    return {'kind': kind, 'metadata': {'name': name, 'namespace': namespace,
            'uid': uid, 'labels': {**({OWNER: owner} if owner else {}),
                                   **({'kubernetes.io/service-name': service} if service else {})}}}


class ReadOnlyKubernetes:
    def _get(self, resource, *args, template):
        raw = W.command(['kubectl', '--context', W.CONTEXT, '-n', W.NAMESPACE,
                         '--request-timeout=10s', 'get', resource, *args,
                         '--chunk-size=0', '-o=go-template=' + template],
                        timeout=15, output_limit=65536)
        require(type(raw) is str and len(raw) <= 65536)
        return raw

    def get(self, resource, name):
        raw = self._get(resource, name, '--ignore-not-found=true', template=ONE)
        if raw == '':
            return None
        require(raw.endswith('\n') and raw.count('\n') == 1)
        return row(raw[:-1])

    def _list(self, resource, selector, limit, kind):
        raw = self._get(resource, '-l', selector, template=MANY)
        # The Go template emits LF-delimited metadata rows only. Do not let
        # splitlines() normalize other separators into a plausible empty list.
        require(raw.endswith('\n') and '\r' not in raw and len(raw) <= 65536)
        lines = raw[:-1].split('\n')
        header = lines.pop(0).split('\t')
        # kubectl's generic printer wraps a single-kind EndpointSlice selector
        # in List on this client; each item is still checked as EndpointSlice.
        expected_headers = ({('v1', 'List'), ('discovery.k8s.io/v1', 'EndpointSliceList')}
                            if kind == 'EndpointSliceList' else {('v1', 'List')})
        require(len(header) == 5 and tuple(header[:2]) in expected_headers and
                header[3] == 'COMPLETE' and
                 0 < len(header[4].encode('utf-8')) <= 1024 and
                 all(not char.isspace() and not unicodedata.category(char).startswith('C') for char in header[4]) and
                 len(lines) <= limit and len(header[2]) <= len(str(limit)) and
                 re.fullmatch(r'(0|[1-9][0-9]*)', header[2]) is not None and
                 int(header[2]) == len(lines))
        return {'kind': kind, 'items': [row(line) for line in lines]}

    def labelled(self, token):
        return self._list(LABELLED, OWNER + '=' + token, 64, 'List')

    def endpoints(self, service):
        # The selector comes from the API; emit just this label, not endpoint
        # addresses. A list selector itself is not evidence that rows match it.
        return self._list('endpointslices', 'kubernetes.io/service-name=' + service, 4,
                          'EndpointSliceList')


def listed(value, limit, kind):
    require(type(value) is dict and value.get('kind') == kind and
            type(value.get('items')) is list and len(value['items']) <= limit)
    return value['items']


def snapshot(token, kube, deadline):
    prefix = M.name(token)
    named = []
    named_values = []
    for kind, (resource, suffixes) in KINDS.items():
        for suffix in suffixes:
            require(time.monotonic() < deadline)
            name = prefix + suffix
            value = kube.get(resource, name)
            if value is not None:
                row = identity(value, kind=kind, name=name)
                named.append(row)
                named_values.append(value)
    require(time.monotonic() < deadline)
    owned_values = listed(kube.labelled(token), 64, 'List')
    owned = [identity(value, label=token) for value in owned_values]
    require(all(kind in KINDS or kind in ('ReplicaSet', 'EndpointSlice') for kind, _, _ in owned))
    endpoints = []
    endpoint_values = []
    for suffix in ('-xs', '-s'):
        require(time.monotonic() < deadline)
        service = prefix + suffix
        for value in listed(kube.endpoints(service), 4, 'EndpointSliceList'):
            row = identity(value, kind='EndpointSlice')
            require(value['metadata']['labels'].get('kubernetes.io/service-name') == service)
            endpoints.append(row)
            endpoint_values.append(value)
    require(time.monotonic() < deadline)
    require(all(len(values) == len(set(values)) for values in (named, owned, endpoints)))
    # The three API projections are separate reads. A resource present in more
    # than one projection must retain its identity within this round as well.
    identities = {}
    uid_owners = {}
    projected_labels = {}
    for values in (named_values, owned_values, endpoint_values):
        for value in values:
            kind, name, uid = identity(value)
            key = kind, name
            require(key not in identities or identities[key] == uid)
            # Repeated projections may refer to the same object, but a UID
            # cannot identify two different resources in one observation.
            require(uid not in uid_owners or uid_owners[uid] == key)
            identities[key] = uid
            uid_owners[uid] = key
            labels = value['metadata']['labels']
            projected = (labels.get(M.OWNER, ''), labels.get('kubernetes.io/service-name', ''))
            require(key not in projected_labels or projected_labels[key] == projected)
            projected_labels[key] = projected
        require(len({(value['kind'], value['metadata']['name']) for value in values}) == len(values))
    # A hash correlates two bounded snapshots; it is not independent attestation.
    rows = [sorted(values) for values in (named, owned, endpoints)] + [sorted(projected_labels.items())]
    return {'named_present': len(named), 'labelled_present': len(owned),
            'service_endpoints_present': len(endpoints),
            'snapshot_sha256': hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()}


def observe(token, kube, *, deadline=None):
    require(type(token) is str and re.fullmatch(r'[0-9a-f]{32}', token) is not None)
    deadline = time.monotonic() + MAX_SECONDS if deadline is None else deadline
    before = datetime.now(timezone.utc).isoformat()
    first = snapshot(token, kube, deadline)
    second = snapshot(token, kube, deadline)
    # The last projection may finish within its own timeout after the overall
    # observation deadline; never publish a late two-round result.
    require(time.monotonic() < deadline)
    require(first == second)
    after = datetime.now(timezone.utc).isoformat()
    report = {'schema_version': 1, 'session_id': token, 'observation': 'current-kubernetes-two-reads',
            'scope': 'fixed-names-owner-labels-and-two-service-endpoints-in-shirokuma-dev',
            'observed_from': before, 'observed_to': after,
            'current_state': 'scoped-absence-observed' if not any(first[key] for key in
                             ('named_present', 'labelled_present', 'service_endpoints_present'))
                             else 'scoped-resources-present', **first,
             'backend_query_state': 'unknown', 'historical_cleanup_proven': False,
             'all_namespace_resources_checked': False}
    require(time.monotonic() < deadline)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--session')
    source.add_argument('--session-from-env', action='store_true')
    args = parser.parse_args(argv)
    try:
        token = os.environ.get('WAREHOUSE_LIFECYCLE_RUN_TOKEN') if args.session_from_env else args.session
        require(type(token) is str and re.fullmatch(r'[0-9a-f]{32}', token) is not None)
        # The context preflight and metadata GETs must read the same kubeconfig.
        require('KUBECONFIG' not in os.environ)
        check_context()
        saved = S.read(token)
        completion = C.read(token)
        require(saved['journal_sha256'] == completion['journal_sha256'] and
                completion['recorded_completion'] == 'correlated-saved-claim')
        deadline = time.monotonic() + MAX_SECONDS
        report = observe(token, ReadOnlyKubernetes(), deadline=deadline)
        require(saved == S.read(token) and completion == C.read(token))
        # The saved-claim recheck is part of the same bounded observation, not
        # permission to publish an inventory that has already aged out.
        require(time.monotonic() < deadline)
        report['saved_completion'] = 'correlated-saved-claim'
    except (RuntimeStatusError, S.StatusError, W.ExperimentError, ContextError,
            OSError, TypeError, KeyError, ValueError):
        print('RUNTIME_OBSERVATION_UNVERIFIED', file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
