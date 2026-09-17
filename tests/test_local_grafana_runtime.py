"""Offline ownership, retained-state and read-only enforcement tests."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('tested_grafana_runtime', ROOT / 'tools/local_experiment/grafana/manage.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def credential():
    return {'schema_version': 1, 'owner_token': 'a' * 32, 'reader_user': 'shirokuma_grafana_ro_' + 'a' * 32,
            'admin_password': 'b' * 64, 'secret_key': 'c' * 64, 'reader_password': 'd' * 64}


def container():
    return {'Id': 'grafana-id', 'Config': {'Image': M.IMAGE, 'User': '472', 'Labels': {M.LABEL: M.OWNER},
              'Env': [key + '=' + value for key, value in M.environment(credential()).items()]},
            'HostConfig': {'PortBindings': {'3000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18030'}]},
                'NanoCpus': 10**9, 'Memory': 1024**3, 'PidsLimit': 256, 'ReadonlyRootfs': True,
                'Privileged': False, 'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges']},
            'Mounts': [{'Type': 'volume', 'Name': M.VOLUME, 'Destination': '/var/lib/grafana', 'RW': True}] + [
                {'Type': 'bind', 'Source': str(source), 'Destination': destination, 'RW': False}
                for source, destination in [(M.STATE / 'plugins', '/opt/grafana/plugins'),
                    (M.HERE / 'provisioning', '/etc/grafana/provisioning'),
                    (M.HERE / 'dashboards', '/etc/grafana/shirokuma-dashboards')]],
            'NetworkSettings': {'Networks': {M.NETWORK: {'NetworkID': 'network-id'}, M.FRONTEND_NETWORK: {'NetworkID': 'frontend-id'}},
                'Ports': {'3000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18030'}]}}, 'State': {'Running': True}}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.patch = patch.object(M, 'STATE', self.state)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_private_credentials_are_stable_and_not_regenerated_for_retained_volume(self):
        with patch.object(M, 'inspect', return_value=None):
            first = M.credentials(create=True)
            self.assertEqual(first, M.credentials(create=True))
        self.assertEqual(stat.S_IMODE((self.state / 'credentials.json').stat().st_mode), 0o600)
        (self.state / 'credentials.json').unlink()
        with patch.object(M, 'inspect', side_effect=[None, {'Name': M.VOLUME}]), self.assertRaises(M.GrafanaError):
            M.credentials(create=True)
        self.assertFalse((self.state / 'credentials.json').exists())

    def test_credentials_reject_symlink_world_readable_and_wrong_schema(self):
        target = self.state / 'target.json'
        M.private_write(target, credential())
        path = self.state / 'credentials.json'
        path.symlink_to(target)
        with self.assertRaises(M.GrafanaError):
            M.credentials()
        path.unlink()
        M.private_write(path, credential())
        path.chmod(0o644)
        with self.assertRaises(M.GrafanaError):
            M.credentials()
        M.private_write(path, {**credential(), 'schema_version': True})
        with self.assertRaises(M.GrafanaError):
            M.credentials()

    def test_container_drift_rejected_before_stop(self):
        valid = container()
        M.validate_container(valid, credential())
        changes = [lambda v: v['Mounts'].append({'Type': 'bind', 'Source': '/', 'Destination': '/host', 'RW': True}),
                   lambda v: v['Mounts'][1].update(RW=True),
                   lambda v: v['HostConfig'].update(NanoCpus=2 * 10**9),
                   lambda v: v['HostConfig'].update(Memory=2 * 1024**3),
                   lambda v: v['HostConfig'].update(CapAdd=['SYS_ADMIN']),
                   lambda v: v['HostConfig'].update(PortBindings={'3000/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '18030'}]}),
                   lambda v: v['Config']['Env'].append('HTTP_PROXY=http://example.invalid'),
                   lambda v: v['NetworkSettings']['Networks'].update(bridge={}),
                   lambda v: v['Config'].update(Image='grafana/grafana:latest')]
        for change in changes:
            with self.subTest(change=change):
                bad = copy.deepcopy(valid)
                change(bad)
                with patch.object(M, 'credentials', return_value=credential()), patch.object(M, 'inspect', return_value=bad), \
                        patch.object(M, 'docker') as docker, self.assertRaises(M.GrafanaError):
                    M.stop()
                docker.assert_not_called()

    def test_reader_xml_contains_only_owned_reader_and_select_grant(self):
        value = M.reader_xml(credential())
        self.assertNotIn(credential()['reader_password'], value)
        root = ET.fromstring(value)
        self.assertEqual([node.tag for node in root.find('users')], [credential()['reader_user']])
        self.assertEqual([node.text for node in root.findall('./users/*/grants/query')], ['GRANT SELECT ON agenthouse.pawprints'])
        self.assertIsNone(root.find('./users/*/access_management'))
        constraints = root.find('./profiles/*/constraints')
        self.assertIsNotNone(constraints.find('readonly/readonly'))
        self.assertEqual(constraints.findtext('max_execution_time/max'), '30')
        self.assertIsNotNone(constraints.find('max_execution_time/changeable_in_readonly'))

    def test_conflicting_reader_config_is_never_overwritten_or_reloaded(self):
        with patch.object(M, 'docker', return_value='<different/>') as docker, patch.object(M.AH, 'Client') as client, \
                self.assertRaises(M.GrafanaError):
            M.ensure_reader(credential(), {'Id': 'retained-ch'})
        self.assertEqual(docker.call_count, 1)
        client.assert_not_called()

    def test_reader_creation_is_exclusive_and_journal_precedes_write(self):
        def docker(*args, **kwargs):
            if args[-2:] == ('cat', M.CH_CONFIG):
                return None
            receipt = M.private_read(self.state / 'reader-config-receipt.json')
            self.assertEqual(receipt['outcome'], 'pending')
            self.assertIn('ln "$f" ' + M.CH_CONFIG, args[-1])
            self.assertNotIn('ln -f', args[-1])
            self.assertEqual(kwargs['input_text'], M.reader_xml(credential()))
            return ''
        with patch.object(M, 'docker', side_effect=docker), patch.object(M.AH, 'Client') as client, \
                patch.object(M, 'verify_reader', return_value={'select_only': True}):
            M.ensure_reader(credential(), {'Id': 'retained-ch'})
        client.return_value.query.assert_called_once_with('SYSTEM RELOAD CONFIG')
        self.assertEqual(M.private_read(self.state / 'reader-config-receipt.json')['outcome'], 'verified')

    def reader_mocks(self, rejection=None, extra_grant=False):
        user = credential()['reader_user']
        grants = [{'access_type': 'SELECT', 'database': 'agenthouse', 'table': 'pawprints', 'column': None,
                   'is_partial_revoke': 0, 'grant_option': 0}]
        if extra_grant:
            grants.append({**grants[0], 'access_type': 'INSERT'})
        admin = Mock()
        admin.query.side_effect = lambda sql: json.dumps({'data': [] if 'role_grants' in sql else grants})
        def query(_, sql):
            if sql == 'SELECT currentUser()':
                return user
            if sql == 'SELECT currentUser(), 1':
                return user + '\t1\n'
            if 'FROM system.settings' in sql:
                return json.dumps({'data': [{'name': k, 'value': v} for k, v in M.SETTINGS.items()]})
            if sql == 'SELECT 1 SETTINGS max_execution_time=5':
                return '1'
            if sql in ('SELECT 1 SETTINGS readonly=0', 'SELECT 1 SETTINGS max_execution_time=31', 'SELECT 1 SETTINGS max_result_rows=0'):
                if rejection is not None:
                    raise rejection
                raise M.QueryRejected(500, 452)
            if sql.startswith('CHECK GRANT '):
                return '0'
            if sql == 'SELECT count() FROM agenthouse.pawprints FINAL':
                return '294'
            raise AssertionError('unexpected query')
        return admin, query

    def test_permissions_require_exact_grant_bounded_settings_and_positive_reconfirmation(self):
        admin, query = self.reader_mocks()
        with patch.object(M.AH, 'Client', return_value=admin), patch.object(M, 'reader_query', side_effect=query) as reader:
            result = M.verify_reader(credential())
        self.assertTrue(result['select_only'])
        self.assertEqual(result['logical_pawprints'], 294)
        self.assertEqual(reader.call_args.args[1], 'SELECT currentUser(), 1')
        admin, query = self.reader_mocks(extra_grant=True)
        with patch.object(M.AH, 'Client', return_value=admin), patch.object(M, 'reader_query', side_effect=query), \
                self.assertRaises(M.GrafanaError):
            M.verify_reader(credential())

    def test_auth_syntax_timeout_and_generic_http_errors_do_not_pass_denial(self):
        failures = [M.GrafanaError('transport'), M.QueryRejected(401, 516), M.QueryRejected(500, 62),
                    M.QueryRejected(500, 159), M.QueryRejected(200, 452)]
        for failure in failures:
            with self.subTest(code=getattr(failure, 'code', None)):
                admin, query = self.reader_mocks(rejection=failure)
                with patch.object(M.AH, 'Client', return_value=admin), patch.object(M, 'reader_query', side_effect=query), \
                        self.assertRaises(M.GrafanaError):
                    M.verify_reader(credential())

    def test_http_error_retains_numeric_code_only_and_refuses_other_hosts(self):
        secret = 'private-query-and-password'
        error = urllib.error.HTTPError(M.AH.URL, 500, secret, {'X-ClickHouse-Exception-Code': '452'}, io.BytesIO(secret.encode()))
        opener = Mock()
        opener.open.side_effect = error
        with patch.object(M.urllib.request, 'build_opener', return_value=opener), self.assertRaises(M.QueryRejected) as caught:
            M.reader_query(credential(), 'SELECT 1 SETTINGS max_execution_time=31')
        self.assertEqual((caught.exception.status, caught.exception.code), (500, 452))
        self.assertNotIn(secret, str(caught.exception))
        with patch.object(M.urllib.request, 'build_opener') as build, self.assertRaises(M.GrafanaError):
            M.request('http://127.0.0.1:18123.example.invalid/')
        build.assert_not_called()

    def network(self, endpoints=None):
        return {'Id': 'network-id', 'Driver': 'bridge', 'Internal': True, 'Options': {},
                'Containers': {} if endpoints is None else endpoints}

    def test_network_connection_is_journaled_and_failed_attachment_remains_pending(self):
        current = {'Id': 'retained-ch', 'NetworkSettings': {'Networks': {'bridge': {}}}}
        def docker(*args, **kwargs):
            self.assertEqual(M.private_read(self.state / 'network-receipt.json')['outcome'], 'pending')
            raise M.GrafanaError('attachment failed')
        with patch.object(M, 'inspect', side_effect=[self.network(), None]), patch.object(M, 'docker', side_effect=docker), \
                self.assertRaises(M.GrafanaError):
            M.ensure_network(current)
        receipt = M.private_read(self.state / 'network-receipt.json')
        self.assertEqual(receipt['prior_agenthouse_networks'], ['bridge'])
        self.assertEqual(receipt['agenthouse_id'], 'retained-ch')
        self.assertEqual(receipt['outcome'], 'pending')

    def test_network_does_not_adopt_unrecorded_attachment_or_unrelated_endpoint(self):
        current = {'Id': 'retained-ch', 'NetworkSettings': {'Networks': {'bridge': {}}}}
        for endpoints in ({'retained-ch': {}}, {'unrelated': {}}):
            with patch.object(M, 'inspect', side_effect=[self.network(endpoints), None]), patch.object(M, 'docker') as docker, \
                    self.assertRaises(M.GrafanaError):
                M.ensure_network(current)
            docker.assert_not_called()

    def test_disconnect_only_removes_journaled_network_attachments_and_retains_volume(self):
        receipt = {'network_id': 'network-id', 'agenthouse_id': 'retained-ch', 'attached_by_grafana': True}
        M.private_write(self.state / 'network-receipt.json', receipt)
        self.frontend_receipt()
        own = container()
        after_backend = copy.deepcopy(own)
        after_backend['NetworkSettings']['Networks'].pop(M.NETWORK)
        after_both = copy.deepcopy(own)
        after_both['NetworkSettings']['Networks'] = {}
        current = {'Id': 'retained-ch', 'NetworkSettings': {'Networks': {'bridge': {}, M.NETWORK: {'NetworkID': 'network-id'}}}}
        current_after = {'Id': 'retained-ch', 'NetworkSettings': {'Networks': {'bridge': {}}}}
        with patch.object(M, 'credentials', return_value=credential()), \
                patch.object(M, 'inspect', side_effect=[own, self.network({'retained-ch': {}}), after_backend,
                    self.frontend(), after_both]), \
                patch.object(M, 'agenthouse', side_effect=[current, current_after]), patch.object(M, 'docker') as docker:
            result = M.stop(disconnect=True)
        self.assertEqual([c.args for c in docker.call_args_list], [('stop', '--time', '30', 'grafana-id'),
            ('network', 'disconnect', '--force', 'network-id', 'grafana-id'), ('network', 'disconnect', 'network-id', 'retained-ch'),
            ('network', 'disconnect', '--force', 'frontend-id', 'grafana-id')])
        self.assertTrue(result['volume_retained'])
        self.assertTrue(result['reader_config_retained'])

    def test_disconnect_refuses_changed_retained_identity(self):
        M.private_write(self.state / 'network-receipt.json', {'network_id': 'network-id', 'agenthouse_id': 'old-ch', 'attached_by_grafana': True})
        with patch.object(M, 'credentials', return_value=credential()), patch.object(M, 'inspect', side_effect=[None, self.network()]), \
                patch.object(M, 'agenthouse', return_value={'Id': 'new-ch'}), patch.object(M, 'docker') as docker, self.assertRaises(M.GrafanaError):
            M.stop(disconnect=True)
        docker.assert_not_called()

    def frontend(self, endpoints=None):
        return {'Id': 'frontend-id', 'Driver': 'bridge', 'Internal': False, 'Options': {},
                'Containers': {} if endpoints is None else endpoints}

    def frontend_receipt(self, *, grafana_id='grafana-id', network_id='frontend-id'):
        value = {'network_id': network_id, 'owner_token': credential()['owner_token'],
                 'grafana_id': grafana_id, 'attached_by_grafana': True, 'outcome': 'connected'}
        M.private_write(self.state / 'frontend-network-receipt.json', value)
        return value

    def test_frontend_rejects_clickhouse_or_other_container_attachment(self):
        for identity in ('retained-ch', 'unrelated-container'):
            with patch.object(M, 'inspect', return_value=self.frontend({identity: {}})), \
                    patch.object(M, 'docker') as docker, self.assertRaises(M.GrafanaError):
                M.ensure_frontend(credential(), container())
            docker.assert_not_called()

    def test_frontend_existing_attachment_cannot_be_adopted_without_receipt(self):
        with patch.object(M, 'inspect', return_value=self.frontend({'grafana-id': {}})), \
                patch.object(M, 'docker') as docker, self.assertRaises(M.GrafanaError):
            M.ensure_frontend(credential(), container())
        docker.assert_not_called()

    def test_frontend_failed_connect_is_journaled_and_only_targets_grafana(self):
        own = container()
        own['NetworkSettings']['Networks'] = {M.NETWORK: {}}
        with patch.object(M, 'inspect', return_value=self.frontend()):
            network = M.ensure_frontend(credential(), own)
        def connect(*args, **kwargs):
            self.assertEqual(args, ('network', 'connect', 'frontend-id', 'grafana-id'))
            receipt = M.private_read(self.state / 'frontend-network-receipt.json')
            self.assertEqual((receipt['outcome'], receipt['grafana_id']), ('pending', 'grafana-id'))
            raise M.GrafanaError('failed')
        with patch.object(M, 'docker', side_effect=connect), self.assertRaises(M.GrafanaError):
            M.attach_frontend(network, own, credential())
        self.assertEqual(M.private_read(self.state / 'frontend-network-receipt.json')['outcome'], 'pending')

    def test_up_reuses_existing_container_volume_credentials_and_adds_only_frontend(self):
        own = container()
        own['NetworkSettings']['Networks'] = {M.NETWORK: {}}
        self.frontend_receipt()
        with patch.object(M, 'prepare'), patch.object(M, 'credentials', return_value=credential()), \
                patch.object(M, 'agenthouse', return_value={'Id': 'retained-ch'}), \
                patch.object(M, 'inspect', return_value=own), patch.object(M, 'ensure_network', return_value=self.network()), \
                patch.object(M, 'ensure_frontend', return_value=self.frontend()), patch.object(M, 'ensure_reader', return_value={}), \
                patch.object(M, 'docker') as docker, patch.object(M, 'status', return_value={'ready': True}):
            self.assertTrue(M.up()['ready'])
        self.assertEqual([c.args for c in docker.call_args_list], [('network', 'connect', 'frontend-id', 'grafana-id')])

    def test_disconnect_frontend_never_removes_clickhouse_network_or_retained_resources(self):
        self.frontend_receipt()
        own = container()
        own['NetworkSettings']['Networks'].pop(M.NETWORK)
        after = copy.deepcopy(own)
        after['NetworkSettings']['Networks'] = {}
        with patch.object(M, 'credentials', return_value=credential()), \
                patch.object(M, 'inspect', side_effect=[own, None, self.frontend({'grafana-id': {}}), after]), \
                patch.object(M, 'docker') as docker:
            result = M.stop(disconnect=True)
        self.assertEqual([c.args for c in docker.call_args_list], [('stop', '--time', '30', 'grafana-id'),
                       ('network', 'disconnect', '--force', 'frontend-id', 'grafana-id')])
        self.assertTrue(result['volume_retained'])
        self.assertEqual(M.private_read(self.state / 'frontend-network-receipt.json')['outcome'], 'disconnected')

    def test_disconnect_frontend_refuses_changed_network_or_grafana_identity(self):
        own = container()
        own['NetworkSettings']['Networks'].pop(M.NETWORK)
        for kwargs in ({'network_id': 'replaced-network'}, {'grafana_id': 'old-grafana'}):
            self.frontend_receipt(**kwargs)
            with patch.object(M, 'credentials', return_value=credential()), \
                    patch.object(M, 'inspect', side_effect=[own, None, self.frontend({'grafana-id': {}})]), \
                    patch.object(M, 'docker') as docker, self.assertRaises(M.GrafanaError):
                M.stop(disconnect=True)
            self.assertFalse(any(c.args[:2] == ('network', 'disconnect') for c in docker.call_args_list))

    def test_stopped_endpoint_omitted_by_network_inspect_is_still_removed_and_verified(self):
        own = container()
        own['State']['Running'] = False
        own['NetworkSettings']['Networks'].pop(M.NETWORK)
        after = copy.deepcopy(own)
        after['NetworkSettings']['Networks'] = {}
        self.frontend_receipt()
        with patch.object(M, 'credentials', return_value=credential()), \
                patch.object(M, 'inspect', side_effect=[own, None, self.frontend(), after]) as inspect, \
                patch.object(M, 'docker') as docker:
            result = M.stop(disconnect=True)
        docker.assert_called_once_with('network', 'disconnect', '--force', 'frontend-id', 'grafana-id')
        self.assertEqual(inspect.call_args.args, ('container', 'grafana-id'))
        self.assertTrue(result['network_disconnected'])

    def test_successful_docker_exit_does_not_claim_disconnect_if_attachment_remains(self):
        own = container()
        own['State']['Running'] = False
        own['NetworkSettings']['Networks'].pop(M.NETWORK)
        self.frontend_receipt()
        with patch.object(M, 'credentials', return_value=credential()), \
                patch.object(M, 'inspect', side_effect=[own, None, self.frontend(), own]), \
                patch.object(M, 'docker', return_value=''), self.assertRaises(M.GrafanaError):
            M.stop(disconnect=True)
        self.assertEqual(M.private_read(self.state / 'frontend-network-receipt.json')['outcome'], 'disconnecting')

    def test_ready_requires_effective_loopback_publish_and_both_networks(self):
        for change in (lambda own: own['NetworkSettings'].update(Ports={}),
                       lambda own: own['NetworkSettings'].update(Networks={M.NETWORK: {}})):
            own = container()
            change(own)
            with patch.object(M, 'credentials', return_value=credential()), patch.object(M, 'inspect', return_value=own), \
                    patch.object(M, 'api') as api, self.assertRaises(M.GrafanaError):
                M.status()
            api.assert_not_called()

    def test_runtime_never_accepts_unsigned_plugin_or_writer_datasource(self):
        health = {'database': 'ok', 'version': M.PINS['grafana']['version']}
        for plugin in ({'signature': 'unsigned', 'info': {'version': M.PINS['plugin']['version']}},
                       {'signature': 'valid', 'info': {'version': '4.0.0'}}):
            with patch.object(M, 'credentials', return_value=credential()), patch.object(M, 'inspect', return_value=container()), \
                    patch.object(M, 'api', side_effect=[health, plugin]), self.assertRaises(M.GrafanaError):
                M.status()
        plugin = {'signature': 'valid', 'info': {'version': M.PINS['plugin']['version']}}
        datasource = {'type': M.PINS['plugin']['id'], 'readOnly': True, 'jsonData': {'username': 'shirokuma_agenthouse',
                      'host': M.AH.NAME, 'protocol': 'http', 'port': 8123}, 'secureJsonFields': {'password': True}}
        with patch.object(M, 'credentials', return_value=credential()), patch.object(M, 'inspect', return_value=container()), \
                patch.object(M, 'api', side_effect=[health, plugin, datasource]), self.assertRaises(M.GrafanaError):
            M.status()

    def test_archive_hash_mismatch_rejects_before_extract(self):
        with self.assertRaises(M.GrafanaError):
            M.validate_archive(b'not-the-official-archive')

    def test_provisioned_timeout_accounts_for_pinned_driver_five_second_margin(self):
        provisioning = (M.HERE / 'provisioning/datasources/agenthouse.yaml').read_text()
        timeout = int(re.search(r'^\s+queryTimeout: ([0-9]+)$', provisioning, re.MULTILINE).group(1))
        self.assertEqual(timeout, M.PLUGIN_QUERY_TIMEOUT)
        # clickhouse-go v2.48.0 context.go queryOptions(): int(seconds + 5).
        # Both initial HTTP hello and subsequent queries must fit the DB cap.
        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout + 5, int(M.SETTINGS['max_execution_time']))

    def archive_fixture(self, *, extra_path=None, tamper=False, machine=183):
        pin = copy.deepcopy(M.PINS)
        plugin = pin['plugin']
        binary = b'\x7fELF\x02\x01' + b'\0' * 12 + machine.to_bytes(2, 'little')
        files = {'plugin.json': json.dumps({'id': plugin['id'], 'info': {'version': plugin['version']},
                  'backend': True, 'executable': 'gpx_clickhouse'}).encode(), 'gpx_clickhouse_linux_arm64': binary}
        manifest = {'plugin': plugin['id'], 'version': plugin['version'], 'signedByOrg': 'grafana',
                    'signatureType': 'grafana', 'files': {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}}
        files['MANIFEST.txt'] = ('-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA256\n\n' + json.dumps(manifest)
                                + '\n-----BEGIN PGP SIGNATURE-----\nfixture-only\n').encode()
        if tamper:
            files['gpx_clickhouse_linux_arm64'] += b'tamper'
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            for name, content in files.items():
                archive.writestr(plugin['id'] + '/' + name, content)
            if extra_path:
                archive.writestr(extra_path, b'escape')
        raw = data.getvalue()
        plugin.update(archive_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        return pin, raw

    def test_locked_archive_still_requires_confined_paths_manifest_hashes_and_arm64(self):
        for options in ({'extra_path': M.PINS['plugin']['id'] + '/../escape'}, {'tamper': True}, {'machine': 62}):
            with self.subTest(options=options):
                pins, raw = self.archive_fixture(**options)
                with patch.object(M, 'PINS', pins), self.assertRaises(M.GrafanaError):
                    M.validate_archive(raw)
        pins, raw = self.archive_fixture()
        with patch.object(M, 'PINS', pins):
            self.assertEqual(len(M.validate_archive(raw)), 3)
        # This verifies structural pin checking only; Grafana's valid signature
        # response is independently mandatory in the runtime readiness gate.


if __name__ == '__main__':
    unittest.main()
