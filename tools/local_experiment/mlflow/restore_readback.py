"""Read a completed private restore with the pinned SDK in an isolated worker.

No live tracking service is contacted. The Python audit hook limits this trusted
SDK worker's network and writes; it is not a general OS sandbox. SQLite logical
comparison occurs in the parent only after the SDK process has exited/reaped.
"""
from contextlib import contextmanager
import argparse
import math
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
from urllib.parse import urlsplit, unquote
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.mlflow import restore as R

PYTHON = Path(__file__).resolve().parent / '.venv/bin/python'
MAX_RUNS = 1024
MAX_LOG = 2 * 1024 * 1024


class ReadbackError(ValueError):
    """Fixed safe code, without SDK response or filesystem details."""


class ReadbackInterrupted(BaseException):
    def __init__(self, signum):
        super().__init__('INTERRUPTED')
        self.signum = signum


def require(value, code='READBACK_INVALID'):
    if not value:
        raise ReadbackError(code)


def keep(first, later):
    return later if first is None or (isinstance(first, Exception) and not isinstance(later, Exception)) else first


@contextmanager
def signals():
    previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    def stop(signum, _frame):
        raise ReadbackInterrupted(signum)
    try:
        for s in previous:
            signal.signal(s, stop)
        yield
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)


def logical_digest(snapshot):
    schema, tables = snapshot
    return R.digest(R.canonical({'schema': R.row_hash(schema), 'tables': {
        name: {'columns': table['columns'], 'count': len(table['rows']), 'sha256': R.row_hash(table['rows'])}
        for name, table in tables.items()}}))


def seal(checked):
    """The completed receipt, inert export, original backup and artifacts stay fixed."""
    root = checked['root']
    return {'trees': {name: R.inventory(path) for name, path in (
        ('backup', checked['source_backup']), ('export', root / 'export'),
        ('artifacts', checked['clone_artifacts']))},
        'files': {str(path): (R.digest(R.read(path)), R.fingerprint(path.lstat())) for path in (
            checked['source_manifest'], root / 'clone.json', root / 'result.json')}}


def environment(evidence, database, artifacts):
    """A fresh mapping, never runtime.environment() or parent credential inheritance."""
    uri = 'sqlite:///' + str(database)
    return {'HOME': str(evidence / 'home'), 'XDG_CONFIG_HOME': str(evidence / 'home/config'),
            'XDG_CACHE_HOME': str(evidence / 'home/cache'), 'XDG_DATA_HOME': str(evidence / 'home/data'),
            'TMPDIR': str(evidence / 'tmp'), 'TMP': str(evidence / 'tmp'), 'TEMP': str(evidence / 'tmp'),
            'PATH': str(PYTHON.parent) + ':/usr/bin:/bin', 'LC_ALL': 'C',
            '__CF_USER_TEXT_ENCODING': f'0x{os.getuid():X}:0x0:0x0',
            'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1',
            'MLFLOW_DISABLE_TELEMETRY': 'true', 'DO_NOT_TRACK': 'true', 'OTEL_SDK_DISABLED': 'true',
            'MLFLOW_ENABLE_WORKSPACES': 'false', 'MLFLOW_ENABLE_REMOTE_ASSISTANT': 'false',
            'MLFLOW_SERVER_ENABLE_JOB_EXECUTION': 'false', 'MLFLOW_CONFIGURE_LOGGING': 'false',
            'MLFLOW_TRACKING_URI': uri, 'MLFLOW_REGISTRY_URI': uri,
            '_MLFLOW_SERVER_ARTIFACT_ROOT': artifacts.as_uri()}


