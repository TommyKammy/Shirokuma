"""Read-only projections of persisted control metadata; never execution inspection."""
import base64
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3

import control_store
import data_management as registry
import database_inventory
import request_auth
from query_context import QueryError

_KEY = os.urandom(32)
COVERAGE = dict(kind='latest_persisted_summary', full_transitions=False,
                untimed='sorted_last_excluded_by_time_filter', metadata_only=True,
                retention='existing_records_unchanged_no_automatic_purge')


def fail(code='NOT_FOUND', status=404):
    raise QueryError(code, status, message='Audit metadata unavailable' if status != 400 else 'Invalid audit parameters')


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


@contextmanager
def snapshot():
    # Do not use control_store.connect/root: both may create/migrate/chmod state.
    path = Path(os.environ.get('SHIROKUMA_STATE_DIR') or
                Path(__file__).resolve().parents[2] / 'var/shirokuma') / 'jobs.sqlite3'
    db = None
    try:
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        if db.execute('PRAGMA user_version').fetchone()[0] != control_store.VERSION:
            fail('AUDIT_STORE_UNAVAILABLE', 503)
        yield db
    except sqlite3.Error:
        fail('AUDIT_STORE_UNAVAILABLE', 503)
    finally:
        if db is not None:
            db.close()


def _account(principal, role):
    if (type(principal) is not request_auth.AuthenticatedPrincipal or role not in request_auth.ROLES or
            principal.maximum_role not in request_auth.ROLES or
            request_auth.ROLES[role] > request_auth.ROLES[principal.maximum_role]):
        fail()
    account = request_auth._principal_accounts().get(principal.principal_id)
    if (not account or account.get('enabled', True) is not True or account.get('disabled', False) is not False or
            request_auth.ROLES[role] > request_auth.ROLES[account['role']]):
        fail()
    return account


def _authority(db, principal, role, scope, database_id):
    account = _account(principal, role)
    platform = (account['platform_admin'] is True and account['role'] == 'admin' and
                principal.maximum_role == 'admin' and principal.platform_admin is True and role == 'admin')
    context = dict(principal_id=principal.principal_id, selected_role=role, scope=scope,
                   database_id=database_id, platform_admin=platform,
                   binding_revision=None, generation_id=None, grant_revision=None)
    authority = dict(account=account, context=context)
    if scope == 'platform':
        if not platform:
            fail()
    else:
        database = registry.get_database(db, database_id)
        binding = registry.get_binding(db, database_id)
        if database is None or binding is None or database.state != 'READY':
            fail()
        try:
            decision = request_auth.authorize_database(principal, database_id, role,
                expected_generation_id=binding.applied_generation_id, db=db)
        except (PermissionError, ValueError):
            fail()
        if scope == 'database' and role != 'admin':
            fail()
        context.update(binding_revision=binding.desired_revision, generation_id=decision.generation_id,
                       grant_revision=decision.grant_revision)
        schemas = [asdict(s) for row in db.execute('SELECT id FROM schemas WHERE database_id=? ORDER BY id', (database_id,))
                   if database_inventory._permissions(role, s := registry.get_schema(db, row['id']))['query']]
        context['schema_ids'] = sorted(s['id'] for s in schemas)
        authority.update(decision=asdict(decision), binding=asdict(binding), schemas=schemas)
    return context, hashlib.sha256(_json(authority).encode()).hexdigest()


def _fence(principal, role, scope, database_id, original):
    # A NEW connection/transaction and freshly read accounts, grants and bindings.
    with snapshot() as live:
        _, current = _authority(live, principal, role, scope, database_id)
    if current != original:
        fail()


def _utc(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 40 or not re.fullmatch(
            r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)', value):
        fail('INVALID_INPUT', 400)
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
    except ValueError:
        fail('INVALID_INPUT', 400)


def filters(*, scope='self', database_id=None, source='', state='', search='', limit=50,
            cursor=None, **dates):
    if (scope not in ('self', 'database', 'platform') or
            scope != 'platform' and not database_id or
            type(limit) is not int or not 1 <= limit <= 100 or set(dates) - {'from', 'to'}):
        fail('INVALID_INPUT', 400)
    for value, maximum in ((database_id, 256), (source, 64), (state, 128), (search, 256), (cursor, 24000)):
        if value is not None and (not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value)):
            fail('INVALID_INPUT', 400)
    if database_id == '':
        fail('INVALID_INPUT', 400)
    if cursor == '':
        fail('STALE_CURSOR', 409)
    if source and source not in SOURCES:
        fail('INVALID_INPUT', 400)
    start, end = _utc(dates.get('from')), _utc(dates.get('to'))
    if start and end and start > end:
        fail('INVALID_INPUT', 400)
    return dict(scope=scope, database_id=database_id, source=source, state=state,
                search=search, limit=limit, **{'from': start, 'to': end})


