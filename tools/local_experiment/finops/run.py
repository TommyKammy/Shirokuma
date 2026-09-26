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
    C.require(type(raw) is str and len(raw.encode()) <= 16384, 'DISK_INVALID')
    lines = raw.strip().splitlines()
    C.require(len(lines) == 2, 'DISK_INVALID')
    fields = lines[1].split()
    C.require(len(fields) == 6 and fields[1].isdigit() and fields[2].isdigit() and fields[3].isdigit(), 'DISK_INVALID')
    total, used, available = (int(fields[i]) * 1024 for i in (1, 2, 3))
    C.require(0 < total <= 2**63-1 and 0 <= used <= total and 0 <= available <= total, 'DISK_INVALID')
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
            return raw.decode('utf-8')
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
            C.require(nodes.get('kind') == 'NodeList' and nodes.get('apiVersion') == 'v1' and
                      not nodes.get('metadata', {}).get('continue') and len(nodes.get('items', [])) == 1,
                      'SCOPE_INVALID')
            info = nodes['items'][0]['status']['nodeInfo']
            C.require(info.get('architecture') == 'arm64' and 'k3s' in info.get('kubeletVersion', ''), 'SCOPE_INVALID')
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
        C.require(value.get('kind') == 'PodList' and value.get('apiVersion') == 'v1' and
                  type(value.get('items')) is list, 'SCOPE_INVALID')
        items = value['items']
        C.require(not value.get('metadata', {}).get('continue') and len(items) <= 1, 'TARGET_AMBIGUOUS')
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
        C.require(type(context) is list and len(context) == 1 and
                  context[0]['Endpoints']['docker']['Host'].startswith('unix://') and
                  context[0]['Endpoints']['docker']['Host'].endswith('/.colima/mac-studio-solo/docker.sock'), 'SCOPE_INVALID')
        value = _json(self._command(['docker', '--context', AH.CONTEXT, 'container', 'inspect', AH.NAME]))
        C.require(type(value) is list and len(value) == 1, 'SCOPE_INVALID')
        container = value[0]
        C.require(container['Config']['Labels'].get(AH.LABEL) == 'agenthouse-local' and
                  container['Config']['Image'] == AH.IMAGE and container['State']['Running'] is True,
                  'SCOPE_INVALID')
        ports = container['NetworkSettings']['Ports'].get('8123/tcp')
        C.require(type(ports) is list and len(ports) == 1 and ports[0] ==
                  {'HostIp': '127.0.0.1', 'HostPort': '18123'}, 'SCOPE_INVALID')
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
                             '--measure-mlflow-store'], timeout=20)
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
        host_storage = H.unknown_all(reason(error), version=3)
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
