#!/usr/bin/env python3
"""Execute the fixed local Notebook with an owned, temporary Python kernel.

nbclient's automatic setup/cleanup installs signal handlers and can kill a
kernel before its Reader owner has cleaned up. We own that lifecycle and call
only async_execute_cell. A deadline interrupts first and preserves uncertainty.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid

import nbformat
from nbclient import NotebookClient
from jupyter_client import AsyncKernelManager
from jupyter_client.kernelspec import KernelSpecManager

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.local_experiment.notebook import selection as S
from tools.local_experiment.notebook.client import REQUEST

HERE = Path(__file__).resolve().parent
TEMPLATE = ROOT / 'examples/local_lakehouse.ipynb'
PREVIEW_TEMPLATE = ROOT / 'examples/local_table_preview.ipynb'
PREVIEW_PROFILE = 'table-preview-v1'
STATE = ROOT / 'tmp/local-experiment/notebook-runs'
EXECUTION_SECONDS = 900
TRAINING_REVIEW_SECONDS = 60
TRAIN_TEMPLATE = ROOT / 'examples/local_lake_training.ipynb'
TRAIN_TEMPLATE_SHA256 = '5921fbed97be1fd428cd9259fb0f909bc66e7f55d84c736ad2a1b029a7754e78'
READER_GRACE_SECONDS = 260
KERNEL_GRACE_SECONDS = 30


class NotebookInterrupted(BaseException):
    def __init__(self, signum):
        self.signum = signum
        super().__init__('Notebook interrupted')


def preserve_error(first, subsequent):
    """Keep the first cancellation, even when an ordinary failure preceded it."""
    if first is None or (isinstance(first, Exception) and subsequent is not None
                         and not isinstance(subsequent, Exception)):
        return subsequent
    return first


def write_json(path, value):
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(value, f, sort_keys=True, indent=2, allow_nan=False)
        f.write('\n'); f.flush(); os.fsync(f.fileno())
    os.replace(temporary, path)


def reader_status(directory):
    path = directory / 'reader-status.json'
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 16384:
        raise ValueError('Invalid saved Reader status')
    value = json.loads(path.read_text())
    fields = {'schema_version', 'state', 'pid', 'pgid', 'process_start', 'evidence_token',
              'exit_code', 'child_exited', 'cleanup_verified', 'error_code'}
    if (type(value) is not dict or set(value) != fields
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['state'] not in ('pending', 'running', 'passed', 'failed', 'interrupted', 'unknown')
            or any(type(value[k]) is not bool for k in ('child_exited', 'cleanup_verified'))
            or any(value[k] is not None and (type(value[k]) is not int or value[k] <= 1) for k in ('pid', 'pgid'))
            or value['pid'] != value['pgid']
            or (value['process_start'] is not None and (type(value['process_start']) is not str
                or not re.fullmatch(r'[A-Za-z0-9 :]{1,64}', value['process_start'])))
            or (value['evidence_token'] is not None and (type(value['evidence_token']) is not str
                or not re.fullmatch(r'[0-9a-f]{32}', value['evidence_token'])))
            or (value['error_code'] is not None and (type(value['error_code']) is not str
                or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,95}', value['error_code'])))
            or (value['exit_code'] is not None and type(value['exit_code']) is not int)
            or value['child_exited'] != (value['exit_code'] is not None)
            or (value['child_exited'] and value['pid'] is None)
            or (value['cleanup_verified'] and (not value['child_exited']
                or value['exit_code'] not in (0, 1, 130, 143) or value['evidence_token'] is None
                or value['state'] not in ('passed', 'failed', 'interrupted')))
            or (value['state'] == 'passed' and (not value['cleanup_verified']
                or value['exit_code'] != 0 or value['error_code'] is not None))):
        raise ValueError('Invalid saved Reader status')
    return value


def reader_closed(directory):
    value = reader_status(directory)
    if value is None:
        return not (directory / 'reader-intent.json').exists()
    return value.get('child_exited') is True and value.get('cleanup_verified') is True


def process_identity(pid):
    if type(pid) is not int or pid <= 1:
        return None
    result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'ppid=,pgid=,lstart=,command='],
                            capture_output=True, text=True, timeout=2, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
    if result.returncode != 0:
        return None
    fields = result.stdout.strip().split(None, 7)
    if len(fields) != 8:
        return None
    return dict(pid=pid, ppid=int(fields[0]), pgid=int(fields[1]),
                process_start=' '.join(fields[2:7]), command=fields[7])


def bind_reader(directory, kernel_pid, *, request=None):
    saved = reader_status(directory)
    if not saved or saved.get('state') != 'running' or not saved.get('process_start'):
        return None
    current = process_identity(saved.get('pid'))
    if (current is None or current['ppid'] != kernel_pid or current['pgid'] != current['pid']
            or current['pgid'] != saved.get('pgid')
            or current['process_start'] != ' '.join(saved['process_start'].split())
            or current['command'] != ' '.join(S.arguments(directory / 'shirokuma', ROOT,
                                                          REQUEST if request is None else request))):
        return None
    write_json(directory / 'reader-owner.json', current)
    return current


def bind_pipeline(directory, kernel_pid):
    """Permit effects only after observing the fixed worker as our kernel's child."""
    from tools.local_experiment.notebook import train as TN
    saved = TN.pipeline_status(directory)
    if not saved or saved['state'] != 'running' or not saved['process_start']:
        return None
    current = process_identity(saved['pid'])
    if (current is None or current['ppid'] != kernel_pid or current['pgid'] != current['pid']
            or current['pgid'] != saved['pgid']
            or current['process_start'] != ' '.join(saved['process_start'].split())
            or current['command'] != ' '.join(TN.arguments(directory))):
        return None
    return current


