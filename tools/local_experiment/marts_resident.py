"""Admin-owned refresh definitions in the WAP registry, executed by local jobs."""
import re
import json
from contextlib import nullcontext
from types import SimpleNamespace

import jobs_resident as jobs
import request_auth
import warehouse_resident as wr
import wap_resident as wap


def _runtime():
    import bears_resident
    return bears_resident


def _identity(actor, role):
    jobs._identity(actor, role)
    if role != 'admin':
        raise PermissionError('Marts require admin')
    jobs._check_account(actor, role)


def _name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', value):
        raise ValueError('Mart name must be a bare model identifier')
    return value.lower()


def _revision(entry, version):
    if type(version) is not int or version != entry['version']:
        raise jobs.JobConflict('Mart version changed; reload before saving or refreshing')


def _target(name, entry, actor, last_run=None, last_success_at=None):
    return dict(name=name, sql=entry['sql'], min_rows=entry['min_rows'],
                not_null=entry['not_null'], owner=entry['owner'], version=entry['version'],
                table='iceberg.marts.' + name, can_manage=entry['owner'] == actor,
                last_run=last_run, last_success_at=last_success_at,
                watch_tables=entry.get('watch_tables', []))


def list_targets(actor, role):
    _identity(actor, role)
    jobs._recover()
    b = _runtime()
    with jobs._db() as connection:
        results = []
        for name, entry in sorted(b.load_wap_registry().items()):
            if not entry.get('owner') or not entry.get('version'):
                continue
            row = connection.execute('SELECT * FROM runs WHERE job_name=? ORDER BY created_at DESC LIMIT 1',
                                     ('mart:' + name,)).fetchone()
            success = connection.execute("SELECT r.finished_at FROM runs r WHERE r.job_name=? AND r.status='success' "
                "AND EXISTS (SELECT 1 FROM steps s WHERE s.run_id=r.id AND s.status='success' "
                "AND COALESCE(json_extract(s.summary,'$.result'),'') != 'unchanged') "
                'ORDER BY r.created_at DESC LIMIT 1', ('mart:' + name,)).fetchone()
            target = _target(name, entry, actor, jobs._run(row, connection) if row else None,
                             success['finished_at'] if success else None)
            target['schedule'] = _get_schedule(name, actor, connection)
            target['requires_manual_refresh'] = _requires_manual(name, connection)
            results.append(target)
        return results


