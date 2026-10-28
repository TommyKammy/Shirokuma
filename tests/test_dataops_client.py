"""HTTP-only CLI, frozen selection, private credentials and atomic export."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import dataops_client as cli
import lake_ingest
import runtime_profile


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.auth = self.root / 'client.json'
        self.auth.write_text(json.dumps(dict(username='alice', password='secret', selected_role='data_engineer')))
        self.auth.chmod(0o600)
        self.profile = SimpleNamespace(client_auth_file=str(self.auth))

    def test_local_only_private_auth_no_proxy_redirect(self):
        for url in ('http://example.com', 'http://127.0.0.1.evil', 'http://u:p@127.0.0.1', 'http://127.0.0.1/path', 'https://127.0.0.1'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                cli.Client(self.profile, url)
        client = cli.Client(self.profile)
        self.assertEqual(client.role, 'data_engineer')
        self.assertEqual(client.headers['X-Shirokuma-Role'], 'data_engineer')
        self.assertTrue(any(isinstance(h, cli.NoRedirect) for h in client.opener.handlers))
        self.assertFalse(any(getattr(h, 'proxies', {}) for h in client.opener.handlers))
        self.auth.chmod(0o644)
        with self.assertRaises(ValueError):
            cli.Client(self.profile)

    def test_capture_selection_once_and_use_for_followups(self):
        client = cli.Client(self.profile)
        database = dict(id='db', sql_catalog='sales', binding_revision=7, generation_id='gen', grant_revision=9)
        schema = dict(id='schema', sql_name='raw')
        with patch.object(client, 'request', side_effect=[dict(items=[database]), dict(items=[schema]), {}]) as request:
            context = client.select('sales', 'raw')
            database['generation_id'] = 'changed'
            client.get('/api/v1/dataops/op')
        self.assertEqual(context['generation_id'], 'gen')
        self.assertEqual(request.call_count, 3)
        self.assertEqual(json.loads(request.call_args.kwargs['params']['context']), context)

    def test_cli_create_only_http_bytes_and_explicit_context(self):
        path = self.root / 'rows.csv'
        path.write_text('id\n001\n')
        client = Mock()
        client.context = dict(database_id='db', schema_id='sc')
        client.post.return_value = dict(context=client.context, operation=dict(state='FINISHED'))
        with patch.object(cli, 'Client', return_value=client):
            result = cli.main(['--database', 'sales', '--schema', 'raw', '--table', 'x', '--mode', 'create',
                '--idempotency-key', 'once', '--', str(path)], profile=self.profile)
        self.assertEqual(result['action'], 'ingest')
        client.select.assert_called_once_with('sales', 'raw')
        client.ref.assert_not_called()
        body = client.post.call_args.args[1]
        self.assertEqual(body['destination'], dict(database_id='db', schema_id='sc', physical_name='x'))
        self.assertNotIn('file', body)
        self.assertEqual(body['filename'], 'rows.csv')

    def test_main_dispatch_does_not_configure_backend_or_execute_adapter(self):
        with patch.object(cli, 'configured_profile', return_value=self.profile), patch.object(cli, 'main', return_value={'action': 'ingest'}) as main, \
                patch.object(runtime_profile, 'configure_process', side_effect=AssertionError('backend credentials')), \
                patch.object(lake_ingest, 'ingest', side_effect=AssertionError('direct adapter')), patch('builtins.print'):
            self.assertEqual(lake_ingest.main(), 0)
        main.assert_called_once()

    def test_profile_reads_only_profile_and_client_reference(self):
        profile = self.root / 'profile.json'
        private_json = cli.runtime_profile.private_json
        for mode in ('database', 'logical_v1'):
            profile.write_text(json.dumps(dict(mode=mode, client_auth_file=str(self.auth),
                trino_auth_file='/never/read', private_config='/never/private')))
            profile.chmod(0o600)
            with self.subTest(mode=mode), patch.dict('os.environ', SHIROKUMA_RUNTIME_PROFILE=str(profile)), \
                    patch.object(cli.runtime_profile, 'private_json', wraps=private_json) as read:
                resolved = cli.configured_profile()
                self.assertEqual(vars(resolved), dict(client_auth_file=str(self.auth)))
                read.assert_called_once_with(profile)
                self.assertEqual(cli.Client(resolved).role, 'data_engineer')
                self.assertEqual(read.call_args_list, [unittest.mock.call(profile), unittest.mock.call(str(self.auth))])

    def test_profile_rejects_unknown_mode_and_invalid_client_reference(self):
        for value in (dict(mode='unknown', client_auth_file=str(self.auth)),
                      dict(mode='logical_v1'), dict(mode='logical_v1', client_auth_file=7)):
            with self.subTest(value=value), patch.object(cli.runtime_profile, 'private_json', return_value=value), \
                    patch.object(cli.runtime_profile, 'load', side_effect=AssertionError('backend profile')), \
                    self.assertRaises(ValueError):
                cli.configured_profile()

    def test_atomic_export_and_integrity_failure_preserve_existing_file(self):
        output = self.root / 'out.csv'
        output.write_bytes(b'original')
        raw = b'id\n001\n'
        receipt = dict(byte_count=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        with self.assertRaises(ValueError):
            cli._write_export(output, raw + b'bad', receipt)
        self.assertEqual(output.read_bytes(), b'original')
        cli._write_export(output, raw, receipt)
        self.assertEqual(output.read_bytes(), raw)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['client.json', 'out.csv'])

    def test_export_failed_content_never_touches_output(self):
        output = self.root / 'out.csv'
        output.write_bytes(b'original')
        client = Mock()
        client.context = dict(database_id='db', schema_id='sc')
        client.post.return_value = dict(operation=dict(state='FINISHED', result=dict(export_id='export')))
        client.get.return_value = dict(receipt={})
        client.request.side_effect = cli.ClientError('DB_ACCESS_REVOKED')
        with patch.object(cli, 'Client', return_value=client), self.assertRaisesRegex(ValueError, 'REVOKED'):
            cli.main(['--database', 'sales', '--schema', 'raw', '--export', 'orders', '--output', str(output), '--idempotency-key', 'once'], profile=self.profile)
        self.assertEqual(output.read_bytes(), b'original')

    def test_finished_export_followup_failures_preserve_stdout_recovery_and_old_file(self):
        raw = b'id\n001\n'
        receipt = dict(byte_count=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        for failure, expected in (('receipt', 'DB_ACCESS_REVOKED'), ('content', 'DB_ACCESS_REVOKED'),
                                  ('integrity', 'EXPORT_INTEGRITY_FAILED'), ('write', 'EXPORT_OUTPUT_WRITE_FAILED')):
            with self.subTest(failure=failure):
                output = self.root / 'out.csv'
                output.write_bytes(b'original')
                client = Mock()
                client.context = self.context()
                client.post.return_value = dict(context=client.context,
                    operation=dict(id='accepted-export-op', state='FINISHED', result=dict(export_id='export')))
                client.get.return_value = dict(receipt=receipt)
                client.request.return_value = raw + b'corrupt' if failure == 'integrity' else raw
                if failure == 'receipt':
                    client.get.side_effect = cli.ClientError('DB_ACCESS_REVOKED')
                if failure == 'content':
                    client.request.side_effect = cli.ClientError('DB_ACCESS_REVOKED')
                argv = ['lake_ingest.py', '--database', 'sales', '--schema', 'raw', '--export',
                        'orders', '--output', str(output), '--idempotency-key', 'once']
                # fsync fails after a real temporary file was created and written.
                fsync = patch.object(cli.os, 'fsync', side_effect=OSError('private path and OS details')) if failure == 'write' else patch.object(cli.os, 'fsync', wraps=cli.os.fsync)
                with patch.object(cli, 'Client', return_value=client), \
                        patch.object(cli, 'configured_profile', return_value=self.profile), fsync, \
                        patch.object(sys, 'argv', argv), \
                        patch.object(sys, 'stdout', new_callable=io.StringIO) as stdout, \
                        patch.object(sys, 'stderr', new_callable=io.StringIO) as stderr:
                    self.assertEqual(lake_ingest.main(), 1)
                recovery = json.loads(stdout.getvalue())
                self.assertEqual(recovery['action'], 'export')
                self.assertEqual(recovery['context'], self.context())
                self.assertEqual(recovery['operation'], client.post.return_value['operation'])
                self.assertEqual(recovery['error']['code'], expected)
                self.assertEqual(stderr.getvalue().strip(), expected)
                self.assertEqual(output.read_bytes(), b'original')
                self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['client.json', 'out.csv'])

    def test_cancel_uses_original_context_without_inventory(self):
        context = self.context()
        client = Mock(role='admin', headers={})
        saved = self.root / 'operation.json'
        saved.write_text(json.dumps(dict(context=context, operation=dict(id='op', state='ACTIVE'))))
        with patch.object(cli, 'Client', return_value=client):
            cli.main(['--operation', 'cancel', '--id', 'op', '--context-file', str(saved)], profile=self.profile)
        client.select.assert_not_called()
        self.assertEqual(client.context, context)
        self.assertEqual(client.headers['X-Shirokuma-Role'], 'data_engineer')
        client.post.assert_called_once_with('/api/v1/dataops/op/cancel', {})

    @staticmethod
    def context():
        return dict(database_id='db', schema_id='sc', warehouse_id='LOCAL', sql_catalog='sales',
            sql_schema='raw', selected_role='data_engineer', binding_revision=1,
            generation_id='g1', grant_revision=2)

    def test_saved_context_rejects_missing_extra_and_wrong_revision_before_http(self):
        for change in ({'extra': 1}, {'binding_revision': True}, {'grant_revision': '2'}, {'selected_role': 'root'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                cli.saved_context(dict(self.context(), **change))
        context = self.context()
        del context['generation_id']
        with self.assertRaises(ValueError):
            cli.saved_context(context)

    def test_failed_and_unknown_accepted_stdout_retains_recovery_before_exit(self):
        for state in ('FAILED', 'UNKNOWN'):
            reply = dict(context=self.context(), operation=dict(id='op', state=state))
            with self.subTest(state=state), patch.object(cli, 'configured_profile', return_value=self.profile), \
                    patch.object(cli, 'main', side_effect=cli.ClientError('OPERATION_' + state, reply)), \
                    patch.object(sys, 'argv', ['lake_ingest.py', '--wap', 'build']), \
                    patch.object(sys, 'stdout', new_callable=io.StringIO) as output, \
                    patch.object(sys, 'stderr', new_callable=io.StringIO):
                self.assertEqual(lake_ingest.main(), 1)
                record = json.loads(output.getvalue())
                self.assertEqual(record['action'], 'wap-build')
                self.assertEqual(record['operation'], reply['operation'])
                self.assertEqual(record['context'], reply['context'])

    def test_unknown_export_does_not_fetch_or_create_output(self):
        output = self.root / 'never.csv'
        client = Mock()
        client.post.return_value = dict(context=self.context(), operation=dict(id='op', state='UNKNOWN'))
        with patch.object(cli, 'Client', return_value=client), self.assertRaises(cli.ClientError) as caught:
            cli.main(['--database', 'sales', '--schema', 'raw', '--export', 'x', '--output', str(output), '--idempotency-key', 'once'], profile=self.profile)
        self.assertEqual(caught.exception.reply['operation']['id'], 'op')
        self.assertFalse(output.exists())
        client.get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
