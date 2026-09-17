"""Bounded, read-only inspection of the fixed saved diagnosis directory."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

ROOT = Path(__file__).absolute().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.local_experiment.query_diagnose_report import contract as C

STATE = ROOT / "tmp/local-experiment/query-diagnose"
MAX_REQUEST_BYTES = 1024
MAX_DIRECTORY_ENTRIES = 1024
TOKEN = re.compile(r"[0-9a-f]{32}\Z")
ARTIFACTS = {"run.lock", "summary.json", "spans.jsonl"}


class InspectionError(Exception):
    def __init__(self, state="invalid", code=None):
        self.state = state
        self.code = code


def retain(original, error):
    if original is None or (isinstance(original, Exception) and not isinstance(error, Exception)):
        return error
    return original


def fingerprint(value):
    return (value.st_dev, value.st_ino, value.st_uid, value.st_mode,
            value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def private(value, directory=False):
    valid_kind = stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode)
    if (not valid_kind or value.st_uid != os.getuid()
            or stat.S_IMODE(value.st_mode) != (0o700 if directory else 0o600)
            or (not directory and value.st_nlink != 1)):
        raise InspectionError()


def close_preserving(descriptors, original=None):
    for fd in reversed(descriptors):
        try:
            os.close(fd)
        except BaseException as error:
            original = retain(original, error)
    return original


def open_root():
    """Walk every absolute parent via directory FDs; never follow a symlink."""
    if not STATE.is_absolute() or ".." in STATE.parts:
        raise InspectionError()
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    original = None
    try:
        for component in STATE.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            old, fd = fd, child
            os.close(old)
        private(os.fstat(fd), directory=True)
        return fd
    except BaseException as error:
        original = error
    original = close_preserving([fd], original)
    raise original


def listing(fd, maximum=None):
    maximum = MAX_DIRECTORY_ENTRIES if maximum is None else maximum
    names, complete, original = [], True, None
    iterator = os.scandir(fd)
    try:
        for item in iterator:
            if len(names) == maximum:
                complete = False
                break
            names.append(item.name)
    except BaseException as error:
        original = error
    finally:
        try:
            iterator.close()
        except BaseException as error:
            original = retain(original, error)
    if original is not None:
        raise original
    return tuple(sorted(names)), complete


class Budget:
    def __init__(self):
        self.remaining = C.MAX_TOTAL_BYTES

    def consume(self, count):
        if count > self.remaining:
            raise InspectionError("incomplete", "REPORT_READ_LIMIT")
        self.remaining -= count


def read_file(directory, name, maximum, budget):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    result, original = None, None
    try:
        before = os.fstat(fd)
        private(before)
        if before.st_size > maximum:
            raise InspectionError("incomplete", "REPORT_READ_LIMIT")
        if before.st_size > budget.remaining:
            raise InspectionError("incomplete", "REPORT_READ_LIMIT")
        data = bytearray()
        while True:
            block = os.read(fd, min(65536, maximum + 1 - len(data)))
            if not block:
                break
            budget.consume(len(block))
            data.extend(block)
            if len(data) > maximum:
                raise InspectionError("incomplete", "REPORT_READ_LIMIT")
        if fingerprint(before) != fingerprint(os.fstat(fd)) or len(data) != before.st_size:
            raise InspectionError()
        result = (bytes(data), fingerprint(before), hashlib.sha256(data).hexdigest())
    except BaseException as error:
        original = error
    original = close_preserving([fd], original)
    if original is not None:
        raise original
    return result


def decode(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate")
            value[key] = item
        return value

    def constant(_):
        raise ValueError("constant")

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    if type(value) is not dict:
        raise ValueError("object")
    return value


def payload(directory, budget):
    names, complete = listing(directory, 4)
    if not complete or not set(names) <= ARTIFACTS:
        raise InspectionError()
    files, values = {}, {}
    for name, maximum in (("summary.json", C.MAX_SUMMARY_BYTES), ("spans.jsonl", C.MAX_SPANS_BYTES)):
        try:
            raw, mark, digest = read_file(directory, name, maximum, budget)
        except FileNotFoundError:
            files[name], values[name] = None, None
            continue
        files[name] = (mark, digest)
        values[name] = decode(raw) if name == "summary.json" else [decode(line) for line in raw.splitlines()]
    return names, files, values


def check_files(rootfd, item, budget):
    token, directory_mark, names, files, lockfd, lockmark = item
    directory = os.open(token, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=rootfd)
    original = None
    try:
        if fingerprint(os.fstat(directory)) != directory_mark:
            raise InspectionError()
        if listing(directory, 4) != (names, True):
            raise InspectionError()
        if fingerprint(os.fstat(lockfd)) != lockmark:
            raise InspectionError()
        if fingerprint(os.stat("run.lock", dir_fd=directory, follow_symlinks=False)) != lockmark:
            raise InspectionError()
        for name, saved in files.items():
            if saved is None:
                try:
                    os.stat(name, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise InspectionError()
            _, mark, digest = read_file(directory, name,
                                        C.MAX_SUMMARY_BYTES if name == "summary.json" else C.MAX_SPANS_BYTES, budget)
            if (mark, digest) != saved:
                raise InspectionError()
    except BaseException as error:
        original = error
    original = close_preserving([directory], original)
    if original is not None:
        raise original


def project(token, state, summary=None, spans=None):
    return C.project_run(token, summary, spans, read_state=state)


def inspect_token(rootfd, token, budget, held, pending):
    directory = os.open(token, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=rootfd)
    lockfd, original, result = None, None, None
    try:
        private(os.fstat(directory), directory=True)
        directory_mark = fingerprint(os.fstat(directory))
        try:
            lockfd = os.open("run.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            result = project(token, "incomplete")
        else:
            private(os.fstat(lockfd))
            lockmark = fingerprint(os.fstat(lockfd))
            if os.fstat(lockfd).st_size != 0:
                raise InspectionError()
            try:
                fcntl.flock(lockfd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                # A held old inode is not evidence that the current path is busy.
                # Directory contents may legitimately change under the live writer.
                current_directory = os.stat(token, dir_fd=rootfd, follow_symlinks=False)
                private(current_directory, directory=True)
                if (fingerprint(current_directory)[:4] != directory_mark[:4]
                        or fingerprint(os.fstat(directory))[:4] != directory_mark[:4]
                        or fingerprint(os.fstat(lockfd)) != lockmark
                        or fingerprint(os.stat("run.lock", dir_fd=directory, follow_symlinks=False)) != lockmark):
                    raise InspectionError()
                result = project(token, "active")
            else:
                held.append(lockfd)
                names, files, values = payload(directory, budget)
                pending.append((token, directory_mark, names, files, lockfd, lockmark))
                lockfd = None  # collect owns the shared lock until final validation.
                result = project(token, "stable" if values["summary.json"] is not None else "incomplete",
                                 values["summary.json"], values["spans.jsonl"])
    except BaseException as error:
        original = error
    finally:
        original = close_preserving([directory] + ([lockfd] if lockfd is not None and lockfd not in held else []), original)
    if original is not None:
        raise original
    return result


def state_for(error):
    if isinstance(error, InspectionError):
        return error.state
    if isinstance(error, FileNotFoundError):
        return "incomplete"
    return "invalid"


def collect(requested_token=None):
    if requested_token is not None and (type(requested_token) is not str or TOKEN.fullmatch(requested_token) is None):
        raise ValueError("request")
    inventory = {"root": "not-observed", "complete": False, "inspected_entries": 0,
                 "unrecognized_entries": 0, "error_code": "REPORT_ROOT_INVALID"}
    entries, held, pending = {}, [], []
    rootfd, original, report = None, None, None
    try:
        try:
            rootfd = open_root()
        except FileNotFoundError:
            inventory.update(root="absent", complete=True, error_code=None)
            if requested_token is not None:
                entries[requested_token] = project(requested_token, "missing")
        except Exception:
            pass
        else:
            rootmark = fingerprint(os.fstat(rootfd))
            names, complete = listing(rootfd)
            tokens = sorted(name for name in names if TOKEN.fullmatch(name))
            inventory.update(root="present", complete=complete,
                             unrecognized_entries=len(names) - len(tokens),
                             error_code=None if complete else "REPORT_ENTRY_LIMIT")
            if inventory["unrecognized_entries"]:
                inventory.update(complete=False, error_code=inventory["error_code"] or "REPORT_UNRECOGNIZED_ENTRIES")
            if requested_token is None:
                selected = tokens[:C.MAX_RUNS]
                if len(tokens) > C.MAX_RUNS:
                    inventory.update(complete=False, error_code="REPORT_ENTRY_LIMIT")
            else:
                selected = [requested_token] if requested_token in tokens else []
                if not selected:
                    entries[requested_token] = project(requested_token, "missing" if complete else "incomplete")
            budget = Budget()
            for token in selected:
                try:
                    entries[token] = inspect_token(rootfd, token, budget, held, pending)
                except Exception as error:
                    entries[token] = project(token, state_for(error))
                    if isinstance(error, InspectionError) and error.code:
                        inventory.update(complete=False, error_code=error.code)
            for item in pending:
                try:
                    check_files(rootfd, item, budget)
                except Exception as error:
                    entries[item[0]] = project(item[0], state_for(error))
                    if isinstance(error, InspectionError) and error.code:
                        inventory.update(complete=False, error_code=error.code)
            if listing(rootfd) != (names, complete) or fingerprint(os.fstat(rootfd)) != rootmark:
                inventory.update(complete=False, error_code="REPORT_ROOT_CHANGED")
            reopened = open_root()
            reopen_error = None
            try:
                if fingerprint(os.fstat(reopened)) != rootmark:
                    inventory.update(complete=False, error_code="REPORT_ROOT_CHANGED")
            except BaseException as error:
                reopen_error = error
            finally:
                reopen_error = close_preserving([reopened], reopen_error)
            if reopen_error is not None:
                raise reopen_error
        if requested_token is not None and not entries:
            entries[requested_token] = project(requested_token, "incomplete")
        inventory["inspected_entries"] = len(entries)
        report = C.build_report(list(entries.values()), requested_token=requested_token, inventory=inventory)
        C.validate_public(report)
    except BaseException as error:
        original = error
    finally:
        original = close_preserving(([rootfd] if rootfd is not None else []) + held, original)
    if original is not None:
        raise original
    return report


def fallback(requested_token=None, error_code="REPORT_ROOT_INVALID"):
    rows = [] if requested_token is None else [project(requested_token, "incomplete")]
    return C.build_report(rows, requested_token=requested_token,
                          inventory={"root": "not-observed", "complete": False,
                                     "inspected_entries": len(rows), "unrecognized_entries": 0,
                                     "error_code": error_code})


def main(argv=None, *, stdin=None, stdout=None):
    stdin = sys.stdin.buffer if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    requested, code, report, inspected = None, 1, None, False
    try:
        args = sys.argv[1:] if argv is None else argv
        if args != ["report", "--request-stdin"]:
            raise ValueError("arguments")
        raw = stdin.read(MAX_REQUEST_BYTES + 1)
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("request")
        request = decode(raw)
        if type(request) is not dict or set(request) != {"token"}:
            raise ValueError("request")
        requested = request["token"]
        if requested is not None and (type(requested) is not str or TOKEN.fullmatch(requested) is None):
            requested = None
            raise ValueError("request")
        inspected = True
        report = collect(requested)
        code = 0 if report["command_result"] == "complete" else 1
    except Exception as error:
        public_limit = getattr(C, "PublicLimitError", ())
        reason = "REPORT_PUBLIC_LIMIT" if isinstance(error, public_limit) else "REPORT_ROOT_INVALID" if inspected else "REPORT_REQUEST_INVALID"
        report = fallback(requested, reason)
    except BaseException:
        report, code = fallback(requested), 130
    try:
        C.validate_public(report)
        encoded = json.dumps(report, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False) + "\n"
        if len(encoded.encode("utf-8")) > C.MAX_PUBLIC_BYTES:
            code = 130 if code == 130 else 1
            encoded = json.dumps(fallback(requested, "REPORT_PUBLIC_LIMIT"), sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n"
        stdout.write(encoded)
        stdout.flush()
    except Exception:
        return 1 if code != 130 else 130
    except BaseException:
        return 130
    return code


if __name__ == "__main__":
    raise SystemExit(main())
