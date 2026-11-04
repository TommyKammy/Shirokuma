"""Bounded namespace-wide controller template declarations, advisory only."""
import json
import re
import selectors
import subprocess
import time

from db_platform_observations import _authority, _now, SOURCE_ERRORS
from db_platform_foundation_baseline import _foundation
from db_platform_pod_baseline import _name
from db_platform_workload_baseline import _boolean, _uid, _drop_all, _nonzero
import logical_foundation
from warehouse_operations import OperationError

MAX_STDOUT_BYTES = 65536
MAX_DTO_BYTES = 65536
MAX_CONTROLLERS = 64
MAX_CONTAINERS = 256
APIS = dict(Deployment='apps/v1', StatefulSet='apps/v1', DaemonSet='apps/v1',
            Job='batch/v1', CronJob='batch/v1')
RESOURCES = 'deployments.apps,statefulsets.apps,daemonsets.apps,jobs.batch,cronjobs.batch'
KINDS = {'C': 'container', 'I': 'init_container'}
TEMPLATE = '.spec.template.spec'
CRON_TEMPLATE = '.spec.jobTemplate.spec.template.spec'

# kubectl wraps a mixed resource selection in a core v1 List. Emit both
# possible paths with distinct markers; only the kind's own path is valid.
JSONPATH = (r'jsonpath=L{"\t"}{.apiVersion}{"\t"}{.kind}{"\t"}'
            r'{.metadata.continue}{"\t"}{.metadata.remainingItemCount}{"\n"}'
            r'{range .items[*]}P{"\t"}{.apiVersion}{"\t"}{.kind}'
            r'{"\t"}{.metadata.namespace}{"\t"}{.metadata.name}{"\t"}{.metadata.uid}')
for _path in (TEMPLATE, CRON_TEMPLATE):
    JSONPATH += (r'{"\t"}{' + _path + '.securityContext.runAsNonRoot}'
                 r'{"\t"}{' + _path + '.securityContext.runAsUser}')
JSONPATH += r'{"\n"}'
for _path, _prefix in ((TEMPLATE, 'T'), (CRON_TEMPLATE, 'J')):
    for _field, _marker in (('containers', 'C'), ('initContainers', 'I')):
        JSONPATH += (r'{range ' + _path + '.' + _field + r'[*]}' + _prefix + _marker +
                     r'{"\t"}{.name}{"\t"}{.securityContext.readOnlyRootFilesystem}'
                     r'{"\t"}{.securityContext.runAsNonRoot}{"\t"}{.securityContext.runAsUser}'
                     r'{"\t"}{.securityContext.capabilities.drop}'
                     r'{"\t"}{.resources.limits.cpu}{"\t"}{.resources.limits.memory}{"\n"}{end}')
JSONPATH += r'{"Q\n"}{end}{"Z\n"}'


