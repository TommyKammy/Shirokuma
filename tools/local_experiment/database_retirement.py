"""Local, nonpurge database retirement. All observations are canonical ledger reads."""
import json
import re
import uuid
from datetime import datetime, timezone

import control_store
import data_management as registry
import request_auth
import warehouse_admission as admission


class RetirementError(RuntimeError):
    def __init__(self, code, message, status=409):
        self.code, self.status = code, status
        super().__init__(message)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def _admin(principal, selected_role):
    if (type(principal) is not request_auth.AuthenticatedPrincipal or selected_role != 'admin' or
            principal.maximum_role != 'admin' or principal.platform_admin is not True):
        raise RetirementError('FORBIDDEN', 'Current platform administrator required', 403)
    try:
        account = request_auth._principal_accounts().get(principal.principal_id)
    except request_auth.AuthenticationError:
        account = None
    if (not account or account.get('role') != 'admin' or account.get('platform_admin') is not True or
            account.get('enabled', True) is not True or account.get('disabled', False) is not False):
        raise RetirementError('FORBIDDEN', 'Current platform administrator required', 403)
    return principal.principal_id


def _database(db, database_id):
    if not isinstance(database_id, str) or not database_id:
        raise RetirementError('DATABASE_NOT_FOUND', 'Database not found', 404)
    row = registry.get_database(db, database_id)
    if row is None:
        raise RetirementError('DATABASE_NOT_FOUND', 'Database not found', 404)
    return dict(id=row.id, sql_catalog=row.sql_catalog, state=row.state, revision=row.revision)


def _payload(payload, key):
    if (type(payload) is not dict or set(payload) != {'expected_revision'} or
            type(payload['expected_revision']) is not int or payload['expected_revision'] < 1):
        raise RetirementError('INVALID_REQUEST', 'Exactly one positive expected_revision required', 400)
    if not isinstance(key, str) or not key.strip() or len(key) > 256:
        raise RetirementError('INVALID_IDEMPOTENCY_KEY', 'Idempotency-Key required', 400)
    return _json(payload)


def _replay(db, actor, route, key, payload):
    row = db.execute('''SELECT payload,response FROM database_retirement_requests
        WHERE actor=? AND route=? AND idempotency_key=?''', (actor, route, key)).fetchone()
    if row is None:
        return None
    if row['payload'] != payload:
        raise RetirementError('IDEMPOTENCY_CONFLICT', 'Key already used with another payload')
    return json.loads(row['response'])


def _remember(db, actor, route, key, payload, response):
    db.execute('INSERT INTO database_retirement_requests VALUES (?,?,?,?,?,?)',
        (actor, route, key, payload, response['id'], _json(response)))


_TERMINAL = frozenset(('FINISHED', 'FAILED', 'CANCELED', 'SUCCEEDED', 'SKIPPED', 'PARTIAL',
                       'NOT_STARTED', 'COMPLETE', 'PUBLISHED'))


