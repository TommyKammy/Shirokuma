"""Bounded observations of a completed Reader SELECT, without replaying it.

The original SELECT is used only inside EXPLAIN. A fresh diagnosis token uses
the existing Reader transport identity to preserve its exact drain rules; it
must differ from the original read token. Raw plan/history/statistics cells
remain in memory. Only contract projections and safe transport receipts reach
progress callbacks. Model inference starts after this function has returned,
when all observation queries and owned tunnels have finished.

Snapshot Reader v2 keeps the truthful current identity and its retained snapshot
catalog separate from the selected historical snapshot. Both fresh windows must
match the saved source; v2/v3 add those rowless catalogs to the journal. The
filtered v3 source keeps its exact typed predicate inside EXPLAIN/SHOW STATS;
the original data SELECT is never executed by this observer.
"""
from contextlib import contextmanager
from copy import deepcopy
import re

from tools.local_experiment.query_read import client as Q
from tools.local_experiment.query_read import contract as R
from tools.local_experiment.query_diagnose import contract as C

ERROR_CODES = frozenset({'OBSERVATION_FAILED', 'INTERRUPTED'})
PLAN_BYTES = 128 * 1024
HISTORY_BYTES = 16 * 1024


class ObservationError(RuntimeError):
    """Fixed code only; no upstream messages or response cells."""


def require(condition):
    if not condition:
        raise ObservationError('OBSERVATION_FAILED')


def validate_source(source):
    return deepcopy(C.validate_source(source))


class DiagnosisClient(Q.QueryClient):
    """Only this source's five precompiled observation statements, once each."""
    def __init__(self, url, token, *, source, **kwargs):
        checked = validate_source(source)
        require(token != checked['read_token'] and not kwargs.get('control', False))
        self._statements = deepcopy(C.statements(checked))
        require(set(self._statements) == {'schema_before', 'explain', 'history', 'stats', 'schema_after'})
        self._attempted_labels = set()
        super().__init__(url, token, **kwargs)

    def _validate_sql(self, sql, label):
        Q.require(type(sql) is str and type(label) is str and label in self._statements and
                  sql == self._statements[label] and 0 < len(sql.encode()) <= 16384 and
                  label not in self._attempted_labels, 'READ_SQL_INVALID')
        # A response or journal failure never authorizes an automatic repeat.
        self._attempted_labels.add(label)

    def _validate_cell(self, value, kind, label):
        if kind == 'varchar' and label in ('explain', 'history'):
            maximum = PLAN_BYTES if label == 'explain' else HISTORY_BYTES
            Q.require(value is None or type(value) is str and len(value.encode('utf-8')) <= maximum,
                      'QUERY_CELL_TYPE_INVALID')
        else:
            super()._validate_cell(value, kind, label)

    def _column_type(self, value, label):
        # Trino 483 ExplainRewrite/ShowStatsRewrite emit string literals;
        # ExpressionAnalyzer.visitStringLiteral gives them varchar(length).
        # History to_iso8601(timestamp(3) with time zone) returns varchar(32)
        # (operator/scalar/timestamptz/ToIso8601.java). Schema/Reader remain
        # unchanged; all per-phase cell byte bounds still apply.
        if label in ('explain', 'stats', 'history') and type(value) is str:
            match = re.fullmatch(r'varchar\((0|[1-9][0-9]{0,9})\)', value)
            if match is not None and int(match.group(1)) <= 2147483647:
                return 'varchar'
        return super()._column_type(value, label)


@contextmanager
def runtime(token, source, progress):
    def factory(url, run_token, **kwargs):
        return DiagnosisClient(url, run_token, source=source, **kwargs)
    with Q.runtime(token, progress, _worker_factory=factory) as clients:
        yield clients