def _parse(raw, namespace):
    if len(raw) > MAX_STDOUT_BYTES or not _name(namespace):
        raise ValueError()
    lines = raw.decode('ascii').split('\n')
    if lines[-1] != '' or len(lines) < 3:
        raise ValueError()
    frame = lines[0].split('\t')
    if (len(frame) != 5 or frame[:2] != ['L', 'v1'] or frame[2] != 'List'
            or frame[3] not in ('', 'null') or frame[4] not in ('', 'null', '0')
            or lines[-2] != 'Z'):
        raise ValueError()
    controllers, names, identities, current, total = [], set(), set(), None, 0
    for line in lines[1:-2]:
        cells = line.split('\t')
        if cells[0] == 'P':
            if current is not None or len(cells) != 10 or len(controllers) >= MAX_CONTROLLERS:
                raise ValueError()
            _, api, kind, ns, name, identity, nr, user, cron_nr, cron_user = cells
            if (APIS.get(kind) != api or ns != namespace or not _name(name, subdomain=True)
                    or (kind, name) in names or identity in identities
                    or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', identity, re.ASCII)):
                raise ValueError()
            names.add((kind, name))
            identities.add(identity)
            if kind == 'CronJob':
                nr, user, cron_nr, cron_user = cron_nr, cron_user, nr, user
            if cron_nr or cron_user:
                raise ValueError()
            prefix = 'J' if kind == 'CronJob' else 'T'
            pod_nr, pod_uid = _boolean(nr), _uid(user)
            current = dict(kind=kind, name=name, containers=[])
            controllers.append(current)
            container_names, counts, last_kind = set(), dict.fromkeys(KINDS, 0), -1
        elif cells == ['Q']:
            if current is None or not counts['C']:
                raise ValueError()
            current = None
        else:
            if current is None or len(cells) != 8 or cells[0] not in (prefix + 'C', prefix + 'I') or total >= MAX_CONTAINERS:
                raise ValueError()
            marker, name, ro, nr, user, caps, cpu, memory = cells
            marker = marker[1:]
            rank = list(KINDS).index(marker)
            if rank < last_kind or not _name(name) or name in container_names:
                raise ValueError()
            last_kind = rank
            container_names.add(name); counts[marker] += 1; total += 1
            ro, nr, user = _boolean(ro), _boolean(nr), _uid(user)
            nr = pod_nr if nr is None else nr
            user = pod_uid if user is None else user
            drop, cpu, memory = _drop_all(caps), _nonzero(cpu), _nonzero(memory)
            checks = dict(read_only_root_filesystem=ro is True, non_root=nr is True and user != 0,
                          drop_all_capabilities=drop, cpu_memory_limits=cpu and memory)
            current['containers'].append(dict(name=name, kind=KINDS[marker], ordinal=counts[marker],
                checks={key: 'met' if value else 'not_met' for key, value in checks.items()}))
    if current is not None:
        raise ValueError()
    return controllers


def _collect(foundation, check):
    argv = [('--request-timeout=3s' if arg == '--request-timeout=15s' else arg)
            for arg in logical_foundation.kube_argv(foundation)]
    check()
    deadline = time.monotonic() + 8
    process = subprocess.Popen(argv + ['get', RESOURCES, '--chunk-size=0', '-o', JSONPATH],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False)
    raw = b''
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise subprocess.TimeoutExpired(argv, 8)
                chunk = process.stdout.read1(MAX_STDOUT_BYTES + 1 - len(raw))
                if not chunk:
                    break
                raw += chunk
                if len(raw) > MAX_STDOUT_BYTES:
                    raise ValueError()
        remaining = deadline - time.monotonic()
        if remaining <= 0 or process.wait(timeout=remaining) != 0:
            raise ValueError()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
    check()
    return _parse(raw, foundation['kube']['namespace'])


def observe(principal, role):
    fingerprint = _authority(principal, role)
    def check():
        if _authority(principal, role) != fingerprint:
            raise OperationError('PLATFORM_ACCESS_DENIED', 403)
    source = dict(availability='unavailable', code='CONTROLLER_BASELINE_UNAVAILABLE',
                  observed_at=_now(), namespace=None, controller_count=None, container_count=None, controllers=None)
    result = dict(scope='platform', warehouse='LOCAL', selected_role='admin', observed_at=_now(),
        baseline_version='namespace-controller-template-declarations-v1',
        coverage=dict(kind='namespace_controller_template_declarations', controller_template=True,
                      per_container=True, runtime_security=False, admission_decision=False,
                      global_policy=False), baseline=source)
    try:
        foundation = _foundation(check)
        check()
        try:
            controllers = _collect(foundation, check)
        finally:
            # Recheck even if spawn/read/wait/parse failed; auth loss always wins.
            check()
        after = _foundation(check)
        check()
        if after != foundation:
            raise ValueError()
        source.update(availability='available', code=None, observed_at=_now(), namespace=foundation['kube']['namespace'],
                      controller_count=len(controllers), container_count=sum(len(p['containers']) for p in controllers), controllers=controllers)
        result['observed_at'] = _now()
        if len(json.dumps(result, default=str).encode('utf-8')) > MAX_DTO_BYTES:
            raise ValueError()
    except SOURCE_ERRORS as error:
        if isinstance(error, OperationError) and error.code == 'PLATFORM_ACCESS_DENIED':
            raise
        source.update(availability='unavailable', code='CONTROLLER_BASELINE_UNAVAILABLE',
                      observed_at=_now(), namespace=None, controller_count=None, container_count=None, controllers=None)
    check()
    return result
