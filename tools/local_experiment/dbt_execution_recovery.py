"""Private abandonment of one indeterminate native publication COUNT audit.

Physical proof closes execution only. The publication and query outcomes remain
UNKNOWN, and the source model's publication reservation remains occupied.
"""
from contextlib import contextmanager
import copy
import hashlib
import json
import re
import uuid

import control_store
import db_dbt as dbt
import dataops_service as ds
import data_management as registry
import query_service as queries
from query_context import ResolvedQueryContext
import warehouse_admission as admission
from execution_recovery import Recovery, CLOSURE, MANAGED_LABELS, _one, _require, _json, _time, _nano, _query_pin
from logical_execution_recovery import LogicalQueryRecovery

ROUTE = 'recover-dbt-audit'
MARKER = '_execution_recovery'


def _rows(db, sql, args=()):
    return [dict(r) for r in db.execute(sql, args)]


def _lineage(db, ident):
    """Raw durable rows, including children missing from the admission tree."""
    row = _one(db, 'SELECT * FROM dbt_runs WHERE id=?', (ident,))
    links = _rows(db, 'SELECT * FROM dbt_run_operations WHERE run_id=? ORDER BY phase,node_id', (ident,))
    operations, children, records, executions, admissions, owned = [], [], [], [], [], []
    if row['admission_id']:
        admissions.append(_one(db, 'SELECT * FROM warehouse_admissions WHERE id=?', (row['admission_id'],)))
    for link in links:
        op = _one(db, 'SELECT * FROM dataops_operations WHERE id=?', (link['operation_id'],))
        operations.append(op)
        owned.extend(_rows(db, 'SELECT * FROM dataops_owned_objects WHERE operation_id=? ORDER BY object_id', (op['id'],)))
        admissions.extend(_rows(db, "SELECT * FROM warehouse_admissions WHERE kind='dataops' AND subject_id=? ORDER BY id", (op['id'],)))
        for child in _rows(db, 'SELECT * FROM dataops_queries WHERE operation_id=? ORDER BY step', (op['id'],)):
            children.append(child)
            records.append(_one(db, 'SELECT * FROM query_records WHERE id=?', (child['query_id'],)))
            executions.append(_one(db, 'SELECT * FROM query_execution_contexts WHERE query_id=?', (child['query_id'],)))
            admissions.extend(_rows(db, "SELECT * FROM warehouse_admissions WHERE kind='query' AND subject_id=? ORDER BY id", (child['query_id'],)))
        _require({r['query_id'] for r in executions if r['request_id'] == op['id']} ==
                 {r[0] for r in db.execute('SELECT query_id FROM query_execution_contexts WHERE request_id=?', (op['id'],))})
    # Capture the complete admission descendants, including unlinked children.
    tree = _rows(db, '''WITH RECURSIVE tree AS (
        SELECT * FROM warehouse_admissions WHERE id=? UNION ALL
        SELECT a.* FROM warehouse_admissions a JOIN tree t ON a.parent_id=t.id)
        SELECT * FROM tree ORDER BY id''', (row['admission_id'],))
    _require({a['id'] for a in tree} == {a['id'] for a in admissions})
    return dict(row=row, links=links, operations=operations, query_links=children,
                queries=records, executions=executions, admissions=admissions, owned_objects=owned)


def _publication_snapshot(db, ident):
    current = _lineage(db, ident)
    publication = json.loads(current['row']['record'])
    return dict(publication=publication, publication_row=current.pop('row'), **current,
        requests=_rows(db, 'SELECT * FROM dbt_requests WHERE route=? AND json_extract(response,\'$.id\')=? '
            'ORDER BY actor,idempotency_key', ('/api/v1/dbt/runs/' + publication['parent_run_id'] + '/publish', ident)))


def _snapshot(db, ident):
    current = _publication_snapshot(db, ident)
    publication = current['publication']
    source = _lineage(db, publication['parent_run_id'])
    plan = _one(db, 'SELECT * FROM dbt_runs WHERE id=?', (publication['plan_id'],))
    context = ResolvedQueryContext(**publication['context'])
    return dict(subject='dbt-audit', source=source, plan=plan, **current,
        binding=_one(db, 'SELECT * FROM catalog_bindings WHERE database_id=?', (context.database_id,)),
        database=_one(db, 'SELECT * FROM databases WHERE id=?', (context.database_id,)),
        schema=_one(db, 'SELECT * FROM schemas WHERE id=?', (context.schema_id,)),
        generation=_one(db, 'SELECT * FROM warehouse_config_generations WHERE id=?', (context.generation_id,)),
        query_ids=sorted(q['id'] for q in current['queries']),
        operation_ids=sorted(o['id'] for o in current['operations']))


