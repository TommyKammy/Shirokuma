"""Read-only offline index seed inspection and separately parent-bound execution.

No CLI or implicit execution. Limits are stop ceilings, not predicted Go peaks.
Unknown/interrupted reservations are immutable and cannot be retried.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import select
import signal
import stat
import subprocess
import tarfile
import time
import zipfile

from tools.local_experiment import flux_test_plan as P

BASE = P.BUILDS[1]
CACHE = BASE + '/gomodcache/cache/download/'
GO_ARCHIVE = BASE + '/go1.26.6.darwin-arm64.tar.gz'
GO_SHA256 = '2dc95ce4675829f2df0e86b28bcef3283635902062a5f0580ca659bf570f3204'
SOURCE_HASHES = {
    'source-snapshot.stdout': '63cec74b395f24b72dff52fa53407f76be63ae3006ef3244f94bfbb1259833b3',
    'source/go.mod': '3feab044a77e6443e173d68faac16f19144fbd756ed4ace30144795d9d1e66c9',
    'source/go.sum': '770ee9b2daeb65a8bb7f6dbc65da77be9b37e11d2265a4d0479253b75b0d56ff',
}
ROOT_MODULE = 'github.com/fluxcd/source-controller'
TARGET = ROOT_MODULE + '/internal/index'
SCOPES = {'index-state': 'tmp/rv/mis', 'index-digest': 'tmp/rv/mid'}
COMPILE = 128 * P.MIB
TEMP = 128 * P.MIB
RECEIPTS = 4 * P.MIB
RESERVE = 32 * P.MIB
PARENT_CHECKS = {'code_closure_integrity', 'session_terminal_idle', 'operations_known',
                 'retention_verified', 'profiles_stopped', 'oauth_only_no_spending',
                 'budget_and_approach_counters'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def binding(value):
    return sha(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())


def escape(value):
    return ''.join('!' + c.lower() if c.isupper() else c for c in value)


def h1(files):
    """Go dirhash.Hash1, including archive prefix; no Go subprocess."""
    lines = ''.join(sha(data) + '  ' + name + '\n' for name, data in sorted(files.items()))
    return 'h1:' + base64.b64encode(hashlib.sha256(lines.encode()).digest()).decode()


def imports(data):
    # Preserve quoted/raw strings while removing comments, so documentation is
    # never mistaken for imports. Conservative union across platform build tags.
    text = data.decode('utf-8')
    tokens = re.sub(r'"(?:\\.|[^"\\])*"|`[^`]*`|//[^\n]*|/\*.*?\*/',
                    lambda m: m[0] if m[0][0] in ('"', '`') else '\n' * m[0].count('\n'),
                    text, flags=re.S)
    P.require('import "C"' not in tokens and '//go:embed' not in text,
              'unsupported cgo/embed closure')
    result = set()
    for match in re.finditer(r'^\s*import\s+(\([^)]*\)|(?:[.\w]+\s+)?["`][^"`\n]+["`])', tokens, re.M):
        result.update(re.findall(r'["`]([^"`\n]+)["`]', match[1]))
    P.require('C' not in result, 'unsupported cgo closure')
    return sorted(result)


def requirements(data):
    text = data.decode()
    pairs = re.findall(r'^\s*(?:require\s+)?([^\s()]+)\s+(v[^\s]+)', text, re.M)
    result = dict(pairs)
    for m in re.finditer(r'^replace\s+(\S+)\s+=>\s+(\S+)\s+(v\S+)\s*$', text, re.M):
        P.require(m[1] == m[2], 'cross-module replacement unsupported')
        result[m[1]] = m[3]
    return result


def archive_files(data, module, version, sums):
    prefix = module + '@' + version + '/'
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        P.require(len(archive.infolist()) < 20000, 'archive file bound')
        files = {}
        total = 0
        for item in archive.infolist():
            if item.is_dir():
                continue
            P.require(item.filename.startswith(prefix), 'module archive prefix')
            tail = item.filename[len(prefix):]
            P.require(all(p not in ('', '.', '..') for p in tail.split('/')) and
                      '\\' not in tail and '\n' not in tail and
                      not stat.S_ISLNK(item.external_attr >> 16), 'unsafe module member')
            total += item.file_size
            P.require(item.file_size <= 16 * P.MIB and total <= 128 * P.MIB,
                      'module archive expansion bound')
            P.require(item.filename not in files, 'duplicate archive member')
            files[item.filename] = archive.read(item)
    P.require(h1(files) == sums.get((module, version)), 'module checksum mismatch')
    return {name[len(prefix):]: data for name, data in files.items()}


def closure(initial, versions, load):
    """Package import fixed point; module tests excluded, target tests included."""
    pending = list(initial)
    seen = set()
    modules = {}
    edges = {}
    while pending:
        package = pending.pop()
        if package in seen:
            continue
        P.require(len(seen) < 512, 'package closure bound')
        seen.add(package)
        candidates = [m for m in versions if package == m or package.startswith(m + '/')]
        P.require(candidates, 'unresolved external import: ' + package)
        module = max(candidates, key=len)
        if module not in modules:
            modules[module] = load(module, versions[module])
        directory = package[len(module):].lstrip('/')
        files = {n: d for n, d in modules[module].items()
                 if n.rpartition('/')[0] == directory and n.endswith('.go') and
                 not n.endswith('_test.go')}
        P.require(files, 'missing dependency package: ' + package)
        deps = sorted({i for d in files.values() for i in imports(d)
                       if '.' in i.split('/')[0]})
        edges[package] = deps
        pending.extend(deps)
    return modules, edges


def entry(path, data, destination, member=None):
    value = dict(path=path, destination=destination, sha256=sha(data), bytes=len(data))
    if member is not None:
        value['member'] = member
    return value


def charge(entries):
    directories = {''}
    for e in entries:
        parts = e['destination'].split('/')
        directories.update('/'.join(parts[:i]) for i in range(1, len(parts)))
    return len(directories) * 4096 + sum((e['bytes'] + 4095)//4096*4096 for e in entries)


def tool_manifest(root):
    data = P.read_bytes(root, GO_ARCHIVE, 80 * P.MIB)
    P.require(sha(data) == GO_SHA256, 'Go1.26.6 archive checksum')
    manifest = []
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        for member in archive:
            P.require((member.name == 'go' or member.name.startswith('go/')) and
                      all(p not in ('.', '..', '') for p in member.name.rstrip('/').split('/')),
                      'unsafe toolchain member')
            if member.isdir():
                continue
            P.require(member.isfile() and member.size < 64*P.MIB, 'toolchain member type/size')
            expected = archive.extractfile(member).read(64*P.MIB)
            path = BASE + '/toolchain/' + member.name
            actual = P.read_bytes(root, path, 64*P.MIB)
            P.require(actual == expected, 'installed toolchain changed')
            manifest.append(entry(path, actual, member.name))
    P.require(any(e['path'] == P.TOOL for e in manifest), 'Go executable missing')
    P.require(P.measure(root, BASE+'/toolchain/go')['files'] == len(manifest),
              'extra installed toolchain inputs')
    return manifest


def inspect(root):
    """Returns independently reviewable manifests, never writes or invokes Go."""
    root = Path(root)
    plan = dict(schema_version=1, decision='hold', execution_authorized=False,
                blockers=[], inputs=[], seed=[], archives=[], toolchain=[], closure={},
                outcomes={}, retained_bytes=0, free_bytes=0, units=[])
    try:
        previous, _ = P.inspect(root)
        plan['outcomes'] = previous['outcomes']
        plan['retained_bytes'] = sum(previous['measured'][b]['charged_bytes'] for b in P.BUILDS)
        plan['free_bytes'] = previous['free_bytes']
        plan['blockers'] = [b for b in previous['blockers'] if b != 'combined_retention_budget']
        reconstruction = json.loads(P.read_bytes(root, BASE + '/source-reconstruction.json'))
        P.require(all(reconstruction['hashes'][k] == v for k, v in SOURCE_HASHES.items()),
                  'retained source identity binding drift')
        source_archive = P.read_bytes(root, BASE + '/source-snapshot.stdout', 32*P.MIB)
        P.require(sha(source_archive) == reconstruction['hashes']['source-snapshot.stdout'],
                  'source archive binding')
        with tarfile.open(fileobj=io.BytesIO(source_archive)) as archive:
            for path in P.INPUTS:
                data = P.read_bytes(root, P.SOURCE + '/' + path)
                if path in ('go.mod', 'go.sum'):
                    P.require(sha(data) == reconstruction['hashes']['source/' + path], 'patched module binding')
                else:
                    member = archive.getmember(path)
                    P.require(member.isfile() and member.size <= 8*P.MIB and
                              archive.extractfile(member).read(8*P.MIB) == data,
                              'selected source archive mismatch')
                plan['inputs'].append(entry(P.SOURCE + '/' + path, data, 's/' + path))
        # Reject added package files that a minimal skeleton would otherwise omit.
        fd = P._open(root, P.SOURCE + '/internal/index', directory=True)
        try:
            P.require(sorted(os.listdir(fd)) == ['digest.go', 'digest_test.go'], 'index package drift')
        finally:
            os.close(fd)
        mod = P.read_bytes(root, P.SOURCE + '/go.mod')
        sums_data = P.read_bytes(root, P.SOURCE + '/go.sum')
        sums = {(m, v): h for m, v, h in (line.split() for line in sums_data.decode().splitlines())}
        versions = requirements(mod)

        def load(module, version):
            path = CACHE + escape(module) + '/@v/' + escape(version) + '.zip'
            data = P.read_bytes(root, path, 64*P.MIB)
            files = archive_files(data, module, version, sums)
            plan['archives'].append(entry(path, data, 'archive'))
            download = 'm/cache/download/' + escape(module) + '/@v/' + escape(version)
            plan['seed'].append(entry(path, data, download + '.zip'))
            ziphash = P.read_bytes(root, path[:-4] + '.ziphash')
            P.require(ziphash.decode().strip() == sums[(module, version)], 'ziphash mismatch')
            plan['seed'].append(entry(path[:-4] + '.ziphash', ziphash, download + '.ziphash'))
            return files

        initial = {i for p in P.INPUTS if p.endswith('.go')
                   for i in imports(P.read_bytes(root, P.SOURCE + '/' + p)) if '.' in i.split('/')[0]}
        modules, plan['closure'] = closure(initial, versions, load)
        for module, files in sorted(modules.items()):
            version = versions[module]
            directories = {package[len(module):].lstrip('/') for package in plan['closure']
                           if package == module or package.startswith(module+'/')}
            path = CACHE + escape(module) + '/@v/' + escape(version) + '.zip'
            for name, content in sorted(files.items()):
                if (name in ('go.mod', 'LICENSE') or name.rpartition('/')[0] in directories
                        and name.endswith(('.go', '.s', '.S', '.h', '.syso'))
                        and not name.endswith('_test.go')):
                    plan['seed'].append(entry(path, content,
                        'm/' + escape(module) + '@' + escape(version) + '/' + name,
                        module + '@' + version + '/' + name))
        # Original root go.mod is retained. Seed checksum-verified .mod metadata
        # for its lazy graph, including transitive .mod entries in original sum.
        for (module, version), expected in sorted(sums.items()):
            if not version.endswith('/go.mod'):
                continue
            version = version[:-7]
            path = CACHE + escape(module) + '/@v/' + escape(version) + '.mod'
            data = P.read_bytes(root, path)
            P.require(h1({'go.mod': data}) == expected, 'go.mod checksum mismatch')
            plan['seed'].append(entry(path, data,
                'm/cache/download/' + escape(module) + '/@v/' + escape(version) + '.mod'))
        plan['toolchain'] = tool_manifest(root)
        for name in P.SELECTORS:
            P.require(len(os.fsencode(root.resolve()/SCOPES[name]/'t')) <= 80,
                      'private temp path headroom')
            u = P.fixed_unit(name, charge(plan['inputs']), charge(plan['seed']))
            tests = re.findall(r'^func (Test\w+)\(t \*testing.T\)',
                               P.read_bytes(root, P.SOURCE+'/internal/index/digest_test.go').decode(), re.M)
            u.update(scope=SCOPES[name], compile_cache_bytes=COMPILE, temp_bytes=TEMP,
                     receipt_bytes=RECEIPTS, inspection_reserve_bytes=RESERVE,
                     tests=[test for test in tests if re.fullmatch(P.SELECTORS[name], test)])
            u['total_bytes'] = u['input_bytes'] + u['module_seed_bytes'] + COMPILE + TEMP + RECEIPTS + RESERVE
            u['environment'] = {k: v.replace('tmp/rv/ixs', SCOPES[name]).replace('tmp/rv/ixd', SCOPES[name])
                                for k, v in u['environment'].items()}
            plan['units'].append(u)
        selected = [test for unit in plan['units'] for test in unit['tests']]
        P.require(len(selected) == len(set(selected)) and set(selected) == set(tests) and
                  all(u['tests'] for u in plan['units']), 'fixed test selectors incomplete/overlap')
        if plan['retained_bytes'] + sum(u['total_bytes'] for u in plan['units']) >= 16*P.GIB:
            plan['blockers'].append('combined_retention_budget')
        if plan['free_bytes'] - sum(u['total_bytes'] for u in plan['units']) < 17*P.GIB:
            plan['blockers'].append('free_space_writer_margin')
    except (OSError, ValueError, KeyError, TypeError, AttributeError,
            tarfile.TarError, zipfile.BadZipFile) as exc:
        plan['blockers'].append('integrity_or_closure_hold: ' + str(exc)[:180])
    plan['decision'] = 'hold' if plan['blockers'] else 'ready_for_parent_review'
    return plan


def exclusive_json(root, relative, value):
    parent, _, name = relative.rpartition('/')
    fd = P._open(root, parent, directory=True)
    try:
        out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(out, 'w') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)


def terminate_owned(process):
    """Signal only the group created for this child; do not use supplied PIDs."""
    if process.poll() is not None:
        return
    P.require(os.getpgid(process.pid) == process.pid, 'child process group ownership')
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def check_bounds(root, scope, unit, retained):
    def charged(path):
        measured = P.measure(root, path)
        return max(measured['charged_bytes'], measured['copy_bytes'])
    used = charged(scope)
    P.require(used + RESERVE < unit['total_bytes'], 'scope write ceiling')
    for leaf, bound in (('c', COMPILE), ('t', TEMP)):
        P.require(charged(scope + '/' + leaf) < bound, 'cache/temp ceiling')
    fs = os.statvfs(root)
    P.require(fs.f_bavail*fs.f_frsize >= 17*P.GIB and retained + used + RESERVE < 16*P.GIB,
              'retention/free ceiling')


def terminal_group(process):
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return
    raise P.PlanError('owned group still present; unknown descendants, no replay')


def unit_outcome(unit, returncode, data):
    result = P.classify(dict(returncode=returncode, disposition='succeeded',
                            finished_at=time.time(), stdout_sha256=sha(data)), data)
    if result['disposition'] == 'successful':
        events = [json.loads(line) for line in data.splitlines() if line.strip()]
        names = [e['Test'] for e in events if e.get('Action') == 'pass' and e.get('Test')]
        if (result['packages_passed'] != 1 or
                any(e.get('Package') != TARGET for e in events) or
                not names or any(not re.match(P.SELECTORS[unit['id']], n.split('/')[0]) for n in names) or
                'tests' in unit and set(n.split('/')[0] for n in names) != set(unit['tests'])):
            result['disposition'] = 'unknown'
    return result


def execute(root, plan, *, review=None, unit_id=None, execute=False):
    """Parent API only. Fresh exact inspection/review, exclusive one-shot scope.

    review binds HEAD, plan hash, selected unit, fixed deadline and independent
    terminal/session/retention/routing checks. Its form is not proof of contents.
    Worker must never call this with execute=True. No shell/model argv accepted.
    """
    if not execute:
        return dict(decision='zero_call', calls=0)
    root = Path(root).resolve()
    P.require(unit_id in SCOPES and plan['decision'] == 'ready_for_parent_review', 'held unit')
    P.require(type(review) is dict and set(review) == {'head', 'plan_sha256', 'unit_id',
              'deadline_epoch', 'independent_checks'} and review['unit_id'] == unit_id and
              review['plan_sha256'] == binding(plan) and
              type(review['independent_checks']) is dict and
              set(review['independent_checks']) == PARENT_CHECKS and
              all(v is True for v in review['independent_checks'].values()),
              'explicit parent binding required')
    # Git is the only prelaunch subprocess; no Go version probe/download.
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    P.require(review['head'] == head, 'review HEAD drift')
    P.require(type(review['deadline_epoch']) in (int, float) and
              time.time()+600 < review['deadline_epoch'] <= 1791682200,
              'fixed owner deadline/unit wall')
    fresh = inspect(root)
    P.require({k: v for k, v in fresh.items() if k != 'free_bytes'} ==
              {k: v for k, v in plan.items() if k != 'free_bytes'}, 'fresh input/budget/outcome drift')
    unit = next(u for u in fresh['units'] if u['id'] == unit_id)
    scope = unit['scope']
    if (root/scope).exists():
        raise FileExistsError('unit reservation exists; never replay')
    for other in SCOPES.values():
        if (root/other).exists():
            terminal = json.loads(P.read_bytes(root, other+'/finished.json'))
            previous_unit = next(u for u in fresh['units'] if u['scope'] == other)
            output = P.read_bytes(root, other+'/stdout', RECEIPTS)
            error = P.read_bytes(root, other+'/stderr', RECEIPTS)
            P.require(terminal['owned_group_terminal'] is True and
                      terminal['stdout_sha256'] == sha(output) and
                      terminal['stderr_sha256'] == sha(error) and
                      terminal['outcome'] == unit_outcome(previous_unit, terminal['returncode'], output) and
                      terminal['outcome']['disposition'] == 'successful' and
                      not (root/(other+'/held.json')).exists(), 'prior unit unknown/held')
            measured = P.measure(root, other)
            fresh['retained_bytes'] += max(measured['charged_bytes'], measured['copy_bytes'])
    P.require(fresh['retained_bytes'] + unit['total_bytes'] < 16*P.GIB, 'prior unit retention ceiling')
    parent = P._open(root, 'tmp/rv', directory=True)
    try:
        os.mkdir(scope.split('/')[-1], 0o700, dir_fd=parent)  # existence = never replay
    finally:
        os.close(parent)
    directory = root/scope
    exclusive_json(root, scope+'/reserved.json', dict(unit_id=unit_id, review=review))
    process = None
    try:
        for leaf in ('h', 'c', 'm', 'p', 't', 'x', 'y', 'd', 's'):
            (directory/leaf).mkdir(mode=0o700)
        for e in fresh['inputs'] + fresh['seed']:
            data = P.read_bytes(root, e['path'], 64*P.MIB)
            if 'member' in e:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    data = archive.read(e['member'])
            P.require(sha(data) == e['sha256'] and len(data) == e['bytes'], 'copy input drift')
            dest = directory/e['destination']
            dest.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            with dest.open('xb') as stream:
                stream.write(data)
            dest.chmod(0o400)
            check_bounds(root, scope, unit, fresh['retained_bytes'])
        env = {k: str(root/v) if v.startswith('tmp/') else v for k, v in unit['environment'].items()}
        env.update(PATH=str((root/P.TOOL).parent), GOOS='darwin', GOARCH='arm64', GOTELEMETRY='off')
        argv = [str(root/P.TOOL)] + unit['argv'][1:]
        check_bounds(root, scope, unit, fresh['retained_bytes'])
        exclusive_json(root, scope+'/started.json', dict(argv=argv, started_at=time.time()))
        with (directory/'stdout').open('xb') as out, (directory/'stderr').open('xb') as err:
            os.fchmod(out.fileno(), 0o600)
            os.fchmod(err.fileno(), 0o600)
            process = subprocess.Popen(argv, cwd=directory/'s', env=env, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, start_new_session=True)
            deadline = min(time.monotonic()+600, time.monotonic()+review['deadline_epoch']-time.time())
            pipes = {process.stdout: out, process.stderr: err}
            recorded = 0
            try:
                while process.poll() is None or pipes:
                    check_bounds(root, scope, unit, fresh['retained_bytes'])
                    P.require(time.monotonic() < deadline, 'unit wall deadline')
                    ready, _, _ = select.select(list(pipes), [], [], 0.1)
                    for pipe in ready:
                        data = os.read(pipe.fileno(), 65536)
                        if not data:
                            pipe.close()
                            del pipes[pipe]
                            continue
                        P.require(recorded + len(data) < RECEIPTS-65536, 'receipt ceiling')
                        pipes[pipe].write(data)
                        recorded += len(data)
            finally:
                for pipe in pipes:
                    pipe.close()
        terminal_group(process)
        check_bounds(root, scope, unit, fresh['retained_bytes'])
        data = P.read_bytes(root, scope+'/stdout', RECEIPTS)
        outcome = unit_outcome(unit, process.returncode, data)
        exclusive_json(root, scope+'/finished.json', dict(returncode=process.returncode,
                       stdout_sha256=sha(data), stderr_sha256=sha(P.read_bytes(root, scope+'/stderr', RECEIPTS)),
                       owned_group_terminal=True, finished_at=time.time(), outcome=outcome))
        return outcome
    except BaseException:
        if process is not None:
            terminate_owned(process)
        # Reservation and started receipt survive every failure. No fallback/retry.
        exclusive_json(root, scope+'/held.json', dict(decision='hold_no_replay'))
        raise
