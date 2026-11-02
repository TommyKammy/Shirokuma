"""Configured-node provenance, exact measurements, bounded reads and HTTP surfaces."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from dataclasses import replace
from http.client import HTTPResponse
import io
import json
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import test_database_platform_resources as base
import test_database_platform_usage as usage_base
import db_platform_capacity as capacity
from warehouse_operations import OperationError

NOW = datetime.now(timezone.utc).replace(microsecond=0)

def stamp(offset=0):
    return (NOW + timedelta(seconds=offset)).isoformat().replace('+00:00', 'Z')


def fixture():
    foundation, old = usage_base.fixture()
    foundation['kube'].update(node_name='enrolled-node', node_uids={'enrolled-node':'enrolled-uid'})
    for d in old[0]['items']:
        d['spec']['replicas'] = 0
        d['spec']['template']['spec']['nodeSelector'] = {'kubernetes.io/hostname':'enrolled-node'}
    node = dict(metadata=dict(name='enrolled-node', uid='enrolled-uid', creationTimestamp=stamp(-300), labels={'kubernetes.io/hostname':'enrolled-node'}),
        status=dict(nodeInfo=dict(bootID='private-boot'), capacity=dict(cpu='1500m', memory='16Gi'), allocatable=dict(cpu='1.25', memory='15Gi')))
    fs = dict(capacityBytes=2**64-1, availableBytes=2**53+1, usedBytes=9, time=stamp(-10))
    summary = dict(node=dict(nodeName='enrolled-node', startTime=stamp(-600), memory=dict(availableBytes=0,time=stamp(-10)),fs=deepcopy(fs),runtime=dict(imageFs=deepcopy(fs),containerFs=deepcopy(fs))))
    return foundation, [old[0], node, summary]


class CapacityTests(unittest.TestCase):
    patch = base.ResourcesTests.patch
    files = base.ResourcesTests.files
    handlers = base.ResourcesTests.handlers

    def setUp(self):
        global NOW
        NOW = datetime.now(timezone.utc).replace(microsecond=0)
        base.ResourcesTests.setUp(self)
        self.config, self.data = fixture()
        self.foundation.return_value = self.config
        self.processes, self.commands = [], []
        self.on_read = lambda index,obj:None
        def process(argv, **kwargs):
            self.assertEqual(kwargs, dict(stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,shell=False))
            self.assertEqual(argv[:len(self.argv)-1],self.argv[:-1])
            self.assertEqual(argv[len(self.argv)-1], '--request-timeout=2s')
            tail=argv[len(self.argv):];self.commands.append(tail)
            index=2 if '--raw' in tail else 0 if tail[1]=='deployments' else 1
            obj=deepcopy(self.data[index]);self.on_read(len(self.processes),obj)
            p=base.Process(json.dumps(obj).encode());self.processes.append(p);return p
        self.runner.side_effect=process

    def observe(self): return capacity.observe(self.principal,'admin')

    def closed(self,r,available=True):
        self.assertEqual(set(r),{'scope','warehouse','selected_role','observed_at','coverage','availability','code','core','image_fs','container_fs'})
        self.assertEqual(r['coverage'],capacity.COVERAGE)
        self.assertEqual((r['scope'],r['warehouse'],r['selected_role']),('platform','LOCAL','admin'))
        self.assertEqual(r['availability'],'available' if available else 'unavailable')
        self.assertEqual(r['code'],None if available else 'CAPACITY_UNAVAILABLE')
        if not available:
            self.assertIsNone(r['core']);self.assertIsNone(r['image_fs']);self.assertIsNone(r['container_fs'])
        else:
            self.assertEqual(set(r['core']),{'cpu_capacity_cores','cpu_allocatable_cores','memory_capacity_bytes','memory_allocatable_bytes','memory_available_bytes','memory_sampled_at','node_started_at','root_fs'})
            for fs in [r['core']['root_fs'],r['image_fs'],r['container_fs']]:
                if fs is not None:self.assertEqual(set(fs),{'capacity_bytes','available_bytes','used_bytes','sampled_at'})
        for private in ('enrolled-','private-boot','trino-local','fixed-ns','test-generation','secret'):
            self.assertNotIn(private,json.dumps(r))

    def test_chilled_exact_five_commands_pure_metadata_and_closed_dto(self):
        before=self.files();r=self.observe();self.closed(r)
        self.assertEqual(before,self.files());self.assertEqual(self.foundation.call_count,2)
        d=['get','deployments','trino-local','trino-local-worker','-o','json'];n=['get','node','enrolled-node','-o','json']
        self.assertEqual(self.commands,[d,n,['get','--raw','/api/v1/nodes/enrolled-node/proxy/stats/summary'],n,d])
        self.assertEqual(r['core']['cpu_capacity_cores'],'1.5');self.assertEqual(r['core']['memory_capacity_bytes'],'17179869184')
        self.assertEqual(r['core']['memory_available_bytes'],'0')
        self.assertTrue(all(p.finished and p.stdout.closed and not p.killed and 0<p.timeouts[0]<=15 for p in self.processes))
        for forbidden in self.forbidden:forbidden.assert_not_called()

    def test_quantity_exact_integral_bytes_zero_allocatable_and_bounds(self):
        for source,want in [('1.5Gi','1610612736'),('1e3','1000'),('18446744073709551615','18446744073709551615')]:self.assertEqual(capacity._memory(source),want)
        for v in ['0.1','18446744073709551616','NaN','-1',True]:
            with self.assertRaises(ValueError):capacity._memory(v)
        for field in ['cpu','memory']:
            self.data[1]['status']['allocatable'][field]='0'
        self.closed(self.observe())
        for section,field,value in [('capacity','cpu','0'),('capacity','memory','0'),('allocatable','cpu','2'),('allocatable','memory','17Gi')]:
            old=self.data[1]['status'][section][field];self.data[1]['status'][section][field]=value
            self.closed(self.observe(),False);self.data[1]['status'][section][field]=old

    def test_uint64_zero_missing_bool_and_independent_used(self):
        r=self.observe();fs=r['core']['root_fs']
        self.assertEqual(fs['capacity_bytes'],str(2**64-1));self.assertEqual(fs['available_bytes'],str(2**53+1));self.assertEqual(fs['used_bytes'],'9')
        source=self.data[2]['node']['fs'];source['availableBytes']=0;source.pop('usedBytes')
        self.assertEqual(self.observe()['core']['root_fs']['used_bytes'],None);self.assertEqual(self.observe()['core']['root_fs']['available_bytes'],'0')
        for key,value in [('capacityBytes',0),('availableBytes',2**64),('usedBytes',True),('availableBytes',-1),('capacityBytes',True)]:
            old=deepcopy(source);source[key]=value;self.closed(self.observe(),False);source.clear();source.update(old)
        del source['availableBytes'];self.closed(self.observe(),False)

    def test_memory_available_direct_value_cannot_exceed_node_capacity(self):
        for total in (17179869184, 2**53 + 1, 2**64 - 1):
            self.data[1]['status']['capacity']['memory'] = str(total)
            self.data[1]['status']['allocatable']['memory'] = '0'
            for available in (0, total):
                self.data[2]['node']['memory']['availableBytes'] = available
                result = self.observe()
                self.closed(result)
                self.assertEqual(result['core']['memory_available_bytes'], str(available))
            if total < 2**64 - 1:
                self.data[2]['node']['memory']['availableBytes'] = total + 1
                self.closed(self.observe(), False)

    def test_core_all_or_null_optional_independent_and_equal_kept(self):
        r=self.observe();self.assertEqual(r['image_fs'],r['container_fs']);self.assertEqual(r['core']['root_fs'],r['image_fs'])
        self.assertIsNot(r['image_fs'],r['container_fs'])
        for key in ['imageFs','containerFs']:
            for bad in [None,{},dict(capacityBytes=0),dict(self.data[2]['node']['runtime'][key],time=stamp(-121)),dict(self.data[2]['node']['runtime'][key],usedBytes=True)]:
                old=self.data[2]['node']['runtime'][key];self.data[2]['node']['runtime'][key]=bad
                r=self.observe();self.closed(r);public='image_fs' if key=='imageFs' else 'container_fs';self.assertIsNone(r[public]);self.assertIsNotNone(r['container_fs' if key=='imageFs' else 'image_fs'])
                self.data[2]['node']['runtime'][key]=old
        self.data[2]['node'].pop('runtime');r=self.observe();self.closed(r);self.assertIsNone(r['image_fs']);self.assertIsNone(r['container_fs'])
        del self.data[2]['node']['memory'];self.closed(self.observe(),False)

    def test_start_before_node_creation_and_precise_time_guards(self):
        self.closed(self.observe())
        for field in ['memory','fs']:
            source=self.data[2]['node'][field]
            for offset,valid in [(-119,True),(-121,False),(4,True),(6,False),(-601,False)]:
                source['time']=stamp(offset);self.closed(self.observe(),valid)
            source['time']=stamp(-10)
        self.data[2]['node']['startTime']=stamp(-10).replace('Z','.123456789Z')
        self.data[2]['node']['memory']['time']=stamp(-10).replace('Z','.123456788Z');self.closed(self.observe(),False)
        for field in ['memory','fs']:self.data[2]['node'][field]['time']=self.data[2]['node']['startTime']
        self.closed(self.observe())

    def test_pin_foundation_namespace_hostname_identity_and_placement(self):
        changes=[lambda f,d:f['kube']['node_uids'].update(extra='uid'),lambda f,d:f.update(warehouse_id='OTHER'),
            lambda f,d:d[1]['metadata'].update(name='other'),lambda f,d:d[1]['metadata'].update(uid='other'),
            lambda f,d:d[1]['metadata'].update(deletionTimestamp=stamp()),lambda f,d:d[1]['metadata']['labels'].clear(),
            lambda f,d:d[1]['status']['nodeInfo'].update(bootID=''),lambda f,d:d[2]['node'].update(nodeName='other'),
            lambda f,d:d[2].pop('node'),lambda f,d:d[0]['items'].pop(),lambda f,d:d[0]['items'].append(deepcopy(d[0]['items'][0])),
            lambda f,d:d[0]['items'][0]['metadata'].update(namespace='other'),lambda f,d:d[0]['items'][0]['metadata']['labels'].clear(),
            lambda f,d:d[0]['items'][0]['spec']['template']['metadata']['labels'].clear(),
            lambda f,d:d[0]['items'][0]['spec']['template']['spec'].update(nodeName='other'),
            lambda f,d:d[0]['items'][0]['spec']['template']['spec']['nodeSelector'].clear()]
        for change in changes:
            self.config,self.data=fixture();self.foundation.return_value=self.config;change(self.config,self.data)
            with self.subTest(change=change):self.closed(self.observe(),False)

    def test_selected_drift_and_unrelated_noise(self):
        for index,change in [(3,lambda o:o['metadata'].update(uid='other')),(3,lambda o:o['status']['nodeInfo'].update(bootID='other')),(3,lambda o:o['status']['capacity'].update(cpu='4')),(4,lambda o:o['items'][0]['metadata'].update(uid='new')),(4,lambda o:o['items'][0]['spec']['template']['spec'].update(nodeName='other'))]:
            self.processes=[]
            self.on_read=lambda i,o:change(o) if i==index else None
            self.closed(self.observe(),False)
        def noise(i,o):
            if i==3:o['metadata'].update(resourceVersion='new',managedFields=[{}]);o['status']['conditions']=[{'heartbeat':'now'}]
            if i==4:o['items'][0]['metadata']['resourceVersion']='new';o['items'][0]['metadata']['labels']['unrelated']='noise'
        self.processes=[];self.on_read=noise;self.closed(self.observe())
        changed=deepcopy(self.config);changed['revision']+=1;self.foundation.side_effect=[self.config,changed];self.closed(self.observe(),False)

    def test_current_platform_authority_without_db_grants_denies_forged(self):
        self.closed(self.observe())
        for principal in [replace(self.principal,platform_admin=False),replace(self.principal,maximum_role='data_engineer'),replace(self.principal,principal_id='forged'),base.auth.AuthenticatedPrincipal('dba','admin',False)]:
            self.runner.reset_mock();self.foundation.reset_mock()
            with self.assertRaises(OperationError):capacity.observe(principal,'admin')
            self.runner.assert_not_called();self.foundation.assert_not_called()
        for role in ['data_engineer','bi_viewer',None]:
            with self.assertRaises(OperationError):capacity.observe(self.principal,role)

    def test_revocation_each_read_and_final_fingerprint(self):
        original=capacity._authority
        for at in range(2,10):
            count=0;self.users['p']['platform_admin']=True;self.processes=[]
            def gate(*args):
                nonlocal count
                count+=1
                if count==at:self.users['p']['platform_admin']=False
                return original(*args)
            with patch.object(capacity,'_authority',side_effect=gate),self.assertRaises(OperationError):self.observe()
        self.users['p']['platform_admin']=True;self.processes=[]
        self.on_read=lambda i,o:self.users['p'].update(password_hash='changed') if i==4 else None
        with self.assertRaises(OperationError):self.observe()

    def test_bounded_reader_malformed_duplicate_cleanup_and_deadline(self):
        for raw in [b'{}',b'{"items":[],"items":[]}',b'{"bad":NaN}',b'x'*(1048576+1)]:
            p=base.Process(raw)
            with patch.object(capacity._read.__globals__['subprocess'],'Popen',return_value=p):self.closed(self.observe(),False)
            self.assertTrue(p.stdout.closed)
        p=base.Process()
        with patch.object(capacity._read.__globals__['subprocess'],'Popen',return_value=p),patch.object(base.Selector,'select',return_value=[]):self.closed(self.observe(),False)
        self.assertTrue(p.killed);self.assertTrue(p.finished);self.assertTrue(p.stdout.closed)
        with patch.object(capacity,'_read',side_effect=ValueError('private')):self.closed(self.observe(),False)
        with patch.object(capacity,'_read',wraps=capacity._read) as read:
            self.closed(self.observe());self.assertEqual(len({c.args[1] for c in read.call_args_list}),1)

    def request(self,handler,*,method='GET',suffix='',headers='',body='',role='admin',principal=None):
        class Connection:
            def __init__(self,raw):self.incoming,self.outgoing=io.BytesIO(raw),io.BytesIO()
            def makefile(self,*args):return self.incoming
            def sendall(self,raw):self.outgoing.write(raw)
        raw=(method+' /api/v1/bears/platform-capacity'+suffix+' HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\nX-Shirokuma-Role: '+role+'\r\n'+headers+'\r\n'+body).encode()
        conn=Connection(raw)
        with patch.object(base.runtime_profile,'active',return_value=True),patch.object(base.auth,'authenticate_principal',return_value=principal or self.principal),patch.object(handler,'log_message'):
            handler(conn,('127.0.0.1',1),SimpleNamespace())
        response=HTTPResponse(Connection(conn.outgoing.getvalue()));response.begin();return response.status,json.loads(response.read())

    def test_both_actual_http_surfaces_zero_input_and_auth(self):
        cases=[({},200),({'headers':'Content-Length: 0\r\n'},200),({'suffix':'?'},400),({'suffix':'#'},400),({'suffix':'?x=y'},400),({'headers':'Content-Length: 1\r\n','body':'x'},400),({'headers':'Content-Length: nope\r\n'},400),({'headers':'Content-Length: -1\r\n'},400),({'headers':'Content-Length: 0\r\nContent-Length: 0\r\n'},400),({'headers':'Host: other\r\n'},400),({'headers':'Broken header\r\n'},400),({'headers':'X-Bad: a\r\n folded\r\n'},400),({'headers':'X-Bad: \x00\r\n'},400),({'headers':'Transfer-Encoding: chunked\r\n'},400),({'method':'POST'},404),({'role':'bi_viewer'},403),({'principal':base.auth.AuthenticatedPrincipal('dba','admin',False)},403)]
        for handler in self.handlers():
            for options,status in cases:
                self.processes=[];self.runner.reset_mock()
                actual,data=self.request(handler,**options);self.assertEqual(actual,status,(options,data))
                if status==200:self.closed(data)
                else:self.runner.assert_not_called();self.assertNotIn('core',data)
            with patch.object(capacity,'_authority',side_effect=[('first',),('second',)]):
                status,data=self.request(handler);self.assertEqual(status,403);self.assertNotIn('core',data)

if __name__=='__main__':unittest.main()
