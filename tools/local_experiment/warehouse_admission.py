"""Durable whole-operation admission and the shared local warehouse lock.

Handles are internal capabilities: HTTP/CLI request fields must never supply them.
Admission and a query/run's intent are written in the caller's same transaction.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import fcntl
import threading
import time
from typing import Optional
import uuid

import control_store


class AdmissionDenied(RuntimeError):
    code, status = 'WAREHOUSE_MAINTENANCE', 503


class AdmissionConflict(RuntimeError):
    code, status = 'ADMISSION_CONFLICT', 409


class GateTimeout(RuntimeError):
    code, status = 'WAREHOUSE_GATE_TIMEOUT', 409


@dataclass(frozen=True)
class AdmissionHandle:
    id: str
    warehouse_id: str
    actor: str
    kind: str
    subject_id: str
    generation_id: Optional[str] = None
    database_id: Optional[str] = None


_current = ContextVar('warehouse_admission', default=None)
_held = threading.local()
TERMINAL = frozenset(('FINISHED', 'FAILED', 'CANCELED'))


def execution_unresolved(row):
    return row['state'] not in TERMINAL and not (
        row['kind'] == 'query' and row['execution_closure'] == 'FENCED_READ_ONLY')


def query_closure(db, query_id):
    row = db.execute("SELECT execution_closure FROM warehouse_admissions WHERE kind='query' AND subject_id=?",
                     (query_id,)).fetchone()
    return row['execution_closure'] if row else ''


def check_execution(db, handle):
    row = _row(db, handle)
    if row['execution_closure']:
        raise AdmissionConflict('execution is closed; outcome remains unknown')
    owner = control(db, handle.warehouse_id)['operation_id']
    if owner and db.execute('SELECT 1 FROM execution_recovery_requests WHERE operation_id=?', (owner,)).fetchone():
        raise AdmissionDenied('warehouse execution recovery is draining')
    return row


def _now():
    return datetime.now(timezone.utc).isoformat()


def _transaction(db):
    if not db.in_transaction:
        raise AdmissionConflict('admission requires the caller transaction')


def control(db, warehouse_id='LOCAL'):
    row = db.execute('SELECT * FROM warehouse_controls WHERE warehouse_id=?', (warehouse_id,)).fetchone()
    return dict(row) if row else dict(warehouse_id=warehouse_id, state='OPEN', operation_id=None,
                                     revision=0, applied_generation_id=None)


def _ensure(db, warehouse_id):
    db.execute("INSERT OR IGNORE INTO warehouse_controls VALUES (?, 'OPEN', NULL, 1, NULL)", (warehouse_id,))
    return control(db, warehouse_id)


def current():
    return _current.get()


def _handle(row):
    return AdmissionHandle(*(row[key] for key in ('id', 'warehouse_id', 'actor', 'kind', 'subject_id', 'generation_id', 'database_id')))


def _row(db, handle):
    if not isinstance(handle, AdmissionHandle):
        raise AdmissionConflict('invalid admission handle')
    row = db.execute('SELECT * FROM warehouse_admissions WHERE id=?', (handle.id,)).fetchone()
    if row is None or _handle(row) != handle:
        raise AdmissionConflict('invalid admission handle')
    return row


def get_for_subject(db, *, kind, subject_id, warehouse_id='LOCAL'):
    row = db.execute('SELECT * FROM warehouse_admissions WHERE warehouse_id=? AND kind=? AND subject_id=?',
                     (warehouse_id, kind, subject_id)).fetchone()
    return _handle(row) if row else None


def check_open(db, warehouse_id='LOCAL'):
    if control(db, warehouse_id)['state'] != 'OPEN':
        raise AdmissionDenied('warehouse configuration is being reconciled')


def admit(db, *, warehouse_id='LOCAL', actor, kind, subject_id, parent=None, generation_id=None, database_id=None):
    _transaction(db)
    if database_id is not None and (not isinstance(database_id, str) or not database_id):
        raise AdmissionConflict('invalid database identity')
    if any(not isinstance(value, str) or not value or len(value) > 256
           for value in (warehouse_id, actor, kind, subject_id)):
        raise AdmissionConflict('invalid admission identity')
    state = _ensure(db, warehouse_id)
    scoped = current()
    if scoped is not None and check_execution(db, scoped)['state'] != 'ACTIVE':
        raise AdmissionConflict('unknown cancellation scope cannot admit child work')
    parent = parent if parent is not None else scoped
    if parent is not None:
        saved = _row(db, parent)
        if saved['state'] != 'ACTIVE' or parent.warehouse_id != warehouse_id or parent.actor != actor:
            raise AdmissionConflict('parent admission is not active for this actor and warehouse')
        if generation_id is not None and generation_id != parent.generation_id:
            raise AdmissionConflict('parent generation differs')
        if database_id is not None and database_id != parent.database_id:
            raise AdmissionConflict('parent database differs')
        check_execution(db, parent)
        generation_id = parent.generation_id
        database_id = parent.database_id
        if database_id is not None:
            database = db.execute('SELECT state FROM databases WHERE id=?', (database_id,)).fetchone()
            if database is None or (database['state'] != 'READY' and
                    not continuing_database(db, actor, database_id, generation_id)):
                raise AdmissionDenied('database is not ready')
        if state['state'] not in ('OPEN', 'DRAINING'):
            raise AdmissionDenied('warehouse configuration is being reconciled')
    else:
        if database_id is not None:
            database = db.execute('SELECT state FROM databases WHERE id=?', (database_id,)).fetchone()
            if database is None or database['state'] != 'READY':
                raise AdmissionDenied('database is not ready')
        check_open(db, warehouse_id)
        # Preserve legacy Job-vs-Job behavior. Mart physical roots alone add
        # cross-capacity exclusion, including independent P3 DataOps roots.
        if kind == 'mart' and (
                db.execute("SELECT 1 FROM warehouse_admissions WHERE kind IN ('mart','job','stage_scan','dataops') AND state IN ('ACTIVE','UNKNOWN')").fetchone()
                or db.execute("SELECT 1 FROM runs WHERE status IN ('queued','running','unknown')").fetchone()):
            raise AdmissionConflict('another physical operation is unresolved')
        if kind in ('job', 'stage_scan', 'dataops') and db.execute(
                "SELECT 1 FROM warehouse_admissions WHERE kind='mart' AND state IN ('ACTIVE','UNKNOWN')").fetchone():
            raise AdmissionConflict('a Mart physical operation is unresolved')
        if kind == 'stage_scan' and (
                db.execute("SELECT 1 FROM warehouse_admissions WHERE kind IN ('job','stage_scan') AND state IN ('ACTIVE','UNKNOWN')").fetchone()
                or db.execute("SELECT 1 FROM runs WHERE status IN ('queued','running','unknown')").fetchone()):
            raise AdmissionConflict('another Job or Stage scan is unresolved')
        if kind == 'job' and db.execute(
                "SELECT 1 FROM warehouse_admissions WHERE kind='stage_scan' AND state IN ('ACTIVE','UNKNOWN')").fetchone():
            raise AdmissionConflict('a Stage scan is unresolved')
        if (generation_id is not None and state['applied_generation_id'] is not None
                and generation_id != state['applied_generation_id']):
            raise AdmissionConflict('warehouse generation differs')
        if generation_id is None:
            generation_id = state['applied_generation_id']
    if get_for_subject(db, kind=kind, subject_id=subject_id, warehouse_id=warehouse_id) is not None:
        raise AdmissionConflict('subject already has an admission')
    handle = AdmissionHandle(str(uuid.uuid4()), warehouse_id, actor, kind, subject_id, generation_id, database_id)
    db.execute('''INSERT INTO warehouse_admissions
               (id,warehouse_id,actor,kind,subject_id,parent_id,generation_id,database_id,state,created_at,finished_at,evidence_ref)
               VALUES (?,?,?,?,?,?,?,?,'ACTIVE',?,NULL,NULL)''',
               (handle.id, warehouse_id, actor, kind, subject_id, parent.id if parent else None, generation_id, database_id, _now()))
    return handle


def continuing_database(db, actor, database_id, generation_id):
    """Only a currently accepted, persisted ACTIVE capability can drain a DB."""
    handle = _current.get()
    if (type(handle) is not AdmissionHandle or handle.actor != actor or
            handle.database_id != database_id or handle.generation_id != generation_id):
        return False
    database = db.execute('SELECT state FROM databases WHERE id=?', (database_id,)).fetchone()
    if database is None or database['state'] != 'retiring':
        return False
    try:
        return check_execution(db, handle)['state'] == 'ACTIVE'
    except (AdmissionConflict, AdmissionDenied):
        return False


@contextmanager
def accepted(handle, *, allow_unknown=False):
    with control_store.connect() as db:
        row = check_execution(db, handle)
        if row['execution_closure'] or (row['state'] != 'ACTIVE' and not (allow_unknown and row['state'] == 'UNKNOWN')):
            raise AdmissionConflict('admission is not active')
    token = _current.set(handle)
    try:
        yield handle
    finally:
        _current.reset(token)


def finish(db, handle, *, state, evidence_ref=None):
    _transaction(db)
    row = _row(db, handle)
    if row['execution_closure']:
        raise AdmissionConflict('execution is closed; outcome remains unknown')
    if state not in TERMINAL | {'UNKNOWN'}:
        raise AdmissionConflict('invalid admission outcome')
    if row['state'] == state:
        return
    if row['state'] in TERMINAL:
        raise AdmissionConflict('terminal admission is immutable')
    if row['state'] == 'UNKNOWN' and state in TERMINAL and not evidence_ref:
        raise AdmissionConflict('unknown outcome requires observed evidence')
    if state in TERMINAL and db.execute(
            """SELECT 1 FROM warehouse_admissions WHERE parent_id=? AND state IN ('ACTIVE','UNKNOWN')
               AND NOT (kind='query' AND execution_closure='FENCED_READ_ONLY')""",
            (handle.id,)).fetchone():
        raise AdmissionConflict('child work is unresolved')
    db.execute('UPDATE warehouse_admissions SET state=?,finished_at=?,evidence_ref=? WHERE id=?',
               (state, _now() if state in TERMINAL else None, evidence_ref, handle.id))


def blockers(db, warehouse_id='LOCAL'):
    return tuple(dict(row) for row in db.execute(
        """SELECT * FROM warehouse_admissions WHERE warehouse_id=? AND state IN ('ACTIVE','UNKNOWN')
           AND NOT (kind='query' AND execution_closure='FENCED_READ_ONLY') ORDER BY created_at,id""",
        (warehouse_id,)))


def begin_drain(db, *, warehouse_id='LOCAL', operation_id, expected_revision=None):
    _transaction(db)
    state = _ensure(db, warehouse_id)
    if (state['operation_id'] not in (None, operation_id) or state['state'] not in ('OPEN', 'DRAINING')
            or expected_revision is not None and state['revision'] != expected_revision):
        raise AdmissionConflict('warehouse transition conflicts')
    db.execute("UPDATE warehouse_controls SET state='DRAINING',operation_id=?,revision=revision+1 WHERE warehouse_id=?",
               (operation_id, warehouse_id))
    return control(db, warehouse_id)


def reopen(db, *, warehouse_id='LOCAL', operation_id, expected_revision=None):
    _transaction(db)
    state = control(db, warehouse_id)
    if (state['operation_id'] != operation_id or state['state'] not in ('OPEN', 'DRAINING')
            or expected_revision is not None and state['revision'] != expected_revision):
        raise AdmissionConflict('warehouse transition conflicts')
    db.execute("UPDATE warehouse_controls SET state='OPEN',operation_id=NULL,revision=revision+1 WHERE warehouse_id=?",
               (warehouse_id,))
    return control(db, warehouse_id)


@contextmanager
def lifecycle_gate(*, exclusive=False, deadline=None):
    """Same file and nesting rules as the existing Warehouse gate; optional deadline."""
    held = getattr(_held, 'mode', None)
    if held:
        if exclusive and held != 'exclusive':
            raise AdmissionConflict('cannot upgrade an active warehouse request')
        yield
        return
    path = control_store.root() / 'warehouse.lock'
    try:
        lock = path.open('a')
    except OSError as error:
        if error.errno not in (None, errno.EACCES, errno.EPERM, errno.EROFS):
            raise
        lock = path.open('r')
    with lock:
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        if deadline is None:
            fcntl.flock(lock, mode)
        else:
            while True:
                try:
                    fcntl.flock(lock, mode | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise GateTimeout('warehouse gate deadline exceeded') from None
                    time.sleep(min(.02, remaining))
        _held.mode = 'exclusive' if exclusive else 'shared'
        try:
            yield
        finally:
            _held.mode = None
            fcntl.flock(lock, fcntl.LOCK_UN)
