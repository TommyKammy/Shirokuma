#!/usr/bin/env python3
"""Build fresh local ARM64 images from pinned inputs; no Pod or service mutation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import posixpath
import shutil
import stat
import tarfile
import tempfile
import urllib.request

from common import CONTEXT, DEPLOY, HERE, PINS, STATE, command, digest, interruption_handlers, new_output, require, write


def verify_archive(path):
    require(not path.is_symlink(), 'ARCHIVE_SYMLINK')
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_size == PINS['trino']['archive_bytes'], 'ARCHIVE_SIZE_MISMATCH')
        sha = hashlib.sha256()
        while chunk := stream.read(8 * 1024**2):
            sha.update(chunk)
        after = os.fstat(stream.fileno())
    require((before.st_size, before.st_mtime_ns, before.st_ino) == (after.st_size, after.st_mtime_ns, after.st_ino)
            and sha.hexdigest() == PINS['trino']['archive_sha256'], 'ARCHIVE_HASH_MISMATCH')


def source_hashes():
    return {name: digest((DEPLOY / name).read_bytes()) for name in ('trino.rego', 'api.rego', 'policy_test.rego')}


def prepare_trino(archive, stage):
    verify_archive(archive)
    require(shutil.disk_usage(stage).free >= 10 * 1024**3, 'BUILD_DISK_BUDGET')
    unpacked, context = stage / 'unpacked', stage / 'trino'
    unpacked.mkdir()
    context.mkdir()
    # The complete hash-bound upstream tar is required to resolve cross-plugin hardlinks.
    with tarfile.open(archive, 'r:gz') as bundle:
        # The host's Python 3.9 lacks tarfile's newer extraction filter.
        for member in bundle.getmembers():
            require((member.name.startswith('trino-server-483/') or (member.name == 'trino-server-483' and member.isdir()))
                    and '..' not in member.name.split('/')
                    and (member.isfile() or member.isdir() or member.issym() or member.islnk()), 'UNSAFE_ARCHIVE_MEMBER')
            if member.issym() or member.islnk():
                target = posixpath.normpath(posixpath.join(posixpath.dirname(member.name), member.linkname)
                                           if member.issym() else member.linkname)
                require(target.startswith('trino-server-483/'), 'UNSAFE_ARCHIVE_LINK')
        bundle.extractall(unpacked)
    distribution = unpacked / 'trino-server-483'
    selected = context / 'trino-server-483'
    selected.mkdir()
    (selected / 'plugin').mkdir()
    for name in ('bin', 'lib'):
        shutil.move(str(distribution / name), selected / name)
    for plugin in PINS['trino']['plugins']:
        shutil.move(str(distribution / 'plugin' / plugin), selected / 'plugin' / plugin)
        jars = list((selected / 'plugin' / plugin).glob('*.jar'))
        require(jars and all(p.is_file() and p.stat().st_size > 0 for p in jars), 'PLUGIN_INPUT_MISSING')
    shutil.copyfile(distribution / 'NOTICE', selected / 'NOTICE')
    launcher = (selected / 'bin/linux-arm64/launcher').read_bytes()
    require(launcher[:6] == b'\x7fELF\x02\x01' and int.from_bytes(launcher[18:20], 'little') == 183, 'LAUNCHER_NOT_ARM64')
    require(all(not p.is_symlink() or p.exists() for p in selected.rglob('*')), 'ARCHIVE_LINK_UNRESOLVED')
    verify_archive(archive)
    shutil.rmtree(unpacked)
    (context / 'Dockerfile').write_text(
        'FROM ' + PINS['trino']['base_image'] + '\n'
        'LABEL shirokuma.dev.component="trino-opa-shadow" shirokuma.dev.scope="local-experiment-only"\n'
        'COPY --chown=10000:10000 trino-server-483/ /opt/trino/\n'
        'ENV HOME=/var/trino\nWORKDIR /opt/trino\nUSER 10000:10000\n'
        'ENTRYPOINT ["/opt/trino/bin/launcher","run","--etc-dir=/etc/trino","--data-dir=/var/trino"]\n')
    return context


def prepare_opa(stage):
    context = stage / 'opa'
    context.mkdir()
    pin = PINS['opa']
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(pin['url'], timeout=45) as response:
        data = response.read(pin['bytes'] + 1)
    require(len(data) == pin['bytes'] and digest(data) == pin['sha256'], 'OPA_BINARY_HASH_MISMATCH')
    require(data[:6] == b'\x7fELF\x02\x01' and int.from_bytes(data[18:20], 'little') == 183, 'OPA_NOT_ARM64')
    (context / 'opa').write_bytes(data)
    (context / 'opa').chmod(0o555)
    (context / 'policy').mkdir()
    for name in source_hashes():
        shutil.copyfile(DEPLOY / name, context / 'policy' / name)
    (context / 'Dockerfile').write_text(
        'FROM scratch\n'
        'LABEL shirokuma.dev.component="trino-opa-shadow" shirokuma.dev.scope="local-experiment-only"\n'
        'COPY opa /opa\nCOPY policy/ /policy/\nUSER 10000:10000\n'
        'RUN ["/opa","version"]\nRUN ["/opa","check","--strict","/policy"]\n'
        'RUN ["/opa","test","/policy","-v"]\nENTRYPOINT ["/opa"]\n')
    return context


def inspect_image(tag, repository):
    value = json.loads(command(['docker', '--context', CONTEXT, 'image', 'inspect', tag]))[0]
    require(value.get('Architecture') == 'arm64' and value.get('Os') == 'linux'
            and value.get('Config', {}).get('Labels', {}).get('shirokuma.dev.component') == 'trino-opa-shadow', 'IMAGE_IDENTITY_MISMATCH')
    references = [ref for ref in value.get('RepoDigests', []) if ref.startswith(repository + '@sha256:')]
    require(len(references) == 1, 'IMAGE_MANIFEST_DIGEST_MISSING')
    return references[0], value['Size']


def build(archive, output, build_token):
    receipt = {'schema_version': 1, 'build_token': build_token, 'result': 'running',
               'inputs_sha256': digest(PINS), 'policy_sha256': source_hashes(),
               'images': {}, 'image_bytes': {}, 'pending': None}
    write(output / 'build.json', receipt)
    try:
        context = json.loads(command(['docker', '--context', CONTEXT, 'context', 'inspect', CONTEXT]))[0]
        endpoint = context['Endpoints']['docker']['Host']
        require(endpoint.startswith('unix://') and endpoint.endswith('/.colima/mac-studio-solo/docker.sock'), 'DOCKER_CONTEXT_MISMATCH')
        base = json.loads(command(['docker', '--context', CONTEXT, 'image', 'inspect', PINS['trino']['base_image']]))[0]
        require(base.get('Architecture') == 'arm64' and base.get('Os') == 'linux', 'CACHED_BASE_NOT_ARM64')
        with tempfile.TemporaryDirectory(prefix='context-', dir=output) as temporary:
            stage = Path(temporary)
            for name, prepare in [('trino', lambda: prepare_trino(archive, stage)), ('opa', lambda: prepare_opa(stage))]:
                repository = 'shirokuma.local/' + ('trino-opa' if name == 'trino' else 'opa')
                tag = repository + ':' + PINS[name]['version'] + '-' + build_token
                require(not command(['docker', '--context', CONTEXT, 'image', 'ls', '--filter', 'reference=' + tag, '--format', '{{.ID}}']).strip(), 'IMAGE_TAG_COLLISION')
                receipt['pending'] = {'operation': 'build', 'component': name, 'tag': tag}
                write(output / 'build.json', receipt)
                prepared = prepare()
                command(['docker', '--context', CONTEXT, 'build', '--pull=false', '--network=none', '--platform', 'linux/arm64', '--tag', tag, str(prepared)],
                        timeout=600, log_path=output / (name + '-build-log.json'))
                image, size = inspect_image(tag, repository)
                receipt['images'][name], receipt['image_bytes'][name] = image, size
                receipt['pending'] = None
                write(output / 'build.json', receipt)
        require(receipt['policy_sha256'] == source_hashes(), 'POLICY_CHANGED_DURING_BUILD')
        receipt['result'] = 'passed'
        write(output / 'build.json', receipt)
    except BaseException as error:
        receipt['result'] = 'failed'
        receipt['failure_type'] = type(error).__name__
        write(output / 'build.json', receipt)
        raise
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trino-archive', required=True, type=Path)
    args = parser.parse_args()
    build_token, output = new_output('builds')
    try:
        with interruption_handlers():
            receipt = build(args.trino_archive, output, build_token)
        print(json.dumps({'result': 'passed', 'build_token': build_token, 'images': receipt['images']}))
        return 0
    except BaseException as error:
        print(json.dumps({'result': 'failed', 'build_token': build_token,
                          'failure_code': str(error) if type(error).__name__ == 'ExperimentError' else 'BUILD_FAILED'}))
        return 128 + error.signum if hasattr(error, 'signum') else 1


if __name__ == '__main__':
    raise SystemExit(main())
