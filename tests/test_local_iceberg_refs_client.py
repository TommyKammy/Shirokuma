"""Offline named-ref protocol/ownership/pending tests; no catalog mutations."""
import copy
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.local_experiment.iceberg_refs import client as R, contract as C

TOKEN = 'a' * 32
TABLE_UUID = '11111111-1111-4111-8111-111111111111'


def metadata(*, head=101, refs=None):
    snapshots = [{'snapshot-id': 101, 'sequence-number': 1, 'timestamp-ms': 1000,
        'manifest-list': 's3://warehouse/private/list-a.avro', 'schema-id': 0,
        'summary': {'operation': 'append', 'total-records': '2', 'total-data-files': '1'}}]
    if head == 202:
        snapshots.append({'snapshot-id': 202, 'parent-snapshot-id': 101,
            'sequence-number': 2, 'timestamp-ms': 2000,
            'manifest-list': 's3://warehouse/private/list-b.avro', 'schema-id': 0,
            'summary': {'operation': 'append', 'total-records': '3', 'total-data-files': '2'}})
    return {'metadata-location': 's3://warehouse/private/v1.metadata.json', 'metadata': {
        'format-version': 2, 'table-uuid': TABLE_UUID, 'location': 's3://warehouse/private/table',
        'last-updated-ms': 2000, 'last-sequence-number': len(snapshots), 'last-column-id': 2,
        'properties': {'comment': C.marker(TOKEN)}, 'current-schema-id': 0,
        'schemas': [{'type': 'struct', 'schema-id': 0, 'fields': [
            {'id': 1, 'name': 'id', 'type': 'long', 'required': False},
            {'id': 2, 'name': 'name', 'type': 'string', 'required': False}]}],
        'partition-specs': [{'spec-id': 0, 'fields': []}], 'default-spec-id': 0,
        'sort-orders': [{'order-id': 0, 'fields': []}], 'default-sort-order-id': 0,
        'current-snapshot-id': head, 'snapshots': snapshots,
        'refs': {'main': {'type': 'branch', 'snapshot-id': head}, **(refs or {})},
        'snapshot-log': [{'timestamp-ms': row['timestamp-ms'], 'snapshot-id': row['snapshot-id']}
                         for row in snapshots], 'metadata-log': []}}


def updated(source, name='baseline_tag', target=101):
    value = copy.deepcopy(source)
    value['metadata']['refs'][name] = {'type': C.REFS[name], 'snapshot-id': target}
    value['metadata']['last-updated-ms'] += 1
    value['metadata']['metadata-log'].append({'timestamp-ms': 2000, 'metadata-file': source['metadata-location']})
    value['metadata-location'] = source['metadata-location'].replace('v1.', 'v2.')
    return value


class Response(io.BytesIO):
    def __init__(self, value, *, raw=None, status=200, content_type='application/json', url=None):
        super().__init__(json.dumps(value).encode() if raw is None else raw)
        self.status = status
        self.headers = {'Content-Type': content_type}
        self.url = url

    def geturl(self):
        return self.url


class Opener:
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def open(self, request, timeout):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        if response.url is None:
            response.url = request.full_url
        return response


def client(*responses, progress=None, guard=None):
    opener = Opener(*responses)
    records = []
    instance = R.RefClient(TOKEN, progress or (lambda value: records.append(value)), guard or Mock(), opener=opener)
    instance._auth.realm, instance._auth.token = 'private-realm', 'private-token'
    instance._journal['authenticated'] = True
    return instance, opener, records


def conflict(value=None):
    value = value or {'error': {'message': 'PRIVATE SERVER TEXT', 'type': 'CommitFailedException', 'code': 409}}
    return HTTPError(R.BASE_URL + C.table_path(TOKEN), 409, 'PRIVATE', {}, io.BytesIO(json.dumps(value).encode()))


