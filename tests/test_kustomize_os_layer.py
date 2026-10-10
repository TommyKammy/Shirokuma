"""Synthetic offline regressions. No retained inputs, fetch or content execution."""
from __future__ import annotations
import base64
import copy
import gzip
import hashlib
import io
import json
import tarfile
import unittest
from unittest.mock import patch

from tools.local_experiment import kustomize_os_layer as l
from tools.local_experiment import kustomize_os_repair as a


def tar(entries, extra=None):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for name, entry in entries.items():
            item = tarfile.TarInfo(name)
            item.mode = entry.get('mode', 0o644)
            item.uid = entry.get('uid', 0)
            item.pax_headers = entry.get('pax', {})
            kind = entry.get('type', 'file')
            if kind == 'directory':
                item.type = tarfile.DIRTYPE
            elif kind in ('symlink', 'hardlink'):
                item.type = tarfile.SYMTYPE if kind == 'symlink' else tarfile.LNKTYPE
                item.linkname = entry['target']
            elif kind == 'fifo':
                item.type = tarfile.FIFOTYPE
            else:
                item.size = len(entry['data'])
            archive.addfile(item, io.BytesIO(entry.get('data', b'')))
        if extra:
            item = tarfile.TarInfo(extra)
            archive.addfile(item)
    return out.getvalue()


def fixture(payload_change=None):
    elf = b'\x7fELF\x02\x01' + bytes(12) + b'\xb7\x00' + bytes(20)
    dirs = {n: {'type': 'directory', 'mode': 0o755, 'uid': 0, 'gid': 0}
            for n in ('usr', 'usr/lib', 'lib', 'lib/apk', 'lib/apk/db')}
    payload = {n: copy.deepcopy(dirs[n]) for n in ('usr', 'usr/lib')}
    for stem, version in [('libpcre2-8.so.0', '.16.1'), ('libpcre2-posix.so.3', '.0.9')]:
        payload['usr/lib/' + stem] = {'type': 'symlink', 'mode': 0o777, 'target': stem + version}
        payload['usr/lib/' + stem + version] = {'type': 'file', 'mode': 0o755, 'data': elf}
    if payload_change:
        payload_change(payload)
    gz = lambda b: gzip.compress(b, mtime=0)
    data = gz(tar(payload))
    info = {'pkgname': 'pcre2', 'pkgver': '10.49-r0', 'arch': 'aarch64', 'origin': 'pcre2',
            'commit': 'a' * 40, 'depend': 'so:libc.musl-aarch64.so.1', 'datahash': a.digest(data)}
    control = gz(tar({'.PKGINFO': {'data': (''.join(k + ' = ' + v + '\n' for k, v in info.items()) +
        'provides = so:libpcre2-8.so.0=0.16.1\nprovides = so:libpcre2-posix.so.3=3.0.9\n').encode()}}))
    sig = gz(tar({'.SIGN.RSA.fixture.rsa.pub': {'data': b'x' * 128}}))
    package = sig + control + data
    selected = {'P': 'pcre2', 'V': '10.49-r0', 'A': 'aarch64', 'S': str(len(package)),
                'C': 'Q1' + base64.b64encode(hashlib.sha1(control).digest()).decode(),
                'o': 'pcre2', 'c': 'a' * 40, 'D': info['depend'],
                'p': 'so:libpcre2-8.so.0=0.16.1 so:libpcre2-posix.so.3=3.0.9'}
    index = sig + gz(tar({'APKINDEX': {'data': ''.join(k + ':' + v + '\n' for k, v in selected.items()).encode() + b'\n'}}))
    old_payload = copy.deepcopy(payload)
    for stem, oldver, newver in [('libpcre2-8.so.0', '.16.0', '.16.1'),
                                 ('libpcre2-posix.so.3', '.0.8', '.0.9')]:
        old_payload['usr/lib/' + stem]['target'] = stem + oldver
        old_payload['usr/lib/' + stem + oldver] = old_payload.pop('usr/lib/' + stem + newver)
    old_record = l.replacement(dict(selected, V='10.48-r0'), old_payload)
    musl_files = {'lib/ld-musl-aarch64.so.1': {'type': 'file', 'mode': 0o755, 'data': elf},
                  'lib/libc.musl-aarch64.so.1': {'type': 'symlink', 'mode': 0o777, 'target': 'ld-musl-aarch64.so.1'}}
    musl = b'C:Q1htKIaEsBKo1P1SXDlnR87oYkBzM=\nP:musl\nV:1.2.5-r23\nA:aarch64\np:so:libc.musl-aarch64.so.1=1\nF:lib\n'
    for name, entry in musl_files.items():
        musl += ('R:' + name.split('/')[-1] + '\na:0:0:' + format(entry['mode'], 'o') +
                 '\nZ:' + l.file_checksum(entry) + '\n').encode()
    musl += b'\n'
    other = b'C:Q1htKIaEsBKo1P1SXDlnR87oYkBzM=\nP:unrelated\nV:1-r0\nA:aarch64\nT:unchanged ordering\n\n'
    tree = dict(dirs, **old_payload, **musl_files)
    tree[l.DB_PATH] = {'type': 'file', 'data': other + old_record + musl,
                       'mode': 0o644, 'uid': 0, 'gid': 0}
    return tree, index, package


