"""Read-only resource samples for three identity-checked local runtimes.

Only the fixed Lab-Python worker imports Jupyter. CPU is the change in ps
cumulative CPU time over one bounded interval, not a lifetime percentage. RSS
is the sum of resident sets and can count shared pages more than once.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.local_experiment.gateway import pod_usage as U

COMPONENTS = ('mlflow', 'notebook-lab', 'model-serving')
STATES = frozenset(('observed', 'stopped', 'unavailable', 'ownership-unknown'))
REASONS = frozenset(('STOPPED', 'UNINITIALIZED', 'READ_FAILED', 'READ_TIMEOUT', 'READ_LIMIT',
    'CLEANUP_UNVERIFIED', 'OWNERSHIP_UNKNOWN', 'IDENTITY_CHANGED', 'SAMPLE_INVALID', 'SCOPE_INVALID'))
MAX_PROCESSES = 128
MAX_PUBLIC_BYTES = 32768
MAX_RSS_BYTES = 2**50
MAX_ELAPSED_NS = 15_000_000_000
MIN_ELAPSED_NS = 500_000_000
WINDOW_TOLERANCE_NS = 100_000_000
PYTHON = ROOT/'tools/local_experiment/notebook_lab/.venv/bin/python'
WORKER = ROOT/'tools/local_experiment/finops/local_processes.py'


class ProcessError(ValueError):
    pass


def require(condition, code='SAMPLE_INVALID'):
    if not condition: raise ProcessError(code)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def _digest(value): return hashlib.sha256(_canonical(value)).hexdigest()


def arguments(): return [str(PYTHON), '-I', '-B', str(WORKER), '--worker']


def _integer(value, maximum):
    require(type(value) is str and len(value) <= 32 and re.fullmatch(r'0|[1-9][0-9]*', value)
            and int(value) <= maximum)
    return int(value)


def _entry(component, state, reason, start=None, end=None):
    return dict(component=component, state=state, reason=reason, request_started_at=start,
                observed_at=end, identity=None, sample=None)


def unknown_all(reason):
    require(reason in REASONS and reason not in ('STOPPED', 'UNINITIALIZED'), 'SCOPE_INVALID')
    state = 'ownership-unknown' if reason in ('OWNERSHIP_UNKNOWN', 'IDENTITY_CHANGED', 'SCOPE_INVALID') else 'unavailable'
    return dict(schema_version=1, components=[_entry(name, state, reason) for name in COMPONENTS])


def validate(value):
    """Pure exact public contract; safe to import in the default FinOps Python."""
    require(type(value) is dict and set(value) == {'schema_version', 'components'}
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and type(value['components']) is list and len(value['components']) == 3)
    observed_window, total_processes = None, 0
    for name, entry in zip(COMPONENTS, value['components']):
        require(type(entry) is dict and set(entry) == {'component', 'state', 'reason',
            'request_started_at', 'observed_at', 'identity', 'sample'}
            and entry['component'] == name and entry['state'] in STATES)
        start, end = entry['request_started_at'], entry['observed_at']
        require((start is None) == (end is None))
        span = None
        if start is not None:
            try: span = U.stamp(end)[1] - U.stamp(start)[1]
            except Exception: raise ProcessError('SAMPLE_INVALID') from None
            require(0 <= span <= MAX_ELAPSED_NS + WINDOW_TOLERANCE_NS)
        if entry['state'] != 'observed':
            require(entry['identity'] is None and entry['sample'] is None and entry['reason'] in REASONS)
            expected = 'stopped' if entry['reason'] == 'STOPPED' else 'ownership-unknown' if entry['reason'] in (
                'OWNERSHIP_UNKNOWN', 'IDENTITY_CHANGED', 'SCOPE_INVALID') else 'unavailable'
            require(entry['state'] == expected)
            continue
        require(entry['reason'] is None and span is not None)
        identity, sample = entry['identity'], entry['sample']
        require(type(identity) is dict and set(identity) == {'process_count', 'kernel_count', 'sha256'}
            and type(identity['process_count']) is int and 1 <= identity['process_count'] <= MAX_PROCESSES
            and type(identity['kernel_count']) is int and 0 <= identity['kernel_count'] < identity['process_count']
            and type(identity['sha256']) is str and re.fullmatch('[0-9a-f]{64}', identity['sha256']))
        require(name == 'notebook-lab' or identity['kernel_count'] == 0)
        require(name != 'notebook-lab' or identity['process_count'] == identity['kernel_count'] + 1)
        require(name != 'model-serving' or identity['process_count'] == 1)
        total_processes += identity['process_count']
        require(type(sample) is dict and set(sample) == {'cpu_time_delta_ms', 'elapsed_ns', 'rss_bytes'})
        elapsed = _integer(sample['elapsed_ns'], MAX_ELAPSED_NS)
        require(MIN_ELAPSED_NS <= elapsed and abs(span - elapsed) <= WINDOW_TOLERANCE_NS)
        _integer(sample['cpu_time_delta_ms'], 2**63-1)
        _integer(sample['rss_bytes'], MAX_RSS_BYTES)
        window = (start, end, elapsed)
        require(observed_window is None or observed_window == window)
        observed_window = window
    require(total_processes <= MAX_PROCESSES and len(_canonical(value)) <= MAX_PUBLIC_BYTES)
    return value


def _pairs(items):
    result = {}
    for key, value in items:
        require(key not in result)
        result[key] = value
    return result


def parse(raw):
    require(type(raw) is bytes and len(raw) <= MAX_PUBLIC_BYTES, 'READ_LIMIT')
    try:
        return validate(json.loads(raw, object_pairs_hook=_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ProcessError('SAMPLE_INVALID'))))
    except ProcessError: raise
    except Exception: raise ProcessError('SAMPLE_INVALID') from None


def _cpu_ms(value):
    match = re.fullmatch(r'(?:(\d+)-)?(?:(\d+):)?(\d+):(\d{2})\.(\d{1,3})', value)
    require(match is not None and len(value) <= 32)
    days, hours, minutes, seconds, fraction = match.groups()
    require(int(seconds) < 60 and (hours is None or int(minutes) < 60)
            and (days is None or hours is not None and int(hours) < 24))
    total = (((int(days or 0)*24 + int(hours or 0))*60 + int(minutes))*60 + int(seconds))*1000
    require(total <= 2**63-1)
    return total + int(fraction.ljust(3, '0'))


def parse_ps(raw, pids):
    """The fixed BSD ps columns contain only identity time and numeric resources."""
    require(type(raw) is bytes and len(raw) <= MAX_PUBLIC_BYTES, 'READ_LIMIT')
    require(type(pids) is set and 0 < len(pids) <= MAX_PROCESSES
            and all(type(pid) is int and pid > 1 for pid in pids), 'SCOPE_INVALID')
    result = {}
    try: lines = raw.decode('ascii').splitlines()
    except UnicodeError: raise ProcessError('SAMPLE_INVALID') from None
    for line in lines:
        parts = line.split()
        require(len(parts) == 8 and re.fullmatch('[1-9][0-9]*', parts[0]))
        pid = int(parts[0]); require(pid in pids and pid not in result)
        start = ' '.join(parts[1:6])
        try: datetime.strptime(start, '%a %b %d %H:%M:%S %Y')
        except ValueError: raise ProcessError('SAMPLE_INVALID') from None
        rss_kib = _integer(parts[6], MAX_RSS_BYTES//1024)
        result[pid] = dict(start_time=start, cpu_ms=_cpu_ms(parts[7]), rss_bytes=rss_kib*1024)
    require(set(result) == pids, 'IDENTITY_CHANGED')
    return result


def _identity(row):
    require(type(row) is dict and all(type(row.get(k)) is int for k in ('pid','ppid','pgid','uid'))
            and row['pid'] > 1 and row['pgid'] > 1 and row['uid'] == os.getuid()
            and type(row.get('start_time')) is str and 1 <= len(row['start_time']) <= 64
            and type(row.get('command')) is str and not row.get('state','').startswith('Z'), 'OWNERSHIP_UNKNOWN')
    return dict(pid=row['pid'], ppid=row['ppid'], pgid=row['pgid'], uid=row['uid'],
                start_time=row['start_time'], command_sha256=hashlib.sha256(row['command'].encode()).hexdigest())


class Backend:
    """Loaded only by --worker in the existing private Lab environment."""
    def __init__(self):
        from tools.local_experiment.mlflow import runtime as mlflow
        from tools.local_experiment.notebook_lab import runtime as lab
        from tools.local_experiment.mlflow import serving_runtime as serving
        self.mlflow, self.lab, self.serving = mlflow, lab, serving

    @staticmethod
    def now(): return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')
    monotonic_ns = staticmethod(time.monotonic_ns)
    sleep = staticmethod(time.sleep)

    def snapshot(self, component):
        module = dict(zip(COMPONENTS, (self.mlflow,self.lab,self.serving)))[component]
        public = module.status()
        state = public['state']
        if state != 'running':
            return dict(state='stopped' if state == 'stopped' else 'unavailable' if state == 'uninitialized'
                        else 'ownership-unknown', reason='STOPPED' if state == 'stopped'
                        else 'UNINITIALIZED' if state == 'uninitialized' else 'OWNERSHIP_UNKNOWN',
                        identities={}, kernel_count=0)
        rows = self.mlflow.processes()
        kernels = 0
        if component == 'mlflow':
            record = module._record(); require(record is not None, 'OWNERSHIP_UNKNOWN')
            require(module._same(rows.get(record['leader']['pid']),record['leader']), 'OWNERSHIP_UNKNOWN')
            members = module._owned_group(record, rows)
            ownership = dict(store_id=public.get('store_id'), record=record)
        elif component == 'notebook-lab':
            owner = module._owner(); record = module._record(owner); require(record is not None, 'OWNERSHIP_UNKNOWN')
            leader = module._leader(record, rows); require(leader is not None, 'OWNERSHIP_UNKNOWN')
            ids = module._kernels(); bound = module._kernel_processes(record, ids, rows)
            members = [leader] + list(bound.values()); kernels = len(bound)
            ownership = dict(owner=owner, record=record)
        else:
            owner = module._owner(); launch = module._intent(owner); record = module._record(owner, launch)
            leader = module._owned(record, rows); require(leader is not None, 'OWNERSHIP_UNKNOWN')
            members = [leader]
            ownership = dict(owner=owner, launch=launch, record=record)
        require(0 < len(members) <= MAX_PROCESSES, 'SCOPE_INVALID')
        identities = {row['pid']:_identity(row) for row in members}
        require(len(identities) == len(members), 'OWNERSHIP_UNKNOWN')
        return dict(state='observed', reason=None, identities=identities, kernel_count=kernels,
                    ownership_sha256=_digest(ownership))

    @staticmethod
    def sample(pids):
        argv = ['/bin/ps','-p',','.join(str(pid) for pid in sorted(pids)),
                '-o','pid=','-o','lstart=','-o','rss=','-o','time=']
        try:
            result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=2, check=False, env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','LC_ALL':'C'})
        except subprocess.TimeoutExpired: raise ProcessError('READ_TIMEOUT') from None
        require(result.returncode == 0, 'IDENTITY_CHANGED')
        return parse_ps(result.stdout, pids)


def _reason(error, default='READ_FAILED'):
    if isinstance(error, ProcessError) and str(error) in REASONS: return str(error)
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)): return 'READ_TIMEOUT'
    if str(error) == 'LAB_HTTP_FAILED': return 'READ_FAILED'
    if str(error) == 'LAB_HTTP_TOO_LARGE': return 'READ_LIMIT'
    if isinstance(error, OSError): return 'READ_FAILED'
    return default


def _snapshot(backend, name):
    try: return backend.snapshot(name)
    except Exception as error:
        reason = _reason(error, 'OWNERSHIP_UNKNOWN')
        return dict(state='ownership-unknown' if reason in ('OWNERSHIP_UNKNOWN','IDENTITY_CHANGED','SCOPE_INVALID')
                    else 'unavailable', reason=reason, identities={}, kernel_count=0)


def collect(backend=None):
    """Two process samples, with strict runtime ownership snapshots surrounding them."""
    backend = Backend() if backend is None else backend
    before = {name:_snapshot(backend,name) for name in COMPONENTS}
    pids, duplicate = set(), set()
    for item in before.values():
        duplicate |= pids & set(item['identities']); pids |= set(item['identities'])
    if len(pids) > MAX_PROCESSES: return unknown_all('SCOPE_INVALID')
    start, tick = backend.now(), backend.monotonic_ns()
    first, second, failure = {}, {}, None
    if pids:
        try:
            first = backend.sample(pids); backend.sleep(1)
            second = backend.sample(pids)
        except Exception as error: failure = _reason(error)
    elapsed, end = backend.monotonic_ns()-tick, backend.now()
    after = {name:_snapshot(backend,name) for name in COMPONENTS}
    entries = []
    for name in COMPONENTS:
        old, new = before[name], after[name]
        entry = _entry(name, old['state'],old['reason'],start,end)
        try:
            require(old == new, 'IDENTITY_CHANGED')
            if old['state'] == 'observed':
                require(not (duplicate & set(old['identities'])), 'OWNERSHIP_UNKNOWN')
                require(failure is None, failure or 'READ_FAILED')
                delta, rss = 0, 0
                for pid, identity in old['identities'].items():
                    a, b = first[pid], second[pid]
                    require(a['start_time'] == b['start_time'] == identity['start_time'], 'IDENTITY_CHANGED')
                    require(b['cpu_ms'] >= a['cpu_ms'], 'SAMPLE_INVALID')
                    delta += b['cpu_ms']-a['cpu_ms']; rss += b['rss_bytes']
                entry['identity'] = dict(process_count=len(old['identities']), kernel_count=old['kernel_count'],
                    sha256=_digest(dict(ownership=old.get('ownership_sha256'),
                        processes=[old['identities'][pid] for pid in sorted(old['identities'])])))
                entry['sample'] = dict(cpu_time_delta_ms=str(delta),elapsed_ns=str(elapsed),rss_bytes=str(rss))
        except Exception as error:
            reason = _reason(error)
            entry = _entry(name,'ownership-unknown' if reason in ('IDENTITY_CHANGED','OWNERSHIP_UNKNOWN','SCOPE_INVALID')
                           else 'unavailable',reason,start,end)
        entries.append(entry)
    return validate(dict(schema_version=1,components=entries))


def main(argv=None, *, stdout=None, backend=None):
    stdout = sys.stdout if stdout is None else stdout
    args = sys.argv[1:] if argv is None else argv
    try:
        require(args == ['--worker'],'SCOPE_INVALID')
        result = collect(backend)
    except Exception as error: result = unknown_all(_reason(error))
    stdout.write(_canonical(result).decode()+'\n'); stdout.flush()
    # Unknown rows are a valid observation payload, not a transport failure.
    return 0


if __name__ == '__main__': raise SystemExit(main())
