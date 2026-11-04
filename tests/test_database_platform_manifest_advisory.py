"""Pure projection and both HTTP handlers over captured, in-memory connections."""
from dataclasses import replace
from http.client import HTTPResponse
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_database_platform_controller_baseline as adjacent
import ai_routes
import db_platform_manifest_advisory as advisory
from warehouse_operations import OperationError

APIS = {'Pod': 'v1', 'Deployment': 'apps/v1', 'StatefulSet': 'apps/v1',
        'DaemonSet': 'apps/v1', 'Job': 'batch/v1', 'CronJob': 'batch/v1'}
KEYS = ('read_only_root_filesystem', 'non_root', 'drop_all_capabilities', 'cpu_memory_limits')
YAML = """apiVersion: v1
kind: Pod
metadata: {generateName: hidden-workload}
spec:
  securityContext: {runAsNonRoot: true, runAsUser: 1000}
  containers:
    - name: hidden-container
      securityContext:
        readOnlyRootFilesystem: true
        capabilities: {drop: [ALL]}
      resources: {limits: {cpu: 0.25, memory: 32Mi}}
    - securityContext: {runAsUser: 0}
  initContainers: [{}]
  ephemeralContainers:
    - securityContext: {runAsNonRoot: false}
"""


def workload(kind, spec):
    if kind != 'Pod':
        spec = {'template': {'spec': spec}}
    if kind == 'CronJob':
        spec = {'jobTemplate': {'spec': spec}}
    return json.dumps(dict(apiVersion=APIS[kind], kind=kind, spec=spec))


class ProjectionTests(unittest.TestCase):
    def test_six_exact_shapes_closed_and_name_free(self):
        for kind in APIS:
            with self.subTest(kind=kind):
                result = advisory.review(workload(kind, {'containers': [{'name': 'hidden🐻'}]}))
                self.assertEqual(result, dict(kind=kind, containers=[
                    dict(kind='container', ordinal=1, checks=dict.fromkeys(KEYS, 'not_met'))]))

    def test_yaml_checks_and_overrides(self):
        rows = advisory.review(YAML)['containers']
        self.assertEqual([(r['kind'], r['ordinal']) for r in rows],
                         [('container', 1), ('container', 2), ('init_container', 1), ('ephemeral_container', 1)])
        self.assertEqual([list(r['checks'].values()) for r in rows], [
            ['met'] * 4, ['not_met'] * 4, ['not_met', 'met', 'not_met', 'not_met'], ['not_met'] * 4])
        self.assertNotIn('hidden', json.dumps(rows))
        result = advisory.review(workload('Pod', {
            'securityContext': {'runAsNonRoot': False, 'runAsUser': 0},
            'containers': [{'securityContext': {'runAsNonRoot': True, 'runAsUser': 1},
                            'resources': {'limits': {'cpu': '0e4', 'memory': '2Gi'}}}]}))
        self.assertEqual(result['containers'][0]['checks']['non_root'], 'met')
        self.assertEqual(result['containers'][0]['checks']['cpu_memory_limits'], 'not_met')
        no_uid = YAML.replace(', runAsUser: 1000', '')
        self.assertEqual(advisory.review(no_uid)['containers'][0]['checks']['non_root'], 'met')

    def test_optional_nulls_are_absent_and_container_nulls_inherit(self):
        spec = {
            'securityContext': {'runAsNonRoot': True, 'runAsUser': 1000},
            'containers': [
                {'securityContext': None, 'resources': None},
                {'securityContext': {'runAsNonRoot': None, 'runAsUser': None, 'capabilities': None},
                 'resources': {'limits': None}},
                {'securityContext': {'capabilities': {'drop': None}},
                 'resources': {'limits': {'cpu': None, 'memory': '1Gi'}}},
                {'resources': {'limits': {'cpu': '1', 'memory': None}}},
            ],
            'initContainers': None, 'ephemeralContainers': None,
        }
        for kind in APIS:
            with self.subTest(kind=kind):
                rows = advisory.review(workload(kind, spec))['containers']
                self.assertEqual(len(rows), 4)
                self.assertTrue(all(list(row['checks'].values()) ==
                                    ['not_met', 'met', 'not_met', 'not_met'] for row in rows))
        spec['securityContext'] = None
        spec['ephemeralContainers'] = []
        rows = advisory.review(workload('CronJob', spec))['containers']
        self.assertTrue(all(row['checks']['non_root'] == 'not_met' for row in rows))
        for value in ({}, False, [{}]):
            spec['ephemeralContainers'] = value
            with self.subTest(ephemeral=value), self.assertRaises(ValueError):
                advisory.review(workload('CronJob', spec))

    def test_invalid_documents_and_projection(self):
        valid = workload('Pod', {'containers': [{}]})
        documents = [
            '', 'null', '[]', '---\n' + valid + '\n---\n' + valid, valid + '\n---\n',
            valid.replace('Pod', 'List'), valid.replace('"v1"', '"V1"'), valid + ']',
            valid.replace('"kind":', '"kind":"Pod","kind":'),
            YAML + '  extra: &unused 1\n', YAML + '  extra: *unknown\n',
            YAML + '  extra: &self [*self]\n', YAML + '  extra: !custom 1\n',
            YAML + '  extra: {<<: {a: 1}}\n', YAML + '  extra: {"<<": 1}\n',
            YAML + '  extra: {a: 1, "a": 2}\n',
            YAML.replace('runAsNonRoot: true', 'runAsNonRoot: yes'),
            workload('Deployment', {'containers': [{}], 'ephemeralContainers': [{}]}),
            *[workload('Pod', spec) for spec in [
                {}, {'containers': []}, {'containers': {}}, {'containers': [None]},
                {'containers': None}, {'containers': [{}], 'initContainers': {}},
                {'containers': [{}], 'securityContext': {'runAsUser': '1000'}},
                {'containers': [{'securityContext': {'readOnlyRootFilesystem': 'true'}}]},
                {'containers': [{'securityContext': {'capabilities': {'drop': 'ALL'}}}]},
                {'containers': [{'securityContext': {'capabilities': {'drop': [3]}}}]},
                {'containers': [{'resources': {'limits': {'cpu': False}}}]},
                {'containers': [{'resources': {'limits': {'memory': 'NaN'}}}]},
            ]],
        ]
        for index, manifest in enumerate(documents):
            with self.subTest(index=index), self.assertRaises(ValueError):
                advisory.review(manifest)

    def test_byte_node_depth_and_container_limits(self):
        valid = workload('Pod', {'containers': [{}]})
        comment = valid + '\n#'
        exact_bytes = comment + 'x' * (65536 - len(comment))
        self.assertEqual(advisory.review(exact_bytes)['kind'], 'Pod')
        self.assertEqual(len(advisory.review(workload('Pod', {'containers': [{}] * 64}))['containers']), 64)
        for manifest in [
            exact_bytes + 'x', comment + '日' * 22000, comment + '\ud800',
            YAML + '  extra: [' + 'x,' * 4096 + ']',
            YAML + '  extra: ' + '[' * 33 + 'x' + ']' * 33,
            workload('Pod', {'containers': [{}] * 63, 'initContainers': [{}] * 2}),
        ]:
            with self.assertRaises(ValueError):
                advisory.review(manifest)