class Tests(unittest.TestCase):
    def setUp(self):
        self.tree, self.index, self.package = fixture()

    def build(self):
        return l.build_patch(self.tree, self.index, self.package)

    def fails(self, call, *args):
        with self.assertRaises((a.RepairError, ValueError, KeyError)):
            call(*args)

    def test_replacement_preserves_other_records_exactly(self):
        layer, report = self.build()
        with tarfile.open(fileobj=io.BytesIO(layer), mode='r:') as archive:
            updated = archive.extractfile(l.DB_PATH).read()
        before = l.database(self.tree[l.DB_PATH]['data'])
        after = l.database(updated)
        self.assertEqual([r['raw'] for r in before if r['fields']['P'] != 'pcre2'],
                         [r['raw'] for r in after if r['fields']['P'] != 'pcre2'])
        fixed = next(r for r in after if r['fields']['P'] == 'pcre2')
        self.assertEqual(fixed['fields']['V'], '10.49-r0')
        self.assertEqual(report['unrelated_package_count'], 2)

    def test_stale_whiteouts_and_exact_scope(self):
        layer, report = self.build()
        with tarfile.open(fileobj=io.BytesIO(layer), mode='r:') as archive:
            entries = {m.name: m for m in archive}
        whites = {n for n in entries if '/.wh.' in n}
        self.assertEqual(whites, {'usr/lib/.wh.libpcre2-8.so.0.16.0', 'usr/lib/.wh.libpcre2-posix.so.3.0.8'})
        self.assertTrue(all(entries[n].isreg() and entries[n].size == 0 for n in whites))
        self.assertEqual(len(entries), 7)
        self.assertFalse(report['full_image_verified'])
        self.assertFalse(report['go_binary_patched'])

    def test_deterministic_and_rederived_validation(self):
        layer, report = self.build()
        self.assertEqual(self.build(), (layer, report))
        l.validate_patch(layer, report, self.tree, self.index, self.package)

    def test_changed_patch_rejects(self):
        layer, report = self.build()
        self.fails(l.validate_patch, layer[:-1], report, self.tree, self.index, self.package)

    def test_inflated_provenance_rejects(self):
        layer, report = self.build()
        report['installed_highs_mitigated'] = True
        self.fails(l.validate_patch, layer, report, self.tree, self.index, self.package)

    def test_wrong_checksum_or_size_rejects(self):
        for checksum, size in [('sha256:' + '0' * 64, 1), ('sha256:' + a.digest(b'x'), 2),
                               ('sha1:' + a.digest(b'x'), 1), ('sha256:' + a.digest(b'x'), True)]:
            self.fails(l.bound, b'x', checksum, size, 10)

    def test_unbound_layer_rejects(self):
        self.fails(l.parse_layer, gzip.compress(b'x'), {'digest': 'sha256:' + '0' * 64, 'size': 21}, 'sha256:' + '0' * 64)

    def test_layer_bound_compressed_and_diff_id(self):
        plain = tar({'x': {'data': b'yes'}}); blob = gzip.compress(plain)
        descriptor = {'digest': 'sha256:' + a.digest(blob), 'size': len(blob)}
        self.assertEqual(l.parse_layer(blob, descriptor, 'sha256:' + a.digest(plain))['x']['data'], b'yes')
        self.fails(l.parse_layer, blob, descriptor, 'sha256:' + '0' * 64)

    def test_concatenated_and_truncated_gzip_rejects(self):
        for blob in [gzip.compress(b'x') + gzip.compress(b'y'), gzip.compress(b'x')[:-2]]:
            self.fails(l.parse_layer, blob, {'digest': 'sha256:' + a.digest(blob), 'size': len(blob)}, 'sha256:' + '0' * 64)

    def test_expansion_bomb_rejects(self):
        blob = gzip.compress(bytes(1025))
        with patch.object(l, 'PLAIN_LIMIT', 1024):
            self.fails(l.parse_layer, blob, {'digest': 'sha256:' + a.digest(blob), 'size': len(blob)}, 'sha256:' + a.digest(bytes(1025)))

    def test_oversize_descriptor_rejects(self):
        self.fails(l.bound, b'x', 'sha256:' + a.digest(b'x'), l.BLOB_LIMIT + 1, l.BLOB_LIMIT)

    def test_paths_reject_traversal_and_absolute(self):
        for name in ('../x', '/x', 'a/../x', 'a//x', 'a\\x', 'a\nx'):
            self.fails(l.parse_tar, tar({name: {'data': b'x'}}))

    def test_duplicate_alias_rejects(self):
        self.fails(l.parse_tar, tar({'x': {'data': b'x'}}, './x'))

    def test_hardlink_and_fifo_rejects(self):
        for kind in ('hardlink', 'fifo'):
            self.fails(l.parse_tar, tar({'x': {'type': kind, 'target': 'y'}}))

    def test_symlink_escape_rejects(self):
        self.fails(l.parse_tar, tar({'a': {'type': 'symlink', 'target': '../outside'}}))

    def test_absolute_in_root_link_is_metadata_only(self):
        entries = l.parse_tar(tar({'a': {'type': 'symlink', 'target': '/lib/x'}}))
        self.assertEqual(entries['a']['target'], '/lib/x')
        self.assertEqual(l.link_target('a', '/lib/x'), 'lib/x')

    def test_overlay_never_follows_archive_links(self):
        first = {'usr': {'type': 'symlink', 'target': 'outside'}}
        self.fails(l.overlay, [first, {'usr/x': {'type': 'file', 'data': b'x'}}])

    def test_missing_os_layer_rejects(self):
        self.fails(l.overlay, [{}])

    def test_input_whiteout_rejects(self):
        self.fails(l.parse_tar, tar({'a/.wh.x': {'data': b''}}))

    def test_tar_truncation_and_hidden_trailing_rejects(self):
        raw = tar({'x': {'data': b'x'}})
        for value in (raw[:512], raw[:-1], raw + bytes(512) + b'x' * 512):
            self.fails(l.parse_tar, value)

    def test_pax_safe_unicode_path(self):
        raw = tar({'safe': {'data': b'x', 'pax': {'path': 'lib/é'}}})
        self.assertIn('lib/é', l.parse_tar(raw))

    def test_pax_path_escape_and_size_override_rejects(self):
        for pax in ({'path': '../bad'}, {'size': '1'}, {'linkpath': '../../bad'}):
            self.fails(l.parse_tar, tar({'safe': {'type': 'symlink', 'target': 'x', 'pax': pax}}))

    def test_db_truncation_duplicates_and_arch_rejects(self):
        db = self.tree[l.DB_PATH]['data']
        for value in (db[:-1], db + db, db.replace(b'A:aarch64', b'A:x86_64', 1),
                      db.replace(b'P:musl', b'P:musl\nP:musl')):
            self.fails(l.database, value)

    def test_db_file_checksums_required(self):
        db = self.tree[l.DB_PATH]['data']
        lines = db.splitlines(keepends=True)
        value = b''.join(line for line in lines if not line.startswith(b'Z:'))
        self.fails(l.database, value)

    def test_unrelated_default_mode_record_preserved(self):
        self.tree[l.DB_PATH]['data'] = self.tree[l.DB_PATH]['data'].replace(
            b'T:unchanged ordering\n', b'T:unchanged ordering\nF:etc\nR:fixture\nZ:Q1htKIaEsBKo1P1SXDlnR87oYkBzM=\n')
        self.build()

    def test_missing_installed_db_rejects(self):
        del self.tree[l.DB_PATH]
        self.fails(self.build)

    def test_wrong_musl_version_or_checksum_rejects(self):
        original = self.tree[l.DB_PATH]['data']
        for value in (original.replace(b'V:1.2.5-r23', b'V:1.2.5-r22'),
                      original.replace(b'P:musl', b'P:othermusl')):
            self.tree[l.DB_PATH]['data'] = value
            self.fails(self.build)

    def test_installed_payload_corruption_rejects(self):
        self.tree['usr/lib/libpcre2-8.so.0.16.0']['data'] = b'wrong'
        self.fails(self.build)

    def test_musl_abi_target_missing_rejects(self):
        del self.tree['lib/ld-musl-aarch64.so.1']
        self.fails(self.build)

    def test_unrelated_ownership_collision_rejects(self):
        db = self.tree[l.DB_PATH]['data']
        self.tree[l.DB_PATH]['data'] = db.replace(b'T:unchanged ordering\n',
            b'F:usr/lib\nR:libpcre2-8.so.0\nZ:Q1htKIaEsBKo1P1SXDlnR87oYkBzM=\n')
        self.fails(self.build)

    def test_unowned_new_payload_collision_rejects(self):
        self.tree['usr/lib/libpcre2-8.so.0.16.1'] = {'type': 'file', 'data': b'keep', 'mode': 0o755}
        self.fails(self.build)

    def test_payload_parent_symlink_rejects(self):
        self.tree['usr/lib'] = {'type': 'symlink', 'target': '/outside'}
        self.fails(self.build)

    def test_candidate_manifest_digest_rejects(self):
        self.fails(l.candidate, b'{}', b'{}')

    def candidate_inputs(self):
        layers = [tar({n: e for n, e in self.tree.items() if e['type'] == 'directory'}),
                  tar({n: e for n, e in self.tree.items() if e['type'] != 'directory'})]
        blobs = [gzip.compress(x, mtime=0) for x in layers]
        config = json.dumps({'architecture': 'arm64', 'os': 'linux', 'config': {'Labels': {
            'org.opencontainers.image.revision': a.common.REVISION}}, 'rootfs': {'type': 'layers',
            'diff_ids': ['sha256:' + a.digest(x) for x in layers] + ['sha256:' + 'f' * 64]},
            'history': [{'created_by': 'base'}, {'created_by': 'os'}, {'created_by':
                'COPY /workspace/kustomize-controller /usr/local/bin/ # buildkit'}]}).encode()
        manifest = json.dumps({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
            'config': {'mediaType': 'application/vnd.oci.image.config.v1+json',
                       'size': len(config), 'digest': 'sha256:' + a.digest(config)},
            'layers': [{'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip',
                        'size': len(x), 'digest': 'sha256:' + a.digest(x)} for x in blobs] + [{
                'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'size': 40000000,
                'digest': 'sha256:' + 'f' * 64}]}).encode()
        child = a.digest(manifest)
        scan = json.dumps({'Metadata': {'Reference': 'ghcr.io/fluxcd/kustomize-controller@sha256:' + child},
            'Results': [{'Class': 'os-pkgs', 'Packages': [{'Name': r['fields']['P'],
                'Version': r['fields']['V'], 'Arch': r['fields']['A'],
                'Digest': 'sha1:' + a.checksum_bytes(r['fields']['C']).hex()}
                for r in l.database(self.tree[l.DB_PATH]['data'])]}]}).encode()
        parent = {'apk_sha256': a.digest(self.package), 'index_sha256': a.digest(self.index),
            'package': 'pcre2', 'version': '10.49-r0', 'elf_payload': {
                n: e['sha256'] for n, e in self.build()[1]['members'].items()
                if n.startswith('usr/lib/') and e['type'] == 'file' and '/.wh.' not in n},
            'arch': 'aarch64', 'local_experimental_input_trust':
            'verified_against_digest_bound_retained_keys_only', 'signatures': {}, 'dependency_closure': {
                'candidate_reference': 'ghcr.io/fluxcd/kustomize-controller@sha256:' + child,
                'status': 'matched_retained_candidate_inventory', 'scan_sha256': a.digest(scan)}}
        for label, value in [('index', self.index), ('package', self.package)]:
            streams = a.gzip_streams(value, a.APK_LIMIT)
            signature = a.signature_stream(streams[0][1])
            parent['signatures'][label] = {'status': 'verified_against_retained_key', 'returncode': 0,
                'algorithm': 'RSA-SHA1', 'signed_sha256': a.digest(streams[1][0]),
                'signature_sha256': a.digest(signature['bytes']), 'key_name': signature['key_name'],
                'publisher_verified': False}
        return manifest, config, blobs, parent, scan, child

    def test_candidate_build_binds_os_prefix_and_review(self):
        manifest, config, blobs, parent, scan, child = self.candidate_inputs()
        raw = json.dumps(parent).encode()
        with patch.object(l, 'CHILD', child):
            layer, report = l.build_candidate(manifest, config, blobs, self.index, self.package,
                                            raw, a.digest(raw), scan)
            inputs = (manifest, config, blobs, self.index, self.package, raw, a.digest(raw), scan)
            l.validate_candidate_patch(layer, report, *inputs)
            self.fails(l.validate_candidate_patch, layer[:-1], report, *inputs)
            changed = dict(report, full_image_verified=True)
            self.fails(l.validate_candidate_patch, layer, changed, *inputs)
        self.assertEqual(layer, self.build()[0])
        self.assertEqual(report['candidate_child_sha256'], child)
        self.assertEqual(report['scope'], 'two_verified_os_layers_only_not_complete_image')
        self.assertFalse(report['full_image_verified'])

    def test_parent_signature_omission_or_failure_rejects(self):
        for missing in (True, False):
            manifest, config, blobs, parent, scan, child = self.candidate_inputs()
            if missing:
                del parent['signatures']['package']
            else:
                parent['signatures']['index']['status'] = 'hold_signature_failed'
            raw = json.dumps(parent).encode()
            with patch.object(l, 'CHILD', child):
                self.fails(l.build_candidate, manifest, config, blobs, self.index, self.package,
                           raw, a.digest(raw), scan)

    def test_parent_receipt_checksum_rejects(self):
        manifest, config, blobs, parent, scan, child = self.candidate_inputs()
        with patch.object(l, 'CHILD', child):
            self.fails(l.build_candidate, manifest, config, blobs, self.index, self.package,
                       json.dumps(parent).encode(), '0' * 64, scan)

    def test_inventory_missing_duplicate_or_changed_package_rejects(self):
        for change in ('missing', 'duplicate', 'version', 'digest'):
            manifest, config, blobs, parent, scan, child = self.candidate_inputs()
            observation = json.loads(scan)
            packages = observation['Results'][0]['Packages']
            if change == 'missing':
                packages.pop()
            elif change == 'duplicate':
                packages.append(packages[0])
            else:
                packages[0]['Version' if change == 'version' else 'Digest'] = 'wrong'
            scan = json.dumps(observation).encode()
            parent['dependency_closure']['scan_sha256'] = a.digest(scan)
            raw = json.dumps(parent).encode()
            with patch.object(l, 'CHILD', child):
                self.fails(l.build_candidate, manifest, config, blobs, self.index, self.package,
                           raw, a.digest(raw), scan)

    def test_candidate_missing_os_blob_rejects(self):
        manifest, config, blobs, parent, scan, child = self.candidate_inputs()
        raw = json.dumps(parent).encode()
        with patch.object(l, 'CHILD', child):
            self.fails(l.build_candidate, manifest, config, blobs[:1], self.index, self.package,
                       raw, a.digest(raw), scan)

    def test_wrong_config_arch_or_history_or_diff_closure_rejects(self):
        for change in ('arch', 'history', 'closure'):
            manifest, config, _, _, _, _ = self.candidate_inputs()
            settings = json.loads(config)
            if change == 'arch':
                settings['architecture'] = 'amd64'
            elif change == 'history':
                settings['history'][-1]['created_by'] = 'RUN arbitrary'
            else:
                settings['rootfs']['diff_ids'].pop()
            config = json.dumps(settings).encode()
            image = json.loads(manifest)
            image['config'].update(size=len(config), digest='sha256:' + a.digest(config))
            manifest = json.dumps(image).encode()
            with patch.object(l, 'CHILD', a.digest(manifest)):
                self.fails(l.candidate, manifest, config)

    def test_signed_payload_link_escape_rejects(self):
        _, index, package = fixture(lambda p: p['usr/lib/libpcre2-8.so.0'].update(target='../../../escape'))
        self.fails(l.build_patch, self.tree, index, package)

    def test_signed_payload_owner_or_setuid_rejects(self):
        for change in ({'uid': 1000}, {'mode': 0o4755}):
            _, index, package = fixture(lambda p: p['usr/lib/libpcre2-8.so.0.16.1'].update(change))
            self.fails(l.build_patch, self.tree, index, package)

    def test_signed_payload_wrong_elf_arch_rejects(self):
        _, index, package = fixture(lambda p: p['usr/lib/libpcre2-8.so.0.16.1'].update(data=b'not ELF'))
        self.fails(l.build_patch, self.tree, index, package)

    def test_signed_payload_extra_file_rejects(self):
        _, index, package = fixture(lambda p: p.update({'usr/lib/other': {'type': 'file', 'mode': 0o644, 'data': b'bad'}}))
        self.fails(l.build_patch, self.tree, index, package)


if __name__ == '__main__':
    unittest.main()
