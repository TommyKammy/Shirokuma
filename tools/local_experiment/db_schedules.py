"""DB schedules over the existing submit APIs; reservations are never replayed."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import logging
import uuid

import control_store
import db_jobs as jobs
import request_auth
from query_context import QueryError, ResolvedQueryContext, resolve_context, revalidate_admission
import schedule_resident as calendar
import warehouse_admission as admission

_scope = ContextVar('db_schedule_admission', default=None)
_TABLES = {'job': 'db_jobs', 'stage': 'db_stages', 'mart': 'db_marts'}
_IDENTITY = ('database_id', 'schema_id', 'warehouse_id', 'selected_role', 'sql_catalog', 'sql_schema')


def _visible(row, principal, context):
    if row is None or row['owner'] != principal.principal_id or row['database_id'] != context.database_id:
        raise QueryError('SCHEDULE_NOT_FOUND', 404)
    saved = json.loads(row['context'])
    if any(saved[k] != asdict(context)[k] for k in _IDENTITY):
        raise QueryError('CONTEXT_MISMATCH', 409)


def _target(db, principal, context, kind, ident, revision=None):
    if not isinstance(kind, str) or kind not in _TABLES or not isinstance(ident, str) or not ident:
        raise ValueError('Job, Stage or Mart target required')
    row = db.execute('SELECT * FROM ' + _TABLES[kind] + ' WHERE id=?', (ident,)).fetchone()
    _visible(row, principal, context)
    if revision is not None and row['revision'] != revision:
        raise QueryError('TARGET_REVISION_CHANGED', 409)
    return row


def _public(row):
    result = dict(row)
    for k in ('context', 'spec', 'intent'):
        result[k] = json.loads(result[k])
    result['enabled'] = bool(result['enabled'])
    return result


def _schedule(db, principal, context, ident):
    row = db.execute('SELECT * FROM db_schedules WHERE id=?', (ident,)).fetchone()
    _visible(row, principal, context)
    _target(db, principal, context, row['target_kind'], row['target_id'])
    return row


def _request(db, principal, route, key, payload):
    row = db.execute('SELECT * FROM db_schedule_requests WHERE actor=? AND route=? AND idempotency_key=?',
                     (principal.principal_id, route, key)).fetchone()
    if row and row['payload'] != payload:
        raise QueryError('IDEMPOTENCY_CONFLICT', 409)
    return json.loads(row['response']) if row else None


def _receipt(db, principal, route, key, payload, result):
    db.execute('INSERT INTO db_schedule_requests VALUES (?,?,?,?,?)',
               (principal.principal_id, route, key, payload, jobs._json(result)))


def _intent(principal, context, kind, row, intent):
    definition = json.loads(row['definition'])
    if kind == 'job':
        if not isinstance(intent, dict) or set(intent) != {'arguments'} or not isinstance(intent['arguments'], dict):
            raise ValueError('Job arguments required')
        if any(s['action'] == 'ingest' for s in definition['steps']):
            raise ValueError('scheduled Jobs cannot require runtime uploads')
        jobs._validate(principal, context, definition, intent['arguments'])
    elif kind == 'stage':
        if intent != {'action': 'scan'}:
            raise ValueError('Stage scan intent required')
        import db_stage_scans as scans
        import db_stages as stages
        stages._access(principal, context)
        stages._definition(context, definition)
        scans._mode(definition)
    else:
        if intent != {'action': 'refresh', 'check_changes': True} or type(intent.get('check_changes')) is not bool:
            raise ValueError('Mart refresh with check_changes required')
        import db_marts as marts
        marts._access(principal, context)
        if not definition['source_refs']:
            raise QueryError('WATCH_REQUIRED', 409)
        marts._definition(principal, context, definition, marts._public(row))


def save_schedule(principal, context, *, key, target_kind, target_id, target_revision,
                  expected_revision, spec, intent, enabled=False, schedule_id=None):
    jobs._access(principal, context)
    jobs._key(key)
    if (type(expected_revision) is not int or expected_revision < 0
            or (schedule_id is not None) != (expected_revision > 0)
            or schedule_id is not None and (not isinstance(schedule_id, str)
                or not schedule_id.strip() or len(schedule_id) > 128)
            or type(target_revision) is not int or target_revision < 1 or type(enabled) is not bool):
        raise ValueError('explicit revisions and Boolean enabled required')
    if not isinstance(spec, dict) or spec.keys() - calendar.SCHEDULE_FIELDS:
        raise ValueError('invalid schedule spec')
    now = calendar._utc()
    # Calendar validity is stable; an accepted once receipt remains observable
    # after its occurrence has passed. Future-time validation is for new saves.
    normalized = calendar._validate_schedule(dict(spec, enabled=False), now)
    spec = {k: v for k, v in normalized.items() if v is not None and v != []}
    intent = json.loads(jobs._json(intent))
    payload = jobs._json(dict(context=asdict(context), schedule_id=schedule_id,
        expected_revision=expected_revision, target_kind=target_kind, target_id=target_id,
        target_revision=target_revision, spec=spec, intent=intent, enabled=enabled))
    with control_store.connect() as db:
        row = _target(db, principal, context, target_kind, target_id)
        replay = _request(db, principal, 'save', key, payload)
        if replay is not None:
            _schedule(db, principal, context, replay['id'])
            return replay
        if row['revision'] != target_revision:
            raise QueryError('TARGET_REVISION_CHANGED', 409)
    calendar._validate_schedule(dict(spec, enabled=enabled), now)
    _intent(principal, context, target_kind, row, intent)
    occurrence = calendar._next(spec, now) if enabled else None
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        revalidate_admission(db, principal, context)
        _target(db, principal, context, target_kind, target_id)
        replay = _request(db, principal, 'save', key, payload)
        if replay is not None:
            _schedule(db, principal, context, replay['id'])
            return replay
        _target(db, principal, context, target_kind, target_id, target_revision)
        if schedule_id:
            old = _schedule(db, principal, context, schedule_id)
            if old['revision'] != expected_revision:
                raise QueryError('REVISION_CONFLICT', 409)
            if (old['target_kind'], old['target_id']) != (target_kind, target_id):
                raise ValueError('schedule target is immutable')
        else:
            schedule_id = str(uuid.uuid4())
        db.execute('''INSERT INTO db_schedules VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL)
            ON CONFLICT(id) DO UPDATE SET context=excluded.context,revision=excluded.revision,
            auth_mode=excluded.auth_mode,target_revision=excluded.target_revision,spec=excluded.spec,
            intent=excluded.intent,enabled=excluded.enabled,next_run_at=excluded.next_run_at,
            last_status=NULL,paused_reason=NULL''',
            (schedule_id, context.database_id, principal.principal_id, context.selected_role, calendar._mode(),
             jobs._json(asdict(context)), expected_revision + 1, target_kind, target_id, target_revision,
             jobs._json(spec), jobs._json(intent), int(enabled), occurrence.isoformat() if occurrence else None))
        result = _public(_schedule(db, principal, context, schedule_id))
        _receipt(db, principal, 'save', key, payload, result)
        return result


def get_schedule(principal, context, schedule_id):
    jobs._access(principal, context)
    with control_store.connect() as db:
        db.execute('BEGIN')
        revalidate_admission(db, principal, context)
        return _public(_schedule(db, principal, context, schedule_id))


def list_schedules(principal, context, target_kind=None, target_id=None):
    jobs._access(principal, context)
    with control_store.connect() as db:
        result = []
        for row in db.execute('SELECT * FROM db_schedules WHERE owner=? AND database_id=? ORDER BY id',
                              (principal.principal_id, context.database_id)):
            if target_kind is not None and row['target_kind'] != target_kind or target_id is not None and row['target_id'] != target_id:
                continue
            try:
                _schedule(db, principal, context, row['id'])
            except QueryError:
                continue
            result.append(_public(row))
        revalidate_admission(db, principal, context)
        return result


def list_ticks(principal, context, schedule_id):
    jobs._access(principal, context)
    with control_store.connect() as db:
        db.execute('BEGIN')
        revalidate_admission(db, principal, context)
        _schedule(db, principal, context, schedule_id)
        return [dict(r) for r in db.execute('''SELECT id,schedule_id,schedule_revision,
            target_revision,scheduled_for,status,code,run_id FROM db_schedule_ticks
            WHERE schedule_id=? ORDER BY scheduled_for,schedule_revision,id''', (schedule_id,))]


@contextmanager
def _reservation(firing, now, refresh_clock, stop):
    token = _scope.set((firing, now, refresh_clock, stop))
    try:
        yield
    finally:
        _scope.reset(token)


def _admit(db, kind, target_id, target_revision, run_id, context):
    """Called only within the submitter's admission transaction, before INSERT."""
    scope = _scope.get()
    if scope is None:
        return
    firing, now, refresh_clock, stop = scope
    tick = db.execute('SELECT * FROM db_schedule_ticks WHERE id=?', (firing,)).fetchone()
    schedule = db.execute('SELECT * FROM db_schedules WHERE id=?', (tick['schedule_id'],)).fetchone() if tick else None
    target = db.execute('SELECT revision FROM ' + _TABLES[kind] + ' WHERE id=?', (target_id,)).fetchone()
    if (tick is None or tick['status'] != 'reserved' or schedule is None or not schedule['enabled']
            or schedule['revision'] != tick['schedule_revision'] or schedule['target_kind'] != kind
            or schedule['target_id'] != target_id or schedule['target_revision'] != target_revision
            or tick['target_revision'] != target_revision or target is None or target['revision'] != target_revision):
        raise QueryError('SCHEDULE_ADMISSION_STALE', 409)
    if stop is not None and stop.is_set():
        raise QueryError('STOPPED', 409)
    current = calendar._utc() if refresh_clock else now
    lateness = (current - datetime.fromisoformat(tick['scheduled_for'])).total_seconds()
    if lateness < 0:
        raise QueryError('CLOCK_MOVED', 409)
    if lateness >= 60:
        raise QueryError('MISSED', 409)
    db.execute("UPDATE db_schedule_ticks SET status='accepted',code=NULL,run_id=?,context=? WHERE id=?",
               (run_id, jobs._json(asdict(context)), firing))


