"""Offline image provenance, no-overwrite and interruption boundaries."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.spark_iceberg import build as B

TOKEN = 'a' * 32
IMAGE_ID = 'sha256:' + 'b' * 64
LAYERS = ['sha256:' + format(index, '064x') for index in range(11)]
BASE = {'Id': B.P.DIGEST, 'Architecture': 'arm64', 'Os': 'linux', 'Size': 1000,
        'Config': {'User': 'spark', 'Entrypoint': ['/opt/entrypoint.sh'], 'WorkingDir': '/opt/spark/work-dir',
                   'Env': ['SPARK_HOME=/opt/spark'], 'Cmd': None, 'Labels': {'base': 'official'}},
        'RootFS': {'Layers': LAYERS}}
PROOF = {'rootfs_diff_ids': LAYERS, 'image': B.BASE.IMAGE}


def image(recipe):
    return {'Id': IMAGE_ID, 'Descriptor': {'digest': IMAGE_ID}, 'Architecture': 'arm64', 'Os': 'linux', 'Size': 2000,
            'RepoTags': [recipe['tag']], 'RepoDigests': [B.REPOSITORY + '@' + IMAGE_ID],
            'Config': dict(deepcopy(BASE['Config']), User='185:185', Labels={'base': 'official', **recipe['labels']}),
            'RootFS': {'Layers': LAYERS + ['sha256:' + 'c' * 64]}}


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.state, self.prepared = self.root/'builds', self.root/'prepared'
        self.prepared.mkdir(mode=0o700)
        folder = self.prepared/TOKEN
        folder.mkdir(mode=0o700)
        self.artifacts = []
        for index, name in enumerate(B.C.JARS):
            path = folder/name
            raw = b'fake verified JAR bytes ' + bytes([index])
            path.write_bytes(raw); path.chmod(0o600)
            self.artifacts.append({'artifact': name.removesuffix('.jar'), 'path': str(path), 'size': len(raw),
                                   'sha256': hashlib.sha256(raw).hexdigest(), 'sha512': hashlib.sha512(raw).hexdigest()})
        self.patches = [patch.object(B, 'ROOT', self.root), patch.object(B, 'STATE', self.state),
                        patch.object(B.J, 'STATE', self.prepared),
                        patch.object(B.J, 'verified_artifacts', side_effect=lambda token: deepcopy(self.artifacts)),
                        patch.object(B, 'sources', return_value={'fixed.py': 'e' * 64}),
                        patch.object(B.P, 'guard', return_value={'docker_id': 'owned', 'node_uid': 'owned'}),
                        patch.object(B, 'cached_base', return_value=(deepcopy(BASE), deepcopy(PROOF)))]
        for handle in self.patches:
            handle.start()
        self.calls, self.images = [], {}
        self.on_build = None

    def tearDown(self):
        for handle in reversed(self.patches):
            handle.stop()
        self.temp.cleanup()

    def command(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        self.assertEqual(argv[:3], ['docker', '--context', B.P.CONTEXT])
        args = argv[3:]
        if args[:2] == ['image', 'ls']:
            tag = next(value.split('=', 1)[1] for value in args if value.startswith('reference='))
            return self.images[tag]['Id'] + '\n' if tag in self.images else ''
        if args[:2] == ['image', 'inspect']:
            return json.dumps([self.images[args[2]]])
        if args[0] == 'build':
            recipe = B.inputs(TOKEN)[0]
            if self.on_build:
                self.on_build()
            self.images[recipe['tag']] = image(recipe)
            return 'built'
        raise AssertionError('UNEXPECTED_COMMAND')

    def build(self):
        return B.build(TOKEN, command=self.command)

    def receipts(self):
        return [json.loads(path.read_bytes()) for path in self.state.glob('*/summary.json')]

    def test_fixed_offline_build_and_readback(self):
        receipt = self.build()
        self.assertEqual(receipt['result'], 'passed')
        self.assertEqual(receipt['image_id'], IMAGE_ID)
        self.assertEqual(receipt['image'], B.REPOSITORY + '@' + IMAGE_ID)
        self.assertFalse(receipt['runtime_started'])
        self.assertFalse(receipt['reused'])
        build = next(args for args, _ in self.calls if args[3] == 'build')
        self.assertIn('--pull=false', build)
        self.assertIn('--network=none', build)
        self.assertIn('linux/arm64', build)
        self.assertFalse(any('pull' == arg or 'push' == arg or 'run' == arg for args, _ in self.calls for arg in args))
        context = self.state/receipt['build_token']/'context'
        dockerfile = (context/'Dockerfile').read_text()
        self.assertTrue(dockerfile.startswith('FROM ' + B.BASE.IMAGE + '\nUSER 185:185\n'))
        self.assertEqual(dockerfile.count('COPY '), 1)
        self.assertNotIn('RUN ', dockerfile)
        self.assertEqual(len(receipt['context']['files']), 4)
        for path in context.rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)
        self.assertEqual(B.read_verified_image(receipt['build_token'], command=self.command), receipt)

    def test_same_inputs_reuse_without_rebuild_or_overwrite(self):
        first = self.build()
        initial = deepcopy(self.images)
        self.calls.clear()
        second = self.build()
        self.assertTrue(second['reused'])
        self.assertNotEqual(first['build_token'], second['build_token'])
        self.assertEqual(first['inputs']['tag'], second['inputs']['tag'])
        self.assertEqual(self.images, initial)
        self.assertFalse(any(args[3] == 'build' for args, _ in self.calls))

    def test_existing_tag_with_foreign_label_refuses_overwrite(self):
        recipe = B.inputs(TOKEN)[0]
        self.images[recipe['tag']] = image(recipe)
        self.images[recipe['tag']]['Config']['Labels']['shirokuma.io.component'] = 'foreign'
        with self.assertRaises(B.ExperimentError):
            self.build()
        self.assertFalse(any(args[3] == 'build' for args, _ in self.calls))
        self.assertEqual(self.receipts()[0]['result'], 'failed')

    def test_foreign_arch_user_entrypoint_base_layers_and_extra_layer_refused(self):
        recipe = B.inputs(TOKEN)[0]
        for change in (lambda v: v.__setitem__('Architecture', 'amd64'),
                       lambda v: v['Config'].__setitem__('User', '0'),
                       lambda v: v['Config'].__setitem__('Entrypoint', ['/bin/sh']),
                       lambda v: v['RootFS']['Layers'].__setitem__(0, 'sha256:' + 'f' * 64),
                       lambda v: v['RootFS']['Layers'].append('sha256:' + 'd' * 64)):
            value = image(recipe); change(value)
            with self.assertRaises(B.ExperimentError):
                B.verify_image(value, recipe, BASE, PROOF)

    def test_jar_tamper_symlink_hardlink_and_permissions_stop_before_docker(self):
        path = Path(self.artifacts[0]['path'])
        raw = path.read_bytes()
        for attack in ('tamper', 'symlink', 'hardlink', 'mode'):
            with self.subTest(attack=attack):
                path.unlink(); path.write_bytes(raw); path.chmod(0o600)
                companion = path.with_name('outside.jar')
                if companion.exists():
                    companion.unlink()
                if attack == 'tamper':
                    path.write_bytes(b'x' * len(raw))
                elif attack == 'symlink':
                    path.rename(companion); path.symlink_to(companion)
                elif attack == 'hardlink':
                    os.link(path, companion)
                else:
                    path.chmod(0o644)
                self.calls.clear()
                with self.assertRaises((B.ExperimentError, OSError)):
                    self.build()
                self.assertEqual(self.calls, [])

    def test_arbitrary_tokens_or_paths_rejected(self):
        for value in ('../' + TOKEN, 'A' * 32, '/tmp/plan', '', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                B.build(value, command=self.command)
        self.assertEqual(self.calls, [])

    def test_inputs_changed_after_build_leave_failed_pending_receipt(self):
        def changed():
            B.sources.return_value = {'fixed.py': 'f' * 64}
        self.on_build = changed
        with self.assertRaises(B.ExperimentError):
            self.build()
        receipt = self.receipts()[0]
        self.assertEqual(receipt['result'], 'failed')
        self.assertEqual(receipt['pending']['operation'], 'build')

    def test_build_cancellation_retains_pending_and_original_signal(self):
        signal = KeyboardInterrupt()
        self.on_build = lambda: (_ for _ in ()).throw(signal)
        with self.assertRaises(KeyboardInterrupt) as raised:
            self.build()
        self.assertIs(raised.exception, signal)
        receipt = self.receipts()[0]
        self.assertEqual(receipt['result'], 'failed')
        self.assertEqual(receipt['pending']['operation'], 'build')

    def test_cancel_survives_secondary_receipt_write_failure(self):
        signal = KeyboardInterrupt()
        original = B.persist
        def persist(output, value):
            if value['result'] == 'failed':
                raise OSError('disk unavailable')
            original(output, value)
        self.on_build = lambda: (_ for _ in ()).throw(signal)
        with patch.object(B, 'persist', side_effect=persist), self.assertRaises(KeyboardInterrupt) as raised:
            self.build()
        self.assertIs(raised.exception, signal)

    def test_pending_intent_write_failure_prevents_build(self):
        original = B.persist
        def persist(output, value):
            if value.get('pending') and value['result'] == 'running':
                raise OSError('disk unavailable')
            original(output, value)
        with patch.object(B, 'persist', side_effect=persist), self.assertRaises(OSError):
            self.build()
        self.assertFalse(any(args[3] == 'build' for args, _ in self.calls))

    def test_completed_context_tamper_or_extra_symlink_rejected_on_read(self):
        receipt = self.build()
        context = self.state/receipt['build_token']/'context'
        (context/'unexpected').symlink_to(context/'rootfs', target_is_directory=True)
        with self.assertRaises(B.ExperimentError):
            B.read_verified_image(receipt['build_token'], command=self.command)
        (context/'unexpected').unlink()
        (context/'rootfs/opt/shirokuma/check.py').write_bytes(b'changed')
        with self.assertRaises(B.ExperimentError):
            B.read_verified_image(receipt['build_token'], command=self.command)

    def test_tag_replacement_rejected_by_saved_image_identity(self):
        receipt = self.build()
        new_id = 'sha256:' + 'd' * 64
        value = self.images[receipt['inputs']['tag']]
        value.update(Id=new_id, Descriptor={'digest': new_id}, RepoDigests=[B.REPOSITORY + '@' + new_id])
        with self.assertRaises(B.ExperimentError):
            B.read_verified_image(receipt['build_token'], command=self.command)

    def test_actual_context_changed_during_build_does_not_pass_original_input_hashes(self):
        def changed():
            path = next(self.state.glob('*/context/rootfs/opt/shirokuma/check.py'))
            path.write_bytes(b'changed COPY input')
        self.on_build = changed
        with self.assertRaisesRegex(B.ExperimentError, 'CONTEXT_CONTENT_CHANGED'):
            self.build()
        receipt = self.receipts()[0]
        self.assertEqual(receipt['result'], 'failed')
        self.assertEqual(receipt['pending']['operation'], 'build')

    def test_context_and_saved_manifest_resealed_together_still_conflict_with_recipe(self):
        receipt = self.build()
        output = self.state/receipt['build_token']
        target = output/'context/rootfs/opt/shirokuma/check.py'
        target.write_bytes(b'changed COPY input')
        receipt['context']['files']['rootfs/opt/shirokuma/check.py'] = B.digest(target.read_bytes())
        B.persist(output, receipt)
        with self.assertRaisesRegex(B.ExperimentError, 'CONTEXT_CONTENT_CHANGED'):
            B.read_verified_image(receipt['build_token'], command=self.command)

    def test_tag_derivation_changes_for_script_sources_and_jar_bytes(self):
        first = B.inputs(TOKEN)[0]
        with patch.object(B.C, 'job_script', return_value='different fixed script\n'):
            self.assertNotEqual(B.inputs(TOKEN)[0]['tag'], first['tag'])
        with patch.object(B, 'sources', return_value={'fixed.py': 'f' * 64}):
            self.assertNotEqual(B.inputs(TOKEN)[0]['tag'], first['tag'])
        path = Path(self.artifacts[0]['path']); raw = b'other jar'
        path.write_bytes(raw)
        self.artifacts[0].update(size=len(raw), sha256=hashlib.sha256(raw).hexdigest(), sha512=hashlib.sha512(raw).hexdigest())
        self.assertNotEqual(B.inputs(TOKEN)[0]['tag'], first['tag'])


if __name__ == '__main__':
    unittest.main()
