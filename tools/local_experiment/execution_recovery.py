"""Bounded operator abandonment of UNKNOWN read-only Job executions.

Docker evidence closes execution, never the historical query outcome. All
external commands live in this exact adapter; no client-supplied proof seam.
"""
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
from urllib.parse import urlsplit
import uuid

import catalog_admin
import control_store
import data_management as registry
import db_jobs
import query_service
from query_context import QueryError, ResolvedQueryContext
import request_auth
import runtime_profile
import warehouse_admission as admission
import warehouse_operations
import local_gitops

ROUTE = 'recover-job'
CLOSURE = 'FENCED_READ_ONLY'
ABANDONED = 'READ_ONLY_EXECUTION_ABANDONED'
TERMINAL_PARENT = 'TERMINAL_PARENT'
HISTORICAL = 'HISTORICAL_STOP_FENCE'
PARENT_CLOSURE = 'TERMINAL_PARENT_ABANDONED'
MANAGED_LABELS = ('io.kubernetes.pod.uid', 'io.kubernetes.pod.namespace',
                  'io.kubernetes.pod.name', 'io.kubernetes.container.name')


def _labels(row):
    return {k: row['Config']['Labels'].get(k) for k in MANAGED_LABELS}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _require(condition, code='RECOVERY_INELIGIBLE'):
    if not condition:
        raise QueryError(code, 409)


def _one(db, sql, parameters):
    rows = db.execute(sql, parameters).fetchall()
    _require(len(rows) == 1)
    return dict(rows[0])


