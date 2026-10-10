"""Evidence-only pinned kustomize module repair; never invokes Go or a runner.

HTTPS identifies retrieved bytes, not publisher authenticity. All writes are
exclusive; a started fetch without a finished receipt is unknown, never retried.
No CLI or automatic fetch/build/graph-resolution entrypoint is provided.
"""
from __future__ import annotations

import difflib
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import time
import urllib.request

REVISION = '060e0d8dbc257a11cf49b9e626b63284dc385870'
BASE_URL = f'https://raw.githubusercontent.com/fluxcd/kustomize-controller/{REVISION}/'
MAX_RESPONSE = 2 * 1024 * 1024
TIMEOUT = 30
SCOPE_LIMIT = 64 * 1024 * 1024
COMBINED_LIMIT = 16 * 1024**3
FREE_MIN = 17 * 1024**3
# Parent-dated charges, not a fresh observation of the prohibited unknown scope.
PARENT_RETAINED_BYTES = 8137981952 + 8127361024 + 305778688
# Observed once over pinned HTTPS on 2026-10-10; not a signed tree attestation.
SOURCE_HASHES = {
    'go.mod': '14864acbf087dedecc12c143cece6599fd4c10f34e2e1528e5c26002d210deb5',
    'go.sum': '6bb93c10a466b034c0be459dc01130f38a3590c831461973fecb98f0bbe9cf7f',
}
MODULE = 'github.com/fluxcd/kustomize-controller'
UPDATES = {'golang.org/x/crypto': ('v0.53.0', 'v0.55.0'),
           'google.golang.org/grpc': ('v1.81.1', 'v1.83.2')}
UNRESOLVED = {
    'module_graph': 'unresolved', 'replacement_sums': 'unresolved',
    'os_pcre2': 'unresolved', 'build': 'not_run', 'upstream_tests': 'not_run',
    'binary_image_scan': 'not_run', 'advisory_freshness': 'not_claimed',
    'publisher': 'unverified', 'admission': 'not_granted',
    'installed_highs': 'not_mitigated', 'l0': 'not_accepted',
    'original_source_operation': 'unknown_no_replay',
}


class RepairError(ValueError):
    pass


def require(ok: bool, message: str) -> None:
    if not ok:
        raise RepairError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def private_directory(path: Path) -> int:
    """Reject symlinks in every component; return a no-follow directory FD."""
    path = path.absolute()
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.parts[1:]:
            try:
                next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                  dir_fd=fd)
            except OSError:
                raise RepairError('SYMLINK_OR_INVALID_SCOPE') from None
            os.close(fd)
            fd = next_fd
    except Exception:
        os.close(fd)
        raise
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        os.close(fd)
        raise RepairError('PRIVATE_SCOPE_REQUIRED')
    return fd


def write_once(scope: Path, name: str, data: bytes) -> None:
    require(re.fullmatch(r'[a-zA-Z0-9_.-]+', name) is not None, 'INVALID_FILENAME')
    require(len(data) <= MAX_RESPONSE, 'ARTIFACT_TOO_LARGE')
    directory = private_directory(scope)
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(directory)


def read_private(scope: Path, name: str) -> bytes:
    require(re.fullmatch(r'[a-zA-Z0-9_.-]+', name) is not None, 'INVALID_FILENAME')
    directory = private_directory(scope)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600,
                    'PRIVATE_ARTIFACT_REQUIRED')
            data = stream.read(MAX_RESPONSE + 1)
            require(len(data) <= MAX_RESPONSE, 'ARTIFACT_TOO_LARGE')
            return data
    finally:
        os.close(directory)


def budget(scope: Path) -> dict:
    """Observe ONLY this new flat private scope, never retained build/test data."""
    fd = private_directory(scope)
    try:
        info = os.fstat(fd)
        used = max(info.st_size, info.st_blocks * 512)
        for name in os.listdir(fd):
            item = os.stat(name, dir_fd=fd, follow_symlinks=False)
            require(stat.S_ISREG(item.st_mode) and item.st_uid == os.getuid()
                    and item.st_nlink == 1, 'UNSAFE_SCOPE_ENTRY')
            used += max(item.st_size, item.st_blocks * 512)
        fs = os.fstatvfs(fd)
        free = fs.f_bavail * fs.f_frsize
        # Reserve all remaining authorized files/receipts before any retrieval.
        require(used + 16 * MAX_RESPONSE <= SCOPE_LIMIT, 'SCOPE_BUDGET')
        require(PARENT_RETAINED_BYTES + SCOPE_LIMIT < COMBINED_LIMIT,
                'COMBINED_BUDGET')
        require(free >= FREE_MIN + SCOPE_LIMIT, 'FREE_BUDGET')
        return {'scope_charged_bytes': used, 'free_bytes': free,
                'scope_limit': SCOPE_LIMIT, 'combined_limit': COMBINED_LIMIT,
                'parent_dated_retained_bytes': PARENT_RETAINED_BYTES,
                'unknown_scope_freshly_observed': False}
    finally:
        os.close(fd)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RepairError('REDIRECT_FORBIDDEN')


