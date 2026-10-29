"""Authenticated metadata and explicit LOCAL runtime operation boundary."""
from contextlib import contextmanager
import fcntl
import json
import os
import re
import threading
import uuid
from urllib.parse import urlsplit

import control_store
import request_auth
import warehouse_admission as admission
import warehouse_lifecycle as lifecycle
import warehouse_resident as wr
from warehouse_operations import OperationError

BASE = '/api/v1/warehouses/LOCAL/runtime'
_PROCESS = str(uuid.uuid4())


def authorize(principal, role, *, platform=False):
    import audit_service
    account = audit_service._account(principal, role)
    if platform and not (role == 'admin' and principal.maximum_role == 'admin' and principal.platform_admin is True
                         and account['role'] == 'admin' and account['platform_admin'] is True):
        raise PermissionError('platform authority required')
    if not lifecycle.applicable():
        raise OperationError('WAREHOUSE_UNAVAILABLE', 503)
    return account


@contextmanager
def _request_lock():
    with (control_store.root() / 'runtime-requests.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def policy():
    path = wr.activity_path().with_name('auto_chill.json')
    try:
        value = json.loads(path.read_text())
        minutes, revision = value['auto_chill_minutes'], value.get('revision', 0)
        if type(minutes) is not int or not 0 <= minutes <= 1440 or type(revision) is not int or revision < 0:
            raise ValueError()
        return dict(minutes=minutes, revision=revision)
    except FileNotFoundError:
        return dict(minutes=0, revision=0)
    except (ValueError, KeyError, TypeError):
        raise OperationError('POLICY_UNAVAILABLE', 503) from None


def _set_policy(body):
    current = policy()
    if current['revision'] != body['expected_revision']:
        raise OperationError('REVISION_CONFLICT', 409)
    path = wr.activity_path().with_name('auto_chill.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.auto_chill.' + uuid.uuid4().hex)
    try:
        temporary.write_text(json.dumps(dict(auto_chill_minutes=body['minutes'], revision=current['revision'] + 1)))
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return policy()


def _owner_alive(metadata):
    pid = metadata.get('pid')
    if type(pid) is not int or pid <= 0:
        return False
    if pid == os.getpid():
        return metadata.get('process') == _PROCESS
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _operation(row):
    metadata = json.loads(row['before_after'])
    state = row['state']
    if state in ('ACCEPTED', 'RUNNING') and not _owner_alive(metadata):
        state = 'UNKNOWN'
    return dict(id=row['id'], actor=row['actor'], action=row['action'], phase=state,
                timestamps=json.loads(row['timestamps']), error_code=metadata.get('error_code'))


def operation(identifier):
    with control_store.connect() as db:
        row = db.execute("SELECT * FROM operations WHERE id=? AND action IN ('WAREHOUSE_WAKE','WAREHOUSE_CHILL','WAREHOUSE_AUTO_CHILL','WAREHOUSE_RECONCILE')", (identifier,)).fetchone()
        if row is None:
            raise OperationError('NOT_FOUND', 404)
        return _operation(row)


def status(principal, role):
    account = authorize(principal, role)
    with control_store.connect() as db:
        counts = lifecycle.blockers(db)
        latest = next((row for row in db.execute("SELECT * FROM operations WHERE action IN ('WAREHOUSE_WAKE','WAREHOUSE_CHILL','WAREHOUSE_AUTO_CHILL','WAREHOUSE_RECONCILE') ORDER BY rowid DESC")
                       if not json.loads(row['before_after']).get('policy_change')), None)
    error = None
    proof = None
    try:
        captured = lifecycle.shared_engine_proof()
        with control_store.connect() as db:
            observed = wr._logical_status('LOCAL', wr._logical_foundation(db))
        if observed['state'] != 'active':
            lifecycle.invalidate_observed_proof(captured)
        if observed['state'] == 'active':
            shared = lifecycle.shared_engine_proof()
            if shared is not None:
                proof = shared['verified_at']
    except OperationError:
        observed = dict(state='unknown',replicas=None,ready_replicas=None)
        error = 'WAREHOUSE_UNAVAILABLE'
    platform = role == 'admin' and principal.maximum_role == 'admin' and principal.platform_admin and account['platform_admin']
    return dict(warehouse='LOCAL', observation_error=error, physical_state=observed['state'], replicas=observed['replicas'],
                ready_replicas=observed['ready_replicas'], observed_at=admission._now(),
                engine_verified=proof is not None, engine_verified_at=proof, policy=policy(), blockers=counts,
                operation_id=latest['id'] if latest else None,
                operation_action=latest['action'] if latest else None,
                operation_phase=_operation(latest)['phase'] if latest else None,
                available_actions=['wake','chill','auto-chill','reconcile'] if platform else [])


def _update(identifier, phase, error=None):
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT before_after,timestamps FROM operations WHERE id=?', (identifier,)).fetchone()
        metadata, times = json.loads(row[0]), json.loads(row[1])
        metadata['error_code'] = error
        if phase in ('SUCCEEDED', 'FAILED', 'RECONCILED'):
            metadata['stopping_barrier'] = False
        times[phase.lower()] = admission._now()
        db.execute('UPDATE operations SET state=?,before_after=?,timestamps=? WHERE id=?',
                   (phase, json.dumps(metadata), json.dumps(times), identifier))


def _run(identifier, action, principal, role):
    check = lambda: authorize(principal, role, platform=True)
    token = lifecycle._management_operation.set(identifier)
    try:
        check()
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("UPDATE operations SET state='RUNNING' WHERE id=? AND state='ACCEPTED'", (identifier,)).rowcount != 1:
                return
        if action == 'wake':
            lifecycle.ensure_awake(actor=principal.principal_id, authorize=check)
        elif action == 'chill':
            lifecycle.chill(authorize=check)
        else:
            lifecycle.reconcile(authorize=check)
        _update(identifier, 'SUCCEEDED')
    except BaseException as error:
        safe = getattr(error, 'code', 'RUNTIME_OPERATION_FAILED')
        if not re.fullmatch('[A-Z][A-Z0-9_]{0,63}', safe):
            safe = 'RUNTIME_OPERATION_FAILED'
        _update(identifier, 'UNKNOWN' if lifecycle.physical_started(identifier) else 'FAILED', safe)
    finally:
        lifecycle._management_operation.reset(token)


def accept(principal, role, action, body, key):
    authorize(principal, role, platform=True)
    if action not in ('wake','chill','reconcile','auto-chill'):
        raise ValueError('invalid action')
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', key):
        raise ValueError('idempotency key required')
    if action == 'auto-chill':
        if (set(body) != {'minutes','expected_revision'} or type(body['minutes']) is not int
                or not 0 <= body['minutes'] <= 1440 or type(body['expected_revision']) is not int
                or body['expected_revision'] < 0):
            raise ValueError('invalid policy')
    elif body != {}:
        raise ValueError('empty body required')
    intent = json.dumps(dict(action=action, body=body), sort_keys=True)
    with _request_lock():
        authorize(principal, role, platform=True)
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM warehouse_action_requests WHERE actor=? AND route=? AND idempotency_key=?',
                (principal.principal_id, BASE, key)).fetchone()
            if previous:
                if previous['payload'] != intent:
                    raise OperationError('IDEMPOTENCY_CONFLICT', 409)
                return dict(operation_id=previous['operation_id'], phase='ACCEPTED')
            if action == 'auto-chill' and policy()['revision'] != body['expected_revision']:
                raise OperationError('REVISION_CONFLICT', 409)
            identifier = str(uuid.uuid4())
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?,?)',
                (identifier, principal.principal_id, 'WAREHOUSE_' + action.replace('-','_').upper(),
                 json.dumps(['LOCAL']), None, 'ACCEPTED', '[]', None,
                 json.dumps(dict(process=_PROCESS, pid=os.getpid(), policy_change=action == 'auto-chill')), json.dumps(dict(accepted=admission._now()))))
            db.execute("INSERT INTO warehouse_action_requests VALUES (?,?,?,?,?,?,?,'REQUESTED')",
                (str(uuid.uuid4()), identifier, principal.principal_id, BASE, key, intent, admission._now()))
        if action == 'auto-chill':
            # Reconcile policy after a crash by explicit fresh CAS; never reapply it on GET.
            try:
                authorize(principal, role, platform=True)
                _set_policy(body)
            except PermissionError:
                _update(identifier, 'FAILED', 'OPERATION_FORBIDDEN')
                raise
            except Exception:
                _update(identifier, 'UNKNOWN', 'POLICY_WRITE_FAILED')
                raise OperationError('POLICY_UNAVAILABLE', 503) from None
            _update(identifier, 'SUCCEEDED')
    if action != 'auto-chill':
        threading.Thread(target=_run, args=(identifier,action,principal,role), daemon=True,
                         name='warehouse-runtime-operation').start()
    return dict(operation_id=identifier, phase='ACCEPTED')


