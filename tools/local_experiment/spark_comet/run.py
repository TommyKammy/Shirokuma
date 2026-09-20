"""Run the fixed Comet native gate without network, host mounts or a remote catalog."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.spark import prepare as SP
from tools.local_experiment.spark.run import save, error_code
from tools.local_experiment.spark_comet import contract as C, build as B
from tools.local_experiment.trino_opa.common import directory, read, require, run_lock
from tools.local_experiment import local_session as SESSION

STATE = ROOT / 'tmp/local-experiment/spark-comet-runs'
OWNER = 'shirokuma-local-spark-comet-classpath'
LABEL = 'shirokuma.io/experiment'
TOKEN = re.compile(r'[0-9a-f]{32}')
IDENTITY = re.compile(r'[0-9a-f]{64}')
IMAGE = re.compile(r'sha256:[0-9a-f]{64}')
# Zstd/Netty extract bundled ARM64 JNI libraries here and mmap executable pages.
# Docker's tmpfs default is noexec even when noexec is omitted from the options.
TMPFS = {'/tmp': 'rw,exec,nosuid,nodev,size=512m,uid=185,gid=185,mode=1777',
         '/opt/spark/work-dir': 'rw,noexec,nosuid,nodev,size=64m,uid=185,gid=185,mode=0700'}


def token(value):
    require(type(value) is str and TOKEN.fullmatch(value), 'INVALID_RUN_TOKEN')
    return value


def name(value):
    return 'shirokuma-spark-comet-' + token(value)


def now():
    return datetime.now(timezone.utc).isoformat()


def retain_error(first, later):
    if first is not None and not isinstance(first, Exception):
        return first
    if not isinstance(later, Exception):
        return later
    return first if first is not None else later


def create_arguments(run_token, image):
    token(run_token)
    require(type(image) is str and IMAGE.fullmatch(image), 'INVALID_IMAGE_ID')
    args = ['create', '--name', name(run_token), '--hostname', 'localhost', '--label', LABEL+'='+OWNER,
            '--label', 'shirokuma.io/run-token='+run_token, '--platform', 'linux/arm64',
            '--network', 'none', '--read-only', '--user', '185:185', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--cpus', '1', '--memory', '2g',
            '--memory-swap', '2g', '--pids-limit', '256', '--stop-timeout', '10']
    for destination, options in TMPFS.items():
        args += ['--tmpfs', destination+':'+options]
    for value in ('TMPDIR=/tmp', 'SPARK_LOCAL_IP=127.0.0.1', 'PYTHONDONTWRITEBYTECODE=1'):
        args += ['--env', value]
    return args + ['--entrypoint', '/opt/spark/bin/spark-submit', image, *C.spark_arguments(run_token)]


def validate_container(value, run_token, image, container_id):
    require(type(container_id) is str and IDENTITY.fullmatch(container_id), 'CONTAINER_ID_INVALID')
    require(value.get('Id') == container_id and value.get('Name') == '/'+name(run_token)
            and value.get('Image') == image, 'CONTAINER_IDENTITY_CHANGED')
    config, host = value.get('Config', {}), value.get('HostConfig', {})
    require(config.get('Labels', {}).get(LABEL) == OWNER and
            config.get('Labels', {}).get('shirokuma.io/run-token') == run_token, 'CONTAINER_OWNER_CHANGED')
    require(config.get('Hostname') == 'localhost' and config.get('User') == '185:185' and config.get('Entrypoint') == ['/opt/spark/bin/spark-submit']
            and config.get('Cmd') == C.spark_arguments(run_token), 'CONTAINER_COMMAND_CHANGED')
    require(host.get('NetworkMode') == 'none' and not host.get('Privileged') and
            host.get('ReadonlyRootfs') is True and host.get('CapDrop') == ['ALL'] and not host.get('CapAdd')
            and host.get('SecurityOpt') == ['no-new-privileges'], 'CONTAINER_SECURITY_CHANGED')
    require(host.get('NanoCpus') == 1_000_000_000 and host.get('Memory') == 2*1024**3 and
            host.get('MemorySwap') == 2*1024**3 and host.get('PidsLimit') == 256, 'CONTAINER_BOUNDS_CHANGED')
    require(not host.get('Binds') and not host.get('Mounts') and not host.get('Devices') and
            not host.get('DeviceRequests') and not host.get('PortBindings') and
            host.get('Tmpfs') == TMPFS and not host.get('AutoRemove') and
            host.get('RestartPolicy', {}).get('Name') in ('no', ''), 'CONTAINER_MOUNTS_CHANGED')
    require(all(m.get('Type') == 'tmpfs' and m.get('Destination') in TMPFS for m in value.get('Mounts', [])),
            'CONTAINER_HOST_MOUNT')
    env = config.get('Env', [])
    for expected in ('TMPDIR=/tmp', 'SPARK_LOCAL_IP=127.0.0.1', 'PYTHONDONTWRITEBYTECODE=1'):
        require([e for e in env if e.split('=', 1)[0] == expected.split('=', 1)[0]] == [expected], 'CONTAINER_ENV_CHANGED')
    return value


class Docker:
    def call(self, *args, **kwargs):
        return SP.command(SP.docker(*args), **kwargs)

    def guard(self):
        return SP.guard()

    def find(self, *, container_id=None, run_token=None):
        expression = 'id='+container_id if container_id else 'name=^/'+name(run_token)+'$'
        rows = self.call('container', 'ls', '--all', '--no-trunc', '--filter', expression, '--format', '{{.ID}}').splitlines()
        require(len(rows) <= 1 and all(IDENTITY.fullmatch(x) for x in rows), 'CONTAINER_LOOKUP_AMBIGUOUS')
        if not rows:
            return None
        require(container_id is None or rows == [container_id], 'CONTAINER_ID_CHANGED')
        result = json.loads(self.call('container', 'inspect', rows[0]))
        require(type(result) is list and len(result) == 1, 'CONTAINER_INSPECT_INVALID')
        return result[0]

    def create(self, run_token, image):
        return self.call(*create_arguments(run_token, image)).strip()

    def start(self, container_id):
        self.call('start', container_id)

    def logs(self, container_id, output):
        path = output / 'docker-logs.json'
        self.call('logs', container_id, timeout=15, output_limit=C.MAX_LOG_BYTES, log_path=path)
        log = json.loads(read(path))
        return log['stdout'] + '\n' + log['stderr']

    def stop(self, container_id):
        self.call('stop', '--time', '10', container_id, timeout=25)

    def remove(self, container_id):
        self.call('rm', container_id)


class Experiment:
    def __init__(self, run_token, image, build_token, output, *, docker=None, clock=time.monotonic, sleep=time.sleep):
        token(run_token); token(build_token)
        require(type(image) is str and IMAGE.fullmatch(image), 'INVALID_IMAGE_ID')
        self.token, self.image, self.output = run_token, image, Path(output)
        self.docker, self.clock, self.sleep = docker or Docker(), clock, sleep
        self.receipt = {'schema_version': 1, 'run_token': run_token, 'build_token': build_token,
                        'image': image, 'result': 'running', 'started_at': now(), 'container_id': None,
                        'pending': None, 'cleanup': {'verified': False},
                        'source_sha256': B.sources() | {str(Path(__file__).relative_to(ROOT)): hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}}

    def persist(self):
        save(self.output / 'summary.json', self.receipt)

    def inspect(self):
        cid = self.receipt['container_id']
        value = self.docker.find(container_id=cid)
        if value is not None:
            validate_container(value, self.token, self.image, cid)
        return value

    def cleanup(self):
        result = {'verified': False, 'removed': False}
        self.receipt['cleanup'] = result
        original = None
        try:
            cid = self.receipt['container_id']
            if cid is None:
                require(not self.receipt.get('creation_attempted'), 'CONTAINER_CREATE_OUTCOME_UNKNOWN')
                result['verified'] = True
                return result
            require(self.docker.guard() == self.receipt['runtime'], 'RUNTIME_CHANGED')
            value = self.inspect()
            if value is None:
                result.update(verified=True, already_absent=True)
                return result
            save(self.output / 'cleanup-before.json', value)
            if value['State'].get('Running'):
                self.receipt['pending'] = 'stop'; self.persist()
                self.docker.stop(cid)
                value = self.inspect()
            require(value is not None and value['State'].get('Running') is False, 'CONTAINER_NOT_STOPPED')
            self.receipt['pending'] = 'remove'; self.persist()
            self.docker.remove(cid)
            require(self.docker.find(container_id=cid) is None, 'CONTAINER_REMAINS')
            result.update(verified=True, removed=True)
            self.receipt['pending'] = None
        except BaseException as error:
            original = error
            result['deferred'] = error_code(error)
        finally:
            try:
                self.persist()
            finally:
                if original is not None and not isinstance(original, Exception):
                    raise original
        return result

    def run(self):
        original = None
        self.persist()
        try:
            self.receipt['runtime'] = self.docker.guard()
            require(self.docker.find(run_token=self.token) is None, 'CONTAINER_NAME_EXISTS')
            self.receipt.update(pending='create', creation_attempted=True); self.persist()
            cid = self.docker.create(self.token, self.image)
            require(IDENTITY.fullmatch(cid), 'CREATE_ID_UNKNOWN')
            self.receipt.update(container_id=cid, pending=None); self.persist()
            value = self.inspect(); require(value is not None, 'CREATED_CONTAINER_MISSING')
            save(self.output / 'created-container.json', value)
            self.receipt['pending'] = 'start'; self.persist()
            self.docker.start(cid); self.receipt['pending'] = None; self.persist()
            deadline = self.clock() + 180
            while True:
                value = self.inspect(); require(value is not None, 'RUNNING_CONTAINER_MISSING')
                if value['State'].get('Running') is False:
                    break
                require(self.clock() < deadline, 'WORKLOAD_TIMEOUT'); self.sleep(.5)
            save(self.output / 'final-container.json', value)
            logs = self.docker.logs(cid, self.output)
            state = value['State']
            self.receipt['exit_code'] = state.get('ExitCode')
            self.receipt['output'] = C.parse_output(logs, self.token)
            require(state.get('Status') == 'exited' and type(state.get('ExitCode')) is int and
                    state['ExitCode'] == 0 and not state.get('OOMKilled') and not state.get('Error'), 'WORKLOAD_EXIT_FAILURE')
            require(self.receipt['output'].get('result') == 'passed', 'WORKLOAD_RESULT_FAILURE')
            self.receipt['workload_verified'] = True
        except BaseException as error:
            original = error
            self.receipt['failure_code'] = error_code(error)
        finally:
            if original is not None and self.receipt['container_id']:
                try:
                    if self.inspect() is not None:
                        self.docker.logs(self.receipt['container_id'], self.output)
                except BaseException as diagnostic:
                    self.receipt['diagnostic_error'] = error_code(diagnostic)
                    original = retain_error(original, diagnostic)
            try:
                self.cleanup()
            except BaseException as cleanup:
                original = retain_error(original, cleanup)
            self.receipt.update(result='passed' if original is None and self.receipt['cleanup']['verified'] else 'failed', finished_at=now())
            try:
                self.persist()
            except BaseException as saving:
                original = retain_error(original, saving)
            if original is not None:
                raise original
        require(self.receipt['cleanup']['verified'], 'CLEANUP_NOT_VERIFIED')
        return self.receipt


def recover(run_token, *, docker=None):
    output = STATE / token(run_token)
    with run_lock(output):
        receipt = json.loads(read(output / 'summary.json'))
        require(receipt.get('run_token') == run_token and IMAGE.fullmatch(receipt.get('image', '')) and
                TOKEN.fullmatch(receipt.get('build_token', '')) and
                (receipt.get('container_id') is None or IDENTITY.fullmatch(receipt['container_id'])), 'SAVED_IDENTITY_INVALID')
        target = output / ('recovery-'+uuid.uuid4().hex)
        directory(target, create=True)
        instance = Experiment(run_token, receipt['image'], receipt['build_token'], target, docker=docker)
        instance.receipt = deepcopy(receipt)
        instance.receipt.update(result='recovery', original_sha256=hashlib.sha256((output/'summary.json').read_bytes()).hexdigest())
        instance.cleanup()
        require(instance.receipt['cleanup']['verified'], 'CLEANUP_NOT_VERIFIED')
        return instance.receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('run', 'recover'))
    parser.add_argument('--build-token')
    parser.add_argument('--run-token')
    args = parser.parse_args(argv)
    try:
        with SESSION.interruption_handlers():
            if args.action == 'recover':
                require(args.build_token is None, 'UNEXPECTED_BUILD_TOKEN')
                value = recover(token(args.run_token))
            else:
                require(args.run_token is None, 'RUN_TOKEN_GENERATED_ONLY')
                built = B.read_verified_image(token(args.build_token))
                run_token = uuid.uuid4().hex
                directory(STATE, create=True)
                output = STATE / run_token
                output.mkdir(mode=0o700)
                with run_lock(output):
                    value = Experiment(run_token, built['image_id'], args.build_token, output).run()
        print(json.dumps({'result': value['result'], 'run_token': value['run_token'],
                          'evidence': str((STATE/value['run_token']).relative_to(ROOT))}))
        return 0
    except BaseException as error:
        print(json.dumps({'result': 'failed', 'error_code': error_code(error)}))
        return 128 + error.signum if isinstance(error, SESSION.SessionInterrupted) else 1


if __name__ == '__main__':
    raise SystemExit(main())