class ContractTests(unittest.TestCase):
    def test_safe_projection_and_fixed_cas_body(self):
        before = C.project_metadata(metadata(), TOKEN)
        self.assertNotIn('private', json.dumps(before))
        self.assertFalse(before['data_files_verified'])
        body = C.commit_body(before, TOKEN, 'baseline_tag', 101, None)
        self.assertEqual(body, {'requirements': [
            {'type': 'assert-table-uuid', 'uuid': TABLE_UUID},
            {'type': 'assert-ref-snapshot-id', 'ref': 'main', 'snapshot-id': 101},
            {'type': 'assert-ref-snapshot-id', 'ref': 'baseline_tag', 'snapshot-id': None}],
            'updates': [{'action': 'set-snapshot-ref', 'ref-name': 'baseline_tag', 'type': 'tag', 'snapshot-id': 101}]})
        for name, target, expected in [('main', 101, None), ('other', 101, None),
                                       ('audit_branch', True, None), ('audit_branch', 999, None),
                                       ('baseline_tag', 101, 101), ('audit_branch', 101, True)]:
            with self.subTest(name=name, target=target, expected=expected), self.assertRaises(C.ContractError):
                C.commit_body(before, TOKEN, name, target, expected)

    def test_metadata_ownership_schema_ref_and_size_boundaries(self):
        mutations = [lambda value: value['metadata']['properties'].update(comment='foreign'),
                     lambda value: value['metadata'].update(**{'table-uuid': 'not-uuid'}),
                     lambda value: value['metadata'].update(**{'current-snapshot-id': True}),
                     lambda value: value['metadata'].update(**{'format-version': True}),
                     lambda value: value['metadata']['refs'].update(foreign={'type': 'tag', 'snapshot-id': 101}),
                     lambda value: value['metadata']['refs']['main'].update(type='tag'),
                     lambda value: value['metadata']['refs']['main'].update(**{'max-ref-age-ms': 1000}),
                     lambda value: value['metadata'].update(**{'current-schema-id': False}),
                     lambda value: value['metadata']['snapshots'].append(copy.deepcopy(value['metadata']['snapshots'][0])),
                     lambda value: value['metadata']['schemas'][0]['fields'][1].update(name='secret'),
                     lambda value: value['metadata']['snapshots'][0].update(**{'parent-snapshot-id': 999})]
        for mutate in mutations:
            value = metadata()
            mutate(value)
            with self.assertRaises(C.ContractError):
                C.project_metadata(value, TOKEN)
        with self.assertRaises(C.ContractError):
            C.project_metadata(metadata(), TOKEN, '22222222-2222-4222-8222-222222222222')
        with self.assertRaises(C.ContractError):
            C.table_name('../fixture_v1')

    def test_reference_only_change_preserves_snapshots_schema_and_other_refs(self):
        source = metadata(head=202, refs={'baseline_tag': {'type': 'tag', 'snapshot-id': 101},
                                         'audit_branch': {'type': 'branch', 'snapshot-id': 101}})
        before = C.project_metadata(source, TOKEN)
        after = C.project_metadata(updated(source, 'audit_branch', 202), TOKEN)
        C.verify_transition(before, after, TOKEN, 'audit_branch', 202)
        self.assertEqual(after['refs']['baseline_tag']['snapshot_id'], 101)
        for key in ('stable_metadata_sha256', 'table_uuid', 'current_snapshot_id'):
            bad = copy.deepcopy(after)
            bad[key] = 'f' * 64 if key.endswith('sha256') else '22222222-2222-4222-8222-222222222222' if key == 'table_uuid' else 101
            with self.assertRaises(C.ContractError):
                C.verify_transition(before, bad, TOKEN, 'audit_branch', 202)

    def test_data_file_projection_retains_measured_zero_without_exposing_paths(self):
        source = {'columns': ['file_path', 'record_count', 'file_size_in_bytes'],
                  'rows': [['s3://private/f1', 0, 19], ['s3://private/f2', 2, 20]]}
        first = C.project_data_files(source)
        source['rows'].reverse()
        self.assertEqual(first, C.project_data_files(source))
        self.assertEqual((first['file_count'], first['record_count'], first['file_size_in_bytes']), (2, 2, 39))
        self.assertNotIn('private', json.dumps(first))
        for invalid in (None, True, -1, 1.5, '0'):
            source['rows'][0][1] = invalid
            with self.assertRaises(C.ContractError):
                C.project_data_files(source)


