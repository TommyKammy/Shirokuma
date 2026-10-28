"""Inventory transport with temp configuration and mocked network boundaries."""
import copy
import json
import unittest
from unittest.mock import patch, MagicMock

import test_logical_access as access
from test_logical_foundation import settings
import control_store as store
import data_management as dm
import database_inventory as inventory
import logical_foundation as foundation
import runtime_profile as rp


class LogicalInventoryTests(unittest.TestCase):
    save_accounts = access.LogicalAccessTests.save_accounts
    secret = access.LogicalAccessTests.secret

    def setUp(self):
        access.LogicalAccessTests.setUp(self)
        self.provider = settings(self.root)
        self.provider.update(foundation_id='foundation', revision=1, environment_id='env', warehouse_id='LOCAL')
        self.provider['polaris']['realm'] = 'realm'
        self.save_provider(self.provider)
        with store.connect() as db:
            db.execute("UPDATE catalog_bindings SET polaris_service_ref='owned-polaris'")
            self.database = dm.get_database(db, 'db')
            self.binding = dm.get_binding(db, 'db')
        self.secret('catalog-db', dict(client_id='db-consumer', client_secret='consumer-secret'))

    def save_provider(self, config):
        from pathlib import Path
        Path(self.config['provider_config_file']).write_text(json.dumps(config))
        rp.configure_process()

    def transport(self):
        socket_mock = MagicMock()
        socket_mock.return_value.__enter__.return_value.getsockname.return_value = ('127.0.0.1', 32123)
        child = MagicMock()
        child.poll.return_value = None
        return socket_mock, child

    def test_logical_enrolled_transport_consumer_and_cleanup(self):
        sock, child = self.transport()
        with patch.object(inventory.socket, 'socket', sock), \
                patch.object(inventory.socket, 'create_connection') as connect, \
                patch.object(inventory.subprocess, 'Popen', return_value=child) as popen, \
                patch.object(inventory, '_http', side_effect=[{'access_token': 'consumer-token'}, {'namespaces': []}]) as http, \
                patch.object(rp, 'validate_catalog_manifest', side_effect=AssertionError('legacy credentials')):
            with inventory.catalog_api(self.database, self.binding) as api:
                self.assertEqual(api('/namespaces'), {'namespaces': []})
            expected = foundation.kube_argv(self.provider) + [
                'port-forward', '--address', '127.0.0.1', 'svc/owned-polaris', '32123:8181']
            self.assertEqual(popen.call_args.args[0], expected)
            self.assertNotIn('shirokuma-dev', expected)
            connect.assert_called_once_with(('127.0.0.1', 32123), timeout=.2)
            sock.return_value.__enter__.return_value.bind.assert_called_once_with(('127.0.0.1', 0))
            import base64
            self.assertEqual(http.call_args_list[0].args[0].get_header('Authorization'),
                             'Basic ' + base64.b64encode(b'db-consumer:consumer-secret').decode())
            self.assertEqual(http.call_args_list[1].args[0].get_header('Authorization'), 'Bearer consumer-token')
            child.terminate.assert_called_once()
            child.wait.assert_called_once_with(timeout=5)

    def test_provider_mismatch_refuses_before_socket_or_popen(self):
        for key, value in (('foundation_id', 'other'), ('revision', 2), ('environment_id', 'other'),
                           ('warehouse_id', 'other'), ('prefix', 'other'), ('realm', 'other')):
            with self.subTest(key=key):
                config = copy.deepcopy(self.provider)
                if key == 'realm':
                    config['polaris']['realm'] = value
                elif key == 'prefix':
                    config[key] = value
                    for section, fields in (('storage', ('endpoint', 'sts_endpoint')), ('polaris', ('endpoint',))):
                        for field in fields:
                            config[section][field] = config[section][field].replace('owned-', 'other-')
                else:
                    config[key] = value
                self.save_provider(config)
                with patch.object(inventory.socket, 'socket') as sock, patch.object(inventory.subprocess, 'Popen') as popen:
                    with self.assertRaises(inventory.InventoryError) as error:
                        with inventory.catalog_api(self.database, self.binding):
                            self.fail('mismatched transport admitted')
                    self.assertEqual(error.exception.code, 'CATALOG_UNAVAILABLE')
                    sock.assert_not_called()
                    popen.assert_not_called()

    def test_legacy_exact_command_and_no_provider_load(self):
        import logical_registry
        entry = dict(service_ref='legacy-polaris', realm='legacy', catalog='db',
                     client_id='legacy-consumer', client_secret='legacy-secret')
        sock, child = self.transport()
        with patch.object(inventory, '_credentials', return_value=entry), \
                patch.object(logical_registry, 'creation_mode', return_value='legacy'), \
                patch.object(foundation, 'load', side_effect=AssertionError('logical provider')), \
                patch.object(inventory.socket, 'socket', sock), \
                patch.object(inventory.socket, 'create_connection'), \
                patch.object(inventory.subprocess, 'Popen', return_value=child) as popen, \
                patch.object(inventory, '_http', return_value={'access_token': 'legacy-token'}):
            with inventory.catalog_api(self.database, self.binding):
                pass
            self.assertEqual(popen.call_args.args[0], ['rtk', 'proxy', 'kubectl', '--context',
                'colima-mac-studio-solo', '-n', 'shirokuma-dev', 'port-forward', '--address',
                '127.0.0.1', 'svc/legacy-polaris', '32123:8181'])
            child.terminate.assert_called_once()