# Every SQL expression below is authored here. No raw JSON/document column crosses
# the projection; JSON paths are fixed, including for search and visibility.
FIELDS = ('source', 'source_id', 'action', 'actor', 'database_id', 'schema_id', 'warehouse_id',
          'saved_role', 'state', 'created_at', 'updated_at', 'finished_at', 'revision',
          'committed', 'has_cleanup_error')
_DBT_FIELDS = ('kind', 'project_id', 'source_digest', 'error_code')
_COLUMNS = FIELDS + ('related_source', 'related_id') + _DBT_FIELDS
_PROJECTIONS = []
_SOURCE_SQL = {}
_TABLES = set()


def _add(source, table, joins='', **expressions):
    _TABLES.add(table)
    values = dict(source=repr(source), source_id='r.id', action=repr(source))
    values.update(expressions)
    _PROJECTIONS.append('SELECT ' + ','.join(values.get(k, 'NULL') + ' AS ' + k for k in _COLUMNS) +
                        f",'{table}' AS origin,r.rowid AS sequence FROM {table} r {joins}")
    _SOURCE_SQL[source] = _PROJECTIONS[-1]


def _path(column, key):
    return f"json_extract({column},'$.{key}')"


def _context(column):
    return {k: _path(column, k if k != 'saved_role' else 'selected_role')
            for k in ('database_id', 'schema_id', 'warehouse_id', 'saved_role')}


_add('query', 'query_records', 'LEFT JOIN query_execution_contexts e ON e.query_id=r.id',
     actor='r.actor', database_id='r.database_id', schema_id='r.schema_id', warehouse_id='r.warehouse_id',
     saved_role='e.selected_role', state='r.state', created_at='r.created_at',
     related_source="'dataops'", related_id='(SELECT d.operation_id FROM dataops_queries d WHERE d.query_id=r.id)')
_add('dataops', 'dataops_operations', actor='r.actor', **_context('r.context'),
     action='r.route', state='r.state', created_at='r.created_at', committed='r.committed',
     has_cleanup_error='r.cleanup_error IS NOT NULL')
_add('job_run', 'db_job_runs', 'JOIN db_jobs j ON j.id=r.job_id',
     actor='r.owner', **(_context('r.context') | dict(saved_role='r.selected_role', database_id='j.database_id')),
     state='r.status', created_at='r.created_at', finished_at='r.finished_at', revision='r.revision')
_add('job_step', 'db_job_steps', 'JOIN db_job_runs j ON j.id=r.run_id JOIN db_jobs p ON p.id=j.job_id',
     source_id="r.run_id || ':' || r.position", actor='j.owner',
     **(_context('j.context') | dict(saved_role='j.selected_role', database_id='p.database_id')),
     action='r.action', state='r.status', revision='j.revision',
     related_source="'job_run'", related_id='r.run_id')
for source, table, parent, fk in (('stage_run', 'db_stage_runs', 'db_stages', 'stage_id'),
                                 ('stage_scan', 'db_stage_scans', 'db_stages', 'stage_id'),
                                 ('mart_run', 'db_mart_runs', 'db_marts', 'mart_id')):
    _add(source, table, f'JOIN {parent} p ON p.id=r.{fk}' +
         (' LEFT JOIN warehouse_admissions a ON a.id=r.admission_id' if source == 'stage_scan' else ''),
         actor=_path('r.record', 'owner'),
         **(_context("json_extract(r.record,'$.context')") | dict(database_id='p.database_id')),
         state=_path('r.record', 'status'),
         created_at='a.created_at' if source == 'stage_scan' else _path('r.record', 'created_at'),
         finished_at=_path('r.record', 'finished_at'), revision=_path('r.record', 'stage_revision' if source.startswith('stage') else 'mart_revision'),
         committed=_path('r.record', 'committed'),
         has_cleanup_error="CASE WHEN json_type(r.record,'$.cleanup_error') IS NOT NULL THEN json_extract(r.record,'$.cleanup_error') IS NOT NULL END",
         **(dict(related_source="'dataops'", related_id='r.operation_id') if source == 'stage_run' else {}))
