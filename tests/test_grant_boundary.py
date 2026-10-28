"""Authorization race checks using temporary state and a captured fake backend."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))

import control_store
import data_management as dm
import dataops_service as ds
import grant_management as grants
import query_service as qs
import request_auth
from grant_fixtures import verified_grants
from query_context import QueryError


class GrantBoundaryTests(unittest.TestCase):
    def setUp(self):
        from test_dataops_service import DataOpsTests
        self.fixture = DataOpsTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.principal = self.fixture.principal
        self.admin = request_auth.AuthenticatedPrincipal('owner','admin',True)

    def edit(self, role, key):
        context = self.fixture.context()
        with control_store.connect() as db:
            revision = dm.get_grant(db,self.principal.principal_id,context.database_id).revision
        return grants.mutate(self.admin,'admin',context.database_id,dict(
            principal_id=self.principal.principal_id,maximum_role=role,expected_revision=revision),key)

    def revoke_and_regrant(self):
        self.edit('revoked','revoke')
        self.edit_without_context('data_engineer','regrant')
        with control_store.connect() as db:
            verified_grants(db)

    def edit_without_context(self, role, key):
        database = self.fixture.databases[0]
        with control_store.connect() as db:
            desired = dm.get_grant(db,self.principal.principal_id,database)
        return grants.mutate(self.admin,'admin',database,dict(principal_id=self.principal.principal_id,
            maximum_role=role,expected_revision=desired.revision),key)

    def test_native_and_dataops_admission_recheck_after_credential_capture(self):
        for kind in ('native','dataops'):
            with self.subTest(kind=kind):
                context = self.fixture.context()
                original = qs._acquire_query_owner
                def revoke_before_transaction(query_id):
                    owner = original(query_id)
                    self.edit('revoked','race-'+kind)
                    return owner
                with patch.object(qs,'_acquire_query_owner',side_effect=revoke_before_transaction):
                    with self.assertRaises(QueryError) as error:
                        if kind == 'native':
                            qs.submit(self.principal,context,'SELECT 1',mode='read-only',
                                max_rows=10,request_id='race',worksheet_id=None)
                        else:
                            ds.begin(self.principal,context,route='/race',idempotency_key='race',payload={})
                    self.assertEqual(error.exception.code,'DB_ACCESS_REVOKED')
                with control_store.connect() as db:
                    for table in ('query_records','dataops_operations','warehouse_admissions'):
                        self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
                self.assertEqual(self.fixture.calls,[])
                self.edit_without_context('data_engineer','restore-'+kind)
                with control_store.connect() as db:
                    verified_grants(db)

    def test_native_old_epoch_reads_rejected_after_verified_regrant(self):
        context = self.fixture.context()
        with patch.object(qs,'_transport',return_value=dict(id='native',data=[[1]])):
            page = qs.submit(self.principal,context,'SELECT 1',mode='read-only',max_rows=10,
                             request_id='old',worksheet_id=None)
        self.revoke_and_regrant()
        self.assertNotEqual(self.fixture.context().grant_revision,context.grant_revision)
        with self.assertRaises(QueryError) as error:
            qs.inspect_query(self.principal,page['id'],selected_role=context.selected_role)
        self.assertEqual(error.exception.code,'DB_ACCESS_REVOKED')
        self.assertEqual(qs.history(self.principal,database_id=context.database_id,
            selected_role=context.selected_role),{'queries':[]})

    def test_history_revoke_regrant_during_collection_rejects_collected_sql(self):
        context = self.fixture.context()
        with patch.object(qs,'_transport',return_value=dict(id='native',data=[[1]])):
            qs.submit(self.principal,context,'SELECT 1',mode='read-only',max_rows=10,
                      request_id='history',worksheet_id=None)
        original = qs.inspect_query
        def collect_then_change(*args,**kwargs):
            item = original(*args,**kwargs)
            self.revoke_and_regrant()
            return item
        with patch.object(qs,'inspect_query',side_effect=collect_then_change):
            with self.assertRaises(QueryError) as error:
                qs.history(self.principal,database_id=context.database_id,selected_role=context.selected_role)
        self.assertEqual(error.exception.code,'DB_ACCESS_REVOKED')

    def test_revoke_during_native_submit_and_fetch_never_returns_backend_rows(self):
        for boundary in ('submit','fetch'):
            with self.subTest(boundary=boundary):
                context = self.fixture.context()
                def response(url,method,pin,body=None):
                    if method == 'DELETE':
                        return {}
                    if boundary == 'fetch' and method == 'POST':
                        return dict(id='native',nextUri='http://127.0.0.1:1234/v1/statement/native/1/page')
                    self.edit('revoked','response-'+boundary)
                    return dict(id='native',data=[['private-result']])
                with patch.object(qs,'_transport',side_effect=response):
                    with self.assertRaises(QueryError) as error:
                        page = qs.submit(self.principal,context,'SELECT 1',mode='read-only',max_rows=10,
                            request_id='response-'+boundary,worksheet_id=None)
                        qs.fetch(self.principal,page['id'],page['next_uri'].rsplit('/',1)[1],
                            selected_role=context.selected_role)
                    self.assertEqual(error.exception.code,'DB_ACCESS_REVOKED')
                self.edit_without_context('data_engineer','response-restore-'+boundary)
                with control_store.connect() as db:
                    verified_grants(db)


class ScopedAdminBoundaryTests(unittest.TestCase):
    def test_scoped_admin_revoke_pending_regrant_and_old_epoch_revalidation(self):
        from test_request_auth import PrincipalTests
        from query_context import resolve_context, revalidate_admission
        fixture = PrincipalTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.users['alice']['role'] = 'admin'
        fixture.save()
        principal = fixture.principal()
        owner = fixture.principal('owner')
        with control_store.connect() as db:
            schema = dm.create_schema(db, fixture.sales, 'raw', 'Raw', 'raw', 'test')
            dm.update_grant(db, 'alice', fixture.sales, 1, 'admin')
            # Seed authority only for the consumer/revalidation boundary.
            db.execute("UPDATE effective_grants SET maximum_role='admin',epoch=epoch+1 WHERE principal_id='alice'")
        def context():
            return resolve_context(principal, fixture.sales, schema.id, 'admin',
                warehouse_id='w', expected_binding_revision=1)
        old = context()
        with control_store.connect() as db:
            revalidate_admission(db, principal, old)
        def change(role, revision, key):
            return grants.mutate(owner, 'admin', fixture.sales, dict(principal_id='alice',
                maximum_role=role, expected_revision=revision), key)
        change('revoked', 2, 'revoke-admin')
        with control_store.connect() as db:
            with self.assertRaises(QueryError):
                revalidate_admission(db, principal, old)
        change('admin', 3, 'regrant-admin')
        with self.assertRaises(QueryError):
            context()
        with control_store.connect() as db:
            db.execute("UPDATE effective_grants SET maximum_role='admin',epoch=epoch+1 WHERE principal_id='alice'")
        current = context()
        self.assertNotEqual(current.grant_revision, old.grant_revision)
        with control_store.connect() as db:
            with self.assertRaises(QueryError):
                revalidate_admission(db, principal, old)
            revalidate_admission(db, principal, current)


if __name__ == '__main__':
    unittest.main()
