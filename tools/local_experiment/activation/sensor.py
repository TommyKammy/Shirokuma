"""One bounded Grafana sensor tick and deterministic SRE job; no daemon.

The caller supplies one normalized observation of the owned rule. A durable
claim precedes the job. Repeated alert instances never rerun, and an incomplete
claim blocks all later dispatches in this experiment until separately resolved.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import time
import uuid

from tools.local_experiment.notebook import client as N

ROOT = Path(__file__).resolve().parents[3]
PYTHON = ROOT / 'tools/local_experiment/gateway/.venv/bin/python'
SRE = ROOT / 'tools/local_experiment/bears/run_sre.py'
SRE_STATE = ROOT / 'tmp/local-experiment/bears'
RULE_UID = 'shirokuma-local-activation'
PROFILE = 'grafana-dagster-sre-v1'
MAX_CLAIMS = 32
RUN_SECONDS = 600
CLEANUP_SECONDS = 120


class ActivationError(ValueError):
    pass


def require(value, code='ACTIVATION_EVIDENCE_INVALID'):
    if not value:
        raise ActivationError(code)


def _keep(first, later):
    return later if first is None or isinstance(first, Exception) and not isinstance(later, Exception) else first


def canonical(value):
    return N._canonical(value)


def normalize_observation(value):
    require(type(value) is dict and set(value) == {'schema_version', 'rule_uid', 'task_id', 'state', 'fingerprint'},
            'ACTIVATION_OBSERVATION_INVALID')
    require(type(value['schema_version']) is int and value['schema_version'] == 1
            and value['rule_uid'] == RULE_UID
            and type(value['task_id']) is str and re.fullmatch(r'activation-[0-9a-f]{32}', value['task_id'])
            and value['state'] in ('normal', 'firing'), 'ACTIVATION_OBSERVATION_INVALID')
    fingerprint = value['fingerprint']
    require(fingerprint is None if value['state'] == 'normal' else
            type(fingerprint) is str and re.fullmatch(r'[0-9a-f]{64}', fingerprint),
            'ACTIVATION_OBSERVATION_INVALID')
    return dict(value)


def run_key(value):
    value = normalize_observation(value)
    return None if value['state'] == 'normal' else value['rule_uid'] + ':' + value['fingerprint']


def _private(directory, *, create=False):
    directory = Path(directory)
    require(directory.is_absolute() and all(not p.is_symlink() for p in (directory, *directory.parents)))
    if create and not directory.exists():
        directory.mkdir(mode=0o700)
    info = directory.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)


@contextmanager
def _locked(directory):
    _private(directory, create=True)
    fd = os.open(directory / 'sensor.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    first = None
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ActivationError('ACTIVATION_BUSY') from None
        yield
    except BaseException as error:
        first = error
    finally:
        try:
            os.close(fd)
        except BaseException as error:
            first = _keep(first, error)
    if first is not None:
        raise first


def _read(path, maximum=256 * 1024):
    return N._parse(N._read(path, maximum))


def _save(path, value):
    N._write(path, value, replace=path.exists())


def _cursor(value, observation):
    require(type(value) is dict and set(value) == {'schema_version', 'rule_uid', 'task_id', 'claimed_keys'})
    require(type(value['schema_version']) is int and value['schema_version'] == 1
            and value['rule_uid'] == RULE_UID and value['task_id'] == observation['task_id'])
    keys = value['claimed_keys']
    require(type(keys) is list and len(keys) <= MAX_CLAIMS and all(type(k) is str and
            re.fullmatch(re.escape(RULE_UID) + r':[0-9a-f]{64}', k) for k in keys)
            and len(keys) == len(set(keys)))
    return value


def definitions(observation, diagnose):
    """Construct actual Dagster definitions; evaluation itself never calls SRE."""
    from dagster import RunRequest, SkipReason, job, op, sensor
    observation = normalize_observation(observation)

    @op
    def deterministic_sre(context):
        return diagnose(context.run_id)

    @job
    def activation_diagnosis():
        deterministic_sre()

    @sensor(job=activation_diagnosis, name='owned_grafana_alert')
    def owned_grafana_alert(context):
        cursor = _cursor(N._parse(context.cursor), observation)
        key = run_key(observation)
        if key is None:
            return SkipReason('The owned alert is normal.')
        if key in cursor['claimed_keys']:
            return SkipReason('This alert instance already has a durable claim.')
        require(len(cursor['claimed_keys']) < MAX_CLAIMS, 'ACTIVATION_CLAIM_LIMIT')
        context.update_cursor(canonical(dict(cursor, claimed_keys=cursor['claimed_keys'] + [key])).decode('ascii'))
        return RunRequest(run_key=key, tags={'shirokuma/alert_rule_uid': RULE_UID,
                                           'shirokuma/alert_task_id': observation['task_id']})

    return activation_diagnosis, owned_grafana_alert


def _group_empty(pid):
    from tools.local_experiment.mlflow import runtime
    return not any(row['pgid'] == pid for row in runtime.processes().values())


def _interrupt(child, first):
    try:
        if child.poll() is None:
            os.kill(child.pid, signal.SIGINT)
    except BaseException as error:
        first = _keep(first, error)
    until = time.monotonic() + CLEANUP_SECONDS
    while time.monotonic() < until:
        try:
            if child.poll() is not None:
                break
            child.wait(timeout=min(1, max(.001, until - time.monotonic())))
        except subprocess.TimeoutExpired:
            pass
        except BaseException as error:
            first = _keep(first, error)
    return first


def validate_diagnostic(value):
    require(type(value) is dict and value.get('result') == 'passed'
            and value.get('mode') == 'deterministic-read-only' and value.get('llm_inference') is False
            and value.get('audit') == 'delivered' and type(value.get('correlated_events')) is int
            and value['correlated_events'] == 4, 'ACTIVATION_DIAGNOSIS_FAILED')
    task = value.get('task_id')
    require(type(task) is str and re.fullmatch(r'workflow-[0-9a-f]{32}', task))
    require(value.get('evidence') == str(SRE_STATE / task.removeprefix('workflow-')))
    require(type(value.get('trace_id')) is str and re.fullmatch(r'[0-9a-f]{32}', value['trace_id'])
            and type(value.get('span_id')) is str and re.fullmatch(r'[0-9a-f]{16}', value['span_id']))
    calls = value.get('tool_calls')
    require(type(calls) is list and [x.get('tool') for x in calls if type(x) is dict] ==
            ['shirokuma_doctor', 'k8s_read_pods', 'k8s_read_events'])
    try:
        ids = [str(uuid.UUID(x['event_id'])) for x in calls] + [str(uuid.UUID(value['event_id']))]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ActivationError('ACTIVATION_EVIDENCE_INVALID') from None
    require(len(set(ids)) == 4)
    diagnosis = value.get('diagnosis')
    require(type(diagnosis) is dict and diagnosis.get('mode') == 'deterministic-read-only'
            and diagnosis.get('llm_inference') is False and diagnosis.get('actions_executed') == [])
    return value


def diagnose_once(directory):
    """Run the existing audited reader exactly once. Uncertain cleanup stays unknown."""
    inherited_home = os.environ.get('HOME')
    require(type(inherited_home) is str and bool(inherited_home), 'ACTIVATION_ENVIRONMENT_INVALID')
    child, streams, first, value = None, [], None, None
    state = {'schema_version': 1, 'pid': None, 'pgid': None, 'exit_code': None,
             'child_exited': False, 'cleanup_verified': False}
    try:
        for name in ('sre.stdout.json', 'sre.stderr.log'):
            fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            streams.append(os.fdopen(fd, 'wb'))
        argv = [str(PYTHON), '-B', str(SRE)]
        env = {'PATH': '/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin', 'LC_ALL': 'C',
               'HOME': inherited_home, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1',
               'PYTHONNOUSERSITE': '1', 'DO_NOT_TRACK': 'true'}
        N._write(directory / 'sre-intent.json', {'schema_version': 1, 'argv': argv})
        child = subprocess.Popen(argv, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                 stdout=streams[0], stderr=streams[1], start_new_session=True, umask=0o077)
        state.update(pid=child.pid, pgid=child.pid)
        N._write(directory / 'sre-process.json', state)
        child.wait(timeout=RUN_SECONDS)
    except BaseException as error:
        first = error
    finally:
        if child is not None:
            try:
                if child.poll() is None:
                    first = _interrupt(child, first)
                state.update(exit_code=child.poll(), child_exited=child.poll() is not None)
                state['cleanup_verified'] = state['child_exited'] and _group_empty(child.pid)
            except BaseException as error:
                first = _keep(first, error)
        for stream in reversed(streams):
            try:
                stream.close()
            except BaseException as error:
                first = _keep(first, error)
        try:
            _save(directory / 'sre-process.json', state)
        except BaseException as error:
            first = _keep(first, error)
    if first is not None:
        raise first
    require(state['exit_code'] == 0 and state['cleanup_verified'], 'ACTIVATION_DIAGNOSIS_UNKNOWN')
    value = validate_diagnostic(_read(directory / 'sre.stdout.json'))
    evidence = Path(value['evidence'])
    _private(evidence)
    require(canonical(_read(evidence / 'result.json')) == canonical(value))
    return value


def tick(observation, directory, *, diagnose=None):
    """Evaluate one real sensor tick and run at most one in-process Dagster job.

    ``diagnose`` is the offline-test seam; it receives the new claim directory.
    No automatic retry follows a failed or interrupted claim.
    """
    from dagster import DagsterInstance, build_sensor_context
    observation = normalize_observation(observation)
    directory, key = Path(directory), run_key(observation)
    diagnose = diagnose_once if diagnose is None else diagnose
    with _locked(directory):
        cursor_path = directory / 'cursor.json'
        cursor = _cursor(_read(cursor_path), observation) if cursor_path.exists() else {
            'schema_version': 1, 'rule_uid': RULE_UID, 'task_id': observation['task_id'], 'claimed_keys': []}
        claims = directory / 'claims'
        _private(claims, create=True)
        entries = sorted(claims.iterdir())
        require(len(entries) <= MAX_CLAIMS)
        known = {}
        for entry in entries:
            _private(entry)
            require(re.fullmatch(r'[0-9a-f]{64}', entry.name))
            claim_key = RULE_UID + ':' + entry.name
            if not (entry / 'result.json').exists():
                known[claim_key] = None
                continue
            record = _read(entry / 'result.json')
            require(type(record) is dict and record.get('run_key') == claim_key
                    and record.get('task_id') == observation['task_id'])
            saved_observation = normalize_observation(_read(entry / 'observation.json'))
            require(saved_observation['task_id'] == observation['task_id'] and run_key(saved_observation) == claim_key)
            require(_read(entry / 'intent.json') == {'schema_version': 1, 'run_key': claim_key,
                    'observation_sha256': hashlib.sha256(canonical(saved_observation)).hexdigest()})
            require(type(record.get('schema_version')) is int and record['schema_version'] == 1
                    and record.get('profile') == PROFILE and record.get('dispatched') is True)
            if record.get('result') == 'passed':
                require(record.get('cleanup_verified') is True and record.get('error_code') is None)
                validate_diagnostic(record.get('diagnostic'))
                try:
                    uuid.UUID(record['dagster_run_id'])
                except (ValueError, TypeError, KeyError, AttributeError):
                    raise ActivationError('ACTIVATION_EVIDENCE_INVALID') from None
            known[claim_key] = record
        unresolved = [k for k, v in known.items() if v is None or v.get('cleanup_verified') is not True
                      or v.get('result') not in ('passed', 'failed')]
        require(set(cursor['claimed_keys']) <= set(known))
        base = {'schema_version': 1, 'profile': PROFILE, 'task_id': observation['task_id'], 'run_key': key,
                'result': 'skipped', 'dispatched': False, 'dagster_run_id': None, 'diagnostic': None,
                'cleanup_verified': True, 'error_code': None}
        if unresolved:
            return dict(base, result='unknown', cleanup_verified=False, error_code='ACTIVATION_CLAIM_UNKNOWN')
        require(set(cursor['claimed_keys']) == set(known))
        _save(cursor_path, cursor)
        claim, first, result = None, None, dict(base)

        def invoke(run_id):
            nonlocal first
            result['dagster_run_id'] = run_id
            try:
                value = validate_diagnostic(diagnose(claim))
                result.update(diagnostic=value, cleanup_verified=True)
                return value
            except BaseException as error:
                first = _keep(first, error)
                raise

        job, sensor = definitions(observation, invoke)
        with build_sensor_context(cursor=canonical(cursor).decode('ascii')) as context:
            evaluated = sensor.evaluate_tick(context)
        require(len(evaluated.run_requests) <= 1)
        if not evaluated.run_requests:
            if key in known:
                prior = known[key]
                return dict(base, result='skipped' if prior['result'] == 'passed' else 'failed',
                            dagster_run_id=prior['dagster_run_id'], diagnostic=prior['diagnostic'],
                            error_code=prior['error_code'])
            return result
        request = evaluated.run_requests[0]
        require(request.run_key == key and key not in cursor['claimed_keys'])
        require(len(known) < MAX_CLAIMS, 'ACTIVATION_CLAIM_LIMIT')
        claim = claims / observation['fingerprint']
        claim.mkdir(mode=0o700)
        N._write(claim / 'observation.json', observation)
        result.update(result='unknown', dispatched=True, cleanup_verified=False,
                      error_code='ACTIVATION_DIAGNOSIS_UNKNOWN')
        # Claim and cursor are durable before any diagnostic side effect.
        N._write(claim / 'intent.json', {'schema_version': 1, 'run_key': key,
                                        'observation_sha256': hashlib.sha256(canonical(observation)).hexdigest()})
        cursor = _cursor(N._parse(evaluated.cursor), observation)
        require(cursor['claimed_keys'][-1] == key)
        _save(cursor_path, cursor)
        try:
            dagster_directory = directory / 'dagster'
            _private(dagster_directory, create=True)
            with DagsterInstance.local_temp(str(dagster_directory),
                                           overrides={'telemetry': {'enabled': False}}) as instance:
                execution = job.execute_in_process(instance=instance, raise_on_error=True,
                                                   tags=dict(request.tags, **{'dagster/run_key': key}))
                require(execution.success, 'ACTIVATION_JOB_FAILED')
                result.update(result='passed', error_code=None)
        except BaseException as error:
            first = _keep(first, error)
        finally:
            try:
                N._write(claim / 'result.json', result)
            except BaseException as error:
                first = _keep(first, error)
        if first is not None:
            raise first
        return result