def monitor_chill():
    # A disabled, recently active, blocked, or already asleep tick has no
    # accepted physical intent and must not manufacture an operation.
    minutes = wr._load_auto_chill_minutes()
    if not minutes or wr.time.time() - wr.read_last_activity() < minutes * 60:
        return dict(state='unchanged', result='idle-policy-not-due')
    with control_store.connect() as db:
        try:
            wr.check_lifecycle(db=db)
        except (admission.AdmissionConflict, admission.AdmissionDenied):
            return dict(state='unchanged', result='blocked')
        foundation = wr._logical_foundation(db)
        captured = lifecycle.shared_engine_proof()
        if wr._logical_status('LOCAL', foundation)['state'] == 'chilled':
            lifecycle.invalidate_observed_proof(captured)
            return dict(state='unchanged', result='already-stopped')
    identifier = str(uuid.uuid4())
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?,?)',
            (identifier,'shirokuma-local','WAREHOUSE_AUTO_CHILL','["LOCAL"]',None,'RUNNING','[]',None,
             json.dumps(dict(process=_PROCESS, pid=os.getpid())),json.dumps(dict(started=admission._now()))))
    token = lifecycle._management_operation.set(identifier)
    try:
        result = lifecycle.chill(authorize=lambda: None, automatic=True)
        _update(identifier,'SUCCEEDED')
        return result
    except BaseException as error:
        _update(identifier,'UNKNOWN' if lifecycle.physical_started(identifier) else 'FAILED',
                'WAREHOUSE_BUSY' if isinstance(error,(admission.AdmissionConflict,admission.AdmissionDenied)) else 'WAREHOUSE_OBSERVATION_FAILED')
        raise
    finally:
        lifecycle._management_operation.reset(token)


