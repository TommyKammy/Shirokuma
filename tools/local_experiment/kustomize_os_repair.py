"""Evidence-only APK v2 reader. No extraction, installation or execution.

SHA1 Q1 indexes bind the compressed control stream, NOT the complete APK.
Publisher proof and dependency closure are separate fail-closed review gates.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import time
import urllib.request
import zlib

from tools.local_experiment import kustomize_source_repair as common

RepairError = common.RepairError
require = common.require
digest = common.digest
BASE_URL = 'https://dl-cdn.alpinelinux.org/alpine/v3.23/main/aarch64/'
INDEX_LIMIT = 4 * 1024**2
APK_LIMIT = 8 * 1024**2
EXPANDED_LIMIT = 32 * 1024**2
SCOPE_LIMIT = 64 * 1024**2
# Parent-dated builds/unknown plus full 64MiB reservations for source/two graphs.
# Conservative, not fresh retention acceptance; never read the unknown scope.
PARENT_RETAINED_BYTES = 16571121664 + 3 * SCOPE_LIMIT


def write_once(scope: Path, name: str, data: bytes) -> None:
    require(re.fullmatch(r'[A-Za-z0-9_.-]+', name) is not None, 'INVALID_FILENAME')
    require(len(data) <= APK_LIMIT, 'ARTIFACT_TOO_LARGE')
    directory = common.private_directory(scope)
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(directory)


def budget(scope: Path) -> dict:
    fd = common.private_directory(scope)
    try:
        used = 0
        for name in os.listdir(fd):
            item = os.stat(name, dir_fd=fd, follow_symlinks=False)
            require(stat.S_ISREG(item.st_mode) and item.st_nlink == 1
                    and item.st_uid == os.getuid(), 'UNSAFE_SCOPE_ENTRY')
            used += max(item.st_size, item.st_blocks * 512)
        fs = os.fstatvfs(fd)
        free = fs.f_bavail * fs.f_frsize
        require(used + 16 * 1024**2 < SCOPE_LIMIT, 'SCOPE_BUDGET')
        require(PARENT_RETAINED_BYTES + SCOPE_LIMIT < common.COMBINED_LIMIT,
                'COMBINED_BUDGET')
        require(free >= common.FREE_MIN + SCOPE_LIMIT, 'FREE_BUDGET')
        return {'scope_charged_bytes': used, 'free_bytes': free,
                'parent_dated_retained_bytes': PARENT_RETAINED_BYTES,
                'unknown_scope_observed': False, 'parent_capacity_review_required': True}
    finally:
        os.close(fd)


def fetch(scope: Path, selected: dict | None = None) -> dict:
    """Exactly one reserved index or index-selected APK GET; no retry/redirect."""
    if selected is None:
        name, limit = 'APKINDEX.tar.gz', INDEX_LIMIT
    else:
        # Re-derive selection from the immutable fetched index, never caller URL.
        raw = read_private(scope, 'APKINDEX.tar.gz', INDEX_LIMIT)
        actual = select_pcre2(parse_index(raw))
        require(selected == actual, 'SELECTION_CHANGED')
        name, limit = package_filename(actual), APK_LIMIT
    observation = budget(scope)
    url = BASE_URL + name
    started = time.time()
    write_once(scope, name + '.fetch-started.json', common.encode({
        'schema_version': 1, 'url': url, 'started_unix': started,
        'timeout_seconds': 30, 'max_response_bytes': limit, 'budget': observation}))
    opener = urllib.request.build_opener(common.NoRedirect(), urllib.request.ProxyHandler({}))
    try:
        with common.fetch_deadline(), opener.open(
                urllib.request.Request(url, method='GET'), timeout=30) as response:
            require(response.status == 200 and response.geturl() == url, 'HTTP_IDENTITY')
            length = response.headers.get('Content-Length')
            require(length is None or (length.isdigit() and int(length) <= limit),
                    'RESPONSE_TOO_LARGE')
            data = response.read(limit + 1)
            require(0 < len(data) <= limit, 'RESPONSE_TOO_LARGE')
            require(length is None or len(data) == int(length), 'TRUNCATED_RESPONSE')
        write_once(scope, name, data)
        result = {'schema_version': 1, 'outcome': 'retrieved', 'url': url,
                  'bytes': len(data), 'sha256': digest(data), 'started_unix': started,
                  'finished_unix': time.time(), 'publisher_verified': False}
        write_once(scope, name + '.fetch-finished.json', common.encode(result))
        return result
    except Exception:
        write_once(scope, name + '.fetch-held.json', common.encode({
            'schema_version': 1, 'outcome': 'failed_or_unknown_no_replay', 'url': url}))
        raise


def gzip_streams(data: bytes, max_input: int) -> list[tuple[bytes, bytes]]:
    """Split concatenated gzip members preserving exact compressed signed bytes."""
    require(0 < len(data) <= max_input, 'COMPRESSED_LIMIT')
    streams = []
    remaining = data
    expanded = 0
    while remaining:
        require(len(streams) < 3 and remaining.startswith(b'\x1f\x8b'), 'GZIP_FORMAT')
        decoder = zlib.decompressobj(31)
        try:
            plain = decoder.decompress(remaining, EXPANDED_LIMIT - expanded + 1)
        except zlib.error:
            raise RepairError('GZIP_CORRUPT') from None
        expanded += len(plain)
        require(expanded <= EXPANDED_LIMIT and decoder.eof, 'GZIP_TRUNCATED_OR_LIMIT')
        consumed = len(remaining) - len(decoder.unused_data)
        require(consumed > 0, 'GZIP_FORMAT')
        streams.append((remaining[:consumed], plain))
        remaining = decoder.unused_data
    return streams


def safe_name(name: str) -> None:
    require(bool(name) and '\\' not in name and '\x00' not in name
            and not name.startswith('/') and '..' not in name.split('/')
            and all(part not in ('', '.') for part in name.rstrip('/').split('/')),
            'ARCHIVE_PATH')


def tar_members(data: bytes, allow_links: bool = False) -> dict:
    """Read regular bytes only; validate every entry, never extract or follow links."""
    require(len(data) <= EXPANDED_LIMIT, 'TAR_LIMIT')
    validate_tar_envelope(data)
    result = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:') as archive:
            for item in archive:
                require(len(result) < 10000, 'MEMBER_LIMIT')
                safe_name(item.name)
                require(item.name not in result, 'DUPLICATE_MEMBER')
                require(set(item.pax_headers) <= {'mtime', 'ctime', 'atime',
                                                 'APK-TOOLS.checksum.SHA1'},
                        'PAX_UNSUPPORTED')
                require(item.size <= EXPANDED_LIMIT, 'MEMBER_LIMIT')
                if item.issym() or item.islnk():
                    require(allow_links, 'ARCHIVE_LINK')
                    # APK payload links are metadata only; reject escaping targets.
                    safe_name(item.linkname)
                    result[item.name] = {'type': 'link', 'target': item.linkname}
                elif item.isdir():
                    result[item.name] = {'type': 'directory'}
                else:
                    require(item.isreg(), 'ARCHIVE_SPECIAL')
                    value = archive.extractfile(item).read(EXPANDED_LIMIT + 1)
                    require(len(value) == item.size, 'TAR_TRUNCATED')
                    checksum = item.pax_headers.get('APK-TOOLS.checksum.SHA1')
                    require(checksum is None or checksum == hashlib.sha1(value).hexdigest(),
                            'MEMBER_CHECKSUM')
                    result[item.name] = value
    except (tarfile.TarError, EOFError, OSError):
        raise RepairError('TAR_CORRUPT') from None
    return result


def validate_tar_envelope(data: bytes) -> None:
    """Check all raw headers/padding, including extensions hidden by tarfile.

    APK control streams may end at a header boundary without tar end blocks.
    Trailing hidden archives, global/GNU/sparse extensions and truncated members
    are rejected before semantic parsing.
    """
    require(len(data) % 512 == 0 and bool(data), 'TAR_BLOCKS')
    offset = 0
    while offset < len(data):
        header = data[offset:offset + 512]
        if header == bytes(512):
            require(not any(data[offset:]), 'TAR_TRAILING_DATA')
            break
        try:
            item = tarfile.TarInfo.frombuf(header, 'utf-8', 'strict')
        except (tarfile.TarError, UnicodeError, ValueError):
            raise RepairError('TAR_HEADER') from None
        require(item.type in {tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE,
                             tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.XHDTYPE},
                'ARCHIVE_SPECIAL_OR_EXTENSION')
        end = offset + 512 + item.size
        padded = offset + 512 + ((item.size + 511) // 512) * 512
        require(0 <= item.size <= EXPANDED_LIMIT and padded <= len(data), 'TAR_TRUNCATED')
        require(not any(data[end:padded]), 'TAR_PADDING')
        if item.type == tarfile.XHDTYPE:
            require(0 < item.size <= 65536, 'PAX_LIMIT')
            body = data[offset + 512:end]
            keys = set()
            while body:
                size, separator, _ = body.partition(b' ')
                require(separator and size.isdigit(), 'PAX_RECORD')
                length = int(size)
                require(0 < length <= len(body) and body[length - 1:length] == b'\n',
                        'PAX_RECORD')
                record = body[len(size) + 1:length - 1]
                key, separator, value = record.partition(b'=')
                require(separator and key not in keys and key in {
                    b'mtime', b'ctime', b'atime', b'APK-TOOLS.checksum.SHA1'}, 'PAX_UNSUPPORTED')
                keys.add(key)
                require(re.fullmatch(rb'[0-9a-f]{40}' if key.endswith(b'SHA1')
                                     else rb'-?\d+(\.\d+)?', value), 'PAX_VALUE')
                body = body[length:]
        elif not item.isreg():
            require(item.size == 0, 'TAR_NONFILE_DATA')
        offset = padded


def signature_stream(plain: bytes) -> dict:
    entries = tar_members(plain)
    require(len(entries) == 1, 'SIGNATURE_MEMBER_COUNT')
    name, value = next(iter(entries.items()))
    require(re.fullmatch(r'\.SIGN\.RSA\.[A-Za-z0-9_@.-]+\.rsa\.pub', name)
            is not None and isinstance(value, bytes) and 64 <= len(value) <= 1024,
            'SIGNATURE_FORMAT')
    return {'name': name, 'key_name': name[len('.SIGN.RSA.'):], 'bytes': value}


def parse_index(data: bytes) -> list[dict]:
    streams = gzip_streams(data, INDEX_LIMIT)
    require(len(streams) == 2, 'INDEX_STREAM_COUNT')
    signature_stream(streams[0][1])
    entries = tar_members(streams[1][1])
    require(set(entries) <= {'APKINDEX', 'DESCRIPTION'}
            and isinstance(entries.get('APKINDEX'), bytes), 'INDEX_MEMBERS')
    try:
        text = entries['APKINDEX'].decode('utf-8')
    except UnicodeError:
        raise RepairError('INDEX_ENCODING') from None
    records = []
    identities = set()
    for block in text.strip().split('\n\n'):
        record = {}
        for line in block.splitlines():
            require(len(line) >= 2 and line[1] == ':' and line[0].isalpha(), 'INDEX_FIELD')
            key, value = line[0], line[2:]
            require(key not in record, 'DUPLICATE_FIELD')
            record[key] = value
        require(all(record.get(k) for k in ('P', 'V', 'A', 'C', 'S')), 'INDEX_REQUIRED')
        identity = (record['P'], record['V'], record['A'])
        require(identity not in identities, 'DUPLICATE_PACKAGE')
        identities.add(identity)
        records.append(record)
    return records


def version_tuple(version: str) -> tuple:
    # Intentionally restricted numeric stable pcre2 versions, not generic apk vercmp.
    match = re.fullmatch(r'(\d+)\.(\d+)-r(\d+)', version)
    require(match is not None, 'VERSION_UNSUPPORTED')
    return tuple(int(x) for x in match.groups())


def select_pcre2(records: list[dict]) -> dict:
    choices = [r for r in records if r['P'] == 'pcre2' and r['A'] == 'aarch64']
    require(len(choices) == 1, 'PCRE2_SELECTION_AMBIGUOUS_OR_MISSING')
    selected = choices[0]
    require(version_tuple(selected['V']) >= version_tuple('10.49-r0'), 'PCRE2_BELOW_FIX')
    require(selected['S'].isdigit() and 0 < int(selected['S']) <= APK_LIMIT,
            'PACKAGE_SIZE')
    require(selected.get('o') == 'pcre2' and re.fullmatch(r'[0-9a-f]{40}', selected.get('c', '')),
            'INDEX_PROVENANCE')
    checksum_bytes(selected['C'])
    package_filename(selected)
    return selected


def package_filename(selected: dict) -> str:
    require(selected.get('P') == 'pcre2' and selected.get('A') == 'aarch64', 'PACKAGE_IDENTITY')
    version_tuple(selected['V'])
    return 'pcre2-' + selected['V'] + '.apk'


def checksum_bytes(value: str) -> bytes:
    require(value.startswith('Q1'), 'CHECKSUM_UNSUPPORTED')
    try:
        raw = base64.b64decode(value[2:], validate=True)
    except ValueError:
        raise RepairError('CHECKSUM_FORMAT') from None
    require(len(raw) == 20 and base64.b64encode(raw).decode() == value[2:], 'CHECKSUM_FORMAT')
    return raw


def parse_pkginfo(data: bytes) -> dict:
    try:
        lines = data.decode('utf-8').splitlines()
    except UnicodeError:
        raise RepairError('PKGINFO_ENCODING') from None
    fields = {}
    repeated = {'depend', 'provides', 'install_if', 'replaces', 'triggers'}
    for line in lines:
        if not line or line.startswith('#'):
            continue
        require(' = ' in line, 'PKGINFO_FIELD')
        key, value = line.split(' = ', 1)
        require(re.fullmatch('[a-z_]+', key) and value, 'PKGINFO_FIELD')
        require(key not in fields or key in repeated, 'PKGINFO_DUPLICATE')
        fields.setdefault(key, []).append(value)
    return fields


def validate_apk(data: bytes, selected: dict) -> dict:
    require(len(data) == int(selected['S']), 'APK_SIZE_MISMATCH')
    streams = gzip_streams(data, APK_LIMIT)
    require(len(streams) == 3, 'APK_STREAM_COUNT')
    signature = signature_stream(streams[0][1])
    require(hashlib.sha1(streams[1][0]).digest() == checksum_bytes(selected['C']),
            'APK_INDEX_CHECKSUM')
    control = tar_members(streams[1][1])
    require(isinstance(control.get('.PKGINFO'), bytes), 'PKGINFO_MISSING')
    require(set(control) <= {'.PKGINFO'}, 'PACKAGE_SCRIPTS_OR_CONTROL_UNSUPPORTED')
    info = parse_pkginfo(control['.PKGINFO'])
    for field, value in {'pkgname': 'pcre2', 'pkgver': selected['V'], 'arch': 'aarch64',
                         'origin': 'pcre2', 'commit': selected['c']}.items():
        require(info.get(field) == [value], 'PKGINFO_IDENTITY_' + field)
    dependencies = info.get('depend', [])
    require(sorted(dependencies) == sorted(selected.get('D', '').split()), 'DEPENDENCY_MISMATCH')
    expected_data = info.get('datahash', [])
    require(expected_data == [digest(streams[2][0])], 'APK_DATAHASH')
    payload = tar_members(streams[2][1], allow_links=True)
    require(bool(payload), 'APK_EMPTY_PAYLOAD')
    return {'schema_version': 1, 'name': 'pcre2', 'version': selected['V'],
            'arch': 'aarch64', 'origin_commit': selected['c'], 'sha256': digest(data),
            'control_sha1': hashlib.sha1(streams[1][0]).hexdigest(),
            'data_sha256': expected_data[0], 'dependencies': dependencies,
            'payload_members': len(payload), 'signature_key': signature['key_name'],
            'signature_status': 'hold_not_verified', 'publisher_verified': False,
            'dependency_closure': 'hold_installed_inventory_not_bound',
            'installed_highs_mitigated': False, 'advisory_freshness': 'not_claimed'}


def dependency_candidates(records: list[dict], dependencies: list[str]) -> dict:
    """Report exact index providers; ambiguous/versioned/negative constraints hold.

    Index availability is never proof of the retained image's installed closure.
    """
    result = {}
    for dependency in dependencies:
        require(re.fullmatch(r'[A-Za-z0-9_+.:/-]+', dependency) is not None,
                'DEPENDENCY_CONSTRAINT_UNSUPPORTED')
        matches = [r for r in records if r['A'] == 'aarch64' and
                   (r['P'] == dependency or dependency in
                    [p.split('=', 1)[0] for p in r.get('p', '').split()])]
        require(len(matches) == 1, 'DEPENDENCY_PROVIDER_AMBIGUOUS_OR_MISSING')
        result[dependency] = {'package': matches[0]['P'], 'version': matches[0]['V'],
                              'checksum': matches[0]['C']}
    return result


def candidate_dependency_closure(index: bytes, apk: bytes, sbom: bytes,
                                 scan: bytes, reference: str) -> dict:
    """Bind providers to retained candidate inventory, never current runtime.

    Parent must additionally bind raw inventory bytes to successful scanner
    receipts and independently verify signatures. This performs no fetching,
    extraction, package execution or installation.
    """
    require(re.fullmatch(r'ghcr\.io/fluxcd/kustomize-controller@sha256:[0-9a-f]{64}',
                         reference) is not None, 'CANDIDATE_REFERENCE')
    records = parse_index(index)
    package = validate_apk(apk, select_pcre2(records))
    providers = dependency_candidates(records, package['dependencies'])
    inventory, observation = json.loads(sbom), json.loads(scan)
    component = inventory.get('metadata', {}).get('component', {})
    metadata = observation.get('Metadata', {})
    config = metadata.get('ImageConfig', {})
    require(component.get('name') == 'ghcr.io/fluxcd/kustomize-controller' and
            component.get('version') == reference.split('@')[1] and
            metadata.get('Reference') == reference and reference in metadata.get('RepoDigests', []) and
            config.get('architecture') == 'arm64' and config.get('os') == 'linux' and
            config.get('config', {}).get('Labels', {}).get('org.opencontainers.image.revision') == common.REVISION,
            'CANDIDATE_INVENTORY_IDENTITY')
    resolved = []
    for dependency, provider in providers.items():
        # Deliberately narrow: no generic ABI/version-constraint inference.
        require(dependency == 'so:libc.musl-aarch64.so.1' and provider['package'] == 'musl',
                'DEPENDENCY_CLOSURE_UNSUPPORTED')
        components = [c for c in inventory.get('components', []) if c.get('name') == 'musl']
        packages = [p for result in observation.get('Results', []) if result.get('Class') == 'os-pkgs'
                    for p in result.get('Packages', []) if p.get('Name') == 'musl']
        require(len(components) == len(packages) == 1, 'INSTALLED_PROVIDER_AMBIGUOUS')
        installed, scanned = components[0], packages[0]
        properties = installed.get('properties', [])
        checksums = [p.get('value') for p in properties if p.get('name') == 'syft:metadata:pullChecksum']
        provided = [p.get('value', '').split('=', 1)[0] for p in properties
                    if p.get('name', '').startswith('syft:metadata:provides:')]
        require(installed.get('version') == scanned.get('Version') == provider['version'] and
                'arch=aarch64' in installed.get('purl', '') and scanned.get('Arch') == 'aarch64' and
                checksums == [provider['checksum']] and dependency in provided and
                scanned.get('Digest') == 'sha1:' + checksum_bytes(provider['checksum']).hex() and
                'lib/libc.musl-aarch64.so.1' in scanned.get('InstalledFiles', []),
                'INSTALLED_PROVIDER_VERSION_CHECKSUM_ABI')
        resolved.append({'dependency': dependency, 'package': 'musl', 'version': provider['version'],
                         'control_sha1': checksum_bytes(provider['checksum']).hex()})
    return {'schema_version': 1, 'candidate_reference': reference,
            'status': 'matched_retained_candidate_inventory', 'providers': resolved,
            'index_sha256': digest(index), 'apk_sha256': digest(apk),
            'sbom_sha256': digest(sbom), 'scan_sha256': digest(scan),
            'signatures_parent_recheck_required': True, 'current_runtime_verified': False,
            'installed_highs_mitigated': False, 'publisher_verified': False,
            'advisory_freshness': 'not_claimed', 'admission': False, 'l0': False}


def read_private(scope: Path, name: str, limit: int = APK_LIMIT) -> bytes:
    require(re.fullmatch(r'[A-Za-z0-9_.-]+', name) is not None, 'INVALID_FILENAME')
    directory = common.private_directory(scope)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600,
                    'PRIVATE_ARTIFACT_REQUIRED')
            value = stream.read(limit + 1)
            require(len(value) <= limit, 'ARTIFACT_TOO_LARGE')
            return value
    finally:
        os.close(directory)


class BoundedReader:
    """Streaming read bound with content hash; no seek or filesystem writes."""
    def __init__(self, stream, limit):
        self.stream, self.limit, self.used = stream, limit, 0
        self.hash = hashlib.sha256()

    def read(self, size=-1):
        value = self.stream.read(min(size if size >= 0 else 65536,
                                     self.limit - self.used + 1))
        self.used += len(value)
        require(self.used <= self.limit, 'STREAM_LIMIT')
        self.hash.update(value)
        return value


def retained_keys(path: Path, expected_sha256: str) -> dict:
    """Stream only base-layer APK keys, bound to retained archive SHA256.

    Local archive attribution is not independent upstream publisher proof.
    Never extract paths, resolve archive links or read installed package data.
    """
    require(re.fullmatch('[0-9a-f]{64}', expected_sha256), 'ANCHOR_DIGEST')
    # Walk parent directories without following symlinks, just like private scope.
    directory = os.open(path.absolute().anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.absolute().parts[1:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = next_fd
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    keys = {}
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= 256 * 1024**2, 'ANCHOR_LIMIT')
        checksum = hashlib.sha256()
        for chunk in iter(lambda: stream.read(65536), b''):
            checksum.update(chunk)
        require(checksum.hexdigest() == expected_sha256, 'ANCHOR_HASH_MISMATCH')
        stream.seek(0)
        with tarfile.open(fileobj=stream, mode='r|*') as outer:
            first = next(iter(outer))
            safe_name(first.name)
            require(first.isreg() and re.fullmatch('[0-9a-f]{64}\.tar.gz', first.name)
                    and first.size <= 8 * 1024**2, 'ANCHOR_BASE_LAYER')
            compressed = BoundedReader(outer.extractfile(first), first.size)
            expanded = BoundedReader(gzip.GzipFile(fileobj=compressed), EXPANDED_LIMIT)
            with tarfile.open(fileobj=expanded, mode='r|') as layer:
                for item in layer:
                    name = item.name.removeprefix('./')
                    safe_name(name)
                    if name.startswith('etc/apk/keys/'):
                        require(item.isreg() and item.size <= 16384 and not item.pax_headers,
                                'ANCHOR_KEY_UNSAFE')
                        key_name = name[len('etc/apk/keys/'):]
                        require(re.fullmatch('[A-Za-z0-9_@.-]+\.rsa\.pub', key_name)
                                and key_name not in keys, 'ANCHOR_KEY_NAME')
                        key = layer.extractfile(item).read(16385)
                        require(len(key) == item.size and b'-----BEGIN PUBLIC KEY-----' in key,
                                'ANCHOR_KEY_FORMAT')
                        keys[key_name] = key
            # Drain for complete compressed hash, gzip CRC and expanded-size bound.
            while expanded.read(65536):
                pass
            while compressed.read(65536):
                pass
            require(compressed.used == first.size, 'ANCHOR_LAYER_TRUNCATED')
            require(compressed.hash.hexdigest() == first.name.split('.')[0], 'ANCHOR_LAYER_HASH')
        require(bool(keys), 'ANCHOR_KEYS_MISSING')
    return keys


def verify_signature(scope: Path, label: str, data: bytes, keys: dict) -> dict:
    """Fixed local openssl command; signature failure/missing key is a typed hold."""
    require(label in ('index', 'package'), 'SIGNATURE_LABEL')
    streams = gzip_streams(data, INDEX_LIMIT if label == 'index' else APK_LIMIT)
    signature = signature_stream(streams[0][1])
    key = keys.get(signature['key_name'])
    result = {'schema_version': 1, 'key_name': signature['key_name'],
              'signature_sha256': digest(signature['bytes']),
              'status': 'hold_key_missing', 'publisher_verified': False}
    if key is None:
        return result
    require(b'-----BEGIN PUBLIC KEY-----' in key and len(key) <= 16384, 'KEY_FORMAT')
    for name, value in [('key.pem', key), ('signature.bin', signature['bytes']),
                        ('signed.bin', streams[1][0])]:
        write_once(scope, label + '-' + name, value)
    argv = ['/usr/bin/openssl', 'dgst', '-sha1', '-verify', label + '-key.pem',
            '-signature', label + '-signature.bin', label + '-signed.bin']
    result.update(key_sha256=digest(key), signed_sha256=digest(streams[1][0]),
                  signing_domain='compressed_control_gzip', algorithm='RSA-SHA1')
    try:
        process = subprocess.run(argv, cwd=scope, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 timeout=10, env={'PATH': '/usr/bin:/bin'})
        result.update(returncode=process.returncode, stdout_sha256=digest(process.stdout),
                      stderr_sha256=digest(process.stderr), status=(
                          'verified_against_retained_key' if process.returncode == 0
                          and process.stdout.strip() == b'Verified OK' else 'hold_signature_failed'))
    except (OSError, subprocess.TimeoutExpired):
        result['status'] = 'hold_verifier_unavailable_or_unknown'
    write_once(scope, label + '-signature-review.json', common.encode(result))
    return result


def evidence(index: bytes, apk: bytes, receipts: dict, signatures: dict) -> dict:
    """Re-derive byte/identity/dependency provenance; retain explicit acceptance gaps."""
    records = parse_index(index)
    selected = select_pcre2(records)
    observation = validate_apk(apk, selected)
    providers = dependency_candidates(records, observation['dependencies'])
    require(set(receipts) == {'APKINDEX.tar.gz', package_filename(selected)}, 'FETCH_PROVENANCE')
    require(set(signatures) == {'index', 'package'}, 'SIGNATURE_PROVENANCE')
    for name, value in [('APKINDEX.tar.gz', index), (package_filename(selected), apk)]:
        receipt = receipts.get(name, {})
        require(receipt.get('schema_version') == 1 and receipt.get('outcome') == 'retrieved'
                and receipt.get('url') == BASE_URL + name and receipt.get('bytes') == len(value)
                and receipt.get('sha256') == digest(value)
                and receipt.get('publisher_verified') is False, 'FETCH_PROVENANCE')
        start, end = receipt.get('started_unix'), receipt.get('finished_unix')
        require(type(start) in (float, int) and type(end) in (float, int)
                and 0 <= end - start <= 30, 'FETCH_TIME')
    for label, value in [('index', index), ('package', apk)]:
        streams = gzip_streams(value, INDEX_LIMIT if label == 'index' else APK_LIMIT)
        sig = signature_stream(streams[0][1])
        report = signatures.get(label, {})
        require(report.get('key_name') == sig['key_name']
                and report.get('signature_sha256') == digest(sig['bytes'])
                and report.get('publisher_verified') is False, 'SIGNATURE_PROVENANCE')
        require(report.get('status') in {'verified_against_retained_key', 'hold_key_missing',
                'hold_signature_failed', 'hold_verifier_unavailable_or_unknown'}, 'SIGNATURE_STATUS')
        if report['status'] == 'verified_against_retained_key':
            require(report.get('returncode') == 0
                    and report.get('signed_sha256') == digest(streams[1][0])
                    and report.get('signing_domain') == 'compressed_control_gzip'
                    and report.get('algorithm') == 'RSA-SHA1', 'SIGNATURE_PROVENANCE')
    observation['signature_status'] = signatures['package']['status']
    signature_holds = [label + '_signature_' + report['status']
                       for label, report in signatures.items()
                       if report['status'] != 'verified_against_retained_key']
    return {'schema_version': 1, 'selected': selected, 'package': observation,
            'index_sha256': digest(index), 'receipts': receipts, 'signatures': signatures,
            'index_dependency_providers': providers,
            'holds': signature_holds + ['installed_dependency_inventory_not_bound',
                      'retained_key_upstream_publisher_not_independently_verified',
                      'parent_terminal_retention_capacity_review_required'],
            'signature_proof': 'local_verifier_report_parent_recheck_required',
            'installed_highs_mitigated': False, 'build_image_scan': 'not_run',
            'advisory_freshness': 'not_claimed', 'admission': False, 'l0': False}
