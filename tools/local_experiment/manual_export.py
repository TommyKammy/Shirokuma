"""Backend-only, owner/context-bound snapshot Table export.

export_table returns Execution.inspect(): {id,state,committed,result,
cleanup_error,queries,replayed}. result is {export_id} from durable intent and
{export_id,receipt} on completion. committed stays false: SQL is read-only.
get_export returns the completed receipt; read_export returns
{receipt: <same metadata>, content: bytes}. Neither returns a filesystem path.

Receipt keys: export_id, operation_id, owner, context, source (ref, table_uuid,
snapshot decimal string, schema_id, columns [{id,name,type}], schema_fingerprint),
format, encoding, newline, null_convention, quoting, expected_count,
written_count, byte_count, sha256. Supported Iceberg types: string, boolean,
int, long, float, double, decimal(p,s) (p <= 38), date and naive timestamp.
Complex, binary, time and timezone types are rejected before SQL.

Source schema_id/columns describe the captured snapshot's schema, including
its original names before any later ADD/RENAME. Separate current_schema_id and
current_schema_fingerprint guards reject DDL during export or retrieval while
allowing a new current head from append. Missing snapshot schema fails closed.
CSV encodes a one-column NULL record as a bare LF (one unquoted empty field
under the declared one-column header); an empty string is a quoted empty field.
"""
import csv
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
import re
import struct
import tempfile
import uuid

import control_store
import data_management as registry
import dataops_service as ds
import query_service as queries
from query_context import QueryError
import warehouse_admission as admission


def _directory():
    # Runtime configuration sets control_store's profile root before use.
    path = control_store.root().resolve() / 'table-exports'
    path.mkdir(mode=0o700, exist_ok=True)
    path.chmod(0o700)
    return path


def _profile_ref():
    return hashlib.sha256(str(control_store.root().resolve()).encode()).hexdigest()


def _supported(kind):
    if isinstance(kind, str) and kind in ('string', 'boolean', 'int', 'long', 'float', 'double', 'date', 'timestamp'):
        return
    match = re.fullmatch(r'decimal\(([1-9][0-9]?),\s*([0-9]+)\)', kind) if isinstance(kind, str) else None
    if not match or not 0 <= int(match[2]) <= int(match[1]) <= 38:
        raise QueryError('EXPORT_TYPE_NOT_SUPPORTED', 400)