def _closed_query_admission(db, query_id, database_id):
    """Prove an exact persisted standalone read-only fence, without runtime IO.

    The old graph is an identity witness, not a request to revalidate today's
    database/head or to expose the saved SQL/credentials in the inventory.
    """
    try:
        rows = db.execute('SELECT * FROM warehouse_admissions WHERE subject_id=?',(query_id,)).fetchall()
        if len(rows) != 1:
            return None
        a = dict(rows[0])
        if (a['kind'] != 'query' or a['state'] != 'UNKNOWN' or admission.execution_unresolved(a)
                or a['parent_id'] is not None or a['database_id'] != database_id or not a['evidence_ref']
                or db.execute('SELECT 1 FROM warehouse_admissions WHERE parent_id=? OR (evidence_ref=? AND id!=?)',
                              (a['id'],a['evidence_ref'],a['id'])).fetchone()):
            return None
        q = db.execute('''SELECT id,actor,database_id,schema_id,warehouse_id,generation_id,state,created_at
            FROM query_records WHERE id=?''',(query_id,)).fetchone()
        e = db.execute('''SELECT query_id,mode,request_id,worksheet_id,selected_role,binding_revision,grant_revision
            FROM query_execution_contexts WHERE query_id=?''',(query_id,)).fetchone()
        if (q is None or e is None or q['state'] != 'UNKNOWN' or e['mode'] != 'read-only'
                or any(a[k] != q[k] for k in ('actor','database_id','warehouse_id','generation_id'))):
            return None
        if db.execute('SELECT 1 FROM dataops_queries WHERE query_id=?',(query_id,)).fetchone():
            return None
        for table,column in (('dataops_operations','id'),('db_job_operations','operation_id'),
                             ('db_job_runs','id'),('runs','id')):
            if db.execute(f'SELECT 1 FROM {table} WHERE {column} IN (?,?)',(query_id,e['request_id'])).fetchone():
                return None
        row = db.execute('''SELECT r.actor,r.route,r.phase,r.proof,o.actor AS operation_actor,o.action,
            json_valid(r.intent) AS valid_intent,json_valid(r.payload) AS valid_payload
            FROM execution_recovery_requests r JOIN operations o ON o.id=r.operation_id
            WHERE r.operation_id=?''',(a['evidence_ref'],)).fetchone()
        if (row is None or row['route'] != 'recover-query' or row['phase'] not in ('CLOSED','OPEN')
                or row['actor'] != q['actor'] or row['operation_actor'] != q['actor']
                or row['action'] != 'QUERY_EXECUTION_RECOVERY' or not row['valid_intent'] or not row['valid_payload']):
            return None
        mode = db.execute("SELECT json_extract(intent,'$.mode') FROM execution_recovery_requests WHERE operation_id=?",
                          (a['evidence_ref'],)).fetchone()[0]
        if mode == 'HISTORICAL_STOP_FENCE':
            from execution_recovery import completed_historical_query
            from query_context import QueryError
            canonical = db.execute('SELECT * FROM execution_recovery_requests WHERE operation_id=?',
                                   (a['evidence_ref'],)).fetchone()
            try:
                closed = completed_historical_query(db,canonical,json.loads(canonical['intent']),
                                                     json.loads(canonical['proof']))
            except (QueryError,IndexError):
                return None
            return a['id'] if closed == a['id'] else None
        # Read only canonical identity fields from the private graph. In
        # particular, never select graph.executions (which contains saved SQL).
        for section,actual in (('queries',dict(q)),('admissions',{k:a[k] for k in
                ('id','warehouse_id','actor','kind','subject_id','parent_id','generation_id','database_id','state')}),
                ('executions',dict(e))):
            paths = ['$.graph.'+section+'[0].'+key for key in actual]
            saved = db.execute('SELECT '+','.join('json_extract(intent,?)' for _ in paths)+
                ' FROM execution_recovery_requests WHERE operation_id=?',(*paths,a['evidence_ref'])).fetchone()
            if tuple(saved) != tuple(actual.values()):
                return None
        meta = db.execute('''SELECT json_extract(payload,'$.subject'),json_extract(payload,'$.query_id'),
            json_extract(intent,'$.mode'),json_extract(intent,'$.graph.subject'),
            json_array_length(intent,'$.graph.queries'),json_array_length(intent,'$.graph.admissions'),
            json_array_length(intent,'$.graph.executions'),json_extract(intent,'$.graph.query_ids'),
            json_extract(intent,'$.graph.operation_ids'),json_extract(intent,'$.graph.database.id'),
            json_extract(intent,'$.graph.schema.id'),json_extract(intent,'$.graph.schema.database_id'),
            json_extract(intent,'$.graph.generation.id'),json_extract(intent,'$.graph.generation.warehouse_id'),
            json_extract(intent,'$.graph.binding.applied_generation_id'),
            json_extract(intent,'$.scope'),json_extract(intent,'$.planned_at'),
            json_extract(intent,'$.graph.admissions[0].execution_closure')
            FROM execution_recovery_requests WHERE operation_id=?''',(a['evidence_ref'],)).fetchone()
        if (tuple(meta[:7]) != ('query',query_id,'FENCE','query',1,1,1)
                or json.loads(meta[7]) != [query_id] or json.loads(meta[8]) != []
                or meta[17] != '' or tuple(meta[9:15]) != (database_id,q['schema_id'],database_id,q['generation_id'],
                                        q['warehouse_id'],q['generation_id'])):
            return None
        # Multiple closed claims for the same query are ambiguous even if the
        # admission currently names just one of them. Invalid JSON never proves closure.
        candidates = db.execute("""SELECT count(*) FROM execution_recovery_requests
            WHERE route='recover-query' AND phase IN ('CLOSED','OPEN') AND
            CASE WHEN json_valid(payload) THEN json_extract(payload,'$.query_id') END=?""",(query_id,)).fetchone()[0]
        if candidates != 1:
            return None
        scope,proof = json.loads(meta[15]),json.loads(row['proof'])
        def timestamp(value):
            result = datetime.fromisoformat(value.replace('Z','+00:00'))
            if result.tzinfo is None:
                raise ValueError()
            return result
        planned = timestamp(meta[16])
        labels = ('io.kubernetes.pod.uid','io.kubernetes.pod.namespace',
                  'io.kubernetes.pod.name','io.kubernetes.container.name')
        receipt = proof['receipt']
        if (not isinstance(scope['engine_id'],str) or not scope['engine_id']
                or not re.fullmatch('[0-9a-f]{64}',scope['container_id'])
                or set(scope['labels']) != set(labels) or any(not isinstance(scope['labels'][k],str) or not scope['labels'][k] for k in labels)
                or not isinstance(scope['image'],str) or not scope['image']
                or not timestamp(scope['started_at']) <= timestamp(q['created_at']) <= planned
                or proof['engine_id'] != scope['engine_id'] or receipt['container_id'] != scope['container_id']
                or receipt['labels'] != scope['labels']):
            return None
        if proof['kind'] == 'stopped-inspect':
            state = receipt['state']
            positive = (receipt['image'] == scope['image'] and receipt['started_at'] == scope['started_at']
                and receipt['restart_policy'] == 'no' and state['Running'] is False and state['Status'] == 'exited'
                and type(state['ExitCode']) is int and timestamp(state['FinishedAt']) >= planned)
        elif proof['kind'] == 'exact-event':
            positive = (receipt['action'] in ('die','destroy') and type(receipt['time_nano']) is int
                and receipt['time_nano'] >= int(planned.timestamp()*1e9))
        else:
            positive = False
        return a['id'] if positive else None
    except (KeyError,TypeError,ValueError,AttributeError,OverflowError):
        return None


