#!/usr/bin/env python3
"""Operate the dedicated, loopback-only ClickHouse Pawprint experiment."""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import http.client
import math
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import time
import urllib.error
import urllib.request
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
STATE = ROOT / "tmp/local-experiment/agenthouse"
CONTEXT = "colima-mac-studio-solo"
NAME = "shirokuma-local-agenthouse"
VOLUME = NAME + "-data"
IMAGE = "clickhouse/clickhouse-server@sha256:79f936494ccd00791676e0352d5988f30a215286592c6da83966c4c49fabd2c1"
URL = "http://127.0.0.1:18123"
USER = "shirokuma_agenthouse"
LABEL = "shirokuma.dev.component"
EVENT_FIELDS = frozenset({
    "event_id", "timestamp", "agent_id", "task_id", "repo", "issue", "pr",
    "action_type", "target_system", "risk_level", "approval_tier", "model_provider",
    "model_name", "prompt_tokens", "completion_tokens", "inference_cost_estimate",
    "cpu_seconds", "memory_mb_seconds", "status", "blocked_by_policy",
    "human_override", "trace_id", "duration_ms",
})


class AgentHouseError(RuntimeError):
    pass


def unique_docker_fields(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError('duplicate Docker inspection field')
        value[key] = item
    return value


def invalid_docker_constant(_value):
    raise ValueError('nonstandard Docker inspection number')


def bounded_docker_int(text):
    if len(text.lstrip('-')) > 128:
        raise ValueError('overlong Docker inspection integer')
    return int(text)


def finite_docker_float(text):
    if len(text) > 128:
        raise ValueError('overlong Docker inspection float')
    number = float(text)
    mantissa = re.split('[eE]', text, maxsplit=1)[0]
    if not math.isfinite(number) or (number == 0 and re.search('[1-9]', mantissa)):
        raise ValueError('unrepresentable Docker inspection number')
    return number


def decode_docker_json(raw):
    try:
        # Inspect replies include untrusted optional fields (and may contain
        # secrets); bound them before allocating a decoded object tree.
        if type(raw) is not str or len(raw) > 2 * 1024 * 1024 or len(raw.encode('utf-8')) > 2 * 1024 * 1024:
            raise ValueError('oversized Docker inspection metadata')
        value = json.loads(raw, object_pairs_hook=unique_docker_fields,
                           parse_constant=invalid_docker_constant,
                           parse_int=bounded_docker_int, parse_float=finite_docker_float)
        stack = [(value, 0)]
        while stack:
            item, depth = stack.pop()
            if depth > 64:
                raise ValueError('deep Docker inspection metadata')
            if isinstance(item, dict):
                for key, child in item.items():
                    key.encode('utf-8')
                    stack.append((child, depth + 1))
            elif isinstance(item, list):
                stack.extend((child, depth + 1) for child in item)
            elif isinstance(item, str):
                item.encode('utf-8')
        return value
    except (RecursionError, UnicodeError):
        raise ValueError('invalid Docker inspection metadata') from None


def unique_credential_fields(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError('duplicate credential field')
        value[key] = item
    return value


def docker(*args: str, required: bool = True) -> str:
    try:
        result = subprocess.run(["docker", "--context", CONTEXT, *args], capture_output=True,
                                text=True, timeout=180)
    except UnicodeError:
        # text=True decodes both stdout and stderr before returning. A broken
        # daemon reply is never evidence that a named retained object is absent.
        raise AgentHouseError('Docker reply is not decodable text; refusing runtime') from None
    except subprocess.TimeoutExpired:
        raise AgentHouseError('Docker command timed out; refusing runtime') from None
    except OSError:
        raise AgentHouseError('Docker command could not run; refusing runtime') from None
    if result.returncode:
        # Only an explicit absence for the exact requested resource allows
        # first-time creation. An unavailable daemon or ambiguous inspect
        # failure must not be interpreted as missing retained data.
        missing = (not required and len(args) == 3 and args[1] == 'inspect' and
                   args[0] in ('container', 'volume') and result.returncode == 1 and
                   result.stdout == '' and result.stderr in {
                       f'Error: No such {args[0]}: {args[2]}\n',
                       f'Error: No such object: {args[2]}\n'})
        if missing:
            return ''
        raise AgentHouseError(f"Docker {args[0]} failed (exit {result.returncode}); inspect scoped runtime locally")
    if not required and result.stdout == '':
        raise AgentHouseError(f'Docker {args[0]} returned empty inspection; refusing runtime')
    return result.stdout


def check_context() -> None:
    try:
        contexts = decode_docker_json(docker("context", "inspect", CONTEXT))
        if not isinstance(contexts, list) or len(contexts) != 1:
            raise ValueError('invalid context count')
        data = contexts[0]
        if not isinstance(data, dict) or data.get('Name') != CONTEXT:
            raise ValueError('context identity mismatch')
        endpoint = data["Endpoints"]["docker"]["Host"]
    except (ValueError, TypeError, KeyError, IndexError):
        raise AgentHouseError("dedicated Colima Docker context metadata is invalid") from None
    expected = "unix://" + str(Path.home() / ".colima/mac-studio-solo/docker.sock")
    if endpoint != expected:
        raise AgentHouseError("requires the dedicated Colima Docker socket")
    if docker("info", "--format", "{{.Architecture}}") not in {"aarch64", "arm64", "aarch64\n", "arm64\n"}:
        raise AgentHouseError("requires native ARM64 Docker runtime")


def inspect_owned(kind: str, name: str) -> dict | None:
    raw = docker(kind, "inspect", name, required=False)
    if not raw:
        return None
    try:
        result = decode_docker_json(raw)
        if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
            raise ValueError('invalid inspect result')
        data = result[0]
        labels = data.get("Config", {}).get("Labels", {}) if kind == "container" else data.get("Labels", {})
        if not isinstance(labels, dict):
            raise ValueError('invalid labels')
    except (ValueError, TypeError, AttributeError):
        raise AgentHouseError(f"invalid {kind} inspection for {name}; refusing runtime") from None
    if labels.get(LABEL) != "agenthouse-local":
        raise AgentHouseError(f"refusing an unowned {kind} named {name}")
    # A label alone must not lend another object the fixed container/volume
    # identity used to authorize startup against retained data.
    expected_name = '/' + name if kind == 'container' else name
    if data.get('Name') != expected_name:
        raise AgentHouseError(f"invalid {kind} inspection for {name}; refusing runtime")
    return data


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AgentHouseError("local AgentHouse must not redirect requests")


@contextmanager
def private_state_fd():
    """Bind a local read/write to the private state directory, not a child pathname."""
    try:
        fd = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        identity = os.fstat(fd)
        if identity.st_uid != os.getuid() or stat.S_IMODE(identity.st_mode) != 0o700:
            raise AgentHouseError('local AgentHouse state directory is not private')
    except OSError:
        raise AgentHouseError('local AgentHouse state directory cannot be opened safely') from None
    except BaseException:
        os.close(fd)
        raise
    try:
        yield fd
    finally:
        os.close(fd)


def read_credential() -> bytes:
    try:
        with private_state_fd() as state:
            fd = os.open('credential.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=state)
            with os.fdopen(fd, 'rb') as file:
                identity = os.fstat(file.fileno())
                if (not stat.S_ISREG(identity.st_mode) or identity.st_uid != os.getuid() or
                        stat.S_IMODE(identity.st_mode) != 0o600 or identity.st_nlink != 1 or
                        identity.st_size > 16384):
                    raise AgentHouseError('local credential is not a private regular file')
                content = file.read(16385)
                if len(content) > 16384:
                    raise AgentHouseError('local credential exceeds read limit')
                after = os.fstat(file.fileno())
                entry = os.stat('credential.json', dir_fd=state, follow_symlinks=False)
                if ((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) !=
                        (identity.st_dev, identity.st_ino, identity.st_size,
                         identity.st_mtime_ns, identity.st_ctime_ns) or
                        (entry.st_dev, entry.st_ino) != (identity.st_dev, identity.st_ino)):
                    raise AgentHouseError('local credential changed during read')
                if len(content) != identity.st_size:
                    raise AgentHouseError('local credential read is incomplete')
                return content
    except OSError:
        raise AgentHouseError('local credential cannot be opened safely') from None


def create_private_file(state: int, name: str, payload: bytes) -> None:
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=state)
        with os.fdopen(fd, 'wb') as file:
            os.fchmod(file.fileno(), 0o600)
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        # Sync the new directory entry as well as the file contents before
        # creating a container which will depend on this name after a restart.
        os.fsync(state)
    except OSError:
        raise AgentHouseError(f'local AgentHouse {name} cannot be created safely') from None


class Client:
    def __init__(self, *, credential_bytes: bytes | None = None) -> None:
        if credential_bytes is None:
            credential_bytes = read_credential()
        try:
            # json.loads(bytes) auto-detects UTF-16/32 and BOMs. Only the
            # generated UTF-8 credential representation is in this scope.
            if type(credential_bytes) is not bytes or len(credential_bytes) > 16384:
                raise ValueError('invalid credential bytes')
            text = credential_bytes.decode('utf-8')
            if text.startswith('\ufeff'):
                raise ValueError('credential BOM')
            credential = json.loads(text, object_pairs_hook=unique_credential_fields,
                                    parse_constant=invalid_docker_constant)
            if type(credential) is not dict or set(credential) != {'password'}:
                raise ValueError('invalid credential fields')
            password = credential['password']
            # Newly generated secrets are URL-safe tokens. Do not feed a
            # differently encoded saved value into Basic auth or an env file.
            if type(password) is not str or re.fullmatch(r'[A-Za-z0-9_-]+', password) is None:
                raise ValueError('invalid credential')
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise AgentHouseError('local credential has invalid format') from None
        try:
            self.auth = base64.b64encode((USER + ":" + password).encode('utf-8')).decode('ascii')
        except UnicodeError:
            raise AgentHouseError('local credential has invalid format') from None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def query(self, sql: str) -> str:
        if type(sql) is not str:
            raise AgentHouseError('local AgentHouse query requires SQL text')
        try:
            payload = sql.encode('utf-8')
        except UnicodeError:
            raise AgentHouseError('local AgentHouse query has invalid SQL encoding') from None
        request = urllib.request.Request(URL + "/?wait_end_of_query=1", data=payload,
                                         headers={"Authorization": "Basic " + self.auth}, method="POST")
        try:
            with self.opener.open(request, timeout=30) as response:
                # A successful transport alone is not acknowledgement that an
                # insert/schema query finished. Never retry an ambiguous write.
                if response.status != 200:
                    raise AgentHouseError('local AgentHouse response status is not a completed query')
                # ClickHouse can report a query failure after sending HTTP 200
                # (for example, once a streamed response has begun). The
                # exception indicator must not become a write acknowledgement.
                exception_codes = (response.headers.get_all('X-ClickHouse-Exception-Code')
                                   if hasattr(response.headers, 'get_all') else
                                   ([value for key, value in response.headers.items()
                                     if key.lower() == 'x-clickhouse-exception-code']))
                if exception_codes:
                    raise AgentHouseError('local AgentHouse response indicates a query exception')
                # This client requests plain text and never decompresses reply
                # bodies. A coded response is not a completed plain-text query.
                encodings = (response.headers.get_all('Content-Encoding')
                             if hasattr(response.headers, 'get_all') else
                             ([value for key, value in response.headers.items()
                               if key.lower() == 'content-encoding']))
                if encodings and encodings != ['identity']:
                    raise AgentHouseError('local AgentHouse response encoding is unsupported')
                # HTTPMessage.get() returns only one of multiple headers. Do
                # not accept a write acknowledgement with ambiguous framing or
                # transfer coding, even if the decoded body happens to fit.
                all_lengths = (response.headers.get_all('Content-Length')
                               if hasattr(response.headers, 'get_all') else
                               ([response.headers['Content-Length']]
                                if 'Content-Length' in response.headers else []))
                all_encodings = (response.headers.get_all('Transfer-Encoding')
                                 if hasattr(response.headers, 'get_all') else
                                 ([response.headers['Transfer-Encoding']]
                                  if 'Transfer-Encoding' in response.headers else []))
                if len(all_lengths or []) > 1 or all_encodings:
                    raise AgentHouseError('local AgentHouse response framing is ambiguous')
                declared = response.headers.get('Content-Length')
                if declared is not None:
                    # read(amt) can return a short body at EOF without raising
                    # IncompleteRead. A 200 with an incomplete body is not a
                    # completed query acknowledgement, especially for writes.
                    if (not isinstance(declared, str) or
                            re.fullmatch(r'(0|[1-9][0-9]*)', declared) is None or
                            len(declared) > 16 or int(declared) > 8 * 1024 * 1024):
                        raise AgentHouseError('local AgentHouse response length is invalid')
                body = response.read(8 * 1024 * 1024 + 1)
                if len(body) > 8 * 1024 * 1024:
                    raise AgentHouseError("AgentHouse response exceeds the local export limit")
                if declared is not None and len(body) != int(declared):
                    raise AgentHouseError('local AgentHouse response body is incomplete')
                try:
                    return body.decode('utf-8')
                except UnicodeError:
                    raise AgentHouseError('local AgentHouse response is not UTF-8') from None
        except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
            raise AgentHouseError("local AgentHouse request failed; verify its scoped runtime") from error


def ensure_runtime() -> Client:
    check_context()
    existing = inspect_owned("container", NAME)
    volume = inspect_owned("volume", VOLUME)
    if existing and volume is None:
        raise AgentHouseError("existing AgentHouse requires its original owned data volume; refusing recreation")
    if existing is None and volume is not None:
        # The retained volume may contain rows from a container that disappeared.
        # Never silently attach it to a newly created runtime or rotate credentials.
        raise AgentHouseError("orphaned owned data volume requires explicit inspection; refusing recreation")
    if existing and (not isinstance(existing.get('Config'), dict) or
                     not isinstance(existing['Config'].get('Image'), str) or
                     not isinstance(existing.get('HostConfig'), dict) or
                     not isinstance(existing.get('Mounts'), list) or
                     any(not isinstance(mount, dict) for mount in existing['Mounts'])):
        raise AgentHouseError('existing AgentHouse inspection is malformed; refusing startup')
    if existing:
        state = existing.get('State')
        # `docker start` can be a no-op for an already running container. Do
        # not mistake that for a scoped startup or write schema into a runtime
        # that was active before this operation.
        if (type(state) is not dict or state.get('Status') not in ('created', 'exited') or
                any(state.get(flag) is not False for flag in
                    ('Running', 'Paused', 'Restarting', 'Dead'))):
            raise AgentHouseError('existing AgentHouse is not unambiguously stopped; refusing startup')
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with private_state_fd() as state:
        if existing:
            try:
                os.stat('credential.json', dir_fd=state, follow_symlinks=False)
            except FileNotFoundError:
                raise AgentHouseError("existing AgentHouse requires its original local credential; refusing rotation")
        else:
            # A previous failed creation may have left either file behind.
            # Do not silently promote partial state into a fresh container.
            for name in ('credential.json', 'container.env'):
                try:
                    os.stat(name, dir_fd=state, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise AgentHouseError(f'local AgentHouse {name} already exists; inspect partial startup state')
            create_private_file(state, 'credential.json',
                                (json.dumps({'password': secrets.token_urlsafe(36)}) + '\n').encode())
    if not existing:
        credential_bytes = read_credential()
        Client(credential_bytes=credential_bytes)  # Validate before using it as an environment value.
        password = json.loads(credential_bytes)['password']
        if any(character in password for character in ('\n', '\r', '\x00')):
            raise AgentHouseError('local credential cannot be used in a container environment')
        environment = STATE / "container.env"
        with private_state_fd() as state:
            create_private_file(state, 'container.env',
                                f"CLICKHOUSE_DB=agenthouse\nCLICKHOUSE_USER={USER}\nCLICKHOUSE_PASSWORD={password}\n".encode())
        docker("image", "inspect", IMAGE)
        docker("volume", "create", "--label", LABEL + "=agenthouse-local", VOLUME)
        docker("create", "--name", NAME, "--label", LABEL + "=agenthouse-local",
               "--platform", "linux/arm64", "--cpus", "2", "--memory", "4g",
               "--pids-limit", "512", "--ulimit", "nofile=262144:262144",
               "--security-opt", "no-new-privileges:true",
               "--publish", "127.0.0.1:18123:8123", "--env-file", str(environment),
               "--mount", f"type=volume,src={VOLUME},dst=/var/lib/clickhouse", IMAGE)
    else:
        if existing["Config"]["Image"] != IMAGE:
            raise AgentHouseError("existing AgentHouse image differs; preserve it for explicit migration")
        bindings = existing["HostConfig"].get("PortBindings", {})
        if bindings != {"8123/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18123"}]}:
            raise AgentHouseError("existing AgentHouse port bindings differ from loopback scope")
        mounts = existing.get("Mounts", [])
        expected_mount = {"Type": "volume", "Name": VOLUME,
                          "Destination": "/var/lib/clickhouse", "RW": True}
        if (len(mounts) != 1 or
                {key: mounts[0].get(key) for key in expected_mount} != expected_mount):
            raise AgentHouseError("existing AgentHouse must mount only its owned data volume read-write")
        host_config = existing["HostConfig"]
        # A retained container must not restart itself outside an explicit
        # local operation after a daemon or host restart.
        policy = host_config.get('RestartPolicy')
        if (type(policy) is not dict or set(policy) != {'Name', 'MaximumRetryCount'} or
                type(policy['Name']) is not str or policy['Name'] != 'no' or
                type(policy['MaximumRetryCount']) is not int or policy['MaximumRetryCount'] != 0):
            raise AgentHouseError('existing AgentHouse restart policy differs; refusing startup')
        if host_config.get("NanoCpus") != 2_000_000_000 or host_config.get("Memory") != 4 * 1024**3:
            raise AgentHouseError("existing AgentHouse resource limits must remain 2 CPU and 4 GiB")
        # PortBindings alone do not constrain a host-networked or privileged
        # container; Docker may ignore published ports in host network mode.
        # IPC/UTS namespace sharing also exposes host or foreign-container state.
        if (host_config.get('NetworkMode') not in ('default', 'bridge') or
                host_config.get('Privileged') is not False or
                host_config.get('PidMode') != '' or
                host_config.get('IpcMode') not in ('', 'private') or
                host_config.get('UTSMode') != '' or
                host_config.get('PublishAllPorts') is not False or
                host_config.get('SecurityOpt') != ['no-new-privileges:true'] or
                'CapAdd' not in host_config or host_config['CapAdd'] not in (None, []) or
                'Devices' not in host_config or host_config['Devices'] not in (None, []) or
                'Binds' not in host_config or host_config['Binds'] not in (None, [])):
            raise AgentHouseError('existing AgentHouse isolation differs; refusing startup')
    client = Client()
    docker("start", NAME)
    deadline = time.monotonic() + 90
    while True:
        try:
            if client.query("SELECT 1") in ("1", "1\n"):
                break
            raise AgentHouseError("AgentHouse readiness response differs")
        except AgentHouseError:
            if time.monotonic() >= deadline:
                raise AgentHouseError("AgentHouse did not become ready within 90 seconds")
            time.sleep(1)
    client.query("CREATE DATABASE IF NOT EXISTS agenthouse")
    client.query((HERE / "schema.sql").read_text())
    return client


def event(action: str, target: str, status: str = "success", **fields) -> dict:
    value = {
        "event_id": str(uuid.uuid4()), "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "agent_id": "local-experiment", "task_id": "local-development", "repo": "Shirokuma",
        "issue": None, "pr": None, "action_type": action, "target_system": target,
        "risk_level": "low", "approval_tier": "local-standing", "model_provider": "none",
        "model_name": "", "prompt_tokens": None, "completion_tokens": None,
        "inference_cost_estimate": None, "cpu_seconds": None, "memory_mb_seconds": None,
        "status": status, "blocked_by_policy": False, "human_override": False,
        "trace_id": "", "duration_ms": 0,
    }
    if set(fields) - set(value):
        raise AgentHouseError("unknown Pawprint field; raw prompts, tool inputs and credentials are not accepted")
    value.update(fields)
    for key in ("issue", "pr", "prompt_tokens", "completion_tokens", "duration_ms"):
        number = value[key]
        if key == "duration_ms" and number is None:
            raise AgentHouseError("duration_ms must be a nonnegative integer, not null")
        if number is not None and (type(number) is not int or not 0 <= number <= 2**64 - 1):
            raise AgentHouseError(f"invalid nonnegative integer: {key}")
    for key in ("inference_cost_estimate", "cpu_seconds", "memory_mb_seconds"):
        number = value[key]
        if number is not None:
            # These columns are Float64. An arbitrary Python int may overflow
            # isfinite() or silently round when ClickHouse stores the event.
            if type(number) is int:
                valid = 0 <= number <= 2**53
            else:
                valid = type(number) is float and math.isfinite(number) and number >= 0
            if not valid:
                raise AgentHouseError(f"invalid measured value: {key}")
    for key, item in value.items():
         if isinstance(item, str) and (len(item) > 256 or
                                       any(ord(character) < 32 or 127 <= ord(character) <= 159
                                           for character in item)):
             raise AgentHouseError(f"unbounded or control-bearing field: {key}")
    if value["status"] not in {"success", "failed", "blocked"}:
        raise AgentHouseError("invalid Pawprint status")
    if value["risk_level"] not in {"low", "normal", "high"}:
        raise AgentHouseError("invalid Pawprint risk level")
    for key in ("blocked_by_policy", "human_override"):
        if type(value[key]) is not bool:
            raise AgentHouseError(f"invalid boolean: {key}")
    for key in ("event_id", "timestamp", "agent_id", "task_id", "repo", "action_type", "target_system",
                 "risk_level", "approval_tier", "model_provider", "model_name", "status", "trace_id"):
        if not isinstance(value[key], str):
            raise AgentHouseError(f"invalid string: {key}")
    # json.dumps escapes lone surrogates, but the HTTP SQL payload must be UTF-8.
    # Reject them here so an insert cannot fail outside the Pawprint boundary.
    for key, item in value.items():
        if isinstance(item, str):
            try:
                item.encode('utf-8')
            except UnicodeError:
                raise AgentHouseError(f"invalid UTF-8 Pawprint field: {key}") from None
    try:
        parsed_id = uuid.UUID(value["event_id"])
    except (ValueError, TypeError, AttributeError):
        raise AgentHouseError('invalid Pawprint event ID') from None
    if str(parsed_id) != value["event_id"]:
        raise AgentHouseError('noncanonical Pawprint event ID')
    try:
        parsed_time = datetime.strptime(value["timestamp"], "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        raise AgentHouseError('invalid Pawprint timestamp') from None
    if parsed_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] != value["timestamp"]:
        raise AgentHouseError('noncanonical Pawprint timestamp')
    return value


def insert(client: Client, value: dict) -> None:
    # Revalidate the complete input, then serialize as data, never SQL literals.
    # Defaults belong to event(); inventing an ID while inserting would turn a
    # retry of an incomplete payload into a second logical audit event.
    if not isinstance(value, dict) or set(value) != EVENT_FIELDS:
        raise AgentHouseError("insert requires the complete Pawprint field set; construct new events with event()")
    validated = event(value["action_type"], value["target_system"], **{
        key: item for key, item in value.items() if key not in {"action_type", "target_system"}})
    client.query("INSERT INTO agenthouse.pawprints FORMAT JSONEachRow\n" + json.dumps(validated))


def export(client: Client) -> Path:
    rows = client.query("SELECT * FROM agenthouse.pawprints FINAL ORDER BY timestamp, event_id FORMAT JSONEachRow")
    try:
        payload = rows.encode('utf-8')
    except (AttributeError, UnicodeError):
        raise AgentHouseError('local AgentHouse export reply is not UTF-8 text') from None
    if len(payload) > 8 * 1024 * 1024:
        raise AgentHouseError('local AgentHouse export exceeds the response limit')
    # A zero-row export is empty; otherwise JSONEachRow must have complete object lines.
    try:
        def unique_fields(pairs):
            value = {}
            for key, field in pairs:
                if key in value:
                    raise ValueError('duplicate export field')
                value[key] = field
            return value
        def invalid_constant(_value):
            raise ValueError('nonstandard export number')
        def bounded_int(text):
            if len(text.lstrip('-')) > 128:
                raise ValueError('overlong export integer')
            return int(text)
        def finite_float(text):
            if len(text) > 128:
                raise ValueError('overlong export float')
            number = float(text)
            mantissa = re.split('[eE]', text, maxsplit=1)[0]
            if not math.isfinite(number) or (number == 0 and re.search('[1-9]', mantissa)):
                raise ValueError('unrepresentable export number')
            return number
        def valid_row(line):
            # JSONEachRow emits a single object per physical line, without
            # whitespace outside it. JSON's permissive surrounding whitespace
            # must not turn a differently framed reply into a saved report.
            if not line.startswith('{') or not line.endswith('}'):
                return False
            value = json.loads(line, object_pairs_hook=unique_fields,
                                parse_constant=invalid_constant, parse_float=finite_float,
                                parse_int=bounded_int)
            if not isinstance(value, dict):
                return False
            stack = [(value, 0)]
            while stack:
                item, depth = stack.pop()
                if depth > 64:
                    return False
                if isinstance(item, dict):
                    for key in item:
                        key.encode('utf-8')
                    stack.extend((child, depth + 1) for child in item.values())
                elif isinstance(item, list):
                    stack.extend((child, depth + 1) for child in item)
                elif isinstance(item, str):
                    item.encode('utf-8')
            return True
        # splitlines() also splits on Unicode separators that JSONEachRow does
        # not use as record delimiters. Only an actual LF frames a row.
        if rows and ('\r' in rows or not rows.endswith('\n') or
                     not all(valid_row(line) for line in rows.split('\n')[:-1])):
            raise AgentHouseError('local AgentHouse export is not JSONEachRow')
    except (ValueError, RecursionError, UnicodeError):
        raise AgentHouseError('local AgentHouse export is not JSONEachRow') from None
    name = "pawprints-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".jsonl"
    path = STATE / name
    # Never publish a partially written JSONL report. Keep an incomplete staging
    # inode for inspection on failure instead of deleting ambiguous saved data.
    staging = name + '.' + secrets.token_hex(16) + '.pending'
    try:
        with private_state_fd() as state:
            try:
                os.stat(name, dir_fd=state, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise FileExistsError('report name already exists')
            fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                          0o600, dir_fd=state)
            with os.fdopen(fd, 'wb') as file:
                os.fchmod(file.fileno(), 0o600)
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            # Make the recoverable staging entry durable before attempting
            # publication. A failed sync leaves it for explicit inspection.
            os.fsync(state)
            # link is exclusive: unlike rename it cannot replace a saved report.
            os.link(staging, name, src_dir_fd=state, dst_dir_fd=state,
                    follow_symlinks=False)
            os.fsync(state)
            os.unlink(staging, dir_fd=state)
            os.fsync(state)
    except OSError:
        raise AgentHouseError('local AgentHouse export cannot be published safely; inspect any pending file') from None
    return path


def read_smoke_export(path: Path) -> list[dict]:
    """Read the just-published report from the private directory, without following links."""
    try:
        if path.parent != STATE or not re.fullmatch(r'pawprints-\d{8}T\d{12}Z\.jsonl', path.name):
            raise AgentHouseError('Pawprint export path is outside the private report scope')
        with private_state_fd() as state:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=state)
            with os.fdopen(fd, 'rb') as file:
                identity = os.fstat(file.fileno())
                if (not stat.S_ISREG(identity.st_mode) or identity.st_uid != os.getuid() or
                        stat.S_IMODE(identity.st_mode) != 0o600 or identity.st_nlink != 1 or
                        identity.st_size > 8 * 1024 * 1024):
                    raise AgentHouseError('Pawprint export is not a private bounded report')
                payload = file.read(8 * 1024 * 1024 + 1)
                after = os.fstat(file.fileno())
                if (len(payload) > 8 * 1024 * 1024 or
                        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) !=
                        (identity.st_dev, identity.st_ino, identity.st_size,
                         identity.st_mtime_ns, identity.st_ctime_ns)):
                    raise AgentHouseError('Pawprint export changed during readback')
            entry = os.stat(path.name, dir_fd=state, follow_symlinks=False)
            if (entry.st_dev, entry.st_ino) != (identity.st_dev, identity.st_ino):
                raise AgentHouseError('Pawprint export changed during readback')
        text = payload.decode('utf-8')
        if text and ('\r' in text or not text.endswith('\n')):
            raise AgentHouseError('Pawprint export readback is invalid; do not replay')
        def unique_fields(pairs):
            row = {}
            for key, value in pairs:
                if key in row:
                    raise ValueError('duplicate export field')
                row[key] = value
            return row
        def invalid_constant(_value):
            raise ValueError('nonstandard export number')
        def bounded_int(value):
            if len(value.lstrip('-')) > 128:
                raise ValueError('overlong export integer')
            return int(value)
        def finite_float(value):
            if len(value) > 128:
                raise ValueError('overlong export float')
            number = float(value)
            mantissa = re.split('[eE]', value, maxsplit=1)[0]
            if not math.isfinite(number) or (number == 0 and re.search('[1-9]', mantissa)):
                raise ValueError('unrepresentable export number')
            return number
        rows = []
        for line in text.split('\n')[:-1]:
            if not line.startswith('{') or not line.endswith('}'):
                raise AgentHouseError('Pawprint export readback is invalid; do not replay')
            row = json.loads(line, object_pairs_hook=unique_fields,
                             parse_constant=invalid_constant, parse_int=bounded_int,
                             parse_float=finite_float)
            if not isinstance(row, dict):
                raise AgentHouseError('Pawprint export readback is invalid; do not replay')
            stack = [(row, 0)]
            while stack:
                item, depth = stack.pop()
                if depth > 64:
                    raise AgentHouseError('Pawprint export readback is invalid; do not replay')
                if isinstance(item, dict):
                    for key in item:
                        key.encode('utf-8')
                    stack.extend((child, depth + 1) for child in item.values())
                elif isinstance(item, list):
                    stack.extend((child, depth + 1) for child in item)
                elif isinstance(item, str):
                    item.encode('utf-8')
            rows.append(row)
        return rows
    except (OSError, ValueError, UnicodeError):
        raise AgentHouseError('Pawprint export readback is invalid; do not replay') from None


