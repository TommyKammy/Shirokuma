"""Mock-only Pod projection, atomic declaration review and captured HTTP adapters."""
from copy import deepcopy
from dataclasses import replace
from http.client import HTTPResponse
from types import SimpleNamespace
import io
import json
import subprocess
import unittest
from unittest.mock import patch

import test_database_platform_foundation_baseline as foundation
import test_database_platform_recent_failures as recent_tests
import db_platform_pod_baseline as resources
import db_platform_workload_baseline as old
from warehouse_operations import OperationError

base = foundation.base
audit, auth, wr = base.audit, base.auth, base.wr
Process, Selector = base.Process, base.Selector


def header(name='batch-42.node', non_root='', uid='', namespace='fixed-ns', **changes):
    fields = dict(api='v1', kind='Pod', namespace=namespace, name=name,
                  identity='uid-'+name, nr=non_root, user=uid)
    fields.update(changes)
    return ('P\t'+'\t'.join(fields.values())+'\n').encode()


def row(kind='C', name=None, **options):
    return base.row(kind, **options).replace((kind+'\t').encode(),
        (kind+'\t'+(name or {'C':'main','I':'setup','E':'debug'}[kind])+'\t').encode(), 1)


def frame(*pods):
    return b'L\tv1\tList\t\t\n'+b''.join(p+b'Q\n' for p in pods)+b'Z\n'


ROWS = frame(header()+row()+row('I')+row('E'))