def _inventory(db, database_id):
    """Explicit native relations, saved contexts and exact operation/parent links.

    Select only identity and state fields. Never load SQL, input, artifact records,
    credentials, directory names or result contents into the management response.
    Unknown stays unknown; proved standalone execution closure only removes its
    active blocker, without changing the dependency or the underlying history.
    """
    dependencies, blockers = {}, {}

    def add(kind, ident, state, *, blocked=None, **links):
        state = str(state or 'UNKNOWN')
        item = dict(kind=kind, id=str(ident), state=state, database_id=database_id, **links)
        identity = (kind, str(ident))
        dependencies[identity] = item
        if blocked:
            blockers[identity] = dict(item, code=blocked)
        elif blocked is None and state.upper() not in _TERMINAL:
            blockers[identity] = dict(item, code='OUTCOME_UNKNOWN' if state.upper() == 'UNKNOWN' else 'WORK_UNRESOLVED')

    for row in db.execute('''SELECT b.id,b.schema_id,b.incarnation FROM object_bindings b
        JOIN schemas s ON s.id=b.schema_id WHERE s.database_id=?''', (database_id,)):
        add('resource_ref', row['id'], 'RETAINED', blocked=False,
            schema_id=row['schema_id'], object_id=row['id'], incarnation=row['incarnation'])
    for row in db.execute('SELECT id,job_id,last_run_id FROM db_alerts WHERE database_id=?', (database_id,)):
        add('alert', row['id'], 'RETAINED', blocked=False,
            **({'job_id': row['job_id']} if row['job_id'] else {}),
            **({'run_id': row['last_run_id']} if row['last_run_id'] else {}))

    # Definitions are retained even when no execution ever existed.
    for kind, table in (('job', 'db_jobs'), ('stage', 'db_stages'), ('mart', 'db_marts')):
        for row in db.execute(f'SELECT id FROM {table} WHERE database_id=?', (database_id,)):
            add(kind, row['id'], 'RETAINED', blocked=False)
    for row in db.execute('SELECT model_id FROM wap_models WHERE database_id=?', (database_id,)):
        add('wap_model', row['model_id'], 'RETAINED', blocked=False)
    for kind, table in (('wap_build', 'wap_builds'), ('wap_audit', 'wap_audits'), ('wap_publication', 'wap_publications')):
        for row in db.execute(f'SELECT id,model_id,operation_id FROM {table} WHERE database_id=?', (database_id,)):
            add(kind, row['id'], 'RETAINED', blocked=False, model_id=row['model_id'], operation_id=row['operation_id'])

    runs = {}
    for row in db.execute('''SELECT r.id,r.job_id,r.status FROM db_job_runs r JOIN db_jobs j ON j.id=r.job_id
        WHERE j.database_id=? OR json_extract(r.context,'$.database_id')=?''', (database_id, database_id)):
        runs[('job', row['id'])] = row['status']
        add('job_run', row['id'], row['status'], job_id=row['job_id'])
    for row in db.execute('''SELECT s.run_id,s.position,s.status FROM db_job_steps s
        JOIN db_job_runs r ON r.id=s.run_id JOIN db_jobs j ON j.id=r.job_id
        WHERE j.database_id=? OR json_extract(r.context,'$.database_id')=?''', (database_id, database_id)):
        add('job_step', row['run_id'] + ':' + str(row['position']), row['status'], run_id=row['run_id'])
    for row in db.execute('''SELECT r.id,r.stage_id,json_extract(r.record,'$.status') AS state
        FROM db_stage_scans r JOIN db_stages s ON s.id=r.stage_id
        WHERE s.database_id=? OR json_extract(r.record,'$.context.database_id')=?''', (database_id, database_id)):
        runs[('stage', row['id'])] = row['state']
        add('stage_scan', row['id'], row['state'], stage_id=row['stage_id'])
    for row in db.execute('''SELECT i.id,i.scan_id,json_extract(i.record,'$.status') AS state
        FROM db_stage_scan_items i JOIN db_stage_scans r ON r.id=i.scan_id
        JOIN db_stages s ON s.id=r.stage_id WHERE s.database_id=?
        OR json_extract(r.record,'$.context.database_id')=?''', (database_id, database_id)):
        add('stage_scan_item', row['id'], row['state'], scan_id=row['scan_id'])
    for row in db.execute('''SELECT r.id,r.mart_id,json_extract(r.record,'$.status') AS state
        FROM db_mart_runs r JOIN db_marts m ON m.id=r.mart_id
        WHERE m.database_id=? OR json_extract(r.record,'$.context.database_id')=?''', (database_id, database_id)):
        runs[('mart', row['id'])] = row['state']
        add('mart_run', row['id'], row['state'], mart_id=row['mart_id'])
    for row in db.execute('''SELECT r.id,r.stage_id,r.operation_id,json_extract(r.record,'$.status') AS state
        FROM db_stage_runs r JOIN db_stages s ON s.id=r.stage_id
        WHERE s.database_id=? OR json_extract(r.record,'$.context.database_id')=?''', (database_id, database_id)):
        add('stage_run', row['id'], row['state'], stage_id=row['stage_id'], operation_id=row['operation_id'])
    # Legacy rows are associated only by their saved DB context or an explicit
    # typed admission; unbound global legacy UNKNOWNs are never assigned here.
    for row in db.execute('''SELECT r.id,r.status FROM runs r WHERE
        json_extract(r.definition,'$.context.database_id')=? OR EXISTS
        (SELECT 1 FROM warehouse_admissions a WHERE a.kind='job' AND a.subject_id=r.id AND a.database_id=?)''',
        (database_id, database_id)):
        add('legacy_run', row['id'], row['status'])
    for row in db.execute('''SELECT p.run_id,p.stage_id,p.state FROM db_stage_publications p
        JOIN db_stages s ON s.id=p.stage_id JOIN db_stage_runs r ON r.id=p.run_id
        WHERE s.database_id=? OR json_extract(r.record,'$.context.database_id')=?''', (database_id, database_id)):
        add('stage_publication', row['run_id'], row['state'], stage_id=row['stage_id'])

    for row in db.execute('SELECT id,enabled,target_kind,target_id FROM db_schedules WHERE database_id=?', (database_id,)):
        add('schedule', row['id'], 'ENABLED' if row['enabled'] else 'STOPPED', blocked=False,
            target_kind=row['target_kind'], target_id=row['target_id'])
    for row in db.execute('''SELECT t.id,t.schedule_id,t.status,t.run_id,s.target_kind,s.target_id,
        s.owner,t.context,t.target_revision FROM db_schedule_ticks t JOIN db_schedules s ON s.id=t.schedule_id
        WHERE s.database_id=? OR json_extract(t.context,'$.database_id')=?''', (database_id, database_id)):
        # Tick terminality never masks active children. Unlinked reservations
        # cannot be dismissed by the act of disabling their schedule.
        state = row['status']
        if state in ('reserved', 'accepted', 'unknown') and row['run_id']:
            table, field, status, revision, context, owner = {
                'job': ('db_job_runs', 'job_id', 'status', 'revision', 'context', 'owner'),
                'stage': ('db_stage_scans', 'stage_id', "json_extract(record,'$.status')",
                          "json_extract(record,'$.stage_revision')", "json_extract(record,'$.context')", "json_extract(record,'$.owner')"),
                'mart': ('db_mart_runs', 'mart_id', "json_extract(record,'$.status')",
                         "json_extract(record,'$.mart_revision')", "json_extract(record,'$.context')", "json_extract(record,'$.owner')")
            }.get(row['target_kind'], (None,) * 6)
            if table:
                linked = db.execute(f'''SELECT {field} AS target,{status} AS status,{revision} AS revision,
                    {context} AS context,{owner} AS owner FROM {table} WHERE id=?''', (row['run_id'],)).fetchone()
                if (linked and linked['target'] == row['target_id'] and linked['revision'] == row['target_revision']
                        and linked['owner'] == row['owner'] and json.loads(linked['context']) == json.loads(row['context'])
                        and str(linked['status']).upper() in _TERMINAL):
                    state = linked['status']
        add('schedule_tick', row['id'], state, schedule_id=row['schedule_id'],
            **({'run_id': row['run_id']} if row['run_id'] else {}))

    # DataOps may predate typed DB admissions: use persisted context and native
    # operation FKs, never infer membership from actor, SQL catalog or generation.
    operations = list(db.execute('''SELECT o.id,o.state,o.actor FROM dataops_operations o WHERE
        json_extract(o.context,'$.database_id')=:d OR o.id IN (
        SELECT x.operation_id FROM db_job_operations x JOIN db_job_runs r ON r.id=x.run_id
            JOIN db_jobs j ON j.id=r.job_id WHERE j.database_id=:d OR json_extract(r.context,'$.database_id')=:d
        UNION SELECT r.operation_id FROM db_stage_runs r JOIN db_stages s ON s.id=r.stage_id
            WHERE s.database_id=:d OR json_extract(r.record,'$.context.database_id')=:d
        UNION SELECT x.operation_id FROM db_stage_scan_operations x JOIN db_stage_scans r ON r.id=x.scan_id
            JOIN db_stages s ON s.id=r.stage_id WHERE s.database_id=:d OR json_extract(r.record,'$.context.database_id')=:d
        UNION SELECT x.operation_id FROM db_mart_operations x JOIN db_mart_runs r ON r.id=x.run_id
            JOIN db_marts m ON m.id=r.mart_id WHERE m.database_id=:d OR json_extract(r.record,'$.context.database_id')=:d
        UNION SELECT operation_id FROM wap_models WHERE database_id=:d
        UNION SELECT operation_id FROM wap_builds WHERE database_id=:d
        UNION SELECT operation_id FROM wap_audits WHERE database_id=:d
        UNION SELECT operation_id FROM wap_publications WHERE database_id=:d
        UNION SELECT operation_id FROM export_artifacts WHERE json_extract(context,'$.database_id')=:d
        UNION SELECT d.operation_id FROM dataops_queries d JOIN query_records q ON q.id=d.query_id WHERE q.database_id=:d
        UNION SELECT a.subject_id FROM warehouse_admissions a WHERE a.kind='dataops' AND a.database_id=:d)''', {'d': database_id}))
    operation_ids = {r['id'] for r in operations}
    query_ids = {r['id'] for r in db.execute('SELECT id FROM query_records WHERE database_id=?', (database_id,))}
    for operation_id in operation_ids:
        query_ids.update(r[0] for r in db.execute(
            'SELECT query_id FROM dataops_queries WHERE operation_id=?', (operation_id,)))

    # Admission ancestry is itself a typed persisted relation. Seed only from
    # direct DB identity or an exact canonical kind/subject association.
    admissions = {r['id']: dict(r) for r in db.execute('''SELECT id,warehouse_id,actor,kind,subject_id,
        parent_id,generation_id,database_id,state,execution_closure FROM warehouse_admissions''')}
    selected = set()
    for ident, row in admissions.items():
        native = {'query': row['subject_id'] in query_ids, 'dataops': row['subject_id'] in operation_ids,
                  'job': ('job', row['subject_id']) in runs, 'stage_scan': ('stage', row['subject_id']) in runs,
                  'mart': ('mart', row['subject_id']) in runs}
        if row['database_id'] == database_id or native.get(row['kind'], False):
            selected.add(ident)
    while True:
        children = {ident for ident, row in admissions.items() if row['parent_id'] in selected}
        if children <= selected:
            break
        selected.update(children)
    closed_queries = {query_id:ident for query_id in query_ids
                      if (ident := _closed_query_admission(db,query_id,database_id)) is not None}
    closed_admissions = set(closed_queries.values())
    for ident in sorted(selected):
        row = admissions[ident]
        native_missing = ((row['kind'] == 'job' and ('job', row['subject_id']) not in runs and
                           not db.execute('SELECT 1 FROM runs WHERE id=?', (row['subject_id'],)).fetchone()) or
                          (row['kind'] == 'stage_scan' and ('stage', row['subject_id']) not in runs) or
                          (row['kind'] == 'mart' and ('mart', row['subject_id']) not in runs))
        add('admission', ident, row['state'], subject_kind=row['kind'], subject_id=row['subject_id'],
            blocked=('DATABASE_IDENTITY_MISMATCH' if row['database_id'] not in (None, database_id)
                     else 'CLOSURE_UNPROVEN' if native_missing else False if ident in closed_admissions else None),
            **({'parent_id': row['parent_id']} if row['parent_id'] else {}))
        if row['kind'] == 'query':
            query_ids.add(row['subject_id'])
        if row['kind'] == 'dataops':
            operation_ids.add(row['subject_id'])
    # Native parents need their exact saved admission and matching operation
    # children even when their displayed status already claims completion.
    for row in db.execute('''SELECT r.id,'job' AS kind,r.owner,r.context,NULL AS admission_id
        FROM db_job_runs r JOIN db_jobs j ON j.id=r.job_id
        WHERE j.database_id=:d OR json_extract(r.context,'$.database_id')=:d
        UNION ALL SELECT r.id,'stage_scan',json_extract(r.record,'$.owner'),
            json_extract(r.record,'$.context'),r.admission_id FROM db_stage_scans r
            JOIN db_stages s ON s.id=r.stage_id WHERE s.database_id=:d OR json_extract(r.record,'$.context.database_id')=:d
        UNION ALL SELECT r.id,'mart',json_extract(r.record,'$.owner'),json_extract(r.record,'$.context'),r.admission_id
            FROM db_mart_runs r JOIN db_marts m ON m.id=r.mart_id
            WHERE m.database_id=:d OR json_extract(r.record,'$.context.database_id')=:d''', {'d': database_id}):
        context = json.loads(row['context'])
        candidates = [a for a in admissions.values() if a['kind'] == row['kind'] and a['subject_id'] == row['id']
                      and a['warehouse_id'] == context.get('warehouse_id')]
        parent = candidates[0] if len(candidates) == 1 else None
        valid = (parent is not None and parent['actor'] == row['owner'] and parent['state'] in _TERMINAL
                 and parent['generation_id'] == context.get('generation_id')
                 and (row['admission_id'] is None or row['admission_id'] == parent['id']))
        table, field = {'job': ('db_job_operations', 'run_id'), 'stage_scan': ('db_stage_scan_operations', 'scan_id'),
                        'mart': ('db_mart_operations', 'run_id')}[row['kind']]
        linked = {r[0] for r in db.execute(f'SELECT operation_id FROM {table} WHERE {field}=?', (row['id'],))}
        if parent:
            children = [a for a in admissions.values() if a['parent_id'] == parent['id']]
            valid = valid and all(a['kind'] == 'dataops' and a['subject_id'] in linked and a['state'] in _TERMINAL for a in children)
            valid = valid and linked == {a['subject_id'] for a in children}
        if not valid:
            kind = {'job': 'job_run', 'stage_scan': 'stage_scan', 'mart': 'mart_run'}[row['kind']]
            item = dependencies[(kind, row['id'])]
            blockers[(kind, row['id'])] = dict(item, code='CLOSURE_UNPROVEN')
    for operation_id in operation_ids:
        query_ids.update(r[0] for r in db.execute(
            'SELECT query_id FROM dataops_queries WHERE operation_id=?', (operation_id,)))
    for query_id in sorted(query_ids):
        row = db.execute('SELECT state FROM query_records WHERE id=?', (query_id,)).fetchone()
        add('query', query_id, row['state'] if row else 'UNKNOWN',blocked=False if query_id in closed_queries else None)
    for operation_id in sorted(operation_ids):
        row = db.execute('SELECT state,actor,context FROM dataops_operations WHERE id=?', (operation_id,)).fetchone()
        context = json.loads(row['context']) if row else {}
        candidates = [a for a in admissions.values() if a['kind'] == 'dataops' and a['subject_id'] == operation_id
                      and a['warehouse_id'] == context.get('warehouse_id')]
        parent = candidates[0] if len(candidates) == 1 else None
        unproved = (row is None or parent is None or parent['actor'] != row['actor'] or
                    parent['generation_id'] != context.get('generation_id') or context.get('database_id') != database_id)
        if parent:
            # Successful physical operations need observed canonical queries.
            links = list(db.execute('''SELECT d.query_id,q.state,q.actor,q.database_id,q.generation_id,
                e.request_id,e.worksheet_id,d.step,a.actor AS admission_actor,a.generation_id AS admission_generation,
                a.parent_id,a.state AS admission_state FROM dataops_queries d
                LEFT JOIN query_records q ON q.id=d.query_id
                LEFT JOIN query_execution_contexts e ON e.query_id=d.query_id
                LEFT JOIN warehouse_admissions a ON a.kind='query' AND a.subject_id=d.query_id AND a.warehouse_id=q.warehouse_id
                WHERE d.operation_id=?''', (operation_id,)))
            unproved = unproved or parent['state'] not in _TERMINAL or (row and row['state'] == 'FINISHED' and not links)
            unproved = unproved or any(q['state'] not in _TERMINAL or q['admission_state'] not in _TERMINAL or
                q['actor'] != row['actor'] or q['admission_actor'] != row['actor'] or q['database_id'] != database_id or
                q['generation_id'] != parent['generation_id'] or q['admission_generation'] != parent['generation_id'] or
                q['parent_id'] != parent['id'] or q['request_id'] != operation_id or q['worksheet_id'] != q['step'] for q in links)
            canonical = {q['query_id'] for q in links}
            unproved = unproved or any(a['subject_id'] not in canonical or a['kind'] != 'query'
                for a in admissions.values() if a['parent_id'] == parent['id'])
        add('dataops', operation_id, row['state'] if row else 'UNKNOWN',
            blocked='CLOSURE_UNPROVEN' if unproved else None)
    for row in db.execute('''SELECT export_id,operation_id,state FROM export_artifacts
        WHERE json_extract(context,'$.database_id')=?''', (database_id,)):
        add('export', row['export_id'], row['state'], operation_id=row['operation_id'])
    # Artifacts linked from a target native operation also belong in inventory,
    # regardless of whether an old artifact context carried database_id.
    for operation_id in operation_ids:
        for row in db.execute('SELECT export_id,state FROM export_artifacts WHERE operation_id=?', (operation_id,)):
            add('export', row['export_id'], row['state'], operation_id=operation_id)
    return [dependencies[k] for k in sorted(dependencies)], [blockers[k] for k in sorted(blockers)]