class ClientTests(unittest.TestCase):
    def test_reuses_local_authentication_without_receipt_secrets(self):
        import base64
        secret = {'data': {key: base64.b64encode(value.encode()).decode() for key, value in {
            'client_id': 'PRIVATE-ID', 'client_secret': 'PRIVATE-SECRET', 'realm': 'PRIVATE-REALM'}.items()}}
        records, guard = [], Mock()
        opener = Opener(Response({'access_token': 'PRIVATE-TOKEN'}))
        instance = R.RefClient(TOKEN, records.append, guard, opener=opener)
        with patch.object(R, '_read_secret', return_value=json.dumps(secret).encode()) as command:
            instance.authenticate()
        command.assert_called_once_with()
        self.assertEqual(opener.requests[0].full_url, R.BASE_URL + R.TOKEN_PATH)
        self.assertNotIn('PRIVATE', json.dumps(instance.receipt()))
        self.assertTrue(instance.receipt()['authenticated'])
        self.assertGreaterEqual(guard.call_count, 2)

    def test_success_has_durable_pending_before_post_and_verified_readback(self):
        source, after = metadata(), updated(metadata())
        before = C.project_metadata(source, TOKEN)
        instance, opener, records = client(Response(source), Response(after), Response(after))
        # Only the first pending save occurs before POST; load() later keeps the
        # unresolved intent while adding its readback projection.
        first_pending = [False]
        def progress(journal):
            if journal['pending'] is not None and not first_pending[0]:
                self.assertEqual([request.method for request in opener.requests], ['GET'])
                first_pending[0] = True
            records.append(journal)
        instance.progress = progress
        result = instance.set_ref(before, 'baseline_tag', 101)
        self.assertEqual(result['refs']['baseline_tag'], {'type': 'tag', 'snapshot_id': 101})
        self.assertEqual([request.method for request in opener.requests], ['GET', 'POST', 'GET'])
        self.assertTrue(first_pending[0])
        self.assertEqual(instance.receipt()['outcome'], 'verified')
        self.assertIsNone(instance.receipt()['pending'])
        self.assertNotIn('private', json.dumps(instance.receipt()))
        records[0]['table_uuid'] = 'tampered'
        self.assertEqual(instance.receipt()['table_uuid'], TABLE_UUID)

    def test_exact_commit_conflict_is_rejected_not_unknown(self):
        source = metadata()
        instance, opener, _ = client(Response(source), conflict())
        with self.assertRaises(R.RefConflict) as caught:
            instance.set_ref(C.project_metadata(source, TOKEN), 'audit_branch', 101, 101)
        self.assertEqual((caught.exception.code, caught.exception.http_status, caught.exception.outcome),
                         ('REF_COMMIT_CONFLICT', 409, 'rejected'))
        self.assertEqual(instance.receipt()['outcome'], 'rejected')
        self.assertIsNone(instance.receipt()['pending'])
        self.assertEqual(len(opener.requests), 2)

    def test_transport_errors_and_untyped_409_are_pending_and_never_retried(self):
        failures = [URLError('PRIVATE NETWORK'), TimeoutError('PRIVATE TIMEOUT'),
                    HTTPError(R.BASE_URL, 500, 'PRIVATE', {}, io.BytesIO()),
                    conflict({'error': {'type': 'OtherFailure', 'code': 409}})]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                source = metadata()
                instance, opener, _ = client(Response(source), failure)
                before = C.project_metadata(source, TOKEN)
                with self.assertRaises(R.RefError) as caught:
                    instance.set_ref(before, 'audit_branch', 101)
                self.assertEqual(caught.exception.outcome, 'unknown')
                self.assertIsNotNone(instance.receipt()['pending'])
                with self.assertRaises(R.RefError):
                    instance.set_ref(before, 'audit_branch', 101)
                self.assertEqual(len(opener.requests), 2)
                self.assertNotIn('PRIVATE', str(caught.exception) + json.dumps(instance.receipt()))

    def test_success_response_without_valid_readback_is_not_success(self):
        for response in (Response(metadata()), URLError('lost readback')):
            source = metadata()
            instance, opener, _ = client(Response(source), Response(updated(source)), response)
            with self.assertRaises(R.RefError):
                instance.set_ref(C.project_metadata(source, TOKEN), 'baseline_tag', 101)
            self.assertEqual(instance.receipt()['outcome'], 'unknown')
            self.assertEqual(len(opener.requests), 3)

    def test_pending_persistence_failure_sends_no_mutation(self):
        def progress(journal):
            if journal['pending']:
                raise OSError('PRIVATE DISK ERROR')
        source = metadata()
        instance, opener, _ = client(Response(source), progress=progress)
        with self.assertRaises(OSError):
            instance.set_ref(C.project_metadata(source, TOKEN), 'baseline_tag', 101)
        self.assertEqual([request.method for request in opener.requests], ['GET'])
        self.assertEqual(instance.receipt()['outcome'], 'not-sent')

    def test_final_persistence_failure_keeps_unknown_intent(self):
        def progress(journal):
            if journal['outcome'] == 'verified':
                raise OSError('PRIVATE DISK ERROR')
        source = metadata()
        instance, opener, _ = client(Response(source), Response(updated(source)), Response(updated(source)), progress=progress)
        with self.assertRaises(R.RefError):
            instance.set_ref(C.project_metadata(source, TOKEN), 'baseline_tag', 101)
        self.assertEqual(instance.receipt()['outcome'], 'unknown')
        self.assertIsNotNone(instance.receipt()['pending'])

    def test_interrupted_post_preserves_original_exception_and_unknown(self):
        source = metadata()
        interruption = KeyboardInterrupt()
        instance, opener, _ = client(Response(source), interruption)
        with self.assertRaises(KeyboardInterrupt) as caught:
            instance.set_ref(C.project_metadata(source, TOKEN), 'baseline_tag', 101)
        self.assertIs(caught.exception, interruption)
        self.assertEqual(instance.receipt()['outcome'], 'unknown')
        self.assertEqual(len(opener.requests), 2)

    def test_rejection_not_durably_saved_remains_unknown_and_cancel_is_preserved(self):
        for failure in (OSError('disk unavailable'), KeyboardInterrupt()):
            def progress(journal):
                if journal['outcome'] == 'rejected':
                    raise failure
            source = metadata()
            instance, _, _ = client(Response(source), conflict(), progress=progress)
            with self.assertRaises(R.RefError if isinstance(failure, Exception) else KeyboardInterrupt) as error:
                instance.set_ref(C.project_metadata(source, TOKEN), 'audit_branch', 101, 101)
            self.assertNotIsInstance(error.exception, R.RefConflict)
            self.assertEqual(instance.receipt()['outcome'], 'unknown')
            self.assertIsNotNone(instance.receipt()['pending'])

    def test_metadata_drift_or_guard_refusal_sends_no_mutation(self):
        source = metadata()
        drift = copy.deepcopy(source)
        drift['metadata']['last-updated-ms'] += 1
        instance, opener, _ = client(Response(drift))
        with self.assertRaises(R.RefError):
            instance.set_ref(C.project_metadata(source, TOKEN), 'baseline_tag', 101)
        self.assertEqual([request.method for request in opener.requests], ['GET'])
        instance, opener, _ = client(guard=Mock(side_effect=RuntimeError('wrong service UID')))
        with self.assertRaises(RuntimeError):
            instance.load()
        self.assertEqual(opener.requests, [])

    def test_same_name_uuid_or_location_replacement_is_refused(self):
        for field, value in [('table-uuid', '22222222-2222-4222-8222-222222222222'),
                             ('location', 's3://warehouse/foreign')]:
            source, other = metadata(), metadata()
            other['metadata'][field] = value
            instance, _, _ = client(Response(source), Response(other))
            instance.load()
            with self.assertRaises(R.RefError):
                instance.load()

    def test_malformed_large_redirect_and_non_json_responses_are_refused(self):
        cases = [Response({}, raw=b'{"a":1,"a":2}'), Response({}, raw=b'{"a":NaN}'),
                 Response({}, raw=b' ' * (C.MAX_RESPONSE_BYTES + 1)),
                 Response(metadata(), content_type='text/html'),
                 Response(metadata(), url='https://foreign.invalid/table'),
                 HTTPError(R.BASE_URL, 302, 'redirect', {'Location': 'https://foreign.invalid/'}, io.BytesIO())]
        for response in cases:
            instance, opener, _ = client(response)
            with self.assertRaises(R.RefError):
                instance.load()
            self.assertEqual(len(opener.requests), 1)
        handler = R.AUTH.NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, '', {}, 'https://foreign.invalid'))


