from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location(
    'local_superset', Path(__file__).resolve().parents[1] / 'tools/local_experiment/superset/local_superset.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def container():
    return {'Image': 'sha256:owned', 'State': {'Running': True},
            'Config': {'Labels': {MODULE.LABEL: 'superset'},
                       'Cmd': ['gunicorn', '--bind', '127.0.0.1:18088']},
            'HostConfig': {'NetworkMode': 'host', 'Privileged': False, 'ReadonlyRootfs': True}}


class SupersetLocalTests(unittest.TestCase):
    def test_owner_label_required_before_container_or_volume_mutation(self):
        for value, kind in [({'Config': {'Labels': {}}}, 'container'), ({'Labels': {}}, 'volume')]:
            with self.assertRaisesRegex(MODULE.LocalError, 'collision'):
                MODULE.require_owned(value, kind)

    def test_existing_runtime_must_retain_loopback_listener_and_isolation(self):
        MODULE.require_local_runtime(container())
        for mode in ('public', 'privileged', 'writable', 'bridge'):
            resource = container()
            if mode == 'public':
                resource['Config']['Cmd'][-1] = '0.0.0.0:18088'
            elif mode == 'privileged':
                resource['HostConfig']['Privileged'] = True
            elif mode == 'writable':
                resource['HostConfig']['ReadonlyRootfs'] = False
            else:
                resource['HostConfig']['NetworkMode'] = 'bridge'
            with self.assertRaises(MODULE.LocalError):
                MODULE.require_local_runtime(resource)

    def test_inspect_distinguishes_missing_resource_from_daemon_failure(self):
        for error in ('Error: No such container: item', 'error: no such volume', 'image not found'):
            with patch.object(MODULE, 'docker', return_value=Mock(returncode=1, stderr=error)):
                self.assertIsNone(MODULE.inspect('volume', 'item'))
        with patch.object(MODULE, 'docker', return_value=Mock(returncode=1, stderr='connection refused')):
            with self.assertRaisesRegex(MODULE.LocalError, 'could not inspect'):
                MODULE.inspect('volume', 'item')

    def test_initialization_name_collision_is_preserved(self):
        with patch.object(MODULE, 'inspect', return_value=container()):
            with patch.object(MODULE, 'docker') as docker:
                with self.assertRaisesRegex(MODULE.LocalError, 'already exists'):
                    MODULE.initialize_container('sha256:owned', 'migration', ['superset', 'db', 'upgrade'])
        docker.assert_not_called()

    def test_timeout_removes_only_its_owned_initialization_container(self):
        with patch.object(MODULE, 'inspect', side_effect=[None, container()]):
            with patch.object(MODULE, 'docker', side_effect=[subprocess.TimeoutExpired('docker', 180), Mock()]) as docker:
                with self.assertRaises(subprocess.TimeoutExpired):
                    MODULE.initialize_container('sha256:owned', 'migration', ['superset', 'db', 'upgrade'])
        self.assertEqual(docker.call_args_list[-1].args,
                         ('rm', '--force', MODULE.NAME + '-init-migration'))

    def test_timeout_preserves_replaced_initialization_container(self):
        replacement = container()
        replacement['Image'] = 'sha256:another-image'
        with patch.object(MODULE, 'inspect', side_effect=[None, replacement]):
            with patch.object(MODULE, 'docker', side_effect=subprocess.TimeoutExpired('docker', 180)) as docker:
                with self.assertRaisesRegex(MODULE.LocalError, 'identity changed'):
                    MODULE.initialize_container('sha256:owned', 'migration', ['superset', 'db', 'upgrade'])
        self.assertEqual(docker.call_count, 1)

    def test_api_refuses_redirects_and_remote_paths(self):
        with self.assertRaisesRegex(MODULE.LocalError, 'redirect'):
            MODULE.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://remote.invalid')
        api = MODULE.SupersetAPI.__new__(MODULE.SupersetAPI)
        for path in ('https://remote.invalid/api/v1/chart/', '/outside', '/api/v1/https://other'):
            with self.assertRaisesRegex(MODULE.LocalError, 'only local'):
                api.request('GET', path)

    def test_api_failure_never_echoes_password_or_bearer_token(self):
        api = MODULE.SupersetAPI.__new__(MODULE.SupersetAPI)
        api.token = 'secret-bearer'
        api.csrf = 'secret-csrf'
        api.opener = Mock()
        api.opener.open.side_effect = MODULE.urllib.error.HTTPError(
            MODULE.URL, 401, 'secret-password', {}, io.BytesIO(b'secret-bearer'))
        with self.assertRaises(MODULE.LocalError) as caught:
            api.request('POST', '/api/v1/security/login', {'password': 'secret-password'})
        self.assertNotIn('secret-', str(caught.exception))
        self.assertIn('HTTP 401', str(caught.exception))

    def test_name_collisions_are_not_silently_overwritten(self):
        api = Mock()
        api.request.return_value = {'result': [{'name': 'owned'}, {'name': 'owned'}]}
        with self.assertRaisesRegex(MODULE.LocalError, 'ambiguous'):
            MODULE.first_named(api, 'chart', 'name', 'owned')

    def test_trino_endpoint_refuses_public_service_address(self):
        result = Mock(stdout=json.dumps({'spec': {'clusterIP': '8.8.8.8', 'ports': []}}))
        with patch.object(MODULE, 'command', return_value=result):
            with self.assertRaisesRegex(MODULE.LocalError, 'private'):
                MODULE.trino_endpoint()

    def test_private_state_is_written_with_owner_only_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'private.json'
            MODULE.save(target, {'generated': 'secret'})
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(target.read_text()), {'generated': 'secret'})


if __name__ == '__main__':
    unittest.main()