_add('dbt', 'dbt_runs', actor=_path('r.record', 'actor'),
     **(_context("json_extract(r.record,'$.context')") | dict(database_id='r.database_id')),
     action=_path('r.record', 'kind'), state=_path('r.record', 'state'),
     created_at=_path('r.record', 'created_at'), updated_at=_path('r.record', 'updated_at'),
     finished_at=_path('r.record', 'finished_at'),
     **{k: _path('r.record', k) for k in _DBT_FIELDS})
_add('schedule', 'db_schedules', actor='r.owner',
     **(_context('r.context') | dict(saved_role='r.selected_role', database_id='r.database_id')),
     state='r.last_status', revision='r.revision')
_add('schedule_tick', 'db_schedule_ticks', 'JOIN db_schedules p ON p.id=r.schedule_id',
     actor='p.owner', **(_context('r.context') | dict(database_id='p.database_id')),
     state='r.status', created_at='r.scheduled_for', revision='r.schedule_revision',
     related_source="'schedule'", related_id='r.schedule_id')
_add('alert', 'db_alerts', actor='r.owner',
     **(_context('r.context') | dict(saved_role='r.selected_role', database_id='r.database_id')),
     state="CASE WHEN r.resolved_at IS NOT NULL THEN 'resolved' WHEN r.acknowledged_at IS NOT NULL THEN 'acknowledged' ELSE 'open' END",
     created_at='r.first_seen_at', updated_at='r.last_seen_at', finished_at='r.resolved_at', revision='r.job_revision')
_add('wap_model_version', 'wap_model_versions',
     source_id="r.database_id || ':' || r.model_id || ':' || r.version", actor='r.actor',
     **(_context("json_extract(r.record,'$.context')") | dict(database_id='r.database_id')), revision='r.version')
for source, table in (('wap_build', 'wap_builds'), ('wap_audit', 'wap_audits'), ('wap_publication', 'wap_publications')):
    _add(source, table, "JOIN dataops_operations d ON d.id=r.operation_id AND json_extract(d.context,'$.database_id')=r.database_id",
         actor='d.actor', **(_context('d.context') | dict(database_id='r.database_id')), state='d.state', created_at='d.created_at',
         committed='d.committed', has_cleanup_error='d.cleanup_error IS NOT NULL', revision='r.version',
         related_source="'dataops'", related_id='r.operation_id')
_add('database_creation', 'db_creations', actor='r.actor', database_id='r.database_id', warehouse_id='r.warehouse_id',
     state="CASE WHEN r.creation_mode='logical_v1' THEN r.logical_state ELSE r.terminal_outcome END",
     created_at='r.created_at', updated_at='r.updated_at', revision='r.revision',
     related_source="'management'", related_id='r.create_operation_id')
_add('database_request', 'db_creation_requests', 'LEFT JOIN db_creations c ON c.id=r.creation_id',
     actor='r.actor', database_id='c.database_id', warehouse_id='c.warehouse_id', action='r.action',
     state='r.dispatch_state', created_at='r.created_at', updated_at='r.updated_at', revision='r.operation_revision',
     related_source="'database_creation'", related_id='r.creation_id')
_add('logical_request', 'logical_requests', 'JOIN db_creations c ON c.id=r.creation_id',
     actor='r.actor', database_id='c.database_id', warehouse_id='c.warehouse_id', action='r.action',
     state='r.dispatch_state', created_at='r.created_at', updated_at='r.updated_at',
     related_source="'database_creation'", related_id='r.creation_id')
