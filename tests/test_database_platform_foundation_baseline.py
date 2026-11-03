"""Foundation declaration projection, atomic parsing, authorization and HTTP contracts."""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import replace
from http.client import HTTPResponse
from types import SimpleNamespace
import io
import json
import sqlite3
import subprocess
import unittest
from unittest.mock import patch

import test_database_platform_workload_baseline as base
from test_logical_foundation import settings
import db_platform_foundation_baseline as resources
import db_platform_workload_baseline as old
from warehouse_operations import OperationError

audit, auth, wr, store = base.audit, base.auth, base.wr, base.store
runtime_profile = base.runtime_profile
Process, Selector, row, ROW = base.Process, base.Selector, base.row, base.ROW
TARGETS = (('Deployment','owned-seaweed','storage'), ('Deployment','owned-postgres','catalog_database'),
           ('Deployment','owned-polaris','catalog'), ('Deployment','trino-local','coordinator'),
           ('Deployment','trino-local-worker','worker'), ('Deployment','trino-opa','authorization'),
           ('Job','owned-bootstrap','catalog_bootstrap'))


def header(target='trino-local', non_root='', uid='', namespace='fixed-ns', **overrides):
    kind = next(kind for kind, name, _ in TARGETS if name == target)
    fields = dict(api='apps/v1' if kind == 'Deployment' else 'batch/v1', kind=kind,
        name=target, namespace=namespace, identity='uid-' + target, deleting='',
        fid='wcg-owned', revision='3', template_fid='wcg-owned', template_revision='3',
        non_root=non_root, user=uid)
    fields.update(overrides)
    return ('W\t' + '\t'.join(fields.values()) + '\n').encode()


def remaining():
    return b''.join(header(name) + ROW for _, name, _ in TARGETS if name != 'trino-local')


ROWS = header() + ROW + row('I') + remaining()


