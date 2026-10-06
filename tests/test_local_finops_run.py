from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from tools.local_experiment.finops import run as R
from test_local_finops_contract import NOW, component_args, pod, metrics, row


def storage_complete():
    records = []
    for component in R.S.TARGETS:
        entry = R.S.unknown(component, 'READ_FAILED')
        entry.update(state='observed' if R.S.TARGETS[component][-1] else 'no_direct_pvc',
                     reason=None, workload_uid='11111111-1111-4111-8111-111111111111',
                     pod_uid='22222222-2222-4222-8222-222222222222',
                     request_started_at='2026-09-20T00:00:20Z', observed_at=NOW)
        if entry['state'] == 'observed':
            entry['pvc'] = dict(name=R.S.TARGETS[component][-1],uid='33333333-3333-4333-8333-333333333333',
                 phase='Bound',requested_bytes='5368709120',capacity_bytes='5368709120',
                 storage_class='local-path',volume_mode='Filesystem',pv_name='pvc-33333333-3333-4333-8333-333333333333')
            entry['pv'] = dict(name=entry['pvc']['pv_name'],uid='44444444-4444-4444-8444-444444444444',
                               capacity_bytes='5368709120',reclaim_policy='Delete')
            entry['retention'] = dict(when_deleted='Retain',when_scaled='Retain',template_prune=None)
        records.append(entry)
    return R.S.validate(dict(schema_version=1,components=records,limits=R.S.LIMITS[:]))


def docker_storage_complete():
    entry = R.D.unknown('READ_FAILED')
    entry.update(state='observed',reason=None,container_id='a'*64,container_running=False,
                 volume_created_at='2026-09-19T00:00:00.000000000Z',
                 mount_destination='/var/lib/clickhouse',mount_read_write=True,
                 request_started_at='2026-09-20T00:00:20Z',observed_at=NOW)
    return R.D.validate(dict(schema_version=1,agenthouse=entry,limits=R.D.LIMITS[:]))


def host_storage_complete():
    rows=[]
    for component in R.H.targets(1):
        entry=R.H.unknown(component,'READ_FAILED')
        entry.update(state='observed',reason=None,owner_sha256='a'*64,
                     directory_identity={'device':'1','inode':'42'},
                     request_started_at='2026-09-20T00:00:20Z',observed_at=NOW)
        rows.append(entry)
    return R.H.validate(dict(schema_version=1,components=rows,limits=R.H.LIMITS[:]))


def host_usage_complete():
    rows=[]
    for row in host_storage_complete()['components']:
        item=R.H.measured_row(row)
        if item['component']=='notebook-lab':
            item.update(usage_state='observed',usage_reason=None,used_bytes='16',regular_file_count=2,
                        directory_count=1,usage_started_at='2026-09-20T00:00:20Z',usage_observed_at=NOW)
        rows.append(item)
    return R.H.validate(dict(schema_version=2,components=rows,limits=R.H.MEASURED_LIMITS[:]))


def mlflow_usage_complete():
    rows=[]
    for row in host_usage_complete()['components']:
        base={key:row[key] for key in R.H.unknown('mlflow','READ_FAILED')}
        item=R.H.measured_row(base,version=3)
        if item['component']=='mlflow':
            item.update(usage_state='observed',usage_reason=None,used_bytes='100',regular_file_count=2,
                        directory_count=1,usage_started_at='2026-09-20T00:00:20Z',usage_observed_at=NOW)
        elif item['component']=='notebook-lab':
            item.update(usage_state='observed',usage_reason=None,used_bytes=row['used_bytes'],
                        regular_file_count=row['regular_file_count'],directory_count=row['directory_count'],
                        usage_started_at=row['usage_started_at'],usage_observed_at=row['usage_observed_at'])
        rows.append(item)
    return R.H.validate(dict(schema_version=3,components=rows,limits=R.H.MLFLOW_LIMITS[:]))