_MANAGEMENT_DB = """CASE r.action
 WHEN 'GRANT_CHANGE' THEN (SELECT a.database_id FROM grant_audit a WHERE a.operation_id=r.id ORDER BY a.id DESC LIMIT 1)
 WHEN 'DISPLAY_METADATA_CHANGE' THEN (SELECT CASE a.resource_type WHEN 'database' THEN a.resource_id
     WHEN 'schema' THEN (SELECT s.database_id FROM schemas s WHERE s.id=a.resource_id) END FROM metadata_audit a WHERE a.operation_id=r.id)
 WHEN 'DATABASE_RETIRE' THEN (SELECT a.database_id FROM database_retirements a WHERE a.operation_id=r.id)
 WHEN 'DATABASE_CREATE' THEN COALESCE((SELECT c.database_id FROM db_creations c WHERE c.create_operation_id=r.id),
     (SELECT c.database_id FROM database_create_requests c WHERE c.operation_id=r.id LIMIT 1))
 ELSE NULL END"""


def _stamp(name):
    return f"(SELECT json_extract(t.value,'$[1]') FROM json_each(r.timestamps) t WHERE json_extract(t.value,'$[0]')='{name}' ORDER BY t.key LIMIT 1)"


_add('management', 'operations', actor='r.actor', action='r.action', database_id=_MANAGEMENT_DB,
     state='r.state', created_at=_stamp('created_at'), updated_at=_stamp('updated_at'),
     finished_at=_stamp('finished_at'), revision='r.expected_revision')
SOURCES = tuple(re.findall(r"SELECT '([^']+)' AS source", '\n'.join(_PROJECTIONS)))
MANAGEMENT = ('management', 'database_creation', 'database_request', 'logical_request')
_SQL = ' UNION ALL '.join(_PROJECTIONS)


def _encode(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def _decode(value):
    return base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)


def _token(value):
    raw = _json(value).encode()
    return _encode(raw) + '.' + _encode(hmac.digest(_KEY, raw, 'sha256'))


def _untoken(value):
    try:
        raw, signature = value.split('.')
        raw = _decode(raw)
        if not hmac.compare_digest(hmac.digest(_KEY, raw, 'sha256'), _decode(signature)):
            raise ValueError()
        return json.loads(raw)
    except (ValueError, TypeError, UnicodeError):
        fail('STALE_CURSOR', 409)


MAX_LINKS = 24


