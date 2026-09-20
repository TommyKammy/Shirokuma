"""The existing Reader owner with filtered v3 frames and fake child processes."""
from copy import deepcopy
import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.notebook import client as N, filtered_selection as FS
from test_local_notebook_client import Child, encoded, reader_frames
from test_query_filter_profile import observed, request

TOKEN='a'*32


def filtered_frames(value=None,rows=None):
    value=value or request()
    _,public=observed(value,rows)
    return [dict(schema_version=3,event='allocated',command='query select',evidence_token=TOKEN),
            dict(schema_version=3,event='result',command='query select',evidence_token=TOKEN,result='passed',
                 observation=public,error_code=None,cleanup_verified=True,
                 evidence=f'tmp/local-experiment/query-filter/{TOKEN}/summary.json')]


class FilteredClientTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.directory=Path(temporary.name).resolve(); self.binary=self.directory/'shirokuma'
        self.binary.write_bytes(b'fake');self.value=request();self.path=self.directory/'request.json'
        self.path.write_bytes(FS.canonical_bytes(self.value));self.path.chmod(0o600)
        env=patch.dict(os.environ,{'SHIROKUMA_NOTEBOOK_RUN_DIR':str(self.directory),
            'SHIROKUMA_NOTEBOOK_BINARY':str(self.binary),FS.REQUEST_PATH_ENV:str(self.path),
            FS.REQUEST_SHA_ENV:FS.digest(self.value)})
        env.start();self.addCleanup(env.stop)

    def spawn(self,child,frames=None):
        def call(argv,**kwargs):
            self.assertEqual(argv,FS.arguments(self.binary,N.ROOT,self.value))
            self.assertTrue(kwargs['start_new_session'])
            intent=json.loads((self.directory/'reader-intent.json').read_bytes())
            self.assertEqual(intent,dict(schema_version=1,command='query select',request=self.value))
            kwargs['stdout'].write(encoded(frames if frames is not None else filtered_frames(self.value)))
            return child
        return call

    def test_request_owned_select_once_and_public_descriptor_only(self):
        with patch.object(N.subprocess,'Popen',side_effect=self.spawn(Child())) as spawn,\
             patch.object(N,'_process_start',return_value='Sun Sep 20 12:00:00 2026'):
            value=N.read_requested_filtered_table()
            with self.assertRaises(FileExistsError): N.read_filtered(self.value)
        self.assertEqual(spawn.call_count,1)
        self.assertEqual(value,filtered_frames()[-1]['observation'])
        self.assertEqual(value['where'],{'column':'id','type':'bigint','operator':'eq'})
        self.assertNotIn('literal',value['where'])
        status=json.loads((self.directory/'reader-status.json').read_bytes())
        self.assertEqual(status['state'],'passed');self.assertTrue(status['cleanup_verified'])
        for name in ('reader-intent.json','reader.stdout.jsonl','reader-status.json'):
            self.assertEqual((self.directory/name).stat().st_mode & 0o777,0o600)

    def test_v3_frame_token_path_exit_and_request_binding_reject_mismatch(self):
        good=filtered_frames()
        bad=[]
        for field,value in [('schema_version',1),('schema_version',True),('evidence_token','b'*32),
                            ('evidence',f'tmp/local-experiment/query-read/{TOKEN}/summary.json')]:
            other=deepcopy(good);other[-1][field]=value;bad.append(other)
        for frames in bad:
            with self.assertRaises(N.NotebookError): N._filtered_reader_frames(encoded(frames),0,self.value)
        with self.assertRaises(N.NotebookError): N._filtered_reader_frames(encoded(good),1,self.value)
        for change in [('table','smoke.other'),('columns',['name','id']),('limit',1),
                       ('where',{'column':'id','literal':'2'})]:
            value=deepcopy(self.value);value[change[0]]=change[1]
            with self.assertRaises(N.NotebookError): N._filtered_reader_frames(encoded(good),0,value)
        bad=deepcopy(good);bad[-1]['observation']['where']['literal']='1'
        with self.assertRaises(N.NotebookError): N._filtered_reader_frames(encoded(bad),0,self.value)
        with self.assertRaises(N.NotebookError): N._reader_frames(encoded(good),0,N.REQUEST)
        N._reader_frames(encoded(reader_frames()),0) # Old v1 default remains accepted.

    def test_empty_result_and_nonprojected_filter_are_valid_without_extra_cells(self):
        frames=filtered_frames(rows=[])
        self.assertEqual(N._filtered_reader_frames(encoded(frames),0,self.value)[1]['rows'],[])
        value=request(columns=['name'])
        frames=filtered_frames(value,rows=[['private-row-sentinel']])
        public=N._filtered_reader_frames(encoded(frames),0,value)[1]
        self.assertEqual([c['name'] for c in public['columns']],['name'])
        self.assertEqual(public['where']['column'],'id')

    def test_duplicate_frame_key_and_cleanup_false_never_release_rows(self):
        raw=encoded(filtered_frames()).replace(b'"schema_version":3',b'"schema_version":3,"schema_version":3',1)
        with self.assertRaises(N.NotebookError): N._filtered_reader_frames(raw,0,self.value)
        frames=filtered_frames();frames[-1]['cleanup_verified']=False
        with self.assertRaises(N.NotebookError): N._filtered_reader_frames(encoded(frames),0,self.value)

    def test_request_hash_and_after_child_tampering_refuse_return(self):
        with patch.dict(os.environ,{FS.REQUEST_SHA_ENV:'0'*64}),patch.object(N,'read_filtered') as child:
            with self.assertRaises(N.NotebookError): N.read_requested_filtered_table()
            child.assert_not_called()
        def changed(value):
            altered=deepcopy(value);altered['where']['literal']='2'
            self.path.write_bytes(FS.canonical_bytes(altered))
            return filtered_frames()[-1]['observation']
        with patch.object(N,'read_filtered',side_effect=changed) as child:
            with self.assertRaises(N.NotebookError): N.read_requested_filtered_table()
        self.assertEqual(child.call_count,1)

    def test_unsupported_literal_refused_before_child_or_intent(self):
        invalid=deepcopy(self.value);invalid['where']['literal']='NULL'
        with patch.object(N.subprocess,'Popen') as child:
            with self.assertRaises(FS.SelectionError): N.read_filtered(invalid)
            child.assert_not_called()
        self.assertFalse((self.directory/'reader-intent.json').exists())

    def test_first_cancel_uses_existing_cleanup_and_is_not_replaced(self):
        original=KeyboardInterrupt();child=Child(initial=original)
        frames=filtered_frames();frames[-1].update(result='interrupted',observation=None,
            error_code='INTERRUPTED',cleanup_verified=True)
        with patch.object(N.subprocess,'Popen',side_effect=self.spawn(child,frames)),\
             patch.object(N,'_process_start',return_value='Sun Sep 20 12:00:00 2026'),\
             patch.object(N.os,'killpg') as interrupt:
            with self.assertRaises(KeyboardInterrupt) as caught: N.read_requested_filtered_table()
        self.assertIs(caught.exception,original);interrupt.assert_called_once_with(child.pid,signal.SIGINT)
        self.assertEqual(child.calls,2)
        status=json.loads((self.directory/'reader-status.json').read_bytes())
        self.assertEqual(status['state'],'interrupted');self.assertTrue(status['cleanup_verified'])

    def test_failed_typed_refusal_preserves_cleanup_without_observation(self):
        frames=filtered_frames();frames[-1].update(result='failed',observation=None,
            error_code='FILTER_TYPE_UNSUPPORTED',cleanup_verified=True)
        self.assertEqual(N._filtered_reader_frames(encoded(frames),1,self.value),(TOKEN,None,True))
        frames[-1]['observation']=filtered_frames()[-1]['observation']
        with self.assertRaises(N.NotebookError): N._filtered_reader_frames(encoded(frames),1,self.value)


if __name__=='__main__': unittest.main()
