"""Dump only this owned PostgreSQL; restore into a separate new database."""
import copy
import json
import os
import selectors
import signal
import subprocess
import tempfile
import time

from . import manifest
from .fixtures import DATABASE, DATABASE_USER, RESTORE_DATABASE, backend_records
from .support import CONTEXT, NAMESPACE, digest, directory, read, require, stop_group, token, write

MAX_DUMP = 16 * 1024**2
TABLES = ['flyway_schema_history', 'gateway_backend', 'query_history']
# No arbitrary SQL or database name comes from caller input.
SNAPSHOT_SQL = """SELECT json_build_object(
 'version',current_setting('server_version'), 'user',current_user,
 'tables',(SELECT json_agg(table_name ORDER BY table_name) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'),
 'columns',(SELECT json_agg(json_build_array(table_name,column_name,data_type,is_nullable,column_default) ORDER BY table_name,ordinal_position) FROM information_schema.columns WHERE table_schema='public'),
 'indexes',(SELECT json_agg(json_build_array(tablename,indexname,indexdef) ORDER BY tablename,indexname) FROM pg_indexes WHERE schemaname='public'),
 'backends',(SELECT json_agg(to_jsonb(b) ORDER BY name) FROM gateway_backend b),
 'migrations',(SELECT json_agg(to_jsonb(f) ORDER BY installed_rank) FROM flyway_schema_history f),
 'history_count',(SELECT count(*) FROM query_history));"""
COLLISION_SQL = "SELECT json_build_object('exists',EXISTS(SELECT 1 FROM pg_database WHERE datname='vw_restored'));"


def binary_command(args, *, body=None, timeout=40, limit=MAX_DUMP):
    """Bound bytes while reading, reap this subprocess group on every interruption."""
    with tempfile.TemporaryFile() as source:
        if body is not None:
            require(type(body) is bytes and len(body) <= MAX_DUMP, 'PG_INPUT_TOO_LARGE')
            source.write(body)
            source.seek(0)
        process = subprocess.Popen(args, stdin=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, env={key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR', 'USER', 'LOGNAME')
                                         if key in os.environ})
        streams, result = selectors.DefaultSelector(), {1: bytearray(), 2: bytearray()}
        streams.register(process.stdout, selectors.EVENT_READ, 1)
        streams.register(process.stderr, selectors.EVENT_READ, 2)
        deadline = time.monotonic() + timeout
        try:
            while streams.get_map():
                require(time.monotonic() < deadline, 'PG_COMMAND_TIMEOUT')
                for key, _ in streams.select(min(0.5, max(0, deadline - time.monotonic()))):
                    data = os.read(key.fileobj.fileno(), 65536)
                    if not data:
                        streams.unregister(key.fileobj)
                        continue
                    result[key.data].extend(data)
                    require(len(result[key.data]) <= (limit if key.data == 1 else 65536), 'PG_OUTPUT_TOO_LARGE')
            require(process.wait(timeout=max(0.01, deadline - time.monotonic())) == 0, 'PG_COMMAND_FAILED')
            return bytes(result[1])
        except BaseException:
            stop_group(process, signal.SIGTERM, 5)
            raise
        finally:
            streams.close()
            process.stdout.close()
            process.stderr.close()


def validate_snapshot(value, run_token):
    require(type(value) is dict and set(value) == {'version', 'user', 'tables', 'columns', 'indexes',
            'backends', 'migrations', 'history_count'}, 'PG_SNAPSHOT_INVALID')
    require(value['version'].split()[0] == '18.4' and value['user'] == DATABASE_USER
            and value['tables'] == TABLES and type(value['history_count']) is int and value['history_count'] == 0,
            'PG_SCOPE_MISMATCH')
    expected = [{'name': row['name'], 'routing_group': row['routingGroup'], 'backend_url': row['proxyTo'],
                 'external_url': row['externalUrl'], 'active': True} for row in backend_records(run_token)]
    require(value['backends'] == sorted(expected, key=lambda row: row['name']), 'PG_BACKENDS_MISMATCH')
    migrations = value['migrations']
    require(type(migrations) is list and len(migrations) == 4
            and [row.get('version') for row in migrations] == ['1', '2', '3', '4']
            and all(row.get('success') is True and row.get('type') == 'SQL'
                    and type(row.get('checksum')) is int for row in migrations), 'PG_MIGRATIONS_INVALID')
    require(type(value['columns']) is list and len(value['columns']) == 23
            and {row[0] for row in value['columns']} == set(TABLES)
            and type(value['indexes']) is list and len(value['indexes']) == 5, 'PG_SCHEMA_INVALID')
    return value


