"""Bounded Kubernetes storage reference inventory; never measures used bytes."""
from __future__ import annotations

from copy import deepcopy
import re
import uuid
from urllib.parse import quote

from tools.local_experiment.gateway import pod_usage as U

TARGETS = {
    'seaweedfs': ('shirokuma-storage', 'StatefulSet', 'seaweedfs', 'seaweedfs', 'object-storage', 'seaweedfs-data', '/data', 'seaweedfs-data-seaweedfs-0'),
    'polaris-postgresql': ('shirokuma-dev', 'StatefulSet', 'polaris-postgresql', 'postgresql', 'catalog-metadata', 'data', '/var/lib/postgresql/data', 'data-polaris-postgresql-0'),
    'polaris': ('shirokuma-dev', 'Deployment', 'polaris', 'polaris', 'catalog-server', None, None, None),
    'trino': ('shirokuma-dev', 'Deployment', 'trino-local', 'trino', None, None, None, None),
}
REASONS = frozenset(('READ_FAILED', 'READ_LIMIT', 'READ_TIMEOUT', 'CLEANUP_UNVERIFIED',
    'SCOPE_INVALID', 'TARGET_MISSING', 'TARGET_AMBIGUOUS', 'IDENTITY_CHANGED', 'SAMPLE_INVALID'))
LIMITS = ['FOUR_FIXED_WORKLOADS_TWO_PVCS_NOT_PLATFORM_COVERAGE',
          'CAPACITY_DECLARATIONS_NOT_USED_OR_PHYSICALLY_RESERVED_BYTES',
          'SERVICE_DEPENDENCY_NOT_EXCLUSIVE_OBJECT_OWNERSHIP',
          'NO_DIRECT_PVC_NOT_NO_DISK_USAGE', 'NO_FILESYSTEM_TRAVERSAL_OR_MOUNT_HEALTH_CHECK']


class StorageError(ValueError):
    pass


def require(ok, reason='SCOPE_INVALID'):
    if not ok: raise StorageError(reason)


def uid(value):
    try: require(type(value) is str and str(uuid.UUID(value)) == value)
    except (ValueError, TypeError, AttributeError): raise StorageError('SCOPE_INVALID') from None
    return value


