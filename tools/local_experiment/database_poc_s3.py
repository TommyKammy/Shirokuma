"""Bounded database PoC S3 identities and explicit-credential offline-safe CLI."""

import argparse
import base64
import copy
import json
import secrets
import sys
from urllib.parse import urlsplit
import uuid

try:
    from tools.local_experiment import database_poc as poc
except ModuleNotFoundError:
    import database_poc as poc

BUCKET = 'shirokuma-lakehouse'
SERVER_SECRET = 'seaweedfs-s3-credentials'
DATABASES = poc.DATABASES


def validate_credentials(value):
    if (not isinstance(value, dict) or set(value) != set(DATABASES)
            or any(not isinstance(value[d], dict) or set(value[d]) != {'accessKey', 'secretKey'}
                   or any(not isinstance(v, str) or not v.strip() for v in value[d].values())
                   for d in DATABASES)):
        raise poc.Failure('invalid S3 principal credentials')
    if value['sales']['accessKey'] == value['finance']['accessKey']:
        raise poc.Failure('S3 principal access keys must be distinct')
    return value


def check_state():
    if not poc.STATE.is_dir() or poc.STATE.stat().st_mode & 0o777 != 0o700:
        raise poc.Failure('existing state directory must be 0700')


def load_credentials():
    check_state()
    path = poc.STATE / 's3-principals.json'
    try:
        if path.stat().st_mode & 0o777 != 0o600:
            raise poc.Failure('S3 principal file must be 0600')
        return validate_credentials(json.loads(path.read_text()))
    except (OSError, ValueError, TypeError):
        raise poc.Failure('S3 principal credentials unavailable or invalid') from None


def expected_identities(credentials):
    validate_credentials(credentials)
    return [{'name': 'database-poc-' + db + '-s3', 'credentials': [credentials[db]],
             'actions': [action + ':' + BUCKET + '/database-poc/' + db + '/*'
                         for action in ('Read', 'Write', 'Tagging', 'List')]}
            for db in DATABASES]


def update_secret(secret, credentials, revoke=False):
    """Return a complete replacement, retaining resourceVersion and unrelated content."""
    result = copy.deepcopy(secret)
    try:
        config = json.loads(base64.b64decode(result['data']['s3.json'], validate=True))
        identities = config['identities']
        if not isinstance(identities, list):
            raise ValueError()
        expected = expected_identities(credentials)
        names = {e['name']: e for e in expected}
        keys = {c['accessKey'] for e in expected for c in e['credentials']}
        seen = set()
        for entry in identities:
            name = entry['name']
            if name in names:
                if entry != names[name] or name in seen:
                    raise poc.Failure('S3 identity ownership collision')
                seen.add(name)
            elif any(c.get('accessKey') in keys for c in entry.get('credentials', [])):
                raise poc.Failure('S3 access key collision')
        updated = ([e for e in identities if e['name'] not in names] if revoke else
                   identities + [e for e in expected if e['name'] not in seen])
        if updated == identities:
            return result, False
        config['identities'] = updated
        result['data']['s3.json'] = base64.b64encode(json.dumps(config).encode()).decode()
        return result, True
    except (KeyError, TypeError, ValueError):
        raise poc.Failure('invalid server S3 configuration') from None


def install_or_revoke(revoke=False):
    check_state()
    path = poc.STATE / 's3-principals.json'
    if not revoke and not path.exists():
        credentials = {d: {'accessKey': secrets.token_hex(16), 'secretKey': secrets.token_urlsafe(32)}
                       for d in DATABASES}
        validate_credentials(credentials)
        poc.save('s3-principals.json', credentials)
    credentials = load_credentials()
    secret = json.loads(poc.run(['kubectl', '--context', poc.CONTEXT, '-n', 'shirokuma-storage',
                                 'get', 'secret', SERVER_SECRET, '-o', 'json']))
    updated, changed = update_secret(secret, credentials, revoke)
    if changed:
        poc.run(['kubectl', '--context', poc.CONTEXT, '-n', 'shirokuma-storage',
                 'replace', '-f', '-'], json.dumps(updated).encode())
    print(('changed' if changed else 'unchanged') + '; parent restart needed')


