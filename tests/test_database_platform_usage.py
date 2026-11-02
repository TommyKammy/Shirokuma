"""Usage contract: exact quantities, provenance fences and both HTTP adapters."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import io
import json
from http.client import HTTPResponse
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_database_platform_resources as base
from test_database_platform_resources import auth, audit, wr, runtime_profile, OperationError
import db_platform_usage as usage

NOW = datetime.now(timezone.utc).replace(microsecond=0)
def stamp(delta=0):
    return (NOW + timedelta(seconds=delta)).isoformat().replace('+00:00', 'Z')


def fixture():
    foundation = dict(warehouse_id='LOCAL', foundation_id='test-generation', revision=7,
                      kube=dict(namespace='fixed-ns'))
    deployments, replicas, pods, metrics = [], [], [], []
    for index, (dn, role) in enumerate(usage.DEPLOYMENTS):
        labels = {usage.lf.LABEL: foundation['foundation_id'], usage.lf.REVISION: '7',
                  usage.COMPUTE: 'true', 'app': role}
        def meta(name, uid):
            return dict(name=name, uid=uid, namespace='fixed-ns', creationTimestamp=stamp(-300), labels=dict(labels))
        template = dict(metadata=dict(labels=dict(labels)), spec=dict(containers=[dict(name='engine'), dict(name='sidecar')]))
        selector = dict(matchLabels=dict(app=role))
        deployment = dict(metadata=meta(dn, 'deployment-'+role), spec=dict(replicas=1, template=deepcopy(template), selector=selector), status=dict(readyReplicas=1, updatedReplicas=1, replicas=1, observedGeneration=3))
        deployment['metadata']['generation'] = 3
        rs = dict(metadata=meta('rs-'+role, 'rs-uid-'+role), spec=dict(template=deepcopy(template), selector=selector))
        rs['metadata']['ownerReferences'] = [dict(kind='Deployment', name=dn, uid='deployment-'+role, controller=True)]
        pod = dict(metadata=meta('pod-'+role, 'pod-uid-'+role), spec=deepcopy(template['spec']), status=dict(phase='Running', containerStatuses=[dict(name=n, ready=True, restartCount=0, containerID='runtime://'+n, state=dict(running=dict(startedAt=stamp(-250)))) for n in ('engine', 'sidecar')]))
        pod['metadata']['ownerReferences'] = [dict(kind='ReplicaSet', name='rs-'+role, uid='rs-uid-'+role, controller=True)]
        metric = dict(metadata=dict(name='pod-'+role, namespace='fixed-ns'), timestamp=stamp(-10), window='30.001s', containers=[dict(name=n, usage=dict(cpu='123456789n', memory='1.5Gi')) for n in ('engine','sidecar')])
        deployments.append(deployment); replicas.append(rs); pods.append(pod); metrics.append(metric)
    return foundation, [dict(items=items) for items in (deployments, replicas, pods, metrics)]


class UsageTests(unittest.TestCase):
    patch = base.ResourcesTests.patch
    files = base.ResourcesTests.files
    handlers = base.ResourcesTests.handlers

    def setUp(self):
        global NOW
        NOW = datetime.now(timezone.utc).replace(microsecond=0)
        base.ResourcesTests.setUp(self)
        self.config, self.data = fixture()
        self.foundation.return_value = self.config
        self.processes = []
        def process(argv, **kwargs):
            self.assertEqual(kwargs, dict(stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False))
            self.assertEqual(argv[:len(self.argv)-1], self.argv[:-1])
            self.assertIn('--request-timeout=2s', argv)
            tail = argv[len(self.argv):]
            index = 3 if '--raw' in tail else {'deployments':0, 'replicasets':1, 'pods':2}[tail[1]]
            obj = deepcopy(self.data[index])
            self.on_read(len(self.processes), obj)
            p = base.Process(json.dumps(obj).encode())
            self.processes.append(p)
            return p
        self.on_read = lambda index, obj: None
        self.runner.side_effect = process

    def observe(self):
        return usage.observe(self.principal, 'admin')

    def closed(self, result, available=True):
        self.assertEqual(set(result), {'scope','warehouse','selected_role','observed_at','coverage','availability','code','samples'})
        self.assertEqual(result['coverage'], usage.COVERAGE)
        self.assertEqual((result['scope'],result['warehouse'],result['selected_role']), ('platform','LOCAL','admin'))
        self.assertEqual(result['availability'], 'available' if available else 'unavailable')
        self.assertEqual(result['code'], None if available else 'USAGE_UNAVAILABLE')
        if not available:
            self.assertIsNone(result['samples'])
        else:
            self.assertEqual(len(result['samples']), 4)
            for row in result['samples']:
                self.assertEqual(set(row), {'role','container_ordinal','sampled_at','window_seconds','cpu_cores','cpu_millicores','memory_bytes'})
                self.assertEqual(Decimal(row['cpu_cores'])*1000, Decimal(row['cpu_millicores']))
                self.assertEqual(row['memory_bytes'],'1610612736')
        raw = json.dumps(result)
        for secret in ('fixed-ns','pod-','rs-','test-generation','trino-local','secret','engine','sidecar'):
            self.assertNotIn(secret, raw)

    def current(self, data=None):
        return usage._source(*(data or self.data)[:3], self.config)[0]

    def test_exact_quantity_conversions(self):
        for value, expected in [('1n','0.000000001'),('7u','0.000007'),('125m','0.125'),('1Ki','1024'),('1.5Mi','1572864'),('2Gi','2147483648'),('1e3','1000'),('1E-3','0.001'),('+0','0'),('.5','0.5'),('1k','1000'),('1Ei','1152921504606846976'),('0e30','0'),('1e-30','0.000000000000000000000000000001')]:
            with self.subTest(value=value):
                self.assertEqual(usage._normal(usage._decimal(value)), expected)
        for value in ['NaN','Infinity','-0','-1','1K','1mi','1e9999','0e9999','1e31','1e-31','9'*65,'9'*64,'1_000',' 1','1 ','1μ',None,0,True]:
            with self.subTest(value=value), self.assertRaises(ValueError): usage._decimal(value)

    def test_high_precision_scientific_and_binary_quantities(self):
        scientific = '1.23456789012345678901234567890123456789e-20'
        self.assertEqual(usage._normal(usage._decimal(scientific)),
            '0.0000000000000000000123456789012345678901234567890123456789')
        binary = '0.123456789012345678901234567890123456789Ki'
        self.assertEqual(usage._normal(usage._decimal(binary)),
            '126.419751948641975194864197519486419751936')

    def test_current_container_full_window_and_restart_rejections(self):
        # Same Pod UID and creation, but each ordinary container has its own start.
        pod = self.data[2]['items'][0]
        for status in pod['status']['containerStatuses']:
            status['state']['running']['startedAt'] = stamp(-50)
            status['restartCount'] = 1
        self.closed(self.observe())  # sample -10, window 30.001: wholly current
        for offset in (-5, -20):  # previous instance sample, then straddling interval
            for index in (0, 1):
                with self.subTest(start=offset, container=index):
                    statuses = pod['status']['containerStatuses']
                    statuses[index]['state']['running']['startedAt'] = stamp(offset)
                    self.closed(self.observe(), False)
                    statuses[index]['state']['running']['startedAt'] = stamp(-50)
        for start in (None, '', 'bad', '2026-02-30T00:00:00Z'):
            pod['status']['containerStatuses'][0]['state']['running']['startedAt'] = start
            self.closed(self.observe(), False)
        del pod['status']['containerStatuses'][0]['state']['running']['startedAt']
        self.closed(self.observe(), False)
        # Nanosecond-precise start boundary: equality is current, 1ns earlier is not.
        self.config, self.data = fixture(); self.foundation.return_value = self.config
        metric = self.data[3]['items'][0]
        metric['timestamp'] = stamp(-10).replace('Z', '.123456789Z')
        metric['window'] = '1ns'
        statuses = self.data[2]['items'][0]['status']['containerStatuses']
        statuses[0]['state']['running']['startedAt'] = stamp(-10).replace('Z', '.123456788Z')
        self.closed(self.observe())
        statuses[0]['state']['running']['startedAt'] = metric['timestamp']
        self.closed(self.observe(), False)

    def test_container_incarnation_changes_during_reads_clear_all_samples(self):
        for field in ('start', 'restart', 'identity'):
            self.processes = []
            def change(index, obj):
                if index != 6:
                    return
                status = obj['items'][0]['status']['containerStatuses'][0]
                if field == 'start':
                    status['state']['running']['startedAt'] = stamp(-100)
                elif field == 'restart':
                    status['restartCount'] += 1
                else:
                    status['containerID'] = 'runtime://replacement'
            self.on_read = change
            with self.subTest(field=field):
                self.closed(self.observe(), False)
            self.assertEqual(len(self.processes), 7)

    def test_generation_and_replica_numeric_types_are_strict(self):
        deployment = self.data[0]['items'][0]
        for value in (True, '3', 0, -1):
            for section, key in (('metadata', 'generation'), ('status', 'observedGeneration')):
                old = deployment[section][key]
                deployment[section][key] = value
                self.closed(self.observe(), False)
                deployment[section][key] = old
        for key in ('readyReplicas', 'replicas', 'updatedReplicas'):
            deployment['status'][key] = True
            self.closed(self.observe(), False)
            deployment['status'][key] = 1
        deployment['spec']['replicas'] = True
        self.closed(self.observe(), False)

    def test_composite_duration_units_precision_and_bounds(self):
        for text, expected in [('1m0s', '60'), ('1m0.25s', '60.25'),
                ('250ms', '0.25'), ('1ns', '0.000000001'), ('125us', '0.000125'),
                ('125µs', '0.000125'), ('0.5m', '30'), ('2m0s', '120')]:
            with self.subTest(window=text):
                self.assertEqual(usage._window(text), expected)
        for text in ('0s', '0m0s', '-1s', '2m0.1s', '1h', '0.1ns',
                '1e1s', 'NaNs', '1m-1s', '1s '*30, '1'*65+'ns'):
            with self.subTest(window=text), self.assertRaises(ValueError):
                usage._window(text)

    def test_readonly_true_p_no_db_grants_exact_commands_cleanup_closed_dto(self):
        before = self.files()
        self.closed(self.observe())
        self.assertEqual(before,self.files())
        self.assertEqual(len(self.processes),7)
        self.assertEqual(self.foundation.call_count,2)
        self.assertTrue(all(p.stdout.closed and not p.killed for p in self.processes))
        for call in self.runner.call_args_list:
            tail=call.args[0][len(self.argv):]
            self.assertIn(tail, [['get','deployments','trino-local','trino-local-worker','-o','json'], ['get','replicasets','-o','json'], ['get','pods','-o','json'], ['get','--raw','/apis/metrics.k8s.io/v1beta1/namespaces/fixed-ns/pods']])
        for forbidden in self.forbidden: forbidden.assert_not_called()

    def test_denies_all_non_p_and_forged_stale_before_source(self):
        from dataclasses import replace
        principals=[auth.AuthenticatedPrincipal('dba','admin',False),replace(self.principal, platform_admin=False),replace(self.principal,maximum_role='data_engineer'),replace(self.principal,principal_id='forged')]
        for principal in principals:
            with self.assertRaises(OperationError): usage.observe(principal,'admin')
        for role in ('data_engineer','bi_viewer',None):
            with self.assertRaises(OperationError): usage.observe(self.principal,role)
        for change in ({'enabled':False},{'platform_admin':False},{'role':'bi_viewer'}):
            old=self.users['p'].copy();self.users['p'].update(change)
            with self.assertRaises(OperationError): self.observe()
            self.users['p']=old
        self.runner.assert_not_called();self.foundation.assert_not_called()

    def test_authority_loss_before_each_read_and_response_discards(self):
        original=usage._authority
        # initial + foundation + seven reads + final foundation + public fence
        for at in range(2,12):
            count=0
            def gate(*args):
                nonlocal count
                count+=1
                if count==at: self.users['p']['platform_admin']=False
                return original(*args)
            self.users['p']['platform_admin']=True;self.processes=[]
            with patch.object(usage,'_authority',side_effect=gate), self.assertRaises(OperationError): self.observe()
            self.assertLessEqual(len(self.processes),max(0,at-3))
        self.users['p']['platform_admin']=True
        for change in ({'role':'data_engineer'},{'enabled':False},{'password_hash':'fingerprint'}):
            old=self.users['p'].copy()
            def revoke(index,obj): self.users['p'].update(change)
            self.on_read=revoke
            with self.assertRaises(OperationError): self.observe()
            self.users['p']=old

    def test_freshness_future_window_creation_and_zero(self):
        current=deepcopy(self.current())
        for age, valid in [(-120,True),(-121,False),(5,True),(6,False)]:
            data=deepcopy(self.data[3]);data['items'][0]['timestamp']=stamp(age)
            if valid: usage._samples(data,current,self.config,NOW)
            else:
                with self.assertRaises(ValueError): usage._samples(data,current,self.config,NOW)
        for value,expected in [('1m30s','90'),('500ms','0.5'),('1000us','0.001'),('1ns','0.000000001'),('120s','120')]:
            self.assertEqual(usage._window(value),expected)
        self.assertEqual(usage._stamp(stamp(-10).replace('Z','.123456789Z')) % 1,Decimal('0.123456789'))
        for window in ('0s','-1s','121s','1e3s','NaNs',''):
            data=deepcopy(self.data[3]);data['items'][0]['window']=window
            with self.assertRaises(ValueError): usage._samples(data,current,self.config,NOW)
        current['trino-local']['metadata']['creationTimestamp']=stamp(-5)
        with self.assertRaises(ValueError): usage._samples(self.data[3],current,self.config,NOW)
        current=self.current()
        for item in self.data[3]['items']:
            for c in item['containers']: c['usage']['cpu']='0';c['usage']['memory']='0'
        result=usage._samples(self.data[3],current,self.config,NOW)
        self.assertTrue(all(r['cpu_cores']==r['cpu_millicores']==r['memory_bytes']=='0' for r in result))

    def test_owner_uid_namespace_selector_label_container_and_duplicate_rejections(self):
        changes=[lambda d:d[0]['items'].pop(),lambda d:d[0]['items'].append(deepcopy(d[0]['items'][0])),
          lambda d:d[0]['items'][0]['spec'].update(replicas=0),
          lambda d:d[0]['items'][0]['status'].update(updatedReplicas=0),
          lambda d:d[0]['items'][0]['status'].update(readyReplicas=True),
          lambda d:d[1]['items'][0]['spec']['template']['spec']['containers'][0].update(image='old'),
          lambda d:d[1]['items'][0]['metadata']['ownerReferences'][0].update(uid='foreign'),
          lambda d:d[2]['items'][0]['metadata']['ownerReferences'][0].update(uid='foreign'),
          lambda d:d[2]['items'][0]['metadata']['ownerReferences'][0].update(name='foreign'),
          lambda d:d[2]['items'][0]['metadata'].update(namespace='foreign'),
          lambda d:d[2]['items'][0]['metadata']['labels'].update(app='foreign'),
          lambda d:d[2]['items'][0]['metadata']['labels'].update({usage.lf.REVISION:'6'}),
          lambda d:d[2]['items'][0]['status'].update(phase='Pending'),
          lambda d:d[2]['items'][0]['spec']['containers'].append(dict(name='hidden')),
          lambda d:d[2]['items'].append(deepcopy(d[2]['items'][0])),
          lambda d:d[2]['items'][0]['status']['containerStatuses'][0].update(ready=False),
          lambda d:d[3]['items'][0]['containers'].pop(),
          lambda d:d[3]['items'][0]['containers'][0].update(name='foreign'),
          lambda d:d[3]['items'][0]['metadata'].update(uid='old-pod'),
          lambda d:d[3]['items'][0]['metadata'].update(namespace='foreign'),
          lambda d:d[3]['items'].append(deepcopy(d[3]['items'][0])),
          lambda d:d[3]['items'][0]['containers'][0]['usage'].update(cpu='-1')]
        for change in changes:
            self.config,self.data=fixture();self.foundation.return_value=self.config;change(self.data);self.processes=[]
            with self.subTest(change=change): self.closed(self.observe(),False)

    def test_rollout_aba_source_drift_and_foundation_drift(self):
        for index in (4,5,6):
            def changed(i,obj):
                if i==index: obj['items'][0]['metadata']['uid']='replacement'
            self.on_read=changed;self.processes=[]
            self.closed(self.observe(),False)
        self.on_read=lambda i,obj:None
        config=deepcopy(self.config);config['revision']+=1
        self.foundation.side_effect=[self.config,config]
        self.closed(self.observe(),False)

    def test_read_transport_bounds_duplicate_json_and_cleanup(self):
        for raw in (b'{}',b'{"items":[],"items":[]}',b'{"x":NaN}',b'x'*(usage.MAX_OUTPUT+1)):
            p=base.Process(raw)
            with patch.object(usage.subprocess,'Popen',return_value=p):
                self.closed(self.observe(),False)
            self.assertTrue(p.stdout.closed)
        p=base.Process()
        with patch.object(usage.subprocess,'Popen',return_value=p),patch.object(base.Selector,'select',return_value=[]):
            self.closed(self.observe(),False)
        self.assertTrue(p.killed);self.assertTrue(p.stdout.closed)
        with patch.object(usage.subprocess,'Popen',side_effect=OSError('secret')):
            self.closed(self.observe(),False)

    def request(self, handler, *, method='GET',suffix='',headers='',body='',role='admin',principal=None):
        class Connection:
            def __init__(self,raw): self.incoming,self.outgoing=io.BytesIO(raw),io.BytesIO()
            def makefile(self,*args): return self.incoming
            def sendall(self,raw): self.outgoing.write(raw)
        raw=(method+' /api/v1/bears/platform-usage'+suffix+' HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\nX-Shirokuma-Role: '+role+'\r\n'+headers+'\r\n'+body).encode()
        conn=Connection(raw)
        with patch.object(runtime_profile,'active',return_value=True),patch.object(auth,'authenticate_principal',return_value=principal or self.principal),patch.object(handler,'log_message'):
            handler(conn,('127.0.0.1',1),SimpleNamespace())
        response=HTTPResponse(Connection(conn.outgoing.getvalue()));response.begin()
        return response.status,json.loads(response.read())

    def test_both_actual_http_surfaces_strict_framing_and_current_auth(self):
        cases=[({},200),({'headers':'Content-Length: 0\r\n'},200),({'suffix':'?'},400),({'suffix':'?unknown=x'},400),({'suffix':'?context={}'},400),({'suffix':'#x'},400),({'headers':'Content-Length: 1\r\n','body':'x'},400),({'headers':'Content-Length: nope\r\n'},400),({'headers':'Content-Length: -1\r\n'},400),({'headers':'Content-Length: 0\r\nContent-Length: 0\r\n'},400),({'headers':'Transfer-Encoding: chunked\r\n'},400),({'method':'POST'},404),({'role':'bi_viewer'},403),({'principal':auth.AuthenticatedPrincipal('dba','admin',False)},403)]
        for handler in self.handlers():
            for options,expected in cases:
                self.processes=[];self.runner.reset_mock()
                with self.subTest(surface=handler.__name__,options=options):
                    status,data=self.request(handler,**options);self.assertEqual(status,expected,data)
                    if status==200:self.closed(data)
                    else:self.runner.assert_not_called();self.assertNotIn('samples',data)
            with patch.object(usage,'_read',side_effect=ValueError('secret')):
                status,data=self.request(handler);self.assertEqual(status,200);self.closed(data,False)
            with patch.object(usage,'_authority',side_effect=[('first',),('changed',),('changed',)]):
                status,data=self.request(handler);self.assertEqual(status,403);self.assertNotIn('samples',data)

if __name__=='__main__': unittest.main()
