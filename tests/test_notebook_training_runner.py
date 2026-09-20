"""Training-review Notebook transport; execution and all SDK/API work are fake."""
from contextlib import redirect_stdout
import asyncio
import io
import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.notebook import runner as R, selection as S

TOKEN = 'a' * 32
REQUEST = {'command': 'select', 'table': 'smoke.fixture_v1', 'columns': ['name'], 'limit': 1}


class TrainingRunnerTests(unittest.TestCase):
    def setUp(self):
        from tools.local_experiment.notebook import training as T
        self.training = T
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.state = self.root / 'runs'
        self.original_umask = os.umask(0o022)
        self.addCleanup(os.umask, self.original_umask)

    def main(self, *, mode='training', public_token=TOKEN, source_changed=False,
             unexpected=None, error=None, cleanup=True, evidence_failure=False,
             public_change=None):
        observed = []
        sealed = {'tools/local_experiment/notebook/training.py': 'b' * 64,
                  'examples/local_training_review.ipynb': 'c' * 64}
        later = dict(sealed)
        if source_changed:
            later['examples/local_training_review.ipynb'] = 'd' * 64
        expected_public = {'identity': {'attempt_token': TOKEN, 'run_id': '9' * 32},
                           'model': {'coefficients': [2.5, -1.7, .5], 'intercept': 4.0},
                           'metrics': {'train': {'rmse': .19}}}

        def verify_saved(public, token):
            self.assertEqual(token, TOKEN)
            if public != expected_public:
                raise ValueError('public review differs from private saved workload')

        def build(arguments, **kwargs):
            Path(arguments[arguments.index('-o') + 1]).write_bytes(b'fake-reader-binary')

        async def execute(directory, binary, notebook, **kwargs):
            observed.append((directory, binary, notebook, kwargs))
            if mode == 'training':
                self.assertIsNone(binary)
                self.assertEqual(kwargs, {'training_token': TOKEN})
                # The detailed public contract belongs to training.py's tests.
                public = json.loads(json.dumps(expected_public))
                public['identity']['attempt_token'] = public_token
                if public_change == 'run_id':
                    public['identity']['run_id'] = 'e' * 32
                elif public_change == 'model':
                    public['model']['coefficients'][0] += .1
                elif public_change == 'metrics':
                    public['metrics']['train']['rmse'] += .1
                R.write_json(directory / 'training-review.json', public)
            elif mode == 'request':
                self.assertEqual(kwargs, {'request': REQUEST})
            else:
                self.assertEqual(kwargs, {})
            if unexpected:
                if unexpected == 'dangling-reader-intent':
                    (directory / 'reader-intent.json').symlink_to(directory / 'absent-intent')
                else:
                    (directory / unexpected).write_bytes(b'unexpected Reader evidence')
            return {'result': 'unknown' if not cleanup else 'interrupted' if error else 'passed',
                    'error': error, 'reader_cleanup_confirmed': True,
                    'kernel_cleanup_confirmed': cleanup, 'kernel_pid': 202}

        output = io.StringIO()
        original_write = R.write_json
        def write(path, value):
            if evidence_failure and path.name == 'result.json':
                raise OSError('private output-save failure')
            return original_write(path, value)

        arguments = ['--training-token', TOKEN] if mode == 'training' else ['--request-stdin'] if mode == 'request' else []
        with patch.object(R, 'ROOT', self.root), patch.object(R, 'STATE', self.state), \
                patch.object(R.subprocess, 'run', side_effect=build) as builder, \
                patch.object(R, 'execute', side_effect=execute) as executor, \
                patch.object(R, 'training_sources', side_effect=[sealed, later]), \
                patch.object(self.training, 'validate_public') as validate, \
                patch.object(self.training, 'verify_saved_review', side_effect=verify_saved) as binding, \
                patch.object(R, 'write_json', side_effect=write), \
                redirect_stdout(output):
            code = R.main(arguments, stdin=io.BytesIO(S.canonical_bytes(REQUEST)))
        self.binding_validator = binding
        return code, json.loads(output.getvalue()), observed, builder, executor, validate

    def test_training_main_binds_token_without_building_reader(self):
        code, result, observed, build, execute, validate = self.main()
        self.assertEqual(code, 0)
        self.assertEqual(result['result'], 'passed')
        self.assertEqual(result['profile'], 'training-review-v1')
        self.assertEqual(result['training_token'], TOKEN)
        self.assertTrue(result['reader_cleanup_confirmed'] and result['kernel_cleanup_confirmed'])
        self.assertIsNone(result['reader_evidence_token'])
        self.assertNotIn('request_sha256', result)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(validate.call_count, 1)
        self.assertEqual(self.binding_validator.call_count, 1)
        build.assert_not_called()
        directory, binary, notebook, _ = observed[0]
        self.assertIsNone(binary)
        inputs = json.loads((directory / 'inputs.json').read_bytes())
        self.assertIsNone(inputs['binary_sha256'])
        self.assertEqual(inputs['training_token'], TOKEN)
        self.assertEqual(inputs['profile'], 'training-review-v1')
        self.assertEqual(set(inputs['source_sha256']), {'tools/local_experiment/notebook/training.py',
                                                      'examples/local_training_review.ipynb'})
        self.assertFalse((directory / 'request.json').exists())
        self.assertEqual(json.loads((directory / 'result.json').read_bytes())['result'], 'passed')
        self.assertTrue((directory / 'executed.ipynb').is_file())

    def test_invalid_token_never_allocates_builds_or_executes(self):
        for value in ('', '../escape', 'a' * 31, 'A' * 32, 'g' * 32, 'a' * 33, TOKEN + '\n'):
            with self.subTest(value=value), patch.object(R, 'STATE', self.state), \
                    patch.object(R.subprocess, 'run') as build, patch.object(R, 'execute') as execute, \
                    redirect_stdout(io.StringIO()) as output:
                self.assertEqual(R.main(['--training-token', value]), 1)
                self.assertEqual(json.loads(output.getvalue())['error_code'], 'NOTEBOOK_TRAINING_TOKEN_INVALID')
                self.assertFalse(self.state.exists())
                build.assert_not_called()
                execute.assert_not_called()

    def test_mixed_or_incomplete_cli_arguments_are_rejected_before_allocation(self):
        for arguments in (['--training-token'], ['--training-token', TOKEN, '--request-stdin'],
                          ['--request-stdin', '--training-token', TOKEN], ['--training-token', TOKEN, TOKEN]):
            with self.subTest(arguments=arguments), patch.object(R, 'STATE', self.state), \
                    patch.object(R.subprocess, 'run') as build, patch.object(R, 'execute') as execute, \
                    redirect_stdout(io.StringIO()) as output:
                self.assertEqual(R.main(arguments), 1)
                self.assertEqual(json.loads(output.getvalue())['error_code'], 'ARGUMENTS_INVALID')
                self.assertFalse(self.state.exists())
                build.assert_not_called()
                execute.assert_not_called()

    def test_public_attempt_token_mismatch_refuses_success(self):
        code, result, _, build, _, _ = self.main(public_token='e' * 32)
        self.assertEqual(code, 1)
        self.assertEqual(result['result'], 'failed')
        self.assertEqual(result['error_code'], 'NOTEBOOK_SETUP_FAILED')
        self.assertTrue(result['reader_cleanup_confirmed'] and result['kernel_cleanup_confirmed'])
        build.assert_not_called()

    def test_source_or_template_change_after_execution_refuses_success(self):
        code, result, _, _, _, _ = self.main(source_changed=True)
        self.assertEqual(code, 1)
        self.assertEqual(result['result'], 'failed')
        self.assertEqual(result['error_code'], 'NOTEBOOK_SETUP_FAILED')

    def test_private_binding_failure_for_forged_run_model_or_metrics_refuses_success(self):
        for field in ('run_id', 'model', 'metrics'):
            with self.subTest(field=field):
                code, result, _, build, _, _ = self.main(public_change=field)
                self.assertEqual(code, 1)
                self.assertEqual(result['result'], 'failed')
                self.assertEqual(result['error_code'], 'NOTEBOOK_SETUP_FAILED')
                self.assertTrue(result['reader_cleanup_confirmed'] and result['kernel_cleanup_confirmed'])
                self.assertEqual(self.binding_validator.call_count, 1)
                build.assert_not_called()

    def test_unexpected_reader_records_or_binary_refuse_training_success(self):
        for name in ('reader-intent.json', 'reader-status.json', 'build.log', 'shirokuma',
                     'dangling-reader-intent'):
            with self.subTest(name=name):
                code, result, _, build, _, _ = self.main(unexpected=name)
                self.assertEqual(code, 1)
                self.assertEqual(result['result'], 'failed')
                build.assert_not_called()

    def test_original_interrupt_survives_post_execution_source_change(self):
        original = R.NotebookInterrupted(signal.SIGINT)
        code, result, _, build, _, _ = self.main(error=original, source_changed=True)
        self.assertEqual(code, 130)
        self.assertEqual(result['result'], 'interrupted')
        self.assertEqual(result['error_code'], 'INTERRUPTED')
        self.assertEqual(result['signal'], signal.SIGINT)
        self.assertTrue(result['kernel_cleanup_confirmed'])
        build.assert_not_called()

    def test_unknown_kernel_cleanup_stays_unknown_after_source_failure(self):
        code, result, _, _, _, _ = self.main(error=TimeoutError('private timeout'),
                                            cleanup=False, source_changed=True)
        self.assertEqual(code, 1)
        self.assertEqual(result['result'], 'unknown')
        self.assertFalse(result['kernel_cleanup_confirmed'])
        self.assertEqual(result['error_code'], 'NOTEBOOK_TIMEOUT')

    def test_final_evidence_save_failure_prevents_passed_stdout(self):
        code, result, _, _, _, _ = self.main(evidence_failure=True)
        self.assertEqual(code, 1)
        self.assertEqual(result['result'], 'unknown')
        self.assertEqual(result['error_code'], 'NOTEBOOK_EVIDENCE_SAVE_FAILED')

    def test_default_and_request_entrypoints_keep_their_prior_shapes(self):
        for mode in ('default', 'request'):
            with self.subTest(mode=mode):
                code, result, observed, build, _, validate = self.main(mode=mode)
                self.assertEqual(code, 0)
                self.assertEqual(build.call_count, 1)
                self.assertNotIn('training_token', result)
                self.assertEqual(result.get('profile'), 'table-preview-v1' if mode == 'request' else None)
                validate.assert_not_called()
                self.binding_validator.assert_not_called()
                directory = observed[0][0]
                self.assertEqual((directory / 'request.json').exists(), mode == 'request')

    def test_parent_training_and_reader_environment_cannot_select_kernel_inputs(self):
        original_token = os.environ.get(self.training.TOKEN_ENV)
        parent = {self.training.TOKEN_ENV: 'f' * 32,
                  'SHIROKUMA_NOTEBOOK_BINARY': '/untrusted/binary',
                  'SHIROKUMA_NOTEBOOK_RUN_DIR': '/untrusted/run',
                  'SHIROKUMA_NOTEBOOK_REQUEST_PATH': '/untrusted/request',
                  'SHIROKUMA_NOTEBOOK_REQUEST_SHA256': 'e' * 64,
                  'PYTHONPATH': '/untrusted/python'}
        for mode in ('training', 'default', 'request'):
            with self.subTest(mode=mode):
                directory = self.root / mode
                directory.mkdir(mode=0o700)
                if mode == 'request':
                    path = directory / 'request.json'
                    path.write_bytes(S.canonical_bytes(REQUEST))
                    path.chmod(0o600)
                binary = None if mode == 'training' else directory / 'shirokuma'
                with patch.dict(os.environ, parent):
                    environment = R.kernel_environment(directory, binary,
                        training_token=TOKEN if mode == 'training' else None,
                        request=REQUEST if mode == 'request' else None)
                self.assertEqual(environment['SHIROKUMA_NOTEBOOK_RUN_DIR'], str(directory))
                self.assertNotIn('PYTHONPATH', environment)
                if mode == 'training':
                    self.assertEqual(environment[self.training.TOKEN_ENV], TOKEN)
                    self.assertNotIn('SHIROKUMA_NOTEBOOK_BINARY', environment)
                else:
                    self.assertNotIn(self.training.TOKEN_ENV, environment)
                    self.assertEqual(environment['SHIROKUMA_NOTEBOOK_BINARY'], str(binary))
                if mode == 'request':
                    self.assertEqual(environment['SHIROKUMA_NOTEBOOK_REQUEST_PATH'], str(directory / 'request.json'))
                else:
                    self.assertNotIn('SHIROKUMA_NOTEBOOK_REQUEST_PATH', environment)
                    self.assertNotIn('SHIROKUMA_NOTEBOOK_REQUEST_SHA256', environment)
        self.assertEqual(os.environ.get(self.training.TOKEN_ENV), original_token)

    def test_training_environment_rejects_reader_binary_or_request(self):
        for name, binary, request in [('binary', self.root / 'reader', None),
                                      ('request', None, REQUEST)]:
            with self.subTest(name=name):
                directory = self.root / name
                directory.mkdir(mode=0o700)
                with self.assertRaises(ValueError):
                    R.kernel_environment(directory, binary, request=request, training_token=TOKEN)
                self.assertEqual(list(directory.iterdir()), [])

    def test_execute_rejects_mixed_training_inputs_before_kernel_or_directory_allocation(self):
        for name, binary, request in [('binary', self.root / 'reader', None),
                                      ('request', None, REQUEST)]:
            with self.subTest(name=name):
                directory = self.root / name
                directory.mkdir(mode=0o700)
                with patch.object(R, 'AsyncKernelManager') as manager:
                    with self.assertRaises(ValueError):
                        asyncio.run(R.execute(directory, binary, object(), request=request, training_token=TOKEN))
                manager.assert_not_called()
                self.assertEqual(list(directory.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
