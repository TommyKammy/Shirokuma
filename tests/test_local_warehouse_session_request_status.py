import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.warehouse_lifecycle import status as S
from test_local_warehouse_session_status import TOKEN,ID,entry,raw

MISSING='22222222-2222-4222-8222-222222222222'


class RequestStatusTests(unittest.TestCase):
    def test_saved_request_identity_and_absence_are_not_execution_claims(self):
        e=entry(state='completed');source=raw([e],[e['response']])
        prior=S.project(source,TOKEN)
        self.assertNotIn('request_lookup',prior)
        found=S.project(source,TOKEN,ID)
        self.assertEqual({k:found[k] for k in prior},prior)
        self.assertEqual(found['request_lookup'],{'request_id':ID,'observation':'recorded-in-snapshot',
            'recorded_action':'query','recorded_result':'verified',
            'operation_id':ID,'receipt_sha256':'b'*64})
        self.assertEqual(found['runtime_state'],'unknown')
        not_found=S.project(source,TOKEN,MISSING)['request_lookup']
        self.assertEqual(not_found['observation'],'not-recorded-in-snapshot')
        self.assertIsNone(not_found['recorded_result'])
        pending=S.project(raw([entry()]),TOKEN,ID)['request_lookup']
        self.assertEqual((pending['recorded_result'],pending['operation_id']),('pending',None))
        self.assertNotIn('not-executed',json.dumps(not_found))

    def test_invalid_id_fails_before_open_and_cli_has_no_partial_json(self):
        with patch.object(S,'_read',side_effect=AssertionError('must not open')):
            for request_id in ('../outside','A'*36,'',None,True):
                if request_id is None:continue  # None deliberately selects the legacy all-entries mode.
                with self.subTest(request_id=request_id),self.assertRaises(S.StatusError):
                    S.read(TOKEN,request_id=request_id)
        with tempfile.TemporaryDirectory(dir=S.ROOT/'tmp/local-experiment') as temp:
            root=Path(temp)/'runs';root.mkdir(mode=0o700)
            with patch.dict(os.environ,{'WAREHOUSE_LIFECYCLE_RUN_TOKEN':TOKEN},clear=False),\
                 patch.object(S,'RUNS',root),patch('sys.stdout',new_callable=io.StringIO) as out,\
                 patch('sys.stderr',new_callable=io.StringIO) as err:
                self.assertEqual(S.main(['--request-id-from-env']),1)
            self.assertEqual(out.getvalue(),'')
            self.assertEqual(err.getvalue().strip(),'JOURNAL_UNVERIFIED')


if __name__=='__main__':unittest.main()