class RuntimeTests(unittest.TestCase):
    def test_component_lists_refuse_unreturned_items_and_pagination_aliases(self):
        for kind in ('PodList', 'NodeList'):
            valid = {'kind': kind, 'apiVersion': 'v1', 'items': [], 'metadata': {}}
            for key, value in (('remainingItemCount', 1), ('remainingItemCount', True),
                               ('remainingItemCount', '0'), ('Continue', 'next'),
                               ('RemainingItemCount', 1)):
                reply = deepcopy(valid)
                reply['metadata'][key] = value
                with self.subTest(kind=kind, key=key, value=value):
                    self.assertFalse(R._complete_list(reply, kind))
            valid['metadata']['remainingItemCount'] = 0
            self.assertTrue(R._complete_list(valid, kind))

    def test_component_lists_refuse_conflicting_top_level_aliases(self):
        for kind in ('PodList', 'NodeList'):
            valid = {'kind': kind, 'apiVersion': 'v1', 'items': [], 'metadata': {}}
            for key, value in (('Items', [{'kind': 'Pod'}]),
                               ('Metadata', {'continue': 'next'}),
                               ('Kind', 'OtherList'), ('ApiVersion', 'v2')):
                reply = deepcopy(valid)
                reply[key] = value
                with self.subTest(kind=kind, key=key):
                    self.assertFalse(R._complete_list(reply, kind))
            self.assertTrue(R._complete_list(valid, kind))

    def test_component_lists_refuse_unexpected_top_level_fields(self):
        for kind in ('PodList', 'NodeList'):
            valid = {'kind': kind, 'apiVersion': 'v1', 'items': [], 'metadata': {}}
            for extra in ({'status': {'reason': 'partial'}}, {'itemsExtra': [1]}):
                with self.subTest(kind=kind, extra=extra):
                    self.assertFalse(R._complete_list({**valid, **extra}, kind))
            self.assertTrue(R._complete_list(valid, kind))

    def test_component_lists_refuse_unexpected_metadata_fields(self):
        for kind in ('PodList', 'NodeList'):
            valid = {'kind': kind, 'apiVersion': 'v1', 'items': [], 'metadata': {}}
            for field in ('pagination', 'ResourceVersion', 'selfLink', 'resourceVersion'):
                reply = deepcopy(valid)
                reply['metadata'][field] = {'continue': 'next'}
                with self.subTest(kind=kind, field=field):
                    self.assertFalse(R._complete_list(reply, kind))
            valid['metadata'].update(selfLink='', resourceVersion='42', remainingItemCount=0)
            valid['metadata']['continue'] = ''
            self.assertTrue(R._complete_list(valid, kind))

    def backend(self):
        value = Mock()
        value.now.return_value = NOW
        value.component.side_effect = component_args
        value.disk.return_value = (1000, 260)
        value.inference.return_value = [row(known='0')]
        value.local_processes.return_value = {'schema_version': 1, 'components': [
            dict(component=name, state='stopped', reason='STOPPED', request_started_at=None,
                 observed_at=None, identity=None, sample=None) for name in R.P.COMPONENTS]}
        value.storage.return_value = storage_complete()
        value.docker_storage.return_value = docker_storage_complete()
        value.host_storage.return_value = mlflow_usage_complete()
        return value

    def test_complete_collection_with_unknown_recorded_cost_and_no_lifecycle(self):
        backend = self.backend()
        with patch.object(R.AH, 'ensure_runtime', side_effect=AssertionError('no lifecycle')):
            value = R.collect(backend)
        self.assertEqual(value['result'], 'complete')
        self.assertIsNone(value['inference']['groups'][0]['inference_cost_estimate']['total'])
        backend.inference.assert_called_once_with(NOW)
        self.assertEqual([c.args[0] for c in backend.component.call_args_list], list(R.C.COMPONENTS))

    def test_failures_are_complete_public_partial_without_private_exception_text(self):
        backend = self.backend()
        backend.component.side_effect = RuntimeError('RAW_PRIVATE_SECRET')
        backend.disk.side_effect = OSError('PRIVATE_PATH')
        backend.inference.side_effect = RuntimeError('CREDENTIAL')
        value = R.collect(backend)
        self.assertEqual(value['result'], 'partial'); self.assertEqual(R.C.validate_public(value), value)
        raw = json.dumps(value)
        for private in ('RAW_PRIVATE_SECRET', 'PRIVATE_PATH', 'CREDENTIAL'): self.assertNotIn(private, raw)
        self.assertTrue(all(x['sample'] is None for x in value['components']))

    def test_unconfirmed_child_cleanup_stops_further_collection(self):
        backend = self.backend(); backend.component.side_effect = R.U.PodUsageCleanupError()
        value = R.collect(backend)
        self.assertEqual(value['result'], 'partial'); self.assertEqual(backend.component.call_count, 1)
        backend.disk.assert_not_called(); backend.inference.assert_not_called()
        self.assertEqual(value['inference']['reason'], 'CLEANUP_UNVERIFIED')

    def test_original_keyboard_interrupt_is_not_converted_to_unknown(self):
        backend = self.backend(); error = KeyboardInterrupt(); backend.component.side_effect = error
        with self.assertRaises(KeyboardInterrupt) as got: R.collect(backend)
        self.assertIs(got.exception, error); backend.disk.assert_not_called(); backend.inference.assert_not_called()

    def test_vm_cleanup_unknown_prevents_agenthouse_collection(self):
        backend = self.backend(); backend.disk.side_effect = [(1000, 300), R.U.PodUsageCleanupError()]
        value = R.collect(backend)
        self.assertEqual(value['disks'][1]['reason'], 'CLEANUP_UNVERIFIED')
        self.assertEqual(value['inference']['reason'], 'CLEANUP_UNVERIFIED'); backend.inference.assert_not_called()

    def test_standalone_main_emits_one_public_line_and_partial_exit(self):
        backend = self.backend(); backend.inference.side_effect = RuntimeError('not running')
        with patch.object(R, 'Backend', return_value=backend), patch('sys.stdout', new_callable=io.StringIO) as stream:
            self.assertEqual(R.main([]), 1)
        lines = stream.getvalue().splitlines(); self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])['result'], 'partial')

    def test_fixed_sql_preserves_uint128_token_totals_and_no_write_or_raw_fields(self):
        sql = R.inference_sql(NOW)
        self.assertIn("repo = 'Shirokuma'", sql); self.assertIn('agenthouse.pawprints FINAL', sql)
        self.assertIn("2026-09-19 00:00:21.000", sql); self.assertIn("2026-09-20 00:00:21.000", sql)
        self.assertIn('sum(toUInt128(ifNull(prompt_tokens, 0)))', sql)
        self.assertIn('LIMIT 51', sql)
        for disallowed in ('INSERT', 'CREATE', 'DELETE', 'task_id', 'trace_id', 'SELECT *'):
            self.assertNotIn(disallowed, sql)

    def test_df_uses_available_bytes_not_used_or_display_percent(self):
        value = 'Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/vda1 1000 700 250 74% /\n'
        self.assertEqual(R.parse_disk(value), (1024000, 256000))
        for bad in (value + value, value.replace('250', '-1'), value.replace('1000', '0')):
            with self.assertRaises(R.C.FinOpsError): R.parse_disk(bad)

    def test_df_rejects_misidentified_or_inconsistent_capacity(self):
        value = 'Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/vda1 1000 700 250 74% /\n'
        for bad in (value.replace('1024-blocks', '512-blocks'),
                    value.replace('700 250', '800 250'),
                    value.replace('74%', 'unknown'),
                    value.replace('74%', '101%'),
                    value.replace('74%', '0%'),
                    value.replace('74%', '75%'),
                    value.replace(' /\n', ' relative\n'),
                    value.replace('/dev/vda1', '')):
            with self.subTest(bad=bad), self.assertRaises(R.C.FinOpsError):
                R.parse_disk(bad)

    def test_df_capacity_uses_ceiling_of_used_over_non_reserved_blocks(self):
        header = 'Filesystem 1024-blocks Used Available Capacity Mounted on\n'
        for row in ('/dev/vda1 1000 0 1000 0% /\n',
                    '/dev/vda1 1000 1 999 1% /\n',
                    '/dev/vda1 1000 700 0 100% /\n',
                    '/dev/vda1 1000 700 250 74% /\n'):
            with self.subTest(row=row):
                self.assertEqual(R.parse_disk(header + row)[0], 1024000)
        for row in ('/dev/vda1 1000 0 0 0% /\n',
                    '/dev/vda1 1000 700 250 73% /\n'):
            with self.subTest(row=row), self.assertRaisesRegex(R.C.FinOpsError, 'DISK_INVALID'):
                R.parse_disk(header + row)

    def test_df_mount_must_contain_fixed_storage_path(self):
        value = 'Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/vda1 1000 700 250 74% /\n'
        self.assertEqual(R.parse_disk(value.replace(' /\n', ' /var/lib/rancher/k3s\n')), (1024000, 256000))
        for mount in ('/unrelated', '/var/lib/rancher/k3s/storage-other',
                      '/var/lib/rancher/../unrelated', '//var/lib/rancher/k3s'):
            with self.subTest(mount=mount), self.assertRaisesRegex(R.C.FinOpsError, 'DISK_INVALID'):
                R.parse_disk(value.replace(' /\n', f' {mount}\n'))

    def test_df_overlong_numeric_field_has_scoped_failure(self):
        value = 'Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/vda1 1000 700 250 74% /\n'
        for field in ('1000', '700', '250', '74%'):
            bad = value.replace(' ' + field + ' ', ' ' + '9' * 5000 + ('%' if field == '74%' else '') + ' ')
            with self.subTest(field=field), self.assertRaises(R.C.FinOpsError):
                R.parse_disk(bad)

    def test_df_unencodable_text_has_scoped_failure(self):
        value = 'Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/vda1 1000 700 250 74% /\n'
        for bad in (value.replace('/dev/vda1', '/dev/\ud800'),
                    value.replace('74%', '\ud800%')):
            with self.subTest(bad=repr(bad)), self.assertRaisesRegex(R.C.FinOpsError, 'DISK_INVALID'):
                R.parse_disk(bad)

    def test_df_refuses_control_characters_in_filesystem_identity(self):
        value = 'Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/vda1 1000 700 250 74% /\n'
        for device in ('/dev/vda1\x00shadow', '/dev/vda1\x1b[31m', '/dev/vda1\x7f'):
            with self.subTest(device=repr(device)), self.assertRaisesRegex(R.C.FinOpsError, 'DISK_INVALID'):
                R.parse_disk(value.replace('/dev/vda1', device))
        self.assertEqual(R.parse_disk(value), (1024000, 256000))

    def test_df_refuses_unicode_format_controls_in_filesystem_identity(self):
        value = 'Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/vda1 1000 700 250 74% /\n'
        for device in ('/dev/vda1\u202e', '/dev/vda1\u200b'):
            with self.subTest(device=repr(device)), self.assertRaisesRegex(R.C.FinOpsError, 'DISK_INVALID'):
                R.parse_disk(value.replace('/dev/vda1', device))

    def test_vm_disk_refuses_wrong_architecture_before_df(self):
        backend = R.Backend()
        with patch.object(backend, '_command', return_value='x86_64\n') as command, \
                self.assertRaisesRegex(R.C.FinOpsError, 'SCOPE_INVALID'):
            backend.disk('colima')
        command.assert_called_once_with(['colima', 'ssh', '--profile', 'mac-studio-solo', '--', 'uname', '-m'])

    def test_vm_disk_reads_only_fixed_path_on_arm64(self):
        backend = R.Backend()
        df = 'Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/vda1 1000 700 250 74% /\n'
        with patch.object(backend, '_command', side_effect=['aarch64\n', df]) as command:
            self.assertEqual(backend.disk('colima'), (1024000, 256000))
        self.assertEqual([call.args[0] for call in command.call_args_list], [
            ['colima', 'ssh', '--profile', 'mac-studio-solo', '--', 'uname', '-m'],
            ['colima', 'ssh', '--profile', 'mac-studio-solo', '--', 'df', '-Pk', R.VM_PATH]])

    def test_component_transport_fixed_namespace_and_before_metrics_after(self):
        backend = R.Backend(); backend.server = 'https://127.0.0.1:6443'
        values = [{'kind':'PodList','apiVersion':'v1','metadata':{},'items':[pod('seaweedfs')]},
                  metrics('seaweedfs'), pod('seaweedfs')]
        with patch.object(backend, '_read', side_effect=values) as read, patch.object(backend, 'now', return_value=NOW):
            result = backend.component('seaweedfs')
        paths = [call.args[0] for call in read.call_args_list]
        self.assertTrue(all('/namespaces/shirokuma-storage/' in path for path in paths))
        self.assertIn('limit=2', paths[0]); self.assertIn('/apis/metrics.k8s.io/', paths[1])
        self.assertEqual(result['before'], result['after'])

    def test_ambiguous_or_incomplete_list_never_fetches_metrics(self):
        for metadata, items in (({}, [pod(), pod()]), ({'continue':'more'}, [pod()]), ({}, [])):
            backend = R.Backend(); backend.server = 'https://127.0.0.1:6443'
            with patch.object(backend, '_read', return_value={'kind':'PodList','apiVersion':'v1',
                    'metadata':metadata,'items':items}) as read, self.assertRaises(R.C.FinOpsError):
                backend.component('trino')
            self.assertEqual(read.call_count, 1)

    def test_malformed_pod_list_page_never_fetches_metrics(self):
        for metadata in (None, [], {'continue': None}, {'continue': False},
                         {'continue': 0}, {'continue': []}, {'continue': 'next'}):
            with self.subTest(metadata=metadata):
                backend = R.Backend(); backend.server = 'https://127.0.0.1:6443'
                with patch.object(backend, '_read', return_value={'kind': 'PodList', 'apiVersion': 'v1',
                        'metadata': metadata, 'items': [pod('trino')]}) as read, \
                        self.assertRaises(R.C.FinOpsError):
                    backend.component('trino')
                self.assertEqual(read.call_count, 1)
        backend = R.Backend(); backend.server = 'https://127.0.0.1:6443'
        with patch.object(backend, '_read', side_effect=[{'kind': 'PodList', 'apiVersion': 'v1',
                'metadata': {'continue': ''}, 'items': [pod('trino')]}, metrics('trino'), pod('trino')]) as read:
            backend.component('trino')
        self.assertEqual(read.call_count, 3)

    def test_polaris_selector_keeps_bootstrap_out_without_client_side_filtering(self):
        server = pod('polaris'); bootstrap = deepcopy(server)
        bootstrap['metadata']['name'] = 'polaris-bootstrap-abc12'
        bootstrap['metadata']['labels']['app.kubernetes.io/component'] = 'catalog-bootstrap'
        bootstrap['status']['phase'] = 'Succeeded'
        bootstrap['spec']['containers'][0]['name'] = 'polaris-admin'
        def read(path, deadline):
            if '?labelSelector=' in path:
                labels = dict(item.split('=', 1) for item in parse_qs(urlsplit(path).query)['labelSelector'][0].split(','))
                self.assertEqual(labels, {'app.kubernetes.io/name': 'polaris',
                                          'app.kubernetes.io/component': 'catalog-server'})
                return {'kind':'PodList','apiVersion':'v1','metadata':{},
                        'items':[item for item in (server, bootstrap) if all(
                            item['metadata']['labels'].get(key) == value for key, value in labels.items())]}
            return metrics('polaris') if '/apis/metrics.k8s.io/' in path else server
        backend = R.Backend(); backend.server = 'https://127.0.0.1:6443'
        with patch.object(backend, '_read', side_effect=read) as fetch, patch.object(backend, 'now', return_value=NOW):
            result = R.C.project_component('polaris', **backend.component('polaris'))
        self.assertEqual(result['state'], 'observed'); self.assertEqual(fetch.call_count, 3)
        self.assertEqual(result['identity']['pod_name'], server['metadata']['name'])

    def test_two_polaris_catalog_servers_remain_ambiguous_before_metrics(self):
        second = pod('polaris'); second['metadata']['name'] = 'polaris-second'
        backend = R.Backend(); backend.server = 'https://127.0.0.1:6443'
        with patch.object(backend, '_read', return_value={'kind':'PodList','apiVersion':'v1',
                'metadata':{},'items':[pod('polaris'), second]}) as read, \
                self.assertRaisesRegex(R.C.FinOpsError, 'TARGET_AMBIGUOUS'):
            backend.component('polaris')
        self.assertEqual(read.call_count, 1)

    def test_foreign_node_resets_server_and_refuses_pod_read(self):
        backend = R.Backend()
        with patch.object(R.U, '_local_server', return_value='https://127.0.0.1:6443'), \
                patch.object(backend, '_read', return_value={'kind':'NodeList','apiVersion':'v1','metadata':{},
                    'items':[{'status':{'nodeInfo':{'architecture':'amd64','kubeletVersion':'k3s'}}}]}) as read, \
                self.assertRaises(R.C.FinOpsError):
            backend.component('trino')
        self.assertIsNone(backend.server); self.assertEqual(read.call_count, 1)

    def test_malformed_node_identity_has_scoped_failure_before_pod_read(self):
        for item in ({}, {'status': None}, {'status': {}},
                     {'status': {'nodeInfo': None}},
                     {'status': {'nodeInfo': {'architecture': 'arm64', 'kubeletVersion': None}}},
                     {'status': {'nodeInfo': {'architecture': 'arm64', 'kubeletVersion': []}}}):
            backend = R.Backend()
            nodes = {'kind': 'NodeList', 'apiVersion': 'v1', 'metadata': {}, 'items': [item]}
            with self.subTest(item=item), patch.object(R.U, '_local_server', return_value='https://127.0.0.1:6443'), \
                    patch.object(backend, '_read', return_value=nodes) as read, \
                    self.assertRaisesRegex(R.C.FinOpsError, 'SCOPE_INVALID'):
                backend.component('trino')
            self.assertIsNone(backend.server)
            self.assertEqual(read.call_count, 1)

    def test_malformed_node_page_refuses_pod_read_and_clears_server(self):
        for metadata in (None, [], {'continue': None}, {'continue': False},
                         {'continue': 0}, {'continue': 'next'}):
            with self.subTest(metadata=metadata):
                backend = R.Backend()
                nodes = {'kind': 'NodeList', 'apiVersion': 'v1', 'metadata': metadata,
                         'items': [{'status': {'nodeInfo': {'architecture': 'arm64', 'kubeletVersion': 'k3s'}}}]}
                with patch.object(R.U, '_local_server', return_value='https://127.0.0.1:6443'), \
                        patch.object(backend, '_read', return_value=nodes) as read, \
                        self.assertRaises(R.C.FinOpsError):
                    backend.component('trino')
                self.assertIsNone(backend.server)
                self.assertEqual(read.call_count, 1)
        backend = R.Backend()
        nodes = {'kind': 'NodeList', 'apiVersion': 'v1', 'metadata': {'continue': ''},
                 'items': [{'status': {'nodeInfo': {'architecture': 'arm64', 'kubeletVersion': 'k3s'}}}]}
        with patch.object(R.U, '_local_server', return_value='https://127.0.0.1:6443'), \
                patch.object(backend, '_read', return_value=nodes) as read:
            backend._guard(100)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(backend.server, 'https://127.0.0.1:6443')

    def test_agenthouse_fixed_read_reuses_client_without_starting_and_rejects_foreign_port(self):
        context = [{'Endpoints':{'docker':{'Host':'unix:///Users/test/.colima/mac-studio-solo/docker.sock'}}}]
        owned = [{'Config':{'Labels':{R.AH.LABEL:'agenthouse-local'},'Image':R.AH.IMAGE},
                  'State':{'Running':True},'NetworkSettings':{'Ports':{'8123/tcp':[
                      {'HostIp':'127.0.0.1','HostPort':'18123'}]}}}]
        with tempfile.TemporaryDirectory() as temp:
            state=Path(temp); state.chmod(0o700)
            credential=state/'credential.json'; credential.write_text('{"password":"FAKE"}'); credential.chmod(0o600)
            backend=R.Backend(); client=Mock(); client.query.return_value=json.dumps(row())+'\n'
            with patch.object(R.AH,'STATE',state), patch.object(R.AH,'Client',return_value=client), \
                    patch.object(R.AH,'ensure_runtime',side_effect=AssertionError('no start')), \
                    patch.object(backend,'_command',side_effect=[json.dumps(context),json.dumps(owned)]):
                self.assertEqual(backend.inference(NOW), [row()])
            client.query.assert_called_once_with(R.inference_sql(NOW))
            owned[0]['NetworkSettings']['Ports']['8123/tcp'][0]['HostIp']='0.0.0.0'
            with patch.object(backend,'_command',side_effect=[json.dumps(context),json.dumps(owned)]), \
                    patch.object(R.AH,'Client') as factory, self.assertRaises(R.C.FinOpsError):
                backend.inference(NOW)
            factory.assert_not_called()

    def test_fixed_command_enforces_live_limit_kills_only_owned_group_and_reaps(self):
        backend = R.Backend()
        process = Mock(pid=1234, returncode=None); process.stdout.fileno.return_value = 10
        process.wait.return_value = 0
        selector = Mock(); selector.select.return_value = [(1, 1)]
        with patch.object(R.subprocess, 'Popen', return_value=process) as launch, \
                patch.object(R.selectors, 'DefaultSelector', return_value=selector), \
                patch.object(R.os, 'set_blocking'), patch.object(R.os, 'read', return_value=b'x'*5), \
                patch.object(R.os, 'killpg') as kill, patch.object(R, 'MAX_RESPONSE', 4), \
                self.assertRaisesRegex(R.C.FinOpsError, 'READ_LIMIT'):
            backend._command(['docker', '--context', R.CONTEXT, 'info'])
        self.assertTrue(launch.call_args.kwargs['start_new_session'])
        kill.assert_called_once_with(1234, R.signal.SIGKILL)
        process.wait.assert_called_once_with(timeout=2); selector.close.assert_called_once(); process.stdout.close.assert_called_once()

    def test_command_timeout_cleanup_failure_and_first_cancel_remain_distinct(self):
        for mode in ('timeout', 'cleanup', 'cancel'):
            process = Mock(pid=1234, returncode=None); process.stdout.fileno.return_value = 10
            error = KeyboardInterrupt()
            process.wait.side_effect = OSError('not reaped') if mode in ('cleanup', 'cancel') else None
            selector=Mock(); selector.select.return_value=[]
            if mode == 'cancel': selector.select.side_effect=error
            expected = KeyboardInterrupt if mode == 'cancel' else R.U.PodUsageCleanupError if mode == 'cleanup' else R.C.FinOpsError
            with self.subTest(mode=mode), patch.object(R.subprocess,'Popen',return_value=process), \
                    patch.object(R.selectors,'DefaultSelector',return_value=selector), patch.object(R.os,'set_blocking'), \
                    patch.object(R.os,'killpg'), self.assertRaises(expected) as got:
                R.Backend()._command(['docker','--context',R.CONTEXT,'info'])
            if mode == 'cancel': self.assertIs(got.exception,error)
            if mode == 'timeout': self.assertEqual(str(got.exception),'READ_TIMEOUT')

    def test_exited_leader_with_inherited_stdout_is_signalled_before_reap(self):
        process = Mock(pid=1234, returncode=None); process.stdout.fileno.return_value = 10
        # The leader has exited, but Popen has not reaped it. Its descendant
        # keeps stdout open without producing bytes, so the read times out.
        order = []
        def poll():
            process.returncode = 0
            return 0
        def kill(pid, sig):
            self.assertIsNone(process.returncode)
            order.append(('kill', pid, sig))
        def wait(**kwargs):
            self.assertEqual(order, [('kill', 1234, R.signal.SIGKILL)])
            order.append(('wait', kwargs['timeout']))
            process.returncode = 0
            return 0
        process.poll.side_effect = poll; process.wait.side_effect = wait
        selector = Mock(); selector.select.return_value = []
        with patch.object(R.subprocess, 'Popen', return_value=process), \
                patch.object(R.selectors, 'DefaultSelector', return_value=selector), \
                patch.object(R.os, 'set_blocking'), patch.object(R.os, 'killpg', side_effect=kill), \
                self.assertRaisesRegex(R.C.FinOpsError, 'READ_TIMEOUT'):
            R.Backend()._command(['colima', 'ssh', '--profile', 'mac-studio-solo', '--', 'df', '-Pk', R.VM_PATH])
        process.poll.assert_not_called()
        self.assertEqual(order, [('kill', 1234, R.signal.SIGKILL), ('wait', 2)])
        selector.close.assert_called_once(); process.stdout.close.assert_called_once()

    def test_successfully_reaped_command_is_not_signalled(self):
        process = Mock(pid=1234, returncode=None); process.stdout.fileno.return_value = 10
        def wait(**kwargs):
            process.returncode = 0
            return 0
        process.wait.side_effect = wait
        selector = Mock(); selector.select.return_value = [True]
        with patch.object(R.subprocess, 'Popen', return_value=process), \
                patch.object(R.selectors, 'DefaultSelector', return_value=selector), \
                patch.object(R.os, 'set_blocking'), patch.object(R.os, 'read', side_effect=[b'safe', b'']), \
                patch.object(R.os, 'killpg') as kill:
            self.assertEqual(R.Backend()._command(['docker', 'context', 'inspect', R.CONTEXT]), 'safe')
        kill.assert_not_called(); process.poll.assert_not_called()
        selector.close.assert_called_once(); process.stdout.close.assert_called_once()

    def test_successful_command_with_invalid_utf8_is_scoped_failure(self):
        process = Mock(pid=1234, returncode=None); process.stdout.fileno.return_value = 10
        def wait(**kwargs):
            process.returncode = 0
            return 0
        process.wait.side_effect = wait
        selector = Mock(); selector.select.return_value = [True]
        with patch.object(R.subprocess, 'Popen', return_value=process), \
                patch.object(R.selectors, 'DefaultSelector', return_value=selector), \
                patch.object(R.os, 'set_blocking'), patch.object(R.os, 'read', side_effect=[b'\xff', b'']), \
                patch.object(R.os, 'killpg') as kill, \
                self.assertRaisesRegex(R.C.FinOpsError, 'READ_FAILED'):
            R.Backend()._command(['colima', 'ssh', '--profile', 'mac-studio-solo', '--', 'uname', '-m'])
        kill.assert_not_called()
        selector.close.assert_called_once(); process.stdout.close.assert_called_once()


if __name__ == '__main__': unittest.main()
