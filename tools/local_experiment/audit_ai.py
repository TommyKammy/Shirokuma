"""Request-local, read-only AI metadata projection. No AI lifecycle imports."""
from dataclasses import asdict
from datetime import datetime, timezone
import json

import request_auth


def safe_context(raw):
    # C2A imports audit_service: defer these pure validators until request time.
    from db_bear_observations import _decode, _context
    try:
        value = _decode(raw, 8192)
        if type(value) is not dict or value.get('selected_role') not in request_auth.ROLES:
            return None
        return json.dumps(asdict(_context(value, value['selected_role'])))
    except (ValueError, UnicodeError, RecursionError, TypeError):
        return None


def project(identifier, actor, context, raw):
    from db_bear_observations import _decode, _text, _time, AI_KINDS, AI_STATES
    try:
        identifier = identifier.decode('utf-8')
        actor = actor.decode('utf-8')
        if not _text(identifier, 256) or not _text(actor, 256):
            return None
        record = _decode(raw, 262144)
        if (type(record) is not dict or record.get('id') != identifier or
                record.get('actor') != actor or record.get('kind') not in AI_KINDS or
                record.get('state') not in AI_STATES):
            return None
        saved = json.loads(context)
        # Validate types before equality (True == 1 must never pass).
        from db_bear_observations import _context
        if asdict(_context(record.get('context'), saved['selected_role'])) != saved:
            return None
        times = {}
        for key in ('created_at', 'updated_at', 'finished_at'):
            value = record.get(key)
            if value is None and key == 'finished_at':
                times[key] = None
            else:
                times[key] = _time(value)
                # Reject offsets that overflow the representable UTC range.
                datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)
        return json.dumps(dict(source_id=identifier, actor=actor, action=record['kind'],
            state=record['state'], **times, **{k: saved[k] for k in
                ('database_id', 'schema_id', 'warehouse_id')}, saved_role=saved['selected_role']))
    except (ValueError, UnicodeError, RecursionError, TypeError, AttributeError, OverflowError):
        return None


def branch(db, context, ceiling, columns, *, event_id=None):
    """Materialization orders scope, byte guards, strict validation, then paging."""
    db.create_function('audit_ai_context', 1, safe_context)
    db.create_function('audit_ai_project', 4, project)
    conditions = ["json_extract(c,'$.database_id')=?",
                  "json_extract(c,'$.schema_id') IN (" + ','.join('?' for _ in context['schema_ids']) + ')']
    args = [ceiling]
    early = ''
    if context['scope'] == 'self':
        early = ' AND actor=?'
        args.append(context['principal_id'])
        conditions.append("json_extract(c,'$.selected_role')=?")
    if event_id is not None:
        early += ' AND id=?'
        args.append(event_id)
    args.extend([context['database_id'], *context['schema_ids']])
    if context['scope'] == 'self':
        args.append(context['selected_role'])
    ctes = '''ai_contexts AS MATERIALIZED (
        SELECT rowid AS rid, CASE WHEN typeof(context)='text'
            AND length(CAST(context AS BLOB))<=8192
            THEN audit_ai_context(CAST(context AS BLOB)) END AS c
        FROM ai_explanations WHERE rowid<=?''' + early + '''
    ), ai_scoped AS MATERIALIZED (
        SELECT rid,c FROM ai_contexts WHERE c IS NOT NULL AND ''' + ' AND '.join(conditions) + '''
    ), ai_projected AS MATERIALIZED (
        SELECT a.rowid AS sequence, CASE WHEN typeof(a.record)='text'
            AND length(CAST(a.record AS BLOB))<=262144
            AND typeof(a.id)='text' AND length(CAST(a.id AS BLOB))<=1024
            AND typeof(a.actor)='text' AND length(CAST(a.actor AS BLOB))<=1024
            THEN audit_ai_project(CAST(a.id AS BLOB),CAST(a.actor AS BLOB),s.c,
                                  CAST(a.record AS BLOB)) END AS p
        FROM ai_scoped s JOIN ai_explanations a ON a.rowid=s.rid
    ), '''
    public = {'source_id', 'actor', 'action', 'state', 'created_at', 'updated_at',
              'finished_at', 'database_id', 'schema_id', 'warehouse_id', 'saved_role'}
    expressions = ["'ai' AS source" if k == 'source' else
                   (f"json_extract(p,'$.{k}') AS {k}" if k in public else f'NULL AS {k}')
                   for k in columns]
    sql = 'SELECT ' + ','.join(expressions) + ", 'ai_explanations' AS origin,sequence FROM ai_projected WHERE p IS NOT NULL AND julianday(json_extract(p,'$.created_at')) IS NOT NULL"
    return ctes, sql, args
