"""Bounded Kubernetes storage reference inventory; never measures used bytes."""
from __future__ import annotations

from copy import deepcopy
import json
import posixpath
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


def _same_json(first, second):
    # Python considers False == 0 and True == 1; API reads must retain JSON types.
    try:
        return json.dumps(first, sort_keys=True, allow_nan=False) == json.dumps(
            second, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        raise StorageError('IDENTITY_CHANGED') from None


def uid(value):
    try: require(type(value) is str and str(uuid.UUID(value)) == value)
    except (ValueError, TypeError, AttributeError): raise StorageError('SCOPE_INVALID') from None
    return value


def name(value):
    require(type(value) is str and len(value) <= 253 and re.fullmatch(
        r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
        r'(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*', value))
    return value


def quantity(value):
    try: return U.quantity(value, 'memory')
    except ValueError: raise StorageError('SAMPLE_INVALID') from None


def unknown(component, reason):
    require(type(component) is str and component in TARGETS and
            type(reason) is str and reason in REASONS)
    return dict(component=component, state='unknown', reason=reason, namespace=TARGETS[component][0],
                workload_uid=None, pod_uid=None, pvc=None, pv=None, retention=None,
                request_started_at=None, observed_at=None, used_bytes=None, usage_reason='NOT_MEASURED')


def _meta(obj, kind, namespace, expected=None):
    # Kubernetes list items may omit kind; the enclosing fixed typed endpoint
    # supplies it. Reject contradictory explicit kind or API version.
    api_version = 'apps/v1' if kind in ('StatefulSet', 'Deployment', 'ReplicaSet') else 'v1'
    require(type(obj) is dict and obj.get('kind', kind) == kind and
            obj.get('apiVersion', api_version) == api_version and type(obj.get('metadata')) is dict)
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
    workload_spec = workload.get('spec')
    require(type(workload_spec) is dict and type(workload_spec.get('selector')) is dict)
    workload_selector = workload_spec['selector']
    # The scoped PodList query uses only matchLabels. Extra expressions could
    # exclude that Pod even while the returned labels appear to match.
    require(set(workload_selector) <= {'matchLabels', 'matchExpressions'} and
            workload_selector.get('matchExpressions', []) == [] and
            type(workload_selector.get('matchExpressions', [])) is list)
    selector = workload_selector.get('matchLabels')
    require(type(selector) is dict and selector.get('app.kubernetes.io/name') == workload_name)
    if label: require(selector.get('app.kubernetes.io/component') == label)
    require(type(pods) is list and len(pods) <= 1, 'TARGET_AMBIGUOUS')
    replicas = workload_spec.get('replicas')
    require(type(replicas) is int and replicas == (1 if pods else 0))
    if not pods:
        require(kind == 'StatefulSet')
        return (w['uid'], None, None, _retention(workload, volume))
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
        # An owned ReplicaSet with a foreign selector does not establish that
        # this Pod belongs to the fixed Deployment's selected replica chain.
        if 'spec' in replica:
            rs_spec = replica['spec']
            require(type(rs_spec) is dict and type(rs_spec.get('selector')) is dict)
            rs_selector = rs_spec['selector']
            rs_labels = rs_selector.get('matchLabels')
            require(set(rs_selector) == {'matchLabels'} and type(rs_labels) is dict and
                    all(rs_labels.get(key) == value for key, value in selector.items()) and
                    all(labels.get(key) == value for key, value in rs_labels.items()))
    spec = pod.get('spec')
    require(type(spec) is dict)
    containers = spec.get('containers'); volumes = spec.get('volumes', [])
    require(type(containers) is list and len(containers) == 1 and
            type(containers[0]) is dict and containers[0].get('name') == container_name and
            type(volumes) is list and len(volumes) <= 64)
    mounts = containers[0].get('volumeMounts', [])
    require(type(mounts) is list and len(mounts) <= 64 and all(type(m) is dict for m in mounts))
    # A fixed reference must have an unambiguous Pod volume name and directory
    # destination, including lexical aliases of non-PVC mounts at the same
    # directory (e.g. /data and /data/.) in the same container. This is not
    # a filesystem/symlink resolution or live mount-health check.
    # Pod volume names are DNS_LABELs, unlike PV/PVC names (DNS subdomains).
    require(all(type(v) is dict and type(v.get('name')) is str and
                re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', v['name'])
                for v in volumes))
    volume_names = {v['name'] for v in volumes}
    require(len(volume_names) == len(volumes) and
            all(type(m.get('name')) is str and m['name'] in volume_names for m in mounts) and
              all(type(m.get('mountPath')) is str and m['mountPath'].startswith('/') and
                  not m['mountPath'].startswith('//') and '\x00' not in m['mountPath']
                  for m in mounts) and
            len({posixpath.normpath(m['mountPath']) for m in mounts}) == len(mounts))
    claims = [v for v in volumes if type(v) is dict and 'persistentVolumeClaim' in v]
    if kind == 'Deployment':
        # Generic ephemeral volumes provision PVCs even without an explicit
        # persistentVolumeClaim entry; they cannot support no_direct_pvc.
        require(not claims and all(type(v) is dict and 'ephemeral' not in v for v in volumes) and
                all(type(v) is dict for v in mounts))
    else:
        # A generic ephemeral volume provisions another PVC. The fixed two-PVC
        # inventory cannot describe that additional claim as part of this row.
        # Another mount below the claim directory shadows part of its contents;
        # lexical normalization bounds this check without claiming symlink or
        # actual filesystem/mount resolution.
        require(all(m.get('name') == volume or not (
                    posixpath.normpath(m['mountPath']).startswith(mount + '/') or
                    posixpath.normpath(m['mountPath']) == '/' or
                    mount.startswith(posixpath.normpath(m['mountPath']) + '/'))
                    for m in mounts))
        # Pin the entire claim mount, not merely one matching mount: a second
        # mount or subPath/device mapping would describe a different exposure.
        # Init/debug containers can mount the same Pod volume independently.
        for field in ('initContainers', 'ephemeralContainers'):
            others = spec.get(field, [])
            require(type(others) is list)
            for other in others:
                require(type(other) is dict)
                for mapping in ('volumeMounts', 'volumeDevices'):
                    entries = other.get(mapping, [])
                    require(type(entries) is list and all(type(entry) is dict and
                            entry.get('name') != volume for entry in entries))
        claim_mounts = [m for m in mounts if m.get('name') == volume]
        require(all(type(v) is dict and 'ephemeral' not in v for v in volumes) and
                sum(v.get('name') == volume for v in volumes) == 1 and
                len(claims) == 1 and claims[0].get('name') == volume and
                set(claims[0]) == {'name', 'persistentVolumeClaim'} and
                type(claims[0]['persistentVolumeClaim']) is dict and
                claims[0]['persistentVolumeClaim'].get('claimName') == claim and
                set(claims[0]['persistentVolumeClaim']) <= {'claimName', 'readOnly'} and
                claims[0]['persistentVolumeClaim'].get('readOnly', False) is False and
                len(claim_mounts) == 1 and claim_mounts[0].get('mountPath') == mount and
                set(claim_mounts[0]) <= {'name', 'mountPath', 'readOnly'} and
                claim_mounts[0].get('readOnly', False) is False and
                not containers[0].get('volumeDevices'))
    return (w['uid'], meta['uid'], claim if claims else None, _retention(workload, volume) if kind == 'StatefulSet' else None)


def _retention(workload, volume):
    spec = workload['spec']
    policy = spec.get('persistentVolumeClaimRetentionPolicy', {})
    require(type(policy) is dict and policy.get('whenDeleted') in ('Retain', 'Delete') and
            policy.get('whenScaled') in ('Retain', 'Delete'))
    template = spec.get('volumeClaimTemplates', [])
    require(type(template) is list and len(template) == 1 and
            type(template[0]) is dict and type(template[0].get('metadata')) is dict and
            template[0]['metadata'].get('name') == volume)
    # Both fixed StatefulSets mount the claim as a directory; the template
    # must not request a Block volume even when a current PVC says Filesystem.
    require(type(template[0].get('spec')) is dict and
            template[0]['spec'].get('volumeMode') in (None, 'Filesystem'))
    annotations = template[0]['metadata'].get('annotations', {})
    require(type(annotations) is dict)
    prune = annotations.get('kustomize.toolkit.fluxcd.io/prune')
    require(prune in (None, 'disabled'))
    return dict(when_deleted=policy['whenDeleted'], when_scaled=policy['whenScaled'], template_prune=prune)


def project(component, before, after, *, pvc=None, pv=None, request_started_at, observed_at):
    require(component in TARGETS)
    a = _snapshot(component, *before); b = _snapshot(component, *after)
    require(a == b, 'IDENTITY_CHANGED')
    # UIDs alone do not pin a live object's spec or its ownership/selection.
    # Ignore status/resourceVersion (which may advance independently), but do
    # not publish the first reference if its scoped source changed mid-read.
    for original, later in zip(before, after):
        if original is None:
            continue
        if type(original) is list:
            pairs = zip(original, later)
        else:
            pairs = ((original, later),)
        for first_obj, last_obj in pairs:
            require(first_obj['metadata']['name'] == last_obj['metadata']['name'] and
                    _same_json(first_obj.get('spec'), last_obj.get('spec')) and
                    _same_json(first_obj['metadata'].get('labels'), last_obj['metadata'].get('labels')) and
                    _same_json(first_obj['metadata'].get('ownerReferences'),
                               last_obj['metadata'].get('ownerReferences')), 'IDENTITY_CHANGED')
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
        resources = spec.get('resources', {}); capacity_map = status.get('capacity', {})
        require(type(resources) is dict and type(resources.get('requests', {})) is dict and
                type(capacity_map) is dict)
        request = quantity(resources.get('requests', {}).get('storage'))
        capacity = capacity_map.get('storage')
        capacity = quantity(capacity) if capacity is not None else None
        phase = status.get('phase'); require(phase in ('Bound', 'Pending', 'Lost'))
        volume = spec.get('volumeName')
        info = dict(name=claim, uid=c['uid'], phase=phase, requested_bytes=request,
                    capacity_bytes=capacity, storage_class=spec.get('storageClassName'),
                    volume_mode=spec.get('volumeMode'), pv_name=volume if volume else None)
        require(info['storage_class'] is None or name(info['storage_class']))
        # Both fixed claims are mounted at directory paths, never via volumeDevices.
        require(info['volume_mode'] in (None, 'Filesystem'))
        value['pvc'] = info
        if phase == 'Bound':
            require(type(volume) is str and name(volume))
            p = _meta(pv, 'PersistentVolume', None, volume)
            pv_spec = pv.get('spec', {}); require(type(pv_spec) is dict)
            # An explicitly non-Bound PV cannot support this Bound PVC reference.
            pv_status = pv.get('status', {})
            require(type(pv_status) is dict and pv_status.get('phase', 'Bound') == 'Bound')
            ref = pv_spec.get('claimRef', {}); pv_capacity = pv_spec.get('capacity', {})
            require(type(ref) is dict and type(pv_capacity) is dict)
            require(ref.get('namespace') == ns and ref.get('name') == claim and
                    ref.get('uid') == c['uid'] and
                    ref.get('kind', 'PersistentVolumeClaim') == 'PersistentVolumeClaim' and
                    ref.get('apiVersion', 'v1') == 'v1')
            # Directory-mounted claims cannot be backed by a block-mode PV.
            require(pv_spec.get('volumeMode') in (None, 'Filesystem'))
            policy = pv_spec.get('persistentVolumeReclaimPolicy')
            require(policy in ('Delete', 'Retain', 'Recycle'))
            value['pv'] = dict(name=volume, uid=p['uid'], capacity_bytes=quantity(
                pv_capacity.get('storage')), reclaim_policy=policy)
        else:
            require(pv is None and (volume is None or name(volume)))
        value['retention'] = a[3]
    else:
        require(pvc is None and pv is None)
    return validate_row(value)


def validate_row(row):
    require(type(row) is dict and type(row.get('component')) is str and
            row['component'] in TARGETS and set(row) == set(unknown('trino', 'READ_FAILED')))
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
        require(pvc['volume_mode'] in (None, 'Filesystem'))
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
    require(all(type(v) is dict for v in value['components']) and
            [v.get('component') for v in value['components']] == list(TARGETS))
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


def _complete_pod_list(response):
    metadata = response.get('metadata', {})
    return type(metadata) is dict and metadata.get('continue', '') == '' and type(
        metadata.get('continue', '')) is str


def _collect_one(backend, component):
    ns, kind, workload, _, label, _, _, claim = TARGETS[component]
    path = 'statefulsets' if kind == 'StatefulSet' else 'deployments'
    start = backend.now()
    before = backend.storage_get(f'/apis/apps/v1/namespaces/{ns}/{path}/{workload}')
    labels = 'app.kubernetes.io/name=' + workload
    if label: labels += ',app.kubernetes.io/component=' + label
    listed = backend.storage_get(f'/api/v1/namespaces/{ns}/pods?labelSelector={quote(labels,safe="")}&limit=2')
    require(type(listed) is dict and listed.get('kind') == 'PodList' and listed.get('apiVersion') == 'v1' and
            type(listed.get('items')) is list and len(listed['items']) <= 1 and
            all(type(p) is dict and type(p.get('metadata')) is dict for p in listed['items']) and
            _complete_pod_list(listed), 'TARGET_AMBIGUOUS')
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
        _meta(pvc, 'PersistentVolumeClaim', ns, claim)
        spec = pvc.get('spec', {}); status = pvc.get('status', {})
        require(type(spec) is dict and type(status) is dict)
        volume = spec.get('volumeName')
        require(volume is None or (type(volume) is str and (not volume or name(volume))))
        if volume and status.get('phase') == 'Bound':
            pv = backend.storage_get(f'/api/v1/persistentvolumes/{name(volume)}')
    after = backend.storage_get(f'/apis/apps/v1/namespaces/{ns}/{path}/{workload}')
    after_pods = backend.storage_get(f'/api/v1/namespaces/{ns}/pods?labelSelector={quote(labels,safe="")}&limit=2')
    require(type(after_pods) is dict and after_pods.get('kind') == 'PodList' and after_pods.get('apiVersion') == 'v1' and
            type(after_pods.get('items')) is list and
            len(after_pods['items']) <= 1 and
            all(type(p) is dict and type(p.get('metadata')) is dict for p in after_pods['items']) and
            _complete_pod_list(after_pods), 'IDENTITY_CHANGED')
    after_replica = backend.storage_get(f'/apis/apps/v1/namespaces/{ns}/replicasets/{replica["metadata"]["name"]}') if replica else None
    if pvc:
        later = backend.storage_get(f'/api/v1/namespaces/{ns}/persistentvolumeclaims/{claim}')
        _meta(later, 'PersistentVolumeClaim', ns, claim)
        def claim_state(item):
            spec = item.get('spec', {}); status = item.get('status', {})
            require(type(spec) is dict and type(status) is dict, 'IDENTITY_CHANGED')
            resources = spec.get('resources', {}); capacity = status.get('capacity', {})
            require(type(resources) is dict and type(resources.get('requests', {})) is dict and
                    type(capacity) is dict, 'IDENTITY_CHANGED')
            return (item['metadata']['uid'], spec.get('volumeName'),
                    resources.get('requests', {}).get('storage'), spec.get('storageClassName'),
                    spec.get('volumeMode'), status.get('phase'), capacity.get('storage'))
        require(_same_json(claim_state(later), claim_state(pvc)), 'IDENTITY_CHANGED')
        if pv:
            later_pv = backend.storage_get(f'/api/v1/persistentvolumes/{name(pv["metadata"]["name"])}')
            _meta(later_pv, 'PersistentVolume', None, pv['metadata']['name'])
            def pv_state(item):
                spec = item.get('spec', {})
                require(type(spec) is dict and type(spec.get('capacity', {})) is dict,
                        'IDENTITY_CHANGED')
                status = item.get('status', {})
                require(type(status) is dict and status.get('phase', 'Bound') == 'Bound',
                        'IDENTITY_CHANGED')
                return (item['metadata']['uid'], spec.get('claimRef'),
                        spec.get('capacity', {}).get('storage'),
                        spec.get('persistentVolumeReclaimPolicy'), spec.get('volumeMode'))
            require(_same_json(pv_state(later_pv), pv_state(pv)), 'IDENTITY_CHANGED')
    return project(component, (before,pods,replica), (after,after_pods['items'],after_replica),
                   pvc=pvc, pv=pv, request_started_at=start, observed_at=backend.now())