def owner_closed(directory, *, pipeline=False):
    if pipeline:
        from tools.local_experiment.notebook import train as TN
        return TN.pipeline_closed(directory)
    return reader_closed(directory)


async def interrupt_orphan(owner, *, timeout):
    """Only a Reader previously observed as our kernel's child may be signalled."""
    if owner is None:
        return False
    current = process_identity(owner['pid'])
    if current is None:
        return True
    # PPID changes when the kernel dies. The recorded start/group/command must
    # remain identical, and no process discovered only after death is adopted.
    if any(current[k] != owner[k] for k in ('pid', 'pgid', 'process_start', 'command')):
        return False
    os.kill(owner['pid'], signal.SIGINT)
    until = time.monotonic() + timeout
    while process_identity(owner['pid']) is not None:
        if time.monotonic() >= until:
            return False
        await asyncio.sleep(0.1)
    return True


async def shutdown_kernel(manager, kernel_client, *, timeout=KERNEL_GRACE_SECONDS):
    """Request a graceful shutdown, reap, then remove sockets; never force kill."""
    if manager.provisioner is None:
        return True
    if await manager.is_alive():
        await manager.request_shutdown(restart=False)
        until = time.monotonic() + timeout
        while await manager.is_alive():
            if time.monotonic() >= until:
                return False
            await asyncio.sleep(0.05)
    await manager.provisioner.wait()
    if kernel_client is not None:
        kernel_client.stop_channels()
    await manager.cleanup_resources(restart=False)
    return True


async def run_cells(client):
    # Do not use execute/async_execute/setup_kernel: they own cancellation and
    # have an automatic kill fallback. All cell code comes from our fixed file.
    for index, cell in enumerate(client.nb.cells):
        await client.async_execute_cell(cell, index, execution_count=client.code_cells_executed + 1)
    client.set_widgets_metadata()