def _relations(db, row):
    """One hop of authored FKs only; never read JSON payloads or inspect services."""
    links = []

    def add(relation, source, identifier, event=True, *, database_id=row['database_id']):
        if identifier is not None:
            link = (relation, source, str(identifier), database_id, event)
            if link not in links:
                links.append(link)

    def select(sql, args):
        for relation, source, identifier in db.execute(sql + ' LIMIT ?', (*args, MAX_LINKS)):
            add(relation, source, identifier)

    source, identifier, database = row['source'], row['source_id'], row['database_id']
    for field, kind in (('database_id', 'database'), ('schema_id', 'schema'), ('warehouse_id', 'warehouse')):
        add(kind, kind, row[field], False)
    add(row['related_source'], row['related_source'], row['related_id'])
    if source == 'dataops':
        select("SELECT 'query','query',query_id FROM dataops_queries WHERE operation_id=?", (identifier,))
        select("""SELECT 'job_step','job_step',o.run_id || ':' || o.position
            FROM db_job_operations o JOIN db_job_runs r ON r.id=o.run_id
            JOIN db_jobs j ON j.id=r.job_id WHERE o.operation_id=? AND j.database_id=?""", (identifier, database))
        select("""SELECT 'job_run','job_run',o.run_id FROM db_job_operations o
            JOIN db_job_runs r ON r.id=o.run_id JOIN db_jobs j ON j.id=r.job_id
            WHERE o.operation_id=? AND j.database_id=?""", (identifier, database))
    if source in ('job_run', 'job_step'):
        if source == 'job_run':
            select("SELECT 'operation','dataops',operation_id FROM db_job_operations WHERE run_id=?", (identifier,))
        else:
            select("SELECT 'operation','dataops',operation_id FROM db_job_operations WHERE run_id=? AND position=?",
                   (row['related_id'], int(identifier.rsplit(':', 1)[1])))
    if source == 'dbt':
        select("SELECT 'operation','dataops',operation_id FROM dbt_run_operations WHERE run_id=?", (identifier,))
        for field, relation in (('plan_id', 'plan'), ('parent_run_id', 'run')):
            select(f"SELECT '{relation}','dbt',json_extract(record,'$.{field}') FROM dbt_runs WHERE id=? AND database_id=?", (identifier, database))
    if source == 'dataops':
        select("""SELECT 'dbt','dbt',o.run_id FROM dbt_run_operations o
            JOIN dbt_runs r ON r.id=o.run_id WHERE o.operation_id=? AND r.database_id=?""", (identifier, database))
    if source == 'mart_run':
        select("SELECT 'operation','dataops',operation_id FROM db_mart_operations WHERE run_id=?", (identifier,))
    if source == 'stage_scan':
        select("SELECT 'operation','dataops',operation_id FROM db_stage_scan_operations WHERE scan_id=?", (identifier,))
    if source in ('wap_build', 'wap_audit', 'wap_publication', 'wap_model_version', 'dataops'):
        # All WAP correlations are the persisted model/version and operation FKs.
        for kind, table in (('wap_build', 'wap_builds'), ('wap_audit', 'wap_audits'),
                            ('wap_publication', 'wap_publications')):
            if source == 'wap_model_version':
                select(f"SELECT '{kind}','{kind}',id FROM {table} WHERE database_id=? AND database_id || ':' || model_id || ':' || version=?",
                       (database, identifier))
            elif source == 'dataops' or source == kind:
                column = 'operation_id' if source == 'dataops' else 'id'
                for model, version in db.execute(f'SELECT model_id,version FROM {table} WHERE {column}=? AND database_id=? LIMIT 1', (identifier, database)):
                    add('model', 'wap_model', model, False)
                    add('model_version', 'wap_model_version', f'{database}:{model}:{version}')
                    if source == kind:
                        # Siblings share a version FK; their records are never read.
                        for peer, peer_table in (('wap_build', 'wap_builds'), ('wap_audit', 'wap_audits'), ('wap_publication', 'wap_publications')):
                            if peer != source:
                                select(f"SELECT '{peer}','{peer}',id FROM {peer_table} WHERE database_id=? AND model_id=? AND version=?",
                                       (database, model, version))
                    else:
                        select(f"SELECT '{kind}','{kind}',id FROM {table} WHERE operation_id=? AND database_id=?", (identifier, database))
    if source == 'wap_model_version':
        for model, in db.execute("SELECT model_id FROM wap_model_versions WHERE database_id=? AND database_id || ':' || model_id || ':' || version=? LIMIT 1", (database, identifier)):
            add('model', 'wap_model', model, False)
        select("""SELECT 'operation','dataops',operation_id FROM wap_models WHERE database_id=?
            AND database_id || ':' || model_id || ':' || version=?""", (database, identifier))
    if source == 'dataops':
        select("""SELECT 'model_version','wap_model_version',database_id || ':' || model_id || ':' || version
            FROM wap_models WHERE operation_id=? AND database_id=?""", (identifier, database))
    if source == 'database_creation':
        for kind, table in (('database_request', 'db_creation_requests'), ('logical_request', 'logical_requests')):
            select(f"SELECT 'request','{kind}',id FROM {table} WHERE creation_id=?", (identifier,))
    if source == 'management':
        select("SELECT 'creation','database_creation',id FROM db_creations WHERE create_operation_id=? AND database_id=?", (identifier, database))
        for principal, operation, apply, grant_database in db.execute('''SELECT principal_id,operation_id,apply_operation_id,database_id
                FROM grant_audit WHERE (operation_id=? OR apply_operation_id=?)
                AND (? IS NULL OR database_id=?) LIMIT ?''',
                (identifier, identifier, database, database, MAX_LINKS)):
            add('target_principal', 'principal', principal, False)
            if operation != identifier:
                add('grant_operation', 'management', operation, database_id=grant_database)
            if apply != identifier:
                add('apply_operation', 'management', apply, database_id=None)
        for kind, resource in db.execute('SELECT resource_type,resource_id FROM metadata_audit WHERE operation_id=? LIMIT ?', (identifier, MAX_LINKS)):
            if kind in ('database', 'schema'):
                add('display_resource', kind, resource, False)
        for retired, in db.execute('SELECT database_id FROM database_retirements WHERE operation_id=? LIMIT 1', (identifier,)):
            add('retired_database', 'database', retired, False)
    return links[:MAX_LINKS]


