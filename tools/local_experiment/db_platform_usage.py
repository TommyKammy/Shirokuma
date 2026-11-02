"""Fixed shared LOCAL usage, read-only metrics.k8s.io observation.

Closed DTO: scope/platform, warehouse/LOCAL, selected_role/admin, observed_at,
coverage {kind, cpu, memory, capacity, database_dedicated}, availability, code,
samples. Available samples have exactly role, container_ordinal, sampled_at,
window_seconds, cpu_cores, cpu_millicores, memory_bytes. All measurements are
exact normalized decimal strings (including seconds); CPU is a window average,
memory a working-set sample. Unavailable means samples=null, never zero or a
partial/stale aggregate. No public Kubernetes identifiers or configuration.
"""
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import json
import re
import selectors
import subprocess
import time

import audit_service as audit
from db_platform_observations import _authority, _now, SOURCE_ERRORS
import logical_foundation as lf
from warehouse_operations import OperationError
import warehouse_resident as wr

DEPLOYMENTS = (('trino-local', 'coordinator'), ('trino-local-worker', 'worker'))
MAX_OUTPUT = 1048576
MAX_DTO = 32768
COMPUTE = 'shirokuma.dev/logical-compute'
QUANTITY = re.compile(r'\+?([0-9]+(?:\.[0-9]*)?|\.[0-9]+)([numkMGTPE]|[KMGTPE]i|[eE][+-]?[0-9]+)?', re.ASCII)
COVERAGE = dict(kind='fixed_local_container_usage', cpu='window_average',
                memory='working_set_sample', capacity=False, database_dedicated=False)


def _decimal(value):
    if type(value) is not str or len(value) > 64:
        raise ValueError()
    m = QUANTITY.fullmatch(value)
    if not m:
        raise ValueError()
    suffix = m[2] or ''
    with localcontext() as ctx:
        ctx.prec = 160
        number = Decimal(m[1])
        if suffix.endswith('i'):
            number *= Decimal(1024) ** ('KMGTPE'.index(suffix[0]) + 1)
        elif len(suffix) > 1:
            if len(suffix[1:].lstrip('+-')) > 3 or abs(int(suffix[1:])) > 30:
                raise ValueError()
            number *= Decimal(10) ** int(suffix[1:])
        elif suffix:
            number *= Decimal(10) ** dict(n=-9, u=-6, m=-3, k=3, M=6, G=9, T=12, P=15, E=18)[suffix]
        if not number.is_finite() or number < 0 or number > Decimal('1e24') or (number and number < Decimal('1e-30')):
            raise ValueError()
        return number


