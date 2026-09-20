"""Metadata Reader adapter checks; all processes and service I/O are faked."""
from copy import deepcopy
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.notebook import client as N
from tests.test_local_notebook_client import Child, TOKEN, encoded, reader_frames
from tests.test_query_read_contract import identity

TABLE = 'smoke.fixture_v1'
REQUEST = {'command': 'describe', 'table': TABLE}


def describe_frames():
    return [dict(schema_version=1, event='allocated', command='query describe', evidence_token=TOKEN),
            dict(schema_version=1, event='result', command='query describe', evidence_token=TOKEN,
                 result='passed', observation=identity(), error_code=None, cleanup_verified=True,
                 evidence=f'tmp/local-experiment/query-read/{TOKEN}/summary.json')]


class FailedChild(Child):
    def wait(self, timeout):
        self.calls += 1
        self.returncode = 1
        return self.returncode


class DescribeClientTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name).resolve()
        self.binary = self.path / 'shirokuma'; self.binary.write_bytes(b'not executed')
        env = patch.dict(os.environ, {'SHIROKUMA_NOTEBOOK_RUN_DIR': str(self.path),
                                    'SHIROKUMA_NOTEBOOK_BINARY': str(self.binary)})
        env.start(); self.addCleanup(env.stop)

    def spawn(self, child, frames=None):
        def start(argv, **kwargs):
            self.assertEqual(argv, [str(self.binary), '--repo-root', str(N.ROOT), '--profile', 'local-experiment',
                                    'query', 'describe', '--table', TABLE])
            self.assertEqual(json.loads((self.path / 'reader-intent.json').read_bytes()),
                             dict(schema_version=1, command='query describe', request=REQUEST))
            self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
            self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
            self.assertTrue(kwargs['start_new_session'])
            kwargs['stdout'].write(encoded(describe_frames() if frames is None else frames))
            return child
        return start

    def status(self):
        return json.loads((self.path / 'reader-status.json').read_bytes())

    def test_describe_uses_owned_process_once_and_private_receipts(self):
        with patch.object(N.subprocess, 'Popen', side_effect=self.spawn(Child())) as spawn, \
                patch.object(N, '_process_start', return_value='start'):
            self.assertEqual(N.read_describe(TABLE), identity())
            with self.assertRaises(FileExistsError): N.read_describe(TABLE)
            self.assertEqual(spawn.call_count, 1)
        self.assertEqual((self.status()['state'], self.status()['child_exited'], self.status()['cleanup_verified']),
                         ('passed', True, True))
        for name in ('reader-intent.json', 'reader-status.json', 'reader.stdout.jsonl'):
            self.assertEqual((self.path / name).stat().st_mode & 0o777, 0o600)

    def test_bad_table_refused_before_directory_or_process_access(self):
        for table in (None, '', 'iceberg.smoke.fixture_v1', 'smoke.x;DROP', 'other.table', ['smoke.fixture_v1']):
            with self.subTest(table=table), patch.object(N, '_directory') as directory, \
                    patch.object(N.subprocess, 'Popen') as spawn:
                with self.assertRaisesRegex(N.NotebookError, 'NOTEBOOK_REQUEST_INVALID'): N.read_describe(table)
                directory.assert_not_called(); spawn.assert_not_called()
        self.assertFalse((self.path / 'reader-intent.json').exists())

    def test_describe_frames_reject_cross_command_table_token_and_shape(self):
        cases = [reader_frames()]
        for change in (lambda f: f[1].update(command='query select'),
                       lambda f: f[1].update(evidence_token='b' * 32),
                       lambda f: f[1].update(extra='private'),
                       lambda f: f[1].update(observation=None),
                       lambda f: f[1]['observation'].update(table='different'),
                       lambda f: f[1]['observation'].update(catalog='other'),
                       lambda f: f[1]['observation'].update(format_version=True),
                       lambda f: f[1]['observation'].update(columns=[{'name':'bad'}])):
            frames = describe_frames(); change(frames); cases.append(frames)
        for frames in cases:
            with self.subTest(command=frames[1]['command']), self.assertRaises(N.NotebookError):
                N._describe_reader_frames(encoded(frames), 0, TABLE)
        with self.assertRaises(N.NotebookError): N._reader_frames(encoded(describe_frames()), 0)
        with self.assertRaises(N.NotebookError): N._describe_reader_frames(encoded(describe_frames()), 0, 'smoke.other')

    def test_describe_frames_require_matching_exit_and_cleanup(self):
        raw = encoded(describe_frames())
        with self.assertRaises(N.NotebookError): N._describe_reader_frames(raw, 1, TABLE)
        for cleanup in (False, None, 1):
            frames = describe_frames(); frames[1]['cleanup_verified'] = cleanup
            with self.subTest(cleanup=cleanup), self.assertRaises(N.NotebookError):
                N._describe_reader_frames(encoded(frames), 0, TABLE)
        with self.assertRaises(N.NotebookError): N._describe_reader_frames(raw.rstrip(), 0, TABLE)

    def test_empty_table_identity_keeps_unknown_current_snapshot(self):
        frames = describe_frames(); frames[1]['observation']['current_snapshot_id'] = None
        token, public, closed = N._describe_reader_frames(encoded(frames), 0, TABLE)
        self.assertEqual(token, TOKEN); self.assertTrue(closed)
        self.assertIsNone(public['current_snapshot_id'])

    def test_failed_describe_retains_cleanup_without_claiming_success(self):
        frames = describe_frames(); frames[1].update(result='failed', observation=None, error_code='READ_FAILED')
        with patch.object(N.subprocess, 'Popen', side_effect=self.spawn(FailedChild(), frames)), \
                patch.object(N, '_process_start', return_value='start'):
            with self.assertRaises(N.NotebookError): N.read_describe(TABLE)
        self.assertEqual((self.status()['state'], self.status()['child_exited'], self.status()['cleanup_verified']),
                         ('failed', True, True))
        self.assertEqual(N._describe_reader_frames(encoded(frames), 1, TABLE), (TOKEN, None, True))

    def test_interruption_preserves_first_object_after_cleanup_and_save_error(self):
        original = KeyboardInterrupt('first')
        frames = describe_frames(); frames[1].update(result='interrupted', observation=None, error_code='INTERRUPTED')
        child, write = Child(original), N._write
        def fail_final(path, value, **kwargs):
            if value.get('state') == 'interrupted': raise OSError('secondary')
            return write(path, value, **kwargs)
        with patch.object(N.subprocess, 'Popen', side_effect=self.spawn(child, frames)), \
                patch.object(N, '_process_start', return_value='start'), patch.object(N.os, 'killpg') as stop, \
                patch.object(N, '_write', side_effect=fail_final):
            with self.assertRaises(KeyboardInterrupt) as caught: N.read_describe(TABLE)
            self.assertIs(caught.exception, original)
            stop.assert_called_once_with(child.pid, signal.SIGINT)

    def test_timeout_with_unknown_cleanup_is_not_retried(self):
        child = Child(subprocess.TimeoutExpired('describe', 240), stays_alive=True)
        with patch.object(N.subprocess, 'Popen', side_effect=self.spawn(child, describe_frames()[:1])) as spawn, \
                patch.object(N, '_process_start', return_value='start'), patch.object(N.os, 'killpg') as stop, \
                patch.object(N, 'CLEANUP_TIMEOUT', 0):
            with self.assertRaises(N.NotebookError): N.read_describe(TABLE)
            self.assertEqual(spawn.call_count, 1)
            stop.assert_called_once_with(child.pid, signal.SIGINT)
        self.assertEqual((self.status()['state'], self.status()['child_exited'], self.status()['cleanup_verified']),
                         ('unknown', False, False))


if __name__ == '__main__': unittest.main()
