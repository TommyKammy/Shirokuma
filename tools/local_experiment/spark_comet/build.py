"""Offline Comet layer above the already verified fixed Spark/Iceberg image."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.spark import prepare as P
from tools.local_experiment.spark_iceberg import build as BASE, remote_contract as REMOTE
from tools.local_experiment.spark_comet import contract as C, prepare as J
from tools.local_experiment.trino_opa.common import directory, require, run_lock
from tools.local_experiment import local_session as SESSION

STATE = ROOT / 'tmp/local-experiment/spark-comet-builds'
REPOSITORY = 'shirokuma.local/spark-comet-classpath'
COMPONENT = 'spark-comet-classpath'
SHA = re.compile(r'sha256:[0-9a-f]{64}')
digest, private, file_bytes, put, persist = BASE.digest, BASE.private, BASE.file_bytes, BASE.put, BASE.persist
context_files, expected_context = BASE.context_files, BASE.expected_context


def sources():
    modules = (C, C.I, J, J.P, BASE, BASE.C, BASE.J, P)
    paths = {Path(__file__), *(Path(module.__file__) for module in modules)}
    return {str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in sorted(paths)}


def inputs(prepare_token, base_build_token):
    C.validate_token(prepare_token); C.validate_token(base_build_token)
    row = J.verified_artifact(prepare_token)
    name = row['file']
    require(name == J.FILENAME and Path(row['path']) == J.STATE / prepare_token / name, 'COMET_JAR_PATH_MISMATCH')
    raw = file_bytes(Path(row['path']), limit=row['size'])
    require(len(raw) == row['size'] and digest(raw) == row['sha256'] == J.SHA256
            and hashlib.sha1(raw).hexdigest() == row['sha1'], 'COMET_JAR_HASH_MISMATCH')
    artifact = {key: row[key] for key in ('artifact', 'size', 'sha256', 'sha1', 'native_entry', 'native_elf')}
    artifact['filename'] = name
    script, sealed = C.job_script().encode(), sources()
    labels = {'shirokuma.io.component': COMPONENT, 'shirokuma.io.script-sha256': digest(script),
        'shirokuma.io.artifacts-sha256': digest([artifact]), 'shirokuma.io.sources-sha256': digest(sealed)}
    dockerfile = ('FROM ' + REMOTE.IMAGE + '\nUSER 185:185\n' +
        ''.join('LABEL ' + key + '=' + json.dumps(value) + '\n' for key, value in sorted(labels.items())) +
        'COPY --chown=185:185 rootfs/ /\n').encode()
    recipe = {'base_image': REMOTE.IMAGE, 'base_build_token': base_build_token,
        'artifacts': [artifact], 'script_sha256': digest(script), 'dockerfile_sha256': digest(dockerfile),
        'source_hashes': sealed, 'labels': labels}
    recipe['input_sha256'] = digest(recipe)
    recipe['tag'] = REPOSITORY + ':' + recipe['input_sha256']
    return recipe, raw, script, dockerfile


def cached_base(base_build_token, command):
    proof = BASE.read_verified_image(base_build_token, command=command)
    require(proof['image'] == REMOTE.IMAGE and proof['architecture'] == 'arm64' and proof['os'] == 'linux',
            'COMET_BASE_IMAGE_CHANGED')
    image = BASE.inspect(proof['image'], command)
    require(image['Id'] == proof['image_id'], 'COMET_BASE_IMAGE_CHANGED')
    return image, proof


def verify_image(image, recipe, base, proof):
    identity = image.get('Id')
    require(type(identity) is str and SHA.fullmatch(identity), 'COMET_IMAGE_ID_INVALID')
    ref = REPOSITORY + '@' + identity
    require(image.get('Architecture') == 'arm64' and image.get('Os') == 'linux'
            and recipe['tag'] in image.get('RepoTags', []) and ref in image.get('RepoDigests', [])
            and image.get('Descriptor', {}).get('digest') == identity, 'COMET_IMAGE_IDENTITY_MISMATCH')
    wanted = dict(base['Config'], User='185:185', Labels={**(base['Config'].get('Labels') or {}), **recipe['labels']})
    clean = lambda value: {key: item for key, item in value.items() if item is not None}
    require(clean(image.get('Config', {})) == clean(wanted), 'COMET_IMAGE_CONFIG_MISMATCH')
    layers = image.get('RootFS', {}).get('Layers', [])
    require(layers[:-1] == proof['rootfs_diff_ids'] and len(layers) == len(proof['rootfs_diff_ids']) + 1
            and all(type(layer) is str and SHA.fullmatch(layer) for layer in layers), 'COMET_IMAGE_LAYERS_MISMATCH')
    require(type(image.get('Size')) is int and base['Size'] < image['Size'] < base['Size'] + 256 * 1024**2,
            'COMET_IMAGE_SIZE_INVALID')
    return {'image': ref, 'image_id': identity, 'image_bytes': image['Size'], 'rootfs_diff_ids': layers,
            'architecture': 'arm64', 'os': 'linux', 'user': '185:185'}


def build(prepare_token, base_build_token, *, command=P.command):
    C.validate_token(prepare_token); C.validate_token(base_build_token)
    recipe, raw, script, dockerfile = inputs(prepare_token, base_build_token)
    directory(STATE, create=True); private(STATE)
    token = uuid.uuid4().hex
    output = STATE / token; output.mkdir(mode=0o700)
    receipt = {'schema_version': 1, 'build_token': token, 'prepare_token': prepare_token,
        'base_build_token': base_build_token, 'result': 'running', 'pending': None, 'reused': False,
        'runtime_started': False, 'evidence': str(output.relative_to(ROOT))}
    persist(output, receipt)
    try:
        with run_lock(STATE):
            receipt['inputs'] = recipe
            receipt['runtime'] = P.guard(run=command)
            base, proof = cached_base(base_build_token, command)
            receipt['base'] = proof
            context = output / 'context'; context.mkdir(mode=0o700)
            rootfs = context / 'rootfs'
            for path in (rootfs, rootfs/'opt', rootfs/'opt/spark', rootfs/'opt/spark/jars', rootfs/'opt/shirokuma'):
                path.mkdir(mode=0o700)
            put(context/'Dockerfile', dockerfile)
            put(rootfs/'opt/shirokuma/check.py', script)
            put(rootfs/'opt/spark/jars'/J.FILENAME, raw)
            receipt['context'] = {'relative_path': 'context', 'files': context_files(context)}
            require(receipt['context']['files'] == expected_context(recipe), 'COMET_CONTEXT_CHANGED')
            persist(output, receipt)
            listed = command(P.docker('image', 'ls', '--no-trunc', '--filter', 'reference='+recipe['tag'], '--format', '{{.ID}}')).splitlines()
            require(len(listed) <= 1 and all(SHA.fullmatch(value.strip()) for value in listed), 'COMET_IMAGE_AMBIGUOUS')
            if listed:
                image = BASE.inspect(recipe['tag'], command)
                require(image['Id'] == listed[0].strip(), 'COMET_IMAGE_CHANGED')
                receipt['reused'] = True
            else:
                require(P.guard(run=command) == receipt['runtime'], 'COMET_RUNTIME_CHANGED')
                receipt['pending'] = {'operation': 'build', 'tag': recipe['tag']}; persist(output, receipt)
                require(context_files(context) == expected_context(recipe), 'COMET_CONTEXT_CHANGED')
                command(P.docker('build', '--pull=false', '--network=none', '--platform', 'linux/arm64',
                    '--tag', recipe['tag'], str(context)), timeout=600, log_path=output/'build-log.json')
                image = BASE.inspect(recipe['tag'], command)
            receipt.update(verify_image(image, recipe, base, proof))
            require(context_files(context) == expected_context(recipe), 'COMET_CONTEXT_CHANGED')
            require(inputs(prepare_token, base_build_token)[0] == recipe, 'COMET_INPUTS_CHANGED')
            require(cached_base(base_build_token, command)[1] == proof, 'COMET_BASE_IMAGE_CHANGED')
            require(P.guard(run=command) == receipt['runtime'], 'COMET_RUNTIME_CHANGED')
            receipt.update(result='passed', pending=None); persist(output, receipt)
            return receipt
    except BaseException as error:
        receipt.update(result='failed', error_type=type(error).__name__)
        try:
            persist(output, receipt)
        finally:
            raise error


def read_verified_image(build_token, *, command=P.command):
    C.validate_token(build_token)
    output = STATE / build_token; private(output)
    receipt = json.loads(file_bytes(output/'summary.json', limit=2*1024**2))
    require(receipt.get('schema_version') == 1 and receipt.get('build_token') == build_token
            and receipt.get('result') == 'passed' and receipt.get('pending') is None, 'COMET_BUILD_NOT_PASSED')
    with run_lock(STATE):
        recipe = inputs(receipt['prepare_token'], receipt['base_build_token'])[0]
        require(recipe == receipt['inputs'], 'COMET_BUILD_INPUTS_CHANGED')
        require(P.guard(run=command) == receipt['runtime'], 'COMET_RUNTIME_CHANGED')
        base, proof = cached_base(receipt['base_build_token'], command)
        require(proof == receipt['base'], 'COMET_BASE_IMAGE_CHANGED')
        checked = verify_image(BASE.inspect(recipe['tag'], command), recipe, base, proof)
        require(all(receipt.get(key) == value for key, value in checked.items()), 'COMET_BUILD_IMAGE_CHANGED')
        require(receipt['context']['relative_path'] == 'context'
                and context_files(output/'context') == receipt['context']['files'] == expected_context(recipe),
                'COMET_CONTEXT_CHANGED')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-token', required=True)
    parser.add_argument('--base-build-token', required=True)
    args = parser.parse_args(argv)
    try:
        with SESSION.interruption_handlers():
            result = build(args.prepare_token, args.base_build_token)
        print(json.dumps({key: result[key] for key in ('result', 'build_token', 'image', 'reused', 'evidence')}))
        return 0
    except BaseException as error:
        print(json.dumps({'result': 'failed', 'error_type': type(error).__name__}))
        return 128 + error.signum if isinstance(error, SESSION.SessionInterrupted) else 1


if __name__ == '__main__':
    raise SystemExit(main())
