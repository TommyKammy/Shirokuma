"""Bounded, advisory declaration review of the seven managed foundation templates."""
import copy
import json
import re
import selectors
import subprocess
import time

import audit_service as audit
from db_platform_observations import _authority, _now, SOURCE_ERRORS
from db_platform_workload_baseline import _boolean, _uid, _drop_all, _nonzero
import logical_foundation
from warehouse_operations import OperationError
import warehouse_resident as wr

MAX_STDOUT_BYTES = 32768
MAX_DTO_BYTES = 131072
# Project only identity/ownership and the four declarations; never whole objects.
JSONPATH = (r'jsonpath={range .items[*]}W{"\t"}{.apiVersion}{"\t"}{.kind}'
            r'{"\t"}{.metadata.name}{"\t"}{.metadata.namespace}{"\t"}{.metadata.uid}'
            r'{"\t"}{.metadata.deletionTimestamp}'
            r'{"\t"}{.metadata.labels.shirokuma\.dev/logical-foundation}'
            r'{"\t"}{.metadata.labels.shirokuma\.dev/foundation-revision}'
            r'{"\t"}{.spec.template.metadata.labels.shirokuma\.dev/logical-foundation}'
            r'{"\t"}{.spec.template.metadata.labels.shirokuma\.dev/foundation-revision}'
            r'{"\t"}{.spec.template.spec.securityContext.runAsNonRoot}'
            r'{"\t"}{.spec.template.spec.securityContext.runAsUser}{"\n"}'
            r'{range .spec.template.spec.containers[*]}C{"\t"}'
            r'{.securityContext.readOnlyRootFilesystem}{"\t"}{.securityContext.runAsNonRoot}'
            r'{"\t"}{.securityContext.runAsUser}{"\t"}{.securityContext.capabilities.drop}'
            r'{"\t"}{.resources.limits.cpu}{"\t"}{.resources.limits.memory}{"\n"}{end}'
            r'{range .spec.template.spec.initContainers[*]}I{"\t"}'
            r'{.securityContext.readOnlyRootFilesystem}{"\t"}{.securityContext.runAsNonRoot}'
            r'{"\t"}{.securityContext.runAsUser}{"\t"}{.securityContext.capabilities.drop}'
            r'{"\t"}{.resources.limits.cpu}{"\t"}{.resources.limits.memory}{"\n"}{end}{end}')


def _targets(foundation):
    prefix = foundation['prefix']
    return (('Deployment', prefix + '-seaweed', 'storage'),
            ('Deployment', prefix + '-postgres', 'catalog_database'),
            ('Deployment', prefix + '-polaris', 'catalog'),
            ('Deployment', 'trino-local', 'coordinator'),
            ('Deployment', 'trino-local-worker', 'worker'),
            ('Deployment', 'trino-opa', 'authorization'),
            ('Job', prefix + '-bootstrap', 'catalog_bootstrap'))


def _parse(raw, foundation):
    if len(raw) > MAX_STDOUT_BYTES:
        raise ValueError()
    lines = raw.decode('ascii').split('\n')
    if lines[-1] == '':
        lines.pop()
    if len(lines) > 71:
        raise ValueError()
    expected = {name: kind for kind, name, _ in _targets(foundation)}
    rows, containers, total, identities = {}, None, 0, set()
    for line in lines:
        cells = line.split('\t')
        if cells[0] == 'W':
            if len(cells) != 13:
                raise ValueError()
            _, api, kind, name, namespace, uid, deleting, fid, rev, tfid, trev, nr, user = cells
            if (name not in expected or name in rows or kind != expected[name]
                    or api != ('apps/v1' if kind == 'Deployment' else 'batch/v1')
                    or namespace != foundation['kube']['namespace']
                    or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', uid, re.ASCII)
                    or uid in identities or deleting not in ('', 'null')
                    or fid != foundation['foundation_id'] or tfid != fid
                    or rev != str(foundation['revision']) or trev != rev):
                raise ValueError()
            identities.add(uid)
            pod_non_root, pod_uid = _boolean(nr), _uid(user)
            containers = []
            rows[name] = containers
        else:
            if (len(cells) != 7 or cells[0] not in ('C', 'I')
                    or containers is None or total >= 64):
                raise ValueError()
            read_only, non_root, uid = _boolean(cells[1]), _boolean(cells[2]), _uid(cells[3])
            if non_root is None:
                non_root = pod_non_root
            if uid is None:
                uid = pod_uid
            drop_all = _drop_all(cells[4])
            cpu, memory = _nonzero(cells[5]), _nonzero(cells[6])
            kind = 'container' if cells[0] == 'C' else 'init_container'
            checks = dict(read_only_root_filesystem=read_only is True,
                          non_root=non_root is True and uid != 0,
                          drop_all_capabilities=drop_all, cpu_memory_limits=cpu and memory)
            containers.append(dict(kind=kind,
                ordinal=1 + sum(c['kind'] == kind for c in containers),
                checks={key: 'met' if value else 'not_met' for key, value in checks.items()}))
            total += 1
    if set(rows) != set(expected) or any(not any(c['kind'] == 'container' for c in row)
                                       for row in rows.values()):
        raise ValueError()
    return [dict(component=component, kind=kind, containers=rows[name])
            for kind, name, component in _targets(foundation)]


def _foundation(check):
    check()
    with audit.snapshot() as db:
        check()
        foundation = copy.deepcopy(wr._logical_foundation(db))
    # Pure validation, never render() or private artifact generation.
    foundation = logical_foundation.validate(foundation)
    if foundation['warehouse_id'] != 'LOCAL':
        raise ValueError()
    return foundation


def _collect(foundation, check):
    argv = [('--request-timeout=3s' if arg == '--request-timeout=15s' else arg)
            for arg in logical_foundation.kube_argv(foundation)]
    check()
    deadline = time.monotonic() + 8
    process = subprocess.Popen(argv + ['get', *[kind.lower() + '/' + name for kind, name, _ in _targets(foundation)], '-o', JSONPATH],
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
    return _parse(raw, foundation)


def observe(principal, role):
    fingerprint = _authority(principal, role)
    def check():
        if _authority(principal, role) != fingerprint:
            raise OperationError('PLATFORM_ACCESS_DENIED', 403)
    source = dict(availability='unavailable', code='FOUNDATION_BASELINE_UNAVAILABLE',
                  observed_at=_now(), workloads=None)
    result = dict(scope='platform', warehouse='LOCAL', selected_role='admin', observed_at=_now(),
        baseline_version='foundation-container-declarations-v1',
        coverage=dict(kind='foundation_workload_container_declarations', pod_template=True,
                      per_container=True, runtime_security=False, admission_decision=False,
                      global_policy=False), baseline=source)
    try:
        foundation = _foundation(check)
        try:
            workloads = _collect(foundation, check)
        finally:
            # Recheck even if spawn/read/wait/parse failed; auth loss always wins.
            check()
        if _foundation(check) != foundation:
            raise ValueError()
        source.update(availability='available', code=None, observed_at=_now(), workloads=workloads)
        if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > MAX_DTO_BYTES:
            raise ValueError()
    except SOURCE_ERRORS as error:
        if isinstance(error, OperationError) and error.code == 'PLATFORM_ACCESS_DENIED':
            raise
        source.update(availability='unavailable', code='FOUNDATION_BASELINE_UNAVAILABLE',
                      observed_at=_now(), workloads=None)
    check()
    return result
