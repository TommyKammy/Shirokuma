"""Grant CLI sends ordinary authenticated HTTP; no backend/control-store imports."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import grant_client


class GrantClientTests(unittest.TestCase):
    def test_list_set_revoke_operation_and_zero_cas(self):
        client = Mock()
        client.request.return_value = {'id':'operation'}
        with patch.object(grant_client,'configured_profile',return_value=object()), \
                patch.object(grant_client,'Client',return_value=client):
            grant_client.main(['list','--database-id','db'])
            client.request.assert_called_with('GET','/api/v1/databases/db/grants')
            grant_client.main(['set','--database-id','db','--principal-id','alice',
                '--maximum-role','data_engineer','--expected-revision','0','--idempotency-key','key'])
            client.request.assert_called_with('POST','/api/v1/databases/db/grants',key='key',body=dict(
                principal_id='alice',maximum_role='data_engineer',expected_revision=0))
            grant_client.main(['set','--database-id','db','--principal-id','db-owner',
                '--maximum-role','admin','--expected-revision','0','--idempotency-key','admin-key'])
            client.request.assert_called_with('POST','/api/v1/databases/db/grants',key='admin-key',body=dict(
                principal_id='db-owner',maximum_role='admin',expected_revision=0))
            grant_client.main(['revoke','--database-id','db','--principal-id','alice',
                '--expected-revision','4','--idempotency-key','revoke'])
            self.assertEqual(client.request.call_args.kwargs['body']['maximum_role'],'revoked')
            grant_client.main(['operation','--operation-id','operation'])
            client.request.assert_called_with('GET','/api/v1/operations/operation')

    def test_missing_cas_negative_cas_and_missing_profile_fail_before_http(self):
        with patch.object(grant_client,'Client') as client:
            for flags in ([], ['--expected-revision','-1']):
                with self.assertRaises(ValueError):
                    grant_client.main(['set','--database-id','db','--principal-id','alice',
                        '--maximum-role','bi_viewer','--idempotency-key','key']+flags)
            with patch.object(grant_client,'configured_profile',return_value=None), self.assertRaises(ValueError):
                grant_client.main(['list','--database-id','db'])
            client.assert_not_called()


if __name__ == '__main__':
    unittest.main()
