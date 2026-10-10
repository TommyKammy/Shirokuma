"""Evidence-only small-unit proposals. No executor, downloads or admission.

Cache/temp limits are prospective ceilings, not measured compile predictions.
The existing combined retention ceiling is not increased by this planner.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Literal, TypedDict

GIB = 1024 ** 3
MIB = 1024 ** 2
SOURCE = 'tmp/local-experiment/flux-local-build/build-don5ufj3/source'
TOOL = 'tmp/local-experiment/flux-local-build/build-don5ufj3/toolchain/go/bin/go'
BUILDS = ('tmp/local-experiment/flux-local-build/build-aoa39q7w',
          'tmp/local-experiment/flux-local-build/build-don5ufj3')
SELECTORS = {
    'index-state': '^Test(WithIndex|NewDigester|Digester_(Add|Delete|Get|Has|Index|Len))$',
    'index-digest': '^Test(Digester_(String|WriteTo|Digest|Verify|sortedKeys|reset)|_writeLine)$',
}
INPUTS = ('go.mod', 'go.sum', 'api/go.mod',
          'internal/index/digest.go', 'internal/index/digest_test.go')
MEASURED_KEYS = set(BUILDS) | {'module_seed', 'retained_compile_cache',
                             'retained_temp', 'api_compile_cache', 'api_temp'}


class PlanError(ValueError):
    pass


class Measurement(TypedDict):
    files: int
    logical_bytes: int
    charged_bytes: int
    copy_bytes: int
    manifest_sha256: str


class Outcome(TypedDict):
    disposition: Literal['successful', 'known_terminated', 'unknown']
    tests_passed: int
    packages_passed: int
    packages_started: list[str]


class Unit(TypedDict):
    id: str
    argv: list[str]
    environment: dict[str, str]
    deadline_seconds: int
    input_bytes: int
    module_seed_bytes: int
    compile_cache_bytes: int
    temp_bytes: int
    receipt_bytes: int
    total_bytes: int


class Plan(TypedDict):
    schema_version: int
    decision: Literal['ready_for_parent_review', 'hold']
    execution_authorized: bool
    measured: dict[str, Measurement]
    free_bytes: int
    outcomes: dict[str, Outcome]
    units: list[Unit]
    blockers: list[str]


def require(value, message):
    if not value:
        raise PlanError(message)


def _open(root, relative, directory=False):
    """Open owned checkout-relative input without following any symlink."""
    parts = relative.split('/')
    require(parts and all(p not in ('', '.', '..') for p in parts), 'unsafe path')
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for i, part in enumerate(parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if i < len(parts)-1 or directory:
                flags |= os.O_DIRECTORY
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
            st = os.fstat(fd)
            require(st.st_uid == os.getuid() and
                    (stat.S_ISDIR(st.st_mode) if i < len(parts)-1 or directory
                     else stat.S_ISREG(st.st_mode)), 'unsafe input')
        result, fd = fd, None
        return result
    finally:
        if fd is not None:
            os.close(fd)


def read_bytes(root, relative, limit=8*MIB):
    fd = _open(root, relative)
    with os.fdopen(fd, 'rb') as stream:
        require(os.fstat(stream.fileno()).st_size <= limit, 'input too large')
        data = stream.read(limit+1)
        require(len(data) <= limit, 'input grew')
        return data


def measure(root, relative):
    """Strict read-only tree accounting; disappearance/denial is never zero.

    Manifest binds names/sizes/mtime, not module content integrity. Parent must
    independently verify selected source and module contents before any copying.
    Copy charge includes one 4KiB directory block and rounded regular-file bytes.
    """
    result = Measurement(files=0, logical_bytes=0, charged_bytes=0,
                         copy_bytes=0, manifest_sha256='')
    digest = hashlib.sha256()

    def visit(fd, prefix):
        result['copy_bytes'] += 4096
        with os.scandir(fd) as entries:
            names = sorted(entry.name for entry in entries)
        for name in names:
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            require(st.st_uid == os.getuid() and
                    (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)), 'unsafe entry')
            label = prefix+name
            digest.update(json.dumps([label, st.st_mode, st.st_size,
                                      st.st_blocks, st.st_mtime_ns]).encode())
            if stat.S_ISDIR(st.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    opened = os.fstat(child)
                    require((opened.st_dev, opened.st_ino) == (st.st_dev, st.st_ino), 'input changed')
                    visit(child, label+'/')
                finally:
                    os.close(child)
            else:
                result['files'] += 1
                result['logical_bytes'] += st.st_size
                result['charged_bytes'] += max(st.st_size, st.st_blocks*512)
                result['copy_bytes'] += ((st.st_size+4095)//4096)*4096
    fd = _open(root, relative, directory=True)
    try:
        visit(fd, '')
    finally:
        os.close(fd)
    result['manifest_sha256'] = digest.hexdigest()
    return result


def classify(finished, stdout):
    """Only matching terminal receipts establish disposition; passes are partial."""
    tests = packages = 0
    started = []
    try:
        events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        tests = sum(e.get('Action') == 'pass' and bool(e.get('Test')) for e in events)
        packages = sum(e.get('Action') == 'pass' and not e.get('Test') for e in events)
        started = sorted({e['Package'] for e in events if e.get('Action') == 'start'
                          and isinstance(e.get('Package'), str)})
        matched = finished['stdout_sha256'] == hashlib.sha256(stdout).hexdigest()
        rc = finished['returncode']
        require(type(rc) is int, 'invalid exit')
        disposition = 'unknown'
        if matched and finished.get('finished_at'):
            if (rc == 0 and finished.get('disposition') == 'succeeded' and tests and packages
                    and not finished.get('observation_error') and not finished.get('termination_error')
                    and not any(e.get('Action') == 'fail' for e in events)):
                disposition = 'successful'
            elif (rc < 0 and finished.get('disposition') == 'terminated_hold_no_replay'
                  and not finished.get('termination_error')):
                disposition = 'known_terminated'
        return Outcome(disposition=disposition, tests_passed=tests, packages_passed=packages,
                       packages_started=started)
    except (KeyError, ValueError, TypeError, AttributeError):
        return Outcome(disposition='unknown', tests_passed=tests, packages_passed=packages,
                       packages_started=started)


def fixed_unit(name, input_bytes, seed_bytes):
    require(name in SELECTORS, 'unapproved unit')
    scope = 'tmp/rv/ixs' if name == 'index-state' else 'tmp/rv/ixd'
    env = {key: scope+'/'+leaf for key, leaf in {
        'HOME': 'h', 'GOCACHE': 'c', 'GOMODCACHE': 'm', 'GOPATH': 'p',
        'TMPDIR': 't', 'TMP': 't', 'TEMP': 't', 'XDG_CACHE_HOME': 'x',
        'XDG_CONFIG_HOME': 'y', 'DOCKER_CONFIG': 'd'}.items()}
    env.update(GOENV='off', GOTOOLCHAIN='local', GOPROXY='off', GOSUMDB='off',
               GOWORK='off', CGO_ENABLED='0', GOMAXPROCS='2')
    return Unit(id=name, argv=[TOOL, 'test', '-mod=readonly', '-vet=off', '-p=1',
        '-count=1', '-json', '-timeout=120s', '-run='+SELECTORS[name], './internal/index'],
        environment=env, deadline_seconds=600, input_bytes=input_bytes,
        module_seed_bytes=seed_bytes, compile_cache_bytes=GIB, temp_bytes=512*MIB,
        receipt_bytes=16*MIB, total_bytes=input_bytes+seed_bytes+GIB+528*MIB)


def blockers(measured, free, outcomes, units):
    held = sum(measured[b]['charged_bytes'] for b in BUILDS)
    reasons = []
    if any(measured[b]['charged_bytes'] >= 8*GIB for b in BUILDS):
        reasons.append('retained_hard_limit')
    if any(o['disposition'] == 'unknown' for o in outcomes.values()):
        reasons.append('unknown_operation_read_only_hold')
    if 'github.com/fluxcd/source-controller/internal/index' in outcomes['final_root']['packages_started']:
        reasons.append('target_unit_previously_started_no_replay')
    # Both fresh units are retained, no reuse/deletion or counter reset assumed.
    total = sum(u['total_bytes'] for u in units)
    if any(u['total_bytes'] >= 7*GIB for u in units):
        reasons.append('unit_bulk_limit')
    if held+total >= 16*GIB:
        reasons.append('combined_retention_budget')
    if free-total < 17*GIB:
        reasons.append('free_space_writer_margin')
    return reasons


def propose(measured, free, outcomes, input_bytes):
    units = [fixed_unit(name, input_bytes, measured['module_seed']['copy_bytes'])
             for name in SELECTORS]
    reasons = blockers(measured, free, outcomes, units)
    plan = Plan(schema_version=1, decision='hold' if reasons else 'ready_for_parent_review',
                execution_authorized=False, measured=measured, free_bytes=free,
                outcomes=outcomes, units=units, blockers=reasons)
    validate(plan)
    return plan


def validate(plan):
    """Reject rehashed scope/argv/budget changes. Form is not parent approval."""
    require(type(plan) is dict and set(plan) == set(Plan.__annotations__), 'plan fields')
    require(type(plan['schema_version']) is int and plan['schema_version'] == 1, 'schema')
    require(plan['execution_authorized'] is False, 'execution prohibited')
    require(type(plan['free_bytes']) is int and plan['free_bytes'] >= 0, 'free bytes')
    measured = plan['measured']
    require(type(measured) is dict and set(measured) == MEASURED_KEYS, 'measurements')
    for m in measured.values():
        require(type(m) is dict and set(m) == set(Measurement.__annotations__), 'measurement fields')
        for key in ('files', 'logical_bytes', 'charged_bytes', 'copy_bytes'):
            require(type(m[key]) is int and m[key] >= 0, 'measurement number')
        require(m['charged_bytes'] >= m['logical_bytes'] and m['copy_bytes'] >= m['logical_bytes'],
                'measurement accounting')
        require(type(m['manifest_sha256']) is str and len(m['manifest_sha256']) == 64 and
                all(c in '0123456789abcdef' for c in m['manifest_sha256']), 'manifest hash')
    outcomes = plan['outcomes']
    require(type(outcomes) is dict and set(outcomes) == {'old_root', 'old_signature', 'final_root', 'api'},
            'outcome scope')
    for o in outcomes.values():
        require(type(o) is dict and set(o) == set(Outcome.__annotations__), 'outcome fields')
        require(o['disposition'] in ('successful', 'known_terminated', 'unknown'), 'disposition')
        for key in ('tests_passed', 'packages_passed'):
            require(type(o[key]) is int and o[key] >= 0, 'test count')
        require(type(o['packages_started']) is list and
                all(type(p) is str and p for p in o['packages_started']) and
                o['packages_started'] == sorted(set(o['packages_started'])), 'package starts')
        require(o['disposition'] != 'successful' or o['tests_passed'] > 0 and o['packages_passed'] > 0,
                'empty success')
    units = plan['units']
    require(type(units) is list and len(units) == len(SELECTORS), 'unit count')
    for u, name in zip(units, SELECTORS):
        require(type(u) is dict and set(u) == set(Unit.__annotations__), 'unit fields')
        require(type(u['input_bytes']) is int and 0 < u['input_bytes'] <= MIB, 'source budget')
        expected = fixed_unit(name, u['input_bytes'], measured['module_seed']['copy_bytes'])
        require(u == expected and all(type(u[k]) is int for k in expected if k.endswith('_bytes')
                                    or k == 'deadline_seconds'), 'fixed unit drift')
    reasons = blockers(measured, plan['free_bytes'], outcomes, units)
    require(plan['blockers'] == reasons and plan['decision'] ==
            ('hold' if reasons else 'ready_for_parent_review'), 'decision drift')


def inspect(root):
    """Read retained inputs only; return detailed proposal, never save or execute."""
    root = Path(root)
    measured = {b: measure(root, b) for b in BUILDS}
    measured['module_seed'] = measure(root, BUILDS[1]+'/gomodcache')
    for key, path in {'retained_compile_cache': BUILDS[1]+'/gocache',
                      'retained_temp': BUILDS[1]+'/temp',
                      'api_compile_cache': 'tmp/rv/api-w8dk1t4k/gocache',
                      'api_temp': 'tmp/rv/api-w8dk1t4k/temp'}.items():
        measured[key] = measure(root, path)
    locations = {'old_root': (BUILDS[0], 'upstream-unit-tests'),
                 'old_signature': (BUILDS[0], 'upstream-signature-unit-tests'),
                 'final_root': (BUILDS[1], 'upstream-unit-tests'),
                 'api': ('tmp/rv/api-w8dk1t4k', 'api-unit-tests')}
    outcomes = {}
    for key, (scope, stem) in locations.items():
        try:
            finished = json.loads(read_bytes(root, scope+'/'+stem+'.finished.json'))
            stdout = read_bytes(root, scope+'/'+stem+'.stdout')
            outcomes[key] = classify(finished, stdout)
        except (OSError, PlanError, ValueError):
            outcomes[key] = Outcome(disposition='unknown', tests_passed=0, packages_passed=0,
                                    packages_started=[])
    # Minimal Go module skeleton plus exact package files; no root main/controller.
    manifests = {p: hashlib.sha256(read_bytes(root, SOURCE+'/'+p)).hexdigest() for p in INPUTS}
    input_bytes = 8*4096 + sum(((len(read_bytes(root, SOURCE+'/'+p))+4095)//4096)*4096 for p in INPUTS)
    fs = os.statvfs(root)
    plan = propose(measured, fs.f_bavail*fs.f_frsize, outcomes, input_bytes)
    return plan, manifests
