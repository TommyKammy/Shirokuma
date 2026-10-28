"""Owner-bound query pages with process-private, captured backend credentials."""
import atexit
import base64
import fcntl
import hashlib
from copy import deepcopy
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
import http.client
import json
import os
import secrets
import re
import threading
from urllib.parse import urlsplit
import uuid
from typing import Optional

import control_store
import data_management as registry
import request_auth
import trino_auth
import warehouse_admission as admission
from query_context import QueryError, ResolvedQueryContext, resolve_context, _resolve_context, revalidate_admission


@dataclass(repr=False)
class _Pin:
    context: ResolvedQueryContext
    origin: str
    headers: dict = field(repr=False)
    tls: object = field(repr=False)
    admission: object = field(default=None, repr=False)
    owner: object = field(default=None, repr=False)
    tokens: dict = field(default_factory=dict)
    cancel_urls: list = field(default_factory=list)
    max_rows: Optional[int] = 1000
    rows: int = 0
    columns: list = field(default_factory=list)
    lock: object = field(default_factory=threading.RLock, repr=False)
    responses: dict = field(default_factory=dict, repr=False)
    failures: dict = field(default_factory=dict, repr=False)


_pins = {}
_lifetime = ExitStack()
_lock = threading.RLock()
_origin = None
atexit.register(_lifetime.close)


def _acquire_query_owner(query_id):
    """A process-private lease, with a stable inode and no PID/TTL inference."""
    try:
        directory = control_store.root() / 'runtime' / 'query-owners'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (hashlib.sha256(query_id.encode()).hexdigest() + '.lock')
        owner = os.fdopen(os.open(path, os.O_CREAT | os.O_RDWR, 0o600), 'r+b')
        try:
            os.fchmod(owner.fileno(), 0o600)
            fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            owner.close()
            raise
        return owner
    except BlockingIOError:
        raise QueryError('CONTEXT_MISMATCH', 409) from None
    except OSError:
        raise QueryError('QUERY_CONTEXT_LOST', 409) from None


def _close_query_owner(pin):
    if pin is not None and pin.owner is not None:
        owner, pin.owner = pin.owner, None
        owner.close()


def _transport(url, method, pin, body=None):
    try:
        parsed, trusted = urlsplit(url), urlsplit(pin.origin)
        parsed.port
        trusted.port
    except (ValueError, TypeError):
        raise QueryError('UNKNOWN_CONTEXT', 400) from None
    if any(c in url for c in '\r\n\x00'):
        raise QueryError('UNKNOWN_CONTEXT', 400)
    if (parsed.scheme, parsed.hostname, parsed.port) != (trusted.scheme, trusted.hostname, trusted.port) or parsed.username or parsed.password or parsed.fragment:
        raise QueryError('UNKNOWN_CONTEXT', 400)
    initial = method == 'POST' and parsed.path == '/v1/statement' and not parsed.query and body is not None
    if not initial and not any(target == url and method in methods for target, methods in pin.tokens.values()):
        raise QueryError('QUERY_NOT_FOUND', 404)
    import warehouse_resident as wr
    with wr.warehouse_activity():
        # accepted() precedes the shared gate: a queued old POST must check
        # again after an exclusive recovery fence has released that gate.
        with control_store.connect() as db:
            if pin.admission is not None:
                if admission._row(db, pin.admission)['execution_closure']:
                    raise QueryError('EXECUTION_CLOSED', 409)
                admission.check_execution(db, pin.admission)
        connection = (http.client.HTTPSConnection(parsed.hostname, parsed.port or 443,
            context=pin.tls, timeout=30) if parsed.scheme == 'https' else
            http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=30))
        try:
            connection.request(method, parsed.path + ('?' + parsed.query if parsed.query else ''),
                               body=body, headers=pin.headers)
            response = connection.getresponse()
            payload = response.read()
            if not 200 <= response.status < 300:
                raise QueryError('QUERY_BACKEND_FAILED', 502)
            return json.loads(payload) if payload else {}
        except QueryError:
            raise
        except Exception:
            raise QueryError('QUERY_BACKEND_FAILED', 502) from None
        finally:
            connection.close()


def _registered(pin, url, methods, backend_id):
    if not isinstance(url, str):
        raise QueryError('UNKNOWN_CONTEXT', 400)
    try:
        parsed, trusted = urlsplit(url), urlsplit(pin.origin)
        parsed.port
        trusted.port
    except ValueError:
        raise QueryError('UNKNOWN_CONTEXT', 400) from None
    if any(c in url for c in '\r\n\x00'):
        raise QueryError('UNKNOWN_CONTEXT', 400)
    if ((parsed.scheme, parsed.hostname, parsed.port) != (trusted.scheme, trusted.hostname, trusted.port)
            or parsed.username or parsed.password or parsed.fragment or
            not parsed.path.startswith('/v1/statement/') or backend_id not in parsed.path.split('/')):
        raise QueryError('UNKNOWN_CONTEXT', 400)
    token = secrets.token_urlsafe(32)
    pin.tokens[token] = (url, frozenset(methods))
    if 'GET' in methods and 'DELETE' in methods and url not in pin.cancel_urls:
        pin.cancel_urls.append(url)
    return token


