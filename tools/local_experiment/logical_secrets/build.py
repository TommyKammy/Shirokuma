"""Explicit code-only build using the cached official image; importing is inert."""
from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
import zipfile

IMAGE = 'trinodb/trino:483@sha256:aa18e61b2e7776ab8641ba8baaa8687d0430894e88c639e61010cc46a994ab36'
CONTEXT = 'colima-mac-studio-solo'
SOURCE = Path(__file__).resolve().parent / 'src'
SERVICE = 'META-INF/services/io.airlift.spi.secrets.SecretsPlugin'
PLUGIN = 'org.shirokuma.logical.secrets.FileSecretsPlugin'
SPI = 'file:/usr/lib/trino/lib/io.airlift_secrets-spi-439.jar'
# Constant container command: public paths/arguments are never shell fragments.
SCRIPT = r"""set -eu
javac -version 2>&1 | grep '^javac 25'
test -f /usr/lib/trino/lib/io.airlift_secrets-spi-439.jar
mkdir /out/classes
javac --release 25 -cp /usr/lib/trino/lib/io.airlift_secrets-spi-439.jar -d /out/classes /src/main/java/org/shirokuma/logical/secrets/*.java
mkdir -p /out/classes/META-INF/services
cp /src/main/resources/META-INF/services/io.airlift.spi.secrets.SecretsPlugin /out/classes/META-INF/services/
printf 'Manifest-Version: 1.0\nClass-Path: file:/usr/lib/trino/lib/io.airlift_secrets-spi-439.jar\n\n' > /out/manifest.mf
jar --create --file /out/logical-secrets.jar --manifest /out/manifest.mf -C /out/classes .
"""


def validate_jar(path):
    """Allow only own classes and the two intended metadata files/directories."""
    directories = {'META-INF/', 'META-INF/services/', 'org/', 'org/shirokuma/',
        'org/shirokuma/logical/', 'org/shirokuma/logical/secrets/'}
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('INVALID_BUILD_JAR')
        for item in archive.infolist():
            name = item.filename
            if (PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts or
                    stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError('INVALID_BUILD_JAR')
            if name in directories or name in ('META-INF/MANIFEST.MF', SERVICE):
                continue
            if not re.fullmatch(r'org/shirokuma/logical/secrets/[A-Za-z_$][A-Za-z0-9_$]*\.class', name):
                raise ValueError('INVALID_BUILD_JAR')
        if archive.read(SERVICE).decode().strip() != PLUGIN:
            raise ValueError('INVALID_BUILD_SERVICE')
        manifest = archive.read('META-INF/MANIFEST.MF').decode().replace('\r\n', '\n').replace('\n ', '')
        if 'Class-Path: '+SPI+'\n' not in manifest or 'Manifest-Version: 1.0\n' not in manifest:
            raise ValueError('INVALID_BUILD_MANIFEST')
        for required in ('FileSecretsPlugin', 'FixedPublisher'):
            if 'org/shirokuma/logical/secrets/'+required+'.class' not in names:
                raise ValueError('INVALID_BUILD_JAR')


def build(output, *, overwrite=False, context=CONTEXT, command=None):
    """Build into a fresh isolated mount; publish validated jar with mode 0600."""
    if context != CONTEXT:
        raise ValueError('UNAUTHORIZED_DOCKER_CONTEXT')
    output = Path(output)
    if (not output.is_absolute() or '..' in output.parts or output.suffix != '.jar' or
            stat.S_IMODE(output.parent.stat().st_mode) != 0o700):
        raise ValueError('PRIVATE_ABSOLUTE_JAR_REQUIRED')
    if output.exists() and not overwrite:
        raise FileExistsError('OUTPUT_EXISTS')
    command = command or subprocess.run
    with tempfile.TemporaryDirectory(prefix='logical-secrets-build-', dir=output.parent) as directory:
        argv = ['rtk', 'proxy', 'docker', '--context', CONTEXT, 'run', '--rm', '--pull=never', '--network=none',
            '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--user=0:0',
            '--mount', 'type=bind,src='+str(SOURCE)+',dst=/src,readonly',
            '--mount', 'type=bind,src='+directory+',dst=/out', '--tmpfs', '/tmp:rw,nosuid,nodev',
            '--entrypoint', '/bin/sh', IMAGE, '-ec', SCRIPT]
        result = command(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=120)
        if result.returncode:
            raise ValueError('BUILD_FAILED')
        jar = Path(directory) / 'logical-secrets.jar'
        validate_jar(jar)
        flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if overwrite else os.O_EXCL)
        with os.fdopen(os.open(output, flags, 0o600), 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(jar.read_bytes())
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description='Build own secrets classes with cached official Trino')
    parser.add_argument('--output', required=True, help='absolute jar path in a private 0700 directory')
    parser.add_argument('--context', choices=(CONTEXT,), default=CONTEXT, help='fixed authorized Docker context')
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args(argv)
    try:
        build(args.output, overwrite=args.overwrite, context=args.context)
    except Exception:
        print('LOGICAL_SECRETS_BUILD_FAILED')
        return 1
    print('LOGICAL_SECRETS_JAR_READY')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
