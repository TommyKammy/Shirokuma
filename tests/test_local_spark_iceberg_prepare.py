"""Offline transport, integrity and private-output boundaries; no runtime calls."""
import hashlib
import importlib.util
import io
import json
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
SPEC = importlib.util.spec_from_file_location('spark_iceberg_prepare', ROOT / 'tools/local_experiment/spark_iceberg/prepare.py')
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


def jar(required, extra=()):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\n')
        for name in required:
            archive.writestr(name, b'\xca\xfe\xba\xbe\x00\x00\x00\x3dtest')
        for name, data in extra:
            archive.writestr(name, data)
    return output.getvalue()


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
    def __init__(self, artifacts, bodies):
        self.artifacts, self.bodies, self.calls = artifacts, bodies, []
        self.transform = lambda request, response: response

    def open(self, request, timeout):
        self.calls.append((request.method, request.full_url))
        for spec in self.artifacts:
            url = P.url_for(spec)
            if request.full_url == url:
                body = self.bodies[spec['artifact']]
                response = Response(url, b'' if request.method == 'HEAD' else body, length=spec['size'])
                return self.transform(request, response)
            for algorithm in ('sha512', 'sha256'):
                if request.full_url == url + '.' + algorithm:
                    return self.transform(request, Response(request.full_url, spec[algorithm].encode()))
            if request.full_url == url + '.asc':
                return self.transform(request, Response(request.full_url, b'-----BEGIN PGP SIGNATURE-----\nnot-verified\n'))
        raise AssertionError('unexpected URL')


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.state = self.base / 'spark-iceberg-prepare'
        self.bodies = {spec['artifact']: jar(spec['required_classes']) for spec in P.ARTIFACTS}
        self.artifacts = tuple({**spec, 'size': len(self.bodies[spec['artifact']]),
                               **{key: hashlib.new(key, self.bodies[spec['artifact']]).hexdigest()
                                  for key in ('sha256', 'sha512')}} for spec in P.ARTIFACTS)
        self.patchers = [patch.object(P, 'STATE', self.state), patch.object(P, 'ARTIFACTS', self.artifacts)]
        for item in self.patchers:
            item.start()
        self.opener = Opener(self.artifacts, self.bodies)
        self.output = self.state / uuid.uuid4().hex

    def tearDown(self):
        for item in reversed(self.patchers):
            item.stop()
        self.temporary.cleanup()

    def run_prepare(self):
        return P.prepare(self.output, opener=self.opener)

    def assert_failed(self, code, stage=None):
        with self.assertRaisesRegex(P.PrepareError, code):
            self.run_prepare()
        result = json.loads((self.output / 'summary.json').read_text())
        self.assertEqual(result['result'], 'failed')
        if stage is not None:
            self.assertEqual(result['pending']['stage'], stage)
        return result

    def test_success_preserves_pins_zip_and_unverified_signature_boundary(self):
        result, output = self.run_prepare()
        self.assertEqual(result['result'], 'passed')
        self.assertEqual(result['total_jar_bytes'], sum(x['size'] for x in self.artifacts))
        self.assertFalse(result['jvm_classpath_tested'])
        self.assertFalse(result['signature_verified'])
        self.assertFalse(result['runtime_started'])
        self.assertIsNone(result['pending'])
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
        self.assertTrue(all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in output.iterdir()))
        for expected, actual in zip(self.artifacts, result['artifacts']):
            self.assertEqual(actual['sha512'], expected['sha512'])
            self.assertEqual((output / actual['file']).read_bytes(), self.bodies[expected['artifact']])
            content = json.loads((output / (actual['file'] + '.zip.json')).read_text())
            self.assertTrue(content['crc_verified'])
            self.assertFalse(content['extracted'])
            self.assertEqual(content['required_classes'][0]['major_version'], 61)
        self.assertEqual(len(self.opener.calls), 10)

    def test_head_size_mismatch_stops_before_download(self):
        self.opener.transform = lambda request, response: Response(request.full_url, b'', length=1)
        self.assert_failed('HTTP_SIZE_MISMATCH', 'metadata')
        self.assertEqual(len(self.opener.calls), 1)

    def test_changed_published_checksum_is_rejected_before_jar_download(self):
        self.opener.transform = lambda request, response: Response(request.full_url, b'0' * 128) if request.full_url.endswith('.sha512') else response
        self.assert_failed('PUBLISHED_SHA512_MISMATCH', 'metadata')
        self.assertFalse(any(method == 'GET' and url.endswith('.jar') for method, url in self.opener.calls))

    def test_corrupt_jar_preserved_as_partial_but_never_claimed_verified(self):
        first = self.artifacts[0]
        self.bodies[first['artifact']] = b'x' * first['size']
        result = self.assert_failed('JAR_DIGEST_MISMATCH', 'download')
        self.assertEqual(result['artifacts'], [])
        self.assertTrue((self.output / (P.filename(first) + '.part')).is_file())
        self.assertFalse((self.output / P.filename(first)).exists())

    def test_truncated_body_rejected(self):
        first = self.artifacts[0]
        self.bodies[first['artifact']] = self.bodies[first['artifact']][:-1]
        self.assert_failed('HTTP_BODY_TRUNCATED', 'download')

    def test_oversized_body_is_stopped(self):
        first = self.artifacts[0]
        self.bodies[first['artifact']] += b'x'
        self.assert_failed('HTTP_BODY_TOO_LARGE', 'download')

    def test_redirect_response_and_encoding_are_rejected(self):
        for changes, code in (({'url': 'https://untrusted.invalid/'}, 'HTTP_RESPONSE_INVALID'),
                              ({'encoding': 'gzip'}, 'HTTP_ENCODING_INVALID')):
            with self.subTest(code=code):
                response = Response(changes.get('url', P.url_for(self.artifacts[0])), b'abc', encoding=changes.get('encoding'))
                opener = type('Fixed', (), {'open': lambda s, *a, **k: response})()
                with self.assertRaisesRegex(P.PrepareError, code):
                    P.fetch(opener, P.url_for(self.artifacts[0]))
        with self.assertRaisesRegex(P.PrepareError, 'REDIRECT_REJECTED'):
            P.NoRedirect().redirect_request(None, None, 302, None, None, 'https://example.org/')

    def test_foreign_url_and_existing_output_never_open_network(self):
        with self.assertRaisesRegex(P.PrepareError, 'URL_NOT_ALLOWED'):
            P.fetch(self.opener, 'https://example.org/arbitrary.jar')
        self.assertEqual(self.opener.calls, [])
        self.output.mkdir(parents=True, mode=0o700)
        self.state.chmod(0o700)
        with self.assertRaisesRegex(P.PrepareError, 'OUTPUT_ALREADY_USED'):
            self.run_prepare()
        self.assertEqual(self.opener.calls, [])

    def test_output_scope_symlink_and_public_state_are_rejected(self):
        with self.assertRaisesRegex(P.PrepareError, 'OUTPUT_SCOPE_INVALID'):
            P.prepare(self.base / 'foreign', opener=self.opener)
        self.output.symlink_to(self.base, target_is_directory=True)
        with self.assertRaisesRegex(P.PrepareError, 'OUTPUT_ALREADY_USED'):
            self.run_prepare()
        self.output.unlink()
        self.state.chmod(0o755)
        with self.assertRaisesRegex(P.PrepareError, 'DIRECTORY_NOT_PRIVATE'):
            self.run_prepare()
        self.assertEqual(self.opener.calls, [])

    def test_insufficient_disk_precedes_network(self):
        with patch.object(P.shutil, 'disk_usage', return_value=type('Disk', (), {'free': 1})()):
            self.assert_failed('INSUFFICIENT_DISK_HEADROOM')
        self.assertEqual(self.opener.calls, [])

    def test_interruption_leaves_pending_receipt_and_original_signal(self):
        interruption = KeyboardInterrupt()
        def fail(request, response):
            if request.method == 'GET' and request.full_url.endswith('.jar'):
                raise interruption
            return response
        self.opener.transform = fail
        with self.assertRaises(KeyboardInterrupt) as caught:
            self.run_prepare()
        self.assertIs(caught.exception, interruption)
        result = json.loads((self.output / 'summary.json').read_text())
        self.assertEqual(result['result'], 'interrupted')
        self.assertEqual(result['pending']['stage'], 'download')
        self.assertEqual(result['artifacts'], [])

    def test_transport_error_text_is_not_saved(self):
        secret = 'do-not-save-proxy-credential'
        def fail(*args):
            raise RuntimeError(secret)
        self.opener.transform = fail
        with self.assertRaises(RuntimeError):
            self.run_prepare()
        raw = (self.output / 'summary.json').read_text()
        self.assertNotIn(secret, raw)
        self.assertEqual(json.loads(raw)['failure_code'], 'PREPARATION_FAILED')

    def test_build_reader_rehashes_actual_pinned_jars(self):
        result, output = self.run_prepare()
        values = P.verified_artifacts(output.name)
        self.assertEqual(len(values), 2)
        self.assertEqual(values[0]['path'], str(output / result['artifacts'][0]['file']))
        path = Path(values[0]['path'])
        path.write_bytes(b'x' * path.stat().st_size)
        with self.assertRaisesRegex(P.PrepareError, 'JAR_DIGEST_MISMATCH'):
            P.verified_artifacts(output.name)

    def test_build_reader_rejects_status_path_and_duplicate_receipt_keys(self):
        result, output = self.run_prepare()
        receipt = output / 'summary.json'
        for changed in ('status', 'path', 'duplicate'):
            value = json.loads(json.dumps(result))
            if changed == 'status':
                value['result'] = 'failed'
            elif changed == 'path':
                value['artifacts'][0]['file'] = '../other.jar'
            raw = json.dumps(value)
            if changed == 'duplicate':
                raw = raw[:-1] + ',"result":"passed"}'
            receipt.write_text(raw)
            with self.subTest(changed=changed), self.assertRaises(P.PrepareError):
                P.verified_artifacts(output.name)

    def test_build_reader_rejects_unprivate_or_linked_inputs(self):
        result, output = self.run_prepare()
        path = output / result['artifacts'][0]['file']
        path.chmod(0o644)
        with self.assertRaisesRegex(P.PrepareError, 'PRIVATE_FILE_INVALID'):
            P.verified_artifacts(output.name)
        path.chmod(0o600)
        saved = output / 'other'
        path.rename(saved)
        path.symlink_to(saved)
        with self.assertRaises(OSError):
            P.verified_artifacts(output.name)

    def test_interruption_survives_failure_receipt_write_error(self):
        interruption = KeyboardInterrupt()
        original = P.write
        def write(path, value):
            if value.get('result') == 'interrupted':
                raise OSError('private-disk-failed')
            original(path, value)
        def fail(*args):
            raise interruption
        self.opener.transform = fail
        with patch.object(P, 'write', side_effect=write), self.assertRaises(KeyboardInterrupt) as caught:
            self.run_prepare()
        self.assertIs(caught.exception, interruption)


