"""Shared registered LOCAL lifecycle; slow physical work never owns a write transaction."""
from contextlib import contextmanager
from contextvars import ContextVar
import base64
from decimal import Decimal
from datetime import datetime
import re
import fcntl
import json
import os
import time
import uuid

import control_store
import warehouse_admission as admission
import warehouse_resident as wr
from warehouse_operations import OperationError


_management_operation = ContextVar('runtime_operation', default=None)
_LOCAL_PROOF_ID = None


def applicable():
    import logical_creations
    import runtime_profile
    if not (logical_creations.enabled() and runtime_profile.logical_config()['warehouse_id'] == 'LOCAL'):
        return False
    cfg = runtime_profile.logical_config()
    with control_store.connect() as db:
        row = db.execute('SELECT generation_id,revision FROM logical_foundations WHERE environment_id=? AND warehouse_id=?',
                         (cfg['environment_id'],cfg['warehouse_id'])).fetchone()
        return bool(row and row['generation_id'] == cfg['foundation_generation_id'] and row['revision'] == cfg['foundation_revision'])


def invalidate_proof():
    global _LOCAL_PROOF_ID
    _LOCAL_PROOF_ID = None
    _proof_path().unlink(missing_ok=True)


def _proof_path():
    return control_store.root() / 'local-engine-ready.json'


def _shared_proof():
    try:
        data = json.loads(_proof_path().read_text())
        import runtime_profile
        cfg = runtime_profile.logical_config()
        if (type(data) is not dict or type(data.get('generation_id')) is not str
                or not data['generation_id'] or data['generation_id'] != cfg['foundation_generation_id']
                or type(data.get('revision')) is not int or data['revision'] < 1
                or data['revision'] != cfg['foundation_revision']
                or type(data.get('epoch')) is not str or not data['epoch']
                or type(data.get('verified_at')) is not str):
            return None
        observed = datetime.fromisoformat(data['verified_at'])
        if observed.tzinfo is None or observed.utcoffset() is None:
            return None
        return {field:data[field] for field in ('generation_id','revision','epoch','verified_at')}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def shared_engine_proof():
    """Read shared engine evidence without adopting this process's Provider epoch."""
    return _shared_proof()


