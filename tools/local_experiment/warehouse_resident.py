"""Resident Trino warehouse control for the dedicated local lab."""
import argparse
from contextlib import contextmanager
import json
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

KUBECTL = ["kubectl", "--context", "colima-mac-studio-solo", "-n", "shirokuma-dev"]
TIMEOUT = 180
_PERSISTENT_TRINO = None
WRITABLE_SCHEMAS = ('raw', 'sandbox', 'staging', 'marts')


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
    request = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


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


def check_sql_policy(sql: str, mode: str = 'read-only') -> str:
    """Check a single statement before any warehouse access and return clean SQL."""
    if mode not in {'read-only', 'sandbox-write', 'admin-write'}:
        raise ValueError(f"unknown SQL guardrail mode: {mode}")

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


def query(size, sql, catalog=None, schema=None, no_auto_wake=False, max_rows=1000, mode='read-only'):
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
    return finish(dict(warehouse=size, action="query", result="verified", auto_woken=auto_woken,
                       **result, row_count=len(result["rows"]), mode=mode), started)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("status", "chill", "wake", "query"):
        command = sub.add_parser(action)
        command.add_argument("--warehouse", default="LOCAL", type=warehouse)
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
        else:
            result = transition(args.action, args.warehouse)
        print(json.dumps(result))
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