def _token(value, kind):
    """Validate CAST text and produce an exact JSON token, never binary float."""
    if value is None:
        return 'null'
    if type(value) is not str:
        raise QueryError('EXPORT_VALUE_INVALID', 502)
    try:
        if kind == 'boolean':
            if value not in ('true', 'false'):
                raise ValueError()
            return value
        if kind in ('int', 'long'):
            if not re.fullmatch(r'-?(0|[1-9][0-9]*)', value):
                raise ValueError()
            number = int(value)
            bits = 32 if kind == 'int' else 64
            if not -(2 ** (bits - 1)) <= number < 2 ** (bits - 1):
                raise ValueError()
            return value
        if kind in ('float', 'double') or kind.startswith('decimal('):
            if not re.fullmatch(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?', value):
                raise ValueError()
            number = Decimal(value)
            if not number.is_finite():
                raise ValueError()
            if kind in ('float', 'double'):
                representable = float(value)
                if kind == 'float':
                    representable = struct.unpack('!f', struct.pack('!f', representable))[0]
                if not math.isfinite(representable) or number != 0 and representable == 0:
                    raise ValueError()
            if kind.startswith('decimal('):
                precision, scale = map(int, re.findall('[0-9]+', kind))
                # Work with the tuple, without Decimal context rounding.
                digits = len(number.as_tuple().digits)
                exponent = number.as_tuple().exponent
                if exponent < -scale or max(0, digits + exponent) > precision - scale:
                    raise ValueError()
            return value
        if kind == 'date':
            if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
                raise ValueError()
            date.fromisoformat(value)
        if kind == 'timestamp':
            if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?', value):
                raise ValueError()
            if datetime.fromisoformat(value).tzinfo is not None:
                raise ValueError()
        return json.dumps(value, ensure_ascii=False)
    except (ValueError, InvalidOperation, OverflowError, struct.error):
        raise QueryError('EXPORT_VALUE_INVALID', 502) from None


def _write(stream, columns, rows, format):
    if format == 'csv':
        # Look up lazily so importing this module remains Python 3.9 compatible.
        writer = csv.writer(stream, quoting=csv.QUOTE_NOTNULL, lineterminator='\n')
        writer.writerow([c['name'] for c in columns])
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != len(columns):
            raise QueryError('EXPORT_ROW_SHAPE_MISMATCH', 502)
        tokens = [_token(v, c['type']) for v, c in zip(row, columns)]
        if format == 'csv':
            # Python 3.12's writer rejects an unquoted single empty field.
            # The header supplies arity, so a bare record preserves NULL while
            # the normal writer emits quoted empty text as "".
            if len(row) == 1 and row[0] is None:
                stream.write('\n')
            else:
                writer.writerow(row)
        else:
            stream.write('{' + ','.join(json.dumps(c['name'], ensure_ascii=False) + ':' + v
                                       for c, v in zip(columns, tokens)) + '}\n')


def _settle(execution, context, error, cleanup_error=None):
    # Uncertain responses remain observation-only; DELETE cannot prove that a
    # query whose FINISHED response was lost was actually canceled.
    with control_store.connect() as db:
        row = db.execute('SELECT state FROM dataops_operations WHERE id=?', (execution.id,)).fetchone()
        if row['state'] != 'ACTIVE':
            return
        ids = [r[0] for r in db.execute('''SELECT q.id FROM query_records q JOIN dataops_queries d
            ON q.id=d.query_id WHERE d.operation_id=? AND q.state IN ('QUEUED','RUNNING')''', (execution.id,))]
    for query_id in ids:
        pin = queries._pins.get(query_id)
        if pin is not None:
            # Serialize with page reception before rechecking uncertainty. The
            # cancellation itself retains the original private credential pin.
            with pin.lock:
                with control_store.connect() as db:
                    parent_state = db.execute('SELECT state FROM dataops_operations WHERE id=?', (execution.id,)).fetchone()[0]
                    query_state = db.execute('SELECT state FROM query_records WHERE id=?', (query_id,)).fetchone()[0]
                if parent_state == 'ACTIVE' and query_state in ('QUEUED', 'RUNNING'):
                    queries._best_cancel(query_id)
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT state FROM dataops_operations WHERE id=?', (execution.id,)).fetchone()
        if row['state'] != 'ACTIVE':
            return
        states = [r[0] for r in db.execute('''SELECT q.state FROM query_records q JOIN dataops_queries d
            ON q.id=d.query_id WHERE d.operation_id=?''', (execution.id,))]
        state = 'FAILED' if all(s in ('FINISHED', 'FAILED', 'CANCELED') for s in states) else 'UNKNOWN'
        parent = admission.get_for_subject(db, kind='dataops', subject_id=execution.id, warehouse_id=context.warehouse_id)
        admission.finish(db, parent, state=state)
        db.execute('UPDATE dataops_operations SET state=?,cleanup_error=? WHERE id=?',
                   (state, (getattr(error, 'code', type(error).__name__) +
                            ('; EXPORT_FILE_CLEANUP_FAILED' if cleanup_error else '')), execution.id))
        db.execute("UPDATE export_artifacts SET state='FAILED' WHERE operation_id=? AND state='INTENT'", (execution.id,))
    if execution.id in ds._live:
        ds._live[execution.id][2].close()


def _publish(execution, receipt, *, _defer_finalization=False):
    _, parent, owner, lock = execution._active()
    with lock:
        execution._active()
        execution._successful_work()
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            changed = db.execute("UPDATE dataops_operations SET state=?,result=? WHERE id=? AND state='ACTIVE'",
                ('ACTIVE' if _defer_finalization else 'FINISHED',
                 ds._json(dict(export_id=receipt['export_id'], receipt=receipt)), execution.id))
            if changed.rowcount != 1:
                raise QueryError('ADMISSION_CONFLICT', 409)
            changed = db.execute("UPDATE export_artifacts SET state='COMPLETE',record=? WHERE operation_id=? AND state='INTENT'",
                       (ds._json(receipt), execution.id))
            if changed.rowcount != 1:
                raise QueryError('EXPORT_NOT_COMPLETE', 409)
            if not _defer_finalization:
                admission.finish(db, parent, state='FINISHED')
        if not _defer_finalization:
            owner.close()


def export_table(principal, context, *, key, source, format='csv',
                 _execution=None, _defer_finalization=False):
    """Export one snapshot, then authorize the public observation after settlement."""
    observation = _export_table(principal, context, key=key, source=source, format=format,
        _execution=_execution, _defer_finalization=_defer_finalization)
    if _execution is None:
        ds._validated_context(principal, context)
    return observation


def _export_table(principal, context, *, key, source, format='csv',
                  _execution=None, _defer_finalization=False):
    """Export every column and row at one captured main snapshot; see module API."""
    if format not in ('csv', 'jsonl'):
        raise QueryError('EXPORT_FORMAT_NOT_SUPPORTED', 400)
    if type(source) is not registry.ResourceRef:
        raise ValueError('ResourceRef required')
    execution = _execution or ds.begin(principal, context, route='manual-table-export', idempotency_key=key,
                                       payload=dict(source=asdict(source), format=format))
    if execution.replayed:
        return execution.inspect()
    with ds._accepted(execution):
        if _execution is not None:
            if execution.principal != principal or execution._context() != context:
                raise QueryError('CONTEXT_MISMATCH', 409)
            execution._active()
        temporary = final = None
        finalization_lock = None
        try:
            export_id = str(uuid.uuid4())
            relative_name = export_id + '.' + format
            receipt = dict(export_id=export_id, operation_id=execution.id, owner=principal.principal_id,
                context=asdict(context), source=None, format=format, encoding='UTF-8', newline='LF',
                quoting='QUOTE_NOTNULL' if format == 'csv' else None,
                null_convention='unquoted empty; empty string is quoted empty' if format == 'csv' else 'JSON null',
                expected_count=None, written_count=None, byte_count=None, sha256=None)
            with control_store.connect() as db:
                db.execute('INSERT INTO export_artifacts VALUES (?,?,?,?,?,?,?,?)',
                    (export_id, execution.id, principal.principal_id, ds._json(asdict(context)),
                     ds._json(receipt), 'INTENT', relative_name, _profile_ref()))
                db.execute('UPDATE dataops_operations SET result=? WHERE id=?',
                           (ds._json(dict(export_id=export_id)), execution.id))
            measurement = execution.export_measure(source)
            for column in measurement['columns']:
                _supported(column['type'])
            receipt['source'] = measurement
            with control_store.connect() as db:
                db.execute('UPDATE export_artifacts SET record=? WHERE export_id=?', (ds._json(receipt), export_id))
            counted = execution.export_query(source, measurement, count=True)['data']
            if (len(counted) != 1 or len(counted[0]) != 1 or
                    type(counted[0][0]) is not int or counted[0][0] < 0):
                raise QueryError('EXPORT_COUNT_INVALID', 502)
            receipt['expected_count'] = counted[0][0]
            rows = execution.export_query(source, measurement)['data']
            if len(rows) != receipt['expected_count']:
                raise QueryError('EXPORT_COUNT_MISMATCH', 502)
            # Cancel may durably mark CANCELING immediately, but cannot release the
            # parent admission while this bounded file work remains in progress.
            finalization_lock = execution._active()[3]
            finalization_lock.acquire()
            execution._active()
            directory = _directory()
            descriptor, name = tempfile.mkstemp(prefix=export_id + '-', suffix='.tmp', dir=directory)
            temporary = directory / os.path.basename(name)
            with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as stream:
                _write(stream, measurement['columns'], rows, format)
                stream.flush()
                os.fsync(stream.fileno())
            content = temporary.read_bytes()
            receipt.update(written_count=len(rows), byte_count=len(content), sha256=hashlib.sha256(content).hexdigest())
            execution.export_measure(source, measurement)
            final = directory / relative_name
            os.replace(temporary, final)
            temporary = None
            descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            execution.export_measure(source, measurement)
            if _defer_finalization:
                _publish(execution, receipt, _defer_finalization=True)
            else:
                _publish(execution, receipt)
            return execution._inspect()
        except Exception as error:
            # Delete only files allocated by this invocation. A stopped process may
            # leave unpublished evidence, which the COMPLETE ledger never authorizes.
            cleanup_error = None
            try:
                for path in (temporary, final):
                    if path is not None:
                        try:
                            with control_store.connect() as db:
                                complete = db.execute("SELECT 1 FROM export_artifacts WHERE operation_id=? AND state='COMPLETE'", (execution.id,)).fetchone()
                            if not complete:
                                path.unlink(missing_ok=True)
                        except OSError as failure:
                            cleanup_error = failure
            finally:
                _settle(execution, context, error, cleanup_error)
            raise
        finally:
            if finalization_lock is not None:
                finalization_lock.release()


def _authorized(principal, context, export_id, *, _execution=None):
    ds._validated_context(principal, context)
    with control_store.connect() as db:
        row = db.execute('SELECT * FROM export_artifacts WHERE export_id=?', (export_id,)).fetchone()
    if (row is None or row['owner'] != principal.principal_id or
            row['context'] != ds._json(asdict(context)) or row['state'] != 'COMPLETE' or
            row['profile_ref'] != _profile_ref()):
        raise QueryError('EXPORT_NOT_FOUND', 404)
    if _execution is not None and (type(_execution) is not ds.Execution or
            _execution.principal != principal or _execution.id != row['operation_id']):
        raise QueryError('EXPORT_NOT_FOUND', 404)
    execution = _execution or ds.Execution(principal, row['operation_id'])
    receipt = json.loads(row['record'])
    operation = execution._inspect() if _execution is not None else execution.inspect()
    states = ('ACTIVE', 'FINISHED') if _execution is not None else ('FINISHED',)
    if operation['state'] not in states or operation['result'] != dict(export_id=export_id, receipt=receipt):
        raise QueryError('EXPORT_NOT_COMPLETE', 409)
    execution.export_measure(registry.ResourceRef(**receipt['source']['ref']), receipt['source'])
    expected_name = str(uuid.UUID(export_id)) + '.' + receipt['format']
    if row['relative_name'] != expected_name or receipt['format'] not in ('csv', 'jsonl'):
        raise QueryError('EXPORT_INTEGRITY_FAILED', 409)
    return receipt, _directory() / expected_name


def _read_complete(principal, context, *, export_id, _execution):
    receipt, path = _authorized(principal, context, export_id, _execution=_execution)
    try:
        content = path.read_bytes()
    except OSError:
        raise QueryError('EXPORT_INTEGRITY_FAILED', 409) from None
    if len(content) != receipt['byte_count'] or hashlib.sha256(content).hexdigest() != receipt['sha256']:
        raise QueryError('EXPORT_INTEGRITY_FAILED', 409)
    _authorized(principal, context, export_id, _execution=_execution)
    return dict(receipt=receipt, content=content)


def read_export(principal, context, *, export_id):
    """Return {receipt: completed metadata, content: bytes}, after integrity checks."""
    receipt, path = _authorized(principal, context, export_id)
    try:
        content = path.read_bytes()
    except OSError:
        raise QueryError('EXPORT_INTEGRITY_FAILED', 409) from None
    if len(content) != receipt['byte_count'] or hashlib.sha256(content).hexdigest() != receipt['sha256']:
        raise QueryError('EXPORT_INTEGRITY_FAILED', 409)
    _authorized(principal, context, export_id)
    return dict(receipt=receipt, content=content)


def get_export(principal, context, *, export_id):
    """Return completed authorized metadata only; also verify the stored bytes."""
    return read_export(principal, context, export_id=export_id)['receipt']