def _normal(value):
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def _stamp(value):
    if (type(value) is not str or len(value) > 40 or not re.fullmatch(
            r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z', value)):
        raise ValueError()
    whole, _, fraction = value[:-1].partition('.')
    delta = datetime.fromisoformat(whole + '+00:00') - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return Decimal(delta.days * 86400 + delta.seconds) + Decimal('0.' + (fraction or '0'))


def _window(value):
    # Kubernetes Duration uses Go duration spelling, including composite units.
    if type(value) is not str or not 0 < len(value) <= 64:
        raise ValueError()
    parts = re.findall(r'([0-9]+(?:\.[0-9]*)?|\.[0-9]+)(ns|us|µs|μs|ms|s|m|h)', value)
    if not parts or ''.join(n + u for n, u in parts) != value:
        raise ValueError()
    factors = dict(ns=Decimal('1e-9'), us=Decimal('1e-6'), ms=Decimal('1e-3'),
                   s=Decimal(1), m=Decimal(60), h=Decimal(3600))
    factors.update({'µs': factors['us'], 'μs': factors['us']})
    with localcontext() as ctx:
        ctx.prec = 160
        seconds = sum((Decimal(n) * factors[u] for n, u in parts), Decimal(0))
    if not 0 < seconds <= 120 or seconds.as_tuple().exponent < -9:
        raise ValueError()
    return _normal(seconds)


def _read(argv, deadline):
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False)
    raw = b''
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise subprocess.TimeoutExpired(argv, 15)
                chunk = process.stdout.read1(MAX_OUTPUT + 1 - len(raw))
                if not chunk:
                    break
                raw += chunk
                if len(raw) > MAX_OUTPUT:
                    raise ValueError()
        remaining = deadline - time.monotonic()
        if remaining <= 0 or process.wait(timeout=remaining) != 0:
            raise ValueError()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def _text(value):
    if type(value) is not str or not 0 < len(value) <= 256 or any(ord(c) < 33 for c in value):
        raise ValueError()
    return value


def _meta(obj, namespace):
    m = obj['metadata']
    if m['namespace'] != namespace or m.get('deletionTimestamp') is not None:
        raise ValueError()
    _stamp(m['creationTimestamp'])
    return tuple(_text(m[k]) for k in ('name', 'uid', 'creationTimestamp'))


def _labels(labels, foundation, compute=False):
    if (labels.get(lf.LABEL) != foundation['foundation_id']
            or labels.get(lf.REVISION) != str(foundation['revision'])
            or compute and labels.get(COMPUTE) != 'true'):
        raise ValueError()


def _matches(selector, labels):
    if type(selector) is not dict or set(selector) - {'matchLabels', 'matchExpressions'}:
        raise ValueError()
    match = selector.get('matchLabels', {})
    if type(match) is not dict or any(type(k) is not str or type(v) is not str for k, v in match.items()):
        raise ValueError()
    result = all(labels.get(k) == v for k, v in match.items())
    expressions = selector.get('matchExpressions', [])
    if type(expressions) is not list or len(expressions) > 16 or not (match or expressions):
        raise ValueError()
    for e in expressions:
        key, op, values = e['key'], e['operator'], e.get('values', [])
        if type(key) is not str or type(values) is not list or any(type(v) is not str for v in values):
            raise ValueError()
        if op in ('In', 'NotIn') and values:
            result &= labels.get(key) in values if op == 'In' else labels.get(key) not in values
        elif op in ('Exists', 'DoesNotExist') and not values:
            result &= (key in labels) if op == 'Exists' else (key not in labels)
        else:
            raise ValueError()
    return result


def _owner(obj, kind):
    owners = obj['metadata'].get('ownerReferences', [])
    if len(owners) != 1 or owners[0].get('controller') is not True or owners[0].get('kind') != kind:
        raise ValueError()
    return _text(owners[0]['name']), _text(owners[0]['uid'])


def _items(data, limit=256):
    items = data['items']
    if type(items) is not list or len(items) > limit or data.get('metadata', {}).get('continue'):
        raise ValueError()
    return items


def _containers(obj):
    items = obj['spec']['containers']
    if type(items) is not list or not 1 <= len(items) <= 16:
        raise ValueError()
    names = [_text(c['name']) for c in items]
    if len(set(names)) != len(names):
        raise ValueError()
    return names


def _running(pod):
    """Current ordinary-container incarnation; retained in the source signature."""
    names = _containers(pod)
    statuses = pod['status']['containerStatuses']
    if (type(statuses) is not list or len(statuses) != len(names)
            or {c['name'] for c in statuses} != set(names)):
        raise ValueError()
    starts = {}
    for status in statuses:
        state = status['state']
        if (status.get('ready') is not True or type(state) is not dict
                or set(state) != {'running'}):
            raise ValueError()
        started = _stamp(state['running']['startedAt'])
        if started < _stamp(pod['metadata']['creationTimestamp']):
            raise ValueError()
        starts[status['name']] = started
    return starts


def _source(deployments, replicas, pods, foundation):
    """Reject hidden matching objects, even if they cannot be attributed."""
    ns = foundation['kube']['namespace']
    ds, rs, current = {}, {}, {}
    for d in _items(deployments, 2):
        name, uid, created = _meta(d, ns)
        if name not in dict(DEPLOYMENTS) or name in ds:
            raise ValueError()
        _labels(d['metadata']['labels'], foundation)
        template = d['spec']['template']
        _labels(template['metadata']['labels'], foundation, True)
        if (not _matches(d['spec']['selector'], template['metadata']['labels'])
                or type(d['spec']['replicas']) is not int or d['spec']['replicas'] != 1
                or type(d['metadata']['generation']) is not int or not 1 <= d['metadata']['generation'] <= 2**53 - 1
                or type(d['status'].get('observedGeneration')) is not int
                or d['status'].get('observedGeneration') != d['metadata']['generation']
                or any(type(d['status'].get(k)) is not int or d['status'][k] != 1
                       for k in ('readyReplicas', 'updatedReplicas', 'replicas'))):
            raise ValueError()
        _containers(template)
        ds[name] = d
    if set(ds) != set(dict(DEPLOYMENTS)):
        raise ValueError()
    duids = {d['metadata']['uid'] for d in ds.values()}
    if len(duids) != 2:
        raise ValueError()
    def relevant(obj):
        labels = obj.get('metadata', {}).get('labels', {})
        owners = obj.get('metadata', {}).get('ownerReferences', [])
        return (labels.get(COMPUTE) == 'true' or any(_matches(d['spec']['selector'], labels) for d in ds.values())
                or any(o.get('uid') in duids or o.get('name') in ds for o in owners))
    for r in _items(replicas):
        if not relevant(r):
            continue
        name, uid, created = _meta(r, ns)
        dn, duid = _owner(r, 'Deployment')
        if (dn not in ds or ds[dn]['metadata']['uid'] != duid or uid in rs
                or any(name == item[0]['metadata']['name'] for item in rs.values())):
            raise ValueError()
        _labels(r['metadata']['labels'], foundation, True)
        template = r['spec']['template']
        _labels(template['metadata']['labels'], foundation, True)
        if (not _matches(ds[dn]['spec']['selector'], template['metadata']['labels'])
                or not _matches(r['spec']['selector'], template['metadata']['labels'])):
            raise ValueError()
        if (not all(template['metadata']['labels'].get(k) == v
                    for k, v in ds[dn]['spec']['template']['metadata']['labels'].items())
                or _containers(template) != _containers(ds[dn]['spec']['template'])):
            raise ValueError()
        rs[uid] = (r, dn)
    seen = set()
    for p in _items(pods):
        owners = p.get('metadata', {}).get('ownerReferences', [])
        if not relevant(p) and not any(o.get('uid') in rs or o.get('name') in {r[0]['metadata']['name'] for r in rs.values()} for o in owners):
            continue
        name, uid, created = _meta(p, ns)
        if (name, uid) in seen or any(name == k[0] or uid == k[1] for k in seen):
            raise ValueError()
        seen.add((name, uid))
        rn, ruid = _owner(p, 'ReplicaSet')
        r, dn = rs[ruid]
        if (rn != r['metadata']['name'] or dn in current
                or r['spec']['template']['spec'] != ds[dn]['spec']['template']['spec']):
            raise ValueError()
        _labels(p['metadata']['labels'], foundation, True)
        if (not _matches(ds[dn]['spec']['selector'], p['metadata']['labels'])
                or not _matches(r['spec']['selector'], p['metadata']['labels'])
                or _containers(p) != _containers(r['spec']['template'])
                or p['status']['phase'] != 'Running'):
            raise ValueError()
        _running(p)
        current[dn] = p
    if set(current) != set(ds):
        raise ValueError()
    # Compare exact relevant source again after metrics, including UIDs, selectors,
    # creation times, container identities/restarts/startedAt and revisions.
    # Old zero-replica RSs remain in this fence.
    return current, dict(deployments=ds, replicas=rs, pods=current)


def _samples(metrics, current, foundation, now):
    ns = foundation['kube']['namespace']
    by_name = {}
    for m in _items(metrics):
        name = _text(m['metadata']['name'])
        if m['metadata']['namespace'] != ns or name in by_name:
            raise ValueError()
        by_name[name] = m
    result = []
    for dn, role in DEPLOYMENTS:
        pod = current[dn]
        meta = pod['metadata']
        metric = by_name[meta['name']]
        # PodMetrics commonly omits UID. Current UID/creation are fenced on both
        # sides, timestamp must be from this incarnation; if supplied UID must match.
        if metric['metadata'].get('uid', meta['uid']) != meta['uid']:
            raise ValueError()
        sampled = _stamp(metric['timestamp'])
        age = _stamp(now.isoformat().replace('+00:00', 'Z')) - sampled
        if not Decimal(-5) <= age <= Decimal(120) or sampled < _stamp(meta['creationTimestamp']):
            raise ValueError()
        window = _window(metric['window'])
        containers = metric['containers']
        names = _containers(pod)
        if len(containers) != len(names) or {c['name'] for c in containers} != set(names):
            raise ValueError()
        lookup = {c['name']: c for c in containers}
        starts = _running(pod)
        interval_start = sampled - Decimal(window)
        for ordinal, name in enumerate(names, 1):
            if sampled < starts[name] or interval_start < starts[name]:
                raise ValueError()
            usage = lookup[name]['usage']
            if set(usage) != {'cpu', 'memory'}:
                raise ValueError()
            cpu, memory = _decimal(usage['cpu']), _decimal(usage['memory'])
            with localcontext() as ctx:
                ctx.prec = 160
                millicores = cpu * 1000
            result.append(dict(role=role, container_ordinal=ordinal,
                sampled_at=metric['timestamp'], window_seconds=window,
                cpu_cores=_normal(cpu), cpu_millicores=_normal(millicores), memory_bytes=_normal(memory)))
    return result


def observe(principal, role):
    fingerprint = _authority(principal, role)
    def fence():
        if _authority(principal, role) != fingerprint:
            raise OperationError('PLATFORM_ACCESS_DENIED', 403)
    def foundation():
        fence()
        with audit.snapshot() as db:
            return wr._logical_foundation(db)
    result = dict(scope='platform', warehouse='LOCAL', selected_role='admin', observed_at=_now(),
                  coverage=dict(COVERAGE), availability='unavailable', code='USAGE_UNAVAILABLE', samples=None)
    try:
        initial = foundation()
        ns = initial['kube']['namespace']
        if initial['warehouse_id'] != 'LOCAL' or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', ns):
            raise ValueError()
        argv = [('--request-timeout=2s' if a == '--request-timeout=15s' else a) for a in lf.kube_argv(initial)]
        deadline = time.monotonic() + 15
        def read(args):
            fence()
            return _read(argv + args, deadline)
        def source():
            return _source(read(['get', 'deployments', *dict(DEPLOYMENTS), '-o', 'json']),
                read(['get', 'replicasets', '-o', 'json']), read(['get', 'pods', '-o', 'json']), initial)
        current, signature = source()
        metrics = read(['get', '--raw', '/apis/metrics.k8s.io/v1beta1/namespaces/' + ns + '/pods'])
        _, after = source()
        if signature != after or foundation() != initial:
            raise ValueError()
        result.update(availability='available', code=None,
                      samples=_samples(metrics, current, initial, datetime.now(timezone.utc)))
        result['observed_at'] = _now()
        if len(json.dumps(result).encode()) > MAX_DTO:
            raise ValueError()
    except SOURCE_ERRORS as error:
        if isinstance(error, OperationError) and error.code == 'PLATFORM_ACCESS_DENIED':
            raise
        result.update(availability='unavailable', code='USAGE_UNAVAILABLE', samples=None, observed_at=_now())
    fence()
    return result
