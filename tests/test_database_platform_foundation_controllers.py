"""Closed controller projection, numeric semantics, fencing and actual HTTP adapters."""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import replace
from http.client import HTTPResponse
from types import SimpleNamespace
import io
import json
import os
import sqlite3
import subprocess
import unittest
from unittest.mock import patch

import test_database_platform_foundation_baseline as baseline
import db_platform_foundation_controllers as resources
from warehouse_operations import OperationError

audit, auth, wr, store = baseline.audit, baseline.auth, baseline.wr, baseline.store
runtime_profile = baseline.runtime_profile
Process, Selector = baseline.Process, baseline.Selector
PRINCIPAL_ACCOUNTS = auth._principal_accounts
TARGETS = (('Deployment','owned-seaweed','storage'), ('Deployment','owned-postgres','catalog_database'),
           ('Deployment','owned-polaris','catalog'))
FIELDS = ('generation','observed_generation','desired_replicas','replicas','updated_replicas',
          'ready_replicas','available_replicas','unavailable_replicas')


def row(name='owned-seaweed', **changes):
    fields = dict(marker='W', api='apps/v1', kind='Deployment', name=name, namespace='fixed-ns',
        identity='uid-'+name, deleting='', fid='wcg-owned', revision='3',
        template_fid='wcg-owned', template_revision='3', generation='5', observed_generation='4',
        desired_replicas='1', replicas='2', updated_replicas='2', ready_replicas='3',
        available_replicas='4', unavailable_replicas='7')
    fields.update(changes)
    return ('\t'.join(fields.values())+'\n').encode()


ROWS = b''.join(row(name) for _, name, _ in TARGETS)


