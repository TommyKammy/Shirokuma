"""Offline AgentHouse Docker metadata guard, not volume-scan authorization.

Inputs are bounded, fresh read-only snapshots supplied by a future transport.
Never persist raw container inspect JSON: it includes environment credentials.
"""
from __future__ import annotations

import re

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


def _stamp(value):
    try:
        return C.stamp(value)
    except C.FinOpsError:
        raise PreflightError('SCOPE_INVALID') from None


def _path(value):
    require(type(value) is str and value.startswith('/') and not value.startswith('//') and
            len(value) <= 4096 and all(ord(ch) >= 32 and not 127 <= ord(ch) <= 159 for ch in value) and
            all(part not in ('', '.', '..')
            for part in value[1:].split('/')))
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise PreflightError('SCOPE_INVALID') from None
    return value


def _label(value, maximum):
    require(type(value) is str and 1 <= len(value) <= maximum and
            all(32 <= ord(ch) < 127 for ch in value))
    return value


def _overlaps(a, b):
    return a == b or a.startswith(b + '/') or b.startswith(a + '/')


def _lifecycle(container, observed):
    state = container.get('State')
    require(type(state) is dict and state.get('Status') in
             ('created', 'running', 'paused', 'restarting', 'removing', 'exited', 'dead') and
            type(state.get('Running')) is bool and type(state.get('Restarting')) is bool and
            type(state.get('Paused')) is bool and type(state.get('Dead')) is bool and
             type(container.get('RestartCount')) is int and container['RestartCount'] >= 0)
    # A stable pair of snapshots with internally contradictory Docker state
    # cannot establish a trustworthy inventory, even for an unrelated writer.
    if state['Status'] in ('created', 'exited'):
        require(all(state[flag] is False for flag in
                    ('Running', 'Restarting', 'Paused', 'Dead')))
    elif state['Status'] == 'running':
        require(state['Running'] is True and state['Restarting'] is False and
                state['Paused'] is False and state['Dead'] is False)
    # The remaining transient statuses cannot silently contradict their
    # corresponding flags in either snapshot of the inventory.
    for status, flag in (('paused', 'Paused'), ('restarting', 'Restarting'), ('dead', 'Dead')):
        require((state['Status'] == status) == state[flag])
    # A stable pair cannot make mutually exclusive process states coherent.
    # In particular, `removing` has no dedicated Boolean flag, so its flags
    # must not let a running process simultaneously appear dead or paused.
    require(not (state['Running'] and state['Dead']) and
            not (state['Paused'] and (state['Dead'] or state['Restarting'])) and
            (not state['Paused'] or state['Running']))
    times = (state.get('StartedAt'), state.get('FinishedAt'))
    require(all(type(value) is str and 1 <= len(value) <= 64 and
                all(ord(ch) >= 32 and not 127 <= ord(ch) <= 159 for ch in value)
                for value in times))
    parsed = [_stamp(value)[1] for value in times]
    # Even an unrelated running writer cannot have started after this
    # inventory was observed. Stable snapshots do not cure a future start.
    require(parsed[0] <= observed)
    # Docker may retain a prior FinishedAt for a running/restarted container.
    # It still cannot describe a termination after this observation.
    require(parsed[1] <= observed)
    # A live or paused process began after its previous termination. A
    # reported finish later than its current start cannot be prior history.
    if state['Status'] in ('running', 'paused'):
        require(parsed[1] <= parsed[0])
    # A not-yet-started container cannot have a later termination. Treat
    # contradictory unrelated lifecycle metadata as an uncertain inventory.
    if state['Status'] == 'created':
        require(parsed[1] <= parsed[0])
    # A terminated container with a finish before its start cannot be
    # evidence for a coherent inventory, even if both samples agree.
    if state['Status'] in ('exited', 'dead'):
        require(parsed[0] <= parsed[1] <= observed)
    return (state['Status'], state['Running'], state['Restarting'],
            state['Paused'], state['Dead'], *times,
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
            name = _label(name, 256)
            # A Docker volume name is not a path or arbitrary printable text.
            # Do not normalize a malformed unrelated reference into inventory.
            require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name) is not None)
            driver = _label(driver, 128)
        else:
            # Do not erase a contradictory volume identity while projecting a
            # bind. Docker bind mounts have no volume name or driver.
            require(entry.get('Name') in (None, '') and entry.get('Driver') in (None, ''))
        result.append((entry['Type'], source, destination, name, driver, entry['RW']))
    require(len(set(result)) == len(result))
    # Overmounted or nested destinations describe an ambiguous visible
    # filesystem topology, even when the sources themselves are distinct.
    # Refuse that inventory rather than infer a unique reference layout.
    require(all(not _overlaps(left[2], right[2])
                for index, left in enumerate(result) for right in result[index + 1:]))
    # The same source (or its child) mounted through another destination has
    # aliasing semantics that this metadata-only inventory cannot resolve.
    require(all(not _overlaps(left[1], right[1])
                for index, left in enumerate(result) for right in result[index + 1:]))
    return tuple(sorted(result))


