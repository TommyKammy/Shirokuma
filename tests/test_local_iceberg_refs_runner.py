"""Fixed orchestration and cleanup boundaries with in-memory Trino/REST fakes."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.iceberg_refs import contract as C
from tools.local_experiment.iceberg_refs import run as R
from tools.local_experiment.iceberg_refs.client import RefConflict, RefError

TOKEN = 'a' * 32
UUID = '00000000-0000-4000-8000-000000000001'
OTHER_UUID = '00000000-0000-4000-8000-000000000002'


def state():
    return {'present': False, 'uuid': UUID, 'main': 10, 'refs': {'main': {'type': 'branch', 'snapshot-id': 10}},
            'rows': copy.deepcopy(R.ROWS_A), 'revision': 0, 'marker': C.marker(TOKEN), 'sql': [],
            'mutations': [], 'durable': [], 'all_tables': [['iceberg', 'smoke', 'fixture_v1']]}


def metadata(value):
    snapshots = [{'snapshot-id': 10, 'sequence-number': 1, 'timestamp-ms': 1,
                  'manifest-list': 's3://private/A.avro', 'summary': {'operation': 'append'}}]
    if value['main'] == 20:
        snapshots.append({'snapshot-id': 20, 'parent-snapshot-id': 10, 'sequence-number': 2,
                          'timestamp-ms': 2, 'manifest-list': 's3://private/B.avro', 'summary': {'operation': 'append'}})
    return {'metadata-location': f's3://private/metadata/v{value["revision"]}.json', 'metadata': {
        'table-uuid': value['uuid'], 'format-version': 2, 'location': 's3://private/table',
        'properties': {'comment': value['marker']}, 'last-updated-ms': value['revision'],
        'current-schema-id': 0, 'schemas': [{'schema-id': 0, 'type': 'struct', 'fields': [
            {'id': 1, 'name': 'id', 'type': 'long', 'required': False},
            {'id': 2, 'name': 'name', 'type': 'string', 'required': False}]}],
        'snapshots': snapshots, 'current-snapshot-id': value['main'], 'refs': copy.deepcopy(value['refs'])}}


class Trino:
    def __init__(self, value, output, *, control=False):
        self.state, self.output = value, output
        self.user, self.timeout = R.SB.run_user(TOKEN, control), 30
        self.records, self.progress = [], None

    def query(self, sql):
        value = self.state
        value['sql'].append(sql)
        record = {'query_id': f'q_{len(value["sql"])}', 'result': 'running', 'response_complete': False,
                  'sql_sha256': hashlib.sha256(sql.encode()).hexdigest()}
        self.records.append(record)
        if self.progress:
            self.progress()
        def result(columns, rows, types=None):
            record.update(result='finished', response_complete=True)
            if self.progress:
                self.progress()
            return {'query_id': record['query_id'], 'columns': columns, 'rows': copy.deepcopy(rows),
                    'column_types': types or ['varchar'] * len(columns)}
        if any(sql.startswith(prefix) for prefix in value.get('fail_sql', [])):
            record['result'] = 'failed'
            raise R.SB.SandboxError('bounded local sandbox SQL failed')
        if sql.startswith(('CREATE TABLE', 'INSERT INTO', 'DROP TABLE')):
            receipt = json.loads((self.output / 'summary.json').read_bytes())
            value['durable'].append(receipt['pending'] is not None and receipt['steps'][-1]['mutation'])
            value['mutations'].append(sql.split()[0])
        if sql.startswith('CREATE TABLE'):
            value['present'] = True
            if value.get('cancel_create'):
                raise R.SESSION.SessionInterrupted(15)
            return result(['rows'], [[2]], ['bigint'])
        if sql.startswith('INSERT INTO'):
            value.update(main=20, rows=copy.deepcopy(R.ROWS_B), revision=value['revision'] + 1)
            value['refs']['main']['snapshot-id'] = 20
            return result(['rows'], [[1]], ['bigint'])
        if sql.startswith('DROP TABLE'):
            value['present'] = False
            return result([], [])
        if sql == R.FIXTURE_SQL['tables']:
            rows = copy.deepcopy(value['all_tables'])
            if value['present']:
                rows.append(['iceberg', 'local_experiment', C.table_name(TOKEN)])
            return result(['table_catalog', 'table_schema', 'table_name'], sorted(rows))
        for key, fixed in R.FIXTURE_SQL.items():
            if sql == fixed:
                values = {
                    'schema': (['column_name','data_type'], [['id','bigint'],['name','varchar']], ['varchar','varchar']),
                    'rows': (['id','name'], R.ROWS_A, ['bigint','varchar']),
                    'snapshots': (['snapshot_id','parent_id','operation'], [[100,None,'append']], ['bigint','bigint','varchar']),
                    'history': (['snapshot_id','parent_id','is_current_ancestor'], [[100,None,True]], ['bigint','bigint','boolean']),
                    'refs': (['name','type','snapshot_id'], [['main','BRANCH',100]], ['varchar','varchar','bigint']),
                }
                columns, rows, types = values[key]
                if value.get('fixture_changed') and not value['present'] and value['mutations']:
                    rows = [[999, 'changed']] if key == 'rows' else rows
                return result(columns, rows, types)
        if sql.startswith('SHOW CREATE TABLE'):
            return result(['Create Table'], [[f"CREATE TABLE t COMMENT '{value['marker']}'"]])
        if '$files"' in sql:
            rows = [['s3://private/A.parquet',2,100]]
            if value['main'] == 20:
                rows += [['s3://private/B.parquet',1,50]]
            if value.get('file_changed') and value['revision'] >= 1:
                rows[0][2] = 999
            return result(['file_path','record_count','file_size_in_bytes'], rows, ['varchar','bigint','bigint'])
        if '$refs"' in sql:
            rows = [[name, ref['type'].upper(), ref['snapshot-id']] for name, ref in sorted(value['refs'].items())]
            if value.get('wrong_trino_ref') and len(rows) > 1:
                rows[0][2] = 999
            return result(['name','type','snapshot_id'], rows, ['varchar','varchar','bigint'])
        if '$snapshots"' in sql:
            rows = [[10,None,'append']] + ([[20,10,'append']] if value['main'] == 20 else [])
            return result(['snapshot_id','parent_id','operation'], rows, ['bigint','bigint','varchar'])
        if sql.startswith('SELECT id, name FROM iceberg.local_experiment.'):
            ref = next((name for name in C.REFS if f"'{name}'" in sql), 'main')
            rows = R.ROWS_A if value['refs'][ref]['snapshot-id'] == 10 else R.ROWS_B
            if value.get('wrong_rows'):
                rows = [[1,'incorrect']]
            return result(['id','name'], rows, ['bigint','varchar'])
        raise AssertionError('unexpected fixed SQL')


class Refs:
    def __init__(self, value, output):
        self.state, self.output, self.progress = value, output, lambda journal: None
        self.journal = {'schema_version': 1, 'run_token': TOKEN, 'table': C.table_name(TOKEN),
            'authenticated': False, 'table_uuid': None, 'outcome': 'ready', 'pending': None,
            'operations': [], 'observations': []}

    def receipt(self):
        return copy.deepcopy(self.journal)

    def authenticate(self):
        self.journal['authenticated'] = True
        self.progress(self.receipt())

    def load(self, expected_uuid=None):
        value = C.project_metadata(metadata(self.state), TOKEN, expected_uuid)
        self.journal['table_uuid'] = value['table_uuid']
        self.journal['observations'].append(value)
        self.progress(self.receipt())
        return value

    def set_ref(self, before, name, target, expected_ref=None):
        operation = {'ref_name': name, 'expected_snapshot_id': expected_ref, 'target_snapshot_id': target}
        self.journal['operations'].append(operation)
        receipt = json.loads((self.output/'summary.json').read_bytes())
        self.state['durable'].append(receipt['pending'] is not None and receipt['steps'][-1]['mutation'])
        if self.state.get('rest_unknown'):
            self.journal.update(outcome='unknown', pending=OTHER_UUID)
            self.progress(self.receipt())
            raise RefError('REF_COMMIT_UNCERTAIN')
        actual = self.state['refs'].get(name, {}).get('snapshot-id')
        if actual != expected_ref:
            self.journal.update(outcome='rejected', pending=None)
            if self.state.get('conflict_mutates'):
                self.state['revision'] += 1
            self.progress(self.receipt())
            raise RefConflict()
        self.state['refs'][name] = {'type': C.REFS[name], 'snapshot-id': target}
        self.state['revision'] += 1
        self.journal.update(outcome='verified', pending=None)
        self.progress(self.receipt())
        return self.load()


class RunnerTests(unittest.TestCase):
    def execute(self, value=None, *, drain=True, guard=None):
        value = value or state()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            worker, control, refs = Trino(value, output), Trino(value, output, control=True), Refs(value, output)
            with patch.object(R.SB, 'drain_queries', return_value={'verified': drain, 'active': [], 'user': worker.user}):
                report = R.run_experiment(worker, control, refs, output, TOKEN, guard=guard or (lambda: None))
            self.assertEqual(report, json.loads((output/'summary.json').read_bytes()))
        return value, report

    def test_fixed_sequence_uses_independent_named_rows_refs_files_and_cleanup(self):
        value, report = self.execute()
        self.assertEqual(report['result'], 'passed')
        self.assertEqual(value['mutations'], ['CREATE','INSERT','DROP'])
        self.assertEqual(len(report['reference_updates']), 4)
        self.assertEqual([r['conflict_verified'] for r in report['reference_updates']], [False,False,False,True])
        self.assertEqual(report['snapshot_A'], 10)
        self.assertEqual(report['snapshot_B'], 20)
        self.assertTrue(report['fixture_unchanged'])
        self.assertTrue(report['table_inventory_unchanged'])
        self.assertTrue(report['cleanup']['verified'])
        self.assertFalse(value['present'])
        self.assertTrue(all(value['durable']))
        self.assertEqual(len(report['row_observations']), 7)
        self.assertNotIn('s3://private', json.dumps(report))
        self.assertNotIn('CREATE TABLE', json.dumps(report))
        self.assertTrue(all(C.table_name(TOKEN) in sql for sql in value['sql'] if sql.startswith(('CREATE','INSERT','DROP'))))

    def test_collision_has_zero_auth_or_mutation_and_preserves_existing_table(self):
        value = state()
        value['present'] = True
        value, report = self.execute(value)
        self.assertEqual(report['failure_code'], 'OWNED_NAME_COLLISION')
        self.assertEqual(value['mutations'], [])
        self.assertFalse(report['rest']['authenticated'])
        self.assertTrue(value['present'])

    def test_rest_unknown_prevents_all_later_mutations_and_cleanup(self):
        value = state()
        value['rest_unknown'] = True
        value, report = self.execute(value)
        self.assertEqual(value['mutations'], ['CREATE'])
        self.assertEqual(report['result'], 'failed')
        self.assertEqual(report['cleanup']['deferred'], ['MUTATION_OUTCOME_UNKNOWN'])
        self.assertEqual(report['rest']['pending'], OTHER_UUID)
        self.assertEqual(len(report['uncertain_operations']), 1)
        self.assertTrue(value['present'])

    def test_sql_uncertainty_preserves_table_without_adopting_unknown_creation(self):
        value = state()
        value['fail_sql'] = ['CREATE TABLE']
        value, report = self.execute(value)
        self.assertEqual(report['result'], 'failed')
        self.assertEqual(report['cleanup']['deferred'], ['MUTATION_OUTCOME_UNKNOWN'])
        self.assertFalse(report['table_created'])
        self.assertNotIn('DROP', value['mutations'])

    def test_bad_rows_fail_but_known_owned_table_is_cleaned_after_drain(self):
        value = state()
        value['wrong_rows'] = True
        value, report = self.execute(value)
        self.assertEqual(report['result'], 'failed')
        self.assertEqual(value['mutations'], ['CREATE','DROP'])
        self.assertTrue(report['cleanup']['verified'])

    def test_data_file_change_is_not_called_metadata_only(self):
        value = state()
        value['file_changed'] = True
        _, report = self.execute(value)
        self.assertEqual(report['failure_code'], 'REF_UPDATE_CHANGED_DATA_FILES')
        self.assertNotIn('named_refs_verified', report)

    def test_rest_trino_reference_disagreement_is_not_success(self):
        value = state()
        value['wrong_trino_ref'] = True
        _, report = self.execute(value)
        self.assertEqual(report['failure_code'], 'TRINO_REFS_DIFFER')

    def test_conflict_must_leave_metadata_and_location_unchanged(self):
        value = state()
        value['conflict_mutates'] = True
        _, report = self.execute(value)
        self.assertEqual(report['failure_code'], 'REJECTED_CAS_CHANGED_METADATA')
        self.assertFalse(report['cleanup']['verified'])

    def test_unverified_drain_defers_drop(self):
        value, report = self.execute(drain=False)
        self.assertEqual(report['cleanup']['deferred'], ['QUERY_DRAIN_UNVERIFIED'])
        self.assertEqual(report['result'], 'failed')
        self.assertNotIn('DROP', value['mutations'])
        self.assertTrue(value['present'])

    def test_fixture_drift_is_a_failed_final_verification(self):
        value = state()
        value['fixture_changed'] = True
        _, report = self.execute(value)
        self.assertEqual(report['result'], 'failed')
        self.assertTrue(report['cleanup']['verified'])
        self.assertEqual(report['verification_error'], 'FINAL_READBACK_OR_CLEANUP_UNVERIFIED')

    def test_interruption_retains_pending_intent_and_rethrows_after_readback(self):
        value = state()
        value['cancel_create'] = True
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with patch.object(R.SB, 'drain_queries', return_value={'verified': True}), self.assertRaises(R.SESSION.SessionInterrupted):
                R.run_experiment(Trino(value,output), Trino(value,output,control=True), Refs(value,output), output,TOKEN)
            report = json.loads((output/'summary.json').read_bytes())
        self.assertEqual(report['result'], 'interrupted')
        self.assertEqual(report['cleanup']['deferred'], ['MUTATION_OUTCOME_UNKNOWN'])
        self.assertEqual(value['mutations'], ['CREATE'])
        self.assertEqual(len(report['uncertain_operations']), 1)

    def test_service_guard_rejection_prevents_create(self):
        guard = Mock(side_effect=R.RefsError('POLARIS_SERVICE_BINDING_CHANGED'))
        value, report = self.execute(guard=guard)
        self.assertEqual(value['mutations'], [])
        self.assertEqual(report['failure_code'], 'POLARIS_SERVICE_BINDING_CHANGED')

    def test_post_create_journal_failure_never_claims_cleanup_success(self):
        actual_save = R.save
        fired = False
        def fail_once(path, receipt):
            nonlocal fired
            steps = receipt.get('steps', [])
            if not fired and steps and steps[-1]['name'] == 'create_owned_table' and steps[-1]['outcome'] == 'confirmed':
                fired = True
                raise OSError('private filesystem error')
            actual_save(path, receipt)
        with patch.object(R, 'save', side_effect=fail_once):
            value, report = self.execute()
        self.assertTrue(fired)
        self.assertTrue(value['present'])
        self.assertTrue(report['creation_attempted'])
        self.assertEqual(report['cleanup']['deferred'], ['ORIGINAL_TABLE_UUID_UNKNOWN'])
        self.assertFalse(report['cleanup']['verified'])
        self.assertEqual(value['mutations'], ['CREATE'])

    def test_failed_prewrite_intent_never_submits_create(self):
        actual_save = R.save
        fired = False
        def fail_once(path, receipt):
            nonlocal fired
            steps = receipt.get('steps', [])
            if not fired and steps and steps[-1]['name'] == 'create_owned_table' and steps[-1]['outcome'] == 'pending':
                fired = True
                raise OSError('private filesystem error')
            actual_save(path, receipt)
        with patch.object(R,'save',side_effect=fail_once):
            value, report = self.execute()
        self.assertTrue(fired)
        self.assertFalse(report['creation_attempted'])
        self.assertEqual(value['mutations'], [])
        self.assertEqual(report['result'], 'failed')

    def test_uuid_or_marker_drift_before_cleanup_never_drops(self):
        for field, target in [('uuid',OTHER_UUID), ('marker','someone-else')]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                output, value = Path(directory), state()
                worker, control, refs = Trino(value,output), Trino(value,output,control=True), Refs(value,output)
                def drain(*args, **kwargs):
                    value[field] = target
                    return {'verified': True}
                with patch.object(R.SB, 'drain_queries', side_effect=drain):
                    report = R.run_experiment(worker,control,refs,output,TOKEN)
                self.assertFalse(report['cleanup']['verified'])
                self.assertNotIn('DROP', value['mutations'])


class TunnelAndJournalTests(unittest.TestCase):
    def test_script_import_entry_rejects_arguments_before_runtime(self):
        result = subprocess.run(['rtk','proxy',sys.executable,R.__file__,'--not-allowed'],
                                capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode,0)
        self.assertIn(b'CLI_ARGUMENTS_NOT_ALLOWED',result.stderr)
        self.assertNotIn(b'ModuleNotFoundError',result.stderr)

    def test_service_projection_binds_fixed_namespace_uid_and_spec(self):
        value = {'kind':'Service','metadata':{'name':'polaris','namespace':R.NAMESPACE,'uid':UUID},
                 'spec':{'type':'ClusterIP','ports':[{'port':8181,'targetPort':8181}]}}
        with patch.object(R,'command',return_value=json.dumps(value).encode()):
            expected = R.service_identity()
        self.assertEqual(expected['uid'],UUID)
        for change in ('namespace','port'):
            bad = copy.deepcopy(value)
            if change == 'namespace': bad['metadata']['namespace']='default'
            else: bad['spec']['ports'][0]['port']=80
            with patch.object(R,'command',return_value=json.dumps(bad).encode()), self.assertRaises(R.RefsError):
                R.service_identity()

    def test_existing_loopback_listener_is_not_reused_or_killed(self):
        with patch.object(R.SESSION,'check_context'), patch.object(R,'service_identity'), \
                patch.object(R.socket,'socket') as factory, patch.object(R.subprocess,'Popen') as process:
            factory.return_value.__enter__.return_value.bind.side_effect=OSError()
            with self.assertRaises(R.RefsError):
                with R.polaris_tunnel(lambda value: None): pass
        process.assert_not_called()

    def test_atomic_evidence_refuses_staging_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)
            protected=output/'protected'
            protected.write_text('keep')
            (output/'summary.json.new').symlink_to(protected)
            with self.assertRaises(FileExistsError): R.save(output/'summary.json',{'new':True})
            self.assertEqual(protected.read_text(),'keep')

    def test_forward_scope_change_stops_only_owned_process_group(self):
        original = {'name':'polaris','namespace':R.NAMESPACE,'uid':UUID,'spec_sha256':'a'*64}
        changed = {**original,'uid':OTHER_UUID}
        process = Mock()
        process.poll.return_value = None
        process.stdout.read1.return_value = b'Forwarding from 127.0.0.1:18181 -> 8181\n'
        process.stdout.read.return_value = b''
        journal = []
        with patch.object(R.SESSION,'check_context'), \
                patch.object(R,'service_identity',side_effect=[original,original,changed]), \
                patch.object(R.socket,'socket'), patch.object(R.subprocess,'Popen',return_value=process) as popen, \
                patch.object(R.selectors,'DefaultSelector') as selector, \
                patch.object(R.SESSION,'stop_group') as stop:
            selector.return_value.select.return_value = [True]
            with self.assertRaises(R.RefsError):
                with R.polaris_tunnel(journal.append):
                    pass
        self.assertEqual([row['state'] for row in journal],['pending','ready','failed'])
        stop.assert_called_once_with(process,R.signal.SIGTERM,5)
        self.assertTrue(popen.call_args.kwargs['start_new_session'])
        self.assertIn('127.0.0.1',popen.call_args.args[0])

    def test_forward_intent_failure_starts_no_process(self):
        with patch.object(R.SESSION,'check_context'), patch.object(R,'service_identity'), \
                patch.object(R.socket,'socket'), patch.object(R.subprocess,'Popen') as process:
            with self.assertRaises(OSError):
                with R.polaris_tunnel(Mock(side_effect=OSError())):
                    pass
        process.assert_not_called()


if __name__ == '__main__':
    unittest.main()