def audit_guard(clone, evidence):
    """Return a small Python audit callback; install only in the disposable worker."""
    allowed = (clone.resolve(), evidence.resolve())
    database = str(clone / 'store/tracking.sqlite')
    def path_ok(value):
        require(isinstance(value, (str, bytes, os.PathLike)), 'READBACK_WRITE_REFUSED')
        path = Path(os.fsdecode(value))
        path = path if path.is_absolute() else Path.cwd() / path
        require(not any(p.is_symlink() for p in (path, *path.parents)), 'READBACK_WRITE_REFUSED')
        require(any(path.resolve().is_relative_to(base) for base in allowed), 'READBACK_WRITE_REFUSED')
    def guard(event, args):
        if event == 'sqlite3.connect':
            require(isinstance(args[0], (str, bytes, os.PathLike))
                    and os.fsdecode(args[0]) in (':memory:', database), 'READBACK_DATABASE_REFUSED')
        if event in ('socket.connect', 'socket.bind', 'socket.getaddrinfo', 'socket.sendto',
                     'socket.gethostbyname', 'socket.gethostbyaddr', 'subprocess.Popen',
                     'os.system', 'os.posix_spawn', 'os.exec', 'os.symlink', 'os.link'):
            raise ReadbackError('READBACK_EXTERNAL_OPERATION_REFUSED')
        if event == 'open':
            path, mode, flags = args
            writing = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if writing and not isinstance(path, int):
                path_ok(path)
            # fdopen wraps a descriptor already opened through the path guard.
            # The worker inherits only stdin and its private stdout/stderr log.
        elif event in ('os.mkdir', 'os.remove', 'os.rmdir', 'os.chmod', 'os.chown', 'os.utime', 'os.truncate'):
            path_ok(args[0])
            if event in ('os.mkdir', 'os.remove', 'os.rmdir', 'os.chmod', 'os.chown', 'os.utime'):
                require(args[-1] in (-1, None), 'READBACK_WRITE_REFUSED')
        elif event in ('os.rename', 'os.replace'):
            path_ok(args[0]); path_ok(args[1])
            require(all(x in (-1, None) for x in args[2:]), 'READBACK_WRITE_REFUSED')
        elif event == 'os.chdir':
            path_ok(args[0])
    return guard


def uri_path(value, base, *, expected=None):
    require(type(value) is str, 'READBACK_ARTIFACT_REFUSED')
    parts = urlsplit(value)
    require(parts.scheme == 'file' and not parts.netloc and not parts.query and not parts.fragment,
            'READBACK_ARTIFACT_REFUSED')
    path = Path(unquote(parts.path))
    require(path.is_absolute() and path.as_uri() == value and path.is_relative_to(base)
            and not any(p.is_symlink() for p in (path, *path.parents))
            and (expected is None or path == expected), 'READBACK_ARTIFACT_REFUSED')
    return path


def number(value):
    require(type(value) in (int, float), 'READBACK_METRIC_INVALID')
    value = float(value)
    return 'nan' if math.isnan(value) else value.hex()


def expected_view(checked):
    _, tables = checked['expected_snapshot']
    rows = lambda name: [dict(zip(tables[name]['columns'], row)) for row in tables[name]['rows']]
    experiments, runs = {}, {}
    for row in rows('experiments'):
        key = str(row['experiment_id'])
        uri_path(row['artifact_location'], checked['clone_artifacts'])
        experiments[key] = {'name': row['name'], 'artifact_location': row['artifact_location'],
            'lifecycle_stage': row['lifecycle_stage'],
            'tags': {r['key']: r['value'] for r in rows('experiment_tags') if str(r['experiment_id']) == key}}
    require(experiments.get('0', {}).get('name') == 'Default', 'READBACK_DEFAULT_MISSING')
    for row in rows('runs'):
        key = row['run_uuid']
        require(type(key) is str and R.TOKEN.fullmatch(key), 'READBACK_RUN_INVALID')
        artifact_root = uri_path(row['artifact_uri'], checked['clone_artifacts'])
        info = {k: row[k] for k in ('status', 'start_time', 'end_time', 'lifecycle_stage', 'artifact_uri', 'user_id')}
        info.update(run_id=key, experiment_id=str(row['experiment_id']), run_name=row['name'])
        selected = lambda table: [r for r in rows(table) if r['run_uuid'] == key]
        histories = {}
        for r in selected('metrics'):
            histories.setdefault(r['key'], []).append([r['key'], r['timestamp'], r['step'], 'nan' if r['is_nan'] else number(r['value'])])
        files, _ = R.inventory(artifact_root)
        runs[key] = {'info': info, 'params': {r['key']: r['value'] for r in selected('params')},
            'tags': {r['key']: r['value'] for r in selected('tags')},
            'metrics': {r['key']: 'nan' if r['is_nan'] else number(r['value']) for r in selected('latest_metrics')},
            'histories': {k: sorted(v) for k, v in histories.items()},
            'artifacts': {k: {'is_dir': v['kind'] == 'directory', 'size': v.get('size'), 'sha256': v.get('sha256')}
                          for k, v in files.items() if k != '.'}}
    require(0 < len(runs) <= MAX_RUNS, 'READBACK_RUN_LIMIT')
    return {'experiments': experiments, 'runs': runs}


