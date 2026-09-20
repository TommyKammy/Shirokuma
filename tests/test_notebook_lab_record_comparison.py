"""Fake-only saved-comparison recording, worker ownership and restoration."""
from contextlib import contextmanager
from copy import deepcopy
import os
from pathlib import Path
import signal
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.notebook_lab import record_comparison as R

TOKEN = 'a' * 32
ATTEMPT = 'b' * 32
STORE = '00000000-0000-4000-8000-000000000001'


def projection():
    return {'comparison': {'identity': {'store_id': STORE}, 'candidate': {'alpha': 10.0}}}


def receipt(token=TOKEN):
    return dict(result='verified', comparison_token=token, source_unchanged=True,
                store_id=STORE, run_id='c' * 32, created_runs=1, projection_sha256=R.I.digest(projection()))


class Runtime:
    def __init__(self, state='running'):
        self.state = state; self.calls = []
    def status(self):
        self.calls.append('status')
        return dict(state=self.state, store_id=STORE, healthy=self.state == 'running',
                    identity={'leader': 60000} if self.state == 'running' else None)
    def stop(self): self.calls.append('stop'); self.state = 'stopped'
    def start(self): self.calls.append('start'); self.state = 'running'
    def require_stopped(self):
        if self.state != 'stopped': raise ValueError('not stopped')
        return dict(state='stopped', store_id=STORE)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); self.state = self.root / 'recordings'
        self.state.mkdir(mode=0o700); self.directory = self.state / ATTEMPT
        p = patch.object(R, 'STATE', self.state); p.start(); self.addCleanup(p.stop)
        p = patch.object(R.uuid, 'uuid4', return_value=SimpleNamespace(hex=ATTEMPT)); p.start(); self.addCleanup(p.stop)
        p = patch.object(R.I, 'Snapshot', return_value=Mock(load=Mock(side_effect=projection)))
        p.start(); self.addCleanup(p.stop)

    def bind(self, pid=None, start=None):
        self.directory.mkdir(mode=0o700, exist_ok=True)
        request = dict(schema_version=1, comparison_token=TOKEN)
        R.N._write(self.directory / 'input.json', request)
        R.N._write(self.directory / 'intent.json', dict(schema_version=1, profile=R.PROFILE,
                    argv=R.arguments(self.directory), request_sha256=R.I.digest(request)))
        status = dict(schema_version=1, state='running' if pid else 'pending', pid=pid, pgid=pid,
                      process_start=start, evidence_token=ATTEMPT, exit_code=None, child_exited=False,
                      cleanup_verified=False, error_code=None)
        R.N._write(self.directory / 'status.json', status)
        return status

    def summary(self, *, passed=True, restored=True):
        before = Runtime().status()
        return dict(schema_version=1, profile=R.PROFILE, comparison_token=TOKEN,
                    result='passed' if passed else 'failed' if restored else 'unknown',
                    failure_phase=None if passed else 'import', error_code=None if passed else 'LAB_RECORD_FAILED',
                    runtime_before=before, runtime_after=before if restored else None,
                    runtime_restored=restored, backup=None, import_receipt=receipt() if passed else None,
                    cleanup_verified=restored)