def invalidate_observed_proof(captured):
    """Discard unchanged evidence after a non-active GET without waiting for Wake."""
    if not captured:
        return
    with (control_store.root() / 'local-lifecycle.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        try:
            if _shared_proof() == captured:
                invalidate_proof()
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _adopt_proof(data):
    global _LOCAL_PROOF_ID
    _LOCAL_PROOF_ID = (os.getpid(),str(_proof_path()),data['epoch'])


def requires_wake():
    if not applicable():
        return False
    data = _shared_proof()
    return data is None or _LOCAL_PROOF_ID != (os.getpid(),str(_proof_path()),data['epoch'])


@contextmanager
def _lock(cancelled=None):
    with (control_store.root() / 'local-lifecycle.lock').open('a') as stream:
        while True:
            if cancelled and cancelled():
                raise OperationError('WAKE_CANCELED', 409)
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(.02)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _proof():
    import runtime_profile
    cfg = runtime_profile.logical_config()
    return dict(verified_at=admission._now(), generation_id=cfg['foundation_generation_id'], revision=cfg['foundation_revision'],epoch=uuid.uuid4().hex)


def _save_proof():
    path = _proof_path()
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    data = _proof()
    try:
        temporary.write_text(json.dumps(data))
        os.chmod(temporary, 0o600)
        temporary.replace(path)
        _adopt_proof(data)
    finally:
        temporary.unlink(missing_ok=True)


def _finish(handle, state, evidence=None):
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        admission.finish(db, handle, state=state, evidence_ref=evidence)


def observe_startup(provider):
    """Adopt an active peer epoch after this lifetime's full registered binding."""
    global _LOCAL_PROOF_ID
    with admission.lifecycle_gate(), _lock():
        # Local startup failure says nothing about an already verified peer.
        _LOCAL_PROOF_ID = None
        state = provider.compute_state()
        if state == 'ACTIVE':
            if not provider.bound_alive():
                return
            import logical_creations
            with logical_creations._provider_use():
                if logical_creations._provider is not provider:
                    raise OperationError('WAREHOUSE_UNAVAILABLE',503)
                repaired = logical_creations.ensure_active_provider(force_rebind=True)
                if repaired is not provider or not provider.bound_alive():
                    raise OperationError('WAREHOUSE_UNAVAILABLE',503)
                shared = _shared_proof()
                if shared is None:
                    _save_proof()
                else:
                    _adopt_proof(shared)
        elif state in ('STOPPED','TRANSITIONING'):
            invalidate_proof()
        else:
            raise OperationError('WAREHOUSE_UNAVAILABLE',503)


def physical_started(operation_id=None):
    identifier = operation_id or _management_operation.get()
    if identifier is None:
        return False
    with control_store.connect() as db:
        row = db.execute('SELECT before_after FROM operations WHERE id=?', (identifier,)).fetchone()
        return bool(row and json.loads(row['before_after']).get('physical_started') is True)


def _repair_provider():
    import logical_creations
    # kubectl can stay locally alive after its selected Pod was terminated.
    # A physical Wake/new shared epoch needs our own fresh tunnels and full proof.
    wr.invalidate_persistent_trino()
    provider = logical_creations.ensure_active_provider(force_rebind=True)
    if provider is None:
        raise OperationError('FOUNDATION_NOT_READY', 503)
    return provider


def mark_physical_started():
    identifier = _management_operation.get()
    if identifier is None:
        raise OperationError('RUNTIME_OPERATION_REQUIRED', 409)
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM operations WHERE id=?', (identifier,)).fetchone()
        if (not row or row['action'] not in ('WAREHOUSE_WAKE','WAREHOUSE_CHILL','WAREHOUSE_AUTO_CHILL')
                or row['state'] not in ('ACCEPTED','RUNNING') or json.loads(row['targets']) != ['LOCAL']):
            raise OperationError('RUNTIME_OPERATION_REQUIRED', 409)
        metadata = json.loads(row['before_after'])
        metadata['physical_started'] = True
        db.execute('UPDATE operations SET before_after=? WHERE id=?', (json.dumps(metadata),identifier))


def _stop_barrier(db, enabled):
    identifier = _management_operation.get()
    row = db.execute('SELECT * FROM operations WHERE id=?', (identifier,)).fetchone()
    if (not row or row['action'] not in ('WAREHOUSE_CHILL','WAREHOUSE_AUTO_CHILL')
            or row['state'] not in ('ACCEPTED','RUNNING','UNKNOWN') or json.loads(row['targets']) != ['LOCAL']):
        raise OperationError('RUNTIME_OPERATION_REQUIRED', 409)
    metadata = json.loads(row['before_after'])
    metadata['stopping_barrier'] = enabled
    db.execute('UPDATE operations SET before_after=? WHERE id=?', (json.dumps(metadata),identifier))


def _warm_ready():
    if requires_wake():
        return False
    with control_store.connect() as db:
        if admission.stopping_barriers(db):
            raise admission.AdmissionDenied('warehouse stop is unresolved')
        if _incompatible_runtime(db):
            raise OperationError('WAREHOUSE_RECOVERY_REQUIRED',409)
        handle = admission.current()
        if handle is not None:
            admission.check_execution(db, handle)
            if admission.control(db)['state'] not in ('OPEN','DRAINING'):
                raise admission.AdmissionDenied('warehouse is unavailable')
        else:
            admission.check_open(db)
    return not requires_wake()


def ensure_awake(*, actor, authorize, cancelled=None):
    if not applicable():
        return dict(state='unsupported')
    authorize()
    if cancelled and cancelled():
        raise OperationError('WAKE_CANCELED',409)
    if _warm_ready():
        return dict(state='active',engine_verified=True)
    with admission.lifecycle_gate(), _lock(cancelled):
        authorize()
        if cancelled and cancelled():
            raise OperationError('WAKE_CANCELED', 409)
        with control_store.connect() as db:
            foundation = wr._logical_foundation(db)
            admission.check_open(db, 'LOCAL')
            import warehouse_runtime_routes as runtime
            if _incompatible_runtime(db):
                raise OperationError('WAREHOUSE_RECOVERY_REQUIRED', 409)
            if any(row['kind'] == 'wake' for row in admission.blockers(db)):
                raise OperationError('WAREHOUSE_RECOVERY_REQUIRED', 409)
            expected = wr._logical_wake_expected(foundation, db)
            observed = wr._logical_status('LOCAL', foundation)
        shared = _shared_proof()
        if observed['state'] == 'active' and shared is not None:
            import logical_creations
            try:
                _repair_provider()
                authorize()
                _adopt_proof(shared)
            except BaseException:
                invalidate_proof()
                raise
            return dict(state='active', engine_verified=True)
        # Wake is independent of the waiting query's capability and cancellation.
        token = admission._current.set(None)
        operation_token = None
        automatic_operation = None
        try:
            authorize()
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                handle = admission.admit(db, actor=actor, kind='wake', subject_id=str(uuid.uuid4()))
                if _management_operation.get() is None:
                    import warehouse_runtime_routes as runtime
                    automatic_operation = handle.subject_id
                    db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (automatic_operation,actor,'WAREHOUSE_WAKE','["LOCAL"]',None,'RUNNING','[]',None,
                         json.dumps(dict(process=runtime._PROCESS,pid=os.getpid())),json.dumps(dict(started=admission._now()))))
                    operation_token = _management_operation.set(automatic_operation)
            started = False
            try:
                with admission.accepted(handle), control_store.connect() as db:
                    authorize()
                    if cancelled and cancelled():
                        raise OperationError('WAKE_CANCELED', 409)
                    def dispatch():
                        nonlocal started
                        mark_physical_started()
                        started = True
                    result = wr._logical_transition('wake', 'LOCAL', foundation, db, authorize=authorize, mutation_started=dispatch)
                import logical_creations
                _repair_provider()
                _save_proof()
                _finish(handle, 'FINISHED')
                if automatic_operation:
                    runtime._update(automatic_operation,'SUCCEEDED')
            except BaseException as error:
                invalidate_proof()
                outcome = 'UNKNOWN' if started else 'CANCELED' if getattr(error, 'code', None) == 'WAKE_CANCELED' else 'FAILED'
                _finish(handle, outcome)
                if automatic_operation:
                    runtime._update(automatic_operation,'UNKNOWN' if started else 'FAILED','WAREHOUSE_WAKE_FAILED')
                raise
        finally:
            if operation_token is not None:
                _management_operation.reset(operation_token)
            admission._current.reset(token)
        return dict(result, engine_verified=True)