class Connection:
    def __init__(self, raw):
        self.reads = []
        reads = self.reads

        class Reader(io.BytesIO):
            def read(self, size=-1):
                reads.append(size)
                return super().read(size)

        self.incoming, self.outgoing = Reader(raw), io.BytesIO()

    def makefile(self, *args):
        return self.incoming

    def sendall(self, raw):
        self.outgoing.write(raw)


class AdapterTests(unittest.TestCase):
    handlers = adjacent.base.BaselineTests.handlers

    def setUp(self):
        auth = adjacent.auth
        self.principal = auth.AuthenticatedPrincipal('p', 'admin', True)
        self.account = {'role': 'admin', 'enabled': True, 'disabled': False, 'platform_admin': True}
        self.auth_patch = patch.object(auth, '_principal_accounts', return_value={'p': self.account})
        self.auth_patch.start()
        self.addCleanup(self.auth_patch.stop)

    def request(self, handler, *, body=None, headers=None, extra='', role='admin', principal=None,
                suffix='', method='POST'):
        if body is None:
            body = json.dumps({'manifest': YAML}).encode()
        if headers is None:
            headers = f'Content-Type: application/json; charset=utf-8\r\nContent-Length: {len(body)}\r\n'
        raw = (f'{method} {ai_routes.MANIFEST_ADVISORY_PATH}{suffix} HTTP/1.1\r\n'
               f'Host: 127.0.0.1\r\nConnection: close\r\nX-Shirokuma-Role: {role}\r\n'
               + headers + extra + '\r\n').encode() + body
        connection = Connection(raw)
        with (patch.object(adjacent.base.runtime_profile, 'active', return_value=True),
              patch.object(adjacent.auth, 'authenticate_principal', return_value=principal or self.principal),
              patch.object(handler, 'log_message')):
            handler(connection, ('127.0.0.1', 1), SimpleNamespace())
        response = HTTPResponse(Connection(connection.outgoing.getvalue()))
        response.begin()
        value = json.loads(response.read())
        return response.status, value, connection.reads

    def test_both_adapters_valid_framing_and_closed_dto(self):
        for handler in self.handlers():
            status, result, reads = self.request(handler)
            self.assertEqual((status, result), (200, advisory.review(YAML)))
            self.assertEqual(reads, [len(json.dumps({'manifest': YAML}).encode())])
            body = json.dumps({'manifest': YAML}).encode().ljust(128 * 1024)
            status, result, reads = self.request(handler, body=body, extra='Origin: http://127.0.0.1\r\n')
            self.assertEqual((status, result, reads), (200, advisory.review(YAML), [128 * 1024]))

    def test_framing_and_origin_rejected_before_read(self):
        for handler in self.handlers():
            for options, expected in [
                *[({'suffix': s}, 400) for s in ('?', '?secret=1', '#secret', '#')],
                *[({'headers': 'Content-Type: application/json\r\n' + length}, 400) for length in (
                    '', 'Content-Length: 0\r\n', 'Content-Length: +1\r\n', 'Content-Length: -1\r\n',
                    'Content-Length: 131073\r\n', 'Content-Length: 1,1\r\n')],
                ({'headers': 'Content-Length: 1\r\n'}, 400),
                ({'headers': 'Content-Length: 1\r\nContent-Type: text/plain\r\n'}, 400),
                *[({'extra': extra}, 400) for extra in (
                    'content-type: application/json\r\n', 'Content-Length: 1\r\n',
                    'Transfer-Encoding: \r\n', 'Transfer-Encoding: chunked\r\n',
                    'Authorization: Basic x\r\nauthorization: Basic y\r\n',
                    'Cookie: a=b\r\nCookie: a=c\r\n', 'X-Shirokuma-Role: admin\r\n',
                    'Host: duplicate\r\n', 'Bad Header: secret\r\n',
                    'Origin: \r\nOrigin: https://elsewhere.invalid\r\n',
                    'X-Test: first\r\n folded\r\n')],
                ({'extra': 'Origin: https://elsewhere.invalid\r\n'}, 403),
                ({'method': 'GET', 'headers': '', 'body': b''}, 404),
            ]:
                with self.subTest(handler=handler.__name__, options=options):
                    status, result, reads = self.request(handler, **options)
                    self.assertEqual(status, expected, result)
                    self.assertEqual(reads, [])
                    self.assertNotIn('secret', json.dumps(result))

    def test_body_errors_are_sanitized(self):
        bodies = [b'{"manifest":"secret","manifest":"secret"}', b'{"secret":"secret"}',
                  b'{"manifest":null}', b'{"manifest":NaN}', b'{"manifest":{"nested":"secret"}}',
                  b'{"manifest":"secret"} extra', b'["secret"]', b'{"manifest":"\xffsecret"}',
                  b'{"manifest":"\\ud800"}', json.dumps({'manifest': 'x' * 65537}).encode(),
                  b'[' * 2000 + b']' * 2000]
        for handler in self.handlers():
            for body in bodies:
                status, result, reads = self.request(handler, body=body)
                self.assertEqual((status, result['code'], reads), (400, 'INVALID_INPUT', [len(body)]))
                self.assertNotIn('secret', json.dumps(result))
            status, result, reads = self.request(handler, headers='Content-Type: application/json\r\nContent-Length: 1234\r\n')
            self.assertEqual((status, result['code'], reads), (400, 'INVALID_INPUT', [1234]))

    def test_true_platform_authority_before_read_and_parse(self):
        for handler in self.handlers():
            for options in [{'role': 'data_engineer'}, {'role': 'bi_viewer'},
                            {'principal': replace(self.principal, platform_admin=False)},
                            {'principal': replace(self.principal, maximum_role='data_engineer')}]:
                with patch.object(advisory, 'review', side_effect=AssertionError('parsed')):
                    status, result, reads = self.request(handler, body=b'broken', **options)
                self.assertEqual(status, 403, result)
                self.assertEqual(reads, [])
            for change in [{'platform_admin': 1}, {'role': 'data_engineer'}, {'disabled': True}]:
                with patch.dict(self.account, change):
                    self.assertEqual(self.request(handler)[0::2], (403, []))

    def test_revocation_wins_over_valid_or_invalid_parse_on_both_adapters(self):
        for handler in self.handlers():
            for after in ['new fingerprint', OperationError('PLATFORM_ACCESS_DENIED', 403)]:
                for body in [None, b'{"manifest":"secret"}', b'not-json']:
                    checks = ['initial', after] if body == b'not-json' else ['initial', 'initial', after]
                    with patch.object(advisory, '_authority', side_effect=checks) as authority:
                        status, result, reads = self.request(handler, body=body)
                    self.assertEqual((status, result['code']), (403, 'PLATFORM_ACCESS_DENIED'))
                    self.assertEqual(authority.call_count, len(checks))
                    self.assertEqual(len(reads), 1)


    def test_revoked_after_read_never_enters_yaml_review(self):
        for handler in self.handlers():
            with (patch.object(advisory, '_authority', side_effect=['initial', 'changed', 'changed']) as authority,
                  patch.object(advisory, 'review') as review):
                status, result, reads = self.request(handler)
            self.assertEqual((status, result['code']), (403, 'PLATFORM_ACCESS_DENIED'))
            self.assertEqual(len(reads), 1)
            self.assertEqual(authority.call_count, 3)
            review.assert_not_called()
