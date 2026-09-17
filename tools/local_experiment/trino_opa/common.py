"""Fixed local state and process helpers for the isolated Trino/OPA experiment."""
from contextlib import contextmanager
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
STATE = ROOT / 'tmp/local-experiment/trino-opa'
DEPLOY = ROOT / 'deploy/local-experiment/trino-opa'
CONTEXT = 'colima-mac-studio-solo'
NAMESPACE = 'shirokuma-dev'
PINS = json.loads((HERE / 'inputs.lock.json').read_text())
TOKEN = re.compile(r'[0-9a-f]{32}')
IMAGE = re.compile(r'shirokuma\.local/(trino-opa|opa)@sha256:[0-9a-f]{64}')
sys.path.insert(0, str(HERE.parent))
from local_session import interruption_handlers, stop_group


class ExperimentError(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise ExperimentError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value).encode()).hexdigest()


def token(value):
    require(type(value) is str and TOKEN.fullmatch(value), 'INVALID_RUN_TOKEN')
    return value


def directory(path, create=False):
    if create:
        # Parent checks happen before creation, so an existing symlink is never followed.
        directory(path.parent)
        path.mkdir(mode=0o700, exist_ok=True)
    for parent in [*reversed(path.parents), path]:
        require(stat.S_ISDIR(parent.lstat().st_mode), 'UNSAFE_PATH')
    require(path.stat().st_uid == os.getuid() and not path.stat().st_mode & 0o022, 'UNSAFE_PATH')


def write(path, value):
    directory(path.parent)
    require(path.parent.stat().st_mode & 0o777 == 0o700 and not path.is_symlink(), 'NONPRIVATE_EVIDENCE')
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex)
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read(path, limit=2 * 1024**2):
    directory(path.parent)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
                and not info.st_mode & 0o022 and info.st_size <= limit, 'UNSAFE_FILE')
        data = stream.read(limit + 1)
    require(len(data) <= limit, 'FILE_TOO_LARGE')
    return data


def command(args, *, body=None, timeout=40, output_limit=2 * 1024**2, log_path=None):
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR', 'USER', 'LOGNAME') if key in os.environ}
    env.update(LANG='C.UTF-8', LC_ALL='C.UTF-8', DOCKER_CONFIG=str(Path.home() / '.docker'))
    process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True, env=env)
    try:
        stdout, stderr = process.communicate(body.encode() if body is not None else None, timeout=timeout)
    except BaseException:
        stop_group(process, signal.SIGTERM, 5)
        raise
    require(len(stdout) + len(stderr) <= output_limit, 'PROCESS_OUTPUT_LIMIT')
    if log_path is not None:
        write(log_path, {'exit_code': process.returncode, 'stdout': stdout.decode(errors='replace'),
                         'stderr': stderr.decode(errors='replace')})
    require(process.returncode == 0, 'LOCAL_COMMAND_FAILED')
    return stdout.decode()


def new_output(kind):
    directory(STATE, create=True)
    parent = STATE / kind
    directory(parent, create=True)
    run_token = uuid.uuid4().hex
    output = parent / run_token
    directory(output, create=True)
    return run_token, output


@contextmanager
def run_lock(output):
    directory(output)
    fd = os.open(output / 'run.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == 0o600, 'UNSAFE_RUN_LOCK')
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ExperimentError('RUN_ACTIVE') from None
        yield


def load_build(build_token, *, verify_inputs=True):
    value = json.loads(read(STATE / 'builds' / token(build_token) / 'build.json'))
    require(value.get('result') == 'passed' and value.get('build_token') == build_token
            and (not verify_inputs or value.get('inputs_sha256') == digest(PINS)), 'BUILD_IDENTITY_MISMATCH')
    require(set(value.get('images', {})) == {'trino', 'opa'}, 'BUILD_IDENTITY_MISMATCH')
    for component, image in value['images'].items():
        require(IMAGE.fullmatch(image) and image.split('@')[0] == 'shirokuma.local/' + ('trino-opa' if component == 'trino' else 'opa'),
                'BUILD_IDENTITY_MISMATCH')
    return value
