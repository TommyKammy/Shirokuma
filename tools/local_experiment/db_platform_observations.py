"""Fixed LOCAL platform observations; persisted evidence is historical only."""
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import selectors
import sqlite3
import subprocess
import time

import audit_service as audit
from db_bear_observations import _decode, _text, _time
import logical_foundation
from query_context import QueryError
import runtime_profile
from warehouse_operations import OperationError
import warehouse_resident as wr

SAFE = 2**53 - 1
COLUMNS = ('custom-columns=NAME:.metadata.name,NS:.metadata.namespace,'
           'DESIRED:.spec.replicas,READY:.status.readyReplicas,'
           'GENERATION:.metadata.generation,OBSERVED:.status.observedGeneration')
SOURCE_ERRORS = (OSError, ValueError, TypeError, KeyError, AttributeError,
                 RecursionError, sqlite3.Error, QueryError, OperationError,
                 subprocess.SubprocessError)


def _now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _integer(value, maximum=SAFE):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError()
    return value


def _read(path):
    with path.open('rb') as stream:
        return _decode(stream.read(4097), 4096)


def _physical():
    with audit.snapshot() as db:
        foundation = wr._logical_foundation(db)
    if foundation['warehouse_id'] != 'LOCAL':
        raise ValueError()
    argv = [('--request-timeout=3s' if arg == '--request-timeout=15s' else arg)
            for arg in logical_foundation.kube_argv(foundation)]
    deadline = time.monotonic() + 8
    process = subprocess.Popen(argv + ['get', 'deployments', 'trino-local',
        'trino-local-worker', '-o', COLUMNS, '--no-headers'],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False)
    raw = b''
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise subprocess.TimeoutExpired(argv, 8)
                chunk = process.stdout.read1(4097 - len(raw))
                if not chunk:
                    break
                raw += chunk
                if len(raw) > 4096:
                    raise ValueError()
        remaining = deadline - time.monotonic()
        if remaining <= 0 or process.wait(timeout=remaining) != 0:
            raise ValueError()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
    lines = raw.decode('utf-8').splitlines()
    if len(lines) != 2:
        raise ValueError()
    rows, current = {}, True
    for line in lines:
        cols = line.split()
        if len(cols) != 6:
            raise ValueError()
        name, namespace, desired, ready, generation, observed = cols
        if (name not in ('trino-local', 'trino-local-worker') or name in rows
                or namespace != foundation['kube']['namespace']):
            raise ValueError()
        def count(text):
            if not re.fullmatch(r'[0-9]{1,16}', text):
                raise ValueError()
            return _integer(int(text))
        desired = count(desired)
        ready = 0 if ready == '<none>' else count(ready)
        generation = count(generation)
        if generation < 1:
            raise ValueError()
        observed = None if observed == '<none>' else count(observed)
        current = current and desired > 0 and ready == desired and observed is not None and observed >= generation
        rows[name] = (desired, ready)
    deployments = [dict(role=role, replicas=rows[name][0], ready_replicas=rows[name][1])
        for role, name in (('coordinator', 'trino-local'), ('worker', 'trino-local-worker'))]
    replicas = _integer(sum(r['replicas'] for r in deployments))
    ready = _integer(sum(r['ready_replicas'] for r in deployments))
    # State summarizes desired/ready Deployment counts only.
    state = 'active' if current else 'chilled' if replicas == ready == 0 else 'transitioning'
    return dict(state=state, replicas=replicas, ready_replicas=ready, deployments=deployments)


def _engine_evidence():
    root = Path(os.environ.get('SHIROKUMA_STATE_DIR') or
                Path(__file__).resolve().parents[2] / 'var/shirokuma')
    data = _read(root / 'local-engine-ready.json')
    cfg = runtime_profile.logical_config()
    if (type(data) is not dict or cfg['warehouse_id'] != 'LOCAL'
            or not _text(data.get('generation_id'), 256)
            or data['generation_id'] != cfg['foundation_generation_id']
            or _integer(data.get('revision')) < 1
            or type(cfg['foundation_revision']) is not int
            or data['revision'] != cfg['foundation_revision']
            or not _text(data.get('epoch'), 256)):
        raise ValueError()
    return dict(verified_at=_time(data.get('verified_at')))


def _auto_chill():
    data = _read(wr.activity_path().with_name('auto_chill.json'))
    if type(data) is not dict:
        raise ValueError()
    return dict(minutes=_integer(data.get('auto_chill_minutes'), 1440),
                revision=_integer(data.get('revision')))


def _saved_admissions():
    with audit.snapshot() as db:
        row = db.execute("""SELECT
            COUNT(CASE WHEN state='ACTIVE' THEN 1 END) AS active,
            COUNT(CASE WHEN state='UNKNOWN' THEN 1 END) AS unknown
            FROM warehouse_admissions WHERE warehouse_id='LOCAL'
            AND state IN ('ACTIVE','UNKNOWN')
            AND NOT (kind='query' AND execution_closure='FENCED_READ_ONLY')""").fetchone()
        return dict(active=_integer(row['active']), unknown=_integer(row['unknown']))


def _authority(principal, role):
    try:
        return audit._authority(None, principal, role, 'platform', None)[1]
    except (QueryError, OperationError, PermissionError, ValueError, OSError,
            KeyError, TypeError, AttributeError):
        raise OperationError('PLATFORM_ACCESS_DENIED', 403) from None


def observe(principal, role):
    fingerprint = _authority(principal, role)
    sources = {}
    for name, collect, code, fields in (
            ('physical', _physical, 'PHYSICAL_UNAVAILABLE',
             ('state', 'replicas', 'ready_replicas', 'deployments')),
            ('engine_evidence', _engine_evidence, 'ENGINE_EVIDENCE_UNAVAILABLE', ('verified_at',)),
            ('auto_chill', _auto_chill, 'POLICY_UNAVAILABLE', ('minutes', 'revision')),
            ('saved_admissions', _saved_admissions, 'ADMISSIONS_UNAVAILABLE', ('active', 'unknown'))):
        try:
            values = collect()
            source = dict(availability='available', code=None, **values)
        except SOURCE_ERRORS:
            source = dict(availability='unavailable', code=code, **dict.fromkeys(fields))
        source['observed_at'] = _now()
        sources[name] = source
    sources['engine_evidence']['live_probe'] = False
    if _authority(principal, role) != fingerprint:
        raise OperationError('PLATFORM_ACCESS_DENIED', 403)
    return dict(scope='platform', warehouse='LOCAL', selected_role='admin', observed_at=_now(),
        coverage=dict(kind='fixed_platform_observations', live_engine_probe=False,
                      policy_audit=False, complete_blocker_set=False), **sources)
