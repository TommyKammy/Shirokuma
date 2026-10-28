"""HTTP serialization and private no-overwrite metadata exports."""
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import audit_client


class AuditClient(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.calls = []
        self.reply = {'event': {'id':'opaque', 'state':'UNKNOWN'}}

    def call(self, *args):
        calls, reply = self.calls, self.reply
        class Response(io.BytesIO): headers = {}
        class Opener:
            def open(self, request, **kwargs):
                calls.append(request)
                return Response(json.dumps(reply).encode())
        with ExitStack() as stack:
            stack.enter_context(patch('dataops_client.runtime_profile.private_json', return_value=
                dict(username='alice', password='secret-password', selected_role='data_engineer')))
            stack.enter_context(patch('dataops_client.build_opener', return_value=Opener()))
            stack.enter_context(patch('control_store.connect', side_effect=AssertionError('CLI store access')))
            stack.enter_context(patch('runtime_profile.current', side_effect=AssertionError('backend profile')))
            return audit_client.main(list(args), profile=SimpleNamespace(client_auth_file='client.json'))

    def test_list_show_telemetry_only_http_and_explicit_role(self):
        self.assertEqual(self.call('list', '--database=db', '--scope=database', '--role=admin', '--source=query',
                                  '--state=UNKNOWN', '--search=a%_', '--from=2026-10-29T00:00:00Z', '--cursor=signed', '--limit=3'), self.reply)
        req = self.calls[-1]
        self.assertEqual(req.method, 'GET')
        self.assertEqual(req.get_header('X-shirokuma-role'), 'admin')
        self.assertEqual(parse_qs(urlsplit(req.full_url).query), dict(scope=['database'], database_id=['db'], source=['query'],
            state=['UNKNOWN'], search=['a%_'], **{'from':['2026-10-29T00:00:00Z']}, cursor=['signed'], limit=['3']))
        self.call('show', '--database=db', '--id=opaque')
        self.assertEqual(urlsplit(self.calls[-1].full_url).path, '/api/v1/audit/events/opaque')
        self.call('telemetry')
        self.assertEqual(parse_qs(urlsplit(self.calls[-1].full_url).query), {'limit':['100']})
        self.assertTrue(all(r.method == 'GET' and r.data is None for r in self.calls))

    def test_export_same_download_new_file_mode_and_no_silent_overwrite(self):
        self.reply = {'id':'opaque', 'source':'query', 'source_id':'q'}
        output = self.root / 'metadata.json'
        self.call('export', '--database=db', '--id=opaque', '--output=' + str(output))
        self.assertEqual(json.loads(output.read_text()), self.reply)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(urlsplit(self.calls[-1].full_url).path, '/api/v1/audit/events/opaque/download')
        self.assertNotIn('secret-password', output.read_text())
        with self.assertRaises(ValueError):
            self.call('export', '--database=db', '--id=opaque', '--output=' + str(output))
        self.assertEqual(len(self.calls), 1)

    def test_invalid_args_and_remote_origin_never_send(self):
        for args in (('list',), ('list','--database=db','--limit=101'), ('list','--database= '),
                     ('show','--database=db','--id=bad/id'), ('telemetry','--api-url=http://example.org'),
                     ('list','--database=db','--api-url=http://localhost@127.0.0.1')):
            with self.subTest(args=args), self.assertRaises(ValueError): self.call(*args)
        self.assertEqual(self.calls, [])


if __name__ == '__main__': unittest.main()
