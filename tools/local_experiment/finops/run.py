#!/usr/bin/env python3
"""Read a bounded FinOps report without starting services or writing evidence."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import selectors
import signal
import stat
import subprocess
import sys
import time
import unicodedata
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools/local_experiment'))
from tools.local_experiment.finops import contract as C
from tools.local_experiment.finops import local_processes as P
from tools.local_experiment.finops import storage as S
from tools.local_experiment.finops import docker_storage as D
from tools.local_experiment.finops import host_storage as H
from tools.local_experiment.gateway import pod_usage as U
from tools.local_experiment.agenthouse import manage as AH
from tools.local_experiment import local_session as SESSION

CONTEXT = 'colima-mac-studio-solo'
VM_PATH = '/var/lib/rancher/k3s/storage'
MAX_RESPONSE = 2 * 1024 * 1024


def _json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            C.require(key not in value, 'READ_FAILED'); value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(C.FinOpsError('READ_FAILED')))


def _complete_list(value, kind):
    if type(value) is not dict:
        return False
    metadata = value.get('metadata', {})
    # A typed Kubernetes list has only TypeMeta, ListMeta and items; an
    # unrecognized sibling must not silently describe a partial list.
    return (set(value) in ({'kind', 'apiVersion', 'items'},
                           {'kind', 'apiVersion', 'items', 'metadata'}) and
             value.get('kind') == kind and value.get('apiVersion') == 'v1' and
             type(metadata) is dict and
             set(metadata) <= {'selfLink', 'resourceVersion', 'continue', 'remainingItemCount'} and
             all(type(metadata[key]) is str for key in ('selfLink', 'resourceVersion') if key in metadata) and
            ('remainingItemCount' not in metadata or
             (type(metadata['remainingItemCount']) is int and metadata['remainingItemCount'] == 0)) and
            metadata.get('continue', '') == '' and
            type(metadata.get('continue', '')) is str and type(value.get('items')) is list)


def inference_sql(end):
    bounds = C.window(end)
    fields = ['model_provider', 'model_name', 'toString(count()) AS events']
    for metric in C.METRICS:
        fields.append(f'toString(countIf(isNotNull({metric}))) AS {metric}_known')
        number = f'toString(sum(toUInt128(ifNull({metric}, 0))))' if metric in C.INTEGER_METRICS else f'sum({metric})'
        fields.append(f'if(countIf(isNotNull({metric})) = 0, NULL, {number}) AS {metric}_sum')
    # Only timestamps generated from a parsed UTC instant enter this fixed SQL.
    def sqltime(value): return value.replace('T', ' ').removesuffix('Z')
    return ('SELECT ' + ', '.join(fields) + ' FROM agenthouse.pawprints FINAL '
            "WHERE repo = 'Shirokuma' AND model_provider != 'none' AND "
            f"timestamp >= toDateTime64('{sqltime(bounds['start'])}', 3, 'UTC') AND "
            f"timestamp < toDateTime64('{sqltime(bounds['end'])}', 3, 'UTC') "
            'GROUP BY model_provider, model_name ORDER BY model_provider, model_name LIMIT 51 '
            'SETTINGS max_execution_time=10, max_result_rows=51, result_overflow_mode=\'throw\', '
            'max_result_bytes=1048576 FORMAT JSONEachRow')


def parse_disk(raw):
    C.require(type(raw) is str and len(raw) <= 16384, 'DISK_INVALID')
    try:
        encoded_size = len(raw.encode('utf-8'))
    except UnicodeError:
        raise C.FinOpsError('DISK_INVALID') from None
    C.require(encoded_size <= 16384, 'DISK_INVALID')
    lines = raw.strip().splitlines()
    C.require(len(lines) == 2 and
              lines[0].split() == ['Filesystem', '1024-blocks', 'Used', 'Available',
                                   'Capacity', 'Mounted', 'on'], 'DISK_INVALID')
    fields = lines[1].split()
    # Do not accept control-bearing device identifiers as capacity provenance.
    C.require(len(fields) == 6 and fields[0] and fields[5].startswith('/') and
              all(unicodedata.category(char) not in ('Cc', 'Cf')
                  for char in fields[0]) and
              all(len(fields[i]) <= 19 and fields[i].isascii() and fields[i].isdigit()
                  for i in (1, 2, 3)) and
              fields[4].endswith('%') and fields[4][:-1].isascii() and
              len(fields[4]) <= 4 and fields[4][:-1].isdigit() and
               int(fields[4][:-1]) <= 100, 'DISK_INVALID')
    # df reports the filesystem mount, not necessarily the requested directory.
    # Refuse a valid-looking row for an unrelated filesystem or a noncanonical path.
    mount = fields[5]
    C.require(mount == '/' or (mount == '/' + '/'.join(part for part in mount.split('/') if part) and
              all(part not in ('.', '..') for part in mount.split('/')[1:]) and
              (mount == VM_PATH or VM_PATH.startswith(mount + '/'))), 'DISK_INVALID')
    total, used, available = (int(fields[i]) * 1024 for i in (1, 2, 3))
    C.require(0 < total <= 2**63-1 and used + available <= total, 'DISK_INVALID')
    # GNU df rounds up Used / (Used + Available); reserved blocks are not
    # available to ordinary users and must not enter this denominator.
    C.require(used + available > 0 and
              int(fields[4][:-1]) == (100 * used + used + available - 1) // (used + available),
              'DISK_INVALID')
    return total, available


class Backend:
    def __init__(self):
        self.server = None
        self.environment = {key: os.environ[key] for key in ('PATH', 'HOME', 'USER', 'TMPDIR', 'LANG', 'LC_ALL')
                            if key in os.environ}

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

    def _command(self, argv, *, timeout=10):
        # These argument vectors are fixed local df/Docker inspection commands.
        child = selector = None
        original = None
        deadline = time.monotonic() + timeout
        try:
            child = subprocess.Popen(['rtk', 'proxy', *argv], cwd=ROOT, env=self.environment,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
            os.set_blocking(child.stdout.fileno(), False)
            selector = selectors.DefaultSelector(); selector.register(child.stdout, selectors.EVENT_READ)
            raw = bytearray()
            while True:
                remaining = deadline-time.monotonic()
                C.require(remaining > 0 and selector.select(remaining), 'READ_TIMEOUT')
                chunk = os.read(child.stdout.fileno(), min(4096, MAX_RESPONSE+1-len(raw)))
                if not chunk: break
                raw.extend(chunk); C.require(len(raw) <= MAX_RESPONSE, 'READ_LIMIT')
            C.require(child.wait(timeout=max(0, deadline-time.monotonic())) == 0, 'READ_FAILED')
            try:
                return raw.decode('utf-8')
            except UnicodeDecodeError:
                raise C.FinOpsError('READ_FAILED') from None
        except BaseException as error:
            original = error; raise
        finally:
            failures = []
            if child is not None:
                try:
                    # Do not poll/reap an exited leader before signalling its
                    # group: descendants may still hold stdout open. A None
                    # returncode means this function has not released its PID.
                    if child.returncode is None:
                        try: os.killpg(child.pid, signal.SIGKILL)
                        except ProcessLookupError: pass
                except BaseException as error: failures.append(error)
                try: child.wait(timeout=2)
                except BaseException as error: failures.append(error)
            for handle in (selector, child.stdout if child is not None else None):
                if handle is not None:
                    try: handle.close()
                    except BaseException as error: failures.append(error)
            if original is None or isinstance(original, Exception):
                interruption = next((error for error in failures if not isinstance(error, Exception)), None)
                if interruption is not None: raise interruption
                if failures: raise U.PodUsageCleanupError() from None

    def _read(self, path, deadline):
        return U._command(['--server', self.server, '--request-timeout=6s', 'get', '--raw', path],
                          deadline=deadline, environment=self.environment, _max_bytes=MAX_RESPONSE)

    def _guard(self, deadline):
        if self.server is not None: return
        self.server = U._local_server(deadline=deadline, environment=self.environment)
        try:
            nodes = self._read('/api/v1/nodes', deadline)
            C.require(_complete_list(nodes, 'NodeList') and len(nodes['items']) == 1, 'SCOPE_INVALID')
            node = nodes['items'][0]
            C.require(type(node) is dict and type(node.get('status')) is dict, 'SCOPE_INVALID')
            info = node['status'].get('nodeInfo')
            C.require(type(info) is dict and info.get('architecture') == 'arm64' and
                      type(info.get('kubeletVersion')) is str and 'k3s' in info['kubeletVersion'],
                      'SCOPE_INVALID')
        except BaseException:
            self.server = None; raise

    def component(self, component):
        namespace, label, _ = C.COMPONENTS[component]
        deadline = time.monotonic() + 20
        self._guard(deadline)
        labels = 'app.kubernetes.io/name=' + label
        if component == 'polaris': labels += ',app.kubernetes.io/component=catalog-server'
        selector = quote(labels, safe='')
        value = self._read(f'/api/v1/namespaces/{namespace}/pods?labelSelector={selector}&limit=2', deadline)
        C.require(_complete_list(value, 'PodList'), 'SCOPE_INVALID')
        items = value['items']
        C.require(len(items) <= 1, 'TARGET_AMBIGUOUS')
        C.require(len(items) == 1, 'TARGET_MISSING')
        before = items[0]
        identity, _ = C._identity(component, before)
        name = identity['pod_name']
        started = self.now()
        metrics = self._read(f'/apis/metrics.k8s.io/v1beta1/namespaces/{namespace}/pods/{name}', deadline)
        after = self._read(f'/api/v1/namespaces/{namespace}/pods/{name}', deadline)
        return {'before': before, 'metrics': metrics, 'after': after,
                'request_started_at': started, 'observed_at': self.now()}

    def disk(self, scope):
        if scope == 'host':
            value = shutil.disk_usage(ROOT)
            return value.total, value.free
        C.require(scope == 'colima', 'SCOPE_INVALID')
        machine = self._command(['colima', 'ssh', '--profile', 'mac-studio-solo', '--', 'uname', '-m']).strip()
        C.require(machine in ('aarch64', 'arm64'), 'SCOPE_INVALID')
        return parse_disk(self._command(['colima', 'ssh', '--profile', 'mac-studio-solo', '--', 'df', '-Pk', VM_PATH]))

    def inference(self, end):
        # Never call ensure_runtime: it can create containers/database/schema.
        context = _json(self._command(['docker', 'context', 'inspect', AH.CONTEXT]))
        C.require(type(context) is list and len(context) == 1 and type(context[0]) is dict,
                  'SCOPE_INVALID')
        endpoints = context[0].get('Endpoints')
        C.require(type(endpoints) is dict and type(endpoints.get('docker')) is dict, 'SCOPE_INVALID')
        host = endpoints['docker'].get('Host')
        C.require(type(host) is str and host.startswith('unix://') and
                  host.endswith('/.colima/mac-studio-solo/docker.sock'), 'SCOPE_INVALID')
        value = _json(self._command(['docker', '--context', AH.CONTEXT, 'container', 'inspect', AH.NAME]))
        C.require(type(value) is list and len(value) == 1 and type(value[0]) is dict,
                  'SCOPE_INVALID')
        container = value[0]
        config, state, network = (container.get(key) for key in ('Config', 'State', 'NetworkSettings'))
        C.require(type(config) is dict and type(config.get('Labels')) is dict and
                  type(state) is dict and type(network) is dict and
                  type(network.get('Ports')) is dict, 'SCOPE_INVALID')
        C.require(config['Labels'].get(AH.LABEL) == 'agenthouse-local' and
                  config.get('Image') == AH.IMAGE and state.get('Running') is True,
                  'SCOPE_INVALID')
        ports = network['Ports'].get('8123/tcp')
        C.require(type(ports) is list and len(ports) == 1 and ports[0] ==
                  {'HostIp': '127.0.0.1', 'HostPort': '18123'} and
                  all(binding is None for port, binding in network['Ports'].items()
                      if port != '8123/tcp'), 'SCOPE_INVALID')
        path = AH.STATE / 'credential.json'
        parent, before = path.parent.lstat(), path.lstat()
        C.require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.getuid() and
                  stat.S_IMODE(parent.st_mode) == 0o700 and stat.S_ISREG(before.st_mode) and
                  before.st_uid == os.getuid() and stat.S_IMODE(before.st_mode) == 0o600 and
                  before.st_nlink == 1 and before.st_size <= 16384, 'SCOPE_INVALID')
        client = AH.Client()
        after = path.lstat()
        C.require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
                  (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), 'SCOPE_INVALID')
        raw = client.query(inference_sql(end))
        C.require(len(raw.encode()) <= 1024**2, 'READ_LIMIT')
        return [_json(line) for line in raw.splitlines() if line.strip()]

    def local_processes(self):
        # Runtime imports are isolated in the already installed Lab environment.
        # No caller-supplied command, PID, environment or service can be selected.
        raw = self._command([str(ROOT/'tools/local_experiment/notebook_lab/.venv/bin/python'),
            '-I', '-B', str(ROOT/'tools/local_experiment/finops/local_processes.py'), '--worker'], timeout=20)
        return P.validate(_json(raw))

    def storage_get(self, path):
        deadline = time.monotonic() + 20
        self._guard(deadline)
        return self._read(path, deadline)

    def storage(self):
        return S.collect(self)

    def docker_storage_inspect(self, kind, name):
        C.require((kind, name) in (('context', AH.CONTEXT), ('container', AH.NAME),
                                  ('volume', AH.VOLUME)), 'SCOPE_INVALID')
        return _json(self._command(['docker', '--context', AH.CONTEXT, kind, 'inspect', name]
                                   if kind != 'context' else ['docker', 'context', 'inspect', name]))

    def docker_storage(self):
        return D.collect(self)

    def host_storage(self):
        raw = self._command([str(ROOT/'tools/local_experiment/notebook_lab/.venv/bin/python'),
                             '-I', '-B', str(ROOT/'tools/local_experiment/finops/host_storage.py'),
                               '--measure-serving-tmp'], timeout=20)
        return H.validate(_json(raw))


def reason(error, fallback='READ_FAILED'):
    if isinstance(error, U.PodUsageCleanupError): return 'CLEANUP_UNVERIFIED'
    if isinstance(error, subprocess.TimeoutExpired): return 'READ_TIMEOUT'
    if type(error) in (C.FinOpsError, U.PodUsageError) and str(error) in C.REASONS: return str(error)
    return fallback


def collect(backend=None):
    backend = backend or Backend()
    end = backend.now()
    components, disks = [], []
    unsafe = False
    for component in C.COMPONENTS:
        try:
            if unsafe: raise C.FinOpsError('CLEANUP_UNVERIFIED')
            values = backend.component(component)
            components.append(C.project_component(component, **values))
        except Exception as error:
            code = reason(error); unsafe = unsafe or code == 'CLEANUP_UNVERIFIED'
            components.append(C.unknown_component(component, code))
    for scope in C.DISKS:
        try:
            if unsafe: raise C.FinOpsError('CLEANUP_UNVERIFIED')
            total, free = backend.disk(scope)
            disks.append(C.disk(scope, total, free, observed_at=backend.now()))
        except Exception as error:
            code = reason(error); unsafe = unsafe or code == 'CLEANUP_UNVERIFIED'
            disks.append(C.disk(scope, observed_at=backend.now(), reason=code))
    try:
        if unsafe: raise C.FinOpsError('CLEANUP_UNVERIFIED')
        inference = C.inference(backend.inference(end), end=end)
    except Exception as error:
        code = reason(error); unsafe = unsafe or code == 'CLEANUP_UNVERIFIED'
        inference = C.inference(None, end=end, reason=code)
    try:
        if unsafe: raise C.FinOpsError('CLEANUP_UNVERIFIED')
        processes = P.validate(backend.local_processes())
    except Exception as error:
        code = reason(error); unsafe = unsafe or code == 'CLEANUP_UNVERIFIED'
        processes = P.unknown_all(code)
    if any(item['reason'] == 'CLEANUP_UNVERIFIED' for item in processes['components']):
        unsafe = True
    try:
        if unsafe: raise C.FinOpsError('CLEANUP_UNVERIFIED')
        storage = S.validate(backend.storage())
    except Exception as error:
        code = reason(error); unsafe = unsafe or code == 'CLEANUP_UNVERIFIED'
        storage = S.unknown_all(code)
    if any(item['reason'] == 'CLEANUP_UNVERIFIED' for item in storage['components']):
        unsafe = True
    try:
        if unsafe: raise C.FinOpsError('CLEANUP_UNVERIFIED')
        docker_storage = D.validate(backend.docker_storage())
    except Exception as error:
        code = reason(error); unsafe = unsafe or code == 'CLEANUP_UNVERIFIED'
        docker_storage = D.validate(dict(schema_version=1, agenthouse=D.unknown(code), limits=D.LIMITS[:]))
    if docker_storage['agenthouse']['reason'] == 'CLEANUP_UNVERIFIED': unsafe = True
    try:
        if unsafe: raise C.FinOpsError('CLEANUP_UNVERIFIED')
        host_storage = H.validate(backend.host_storage())
    except Exception as error:
        host_storage = H.unknown_all(reason(error), version=5)
    value = C.build_report(components, disks, inference, observed_at=backend.now(),
                           local_processes=processes, storage=storage, docker_storage=docker_storage,
                           host_storage=host_storage)
    return C.validate_public(value)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    with SESSION.interruption_handlers():
        report = collect()
    print(json.dumps(report, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(',', ':')))
    return 0 if report['result'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
