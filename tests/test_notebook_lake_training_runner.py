"""Iceberg training Notebook dispatch; kernels, HTTP, SDK and Reader stay fake."""
import asyncio
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import signal
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tools.local_experiment.notebook import runner as R, selection as S, training as T

TOKEN = 'a'*32
REQUEST = {'command':'select','table':'smoke.fixture_v1','columns':['name'],'limit':1}


class LakeTrainingRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root/'runs'
        previous = os.umask(0o022)
        self.addCleanup(os.umask,previous)

    def main(self, *, change=None, source_changed=False, error=None, cleanup=True, unexpected=None, save_failure=False, intent_change=None):
        source = {'tools/local_experiment/notebook/runner.py':'b'*64,
                  'examples/local_lake_training_review.ipynb':'c'*64}
        later = dict(source)
        if source_changed: later['examples/local_lake_training_review.ipynb']='d'*64
        expected = {'profile':T.LAKE_PROFILE,'identity':{'attempt_token':TOKEN,'run_id':'e'*32},
                    'model':{'coefficients':[2.5,-1.7,.5],'intercept':4.0},
                    'metrics':{'test':{'rmse':.18}},'lake_source':{'table_uuid':'fixed-uuid'}}
        captured=[]
        async def execute(directory,binary,notebook,**kwargs):
            captured.append((directory,binary,notebook,kwargs))
            self.assertIsNone(binary)
            self.assertEqual(kwargs,{'training_token':TOKEN,'kind':'iceberg'})
            public=deepcopy(expected)
            if change=='token':public['identity']['attempt_token']='f'*32
            elif change=='profile':public['profile']='training-review-v1'
            elif change=='run':public['identity']['run_id']='f'*32
            elif change=='model':public['model']['intercept']+=1
            elif change=='metrics':public['metrics']['test']['rmse']+=1
            elif change=='source':public['lake_source']['table_uuid']='different-uuid'
            R.write_json(directory/'training-review.json',public)
            intent={'schema_version':1,'profile':T.LAKE_PROFILE,'attempt_token':TOKEN,'kind':'iceberg'}
            if intent_change=='kind':intent['kind']='synthetic'
            elif intent_change=='profile':intent['profile']='training-review-v1'
            elif intent_change=='token':intent['attempt_token']='f'*32
            elif intent_change=='schema':intent['schema_version']=True
            elif intent_change=='extra':intent['extra']=True
            if intent_change!='missing':R.write_json(directory/'training-intent.json',intent)
            if unexpected:
                if unexpected=='dangling':(directory/'reader-intent.json').symlink_to(directory/'absent')
                else:(directory/unexpected).write_bytes(b'unexpected')
            return {'result':'unknown' if not cleanup else 'interrupted' if error else 'passed',
                    'error':error,'reader_cleanup_confirmed':True,'kernel_cleanup_confirmed':cleanup,'kernel_pid':202}
        def verify(public,token,*,kind):
            self.assertEqual((token,kind),(TOKEN,'iceberg'))
            if public!=expected:raise ValueError('public value differs from saved private training')
        original_write=R.write_json
        def write(path,value):
            if save_failure and path.name=='result.json':raise OSError('private final receipt error')
            return original_write(path,value)
        output=io.StringIO()
        with patch.object(R,'ROOT',self.root),patch.object(R,'STATE',self.state), \
             patch.object(R.subprocess,'run') as build,patch.object(R,'execute',side_effect=execute) as execution, \
             patch.object(R,'training_sources',side_effect=[source,later]) as sources, \
             patch.object(T,'validate_public') as validation,patch.object(T,'verify_saved_review',side_effect=verify) as binding, \
             patch.object(R,'write_json',side_effect=write),redirect_stdout(output):
            code=R.main(['--lake-training-token',TOKEN])
        return code,json.loads(output.getvalue()),captured,build,execution,validation,binding,sources

    def test_lake_main_binds_profile_kind_token_template_and_no_reader_build(self):
        code,result,captured,build,execution,validation,binding,sources=self.main()
        self.assertEqual(code,0);self.assertEqual(result['result'],'passed')
        self.assertEqual((result['profile'],result['kind'],result['training_token']),(T.LAKE_PROFILE,'iceberg',TOKEN))
        self.assertIsNone(result['reader_evidence_token'])
        self.assertTrue(result['reader_cleanup_confirmed'] and result['kernel_cleanup_confirmed'])
        self.assertEqual(result['code_cells'],3);build.assert_not_called()
        self.assertEqual(execution.call_count,1);self.assertEqual(validation.call_count,1);self.assertEqual(binding.call_count,1)
        self.assertEqual([c.args for c in sources.call_args_list],[('iceberg',),('iceberg',)])
        directory,_,notebook,_=captured[0]
        self.assertIn('kind="iceberg"',notebook.cells[3].source)
        inputs=json.loads((directory/'inputs.json').read_text())
        self.assertEqual((inputs['profile'],inputs['kind'],inputs['training_token']),(T.LAKE_PROFILE,'iceberg',TOKEN))
        self.assertIsNone(inputs['binary_sha256']);self.assertEqual(inputs['template_sha256'],T.template_sha256('iceberg'))
        self.assertFalse((directory/'request.json').exists());self.assertTrue((directory/'executed.ipynb').is_file())
        self.assertEqual(json.loads((directory/'result.json').read_text()),{k:v for k,v in result.items() if k!='evidence'})

    def test_invalid_or_mixed_cli_arguments_never_allocate(self):
        cases=[['--lake-training-token'],['--lake-training-token',TOKEN,'--training-token',TOKEN],
               ['--training-token',TOKEN,'--lake-training-token',TOKEN],['--lake-training-token',TOKEN,'--request-stdin'],
               ['--request-stdin','--lake-training-token',TOKEN],['--lake-training-token',TOKEN,TOKEN]]
        for arguments in cases:
            with self.subTest(arguments=arguments),patch.object(R,'STATE',self.state),patch.object(R,'execute') as execution, \
                 patch.object(R.subprocess,'run') as build,redirect_stdout(io.StringIO()) as output:
                self.assertEqual(R.main(arguments),1)
                self.assertEqual(json.loads(output.getvalue())['error_code'],'ARGUMENTS_INVALID')
                self.assertFalse(self.state.exists());execution.assert_not_called();build.assert_not_called()
        for token in ('',False,'../escape','A'*32,'a'*31,TOKEN+'\n'):
            with self.subTest(token=token),patch.object(R,'STATE',self.state),redirect_stdout(io.StringIO()) as output:
                self.assertEqual(R.main(['--lake-training-token',token]),1)
                self.assertEqual(json.loads(output.getvalue())['error_code'],'NOTEBOOK_TRAINING_TOKEN_INVALID')
                self.assertFalse(self.state.exists())

    def test_wrong_public_profile_token_or_bound_training_values_refuse_success(self):
        for field in ('profile','token','run','model','metrics','source'):
            with self.subTest(field=field):
                code,result,_,build,_,_,binding,_=self.main(change=field)
                self.assertEqual(code,1);self.assertEqual(result['result'],'failed')
                self.assertEqual(result['error_code'],'NOTEBOOK_SETUP_FAILED')
                self.assertEqual(binding.call_count,0 if field in ('profile','token') else 1)
                build.assert_not_called()

    def test_saved_lake_intent_requires_exact_kind_profile_token_and_integer_schema(self):
        for field in ('kind','profile','token','schema','extra','missing'):
            with self.subTest(field=field):
                code,result,_,build,_,_,binding,_=self.main(intent_change=field)
                self.assertEqual(code,1);self.assertEqual(result['result'],'failed')
                binding.assert_not_called();build.assert_not_called()

    def test_changed_source_or_unexpected_reader_outputs_refuse_success(self):
        code,result,*_=self.main(source_changed=True)
        self.assertEqual(code,1);self.assertEqual(result['result'],'failed')
        for name in ('reader-intent.json','reader-status.json','build.log','shirokuma','dangling'):
            with self.subTest(name=name):
                code,result,_,build,*_=self.main(unexpected=name)
                self.assertEqual(code,1);self.assertEqual(result['result'],'failed');build.assert_not_called()

    def test_first_cancellation_survives_source_failure_and_unknown_cleanup(self):
        original=R.NotebookInterrupted(signal.SIGTERM)
        for cleanup in (True,False):
            with self.subTest(cleanup=cleanup):
                code,result,*_=self.main(error=original,source_changed=True,cleanup=cleanup)
                self.assertEqual(code,143);self.assertEqual(result['signal'],signal.SIGTERM)
                self.assertEqual(result['error_code'],'INTERRUPTED')
                self.assertEqual(result['result'],'interrupted' if cleanup else 'unknown')
                self.assertEqual(result['kernel_cleanup_confirmed'],cleanup)

    def test_final_receipt_save_failure_does_not_claim_success(self):
        code,result,*_=self.main(save_failure=True)
        self.assertEqual(code,1);self.assertEqual(result['result'],'unknown')
        self.assertEqual(result['error_code'],'NOTEBOOK_EVIDENCE_SAVE_FAILED')

    def test_parent_kind_token_and_reader_environment_are_never_inherited(self):
        parent={T.KIND_ENV:'untrusted',T.TOKEN_ENV:'f'*32,'PYTHONPATH':'/untrusted/python',
                'SHIROKUMA_NOTEBOOK_REQUEST_PATH':'/untrusted/request','SHIROKUMA_NOTEBOOK_REQUEST_SHA256':'e'*64,
                'SHIROKUMA_NOTEBOOK_BINARY':'/untrusted/binary','SHIROKUMA_NOTEBOOK_RUN_DIR':'/untrusted/run'}
        before={key:os.environ.get(key) for key in parent}
        for kind in ('synthetic','iceberg','reader'):
            directory=self.root/kind;directory.mkdir(mode=0o700)
            token=None if kind=='reader' else TOKEN;binary=directory/'shirokuma' if kind=='reader' else None
            with patch.dict(os.environ,parent):
                environment=R.kernel_environment(directory,binary,training_token=token,kind='synthetic' if kind=='reader' else kind)
                self.assertEqual({key:os.environ[key] for key in parent},parent)
            self.assertEqual(environment['SHIROKUMA_NOTEBOOK_RUN_DIR'],str(directory))
            self.assertNotIn('PYTHONPATH',environment);self.assertNotIn('SHIROKUMA_NOTEBOOK_REQUEST_PATH',environment)
            self.assertNotIn('SHIROKUMA_NOTEBOOK_REQUEST_SHA256',environment)
            if kind=='iceberg':self.assertEqual(environment[T.KIND_ENV],'iceberg')
            else:self.assertNotIn(T.KIND_ENV,environment)
            if token:self.assertEqual(environment[T.TOKEN_ENV],TOKEN);self.assertNotIn('SHIROKUMA_NOTEBOOK_BINARY',environment)
            else:self.assertNotIn(T.TOKEN_ENV,environment)
        self.assertEqual({key:os.environ.get(key) for key in parent},before)

    def test_unknown_kind_or_mixed_internal_inputs_refused_before_any_allocation(self):
        cases=[dict(kind='unknown',training_token=TOKEN),dict(kind=False,training_token=TOKEN),
               dict(kind='iceberg'),dict(kind='iceberg',training_token=TOKEN,request=REQUEST),
               dict(kind='iceberg',training_token=False),dict(kind='iceberg',training_token=TOKEN,binary=self.root/'binary')]
        for index,values in enumerate(cases):
            for entry in ('environment','execute'):
                directory=self.root/(str(index)+'-'+entry);directory.mkdir(mode=0o700)
                kwargs=dict(values);binary=kwargs.pop('binary',None)
                with self.subTest(values=values,entry=entry),patch.object(R,'AsyncKernelManager') as manager:
                    with self.assertRaises(ValueError):
                        if entry=='environment':R.kernel_environment(directory,binary,**kwargs)
                        else:asyncio.run(R.execute(directory,binary,object(),**kwargs))
                    manager.assert_not_called();self.assertEqual(list(directory.iterdir()),[])

    def test_owned_kernel_receives_lake_kind_and_sixty_second_deadline(self):
        directory=self.root/'execution';directory.mkdir(mode=0o700)
        channels=NS(start_channels=Mock(),wait_for_ready=AsyncMock())
        manager=NS(provisioner=NS(pid=234,pgid=234),start_kernel=AsyncMock(),client=Mock(return_value=channels))
        client=NS(kc=None)
        observed={'result':'passed','error':None,'reader_cleanup_confirmed':True,'kernel_cleanup_confirmed':True}
        with patch.object(R,'AsyncKernelManager',return_value=manager),patch.object(R,'NotebookClient',return_value=client), \
             patch.object(R,'execute_owned',new=AsyncMock(return_value=observed)) as owned:
            result=asyncio.run(R.execute(directory,None,object(),training_token=TOKEN,kind='iceberg'))
        self.assertEqual(result['kernel_pid'],234)
        environment=manager.start_kernel.call_args.kwargs['env']
        self.assertEqual(environment[T.KIND_ENV],'iceberg');self.assertEqual(environment[T.TOKEN_ENV],TOKEN)
        self.assertEqual(owned.call_args.kwargs['timeout'],60)
        self.assertNotIn('request',owned.call_args.kwargs)
        self.assertNotIn('SHIROKUMA_NOTEBOOK_BINARY',environment)

    def test_sources_choose_only_registered_template_and_reject_unknown_kind(self):
        synthetic=R.training_sources();lake=R.training_sources('iceberg')
        self.assertIn('examples/local_training_review.ipynb',synthetic)
        self.assertNotIn('examples/local_lake_training_review.ipynb',synthetic)
        self.assertIn('examples/local_lake_training_review.ipynb',lake)
        self.assertNotIn('examples/local_training_review.ipynb',lake)
        contract='tools/local_experiment/query_read/contract.py'
        self.assertIn(contract,lake);self.assertNotIn(contract,synthetic)
        self.assertEqual({k:v for k,v in synthetic.items() if not k.startswith('examples/')},
                         {k:v for k,v in lake.items() if not k.startswith('examples/') and k!=contract})
        with self.assertRaises(ValueError):R.training_sources('unknown')


if __name__=='__main__':unittest.main()
