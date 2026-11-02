"""Nonpurging catalog removal against real DataOps ledgers, controlled catalogs."""
from contextlib import contextmanager, ExitStack
from dataclasses import asdict, replace
from http.client import HTTPResponse
import io
import json
from pathlib import Path
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import unquote, urlencode
import urllib.error
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import control_store
import data_management as dm
import database_inventory as inv
import dataops_service as ds
import manual_table_drop as drop
from query_context import QueryError
import test_dataops_service as boundary

CATALOG_API = inv.catalog_api
REAL_THREAD = threading.Thread


class InlineThread:
    def __init__(self, target, args, **kwargs): self.target, self.args = target, args
    def start(self): self.target(*self.args)


class DropTests(unittest.TestCase):
    def setUp(self):
        self.b = boundary.DataOpsTests()
        self.b.setUp()
        self.addCleanup(self.b.doCleanups)
        self.b.fixture.users['alice']['role'] = 'admin'
        self.b.fixture.save()
        with control_store.connect() as db:
            dm.update_grant(db, 'alice', self.b.databases[0], 1, 'admin')
            from grant_fixtures import verified_grants
            verified_grants(db)
        self.principal = self.b.fixture.principal()
        self.context = self.b.context(principal=self.principal, role='admin')
        self.source = self.b.existing()
        self.name = 'observed_data'
        self.metadata = self.b.objects[('sales', 'raw', self.name, 'tables')]
        self.metadata.update({'current-snapshot-id': 0, 'current-schema-id': 1, 'snapshots': [],
            'schemas': [{'schema-id': 1, 'fields': []}], 'metadata-file-location': 's3://secret/private'})
        self.deletes = []
        self.behavior = lambda: None
        self.read_hook = lambda: None
        self.b.start_patch('database_inventory.catalog_api', self.catalog)
        self.b.start_patch('database_inventory.table_drop_api', self.transport)
        self.b.start_patch('manual_table_drop.threading.Thread', InlineThread)

    @contextmanager
    def catalog(self, database, binding):
        def read(path):
            self.read_hook()
            parts = path.split('/')
            key = (database.sql_catalog, unquote(parts[2]), unquote(parts[4]), parts[3])
            if key not in self.b.objects:
                raise inv.InventoryError('NOT_FOUND', 404, 'private backend URI')
            return {'metadata': json.loads(json.dumps(self.b.objects[key])), 'metadata-location': 's3://private/metadata.json'}
        yield read

    @contextmanager
    def transport(self, database, binding):
        with self.catalog(database, binding) as reader:
            def remove(schema, name):
                with control_store.connect() as db:
                    row = db.execute('SELECT * FROM dataops_operations WHERE route=? ORDER BY created_at DESC', (drop.ROUTE,)).fetchone()
                    self.assertEqual(json.loads(row['result'])['phase'], 'SENT')
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM dataops_queries').fetchone()[0], 0)
                self.deletes.append((schema.sql_name, name))
                self.behavior()
                self.b.objects.pop((database.sql_catalog, schema.sql_name, name, 'tables'), None)
                return 204
            yield reader, remove

    def preview(self): return drop.preview(self.principal, self.context, self.source)

    def submit(self, key='remove', **options):
        fingerprint = options.pop('fingerprint', None) or self.preview()['preview_fingerprint']
        return drop.submit(self.principal, self.context, key=key, source=self.source,
            mode=options.pop('mode', drop.MODE), expected_preview_fingerprint=fingerprint,
            confirm_name=options.pop('confirm_name', self.name), **options)

    def row(self):
        with control_store.connect() as db:
            return dict(db.execute('SELECT * FROM dataops_operations WHERE route=? ORDER BY created_at DESC', (drop.ROUTE,)).fetchone())

    def test_pure_preview_closed_no_admission_or_registry_observe(self):
        with patch.object(dm, 'observe_object', side_effect=AssertionError('write')):
            value = self.preview()
        self.assertEqual(set(value), {'label', 'source', 'mode', 'head', 'scope', 'preview_fingerprint', 'confirmation_name'})
        self.assertEqual(value['head'], '0')
        self.assertNotIn('private', json.dumps(value))
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dataops_operations').fetchone()[0], 0)

    def test_canonical_empty_heads_and_noncanonical_rejections(self):
        for head in (None, -1):
            self.metadata.update({'current-snapshot-id':head,'snapshots':[]})
            self.assertIsNone(self.preview()['head'])
        for head, snapshots in ((-1,[{'snapshot-id':1}]),(None,[{'snapshot-id':1}]),(-2,[]),('-1',[]),('00',[]),('01',[]),('',[]),(True,[]),(1.0,[])):
            self.metadata.update({'current-snapshot-id':head,'snapshots':snapshots})
            with self.subTest(head=head,snapshots=snapshots),self.assertRaises(QueryError):self.preview()
        self.metadata.update({'current-snapshot-id':0,'snapshots':[]})
        self.assertEqual(self.preview()['head'],'0')

    def test_ack_bookkeeping_mismatch_preserves_commit_unproven_closure(self):
        def mismatch():
            with control_store.connect() as db:
                db.execute('UPDATE object_bindings SET backend_identity=? WHERE id=?',(str(uuid.uuid4()),self.source.object_id))
        self.behavior=mismatch
        result=self.submit()
        self.assertEqual((result['state'],result['committed'],result['acknowledged']),('FINISHED',True,True))
        self.assertEqual(result['retirement_closure'],'UNPROVEN')
        self.assertEqual(result['warning'],'CATALOG_REMOVED_RETIREMENT_UNPROVEN')
        with control_store.connect() as db:self.assertFalse(drop.pure_closed_proof(db,result['id']))

    def test_ack_missing_old_row_keeps_effect_receipt(self):
        def missing():
            with control_store.connect() as db:
                db.execute('DELETE FROM object_bindings WHERE id=?',(self.source.object_id,))
        self.behavior=missing
        result=self.submit()
        self.assertTrue(result['committed']);self.assertTrue(result['acknowledged'])
        self.assertEqual(result['retirement_closure'],'UNPROVEN')
        self.assertEqual(result['state'],'FINISHED')
        self.assertEqual(len(self.deletes),1)

    def test_pure_closed_proof_historical_readonly_and_strict_saved_contract(self):
        result=self.submit();ident=result['id']
        with control_store.connect() as db:
            saved=dict(db.execute('SELECT * FROM dataops_operations WHERE id=?',(ident,)).fetchone())
            before=db.total_changes
            with patch.object(drop,'_access',side_effect=AssertionError('auth')),patch.object(inv,'catalog_api',side_effect=AssertionError('catalog')):
                self.assertTrue(drop.pure_closed_proof(db,ident))
            self.assertEqual(db.total_changes,before)
            db.execute('UPDATE catalog_bindings SET desired_revision=desired_revision+1 WHERE database_id=?',(self.context.database_id,))
            dm.update_grant(db,'alice',self.context.database_id,2,'revoked')
            dm.observe_object(db,self.source.schema_id,self.name,'TABLE',str(uuid.uuid4()),self.name)
            self.assertTrue(drop.pure_closed_proof(db,ident))
            for column,value in (('route','other'),('state','UNKNOWN'),('committed',0),('fingerprint','bad'),('result','{'),('payload','{}')):
                db.execute('UPDATE dataops_operations SET '+column+'=? WHERE id=?',(value,ident))
                self.assertFalse(drop.pure_closed_proof(db,ident),column)
                db.execute('UPDATE dataops_operations SET '+column+'=? WHERE id=?',(saved[column],ident))
            receipt=json.loads(saved['result'])
            for field,value in (('ack',200),('ack_type','SQL'),('mode','DROP'),('retirement_closure','UNPROVEN')):
                changed=dict(receipt,**{field:value})
                db.execute('UPDATE dataops_operations SET result=? WHERE id=?',(ds._json(changed),ident))
                self.assertFalse(drop.pure_closed_proof(db,ident),field)
            db.execute('UPDATE dataops_operations SET result=? WHERE id=?',(saved['result'],ident))
            db.execute("UPDATE warehouse_admissions SET actor='other' WHERE subject_id=?",(ident,))
            self.assertFalse(drop.pure_closed_proof(db,ident))

    def test_drop_reconciliation_cannot_abandon_sent_unknown(self):
        self.behavior=lambda:(_ for _ in ()).throw(inv.TableDropTransportError(True))
        result=self.submit();execution=drop.DropExecution(self.principal,result['id'])
        for action in (execution.reconcile_failure,execution.reconcile_queries,execution._successful_work):
            with self.assertRaises(QueryError):action()
        self.assertEqual(self.row()['state'],'UNKNOWN')

    def test_ack_retires_original_row_and_deleted_source_recovery_no_sql(self):
        fingerprint = self.preview()['preview_fingerprint']
        result = self.submit(fingerprint=fingerprint)
        self.assertEqual((result['state'], result['committed'], result['acknowledged']), ('FINISHED', True, True))
        self.assertEqual(result['queries'], [])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT current FROM object_bindings WHERE id=?', (self.source.object_id,)).fetchone()[0], 0)
        replay = self.submit(fingerprint=fingerprint)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['id'], result['id'])
        self.assertEqual(len(self.deletes), 1)
        self.assertFalse(self.b.calls)
        for secret in ('private', 'table-uuid', 'backend_identity', 'metadata-location', 'guard', 's3://'):
            self.assertNotIn(secret, json.dumps(replay))
        with self.assertRaises(QueryError) as conflict:
            self.submit(fingerprint='0' * 64)
        self.assertEqual(conflict.exception.code, 'IDEMPOTENCY_CONFLICT')

    def test_by_key_missing_and_owned_lookup_leave_all_stores_unchanged(self):
        def snapshot():
            with control_store.connect() as db:ledger=tuple(db.iterdump())
            return ledger,dict(ds._live),tuple((k,v[2].closed) for k,v in ds._live.items()),repr(self.b.objects),list(self.deletes)
        before=snapshot()
        with (patch.object(ds,'begin',side_effect=AssertionError('admission')),patch.object(drop,'_settle',side_effect=AssertionError('settle')),
                patch.object(inv,'catalog_api',side_effect=AssertionError('catalog'))):
            with self.assertRaises(QueryError) as missing:drop.lookup_by_key(self.principal,self.context,'not-arrived')
        self.assertEqual((missing.exception.code,missing.exception.status,missing.exception.details),('OPERATION_NOT_FOUND',404,{}))
        self.assertEqual(snapshot(),before)
        # This is the delayed original explicit submission, not a lookup retry.
        result=self.submit(key='not-arrived');self.assertTrue(result['committed'])
        before=snapshot()
        with (patch.object(ds,'begin',side_effect=AssertionError('admission')),patch.object(drop,'_settle',side_effect=AssertionError('settle')),
                patch.object(inv,'catalog_api',side_effect=AssertionError('catalog'))):
            found=drop.lookup_by_key(self.principal,self.context,'not-arrived')
            self.assertEqual(found['id'],result['id']);self.assertTrue(found['committed'])
            owner=self.b.fixture.principal('owner');context=self.b.context(principal=owner,role='admin')
            with self.assertRaises(QueryError) as foreign:drop.lookup_by_key(owner,context,'not-arrived')
        self.assertEqual((foreign.exception.code,foreign.exception.status,foreign.exception.details),('OPERATION_NOT_FOUND',404,{}))
        self.assertEqual(snapshot(),before);self.assertEqual(len(self.deletes),1)
        for hidden in ('guard','s3://','private','table-uuid'):self.assertNotIn(hidden,json.dumps(found))

    def test_by_key_exact_saved_context_current_role_and_late_revocation(self):
        result=self.submit(key='saved')
        other=self.b.context(principal=self.principal,role='admin',schema='sandbox')
        with self.assertRaises(QueryError) as mismatch:drop.lookup_by_key(self.principal,other,'saved')
        self.assertEqual(mismatch.exception.code,'CONTEXT_MISMATCH')
        for context in (replace(self.context,selected_role='data_engineer'),replace(self.context,grant_revision=None)):
            with self.assertRaises((QueryError,PermissionError)):drop.lookup_by_key(self.principal,context,'saved')
        original=drop.DropExecution._inspect
        def revoke(execution,**kwargs):
            value=original(execution,**kwargs)
            self.b.fixture.users['alice']['enabled']=False;self.b.fixture.save()
            return value
        with patch.object(drop.DropExecution,'_inspect',revoke),patch('query_service._best_cancel',side_effect=AssertionError('cancel')):
            with self.assertRaises((QueryError,PermissionError)):drop.lookup_by_key(self.principal,self.context,'saved')
        self.assertEqual(self.row()['state'],'FINISHED');self.assertTrue(self.row()['committed']);self.assertEqual(len(self.deletes),1)

    def test_by_key_lost_worker_sent_observation_does_not_reconcile_or_acquire_lease(self):
        class DeferredThread(InlineThread):
            def start(self):pass
        with patch.object(drop.threading,'Thread',DeferredThread):result=self.submit(key='lost-worker')
        ds._live.pop(result['id'])[2].close()
        with control_store.connect() as db:
            row=db.execute('SELECT result FROM dataops_operations WHERE id=?',(result['id'],)).fetchone()
            receipt=json.loads(row['result']);receipt['phase']='SENT'
            db.execute('UPDATE dataops_operations SET result=? WHERE id=?',(ds._json(receipt),result['id']))
        with control_store.connect() as db:before=tuple(db.iterdump())
        with patch.object(ds.queries,'_acquire_query_owner',side_effect=AssertionError('lease')),patch.object(drop,'_settle',side_effect=AssertionError('settle')):
            found=drop.lookup_by_key(self.principal,self.context,'lost-worker')
        self.assertEqual((found['state'],found['phase'],found['committed']),('ACTIVE','SENT',False))
        with control_store.connect() as db:self.assertEqual(tuple(db.iterdump()),before)
        self.assertFalse(self.deletes)

    def test_general_names_and_empty_heads(self):
        for index, head in enumerate((None, 0, '0', 123456789123456789)):
            name = '大文字 A/"?%#' + 'Long' * 30 + str(index)
            with control_store.connect() as db:
                obj = dm.observe_object(db, self.context.schema_id, name, 'TABLE', str(uuid.uuid4()), name)
            self.source = dm.ResourceRef(self.context.database_id, self.context.schema_id, obj.id, obj.incarnation)
            self.name = name
            self.b.objects[('sales', 'raw', name, 'tables')] = {'table-uuid': obj.backend_identity, 'current-snapshot-id': head, 'snapshots': []}
            self.assertTrue(self.submit(key=str(index))['committed'])
        self.assertEqual(len(self.deletes), 4)

    def test_all_four_registered_schema_purposes(self):
        for schema in ('raw', 'sandbox', 'staging', 'marts'):
            if schema == 'staging':
                with control_store.connect() as db:
                    self.b.schemas[self.context.database_id][schema] = dm.create_schema(db, self.context.database_id, schema, schema, schema, 'test').id
            self.context = self.b.context(schema=schema, principal=self.principal, role='admin')
            self.source = self.b.existing(schema=schema)
            self.b.objects[('sales',schema,self.name,'tables')]['snapshots']=[]
            self.assertTrue(self.submit(key=schema)['committed'])

    def test_non_admin_role_denied_before_catalog(self):
        for role in ('data_engineer', 'bi_viewer'):
            with patch.object(inv, 'catalog_api', side_effect=AssertionError('read')), self.assertRaises(QueryError):
                drop.preview(self.principal, replace(self.context, selected_role=role), self.source)

    def test_canonical_platform_admin_null_no_grant_and_revoked_grant_both_databases(self):
        import dataops_routes
        from email.message import Message
        self.principal = self.b.fixture.principal('owner')
        second = self.b.databases[1]
        with control_store.connect() as db:
            dm.create_grant(db, 'owner', second, 'admin')
            dm.update_grant(db, 'owner', second, 1, 'revoked')
            from grant_fixtures import verified_grants
            verified_grants(db)
        class Handler:
            def __init__(self, path, body=None):
                self.path, self.body, self.headers = path, body, Message()
                if body is not None:self.headers['Content-Type']='application/json'
            def database_body(self):return self.body
            def respond(self,value):self.reply=value
        for index,database in enumerate(self.b.databases[:2]):
            self.context=self.b.context(database=database,principal=self.principal,role='admin')
            self.assertIsNone(self.context.grant_revision)
            self.name='platform_drop_'+str(index)
            self.source=self.b.existing(self.name,database=database)
            catalog=self.context.sql_catalog
            self.b.objects[(catalog,'raw',self.name,'tables')]['snapshots']=[]
            preview=self.preview();fingerprint=preview['preview_fingerprint']
            self.assertEqual(preview['source'],asdict(self.source))
            result=self.submit(key=str(index),fingerprint=fingerprint)
            self.assertTrue(result['committed']);self.assertIsNone(result['context']['grant_revision'])
            self.assertEqual(self.submit(key=str(index),fingerprint=fingerprint)['id'],result['id'])
            for prefix in ('dataops','table-drops'):
                path='/api/v1/'+prefix+'/'+result['id']
                for method,suffix,body,params in (
                    ('GET','',None,{'context':[json.dumps(asdict(self.context))]}),
                    ('POST','/cancel',{'context':asdict(self.context)},{})):
                    handler=Handler(path+suffix,body)
                    self.assertTrue(dataops_routes.dispatch(handler,method,path+suffix,params,self.principal,'admin'))
                    self.assertTrue(handler.reply['operation']['committed'])
                    self.assertEqual(handler.reply['operation']['source'],asdict(self.source))
                    self.assertNotIn('private',json.dumps(handler.reply))
            with control_store.connect() as db:self.assertTrue(drop.pure_closed_proof(db,result['id']))
        self.assertEqual(len(self.deletes),2);self.assertFalse(self.b.calls)

    def test_forged_null_and_stale_platform_identity_denied_before_catalog(self):
        import request_auth
        forged=replace(self.context,grant_revision=None)
        for principal in (self.principal,replace(self.principal,platform_admin=True),
                request_auth.AuthenticatedPrincipal('unknown','admin',True)):
            with patch.object(inv,'catalog_api',side_effect=AssertionError('catalog')),self.assertRaises((QueryError,PermissionError)):
                drop.preview(principal,forged,self.source)
        owner=self.b.fixture.principal('owner')
        context=self.b.context(principal=owner,role='admin')
        for change in ({'platform_admin':False},{'role':'data_engineer'},{'enabled':False}):
            original=dict(self.b.fixture.users['owner']);self.b.fixture.users['owner'].update(change);self.b.fixture.save()
            with patch.object(inv,'catalog_api',side_effect=AssertionError('catalog')),self.assertRaises((QueryError,PermissionError)):
                drop.preview(owner,context,self.source)
            self.b.fixture.users['owner']=original;self.b.fixture.save()
        with patch.object(inv,'catalog_api',side_effect=AssertionError('catalog')),self.assertRaises((QueryError,PermissionError)):
            drop.preview(replace(owner,maximum_role='data_engineer'),context,self.source)
        self.assertFalse(self.deletes)

    def test_platform_flag_loss_after_ack_hides_receipt_without_cancel_or_resend(self):
        self.principal=self.b.fixture.principal('owner')
        self.context=self.b.context(principal=self.principal,role='admin')
        fingerprint=self.preview()['preview_fingerprint']
        def revoke():
            self.b.fixture.users['owner']['platform_admin']=False;self.b.fixture.save()
        self.behavior=revoke
        with patch('query_service._best_cancel',side_effect=AssertionError('cancel')):
            with self.assertRaises((QueryError,PermissionError)):self.submit(fingerprint=fingerprint)
            self.assertEqual(self.row()['state'],'FINISHED');self.assertTrue(self.row()['committed'])
            with self.assertRaises((QueryError,PermissionError)):self.submit(fingerprint=fingerprint)
        self.assertEqual(len(self.deletes),1)
        with control_store.connect() as db:self.assertTrue(drop.pure_closed_proof(db,self.row()['id']))

    def test_null_closed_proof_rejects_malformed_epochs_even_with_consistent_digests(self):
        self.principal=self.b.fixture.principal('owner')
        self.context=self.b.context(principal=self.principal,role='admin')
        result=self.submit();ident=result['id']
        with control_store.connect() as db:
            saved=dict(db.execute('SELECT * FROM dataops_operations WHERE id=?',(ident,)).fetchone())
            for field in ('grant_revision','binding_revision'):
                for epoch in (True,False,0,-1,'1'):
                    context=json.loads(saved['context']);context[field]=epoch
                    payload=json.loads(saved['payload']);payload['context']=context
                    receipt=json.loads(saved['result']);receipt['guard']['context']=context
                    payload['payload']['expected_preview_fingerprint']=drop.hashlib.sha256(ds._json(receipt['guard']).encode()).hexdigest()
                    normalized=ds._json(payload)
                    db.execute('UPDATE dataops_operations SET context=?,payload=?,fingerprint=?,result=? WHERE id=?',
                        (ds._json(context),normalized,drop.hashlib.sha256(normalized.encode()).hexdigest(),ds._json(receipt),ident))
                    self.assertFalse(drop.pure_closed_proof(db,ident),(field,epoch))
            db.execute('UPDATE dataops_operations SET context=?,payload=?,fingerprint=?,result=? WHERE id=?',
                (saved['context'],saved['payload'],saved['fingerprint'],saved['result'],ident))
            self.assertTrue(drop.pure_closed_proof(db,ident))

    def test_wrong_primary_crossdb_view_incarnation_uuid(self):
        for source in (replace(self.source, schema_id=self.b.schemas[self.context.database_id]['sandbox']),
                replace(self.source, database_id=self.b.databases[1]), replace(self.source, incarnation=999),
                self.b.existing('view', kind='VIEW')):
            with self.assertRaises(QueryError): drop.preview(self.principal, self.context, source)
        self.metadata['table-uuid'] = str(uuid.uuid4())
        with self.assertRaises(QueryError): self.preview()
        self.assertFalse(self.deletes)

    def test_schema_purpose_denied_before_credentials(self):
        with control_store.connect() as db:
            db.execute("UPDATE schemas SET purpose='RAW' WHERE id=?", (self.context.schema_id,))
        with patch.object(inv, 'catalog_api', side_effect=AssertionError('read')), self.assertRaises(QueryError): self.preview()

    def test_full_context_binding_generation_and_grant_epoch_denied_before_catalog(self):
        for name, value in (('sql_catalog', 'other'), ('sql_schema', 'sandbox'),
                ('warehouse_id', 'other'), ('binding_revision', 2), ('generation_id', 'other'),
                ('grant_revision', self.context.grant_revision + 1)):
            with self.subTest(field=name), patch.object(inv, 'catalog_api', side_effect=AssertionError('read')):
                with self.assertRaises((QueryError, PermissionError)):
                    drop.preview(self.principal, replace(self.context, **{name:value}), self.source)
        self.assertFalse(self.deletes)

    def test_current_grant_account_and_schema_change_during_preview_denied(self):
        def change_schema():
            with control_store.connect() as db:
                db.execute("UPDATE schemas SET purpose='RAW' WHERE id=?", (self.context.schema_id,))
        self.read_hook=change_schema
        with self.assertRaises(QueryError): self.preview()
        self.assertFalse(self.deletes)

    def test_late_owner_inspection_denied_without_deleted_source_dependency(self):
        result=self.submit()
        with self.assertRaises(QueryError) as denied:
            drop.inspect(ds.Execution(self.b.fixture.principal('owner'), result['id']), self.context)
        self.assertEqual(denied.exception.status,404)
        self.assertTrue(drop.inspect(ds.Execution(self.principal,result['id']),self.context)['committed'])

    def test_retired_uuid_cannot_be_observed_back_into_current(self):
        identity=self.metadata['table-uuid']
        self.submit()
        with control_store.connect() as db, self.assertRaises(dm.RevisionConflict):
            dm.observe_object(db,self.source.schema_id,self.name,'TABLE',identity,self.name)

    def test_exact_confirmation_digest_mode_and_types(self):
        for options in ({'confirm_name': self.name.upper()}, {'fingerprint': '0' * 64}, {'mode': 'DROP'},
                {'mode': None}, {'confirm_name': 1}):
            with self.subTest(options=options), self.assertRaises((QueryError, ValueError)):
                self.submit(key=str(options), **options)
        self.assertFalse(self.deletes)

    def test_owned_stage_or_backup_even_terminal(self):
        owner = ds.begin(self.principal, self.context, route='other', idempotency_key='owned', payload={})
        for kind in ('stage', 'backup'):
            with control_store.connect() as db:
                db.execute('DELETE FROM dataops_owned_objects')
                db.execute('INSERT INTO dataops_owned_objects VALUES (?,?,?,?)', (owner.id, self.source.object_id, self.source.incarnation, kind))
            with self.assertRaises(QueryError): self.preview()
        self.assertFalse(self.deletes)

    def test_unknown_claim_blocks_removal(self):
        owner = ds.begin(self.principal, self.context, route='other', idempotency_key='claim', payload={})
        owner._target_claim(self.source)
        with control_store.connect() as db:
            db.execute("UPDATE dataops_operations SET state='UNKNOWN' WHERE id=?", (owner.id,))
        self.assertEqual(self.submit()['state'], 'FAILED')
        self.assertFalse(self.deletes)

    def test_head_schema_change_at_last_read_prevents_delete(self):
        for index, change in enumerate(({'current-snapshot-id': 19}, {'current-schema-id': 88}, {'table-uuid': str(uuid.uuid4())})):
            saved = dict(self.metadata)
            original = self.transport
            @contextmanager
            def changed(database, binding):
                self.metadata.update(change)
                with original(database, binding) as transport: yield transport
            with patch.object(inv, 'table_drop_api', changed):
                self.assertEqual(self.submit(key=str(index))['state'], 'FAILED')
            self.metadata.clear();self.metadata.update(saved)
        self.assertFalse(self.deletes)

    def test_definite_reject_vs_unknown_and_restart_no_retry(self):
        for index, possible in enumerate((False, True)):
            def reject(): raise inv.TableDropTransportError(possible)
            self.behavior = reject
            fingerprint = self.preview()['preview_fingerprint']
            result = self.submit(key=str(index), fingerprint=fingerprint)
            self.assertEqual(result['state'], 'UNKNOWN' if possible else 'FAILED')
            ds._live.pop(result['id'])[2].close()
            self.assertEqual(self.submit(key=str(index), fingerprint=fingerprint)['id'], result['id'])
        self.assertEqual(len(self.deletes), 2)

    def test_post_ack_failed_observation_is_committed_warning(self):
        def fail_reads():
            self.read_hook = lambda: (_ for _ in ()).throw(OSError('private url'))
        self.behavior = fail_reads
        result = self.submit()
        self.assertTrue(result['committed'])
        self.assertEqual(result['warning'], 'POST_ACK_OBSERVATION_UNAVAILABLE')

    def test_ack_after_revocation_persists_receipt_and_settles_lease(self):
        self.behavior = lambda: self.b.fixture.users.pop('alice')
        def revoke():
            self.b.fixture.users.pop('alice'); self.b.fixture.save()
        self.behavior = revoke
        with self.assertRaises((QueryError, PermissionError)): self.submit()
        row = self.row()
        self.assertTrue(row['committed'])
        self.assertEqual(row['state'], 'FINISHED')
        self.assertEqual(json.loads(row['result'])['ack'], 204)

    def test_external_replacement_never_retires_new_identity(self):
        def replace_object():
            with control_store.connect() as db:
                self.replacement = dm.observe_object(db, self.source.schema_id, self.name, 'TABLE', str(uuid.uuid4()), self.name)
        self.behavior = replace_object
        self.assertTrue(self.submit()['committed'])
        with control_store.connect() as db:
            self.assertIsNotNone(dm.get_object(db, self.replacement.id))
            self.assertTrue(drop.pure_closed_proof(db,self.row()['id']))

    def test_persistence_failure_after_send_keeps_unknown(self):
        original = drop._settle
        def fail_ack(execution, state, result, **kwargs):
            if kwargs.get('ack'): raise OSError('disk')
            return original(execution, state, result, **kwargs)
        with patch.object(drop, '_settle', fail_ack): result = self.submit()
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertFalse(result['acknowledged'])
        self.assertEqual(len(self.deletes), 1)

    def test_cancel_before_dispatch_and_sent_never_false_canceled(self):
        pending = []
        class DeferredThread(InlineThread):
            def start(self): pending.append(self)
        with patch.object(drop.threading, 'Thread', DeferredThread): result = self.submit()
        execution = drop.DropExecution(self.principal, result['id'])
        self.assertEqual(execution.cancel()['state'], 'CANCELED')
        pending[0].target(*pending[0].args)
        self.assertFalse(self.deletes)
        def cancel_sent():
            row = self.row()
            self.assertEqual(drop.DropExecution(self.principal, row['id']).cancel()['state'], 'ACTIVE')
        self.behavior = cancel_sent
        self.assertEqual(self.submit(key='late')['state'], 'FINISHED')

    def test_remote_predispatch_cancel_worker_closes_only_own_live_lease(self):
        pending = []
        class DeferredThread(InlineThread):
            def start(self): pending.append(self)
        unrelated = ds.begin(self.principal, self.context, route='unrelated',
            idempotency_key='unrelated-owner', payload={})
        unrelated_owner = ds._live[unrelated.id][2]
        with patch.object(drop.threading, 'Thread', DeferredThread):
            result = self.submit()
        ident, source = result['id'], self.source
        execution = drop.DropExecution(self.principal, ident)
        live = ds._live.pop(ident)
        owner = live[2]
        try:
            # A cancellation process has no access to the accepting process's
            # live lease. It can only persist the canonical pre-dispatch cancel.
            self.assertEqual(execution.cancel()['state'], 'CANCELED')
            self.assertFalse(owner.closed)
            with self.assertRaises(QueryError): ds.queries._acquire_query_owner('dataops-' + ident)
        finally:
            ds._live[ident] = live
        with control_store.connect() as db:
            canceled = dict(db.execute('SELECT * FROM dataops_operations WHERE id=?', (ident,)).fetchone())
        pending[0].target(*pending[0].args)
        self.assertTrue(owner.closed)
        self.assertFalse(self.deletes)
        with ds.queries._acquire_query_owner('dataops-' + ident): pass
        with control_store.connect() as db:
            self.assertIsNotNone(dm.get_object(db, source.object_id))
            self.assertEqual(dict(db.execute('SELECT * FROM dataops_operations WHERE id=?', (ident,)).fetchone()), canceled)
        # A separate acknowledged operation and unrelated live owner survive
        # repeated cleanup of the already canceled operation.
        self.name = 'ack_table'
        self.source = self.b.existing(self.name)
        self.b.objects[('sales', 'raw', self.name, 'tables')]['snapshots'] = []
        acknowledged = self.submit(key='separate-ack')
        self.assertTrue(acknowledged['committed'])
        with control_store.connect() as db:
            ack_row = dict(db.execute('SELECT * FROM dataops_operations WHERE id=?', (acknowledged['id'],)).fetchone())
        for _ in range(2): drop._settle(execution, 'FAILED', dict(phase='REJECTED'))
        self.assertTrue(owner.closed)
        self.assertFalse(unrelated_owner.closed)
        with self.assertRaises(QueryError): ds.queries._acquire_query_owner('dataops-' + unrelated.id)
        with ds.queries._acquire_query_owner('dataops-' + ident): pass
        with control_store.connect() as db:
            self.assertEqual(dict(db.execute('SELECT * FROM dataops_operations WHERE id=?', (ident,)).fetchone()), canceled)
            self.assertEqual(dict(db.execute('SELECT * FROM dataops_operations WHERE id=?', (acknowledged['id'],)).fetchone()), ack_row)
            self.assertIsNotNone(dm.get_object(db, source.object_id))
        self.assertEqual(self.deletes, [('raw', 'ack_table')])

    def test_public_inspect_auth_loss_no_best_cancel(self):
        self.behavior = lambda: (_ for _ in ()).throw(inv.TableDropTransportError(True))
        result = self.submit()
        self.b.fixture.users['alice']['role'] = 'bi_viewer';self.b.fixture.save()
        with patch('query_service._best_cancel', side_effect=AssertionError('cancel')), self.assertRaises((QueryError, PermissionError)):
            drop.inspect(ds.Execution(self.principal, result['id']), self.context)
        self.assertEqual(self.row()['state'], 'UNKNOWN')

    def test_transport_strict_204_status_classification_sanitization(self):
        import urllib.request
        req = urllib.request.Request('http://localhost/private', method='DELETE')
        for status in (204, 200, 301, 302, 307, 308, 400, 401, 403, 404, 408, 429, 499, 500, 503):
            opener = SimpleNamespace(open=lambda *a, **k: None)
            if status < 400:
                response = SimpleNamespace(status=status)
                cm = unittest.mock.MagicMock();cm.__enter__.return_value=response
                opener.open = unittest.mock.Mock(return_value=cm)
            else:
                opener.open = unittest.mock.Mock(side_effect=urllib.error.HTTPError(req.full_url, status, 'secret', {}, io.BytesIO(b'private')))
            with patch.object(urllib.request, 'build_opener', return_value=opener):
                if status == 204: self.assertEqual(inv._delete_table_http(req), 204)
                else:
                    with self.assertRaises(inv.TableDropTransportError) as error: inv._delete_table_http(req)
                    self.assertEqual(error.exception.possible_send, status not in (400,401,403,404))
                    self.assertNotIn('secret', str(error.exception))
                    self.assertEqual(error.exception.details,{})
                opener.open.assert_called_once_with(req,timeout=20)
        with patch.object(urllib.request,'build_opener',side_effect=OSError('private setup')):
            with self.assertRaises(inv.TableDropTransportError) as error:inv._delete_table_http(req)
            self.assertFalse(error.exception.possible_send)
        for failure in (TimeoutError('private timeout'),OSError('private connection')):
            with patch.object(urllib.request,'build_opener') as opener:
                opener.return_value.open.side_effect=failure
                with self.assertRaises(inv.TableDropTransportError) as error:inv._delete_table_http(req)
                self.assertTrue(error.exception.possible_send)

    def test_typed_transport_bound_path_purge_false_and_default_get(self):
        with control_store.connect() as db:
            database = dm.get_database(db, self.context.database_id)
            binding = dm.get_binding(db, database.id)
            schema = dm.get_schema(db, self.context.schema_id)
        entry = dict(service_ref='scoped-polaris', realm='SALES', catalog='remote/catalog',
            generation_id=binding.applied_generation_id, client_id='private-client', client_secret='private-secret')
        with patch.object(inv, '_credentials', return_value=entry), patch.object(inv.socket, 'socket') as sock, \
                patch.object(inv.socket, 'create_connection'), patch.object(inv.subprocess, 'Popen') as process, \
                patch.object(inv, '_http', return_value={'access_token': 'private-bearer'}) as http, \
                patch.object(inv, '_delete_table_http', return_value=204) as delete:
            sock.return_value.__enter__.return_value.getsockname.return_value = ('127.0.0.1', 12345)
            process.return_value.poll.return_value = None
            with CATALOG_API(database, binding, _table_drop=True) as (reader, unregister):
                self.assertEqual(unregister(schema, '大 A/"?%#'), 204)
            request = delete.call_args.args[0]
            self.assertEqual(request.method, 'DELETE')
            self.assertEqual(request.full_url, 'http://127.0.0.1:12345/api/catalog/v1/remote%2Fcatalog/namespaces/raw/tables/%E5%A4%A7%20A%2F%22%3F%25%23?purgeRequested=false')
            self.assertEqual(request.headers['Authorization'], 'Bearer private-bearer')
            delete.reset_mock()
            with CATALOG_API(database, binding) as reader:
                self.assertTrue(callable(reader));reader('/namespaces/raw/tables')
            self.assertEqual(http.call_args.args[0].get_method(), 'GET')
            delete.assert_not_called()

    def test_lost_worker_sent_absent_is_unknown_not_commit_or_retry(self):
        fingerprint = self.preview()['preview_fingerprint']
        self.behavior = lambda: (_ for _ in ()).throw(inv.TableDropTransportError(True))
        result = self.submit(fingerprint=fingerprint)
        self.b.objects.clear()
        ds._live.pop(result['id'])[2].close()
        with control_store.connect() as db:
            db.execute("UPDATE dataops_operations SET state='ACTIVE' WHERE id=?", (result['id'],))
        result = self.submit(fingerprint=fingerprint)
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertFalse(result['committed'])
        self.assertEqual(len(self.deletes), 1)

    def test_last_guard_revocation_before_delete_prevents_send(self):
        original = self.transport
        @contextmanager
        def revoked(database, binding):
            self.b.fixture.users['alice']['role'] = 'bi_viewer';self.b.fixture.save()
            with original(database, binding) as transport: yield transport
        with patch.object(inv, 'table_drop_api', revoked), self.assertRaises((QueryError, PermissionError)):
            self.submit()
        self.assertFalse(self.deletes)
        self.assertEqual(self.row()['state'], 'FAILED')

    def test_actual_both_handlers_strict_preview_submit_observe_aliases_replay(self):
        import warehouse_resident as wr
        import workbench_server
        import runtime_profile
        import request_auth
        handlers = []
        class Capture:
            def __init__(self, address, handler): handlers.append(handler)
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def handle_request(self): pass
        with ExitStack() as stack:
            for target, value in (('warehouse_resident._profile_active', True), ('logical_creations.enabled', False)):
                stack.enter_context(patch(target, return_value=value))
            stack.enter_context(patch('http.server.ThreadingHTTPServer', Capture))
            stack.enter_context(patch('database_creation_bridge.configure'))
            stack.enter_context(patch.object(wr, 'warehouse', return_value='LOCAL'))
            wr.serve_endpoint(once=True)
        class Connection:
            def __init__(self, incoming): self.incoming, self.outgoing = io.BytesIO(incoming), io.BytesIO()
            def makefile(self, *args): return self.incoming
            def sendall(self, data): self.outgoing.write(data)
        for index, handler in enumerate((handlers[0], workbench_server.WorkbenchHandler)):
            def request(method, path, body=None, key=None, extra='', raw_body=None, actor=None, role='admin'):
                payload = raw_body if raw_body is not None else json.dumps(body).encode() if body is not None else b''
                raw = (method+' '+path+' HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n'
                    'X-Shirokuma-Role: '+role+'\r\nContent-Type: application/json\r\nContent-Length: '+str(len(payload))+'\r\n'
                    + ('Idempotency-Key: '+key+'\r\n' if key else '')+extra+'\r\n').encode()+payload
                connection = Connection(raw)
                with patch.object(runtime_profile, 'active', return_value=True), patch.object(request_auth, 'authenticate_principal', return_value=actor or self.principal), patch.object(handler, 'log_message'):
                    handler(connection, ('127.0.0.1', 1), SimpleNamespace())
                response = HTTPResponse(Connection(connection.outgoing.getvalue()));response.begin()
                return response.status, json.loads(response.read())
            self.source = self.b.existing('wire'+str(index));self.name='wire'+str(index)
            self.b.objects[('sales','raw',self.name,'tables')]['snapshots']=[]
            context = asdict(self.context)
            lookup='/api/v1/table-drops/by-key?'+urlencode({'context':json.dumps(context),'key':'wire'+str(index)})
            with control_store.connect() as db:before=tuple(db.iterdump())
            live=dict(ds._live)
            status,reply=request('GET',lookup);self.assertEqual(status,404,reply)
            with control_store.connect() as db:self.assertEqual(tuple(db.iterdump()),before)
            self.assertEqual(ds._live,live)
            query = urlencode(dict(context=json.dumps(context), source=json.dumps(asdict(self.source))))
            status, reply = request('GET', '/api/v1/table-drops/preview?'+query)
            self.assertEqual(status, 200, reply)
            body = dict(context=context, source=asdict(self.source), mode=drop.MODE,
                confirm_name=self.name, expected_preview_fingerprint=reply['preview']['preview_fingerprint'])
            status, reply = request('POST', '/api/v1/table-drops', body, 'wire'+str(index))
            self.assertEqual(status, 200, reply);operation=reply['operation']
            status,reply=request('GET',lookup);self.assertEqual(status,200,reply)
            self.assertEqual(reply['operation']['id'],operation['id']);self.assertTrue(reply['operation']['committed'])
            self.assertNotIn('private',json.dumps(reply));self.assertNotIn('guard',json.dumps(reply))
            owner=self.b.fixture.principal('owner');owner_context=asdict(self.b.context(principal=owner,role='admin'))
            status,reply=request('GET','/api/v1/table-drops/by-key?'+urlencode({'context':json.dumps(owner_context),'key':'wire'+str(index)}),actor=owner)
            self.assertEqual((status,reply['code']),(404,'OPERATION_NOT_FOUND'))
            other=asdict(self.b.context(principal=self.principal,role='admin',schema='sandbox'))
            self.assertEqual(request('GET','/api/v1/table-drops/by-key?'+urlencode({'context':json.dumps(other),'key':'wire'+str(index)}))[0],409)
            engineer=asdict(replace(self.context,selected_role='data_engineer'))
            self.assertEqual(request('GET','/api/v1/table-drops/by-key?'+urlencode({'context':json.dumps(engineer),'key':'wire'+str(index)}),role='data_engineer')[0],403)
            for key in ('',' ','x'*257,'bad\nkey','bad\x00key'):
                self.assertEqual(request('GET','/api/v1/table-drops/by-key?'+urlencode({'context':json.dumps(context),'key':key}))[0],400)
            for url in (lookup+'&key=duplicate',lookup+'&unknown=x','/api/v1/table-drops/by-key?'+urlencode({'context':json.dumps(context)}),
                    lookup+'&context={}',lookup+'#fragment',
                    '/api/v1/table-drops/by-key?'+urlencode({'context':json.dumps(context)[:-1]+',"selected_role":"admin"}','key':'wire'+str(index)})):
                self.assertEqual(request('GET',url)[0],400)
            self.assertEqual(request('GET',lookup,body={})[0],400)
            self.assertEqual(request('GET',lookup,extra='Transfer-Encoding: chunked\r\n')[0],400)
            self.assertEqual(request('POST','/api/v1/table-drops/by-key',{'context':context})[0],404)
            for prefix in ('dataops', 'table-drops'):
                path = '/api/v1/'+prefix+'/'+operation['id']
                for method, url, payload in (('GET', path+'?'+urlencode({'context':json.dumps(context)}), None), ('POST', path+'/cancel', {'context':context})):
                    status, reply = request(method, url, payload)
                    self.assertEqual(status, 200, reply)
                    self.assertTrue(reply['operation']['committed'])
                    self.assertNotIn('private', json.dumps(reply))
            status, reply = request('POST', '/api/v1/table-drops', body, 'wire'+str(index))
            self.assertEqual(status, 200, reply)
            for method, path, payload, key, extra in (
                ('GET','/api/v1/table-drops/preview?'+query+'&source={}',None,None,''),
                ('GET','/api/v1/table-drops/preview?'+query,{},None,''),
                ('POST','/api/v1/table-drops',dict(body,sql='DROP TABLE x'),'bad',''),
                ('POST','/api/v1/table-drops',body,None,''),
                ('POST','/api/v1/table-drops',body,'bad','Idempotency-Key: duplicate\r\n'),
                ('POST','/api/v1/table-drops?x=1',body,'bad','')):
                status, reply = request(method,path,payload,key,extra)
                self.assertEqual(status, 400, reply)
            raw = json.dumps(body)[:-1]+',"mode":"'+drop.MODE+'"}'
            self.assertEqual(request('POST','/api/v1/table-drops',key='duplicate',raw_body=raw.encode())[0],400)
            query_context = urlencode({'context':json.dumps(context)[:-1]+',"selected_role":"admin"}', 'source':json.dumps(body['source'])})
            self.assertEqual(request('GET','/api/v1/table-drops/preview?'+query_context)[0],400)
            # Both real handlers must preserve SENT through every cancellation
            # alias while the actual worker is blocked in the typed transport.
            self.source = self.b.existing('inflight'+str(index));self.name='inflight'+str(index)
            self.b.objects[('sales','raw',self.name,'tables')]['snapshots']=[]
            fingerprint = self.preview()['preview_fingerprint']
            body.update(source=asdict(self.source),confirm_name=self.name,expected_preview_fingerprint=fingerprint)
            entered, release, finished = threading.Event(), threading.Event(), threading.Event()
            original_work = drop._work
            def blocked():
                entered.set();self.assertTrue(release.wait(5))
            def work(*args):
                try: original_work(*args)
                finally: finished.set()
            self.behavior=blocked
            try:
                with patch.object(drop.threading,'Thread',REAL_THREAD), patch.object(drop,'_work',work):
                    status, reply = request('POST','/api/v1/table-drops',body,'inflight'+str(index))
                    self.assertEqual(status,200,reply);self.assertTrue(entered.wait(5));operation=reply['operation']
                    for prefix in ('dataops','table-drops'):
                        status, reply = request('POST','/api/v1/'+prefix+'/'+operation['id']+'/cancel',{'context':context})
                        self.assertEqual(status,200,reply)
                        self.assertEqual(reply['operation']['state'],'ACTIVE')
                        self.assertEqual(reply['operation']['phase'],'SENT')
                    release.set();self.assertTrue(finished.wait(5))
            finally: release.set();self.behavior=lambda:None
            status, reply = request('GET','/api/v1/dataops/'+operation['id']+'?'+urlencode({'context':json.dumps(context)}))
            self.assertEqual(status,200,reply);self.assertTrue(reply['operation']['committed'])
            self.source=self.b.existing('unknown'+str(index));self.name='unknown'+str(index)
            self.b.objects[('sales','raw',self.name,'tables')]['snapshots']=[]
            body.update(source=asdict(self.source),confirm_name=self.name,expected_preview_fingerprint=self.preview()['preview_fingerprint'])
            self.behavior=lambda:(_ for _ in ()).throw(inv.TableDropTransportError(True))
            status,reply=request('POST','/api/v1/table-drops',body,'unknown'+str(index))
            self.assertEqual(status,200,reply);unknown=reply['operation'];self.assertEqual(unknown['state'],'UNKNOWN')
            for prefix in ('dataops','table-drops'):
                status,reply=request('POST','/api/v1/'+prefix+'/'+unknown['id']+'/cancel',{'context':context})
                self.assertEqual(status,200,reply);self.assertEqual(reply['operation']['state'],'UNKNOWN')
                self.assertFalse(reply['operation']['committed'])
            status,reply=request('POST','/api/v1/table-drops',dict(body,confirm_name='different'),'unknown'+str(index))
            self.assertEqual(status,409,reply)
            self.assertNotIn('private',json.dumps(reply));self.assertNotIn('guard',json.dumps(reply))
            self.behavior=lambda:None
        self.assertEqual(len(self.deletes), 6)


if __name__ == '__main__': unittest.main()
