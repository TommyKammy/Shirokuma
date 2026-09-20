"""Fake image builder: source pin, old layers, offline flags and input drift."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.spark_comet import build as B

PREPARED, BASE_TOKEN = 'a' * 32, 'b' * 32
IMAGE_ID = 'sha256:' + 'c' * 64
LAYERS = ['sha256:' + 'd' * 64, 'sha256:' + 'e' * 64]
BASE = {'Id': 'sha256:' + 'f' * 64, 'Architecture': 'arm64', 'Os': 'linux', 'Size': 1000,
    'Config': {'User': '185:185', 'Entrypoint': ['/opt/entrypoint.sh'], 'WorkingDir': '/opt/spark/work-dir',
               'Labels': {'old': 'retained'}}, 'RootFS': {'Layers': LAYERS}}
PROOF = {'image': B.REMOTE.IMAGE, 'image_id': BASE['Id'], 'rootfs_diff_ids': LAYERS}


class BuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state, self.prepared = self.root/'builds', self.root/'prepared'
        self.prepared.mkdir(mode=0o700)
        folder = self.prepared/PREPARED; folder.mkdir(mode=0o700)
        self.raw = b'fake-comet-jar'
        self.path = folder/B.J.FILENAME; self.path.write_bytes(self.raw); self.path.chmod(0o600)
        self.row = {'artifact': 'fixed-comet-artifact', 'file': B.J.FILENAME, 'path': str(self.path),
            'size': len(self.raw), 'sha256': hashlib.sha256(self.raw).hexdigest(),
            'sha1': hashlib.sha1(self.raw).hexdigest(), 'native_entry': 'fixed-arm64.so',
            'native_elf': {'machine': 183}}
        for handle in [patch.object(B, 'ROOT', self.root), patch.object(B, 'STATE', self.state),
            patch.object(B.J, 'STATE', self.prepared), patch.object(B.J, 'SHA256', self.row['sha256']),
            patch.object(B.J, 'verified_artifact', side_effect=lambda _: deepcopy(self.row)),
            patch.object(B, 'sources', return_value={'fixed.py': '0' * 64}),
            patch.object(B.P, 'guard', return_value={'daemon': 'same'}),
            patch.object(B, 'cached_base', return_value=(deepcopy(BASE), deepcopy(PROOF)))]:
            handle.start(); self.addCleanup(handle.stop)
        self.calls, self.images = [], {}
        self.mutate = lambda: None

    def image(self, recipe):
        return {'Id': IMAGE_ID, 'Descriptor': {'digest': IMAGE_ID}, 'Architecture': 'arm64', 'Os': 'linux',
            'Size': 2000, 'RepoTags': [recipe['tag']], 'RepoDigests': [B.REPOSITORY + '@' + IMAGE_ID],
            'Config': dict(deepcopy(BASE['Config']), Labels={**BASE['Config']['Labels'], **recipe['labels']}),
            'RootFS': {'Layers': LAYERS + ['sha256:' + '1' * 64]}}

    def command(self, argv, **kwargs):
        self.calls.append(argv)
        self.assertEqual(argv[:3], ['docker', '--context', B.P.CONTEXT])
        args = argv[3:]
        if args[:2] == ['image', 'ls']:
            tag = next(v.split('=', 1)[1] for v in args if v.startswith('reference='))
            return IMAGE_ID if tag in self.images else ''
        if args[:2] == ['image', 'inspect']: return json.dumps([self.images[args[2]]])
        if args[0] == 'build':
            recipe = B.inputs(PREPARED, BASE_TOKEN)[0]
            self.images[recipe['tag']] = self.image(recipe)
            self.mutate()
            return 'built'
        raise AssertionError('UNEXPECTED_COMMAND')

    def test_offline_one_layer_readback_and_idempotent_reuse(self):
        first = B.build(PREPARED, BASE_TOKEN, command=self.command)
        self.assertEqual(first['result'], 'passed')
        self.assertEqual(B.read_verified_image(first['build_token'], command=self.command), first)
        build = next(args for args in self.calls if args[3] == 'build')
        for value in ('--pull=false', '--network=none', 'linux/arm64'): self.assertIn(value, build)
        context = self.state/first['build_token']/'context'
        self.assertTrue((context/'Dockerfile').read_text().startswith('FROM ' + B.REMOTE.IMAGE))
        self.assertEqual(len(B.context_files(context)), 3)
        self.calls.clear()
        second = B.build(PREPARED, BASE_TOKEN, command=self.command)
        self.assertTrue(second['reused'])
        self.assertFalse(any(args[3] == 'build' for args in self.calls))
        self.assertEqual(first['inputs'], second['inputs'])

    def test_jar_change_before_allocation_is_refused(self):
        self.path.write_bytes(b'changed')
        with self.assertRaises(Exception): B.build(PREPARED, BASE_TOKEN, command=self.command)
        self.assertFalse(self.state.exists()); self.assertEqual(self.calls, [])

    def test_changed_jar_during_build_cannot_publish_pass(self):
        self.mutate = lambda: self.path.write_bytes(b'changed')
        with self.assertRaises(Exception): B.build(PREPARED, BASE_TOKEN, command=self.command)
        receipt = json.loads(next(self.state.glob('*/summary.json')).read_bytes())
        self.assertEqual(receipt['result'], 'failed')
        self.assertIsNotNone(receipt['pending'])

    def test_altered_base_layer_arch_or_config_rejected(self):
        recipe = B.inputs(PREPARED, BASE_TOKEN)[0]
        for mutate in [lambda v: v.update(Architecture='amd64'),
                       lambda v: v['RootFS']['Layers'].__setitem__(0, 'sha256:' + '9' * 64),
                       lambda v: v['Config'].update(Entrypoint=['bash'])]:
            value = self.image(recipe); mutate(value)
            with self.assertRaises(Exception): B.verify_image(value, recipe, BASE, PROOF)

    def test_first_cancellation_survives_failed_summary_save(self):
        first = KeyboardInterrupt('first')
        self.mutate = lambda: (_ for _ in ()).throw(first)
        real = B.persist
        def save(path, value):
            if value['result'] == 'failed': raise OSError('late-save')
            return real(path, value)
        with patch.object(B, 'persist', side_effect=save):
            with self.assertRaises(KeyboardInterrupt) as caught:
                B.build(PREPARED, BASE_TOKEN, command=self.command)
        self.assertIs(caught.exception, first)


if __name__ == '__main__':
    unittest.main()