def endpoint(value):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ('http', 'https') or parsed.hostname not in ('localhost', '127.0.0.1', '::1')
                or parsed.username is not None or parsed.password is not None
                or parsed.path or parsed.query or parsed.fragment or not parsed.port
                or '?' in value or '#' in value):
            raise ValueError()
    except ValueError:
        raise argparse.ArgumentTypeError('endpoint requires localhost and explicit port only') from None
    return value


def classify(result=None, error=None, deny=False, head=False):
    """Only the documented denial is evidence; never emit exception text."""
    response = getattr(error, 'response', None) if error is not None else result
    if not isinstance(response, dict):
        return 'UNKNOWN', None, 'TransportFailure' if error else 'InvalidResponse'
    status = response.get('ResponseMetadata', {}).get('HTTPStatusCode')
    code = response.get('Error', {}).get('Code', '')
    safe_codes = {'AccessDenied', 'NoSuchKey', 'NoSuchUpload', 'InvalidAccessKeyId',
                  'SignatureDoesNotMatch', 'InternalError', 'SlowDown', '404', '403'}
    safe_code = code if code in safe_codes else ('ServiceError' if code else '-')
    if deny:
        passed = status == 403 and (code == 'AccessDenied' or (head and code in ('', '403')))
    else:
        passed = error is None and isinstance(status, int) and 200 <= status < 300
    return ('PASS' if passed else 'FAIL'), status, safe_code


def mixed_delete_ok(response, own, foreign):
    errors = response.get('Errors', [])
    return (len(errors) == 1 and errors[0].get('Key') == foreign
            and errors[0].get('Code') == 'AccessDenied'
            and not any(e.get('Key') == foreign for e in response.get('Deleted', []))
            and any(e.get('Key') == own for e in response.get('Deleted', [])))


def sdk_clients(credentials, origin, region):
    # Called only by verify. Install/revoke/import need no SDK.
    import boto3
    from botocore.config import Config
    config = Config(signature_version='s3v4', s3={'addressing_style': 'path'},
                    retries={'max_attempts': 0}, connect_timeout=3, read_timeout=5,
                    request_checksum_calculation='when_required',
                    response_checksum_validation='when_required')
    return {db: boto3.client('s3', endpoint_url=origin, region_name=region,
                             aws_access_key_id=credentials[db]['accessKey'],
                             aws_secret_access_key=credentials[db]['secretKey'],
                             aws_session_token=None, config=config) for db in DATABASES}


