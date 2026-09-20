#!/usr/bin/env python3
"""One local alert cycle with an actual Dagster tick; no resident scheduler."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.local_experiment.activation import fixture as F, grafana as A, runtime as R, sensor as S
from tools.local_experiment.grafana import manage as G

STATE = ROOT / 'tmp/local-experiment/activation'
PROFILE = 'local-activation-cycle-v1'


@contextmanager
def locked():
    S._private(STATE, create=True)
    with S._locked(STATE):
        # The same lock covers the shared rule receipt and every experiment.
        yield


def save(directory, name, value):
    S.N._write(directory / name, value)


def wait_state(client, task_id, expected, directory, name):
    deadline = time.monotonic() + 120
    last = None
    while True:
        payload = client.request('/api/prometheus/grafana/api/v1/rules')
        # Normal/NoData/Pending are not silently promoted to Firing. The API
        # normalizer rejects unknown/error shapes before a sensor sees them.
        try:
            last = A.observe(payload, task_id, now=datetime.now(timezone.utc))
        except A.GrafanaError as error:
            if str(error) not in ('ACTIVATION_STATE_NOT_READY', 'ACTIVATION_STATE_STALE'):
                try:
                    save(directory, name + '-rejected-grafana.json', payload)
                    save(directory, name + '-error.json', {'error_code': str(error)})
                except BaseException as later:
                    raise S._keep(error, later)
                raise
            last = None
        if last is not None and last['state'] == expected:
            save(directory, name + '-grafana.json', payload)
            save(directory, name + '.json', last)
            print(json.dumps(dict(phase=name, state=last['state'])), flush=True)
            return last
        S.require(time.monotonic() < deadline, 'ACTIVATION_STATE_TIMEOUT')
        time.sleep(2)


def append(directory, task_id, status):
    value = F.build(task_id, status)
    # An ambiguous write is retained and never automatically repeated.
    save(directory, status + '-intent.json', value)
    F.append(value)
    save(directory, status + '-event.json', value)
    return value


def cycle():
    with locked():
        active_path = STATE / 'active.json'
        if active_path.exists():
            previous = S._read(active_path)
            S.require(previous.get('result') == 'passed', 'ACTIVATION_PREVIOUS_RUN_UNKNOWN')
        S.require(R.status().get('alerting') is True and G.status().get('ready') is True,
                  'ACTIVATION_RUNTIME_NOT_READY')
        runs, control = STATE / 'runs', STATE / 'control'
        S._private(runs, create=True); S._private(control, create=True)
        token = uuid.uuid4().hex
        output = runs / token; S._private(output, create=True)
        task_id = 'activation-' + token
        result = dict(schema_version=1, profile=PROFILE, token=token, task_id=task_id,
                      result='unknown', rule_uid=S.RULE_UID, diagnostic=None,
                      fixture_event_ids=[], duplicate_dispatches=None,
                      evidence=str(output.relative_to(ROOT)), error_code=None)
        S._save(active_path, result)
        save(output, 'intent.json', result)
        configured, first = False, None
        try:
            A.create_rule(task_id, control)
            configured = True
            A.configure_notifications(task_id, control)
            A.set_paused(task_id, control, False)
            client = A.Client()
            sensor_dir = output / 'sensor'; S._private(sensor_dir, create=True)
            normal = wait_state(client, task_id, 'normal', output, 'initial-normal')
            initial = S.tick(normal, sensor_dir)
            S.require(initial['result'] == 'skipped' and not initial['dispatched'])
            save(output, 'initial-tick.json', initial)
            failed = append(output, task_id, 'failed')
            result['fixture_event_ids'].append(failed['event_id'])
            firing = wait_state(client, task_id, 'firing', output, 'firing')
            diagnosed = S.tick(firing, sensor_dir)
            S.require(diagnosed['result'] == 'passed' and diagnosed['dispatched'])
            save(output, 'diagnostic-tick.json', diagnosed)
            result['diagnostic'] = diagnosed['diagnostic']
            repeated = wait_state(client, task_id, 'firing', output, 'duplicate-firing')
            S.require(repeated['fingerprint'] == firing['fingerprint'], 'ACTIVATION_ALERT_INSTANCE_CHANGED')
            duplicate = S.tick(repeated, sensor_dir)
            S.require(duplicate['result'] == 'skipped' and not duplicate['dispatched']
                      and duplicate['dagster_run_id'] == diagnosed['dagster_run_id'])
            save(output, 'duplicate-tick.json', duplicate)
            result['duplicate_dispatches'] = 0
            resolved = append(output, task_id, 'success')
            result['fixture_event_ids'].append(resolved['event_id'])
            normal = wait_state(client, task_id, 'normal', output, 'resolved-normal')
            final = S.tick(normal, sensor_dir)
            S.require(final['result'] == 'skipped' and not final['dispatched'])
            save(output, 'resolved-tick.json', final)
            result.update(result='passed', dagster_run_id=diagnosed['dagster_run_id'],
                          run_key=diagnosed['run_key'])
        except BaseException as error:
            first = error
            result['error_code'] = 'ACTIVATION_CYCLE_UNVERIFIED'
            if configured:
                try:
                    A.set_paused(task_id, control, True)
                    result['rule_paused_after_failure'] = True
                except BaseException as error:
                    first = S._keep(first, error)
                    result['rule_paused_after_failure'] = False
        finally:
            publication_failed = False
            for call in (lambda: save(output, 'result.json', result), lambda: S._save(active_path, result)):
                try:
                    call()
                except BaseException as error:
                    first = S._keep(first, error)
                    publication_failed = True
                    result.setdefault('completed_result', result['result'])
                    result.update(result='unknown', error_code='ACTIVATION_RESULT_SAVE_FAILED')
            if publication_failed:
                # A write may replace active.json before its final fsync/close
                # fails. Publish the conservative state without rerunning work.
                try:
                    S._save(active_path, result)
                except BaseException as error:
                    first = S._keep(first, error)
        if first is not None:
            raise first
        return result


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args:
        print(json.dumps(dict(result='failed', error_code='ACTIVATION_ARGUMENTS_INVALID')))
        return 1
    # Dagster also creates SQLite and event-log files beneath the private run.
    old_umask = os.umask(0o077)
    try:
        result = cycle()
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        print(json.dumps(dict(result='failed', error_code='ACTIVATION_CYCLE_UNVERIFIED')))
        return 1
    finally:
        os.umask(old_umask)


if __name__ == '__main__':
    raise SystemExit(main())
