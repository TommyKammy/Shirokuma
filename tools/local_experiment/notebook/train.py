"""A registered Notebook owns one gated, fixed Lake training pipeline.

The outer runner must observe the worker as its kernel's child before allowing
the existing pipeline to start. Unknown outcomes are retained, never retried.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.local_experiment.notebook import client as N, training as T

PROFILE = 'notebook-lake-training-v1'
NOTEBOOK_STATE = ROOT / 'tmp/local-experiment/notebook-runs'
LAKE_STATE = ROOT / 'tmp/local-experiment/lake-training'
PYTHON = ROOT / 'tools/local_experiment/mlflow/.venv/bin/python'
LAUNCH_SECONDS, TRAIN_SECONDS, CLEANUP_SECONDS = 30, 600, 600
STATUS_KEYS = {'schema_version','state','pid','pgid','process_start','evidence_token',
               'exit_code','child_exited','cleanup_verified','error_code'}
ERROR_CODES = {'NOTEBOOK_TRAIN_FAILED','NOTEBOOK_TRAIN_INVALID','NOTEBOOK_TRAIN_INTERRUPTED',
               'NOTEBOOK_TRAIN_CLEANUP_UNKNOWN','NOTEBOOK_TRAIN_NOT_AUTHORIZED'}


def require(condition):
    N._require(condition, 'NOTEBOOK_TRAIN_INVALID')


def _keep(first, later):
    return later if first is None or isinstance(first, Exception) and not isinstance(later, Exception) else first


def _directory(value):
    path = Path(value)
    require(path.is_absolute() and path.parent == NOTEBOOK_STATE and N.TOKEN.fullmatch(path.name)
            and all(not item.is_symlink() for item in (path, *path.parents)))
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700 and info.st_uid == os.getuid())
    return path


def arguments(directory):
    return [str(PYTHON), '-I', '-B', str(Path(__file__).resolve()), '--worker', str(directory)]


def _json(path, maximum=16384):
    return N._parse(N._read(path, maximum))


def pipeline_status(directory):
    directory = _directory(directory)
    path = directory / 'pipeline-status.json'
    if not path.exists() and not path.is_symlink():
        return None
    value = _json(path)
    require(type(value) is dict and set(value) == STATUS_KEYS
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and value['state'] in ('pending','running','passed','failed','interrupted','unknown')
            and all(type(value[k]) is bool for k in ('child_exited','cleanup_verified'))
            and all(value[k] is None or type(value[k]) is int and value[k] > 1 for k in ('pid','pgid'))
            and value['pid'] == value['pgid']
            and (value['process_start'] is None or type(value['process_start']) is str
                 and re.fullmatch(r'[A-Za-z0-9 :]{1,64}', value['process_start']))
            and (value['evidence_token'] is None or type(value['evidence_token']) is str
                 and N.TOKEN.fullmatch(value['evidence_token']))
            and (value['exit_code'] is None or type(value['exit_code']) is int)
            and value['child_exited'] == (value['exit_code'] is not None)
            and (value['error_code'] is None or value['error_code'] in ERROR_CODES))
    require(not value['cleanup_verified'] or value['child_exited']
            and (value['exit_code'] in (0,1,130,143)
                 or value['evidence_token'] is None and value['exit_code'] in (-signal.SIGINT,-signal.SIGTERM))
            and value['state'] in ('passed','failed','interrupted'))
    require(value['state'] != 'passed' or value['cleanup_verified'] and value['exit_code'] == 0
            and value['evidence_token'] is not None and value['error_code'] is None)
    return value


def allow_launch(directory, owner):
    """Called only after the runner has verified kernel parent/start/argv."""
    directory = _directory(directory)
    saved = pipeline_status(directory)
    require(type(owner) is dict and set(owner) == {'pid','ppid','pgid','process_start','command'}
            and type(owner['ppid']) is int and owner['ppid'] > 1
            and saved is not None and saved['state'] == 'running'
            and owner['pid'] == owner['pgid'] == saved['pid']
            and owner['process_start'] == ' '.join(saved['process_start'].split())
            and owner['command'] == ' '.join(arguments(directory)))
    N._write(directory/'pipeline-owner.json', owner)
    N._write(directory/'pipeline-launch.json', dict(schema_version=1,
        **{key:owner[key] for key in ('pid','pgid','process_start')}))


def _terminal(directory, exit_code):
    """Read only the fixed pipeline's final frame and terminal receipts."""
    raw = N._read(directory/'pipeline.stdout.json', 16384)
    require(raw.endswith(b'\n') and len(raw.splitlines()) == 1)
    frame = N._parse(raw)
    require(type(frame) is dict and set(frame) == {'result','cleanup_confirmed','evidence'}
            and frame['result'] in ('passed','failed','interrupted','unknown')
            and type(frame['cleanup_confirmed']) is bool and type(frame['evidence']) is str)
    match = re.fullmatch(r'tmp/local-experiment/lake-training/([0-9a-f]{32})/owner-result.json', frame['evidence'])
    require(match is not None)
    token = match.group(1)
    output = LAKE_STATE/token
    require(all(not item.is_symlink() for item in (output, *output.parents)))
    owner = _json(output/'owner-result.json')
    require(type(owner) is dict and set(owner) == {'schema_version','profile','result','summary_result',
                                                   'cleanup_confirmed','error_code'}
            and type(owner['schema_version']) is int and owner['schema_version'] == 1
            and owner['profile'] == 'fixed-iceberg-training-pipeline-v1'
            and owner['result'] == frame['result'] and owner['cleanup_confirmed'] is frame['cleanup_confirmed']
            and owner['error_code'] == (None if frame['result'] == 'passed' else 'LAKE_PIPELINE_FAILED'))
    if owner['summary_result'] is None:
        require(frame['result'] in ('failed','interrupted') and frame['cleanup_confirmed'] is True
                and exit_code in (1,130,143)
                and all(not (output/name).exists() and not (output/name).is_symlink() for name in
                    ('summary.json','pipeline-intent.json','reader','fixture','lake-input.json','source-ready.json',
                     'tracker-owner-intent.json','tracker-status.json','cold-backup')))
        return token, None, True, frame['result']
    summary = _json(output/'summary.json', 256*1024)
    require(type(summary) is dict and type(summary.get('schema_version')) is int and summary['schema_version'] == 1
            and summary.get('profile') == owner['profile'] and summary.get('source_token') == token
            and summary.get('phase') == 'complete' and summary.get('result') == owner['summary_result']
            and summary['result'] in ('passed','failed','interrupted','unknown'))
    keys = ('reader_cleanup_confirmed','fixture_cleanup_confirmed','tracker_cleanup_confirmed','runtime_restored')
    require(all(type(summary.get(key)) is bool for key in keys)
            and (not frame['cleanup_confirmed'] or all(summary[key] for key in keys)))
    require(type(exit_code) is int and ((frame['result'] == 'passed' and exit_code == 0
             and frame['cleanup_confirmed'] and summary['result'] == 'passed')
            or frame['result'] != 'passed' and exit_code in (1,130,143)))
    return token, summary, frame['cleanup_confirmed'], frame['result']