def checked_version(client: Client) -> str:
    version = client.query("SELECT version()")
    if (not isinstance(version, str) or
            re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,5}\n?', version) is None or
            len(version) > 128):
        raise AgentHouseError("Pawprint version reply is invalid")
    return version.rstrip('\n')


def smoke(client: Client) -> dict:
    value = event("runtime.smoke", "clickhouse")
    insert(client, value)
    insert(client, value)  # Retry must produce one logical event through FINAL.
    try:
        raw = client.query("SELECT count() AS n, any(status) AS status FROM agenthouse.pawprints FINAL "
                           f"WHERE event_id = '{value['event_id']}' FORMAT JSON")
        if not isinstance(raw, str) or len(raw.encode('utf-8')) > 64 * 1024:
            raise ValueError('oversized count reply')
        def unique_fields(pairs):
            fields = {}
            for key, item in pairs:
                if key in fields:
                    raise ValueError('duplicate count field')
                fields[key] = item
            return fields
        def bounded_number(text):
            if len(text) > 128:
                raise ValueError('oversized count number')
            number = float(text)
            mantissa = re.split('[eE]', text, maxsplit=1)[0]
            if not math.isfinite(number) or (number == 0 and re.search('[1-9]', mantissa)):
                raise ValueError('unrepresentable count number')
            return number
        def bounded_integer(text):
            if len(text.lstrip('-')) > 128:
                raise ValueError('oversized count integer')
            return int(text)
        def invalid_constant(_text):
            raise ValueError('nonstandard count number')
        reply = json.loads(raw, object_pairs_hook=unique_fields,
                           parse_constant=invalid_constant, parse_int=bounded_integer,
                           parse_float=bounded_number)
        # Even an unused response field must not hide an unencodable or
        # excessively deep value behind an otherwise valid count.
        stack = [(reply, 0)]
        while stack:
            item, depth = stack.pop()
            if depth > 64:
                raise ValueError('count nesting exceeds limit')
            if isinstance(item, dict):
                for key in item:
                    key.encode('utf-8')
                stack.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                stack.extend((child, depth + 1) for child in item)
            elif isinstance(item, str):
                item.encode('utf-8')
        rows = reply.get('data') if isinstance(reply, dict) else None
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise AgentHouseError('Pawprint retry readback is invalid; do not replay') from None
    if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or
            set(rows[0]) != {'n', 'status'} or rows[0]['status'] != 'success' or
            not (type(rows[0]['n']) is int and rows[0]['n'] == 1 or rows[0]['n'] == '1')):
        raise AgentHouseError("Pawprint insertion or retry readback differs")
    path = export(client)
    exported = read_smoke_export(path)
    matching = [row for row in exported if isinstance(row, dict) and row.get('event_id') == value['event_id']]
    if len(matching) != 1 or matching[0].get('status') != 'success':
        raise AgentHouseError("export does not contain exactly one successful inserted event")
    # The ID alone is not evidence that the exported row is the event we sent.
    # Compare the stable identity columns rather than ClickHouse's numeric/null
    # formatting, which may differ from the JSONEachRow insert representation.
    if any(matching[0].get(key) != value[key] for key in
           ('timestamp', 'agent_id', 'task_id', 'action_type', 'target_system')):
        raise AgentHouseError("exported event identity differs from inserted event; do not replay")
    version = checked_version(client)
    return {"result": "passed", "version": version,
            "event_id": value["event_id"], "logical_rows": 1, "export": str(path), "endpoint": URL}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["up", "status", "smoke", "export", "stop"])
    args = parser.parse_args()
    try:
        if args.command in {"up", "smoke"}:
            client = ensure_runtime()
            result = smoke(client) if args.command == "smoke" else {"result": "ready", "endpoint": URL}
        elif args.command == "stop":
            check_context()
            if inspect_owned("container", NAME):
                docker("stop", NAME)
            result = {"result": "stopped", "data_volume_retained": True}
        elif args.command == "export":
            result = {"export": str(export(Client()))}
        else:
            result = {"result": "ready", "version": checked_version(Client()), "endpoint": URL}
        print(json.dumps(result))
        return 0
    except (AgentHouseError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"result": "failed", "error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
