"""Installed-tool, anonymous, exact-digest evidence collection; never starts a VM.

Call collect explicitly from the authorized mission. Existing outputs are never
replaced, failed operations never replayed, and caches are never cleaned up.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time

from tools.local_experiment import flux_refresh_supervisor as F

GIB = 1024**3


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)


def save(path, value):
    private(path, (json.dumps(value, indent=2) + '\n').encode())


def usage(directory):
    total = 0
    for base, dirs, files in os.walk(directory, followlinks=False):
        for name in dirs + files:
            entry = Path(base)/name
            st = entry.lstat()
            F.R.require(not stat.S_ISLNK(st.st_mode), 'REFRESH_CACHE_SYMLINK')
            if stat.S_ISREG(st.st_mode):
                total += st.st_size
    return total


def check_budget(directory):
    # Stop conservatively before the mission's hard 8 GiB/16 GiB boundaries.
    F.R.require(usage(directory) < 7*GIB and
                shutil.disk_usage(directory).free >= 17*GIB, 'REFRESH_DISK_BUDGET_INVALID')


def secure_generated(work):
    """Trivy explicitly chmods downloaded DB files 0644 despite a private umask.

    Narrowly normalize only this collection's new files, never retained inputs.
    The containing mission/work directories remain 0700 during retrieval.
    """
    changes = []
    for base, dirs, files in os.walk(work, followlinks=False):
        for name in dirs + files:
            entry = Path(base)/name
            st = entry.lstat()
            F.R.require(st.st_uid == os.getuid() and not stat.S_ISLNK(st.st_mode),
                        'REFRESH_CACHE_OWNERSHIP_UNVERIFIED')
            mode = 0o700 if stat.S_ISDIR(st.st_mode) else 0o600
            F.R.require(stat.S_ISDIR(st.st_mode) or
                        (stat.S_ISREG(st.st_mode) and st.st_nlink == 1),
                        'REFRESH_CACHE_TYPE_UNVERIFIED')
            if stat.S_IMODE(st.st_mode) != mode:
                changes.append({'path': str(entry.relative_to(work)),
                                'original_mode': oct(stat.S_IMODE(st.st_mode))})
                os.chmod(entry, mode, follow_symlinks=False)
    return changes


def environment(directory):
    work = directory/'scan-work'
    work.mkdir(mode=0o700)  # Exclusive reservation: no second collection/replay.
    env = {k: os.environ[k] for k in ('PATH', 'LANG', 'LC_ALL') if k in os.environ}
    for key, name in (('HOME', 'home'), ('XDG_CACHE_HOME', 'cache'),
                      ('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'),
                      ('TMPDIR', 'temp'), ('DOCKER_CONFIG', 'docker')):
        path = work/name
        path.mkdir(mode=0o700)
        env[key] = str(path)
    env['TMP'] = env['TEMP'] = env['TMPDIR']
    env['SYFT_CHECK_FOR_APP_UPDATE'] = 'false'
    env['SYFT_CACHE_DIR'] = str(work/'cache')
    env['COSIGN_CACHE_DIR'] = str(work/'cache')
    save(work/'docker/config.json', {})  # No host credentials/helpers.
    private(work/'trivy.yaml', b'{}\n')
    private(work/'syft.yaml', b'{}\n')
    return env


def commands(directory, images):
    work = directory/'scan-work'
    trivy = ['trivy', '--cache-dir', str(work/'cache/trivy'),
             '--config', str(work/'trivy.yaml')]
    yield 'db-refresh', trivy + ['image', '--download-db-only', '--no-progress',
        '--skip-version-check', '--db-repository', 'ghcr.io/aquasecurity/trivy-db:2'], 'db-refresh.stdout'
    yield 'scanner-version', trivy + ['--version', '--format', 'json'], 'scanner-version.json'
    for image in images:
        component, reference = image['component'], image['reference']
        F.R.require(image['platform'] == 'linux/arm64' and
                    re.fullmatch(r'ghcr\.io/fluxcd/' + re.escape(component) +
                                 r'@sha256:[a-f0-9]{64}', reference), 'REFRESH_INPUT_UNVERIFIED')
        yield component+'-config', ['crane', 'config', '--platform', 'linux/arm64', reference], component+'.config.json'
        yield component+'-scan', trivy + ['image', '--image-src', 'remote',
            '--platform', 'linux/arm64', '--scanners', 'vuln', '--format', 'json',
            '--skip-db-update', '--skip-java-db-update', '--skip-check-update',
            '--skip-version-check', '--no-progress', '--timeout', '15m', reference], component+'.trivy.json'
        yield component+'-sbom', ['syft', 'scan', 'registry:'+reference,
            '--config', str(work/'syft.yaml'), '--platform', 'linux/arm64',
            '--output', 'cyclonedx-json'], component+'.sbom.json'


def execute(directory, env, name, argv, output, *, deadline=None):
    executable = shutil.which(argv[0])
    F.R.require(executable is not None, 'REFRESH_INSTALLED_TOOL_MISSING')
    check_budget(directory)
    save(directory/(name+'.started.json'), {'argv': argv, 'started_at': timestamp()})
    outfd = os.open(directory/output, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    errfd = os.open(directory/(name+'.stderr'), os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    disposition = 'unknown'
    code = None
    process = None
    try:
        with os.fdopen(outfd, 'wb') as out, os.fdopen(errfd, 'wb') as err:
            process = subprocess.Popen([executable, *argv[1:]], cwd=directory/'scan-work',
                                       env=env, stdout=out, stderr=err)
            started = time.monotonic()
            while process.poll() is None:
                check_budget(directory)
                F.R.require(time.monotonic()-started < 1000 and
                            (deadline is None or time.monotonic() < deadline), 'REFRESH_OPERATION_TIMEOUT')
                time.sleep(0.5)
            code = process.returncode
            disposition = 'succeeded' if code == 0 else 'failed'
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            disposition = 'terminated_hold_no_replay'
            code = process.returncode
        changes = secure_generated(directory/'scan-work')
        save(directory/(name+'.finished.json'), {'finished_at': timestamp(),
             'returncode': code, 'disposition': disposition,
             'generated_mode_changes': changes,
             'output_sha256': hashlib.sha256((directory/output).read_bytes()).hexdigest()})
    F.R.require(code == 0, 'REFRESH_OPERATION_FAILED:'+name)


def collect(root, directory):
    F.R.require(not directory.is_symlink(), 'REFRESH_SCOPE_INVALID')
    directory = directory.resolve(strict=True)
    F.R.require(directory.is_relative_to(root.resolve()) and
                directory.parent == (root/'tmp/local-experiment/flux-refresh').resolve(),
                'REFRESH_SCOPE_INVALID')
    st = directory.lstat()
    F.R.require(stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and
                stat.S_IMODE(st.st_mode) == 0o700, 'REFRESH_SCOPE_INVALID')
    os.umask(0o077)
    images, ledger_hash = F.inputs(root)
    plan = list(commands(directory, images))  # Validate every image before any retrieval.
    before = F.stopped(root)
    env = environment(directory)
    started = time.time()
    save(directory/'collection-started.json', {'started_at': timestamp(),
         'ledger_sha256': ledger_hash, 'images': images, 'profiles': before})
    try:
        for name, argv, output in plan:
            execute(directory, env, name, argv, output)
            if name.endswith('-config'):
                config = json.loads((directory/output).read_bytes())
                F.R.require(config.get('architecture') == 'arm64' and config.get('os') == 'linux',
                            'REFRESH_IMAGE_PLATFORM_UNVERIFIED')
        observed = F.verify_scans(directory, images, started)
        classification = 'FRESH_SCAN_EVIDENCE_ONLY'
    except (F.R.RunnerError, OSError, ValueError, TypeError, AttributeError) as error:
        observed = {'evidence_refresh_verified': False, 'reason': str(error),
                    'admission': False, 'risk_approval': False}
        classification = 'REFRESH_DEPENDENCY_HELD'
    F.R.require(F.inputs(root)[1] == ledger_hash and F.stopped(root) == before,
                'REFRESH_SCOPE_DRIFT')
    save(directory/'collection-result.json', {'classification': classification,
         'finished_at': timestamp(), 'observations': observed, 'generated_bytes': usage(directory),
         'free_bytes': shutil.disk_usage(directory).free, 'gate_acceptance': False,
         'publisher_verification': 'not_refreshed; signed index outside four-digest scope'})
    return classification
