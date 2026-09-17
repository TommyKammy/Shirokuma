"""Offline notebook-client checks: no Go Reader, service, kernel or model runs."""
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
from tests.test_query_read_contract import identity, schema, result

TOKEN = 'a' * 32
RUN = 'b' * 32


def reader_frames():
    public = N.C.project_rows(result(N.REQUEST), N.REQUEST, schema(), identity())
    frames = [dict(schema_version=1, event='allocated', command='query select', evidence_token=TOKEN),
              dict(schema_version=1, event='result', command='query select', evidence_token=TOKEN,
                   result='passed', observation=public, error_code=None, cleanup_verified=True,
                   evidence=f'tmp/local-experiment/query-read/{TOKEN}/summary.json')]
    return frames


def encoded(frames):
    return b''.join(N._canonical(frame) for frame in frames)


def mlflow_fixture():
    source = 'sre/' + TOKEN
    entry = dict(run_id=RUN, source_key='c' * 64, projection_sha256='d' * 64, completed=True)
    index = dict(schema_version=1, store_id='e' * 32, experiment_id='1', experiment_pending=False,
                 runs={source: entry})
    params = dict(suite='sre', provider_kind='real-llm', provider='local-qwen', model='qwen3.5:9b',
                  case_signature='f' * 64, evaluation_suite='observation-signals-v1', source_result='passed')
    tags = {'shirokuma.owner':N.OWNER, 'shirokuma.store_id':index['store_id'],
            'shirokuma.source_id':source, 'shirokuma.source_key':entry['source_key'],
            'shirokuma.projection_sha256':entry['projection_sha256'],
            'shirokuma.time_semantics':'import-time-not-source-execution'}
    metrics = {'requested_inferences':16.0, 'attempted_inferences':16.0,
               'functional_success_observed.numerator':16.0, 'functional_success_observed.denominator':16.0,
               'functional_success_observed.value':1.0,
               'prompt_tokens.total':0.0, 'prompt_tokens.known_subtotal':0.0,
               'prompt_tokens.known_calls':16.0, 'prompt_tokens.missing_calls':0.0,
               'completion_tokens.known_subtotal':22.0, 'completion_tokens.known_calls':15.0,
               'completion_tokens.missing_calls':1.0}
    pairs = lambda value: [dict(key=k, value=v) for k,v in value.items()]
    experiment = {'experiment':dict(experiment_id='1', name=N.EXPERIMENT, lifecycle_stage='active',
                                   tags=pairs({k:tags[k] for k in ('shirokuma.owner','shirokuma.store_id')}))}
    runs = {'runs':[dict(info=dict(run_id=RUN, experiment_id='1', status='FINISHED', lifecycle_stage='active'),
                        data=dict(params=pairs(params), tags=pairs(tags), metrics=pairs(metrics)))]}
    return index, experiment, runs


