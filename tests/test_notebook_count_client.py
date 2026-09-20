"""COUNT uses the shared Reader owner; all child processes here are fakes."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.notebook import client as N
from tests.test_local_query_count import request, public
from tests.test_local_notebook_client import Child, encoded

TOKEN = 'a' * 32


def frames(value=None, number=2, *, result='passed', cleanup=True):
    value = value or request()
    return [dict(schema_version=4,event='allocated',command='query count',evidence_token=TOKEN),
            dict(schema_version=4,event='result',command='query count',evidence_token=TOKEN,result=result,
                 observation=public(value,number) if result=='passed' else None,
                 error_code=None if result=='passed' else 'INTERRUPTED' if result=='interrupted' else 'COUNT_RESULT_INVALID',
                 cleanup_verified=cleanup,evidence=f'tmp/local-experiment/query-count/{TOKEN}/summary.json')]


class CountClientTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.output=Path(temp.name).resolve();self.output.chmod(0o700)
        self.binary=self.output/'shirokuma';self.binary.write_bytes(b'fake')
        env=patch.dict(os.environ,{'SHIROKUMA_NOTEBOOK_RUN_DIR':str(self.output),
                                   'SHIROKUMA_NOTEBOOK_BINARY':str(self.binary)})
        env.start();self.addCleanup(env.stop)

    def launch(self, child, saved):
        def fake(argv, **kwargs):
            self.assertTrue(kwargs['start_new_session'])
            self.assertEqual(json.loads((self.output/'reader-intent.json').read_bytes())['command'],'query count')
            kwargs['stdout'].write(encoded(saved))
            return child
        return fake

    def test_owned_current_count_once_and_exact_private_request(self):
        value=request(column='id',literal='1');child=Child()
        with patch.object(N.subprocess,'Popen',side_effect=self.launch(child,frames(value))) as launch, \
                patch.object(N,'_process_start',return_value='synthetic child start'):
            got=N.read_count(value)
            with self.assertRaises(FileExistsError):N.read_count(value)
        self.assertEqual(launch.call_count,1);self.assertEqual(got,public(value))
        self.assertEqual(launch.call_args.args[0],N._count_arguments(self.binary,value))
        self.assertEqual(launch.call_args.args[0][-4:],['--where-column','id','--equals','1'])
        self.assertNotIn('literal',got['where'])
        status=json.loads((self.output/'reader-status.json').read_bytes())
        self.assertEqual(status['state'],'passed');self.assertTrue(status['child_exited'] and status['cleanup_verified'])

    def test_zero_and_max_int64_remain_strings_without_preview_rows(self):
        for number in (0,2**63-1):
            _,got,cleanup=N._count_reader_frames(encoded(frames(number=number)),0,request())
            self.assertEqual(got['count'],str(number));self.assertTrue(cleanup)
            self.assertNotIn('rows',got)

    def test_wrong_envelope_table_predicate_or_count_is_rejected(self):
        for key,value in [('schema_version',1),('command','query select'),('evidence','tmp/not-ours'),
                          ('count',True),('count',1.0),('count','01'),('table','iceberg.smoke.other')]:
            changed=frames()
            target=changed[-1] if key in ('schema_version','command','evidence') else changed[-1]['observation']
            target[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(N.NotebookError):
                N._count_reader_frames(encoded(changed),0,request())
        with self.assertRaises(N.NotebookError):
            N._count_reader_frames(encoded(frames()),0,request(column='id',literal='1'))

    def test_historical_and_bad_requests_fail_before_intent_or_child(self):
        for value in (request(snapshot_id='1'),{**request(),'where':{}},{**request(),'sql':'SELECT 1'}):
            with patch.object(N.subprocess,'Popen') as launch,self.assertRaises((N.NotebookError,N.QC.ContractError)):
                N.read_count(value)
            launch.assert_not_called();self.assertFalse((self.output/'reader-intent.json').exists())

    def test_terminal_failure_keeps_cleanup_without_fabricating_zero(self):
        token,result,cleanup=N._count_reader_frames(encoded(frames(result='failed')),1,request())
        self.assertEqual(token,TOKEN);self.assertIsNone(result);self.assertTrue(cleanup)
        self.assertFalse(N._count_reader_frames(encoded(frames(result='failed',cleanup=False)),1,request())[2])

    def test_original_interrupt_survives_secondary_signal_failure(self):
        original=KeyboardInterrupt(); child=Child(initial=original)
        with patch.object(N.subprocess,'Popen',side_effect=self.launch(child,frames(result='interrupted'))), \
                patch.object(N,'_process_start',return_value='synthetic child start'), \
                patch.object(N.os,'killpg',side_effect=OSError('secondary')),self.assertRaises(KeyboardInterrupt) as got:
            N.read_count(request())
        self.assertIs(got.exception,original)
        status=json.loads((self.output/'reader-status.json').read_bytes())
        self.assertEqual(status['state'],'interrupted');self.assertTrue(status['cleanup_verified'])


if __name__=='__main__':unittest.main()