def _summary(operation_id):
    return _json(dict(code='OPERATOR_ABANDONED_READ_ONLY', query_outcome='UNKNOWN',
                      recovery_operation_id=operation_id, execution_closure=CLOSURE))


def _marker(row):
    return dict(operation_id=row['operation_id'], intent_digest=DbtAuditRecovery.digest(row),
                proof_digest=hashlib.sha256(row['proof'].encode()).hexdigest())


def _identities(db, frozen):
    """Immutable source/plan identity, independent of current eligibility."""
    p = frozen['publication']
    source_row, source = dbt._row(db, p['parent_run_id'])
    plan_row, plan = dbt._row(db, p['plan_id'])
    original_source = json.loads(frozen['source']['row']['record'])
    original_plan = json.loads(frozen['plan']['record'])
    for row, record, original, kind in ((source_row, source, original_source, 'run'),
                                       (plan_row, plan, original_plan, 'plan')):
        _require(row['id'] == record['id'] == original['id'] and record['kind'] == kind and
                 row['database_id'] == record['context']['database_id'] == p['context']['database_id'])
        for key in ('actor', 'context', 'source_digest', '_plan_digest'):
            _require(ds._json(record[key]) == ds._json(original[key]), 'RECOVERY_GRAPH_CHANGED')
    _require(source['actor'] == plan['actor'] and source['context'] == plan['context'] and
             source['executor'] == 'native_graph' and source['plan_id'] == plan['id'])
    _require(next(m for m in source['models'] if m['unique_id'] == p['node_id']).get('publication_id') == p['id'],
             'RECOVERY_GRAPH_CHANGED')
    saved_parent = frozen['source']['admissions'][0]
    parent = _one(db, 'SELECT * FROM warehouse_admissions WHERE id=?', (source_row['admission_id'],))
    fields = ('id', 'actor', 'kind', 'subject_id', 'parent_id', 'database_id', 'warehouse_id', 'generation_id')
    _require(all(parent[k] == saved_parent[k] for k in fields) and parent['kind'] == 'dbt' and
             parent['subject_id'] == source['id'] and parent['actor'] == source['actor'] and
             parent['parent_id'] is None and all(parent[k] == source['context'][k] for k in
                ('database_id', 'warehouse_id', 'generation_id')))
    return source


def _closed(db, row, frozen):
    operation_id = row['operation_id']
    actual = _publication_snapshot(db, frozen['publication']['id'])
    expected = copy.deepcopy({k: frozen[k] for k in actual})
    expected['publication'][MARKER] = _marker(row)
    expected['publication_row']['record'] = ds._json(expected['publication'])
    expected['operations'][0].update(state='FAILED', cleanup_error=_summary(operation_id))
    for index, a in enumerate(expected['admissions']):
        if a['kind'] == 'query':
            a.update(execution_closure=CLOSURE, evidence_ref=operation_id)
        else:
            finished = actual['admissions'][index]['finished_at']
            _require(isinstance(finished, str) and _time(finished) >= _time(a['created_at']), 'RECOVERY_GRAPH_CHANGED')
            a.update(state='FAILED', evidence_ref=operation_id, finished_at=finished)
    _require(actual == expected, 'RECOVERY_GRAPH_CHANGED')
    _identities(db, frozen)
    _require(dbt._native_frozen_graph(db, actual['publication']) is not None, 'RECOVERY_GRAPH_CHANGED')


def verified_closure(db, publication):
    """Read only; a marker alone never exempts UNKNOWN from normal settlement."""
    try:
        return _verified_closure(db, publication)
    except (KeyError, TypeError, ValueError, AttributeError, StopIteration, IndexError):
        _require(False, 'RECOVERY_CLOSURE_INVALID')


