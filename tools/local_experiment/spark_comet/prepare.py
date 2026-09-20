"""Prepare one pinned Comet JAR without extracting or executing its contents.

Published SHA-1, a source-pinned SHA-256 and HTTPS establish byte agreement.
The detached signature is retained as provenance, not verified here. ELF and
class headers do not prove CPU instruction, JVM or native S3 compatibility.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import time
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location(
    'spark_comet_iceberg_prepare_helpers', ROOT / 'tools/local_experiment/spark_iceberg/prepare.py')
P = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(P)

STATE = ROOT / 'tmp/local-experiment/spark-comet-prepare'
VERSION = '1.0.0'
SOURCE_COMMIT = '3a7a2c437cc771621b6040a308657573dbc1b9c2'
ARTIFACT = 'comet-spark-spark4.1_2.13'
FILENAME = ARTIFACT + '-' + VERSION + '.jar'
MAVEN = 'https://repo.maven.apache.org/maven2/org/apache/datafusion/'
URL = MAVEN + ARTIFACT + '/' + VERSION + '/' + FILENAME
SIZE = 87971336
SHA1 = 'ffe250a1a9087c7211a7e7b209b11ca9f1e91fbc'
# Observed during the separately controlled initial official download; retain a
# pre-allocation gate if a future version is introduced without its byte pin.
SHA256 = 'fc47c35419c20c38743ad5752ae3ae32e7a167923be628878d9caa22f41d6b67'
REQUIRED_CLASSES = ('org/apache/spark/CometPlugin.class', 'org/apache/comet/NativeBase.class')
# NativeBase.resourceName() in SOURCE_COMMIT constructs this exact classpath name.
NATIVE_ENTRY = 'org/apache/comet/linux/aarch64/libcomet.so'
MAX_ENTRIES = 100000
MAX_ENTRY_BYTES = 256 * 1024**2
MAX_UNCOMPRESSED_BYTES = 1024**3
MAX_INVENTORY_BYTES = 32 * 1024**2
REQUEST_SECONDS = 120
PrepareError = P.PrepareError
require = P.require
private_directory = P.private_directory
write = P.write
official_opener = P.official_opener
NoRedirect = P.NoRedirect
now = P.now


def require_pins():
    require(type(SHA256) is str and re.fullmatch(r'[0-9a-f]{64}', SHA256),
            'COMET_SHA256_PIN_MISSING')
    require(type(SHA1) is str and re.fullmatch(r'[0-9a-f]{40}', SHA1)
            and type(SIZE) is int and SIZE > 0, 'COMET_PINS_INVALID')


def fetch(opener, url, *, method='GET', destination=None):
    """Only this artifact and its two small metadata files, without redirects."""
    require(url in (URL, URL + '.sha1', URL + '.asc')
            and method in ('HEAD', 'GET') and (method != 'HEAD' or url == URL),
            'URL_NOT_ALLOWED')
    limit = SIZE if url == URL else 8192
    deadline = time.monotonic() + REQUEST_SECONDS
    request = urllib.request.Request(url, method=method, headers={'Accept-Encoding': 'identity'})
    with opener.open(request, timeout=20) as response:
        require(response.status == 200 and response.geturl() == url, 'HTTP_RESPONSE_INVALID')
        require(response.headers.get('Content-Encoding', 'identity') == 'identity', 'HTTP_ENCODING_INVALID')
        length = response.headers.get('Content-Length')
        require(type(length) is str and re.fullmatch(r'[0-9]{1,12}', length), 'HTTP_LENGTH_INVALID')
        length = int(length)
        require(0 < length <= limit and (url != URL or length == SIZE), 'HTTP_SIZE_MISMATCH')
        if method == 'HEAD':
            return {'url': url, 'size': length}
        stream, body, count = None, bytearray(), 0
        digests = {key: hashlib.new(key) for key in ('sha1', 'sha256')}
        try:
            if destination is not None:
                fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                stream = os.fdopen(fd, 'wb')
            while True:
                require(time.monotonic() <= deadline, 'DOWNLOAD_TIMEOUT')
                chunk = response.read1(min(1024**2, limit - count + 1))
                if not chunk:
                    break
                count += len(chunk)
                require(count <= length and count <= limit, 'HTTP_BODY_TOO_LARGE')
                for digest in digests.values():
                    digest.update(chunk)
                if stream is None:
                    body.extend(chunk)
                else:
                    stream.write(chunk)
            require(count == length, 'HTTP_BODY_TRUNCATED')
            if stream is not None:
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            if stream is not None:
                stream.close()
        return {'size': count, **{key: value.hexdigest() for key, value in digests.items()},
                'body': bytes(body) if destination is None else None}


def read_private(path, limit, *, content=True):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1
                and 0 < before.st_size <= limit, 'PRIVATE_FILE_INVALID')
        hashes, data, count = {key: hashlib.new(key) for key in ('sha1', 'sha256')}, bytearray(), 0
        while True:
            chunk = stream.read(min(1024**2, limit - count + 1))
            if not chunk:
                break
            count += len(chunk)
            require(count <= limit, 'PRIVATE_FILE_LIMIT')
            for digest in hashes.values():
                digest.update(chunk)
            if content:
                data.extend(chunk)
        after, current = os.fstat(stream.fileno()), Path(path).lstat()
        seal = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns,
                          s.st_mode, s.st_uid, s.st_nlink)
        require(seal(before) == seal(after) == seal(current) and count == before.st_size,
                'PRIVATE_FILE_CHANGED')
    return {'size': count, **{key: value.hexdigest() for key, value in hashes.items()}, 'body': bytes(data)}


def elf_header(header):
    require(len(header) == 64 and header[:4] == b'\x7fELF', 'NATIVE_ELF_HEADER_INVALID')
    require(header[4:7] == b'\x02\x01\x01', 'NATIVE_ELF_FORMAT_INVALID')
    value = {'class': 64, 'byte_order': 'little',
             'type': int.from_bytes(header[16:18], 'little'),
             'machine': int.from_bytes(header[18:20], 'little'),
             'version': int.from_bytes(header[20:24], 'little'),
             'header_size': int.from_bytes(header[52:54], 'little')}
    require(value['type'] == 3 and value['machine'] == 183
            and value['version'] == 1 and value['header_size'] == 64,
            'NATIVE_ELF_ARCHITECTURE_INVALID')
    return value


def inspect_zip(path):
    """Bound total expansion and CRC-check each member, with no extraction."""
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        require(0 < len(entries) <= MAX_ENTRIES, 'ZIP_ENTRY_LIMIT')
        names, occurrences, total, inventory = set(), {}, 0, []
        for item in entries:
            name = item.filename
            occurrences[name] = occurrences.get(name, 0) + 1
            require(name not in names or (name in ('LICENSE', 'NOTICE') and occurrences[name] == 2),
                    'ZIP_PATH_INVALID')
            require(name and not name.startswith('/') and '\\' not in name and '\x00' not in name
                    and '..' not in PurePosixPath(name).parts, 'ZIP_PATH_INVALID')
            require(not stat.S_ISLNK(item.external_attr >> 16) and not item.flag_bits & 1, 'ZIP_TYPE_INVALID')
            require(item.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED), 'ZIP_COMPRESSION_INVALID')
            require(0 <= item.file_size <= MAX_ENTRY_BYTES and item.compress_size >= 0, 'ZIP_SIZE_INVALID')
            total += item.file_size
            require(total <= MAX_UNCOMPRESSED_BYTES, 'ZIP_TOTAL_LIMIT')
            names.add(name)
            inventory.append({'name': name, 'occurrence': occurrences[name], 'bytes': item.file_size,
                              'compressed_bytes': item.compress_size, 'crc32': format(item.CRC, '08x'),
                              'directory': item.is_dir()})
        require('META-INF/MANIFEST.MF' in names and NATIVE_ENTRY in names
                and all(name in names for name in REQUIRED_CLASSES), 'ZIP_REQUIRED_CONTENT_MISSING')
        native_hash = hashlib.sha256()
        for item in entries:
            count = 0
            with archive.open(item) as stream:
                while True:
                    chunk = stream.read(1024**2)
                    if not chunk:
                        break
                    count += len(chunk)
                    require(count <= item.file_size, 'ZIP_SIZE_INVALID')
                    if item.filename == NATIVE_ENTRY:
                        native_hash.update(chunk)
            require(count == item.file_size, 'ZIP_SIZE_INVALID')
        classes = []
        for name in REQUIRED_CLASSES:
            with archive.open(name) as stream:
                header = stream.read(8)
            require(len(header) == 8 and header[:4] == b'\xca\xfe\xba\xbe', 'CLASS_HEADER_INVALID')
            major = int.from_bytes(header[6:8], 'big')
            require(45 <= major <= 61, 'CLASS_REQUIRES_NEWER_JAVA')
            classes.append({'name': name, 'major_version': major})
        with archive.open(NATIVE_ENTRY) as stream:
            native = elf_header(stream.read(64))
        return {'entry_count': len(entries), 'uncompressed_bytes': total, 'crc_verified': True,
                'extracted': False, 'required_classes': classes, 'native_entry': NATIVE_ENTRY,
                'native_elf': native, 'native_sha256': native_hash.hexdigest(),
                'manifest_sha256': hashlib.sha256(archive.read('META-INF/MANIFEST.MF')).hexdigest(),
                'entries': inventory}


def _pins_match(value):
    return type(value) is dict and type(value.get('size')) is int and value['size'] == SIZE \
        and value.get('sha1') == SHA1 and value.get('sha256') == SHA256


def prepare(output=None, *, opener=None):
    require_pins()
    output = Path(output) if output is not None else STATE / uuid.uuid4().hex
    require(output.parent == STATE and re.fullmatch(r'[0-9a-f]{32}', output.name), 'OUTPUT_SCOPE_INVALID')
    require(not output.exists() and not output.is_symlink(), 'OUTPUT_ALREADY_USED')
    private_directory(STATE, create=True)
    private_directory(output, create=True)
    receipt = {'schema_version': 1, 'result': 'failed', 'started_at': now(), 'version': VERSION,
               'token': output.name, 'pending': None, 'artifact': None,
               'jvm_classpath_tested': False, 'runtime_started': False, 'signature_verified': False,
               'verification_boundary': 'pinned SHA-256 and published SHA-1 byte agreement; '
                                        'detached signature saved but not verified; native code never loaded'}
    write(output / 'summary.json', receipt)
    try:
        available = shutil.disk_usage(output).free
        require(available >= SIZE + 128 * 1024**2, 'INSUFFICIENT_DISK_HEADROOM')
        receipt['disk_available_before_bytes'] = available
        write(output / 'sources.json', {'version': VERSION, 'source_commit': SOURCE_COMMIT,
              'source_url': 'https://github.com/apache/datafusion-comet/tree/' + SOURCE_COMMIT,
              'release_url': 'https://datafusion.apache.org/comet/user-guide/1.0/installation.html',
              'spark': '4.1.3', 'scala': '2.13', 'url': URL, 'size': SIZE,
              'sha1': SHA1, 'sha256': SHA256, 'required_classes': REQUIRED_CLASSES,
              'native_entry': NATIVE_ENTRY, 'native_cpu_target': 'neoverse-n1'})
        opener = official_opener() if opener is None else opener
        receipt['pending'] = 'metadata'
        write(output / 'summary.json', receipt)
        fetch(opener, URL, method='HEAD')
        checksum = fetch(opener, URL + '.sha1', destination=output / (FILENAME + '.sha1'))
        P.verify_checksum(read_private(output / (FILENAME + '.sha1'), 8192)['body'], SHA1, 'sha1')
        signature = fetch(opener, URL + '.asc', destination=output / (FILENAME + '.asc'))
        receipt['pending'] = 'download'
        write(output / 'summary.json', receipt)
        partial = output / (FILENAME + '.part')
        downloaded = fetch(opener, URL, destination=partial)
        require(_pins_match(downloaded), 'JAR_DIGEST_MISMATCH')
        receipt['pending'] = 'zip-validation'
        write(output / 'summary.json', receipt)
        inventory = inspect_zip(partial)
        write(output / (FILENAME + '.zip.json'), inventory)
        inventory_hash = read_private(output / (FILENAME + '.zip.json'), MAX_INVENTORY_BYTES, content=False)['sha256']
        os.rename(partial, output / FILENAME)
        receipt['artifact'] = {'artifact': ARTIFACT, 'file': FILENAME, 'url': URL,
            'size': downloaded['size'], 'sha256': downloaded['sha256'], 'sha1': downloaded['sha1'],
            'checksum_url': URL + '.sha1', 'published_checksum_matched': True,
            'checksum_sha256': checksum['sha256'], 'signature_url': URL + '.asc',
            'signature_sha256': signature['sha256'], 'signature_verified': False,
            'zip_crc_verified': True, 'zip_inventory_sha256': inventory_hash,
            'zip_entry_count': inventory['entry_count'], 'zip_uncompressed_bytes': inventory['uncompressed_bytes'],
            'required_classes': inventory['required_classes'], 'native_entry': NATIVE_ENTRY,
            'native_elf': inventory['native_elf'], 'native_sha256': inventory['native_sha256']}
        receipt.update(result='passed', pending=None, observed_at=now(), total_jar_bytes=SIZE,
                       disk_available_after_bytes=shutil.disk_usage(output).free)
    except BaseException as error:
        receipt['result'] = 'failed' if isinstance(error, Exception) else 'interrupted'
        receipt['failure_code'] = str(error) if isinstance(error, PrepareError) else 'PREPARATION_FAILED'
        receipt['observed_at'] = now()
        try:
            write(output / 'summary.json', receipt)
        except BaseException:
            if not isinstance(error, Exception):
                raise error
            raise
        raise
    write(output / 'summary.json', receipt)
    return receipt, output


def verified_artifact(token):
    """Recheck private, fixed-name inputs and content pins before a local build."""
    require_pins()
    require(type(token) is str and re.fullmatch(r'[0-9a-f]{32}', token), 'TOKEN_INVALID')
    private_directory(STATE)
    output = STATE / token
    private_directory(output)
    try:
        receipt = json.loads(read_private(output / 'summary.json', 128 * 1024)['body'],
                             object_pairs_hook=P.unique_object)
    except (ValueError, UnicodeError):
        raise PrepareError('RECEIPT_INVALID') from None
    require(type(receipt) is dict and type(receipt.get('schema_version')) is int
            and receipt['schema_version'] == 1 and receipt.get('token') == token
            and receipt.get('version') == VERSION and receipt.get('result') == 'passed'
            and receipt.get('pending') is None and receipt.get('runtime_started') is False
            and receipt.get('jvm_classpath_tested') is False and receipt.get('signature_verified') is False,
            'RECEIPT_INVALID')
    row = receipt.get('artifact')
    require(_pins_match(row) and row.get('artifact') == ARTIFACT and row.get('file') == FILENAME
            and row.get('url') == URL and row.get('published_checksum_matched') is True
            and row.get('signature_verified') is False and row.get('zip_crc_verified') is True,
            'RECEIPT_ARTIFACT_INVALID')
    checksum = read_private(output / (FILENAME + '.sha1'), 8192)
    P.verify_checksum(checksum['body'], SHA1, 'sha1')
    require(checksum['sha256'] == row.get('checksum_sha256'), 'CHECKSUM_BYTES_CHANGED')
    signature = read_private(output / (FILENAME + '.asc'), 8192)
    require(signature['sha256'] == row.get('signature_sha256'), 'SIGNATURE_BYTES_CHANGED')
    require(_pins_match(read_private(output / FILENAME, SIZE, content=False)), 'JAR_DIGEST_MISMATCH')
    inventory = read_private(output / (FILENAME + '.zip.json'), MAX_INVENTORY_BYTES)
    require(inventory['sha256'] == row.get('zip_inventory_sha256'), 'ZIP_INVENTORY_CHANGED')
    inspected = inspect_zip(output / FILENAME)
    for key in ('required_classes', 'native_entry', 'native_elf', 'native_sha256'):
        require(row.get(key) == inspected[key], 'RECEIPT_NATIVE_INVALID')
    require(row.get('zip_entry_count') == inspected['entry_count']
            and row.get('zip_uncompressed_bytes') == inspected['uncompressed_bytes'], 'RECEIPT_ZIP_INVALID')
    return {key: row[key] for key in ('artifact', 'file', 'size', 'sha256', 'sha1',
                                      'native_entry', 'native_elf')} | {'path': str(output / FILENAME)}


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    def interrupt(signum, frame):
        raise KeyboardInterrupt()
    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        receipt, output = prepare()
        print(json.dumps({'result': receipt['result'], 'evidence': str(output.relative_to(ROOT)),
                          'total_jar_bytes': receipt['total_jar_bytes'],
                          'runtime_started': False, 'signature_verified': False}))
        return 0
    except BaseException as error:
        print(json.dumps({'result': 'failed', 'runtime_started': False,
                          'failure_code': str(error) if isinstance(error, PrepareError) else 'PREPARATION_FAILED'}))
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    raise SystemExit(main())