class WorkerTests(Fixture):
    def setUp(self):
        super().setUp(); self.directory.mkdir(mode=0o700)
        self.source = Mock()
        p = patch.object(R.I, 'Snapshot', return_value=self.source); p.start(); self.addCleanup(p.stop)
        self.importer = Mock(return_value=0)
        self.export = Mock(return_value=dict(result='verified', store_id=STORE))

    def execute(self, runtime, recorded=None):
        values = [None, receipt()] if recorded is None else recorded
        with patch.object(R.I, 'recorded', side_effect=values):
            return R.execute(self.directory, TOKEN, runtime=runtime, export=self.export, importer=self.importer)

    def test_running_store_cold_backup_one_import_verified_then_restored(self):
        runtime = Runtime()
        self.assertEqual(self.execute(runtime), 0)
        self.assertEqual(runtime.calls, ['status', 'stop', 'status', 'start', 'status'])
        self.export.assert_called_once_with(self.directory / 'cold-backup')
        self.importer.assert_called_once_with(TOKEN)
        self.assertEqual(self.source.check.call_count, 2)
        summary = R._summary(self.directory, TOKEN)
        self.assertEqual(summary['result'], 'passed'); self.assertTrue(summary['cleanup_verified'])
        self.assertEqual(summary['import_receipt'], receipt())

    def test_original_stopped_state_is_preserved(self):
        runtime = Runtime('stopped')
        self.assertEqual(self.execute(runtime), 0)
        self.assertEqual(runtime.calls, ['status', 'status'])
        self.assertTrue(R._summary(self.directory, TOKEN)['runtime_restored'])

    def test_lost_import_reply_restores_runtime_without_resend(self):
        runtime = Runtime(); self.importer.side_effect = OSError('private SDK response')
        self.assertEqual(self.execute(runtime), 1)
        self.importer.assert_called_once()
        value = R._summary(self.directory, TOKEN)
        self.assertEqual(value['result'], 'failed'); self.assertTrue(value['runtime_restored'])
        self.assertEqual(value['failure_phase'], 'import'); self.assertIsNone(value['import_receipt'])
        self.assertNotIn('private SDK response', (self.directory / 'worker-summary.json').read_text())

    def test_first_cancel_preserved_when_runtime_restoration_also_fails(self):
        runtime = Runtime(); runtime.start = Mock(side_effect=OSError('second'))
        first = KeyboardInterrupt('first'); self.importer.side_effect = first
        with self.assertRaises(KeyboardInterrupt) as caught: self.execute(runtime)
        self.assertIs(caught.exception, first)
        self.assertFalse(R._summary(self.directory, TOKEN)['cleanup_verified'])
        self.importer.assert_called_once(); runtime.start.assert_called_once()

    def test_failed_backup_never_enters_importer_and_restores_original_runtime(self):
        runtime = Runtime(); self.export.side_effect = OSError('copy failed')
        self.assertEqual(self.execute(runtime), 1)
        self.importer.assert_not_called()
        value = R._summary(self.directory, TOKEN)
        self.assertEqual(value['failure_phase'], 'backup'); self.assertTrue(value['runtime_restored'])

    def test_saved_import_is_not_success_if_source_changes_during_restoration(self):
        self.source.check.side_effect = [None, ValueError('source drift')]
        self.assertEqual(self.execute(Runtime()), 1)
        value = R._summary(self.directory, TOKEN)
        self.assertEqual(value['failure_phase'], 'source-after')
        self.assertTrue(value['cleanup_verified']); self.assertEqual(value['import_receipt'], receipt())

    def test_worker_binds_request_argv_and_parent_launch_before_effects(self):
        self.bind(os.getpid(), 'start')
        R.N._write(self.directory / 'launch.json', dict(pid=os.getpid(), ppid=os.getppid(),
                   pgid=os.getpid(), process_start='start'))
        with patch.object(R.N, '_process_start', return_value='start'), \
             patch.object(R.os, 'getpgrp', return_value=os.getpid()), patch.object(R, 'execute', return_value=0) as execute:
            self.assertEqual(R.worker(self.directory), 0); execute.assert_called_once_with(self.directory, TOKEN)
            bad = dict(R._input(self.directory), comparison_token='d' * 32)
            R.N._write(self.directory / 'input.json', bad, replace=True)
            with self.assertRaises(R.N.NotebookError): R.worker(self.directory)
            self.assertEqual(execute.call_count, 1)


