"""Download two pinned official Iceberg JARs without loading or extracting them.

Every invocation uses a fresh private directory; interrupted/failed downloads
remain there and are never reused as verified inputs. TLS delivery, published
checksums and local hash pins prove byte agreement, not independent authorship.
Detached signatures are preserved but not verified. ZIP/class-header checks are
not a JVM classpath or Spark/Polaris/S3 compatibility test.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import sys
import time
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[3]
STATE = ROOT / 'tmp/local-experiment/spark-iceberg-prepare'
VERSION = '1.11.0'
RELEASE_SOURCE = 'https://iceberg.apache.org/releases/'
MAVEN = 'https://repo.maven.apache.org/maven2/org/apache/iceberg/'
ARTIFACTS = (
    {'artifact': 'iceberg-spark-runtime-4.1_2.13', 'size': 47959591,
     'sha256': 'd6ea6c5d099288daeb7d5a92061bd3d7d8f296492632b42378e5f2f0e3066242',
     'sha512': 'f4620bb2d20777146a769a8a939a5bed658e1d5708b6a5155e1f6e831c0bfd29bd1f82618dc59bc0f30399ffcb03add3e66656286692dc6c2be94e4e1f4e7479',
     'required_classes': ('org/apache/iceberg/spark/SparkCatalog.class',
                          'org/apache/iceberg/spark/extensions/IcebergSparkSessionExtensions.class',
                          'org/apache/iceberg/rest/RESTCatalog.class',
                          'org/apache/iceberg/aws/s3/S3FileIO.class')},
    {'artifact': 'iceberg-aws-bundle', 'size': 63613165,
     'sha256': '38f01da7e96850cdd05e6616d758b77b43314b712a8808e3f9a824d56976162f',
     'sha512': 'bb50f7dc5f36a001efecf15e4d03eff955466f06557261fbacdd7fe0d88113c82e0b9b2af96cd554cef4a1d4c2348fc950270bc3f51cf6bf1e4526f46ce336a0',
     'required_classes': ('software/amazon/awssdk/services/s3/S3Client.class',
                          'software/amazon/awssdk/auth/credentials/DefaultCredentialsProvider.class')},
)
MAX_ENTRIES = 100000
MAX_UNCOMPRESSED_BYTES = 1024**3
MAX_ENTRY_BYTES = 32 * 1024**2
MIN_FREE_BYTES = sum(item['size'] for item in ARTIFACTS) + 128 * 1024**2
REQUEST_SECONDS = 120


class PrepareError(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise PrepareError(code)


def now():
    return datetime.now(timezone.utc).isoformat()


def filename(artifact):
    return artifact['artifact'] + '-' + VERSION + '.jar'


def url_for(artifact, suffix=''):
    return MAVEN + artifact['artifact'] + '/' + VERSION + '/' + filename(artifact) + suffix


def private_directory(path, *, create=False):
    path = Path(path).absolute()
    # Existing ancestors can be public workspace directories, but never links or
    # another user's directories. Do not chmod pre-existing workspace state.
    for item in reversed((path, *path.parents)):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            require(stat.S_ISDIR(info.st_mode) and not item.is_symlink(), 'DIRECTORY_INVALID')
            require(info.st_uid in (0, os.getuid()), 'DIRECTORY_OWNER_INVALID')
        elif create:
            item.mkdir(mode=0o700)
        else:
            raise PrepareError('DIRECTORY_MISSING')
    info = path.lstat()
    require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700, 'DIRECTORY_NOT_PRIVATE')


def write(path, value):
    """Atomic receipts within the fresh directory; existing symlinks rejected."""
    require(not path.is_symlink(), 'FILE_SYMLINK')
    raw = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PrepareError('REDIRECT_REJECTED')


def official_opener():
    # Avoid ambient proxy credentials and redirects; default TLS validation stays on.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def fetch(opener, url, *, method='GET', limit=8192, exact_size=None, destination=None):
    allowed = {url_for(item, suffix) for item in ARTIFACTS for suffix in ('', '.sha512', '.sha256', '.asc')}
    require(url in allowed and method in ('HEAD', 'GET'), 'URL_NOT_ALLOWED')
    deadline = time.monotonic() + REQUEST_SECONDS
    request = urllib.request.Request(url, method=method, headers={'Accept-Encoding': 'identity'})
    with opener.open(request, timeout=20) as response:
        require(response.status == 200 and response.geturl() == url, 'HTTP_RESPONSE_INVALID')
        require(response.headers.get('Content-Encoding', 'identity') == 'identity', 'HTTP_ENCODING_INVALID')
        length = response.headers.get('Content-Length')
        require(type(length) is str and re.fullmatch(r'[0-9]{1,12}', length), 'HTTP_LENGTH_INVALID')
        length = int(length)
        require(0 < length <= limit and (exact_size is None or length == exact_size), 'HTTP_SIZE_MISMATCH')
        if method == 'HEAD':
            return {'size': length, 'url': url}
        stream = None
        body = bytearray()
        digests = {key: hashlib.new(key) for key in ('sha256', 'sha512')}
        count = 0
        try:
            if destination is not None:
                fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                stream = os.fdopen(fd, 'wb')
            while True:
                require(time.monotonic() <= deadline, 'DOWNLOAD_TIMEOUT')
                # HTTPResponse.read(n) may wait for n bytes while a slow peer
                # keeps resetting its socket timeout. read1 returns one receive
                # buffer so the absolute deadline is rechecked between receives.
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


def verify_checksum(raw, expected, algorithm):
    try:
        value = raw.decode('ascii').strip()
    except UnicodeError:
        raise PrepareError('CHECKSUM_INVALID') from None
    require(re.fullmatch('[0-9a-f]{' + str(len(expected)) + '}', value) and value == expected,
            'PUBLISHED_' + algorithm.upper() + '_MISMATCH')


def read_private(path, limit, *, content=True):
    """Bounded reads from our fresh evidence only, including live hash recheck."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1
                and 0 < before.st_size <= limit, 'PRIVATE_FILE_INVALID')
        digest256, digest512, data, count = hashlib.sha256(), hashlib.sha512(), bytearray(), 0
        while True:
            chunk = stream.read(min(1024**2, limit - count + 1))
            if not chunk:
                break
            count += len(chunk)
            require(count <= limit, 'PRIVATE_FILE_LIMIT')
            digest256.update(chunk)
            digest512.update(chunk)
            if content:
                data.extend(chunk)
        after = os.fstat(stream.fileno())
        require((before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                == (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                and count == before.st_size, 'PRIVATE_FILE_CHANGED')
    return {'size': count, 'sha256': digest256.hexdigest(), 'sha512': digest512.hexdigest(), 'body': bytes(data)}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'RECEIPT_DUPLICATE_KEY')
        result[key] = value
    return result


def verified_artifacts(token):
    """Read-only build input: a fixed token resolves only the two pinned JARs.

Paths in the receipt are never followed. Return absolute paths only after
checking modes, ownership and both independent-of-receipt content pins.
"""
    require(type(token) is str and re.fullmatch(r'[0-9a-f]{32}', token), 'TOKEN_INVALID')
    private_directory(STATE)
    output = STATE / token
    private_directory(output)
    raw = read_private(output / 'summary.json', 128 * 1024)['body']
    try:
        receipt = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object)
    except (ValueError, UnicodeError):
        raise PrepareError('RECEIPT_INVALID') from None
    require(type(receipt) is dict and type(receipt.get('schema_version')) is int
            and receipt['schema_version'] == 1 and receipt.get('result') == 'passed'
            and receipt.get('version') == VERSION and receipt.get('pending') is None
            and receipt.get('runtime_started') is False and receipt.get('jvm_classpath_tested') is False
            and receipt.get('signature_verified') is False, 'RECEIPT_INVALID')
    rows = receipt.get('artifacts')
    require(type(rows) is list and len(rows) == len(ARTIFACTS), 'RECEIPT_ARTIFACTS_INVALID')
    result = []
    for artifact, observed in zip(ARTIFACTS, rows):
        name = filename(artifact)
        require(type(observed) is dict and observed.get('artifact') == artifact['artifact']
                and observed.get('file') == name and observed.get('url') == url_for(artifact)
                and type(observed.get('size')) is int
                and all(observed.get(key) == artifact[key] for key in ('size', 'sha256', 'sha512'))
                and observed.get('published_checksums_matched') is True
                and observed.get('signature_verified') is False
                and observed.get('zip_crc_verified') is True, 'RECEIPT_ARTIFACTS_INVALID')
        for algorithm in ('sha256', 'sha512'):
            verify_checksum(read_private(output / (name + '.' + algorithm), 8192)['body'], artifact[algorithm], algorithm)
        signature = read_private(output / (name + '.asc'), 8192)
        require(signature['sha256'] == observed.get('signature_sha256'), 'SIGNATURE_BYTES_CHANGED')
        actual = read_private(output / name, artifact['size'], content=False)
        require(all(actual[key] == artifact[key] for key in ('size', 'sha256', 'sha512')), 'JAR_DIGEST_MISMATCH')
        result.append({**{key: artifact[key] for key in ('artifact', 'size', 'sha256', 'sha512')},
                       'path': str(output / name)})
    return result