def _row(query_id):
    if not isinstance(query_id, str) or not query_id:
        raise QueryError('QUERY_NOT_FOUND', 404)
    with control_store.connect() as db:
        row = db.execute('''SELECT q.*, e.*,
            COALESCE((SELECT execution_closure FROM warehouse_admissions a
                WHERE a.kind='query' AND a.subject_id=q.id),'') AS execution_closure
            FROM query_records q JOIN query_execution_contexts e
            ON q.id=e.query_id WHERE q.id=?''', (query_id,)).fetchone()
        return dict(row) if row else None


def _retirement_only_denial(principal, context, *, backend_username=None):
    """Classify failed authorization for cleanup only; never authorize a result.

    Callers supply the exact persisted context after checking its owner. UNKNOWN
    work needs this check too, so it must not use an accepted admission scope.
    """
    with control_store.connect() as db:
        db.execute('BEGIN')
        database = registry.get_database(db, context.database_id)
        if database is None or database.state != 'retiring':
            return False
        try:
            decision = request_auth._authorize_database_current(principal, context.database_id,
                context.selected_role, expected_generation_id=context.generation_id, db=db)
        except PermissionError:
            return False
        if backend_username is not None:
            import logical_registry
            if logical_registry.creation_mode(db, context.database_id) == 'logical_v1':
                try:
                    logical = logical_registry.get_binding(db, context.database_id)
                    identity = logical_registry._identity_for_binding(db,
                        dict(logical, generation_id=context.generation_id), principal.principal_id,
                        context.database_id, context.selected_role, decision.grant_revision, decision.platform_admin)
                    if identity['backend_username'] != backend_username:
                        return False
                except ValueError:
                    return False
        binding = registry.get_binding(db, context.database_id)
        schema = registry.get_schema(db, context.schema_id)
        generation = registry.get_generation(db, context.generation_id)
        if (binding is None or schema is None or generation is None or
                schema.database_id != context.database_id or schema.sql_name != context.sql_schema or
                generation.warehouse_id != context.warehouse_id or
                binding.desired_revision != context.binding_revision or
                binding.applied_revision != context.binding_revision or
                binding.applied_generation_id != context.generation_id):
            return False
        if (context.selected_role != 'admin' and
                (schema.purpose != schema.sql_name or schema.sql_name not in ('raw', 'sandbox', 'staging', 'marts') or
                 context.selected_role == 'bi_viewer' and schema.sql_name != 'marts')):
            return False
        return (decision.principal_id == principal.principal_id and
                decision.database_id == context.database_id and decision.sql_catalog == context.sql_catalog and
                decision.selected_role == context.selected_role and decision.generation_id == context.generation_id and
                decision.grant_revision == context.grant_revision)


def _owned(principal, query_id, selected_role, *, grants=True, cleanup=False):
    row = _row(query_id)
    if type(principal) is not request_auth.AuthenticatedPrincipal or row is None or row['actor'] != principal.principal_id:
        raise QueryError('QUERY_NOT_FOUND', 404)
    if selected_role != row['selected_role']:
        raise QueryError('CONTEXT_MISMATCH', 409)
    try:
        account = request_auth._principal_accounts().get(principal.principal_id)
    except PermissionError:
        if cleanup:
            _best_cancel(query_id)
        raise QueryError('DB_ACCESS_REVOKED', 403) from None
    if account is None:
        if cleanup:
            _best_cancel(query_id)
        raise QueryError('DB_ACCESS_REVOKED', 403)
    if grants:
        try:
            context = resolve_context(principal, row['database_id'], row['schema_id'], selected_role,
                warehouse_id=row['warehouse_id'], expected_binding_revision=row['binding_revision'])
            if (context.generation_id != row['generation_id'] or context.sql_catalog != row['sql_catalog']
                    or context.sql_schema != row['sql_schema']
                    or context.grant_revision != row['grant_revision']):
                raise QueryError('CONTEXT_MISMATCH', 409)
            import logical_registry
            with control_store.connect() as db:
                db.execute('BEGIN')
                if logical_registry.creation_mode(db, context.database_id) == 'logical_v1':
                    decision = request_auth.authorize_database(principal, context.database_id, selected_role,
                        expected_generation_id=context.generation_id, db=db)
                    try:
                        identity = logical_registry.resolve_identity(db, principal.principal_id, context.database_id,
                            selected_role, context.grant_revision, decision.platform_admin)
                    except ValueError:
                        raise PermissionError('Query identity is no longer current') from None
                    if identity['backend_username'] != row['backend_username']:
                        raise PermissionError('Query identity is no longer current')
        except (QueryError, PermissionError):
            frozen = ResolvedQueryContext(**_metadata(row))
            if cleanup and not _retirement_only_denial(principal, frozen, backend_username=row['backend_username']):
                _best_cancel(query_id)
            raise QueryError('DB_ACCESS_REVOKED', 403) from None
    return row


