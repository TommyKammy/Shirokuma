"""Project bounded stdout/stderr from the two owned telemetry containers."""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import time

from agenthouse import manage as agenthouse
from loki import manage as loki
from db_platform_observations import _authority, _now
from warehouse_operations import OperationError

_SCOPE = dict(since_minutes=10, tail_lines=100, event_limit=20, streams=['stdout', 'stderr'])
_MAX_BYTES = 64 * 1024
_TIMEOUT = 8
RAW_LOGS_PATH = '/api/v1/bears/platform-telemetry-raw-logs'
_PROJECTION = (
    '[{{json .Name}},{{json .Id}},{{json (index .Config.Labels "shirokuma.dev.component")}},'
    '{{json .Config.Image}},{{json .HostConfig.PortBindings}},['
    '{{range $i, $m := .Mounts}}{{if $i}},{{end}}'
    '[{{json $m.Type}},{{if eq $m.Type "volume"}}{{json $m.Name}}{{else}}{{json $m.Source}}{{end}},'
    '{{json $m.Destination}},{{json $m.RW}}]{{end}}]]'
)
_SOURCES = (
    ('agenthouse', agenthouse, '8123/tcp', '18123', '/var/lib/clickhouse', ()),
    ('loki', loki, '3100/tcp', '13100', '/loki',
     (('bind', str(loki.HERE / 'config.yaml'), '/etc/loki/local-config.yaml', False),)),
)
_TIMESTAMP = re.compile(rb'([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:\.([0-9]{1,9}))?Z')
_LEVEL = {
    'agenthouse': re.compile(
        rb'[0-9]{4}\.[0-9]{2}\.[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6} '
        rb'\[ +[0-9]+ +\] (?:\{[^{}\r\n]*\} )?<(Warning|Error|Fatal)>[ \t]+'
    ),
    'loki': re.compile(rb'level=(warn|error|fatal)[ \t\r]'),
}
_SEVERITY = {b'Warning': 'warning', b'Error': 'error', b'Fatal': 'fatal',
             b'warn': 'warning', b'error': 'error', b'fatal': 'fatal'}


