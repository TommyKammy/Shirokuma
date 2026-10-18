"""Resident Trino warehouse control for the dedicated local lab."""
import argparse
from contextlib import contextmanager
import fcntl
import errno
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
def _warehouse_gate(exclusive=False):
    """Coordinate accepted work and warehouse transitions across local processes."""
    held = getattr(_WAREHOUSE_GATE, 'mode', None)
    if held:
        if exclusive and held != 'exclusive':
            raise RuntimeError('cannot stop a warehouse inside an active request')
        yield
        return
    path = activity_path().parent.parent / 'warehouse.lock'
    if not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock = path.open('a')
    except OSError as exc:
        if exc.errno not in (None, errno.EACCES, errno.EPERM, errno.EROFS):
            raise
        # flock does not write file contents. An existing readable lock remains
        # usable when the state volume has become read-only.
        lock = path.open('r')
    with lock:
        fcntl.flock(lock, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        _WAREHOUSE_GATE.mode = 'exclusive' if exclusive else 'shared'
        try:
            yield
        finally:
            _WAREHOUSE_GATE.mode = None
            fcntl.flock(lock, fcntl.LOCK_UN)


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
    with _warehouse_gate(exclusive=True), _ACTIVITY_LOCK:
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
    # Hold the same gate used by request admission through the actual scale.
    # The runtime query poll must occur under this gate too: a request that
    # finished submission before we acquired it can still be running in Trino.
    with _warehouse_gate(exclusive=True):
        _AUTO_CHILL_MINUTES = _load_auto_chill_minutes(_AUTO_CHILL_MINUTES)
        last_activity = read_last_activity()
        if (_AUTO_CHILL_MINUTES == 0 or
                time.time() - last_activity < _AUTO_CHILL_MINUTES * 60 or
                status(size)['replicas'] == 0):
            return dict(chilled=False, **auto_chill_status())
        with trino() as url:
            count = execute(url,
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
                transition('chill', size)
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
    return subprocess.run(KUBECTL + list(args), check=True, capture_output=True,
                          text=True, timeout=TIMEOUT).stdout


def deployment():
    return json.loads(kubectl("get", "deployment/trino-local", "-o", "json"))


def status(size):
    size = warehouse(size)
    obj = deployment()
    replicas = obj.get("spec", {}).get("replicas", 1)
    ready = obj.get("status", {}).get("readyReplicas", 0)
    state = "chilled" if replicas == 0 else "active" if ready >= 1 else "starting"
    return dict(warehouse=warehouse(size), backend="trino-local", deployment="trino-local", namespace="shirokuma-dev",
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
            if isinstance(exc, urllib.error.HTTPError) or attempt == 1:
                raise


def trino_http_json(url, data=None, headers=None, timeout=30):
    """Apply warehouse credentials only on explicitly Trino-owned requests."""
    import request_auth
    import trino_auth
    headers = trino_auth.backend_headers(headers, role=request_auth.current_role())
    trino_auth.validate_backend_url(url)
    options = {'context': trino_auth.backend_ssl_context()} if url.startswith('https:') else {}
    return http_json(url, data, headers, timeout, **options)


@contextmanager
def _trino_connection():
    import trino_auth
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(KUBECTL + ["port-forward", "--address", "127.0.0.1",
                                        "svc/trino-local", f"{port}:8080"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    scheme = 'https' if trino_auth.password_mode() else 'http'
    url = f"{scheme}://127.0.0.1:{port}"
    try:
        def ready():
            if process.poll() is not None:
                raise RuntimeError("Trino port-forward exited")
            try:
                return trino_http_json(url + "/v1/info").get("starting") is False
            except (OSError, urllib.error.URLError):
                return False
        wait_for(ready)
        yield dict(process=process, url=url)
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
    if _PERSISTENT_TRINO and _PERSISTENT_TRINO['process'].poll() is None:
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
    if _PERSISTENT_TRINO and _PERSISTENT_TRINO['process'].poll() is None:
        yield _PERSISTENT_TRINO['url']
        return
    with _trino_connection() as connection:
        yield connection['url']


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
    with _warehouse_gate(exclusive=action == 'chill'):
        return _transition(action, size)


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


def execute(url, sql, headers, max_rows=1000):
    with warehouse_activity():
        return _execute(url, sql, headers, max_rows)


def _execute(url, sql, headers, max_rows=1000):
    # None drains the query to completion without retaining result rows for jobs.
    import request_auth
    import trino_auth
    headers = trino_auth.backend_headers(headers, role=request_auth.current_role())
    rows, columns, query_id, truncated = [], [], None, False
    row_count = 0
    page = trino_http_json(url + "/v1/statement", sql.encode("utf-8"), headers)
    while True:
        if page.get("error"):
            raise TrinoQueryError(page["error"].get("message", "unknown error"),
                                  page["error"].get("errorName"))
        query_id = page.get("id", query_id)
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
                except Exception:
                    pass
            break
        if not next_uri:
            break
        page = trino_http_json(next_uri, headers=headers)
    result = dict(query_id=query_id, columns=columns, rows=rows, truncated=truncated)
    if max_rows is None:
        result['row_count'] = row_count
    return result


def normalize_trino_sql(sql: str) -> str:
    """Wrap a top-level QUALIFY filter, leaving other SQL byte-for-byte intact."""
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


def check_sql_policy(sql: str, mode: str = 'read-only', role: str = 'admin') -> str:
    """Check a single statement before any warehouse access and return clean SQL."""
    if mode not in {'read-only', 'sandbox-write', 'admin-write'}:
        raise ValueError(f"unknown SQL guardrail mode: {mode}")
    _check_dynamic_sql(sql)
    if role == 'bi_viewer':
        if mode != 'read-only':
            raise ValueError('BI Viewer requires read-only mode')
        return check_bi_policy(sql, 'bi_reader')
    if role == 'data_engineer' and mode == 'admin-write':
        raise ValueError('Data Engineer cannot use admin-write mode')

    def reject(reason):
        raise ValueError(f"SQL policy violation ({mode}): {reason}")

    statement = sql.rstrip().rstrip('; \t\r\n')
    statement = normalize_trino_sql(statement)
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
    destructive = keywords & {'DROP', 'TRUNCATE', 'DELETE', 'GRANT', 'REVOKE'}
    if destructive:
        reject('destructive statement: ' + ', '.join(sorted(destructive)))
    mutations = keywords & {'INSERT', 'UPDATE', 'MERGE', 'CREATE', 'ALTER', 'CALL'}
    if not mutations:
        return statement
    if mode == 'read-only':
        reject('write statements are not allowed')

    # Match only the supported write forms, including quoted qualified names.
    normalized = ' '.join(tokens)
    ident = r'(?:[A-Za-z_][A-Za-z_0-9]*|"(?:[^"]|"")+")'
    def name(value):
        return rf'(?:{value}|"{value}")'
    dot = r'\s*\.\s*'
    catalog = name('iceberg') + dot
    writable = ('sandbox', 'raw') if mode == 'sandbox-write' else WRITABLE_SCHEMAS
    schemas = '(?:' + '|'.join(name(s) for s in writable) + ')'
    target = catalog + schemas + dot + ident
    schema = schemas if mode == 'sandbox-write' else ident
    rollback_target = catalog + schema + dot + ident
    allowed = [
        rf'CREATE SCHEMA IF NOT EXISTS {catalog}{schema}',
        rf'CREATE (?:OR REPLACE )?TABLE (?:IF NOT EXISTS )?{target}(?=\s|\(|$)(?!\s*\.).*',
        rf'INSERT INTO {target}(?=\s|\(|$)(?!\s*\.).*',
        rf'ALTER TABLE {rollback_target} EXECUTE rollback_to_snapshot\s*\(\s*[0-9]+\s*\)',
    ]
    if not any(re.fullmatch(p, normalized, re.I | re.S) for p in allowed):
        reject('write target or operation is not permitted')
    return statement


def _is_bi_user(user):
    return user.lower() == 'powerbi' or user.lower().startswith('bi_')


def _check_bi_schemas(sql):
    # Tokenize so comments, strings, whitespace and quoted names cannot hide a
    # restricted schema. String literals and quoted names remain distinct.
    pattern = r"--[^\n]*|/\*[\s\S]*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|[A-Za-z_][A-Za-z_0-9]*|[^\s]"
    raw_tokens = [t for t in re.findall(pattern, sql) if not t.startswith(('--', '/*'))]
    tokens = [t[1:-1].replace('""', '"').lower() if t.startswith('"') else t.lower()
              for t in raw_tokens]
    def reject():
        raise ValueError('SQL policy violation (bi_reader): query restricted to iceberg.marts and metadata')

    def allowed(parts):
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
        reference = token in {'from', 'join', 'describe', 'table'} or show_context or (token == ',' and depth in from_depths)
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
        if tokens[j + 1:j + 2] == ['(']:
            reject()  # Table functions require separate authorization semantics.
        alias_after(j + 1)
        prefix = [t.lower() for t in raw_tokens[:2]]
        if prefix == ['show', 'tables'] and token in {'from', 'in'}:
            if not allowed(parts + ['_table']):
                reject()
        elif prefix == ['show', 'schemas'] and token in {'from', 'in'}:
            if parts not in (['iceberg'], ['system']):
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


def serve_endpoint(port=8080, bind='127.0.0.1', warehouse='LOCAL', once=False):
    """Serve Trino's client protocol, keeping result/cancel URLs on this endpoint."""
    import request_auth
    import trino_auth
    from contextlib import nullcontext
    import collections
    import threading
    request_auth.validate_bind(bind)
    continuation_owners = collections.OrderedDict()
    owner_lock = threading.Lock()
    size = globals()['warehouse'](warehouse)
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

        def proxy(self):
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
                with warehouse_activity() if path != '/v1/info' else nullcontext():
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
                self.send_response(response.status)
                excluded = hop_headers | {h.strip().lower() for h in response.getheader('Connection', '').split(',')}
                for key, value in response_headers:
                    if key.lower() not in excluded:
                        self.send_header(key, local_uri(value) if key.lower() == 'location' else value)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except request_auth.AuthenticationError as exc:
                self.error_json(401, str(exc))
            except PermissionError as exc:
                self.error_json(403, str(exc))
            except ValueError as exc:
                self.error_json(400, str(exc))
            except (OSError, RuntimeError, http.client.HTTPException, subprocess.SubprocessError) as exc:
                self.error_json(502, str(exc))

        do_POST = proxy
        do_GET = proxy
        do_DELETE = proxy

    with http.server.ThreadingHTTPServer((bind, port), Handler) as server:
        if once:
            server.handle_request()
        else:
            server.serve_forever()
    return dict(warehouse=size, action='endpoint', result='verified')


def query(size, sql, catalog=None, schema=None, no_auto_wake=False, max_rows=1000, mode='read-only'):
    with warehouse_activity():
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


def main():
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
            result = query(args.warehouse, args.sql, args.catalog, args.schema, args.no_auto_wake, args.max_rows, mode=args.mode)
        elif args.action == 'endpoint':
            result = serve_endpoint(args.port, args.bind, args.warehouse, args.once)
        else:
            result = transition(args.action, args.warehouse)
        print(json.dumps(result))
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