async def execute_owned(client, manager, directory, *, stop=None,
                        timeout=EXECUTION_SECONDS, reader_grace=READER_GRACE_SECONDS,
                        kernel_grace=KERNEL_GRACE_SECONDS, request=None, pipeline=False):
    """Keep the first cancellation and wait for the Reader before kernel shutdown."""
    if type(pipeline) is not bool or pipeline and request is not None:
        raise ValueError('Mixed Notebook owners')
    request = None if request is None else S.normalize_request(request)
    def outcome(**value):
        if pipeline:
            value['pipeline_cleanup_confirmed'] = value['reader_cleanup_confirmed']
        return value
    stop = stop if stop is not None else {'reason': None}
    if stop['reason'] is not None:
        cleaned, closed = False, False
        with suppress(BaseException):
            closed = owner_closed(directory, pipeline=pipeline)
            if closed:
                cleaned = await shutdown_kernel(manager, client.kc, timeout=kernel_grace)
        return outcome(result='interrupted' if cleaned else 'unknown', error=stop['reason'],
                    reader_cleanup_confirmed=closed, kernel_cleanup_confirmed=cleaned)
    task = asyncio.create_task(run_cells(client))
    deadline = time.monotonic() + timeout
    first_error = None
    reader_owner = None
    kernel_pid = manager.provisioner.pid if manager.provisioner is not None else None
    try:
        try:
            while not task.done():
                if pipeline and (stop['reason'] is not None or time.monotonic() >= deadline):
                    first_error = stop['reason'] or TimeoutError('Notebook deadline exceeded')
                    break
                if reader_owner is None:
                    reader_owner = (bind_pipeline(directory, kernel_pid) if pipeline else
                                    bind_reader(directory, kernel_pid) if request is None else
                                    bind_reader(directory, kernel_pid, request=request))
                    if pipeline and reader_owner is not None:
                        from tools.local_experiment.notebook import train as TN
                        # Remember ownership before the acknowledgement can be
                        # published and then fail during a flush or cancellation.
                        TN.allow_launch(directory, reader_owner)
                if stop['reason'] is not None:
                    first_error = stop['reason']
                    break
                if time.monotonic() >= deadline:
                    first_error = TimeoutError('Notebook deadline exceeded')
                    break
                await asyncio.wait({task}, timeout=0.05)
            if first_error is None:
                first_error = stop['reason']
            if first_error is None:
                task.result()
        except BaseException as error:
            # In particular an outer Task.cancel must use the same interrupt and
            # Reader grace as a signal/deadline; cancelling the cell waiter first
            # can strand the kernel's Reader cleanup.
            first_error = preserve_error(preserve_error(first_error, stop['reason']), error)
        if first_error is not None and not task.done():
            try:
                if await manager.is_alive():
                    # KeyboardInterrupt lets client.py signal its separate Go
                    # process and wait for the existing Reader cleanup.
                    await manager.interrupt_kernel()
            except BaseException as error:
                first_error = preserve_error(first_error, error)
            grace_until = time.monotonic() + reader_grace
            while not task.done() and time.monotonic() < grace_until:
                # Further task cancellations cannot shorten the owner's grace.
                try:
                    await asyncio.wait({task}, timeout=max(0, grace_until - time.monotonic()))
                except BaseException as error:
                    first_error = preserve_error(first_error, error)
        first_error = preserve_error(first_error, stop['reason'])
        closed, alive = False, True
        try:
            closed = owner_closed(directory, pipeline=pipeline)
            alive = await manager.is_alive()
        except BaseException as error:
            first_error = preserve_error(preserve_error(first_error, stop['reason']), error)
        if not closed and not alive:
            with suppress(BaseException):
                await interrupt_orphan(reader_owner, timeout=reader_grace)
            # An orphan's exit alone is not proof of its query cleanup. Keep the
            # saved outcome unknown for receipt inspection, while reaping our kernel.
            with suppress(BaseException):
                await shutdown_kernel(manager, client.kc, timeout=kernel_grace)
        if not task.done() or not closed:
            # Do not call nbclient cleanup or terminate a kernel over its owner.
            return outcome(result='unknown', reader_cleanup_confirmed=closed,
                        kernel_cleanup_confirmed=False, error=first_error)
        with suppress(BaseException):
            task.result()  # Retrieve the post-interrupt exception, preserving the first.
        try:
            cleaned = await shutdown_kernel(manager, client.kc, timeout=kernel_grace)
        except BaseException as error:
            first_error = preserve_error(first_error, error)
            cleaned = False
        first_error = preserve_error(first_error, stop['reason'])
        return outcome(result=('passed' if first_error is None else 'interrupted'
                            if isinstance(first_error, NotebookInterrupted) else 'failed') if cleaned else 'unknown',
                    reader_cleanup_confirmed=True, kernel_cleanup_confirmed=cleaned, error=first_error)
    finally:
        if not task.done():
            # Abandon only the host's message wait after recording uncertainty.
            # Cancelling this task does not signal or kill the kernel or Reader.
            task.cancel()
            with suppress(BaseException):
                await task


def training_inputs(binary, request, training_token, kind, *, train_lake=False):
    if type(train_lake) is not bool or train_lake and (
            binary is not None or request is not None or training_token is not None or kind != 'synthetic'):
        raise ValueError('Fixed training must not have other Notebook inputs')
    if type(kind) is not str or kind not in ('synthetic', 'iceberg'):
        raise ValueError('Unknown training review kind')
    if training_token is None:
        if kind != 'synthetic':
            raise ValueError('Training review kind requires a token')
        return None
    from tools.local_experiment.notebook import training as T
    if request is not None or binary is not None:
        raise ValueError('Training review must not have a Reader request or binary')
    return T.normalize_token(training_token)


