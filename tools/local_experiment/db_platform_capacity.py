"""Bounded configured shared VM Node capacity; no admission or free-CPU claim.

Closed DTO: scope, warehouse, selected_role, observed_at, coverage, availability,
code, core, image_fs, container_fs. Core is all-or-null: cpu_capacity_cores,
cpu_allocatable_cores, memory_capacity_bytes, memory_allocatable_bytes,
memory_available_bytes, memory_sampled_at, node_started_at, root_fs. Each FS
has capacity_bytes, available_bytes, used_bytes (optional/null), sampled_at.
Byte measurements are canonical uint64 strings. Optional runtime filesystems
are independent projections and must never be summed, even when equal.
"""
import copy
import json
import re
import time

import audit_service as audit
from db_platform_observations import _authority, _now, SOURCE_ERRORS
from db_platform_usage import (_read, _decimal, _normal, _stamp, _text,
                               _meta, _labels, _items, DEPLOYMENTS, MAX_DTO)
import logical_foundation as lf
from warehouse_operations import OperationError
import warehouse_resident as wr

UINT64 = 2**64 - 1
COVERAGE = dict(kind='configured_shared_kubernetes_vm_node',
    cpu='node_capacity_and_allocatable_cores', memory='kubelet_limit_minus_working_set_estimate',
    allocatable='scheduler_pod_budget_not_free', root_fs='node_root_filesystem',
    runtime_fs='independent_optional_filesystems_never_sum', database_dedicated=False,
    mac_host=False, admission=False)


def _uint(value):
    if type(value) is not int or not 0 <= value <= UINT64:
        raise ValueError()
    return str(value)


def _memory(value):
    number = _decimal(value)
    if number != number.to_integral_value() or number > UINT64:
        raise ValueError()
    return _uint(int(number))


