"""Build only the dedicated Keycloak image; preserve the cached OPA/Trino images."""
import argparse
import json
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.trino_identity.support import (
    CONTEXT, STATE, command, digest, interruption_handlers, new_output, read, require, token, write)
from tools.local_experiment.trino_opa import common as opa_common

HERE = Path(__file__).resolve().parent
DEPLOY = ROOT / 'deploy/local-experiment/trino-identity'
PINS = json.loads((HERE / 'inputs.lock.json').read_text())
REPOSITORY = PINS['keycloak']['local_repository']


def inputs_hash():
    return digest({'pins': PINS, 'dockerfile_sha256': digest((DEPLOY / 'Dockerfile').read_bytes())})


def _docker(*args, **kwargs):
    return command(['docker', '--context', CONTEXT, *args], **kwargs)


def check_context():
    context = json.loads(_docker('context', 'inspect', CONTEXT))[0]
    endpoint = context.get('Endpoints', {}).get('docker', {}).get('Host', '')
    require(endpoint.startswith('unix://') and endpoint.endswith('/.colima/mac-studio-solo/docker.sock'),
            'DOCKER_CONTEXT_MISMATCH')


def _image(reference, *, component=None):
    value = json.loads(_docker('image', 'inspect', reference))[0]
    require(value.get('Architecture') == 'arm64' and value.get('Os') == 'linux', 'IMAGE_NOT_ARM64')
    if component is not None:
        require(value.get('Config', {}).get('Labels', {}).get('shirokuma.dev.component') == component,
                'IMAGE_OWNER_MISMATCH')
    if '@sha256:' in reference:
        require(reference in value.get('RepoDigests', []), 'IMAGE_DIGEST_MISMATCH')
    return value


def load_build(build_token):
    receipt = json.loads(read(STATE / 'builds' / token(build_token) / 'build.json'))
    require(receipt.get('result') == 'passed' and receipt.get('build_token') == build_token
            and receipt.get('inputs_sha256') == inputs_hash(), 'BUILD_IDENTITY_MISMATCH')
    images = receipt.get('images', {})
    require(set(images) == {'trino', 'opa', 'keycloak'} and
            all(images.get(key) == value for key, value in PINS['images'].items()) and
            re.fullmatch(re.escape(REPOSITORY) + r'@sha256:[0-9a-f]{64}', images.get('keycloak', '')),
            'BUILD_IDENTITY_MISMATCH')
    return receipt


def build(output, build_token):
    token(build_token)
    receipt = {'schema_version': 1, 'build_token': build_token, 'result': 'running',
               'inputs_sha256': inputs_hash(), 'images': {}, 'image_bytes': {}, 'pending': None}
    write(output / 'build.json', receipt)
    try:
        check_context()
        source = opa_common.load_build(PINS['opa_build_token'])
        require(source['images'] == PINS['images'], 'OPA_BUILD_MISMATCH')
        for component, image in PINS['images'].items():
            inspected = _image(image, component='trino-opa-shadow')
            receipt['images'][component] = image
            receipt['image_bytes'][component] = inspected['Size']
        source_image = PINS['keycloak']['source_image']
        receipt['pending'] = {'operation': 'pull', 'source_image': source_image}
        write(output / 'build.json', receipt)
        _docker('pull', '--platform', 'linux/arm64', source_image, timeout=600,
                log_path=output / 'pull-log.json')
        _image(source_image)
        tag = REPOSITORY + ':' + PINS['keycloak']['version'] + '-' + build_token
        require(not _docker('image', 'ls', '--filter', 'reference=' + tag, '--format', '{{.ID}}').strip(),
                'IMAGE_TAG_COLLISION')
        receipt['pending'] = {'operation': 'build', 'tag': tag}
        write(output / 'build.json', receipt)
        with tempfile.TemporaryDirectory(prefix='context-', dir=output) as temporary:
            stage = Path(temporary)
            (stage / 'Dockerfile').write_bytes((DEPLOY / 'Dockerfile').read_bytes())
            _docker('build', '--pull=false', '--network=none', '--platform', 'linux/arm64',
                    '--tag', tag, str(stage), timeout=600, log_path=output / 'build-log.json')
        image = _image(tag, component='trino-identity-keycloak')
        refs = [ref for ref in image.get('RepoDigests', []) if ref.startswith(REPOSITORY + '@sha256:')]
        require(len(refs) == 1, 'IMAGE_MANIFEST_DIGEST_MISSING')
        receipt['images']['keycloak'] = refs[0]
        receipt['image_bytes']['keycloak'] = image['Size']
        require(receipt['inputs_sha256'] == inputs_hash(), 'BUILD_INPUTS_CHANGED')
        receipt['pending'] = None
        receipt['result'] = 'passed'
    except BaseException as error:
        receipt['result'] = 'failed'
        receipt['error_type'] = type(error).__name__
        write(output / 'build.json', receipt)
        raise
    write(output / 'build.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    build_token, output = new_output('builds')
    try:
        with interruption_handlers():
            receipt = build(output, build_token)
        print(json.dumps({'result': receipt['result'], 'build_token': build_token, 'images': receipt['images']}))
        return 0
    except BaseException as error:
        print(json.dumps({'result': 'failed', 'build_token': build_token, 'error_type': type(error).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