def kernel_environment(directory, binary, *, request=None, training_token=None, kind='synthetic', train_lake=False):
    training_token = training_inputs(binary, request, training_token, kind, train_lake=train_lake)
    environment = {k: os.environ[k] for k in ('PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TMPDIR')
                   if k in os.environ}
    for name, relative in [('IPYTHONDIR', 'ipython'), ('JUPYTER_CONFIG_DIR', 'jupyter-config'),
                           ('JUPYTER_RUNTIME_DIR', 'jupyter-runtime')]:
        path = directory / relative
        path.mkdir(mode=0o700)
        environment[name] = str(path)
    environment.update(PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1',
        SHIROKUMA_NOTEBOOK_RUN_DIR=str(directory))
    if binary is not None:
        environment['SHIROKUMA_NOTEBOOK_BINARY'] = str(binary)
    if training_token is not None:
        from tools.local_experiment.notebook import training as T
        environment[T.TOKEN_ENV] = training_token
        if kind == 'iceberg':
            environment[T.KIND_ENV] = kind
    if request is not None:
        request = S.normalize_request(request)
        expected = S.digest(request)
        S.load_request(directory / 'request.json', expected)
        environment.update(SHIROKUMA_NOTEBOOK_REQUEST_PATH=str(directory / 'request.json'),
                           SHIROKUMA_NOTEBOOK_REQUEST_SHA256=expected)
    return environment


async def execute(directory, binary, notebook, *, request=None, training_token=None, kind='synthetic', train_lake=False):
    training_token = training_inputs(binary, request, training_token, kind, train_lake=train_lake)
    request = None if request is None else S.normalize_request(request)
    if request is not None:
        S.load_request(directory / 'request.json', S.digest(request))
    specs = directory / 'kernels'
    spec = specs / 'shirokuma-local'
    spec.mkdir(parents=True, mode=0o700)
    write_json(spec / 'kernel.json', {'argv': [str(HERE / '.venv/bin/python'), '-m', 'ipykernel_launcher',
               '-f', '{connection_file}'], 'display_name': 'Shirokuma local experiment', 'language': 'python'})
    manager = AsyncKernelManager(kernel_name='shirokuma-local',
        kernel_spec_manager=KernelSpecManager(kernel_dirs=[str(specs)], ensure_native_kernel=False),
        # jupyter_client sets the connection directory's sticky bit. Keep that
        # library-owned permission change off the Reader's exact-0700 run root.
        connection_file=str(directory / 'jupyter-runtime/kernel-connection.json'))
    client = NotebookClient(notebook, km=manager, timeout=None, allow_errors=False, force_raise_errors=True,
                            record_timing=True, iopub_timeout=30, raise_on_iopub_timeout=True)
    stop = {'reason': None}
    loop = asyncio.get_running_loop()
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    for sig in previous:
        def requested(signum=sig):
            if stop['reason'] is None:
                stop['reason'] = NotebookInterrupted(signum)
        loop.add_signal_handler(sig, requested)
    started = False
    try:
        with (directory / 'kernel.log').open('xb') as kernel_log:
            if train_lake:
                environment = kernel_environment(directory, binary, train_lake=True)
            elif training_token is not None:
                if kind == 'iceberg':
                    environment = kernel_environment(directory, binary, training_token=training_token, kind=kind)
                else:
                    environment = kernel_environment(directory, binary, training_token=training_token)
            elif request is None:
                environment = kernel_environment(directory, binary)
            else:
                environment = kernel_environment(directory, binary, request=request)
            await manager.start_kernel(cwd=str(ROOT), env=environment,
                                       stdout=kernel_log, stderr=kernel_log)
            started = True
            kernel_pid = manager.provisioner.pid
            write_json(directory / 'kernel-identity.json', dict(pid=kernel_pid,
                pgid=manager.provisioner.pgid, connection_file='jupyter-runtime/kernel-connection.json'))
            client.kc = manager.client()
            client.kc.start_channels()
            await client.kc.wait_for_ready(timeout=30)
            if train_lake:
                from tools.local_experiment.notebook import train as TN
                result = await execute_owned(client, manager, directory, stop=stop,
                    timeout=TN.TRAIN_SECONDS, reader_grace=TN.CLEANUP_SECONDS, pipeline=True)
            elif training_token is not None:
                result = await execute_owned(client, manager, directory, stop=stop, timeout=TRAINING_REVIEW_SECONDS)
            elif request is None:
                result = await execute_owned(client, manager, directory, stop=stop)
            else:
                result = await execute_owned(client, manager, directory, stop=stop, request=request)
            result['kernel_pid'] = kernel_pid
            return result
    except BaseException as error:
        cleaned, closed = False, False
        with suppress(BaseException):
            closed = owner_closed(directory, pipeline=train_lake)
        if not started and manager.provisioner is None:
            cleaned = True
        elif closed:
            with suppress(BaseException):
                cleaned = await shutdown_kernel(manager, client.kc)
        outcome = dict(result='failed' if cleaned else 'unknown', error=stop['reason'] or error,
                    reader_cleanup_confirmed=closed, kernel_cleanup_confirmed=cleaned,
                    kernel_pid=manager.provisioner.pid if manager.provisioner is not None else None)
        if train_lake:
            outcome['pipeline_cleanup_confirmed'] = closed
        return outcome
    finally:
        for sig, handler in previous.items():
            loop.remove_signal_handler(sig)
            signal.signal(sig, handler)


