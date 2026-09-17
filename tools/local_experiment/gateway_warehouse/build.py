"""Prepare pinned ARM64 images in the dedicated Docker context; start no runtime."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.gateway_warehouse.support import (
    CONTEXT, DEPLOY, STATE, command, digest, interruption_handlers, new_output, read, require, token, write)
from tools.local_experiment.trino_opa import common as opa_common

HERE = Path(__file__).resolve().parent
PINS = json.loads((HERE / 'inputs.lock.json').read_text())


def inputs_hash():
    return digest({'pins': PINS, 'assets': {name: digest((DEPLOY / name).read_bytes())
                   for name in ('routing-rules.yaml', 'warehouse-mapping.json')}})


def docker(*args, **kwargs):
    return command(['docker', '--context', CONTEXT, *args], **kwargs)


def check_context():
    value = json.loads(docker('context', 'inspect', CONTEXT))[0]
    host = value.get('Endpoints', {}).get('docker', {}).get('Host', '')
    require(host.startswith('unix://') and host.endswith('/.colima/mac-studio-solo/docker.sock'),
            'DOCKER_CONTEXT_MISMATCH')


def inspect_image(image, component):
    require(image == PINS['images'].get(component), 'IMAGE_PIN_MISMATCH')
    value = json.loads(docker('image', 'inspect', image))[0]
    require(value.get('Architecture') == 'arm64' and value.get('Os') == 'linux', 'IMAGE_NOT_ARM64')
    require(image in value.get('RepoDigests', []), 'IMAGE_DIGEST_MISMATCH')
    require(type(value.get('Size')) is int and 0 < value['Size'] < 3 * 1024**3, 'IMAGE_SIZE_INVALID')
    if component == 'trino':
        require(value.get('Config', {}).get('Labels', {}).get('shirokuma.dev.component') == 'trino-opa-shadow',
                'IMAGE_OWNER_MISMATCH')
    return {'image': image, 'config_id': value['Id'], 'architecture': 'arm64', 'os': 'linux', 'bytes': value['Size']}


def load_build(build_token):
    value = json.loads(read(STATE / 'builds' / token(build_token) / 'build.json'))
    require(value.get('schema_version') == 1 and value.get('build_token') == build_token
            and value.get('result') == 'passed' and value.get('inputs_sha256') == inputs_hash()
            and value.get('images') == PINS['images'], 'BUILD_IDENTITY_MISMATCH')
    require(set(value.get('image_evidence', {})) == set(PINS['images']), 'BUILD_EVIDENCE_MISSING')
    return value


def build(output, build_token):
    token(build_token)
    receipt = {'schema_version': 1, 'build_token': build_token, 'inputs_sha256': inputs_hash(),
               'result': 'running', 'images': {}, 'image_evidence': {}, 'pending': None}
    write(output / 'build.json', receipt)
    try:
        check_context()
        previous = opa_common.load_build(PINS['trino_build_token'])
        require(previous['images']['trino'] == PINS['images']['trino'], 'TRINO_BUILD_MISMATCH')
        for component in ('trino', 'postgres', 'gateway'):
            image = PINS['images'][component]
            if component == 'gateway':
                receipt['pending'] = {'operation': 'pull', 'image': image}
                write(output / 'build.json', receipt)
                docker('pull', '--platform', 'linux/arm64', image, timeout=600,
                       log_path=output / 'gateway-pull.json')
            receipt['image_evidence'][component] = inspect_image(image, component)
            receipt['images'][component] = image
        require(receipt['inputs_sha256'] == inputs_hash(), 'BUILD_INPUTS_CHANGED')
        receipt.update(result='passed', pending=None)
    except BaseException as error:
        receipt.update(result='failed', error_type=type(error).__name__)
        write(output / 'build.json', receipt)
        raise
    write(output / 'build.json', receipt)
    return receipt


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    build_token, output = new_output('builds')
    try:
        with interruption_handlers():
            receipt = build(output, build_token)
        print(json.dumps({'result': 'passed', 'build_token': build_token, 'images': receipt['images']}))
        return 0
    except BaseException as error:
        print(json.dumps({'result': 'failed', 'build_token': build_token, 'error_type': type(error).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
