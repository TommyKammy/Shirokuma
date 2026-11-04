"""Bounded, name-free declaration review of one pasted workload."""
import yaml
from yaml.events import AliasEvent
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from db_platform_observations import _authority
from db_platform_workload_baseline import _nonzero, _uid
from warehouse_operations import OperationError

MAX_MANIFEST_BYTES = 64 * 1024
MAX_NODES = 4096
MAX_DEPTH = 32
MAX_CONTAINERS = 64
TAG = 'tag:yaml.org,2002:'
APIS = dict(Pod='v1', Deployment='apps/v1', StatefulSet='apps/v1',
            DaemonSet='apps/v1', Job='batch/v1', CronJob='batch/v1')
CONTAINERS = (('containers', 'container'), ('initContainers', 'init_container'),
              ('ephemeralContainers', 'ephemeral_container'))


class ManifestLoader(yaml.SafeLoader):
    def __init__(self, stream):
        super().__init__(stream)
        self.depth = self.nodes = 0

    def compose_node(self, parent, index):
        event = self.peek_event()
        self.nodes += 1
        if (self.nodes > MAX_NODES or self.depth >= MAX_DEPTH
                or isinstance(event, AliasEvent) or event.anchor is not None
                or event.tag is not None):
            raise ValueError()
        self.depth += 1
        try:
            node = super().compose_node(parent, index)
        finally:
            self.depth -= 1
        if isinstance(node, MappingNode):
            keys = set()
            for key, _ in node.value:
                name = _scalar(key, 'str')
                if name == '<<' or name in keys:
                    raise ValueError()
                keys.add(name)
        elif isinstance(node, ScalarNode):
            # JSON permits astral characters written as two UTF-16 escapes.
            # PyYAML composes those escapes separately. Reject unpaired halves.
            try:
                node.value.encode('utf-8')
            except UnicodeEncodeError:
                node.value = node.value.encode('utf-16-le', 'surrogatepass').decode('utf-16-le')
        return node


def _scalar(node, *tags):
    if not isinstance(node, ScalarNode) or node.tag not in {TAG + tag for tag in tags}:
        raise ValueError()
    return node.value


def _mapping(node):
    if not isinstance(node, MappingNode):
        raise ValueError()
    return {key.value: value for key, value in node.value}


def _present(mapping, key):
    node = mapping.get(key)
    return node is not None and node.tag != TAG + 'null'


def _optional_mapping(mapping, key):
    return _mapping(mapping[key]) if _present(mapping, key) else {}


def _boolean(mapping, key, default=None):
    if not _present(mapping, key):
        return default
    value = _scalar(mapping[key], 'bool').lower()
    if value not in ('true', 'false'):
        raise ValueError()
    return value == 'true'


def _user(mapping, default=None):
    return _uid(_scalar(mapping['runAsUser'], 'int')) if _present(mapping, 'runAsUser') else default


def _limit(mapping, key):
    if not _present(mapping, key):
        return False
    value = _scalar(mapping[key], 'str', 'int', 'float')
    if not value:
        raise ValueError()
    return _nonzero(value)


def _sequence(node):
    if not isinstance(node, SequenceNode):
        raise ValueError()
    return node.value


def _checks(container, pod_non_root, pod_uid):
    security = _optional_mapping(container, 'securityContext')
    capabilities = _optional_mapping(security, 'capabilities')
    drop = [_scalar(entry, 'str') for entry in _sequence(capabilities['drop'])] if _present(capabilities, 'drop') else []
    limits = _optional_mapping(_optional_mapping(container, 'resources'), 'limits')
    cpu, memory = _limit(limits, 'cpu'), _limit(limits, 'memory')
    non_root, uid = _boolean(security, 'runAsNonRoot', pod_non_root), _user(security, pod_uid)
    checks = dict(read_only_root_filesystem=_boolean(security, 'readOnlyRootFilesystem') is True,
                  non_root=non_root is True and uid != 0,
                  drop_all_capabilities='ALL' in drop, cpu_memory_limits=cpu and memory)
    return {key: 'met' if value else 'not_met' for key, value in checks.items()}


def review(manifest):
    if type(manifest) is not str or len(manifest.encode('utf-8')) > MAX_MANIFEST_BYTES:
        raise ValueError()
    try:
        workload = _mapping(yaml.compose(manifest, Loader=ManifestLoader))
    except yaml.YAMLError:
        raise ValueError() from None
    kind = _scalar(workload.get('kind'), 'str')
    if APIS.get(kind) != _scalar(workload.get('apiVersion'), 'str'):
        raise ValueError()
    spec = _mapping(workload.get('spec'))
    if kind == 'CronJob':
        spec = _mapping(_mapping(spec.get('jobTemplate')).get('spec'))
    if kind != 'Pod':
        spec = _mapping(_mapping(spec.get('template')).get('spec'))
        if _present(spec, 'ephemeralContainers') and _sequence(spec['ephemeralContainers']):
            raise ValueError()

    security = _optional_mapping(spec, 'securityContext')
    pod_non_root, pod_uid = _boolean(security, 'runAsNonRoot'), _user(security)
    containers = []
    for field, container_kind in CONTAINERS:
        entries = _sequence(spec[field]) if _present(spec, field) else []
        if (field == 'containers' and not entries) or len(containers) + len(entries) > MAX_CONTAINERS:
            raise ValueError()
        for ordinal, entry in enumerate(entries, 1):
            containers.append(dict(kind=container_kind, ordinal=ordinal,
                                   checks=_checks(_mapping(entry), pod_non_root, pod_uid)))
    return dict(kind=kind, containers=containers)


def advise(principal, role, read_manifest):
    fingerprint = _authority(principal, role)
    def check():
        if _authority(principal, role) != fingerprint:
            raise OperationError('PLATFORM_ACCESS_DENIED', 403)

    try:
        manifest = read_manifest()
        check()
        return review(manifest)
    finally:
        check()
