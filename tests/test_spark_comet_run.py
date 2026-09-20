"""Fixed container ownership and cancellation with a fake Docker boundary."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.spark_comet import run as R
from test_local_spark_iceberg_run import FakeDocker as IcebergFake, container as iceberg_container

TOKEN, BUILD, IMAGE, CID = '1' * 32, '2' * 32, 'sha256:' + 'a' * 64, 'c' * 64


def container():
    value = iceberg_container()
    value['Name'] = '/' + R.name(TOKEN)
    value['Config']['Labels'][R.LABEL] = R.OWNER
    value['Config']['Cmd'] = R.C.spark_arguments(TOKEN)
    return value


class FakeDocker(IcebergFake):
    def create(self, run_token, image):
        self.calls.append('create'); self.value = container()
        if self.create_error: raise self.create_error
        return CID


class RunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name).resolve()/TOKEN; self.output.mkdir(mode=0o700)
        self.docker = FakeDocker()
        for handle in [patch.object(R.B, 'sources', return_value={'fixed': 'a' * 64}),
                       patch.object(R.C, 'parse_output', return_value={'result': 'passed'})]:
            handle.start(); self.addCleanup(handle.stop)

    def run_case(self):
        return R.Experiment(TOKEN, IMAGE, BUILD, self.output, docker=self.docker, sleep=lambda _: None).run()

    def test_success_owns_one_offline_container_and_removes_it(self):
        value = self.run_case()
        self.assertEqual(value['result'], 'passed'); self.assertTrue(value['cleanup']['verified'])
        self.assertEqual(self.docker.calls.count('create'), 1)
        args = R.create_arguments(TOKEN, IMAGE)
        self.assertIn('none', args); self.assertIn('--read-only', args)
        self.assertIn('spark.plugins=org.apache.spark.CometPlugin', args)
        self.assertFalse(any(a in args for a in ('--privileged', '--publish', '--mount', '--volume')))
        self.assertFalse(any(a.startswith(('HOME=', 'home=', 'CODEX_HOME=')) for a in args))

    def test_image_home_is_inherited_without_fixed_override(self):
        value = container()
        value['Config']['Env'] = [e for e in value['Config']['Env'] if not e.startswith('HOME=')] + ['HOME=/home/spark']
        self.assertEqual(R.validate_container(value, TOKEN, IMAGE, CID), value)

    def test_changed_ownership_never_stops_or_removes_foreign_container(self):
        self.docker.mutate_start = lambda d: d.value['Config']['Labels'].update({R.LABEL: 'other'})
        with self.assertRaisesRegex(Exception, 'CONTAINER_OWNER_CHANGED'): self.run_case()
        self.assertNotIn('stop', self.docker.calls); self.assertNotIn('remove', self.docker.calls)
        saved = json.loads((self.output/'summary.json').read_bytes())
        self.assertFalse(saved['cleanup']['verified'])

    def test_unknown_create_never_adopts_or_deletes_by_name(self):
        self.docker.create_error = OSError('lost reply')
        with self.assertRaises(OSError): self.run_case()
        saved = json.loads((self.output/'summary.json').read_bytes())
        self.assertIsNone(saved['container_id']); self.assertFalse(saved['cleanup']['verified'])
        self.assertNotIn('stop', self.docker.calls); self.assertNotIn('remove', self.docker.calls)

    def test_correct_json_cannot_override_failed_process(self):
        self.docker.exit_code = 1
        with self.assertRaisesRegex(Exception, 'WORKLOAD_EXIT_FAILURE'): self.run_case()
        self.assertIn('remove', self.docker.calls)

    def test_first_cancellation_survives_later_cleanup_or_save_failure(self):
        first = KeyboardInterrupt('first')
        self.docker.start_error = first
        with patch.object(self.docker, 'remove', side_effect=OSError('cleanup')):
            with self.assertRaises(KeyboardInterrupt) as caught: self.run_case()
        self.assertIs(caught.exception, first)
        saved = json.loads((self.output/'summary.json').read_bytes())
        self.assertFalse(saved['cleanup']['verified'])

    def test_late_cancellation_takes_precedence_over_ordinary_failure(self):
        self.docker.start_error = OSError('start')
        cancel = KeyboardInterrupt('cleanup')
        with patch.object(self.docker, 'remove', side_effect=cancel):
            with self.assertRaises(KeyboardInterrupt) as caught: self.run_case()
        self.assertIs(caught.exception, cancel)

    def test_recovery_preserves_original_receipt_and_targets_saved_id(self):
        saved = self.run_case()
        original = (self.output/'summary.json').read_bytes()
        with patch.object(R, 'STATE', self.output.parent):
            receipt = R.recover(TOKEN, docker=self.docker)
        self.assertTrue(receipt['cleanup']['verified'])
        self.assertEqual((self.output/'summary.json').read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
