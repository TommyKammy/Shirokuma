"""Latest owned ordinary SQL text; local authority reads, no query observation."""
import sqlglot
from sqlglot import exp

import control_store
import dataops_routes as common
import db_ai as ai
from query_context import QueryError, revalidate_admission


def _authority(principal, context, pins=(), *, backend_username=None):
    common.current_context(principal, context)
    with control_store.connect() as db:
        db.execute('BEGIN')
        revalidate_admission(db, principal, context, backend_username=backend_username)
        ai._schema_authority(db, context, pins)


def latest(principal, context):
    _authority(principal, context)
    metadata = context.public_metadata()
    query_fields = {'database_id', 'schema_id', 'warehouse_id', 'generation_id'}
    columns = [('q.' if key in query_fields else 'e.') + key for key in metadata]
    scope = ' AND '.join(column + ' IS ?' for column in columns)
    # Stream only the exact actor/context scope. Internal links are excluded by
    # their existing ledgers, including plan queries whose SQL looks ordinary.
    selected = None
    with control_store.connect() as db:
        candidates = db.execute("SELECT q.id,q.created_at,q.state,e.sql,e.backend_username,"
            + ",".join(columns) + """
            FROM query_records q JOIN query_execution_contexts e ON e.query_id=q.id
            WHERE q.actor=? AND """ + scope + """ AND e.mode='read-only'
            AND q.state IN ('QUEUED','RUNNING','FINISHED','FAILED','CANCELED','UNKNOWN')
            AND NOT EXISTS (SELECT 1 FROM dataops_queries d WHERE d.query_id=q.id)
            AND NOT EXISTS (SELECT 1 FROM ai_explanations a
                WHERE json_extract(a.record,'$.plan.query_id')=q.id OR a.id=e.request_id)
            AND NOT EXISTS (SELECT 1 FROM warehouse_admissions a WHERE a.subject_id=q.id
                AND (a.kind!='query' OR a.parent_id IS NOT NULL
                    OR EXISTS (SELECT 1 FROM warehouse_admissions child WHERE child.parent_id=a.id)))
            ORDER BY q.created_at DESC,q.rowid DESC""", (principal.principal_id, *metadata.values()))
        for row in candidates:
            sql = row['sql']
            if not isinstance(sql, str) or not sql.strip() or len(sql.encode()) > 32768:
                continue
            try:
                # History has not been explicitly submitted to AI. Keep parser
                # diagnostics private before invoking the shared A1 policy.
                trees = sqlglot.parse(sql, read='trino', error_message_context=0,
                                     error_level=sqlglot.ErrorLevel.RAISE)
                if (len(trees) != 1 or not isinstance(trees[0], (exp.Select, exp.SetOperation))
                        or any(isinstance(node, exp.Command) for node in trees[0].walk())):
                    continue
            except sqlglot.errors.SqlglotError:
                continue
            try:
                refs, _ = ai._parse(sql, context)
            except QueryError as error:
                if error.code != 'AI_INPUT_INVALID':
                    raise
                continue
            # Once structurally eligible, authority loss must refuse this load;
            # it must never select an older query to conceal the denial.
            pins = []
            for name, _ in refs:
                schema = db.execute('SELECT id FROM schemas WHERE database_id=? AND sql_name=?',
                    (context.database_id, name)).fetchone()
                if schema is None:
                    raise QueryError('SQL_PERMISSION_DENIED', 403)
                pins.append(dict(schema_id=schema['id'], schema=name))
            selected = dict(row), pins
            break
        candidates.close()
    if selected is None:
        _authority(principal, context)
        return None
    row, pins = selected
    _authority(principal, context, pins, backend_username=row['backend_username'])
    result = dict(query_id=row['id'], created_at=row['created_at'], state=row['state'],
                  sql=row['sql'], context={key: row[key] for key in metadata})
    _authority(principal, context, pins, backend_username=row['backend_username'])
    return result