def _observe(source, worker, catalog, journal, save):
    statements = C.statements(source)
    request = C.base_request(source)
    before_columns = R.project_schema(worker.query(statements['schema_before'], 'schema_before'))
    catalog.authenticate()
    before = C.project_source_window(catalog.load(request), source, before_columns)
    C.verify_source_window(source, before)
    journal['identity_before'] = before['identity']
    if source['schema_version'] >= 2:
        journal['snapshot_catalog_before'] = before['snapshots']
    save()
    for label, projector in (('explain', C.project_plan), ('history', C.project_history), ('stats', C.project_stats)):
        raw = worker.query(statements[label], label)
        projected = projector(raw, source)
        del raw
        journal['plan' if label == 'explain' else label] = projected
        save()
    after_columns = R.project_schema(worker.query(statements['schema_after'], 'schema_after'))
    after = C.project_source_window(catalog.load(request), source, after_columns)
    C.verify_source_window(source, before, after)
    journal['identity_after'] = after['identity']
    if source['schema_version'] >= 2:
        journal['snapshot_catalog_after'] = after['snapshots']
    save()


def observe(source, token, progress, *, runtime_factory=None, drain_fn=None):
    """Complete fresh observations, drain and tunnel closure before returning.

    Runtime/drain dependency injection is for offline tests. The ordinary path
    always uses the fixed client above and the existing owned-query drainer.
    Cancellation is rethrown as the same object after best-effort durable safe
    progress and cleanup. Ordinary failures expose only OBSERVATION_FAILED.
    """
    require(callable(progress))
    journal = {'state': 'pending', 'source_sha256': None, 'plan': None, 'history': None,
               'stats': None, 'identity_before': None, 'identity_after': None,
               'queries': {'worker': [], 'control': []}, 'query_drain': {'verified': False},
               'runtime': None, 'cleanup_verified': False, 'error_code': None}
    clients = None
    error = None

    def save():
        if clients is not None:
            journal['queries'] = {key: clients[i].receipt()['records']
                                  for i, key in enumerate(('worker', 'control'))}
        progress(deepcopy(journal))

    def runtime_progress(snapshot):
        journal['runtime'] = deepcopy(snapshot)
        save()

    try:
        Q.token_id(token)
        source = validate_source(source)
        require(token != source['read_token'])
        journal['source_sha256'] = R.digest(source)
        if source['schema_version'] >= 2:
            journal.update(snapshot_catalog_before=None, snapshot_catalog_after=None)
        save()
        factory = runtime if runtime_factory is None else runtime_factory
        drainer = Q.drain if drain_fn is None else drain_fn
        require(callable(factory) and callable(drainer))
        with factory(token, source, runtime_progress) as current:
            clients = current
            worker, control, catalog = clients
            require(worker.token == control.token == token and not worker.control and control.control and
                    (worker.user, worker.source) == Q.identity(token) and
                    (control.user, control.source) == Q.identity(token, True))
            try:
                journal['state'] = 'observing'
                save()
                _observe(source, worker, catalog, journal, save)
            except BaseException as caught:
                error = caught
            finally:
                # Reset before each attempt: no stale successful proof survives.
                journal['query_drain'] = {'verified': False}
                try:
                    save()
                except BaseException as caught:
                    error = Q.first_error(error, caught)
                try:
                    journal['query_drain'] = drainer(control, worker, token)
                    require(journal['query_drain'].get('verified') is True)
                except BaseException as caught:
                    error = Q.first_error(error, caught)
                try:
                    save()
                except BaseException as caught:
                    error = Q.first_error(error, caught)
            if error is not None:
                raise error
        require(journal['runtime'] is not None and journal['runtime']['state'] == 'verified' and
                journal['runtime']['cleanup']['verified'] is True and
                journal['query_drain']['verified'] is True)
    except BaseException as caught:
        error = Q.first_error(error, caught)
    journal['cleanup_verified'] = bool(journal['runtime'] and
        journal['runtime'].get('cleanup', {}).get('verified') is True and
        journal['query_drain'].get('verified') is True)
    journal['state'] = 'verified' if error is None else ('failed' if isinstance(error, Exception) else 'interrupted')
    journal['error_code'] = None if error is None else ('OBSERVATION_FAILED' if isinstance(error, Exception) else 'INTERRUPTED')
    try:
        save()
    except BaseException as caught:
        error = Q.first_error(error, caught)
    if error is not None:
        if not isinstance(error, Exception):
            raise error
        raise ObservationError('OBSERVATION_FAILED') from None
    return deepcopy(journal)
