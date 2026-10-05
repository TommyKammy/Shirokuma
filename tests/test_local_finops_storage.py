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

    def test_pending_retained_claim_zero_pods_not_zero_capacity(self):
        data=list(objects('seaweedfs'));data[0]['spec']['replicas']=0;data[1]=[]
        data[3]['spec'].pop('volumeName');data[3]['status']={'phase':'Pending'};data[4]=None
        value=projected('seaweedfs',data)
        self.assertEqual(value['state'],'observed');self.assertIsNone(value['pod_uid'])
        self.assertEqual(value['pvc']['requested_bytes'],'21474836480');self.assertIsNone(value['pv'])

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