def counts(view):
    return {'experiments': len(view['experiments']), 'runs': len(view['runs']),
            'metrics': sum(len(points) for run in view['runs'].values() for points in run['histories'].values()),
            'artifacts': sum(not x['is_dir'] for run in view['runs'].values() for x in run['artifacts'].values())}


def sdk_read(checked, expected, *, factory=None):
    """Only this function lazily imports MLflow. Tests inject a strict fake SDK."""
    if factory is None:
        import mlflow
        require(mlflow.__version__ == R.VERSION, 'READBACK_VERSION_INVALID')
        from mlflow import MlflowClient
        factory = MlflowClient
    uri = 'sqlite:///' + str(checked['clone_db'])
    client = factory(tracking_uri=uri, registry_uri=uri, workspace_store_uri=uri)
    exps = client.search_experiments(view_type=3, max_results=len(expected['experiments']) + 1)
    require(getattr(exps, 'token', None) is None and len(exps) == len(expected['experiments'])
            and {e.experiment_id for e in exps} == set(expected['experiments']), 'READBACK_EXPERIMENT_MISMATCH')
    found = client.search_runs(list(expected['experiments']), run_view_type=3, max_results=MAX_RUNS + 1)
    require(getattr(found, 'token', None) is None and len(found) == len(expected['runs'])
            and {r.info.run_id for r in found} == set(expected['runs']), 'READBACK_RUN_MISMATCH')
    for key, value in expected['experiments'].items():
        e = client.get_experiment(key)
        actual = {k: getattr(e, k) for k in value}
        uri_path(actual['artifact_location'], checked['clone_artifacts'])
        require(actual == value, 'READBACK_EXPERIMENT_MISMATCH')
    for key, expected_run in expected['runs'].items():
        run = client.get_run(key)
        require({k: getattr(run.info, k) for k in expected_run['info']} == expected_run['info'], 'READBACK_RUN_MISMATCH')
        root = uri_path(run.info.artifact_uri, checked['clone_artifacts'])
        require(run.data.params == expected_run['params'] and run.data.tags == expected_run['tags']
                and {k: number(v) for k, v in run.data.metrics.items()} == expected_run['metrics'], 'READBACK_DATA_MISMATCH')
        for metric_key, points in expected_run['histories'].items():
            actual = client.get_metric_history(key, metric_key)
            require(len(actual) == len(points) and all(type(m.timestamp) is int and type(m.step) is int for m in actual)
                    and sorted([m.key, m.timestamp, m.step, number(m.value)] for m in actual) == points,
                    'READBACK_HISTORY_MISMATCH')
        actual_files, pending = {}, [None]
        while pending:
            prefix = pending.pop()
            for item in client.list_artifacts(key, prefix):
                relative = R.relative(item.path)
                require(relative not in actual_files and len(actual_files) < R.MAX_ENTRIES
                        and type(item.is_dir) is bool and (Path(relative).parent.as_posix() if '/' in relative else None) == prefix,
                        'READBACK_ARTIFACT_REFUSED')
                require(relative in expected_run['artifacts']
                        and item.is_dir == expected_run['artifacts'][relative]['is_dir'], 'READBACK_ARTIFACT_MISMATCH')
                if item.is_dir:
                    actual_files[relative] = {'is_dir': True, 'size': None, 'sha256': None}
                    pending.append(relative)
                else:
                    # In 3.16.1 a nonempty path routes through runs:/ and makes
                    # a temporary copy. Resolve the local run root directly.
                    destination = client.download_artifacts(key, '', dst_path=None)
                    require(type(destination) is str and Path(destination) == root, 'READBACK_ARTIFACT_REFUSED')
                    path = uri_path((Path(destination) / relative).as_uri(), checked['clone_artifacts'], expected=root / relative)
                    raw = R.read(path)
                    require(type(item.file_size) is int and item.file_size == len(raw), 'READBACK_ARTIFACT_MISMATCH')
                    actual_files[relative] = {'is_dir': False, 'size': len(raw), 'sha256': R.digest(raw)}
        require(actual_files == expected_run['artifacts'], 'READBACK_ARTIFACT_MISMATCH')
    return {'schema_version': 1, 'result': 'passed', 'restore_token': checked['root'].name,
            'counts': counts(expected), 'sdk_view_sha256': R.digest(R.canonical(expected))}