def _state(query_id, state, backend_id=None, evidence_ref=None):
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if admission.query_closure(db, query_id):
            raise QueryError('EXECUTION_CLOSED', 409)
        registry.set_query_state(db, query_id, state)
        if backend_id is not None:
            try:
                registry.set_query_backend_id(db, query_id, backend_id)
            except registry.RevisionConflict:
                raise QueryError('CONTEXT_MISMATCH', 409)
        handle = _pins[query_id].admission
        if state in _TERMINAL or state == 'UNKNOWN':
            admission.finish(db, handle, state=state, evidence_ref=evidence_ref)
        child = db.execute('SELECT operation_id,commit_result FROM dataops_queries WHERE query_id=?', (query_id,)).fetchone()
        if child is not None and state == 'FINISHED' and child['commit_result'] is not None:
            db.execute('UPDATE dataops_operations SET committed=1,result=? WHERE id=?',
                       (child['commit_result'], child['operation_id']))
        if child is not None and state == 'UNKNOWN':
            db.execute("UPDATE dataops_operations SET state='UNKNOWN' WHERE id=?", (child['operation_id'],))
            parent = admission.get_for_subject(db, kind='dataops', subject_id=child['operation_id'],
                                               warehouse_id=handle.warehouse_id)
            admission.finish(db, parent, state='UNKNOWN')
    if state in _TERMINAL or state == 'UNKNOWN':
        _close_query_owner(_pins.get(query_id))


def _best_cancel(query_id):
    pin = _pins.get(query_id)
    if pin is not None:
        with pin.lock:
            try:
                _cancel_locked(query_id, pin, _row(query_id))
            except QueryError:
                pass


_TERMINAL = frozenset(('FINISHED', 'FAILED', 'CANCELED'))


def _cancel_locked(query_id, pin, row, url=None):
    if row['execution_closure']:
        return _reconcile_result(row)
    if row['state'] in _TERMINAL:
        _close_query_owner(pin)
        return {'id': query_id, 'state': row['state']}
    target = url or (pin.cancel_urls[-1] if pin.cancel_urls else None)
    if target is None:
        _state(query_id, 'UNKNOWN')
        return {'id': query_id, 'state': 'UNKNOWN'}
    try:
        with admission.accepted(pin.admission, allow_unknown=True):
            acknowledgment = _transport(target, 'DELETE', pin)
        if not isinstance(acknowledgment, dict):
            raise QueryError('QUERY_BACKEND_FAILED', 502)
    except (admission.AdmissionDenied, admission.AdmissionConflict, admission.GateTimeout) as error:
        raise QueryError(error.code, error.status) from None
    except Exception:
        _state(query_id, 'UNKNOWN')
        raise QueryError('QUERY_BACKEND_FAILED', 502) from None
    _state(query_id, 'CANCELED', evidence_ref=secrets.token_urlsafe(32))
    return {'id': query_id, 'state': 'CANCELED'}


def _metadata(row):
    return {key: row[key] for key in ('database_id', 'schema_id', 'warehouse_id', 'sql_catalog',
        'sql_schema', 'selected_role', 'binding_revision', 'generation_id', 'grant_revision')}


