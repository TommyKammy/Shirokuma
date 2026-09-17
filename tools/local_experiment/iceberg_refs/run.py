#!/usr/bin/env python3
"""Fixed owned-table Iceberg named-reference experiment; no user SQL or paths."""
from __future__ import annotations

from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.iceberg_refs import contract as C
from tools.local_experiment.iceberg_sandbox import run as SB

SESSION = SB.SESSION
CONTEXT, NAMESPACE = 'colima-mac-studio-solo', 'shirokuma-dev'
TABLE_NAMESPACE = 'iceberg.local_experiment'
FIXTURE = 'iceberg.smoke.fixture_v1'
ROWS_A = [[1, 'shirokuma'], [2, 'polaris']]
ROWS_B = ROWS_A + [[3, 'named-ref']]
FIXTURE_SQL = {
    'schema': "SELECT column_name, data_type FROM iceberg.information_schema.columns WHERE table_schema = 'smoke' AND table_name = 'fixture_v1' ORDER BY ordinal_position",
    'rows': f'SELECT id, name FROM {FIXTURE} ORDER BY id',
    'snapshots': 'SELECT snapshot_id, parent_id, operation FROM iceberg.smoke."fixture_v1$snapshots" ORDER BY snapshot_id',
    'history': 'SELECT snapshot_id, parent_id, is_current_ancestor FROM iceberg.smoke."fixture_v1$history" ORDER BY made_current_at',
    'refs': 'SELECT name, "type", snapshot_id FROM iceberg.smoke."fixture_v1$refs" ORDER BY name',
    'tables': 'SELECT table_catalog, table_schema, table_name FROM iceberg.information_schema.tables ORDER BY 1, 2, 3',
}
LIMITS = ['ONE_NEW_OWNED_TABLE_ONLY', 'FIXED_NAMED_REFS', 'METADATA_ONLY_REF_UPDATES',
          'NO_BRANCH_WRITES', 'NO_SEPARATE_TABLE_ZERO_COPY_CLONE', 'NO_SNAPSHOT_EXPIRY_OR_ORPHAN_CLEANUP',
          'SYNTHETIC_QUERY_USER_NOT_AUTHENTICATION']


class RefsError(RuntimeError):
    pass


def require(value, code):
    if not value:
        raise RefsError(code)


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def save(path, value):
    """Private atomic durable journal; never follow a pre-existing staging link."""
    require(path.parent.is_dir() and not path.parent.is_symlink(), 'INVALID_EVIDENCE_DIRECTORY')
    temporary = path.with_name(path.name + '.new')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    with os.fdopen(os.open(temporary, flags, 0o600), 'w') as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    require(not path.is_symlink(), 'INVALID_EVIDENCE_FILE')
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def command(*args):
    environment = {key: os.environ[key] for key in ('PATH', 'HOME', 'USER', 'TMPDIR', 'LANG', 'LC_ALL') if key in os.environ}
    result = subprocess.run(['rtk', 'proxy', *args], cwd=ROOT, env=environment,
                            capture_output=True, timeout=35)
    require(result.returncode == 0 and len(result.stdout) <= 1024 * 1024, 'SCOPED_COMMAND_FAILED')
    return result.stdout


def service_identity():
    value = json.loads(command('kubectl', '--context', CONTEXT, '-n', NAMESPACE,
                              '--request-timeout=30s', 'get', 'service', 'polaris', '-o', 'json'))
    metadata, spec = value.get('metadata', {}), value.get('spec', {})
    require(value.get('kind') == 'Service' and metadata.get('name') == 'polaris'
            and metadata.get('namespace') == NAMESPACE, 'POLARIS_SERVICE_SCOPE_MISMATCH')
    uid = C.canonical_uuid(metadata.get('uid'))
    ports = spec.get('ports')
    require(spec.get('type', 'ClusterIP') == 'ClusterIP' and type(ports) is list
            and any(row.get('port') == 8181 and row.get('protocol', 'TCP') == 'TCP' for row in ports),
            'POLARIS_SERVICE_PORT_MISMATCH')
    return {'name': 'polaris', 'namespace': NAMESPACE, 'uid': uid, 'spec_sha256': C.digest(spec)}