def _incompatible_runtime(db):
    import warehouse_runtime_routes as runtime
    return tuple(row for row in runtime_blockers(db)
        if not (row['action'] == 'WAREHOUSE_WAKE' and runtime._operation(row)['phase'] in ('ACCEPTED','RUNNING')))


def runtime_blockers(db):
    return tuple(dict(row) for row in db.execute("SELECT * FROM operations WHERE action IN ('WAREHOUSE_WAKE','WAREHOUSE_CHILL','WAREHOUSE_AUTO_CHILL','WAREHOUSE_RECONCILE') AND state IN ('ACCEPTED','RUNNING','UNKNOWN') AND id != ?", (_management_operation.get() or '',))
                 if not json.loads(row['before_after']).get('policy_change'))


def blockers(db):
    import logical_creations
    work = admission.blockers(db)
    creation = logical_creations.compute_blockers(db)
    return dict(runtime_operations=len(runtime_blockers(db)), admissions=len(work), unknown=sum(r['state'] == 'UNKNOWN' for r in work),
                creation=creation)


_METRIC_ALL = 'trino_execution_name_QueryManager_QueryTracker_AllQueriesCount'
_METRIC_EXPIRED = 'trino_execution_name_QueryManager_QueryTracker_ExpiredQueriesCount'
_METRIC_NUMBER = r'(?:[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?|[+-]?Inf|NaN)'
_METRIC_LABEL = r'[A-Za-z_][A-Za-z0-9_]*="(?:[^"\\\r\n]|\\[\\"n])*"'
_METRIC_LABELS = r'\{(?:' + _METRIC_LABEL + r'(?:,\s*' + _METRIC_LABEL + r')*,?)?\}'
_METRIC_SAMPLE = re.compile(r'([A-Za-z_:][A-Za-z0-9_:]*)(?:' + _METRIC_LABELS + r')?[ \t]+('
    + _METRIC_NUMBER + r')(?:[ \t]+' + _METRIC_NUMBER + r')?(?:[ \t]+#[ \t]+'
    + _METRIC_LABELS + r'[ \t]+' + _METRIC_NUMBER + r'(?:[ \t]+' + _METRIC_NUMBER + r')?)?[ \t]*')


