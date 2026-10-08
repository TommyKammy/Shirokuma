import copy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from tools.local_experiment import render_inventory as I
from tools.local_experiment import source_candidate as C


@unittest.skipUnless(shutil.which('kubectl'), 'local kubectl renderer unavailable')
class RenderInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stage = self.root/'staging'; self.stage.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.paths = dict(C.CHILDREN, **{'flux-system': C.ROOT_PATH})
        root_objects = [{'apiVersion': 'source.toolkit.fluxcd.io/v1', 'kind': 'GitRepository',
                         'metadata': {'name': 'flux-system', 'namespace': 'flux-system'},
                         'spec': {'url': 'https://external.invalid/repository', 'ref': {'branch': 'main'}}}]
        for name, path in self.paths.items():
            d = self.root/path; d.mkdir(parents=True)
            root_objects.append({'apiVersion': 'kustomize.toolkit.fluxcd.io/v1', 'kind': 'Kustomization',
                'metadata': {'name': name, 'namespace': 'flux-system'},
                'spec': {'path': './'+path, 'sourceRef': {'kind': 'GitRepository', 'name': 'flux-system'}}})
            (d/'kustomization.yaml').write_text('resources: [objects.yaml]\n')
            (d/'objects.yaml').write_text(json.dumps(self.workload(name)))
        self.controllers = [{'name': name, 'replicas': 1, 'images': ['image@sha256:'+'a'*64]}
                            for name in sorted(C.S.FLUX)]
        root_objects += [self.workload(v['name'], namespace='flux-system') for v in self.controllers]
        (self.root/C.ROOT_PATH/'objects.yaml').write_text('\n---\n'.join(json.dumps(o) for o in root_objects))
        self.commit()

    def workload(self, name, kind='Deployment', namespace='test'):
        return {'apiVersion': 'apps/v1', 'kind': kind, 'metadata': {'name': name, 'namespace': namespace},
            'spec': {'replicas': 1, 'template': {'spec': {'containers': [
                {'name': 'main', 'image': 'image@sha256:'+'a'*64,
                 'env': [{'name': 'PASSWORD', 'valueFrom': {'secretKeyRef': {'name': 'missing', 'key': 'password'}}}]}]}}}}

    def commit(self):
        subprocess.run(['git', 'add', 'deploy'], cwd=self.root, check=True)
        subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=f@invalid',
                        'commit', '--allow-empty', '-qm', 'fixture'], cwd=self.root, check=True)
        commit = C.git(self.root, 'rev-parse', 'HEAD')
        self.candidate = {'identity': {'commit': commit, 'tree': C.git(self.root, 'rev-parse', 'HEAD^{tree}'),
                           'root_path': './'+C.ROOT_PATH},
            'source': {'ref': {'commit': commit}, 'suspend': True, 'transport': 'unselected_local_only'},
            'kustomizations': [{'name': n, 'path': './'+p, 'suspend': True} for n, p in self.paths.items()],
            'controllers': self.controllers}

    def build(self):
        return I.build(self.root, self.candidate, self.stage)

    def test_real_transitive_render_complete_images_inhibition_and_missing_inputs(self):
        before = copy.deepcopy(self.candidate)
        result = self.build()
        self.assertEqual(self.candidate, before)
        self.assertEqual(len(result['renders']), 7)
        self.assertEqual(len(result['inputs']), 14)
        self.assertEqual(len(result['workloads']), 10)
        self.assertEqual(len(result['images']), 10)
        self.assertTrue(result['repository_inventory_complete'])
        self.assertFalse(result['runtime_inputs_complete'])
        self.assertTrue(all(not r['repository_available'] for r in result['runtime_input_references']))
        self.assertFalse(result['activation_authorized'])
        self.assertFalse(result['admission_verified'])
        self.assertFalse(result['gate_acceptance'])
        self.assertIsNone(result['local_risk_decision']['decision'])
        self.assertTrue(I.validate(self.root, result, self.candidate, self.stage))

    def test_dirty_checkout_isolated_and_inventory_edit_rejected(self):
        before = self.build()
        (self.root/C.ROOT_PATH/'objects.yaml').write_text('dirty non-YAML\n')
        self.assertEqual(self.build(), before)
        before['images'].pop()
        with self.assertRaisesRegex(C.S.R.RunnerError, 'RENDER_INVENTORY_DRIFT'):
            I.validate(self.root, before, self.candidate, self.stage)

    def test_generated_file_outside_root_directory_bound(self):
        p = self.root/C.CHILDREN['shirokuma-dev']
        (p/'source.txt').write_bytes(b'committed trailing newlines\n\n')
        (p/'kustomization.yaml').write_text('resources: [objects.yaml]\nconfigMapGenerator:\n- name: code\n  files: [source.txt]\n')
        self.commit()
        v = self.build()
        record = next(i for i in v['inputs'] if i['path'].endswith('/source.txt'))
        self.assertEqual(record['sha256'], I.hashlib.sha256(b'committed trailing newlines\n\n').hexdigest())

    def test_file_patch_and_replacement_inputs_are_bound(self):
        p = self.root/C.CHILDREN['shirokuma-dev']
        (p/'patch.yaml').write_text('- op: replace\n  path: /spec/replicas\n  value: 2\n')
        (p/'replacement.yaml').write_text('source:\n  kind: Deployment\n  name: shirokuma-dev\n  fieldPath: metadata.name\ntargets:\n- select:\n    kind: Deployment\n  fieldPaths: [metadata.labels.copied]\n  options: {create: true}\n')
        (p/'kustomization.yaml').write_text('resources: [objects.yaml]\npatches:\n- path: patch.yaml\n  target: {kind: Deployment}\nreplacements:\n- path: replacement.yaml\n')
        self.commit(); v = self.build()
        self.assertTrue(v['repository_inventory_complete'])
        paths = {i['path'] for i in v['inputs']}
        self.assertIn(C.CHILDREN['shirokuma-dev']+'/patch.yaml', paths)
        self.assertIn(C.CHILDREN['shirokuma-dev']+'/replacement.yaml', paths)

    def test_remote_or_executable_or_unknown_config_never_invokes_renderer(self):
        for config in ('resources: [https://external.invalid/base]\n',
                       'generators: [plugin.yaml]\n', 'openapi: {path: https://external.invalid/schema}\n',
                       'resources: [/outside]\n', 'resources: [../../../../../../outside]\n'):
            (self.root/C.ROOT_PATH/'kustomization.yaml').write_text(config)
            self.commit()
            closure = I.Closure(self.root, self.candidate['identity']['commit'])
            with mock.patch.object(I.subprocess, 'run', wraps=subprocess.run) as run:
                self.assertIsNone(closure.render(C.ROOT_PATH, self.stage, 'kubectl'))
            self.assertFalse(any(c.args[0][0] == 'kubectl' for c in run.call_args_list))
            self.assertTrue(closure.gaps)

    def test_symlink_missing_and_cycle_gaps(self):
        p = self.root/C.ROOT_PATH
        for config in ('resources: [absent.yaml]\n', 'resources: [link.yaml]\n', 'resources: [.]\n'):
            (p/'kustomization.yaml').write_text(config)
            if not (p/'link.yaml').is_symlink():
                (p/'link.yaml').symlink_to('/outside')
            self.commit()
            v = self.build()
            self.assertFalse(v['repository_inventory_complete'])
            self.assertTrue(v['gaps'])

    def test_duplicate_yaml_keys_cannot_hide_remote_input(self):
        (self.root/C.ROOT_PATH/'kustomization.yaml').write_text('resources: [https://external.invalid]\nresources: []\n')
        self.commit()
        with self.assertRaisesRegex(C.S.R.RunnerError, 'RENDER_DUPLICATE_KEY'):
            self.build()

    def test_external_child_gap_does_not_hide_independent_local_workloads(self):
        p = self.root/C.CHILDREN['shirokuma-catalog-bootstrap']/'kustomization.yaml'
        p.write_text('resources: [https://external.invalid/base]\n')
        self.commit(); v = self.build()
        self.assertFalse(v['repository_inventory_complete'])
        self.assertEqual(len(v['renders']), 6)
        self.assertEqual(len(v['workloads']), 9)
        self.assertTrue(any(g['reason'] == 'external_or_invalid_input' for g in v['gaps']))

    def test_postbuild_substitution_and_missing_variable_gap(self):
        p = self.root/C.CHILDREN['shirokuma-dev']/'objects.yaml'
        obj = json.loads(p.read_text()); obj['spec']['template']['metadata'] = {'annotations': {'generation': '${GEN}'}}
        p.write_text(json.dumps(obj))
        root = self.root/C.ROOT_PATH/'objects.yaml'
        docs = I.documents(root.read_text())
        child = next(o for o in docs if o['metadata']['name'] == 'shirokuma-dev')
        child['spec']['postBuild'] = {'substitute': {'GEN': '3'}}
        root.write_text('\n---\n'.join(json.dumps(o) for o in docs)); self.commit()
        first = self.build(); self.assertTrue(first['repository_inventory_complete'])
        child['spec']['postBuild']['substitute'] = {}
        root.write_text('\n---\n'.join(json.dumps(o) for o in docs)); self.commit()
        second = self.build(); self.assertFalse(second['repository_inventory_complete'])
        self.assertEqual(second['gaps'][0]['reason'], 'unavailable_or_unsupported_substitution')

    def test_scope_pin_and_candidate_transport_drift_rejected(self):
        for change in ('path', 'pin', 'transport', 'suspension'):
            old = copy.deepcopy(self.candidate)
            if change == 'path': self.candidate['kustomizations'][0]['path'] = './other'
            if change == 'pin': self.candidate['controllers'][0]['images'] = ['other@sha256:'+'b'*64]
            if change == 'transport': self.candidate['source']['transport'] = 'external'
            if change == 'suspension': self.candidate['kustomizations'][0]['suspend'] = False
            with self.subTest(change=change), self.assertRaises(C.S.R.RunnerError): self.build()
            self.candidate = old

    def test_all_pod_surfaces_unpinned_images_and_unsupported_writers(self):
        pod = self.workload('job', kind='Job')
        spec = pod['spec']['template']['spec']
        spec['initContainers'] = [{'name': 'init', 'image': 'init:latest'}]
        spec['ephemeralContainers'] = [{'name': 'debug', 'image': 'debug:latest'}]
        daemon = self.workload('daemon', kind='DaemonSet')
        custom = {'apiVersion': 'custom/v1', 'kind': 'CustomWriter', 'metadata': {'name': 'custom'},
                  'spec': {'image': 'hidden:latest'}}
        _, images, _, gaps = I.inventory([pod, daemon, custom], self.candidate)
        self.assertEqual(len(images), 4)
        self.assertEqual(sum(not i['digest_pinned'] for i in images), 2)
        self.assertEqual({g['reason'] for g in gaps},
                         {'unsupported_workload_surface', 'workload_has_no_safe_inhibited_projection'})


if __name__ == '__main__':
    unittest.main()