def _identity(schedule):
    if schedule['auth_mode'] != calendar._mode():
        raise QueryError('AUTH_MODE_CHANGED', 403)
    account = request_auth._principal_accounts().get(schedule['owner'])
    if not account:
        raise QueryError('DB_ACCESS_REVOKED', 403)
    principal = request_auth.AuthenticatedPrincipal(schedule['owner'], account['role'], account['platform_admin'])
    saved = ResolvedQueryContext(**json.loads(schedule['context']))
    context = resolve_context(principal, saved.database_id, saved.schema_id, saved.selected_role,
        warehouse_id=saved.warehouse_id, expected_binding_revision=saved.binding_revision)
    if context.grant_revision != saved.grant_revision:
        raise QueryError('DB_ACCESS_REVOKED', 403)
    if any(asdict(saved)[k] != asdict(context)[k] for k in asdict(saved) if k != 'generation_id'):
        raise QueryError('CONTEXT_MISMATCH', 409)
    jobs._access(principal, context)
    return principal, context


def _finish(firing, status, code=None, pause=False):
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM db_schedule_ticks WHERE id=?', (firing,)).fetchone()
        db.execute('UPDATE db_schedule_ticks SET status=?,code=? WHERE id=?', (status, code, firing))
        if row is None:
            return
        db.execute('''UPDATE db_schedules SET last_status=?,
            paused_reason=CASE WHEN enabled=0 AND paused_reason IS NOT NULL THEN paused_reason
                WHEN ? THEN ? ELSE NULL END,
            enabled=CASE WHEN ? THEN 0 WHEN json_extract(spec,'$.kind')='once' THEN 0 ELSE enabled END,
            next_run_at=CASE WHEN ? THEN NULL ELSE next_run_at END
            WHERE id=? AND revision=?''',
            (status, int(pause), code, int(pause), int(pause), row['schedule_id'], row['schedule_revision']))