def dispatch(handler, method):
    path = urlsplit(handler.path).path
    if path != BASE and not path.startswith(BASE + '/'):
        return False
    try:
        principal = request_auth.authenticate_principal(handler.headers)
        roles = handler.headers.get_all('X-Shirokuma-Role', [])
        if len(roles) != 1 or roles[0] not in request_auth.ROLES:
            raise ValueError('role required')
        role = roles[0]
        if '?' in handler.path or '#' in handler.path or handler.headers.get_all('Transfer-Encoding', []):
            raise ValueError('invalid framing')
        authorize(principal, role)
        detail = re.fullmatch(re.escape(BASE) + r'/operations/([A-Za-z0-9_-]{1,128})', path)
        if method == 'GET':
            if handler.headers.get_all('Content-Length', []) not in ([], ['0']):
                raise ValueError('GET body forbidden')
            if path == BASE:
                result = status(principal, role)
            elif detail:
                authorize(principal, role, platform=True)
                result = operation(detail[1])
            else:
                raise OperationError('NOT_FOUND', 404)
            handler.respond(result)
        elif method == 'POST' and path in [BASE + '/' + a for a in ('wake','chill','reconcile','auto-chill')]:
            import workbench_server
            if handler.headers.get('Origin') and not workbench_server.request_origin_allowed(handler.headers):
                raise PermissionError('invalid origin')
            lengths = handler.headers.get_all('Content-Length', [])
            if len(lengths) != 1 or not re.fullmatch('[0-9]{1,4}', lengths[0]) or not 0 < int(lengths[0]) <= 4096:
                raise ValueError('invalid framing')
            keys = handler.headers.get_all('Idempotency-Key', [])
            if len(keys) != 1:
                raise ValueError('key required')
            result = accept(principal, role, path.rsplit('/',1)[1], handler.database_body(), keys[0])
            handler.respond(result, 202)
        else:
            raise OperationError('METHOD_NOT_ALLOWED', 405)
    except Exception as error:
        if isinstance(error, request_auth.AuthenticationError):
            code, http = 'AUTHENTICATION_REQUIRED', 401
        elif isinstance(error, PermissionError):
            code, http = 'FORBIDDEN', 403
        elif isinstance(error, (ValueError, TypeError, UnicodeError)):
            code, http = 'INVALID_INPUT', 400
        else:
            code, http = getattr(error, 'code', 'WAREHOUSE_UNAVAILABLE'), getattr(error, 'status', 503)
        handler.respond(dict(code=code, message='Warehouse runtime request rejected', retryable=False, details={}), http)
    return True