def _operation(db, operation_id):
    if not isinstance(operation_id, str) or not operation_id:
        raise RetirementError('OPERATION_NOT_FOUND', 'Retirement operation not found', 404)
    row = db.execute('''SELECT r.*,o.actor FROM database_retirements r JOIN operations o ON o.id=r.operation_id
        WHERE r.operation_id=? AND o.action='DATABASE_RETIRE' ''', (operation_id,)).fetchone()
    if row is None:
        raise RetirementError('OPERATION_NOT_FOUND', 'Retirement operation not found', 404)
    dependencies, blockers = _inventory(db, row['database_id'])
    return dict(id=operation_id, action='DATABASE_RETIRE', actor=row['actor'], database_id=row['database_id'],
        revision=row['revision'], state=row['state'], database=_database(db, row['database_id']),
        dependencies=dependencies, blockers=blockers, stopped_schedule_ids=json.loads(row['stopped_schedule_ids']))


def preview(principal, selected_role, database_id):
    _admin(principal, selected_role)
    with control_store.connect() as db:
        db.execute('BEGIN')
        database = _database(db, database_id)
        dependencies, blockers = _inventory(db, database_id)
        row = db.execute('SELECT operation_id FROM database_retirements WHERE database_id=?', (database_id,)).fetchone()
        operation = _operation(db, row[0]) if row else None
        return dict(database=database, dependencies=dependencies, blockers=blockers,
                    stopped_schedule_ids=operation['stopped_schedule_ids'] if operation else [], operation=operation)


