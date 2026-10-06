"""Read-only, fixed AgentHouse Docker volume reference (not byte usage)."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
import pwd
import re

from tools.local_experiment.finops import contract as C
from tools.local_experiment.agenthouse import manage as AH

LIMITS = ['ONE_FIXED_AGENTHOUSE_NAMED_VOLUME_NOT_DOCKER_INVENTORY',
          'MOUNT_REFERENCE_NOT_EXCLUSIVE_VOLUME_OWNERSHIP_OR_HEALTH',
          'DOCKER_VOLUME_USED_BYTES_AND_PHYSICAL_OCCUPANCY_NOT_MEASURED']
REASONS = frozenset(('READ_FAILED', 'READ_LIMIT', 'READ_TIMEOUT', 'CLEANUP_UNVERIFIED',
                     'SCOPE_INVALID', 'TARGET_MISSING', 'IDENTITY_CHANGED', 'SAMPLE_INVALID'))


class DockerStorageError(ValueError):
    pass


def require(ok, reason='SCOPE_INVALID'):
    if not ok: raise DockerStorageError(reason)


def unknown(reason):
    require(reason in REASONS)
    return dict(state='unknown', reason=reason, container_name=AH.NAME,
                container_id=None, container_running=None, volume_name=AH.VOLUME,
                volume_created_at=None, mount_destination=None, mount_read_write=None,
                request_started_at=None, observed_at=None, used_bytes=None,
                usage_reason='NOT_MEASURED')


def _object(raw):
    require(type(raw) is list and len(raw) == 1 and type(raw[0]) is dict)
    return raw[0]


def _context(raw):
    obj = _object(raw)
    require(obj.get('Name') == AH.CONTEXT)
    endpoints = obj.get('Endpoints')
    require(type(endpoints) is dict)
    docker = endpoints.get('docker')
    require(type(docker) is dict)
    host = docker.get('Host')
    # A suffix match alone also accepts a socket in an unrelated directory.
    # Reject control characters and traversal-like user segments as well.
    # This is a path scope check, not authentication of the daemon at the path.
    home = pwd.getpwuid(os.getuid()).pw_dir
    require(type(host) is str and home.startswith('/') and
            all(part not in ('', '.', '..') for part in home.split('/')[1:]) and
            host == 'unix://' + home + '/.colima/mac-studio-solo/docker.sock')
    return host


def created_at(value):
    require(type(value) is str and len(value) <= 48, 'SAMPLE_INVALID')
    match = re.fullmatch(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d{1,9})?(Z|[+-](?:(?:0\d|1[0-3]):[0-5]\d|14:00))', value)
    require(match is not None, 'SAMPLE_INVALID')
    try:
        local = datetime.strptime(match[1], '%Y-%m-%dT%H:%M:%S')
        offset = match[3]
        if offset != 'Z':
            minutes = (int(offset[1:3])*60 + int(offset[4:])) * (1 if offset[0] == '+' else -1)
            local -= timedelta(minutes=minutes)
        normalized = local.strftime('%Y-%m-%dT%H:%M:%S') + (match[2] or '') + 'Z'
        return C.stamp(normalized)[0]
    except (ValueError, OverflowError):
        raise DockerStorageError('SAMPLE_INVALID') from None


def _container(container):
    require(type(container) is dict)
    config = container.get('Config', {}); state = container.get('State', {})
    require(type(config) is dict and type(state) is dict)
    container_labels = config.get('Labels')
    require(type(container_labels) is dict)
    identifier = container.get('Id')
    require(type(identifier) is str and re.fullmatch(r'[0-9a-f]{64}', identifier))
    require(container.get('Name') == '/' + AH.NAME and
            container_labels.get(AH.LABEL) == 'agenthouse-local' and
            config.get('Image') == AH.IMAGE and type(state.get('Running')) is bool)
    mounts = container.get('Mounts')
    require(type(mounts) is list and len(mounts) == 1)
    mount = mounts[0]
    require(type(mount) is dict and mount.get('Type') == 'volume' and
            mount.get('Name') == AH.VOLUME and mount.get('Driver') == 'local' and
            mount.get('Source') == '/var/lib/docker/volumes/' + AH.VOLUME + '/_data' and
            mount.get('Destination') == '/var/lib/clickhouse' and mount.get('RW') is True)
    return identifier, state['Running']


def _identity(container, volume):
    identifier, running = _container(container)
    require(type(volume) is dict)
    volume_labels = volume.get('Labels')
    require(type(volume_labels) is dict)
    require(volume.get('Name') == AH.VOLUME and volume_labels.get(AH.LABEL) == 'agenthouse-local'
            and volume.get('Driver') == 'local' and volume.get('Scope') == 'local'
            and (volume.get('Options') is None or
                 type(volume.get('Options')) is dict and not volume['Options']))
    created = created_at(volume.get('CreatedAt'))
    mountpoint = volume.get('Mountpoint')
    # A matching suffix under another directory is not the fixed named volume.
    require(type(mountpoint) is str and
            mountpoint == '/var/lib/docker/volumes/' + AH.VOLUME + '/_data')
    # Do not publish host paths or infer that other containers cannot also use this volume.
    return (identifier, running, created, mountpoint)


def project(context_before, container_before, volume_before, context_after, container_after,
            volume_after, *, request_started_at, observed_at):
    require(_context(context_before) == _context(context_after), 'IDENTITY_CHANGED')
    before = _identity(_object(container_before), _object(volume_before))
    after = _identity(_object(container_after), _object(volume_after))
    require(before == after, 'IDENTITY_CHANGED')
    start, end = C.stamp(request_started_at), C.stamp(observed_at)
    require(0 <= end[1]-start[1] <= 30*10**9, 'SAMPLE_INVALID')
    require(C.stamp(before[2])[1] <= start[1], 'SAMPLE_INVALID')
    row = unknown('READ_FAILED')
    row.update(state='observed', reason=None, container_id=before[0],
               container_running=before[1], volume_created_at=before[2],
               mount_destination='/var/lib/clickhouse', mount_read_write=True,
               request_started_at=start[0], observed_at=end[0])
    return validate_row(row)


def validate_row(row):
    require(type(row) is dict and set(row) == set(unknown('READ_FAILED')) and
            row['container_name'] == AH.NAME and row['volume_name'] == AH.VOLUME and
            row['used_bytes'] is None and row['usage_reason'] == 'NOT_MEASURED')
    if row['state'] == 'unknown':
        require(row == unknown(row['reason']))
    else:
        require(row['state'] == 'observed' and row['reason'] is None and
                type(row['container_id']) is str and re.fullmatch(r'[0-9a-f]{64}', row['container_id']) and
                type(row['container_running']) is bool and
                row['mount_destination'] == '/var/lib/clickhouse' and row['mount_read_write'] is True)
        created = C.stamp(row['volume_created_at'])
        start, end = C.stamp(row['request_started_at']), C.stamp(row['observed_at'])
        require(created[1] <= start[1] and 0 <= end[1]-start[1] <= 30*10**9)
    return deepcopy(row)


def validate(value):
    require(type(value) is dict and set(value) == {'schema_version', 'agenthouse', 'limits'} and
            type(value['schema_version']) is int and value['schema_version'] == 1 and value['limits'] == LIMITS)
    return dict(schema_version=1, agenthouse=validate_row(value['agenthouse']), limits=LIMITS[:])


def collect(backend):
    try:
        start = backend.now()
        # Check each context reply before querying that context's daemon.
        # Validating only in project() would issue all four daemon reads first.
        first_context = backend.docker_storage_inspect('context', AH.CONTEXT)
        _context(first_context)
        first_container = backend.docker_storage_inspect('container', AH.NAME)
        _container(_object(first_container))
        first_volume = backend.docker_storage_inspect('volume', AH.VOLUME)
        first_identity = _identity(_object(first_container), _object(first_volume))
        # A volume created after this collection began cannot certify a
        # reference, even if a later read reports the same invalid identity.
        require(C.stamp(first_identity[2])[1] <= C.stamp(start)[1], 'SAMPLE_INVALID')
        before = [first_context, first_container, first_volume]
        second_context = backend.docker_storage_inspect('context', AH.CONTEXT)
        require(_context(first_context) == _context(second_context), 'IDENTITY_CHANGED')
        second_container = backend.docker_storage_inspect('container', AH.NAME)
        require(_container(_object(first_container)) == _container(_object(second_container)),
                'IDENTITY_CHANGED')
        second_volume = backend.docker_storage_inspect('volume', AH.VOLUME)
        require(first_identity == _identity(_object(second_container), _object(second_volume)),
                'IDENTITY_CHANGED')
        after = [second_context, second_container, second_volume]
        row = project(*before, *after, request_started_at=start, observed_at=backend.now())
    except Exception as error:
        from tools.local_experiment.gateway import pod_usage as U
        if isinstance(error, U.PodUsageCleanupError): raise
        code = str(error) if isinstance(error, (DockerStorageError, C.FinOpsError, U.PodUsageError)) and str(error) in REASONS else 'READ_FAILED'
        row = unknown(code)
    return validate(dict(schema_version=1, agenthouse=row, limits=LIMITS[:]))
