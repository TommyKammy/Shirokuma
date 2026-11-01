"""Read configured per-container resources from the fixed LOCAL Pod templates."""
import json
import re
import selectors
import subprocess
import time

import audit_service as audit
from db_platform_observations import _authority, _now, SOURCE_ERRORS
import logical_foundation
from warehouse_operations import OperationError
import warehouse_resident as wr


JSONPATH = (r'jsonpath={range .items[*]}D{"\t"}{.metadata.name}{"\t"}'
            r'{.metadata.namespace}{"\n"}{range .spec.template.spec.containers[*]}'
            r'C{"\t"}{.resources.requests.cpu}{"\t"}{.resources.requests.memory}'
            r'{"\t"}{.resources.limits.cpu}{"\t"}{.resources.limits.memory}{"\n"}{end}'
            r'{range .spec.template.spec.initContainers[*]}'
            r'I{"\t"}{.resources.requests.cpu}{"\t"}{.resources.requests.memory}'
            r'{"\t"}{.resources.limits.cpu}{"\t"}{.resources.limits.memory}{"\n"}{end}{end}')
DEPLOYMENTS = (('trino-local', 'coordinator'), ('trino-local-worker', 'worker'))
MAX_STDOUT_BYTES = 16384
MAX_DTO_BYTES = 65536
# Kubernetes Quantity decimal, DecimalSI, BinarySI and DecimalExponent forms.
# Keep the original spelling, including an explicit plus sign or zero.
QUANTITY = re.compile(r'\+?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)'
                      r'(?:[numkMGTPE]|[KMGTPE]i|[eE][+-]?[0-9]+)?', re.ASCII)


def _quantity(value):
    if value == '':
        return None
    if len(value) > 64 or not value.isascii() or not QUANTITY.fullmatch(value):
        raise ValueError()
    return value


def _parse(raw, namespace):
    if len(raw) > MAX_STDOUT_BYTES:
        raise ValueError()
    lines = raw.decode('ascii').split('\n')
    if lines[-1] == '':
        lines.pop()
    if len(lines) > 34:
        raise ValueError()
    rows, containers = {}, None
    names = dict(DEPLOYMENTS)
    for line in lines:
        cells = line.split('\t')
        if cells[0] == 'D':
            if (len(cells) != 3 or cells[1] not in names or cells[1] in rows
                    or cells[2] != namespace):
                raise ValueError()
            containers = []
            rows[cells[1]] = containers
        else:
            if (len(cells) != 5 or cells[0] not in ('C', 'I')
                    or containers is None or len(containers) >= 16):
                raise ValueError()
            cpu_request, memory_request, cpu_limit, memory_limit = map(_quantity, cells[1:])
            kind = 'container' if cells[0] == 'C' else 'init'
            containers.append(dict(kind=kind,
                index=1 + sum(c['kind'] == kind for c in containers),
                requests=dict(cpu=cpu_request, memory=memory_request),
                limits=dict(cpu=cpu_limit, memory=memory_limit)))
    if set(rows) != set(names) or any(not any(c['kind'] == 'container' for c in row)
                                    for row in rows.values()):
        raise ValueError()
    return [dict(role=role, containers=rows[name]) for name, role in DEPLOYMENTS]


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
    source = dict(availability='unavailable', code='RESOURCE_CONFIGURATION_UNAVAILABLE',
                  observed_at=_now(), deployments=None)
    result = dict(scope='platform', warehouse='LOCAL', selected_role='admin', observed_at=_now(),
        coverage=dict(kind='deployment_container_resource_configuration', pod_template=True,
                      per_container=True, live_usage=False, effective_pod_resources=False,
                      capacity=False), resources=source)
    try:
        deployments = _collect()
        source.update(availability='available', code=None, observed_at=_now(), deployments=deployments)
        if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > MAX_DTO_BYTES:
            raise ValueError()
    except SOURCE_ERRORS:
        source.update(availability='unavailable', code='RESOURCE_CONFIGURATION_UNAVAILABLE',
                      observed_at=_now(), deployments=None)
    if _authority(principal, role) != fingerprint:
        raise OperationError('PLATFORM_ACCESS_DENIED', 403)
    return result
