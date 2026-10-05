from copy import deepcopy
import json
import unittest
from unittest.mock import Mock

from tools.local_experiment.finops import storage as S, contract as C, render as H, history as HISTORY, run as R
from test_local_finops_contract import NOW, component_args, row
from test_local_finops_process_report import report, processes

W = '11111111-1111-4111-8111-111111111111'
P = '22222222-2222-4222-8222-222222222222'
RS_UID = '33333333-3333-4333-8333-333333333333'
Q = '44444444-4444-4444-8444-444444444444'
V = '55555555-5555-4555-8555-555555555555'


def objects(component):
    ns, kind, target, container, label, volume, mount, claim = S.TARGETS[component]
    selector = {'app.kubernetes.io/name':target}
    if label: selector['app.kubernetes.io/component'] = label
    template = [{'metadata':{'name':volume,'annotations':{'kustomize.toolkit.fluxcd.io/prune':'disabled'}},
                 'spec':{'resources':{'requests':{'storage':'20Gi'}}}}] if claim else []
    workload = {'kind':kind,'metadata':{'namespace':ns,'name':target,'uid':W},
                'spec':{'replicas':1,'selector':{'matchLabels':selector},'volumeClaimTemplates':template,
                        'persistentVolumeClaimRetentionPolicy':{'whenDeleted':'Retain','whenScaled':'Retain'}}}
    replica = {'kind':'ReplicaSet','metadata':{'namespace':ns,'name':target+'-abc12','uid':RS_UID,
              'ownerReferences':[{'apiVersion':'apps/v1','controller':True,'kind':'Deployment','name':target,'uid':W}]}} if kind=='Deployment' else None
    pod = {'kind':'Pod','metadata':{'namespace':ns,'name':target+'-0' if kind=='StatefulSet' else target+'-abc12-pod',
           'uid':P,'labels':selector,'ownerReferences':[{'apiVersion':'apps/v1','controller':True,'kind':kind if kind=='StatefulSet' else 'ReplicaSet',
                  'name':target if kind=='StatefulSet' else target+'-abc12','uid':W if kind=='StatefulSet' else RS_UID}]},
           'spec':{'containers':[{'name':container,'volumeMounts':[{'name':volume,'mountPath':mount}] if claim else []}],
                   'volumes':[{'name':volume,'persistentVolumeClaim':{'claimName':claim}}] if claim else [{'name':'tmp','emptyDir':{}}]}}
    pvc = {'kind':'PersistentVolumeClaim','metadata':{'namespace':ns,'name':claim,'uid':Q},
           'spec':{'resources':{'requests':{'storage':'20Gi'}},'volumeName':'pvc-'+Q,
                   'storageClassName':'local-path','volumeMode':'Filesystem'},
           'status':{'phase':'Bound','capacity':{'storage':'20Gi'}}} if claim else None
    pv = {'kind':'PersistentVolume','metadata':{'name':'pvc-'+Q,'uid':V},
          'spec':{'claimRef':{'namespace':ns,'name':claim,'uid':Q},
                  'capacity':{'storage':'20Gi'},'persistentVolumeReclaimPolicy':'Delete'}} if claim else None
    return workload, [pod], replica, pvc, pv


def projected(component, data=None):
    w,p,r,c,v = deepcopy(data if data is not None else objects(component))
    return S.project(component,(w,p,r),(deepcopy(w),deepcopy(p),deepcopy(r)),pvc=c,pv=v,
                     request_started_at='2026-09-20T00:00:20Z',observed_at=NOW)


