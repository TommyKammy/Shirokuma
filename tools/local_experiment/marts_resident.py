"""Admin-owned refresh definitions in the WAP registry, executed by local jobs."""
import re
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
                last_run=last_run, last_success_at=last_success_at)


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
            success = connection.execute("SELECT finished_at FROM runs WHERE job_name=? AND status='success' "
                                         'ORDER BY created_at DESC LIMIT 1', ('mart:' + name,)).fetchone()
            results.append(_target(name, entry, actor, jobs._run(row, connection) if row else None,
                                   success['finished_at'] if success else None))
        return results


def save_target(payload, actor, role):
    _identity(actor, role)
    if not isinstance(payload, dict) or payload.keys() - {'name', 'sql', 'min_rows', 'not_null', 'version'}:
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
                             table='iceberg.marts.' + name, sources=wap.extract_source_tables(statement))
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


def _execute_refresh(step):
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
