"""Offline generation prepare: synthetic bundles and private files only."""
import copy
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/local_experiment'))
import local_gitops as gitops
import runtime_profile
import trino_auth
import database_generation as generation
from test_database_generation import spec


class GenerationFlagTests(unittest.TestCase):
    def test_runtime_commands_rejected_before_profile_or_operations(self):
        with ExitStack() as stack:
            for name in ('configure_process', 'auth_overlay', 'private_json'):
                stack.enter_context(patch.object(runtime_profile, name, side_effect=AssertionError(name)))
            for name in ('kubectl', 'snapshot', 'activate', 'collect_files', 'prepare_database_generation'):
                stack.enter_context(patch.object(gitops, name, side_effect=AssertionError(name)))
            stack.enter_context(patch('sys.stderr', new_callable=io.StringIO))
            for command in ('snapshot', 'activate', 'status'):
                with self.subTest(command=command):
                    self.assertEqual(gitops.main([command, '--database-generation', 'unused']), 1)
            self.assertEqual(gitops.main(['prepare', '--prospective-backend-auth', 'unused']), 1)


class DatabaseGenerationGitopsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.bundle = generation.render(spec(3), ROOT)
        self.directory = self.root / 'bundle'
        generation.write_bundle(self.bundle, self.directory)
        self.guard = ExitStack()
        self.addCleanup(self.guard.close)
        for name in ('configure_process', 'auth_overlay', 'current'):
            self.guard.enter_context(patch.object(runtime_profile, name, side_effect=AssertionError(name)))
        for name in ('collect_files', 'child_definitions', 'kubectl', 'snapshot', 'activate', 'operator_credentials'):
            self.guard.enter_context(patch.object(gitops, name, side_effect=AssertionError(name)))
        self.guard.enter_context(patch.dict(os.environ, {
            'SHIROKUMA_RUNTIME_PROFILE': str(self.root / 'current-G1-profile-must-not-be-read'),
            'SHIROKUMA_TRINO_AUTH_FILE': str(self.root / 'current-G1-backend-must-not-be-read')}))

    def run_prepare(self, *extra):
        with patch('sys.stdout', new_callable=io.StringIO) as out, patch('sys.stderr', new_callable=io.StringIO) as err:
            code = gitops.main(['prepare', '--database-generation', str(self.directory), *extra])
        return code, out.getvalue(), err.getvalue()

    def auth(self):
        identity = json.loads(self.bundle['files']['identity-public.json'])
        value = dict(version=2, platform_admin=dict(username=identity['platform_admin'], password='synthetic-admin'), databases={})
        for key, entry in identity['databases'].items():
            value['databases'][key] = dict(sql_catalog=entry['sql_catalog'], generation_id=entry['generation_id'],
                roles={role: dict(username=username, password='synthetic-'+key+'-'+role) for role, username in entry['roles'].items()})
        return value

    def private(self, value, mode=0o600):
        path = self.root / 'prospective-backend.json'
        path.write_text(json.dumps(value))
        path.chmod(mode)
        return path

    def test_deterministic_receipt_uses_verified_delivery_digest_without_writes(self):
        with patch.object(Path, 'write_text', side_effect=AssertionError('write')), patch.object(Path, 'write_bytes', side_effect=AssertionError('write')):
            first = self.run_prepare()
            self.assertEqual(first, self.run_prepare())
        self.assertEqual(first[0], 0, first[2])
        receipt = json.loads(first[1])
        self.assertEqual(receipt, dict(relativeFilesCount=sum(p.startswith('deploy/') for p in self.bundle['files']),
            digest=self.bundle['manifest']['delivery_digest'], generationId=self.bundle['manifest']['generation_id']))
        self.assertNotEqual(receipt['digest'], receipt['generationId'])

    def test_generated_children_preserve_every_field_except_path_and_source(self):
        files = self.bundle['files']
        paths = self.bundle['manifest']['child_files']
        prefix = gitops.generation_prefix(self.bundle['manifest']['delivery_digest'])
        children = gitops.generation_children(files, paths, prefix)
        self.assertEqual(len(children), 7)
        for path, actual in zip(paths, children):
            expected = yaml.safe_load(files[path])
            expected['spec']['path'] = './'+prefix+expected['spec']['path'].removeprefix('./')
            expected['spec']['sourceRef'] = dict(kind='Bucket', name=gitops.SOURCE)
            self.assertEqual(actual, expected)
        text = yaml.safe_dump_all(children)
        for entry in self.bundle['manifest']['spec']['databases'].values():
            self.assertIn(entry['binding']['polaris_service_ref'], text)
        self.assertEqual(self.run_prepare()[0], 0)

    def test_generated_source_uses_versioned_secret(self):
        prefix = gitops.generation_prefix(self.bundle['manifest']['delivery_digest'])
        sources = gitops.source_definitions(ROOT, prefix)
        with patch.object(gitops, 'source_definitions', return_value=sources):
            self.assertEqual(self.run_prepare()[0], 0)
        self.assertEqual(sources[0]['spec']['secretRef']['name'],
            self.bundle['manifest']['spec']['shared_secret_refs']['gitops_source']['name'])
        self.assertEqual(sources[0]['spec']['prefix'], prefix)

    def test_env_tokens_and_disabled_substitution_survive(self):
        before = dict(self.bundle['files'])
        self.assertTrue(any(b'${ENV:' in content for content in before.values()))
        def disabled(node):
            if isinstance(node, dict):
                return node.get('kustomize.toolkit.fluxcd.io/substitute') == 'disabled' or any(disabled(v) for v in node.values())
            return isinstance(node, list) and any(disabled(v) for v in node)
        self.assertTrue(any(disabled(doc) for path, content in before.items() if path.endswith('.yaml')
            for doc in yaml.safe_load_all(content)))
        with patch.object(gitops, 'validate_content', wraps=gitops.validate_content) as validate:
            self.assertEqual(self.run_prepare()[0], 0)
        delivered = {call.args[0]: call.args[1] for call in validate.call_args_list if call.args[0].startswith('deploy/')}
        self.assertEqual(delivered, {p: content for p, content in before.items() if p.startswith('deploy/')})

    def test_core_rejects_missing_artifact_and_reference_drift(self):
        victim = self.directory / self.bundle['manifest']['child_files'][0]
        original = victim.read_bytes()
        victim.unlink()
        self.assertEqual(self.run_prepare()[0], 1)
        victim.write_bytes(original)
        manifest_path = self.directory / 'generation.json'
        manifest = json.loads(manifest_path.read_bytes())
        manifest['profile_fingerprint'] = '0'*64
        manifest_path.write_text(json.dumps(manifest))
        self.assertEqual(self.run_prepare()[0], 1)

    def test_prospective_exact_identity_is_accepted(self):
        path = self.private(self.auth())
        result = self.run_prepare('--prospective-backend-auth', str(path))
        self.assertEqual(result[0], 0, result[2])
        self.assertNotIn('synthetic-', result[1]+result[2])

    def test_wrong_generation_catalog_database_username_and_admin_fail_safely(self):
        base = self.auth()
        key = next(iter(base['databases']))
        mutations = [lambda v: v['databases'][key].update(generation_id='wrong'),
            lambda v: v['databases'][key].update(sql_catalog='wrong'),
            lambda v: v['databases'].pop(key),
            lambda v: v['databases'][key]['roles']['bi_viewer'].update(username='wrong'),
            lambda v: v['platform_admin'].update(username='wrong'),
            lambda v: v['databases'].update(extra=copy.deepcopy(v['databases'][key]))]
        for mutate in mutations:
            value = copy.deepcopy(base)
            mutate(value)
            with self.subTest(mutation=mutate):
                result = self.run_prepare('--prospective-backend-auth', str(self.private(value)))
                self.assertEqual(result[0], 1)
                self.assertNotIn('synthetic-', result[1]+result[2])

    def test_prospective_file_requires_0600(self):
        result = self.run_prepare('--prospective-backend-auth', str(self.private(self.auth(), 0o644)))
        self.assertEqual(result[0], 1)

    def test_module_cli_with_real_core_bundle_and_invalid_current_profile(self):
        result = subprocess.run(['rtk', 'proxy', sys.executable, '-m', 'tools.local_experiment.local_gitops',
            'prepare', '--database-generation', str(self.directory)], cwd=ROOT, env=os.environ.copy(),
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['digest'], self.bundle['manifest']['delivery_digest'])


if __name__ == '__main__':
    unittest.main()