@contextmanager
def polaris_tunnel(progress, *, timeout=30):
    """Own only this port-forward process group, bound to one observed Service."""
    SESSION.check_context()
    before = service_identity()
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', C.PORT))
        except OSError:
            raise RefsError('POLARIS_LOOPBACK_PORT_IN_USE') from None
    journal = {'service': before, 'state': 'pending', 'port': C.PORT}
    progress(copy.deepcopy(journal))
    process = subprocess.Popen(['rtk', 'proxy', 'kubectl', '--context', CONTEXT, '-n', NAMESPACE,
        'port-forward', 'service/polaris', f'{C.PORT}:8181', '--address', '127.0.0.1'],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    selector, drainer = selectors.DefaultSelector(), None
    def guard():
        require(process.poll() is None and service_identity() == before, 'POLARIS_SERVICE_BINDING_CHANGED')
        return copy.deepcopy(before)
    try:
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline, output = time.monotonic() + timeout, b''
        while not re.search(rb'(?:^|\n)Forwarding from 127\.0\.0\.1:18181 -> 8181\r?\n', output):
            require(time.monotonic() < deadline and process.poll() is None, 'POLARIS_TUNNEL_NOT_READY')
            if selector.select(min(.25, max(0, deadline - time.monotonic()))):
                chunk = process.stdout.read1(4096)
                require(bool(chunk), 'POLARIS_TUNNEL_CLOSED')
                output += chunk
                require(len(output) <= 65536, 'POLARIS_TUNNEL_OUTPUT_OVERSIZED')
        guard()
        def drain():
            while process.stdout.read(4096):
                pass
        drainer = threading.Thread(target=drain, daemon=True)
        drainer.start()
        journal['state'] = 'ready'
        progress(copy.deepcopy(journal))
        yield guard
        guard()
        journal['state'] = 'verified'
        progress(copy.deepcopy(journal))
    except BaseException:
        journal['state'] = 'failed'
        try:
            progress(copy.deepcopy(journal))
        except Exception:
            pass
        raise
    finally:
        selector.close()
        SESSION.stop_group(process, signal.SIGTERM, 5)
        if drainer is not None:
            drainer.join(timeout=5)
        process.stdout.close()


class Experiment:
    def __init__(self, worker, control, refs, output, token, *, guard=lambda: None, timeout=600):
        self.name = C.table_name(token)
        self.table = TABLE_NAMESPACE + '.' + self.name
        self.worker, self.control, self.refs = worker, control, refs
        self.output, self.token, self.guard = output, token, guard
        require(worker.user == SB.run_user(token) and control.user == SB.run_user(token, True), 'QUERY_IDENTITIES_DIFFER')
        self.deadline = time.monotonic() + timeout
        self.latest = None
        self.created = False
        self.creation_attempted = False
        self.uncertain = False
        self.report = {'schema_version': 1, 'run_token': token, 'table': self.table, 'marker': C.marker(token),
            'result': 'running', 'started_at': stamp(), 'limits': LIMITS, 'steps': [], 'pending': None,
            'uncertain_operations': [], 'table_created': False,
            'rest': refs.receipt(), 'query_drain': {'verified': False},
            'cleanup': {'verified': False, 'removed': [], 'remaining': [], 'deferred': [], 'errors': []}}
        worker.progress = control.progress = self.persist
        refs.progress = self.rest_progress

    def persist(self):
        self.report['creation_attempted'] = self.creation_attempted
        self.report['sql_outcome_unknown'] = self.uncertain
        self.report['queries'] = {key: [{k: copy.deepcopy(v) for k, v in row.items() if not k.startswith('_')}
            for row in client.records] for key, client in [('worker', self.worker), ('control', self.control)]}
        save(self.output / 'summary.json', self.report)

    def rest_progress(self, journal):
        self.report['rest'] = copy.deepcopy(journal)
        self.persist()

    def step(self, label, call, *, mutation=False):
        require(time.monotonic() < self.deadline, 'EXPERIMENT_DEADLINE_EXCEEDED')
        operation = {'id': uuid.uuid4().hex, 'name': label, 'mutation': mutation, 'outcome': 'pending', 'started_at': stamp()}
        self.report['steps'].append(operation)
        self.report['pending'] = operation['id']
        self.persist()
        try:
            if mutation:
                require(not self.uncertain and self.refs.receipt()['pending'] is None
                        and self.refs.receipt()['outcome'] != 'unknown', 'MUTATION_STATE_UNKNOWN')
                self.guard()
            result = call()
        except BaseException:
            operation['outcome'] = 'unknown' if mutation else 'failed'
            self.uncertain |= mutation
            if mutation:
                self.report['uncertain_operations'].append(operation['id'])
            self.persist()
            raise
        operation.update(outcome='confirmed', completed_at=stamp())
        self.report['pending'] = None
        self.persist()
        return result

    def sql(self, label, sql, *, mutation=False, client=None):
        client = client or self.worker
        def execute():
            if label == 'create_owned_table':
                # step() has already persisted its intent and checked the scope.
                # Keep this in memory before even starting the HTTP exchange so
                # a successful CREATE followed by a failed journal write cannot
                # be mistaken for "no table was created" during cleanup.
                self.creation_attempted = True
            return SB.bounded_query(client, sql, self.deadline)
        try:
            return self.step(label, execute, mutation=mutation)
        except BaseException:
            if any(not row.get('response_complete') for row in client.records):
                self.uncertain = True
            raise

    def fixture(self, label):
        captured = {}
        for key, sql in FIXTURE_SQL.items():
            value = self.sql(label + '_' + key, sql, client=self.control)
            captured[key] = {k: value[k] for k in ('columns', 'column_types', 'rows')}
        SB.check_rows(captured['rows'], ROWS_A)
        require(captured['schema']['rows'] == [['id', 'bigint'], ['name', 'varchar']], 'FIXTURE_SCHEMA_CHANGED')
        self.report['fixture_' + label] = captured
        self.persist()
        return captured

    def load(self):
        value = self.step('rest_load', lambda: self.refs.load(expected_uuid=self.latest['table_uuid'] if self.latest else None))
        C.validate_projection(value, self.token)
        return value

    def owner(self):
        require(self.created and self.latest is not None, 'ORIGINAL_TABLE_UUID_UNKNOWN')
        current = self.load()
        require(current == self.latest, 'OWNED_METADATA_CHANGED')
        ddl = self.sql('owner_marker', f'SHOW CREATE TABLE {self.table}', client=self.control)
        require(len(ddl['rows']) == 1 and len(ddl['rows'][0]) == 1 and type(ddl['rows'][0][0]) is str
                and re.search(r"\bCOMMENT\s+'" + re.escape(C.marker(self.token)) + r"'", ddl['rows'][0][0]),
                'OWNER_MARKER_CHANGED')
        return current

    def files(self):
        result = self.sql('data_files', f'SELECT file_path, record_count, file_size_in_bytes FROM {TABLE_NAMESPACE}."{self.name}$files" ORDER BY file_path')
        require(result['column_types'] == ['varchar', 'bigint', 'bigint'], 'DATA_FILES_TYPES_DIFFER')
        return C.project_data_files(result)

    def inspect(self, label, expected_refs, expected_snapshots):
        current = self.load()
        require(current['refs'] == expected_refs, 'REST_REFS_DIFFER')
        actual_snapshots = {row['snapshot_id']: row['parent_snapshot_id'] for row in current['snapshots']}
        require(actual_snapshots == expected_snapshots, 'REST_SNAPSHOT_LINEAGE_DIFFERS')
        refs = self.sql('trino_refs', f'SELECT name, "type", snapshot_id FROM {TABLE_NAMESPACE}."{self.name}$refs" ORDER BY name')
        wanted = [[name, ref['type'].upper(), ref['snapshot_id']] for name, ref in sorted(expected_refs.items())]
        require(refs['columns'] == ['name', 'type', 'snapshot_id'] and refs['column_types'] == ['varchar', 'varchar', 'bigint']
                and refs['rows'] == wanted, 'TRINO_REFS_DIFFER')
        snapshots = self.sql('trino_snapshots', f'SELECT snapshot_id, parent_id, operation FROM {TABLE_NAMESPACE}."{self.name}$snapshots" ORDER BY snapshot_id')
        require(snapshots['columns'] == ['snapshot_id', 'parent_id', 'operation']
                and snapshots['column_types'] == ['bigint', 'bigint', 'varchar']
                and snapshots['rows'] == [[sid, parent, 'append'] for sid, parent in sorted(expected_snapshots.items())],
                'TRINO_SNAPSHOT_LINEAGE_DIFFERS')
        self.latest = current
        value = {'metadata': current, 'refs': refs['rows'], 'snapshots': snapshots['rows'], 'files': self.files()}
        self.report.setdefault('observations', {})[label] = value
        self.persist()
        return value

    def rows(self, label, ref, expected):
        require(ref in ('main', 'baseline_tag', 'audit_branch'), 'REF_NAME_NOT_ALLOWED')
        self.owner()
        clause = '' if ref == 'main' else f" FOR VERSION AS OF '{ref}'"
        result = self.sql(label, f'SELECT id, name FROM {self.table}{clause} ORDER BY id')
        SB.check_rows(result, expected)
        self.report.setdefault('row_observations', {})[label] = {'ref': ref, 'query_id': result['query_id'], 'rows': result['rows']}
        self.persist()

    def ref_update(self, name, target, expected=None, *, conflict=False):
        from tools.local_experiment.iceberg_refs.client import RefConflict
        before = self.owner()
        files_before = self.files()
        rejected = False
        def commit():
            nonlocal rejected
            try:
                return self.refs.set_ref(before, name, target, expected_ref=expected)
            except RefConflict as error:
                require(conflict and error.outcome == 'rejected' and error.http_status == 409,
                        'UNEXPECTED_REF_CONFLICT')
                rejected = True
                return None
        after = self.step('stale_cas' if conflict else 'set_' + name, commit, mutation=True)
        if conflict:
            require(rejected, 'STALE_CAS_NOT_REJECTED')
            after = self.load()
            require(after == before, 'REJECTED_CAS_CHANGED_METADATA')
        else:
            C.verify_transition(before, after, self.token, name, target)
        self.latest = after
        files_after = self.files()
        require(after['current_snapshot_id'] == before['current_snapshot_id'] and files_after == files_before,
                'REF_UPDATE_CHANGED_DATA_FILES')
        record = {'name': name, 'before_snapshot': expected, 'target_snapshot': target,
                  'current_snapshot_id': before['current_snapshot_id'], 'files_before': files_before,
                  'files_after': files_after, 'metadata_only_verified': True, 'conflict_verified': rejected}
        self.report.setdefault('reference_updates', []).append(record)
        self.persist()

    def cleanup(self):
        result = self.report['cleanup']
        journal = self.refs.receipt()
        if self.uncertain or journal['pending'] is not None or journal['outcome'] == 'unknown':
            result['deferred'] = ['MUTATION_OUTCOME_UNKNOWN']
            return
        if not self.created:
            if self.creation_attempted:
                result['deferred'] = ['ORIGINAL_TABLE_UUID_UNKNOWN']
            else:
                result['verified'] = True
            return
        if self.latest is None:
            result['deferred'] = ['ORIGINAL_TABLE_UUID_UNKNOWN']
            return
        if not self.report['query_drain']['verified']:
            result['deferred'] = ['QUERY_DRAIN_UNVERIFIED']
            return
        try:
            self.owner()
            self.sql('drop_owned_table', f'DROP TABLE {self.table}', mutation=True, client=self.control)
            tables = self.sql('cleanup_inventory', FIXTURE_SQL['tables'], client=self.control)['rows']
            result['remaining'] = [self.name] if ['iceberg', 'local_experiment', self.name] in tables else []
            require(not result['remaining'], 'OWNED_TABLE_STILL_PRESENT')
            result.update(removed=[self.name], verified=True)
        except BaseException as error:
            result['deferred'] = ['OWNED_CLEANUP_UNVERIFIED']
            if not isinstance(error, Exception):
                raise

    def run(self):
        interruption = None
        success = False
        self.persist()
        try:
            before = self.fixture('before')
            require(['iceberg', 'local_experiment', self.name] not in before['tables']['rows'], 'OWNED_NAME_COLLISION')
            self.step('authenticate', self.refs.authenticate)
            self.sql('create_owned_table', f"CREATE TABLE {self.table} COMMENT '{C.marker(self.token)}' WITH (format = 'PARQUET', format_version = 2) AS SELECT CAST(id AS BIGINT) AS id, name FROM (VALUES (1, 'shirokuma'), (2, 'polaris')) AS fixture(id, name)", mutation=True)
            self.created = True
            self.report['table_created'] = True
            self.persist()
            first = self.load()
            self.latest = first
            self.report['original_table_uuid'] = first['table_uuid']
            a = first['current_snapshot_id']
            refs = {'main': {'type': 'branch', 'snapshot_id': a}}
            self.inspect('A', refs, {a: None})
            self.rows('main_A', 'main', ROWS_A)
            self.ref_update('baseline_tag', a)
            self.ref_update('audit_branch', a)
            refs.update(baseline_tag={'type': 'tag', 'snapshot_id': a}, audit_branch={'type': 'branch', 'snapshot_id': a})
            self.inspect('refs_A', refs, {a: None})
            self.owner()
            self.sql('append_main', f"INSERT INTO {self.table} VALUES (BIGINT '3', 'named-ref')", mutation=True)
            second = self.load()
            b = second['current_snapshot_id']
            require(a != b, 'APPEND_SNAPSHOT_UNCHANGED')
            self.latest = second
            refs['main']['snapshot_id'] = b
            self.inspect('main_B_refs_A', refs, {a: None, b: a})
            for ref, rows in [('main', ROWS_B), ('baseline_tag', ROWS_A), ('audit_branch', ROWS_A)]:
                self.rows('before_cas_' + ref, ref, rows)
            self.ref_update('audit_branch', b, a)
            refs['audit_branch']['snapshot_id'] = b
            self.inspect('main_B_branch_B_tag_A', refs, {a: None, b: a})
            for ref, rows in [('main', ROWS_B), ('baseline_tag', ROWS_A), ('audit_branch', ROWS_B)]:
                self.rows('after_cas_' + ref, ref, rows)
            self.ref_update('audit_branch', a, a, conflict=True)
            self.inspect('stale_cas_unchanged', refs, {a: None, b: a})
            self.report.update(snapshot_A=a, snapshot_B=b, named_refs_verified=True, stale_cas_verified=True)
            success = True
        except BaseException as error:
            self.report['failure_code'] = str(error) if re.fullmatch('[A-Z][A-Z0-9_]{0,95}', str(error)) else 'REFS_EXPERIMENT_FAILED'
            if not isinstance(error, Exception):
                interruption = error
        finally:
            self.deadline = time.monotonic() + 180
            try:
                self.report['query_drain'] = SB.drain_queries(self.control, self.worker, self.token)
            except BaseException as error:
                self.report['query_drain'] = {'verified': False, 'errors': ['OWNED_QUERY_DRAIN_UNVERIFIED']}
                if not isinstance(error, Exception) and interruption is None:
                    interruption = error
            try:
                self.cleanup()
                after = self.fixture('after')
                before = self.report.get('fixture_before')
                self.report['fixture_unchanged'] = before is not None and all(before[k] == after[k] for k in before if k != 'tables')
                self.report['table_inventory_unchanged'] = before is not None and before['tables'] == after['tables']
            except BaseException as error:
                self.report['verification_error'] = 'FINAL_READBACK_OR_CLEANUP_UNVERIFIED'
                if not isinstance(error, Exception) and interruption is None:
                    interruption = error
            passed = (success and self.report['query_drain']['verified'] and self.report['cleanup']['verified']
                      and self.report.get('fixture_unchanged') and self.report.get('table_inventory_unchanged'))
            self.report.update(result='interrupted' if interruption else 'passed' if passed else 'failed', completed_at=stamp(),
                               rest=self.refs.receipt())
            self.persist()
        if interruption is not None:
            raise interruption
        return self.report


def run_experiment(worker, control, refs, output, token, *, guard=lambda: None):
    return Experiment(worker, control, refs, output, token, guard=guard).run()


def main():
    from tools.local_experiment.iceberg_refs.client import RefClient
    require(len(sys.argv) == 1, 'CLI_ARGUMENTS_NOT_ALLOWED')
    token = uuid.uuid4().hex
    output = ROOT / 'tmp/local-experiment/iceberg-refs' / token
    output.mkdir(parents=True, mode=0o700)
    tunnel = {}
    def tunnel_progress(value):
        tunnel.update(value)
        save(output / 'tunnel.json', tunnel)
    try:
        with SESSION.interruption_handlers(), polaris_tunnel(tunnel_progress) as guard, SESSION.trino_tunnel() as url:
            worker, control = SB.SandboxClient(url, token), SB.SandboxClient(url, token, control=True)
            refs = RefClient(token, progress=lambda value: None, guard=guard)
            report = run_experiment(worker, control, refs, output, token, guard=guard)
        require(tunnel.get('state') == 'verified', 'POLARIS_TUNNEL_FINAL_GUARD_FAILED')
    except BaseException as error:
        path = output / 'summary.json'
        report = json.loads(path.read_bytes()) if path.is_file() and not path.is_symlink() else {'run_token': token}
        report.update(result='interrupted' if not isinstance(error, Exception) else 'failed',
                      exit_error='REFS_RUNTIME_OR_SCOPE_FAILED')
        save(path, report)
        if isinstance(error, SESSION.SessionInterrupted):
            print(json.dumps({'result': report['result'], 'run_token': token, 'evidence_path': str(output)}))
            return 128 + error.signum
        if not isinstance(error, Exception):
            raise
    print(json.dumps({'result': report['result'], 'run_token': token, 'evidence_path': str(output)}))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