class Child:
    pid = 987654

    def __init__(self, initial=None, stays_alive=False):
        self.returncode = None
        self.initial, self.stays_alive = initial, stays_alive
        self.calls = 0

    def wait(self, timeout):
        self.calls += 1
        if self.calls == 1 and self.initial is not None:
            raise self.initial
        if self.stays_alive:
            raise subprocess.TimeoutExpired('fixed', timeout)
        self.returncode = 130 if self.initial is not None else 0
        return self.returncode

    def poll(self):
        return self.returncode


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name).resolve()
        self.binary = self.path / 'shirokuma'
        self.binary.write_bytes(b'fake-not-executable')
        self.env = patch.dict(os.environ, {'SHIROKUMA_NOTEBOOK_RUN_DIR':str(self.path),
                                          'SHIROKUMA_NOTEBOOK_BINARY':str(self.binary)})
        self.env.start(); self.addCleanup(self.env.stop)

    def fake_spawn(self, child, frames=None):
        def spawn(argv, **kwargs):
            self.assertTrue((self.path / 'reader-intent.json').is_file())
            self.assertEqual(argv, [str(self.binary), '--repo-root', str(N.ROOT), '--profile', 'local-experiment',
                                   'query','select','--table','smoke.fixture_v1','--columns','id,name','--limit','10'])
            self.assertTrue(kwargs['start_new_session'])
            self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
            kwargs['stdout'].write(encoded(frames if frames is not None else reader_frames()))
            return child
        return spawn

    def test_fixed_reader_once_private_output_and_durable_status(self):
        with patch.object(N.subprocess, 'Popen', side_effect=self.fake_spawn(Child())) as spawn, \
                patch.object(N, '_process_start', return_value='Fri Sep 18 10:00:00 2026'):
            public = N.read_fixture()
            self.assertEqual(public, reader_frames()[1]['observation'])
            with self.assertRaises(FileExistsError):
                N.read_fixture()
            self.assertEqual(spawn.call_count, 1)
        status = json.loads((self.path / 'reader-status.json').read_bytes())
        self.assertEqual((status['state'], status['child_exited'], status['cleanup_verified']), ('passed', True, True))
        self.assertEqual((status['pid'],status['pgid'],status['evidence_token']), (Child.pid,Child.pid,TOKEN))
        for name in ('reader-intent.json','reader.stdout.jsonl','reader-status.json'):
            self.assertEqual((self.path/name).stat().st_mode & 0o777, 0o600)

    def test_intent_save_failure_prevents_process(self):
        with patch.object(N, '_write', side_effect=OSError('private-error')), patch.object(N.subprocess, 'Popen') as spawn:
            with self.assertRaises(OSError): N.read_fixture()
            spawn.assert_not_called()

    def test_envelope_tampering_false_cleanup_exit_and_row_hash_refused(self):
        cases = []
        for mutator in (lambda f:f[1].update(cleanup_verified=False),
                        lambda f:f[1].update(extra='raw-secret'),
                        lambda f:f[1].update(evidence_token='c'*32),
                        lambda f:f[1]['observation']['rows'][0][0].update(value='999')):
            frames = reader_frames(); mutator(frames); cases.append(encoded(frames))
        cases.append(encoded(reader_frames()).replace(b'"cleanup_verified":true', b'"cleanup_verified":true,"cleanup_verified":true'))
        for raw in cases:
            with self.subTest(raw_sha=N.C.digest(raw.decode())):
                with self.assertRaises(N.NotebookError): N._reader_frames(raw, 0)
        with self.assertRaises(N.NotebookError): N._reader_frames(encoded(reader_frames()), 1)

    def test_cancel_preserves_first_object_if_final_save_fails(self):
        original = KeyboardInterrupt('original')
        child = Child(original)
        write = N._write
        def fail_final(path, value, **kwargs):
            if value.get('state') == 'interrupted': raise OSError('secondary-private')
            return write(path, value, **kwargs)
        with patch.object(N.subprocess,'Popen',side_effect=self.fake_spawn(child,reader_frames()[:1])), \
                patch.object(N,'_process_start',return_value='start'), patch.object(N.os,'killpg') as interrupt, \
                patch.object(N,'_write',side_effect=fail_final):
            with self.assertRaises(KeyboardInterrupt) as raised: N.read_fixture()
            self.assertIs(raised.exception, original)
            interrupt.assert_called_once_with(child.pid, signal.SIGINT)

    def test_unconfirmed_timeout_never_reinterrupts_or_claims_cleanup(self):
        child = Child(subprocess.TimeoutExpired('fixed',240), stays_alive=True)
        with patch.object(N.subprocess,'Popen',side_effect=self.fake_spawn(child,reader_frames()[:1])), \
                patch.object(N,'_process_start',return_value='start'), patch.object(N.os,'killpg') as interrupt, \
                patch.object(N,'CLEANUP_TIMEOUT',0):
            with self.assertRaises(N.NotebookError): N.read_fixture()
            interrupt.assert_called_once_with(child.pid, signal.SIGINT)
        status = json.loads((self.path / 'reader-status.json').read_bytes())
        self.assertEqual((status['state'],status['child_exited'],status['cleanup_verified']), ('unknown',False,False))

    def test_explicit_interrupted_final_can_prove_cleanup_without_success(self):
        original = KeyboardInterrupt('original')
        child = Child(original)
        frames = reader_frames()
        frames[1].update(result='interrupted',observation=None,error_code='INTERRUPTED')
        with patch.object(N.subprocess,'Popen',side_effect=self.fake_spawn(child,frames)), \
                patch.object(N,'_process_start',return_value='start'), patch.object(N.os,'killpg') as interrupt:
            with self.assertRaises(KeyboardInterrupt) as raised: N.read_fixture()
            self.assertIs(raised.exception,original)
            interrupt.assert_called_once_with(child.pid,signal.SIGINT)
        status = json.loads((self.path/'reader-status.json').read_bytes())
        self.assertEqual((status['state'],status['child_exited'],status['cleanup_verified']),('interrupted',True,True))
        frames[1].update(result='failed',error_code='READ_FAILED')
        self.assertEqual(N._reader_frames(encoded(frames),1),(TOKEN,None,True))

    def test_mlflow_only_two_read_endpoints_project_known_zero_and_missing(self):
        index, experiment, runs = mlflow_fixture()
        index_path = self.path / 'index.json'; N._write(index_path,index)
        runs['runs'][0]['data']['raw_private_body'] = 'never-persist-this'
        with patch.object(N,'INDEX',index_path), patch.object(N,'_rest',side_effect=[experiment,runs]) as read:
            view = N.read_evaluations()
            self.assertEqual(read.call_count,2)
            self.assertIn('/experiments/get-by-name?',read.call_args_list[0].args[0])
            self.assertEqual(read.call_args_list[1].args[0],'/api/2.0/mlflow/runs/search')
            self.assertEqual(read.call_args_list[1].args[1]['experiment_ids'],['1'])
            with self.assertRaises(FileExistsError): N.read_evaluations()
        row = view['runs'][0]
        self.assertEqual(row['usage']['prompt_tokens']['total'],0)
        self.assertIsNone(row['usage']['completion_tokens']['total'])
        self.assertEqual(row['usage']['completion_tokens']['missing_calls'],1)
        self.assertIsNone(row['cost'])
        self.assertNotIn('never-persist-this',(self.path/'evaluations.json').read_text())

    def test_mlflow_incomplete_identity_and_nonfinite_numbers_refused(self):
        for change in (lambda r:r.update(next_page_token='remaining'),
                       lambda r:r.update(runs=[]),
                       lambda r:r['runs'].append(deepcopy(r['runs'][0])),
                       lambda r:r['runs'][0]['info'].update(experiment_id='2'),
                       lambda r:r['runs'][0]['data']['metrics'][0].update(value=float('inf')),
                       lambda r:r['runs'][0]['data']['metrics'][0].update(value=True)):
            index, _, runs = mlflow_fixture(); change(runs)
            with self.assertRaises(N.NotebookError): N._evaluation_rows(runs,'1',index)

    def test_fixed_http_paths_and_no_redirects(self):
        with patch.object(N.urllib.request,'build_opener') as opener:
            with self.assertRaises(N.NotebookError): N._rest('/api/2.0/mlflow/runs/create',{})
            opener.assert_not_called()
        with self.assertRaises(N.NotebookError): N._NoRedirect().redirect_request(None,None,None,None,None,None)

    def test_http_read_is_bounded_no_proxy_and_raw_errors_are_not_exposed(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def read(self,maximum):
                self.maximum = maximum
                return b'{"runs":[]}'
        response = Response()
        class Opener:
            def open(inner,request,timeout):
                self.assertEqual(request.full_url,N.URL+'/api/2.0/mlflow/runs/search')
                self.assertEqual((request.get_method(),timeout),('POST',10))
                return response
        with patch.object(N.urllib.request,'build_opener',return_value=Opener()) as build:
            self.assertEqual(N._rest('/api/2.0/mlflow/runs/search',{'experiment_ids':['1']}),{'runs':[]})
            self.assertEqual(build.call_args.args[0].proxies,{})
            self.assertEqual(response.maximum,N.MAX_HTTP+1)
        with patch.object(N.urllib.request,'build_opener',side_effect=ValueError('raw-secret')):
            with self.assertRaises(N.NotebookError) as raised: N._rest('/api/2.0/mlflow/runs/search',{})
            self.assertEqual(str(raised.exception),'NOTEBOOK_MLFLOW_READ_FAILED')

    def test_cleanup_poll_failure_does_not_replace_original_cancel(self):
        original = KeyboardInterrupt('original')
        child = Child(original)
        with patch.object(child,'poll',side_effect=OSError('secondary-private')), patch.object(N.os,'killpg'):
            self.assertIs(N._interrupt_and_wait(child,original),original)

    def test_template_is_output_free_with_only_fixed_read_functions(self):
        notebook = json.loads((N.ROOT/'examples/local_lakehouse.ipynb').read_bytes())
        code = [cell for cell in notebook['cells'] if cell['cell_type']=='code']
        self.assertTrue(all(cell['execution_count'] is None and cell['outputs']==[] for cell in code))
        joined = ''.join(''.join(cell['source']) for cell in code)
        self.assertEqual(joined.count('read_fixture()'),1)
        self.assertEqual(joined.count('read_evaluations()'),1)


if __name__ == '__main__':
    unittest.main()
