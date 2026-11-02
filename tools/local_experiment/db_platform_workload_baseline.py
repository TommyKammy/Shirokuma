"""Compare fixed LOCAL Pod-template declarations with four advisory rules."""
import json
import re
import selectors
import subprocess
import time

import audit_service as audit
from db_platform_observations import _authority, _now, SOURCE_ERRORS
from db_platform_resources import _quantity
import logical_foundation
from warehouse_operations import OperationError
import warehouse_resident as wr


JSONPATH = (r'jsonpath={range .items[*]}D{"\t"}{.metadata.name}{"\t"}'
            r'{.metadata.namespace}{"\t"}{.spec.template.spec.securityContext.runAsNonRoot}'
            r'{"\t"}{.spec.template.spec.securityContext.runAsUser}{"\n"}'
            r'{range .spec.template.spec.containers[*]}C{"\t"}'
            r'{.securityContext.readOnlyRootFilesystem}{"\t"}{.securityContext.runAsNonRoot}'
            r'{"\t"}{.securityContext.runAsUser}{"\t"}{.securityContext.capabilities.drop}'
            r'{"\t"}{.resources.limits.cpu}{"\t"}{.resources.limits.memory}{"\n"}{end}'
            r'{range .spec.template.spec.initContainers[*]}I{"\t"}'
            r'{.securityContext.readOnlyRootFilesystem}{"\t"}{.securityContext.runAsNonRoot}'
            r'{"\t"}{.securityContext.runAsUser}{"\t"}{.securityContext.capabilities.drop}'
            r'{"\t"}{.resources.limits.cpu}{"\t"}{.resources.limits.memory}{"\n"}{end}{end}')
DEPLOYMENTS = (('trino-local', 'coordinator'), ('trino-local-worker', 'worker'))
MAX_STDOUT_BYTES = 16384
MAX_DTO_BYTES = 65536


def _boolean(value):
    if value in ('', 'null'):
        return None
    if value not in ('true', 'false'):
        raise ValueError()
    return value == 'true'


def _uid(value):
    if value in ('', 'null'):
        return None
    if not re.fullmatch(r'0|[1-9][0-9]{0,18}', value, re.ASCII):
        raise ValueError()
    number = int(value)
    if number > 2**63 - 1:
        raise ValueError()
    return number


def _drop_all(value):
    if value in ('', 'null'):
        return False
    entries = json.loads(value)
    if type(entries) is not list or any(type(entry) is not str for entry in entries):
        raise ValueError()
    return 'ALL' in entries


def _nonzero(value):
    quantity = _quantity(value)
    # Only coefficient digits count; exponent and suffix digits never do.
    return quantity is not None and any(c in '123456789'
        for c in re.match(r'\+?[0-9.]+', quantity).group())


def _parse(raw, namespace):
    if len(raw) > MAX_STDOUT_BYTES:
        raise ValueError()
    lines = raw.decode('ascii').split('\n')
    if lines[-1] == '':
        lines.pop()
    if len(lines) > 34:
        raise ValueError()
    rows, containers, total = {}, None, 0
    names = dict(DEPLOYMENTS)
    for line in lines:
        cells = line.split('\t')
        if cells[0] == 'D':
            if (len(cells) != 5 or cells[1] not in names or cells[1] in rows
                    or cells[2] != namespace):
                raise ValueError()
            pod_non_root, pod_uid = _boolean(cells[3]), _uid(cells[4])
            containers = []
            rows[cells[1]] = containers
        else:
            if (len(cells) != 7 or cells[0] not in ('C', 'I')
                    or containers is None or total >= 32):
                raise ValueError()
            read_only, non_root = _boolean(cells[1]), _boolean(cells[2])
            uid = _uid(cells[3])
            if non_root is None:
                non_root = pod_non_root
            if uid is None:
                uid = pod_uid
            drop_all = _drop_all(cells[4])
            # Validate both quantities even when one is missing or zero.
            cpu, memory = _nonzero(cells[5]), _nonzero(cells[6])
            kind = 'container' if cells[0] == 'C' else 'init_container'
            checks = dict(read_only_root_filesystem=read_only is True,
                          non_root=non_root is True and uid != 0,
                          drop_all_capabilities=drop_all, cpu_memory_limits=cpu and memory)
            containers.append(dict(kind=kind,
                ordinal=1 + sum(c['kind'] == kind for c in containers),
                checks={key: 'met' if value else 'not_met' for key, value in checks.items()}))
            total += 1
    if set(rows) != set(names) or any(not any(c['kind'] == 'container' for c in row)
                                    for row in rows.values()):
        raise ValueError()
    return [dict(component=component, containers=rows[name]) for name, component in DEPLOYMENTS]


def _collect():
    with audit.snapshot() as db:
        foundation = wr._logical_foundation(db)
    namespace = foundation['kube']['namespace']
    if foundation['warehouse_id'] != 'LOCAL' or not isinstance(namespace, str) or not namespace:
        raise ValueError()
    argv = [('--request-timeout=3s' if arg == '--request-timeout=15s' else arg)
            for arg in logical_foundation.kube_argv(foundation)]
    deadline = time.monotonic() + 8
    process = subprocess.Popen(argv + ['get', 'deployments', 'trino-local',
        'trino-local-worker', '-o', JSONPATH],
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
    return _parse(raw, namespace)


def observe(principal, role):
    fingerprint = _authority(principal, role)
    source = dict(availability='unavailable', code='WORKLOAD_BASELINE_UNAVAILABLE',
                  observed_at=_now(), deployments=None)
    result = dict(scope='platform', warehouse='LOCAL', selected_role='admin', observed_at=_now(),
        baseline_version='local-container-declarations-v1',
        coverage=dict(kind='deployment_container_declarations', pod_template=True,
                      per_container=True, runtime_security=False, admission_decision=False,
                      global_policy=False), baseline=source)
    try:
        deployments = _collect()
        source.update(availability='available', code=None, observed_at=_now(), deployments=deployments)
        if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > MAX_DTO_BYTES:
            raise ValueError()
    except SOURCE_ERRORS:
        source.update(availability='unavailable', code='WORKLOAD_BASELINE_UNAVAILABLE',
                      observed_at=_now(), deployments=None)
    if _authority(principal, role) != fingerprint:
        raise OperationError('PLATFORM_ACCESS_DENIED', 403)
    return result