class FoundationBaselineTests(unittest.TestCase):
    patch = base.BaselineTests.patch
    files = base.BaselineTests.files
    handlers = base.BaselineTests.handlers

    def setUp(self):
        base.BaselineTests.setUp(self)
        self.config = json.loads(json.dumps(settings(self.root)).replace('safe-logical','fixed-ns'))
        self.config['warehouse_id'] = 'LOCAL'
        self.foundation.return_value = self.config
        self.process = Process(ROWS)
        self.patch(patch.object(resources.logical_foundation, 'render', side_effect=AssertionError('render')))
        self.patch(patch.object(resources.logical_foundation, 'private_read', side_effect=AssertionError('private')))

    def observe(self, principal=None, role='admin'):
        return resources.observe(principal or self.principal, role)

    def closed(self, value, available=True):
        self.assertEqual(set(value), {'scope','warehouse','selected_role','observed_at','baseline_version','coverage','baseline'})
        self.assertEqual((value['scope'],value['warehouse'],value['selected_role']),('platform','LOCAL','admin'))
        self.assertEqual(value['baseline_version'],'foundation-container-declarations-v1')
        self.assertEqual(value['coverage'],dict(kind='foundation_workload_container_declarations',
            pod_template=True,per_container=True,runtime_security=False,admission_decision=False,global_policy=False))
        source=value['baseline']
        self.assertEqual(set(source),{'availability','code','observed_at','workloads'})
        for stamp in (value['observed_at'],source['observed_at']):
            self.assertTrue(stamp.endswith('Z'))
        if available:
            self.assertEqual(source['availability'],'available')
            self.assertIsNone(source['code'])
            self.assertEqual([(w['kind'],w['component']) for w in source['workloads']],
                             [(kind,component) for kind,_,component in TARGETS])
            total=0
            for workload in source['workloads']:
                self.assertEqual(set(workload),{'component','kind','containers'})
                ordinals={'container':0,'init_container':0}
                for container in workload['containers']:
                    total+=1
                    self.assertEqual(set(container),{'kind','ordinal','checks'})
                    ordinals[container['kind']]+=1
                    self.assertEqual(container['ordinal'],ordinals[container['kind']])
                    self.assertEqual(set(container['checks']),{'read_only_root_filesystem','non_root','drop_all_capabilities','cpu_memory_limits'})
                    self.assertTrue(all(v in ('met','not_met') for v in container['checks'].values()))
                self.assertGreater(ordinals['container'],0)
            self.assertLessEqual(total,64)
        else:
            self.assertEqual(source['availability'],'unavailable')
            self.assertEqual(source['code'],'FOUNDATION_BASELINE_UNAVAILABLE')
            self.assertIsNone(source['workloads'])
        raw=json.dumps(value,ensure_ascii=False).encode()
        self.assertLessEqual(len(raw),131072)
        for secret in (b'fixed-ns',b'owned-seaweed',b'uid-',b'wcg-owned',b'context-secret',b'config-secret',b'error-secret',b'securityContext',b'runAsUser',b'1Gi',b'"PASS"',b'"allow"'):
            self.assertNotIn(secret,raw)

    def checks(self, container=None, pod_non_root='', pod_uid=''):
        self.process=Process(header(non_root=pod_non_root,uid=pod_uid)+(ROW if container is None else container)+remaining())
        result=self.observe();self.closed(result)
        return result['baseline']['workloads'][3]['containers'][0]['checks']

    def test_fixed_seven_mixed_kind_command_and_closed_projection(self):
        self.closed(self.observe())
        expected=self.argv[:-1]+['--request-timeout=3s','get',
            'deployment/owned-seaweed','deployment/owned-postgres','deployment/owned-polaris',
            'deployment/trino-local','deployment/trino-local-worker','deployment/trino-opa',
            'job/owned-bootstrap','-o',resources.JSONPATH]
        self.runner.assert_called_once_with(expected,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,shell=False)
        expected_header=(r'jsonpath={range .items[*]}W{"\t"}{.apiVersion}{"\t"}{.kind}'
            r'{"\t"}{.metadata.name}{"\t"}{.metadata.namespace}{"\t"}{.metadata.uid}'
            r'{"\t"}{.metadata.deletionTimestamp}'
            r'{"\t"}{.metadata.labels.shirokuma\.dev/logical-foundation}'
            r'{"\t"}{.metadata.labels.shirokuma\.dev/foundation-revision}'
            r'{"\t"}{.spec.template.metadata.labels.shirokuma\.dev/logical-foundation}'
            r'{"\t"}{.spec.template.metadata.labels.shirokuma\.dev/foundation-revision}'
            r'{"\t"}{.spec.template.spec.securityContext.runAsNonRoot}'
            r'{"\t"}{.spec.template.spec.securityContext.runAsUser}{"\n"}')
        # The container projection must remain exactly the old four-field projection.
        old_suffix=old.JSONPATH[old.JSONPATH.index(r'{range .spec.template.spec.containers[*]}'):]
        self.assertEqual(resources.JSONPATH,expected_header+old_suffix)
        for forbidden in ('status','replicas','completion','env','args','volumes','image','requests','pods','selector'):
            self.assertNotIn(forbidden,resources.JSONPATH)
        self.assertTrue(self.process.stdout.closed)
        self.assertFalse(self.process.killed)
        self.assertEqual(len(self.process.timeouts),1)
        self.assertTrue(0<self.process.timeouts[0]<=8)

    def test_alternative_valid_foundation_shuffled_templates_and_independent_oracle(self):
        foundation = deepcopy(self.config)
        foundation.update(prefix='aurora', foundation_id='wcg-aurora-42', revision=17)
        foundation['kube']['namespace'] = 'research-zone'
        foundation['storage']['endpoint'] = 'http://aurora-seaweed.research-zone.svc.cluster.local:8333'
        foundation['storage']['sts_endpoint'] = foundation['storage']['endpoint']
        foundation['polaris']['endpoint'] = 'http://aurora-polaris.research-zone.svc.cluster.local:8181'
        foundation['trino']['endpoint'] = 'https://trino-local.research-zone.svc.cluster.local:8080'
        foundation = resources.logical_foundation.validate(foundation)
        for field in ('prefix', 'foundation_id', 'revision'):
            self.assertNotEqual(foundation[field], self.config[field])
        self.assertNotEqual(foundation['kube']['namespace'], self.config['kube']['namespace'])
        self.foundation.return_value = foundation
        alternate_argv = ['rtk', 'proxy', 'kubectl', '--kubeconfig', '/private/config-secret',
                          '--context', 'context-secret', '--namespace', 'research-zone',
                          '--request-timeout=15s']
        self.kube.return_value = alternate_argv
        # Independent wire records: both object and template ownership labels are literal.
        headers = [
            b'W\tapps/v1\tDeployment\taurora-seaweed\tresearch-zone\talt-storage\t\twcg-aurora-42\t17\twcg-aurora-42\t17\ttrue\t2000\n',
            b'W\tapps/v1\tDeployment\taurora-postgres\tresearch-zone\talt-database\t\twcg-aurora-42\t17\twcg-aurora-42\t17\ttrue\t2000\n',
            b'W\tapps/v1\tDeployment\taurora-polaris\tresearch-zone\talt-catalog\t\twcg-aurora-42\t17\twcg-aurora-42\t17\ttrue\t2000\n',
            b'W\tapps/v1\tDeployment\ttrino-local\tresearch-zone\talt-coordinator\t\twcg-aurora-42\t17\twcg-aurora-42\t17\ttrue\t2000\n',
            b'W\tapps/v1\tDeployment\ttrino-local-worker\tresearch-zone\talt-worker\t\twcg-aurora-42\t17\twcg-aurora-42\t17\ttrue\t2000\n',
            b'W\tapps/v1\tDeployment\ttrino-opa\tresearch-zone\talt-authorization\t\twcg-aurora-42\t17\twcg-aurora-42\t17\ttrue\t2000\n',
            b'W\tbatch/v1\tJob\taurora-bootstrap\tresearch-zone\talt-bootstrap\t\twcg-aurora-42\t17\twcg-aurora-42\t17\ttrue\t2000\n',
        ]
        ordinary = b'C\ttrue\t\t\t["ALL"]\t250m\t256Mi\n'
        init = b'I\tfalse\tfalse\t0\t[]\t0\t0\n'
        # This oracle does not derive names, components, counts or ordering from _targets.
        expected_workloads = [
            ('storage', 'Deployment', 2, 1),
            ('catalog_database', 'Deployment', 1, 3),
            ('catalog', 'Deployment', 3, 0),
            ('coordinator', 'Deployment', 1, 2),
            ('worker', 'Deployment', 2, 0),
            ('authorization', 'Deployment', 1, 1),
            ('catalog_bootstrap', 'Job', 2, 2),
        ]
        records = [wire + ordinary * count + init * init_count
                   for wire, (_, _, count, init_count) in zip(headers, expected_workloads)]
        self.process = Process(b''.join(records[i] for i in (4, 6, 1, 5, 0, 3, 2)))
        value = self.observe()
        self.runner.assert_called_once_with([
            'rtk', 'proxy', 'kubectl', '--kubeconfig', '/private/config-secret',
            '--context', 'context-secret', '--namespace', 'research-zone',
            '--request-timeout=3s', 'get', 'deployment/aurora-seaweed',
            'deployment/aurora-postgres', 'deployment/aurora-polaris',
            'deployment/trino-local', 'deployment/trino-local-worker',
            'deployment/trino-opa', 'job/aurora-bootstrap', '-o', resources.JSONPATH,
        ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False)
        self.kube.assert_called_once_with(foundation)
        self.assertEqual(self.foundation.call_count, 2)
        self.closed(value)
        expected = []
        check_names = ('read_only_root_filesystem', 'non_root',
                       'drop_all_capabilities', 'cpu_memory_limits')
        for component, kind, count, init_count in expected_workloads:
            containers = [dict(kind=container_kind, ordinal=ordinal,
                               checks=dict.fromkeys(check_names, status))
                          for container_kind, number, status in (
                              ('container', count, 'met'), ('init_container', init_count, 'not_met'))
                          for ordinal in range(1, number + 1)]
            expected.append(dict(component=component, kind=kind, containers=containers))
        self.assertEqual(value['baseline']['workloads'], expected)
        public = json.dumps(value)
        for private in ('aurora', 'research-zone', 'wcg-aurora-42', 'alt-storage',
                        'alt-bootstrap', '250m', '256Mi'):
            self.assertNotIn(private, public)
        self.assertTrue(self.process.stdout.closed)

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

    def test_malformed_bool_uid_and_int64_edges_are_atomic_even_if_overridden(self):
        for value in ('True', 'FALSE', '1', '0', '"true"', '[]', '{}', ' true', 'false ', '\r'):
            for field in ('readonly', 'non_root', 'pod_non_root'):
                options = {field: value}
                raw = (header(non_root=options.pop('pod_non_root', 'true')) + row(**options)
                       + remaining())
                with self.subTest(value=value, field=field):
                    self.process = Process(raw)
                    self.closed(self.observe(), False)
        for value in ('-1', '-0', '+1', '1.0', '1e3', 'true', 'false', '"1"', '[]', '{}',
                      ' 1', '1 ', '01', str(2**63), '9' * 30):
            for pod in (False, True):
                raw = (header(uid=value if pod else '1000')
                       + row(uid='1000' if pod else value) + remaining())
                with self.subTest(value=value, pod=pod):
                    self.process = Process(raw)
                    self.closed(self.observe(), False)

    def test_capability_exact_all_membership_with_extra_entries(self):
        for caps, expected in [('', 'not_met'), ('null', 'not_met'), ('[]', 'not_met'),
                ('["all"]', 'not_met'), ('["ALL_EXTRA"]', 'not_met'),
                ('["ALL "]', 'not_met'), ('["ALL"]', 'met'),
                ('["NET_RAW","ALL","SYS_ADMIN"]', 'met'), ('["ALL","ALL"]', 'met')]:
            with self.subTest(caps=caps):
                self.assertEqual(self.checks(row(caps=caps))['drop_all_capabilities'], expected)
        for malformed in ('ALL', 'true', '0', '{}', '"ALL"', '[null]', '[1]', '[true]',
                          '[["ALL"]]', '["ALL",]', '["ALL"]junk'):
            self.process = Process(ROWS + row('I', caps=malformed))
            with self.subTest(malformed=malformed):
                self.closed(self.observe(), False)

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

    def test_quantity_validator_is_reused_unchanged_invalid_is_atomic(self):
        import db_platform_resources
        self.assertIs(resources._nonzero, old._nonzero)
        invalid = ['-1', '-0', '-.1', 'NaN', 'Infinity', '1K', '1ki', '1mi', '1e',
                   '1e1m', '1MiB', '.', '+', '1..0', '1_000', ' 1', '1 ', '1\r',
                   '\x00', '１', '1μ', '9' * 65, '1/2', '0x10', 'true', 'null']
        for value in invalid:
            for field in ('cpu', 'memory'):
                # Invalid must not short-circuit to not_met when the other field is absent/zero.
                options = dict(cpu='', memory='0')
                options[field] = value
                with self.subTest(value=value, field=field):
                    self.process = Process(ROWS + row('I', **options))
                    self.closed(self.observe(), False)

    def test_identity_api_kind_namespace_uid_deleting_and_both_ownership_labels_atomic(self):
        for _,name,_ in TARGETS:
            for field,values in {
                'api':['','v1','apps/v2','batch/v2'], 'kind':['','Pod','deployment'],
                'name':['foreign'], 'namespace':['','foreign'],
                'identity':['','null uid','x/y','x'*129], 'deleting':['2026-11-03T00:00:00Z','false'],
                'fid':['','foreign'], 'revision':['','03','4'],
                'template_fid':['','foreign'], 'template_revision':['','4'],
            }.items():
                for value in values:
                    with self.subTest(name=name,field=field,value=value):
                        self.process=Process(ROWS.replace(header(name),header(name,**{field:value}),1))
                        self.closed(self.observe(),False)
        for raw in (b'',ROW+ROWS,ROWS+header()+ROW,ROWS.replace(header(),b'',1),
                    ROWS.replace(header(),header(identity='uid-owned-seaweed'),1),
                    ROWS.replace(header()+ROW+row('I'),header()+row('I'),1),
                    ROWS+b'\n',ROWS+b'\xff',ROWS.replace(b'\n',b'\r\n'),
                    ROWS.replace(ROW,b'C\ttrue\n',1),ROWS.replace(ROW,row('X'),1)):
            self.process=Process(raw);self.closed(self.observe(),False)

    def test_arbitrary_distribution_kind_ordinals_and_exact_64_limit(self):
        for kind in ('C','I'):
            for large in (0,3,6):
                raw=b''.join(header(name)+ROW+row(kind)*(57 if index==large else 0)
                             for index,(_,name,_) in reversed(list(enumerate(TARGETS))))
                self.process=Process(raw);value=self.observe();self.closed(value)
                self.assertEqual(len(value['baseline']['workloads'][large]['containers']),58)
                self.process=Process(raw+row(kind));self.closed(self.observe(),False)
        for _,name,_ in TARGETS:
            raw=b''.join(header(n)+ROW*2+row('I')*3 for _,n,_ in TARGETS)
            self.process=Process(raw);self.closed(self.observe())

    def test_exact_raw_bytes_and_whole_dto_byte_boundary(self):
        padding=32768-len(ROWS)
        raw=ROWS.replace(b'["ALL"]',b'["'+b'x'*padding+b'ALL"]',1)
        self.assertEqual(len(raw),32768)
        self.process=Process(raw);self.closed(self.observe())
        self.process=Process(raw+b' ');self.closed(self.observe(),False)
        # A bounded internal DTO cannot silently cross the final UTF-8 envelope.
        workloads=resources._parse(ROWS,self.config)
        workloads[0]['containers'][0]['checks']['read_only_root_filesystem']='界'*45000
        with patch.object(resources,'_collect',return_value=workloads):
            self.closed(self.observe(),False)
        # Exact JSON byte cap (including multi-byte UTF-8), measured on the entire DTO.
        with patch.object(resources,'_now',return_value='2026-11-03T00:00:00Z'):
            self.process=Process(ROWS);value=self.observe()
            size=len(json.dumps(value,ensure_ascii=False).encode())
            self.process=Process(ROWS)
            with patch.object(resources,'MAX_DTO_BYTES',size):self.closed(self.observe())
            self.process=Process(ROWS)
            with patch.object(resources,'MAX_DTO_BYTES',size-1):self.closed(self.observe(),False)

    def test_completed_job_and_zero_replicas_are_not_collection_conditions(self):
        for field in ('replicas','ready','status','succeeded','completion','conditions'):
            self.assertNotIn(field,resources.JSONPATH)
        self.closed(self.observe())

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

    def test_authority_before_each_local_and_external_read_and_final_reply(self):
        order=[]
        authority=resources._authority
        def checked(*args):
            order.append('auth');return authority(*args)
        @contextmanager
        def snapshot():
            order.append('snapshot')
            with audit_snapshot() as db:yield db
        def foundation(db):order.append('foundation');return self.config
        def run(*args,**kwargs):order.append('external');return self.process
        audit_snapshot=audit.snapshot
        with patch.object(resources,'_authority',checked),patch.object(audit,'snapshot',snapshot),patch.object(wr,'_logical_foundation',foundation),patch.object(resources.subprocess,'Popen',run):
            self.closed(self.observe())
        self.assertEqual(order,['auth','auth','snapshot','auth','foundation','auth','external','auth','auth','auth','snapshot','auth','foundation','auth'])

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
            self.process = Process(b'x' * 32769 if mode == 'overflow' else ROWS,
                                   returncode=1 if mode == 'exit' else 0)
            self.runner.reset_mock()
            with ExitStack() as stack:
                if mode == 'select_timeout':
                    stack.enter_context(patch.object(Selector, 'select', return_value=[]))
                if mode == 'deadline':
                    stack.enter_context(patch.object(resources.time, 'monotonic', side_effect=[0, 9]))
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
        raw = (method + ' /api/v1/bears/platform-foundation-baseline' + suffix + ' HTTP/1.1\r\n'
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
                        self.assertNotIn('baseline', value)
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
            self.assertNotIn('baseline',result)
            self.users['p']['platform_admin']=True


if __name__ == '__main__':
    unittest.main()
