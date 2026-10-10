"""Offline synthetic fixtures; no fetch, install, compiler or retained input access."""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.local_experiment import kustomize_os_repair as r


KEY_NAME = 'fixture@example.org.rsa.pub'
KEY = b'-----BEGIN PUBLIC KEY-----\nfixture\n-----END PUBLIC KEY-----\n'


def tar(entries, pax=None):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for name, value in entries:
            item = tarfile.TarInfo(name)
            item.pax_headers = pax or {}
            if isinstance(value, tuple):
                item.type, item.linkname = value
                archive.addfile(item)
            else:
                item.size = len(value)
                archive.addfile(item, io.BytesIO(value))
    return buffer.getvalue()


def gz(value):
    return gzip.compress(value, mtime=0)


SIG = gz(tar([('.SIGN.RSA.' + KEY_NAME, b'x' * 128)]))


def fixtures(info_change=None, payload=None, control_extra=None):
    data = gz(tar(payload or [('usr/lib/libpcre2.so.0', b'fixture')]))
    info = {'pkgname': ['pcre2'], 'pkgver': ['10.49-r0'], 'arch': ['aarch64'],
            'origin': ['pcre2'], 'commit': ['a' * 40],
            'depend': ['so:libc.musl-aarch64.so.1'], 'datahash': [r.digest(data)]}
    if info_change:
        info.update(info_change)
    text = ''.join(f'{k} = {v}\n' for k, values in info.items() for v in values).encode()
    control = gz(tar([('.PKGINFO', text)] + (control_extra or [])))
    apk = SIG + control + data
    selected = {'P': 'pcre2', 'V': '10.49-r0', 'A': 'aarch64', 'S': str(len(apk)),
                'C': 'Q1' + base64.b64encode(hashlib.sha1(control).digest()).decode(),
                'o': 'pcre2', 'c': 'a' * 40, 'D': 'so:libc.musl-aarch64.so.1'}
    musl = {'P': 'musl', 'V': '1.2.5-r23', 'A': 'aarch64', 'S': '1',
            'C': selected['C'], 'p': 'so:libc.musl-aarch64.so.1=1'}
    raw_index = '\n\n'.join('\n'.join(k + ':' + v for k, v in x.items())
                           for x in [selected, musl]).encode() + b'\n\n'
    index = SIG + gz(tar([('APKINDEX', raw_index), ('DESCRIPTION', b'fixture')]))
    return index, apk, selected


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.temp.cleanup)
        self.scope = Path(self.temp.name)
        self.scope.chmod(0o700)
        self.index, self.apk, self.selected = fixtures()

    def fails(self, error, call, *args):
        with self.assertRaisesRegex(r.RepairError, error):
            call(*args)

    def test_valid_package_and_provider(self):
        records = r.parse_index(self.index)
        self.assertEqual(r.select_pcre2(records), self.selected)
        result = r.validate_apk(self.apk, self.selected)
        self.assertFalse(result['publisher_verified'])
        self.assertFalse(result['installed_highs_mitigated'])
        self.assertEqual(r.dependency_candidates(records, result['dependencies'])[
            result['dependencies'][0]]['package'], 'musl')

    def test_old_or_prerelease_version_rejects(self):
        for version in ['10.48-r9', '10.49_rc1-r0', '10.49-r0/../x', '10.49-r0?x']:
            selected = dict(self.selected, V=version)
            self.fails('PCRE2_BELOW_FIX|VERSION_UNSUPPORTED', r.select_pcre2, [selected])

    def test_numeric_version_not_lexicographic(self):
        self.assertGreater(r.version_tuple('10.100-r0'), r.version_tuple('10.49-r0'))

    def test_wrong_arch_and_ambiguous_selection(self):
        self.fails('SELECTION', r.select_pcre2, [dict(self.selected, A='x86_64')])
        self.fails('SELECTION', r.select_pcre2, [self.selected, self.selected])

    def test_provenance_and_size(self):
        for key, value, error in [('c', 'bad', 'PROVENANCE'), ('o', 'other', 'PROVENANCE'),
                                   ('S', '-1', 'SIZE'), ('S', str(r.APK_LIMIT + 1), 'SIZE')]:
            self.fails(error, r.select_pcre2, [dict(self.selected, **{key: value})])

    def test_checksum_type_length_encoding(self):
        for value in ['Q2abcd', 'Q1???', 'Q1YWJj', 'Q1']:
            self.fails('CHECKSUM', r.checksum_bytes, value)

    def test_index_checksum_and_size_mismatch(self):
        self.fails('APK_INDEX_CHECKSUM', r.validate_apk, self.apk,
                   dict(self.selected, C='Q1' + base64.b64encode(b'0' * 20).decode()))
        self.fails('APK_SIZE', r.validate_apk, self.apk, dict(self.selected, S='1'))

    def test_pkginfo_identity_all_fields(self):
        for key in ['pkgname', 'pkgver', 'arch', 'origin', 'commit']:
            _, apk, selected = fixtures({key: ['wrong']})
            self.fails('PKGINFO_IDENTITY', r.validate_apk, apk, selected)

    def test_dependencies_and_payload_hash(self):
        for change, error in [({'depend': ['other']}, 'DEPENDENCY_MISMATCH'),
                              ({'datahash': ['0' * 64]}, 'APK_DATAHASH')]:
            _, apk, selected = fixtures(change)
            self.fails(error, r.validate_apk, apk, selected)

    def test_pkginfo_duplicate(self):
        self.fails('PKGINFO_DUPLICATE', r.parse_pkginfo, b'arch = aarch64\narch = aarch64\n')

    def test_package_scripts_reject(self):
        _, apk, selected = fixtures(control_extra=[('.post-install', b'never execute')])
        self.fails('CONTROL_UNSUPPORTED', r.validate_apk, apk, selected)

    def test_archive_paths(self):
        for name in ['../bad', '/absolute', 'usr/../bad', 'usr//bad', 'usr\\bad']:
            self.fails('ARCHIVE_PATH', r.tar_members, tar([(name, b'x')]))

    def test_links_never_followed(self):
        raw = tar([('usr/lib/link', (tarfile.SYMTYPE, 'target'))])
        self.fails('ARCHIVE_LINK', r.tar_members, raw)
        self.assertEqual(r.tar_members(raw, True)['usr/lib/link']['target'], 'target')
        self.fails('ARCHIVE_PATH', r.tar_members,
                   tar([('link', (tarfile.SYMTYPE, '../../escape'))]), True)

    def test_special_and_duplicate_members(self):
        self.fails('ARCHIVE_SPECIAL', r.tar_members, tar([('dev', (tarfile.FIFOTYPE, ''))]))
        self.fails('DUPLICATE_MEMBER', r.tar_members, tar([('x', b'a'), ('x', b'b')]))

    def test_pax_whitelist_and_member_checksum(self):
        self.assertEqual(r.tar_members(tar([('x', b'a')], {'mtime': '0'}))['x'], b'a')
        self.fails('PAX_UNSUPPORTED|ARCHIVE_PATH', r.tar_members,
                   tar([('x', b'a')], {'path': '../bad'}))
        self.fails('MEMBER_CHECKSUM', r.tar_members,
                   tar([('x', b'a')], {'APK-TOOLS.checksum.SHA1': '0' * 40}))

    def test_gzip_crc_truncation_trailing_and_bomb(self):
        for value in [self.index[:-4], self.index + b'junk', self.index[:-1] + b'X']:
            self.fails('GZIP', r.gzip_streams, value, r.INDEX_LIMIT)
        with patch.object(r, 'EXPANDED_LIMIT', 20):
            self.fails('LIMIT', r.gzip_streams, gz(b'a' * 100), r.INDEX_LIMIT)

    def test_stream_counts_and_signature_format(self):
        self.fails('INDEX_STREAM_COUNT', r.parse_index, SIG)
        self.fails('SIGNATURE_FORMAT', r.signature_stream, tar([('.SIGN.DSA.key', b'x' * 128)]))
        self.fails('SIGNATURE_MEMBER_COUNT', r.signature_stream,
                   tar([('.SIGN.RSA.' + KEY_NAME, b'x' * 128), ('extra', b'x')]))

    def test_tar_truncated_hidden_trailing_and_header_checksum(self):
        raw = tar([('x', b'a')])
        for bad, error in [(raw[:513], 'TAR_BLOCKS'), (raw[:512], 'TAR_TRUNCATED'),
                           (raw + tar([('hidden', b'b')]), 'TAR_TRAILING_DATA'),
                           (b'X' + raw[1:], 'TAR_HEADER')]:
            self.fails(error, r.tar_members, bad)

    def test_global_pax_and_unknown_metadata_reject(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w', format=tarfile.PAX_FORMAT,
                          pax_headers={'mtime': '0'}) as archive:
            item = tarfile.TarInfo('x')
            archive.addfile(item)
        self.fails('EXTENSION', r.tar_members, buffer.getvalue())
        self.fails('PAX_UNSUPPORTED', r.tar_members, tar([('x', b'a')], {'unknown': 'a'}))

    def test_duplicate_index_fields_packages_and_extra_member(self):
        for raw, error in [(b'P:pcre2\nP:other\n', 'DUPLICATE_FIELD'),
                           (b'broken\n', 'INDEX_FIELD'), (b'P:pcre2\n', 'INDEX_REQUIRED')]:
            self.fails(error, r.parse_index, SIG + gz(tar([('APKINDEX', raw)])))
        self.fails('INDEX_MEMBERS', r.parse_index,
                   SIG + gz(tar([('APKINDEX', b''), ('extra', b'x')])))

    def test_dependency_hold_constraints_missing_and_ambiguity(self):
        records = r.parse_index(self.index)
        self.fails('CONSTRAINT_UNSUPPORTED', r.dependency_candidates, records, ['musl>=1'])
        self.fails('MISSING', r.dependency_candidates, records, ['missing'])
        self.fails('AMBIGUOUS', r.dependency_candidates, records + [records[1]], ['musl'])

    def test_exclusive_private_writes_and_read_symlink(self):
        r.write_once(self.scope, 'once', b'x')
        self.assertEqual((self.scope / 'once').stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            r.write_once(self.scope, 'once', b'y')
        (self.scope / 'link').symlink_to('once')
        with self.assertRaises(OSError):
            r.read_private(self.scope, 'link')
        self.fails('INVALID_FILENAME', r.write_once, self.scope, '../bad', b'x')

    def test_retained_anchor_stream_hash_and_key_link(self):
        for linked in [False, True]:
            value = (tarfile.SYMTYPE, 'other') if linked else KEY
            layer = gz(tar([('etc/apk/keys/' + KEY_NAME, value)]))
            archive = tar([(r.digest(layer) + '.tar.gz', layer)])
            path = self.scope / ('anchor-link.tar' if linked else 'anchor.tar')
            path.write_bytes(archive)
            self.fails('ANCHOR_HASH_MISMATCH', r.retained_keys, path, '0' * 64)
            if linked:
                self.fails('ANCHOR_KEY_UNSAFE', r.retained_keys, path, r.digest(archive))
            else:
                self.assertEqual(r.retained_keys(path, r.digest(archive)), {KEY_NAME: KEY})

    def test_stream_read_bound(self):
        self.fails('STREAM_LIMIT', r.BoundedReader(io.BytesIO(b'123'), 2).read, 10)

    def test_signature_missing_failure_and_fixed_command(self):
        self.assertEqual(r.verify_signature(self.scope, 'index', self.index, {})['status'],
                         'hold_key_missing')
        process = SimpleNamespace(returncode=1, stdout=b'Verification failure\n', stderr=b'')
        with patch.object(r.subprocess, 'run', return_value=process) as run:
            result = r.verify_signature(self.scope, 'index', self.index, {KEY_NAME: KEY})
        self.assertEqual(result['status'], 'hold_signature_failed')
        self.assertFalse(result['publisher_verified'])
        self.assertEqual(run.call_args.args[0][:3], ['/usr/bin/openssl', 'dgst', '-sha1'])
        self.assertNotIn('shell', run.call_args.kwargs)

    def receipts(self):
        return {name: {'schema_version': 1, 'outcome': 'retrieved', 'url': r.BASE_URL + name,
                       'bytes': len(data), 'sha256': r.digest(data),
                       'started_unix': 1., 'finished_unix': 2., 'publisher_verified': False}
                for name, data in [('APKINDEX.tar.gz', self.index),
                                   (r.package_filename(self.selected), self.apk)]}

    def test_evidence_rederives_receipt_identity_and_holds(self):
        signatures = {label: r.verify_signature(self.scope, label, value, {})
                      for label, value in [('index', self.index), ('package', self.apk)]}
        result = r.evidence(self.index, self.apk, self.receipts(), signatures)
        self.assertFalse(result['l0'])
        self.assertTrue(result['holds'])
        for field, value in [('url', 'https://evil/'), ('sha256', '0' * 64),
                             ('bytes', 0), ('finished_unix', 100.)]:
            receipts = self.receipts()
            receipts['APKINDEX.tar.gz'][field] = value
            self.fails('FETCH_', r.evidence, self.index, self.apk, receipts, signatures)
        signatures['index']['status'] = 'publisher_verified'
        self.fails('SIGNATURE_STATUS', r.evidence, self.index, self.apk, self.receipts(), signatures)

    def test_fetch_reserved_failure_never_retried(self):
        with patch.object(r, 'budget', return_value={}), patch.object(
                r.urllib.request, 'build_opener') as build:
            build.return_value.open.side_effect = TimeoutError()
            with self.assertRaises(TimeoutError):
                r.fetch(self.scope)
            with self.assertRaises(FileExistsError):
                r.fetch(self.scope)
            self.assertEqual(build.return_value.open.call_count, 1)
        self.assertTrue((self.scope / 'APKINDEX.tar.gz.fetch-held.json').exists())

    def test_fetch_selection_rederived_before_network(self):
        r.write_once(self.scope, 'APKINDEX.tar.gz', self.index)
        with patch.object(r.urllib.request, 'build_opener') as build:
            self.fails('SELECTION_CHANGED', r.fetch, self.scope, dict(self.selected, V='10.50-r0'))
            build.assert_not_called()

    def test_fetch_success_and_response_bounds(self):
        response = SimpleNamespace(status=200, headers={'Content-Length': str(len(self.index))},
                                   geturl=lambda: r.BASE_URL + 'APKINDEX.tar.gz',
                                   read=lambda size: self.index)
        with patch.object(r, 'budget', return_value={}), patch.object(
                r.urllib.request, 'build_opener') as build:
            build.return_value.open.return_value.__enter__.return_value = response
            result = r.fetch(self.scope)
            self.assertEqual(result['sha256'], r.digest(self.index))
            self.assertEqual(build.return_value.open.call_count, 1)
        self.assertEqual(r.read_private(self.scope, 'APKINDEX.tar.gz'), self.index)

    def test_fetch_oversize_declared_length(self):
        response = SimpleNamespace(status=200, headers={'Content-Length': str(r.INDEX_LIMIT + 1)},
                                   geturl=lambda: r.BASE_URL + 'APKINDEX.tar.gz')
        with patch.object(r, 'budget', return_value={}), patch.object(
                r.urllib.request, 'build_opener') as build:
            build.return_value.open.return_value.__enter__.return_value = response
            self.fails('RESPONSE_TOO_LARGE', r.fetch, self.scope)
        self.assertFalse((self.scope / 'APKINDEX.tar.gz').exists())

    def test_budget_combined_free_and_scope_bounds(self):
        self.assertFalse(r.budget(self.scope)['unknown_scope_observed'])
        with patch.object(r, 'PARENT_RETAINED_BYTES', r.common.COMBINED_LIMIT):
            self.fails('COMBINED_BUDGET', r.budget, self.scope)
        with patch.object(r, 'SCOPE_LIMIT', 10):
            self.fails('SCOPE_BUDGET', r.budget, self.scope)
        with patch.object(r.common, 'FREE_MIN', 2**80):
            self.fails('FREE_BUDGET', r.budget, self.scope)

    def test_forged_verified_signature_report_rejects(self):
        signatures = {label: r.verify_signature(self.scope, label, value, {})
                      for label, value in [('index', self.index), ('package', self.apk)]}
        signatures['index'].update(status='verified_against_retained_key', returncode=0,
                                   signed_sha256='0' * 64, algorithm='RSA-SHA1',
                                   signing_domain='compressed_control_gzip')
        self.fails('SIGNATURE_PROVENANCE', r.evidence, self.index, self.apk,
                   self.receipts(), signatures)


if __name__ == '__main__':
    unittest.main()
