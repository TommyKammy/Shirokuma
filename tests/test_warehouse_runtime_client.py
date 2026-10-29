from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import warehouse_runtime_client as client


class ClientTests(unittest.TestCase):
    def test_http_intents_preserve_key_and_revision(self):
        with patch.object(client,'Client') as constructor:
            client.main(['autochill','--minutes','0','--expected-revision','2','--key','disable','--role','admin'],profile=object())
            constructor.return_value.request.assert_called_once_with('POST',client.BASE+'/auto-chill',body={'minutes':0,'expected_revision':2},key='disable')

    def test_invalid_policy_never_dispatches(self):
        with patch.object(client,'Client') as constructor:
            with self.assertRaises(ValueError): client.main(['autochill','--minutes','1441','--expected-revision','0','--key','x'],profile=object())
            constructor.assert_not_called()

    def test_real_http_client_uses_saved_auth_and_policy_cas_without_backend_access(self):
        import http.server
        import json
        import threading
        from types import SimpleNamespace
        from test_request_auth import PrincipalTests
        from workbench_server import WorkbenchHandler
        import warehouse_runtime_routes as runtime
        import warehouse_resident as resident
        fixture = PrincipalTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        auth = fixture.root / 'client.json'
        auth.write_text(json.dumps(dict(username='owner', password='correct', selected_role='admin')))
        auth.chmod(0o600)
        profile = SimpleNamespace(client_auth_file=str(auth))
        with patch('warehouse_lifecycle.applicable', return_value=True), \
             patch.object(resident, '_logical_kubectl', side_effect=AssertionError('HTTP client must not touch compute')):
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), WorkbenchHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = 'http://127.0.0.1:' + str(server.server_port)
                args = ['autochill', '--api-url', url, '--minutes', '0', '--expected-revision', '0', '--key', 'disable']
                ack = client.main(args, profile=profile)
                self.assertEqual(client.main(args, profile=profile), ack)
                detail = client.main(['operation', '--api-url', url, '--id', ack['operation_id']], profile=profile)
                self.assertEqual(detail['phase'], 'SUCCEEDED')
                self.assertEqual(detail['action'], 'WAREHOUSE_AUTO_CHILL')
                self.assertEqual(runtime.policy(), dict(minutes=0, revision=1))
                from dataops_client import ClientError
                with self.assertRaisesRegex(ClientError, 'REVISION_CONFLICT'):
                    client.main(args[:-1] + ['new-key'], profile=profile)
                fixture.users['owner']['platform_admin'] = False
                fixture.save()
                with self.assertRaisesRegex(ClientError, 'FORBIDDEN'):
                    client.main(['autochill', '--api-url', url, '--minutes', '0', '--expected-revision', '1', '--key', 'revoked'], profile=profile)
                self.assertEqual(runtime.policy(), dict(minutes=0, revision=1))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == '__main__': unittest.main()