def _page(query_id, page):
    pin = _pins[query_id]
    row = _row(query_id)
    if row['execution_closure']:
        raise QueryError('EXECUTION_CLOSED', 409)
    if not isinstance(page, dict):
        raise QueryError('QUERY_BACKEND_FAILED', 502)
    backend_id = page.get('id')
    import re
    if not isinstance(backend_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', backend_id):
        raise QueryError('QUERY_BACKEND_FAILED', 502)
    if row['backend_query_id'] is not None and row['backend_query_id'] != backend_id:
        raise QueryError('CONTEXT_MISMATCH', 409)
    next_uri = None
    if page.get('nextUri'):
        token = _registered(pin, page['nextUri'], ('GET', 'DELETE'), backend_id)
        next_uri = '/api/v1/queries/' + query_id + '/pages/' + token
    if page.get('partialCancelUri'):
        _registered(pin, page['partialCancelUri'], ('DELETE',), backend_id)
    stats = page.get('stats', {})
    if not isinstance(stats, dict):
        raise QueryError('QUERY_BACKEND_FAILED', 502)
    if 'columns' in page:
        if not isinstance(page['columns'], list):
            raise QueryError('QUERY_BACKEND_FAILED', 502)
        pin.columns = deepcopy(page['columns'])
    state = str(stats.get('state', 'RUNNING' if next_uri else 'FINISHED')).upper()
    if page.get('error'):
        state = 'FAILED'
    elif not next_uri:
        state = 'FINISHED'
    elif state not in ('QUEUED', 'RUNNING'):
        state = 'RUNNING'
    data = page.get('data', [])
    if not isinstance(data, list):
        raise QueryError('QUERY_BACKEND_FAILED', 502)
    truncated = False
    if pin.max_rows is not None:
        remaining = max(0, pin.max_rows - pin.rows)
        truncated = len(data) > remaining or len(data) >= remaining and bool(next_uri)
        data = data[:remaining]
    pin.rows += len(data)
    if truncated and next_uri and state not in _TERMINAL:
        _state(query_id, row['state'], backend_id)
        try:
            state = _cancel_locked(query_id, pin, _row(query_id))['state']
        except QueryError:
            state = 'UNKNOWN'
        next_uri = None
    else:
        _state(query_id, state, backend_id)
    result = dict(id=query_id, backend_query_id=backend_id, request_id=row['request_id'],
        worksheet_id=row['worksheet_id'], context=_metadata(row), columns=deepcopy(pin.columns),
        data=data, stats={key: value for key, value in stats.items() if key in {
            'state', 'queued', 'scheduled', 'nodes', 'totalSplits', 'queuedSplits', 'runningSplits',
            'completedSplits', 'cpuTimeMillis', 'wallTimeMillis', 'queuedTimeMillis', 'elapsedTimeMillis',
            'processedRows', 'processedBytes', 'peakMemoryBytes', 'spilledBytes', 'progressPercentage'}
            and type(value) in (str, bool, int, float)}, state=state, next_uri=next_uri,
        cancel_uri='/api/v1/queries/' + query_id + '/cancel')
    if page.get('error'):
        result['error'] = {'code': 'QUERY_FAILED', 'message': 'Query execution failed'}
    if truncated:
        result['truncated'] = True
    result['stats']['state'] = state
    return result


def _backend_statement(sql):
    """Remove one terminal delimiter, preserving all quoted text and comments.

    This is a lexical single-statement check, not the public SQL policy: private
    DataOps cleanup statements have their own constrained authorization path.
    """
    if not isinstance(sql, str):
        raise ValueError('SQL must be a string')
    index, delimiter, has_statement = 0, None, False
    while index < len(sql):
        char = sql[index]
        if char.isspace():
            index += 1
            continue
        if sql.startswith('--', index):
            index += 2
            while index < len(sql) and sql[index] not in '\r\n':
                index += 1
            continue
        if sql.startswith('/*', index):
            depth = 1
            index += 2
            while index < len(sql) and depth:
                if sql.startswith('/*', index):
                    depth += 1
                    index += 2
                elif sql.startswith('*/', index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            if depth:
                raise ValueError('unterminated SQL comment')
            continue
        if delimiter is not None:
            raise ValueError('multiple statements are not allowed')
        if char == ';':
            delimiter = index
            index += 1
            continue
        has_statement = True
        index += 1
        if char in ("'", '"'):
            while index < len(sql):
                if sql[index] == char:
                    index += 1
                    if index < len(sql) and sql[index] == char:
                        index += 1
                        continue
                    break
                index += 1
            else:
                raise ValueError('unterminated SQL quote')
    if not has_statement:
        raise ValueError('empty SQL statement')
    return sql if delimiter is None else sql[:delimiter] + sql[delimiter + 1:]


def submit(principal, context, sql, *, mode, max_rows, request_id, worksheet_id):
    global _origin
    import warehouse_resident as wr
    if not isinstance(context, ResolvedQueryContext):
        raise QueryError('UNKNOWN_CONTEXT', 400)
    if (not isinstance(sql, str) or not sql.strip() or
            max_rows is not None and (type(max_rows) is not int or max_rows < 1)):
        raise QueryError('UNKNOWN_CONTEXT', 400)
    for value in (request_id, worksheet_id):
        if value is not None and (not isinstance(value, str) or not value or len(value) > 256):
            raise QueryError('UNKNOWN_CONTEXT', 400)
    try:
        decision, headers = trino_auth.prepare_backend_v2(principal=principal,
            database_id=context.database_id, selected_role=context.selected_role)
        current = _resolve_context(principal, context.database_id, context.schema_id, context.selected_role,
            warehouse_id=context.warehouse_id, expected_binding_revision=context.binding_revision, decision=decision)
        if current != context:
            raise QueryError('CONTEXT_MISMATCH', 409)
        try:
            backend_sql = _backend_statement(sql)
            wr.check_sql_policy(backend_sql, mode, context.selected_role, context=context,
                _platform_authorized=decision.platform_admin)
        except ValueError:
            raise QueryError('SQL_PERMISSION_DENIED', 403) from None
        tls = trino_auth.backend_ssl_context()
    except QueryError:
        raise
    except (RuntimeError, PermissionError):
        raise QueryError('FEATURE_UNAVAILABLE', 501) from None
    headers.update({'X-Trino-Catalog': context.sql_catalog, 'X-Trino-Schema': context.sql_schema,
                    'Accept-Encoding': 'identity'})
    query_id, credential_ref = str(uuid.uuid4()), secrets.token_urlsafe(32)
    headers = {key: value for key, value in headers.items() if key.lower() != 'x-trino-source'}
    headers['X-Trino-Source'] = 'shirokuma-query-' + query_id
    owner = _acquire_query_owner(query_id)
    try:
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            revalidate_admission(db, principal, context, backend_username=headers['X-Trino-User'])
            handle = admission.admit(db, warehouse_id=context.warehouse_id,
                actor=principal.principal_id, kind='query', subject_id=query_id,
                generation_id=context.generation_id, database_id=context.database_id)
            registry.record_query(db, registry.QueryRecord(query_id, principal.principal_id,
                registry.QueryContext(context.database_id, context.schema_id, context.warehouse_id,
                                      context.generation_id), 'QUEUED', datetime.now(timezone.utc).isoformat()))
            registry.record_query_execution(db, registry.QueryExecutionContext(
                 query_id, context.selected_role, mode, context.sql_catalog, context.sql_schema,
                 context.binding_revision, context.grant_revision, headers['X-Trino-User'],
                 credential_ref, None, request_id, worksheet_id, sql))
        _pins[query_id] = _Pin(context, '', headers, tls, admission=handle, owner=owner, max_rows=max_rows)
    except (admission.AdmissionDenied, admission.AdmissionConflict, admission.GateTimeout) as error:
        owner.close()
        raise QueryError(error.code, error.status) from None
    except BaseException:
        owner.close()
        raise
    try:
        with admission.accepted(handle):
            with _lock:
                if _origin is None or not wr._borrowable_trino():
                    _origin = _lifetime.enter_context(wr.persistent_trino())
                origin = _origin
                trino_auth.validate_backend_url(origin)
                _pins[query_id].origin = origin
            with _pins[query_id].lock:
                _owned(principal, query_id, context.selected_role, cleanup=True)
                response = _receive(query_id, origin.rstrip('/') + '/v1/statement', 'POST', backend_sql.encode('utf-8'))
    except (admission.AdmissionDenied, admission.AdmissionConflict, admission.GateTimeout) as error:
        raise QueryError(error.code, error.status) from None
    except QueryError:
        raise
    except Exception:
        _state(query_id, 'UNKNOWN')
        raise QueryError('QUERY_BACKEND_FAILED', 502) from None
    with _pins[query_id].lock:
        _owned(principal, query_id, context.selected_role, cleanup=True)
        return response


def _capture_dataops(principal, context):
    """Private credential capture for a server-owned DataOps operation."""
    decision, headers = trino_auth.prepare_backend_v2(principal=principal,
        database_id=context.database_id, selected_role=context.selected_role)
    current = _resolve_context(principal, context.database_id, context.schema_id, context.selected_role,
        warehouse_id=context.warehouse_id, expected_binding_revision=context.binding_revision, decision=decision)
    if current != context:
        raise QueryError('CONTEXT_MISMATCH', 409)
    headers.update({'X-Trino-Catalog': context.sql_catalog, 'X-Trino-Schema': context.sql_schema,
                    'Accept-Encoding': 'identity'})
    return _Pin(context, '', dict(headers), trino_auth.backend_ssl_context(), max_rows=None)


def _submit_dataops(principal, statement, frozen, parent, operation_id, step):
    """Only DataOps' constrained generated statements enter this private hook.

    Parent, query intent, and child relation are durable before any POST. There
    is deliberately no retry path for an existing step.
    """
    from dataops_service import _Statement
    if type(statement) is not _Statement or not isinstance(frozen, _Pin):
        raise QueryError('UNKNOWN_CONTEXT', 400)
    try:
        backend_sql = _backend_statement(statement.sql)
    except ValueError:
        raise QueryError('SQL_PERMISSION_DENIED', 403) from None
    context = frozen.context
    current = resolve_context(principal, context.database_id, context.schema_id, context.selected_role,
        warehouse_id=context.warehouse_id, expected_binding_revision=context.binding_revision)
    if current != context:
        raise QueryError('CONTEXT_MISMATCH', 409)
    query_id = str(uuid.uuid4())
    headers = dict(frozen.headers)
    headers['X-Trino-Source'] = 'shirokuma-query-' + query_id
    owner = _acquire_query_owner(query_id)
    try:
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            revalidate_admission(db, principal, context, backend_username=headers['X-Trino-User'])
            op = db.execute('SELECT * FROM dataops_operations WHERE id=?', (operation_id,)).fetchone()
            if op is None or op['actor'] != principal.principal_id or op['state'] != 'ACTIVE':
                raise QueryError('ADMISSION_CONFLICT', 409)
            from dataops_service import _check_job
            job = db.execute('SELECT run_id FROM db_job_operations WHERE operation_id=?', (operation_id,)).fetchone()
            if job is not None:
                _check_job(db, job['run_id'])
            if db.execute('SELECT 1 FROM dataops_queries WHERE operation_id=? AND step=?',
                          (operation_id, step)).fetchone():
                raise QueryError('STEP_ALREADY_SUBMITTED', 409)
            handle = admission.admit(db, warehouse_id=context.warehouse_id, actor=principal.principal_id,
                kind='query', subject_id=query_id, parent=parent, generation_id=context.generation_id, database_id=context.database_id)
            registry.record_query(db, registry.QueryRecord(query_id, principal.principal_id,
                registry.QueryContext(context.database_id, context.schema_id, context.warehouse_id,
                                      context.generation_id), 'QUEUED', datetime.now(timezone.utc).isoformat()))
            registry.record_query_execution(db, registry.QueryExecutionContext(query_id,
                context.selected_role, 'dataops', context.sql_catalog, context.sql_schema,
                context.binding_revision, context.grant_revision, headers['X-Trino-User'],
                op['credential_snapshot_ref'], None, operation_id, step, statement.sql))
            db.execute('INSERT INTO dataops_queries (operation_id,step,query_id,action,commit_result) VALUES (?,?,?,?,?)',
                       (operation_id, step, query_id, statement.action, statement.commit_result))
        _pins[query_id] = _Pin(context, frozen.origin, headers, frozen.tls,
            admission=handle, owner=owner, max_rows=None)
    except BaseException:
        owner.close()
        raise
    try:
        import warehouse_resident as wr
        global _origin
        with admission.accepted(handle), _lock:
            if not frozen.origin:
                if _origin is None or not wr._borrowable_trino():
                    _origin = _lifetime.enter_context(wr.persistent_trino())
                trino_auth.validate_backend_url(_origin)
                frozen.origin = _origin
            _pins[query_id].origin = frozen.origin
        with _pins[query_id].lock:
            _owned(principal, query_id, context.selected_role, cleanup=True)
            response = _receive(query_id, frozen.origin.rstrip('/') + '/v1/statement',
                                'POST', backend_sql.encode('utf-8'))
            _owned(principal, query_id, context.selected_role, cleanup=True)
            return response
    except BaseException:
        if _row(query_id)['state'] not in _TERMINAL | {'UNKNOWN'}:
            _state(query_id, 'UNKNOWN')
        raise


def _receive(query_id, url, method, body=None):
    """Only backend attempts/response parsing may become execution uncertainty."""
    try:
        with admission.accepted(_pins[query_id].admission):
            return _receive_accepted(query_id, url, method, body)
    except (admission.AdmissionDenied, admission.AdmissionConflict, admission.GateTimeout) as error:
        raise QueryError(error.code, error.status) from None


def _receive_accepted(query_id, url, method, body=None):
    try:
        return _page(query_id, _transport(url, method, _pins[query_id], body))
    except (admission.AdmissionDenied, admission.AdmissionConflict, admission.GateTimeout):
        raise
    except QueryError:
        _state(query_id, 'UNKNOWN')
        raise
    except Exception:
        _state(query_id, 'UNKNOWN')
        raise QueryError('QUERY_BACKEND_FAILED', 502) from None


def fetch(principal, query_id, page_token, *, selected_role):
    pin = _pins.get(query_id)
    if pin is None:
        row = _owned(principal, query_id, selected_role)
        if row['execution_closure']:
            raise QueryError('EXECUTION_CLOSED', 409)
        raise QueryError('QUERY_CONTEXT_LOST', 409)
    with pin.lock:
        row = _owned(principal, query_id, selected_role, cleanup=True)
        if row['execution_closure']:
            raise QueryError('EXECUTION_CLOSED', 409)
        registered = pin.tokens.get(page_token)
        if not registered or 'GET' not in registered[1]:
            raise QueryError('QUERY_NOT_FOUND', 404)
        if page_token in pin.failures:
            code, status = pin.failures[page_token]
            raise QueryError(code, status)
        if row['state'] == 'UNKNOWN':
            raise QueryError('ADMISSION_CONFLICT', 409)
        if page_token in pin.responses:
            response = deepcopy(pin.responses[page_token])
            if row['state'] in ('CANCELED', 'FAILED', 'UNKNOWN'):
                response['state'] = response['stats']['state'] = row['state']
                response['next_uri'] = None
            return response
        if row['state'] in _TERMINAL:
            raise QueryError('CONTEXT_MISMATCH', 409)
        try:
            response = _receive(query_id, registered[0], 'GET')
            _owned(principal, query_id, selected_role, cleanup=True)
        except QueryError as error:
            pin.failures[page_token] = (error.code, error.status)
            raise
        pin.responses[page_token] = deepcopy(response)
        return deepcopy(response)


def cancel(principal, query_id, *, selected_role):
    pin = _pins.get(query_id)
    if pin is None:
        row = _owned(principal, query_id, selected_role, grants=False)
        if row['execution_closure']:
            return _reconcile_result(row)
        raise QueryError('QUERY_CONTEXT_LOST', 409)
    with pin.lock:
        row = _owned(principal, query_id, selected_role, grants=False)
        return _cancel_locked(query_id, pin, row)


def _cancel_registered(principal, query_id, token, *, selected_role):
    pin = _pins.get(query_id)
    if pin is None:
        row = _owned(principal, query_id, selected_role, grants=False)
        if row['execution_closure']:
            return _reconcile_result(row)
        raise QueryError('QUERY_CONTEXT_LOST', 409)
    with pin.lock:
        row = _owned(principal, query_id, selected_role, grants=False)
        registered = pin.tokens.get(token)
        if not registered or not {'GET', 'DELETE'}.issubset(registered[1]):
            raise QueryError('QUERY_NOT_FOUND', 404)
        return _cancel_locked(query_id, pin, row, registered[0])


def inspect_query(principal, query_id, *, selected_role):
    row = _owned(principal, query_id, selected_role)
    return dict(id=query_id, backend_query_id=row['backend_query_id'], context=_metadata(row),
                state=row['state'], execution_closure=row['execution_closure'], request_id=row['request_id'], worksheet_id=row['worksheet_id'],
                sql=row['sql'], mode=row['mode'], created_at=row['created_at'])


def _reconcile_authorized(principal, row, selected_role):
    if not isinstance(selected_role, str) or selected_role not in request_auth.ROLES:
        raise QueryError('UNKNOWN_CONTEXT', 400)
    if type(principal) is not request_auth.AuthenticatedPrincipal or row is None:
        raise QueryError('QUERY_NOT_FOUND', 404)
    try:
        account = request_auth._principal_accounts().get(principal.principal_id)
    except PermissionError:
        raise QueryError('QUERY_NOT_FOUND', 404) from None
    if account is None:
        raise QueryError('QUERY_NOT_FOUND', 404)
    if row['actor'] != principal.principal_id and not (
            selected_role == 'admin' and principal.platform_admin is True and
            principal.maximum_role == 'admin' and account.get('platform_admin') is True and
            account.get('role') == 'admin'):
        raise QueryError('QUERY_NOT_FOUND', 404)


def _reconcile_result(row):
    result = dict(id=row['id'], state=row['state'], backend_query_id=row['backend_query_id'])
    if row.get('execution_closure'):
        result['execution_closure'] = row['execution_closure']
    return result


def _backend_id(value):
    return isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', value) is not None


def _observation_matches(item, expected):
    return (isinstance(item, dict) and
        all(isinstance(item.get(key), str) for key in ('id', 'source', 'user', 'sql', 'state')) and
        _backend_id(item['id']) and item['source'] == expected['source'] and
        item['user'] == expected['backend_username'] and item['sql'] == _backend_statement(expected['sql']) and
        (expected['backend_query_id'] is None or item['id'] == expected['backend_query_id']))


def reconcile_query(principal, query_id, *, selected_role, observer):
    """Operator-only observation seam. No submitted evidence can execute SQL."""
    pin = _pins.get(query_id)
    with ExitStack() as leases:
        if pin is not None:
            leases.enter_context(pin.lock)
        original = _row(query_id)
        _reconcile_authorized(principal, original, selected_role)
        if original['execution_closure']:
            return _reconcile_result(original)
        if original['state'] in _TERMINAL:
            _close_query_owner(pin)
            return _reconcile_result(original)
        if pin is not None and original['state'] != 'UNKNOWN':
            raise QueryError('CONTEXT_MISMATCH', 409)
        if original['state'] == 'UNKNOWN':
            _close_query_owner(pin)
        leases.enter_context(_acquire_query_owner(query_id))
        expected = dict(query_id=query_id, backend_query_id=original['backend_query_id'],
            warehouse_id=original['warehouse_id'], backend_username=original['backend_username'],
            source='shirokuma-query-' + query_id, sql=original['sql'])
        observation_failed = False
        try:
            observed = observer(deepcopy(expected))
        except Exception:
            observed, observation_failed = None, True
        # Malformed evidence, ambiguity and absence are never terminal proof.
        valid = isinstance(observed, list) and all(isinstance(item, dict) and
            all(isinstance(item.get(key), str) for key in ('id', 'source', 'user', 'sql', 'state')) and
            _backend_id(item['id']) for item in observed)
        matches = [item for item in observed if _observation_matches(item, expected)] if valid else []
        proof = matches[0] if len(matches) == 1 and matches[0]['state'] in _TERMINAL else None
        # Account files and observation are read outside the write transaction.
        _reconcile_authorized(principal, original, selected_role)
        try:
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if admission.query_closure(db, query_id):
                    return _reconcile_result(_row(query_id))
                row = db.execute('''SELECT q.*,e.* FROM query_records q JOIN query_execution_contexts e
                    ON q.id=e.query_id WHERE q.id=?''', (query_id,)).fetchone()
                if row is None or any(row[key] != original[key] for key in original
                                     if key not in ('state', 'backend_query_id', 'execution_closure')):
                    raise QueryError('CONTEXT_MISMATCH', 409)
                row = dict(row)
                if row['state'] in _TERMINAL:
                    _close_query_owner(pin)
                    return _reconcile_result(row)
                if row['backend_query_id'] != original['backend_query_id']:
                    raise QueryError('CONTEXT_MISMATCH', 409)
                if proof is None:
                    if pin is None and row['state'] in ('QUEUED', 'RUNNING'):
                        if _pins.get(query_id) is not None:
                            raise QueryError('CONTEXT_MISMATCH', 409)
                        handle = admission.get_for_subject(db, kind='query', subject_id=query_id,
                                                           warehouse_id=row['warehouse_id'])
                        registry.set_query_state(db, query_id, 'UNKNOWN')
                        admission.finish(db, handle, state='UNKNOWN')
                        row['state'] = 'UNKNOWN'
                else:
                    handle = admission.get_for_subject(db, kind='query', subject_id=query_id,
                                                       warehouse_id=row['warehouse_id'])
                    registry.set_query_backend_id(db, query_id, proof['id'])
                    registry.set_query_state(db, query_id, proof['state'])
                    if proof['state'] == 'FINISHED':
                        child = db.execute('SELECT operation_id,commit_result FROM dataops_queries WHERE query_id=?',
                                           (query_id,)).fetchone()
                        if child is not None and child['commit_result'] is not None:
                            db.execute('UPDATE dataops_operations SET committed=1,result=? WHERE id=?',
                                       (child['commit_result'], child['operation_id']))
                    admission.finish(db, handle, state=proof['state'], evidence_ref=secrets.token_urlsafe(32))
                    row.update(state=proof['state'], backend_query_id=proof['id'])
                result = _reconcile_result(row)
        except (admission.AdmissionDenied, admission.AdmissionConflict, admission.GateTimeout) as error:
            raise QueryError(error.code, error.status) from None
        if result['state'] in _TERMINAL or result['state'] == 'UNKNOWN':
            _close_query_owner(pin)
        if observation_failed:
            raise QueryError('QUERY_BACKEND_FAILED', 502)
        return result


def observe_query_backend(expected):
    """GET-only Trino 483 metadata observation using INTERNAL admin authority."""
    global _origin
    known_id = expected.get('backend_query_id')
    if known_id is not None and not _backend_id(known_id):
        raise QueryError('UNKNOWN_CONTEXT', 400)
    try:
        import warehouse_resident as wr
        credential = trino_auth.platform_admin_credentials()
        authorization = base64.b64encode((credential['username'] + ':' + credential['password']).encode()).decode()
        tls = trino_auth.backend_ssl_context()
        with _lock:
            if _origin is None or not wr._borrowable_trino():
                _origin = _lifetime.enter_context(wr.persistent_trino())
            origin = _origin
        trino_auth.validate_backend_url(origin)
        parsed = urlsplit(origin)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
                parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
            raise ValueError()

        def get(path):
            connection = (http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, context=tls, timeout=30)
                          if parsed.scheme == 'https' else http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=30))
            try:
                connection.request('GET', path, headers={'Authorization': 'Basic ' + authorization,
                    'X-Trino-User': credential['username'], 'Accept-Encoding': 'identity'})
                response = connection.getresponse()
                if response.status in (404, 410):
                    return None
                if response.status != 200:
                    raise ValueError()
                return json.loads(response.read())
            finally:
                connection.close()

        def record(value):
            if not isinstance(value, dict) or not isinstance(value.get('session'), dict):
                return None
            item = dict(id=value.get('queryId'), state=value.get('state'), sql=value.get('query'),
                        source=value['session'].get('source'), user=value['session'].get('user'))
            if not _backend_id(item['id']) or any(not isinstance(v, str) for v in item.values()):
                return None
            return item

        if known_id is not None:
            item = record(get('/v1/query/' + known_id))
            return [item] if item is not None else []
        listing = get('/v1/query')
        if not isinstance(listing, list):
            return []
        # Untagged and unrelated Trino queries are normal. Only exact saved
        # identity candidates can supply proof or make that proof ambiguous.
        candidates = [record(value) for value in listing if isinstance(value, dict) and
            isinstance(value.get('session'), dict) and
            value['session'].get('source') == expected['source'] and
            value['session'].get('user') == expected['backend_username'] and
            value.get('query') == _backend_statement(expected['sql'])]
        if len(candidates) != 1 or candidates[0] is None:
            return []
        if not _observation_matches(candidates[0], expected):
            return []
        item = record(get('/v1/query/' + candidates[0]['id']))
        return [item] if item is not None and item['id'] == candidates[0]['id'] else []
    except Exception:
        raise QueryError('QUERY_BACKEND_FAILED', 502) from None