def _active_engine_queries(provider):
    """Observe fixed aggregate QueryTracker gauges without SQL or query details."""
    try:
        identity = provider._provisioner()
        username, password = identity['username'], identity['password']
        if not isinstance(username,str) or not username or not isinstance(password,str) or not password:
            raise ValueError()
        credential = base64.b64encode((username + ':' + password).encode()).decode('ascii')
        body = provider._request('trino-local','GET','/metrics',
            headers={'Authorization':'Basic ' + credential},raw=True)
        if type(body) is not bytes or not body or len(body) >= 4 * 1024 * 1024:
            raise ValueError()
        text = body.decode('utf-8',errors='strict')
        if any((ord(char) < 32 and char not in '\t\r\n') or ord(char) == 127 for char in text):
            raise ValueError()
        values = {}
        for line in text.splitlines():
            if len(line) > 16384:
                raise ValueError()
            if not line.strip() or line.startswith('#'):
                continue
            sample = _METRIC_SAMPLE.fullmatch(line)
            if sample is None:
                raise ValueError()
            name, number = sample.group(1,2)
            if name not in (_METRIC_ALL,_METRIC_EXPIRED):
                continue
            # These fixed global gauges have no dimensions, timestamps or exemplars.
            if re.fullmatch(re.escape(name) + r'[ \t]+' + _METRIC_NUMBER + r'[ \t]*',line) is None:
                raise ValueError()
            if name in values or len(number) > 64:
                raise ValueError()
            count = Decimal(number)
            if (not count.is_finite() or count < 0 or count > 9223372036854775807
                    or count != count.to_integral_value()):
                raise ValueError()
            values[name] = int(count)
        if set(values) != {_METRIC_ALL,_METRIC_EXPIRED} or values[_METRIC_EXPIRED] > values[_METRIC_ALL]:
            raise ValueError()
        return values[_METRIC_ALL] - values[_METRIC_EXPIRED]
    except Exception:
        # Backend diagnostics may contain private input. Never propagate it.
        raise OperationError('WAREHOUSE_OBSERVATION_FAILED',503) from None