def _docker(args, deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError()
    host = 'unix://' + str(Path.home() / '.colima/mac-studio-solo/docker.sock')
    env = {key: value for key, value in os.environ.items() if not key.startswith('DOCKER_')}
    process = subprocess.Popen(
        ['docker', '--host', host, *args], env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    output = {process.stdout: bytearray(), process.stderr: bytearray()}
    size = 0
    try:
        with selectors.DefaultSelector() as selector:
            for stream in output:
                selector.register(stream, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not (ready := selector.select(remaining)):
                    raise TimeoutError()
                for key, _ in ready:
                    chunk = os.read(key.fd, min(8192, _MAX_BYTES - size + 1))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    size += len(chunk)
                    if size > _MAX_BYTES:
                        raise ValueError()
                    output[key.fileobj].extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0 or process.wait(timeout=remaining) != 0 or time.monotonic() >= deadline:
            raise ValueError()
        if any(stream and not stream.endswith(b'\n') for stream in output.values()):
            raise ValueError()
        return bytes(output[process.stdout]), bytes(output[process.stderr])
    finally:
        try:
            if process.poll() is None:
                process.kill()
                process.wait()
        finally:
            process.stdout.close()
            process.stderr.close()


def _owned_id(source, stdout, stderr):
    name, manager, port, published, target, binds = source
    if stderr:
        raise ValueError()
    projected = json.loads(stdout)
    if not isinstance(projected, list) or len(projected) != 6:
        raise ValueError()
    container_name, identity, label, image, ports, mounts = projected
    expected_mounts = [[kind, resource, path, writable] for kind, resource, path, writable in (
        ('volume', manager.VOLUME, target, True), *binds
    )]
    if (container_name != '/' + manager.NAME or not isinstance(identity, str)
            or not re.fullmatch(r'[0-9a-f]{64}', identity)
            or label != name + '-local' or image != manager.IMAGE
            or ports != {port: [{'HostIp': '127.0.0.1', 'HostPort': published}]}
            or not isinstance(mounts, list) or len(mounts) != len(expected_mounts)
            or any(not isinstance(mount, list) or len(mount) != 4 or type(mount[3]) is not bool
                   for mount in mounts)
            or any(mount not in mounts for mount in expected_mounts)):
        raise ValueError()
    return identity


def _classified(name, streams):
    if sum(map(len, streams)) > _MAX_BYTES or any(
        stream and not stream.endswith(b'\n') for stream in streams
    ):
        raise ValueError()
    lines = [line for stream in streams for line in stream.split(b'\n')[:-1]]
    if len(lines) > _SCOPE['tail_lines']:
        raise ValueError()
    counts = dict(warning=0, error=0, fatal=0)
    events = []
    for line in lines:
        stamp, _, body = line.partition(b' ')
        timestamp = _TIMESTAMP.fullmatch(stamp)
        level = _LEVEL[name].match(body)
        if not timestamp or not level:
            continue
        seconds, fraction = timestamp.groups()
        try:
            datetime.fromisoformat(seconds.decode('ascii'))
        except ValueError:
            continue
        severity = _SEVERITY[level[1]]
        counts[severity] += 1
        events.append(((seconds, (fraction or b'').ljust(9, b'0')),
                       dict(timestamp=stamp.decode('ascii'), severity=severity)))
    return len(lines), counts, events


def _sample(name, streams):
    line_count, counts, events = _classified(name, streams)
    # Keep equal timestamps in source order, with stdout before stderr.
    events.sort(key=lambda event: event[0], reverse=True)
    return dict(status='available', line_count=line_count, other_count=line_count - sum(counts.values()),
                level_counts=counts, events=[event for _, event in events[:_SCOPE['event_limit']]])


def _raw_sample(_name, streams):
    if sum(map(len, streams)) > _MAX_BYTES:
        raise ValueError()
    line_count = 0
    for stream in streams:
        if stream and not stream.endswith(b'\n'):
            raise ValueError()
        for line in stream.split(b'\n')[:-1]:
            stamp, separator, _ = line.partition(b' ')
            timestamp = _TIMESTAMP.fullmatch(stamp)
            if not separator or not timestamp:
                raise ValueError()
            datetime.fromisoformat(timestamp[1].decode('ascii'))
            line_count += 1
            if line_count > _SCOPE['tail_lines']:
                raise ValueError()
    stdout, stderr = (stream.decode('utf-8') for stream in streams)
    return dict(status='available', line_count=line_count, stdout=stdout, stderr=stderr)


def observe(principal, role):
    return _observe(
        principal, role, _sample,
        dict(status='unavailable', line_count=0, other_count=0,
             level_counts=dict(warning=0, error=0, fatal=0), events=[]),
        {**_SCOPE, 'streams': list(_SCOPE['streams'])},
    )


def observe_raw(principal, role):
    return _observe(
        principal, role, _raw_sample, dict(status='unavailable', line_count=0, stdout='', stderr=''),
        dict(since_minutes=_SCOPE['since_minutes'], tail_lines=_SCOPE['tail_lines'], max_bytes=_MAX_BYTES),
    )


def _observe(principal, role, project, unavailable, scope):
    check = _authority_check(principal, role)
    return dict(scope=scope, **_collect(check, project, unavailable))


def _authority_check(principal, role):
    fingerprint = _authority(principal, role)

    def check():
        if _authority(principal, role) != fingerprint:
            raise OperationError('PLATFORM_ACCESS_DENIED', 403)

    return check


def _collect(check, project, unavailable):
    result = {}
    for source in _SOURCES:
        name, manager, *_ = source
        deadline = time.monotonic() + _TIMEOUT
        observation = unavailable
        try:
            check()
            identity = _owned_id(source, *_docker(
                ['container', 'inspect', '--format', _PROJECTION, manager.NAME], deadline))
            check()
            observation = project(name, _docker(
                ['logs', '--since=10m', '--tail=100', '--timestamps', identity], deadline))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        finally:
            check()
        result[name] = dict(observed_at=_now(), **observation)
    return result
