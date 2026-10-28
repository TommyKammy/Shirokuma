"""P4-8 core contracts, temporary SQLite only; no adapters or backend calls."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import json
import copy
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import control_store as store
import data_management as dm
import database_retirement as retirement
import request_auth as auth
import warehouse_admission as admission
from query_context import ResolvedQueryContext, QueryError, resolve_context, revalidate_admission
from grant_fixtures import verified_grants


class ClosureAwareRetirementTests(unittest.TestCase):
    def historical_closure(self, db):
        """Synthetic saved PLAN facts linked to this fixture's real canonical graph."""
        r = self.recovery
        row = dict(db.execute('SELECT * FROM execution_recovery_requests WHERE operation_id=?',
                              (self.plan['operation_id'],)).fetchone())
        intent = json.loads(row['intent'])
        # Historical closure is restricted to a submission with no backend ID.
        db.execute('UPDATE query_execution_contexts SET backend_query_id=NULL WHERE query_id=?',(self.query_id,))
        intent['graph']['executions'][0] = dict(db.execute(
            'SELECT * FROM query_execution_contexts WHERE query_id=?',(self.query_id,)).fetchone())
        query_ns = min(r._nano(x['created_at']) for x in
                       intent['graph']['queries'] + intent['graph']['admissions'])
        def stamp(ns):
            return r.datetime.fromtimestamp(ns / 1e9, r.timezone.utc).isoformat()
        def binding(name, ns):
            return dict(path='/isolated/'+name, sha256='e'*64,
                        stat=dict(birth_ns=ns,mtime_ns=ns,ctime_ns=ns))
        selector = dict(members=[dict(binding('pod-'+c,query_ns-4_000_000_000),container_id=c*64)
                                 for c in ('a','b')], producer=dict(
            intent=binding('intent',query_ns-1_000_000_000),
            result=binding('result',query_ns+1_000_000_000)))
        producer = dict(kind='original-cli-failed-once',calls=1,outcome='UNKNOWN',files=selector['producer'],
            intent_at=stamp(query_ns-2_000_000_000),window_start_ns=query_ns-1_000_000_000,
            window_end_ns=query_ns+1_000_000_000)
        image = 'sha256:'+'d'*64
        cms = [dict(name='trino-config-589cfhfttm',generator='trino-config',name_hash='589cfhfttm',
            current_uid='cm-uid',current_data_digest='d'*64,current_created_at=stamp(query_ns-20_000_000_000))]
        def scope(c, role):
            return dict(container_id=c*64,pod_uid='pod-'+c,replica_set_uid='rs-'+c,deployment_uid='deployment',
                labels=dict(zip(r.MANAGED_LABELS,('pod-'+c,'test-namespace','trino-'+c,'trino'))),
                namespace='test-namespace',image=image,pinned_image='docker.io/trinodb/trino@'+image,
                configmaps=cms,generation_id=intent['graph']['generation']['id'],
                pod_created_at=stamp(query_ns-10_000_000_000),role=role)
        members = []
        for index,c in enumerate(('a','b')):
            s = scope(c,'possible-predecessor' if index == 0 else 'historical-original')
            offsets = (-8,-7,-3,-2.9,-2.8) if index == 0 else (-2.7,-2.6,2,2.1,2.2)
            events = [dict(action=action,time_nano=query_ns+int(offset*1e9),container_id=c*64,
                           labels=s['labels'],image=image,exit_code='143' if action=='die' else None)
                      for action,offset in zip(('create','start','stop','die','destroy'),offsets)]
            member = dict(scope=s,snapshot=selector['members'][index],events=events,
                          events_digest=r.hashlib.sha256(r._json(events).encode()).hexdigest())
            if index == 0:
                member['saved_start'] = dict(kind='pod-running-startedAt',snapshot_sha256=member['snapshot']['sha256'],
                    container_id=c*64,pod_uid=s['pod_uid'],started_at=stamp(query_ns-7_000_000_000))
            members.append(member)
        replacement = dict(scope('c','replacement'),engine_id='same-daemon',started_at=stamp(query_ns+3_000_000_000))
        observation = dict(kind='historical-exact-events',version=2,engine_id='same-daemon',members=members,
            historical_scope=dict(members[1]['scope'],engine_id='same-daemon'),replacement_scope=replacement,producer=producer)
        intent.update(mode=r.HISTORICAL,selector=selector,scope=observation['historical_scope'],
            replacement_scope=replacement,observation=observation,planned_at=stamp(query_ns+4_000_000_000))
        payload = dict(json.loads(row['payload']),mode=r.HISTORICAL,selector=selector)
        row.update(intent=r._json(intent),payload=r._json(payload))
        fresh = dict(engine_id='same-daemon',old_container_ids_absent=['a'*64,'b'*64],
                     replacement_scope=replacement,producer=producer)
        row['proof'] = r._json(r._consumed_historical_proof(row,intent,fresh))
        self.replace_recovery_row(db,row)
        self.assertEqual(r.completed_historical_query(db,row,intent,json.loads(row['proof'])),
                         intent['graph']['admissions'][0]['id'])
        return row

    def replace_recovery_row(self, db, row):
        # Keep production immutable-intent triggers; replace only isolated unit rows.
        db.execute('DELETE FROM execution_recovery_requests WHERE operation_id=?',(row['operation_id'],))
        db.execute('INSERT INTO execution_recovery_requests ('+','.join(row)+') VALUES ('+
                   ','.join('?' for _ in row)+')',tuple(row.values()))

    def test_historical_completed_dependency_survives_later_head_without_external_reads(self):
        with store.connect() as db:
            row = self.historical_closure(db)
        before = self.snapshot()
        with store.connect() as db:
            dm.record_generation(db,dm.WarehouseConfigGeneration('later-historical-head','LOCAL','later',(),(),(),'APPLIED'))
            db.execute("UPDATE warehouse_controls SET applied_generation_id='later-historical-head'")
            db.execute("UPDATE catalog_bindings SET applied_generation_id='later-historical-head'")
        with patch.object(self.f.operator,'graph',side_effect=AssertionError('saved facts only')),\
                patch.object(self.f.operator,'adapter',side_effect=AssertionError('no daemon or files')),\
                patch.object(self.queries,'_transport',side_effect=AssertionError('no SQL')):
            preview = retirement.preview(self.actor,'admin',self.database_id)
        self.assertFalse(preview['blockers'])
        self.assertEqual({d['state'] for d in preview['dependencies'] if d['kind'] in ('query','admission')},{'UNKNOWN'})
        self.assertEqual(self.snapshot(),before)
        self.assertNotIn('SELECT 17 AS preserved',json.dumps(preview))

    def test_historical_completed_dependency_rejects_invalid_saved_claims(self):
        with store.connect() as db:
            row = self.historical_closure(db)
            intent,proof = json.loads(row['intent']),json.loads(row['proof'])
            admission_id = intent['graph']['admissions'][0]['id']
            mutations = [('intent JSON',dict(intent='{')),('proof JSON',dict(proof='{')),
                ('actor',dict(actor='other')),('route',dict(route='recover-job')),
                ('planned',dict(phase='PLANNED')),('proved',dict(phase='PROVED')),
                ('missing proof',dict(proof=None)),('empty proof',dict(proof='{}'))]
            for name,path,value in [('mode',['mode'],'FENCE'),('query',['graph','queries',0,'id'],'other'),
                    ('context',['graph','executions',0,'sql'],'SELECT 999'),
                    ('generation',['graph','queries',0,'generation_id'],'other'),
                    ('missing selector member',['selector','members'],[]),
                    ('chronology',['planned_at'],'2000-01-01T00:00:00Z')]:
                changed = copy.deepcopy(intent)
                target = changed
                for key in path[:-1]: target = target[key]
                target[path[-1]] = value
                mutations.append((name,dict(intent=self.recovery._json(changed))))
            for name,path,value in [('digest',['reference','intent_digest'],'wrong'),
                    ('operation reference',['reference','operation_id'],'other'),
                    ('observation',['reference','observation_digest'],'wrong'),
                    ('fresh daemon',['fresh_current','engine_id'],'other')]:
                changed = copy.deepcopy(proof)
                changed[path[0]][path[1]] = value
                mutations.append((name,dict(proof=self.recovery._json(changed))))
            for name,changes in mutations + [(n,None) for n in ('typed operation','operation actor','evidence','duplicate')]:
                with self.subTest(case=name):
                    db.execute('SAVEPOINT historical_negative')
                    try:
                        if changes:
                            self.replace_recovery_row(db,dict(row,**changes))
                        elif name == 'typed operation':
                            db.execute("UPDATE operations SET action='JOB_EXECUTION_RECOVERY' WHERE id=?",(row['operation_id'],))
                        elif name == 'operation actor':
                            db.execute("UPDATE operations SET actor='other' WHERE id=?",(row['operation_id'],))
                        elif name == 'evidence':
                            db.execute("UPDATE warehouse_admissions SET evidence_ref='other' WHERE id=?",(admission_id,))
                        else:
                            dm.record_operation(db,dm.Operation('duplicate-historical',self.actor.principal_id,
                                'QUERY_EXECUTION_RECOVERY',(),0,'OPEN',(),None,(),()))
                            self.replace_recovery_row(db,dict(row,operation_id='duplicate-historical',idempotency_key='duplicate-historical'))
                        _,blockers = retirement._inventory(db,self.database_id)
                        self.assertTrue(any(b['id'] in (self.query_id,admission_id) for b in blockers))
                    finally:
                        db.execute('ROLLBACK TO historical_negative')
                        db.execute('RELEASE historical_negative')

    def setUp(self):
        from tests import test_execution_recovery as fixtures
        import execution_recovery
        import query_service
        self.recovery,self.queries = execution_recovery,query_service
        self.f = fixtures.StandaloneQueryRecoveryTests()
        self.f.setUp(accepted=True)
        self.addCleanup(self.f.doCleanups)
        self.actor,self.database_id,self.query_id = self.f.admin,self.f.context.database_id,self.f.query_id
        self.f.scope.update(labels={key:'retained-'+str(i) for i,key in enumerate(execution_recovery.MANAGED_LABELS)},
            image='retained-image',started_at='2020-01-01T00:00:00Z')
        def positive_stop(scope,since):
            return dict(kind='stopped-inspect',engine_id=scope['engine_id'],receipt=dict(
                container_id=scope['container_id'],labels=scope['labels'],image=scope['image'],
                started_at=scope['started_at'],restart_policy='no',
                state=dict(Running=False,Status='exited',FinishedAt=since,ExitCode=0)))
        self.f.backend.proof.side_effect = positive_stop
        self.plan = self.f.plan()
        result = self.f.apply(self.plan)
        self.assertEqual(result['phase'],'OPEN')
        self.f.backend.stop.assert_not_called()

    def snapshot(self):
        with store.connect() as db:
            saved = {table:[tuple(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                for table in ('query_records','query_execution_contexts','warehouse_admissions',
                              'execution_recovery_requests','dataops_queries','db_job_runs','db_job_steps')}
            saved['recovery_operation'] = [tuple(row) for row in db.execute('SELECT * FROM operations WHERE id=?',
                (self.plan['operation_id'],))]
            saved['old_generation'] = [tuple(row) for row in db.execute('SELECT * FROM warehouse_config_generations WHERE id=?',
                (self.f.context.generation_id,))]
            return saved

    def test_proved_standalone_closure_preserves_unknown_and_allows_nonpurge_retirement(self):
        before = self.snapshot()
        pin = self.queries._pins[self.query_id]
        pin_before = (pin.context,copy.deepcopy(pin.tokens),list(pin.cancel_urls),dict(pin.headers),pin.origin)
        # Retirement retains the old pin; a newer head must not invalidate proof.
        with store.connect() as db:
            dm.record_generation(db,dm.WarehouseConfigGeneration('new-head','LOCAL','new-apply',(),(),(),'APPLIED'))
            db.execute("UPDATE warehouse_controls SET applied_generation_id='new-head'")
            db.execute("UPDATE catalog_bindings SET applied_generation_id='new-head'")
        with patch.object(self.f.operator,'graph',side_effect=AssertionError('No current graph/READY lookup')),\
                patch.object(self.f.operator,'adapter',side_effect=AssertionError('DB-only inventory')),\
                patch.object(self.queries,'_transport',side_effect=AssertionError('No backend IO')):
            preview = retirement.preview(self.actor,'admin',self.database_id)
            self.assertFalse(preview['blockers'])
            dependencies = [d for d in preview['dependencies'] if d['kind'] in ('query','admission')]
            self.assertTrue(dependencies and all(d['state']=='UNKNOWN' for d in dependencies))
            public = json.dumps(preview)
            for private in ('SELECT 17 AS preserved','credential_snapshot_ref','backend_username','next_uri',str(self.f.accounts.root)):
                self.assertNotIn(private,public)
            op = retirement.request_retire(self.actor,'admin',self.database_id,
                dict(expected_revision=preview['database']['revision']),'proved-closed-query')
            self.assertEqual(op['state'],'RETIRING')
            self.assertFalse(op['blockers'])
            done = retirement.reconcile(self.actor,'admin',op['id'],dict(expected_revision=op['revision']),'closed-query-reconcile')
            self.assertEqual(done['state'],'RETIRED')
        self.assertTrue(self.snapshot() == before)
        self.assertIs(self.queries._pins[self.query_id],pin)
        self.assertTrue((pin.context,pin.tokens,pin.cancel_urls,pin.headers,pin.origin) == pin_before)

    def test_closure_evidence_and_standalone_identity_must_be_unambiguous(self):
        with store.connect() as db:
            a = dict(db.execute("SELECT * FROM warehouse_admissions WHERE kind='query' AND subject_id=?",(self.query_id,)).fetchone())
            request = dict(db.execute('SELECT * FROM execution_recovery_requests WHERE operation_id=?',(self.plan['operation_id'],)).fetchone())
            intent,proof = json.loads(request['intent']),json.loads(request['proof'])
            mutations = [
                ('no evidence',"UPDATE warehouse_admissions SET evidence_ref=NULL",()),
                ('wrong evidence',"UPDATE warehouse_admissions SET evidence_ref='missing'",()),
                ('actor',"UPDATE warehouse_admissions SET actor='other'",()),
                ('generation',"UPDATE warehouse_admissions SET generation_id='other'",()),
                ('query actor',"UPDATE query_records SET actor='other'",()),
                ('old query generation',"UPDATE query_records SET generation_id='other'",()),
                ('write',"UPDATE query_execution_contexts SET mode='write'",()),
                ('parent',"UPDATE warehouse_admissions SET parent_id=id",()),
                ('job',"UPDATE warehouse_admissions SET kind='job'",()),
                ('wrong payload','replace request',('payload',json.dumps(dict(json.loads(request['payload']),query_id='other')))),
                ('route','replace request',('route','recover-job')),
                ('unclosed',"UPDATE execution_recovery_requests SET phase='PROVED'",()),
                ('missing proof',"UPDATE execution_recovery_requests SET proof=NULL",()),
                ('invalid proof JSON',"UPDATE execution_recovery_requests SET proof='{'",()),
            ]
            for name,path,value in [('query','$.graph.queries[0].id','other'),('saved actor','$.graph.queries[0].actor','other'),
                    ('saved generation','$.graph.admissions[0].generation_id','other'),('scope','$.scope.engine_id','other'),
                    ('mode','$.mode','TERMINAL_PARENT')]:
                changed = copy.deepcopy(intent)
                keys = path[2:].replace('[0]','.0').split('.')
                target = changed
                for key in keys[:-1]: target = target[int(key)] if isinstance(target,list) else target[key]
                target[keys[-1]] = value
                mutations.append((name,'replace request',('intent',json.dumps(changed))))
            mutations.append(('invalid intent JSON','replace request',('intent','{')))
            for name,path,value in [('running','$.receipt.state.Running',True),('container','$.receipt.container_id','other'),
                    ('engine','$.engine_id','other'),('old stop','$.receipt.state.FinishedAt','2000-01-01T00:00:00Z')]:
                changed = copy.deepcopy(proof)
                keys = path[2:].split('.')
                target = changed
                for key in keys[:-1]: target = target[key]
                target[keys[-1]] = value
                mutations.append((name,'UPDATE execution_recovery_requests SET proof=?',(json.dumps(changed),)))
            mutations += [('duplicate', '''INSERT INTO warehouse_admissions
                (id,warehouse_id,actor,kind,subject_id,parent_id,generation_id,database_id,state,created_at)
                SELECT 'duplicate',warehouse_id,actor,'job',subject_id,NULL,generation_id,database_id,state,created_at
                FROM warehouse_admissions WHERE id=?''',(a['id'],)),
                ('child', '''INSERT INTO warehouse_admissions
                (id,warehouse_id,actor,kind,subject_id,parent_id,generation_id,database_id,state,created_at)
                SELECT 'child',warehouse_id,actor,'query','child-query',id,generation_id,database_id,'UNKNOWN',created_at
                FROM warehouse_admissions WHERE id=?''',(a['id'],)),
                ('dataops link',"INSERT INTO dataops_queries VALUES ('unlinked-operation','step',?,'read',NULL)",(self.query_id,)),
                ('duplicate closed evidence','duplicate request',())]
            for name,sql,args in mutations:
                with self.subTest(case=name):
                    db.execute('SAVEPOINT negative')
                    try:
                        if name == 'old query generation':
                            dm.record_generation(db,dm.WarehouseConfigGeneration('other','LOCAL','other',(),(),(),'APPLIED'))
                        if name == 'dataops link':
                            db.execute('''INSERT INTO dataops_operations
                                (id,actor,route,idempotency_key,payload,fingerprint,context,backend_username,
                                 credential_snapshot_ref,state,created_at)
                                VALUES ('unlinked-operation','owner','test','test','{}','test',?,'private','private','UNKNOWN','now')''',
                                (json.dumps(asdict(self.f.context)),))
                        if sql == 'duplicate request':
                            dm.record_operation(db,dm.Operation('duplicate-closure',self.actor.principal_id,
                                'QUERY_EXECUTION_RECOVERY',(),0,'PLANNED',(),None,(),()))
                            changed = dict(request,operation_id='duplicate-closure',idempotency_key='duplicate-key')
                            db.execute('INSERT INTO execution_recovery_requests ('+','.join(changed)+') VALUES ('+
                                ','.join('?' for _ in changed)+')',tuple(changed.values()))
                        elif sql == 'replace request':
                            # Immutable-intent triggers remain installed. Construct
                            # a malformed fixture row rather than disabling guards.
                            changed = dict(request)
                            changed[args[0]] = args[1]
                            db.execute('DELETE FROM execution_recovery_requests WHERE operation_id=?',(request['operation_id'],))
                            db.execute('INSERT INTO execution_recovery_requests ('+','.join(changed)+') VALUES ('+
                                ','.join('?' for _ in changed)+')',tuple(changed.values()))
                        else:
                            db.execute(sql,args)
                        _,blockers = retirement._inventory(db,self.database_id)
                        self.assertTrue(any(b['id'] in (self.query_id,a['id']) for b in blockers))
                    finally:
                        db.execute('ROLLBACK TO negative'); db.execute('RELEASE negative')


class RetirementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.accounts = self.root / 'accounts.json'
        self.users = {'owner': dict(role='admin', platform_admin=True, salt='ab'*16, password_hash='cd'*32),
                      'reader': dict(role='data_engineer', platform_admin=False, salt='ab'*16, password_hash='cd'*32)}
        self.save()
        env = patch.dict(os.environ, SHIROKUMA_STATE_DIR=str(self.root / 'state'),
                         SHIROKUMA_AUTH_MODE='shared', SHIROKUMA_AUTH_FILE=str(self.accounts))
        env.start()
        self.addCleanup(env.stop)
        self.owner = auth.AuthenticatedPrincipal('owner', 'admin', True)
        self.reader = auth.AuthenticatedPrincipal('reader', 'data_engineer', False)
        with store.connect() as db:
            dm.record_generation(db, dm.WarehouseConfigGeneration('g', 'LOCAL', 'apply', (), (), (), 'READY'))
            self.a = dm.create_database(db, 'sales', 'Sales', '', 'test').id
            self.b = dm.create_database(db, 'finance', 'Finance', '', 'test').id
            self.schemas = {}
            for ident in (self.a, self.b):
                dm.create_binding(db, ident, 'service', ident, 'catalog', 'storage', 'credentials', 's3://bucket')
                dm.update_binding(db, ident, 1, 1, 'g')
                self.schemas[ident] = dm.create_schema(db, ident, 'raw', 'Raw', 'raw', 'test').id
                db.execute("UPDATE databases SET state='READY' WHERE id=?", (ident,))
            dm.create_grant(db, 'reader', self.a, 'data_engineer')
            verified_grants(db)
        self.context = resolve_context(self.reader, self.a, self.schemas[self.a], 'data_engineer', expected_binding_revision=1)

    def save(self):
        self.accounts.write_text(json.dumps(self.users))

    def start(self, key='start', revision=1):
        return retirement.request_retire(self.owner, 'admin', self.a, {'expected_revision': revision}, key)

    def reconcile(self, op, key='reconcile'):
        return retirement.reconcile(self.owner, 'admin', op['id'], {'expected_revision': op['revision']}, key)

    def query(self, db, ident, database_id=None, state='UNKNOWN', parent=None, legacy=False):
        database_id = database_id or self.a
        handle = admission.admit(db, actor='owner', kind='query', subject_id=ident, generation_id='g',
            parent=parent, database_id=None if legacy else database_id)
        dm.record_query(db, dm.QueryRecord(ident, 'owner', dm.QueryContext(database_id, self.schemas[database_id], 'LOCAL', 'g'), state, 'now'))
        db.execute('''INSERT INTO query_execution_contexts
            (query_id,selected_role,mode,sql_catalog,sql_schema,binding_revision,grant_revision,
            backend_username,credential_snapshot_ref,request_id,worksheet_id,sql)
            VALUES (?,'admin','read','sales','raw',1,NULL,'private-user','private-credential',?,?, 'private SQL')''',
            (ident, parent.subject_id if parent else None, 'step' if parent else None))
        if state in admission.TERMINAL:
            admission.finish(db, handle, state=state)
        else:
            db.execute('UPDATE warehouse_admissions SET state=? WHERE id=?', (state, handle.id))
        return handle

    def dataops(self, db, ident='physical', state='FINISHED', child_state='FINISHED'):
        context = asdict(self.context)
        context['selected_role'] = 'admin'
        db.execute('''INSERT INTO dataops_operations
            (id,actor,route,idempotency_key,payload,fingerprint,context,backend_username,
            credential_snapshot_ref,state,created_at) VALUES (?,'owner','route',?,'{}','fingerprint',?,
            'private-user','private-credential',?,'now')''', (ident, ident, json.dumps(context), state))
        parent = admission.admit(db, actor='owner', kind='dataops', subject_id=ident, generation_id='g', database_id=self.a)
        child = self.query(db, ident+'-q', state=child_state, parent=parent)
        db.execute("INSERT INTO dataops_queries VALUES (?,'step',?,'read',NULL)", (ident, child.subject_id))
        # Represent a persisted interrupted/incorrect parent projection too.
        db.execute('UPDATE warehouse_admissions SET state=? WHERE id=?', (state, parent.id))
        return parent, child

    def schedule(self, db, ident='schedule-a', database_id=None, enabled=1):
        database_id = database_id or self.a
        context = dict(asdict(self.context), database_id=database_id)
        db.execute('''INSERT INTO db_schedules VALUES (?,?,'owner','admin','shared',?,4,'job','job',1,
            '{}','{}',?,?,'succeeded',NULL)''', (ident, database_id, json.dumps(context), enabled, 'future' if enabled else None))
        return json.dumps(context)

    def test_first_start_always_drains_and_explicit_reconcile_retains_identity(self):
        with store.connect() as db:
            object_id = dm.observe_object(db, self.schemas[self.a], 'kept', 'TABLE', 'native', 'Kept').id
        op = self.start()
        self.assertEqual((op['state'], op['revision'], op['database']['state'], op['database']['revision']), ('RETIRING', 1, 'retiring', 2))
        done = self.reconcile(op)
        self.assertEqual((done['state'], done['revision'], done['database']['state'], done['database']['revision']), ('RETIRED', 2, 'retired', 3))
        with store.connect() as db:
            self.assertEqual(dm.get_database(db, self.a).sql_catalog, 'sales')
            self.assertIsNotNone(db.execute('SELECT id FROM object_bindings WHERE id=?', (object_id,)).fetchone())
            self.assertEqual(dm.get_operation(db, op['id']).action, 'DATABASE_RETIRE')
            with self.assertRaises(Exception):
                dm.create_database(db, 'sales', 'Reuse', '', 'test')
        self.assertEqual(retirement.inspect(self.owner, 'admin', done['id']), done)

    def test_resource_refs_include_only_target_canonical_history_without_content_or_blockers(self):
        with store.connect() as db:
            historical = dm.observe_object(db, self.schemas[self.a], 'private_table', 'TABLE', 'private-old-backend', 'private-old-display')
            current = dm.observe_object(db, self.schemas[self.a], 'private_table', 'TABLE', 'private-new-backend', 'private-new-display')
            other = dm.observe_object(db, self.schemas[self.b], 'private_table', 'TABLE', 'private-other-backend', 'private-other-display')
            before = [tuple(r) for r in db.execute('SELECT * FROM object_bindings ORDER BY id')]
            self.assertEqual((historical.incarnation, current.incarnation), (1, 2))
            self.assertEqual(db.execute('SELECT current FROM object_bindings WHERE id=?', (historical.id,)).fetchone()[0], 0)
        expected = [dict(kind='resource_ref', id=b.id, state='RETAINED', database_id=self.a,
                         schema_id=self.schemas[self.a], object_id=b.id, incarnation=b.incarnation)
                    for b in sorted((historical, current), key=lambda b: b.id)]
        preview = retirement.preview(self.owner, 'admin', self.a)
        self.assertEqual(preview['dependencies'], expected)
        self.assertFalse(preview['blockers'])
        op = self.start()
        done = self.reconcile(op)
        for view in (preview, op, done):
            self.assertEqual(view['dependencies'], expected)
            self.assertFalse(view['blockers'])
            self.assertNotIn(other.id, json.dumps(view))
            self.assertNotIn('private', json.dumps(view))
        self.assertEqual(done['state'], 'RETIRED')
        with store.connect() as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM object_bindings ORDER BY id')], before)

    def test_alert_history_is_target_only_retained_nonblocking_and_excludes_content(self):
        with store.connect() as db:
            for ident, database_id, acknowledged, resolved, job_id, run_id in (
                    ('open', self.a, None, None, 'job', 'open-run'),
                    ('acknowledged', self.a, 'now', None, 'job', 'ack-run'),
                    ('resolved', self.a, 'now', 'later', '', ''),
                    ('other-alert', self.b, None, None, 'other-job', 'other-run')):
                db.execute('''INSERT INTO db_alerts
                    (id,subject,database_id,owner,selected_role,context,job_id,job_revision,
                     step_position,step_name,arguments,first_run_id,last_run_id,occurrences,
                     first_seen_at,last_seen_at,acknowledged_at,resolved_at,resolution)
                    VALUES (?,?,?,'owner','admin','private context',?,1,0,'private step',
                     'private arguments','private first run',?,2,'first','last',?,?,?)''',
                    (ident, 'private-subject-' + ident, database_id, job_id, run_id,
                     acknowledged, resolved, 'private resolution' if resolved else None))
            db.execute("INSERT INTO db_alert_subjects VALUES ('private-unbound-subject',1)")
            before = {table: [tuple(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                      for table in ('db_alerts', 'db_alert_subjects')}
        expected = [dict(kind='alert', id='acknowledged', state='RETAINED', database_id=self.a,
                         job_id='job', run_id='ack-run'),
                    dict(kind='alert', id='open', state='RETAINED', database_id=self.a,
                         job_id='job', run_id='open-run'),
                    dict(kind='alert', id='resolved', state='RETAINED', database_id=self.a)]
        preview = retirement.preview(self.owner, 'admin', self.a)
        op = self.start()
        done = self.reconcile(op)
        for view in (preview, op, done):
            self.assertEqual(view['dependencies'], expected)
            self.assertFalse(view['blockers'])
            self.assertNotIn('private', json.dumps(view))
            self.assertNotIn('other-alert', json.dumps(view))
        self.assertEqual(done['state'], 'RETIRED')
        with store.connect() as db:
            for table, rows in before.items():
                self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY rowid')], rows)

    def test_payload_cas_and_route_scoped_idempotency_snapshots(self):
        for payload in ({}, {'expected_revision': True}, {'expected_revision': 0}, {'expected_revision': 1.0},
                        {'expected_revision': 1, 'purge': False}, None, []):
            with self.subTest(payload=payload), self.assertRaises(retirement.RetirementError) as error:
                retirement.request_retire(self.owner, 'admin', self.a, payload, 'bad')
            self.assertEqual(error.exception.status, 400)
        for key in ('', ' ', None, 'x'*257):
            with self.assertRaises(retirement.RetirementError):
                self.start(key)
        with self.assertRaises(retirement.RetirementError):
            self.start(revision=99)
        op = self.start()
        with self.assertRaises(retirement.RetirementError) as error:
            self.start(revision=2)
        self.assertEqual(error.exception.code, 'IDEMPOTENCY_CONFLICT')
        done = self.reconcile(op, 'start')
        self.assertEqual(self.start(), op)  # original accepted response after target changes
        self.assertEqual(self.reconcile(op, 'start'), done)
        with self.assertRaises(retirement.RetirementError):
            self.reconcile(op, 'stale-new-key')
        with self.assertRaises(retirement.RetirementError):
            self.start('new-key', 3)

    def test_management_rechecks_exact_type_current_enabled_admin_every_entry(self):
        op = self.start()
        calls = [lambda p, r: retirement.preview(p, r, self.a), lambda p, r: retirement.inspect(p, r, op['id']),
                 lambda p, r: retirement.request_retire(p, r, self.a, {'expected_revision': 1}, 'start'),
                 lambda p, r: retirement.reconcile(p, r, op['id'], {'expected_revision': 1}, 'reconcile')]
        class Derived(auth.AuthenticatedPrincipal):
            pass
        for principal, role in ((Derived('owner', 'admin', True), 'admin'), (self.reader, 'admin'),
                (replace(self.owner, maximum_role='data_engineer'), 'admin'), (self.owner, 'data_engineer'),
                (replace(self.owner, platform_admin=False), 'admin'), ('owner', 'admin')):
            for call in calls:
                with self.assertRaises(retirement.RetirementError) as error:
                    call(principal, role)
                self.assertEqual(error.exception.status, 403)
        for field, value in (('enabled', False), ('disabled', True), ('platform_admin', False), ('role', 'data_engineer')):
            original = dict(self.users['owner'])
            self.users['owner'][field] = value
            self.save()
            for call in calls:
                with self.assertRaises(retirement.RetirementError):
                    call(self.owner, 'admin')
            self.users['owner'] = original
        self.save()
        del self.users['owner']
        self.save()
        with self.assertRaises(retirement.RetirementError):
            retirement.inspect(self.owner, 'admin', op['id'])

    def test_preview_all_states_and_nonretirement_inspect_404(self):
        with store.connect() as db:
            dm.record_operation(db, dm.Operation('other', 'owner', 'OTHER', (), None, 'FINISHED', (), None, (), ()))
        for state in ('PENDING', 'READY', 'retiring', 'retired', 'UNKNOWN'):
            with store.connect() as db:
                db.execute('UPDATE databases SET state=? WHERE id=?', (state, self.a))
            view = retirement.preview(self.owner, 'admin', self.a)
            self.assertEqual(view['database']['state'], state)
            self.assertEqual(view['stopped_schedule_ids'], [])
            self.assertIsNone(view['operation'])
        for ident in ('other', 'missing', None):
            with self.assertRaises(retirement.RetirementError) as error:
                retirement.inspect(self.owner, 'admin', ident)
            self.assertEqual((error.exception.code, error.exception.status), ('OPERATION_NOT_FOUND', 404))

    def test_only_target_schedules_stop_atomically_and_unlinked_tick_blocks(self):
        with store.connect() as db:
            context = self.schedule(db)
            self.schedule(db, 'disabled', enabled=0)
            self.schedule(db, 'schedule-b', self.b)
            db.execute("INSERT INTO db_schedule_ticks VALUES ('tick','schedule-a',4,1,'now','reserved',NULL,NULL,?,'shared')", (context,))
            before = tuple(db.execute("SELECT * FROM db_schedules WHERE id='schedule-b'").fetchone())
        op = self.start()
        self.assertEqual(op['stopped_schedule_ids'], ['schedule-a'])
        self.assertIn(('schedule_tick', 'tick'), {(x['kind'], x['id']) for x in op['blockers']})
        with store.connect() as db:
            self.assertEqual(tuple(db.execute("SELECT * FROM db_schedules WHERE id='schedule-b'").fetchone()), before)
            self.assertEqual(tuple(db.execute("SELECT enabled,next_run_at,revision FROM db_schedules WHERE id='schedule-a'").fetchone()), (0, None, 5))
            self.assertEqual(db.execute("SELECT revision FROM db_schedules WHERE id='disabled'").fetchone()[0], 5)
            db.execute("UPDATE db_schedule_ticks SET status='unknown' WHERE id='tick'")
        pending = self.reconcile(op)
        self.assertEqual(pending['state'], 'RETIRING')
        view = retirement.preview(self.owner, 'admin', self.a)
        self.assertEqual(view['operation']['stopped_schedule_ids'], ['schedule-a'])
        self.assertEqual(view['stopped_schedule_ids'], ['schedule-a'])

    def test_target_unknown_fenced_and_standalone_queries_block_other_db_ignored(self):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.query(db, 'other', self.b, legacy=True)
            target = self.query(db, 'target', legacy=True)
            db.execute("UPDATE warehouse_admissions SET execution_closure='FENCED_READ_ONLY' WHERE id IN (?,?)",
                       (target.id, admission.get_for_subject(db, kind='query', subject_id='other').id))
            dm.record_query(db, dm.QueryRecord('standalone', 'owner', dm.QueryContext(self.a, self.schemas[self.a], 'LOCAL', 'g'), 'UNKNOWN', 'now'))
        op = self.start()
        public = json.dumps(op)
        self.assertNotIn('other', public)
        self.assertNotIn('private', public)
        self.assertTrue({'target', 'standalone'} <= {b['id'] for b in op['blockers']})
        self.assertEqual(self.reconcile(op)['state'], 'RETIRING')

    def test_unrelated_legacy_unknown_never_blocks_empty_target(self):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            handle = self.query(db, 'old-b', self.b, legacy=True)
            db.execute("UPDATE warehouse_admissions SET execution_closure='FENCED_READ_ONLY' WHERE id=?", (handle.id,))
            admission.admit(db, actor='unrelated', kind='unknown', subject_id='unresolved-without-db')
        op = self.start()
        self.assertFalse(op['blockers'])
        self.assertEqual(self.reconcile(op)['state'], 'RETIRED')

    def test_terminal_parent_cannot_hide_unknown_child_or_orphan_query(self):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            _, child = self.dataops(db, child_state='UNKNOWN')
            db.execute("UPDATE warehouse_admissions SET execution_closure='FENCED_READ_ONLY' WHERE id=?", (child.id,))
        op = self.start()
        self.assertTrue(any(b['kind'] == 'dataops' and b['code'] == 'CLOSURE_UNPROVEN' for b in op['blockers']))
        self.assertEqual(self.reconcile(op)['state'], 'RETIRING')

    def test_orphan_persisted_admission_child_is_inventoried_without_owner_impersonation(self):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            parent, _ = self.dataops(db, state='ACTIVE')
            orphan = self.query(db, 'orphan', state='UNKNOWN', parent=parent)
            # Persist an interrupted parent projection; this real child has no
            # canonical dataops_queries FK, regardless of its metadata strings.
            db.execute("UPDATE query_execution_contexts SET request_id=NULL,worksheet_id=NULL WHERE query_id='orphan'")
            db.execute("UPDATE warehouse_admissions SET state='FINISHED' WHERE id=?", (parent.id,))
            db.execute("UPDATE dataops_operations SET state='FINISHED' WHERE id='physical'")
        op = self.start()
        self.assertIn(orphan.id, {b['id'] for b in op['blockers']})
        self.assertTrue(any(b['kind'] == 'dataops' and b['code'] == 'CLOSURE_UNPROVEN' for b in op['blockers']))
        self.assertEqual(self.reconcile(op)['state'], 'RETIRING')

    def test_other_database_request_id_collisions_never_create_membership_or_break_closure(self):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.dataops(db)
            other = self.query(db, 'other-query', self.b, state='UNKNOWN')
            db.execute("UPDATE query_execution_contexts SET request_id='physical',worksheet_id='step' WHERE query_id='other-query'")
            other_context = json.dumps(dict(asdict(self.context), database_id=self.b, schema_id=self.schemas[self.b]))
            db.execute('''INSERT INTO dataops_operations
                (id,actor,route,idempotency_key,payload,fingerprint,context,backend_username,
                 credential_snapshot_ref,state,created_at)
                SELECT 'other-operation',actor,route,'other-key',payload,fingerprint,?,backend_username,
                 credential_snapshot_ref,'UNKNOWN',created_at FROM dataops_operations WHERE id='physical' ''', (other_context,))
            other_parent = admission.admit(db, actor='owner', kind='dataops', subject_id='other-operation',
                                           generation_id='g', database_id=self.b)
            admission.finish(db, other_parent, state='UNKNOWN')
            self.query(db, 'target-standalone', state='FINISHED')
            db.execute("UPDATE query_execution_contexts SET request_id='other-operation',worksheet_id='step' WHERE query_id='target-standalone'")
        view = retirement.preview(self.owner, 'admin', self.a)
        self.assertFalse(view['blockers'], view['blockers'])
        identities = {d['id'] for d in view['dependencies']}
        self.assertTrue({'physical', 'physical-q', 'target-standalone'} <= identities)
        self.assertTrue({'other-query', other.id, 'other-operation', other_parent.id}.isdisjoint(identities))
        op = self.start()
        self.assertFalse(op['blockers'], op['blockers'])
        self.assertEqual(op['dependencies'], view['dependencies'])
        self.assertEqual(self.reconcile(op)['state'], 'RETIRED')

    def test_native_definitions_runs_steps_scan_items_publications_and_marts_are_minimal(self):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            context = json.dumps(asdict(self.context))
            db.execute("INSERT INTO db_jobs VALUES ('job',?,'Job','owner','admin',1,?,'private definition','now','now')", (self.a, context))
            job_parent = admission.admit(db, actor='owner', kind='job', subject_id='job-run', generation_id='g', database_id=self.a)
            db.execute('''INSERT INTO db_job_runs VALUES ('job-run','job','owner','admin',1,?,
                'private definition','private arguments','private input','private user','private credential',
                'succeeded',0,'now','now','now')''', (context,))
            db.execute("INSERT INTO db_job_steps VALUES ('job-run',0,'step','read','unknown','private result')")
            db.execute("UPDATE warehouse_admissions SET state='FINISHED' WHERE id=?", (job_parent.id,))
            db.execute("INSERT INTO db_stages VALUES ('stage',?,'owner','admin','Stage','private/path',1,?,'private definition','now','now')", (self.a, context))
            scan_parent = admission.admit(db, actor='owner', kind='dataops', subject_id='scan-parent', generation_id='g', database_id=self.a)
            # Use the real scan capability kind; bypass cross-Job policy only to
            # model already persisted rows whose parent incorrectly says terminal.
            db.execute("UPDATE warehouse_admissions SET kind='stage_scan',subject_id='scan',state='FINISHED' WHERE id=?", (scan_parent.id,))
            record = json.dumps(dict(status='succeeded', context=asdict(self.context), owner='owner', private='private result'))
            db.execute("INSERT INTO db_stage_scans VALUES ('scan','stage',?,'private definition',?,0)", (record, scan_parent.id))
            db.execute("INSERT INTO db_stage_scan_items VALUES ('item','scan',0,?,'private bytes','content')", (json.dumps(dict(status='unknown', private='private file')),))
            self.dataops(db)
            db.execute("INSERT INTO db_stage_runs VALUES ('stage-run','stage','physical',?,'private definition','fingerprint','private bytes')", (record,))
            db.execute("INSERT INTO db_stage_publications VALUES ('stage','private.csv','stage-run','reserved','private receipt')")
            db.execute("INSERT INTO db_marts VALUES ('mart',?,'owner',1,?,'private definition','private record')", (self.a, context))
            mart_parent = admission.admit(db, actor='owner', kind='dataops', subject_id='mart-parent', generation_id='g', database_id=self.a)
            db.execute("UPDATE warehouse_admissions SET kind='mart',subject_id='mart-run',state='FINISHED' WHERE id=?", (mart_parent.id,))
            db.execute("INSERT INTO db_mart_runs VALUES ('mart-run','mart',?,?)", (record, mart_parent.id))
            snapshots = {t: [tuple(r) for r in db.execute('SELECT * FROM '+t)] for t in
                ('db_jobs','db_job_runs','db_job_steps','db_stages','db_stage_scans','db_stage_scan_items',
                 'db_stage_runs','db_stage_publications','db_marts','db_mart_runs','query_records','query_execution_contexts')}
        op = self.start()
        self.assertTrue({'job', 'job_run', 'job_step', 'stage', 'stage_scan', 'stage_scan_item', 'stage_run',
                         'stage_publication', 'mart', 'mart_run'} <= {d['kind'] for d in op['dependencies']})
        self.assertTrue({'job_step', 'stage_scan_item', 'stage_publication'} <= {d['kind'] for d in op['blockers']})
        self.assertNotIn('private', json.dumps(op))
        self.assertEqual(self.reconcile(op)['state'], 'RETIRING')
        with store.connect() as db:
            for table, rows in snapshots.items():
                self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM '+table)], rows, table)

    def test_completed_export_retained_minimal_inventory_and_pending_intent_blocks(self):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.dataops(db)
            db.execute("INSERT INTO export_artifacts VALUES ('export','physical','owner',?,'private artifact','COMPLETE','private/path','private-profile')", (json.dumps(asdict(self.context)),))
            kept = tuple(db.execute('SELECT * FROM export_artifacts').fetchone())
        view = retirement.preview(self.owner, 'admin', self.a)
        self.assertFalse(view['blockers'], view['blockers'])
        self.assertNotIn('private', json.dumps(view))
        with store.connect() as db:
            db.execute("UPDATE export_artifacts SET state='INTENT' WHERE export_id='export'")
        op = self.start()
        self.assertTrue(any(b['kind'] == 'export' for b in op['blockers']))
        waiting = self.reconcile(op)
        with store.connect() as db:
            db.execute("UPDATE export_artifacts SET state='COMPLETE' WHERE export_id='export'")
        self.assertEqual(self.reconcile(waiting, 'finish')['state'], 'RETIRED')
        with store.connect() as db:
            self.assertEqual(tuple(db.execute('SELECT * FROM export_artifacts').fetchone()), kept)

    def test_private_continuation_inherits_exact_identity_and_retired_denies(self):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            parent = admission.admit(db, actor='reader', kind='dataops', subject_id='accepted', generation_id='g', database_id=self.a)
        op = self.start()
        with self.assertRaises(PermissionError):
            auth.authorize_database(self.reader, self.a, 'data_engineer', expected_generation_id='g')
        with admission.accepted(parent):
            fresh = resolve_context(self.reader, self.a, self.schemas[self.a], 'data_engineer', expected_binding_revision=1)
            self.assertEqual(fresh, self.context)
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                revalidate_admission(db, self.reader, self.context)
                child = admission.admit(db, actor='reader', kind='query', subject_id='child')
                self.assertEqual((child.database_id, child.generation_id), (self.a, 'g'))
                for kwargs in ({'actor': 'owner'}, {'warehouse_id': 'other'}, {'database_id': self.b}, {'generation_id': 'other'}):
                    values = dict(actor='reader', kind='query', subject_id='bad', **{})
                    values.update(kwargs)
                    with self.assertRaises((admission.AdmissionConflict, admission.AdmissionDenied)):
                        admission.admit(db, **values)
            with store.connect() as db:
                db.execute("UPDATE databases SET state='retired' WHERE id=?", (self.a,))
            with self.assertRaises(PermissionError):
                auth.authorize_database(self.reader, self.a, 'data_engineer', expected_generation_id='g')
        self.assertIsNone(admission.current())

    def test_continuation_never_rescues_account_epoch_binding_schema_generation(self):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            parent = admission.admit(db, actor='reader', kind='dataops', subject_id='accepted', generation_id='g', database_id=self.a)
        self.start()
        with admission.accepted(parent):
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                for altered in (replace(self.context, generation_id='other'), replace(self.context, grant_revision=999),
                                replace(self.context, binding_revision=99), replace(self.context, sql_catalog='finance'),
                                replace(self.context, schema_id=self.schemas[self.b])):
                    with self.assertRaises(QueryError):
                        revalidate_admission(db, self.reader, altered)
                db.execute("UPDATE effective_grants SET epoch=epoch+1 WHERE principal_id='reader'")
                with self.assertRaises(QueryError):
                    revalidate_admission(db, self.reader, self.context)
                db.rollback()
            self.users['reader']['enabled'] = False
            self.save()
            with self.assertRaises(PermissionError):
                auth.authorize_database(self.reader, self.a, 'data_engineer', expected_generation_id='g')
            self.users['reader']['enabled'] = True
            self.users['reader']['role'] = 'bi_viewer'
            self.save()
            with self.assertRaises(PermissionError):
                auth.authorize_database(self.reader, self.a, 'data_engineer', expected_generation_id='g')

    def test_unknown_accepted_scope_forged_handle_and_new_root_cannot_continue(self):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            parent = admission.admit(db, actor='owner', kind='query', subject_id='unknown-parent', generation_id='g', database_id=self.a)
            other = admission.admit(db, actor='owner', kind='dataops', subject_id='other-parent', generation_id='g', database_id=self.b)
            db.execute("UPDATE warehouse_admissions SET state='UNKNOWN' WHERE id=?", (parent.id,))
        self.start()
        with self.assertRaises(admission.AdmissionConflict), admission.accepted(replace(parent, database_id=self.b)):
            pass
        with admission.accepted(parent, allow_unknown=True):
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                self.assertFalse(admission.continuing_database(db, 'owner', self.a, 'g'))
                with self.assertRaises(admission.AdmissionConflict):
                    admission.admit(db, actor='owner', kind='query', subject_id='forbidden-child')
                with self.assertRaises(admission.AdmissionConflict):
                    admission.admit(db, actor='owner', kind='query', subject_id='explicit-other-parent', parent=other)
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaises(admission.AdmissionDenied):
                admission.admit(db, actor='owner', kind='query', subject_id='new-root', database_id=self.a)

    def test_retire_and_new_admission_serialize_on_same_immediate_transaction(self):
        waiting = threading.Event()
        def retire():
            waiting.set()
            return self.start()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                handle = admission.admit(db, actor='owner', kind='dataops', subject_id='first', database_id=self.a)
                future = pool.submit(retire)
                self.assertTrue(waiting.wait(2))
            op = future.result(timeout=5)
        self.assertIn(handle.id, {b['id'] for b in op['blockers']})
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaises(admission.AdmissionDenied):
                admission.admit(db, actor='owner', kind='dataops', subject_id='late', database_id=self.a)

    def test_start_failure_rolls_back_database_and_schedule_stop(self):
        with store.connect() as db:
            self.schedule(db)
        with patch.object(retirement, '_inventory', side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):
                self.start()
        with store.connect() as db:
            self.assertEqual(dm.get_database(db, self.a).state, 'READY')
            self.assertEqual(tuple(db.execute('SELECT enabled,revision FROM db_schedules').fetchone()), (1, 4))
            self.assertEqual(db.execute('SELECT count(*) FROM database_retirements').fetchone()[0], 0)
        self.assertEqual(self.start()['state'], 'RETIRING')


if __name__ == '__main__':
    unittest.main()