def _links(db, page, principal, role, f, context, ceilings):
    relations = [_relations(db, row) for row in page]
    wanted = sorted({(source, identifier, database) for links in relations
                     for _, source, identifier, database, event in links if event})
    visible = set()
    if wanted:
        clauses, args = _where(principal, role,
            f | dict(source='', state='', search='', **{'from': None, 'to': None}), context)
        clauses.append('sequence <= CASE origin ' + ' '.join('WHEN ? THEN ?' for _ in ceilings) + ' END')
        for table, ceiling in ceilings.items():
            args.extend((table, ceiling))
        values = ','.join('(?,?,?)' for _ in wanted)
        # Look up only the candidate source types, rather than traversing the store.
        projections = ' UNION ALL '.join(_SOURCE_SQL[s] for s in sorted({s for s, _, _ in wanted}))
        sql = ('WITH wanted(s,i,d) AS (VALUES ' + values + '), metadata AS (' + projections +
               ') SELECT DISTINCT source,source_id,database_id FROM metadata JOIN wanted ON '
               'source=s AND source_id=i AND database_id IS d WHERE ' + ' AND '.join(clauses) + ' LIMIT ?')
        visible = {tuple(r) for r in db.execute(sql, (*[v for link in wanted for v in link], *args, len(wanted)))}
    # Hidden peers (including a different actor/role/schema) disclose no identifiers.
    # Resource FKs are metadata, never a source SQL/result capability.
    return [[dict(relation=relation, source=source, source_id=identifier,
                  event_id=_encode(_json([source, identifier]).encode()) if event else None)
             for relation, source, identifier, database, event in links
             if not event or (source, identifier, database) in visible] for links in relations]


def _event(row, links):
    event = {k: row[k] for k in FIELDS}
    event['id'] = _encode(_json([event['source'], event['source_id']]).encode())
    if row['source'] == 'dbt':
        event.update({k: row[k] for k in _DBT_FIELDS})
    event['effective_role'] = None  # Historical effective authority is not stored.
    event['links'] = links
    for key in ('committed', 'has_cleanup_error'):
        if event[key] is not None:
            event[key] = bool(event[key])
    return event


def _where(principal, role, f, context):
    clauses, args = [], []
    if role == 'bi_viewer':
        clauses.append("source != 'dbt'")
    if f['scope'] == 'platform':
        clauses.append('source IN (' + ','.join('?' for _ in MANAGEMENT) + ')')
        args.extend(MANAGEMENT)
    if f['database_id'] is not None:
        clauses.append('database_id=?')
        args.append(f['database_id'])
    if f['scope'] == 'self':
        clauses.extend(['actor=?', 'saved_role=?', 'schema_id IN (' + ','.join('?' for _ in context['schema_ids']) + ')'])
        args.extend([principal.principal_id, role, *context['schema_ids']])
    elif f['scope'] == 'database':
        clauses.append('(schema_id IS NULL OR schema_id IN (' + ','.join('?' for _ in context['schema_ids']) + '))')
        args.extend(context['schema_ids'])
    for key in ('source', 'state'):
        if f[key]:
            clauses.append(key + '=?')
            args.append(f[key])
    if f['search']:
        clauses.append("instr(lower(coalesce(source_id,'') || ' ' || source || ' ' || coalesce(action,'') || ' ' || coalesce(actor,'') || ' ' || coalesce(state,'') || ' ' || coalesce(database_id,'')),lower(?))>0")
        args.append(f['search'])
    for key, op in (('from', '>='), ('to', '<=')):
        if f[key]:
            clauses.append('julianday(created_at)' + op + 'julianday(?)')
            args.append(f[key])
    return clauses, args