def verify(origin):
    endpoint(origin)
    credentials = load_credentials()
    # Ask kubectl for just this field; do not retrieve other application credentials.
    region = base64.b64decode(poc.kube('get', 'secret', 'seaweedfs-s3-application-credentials',
                                      '-o', 'jsonpath={.data.S3_REGION}'), validate=True).decode()
    if not region.strip():
        raise poc.Failure('S3 region unavailable')
    clients = sdk_clients(credentials, origin, region)
    run_id = uuid.uuid4().hex
    report = {'runId': run_id, 'cases': []}
    roots = {db: 'database-poc/' + db + '/_s3_probe/' + run_id + '/' for db in DATABASES}
    markers = {db: roots[db] + 'marker' for db in DATABASES}
    payloads = {db: ('probe-' + db + '-' + run_id).encode() for db in DATABASES}
    tags = {'TagSet': [{'Key': 'probe', 'Value': run_id}]}
    objects = set()
    uploads = set()

    def record(name, status, http=None, code='-'):
        report['cases'].append({'case': name, 'status': status, 'HTTP': http, 'errorCode': code})
        poc.save('s3-verification.json', report)
        print(f'{name} {status} HTTP={http if http is not None else "-"} errorCode={code}')

    def check(name, call, deny=False, head=False, valid=None):
        try:
            response = call()
        except Exception as error:
            record(name, *classify(error=error, deny=deny, head=head))
            return None
        status, http, code = classify(response, deny=deny, head=head)
        if status == 'PASS' and valid is not None:
            try:
                if not valid(response):
                    status, code = 'FAIL', 'StateMismatch'
            except Exception:
                status, code = 'UNKNOWN', 'InvalidResponse'
        record(name, status, http, code)
        return response if status == 'PASS' else None

    def put(db, key, body):
        # Track only this run's explicitly attempted creations, including unknown outcomes.
        objects.add((db, key))
        return clients[db].put_object(Bucket=BUCKET, Key=key, Body=body)

    def create_upload(db, key):
        response = clients[db].create_multipart_upload(Bucket=BUCKET, Key=key)
        uploads.add((db, key, response['UploadId']))
        return response

    def foreign_create(client, owner_db, key):
        response = client.create_multipart_upload(Bucket=BUCKET, Key=key)
        uploads.add((owner_db, key, response['UploadId']))
        return response

    def state(db):
        value = clients[db].get_object(Bucket=BUCKET, Key=markers[db])
        body = value['Body'].read()
        value['Body'].close()
        actual_tags = clients[db].get_object_tagging(Bucket=BUCKET, Key=markers[db])
        if body != payloads[db] or actual_tags['TagSet'] != tags['TagSet']:
            raise poc.Failure('marker state mismatch')
        return value

    def absent(db, key):
        try:
            clients[db].head_object(Bucket=BUCKET, Key=key)
        except Exception as error:
            response = getattr(error, 'response', {})
            if (response.get('ResponseMetadata', {}).get('HTTPStatusCode') == 404
                    and response.get('Error', {}).get('Code') in ('404', 'NoSuchKey', 'NotFound')):
                return {'ResponseMetadata': {'HTTPStatusCode': 200}}
            raise
        raise poc.Failure('destination unexpectedly exists')

    def parts(db, key, upload_id):
        return clients[db].list_parts(Bucket=BUCKET, Key=key, UploadId=upload_id)

    def listed(response, root):
        return (not response.get('IsTruncated', False)
                and all(e['Key'].startswith(root) for e in response.get('Contents', []))
                and not response.get('CommonPrefixes'))

    ready = {}
    poc.save('s3-verification.json', report)
    try:
        # Establish both owners before any cross-principal negative probe.
        for db in DATABASES:
            c, key = clients[db], markers[db]
            start = len(report['cases'])
            check(db + '.marker.put', lambda: put(db, key, payloads[db]))
            check(db + '.marker.tags.put', lambda: c.put_object_tagging(Bucket=BUCKET, Key=key, Tagging=tags))
            check(db + '.marker.get.tags', lambda: state(db))
            check(db + '.marker.head', lambda: c.head_object(Bucket=BUCKET, Key=key),
                  valid=lambda r: r.get('ContentLength') == len(payloads[db]))
            check(db + '.own.list', lambda: c.list_objects_v2(Bucket=BUCKET, Prefix='database-poc/' + db + '/'),
                  valid=lambda r: listed(r, 'database-poc/' + db + '/')
                  and any(e['Key'] == key for e in r.get('Contents', [])))
            ready[db] = all(r['status'] == 'PASS' for r in report['cases'][start:])

        for db in DATABASES:
            c = clients[db]
            foreign = next(d for d in DATABASES if d != db)
            other = markers[foreign]
            root, marker = roots[db], markers[db]
            if not ready[db] or not ready[foreign]:
                record(db + '.negative.prerequisites', 'UNKNOWN', code='PrerequisiteFailure')
                continue
            for suffix, call, head in [
                ('get', lambda: c.get_object(Bucket=BUCKET, Key=other), False),
                ('head', lambda: c.head_object(Bucket=BUCKET, Key=other), True),
                ('overwrite', lambda: c.put_object(Bucket=BUCKET, Key=other, Body=b'forbidden'), False),
                ('delete', lambda: c.delete_object(Bucket=BUCKET, Key=other), False),
                ('list', lambda: c.list_objects_v2(Bucket=BUCKET, Prefix='database-poc/' + foreign + '/'), False),
                ('tags.put', lambda: c.put_object_tagging(Bucket=BUCKET, Key=other, Tagging={'TagSet': []}), False),
                ('tags.get', lambda: c.get_object_tagging(Bucket=BUCKET, Key=other), False),
            ]:
                check(db + '.foreign.' + suffix, call, deny=True, head=head)
                check(db + '.foreign.' + suffix + '.unchanged', lambda: state(foreign))
            for suffix, call, head in [
                ('empty.list', lambda: c.list_objects_v2(Bucket=BUCKET, Prefix=''), False),
                ('parent.list', lambda: c.list_objects_v2(Bucket=BUCKET, Prefix='database-poc/'), False),
                ('bucket.head', lambda: c.head_bucket(Bucket=BUCKET), True),
            ]:
                check(db + '.' + suffix, call, deny=True, head=head)

            copy_key = root + 'copy'
            objects.add((db, copy_key))
            check(db + '.copy.own', lambda: c.copy_object(Bucket=BUCKET, Key=copy_key,
                  CopySource={'Bucket': BUCKET, 'Key': marker}),
                  valid=lambda r: clients[db].get_object(Bucket=BUCKET, Key=copy_key)['Body'].read() == payloads[db])
            for suffix, source, owner, dest in [
                ('foreign-source', other, db, root + 'forbidden-copy'),
                ('foreign-destination', marker, foreign, roots[foreign] + 'forbidden-copy-' + db),
            ]:
                objects.add((owner, dest))
                check(db + '.copy.' + suffix, lambda: c.copy_object(Bucket=BUCKET, Key=dest,
                      CopySource={'Bucket': BUCKET, 'Key': source}), deny=True)
                check(db + '.copy.' + suffix + '.absent', lambda: absent(owner, dest))
                check(db + '.copy.' + suffix + '.unchanged', lambda: state(foreign))

            # One small part is valid for the final/only MPU part.
            for mode in ('complete', 'abort'):
                key = root + 'mpu-' + mode
                created = check(db + '.mpu.' + mode + '.create', lambda: create_upload(db, key))
                if not created:
                    record(db + '.mpu.' + mode + '.prerequisite', 'UNKNOWN', code='PrerequisiteFailure')
                    continue
                uid = created['UploadId']
                part = check(db + '.mpu.' + mode + '.part', lambda: c.upload_part(
                    Bucket=BUCKET, Key=key, UploadId=uid, PartNumber=1, Body=payloads[db]))
                if mode == 'complete' and part:
                    objects.add((db, key))
                    check(db + '.mpu.complete', lambda: c.complete_multipart_upload(
                        Bucket=BUCKET, Key=key, UploadId=uid,
                        MultipartUpload={'Parts': [{'PartNumber': 1, 'ETag': part['ETag']}]}))
                    check(db + '.mpu.complete.payload', lambda: c.get_object(Bucket=BUCKET, Key=key),
                          valid=lambda r: r['Body'].read() == payloads[db])
                elif mode == 'abort':
                    check(db + '.mpu.abort', lambda: c.abort_multipart_upload(Bucket=BUCKET, Key=key, UploadId=uid))
                else:
                    record(db + '.mpu.complete.prerequisite', 'UNKNOWN', code='PrerequisiteFailure')

            owner = clients[foreign]
            key = roots[foreign] + 'foreign-mpu-' + db
            created = check(db + '.foreign.mpu.owner.create', lambda: create_upload(foreign, key))
            if created:
                uid = created['UploadId']
                part = check(db + '.foreign.mpu.owner.part', lambda: owner.upload_part(
                    Bucket=BUCKET, Key=key, UploadId=uid, PartNumber=1, Body=payloads[foreign]))
                before = check(db + '.foreign.mpu.owner.parts', lambda: parts(foreign, key, uid),
                               valid=lambda r: bool(part) and len(r.get('Parts', [])) == 1
                               and r['Parts'][0]['PartNumber'] == 1
                               and r['Parts'][0]['ETag'] == part['ETag'])
                if part and before:
                    objects.add((foreign, key))
                    for suffix, call in [
                        ('initiate', lambda: foreign_create(c, foreign, key + '-new')),
                        ('part', lambda: c.upload_part(Bucket=BUCKET, Key=key, UploadId=uid,
                                                      PartNumber=1, Body=b'forbidden')),
                        ('complete', lambda: c.complete_multipart_upload(Bucket=BUCKET, Key=key,
                         UploadId=uid, MultipartUpload={'Parts': [{'PartNumber': 1, 'ETag': part['ETag']}]})),
                        ('abort', lambda: c.abort_multipart_upload(Bucket=BUCKET, Key=key, UploadId=uid)),
                    ]:
                        check(db + '.foreign.mpu.' + suffix, call, deny=True)
                        check(db + '.foreign.mpu.' + suffix + '.unchanged', lambda: parts(foreign, key, uid),
                              valid=lambda r: r.get('Parts') == before.get('Parts'))
                else:
                    record(db + '.foreign.mpu.prerequisite', 'UNKNOWN', code='PrerequisiteFailure')
            else:
                record(db + '.foreign.mpu.prerequisite', 'UNKNOWN', code='PrerequisiteFailure')

            key = root + 'part-copy'
            created = check(db + '.part-copy.create', lambda: create_upload(db, key))
            if created:
                uid = created['UploadId']
                check(db + '.part-copy.foreign-source', lambda: c.upload_part_copy(
                    Bucket=BUCKET, Key=key, UploadId=uid, PartNumber=1,
                    CopySource={'Bucket': BUCKET, 'Key': other}), deny=True)
                check(db + '.part-copy.parts.unchanged', lambda: parts(db, key, uid),
                      valid=lambda r: not r.get('Parts'))
                check(db + '.part-copy.source.unchanged', lambda: state(foreign))

            keys = [root + 'multi-1', root + 'multi-2']
            made = [check(db + '.multi.put.' + str(i), lambda k=k: put(db, k, payloads[db]))
                    for i, k in enumerate(keys)]
            if all(made):
                check(db + '.multi.own', lambda: c.delete_objects(Bucket=BUCKET,
                      Delete={'Objects': [{'Key': k} for k in keys]}),
                      valid=lambda r: not r.get('Errors') and
                      {e['Key'] for e in r.get('Deleted', [])} == set(keys))
                for i, k in enumerate(keys):
                    check(db + '.multi.absent.' + str(i), lambda k=k: absent(db, k))
            own = root + 'mixed-own'
            if check(db + '.mixed.put', lambda: put(db, own, payloads[db])):
                check(db + '.multi.mixed', lambda: c.delete_objects(Bucket=BUCKET,
                      Delete={'Objects': [{'Key': own}, {'Key': other}]}),
                      valid=lambda r: mixed_delete_ok(r, own, other))
                check(db + '.multi.mixed.foreign.unchanged', lambda: state(foreign))
        # Markers remain available until every negative case has finished.
        for db in DATABASES:
            check(db + '.marker.delete', lambda: clients[db].delete_object(Bucket=BUCKET, Key=markers[db]))
            check(db + '.marker.deleted', lambda: absent(db, markers[db]))
    except Exception:
        record('verification', 'UNKNOWN', code='LocalFailure')
    finally:
        for db, key, uid in sorted(uploads):
            def abort():
                try:
                    return clients[db].abort_multipart_upload(Bucket=BUCKET, Key=key, UploadId=uid)
                except Exception as error:
                    response = getattr(error, 'response', {})
                    if (response.get('ResponseMetadata', {}).get('HTTPStatusCode') == 404
                            and response.get('Error', {}).get('Code') == 'NoSuchUpload'):
                        return {'ResponseMetadata': {'HTTPStatusCode': 204}}
                    raise
            check(db + '.cleanup.upload.' + key.rsplit('/', 1)[-1], abort)
        for db, key in sorted(objects):
            check(db + '.cleanup.object.' + key.rsplit('/', 1)[-1],
                  lambda: clients[db].delete_object(Bucket=BUCKET, Key=key))
    return int(any(r['status'] != 'PASS' for r in report['cases']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('install')
    sub.add_parser('revoke')
    verify_parser = sub.add_parser('verify')
    verify_parser.add_argument('--endpoint', type=endpoint, default='http://127.0.0.1:18333')
    args = parser.parse_args()
    try:
        if args.command == 'verify':
            return verify(args.endpoint)
        install_or_revoke(args.command == 'revoke')
        return 0
    except Exception:
        print('command FAIL HTTP=- errorCode=LocalFailure')
        return 1


if __name__ == '__main__':
    sys.exit(main())
