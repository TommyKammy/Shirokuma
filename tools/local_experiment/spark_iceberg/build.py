"""Build a fixed offline classpath image; no image pull, publication or workload launch."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.spark import contract as BASE, prepare as P
from tools.local_experiment.spark_iceberg import contract as C, prepare as J
from tools.local_experiment.trino_opa.common import ExperimentError, canonical, directory, read, require, run_lock, write
from tools.local_experiment import local_session as SESSION

STATE = ROOT / 'tmp/local-experiment/spark-iceberg-builds'
REPOSITORY = 'shirokuma.local/spark-iceberg-classpath'
COMPONENT = 'spark-iceberg-classpath'
SHA = re.compile(r'sha256:[0-9a-f]{64}')


def digest(raw):
    return hashlib.sha256(raw if isinstance(raw, bytes) else canonical(raw).encode()).hexdigest()


def private(path):
    directory(path)
    require(stat.S_IMODE(path.stat().st_mode) == 0o700, 'NONPRIVATE_DIRECTORY')


def file_bytes(path, *, limit):
    private(path.parent)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid() and before.st_nlink == 1
            and stat.S_IMODE(before.st_mode) == 0o600 and before.st_size <= limit, 'UNSAFE_BUILD_INPUT')
    data = read(path, limit=limit)
    after = path.lstat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_mode) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode), 'BUILD_INPUT_CHANGED')
    return data


def put(path, raw):
    private(path.parent)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def persist(output, value):
    write(output / 'summary.json', value)
    fd = os.open(output, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sources():
    return {str(path.relative_to(ROOT)): digest(path.read_bytes())
            for path in (Path(__file__), Path(C.__file__), Path(J.__file__))}


def inputs(prepare_token):
    BASE.validate_token(prepare_token)
    artifacts = J.verified_artifacts(prepare_token)
    require(len(artifacts) == 2 and {Path(row['path']).name for row in artifacts} == set(C.JARS), 'JAR_INVENTORY_MISMATCH')
    contents, projected = {}, []
    for row in artifacts:
        name = Path(row['path']).name
        path = J.STATE / prepare_token / name
        require(str(path) == row['path'], 'JAR_PATH_MISMATCH')
        raw = file_bytes(path, limit=row['size'])
        require(len(raw) == row['size'] and digest(raw) == row['sha256']
                and hashlib.sha512(raw).hexdigest() == row['sha512'], 'JAR_HASH_MISMATCH')
        contents[name] = raw
        projected.append({key: row[key] for key in ('artifact', 'size', 'sha256', 'sha512')} | {'filename': name})
    projected.sort(key=lambda row: row['filename'])
    script = C.job_script().encode('utf-8')
    require(0 < len(script) <= 64 * 1024, 'SCRIPT_SIZE_INVALID')
    source_hashes = sources()
    labels = {'shirokuma.io.component': COMPONENT, 'shirokuma.io.script-sha256': digest(script),
              'shirokuma.io.artifacts-sha256': digest(projected), 'shirokuma.io.sources-sha256': digest(source_hashes)}
    dockerfile = ('FROM ' + BASE.IMAGE + '\nUSER 185:185\n' +
                  ''.join('LABEL ' + key + '=' + json.dumps(value) + '\n' for key, value in sorted(labels.items())) +
                  'COPY --chown=185:185 rootfs/ /\n').encode()
    projection = {'base_image': BASE.IMAGE, 'artifacts': projected, 'script_sha256': digest(script),
                  'dockerfile_sha256': digest(dockerfile), 'source_hashes': source_hashes, 'labels': labels}
    projection['input_sha256'] = digest(projection)
    projection['tag'] = REPOSITORY + ':' + projection['input_sha256']
    return projection, contents, script, dockerfile


def cached_base(command):
    inspected = json.loads(command(P.docker('image', 'inspect', BASE.IMAGE)))
    cri = json.loads(command(P.ssh('sudo', 'k3s', 'crictl', '--timeout', '10s', 'inspecti', '--output', 'json', BASE.IMAGE)))
    verified = P.validate_cache(inspected, cri, P.cache_content(run=command))
    return inspected[0], verified


def inspect(reference, command):
    rows = json.loads(command(P.docker('image', 'inspect', reference)))
    require(type(rows) is list and len(rows) == 1, 'IMAGE_INSPECT_INVALID')
    return rows[0]


def context_files(context):
    private(context)
    result = {}
    for path in sorted(context.rglob('*')):
        require(not path.is_symlink(), 'CONTEXT_SYMLINK')
        if path.is_dir():
            private(path)
        else:
            result[str(path.relative_to(context))] = digest(file_bytes(path, limit=256*1024**2))
    return result


def expected_context(recipe):
    return {'Dockerfile': recipe['dockerfile_sha256'],
            'rootfs/opt/shirokuma/check.py': recipe['script_sha256'],
            **{'rootfs/opt/spark/jars/' + row['filename']: row['sha256'] for row in recipe['artifacts']}}


def verify_image(image, expected, base, base_proof):
    identity = image.get('Id')
    require(type(identity) is str and SHA.fullmatch(identity), 'IMAGE_ID_INVALID')
    ref = REPOSITORY + '@' + identity
    require(image.get('Architecture') == 'arm64' and image.get('Os') == 'linux'
            and expected['tag'] in image.get('RepoTags', []) and ref in image.get('RepoDigests', [])
            and image.get('Descriptor', {}).get('digest') == identity, 'IMAGE_IDENTITY_MISMATCH')
    config, original = image.get('Config', {}), base['Config']
    wanted = dict(original, User='185:185', Labels={**(original.get('Labels') or {}), **expected['labels']})
    # Docker can represent absent OCI fields as explicit nulls.
    clean = lambda value: {key: item for key, item in value.items() if item is not None}
    require(clean(config) == clean(wanted), 'IMAGE_CONFIG_MISMATCH')
    layers = image.get('RootFS', {}).get('Layers', [])
    require(len(layers) == P.LAYERS + 1 and layers[:P.LAYERS] == base_proof['rootfs_diff_ids']
            and all(type(layer) is str and SHA.fullmatch(layer) for layer in layers), 'IMAGE_LAYERS_MISMATCH')
    require(type(image.get('Size')) is int and base['Size'] < image['Size'] < base['Size'] + 512 * 1024**2, 'IMAGE_SIZE_INVALID')
    return {'image': ref, 'image_id': identity, 'image_bytes': image['Size'], 'rootfs_diff_ids': layers,
            'architecture': 'arm64', 'os': 'linux', 'user': '185:185'}


def build(prepare_token, *, command=P.command):
    BASE.validate_token(prepare_token)
    directory(STATE, create=True); private(STATE)
    build_token = uuid.uuid4().hex
    output = STATE / build_token
    output.mkdir(mode=0o700)
    receipt = {'schema_version': 1, 'build_token': build_token, 'prepare_token': prepare_token,
               'result': 'running', 'pending': None, 'reused': False, 'runtime_started': False,
               'evidence': str(output.relative_to(ROOT))}
    persist(output, receipt)
    try:
        with run_lock(STATE):
            recipe, jars, script, dockerfile = inputs(prepare_token)
            receipt['inputs'] = recipe
            receipt['runtime'] = P.guard(run=command)
            base, proof = cached_base(command)
            receipt['base'] = proof
            context = output / 'context'
            context.mkdir(mode=0o700)
            rootfs = context / 'rootfs'
            for path in (rootfs, rootfs/'opt', rootfs/'opt/spark', rootfs/'opt/spark/jars', rootfs/'opt/shirokuma'):
                path.mkdir(mode=0o700)
            put(context/'Dockerfile', dockerfile)
            put(rootfs/'opt/shirokuma/check.py', script)
            for name, raw in jars.items():
                put(rootfs/'opt/spark/jars'/name, raw)
            receipt['context'] = {'relative_path': str(context.relative_to(output)), 'uid': context.stat().st_uid,
                                  'files': context_files(context)}
            require(receipt['context']['files'] == expected_context(recipe), 'CONTEXT_CONTENT_CHANGED')
            persist(output, receipt)
            listed = command(P.docker('image', 'ls', '--no-trunc', '--filter', 'reference='+recipe['tag'], '--format', '{{.ID}}')).splitlines()
            require(len(listed) <= 1 and all(SHA.fullmatch(value.strip()) for value in listed), 'IMAGE_TAG_AMBIGUOUS')
            if listed:
                image = inspect(recipe['tag'], command)
                require(image['Id'] == listed[0].strip(), 'IMAGE_TAG_CHANGED')
                receipt['reused'] = True
            else:
                require(P.guard(run=command) == receipt['runtime'], 'RUNTIME_CHANGED')
                receipt['pending'] = {'operation': 'build', 'tag': recipe['tag']}
                persist(output, receipt)
                require(context_files(context) == expected_context(recipe), 'CONTEXT_CONTENT_CHANGED')
                command(P.docker('build', '--pull=false', '--network=none', '--platform', 'linux/arm64',
                                 '--tag', recipe['tag'], str(context)), timeout=600, log_path=output/'build-log.json')
                image = inspect(recipe['tag'], command)
            receipt.update(verify_image(image, recipe, base, proof))
            require(context_files(context) == expected_context(recipe), 'CONTEXT_CONTENT_CHANGED')
            require(inputs(prepare_token)[0] == recipe, 'BUILD_INPUTS_CHANGED')
            require(P.guard(run=command) == receipt['runtime'], 'RUNTIME_CHANGED')
            receipt.update(result='passed', pending=None)
            persist(output, receipt)
            return receipt
    except BaseException as error:
        receipt.update(result='failed', error_type=type(error).__name__)
        try:
            persist(output, receipt)
        finally:
            raise error


def read_verified_image(build_token, *, command=P.command):
    BASE.validate_token(build_token)
    output = STATE / build_token
    private(output)
    receipt = json.loads(file_bytes(output/'summary.json', limit=2*1024**2))
    require(receipt.get('schema_version') == 1 and receipt.get('build_token') == build_token
            and receipt.get('result') == 'passed' and receipt.get('pending') is None, 'BUILD_NOT_PASSED')
    with run_lock(STATE):
        recipe = inputs(receipt['prepare_token'])[0]
        require(recipe == receipt['inputs'], 'BUILD_INPUTS_CHANGED')
        require(P.guard(run=command) == receipt['runtime'], 'RUNTIME_CHANGED')
        base, proof = cached_base(command)
        require(proof == receipt['base'], 'BASE_CHANGED')
        checked = verify_image(inspect(recipe['tag'], command), recipe, base, proof)
        require(all(receipt.get(key) == value for key, value in checked.items()), 'BUILD_IMAGE_CHANGED')
        context = output/'context'
        require(receipt['context']['relative_path'] == 'context' and receipt['context']['uid'] == os.getuid(), 'CONTEXT_IDENTITY_CHANGED')
        actual = context_files(context)
        require(actual == receipt['context']['files'] == expected_context(recipe), 'CONTEXT_CONTENT_CHANGED')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-token', required=True)
    args = parser.parse_args(argv)
    try:
        with SESSION.interruption_handlers():
            result = build(args.prepare_token)
        print(json.dumps({key: result[key] for key in ('result', 'build_token', 'image', 'reused', 'evidence')}))
        return 0
    except BaseException as error:
        print(json.dumps({'result': 'failed', 'error_type': type(error).__name__}))
        return 128 + error.signum if isinstance(error, SESSION.SessionInterrupted) else 1


if __name__ == '__main__':
    raise SystemExit(main())