def _pin(foundation):
    kube = foundation['kube']
    pin = kube['node_name']
    if (foundation['warehouse_id'] != 'LOCAL' or type(pin) is not str
            or len(pin) > 253 or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', pin)
            or type(kube['node_uids']) is not dict or set(kube['node_uids']) != {pin}
            or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', kube['namespace'])):
        raise ValueError()
    _text(kube['node_uids'][pin])
    return pin


def _deployments(data, foundation):
    pin = _pin(foundation)
    result = {}
    for d in _items(data, 2):
        name, uid, created = _meta(d, foundation['kube']['namespace'])
        if name not in dict(DEPLOYMENTS) or name in result:
            raise ValueError()
        _labels(d['metadata']['labels'], foundation)
        template = d['spec']['template']
        _labels(template['metadata']['labels'], foundation, True)
        if template['metadata'].get('namespace', foundation['kube']['namespace']) != foundation['kube']['namespace']:
            raise ValueError()
        spec = template['spec']
        selector = spec['nodeSelector']
        if (type(selector) is not dict or selector.get('kubernetes.io/hostname') != pin
                or spec.get('nodeName', pin) != pin
                or type(d['spec']['replicas']) is not int or not 0 <= d['spec']['replicas'] <= 2**53-1):
            raise ValueError()
        # Ignore controller bookkeeping, heartbeat and resourceVersion noise.
        result[name] = (uid, created,
                        tuple(d['metadata']['labels'].get(k) for k in (lf.LABEL, lf.REVISION)),
                        tuple(template['metadata']['labels'].get(k) for k in (lf.LABEL, lf.REVISION, 'shirokuma.dev/logical-compute')),
                        selector, spec.get('nodeName'), d['spec']['replicas'])
    if set(result) != set(dict(DEPLOYMENTS)) or len({r[0] for r in result.values()}) != 2:
        raise ValueError()
    return result


def _node(data, foundation):
    pin = _pin(foundation)
    m, status = data['metadata'], data['status']
    if (m['name'] != pin or m['uid'] != foundation['kube']['node_uids'][pin]
            or m.get('deletionTimestamp') is not None
            or m['labels'].get('kubernetes.io/hostname') != pin):
        raise ValueError()
    _stamp(m['creationTimestamp'])
    boot = _text(status['nodeInfo']['bootID'])
    cap, alloc = status['capacity'], status['allocatable']
    cpu, cpu_alloc = _decimal(cap['cpu']), _decimal(alloc['cpu'])
    memory, memory_alloc = _memory(cap['memory']), _memory(alloc['memory'])
    if cpu <= 0 or cpu_alloc > cpu or int(memory) <= 0 or int(memory_alloc) > int(memory):
        raise ValueError()
    values = dict(cpu_capacity_cores=_normal(cpu), cpu_allocatable_cores=_normal(cpu_alloc),
                  memory_capacity_bytes=memory, memory_allocatable_bytes=memory_alloc)
    return values, (m['uid'], m['creationTimestamp'], boot, m['labels']['kubernetes.io/hostname'], values)


def _sample(value, start, now):
    sample = _stamp(value)
    if not -5 <= now - sample <= 120 or sample < start:
        raise ValueError()
    return value


def _fs(data, start, now):
    cap, available = _uint(data['capacityBytes']), _uint(data['availableBytes'])
    if int(cap) == 0 or int(available) > int(cap):
        raise ValueError()
    used = None if data.get('usedBytes') is None else _uint(data['usedBytes'])
    return dict(capacity_bytes=cap, available_bytes=available, used_bytes=used,
                sampled_at=_sample(data['time'], start, now))


def _summary(data, foundation, values, now):
    node = data['node']
    if node['nodeName'] != _pin(foundation):
        raise ValueError()
    start = _stamp(node['startTime'])
    if start > now + 5:
        raise ValueError()
    # Kubelet/Node boot may precede API registration; no creation-time ordering.
    available = _uint(node['memory']['availableBytes'])
    if int(available) > int(values['memory_capacity_bytes']):
        raise ValueError()
    core = dict(values, node_started_at=node['startTime'],
        memory_available_bytes=available,
        memory_sampled_at=_sample(node['memory']['time'], start, now),
        root_fs=_fs(node['fs'], start, now))
    optional = []
    for key in ('imageFs', 'containerFs'):
        try:
            optional.append(_fs(node['runtime'][key], start, now))
        except (KeyError, ValueError, TypeError, AttributeError):
            optional.append(None)
    return core, *optional


def observe(principal, role):
    fingerprint = _authority(principal, role)
    def fence():
        if _authority(principal, role) != fingerprint:
            raise OperationError('PLATFORM_ACCESS_DENIED', 403)
    def foundation():
        fence()
        with audit.snapshot() as db:
            return copy.deepcopy(wr._logical_foundation(db))
    result = dict(scope='platform', warehouse='LOCAL', selected_role='admin', observed_at=_now(),
                  coverage=dict(COVERAGE), availability='unavailable', code='CAPACITY_UNAVAILABLE',
                  core=None, image_fs=None, container_fs=None)
    try:
        initial = foundation()
        pin = _pin(initial)
        argv = [('--request-timeout=2s' if a == '--request-timeout=15s' else a) for a in lf.kube_argv(initial)]
        deadline = time.monotonic() + 15
        def read(args):
            fence()
            return _read(argv + args, deadline)
        deployments = ['get', 'deployments', *dict(DEPLOYMENTS), '-o', 'json']
        exact_node = ['get', 'node', pin, '-o', 'json']
        before_d = _deployments(read(deployments), initial)
        values, before_n = _node(read(exact_node), initial)
        summary = read(['get', '--raw', '/api/v1/nodes/' + pin + '/proxy/stats/summary'])
        _, after_n = _node(read(exact_node), initial)
        after_d = _deployments(read(deployments), initial)
        if before_n != after_n or before_d != after_d or foundation() != initial:
            raise ValueError()
        result['observed_at'] = _now()
        core, image, container = _summary(summary, initial, values, _stamp(result['observed_at']))
        result.update(availability='available', code=None, core=core, image_fs=image, container_fs=container)
        if len(json.dumps(result).encode()) > MAX_DTO:
            raise ValueError()
    except SOURCE_ERRORS as error:
        if isinstance(error, OperationError) and error.code == 'PLATFORM_ACCESS_DENIED':
            raise
        result.update(availability='unavailable', code='CAPACITY_UNAVAILABLE', core=None,
                      image_fs=None, container_fs=None, observed_at=_now())
    fence()
    return result
