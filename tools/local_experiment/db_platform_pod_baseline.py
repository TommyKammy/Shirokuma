"""Bounded namespace-wide actual Pod SPEC declarations, advisory only."""
import json
import re
import selectors
import subprocess
import time

from db_platform_observations import _authority, _now, SOURCE_ERRORS
from db_platform_foundation_baseline import _foundation
from db_platform_workload_baseline import _boolean, _uid, _drop_all, _nonzero
import logical_foundation
from warehouse_operations import OperationError

MAX_STDOUT_BYTES = 65536
MAX_DTO_BYTES = 65536
MAX_PODS = 64
MAX_CONTAINERS = 256
KINDS = {'C': 'container', 'I': 'init_container', 'E': 'ephemeral_container'}
# Explicit list and per-Pod framing rejects continuation and incomplete output.
JSONPATH = (r'jsonpath=L{"\t"}{.apiVersion}{"\t"}{.kind}{"\t"}'
            r'{.metadata.continue}{"\t"}{.metadata.remainingItemCount}{"\n"}'
            r'{range .items[*]}P{"\t"}{.apiVersion}{"\t"}{.kind}'
            r'{"\t"}{.metadata.namespace}{"\t"}{.metadata.name}{"\t"}{.metadata.uid}'
            r'{"\t"}{.spec.securityContext.runAsNonRoot}'
            r'{"\t"}{.spec.securityContext.runAsUser}{"\n"}')
for _field, _marker in (('containers', 'C'), ('initContainers', 'I'), ('ephemeralContainers', 'E')):
    JSONPATH += (r'{range .spec.' + _field + r'[*]}' + _marker +
                 r'{"\t"}{.name}{"\t"}{.securityContext.readOnlyRootFilesystem}'
                 r'{"\t"}{.securityContext.runAsNonRoot}{"\t"}{.securityContext.runAsUser}'
                 r'{"\t"}{.securityContext.capabilities.drop}'
                 r'{"\t"}{.resources.limits.cpu}{"\t"}{.resources.limits.memory}{"\n"}{end}')
JSONPATH += r'{"Q\n"}{end}{"Z\n"}'


def _name(value, *, subdomain=False):
    maximum = 253 if subdomain else 63
    return (type(value) is str and 0 < len(value) <= maximum
            and all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', label, re.ASCII)
                    and (subdomain or len(label) <= 63) for label in value.split('.'))
            and (subdomain or '.' not in value))


def _parse(raw, namespace):
    if len(raw) > MAX_STDOUT_BYTES or not _name(namespace):
        raise ValueError()
    lines = raw.decode('ascii').split('\n')
    if lines[-1] != '' or len(lines) < 3:
        raise ValueError()
    frame = lines[0].split('\t')
    if (len(frame) != 5 or frame[:2] != ['L', 'v1'] or frame[2] not in ('List', 'PodList')
            or frame[3] not in ('', 'null') or frame[4] not in ('', 'null', '0')
            or lines[-2] != 'Z'):
        raise ValueError()
    pods, names, identities, current, total = [], set(), set(), None, 0
    for line in lines[1:-2]:
        cells = line.split('\t')
        if cells[0] == 'P':
            if current is not None or len(cells) != 8 or len(pods) >= MAX_PODS:
                raise ValueError()
            _, api, kind, ns, name, identity, nr, user = cells
            if (api != 'v1' or kind != 'Pod' or ns != namespace or not _name(name, subdomain=True)
                    or name in names or identity in identities
                    or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', identity, re.ASCII)):
                raise ValueError()
            names.add(name); identities.add(identity)
            pod_nr, pod_uid = _boolean(nr), _uid(user)
            current = dict(name=name, containers=[])
            pods.append(current)
            container_names, counts, last_kind = set(), dict.fromkeys(KINDS, 0), -1
        elif cells == ['Q']:
            if current is None or not counts['C']:
                raise ValueError()
            current = None
        else:
            if current is None or len(cells) != 8 or cells[0] not in KINDS or total >= MAX_CONTAINERS:
                raise ValueError()
            marker, name, ro, nr, user, caps, cpu, memory = cells
            rank = list(KINDS).index(marker)
            if rank < last_kind or not _name(name) or name in container_names:
                raise ValueError()
            last_kind = rank
            container_names.add(name); counts[marker] += 1; total += 1
            ro, nr, user = _boolean(ro), _boolean(nr), _uid(user)
            nr = pod_nr if nr is None else nr
            user = pod_uid if user is None else user
            drop, cpu, memory = _drop_all(caps), _nonzero(cpu), _nonzero(memory)
            checks = dict(read_only_root_filesystem=ro is True, non_root=nr is True and user != 0,
                          drop_all_capabilities=drop, cpu_memory_limits=cpu and memory)
            current['containers'].append(dict(name=name, kind=KINDS[marker], ordinal=counts[marker],
                checks={key: 'met' if value else 'not_met' for key, value in checks.items()}))
    if current is not None:
        raise ValueError()
    return pods


def _collect(foundation, check):
    argv = [('--request-timeout=3s' if arg == '--request-timeout=15s' else arg)
            for arg in logical_foundation.kube_argv(foundation)]
    check()
    deadline = time.monotonic() + 8
    process = subprocess.Popen(argv + ['get', 'pods', '--chunk-size=0', '-o', JSONPATH],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False)
    raw = b''
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise subprocess.TimeoutExpired(argv, 8)
                chunk = process.stdout.read1(MAX_STDOUT_BYTES + 1 - len(raw))
                if not chunk:
                    break
                raw += chunk
                if len(raw) > MAX_STDOUT_BYTES:
                    raise ValueError()
        remaining = deadline - time.monotonic()
        if remaining <= 0 or process.wait(timeout=remaining) != 0:
            raise ValueError()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
    check()
    return _parse(raw, foundation['kube']['namespace'])


def observe(principal, role):
    fingerprint = _authority(principal, role)
    def check():
        if _authority(principal, role) != fingerprint:
            raise OperationError('PLATFORM_ACCESS_DENIED', 403)
    source = dict(availability='unavailable', code='POD_BASELINE_UNAVAILABLE',
                  observed_at=_now(), namespace=None, pod_count=None, container_count=None, pods=None)
    result = dict(scope='platform', warehouse='LOCAL', selected_role='admin', observed_at=_now(),
        baseline_version='namespace-pod-container-declarations-v1',
        coverage=dict(kind='namespace_pod_container_declarations', pod_spec=True,
                      per_container=True, runtime_security=False, admission_decision=False,
                      global_policy=False), baseline=source)
    try:
        foundation = _foundation(check)
        check()
        try:
            pods = _collect(foundation, check)
        finally:
            # Recheck even if spawn/read/wait/parse failed; auth loss always wins.
            check()
        after = _foundation(check)
        check()
        if after != foundation:
            raise ValueError()
        source.update(availability='available', code=None, observed_at=_now(), namespace=foundation['kube']['namespace'],
                      pod_count=len(pods), container_count=sum(len(p['containers']) for p in pods), pods=pods)
        result['observed_at'] = _now()
        if len(json.dumps(result, default=str).encode('utf-8')) > MAX_DTO_BYTES:
            raise ValueError()
    except SOURCE_ERRORS as error:
        if isinstance(error, OperationError) and error.code == 'PLATFORM_ACCESS_DENIED':
            raise
        source.update(availability='unavailable', code='POD_BASELINE_UNAVAILABLE',
                      observed_at=_now(), namespace=None, pod_count=None, container_count=None, pods=None)
    check()
    return result
