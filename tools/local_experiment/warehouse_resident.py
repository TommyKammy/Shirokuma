"""Resident Trino warehouse control for the dedicated local lab."""
import argparse
from contextlib import contextmanager
import http.client
import http.server
import json
import logging
import os
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
_LAST_ACTIVITY = time.monotonic()
_AUTO_CHILL_THREAD = None
_AUTO_CHILL_STOP = threading.Event()
_AUTO_CHILL_LOCK = threading.Lock()


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


def auto_chill_status():
    return dict(auto_chill_minutes=_AUTO_CHILL_MINUTES,
                idle_seconds=max(0, int(time.monotonic() - _LAST_ACTIVITY)))


def configure_auto_chill(minutes: int):
    global _AUTO_CHILL_MINUTES, _LAST_ACTIVITY
    if type(minutes) is not int or not 0 <= minutes <= 1440:
        raise ValueError('minutes must be an integer between 0 and 1440')
    _AUTO_CHILL_MINUTES = minutes
    _LAST_ACTIVITY = time.monotonic()
    if minutes > 0:
        start_auto_chill_daemon()
    else:
        stop_auto_chill_daemon()
    return auto_chill_status()


def check_auto_chill(size='LOCAL'):
    global _LAST_ACTIVITY
    chilled = False
    if (_AUTO_CHILL_MINUTES > 0 and
            time.monotonic() - _LAST_ACTIVITY >= _AUTO_CHILL_MINUTES * 60 and
            status(size)['replicas'] > 0):
        with trino() as url:
            count = execute(url,
                "SELECT COUNT(*) FROM system.runtime.queries WHERE state IN ('RUNNING', 'QUEUED') "
                "AND query NOT LIKE '%system.runtime.queries%'",
                {'X-Trino-User': 'shirokuma-warehouse'})['rows'][0][0]
        if count > 0:
            _LAST_ACTIVITY = time.monotonic()
            return dict(chilled=False, active_queries=count, **auto_chill_status())
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


def http_json(url, data=None, headers=None, timeout=30):
    parsed = urllib.parse.urlsplit(url)
    if urllib.request.urlopen is not _DEFAULT_URLOPEN or parsed.scheme != "http" or not parsed.hostname:
        request = urllib.request.Request(url, data=data, headers=headers or {})
        with urllib.request.urlopen(request, timeout=timeout) as response:
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


