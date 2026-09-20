"""Fixed Catalog Reader adapter; fake children only, without API or SQL calls."""
from copy import deepcopy
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.notebook import client as N
from tools.local_experiment.catalog_read import contract as C, run as R
from tests.test_catalog_read_contract import public, documents, CANARY
from tests.test_local_notebook_client import Child, encoded

TOKEN = 'f' * 32
TABLE = 'smoke.fixture_v1'


def frames(value=None, *, result='passed', cleanup=True, error='CATALOG_CHANGED'):
    return [dict(schema_version=1,event='allocated',command='catalog describe',evidence_token=TOKEN),
            dict(schema_version=1,event='result',command='catalog describe',evidence_token=TOKEN,
                 result=result,observation=public() if value is None and result == 'passed' else value,
                 error_code=None if result == 'passed' else 'INTERRUPTED' if result == 'interrupted' else error,
                 cleanup_verified=cleanup,evidence=f'tmp/local-experiment/catalog-read/{TOKEN}/summary.json')]


class CatalogClientTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.output=Path(temporary.name).resolve(); self.output.chmod(0o700)
        self.binary=self.output/'shirokuma'; self.binary.write_bytes(b'fake')
        env=patch.dict(os.environ,{'SHIROKUMA_NOTEBOOK_RUN_DIR':str(self.output),
                                  'SHIROKUMA_NOTEBOOK_BINARY':str(self.binary)})
        env.start(); self.addCleanup(env.stop)

    def launch(self,child,saved):
        def fake(argv,**kwargs):
            self.assertTrue(kwargs['start_new_session'])
            intent=json.loads((self.output/'reader-intent.json').read_bytes())
            self.assertEqual(intent,dict(schema_version=1,command='catalog describe',request=N._catalog_request(TABLE)))
            kwargs['stdout'].write(encoded(saved)); return child
        return fake

    def test_fixed_catalog_once_exact_command_and_private_saved_owner(self):
        child=Child(); before=dict(os.environ)
        with patch.dict(os.environ,{'HTTP_PROXY':'private-proxy','DOCKER_HOST':'foreign-socket'}), \
                patch.object(N.subprocess,'Popen',side_effect=self.launch(child,frames())) as launch, \
                patch.object(N,'_process_start',return_value='synthetic start'):
            got=N.read_catalog(TABLE)
            with self.assertRaises(FileExistsError): N.read_catalog(TABLE)
        self.assertEqual(dict(os.environ),before); self.assertEqual(launch.call_count,1)
        self.assertEqual(launch.call_args.args[0],[str(self.binary),'--repo-root',str(N.ROOT),'--profile',
                         'local-experiment','catalog','describe','--table',TABLE])
        self.assertNotIn('HTTP_PROXY',launch.call_args.kwargs['env'])
        self.assertNotIn('DOCKER_HOST',launch.call_args.kwargs['env'])
        self.assertEqual(got,public())
        for name in ('reader-intent.json','reader-status.json','reader.stdout.jsonl'):
            self.assertEqual(stat.S_IMODE((self.output/name).stat().st_mode),0o600)
            self.assertNotIn(CANARY,(self.output/name).read_text())
        status=json.loads((self.output/'reader-status.json').read_bytes())
        self.assertEqual(status['state'],'passed'); self.assertEqual(status['evidence_token'],TOKEN)
        self.assertTrue(status['cleanup_verified'] and status['child_exited'])

    def test_unsupported_table_is_rejected_before_private_allocation(self):
        with patch.object(N.subprocess,'Popen') as launch:
            for table in (None,True,1,'','iceberg.'+TABLE,TABLE+'\n','../fixture_v1',
                          'local_experiment.dataops_'+'a'*32+'_stg'):
                with self.subTest(table=table), self.assertRaises(N.NotebookError): N.read_catalog(table)
        launch.assert_not_called(); self.assertFalse((self.output/'reader-intent.json').exists())

    def test_public_retains_missing_empty_comments_and_observed_empty_lineage(self):
        for description in (None,''):
            raw=documents(empty=True); raw['table']['description']=description
            raw['table']['columns'][0]['description']=''
            value=public(raw)
            self.assertEqual(N._catalog_public(value,TABLE),value)
            token,got,cleanup=N._catalog_reader_frames(encoded(frames(value)),0,TABLE)
            self.assertEqual((token,cleanup),(TOKEN,True)); self.assertIs(got['description'],description)
            self.assertEqual(got['columns'][0]['comment'],'')
            self.assertIsNone(got['columns'][1]['comment'])
            self.assertEqual(got['lineage']['nodes'],[]); self.assertEqual(got['lineage']['edges'],[])
            self.assertEqual(got['sql_presence'],'not-checked')

    def test_public_source_types_unknown_presence_and_raw_fields_remain_strict(self):
        for source in ('Manual','OpenLineage',None):
            value=public(); value['lineage']['edges'][0]['source']=source
            self.assertEqual(N._catalog_public(value,TABLE)['lineage']['edges'][0]['source'],source)
        mutations=[lambda x:x.update(table='iceberg.smoke.other'),lambda x:x.update(schema_version=True),
                   lambda x:x.update(sql_presence='present'),lambda x:x.update(href=CANARY),
                   lambda x:x.update(description='x'*(C.MAX_DESCRIPTION_BYTES+1)),
                   lambda x:x['lineage'].update(scope='all-catalog'),
                   lambda x:x['lineage'].update(nodes=None),lambda x:x['lineage'].update(edges=None),
                   lambda x:x['lineage']['nodes'][0].update(sql_presence='present'),
                   lambda x:x['lineage']['edges'][0].update(source='generated'),
                   lambda x:x['catalog_entity'].update(connection=CANARY),
                   lambda x:x['columns'][0].update(type='FLOAT')]
        for change in mutations:
            value=public(); change(value)
            with self.assertRaisesRegex(N.NotebookError,'NOTEBOOK_READER_INVALID'):
                N._catalog_public(value,TABLE)

    def test_maximum_nodes_and_edges_and_public_bytes_use_existing_contract(self):
        self.assertEqual((C.MAX_NODES,C.MAX_EDGES,C.MAX_PUBLIC_BYTES),(64,64,64*1024))
        for field in ('nodes','edges'):
            value=public(); value['lineage'][field]=value['lineage'][field]*65
            with self.assertRaises(N.NotebookError): N._catalog_public(value,TABLE)
        self.assertEqual(N.CATALOG_MAX_FRAME,R.MAX_FRAME)

    def test_frames_bind_catalog_command_token_schema_root_exit_and_cleanup(self):
        changes=[(0,'schema_version',6),(1,'schema_version',True),(1,'command','query describe'),
                 (1,'evidence_token','a'*32),(1,'evidence',f'tmp/local-experiment/query-read/{TOKEN}/summary.json'),
                 (1,'cleanup_verified',False),(1,'error_code','CATALOG_CHANGED'),(1,'raw',CANARY)]
        for index,key,value in changes:
            changed=frames(); changed[index][key]=value
            with self.assertRaises(N.NotebookError): N._catalog_reader_frames(encoded(changed),0,TABLE)
        raw=encoded(frames())
        for changed in (raw.rstrip(b'\n'),raw+b'{}\n',b' ' * N.CATALOG_MAX_FRAME+raw,
                        raw.replace(b'"schema_version":1',b'"schema_version":1,"schema_version":1',1)):
            with self.assertRaises(N.NotebookError): N._catalog_reader_frames(changed,0,TABLE)
        with self.assertRaises(N.NotebookError): N._catalog_reader_frames(raw,1,TABLE)

    def test_fixed_failure_enum_matches_owner_and_unknown_is_not_empty(self):
        self.assertEqual(N.CATALOG_ERROR_CODES,R.PUBLIC_CODES)
        for error in R.PUBLIC_CODES:
            interrupted=error=='INTERRUPTED'
            saved=frames(result='interrupted' if interrupted else 'failed',cleanup=False,error=error)
            self.assertEqual(N._catalog_reader_frames(encoded(saved),130 if interrupted else 1,TABLE),
                             (TOKEN,None,False))
        with self.assertRaises(N.NotebookError):
            N._catalog_reader_frames(encoded(frames(result='failed',error='PRIVATE_MESSAGE')),1,TABLE)

    def test_failed_catalog_with_verified_cleanup_does_not_publish_public(self):
        child=Child()
        def finished(timeout):child.returncode=1; return 1
        child.wait=finished
        with patch.object(N.subprocess,'Popen',side_effect=self.launch(child,frames(result='failed'))) as launch, \
                patch.object(N,'_process_start',return_value='synthetic start'):
            with self.assertRaisesRegex(N.NotebookError,'NOTEBOOK_READER_FAILED'): N.read_catalog(TABLE)
        self.assertEqual(launch.call_count,1)
        status=json.loads((self.output/'reader-status.json').read_bytes())
        self.assertEqual(status['state'],'failed'); self.assertTrue(status['cleanup_verified'])

    def test_first_cancel_survives_signal_and_terminal_save_failures(self):
        original=KeyboardInterrupt('first'); child=Child(initial=original); write=N._write
        def save(path,value,**kwargs):
            if path.name=='reader-status.json' and value['state']=='interrupted':raise OSError('secondary save')
            return write(path,value,**kwargs)
        with patch.object(N.subprocess,'Popen',side_effect=self.launch(child,frames(result='interrupted'))), \
                patch.object(N,'_process_start',return_value='synthetic start'), \
                patch.object(N.os,'killpg',side_effect=OSError('secondary signal')) as signal, \
                patch.object(N,'_write',side_effect=save):
            with self.assertRaises(KeyboardInterrupt) as caught:N.read_catalog(TABLE)
        self.assertIs(caught.exception,original); self.assertEqual(signal.call_count,1)
        self.assertEqual(child.returncode,130)

    def test_success_is_returned_only_after_owned_child_exit(self):
        child=Child(); wait=child.wait
        def finished(timeout):
            status=json.loads((self.output/'reader-status.json').read_bytes())
            self.assertEqual(status['state'],'running')
            self.assertFalse(status['child_exited'] or status['cleanup_verified'])
            return wait(timeout)
        child.wait=finished
        with patch.object(N.subprocess,'Popen',side_effect=self.launch(child,frames())), \
                patch.object(N,'_process_start',return_value='synthetic start'):
            self.assertEqual(N.read_catalog(TABLE),public())


if __name__=='__main__':unittest.main()
