"""Saved log-context compatibility through the real SRE status entry point."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_sre_cli import CLI, TOKEN, fixture_documents, write_fixture
from test_local_sre_contract import usage_snapshots, usage_reread
from tools.local_experiment.gateway import pod_logs as L


def saved_log_snapshots(state='observed'):
    snapshots = usage_snapshots()
    pod = snapshots['k8s_read_pods']
    value = L.unknown('READ_FAILED', identity=copy.deepcopy(pod['resource_usage']['identity']), attempts=1,
                      started='2026-09-17T02:00:00.000000000Z',
                      observed='2026-09-17T02:00:01.000000000Z', since='2026-09-17T01:55:00Z')
    if state == 'observed':
        value.update(state='observed', reason=None, binding='name-time-correlated', get_attempts=2,
                     fragment=dict(returned_bytes=0, complete_lines=0, header_lines=0,
                                   warn_header_lines=0, error_header_lines=0, unparsed_lines=0,
                                   first_prefix_at=None, last_prefix_at=None))
    pod['log_signals'] = L.validate(value)
    return snapshots


class SRESavedLogSignalsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for name, value in [('STATE', self.root / 'intents'), ('EVIDENCE', self.root / 'evidence')]:
            patcher = patch.object(CLI, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ('runner', 'configured_runner', 'persist'):
            patcher = patch.object(CLI, name, side_effect=AssertionError('saved status attempted a side effect'))
            patcher.start()
            self.addCleanup(patcher.stop)
        CLI.EVIDENCE.mkdir(mode=0o700)

    def fingerprint(self):
        result = {}
        for path in [self.root, *sorted(self.root.rglob('*'))]:
            s = path.lstat()
            result[str(path.relative_to(self.root))] = (s.st_mode, s.st_mtime_ns, s.st_ctime_ns, s.st_ino,
                hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None)
        return result

    def inspect(self, *, usage=False):
        before = self.fingerprint()
        output = io.StringIO()
        code = CLI.main(['status', '--evidence-token', TOKEN] + (['--usage'] if usage else []), stdout=output)
        self.assertEqual(self.fingerprint(), before)
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]['schema_version'], 2 if usage else 1)
        self.assertEqual(len(rows[-1]), 14 if usage else 13)
        self.assertNotIn('log_signals', output.getvalue())
        self.assertNotIn('PRIVATE', output.getvalue())
        return code, rows[-1]

    def test_saved_empty_fragment_is_context_and_keeps_public_usage_protocol(self):
        snapshots = saved_log_snapshots()
        documents = fixture_documents(initial_snapshots=snapshots)
        write_fixture(CLI.EVIDENCE / TOKEN, documents)
        code, plain = self.inspect()
        self.assertEqual((code, plain['result']), (0, 'passed'))
        code, view = self.inspect(usage=True)
        self.assertEqual((code, view['result']), (0, 'passed'))
        self.assertEqual(view['resource_usage']['initial'], snapshots['k8s_read_pods']['resource_usage'])
        self.assertEqual(self.inspect()[1], plain)
        without = copy.deepcopy(snapshots)
        without['k8s_read_pods'].pop('log_signals')
        old = fixture_documents(initial_snapshots=without)['result.json']['evidence']
        self.assertEqual(documents['result.json']['evidence']['health'], old['health'])
        self.assertEqual(documents['result.json']['evidence']['findings'], old['findings'])

    def test_saved_unknown_logs_keep_null_fragment_without_reobservation(self):
        snapshots = saved_log_snapshots('unknown')
        documents = fixture_documents(initial_snapshots=snapshots)
        write_fixture(CLI.EVIDENCE / TOKEN, documents)
        self.assertIsNone(documents['result.json']['evidence']['observations']['pods']['log_signals']['fragment'])
        self.assertEqual(self.inspect(usage=True)[0], 0)

    def test_followup_reconstructs_requested_overlap_without_count_deltas(self):
        snapshots = saved_log_snapshots()
        pod = snapshots['k8s_read_pods']['pods'][0]
        pod['ready_containers'] = 0
        pod['container_observation']['containers'][0]['ready'] = False
        followup = usage_reread(snapshots)
        followup['k8s_read_pods']['log_signals'].update(
            request_started_at='2026-09-17T02:00:04.000000000Z',
            observed_at='2026-09-17T02:00:05.000000000Z', since_time='2026-09-17T01:55:04Z')
        documents = fixture_documents(initial_snapshots=snapshots, followup_snapshots=followup)
        comparison = documents['result.json']['comparisons']['sources']['k8s_read_pods']['log_signals']
        self.assertEqual(comparison['identity'], 'same')
        self.assertTrue(comparison['requested_window_overlap'])
        self.assertEqual(set(comparison), {'before_state', 'after_state', 'identity', 'requested_window_overlap', 'limitations'})
        write_fixture(CLI.EVIDENCE / TOKEN, documents)
        self.assertEqual(self.inspect(usage=True)[0], 0)

    def test_saved_log_tampering_cannot_escape_status_validation(self):
        for mode in ('raw-field', 'bool-count', 'valid-clock-hash-drift'):
            with self.subTest(mode=mode):
                snapshots = saved_log_snapshots()
                documents = fixture_documents(initial_snapshots=snapshots)
                value = documents['observations.json'][1]['snapshot']['log_signals']
                if mode == 'raw-field':
                    value['raw'] = 'PRIVATE_SQL_AND_CREDENTIALS'
                elif mode == 'bool-count':
                    value['fragment']['complete_lines'] = False
                else:
                    value['observed_at'] = '2026-09-17T02:00:02.000000000Z'
                # Each case uses a new disposable saved directory.
                with tempfile.TemporaryDirectory(dir=self.root) as temporary:
                    with patch.object(CLI, 'EVIDENCE', Path(temporary)):
                        write_fixture(CLI.EVIDENCE / TOKEN, documents)
                        code, view = self.inspect(usage=True)
                self.assertEqual((code, view['result'], view['error_code']), (1, 'unknown', 'SRE_EVIDENCE_INVALID'))
                self.assertEqual(view['resource_usage']['availability'], 'unverified')

    def test_pre_log_receipt_keeps_its_saved_shape(self):
        documents = fixture_documents(initial_snapshots=usage_snapshots())
        self.assertNotIn('log_signals', documents['result.json']['evidence']['observations']['pods'])
        write_fixture(CLI.EVIDENCE / TOKEN, documents)
        self.assertEqual(self.inspect()[0], 0)
        self.assertEqual(self.inspect(usage=True)[0], 0)


if __name__ == '__main__':
    unittest.main()