def _project(snapshot):
    require(type(snapshot) is dict and
            set(snapshot) == {'context', 'daemon', 'container', 'volume', 'listed_ids',
                              'containers', 'boot_id', 'started_at', 'observed_at', 'listing_complete'} and
            snapshot['listing_complete'] is True)
    _stamp(snapshot['started_at'])
    observed = _stamp(snapshot['observed_at'])[1]
    context = snapshot['context']
    require(type(context) is dict and context.get('Name') == AH.CONTEXT)
    endpoints = context.get('Endpoints')
    require(type(endpoints) is dict and type(endpoints.get('docker')) is dict)
    host = endpoints['docker'].get('Host')
    # A suffix match also accepts nested/empty user paths and control-bearing
    # context endpoints. This is syntax validation, not socket authentication.
    require(type(host) is str and re.fullmatch(
        r'unix:///Users/[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*/\.colima/mac-studio-solo/docker\.sock', host) is not None)
    daemon = snapshot['daemon']
    require(type(daemon) is dict and type(daemon.get('ID')) is str and
             1 <= len(daemon['ID']) <= 128 and
            all(33 <= ord(ch) < 127 for ch in daemon['ID']) and
             daemon.get('OSType') == 'linux' and daemon.get('Architecture') in ('arm64', 'aarch64') and
            daemon.get('DockerRootDir') == ROOT)
    boot = snapshot['boot_id']
    require(type(boot) is str and re.fullmatch(
        r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', boot) is not None)
    volume = snapshot['volume']
    require(type(volume) is dict and type(volume.get('Labels')) is dict and
            volume.get('Name') == AH.VOLUME and
            volume['Labels'].get(AH.LABEL) == 'agenthouse-local' and
            volume.get('Driver') == 'local' and volume.get('Scope') == 'local' and
            volume.get('Options') in (None, {}) and _path(volume.get('Mountpoint')) == VOLUME_ROOT)
    try:
        created = D.created_at(volume.get('CreatedAt'))
    except D.DockerStorageError:
        raise PreflightError('SCOPE_INVALID') from None
    require(_stamp(created)[1] <= _stamp(snapshot['started_at'])[1])
    container = snapshot['container']
    require(type(container) is dict)
    config = container.get('Config')
    host_config = container.get('HostConfig')
    require(type(config) is dict and type(config.get('Labels')) is dict and
            type(host_config) is dict and type(host_config.get('RestartPolicy')) is dict)
    require(container.get('Name') == '/' + AH.NAME and
            config['Labels'].get(AH.LABEL) == 'agenthouse-local' and
            config.get('Image') == AH.IMAGE)
    identifier = container.get('Id')
    require(type(identifier) is str and len(identifier) == 64 and
            all(ch in '0123456789abcdef' for ch in identifier))
    state = container.get('State')
    require(type(state) is dict and state.get('Status') == 'exited' and
            state.get('Running') is False and state.get('Restarting') is False and
             state.get('Paused') is False and state.get('Dead') is False and
             host_config['RestartPolicy'].get('Name') == 'no' and
             type(host_config['RestartPolicy'].get('MaximumRetryCount')) is int and
             host_config['RestartPolicy']['MaximumRetryCount'] == 0)
    target_lifecycle = _lifecycle(container, observed)
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
    require(all(type(item) is dict and type(item.get('Id')) is str for item in inspected))
    require(len(set(ids)) == len(ids) and set(ids) == {item['Id'] for item in inspected}
            and identifier in ids)
    inventory = []
    for other in inspected:
        require(type(other) is dict)
        lifecycle = _lifecycle(other, observed)
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
    begin, middle, later, end, current = (_stamp(value)[1] for value in
        (before['started_at'], before['observed_at'], after['started_at'],
         after['observed_at'], now))
    require(0 <= middle-begin <= 20*10**9 and 0 <= later-middle and
            0 <= end-later <= 20*10**9 and 0 <= current-end and
            current-begin <= 30*10**9, 'SAMPLE_STALE')
    return {'state':'metadata_eligible', 'container_count':len(first[6]),
            'vm_host_writers_unresolved':True, 'volume_traversal_authorized':False}