def request_retire(principal, selected_role, database_id, payload, key):
    actor = _admin(principal, selected_role)
    canonical = _payload(payload, key)
    route = 'database-retire/' + str(database_id)
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        replay = _replay(db, actor, route, key, canonical)
        if replay is not None:
            return replay
        _database(db, database_id)
        try:
            registry.retire_database_state(db, database_id, payload['expected_revision'])
        except registry.RevisionConflict:
            raise RetirementError('REVISION_CONFLICT', 'Database must be READY at expected revision') from None
        stopped = [r['id'] for r in db.execute('''SELECT id FROM db_schedules WHERE database_id=?
            AND (enabled!=0 OR next_run_at IS NOT NULL) ORDER BY id''', (database_id,))]
        # Invalidate every target pending schedule guard, including disabled rows.
        db.execute('''UPDATE db_schedules SET enabled=0,next_run_at=NULL,revision=revision+1,
            last_status='paused',paused_reason='DATABASE_RETIRING' WHERE database_id=?''', (database_id,))
        operation_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        registry.record_operation(db, registry.Operation(operation_id, actor, 'DATABASE_RETIRE', (),
            payload['expected_revision'], 'RETIRING', (), None, (('database_id', database_id),), (('created_at', now),)))
        db.execute('INSERT INTO database_retirements VALUES (?,?,1,?,?)',
            (operation_id, database_id, 'RETIRING', _json(stopped)))
        response = _operation(db, operation_id)
        _remember(db, actor, route, key, canonical, response)
        return response