def training_sources(kind='synthetic'):
    if type(kind) is not str or kind not in ('synthetic', 'iceberg'):
        raise ValueError('Unknown training review kind')
    template = 'examples/local_lake_training_review.ipynb' if kind == 'iceberg' else 'examples/local_training_review.ipynb'
    names = ('tools/local_experiment/notebook/runner.py', 'tools/local_experiment/notebook/client.py',
             'tools/local_experiment/notebook/training.py', 'tools/local_experiment/notebook/pyproject.toml',
             'tools/local_experiment/notebook/uv.lock', template)
    if kind == 'iceberg':
        names += ('tools/local_experiment/query_read/contract.py',)
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}


def train_sources():
    from tools.local_experiment.lake_training import run as P
    sources = P.sources()
    names = ('tools/local_experiment/notebook/runner.py', 'tools/local_experiment/notebook/train.py',
             'tools/local_experiment/notebook/training.py', 'tools/local_experiment/notebook/pyproject.toml',
             'tools/local_experiment/notebook/uv.lock', 'examples/local_lake_training.ipynb')
    sources.update({name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names})
    return sources


def train_result(directory, result):
    """Bind the kernel publication to the pipeline and the saved tracked model."""
    from tools.local_experiment.notebook import client as N, train as TN, training as T
    result['pipeline_cleanup_confirmed'] = False
    status = TN.pipeline_status(directory)
    closed = TN.pipeline_closed(directory)
    if not closed:
        result['reader_cleanup_confirmed'] = False
    if status is None or status['evidence_token'] is None:
        result['pipeline_cleanup_confirmed'] = closed
        if result['result'] == 'passed':
            raise ValueError('Missing training pipeline receipt')
        return
    token, summary, terminal_closed, terminal_result = TN.terminal_receipt(directory, status['exit_code'])
    if token != status['evidence_token'] or closed != terminal_closed:
        raise ValueError('Training pipeline receipt changed')
    result['pipeline_cleanup_confirmed'] = closed
    result['source_token'] = token
    if summary is None:
        if result['result'] == 'passed':
            raise ValueError('Missing completed training summary')
        return
    result['reader_evidence_token'] = summary['query_evidence_token']
    result['reader_cleanup_confirmed'] = summary['reader_cleanup_confirmed']
    tracking = summary.get('tracking')
    if type(tracking) is dict:
        result['training_token'] = tracking.get('attempt_token')
    if result['result'] == 'passed':
        if status['state'] != 'passed' or not closed or terminal_result != 'passed':
            raise ValueError('Training pipeline did not pass')
        public = N._parse(N._read(directory / 'training-review.json', N.MAX_HTTP))
        T.validate_public(public)
        if (public['profile'] != T.LAKE_PROFILE or type(tracking) is not dict
                or set(tracking) != {'attempt_token','run_id','experiment_id','workload_sha256'}
                or any(public['identity'][key] != tracking[key] for key in tracking)
                or public['identity']['store_id'] != summary.get('store_id')
                or public['lake_source']['source_token'] != token
                or public['lake_source']['query_evidence_token'] != summary['query_evidence_token']):
            raise ValueError('Training publication differs from pipeline')
        T.verify_saved_review(public, tracking['attempt_token'], kind='iceberg')
        result['training_identity'] = public['identity']


