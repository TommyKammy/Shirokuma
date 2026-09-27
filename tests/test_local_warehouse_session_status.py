import json
import io
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.warehouse_lifecycle import session_rpc as RPC, status as S

TOKEN='a'*32
ID='11111111-1111-4111-8111-111111111111'


def request(action='query'):
    return dict(schema_version=1,session_id=TOKEN,request_id=ID,warehouse='XS',action=action)


def entry(action='query',state='pending'):
    q=request(action)
    response=({**q,'result':'verified','before':RPC.TARGETS['chill'],
               'after':RPC.TARGETS['wake'],'operation_id':ID,'receipt_sha256':'b'*64,
               'query':dict(case='xs_caller',query_id='20260917_152707_00001_gps5i',rows_observed=25,
                            rows_sha256='c'*64,post_count=1,response_complete=True,matched=True)}
              if action=='query' else {**q,'result':'verified','before':RPC.TARGETS['wake'],
                                         'after':RPC.TARGETS['chill'],'operation_id':ID,
                                         'receipt_sha256':'b'*64})
    return dict(request=q,state=state,response=response if state=='completed' else None,error_code=None)


def raw(items=None,replies=None):
    return json.dumps(dict(schema_version=1,session_id=TOKEN,requests=items or [],
                           replies=replies or [],error_codes=[]),separators=(',',':')).encode()+b'\n'


class SessionStatusTests(unittest.TestCase):
    def test_recorded_verified_is_never_live_or_replayed(self):
        e=entry(state='completed');source=raw([e],[e['response']])
        result=S.project(source,TOKEN)
        self.assertEqual(result['recorded_actions'],[{'action':'query','recorded_result':'verified'}])
        self.assertEqual((result['observation'],result['runtime_state']),('saved-journal-only','unknown'))
        self.assertNotIn('rows_sha256',result)
        self.assertEqual(S.project(raw([entry()]),TOKEN)['recorded_actions'][0]['recorded_result'],'pending')

    def test_bad_shape_duplicate_or_forged_completion_is_refused(self):
        e=entry(state='completed')
        cases=[raw([e,e]),raw([dict(e,state='pending')]),
               raw([dict(e,response=dict(e['response'],query=None))]),
               raw([e],[dict(e['response'],request_id='22222222-2222-4222-8222-222222222222')]),
               raw([dict(e,error_code='FAILED')]),
               raw([entry()],[e['response']]),
               raw([e],[dict(e['response'],receipt_sha256='d'*64)]),
               raw([e]).replace(b'"schema_version":1',b'"schema_version":1,"schema_version":1',1),
               raw([e]).replace(b'"journal_sha256"',b'"invalid"')+b' ' * S.MAX_JOURNAL_BYTES]
        for source in cases:
            with self.subTest(source=source[:120]),self.assertRaises(S.StatusError):S.project(source,TOKEN)
        with self.assertRaises(S.StatusError):S.project(raw([e]),'../outside')

    def test_non_success_replies_must_be_original_or_uncertain_without_evidence(self):
        e=entry(state='completed')
        interim=RPC._uncertain(e['request'])
        collision=RPC._uncertain({**e['request'],'action':'wake'})
        valid=S.project(raw([e],[interim,collision,e['response']]),TOKEN,ID)
        self.assertEqual(valid['request_lookup']['recorded_result'],'verified')
        self.assertEqual(valid['reply_count'],3)
        for bad in (
            {**interim,'operation_id':ID,'receipt_sha256':'b'*64},
            {**collision,'operation_id':ID,'receipt_sha256':'b'*64},
            {**interim,'result':'busy'},
            {**e['response'],'result':'failed'},
        ):
            with self.subTest(bad=bad),self.assertRaises(S.StatusError):
                S.project(raw([e],[bad]),TOKEN,ID)
        # The stored busy response itself is replayable; not every negative
        # response is a fabricated interim reply.
        busy=deepcopy(e)
        busy['response']=RPC._uncertain(e['request'],'busy')
        self.assertEqual(S.project(raw([busy],[busy['response']]),TOKEN)['reply_count'],1)

    def test_private_filesystem_rejects_redirects_and_mutation_without_output(self):
        with tempfile.TemporaryDirectory(dir=S.ROOT/'tmp/local-experiment') as temp:
            root=Path(temp)/'runs';root.mkdir(mode=0o700)
            run=root/TOKEN;run.mkdir(mode=0o700)
            journal=run/'rpc';journal.mkdir(mode=0o700)
            file=journal/'journal.json';file.write_bytes(raw([entry(state='completed')]))
            file.chmod(0o600)
            with patch.object(RPC,'SessionRPC',side_effect=AssertionError('no owner')):
                result=S.read(TOKEN,root)
            self.assertEqual(result['runtime_state'],'unknown')
            file.chmod(0o644)
            with self.assertRaises(S.StatusError):S.read(TOKEN,root)
            file.chmod(0o600);file.rename(journal/'old.json')
            file.symlink_to(journal/'old.json')
            with self.assertRaises(S.StatusError):S.read(TOKEN,root)
            file.unlink()
            (journal/'old.json').rename(file)
            other=Path(temp)/'outside';other.write_bytes(b'x')
            file.unlink();file.hardlink_to(other)
            with self.assertRaises(S.StatusError):S.read(TOKEN,root)
            with patch.object(S,'RUNS',root),patch('sys.stdout',new_callable=io.StringIO) as output,\
                 patch('sys.stderr',new_callable=io.StringIO) as errors:
                self.assertEqual(S.main([TOKEN]),1)
            self.assertEqual(output.getvalue(),'')
            self.assertEqual(errors.getvalue().strip(),'JOURNAL_UNVERIFIED')
            with self.assertRaises(S.StatusError):S.read('b'*32,root)
            with patch.dict(os.environ,{'WAREHOUSE_LIFECYCLE_RUN_TOKEN':TOKEN}),\
                 patch.object(S,'RUNS',root),patch('sys.stdout',new_callable=io.StringIO) as output,\
                 patch('sys.stderr',new_callable=io.StringIO) as errors:
                self.assertEqual(S.main([]),1)
            self.assertEqual(output.getvalue(),'')
            self.assertEqual(errors.getvalue().strip(),'JOURNAL_UNVERIFIED')


if __name__=='__main__':unittest.main()