@contextmanager
def fetch_deadline():
    """Whole-call bound also covers TLS and trickled response headers (Unix)."""
    require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), 'ACTIVE_TIMER_HOLD')
    previous = signal.getsignal(signal.SIGALRM)

    def expired(signum, frame):
        raise RepairError('FETCH_TIMEOUT')

    signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, TIMEOUT)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def fetch(scope: Path, filename: str) -> dict:
    """One exact GET, no proxy credentials, redirects, retries or unknown replay."""
    require(filename in ('go.mod', 'go.sum'), 'FETCH_NOT_ALLOWED')
    observation = budget(scope)
    url = BASE_URL + filename
    started = time.time()
    write_once(scope, filename + '.fetch-started.json', encode({
        'schema_version': 1, 'url': url, 'revision': REVISION,
        'started_unix': started, 'timeout_seconds': TIMEOUT,
        'max_response_bytes': MAX_RESPONSE, 'budget': observation}))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    clock = time.monotonic()
    try:
        with fetch_deadline(), opener.open(
            urllib.request.Request(url, method='GET'), timeout=TIMEOUT
        ) as response:
            require(response.status == 200 and response.geturl() == url, 'HTTP_IDENTITY')
            length = response.headers.get('Content-Length')
            require(length is None or (length.isdigit() and int(length) <= MAX_RESPONSE),
                    'RESPONSE_TOO_LARGE')
            # read1 performs at most one raw read. Reset the socket timeout to
            # the remaining whole-call allowance; trickle responses cannot
            # repeatedly renew a 30-second per-read timeout.
            chunks = []
            size = 0
            while size <= MAX_RESPONSE and not response.isclosed():
                remaining = TIMEOUT - (time.monotonic() - clock)
                require(remaining > 0, 'FETCH_TIMEOUT')
                response.fp.raw._sock.settimeout(remaining)
                chunk = response.read1(min(65536, MAX_RESPONSE + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            data = b''.join(chunks)
            require(time.monotonic() - clock <= TIMEOUT, 'FETCH_TIMEOUT')
            require(0 < len(data) <= MAX_RESPONSE, 'RESPONSE_TOO_LARGE')
            require(length is None or len(data) == int(length), 'TRUNCATED_RESPONSE')
        write_once(scope, filename, data)
        receipt = {'schema_version': 1, 'outcome': 'retrieved', 'url': url,
                   'revision': REVISION, 'bytes': len(data), 'sha256': digest(data),
                   'started_unix': started, 'finished_unix': time.time(),
                   'publisher_verified': False}
        write_once(scope, filename + '.fetch-finished.json', encode(receipt))
        return receipt
    except Exception:
        # No exception body/private network details; started reservation is retained.
        write_once(scope, filename + '.fetch-held.json', encode({
            'schema_version': 1, 'outcome': 'failed_or_unknown_no_replay', 'url': url}))
        raise


def validate_source(go_mod: bytes, go_sum: bytes) -> str:
    for name, data in (('go.mod', go_mod), ('go.sum', go_sum)):
        require(len(data) <= MAX_RESPONSE and digest(data) == SOURCE_HASHES.get(name),
                'PINNED_SOURCE_HASH_MISMATCH')
    text = go_mod.decode('utf-8')
    require(re.findall(r'^module (\S+)$', text, re.M) == [MODULE], 'MODULE_IDENTITY')
    require('\r' not in text and text.endswith('\n'), 'UNSUPPORTED_MODULE_FORMAT')
    return text


def patch_source(go_mod: bytes, go_sum: bytes) -> tuple[bytes, bytes]:
    text = validate_source(go_mod, go_sum)
    # Exact pinned bytes plus single literal require-line edits; no broad replace.
    patched = text
    for module, (old, new) in UPDATES.items():
        pattern = rf'^(\t{re.escape(module)} ){re.escape(old)}( // indirect\n)'
        require(len(re.findall(pattern, patched, re.M)) == 1, 'EXACT_REQUIRE_LINE_MISSING')
        patched = re.sub(pattern, rf'\g<1>{new}\g<2>', patched, flags=re.M)
    diff = ''.join(difflib.unified_diff(text.splitlines(True), patched.splitlines(True),
                                      fromfile='a/go.mod', tofile='b/go.mod')).encode()
    return patched.encode(), diff


def provenance(go_mod: bytes, go_sum: bytes, patched: bytes, diff: bytes,
               receipts: dict) -> dict:
    expected_mod, expected_diff = patch_source(go_mod, go_sum)
    require(patched == expected_mod and diff == expected_diff, 'PATCH_MISMATCH')
    for name, data in (('go.mod', go_mod), ('go.sum', go_sum)):
        receipt = receipts.get(name, {})
        require(type(receipt.get('schema_version')) is int and receipt.get('schema_version') == 1
                and receipt.get('outcome') == 'retrieved'
                and receipt.get('url') == BASE_URL + name
                and receipt.get('revision') == REVISION and receipt.get('bytes') == len(data)
                and receipt.get('sha256') == digest(data)
                and receipt.get('publisher_verified') is False, 'FETCH_RECEIPT_MISMATCH')
        start, end = receipt.get('started_unix'), receipt.get('finished_unix')
        require(type(start) in (int, float) and type(end) in (int, float)
                and 0 <= end - start <= TIMEOUT, 'FETCH_TIME_INVALID')
    return {'schema_version': 1, 'revision': REVISION, 'module': MODULE,
            'source': {name: {'url': BASE_URL + name, 'sha256': digest(data)}
                       for name, data in (('go.mod', go_mod), ('go.sum', go_sum))},
            'updates': {name: {'from': old, 'to': new}
                        for name, (old, new) in UPDATES.items()},
            'patched_go_mod_sha256': digest(patched), 'patch_sha256': digest(diff),
            'go_sum_unchanged_sha256': digest(go_sum),
            'fetch_receipt_sha256': {name: digest(encode(receipts[name]))
                                     for name in ('go.mod', 'go.sum')},
            'status': dict(UNRESOLVED), 'source_patch': 'validated_only',
            'identity_basis': 'pinned_public_https_bytes_not_publisher_proof'}


def validate_provenance(record: dict, go_mod: bytes, go_sum: bytes,
                        patched: bytes, diff: bytes, receipts: dict) -> None:
    require(encode(record) == encode(provenance(go_mod, go_sum, patched, diff, receipts)),
            'PROVENANCE_MISMATCH')


def source_inputs(scope: Path) -> tuple[bytes, bytes, dict]:
    receipts = {}
    for name in ('go.mod', 'go.sum'):
        raw = read_private(scope, name + '.fetch-finished.json')
        receipts[name] = json.loads(raw)
        require(raw == encode(receipts[name]), 'NONCANONICAL_RECEIPT')
        start_raw = read_private(scope, name + '.fetch-started.json')
        start = json.loads(start_raw)
        require(start_raw == encode(start) and type(start.get('schema_version')) is int
                and start.get('schema_version') == 1
                and start.get('url') == BASE_URL + name and start.get('revision') == REVISION
                and start.get('timeout_seconds') == TIMEOUT
                and start.get('max_response_bytes') == MAX_RESPONSE
                and start.get('started_unix') == receipts[name].get('started_unix'),
                'FETCH_START_MISMATCH')
    return read_private(scope, 'go.mod'), read_private(scope, 'go.sum'), receipts


def materialize(scope: Path) -> dict:
    """No network: create patch and evidence from already completed exact fetches."""
    budget(scope)
    mod, sums, receipts = source_inputs(scope)
    patched, diff = patch_source(mod, sums)
    record = provenance(mod, sums, patched, diff, receipts)
    record['fetch_start_sha256'] = {
        name: digest(read_private(scope, name + '.fetch-started.json'))
        for name in ('go.mod', 'go.sum')}
    write_once(scope, 'go.mod.patched', patched)
    write_once(scope, 'module.patch', diff)
    write_once(scope, 'provenance.json', encode(record))
    validate_scope(scope)
    return record


def validate_scope(scope: Path) -> dict:
    mod, sums, receipts = source_inputs(scope)
    patched, diff = read_private(scope, 'go.mod.patched'), read_private(scope, 'module.patch')
    raw = read_private(scope, 'provenance.json')
    record = json.loads(raw)
    expected = provenance(mod, sums, patched, diff, receipts)
    expected['fetch_start_sha256'] = {
        name: digest(read_private(scope, name + '.fetch-started.json'))
        for name in ('go.mod', 'go.sum')}
    require(raw == encode(expected) and record == expected, 'PROVENANCE_MISMATCH')
    return record
