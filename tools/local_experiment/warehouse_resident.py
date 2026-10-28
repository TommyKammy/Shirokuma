"""Resident Trino warehouse control for the dedicated local lab."""
import argparse
from contextlib import contextmanager
from functools import wraps
import control_store
import warehouse_admission as admission
import http.client
import http.server
import json
import logging
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

KUBE_CONTEXT = os.environ.get('KUBE_CONTEXT', 'colima-mac-studio-solo')
KUBECTL = ['kubectl']
if KUBE_CONTEXT not in ('', 'none', 'default'):
    KUBECTL += ['--context', KUBE_CONTEXT]
KUBECTL += ['-n', os.environ.get('KUBE_NAMESPACE', 'shirokuma-dev')]
TIMEOUT = 180
_PERSISTENT_TRINO = None
_HTTP_POOL = threading.local()
_DEFAULT_URLOPEN = urllib.request.urlopen
WRITABLE_SCHEMAS = ('raw', 'sandbox', 'staging', 'marts')
_AUTO_CHILL_MINUTES = 0
_LAST_ACTIVITY = time.time()
_ACTIVITY_LOCK = threading.Lock()
_AUTO_CHILL_THREAD = None
_AUTO_CHILL_STOP = threading.Event()
_AUTO_CHILL_LOCK = threading.Lock()
_WAREHOUSE_GATE = threading.local()


class TrinoQueryError(RuntimeError):
    """Keep a safe error code available without exposing backend message values."""

    def __init__(self, message, error_name=None):
        super().__init__('Trino query failed: ' + message)
        self.error_name = (error_name if isinstance(error_name, str) and
                           re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', error_name) else 'UNKNOWN')


def start_auto_chill_daemon(interval=15):
    """Start one background idle check loop for this server process."""
    global _AUTO_CHILL_THREAD
    if interval <= 0:
        raise ValueError('interval must be positive')
    with _AUTO_CHILL_LOCK:
        if _AUTO_CHILL_THREAD is not None and _AUTO_CHILL_THREAD.is_alive():
            return
        _AUTO_CHILL_STOP.clear()

        def run():
            while not _AUTO_CHILL_STOP.wait(interval):
                try:
                    check_auto_chill()
                except Exception:
                    logging.getLogger(__name__).exception('auto-chill check failed')

        _AUTO_CHILL_THREAD = threading.Thread(target=run, name='warehouse-auto-chill', daemon=True)
        _AUTO_CHILL_THREAD.start()


def stop_auto_chill_daemon():
    """Stop the loop and wait for any in-progress check to finish."""
    global _AUTO_CHILL_THREAD
    with _AUTO_CHILL_LOCK:
        _AUTO_CHILL_STOP.set()
        if _AUTO_CHILL_THREAD is not None:
            _AUTO_CHILL_THREAD.join()
            _AUTO_CHILL_THREAD = None


def activity_path():
    root = Path(__file__).resolve().parents[2]
    return Path(os.environ.get('SHIROKUMA_STATE_DIR', root / 'var/shirokuma')) / 'runtime' / 'last_activity.json'


@contextmanager
def _warehouse_gate(exclusive=False, deadline=None):
    """Coordinate accepted work and warehouse transitions across local processes."""
    previous = getattr(_WAREHOUSE_GATE, 'mode', None)
    with admission.lifecycle_gate(exclusive=exclusive, deadline=deadline):
        _WAREHOUSE_GATE.mode = previous or ('exclusive' if exclusive else 'shared')
        try:
            yield
        finally:
            _WAREHOUSE_GATE.mode = previous


def _operation_finish(handle, state):
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        rows = admission.blockers(db)
        saved = next((row for row in rows if row['id'] == handle.id), None)
        if saved is None or saved['state'] == 'UNKNOWN':
            return
        if state != 'UNKNOWN' and any(row.get('parent_id') == handle.id for row in rows):
            state = 'UNKNOWN'
        admission.finish(db, handle, state=state)


@contextmanager
def warehouse_operation(*, sql=False):
    """One durable compound boundary, and one child per logical legacy SQL."""
    if _profile_active():
        raise ValueError('Legacy warehouse operation is unsupported with the database profile')
    parent = admission.current()
    if parent is not None and (not sql or parent.kind == 'legacy_sql'):
        with admission.accepted(parent):
            yield parent
        return
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        handle = admission.admit(db, actor=parent.actor if parent else 'shirokuma-local',
            kind='legacy_sql' if sql else 'operation', subject_id=uuid.uuid4().hex,
            parent=parent)
    with admission.accepted(handle):
        try:
            yield handle
        except BaseException as error:
            state = 'FAILED' if not sql and isinstance(error, (ValueError, PermissionError)) else 'UNKNOWN'
            _operation_finish(handle, state)
            raise
        else:
            _operation_finish(handle, 'FINISHED')


def legacy_operation(function):
    @wraps(function)
    def run(*args, **kwargs):
        with warehouse_operation():
            return function(*args, **kwargs)
    return run


def check_lifecycle(*, accepted_wake=False, db=None):
    """Called while the lifecycle gate is held, before any mutation."""
    handle = admission.current() if accepted_wake else None
    if db is None:
        with control_store.connect() as connection:
            return check_lifecycle(accepted_wake=accepted_wake, db=connection)
    else:
        if handle is not None:
            with admission.accepted(handle):
                return
        import logical_creations
        import runtime_profile
        warehouse_id = runtime_profile.logical_config()['warehouse_id'] if logical_creations.enabled() else 'LOCAL'
        admission.check_open(db, warehouse_id)
        if admission.blockers(db, warehouse_id):
            raise admission.AdmissionConflict('warehouse has unresolved work')


@contextmanager
def lifecycle_mutation(*, accepted_wake=False):
    with _warehouse_gate(exclusive=not accepted_wake), control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        check_lifecycle(accepted_wake=accepted_wake, db=db)
        yield db


@contextmanager
def warehouse_activity():
    """Keep accepted work ahead of stop, or wait for stop before accepting it."""
    nested = getattr(_WAREHOUSE_GATE, 'mode', None)
    with _warehouse_gate():
        if not nested:
            touch_activity()
        try:
            yield
        finally:
            if not nested:
                touch_activity()


def touch_activity():
    """Record activity for CLI, endpoint and Workbench processes atomically."""
    global _LAST_ACTIVITY
    with _ACTIVITY_LOCK:
        _LAST_ACTIVITY = time.time()
        path = activity_path()
        temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps({'last_activity': _LAST_ACTIVITY}), encoding='utf-8')
            temporary.replace(path)
        except OSError as exc:
            logging.getLogger(__name__).warning('Unable to record warehouse activity: %s', exc)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                logging.getLogger(__name__).warning('Unable to clean up activity file: %s', exc)


def read_last_activity():
    """Return the latest in-memory or shared Unix timestamp."""
    try:
        shared = json.loads(activity_path().read_text(encoding='utf-8'))['last_activity']
        if type(shared) not in (int, float):
            return _LAST_ACTIVITY
        return max(_LAST_ACTIVITY, shared)
    except (OSError, ValueError, KeyError, TypeError):
        return _LAST_ACTIVITY


def auto_chill_status():
    return dict(auto_chill_minutes=_AUTO_CHILL_MINUTES,
                idle_seconds=max(0, int(time.time() - read_last_activity())))


def configure_auto_chill(minutes: int):
    global _AUTO_CHILL_MINUTES
    if type(minutes) is not int or not 0 <= minutes <= 1440:
        raise ValueError('minutes must be an integer between 0 and 1440')
    path = activity_path().with_name('auto_chill.json')
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    with lifecycle_mutation(), _ACTIVITY_LOCK:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps({'auto_chill_minutes': minutes}), encoding='utf-8')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        _AUTO_CHILL_MINUTES = minutes
    touch_activity()
    if minutes > 0:
        start_auto_chill_daemon()
    else:
        stop_auto_chill_daemon()
    return auto_chill_status()

def _load_auto_chill_minutes(default=0):
    """Read the shared setting without starting or joining the monitor thread."""
    try:
        minutes = json.loads(activity_path().with_name('auto_chill.json').read_text())['auto_chill_minutes']
        if type(minutes) is not int or not 0 <= minutes <= 1440:
            raise ValueError('invalid auto-chill minutes')
    except FileNotFoundError:
        minutes = default
    except (OSError, ValueError, KeyError, TypeError):
        logging.getLogger(__name__).warning('Unable to load auto-chill setting; disabling auto-chill')
        minutes = 0
    return minutes


def initialize_auto_chill():
    """Load the saved setting and resume idle checks when a server starts."""
    global _AUTO_CHILL_MINUTES
    minutes = _load_auto_chill_minutes()
    with _ACTIVITY_LOCK:
        _AUTO_CHILL_MINUTES = minutes
    if minutes > 0:
        start_auto_chill_daemon()
    else:
        stop_auto_chill_daemon()


def check_auto_chill(size='LOCAL'):
    global _AUTO_CHILL_MINUTES
    _AUTO_CHILL_MINUTES = _load_auto_chill_minutes(_AUTO_CHILL_MINUTES)
    chilled = False
    last_activity = read_last_activity()
    if not (_AUTO_CHILL_MINUTES > 0 and
            time.time() - last_activity >= _AUTO_CHILL_MINUTES * 60):
        return dict(chilled=False, **auto_chill_status())
    # Serialize the poll and stop with lifecycle changes. Durable admissions
    # additionally cover the gaps between backend requests and job steps.
    with _warehouse_gate(exclusive=True):
        try:
            check_lifecycle()
        except (admission.AdmissionDenied, admission.AdmissionConflict):
            return dict(chilled=False, **auto_chill_status())
        _AUTO_CHILL_MINUTES = _load_auto_chill_minutes(_AUTO_CHILL_MINUTES)
        last_activity = read_last_activity()
        if (_AUTO_CHILL_MINUTES == 0 or
                time.time() - last_activity < _AUTO_CHILL_MINUTES * 60 or
                status(size)['replicas'] == 0):
            return dict(chilled=False, **auto_chill_status())
        with warehouse_operation(sql=True), trino() as url:
            count = _execute(url,
                "SELECT COUNT(*) FROM system.runtime.queries WHERE state IN ('QUEUED', 'WAITING_FOR_RESOURCES', 'DISPATCHING', 'PLANNING', 'STARTING', 'RUNNING', 'FINISHING') "
                "AND query NOT LIKE '%system.runtime.queries%'",
                {'X-Trino-User': 'shirokuma-warehouse'})['rows'][0][0]
        if count > 0:
            touch_activity()
            return dict(chilled=False, active_queries=count, **auto_chill_status())
        with _ACTIVITY_LOCK:
            # Another process may have turned chilling off during the Trino poll.
            _AUTO_CHILL_MINUTES = _load_auto_chill_minutes(_AUTO_CHILL_MINUTES)
            latest = read_last_activity()
            if (_AUTO_CHILL_MINUTES > 0 and latest == last_activity and
                    time.time() - latest >= _AUTO_CHILL_MINUTES * 60):
                try:
                    transition('chill', size)
                except (admission.AdmissionDenied, admission.AdmissionConflict):
                    pass  # A new durable admission won the race with this poll.
                else:
                    chilled = True
    return dict(chilled=chilled, **auto_chill_status())