def terminal_receipt(directory, exit_code):
    return _terminal(_directory(directory), exit_code)


def group_empty(pid):
    from tools.local_experiment.mlflow import runtime as R
    return not any(row['pgid'] == pid for row in R.processes().values())


def pipeline_closed(directory):
    directory = _directory(directory)
    value = pipeline_status(directory)
    if value is None:
        return not (directory/'pipeline-intent.json').exists()
    if not value['child_exited'] or not value['cleanup_verified']:
        return False
    if value['pid'] is not None and not group_empty(value['pid']):
        return False
    if value['evidence_token'] is None:
        return not (directory/'pipeline-launch.json').exists()
    token, _, closed, _ = _terminal(directory, value['exit_code'])
    return closed and token == value['evidence_token']


def _interrupt(child, first):
    try:
        if child.poll() is None:
            # Signal the wrapper only. Its pipeline owns separate Reader/SDK
            # sessions and performs their existing cleanup exactly once.
            os.kill(child.pid, signal.SIGINT)
    except BaseException as error:
        first = _keep(first, error)
    until = time.monotonic()+CLEANUP_SECONDS
    while time.monotonic() < until:
        try:
            if child.poll() is not None: break
            child.wait(timeout=min(1,max(.001,until-time.monotonic())))
        except subprocess.TimeoutExpired: pass
        except BaseException as error: first = _keep(first,error)
    return first