def events(principal, role, *, event_id=None, cursor=None, **parameters):
    f = filters(cursor=cursor, **parameters)
    with snapshot() as db:
        context, auth = _authority(db, principal, role, f['scope'], f['database_id'])
        binding = dict(version=1, principal=principal.principal_id, role=role, auth=auth, filters=f)
        saved = _untoken(cursor) if cursor else None
        if saved is not None and (not isinstance(saved, dict) or saved.get('binding') != binding):
            fail('STALE_CURSOR', 409)
        ceilings = saved['ceilings'] if saved else {t: db.execute(f'SELECT coalesce(max(rowid),0) FROM {t}').fetchone()[0] for t in sorted(_TABLES)}
        clauses, args = _where(principal, role, f, context)
        clauses.append('sequence <= CASE origin ' + ' '.join('WHEN ? THEN ?' for _ in ceilings) + ' END')
        for table, ceiling in ceilings.items():
            args.extend((table, ceiling))
        if event_id:
            try:
                source, source_id = json.loads(_decode(event_id))
                if source not in SOURCES or not isinstance(source_id, str):
                    raise ValueError()
            except (ValueError, TypeError, UnicodeError):
                fail()
            clauses.extend(['source=?', 'source_id=?'])
            args.extend((source, source_id))
        key = "(coalesce(julianday(created_at),-1),source,source_id)"
        if saved:
            for name, op in (('upper', '<='), ('last', '<')):
                if saved[name] is not None:
                    clauses.append(key + op + '(?,?,?)')
                    args.extend(saved[name])
        sql = 'WITH metadata AS (' + _SQL + ') SELECT *,coalesce(julianday(created_at),-1) AS time_key FROM metadata WHERE ' + ' AND '.join(clauses) + ' ORDER BY coalesce(julianday(created_at),-1) DESC,source DESC,source_id DESC LIMIT ?'
        rows = db.execute(sql, (*args, 1 if event_id else f['limit'] + 1)).fetchall()
        if event_id and not rows:
            fail()
        page = rows[:f['limit']]
        links = _links(db, page, principal, role, f, context, ceilings)
        coordinate = lambda r: [r['time_key'], r['source'], r['source_id']]
        upper = saved['upper'] if saved else coordinate(page[0]) if page else None
        next_cursor = None
        if not event_id and len(rows) > f['limit']:
            next_cursor = _token(dict(binding=binding, ceilings=ceilings,
                upper=upper, last=coordinate(page[-1])))
        result = dict(context=context, coverage=dict(COVERAGE))
        if event_id:
            result['event'] = _event(rows[0], links[0])
        else:
            result.update(items=[_event(r, relation) for r, relation in zip(page, links)],
                          page_cursor=_token(dict(binding=binding, ceilings=ceilings, upper=upper,
                                                  last=saved['last'] if saved else None)),
                          next_cursor=next_cursor, filters=f,
                          observed_at=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'))
    _fence(principal, role, f['scope'], f['database_id'], auth)
    return result


def capabilities(principal, role):
    account = _account(principal, role)
    platform = (role == 'admin' and principal.maximum_role == 'admin' and principal.platform_admin and
                account['role'] == 'admin' and account['platform_admin'])
    scopes = ['self'] + (['database'] if role == 'admin' else []) + (['platform'] if platform else [])
    result = dict(scopes=scopes, sources=list(SOURCES), selected_role=role,
                  platform_allowed=bool(platform), metadata_only=True, coverage=dict(COVERAGE),
                  retention=COVERAGE['retention'])
    if _account(principal, role) != account:
        fail()
    return result


TELEMETRY_FIELDS = ('event_id', 'timestamp', 'agent_id', 'action_type', 'target_system', 'status', 'trace_id', 'duration_ms')
TELEMETRY_SQL = 'SELECT ' + ','.join(TELEMETRY_FIELDS) + ' FROM agenthouse.pawprints ORDER BY timestamp DESC,event_id DESC LIMIT 101'


def telemetry(principal, role, *, limit=100):
    if type(limit) is not int or not 1 <= limit <= 100:
        fail('INVALID_INPUT', 400)
    with snapshot() as db:
        context, auth = _authority(db, principal, role, 'platform', None)
    result = dict(availability='available', source='legacy_pawprints', limited_preview=True,
                  limit=limit, database_correlation=False, items=[], truncated=False, context=context)
    try:
        from agenthouse import manage
        rows = manage.Client().rows(TELEMETRY_SQL, timeout=3)
        if not isinstance(rows, list) or len(rows) > 101 or any(not isinstance(r, dict) or
                any(k not in r or not isinstance(r[k], (str, int, float, type(None))) for k in TELEMETRY_FIELDS) for r in rows):
            raise ValueError()
        result.update(items=[{k: r[k] for k in TELEMETRY_FIELDS} for r in rows[:limit]], truncated=len(rows) > limit)
    except Exception:
        result.update(availability='unavailable', code='TELEMETRY_UNAVAILABLE')
    _fence(principal, role, 'platform', None, auth)
    return result
