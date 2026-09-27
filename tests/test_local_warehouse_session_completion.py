from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.warehouse_lifecycle import session_rpc as RPC, status as S, status_completion as C

TOKEN='a'*32
REQUEST_ID='11111111-1111-4111-8111-111111111111'
OPERATION_ID='22222222-2222-4222-8222-222222222222'


def fixture():
    request=dict(schema_version=1,session_id=TOKEN,request_id=REQUEST_ID,warehouse='XS',action='wake')
    operation={**request,'operation_id':OPERATION_ID,'result':'verified',
               'before':RPC.TARGETS['chill'],'after':RPC.TARGETS['wake'],
               'duration_ms':5,'failure_code':None,'observed_at':'2026-09-17T00:00:00Z'}
    raw_operation=(json.dumps(operation,sort_keys=True,indent=2)+'\n').encode()
    digest=hashlib.sha256(raw_operation).hexdigest()
    response={key:operation[key] for key in RPC.RESPONSE_KEYS-{'receipt_sha256'}}
    response['receipt_sha256']=digest
    journal={'schema_version':1,'session_id':TOKEN,'requests':[
        {'request':request,'response':response,'state':'completed','error_code':None}],
        'replies':[response],'error_codes':[]}
    receipt={'run_token':TOKEN,'result':'passed','pending':None,
             'cleanup':{'verified':True,'removed':[],'conflicts':[],'deferred':[]},
             'telemetry':{'result':'passed','cleanup_verified':True,'saved_spans_verified':True},
             'session':{'phase':'closed','server_result':{'result':'verified','error_codes':[],
                          'requests':1,'completed':1},
                        'requests':[{'request_id':REQUEST_ID,'action':'wake','operation_id':OPERATION_ID,
                                     'result':'verified','receipt':'operations/'+OPERATION_ID+'.json',
                                     'receipt_sha256':digest}]}}
    return journal,receipt,raw_operation


def raw(value):
    return (json.dumps(value,sort_keys=True)+'\n').encode()


class SavedCompletionTests(unittest.TestCase):
    def test_correlated_claim_does_not_claim_live_cleanup(self):
        journal,receipt,op=fixture()
        projected=C.correlate(raw(journal),raw(receipt),{OPERATION_ID+'.json':op},TOKEN)
        self.assertEqual(projected['recorded_completion'],'correlated-saved-claim')
        self.assertEqual((projected['operation_count'],projected['runtime_state']),(1,'unknown'))
        self.assertNotIn('cleanup_verified',projected)

    def test_conflicting_saved_claims_cannot_be_promoted(self):
        journal,receipt,op=fixture();name=OPERATION_ID+'.json'
        cases=[]
        r=deepcopy(receipt);r['cleanup']['verified']=False;cases.append((journal,r,{name:op}))
        r=deepcopy(receipt);r['session']['phase']='chilled';cases.append((journal,r,{name:op}))
        r=deepcopy(receipt);r['session']['query_mode']=True;cases.append((journal,r,{name:op}))
        r=deepcopy(receipt);r['telemetry']['saved_spans_verified']=False;cases.append((journal,r,{name:op}))
        r=deepcopy(receipt);r['session']['requests'][0]['receipt_sha256']='c'*64;cases.append((journal,r,{name:op}))
        j=deepcopy(journal);j['requests'][0]['state']='pending';j['requests'][0]['response']=None
        j['replies']=[];cases.append((j,receipt,{name:op}))
        j=deepcopy(journal);j['replies']=[];cases.append((j,receipt,{name:op}))
        cases.append((journal,receipt,{name:op+b' '}))
        cases.append((journal,receipt,{name:op,'extra.json':op}))
        for j,r,files in cases:
            with self.subTest(index=cases.index((j,r,files))),self.assertRaises(S.StatusError):
                C.correlate(raw(j),raw(r),files,TOKEN)

    def test_bounded_private_files_and_cli_option_are_read_only(self):
        journal,receipt,op=fixture();name=OPERATION_ID+'.json'
        with tempfile.TemporaryDirectory(dir=S.ROOT/'tmp/local-experiment') as temp:
            root=Path(temp)/'runs';root.mkdir(mode=0o700)
            run=root/TOKEN;run.mkdir(mode=0o700)
            rpc=run/'rpc';rpc.mkdir(mode=0o700)
            operations=run/'operations';operations.mkdir(mode=0o700)
            for p,data in ((rpc/'journal.json',raw(journal)),(run/'receipt.json',raw(receipt)),
                           (operations/name,op)):
                p.write_bytes(data);p.chmod(0o600)
            with patch.object(S,'RUNS',root):
                self.assertEqual(C.read(TOKEN,root)['recorded_completion'],'correlated-saved-claim')
            (run/'receipt.json').chmod(0o644)
            with self.assertRaises(S.StatusError):C.read(TOKEN,root)
            with patch.object(S,'RUNS',root),patch('sys.stdout',new_callable=io.StringIO) as output,\
                 patch('sys.stderr',new_callable=io.StringIO) as errors:
                self.assertEqual(S.main(['--with-saved-completion',TOKEN]),1)
            self.assertEqual(output.getvalue(),'')
            self.assertEqual(errors.getvalue().strip(),'SAVED_COMPLETION_UNVERIFIED')
            (run/'receipt.json').chmod(0o600)
            (operations/'extra.json').write_bytes(op)
            with self.assertRaises(S.StatusError):C.read(TOKEN,root)
            (operations/'extra.json').unlink()
            (operations/name).rename(operations/'original.json')
            (operations/name).symlink_to(operations/'original.json')
            with self.assertRaises(S.StatusError):C.read(TOKEN,root)


if __name__=='__main__':unittest.main()