class ArchiveTests(unittest.TestCase):
    def inspect(self, body, required=('Example.class',)):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.jar'
            path.write_bytes(body)
            return P.inspect_zip(path, required)

    def test_traversal_and_duplicate_entries_are_rejected(self):
        for bad in ('../escaped', '/absolute', 'bad\\windows'):
            with self.subTest(name=bad), self.assertRaisesRegex(P.PrepareError, 'ZIP_PATH_INVALID'):
                self.inspect(jar(('Example.class',), [(bad, b'x')]))
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            body = jar(('Example.class',), [('Example.class', b'x')])
        with self.assertRaisesRegex(P.PrepareError, 'ZIP_PATH_INVALID'):
            self.inspect(body)

    def test_missing_class_and_non_class_are_rejected(self):
        with self.assertRaisesRegex(P.PrepareError, 'ZIP_REQUIRED_CONTENT_MISSING'):
            self.inspect(jar(('Other.class',)))
        with self.assertRaisesRegex(P.PrepareError, 'CLASS_HEADER_INVALID'):
            self.inspect(jar((), [('Example.class', b'wrong')]))

    def test_official_legal_notice_duplicates_preserve_both_occurrences(self):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            body = jar(('Example.class',), [('LICENSE', b'one'), ('LICENSE', b'two'),
                                           ('NOTICE', b'a'), ('NOTICE', b'b')])
        result = self.inspect(body)
        self.assertEqual([x['occurrence'] for x in result['entries'] if x['name'] == 'LICENSE'], [1, 2])
        self.assertTrue(result['crc_verified'])
        # Corrupt the first duplicate's compressed bytes. A name-based CRC loop
        # would reread only the last entry and miss this first occurrence.
        raw = bytearray(body)
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            info = next(x for x in archive.infolist() if x.filename == 'LICENSE')
            offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
            raw[offset] ^= 0x80
        with self.assertRaises((zipfile.BadZipFile, zlib.error)):
            self.inspect(bytes(raw))

    def test_expansion_entry_and_java_limits(self):
        body = jar(('Example.class',))
        with patch.object(P, 'MAX_ENTRIES', 1), self.assertRaisesRegex(P.PrepareError, 'ZIP_ENTRY_LIMIT'):
            self.inspect(body)
        with patch.object(P, 'MAX_UNCOMPRESSED_BYTES', 1), self.assertRaisesRegex(P.PrepareError, 'ZIP_TOTAL_LIMIT'):
            self.inspect(body)
        with self.assertRaisesRegex(P.PrepareError, 'CLASS_REQUIRES_NEWER_JAVA'):
            self.inspect(jar((), [('Example.class', b'\xca\xfe\xba\xbe\x00\x00\x00\x41')]))


if __name__ == '__main__':
    unittest.main()