def save_target(payload, actor, role):
    _identity(actor, role)
    if not isinstance(payload, dict) or payload.keys() - {'name', 'sql', 'min_rows', 'not_null', 'version', 'watch_tables'}:
        raise ValueError('Invalid mart definition fields')
    name = _name(payload.get('name'))
    statement = payload.get('sql')
    if not isinstance(statement, str) or not re.match(r'(?i)^(SELECT|WITH)\b', statement.strip()):
        raise ValueError('SELECT model required')
    statement = wr.check_sql_policy(statement.strip(), mode='read-only', role=role)
    minimum, columns = payload.get('min_rows', 1), payload.get('not_null', [])
    if type(minimum) is not int or minimum < 1:
        raise ValueError('min_rows must be a positive integer')
    if (not isinstance(columns, list) or any(not isinstance(c, str) or
            not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', c) for c in columns)):
        raise ValueError('not_null must contain column names')
    columns = list(dict.fromkeys(columns))
    watched = _watch_tables(payload.get('watch_tables', []))
    lock = jobs._lock()
    if lock is None:
        raise jobs.JobConflict('Another job is active; wait before editing mart definitions')
    try:
        b = _runtime()
        with wap.model_operation(b):
            with b.WAP_REGISTRY.with_suffix('.lock').open('a') as registry_lock:
                import fcntl
                fcntl.flock(registry_lock, fcntl.LOCK_EX)
                registry = b.load_wap_registry()
                old = registry.get(name)
                # Case-insensitive Trino identifiers must never collide with legacy entries.
                if any(key.lower() == name and key != name for key in registry):
                    raise jobs.JobConflict('Model name collides with an existing WAP entry')
                if old:
                    if not old.get('owner'):
                        raise jobs.JobConflict('Model already exists in the legacy WAP registry')
                    if old['owner'] != actor:
                        raise PermissionError('Only the mart owner can update it')
                    _revision(old, payload.get('version'))
                elif 'version' in payload:
                    raise jobs.JobConflict('Mart no longer exists; reload before saving')
                entry = dict(old or {}, owner=actor, version=(old['version'] + 1 if old else 1),
                             sql=statement, min_rows=minimum, not_null=columns,
                             table='iceberg.marts.' + name, sources=wap.extract_source_tables(statement),
                             watch_tables=watched)
                entry.pop('watch_baseline', None)
                # Pause before registry replacement; failures leave automation disabled.
                with jobs._db() as connection:
                    connection.execute('UPDATE schedules SET enabled=0,next_run_at=NULL WHERE name=?', ('mart:' + name,))
                registry[name] = entry
                wap.persist_json(b.WAP_REGISTRY, registry)
        return next(t for t in list_targets(actor, role) if t['name'] == name)
    finally:
        lock.close()


def submit_refresh(name, version, actor, role):
    _identity(actor, role)
    name = _name(name)
    lock = jobs._lock()
    if lock is None:
        raise jobs.JobConflict('Another job is active; no waiting queue')
    try:
        b = _runtime()
        with wap.model_operation(b):
            entry = b.load_wap_registry().get(name)
            if not entry or not entry.get('owner'):
                raise jobs.JobNotFound('Registered mart not found')
            if entry['owner'] != actor:
                raise PermissionError('Only the mart owner can refresh it')
            _revision(entry, version)
            snapshot = {key: entry[key] for key in ('sql', 'min_rows', 'not_null', 'version', 'owner')}
            snapshot['watch_tables'] = entry.get('watch_tables', [])
            step = dict(name='Refresh ' + name, action='mart_refresh', model=name, **snapshot)
            definition = dict(name='mart:' + name, description='Registered mart refresh', parameters={}, steps=[step])
            with jobs._db() as connection:
                connection.execute('BEGIN IMMEDIATE')
                jobs._lost(connection)
                run_id = jobs._insert_run(connection, definition, {}, [step], actor, role)
        result = jobs.get_run(run_id, actor, role)
        jobs._start_worker(run_id, [step], actor, role, lock, request_auth.shared_mode(), definition['name'])
        return result
    except Exception:
        lock.close()
        raise


def _publish_refresh(step):
    b = _runtime()
    try:
        report = b.data(SimpleNamespace(refresh=step['model'], sql=step['sql'],
            min_rows=step['min_rows'], not_null=','.join(step['not_null']), _managed_version=step['version']))
    except (ValueError, PermissionError, jobs.StepUnknown):
        raise
    except Exception as exc:
        raise jobs.StepUnknown('Mart refresh interrupted; publication outcome unknown; no automatic retry') from exc
    if report.get('valid') is not True or report.get('error'):
        raise ValueError(report.get('error') or 'Mart refresh failed quality checks')
    return report


def _watch_tables(value):
    if not isinstance(value, list) or any(not isinstance(table, str) or not re.fullmatch(
            r'iceberg\.(?:raw|staging)\.[A-Za-z_][A-Za-z_0-9]*', table, re.I) for table in value):
        raise ValueError('watch_tables must contain iceberg.raw or iceberg.staging base table identifiers')
    return sorted(set(table.lower() for table in value))


def _get_schedule(name, actor, connection):
    import schedule_resident as scheduler
    row = connection.execute('SELECT * FROM schedules WHERE name=?', ('mart:' + name,)).fetchone()
    if row is None:
        return None
    result = scheduler._schedule(row, actor, connection)
    result['name'] = name
    return result


def _requires_manual(name, connection):
    # Skipped and unchanged checks cannot hide an unresolved publication outcome.
    for row in connection.execute('SELECT * FROM runs WHERE job_name=? ORDER BY created_at DESC,rowid DESC', ('mart:' + name,)):
        if row['status'] == 'unknown':
            return True
        if row['status'] == 'success' and row['trigger'] == 'manual':
            summaries = connection.execute('SELECT summary FROM steps WHERE run_id=?', (row['id'],)).fetchall()
            if any(item['summary'] and json.loads(item['summary']).get('result') != 'unchanged' for item in summaries):
                return False
    return False


def _owned(name, actor):
    entry = _runtime().load_wap_registry().get(name)
    if not entry or not entry.get('owner'):
        raise jobs.JobNotFound('Registered mart not found')
    if entry['owner'] != actor:
        raise PermissionError('Only the mart owner can manage it')
    return entry


def save_schedule(payload, actor, role):
    import schedule_resident as scheduler
    _identity(actor, role)
    if not isinstance(payload, dict) or payload.keys() - (scheduler.SCHEDULE_FIELDS | {'name', 'enabled', 'version'}):
        raise ValueError('invalid mart schedule fields')
    name = _name(payload.get('name'))
    pause = payload.keys() == {'name', 'enabled'} and payload['enabled'] is False
    lock = None if pause else jobs._lock()
    if not pause and lock is None:
        raise jobs.JobConflict('Another job is active; wait before changing mart schedules')
    try:
        entry = _owned(name, actor)
        now = scheduler._utc()
        enabled = payload.get('enabled', False)
        if not pause:
            schedule = scheduler._validate_schedule({'kind': 'interval', **payload}, now)
            scheduler._authorize(dict(owner=actor, role=role, auth_mode=scheduler._mode()))
            if enabled:
                _revision(entry, payload.get('version'))
                if not _watch_tables(entry.get('watch_tables', [])):
                    raise ValueError('Automatic refresh requires explicit watch_tables')
        with jobs._db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if lock is not None:
                jobs._lost(connection)
            old = connection.execute('SELECT * FROM schedules WHERE name=?', ('mart:' + name,)).fetchone()
            if old and old['owner'] != actor:
                raise PermissionError('Only the schedule owner can change it')
            if pause:
                if old is None:
                    raise jobs.JobNotFound('Schedule not found')
                connection.execute('UPDATE schedules SET enabled=0,next_run_at=NULL WHERE name=?', ('mart:' + name,))
            else:
                if enabled and _requires_manual(name, connection):
                    raise ValueError('Unknown mart outcome requires a successful manual refresh before enabling')
                scheduler._persist(connection, 'mart:' + name, actor, role, schedule,
                                   {'version': entry['version']}, enabled, now)
            return _get_schedule(name, actor, connection)
    finally:
        if lock is not None:
            lock.close()


def _scheduled(name, actor, role, preflight=False, connection=None):
    _identity(actor, role)
    name = _name(name)
    entry = _owned(name, actor)
    with (nullcontext(connection) if connection is not None else jobs._db()) as connection:
        row = connection.execute('SELECT * FROM schedules WHERE name=?', ('mart:' + name,)).fetchone()
        if row is None or row['owner'] != actor:
            raise PermissionError('Mart schedule ownership unavailable')
        _revision(entry, json.loads(row['arguments']).get('version'))
        if _requires_manual(name, connection):
            raise ValueError('Unknown mart outcome requires a successful manual refresh')
    watched = _watch_tables(entry.get('watch_tables', []))
    if not watched:
        raise ValueError('Automatic refresh requires explicit watch_tables')
    step = dict(name='Check and refresh ' + name, action='mart_refresh', model=name,
                **{key: entry[key] for key in ('sql', 'min_rows', 'not_null', 'version', 'owner')},
                watch_tables=watched, check_changes=True)
    return dict(name='mart:' + name, description='Registered mart change check', parameters={}, steps=[step]), [step]


def _source_snapshots(watched):
    b = _runtime()
    try:
        with b.wr.trino() as url:
            # Iceberg metadata relations fail for missing tables and views.
            return {table: int(wap.current_snapshot(b, url, table)) for table in watched}
    except Exception as exc:
        raise ValueError('Source snapshot metadata unavailable; change check failed') from exc


def _execute_refresh(step):
    _identity(step['owner'], 'admin')
    _revision(_owned(step['model'], step['owner']), step['version'])
    with wr.warehouse_activity():
        if wr.status('LOCAL')['replicas'] == 0:
            wr.transition('wake', 'LOCAL')
        return _execute_active_refresh(step)


def _execute_active_refresh(step):
    b = _runtime()
    entry = _owned(step['model'], step['owner'])
    _revision(entry, step['version'])
    watched = _watch_tables(step.get('watch_tables', []))
    snapshots = _source_snapshots(watched) if watched else None
    baseline = entry.get('watch_baseline')
    if step.get('check_changes'):
        with jobs._db() as connection:
            if _requires_manual(step['model'], connection):
                raise ValueError('Unknown mart outcome requires a successful manual refresh')
        if baseline == dict(version=step['version'], snapshots=snapshots):
            return dict(result='unchanged', changed=False, table=entry['table'])
    report = _publish_refresh(step)
    if snapshots is not None:
        # Publication, registry and ledger are separate; late failures become unknown.
        with wap.model_operation(b):
            with b.WAP_REGISTRY.with_suffix('.lock').open('a') as registry_lock:
                import fcntl
                fcntl.flock(registry_lock, fcntl.LOCK_EX)
                registry = b.load_wap_registry()
                current = registry.get(step['model'])
                if not current or current.get('version') != step['version']:
                    raise jobs.StepUnknown('Published mart definition changed; manual refresh required')
                current['watch_baseline'] = dict(version=step['version'], snapshots=snapshots)
                try:
                    wap.persist_json(b.WAP_REGISTRY, registry)
                except Exception as exc:
                    raise jobs.StepUnknown('Published mart baseline could not be saved; manual refresh required') from exc
    return dict(report, result='refreshed', changed=True)
