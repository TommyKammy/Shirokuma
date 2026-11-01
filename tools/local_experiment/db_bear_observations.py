"""Bounded own-context persisted observations, using only read-only snapshots."""
from datetime import datetime, timezone
import json
import re

import audit_service as audit
import data_management as registry
import database_inventory as inventory
from query_context import QueryError, ResolvedQueryContext, revalidate_admission

FIELDS = tuple(ResolvedQueryContext.__dataclass_fields__)
QUERY_STATES = {'QUEUED', 'RUNNING', 'FINISHED', 'FAILED', 'CANCELED', 'UNKNOWN'}
AI_STATES = {'QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELED', 'UNKNOWN'}
AI_KINDS = {'sql_explanation', 'sql_plan_explanation', 'saved_plan_explanation',
            'sql_proposal', 'catalog_proposal'}


def _invalid():
    raise ValueError('Invalid observation context')


def _decode(raw, cap):
    if isinstance(raw, bytes):
        if len(raw) > cap:
            _invalid()
        raw = raw.decode('utf-8')
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > cap:
        _invalid()
    # Bound nesting before invoking Python's recursive decoder. Ignore quoted
    # delimiters, including escaped quotes, without interpreting source values.
    depth, quoted, escaped = 0, False, False
    for char in raw:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '{[':
            depth += 1
            if depth > 32:
                _invalid()
        elif char in '}]':
            depth -= 1

    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                _invalid()
            result[key] = value
        return result

    def constant(_):
        _invalid()

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    # Also reject escaped invalid Unicode and finite-looking float overflows.
    json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
    return value


def _context(value, role):
    if (type(value) is not dict or set(value) != set(FIELDS)
            or any(not _text(value[k], 256) for k in FIELDS
                   if k not in ('binding_revision', 'grant_revision'))
            or type(value['binding_revision']) is not int or value['binding_revision'] < 1
            or (value['grant_revision'] is not None and
                (type(value['grant_revision']) is not int or value['grant_revision'] < 1))
            or value['selected_role'] != role or value['warehouse_id'] != 'LOCAL'):
        _invalid()
    return ResolvedQueryContext(**value)


def parse_context(raw, role):
    try:
        return _context(_decode(raw, 8192), role)
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise QueryError('INVALID_INPUT', 400) from None


def _text(value, cap):
    return (type(value) is str and 0 < len(value) <= cap and value.strip() != ''
            and value.isprintable())


def _time(value):
    if (not _text(value, 64) or not re.fullmatch(
            r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])', value)):
        _invalid()
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        _invalid()
    return value


def _authority(db, principal, context):
    authority, fingerprint = audit._authority(db, principal, context.selected_role,
                                             'self', context.database_id)
    revalidate_admission(db, principal, context)
    database = registry.get_database(db, context.database_id)
    binding = registry.get_binding(db, context.database_id)
    generation = registry.get_generation(db, context.generation_id)
    schema = registry.get_schema(db, context.schema_id)
    if (database is None or database.state != 'READY' or binding is None
            or binding.applied_revision != context.binding_revision
            or binding.desired_revision != context.binding_revision
            or binding.applied_generation_id != context.generation_id
            or generation is None or generation.warehouse_id != 'LOCAL'):
        raise QueryError('STALE_BINDING', 409)
    permissions = inventory._permissions(context.selected_role, schema)
    if not permissions['query']:
        raise QueryError('SQL_PERMISSION_DENIED', 403)
    return fingerprint, permissions, inventory._allowed_modes(context.selected_role, schema), authority['platform_admin']


def _queries(db, principal, context):
    columns = [('q.' if k in ('database_id', 'schema_id', 'warehouse_id', 'generation_id')
                else 'e.') + k for k in FIELDS]
    conditions = []
    for key, col in zip(FIELDS, columns):
        kind = 'integer' if key in ('binding_revision', 'grant_revision') else 'text'
        if key == 'grant_revision' and context.grant_revision is None:
            kind = 'null'
        conditions.append(f"(typeof({col})='{kind}' AND {col} IS ?)")
    rows = db.execute('''SELECT
        CASE WHEN typeof(q.id)='text' AND length(CAST(q.id AS BLOB))<=1024
             THEN CAST(q.id AS BLOB) END AS id,
        CASE WHEN q.state IN ('QUEUED','RUNNING','FINISHED','FAILED','CANCELED','UNKNOWN')
             THEN q.state END AS state,
        q.created_at FROM query_records q
        INNER JOIN query_execution_contexts e ON e.query_id=q.id
        WHERE q.actor=? AND ''' + ' AND '.join(conditions) + '''
        AND typeof(q.created_at)='text' AND length(CAST(q.created_at AS BLOB))<=64
        AND substr(q.created_at,1,19) GLOB
            '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]'
        AND (
            (substr(q.created_at,-1)='Z' AND (length(q.created_at)=20 OR
                (length(q.created_at)>=22 AND substr(q.created_at,20,1)='.' AND
                 substr(q.created_at,21,length(q.created_at)-21) NOT GLOB '*[^0-9]*')))
            OR
            (substr(q.created_at,-6) GLOB '[-+][0-9][0-9]:[0-9][0-9]' AND
                (length(q.created_at)=25 OR
                 (length(q.created_at)>=27 AND substr(q.created_at,20,1)='.' AND
                  substr(q.created_at,21,length(q.created_at)-26) NOT GLOB '*[^0-9]*')))
        )
        AND julianday(q.created_at) IS NOT NULL
        ORDER BY julianday(q.created_at) DESC,q.rowid DESC LIMIT 21''',
        (principal.principal_id, *(getattr(context, key) for key in FIELDS))).fetchall()
    items = []
    for row in rows[:20]:
        try:
            ident = row['id'].decode('utf-8') if isinstance(row['id'], bytes) else row['id']
            if not _text(ident, 256) or row['state'] not in QUERY_STATES:
                _invalid()
            items.append(dict(id=ident, kind='query', state=row['state'],
                              created_at=_time(row['created_at']), updated_at=None))
        except (ValueError, UnicodeError, TypeError):
            continue
    return dict(items=items, has_more=len(rows) == 21, omitted=min(20, len(rows)) - len(items))