def _terminal_status(tick, schedule):
    """Internal status only; denied results never pass through this observer."""
    kind = schedule['target_kind']
    with control_store.connect() as db:
        if kind == 'job':
            row = db.execute('''SELECT owner,job_id AS target,revision,context,status
                FROM db_job_runs WHERE id=?''', (tick['run_id'],)).fetchone()
        else:
            table, field = ('db_stage_scans', 'stage') if kind == 'stage' else ('db_mart_runs', 'mart')
            row = db.execute(f'''SELECT json_extract(record,'$.owner') AS owner,
                {field}_id AS target,json_extract(record,'$.{field}_revision') AS revision,
                json_extract(record,'$.context') AS context,json_extract(record,'$.status') AS status
                FROM {table} WHERE id=?''', (tick['run_id'],)).fetchone()
    if (row is None or row['owner'] != schedule['owner'] or row['target'] != schedule['target_id']
            or row['revision'] != tick['target_revision']
            or json.loads(row['context']) != json.loads(tick['context'])):
        raise QueryError('SCHEDULE_RUN_MISMATCH', 409)
    status = row['status'].lower()
    return status if status in ('succeeded', 'failed', 'canceled', 'partial', 'skipped') else None


def _observe(tick, schedule):
    if not tick['run_id']:
        _finish(tick['id'], 'unknown', 'RESERVATION_OUTCOME_UNKNOWN', True)
        return
    schedule = dict(schedule, auth_mode=tick['auth_mode'])
    kind, target, run_id = schedule['target_kind'], schedule['target_id'], tick['run_id']
    status = _terminal_status(tick, schedule)
    if status is None:
        principal, _ = _identity(schedule)
        context = ResolvedQueryContext(**json.loads(tick['context']))
        if kind == 'job':
            run = jobs.get_run(principal, context, run_id)
        elif kind == 'stage':
            import db_stage_scans
            run = db_stage_scans.get(principal, context, target, run_id)
        else:
            import db_marts
            run = db_marts.get_run(principal, context, target, run_id)
        status = run['status'].lower()
    if status not in ('queued', 'running'):
        successful = status == 'succeeded' or kind == 'mart' and status == 'skipped'
        if successful:
            try:
                _identity(schedule)
            except Exception as error:
                _finish(tick['id'], status, getattr(error, 'code', 'OBSERVATION_FAILED'), True)
                return
        _finish(tick['id'], status, 'NO_CHANGES' if kind == 'mart' and status == 'skipped'
                else None if successful else 'RUN_' + status.upper(), not successful)