def inspect_zip(path, required_classes):
    """Inspect bounded metadata/CRC and selected class headers, never extract."""
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        require(0 < len(entries) <= MAX_ENTRIES, 'ZIP_ENTRY_LIMIT')
        names = set()
        occurrences = {}
        total = 0
        inventory = []
        for item in entries:
            name = item.filename
            occurrences[name] = occurrences.get(name, 0) + 1
            # The official Spark fat JAR has two LICENSE and two NOTICE files.
            # Preserve both in the inventory; executable/resource ambiguity is
            # still rejected, and CRC-check each ZipInfo rather than name lookup.
            require(name not in names or (name in ('LICENSE', 'NOTICE') and occurrences[name] == 2),
                    'ZIP_PATH_INVALID')
            require(name and not name.startswith('/') and '\\' not in name
                    and '\x00' not in name and '..' not in PurePosixPath(name).parts, 'ZIP_PATH_INVALID')
            require(not stat.S_ISLNK(item.external_attr >> 16) and not item.flag_bits & 1, 'ZIP_TYPE_INVALID')
            require(item.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED), 'ZIP_COMPRESSION_INVALID')
            require(0 <= item.file_size <= MAX_ENTRY_BYTES and item.compress_size >= 0, 'ZIP_SIZE_INVALID')
            total += item.file_size
            require(total <= MAX_UNCOMPRESSED_BYTES, 'ZIP_TOTAL_LIMIT')
            names.add(name)
            inventory.append({'name': name, 'occurrence': occurrences[name], 'bytes': item.file_size, 'compressed_bytes': item.compress_size,
                              'crc32': format(item.CRC, '08x'), 'directory': item.is_dir()})
        require('META-INF/MANIFEST.MF' in names and all(name in names for name in required_classes),
                'ZIP_REQUIRED_CONTENT_MISSING')
        for item in entries:
            with archive.open(item) as stream:
                while stream.read(1024**2):
                    pass  # ZipFile verifies this individual member's CRC at EOF.
        classes = []
        for name in required_classes:
            with archive.open(name) as stream:
                header = stream.read(8)
            require(len(header) == 8 and header[:4] == b'\xca\xfe\xba\xbe', 'CLASS_HEADER_INVALID')
            major = int.from_bytes(header[6:8], 'big')
            require(45 <= major <= 61, 'CLASS_REQUIRES_NEWER_JAVA')
            classes.append({'name': name, 'major_version': major})
        manifest = archive.read('META-INF/MANIFEST.MF')
        return {'entry_count': len(entries), 'uncompressed_bytes': total,
                'crc_verified': True, 'extracted': False, 'required_classes': classes,
                'manifest_sha256': hashlib.sha256(manifest).hexdigest(), 'entries': inventory}


