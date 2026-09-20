"""Doctor uses the existing Reader owner; no services or model calls in these tests."""
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.notebook import client as N
from tools.local_experiment.query_diagnose import contract as D, run as R
from tests.test_local_notebook_client import Child, encoded
from tests.test_query_diagnose_contract import evidence, source, snapshot_source
from tests.test_query_diagnose_run import receipt as v1_receipt
from tests.test_query_diagnose_snapshot_source import receipt as v2_receipt
from tests.test_query_diagnose_filter import source as filtered_source, receipt as v3_receipt

TOKEN = 'f' * 32
READ_TOKEN = source()['read_token']


def public(version=1, *, missing=False):
    src = {1: source, 2: snapshot_source, 3: filtered_source}[version]()
    observed = evidence(src, missing=missing)
    return D.public_summary(src, observed, D.expected_diagnosis(observed))


def frames(value=None, *, result='passed', cleanup=True, error='SOURCE_INVALID'):
    return [dict(schema_version=1, event='allocated', command='query diagnose', evidence_token=TOKEN),
            dict(schema_version=1, event='result', command='query diagnose', evidence_token=TOKEN,
                 result=result, observation=public() if value is None and result == 'passed' else value,
                 error_code=None if result == 'passed' else 'INTERRUPTED' if result == 'interrupted' else error,
                 cleanup_verified=cleanup, evidence=f'tmp/local-experiment/query-diagnose/{TOKEN}/summary.json')]


class DiagnoseClientTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.output = Path(temp.name).resolve(); self.output.chmod(0o700)
        self.binary = self.output / 'shirokuma'; self.binary.write_bytes(b'fake')
        env = patch.dict(os.environ, {'SHIROKUMA_NOTEBOOK_RUN_DIR': str(self.output),
                                     'SHIROKUMA_NOTEBOOK_BINARY': str(self.binary)})
        env.start(); self.addCleanup(env.stop)
        self.roots = []
        for attr in ('READ_STATE', 'SNAPSHOT_READ_STATE', 'FILTER_READ_STATE'):
            root = self.output / attr; root.mkdir(mode=0o700); self.roots.append(root)
            mocked = patch.object(R, attr, root); mocked.start(); self.addCleanup(mocked.stop)

    def put(self, version, value):
        folder = self.roots[version - 1] / value['evidence_token']; folder.mkdir(mode=0o700)
        (folder / 'run.lock').touch(mode=0o600)
        path = folder / 'summary.json'; path.write_bytes(N._canonical(value)); path.chmod(0o600)
        return path

    def launch(self, child, saved):
        def fake(argv, **kwargs):
            self.assertTrue(kwargs['start_new_session'])
            intent = json.loads((self.output / 'reader-intent.json').read_bytes())
            self.assertEqual(intent['command'], 'query diagnose')
            self.assertEqual(intent['request'], N._diagnose_request(READ_TOKEN))
            kwargs['stdout'].write(encoded(saved))
            return child
        return fake

    def test_existing_source_lease_accepts_v1_v2_v3_select_without_changing_receipts(self):
        # The actual source validator, not a Notebook approximation, decides eligibility.
        for version, make_receipt in ((1, v1_receipt), (2, v2_receipt), (3, v3_receipt)):
            with self.subTest(version=version):
                value = make_receipt(); path = self.put(version, value)
                before = (path.read_bytes(), R.fingerprint(path.stat()))
                with R.SourceLease(value['evidence_token']) as lease:
                    self.assertEqual(lease.source['schema_version'], version)
                    observed = evidence(lease.source)
                    result = D.public_summary(lease.source, observed, D.expected_diagnosis(observed))
                    token, got, cleanup = N._diagnose_reader_frames(encoded(frames(result)), 0, value['evidence_token'])
                    self.assertEqual((token, got, cleanup), (TOKEN, result, True))
                self.assertEqual(before, (path.read_bytes(), R.fingerprint(path.stat())))
                # Avoid intentionally ambiguous identical fixture tokens across roots.
                path.unlink(); (path.parent / 'run.lock').unlink(); path.parent.rmdir()

    def test_owned_diagnose_once_exact_argv_private_intent_and_child_environment(self):
        child = Child(); before = dict(os.environ)
        with patch.dict(os.environ, {'KUBECONFIG': '/scoped/config', 'HTTP_PROXY': 'private-proxy'}), \
                patch.object(N.subprocess, 'Popen', side_effect=self.launch(child, frames())) as launch, \
                patch.object(N, '_process_start', return_value='synthetic start'):
            got = N.read_diagnose(READ_TOKEN)
            with self.assertRaises(FileExistsError): N.read_diagnose(READ_TOKEN)
            self.assertEqual(os.environ['KUBECONFIG'], '/scoped/config')
        self.assertEqual(dict(os.environ), before)
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(launch.call_args.args[0], [str(self.binary), '--repo-root', str(N.ROOT), '--profile',
                         'local-experiment', 'query', 'diagnose', '--read-token', READ_TOKEN])
        self.assertEqual(launch.call_args.kwargs['env']['KUBECONFIG'], '/scoped/config')
        self.assertNotIn('HTTP_PROXY', launch.call_args.kwargs['env'])
        self.assertEqual(got, public())
        status = json.loads((self.output / 'reader-status.json').read_bytes())
        self.assertEqual(status['evidence_token'], TOKEN)
        self.assertEqual(status['state'], 'passed')
        self.assertTrue(status['child_exited'] and status['cleanup_verified'])

    def test_invalid_token_does_not_allocate_or_launch(self):
        with patch.object(N.subprocess, 'Popen') as launch:
            for value in (None, True, 1, 'A' * 32, 'a' * 31, '../' + READ_TOKEN, READ_TOKEN + '\n'):
                with self.subTest(value=value), self.assertRaises(N.NotebookError): N.read_diagnose(value)
        launch.assert_not_called()
        self.assertFalse((self.output / 'reader-intent.json').exists())

    def test_count_failed_and_busy_source_never_become_diagnosis_success(self):
        count = self.output / 'query-count' / READ_TOKEN; count.mkdir(parents=True)
        (count / 'summary.json').write_bytes(b'{"schema_version":4,"result":"passed"}')
        with self.assertRaisesRegex(R.DiagnoseError, 'SOURCE_NOT_FOUND'):
            with R.SourceLease(READ_TOKEN): self.fail('COUNT was eligible')
        path = self.put(1, v1_receipt())
        value = json.loads(path.read_bytes()); value['result'] = 'failed'; path.write_bytes(N._canonical(value))
        with self.assertRaisesRegex(R.DiagnoseError, 'SOURCE_INVALID'):
            with R.SourceLease(READ_TOKEN): self.fail('failed SELECT was eligible')
        path.write_bytes(N._canonical(v1_receipt()))
        with (path.parent / 'run.lock').open('rb') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(R.DiagnoseError, 'SOURCE_BUSY'):
                with R.SourceLease(READ_TOKEN): self.fail('busy SELECT was eligible')
        for error in ('SOURCE_NOT_FOUND', 'SOURCE_INVALID', 'SOURCE_BUSY'):
            self.assertEqual(N._diagnose_reader_frames(encoded(frames(result='failed', error=error)), 1, READ_TOKEN),
                             (TOKEN, None, True))

    def test_public_boundaries_and_findings_mapping_reject_false_claims(self):
        changes = [('schema_version', True), ('read_token', 'b' * 32), ('query_id', 'arbitrary'),
                   ('history', 'unknown'), ('root_cause', 'measured'), ('performance_gain', 'faster'),
                   ('original_select_reexecuted', 0), ('model_calls', True), ('model_calls', 2),
                   ('findings', None), ('findings', ['UNRECOGNIZED']), ('findings', ['RESULT_LIMIT_REACHED'] * 2),
                   ('findings', ['HISTORY_NOT_RETAINED']), ('next_steps', []), ('next_steps', ['RUN_SQL']),
                   ('rows', []), ('sql', 'PRIVATE_SQL')]
        for key, value in changes:
            changed = public(); changed[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(N.NotebookError):
                N._diagnose_public(changed, READ_TOKEN)
        missing = public(missing=True)
        self.assertEqual(N._diagnose_public(missing, READ_TOKEN), missing)
        missing['findings'] = sorted(missing['findings'] + ['QUEUE_TIME_OBSERVED'])
        missing['next_steps'] = sorted(set(missing['next_steps']) | {'REVIEW_QUEUE_TIMING'})
        with self.assertRaises(N.NotebookError): N._diagnose_public(missing, READ_TOKEN)

    def test_envelope_version_command_evidence_duplicate_and_size_are_strict(self):
        mutations = [(0, 'schema_version', 4), (1, 'schema_version', 3), (1, 'command', 'query count'),
                     (1, 'evidence', 'tmp/local-experiment/query-read/' + TOKEN + '/summary.json'),
                     (1, 'cleanup_verified', False), (1, 'read_token', READ_TOKEN), (1, 'error_code', 'SOURCE_INVALID')]
        for index, key, value in mutations:
            changed = frames(); changed[index][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(N.NotebookError):
                N._diagnose_reader_frames(encoded(changed), 0, READ_TOKEN)
        raw = encoded(frames())
        for changed in (raw.rstrip(b'\n'), raw + b'{}\n', raw.replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1', 1),
                        b' ' * D.MAX_PUBLIC_BYTES + raw):
            with self.assertRaises(N.NotebookError): N._diagnose_reader_frames(changed, 0, READ_TOKEN)
        with self.assertRaises(N.NotebookError): N._diagnose_reader_frames(raw, 1, READ_TOKEN)
        changed = frames()
        for event in changed: event['evidence_token'] = READ_TOKEN
        changed[1]['evidence'] = f'tmp/local-experiment/query-diagnose/{READ_TOKEN}/summary.json'
        with self.assertRaises(N.NotebookError): N._diagnose_reader_frames(encoded(changed), 0, READ_TOKEN)

    def test_fixed_failure_enum_matches_doctor_and_preserves_cleanup_unknown(self):
        self.assertEqual(N.DIAGNOSE_ERROR_CODES, R.ERROR_CODES)
        for error in R.ERROR_CODES:
            interrupted = error == 'INTERRUPTED'
            saved = frames(result='interrupted' if interrupted else 'failed', cleanup=False, error=error)
            self.assertEqual(N._diagnose_reader_frames(encoded(saved), 130 if interrupted else 1, READ_TOKEN),
                             (TOKEN, None, False))
        with self.assertRaises(N.NotebookError):
            N._diagnose_reader_frames(encoded(frames(result='failed', error='PRIVATE_MESSAGE')), 1, READ_TOKEN)

    def test_failed_child_with_verified_cleanup_does_not_return_public(self):
        child = Child()
        def finished(timeout): child.returncode = 1; return 1
        child.wait = finished
        with patch.object(N.subprocess, 'Popen', side_effect=self.launch(child, frames(result='failed'))) as launch, \
                patch.object(N, '_process_start', return_value='synthetic start'):
            with self.assertRaisesRegex(N.NotebookError, 'NOTEBOOK_READER_FAILED'): N.read_diagnose(READ_TOKEN)
        self.assertEqual(launch.call_count, 1)
        status = json.loads((self.output / 'reader-status.json').read_bytes())
        self.assertEqual(status['state'], 'failed')
        self.assertTrue(status['child_exited'] and status['cleanup_verified'])

    def test_first_cancel_survives_signal_and_terminal_save_failures(self):
        original = KeyboardInterrupt('first'); child = Child(initial=original); write = N._write
        def save(path, value, **kwargs):
            if path.name == 'reader-status.json' and value['state'] == 'interrupted': raise OSError('secondary save')
            return write(path, value, **kwargs)
        with patch.object(N.subprocess, 'Popen', side_effect=self.launch(child, frames(result='interrupted'))), \
                patch.object(N, '_process_start', return_value='synthetic start'), \
                patch.object(N.os, 'killpg', side_effect=OSError('secondary signal')) as signal, \
                patch.object(N, '_write', side_effect=save):
            with self.assertRaises(KeyboardInterrupt) as caught: N.read_diagnose(READ_TOKEN)
        self.assertIs(caught.exception, original)
        self.assertEqual(signal.call_count, 1)
        self.assertEqual(child.returncode, 130)

    def test_public_success_is_not_released_before_owned_child_exit(self):
        child = Child(); original = child.wait
        def wait(timeout):
            status = json.loads((self.output / 'reader-status.json').read_bytes())
            self.assertEqual(status['state'], 'running')
            self.assertFalse(status['child_exited'] or status['cleanup_verified'])
            return original(timeout)
        child.wait = wait
        with patch.object(N.subprocess, 'Popen', side_effect=self.launch(child, frames())), \
                patch.object(N, '_process_start', return_value='synthetic start'):
            self.assertEqual(N.read_diagnose(READ_TOKEN), public())


if __name__ == '__main__': unittest.main()
