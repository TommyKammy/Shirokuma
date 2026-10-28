"""Ordinary profile and HTTP wiring for display management."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import display_client as client


class DisplayClientTests(unittest.TestCase):
    def test_ordinary_profile_produces_authenticated_patch(self):
        import dataops_client
        response = Mock()
        reply = {'operation_id':'op', 'state':'APPLIED', 'resource_type':'database','resource_id':'db','revision':2}
        raw = json.dumps(reply).encode()
        response.read.return_value = raw
        response.headers = {'Content-Length':str(len(raw))}
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        profile = SimpleNamespace(client_auth_file='ordinary-client-auth')
        with patch.object(client, 'configured_profile', return_value=profile), \
                patch.object(dataops_client.runtime_profile, 'private_json', return_value=dict(
                    username='admin', password='synthetic', selected_role='admin')) as credentials, \
                patch.object(dataops_client, 'build_opener', return_value=opener):
            self.assertEqual(client.main(['set','--database-id','db','--display-name','Name','--description','',
                '--expected-revision','1','--idempotency-key','key']), reply)
        credentials.assert_called_once_with('ordinary-client-auth')
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'PATCH')
        self.assertEqual(request.full_url, 'http://127.0.0.1:8888/api/v1/databases/db/display')
        headers = {k.lower():v for k,v in request.header_items()}
        self.assertTrue(headers['authorization'].startswith('Basic '))
        self.assertEqual(headers['x-shirokuma-role'], 'admin')
        self.assertEqual(headers['idempotency-key'], 'key')
        self.assertEqual(json.loads(request.data), dict(display_name='Name',description='',expected_revision=1))

    def test_all_routes_and_empty_description(self):
        http = Mock()
        profile = object()
        with patch.object(client, 'configured_profile', return_value=profile), patch.object(client, 'Client', return_value=http) as factory:
            client.main(['list'])
            factory.assert_called_with(profile, 'http://127.0.0.1:8888')
            http.request.assert_called_with('GET', '/api/v1/admin/databases')
            client.main(['set','--database-id','db','--display-name',' 研究 ', '--description','',
                '--expected-revision','1','--idempotency-key','key','--api-url','http://localhost:8888'])
            factory.assert_called_with(profile, 'http://localhost:8888')
            http.request.assert_called_with('PATCH', '/api/v1/databases/db/display', key='key',
                body=dict(display_name=' 研究 ', description='', expected_revision=1))
            client.main(['schema','--schema-id','s','--display-name','Raw','--expected-revision','2','--idempotency-key','schema-key'])
            http.request.assert_called_with('PATCH', '/api/v1/schemas/s/display', key='schema-key',
                body=dict(display_name='Raw', expected_revision=2))
            client.main(['operation','--operation-id','op/slash'])
            http.request.assert_called_with('GET', '/api/v1/operations/op%2Fslash')
            client.main(['set','--database-id','db','--display-name=--研究', '--description=- details',
                '--expected-revision','2','--idempotency-key=-key'])
            http.request.assert_called_with('PATCH', '/api/v1/databases/db/display', key='-key',
                body=dict(display_name='--研究', description='- details', expected_revision=2))

    def test_missing_arguments_revision_and_profile_fail_before_http(self):
        base = ['set','--database-id','db','--display-name','Name','--description','', '--idempotency-key','key']
        with patch.object(client, 'Client') as http:
            for args in (base, base+['--expected-revision','0'], base+['--expected-revision','-1'], ['operation'], ['schema']):
                with self.assertRaises(ValueError):
                    client.main(args)
            with patch.object(client, 'configured_profile', return_value=None), self.assertRaises(ValueError):
                client.main(['list'])
            http.assert_not_called()
