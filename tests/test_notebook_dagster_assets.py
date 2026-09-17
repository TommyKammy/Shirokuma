"""Fixed export and fake materialization only; no Reader/API/kernel/model calls."""
from copy import deepcopy
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import signal
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.local_experiment.notebook_assets import run as A
from tests.test_local_notebook_client import reader_frames, encoded, mlflow_fixture


class FakeAPI:
    @staticmethod
    def asset(**options):
        def decorate(function):
            function.options = options
            return function
        return decorate
    RetryPolicy = staticmethod(lambda **kwargs: SimpleNamespace(**kwargs))
    MaterializeResult = staticmethod(lambda **kwargs: SimpleNamespace(**kwargs))
    MetadataValue = SimpleNamespace(null=lambda:None)


class Instance:
    def __init__(self, on_exit=None, suppress=False):
        self.on_exit, self.suppress, self.closed = on_exit, suppress, False
    def __enter__(self): return self
    def __exit__(self,*args):
        self.closed = True
        if self.on_exit is not None: raise self.on_exit
        return self.suppress


class Client:
    def __init__(self, output):
        self.output, self.calls = output, []
        self.error, self.failed_evaluations, self.unknown_cleanup = None, False, False
    def read_fixture(self):
        self.calls.append('read_fixture')
        frames = reader_frames()
        A.N._write(self.output/'reader-intent.json', {'command':'query select'})
        if self.error is not None:
            frames[1].update(result='interrupted',observation=None,error_code='INTERRUPTED')
        with (self.output/'reader.stdout.jsonl').open('xb') as stream:
            stream.write(encoded(frames)); os.fchmod(stream.fileno(),0o600)
        A.N._write(self.output/'reader-status.json', dict(schema_version=1,
                    state='interrupted' if self.error is not None else 'passed', pid=1234,pgid=1234,
                    process_start='synthetic-start',evidence_token=frames[0]['evidence_token'],
                    exit_code=130 if self.error is not None else 0,
                    child_exited=not self.unknown_cleanup,cleanup_verified=not self.unknown_cleanup,
                    error_code='NOTEBOOK_INTERRUPTED' if self.error is not None else None))
        if self.error is not None:
            self.calls.append('reader-cleanup-awaited')
            raise self.error
        return frames[1]['observation']
    def read_evaluations(self):
        self.calls.append('read_evaluations')
        if self.failed_evaluations: raise A.N.NotebookError('NOTEBOOK_MLFLOW_READ_FAILED')
        index,_,response=mlflow_fixture()
        value=dict(experiment=A.N.EXPERIMENT,experiment_id='1',count=1,
                   runs=A.N._evaluation_rows(response,'1',index),scope='saved-evaluation-latest-metrics')
        A.N._write(self.output/'evaluations.json',value)
        return value


