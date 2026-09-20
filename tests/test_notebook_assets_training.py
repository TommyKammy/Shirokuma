"""Private fake autolog evidence and real Dagster engine; no model fits or services."""
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.local_experiment.notebook_assets import training as T
from tests.test_notebook_dagster_assets import FakeAPI, Instance
from tests import test_mlflow_autolog as F

L = F.L


class Worker:
    def __init__(self):
        self.calls, self.error, self.closed_value, self.change = [], None, True, None
    def run(self, output, token, backup=None, *, source_sha256):
        self.calls.append((output, token, backup, source_sha256))
        if self.error is not None: raise self.error
        result = dict(schema_version=1, profile='owned-autolog-worker-v1', result='passed',
            training_token=token, source_sha256=source_sha256, reused=True,
            cleanup_verified=True, autolog=T.A.public(T.A.recorded(token)))
        if self.change: self.change(result)
        return result
    def closed(self, output): return self.closed_value


class TrainingAssetTests(unittest.TestCase):
    def setUp(self):
        self.fixture = F.AutologTests(); self.addCleanup(self.fixture.doCleanups); self.fixture.setUp()
        # Populate a completed receipt with the existing fake SDK and arithmetic fixture.
        self.receipt = self.fixture.owner().run(L.TOKEN)
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.output = self.base/('a'*32)
        self.request = T.request(L.TOKEN)
        self.worker, self.instance, self.events = Worker(), Instance(), []
        self.export()

    def export(self, output=None): return T.export(output or self.output, self.request)

    def materialize(self, assets, *, instance, raise_on_error):
        self.assertTrue(raise_on_error)
        for asset in assets:
            self.assertEqual(asset.options['retry_policy'].max_retries, 0)
            asset()
            self.events.append(SimpleNamespace(asset_key=SimpleNamespace(path=[asset.options['name']])))
        return SimpleNamespace(success=True, run_id='00000000-0000-4000-8000-000000000001',
                               get_asset_materialization_events=lambda:self.events)

    def execute(self, **kwargs):
        return T.execute(self.output, materializer=kwargs.pop('materializer',self.materialize),
            instance_factory=kwargs.pop('instance_factory',lambda *a, **kw:self.instance),
            api=FakeAPI, worker=self.worker, **kwargs)

    def saved(self, name): return json.loads((self.output/name).read_text())

    def test_registered_export_is_private_and_binds_request_without_code_interpolation(self):
        request, manifest = T.verify_export(self.output)
        self.assertEqual(request,self.request)
        self.assertEqual(manifest['request_sha256'],T.digest(request))
        self.assertNotIn(L.TOKEN,(self.output/'defs.py').read_text())
        self.assertEqual(manifest['template_sha256'],T.TEMPLATE_SHA256)
        self.assertEqual(self.output.stat().st_mode & 0o7777,0o700)
        self.assertTrue(all(p.stat().st_mode & 0o7777 == 0o600 for p in self.output.iterdir()))
        with patch.object(T.A,'Snapshot',side_effect=AssertionError('must not read training')):
            definitions = T.definitions_from_export(self.output/'defs.py',T.digest(request))
        self.assertEqual(len(definitions.assets),2)
        self.assertEqual(set(p.name for p in self.output.iterdir()),{'request.json','defs.py','manifest.json'})

    def test_two_assets_bind_source_and_metadata_then_completed_repeat(self):
        before = self.fixture.files()
        result = self.execute()
        self.assertEqual(result['result'],'passed')
        self.assertEqual([e.asset_key.path[0] for e in self.events],list(T.ASSETS))
        source = self.saved('source.json')
        self.assertEqual(self.worker.calls[0][1:],(L.TOKEN,None,T.digest(source)))
        self.assertEqual(result['autolog']['autolog'],T.A.public(self.receipt))
        self.assertTrue(result['autolog']['reused'])
        self.assertTrue(self.instance.closed)
        self.assertEqual(result['assets'][T.ASSETS[0]]['train_rows'],48)
        self.assertNotIn('coefficients',json.dumps(result['assets']))
        self.assertNotIn('dataset',json.dumps(result['assets']))
        self.assertEqual(before,self.fixture.files())
        self.output = self.base/('b'*32); self.events=[]; self.export()
        self.assertEqual(self.execute()['autolog']['autolog'],result['autolog']['autolog'])
        self.assertEqual(before,self.fixture.files())

    def test_request_and_export_changes_refuse_before_worker(self):
        original=(self.output/'request.json').read_bytes()
        changed=dict(self.request,training_token='f'*32)
        (self.output/'request.json').write_bytes(T.N._canonical(changed))
        with self.assertRaisesRegex(T.TrainingError,'TRAINING_EXPORT_CHANGED'): self.execute()
        self.assertFalse(self.worker.calls)
        (self.output/'request.json').write_bytes(original)
        (self.output/'defs.py').write_text('raise AssertionError("arbitrary code")\n')
        with self.assertRaisesRegex(T.TrainingError,'TRAINING_EXPORT_CHANGED'): self.execute()
        self.assertFalse(self.worker.calls)

    def test_source_failure_emits_no_autolog_and_no_retry_in_same_export(self):
        with patch.object(T.A,'Snapshot',side_effect=ValueError('private source error')):
            with self.assertRaises(ValueError): self.execute()
        self.assertFalse(self.worker.calls); self.assertEqual(self.events,[])
        self.assertEqual(self.saved('owner-result.json')['result'],'failed')
        with self.assertRaises(FileExistsError): self.execute()
        self.assertFalse(self.worker.calls)

    def test_source_change_between_assets_prevents_worker(self):
        def change(assets, **kwargs):
            assets[0]()
            path=self.fixture.state/'lake-training'/L.TOKEN/'workload.json'
            path.write_bytes(path.read_bytes()+b' ')
            assets[1]()
        with self.assertRaises(Exception): self.execute(materializer=change)
        self.assertFalse(self.worker.calls)
        self.assertEqual(self.saved('owner-result.json')['result'],'failed')

    def test_second_failure_has_only_first_event_and_no_success_owner(self):
        self.worker.error=ValueError('not a public message')
        with self.assertRaises(ValueError): self.execute()
        self.assertEqual([e.asset_key.path[0] for e in self.events],[T.ASSETS[0]])
        owner=self.saved('owner-result.json')
        self.assertEqual(owner['result'],'failed'); self.assertTrue(owner['worker_cleanup_verified'])
        self.assertNotIn('not a public message',json.dumps(owner))

    def test_mismatched_worker_result_or_unknown_cleanup_prevents_second_event(self):
        for field,value in [('training_token','f'*32),('source_sha256','f'*64),('cleanup_verified',False)]:
            with self.subTest(field=field):
                if self.worker.calls:
                    self.output=self.base/('%032x' % (len(self.worker.calls)+2)); self.export()
                self.events=[]
                self.worker.change=lambda result,k=field,v=value:result.update({k:v})
                with self.assertRaisesRegex(T.TrainingError,'TRAINING_WORKER_UNVERIFIED'):self.execute()
                self.assertEqual(len(self.events),1)
        self.output=self.base/('c'*32); self.export(); self.events=[]
        self.worker.change=None; self.worker.closed_value=False
        with self.assertRaisesRegex(T.TrainingError,'TRAINING_WORKER_UNVERIFIED'):self.execute()
        self.assertFalse(self.saved('owner-result.json')['worker_cleanup_verified'])

    def test_first_interrupt_survives_instance_close_and_secondary_write_failure(self):
        original=KeyboardInterrupt(); self.worker.error=original
        self.instance=Instance(on_exit=OSError('close'))
        write=T.N._write
        def failed_write(path,value,**kwargs):
            if path.name=='owner-result.json': raise OSError('secondary')
            return write(path,value,**kwargs)
        with patch.object(T.N,'_write',side_effect=failed_write):
            with self.assertRaises(KeyboardInterrupt) as caught:self.execute()
        self.assertIs(caught.exception,original)

    def test_business_pass_retained_when_instance_close_fails(self):
        self.instance=Instance(on_exit=OSError('close'))
        with self.assertRaises(OSError):self.execute()
        self.assertEqual(self.saved('result.json')['result'],'passed')
        self.assertEqual(self.saved('owner-result.json')['result'],'failed')
        self.assertEqual(len(self.events),2)

    def test_owner_publication_failure_does_not_leave_passed_owner(self):
        state=self.base/'attempts'; original=T.execute
        def execute(output):
            self.output=output
            return original(output,materializer=self.materialize,instance_factory=lambda *a,**k:self.instance,
                            api=FakeAPI,worker=self.worker)
        class Broken:
            def write(self,text): raise OSError('output')
            def flush(self): pass
        with patch.object(T,'STATE',state),patch.object(T,'execute',side_effect=execute):
            with self.assertRaises(OSError):T.main(['--training-token',L.TOKEN],stdout=Broken())
        self.assertEqual(self.saved('result.json')['result'],'passed')
        self.assertEqual(self.saved('owner-result.json')['result'],'failed')

    def test_invalid_request_has_no_allocation_and_initial_notebook_has_no_actions(self):
        state=self.base/'never-created'
        for args in ([],['--training-token','../x'],['--training-token',L.TOKEN,'--other','x'],
                     ['--training-token',L.TOKEN,'--backup-manifest','/tmp/backup-manifest.json']):
            with patch.object(T,'STATE',state):
                output=io.StringIO(); self.assertEqual(T.main(args,stdout=output),1)
                self.assertEqual(json.loads(output.getvalue())['error_code'],'TRAINING_REQUEST_INVALID')
        self.assertFalse(state.exists())
        with patch.object(T,'preview_source',side_effect=AssertionError('read')), \
             patch.object(T,'materialize_training',side_effect=AssertionError('fit')), \
             patch.dict(sys.modules,{'IPython':SimpleNamespace(),
                 'IPython.display':SimpleNamespace(JSON=lambda value:value,display=lambda value:None)}):
            context={}
            for cell in T.CODE_CELLS: exec(compile(cell,'registered-cell','exec'),context)
        self.assertFalse(context['run_training']); self.assertIsNone(context['result'])

    def test_assets_refuse_reverse_order_and_duplicate_calls(self):
        assets=T.build_assets(self.output,self.request,T._state(),api=FakeAPI,worker=self.worker)
        with self.assertRaisesRegex(T.TrainingError,'TRAINING_ASSET_ORDER_INVALID'):assets[1]()
        assets[0]()
        with self.assertRaisesRegex(T.TrainingError,'TRAINING_ASSET_RETRY_REFUSED'):assets[0]()
        self.assertFalse(self.worker.calls)

    def test_real_dagster_materializes_two_assets_with_fake_worker(self):
        result=T.execute(self.output,worker=self.worker)
        self.assertEqual(result['result'],'passed')
        self.assertEqual(set(result['assets']),set(T.ASSETS))
        self.assertEqual(len(self.worker.calls),1)
        self.assertTrue(result['worker_cleanup_verified'])

    def test_real_dagster_second_failure_keeps_first_materialization(self):
        self.worker.error=ValueError('fake worker failure')
        with self.assertRaises(Exception):T.execute(self.output,worker=self.worker)
        import dagster
        with dagster.DagsterInstance.local_temp(str(self.output/'dagster'),overrides={'telemetry':{'enabled':False}}) as instance:
            records=instance.get_run_records()
            self.assertEqual(len(records),1)
            self.assertEqual(records[0].dagster_run.status,dagster.DagsterRunStatus.FAILURE)
            events=instance.all_logs(records[0].dagster_run.run_id)
            keys=[event.dagster_event.asset_key.path for event in events
                  if event.is_dagster_event and event.dagster_event.is_step_materialization]
            self.assertEqual(keys,[[T.ASSETS[0]]])
        self.assertEqual(self.saved('owner-result.json')['result'],'failed')


if __name__=='__main__': unittest.main()