class StorageTests(unittest.TestCase):
    def test_fixed_chain_capacity_and_no_direct_pvc_are_distinct(self):
        for component in S.TARGETS:
            item = projected(component)
            self.assertIsNone(item['used_bytes']); self.assertEqual(item['usage_reason'],'NOT_MEASURED')
            if component in ('seaweedfs','polaris-postgresql'):
                self.assertEqual(item['state'],'observed')
                self.assertEqual(item['pvc']['requested_bytes'],'21474836480')
                self.assertEqual(item['pv']['reclaim_policy'],'Delete')
            else:
                self.assertEqual(item['state'],'no_direct_pvc');self.assertIsNone(item['pvc'])

    def test_wrong_controller_claim_binding_and_mount_refused(self):
        for component,change in (
            ('seaweedfs',lambda d:d[1][0]['metadata']['ownerReferences'][0].update(uid=RS_UID)),
            ('seaweedfs',lambda d:d[1][0]['metadata']['ownerReferences'][0].update(name='foreign')),
            ('seaweedfs',lambda d:d[1][0]['metadata']['ownerReferences'][0].update(apiVersion='v1')),
            ('seaweedfs',lambda d:d[1][0]['spec']['containers'][0]['volumeMounts'][0].update(mountPath='/wrong')),
            ('seaweedfs',lambda d:d[3]['metadata'].update(namespace='shirokuma-dev')),
            ('seaweedfs',lambda d:d[4]['spec']['claimRef'].update(uid=RS_UID)),
            ('polaris',lambda d:d[2]['metadata']['ownerReferences'][0].update(uid=Q)),
            ('polaris',lambda d:d[2]['metadata']['ownerReferences'][0].update(name='foreign')),
            ('trino',lambda d:d[1][0]['spec']['volumes'].append({'name':'extra','persistentVolumeClaim':{'claimName':'foreign'}}))):
            data=deepcopy(objects(component));change(data)
            with self.subTest(component=component,change=change),self.assertRaises(S.StorageError):
                projected(component,data)

    def test_fixed_directory_mount_cannot_reference_block_mode_claim(self):
        for component in ('seaweedfs', 'polaris-postgresql'):
            data = deepcopy(objects(component))
            data[3]['spec']['volumeMode'] = 'Block'
            with self.subTest(component=component), self.assertRaises(S.StorageError):
                projected(component, data)
            row = projected(component)
            row['pvc']['volume_mode'] = 'Block'
            with self.assertRaises(S.StorageError):
                S.validate_row(row)
            data[3]['spec'].pop('volumeMode')
            self.assertEqual(projected(component, data)['state'], 'observed')

    def test_bound_directory_claim_cannot_resolve_to_block_mode_pv(self):
        for component in ('seaweedfs', 'polaris-postgresql'):
            data = deepcopy(objects(component))
            data[4]['spec']['volumeMode'] = 'Block'
            with self.subTest(component=component), self.assertRaises(S.StorageError):
                projected(component, data)
            data[4]['spec']['volumeMode'] = 'Filesystem'
            self.assertEqual(projected(component, data)['state'], 'observed')

    def test_directory_claim_template_cannot_provision_block_mode(self):
        for component in ('seaweedfs', 'polaris-postgresql'):
            data = deepcopy(objects(component))
            data[0]['spec']['volumeClaimTemplates'][0]['spec']['volumeMode'] = 'Block'
            with self.subTest(component=component), self.assertRaises(S.StorageError):
                projected(component, data)
            data[0]['spec']['volumeClaimTemplates'][0]['spec']['volumeMode'] = 'Filesystem'
            self.assertEqual(projected(component, data)['state'], 'observed')

    def test_claim_template_must_match_fixed_workload_volume(self):
        for component, foreign in (('seaweedfs', 'data'),
                                   ('polaris-postgresql', 'seaweedfs-data')):
            data = deepcopy(objects(component))
            data[0]['spec']['volumeClaimTemplates'][0]['metadata']['name'] = foreign
            with self.subTest(component=component), self.assertRaises(S.StorageError):
                projected(component, data)

    def test_pending_retained_claim_zero_pods_not_zero_capacity(self):
        data=list(objects('seaweedfs'));data[0]['spec']['replicas']=0;data[1]=[]
        data[3]['spec'].pop('volumeName');data[3]['status']={'phase':'Pending'};data[4]=None
        value=projected('seaweedfs',data)
        self.assertEqual(value['state'],'observed');self.assertIsNone(value['pod_uid'])
        self.assertEqual(value['pvc']['requested_bytes'],'21474836480');self.assertIsNone(value['pv'])

    def test_selected_pod_requires_single_declared_replica(self):
        for component in S.TARGETS:
            for replicas in (0, 2, False, '1', None):
                data = deepcopy(objects(component))
                data[0]['spec']['replicas'] = replicas
                with self.subTest(component=component, replicas=replicas), self.assertRaises(S.StorageError):
                    projected(component, data)
        data = list(deepcopy(objects('seaweedfs')))
        data[0]['spec']['replicas'] = False
        data[1] = []
        with self.assertRaises(S.StorageError):
            projected('seaweedfs', data)

    def test_malformed_reference_envelopes_fail_with_scoped_error(self):
        for component, index, mutate in (
            ('seaweedfs', 0, lambda w: w.update(spec=None)),
            ('seaweedfs', 0, lambda w: w['spec'].update(selector=None)),
            ('seaweedfs', 0, lambda w: w['spec'].update(persistentVolumeClaimRetentionPolicy=None)),
            ('seaweedfs', 0, lambda w: w['spec'].update(volumeClaimTemplates=[None])),
            ('seaweedfs', 1, lambda pods: pods[0].update(spec=None)),
            ('seaweedfs', 1, lambda pods: pods[0]['spec'].update(containers=[None])),
            ('seaweedfs', 1, lambda pods: pods[0]['spec'].update(volumes=[{'name': 'data', 'persistentVolumeClaim': None}])),
            ('seaweedfs', 1, lambda pods: pods[0]['spec']['containers'][0].update(volumeMounts=[None])),
            ('trino', 1, lambda pods: pods[0].update(spec=None)),
            ('trino', 1, lambda pods: pods[0]['spec'].update(containers=[None])),
        ):
            data = deepcopy(objects(component))
            mutate(data[index])
            with self.subTest(component=component, index=index, mutate=mutate), self.assertRaises(S.StorageError):
                projected(component, data)

    def test_malformed_claim_and_volume_envelopes_fail_with_scoped_error(self):
        for index, mutate in (
            (3, lambda pvc: pvc['spec'].update(resources=None)),
            (3, lambda pvc: pvc['spec'].update(resources={'requests': None})),
            (3, lambda pvc: pvc['status'].update(capacity=None)),
            (4, lambda pv: pv.update(spec=None)),
            (4, lambda pv: pv['spec'].update(claimRef=None)),
            (4, lambda pv: pv['spec'].update(capacity=None)),
        ):
            data = deepcopy(objects('seaweedfs'))
            mutate(data[index])
            with self.subTest(index=index, mutate=mutate), self.assertRaises(S.StorageError):
                projected('seaweedfs', data)

    def test_deployment_ephemeral_claim_is_not_no_direct_pvc(self):
        for component in ('polaris', 'trino'):
            data = deepcopy(objects(component))
            data[1][0]['spec']['volumes'].append({
                'name':'scratch', 'ephemeral':{'volumeClaimTemplate':{
                    'spec':{'resources':{'requests':{'storage':'1Gi'}}}}}})
            with self.subTest(component=component), self.assertRaises(S.StorageError):
                projected(component, data)
        self.assertEqual(projected('trino')['state'], 'no_direct_pvc')

    def test_deployment_replicaset_selector_must_select_reported_pod(self):
        for component in ('polaris', 'trino'):
            data = deepcopy(objects(component))
            data[2]['spec'] = {'selector': {'matchLabels': {
                **data[0]['spec']['selector']['matchLabels'], 'pod-template-hash': 'owned'}}}
            data[1][0]['metadata']['labels']['pod-template-hash'] = 'owned'
            self.assertEqual(projected(component, data)['state'], 'no_direct_pvc')
            for selector in ({'matchLabels': {'pod-template-hash': 'foreign'}},
                             {'matchLabels': {}}, None, {'matchExpressions': []}):
                changed = deepcopy(data)
                changed[2]['spec']['selector'] = selector
                with self.subTest(component=component, selector=selector), self.assertRaises(S.StorageError):
                    projected(component, changed)

    def test_lost_claim_retains_reference_without_claiming_live_pv(self):
        data=list(objects('seaweedfs'));data[3]['status']['phase']='Lost';data[4]=None
        value=projected('seaweedfs',data)
        self.assertEqual(value['pvc']['phase'],'Lost');self.assertEqual(value['pvc']['pv_name'],'pvc-'+Q)
        self.assertIsNone(value['pv']);self.assertEqual(value['pvc']['requested_bytes'],'21474836480')

    def test_pending_prebound_claim_keeps_request_without_claiming_verified_pv(self):
        data=list(objects('seaweedfs'));data[3]['status']['phase']='Pending';data[4]=None
        value=projected('seaweedfs',data)
        self.assertEqual(value['pvc']['requested_bytes'],'21474836480')
        self.assertEqual(value['pvc']['pv_name'],'pvc-'+Q)
        self.assertIsNone(value['pv']);self.assertIsNone(value['used_bytes'])
        value['pvc']['pv_name']={'invalid':'PRIVATE'}
        with self.assertRaises(S.StorageError):S.validate_row(value)

    def test_quantity_binary_decimal_invalid_and_unknown_public(self):
        self.assertEqual(S.quantity('20Gi'),'21474836480')
        self.assertEqual(S.quantity('20G'),'20000000000')
        for raw in ('-1Gi','1.1Gi','20K','999999999999999999999Gi',None):
            with self.subTest(raw=raw),self.assertRaises(S.StorageError):S.quantity(raw)
        unknown=S.unknown_all('READ_FAILED');self.assertEqual(S.validate(unknown),unknown)
        unknown['components'][0]['pvc']={'secret':'BAD'}
        with self.assertRaises(S.StorageError):S.validate(unknown)

    def test_malformed_saved_storage_rows_fail_with_scoped_error(self):
        for malformed in (None, False, 0, [], 'trino'):
            value = S.unknown_all('READ_FAILED')
            value['components'][0] = malformed
            with self.subTest(malformed=malformed), self.assertRaises(S.StorageError):
                S.validate(value)
        for reason in ([], {}, None, False):
            value = S.unknown_all('READ_FAILED')
            value['components'][0]['reason'] = reason
            with self.subTest(reason=reason), self.assertRaises(S.StorageError):
                S.validate(value)

    def test_v3_roundtrip_render_history_v1_v2_compat(self):
        storage=S.validate(dict(schema_version=1,components=[projected(n) for n in S.TARGETS],limits=S.LIMITS[:]))
        previous=report(processes()); current=C.build_report(previous['components'],previous['disks'],previous['inference'],
            observed_at=NOW,local_processes=processes(),storage=storage)
        self.assertEqual(current['schema_version'],3);self.assertEqual(C.validate_public(current),current)
        html=H.render(current)
        self.assertIn('未計測（NOT_MEASURED）',html)
        self.assertIn('20 GiB',html)
        self.assertIn('直接のPVC参照なし',html)
        pages=H.render(report());self.assertNotIn('id="storage"',pages)
        hist=HISTORY.render(HISTORY.parse(json.dumps([report(),previous,current]).encode()))
        self.assertEqual(hist.count('このschemaにはPVC参照は含まれません'),2)
        self.assertEqual(hist.count('id="observation-3-storage"'),1)

    def test_storage_collection_rejects_changed_claim_and_partial_read(self):
        data=objects('seaweedfs');w,p,r,c,v=data
        responses=[w,{'kind':'PodList','apiVersion':'v1','metadata':{},'items':p},c,v,deepcopy(w),
                   {'kind':'PodList','apiVersion':'v1','metadata':{},'items':deepcopy(p)},deepcopy(c),deepcopy(v)]
        backend=Mock();backend.now.return_value=NOW;backend.storage_get.side_effect=deepcopy(responses)
        self.assertEqual(S._collect_one(backend,'seaweedfs')['state'],'observed')
        paths=[call.args[0] for call in backend.storage_get.call_args_list]
        self.assertTrue(all(path.startswith(('/api/v1/','/apis/apps/v1/')) for path in paths))
        changed=deepcopy(responses);changed[-2]['metadata']['uid']=RS_UID
        backend.storage_get.side_effect=changed
        with self.assertRaisesRegex(S.StorageError,'IDENTITY_CHANGED'):S._collect_one(backend,'seaweedfs')
        changed=deepcopy(responses);changed[4]['spec']['persistentVolumeClaimRetentionPolicy']['whenDeleted']='Delete'
        backend.storage_get.side_effect=changed
        with self.assertRaisesRegex(S.StorageError,'IDENTITY_CHANGED'):S._collect_one(backend,'seaweedfs')
        changed=deepcopy(responses);changed[-1]['metadata']['deletionTimestamp']=NOW
        backend.storage_get.side_effect=changed
        with self.assertRaises(S.StorageError):S._collect_one(backend,'seaweedfs')
        backend.storage_get.side_effect=RuntimeError('SECRET')
        self.assertTrue(all(row['reason']=='READ_FAILED' for row in S.collect(backend)['components']))

    def test_storage_collection_rejects_storage_semantics_changed_between_reads(self):
        for component in ('seaweedfs', 'polaris-postgresql'):
            w, p, _, c, v = objects(component)
            original = [w, {'kind':'PodList','apiVersion':'v1','metadata':{},'items':p},
                        c, v, deepcopy(w),
                        {'kind':'PodList','apiVersion':'v1','metadata':{},'items':deepcopy(p)},
                        deepcopy(c), deepcopy(v)]
            for index, field, value in ((-2, 'volumeMode', 'Block'),
                                        (-2, 'storageClassName', 'foreign'),
                                        (-1, 'volumeMode', 'Block')):
                changed = deepcopy(original)
                changed[index]['spec'][field] = value
                backend = Mock()
                backend.now.return_value = NOW
                backend.storage_get.side_effect = changed
                with self.subTest(component=component, field=field), self.assertRaisesRegex(
                        S.StorageError, 'IDENTITY_CHANGED'):
                    S._collect_one(backend, component)

    def test_storage_collection_refuses_json_type_alias_in_pv_reference(self):
        w, p, _, c, v = objects('seaweedfs')
        v['spec']['claimRef']['resourceVersion'] = False
        later_pv = deepcopy(v)
        later_pv['spec']['claimRef']['resourceVersion'] = 0
        responses = [w, {'kind':'PodList','apiVersion':'v1','metadata':{},'items':p},
                     c, v, deepcopy(w),
                     {'kind':'PodList','apiVersion':'v1','metadata':{},'items':deepcopy(p)},
                     deepcopy(c), later_pv]
        backend = Mock()
        backend.now.return_value = NOW
        backend.storage_get.side_effect = responses
        with self.assertRaisesRegex(S.StorageError, 'IDENTITY_CHANGED'):
            S._collect_one(backend, 'seaweedfs')

    def test_storage_collection_refuses_malformed_later_claim_and_volume(self):
        w, p, _, c, v = objects('seaweedfs')
        original = [w, {'kind':'PodList','apiVersion':'v1','metadata':{},'items':p}, c, v,
                    deepcopy(w), {'kind':'PodList','apiVersion':'v1','metadata':{},'items':deepcopy(p)},
                    deepcopy(c), deepcopy(v)]
        for index, mutate in (
            (6, lambda item: item.update(spec=None)),
            (6, lambda item: item['spec'].update(resources=None)),
            (6, lambda item: item['spec']['resources'].update(requests=None)),
            (6, lambda item: item.update(status=None)),
            (6, lambda item: item['status'].update(capacity=None)),
            (7, lambda item: item.update(spec=None)),
            (7, lambda item: item['spec'].update(capacity=None)),
        ):
            responses = deepcopy(original)
            mutate(responses[index])
            backend = Mock()
            backend.now.return_value = NOW
            backend.storage_get.side_effect = responses
            with self.subTest(index=index, mutate=mutate), self.assertRaises(S.StorageError):
                S._collect_one(backend, 'seaweedfs')

    def test_storage_collection_requires_typed_second_pod_list(self):
        w, p, _, c, v = objects('seaweedfs')
        responses = [w, {'kind':'PodList','apiVersion':'v1','metadata':{},'items':p},
                     c, v, deepcopy(w),
                     {'kind':'PodList','metadata':{},'items':deepcopy(p)},
                     deepcopy(c), deepcopy(v)]
        backend = Mock()
        backend.now.return_value = NOW
        backend.storage_get.side_effect = responses
        with self.assertRaisesRegex(S.StorageError, 'IDENTITY_CHANGED'):
            S._collect_one(backend, 'seaweedfs')

    def test_storage_collection_refuses_malformed_pagination_on_either_read(self):
        w, p, _, c, v = objects('seaweedfs')
        original = [w, {'kind':'PodList','apiVersion':'v1','metadata':{},'items':p}, c, v,
                    deepcopy(w), {'kind':'PodList','apiVersion':'v1','metadata':{},'items':deepcopy(p)},
                    deepcopy(c), deepcopy(v)]
        for index, metadata in ((1, {'continue':None}), (1, {'continue':False}),
                                (1, []), (5, {'continue':0}), (5, {'continue':'next'}),
                                (5, None)):
            responses = deepcopy(original)
            responses[index]['metadata'] = metadata
            backend = Mock()
            backend.now.return_value = NOW
            backend.storage_get.side_effect = responses
            with self.subTest(index=index, metadata=metadata), self.assertRaisesRegex(
                    S.StorageError, 'TARGET_AMBIGUOUS' if index == 1 else 'IDENTITY_CHANGED'):
                S._collect_one(backend, 'seaweedfs')
        for index in (1, 5):
            responses = deepcopy(original)
            responses[index]['metadata']['continue'] = ''
            backend = Mock()
            backend.now.return_value = NOW
            backend.storage_get.side_effect = responses
            self.assertEqual(S._collect_one(backend, 'seaweedfs')['state'], 'observed')

    def test_storage_collection_refuses_changed_workload_or_pod_scope(self):
        for component in ('seaweedfs', 'trino'):
            w, pods, replica, pvc, pv = objects(component)
            first = [w, {'kind':'PodList','apiVersion':'v1','metadata':{},'items':pods}]
            if replica is not None:
                first.append(replica)
            if pvc is not None:
                first.extend((pvc, pv))
            first.extend((deepcopy(w), {'kind':'PodList','apiVersion':'v1','metadata':{},'items':deepcopy(pods)}))
            if replica is not None:
                first.append(deepcopy(replica))
            if pvc is not None:
                first.extend((deepcopy(pvc), deepcopy(pv)))
            later_workload = 4 if pvc is not None else 3
            # The fixed UIDs and projected claim chain remain unchanged in each case.
            for index, mutate in (
                (later_workload, lambda x: x['spec'].update(replicas=2)),
                (later_workload + 1, lambda x: x['items'][0]['spec'].update(nodeName='foreign')),
                (later_workload + 1, lambda x: x['items'][0]['metadata']['labels'].update(zone='foreign')),
                (later_workload + 1, lambda x: x['items'][0]['metadata']['ownerReferences'].append(
                    {'kind':'ReplicaSet','controller':False,'uid':Q})),
            ):
                changed = deepcopy(first)
                mutate(changed[index])
                backend = Mock()
                backend.now.return_value = NOW
                backend.storage_get.side_effect = changed
                reason = 'SCOPE_INVALID' if index == later_workload else 'IDENTITY_CHANGED'
                with self.subTest(component=component, index=index, mutate=mutate), self.assertRaisesRegex(
                        S.StorageError, reason):
                    S._collect_one(backend, component)

    def test_transport_reason_and_cleanup_boundary(self):
        backend=Mock();backend.now.return_value=NOW
        for exception,expected in ((R.U.PodUsageError('READ_TIMEOUT'),'READ_TIMEOUT'),
                                    (R.C.FinOpsError('SCOPE_INVALID'),'SCOPE_INVALID')):
            backend.storage_get.side_effect=exception
            self.assertTrue(all(item['reason']==expected for item in S.collect(backend)['components']))
        backend.storage_get.side_effect=R.U.PodUsageCleanupError()
        with self.assertRaises(R.U.PodUsageCleanupError):S.collect(backend)
        outer=Mock();outer.now.return_value=NOW;outer.component.side_effect=component_args
        outer.disk.return_value=(1000,260);outer.inference.return_value=[row()]
        outer.local_processes.side_effect=R.U.PodUsageCleanupError()
        value=R.collect(outer);outer.storage.assert_not_called()
        self.assertTrue(all(item['reason']=='CLEANUP_UNVERIFIED' for item in value['storage']['components']))


if __name__=='__main__':unittest.main()