def chill(*, authorize, automatic=False):
    authorize()
    if _management_operation.get() is None:
        import warehouse_runtime_routes as runtime
        identifier = str(uuid.uuid4())
        with control_store.connect() as db:
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?,?)',
                (identifier,'shirokuma-local','WAREHOUSE_AUTO_CHILL' if automatic else 'WAREHOUSE_CHILL',
                 '["LOCAL"]',None,'RUNNING','[]',None,json.dumps(dict(process=runtime._PROCESS,pid=os.getpid())),
                 json.dumps(dict(started=admission._now()))))
        token = _management_operation.set(identifier)
        try:
            result = chill(authorize=authorize,automatic=automatic)
            runtime._update(identifier,'SUCCEEDED')
            return result
        except BaseException:
            runtime._update(identifier,'UNKNOWN' if physical_started(identifier) else 'FAILED','WAREHOUSE_STOP_FAILED')
            raise
        finally:
            _management_operation.reset(token)
    with wr._warehouse_gate(exclusive=True), _lock():
        authorize()
        with control_store.connect() as db:
            wr.check_lifecycle(db=db)
            foundation = wr._logical_foundation(db)
            observed = wr._logical_status('LOCAL', foundation)
        if observed['state'] == 'chilled':
            with control_store.connect() as db:
                expected = wr._logical_wake_expected(foundation, db)
                wr._logical_stopped_ready(foundation, db, expected)
            invalidate_proof()
            return dict(state='chilled', result='noop')
        import logical_creations
        # The provider owns the fixed diagnostic and its provisioner identity.
        provider = logical_creations.ensure_active_provider()
        if provider is None:
            raise OperationError('WAREHOUSE_UNAVAILABLE', 503)
        count = _active_engine_queries(provider)
        if count:
            raise OperationError('WAREHOUSE_BUSY', 409)
        authorize()
        if automatic:
            minutes = wr._load_auto_chill_minutes()
            if not minutes or time.time() - wr.read_last_activity() < minutes * 60:
                return dict(state='active', result='busy')
        invalidate_proof()
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            wr.check_lifecycle(db=db)
            _stop_barrier(db, True)
        try:
            with control_store.connect() as db:
                result = wr._logical_transition('chill', 'LOCAL', foundation, db, authorize=authorize,
                                                mutation_started=mark_physical_started)
        except BaseException:
            if not physical_started():
                raise
            raise OperationError('WAREHOUSE_STOP_UNKNOWN', 503) from None
        # The owning worker publishes terminal state and clears the barrier in
        # one transaction. Admissions cannot slip into a RUNNING-stop gap.
        return result


def reconcile(*, authorize):
    authorize()
    with admission.lifecycle_gate(exclusive=True), _lock():
        authorize()
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # Rebinding a Provider also invalidates this process's SQL tunnel.
            # Ordinary accepted work and creation gaps must stay untouched.
            if any(row['kind'] != 'wake' for row in admission.blockers(db)):
                raise OperationError('WAREHOUSE_BUSY',409)
            import logical_creations
            if any(logical_creations.compute_blockers(db).values()):
                raise OperationError('WAREHOUSE_BUSY',409)
            import warehouse_runtime_routes
            if any(warehouse_runtime_routes._operation(row)['phase'] != 'UNKNOWN' for row in runtime_blockers(db)):
                raise OperationError('WAREHOUSE_BUSY', 409)
        # No write transaction survives into Kubernetes/Provider observations.
        with control_store.connect() as db:
            foundation = wr._logical_foundation(db)
            expected = wr._logical_wake_expected(foundation, db)
            observed = wr._logical_status('LOCAL', foundation)
            if observed['state'] == 'active':
                wr._logical_wake_ready(foundation, db, expected)
                import logical_creations
                try:
                    _repair_provider()
                except BaseException:
                    invalidate_proof()
                    raise
            elif observed['state'] == 'chilled':
                wr._logical_stopped_ready(foundation,db,expected)
            else:
                raise OperationError('WAREHOUSE_RECOVERY_REQUIRED', 409)
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in runtime_blockers(db):
                metadata, timestamps = json.loads(row['before_after']), json.loads(row['timestamps'])
                metadata['observed_state'] = observed['state']
                metadata['stopping_barrier'] = False
                timestamps['reconciled'] = admission._now()
                # Observation resolves uncertainty, not the original requested
                # outcome (a lost Chill may still be physically active).
                db.execute('UPDATE operations SET state=?,before_after=?,timestamps=? WHERE id=?',
                    ('RECONCILED', json.dumps(metadata), json.dumps(timestamps), row['id']))
            for row in admission.blockers(db):
                if row['kind'] == 'wake':
                    admission.finish(db, admission._handle(row), state='FINISHED', evidence_ref='runtime:observed')
        if observed['state'] == 'active':
            try:
                _save_proof()
            except BaseException:
                invalidate_proof()
                raise
        else:
            invalidate_proof()
        return dict(state=observed['state'], engine_verified=observed['state'] == 'active')
