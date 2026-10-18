"""Local jobs with a durable ledger and one host-wide worker.

No automatic retries. The 900 second budget is checked between steps, not a
hard cancellation deadline for a running warehouse operation. A lost worker is
reported unknown; already successful steps are retained and never replayed.
"""
from contextlib import contextmanager
import csv
from datetime import date, datetime, timezone
from decimal import InvalidOperation
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
import uuid

import request_auth
import warehouse_resident as wr


class JobConflict(RuntimeError):
    pass


class JobNotFound(LookupError):
    pass


class StepUnknown(RuntimeError):
    pass


ACTIVE = ('queued', 'running')
PARAMETER = re.compile(r'[A-Za-z_][A-Za-z_0-9]*')
SLOT = re.compile(r'\{\{\s*([A-Za-z_][A-Za-z_0-9]*)\s*\}\}')
QUOTED = re.compile(r"--[^\n]*|/\*[\s\S]*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"")
SCHEMAS = {'raw', 'sandbox', 'staging', 'marts', 'tpch_tiny', 'smoke'}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _root():
    path = Path(os.environ.get('SHIROKUMA_STATE_DIR') or
                Path(__file__).resolve().parents[2] / 'var/shirokuma')
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def _db():
    path = _root() / 'jobs.sqlite3'
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(descriptor)
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript('''
            CREATE TABLE IF NOT EXISTS jobs (
                name TEXT PRIMARY KEY, owner TEXT NOT NULL, definition TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS stages (
                name TEXT PRIMARY KEY, owner TEXT NOT NULL, definition TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, job_name TEXT NOT NULL, actor TEXT NOT NULL,
                role TEXT NOT NULL, definition TEXT NOT NULL, arguments TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT,
                finished_at TEXT, error TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_job ON runs((1))
                WHERE status IN ('queued', 'running');
            CREATE TABLE IF NOT EXISTS steps (
                run_id TEXT NOT NULL, position INTEGER NOT NULL, name TEXT NOT NULL,
                action TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT,
                finished_at TEXT, error TEXT, summary TEXT,
                PRIMARY KEY(run_id, position));
            CREATE TABLE IF NOT EXISTS schedules (
                name TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
                every_minutes INTEGER, time TEXT, timezone TEXT, arguments TEXT NOT NULL,
                enabled INTEGER NOT NULL, role TEXT NOT NULL, auth_mode TEXT NOT NULL,
                next_run_at TEXT, last_status TEXT, last_run_id TEXT, last_scheduled_for TEXT);
            CREATE TABLE IF NOT EXISTS alerts (
                id TEXT PRIMARY KEY, job_name TEXT NOT NULL, step_position INTEGER NOT NULL,
                step_name TEXT NOT NULL, owner TEXT NOT NULL, severity TEXT NOT NULL,
                message TEXT NOT NULL, first_run_id TEXT NOT NULL, last_run_id TEXT NOT NULL,
                first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, occurrences INTEGER NOT NULL,
                acknowledged_at TEXT, acknowledged_by TEXT, resolved_at TEXT, resolution TEXT,
                rule_key TEXT NOT NULL, arguments_key TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS one_open_alert ON alerts
                (job_name,step_position,owner,rule_key,arguments_key) WHERE resolved_at IS NULL;
        ''')
        connection.execute('BEGIN IMMEDIATE')
        columns = {row['name'] for row in connection.execute('PRAGMA table_info(runs)')}
        if 'trigger' not in columns:
            connection.execute("ALTER TABLE runs ADD COLUMN trigger TEXT NOT NULL DEFAULT 'manual'")
        if 'scheduled_for' not in columns:
            connection.execute('ALTER TABLE runs ADD COLUMN scheduled_for TEXT')
        connection.commit()
        yield connection
        connection.commit()
    finally:
        connection.close()


def _identity(actor, role):
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError('actor is required')
    if role not in {'admin', 'data_engineer'}:
        raise PermissionError('Jobs require admin or data_engineer')


def _check_account(actor, role, require_shared=False):
    shared = request_auth.shared_mode()
    if require_shared and not shared:
        raise PermissionError('Shared authentication changed while the job was active')
    if not shared:
        return
    try:
        account = json.loads(Path(os.environ['SHIROKUMA_AUTH_FILE']).read_text())[actor]
        current = account['role']
        if request_auth.ROLES[current] < request_auth.ROLES[role]:
            raise ValueError()
    except (OSError, KeyError, TypeError, ValueError):
        raise PermissionError('Job identity was removed, downgraded, or is unavailable') from None


def _typed(value, kind, nullable=False):
    if value is None and nullable:
        return None
    if kind == 'string' and isinstance(value, str):
        return value
    if kind == 'integer' and type(value) is int and -(2**63) <= value < 2**63:
        return value
    if kind == 'boolean' and type(value) is bool:
        return value
    if kind == 'date' and isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        date.fromisoformat(value)
        return value
    raise ValueError('parameter does not match type ' + str(kind))


def _render(sql, parameters, arguments):
    for quoted in QUOTED.finditer(sql):
        if '{{' in quoted[0] or '}}' in quoted[0]:
            raise ValueError('SQL parameters cannot occur inside strings, identifiers, or comments')
    unquoted = QUOTED.sub('', sql)
    if "'" in unquoted or '"' in unquoted or '/*' in unquoted or '*/' in unquoted:
        raise ValueError('SQL contains an unterminated quote or comment')
    used = set()
    def replace(match):
        name = match[1]
        if name not in parameters:
            raise ValueError('undeclared SQL parameter: ' + name)
        before, after = sql[:match.start()], sql[match.end():]
        if (before and re.search(r'[\w$."\']$', before) or
                after and re.match(r'[\w$."\']', after)):
            raise ValueError('SQL parameters must be separate scalar values')
        if re.search(r'\b(?:FROM|JOIN|TABLE|SCHEMA|CATALOG|USE|AS|INTO|UPDATE|CALL|EXECUTE|EXISTS)\s*$',
                     QUOTED.sub('', before), re.IGNORECASE):
            raise ValueError('SQL parameters cannot supply identifiers or SQL types')
        used.add(name)
        value = arguments[name]
        kind = parameters[name]['type']
        if value is None:
            return 'NULL'
        if kind == 'integer':
            return str(value)
        if kind == 'boolean':
            return 'TRUE' if value else 'FALSE'
        return ('DATE ' if kind == 'date' else '') + wr.literal(value)
    rendered = SLOT.sub(replace, sql)
    if '{{' in rendered or '}}' in rendered:
        # Inspect the original leftovers, since an argument can itself contain braces.
        if '{{' in SLOT.sub('', sql) or '}}' in SLOT.sub('', sql):
            raise ValueError('invalid SQL parameter placeholder')
    return rendered, used


def _validate(definition, role, arguments=None, preflight=False):
    if not isinstance(definition, dict) or set(definition) - {'name', 'description', 'parameters', 'steps'}:
        raise ValueError('invalid job definition')
    if not isinstance(definition.get('name'), str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', definition['name']):
        raise ValueError('job name must be a lowercase slug')
    if not isinstance(definition.get('description', ''), str):
        raise ValueError('description must be text')
    parameters = definition.get('parameters', {})
    if not isinstance(parameters, dict):
        raise ValueError('parameters must be an object')
    values = {}
    for name, spec in parameters.items():
        if (not isinstance(name, str) or not PARAMETER.fullmatch(name) or not isinstance(spec, dict)
                or set(spec) - {'type', 'required', 'default'} or
                not isinstance(spec.get('type'), str) or
                spec['type'] not in {'string', 'integer', 'boolean', 'date'} or
                type(spec.get('required', False)) is not bool):
            raise ValueError('invalid parameter declaration')
        if 'default' in spec:
            _typed(spec['default'], spec['type'], not spec.get('required', False))
        sample = {'string': '', 'integer': 0, 'boolean': False, 'date': '2000-01-01'}[spec['type']]
        values[name] = spec.get('default', sample)
    if arguments is not None:
        if not isinstance(arguments, dict) or arguments.keys() - parameters.keys():
            raise ValueError('unexpected job arguments')
        for name, spec in parameters.items():
            if name not in arguments and 'default' not in spec and spec.get('required', False):
                raise ValueError('missing required parameter: ' + name)
            values[name] = _typed(arguments.get(name, spec.get('default')), spec['type'],
                                  not spec.get('required', False))
    steps = definition.get('steps')
    if not isinstance(steps, list) or not 1 <= len(steps) <= 50:
        raise ValueError('job requires between 1 and 50 steps')
    used, prepared = set(), []
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get('name', ''), str):
            raise ValueError('invalid step')
        action = step.get('action')
        fields = {'sql': {'sql', 'mode', 'schema'}, 'ingest': {'file', 'table', 'mode', 'format', 'key'},
                  'dbt_build': set(), 'dbt_test': set(), 'wap_refresh': {'table'}, 'wap_publish': {'table'},
                  'alert': {'sql', 'schema', 'message', 'severity'}}
        if not isinstance(action, str) or action not in fields or step.keys() - fields[action] - {'name', 'action'}:
            raise ValueError('unsupported step action or field')
        if action not in {'sql', 'alert'} and role != 'admin':
            raise PermissionError('Only admin can run dbt, ingestion, and WAP steps')
        normalized = dict(step)
        if action in {'sql', 'alert'}:
            if not isinstance(step.get('sql'), str) or not step['sql'].strip():
                raise ValueError('SQL step requires sql')
            mode = 'read-only' if action == 'alert' else step.get('mode')
            if (not isinstance(mode, str) or mode not in {'read-only', 'sandbox-write', 'admin-write'} or
                    not isinstance(step.get('schema', 'marts'), str) or step.get('schema', 'marts') not in SCHEMAS):
                raise ValueError('invalid SQL mode or schema')
            sql, names = _render(step['sql'], parameters, values)
            used.update(names)
            normalized['sql'] = wr.check_sql_policy(sql, mode, role=role)
            if action == 'alert':
                import alerts_resident
                if (not isinstance(step.get('message'), str) or not step['message'].strip()
                        or len(step['message']) > 500 or not isinstance(step.get('severity', 'warning'), str)
                        or step.get('severity', 'warning') not in {'warning', 'critical'}):
                    raise ValueError('alert requires a message of 1..500 characters and warning or critical severity')
                normalized['_rule_key'] = alerts_resident.rule_key(step, parameters)
        elif action == 'ingest':
            import lake_ingest
            path = step.get('file')
            if not isinstance(path, str) or not Path(path).is_absolute():
                raise ValueError('ingest file must be an absolute local path')
            if (not isinstance(step.get('table'), str) or not isinstance(step.get('mode'), str)
                    or step['mode'] not in {'replace', 'append', 'upsert'}):
                raise ValueError('ingest requires table and mode')
            lake_ingest.target_table(step['table'], Path(path))
            if not isinstance(step.get('format', 'auto'), str) or step.get('format', 'auto') not in {'auto', 'csv', 'jsonl', 'parquet'}:
                raise ValueError('invalid ingest format')
            if 'key' in step and not isinstance(step['key'], str):
                raise ValueError('ingest key must be text')
            if step['mode'] == 'upsert' and not step.get('key'):
                raise ValueError('upsert requires key')
            if preflight:
                try:
                    _, schema, _ = lake_ingest.read_file(Path(path), step.get('format', 'auto'))
                except csv.Error as exc:
                    raise ValueError('ingest CSV could not be parsed') from exc
                except InvalidOperation as exc:
                    raise ValueError('ingest JSON number could not be parsed') from exc
                if step['mode'] == 'upsert':
                    names = [name for name, _ in schema]
                    keys = [lake_ingest.sanitize(k) for k in step['key'].split(',') if k.strip()]
                    if not keys or any(key not in names for key in keys):
                        raise ValueError('upsert key must match input columns')
        elif action in {'wap_refresh', 'wap_publish'}:
            if not isinstance(step.get('table'), str) or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', step['table']):
                raise ValueError('WAP table must be a model name')
        prepared.append(normalized)
    if used != set(parameters):
        raise ValueError('all declared parameters must be used by SQL steps')
    return values, prepared


def _lock():
    lock = (_root() / 'jobs-active.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    return lock


def _lost(connection):
    now = _now()
    connection.execute("UPDATE steps SET status='unknown',finished_at=?,error='Worker lost; outcome unknown' "
                       "WHERE status='running' AND run_id IN (SELECT id FROM runs WHERE status IN ('queued','running'))", (now,))
    connection.execute("UPDATE steps SET status='skipped',finished_at=? WHERE status='pending' "
                       "AND run_id IN (SELECT id FROM runs WHERE status IN ('queued','running'))", (now,))
    connection.execute("UPDATE runs SET status='unknown',finished_at=?,error='Worker lost; outcome unknown; no automatic retry' "
                       "WHERE status IN ('queued','running')", (now,))


def _recover():
    lock = _lock()
    if lock is not None:
        try:
            with _db() as connection:
                _lost(connection)
        finally:
            lock.close()


def _job(row, actor):
    return dict(name=row['name'], owner=row['owner'], definition=json.loads(row['definition']),
                created_at=row['created_at'], updated_at=row['updated_at'], can_manage=row['owner'] == actor)


def list_jobs(actor, role):
    _identity(actor, role)
    with _db() as connection:
        rows = connection.execute('SELECT * FROM jobs WHERE owner=? OR ?=\'admin\' ORDER BY name', (actor, role))
        return [_job(row, actor) for row in rows]


def save_job(definition, actor, role):
    _identity(actor, role)
    _check_account(actor, role)
    _validate(definition, role)
    definition = json.loads(json.dumps(definition))
    name, now = definition['name'], _now()
    with _db() as connection:
        connection.execute('BEGIN IMMEDIATE')
        old = connection.execute('SELECT * FROM jobs WHERE name=?', (name,)).fetchone()
        if old and old['owner'] != actor:
            raise PermissionError('Only the job owner can update it')
        connection.execute('INSERT INTO jobs VALUES (?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET '
                           'definition=excluded.definition,updated_at=excluded.updated_at',
                           (name, actor, json.dumps(definition), now, now))
        import alerts_resident
        alerts_resident.retire_changed(connection, definition, actor)
        return _job(connection.execute('SELECT * FROM jobs WHERE name=?', (name,)).fetchone(), actor)


def submit_run(name, arguments, actor, role):
    _identity(actor, role)
    _check_account(actor, role)
    if not isinstance(arguments, dict):
        raise ValueError('job arguments must be an object')
    lock = _lock()
    if lock is None:
        raise JobConflict('Another job is active; no waiting queue')
    try:
        with _db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            _lost(connection)
            job = connection.execute('SELECT * FROM jobs WHERE name=?', (name,)).fetchone()
            if not job or job['owner'] != actor:
                raise JobNotFound('Job not found or not owned by this identity')
            definition = json.loads(job['definition'])
            values, prepared = _validate(definition, role, arguments, preflight=True)
            run_id = _insert_run(connection, definition, values, prepared, actor, role)
        result = get_run(run_id, actor, role)
        _start_worker(run_id, prepared, actor, role, lock, request_auth.shared_mode(), name)
        return result
    except Exception:
        lock.close()
        raise


def _insert_run(connection, definition, arguments, steps, actor, role, *,
                trigger='manual', scheduled_for=None, status='queued', error=None):
    run_id, now = uuid.uuid4().hex, _now()
    finished = now if status not in ACTIVE else None
    connection.execute('INSERT INTO runs(id,job_name,actor,role,definition,arguments,status,created_at,'
                       'finished_at,error,trigger,scheduled_for) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       (run_id, definition['name'], actor, role, json.dumps(definition), json.dumps(arguments),
                        status, now, finished, error, trigger, scheduled_for))
    connection.executemany('INSERT INTO steps(run_id,position,name,action,status,finished_at) VALUES (?,?,?,?,?,?)',
        [(run_id, i, step.get('name') or 'Step ' + str(i), step['action'],
          'pending' if status in ACTIVE else 'skipped', finished) for i, step in enumerate(steps, 1)])
    return run_id


def _start_worker(run_id, steps, actor, role, lock, shared, name, auth_mode=None):
    thread = threading.Thread(target=_worker, args=(run_id, steps, actor, role, lock, shared, name, auth_mode),
                              name='job-' + run_id[:8], daemon=True)
    thread.start()


def save_schedule(payload, actor, role):
    import schedule_resident
    return schedule_resident.save_schedule(payload, actor, role)


def list_schedules(actor, role):
    import schedule_resident
    return schedule_resident.list_schedules(actor, role)


def tick(now=None):
    import schedule_resident
    return schedule_resident.tick(now)


def start_scheduler(interval=10):
    import schedule_resident
    return schedule_resident.start_scheduler(interval)


def stop_scheduler():
    import schedule_resident
    return schedule_resident.stop_scheduler()


def list_alerts(actor, role):
    import alerts_resident
    return alerts_resident.list_alerts(actor, role)


def acknowledge(alert_id, actor, role):
    import alerts_resident
    return alerts_resident.acknowledge(alert_id, actor, role)


def _run(row, connection):
    result = dict(row)
    result['definition'] = json.loads(result['definition'])
    result['arguments'] = json.loads(result['arguments'])
    result['steps'] = [dict(step) for step in connection.execute(
        'SELECT position,name,action,status,started_at,finished_at,error,summary FROM steps WHERE run_id=? ORDER BY position',
        (result['id'],))]
    for step in result['steps']:
        step['summary'] = json.loads(step['summary']) if step['summary'] else None
    return result


def list_runs(actor, role):
    _identity(actor, role)
    _recover()
    with _db() as connection:
        rows = connection.execute("SELECT * FROM runs WHERE actor=? OR ?='admin' ORDER BY created_at DESC LIMIT 100", (actor, role)).fetchall()
        return [_run(row, connection) for row in rows]


def get_run(run_id, actor, role):
    _identity(actor, role)
    _recover()
    with _db() as connection:
        row = connection.execute("SELECT * FROM runs WHERE id=? AND (actor=? OR ?='admin')", (run_id, actor, role)).fetchone()
        if row is None:
            raise JobNotFound('Run not found')
        return _run(row, connection)


def _execute_step(step):
    action = step['action']
    if action == 'sql':
        return wr.query('LOCAL', step['sql'], catalog='iceberg', schema=step.get('schema', 'marts'),
                        mode=step['mode'], max_rows=None)
    if action == 'alert':
        import alerts_resident
        return alerts_resident.evaluate(step)
    if action in {'dbt_build', 'dbt_test'}:
        import lake_pipeline
        report = lake_pipeline.run('build' if action == 'dbt_build' else 'test')
        if report.get('result') != 'success':
            if 'timed out' in str(report.get('error', '')).lower():
                raise StepUnknown('dbt timed out; outcome unknown')
            raise ValueError('dbt reported failure')
        return report
    if action == 'stage_import':
        import stages_resident
        return stages_resident._execute_import(step)
    if action == 'ingest':
        import lake_ingest
        return lake_ingest.ingest(step['file'], table=step['table'], mode=step['mode'],
                                  format=step.get('format', 'auto'), key=step.get('key'), atomic_append=True)
    import bears_resident
    from types import SimpleNamespace
    report = bears_resident.data(SimpleNamespace(**{
        'refresh' if action == 'wap_refresh' else 'publish': step['table']}))
    if report.get('valid') is not True or report.get('error'):
        raise ValueError('WAP reported failure')
    return report


def _worker(run_id, steps, actor, role, lock, shared=False, job_name='', auth_mode=None):
    deadline = time.monotonic() + 900
    final_status = None
    try:
        with request_auth.effective_role(role):
            with _db() as connection:
                arguments = json.loads(connection.execute('SELECT arguments FROM runs WHERE id=?', (run_id,)).fetchone()[0])
                connection.execute("UPDATE runs SET status='running',started_at=? WHERE id=?", (_now(), run_id))
            for position, step in enumerate(steps, 1):
                try:
                    if auth_mode is not None and auth_mode != ('shared' if request_auth.shared_mode() else 'local'):
                        raise PermissionError('Authentication mode changed while the scheduled job was active')
                    _check_account(actor, role, require_shared=shared)
                    if time.monotonic() >= deadline:
                        raise ValueError('Job budget exceeded between steps; no further steps started')
                    with _db() as connection:
                        connection.execute("UPDATE steps SET status='running',started_at=? WHERE run_id=? AND position=?", (_now(), run_id, position))
                    report = _execute_step(step)
                    summary = {key: report[key] for key in ('action', 'table', 'rows_ingested', 'row_count', 'result', 'valid', 'duration_ms') if key in report}
                    if step['action'] in {'dbt_build', 'dbt_test'}:
                        summary['model_count'] = len(report.get('models', []))
                    with _db() as connection:
                        connection.execute('BEGIN IMMEDIATE')
                        if step['action'] == 'alert':
                            import alerts_resident
                            summary.update(alerts_resident.record(connection, job_name, position,
                                step.get('name') or 'Step ' + str(position), actor, step, arguments,
                                run_id, report['condition_met']))
                        connection.execute("UPDATE steps SET status='success',finished_at=?,summary=? WHERE run_id=? AND position=?",
                                           (_now(), json.dumps(summary), run_id, position))
                except Exception as exc:
                    uncertain = isinstance(exc, (OSError, subprocess.TimeoutExpired, StepUnknown)) and not isinstance(exc, PermissionError)
                    status = 'unknown' if uncertain else 'failed'
                    # Provider/driver exception messages may contain credentials or rows.
                    if isinstance(exc, wr.TrinoQueryError):
                        error = 'Trino query failed (' + exc.error_name + ')'
                    else:
                        error = str(exc) if isinstance(exc, (ValueError, PermissionError, StepUnknown)) else 'Step operation failed (' + type(exc).__name__ + ')'
                    now = _now()
                    with _db() as connection:
                        connection.execute('UPDATE steps SET status=?,finished_at=?,error=? WHERE run_id=? AND position=?', (status, now, error, run_id, position))
                        connection.execute("UPDATE steps SET status='skipped',finished_at=? WHERE run_id=? AND status='pending'", (now, run_id))
                        connection.execute('UPDATE runs SET status=?,finished_at=?,error=? WHERE id=?', (status, now, error, run_id))
                    final_status = status
                    return
            with _db() as connection:
                connection.execute("UPDATE runs SET status='success',finished_at=? WHERE id=?", (_now(), run_id))
            final_status = 'success'
    finally:
        lock.close()
        if final_status is not None:
            try:
                wr.telemetry('run.' + final_status, int((time.monotonic() - deadline + 900) * 1000),
                             status='success' if final_status == 'success' else 'failed', prefix='jobs.',
                             task_id=run_id, trace_id=run_id, agent_id=actor,
                             target='job:' + job_name + ';role:' + role)
            except Exception:
                pass  # The committed SQLite ledger remains authoritative.
