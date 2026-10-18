"""Restore the upstream password plugin omitted from the slim local Trino image.

Usage: python3 tools/local_experiment/trino_auth_image.py /path/to/trino-server-483.tar.gz
Builds in the dedicated lab Docker context; does not publish or roll out the image.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile


BASE = 'shirokuma.local/trino@sha256:8634ba3cac5c9735647b95602eff04497bccd92f7eb2a2e9beab7a7488c03ca2'
TAG = 'shirokuma.local/trino:483-password'
DOCKER = ['docker', '--context', 'colima-mac-studio-solo']


def build(archive):
    image = json.loads(subprocess.check_output(DOCKER + ['image', 'inspect', BASE]))[0]
    expected = image['Config']['Labels']['shirokuma.dev.upstream-archive-sha256']
    digest = hashlib.sha256()
    with archive.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise RuntimeError('archive differs from the approved base image distribution')
    with tempfile.TemporaryDirectory(prefix='shirokuma-trino-password-') as temporary:
        context = Path(temporary)
        plugin = context / 'password-authenticators'
        plugin.mkdir()
        prefix = 'trino-server-483/plugin/password-authenticators/'
        with tarfile.open(archive) as source:
            entries = []
            for member in source.getmembers():
                if not member.name.startswith(prefix) or not (member.isfile() or member.islnk()):
                    continue
                name = member.name[len(prefix):]
                if Path(name).name != name:
                    raise RuntimeError('unexpected nested password plugin entry')
                while member.islnk():
                    member = source.getmember(member.linkname)
                entries.append((member, name))
            # The distribution deduplicates jars with tar hardlinks; materialize
            # each original jar so the plugin remains complete in the slim image.
            for member, name in sorted(entries, key=lambda entry: entry[0].offset_data):
                with source.extractfile(member) as original, (plugin / name).open('wb') as target:
                    shutil.copyfileobj(original, target)
        if not list(plugin.glob('*password-authenticators*483*.jar')):
            raise RuntimeError('archive does not contain the Trino483 password authenticator plugin')
        dockerfile = Path(__file__).resolve().parents[2] / 'deploy/local-experiment/trino/secure/Dockerfile'
        shutil.copyfile(dockerfile, context / 'Dockerfile')
        subprocess.run(DOCKER + ['build', '--network=none', '-t', TAG, str(context)], check=True)
    print('Built', TAG, 'with the unmodified same-distribution password plugin')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    build(parser.parse_args().archive)
