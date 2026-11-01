"""Persisted projection, current authorization and bounded seek without backends."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import audit_service as audit
import control_store
import data_management as dm
import request_auth
from grant_fixtures import verified_grants
import test_request_auth as fixtures

SECRET = 'SECRET_SQL_RESULT_PATH_PASSWORD'
NOW = '2026-10-29T10:00:00Z'


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PrincipalTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.alice = self.f.principal()
        self.platform = self.f.principal('owner')
        self.sales, self.finance = self.f.sales, self.f.finance
        self.schemas = {}
        with control_store.connect() as db:
            for database in (self.sales, self.finance):
                for name in ('raw', 'marts', 'private'):
                    self.schemas[database, name] = dm.create_schema(db, database, name, name, name, 'test').id
            # A is a real database admin, distinct from P.
            self.f.users['admin'] = dict(self.f.users['owner'], platform_admin=False)
            dm.create_grant(db, 'admin', self.sales, 'admin')
            verified_grants(db)
        self.f.save()
        self.admin = self.f.principal('admin')
        self.query('q-own')
        self.query('q-other', actor='other')
        self.query('q-role', role='bi_viewer', schema='marts')
        self.query('q-private', schema='private')
        self.query('q-foreign', database=self.finance)

    def context(self, database=None, role='data_engineer', schema='raw'):
        database = database or self.sales
        return dict(database_id=database, schema_id=self.schemas[database, schema], warehouse_id='w',
                    selected_role=role, sql_catalog='sales', sql_schema=schema,
                    binding_revision=1, generation_id='g-' + database, grant_revision=2)

    def insert(self, table, **values):
        with control_store.connect() as db:
            db.execute('INSERT INTO ' + table + '(' + ','.join(values) + ') VALUES (' + ','.join('?' for _ in values) + ')',
                       tuple(json.dumps(v) if isinstance(v, (dict, list)) else v for v in values.values()))

    def query(self, identifier, *, database=None, actor='alice', role='data_engineer', schema='raw', created=NOW):
        c = self.context(database, role, schema)
        self.insert('query_records', id=identifier, actor=actor, database_id=c['database_id'], schema_id=c['schema_id'],
                    warehouse_id='w', generation_id=c['generation_id'], state='UNKNOWN', created_at=created)
        self.insert('query_execution_contexts', query_id=identifier, selected_role=role, mode='read-only',
                    sql_catalog='sales', sql_schema=schema, binding_revision=1, grant_revision=2,
                    backend_username=SECRET, credential_snapshot_ref=SECRET, sql=SECRET)

    def listing(self, principal=None, role='data_engineer', **kw):
        return audit.events(principal or self.alice, role, **(dict(database_id=self.sales) | kw))

    def error(self, call, code='NOT_FOUND'):
        with self.assertRaises(audit.QueryError) as raised:
            call()
        self.assertEqual(raised.exception.code, code)

    def test_self_saved_role_schema_and_database_admin(self):
        result = self.listing()
        self.assertEqual([r['source_id'] for r in result['items']], ['q-own'])
        self.assertEqual(result['coverage']['kind'], 'latest_persisted_summary')
        viewer = self.listing(role='bi_viewer')
        self.assertEqual([r['source_id'] for r in viewer['items']], ['q-role'])
        rows = self.listing(self.admin, 'admin', scope='database')['items']
        self.assertEqual({r['source_id'] for r in rows if r['source'] == 'query'}, {'q-own', 'q-other', 'q-role', 'q-private'})
        self.assertNotIn(SECRET, json.dumps(rows))
        self.error(lambda: self.listing(scope='database'))
        self.error(lambda: self.listing(self.admin, 'admin', scope='platform'))
        for db in (self.finance, 'unknown'):
            self.error(lambda: self.listing(database_id=db))
        foreign = self.listing(self.platform, 'admin', database_id=self.finance, scope='database')['items'][0]['id']
        self.error(lambda: self.listing(event_id=foreign))
        own = result['items'][0]
        self.assertEqual(self.listing(event_id=own['id'])['event'], own)
        self.assertIsNone(own['effective_role'])

    def dataops(self, identifier='d', **changes):
        self.insert('dataops_operations', **(dict(id=identifier, actor='alice', route='ingest', idempotency_key=identifier,
            payload=SECRET, fingerprint=SECRET, context=self.context(), backend_username=SECRET,
            credential_snapshot_ref=SECRET, state='UNKNOWN', committed=1, result=SECRET,
            cleanup_error=SECRET, created_at=NOW) | changes))

    def test_dbt_metadata_redaction_scope_and_authorized_links(self):
        def record(identifier, kind='run', actor='alice', database=None, role='data_engineer', **extra):
            self.insert('dbt_runs', id=identifier, database_id=database or self.sales,
                record=dict(id=identifier, kind=kind, actor=actor, context=self.context(database, role),
                    state='READY', project_id='lake_marts', source_digest='safe-digest',
                    created_at=NOW, updated_at=NOW, error_code=None,
                    compiled_sql=SECRET, project_path=SECRET, credential=SECRET, raw_log=SECRET, **extra))
        record('plan', 'plan')
        record('run', plan_id='plan')
        record('pub', 'publication', actor='admin', role='admin', parent_run_id='run', plan_id='plan')
        record('other', actor='other')
        record('viewer-forged', role='bi_viewer')
        record('foreign', database=self.finance)
        self.dataops('child')
        self.dataops('hidden-child', actor='other')
        self.insert('dbt_run_operations', run_id='run', phase='test', node_id='test.orders', operation_id='child')
        self.insert('dbt_run_operations', run_id='run', phase='build', node_id='model.orders', operation_id='hidden-child')
        rows = self.listing(source='dbt')['items']
        self.assertEqual({r['source_id'] for r in rows}, {'plan', 'run'})
        run = next(r for r in rows if r['source_id'] == 'run')
        self.assertEqual(run['kind'], 'run')
        self.assertEqual(run['project_id'], 'lake_marts')
        self.assertEqual(run['source_digest'], 'safe-digest')
        self.assertEqual({l['source_id'] for l in run['links'] if l['event_id']}, {'plan', 'child'})
        self.assertNotIn(SECRET, json.dumps(rows))
        self.assertEqual(self.listing(role='bi_viewer', source='dbt')['items'], [])
        admin = self.listing(self.admin, 'admin', scope='database', source='dbt')['items']
        self.assertEqual({r['source_id'] for r in admin}, {'plan', 'run', 'pub', 'other', 'viewer-forged'})
        pub = next(r for r in admin if r['source_id'] == 'pub')
        self.assertEqual({l['source_id'] for l in pub['links'] if l['event_id']}, {'plan', 'run'})
        self.assertNotIn(SECRET, json.dumps(admin))
        self.error(lambda: self.listing(event_id=pub['id']))
        child = self.listing(source='dataops')['items']
        child = next(r for r in child if r['source_id'] == 'child')
        self.assertIn('run', {l['source_id'] for l in child['links'] if l['source'] == 'dbt'})

    def test_unknown_committed_cleanup_no_mutation_or_recovery(self):
        import dataops_service
        import db_stages
        import db_stage_scans
        import db_marts
        import database_creations
        self.dataops()
        path = self.f.root / 'state/jobs.sqlite3'
        before = path.read_bytes()
        with patch.object(dataops_service.Execution, 'inspect', side_effect=AssertionError('recovery')), \
             patch.object(db_stages, 'get_run', side_effect=AssertionError('stage observation')):
            result = self.listing(source='dataops')['items'][0]
        self.assertEqual((result['state'], result['committed'], result['has_cleanup_error']), ('UNKNOWN', True, True))
        self.assertEqual(path.read_bytes(), before)
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertEqual(self.listing(search=SECRET)['items'], [])
        with audit.snapshot() as db:
            self.assertEqual(db.execute('PRAGMA query_only').fetchone()[0], 1)
            with self.assertRaises(Exception): db.execute("UPDATE dataops_operations SET state='FAILED'")

    def test_signed_cursor_binding_ties_inserts_and_latest_state(self):
        for n in range(5): self.query('q-tie-' + str(n))
        first = self.listing(limit=2)
        cursor = first['next_cursor']
        self.assertTrue(cursor)
        self.assertIsNone(audit._untoken(first['page_cursor'])['last'])
        for change in (dict(limit=3), dict(search='q'), dict(state='UNKNOWN'), dict(source='query'),
                       dict(scope='database'), dict(database_id=self.finance), dict(**{'from': NOW})):
            # Ordinary unauthorized contexts stay non-disclosing; authorized changed filters are stale.
            expected = 'NOT_FOUND' if change.get('scope') == 'database' or change.get('database_id') else 'STALE_CURSOR'
            self.error(lambda: self.listing(cursor=cursor, **(dict(limit=2) | change)), expected)
        self.error(lambda: self.listing(cursor=cursor[:-2] + 'xx', limit=2), 'STALE_CURSOR')
        self.query('q-new-later', created='2026-10-30T00:00:00Z')
        self.query('q-a-new-same-time')
        self.query('q-a-backdated', created='2026-10-28T00:00:00Z')
        reread = self.listing(limit=2, cursor=first['page_cursor'])
        self.assertEqual(reread['items'], first['items'])
        self.assertEqual(reread['page_cursor'], first['page_cursor'])
        with control_store.connect() as db: db.execute("UPDATE query_records SET state='FINISHED' WHERE id='q-own'")
        seen = [r['source_id'] for r in first['items']]
        while cursor:
            page = self.listing(cursor=cursor, limit=2)
            self.assertEqual(self.listing(cursor=page['page_cursor'], limit=2)['items'], page['items'])
            self.assertEqual(audit._untoken(page['page_cursor'])['ceilings'], audit._untoken(first['page_cursor'])['ceilings'])
            seen.extend(r['source_id'] for r in page['items'])
            cursor = page['next_cursor']
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(set(seen), {'q-own', *('q-tie-' + str(n) for n in range(5))})
        with patch.object(audit, '_KEY', b'restarted'):
            self.error(lambda: self.listing(limit=2, cursor=first['next_cursor']), 'STALE_CURSOR')
        with control_store.connect() as db:
            db.execute("UPDATE effective_grants SET epoch=epoch+1 WHERE principal_id='alice' AND database_id=?", (self.sales,))
        self.error(lambda: self.listing(limit=2, cursor=first['next_cursor']), 'STALE_CURSOR')

    def test_page_cursor_empty_single_page_and_unsigned_start_rejected(self):
        first = self.listing(search='absent')
        self.assertTrue(first['page_cursor'])
        self.assertIsNone(first['next_cursor'])
        self.query('absent')
        self.assertEqual(self.listing(search='absent', cursor=first['page_cursor'])['items'], [])
        single = self.listing(source='query', search='q-own')
        self.assertIsNone(single['next_cursor'])
        self.assertEqual(self.listing(source='query', search='q-own', cursor=single['page_cursor'])['items'], single['items'])
        token = audit._untoken(single['page_cursor'])
        token['ceilings']['query_records'] += 100
        self.error(lambda: self.listing(source='query', search='q-own', cursor=audit._encode(audit._json(token).encode())), 'STALE_CURSOR')
        self.error(lambda: self.listing(source='query', search='q-own', cursor=''), 'STALE_CURSOR')

    def test_context_current_authorized_stamps_and_platform_filter(self):
        result = self.listing()
        with audit.snapshot() as db:
            binding = dm.get_binding(db, self.sales)
            decision = request_auth.authorize_database(self.alice, self.sales, 'data_engineer',
                                                       expected_generation_id=binding.applied_generation_id, db=db)
        expected = dict(scope='self', principal_id='alice', selected_role='data_engineer', database_id=self.sales,
                        binding_revision=binding.desired_revision, generation_id=decision.generation_id,
                        grant_revision=decision.grant_revision, platform_admin=False)
        self.assertEqual({k:v for k,v in result['context'].items() if k != 'schema_ids'}, expected)
        self.assertEqual(self.listing(event_id=result['items'][0]['id'])['context'], result['context'])
        for database in (None, self.sales):
            context = self.listing(self.platform, 'admin', scope='platform', database_id=database)['context']
            self.assertEqual(context, dict(scope='platform', principal_id='owner', selected_role='admin',
                database_id=database, binding_revision=None, generation_id=None, grant_revision=None, platform_admin=True))
        self.assertFalse(self.listing(self.admin, 'admin', scope='database')['context']['platform_admin'])
        cursor = result['page_cursor']
        self.f.users['alice']['role'] = 'admin'
        self.f.save()
        self.error(lambda: self.listing(cursor=cursor), 'STALE_CURSOR')

    def test_page_cursor_preserves_peer_insert_ceilings(self):
        self.dataops()
        first = self.listing(source='dataops')
        self.query('q-inserted-peer')
        self.insert('dataops_queries', operation_id='d', step='inserted', query_id='q-inserted-peer', action='read')
        self.assertEqual(self.listing(source='dataops', cursor=first['page_cursor'])['items'], first['items'])
        current = self.listing(event_id=first['items'][0]['id'])['event']
        self.assertIn('q-inserted-peer', {l['source_id'] for l in current['links']})

    def test_final_live_fence_account_grant_binding_platform_loss(self):
        original = audit._fence
        self.insert('operations', id='fence-create', actor='owner', action='DATABASE_CREATE', targets=[],
                    state='UNKNOWN', query_ids=[], before_after=[], timestamps=[])
        self.insert('database_create_requests', actor='owner', route='create', idempotency_key='fence', payload=SECRET,
                    database_id=self.sales, operation_id='fence-create')
        cases = ('account', 'grant', 'binding', 'platform')
        for case in cases:
            with self.subTest(case=case):
                user = self.platform if case == 'platform' else self.alice
                role = 'admin' if case == 'platform' else 'data_engineer'
                detail = (self.listing(self.platform, 'admin', scope='platform')['items'][0]['id']
                          if case == 'platform' else self.listing()['items'][0]['id'])
                def revoke(*args):
                    if case in ('account', 'platform'):
                        if case == 'platform':
                            self.f.users['owner']['platform_admin'] = False
                        else:
                            self.f.users['alice']['disabled'] = True
                        self.f.save()
                    else:
                        with control_store.connect() as db:
                            if case == 'grant': db.execute("UPDATE effective_grants SET epoch=epoch+1 WHERE principal_id='alice'")
                            else: db.execute('UPDATE catalog_bindings SET revision=revision+1 WHERE database_id=?', (self.sales,))
                    return original(*args)
                for identifier in (None, detail) if detail else (None,):
                    with patch.object(audit, '_fence', side_effect=revoke):
                        self.error(lambda: self.listing(user, role, scope='platform' if case == 'platform' else 'self', event_id=identifier))
                    self.f.users['alice'].pop('disabled', None)
                    self.f.users['owner'].pop('disabled', None)
                    self.f.users['owner']['platform_admin'] = True
                    self.f.save()

    def test_management_typed_joins_retired_and_list_timestamps(self):
        for ident, action in (('op-display', 'DISPLAY_METADATA_CHANGE'), ('op-create', 'DATABASE_CREATE'),
                              ('op-retire', 'DATABASE_RETIRE'), ('op-config', 'WAREHOUSE_CONFIG_APPLY'),
                              ('op-grant', 'GRANT_CHANGE'), ('op-apply', 'WAREHOUSE_CONFIG_APPLY')):
            self.insert('operations', id=ident, actor='owner', action=action, targets=[], state='UNKNOWN',
                        query_ids=[], before_after=[[SECRET, SECRET]], timestamps=[['created_at', NOW]])
        self.insert('metadata_requests', operation_id='op-display', actor='owner', route='display', idempotency_key='x', payload=SECRET, response=SECRET)
        self.insert('metadata_audit', operation_id='op-display', resource_type='schema',
                    resource_id=self.schemas[self.sales, 'raw'], before_metadata=SECRET, after_metadata=SECRET)
        self.insert('database_create_requests', actor='owner', route='create', idempotency_key='x', payload=SECRET,
                    database_id=self.sales, operation_id='op-create')
        self.insert('database_retirements', operation_id='op-retire', database_id=self.sales, revision=1, state='RETIRED', stopped_schedule_ids=[])
        self.insert('grant_audit', operation_id='op-grant', apply_operation_id='op-apply', actor='owner', principal_id='alice', database_id=self.sales,
                    before_role='revoked', after_role='admin', effective_role='admin', expected_revision=1,
                    desired_revision=2, effective_epoch=2, receipt=SECRET)
        rows = self.listing(self.admin, 'admin', scope='database', source='management')['items']
        self.assertEqual({r['source_id'] for r in rows if r['source_id'].startswith('op-')}, {'op-display', 'op-create', 'op-retire', 'op-grant'})
        self.assertTrue(all(r['created_at'] == NOW for r in rows if r['source_id'].startswith('op-')))
        self.assertTrue(all(r['saved_role'] is None for r in rows))
        grant = next(r for r in rows if r['source_id'] == 'op-grant')
        self.assertFalse(any(l['relation'] == 'apply_operation' for l in grant['links']))
        self.assertIn(dict(relation='target_principal', source='principal', source_id='alice', event_id=None), grant['links'])
        display = next(r for r in rows if r['source_id'] == 'op-display')
        self.assertIn(dict(relation='display_resource', source='schema', source_id=self.schemas[self.sales, 'raw'], event_id=None), display['links'])
        retire = next(r for r in rows if r['source_id'] == 'op-retire')
        self.assertIn(dict(relation='retired_database', source='database', source_id=self.sales, event_id=None), retire['links'])
        with control_store.connect() as db: db.execute("UPDATE databases SET state='retired' WHERE id=?", (self.sales,))
        self.error(lambda: self.listing())
        self.error(lambda: self.listing(self.admin, 'admin', scope='database'))
        p = self.listing(self.platform, 'admin', scope='platform', database_id=None)
        self.assertEqual({r['source_id'] for r in p['items'] if r['source_id'].startswith('op-')},
                         {'op-display', 'op-create', 'op-retire', 'op-grant', 'op-config', 'op-apply'})
        self.assertNotIn(SECRET, json.dumps(p))

    def test_shared_grant_apply_is_platform_only_with_scope_checked_links(self):
        with control_store.connect() as db:
            dm.create_grant(db, 'admin', self.finance, 'admin')
            verified_grants(db)
        for identifier, action in (('shared-apply', 'WAREHOUSE_CONFIG_APPLY'),
                                   ('grant-sales', 'GRANT_CHANGE'), ('grant-finance', 'GRANT_CHANGE')):
            self.insert('operations', id=identifier, actor='owner', action=action, targets=[], state='APPLIED',
                        query_ids=[], before_after=[[SECRET, SECRET]], timestamps=[['created_at', NOW]])
        for database, operation in ((self.sales, 'grant-sales'), (self.finance, 'grant-finance')):
            self.insert('grant_audit', operation_id=operation, apply_operation_id='shared-apply', actor='owner',
                        principal_id='alice', database_id=database, before_role='revoked', after_role='admin',
                        effective_role='admin', expected_revision=1, desired_revision=2, effective_epoch=2, receipt=SECRET)
        before = (self.f.root / 'state/jobs.sqlite3').read_bytes()
        platform = self.listing(self.platform, 'admin', scope='platform', database_id=None, limit=100)
        shared = next(r for r in platform['items'] if r['source_id'] == 'shared-apply')
        self.assertIsNone(shared['database_id'])
        grants = [r for r in platform['items'] if r['source_id'] in ('grant-sales', 'grant-finance')]
        self.assertEqual({l['source_id'] for l in shared['links'] if l['relation'] == 'grant_operation'},
                         {'grant-sales', 'grant-finance'})
        for grant in grants:
            self.assertIn(dict(relation='apply_operation', source='management', source_id='shared-apply',
                               event_id=shared['id']), grant['links'])
        self.assertEqual(self.listing(self.platform, 'admin', scope='platform', database_id=None,
                                     event_id=shared['id'])['event'], shared)
        for link in shared['links']:
            if link['event_id']:
                self.assertEqual(self.listing(self.platform, 'admin', scope='platform', database_id=None,
                                             event_id=link['event_id'])['event']['source_id'], link['source_id'])
        for database in (self.sales, self.finance):
            scoped = self.listing(self.admin, 'admin', scope='database', database_id=database, limit=100)
            self.assertNotIn('shared-apply', json.dumps(scoped))
            self.assertTrue(any(r['source_id'] in ('grant-sales', 'grant-finance') for r in scoped['items']))
            self.error(lambda: self.listing(self.admin, 'admin', scope='database', database_id=database, event_id=shared['id']))
            filtered = self.listing(self.platform, 'admin', scope='platform', database_id=database, limit=100)
            self.assertNotIn('shared-apply', json.dumps(filtered))
            self.error(lambda: self.listing(self.platform, 'admin', scope='platform', database_id=database, event_id=shared['id']))
        self.assertNotIn(SECRET, json.dumps(platform))
        self.assertEqual((self.f.root / 'state/jobs.sqlite3').read_bytes(), before)

    def test_mart_and_stage_scan_child_operations_respect_current_visibility(self):
        c = self.context()
        record = dict(context=c, owner='alice', status='unknown', definition=SECRET, result=SECRET)
        self.insert('warehouse_controls', warehouse_id='w', state='OPEN', revision=1)
        self.insert('db_stages', id='linked-stage', database_id=self.sales, owner='alice', selected_role='data_engineer',
                    name='linked-stage', directory=SECRET, revision=1, context=c, definition=SECRET, created_at=NOW, updated_at=NOW)
        self.insert('warehouse_admissions', id='linked-scan-admission', warehouse_id='w', actor='alice',
                    kind='stage', subject_id='linked-scan', state='UNKNOWN', created_at=NOW)
        self.insert('db_stage_scans', id='linked-scan', stage_id='linked-stage', record=record,
                    definition=SECRET, admission_id='linked-scan-admission')
        self.insert('db_marts', id='linked-mart', database_id=self.sales, owner='alice', revision=1,
                    context=c, definition=SECRET, record=record)
        self.insert('warehouse_admissions', id='linked-mart-admission', warehouse_id='w', actor='alice',
                    kind='mart', subject_id='linked-mart-run', state='UNKNOWN', created_at=NOW)
        self.insert('db_mart_runs', id='linked-mart-run', mart_id='linked-mart', record=record, admission_id='linked-mart-admission')
        for source in ('mart_run', 'stage_scan'):
            for position, (suffix, changes) in enumerate((('visible', {}), ('actor', {'actor':'other'}),
                    ('role', {'context':self.context(role='bi_viewer', schema='marts')}),
                    ('schema', {'context':self.context(schema='private')}),
                    ('foreign', {'context':self.context(self.finance)}))):
                operation = source + '-' + suffix
                self.dataops(operation, **changes)
                if source == 'mart_run':
                    self.insert('db_mart_operations', run_id='linked-mart-run', phase=suffix, operation_id=operation)
                else:
                    item = 'scan-item-' + suffix
                    self.insert('db_stage_scan_items', id=item, scan_id='linked-scan', position=position,
                                record={'private':SECRET}, input=b'secret', content_key=item)
                    self.insert('db_stage_scan_operations', scan_id='linked-scan', item_id=item,
                                attempt=1, operation_id=operation)
        before = (self.f.root / 'state/jobs.sqlite3').read_bytes()
        for source in ('mart_run', 'stage_scan'):
            event = self.listing(source=source)['items'][0]
            children = [l for l in event['links'] if l['source'] == 'dataops']
            self.assertEqual([l['source_id'] for l in children], [source + '-visible'])
            self.assertEqual(children[0]['relation'], 'operation')
            self.assertEqual(self.listing(event_id=children[0]['event_id'])['event']['source_id'], source + '-visible')
            self.assertEqual(self.listing(event_id=event['id'])['event'], event)
            admin = self.listing(self.admin, 'admin', scope='database', source=source)['items'][0]
            self.assertEqual({l['source_id'] for l in admin['links'] if l['source'] == 'dataops'},
                             {source + '-' + suffix for suffix in ('visible', 'actor', 'role', 'schema')})
            self.assertNotIn(SECRET, json.dumps(event))
            self.assertNotIn(source + '-foreign', json.dumps(admin))
        self.assertEqual((self.f.root / 'state/jobs.sqlite3').read_bytes(), before)
        for source in ('mart_run', 'stage_scan'):
            event = self.listing(source=source)['items'][0]
            with control_store.connect() as db:
                db.execute("UPDATE dataops_operations SET actor='other' WHERE id=?", (source + '-visible',))
            self.assertFalse(any(l['source'] == 'dataops' for l in self.listing(event_id=event['id'])['event']['links']))

    def test_all_sources_fixed_paths_redacted_and_wap_untimed(self):
        c = self.context()
        record = dict(context=c, owner='alice', actor='alice', status='unknown', committed=True,
                      created_at=NOW, definition=SECRET, sql=SECRET, args=SECRET, result=SECRET,
                      input=SECRET, cleanup_error=SECRET, directory=SECRET)
        self.dataops()
        self.insert('db_jobs', id='job', database_id=self.sales, name='same', owner='alice', selected_role='data_engineer',
                    revision=1, context=c, definition=SECRET, created_at=NOW, updated_at=NOW)
        self.insert('db_job_runs', id='run', job_id='job', owner='alice', selected_role='data_engineer', revision=1,
                    context=c, definition=SECRET, arguments=SECRET, inputs=SECRET, backend_username=SECRET,
                    credential_snapshot_ref=SECRET, status='unknown', created_at=NOW)
        self.insert('db_job_steps', run_id='run', position=1, name='step', action='sql', status='unknown', summary=SECRET)
        self.insert('db_stages', id='stage', database_id=self.sales, owner='alice', selected_role='data_engineer', name='same',
                    directory=SECRET, revision=1, context=c, definition=SECRET, created_at=NOW, updated_at=NOW)
        self.insert('db_stage_runs', id='stage-run', stage_id='stage', operation_id='d', record=record,
                    definition=SECRET, config_fingerprint=SECRET, input=b'secret')
        self.insert('db_stage_scans', id='scan', stage_id='stage',
                    record={k:v for k,v in record.items() if k != 'created_at'}, definition=SECRET, admission_id='adm')
        self.insert('db_marts', id='mart', database_id=self.sales, owner='alice', revision=1, context=c, definition=SECRET, record=record)
        self.insert('warehouse_controls', warehouse_id='w', state='OPEN', revision=1)
        self.insert('warehouse_admissions', id='adm', warehouse_id='w', actor='alice', kind='mart', subject_id='mart-run', state='UNKNOWN', created_at=NOW)
        self.insert('db_mart_runs', id='mart-run', mart_id='mart', record=record, admission_id='adm')
        self.insert('dbt_runs', id='dbt-run', database_id=self.sales, record=dict(record, kind='run', state='UNKNOWN'))
        self.insert('db_schedules', id='sched', database_id=self.sales, owner='alice', selected_role='data_engineer',
                    auth_mode='shared', context=c, revision=1, target_kind='job', target_id='job', target_revision=1,
                    spec=SECRET, intent=SECRET, enabled=1, last_status='unknown')
        self.insert('db_schedule_ticks', id='tick', schedule_id='sched', schedule_revision=1, target_revision=1,
                    scheduled_for=NOW, status='unknown', context=c, auth_mode='shared')
        self.insert('db_alerts', id='alert', subject='x', database_id=self.sales, owner='alice', selected_role='data_engineer',
                    context=c, job_id='job', job_revision=1, step_position=1, step_name='step', arguments=SECRET,
                    first_run_id='run', last_run_id='run', occurrences=1, first_seen_at=NOW, last_seen_at=NOW)
        self.insert('wap_models', database_id=self.sales, model_id='same', version=1)
        self.insert('wap_model_versions', database_id=self.sales, model_id='same', version=1, actor='alice', record=record)
        for table in ('wap_builds', 'wap_audits', 'wap_publications'):
            self.insert(table, id=table, database_id=self.sales, model_id='same', version=1, operation_id='d', record=record)
        self.insert('db_job_operations', run_id='run', position=1, operation_id='d')
        self.insert('dataops_queries', operation_id='d', step='query', query_id='q-own', action='read')
        ai_context = c | {'warehouse_id': 'LOCAL'}
        self.insert('ai_explanations', id='ai', actor='alice', idempotency_key='ai',
                    payload={'sql': SECRET}, pins={'private': SECRET}, context=ai_context,
                    record=dict(id='ai', actor='alice', context=ai_context, kind='sql_explanation',
                                state='SUCCEEDED', created_at=NOW, updated_at=NOW,
                                input={'sql': SECRET}, output=SECRET))
        path = self.f.root / 'state/jobs.sqlite3'
        before = path.read_bytes()
        result = self.listing(limit=100)
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertEqual({r['source'] for r in result['items']}, set(audit.SOURCES) - set(audit.MANAGEMENT))
        model = next(r for r in result['items'] if r['source'] == 'wap_model_version')
        self.assertIsNone(model['created_at'])
        timed = self.listing(limit=100, **{'from': '2026-10-29T19:00:00+09:00', 'to': NOW})
        self.assertNotIn('wap_model_version', {r['source'] for r in timed['items']})
        scan = next(r for r in timed['items'] if r['source'] == 'stage_scan')
        self.assertEqual(scan['created_at'], NOW)
        self.assertEqual(timed['filters']['from'], '2026-10-29T10:00:00.000000Z')
        for row in result['items']:
            self.assertEqual(self.listing(event_id=row['id'])['event'], row)
        step = next(r for r in result['items'] if r['source'] == 'job_step')
        self.assertIsNone(step['created_at'])
        self.assertIn(('job_run', 'run'), {(l['source'], l['source_id']) for l in step['links']})
        self.assertIn(('dataops', 'd'), {(l['source'], l['source_id']) for l in step['links']})
        operation = next(r for r in result['items'] if r['source'] == 'dataops')
        self.assertTrue({'query', 'job_run', 'job_step', 'wap_model_version', 'wap_build', 'wap_audit', 'wap_publication'} <=
                        {l['source'] for l in operation['links']})
        for row in result['items']:
            for link in row['links']:
                self.assertEqual(set(link), {'relation', 'source', 'source_id', 'event_id'})
                if link['event_id']:
                    peer = self.listing(event_id=link['event_id'])['event']
                    self.assertEqual((peer['source'], peer['source_id']), (link['source'], link['source_id']))
        self.assertEqual(path.read_bytes(), before)
        # Related metadata must itself be visible under self's saved role.
        with control_store.connect() as db:
            db.execute("UPDATE dataops_operations SET actor='other' WHERE id='d'")
        self.assertFalse(any(l['source'] == 'dataops' for l in self.listing(source='stage_run')['items'][0]['links']))
        self.assertFalse(any(l['source'] == 'dataops' for l in self.listing(source='query')['items'][0]['links']))

    def test_sql_visibility_and_search_precede_bounded_page(self):
        c = self.context()
        with control_store.connect() as db:
            for n in range(125):
                identifier = 'hidden-' + str(n)
                db.execute('INSERT INTO query_records VALUES (?,?,?,?,?,?,?,?)',
                    (identifier, 'other', self.sales, c['schema_id'], 'w', c['generation_id'], 'FINISHED', '2026-10-30T00:00:00Z'))
        result = self.listing(limit=1, source='query', search='q-own', state='UNKNOWN')
        self.assertEqual([r['source_id'] for r in result['items']], ['q-own'])
        self.assertIsNone(result['next_cursor'])
        self.assertEqual(self.listing(search='%')['items'], [])
        self.assertEqual(self.listing(search='_')['items'], [])

    def test_creation_root_requests_and_empty_operation_time(self):
        self.insert('operations', id='create', actor='owner', action='DATABASE_CREATE', targets=[],
                    state='UNKNOWN', query_ids=[], before_after=[], timestamps=[])
        self.insert('db_creations', id='root', environment_id='env', warehouse_id='w', actor='owner',
                    enrollment_id='enrollment', enrollment_revision=1, database_id=self.sales, create_operation_id='create',
                    intent_json={'sql':SECRET}, child_ids_json={'private':SECRET}, revision=1, reservation_active=1,
                    created_at=NOW, updated_at=NOW, terminal_outcome='READY')
        self.insert('db_creation_requests', id='request', environment_id='env', actor='owner', route='create',
                    idempotency_key='key', payload=SECRET, creation_id='root', action='CREATE', ack=SECRET,
                    dispatch_state='UNKNOWN', created_at=NOW, updated_at=NOW)
        result = self.listing(self.platform, 'admin', scope='platform')
        root = next(r for r in result['items'] if r['source_id'] == 'root')
        self.assertEqual(root['state'], 'READY')
        request = next(r for r in result['items'] if r['source_id'] == 'request')
        self.assertIn(dict(relation='database_creation', source='database_creation', source_id='root', event_id=root['id']), request['links'])
        operation = next(r for r in result['items'] if r['source_id'] == 'create')
        self.assertIsNone(operation['created_at'])
        self.assertNotIn(SECRET, json.dumps(result))
        timed = self.listing(self.platform, 'admin', scope='platform', **{'from': NOW})
        self.assertNotIn('create', {r['source_id'] for r in timed['items']})

    def test_logical_creation_nonterminal_and_real_requests_metadata_only(self):
        self.insert('operations', id='create-logical', actor='owner', action='DATABASE_CREATE', targets=[],
                    state='UNKNOWN', query_ids=[], before_after=[], timestamps=[])
        self.insert('db_creations', id='logical-root', environment_id='env', warehouse_id='w', actor='owner',
                    enrollment_id='enrollment', enrollment_revision=1, database_id=self.sales,
                    create_operation_id='create-logical', intent_json={'private':SECRET}, child_ids_json={'private':SECRET},
                    revision=2, reservation_active=1, creation_mode='logical_v1', logical_state='CREATING',
                    created_at=NOW, updated_at=NOW)
        for action in ('CREATE', 'RESUME', 'RECONCILE', 'WITHDRAW', 'AUTH_REFRESH'):
            self.insert('logical_requests', id='logical-' + action, creation_id='logical-root', environment_id='env',
                actor='owner', route='logical/' + action, idempotency_key=action, payload={'private':SECRET}, ack={'private':SECRET},
                action=action, dispatch_state='UNKNOWN', executor_id=SECRET, owner_id=SECRET, claim_id=SECRET,
                requester_gate_revision=3, executor_gate_revision=4, pins_json={'private':SECRET}, result_json={'private':SECRET},
                created_at=NOW, updated_at=NOW)
        for state in ('CREATING', 'VERIFYING', 'BLOCKED', 'UNKNOWN'):
            with control_store.connect() as db:
                db.execute('UPDATE db_creations SET logical_state=? WHERE id=?', (state, 'logical-root'))
            before = (self.f.root / 'state/jobs.sqlite3').read_bytes()
            result = self.listing(self.platform, 'admin', scope='platform', database_id=None, limit=100)
            root = next(r for r in result['items'] if r['source_id'] == 'logical-root')
            self.assertEqual(root['state'], state)
            requests = [r for r in result['items'] if r['source'] == 'logical_request']
            self.assertEqual({r['action'] for r in requests}, {'CREATE', 'RESUME', 'RECONCILE', 'WITHDRAW', 'AUTH_REFRESH'})
            for row in requests:
                self.assertEqual((row['actor'], row['state'], row['created_at'], row['revision']), ('owner', 'UNKNOWN', NOW, None))
                self.assertIn(dict(relation='database_creation', source='database_creation', source_id='logical-root', event_id=root['id']), row['links'])
                detail = self.listing(self.platform, 'admin', scope='platform', database_id=None, event_id=row['id'])
                self.assertEqual(detail['event'], row)
                self.assertNotIn('page_cursor', detail)
            self.assertIn(('management', 'create-logical'), {(l['source'], l['source_id']) for l in root['links']})
            self.assertNotIn(SECRET, json.dumps(result))
            self.assertEqual(self.listing(self.platform, 'admin', scope='platform', database_id=None, search=SECRET)['items'], [])
            self.assertEqual(self.listing(self.platform, 'admin', scope='platform', database_id=self.finance, source='logical_request')['items'], [])
            self.assertEqual((self.f.root / 'state/jobs.sqlite3').read_bytes(), before)

    def test_links_foreign_database_hidden_schema_actor_and_bounded_detail(self):
        self.dataops()
        for step, query in enumerate(('q-own', 'q-other', 'q-private', 'q-foreign', 'q-role')):
            self.insert('dataops_queries', operation_id='d', step=str(step), query_id=query, action='read')
        event = self.listing(source='dataops')['items'][0]
        self.assertEqual({l['source_id'] for l in event['links'] if l['source'] == 'query'}, {'q-own'})
        admin = self.listing(self.admin, 'admin', scope='database', source='dataops')['items'][0]
        self.assertEqual({l['source_id'] for l in admin['links'] if l['source'] == 'query'}, {'q-own', 'q-other', 'q-private', 'q-role'})
        with control_store.connect() as db:
            db.execute("UPDATE query_records SET actor='other' WHERE id='q-own'")
        self.assertFalse(any(l['source'] == 'query' for l in self.listing(event_id=event['id'])['event']['links']))
        for n in range(30):
            self.query('q-bounded-' + str(n))
            self.insert('dataops_queries', operation_id='d', step='bounded-' + str(n), query_id='q-bounded-' + str(n), action='read')
        detail = self.listing(event_id=event['id'])['event']
        self.assertLessEqual(len(detail['links']), audit.MAX_LINKS)
        self.assertNotIn('q-foreign', json.dumps(detail))
        self.assertNotIn(SECRET, json.dumps(detail))

    def test_validation_dates_limits_missing_store_and_caps(self):
        for values in (dict(limit=0), dict(limit=101), dict(limit=True), dict(source='unknown'), dict(search='x'*257),
                       {'from': '2026-02-30T00:00:00Z'}, {'from': '2026-10-29'}, {'from': '2026-10-29T10:00:00'},
                       {'from': NOW, 'to': '2026-01-01T00:00:00Z'}):
            self.error(lambda: self.listing(**values), 'INVALID_INPUT')
        self.assertEqual(audit.capabilities(self.alice, 'data_engineer')['scopes'], ['self'])
        self.assertEqual(audit.capabilities(self.admin, 'admin')['scopes'], ['self', 'database'])
        self.assertEqual(audit.capabilities(self.platform, 'admin')['scopes'], ['self', 'database', 'platform'])
        missing = self.f.root / 'absent'
        with patch.dict(os.environ, SHIROKUMA_STATE_DIR=str(missing)):
            self.error(lambda: self.listing(), 'AUDIT_STORE_UNAVAILABLE')
        self.assertFalse(missing.exists())

    def test_telemetry_explicit_p_only_short_timeout_outage_is_independent(self):
        from agenthouse import manage
        self.error(lambda: audit.telemetry(self.admin, 'admin'))
        before = self.listing()
        with patch.object(manage, 'ensure_runtime', side_effect=AssertionError('startup')) as startup, \
                patch.object(manage.Client, 'rows', side_effect=ValueError(SECRET)) as rows, \
                patch.object(manage.Client, '__init__', return_value=None):
            preview = audit.telemetry(self.platform, 'admin')
            self.assertEqual(preview['availability'], 'unavailable')
            self.assertEqual(preview['code'], 'TELEMETRY_UNAVAILABLE')
            rows.assert_called_once_with(audit.TELEMETRY_SQL, timeout=3)
            self.assertEqual(before['items'], self.listing()['items'])
            self.assertEqual(rows.call_count, 1)
            startup.assert_not_called()
        self.assertNotIn(SECRET, json.dumps(preview))
        row = dict.fromkeys(audit.TELEMETRY_FIELDS, 'public') | dict(duration_ms=1, prompt=SECRET)
        with patch.object(manage.Client, '__init__', return_value=None), patch.object(manage.Client, 'rows', return_value=[row]*101):
            preview = audit.telemetry(self.platform, 'admin')
            self.assertEqual(len(preview['items']), 100)
            self.assertTrue(preview['truncated'])
            self.assertNotIn(SECRET, json.dumps(preview))


if __name__ == '__main__': unittest.main()