class ParentTests(Fixture):
    def setUp(self):
        super().setUp()
        @contextmanager
        def locked(): yield
        p=patch.object(R, '_lock', locked); p.start(); self.addCleanup(p.stop)
        self.child = Mock(pid=60001); self.child.returncode = None
        self.child.poll.side_effect = lambda: self.child.returncode
        self.interrupt = None; self.waits = 0; self.restored = True; self.wrong_receipt = False
        def wait(timeout):
            self.waits += 1
            if self.interrupt is not None and self.waits == 1: raise self.interrupt
            self.child.returncode = 130 if self.interrupt else 0
            R.N._write(self.directory / 'worker-summary.json', self.summary(
                passed=self.interrupt is None and self.restored, restored=self.restored))
        self.child.wait.side_effect=wait
        self.row=dict(pid=60001, ppid=os.getpid(), pgid=60001, uid=os.getuid(), start_time='start',
                      command=' '.join(R.arguments(self.directory)))
        for target, name, kwargs in [(R.M, 'processes', {'return_value': {60001:self.row}}),
                                     (R.N, '_process_start', {'return_value':'start'}),
                                     (R.T, 'group_empty', {'return_value':True})]:
            p=patch.object(target,name,**kwargs);p.start();self.addCleanup(p.stop)
        p=patch.object(R.subprocess, 'Popen', return_value=self.child);self.spawn=p.start();self.addCleanup(p.stop)
        p=patch.object(R.os, 'kill');self.kill=p.start();self.addCleanup(p.stop)
        def recorded(token):
            if not self.waits: return None
            return receipt('d'*32 if self.wrong_receipt else token)
        p=patch.object(R.I, 'recorded', side_effect=recorded);p.start();self.addCleanup(p.stop)

    def test_input_changed_after_worker_is_rejected_before_publication(self):
        original=self.child.wait.side_effect
        def wait(timeout):
            original(timeout)
            R.N._write(self.directory/'input.json',dict(schema_version=1,comparison_token='d'*32),replace=True)
        self.child.wait.side_effect=wait
        with self.assertRaises(R.N.NotebookError):R.record_comparison(TOKEN)
        self.assertFalse((self.directory/'result.json').exists())
        self.assertTrue(R.recording_status(self.directory)['cleanup_verified'])

    def test_owned_worker_success_parent_environment_unchanged_and_saved_result_bound(self):
        before=dict(os.environ)
        result=R.record_comparison(TOKEN)
        self.assertEqual(os.environ,before);self.assertFalse(result['reused'])
        self.assertEqual(result['attempt_token'],ATTEMPT)
        self.assertEqual(self.spawn.call_args.kwargs['env'],R.M.environment())
        self.assertTrue(self.spawn.call_args.kwargs['start_new_session'])
        self.assertTrue(R.recording_closed(self.directory));self.kill.assert_not_called()

    def test_completed_record_fastpath_allocates_no_worker_backup_or_new_attempt(self):
        with patch.object(R.I, 'recorded', return_value=receipt()),patch.object(R.B,'export_stopped') as backup:
            result=R.record_comparison(TOKEN)
        self.assertTrue(result['reused']);self.assertIsNone(result['attempt_token'])
        self.assertEqual(list(self.state.iterdir()),[]);self.spawn.assert_not_called();backup.assert_not_called()

    def test_completed_receipt_cannot_be_mixed_with_another_validated_candidate_in_same_store(self):
        changed=projection();changed['comparison']['candidate']['alpha']=2.0
        with patch.object(R.I,'recorded',return_value=receipt()), \
             patch.object(R.I,'Snapshot',return_value=Mock(load=Mock(return_value=changed))):
            with self.assertRaises(R.N.NotebookError):R.record_comparison(TOKEN)
        self.spawn.assert_not_called();self.assertEqual(list(self.state.iterdir()),[])

    def test_pending_import_or_bad_token_is_rejected_before_new_worker(self):
        with patch.object(R.I,'recorded',side_effect=R.I.ImportError('COMPARISON_IMPORT_PRIOR_OUTCOME_UNKNOWN')):
            with self.assertRaises(R.N.NotebookError):R.record_comparison(TOKEN)
        with self.assertRaises(R.N.NotebookError):R.record_comparison('../invalid')
        self.spawn.assert_not_called();self.assertEqual(list(self.state.iterdir()),[])

    def test_returned_import_mismatch_releases_no_public_result_but_keeps_cleanup(self):
        self.wrong_receipt=True
        with self.assertRaises(R.N.NotebookError):R.record_comparison(TOKEN)
        self.assertFalse((self.directory/'result.json').exists())
        self.assertTrue(R.recording_status(self.directory)['cleanup_verified'])

    def test_cancel_interrupts_only_owned_worker_once_and_preserves_first_object(self):
        self.interrupt=KeyboardInterrupt('first')
        with self.assertRaises(KeyboardInterrupt) as caught:R.record_comparison(TOKEN)
        self.assertIs(caught.exception,self.interrupt)
        self.kill.assert_called_once_with(60001,signal.SIGINT)
        self.assertEqual(R.recording_status(self.directory)['state'],'interrupted')

    def test_unrestored_runtime_blocks_idle_and_never_publishes_success(self):
        self.restored=False
        with self.assertRaises(R.N.NotebookError):R.record_comparison(TOKEN)
        self.assertFalse(R.check_idle());self.assertFalse((self.directory/'result.json').exists())
        self.assertEqual(R.recording_status(self.directory)['state'],'unknown')

    def test_parent_ownership_mismatch_never_releases_worker_handshake(self):
        self.row['command']='unexpected executable'
        with self.assertRaises(R.N.NotebookError):R.record_comparison(TOKEN)
        self.assertFalse((self.directory/'launch.json').exists())
        self.kill.assert_called_once_with(60001,signal.SIGINT)


class LockTests(Fixture):
    def test_lab_owner_lock_busy_prevents_lookup_allocation_and_worker(self):
        from tools.local_experiment.notebook_lab import runtime as L
        with patch.object(L,'_lock',side_effect=L.LabError('LAB_BUSY')), \
             patch.object(R.I,'recorded') as recorded,patch.object(R.subprocess,'Popen') as spawn:
            with self.assertRaisesRegex(R.N.NotebookError,'LAB_RECORD_BUSY'):R.record_comparison(TOKEN)
        recorded.assert_not_called();spawn.assert_not_called();self.assertEqual(list(self.state.iterdir()),[])


if __name__=='__main__':unittest.main()
