"""Bounded snapshot listing and historical reads with owned-query cleanup.

Fresh discovery, schema and catalog metadata bind the only permitted SELECT to
one retained, compatible snapshot. Current table identity remains unchanged in
the evidence; the selected snapshot is separate. Raw rows stay in memory and
are returned only after drain, tunnel closure and final safe journal delivery.
This module never calls a model or changes table/catalog state.
"""
from contextlib import contextmanager
from copy import deepcopy

from tools.local_experiment.query_read import client as Q
from tools.local_experiment.query_read import contract as R
from tools.local_experiment.query_snapshots import contract as C

ERROR_CODES = frozenset(C.ERROR_CODES) | {'OBSERVATION_FAILED', 'INTERRUPTED'}


class ObservationError(RuntimeError):
    def __init__(self, code):
        super().__init__(code if type(code) is str and code in ERROR_CODES else 'OBSERVATION_FAILED')


def require(condition):
    if not condition:
        raise ObservationError('OBSERVATION_FAILED')


def error_code(error):
    if error is None:
        return None
    if not isinstance(error, Exception):
        return 'INTERRUPTED'
    if type(error) in (ObservationError, C.ContractError, R.ContractError) and str(error) in ERROR_CODES:
        return str(error)
    return 'OBSERVATION_FAILED'


class SnapshotClient(Q.QueryClient):
    """Restrict the existing Reader transport to this request, once per phase."""
    def __init__(self, url, token, *, request, **kwargs):
        self._request = deepcopy(C.normalize_request(request))
        require(not kwargs.get('control', False))
        self._statements = {'inventory': C.discovery_sql(self._request),
                            'schema_before': C.schema_sql(self._request),
                            'schema_after': C.schema_sql(self._request)}
        self._attempted_labels = set()
        super().__init__(url, token, **kwargs)

    def bind_select(self, inventory, columns, window):
        require(self._request['command']=='select' and 'select' not in self._statements and
                self._attempted_labels == {'inventory', 'schema_before'})
        # The only way to authorize a data statement is the pure fresh-window
        # compiler. There is no arbitrary SQL argument on this binding API.
        self._statements['select'] = C.select_sql(self._request, inventory, columns, window)

    def _validate_sql(self, sql, label):
        Q.require(type(sql) is str and type(label) is str and label in self._statements and
                  0 < len(sql.encode()) <= 16384 and sql == self._statements[label] and
                  label not in self._attempted_labels,
                  'READ_SQL_INVALID')
        # Only the new profile's fresh-bound SELECT uses a typed BIGINT literal.
        # All other statements keep the original Reader allowlist unchanged.
        if label != 'select':
            super()._validate_sql(sql, label)
        self._attempted_labels.add(label)


@contextmanager
def runtime(token, request, progress):
    def factory(url, run_token, **kwargs):
        return SnapshotClient(url, run_token, request=request, **kwargs)
    with Q.runtime(token, progress, _worker_factory=factory) as clients:
        yield clients


def _observe(request, worker, catalog, journal, save):
    base = C.base_request(request)
    inventory = R.project_inventory(worker.query(C.discovery_sql(request), 'inventory'),
                                    base['table'].split('.')[0])
    journal['inventory'] = inventory
    save()
    C.require_discovered(request, inventory)
    columns = R.project_schema(worker.query(C.schema_sql(request), 'schema_before'))
    catalog.authenticate()
    before = C.project_window(catalog.load(base), request, columns)
    journal['identity_before'] = before['identity']
    journal['snapshot_catalog_before'] = before['snapshots']
    save()
    if request['command']=='select':
        C.select_snapshot(before, request)
        worker.bind_select(inventory, columns, before)
        raw = worker.query(C.select_sql(request, inventory, columns, before), 'select')
        public = C.project_rows(raw, request, columns, before)
        del raw
        journal['selection'] = C.receipt_projection(public)
        save()
    else:
        public = C.project_snapshots(before, request)
    after_columns = R.project_schema(worker.query(C.schema_sql(request), 'schema_after'))
    after = C.project_window(catalog.load(base), request, after_columns)
    journal['identity_after'] = after['identity']
    journal['snapshot_catalog_after'] = after['snapshots']
    save()
    C.verify_window(before, after)
    return public


def observe(request, token, progress, *, runtime_factory=None, drain_fn=None):
    """Return (rowless journal, public candidate) only after resource cleanup.

    Expected pure-contract refusals keep their fixed codes. Other errors become
    OBSERVATION_FAILED. The first non-Exception cancellation survives secondary
    drain, teardown and persistence failures and is rethrown as the same object.
    Dependency injection is only for offline tests; defaults use fixed runtime.
    """
    require(callable(progress))
    journal = {'state': 'pending', 'request_sha256': None, 'inventory': None,
               'identity_before': None, 'identity_after': None,
               'snapshot_catalog_before': None, 'snapshot_catalog_after': None,
               'selection': None, 'queries': {'worker': [], 'control': []},
               'query_drain': {'verified': False}, 'runtime': None,
               'cleanup_verified': False, 'error_code': None}
    clients = None
    error = None
    public = None

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
        request = deepcopy(C.normalize_request(request))
        journal['request_sha256'] = R.digest(request)
        save()
        factory = runtime if runtime_factory is None else runtime_factory
        drainer = Q.drain if drain_fn is None else drain_fn
        require(callable(factory) and callable(drainer))
        with factory(token, request, runtime_progress) as current:
            clients = current
            worker, control, catalog = clients
            require(worker.token == control.token == token and not worker.control and control.control and
                    (worker.user, worker.source) == Q.identity(token) and
                    (control.user, control.source) == Q.identity(token, True))
            try:
                journal['state'] = 'observing'
                save()
                public = _observe(request, worker, catalog, journal, save)
            except BaseException as caught:
                error = caught
            finally:
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
        require(public is not None and journal['runtime'] is not None and
                journal['runtime']['state'] == 'verified' and
                journal['runtime']['cleanup']['verified'] is True and
                journal['query_drain']['verified'] is True)
        C.validate_public(public, request)
    except BaseException as caught:
        error = Q.first_error(error, caught)
    journal['cleanup_verified'] = bool(journal['runtime'] and
        journal['runtime'].get('cleanup', {}).get('verified') is True and
        journal['query_drain'].get('verified') is True)
    journal['state'] = 'verified' if error is None else ('failed' if isinstance(error, Exception) else 'interrupted')
    journal['error_code'] = error_code(error)
    try:
        save()
    except BaseException as caught:
        error = Q.first_error(error, caught)
    if error is not None:
        if not isinstance(error, Exception):
            raise error
        raise ObservationError(error_code(error)) from None
    return deepcopy(journal), deepcopy(public)