class PodBaselineTests(unittest.TestCase):
    patch = base.BaselineTests.patch
    files = base.BaselineTests.files
    handlers = base.BaselineTests.handlers

    def setUp(self):
        foundation.FoundationBaselineTests.setUp(self)
        self.process = Process(ROWS)

    def observe(self, principal=None, role='admin'):
        return resources.observe(principal or self.principal, role)

    def closed(self, result, available=True):
        self.assertEqual(set(result), {'scope','warehouse','selected_role','observed_at','baseline_version','coverage','baseline'})
        self.assertEqual((result['scope'],result['warehouse'],result['selected_role']),('platform','LOCAL','admin'))
        self.assertEqual(result['baseline_version'],'namespace-pod-container-declarations-v1')
        self.assertEqual(result['coverage'],dict(kind='namespace_pod_container_declarations',pod_spec=True,
            per_container=True,runtime_security=False,admission_decision=False,global_policy=False))
        b=result['baseline']
        self.assertEqual(set(b),{'availability','code','observed_at','namespace','pod_count','container_count','pods'})
        self.assertEqual(b['availability'],'available' if available else 'unavailable')
        self.assertEqual(b['code'],None if available else 'POD_BASELINE_UNAVAILABLE')
        if available:
            self.assertEqual(b['namespace'],'fixed-ns')
            self.assertEqual(b['pod_count'],len(b['pods']))
            self.assertEqual(b['container_count'],sum(len(p['containers']) for p in b['pods']))
            for pod in b['pods']:
                self.assertEqual(set(pod),{'name','containers'})
                for c in pod['containers']:
                    self.assertEqual(set(c),{'name','kind','ordinal','checks'})
                    self.assertEqual(set(c['checks']),{'read_only_root_filesystem','non_root','drop_all_capabilities','cpu_memory_limits'})
                    self.assertTrue(all(v in ('met','not_met') for v in c['checks'].values()))
        else:
            for key in ('namespace','pod_count','container_count','pods'):self.assertIsNone(b[key])
        raw=json.dumps(result,default=str).encode()
        self.assertLessEqual(len(raw),65536)
        for secret in (b'uid-',b'context-secret',b'config-secret',b'error-secret',b'runAsUser',b'securityContext',b'"allow"',b'1Gi'):
            self.assertNotIn(secret,raw)

    def checks(self, container=None, pod_non_root='', pod_uid=''):
        self.process=Process(frame(header(non_root=pod_non_root,uid=pod_uid)+(row() if container is None else container)))
        result=self.observe();self.closed(result)
        return result['baseline']['pods'][0]['containers'][0]['checks']

    def test_exact_namespace_only_single_projected_read_without_filters(self):
        before=self.files();self.closed(self.observe())
        expected=self.argv[:-1]+['--request-timeout=3s','get','pods','--chunk-size=0','-o',resources.JSONPATH]
        self.runner.assert_called_once_with(expected,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,shell=False)
        for field in ('apiVersion','kind','metadata.namespace','metadata.name','metadata.uid','metadata.continue','metadata.remainingItemCount', 'ephemeralContainers','initContainers','readOnlyRootFilesystem','runAsNonRoot','runAsUser','capabilities.drop','limits.cpu','limits.memory'):
            self.assertIn(field,resources.JSONPATH)
        for forbidden in ('status','phase','ready','deletionTimestamp','labels','ownerReferences','template','env','args','logs','secrets','image','volumes','requests'):
            self.assertNotIn(forbidden,resources.JSONPATH)
        self.assertEqual(self.foundation.call_count,2)
        self.assertTrue(self.process.stdout.closed);self.assertFalse(self.process.killed)
        self.assertTrue(0<self.process.timeouts[0]<=8)
        self.assertEqual(before,self.files())
        for mock in self.forbidden:mock.assert_not_called()

    def test_generic_names_multiple_unlabelled_all_phases_and_terminating_projection(self):
        # Projecting the same actual spec omits lifecycle/ownership entirely.
        specs=[dict(apiVersion='v1',kind='Pod',metadata=dict(name='pod-'+str(i),namespace='fixed-ns',uid='uid-'+str(i),
            labels={} if i%2 else {'unrelated':'true'},deletionTimestamp='2026-11-03T00:00:00Z' if i==4 else None),
            status=dict(phase=phase)) for i,phase in enumerate(['Pending','Running','Succeeded','Failed','Unknown'])]
        raw=frame(*(header(name=p['metadata']['name'],identity=p['metadata']['uid'])+row()+row('I')+row('E') for p in specs))
        self.process=Process(raw);r=self.observe();self.closed(r)
        self.assertEqual([p['name'] for p in r['baseline']['pods']],[p['metadata']['name'] for p in specs])
        self.assertEqual(r['baseline']['container_count'],15)
        for pod in r['baseline']['pods']:
            self.assertEqual([(c['name'],c['kind'],c['ordinal']) for c in pod['containers']],
                [('main','container',1),('setup','init_container',1),('debug','ephemeral_container',1)])
        self.assertTrue(resources._name('x'*200,subdomain=True))
        longest_name = 'x' * 253
        parsed = resources._parse(frame(header(longest_name, identity='uid-longest-name') + row()), 'fixed-ns')
        self.assertEqual(parsed[0]['name'], longest_name)
        for names in [('z-last','a-first'),('a-first','z-last')]:
            self.assertEqual([p['name'] for p in resources._parse(frame(*(header(n)+row() for n in names)),'fixed-ns')],list(names))

    def test_empty_complete_list_is_available_zero_without_aggregate_verdict(self):
        self.process=Process(frame());r=self.observe();self.closed(r)
        self.assertEqual((r['baseline']['pods'],r['baseline']['pod_count'],r['baseline']['container_count']),([],0,0))
        self.assertNotIn('allow',json.dumps(r))

    def test_malformed_list_frames_continuations_identity_and_names_atomic(self):
        malformed=[b'',ROWS[:-1],ROWS[:-2],ROWS+b'Z\n',ROWS.replace(b'L\tv1',b'L\tv2'),
            ROWS.replace(b'List\t\t',b'List\tnext-page\t'), ROWS.replace(b'List\t\t',b'List\t\t1'),
            ROWS.replace(b'List',b'Pod'),ROWS.replace(b'Q\n',b''),frame(header()),frame(row()),
            frame(header()+row()+header('other')+row()),frame(header()+row(),header()+row()),
            frame(header()+row(),header('other',identity='uid-batch-42.node')+row()),
            frame(header()+row()+row(name='main')),frame(header()+row('I')+row())]
        for changes in [dict(api='apps/v1'),dict(kind='Deployment'),dict(namespace='other'),dict(identity=''),
            dict(identity='x'*129),dict(identity='bad uid'),dict(name='Upper'),dict(name='bad..name'),dict(name='x'*254)]:
            malformed.append(frame(header(**changes)+row()))
        for raw in malformed:
            with self.subTest(raw=raw[:80]):
                self.process=Process(raw);self.closed(self.observe(),False)

    def test_pod_container_and_raw_byte_caps_no_partial_results(self):
        for count in (64,65):
            self.process=Process(frame(*(header('p-'+str(i))+row() for i in range(count))))
            self.closed(self.observe(),count==64)
        raw=frame(header()+b''.join(row(name='c-'+str(i)) for i in range(256)))
        self.assertEqual(len(resources._parse(raw,'fixed-ns')[0]['containers']),256)
        with self.assertRaises(ValueError):resources._parse(raw.replace(b'Q\n',row(name='overflow')+b'Q\n'),'fixed-ns')
        raw=frame(header()+row(caps='["ALL"]'))
        padding=65536-len(raw)
        exact=raw.replace(b'["ALL"]',b'["'+b'x'*padding+b'ALL"]')
        self.assertEqual(len(exact),65536);self.assertEqual(len(resources._parse(exact,'fixed-ns')),1)
        with self.assertRaises(ValueError):resources._parse(exact+b'\n','fixed-ns')
        self.process=Process(b'x'*65537);self.closed(self.observe(),False)
        self.assertTrue(self.process.killed);self.assertTrue(self.process.stdout.closed)

    def test_all_three_kinds_inherit_override_and_count_ordinals(self):
        for kind in ('C','I','E'):
            # A required ordinary container precedes optional kinds.
            for nr,user,pnr,puser,expected in [('', '', 'true', '', 'met'),('false','','true','1000','not_met'),
                ('','0','true','1000','not_met'),('','2000','true','0','met'),('true','','false','1000','met')]:
                rows=(b'' if kind=='C' else row())+row(kind,name='one',non_root=nr,uid=user)+row(kind,name='two',non_root=nr,uid=user)
                r=resources._parse(frame(header(non_root=pnr,uid=puser)+rows),'fixed-ns')[0]['containers']
                self.assertEqual([c['ordinal'] for c in r[-2:]],[1,2])
                self.assertEqual([c['checks']['non_root'] for c in r[-2:]],[expected]*2)

    def test_deadline_spawn_exit_cleanup_and_no_raw_errors(self):
        for mode in ('timeout','exit','spawn','clock'):
            self.process=Process(ROWS,returncode=1 if mode=='exit' else 0)
            with self.subTest(mode=mode):
                if mode=='timeout':
                    with patch.object(Selector,'select',return_value=[]):self.closed(self.observe(),False)
                elif mode=='spawn':
                    with patch.object(resources.subprocess,'Popen',side_effect=OSError('error-secret')):self.closed(self.observe(),False)
                elif mode=='clock':
                    with patch.object(resources.time,'monotonic',side_effect=[0,9]):self.closed(self.observe(),False)
                else:self.closed(self.observe(),False)
                if mode!='spawn':self.assertTrue(self.process.stdout.closed)
                if mode in ('timeout','clock'):self.assertTrue(self.process.killed)

    def test_post_read_revoke_or_fingerprint_change_wins_even_on_outage(self):
        for change in ({'platform_admin':False},{'role':'data_engineer'},{'password_hash':'changed'}):
            for fail in (False,True):
                original=self.users['p'].copy()
                def collect(foundation,check):
                    self.users['p'].update(change)
                    if fail:raise OSError('error-secret')
                    return resources._parse(ROWS,'fixed-ns')
                with patch.object(resources,'_collect',collect),self.assertRaises(OperationError) as caught:self.observe()
                self.assertEqual(caught.exception.code,'PLATFORM_ACCESS_DENIED');self.users['p']=original

    def test_foundation_reread_change_or_revoke_never_discloses(self):
        changed=deepcopy(self.config);changed['revision']+=1
        self.foundation.side_effect=[self.config,changed];self.closed(self.observe(),False)
        self.foundation.side_effect=None
        original=resources._foundation
        n=0
        def read(check):
            nonlocal n
            result=original(check);n+=1
            if n==2:self.users['p']['password_hash']='changed'
            return result
        self.process=Process(ROWS)
        with patch.object(resources,'_foundation',read),self.assertRaises(OperationError):self.observe()

    def test_authority_order_canonical_null_database_before_every_read(self):
        order=[];original=audit._authority
        def authority(db,principal,role,scope,database):
            self.assertIsNone(db);self.assertIsNone(database);self.assertEqual(scope,'platform');order.append('auth')
            return original(db,principal,role,scope,database)
        def foundation_read(db):order.append('foundation');return self.config
        def spawn(*a,**k):order.append('pods');return self.process
        with patch.object(audit,'_authority',authority),patch.object(wr,'_logical_foundation',foundation_read),patch.object(resources.subprocess,'Popen',spawn):self.closed(self.observe())
        for i,event in enumerate(order):
            if event in ('foundation','pods'):
                self.assertEqual(order[i-1],'auth');self.assertEqual(order[i+1],'auth')

    def test_actual_responder_wire_size_including_final_timestamps(self):
        raw=frame(header()+b''.join(row(name='c'+str(i).zfill(62),readonly='',non_root='',uid='',caps='',cpu='',memory='') for i in range(256)))
        self.assertLess(len(raw),65536)
        for handler in self.handlers():
            self.response_raw=raw;status,result=self.request(handler);self.assertEqual(status,200);self.closed(result,False)
            self.assertLessEqual(len(self.response_bytes),65536)
            self.response_raw=ROWS;status,result=self.request(handler);self.assertEqual(status,200);self.closed(result)
        stamp='2026-11-03T01:02:03Z'
        self.process=Process(ROWS)
        with patch.object(resources,'_now',return_value=stamp):candidate=self.observe()
        size=len(json.dumps(candidate,default=str).encode())
        # Only the final result timestamp grows; checking before assigning it fails this test.
        self.process=Process(ROWS)
        with patch.object(resources,'MAX_DTO_BYTES',size),patch.object(resources,'_now',side_effect=[stamp,stamp,stamp,stamp+'extra',stamp]):
            self.closed(self.observe(),False)

    def request(self, handler, *, method='GET', suffix='', headers='', body='', role='admin', principal=None):
        self.process=Process(getattr(self,'response_raw',ROWS))
        class Connection:
            def __init__(self,raw):self.incoming,self.outgoing=io.BytesIO(raw),io.BytesIO()
            def makefile(self,*args):return self.incoming
            def sendall(self,raw):self.outgoing.write(raw)
        raw=(method+' /api/v1/bears/platform-pod-baseline'+suffix+' HTTP/1.1\r\n'
            'Host: 127.0.0.1\r\nConnection: close\r\nX-Shirokuma-Role: '+role+'\r\n'+headers+'\r\n'+body).encode()
        conn=Connection(raw)
        with patch.object(base.runtime_profile,'active',return_value=True),patch.object(auth,'authenticate_principal',return_value=principal or self.principal),patch.object(handler,'log_message'):
            handler(conn,('127.0.0.1',1),SimpleNamespace())
        response=HTTPResponse(Connection(conn.outgoing.getvalue()));response.begin()
        self.response_bytes = response.read()
        return response.status,json.loads(self.response_bytes)

    def test_both_captured_http_surfaces_strict_get_zero_inputs_and_headers(self):
        cases=[({},200),({'headers':'Content-Length: 0\r\n'},200),
            ({'headers':'X-Shirokuma-Role: admin\r\n'},400),
            ({'headers':'Authorization: Basic x\r\nAuthorization: Basic x\r\n'},400),
            ({'headers':'Cookie: a=b\r\nCookie: a=b\r\n'},400),
            ({'headers':'X-Unrelated: a\r\nX-Unrelated: b\r\n'},400),
            ({'headers':'Bad Header: value\r\n'},400),({'headers':'X-Test: bad\x01value\r\n'},400),
            *[({'suffix':s},400) for s in ('?','?x=1','?x=&x=','?database_id=secret','#fragment','?%FF')],
            *[({'headers':'Content-Length: '+s+'\r\n','body':'x' if s=='1' else ''},400) for s in ('1','00','+0','-1','nope')],
            ({'headers':'Content-Length: 0\r\nContent-Length: 0\r\n'},400),
            ({'headers':'Content-Length: 2\r\nContent-Type: application/json\r\n','body':'{}'},400),
            ({'headers':'Transfer-Encoding: \r\n'},400),
            ({'headers':'Transfer-Encoding: chunked\r\n','body':'0\r\n\r\n'},400),
            ({'method':'POST','headers':'Content-Length: 0\r\n'},404),
            ({'role':'data_engineer'},403),({'role':'bi_viewer'},403),
            ({'principal':auth.AuthenticatedPrincipal('dba','admin',False)},403)]
        before=self.files()
        for handler in self.handlers():
            for options,expected in cases:
                self.runner.reset_mock()
                with self.subTest(surface=handler.__name__,options=options):
                    status,result=self.request(handler,**options);self.assertEqual(status,expected,result)
                    if expected==200:self.closed(result)
                    else:self.runner.assert_not_called();self.assertNotIn('items',result)
        self.assertEqual(before,self.files())

    def test_reuses_exact_four_pure_helpers(self):
        for name in ('_boolean','_uid','_drop_all','_nonzero'):
            self.assertIs(getattr(resources,name),getattr(old,name))

    def test_missing_declarations_are_known_not_met_and_no_defaults(self):
        checks = self.checks(row(readonly='', non_root='', uid='', caps='', cpu='', memory=''))
        self.assertEqual(list(checks.values()), ['not_met'] * 4)
        self.assertEqual(self.checks(row(readonly='false'))['read_only_root_filesystem'], 'not_met')
        self.assertEqual(self.checks(row(readonly='null'))['read_only_root_filesystem'], 'not_met')
        # Pod only contributes these two non-root fields, never readonly or capabilities.
        checks = self.checks(row(readonly='', non_root='', uid='', caps=''), 'true', '1000')
        self.assertEqual(list(checks.values()), ['not_met', 'met', 'not_met', 'met'])

    def test_independent_non_root_inheritance_overrides_and_absent_uid(self):
        cases = [
            ('', '', 'true', '1000', 'met'),
            ('null', 'null', 'true', '1000', 'met'),
            ('false', '', 'true', '1000', 'not_met'),
            ('', '0', 'true', '1000', 'not_met'),
            ('true', '', 'false', '0', 'not_met'),
            ('', '1000', 'true', '0', 'met'),
            ('true', '', 'false', '1000', 'met'),
            ('true', '1000', 'false', '0', 'met'),
            ('', '1000', 'false', '0', 'not_met'),
            ('', '1000', '', '', 'not_met'),
            ('true', '', '', '', 'met'),
            ('', '', 'true', '', 'met'),
            ('true', 'null', '', 'null', 'met'),
            ('false', '', 'true', '', 'not_met'),
            ('true', '0', '', '', 'not_met'),
            ('true', str(2**63 - 1), '', '', 'met'),
            ('', '', 'true', str(2**63 - 1), 'met'),
        ]
        for nr, uid, pod_nr, pod_uid, expected in cases:
            with self.subTest(container=(nr, uid), pod=(pod_nr, pod_uid)):
                checks = self.checks(row(non_root=nr, uid=uid), pod_nr, pod_uid)
                self.assertEqual(checks['non_root'], expected)

    def test_quantity_zero_coefficient_exponent_trap_and_positive_fraction(self):
        zero = ['', '0', '00', '+0', '0.0', '+0.0m', '0e9', '0E+9', '00Gi',
                '.0', '0.', '+.000Ki', '0e-999', '00.00Ei']
        positive = ['1', '1.', '.5', '+.5', '0.001', '1e-999', '.001E-9', '1E+999',
                    '9' * 64, '00.10Gi']
        positive += ['1' + unit for unit in ('n', 'u', 'm', 'k', 'M', 'G', 'T', 'P', 'E',
                                           'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei')]
        for values, expected in ((zero, 'not_met'), (positive, 'met')):
            for value in values:
                for field in ('cpu', 'memory'):
                    with self.subTest(value=value, field=field):
                        self.assertEqual(self.checks(row(**{field: value}))['cpu_memory_limits'], expected)

    def test_all_non_p_gates_before_source(self):
        principals = [auth.AuthenticatedPrincipal('dba', 'admin', False),
                      replace(self.principal, platform_admin=False),
                      replace(self.principal, maximum_role='data_engineer'),
                      replace(self.principal, maximum_role='bi_viewer'),
                      replace(self.principal, maximum_role='invalid'),
                      replace(self.principal, principal_id='unknown')]
        principals += [replace(self.principal, platform_admin=value)
                       for value in (1, 0, 'true', 'false', None)]
        with patch.object(resources, '_collect', side_effect=AssertionError('source')) as collect:
            for principal in principals:
                with self.subTest(principal=principal), self.assertRaises(OperationError) as caught:
                    self.observe(principal)
                self.assertEqual((caught.exception.code, caught.exception.status), ('PLATFORM_ACCESS_DENIED', 403))
            for role in ('data_engineer', 'bi_viewer', 'invalid', None):
                with self.subTest(role=role), self.assertRaises(OperationError):
                    self.observe(role=role)
            for change in ({'enabled': False}, {'disabled': True}, {'platform_admin': False},
                           {'enabled': 1}, {'enabled': 'true'}, {'enabled': None},
                           {'disabled': 0}, {'disabled': 'false'}, {'disabled': None},
                           {'platform_admin': 1}, {'platform_admin': 'true'}, {'platform_admin': None},
                           {'role': 'data_engineer'}, {'role': 'bi_viewer'}, {'role': 'invalid'}):
                original = self.users['p'].copy()
                self.users['p'].update(change)
                with self.subTest(change=change), self.assertRaises(OperationError):
                    self.observe()
                self.users['p'] = original
            collect.assert_not_called()
        self.foundation.assert_not_called()
        self.runner.assert_not_called()

    def test_unauthorized_without_store_or_foundation_never_reads_source(self):
        (self.root / 'jobs.sqlite3').unlink()
        with patch.object(audit, 'snapshot', side_effect=AssertionError('store')) as snapshot:
            for principal in (None, object(), replace(self.principal, platform_admin=False)):
                with self.subTest(kind=type(principal).__name__), self.assertRaises(OperationError):
                    resources.observe(principal, 'admin')
            snapshot.assert_not_called()
        self.foundation.assert_not_called()
        self.runner.assert_not_called()
        self.assertFalse((self.root / 'jobs.sqlite3').exists())


    def test_malformed_declarations_of_any_kind_are_atomic(self):
        for kind in ('C','I','E'):
            for options in [dict(readonly='True'),dict(non_root='1'),dict(uid='01'),dict(uid=str(2**63)),dict(caps='[1]'),dict(caps='ALL'),dict(cpu='-1'),dict(memory='null'),dict(cpu='x'),dict(memory='1'*65)]:
                raw=frame(header()+row()+row(kind,name='bad',**options))
                with self.subTest(kind=kind,options=options):
                    self.process=Process(raw);self.closed(self.observe(),False)


if __name__ == '__main__':
    unittest.main()
