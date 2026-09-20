#!/usr/bin/env python3
"""Read only the four fixed Reader receipt roots; never allocate a run."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import sys

ROOT = Path(__file__).absolute().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.local_experiment.query_reads import contract as C
from tools.local_experiment.query_diagnose_report import run as FS

STATE = ROOT / 'tmp/local-experiment'
MAX_DIRECTORY_ENTRIES = 1024
ARTIFACTS = {'run.lock', 'summary.json'}


def open_root(path):
    """Traverse absolute parents by descriptor, without following symlinks."""
    if not path.is_absolute() or '..' in path.parts:
        raise FS.InspectionError()
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    original = None
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            old, fd = fd, child
            os.close(old)
        FS.private(os.fstat(fd), directory=True)
        return fd
    except BaseException as error:
        original = error
    raise FS.close_preserving([fd], original)


def issue(error, *, changed=False):
    if isinstance(error, FS.InspectionError) and error.code == 'REPORT_READ_LIMIT':
        return 'incomplete', 'READS_READ_LIMIT'
    if changed:
        return 'incomplete', 'READS_RECEIPT_CHANGED'
    if isinstance(error, FileNotFoundError):
        return 'incomplete', 'READS_RECEIPT_MISSING'
    return 'invalid', 'READS_RECEIPT_INVALID'


def payload(directory, budget):
    names, complete = FS.listing(directory, 3)
    if not complete or not set(names) <= ARTIFACTS:
        raise FS.InspectionError()
    try:
        raw, mark, digest = FS.read_file(directory, 'summary.json', C.MAX_SUMMARY_BYTES, budget)
    except FileNotFoundError:
        return names, None, None
    return names, (mark, digest), FS.decode(raw)


def inspect_token(rootfd, version, token, budget, held, pending):
    directory = os.open(token, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=rootfd)
    lockfd, original, result = None, None, None
    try:
        FS.private(os.fstat(directory), directory=True)
        directory_mark = FS.fingerprint(os.fstat(directory))
        try:
            lockfd = os.open('run.lock', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            result = C.minimal(version, token, 'incomplete', 'READS_LOCK_MISSING')
        else:
            FS.private(os.fstat(lockfd))
            lockmark = FS.fingerprint(os.fstat(lockfd))
            if os.fstat(lockfd).st_size != 0:
                raise FS.InspectionError()
            try:
                fcntl.flock(lockfd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                current = os.stat(token, dir_fd=rootfd, follow_symlinks=False)
                FS.private(current, directory=True)
                if (FS.fingerprint(current)[:4] != directory_mark[:4]
                        or FS.fingerprint(os.fstat(directory))[:4] != directory_mark[:4]
                        or FS.fingerprint(os.fstat(lockfd)) != lockmark
                        or FS.fingerprint(os.stat('run.lock', dir_fd=directory, follow_symlinks=False)) != lockmark):
                    raise FS.InspectionError()
                result = C.minimal(version, token, 'active')
            else:
                held.append(lockfd)
                names, filemark, saved = payload(directory, budget)
                pending.append((version, token, rootfd, directory_mark, names, filemark, lockfd, lockmark))
                lockfd = None
                result = (C.project_run(version, token, saved) if saved is not None else
                          C.minimal(version, token, 'incomplete', 'READS_RECEIPT_MISSING'))
    except BaseException as error:
        original = error
    original = FS.close_preserving([directory] + ([lockfd] if lockfd is not None and lockfd not in held else []), original)
    if original is not None:
        raise original
    return result


def check_files(item, budget):
    version, token, rootfd, directory_mark, names, filemark, lockfd, lockmark = item
    directory = os.open(token, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=rootfd)
    original = None
    try:
        if (FS.fingerprint(os.fstat(directory)) != directory_mark
                or FS.listing(directory, 3) != (names, True)
                or FS.fingerprint(os.fstat(lockfd)) != lockmark
                or FS.fingerprint(os.stat('run.lock', dir_fd=directory, follow_symlinks=False)) != lockmark):
            raise FS.InspectionError()
        if filemark is None:
            try:
                os.stat('summary.json', dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise FS.InspectionError()
        else:
            _, mark, digest = FS.read_file(directory, 'summary.json', C.MAX_SUMMARY_BYTES, budget)
            if (mark, digest) != filemark:
                raise FS.InspectionError()
    except BaseException as error:
        original = error
    original = FS.close_preserving([directory], original)
    if original is not None:
        raise original


def collect(requested_token=None, *, state=None):
    C.normalize_request({'command':'reads', 'token':requested_token})
    state = STATE if state is None else Path(state)
    entries, inventories, roots, held, pending = {}, [], [], [], []
    budget = FS.Budget()
    budget.remaining = C.MAX_TOTAL_BYTES
    original, report = None, None
    try:
        for version, name in C.ROOT_NAMES.items():
            inventory = C.inventory(version)
            inventories.append(inventory)
            path = state / name
            try:
                rootfd = open_root(path)
            except FileNotFoundError:
                inventory.update(root='absent', complete=True, error_code=None)
                roots.append((version, path, None, None, None))
                continue
            except Exception:
                continue
            roots.append((version, path, rootfd, None, None))
            inventory.update(root='present')
            try:
                mark = FS.fingerprint(os.fstat(rootfd))
                listed = FS.listing(rootfd, MAX_DIRECTORY_ENTRIES)
                roots[-1] = (version, path, rootfd, mark, listed)
                names, complete = listed
                tokens = [name for name in names if C.TOKEN.fullmatch(name)]
                unknown = len(names) - len(tokens)
                inventory.update(complete=complete and not unknown, unrecognized_entries=unknown,
                    error_code='READS_ENTRY_LIMIT' if not complete else 'READS_UNRECOGNIZED_ENTRIES' if unknown else None)
                selected = tokens if requested_token is None else [name for name in tokens if name == requested_token]
                remaining = C.MAX_RUNS - len(entries)
                if len(selected) > remaining:
                    selected = selected[:remaining]
                    inventory.update(complete=False, error_code='READS_ENTRY_LIMIT')
                for token in selected:
                    try:
                        entry = inspect_token(rootfd, version, token, budget, held, pending)
                    except Exception as error:
                        status, code = issue(error)
                        entry = C.minimal(version, token, status, code)
                        if code == 'READS_READ_LIMIT':
                            inventory.update(complete=False, error_code=code)
                    entries[(version, token)] = entry
            except Exception:
                inventory.update(complete=False, error_code='READS_ROOT_INVALID')
        for item in pending:
            try:
                check_files(item, budget)
            except Exception as error:
                status, code = issue(error, changed=True)
                entries[item[:2]] = C.minimal(*item[:2], status, code)
                if code == 'READS_READ_LIMIT':
                    inventories[item[0]-1].update(complete=False, error_code=code)
        for version, path, rootfd, mark, listed in roots:
            reopened, reopen_error = None, None
            try:
                if rootfd is None:
                    try:
                        reopened = open_root(path)
                    except FileNotFoundError:
                        continue
                    raise FS.InspectionError()
                if mark is None or FS.fingerprint(os.fstat(rootfd)) != mark or FS.listing(rootfd, MAX_DIRECTORY_ENTRIES) != listed:
                    raise FS.InspectionError()
                reopened = open_root(path)
                if FS.fingerprint(os.fstat(reopened)) != mark:
                    raise FS.InspectionError()
            except Exception:
                inventories[version-1].update(complete=False, error_code='READS_ROOT_CHANGED')
                for key in entries:
                    if key[0] == version:
                        entries[key] = C.minimal(*key, 'incomplete', 'READS_ROOT_CHANGED')
            except BaseException as error:
                reopen_error = error
            reopen_error = FS.close_preserving([reopened] if reopened is not None else [], reopen_error)
            if reopen_error is not None:
                raise reopen_error
        if requested_token is not None and not entries:
            for inventory in inventories:
                inventory.update(complete=False, error_code=inventory['error_code'] or 'READS_TOKEN_MISSING')
        for inventory in inventories:
            inventory['inspected_entries'] = sum(key[0] == inventory['reader_version'] for key in entries)
        report = C.build_report(list(entries.values()), requested_token, inventories)
    except BaseException as error:
        original = error
    original = FS.close_preserving([row[2] for row in roots if row[2] is not None] + held, original)
    if original is not None:
        raise original
    return report


def fallback(requested_token=None, error_code='READS_ROOT_INVALID'):
    return C.build_report([], requested_token, [C.inventory(version, error=error_code) for version in C.ROOT_NAMES])


def main(argv=None, *, stdin=None, stdout=None):
    stdin, stdout = (sys.stdin.buffer if stdin is None else stdin), (sys.stdout if stdout is None else stdout)
    requested, started, original = None, False, None
    try:
        if (sys.argv[1:] if argv is None else argv) != ['reads', '--request-stdin']:
            raise C.ReadsError('READS_REQUEST_INVALID')
        raw = stdin.read(1025)
        if type(raw) is str:
            raw = raw.encode('utf-8')
        requested = C.parse_request(raw)['token']
        started = True
        report = collect(requested)
    except Exception as error:
        code = 'READS_PUBLIC_LIMIT' if isinstance(error, C.PublicLimitError) else 'READS_ROOT_INVALID' if started else 'READS_REQUEST_INVALID'
        report = fallback(requested, code)
    except BaseException as error:
        original, report = error, fallback(requested)
    try:
        C.validate_public(report)
        stdout.write(C.canonical(report).decode('ascii') + '\n')
        stdout.flush()
    except BaseException as error:
        original = FS.retain(original, error)
    if original is not None:
        if not isinstance(original, Exception):
            raise original
        return 1
    return 0 if report['command_result'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
