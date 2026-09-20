"""Selected asset requests with fake reads and real local Dagster only."""
from contextlib import contextmanager
from copy import deepcopy
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
from tools.local_experiment.notebook import selection as S
from tests.test_notebook_dagster_assets import Client, FakeAPI, Instance
from tests.test_local_notebook_client import reader_frames
from tests.test_query_read_contract import identity, schema, result

REQUEST = {'command':'select', 'table':'smoke.fixture_v1', 'columns':['name','id'], 'limit':1}


def frames(request=REQUEST):
    raw = result(request, rows=[['Shirokuma',1],['Polaris',2]])
    value = reader_frames()
    value[-1]['observation'] = A.N.C.project_rows(raw, request, schema(), identity())
    return value


class SelectedClient(Client):
    def __init__(self, output):
        super().__init__(output)
        self.requests, self.environment = [], None
        self.mutate = None
    def read_select(self, request):
        self.requests.append(deepcopy(request))
        self.environment = {name:os.environ.get(name) for name in A.ENV_KEYS}
        try:
            with patch('tests.test_notebook_dagster_assets.reader_frames', side_effect=lambda:frames(request)):
                return super().read_fixture()
        finally:
            self.calls[0] = 'read_select'
            if self.mutate is not None:
                self.mutate()


def materialize(assets, *, instance, raise_on_error):
    assert raise_on_error is True
    events = []
    for asset in assets:
        assert asset.options['retry_policy'].max_retries == 0
        asset()
        events.append(SimpleNamespace(asset_key=SimpleNamespace(path=[asset.options['name']])))
    return SimpleNamespace(success=True, run_id='00000000-0000-4000-8000-000000000001',
                           get_asset_materialization_events=lambda:events)


class SelectedAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.output = self.base/'run'
        self.binary = self.base/'shirokuma'; self.binary.write_bytes(b'not executed')
        self.client, self.instance = SelectedClient(self.output), Instance()
    def execute(self, **kwargs):
        return A.execute(self.output,self.binary,request=REQUEST,materializer=materialize,
                         instance_factory=lambda *a,**kw:self.instance,api=FakeAPI,client=self.client,**kwargs)
    def saved(self):
        return json.loads((self.output/'result.json').read_bytes())

    def test_custom_export_binds_private_canonical_request_and_registered_template(self):
        before = (A.ROOT/S.TEMPLATE_PATH).read_bytes()
        manifest = A.export(self.output, request=REQUEST)
        self.assertEqual(A.verify_export(self.output,request=REQUEST),manifest)
        self.assertEqual((manifest['schema_version'],manifest['profile']),(2,A.SELECTED_PROFILE))
        self.assertEqual(manifest['request_sha256'],S.digest(REQUEST))
        self.assertEqual(manifest['template_sha256'],S.TEMPLATE_SHA256)
        self.assertEqual(manifest['selection_sha256'],A.digest(Path(S.__file__).read_bytes()))
        self.assertEqual(S.load_request(self.output/'request.json',S.digest(REQUEST)),REQUEST)
        generated = (self.output/'defs.py').read_text()
        self.assertIn('definitions_from_export(__file__, "'+S.digest(REQUEST)+'")',generated)
        self.assertNotIn(REQUEST['table'],generated)
        self.assertNotIn('read_select(',generated)
        for name in ('defs.py','manifest.json','request.json'):
            self.assertEqual((self.output/name).stat().st_mode&0o777,0o600)
        self.assertEqual((A.ROOT/S.TEMPLATE_PATH).read_bytes(),before)

    def test_invalid_requests_fail_before_allocation(self):
        bad = [dict(REQUEST,limit=True),dict(REQUEST,limit=101),dict(REQUEST,snapshot_id=1),
               dict(REQUEST,where={}),dict(REQUEST,table='smoke.x;DROP'),dict(REQUEST,columns=['id','id'])]
        for value in bad:
            with self.subTest(value=value),self.assertRaises(S.SelectionError):
                A.export(self.output,request=value)
            self.assertFalse(self.output.exists())

    def test_request_tamper_or_wrong_binding_blocks_every_read(self):
        A.export(self.output,request=REQUEST)
        for kind in ('bytes','mode','symlink','binding'):
            with self.subTest(kind=kind):
                path = self.output/'request.json'
                if path.exists() or path.is_symlink(): path.unlink()
                path.write_bytes(S.canonical(REQUEST)); path.chmod(0o600)
                if kind == 'bytes': path.write_bytes(S.canonical(dict(REQUEST,limit=2)))
                if kind == 'mode': path.chmod(0o644)
                if kind == 'symlink':
                    copy = self.output/'copied.json'; copy.write_bytes(path.read_bytes()); copy.chmod(0o600)
                    path.unlink(); path.symlink_to(copy)
                request = dict(REQUEST,limit=2) if kind == 'binding' else REQUEST
                with self.assertRaises((A.AssetError,S.SelectionError)):
                    A.execute(self.output,self.binary,request=request,api=FakeAPI,client=self.client)
                self.assertEqual(self.client.calls,[])

    def test_custom_success_binds_receipts_metadata_frames_and_all_environment(self):
        A.export(self.output,request=REQUEST)
        inherited = {name:'inherited-'+str(i) for i,name in enumerate(A.ENV_KEYS)}
        with patch.dict(os.environ,inherited):
            value = self.execute()
            self.assertEqual({name:os.environ[name] for name in A.ENV_KEYS},inherited)
        self.assertEqual(self.client.environment,dict(zip(A.ENV_KEYS,
                         [str(self.output),str(self.binary),str(self.output/'request.json'),S.digest(REQUEST)])))
        self.assertEqual(self.client.requests,[REQUEST])
        self.assertEqual(self.client.calls,['read_select','read_evaluations'])
        self.assertTrue(self.instance.closed)
        self.assertEqual((value['result'],value['reader_cleanup_confirmed']),('passed',True))
        self.assertEqual([row['key'] for row in value['assets']],list(A.SELECTED_ASSETS))
        for name in ('result.json','execution-intent.json'):
            receipt = json.loads((self.output/name).read_bytes())
            self.assertEqual((receipt['schema_version'],receipt['profile']),(2,A.SELECTED_PROFILE))
            self.assertEqual(receipt['request_sha256'],S.digest(REQUEST))
            self.assertEqual(receipt['template_sha256'],S.TEMPLATE_SHA256)
        for asset in value['assets']:
            self.assertEqual(asset['metadata']['request_sha256'],S.digest(REQUEST))
            self.assertNotIn('rows',asset['metadata'])
        self.assertTrue(A.reader_closed(self.output,request=REQUEST))
        self.assertFalse(A.reader_closed(self.output))
        self.assertNotIn('Shirokuma',json.dumps(value))

    def test_default_clears_inherited_request_environment_and_restores_all_four(self):
        A.export(self.output)
        inherited = {name:'inherited' for name in A.ENV_KEYS}
        observed = []
        client = Client(self.output)
        read = client.read_fixture
        def fixture():
            observed.append({name:os.environ.get(name) for name in A.ENV_KEYS})
            return read()
        client.read_fixture = fixture
        with patch.dict(os.environ,inherited):
            value = A.execute(self.output,self.binary,materializer=materialize,
                              instance_factory=lambda *a,**kw:self.instance,api=FakeAPI,client=client)
            self.assertEqual({name:os.environ[name] for name in A.ENV_KEYS},inherited)
        self.assertIsNone(observed[0][S.REQUEST_PATH_ENV]); self.assertIsNone(observed[0][S.REQUEST_SHA_ENV])
        self.assertEqual(set(value),{'schema_version','profile','result','dagster_run_id','assets',
                                   'reader_cleanup_confirmed','error_code'})
        self.assertEqual((self.output/'defs.py').read_bytes(),A.DEFINITIONS)
        self.assertFalse((self.output/'request.json').exists())

    def test_post_read_tamper_prevents_success_without_retry(self):
        A.export(self.output,request=REQUEST)
        self.client.mutate = lambda:(self.output/'request.json').write_bytes(S.canonical(dict(REQUEST,limit=2)))
        with self.assertRaises(A.AssetError): self.execute()
        self.assertEqual(self.saved()['result'],'failed')
        self.assertTrue(self.saved()['reader_cleanup_confirmed'])
        self.assertEqual(self.client.calls,['read_select','read_evaluations'])

    def test_environment_setup_failure_restores_every_parent_value_without_reads(self):
        A.export(self.output,request=REQUEST)
        inherited = {name:'preserved' for name in A.ENV_KEYS}
        with patch.dict(os.environ,inherited):
            original_update = os.environ.update
            def fail_once(values):
                original_update({A.ENV_KEYS[0]:values[A.ENV_KEYS[0]]})
                raise OSError('partial environment setup')
            with patch.object(os.environ,'update',side_effect=fail_once):
                with self.assertRaises(A.AssetError): self.execute()
            self.assertEqual({name:os.environ[name] for name in A.ENV_KEYS},inherited)
        self.assertEqual(self.client.calls,[])
        self.assertEqual(self.saved()['result'],'failed')

    def test_changed_registered_template_refused_before_export(self):
        fake_root = self.base/'templates'; (fake_root/'examples').mkdir(parents=True)
        path = fake_root/S.TEMPLATE_PATH
        path.write_bytes((A.ROOT/S.TEMPLATE_PATH).read_bytes()+b'\n')
        with patch.object(A,'ROOT',fake_root),self.assertRaises(S.SelectionError):
            A.export(self.output,request=REQUEST)
        self.assertFalse(self.output.exists())

    def test_manifest_tamper_and_custom_export_used_as_default_are_refused(self):
        A.export(self.output,request=REQUEST)
        with self.assertRaises(A.AssetError):
            A.execute(self.output,self.binary,api=FakeAPI,client=self.client)
        manifest = json.loads((self.output/'manifest.json').read_bytes())
        manifest['request_sha256'] = '0'*64
        (self.output/'manifest.json').write_bytes(A.N._canonical(manifest))
        with self.assertRaises(A.AssetError): self.execute()
        self.assertEqual(self.client.calls,[])

    def test_first_cancel_survives_tamper_instance_close_and_restores_environment(self):
        A.export(self.output,request=REQUEST)
        original = A.AssetsInterrupted(signal.SIGTERM)
        self.client.error = original
        self.client.mutate = lambda:(self.output/'request.json').write_bytes(b'changed')
        self.instance = Instance(on_exit=OSError('secondary'))
        inherited = {name:'inherited' for name in A.ENV_KEYS}
        with patch.dict(os.environ,inherited):
            with self.assertRaises(A.AssetsInterrupted) as caught: self.execute()
            self.assertEqual({name:os.environ[name] for name in A.ENV_KEYS},inherited)
        self.assertIs(caught.exception,original)
        self.assertEqual(self.client.calls,['read_select','reader-cleanup-awaited'])
        self.assertEqual((self.saved()['result'],self.saved()['reader_cleanup_confirmed']),('interrupted',True))

    def test_unknown_cleanup_never_success_or_retry(self):
        A.export(self.output,request=REQUEST)
        original = KeyboardInterrupt(); self.client.error=original; self.client.unknown_cleanup=True
        with self.assertRaises(KeyboardInterrupt) as caught: self.execute()
        self.assertIs(caught.exception,original)
        self.assertEqual((self.saved()['result'],self.saved()['reader_cleanup_confirmed']),('unknown',False))
        self.assertEqual(self.client.calls,['read_select','reader-cleanup-awaited'])

    def test_custom_order_retry_and_incorrect_observation_are_refused(self):
        A.export(self.output,request=REQUEST)
        assets = A.build_assets(A._state(),FakeAPI,self.client,request=REQUEST)
        with self.assertRaisesRegex(A.AssetError,'ORDER_INVALID'): assets[1]()
        assets[0]()
        with self.assertRaisesRegex(A.AssetError,'RETRY_REFUSED'): assets[0]()
        wrong = SimpleNamespace(read_select=lambda request:reader_frames()[-1]['observation'])
        with self.assertRaisesRegex(A.AssetError,'READER_INVALID'):
            A.build_assets(A._state(),FakeAPI,wrong,request=REQUEST)[0]()

    def test_generated_helper_only_constructs_trusted_definitions_and_rejects_drift(self):
        import dagster
        A.export(self.output,request=REQUEST)
        before = {path.name:path.read_bytes() for path in self.output.iterdir()}
        with patch.object(A.N,'read_select',side_effect=AssertionError('no Reader')), \
             patch.object(A.N,'read_evaluations',side_effect=AssertionError('no API')):
            definitions = A.definitions_from_export(str(self.output/'defs.py'),S.digest(REQUEST))
            dagster.Definitions.validate_loadable(definitions)
            self.assertEqual({tuple(key.path) for item in definitions.assets for key in item.keys},
                             {(name,) for name in A.SELECTED_ASSETS})
        self.assertEqual({path.name:path.read_bytes() for path in self.output.iterdir()},before)
        with self.assertRaises(A.AssetError):
            A.definitions_from_export(str(self.output/'different.py'),S.digest(REQUEST))
        (self.output/'defs.py').write_bytes(b'raise AssertionError("never imported")')
        with self.assertRaises(A.AssetError):
            A.definitions_from_export(str(self.output/'defs.py'),S.digest(REQUEST))

    def test_real_dagster_engine_selected_assets_with_fake_client(self):
        import dagster
        A.export(self.output,request=REQUEST)
        captured = []
        @contextmanager
        def factory(path,overrides):
            with dagster.DagsterInstance.local_temp(path,overrides=overrides) as instance:
                yield instance
                run = instance.get_runs()[0]
                captured.append((run.status.value,[tuple(record.event_log_entry.dagster_event.asset_key.path)
                    for record in instance.get_records_for_run(run.run_id,
                        of_type=dagster.DagsterEventType.ASSET_MATERIALIZATION).records]))
        with patch.object(A.N,'read_select',side_effect=AssertionError('no actual Reader')), \
             patch.object(A.N,'read_evaluations',side_effect=AssertionError('no actual API')):
            value = A.execute(self.output,self.binary,request=REQUEST,instance_factory=factory,client=self.client)
        self.assertEqual(value['result'],'passed')
        self.assertEqual(captured,[('SUCCESS',[(name,) for name in A.SELECTED_ASSETS])])
        self.assertEqual(self.client.requests,[REQUEST])

    def test_main_input_bounds_refuse_before_allocation_build_or_execution(self):
        for raw in (b'{"command":"select","limit":true}',b'x'*4097,b'{}',
                    S.canonical(dict(REQUEST,limit=2))[:-2]+b',"limit":3}\n'):
            with self.subTest(raw=raw[:20]),patch.object(A,'STATE',self.base/'state'), \
                 patch.object(A,'export') as export,patch.object(A.subprocess,'run') as build, \
                 patch('sys.stdout',io.StringIO()) as stdout:
                self.assertEqual(A.main(['--request-stdin'],stdin=io.BytesIO(raw)),1)
                self.assertEqual(json.loads(stdout.getvalue())['error_code'],'NOTEBOOK_REQUEST_INVALID')
                export.assert_not_called(); build.assert_not_called()
                self.assertFalse((self.base/'state').exists())

    def test_main_owner_result_binds_request_and_preserves_post_success_cancel(self):
        original = A.AssetsInterrupted(signal.SIGTERM)
        class CancelOnExit:
            def __enter__(self): return self
            def __exit__(self,*args): raise original
        observed = []
        def execute(output,binary,*,request):
            self.assertEqual(request,REQUEST)
            value = dict(result='passed',reader_cleanup_confirmed=True)
            A.N._write(output/'result.json',value); observed.append(output)
            return value
        with patch.object(A,'STATE',self.base/'state'),patch.object(A.subprocess,'run'), \
             patch.object(A,'execute',side_effect=execute),patch.object(A,'_signals',return_value=CancelOnExit()), \
             patch('sys.stdout',io.StringIO()) as stdout:
            self.assertEqual(A.main(['--request-stdin'],stdin=io.BytesIO(S.canonical(REQUEST))),143)
        owner = json.loads((observed[0]/'owner-result.json').read_bytes())
        self.assertEqual((owner['result'],owner['materialization_result']),('interrupted','passed'))
        self.assertEqual((owner['schema_version'],owner['profile']),(2,A.SELECTED_PROFILE))
        self.assertEqual(owner['request_sha256'],S.digest(REQUEST))
        self.assertEqual(owner['template_sha256'],S.TEMPLATE_SHA256)
        self.assertEqual(json.loads(stdout.getvalue())['result'],'interrupted')


if __name__ == '__main__': unittest.main()