def train_lake():
    """Launch once, wait for terminal cleanup, then return the saved review."""
    directory = _directory(os.environ.get('SHIROKUMA_NOTEBOOK_RUN_DIR',''))
    N._write(directory/'pipeline-intent.json', dict(schema_version=1,profile=PROFILE,argv=arguments(directory)))
    status = dict(schema_version=1,state='pending',pid=None,pgid=None,process_start=None,evidence_token=None,
                  exit_code=None,child_exited=False,cleanup_verified=False,error_code=None)
    child, first, public, interrupted = None, None, None, False
    try:
        N._write(directory/'pipeline-status.json',status,replace=True)
        fd = os.open(directory/'pipeline.stdout.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as stdout:
            fd = os.open(directory/'pipeline.stderr.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as stderr:
                child = subprocess.Popen(arguments(directory),cwd=ROOT,stdin=subprocess.DEVNULL,stdout=stdout,
                    stderr=stderr,start_new_session=True,umask=0o077,
                    env={key:value for key,value in os.environ.items()
                         if key in ('PATH','HOME','USER','LOGNAME','TMPDIR','LANG','LC_ALL')})
                status.update(state='running',pid=child.pid,pgid=child.pid)
                N._write(directory/'pipeline-status.json',status,replace=True)
                status['process_start'] = N._process_start(child.pid)
                N._write(directory/'pipeline-status.json',status,replace=True)
                try: child.wait(timeout=TRAIN_SECONDS)
                except BaseException as error:
                    first, interrupted = error, True
                    first = _interrupt(child, first)
                stdout.flush(); os.fsync(stdout.fileno())
        status.update(exit_code=child.poll(),child_exited=child.poll() is not None)
        if status['child_exited']:
            require(group_empty(child.pid))
            if not (directory/'pipeline-launch.json').exists():
                status['cleanup_verified'] = True
                raise N.NotebookError('NOTEBOOK_TRAIN_NOT_AUTHORIZED')
            token, summary, closed, result = _terminal(directory,child.returncode)
            status.update(evidence_token=token,cleanup_verified=closed)
            require(result == 'passed')
            tracking = summary.get('tracking')
            require(type(tracking) is dict and set(tracking) == {'attempt_token','run_id','experiment_id','workload_sha256'})
            public = T.load_saved_review(tracking['attempt_token'],kind='iceberg')
            T.validate_public(public)
            require(all(public['identity'][key] == tracking[key] for key in tracking)
                    and public['identity']['store_id'] == summary.get('store_id')
                    and public['lake_source']['source_token'] == token
                    and public['lake_source']['query_evidence_token'] == summary.get('query_evidence_token'))
            require(first is None)
            N._write(directory/'training-review.json',public)
        else:
            raise N.NotebookError('NOTEBOOK_TRAIN_CLEANUP_UNKNOWN')
    except BaseException as error:
        first = _keep(first,error)
        try:
            if child is not None and child.poll() is None and not interrupted:
                interrupted = True
                first = _interrupt(child,first)
            if child is not None:
                status.update(exit_code=child.poll(),child_exited=child.poll() is not None)
        except BaseException as error: first = _keep(first,error)
    finally:
        if child is None:
            status.update(child_exited=True,exit_code=1,cleanup_verified=True)
        elif status['child_exited'] and not status['cleanup_verified']:
            try:
                if not (directory/'pipeline-launch.json').exists():
                    status['cleanup_verified'] = True
                else:
                    token, _, closed, _ = _terminal(directory,status['exit_code'])
                    status.update(evidence_token=token,cleanup_verified=closed)
            except BaseException as error: first = _keep(first,error)
        if child is not None and status['child_exited']:
            try:
                if not group_empty(child.pid):
                    status['cleanup_verified'] = False
                    first = _keep(first,N.NotebookError('NOTEBOOK_TRAIN_CLEANUP_UNKNOWN'))
            except BaseException as error:
                status['cleanup_verified'] = False
                first = _keep(first,error)
        status.update(state='passed' if first is None else 'unknown' if not status['cleanup_verified'] else
                      'interrupted' if not isinstance(first,Exception) else 'failed',
                      error_code=None if first is None else 'NOTEBOOK_TRAIN_INTERRUPTED' if not isinstance(first,Exception)
                      else 'NOTEBOOK_TRAIN_CLEANUP_UNKNOWN' if not status['cleanup_verified'] else 'NOTEBOOK_TRAIN_FAILED')
        try: N._write(directory/'pipeline-status.json',status,replace=True)
        except BaseException as error: first = _keep(first,error)
    if first is not None:
        if not isinstance(first,Exception): raise first
        raise N.NotebookError(status['error_code'] or 'NOTEBOOK_TRAIN_FAILED') from None
    return public


def worker(directory, *, pipeline=None):
    """A no-effect gate precedes the unchanged pipeline's own lifecycle."""
    directory = _directory(directory)
    intent = _json(directory/'pipeline-intent.json')
    require(intent == dict(schema_version=1,profile=PROFILE,argv=arguments(directory)))
    until = time.monotonic()+LAUNCH_SECONDS
    while not (directory/'pipeline-launch.json').exists():
        if time.monotonic() >= until:
            raise N.NotebookError('NOTEBOOK_TRAIN_NOT_AUTHORIZED')
        time.sleep(.05)
    status = pipeline_status(directory)
    owner, launch = _json(directory/'pipeline-owner.json'), _json(directory/'pipeline-launch.json')
    start = ' '.join(N._process_start(os.getpid()).split())
    require(status is not None and status['state'] == 'running'
            and status['pid'] == status['pgid'] == os.getpid() and os.getpgrp() == os.getpid()
            and ' '.join(status['process_start'].split()) == start
            and owner == dict(pid=os.getpid(),ppid=os.getppid(),pgid=os.getpid(),process_start=start,
                              command=' '.join(arguments(directory)))
            and launch == dict(schema_version=1,pid=os.getpid(),pgid=os.getpid(),process_start=start))
    if pipeline is None:
        from tools.local_experiment.lake_training import run as P
        pipeline = P.main
    return pipeline([])


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        require(len(argv) == 2 and argv[0] == '--worker')
        return worker(argv[1])
    except KeyboardInterrupt: return 130
    except Exception: return 1


if __name__ == '__main__':
    raise SystemExit(main())