def materialize(assets, *, instance, raise_on_error):
    assert raise_on_error is True
    assert len(assets)==2
    events=[]
    for asset in assets:
        assert asset.options['retry_policy'].max_retries==0
        value=asset()
        assert value.metadata['template_sha256']==A.TEMPLATE_SHA256
        events.append(SimpleNamespace(asset_key=SimpleNamespace(path=[asset.options['name']])))
    return SimpleNamespace(success=True,run_id='00000000-0000-4000-8000-000000000001',
                           get_asset_materialization_events=lambda:events)


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.base=Path(self.temporary.name).resolve(); self.output=self.base/'run'
        self.binary=self.base/'shirokuma'; self.binary.write_bytes(b'fake-never-executed')
        self.instance=Instance()
        self.client=Client(self.output)
    def factory(self,path,overrides):
        self.assertEqual(path,str(self.output/'dagster'))
        self.assertEqual(overrides,{'telemetry':{'enabled':False}})
        return self.instance
    def execute(self, materializer=materialize):
        return A.execute(self.output,self.binary,materializer=materializer,instance_factory=self.factory,
                         api=FakeAPI,client=self.client)
    def saved(self): return json.loads((self.output/'result.json').read_bytes())

    def test_exact_export_is_private_deterministic_and_does_not_execute_cells(self):
        original=A.TEMPLATE.read_bytes()
        manifest=A.export(self.output)
        self.assertEqual(A.verify_export(self.output),manifest)
        self.assertEqual((self.output/'defs.py').read_bytes(),A.DEFINITIONS)
        self.assertEqual(manifest['template_sha256'],A.digest(original))
        self.assertEqual([x['key'] for x in manifest['assets']],list(A.ASSETS))
        self.assertEqual(self.output.stat().st_mode&0o777,0o700)
        for name in ('defs.py','manifest.json'): self.assertEqual((self.output/name).stat().st_mode&0o777,0o600)
        self.assertEqual(A.TEMPLATE.read_bytes(),original)
        with self.assertRaises(FileExistsError): A.export(self.output)

    def test_changed_notebook_hash_outputs_and_code_are_rejected_before_export(self):
        raw=A.TEMPLATE.read_bytes(); value=json.loads(raw)
        code=next(c for c in value['cells'] if c['cell_type']=='code')
        code['source']=['raise RuntimeError("arbitrary-code")']
        changed=self.base/'changed.ipynb'; changed.write_text(json.dumps(value))
        with patch.object(A,'TEMPLATE',changed):
            with self.assertRaisesRegex(A.AssetError,'TEMPLATE_CHANGED'): A.export(self.output)
            with patch.object(A,'TEMPLATE_SHA256',A.digest(changed.read_bytes())):
                with self.assertRaisesRegex(A.AssetError,'CODE_CONTRACT'): A.export(self.output)
        self.assertFalse(self.output.exists())
        value=json.loads(raw); next(c for c in value['cells'] if c['cell_type']=='code')['outputs']=[{'text':'private'}]
        changed.write_text(json.dumps(value))
        with patch.object(A,'TEMPLATE',changed),patch.object(A,'TEMPLATE_SHA256',A.digest(changed.read_bytes())):
            with self.assertRaisesRegex(A.AssetError,'CODE_CONTRACT'): A.export(self.output)

    def test_two_materializations_reuse_client_keep_rows_private_and_restore_environment(self):
        A.export(self.output)
        with patch.dict(os.environ,{'SHIROKUMA_NOTEBOOK_RUN_DIR':'preserved','SHIROKUMA_NOTEBOOK_BINARY':'prior'}):
            result=self.execute()
            self.assertEqual(os.environ['SHIROKUMA_NOTEBOOK_RUN_DIR'],'preserved')
            self.assertEqual(os.environ['SHIROKUMA_NOTEBOOK_BINARY'],'prior')
        self.assertEqual(self.client.calls,['read_fixture','read_evaluations'])
        self.assertTrue(self.instance.closed)
        self.assertEqual((result['result'],result['reader_cleanup_confirmed']),('passed',True))
        self.assertEqual([row['key'] for row in result['assets']],list(A.ASSETS))
        self.assertIsNone(result['assets'][1]['metadata']['cost'])
        self.assertNotIn('Shirokuma',(self.output/'result.json').read_text())
        self.assertIn('Shirokuma',(self.output/'reader.stdout.jsonl').read_text())
        with self.assertRaises(FileExistsError): self.execute()
        self.assertEqual(self.client.calls,['read_fixture','read_evaluations'])

    def test_changed_generated_code_refused_without_import_or_calls(self):
        A.export(self.output)
        (self.output/'defs.py').write_bytes(A.DEFINITIONS+b'raise Exception("untrusted")\n')
        with self.assertRaisesRegex(A.AssetError,'EXPORT_CHANGED'): self.execute()
        self.assertEqual(self.client.calls,[])

    def test_converter_change_invalidates_prior_export(self):
        manifest=A.export(self.output)
        self.assertEqual(manifest['converter_sha256'],A.digest(Path(A.__file__).read_bytes()))
        other=self.base/'other_converter.py'; other.write_text('raise Exception("not executed")\n')
        with patch.object(A,'__file__',str(other)):
            with self.assertRaisesRegex(A.AssetError,'EXPORT_CHANGED'): self.execute()
        self.assertEqual(self.client.calls,[])

    def test_failed_second_asset_does_not_claim_second_materialization(self):
        A.export(self.output); self.client.failed_evaluations=True
        with self.assertRaises(A.AssetError): self.execute()
        value=self.saved()
        self.assertEqual((value['result'],value['reader_cleanup_confirmed']),('failed',True))
        self.assertEqual([x['key'] for x in value['assets']],[A.ASSETS[0]])
        self.assertEqual(self.client.calls,['read_fixture','read_evaluations'])

    def test_missing_event_or_success_without_running_assets_never_passes(self):
        A.export(self.output)
        def false_success(assets,**kwargs):
            return SimpleNamespace(success=True,run_id='00000000-0000-4000-8000-000000000001',
                                   get_asset_materialization_events=lambda:[])
        with self.assertRaises(A.AssetError): self.execute(false_success)
        self.assertEqual(self.saved()['result'],'failed')
        self.assertEqual(self.client.calls,[])

    def test_retry_and_out_of_order_calls_are_refused(self):
        A.export(self.output)
        assets=A.build_assets(A._state(),FakeAPI,self.client)
        with self.assertRaisesRegex(A.AssetError,'ORDER_INVALID'): assets[1]()
        assets[0]()
        with self.assertRaisesRegex(A.AssetError,'RETRY_REFUSED'): assets[0]()
        self.assertEqual(self.client.calls,['read_fixture'])

    def test_cancel_waits_client_cleanup_and_survives_instance_exit_error(self):
        A.export(self.output); original=KeyboardInterrupt('first')
        self.client.error=original; self.instance=Instance(on_exit=OSError('secondary'))
        with self.assertRaises(KeyboardInterrupt) as raised: self.execute()
        self.assertIs(raised.exception,original)
        self.assertEqual(self.client.calls,['read_fixture','reader-cleanup-awaited'])
        self.assertEqual((self.saved()['result'],self.saved()['reader_cleanup_confirmed']),('interrupted',True))

    def test_cancel_is_not_suppressed_by_instance_or_secondary_final_save(self):
        A.export(self.output); original=KeyboardInterrupt('first')
        self.client.error=original; self.instance=Instance(suppress=True)
        write=A.N._write
        def fail_final(path,value,**kwargs):
            if path.name=='result.json': raise OSError('secondary-final-write')
            return write(path,value,**kwargs)
        with patch.object(A.N,'_write',side_effect=fail_final):
            with self.assertRaises(KeyboardInterrupt) as raised: self.execute()
        self.assertIs(raised.exception,original)

    def test_sigterm_is_delivered_as_cancellation_in_synchronous_client_scope(self):
        old={s:signal.getsignal(s) for s in (signal.SIGINT,signal.SIGTERM)}
        def term(): signal.getsignal(signal.SIGTERM)(signal.SIGTERM,None)
        state=A._state()
        assets=A.build_assets(state,FakeAPI,SimpleNamespace(read_fixture=term,read_evaluations=lambda:None))
        with self.assertRaises(A.AssetsInterrupted) as raised: assets[0]()
        self.assertEqual(raised.exception.signum,signal.SIGTERM)
        self.assertIs(state['first_error'],raised.exception)
        self.assertEqual({s:signal.getsignal(s) for s in old},old)

    def test_unconfirmed_cleanup_is_unknown_and_never_retried(self):
        A.export(self.output); original=KeyboardInterrupt('first')
        self.client.error=original; self.client.unknown_cleanup=True
        with self.assertRaises(KeyboardInterrupt) as raised: self.execute()
        self.assertIs(raised.exception,original)
        self.assertEqual((self.saved()['result'],self.saved()['reader_cleanup_confirmed']),('unknown',False))
        self.assertEqual(self.client.calls,['read_fixture','reader-cleanup-awaited'])

    def test_real_pinned_definitions_validate_without_materialization(self):
        import dagster
        with patch.object(A.N,'read_fixture',side_effect=AssertionError('no Reader')), \
             patch.object(A.N,'read_evaluations',side_effect=AssertionError('no HTTP')):
            definitions=A.definitions()
            dagster.Definitions.validate_loadable(definitions)
            self.assertEqual({tuple(key.path) for item in definitions.assets for key in item.keys},
                             {(name,) for name in A.ASSETS})
        self.assertIsNone(dagster.MetadataValue.null().value)

    def real_engine(self):
        import dagster
        captured={}
        @contextmanager
        def instance(path,overrides):
            with dagster.DagsterInstance.local_temp(path,overrides=overrides) as actual:
                try:
                    yield actual
                finally:
                    runs=actual.get_runs()
                    captured['status']=runs[0].status.value
                    captured['events']=[tuple(record.event_log_entry.dagster_event.asset_key.path)
                        for record in actual.get_records_for_run(runs[0].run_id,
                            of_type=dagster.DagsterEventType.ASSET_MATERIALIZATION).records]
        with patch.object(A.N,'read_fixture',side_effect=AssertionError('real Reader prohibited')), \
             patch.object(A.N,'read_evaluations',side_effect=AssertionError('real HTTP prohibited')):
            try:
                A.execute(self.output,self.binary,instance_factory=instance,client=self.client)
            except A.AssetError:
                if not self.client.failed_evaluations: raise
        return captured

    def test_real_dagster_engine_two_events_with_fake_client(self):
        A.export(self.output)
        captured=self.real_engine()
        self.assertEqual(captured,{'status':'SUCCESS','events':[(name,) for name in A.ASSETS]})
        self.assertEqual(self.saved()['result'],'passed')
        self.assertEqual(self.client.calls,['read_fixture','read_evaluations'])

    def test_real_dagster_engine_second_failure_has_no_second_materialization(self):
        A.export(self.output); self.client.failed_evaluations=True
        captured=self.real_engine()
        self.assertEqual(captured,{'status':'FAILURE','events':[(A.ASSETS[0],)]})
        self.assertEqual(self.saved()['result'],'failed')
        self.assertEqual(self.client.calls,['read_fixture','read_evaluations'])

    def test_main_rejects_arguments_before_export_build_or_reads(self):
        with patch.object(A,'export') as export,patch.object(A.subprocess,'run') as build,patch('sys.stdout',io.StringIO()):
            self.assertEqual(A.main(['arbitrary.ipynb']),1)
            export.assert_not_called(); build.assert_not_called()

    def test_main_post_success_cancel_keeps_business_proof_but_never_reports_passed(self):
        original=A.AssetsInterrupted(signal.SIGTERM)
        class InterruptOnExit:
            def __enter__(self): return self
            def __exit__(self,*args): raise original
        saved=[]
        def execute(output,binary):
            result=dict(result='passed',error_code=None,reader_cleanup_confirmed=True)
            A.N._write(output/'result.json',result)
            saved.append((output,(output/'result.json').read_bytes()))
            return result
        stdout=io.StringIO()
        with patch.object(A,'STATE',self.base/'state'), patch.object(A,'_signals',return_value=InterruptOnExit()), \
             patch.object(A.subprocess,'run'), patch.object(A,'execute',side_effect=execute),patch('sys.stdout',stdout):
            self.assertEqual(A.main([]),143)
        value=json.loads(stdout.getvalue())
        self.assertEqual((value['result'],value['materialization_result']),('interrupted','passed'))
        output,raw=saved[0]
        self.assertEqual((output/'result.json').read_bytes(),raw)
        self.assertEqual(json.loads((output/'owner-result.json').read_bytes())['result'],'interrupted')

    def test_main_late_cleanup_unknown_cannot_publish_prior_success(self):
        stdout=io.StringIO()
        with patch.object(A,'STATE',self.base/'state'),patch.object(A.subprocess,'run'), \
             patch.object(A,'execute',return_value={'result':'passed'}), \
             patch.object(A,'reader_closed',return_value=False),patch('sys.stdout',stdout):
            self.assertEqual(A.main([]),1)
        value=json.loads(stdout.getvalue())
        self.assertEqual((value['result'],value['materialization_result'],value['reader_cleanup_confirmed']),
                         ('unknown','passed',False))

    def test_missing_receipt_and_broken_symlink_never_prove_reader_cleanup(self):
        A.export(self.output)
        self.assertTrue(A.reader_closed(self.output))
        (self.output/'reader-status.json').symlink_to(self.output/'absent')
        self.assertFalse(A.reader_closed(self.output))
        (self.output/'reader-status.json').unlink()
        (self.output/'reader-intent.json').symlink_to(self.output/'absent')
        self.assertFalse(A.reader_closed(self.output))


if __name__=='__main__': unittest.main()