def name(value):
    require(type(value) is str and re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', value))
    return value


def quantity(value):
    try: return U.quantity(value, 'memory')
    except ValueError: raise StorageError('SAMPLE_INVALID') from None


def unknown(component, reason):
    require(component in TARGETS and reason in REASONS)
    return dict(component=component, state='unknown', reason=reason, namespace=TARGETS[component][0],
                workload_uid=None, pod_uid=None, pvc=None, pv=None, retention=None,
                request_started_at=None, observed_at=None, used_bytes=None, usage_reason='NOT_MEASURED')


def _meta(obj, kind, namespace, expected=None):
    # Kubernetes list items may omit kind; the enclosing fixed typed endpoint
    # supplies it. Reject a contradictory explicit kind.
    require(type(obj) is dict and obj.get('kind', kind) == kind and type(obj.get('metadata')) is dict)
    meta = obj['metadata']
    require(meta.get('namespace') == namespace and
            (expected is None or meta.get('name') == expected) and meta.get('deletionTimestamp') is None)
    name(meta.get('name')); uid(meta.get('uid'))
    return meta


def _owner(obj, kind, owner_uid, owner_name):
    refs = obj['metadata'].get('ownerReferences', [])
    require(type(refs) is list and len(refs) <= 8)
    controllers = [v for v in refs if type(v) is dict and v.get('controller') is True]
    require(len(controllers) == 1 and controllers[0].get('apiVersion') == 'apps/v1' and
            controllers[0].get('kind') == kind and controllers[0].get('uid') == owner_uid and
            controllers[0].get('name') == owner_name)
    return controllers[0]['name']


def _snapshot(component, workload, pods, replica=None):
    ns, kind, workload_name, container_name, label, volume, mount, claim = TARGETS[component]
    w = _meta(workload, kind, ns, workload_name)
    selector = workload.get('spec', {}).get('selector', {}).get('matchLabels')
    require(type(selector) is dict and selector.get('app.kubernetes.io/name') == workload_name)
    if label: require(selector.get('app.kubernetes.io/component') == label)
    require(type(pods) is list and len(pods) <= 1, 'TARGET_AMBIGUOUS')
    if not pods:
        require(kind == 'StatefulSet' and workload.get('spec', {}).get('replicas') == 0)
        return (w['uid'], None, None, _retention(workload))
    pod = pods[0]; meta = _meta(pod, 'Pod', ns)
    labels = meta.get('labels', {})
    require(type(labels) is dict and all(labels.get(key) == value for key, value in selector.items()))
    if kind == 'StatefulSet':
        require(meta['name'] == workload_name + '-0')
        _owner(pod, kind, w['uid'], workload_name)
    else:
        rs = _meta(replica, 'ReplicaSet', ns)
        _owner(pod, 'ReplicaSet', rs['uid'], rs['name'])
        _owner(replica, 'Deployment', w['uid'], workload_name)
    spec = pod.get('spec', {}); containers = spec.get('containers'); volumes = spec.get('volumes', [])
    require(type(containers) is list and len(containers) == 1 and
            containers[0].get('name') == container_name and type(volumes) is list and len(volumes) <= 64)
    mounts = containers[0].get('volumeMounts', [])
    require(type(mounts) is list and len(mounts) <= 64)
    claims = [v for v in volumes if type(v) is dict and 'persistentVolumeClaim' in v]
    if kind == 'Deployment':
        require(not claims and all(type(v) is dict for v in mounts))
    else:
        require(len(claims) == 1 and claims[0].get('name') == volume and
                claims[0]['persistentVolumeClaim'].get('claimName') == claim and
                len([m for m in mounts if m.get('name') == volume and m.get('mountPath') == mount]) == 1)
    return (w['uid'], meta['uid'], claim if claims else None, _retention(workload) if kind == 'StatefulSet' else None)


def _retention(workload):
    policy = workload.get('spec', {}).get('persistentVolumeClaimRetentionPolicy', {})
    require(policy.get('whenDeleted') in ('Retain', 'Delete') and policy.get('whenScaled') in ('Retain', 'Delete'))
    template = workload.get('spec', {}).get('volumeClaimTemplates', [])
    require(type(template) is list and len(template) == 1 and
            template[0].get('metadata', {}).get('name') in ('seaweedfs-data', 'data'))
    prune = template[0]['metadata'].get('annotations', {}).get('kustomize.toolkit.fluxcd.io/prune')
    require(prune in (None, 'disabled'))
    return dict(when_deleted=policy['whenDeleted'], when_scaled=policy['whenScaled'], template_prune=prune)


def project(component, before, after, *, pvc=None, pv=None, request_started_at, observed_at):
    require(component in TARGETS)
    a = _snapshot(component, *before); b = _snapshot(component, *after)
    require(a == b, 'IDENTITY_CHANGED')
    ns, kind, _, _, _, _, _, claim = TARGETS[component]
    start, end = U.stamp(request_started_at), U.stamp(observed_at)
    require(0 <= end[1]-start[1] <= 30*10**9, 'SAMPLE_INVALID')
    value = unknown(component, 'READ_FAILED')
    value.update(state='observed' if claim else 'no_direct_pvc', reason=None,
                 workload_uid=a[0], pod_uid=a[1], request_started_at=start[0], observed_at=end[0])
    if claim:
        c = _meta(pvc, 'PersistentVolumeClaim', ns, claim)
        spec = pvc.get('spec', {}); status = pvc.get('status', {})
        require(type(spec) is dict and type(status) is dict)
        request = quantity(spec.get('resources', {}).get('requests', {}).get('storage'))
        capacity = status.get('capacity', {}).get('storage')
        capacity = quantity(capacity) if capacity is not None else None
        phase = status.get('phase'); require(phase in ('Bound', 'Pending', 'Lost'))
        volume = spec.get('volumeName')
        info = dict(name=claim, uid=c['uid'], phase=phase, requested_bytes=request,
                    capacity_bytes=capacity, storage_class=spec.get('storageClassName'),
                    volume_mode=spec.get('volumeMode'), pv_name=volume if volume else None)
        require(info['storage_class'] is None or name(info['storage_class']))
        require(info['volume_mode'] in (None, 'Filesystem', 'Block'))
        value['pvc'] = info
        if phase == 'Bound':
            require(type(volume) is str and name(volume))
            p = _meta(pv, 'PersistentVolume', None, volume)
            ref = pv.get('spec', {}).get('claimRef', {})
            require(ref.get('namespace') == ns and ref.get('name') == claim and ref.get('uid') == c['uid'])
            policy = pv['spec'].get('persistentVolumeReclaimPolicy')
            require(policy in ('Delete', 'Retain', 'Recycle'))
            value['pv'] = dict(name=volume, uid=p['uid'], capacity_bytes=quantity(
                pv['spec'].get('capacity', {}).get('storage')), reclaim_policy=policy)
        else:
            require(pv is None and (volume is None or name(volume)))
        value['retention'] = a[3]
    else:
        require(pvc is None and pv is None)
    return validate_row(value)


def validate_row(row):
    require(type(row) is dict and row.get('component') in TARGETS and set(row) == set(unknown('trino', 'READ_FAILED')))
    component = row['component']
    require(row['namespace'] == TARGETS[component][0] and row['used_bytes'] is None and row['usage_reason'] == 'NOT_MEASURED')
    if row['state'] == 'unknown':
        require(row == unknown(component, row['reason']))
        return deepcopy(row)
    require(row['state'] in ('observed', 'no_direct_pvc') and row['reason'] is None)
    uid(row['workload_uid'])
    if row['pod_uid'] is not None: uid(row['pod_uid'])
    start, end = U.stamp(row['request_started_at']), U.stamp(row['observed_at'])
    require(0 <= end[1]-start[1] <= 30*10**9)
    if row['state'] == 'no_direct_pvc':
        require(component in ('polaris', 'trino') and row['pod_uid'] is not None and
                row['pvc'] is None and row['pv'] is None and row['retention'] is None)
    else:
        require(component in ('seaweedfs', 'polaris-postgresql') and type(row['pvc']) is dict and
                set(row['pvc']) == {'name', 'uid', 'phase', 'requested_bytes', 'capacity_bytes',
                    'storage_class', 'volume_mode', 'pv_name'})
        pvc = row['pvc']; require(pvc['name'] == TARGETS[component][-1]); uid(pvc['uid'])
        require(pvc['phase'] in ('Bound', 'Pending', 'Lost'))
        require(pvc['pv_name'] is None or name(pvc['pv_name']))
        require(U.uint(pvc['requested_bytes']) > 0)
        require(pvc['capacity_bytes'] is None or U.uint(pvc['capacity_bytes']) > 0)
        require(pvc['storage_class'] is None or name(pvc['storage_class']))
        require(pvc['volume_mode'] in (None, 'Filesystem', 'Block'))
        require(type(row['retention']) is dict and set(row['retention']) == {'when_deleted', 'when_scaled', 'template_prune'} and
                row['retention']['when_deleted'] in ('Retain','Delete') and row['retention']['when_scaled'] in ('Retain','Delete') and
                row['retention']['template_prune'] in (None, 'disabled'))
        if pvc['phase'] == 'Bound':
            require(type(row['pv']) is dict and set(row['pv']) == {'name', 'uid', 'capacity_bytes', 'reclaim_policy'} and
                    pvc['pv_name'] == row['pv']['name'] and name(row['pv']['name']) and
                    row['pv']['reclaim_policy'] in ('Retain', 'Delete', 'Recycle'))
            uid(row['pv']['uid']); require(U.uint(row['pv']['capacity_bytes']) > 0)
        else:
            require(row['pv'] is None)
    return deepcopy(row)


def validate(value):
    require(type(value) is dict and set(value) == {'schema_version','components','limits'} and
            type(value['schema_version']) is int and value['schema_version'] == 1 and
            value['limits'] == LIMITS and type(value['components']) is list and len(value['components']) == 4)
    require([v.get('component') for v in value['components']] == list(TARGETS))
    return dict(schema_version=1, components=[validate_row(v) for v in value['components']], limits=LIMITS[:])


def unknown_all(reason):
    return dict(schema_version=1, components=[unknown(n, reason) for n in TARGETS], limits=LIMITS[:])


def collect(backend):
    rows = []
    for component in TARGETS:
        try: rows.append(_collect_one(backend, component))
        except Exception as error:
            if isinstance(error, U.PodUsageCleanupError): raise
            from tools.local_experiment.finops import contract as C
            reason = str(error) if isinstance(error, (StorageError, U.PodUsageError, C.FinOpsError)) and str(error) in REASONS else 'READ_FAILED'
            rows.append(unknown(component, reason))
    return validate(dict(schema_version=1, components=rows, limits=LIMITS[:]))


def _collect_one(backend, component):
    ns, kind, workload, _, label, _, _, claim = TARGETS[component]
    path = 'statefulsets' if kind == 'StatefulSet' else 'deployments'
    start = backend.now()
    before = backend.storage_get(f'/apis/apps/v1/namespaces/{ns}/{path}/{workload}')
    labels = 'app.kubernetes.io/name=' + workload
    if label: labels += ',app.kubernetes.io/component=' + label
    listed = backend.storage_get(f'/api/v1/namespaces/{ns}/pods?labelSelector={quote(labels,safe="")}&limit=2')
    require(listed.get('kind') == 'PodList' and listed.get('apiVersion') == 'v1' and
            type(listed.get('items')) is list and len(listed['items']) <= 1 and
            not listed.get('metadata', {}).get('continue'), 'TARGET_AMBIGUOUS')
    pods = listed['items']; replica = None
    if kind == 'Deployment' and pods:
        refs = pods[0].get('metadata', {}).get('ownerReferences', [])
        require(type(refs) is list and len(refs) <= 8)
        controllers = [r for r in refs if type(r) is dict and r.get('controller') is True and r.get('kind') == 'ReplicaSet']
        require(len(controllers) == 1)
        replica = backend.storage_get(f'/apis/apps/v1/namespaces/{ns}/replicasets/{name(controllers[0].get("name"))}')
    pvc = pv = None
    if claim:
        pvc = backend.storage_get(f'/api/v1/namespaces/{ns}/persistentvolumeclaims/{claim}')
        volume = pvc.get('spec', {}).get('volumeName')
        if volume and pvc.get('status', {}).get('phase') == 'Bound':
            pv = backend.storage_get(f'/api/v1/persistentvolumes/{name(volume)}')
    after = backend.storage_get(f'/apis/apps/v1/namespaces/{ns}/{path}/{workload}')
    after_pods = backend.storage_get(f'/api/v1/namespaces/{ns}/pods?labelSelector={quote(labels,safe="")}&limit=2')
    require(after_pods.get('kind') == 'PodList' and type(after_pods.get('items')) is list and
            len(after_pods['items']) <= 1 and not after_pods.get('metadata', {}).get('continue'), 'IDENTITY_CHANGED')
    after_replica = backend.storage_get(f'/apis/apps/v1/namespaces/{ns}/replicasets/{replica["metadata"]["name"]}') if replica else None
    if pvc:
        later = backend.storage_get(f'/api/v1/namespaces/{ns}/persistentvolumeclaims/{claim}')
        _meta(later, 'PersistentVolumeClaim', ns, claim)
        def claim_state(item):
            return (item.get('metadata', {}).get('uid'), item.get('spec', {}).get('volumeName'),
                    item.get('spec', {}).get('resources', {}).get('requests', {}).get('storage'),
                    item.get('status', {}).get('phase'), item.get('status', {}).get('capacity', {}).get('storage'))
        require(claim_state(later) == claim_state(pvc), 'IDENTITY_CHANGED')
        if pv:
            later_pv = backend.storage_get(f'/api/v1/persistentvolumes/{name(pv["metadata"]["name"])}')
            _meta(later_pv, 'PersistentVolume', None, pv['metadata']['name'])
            def pv_state(item):
                return (item.get('metadata', {}).get('uid'), item.get('spec', {}).get('claimRef'),
                        item.get('spec', {}).get('capacity', {}).get('storage'),
                        item.get('spec', {}).get('persistentVolumeReclaimPolicy'))
            require(pv_state(later_pv) == pv_state(pv), 'IDENTITY_CHANGED')
    return project(component, (before,pods,replica), (after,after_pods['items'],after_replica),
                   pvc=pvc, pv=pv, request_started_at=start, observed_at=backend.now())