class FoundationControllersTests(unittest.TestCase):
    patch = baseline.FoundationBaselineTests.patch
    files = baseline.FoundationBaselineTests.files
    handlers = baseline.FoundationBaselineTests.handlers

    def setUp(self):
        baseline.FoundationBaselineTests.setUp(self)
        self.process = Process(ROWS)

    def observe(self, principal=None, role='admin'):
        return resources.observe(principal or self.principal, role)

    def closed(self, value, available=True):
        self.assertEqual(set(value), {'scope','warehouse','selected_role','observed_at','controller_version','coverage','controllers'})
        self.assertEqual((value['scope'],value['warehouse'],value['selected_role']),('platform','LOCAL','admin'))
        self.assertEqual(value['controller_version'],'foundation-storage-catalog-controllers-v1')
        self.assertEqual(value['coverage'],dict(kind='deployment_controller_counts',managed_foundation=True,
            service_connectivity=False,sql_health=False,policy_decision=False))
        source=value['controllers']
        self.assertEqual(set(source),{'availability','code','observed_at','deployments'})
        self.assertTrue(value['observed_at'].endswith('Z'))
        self.assertTrue(source['observed_at'].endswith('Z'))
        self.assertEqual(source['availability'],'available' if available else 'unavailable')
        self.assertEqual(source['code'],None if available else 'FOUNDATION_CONTROLLERS_UNAVAILABLE')
        if available:
            self.assertEqual([r['component'] for r in source['deployments']],['storage','catalog_database','catalog'])
            for r in source['deployments']:
                self.assertEqual(set(r),{'component',*FIELDS})
                self.assertGreater(r['generation'],0)
                for k in FIELDS:
                    if r[k] is not None:
                        self.assertIs(type(r[k]),int)
                        self.assertTrue(0<=r[k]<=9007199254740991)
        else:
            self.assertIsNone(source['deployments'])
        public=json.dumps(value,ensure_ascii=False).encode()
        self.assertLessEqual(len(public),65536)
        for private in (b'fixed-ns',b'owned-',b'uid-',b'wcg-owned',b'config-secret',b'context-secret',b'error-secret',b'conditions',b'healthy'):
            self.assertNotIn(private,public)

    def test_literal_fixed_command_projection_one_attempt_and_closed_dto(self):
        value=self.observe();self.closed(value)
        expected=(r'jsonpath={range .items[*]}W{"\t"}{.apiVersion}{"\t"}{.kind}'
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
        self.assertEqual(resources.JSONPATH,expected)
        self.runner.assert_called_once_with(self.argv[:-1]+['--request-timeout=3s','get',
            'deployment/owned-seaweed','deployment/owned-postgres','deployment/owned-polaris','-o',expected],
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,shell=False)
        for forbidden in ('conditions','messages','reasons','paused','containers','env','args','probe','image','selector'):
            self.assertNotIn(forbidden,expected)
        self.assertTrue(self.process.stdout.closed)
        self.assertFalse(self.process.killed)
        self.assertEqual(len(self.process.timeouts),1)
        self.assertTrue(0<self.process.timeouts[0]<=8)
        self.assertEqual(value['controllers']['deployments'][0],dict(component='storage',generation=5,
            observed_generation=4,desired_replicas=1,replicas=2,updated_replicas=2,ready_replicas=3,
            available_replicas=4,unavailable_replicas=7))

    def test_all_status_null_zero_stale_and_count_relationships_preserved(self):
        for nullable in ('','null','0'):
            changes={k:nullable for k in FIELDS if k not in ('generation','desired_replicas')}
            self.process=Process(row(desired_replicas='0',**changes)+row('owned-postgres')+row('owned-polaris'))
            value=self.observe();self.closed(value);r=value['controllers']['deployments'][0]
            self.assertEqual(r['desired_replicas'],0)
            for k in changes:self.assertEqual(r[k],0 if nullable=='0' else None)
        for observed in ('0','1','4','5','null',''):
            self.process=Process(row(observed_generation=observed)+row('owned-postgres')+row('owned-polaris'))
            value=self.observe();self.closed(value)
            self.assertEqual(value['controllers']['deployments'][0]['observed_generation'],None if observed in ('','null') else int(observed))
        self.process=Process(row(observed_generation='6')+row('owned-postgres')+row('owned-polaris'))
        self.closed(self.observe(),False)
        # Desired=0, ready>replicas, all counts>desired, and independent unavailable remain intact.
        self.process=Process(row(desired_replicas='0')+row('owned-postgres')+row('owned-polaris'))
        r=self.observe()['controllers']['deployments'][0]
        self.assertEqual([r[k] for k in FIELDS],[5,4,0,2,2,3,4,7])

    def test_canonical_numbers_maximum_and_overflow_for_each_field(self):
        for k in FIELDS:
            for bad in ('true','false','-1','-0','+0','01','1.0','1e0','1E3',' 1','1 ','１','NaN','[]','{}','9007199254740992','9'*50):
                with self.subTest(field=k,value=bad):
                    self.process=Process(row(**{k:bad})+row('owned-postgres')+row('owned-polaris'))
                    self.closed(self.observe(),False)
            changes={k:'9007199254740991','generation':'9007199254740991'}
            self.process=Process(row(**changes)+row('owned-postgres')+row('owned-polaris'))
            self.closed(self.observe())
        for k in ('generation','desired_replicas'):
            for bad in ('','null')+ (('0',) if k=='generation' else ()):
                self.process=Process(row(**{k:bad})+row('owned-postgres')+row('owned-polaris'))
                self.closed(self.observe(),False)

    def test_all_identity_and_ownership_fields_and_atomic_rows(self):
        cases={'marker':['D',''], 'api':['v1','batch/v1',''], 'kind':['Job','deployment',''],
            'name':['trino-local','foreign',''], 'namespace':['foreign',''],
            'identity':['','-bad','a'*129,'bad/value','uid-owned-postgres'],
            'deleting':['2026-11-03T00:00:00Z','false'], 'fid':['foreign',''],
            'revision':['4','03',''], 'template_fid':['foreign',''], 'template_revision':['4','03','']}
        for k,values in cases.items():
            for bad in values:
                with self.subTest(field=k,value=bad):
                    self.process=Process(row(**{k:bad})+row('owned-postgres')+row('owned-polaris'))
                    self.closed(self.observe(),False)
        for raw in (b'',ROWS+row(),row()+row(),ROWS+b'\n',ROWS+b'\xff',ROWS.replace(b'\n',b'\r\n'),ROWS.replace(b'\t5\t',b'\t5\textra\t',1)):
            self.process=Process(raw);self.closed(self.observe(),False)
        self.process=Process(row(deleting='null')+row('owned-postgres')+row('owned-polaris'))
        self.closed(self.observe())

    def test_alternative_valid_foundation_shuffled_independent_literal_oracle(self):
        foundation=deepcopy(self.config)
        foundation.update(prefix='aurora',foundation_id='wcg-aurora-42',revision=17)
        foundation['kube']['namespace']='research-zone'
        foundation['storage']['endpoint']='http://aurora-seaweed.research-zone.svc.cluster.local:8333'
        foundation['storage']['sts_endpoint']=foundation['storage']['endpoint']
        foundation['polaris']['endpoint']='http://aurora-polaris.research-zone.svc.cluster.local:8181'
        foundation['trino']['endpoint']='https://trino-local.research-zone.svc.cluster.local:8080'
        foundation=resources.logical_foundation.validate(foundation)
        self.foundation.return_value=foundation
        self.kube.return_value=['rtk','proxy','kubectl','--namespace','research-zone','--request-timeout=15s']
        self.process=Process(
            b'W\tapps/v1\tDeployment\taurora-polaris\tresearch-zone\talt-catalog\t\twcg-aurora-42\t17\twcg-aurora-42\t17\t8\t8\t1\t2\t3\t4\t5\t6\n'
            b'W\tapps/v1\tDeployment\taurora-seaweed\tresearch-zone\talt-storage\t\twcg-aurora-42\t17\twcg-aurora-42\t17\t3\t0\t0\t\tnull\t0\t7\t9\n'
            b'W\tapps/v1\tDeployment\taurora-postgres\tresearch-zone\talt-db\t\twcg-aurora-42\t17\twcg-aurora-42\t17\t9\t\t2\t7\t6\t5\t4\t3\n')
        value=self.observe();self.closed(value)
        expected=[dict(component='storage',generation=3,observed_generation=0,desired_replicas=0,replicas=None,updated_replicas=None,ready_replicas=0,available_replicas=7,unavailable_replicas=9),
            dict(component='catalog_database',generation=9,observed_generation=None,desired_replicas=2,replicas=7,updated_replicas=6,ready_replicas=5,available_replicas=4,unavailable_replicas=3),
            dict(component='catalog',generation=8,observed_generation=8,desired_replicas=1,replicas=2,updated_replicas=3,ready_replicas=4,available_replicas=5,unavailable_replicas=6)]
        self.assertEqual(value['controllers']['deployments'],expected)
        self.runner.assert_called_once_with(['rtk','proxy','kubectl','--namespace','research-zone','--request-timeout=3s','get','deployment/aurora-seaweed','deployment/aurora-postgres','deployment/aurora-polaris','-o',resources.JSONPATH],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,shell=False)
        self.kube.assert_called_once_with(foundation)
        self.assertEqual(self.foundation.call_count,2)
        for private in ('aurora','research-zone','wcg-aurora-42','alt-catalog'):self.assertNotIn(private,json.dumps(value))

    def test_raw16k_and_dto64k_exact_bounds(self):
        self.assertEqual(resources.MAX_STDOUT_BYTES,16384)
        self.assertEqual(resources.MAX_DTO_BYTES,65536)
        # Even syntactically invalid data at the cap must be read to EOF, while cap+1 kills.
        for size,killed in ((16384,False),(16385,True)):
            self.process=Process(b'x'*size);self.closed(self.observe(),False)
            self.assertEqual(self.process.killed,killed)
            self.assertTrue(self.process.stdout.closed)
        with patch.object(resources,'_now',return_value='2026-11-03T00:00:00Z'):
            self.process=Process(ROWS);value=self.observe();size=len(json.dumps(value,ensure_ascii=False).encode())
            self.process=Process(ROWS)
            with patch.object(resources,'MAX_DTO_BYTES',size):self.closed(self.observe())
            self.process=Process(ROWS)
            with patch.object(resources,'MAX_DTO_BYTES',size-1):self.closed(self.observe(),False)
        huge=resources._parse(ROWS,self.config);huge[0]['component']='界'*22000
        with patch.object(resources,'_collect',return_value=huge):self.closed(self.observe(),False)

    def test_canonical_current_accounts_and_p_null_without_database_or_grants(self):
        account=dict(role='admin',platform_admin=True,enabled=True,disabled=False,salt='ab'*16,password_hash='cd'*32,default_database=None)
        path=self.root/'accounts.json';path.write_text(json.dumps({'p':account}));path.chmod(0o600)
        before=self.files()
        # The real account reader validates canonical on-disk current authority.
        with patch.dict(os.environ,{'SHIROKUMA_AUTH_MODE':'shared','SHIROKUMA_AUTH_FILE':str(path)}),patch.object(runtime_profile,'current',return_value=None):
            self.accounts.side_effect=PRINCIPAL_ACCOUNTS
            self.closed(self.observe())
            account['platform_admin']=False;path.write_text(json.dumps({'p':account}))
            self.runner.reset_mock()
            with self.assertRaises(OperationError) as caught:self.observe()
            self.assertEqual(caught.exception.status,403);self.runner.assert_not_called()
        # Test-owned account edits aside, the source creates no store tables or data.
        self.assertEqual((self.root/'jobs.sqlite3').read_bytes(),before['jobs.sqlite3'][0])
        for forbidden in self.forbidden:forbidden.assert_not_called()

    def test_shared_deadline_includes_initial_and_final_local_reads(self):
        clock=[0]
        def initial(db):
            clock[0]=9
            return self.config
        with patch.object(resources.time,'monotonic',side_effect=lambda:clock[0]),patch.object(wr,'_logical_foundation',side_effect=initial):
            self.closed(self.observe(),False)
        self.runner.assert_not_called()
        clock[0]=0;calls=0;self.process=Process(ROWS)
        def final(db):
            nonlocal calls
            calls+=1
            if calls==2:clock[0]=9
            return self.config
        with patch.object(resources.time,'monotonic',side_effect=lambda:clock[0]),patch.object(wr,'_logical_foundation',side_effect=final):
            self.closed(self.observe(),False)
        self.runner.assert_called_once()

    def test_local_read_error_and_final_fingerprint_revocation_precede_unavailable(self):
        for point in (1,2):
            calls=0;self.process=Process(ROWS)
            original=self.users['p'].copy()
            def read(db):
                nonlocal calls
                calls+=1
                if calls==point:
                    self.users['p']['enabled']=False
                    raise ValueError('error-secret')
                return self.config
            with patch.object(wr,'_logical_foundation',side_effect=read),self.assertRaises(OperationError) as caught:self.observe()
            self.assertEqual(caught.exception.status,403)
            self.users['p']=original

    def test_authority_before_each_read_and_final_reply(self):
        order=[];authority=resources._authority;original=audit.snapshot
        def checked(*args):order.append('auth');return authority(*args)
        @contextmanager
        def snapshot():
            order.append('snapshot')
            with original() as db:yield db
        def foundation(db):order.append('foundation');return self.config
        def run(*args,**kwargs):order.append('external');return self.process
        with patch.object(resources,'_authority',checked),patch.object(audit,'snapshot',snapshot),patch.object(wr,'_logical_foundation',foundation),patch.object(resources.subprocess,'Popen',run):self.closed(self.observe())
        for i,event in enumerate(order):
            if event in ('snapshot','foundation','external'):self.assertEqual(order[i-1],'auth')
        self.assertEqual(order[-1],'auth');self.assertEqual(order.count('external'),1)
        self.assertIs(resources._foundation,baseline.resources._foundation)


    def test_p_null_real_accounts_readonly_snapshots_no_database_grants_or_writes(self):
            original=audit.snapshot
            @contextmanager
            def checked():
                with original() as db:
                    self.assertEqual(db.execute('PRAGMA query_only').fetchone()[0],1)
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0],0)
                    yield db
            before=self.files()
            with patch.object(audit,'snapshot',checked):self.closed(self.observe())
            self.assertEqual(self.foundation.call_count,2)
            for call in self.foundation.call_args_list:self.assertIsInstance(call.args[0],sqlite3.Connection)
            self.assertEqual(before,self.files())
            for forbidden in self.forbidden:forbidden.assert_not_called()

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

    def test_mid_read_revocation_and_fingerprint_changes_are_403_even_on_source_failure(self):
            for change in ({'platform_admin':False},{'enabled':False},{'role':'data_engineer'},
                           {'password_hash':'changed'},{'platform_admin':1}):
                for fail in (False,True):
                    original=self.users['p'].copy()
                    def read(size):
                        self.users['p'].update(change)
                        if fail:raise OSError('error-secret')
                        return b''
                    self.process=Process(ROWS)
                    with patch.object(self.process.stdout,'read1',side_effect=read),self.assertRaises(OperationError) as caught:
                        self.observe()
                    self.assertEqual((caught.exception.code,caught.exception.status),('PLATFORM_ACCESS_DENIED',403))
                    self.assertTrue(self.process.stdout.closed)
                    self.users['p']=original

    def test_initial_final_foundation_equality_and_post_foundation_fingerprint(self):
            for field in ('revision','foundation_id','prefix'):
                changed=deepcopy(self.config);changed[field]=4 if field=='revision' else 'different'
                self.process=Process(ROWS)
                with patch.object(wr,'_logical_foundation',side_effect=[self.config,changed]):self.closed(self.observe(),False)
            for point in ('initial','final'):
                calls=0
                def foundation(db):
                    nonlocal calls
                    calls+=1
                    if calls==(1 if point=='initial' else 2):self.users['p']['password_hash']='changed-'+point
                    return self.config
                self.process=Process(ROWS)
                with patch.object(wr,'_logical_foundation',foundation),self.assertRaises(OperationError) as caught:self.observe()
                self.assertEqual(caught.exception.status,403)

    def test_unavailable_local_source_or_invalid_foundation_never_spawns(self):
            for error in (OSError('error-secret'),ValueError('error-secret'),OperationError('NOT_READY',503)):
                with patch.object(wr,'_logical_foundation',side_effect=error):self.closed(self.observe(),False)
            for field,value in (('warehouse_id','OTHER'),('prefix','bad/name'),('revision',0)):
                config=deepcopy(self.config);config[field]=value
                with patch.object(wr,'_logical_foundation',return_value=config):self.closed(self.observe(),False)
            self.runner.assert_not_called()

    def test_timeout_exit_overflow_spawn_and_kill_wait_close_single_attempt(self):
            for mode in ('select_timeout', 'deadline', 'exit', 'overflow', 'wait_timeout', 'read_error'):
                self.process = Process(b'x' * 16385 if mode == 'overflow' else ROWS,
                                       returncode=1 if mode == 'exit' else 0)
                self.runner.reset_mock()
                with ExitStack() as stack:
                    if mode == 'select_timeout':
                        stack.enter_context(patch.object(Selector, 'select', return_value=[]))
                    if mode == 'deadline':
                        stack.enter_context(patch.object(resources.time, 'monotonic', side_effect=[0, 0, 9]))
                    if mode == 'wait_timeout':
                        original = self.process.wait
                        def wait(timeout=None):
                            if timeout is not None:
                                raise subprocess.TimeoutExpired('fixed', timeout)
                            return original()
                        stack.enter_context(patch.object(self.process, 'wait', side_effect=wait))
                    if mode == 'read_error':
                        stack.enter_context(patch.object(self.process.stdout, 'read1', side_effect=OSError('error-secret')))
                    with self.subTest(mode=mode):
                        self.closed(self.observe(), False)
                        self.runner.assert_called_once()
                        self.assertTrue(self.process.stdout.closed)
                        self.assertTrue(self.process.finished)
                        self.assertEqual(self.process.killed, mode != 'exit')
                        if mode != 'exit':
                            self.assertIsNone(self.process.timeouts[-1])
            with patch.object(resources.subprocess, 'Popen', side_effect=OSError('error-secret')) as run:
                self.closed(self.observe(), False)
                run.assert_called_once()

    def request(self, handler, *, method='GET', suffix='', headers='', body='', role='admin', principal=None):
            class Connection:
                def __init__(self, raw): self.incoming, self.outgoing = io.BytesIO(raw), io.BytesIO()
                def makefile(self, *args): return self.incoming
                def sendall(self, raw): self.outgoing.write(raw)
            raw = (method + ' /api/v1/bears/platform-foundation-controllers' + suffix + ' HTTP/1.1\r\n'
                   'Host: 127.0.0.1\r\nConnection: close\r\nX-Shirokuma-Role: ' + role + '\r\n'
                   + headers + '\r\n' + body).encode()
            conn = Connection(raw)
            with patch.object(runtime_profile, 'active', return_value=True), \
                    patch.object(auth, 'authenticate_principal', return_value=principal or self.principal), \
                    patch.object(handler, 'log_message'):
                handler(conn, ('127.0.0.1', 1), SimpleNamespace())
            response = HTTPResponse(Connection(conn.outgoing.getvalue()))
            response.begin()
            return response.status, json.loads(response.read())

    def test_both_actual_http_families_strict_zero_input(self):
            cases = [({}, 200), ({'headers': 'Content-Length: 0\r\n'}, 200),
                     ({'headers': 'X-Shirokuma-Role: admin\r\n'}, 400),
                     ({'headers': 'X-Unrelated: a\r\nX-Unrelated: b\r\n'}, 400),
                     ({'headers': 'Bad Header: value\r\n'}, 400),
                     ({'headers': 'X-Test: bad\x01value\r\n'}, 400),
                     ({'suffix': '?'}, 400), ({'suffix': '?context=%7B%7D'}, 400),
                     ({'suffix': '?database_id=secret'}, 400), ({'suffix': '?x=&x='}, 400),
                     ({'suffix': '#fragment'}, 400), ({'suffix': '?%FF'}, 400),
                     ({'headers': 'Content-Length: 1\r\n', 'body': 'x'}, 400),
                     ({'headers': 'Content-Length: 1\r\n', 'body': ' '}, 400),
                     ({'headers': 'Content-Length: 2\r\nContent-Type: application/json\r\n', 'body': '{}'}, 400),
                     ({'headers': 'Content-Length: 00\r\n'}, 400),
                     ({'headers': 'Content-Length: +0\r\n'}, 400),
                     ({'headers': 'Content-Length: nope\r\n'}, 400),
                     ({'headers': 'Content-Length: 0\r\nContent-Length: 0\r\n'}, 400),
                     ({'headers': 'Content-Length: -1\r\n'}, 400),
                     ({'headers': 'Transfer-Encoding: \r\n'}, 400),
                     ({'headers': 'Transfer-Encoding: chunked\r\n', 'body': '0\r\n\r\n'}, 400),
                     ({'method': 'POST', 'headers': 'Content-Length: 0\r\n'}, 404),
                     ({'role': 'data_engineer'}, 403), ({'role': 'bi_viewer'}, 403),
                     ({'principal': auth.AuthenticatedPrincipal('dba', 'admin', False)}, 403)]
            before = self.files()
            for handler in self.handlers():
                for options, expected in cases:
                    with self.subTest(surface=handler.__name__, options=options):
                        self.process = Process(ROWS)
                        self.runner.reset_mock()
                        status, value = self.request(handler, **options)
                        self.assertEqual(status, expected, value)
                        if expected == 200:
                            self.closed(value)
                        else:
                            self.runner.assert_not_called()
                            self.assertNotIn('controllers', value)
                            if expected == 403:
                                self.assertEqual(value['code'], 'PLATFORM_ACCESS_DENIED')
            self.assertEqual(before, self.files())

    def test_both_actual_http_families_source_failure_and_current_revocation(self):
            for handler in self.handlers():
                with patch.object(wr,'_logical_foundation',side_effect=ValueError('error-secret')):
                    status,result=self.request(handler)
                self.assertEqual(status,200);self.closed(result,False)
                self.process=Process(ROWS)
                def read(size):
                    self.users['p']['platform_admin']=False
                    raise OSError('error-secret')
                with patch.object(self.process.stdout,'read1',side_effect=read):status,result=self.request(handler)
                self.assertEqual(status,403,result)
                self.assertEqual(result['code'],'PLATFORM_ACCESS_DENIED')
                self.assertNotIn('controllers',result)
                self.users['p']['platform_admin']=True


if __name__ == '__main__':
    unittest.main()