def identifier(value: str) -> str:
    return '.'.join('"' + part.replace('"', '""') + '"' for part in value.split('.'))


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def warehouse(value):
    size = value.upper()
    if size not in {"XS", "S", "LOCAL"}:
        raise ValueError("warehouse must be XS, S, or LOCAL")
    return size


def kubectl(*args):
    import logical_creations
    argv = _sql_kubectl() if logical_creations.enabled() else KUBECTL
    return subprocess.run(argv + list(args), check=True, capture_output=True,
                          text=True, timeout=TIMEOUT).stdout


def deployment():
    return json.loads(kubectl("get", "deployment/trino-local", "-o", "json"))


def status(size):
    size = warehouse(size)
    import logical_creations
    if logical_creations.enabled():
        return _logical_status(size, _logical_foundation())
    return status_from_deployment(size, deployment())


def _logical_foundation(db=None):
    """Fence shared compute against committed foundation and applied provenance."""
    import logical_foundation
    import runtime_profile
    from warehouse_operations import OperationError
    if db is None:
        with control_store.connect() as connection:
            return _logical_foundation(connection)
    try:
        cfg = runtime_profile.logical_config()
        foundation = logical_foundation.load(cfg['provider_config_file'])
        if any(foundation[k] != cfg[v] for k, v in (
                ('environment_id', 'environment_id'), ('warehouse_id', 'warehouse_id'),
                ('foundation_id', 'foundation_generation_id'), ('revision', 'foundation_revision'))):
            raise ValueError()
        row = db.execute('SELECT * FROM logical_foundations WHERE environment_id=? AND warehouse_id=?',
                         (cfg['environment_id'], cfg['warehouse_id'])).fetchone()
        if (not row or row['status'] != 'READY' or row['revision'] != cfg['foundation_revision']
                or row['generation_id'] != cfg['foundation_generation_id']
                or row['config_ref'] != cfg['provider_config_file'] or not row['verification_ref']):
            raise ValueError()
        generation = db.execute('SELECT * FROM warehouse_config_generations WHERE id=?',
                                (row['generation_id'],)).fetchone()
        if (not generation or generation['state'] != 'APPLIED'
                or generation['warehouse_id'] != cfg['warehouse_id']
                or generation['apply_id'] != 'foundation:' + row['generation_id']
                or json.loads(generation['configuration_refs']) != [row['config_ref'], row['verification_ref']]):
            raise ValueError()
        control = db.execute('SELECT * FROM warehouse_controls WHERE warehouse_id=?',
                             (cfg['warehouse_id'],)).fetchone()
        if (not control or control['state'] != 'OPEN' or control['operation_id'] is not None
                or control['applied_generation_id'] != row['generation_id']):
            raise ValueError()
        return foundation
    except (KeyError, TypeError, ValueError):
        raise OperationError('FOUNDATION_NOT_READY', 503) from None


def _logical_kubectl(foundation, *args):
    import logical_foundation
    return subprocess.run(logical_foundation.kube_argv(foundation) + list(args),
        check=True, capture_output=True, text=True, timeout=TIMEOUT).stdout


def _logical_status(size, foundation):
    """One batch metadata observation; never borrow the Provider's compute probe."""
    import logical_foundation
    from warehouse_operations import OperationError
    try:
        items = json.loads(_logical_kubectl(foundation, 'get', 'deployment/trino-local',
            'deployment/trino-local-worker', '-o', 'json'))['items']
        names = ('trino-local', 'trino-local-worker')
        if len(items) != 2 or {o['metadata']['name'] for o in items} != set(names):
            raise ValueError()
        deployments = {o['metadata']['name']: o for o in items}
        active, stopped, replicas, ready = True, True, 0, 0
        for name, target in zip(names, (1, foundation['trino']['workers'])):
            obj = deployments[name]
            meta, spec, observed = obj['metadata'], obj['spec'], obj.get('status', {})
            labels = meta.get('labels', {})
            if (obj.get('kind') != 'Deployment' or not meta.get('uid') or meta.get('deletionTimestamp')
                    or meta.get('namespace') != foundation['kube']['namespace']
                    or labels.get(logical_foundation.LABEL) != foundation['foundation_id']
                    or labels.get(logical_foundation.REVISION) != str(foundation['revision'])):
                raise ValueError()
            desired = spec['replicas']
            generation, observed_generation = meta['generation'], observed.get('observedGeneration', 0)
            counts = [observed.get(k, 0) for k in
                      ('replicas', 'readyReplicas', 'updatedReplicas', 'availableReplicas')]
            if (any(type(n) is not int or n < 0 for n in [desired, *counts, observed_generation])
                    or type(generation) is not int or generation < 1):
                raise ValueError()
            current = observed_generation >= generation
            active &= current and desired == target and all(n == target for n in counts)
            stopped &= current and desired == 0 and all(n == 0 for n in counts)
            replicas += desired
            ready += counts[1]
        return dict(warehouse=warehouse(size), backend='trino-local', deployment='trino-local',
            namespace=foundation['kube']['namespace'], state='active' if active else 'chilled' if stopped else 'starting',
            replicas=replicas, ready_replicas=ready)
    except Exception:
        raise OperationError('WAREHOUSE_UNAVAILABLE', 503) from None


def status_from_deployment(size, obj):
    """Calculate warehouse state from an already acquired deployment."""
    size = warehouse(size)
    replicas = obj.get("spec", {}).get("replicas", 1)
    ready = obj.get("status", {}).get("readyReplicas", 0)
    state = "chilled" if replicas == 0 else "active" if ready >= 1 else "starting"
    import runtime_profile
    namespace = 'shirokuma-dev'
    if runtime_profile.current() is not None and runtime_profile.current().mode == 'logical_v1':
        import logical_foundation
        namespace = logical_foundation.load(runtime_profile.logical_config()['provider_config_file'])['kube']['namespace']
    return dict(warehouse=warehouse(size), backend="trino-local", deployment="trino-local", namespace=namespace,
                state=state, replicas=replicas, ready_replicas=ready)


def wait_for(check):
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.2)
    raise TimeoutError("resident warehouse readiness timed out")


def http_json(url, data=None, headers=None, timeout=30, *, context=None):
    parsed = urllib.parse.urlsplit(url)
    if urllib.request.urlopen is not _DEFAULT_URLOPEN or parsed.scheme != "http" or not parsed.hostname:
        request = urllib.request.Request(url, data=data, headers=headers or {})
        options = {'context': context} if context is not None else {}
        with urllib.request.urlopen(request, timeout=timeout, **options) as response:
            return json.load(response)
    conns = getattr(_HTTP_POOL, "conns", None)
    if conns is None:
        conns = _HTTP_POOL.conns = {}
    key = (parsed.hostname, parsed.port or 80)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    method = "POST" if data is not None else "GET"
    req_headers = dict(headers or {})
    req_headers.setdefault("Connection", "keep-alive")
    for attempt in range(2):
        conn = conns.get(key)
        if conn is None:
            conn = conns[key] = http.client.HTTPConnection(key[0], key[1], timeout=timeout)
        else:
            conn.timeout = timeout
        try:
            conn.request(method, path, body=data, headers=req_headers)
            response = conn.getresponse()
            payload = response.read()
            if response.status >= 400:
                raise urllib.error.HTTPError(url, response.status, response.reason, response.headers, None)
            return json.loads(payload)
        except (http.client.HTTPException, OSError) as exc:
            conn.close()
            conns.pop(key, None)
            if method == 'POST' or isinstance(exc, urllib.error.HTTPError) or attempt == 1:
                raise


def trino_http_json(url, data=None, headers=None, timeout=30):
    """Apply warehouse credentials only on explicitly Trino-owned requests."""
    import request_auth
    import trino_auth
    if data is not None and admission.current() is None:
        raise admission.AdmissionConflict('SQL submission requires a durable admission')
    headers = trino_auth.backend_headers(headers, role=request_auth.current_role())
    trino_auth.validate_backend_url(url)
    options = {'context': trino_auth.backend_ssl_context()} if url.startswith('https:') else {}
    return http_json(url, data, headers, timeout, **options)


