"""Nonactivating pcre2-only OS-prefix patch. Never extract or execute contents.

Two hash-bound OS layers are not proof of the uninspected controller layer.
This produces a review artifact, not an image, installed mitigation or admission.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import posixpath
import re
import tarfile
import zlib

from tools.local_experiment import kustomize_os_repair as apk

require = apk.require
Error = apk.RepairError
CHILD = '039165f7ee401c107cdd2e1a5d16fa73bafc7dfe9143c2d61b10193a14190443'
BLOB_LIMIT = 16 * 1024**2
PLAIN_LIMIT = 48 * 1024**2
DB_PATH = 'lib/apk/db/installed'


def bound(value: bytes, checksum: str, size: int, limit: int) -> None:
    require(type(size) is int and 0 < size <= limit and len(value) == size,
            'BOUND_SIZE')
    require(re.fullmatch(r'sha256:[0-9a-f]{64}', checksum) is not None and
            'sha256:' + apk.digest(value) == checksum, 'BOUND_CHECKSUM')


def candidate(manifest: bytes, config: bytes) -> dict:
    bound(manifest, 'sha256:' + CHILD, len(manifest), 1024**2)
    image = json.loads(manifest)
    require(image.get('schemaVersion') == 2 and image.get('mediaType') ==
            'application/vnd.oci.image.manifest.v1+json', 'MANIFEST_TYPE')
    descriptor = image['config']
    require(descriptor.get('mediaType') == 'application/vnd.oci.image.config.v1+json',
            'CONFIG_TYPE')
    bound(config, descriptor['digest'], descriptor['size'], 1024**2)
    settings = json.loads(config)
    require(settings.get('architecture') == 'arm64' and settings.get('os') == 'linux' and
            settings.get('config', {}).get('Labels', {}).get('org.opencontainers.image.revision')
            == apk.common.REVISION, 'CONFIG_IDENTITY')
    layers = image['layers']
    roots = settings.get('rootfs', {})
    require(len(layers) == 3 and roots.get('type') == 'layers' and
            len(roots.get('diff_ids', [])) == 3, 'LAYER_CLOSURE')
    history = [h for h in settings.get('history', []) if not h.get('empty_layer')]
    require(len(history) == 3 and history[-1].get('created_by') ==
            'COPY /workspace/kustomize-controller /usr/local/bin/ # buildkit',
            'OS_PREFIX_SELECTION')
    for item in layers[:2]:
        require(item.get('mediaType') == 'application/vnd.oci.image.layer.v1.tar+gzip' and
                type(item.get('size')) is int and 0 < item['size'] <= BLOB_LIMIT,
                'OS_LAYER_DESCRIPTOR')
    return {'layers': layers[:2], 'diff_ids': roots['diff_ids'][:2],
            'uninspected_layer': layers[2], 'config_sha256': apk.digest(config)}


def canonical(name: str) -> str:
    if name.startswith('./'):
        name = name[2:]
    name = name.rstrip('/')
    apk.safe_name(name)
    require(len(name) <= 256 and not any(ord(c) < 32 for c in name), 'PATH_FORMAT')
    return name


def link_target(name: str, target: str, hard: bool = False) -> str:
    require(bool(target) and '\\' not in target and '\x00' not in target and
            not any(ord(c) < 32 for c in target), 'LINK_FORMAT')
    # Resolve lexically, never against the host filesystem. Reject root escape.
    parts = [] if hard or target.startswith('/') else name.split('/')[:-1]
    for part in target.split('/'):
        if part in ('', '.'):
            continue
        if part == '..':
            require(bool(parts), 'LINK_ESCAPE')
            parts.pop()
        else:
            parts.append(part)
    result = '/'.join(parts)
    apk.safe_name(result)
    return result


def envelope(plain: bytes) -> None:
    """Validate raw blocks, including PAX paths hidden by tarfile iteration."""
    require(bool(plain) and len(plain) % 512 == 0, 'TAR_BLOCKS')
    offset, pending = 0, False
    while offset < len(plain):
        header = plain[offset:offset + 512]
        if header == bytes(512):
            require(not pending and len(plain) - offset >= 1024 and
                    not any(plain[offset:]), 'TAR_INCOMPLETE_OR_TRAILING')
            return
        try:
            item = tarfile.TarInfo.frombuf(header, 'utf-8', 'strict')
        except (tarfile.TarError, ValueError, UnicodeError):
            raise Error('TAR_HEADER') from None
        require(item.type in {tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE,
                             tarfile.SYMTYPE, tarfile.XHDTYPE}, 'HARDLINK_OR_SPECIAL')
        end = offset + 512 + item.size
        padded = offset + 512 + ((item.size + 511) // 512) * 512
        require(0 <= item.size <= PLAIN_LIMIT and padded <= len(plain) and
                not any(plain[end:padded]), 'TAR_TRUNCATED_OR_PADDING')
        if item.type == tarfile.XHDTYPE:
            require(not pending and 0 < item.size <= 65536, 'PAX_LIMIT')
            pending, keys = True, set()
            body = plain[offset + 512:end]
            while body:
                size, separator, _ = body.partition(b' ')
                require(separator and size.isdigit() and len(size) <= 6, 'PAX_RECORD')
                length = int(size)
                require(len(size) + 3 < length <= len(body) and body[length - 1] == 10,
                        'PAX_RECORD')
                key, separator, value = body[len(size) + 1:length - 1].partition(b'=')
                require(separator and key not in keys and key in {b'path', b'linkpath',
                        b'mtime', b'ctime', b'atime', b'APK-TOOLS.checksum.SHA1'}, 'PAX_UNSUPPORTED')
                keys.add(key)
                if key in (b'path', b'linkpath'):
                    text = value.decode('utf-8')
                    require(0 < len(value) <= 512 and '\x00' not in text and
                            not any(ord(c) < 32 for c in text), 'PAX_PATH')
                    if key == b'path':
                        canonical(text)
                else:
                    require(re.fullmatch(rb'[0-9a-f]{40}' if key.endswith(b'SHA1') else
                                        rb'-?\d+(\.\d+)?', value) is not None, 'PAX_VALUE')
                body = body[length:]
        else:
            canonical(item.name)
            pending = False
            require(item.isreg() or item.size == 0, 'TAR_NONFILE_DATA')
        offset = padded
    raise Error('TAR_INCOMPLETE')


def parse_tar(plain: bytes) -> dict:
    require(len(plain) <= PLAIN_LIMIT, 'EXPANDED_LIMIT')
    envelope(plain)
    require(plain.endswith(bytes(1024)), 'TAR_INCOMPLETE')
    entries = {}
    with tarfile.open(fileobj=io.BytesIO(plain), mode='r:') as archive:
        for item in archive:
            name = canonical(item.name)
            require(name not in entries and len(entries) < 10000, 'DUPLICATE_OR_COUNT')
            require(not item.pax_headers or set(item.pax_headers) <=
                    {'mtime', 'ctime', 'atime', 'path', 'linkpath', 'APK-TOOLS.checksum.SHA1'}, 'PAX_UNSUPPORTED')
            require(0 <= item.mode <= 0o7777 and 0 <= item.uid <= 65535 and 0 <= item.gid <= 65535,
                    'UNSUPPORTED_OWNER_OR_MODE')
            entry = {'mode': item.mode, 'uid': item.uid, 'gid': item.gid}
            if item.isreg():
                value = archive.extractfile(item).read(PLAIN_LIMIT + 1)
                require(len(value) == item.size, 'MEMBER_INCOMPLETE')
                checksum = item.pax_headers.get('APK-TOOLS.checksum.SHA1')
                require(checksum is None or checksum == hashlib.sha1(value).hexdigest(),
                        'PAYLOAD_CHECKSUM')
                entry.update(type='file', data=value)
            elif item.isdir():
                entry.update(type='directory')
            elif item.issym():
                link_target(name, item.linkname)
                entry.update(type='symlink', target=item.linkname)
            else:
                # Hard links need order/inode semantics; deliberately unsupported.
                raise Error('HARDLINK_OR_SPECIAL')
            require(not any(p.startswith('.wh.') for p in name.split('/')),
                    'WHITEOUT_INPUT_UNSUPPORTED')
            entries[name] = entry
    return entries


def parse_layer(blob: bytes, descriptor: dict, diff_id: str) -> dict:
    bound(blob, descriptor['digest'], descriptor['size'], BLOB_LIMIT)
    decoder = zlib.decompressobj(31)
    try:
        plain = decoder.decompress(blob, PLAIN_LIMIT + 1)
    except zlib.error:
        raise Error('GZIP_CORRUPT') from None
    require(len(plain) <= PLAIN_LIMIT and decoder.eof and not decoder.unused_data,
            'GZIP_INCOMPLETE_OR_OVERSIZE')
    bound(plain, diff_id, len(plain), PLAIN_LIMIT)
    return parse_tar(plain)


def overlay(layers: list[dict]) -> dict:
    require(len(layers) == 2, 'OS_LAYER_CLOSURE')
    result = {}
    for layer in layers:
        for name, entry in layer.items():
            prefixes = ['/'.join(name.split('/')[:i]) for i in range(1, len(name.split('/')))]
            require(all(result.get(p, layer.get(p, {})).get('type') == 'directory'
                        for p in prefixes), 'PATH_PARENT_NOT_DIRECTORY')
            old = result.get(name)
            require(old is None or (old['type'] == 'directory') ==
                    (entry['type'] == 'directory'), 'OVERLAY_TYPE_CHANGE')
            result[name] = entry
    return result


def database(data: bytes) -> list[dict]:
    require(0 < len(data) <= 1024**2 and data.endswith(b'\n\n') and b'\r' not in data,
            'DB_INCOMPLETE')
    records, names = [], set()
    for raw in data[:-2].split(b'\n\n'):
        fields, files, directory = {}, {}, None
        for line in raw.decode('utf-8').split('\n'):
            require(len(line) >= 2 and line[1] == ':' and line[0].isalpha(), 'DB_FIELD')
            key, value = line[0], line[2:]
            if key == 'F':
                directory = canonical(value)
            elif key == 'R':
                require(directory is not None and '/' not in value, 'DB_FILE')
                name = canonical(directory + '/' + value)
                require(name not in files, 'DB_DUPLICATE_FILE')
                files[name] = {}
            elif key in ('a', 'Z'):
                require(bool(files), 'DB_FILE_METADATA')
                info = files[next(reversed(files))]
                require(key not in info, 'DB_DUPLICATE_FILE_METADATA')
                info[key] = value
                if key == 'Z':
                    apk.checksum_bytes(value)
            elif key in ('M',):
                require(directory is not None, 'DB_DIRECTORY_METADATA')
            else:
                require(key not in fields, 'DB_DUPLICATE_FIELD')
                fields[key] = value
        require(all(fields.get(k) for k in ('P', 'V', 'A', 'C')) and
                fields['P'] not in names and fields['A'] == 'aarch64', 'DB_PACKAGE_IDENTITY')
        apk.checksum_bytes(fields['C'])
        require(all('Z' in f and set(f) <= {'a', 'Z'} for f in files.values()), 'DB_FILE_CLOSURE')
        names.add(fields['P'])
        records.append({'raw': raw + b'\n\n', 'fields': fields, 'files': files})
    return records


def file_checksum(entry: dict) -> str:
    require(entry['type'] in ('file', 'symlink'), 'PACKAGE_FILE_TYPE')
    value = entry['data'] if entry['type'] == 'file' else entry['target'].encode()
    return 'Q1' + base64.b64encode(hashlib.sha1(value).digest()).decode()


def verify_files(record: dict, tree: dict) -> None:
    for name, metadata in record['files'].items():
        entry = tree.get(name, {})
        require(entry.get('type') in ('file', 'symlink') and
                file_checksum(entry) == metadata['Z'] and
                metadata.get('a', '0:0:644') == '0:0:' + format(entry['mode'], 'o'), 'INSTALLED_FILE_CLOSURE')


def replacement(selected: dict, payload: dict) -> bytes:
    fields = ''.join(k + ':' + selected[k] + '\n' for k in
                     ('C', 'P', 'V', 'A', 'S', 'I', 'T', 'U', 'L', 'o', 'm', 't', 'c', 'D', 'p')
                     if k in selected)
    for directory in ('usr', 'usr/lib'):
        fields += 'F:' + directory + '\n'
        for name, entry in sorted(payload.items()):
            if posixpath.dirname(name) == directory and entry['type'] != 'directory':
                fields += 'R:' + posixpath.basename(name) + '\na:0:0:' + format(entry['mode'], 'o')
                fields += '\nZ:' + file_checksum(entry) + '\n'
    return fields.encode() + b'\n'


def build_patch(tree: dict, index: bytes, package: bytes) -> tuple[bytes, dict]:
    selected = apk.select_pcre2(apk.parse_index(index))
    package_info = apk.validate_apk(package, selected)
    require(selected['V'] == '10.49-r0', 'EXACT_PATCH_VERSION')
    require(package_info['dependencies'] == ['so:libc.musl-aarch64.so.1'], 'DEPENDENCY_UNSUPPORTED')
    streams = apk.gzip_streams(package, apk.APK_LIMIT)
    info = apk.parse_pkginfo(apk.tar_members(streams[1][1])['.PKGINFO'])
    require(sorted(info.get('provides', [])) == sorted(selected.get('p', '').split()),
            'PROVIDED_ABI_MISMATCH')
    payload = parse_tar(streams[2][1])
    expected = {'usr', 'usr/lib', 'usr/lib/libpcre2-8.so.0', 'usr/lib/libpcre2-8.so.0.16.1',
                'usr/lib/libpcre2-posix.so.3', 'usr/lib/libpcre2-posix.so.3.0.9'}
    require(set(payload) == expected, 'PCRE2_PAYLOAD_SCOPE')
    for name, entry in payload.items():
        require(entry['uid'] == entry['gid'] == 0, 'PAYLOAD_OWNER')
        if name in ('usr', 'usr/lib'):
            require(entry['type'] == 'directory' and entry['mode'] == 0o755,
                    'PAYLOAD_DIRECTORY')
        elif name.endswith(('.0.16.1', '.3.0.9')):
            value = entry.get('data', b'')
            require(entry['type'] == 'file' and entry['mode'] == 0o755 and
                    value[:6] == b'\x7fELF\x02\x01' and value[18:20] == b'\xb7\x00', 'ARM64_ELF')
        else:
            require(entry['type'] == 'symlink' and entry['mode'] == 0o777 and
                    link_target(name, entry['target']) in expected and
                    payload[link_target(name, entry['target'])]['type'] == 'file', 'PAYLOAD_LINK')
    require(tree.get(DB_PATH, {}).get('type') == 'file', 'INSTALLED_DB_MISSING')
    require(tree[DB_PATH]['uid'] == tree[DB_PATH]['gid'] == 0 and
            tree[DB_PATH]['mode'] == 0o644, 'INSTALLED_DB_METADATA')
    original = tree[DB_PATH]['data']
    records = database(original)
    packages = {r['fields']['P']: r for r in records}
    require('pcre2' in packages and 'musl' in packages, 'INSTALLED_CLOSURE')
    old, musl = packages['pcre2'], packages['musl']
    require(old['fields']['V'] == '10.48-r0' and musl['fields']['V'] == '1.2.5-r23' and
            musl['fields']['C'] == 'Q1htKIaEsBKo1P1SXDlnR87oYkBzM=' and
            'so:libc.musl-aarch64.so.1=1' in musl['fields'].get('p', '').split(), 'MUSL_CLOSURE')
    verify_files(old, tree)
    verify_files(musl, tree)
    abi = tree.get('lib/libc.musl-aarch64.so.1', {})
    require(abi.get('type') == 'symlink' and tree.get(link_target(
            'lib/libc.musl-aarch64.so.1', abi['target']), {}).get('type') == 'file', 'MUSL_ABI')
    owned = {name for r in records if r is not old for name in r['files']}
    require(not owned.intersection(expected), 'UNRELATED_FILE_COLLISION')
    for name in expected:
        if name in ('usr', 'usr/lib'):
            require(tree.get(name, {}).get('type') == 'directory', 'PAYLOAD_PARENT')
        else:
            require(name not in tree or name in old['files'], 'UNOWNED_FILE_COLLISION')
    stale = sorted(set(old['files']) - expected)
    require(set(old['files']) == {'usr/lib/libpcre2-8.so.0', 'usr/lib/libpcre2-8.so.0.16.0',
                                'usr/lib/libpcre2-posix.so.3', 'usr/lib/libpcre2-posix.so.3.0.8'},
            'OLD_PACKAGE_SCOPE')
    new_record = replacement(selected, payload)
    updated = b''.join(new_record if r is old else r['raw'] for r in records)
    new_records = database(updated)
    require([r['raw'] for r in new_records if r['fields']['P'] != 'pcre2'] ==
            [r['raw'] for r in records if r is not old], 'UNRELATED_DB_CHANGED')
    output = {n: e for n, e in payload.items() if e['type'] != 'directory'}
    output[DB_PATH] = {'type': 'file', 'data': updated, 'mode': tree[DB_PATH]['mode']}
    for name in stale:
        output[posixpath.dirname(name) + '/.wh.' + posixpath.basename(name)] = {
            'type': 'file', 'data': b'', 'mode': 0}
    layer = encode_tar(output)
    provenance = {'schema_version': 1, 'package': 'pcre2', 'version': selected['V'],
                  'index_sha256': apk.digest(index), 'apk_sha256': apk.digest(package),
                  'layer_sha256': apk.digest(layer), 'layer_bytes': len(layer),
                  'original_db_sha256': apk.digest(original), 'updated_db_sha256': apk.digest(updated),
                  'unrelated_records_sha256': apk.digest(b''.join(r['raw'] for r in records if r is not old)),
                  'unrelated_package_count': len(records) - 1, 'stale_whiteouts': stale,
                  'members': {n: {'type': e['type'], 'mode': e['mode'],
                      'sha256': apk.digest(e['data']) if e['type'] == 'file' else None,
                      'target': e.get('target')} for n, e in output.items()},
                  'installed_highs_mitigated': False, 'go_binary_patched': False,
                  'full_image_verified': False, 'publisher_verified': False,
                  'advisory_freshness': 'not_claimed', 'admission': False, 'l0': False}
    return layer, provenance


def encode_tar(entries: dict) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for name, entry in sorted(entries.items()):
            item = tarfile.TarInfo(name)
            item.mode = entry['mode']
            item.uid = item.gid = item.mtime = 0
            if entry['type'] == 'symlink':
                item.type, item.linkname = tarfile.SYMTYPE, entry['target']
                archive.addfile(item)
            else:
                item.size = len(entry['data'])
                archive.addfile(item, io.BytesIO(entry['data']))
    return buffer.getvalue()


def validate_patch(layer: bytes, provenance: dict, tree: dict, index: bytes, package: bytes) -> None:
    expected, report = build_patch(tree, index, package)
    require(layer == expected and provenance == report, 'PATCH_OR_PROVENANCE_CHANGED')


def build_candidate(manifest: bytes, config: bytes, blobs: list[bytes], index: bytes,
                    package: bytes, parent: bytes, parent_sha256: str,
                    scan: bytes) -> tuple[bytes, dict]:
    """Bind review inputs before producing an OS-prefix-only artifact.

    The caller supplies the independently reviewed receipt hash; receipt parsing
    is not a new signature verification or upstream publisher trust decision.
    """
    identity = candidate(manifest, config)
    require(len(blobs) == 2, 'OS_LAYER_CLOSURE')
    bound(parent, 'sha256:' + parent_sha256, len(parent), 1024**2)
    review = json.loads(parent)
    require(review.get('apk_sha256') == apk.digest(package) and
            review.get('index_sha256') == apk.digest(index) and
            review.get('package') == 'pcre2' and review.get('arch') == 'aarch64' and
            review.get('local_experimental_input_trust') ==
            'verified_against_digest_bound_retained_keys_only', 'PARENT_INPUT_PROOF')
    for label, value in (('index', index), ('package', package)):
        report = review.get('signatures', {}).get(label, {})
        streams = apk.gzip_streams(value, apk.INDEX_LIMIT if label == 'index' else apk.APK_LIMIT)
        signature = apk.signature_stream(streams[0][1])
        require(report.get('status') == 'verified_against_retained_key' and
                report.get('returncode') == 0 and report.get('algorithm') == 'RSA-SHA1' and
                report.get('signed_sha256') == apk.digest(streams[1][0]) and
                report.get('signature_sha256') == apk.digest(signature['bytes']) and
                report.get('key_name') == signature['key_name'] and
                report.get('publisher_verified') is False, 'PARENT_SIGNATURE_PROOF')
    closure = review.get('dependency_closure', {})
    require(closure.get('candidate_reference') ==
            'ghcr.io/fluxcd/kustomize-controller@sha256:' + CHILD and
            closure.get('status') == 'matched_retained_candidate_inventory' and
            closure.get('scan_sha256') == apk.digest(scan), 'PARENT_CANDIDATE_CLOSURE')
    tree = overlay([parse_layer(b, d, h) for b, d, h in
                    zip(blobs, identity['layers'], identity['diff_ids'])])
    records = database(tree.get(DB_PATH, {}).get('data', b''))
    observation = json.loads(scan)
    require(observation.get('Metadata', {}).get('Reference') == closure['candidate_reference'],
            'SCAN_IDENTITY')
    packages = [p for result in observation.get('Results', []) if result.get('Class') == 'os-pkgs'
                for p in result.get('Packages', [])]
    expected = {(r['fields']['P'], r['fields']['V'], r['fields']['A'],
                 'sha1:' + apk.checksum_bytes(r['fields']['C']).hex()) for r in records}
    actual = {(p.get('Name'), p.get('Version'), p.get('Arch'), p.get('Digest')) for p in packages}
    require(len(packages) == len(actual) == len(records) and actual == expected,
            'INSTALLED_INVENTORY_CLOSURE')
    layer, report = build_patch(tree, index, package)
    payload_hashes = {name: entry['sha256'] for name, entry in report['members'].items()
                      if name.startswith('usr/lib/') and entry['type'] == 'file' and
                      '/.wh.' not in name}
    require(review.get('version') == '10.49-r0' and
            review.get('elf_payload') == payload_hashes, 'PARENT_ELF_PROOF')
    report.update(candidate_child_sha256=CHILD, candidate_config_sha256=identity['config_sha256'],
                  os_layers=identity['layers'], os_diff_ids=identity['diff_ids'],
                  uninspected_controller_layer=identity['uninspected_layer'],
                  scope='two_verified_os_layers_only_not_complete_image',
                  parent_receipt_sha256=parent_sha256, scan_sha256=apk.digest(scan),
                  input_trust='parent_verified_retained_key_only',
                  full_image_dependency='uninspected_controller_layer_requires_separate_review')
    return layer, report


def validate_candidate_patch(layer: bytes, provenance: dict, manifest: bytes,
                             config: bytes, blobs: list[bytes], index: bytes,
                             package: bytes, parent: bytes, parent_sha256: str,
                             scan: bytes) -> None:
    """Rederive complete candidate-bound provenance; reject added acceptance claims."""
    expected, report = build_candidate(manifest, config, blobs, index, package,
                                      parent, parent_sha256, scan)
    require(layer == expected and provenance == report, 'CANDIDATE_PATCH_CHANGED')
