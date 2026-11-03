"""Passive counts reported by the three managed foundation Deployment controllers."""
import json
import re
import selectors
import subprocess
import time

from db_platform_observations import _authority, _integer, _now, SOURCE_ERRORS
from db_platform_foundation_baseline import _foundation
import logical_foundation
from warehouse_operations import OperationError

MAX_STDOUT_BYTES = 16384
MAX_DTO_BYTES = 65536
JSONPATH = (r'jsonpath={range .items[*]}W{"\t"}{.apiVersion}{"\t"}{.kind}'
            r'{"\t"}{.metadata.name}{"\t"}{.metadata.namespace}{"\t"}{.metadata.uid}'
            r'{"\t"}{.metadata.deletionTimestamp}'
            r'{"\t"}{.metadata.labels.shirokuma\.dev/logical-foundation}'
            r'{"\t"}{.metadata.labels.shirokuma\.dev/foundation-revision}'
            r'{"\t"}{.spec.template.metadata.labels.shirokuma\.dev/logical-foundation}'
            r'{"\t"}{.spec.template.metadata.labels.shirokuma\.dev/foundation-revision}'
            r'{"\t"}{.metadata.generation}{"\t"}{.status.observedGeneration}'
            r'{"\t"}{.spec.replicas}{"\t"}{.status.replicas}'
            r'{"\t"}{.status.updatedReplicas}{"\t"}{.status.readyReplicas}'
            r'{"\t"}{.status.availableReplicas}{"\t"}{.status.unavailableReplicas}{"\n"}{end}')

FIELDS = ('generation', 'observed_generation', 'desired_replicas', 'replicas',
          'updated_replicas', 'ready_replicas', 'available_replicas', 'unavailable_replicas')


def _targets(foundation):
    prefix = foundation['prefix']
    return (('Deployment', prefix + '-seaweed', 'storage'),
            ('Deployment', prefix + '-postgres', 'catalog_database'),
            ('Deployment', prefix + '-polaris', 'catalog'))


def _count(text, nullable=False):
    if nullable and text in ('', 'null'):
        return None
    if not re.fullmatch(r'(?:0|[1-9][0-9]{0,15})', text, re.ASCII):
        raise ValueError()
    return _integer(int(text))


def _parse(raw, foundation):
    if len(raw) > MAX_STDOUT_BYTES:
        raise ValueError()
    lines = raw.decode('ascii').split('\n')
    if lines[-1] == '':
        lines.pop()
    if len(lines) != 3:
        raise ValueError()
    expected = {name for _, name, _ in _targets(foundation)}
    rows, identities = {}, set()
    for line in lines:
        cells = line.split('\t')
        if len(cells) != 19:
            raise ValueError()
        marker, api, kind, name, namespace, uid, deleting, fid, rev, tfid, trev = cells[:11]
        if (marker != 'W' or api != 'apps/v1' or kind != 'Deployment'
                or name not in expected or name in rows
                or namespace != foundation['kube']['namespace']
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', uid, re.ASCII)
                or uid in identities or deleting not in ('', 'null')
                or fid != foundation['foundation_id'] or tfid != fid
                or rev != str(foundation['revision']) or trev != rev):
            raise ValueError()
        identities.add(uid)
        values = {field: _count(text, field not in ('generation', 'desired_replicas'))
                  for field, text in zip(FIELDS, cells[11:])}
        if (values['generation'] == 0 or values['observed_generation'] is not None
                and values['observed_generation'] > values['generation']):
            raise ValueError()
        rows[name] = values
    if set(rows) != expected:
        raise ValueError()
    return [dict(component=component, **rows[name]) for _, name, component in _targets(foundation)]


def _collect(foundation, check, deadline):
    argv = [('--request-timeout=3s' if arg == '--request-timeout=15s' else arg)
            for arg in logical_foundation.kube_argv(foundation)]
    check()
    if time.monotonic() >= deadline:
        raise ValueError()
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
        try:
            if process.poll() is None:
                process.kill()
                process.wait()
        finally:
            process.stdout.close()
            check()
    return _parse(raw, foundation)



def observe(principal, role):
    fingerprint = _authority(principal, role)
    deadline = time.monotonic() + 8
    def check():
        if _authority(principal, role) != fingerprint:
            raise OperationError('PLATFORM_ACCESS_DENIED', 403)
    source = dict(availability='unavailable', code='FOUNDATION_CONTROLLERS_UNAVAILABLE',
                  observed_at=_now(), deployments=None)
    result = dict(scope='platform', warehouse='LOCAL', selected_role='admin', observed_at=_now(),
        controller_version='foundation-storage-catalog-controllers-v1',
        coverage=dict(kind='deployment_controller_counts', managed_foundation=True,
                      service_connectivity=False, sql_health=False, policy_decision=False),
        controllers=source)
    try:
        try:
            foundation = _foundation(check)
        finally:
            check()
        try:
            deployments = _collect(foundation, check, deadline)
        finally:
            check()
        try:
            saved = _foundation(check)
        finally:
            check()
        if saved != foundation or time.monotonic() >= deadline:
            raise ValueError()
        source.update(availability='available', code=None, observed_at=_now(), deployments=deployments)
        if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > MAX_DTO_BYTES:
            raise ValueError()
    except SOURCE_ERRORS as error:
        if isinstance(error, OperationError) and error.code == 'PLATFORM_ACCESS_DENIED':
            raise
        source.update(availability='unavailable', code='FOUNDATION_CONTROLLERS_UNAVAILABLE',
                      observed_at=_now(), deployments=None)
    check()
    return result