def verify_backup_restore(run_token, pod, pod_uid, output, *, guard):
    token(run_token)
    require(callable(guard) and pod['kind'] == 'Pod' and pod['metadata']['name'] == manifest.name(run_token)
            and pod['metadata']['namespace'] == NAMESPACE, 'PG_POD_SCOPE_INVALID')
    directory(output)
    require(output.stat().st_mode & 0o777 == 0o700, 'NONPRIVATE_EVIDENCE')
    receipt_path, dump_path = output / 'database.json', output / 'gateway-postgresql.dump'
    require(not receipt_path.exists() and not dump_path.exists(), 'PG_EVIDENCE_COLLISION')
    receipt = {'schema_version': 1, 'run_token': run_token, 'pod_uid': pod_uid, 'result': 'running',
               'source_database': DATABASE, 'restore_database': RESTORE_DATABASE, 'pending': None,
               'history_enabled': False, 'history_rows': None, 'dump': None, 'source': None, 'restored': None,
               'restore_created': False, 'source_unchanged': False}

    def save():
        write(receipt_path, receipt)

    def invoke(args, *, body=None, limit=MAX_DUMP):
        current = guard()
        manifest.assert_owned(current, pod, pod_uid)
        return binary_command(['kubectl', '--context', CONTEXT, '-n', NAMESPACE, 'exec', '-i',
            pod['metadata']['name'], '-c', 'postgres', '--', *args], body=body, limit=limit)

    def query(sql, database):
        require(database in (DATABASE, RESTORE_DATABASE), 'PG_DATABASE_INVALID')
        return json.loads(invoke(['psql', '-X', '-w', '-U', DATABASE_USER, '-d', database,
            '-v', 'ON_ERROR_STOP=1', '-A', '-t', '-c', sql], limit=1024**2))

    save()
    try:
        receipt['pending'] = 'source-snapshot'
        save()
        before = validate_snapshot(query(SNAPSHOT_SQL, DATABASE), run_token)
        receipt['source'] = {'logical_sha256': digest(before), 'tables': TABLES, 'backend_rows': 2,
                             'migration_rows': 4, 'history_rows': 0, 'postgres_version': before['version']}
        require(query(COLLISION_SQL, DATABASE) == {'exists': False}, 'PG_RESTORE_DATABASE_COLLISION')
        receipt['pending'] = 'dump'
        save()
        dump = invoke(['pg_dump', '-w', '-U', DATABASE_USER, '-d', DATABASE, '--format=custom',
                       '--no-owner', '--no-privileges'])
        require(dump.startswith(b'PGDMP') and 0 < len(dump) <= MAX_DUMP, 'PG_DUMP_INVALID')
        with os.fdopen(os.open(dump_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as stream:
            stream.write(dump)
            stream.flush()
            os.fsync(stream.fileno())
        receipt['dump'] = {'sha256': digest(dump), 'bytes': len(dump), 'format': 'postgres-custom'}
        receipt['pending'] = 'create-restore-database'
        save()
        # No DROP and no --clean: any pre-existing or raced-in name causes an error.
        invoke(['createdb', '-w', '-U', DATABASE_USER, '--template=template0', RESTORE_DATABASE], limit=65536)
        receipt['restore_created'] = True
        receipt['pending'] = 'restore'
        save()
        saved_dump = read(dump_path, MAX_DUMP)
        require(digest(saved_dump) == receipt['dump']['sha256'], 'PG_DUMP_CHANGED')
        invoke(['pg_restore', '-w', '-U', DATABASE_USER, '-d', RESTORE_DATABASE, '--exit-on-error',
                '--single-transaction', '--no-owner', '--no-privileges'], body=saved_dump, limit=65536)
        receipt['pending'] = 'readback'
        save()
        restored = validate_snapshot(query(SNAPSHOT_SQL, RESTORE_DATABASE), run_token)
        after = validate_snapshot(query(SNAPSHOT_SQL, DATABASE), run_token)
        require(before == restored == after, 'PG_RESTORE_MISMATCH')
        receipt.update(restored={'logical_sha256': digest(restored), 'matches_source': True},
                       source_unchanged=True, history_rows=0, result='passed', pending=None)
    except BaseException as error:
        receipt.update(result='failed', error_type=type(error).__name__)
        save()
        raise
    save()
    return copy.deepcopy(receipt)