def _sql_kubectl():
    """Bind managed SQL to sealed enrolled inputs before opening a transport."""
    import database_creation_bridge as bridge
    import runtime_profile
    from warehouse_operations import OperationError
    if runtime_profile.current() is not None and runtime_profile.current().mode == 'logical_v1':
        import logical_foundation
        config = runtime_profile.logical_config()
        foundation = logical_foundation.load(config['provider_config_file'])
        if any(foundation[k] != config[v] for k, v in (
                ('environment_id', 'environment_id'), ('warehouse_id', 'warehouse_id'),
                ('foundation_id', 'foundation_generation_id'), ('revision', 'foundation_revision'))):
            raise OperationError('FOUNDATION_NOT_READY', 503)
        return logical_foundation.kube_argv(foundation)
    if bridge._managed is None:
        if os.environ.get('SHIROKUMA_MANAGED_FD') is not None:
            raise OperationError('OPERATOR_UNAVAILABLE', 503)
        return KUBECTL
    from database_creation_worker import validate_candidate_profile
    try:
        managed = bridge.current()
        if type(managed) is not bridge.Managed or managed.hello['purpose'] != 'http':
            raise ValueError('Managed HTTP owner required')
        managed.instance()  # live FD/owner plus unchanged startup runtime material
        path = managed.hello['profile_path']
        if (os.environ.get('SHIROKUMA_RUNTIME_PROFILE') != path or
                runtime_profile.current() != runtime_profile.load(path)):
            raise ValueError('Current managed profile differs')
        config = managed.config
        preparation = config.preparation()
        if path == config.value['base_runtime_profile']:
            if runtime_profile.current() != config.base:
                raise ValueError('Enrolled base differs')
        else:
            operator = validate_candidate_profile(config, path, hello=managed.hello)
            if operator.config['kubeconfig'] != str(preparation.kubeconfig):
                raise ValueError('Sealed foundation differs')
        foundation = config.value['foundation']
        argv = ['kubectl', '--kubeconfig', str(preparation.kubeconfig),
                '--context', foundation['kube_context']]

        def kubectl(*args):
            result = subprocess.run(['rtk', 'proxy', *argv, '--request-timeout=10s', *args],
                                    capture_output=True, text=True, timeout=15)
            if result.returncode:
                raise ValueError('Foundation unavailable')
            return json.loads(result.stdout)

        view = kubectl('config', 'view', '--minify', '--raw', '-o', 'json')
        cluster = view['clusters'][0]['cluster']
        import base64
        import hashlib
        ca = base64.b64decode(cluster['certificate-authority-data'], validate=True)
        if (view['current-context'] != foundation['kube_context']
                or cluster['server'] != foundation['api_server']
                or hashlib.sha256(ca).hexdigest() != foundation['ca_sha256']
                or cluster.get('insecure-skip-tls-verify')):
            raise ValueError('Foundation identity differs')
        nodes = kubectl('get', 'nodes', '-o', 'json')
        if {n['metadata']['name']: n['metadata']['uid'] for n in nodes['items']} != foundation['node_uids']:
            raise ValueError('Foundation nodes differ')
        if runtime_profile.private_json(preparation.tofu_state)['lineage'] != foundation['tofu_lineage']:
            raise ValueError('Foundation lineage differs')
        managed.require()
        return argv + ['-n', 'shirokuma-dev']
    except Exception:
        raise OperationError('OPERATOR_UNAVAILABLE', 503) from None