def _verified_closure(db, publication):
    _require(publication.get('kind') == 'publication' and publication.get('origin') == 'native_graph' and
             publication.get('state') == 'UNKNOWN', 'RECOVERY_CLOSURE_INVALID')
    marker = publication.get(MARKER)
    _require(isinstance(marker, dict) and set(marker) == {'operation_id', 'intent_digest', 'proof_digest'},
             'RECOVERY_CLOSURE_INVALID')
    operation_id = marker['operation_id']
    _require(str(uuid.UUID(operation_id)) == operation_id, 'RECOVERY_CLOSURE_INVALID')
    row = _one(db, 'SELECT * FROM execution_recovery_requests WHERE operation_id=?', (operation_id,))
    intent = json.loads(row['intent'])
    frozen = intent['graph']
    _require(row['route'] == ROUTE and row['actor'] == publication['actor'] and
             row['phase'] in ('CLOSED', 'OPEN') and row['proof'] and
             intent.get('mode') == 'FENCE' and frozen.get('subject') == 'dbt-audit' and
             frozen['publication']['id'] == publication['id'], 'RECOVERY_CLOSURE_INVALID')
    payload = json.loads(row['payload'])
    _require(marker == _marker(row) and
             row['intent'] == _json(intent) and row['proof'] == _json(json.loads(row['proof'])) and
             set(payload) == {'subject', 'publication_id', 'config', 'runtime_binding'} and
             payload['subject'] == 'dbt-audit' and payload['publication_id'] == publication['id'] and
             row['payload'] == _json(payload), 'RECOVERY_CLOSURE_INVALID')
    operation = _one(db, 'SELECT * FROM operations WHERE id=?', (operation_id,))
    _require(operation['actor'] == row['actor'] and operation['action'] == DbtAuditRecovery.action and
             operation['state'] == row['phase'] and json.loads(operation['query_ids']) == frozen['query_ids'] and
             json.loads(operation['targets']) == [] and operation['apply_id'] is None and
             json.loads(operation['before_after']) == [['execution_closure', CLOSURE]],
             'RECOVERY_CLOSURE_INVALID')
    # Validate the saved receipt contract without re-observing a daemon or
    # invoking the adapter's live membership census from a public GET.
    proof, scope = json.loads(row['proof']), intent['scope']
    _require(scope.get('kind') == 'logical-current-managed-scope-v1' and
             isinstance(scope.get('members'), list) and scope['members'] and
             proof.get('kind') == 'logical-all-members-stopped-v1' and
             proof.get('engine_id') == scope['engine_id'] and
             proof.get('scope_digest') == hashlib.sha256(_json(scope).encode()).hexdigest() and
             len(proof.get('members', [])) == len(scope['members']), 'RECOVERY_CLOSURE_INVALID')
    _require(len({m['container_id'] for m in scope['members']}) == len(scope['members']), 'RECOVERY_CLOSURE_INVALID')
    for member, saved in zip(scope['members'], proof['members']):
        receipt = saved.get('receipt', {})
        _require(re.fullmatch('[0-9a-f]{64}', member['container_id']) is not None and
                 member['engine_id'] == scope['engine_id'] and
                 all(isinstance(member['labels'].get(k), str) and member['labels'][k] for k in
                     MANAGED_LABELS) and saved.get('engine_id') == scope['engine_id'] and
                 receipt.get('container_id') == member['container_id'] and
                 receipt.get('labels') == member['labels'], 'RECOVERY_CLOSURE_INVALID')
        if saved.get('kind') == 'exact-event':
            _require(receipt.get('action') in ('die', 'destroy') and
                     type(receipt.get('time_nano')) is int and
                     receipt['time_nano'] >= _nano(intent['planned_at']), 'RECOVERY_CLOSURE_INVALID')
        else:
            state = receipt.get('state', {})
            _require(saved.get('kind') == 'stopped-inspect' and receipt.get('image') == member['image'] and
                     receipt.get('started_at') == member['started_at'] and receipt.get('restart_policy') == 'no' and
                     state.get('Running') is False and state.get('Status') == 'exited' and
                     _time(state['FinishedAt']) >= _time(intent['planned_at']) and
                     type(state.get('ExitCode')) is int and 0 <= state['ExitCode'] <= 255,
                     'RECOVERY_CLOSURE_INVALID')
    _closed(db, row, frozen)
    return dict(operation_id=operation_id, phase=row['phase'], query_outcome='UNKNOWN')


