#!/usr/bin/env python3
"""Convert the one registered Notebook into two fixed, read-only Dagster assets.

Generated Python is an inspectable artifact, never dynamically imported or
executed by this runner. The second asset requires the first asset's successful
receipt to preserve Notebook execution order; it does not claim that saved
evaluations were derived from the fixture. No scheduler, kernel or retry loop.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.notebook import client as N

STATE = ROOT / 'tmp/local-experiment/notebook-assets'
TEMPLATE = ROOT / 'examples/local_lakehouse.ipynb'
TEMPLATE_SHA256 = '186aa3b75ef02c1efd963f1df02ed2797c5dd77a07a0856dbcc7b603d8fd3ef4'
CODE_CELLS = (
    'from IPython.display import JSON, display\nfrom tools.local_experiment.notebook.client import read_fixture, read_evaluations',
    'fixture = read_fixture()\ndisplay(JSON(fixture))',
    'evaluations = read_evaluations()\ndisplay(JSON(evaluations))',
)
ASSETS = ('fixture_preview', 'saved_evaluation_summary')
PROFILE = 'fixed-local-lakehouse-notebook-assets-v1'
DEFINITIONS = b'''"""Generated fixed definitions; run through the owned notebook-assets runner."""
from tools.local_experiment.notebook_assets.run import definitions

defs = definitions()
'''


class AssetError(ValueError):
    """Fixed code only; Reader/MLflow bodies and rows are never exception text."""


class AssetsInterrupted(BaseException):
    def __init__(self, signum):
        super().__init__('NOTEBOOK_ASSETS_INTERRUPTED')
        self.signum = signum


def require(condition, code):
    if not condition:
        raise AssetError(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _keep(first, later):
    return later if first is None or (isinstance(first, Exception) and not isinstance(later, Exception)) else first


@contextmanager
def _signals():
    previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    def stop(signum, _frame):
        raise AssetsInterrupted(signum)
    try:
        for s in previous:
            signal.signal(s, stop)
        yield
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)


def _private_directory(path):
    require(path.is_absolute() and all(not item.is_symlink() for item in (path, *path.parents)),
            'NOTEBOOK_ASSETS_DIRECTORY_INVALID')
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700
            and info.st_uid == os.getuid(), 'NOTEBOOK_ASSETS_DIRECTORY_INVALID')


def _template_manifest():
    require(not TEMPLATE.is_symlink() and TEMPLATE.stat().st_size <= 65536, 'NOTEBOOK_TEMPLATE_INVALID')
    raw = TEMPLATE.read_bytes()
    require(digest(raw) == TEMPLATE_SHA256, 'NOTEBOOK_TEMPLATE_CHANGED')
    notebook = N._parse(raw)
    cells = [cell for cell in notebook['cells'] if cell['cell_type'] == 'code']
    require(tuple(''.join(cell['source']) for cell in cells) == CODE_CELLS
            and all(cell['outputs'] == [] and cell['execution_count'] is None for cell in cells),
            'NOTEBOOK_CODE_CONTRACT_INVALID')
    return dict(schema_version=1, profile=PROFILE, template='examples/local_lakehouse.ipynb',
                template_sha256=TEMPLATE_SHA256, code_cell_sha256=[digest(cell.encode()) for cell in CODE_CELLS],
                client_sha256=digest(Path(N.__file__).read_bytes()), definitions_sha256=digest(DEFINITIONS),
                converter_sha256=digest(Path(__file__).read_bytes()),
                assets=[dict(key=ASSETS[0], read='read_fixture', depends_on=[], max_calls=1, max_retries=0),
                        dict(key=ASSETS[1], read='read_evaluations', depends_on=[ASSETS[0]], max_calls=1, max_retries=0)],
                dependency_semantics='execution-order; evaluations are not derived from fixture rows')


def export(output: Path):
    """Create a fresh 0700 directory, fixed 0600 defs.py and conversion manifest."""
    manifest = _template_manifest()
    output = Path(output)
    require(output.is_absolute() and all(not p.is_symlink() for p in output.parents),
            'NOTEBOOK_ASSETS_DIRECTORY_INVALID')
    output.mkdir(mode=0o700)
    _private_directory(output)
    fd = os.open(output / 'defs.py', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(DEFINITIONS)
        stream.flush()
        os.fsync(stream.fileno())
    N._write(output / 'manifest.json', manifest)
    return manifest


def verify_export(output):
    _private_directory(output)
    expected = _template_manifest()
    require(N._read(output / 'defs.py', 4096) == DEFINITIONS
            and N._parse(N._read(output / 'manifest.json', 16384)) == expected,
            'NOTEBOOK_EXPORT_CHANGED')
    return expected


def reader_closed(output):
    """No API: require both the client status and existing Reader public proof."""
    intent = output / 'reader-intent.json'
    if not intent.exists() and not intent.is_symlink():
        return not any((output / name).exists() or (output / name).is_symlink()
                       for name in ('reader-status.json', 'reader.stdout.jsonl'))
    try:
        status = N._parse(N._read(output / 'reader-status.json', 16384))
        require(type(status) is dict and set(status) == {'schema_version', 'state', 'pid', 'pgid',
                'process_start', 'evidence_token', 'exit_code', 'child_exited', 'cleanup_verified', 'error_code'}
                and type(status['schema_version']) is int and status['schema_version'] == 1
                and status['state'] in ('passed','failed','interrupted')
                and type(status['pid']) is int and status['pid'] > 0 and type(status['pgid']) is int
                and status['pgid'] == status['pid']
                and (status['process_start'] is None or (type(status['process_start']) is str
                     and 1 <= len(status['process_start']) <= 64 and '\n' not in status['process_start']))
                and status['error_code'] == {'passed':None,'failed':'NOTEBOOK_READER_FAILED',
                                             'interrupted':'NOTEBOOK_INTERRUPTED'}[status['state']]
                and status['child_exited'] is True and status['cleanup_verified'] is True
                and type(status['exit_code']) is int and status['exit_code'] in (0,1,130,143)
                and (status['state'] != 'passed' or status['exit_code'] == 0), 'NOTEBOOK_READER_UNVERIFIED')
        token, _, cleanup = N._reader_frames(N._read(output / 'reader.stdout.jsonl', N.C.MAX_PUBLIC_BYTES*2+2),
                                             status['exit_code'])
        return cleanup is True and token == status['evidence_token']
    except Exception:
        return False


def _state():
    return {'started': set(), 'metadata': {}, 'first_error': None}


def build_assets(state, api=None, client=N):
    """Construct trusted host definitions. api/client injection is only for tests."""
    if api is None:
        import dagster as api
    def call(name, function):
        require(name not in state['started'], 'NOTEBOOK_ASSET_RETRY_REFUSED')
        if name == ASSETS[1]:
            require(ASSETS[0] in state['metadata'], 'NOTEBOOK_ASSET_ORDER_INVALID')
        state['started'].add(name)
        try:
            with _signals():
                try:
                    return function()
                except BaseException as error:
                    state['first_error'] = _keep(state['first_error'], error)
                    raise
        except BaseException as error:
            state['first_error'] = _keep(state['first_error'], error)
            raise state['first_error']

    @api.asset(name=ASSETS[0], retry_policy=api.RetryPolicy(max_retries=0))
    def fixture_preview():
        public = call(ASSETS[0], client.read_fixture)
        N.C.receipt_projection(public)
        metadata = dict(row_count=public['row_count'], rows_sha256=public['rows_sha256'],
                        snapshot_id=public['snapshot_id'], artifact='reader.stdout.jsonl',
                        template_sha256=TEMPLATE_SHA256)
        state['metadata'][ASSETS[0]] = metadata
        return api.MaterializeResult(metadata=metadata)

    @api.asset(name=ASSETS[1], deps=[ASSETS[0]], retry_policy=api.RetryPolicy(max_retries=0))
    def saved_evaluation_summary():
        public = call(ASSETS[1], client.read_evaluations)
        metadata = dict(run_count=public['count'], evaluations_sha256=digest(N._canonical(public)),
                        artifact='evaluations.json', cost=api.MetadataValue.null(),
                        template_sha256=TEMPLATE_SHA256)
        state['metadata'][ASSETS[1]] = {**metadata, 'cost': None}
        return api.MaterializeResult(metadata=metadata)
    return [fixture_preview, saved_evaluation_summary]


def definitions():
    """Public generated-definitions entrypoint; constructing it executes no reads."""
    import dagster
    return dagster.Definitions(assets=build_assets(_state(), dagster))


def execute(output: Path, binary: Path, *, materializer=None, instance_factory=None, api=None, client=N):
    """Materialize once in process, with a fresh private instance and retry zero.

    The client owns the Go process and its 240/245-second execution/cleanup
    bounds. Cancellation is delivered inside the synchronous client call, so
    Dagster cannot advance or close the instance before that call unwinds.
    Keep an external interruption grace of at least 260 seconds; never kill
    the owner to force an unknown Reader to look complete.
    """
    output, binary = Path(output), Path(binary)
    manifest = verify_export(output)
    require(binary.is_absolute() and binary.is_file() and not binary.is_symlink(), 'NOTEBOOK_BINARY_INVALID')
    N._write(output / 'execution-intent.json', dict(schema_version=1, profile=PROFILE,
                manifest_sha256=digest(N._canonical(manifest)), binary_sha256=digest(binary.read_bytes())))
    if api is None:
        import dagster as api
    materializer = materializer or api.materialize
    instance_factory = instance_factory or api.DagsterInstance.local_temp
    state, original, run_id = _state(), None, None
    result = dict(schema_version=1, profile=PROFILE, result='failed', dagster_run_id=None,
                  assets=[], reader_cleanup_confirmed=False, error_code=None)
    env = {'SHIROKUMA_NOTEBOOK_RUN_DIR':str(output), 'SHIROKUMA_NOTEBOOK_BINARY':str(binary)}
    previous = {name:os.environ.get(name) for name in env}
    os.environ.update(env)
    try:
        assets = build_assets(state, api, client)
        (output / 'dagster').mkdir(mode=0o700)
        with instance_factory(str(output / 'dagster'), overrides={'telemetry':{'enabled':False}}) as instance:
            try:
                outcome = materializer(assets, instance=instance, raise_on_error=True)
                run_id = str(uuid.UUID(outcome.run_id))
                events = outcome.get_asset_materialization_events()
                event_keys = [tuple(event.asset_key.path) for event in events]
                require(outcome.success is True and sorted(event_keys) == [(key,) for key in ASSETS]
                        and set(state['metadata']) == set(ASSETS) and state['first_error'] is None,
                        'NOTEBOOK_MATERIALIZATION_INCOMPLETE')
            except BaseException as error:
                original = _keep(state['first_error'], error)
                raise original
        verify_export(output)
        require(reader_closed(output), 'NOTEBOOK_READER_UNVERIFIED')
    except BaseException as error:
        original = _keep(original, error)
    finally:
        original = _keep(state['first_error'], original) if original is not None else state['first_error']
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        try:
            closed = reader_closed(output)
        except BaseException as error:
            original = _keep(original, error)
            closed = False
        result.update(dagster_run_id=run_id, assets=[dict(key=key, metadata=value) for key,value in state['metadata'].items()],
                      reader_cleanup_confirmed=closed,
                      result='passed' if original is None and closed else 'unknown' if not closed else
                             'interrupted' if not isinstance(original, Exception) else 'failed',
                      error_code=None if original is None else 'INTERRUPTED' if not isinstance(original, Exception)
                                 else 'NOTEBOOK_ASSETS_FAILED')
        try:
            N._write(output / 'result.json', result)
        except BaseException as error:
            original = _keep(original, error)
    if original is not None:
        if not isinstance(original, Exception):
            raise original
        raise AssetError('NOTEBOOK_ASSETS_FAILED') from None
    return result


def main(argv=None):
    if (sys.argv[1:] if argv is None else argv):
        print(json.dumps({'result':'failed','error_code':'ARGUMENTS_INVALID'}))
        return 1
    os.umask(0o077)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    output = STATE / uuid.uuid4().hex
    business, original = None, None
    try:
        with _signals():
            export(output)
            binary = output / 'shirokuma'
            with (output / 'build.log').open('xb') as log:
                subprocess.run(['go','build','-o',str(binary),'./cmd/shirokuma'], cwd=ROOT,
                               stdout=log, stderr=log, check=True, timeout=60)
            business = execute(output, binary)
    except BaseException as error:
        original = error
        try:
            business = N._parse(N._read(output / 'result.json',65536))
        except BaseException as secondary:
            original = _keep(original, secondary)
    try:
        closed = reader_closed(output)
    except BaseException as error:
        original, closed = _keep(original, error), False
    if not closed and original is None:
        original = AssetError('NOTEBOOK_READER_UNVERIFIED')
    owner = dict(schema_version=1, profile=PROFILE,
                 result=business['result'] if original is None and business is not None else
                        'unknown' if not closed else 'interrupted' if not isinstance(original,Exception) else 'failed',
                 materialization_result=business['result'] if business is not None else None,
                 reader_cleanup_confirmed=closed,
                 error_code=None if original is None else 'INTERRUPTED' if not isinstance(original,Exception)
                            else 'NOTEBOOK_ASSETS_FAILED')
    saved = False
    try:
        N._write(output / 'owner-result.json', owner)
        saved = True
    except BaseException as error:
        original = _keep(original, error)
        owner.update(result='unknown' if not closed else 'interrupted' if not isinstance(original,Exception) else 'failed',
                     error_code='INTERRUPTED' if not isinstance(original,Exception) else 'NOTEBOOK_ASSETS_FAILED')
    code = 0 if original is None and owner['result'] == 'passed' else \
           128 + original.signum if isinstance(original,AssetsInterrupted) else \
           130 if original is not None and not isinstance(original,Exception) else 1
    print(json.dumps(dict(result=owner['result'], materialization_result=owner['materialization_result'],
                         error_code=owner['error_code'], reader_cleanup_confirmed=closed,
                         evidence=f'tmp/local-experiment/notebook-assets/{output.name}/owner-result.json' if saved else None)))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
