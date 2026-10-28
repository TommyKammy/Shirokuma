import base64
import copy
import json
import tempfile
import unittest
import argparse
import io
import sys
import types
from pathlib import Path
from unittest.mock import patch
from botocore.exceptions import ClientError, EndpointConnectionError

from tools.local_experiment import database_poc as poc
from tools.local_experiment import database_poc_s3 as s3


def credentials():
    return {d: {'accessKey': d + '-key', 'secretKey': d + '-secret'} for d in s3.DATABASES}


def server(identities=None):
    return {'metadata': {'resourceVersion': '42', 'labels': {'keep': 'yes'}},
            'data': {'unrelated': 'a2VlcA==', 's3.json': base64.b64encode(json.dumps({
                'identities': identities or [], 'other': {'keep': True}}).encode()).decode()}}


class IdentityTests(unittest.TestCase):
    def test_policy(self):
        for db, entry in zip(s3.DATABASES, s3.expected_identities(credentials())):
            self.assertEqual(entry['name'], 'database-poc-' + db + '-s3')
            self.assertEqual(entry['credentials'], [credentials()[db]])
            self.assertEqual(entry['actions'], [a + ':shirokuma-lakehouse/database-poc/' + db + '/*'
                                               for a in ('Read', 'Write', 'Tagging', 'List')])

    def test_preservation_idempotency_revoke(self):
        other = {'name': 'unrelated', 'credentials': [{'accessKey': 'other'}], 'actions': ['Admin']}
        original = server([other]); before = copy.deepcopy(original)
        added, changed = s3.update_secret(original, credentials())
        self.assertTrue(changed); self.assertEqual(original, before)
        self.assertEqual(added['metadata'], before['metadata'])
        self.assertEqual(added['data']['unrelated'], before['data']['unrelated'])
        self.assertEqual(json.loads(base64.b64decode(added['data']['s3.json']))['other'], {'keep': True})
        self.assertEqual(s3.update_secret(added, credentials()), (added, False))
        removed, changed = s3.update_secret(added, credentials(), True)
        self.assertTrue(changed)
        self.assertEqual(json.loads(base64.b64decode(removed['data']['s3.json']))['identities'], [other])
        self.assertEqual(s3.update_secret(removed, credentials(), True), (removed, False))

    def test_collisions(self):
        expected = s3.expected_identities(credentials())
        bad = copy.deepcopy(expected[0]); bad['actions'] = ['Admin']
        for identities in ([bad], [expected[0], expected[0]],
                           [{'name': 'other', 'credentials': [credentials()['sales']]}]):
            for revoke in (False, True):
                with self.assertRaises(poc.Failure):
                    s3.update_secret(server(identities), credentials(), revoke)

    def test_validation(self):
        bad = credentials(); bad['finance']['accessKey'] = bad['sales']['accessKey']
        for value in ({}, {'sales': credentials()['sales']}, bad,
                      dict(credentials(), finance={'accessKey': 'key', 'secretKey': ''})):
            with self.assertRaises(poc.Failure): s3.validate_credentials(value)

    def test_private_state_partial_not_regenerated(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(poc, 'STATE', Path(tmp)):
            Path(tmp).chmod(0o700)
            poc.save('s3-principals.json', {'sales': {}})
            before = (Path(tmp) / 's3-principals.json').read_bytes()
            with patch.object(poc, 'run') as run:
                with self.assertRaises(poc.Failure): s3.install_or_revoke()
                run.assert_not_called()
            self.assertEqual((Path(tmp) / 's3-principals.json').read_bytes(), before)
            (Path(tmp) / 's3-principals.json').chmod(0o644)
            with self.assertRaises(poc.Failure): s3.load_credentials()

    def test_helper_env(self):
        resources = poc.manifests({d: {'clientId': 'id', 'clientSecret': 'secret'} for d in s3.DATABASES},
                                 'realm', {}, credentials())
        env = resources[3]['spec']['template']['spec']['containers'][0]['env']
        names = {e['name'] for e in env}
        self.assertNotIn('AWS_ACCESS_KEY_ID', names); self.assertNotIn('AWS_SECRET_ACCESS_KEY', names)
        for db in s3.DATABASES:
            for suffix in ('ACCESS_KEY_ID', 'SECRET_ACCESS_KEY'):
                key = db.upper() + '_S3_' + suffix
                self.assertIn('${ENV:' + key + '}', poc.config_data()[db + '.properties'])
                self.assertIn({'name': key, 'valueFrom': {'secretKeyRef': {'name': poc.NAME, 'key': key}}}, env)

    def test_install_replace_stdin_and_reuse(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(poc, 'STATE', Path(tmp)):
            Path(tmp).chmod(0o700)
            with patch.object(poc, 'run', return_value=json.dumps(server()).encode()) as run, patch('sys.stdout', new=io.StringIO()):
                s3.install_or_revoke()
                saved = s3.load_credentials()
                self.assertEqual((Path(tmp) / 's3-principals.json').stat().st_mode & 0o777, 0o600)
                self.assertEqual(run.call_args_list[0].args[0], ['kubectl', '--context', poc.CONTEXT,
                    '-n', 'shirokuma-storage', 'get', 'secret', s3.SERVER_SECRET, '-o', 'json'])
                args, stdin = run.call_args_list[1].args
                self.assertEqual(args[-3:], ['replace', '-f', '-'])
                self.assertEqual(json.loads(stdin)['metadata']['resourceVersion'], '42')
                existing, _ = s3.update_secret(server(), saved)
                run.reset_mock(); run.return_value = json.dumps(existing).encode()
                s3.install_or_revoke()
                self.assertEqual(run.call_count, 1)
                self.assertEqual(s3.load_credentials(), saved)
                s3.install_or_revoke(True)
                self.assertEqual(s3.load_credentials(), saved)


class VerifyBoundaryTests(unittest.TestCase):
    def test_endpoint_bounds(self):
        for value in ('http://127.0.0.1:18333', 'https://localhost:443', 'http://[::1]:8333'):
            self.assertEqual(s3.endpoint(value), value)
        for value in ('http://example.com:8333', 'http://localhost', 'http://localhost:0',
                      'http://a:b@localhost:8333', 'http://localhost:8333/',
                      'http://localhost:8333?x', 'http://localhost:8333#x',
                      'http://localhost:8333?', 'ftp://localhost:8333', 'http://localhost:65536'):
            with self.assertRaises(argparse.ArgumentTypeError): s3.endpoint(value)

    def test_classification(self):
        def error(http, code):
            return ClientError({'ResponseMetadata': {'HTTPStatusCode': http}, 'Error': {'Code': code}}, 'test')
        self.assertEqual(s3.classify(error=error(403, 'AccessDenied'), deny=True), ('PASS', 403, 'AccessDenied'))
        self.assertEqual(s3.classify(error=error(403, '403'), deny=True, head=True)[0], 'PASS')
        self.assertEqual(s3.classify(error=error(403, ''), deny=True, head=True)[0], 'PASS')
        for http, code in ((404, 'NoSuchKey'), (500, 'InternalError'), (403, 'SignatureDoesNotMatch'),
                           (403, 'InvalidAccessKeyId'), (200, ''), (403, '403')):
            self.assertEqual(s3.classify(error=error(http, code), deny=True)[0], 'FAIL')
        self.assertEqual(s3.classify(error=EndpointConnectionError(endpoint_url='hidden'), deny=True)[0], 'UNKNOWN')
        self.assertEqual(s3.classify({'ResponseMetadata': {'HTTPStatusCode': 200}})[0], 'PASS')

    def test_mixed_errors(self):
        response = {'ResponseMetadata': {'HTTPStatusCode': 200}, 'Deleted': [{'Key': 'own'}],
                    'Errors': [{'Key': 'foreign', 'Code': 'AccessDenied'}]}
        self.assertTrue(s3.mixed_delete_ok(response, 'own', 'foreign'))
        for bad in ({'Deleted': [{'Key': 'own'}, {'Key': 'foreign'}]},
                    {'Errors': [{'Key': 'foreign', 'Code': 'InternalError'}]},
                    {'Deleted': [{'Key': 'own'}]},
                    dict(response, Deleted=[{'Key': 'foreign'}])):
            self.assertFalse(s3.mixed_delete_ok(bad, 'own', 'foreign'))

    def test_sdk_configuration(self):
        with patch('boto3.client') as client:
            s3.sdk_clients(credentials(), 'http://localhost:18333', 'test-region')
        self.assertEqual(client.call_count, 2)
        for db, call in zip(s3.DATABASES, client.call_args_list):
            kw = call.kwargs; config = kw['config']
            self.assertEqual(call.args, ('s3',))
            self.assertEqual(kw['aws_access_key_id'], credentials()[db]['accessKey'])
            self.assertEqual(kw['aws_secret_access_key'], credentials()[db]['secretKey'])
            self.assertIsNone(kw['aws_session_token'])
            self.assertEqual(config.signature_version, 's3v4')
            self.assertEqual(config.s3, {'addressing_style': 'path'})
            self.assertEqual(config.retries, {'max_attempts': 0})
            self.assertEqual((config.connect_timeout, config.read_timeout), (3, 5))
            self.assertEqual(config.request_checksum_calculation, 'when_required')
            self.assertEqual(config.response_checksum_validation, 'when_required')

    def test_import_does_not_require_sdk(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('offline_s3', s3.__file__)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'boto3': None, 'botocore': None}), patch.object(poc, 'run') as run:
            spec.loader.exec_module(module)
            module.expected_identities(credentials())
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