class CredentialBoundaryTests(unittest.TestCase):
    def child_reader(self, script, *, timeout=2, limit=R.MAX_SECRET_BYTES, authenticate=False):
        real_popen = R.subprocess.Popen
        processes, commands = [], []
        def spawn(command, **kwargs):
            commands.append(command)
            process = real_popen([sys.executable, '-c', script], **kwargs)
            processes.append(process)
            return process
        opener = Opener()
        instance = R.RefClient(TOKEN, lambda _: None, Mock(), opener=opener)
        with patch.object(R.subprocess, 'Popen', side_effect=spawn), \
                patch.object(R, 'SECRET_TIMEOUT_SECONDS', timeout), patch.object(R, 'MAX_SECRET_BYTES', limit):
            try:
                value = instance.authenticate() if authenticate else R._read_secret()
                error = None
            except R.RefError as caught:
                value, error = None, caught
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0], ['kubectl', '--context', 'colima-mac-studio-solo',
            '--request-timeout=10s', '-n', 'shirokuma-dev', 'get', 'secret',
            'polaris-root-credentials', '-o', 'json'])
        self.assertTrue(all(process.poll() is not None for process in processes))
        self.assertEqual(opener.requests, [])
        self.assertNotIn('PRIVATE', json.dumps(instance.receipt()) + str(error))
        return value, error

    def test_fixed_secret_subprocess_returns_only_bounded_stdout(self):
        value, error = self.child_reader("import sys;sys.stdout.write('{}');sys.stderr.write('PRIVATE STDERR')")
        self.assertEqual(value, b'{}')
        self.assertIsNone(error)

    def test_hanging_secret_process_is_terminated_before_any_http(self):
        _, error = self.child_reader('import time;time.sleep(10)', timeout=0.05)
        self.assertEqual(error.code, 'SECRET_READ_TIMEOUT')
        _, error = self.child_reader('import time;time.sleep(10)', timeout=0.05, authenticate=True)
        self.assertEqual(error.code, 'AUTHENTICATION_FAILED')

    def test_stdout_or_stderr_overflow_is_bounded_and_prevents_http(self):
        for stream in ('stdout', 'stderr'):
            script = "import sys,time;sys.%s.write('PRIVATE' * 1000);sys.%s.flush();time.sleep(10)" % (stream, stream)
            _, error = self.child_reader(script, limit=128)
            self.assertEqual(error.code, 'SECRET_READ_TOO_LARGE')
            _, error = self.child_reader(script, limit=128, authenticate=True)
            self.assertEqual(error.code, 'AUTHENTICATION_FAILED')

    def test_nonzero_command_and_malformed_credentials_never_exchange_token(self):
        _, error = self.child_reader("import sys;sys.stderr.write('PRIVATE');sys.exit(1)", authenticate=True)
        self.assertEqual(error.code, 'AUTHENTICATION_FAILED')
        for raw in (b'PRIVATE not-json', b'{"data":{}}', b'{"data":{"client_id":"!PRIVATE"}}',
                    b'[]', b'{"data":{},"data":{}}'):
            opener = Opener()
            instance = R.RefClient(TOKEN, lambda _: None, Mock(), opener=opener)
            with patch.object(R, '_read_secret', return_value=raw), self.assertRaises(R.RefError) as caught:
                instance.authenticate()
            self.assertEqual(opener.requests, [])
            self.assertFalse(instance.receipt()['authenticated'])
            self.assertNotIn('PRIVATE', str(caught.exception) + json.dumps(instance.receipt()))


if __name__ == '__main__':
    unittest.main()