def _submit(schedule, firing, *, now, refresh_clock, stop):
    principal, context = _identity(schedule)
    with control_store.connect() as db:
        row = _target(db, principal, context, schedule['target_kind'], schedule['target_id'], schedule['target_revision'])
    kind, ident = schedule['target_kind'], schedule['target_id']
    intent = json.loads(schedule['intent'])
    if kind == 'stage':
        # Scan submit preserves its own manifest/content guards. Revalidate the
        # registered Stage Ref before reserving its native parent as well.
        _intent(principal, context, kind, row, intent)
    with _reservation(firing, now, refresh_clock, stop):
        if kind == 'job':
            return jobs.submit_run(principal, context, ident, expected_revision=schedule['target_revision'],
                arguments=intent['arguments'], key='schedule:' + firing)
        if kind == 'stage':
            import db_stage_scans
            return db_stage_scans.submit(principal, context, ident, key='schedule:' + firing,
                                        expected_revision=schedule['target_revision'])
        import db_marts
        return db_marts.submit(principal, context, ident, key='schedule:' + firing,
            expected_revision=schedule['target_revision'], kind='refresh', check_changes=True)


def tick(now=None, *, refresh_clock=None, stop=None):
    if refresh_clock is None:
        refresh_clock = now is None
    now = calendar._utc(now)
    result = dict(started=[], skipped=[], paused=[], unknown=[])
    claim = calendar._lock('db-scheduler-tick.lock')
    if claim is None:
        return result
    try:
        # Recover observations, never submissions, including disabled definitions.
        with control_store.connect() as db:
            pending = [(dict(t), dict(db.execute('SELECT * FROM db_schedules WHERE id=?', (t['schedule_id'],)).fetchone()))
                       for t in db.execute("SELECT * FROM db_schedule_ticks WHERE status IN ('reserved','accepted') OR (status='unknown' AND run_id IS NOT NULL)")]
        for firing, schedule in pending:
            try:
                _observe(firing, schedule)
            except Exception as error:
                _finish(firing['id'], 'unknown', getattr(error, 'code', 'OBSERVATION_FAILED'), True)
        selection_now = calendar._utc() if refresh_clock else now
        with control_store.connect() as db:
            due = [r['id'] for r in db.execute('SELECT id FROM db_schedules WHERE enabled=1 AND next_run_at<=? ORDER BY next_run_at,id', (selection_now.isoformat(),))]
        for ident in due:
            if stop is not None and stop.is_set():
                break
            current = calendar._utc() if refresh_clock else now
            firing = None
            try:
                with control_store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    row = db.execute('SELECT * FROM db_schedules WHERE id=? AND enabled=1 AND next_run_at<=?', (ident, current.isoformat())).fetchone()
                    if row is None:
                        continue
                    schedule = dict(row)
                    firing = str(uuid.uuid5(uuid.NAMESPACE_URL, jobs._json([
                        ident, row['next_run_at'], row['revision'], row['target_revision']])))
                    db.execute("INSERT INTO db_schedule_ticks VALUES (?,?,?,?,?,'reserved',NULL,NULL,?,?)",
                        (firing, ident, row['revision'], row['target_revision'], row['next_run_at'], row['context'], row['auth_mode']))
                    try:
                        due_at = datetime.fromisoformat(row['next_run_at'])
                        following = calendar._next(json.loads(row['spec']), current, due_at)
                    except (ValueError, TypeError, KeyError, OverflowError):
                        db.execute("UPDATE db_schedule_ticks SET status='failed',code='INVALID_SCHEDULE' WHERE id=?", (firing,))
                        db.execute("UPDATE db_schedules SET enabled=0,next_run_at=NULL,last_status='failed',paused_reason='INVALID_SCHEDULE' WHERE id=?", (ident,))
                        result['paused'].append(firing)
                        continue
                    db.execute('UPDATE db_schedules SET next_run_at=? WHERE id=?',
                        (following.isoformat() if following else None, ident))
                if (current - due_at).total_seconds() >= 60:
                    _finish(firing, 'skipped', 'MISSED')
                    result['skipped'].append(firing)
                    continue
                with control_store.connect() as db:
                    admission.check_open(db, json.loads(schedule['context'])['warehouse_id'])
                _submit(schedule, firing, now=now, refresh_clock=refresh_clock, stop=stop)
                with control_store.connect() as db:
                    accepted = dict(db.execute('SELECT * FROM db_schedule_ticks WHERE id=?', (firing,)).fetchone())
                _observe(accepted, schedule)
                result['started'].append(firing)
            except Exception as error:
                with control_store.connect() as db:
                    reserved = db.execute('SELECT run_id FROM db_schedule_ticks WHERE id=?', (firing,)).fetchone() if firing else None
                if reserved is None:
                    logging.getLogger(__name__).exception('DB schedule claim failed: %s', ident)
                    with control_store.connect() as db:
                        db.execute("UPDATE db_schedules SET enabled=0,next_run_at=NULL,last_status='paused',paused_reason='INVALID_SCHEDULE' WHERE id=?", (ident,))
                    result['paused'].append(ident)
                    continue
                if reserved['run_id']:
                    # The submitter committed but its ACK/start was lost. Preserve
                    # the link for the engine's read/recovery path on the next tick.
                    result['unknown'].append(firing)
                    continue
                code = getattr(error, 'code', type(error).__name__)
                skip = code in ('JOB_BUSY', 'MART_BUSY', 'STAGE_BUSY', 'MISSED', 'CLOCK_MOVED', 'STOPPED') or isinstance(error, (admission.AdmissionDenied, admission.AdmissionConflict))
                if isinstance(error, (admission.AdmissionDenied, admission.AdmissionConflict)):
                    code = 'DRAIN' if isinstance(error, admission.AdmissionDenied) else 'BUSY'
                _finish(firing, 'skipped' if skip else 'failed', code, not skip)
                result['skipped' if skip else 'paused'].append(firing)
    finally:
        claim.close()
    return result