@contextmanager
def _trino_connection():
    import trino_auth
    kubectl_argv = _sql_kubectl()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(kubectl_argv + ["port-forward", "--address", "127.0.0.1",
                                        "svc/trino-local", f"{port}:8080"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    scheme = 'https' if trino_auth.password_mode() else 'http'
    url = f"{scheme}://127.0.0.1:{port}"
    try:
        database_auth = trino_auth.configured_database_mode()
        def ready():
            if process.poll() is not None:
                raise RuntimeError("Trino port-forward exited")
            try:
                if database_auth:
                    return http_json(url + '/v1/info', context=trino_auth.backend_ssl_context()).get('starting') is False
                return trino_http_json(url + "/v1/info").get("starting") is False
            except (OSError, urllib.error.URLError):
                return False
        wait_for(ready)
        yield dict(process=process, url=url, kubectl=kubectl_argv)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


@contextmanager
def persistent_trino():
    """Own a reusable tunnel for a server's lifetime; nested callers borrow it."""
    global _PERSISTENT_TRINO
    if _borrowable_trino():
        yield _PERSISTENT_TRINO['url']
        return
    previous = _PERSISTENT_TRINO
    with _trino_connection() as connection:
        _PERSISTENT_TRINO = connection
        try:
            yield connection['url']
        finally:
            _PERSISTENT_TRINO = previous


@contextmanager
def trino():
    if _borrowable_trino():
        yield _PERSISTENT_TRINO['url']
        return
    with _trino_connection() as connection:
        yield connection['url']


def _borrowable_trino():
    if not _PERSISTENT_TRINO or _PERSISTENT_TRINO['process'].poll() is not None:
        return False
    argv = _sql_kubectl()
    return argv is KUBECTL or _PERSISTENT_TRINO.get('kubectl') == argv


def telemetry(action, duration, status="success", prefix="warehouse.", **extra):
    try:
        from agenthouse.manage import Client, event, insert
        fields = dict(action=prefix + action, target="trino-warehouse",
                      status=status, agent_id="shirokuma-warehouse",
                      task_id=str(uuid.uuid4()), trace_id=uuid.uuid4().hex,
                      duration_ms=duration)
        fields.update({key: value for key, value in extra.items() if value is not None})
        insert(Client(), event(**fields))
        return "recorded"
    except Exception:
        return "skipped"


def finish(report, started):
    report["backend"] = "trino-local"
    report["duration_ms"] = int((time.monotonic() - started) * 1000)
    report["telemetry"] = telemetry(report["action"], report["duration_ms"])
    return report


def transition(action, size):
    with lifecycle_mutation(accepted_wake=action == 'wake') as db:
        import logical_creations
        if logical_creations.enabled():
            return _logical_transition(action, size, _logical_foundation(db), db)
        return _transition(action, size)


def _logical_transition(action, size, foundation, db):
    if action not in ('chill', 'wake'):
        raise ValueError('Invalid warehouse action')
    started = time.monotonic()
    expected = _logical_wake_expected(foundation, db) if action == 'wake' else None
    before = _logical_status(size, foundation)
    target_state = 'chilled' if action == 'chill' else 'active'
    noop = before['state'] == target_state
    targets = (0, 0) if action == 'chill' else (1, foundation['trino']['workers'])
    if not noop:
        for name, replicas in zip(('trino-local', 'trino-local-worker'), targets):
            if _logical_foundation(db) != foundation:
                from warehouse_operations import OperationError
                raise OperationError('FOUNDATION_NOT_READY', 503)
            _logical_kubectl(foundation, 'scale', 'deployment/' + name, f'--replicas={replicas}')
        wait_for(lambda: _logical_status(size, foundation)['state'] == target_state)
    if action == 'wake':
        _logical_wake_ready(foundation, db, expected)
        touch_activity()
    return finish(dict(warehouse=warehouse(size), action=action, result='noop' if noop else 'verified',
        before_replicas=before['replicas'], after_replicas=sum(targets), state=target_state), started)


def _logical_wake_expected(foundation, db):
    """Validate the saved proof before any wake observation or scale dispatch."""
    import runtime_profile
    from warehouse_operations import OperationError
    if _logical_foundation(db) != foundation:
        raise OperationError('FOUNDATION_NOT_READY', 503)
    try:
        registered = db.execute('SELECT verification_ref FROM logical_foundations WHERE environment_id=? AND warehouse_id=?',
            (foundation['environment_id'], foundation['warehouse_id'])).fetchone()
        saved = runtime_profile.private_json(registered['verification_ref'])
        expected = saved['result']
        incarnation = expected['incarnation']
        prefix = foundation['prefix']
        pvcs = ('trino-runtime', 'trino-catalogs', prefix+'-seaweed-data', prefix+'-postgres-data')
        pins = {'namespace', 'namespace/'+foundation['kube']['namespace'],
            'configmap/'+prefix+'-secrets-code', *pvcs, *('pvc/'+name for name in pvcs),
            *('service/'+name for name in (prefix+'-seaweed', prefix+'-polaris', 'trino-local')),
            *('deployment/'+name for name in ('trino-local', 'trino-local-worker', 'trino-opa',
                prefix+'-postgres', prefix+'-seaweed', prefix+'-polaris'))}
        names = {'target.'+name for name in ('credentials.properties', 'password.db', 'rules.json', 'guard.rego')}
        if (type(saved) is not dict or saved.get('kind') != 'foundation_probe'
                or type(saved.get('generation_id')) is not str or saved['generation_id'] != foundation['foundation_id']
                or type(saved.get('revision')) is not int or saved['revision'] != foundation['revision']
                or type(expected) is not dict or expected.get('ready') is not True
                or expected.get('foundation_id') != foundation['foundation_id']
                or type(expected.get('revision')) is not int or expected['revision'] != foundation['revision']
                or type(incarnation) is not dict or incarnation.get('node') != foundation['kube']['node_uids']
                or not pins <= incarnation.keys() or sum(key.startswith('pv/') for key in incarnation) != 4
                or type(expected.get('spi_sha256')) is not str
                or re.fullmatch('[0-9a-f]{64}', expected['spi_sha256']) is None
                or type(expected.get('readiness')) is not dict
                or any(expected['readiness'].get(key) is not True for key in
                    ('opa', 'storage', 'polaris', 'trino', 'runtime_files', 'spi'))
                or type(expected.get('hashes')) is not dict or set(expected['hashes']) != names
                or any(type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None
                    for value in expected['hashes'].values())
                or any(type(value) is not str or not value.strip() for key, value in incarnation.items()
                    if key == 'namespace' or key in pvcs or
                    key.startswith(('namespace/', 'service/', 'deployment/', 'pvc/', 'pv/', 'configmap/')))):
            raise ValueError()
    except Exception:
        raise OperationError('FOUNDATION_NOT_READY', 503) from None
    return expected


def _logical_wake_ready(foundation, db, expected):
    """Wait for the registered engine proof using one owned Provider lifetime."""
    from logical_provider import Provider, Rejected
    from warehouse_operations import OperationError
    if _logical_foundation(db) != foundation:
        raise OperationError('FOUNDATION_NOT_READY', 503)
    with Provider(foundation) as provider:
        def engine_ready():
            if _logical_foundation(db) != foundation:
                raise OperationError('FOUNDATION_NOT_READY', 503)
            try:
                provider.bind_registered(expected)
            except Rejected as error:
                # The saved proof was validated before wake. Full probe unready
                # collapses to INVALID; identity/UID mismatches remain permanent.
                if error.code == 'FOUNDATION_BINDING_INVALID':
                    return False
                raise
            return True
        wait_for(engine_ready)


def _transition(action, size):
    started = time.monotonic()
    before = status(size)
    chilled = action == "chill"
    noop = before["replicas"] == 0 if chilled else before["state"] == "active"
    if not noop:
        kubectl("scale", "deployment/trino-local", f"--replicas={0 if chilled else 1}")
        if chilled:
            def stopped():
                obj = deployment()
                observed = obj.get("status", {})
                return (obj["spec"]["replicas"] == 0 and
                        observed.get("replicas", 0) == 0 and
                        observed.get("readyReplicas", 0) == 0)
            wait_for(stopped)
        else:
            kubectl("rollout", "status", "deployment/trino-local", f"--timeout={TIMEOUT}s")
            with trino():
                pass
    if action == 'wake':
        touch_activity()
    return finish(dict(warehouse=warehouse(size), action=action,
                       result="noop" if noop else "verified", before_replicas=before["replicas"],
                       after_replicas=0 if chilled else 1,
                       state="chilled" if chilled else "active"), started)


def execute(url, sql, headers, max_rows=1000, *, context=None, principal=None):
    if _profile_active():
        raise ValueError('Use the authenticated query service with an explicit context')
    with warehouse_operation(sql=True), warehouse_activity():
        return _execute(url, sql, headers, max_rows)


def _statement_page(page, expected_id=None):
    """Only an identifiable, consistent Trino statement page is terminal proof."""
    if not isinstance(page, dict):
        raise RuntimeError('Invalid Trino statement page')
    query_id, stats = page.get('id'), page.get('stats')
    states = {'QUEUED', 'WAITING_FOR_RESOURCES', 'DISPATCHING', 'PLANNING',
              'STARTING', 'RUNNING', 'FINISHING', 'FINISHED', 'FAILED', 'CANCELED'}
    if (not isinstance(query_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', query_id) or
            expected_id is not None and expected_id != query_id or
            not isinstance(stats, dict) or not isinstance(stats.get('state'), str) or stats['state'] not in states or
            'data' in page and not isinstance(page['data'], list) or
            'columns' in page and not isinstance(page['columns'], list) or
            page.get('error') is not None and not isinstance(page['error'], dict) or
            page.get('nextUri') is not None and (not isinstance(page['nextUri'], str) or not page['nextUri'])):
        raise RuntimeError('Invalid Trino statement page')
    if not page.get('nextUri') and stats['state'] not in {'FINISHED', 'FAILED', 'CANCELED'}:
        raise RuntimeError('Invalid Trino terminal page')
    if page.get('error') is not None and (not isinstance(page['error'].get('message'), str) or
            stats['state'] != 'FAILED' or page.get('nextUri')):
        raise RuntimeError('Invalid Trino error page')
    return query_id, 'RUNNING' if page.get('nextUri') else stats['state']


def _execute(url, sql, headers, max_rows=1000):
    # None drains the query to completion without retaining result rows for jobs.
    import request_auth
    import trino_auth
    headers = trino_auth.backend_headers(headers, role=request_auth.current_role())
    rows, columns, query_id, truncated = [], [], None, False
    row_count = 0
    page = trino_http_json(url + "/v1/statement", sql.encode("utf-8"), headers)
    while True:
        query_id, state = _statement_page(page, query_id)
        if state == 'FAILED':
            if admission.current() is not None:
                _operation_finish(admission.current(), 'FAILED')
            error = page.get('error') or {}
            raise TrinoQueryError(error.get('message', 'backend failed'), error.get('errorName'))
        if state == 'CANCELED' and admission.current() is not None:
            _operation_finish(admission.current(), 'CANCELED')
        columns = page.get("columns", columns)
        data, next_uri = page.get("data", []), page.get("nextUri")
        if max_rows is None:
            row_count += len(data)
        else:
            remaining = max_rows - len(rows)
            rows.extend(data if len(data) <= remaining else data[:remaining])
            truncated = len(data) > remaining or (len(rows) == max_rows and bool(next_uri))
        if truncated:
            if next_uri:
                try:
                    trino_auth.validate_backend_url(next_uri)
                    request = urllib.request.Request(next_uri, headers=headers, method="DELETE")
                    options = {'context': trino_auth.backend_ssl_context()} if next_uri.startswith('https:') else {}
                    with urllib.request.urlopen(request, timeout=30, **options):
                        pass
                    if admission.current() is not None:
                        _operation_finish(admission.current(), 'CANCELED')
                except Exception:
                    if admission.current() is not None:
                        _operation_finish(admission.current(), 'UNKNOWN')
            break
        if not next_uri:
            break
        page = trino_http_json(next_uri, headers=headers)
    result = dict(query_id=query_id, columns=columns, rows=rows, truncated=truncated)
    if max_rows is None:
        result['row_count'] = row_count
    return result


def normalize_trino_sql(sql: str, *, context=None) -> str:
    """Wrap a top-level QUALIFY filter, leaving other SQL byte-for-byte intact."""
    if context is not None:
        return sql
    pattern = r"--[^\n]*|/\*[\s\S]*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|[A-Za-z_][A-Za-z_0-9]*|[^\s]"
    top, depth = [], 0
    for match in re.finditer(pattern, sql):
        token = match.group()
        if token.startswith(('--', '/*', "'", '"')):
            continue
        if token == '(':
            depth += 1
        elif token == ')':
            depth -= 1
        elif depth == 0:
            top.append((token.upper(), match.start()))
    if not top or top[0][0] not in {'SELECT', 'WITH'}:
        return sql
    for i, (token, start) in enumerate(top):
        if token != 'QUALIFY':
            continue
        end = len(sql)
        for j in range(i + 1, len(top)):
            if top[j][0] == 'LIMIT' or (top[j][0] == 'ORDER' and
                    j + 1 < len(top) and top[j + 1][0] == 'BY'):
                end = top[j][1]
                break
        # Keep a newline before the closing parenthesis if the body ends in a
        # line comment, so that the wrapper cannot become part of that comment.
        body = sql[:start].rstrip(' \t')
        condition = sql[start + len('QUALIFY'):end].strip(' \t')
        tail = ' ' + sql[end:] if end < len(sql) else ''
        return f'SELECT * FROM ({body}) AS _qualify_sub WHERE {condition}{tail}'
    return sql


def resolve_role(user: str, role: str = None) -> str:
    aliases = {'admin': 'admin', 'data_engineer': 'data_engineer', 'editor': 'data_engineer',
               'bi_viewer': 'bi_viewer', 'viewer': 'bi_viewer', 'bi_reader': 'bi_viewer'}
    user = (user or '').strip().lower()
    default = os.environ.get('SHIROKUMA_DEFAULT_ROLE', 'admin').strip().lower()
    if default not in aliases:
        raise ValueError(f'Invalid role: {default}')
    # The admin default is the trusted local single-user mode. Request headers
    # and user names are selectors, not verified identities in restricted mode.
    maximum = 'bi_viewer' if _is_bi_user(user) else aliases[default]
    if role:
        if role.strip().lower() not in aliases:
            raise ValueError(f'Invalid role: {role}')
        resolved = aliases[role.strip().lower()]
        ranks = {'bi_viewer': 0, 'data_engineer': 1, 'admin': 2}
        if ranks[resolved] > ranks[maximum]:
            raise ValueError('User cannot assume role: ' + role)
        return resolved
    return maximum


def _check_dynamic_sql(sql):
    pattern = r"--[^\n]*|/\*[\s\S]*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|[A-Za-z_][A-Za-z_0-9]*|[^\s]"
    tokens = [t.upper() for t in re.findall(pattern, sql) if not t.startswith(('--', '/*'))]
    ident = r'(?:[A-Z_][A-Z_0-9]*|"(?:[^"]|"")+")'
    rollback = re.fullmatch(
        rf'ALTER TABLE {ident}(?: \. {ident}){{2}} EXECUTE ROLLBACK_TO_SNAPSHOT \( [0-9 ]+ \)(?: ;)?',
        ' '.join(tokens), re.S)
    # Inspect statement positions, not column names such as SELECT execute.
    statements = [[]]
    for token in tokens:
        if token == ';':
            statements.append([])
        else:
            statements[-1].append(token)
    for statement in statements:
        words = list(statement)
        while words and words[0] in {'EXPLAIN', 'ANALYZE', 'VERBOSE'}:
            words.pop(0)
            if words[:1] == ['(']:
                depth = 0
                for i, token in enumerate(words):
                    depth += (token == '(') - (token == ')')
                    if depth == 0:
                        words = words[i + 1:]
                        break
                else:
                    words = []
        if (words and words[0] in {'PREPARE', 'DEALLOCATE', 'EXECUTE'} or
                (words[:1] == ['ALTER'] and 'EXECUTE' in words and not rollback)):
            raise ValueError('SQL policy violation: dynamic SQL and prepared statements are not allowed')


def check_sql_policy(sql: str, mode: str = 'read-only', role: str = 'admin', *, context=None,
                     database_only=False, _platform_authorized=False) -> str:
    """Check a single statement before any warehouse access and return clean SQL."""
    if mode not in {'read-only', 'sandbox-write', 'admin-write'}:
        raise ValueError(f"unknown SQL guardrail mode: {mode}")
    _check_dynamic_sql(sql)
    # Only the authenticated caller may supply platform authority. Context
    # metadata (including a null epoch) never establishes that authority.
    platform = _platform_authorized is True and role == 'admin' and not database_only
    if context is not None:
        if role != context.selected_role:
            raise ValueError('SQL context role mismatch')
        _check_bi_schemas(sql, context=context, database_only=database_only,
                          _platform_authorized=platform)
    if role == 'bi_viewer' and context is None:
        if mode != 'read-only':
            raise ValueError('BI Viewer requires read-only mode')
        return check_bi_policy(sql, 'bi_reader')
    if role == 'data_engineer' and mode == 'admin-write':
        raise ValueError('Data Engineer cannot use admin-write mode')
    if role == 'bi_viewer' and mode != 'read-only':
        raise ValueError('BI Viewer requires read-only mode')

    def reject(reason):
        raise ValueError(f"SQL policy violation ({mode}): {reason}")

    statement = sql.rstrip().rstrip('; \t\r\n')
    statement = normalize_trino_sql(statement, context=context)
    # Preserve quoted identifiers, mask literals, and ignore SQL comments. Doubled
    # quotes are SQL escapes; semicolons inside either quote type are harmless.
    tokens = []
    pattern = r"--[^\n]*|/\*[\s\S]*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|[A-Za-z_][A-Za-z_0-9]*|[0-9]+|[^\s]"
    for match in re.finditer(pattern, statement):
        token = match.group()
        if token.startswith(('--', '/*')):
            continue
        if token == ';':
            reject('multiple statements are not allowed')
        tokens.append("'literal'" if token.startswith("'") else token)
    keywords = {t.upper() for t in tokens if re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', t)}
    if context is not None and not platform:
        words = [t.upper() for t in tokens]
        # The reference guard checks qualified objects. These commands have no
        # object reference and must not inherit role-name-based admin authority.
        for index, word in enumerate(words):
            if word == 'SHOW' and words[index + 1:index + 2] not in (
                    ['TABLES'], ['SCHEMAS'], ['CREATE'], ['COLUMNS']):
                reject('global or session metadata is not permitted')
            if word in {'SET', 'RESET', 'USE', 'SESSION'} and words[:1] not in (['SELECT'], ['WITH']):
                reject('system or session command is not permitted')
    if context is not None and (not tokens or tokens[0].upper() not in
            {'SELECT', 'WITH', 'DESCRIBE', 'EXPLAIN', 'SHOW', 'CREATE', 'INSERT', 'ALTER'}):
        reject('statement is not supported')
    destructive = keywords & {'DROP', 'TRUNCATE', 'DELETE', 'GRANT', 'REVOKE'}
    if destructive:
        reject('destructive statement: ' + ', '.join(sorted(destructive)))
    normalized = ' '.join(tokens)
    ident = r'(?:[A-Za-z_][A-Za-z_0-9]*|"(?:[^"]|"")+")'
    if re.fullmatch(rf'SHOW CREATE (?:TABLE|VIEW) {ident}(?:\s*\.\s*{ident}){{0,2}}', normalized, re.I):
        return sql if context is not None else statement
    mutations = keywords & {'INSERT', 'UPDATE', 'MERGE', 'CREATE', 'ALTER', 'CALL'}
    if not mutations:
        return sql if context is not None else statement
    if mode == 'read-only':
        reject('write statements are not allowed')

    # Match only the supported write forms, including quoted qualified names.
    def name(value):
        return rf'(?:{value}|"{value}")'
    dot = r'\s*\.\s*'
    catalog = name(context.sql_catalog if context is not None else 'iceberg') + dot
    writable = ('sandbox', 'raw') if mode == 'sandbox-write' else WRITABLE_SCHEMAS
    schemas = '(?:' + '|'.join(name(s) for s in writable) + ')'
    target = catalog + schemas + dot + ident
    schema_target = None
    if context is not None:
        import control_store
        with control_store.connect() as db:
            registered = db.execute('SELECT d.sql_catalog,s.sql_name,s.purpose FROM databases d JOIN schemas s ON d.id=s.database_id').fetchall()
        registered = [(c, s) for c, s, purpose in registered
                      if (platform or c == context.sql_catalog and purpose == s)
                      and (not database_only or c == context.sql_catalog)
                      and (mode == 'admin-write' or s in writable)]
        schema_target = '(?:' + '|'.join(name(c) + dot + name(s) for c, s in registered) + ')'
        target = schema_target + dot + ident
        schemas = '(?:' + '|'.join(name(s) for c, s in registered if c == context.sql_catalog) + ')'
        target = '(?:' + target + '|' + schemas + dot + ident
        if (context.sql_catalog, context.sql_schema) in registered:
            target += '|' + ident
        target += ')'
    schema = schemas if mode == 'sandbox-write' else ident
    rollback_target = catalog + schema + dot + ident
    if context is not None:
        rollback_target = target
    allowed = [
        rf'CREATE SCHEMA IF NOT EXISTS {schema_target or catalog + schema}',
        rf'CREATE (?:OR REPLACE )?TABLE (?:IF NOT EXISTS )?{target}(?=\s|\(|$)(?!\s*\.).*',
        rf'INSERT INTO {target}(?=\s|\(|$)(?!\s*\.).*',
        rf'ALTER TABLE {rollback_target} EXECUTE rollback_to_snapshot\s*\(\s*[0-9]+\s*\)',
    ]
    if not any(re.fullmatch(p, normalized, re.I | re.S) for p in allowed):
        reject('write target or operation is not permitted')
    return sql if context is not None else statement


def _is_bi_user(user):
    return user.lower() == 'powerbi' or user.lower().startswith('bi_')


def _check_bi_schemas(sql, *, context=None, database_only=False, _platform_authorized=False):
    # Tokenize so comments, strings, whitespace and quoted names cannot hide a
    # restricted schema. String literals and quoted names remain distinct.
    pattern = r"--[^\n]*|/\*[\s\S]*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|[A-Za-z_][A-Za-z_0-9]*|[^\s]"
    raw_tokens = [t for t in re.findall(pattern, sql) if not t.startswith(('--', '/*'))]
    tokens = [t[1:-1].replace('""', '"').lower() if t.startswith('"') else t.lower()
              for t in raw_tokens]
    def reject():
        raise ValueError('SQL policy violation (bi_reader): query restricted to iceberg.marts and metadata')

    def allowed(parts):
        if context is not None:
            catalog, schema = (context.sql_catalog, context.sql_schema)
            if len(parts) == 1:
                parts = [catalog, schema] + parts
            elif len(parts) == 2:
                parts = [catalog] + parts
            if len(parts) != 3:
                return False
            if (database_only or not _platform_authorized) and parts[0] != catalog:
                return False
            if _platform_authorized:
                import control_store
                with control_store.connect() as db:
                    return db.execute('SELECT 1 FROM schemas s JOIN databases d ON s.database_id=d.id WHERE d.sql_catalog=? AND s.sql_name=?', parts[:2]).fetchone() is not None
            if parts[0] != catalog or parts[1] not in (
                    ('marts',) if context.selected_role == 'bi_viewer' else ('raw', 'sandbox', 'staging', 'marts')):
                return False
            import control_store
            with control_store.connect() as db:
                return db.execute('''SELECT 1 FROM schemas WHERE database_id=?
                    AND sql_name=? AND purpose=sql_name''', (context.database_id, parts[1])).fetchone() is not None
        if len(parts) == 1:
            return True  # Unqualified tables use the enforced marts session schema.
        if len(parts) == 2:
            return parts[0] in {'marts', 'information_schema', 'sys'}
        return (len(parts) == 3 and
                ((parts[0] == 'iceberg' and parts[1] == 'marts') or
                 parts[1] in {'information_schema', 'sys'} or
                 (parts[0] == 'system' and parts[1] in {'runtime', 'metadata', 'jdbc'})))

    # Consume names and aliases in relation position before interpreting clause
    # words. Trino permits non-reserved names such as `limit` in both positions.
    depth, from_depths = 0, set()
    consumed, relation_groups = set(), set()
    clauses = {'where', 'group', 'order', 'having', 'union', 'except', 'intersect',
               'window', 'join', 'left', 'right', 'full', 'inner', 'cross', 'on',
               'using', 'natural', 'tablesample', 'for'}

    def identifier_token(index):
        return index < len(tokens) and (raw_tokens[index].startswith('"') or
                bool(re.fullmatch(r'[a-z_][a-z_0-9$]*', tokens[index])))

    def clause_at(index):
        if index >= len(tokens) or raw_tokens[index].startswith('"'):
            return False
        if tokens[index - 1:index] == ['.'] or tokens[index + 1:index + 2] == ['.']:
            return False
        word = tokens[index]
        following = tokens[index + 1:index + 2]
        if word in {'where', 'having', 'union', 'except', 'intersect'}:
            return True
        if word in {'group', 'order'}:
            return following == ['by']
        if word in {'limit', 'offset'}:
            return bool(following and (following[0].isdigit() or following[0] in {'all', '?'}))
        if word == 'fetch':
            return bool(following and following[0] in {'first', 'next'})
        if word == 'window':
            return identifier_token(index + 1) and tokens[index + 2:index + 3] == ['as']
        return False

    def alias_after(index):
        if tokens[index:index + 1] == ['as']:
            consumed.add(index)
            if not identifier_token(index + 1):
                reject()
            consumed.add(index + 1)
            return
        if identifier_token(index) and (raw_tokens[index].startswith('"') or
                (tokens[index] not in clauses - {'window', 'group', 'order'} and not clause_at(index))):
            consumed.add(index)

    for i, token in enumerate(tokens):
        if i in consumed or raw_tokens[i].startswith(('"', "'")):
            continue
        if token == '(':
            depth += 1
        elif token == ')':
            from_depths.discard(depth)
            if depth in relation_groups:
                relation_groups.discard(depth)
                alias_after(i + 1)
            depth -= 1
            if depth < 0:
                reject()
        elif clause_at(i):
            from_depths.discard(depth)
        if token == 'from':
            from_depths.add(depth)
        show_context = raw_tokens[0].lower() == 'show' and token in {'in', 'for'}
        show_create_view = i == 2 and [t.lower() for t in raw_tokens[:3]] == ['show', 'create', 'view']
        show_create_schema = i == 2 and [t.lower() for t in raw_tokens[:3]] == ['show', 'create', 'schema']
        reference = token in {'from', 'join', 'describe', 'table'} or show_create_view or show_create_schema or show_context or (token == ',' and depth in from_depths)
        if not reference:
            continue
        j = i + 1
        while tokens[j:j + 1] == ['(']:
            relation_groups.add(depth + j - i)
            j += 1
        if tokens[j:j + 1] in (['select'], ['with'], ['values']):
            continue  # Nested FROM clauses are checked by the same traversal.
        if not identifier_token(j):
            reject()
        parts = [tokens[j]]
        consumed.add(j)
        while j + 1 < len(tokens) and tokens[j + 1] == '.':
            if not identifier_token(j + 2):
                reject()
            consumed.update((j + 1, j + 2))
            parts.append(tokens[j + 2])
            j += 2
        create_target = token == 'table' and tokens[:1] == ['create'] and 'select' not in tokens[:i]
        if tokens[j + 1:j + 2] == ['('] and not create_target:
            if parts == ['unnest'] and raw_tokens[j].lower() == 'unnest':
                relation_groups.add(depth + j - i)
                continue  # Arguments still traverse the same nested-reference checks.
            reject()  # Table functions require separate authorization semantics.
        alias_after(j + 1)
        prefix = [t.lower() for t in raw_tokens[:2]]
        if show_create_schema or prefix == ['show', 'tables'] and token in {'from', 'in'}:
            if not allowed(parts + ['_table']):
                reject()
        elif prefix == ['show', 'schemas'] and token in {'from', 'in'}:
            if parts not in ([context.sql_catalog],) if context is not None else parts not in (['iceberg'], ['system']):
                reject()
        elif not allowed(parts):
            reject()
    if depth:
        reject()


def check_bi_policy(sql: str, user: str, role=None) -> str:
    _check_dynamic_sql(sql)
    if (role or resolve_role(user)) != 'bi_viewer':
        return normalize_trino_sql(sql.rstrip().rstrip('; \t\r\n'))
    statement = check_sql_policy(sql, mode='read-only')
    clean = re.sub(r'--[^\n]*|/\*[\s\S]*?\*/', ' ', statement).lstrip()
    if not re.match(r'(SELECT|WITH|DESCRIBE|EXPLAIN|SHOW)\b', clean, re.I):
        raise ValueError('SQL policy violation (bi_reader): statement must be SELECT, WITH, DESCRIBE, EXPLAIN, or SHOW')
    _check_bi_schemas(statement)
    return statement


def _profile_active():
    try:
        import runtime_profile
        return runtime_profile.active()
    except ImportError:
        return False


def _native_request(headers, path, method, body, endpoint, *, warehouse_id='LOCAL'):
    """Authenticated native protocol, using the same owner-bound page service."""
    import query_service as service
    import query_context
    import request_auth
    import control_store
    parsed = urllib.parse.urlsplit(path)
    if parsed.query or parsed.fragment or parsed.scheme or parsed.netloc:
        raise service.QueryError('QUERY_NOT_FOUND', 404)
    principal = request_auth.authenticate_principal(headers)
    def single(name, required=True):
        values = headers.get_all(name, []) if hasattr(headers, 'get_all') else [v for k, v in headers.items() if k.lower() == name.lower()]
        if (len(values) != 1) if required else (len(values) > 1):
            raise service.QueryError('UNKNOWN_CONTEXT', 400)
        return values[0] if values else None
    role = single('X-Shirokuma-Role', required=False)
    if role is not None and role not in request_auth.ROLES:
        raise service.QueryError('UNKNOWN_CONTEXT', 400)
    if path in ('/api/v1/runtime', '/api/v1/admin/database-creations/runtime-instance'):
        if method != 'GET' or body:
            raise service.QueryError('QUERY_NOT_FOUND', 404)
        import catalog_admin
        import runtime_profile
        try:
            catalog_admin.require_admin(principal, role or principal.maximum_role)
        except PermissionError:
            raise service.QueryError('OPERATION_FORBIDDEN', 403) from None
        if path.endswith('/runtime-instance'):
            import database_creation_bridge
            try:
                return database_creation_bridge.runtime_instance()
            except Exception:
                raise service.QueryError('OPERATOR_UNAVAILABLE',503) from None
        return runtime_profile.generation_attestation()
    if path in ('/v1/info', '/v1/statement'):
        role = role or principal.maximum_role
        if request_auth.ROLES[role] > request_auth.ROLES[principal.maximum_role]:
            raise service.QueryError('DB_ACCESS_REVOKED', 403)
    selected_mode = single('X-Shirokuma-Mode', required=False)
    if selected_mode is not None and selected_mode not in ('read-only', 'sandbox-write', 'admin-write'):
        raise service.QueryError('UNKNOWN_CONTEXT', 400)
    forbidden = {'role', 'session', 'original-user', 'original-roles', 'extra-credential',
        'authorization-user', 'session-user', 'authenticated-user', 'principal', 'groups',
        'prepared-statement', 'transaction-id'}
    for key, value in headers.items():
        name = key.lower()
        suffix = re.sub(r'^x-(trino|presto)-', '', name)
        if suffix in forbidden or (suffix == 'user' and value != principal.principal_id):
            raise service.QueryError('SQL_PERMISSION_DENIED', 403)
    if path == '/v1/info' and method == 'GET':
        return {'nodeVersion': {'version': 'shirokuma'}, 'environment': 'local', 'coordinator': True, 'starting': False}
    if path == '/v1/statement' and method == 'POST':
        catalog, schema = single('X-Trino-Catalog'), single('X-Trino-Schema')
        with control_store.connect() as db:
            row = db.execute('''SELECT d.id AS database_id,s.id AS schema_id,b.desired_revision
                FROM databases d JOIN schemas s ON d.id=s.database_id
                JOIN catalog_bindings b ON d.id=b.database_id
                WHERE d.sql_catalog=? AND s.sql_name=?''', (catalog, schema)).fetchone()
        if row is None:
            raise service.QueryError('UNKNOWN_CONTEXT', 404)
        context = query_context.resolve_context(principal, row['database_id'], row['schema_id'], role,
            warehouse_id=warehouse_id, expected_binding_revision=row['desired_revision'])
        try:
            sql = body.decode('utf-8')
        except (AttributeError, UnicodeError):
            raise service.QueryError('UNKNOWN_CONTEXT', 400) from None
        page = service.submit(principal, context, sql,
            mode=selected_mode or ('admin-write' if role == 'admin' else 'sandbox-write' if role == 'data_engineer' else 'read-only'),
            max_rows=None, request_id=str(uuid.uuid4()), worksheet_id=None)
    else:
        match = re.fullmatch(r'/v1/statement/([a-zA-Z0-9-]+)/(?:pages/([a-zA-Z0-9_-]+)|(cancel))', path)
        if match is None or method not in ('GET', 'DELETE') or (match.group(3) and method != 'DELETE'):
            raise service.QueryError('QUERY_NOT_FOUND', 404)
        query_id, token, cancel_route = match.groups()
        if role is None:
            original = service._row(query_id)
            if original is None or original['actor'] != principal.principal_id:
                raise service.QueryError('QUERY_NOT_FOUND', 404)
            role = original['selected_role']
        row = service._owned(principal, query_id, role, grants=False)
        if selected_mode is not None and selected_mode != row['mode']:
            raise service.QueryError('CONTEXT_MISMATCH', 409)
        for name, key in (('X-Trino-Catalog', 'sql_catalog'), ('X-Trino-Schema', 'sql_schema')):
            selection = single(name, required=False)
            if selection is not None and selection != row[key]:
                raise service.QueryError('CONTEXT_MISMATCH', 409)
        if method == 'DELETE':
            if token:
                return service._cancel_registered(principal, query_id, token, selected_role=role)
            return service.cancel(principal, query_id, selected_role=role)
        page = service.fetch(principal, query_id, token, selected_role=role)
    result = dict(id=page['backend_query_id'], columns=page['columns'], data=page['data'], stats=page['stats'])
    if page.get('truncated'):
        result['truncated'] = True
    if page.get('error'):
        result['error'] = {'message': 'Query execution failed', 'errorName': 'QUERY_FAILED', 'errorType': 'USER_ERROR'}
    if page['next_uri']:
        result['nextUri'] = endpoint.rstrip('/') + page['next_uri'].replace('/api/v1/queries/', '/v1/statement/', 1)
    if page['state'] in ('QUEUED', 'RUNNING'):
        result['partialCancelUri'] = endpoint.rstrip('/') + page['cancel_uri'].replace('/api/v1/queries/', '/v1/statement/', 1)
    return result


def serve_endpoint(port=8080, bind='127.0.0.1', warehouse='LOCAL', once=False):
    """Serve Trino's client protocol, keeping result/cancel URLs on this endpoint."""
    import request_auth
    import trino_auth
    from contextlib import nullcontext
    import collections
    import threading
    native = _profile_active()
    if native:
        import ipaddress
        try:
            loopback = ipaddress.ip_address(bind).is_loopback
        except ValueError:
            loopback = bind == 'localhost'
        if not loopback:
            raise request_auth.AuthenticationError('Profile endpoint requires a loopback bind')
    else:
        request_auth.validate_bind(bind)
    continuation_owners = collections.OrderedDict()
    continuation_admissions = {}
    continuation_backend_ids = {}
    owner_lock = threading.Lock()
    size = globals()['warehouse'](warehouse)
    if not native:
        initialize_auto_chill()
    hop_headers = {'connection', 'keep-alive', 'proxy-authenticate',
                   'proxy-authorization', 'te', 'trailer', 'transfer-encoding',
                   'upgrade', 'host', 'content-length'}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            logging.getLogger(__name__).debug(fmt, *args)

        def error_json(self, code, message):
            data = json.dumps({'error': {'message': message}}).encode()
            self.send_response(code)
            if code == 401:
                self.send_header('WWW-Authenticate', 'Basic realm="Shirokuma", charset="UTF-8"')
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def database_body(self):
            # Share normal JSON framing without routing native SQL via Workbench.
            from workbench_server import WorkbenchHandler
            return WorkbenchHandler.database_body(self)

        def respond(self, value, status=200):
            from workbench_server import WorkbenchHandler
            return WorkbenchHandler.respond(self, value, status)

        def stage_request(self):
            import db_stage_routes
            import db_mart_routes
            import db_schedule_routes
            import db_inventory_routes
            import dataops_routes
            import database_inventory
            from query_context import QueryError
            from workbench_server import request_origin_allowed
            url = urllib.parse.urlsplit(self.path)
            stage = url.path == '/api/v1/stages' or url.path.startswith('/api/v1/stages/')
            mart = url.path == '/api/v1/marts' or url.path.startswith('/api/v1/marts/')
            schedule = any(url.path == '/api/v1/' + name or url.path.startswith('/api/v1/' + name + '/')
                           for name in ('schedules', 'alerts'))
            inventory = any(url.path == '/api/v1/' + name or url.path.startswith('/api/v1/' + name + '/')
                            for name in ('databases', 'schemas', 'objects', 'grants'))
            model_read = self.command == 'GET' and url.path == '/api/v1/wap/models'
            session_read = self.command == 'GET' and url.path == '/api/session'
            if not stage and not mart and not schedule and not inventory and not model_read and not session_read:
                return False
            try:
                principal = request_auth.authenticate_principal(self.headers)
                roles = self.headers.get_all('X-Shirokuma-Role', [])
                if len(roles) != 1 or roles[0] not in request_auth.ROLES:
                    raise ValueError('one explicit role required')
                if (stage or mart or schedule) and self.command == 'POST' and self.headers.get('Origin') and not request_origin_allowed(self.headers):
                    raise QueryError('INVALID_ORIGIN', 403)
                params = urllib.parse.parse_qs(url.query, keep_blank_values=True)
                if session_read:
                    if (url.query or url.fragment or self.headers.get('Transfer-Encoding') or
                            self.headers.get_all('Content-Length', []) not in ([], ['0'])):
                        raise ValueError('session GET does not accept parameters or a body')
                    try:
                        role = request_auth.select_role(principal.maximum_role, roles)
                    except PermissionError:
                        raise QueryError('SQL_PERMISSION_DENIED', 403) from None
                    self.respond(dict(username=principal.principal_id, role=role,
                        maximum_role=principal.maximum_role, authenticated=True,
                        data_mode='database', platform_admin=principal.platform_admin))
                elif inventory:
                    if not db_inventory_routes.dispatch(self, self.command, url.path, params, principal, roles[0]):
                        raise QueryError('NOT_FOUND', 404)
                elif mart:
                    db_mart_routes.dispatch(self, self.command, url.path, params, principal, roles[0])
                elif schedule:
                    db_schedule_routes.dispatch(self, self.command, url.path, params, principal, roles[0])
                elif model_read:
                    dataops_routes.dispatch(self, self.command, url.path, params, principal, roles[0])
                else:
                    db_stage_routes.dispatch(self, self.command, url.path, params, principal, roles[0])
            except Exception as error:
                if isinstance(error, request_auth.AuthenticationError):
                    code, status, details = 'AUTHENTICATION_REQUIRED', 401, {}
                elif isinstance(error, (QueryError, database_inventory.InventoryError)):
                    code, status, details = error.code, error.status, error.details
                elif isinstance(error, (ValueError, TypeError, UnicodeError)):
                    code, status, details = 'INVALID_INPUT', 400, {}
                else:
                    code, status, details = 'SERVICE_UNAVAILABLE', 502, {}
                self.respond(dict(code=code, message=code.replace('_', ' ').capitalize(),
                    details=details, retryable=status >= 500), status)
            return True

        def proxy(self):
            handle, attempted = None, False
            if native:
                import database_creations
                if database_creations.dispatch(self, self.command):
                    return
                import database_retirement_routes
                if database_retirement_routes.dispatch(self, self.command):
                    return
                if self.stage_request():
                    return
                try:
                    import runtime_profile
                    import query_service
                    if self.headers.get('Transfer-Encoding'):
                        raise query_service.QueryError('UNKNOWN_CONTEXT', 400)
                    length = int(self.headers.get('Content-Length', '0'))
                    if length < 0 or length > 1024 * 1024:
                        raise query_service.QueryError('UNKNOWN_CONTEXT', 400)
                    body = self.rfile.read(length) if length else b''
                    page = _native_request(self.headers, self.path, self.command, body,
                        runtime_profile.current().sql_endpoint_url, warehouse_id=size)
                    payload = json.dumps(page).encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except request_auth.AuthenticationError:
                    self.error_json(401, 'Valid Basic credentials are required')
                except query_service.QueryError as exc:
                    self.error_json(exc.status, exc.message)
                except (ValueError, OSError, RuntimeError):
                    self.error_json(400, 'Invalid native protocol request')
                return
            path = urllib.parse.urlsplit(self.path).path
            statement = self.command == 'POST' and path == '/v1/statement'
            if not (statement or path.startswith('/v1/statement/') or path == '/v1/info'):
                self.error_json(404, 'unknown Trino endpoint')
                return
            try:
                if self.headers.get('Transfer-Encoding'):
                    self.error_json(400, 'Content-Length is required')
                    return
                length = int(self.headers.get('Content-Length', '0'))
                if length < 0:
                    raise ValueError('invalid Content-Length')
                body = self.rfile.read(length) if length else None
                principal = request_auth.authenticate(self.headers)
                user = principal[0] if principal else self.headers.get('X-Trino-User', '')
                role = (request_auth.select_role(principal[1], self.headers.get_all('X-Shirokuma-Role', []))
                        if principal else resolve_role(user, self.headers.get('X-Shirokuma-Role')))
                if principal and path.startswith('/v1/statement/'):
                    with owner_lock:
                        if continuation_owners.get(path) != user:
                            self.error_json(403, 'Query continuation does not belong to this identity')
                            return
                if principal and any(self.headers.get_all(name, []) for name in
                                     ('X-Trino-Role', 'X-Trino-Session', 'X-Trino-Original-User')):
                    self.error_json(403, 'Client role and session overrides are not permitted')
                    return
                try:
                    for header in self.headers.get_all('X-Trino-Prepared-Statement', []):
                        if role != 'admin':
                            raise ValueError('SQL policy violation: restricted prepared statements are not allowed')
                        for entry in header.split(','):
                            name, separator, prepared = entry.partition('=')
                            if not separator or not name.strip() or not prepared.strip():
                                raise ValueError('SQL policy violation: invalid prepared statement header')
                            check_sql_policy(urllib.parse.unquote(prepared))
                except ValueError as exc:
                    self.error_json(403, str(exc))
                    return
                if statement:
                    try:
                        source_sql = (body or b'').decode('utf-8')
                        sql = (check_sql_policy(source_sql, mode='sandbox-write' if role == 'data_engineer' else 'read-only', role=role) if role != 'admin'
                               else check_bi_policy(source_sql, user, role=role))
                        if role == 'bi_viewer':
                            catalog = self.headers.get('X-Trino-Catalog', '')
                            _check_bi_schemas(sql)
                            schema = self.headers.get('X-Trino-Schema', '')
                            if schema:
                                _check_bi_schemas('SHOW TABLES FROM ' +
                                                  (identifier(catalog) + '.' if catalog else '') + identifier(schema))
                            elif catalog and catalog.lower() not in {'iceberg', 'system'}:
                                raise ValueError('SQL policy violation (bi_reader): query restricted to iceberg.marts and metadata')
                    except ValueError as exc:
                        self.error_json(403, str(exc))
                        return
                    body = sql.encode('utf-8')
                if statement:
                    with control_store.connect() as db:
                        db.execute('BEGIN IMMEDIATE')
                        handle = admission.admit(db, actor=user or 'shirokuma-local',
                            kind='legacy_sql', subject_id=uuid.uuid4().hex)
                elif path.startswith('/v1/statement/'):
                    with owner_lock:
                        handle = continuation_admissions.get(path)
                    if handle is None:
                        self.error_json(409, 'Query context lost')
                        return
                with (admission.accepted(handle, allow_unknown=self.command == 'DELETE')
                      if handle is not None else nullcontext()), (warehouse_activity() if path != '/v1/info' else nullcontext()):
                    if statement and status(size)['replicas'] == 0:
                        transition('wake', size)
                    excluded = hop_headers | {h.strip().lower() for h in self.headers.get('Connection', '').split(',')}
                    headers = {k: v for k, v in self.headers.items() if k.lower() not in excluded}
                    # Request uncompressed JSON so continuation URLs can be rewritten.
                    if principal:
                        headers = {k: v for k, v in headers.items() if k.lower() not in
                                   {'authorization', 'x-trino-user', 'x-shirokuma-role'}}
                        headers['X-Trino-User'] = user
                    if role == 'bi_viewer':
                        headers['X-Trino-Catalog'] = 'iceberg'
                        headers['X-Trino-Schema'] = 'marts'
                    headers['Accept-Encoding'] = 'identity'
                    headers = trino_auth.backend_headers(headers, role=role)
                    with trino() as url:
                        trino_auth.validate_backend_url(url)
                        backend = urllib.parse.urlsplit(url)
                        if backend.scheme == 'https':
                            conn = http.client.HTTPSConnection(backend.hostname, backend.port or 443,
                                timeout=TIMEOUT, context=trino_auth.backend_ssl_context())
                        else:
                            conn = http.client.HTTPConnection(backend.hostname, backend.port or 80, timeout=TIMEOUT)
                        try:
                            parsed_path = urllib.parse.urlsplit(self.path)
                            target = urllib.parse.urlunsplit(('', '', parsed_path.path, parsed_path.query, ''))
                            attempted = True
                            conn.request(self.command, target, body=body, headers=headers)
                            response = conn.getresponse()
                            data = response.read()
                            response_headers = response.getheaders()
                        finally:
                            conn.close()
                host, actual_port = self.server.server_address[:2]
                endpoint = os.environ.get('TRINO_ADVERTISED_URL')
                if not endpoint:
                    authority = self.headers.get('Host')
                    trust_forwarded = os.environ.get(
                        'TRINO_TRUST_FORWARDED_HEADERS', '').lower() in {'1', 'true', 'yes', 'on'}
                    proto = 'http'
                    if trust_forwarded:
                        authority = self.headers.get('X-Forwarded-Host') or authority
                        proto = self.headers.get('X-Forwarded-Proto', 'http')
                    endpoint = f'{proto}://{authority}' if authority else f'http://{host}:{actual_port}'
                endpoint = endpoint.rstrip('/')

                def local_uri(value):
                    parsed = urllib.parse.urlsplit(value)
                    return endpoint + urllib.parse.urlunsplit(('', '', parsed.path, parsed.query, parsed.fragment))

                if data:
                    try:
                        page = json.loads(data)
                    except (ValueError, UnicodeError):
                        page = None
                    if isinstance(page, dict):
                        if handle is not None:
                            if 200 <= response.status < 300 and self.command != 'DELETE':
                                backend_id, terminal = _statement_page(page, continuation_backend_ids.get(handle.id))
                                continuation_backend_ids[handle.id] = backend_id
                            with owner_lock:
                                for key in ('nextUri', 'partialCancelUri'):
                                    if isinstance(page.get(key), str):
                                        continuation_admissions[urllib.parse.urlsplit(page[key]).path] = handle
                            if 200 <= response.status < 300 and self.command != 'DELETE':
                                if not page.get('nextUri'):
                                    _operation_finish(handle, terminal)
                        if principal:
                            with owner_lock:
                                for key in ('nextUri', 'partialCancelUri'):
                                    if isinstance(page.get(key), str):
                                        continuation_owners[urllib.parse.urlsplit(page[key]).path] = user
                                while len(continuation_owners) > 4096:
                                    continuation_owners.popitem(last=False)
                        for key in ('nextUri', 'partialCancelUri', 'infoUri'):
                            if isinstance(page.get(key), str):
                                page[key] = local_uri(page[key])
                        data = json.dumps(page).encode('utf-8')
                    elif handle is not None and self.command != 'DELETE':
                        _operation_finish(handle, 'UNKNOWN')
                if handle is not None:
                    if self.command == 'DELETE' and 200 <= response.status < 300:
                        with control_store.connect() as db:
                            db.execute('BEGIN IMMEDIATE')
                            admission.finish(db, handle, state='CANCELED', evidence_ref=uuid.uuid4().hex)
                    elif response.status >= 300 or not data:
                        _operation_finish(handle, 'UNKNOWN')
                self.send_response(response.status)
                excluded = hop_headers | {h.strip().lower() for h in response.getheader('Connection', '').split(',')}
                for key, value in response_headers:
                    if key.lower() not in excluded:
                        self.send_header(key, local_uri(value) if key.lower() == 'location' else value)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (admission.AdmissionDenied, admission.AdmissionConflict, admission.GateTimeout) as exc:
                self.error_json(exc.status, exc.code)
            except request_auth.AuthenticationError as exc:
                self.error_json(401, str(exc))
            except PermissionError as exc:
                self.error_json(403, str(exc))
            except ValueError as exc:
                self.error_json(400, str(exc))
            except (OSError, RuntimeError, http.client.HTTPException, subprocess.SubprocessError) as exc:
                if handle is not None and attempted:
                    _operation_finish(handle, 'UNKNOWN')
                self.error_json(502, str(exc))

        do_POST = proxy
        do_GET = proxy
        do_DELETE = proxy

    with http.server.ThreadingHTTPServer((bind, port), Handler) as server:
        import database_creation_bridge
        import logical_creations
        from contextlib import ExitStack
        with ExitStack() as stack:
            if logical_creations.enabled():
                stack.enter_context(logical_creations.lifetime())
            else:
                database_creation_bridge.configure()
            if once:
                server.handle_request()
            else:
                server.serve_forever()
    return dict(warehouse=size, action='endpoint', result='verified')


def query(size, sql, catalog=None, schema=None, no_auto_wake=False, max_rows=1000, mode='read-only', *, context=None, principal=None):
    if _profile_active():
        if context is None or principal is None:
            raise ValueError('Authenticated query context is required')
        import query_service
        return query_service.submit(principal, context, sql, mode=mode, max_rows=max_rows,
                                    request_id=str(uuid.uuid4()), worksheet_id=None)
    warehouse(size)
    if not sql or not sql.strip():
        raise ValueError('query requires non-empty SQL')
    if max_rows is not None and max_rows < 1:
        raise ValueError('max-rows must be >= 1')
    check_sql_policy(sql, mode)
    with warehouse_operation(sql=True), warehouse_activity():
        return _query(size, sql, catalog, schema, no_auto_wake, max_rows, mode)


def _query(size, sql, catalog=None, schema=None, no_auto_wake=False, max_rows=1000, mode='read-only'):
    size = warehouse(size)
    if not sql or not sql.strip():
        raise ValueError("query requires non-empty SQL")
    if max_rows is not None and max_rows < 1:
        raise ValueError("max-rows must be >= 1")
    sql = check_sql_policy(sql, mode)
    touch_activity()
    started = time.monotonic()
    auto_woken = status(size)["replicas"] == 0
    try:
        if auto_woken:
            if no_auto_wake:
                raise ValueError("warehouse is chilled; auto-wake is disabled")
            transition("wake", size)
        headers = {"X-Trino-User": "shirokuma-warehouse", "X-Trino-Source": "shirokuma-warehouse-" + size}
        if catalog:
            headers["X-Trino-Catalog"] = catalog
        if schema:
            headers["X-Trino-Schema"] = schema
        with trino() as url:
            result = execute(url, sql, headers, max_rows)
    except Exception:
        telemetry("query", int((time.monotonic() - started) * 1000), status="failed")
        raise
    touch_activity()
    row_count = result.pop('row_count', len(result['rows']))
    return finish(dict(warehouse=size, action="query", result="verified", auto_woken=auto_woken,
                       **result, row_count=row_count, mode=mode), started)


def _profile_client_query(sql, catalog, schema, max_rows, *, mode='read-only'):
    import base64
    import stat
    import runtime_profile
    if not catalog or not schema:
        raise ValueError('Active profile requires --catalog and --schema')
    if type(max_rows) is not int or max_rows < 1 or not isinstance(sql, str) or not sql.strip():
        raise ValueError('Query SQL and positive max-rows are required')
    if mode not in ('read-only', 'sandbox-write', 'admin-write'):
        raise ValueError('Invalid query mode')
    profile = runtime_profile.current()
    try:
        path = Path(profile.client_auth_file)
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
            raise ValueError()
        account = json.loads(path.read_text())
        if (not isinstance(account, dict) or set(account) != {'username', 'password', 'selected_role'} or
                any(not isinstance(value, str) or not value or any(c in value for c in '\r\n\x00') for value in account.values()) or
                account['selected_role'] not in ('admin', 'data_engineer', 'bi_viewer')):
            raise ValueError()
    except (ValueError, OSError, TypeError):
        raise ValueError('Private client authentication configuration is required') from None
    origin = profile.sql_endpoint_url.rstrip('/')
    trusted = urllib.parse.urlsplit(origin)
    headers = {'Authorization': 'Basic ' + base64.b64encode((account['username'] + ':' + account['password']).encode()).decode(),
        'X-Shirokuma-Role': account['selected_role'], 'X-Shirokuma-Mode': mode,
        'X-Trino-Catalog': catalog, 'X-Trino-Schema': schema}
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, response_headers, newurl):
            return None
    opener = urllib.request.build_opener(NoRedirect())
    target, body, rows, columns, query_id = origin + '/v1/statement', sql.encode('utf-8'), [], [], None
    truncated, state, context = False, 'UNKNOWN', None

    def continuation(url):
        parsed = urllib.parse.urlsplit(url)
        if ((parsed.scheme, parsed.netloc) != (trusted.scheme, trusted.netloc) or parsed.query or parsed.fragment or
                not re.fullmatch(r'/v1/statement/[a-zA-Z0-9-]+/pages/[a-zA-Z0-9_-]+', parsed.path)):
            raise ValueError('Invalid SQL endpoint continuation')

    while target:
        if body is None:
            continuation(target)
        try:
            request = urllib.request.Request(target, data=body, headers=headers)
            with opener.open(request, timeout=TIMEOUT) as response:
                page = json.load(response)
        except Exception:
            raise RuntimeError('Authenticated SQL endpoint request failed') from None
        if page.get('error'):
            raise RuntimeError('Query execution failed')
        query_id, columns = page.get('id', query_id), page.get('columns', columns)
        if isinstance(page.get('context'), dict):
            context = page['context']
        data = page.get('data', [])
        remaining = max(0, max_rows - len(rows))
        rows.extend(data[:remaining])
        target, body = page.get('nextUri'), None
        state = page.get('stats', {}).get('state', 'RUNNING' if target else 'FINISHED')
        truncated = bool(page.get('truncated')) or len(data) > remaining or bool(target) and len(rows) >= max_rows
        if truncated:
            if state not in ('FINISHED', 'FAILED', 'CANCELED'):
                state = 'UNKNOWN'
            if target and state not in ('FINISHED', 'FAILED', 'CANCELED'):
                continuation(target)
                try:
                    with opener.open(urllib.request.Request(target, headers=headers, method='DELETE'), timeout=30) as response:
                        acknowledgment = json.load(response)
                    if acknowledgment.get('state') == 'CANCELED':
                        state = 'CANCELED'
                except Exception:
                    pass
            break
    result = dict(warehouse='LOCAL', action='query', result='verified' if state == 'FINISHED' else 'unverified',
        query_id=query_id, columns=columns, rows=rows, row_count=len(rows), state=state, truncated=truncated)
    if context is not None:
        result['context'] = context
    return result


def main():
    try:
        import runtime_profile
        runtime_profile.configure_process()
    except ImportError:
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("status", "chill", "wake", "query", "endpoint"):
        command = sub.add_parser(action)
        command.add_argument("--warehouse", default="LOCAL", type=warehouse)
        if action == 'endpoint':
            command.add_argument('--port', type=int, default=8080)
            command.add_argument('--bind', default='127.0.0.1')
            command.add_argument('--once', action='store_true')
        if action == "query":
            command.add_argument("--sql", required=True)
            command.add_argument("--catalog")
            command.add_argument("--schema")
            command.add_argument("--no-auto-wake", action="store_true")
            command.add_argument("--max-rows", type=int, default=1000)
            command.add_argument("--mode", default="read-only", choices=['read-only', 'sandbox-write', 'admin-write'])
    args = parser.parse_args()
    try:
        if args.action == "status":
            result = status(args.warehouse)
        elif args.action == "query":
            if _profile_active():
                result = _profile_client_query(args.sql, args.catalog, args.schema, args.max_rows, mode=args.mode)
                result['warehouse'] = args.warehouse
            else:
                result = query(args.warehouse, args.sql, args.catalog, args.schema,
                               args.no_auto_wake, args.max_rows, mode=args.mode)
        elif args.action == 'endpoint':
            result = serve_endpoint(args.port, args.bind, args.warehouse, args.once)
        else:
            result = transition(args.action, args.warehouse)
        print(json.dumps(result))
        if args.action == 'query' and result.get('result') == 'unverified':
            return 1
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