def main(argv=None, *, stdin=None):
    arguments = sys.argv[1:] if argv is None else argv
    training_token = None
    train_lake = arguments == ['--train-lake']
    training_mode = len(arguments) == 2 and arguments[0] in ('--training-token', '--lake-training-token')
    kind = 'iceberg' if training_mode and arguments[0] == '--lake-training-token' else 'synthetic'
    if arguments not in ([], ['--request-stdin'], ['--train-lake']) and not training_mode:
        print(json.dumps({'result': 'failed', 'error_code': 'ARGUMENTS_INVALID'}))
        return 1
    request = None
    if training_mode:
        try:
            from tools.local_experiment.notebook import training as T
            training_token = T.normalize_token(arguments[1])
        except Exception:
            print(json.dumps({'result': 'failed', 'error_code': 'NOTEBOOK_TRAINING_TOKEN_INVALID'}))
            return 1
    elif arguments == ['--request-stdin']:
        try:
            request = S.parse_request((stdin or sys.stdin.buffer).read(4097))
        except Exception:
            print(json.dumps({'result': 'failed', 'error_code': 'NOTEBOOK_REQUEST_INVALID'}))
            return 1
    os.umask(0o077)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = STATE / uuid.uuid4().hex
    directory.mkdir(mode=0o700)
    result = dict(schema_version=1, run_token=directory.name, result='failed', error_code=None,
                  reader_cleanup_confirmed=True, kernel_cleanup_confirmed=True, kernel_pid=None)
    if train_lake:
        from tools.local_experiment.notebook import train as TN
        result.update(profile=TN.PROFILE, pipeline_cleanup_confirmed=True, source_token=None,
                      training_token=None, reader_evidence_token=None, training_identity=None)
    if request is not None:
        result.update(profile=PREVIEW_PROFILE, request_sha256=S.digest(request))
    if training_token is not None:
        result.update(profile=T.LAKE_PROFILE if kind == 'iceberg' else 'training-review-v1', training_token=training_token)
        if kind == 'iceberg':
            result['kind'] = kind
    notebook = None
    first_error = None
    try:
        if train_lake:
            template = TRAIN_TEMPLATE
        elif training_token is not None:
            template = T.template_path(kind) if kind == 'iceberg' else T.TEMPLATE_PATH
        else:
            template = TEMPLATE if request is None else PREVIEW_TEMPLATE
        raw = template.read_bytes()
        sealed = None
        if train_lake:
            if hashlib.sha256(raw).hexdigest() != TRAIN_TEMPLATE_SHA256:
                raise ValueError('Training template changed')
            sealed = train_sources()
        if training_token is not None:
            if kind == 'iceberg':
                T.validate_template(raw, kind=kind)
                sealed = training_sources(kind)
            else:
                T.validate_template(raw)
                sealed = training_sources()
        if request is not None:
            S.validate_template(raw)
            fd = os.open(directory / 'request.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(S.canonical_bytes(request)); stream.flush(); os.fsync(stream.fileno())
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            S.load_request(directory / 'request.json', S.digest(request))
        notebook = nbformat.reads(raw.decode(), as_version=4)
        if any(c.get('outputs') or c.get('execution_count') is not None for c in notebook.cells if c.cell_type == 'code'):
            raise ValueError('Notebook template must not contain outputs')
        binary = None if training_token is not None or train_lake else directory / 'shirokuma'
        if binary is not None:
            with (directory / 'build.log').open('xb') as build:
                subprocess.run(['go', 'build', '-o', str(binary), './cmd/shirokuma'], cwd=ROOT,
                               stdout=build, stderr=build, check=True, timeout=60)
        inputs = dict(template_sha256=hashlib.sha256(raw).hexdigest(),
                   binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest() if binary is not None else None,
                   python=sys.version.split()[0], scope='fixed Notebook; Reader one SELECT and read-only MLflow evaluations')
        if train_lake:
            inputs.update(profile=TN.PROFILE, source_sha256=sealed,
                scope='fixed Iceberg training pipeline; owned Reader, backup and tracked fit; offline model review')
        if training_token is not None:
            inputs.update(profile=result['profile'], training_token=training_token, source_sha256=sealed,
                          scope='fixed training result review; two MLflow GETs; no Reader or training')
            if kind == 'iceberg':
                inputs['kind'] = kind
        if request is not None:
            inputs.update(profile=PREVIEW_PROFILE, request_file='request.json', request_sha256=S.digest(request))
            S.load_request(directory / 'request.json', S.digest(request))
        write_json(directory / 'inputs.json', inputs)
        if train_lake:
            observed = asyncio.run(execute(directory, binary, notebook, train_lake=True))
        elif training_token is not None:
            if kind == 'iceberg':
                observed = asyncio.run(execute(directory, binary, notebook, training_token=training_token, kind=kind))
            else:
                observed = asyncio.run(execute(directory, binary, notebook, training_token=training_token))
        elif request is None:
            observed = asyncio.run(execute(directory, binary, notebook))
        else:
            observed = asyncio.run(execute(directory, binary, notebook, request=request))
        error = observed.pop('error')
        first_error = error
        result.update(observed)
        result['error_code'] = ('INTERRUPTED' if isinstance(error, NotebookInterrupted) else
                                'NOTEBOOK_TIMEOUT' if isinstance(error, TimeoutError) else
                                'NOTEBOOK_EXECUTION_FAILED' if error is not None else None)
        if isinstance(error, NotebookInterrupted):
            result['signal'] = error.signum
        if request is not None:
            S.load_request(directory / 'request.json', S.digest(request))
        if training_token is not None:
            current_sources = training_sources(kind) if kind == 'iceberg' else training_sources()
            if current_sources != sealed:
                raise ValueError('Training review source changed')
            if any((directory / name).exists() or (directory / name).is_symlink()
                   for name in ('reader-intent.json', 'reader-status.json', 'build.log', 'shirokuma')):
                raise ValueError('Unexpected Reader activity during training review')
            if result['result'] == 'passed':
                from tools.local_experiment.notebook import client as N
                public = N._parse(N._read(directory / 'training-review.json', N.MAX_HTTP))
                T.validate_public(public)
                if public['identity']['attempt_token'] != training_token:
                    raise ValueError('Training review attempt changed')
                if kind == 'iceberg':
                    intent = N._parse(N._read(directory / 'training-intent.json', 16384))
                    if (type(intent) is not dict or type(intent.get('schema_version')) is not int
                            or intent != {'schema_version': 1, 'profile': T.LAKE_PROFILE,
                                          'attempt_token': training_token, 'kind': kind}):
                        raise ValueError('Training review intent changed')
                    if public['profile'] != T.LAKE_PROFILE:
                        raise ValueError('Training review profile changed')
                    T.verify_saved_review(public, training_token, kind=kind)
                else:
                    T.verify_saved_review(public, training_token)
        if train_lake:
            train_result(directory, result)
            if train_sources() != sealed:
                raise ValueError('Training pipeline source changed')
        else:
            status = reader_status(directory)
            result['reader_evidence_token'] = status.get('evidence_token') if status else None
        result['code_cells'] = sum(c.cell_type == 'code' for c in notebook.cells)
    except BaseException as error:
        first_error = preserve_error(first_error, error)
        result['error_code'] = result['error_code'] or ('INTERRUPTED' if not isinstance(first_error, Exception)
                                                       else 'NOTEBOOK_SETUP_FAILED')
        result['result'] = ('unknown' if not result['reader_cleanup_confirmed'] or not result['kernel_cleanup_confirmed']
                            or not result.get('pipeline_cleanup_confirmed', True)
                            else 'interrupted' if not isinstance(first_error, Exception) else 'failed')
        if isinstance(first_error, NotebookInterrupted):
            result['signal'] = first_error.signum
    finally:
        try:
            if notebook is not None:
                with (directory / 'executed.ipynb').open('x') as f:
                    nbformat.write(notebook, f)
                    f.flush(); os.fsync(f.fileno())
        except BaseException:
            result['error_code'] = result['error_code'] or 'NOTEBOOK_EVIDENCE_SAVE_FAILED'
            result['result'] = 'unknown'
        try:
            write_json(directory / 'result.json', result)
        except BaseException:
            result['error_code'] = result['error_code'] or 'NOTEBOOK_EVIDENCE_SAVE_FAILED'
            result['result'] = 'unknown'
    print(json.dumps(dict(result, evidence=str(directory.relative_to(ROOT)))))
    return 0 if result['result'] == 'passed' else 128 + result['signal'] if 'signal' in result else 1


if __name__ == '__main__':
    raise SystemExit(main())