def record_alert(principal, context, run_id, position, step, triggered):
    """Record only the already validated Boolean; run rowid is acceptance order."""
    if type(triggered) is not bool:
        raise ValueError('strict Boolean required')
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        revalidate_admission(db, principal, context)
        run = db.execute('SELECT rowid AS acceptance_order,* FROM db_job_runs WHERE id=?', (run_id,)).fetchone()
        current = db.execute('SELECT revision FROM db_jobs WHERE id=?', (run['job_id'],)).fetchone()
        if current is None or current['revision'] != run['revision']:
            return
        subject = hashlib.sha256(jobs._json([run['owner'], context.database_id, run['selected_role'], run['job_id'],
            run['revision'], position, json.loads(run['arguments'])]).encode()).hexdigest()
        prior = db.execute('SELECT acceptance_order FROM db_alert_subjects WHERE subject=?', (subject,)).fetchone()
        if prior and prior[0] >= run['acceptance_order']:
            return
        db.execute('INSERT INTO db_alert_subjects VALUES (?,?) ON CONFLICT(subject) DO UPDATE SET acceptance_order=excluded.acceptance_order',
                   (subject, run['acceptance_order']))
        old = db.execute('SELECT * FROM db_alerts WHERE subject=? AND resolved_at IS NULL', (subject,)).fetchone()
        now = admission._now()
        if not triggered:
            db.execute("UPDATE db_alerts SET resolved_at=?,resolution='false',last_run_id=?,last_seen_at=? WHERE subject=? AND resolved_at IS NULL",
                       (now, run_id, now, subject))
        elif old:
            db.execute('UPDATE db_alerts SET last_run_id=?,last_seen_at=?,occurrences=occurrences+1 WHERE id=?', (run_id, now, old['id']))
        else:
            db.execute('INSERT INTO db_alerts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL)',
                (str(uuid.uuid4()), subject, context.database_id, run['owner'], context.selected_role,
                 run['context'], run['job_id'], run['revision'], position, step['name'], run['arguments'],
                 run_id, run_id, 1, now, now))


