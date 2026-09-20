#!/usr/bin/env python3
"""Registered saved-training Notebook to two fixed, retry-zero Dagster assets."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.local_experiment.notebook_assets import run as B
from tools.local_experiment.notebook import client as N
from tools.local_experiment.mlflow import autolog as A

STATE = ROOT/'tmp/local-experiment/notebook-training-assets'
PROFILE = 'notebook-autolog-assets-v1'
TEMPLATE_PATH = 'examples/local_autolog_assets.ipynb'
TEMPLATE = ROOT/TEMPLATE_PATH
TEMPLATE_SHA256 = 'eb64240f49223198b5b38d4fce61c485de3267dd43c803ce0678b1fdfafef6da'
ASSETS = ('saved_training_source', 'ridge_autolog')
CODE_CELLS = (
    'from IPython.display import JSON, display\nfrom tools.local_experiment.notebook_assets.training import preview_source, materialize_training\n\ntraining_token = ""\nbackup_manifest = None\nrun_training = False',
    'source = None\nif training_token:\n    source = preview_source(training_token)\n    display(JSON(source))',
    'result = None\nif run_training:\n    result = materialize_training(training_token, backup_manifest)\n    display(JSON(result))',
)
ERROR_CODES = {'TRAINING_REQUEST_INVALID', 'TRAINING_TEMPLATE_CHANGED', 'TRAINING_EXPORT_CHANGED',
               'TRAINING_SOURCE_CHANGED', 'TRAINING_ASSET_ORDER_INVALID', 'TRAINING_ASSET_RETRY_REFUSED',
               'TRAINING_MATERIALIZATION_INCOMPLETE', 'TRAINING_WORKER_UNVERIFIED',
               'TRAINING_ASSETS_FAILED', 'TRAINING_ASSETS_INTERRUPTED'}
canonical, digest = A.C.canonical, A.C.digest


class TrainingError(ValueError):
    pass


def require(condition, code='TRAINING_ASSETS_FAILED'):
    if not condition:
        raise TrainingError(code)


def _worker():
    from tools.local_experiment.notebook_assets import autolog_worker
    return autolog_worker


def normalize_request(value):
    code = 'TRAINING_REQUEST_INVALID'
    require(type(value) is dict and set(value) == {'schema_version', 'training_token', 'backup_manifest'}
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and type(value['training_token']) is str
            and re.fullmatch(r'[0-9a-f]{32}', value['training_token']) is not None, code)
    backup = value['backup_manifest']
    if backup is not None:
        require(type(backup) is str and 0 < len(backup.encode('utf-8')) <= 1024
                and all(ord(c) >= 32 for c in backup) and '\\' not in backup, code)
        path = PurePosixPath(backup)
        require(not path.is_absolute() and str(path) == backup
                and path.parts[:2] == ('tmp', 'local-experiment') and len(path.parts) >= 4
                and not any(part in ('.', '..') for part in path.parts)
                and path.name == 'backup-manifest.json', code)
    return dict(schema_version=1, training_token=value['training_token'], backup_manifest=backup)


def request(training_token, backup_manifest=None):
    return normalize_request(dict(schema_version=1, training_token=training_token, backup_manifest=backup_manifest))


def preview_source(training_token):
    """Verified saved source metadata only; no materialization, worker or fit."""
    request(training_token)
    snapshot = A.Snapshot(training_token); first, source = None, None
    try:
        source = snapshot.load()
    except BaseException as error:
        first = error
    try: snapshot.check()
    except BaseException as error: first = B._keep(first, error)
    if first is not None: raise first
    return source


def _definitions(request_sha):
    # Only a SHA is code: all request values stay in private canonical JSON.
    return ('from tools.local_experiment.notebook_assets.training import definitions_from_export\n'
            'defs = definitions_from_export(__file__, "' + request_sha + '")\n').encode()


def _manifest(value):
    value = normalize_request(value)
    require(not TEMPLATE.is_symlink() and TEMPLATE.stat().st_size <= 32768, 'TRAINING_TEMPLATE_CHANGED')
    raw = TEMPLATE.read_bytes()
    require(B.digest(raw) == TEMPLATE_SHA256, 'TRAINING_TEMPLATE_CHANGED')
    notebook = N._parse(raw)
    cells = [cell for cell in notebook['cells'] if cell['cell_type'] == 'code']
    require(tuple(''.join(cell['source']) for cell in cells) == CODE_CELLS
            and all(cell['outputs'] == [] and cell['execution_count'] is None for cell in cells),
            'TRAINING_TEMPLATE_CHANGED')
    modules = [sys.modules[__name__], _worker(), B, N, A, A.C, A.G, A.C.K, A.T, A.R, A.M, A.E]
    sources = {str(Path(module.__file__).relative_to(ROOT)):B.digest(Path(module.__file__).read_bytes())
               for module in modules}
    for environment in ('dataops', 'mlflow'):
        for filename in ('pyproject.toml', 'uv.lock'):
            path = 'tools/local_experiment/'+environment+'/'+filename
            sources[path] = B.digest((ROOT/path).read_bytes())
    return dict(schema_version=1, profile=PROFILE, template=TEMPLATE_PATH, template_sha256=TEMPLATE_SHA256,
        code_cell_sha256=[B.digest(code.encode()) for code in CODE_CELLS], request_sha256=digest(value),
        definitions_sha256=B.digest(_definitions(digest(value))), source_sha256=sources,
        assets=[dict(key=ASSETS[0], depends_on=[], max_calls=1, max_retries=0),
                dict(key=ASSETS[1], depends_on=[ASSETS[0]], max_calls=1, max_retries=0)],
        dependency_semantics='autolog uses the verified saved training source; no lake or catalog lineage claim')


def export(output, value):
    output = Path(output); value = normalize_request(value); manifest = _manifest(value)
    require(output.is_absolute() and all(not p.is_symlink() for p in output.parents))
    output.mkdir(mode=0o700); B._private_directory(output)
    N._write(output/'request.json', value)
    descriptor = os.open(output/'defs.py', os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(_definitions(digest(value))); stream.flush(); os.fsync(stream.fileno())
    N._write(output/'manifest.json', manifest)
    return manifest


def verify_export(output, expected_sha=None):
    output = Path(output); B._private_directory(output)
    raw = N._read(output/'request.json', 4096)
    value = normalize_request(N._parse(raw))
    require(raw == N._canonical(value) and (expected_sha is None or digest(value) == expected_sha),
            'TRAINING_EXPORT_CHANGED')
    manifest = _manifest(value)
    require(N._read(output/'defs.py', 4096) == _definitions(digest(value))
            and N._parse(N._read(output/'manifest.json', 16384)) == manifest, 'TRAINING_EXPORT_CHANGED')
    return value, manifest


def _state():
    return dict(started=set(), metadata={}, first_error=None, snapshot=None, source_sha256=None, worker=None)


def build_assets(output, value, state, *, api=None, worker=None):
    if api is None:
        import dagster as api
    worker = _worker() if worker is None else worker
    value = normalize_request(value); output = Path(output)
    def call(name, function):
        require(name not in state['started'], 'TRAINING_ASSET_RETRY_REFUSED')
        if name == ASSETS[1]:
            require(ASSETS[0] in state['metadata'], 'TRAINING_ASSET_ORDER_INVALID')
        state['started'].add(name)
        try:
            with B._signals():
                try: return function()
                except BaseException as error:
                    state['first_error'] = B._keep(state['first_error'], error); raise state['first_error']
        except BaseException as error:
            state['first_error'] = B._keep(state['first_error'], error); raise state['first_error']

    @api.asset(name=ASSETS[0], retry_policy=api.RetryPolicy(max_retries=0))
    def saved_training_source():
        def read():
            verify_export(output, digest(value))
            snapshot = A.Snapshot(value['training_token']); state['snapshot'] = snapshot
            source = snapshot.load(); snapshot.check()
            state['source_sha256'] = digest(source)
            N._write(output/'source.json', source)
            binding = source['binding']
            metadata = dict(training_token=value['training_token'], source_run_id=binding['run_id'],
                store_id=binding['store_id'], source_sha256=state['source_sha256'],
                request_sha256=digest(value), template_sha256=TEMPLATE_SHA256,
                train_rows=source['train_rows'], holdout_rows=source['holdout_rows'], source='source.json')
            state['metadata'][ASSETS[0]] = metadata
            return api.MaterializeResult(metadata=metadata)
        return call(ASSETS[0], read)

    @api.asset(name=ASSETS[1], deps=[ASSETS[0]], retry_policy=api.RetryPolicy(max_retries=0))
    def ridge_autolog():
        def train():
            verify_export(output, digest(value)); state['snapshot'].check()
            child = output/'autolog'; child.mkdir(mode=0o700)
            result = worker.run(child, value['training_token'], value['backup_manifest'],
                                source_sha256=state['source_sha256'])
            require(type(result) is dict and set(result) == {'schema_version','profile','result',
                'training_token','source_sha256','reused','cleanup_verified','autolog'}
                and type(result['schema_version']) is int and result['schema_version'] == 1
                and result['profile'] == 'owned-autolog-worker-v1' and result['result'] == 'passed'
                and result['training_token'] == value['training_token']
                and result['source_sha256'] == state['source_sha256'] and type(result['reused']) is bool
                and result['cleanup_verified'] is True and worker.closed(child), 'TRAINING_WORKER_UNVERIFIED')
            receipt = A.recorded(value['training_token'])
            require(receipt is not None and canonical(A.public(receipt)) == canonical(result['autolog']),
                    'TRAINING_WORKER_UNVERIFIED')
            state['snapshot'].check()
            state['worker'] = deepcopy(result)
            public = result['autolog']
            metadata = dict(training_token=value['training_token'], source_sha256=state['source_sha256'],
                request_sha256=digest(value), template_sha256=TEMPLATE_SHA256,
                run_id=public['run_id'], experiment_id=public['experiment_id'],
                reused=result['reused'], params=len(public['params']), metrics=len(public['metrics']),
                artifact_sha256=public['artifact']['sha256'], evidence=public['evidence'])
            state['metadata'][ASSETS[1]] = metadata
            return api.MaterializeResult(metadata=metadata)
        return call(ASSETS[1], train)
    return [saved_training_source, ridge_autolog]


def definitions_from_export(definitions_path, expected_sha):
    """Read/validate fixed definitions; constructing them performs no fit/write."""
    path = Path(definitions_path)
    require(path.name == 'defs.py', 'TRAINING_EXPORT_CHANGED')
    value, _ = verify_export(path.parent, expected_sha)
    import dagster as api
    return api.Definitions(assets=build_assets(path.parent, value, _state(), api=api))


def execute(output, *, materializer=None, instance_factory=None, api=None, worker=None):
    output = Path(output); value, manifest = verify_export(output)
    worker = _worker() if worker is None else worker
    N._write(output/'execution-intent.json', dict(schema_version=1, profile=PROFILE,
        manifest_sha256=digest(manifest), request_sha256=digest(value)))
    if api is None:
        import dagster as api
    materializer = materializer or api.materialize
    instance_factory = instance_factory or api.DagsterInstance.local_temp
    state, first, run_id, materialized = _state(), None, None, False
    result = dict(schema_version=1, profile=PROFILE, result='failed', dagster_run_id=None,
        run_token=output.name, evidence='tmp/local-experiment/notebook-training-assets/'+output.name+'/owner-result.json',
        template_sha256=TEMPLATE_SHA256, manifest_sha256=digest(manifest),
        request_sha256=digest(value), training_token=value['training_token'], assets=[],
        worker_cleanup_verified=False, autolog=None, error_code=None)
    try:
        assets = build_assets(output, value, state, api=api, worker=worker)
        (output/'dagster').mkdir(mode=0o700)
        with instance_factory(str(output/'dagster'), overrides={'telemetry':{'enabled':False}}) as instance:
            try:
                outcome = materializer(assets, instance=instance, raise_on_error=True)
                run_id = str(uuid.UUID(outcome.run_id))
                events = [tuple(event.asset_key.path) for event in outcome.get_asset_materialization_events()]
                require(outcome.success is True and sorted(events) == sorted((name,) for name in ASSETS)
                        and set(state['metadata']) == set(ASSETS) and state['first_error'] is None,
                        'TRAINING_MATERIALIZATION_INCOMPLETE')
                materialized = True
                result.update(result='passed', dagster_run_id=run_id, assets=deepcopy(state['metadata']),
                              worker_cleanup_verified=True, autolog=deepcopy(state['worker']))
                # A later instance close failure must not erase this business result.
                N._write(output/'result.json', result)
            except BaseException as error:
                first = B._keep(state['first_error'], error); raise first
    except BaseException as error:
        first = B._keep(first, error)
    finally:
        first = B._keep(state['first_error'], first) if first is not None else state['first_error']
        try:
            verify_export(output, digest(value))
            if state['snapshot'] is not None: state['snapshot'].check()
        except BaseException as error: first = B._keep(first, error)
        try:
            child = output/'autolog'
            closed = not child.exists() or worker.closed(child)
            require(closed, 'TRAINING_WORKER_UNVERIFIED')
        except BaseException as error:
            closed = False; first = B._keep(first, error)
        owner = deepcopy(result)
        owner.update(result='interrupted' if first is not None and not isinstance(first, Exception)
                     else 'failed' if first is not None else 'passed', worker_cleanup_verified=closed,
                     error_code=_code(first) if first is not None else None)
        try:
            if not materialized: N._write(output/'result.json', owner)
            N._write(output/'owner-result.json', owner)
        except BaseException as error: first = B._keep(first, error)
    if first is not None: raise first
    return owner


def _code(error):
    if not isinstance(error, Exception): return 'TRAINING_ASSETS_INTERRUPTED'
    return str(error) if type(error) is TrainingError and str(error) in ERROR_CODES else 'TRAINING_ASSETS_FAILED'


def _materialize(value, publish=None):
    require(all(not p.is_symlink() for p in (STATE, *STATE.parents)))
    STATE.mkdir(mode=0o700, exist_ok=True); B._private_directory(STATE)
    output = STATE/uuid.uuid4().hex
    export(output, value)
    try:
        with B._signals():
            result = execute(output)
            if publish is not None: publish(result)
        return result
    except BaseException as first:
        # A publication/outer signal failure must not advertise a successful owner.
        # The materialized business receipt is deliberately left unchanged.
        try:
            path = output/'owner-result.json'
            if path.exists():
                owner = N._parse(N._read(path, 65536))
                owner.update(result='interrupted' if not isinstance(first, Exception) else 'failed',
                             error_code=_code(first))
                N._write(path, owner, replace=True)
        except BaseException as later: first = B._keep(first, later)
        raise first


def materialize_training(training_token, backup_manifest=None):
    """DataOps environment entrypoint; first registration requires a cold store."""
    return _materialize(request(training_token, backup_manifest))


def main(argv=None, *, stdout=None):
    stdout = sys.stdout if stdout is None else stdout
    args = sys.argv[1:] if argv is None else argv
    try:
        require(type(args) is list and len(args) in (2, 4) and args[0] == '--training-token'
                and (len(args) == 2 or args[2] == '--backup-manifest'), 'TRAINING_REQUEST_INVALID')
        value = request(args[1], args[3] if len(args) == 4 else None)
        def publish(result):
            stdout.write(canonical(result).decode() + '\n'); stdout.flush()
        _materialize(value, publish)
        return 0
    except Exception as error:
        stdout.write(canonical(dict(schema_version=1, profile=PROFILE, result='failed', error_code=_code(error))).decode()+'\n')
        stdout.flush(); return 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, B.AssetsInterrupted):
        raise SystemExit(130) from None