def inspect(principal, selected_role, operation_id):
    _admin(principal, selected_role)
    with control_store.connect() as db:
        db.execute('BEGIN')
        return _operation(db, operation_id)


def reconcile(principal, selected_role, operation_id, payload, key):
    actor = _admin(principal, selected_role)
    canonical = _payload(payload, key)
    route = 'database-retire-reconcile/' + str(operation_id)
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        replay = _replay(db, actor, route, key, canonical)
        if replay is not None:
            return replay
        response = _operation(db, operation_id)
        if response['revision'] != payload['expected_revision'] or response['state'] != 'RETIRING':
            raise RetirementError('REVISION_CONFLICT', 'Retirement operation revision changed')
        if response['database']['state'] != 'retiring':
            raise RetirementError('REVISION_CONFLICT', 'Database retirement state changed')
        state = 'RETIRING'
        if not response['blockers']:
            try:
                registry.retire_database_state(db, response['database_id'], response['database']['revision'], finalize=True)
            except registry.RevisionConflict:
                raise RetirementError('REVISION_CONFLICT', 'Database retirement state changed') from None
            state = 'RETIRED'
        db.execute('''UPDATE database_retirements SET revision=revision+1,state=?
            WHERE operation_id=? AND revision=? AND state='RETIRING' ''', (state, operation_id, payload['expected_revision']))
        db.execute('UPDATE operations SET state=? WHERE id=?', (state, operation_id))
        response = _operation(db, operation_id)
        _remember(db, actor, route, key, canonical, response)
        return response