def _time(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    _require(result.tzinfo is not None)
    return result


def graph(db, run_id):
    """Inspect all durable links, including children omitted from admission trees."""
    run = _one(db, 'SELECT * FROM db_job_runs WHERE id=?', (run_id,))
    _require(run['status'] == 'unknown')
    context = ResolvedQueryContext(**json.loads(run['context']))
    _require(context.warehouse_id == 'LOCAL' and context.selected_role == run['selected_role'])
    binding = _one(db, 'SELECT * FROM catalog_bindings WHERE database_id=?', (context.database_id,))
    database = _one(db, 'SELECT * FROM databases WHERE id=?', (context.database_id,))
    schema = _one(db, 'SELECT * FROM schemas WHERE id=?', (context.schema_id,))
    control = admission.control(db, context.warehouse_id)
    _require(binding['desired_revision'] == context.binding_revision and
             binding['desired_revision'] == binding['applied_revision'] and
             binding['applied_generation_id'] == context.generation_id == control['applied_generation_id'] and
             database['sql_catalog'] == context.sql_catalog and schema['database_id'] == context.database_id and
             schema['sql_name'] == context.sql_schema)
    generation = _one(db, 'SELECT * FROM warehouse_config_generations WHERE id=?', (context.generation_id,))
    _require(generation['state'] == 'APPLIED' and generation['warehouse_id'] == context.warehouse_id)
    tree = [dict(row) for row in db_jobs._descendants(db, run_id)]
    _require(tree and tree[0]['state'] == 'UNKNOWN' and tree[0]['parent_id'] is None)
    root = tree[0]
    # Legacy NULL scope is proved by the canonical context and links below;
    # a typed admission must agree and is never repaired or inferred by name.
    _require(all(a['actor'] == run['owner'] and a['warehouse_id'] == context.warehouse_id and
                 (a['database_id'] is None or a['database_id'] == context.database_id) and
                 a['generation_id'] == context.generation_id and not a['execution_closure'] for a in tree))
    steps = [dict(r) for r in db.execute('SELECT * FROM db_job_steps WHERE run_id=? ORDER BY position', (run_id,))]
    definition = json.loads(run['definition'])
    _require(len(steps) == len(definition['steps']))
    links = [dict(r) for r in db.execute('SELECT * FROM db_job_operations WHERE run_id=? ORDER BY position,operation_id', (run_id,))]
    operations, queries, query_links, executions = [], [], [], []
    expected_admissions = {root['id']}
    unresolved_queries, unresolved_operations = [], []
    for link in links:
        position = link['position']
        _require(type(position) is int and 1 <= position <= len(steps))
        step, saved = steps[position - 1], definition['steps'][position - 1]
        _require(step['position'] == position and step['action'] == saved['action'] and step['name'] == saved['name'])
        op = _one(db, 'SELECT * FROM dataops_operations WHERE id=?', (link['operation_id'],))
        a = _one(db, "SELECT * FROM warehouse_admissions WHERE kind='dataops' AND subject_id=?", (op['id'],))
        _require(a['parent_id'] == root['id'] and op['actor'] == run['owner'] and
                 op['context'] == run['context'] and op['backend_username'] == run['backend_username'] and
                 op['credential_snapshot_ref'] == run['credential_snapshot_ref'])
        expected_admissions.add(a['id'])
        operations.append(op)
        children = [dict(r) for r in db.execute('SELECT * FROM dataops_queries WHERE operation_id=? ORDER BY step', (op['id'],))]
        unresolved = admission.execution_unresolved(a) or op['state'] not in admission.TERMINAL
        if unresolved:
            _require(a['state'] == op['state'] == 'UNKNOWN' and children and step['status'] == 'unknown')
            _require(saved['action'] in ('sql', 'alert') and saved.get('mode', 'read-only') == 'read-only' and op['route'] == 'job-sql')
            payload = json.loads(op['payload'])
            sql, _ = db_jobs.legacy._render(saved['sql'], definition.get('parameters', {}), json.loads(run['arguments']))
            _require(payload == dict(context=json.loads(run['context']),
                payload=dict(action=saved['action'], sql=sql, mode='read-only')))
            _require(hashlib.sha256(op['payload'].encode()).hexdigest() == op['fingerprint'])
            _require(not op['committed'])
            import warehouse_resident
            warehouse_resident.check_sql_policy(sql, 'read-only', context.selected_role,
                                                context=context, database_only=True)
            unresolved_operations.append(op['id'])
        else:
            _require(a['state'] == op['state'] and step['status'] in ('succeeded', 'failed', 'canceled'))
        for child in children:
            q = _one(db, 'SELECT * FROM query_records WHERE id=?', (child['query_id'],))
            e = _one(db, 'SELECT * FROM query_execution_contexts WHERE query_id=?', (q['id'],))
            qa = _one(db, "SELECT * FROM warehouse_admissions WHERE kind='query' AND subject_id=?", (q['id'],))
            _require(qa['parent_id'] == a['id'] and q['actor'] == run['owner'] and
                     all(q[k] == getattr(context, k) for k in ('database_id', 'schema_id', 'warehouse_id', 'generation_id')) and
                     all(e[k] == getattr(context, k) for k in ('selected_role', 'sql_catalog', 'sql_schema', 'binding_revision', 'grant_revision')) and
                     e['backend_username'] == run['backend_username'] and
                     e['credential_snapshot_ref'] == run['credential_snapshot_ref'] and
                     e['request_id'] == op['id'] and e['worksheet_id'] == child['step'] and e['mode'] == 'dataops')
            if unresolved:
                _require(e['sql'] == sql and child['step'] == 'sql' and
                         child['action'] == ('job_alert' if saved['action'] == 'alert' else 'job_sql') and
                         child['commit_result'] is None)
            if admission.execution_unresolved(qa) or q['state'] not in admission.TERMINAL:
                _require(unresolved and qa['state'] == q['state'] == 'UNKNOWN')
                unresolved_queries.append(q['id'])
            else:
                _require(qa['state'] == q['state'])
            expected_admissions.add(qa['id'])
            queries.append(q)
            executions.append(e)
            query_links.append(child)
        # Detect execution records absent from the nominal DataOps link set.
        _require({r[0] for r in db.execute('SELECT query_id FROM query_execution_contexts WHERE request_id=?', (op['id'],))}
                 == {c['query_id'] for c in children})
    _require(all(s['status'] in ('succeeded', 'failed', 'canceled', 'unknown', 'skipped', 'pending') and
                 (s['status'] != 'pending' or not any(l['position'] == s['position'] for l in links)) and
                 (s['status'] != 'unknown' or any(l['position'] == s['position'] for l in links)) for s in steps))
    _require(unresolved_operations and expected_admissions == {a['id'] for a in tree})
    if not unresolved_queries:
        # Normal cancel/reconcile/response paths own terminal evidence. ACTIVE
        # -> terminal may have no evidence_ref; UNKNOWN -> terminal requires it
        # in admission.finish. Never infer that history from an absent ref.
        terminal = {a['subject_id']: a for a in tree if a['kind'] == 'query'}
        _require(queries and all(q['state'] == terminal[q['id']]['state'] and
            q['state'] in admission.TERMINAL and terminal[q['id']]['finished_at'] and
            query_service._backend_id(e['backend_query_id'])
            for q, e in zip(queries, executions)))
    _require({a['id'] for a in admission.blockers(db)} == {a['id'] for a in tree if admission.execution_unresolved(a)},
             'UNRELATED_UNRESOLVED_WORK')
    # Other warehouses and legacy/global Jobs are also outside this fence.
    _require(not db.execute("SELECT 1 FROM warehouse_admissions WHERE warehouse_id!='LOCAL' AND state IN ('ACTIVE','UNKNOWN')").fetchone())
    _require(not db.execute("SELECT 1 FROM runs WHERE status IN ('queued','running','unknown')").fetchone())
    _require(not db.execute("SELECT 1 FROM db_job_runs WHERE id!=? AND status IN ('queued','running','unknown')", (run_id,)).fetchone())
    return dict(run=run, binding=binding, database=database, schema=schema, generation=generation,
        admissions=tree, steps=steps, links=links, operations=operations, queries=queries,
        executions=executions, query_links=query_links, query_ids=sorted(unresolved_queries),
        operation_ids=sorted(unresolved_operations))


def _mode(intent):
    # Pre-mode intents keep their original graph and FENCE semantics verbatim.
    mode = intent.get('mode', 'FENCE')
    if mode == HISTORICAL:
        _require(intent['graph'].get('subject') == 'query' and len(intent['graph']['query_ids']) == 1)
    else:
        _require(mode == ('FENCE' if intent['graph']['query_ids'] else TERMINAL_PARENT))
    return mode


def _nano(value):
    # Ledger timestamps have microsecond precision; avoid float rounding when
    # comparing them with the daemon's integer nanosecond journal.
    delta = _time(value) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000


def _snapshot(path, container_id):
    _require(isinstance(container_id, str) and re.fullmatch('[0-9a-f]{64}', container_id) is not None,
             'RECOVERY_HISTORICAL_SELECTOR')
    binding, _ = _historical_file(path)
    return dict(binding, container_id=container_id)


def _historical_file(path):
    path = Path(path)
    _require(path.is_absolute(), 'RECOVERY_HISTORICAL_SELECTOR')
    info = path.lstat()
    _require(path.is_absolute() and stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and
             stat.S_IMODE(info.st_mode) == 0o600 and stat.S_IMODE(path.parent.stat().st_mode) == 0o700,
             'RECOVERY_HISTORICAL_SELECTOR')
    birth = getattr(info, 'st_birthtime_ns', int(getattr(info, 'st_birthtime', 0) * 1e9))
    _require(birth > 0, 'RECOVERY_ORIGINAL_BIRTHTIME_REQUIRED')
    raw = path.read_bytes()
    after = path.lstat()
    _require(all(getattr(info, key) == getattr(after, key) for key in
        ('st_dev', 'st_ino', 'st_uid', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns')), 'RECOVERY_SNAPSHOT_CHANGED')
    return dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), stat=dict(
        device=info.st_dev, inode=info.st_ino, uid=info.st_uid, mode=stat.S_IMODE(info.st_mode),
        size=info.st_size, birth_ns=birth, mtime_ns=info.st_mtime_ns, ctime_ns=info.st_ctime_ns)), raw


def _historical_read(binding):
    current, raw = _historical_file(binding['path'])
    _require(current == {k: v for k, v in binding.items() if k != 'container_id'}, 'RECOVERY_SNAPSHOT_CHANGED')
    return json.loads(raw)


def _producer(graph, selector):
    intent, result = (_historical_read(selector['producer'][k]) for k in ('intent', 'result'))
    q, execution = graph['queries'][0], graph['executions'][0]
    _require(intent.get('actor') == q['actor'] and type(intent.get('calls')) is int and intent['calls'] == 1,
             'RECOVERY_ORIGINAL_SUBMISSION_UNPROVED')
    argv = intent['argv']
    _require(isinstance(argv, list) and argv[:2] == ['rtk', 'proxy'] and Path(argv[2]).is_absolute() and
             argv[3:6] == ['warehouse', 'query', 'LOCAL'])
    options, index = {}, 6
    while index < len(argv):
        key = argv[index]
        _require(key not in options and key in ('--catalog', '--schema', '--mode', '--max-rows', '--sql', '--no-auto-wake'))
        if key == '--no-auto-wake':
            options[key], index = True, index + 1
        else:
            _require(index + 1 < len(argv))
            options[key], index = argv[index + 1], index + 2
    _require(all(options.get(k) == v for k, v in {'--catalog': execution['sql_catalog'],
        '--schema': execution['sql_schema'], '--mode': 'read-only', '--sql': execution['sql']}.items()) and
        options.get('--no-auto-wake') is True and str(options.get('--max-rows', '')).isdigit())
    _require(type(result.get('exit')) is int and result['exit'] == 1 and result.get('stdout') == '' and
             result.get('stderr', '').strip() == 'resident warehouse failed: Authenticated SQL endpoint request failed',
             'RECOVERY_ORIGINAL_SUBMISSION_UNPROVED')
    start, end = (selector['producer'][k]['stat'] for k in ('intent', 'result'))
    created = [_nano(r['created_at']) for r in graph['queries'] + graph['admissions']]
    _require(_nano(intent['at']) <= start['birth_ns'] <= start['mtime_ns'] <= min(created) and
             start['ctime_ns'] <= min(created) and
             max(created) <= end['birth_ns'] <= end['mtime_ns'], 'RECOVERY_ORIGINAL_SUBMISSION_UNPROVED')
    return dict(kind='original-cli-failed-once', intent_at=intent['at'], calls=1,
                window_start_ns=start['birth_ns'], window_end_ns=max(end['mtime_ns'], end['ctime_ns']),
                files=selector['producer'], outcome='UNKNOWN')


def _image_digest(value, pinned):
    repository, digest = pinned.split('@')
    _require(re.fullmatch(r'sha256:[0-9a-f]{64}', digest) is not None)
    repository = repository.removeprefix('docker.io/')
    value = value.removeprefix('docker-pullable://')
    if value.startswith('sha256:'):
        _require(value == digest, 'RECOVERY_IMAGE_CHANGED')
    else:
        _require(value.removeprefix('docker.io/') == repository + '@' + digest, 'RECOVERY_IMAGE_CHANGED')
    return digest


def _historical_member(member, index, producer, planned_at):
    scope, events = member['scope'], member['events']
    captured = member['snapshot']['stat']
    _require(captured['birth_ns'] <= captured['mtime_ns'] <= producer['window_start_ns'] and
             captured['ctime_ns'] <= producer['window_start_ns'])
    actions = [e['action'] for e in events]
    times = [e['time_nano'] for e in events]
    _require(times and all(type(t) is int for t in times) and
             all(a < b for a, b in zip(times, times[1:])) and times[-1] <= _nano(planned_at),
             'RECOVERY_STOP_UNPROVED')
    terminal = [e for e in events if e['action'] in ('stop', 'die', 'destroy')]
    _require([e['action'] for e in terminal] in (['stop', 'die', 'destroy'], ['die', 'stop', 'destroy']),
             'RECOVERY_STOP_UNPROVED')
    die = next(e for e in terminal if e['action'] == 'die')
    _require(re.fullmatch(r'[0-9]{1,3}', str(die['exit_code'])) and int(die['exit_code']) <= 255)
    _require(times[0] >= _nano(scope['pod_created_at']))
    if index == 0:
        saved = member['saved_start']
        _require(saved['kind'] == 'pod-running-startedAt' and saved['snapshot_sha256'] == member['snapshot']['sha256'] and
                 saved['container_id'] == scope['container_id'] and saved['pod_uid'] == scope['pod_uid'])
        started = _nano(saved['started_at'])
        _require(_nano(scope['pod_created_at']) <= started <= member['snapshot']['stat']['mtime_ns'] and
                 started < terminal[0]['time_nano'] and times[-1] < producer['window_start_ns'])
        prefix = actions[:-3]
        _require(prefix in ([], ['create'], ['start'], ['create', 'start']) and actions[-3:] == [e['action'] for e in terminal],
                 'RECOVERY_STOP_UNPROVED')
        for e in events[:-3]:
            if e['action'] == 'start':
                # Kubernetes running.startedAt is normally recorded to seconds.
                _require(e['time_nano'] // 1_000_000_000 == started // 1_000_000_000)
            else:
                _require(e['time_nano'] < (started // 1_000_000_000 + 1) * 1_000_000_000)
    else:
        _require(actions in (['create', 'start', 'stop', 'die', 'destroy'],
                             ['create', 'start', 'die', 'stop', 'destroy']), 'RECOVERY_STOP_UNPROVED')
        _require(times[1] < producer['window_start_ns'] and min(e['time_nano'] for e in terminal) > producer['window_end_ns'])


def _historical_claim(intent):
    """Validate the saved positive claim without depending on journal retention."""
    proof, graph, selector = intent['observation'], intent['graph'], intent['selector']
    _require(proof['kind'] == 'historical-exact-events' and proof['version'] == 2 and
             isinstance(proof['engine_id'], str) and bool(proof['engine_id']) and len(proof['members']) == 2)
    producer = proof['producer']
    _require(producer['kind'] == 'original-cli-failed-once' and producer['calls'] == 1 and
             producer['outcome'] == 'UNKNOWN' and producer['files'] == selector['producer'])
    lower = selector['producer']['intent']['stat']
    upper = selector['producer']['result']['stat']
    created = [_nano(r['created_at']) for r in graph['queries'] + graph['admissions']]
    _require(_nano(producer['intent_at']) <= lower['birth_ns'] <= lower['mtime_ns'] <= min(created) and
             lower['ctime_ns'] <= min(created) and
             max(created) <= upper['birth_ns'] <= upper['mtime_ns'] and
             producer['window_start_ns'] == lower['birth_ns'] and
             producer['window_end_ns'] == max(upper['mtime_ns'], upper['ctime_ns']))
    for index, member in enumerate(proof['members']):
        scope, events = member['scope'], member['events']
        _require(member['snapshot'] == selector['members'][index] and
                 scope['container_id'] == member['snapshot']['container_id'] and
                 scope['role'] == ('possible-predecessor' if index == 0 else 'historical-original') and
                 hashlib.sha256(_json(events).encode()).hexdigest() == member['events_digest'])
        _historical_member(member, index, producer, intent['planned_at'])
        _require(all(e['container_id'] == scope['container_id'] and e['labels'] == scope['labels'] and
                     e['image'] == scope['image'] == _image_digest(scope['image'], scope['pinned_image']) for e in events))
        if index == 1:
            _require(max(e['time_nano'] for e in proof['members'][0]['events'] if e['action'] in ('stop', 'die')) <
                     next(e['time_nano'] for e in events if e['action'] == 'start'))
    replacement = proof['replacement_scope']
    _require(proof['historical_scope'] == intent['scope'] == dict(proof['members'][1]['scope'], engine_id=proof['engine_id']) and
             replacement == intent['replacement_scope'] and replacement['engine_id'] == proof['engine_id'] and
             replacement['role'] == 'replacement' and
             producer['window_end_ns'] < _nano(replacement['started_at']) <= _nano(intent['planned_at']))
    scopes = [m['scope'] for m in proof['members']] + [replacement]
    for scope in scopes:
        _require(scope['generation_id'] == graph['generation']['id'] and
                 scope['namespace'] == scope['labels']['io.kubernetes.pod.namespace'] and
                 scope['labels']['io.kubernetes.pod.uid'] == scope['pod_uid'] and
                 scope['labels']['io.kubernetes.container.name'] == 'trino' and scope['replica_set_uid'] and
                 scope['pinned_image'] == replacement['pinned_image'] and
                 _image_digest(scope['image'], scope['pinned_image']) == replacement['image'])
        _require(scope['configmaps'] and all(cm['current_uid'] and
            cm['name'] == cm['generator'] + '-' + cm['name_hash'] and
            re.fullmatch('[2456789bcdfghkmt]{10}', cm['name_hash']) and
            re.fullmatch('[0-9a-f]{64}', cm['current_data_digest']) and
            _time(cm['current_created_at']) <= _time(scope['pod_created_at']) for cm in scope['configmaps']))
    _require(len({s['container_id'] for s in scopes}) == len({s['pod_uid'] for s in scopes}) == 3 and
             len({s['deployment_uid'] for s in scopes}) == 1 and
             all(s['configmaps'] == replacement['configmaps'] for s in scopes))


def _historical_subset(member, planned, producer, until):
    """Fresh retained A events must be an exact ordered subset, anchored by destroy."""
    _require({k: v for k, v in member.items() if k not in ('events', 'events_digest')} ==
             {k: v for k, v in planned.items() if k not in ('events', 'events_digest')})
    events = member['events']
    _require(events and events[-1] == planned['events'][-1] and events[-1]['action'] == 'destroy' and
             events[-1]['time_nano'] < producer['window_start_ns'] and events[-1]['time_nano'] <= _nano(until),
             'RECOVERY_STOP_UNPROVED')
    _require(all(e in planned['events'] for e in events), 'RECOVERY_HISTORICAL_CHANGED')
    indices = [planned['events'].index(e) for e in events]
    _require(all(a < b for a, b in zip(indices, indices[1:])) and
             member['events_digest'] == hashlib.sha256(_json(events).encode()).hexdigest(), 'RECOVERY_HISTORICAL_CHANGED')


def _historical_reference(db, reference):
    """Only an actual typed immutable recovery row supplies the original history."""
    reference = dict(reference)
    _require(reference.get('operation_id') and 'intent' in reference, 'RECOVERY_INTENT_MISMATCH')
    row = _one(db, 'SELECT * FROM execution_recovery_requests WHERE operation_id=?', (reference['operation_id'],))
    _require(row == reference and row['route'] == 'recover-query', 'RECOVERY_INTENT_MISMATCH')
    intent, payload = json.loads(row['intent']), json.loads(row['payload'])
    _historical_claim(intent)
    _require(intent['mode'] == HISTORICAL and payload['mode'] == HISTORICAL and payload['subject'] == 'query' and
             payload['query_id'] == intent['graph']['queries'][0]['id'] and payload['selector'] == intent['selector'] and
             row['actor'] == intent['graph']['queries'][0]['actor'])
    operation = _one(db, 'SELECT * FROM operations WHERE id=?', (row['operation_id'],))
    _require(operation['actor'] == row['actor'] and operation['action'] == 'QUERY_EXECUTION_RECOVERY' and
             operation['state'] == row['phase'])
    claims = db.execute("""SELECT count(*) FROM execution_recovery_requests WHERE route='recover-query'
        AND phase IN ('CLOSED','OPEN') AND CASE WHEN json_valid(payload)
        THEN json_extract(payload,'$.query_id') END=?""", (payload['query_id'],)).fetchone()[0]
    _require(claims == (1 if row['phase'] in ('CLOSED', 'OPEN') else 0), 'RECOVERY_CLAIM_AMBIGUOUS')
    return intent


def _historical_proof(row, intent, fresh):
    """Legacy v3 saved-proof validation; not used to acquire APPLY evidence."""
    planned = intent['observation']
    if fresh == planned:
        return fresh
    _historical_subset(fresh['members'][0], planned['members'][0], planned['producer'], intent['planned_at'])
    _require(dict(fresh, members=[planned['members'][0]] + fresh['members'][1:]) == planned,
             'RECOVERY_HISTORICAL_CHANGED')
    return dict(kind='historical-exact-events', version=3,
        reference=dict(operation_id=row['operation_id'], intent_digest=QueryRecovery.digest(row),
            observation_digest=hashlib.sha256(_json(planned).encode()).hexdigest()),
        acquisition='fresh A subset with positive exact destroy; omitted plan events were not reacquired',
        fresh_observation=fresh)


def _consumed_historical_proof(row, intent, fresh):
    planned = intent['observation']
    _require(fresh == dict(engine_id=planned['engine_id'],
        old_container_ids_absent=[m['scope']['container_id'] for m in planned['members']],
        replacement_scope=planned['replacement_scope'], producer=planned['producer']), 'RECOVERY_HISTORICAL_CHANGED')
    return dict(kind='historical-exact-events', version=4,
        reference=dict(operation_id=row['operation_id'], intent_digest=QueryRecovery.digest(row),
            observation_digest=hashlib.sha256(_json(planned).encode()).hexdigest()),
        terminal_basis='official PLAN-time daemon observations consumed', event_history_reacquired=False,
        fresh_current=fresh)


def _saved_historical_proof(row, intent, proof):
    if proof == intent['observation']:
        return
    if proof.get('kind') == 'historical-exact-events' and proof.get('version') == 4:
        _require(_consumed_historical_proof(row, intent, proof['fresh_current']) == proof, 'RECOVERY_HISTORICAL_CHANGED')
        return
    _require(proof.get('kind') == 'historical-exact-events' and proof.get('version') == 3,
             'RECOVERY_HISTORICAL_CHANGED')
    _require(_historical_proof(row, intent, proof['fresh_observation']) == proof, 'RECOVERY_HISTORICAL_CHANGED')


def completed_historical_query(db, row, intent, proof):
    """Read-only saved closure predicate; return its admission ID or raise QueryError.

    Authentication belongs to the caller. No current head, runtime, file or
    daemon observation is required for an already saved CLOSED/OPEN fact.
    """
    row = dict(row)
    _require(_one(db, 'SELECT * FROM execution_recovery_requests WHERE operation_id=?', (row['operation_id'],)) == row)
    _require(row['route'] == 'recover-query' and row['intent'] == _json(intent) and row['proof'] == _json(proof) and
             intent['mode'] == HISTORICAL)
    frozen = intent['graph']
    _require(frozen['subject'] == 'query' and len(frozen['queries']) == len(frozen['executions']) == len(frozen['admissions']) == 1 and
             frozen['operation_ids'] == [])
    query, execution, original = frozen['queries'][0], frozen['executions'][0], frozen['admissions'][0]
    query_id = query['id']
    payload = json.loads(row['payload'])
    _require(set(payload) == {'subject','query_id','config','runtime_binding','mode','selector'} and
             payload['subject'] == 'query' and payload['query_id'] == query_id and payload['mode'] == HISTORICAL and
             payload['selector'] == intent['selector'] and frozen['query_ids'] == [query_id])
    _require(row['actor'] == query['actor'] == original['actor'] and query['state'] == original['state'] == 'UNKNOWN' and
             execution['query_id'] == original['subject_id'] == query_id and original['kind'] == 'query' and
             original['parent_id'] is None and original['execution_closure'] == '' and
             execution['mode'] == 'read-only' and execution['backend_query_id'] is None)
    _require(query['database_id'] == original['database_id'] == frozen['database']['id'] == frozen['schema']['database_id'] and
             query['schema_id'] == frozen['schema']['id'] and query['warehouse_id'] == original['warehouse_id'] == 'LOCAL' and
             query['generation_id'] == original['generation_id'] == frozen['generation']['id'] ==
                 frozen['binding']['applied_generation_id'] and execution['sql_catalog'] == frozen['database']['sql_catalog'] and
             execution['sql_schema'] == frozen['schema']['sql_name'] and
             execution['binding_revision'] == frozen['binding']['desired_revision'] == frozen['binding']['applied_revision'])
    claims = db.execute("""SELECT count(*) FROM execution_recovery_requests WHERE route='recover-query'
        AND phase IN ('CLOSED','OPEN') AND CASE WHEN json_valid(payload)
        THEN json_extract(payload,'$.query_id') END=?""", (query_id,)).fetchone()[0]
    _require(claims == 1, 'RECOVERY_CLAIM_AMBIGUOUS')
    for table, column in (('dataops_operations','id'), ('db_job_operations','operation_id'), ('db_job_runs','id')):
        _require(not db.execute('SELECT 1 FROM ' + table + ' WHERE ' + column + ' IN (?,?)',
                               (query_id, execution['request_id'])).fetchone())
    _historical_claim(intent)
    _require(row['phase'] in ('CLOSED', 'OPEN'))
    _saved_historical_proof(row, intent, proof)
    for table, records, key in (('query_records', frozen['queries'], 'id'),
                                 ('query_execution_contexts', frozen['executions'], 'query_id')):
        for record in records:
            _require(_one(db, 'SELECT * FROM ' + table + ' WHERE ' + key + '=?', (record[key],)) == record,
                     'RECOVERY_GRAPH_CHANGED')
    admission_record = dict(frozen['admissions'][0], execution_closure=CLOSURE, evidence_ref=row['operation_id'])
    _require(_one(db, 'SELECT * FROM warehouse_admissions WHERE id=?', (admission_record['id'],)) == admission_record)
    _require(db.execute("SELECT count(*) FROM warehouse_admissions WHERE kind='query' AND subject_id=?",
                        (frozen['queries'][0]['id'],)).fetchone()[0] == 1 and
             db.execute('SELECT count(*) FROM warehouse_admissions WHERE evidence_ref=? AND execution_closure=?',
                        (row['operation_id'], CLOSURE)).fetchone()[0] == 1 and
             not db.execute('SELECT 1 FROM warehouse_admissions WHERE parent_id=?', (admission_record['id'],)).fetchone())
    _require(not db.execute('SELECT 1 FROM dataops_queries WHERE query_id=?', (frozen['queries'][0]['id'],)).fetchone())
    operation = _one(db, 'SELECT * FROM operations WHERE id=?', (row['operation_id'],))
    _require(operation['actor'] == row['actor'] and operation['action'] == 'QUERY_EXECUTION_RECOVERY' and operation['state'] == row['phase'])

    return admission_record['id']


def _terminal_proof(frozen):
    executions = {e['query_id']: e for e in frozen['executions']}
    admissions = {a['subject_id']: a for a in frozen['admissions'] if a['kind'] == 'query'}
    return dict(kind='canonical-terminal-children', queries=[dict(query_id=q['id'], state=q['state'],
        backend_query_id=executions[q['id']]['backend_query_id'],
        finished_at=admissions[q['id']]['finished_at'], evidence_ref=admissions[q['id']]['evidence_ref'])
        for q in sorted(frozen['queries'], key=lambda q: q['id'])])


class DockerAdapter:
    """Exact Docker/Colima single-backend fence; every subprocess has a deadline."""
    def __init__(self, config, profile, bundle):
        self.config, self.profile, self.bundle = config, profile, bundle
        self.expected = json.loads(bundle['files']['deploy/local-experiment/trino/deployment.yaml'])
        self.namespace = json.loads(bundle['files']['deploy/local-experiment/trino/secure/kustomization.yaml'])['namespace']
        self.deployment = self.expected['metadata']['name']
        self.container = 'trino'
        _require(self.namespace == 'shirokuma-dev' and self.deployment == 'trino-local')

    def _run(self, program, *args, input=None):
        command = (['rtk', 'proxy', 'docker', '--context', self.config['docker_context']] if program == 'docker'
                   else ['rtk', 'proxy', 'kubectl', '--context', self.config['kube_context']])
        env = {k: v for k, v in os.environ.items() if k not in
               ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH')}
        result = subprocess.run(command + list(args), capture_output=True, text=True, timeout=20, env=env, input=input)
        _require(result.returncode == 0, 'RECOVERY_BACKEND_UNAVAILABLE')
        return result.stdout

    def _docker(self, *args):
        return json.loads(self._run('docker', *args))

    def _kube(self, resource, *args):
        return json.loads(self._run('kubectl', '-n', self.namespace, 'get', resource, *args, '-o', 'json'))

    def engine(self):
        context = self._docker('context', 'inspect', self.config['docker_context'])
        expected = 'unix://' + str(Path.home() / '.colima/mac-studio-solo/docker.sock')
        _require(len(context) == 1 and context[0]['Name'] == 'colima-mac-studio-solo' and
                 context[0]['Endpoints']['docker']['Host'] == expected, 'UNTRUSTED_DOCKER_CONTEXT')
        endpoint = json.loads(self._run('kubectl', 'config', 'view', '--minify', '-o', 'json'))
        clusters = endpoint['clusters']
        _require(len(clusters) == 1)
        server = urlsplit(clusters[0]['cluster']['server'])
        _require(server.scheme == 'https' and ipaddress.ip_address(server.hostname).is_loopback)
        engine = self._docker('info', '--format', '{{json .ID}}')
        _require(isinstance(engine, str) and bool(engine))
        return dict(engine_id=engine, socket=expected)

    def scope(self, graph, *, replacement=False):
        engine = self.engine()
        generation = graph['generation']['id']
        deployment = self._kube('deployment', self.deployment)
        _require(deployment['metadata']['annotations'].get('shirokuma.dev/database-generation') == generation)
        _require(deployment['spec']['replicas'] == 1)
        selector = deployment['spec']['selector']['matchLabels']
        _require(selector)
        pods = self._kube('pods', '-l', ','.join(k + '=' + v for k, v in sorted(selector.items())))['items']
        _require(len(pods) == 1 and not pods[0]['metadata'].get('deletionTimestamp'))
        pod = pods[0]
        owners = pod['metadata'].get('ownerReferences', [])
        _require(len(owners) == 1 and owners[0]['kind'] == 'ReplicaSet' and owners[0].get('controller') is True)
        rs = self._kube('replicaset', owners[0]['name'])
        _require(rs['metadata']['uid'] == owners[0]['uid'] and
                 any(o['uid'] == deployment['metadata']['uid'] and o['kind'] == 'Deployment' and o.get('controller') is True
                     for o in rs['metadata']['ownerReferences']))
        containers = pod['spec']['containers']
        _require({c['name'] for c in containers} <= {'trino', 'opa'} and
                 sum(c['name'] == 'trino' for c in containers) == 1 and not pod['spec'].get('initContainers'))
        trino = next(c for c in containers if c['name'] == 'trino')
        expected_containers = self.expected['spec']['template']['spec']['containers']
        _require({c['name'] for c in containers} == {c['name'] for c in expected_containers})
        for c in containers:
            expected = next(e for e in expected_containers if e['name'] == c['name'])
            _require(all(c.get(k) == expected.get(k) for k in ('image', 'command', 'args')))
        _require(pod['metadata'].get('annotations', {}).get('shirokuma.dev/database-generation') == generation)
        _require(trino.get('command') == ['/usr/lib/trino/bin/run-trino'] and not trino.get('args'))
        _require(trino['image'] == deployment['spec']['template']['spec']['containers'][0]['image'])
        statuses = [c for c in pod['status']['containerStatuses'] if c['name'] == 'trino']
        _require(len(statuses) == 1 and statuses[0]['containerID'].startswith('docker://'))
        _require(statuses[0].get('ready') is True and any(
            c.get('type') == 'Ready' and c.get('status') == 'True' for c in pod['status'].get('conditions', [])))
        container_id = statuses[0]['containerID'][9:]
        _require(re.fullmatch('[0-9a-f]{64}', container_id))
        inspected = self._docker('inspect', container_id)
        _require(len(inspected) == 1)
        inspected = inspected[0]
        labels = _labels(inspected)
        _require(inspected['Id'] == container_id and
                 labels.get('io.kubernetes.pod.uid') == pod['metadata']['uid'] and
                 labels.get('io.kubernetes.pod.namespace') == self.namespace and
                 labels.get('io.kubernetes.pod.name') == pod['metadata']['name'] and
                 labels.get('io.kubernetes.container.name') == 'trino' and
                 inspected['HostConfig']['RestartPolicy']['Name'] == 'no' and
                 inspected['State']['Running'] is True)
        # Check actual mounted runtime configuration, rather than query mode.
        mounts = [v for v in trino.get('volumeMounts', []) if v['mountPath'] == '/etc/trino']
        _require(len(mounts) == 1 and not mounts[0].get('subPath'))
        volumes = [v for v in pod['spec']['volumes'] if v['name'] == mounts[0]['name']]
        _require(len(volumes) == 1 and 'configMap' in volumes[0])
        configmap = self._kube('configmap', volumes[0]['configMap']['name'])
        expected_config = self.bundle['files']['deploy/local-experiment/trino/config.properties']
        if isinstance(expected_config, bytes):
            expected_config = expected_config.decode()
        _require(configmap['data']['config.properties'] == expected_config)
        properties = {}
        for line in configmap['data']['config.properties'].splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                _require(key.strip() not in properties)
                properties[key.strip()] = value.strip()
        _require(properties.get('coordinator') == 'true' and properties.get('node-scheduler.include-coordinator') == 'true'
                 and properties.get('discovery.uri') == 'http://localhost:8080')
        # Inspect all namespace pods and the sole service endpoint: a worker or
        # foreign endpoint cannot be silently excluded by the deployment selector.
        all_pods = self._kube('pods', '--all-namespaces')['items']
        for other in all_pods:
            if other['metadata']['uid'] != pod['metadata']['uid']:
                _require(not any('trino' in c.get('image', '').lower() or 'trino' in ' '.join(c.get('command', []))
                                 for c in other['spec']['containers']))
        endpoints = self._kube('endpoints', self.deployment)
        service = self._kube('service', self.deployment)
        _require(service['spec']['selector'] == selector and
                 service['metadata'].get('annotations', {}).get('shirokuma.dev/database-generation') == generation)
        addresses = [a for s in endpoints.get('subsets', []) for a in s.get('addresses', [])]
        _require(len(addresses) == 1 and addresses[0]['targetRef']['uid'] == pod['metadata']['uid'] and
                 not any(s.get('notReadyAddresses') for s in endpoints.get('subsets', [])))
        processes = self._run('docker', 'top', container_id, '-eo', 'pid,comm,args').splitlines()[1:]
        _require(sum('java' in p and 'io.trino.server.TrinoServer' in p for p in processes) == 1 and
                 sum('java' in p for p in processes) == 1)
        # Inspect the trusted Engine, including digest-only/default-command
        # workers which cannot be classified from the Kubernetes pod spec.
        ids = self._run('docker', 'ps', '--no-trunc', '--format', '{{.ID}}').splitlines()
        _require(container_id in ids and len(ids) <= 256 and all(re.fullmatch('[0-9a-f]{64}', i) for i in ids))
        for other in self._docker('inspect', *ids):
            if other['Id'] == container_id:
                continue
            managed = other.get('Config', {}).get('Labels', {}).get('io.kubernetes.pod.namespace') == self.namespace
            _require(other['Image'] != inspected['Image'])
            if managed:
                process = self._run('docker', 'top', other['Id'], '-eo', 'pid,comm,args')
                _require('io.trino.server.' not in process and 'run-trino' not in process)
        started = inspected['State']['StartedAt']
        if not replacement:
            _require(_time(started) <= min(_time(q['created_at']) for q in
                    ([graph['run']] if 'run' in graph else []) + graph['queries']))
        return dict(**engine, container_id=container_id, labels=labels, started_at=started,
            image=inspected['Image'], pod_uid=pod['metadata']['uid'], deployment_uid=deployment['metadata']['uid'],
            binding_basis='current operator-established managed scope; Engine ID was not recorded at admission')

    def _historical_pod(self, graph, pod, deployment):
        meta = pod['metadata']
        generation = graph['generation']['id']
        _require(meta['namespace'] == self.namespace and bool(meta['uid']) and
                 meta.get('annotations', {}).get('shirokuma.dev/database-generation') == generation)
        owners = meta.get('ownerReferences', [])
        _require(len(owners) == 1 and owners[0]['kind'] == 'ReplicaSet' and owners[0].get('controller') is True)
        rs = self._kube('replicaset', owners[0]['name'])
        controllers = rs['metadata'].get('ownerReferences', [])
        _require(rs['metadata']['uid'] == owners[0]['uid'] and rs['metadata']['namespace'] == self.namespace and
                 len(controllers) == 1 and controllers[0]['uid'] == deployment['metadata']['uid'] and
                 controllers[0]['kind'] == 'Deployment' and controllers[0].get('controller') is True)
        template = rs['spec']['template']
        for selector in (rs['spec']['selector']['matchLabels'], deployment['spec']['selector']['matchLabels'],
                         template['metadata']['labels']):
            _require(selector and all(meta.get('labels', {}).get(k) == v for k, v in selector.items()))
        _require(template['metadata'].get('annotations', {}).get('shirokuma.dev/database-generation') == generation)
        expected = self.expected['spec']['template']['spec']
        for spec in (pod['spec'], template['spec'], deployment['spec']['template']['spec']):
            _require(not spec.get('initContainers') and len(spec['containers']) == len(expected['containers']) and
                     {c['name'] for c in spec['containers']} == {c['name'] for c in expected['containers']})
            for canonical in expected['containers']:
                c = next(c for c in spec['containers'] if c['name'] == canonical['name'])
                _require(all(c.get(k) == canonical.get(k) for k in
                    ('image', 'command', 'args', 'env', 'envFrom', 'volumeMounts', 'workingDir')))
        _require(pod['spec']['volumes'] == template['spec']['volumes'] == deployment['spec']['template']['spec']['volumes'])
        secure = json.loads(self.bundle['files']['deploy/local-experiment/trino/secure/kustomization.yaml'])
        _require(not secure.get('generatorOptions', {}).get('disableNameSuffixHash'))
        config_refs = []
        for volume in expected['volumes']:
            actual = [v for v in pod['spec']['volumes'] if v['name'] == volume['name']]
            _require(len(actual) == 1)
            normalized = json.loads(_json(actual[0]))
            for source_kind in ('secret', 'configMap'):
                if source_kind in normalized:
                    _require(source_kind in volume)
                    if normalized[source_kind].get('defaultMode') == 420 and 'defaultMode' not in volume[source_kind]:
                        normalized[source_kind].pop('defaultMode')
                    if normalized[source_kind].get('optional') is False and 'optional' not in volume[source_kind]:
                        normalized[source_kind].pop('optional')
            if 'configMap' not in volume:
                _require(normalized == volume)
                continue
            base = volume['configMap']['name']
            name = actual[0]['configMap']['name']
            _require(re.fullmatch(re.escape(base) + r'-[2456789bcdfghkmt]{10}', name) is not None,
                     'RECOVERY_CONFIG_REFERENCE_CHANGED')
            normalized['configMap']['name'] = base
            _require(normalized == volume)
            generators = [g for g in secure.get('configMapGenerator', []) if g['name'] == base]
            _require(len(generators) == 1 and not any(generators[0].get(k) for k in ('envs', 'env', 'literals')))
            data = {}
            for source in generators[0]['files']:
                key, file = source.split('=', 1) if '=' in source else (Path(source).name, source)
                path = str(Path(os.path.normpath('deploy/local-experiment/trino/secure/' + file)))
                value = self.bundle['files'][path]
                data[key] = value.decode() if isinstance(value, bytes) else value
            cm = self._kube('configmap', name)
            _require(cm['metadata']['namespace'] == self.namespace and cm['metadata']['name'] == name and
                     bool(cm['metadata']['uid']) and cm['data'] == data and 'binaryData' not in cm and
                     _time(cm['metadata']['creationTimestamp']) <= _time(meta['creationTimestamp']))
            config_refs.append(dict(name=name, generator=base, name_hash=name[len(base) + 1:],
                current_uid=cm['metadata']['uid'], current_data_digest=hashlib.sha256(_json(data).encode()).hexdigest(),
                current_created_at=cm['metadata']['creationTimestamp'],
                basis='current canonical data verification and sealed generator/captured hashed mount reference'))
        _require(len(pod['spec']['volumes']) == len(expected['volumes']))
        properties = {}
        content = self.bundle['files']['deploy/local-experiment/trino/config.properties']
        if isinstance(content, bytes):
            content = content.decode()
        for line in content.splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                _require(key.strip() not in properties)
                properties[key.strip()] = value.strip()
        _require(properties.get('coordinator') == 'true' and properties.get('node-scheduler.include-coordinator') == 'true'
                 and properties.get('discovery.uri') == 'http://localhost:8080')
        statuses = [c for c in pod['status']['containerStatuses'] if c['name'] == 'trino']
        _require(len(statuses) == 1 and statuses[0]['containerID'].startswith('docker://'))
        cid = statuses[0]['containerID'][9:]
        _require(re.fullmatch('[0-9a-f]{64}', cid) is not None)
        pinned = next(c['image'] for c in expected['containers'] if c['name'] == 'trino')
        digest = _image_digest(statuses[0]['imageID'], pinned)
        images = self._docker('image', 'inspect', pinned)
        _require(len(images) == 1 and any(d.removeprefix('docker.io/') == pinned.removeprefix('docker.io/')
            for d in images[0].get('RepoDigests', [])), 'RECOVERY_IMAGE_CHANGED')
        return dict(container_id=cid, labels=dict(zip(MANAGED_LABELS, (meta['uid'], self.namespace, meta['name'], 'trino'))),
            pod_uid=meta['uid'], deployment_uid=deployment['metadata']['uid'], replica_set_uid=rs['metadata']['uid'],
            namespace=self.namespace, generation_id=generation, pod_created_at=meta['creationTimestamp'],
            image=digest, pinned_image=pinned, configmaps=config_refs)

    def historical_current(self, graph, deployment):
        selector = deployment['spec']['selector']['matchLabels']
        deployments = self._kube('deployments')['items']
        coordinators = [d for d in deployments if any('trino' in c.get('image', '').lower() or
            'run-trino' in ' '.join(c.get('command', [])) for c in d['spec']['template']['spec']['containers'])]
        _require(len(coordinators) == 1 and coordinators[0]['metadata']['uid'] == deployment['metadata']['uid'])
        pods = self._kube('pods', '--all-namespaces')['items']
        qualified = [p for p in pods if p['metadata']['namespace'] == self.namespace and
                     any('trino' in c.get('image', '').lower() or 'run-trino' in ' '.join(c.get('command', []))
                         for c in p['spec']['containers'])]
        _require(len(qualified) == 1 and not qualified[0]['metadata'].get('deletionTimestamp'))
        pod = qualified[0]
        _require(all(pod['metadata']['labels'].get(k) == v for k, v in selector.items()))
        scope = self._historical_pod(graph, pod, deployment)
        _require(any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in pod['status'].get('conditions', [])) and
                 all(c.get('ready') is True for c in pod['status']['containerStatuses']))
        service, endpoints = self._kube('service', self.deployment), self._kube('endpoints', self.deployment)
        services = [s for s in self._kube('services')['items'] if s['spec'].get('selector') and
                    all(s['spec']['selector'].get(k) == v for k, v in selector.items())]
        _require(len(services) == 1 and services[0]['metadata']['uid'] == service['metadata']['uid'] and
                 service['metadata']['namespace'] == self.namespace and bool(service['metadata']['uid']) and
                 service['metadata']['name'] == self.deployment and service['spec']['selector'] == selector and
                 service['metadata']['annotations'].get('shirokuma.dev/database-generation') == graph['generation']['id'])
        addresses = [a for subset in endpoints.get('subsets', []) for a in subset.get('addresses', [])]
        _require(len(addresses) == 1 and addresses[0]['targetRef']['uid'] == scope['pod_uid'] and
                 not any(s.get('notReadyAddresses') for s in endpoints.get('subsets', [])))
        ids = self._run('docker', 'ps', '--no-trunc', '--format', '{{.ID}}').splitlines()
        _require(scope['container_id'] in ids and len(set(ids)) == len(ids) <= 256 and
                 all(re.fullmatch('[0-9a-f]{64}', cid) for cid in ids))
        inspected = self._docker('inspect', *ids)
        _require({c['Id'] for c in inspected} == set(ids) and len(inspected) == len(ids))
        current = next(c for c in inspected if c['Id'] == scope['container_id'])
        _require(_labels(current) == scope['labels'] and current['State']['Running'] is True and
                 current['HostConfig']['RestartPolicy']['Name'] == 'no')
        _image_digest(current['Image'], scope['pinned_image'])
        for row in inspected:
            if row.get('Config', {}).get('Labels', {}).get('io.kubernetes.pod.namespace') != self.namespace:
                continue  # separate qualified environments are not this profile's backend
            processes = self._run('docker', 'top', row['Id'], '-eo', 'pid,comm,args').splitlines()[1:]
            if row['Id'] == scope['container_id']:
                _require(sum('java' in p and 'io.trino.server.TrinoServer' in p for p in processes) == 1 and
                         sum('java' in p for p in processes) == 1)
            else:
                _require(row['Image'] != current['Image'] and
                         not any('io.trino.server.' in p or 'run-trino' in p for p in processes))
        return dict(scope, role='replacement', started_at=current['State']['StartedAt'],
                    service_uid=service['metadata']['uid'], service_selector=selector,
                    qualified_backend_census=sorted([dict(container_id=row['Id'], image=row['Image'], labels=_labels(row))
                        for row in inspected if row.get('Config', {}).get('Labels', {}).get(
                            'io.kubernetes.pod.namespace') == self.namespace], key=lambda row: row['container_id']))

    def consume_historical(self, graph, selector, reference):
        """Consume official monotonic terminal facts; observe only present contradictions/health."""
        with control_store.connect() as db:
            intent = _historical_reference(db, reference)
        _require(intent['graph'] == graph and intent['selector'] == selector, 'RECOVERY_INTENT_MISMATCH')
        _require(graph['queries'][0]['state'] == 'UNKNOWN' and graph['executions'][0]['mode'] == 'read-only' and
                 graph['executions'][0]['backend_query_id'] is None)
        planned = intent['observation']
        producer = _producer(graph, selector)
        engine = self.engine()
        _require(engine['engine_id'] == planned['engine_id'], 'RECOVERY_ENGINE_CHANGED')
        deployment = self._kube('deployment', self.deployment)
        _require(deployment['metadata']['namespace'] == self.namespace and deployment['spec']['replicas'] == 1 and
                 deployment['metadata']['annotations'].get('shirokuma.dev/database-generation') == graph['generation']['id'])
        replacement = dict(self.historical_current(graph, deployment), engine_id=engine['engine_id'])
        old_ids = [m['scope']['container_id'] for m in planned['members']]
        inventory = self._run('docker', 'ps', '-a', '--no-trunc', '--format', '{{.ID}}').splitlines()
        _require(replacement['container_id'] in inventory and all(re.fullmatch('[0-9a-f]{64}', cid) for cid in inventory) and
                 not set(old_ids).intersection(inventory), 'RECOVERY_OLD_CONTAINER_PRESENT')
        fresh = dict(engine_id=engine['engine_id'], old_container_ids_absent=old_ids,
                     replacement_scope=replacement, producer=producer)
        _consumed_historical_proof(reference, intent, fresh)
        self.historical_tls()
        _require(self.engine() == engine, 'RECOVERY_ENGINE_CHANGED')
        for selected in selector['members'] + list(selector['producer'].values()):
            _historical_read(selected)
        return fresh

    def historical(self, graph, selector):
        producer = _producer(graph, selector)
        engine = self.engine()
        deployment = self._kube('deployment', self.deployment)
        _require(deployment['metadata']['namespace'] == self.namespace and deployment['spec']['replicas'] == 1 and
                 deployment['metadata']['annotations'].get(
            'shirokuma.dev/database-generation') == graph['generation']['id'])
        _require(len(selector['members']) == 2)
        members = []
        for index, selected in enumerate(selector['members']):
            document = _historical_read(selected)
            _require(document.get('kind') in ('List', 'PodList'))
            pods = [p for p in document['items'] if p['metadata']['namespace'] == self.namespace and
                    any(c['name'] == 'trino' for c in p['spec']['containers'])]
            _require(len(pods) == 1, 'RECOVERY_HISTORICAL_CUTSET_UNPROVED')
            pod = pods[0]
            scope = self._historical_pod(graph, pod, deployment)
            _require(scope['container_id'] == selected['container_id'])
            until = admission._now()
            raw = self._run('docker', 'events', '--since', pod['metadata']['creationTimestamp'], '--until', until,
                            '--filter', 'container=' + selected['container_id'], '--format', '{{json .}}')
            events = []
            for line in raw.splitlines():
                e = json.loads(line)
                _require(e.get('Type') == 'container' and e.get('Actor', {}).get('ID') == selected['container_id'])
                attrs = e['Actor'].get('Attributes', {})
                _require(all(attrs.get(k) == v for k, v in scope['labels'].items()))
                _image_digest(attrs.get('image', ''), scope['pinned_image'])
                if e.get('Action') in ('create', 'start', 'stop', 'die', 'destroy', 'restart', 'unpause', 'pause'):
                    _require(type(e.get('timeNano')) is int)
                    events.append(dict(action=e['Action'], time_nano=e['timeNano'], container_id=scope['container_id'],
                        labels=scope['labels'], image=scope['image'], exit_code=attrs.get('exitCode')))
            member = dict(scope=dict(scope, role='possible-predecessor' if index == 0 else 'historical-original'),
                snapshot=selected, events=events, events_digest=hashlib.sha256(_json(events).encode()).hexdigest())
            if index == 0:
                status = next(c for c in pod['status']['containerStatuses'] if c['name'] == 'trino')
                _require(set(status.get('state', {})) == {'running'}, 'RECOVERY_SAVED_START_UNPROVED')
                member['saved_start'] = dict(kind='pod-running-startedAt', started_at=status['state']['running']['startedAt'],
                    snapshot_sha256=selected['sha256'], container_id=scope['container_id'], pod_uid=scope['pod_uid'])
            _historical_member(member, index, producer, until)
            if index == 1:
                previous = members[0]
                _require(max(e['time_nano'] for e in previous['events'] if e['action'] in ('stop', 'die')) <
                         next(e['time_nano'] for e in events if e['action'] == 'start'))
            members.append(member)
        replacement = self.historical_current(graph, deployment)
        _require(len({m['scope']['container_id'] for m in members} | {replacement['container_id']}) == 3 and
                 len({m['scope']['pod_uid'] for m in members} | {replacement['pod_uid']}) == 3 and
                 _nano(replacement['started_at']) > producer['window_end_ns'])
        _require(all(m['scope']['configmaps'] == replacement['configmaps'] for m in members), 'RECOVERY_CONFIG_REFERENCE_CHANGED')
        self.historical_tls()
        _require(self.engine() == engine, 'RECOVERY_ENGINE_CHANGED')
        for item in selector['members'] + list(selector['producer'].values()):
            _historical_read(item)
        observed = dict(kind='historical-exact-events', version=2, engine_id=engine['engine_id'], members=members,
            historical_scope=dict(members[1]['scope'], engine_id=engine['engine_id']),
            replacement_scope=dict(replacement, engine_id=engine['engine_id']), producer=producer,
            basis='operator-established two-member endpoint cutset; A start from original pre-query Pod running.startedAt '
                  'and fresh terminal events; B start and terminal from fresh daemon events; single Deployment/Service selector; '
                  'one CLI call and one POST without retry; pinned GET/DELETE origin; fixed one-Pod port-forward lifetime',
            limitations='No captured historical daemon census or Endpoints object; no captured historical ConfigMap '
                         'data, UID or resourceVersion; current canonical CM data and hashed mount references verified only')
        return observed

    def historical_tls(self):
        # Reuse the verified service TLS tunnel; no native endpoint or SQL.
        import base64
        import http.client
        import trino_auth
        import warehouse_resident as wr
        os.environ.update(SHIROKUMA_TRINO_AUTH_MODE='password',
            SHIROKUMA_TRINO_AUTH_FILE=self.profile.trino_auth_file, SHIROKUMA_TRINO_CA_FILE=self.profile.trino_ca_file)
        admin = trino_auth.platform_admin_credentials()
        auth = base64.b64encode((admin['username'] + ':' + admin['password']).encode()).decode()
        # _trino_connection does not acquire warehouse_activity. Bind its
        # existing helper to this dedicated context and avoid borrowing any
        # unrelated process-global tunnel.
        previous = wr.KUBECTL
        wr.KUBECTL = ['rtk', 'proxy', 'kubectl', '--context', self.config['kube_context'], '-n', self.namespace]
        try:
            with wr._trino_connection() as tunnel:
                origin = tunnel['url']
                trino_auth.validate_backend_url(origin)
                endpoint = urlsplit(origin)
                _require(endpoint.scheme == 'https' and ipaddress.ip_address(endpoint.hostname).is_loopback)
                connection = http.client.HTTPSConnection(endpoint.hostname, endpoint.port,
                    context=trino_auth.backend_ssl_context(), timeout=10)
                try:
                    connection.request('GET', '/v1/info', headers={'Authorization': 'Basic ' + auth,
                        'X-Trino-User': admin['username']})
                    response = connection.getresponse()
                    body = json.loads(response.read())
                    _require(response.status == 200 and body.get('starting') is False and body.get('coordinator') is True)
                finally:
                    connection.close()
        finally:
            wr.KUBECTL = previous

    def proof(self, scope, since):
        _require(self.engine()['engine_id'] == scope['engine_id'], 'RECOVERY_ENGINE_CHANGED')
        try:
            rows = self._docker('inspect', scope['container_id'])
        except QueryError:
            rows = []
        if len(rows) == 1:
            row = rows[0]
            if (row['State']['Running'] is True or not self._matches(row, scope)):
                return None
            if (row['State']['Running'] is False and row['State']['Status'] == 'exited' and
                    _time(row['State']['FinishedAt']) >= _time(since)):
                return dict(kind='stopped-inspect', engine_id=scope['engine_id'],
                    receipt=dict(container_id=row['Id'], labels=_labels(row), image=row['Image'],
                        started_at=row['State']['StartedAt'], restart_policy='no',
                        state={k: row['State'][k] for k in ('Running','Status','FinishedAt','ExitCode')}))
        # Docker's last-256 in-memory event retention is a fallback only. An
        # empty/expired journal or a daemon restart cannot establish closure.
        raw = self._run('docker', 'events', '--since', since, '--until', admission._now(),
                        '--filter', 'container=' + scope['container_id'], '--format', '{{json .}}')
        events = [json.loads(line) for line in raw.splitlines()]
        lifecycle = [e for e in events if e.get('Type') == 'container' and
                     e.get('Actor', {}).get('ID') == scope['container_id'] and
                     e.get('Action') in ('die', 'destroy', 'start', 'restart') and type(e.get('timeNano')) is int]
        if lifecycle:
            event = max(lifecycle, key=lambda e: e['timeNano'])
            if (event.get('Action') in ('die', 'destroy') and
                    event.get('Actor', {}).get('ID') == scope['container_id'] and
                    all(event['Actor'].get('Attributes', {}).get(k) == v for k, v in scope['labels'].items()) and
                    type(event.get('timeNano')) is int and event['timeNano'] >= int(_time(since).timestamp() * 1e9)):
                return dict(kind='exact-event', engine_id=scope['engine_id'], receipt=dict(
                    container_id=event['Actor']['ID'], action=event['Action'], time_nano=event['timeNano'],
                    labels={k: event['Actor']['Attributes'][k] for k in MANAGED_LABELS}))
        return None

    def _matches(self, row, scope):
        return (row['Id'] == scope['container_id'] and _labels(row) == scope['labels'] and
                row['Image'] == scope['image'] and row['State']['StartedAt'] == scope['started_at'] and
                row['HostConfig']['RestartPolicy']['Name'] == 'no')

    def old_present(self, scope):
        """Called only after durable positive proof; absence is not new proof."""
        _require(self.engine()['engine_id'] == scope['engine_id'], 'RECOVERY_ENGINE_CHANGED')
        ids = self._run('docker', 'ps', '-a', '--no-trunc', '--filter',
                        'id=' + scope['container_id'], '--format', '{{.ID}}').splitlines()
        if not ids:
            return False
        _require(ids == [scope['container_id']], 'RECOVERY_SCOPE_CHANGED')
        rows = self._docker('inspect', scope['container_id'])
        _require(len(rows) == 1 and self._matches(rows[0], scope) and
                 rows[0]['State']['Running'] is False and rows[0]['State']['Status'] == 'exited' and
                 _time(rows[0]['State']['FinishedAt']) >= _time(scope['started_at']), 'RECOVERY_SCOPE_CHANGED')
        return True

    def remove(self, scope):
        if self.old_present(scope):
            self._run('docker', 'rm', scope['container_id'])
        _require(not self.old_present(scope), 'RECOVERY_OLD_CONTAINER_PRESENT')

    def stop(self, scope):
        self._run('docker', 'stop', '--time', '10', scope['container_id'])

    def healthy(self, graph, old_scope):
        fresh = self.scope(graph, replacement=True)
        _require(fresh['engine_id'] == old_scope['engine_id'] and fresh['container_id'] != old_scope['container_id'] and
                 fresh['deployment_uid'] == old_scope['deployment_uid'])
        # Reuse the verified service TLS tunnel; no native endpoint or SQL.
        import base64
        import http.client
        import trino_auth
        import warehouse_resident as wr
        os.environ.update(SHIROKUMA_TRINO_AUTH_MODE='password',
            SHIROKUMA_TRINO_AUTH_FILE=self.profile.trino_auth_file, SHIROKUMA_TRINO_CA_FILE=self.profile.trino_ca_file)
        admin = trino_auth.platform_admin_credentials()
        auth = base64.b64encode((admin['username'] + ':' + admin['password']).encode()).decode()
        # _trino_connection does not acquire warehouse_activity. Bind its
        # existing helper to this dedicated context and avoid borrowing any
        # unrelated process-global tunnel.
        previous = wr.KUBECTL
        wr.KUBECTL = ['rtk', 'proxy', 'kubectl', '--context', self.config['kube_context'], '-n', self.namespace]
        try:
            with wr._trino_connection() as tunnel:
                origin = tunnel['url']
                trino_auth.validate_backend_url(origin)
                endpoint = urlsplit(origin)
                _require(endpoint.scheme == 'https' and ipaddress.ip_address(endpoint.hostname).is_loopback)
                connection = http.client.HTTPSConnection(endpoint.hostname, endpoint.port,
                    context=trino_auth.backend_ssl_context(), timeout=10)
                try:
                    connection.request('GET', '/v1/info', headers={'Authorization': 'Basic ' + auth,
                        'X-Trino-User': admin['username']})
                    response = connection.getresponse()
                    body = json.loads(response.read())
                    _require(response.status == 200 and body.get('starting') is False and body.get('coordinator') is True)
                finally:
                    connection.close()
        finally:
            wr.KUBECTL = previous
        return fresh


class Recovery:
    route = ROUTE
    action = 'JOB_EXECUTION_RECOVERY'

    @staticmethod
    def subject(frozen):
        return frozen['run']['id']

    def graph(self, db, subject_id):
        return graph(db, subject_id)

    def __init__(self, config_path):
        self.config = runtime_profile.private_json(config_path)
        _require(set(self.config) == {'version', 'auth_headers', 'runtime_profile', 'docker_context', 'kube_context'} and
                 type(self.config['version']) is int and self.config['version'] == 1 and
                 self.config['docker_context'] == local_gitops.CONTEXT and self.config['kube_context'] == local_gitops.CONTEXT)
        _require(all(isinstance(self.config[k], str) and Path(self.config[k]).is_absolute()
                     for k in ('auth_headers', 'runtime_profile')))
        self.profile = runtime_profile.load(self.config['runtime_profile'])
        # Explicit per-process inputs; deliberately do not configure a runtime.
        os.environ.update(SHIROKUMA_STATE_DIR=self.profile.state_dir, SHIROKUMA_AUTH_MODE='shared',
                          SHIROKUMA_AUTH_FILE=self.profile.auth_file)
        self.seal = runtime_profile.private_binding(self.config['runtime_profile'])
        self.actor = self.authenticate()[0].principal_id

    def authenticate(self):
        principal, role = catalog_admin.authenticate_headers(self.config['auth_headers'])
        catalog_admin.require_admin(principal, role)
        account = request_auth._principal_accounts().get(principal.principal_id)
        _require(account is not None and account.get('role') == 'admin' and account.get('platform_admin') is True,
                 'RECOVERY_ADMIN_REQUIRED')
        if hasattr(self, 'actor'):
            _require(principal.principal_id == self.actor, 'RECOVERY_ACTOR_CHANGED')
        return principal, role

    def adapter(self, frozen):
        _require(runtime_profile.private_binding(self.config['runtime_profile']) == self.seal, 'RECOVERY_RUNTIME_CHANGED')
        with control_store.connect() as db:
            plan = _one(db, 'SELECT * FROM generation_plans WHERE generation_id=?', (frozen['generation']['id'],))
        bundle = warehouse_operations._render(json.loads(plan['manifest']))
        runtime_profile.validate_generation(self.config['runtime_profile'], bundle)
        # The backend endpoint is established from the current validated profile;
        # it is never claimed to be an Engine identity recorded at admission.
        return DockerAdapter(self.config, self.profile, bundle)

    def _request(self, run_id):
        return _json(dict(run_id=run_id, config=self.config, runtime_binding=self.seal))

    @contextmanager
    def locks(self, frozen):
        with ExitStack() as stack:
            stack.enter_context(warehouse_operations._operator_gate())
            lock = db_jobs.legacy._lock()
            _require(lock is not None, 'JOB_BUSY')
            stack.enter_context(lock)
            for operation_id in frozen['operation_ids']:
                stack.enter_context(query_service._acquire_query_owner('dataops-' + operation_id))
            for query_id in sorted(q['id'] for q in frozen['queries']):
                stack.enter_context(query_service._acquire_query_owner(query_id))
            stack.enter_context(admission.lifecycle_gate(exclusive=True, deadline=time.monotonic() + 2))
            yield

    def plan(self, run_id, key):
        self.authenticate()
        _require(str(uuid.UUID(run_id)) == run_id)
        db_jobs._key(key)
        payload = self._request(run_id)
        with control_store.connect() as db:
            saved = db.execute('SELECT * FROM execution_recovery_requests WHERE actor=? AND route=? AND idempotency_key=?',
                               (self.actor, self.route, key)).fetchone()
            if saved:
                _require(saved['payload'] == payload, 'IDEMPOTENCY_CONFLICT')
                operation_id = saved['operation_id']
            else:
                operation_id = None
                frozen = self.graph(db, run_id)
        if operation_id:
            with control_store.connect() as db:
                saved_intent = json.loads(db.execute('SELECT intent FROM execution_recovery_requests WHERE operation_id=?',
                                                    (operation_id,)).fetchone()[0])
                self._binding(db, saved_intent['graph'])
            return self.inspect(operation_id)
        with self.locks(frozen):
            self.authenticate()
            adapter = self.adapter(frozen)
            scope = adapter.scope(frozen)
            self.authenticate()
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                _require(self.graph(db, run_id) == frozen, 'RECOVERY_GRAPH_CHANGED')
                control = admission.control(db)
                _require(control['state'] == 'OPEN' and control['operation_id'] is None)
                operation_id = str(uuid.uuid4())
                intent = dict(graph=frozen, scope=scope, planned_at=admission._now(),
                              mode='FENCE' if frozen['query_ids'] else TERMINAL_PARENT)
                registry.record_operation(db, registry.Operation(operation_id, self.actor, self.action, (),
                    control['revision'], 'PLANNED', tuple(frozen['query_ids']), None, (), ()))
                db.execute('''INSERT INTO execution_recovery_requests
                    VALUES (?,?,?,?,?,?,1,'PLANNED',NULL,?)''',
                    (operation_id, self.actor, self.route, key, payload, _json(intent), control['revision']))
        return self.inspect(operation_id)

    def inspect(self, operation_id):
        self.authenticate()
        with control_store.connect() as db:
            row = _one(db, 'SELECT * FROM execution_recovery_requests WHERE operation_id=?', (operation_id,))
        # No SQL, result rows, credential references or saved owner grant in this view.
        _require(row['route'] == self.route, 'RECOVERY_SUBJECT_MISMATCH')
        intent = json.loads(row['intent'])
        mode = _mode(intent)
        terminal = mode == TERMINAL_PARENT
        return dict(operation_id=operation_id, actor=row['actor'], run_id=intent['graph']['run']['id'],
            revision=row['revision'], phase=row['phase'], mode=mode,
            query_ids=sorted(q['id'] for q in intent['graph']['queries']) if terminal else intent['graph']['query_ids'],
            scope=intent['scope'], proof=json.loads(row['proof']) if row['proof'] else None,
            outcome='TERMINAL_CHILDREN_PRESERVED' if terminal else 'UNKNOWN',
            parent_closure=PARENT_CLOSURE if terminal and row['phase'] in ('CLOSED', 'OPEN') else '',
            execution_closure=CLOSURE if not terminal and row['phase'] in ('CLOSED', 'OPEN') else '')

    def _transition(self, db, row, phase, *, proof=None, control_revision=None):
        cursor = db.execute('''UPDATE execution_recovery_requests SET phase=?,revision=revision+1,
            proof=COALESCE(?,proof),control_revision=COALESCE(?,control_revision)
            WHERE operation_id=? AND revision=?''',
            (phase, _json(proof) if proof else None, control_revision, row['operation_id'], row['revision']))
        _require(cursor.rowcount == 1, 'REVISION_CONFLICT')
        terminal = _mode(json.loads(row['intent'])) == TERMINAL_PARENT
        db.execute('UPDATE operations SET state=?,before_after=?,timestamps=? WHERE id=?',
            (phase, _json([['parent_closure' if terminal else 'execution_closure',
                           (PARENT_CLOSURE if terminal else CLOSURE) if phase in ('CLOSED', 'OPEN') else '']]),
             _json([['updated_at', admission._now()]]), row['operation_id']))

    def apply(self, operation_id, expected_revision, *, reconcile=False):
        self.authenticate()
        with control_store.connect() as db:
            row = _one(db, 'SELECT * FROM execution_recovery_requests WHERE operation_id=?', (operation_id,))
        _require(row['actor'] == self.actor and type(expected_revision) is int and row['revision'] == expected_revision,
                 'REVISION_CONFLICT')
        _require(row['route'] == self.route, 'RECOVERY_SUBJECT_MISMATCH')
        intent = json.loads(row['intent'])
        mode = _mode(intent)
        frozen, scope = intent['graph'], intent['scope']
        _require(row['payload'] == self._request(self.subject(frozen)), 'RECOVERY_RUNTIME_CHANGED')
        with self.locks(frozen):
            adapter = self.adapter(frozen)
            self.authenticate()
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                current = _one(db, 'SELECT * FROM execution_recovery_requests WHERE operation_id=?', (operation_id,))
                _require(current == row, 'REVISION_CONFLICT')
                control = admission.control(db)
                if row['phase'] == 'OPEN':
                    return self.inspect(operation_id)
                _require(control['revision'] == row['control_revision'], 'REVISION_CONFLICT')
                if row['phase'] == 'PLANNED':
                    _require(self.graph(db, self.subject(frozen)) == frozen, 'RECOVERY_GRAPH_CHANGED')
                    if reconcile:
                        return self.inspect(operation_id)
                    _require(adapter.scope(frozen) == scope, 'RECOVERY_SCOPE_CHANGED')
                    control = admission.begin_drain(db, operation_id=operation_id, expected_revision=control['revision'])
                    # Terminal proof and owned drain commit before parent close.
                    # FENCE's immutable planned_at remains the lower event bound.
                    self._transition(db, row, 'PROVED' if mode == TERMINAL_PARENT else 'STOP_INTENT',
                        proof=_terminal_proof(frozen) if mode == TERMINAL_PARENT else None,
                        control_revision=control['revision'])
                else:
                    _require(control['state'] == 'DRAINING' and control['operation_id'] == operation_id)
                    if row['phase'] in ('STOP_INTENT', 'PROVED'):
                        _require(self.graph(db, self.subject(frozen)) == frozen, 'RECOVERY_GRAPH_CHANGED')
            if mode == TERMINAL_PARENT:
                return self._apply_terminal(operation_id, frozen, scope, adapter)
            # At this point both durable intent and DRAINING survive process loss.
            phase = row['phase']
            if phase in ('PLANNED', 'STOP_INTENT'):
                _require(adapter.engine()['engine_id'] == scope['engine_id'], 'RECOVERY_ENGINE_CHANGED')
                proof = adapter.proof(scope, intent['planned_at'])
                if proof is None and not reconcile:
                    # Only an exact, still-running original container can be
                    # stopped on resume. Absence never authorizes another stop.
                    _require(adapter.scope(frozen) == scope, 'RECOVERY_SCOPE_CHANGED')
                    self.authenticate()
                    adapter.stop(scope)
                    proof = adapter.proof(scope, intent['planned_at'])
                if proof is None and reconcile:
                    return self.inspect(operation_id)
                _require(proof is not None, 'RECOVERY_STOP_UNPROVED')
                with control_store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    row = _one(db, 'SELECT * FROM execution_recovery_requests WHERE operation_id=?', (operation_id,))
                    self._transition(db, row, 'PROVED', proof=proof)
            self.authenticate()
            _require(adapter.engine()['engine_id'] == scope['engine_id'], 'RECOVERY_ENGINE_CHANGED')
            with control_store.connect() as db:
                row = _one(db, 'SELECT * FROM execution_recovery_requests WHERE operation_id=?', (operation_id,))
            if row['phase'] == 'PROVED':
                present = adapter.old_present(scope)
                if present and reconcile:
                    return self.inspect(operation_id)
                if present:
                    adapter.remove(scope)
            _require(not adapter.old_present(scope), 'RECOVERY_OLD_CONTAINER_PRESENT')
            self.authenticate()
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row = _one(db, 'SELECT * FROM execution_recovery_requests WHERE operation_id=?', (operation_id,))
                if row['phase'] == 'PROVED':
                    _require(self.graph(db, self.subject(frozen)) == frozen, 'RECOVERY_GRAPH_CHANGED')
                    self._close(db, operation_id, frozen)
                    self._transition(db, row, 'CLOSED')
            # No stop replay after proof; removal is explicit apply only.
            with control_store.connect() as db:
                self._closed_graph(db, operation_id, frozen)
            adapter.healthy(frozen, scope)
            self.authenticate()
            self.adapter(frozen)
            _require(not adapter.old_present(scope), 'RECOVERY_OLD_CONTAINER_PRESENT')
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row = _one(db, 'SELECT * FROM execution_recovery_requests WHERE operation_id=?', (operation_id,))
                _require(row['phase'] == 'CLOSED' and not admission.blockers(db), 'UNRELATED_UNRESOLVED_WORK')
                self._closed_graph(db, operation_id, frozen)
                control = admission.reopen(db, operation_id=operation_id, expected_revision=row['control_revision'])
                self._transition(db, row, 'OPEN', control_revision=control['revision'])
        return self.inspect(operation_id)

    def _apply_terminal(self, operation_id, frozen, scope, adapter):
        """Abandon parents only; observe the same backend without stopping it."""
        self.authenticate()
        _require(adapter.scope(frozen) == scope, 'RECOVERY_SCOPE_CHANGED')
        self.authenticate()
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = _one(db, 'SELECT * FROM execution_recovery_requests WHERE operation_id=?', (operation_id,))
            _require(row['proof'] == _json(_terminal_proof(frozen)), 'RECOVERY_GRAPH_CHANGED')
            if row['phase'] == 'PROVED':
                _require(self.graph(db, self.subject(frozen)) == frozen, 'RECOVERY_GRAPH_CHANGED')
                self._close(db, operation_id, frozen, mode=TERMINAL_PARENT)
                self._transition(db, row, 'CLOSED')
        with control_store.connect() as db:
            self._closed_graph(db, operation_id, frozen, mode=TERMINAL_PARENT)
        # Replacement health is inappropriate here: the same managed container
        # must still match the plan, with unchanged profile/binding/generation.
        _require(adapter.scope(frozen) == scope, 'RECOVERY_SCOPE_CHANGED')
        self.authenticate()
        self.adapter(frozen)
        self.authenticate()
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = _one(db, 'SELECT * FROM execution_recovery_requests WHERE operation_id=?', (operation_id,))
            control = admission.control(db)
            _require(control['state'] == 'DRAINING' and control['operation_id'] == operation_id and
                     control['revision'] == row['control_revision'], 'REVISION_CONFLICT')
            _require(row['phase'] == 'CLOSED' and not admission.blockers(db), 'UNRELATED_UNRESOLVED_WORK')
            self._closed_graph(db, operation_id, frozen, mode=TERMINAL_PARENT)
            control = admission.reopen(db, operation_id=operation_id, expected_revision=row['control_revision'])
            self._transition(db, row, 'OPEN', control_revision=control['revision'])
        return self.inspect(operation_id)

    def _binding(self, db, frozen):
        _require(admission.control(db)['applied_generation_id'] == frozen['generation']['id'], 'RECOVERY_BINDING_CHANGED')
        for table, item, key in (('catalog_bindings', frozen['binding'], 'database_id'),
                                 ('databases', frozen['database'], 'id'), ('schemas', frozen['schema'], 'id'),
                                 ('warehouse_config_generations', frozen['generation'], 'id')):
            _require(_one(db, 'SELECT * FROM ' + table + ' WHERE ' + key + '=?', (item[key],)) == item,
                     'RECOVERY_BINDING_CHANGED')

    def _summary(self, operation_id, mode='FENCE'):
        if mode == TERMINAL_PARENT:
            return _json(dict(code=PARENT_CLOSURE, recovery_operation_id=operation_id,
                              query_outcome='TERMINAL_CHILDREN_PRESERVED', parent_closure=PARENT_CLOSURE))
        return _json(dict(code=ABANDONED, recovery_operation_id=operation_id,
                          query_outcome='UNKNOWN', execution_closure=CLOSURE))

    def _closed_graph(self, db, operation_id, frozen, mode='FENCE'):
        """CAS the frozen graph again while waiting for replacement health."""
        self._binding(db, frozen)
        run_id, summary = frozen['run']['id'], self._summary(operation_id, mode)
        run = _one(db, 'SELECT * FROM db_job_runs WHERE id=?', (run_id,))
        expected = dict(frozen['run'], status='failed', finished_at=run['finished_at'])
        _require(run == expected, 'RECOVERY_GRAPH_CHANGED')
        expected_tree = []
        current_tree = [dict(a) for a in db_jobs._descendants(db, run_id)]
        _require(len(current_tree) == len(frozen['admissions']), 'RECOVERY_GRAPH_CHANGED')
        current_by_id = {a['id']: a for a in current_tree}
        for original in frozen['admissions']:
            a = dict(original)
            if mode == 'FENCE' and a['kind'] == 'query' and a['subject_id'] in frozen['query_ids']:
                a.update(execution_closure=CLOSURE, evidence_ref=operation_id)
            if a['kind'] == 'job' or a['kind'] == 'dataops' and a['subject_id'] in frozen['operation_ids']:
                a.update(state='FAILED', evidence_ref=operation_id, finished_at=current_by_id[a['id']]['finished_at'])
            expected_tree.append(a)
        _require({a['id']: a for a in expected_tree} == current_by_id, 'RECOVERY_GRAPH_CHANGED')
        for table, records, key in (('query_records', frozen['queries'], 'id'),
                                    ('query_execution_contexts', frozen['executions'], 'query_id'),
                                    ('dataops_operations', frozen['operations'], 'id')):
            for original in records:
                expected = dict(original)
                if table == 'dataops_operations' and original['id'] in frozen['operation_ids']:
                    expected.update(state='FAILED', cleanup_error=summary)
                _require(_one(db, 'SELECT * FROM ' + table + ' WHERE ' + key + '=?', (original[key],)) == expected,
                         'RECOVERY_GRAPH_CHANGED')
        links = [dict(r) for r in db.execute('SELECT * FROM db_job_operations WHERE run_id=? ORDER BY position,operation_id', (run_id,))]
        _require(links == frozen['links'], 'RECOVERY_GRAPH_CHANGED')
        for original in frozen['operations']:
            children = [dict(r) for r in db.execute('SELECT * FROM dataops_queries WHERE operation_id=? ORDER BY step', (original['id'],))]
            _require(children == [c for c in frozen['query_links'] if c['operation_id'] == original['id']], 'RECOVERY_GRAPH_CHANGED')
            _require({r[0] for r in db.execute('SELECT query_id FROM query_execution_contexts WHERE request_id=?', (original['id'],))}
                     == {c['query_id'] for c in children}, 'RECOVERY_GRAPH_CHANGED')
        steps = [dict(r) for r in db.execute('SELECT * FROM db_job_steps WHERE run_id=? ORDER BY position', (run_id,))]
        expected_steps = []
        for s in frozen['steps']:
            expected = dict(s)
            if s['status'] == 'unknown':
                expected.update(status='failed', summary=summary)
            elif s['status'] == 'pending':
                expected.update(status='skipped')
            expected_steps.append(expected)
        _require(steps == expected_steps, 'RECOVERY_GRAPH_CHANGED')

    def _close(self, db, operation_id, frozen, mode='FENCE'):
        self._binding(db, frozen)
        summary = self._summary(operation_id, mode)
        for query_id in frozen['query_ids']:
            cursor = db.execute("""UPDATE warehouse_admissions SET execution_closure=?,evidence_ref=?
                WHERE kind='query' AND subject_id=? AND state='UNKNOWN' AND execution_closure=''""",
                (CLOSURE, operation_id, query_id))
            _require(cursor.rowcount == 1)
        for child_id in frozen['operation_ids']:
            parent = admission.get_for_subject(db, kind='dataops', subject_id=child_id)
            admission.finish(db, parent, state='FAILED', evidence_ref=operation_id)
            db.execute("UPDATE dataops_operations SET state='FAILED',cleanup_error=? WHERE id=?", (summary, child_id))
        run_id = frozen['run']['id']
        parent = admission.get_for_subject(db, kind='job', subject_id=run_id)
        admission.finish(db, parent, state='FAILED', evidence_ref=operation_id)
        db.execute("UPDATE db_job_runs SET status='failed',finished_at=? WHERE id=?", (admission._now(), run_id))
        db.execute("UPDATE db_job_steps SET status='failed',summary=? WHERE run_id=? AND status='unknown'", (summary, run_id))
        db.execute("UPDATE db_job_steps SET status='skipped' WHERE run_id=? AND status='pending'", (run_id,))


def _query_pin(query_id):
    """Freeze a resident pin when present; CLI recovery never manufactures one."""
    pin = query_service._pins.get(query_id)
    if pin is None:
        return None
    # A resident cancel may already hold the pin while waiting on the shared
    # lifecycle gate. Never wait for that lock behind our exclusive gate.
    _require(pin.lock.acquire(blocking=False), 'QUERY_CONTEXT_BUSY')
    try:
        return dict(context=pin.context.public_metadata(), origin=pin.origin,
            headers_digest=hashlib.sha256(_json(pin.headers).encode()).hexdigest(),
            tokens={k: [url, sorted(methods)] for k, (url, methods) in pin.tokens.items()},
            cancel_urls=list(pin.cancel_urls), max_rows=pin.max_rows, rows=pin.rows)
    finally:
        pin.lock.release()


class QueryRecovery(Recovery):
    """Private, same-actor standalone query fencing; no query outcome mutation."""
    route = 'recover-query'
    action = 'QUERY_EXECUTION_RECOVERY'

    def __init__(self, config_path):
        super().__init__(config_path)
        # Private CLI inputs, just as in DockerAdapter.healthy; no runtime setup
        # or backend access, and no dependence on unrelated caller environment.
        os.environ.update(SHIROKUMA_TRINO_AUTH_MODE='password',
            SHIROKUMA_TRINO_AUTH_FILE=self.profile.trino_auth_file,
            SHIROKUMA_TRINO_CA_FILE=self.profile.trino_ca_file)

    @staticmethod
    def subject(frozen):
        return frozen['queries'][0]['id']

    def _request(self, query_id):
        return _json(dict(subject='query', query_id=query_id, config=self.config, runtime_binding=self.seal))

    def plan(self, query_id, key, *, historical_pod_snapshot=None, historical_container_id=None,
             historical_predecessor_pod_snapshot=None, historical_predecessor_container_id=None,
             historical_query_intent=None, historical_query_result=None):
        inputs = (historical_pod_snapshot, historical_container_id, historical_predecessor_pod_snapshot,
                  historical_predecessor_container_id, historical_query_intent, historical_query_result)
        if all(value is None for value in inputs):
            return super().plan(query_id, key)
        _require(all(value is not None for value in inputs), 'RECOVERY_HISTORICAL_SELECTOR')
        self.authenticate()
        _require(str(uuid.UUID(query_id)) == query_id)
        db_jobs._key(key)
        selector = dict(members=[_snapshot(historical_predecessor_pod_snapshot, historical_predecessor_container_id),
                                 _snapshot(historical_pod_snapshot, historical_container_id)],
            producer=dict(intent=_historical_file(historical_query_intent)[0], result=_historical_file(historical_query_result)[0]))
        _require(len({m['container_id'] for m in selector['members']}) == 2)

        payload = _json(dict(**json.loads(self._request(query_id)), mode=HISTORICAL, selector=selector))
        with control_store.connect() as db:
            saved = db.execute('SELECT * FROM execution_recovery_requests WHERE actor=? AND route=? AND idempotency_key=?',
                               (self.actor, self.route, key)).fetchone()
            if saved:
                _require(saved['payload'] == payload, 'IDEMPOTENCY_CONFLICT')
                self._binding(db, json.loads(saved['intent'])['graph'])
                return self.inspect(saved['operation_id'])
            frozen = self.graph(db, query_id)
            self._producer_graph(db, frozen, selector)
        with self.locks(frozen):
            self.authenticate()
            proof = self.adapter(frozen).historical(frozen, selector)
            self.authenticate()
            self.adapter(frozen)  # runtime seal after external observations
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                _require(self.graph(db, query_id) == frozen, 'RECOVERY_GRAPH_CHANGED')
                self._producer_graph(db, frozen, selector)
                control = admission.control(db)
                _require(control['state'] == 'OPEN' and control['operation_id'] is None)
                operation_id = str(uuid.uuid4())
                intent = dict(graph=frozen, scope=proof['historical_scope'], replacement_scope=proof['replacement_scope'],
                              selector=selector, observation=proof, planned_at=admission._now(), mode=HISTORICAL)
                _historical_claim(intent)
                registry.record_operation(db, registry.Operation(operation_id, self.actor, self.action, (),
                    control['revision'], 'PLANNED', tuple(frozen['query_ids']), None, (), ()))
                db.execute('''INSERT INTO execution_recovery_requests
                    VALUES (?,?,?,?,?,?,1,'PLANNED',NULL,?)''',
                    (operation_id, self.actor, self.route, key, payload, _json(intent), control['revision']))
        return self.inspect(operation_id)

    def _producer_graph(self, db, frozen, selector):
        producer = _producer(frozen, selector)
        execution = frozen['executions'][0]
        rows = db.execute('''SELECT q.id,q.created_at FROM query_records q
            JOIN query_execution_contexts e ON e.query_id=q.id WHERE q.actor=? AND e.sql=?
            AND e.sql_catalog=? AND e.sql_schema=? AND e.mode='read-only'
            ''',
            (frozen['queries'][0]['actor'], execution['sql'], execution['sql_catalog'], execution['sql_schema']))
        matches = [r['id'] for r in rows if producer['window_start_ns'] <= _nano(r['created_at']) <= producer['window_end_ns']]
        _require(matches == [self.subject(frozen)], 'RECOVERY_ORIGINAL_SUBMISSION_AMBIGUOUS')

    def _historical_completed(self, db, row, intent):
        _require(row['phase'] == 'OPEN')
        return completed_historical_query(db, row, intent, json.loads(row['proof']))

    def _historical_apply(self, approved, expected_revision, *, reconcile):
        _require(type(expected_revision) is int and approved['revision'] == expected_revision, 'REVISION_CONFLICT')
        intent = json.loads(approved['intent'])
        _historical_claim(intent)
        if approved['phase'] == 'OPEN':
            self.authenticate()
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                _require(self._query_row(db, approved['operation_id']) == approved, 'REVISION_CONFLICT')
                self._historical_completed(db, approved, intent)
            return self.inspect(approved['operation_id'])
        frozen, selector = intent['graph'], intent['selector']
        expected_payload = _json(dict(**json.loads(self._request(self.subject(frozen))),
                                      mode=HISTORICAL, selector=selector))
        _require(approved['payload'] == expected_payload, 'RECOVERY_RUNTIME_CHANGED')
        operation_id = approved['operation_id']
        with self.locks(frozen):
            self.authenticate()
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row = self._query_row(db, operation_id)
                _require(row == approved, 'REVISION_CONFLICT')
                if row['phase'] in ('CLOSED', 'OPEN'):
                    _saved_historical_proof(row, intent, json.loads(row['proof']))
                    self._closed_graph(db, operation_id, frozen)
                else:
                    _require(self.graph(db, self.subject(frozen)) == frozen, 'RECOVERY_GRAPH_CHANGED')
                self._producer_graph(db, frozen, selector)
                control = admission.control(db)
                _require(control['revision'] == row['control_revision'], 'REVISION_CONFLICT')
                if row['phase'] == 'PLANNED' and not row['proof']:
                    if reconcile:
                        return self.inspect(operation_id)
                    control = admission.begin_drain(db, operation_id=operation_id, expected_revision=control['revision'])
                    # No external read until the verification intent is durable.
                    self._transition(db, row, 'PLANNED', proof=dict(kind='historical-verification-intent',
                        version=1, operation_id=operation_id, recorded_at=admission._now()),
                        control_revision=control['revision'])
                elif row['phase'] != 'OPEN':
                    _require(control['state'] == 'DRAINING' and control['operation_id'] == operation_id)
                else:
                    _require(control['state'] == 'OPEN' and control['operation_id'] is None)
            for destination in ('CLOSED', 'OPEN'):
                self.authenticate()
                with control_store.connect() as db:
                    boundary = self._query_row(db, operation_id)
                    if boundary['phase'] in ('CLOSED', 'OPEN'):
                        self._closed_graph(db, operation_id, frozen)
                    else:
                        _require(self.graph(db, self.subject(frozen)) == frozen, 'RECOVERY_GRAPH_CHANGED')
                for selected in selector['members'] + list(selector['producer'].values()):
                    _historical_read(selected)
                adapter = self.adapter(frozen)
                observed = adapter.consume_historical(frozen, selector, boundary)
                proof = _consumed_historical_proof(boundary, intent, observed)
                self.authenticate()
                self.adapter(frozen)
                with control_store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    row = self._query_row(db, operation_id)
                    _require(row == boundary, 'REVISION_CONFLICT')
                    self._producer_graph(db, frozen, selector)
                    _require(row['intent'] == approved['intent'] and row['payload'] == approved['payload'],
                             'RECOVERY_INTENT_MISMATCH')
                    control = admission.control(db)
                    _require(control['revision'] == row['control_revision'], 'REVISION_CONFLICT')
                    if row['phase'] in ('CLOSED', 'OPEN'):
                        _saved_historical_proof(row, intent, json.loads(row['proof']))
                    if row['phase'] == 'OPEN':
                        self._closed_graph(db, operation_id, frozen)
                        return self.inspect(operation_id)
                    _require(control['state'] == 'DRAINING' and control['operation_id'] == operation_id)
                    if destination == 'CLOSED' and row['phase'] == 'PLANNED':
                        verification = json.loads(row['proof'])
                        _require(verification.get('kind') == 'historical-verification-intent' and
                                 verification.get('operation_id') == operation_id, 'RECOVERY_INTENT_MISMATCH')
                        _require(self.graph(db, self.subject(frozen)) == frozen, 'RECOVERY_GRAPH_CHANGED')
                        self._transition(db, row, 'PROVED', proof=proof)
                        row = self._query_row(db, operation_id)
                    if destination == 'CLOSED' and row['phase'] == 'PROVED':
                        _saved_historical_proof(row, intent, json.loads(row['proof']))
                        self._close(db, operation_id, frozen)
                        self._transition(db, row, 'CLOSED')
                    else:
                        _require(row['phase'] == 'CLOSED', 'REVISION_CONFLICT')
                        self._closed_graph(db, operation_id, frozen)
                        _require(not admission.blockers(db), 'UNRELATED_UNRESOLVED_WORK')
                        if destination == 'OPEN':
                            control = admission.reopen(db, operation_id=operation_id, expected_revision=row['control_revision'])
                            self._transition(db, row, 'OPEN', control_revision=control['revision'])
        return self.inspect(operation_id)

    def graph(self, db, query_id, *, closure=''):
        principal, _ = self.authenticate()
        q = _one(db, 'SELECT * FROM query_records WHERE id=?', (query_id,))
        e = _one(db, 'SELECT * FROM query_execution_contexts WHERE query_id=?', (query_id,))
        _require(q['actor'] == self.actor and q['state'] == 'UNKNOWN' and e['mode'] == 'read-only')
        # _owned performs the actual current account/grant/context read, without
        # cleanup, cancellation, owner substitution or accepted-work delegation.
        owned = query_service._owned(principal, query_id, e['selected_role'])
        _require(all(owned[k] == v for k, v in {**q, **e}.items()), 'RECOVERY_GRAPH_CHANGED')
        context = ResolvedQueryContext(**query_service._metadata(owned))
        query_service.revalidate_admission(db, principal, context)
        _require(context.warehouse_id == 'LOCAL')
        binding = _one(db, 'SELECT * FROM catalog_bindings WHERE database_id=?', (context.database_id,))
        database = _one(db, 'SELECT * FROM databases WHERE id=?', (context.database_id,))
        schema = _one(db, 'SELECT * FROM schemas WHERE id=?', (context.schema_id,))
        generation = _one(db, 'SELECT * FROM warehouse_config_generations WHERE id=?', (context.generation_id,))
        _require(binding['desired_revision'] == binding['applied_revision'] == context.binding_revision and
                 binding['applied_generation_id'] == context.generation_id == admission.control(db)['applied_generation_id'] and
                 generation['state'] == 'APPLIED' and generation['warehouse_id'] == context.warehouse_id and
                 database['state'] == 'READY' and database['sql_catalog'] == context.sql_catalog and
                 schema['database_id'] == context.database_id and schema['sql_name'] == context.sql_schema)
        # Local credential/configuration reads only; never send a backend request.
        decision, headers = query_service.trino_auth.prepare_backend_v2(principal=principal,
            database_id=context.database_id, selected_role=context.selected_role)
        _require(headers['X-Trino-User'] == e['backend_username'] and
                 decision.generation_id == context.generation_id and decision.grant_revision == context.grant_revision)
        import warehouse_resident
        warehouse_resident.check_sql_policy(query_service._backend_statement(e['sql']), 'read-only', context.selected_role,
            context=context, _platform_authorized=decision.platform_admin)
        a = _one(db, "SELECT * FROM warehouse_admissions WHERE kind='query' AND subject_id=?", (query_id,))
        _require(db.execute('SELECT count(*) FROM warehouse_admissions WHERE subject_id=?', (query_id,)).fetchone()[0] == 1)
        _require(a['parent_id'] is None and a['actor'] == q['actor'] and a['state'] == 'UNKNOWN' and
                 a['warehouse_id'] == context.warehouse_id and a['database_id'] == context.database_id and
                 a['generation_id'] == context.generation_id and a['execution_closure'] == closure)
        _require(not db.execute('SELECT 1 FROM warehouse_admissions WHERE parent_id=?', (a['id'],)).fetchone())
        _require(not db.execute('SELECT 1 FROM dataops_queries WHERE query_id=?', (query_id,)).fetchone())
        # Request correlation is not ownership. Refuse any actual DataOps/Job
        # identity collision rather than inferring a parent from a request name.
        _require(not db.execute('SELECT 1 FROM dataops_operations WHERE id IN (?,?)', (query_id, e['request_id'])).fetchone())
        _require(not db.execute('SELECT 1 FROM db_job_operations WHERE operation_id IN (?,?)', (query_id, e['request_id'])).fetchone())
        _require(not db.execute('SELECT 1 FROM db_job_runs WHERE id IN (?,?)', (query_id, e['request_id'])).fetchone())
        blockers = {r['id'] for r in db.execute('SELECT * FROM warehouse_admissions') if admission.execution_unresolved(dict(r))}
        _require(blockers == ({a['id']} if not closure else set()), 'UNRELATED_UNRESOLVED_WORK')
        for table, state, terminal in (('runs', 'status', ('succeeded', 'failed', 'canceled')),
                                       ('db_job_runs', 'status', ('succeeded', 'failed', 'canceled')),
                                       ('dataops_operations', 'state', tuple(admission.TERMINAL))):
            placeholders = ','.join('?' for _ in terminal)
            _require(not db.execute('SELECT 1 FROM ' + table + ' WHERE ' + state + ' NOT IN (' + placeholders + ')',
                                   terminal).fetchone(), 'UNRELATED_UNRESOLVED_WORK')
        _require(not db.execute("""SELECT 1 FROM query_records q WHERE q.id!=?
            AND q.state NOT IN ('FINISHED','FAILED','CANCELED') AND NOT EXISTS (
                SELECT 1 FROM warehouse_admissions a WHERE a.kind='query' AND a.subject_id=q.id
                AND a.execution_closure=?)""", (query_id, CLOSURE)).fetchone(), 'UNRELATED_UNRESOLVED_WORK')
        pin = _query_pin(query_id)
        if pin is not None:
            resident = query_service._pins[query_id]
            _require(pin['context'] == context.public_metadata() and
                     resident.headers.get('X-Trino-User') == e['backend_username'] and
                     resident.admission.id == a['id'])
        _require(e['backend_query_id'] is None or query_service._backend_id(e['backend_query_id']))
        return dict(subject='query', binding=binding, database=database, schema=schema, generation=generation,
            admissions=[a], queries=[q], executions=[e], query_ids=[query_id], operation_ids=[], pin=pin)

    @staticmethod
    def digest(row):
        # Versioned query-only review contract: all immutable request identity,
        # runtime seal, original graph, exact engine scope and planned time.
        return hashlib.sha256(_json(dict(contract='recover-query-intent-v1',
            **{k: row[k] for k in ('operation_id', 'actor', 'route', 'payload', 'intent')})).encode()).hexdigest()

    def _query_row(self, db, operation_id):
        row = _one(db, 'SELECT * FROM execution_recovery_requests WHERE operation_id=?', (operation_id,))
        _require(row['route'] == self.route and row['actor'] == self.actor, 'RECOVERY_SUBJECT_MISMATCH')
        intent = json.loads(row['intent'])
        _require(intent['graph'].get('subject') == 'query' and _mode(intent) in ('FENCE', HISTORICAL), 'RECOVERY_SUBJECT_MISMATCH')
        return row

    def inspect(self, operation_id):
        self.authenticate()
        with control_store.connect() as db:
            row = self._query_row(db, operation_id)
        intent = json.loads(row['intent'])
        result = dict(operation_id=operation_id, actor=row['actor'], query_id=self.subject(intent['graph']),
            revision=row['revision'], phase=row['phase'], mode=_mode(intent), intent_digest=self.digest(row),
            query_ids=intent['graph']['query_ids'], scope=intent['scope'],
            proof=json.loads(row['proof']) if row['proof'] else None, outcome='UNKNOWN',
            execution_closure=CLOSURE if row['phase'] in ('CLOSED', 'OPEN') else '')
        if _mode(intent) == HISTORICAL:
            result.update(replacement_scope=intent['replacement_scope'], historical_observation=intent['observation'])
        return result

    def apply(self, operation_id, expected_revision, *, expected_intent_digest=None, reconcile=False):
        self.authenticate()
        with control_store.connect() as db:
            row = self._query_row(db, operation_id)
        _require(isinstance(expected_intent_digest, str) and expected_intent_digest == self.digest(row),
                 'RECOVERY_INTENT_MISMATCH')
        if _mode(json.loads(row['intent'])) == HISTORICAL:
            return self._historical_apply(row, expected_revision, reconcile=reconcile)
        frozen = json.loads(row['intent'])['graph']
        with control_store.connect() as db:
            if row['phase'] in ('CLOSED', 'OPEN'):
                self._closed_graph(db, operation_id, frozen)
            else:
                _require(self.graph(db, self.subject(frozen)) == frozen, 'RECOVERY_GRAPH_CHANGED')
        return super().apply(operation_id, expected_revision, reconcile=reconcile)

    def _binding(self, db, frozen):
        super()._binding(db, frozen)
        principal, _ = self.authenticate()
        e = frozen['executions'][0]
        query_service._owned(principal, self.subject(frozen), e['selected_role'])
        _, headers = query_service.trino_auth.prepare_backend_v2(principal=principal,
            database_id=frozen['database']['id'], selected_role=e['selected_role'])
        _require(headers['X-Trino-User'] == e['backend_username'], 'RECOVERY_BINDING_CHANGED')
        query_service.revalidate_admission(db, principal, ResolvedQueryContext(**{
            **{k: frozen['queries'][0][k] for k in ('database_id', 'schema_id', 'warehouse_id', 'generation_id')},
            **{k: e[k] for k in ('sql_catalog', 'sql_schema', 'selected_role', 'binding_revision', 'grant_revision')}}))

    def _closed_graph(self, db, operation_id, frozen, mode='FENCE'):
        self._binding(db, frozen)
        expected = dict(frozen, admissions=[dict(frozen['admissions'][0], execution_closure=CLOSURE,
                                                evidence_ref=operation_id)])
        _require(self.graph(db, self.subject(frozen), closure=CLOSURE) == expected, 'RECOVERY_GRAPH_CHANGED')

    def _close(self, db, operation_id, frozen, mode='FENCE'):
        _require(self.graph(db, self.subject(frozen)) == frozen, 'RECOVERY_GRAPH_CHANGED')
        cursor = db.execute("""UPDATE warehouse_admissions SET execution_closure=?,evidence_ref=?
            WHERE id=? AND kind='query' AND subject_id=? AND parent_id IS NULL
            AND state='UNKNOWN' AND execution_closure=''""",
            (CLOSURE, operation_id, frozen['admissions'][0]['id'], self.subject(frozen)))
        _require(cursor.rowcount == 1, 'RECOVERY_GRAPH_CHANGED')


def run(args):
    if args.command.startswith('recover-query-'):
        _require(args.config.is_absolute())
        operator = QueryRecovery(args.config)
        if args.command == 'recover-query-plan':
            return operator.plan(args.query_id, args.idempotency_key,
                historical_pod_snapshot=args.historical_pod_snapshot,
                historical_container_id=args.historical_container_id,
                historical_predecessor_pod_snapshot=args.historical_predecessor_pod_snapshot,
                historical_predecessor_container_id=args.historical_predecessor_container_id,
                historical_query_intent=args.historical_query_intent,
                historical_query_result=args.historical_query_result)
        if args.command == 'recover-query-inspect':
            return operator.inspect(args.operation_id)
        return operator.apply(args.operation_id, args.expected_revision,
            expected_intent_digest=args.expected_intent_digest, reconcile=args.command == 'recover-query-reconcile')
    operator = Recovery(args.config)
    if args.command == 'recover-job-plan':
        return operator.plan(args.run_id, args.idempotency_key)
    if args.command == 'recover-job-inspect':
        return operator.inspect(args.operation_id)
    return operator.apply(args.operation_id, args.expected_revision,
                          reconcile=args.command == 'recover-job-reconcile')
