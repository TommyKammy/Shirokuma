"""Filtered assets with private fake Reader receipts and local Dagster only."""
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
from tools.local_experiment.notebook import filtered_selection as FS, selection as S
from tools.local_experiment.query_filter import contract as F
from tests.test_notebook_dagster_assets import Client, FakeAPI, Instance
from tests.test_notebook_assets_selection import materialize
from tests.test_local_notebook_client import reader_frames, encoded
from tests.test_query_read_contract import metadata, schema, result

REQUEST = dict(command='select',table='smoke.fixture_v1',columns=['name'],limit=1,
               snapshot_id=None,where=dict(column='id',literal='9007199254740993'))


def frames(request=REQUEST):
    value=metadata()
    value['metadata']['snapshots'][0].update(**{'sequence-number':1,'timestamp-ms':1700000000000,
        'schema-id':0,'summary':{'operation':'append'}})
    window=F.project_window(value,request,schema())
    public=F.project_rows(result(F.base_request(request),[['private-first'],['private-sentinel']]),request,schema(),window)
    values=reader_frames()
    for frame in values:frame['schema_version']=3
    values[1].update(observation=public,evidence=values[1]['evidence'].replace('query-read/','query-filter/'))
    return values


class FilteredClient(Client):
    def __init__(self,output):
        super().__init__(output);self.requests=[];self.environment=None;self.mutate=None
    def read_filtered(self,request):
        self.calls.append('read_filtered');self.requests.append(deepcopy(request))
        self.environment={key:os.environ.get(key) for key in A.ENV_KEYS}
        values=frames(request)
        A.N._write(self.output/'reader-intent.json',dict(schema_version=1,command='query select',request=request))
        if self.error is not None:values[1].update(result='interrupted',observation=None,error_code='INTERRUPTED')
        with (self.output/'reader.stdout.jsonl').open('xb') as stream:
            stream.write(encoded(values));os.fchmod(stream.fileno(),0o600)
        A.N._write(self.output/'reader-status.json',dict(schema_version=1,
            state='interrupted' if self.error is not None else 'passed',pid=1234,pgid=1234,
            process_start='synthetic-start',evidence_token=values[0]['evidence_token'],
            exit_code=130 if self.error is not None else 0,child_exited=not self.unknown_cleanup,
            cleanup_verified=not self.unknown_cleanup,error_code='NOTEBOOK_INTERRUPTED' if self.error is not None else None))
        if self.mutate is not None:self.mutate()
        if self.error is not None:
            self.calls.append('reader-cleanup-awaited');raise self.error
        return values[1]['observation']


class FilteredAssetsTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.base=Path(temporary.name).resolve();self.output=self.base/'run'
        self.binary=self.base/'shirokuma';self.binary.write_bytes(b'not executed')
        self.client=FilteredClient(self.output);self.instance=Instance()
    def export(self):return A.export(self.output,request=REQUEST,filtered=True)
    def execute(self,**kwargs):
        return A.execute(self.output,self.binary,request=REQUEST,filtered=True,materializer=materialize,
            instance_factory=lambda *a,**kw:self.instance,api=FakeAPI,client=self.client,**kwargs)
    def saved(self):return json.loads((self.output/'result.json').read_bytes())

    def test_export_schema3_private_literal_and_fixed_generated_mode(self):
        manifest=self.export()
        self.assertEqual((manifest['schema_version'],manifest['profile']),(3,A.FILTERED_PROFILE))
        self.assertEqual(A.verify_export(self.output,request=REQUEST,filtered=True),manifest)
        self.assertEqual(manifest['selection_sha256'],A.digest(Path(FS.__file__).read_bytes()))
        self.assertEqual(manifest['base_selection_sha256'],A.digest(Path(S.__file__).read_bytes()))
        self.assertEqual(FS.load_request(self.output/'request.json',FS.digest(REQUEST)),REQUEST)
        generated=(self.output/'defs.py').read_text()
        self.assertIn('definitions_from_export(__file__, "'+FS.digest(REQUEST)+'", filtered=True)',generated)
        self.assertNotIn(REQUEST['where']['literal'],generated)
        self.assertNotIn(REQUEST['table'],generated)
        self.assertNotIn(REQUEST['where']['literal'],json.dumps(manifest))
        self.assertEqual([item['key'] for item in manifest['assets']],list(A.FILTERED_ASSETS))
        for name in ('defs.py','manifest.json','request.json'):
            self.assertEqual((self.output/name).stat().st_mode&0o777,0o600)

    def test_explicit_filtered_mode_and_current_head_are_required_before_allocation(self):
        bad=[dict(REQUEST,snapshot_id='8088807033386546123'),dict(REQUEST,where={'column':'id','literal':'NULL'}),
             dict(REQUEST,limit=True),dict(REQUEST,where={'column':'id','literal':'1','type':'bigint'})]
        for request in bad:
            with self.subTest(request=request),self.assertRaises(FS.SelectionError):
                A.export(self.output,request=request,filtered=True)
            self.assertFalse(self.output.exists())
        for kwargs in ({'filtered':True},{'filtered':1,'request':REQUEST},{'request':REQUEST}):
            with self.subTest(kwargs=kwargs),self.assertRaises((A.AssetError,S.SelectionError)):
                A.export(self.output,**kwargs)
            self.assertFalse(self.output.exists())

    def test_success_binds_filtered_frames_and_metadata_without_literal_or_rows(self):
        self.export();inherited={key:'inherited-'+str(i) for i,key in enumerate(A.ENV_KEYS)}
        with patch.dict(os.environ,inherited):
            value=self.execute();self.assertEqual({key:os.environ[key] for key in inherited},inherited)
        self.assertEqual(self.client.environment,dict(zip(A.ENV_KEYS,
            [str(self.output),str(self.binary),str(self.output/'request.json'),FS.digest(REQUEST)])))
        self.assertEqual(self.client.calls,['read_filtered','read_evaluations']);self.assertEqual(self.client.requests,[REQUEST])
        self.assertTrue(self.instance.closed);self.assertEqual(value['result'],'passed')
        self.assertEqual((value['schema_version'],value['profile']),(3,A.FILTERED_PROFILE))
        self.assertEqual([item['key'] for item in value['assets']],list(A.FILTERED_ASSETS))
        first=value['assets'][0]['metadata'];self.assertEqual(first['where'],dict(column='id',type='bigint',operator='eq'))
        self.assertEqual(first['row_count'],1);self.assertTrue(A.reader_closed(self.output,request=REQUEST,filtered=True))
        self.assertFalse(A.reader_closed(self.output))
        for receipt in (value,json.loads((self.output/'execution-intent.json').read_bytes())):
            self.assertEqual(receipt['request_sha256'],FS.digest(REQUEST));self.assertEqual(receipt['template_sha256'],FS.TEMPLATE_SHA256)
            raw=json.dumps(receipt)
            for forbidden in ('private-first','private-sentinel',REQUEST['where']['literal'],'"literal"','"rows"'):
                self.assertNotIn(forbidden,raw)

    def test_request_bytes_manifest_and_generated_mode_tamper_refuse_reads(self):
        self.export();original={p.name:p.read_bytes() for p in self.output.iterdir()}
        for name in ('request.json','manifest.json','defs.py'):
            with self.subTest(name=name):
                for key,raw in original.items():(self.output/key).write_bytes(raw)
                if name=='request.json':
                    (self.output/name).write_bytes(FS.canonical(dict(REQUEST,where=dict(column='id',literal='1'))))
                elif name=='manifest.json':
                    value=json.loads(original[name]);value['profile']=A.SELECTED_PROFILE
                    (self.output/name).write_bytes(A.N._canonical(value))
                else:(self.output/name).write_bytes(original[name].replace(b', filtered=True',b''))
                with self.assertRaises((A.AssetError,FS.SelectionError)):self.execute()
                self.assertEqual(self.client.calls,[])

    def test_cross_request_intent_and_cross_profile_frame_cannot_confirm_cleanup(self):
        self.export();self.execute()
        intent=self.output/'reader-intent.json';original=intent.read_bytes()
        changed=dict(schema_version=1,command='query select',request=dict(REQUEST,where=dict(column='id',literal='1')))
        intent.write_bytes(A.N._canonical(changed))
        self.assertFalse(A.reader_closed(self.output,request=REQUEST,filtered=True))
        intent.write_bytes(original)
        (self.output/'reader.stdout.jsonl').write_bytes(encoded(reader_frames()))
        self.assertFalse(A.reader_closed(self.output,request=REQUEST,filtered=True))

    def test_post_read_tamper_refuses_success_after_cleanup_without_retry(self):
        self.export();self.client.mutate=lambda:(self.output/'request.json').write_bytes(b'changed')
        with self.assertRaises(A.AssetError):self.execute()
        self.assertEqual(self.saved()['result'],'failed');self.assertTrue(self.saved()['reader_cleanup_confirmed'])
        self.assertEqual(self.client.calls,['read_filtered','read_evaluations'])

    def test_first_cancel_survives_close_and_export_failures_with_environment_restored(self):
        self.export();original=A.AssetsInterrupted(signal.SIGTERM);self.client.error=original
        self.client.mutate=lambda:(self.output/'request.json').write_bytes(b'changed')
        self.instance=Instance(on_exit=OSError('secondary close'))
        inherited={key:'preserved' for key in A.ENV_KEYS}
        with patch.dict(os.environ,inherited):
            with self.assertRaises(A.AssetsInterrupted) as caught:self.execute()
            self.assertEqual({key:os.environ[key] for key in inherited},inherited)
        self.assertIs(caught.exception,original);self.assertEqual(self.saved()['result'],'interrupted')
        self.assertTrue(self.saved()['reader_cleanup_confirmed'])
        self.assertEqual(self.client.calls,['read_filtered','reader-cleanup-awaited'])

    def test_environment_restore_failure_keeps_first_cancel_and_restores_remaining_keys(self):
        self.export();original=A.AssetsInterrupted(signal.SIGTERM);self.client.error=original
        inherited={key:'preserved-'+str(i) for i,key in enumerate(A.ENV_KEYS)}
        original_set=os.environ.__class__.__setitem__;raised=[]
        def setitem(environment,key,value):
            original_set(environment,key,value)
            if key==A.ENV_KEYS[0] and value==inherited[key] and not raised:
                raised.append(True);raise OSError('secondary restore')
        with patch.dict(os.environ,inherited):
            with patch.object(os.environ.__class__,'__setitem__',setitem):
                with self.assertRaises(A.AssetsInterrupted) as caught:self.execute()
            self.assertEqual({key:os.environ[key] for key in inherited},inherited)
        self.assertIs(caught.exception,original);self.assertEqual(raised,[True])
        self.assertEqual(self.saved()['result'],'interrupted')

    def test_unknown_cleanup_is_retained_and_evaluations_are_not_started(self):
        self.export();original=KeyboardInterrupt();self.client.error=original;self.client.unknown_cleanup=True
        with self.assertRaises(KeyboardInterrupt) as caught:self.execute()
        self.assertIs(caught.exception,original);self.assertEqual(self.saved()['result'],'unknown')
        self.assertFalse(self.saved()['reader_cleanup_confirmed']);self.assertEqual(self.client.calls,['read_filtered','reader-cleanup-awaited'])

    def test_order_retry_and_unfiltered_observation_are_refused(self):
        self.export();assets=A.build_assets(A._state(),FakeAPI,self.client,request=REQUEST,filtered=True)
        with self.assertRaisesRegex(A.AssetError,'ORDER_INVALID'):assets[1]()
        assets[0]()
        with self.assertRaisesRegex(A.AssetError,'RETRY_REFUSED'):assets[0]()
        client=SimpleNamespace(read_filtered=lambda request:reader_frames()[1]['observation'])
        with self.assertRaises(F.ContractError):
            A.build_assets(A._state(),FakeAPI,client,request=REQUEST,filtered=True)[0]()

    def test_generated_helper_only_constructs_and_rejects_other_mode(self):
        import dagster
        self.export();before={p.name:p.read_bytes() for p in self.output.iterdir()}
        with patch.object(A.N,'read_filtered',side_effect=AssertionError('no Reader')), \
             patch.object(A.N,'read_evaluations',side_effect=AssertionError('no API')):
            definitions=A.definitions_from_export(str(self.output/'defs.py'),FS.digest(REQUEST),filtered=True)
            dagster.Definitions.validate_loadable(definitions)
        self.assertEqual({tuple(key.path) for item in definitions.assets for key in item.keys},{(key,) for key in A.FILTERED_ASSETS})
        self.assertEqual({p.name:p.read_bytes() for p in self.output.iterdir()},before)
        with self.assertRaises(S.SelectionError):A.definitions_from_export(str(self.output/'defs.py'),FS.digest(REQUEST))

    def test_real_dagster_materialization_uses_fake_filtered_reader_only(self):
        import dagster
        self.export();captured=[]
        @contextmanager
        def factory(path,overrides):
            with dagster.DagsterInstance.local_temp(path,overrides=overrides) as instance:
                yield instance
                run=instance.get_runs()[0]
                captured.append((run.status.value,[tuple(row.event_log_entry.dagster_event.asset_key.path)
                    for row in instance.get_records_for_run(run.run_id,of_type=dagster.DagsterEventType.ASSET_MATERIALIZATION).records]))
        with patch.object(A.N,'read_filtered',side_effect=AssertionError('no Reader')), \
             patch.object(A.N,'read_evaluations',side_effect=AssertionError('no API')):
            value=A.execute(self.output,self.binary,request=REQUEST,filtered=True,instance_factory=factory,client=self.client)
        self.assertEqual(value['result'],'passed')
        self.assertEqual(captured,[('SUCCESS',[(name,) for name in A.FILTERED_ASSETS])])

    def test_cli_invalid_input_fails_before_allocation(self):
        for raw in (b'{}',b'x'*4097,FS.canonical(dict(REQUEST,limit=1)).replace(b'"limit":1',b'"limit":true')):
            with self.subTest(raw=raw[:20]),patch.object(A,'STATE',self.base/'state'), \
                 patch.object(A,'export') as export,patch.object(A.subprocess,'run') as build,patch('sys.stdout',io.StringIO()) as stdout:
                self.assertEqual(A.main(['--filtered-request-stdin'],stdin=io.BytesIO(raw)),1)
                self.assertEqual(json.loads(stdout.getvalue())['error_code'],'NOTEBOOK_REQUEST_INVALID')
                export.assert_not_called();build.assert_not_called();self.assertFalse((self.base/'state').exists())

    def test_cli_schema3_owner_preserves_late_cancellation(self):
        original=A.AssetsInterrupted(signal.SIGTERM)
        class CancelOnExit:
            def __enter__(self):return self
            def __exit__(self,*args):raise original
        observed=[]
        def execute(output,binary,*,request,filtered):
            self.assertEqual(request,REQUEST);self.assertIs(filtered,True)
            value=dict(result='passed',reader_cleanup_confirmed=True)
            A.N._write(output/'result.json',value);observed.append(output);return value
        with patch.object(A,'STATE',self.base/'state'),patch.object(A.subprocess,'run'), \
             patch.object(A,'execute',side_effect=execute),patch.object(A,'_signals',return_value=CancelOnExit()), \
             patch('sys.stdout',io.StringIO()) as stdout:
            self.assertEqual(A.main(['--filtered-request-stdin'],stdin=io.BytesIO(FS.canonical(REQUEST))),143)
        owner=json.loads((observed[0]/'owner-result.json').read_bytes())
        self.assertEqual((owner['schema_version'],owner['profile']),(3,A.FILTERED_PROFILE))
        self.assertEqual((owner['result'],owner['materialization_result']),('interrupted','passed'))
        self.assertEqual(owner['request_sha256'],FS.digest(REQUEST));self.assertNotIn('literal',json.dumps(owner))
        self.assertEqual(json.loads(stdout.getvalue())['result'],'interrupted')


if __name__=='__main__':unittest.main()
