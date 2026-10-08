"""Private, no-replay command records for the isolated Flux binary experiment.

This is not an image builder, runner, installer or admission implementation.
Commands are selected explicitly by the bounded mission, never retried here.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import time

GIB = 1024 ** 3


def extract_verified_archive(archive, destination, sha256, size=None, executable=False):
    """Extract checked regular files only into a new private destination.

    Used for the retained source snapshot and the public Go archive, not OCI
    layers. Reject links, special files and traversal before any extraction.
    """
    archive, destination = Path(archive), Path(destination)
    fd = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        st = os.fstat(stream.fileno())
        if not stat.S_ISREG(st.st_mode) or (size is not None and st.st_size != size):
            raise RuntimeError('BUILD_ARCHIVE_IDENTITY_HOLD')
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != sha256:
            raise RuntimeError('BUILD_ARCHIVE_CHECKSUM_HOLD')
        stream.seek(0)
        with tarfile.open(fileobj=stream) as tar:
            members = tar.getmembers()
            names = set()
            for member in members:
                path = Path(member.name)
                if (path.is_absolute() or '..' in path.parts or
                        not (member.isfile() or member.isdir()) or path in names):
                    raise RuntimeError('BUILD_ARCHIVE_CONTENT_HOLD')
                names.add(path)
            destination.mkdir(mode=0o700)
            for member in members:
                target = destination/member.name
                for parent in reversed(target.parent.relative_to(destination).parents):
                    (destination/parent).mkdir(mode=0o700, exist_ok=True)
                target.parent.mkdir(mode=0o700, exist_ok=True)
                if member.isdir():
                    target.mkdir(mode=0o700, exist_ok=True)
                else:
                    mode = 0o700 if executable and member.mode & 0o111 else 0o600
                    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
                    with os.fdopen(fd, 'wb') as out, tar.extractfile(member) as source:
                        shutil.copyfileobj(source, out)
    return dict(sha256=digest, size=st.st_size, members=len(members))


def save(path, data):
    if not isinstance(data, bytes):
        data = (json.dumps(data, indent=2) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)


def usage(directory):
    total = 0
    for base, _, files in os.walk(directory, followlinks=False):
        for name in files:
            try:
                st = (Path(base)/name).lstat()
                total += max(st.st_size, st.st_blocks * 512)
            except FileNotFoundError:
                # Go removes temporary compiler inputs during active builds.
                # A vanished entry contributes no currently retained bytes.
                continue
    return total


def budget(directory, reserve_bytes=GIB):
    if not 256 * 1024**2 <= reserve_bytes <= GIB:
        raise ValueError('BUILD_RESERVE_INVALID')
    if usage(directory) >= 8 * GIB - reserve_bytes or shutil.disk_usage(directory).free < 17 * GIB:
        raise RuntimeError('BUILD_DISK_HOLD')


class Evidence:
    def __init__(self, directory):
        self.directory = Path(directory).absolute()
        st = self.directory.lstat()
        if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) != 0o700):
            raise RuntimeError('BUILD_SCOPE_HOLD')

    def environment(self, create=False):
        env = {k: os.environ[k] for k in ('PATH', 'LANG', 'LC_ALL') if k in os.environ}
        for key, name in (('HOME', 'home'), ('GOCACHE', 'gocache'),
                          ('GOMODCACHE', 'gomodcache'), ('GOPATH', 'gopath'),
                          ('TMPDIR', 'temp'), ('XDG_CACHE_HOME', 'xdg-cache'),
                          ('XDG_CONFIG_HOME', 'xdg-config'), ('DOCKER_CONFIG', 'docker')):
            path = self.directory/name
            if create:
                path.mkdir(mode=0o700)
            st = path.lstat()
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
                raise RuntimeError('BUILD_ENVIRONMENT_HOLD')
            env[key] = str(path)
        env.update(GOENV='off', GOTOOLCHAIN='auto', GOPROXY='https://proxy.golang.org',
                   GOSUMDB='sum.golang.org', GIT_CONFIG_NOSYSTEM='1',
                   GIT_CONFIG_GLOBAL='/dev/null', GIT_TERMINAL_PROMPT='0',
                   SYFT_CHECK_FOR_APP_UPDATE='false', CGO_ENABLED='0')
        env['TMP'] = env['TEMP'] = env['TMPDIR']
        return env

    def command(self, name, argv, cwd=None, extra=None, timeout=1200, allow_failure=False,
                reserve_bytes=GIB):
        env = self.environment()
        env.update(extra or {})
        cwd = Path(cwd or self.directory).resolve(strict=True)
        if not cwd.is_relative_to(self.directory.resolve()):
            raise RuntimeError('BUILD_CWD_HOLD')
        budget(self.directory, reserve_bytes)
        started = dict(argv=argv, cwd=str(cwd.relative_to(self.directory)),
                       environment=env, reserve_bytes=reserve_bytes,
                       started_at=datetime.now(timezone.utc).isoformat())
        save(self.directory/(name+'.started.json'), started)
        process = None
        disposition = 'unknown'
        observation_error = None
        try:
            with os.fdopen(os.open(self.directory/(name+'.stdout'), os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600), 'wb') as out, \
                 os.fdopen(os.open(self.directory/(name+'.stderr'), os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600), 'wb') as err:
                process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=out, stderr=err)
                deadline = time.monotonic() + timeout
                while process.poll() is None:
                    budget(self.directory, reserve_bytes)
                    if time.monotonic() >= deadline:
                        raise RuntimeError('BUILD_TIMEOUT_HOLD')
                    time.sleep(1)
                disposition = 'succeeded' if process.returncode == 0 else 'failed'
        except Exception as error:
            observation_error = type(error).__name__
            raise
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
                disposition = 'terminated_hold_no_replay'
            elif process is not None and process.returncode is not None:
                # A guard may raise just as the command exits. Preserve both
                # the guard failure and the independently known terminal code.
                disposition = 'succeeded' if process.returncode == 0 else 'failed'
            save(self.directory/(name+'.finished.json'), dict(
                disposition=disposition, returncode=None if process is None else process.returncode,
                observation_error=observation_error,
                finished_at=datetime.now(timezone.utc).isoformat(),
                generated_bytes=usage(self.directory),
                stdout_sha256=hashlib.sha256((self.directory/(name+'.stdout')).read_bytes()).hexdigest()))
        if process.returncode and not allow_failure:
            raise RuntimeError('BUILD_OPERATION_FAILED:'+name)
        return process.returncode
