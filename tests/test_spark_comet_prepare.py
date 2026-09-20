"""Offline Comet download, native-header and immutable input boundaries."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import uuid
import warnings
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('spark_comet_prepare', ROOT / 'tools/local_experiment/spark_comet/prepare.py')
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


def elf(*, machine=183, bits=2, byte_order=1, kind=3):
    value = bytearray(64)
    value[:7] = b'\x7fELF' + bytes((bits, byte_order, 1))
    value[16:18] = kind.to_bytes(2, 'little')
    value[18:20] = machine.to_bytes(2, 'little')
    value[20:24] = (1).to_bytes(4, 'little')
    value[52:54] = (64).to_bytes(2, 'little')
    return bytes(value)


def jar(*, classes=None, native=None, native_name=None, extra=(), compression=zipfile.ZIP_DEFLATED):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=compression) as archive:
        archive.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\n')
        for name, body in (classes if classes is not None else [(x, b'\xca\xfe\xba\xbe\x00\x00\x00\x3d') for x in P.REQUIRED_CLASSES]):
            archive.writestr(name, body)
        archive.writestr(native_name or P.NATIVE_ENTRY, native if native is not None else elf())
        for name, body in extra:
            archive.writestr(name, body)
    return stream.getvalue()


class Response(io.BytesIO):
    def __init__(self, url, body, *, length=None, status=200, encoding=None):
        super().__init__(body)
        self.url, self.status = url, status
        self.headers = {'Content-Length': str(len(body) if length is None else length)}
        if encoding is not None:
            self.headers['Content-Encoding'] = encoding

    def geturl(self):
        return self.url


class Opener:
    def __init__(self, body):
        self.body, self.calls = body, []
        self.transform = lambda req, response: response

    def open(self, request, timeout):
        self.calls.append((request.method, request.full_url))
        if request.full_url == P.URL:
            response = Response(P.URL, b'' if request.method == 'HEAD' else self.body, length=P.SIZE)
        elif request.full_url == P.URL + '.sha1':
            response = Response(request.full_url, (P.SHA1 + '\n').encode())
        elif request.full_url == P.URL + '.asc':
            response = Response(request.full_url, b'-----BEGIN PGP SIGNATURE-----\nfake-not-verified\n-----END PGP SIGNATURE-----\n')
        else:
            raise AssertionError('unexpected network request')
        return self.transform(request, response)


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.state = self.base / 'prepare'
        self.output = self.state / uuid.uuid4().hex
        self.body = jar()
        self.patchers = [patch.object(P, 'STATE', self.state), patch.object(P, 'SIZE', len(self.body)),
                         patch.object(P, 'SHA1', hashlib.sha1(self.body).hexdigest()),
                         patch.object(P, 'SHA256', hashlib.sha256(self.body).hexdigest())]
        for item in self.patchers:
            item.start()
        self.opener = Opener(self.body)

    def tearDown(self):
        for item in reversed(self.patchers):
            item.stop()
        self.temp.cleanup()

    def prepare(self):
        return P.prepare(self.output, opener=self.opener)

    def fails(self, code, pending=None):
        with self.assertRaisesRegex(P.PrepareError, code):
            self.prepare()
        receipt = json.loads((self.output / 'summary.json').read_text())
        self.assertEqual(receipt['result'], 'failed')
        self.assertEqual(receipt['pending'], pending)
        self.assertIsNone(receipt['artifact'])
        return receipt

    def test_missing_sha256_pin_precedes_allocation_and_any_network(self):
        for invalid in (None, '', '0' * 63, 'X' * 64):
            with self.subTest(pin=invalid), patch.object(P, 'SHA256', invalid):
                with self.assertRaisesRegex(P.PrepareError, 'COMET_SHA256_PIN_MISSING'):
                    self.prepare()
                with self.assertRaisesRegex(P.PrepareError, 'COMET_SHA256_PIN_MISSING'):
                    P.verified_artifact(self.output.name)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.opener.calls, [])

    def test_success_private_no_extraction_and_rechecked_build_identity(self):
        receipt, output = self.prepare()
        self.assertEqual(receipt['result'], 'passed')
        self.assertEqual(receipt['total_jar_bytes'], len(self.body))
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
        self.assertTrue(all(stat.S_IMODE(x.stat().st_mode) == 0o600 for x in output.iterdir()))
        self.assertFalse(any(x.is_dir() for x in output.iterdir()))
        self.assertFalse(receipt['runtime_started'])
        self.assertFalse(receipt['jvm_classpath_tested'])
        self.assertFalse(receipt['signature_verified'])
        self.assertEqual(receipt['artifact']['native_elf']['machine'], 183)
        self.assertIsNone(receipt['pending'])
        result = P.verified_artifact(output.name)
        self.assertEqual(result['path'], str(output / P.FILENAME))
        self.assertEqual(result['sha256'], hashlib.sha256(self.body).hexdigest())
        self.assertEqual(result['native_entry'], P.NATIVE_ENTRY)
        self.assertEqual(self.opener.calls, [('HEAD', P.URL), ('GET', P.URL + '.sha1'),
                                           ('GET', P.URL + '.asc'), ('GET', P.URL)])

    def test_metadata_size_failure_stops_before_get(self):
        self.opener.transform = lambda req, res: Response(req.full_url, b'', length=1)
        self.fails('HTTP_SIZE_MISMATCH', 'metadata')
        self.assertEqual(self.opener.calls, [('HEAD', P.URL)])

    def test_changed_published_sha1_stops_before_artifact_download(self):
        self.opener.transform = lambda req, res: Response(req.full_url, b'0' * 40) if req.full_url.endswith('.sha1') else res
        self.fails('PUBLISHED_SHA1_MISMATCH', 'metadata')
        self.assertNotIn(('GET', P.URL), self.opener.calls)

    def test_sha256_pin_independent_of_published_sha1(self):
        with patch.object(P, 'SHA256', '0' * 64):
            self.fails('JAR_DIGEST_MISMATCH', 'download')
        self.assertTrue((self.output / (P.FILENAME + '.part')).exists())
        self.assertFalse((self.output / P.FILENAME).exists())

    def test_wrong_sha1_is_rejected_even_if_sha256_matches(self):
        with patch.object(P, 'SHA1', '0' * 40):
            self.fails('JAR_DIGEST_MISMATCH', 'download')

    def test_truncated_and_oversized_bodies(self):
        for change, code in ((-1, 'HTTP_BODY_TRUNCATED'), (1, 'HTTP_BODY_TOO_LARGE')):
            with self.subTest(change=change):
                self.output = self.state / uuid.uuid4().hex
                self.opener.body = self.body[:-1] if change == -1 else self.body + b'x'
                self.fails(code, 'download')

    def test_redirect_encoding_status_and_missing_length_rejected(self):
        cases = [('url', 'https://invalid.example/', 'HTTP_RESPONSE_INVALID'),
                 ('status', 302, 'HTTP_RESPONSE_INVALID'), ('encoding', 'gzip', 'HTTP_ENCODING_INVALID'),
                 ('length', None, 'HTTP_LENGTH_INVALID')]
        for field, value, code in cases:
            with self.subTest(field=field):
                response = Response(P.URL, self.body)
                if field == 'encoding':
                    response.headers['Content-Encoding'] = value
                elif field == 'length':
                    del response.headers['Content-Length']
                else:
                    setattr(response, field, value)
                opener = type('Fixed', (), {'open': lambda *a, **k: response})()
                with self.assertRaisesRegex(P.PrepareError, code):
                    P.fetch(opener, P.URL)
        with self.assertRaisesRegex(P.PrepareError, 'REDIRECT_REJECTED'):
            P.NoRedirect().redirect_request(None, None, 302, None, None, 'https://invalid.example/')

    def test_foreign_url_and_method_do_not_open_transport(self):
        for url, method in (('https://invalid.example/', 'GET'), (P.URL, 'POST'),
                            (P.URL + '.sha256', 'GET'), (P.URL + '.asc', 'HEAD')):
            with self.subTest(url=url, method=method), self.assertRaisesRegex(P.PrepareError, 'URL_NOT_ALLOWED'):
                P.fetch(self.opener, url, method=method)
        self.assertEqual(self.opener.calls, [])

    def test_absolute_read_deadline_rechecked_for_each_chunk(self):
        with patch.object(P.time, 'monotonic', side_effect=(0, P.REQUEST_SECONDS + 1)):
            with self.assertRaisesRegex(P.PrepareError, 'DOWNLOAD_TIMEOUT'):
                P.fetch(self.opener, P.URL)

    def test_default_transport_disables_proxy_and_rejects_redirects(self):
        with patch.object(P.P.urllib.request, 'build_opener') as build:
            P.official_opener()
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsInstance(build.call_args.args[1], P.NoRedirect)

    def test_scope_existing_output_and_symlink_never_open_transport(self):
        with self.assertRaisesRegex(P.PrepareError, 'OUTPUT_SCOPE_INVALID'):
            P.prepare(self.base / 'elsewhere', opener=self.opener)
        self.assertFalse(self.state.exists())
        self.state.mkdir(mode=0o700)
        self.output.symlink_to(self.base, target_is_directory=True)
        with self.assertRaisesRegex(P.PrepareError, 'OUTPUT_ALREADY_USED'):
            self.prepare()
        self.output.unlink()
        self.output.mkdir(mode=0o700)
        with self.assertRaisesRegex(P.PrepareError, 'OUTPUT_ALREADY_USED'):
            self.prepare()
        self.assertEqual(self.opener.calls, [])

    def test_public_or_linked_state_is_rejected_without_chmod(self):
        self.state.mkdir(mode=0o755)
        with self.assertRaisesRegex(P.PrepareError, 'DIRECTORY_NOT_PRIVATE'):
            self.prepare()
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o755)
        self.state.rmdir()
        self.state.symlink_to(self.base, target_is_directory=True)
        with self.assertRaisesRegex(P.PrepareError, 'DIRECTORY_INVALID'):
            self.prepare()
        self.assertEqual(self.opener.calls, [])

    def test_disk_check_before_network(self):
        with patch.object(P.shutil, 'disk_usage', return_value=type('Disk', (), {'free': 1})()):
            self.fails('INSUFFICIENT_DISK_HEADROOM')
        self.assertEqual(self.opener.calls, [])

    def test_interruption_preserves_partial_receipt_and_original_exception(self):
        interruption = KeyboardInterrupt()
        def stop(req, response):
            if req.method == 'GET' and req.full_url == P.URL:
                raise interruption
            return response
        self.opener.transform = stop
        with self.assertRaises(KeyboardInterrupt) as caught:
            self.prepare()
        self.assertIs(caught.exception, interruption)
        receipt = json.loads((self.output / 'summary.json').read_text())
        self.assertEqual(receipt['result'], 'interrupted')
        self.assertEqual(receipt['pending'], 'download')
        self.assertIsNone(receipt['artifact'])

    def test_interruption_survives_failed_failure_receipt_write(self):
        interruption = KeyboardInterrupt()
        original = P.write
        def write(path, value):
            if value.get('result') == 'interrupted':
                raise OSError('disk write failed')
            original(path, value)
        def stop(*args):
            raise interruption
        self.opener.transform = stop
        with patch.object(P, 'write', side_effect=write), self.assertRaises(KeyboardInterrupt) as caught:
            self.prepare()
        self.assertIs(caught.exception, interruption)

    def test_transport_error_details_not_persisted(self):
        def stop(*args):
            raise RuntimeError('ambient-secret-must-not-persist')
        self.opener.transform = stop
        with self.assertRaises(RuntimeError):
            self.prepare()
        raw = (self.output / 'summary.json').read_text()
        self.assertNotIn('ambient-secret', raw)
        self.assertEqual(json.loads(raw)['failure_code'], 'PREPARATION_FAILED')

    def test_build_reader_rejects_changed_jar_and_metadata(self):
        receipt, output = self.prepare()
        for suffix, code in (('', 'JAR_DIGEST_MISMATCH'), ('.asc', 'SIGNATURE_BYTES_CHANGED'),
                             ('.zip.json', 'ZIP_INVENTORY_CHANGED')):
            path = output / (P.FILENAME + suffix)
            raw = path.read_bytes()
            path.write_bytes(b'x' * len(raw))
            with self.subTest(suffix=suffix), self.assertRaisesRegex(P.PrepareError, code):
                P.verified_artifact(output.name)
            path.write_bytes(raw)

    def test_build_reader_receipt_paths_identity_flags_native_and_duplicate_keys(self):
        receipt, output = self.prepare()
        path = output / 'summary.json'
        for change in ('path', 'token', 'failed', 'native', 'runtime', 'duplicate'):
            value = json.loads(json.dumps(receipt))
            if change == 'path':
                value['artifact']['file'] = '../foreign.jar'
            elif change == 'token':
                value['token'] = 'a' * 32
            elif change == 'failed':
                value['result'] = 'failed'
            elif change == 'native':
                value['artifact']['native_elf']['machine'] = 62
            elif change == 'runtime':
                value['runtime_started'] = True
            raw = json.dumps(value)
            if change == 'duplicate':
                raw = raw[:-1] + ',"result":"passed"}'
            path.write_text(raw)
            with self.subTest(change=change), self.assertRaises(P.PrepareError):
                P.verified_artifact(output.name)

    def test_build_reader_rejects_public_hardlinked_and_symlinked_jar(self):
        receipt, output = self.prepare()
        path = output / P.FILENAME
        path.chmod(0o644)
        with self.assertRaisesRegex(P.PrepareError, 'PRIVATE_FILE_INVALID'):
            P.verified_artifact(output.name)
        path.chmod(0o600)
        other = output / 'other'
        os.link(path, other)
        with self.assertRaisesRegex(P.PrepareError, 'PRIVATE_FILE_INVALID'):
            P.verified_artifact(output.name)
        other.unlink()
        path.rename(other)
        path.symlink_to(other)
        with self.assertRaises(OSError):
            P.verified_artifact(output.name)


class ArchiveTests(unittest.TestCase):
    def inspect(self, body):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.jar'
            path.write_bytes(body)
            return P.inspect_zip(path)

    def test_native_architecture_format_type_and_header_length(self):
        cases = [(elf(machine=62), 'NATIVE_ELF_ARCHITECTURE_INVALID'),
                 (elf(bits=1), 'NATIVE_ELF_FORMAT_INVALID'),
                 (elf(byte_order=2), 'NATIVE_ELF_FORMAT_INVALID'),
                 (elf(kind=2), 'NATIVE_ELF_ARCHITECTURE_INVALID'),
                 (b'not ELF', 'NATIVE_ELF_HEADER_INVALID')]
        for native, code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(P.PrepareError, code):
                self.inspect(jar(native=native))

    def test_native_resource_requires_exact_loader_path(self):
        with self.assertRaisesRegex(P.PrepareError, 'ZIP_REQUIRED_CONTENT_MISSING'):
            self.inspect(jar(native_name='untrusted/aarch64/libcomet.so'))

    def test_required_class_headers_and_java17_limit(self):
        for body, code in ((b'wrong', 'CLASS_HEADER_INVALID'),
                           (b'\xca\xfe\xba\xbe\x00\x00\x00\x41', 'CLASS_REQUIRES_NEWER_JAVA')):
            with self.subTest(code=code), self.assertRaisesRegex(P.PrepareError, code):
                self.inspect(jar(classes=[(x, body) for x in P.REQUIRED_CLASSES]))
        with self.assertRaisesRegex(P.PrepareError, 'ZIP_REQUIRED_CONTENT_MISSING'):
            self.inspect(jar(classes=[]))

    def test_traversal_duplicate_executable_and_symlink_rejected(self):
        for path in ('../escaped', '/absolute', 'bad\\windows'):
            with self.subTest(path=path), self.assertRaisesRegex(P.PrepareError, 'ZIP_PATH_INVALID'):
                self.inspect(jar(extra=[(path, b'x')]))
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            body = jar(extra=[(P.NATIVE_ENTRY, elf())])
        with self.assertRaisesRegex(P.PrepareError, 'ZIP_PATH_INVALID'):
            self.inspect(body)
        link = zipfile.ZipInfo('link')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaisesRegex(P.PrepareError, 'ZIP_TYPE_INVALID'):
            self.inspect(jar(extra=[(link, b'target')]))

    def test_crc_reads_every_member_including_first_duplicate_notice(self):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            body = jar(extra=[('LICENSE', b'first'), ('LICENSE', b'second')], compression=zipfile.ZIP_STORED)
        value = self.inspect(body)
        self.assertEqual([x['occurrence'] for x in value['entries'] if x['name'] == 'LICENSE'], [1, 2])
        raw = bytearray(body)
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            item = next(x for x in archive.infolist() if x.filename == 'LICENSE')
            offset = item.header_offset + 30 + len(item.filename.encode()) + len(item.extra)
            raw[offset] ^= 0x01
        with self.assertRaises((zipfile.BadZipFile, zlib.error)):
            self.inspect(bytes(raw))

    def test_expansion_entry_and_compression_limits(self):
        for constant, value, code in (('MAX_ENTRIES', 1, 'ZIP_ENTRY_LIMIT'),
                                      ('MAX_ENTRY_BYTES', 16, 'ZIP_SIZE_INVALID'),
                                      ('MAX_UNCOMPRESSED_BYTES', 1, 'ZIP_TOTAL_LIMIT')):
            with self.subTest(constant=constant), patch.object(P, constant, value):
                with self.assertRaisesRegex(P.PrepareError, code):
                    self.inspect(jar())
        with self.assertRaisesRegex(P.PrepareError, 'ZIP_COMPRESSION_INVALID'):
            self.inspect(jar(compression=zipfile.ZIP_BZIP2))


if __name__ == '__main__':
    unittest.main()
