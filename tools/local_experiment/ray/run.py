#!/usr/bin/env python3
"""One private local Ray job; no cluster attachment, fit, or retained-data write."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import ipaddress
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.local_experiment.ray import prediction as C
from tools.local_experiment.notebook import client as N
from tools.local_experiment.mlflow import runtime as M

STATE = ROOT / 'tmp/local-experiment/ray-runs'
PYTHON = Path(__file__).resolve().parent / '.venv/bin/python'
SHORT_ROOT = Path('/private/tmp')
SECONDS, GRACE_SECONDS, MAX_RUNS = 180, 60, 128
PS_ARGV = ['/bin/ps', '-ax', '-o', 'pid=,ppid=,pgid=,ruid=,uid=,stat=,lstart=,command=']
ERROR_CODES = frozenset({'RAY_ARGUMENTS_INVALID', 'RAY_SOURCE_INVALID', 'RAY_RESULT_INVALID',
    'RAY_PARITY_FAILED', 'RAY_RUNTIME_FAILED', 'RAY_RUNTIME_VERSION', 'RAY_LISTENER_INVALID',
    'RAY_OWNERSHIP_INVALID', 'RAY_CLEANUP_UNKNOWN', 'RAY_BUSY', 'RAY_INTERRUPTED', 'RAY_TIMEOUT'})


class Interrupted(BaseException):
    def __init__(self, signum):
        self.signum = signum


def require(value, code='RAY_OWNERSHIP_INVALID'):
    C.require(value, code)


def _code(error):
    if not isinstance(error, Exception): return 'RAY_INTERRUPTED'
    if (type(error) is C.RayPredictionError and len(error.args) == 1
            and type(error.args[0]) is str and error.args[0] in ERROR_CODES):
        return error.args[0]
    return 'RAY_RUNTIME_FAILED'


@contextmanager
def _signals():
    previous, first = {}, None
    def caught(signum, frame): raise Interrupted(signum)
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, caught)
        yield
    except BaseException as error: first = error
    finally:
        for signum, handler in previous.items():
            try: signal.signal(signum, handler)
            except BaseException as error: first = C.keep(first, error)
    if first is not None: raise first


def _private(path):
    require(all(not p.is_symlink() for p in (path, *path.parents)))
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)


def _json(path, maximum=65536):
    require(all(not p.is_symlink() for p in (path, *path.parents)))
    return N._parse(N._read(path, maximum))


def _directory(path):
    value = Path(path)
    require(value.is_absolute() and value.parent == STATE and N.TOKEN.fullmatch(value.name))
    _private(STATE); _private(value)
    return value


def _short(directory):
    # Keep Unix-domain socket names under Darwin's 104-byte sockaddr_un limit.
    return SHORT_ROOT / ('skr-' + str(os.getuid()) + '-' + directory.name[:12])


def arguments(directory):
    return [str(PYTHON), '-I', '-B', str(Path(__file__).resolve()), '--worker', str(directory)]


def environment(directory):
    return {'PATH': str(PYTHON.parent) + ':/usr/bin:/bin', 'LC_ALL': 'C',
            'TMPDIR': str(_short(directory)), 'PYTHONPATH': str(ROOT),
            'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1',
            'RAY_USAGE_STATS_ENABLED': '0', 'DO_NOT_TRACK': 'true', 'MLFLOW_DISABLE_TELEMETRY': 'true',
            'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1', 'VECLIB_MAXIMUM_THREADS': '1'}


@contextmanager
def _lock():
    STATE.mkdir(mode=0o700, exist_ok=True)
    _private(STATE)
    fd, first = None, None
    try:
        fd = os.open(STATE/'owner.lock', os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW, 0o600)
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1)
        try: fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise C.RayPredictionError('RAY_BUSY') from None
        entries = [p for p in STATE.iterdir() if p.name != 'owner.lock']
        require(len(entries) < MAX_RUNS, 'RAY_BUSY')
        for entry in entries:
            _directory(entry)
            require(_json(entry/'status.json').get('cleanup_verified') is True, 'RAY_CLEANUP_UNKNOWN')
        yield
    except BaseException as error: first = error
    finally:
        if fd is not None:
            try: os.close(fd)
            except BaseException as error: first = C.keep(first, error)
    if first is not None: raise first


def processes():
    # Darwin ps is setuid-root. Preserve both UIDs, rather than mistaking our
    # own inspection subprocess for an unrelated privileged Ray descendant.
    result = M._command(PS_ARGV)
    require(result.returncode == 0)
    rows = {}
    for line in result.stdout.decode('utf-8').splitlines():
        parts = line.split(None, 11)
        require(len(parts) == 12)
        pid, ppid, pgid, real_uid, effective_uid = map(int, parts[:5])
        rows[pid] = dict(pid=pid, ppid=ppid, pgid=pgid, uid=real_uid,
                         effective_uid=effective_uid, state=parts[5],
                         start_time=' '.join(parts[6:11]), command=parts[11])
    return rows


def saved_identity(value):
    require(type(value) is dict and set(value) == {'pid','pgid','uid','start_time'}
            and all(type(value[k]) is int for k in ('pid','pgid','uid'))
            and value['pid'] > 1 and value['pgid'] > 1 and value['uid'] == os.getuid()
            and type(value['start_time']) is str and bool(value['start_time']))
    return value


def identity(row, *, allow_inspector=False):
    require(type(row) is dict and type(row.get('ppid')) is int
            and type(row.get('effective_uid')) is int)
    value = saved_identity({k:row.get(k) for k in ('pid','pgid','uid','start_time')})
    require(row['effective_uid'] == os.getuid() or (allow_inspector
            and row['effective_uid'] == 0 and row.get('command') == ' '.join(PS_ARGV)))
    return value


def _known_identity(row, saved):
    require(type(row) is dict and type(row.get('ppid')) is int
            and type(row.get('effective_uid')) is int and type(row.get('state')) is str)
    current = saved_identity({k:row.get(k) for k in ('pid','pgid','uid','start_time')})
    require(current == saved)
    # An already bound, exited setuid inspector can become <defunct> before
    # its owner reaps it. No signal is sent based on this dead-process check.
    if not row['state'].startswith('Z'):
        require(identity(row, allow_inspector=True) == saved)


def capture(leader, known, rows):
    """Track descendants across new process groups without trusting argv text."""
    current = rows.get(leader['pid'])
    if current is not None:
        require(identity(current) == leader)
    for pid, saved in list(known.items()):
        if pid in rows: _known_identity(rows[pid], saved)
    owners = {pid for pid in known if pid in rows}
    if current is not None: owners.add(leader['pid'])
    changed = True
    while changed:
        changed = False
        for row in rows.values():
            if row['ppid'] in owners and row['pid'] not in owners:
                known[row['pid']] = identity(row, allow_inspector=True)
                owners.add(row['pid']); changed = True
    return known


def gone(known, rows):
    for pid, saved in known.items():
        current = rows.get(pid)
        if current is None: continue
        _known_identity(current, saved)
        if not current.get('state', '').startswith('Z'): return False
    return True


def listeners(pids):
    if not pids: return []
    result = subprocess.run(['/usr/sbin/lsof','-nP','-a','-p',','.join(str(p) for p in sorted(pids)),
        '-iTCP','-sTCP:LISTEN','-Fpn'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, timeout=5, env={'PATH':'/usr/bin:/bin','LC_ALL':'C'})
    require(result.returncode in (0,1) and len(result.stdout) <= 65536, 'RAY_LISTENER_INVALID')
    records, pid = [], None
    for line in result.stdout.decode('ascii').splitlines():
        if line.startswith('p'):
            require(line[1:].isdigit(), 'RAY_LISTENER_INVALID'); pid = int(line[1:])
        elif line.startswith('n'):
            require(pid in pids and re.fullmatch(r'(127\.0\.0\.1|\[::1\]):[0-9]{1,5}', line[1:]),
                    'RAY_LISTENER_INVALID')
            records.append({'pid':pid,'address':line[1:]})
    return records


def _bound(directory):
    value = _json(directory/'input.json', 4096)
    require(type(value) is dict and set(value) == {'schema_version','training_token'}
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and type(value['training_token']) is str and N.TOKEN.fullmatch(value['training_token']))
    intent = _json(directory/'intent.json')
    require(intent == {'schema_version':1,'profile':C.PROFILE,'argv':arguments(directory),
                       'input_sha256':C.P.digest(value),'short_temp':str(_short(directory))})
    return value


def worker(directory, *, ray_module=None):
    directory = _directory(directory)
    request = _bound(directory)
    short = _short(directory); _private(short)
    require(not list(short.iterdir()))
    deadline = time.monotonic()+30
    while not (directory/'launch.json').exists() and time.monotonic() < deadline: time.sleep(.05)
    launch = _json(directory/'launch.json')
    self_row = processes().get(os.getpid())
    require(self_row is not None and launch == dict(identity(self_row), ppid=os.getppid())
            and self_row['pgid'] == os.getpid())
    leader, known = identity(self_row), {}
    first, public, initialized, closed, listening = None, None, False, False, []
    ray = ray_module
    def inspect(actor_pids=()):
        rows = processes(); capture(leader, known, rows)
        require(all(pid in known and pid in rows for pid in actor_pids))
        found = listeners(set(known)|{leader['pid']})
        if actor_pids: require(found, 'RAY_LISTENER_INVALID')
        N._write(directory/'processes.json', list(known.values()), replace=True)
        return found
    try:
        C.T.load_saved_review(request['training_token'], kind='iceberg')
        if ray is None:
            import ray
        require(ray.__version__ == C.VERSION and ray.__commit__ == C.COMMIT
                and not ray.is_initialized(), 'RAY_RUNTIME_VERSION')
        require(ipaddress.ip_address(ray._common.network_utils.get_localhost_ip()).is_loopback,
                'RAY_LISTENER_INVALID')
        initialized = True  # init may fail after allocating processes.
        ray.init(address='local', num_cpus=2, num_gpus=0, include_dashboard=False, dashboard_host='127.0.0.1',
                 object_store_memory=128*1024**2, _node_ip_address='127.0.0.1',
                 _temp_dir=str(short/'r'), log_to_driver=False,
                 _system_config={'enable_metrics_collection':False})
        inspect()
        def inspected(pids):
            nonlocal listening
            listening = inspect(pids)
        public = C.evaluate(request['training_token'], ray, inspected)
    except BaseException as error: first = error
    finally:
        if initialized:
            try: inspect()
            except BaseException as error: first = C.keep(first,error)
            try: ray.shutdown(wait_for_processes=True)
            except BaseException as error: first = C.keep(first,error)
        try:
            _private(short)  # Ray may share its inner temp dir; the outer stays private.
            capture(leader, known, processes())
            closed = gone(known, processes())
            require(closed, 'RAY_CLEANUP_UNKNOWN')
            N._write(directory/'processes.json', list(known.values()), replace=True)
        except BaseException as error: closed=False; first=C.keep(first,error)
        result = {'schema_version':1,'profile':C.PROFILE,'result':'passed' if first is None else
                  'unknown' if not closed else 'interrupted' if not isinstance(first,Exception) else 'failed',
                  'cleanup_verified':closed,'error_code':None if first is None else _code(first),
                  'observation':public if first is None and closed else None,'listeners':listening}
        try: N._write(directory/'worker-result.json',result)
        except BaseException as error: first=C.keep(first,error)
    if first is not None and not isinstance(first,Exception): raise first
    return 0 if first is None else 1


def _interrupt(child, leader, known, first):
    try:
        current = processes().get(child.pid)
        if child.poll() is None and current is not None:
            require(identity(current) == leader)
            os.kill(child.pid, signal.SIGINT)
    except BaseException as error: first=C.keep(first,error)
    deadline=time.monotonic()+GRACE_SECONDS
    while child.poll() is None and time.monotonic()<deadline:
        try:
            capture(leader,known,processes())
            child.wait(timeout=min(.2,max(.001,deadline-time.monotonic())))
        except subprocess.TimeoutExpired: pass
        except BaseException as error: first=C.keep(first,error)
    return first


def run(token):
    require(type(token) is str and N.TOKEN.fullmatch(token), 'RAY_ARGUMENTS_INVALID')
    try: C.T.load_saved_review(token,kind='iceberg')
    except Exception: raise C.RayPredictionError('RAY_SOURCE_INVALID') from None
    with _lock():
        directory=STATE/uuid.uuid4().hex; directory.mkdir(mode=0o700)
        short=_short(directory)
        socket_path=short/'r/session_2000-01-01_00-00-00_000000_2147483647/sockets/plasma_store'
        require(SHORT_ROOT.is_dir() and not SHORT_ROOT.is_symlink() and len(str(socket_path).encode())<=103)
        short.mkdir(mode=0o700); _private(short)
        request={'schema_version':1,'training_token':token}
        N._write(directory/'input.json',request)
        N._write(directory/'intent.json',dict(schema_version=1,profile=C.PROFILE,argv=arguments(directory),
                 input_sha256=C.P.digest(request),short_temp=str(short)))
        status=dict(schema_version=1,result='running',pid=None,process_start=None,child_exited=False,
                    exit_code=None,cleanup_verified=False,error_code=None)
        child,leader,first,observation=None,None,None,None
        known={}; streams=[]
        try:
            N._write(directory/'status.json',status,replace=True)
            for name in ('worker.stdout.log','worker.stderr.log'):
                streams.append(os.fdopen(os.open(directory/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'wb'))
            child=subprocess.Popen(arguments(directory),cwd=ROOT,stdin=subprocess.DEVNULL,
                stdout=streams[0],stderr=streams[1],env=environment(directory),start_new_session=True,umask=0o077)
            row=processes().get(child.pid)
            require(row is not None and row['ppid']==os.getpid() and row['pgid']==child.pid
                    and row['command']==' '.join(arguments(directory)))
            leader=identity(row);status.update(pid=child.pid,process_start=leader['start_time'])
            N._write(directory/'status.json',status,replace=True)
            N._write(directory/'launch.json',dict(leader,ppid=os.getpid()))
            deadline=time.monotonic()+SECONDS
            while child.poll() is None and time.monotonic()<deadline:
                capture(leader,known,processes())
                try: child.wait(timeout=.2)
                except subprocess.TimeoutExpired: pass
            require(child.poll() is not None, 'RAY_TIMEOUT')
        except BaseException as error:
            first=error
            if child is not None and leader is not None and child.poll() is None:
                first=_interrupt(child,leader,known,first)
        finally:
            try:
                if child is None:
                    status.update(child_exited=True,exit_code=1,cleanup_verified=True)
                else:
                    rc=child.poll();status.update(child_exited=rc is not None,exit_code=rc)
                    if rc is not None and leader is not None:
                        capture(leader,known,processes())
                        for saved in _json(directory/'processes.json'):
                            saved_identity(saved)
                            require(saved['pid'] not in known or known[saved['pid']]==saved)
                            known[saved['pid']]=saved
                        closed=gone(known,processes())
                        result=_json(directory/'worker-result.json')
                        require(type(result) is dict and set(result)=={'schema_version','profile','result',
                                'cleanup_verified','error_code','observation','listeners'}
                                and type(result['schema_version']) is int and result['schema_version']==1
                                and result['profile']==C.PROFILE and type(result['cleanup_verified']) is bool)
                        status['cleanup_verified']=closed and result['cleanup_verified']
                        if first is None:
                            require(rc==0 and result['result']=='passed' and status['cleanup_verified'], 'RAY_RUNTIME_FAILED')
                            observation=result['observation']
                            with C.saved_batch(token) as (request,baseline):
                                C.validate_result(observation,request,baseline)
                            require(all(actor['pid'] in known for actor in observation['actors']))
                require(status['cleanup_verified'], 'RAY_CLEANUP_UNKNOWN')
            except BaseException as error: first=C.keep(first,error)
            for stream in reversed(streams):
                try: stream.close()
                except BaseException as error: first=C.keep(first,error)
            status.update(result='passed' if first is None else 'unknown' if not status['cleanup_verified'] else
                          'interrupted' if not isinstance(first,Exception) else 'failed',
                          error_code=None if first is None else _code(first))
            try: N._write(directory/'status.json',status,replace=True)
            except BaseException as error: first=C.keep(first,error)
        if first is not None:
            if not isinstance(first,Exception): raise first
            raise C.RayPredictionError('RAY_CLEANUP_UNKNOWN' if not status['cleanup_verified'] else _code(first)) from None
        return dict(observation,result='passed',cleanup_verified=True,
                    runtime=dict(ray_version=C.VERSION,ray_commit=C.COMMIT,num_cpus=2,num_gpus=0,
                                 object_store_bytes=128*1024**2),
                    evidence=str((directory/'status.json').relative_to(ROOT)))


def main(argv=None, *, stdout=None):
    argv=sys.argv[1:] if argv is None else argv
    stdout=sys.stdout if stdout is None else stdout
    first=None
    try:
        with _signals():
            if len(argv)==2 and argv[0]=='--worker': return worker(argv[1])
            require(len(argv)==2 and argv[0]=='--training-token', 'RAY_ARGUMENTS_INVALID')
            value=run(argv[1])
    except BaseException as error:
        first=error
        value=dict(schema_version=1,profile=C.PROFILE,result='interrupted' if not isinstance(error,Exception) else 'failed',
                   cleanup_verified=False,error_code=_code(error),observation=None,scope=C.SCOPE)
    try: stdout.write(C.P.canonical(value).decode('ascii')+'\n');stdout.flush()
    except BaseException as error: first=C.keep(first,error)
    if first is not None and not isinstance(first,Exception):
        return 128+first.signum if isinstance(first,Interrupted) else 130
    return 0 if first is None else 1


if __name__=='__main__':
    raise SystemExit(main())
