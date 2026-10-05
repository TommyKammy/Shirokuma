"""Read-only, fixed AgentHouse Docker volume reference (not byte usage)."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
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
    host = obj.get('Endpoints', {}).get('docker', {}).get('Host')
    # A suffix match alone also accepts a socket in an unrelated directory.
    # This is a path scope check, not authentication of the daemon at the path.
    require(type(host) is str and re.fullmatch(
        r'unix:///Users/[^/\.][^/]*/\.colima/mac-studio-solo/docker\.sock', host) is not None)
    return host


def created_at(value):
    require(type(value) is str and len(value) <= 48, 'SAMPLE_INVALID')
    match = re.fullmatch(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d{1,9})?(Z|[+-](?:0\d|1[0-4]):[0-5]\d)', value)
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


def _identity(container, volume):
    require(type(container) is dict and type(volume) is dict)
    config = container.get('Config', {}); state = container.get('State', {})
    require(type(config) is dict and type(state) is dict)
    identifier = container.get('Id')
    require(type(identifier) is str and re.fullmatch(r'[0-9a-f]{64}', identifier))
    require(container.get('Name') == '/' + AH.NAME and
            config.get('Labels', {}).get(AH.LABEL) == 'agenthouse-local' and
            config.get('Image') == AH.IMAGE and type(state.get('Running')) is bool)
    require(volume.get('Name') == AH.VOLUME and volume.get('Labels', {}).get(AH.LABEL) == 'agenthouse-local'
            and volume.get('Driver') == 'local' and volume.get('Scope') == 'local')
    created = created_at(volume.get('CreatedAt'))
    mountpoint = volume.get('Mountpoint')
    # A matching suffix under another directory is not the fixed named volume.
    require(type(mountpoint) is str and
            mountpoint == '/var/lib/docker/volumes/' + AH.VOLUME + '/_data')
    mounts = container.get('Mounts')
    require(type(mounts) is list and len(mounts) == 1)
    mount = mounts[0]
    require(type(mount) is dict and mount.get('Type') == 'volume' and
            mount.get('Name') == AH.VOLUME and mount.get('Driver') == 'local' and
            mount.get('Source') == mountpoint and mount.get('Destination') == '/var/lib/clickhouse' and
            mount.get('RW') is True)
    # Do not publish host paths or infer that other containers cannot also use this volume.
    return (identifier, state['Running'], created, mountpoint)


def project(context_before, container_before, volume_before, context_after, container_after,
            volume_after, *, request_started_at, observed_at):
    require(_context(context_before) == _context(context_after), 'IDENTITY_CHANGED')
    before = _identity(_object(container_before), _object(volume_before))
    after = _identity(_object(container_after), _object(volume_after))
    require(before == after, 'IDENTITY_CHANGED')
    start, end = C.stamp(request_started_at), C.stamp(observed_at)
    require(0 <= end[1]-start[1] <= 30*10**9, 'SAMPLE_INVALID')
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
        C.stamp(row['volume_created_at'])
        start, end = C.stamp(row['request_started_at']), C.stamp(row['observed_at'])
        require(0 <= end[1]-start[1] <= 30*10**9)
    return deepcopy(row)


def validate(value):
    require(type(value) is dict and set(value) == {'schema_version', 'agenthouse', 'limits'} and
            type(value['schema_version']) is int and value['schema_version'] == 1 and value['limits'] == LIMITS)
    return dict(schema_version=1, agenthouse=validate_row(value['agenthouse']), limits=LIMITS[:])


def collect(backend):
    try:
        start = backend.now()
        before = [backend.docker_storage_inspect(kind, name) for kind, name in
                  (('context', AH.CONTEXT), ('container', AH.NAME), ('volume', AH.VOLUME))]
        after = [backend.docker_storage_inspect(kind, name) for kind, name in
                 (('context', AH.CONTEXT), ('container', AH.NAME), ('volume', AH.VOLUME))]
        row = project(*before, *after, request_started_at=start, observed_at=backend.now())
    except Exception as error:
        from tools.local_experiment.gateway import pod_usage as U
        if isinstance(error, U.PodUsageCleanupError): raise
        code = str(error) if isinstance(error, (DockerStorageError, C.FinOpsError, U.PodUsageError)) and str(error) in REASONS else 'READ_FAILED'
        row = unknown(code)
    return validate(dict(schema_version=1, agenthouse=row, limits=LIMITS[:]))