def _ai(db, principal, context):
    conditions = ["json_type(c)='object'", '(SELECT count(*) FROM json_each(c))=9']
    for key in FIELDS:
        kind = 'integer' if key in ('binding_revision', 'grant_revision') else 'text'
        if key == 'grant_revision' and context.grant_revision is None:
            kind = 'null'
        conditions.append(f"(json_type(c,'$.{key}')='{kind}' AND json_extract(c,'$.{key}') IS ?)")
    # Materialize the exact context match before touching records. Nested CASE
    # bounds record bytes before either JSON validity or timestamp extraction.
    # Only scalar time is extracted for ordering; blobs are fetched separately
    # for the first twenty candidates, so the sentinel never loads a record.
    rows = db.execute('''WITH scoped AS MATERIALIZED (
        SELECT rowid AS rid,
        CASE WHEN typeof(context)='text' AND length(CAST(context AS BLOB))<=8192
             THEN CASE WHEN json_valid(context) THEN context END END AS c
        FROM ai_explanations WHERE actor=?
    ), matched AS MATERIALIZED (
        SELECT rid FROM scoped WHERE ''' + ' AND '.join(conditions) + '''
    ), timed AS MATERIALIZED (
        SELECT a.rowid AS rid,
        CASE WHEN typeof(a.record)='text' AND length(CAST(a.record AS BLOB))<=262144
             THEN CASE WHEN json_valid(a.record)
                  THEN json_extract(a.record,'$.created_at') END END AS created
        FROM ai_explanations a JOIN matched m ON m.rid=a.rowid
    ) SELECT rid FROM timed
        WHERE typeof(created)='text' AND length(CAST(created AS BLOB))<=64
        AND substr(created,1,19) GLOB
            '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]'
        AND (
            (substr(created,-1)='Z' AND (length(created)=20 OR
                (length(created)>=22 AND substr(created,20,1)='.' AND
                 substr(created,21,length(created)-21) NOT GLOB '*[^0-9]*')))
            OR
            (substr(created,-6) GLOB '[-+][0-9][0-9]:[0-9][0-9]' AND
                (length(created)=25 OR
                 (length(created)>=27 AND substr(created,20,1)='.' AND
                  substr(created,21,length(created)-26) NOT GLOB '*[^0-9]*')))
        )
        AND julianday(created) IS NOT NULL
        ORDER BY julianday(created) DESC,rid DESC LIMIT 21''',
        (principal.principal_id, *(getattr(context, key) for key in FIELDS))).fetchall()
    items = []
    for candidate in rows[:20]:
        row = db.execute('''SELECT
            CASE WHEN typeof(id)='text' AND length(CAST(id AS BLOB))<=1024
                 THEN CAST(id AS BLOB) END AS id,
            CAST(context AS BLOB) AS context,
            CASE WHEN typeof(record)='text' AND length(CAST(record AS BLOB))<=262144
                 THEN CAST(record AS BLOB) END AS record
            FROM ai_explanations WHERE rowid=?''', (candidate['rid'],)).fetchone()
        try:
            ident = row['id'].decode('utf-8') if isinstance(row['id'], bytes) else row['id']
            saved = _context(_decode(row['context'], 8192), context.selected_role)
            record = _decode(row['record'], 262144)
            if (saved != context or type(record) is not dict or
                    not _text(ident, 256) or record.get('id') != ident or
                    record.get('actor') != principal.principal_id or
                    _context(record.get('context'), context.selected_role) != context or
                    record.get('kind') not in AI_KINDS or record.get('state') not in AI_STATES):
                _invalid()
            items.append(dict(id=ident, kind=record['kind'], state=record['state'],
                created_at=_time(record.get('created_at')), updated_at=_time(record.get('updated_at'))))
        except (ValueError, UnicodeError, RecursionError, TypeError):
            continue
    return dict(items=items, has_more=len(rows) == 21, omitted=min(20, len(rows)) - len(items))


def observe(principal, context):
    # Validate internal callers as strictly as the HTTP adapter.
    try:
        _context(context.public_metadata(), context.selected_role)
    except (ValueError, UnicodeError, TypeError, AttributeError):
        raise QueryError('INVALID_INPUT', 400) from None
    with audit.snapshot() as db:
        authority = _authority(db, principal, context)
        queries, ai = _queries(db, principal, context), _ai(db, principal, context)
    with audit.snapshot() as fresh:
        current = _authority(fresh, principal, context)
    if current != authority:
        raise QueryError('DB_ACCESS_REVOKED', 403)
    _, permissions, modes, platform = current
    return dict(context=context.public_metadata(),
        observed_at=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        coverage=dict(kind='latest_persisted_own_context', limit_per_source=20,
                      full_history=False, live_runtime=False),
        sre=dict(queries=queries, ai=ai),
        policy=dict(context_authorized=True, schema_permissions=permissions, sql_modes=modes,
                    statement_approval=False, shared_local=True),
        platform=dict(allowed=platform, code='PLATFORM_OBSERVATION_DEFERRED' if platform
                      else 'PLATFORM_ADMIN_REQUIRED'))