def worker(evidence):
    evidence = Path(evidence)
    R.parents(ROOT, evidence); R.private(evidence, True)
    require(evidence.parent.name == 'readback' and R.TOKEN.fullmatch(evidence.name), 'READBACK_PATH_INVALID')
    token = evidence.parent.parent.name
    require(evidence.parent.parent == ROOT / R.DESTINATION / token, 'READBACK_PATH_INVALID')
    checked = R.inspect_completed(token)
    intent = R.parse(R.read(evidence / 'intent.json'))
    require(intent == {'schema_version': 1, 'restore_token': token, 'readback_token': evidence.name,
                       'logical_sha256': logical_digest(checked['expected_snapshot'])}, 'READBACK_INTENT_INVALID')
    # On macOS stdlib queries `uname -p` and `file` for platform metadata.
    # Cache that local metadata
    # before the SDK guard; SDK imports may read it, but cannot spawn commands.
    platform.platform()
    sys.addaudithook(audit_guard(checked['root'] / 'clone', evidence))
    expected = expected_view(checked)
    result = sdk_read(checked, expected)
    R.write(evidence / 'worker-result.json', R.canonical(result))


def launch(evidence, checked):
    code = "import runpy,sys; runpy.run_path(sys.argv[1])['worker'](sys.argv[2])"
    with (evidence / 'worker.log').open('xb') as log:
        os.fchmod(log.fileno(), 0o600)
        R.write(evidence / 'worker-started.json', R.canonical({'schema_version': 1, 'spawn_attempted': True}))
        process = subprocess.Popen([str(PYTHON), '-I', '-B', '-c', code, str(Path(__file__).resolve()), str(evidence)],
            cwd=evidence, env=environment(evidence, checked['clone_db'], checked['clone_artifacts']),
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, umask=0o077)
        original, reaped = None, False
        try:
            process.wait(timeout=120)
            reaped = True
        except BaseException as error:
            original = error
        finally:
            if not reaped:
                try:
                    process.kill()  # this disposable SDK child only, never a process group/server
                except ProcessLookupError:
                    pass
                except BaseException as error:
                    original = keep(original, error)
                for _ in range(2):
                    try:
                        process.wait(timeout=5)
                        reaped = True
                        break
                    except BaseException as error:
                        original = keep(original, error)
            if reaped:
                try:
                    R.write(evidence / 'worker-exited.json', R.canonical({
                        'schema_version': 1, 'pid': process.pid, 'exit_code': process.returncode}))
                except BaseException as error:
                    original = keep(original, error)
            if original is not None:
                raise original
            require(reaped, 'READBACK_WORKER_NOT_REAPED')
    require((evidence / 'worker.log').stat().st_size <= MAX_LOG, 'READBACK_LOG_LIMIT')
    require(process.returncode == 0, 'READBACK_WORKER_FAILED')