def _alert(row):
    result = dict(row)
    for k in ('context', 'arguments'):
        result[k] = json.loads(result[k])
    return result


def list_alerts(principal, context):
    jobs._access(principal, context)
    with control_store.connect() as db:
        alerts = []
        for row in db.execute('SELECT * FROM db_alerts WHERE owner=? AND database_id=? ORDER BY first_seen_at,id',
                              (principal.principal_id, context.database_id)):
            try:
                _visible(row, principal, context)
            except QueryError:
                continue
            alerts.append(_alert(row))
        revalidate_admission(db, principal, context)
    return dict(alerts=alerts, unacknowledged_count=sum(a['acknowledged_at'] is None and a['resolved_at'] is None for a in alerts))


def acknowledge_alert(principal, context, alert_id, *, key):
    jobs._access(principal, context)
    jobs._key(key)
    payload = jobs._json(dict(context=asdict(context), alert_id=alert_id))
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        revalidate_admission(db, principal, context)
        row = db.execute('SELECT * FROM db_alerts WHERE id=?', (alert_id,)).fetchone()
        _visible(row, principal, context)
        replay = _request(db, principal, 'ack', key, payload)
        if replay is not None:
            return replay
        db.execute('UPDATE db_alerts SET acknowledged_at=COALESCE(acknowledged_at,?) WHERE id=?', (admission._now(), alert_id))
        result = _alert(db.execute('SELECT * FROM db_alerts WHERE id=?', (alert_id,)).fetchone())
        _receipt(db, principal, 'ack', key, payload, result)
        return result