@contextmanager
def _trino_connection():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(KUBECTL + ["port-forward", "--address", "127.0.0.1",
                                        "svc/trino-local", f"{port}:8080"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    try:
        def ready():
            if process.poll() is not None:
                raise RuntimeError("Trino port-forward exited")
            try:
                return http_json(url + "/v1/info").get("starting") is False
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
    global _LAST_ACTIVITY
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
        _LAST_ACTIVITY = time.monotonic()
    return finish(dict(warehouse=warehouse(size), action=action,
                       result="noop" if noop else "verified", before_replicas=before["replicas"],
                       after_replicas=0 if chilled else 1,
                       state="chilled" if chilled else "active"), started)


def execute(url, sql, headers, max_rows=1000):
    rows, columns, query_id, truncated = [], [], None, False
    page = http_json(url + "/v1/statement", sql.encode("utf-8"), headers)
    while True:
        if page.get("error"):
            raise RuntimeError("Trino query failed: " + page["error"].get("message", "unknown error"))
        query_id = page.get("id", query_id)
        columns = page.get("columns", columns)
        data, next_uri = page.get("data", []), page.get("nextUri")
        remaining = max_rows - len(rows)
        rows.extend(data if len(data) <= remaining else data[:remaining])
        truncated = len(data) > remaining or (len(rows) == max_rows and bool(next_uri))
        if truncated:
            if next_uri:
                try:
                    request = urllib.request.Request(next_uri, headers=headers, method="DELETE")
                    with urllib.request.urlopen(request, timeout=30):
                        pass
                except Exception:
                    pass
            break
        if not next_uri:
            break
        page = http_json(next_uri, headers=headers)
    return dict(query_id=query_id, columns=columns, rows=rows, truncated=truncated)


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
    user, role = (user or '').lower(), (role or '').lower()
    if user in ('bi_reader', 'powerbi') or user.startswith('bi_') or role in ('bi_viewer', 'viewer', 'bi_reader'):
        return 'bi_viewer'
    if role in ('data_engineer', 'editor'):
        return 'data_engineer'
    return 'admin'


def check_sql_policy(sql: str, mode: str = 'read-only', role: str = 'admin') -> str:
    """Check a single statement before any warehouse access and return clean SQL."""
    if mode not in {'read-only', 'sandbox-write', 'admin-write'}:
        raise ValueError(f"unknown SQL guardrail mode: {mode}")
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


def _check_bi_schemas(sql, catalog=''):
    # Tokenize so comments, strings, whitespace and quoted names cannot hide a
    # restricted schema. String literals and quoted names remain distinct.
    pattern = r"--[^\n]*|/\*[\s\S]*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|[A-Za-z_][A-Za-z_0-9]*|[^\s]"
    tokens = [t.strip('"').lower() for t in re.findall(pattern, sql)
              if not t.startswith(('--', '/*'))]
    for i, token in enumerate(tokens):
        qualified = tokens[i:i + 2] == ['iceberg', '.']
        schema = i + 2 if qualified else i
        contextual = catalog.lower() == 'iceberg' and tokens[i + 1:i + 2] == ['.']
        if (qualified or contextual) and schema < len(tokens):
            if tokens[schema] in {'raw', 'sandbox', 'staging'}:
                raise ValueError('SQL policy violation (bi_reader): query restricted to iceberg.marts and metadata')


def check_bi_policy(sql: str, user: str) -> str:
    if not _is_bi_user(user):
        return normalize_trino_sql(sql.rstrip().rstrip('; \t\r\n'))
    statement = check_sql_policy(sql, mode='read-only')
    _check_bi_schemas(statement)
    return statement


def serve_endpoint(port=8080, bind='127.0.0.1', warehouse='LOCAL', once=False):
    """Serve Trino's client protocol, keeping result/cancel URLs on this endpoint."""
    size = globals()['warehouse'](warehouse)
    hop_headers = {'connection', 'keep-alive', 'proxy-authenticate',
                   'proxy-authorization', 'te', 'trailer', 'transfer-encoding',
                   'upgrade', 'host', 'content-length'}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            logging.getLogger(__name__).debug(fmt, *args)

        def error_json(self, code, message):
            data = json.dumps({'error': {'message': message}}).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def proxy(self):
            global _LAST_ACTIVITY
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
                if statement:
                    user = self.headers.get('X-Trino-User', '')
                    try:
                        sql = check_bi_policy((body or b'').decode('utf-8'), user)
                        if _is_bi_user(user):
                            catalog = self.headers.get('X-Trino-Catalog', '')
                            _check_bi_schemas(sql, catalog)
                            schema = self.headers.get('X-Trino-Schema', '')
                            _check_bi_schemas(identifier(catalog) + '.' + identifier(schema))
                    except ValueError as exc:
                        self.error_json(403, str(exc))
                        return
                    body = sql.encode('utf-8')
                    if status(size)['replicas'] == 0:
                        transition('wake', size)
                excluded = hop_headers | {h.strip().lower() for h in self.headers.get('Connection', '').split(',')}
                headers = {k: v for k, v in self.headers.items() if k.lower() not in excluded}
                # Request uncompressed JSON so continuation URLs can be rewritten.
                headers['Accept-Encoding'] = 'identity'
                with trino() as url:
                    backend = urllib.parse.urlsplit(url)
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
                if response.status < 400:
                    _LAST_ACTIVITY = time.monotonic()
                host, actual_port = self.server.server_address[:2]
                endpoint = f'http://{host}:{actual_port}'

                def local_uri(value):
                    parsed = urllib.parse.urlsplit(value)
                    return endpoint + urllib.parse.urlunsplit(('', '', parsed.path, parsed.query, parsed.fragment))

                if data:
                    try:
                        page = json.loads(data)
                    except (ValueError, UnicodeError):
                        page = None
                    if isinstance(page, dict):
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
            except ValueError as exc:
                self.error_json(400, str(exc))
            except (OSError, RuntimeError, http.client.HTTPException, subprocess.SubprocessError) as exc:
                self.error_json(502, str(exc))

        do_POST = proxy
        do_GET = proxy
        do_DELETE = proxy

    with http.server.HTTPServer((bind, port), Handler) as server:
        if once:
            server.handle_request()
        else:
            server.serve_forever()
    return dict(warehouse=size, action='endpoint', result='verified')


def query(size, sql, catalog=None, schema=None, no_auto_wake=False, max_rows=1000, mode='read-only'):
    global _LAST_ACTIVITY
    size = warehouse(size)
    if not sql or not sql.strip():
        raise ValueError("query requires non-empty SQL")
    if max_rows < 1:
        raise ValueError("max-rows must be >= 1")
    sql = check_sql_policy(sql, mode)
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
    _LAST_ACTIVITY = time.monotonic()
    return finish(dict(warehouse=size, action="query", result="verified", auto_woken=auto_woken,
                       **result, row_count=len(result["rows"]), mode=mode), started)


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