def reconcile_query_from_backend(principal, query_id, *, selected_role):
    return reconcile_query(principal, query_id, selected_role=selected_role, observer=observe_query_backend)


def history(principal, *, database_id, selected_role):
    if not isinstance(database_id, str) or not database_id:
        raise QueryError('UNKNOWN_CONTEXT', 400)
    with control_store.connect() as db:
        ids = [r[0] for r in db.execute('SELECT q.id FROM query_records q JOIN query_execution_contexts e ON q.id=e.query_id WHERE q.actor=? AND q.database_id=? ORDER BY q.created_at DESC',
                                      (principal.principal_id, database_id))]
    with control_store.connect() as db:
        binding = registry.get_binding(db, database_id)
    if binding is None:
        raise QueryError('UNKNOWN_CONTEXT', 404)
    try:
        starting = request_auth.authorize_database(principal, database_id, selected_role,
                                       expected_generation_id=binding.applied_generation_id)
    except PermissionError:
        raise QueryError('DB_ACCESS_REVOKED', 403) from None
    items = []
    for query_id in ids:
        row = _row(query_id)
        if row is None or row['selected_role'] != selected_role:
            continue
        try:
            items.append(inspect_query(principal, query_id, selected_role=selected_role))
        except QueryError as error:
            # An old pinned context can lose access while the selected database
            # remains authorized. Such records must not poison current history.
            if error.code not in ('DB_ACCESS_REVOKED', 'CONTEXT_MISMATCH', 'QUERY_NOT_FOUND'):
                raise
    # A real grant loss during iteration must not be mistaken for one stale
    # record and return SQL collected before access was revoked.
    try:
        final = request_auth.authorize_database(principal, database_id, selected_role,
                                       expected_generation_id=binding.applied_generation_id)
        if final.grant_revision != starting.grant_revision:
            raise PermissionError('Authorization changed during history collection')
    except PermissionError:
        raise QueryError('DB_ACCESS_REVOKED', 403) from None
    return {'queries': items}
