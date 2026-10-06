"""Offline AgentHouse Docker metadata guard, not volume-scan authorization.

Inputs are bounded, fresh read-only snapshots supplied by a future transport.
Never persist raw container inspect JSON: it includes environment credentials.
"""
from __future__ import annotations

from tools.local_experiment.agenthouse import manage as AH
from tools.local_experiment.finops import contract as C
from tools.local_experiment.finops import docker_storage as D

MAX_CONTAINERS = 128
ROOT = '/var/lib/docker'
VOLUME_ROOT = ROOT + '/volumes/' + AH.VOLUME + '/_data'


class PreflightError(ValueError):
    pass


def require(ok, reason='SCOPE_INVALID'):
    if not ok:
        raise PreflightError(reason)


def _path(value):
    require(type(value) is str and value.startswith('/') and not value.startswith('//') and
            len(value) <= 4096 and '\x00' not in value and all(part not in ('', '.', '..')
            for part in value[1:].split('/')))
    return value


def _overlaps(a, b):
    return a == b or a.startswith(b + '/') or b.startswith(a + '/')


def _lifecycle(container):
    state = container.get('State')
    require(type(state) is dict and state.get('Status') in
            ('created', 'running', 'paused', 'restarting', 'removing', 'exited', 'dead') and
            type(state.get('Running')) is bool and type(state.get('Restarting')) is bool and
            type(container.get('RestartCount')) is int and container['RestartCount'] >= 0)
    times = (state.get('StartedAt'), state.get('FinishedAt'))
    require(all(type(value) is str and 1 <= len(value) <= 64 for value in times))
    return (state['Status'], state['Running'], state['Restarting'], *times,
            container['RestartCount'])


def _mounts(container):
    mounts = container.get('Mounts')
    require(type(mounts) is list and len(mounts) <= 16)
    result = []
    for entry in mounts:
        require(type(entry) is dict and entry.get('Type') in ('bind', 'volume') and
                type(entry.get('RW')) is bool)
        source = _path(entry.get('Source'))
        destination = _path(entry.get('Destination'))
        name = entry.get('Name') if entry['Type'] == 'volume' else None
        driver = entry.get('Driver') if entry['Type'] == 'volume' else None
        if entry['Type'] == 'volume':
            require(type(name) is str and 1 <= len(name) <= 256 and
                    type(driver) is str and 1 <= len(driver) <= 128)
        result.append((entry['Type'], source, destination, name, driver, entry['RW']))
    return tuple(sorted(result))


def _project(snapshot):
    require(type(snapshot) is dict and
            set(snapshot) == {'context', 'daemon', 'container', 'volume', 'listed_ids',
                              'containers', 'boot_id', 'started_at', 'observed_at', 'listing_complete'} and
            snapshot['listing_complete'] is True)
    C.stamp(snapshot['started_at'])
    C.stamp(snapshot['observed_at'])
    context = snapshot['context']
    require(type(context) is dict and context.get('Name') == AH.CONTEXT)
    host = context.get('Endpoints', {}).get('docker', {}).get('Host')
    require(type(host) is str and host.startswith('unix:///Users/') and
            host.endswith('/.colima/mac-studio-solo/docker.sock') and
            '..' not in host.split('/'))
    daemon = snapshot['daemon']
    require(type(daemon) is dict and type(daemon.get('ID')) is str and
            1 <= len(daemon['ID']) <= 128 and
            daemon.get('OSType') == 'linux' and daemon.get('Architecture') in ('arm64', 'aarch64') and
            daemon.get('DockerRootDir') == ROOT)
    boot = snapshot['boot_id']
    require(type(boot) is str and len(boot) == 36 and
            all(ch in '0123456789abcdef-' for ch in boot))
    volume = snapshot['volume']
    require(type(volume) is dict and volume.get('Name') == AH.VOLUME and
            volume.get('Labels', {}).get(AH.LABEL) == 'agenthouse-local' and
            volume.get('Driver') == 'local' and volume.get('Scope') == 'local' and
            volume.get('Options') in (None, {}) and _path(volume.get('Mountpoint')) == VOLUME_ROOT)
    created = D.created_at(volume.get('CreatedAt'))
    require(C.stamp(created)[1] <= C.stamp(snapshot['started_at'])[1])
    container = snapshot['container']
    require(type(container) is dict and container.get('Name') == '/' + AH.NAME and
            container.get('Config', {}).get('Labels', {}).get(AH.LABEL) == 'agenthouse-local' and
            container.get('Config', {}).get('Image') == AH.IMAGE)
    identifier = container.get('Id')
    require(type(identifier) is str and len(identifier) == 64 and
            all(ch in '0123456789abcdef' for ch in identifier))
    state = container.get('State')
    require(type(state) is dict and state.get('Status') == 'exited' and
            state.get('Running') is False and state.get('Restarting') is False and
            state.get('Paused') is False and state.get('Dead') is False and
            container.get('HostConfig', {}).get('RestartPolicy', {}).get('Name') == 'no')
    target_lifecycle = _lifecycle(container)
    image_id = container.get('Image')
    require(type(image_id) is str and image_id.startswith('sha256:') and
            len(image_id) == 71 and all(ch in '0123456789abcdef' for ch in image_id[7:]))
    mounts = container.get('Mounts')
    require(type(mounts) is list and len(mounts) == 1 and type(mounts[0]) is dict)
    mount = mounts[0]
    require(mount.get('Type') == 'volume' and mount.get('Name') == AH.VOLUME and
            mount.get('Driver') == 'local' and mount.get('Source') == VOLUME_ROOT and
            mount.get('Destination') == '/var/lib/clickhouse' and mount.get('RW') is True)
    ids = snapshot['listed_ids']; inspected = snapshot['containers']
    require(type(ids) is list and 1 <= len(ids) <= MAX_CONTAINERS and
            type(inspected) is list and len(inspected) == len(ids) and
            all(type(item) is str and len(item) == 64 and
                all(ch in '0123456789abcdef' for ch in item) for item in ids))
    require(len(set(ids)) == len(ids) and set(ids) == {item.get('Id') for item in inspected
                                                    if type(item) is dict} and identifier in ids)
    inventory = []
    for other in inspected:
        require(type(other) is dict)
        lifecycle = _lifecycle(other)
        references = _mounts(other)
        inventory.append((other['Id'], lifecycle, references))
        if other['Id'] == identifier:
            require(other == container, 'IDENTITY_CHANGED')
            continue
        for kind, source, _, name, _, _ in references:
            if kind == 'volume':
                require(name != AH.VOLUME, 'SHARED_REFERENCE')
            if _overlaps(source, VOLUME_ROOT):
                raise PreflightError('SHARED_REFERENCE')
    return (host, daemon['ID'], boot, identifier, image_id, created,
            tuple(sorted(ids)), target_lifecycle, tuple(sorted(inventory)), VOLUME_ROOT)


def validate_pair(before, after, *, now):
    """Compare checked projections; continuous writer quiescence is unproven."""
    first, second = _project(before), _project(after)
    require(first == second, 'IDENTITY_CHANGED')
    begin, middle, later, end, current = (C.stamp(value)[1] for value in
        (before['started_at'], before['observed_at'], after['started_at'],
         after['observed_at'], now))
    require(0 <= middle-begin <= 20*10**9 and 0 <= later-middle and
            0 <= end-later <= 20*10**9 and 0 <= current-end and
            current-begin <= 30*10**9, 'SAMPLE_STALE')
    return {'state':'metadata_eligible', 'container_count':len(first[6]),
            'vm_host_writers_unresolved':True, 'volume_traversal_authorized':False}