def prepare(output=None, *, opener=None):
    private_directory(STATE, create=True)
    output = Path(output) if output is not None else STATE / uuid.uuid4().hex
    require(output.parent == STATE and re.fullmatch(r'[0-9a-f]{32}', output.name), 'OUTPUT_SCOPE_INVALID')
    require(not output.exists() and not output.is_symlink(), 'OUTPUT_ALREADY_USED')
    private_directory(output, create=True)
    receipt = {'schema_version': 1, 'result': 'failed', 'started_at': now(), 'version': VERSION,
               'pending': None, 'artifacts': [], 'jvm_classpath_tested': False,
               'runtime_started': False, 'signature_verified': False,
               'verification_boundary': 'pinned bytes and TLS-served published checksums; detached signatures saved but not verified'}
    write(output / 'summary.json', receipt)
    try:
        available = shutil.disk_usage(output).free
        require(available >= MIN_FREE_BYTES, 'INSUFFICIENT_DISK_HEADROOM')
        receipt['disk_available_before_bytes'] = available
        write(output / 'sources.json', {'release_page': RELEASE_SOURCE, 'maven_base': MAVEN, 'spark': '4.1.3', 'scala': '2.13',
                                       'iceberg': VERSION, 'artifacts': ARTIFACTS})
        opener = official_opener() if opener is None else opener
        for artifact in ARTIFACTS:
            name = filename(artifact)
            receipt['pending'] = {'artifact': artifact['artifact'], 'stage': 'metadata'}
            write(output / 'summary.json', receipt)
            head = fetch(opener, url_for(artifact), method='HEAD', limit=artifact['size'], exact_size=artifact['size'])
            for algorithm in ('sha512', 'sha256'):
                result = fetch(opener, url_for(artifact, '.' + algorithm))
                verify_checksum(result['body'], artifact[algorithm], algorithm)
                path = output / (name + '.' + algorithm)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(result['body'])
            signature = fetch(opener, url_for(artifact, '.asc'), destination=output / (name + '.asc'))
            receipt['pending']['stage'] = 'download'
            write(output / 'summary.json', receipt)
            partial = output / (name + '.part')
            downloaded = fetch(opener, url_for(artifact), limit=artifact['size'], exact_size=artifact['size'], destination=partial)
            require(all(downloaded[key] == artifact[key] for key in ('size', 'sha256', 'sha512')), 'JAR_DIGEST_MISMATCH')
            receipt['pending']['stage'] = 'zip-validation'
            write(output / 'summary.json', receipt)
            inventory = inspect_zip(partial, artifact['required_classes'])
            write(output / (name + '.zip.json'), inventory)
            os.rename(partial, output / name)
            receipt['artifacts'].append({'artifact': artifact['artifact'], 'file': name, 'url': head['url'],
                'size': downloaded['size'], 'sha256': downloaded['sha256'], 'sha512': downloaded['sha512'],
                'checksum_urls': {key: url_for(artifact, '.' + key) for key in ('sha256', 'sha512')},
                'published_checksums_matched': True, 'signature_url': url_for(artifact, '.asc'),
                'signature_sha256': signature['sha256'], 'signature_verified': False,
                'zip_entry_count': inventory['entry_count'], 'zip_uncompressed_bytes': inventory['uncompressed_bytes'],
                'zip_crc_verified': True, 'required_classes': inventory['required_classes']})
            receipt['pending'] = None
            write(output / 'summary.json', receipt)
        receipt.update(result='passed', observed_at=now(), total_jar_bytes=sum(x['size'] for x in receipt['artifacts']),
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


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    def interrupt(signum, frame):
        raise KeyboardInterrupt()
    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        value, output = prepare()
        print(json.dumps({'result': value['result'], 'evidence': str(output.relative_to(ROOT)),
                          'jar_count': len(value['artifacts']), 'total_jar_bytes': value['total_jar_bytes'],
                          'runtime_started': False, 'signature_verified': False}))
        return 0
    except BaseException:
        print(json.dumps({'result': 'failed', 'runtime_started': False}))
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    raise SystemExit(main())