def readback(token, *, root=ROOT, runner=launch):
    require(type(token) is str and R.TOKEN.fullmatch(token), 'READBACK_TOKEN_INVALID')
    checked = R.inspect_completed(token, root=root)
    before_seal = seal(checked)
    expected = expected_view(checked)
    logical = logical_digest(checked['expected_snapshot'])
    parent = checked['root'] / 'readback'
    if not parent.exists():
        parent.mkdir(mode=0o700)
    R.parents(Path(root), parent); R.private(parent, True)
    evidence = parent / uuid.uuid4().hex
    evidence.mkdir(mode=0o700)
    for name in ('home', 'home/config', 'home/cache', 'home/data', 'tmp'):
        (evidence / name).mkdir(mode=0o700)
    result = {'schema_version': 1, 'result': 'pending', 'restore_token': token, 'readback_token': evidence.name,
              'counts': counts(expected), 'sdk_view_sha256': None, 'logical_unchanged': False,
              'retained_unchanged': False, 'error_code': None}
    original = None
    try:
        R.write(evidence / 'intent.json', R.canonical({'schema_version': 1, 'restore_token': token,
            'readback_token': evidence.name, 'logical_sha256': logical}))
        runner(evidence, checked)  # returns/raises only after the child has exited and been reaped
        observed = R.parse(R.read(evidence / 'worker-result.json'))
        require(R.canonical(observed) == R.canonical({'schema_version': 1, 'result': 'passed', 'restore_token': token,
            'counts': counts(expected), 'sdk_view_sha256': R.digest(R.canonical(expected))}), 'READBACK_WORKER_MISMATCH')
        result['sdk_view_sha256'] = observed['sdk_view_sha256']
    except BaseException as error:
        original = error
    finally:
        try:
            # A failed cleanup must not let immutable SQLite reads overlook a live writer.
            require(not (evidence / 'worker-started.json').exists()
                    or (evidence / 'worker-exited.json').exists(), 'READBACK_WORKER_NOT_REAPED')
            result['logical_unchanged'] = logical_digest(R.logical_snapshot(checked['clone_db'])) == logical
            result['retained_unchanged'] = seal(checked) == before_seal
            require(result['logical_unchanged'] and result['retained_unchanged'], 'READBACK_RETENTION_CHANGED')
        except BaseException as error:
            original = keep(original, error)
        result.update(result='passed' if original is None else 'failed' if isinstance(original, Exception) else 'interrupted',
                      error_code=None if original is None else 'READBACK_FAILED' if isinstance(original, Exception) else 'INTERRUPTED')
        try:
            R.write(evidence / 'result.json', R.canonical(result))
            R.sync(evidence)
        except BaseException as error:
            original = keep(original, error)
    if original is not None:
        if not isinstance(original, Exception):
            raise original
        raise ReadbackError('READBACK_FAILED') from None
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restore-token', required=True)
    args = parser.parse_args(argv)
    try:
        with signals():
            result = readback(args.restore_token)
        print(R.canonical({'result': 'passed', 'restore_token': args.restore_token,
              'counts': result['counts'], 'evidence': R.DESTINATION + '/' + args.restore_token
                  + '/readback/' + result['readback_token'] + '/result.json'}).decode())
        return 0
    except BaseException as error:
        interrupted = not isinstance(error, Exception)
        print(R.canonical({'result': 'interrupted' if interrupted else 'failed',
                          'error_code': 'INTERRUPTED' if interrupted else 'READBACK_FAILED'}).decode())
        return 128 + error.signum if isinstance(error, ReadbackInterrupted) else 130 if interrupted else 1


if __name__ == '__main__':
    raise SystemExit(main())