class DbtAuditRecovery(LogicalQueryRecovery):
    route = ROUTE
    action = 'DBT_AUDIT_EXECUTION_RECOVERY'

    @staticmethod
    def subject(frozen):
        return frozen['publication']['id']

    def _request(self, ident):
        return _json(dict(subject='dbt-audit', publication_id=ident,
                          config=self.config, runtime_binding=self.seal))

    @contextmanager
    def locks(self, frozen):
        with Recovery.locks(self, frozen):
            with queries._acquire_query_owner('dbt-' + self.subject(frozen)):
                yield

    def plan(self, publication_id, key):
        return Recovery.plan(self, publication_id, key)

    def _binding(self, db, frozen):
        Recovery._binding(self, db, frozen)
        self._logical_blockers(db)
        principal, _ = self.authenticate()
        q, e = frozen['queries'][0], frozen['executions'][0]
        queries._owned(principal, q['id'], e['selected_role'])
        context = ResolvedQueryContext(**frozen['publication']['context'])
        queries.revalidate_admission(db, principal, context)
        decision, headers = queries.trino_auth.prepare_backend_v2(principal=principal,
            database_id=context.database_id, selected_role=context.selected_role)
        _require(headers['X-Trino-User'] == e['backend_username'] and
                 decision.generation_id == context.generation_id and
                 decision.grant_revision == context.grant_revision, 'RECOVERY_BINDING_CHANGED')

    def graph(self, db, ident):
        try:
            return self._graph(db, ident)
        except (KeyError, TypeError, ValueError, AttributeError, StopIteration, IndexError):
            _require(False)

    def _graph(self, db, ident):
        g = _snapshot(db, ident)
        p = g['publication']
        _require(p['id'] == ident and p['kind'] == 'publication' and p['actor'] == self.actor and
                 p['state'] == 'UNKNOWN' and p.get('origin') == 'native_graph' and
                 p.get('committed') is False and p.get('native_audit') is None and
                 p.get('native_publication') is None and p.get('audit_id') is None and
                 p.get('publication_id') is None and MARKER not in p)
        context = ResolvedQueryContext(**p['context'])
        _require(len(g['requests']) == 1 and g['requests'][0]['actor'] == self.actor and
                 g['requests'][0]['payload'] == ds._json(dict(context=p['context'], node_id=p['node_id'],
                     expected_target=p['expected_target'])) and g['requests'][0]['response'] == ds._json(dict(id=ident)))
        _require(context.warehouse_id == 'LOCAL' and context.selected_role == 'admin' and
                 g['publication_row']['id'] == p['id'] and g['publication_row']['database_id'] == context.database_id)
        _require(g['binding']['desired_revision'] == g['binding']['applied_revision'] == context.binding_revision and
                 g['binding']['applied_generation_id'] == context.generation_id == admission.control(db)['applied_generation_id'] and
                 g['generation']['state'] == 'APPLIED' and g['generation']['warehouse_id'] == context.warehouse_id and
                 g['database']['state'] == 'READY' and g['database']['sql_catalog'] == context.sql_catalog and
                 g['schema']['database_id'] == context.database_id and g['schema']['sql_name'] == context.sql_schema and
                 g['schema']['purpose'] == 'sandbox')
        _require(len(g['links']) == len(g['operations']) == len(g['query_links']) ==
                 len(g['queries']) == len(g['executions']) == 1 and len(g['admissions']) == 3 and not g['owned_objects'])
        op, child, q, e = (g[k][0] for k in ('operations', 'query_links', 'queries', 'executions'))
        _require(g['links'] == [dict(run_id=ident, phase='audit', node_id=p['node_id'], operation_id=op['id'])])
        payload = ds._json(dict(context=p['context'], payload=dbt._native_child_payload(p, 'audit')))
        pending = json.loads(op['result'] or '{}')
        _require(set(pending) == {'origin', 'audit_id', 'intent'} and
                 isinstance(pending['audit_id'], str) and pending['audit_id'] and
                 op['result'] == ds._json(dict(origin=p['origin'], audit_id=pending['audit_id'], intent=p['_native_intent'])))
        _require(op['route'] == 'dbt-native-audit' and op['idempotency_key'] == ident + ':audit' and
                 op['state'] == q['state'] == 'UNKNOWN' and op['actor'] == q['actor'] == self.actor and
                 op['context'] == ds._json(p['context']) and op['payload'] == payload and
                 op['fingerprint'] == hashlib.sha256(payload.encode()).hexdigest() and not op['committed'] and
                 child == dict(operation_id=op['id'], step='audit', query_id=q['id'], action='wap_audit', commit_result=None))
        _require(e['query_id'] == q['id'] and e['request_id'] == op['id'] and e['worksheet_id'] == 'audit' and
                 e['mode'] == 'dataops' and e['backend_username'] == op['backend_username'] and
                 e['credential_snapshot_ref'] == op['credential_snapshot_ref'] and
                 all({**q, **e}[k] == v for k, v in p['context'].items()) and
                 (e['backend_query_id'] is None or queries._backend_id(e['backend_query_id'])))
        pin = _query_pin(q['id'])
        if pin is not None:
            resident = queries._pins[q['id']]
            _require(pin['context'] == context.public_metadata() and
                     resident.headers.get('X-Trino-User') == e['backend_username'] and
                     resident.admission.id == g['admissions'][2]['id'])
        intent, source = p['_native_intent'], json.loads(g['source']['row']['record'])
        _require(p['run_id'] == p['parent_run_id'] == source['id'] and p['plan_id'] == source['plan_id'] and
                 source['executor'] == p['origin'] and
                 all(source['context'][k] == p['context'][k] for k in ('database_id', 'schema_id', 'warehouse_id')) and
                 g['plan']['database_id'] == context.database_id and
                 next(m for m in source['models'] if m['unique_id'] == p['node_id'])['publication_id'] == ident)
        _identities(db, g)
        _require(dbt._native_frozen_graph(db, p) is not None)
        candidate, locator = intent['candidate'], intent['build']['candidate_locator']
        _require(candidate['ref']['database_id'] == locator['database_id'] == context.database_id and
                 candidate['ref']['schema_id'] == locator['schema_id'] == context.schema_id and
                 isinstance(candidate['snapshot'], str) and re.fullmatch('[0-9]+', candidate['snapshot']) is not None)
        # These names come from the matched registered context and immutable
        # native locator. Quote their exact spelling, including SANDBOX casing.
        sql = 'SELECT COUNT(*) FROM ' + '.'.join(registry.quote_identifier(n) for n in
            (context.sql_catalog, context.sql_schema, locator['physical_name'])) + ' FOR VERSION AS OF ' + candidate['snapshot']
        _require(e['sql'] == sql)
        parent, oa, qa = g['admissions']
        _require(parent['id'] == g['publication_row']['admission_id'] and parent['kind'] == 'dbt' and
                 parent['subject_id'] == ident and parent['parent_id'] is None and
                 oa['parent_id'] == parent['id'] and qa['parent_id'] == oa['id'])
        _require(all(a['state'] == 'UNKNOWN' and a['actor'] == self.actor and
            a['database_id'] == context.database_id and a['warehouse_id'] == context.warehouse_id and
            a['generation_id'] == context.generation_id and not a['execution_closure'] for a in g['admissions']))
        self._binding(db, g)
        _require({a['id'] for a in admission.blockers(db)} == {a['id'] for a in g['admissions']}, 'UNRELATED_UNRESOLVED_WORK')
        for table, field, terminal, excluded in (
            ('runs', 'status', ('succeeded', 'failed', 'canceled'), ''),
            ('db_job_runs', 'status', ('succeeded', 'failed', 'canceled'), ''),
            ('dataops_operations', 'state', tuple(admission.TERMINAL), op['id'])):
            _require(not db.execute('SELECT 1 FROM ' + table + ' WHERE ' + field + ' NOT IN (' +
                ','.join('?' for _ in terminal) + ') AND id!=?', (*terminal, excluded)).fetchone(), 'UNRELATED_UNRESOLVED_WORK')
        _require(not db.execute('''SELECT 1 FROM query_records q WHERE q.id!=? AND
            q.state NOT IN ('FINISHED','FAILED','CANCELED') AND NOT EXISTS (SELECT 1 FROM
            warehouse_admissions a WHERE a.kind='query' AND a.subject_id=q.id AND a.execution_closure=?)''',
            (q['id'], CLOSURE)).fetchone(), 'UNRELATED_UNRESOLVED_WORK')
        _require(not db.execute("""SELECT 1 FROM dbt_runs WHERE id!=? AND
            (json_extract(record,'$.state') IN ('QUEUED','BUILDING','TESTING','AUDITING','PUBLISHING',
                'CANCELING','COMPILING','UNKNOWN','COMMITTED_RECORDING_FAILED')) AND NOT EXISTS (
                SELECT 1 FROM warehouse_admissions a WHERE a.id=dbt_runs.admission_id
                AND a.state IN ('FINISHED','FAILED','CANCELED'))""", (ident,)).fetchone(),
            'UNRELATED_UNRESOLVED_WORK')
        return g

    @staticmethod
    def digest(row):
        return hashlib.sha256(_json(dict(contract='recover-dbt-audit-intent-v1',
            **{k: row[k] for k in ('operation_id', 'actor', 'route', 'payload', 'intent')})).encode()).hexdigest()

    def inspect(self, operation_id):
        self.authenticate()
        with control_store.connect() as db:
            row = _one(db, 'SELECT * FROM execution_recovery_requests WHERE operation_id=?', (operation_id,))
        _require(row['route'] == self.route and row['actor'] == self.actor, 'RECOVERY_SUBJECT_MISMATCH')
        intent = json.loads(row['intent'])
        _require(intent['graph'].get('subject') == 'dbt-audit')
        return dict(operation_id=operation_id, publication_id=self.subject(intent['graph']), actor=row['actor'],
            revision=row['revision'], phase=row['phase'], mode='FENCE', intent_digest=self.digest(row),
            query_ids=intent['graph']['query_ids'], scope=intent['scope'],
            proof=json.loads(row['proof']) if row['proof'] else None, outcome='UNKNOWN',
            execution_closure=CLOSURE if row['phase'] in ('CLOSED', 'OPEN') else '')

    def apply(self, operation_id, expected_revision, *, expected_intent_digest=None, reconcile=False):
        self.authenticate()
        with control_store.connect() as db:
            row = _one(db, 'SELECT * FROM execution_recovery_requests WHERE operation_id=?', (operation_id,))
            _require(row['actor'] == self.actor and row['route'] == self.route, 'RECOVERY_SUBJECT_MISMATCH')
            _require(expected_intent_digest == self.digest(row), 'RECOVERY_INTENT_MISMATCH')
            if row['phase'] in ('CLOSED', 'OPEN'):
                self._closed_graph(db, operation_id, json.loads(row['intent'])['graph'])
        return Recovery.apply(self, operation_id, expected_revision, reconcile=reconcile)

    def _closed_graph(self, db, operation_id, frozen, mode='FENCE'):
        self._binding(db, frozen)
        verified_closure(db, dbt._row(db, self.subject(frozen))[1])

    def _close(self, db, operation_id, frozen, mode='FENCE'):
        _require(self.graph(db, self.subject(frozen)) == frozen, 'RECOVERY_GRAPH_CHANGED')
        parent, oa, qa = frozen['admissions']
        db.execute('UPDATE warehouse_admissions SET execution_closure=?,evidence_ref=? WHERE id=?',
                   (CLOSURE, operation_id, qa['id']))
        db.execute("UPDATE dataops_operations SET state='FAILED',cleanup_error=? WHERE id=?",
                   (_summary(operation_id), frozen['operations'][0]['id']))
        for a in (oa, parent):
            admission.finish(db, admission.AdmissionHandle(**{k: a[k] for k in
                ('id', 'warehouse_id', 'actor', 'kind', 'subject_id', 'generation_id', 'database_id')}),
                             state='FAILED', evidence_ref=operation_id)
        row = _one(db, 'SELECT * FROM execution_recovery_requests WHERE operation_id=?', (operation_id,))
        publication = dict(frozen['publication'], **{MARKER: _marker(row)})
        db.execute('UPDATE dbt_runs SET record=? WHERE id=?', (ds._json(publication), publication['id']))


def run(args):
    _require(args.config.is_absolute())
    operator = DbtAuditRecovery(args.config)
    if args.command.endswith('-plan'):
        return operator.plan(args.publication_id, args.idempotency_key)
    if args.command.endswith('-inspect'):
        return operator.inspect(args.operation_id)
    return operator.apply(args.operation_id, args.expected_revision,
        expected_intent_digest=args.expected_intent_digest, reconcile=args.command.endswith('-reconcile'))
