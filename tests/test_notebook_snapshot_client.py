"""Snapshot catalog and explicit COUNT clients share the fake-only Reader owner."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.notebook import client as N
from tests.test_local_notebook_client import Child, encoded
from tests.test_local_query_count import request, public as count_public
from tests.test_query_snapshots_contract import listing_request, window, OLD, HEAD

TOKEN = 'a' * 32


def snapshots_public():
    return N.QS.project_snapshots(window(req=listing_request()), listing_request())


def frames(public=None, *, count=False, result='passed', cleanup=True):
    command, version, root = ('query count', 4, 'query-count') if count else (
        'query snapshots', 2, 'query-snapshots')
    if public is None and result == 'passed':
        public = count_public(request(snapshot_id=str(OLD))) if count else snapshots_public()
    return [dict(schema_version=version, event='allocated', command=command, evidence_token=TOKEN),
            dict(schema_version=version, event='result', command=command, evidence_token=TOKEN,
                 result=result, observation=public if result == 'passed' else None,
                 error_code=None if result == 'passed' else 'INTERRUPTED' if result == 'interrupted' else 'QUERY_FAILED',
                 cleanup_verified=cleanup, evidence=f'tmp/local-experiment/{root}/{TOKEN}/summary.json')]


class SnapshotClientTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name).resolve()
        self.output.chmod(0o700)
        self.binary = self.output / 'shirokuma'
        self.binary.write_bytes(b'fake-not-executable')
        environment = patch.dict(os.environ, {'SHIROKUMA_NOTEBOOK_RUN_DIR': str(self.output),
                                              'SHIROKUMA_NOTEBOOK_BINARY': str(self.binary)})
        environment.start()
        self.addCleanup(environment.stop)

    def launch(self, child, saved):
        def fake(argv, **kwargs):
            self.assertTrue(kwargs['start_new_session'])
            self.assertTrue((self.output / 'reader-intent.json').is_file())
            kwargs['stdout'].write(encoded(saved))
            return child
        return fake

    def test_owned_snapshot_catalog_once_exact_argv_and_receipt(self):
        with patch.object(N.subprocess, 'Popen', side_effect=self.launch(Child(), frames())) as launch, \
                patch.object(N, '_process_start', return_value='synthetic child start'):
            observed = N.read_snapshots('smoke.fixture_v1')
            with self.assertRaises(FileExistsError):
                N.read_snapshots('smoke.fixture_v1')
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(observed, snapshots_public())
        self.assertEqual(launch.call_args.args[0], [str(self.binary), '--repo-root', str(N.ROOT),
            '--profile', 'local-experiment', 'query', 'snapshots', '--table', 'smoke.fixture_v1'])
        self.assertEqual(json.loads((self.output / 'reader-intent.json').read_bytes()),
            {'schema_version': 1, 'command': 'query snapshots', 'request': listing_request()})
        status = json.loads((self.output / 'reader-status.json').read_bytes())
        self.assertEqual(status['state'], 'passed')
        self.assertTrue(status['child_exited'] and status['cleanup_verified'])

    def test_owned_historical_count_keeps_filter_and_selected_id(self):
        selected = request(snapshot_id=str(OLD), column='id', literal='1')
        original = deepcopy(selected)
        public = count_public(selected, 1)
        with patch.object(N.subprocess, 'Popen', side_effect=self.launch(Child(), frames(public, count=True))) as launch, \
                patch.object(N, '_process_start', return_value='synthetic child start'):
            observed = N.read_snapshot_count(selected)
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(observed, public)
        self.assertEqual(selected, original)
        self.assertEqual(launch.call_args.args[0], [str(self.binary), '--repo-root', str(N.ROOT),
            '--profile', 'local-experiment', 'query', 'count', '--table', 'smoke.fixture_v1',
            '--where-column', 'id', '--equals', '1', '--snapshot-id', str(OLD)])
        self.assertNotIn('literal', observed['where'])
        self.assertEqual(json.loads((self.output / 'reader-intent.json').read_bytes())['request'], original)
        status = json.loads((self.output / 'reader-status.json').read_bytes())
        self.assertTrue(status['child_exited'] and status['cleanup_verified'])
        for name in ('reader-intent.json', 'reader-status.json', 'reader.stdout.jsonl'):
            self.assertEqual((self.output / name).stat().st_mode & 0o777, 0o600)

    def test_snapshot_listing_preserves_order_compatibility_and_empty(self):
        public = snapshots_public()
        self.assertEqual([x['snapshot_id'] for x in public['snapshots']], [str(OLD), str(HEAD)])
        public['snapshots'][0]['schema_compatibility'] = 'schema-mismatch'
        _, observed, cleanup = N._snapshots_reader_frames(encoded(frames(public)), 0, 'smoke.fixture_v1')
        self.assertEqual(observed, public)
        self.assertTrue(cleanup)
        empty = dict(public, current_snapshot_id=None, snapshots=[])
        self.assertEqual(N._snapshots_reader_frames(encoded(frames(empty)), 0, 'smoke.fixture_v1')[1], empty)

    def test_count_zero_and_int64_max_are_exact_at_explicit_snapshot(self):
        for snapshot in (OLD, HEAD):
            selected = request(snapshot_id=str(snapshot))
            for number in (0, 2**63 - 1):
                with self.subTest(snapshot=snapshot, number=number):
                    public = count_public(selected, number)
                    _, observed, cleanup = N._snapshot_count_reader_frames(encoded(frames(public, count=True)), 0, selected)
                    self.assertEqual(observed['count'], str(number))
                    self.assertEqual(observed['snapshot_id'], str(snapshot))
                    self.assertEqual(observed['current_snapshot_id'], str(HEAD))
                    self.assertTrue(cleanup)
                    self.assertNotIn('rows', observed)

    def test_invalid_snapshot_or_table_refused_before_any_intent_or_child(self):
        for snapshot in (None, True, 1, 1.0, '0', '01', '-1', '1.0', str(2**63), '１'):
            selected = dict(request(), snapshot_id=snapshot)
            with self.subTest(snapshot=snapshot), patch.object(N.subprocess, 'Popen') as launch, \
                    self.assertRaises((N.NotebookError, N.QC.ContractError)):
                N.read_snapshot_count(selected)
            launch.assert_not_called()
            self.assertFalse((self.output / 'reader-intent.json').exists())
        for table in ('other.fixture', 'smoke.fixture;DROP TABLE x', '', None):
            with patch.object(N.subprocess, 'Popen') as launch, self.assertRaises(N.NotebookError):
                N.read_snapshots(table)
            launch.assert_not_called()
            self.assertFalse((self.output / 'reader-intent.json').exists())

    def test_current_only_count_still_refuses_historical_requests(self):
        selected = request(snapshot_id=str(OLD))
        with patch.object(N.subprocess, 'Popen') as launch, self.assertRaises(N.NotebookError):
            N.read_count(selected)
        launch.assert_not_called()
        with self.assertRaises(N.NotebookError):
            N._count_reader_frames(encoded(frames(count=True)), 0, selected)

    def test_snapshot_envelope_and_public_binding_reject_wrong_identity_or_limits(self):
        for key, value in (('command', 'query tables'), ('schema_version', 1),
                           ('evidence_token', 'b' * 32), ('evidence', 'tmp/not-ours')):
            saved = frames()
            saved[-1][key] = value
            with self.subTest(key=key), self.assertRaises(N.NotebookError):
                N._snapshots_reader_frames(encoded(saved), 0, 'smoke.fixture_v1')
        public = snapshots_public()
        invalid = [dict(public, table='iceberg.smoke.other'), dict(public, schema_version=1),
                   dict(public, complete=False), dict(public, current_snapshot_id='999'),
                   dict(public, snapshots=public['snapshots'] * 33), dict(public, extra='private')]
        for changed in invalid:
            with self.subTest(public=changed), self.assertRaises(N.NotebookError):
                N._snapshots_reader_frames(encoded(frames(changed)), 0, 'smoke.fixture_v1')

    def test_count_result_must_match_selected_snapshot_and_canonical_integer(self):
        selected = request(snapshot_id=str(OLD))
        public = count_public(selected)
        invalid = [dict(public, table='iceberg.smoke.other'), dict(public, snapshot_id=str(HEAD)),
                   dict(public, count=True), dict(public, count=0), dict(public, count='01'),
                   dict(public, count=str(2**63)), dict(public, current_snapshot_id=None)]
        for changed in invalid:
            with self.subTest(public=changed), self.assertRaises(N.NotebookError):
                N._snapshot_count_reader_frames(encoded(frames(changed, count=True)), 0, selected)
        changed = deepcopy(public)
        changed['selected_snapshot']['schema_compatibility'] = 'schema-mismatch'
        with self.assertRaises(N.NotebookError):
            N._snapshot_count_reader_frames(encoded(frames(changed, count=True)), 0, selected)

    def test_failed_reads_are_unavailable_and_preserve_cleanup_state(self):
        for count in (False, True):
            for cleanup in (False, True):
                raw = encoded(frames(count=count, result='failed', cleanup=cleanup))
                result = (N._snapshot_count_reader_frames(raw, 1, request(snapshot_id=str(OLD))) if count else
                          N._snapshots_reader_frames(raw, 1, 'smoke.fixture_v1'))
                self.assertEqual(result, (TOKEN, None, cleanup))

    def test_snapshot_first_interrupt_survives_secondary_signal_failure(self):
        first = KeyboardInterrupt()
        with patch.object(N.subprocess, 'Popen', side_effect=self.launch(Child(initial=first), frames(result='interrupted'))), \
                patch.object(N, '_process_start', return_value='synthetic child start'), \
                patch.object(N.os, 'killpg', side_effect=OSError('secondary')), \
                self.assertRaises(KeyboardInterrupt) as caught:
            N.read_snapshots('smoke.fixture_v1')
        self.assertIs(caught.exception, first)
        status = json.loads((self.output / 'reader-status.json').read_bytes())
        self.assertEqual(status['state'], 'interrupted')
        self.assertTrue(status['child_exited'] and status['cleanup_verified'])

    def test_historical_count_timeout_unknown_cleanup_returns_no_count(self):
        first = subprocess.TimeoutExpired('fixed Reader', 240)
        child = Child(initial=first, stays_alive=True)
        with patch.object(N.subprocess, 'Popen', side_effect=self.launch(child, frames(count=True))), \
                patch.object(N, '_process_start', return_value='synthetic child start'), \
                patch.object(N, 'CLEANUP_TIMEOUT', 0), patch.object(N.os, 'killpg') as signal, \
                self.assertRaises(N.NotebookError):
            N.read_snapshot_count(request(snapshot_id=str(OLD)))
        self.assertEqual(signal.call_count, 1)
        status = json.loads((self.output / 'reader-status.json').read_bytes())
        self.assertFalse(status['cleanup_verified'])
        self.assertFalse(status['child_exited'])


if __name__ == '__main__':
    unittest.main()
