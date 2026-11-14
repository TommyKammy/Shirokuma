"""Failed atomic ingest uses the catalog unregister ledger, never SQL DROP."""
from dataclasses import asdict, replace
from contextlib import contextmanager
import json
import unittest
from unittest.mock import patch
import uuid

import test_manual_ingest as adapter

ds, dm, manual = adapter.ds, adapter.dm, adapter.manual
control_store, QueryError = adapter.control_store, adapter.QueryError


class FailedIngestCleanupTests(unittest.TestCase):
    setUp = adapter.ManualIngestTests.setUp
    drop_api = adapter.ManualIngestTests.drop_api
    transport = adapter.ManualIngestTests.transport
    run_ingest = adapter.ManualIngestTests.run_ingest
    sql = adapter.ManualIngestTests.sql

    def ready(self, key='cleanup'):
        execution = self.fixture.begin(key)
        stage = execution.create_stage(self.fixture.schemas[self.fixture.databases[0]]['sandbox'],
                                       self.columns, step='stage')
        receipt = dict(operation_id=execution.id, atomic=True, cleanup='pending',
                       error='PRIMARY_FAILURE', committed_rows=0, committed_batches=0,
                       loaded_batches=3, accepted_rows=11)
        manual._persist(execution, receipt)
        return execution, stage, receipt

    def current(self, ref):
        with control_store.connect() as db:
            return db.execute('SELECT current FROM object_bindings WHERE id=?', (ref.object_id,)).fetchone()[0]

    def fail_load(self, accepted=1, unknown=False):
        count = 0
        def fault(sql):
            nonlocal count
            if sql.startswith('INSERT INTO') and '."sandbox".' in sql:
                count += 1
                if count == accepted + 1:
                    if unknown:
                        raise QueryError('QUERY_BACKEND_FAILED', 502)
                    return dict(id='load_failure_' + str(uuid.uuid4()), error=dict(message='primary load failure'))
        self.fault = fault

    def test_partial_loads_across_modes_databases_and_targets(self):
        for index, (mode, accepted) in enumerate((('replace', 1), ('append', 2), ('upsert', 3))):
            with self.subTest(mode=mode):
                database = self.fixture.databases[index]
                self.columns = (('event_id_' + str(index), 'BIGINT'), ('description_' + str(index), 'VARCHAR'))
                target = self.fixture.existing('customer_events_' + str(index), database)
                with control_store.connect() as db:
                    original = dm.get_object(db, target.object_id)
                contents = dict(self.fixture.objects)
                self.fail_load(accepted)
                start = len(self.sql())
                options = dict(target=target, context=self.fixture.context(database), atomic_append=True,
                    key_columns=[self.columns[0][0]], batch_size=1,
                    content=','.join(n for n, _ in self.columns) + '\n11,alpha\n22,beta\n33,gamma\n44,delta\n55,end\n')
                result = self.run_ingest('partial-' + str(index), mode, **options)
                receipt = result['result']
                self.assertEqual((result['state'], result['committed']), ('FAILED', False))
                self.assertEqual((receipt['committed_rows'], receipt['committed_batches'],
                                  receipt['loaded_batches'], receipt['accepted_rows']), (0, 0, accepted, 5))
                failed = [q for q in result['queries'] if q['state'] == 'FAILED']
                self.assertEqual(len(failed), 1)
                self.assertEqual(receipt['error'], 'DATAOPS_STEP_FAILED')
                self.assertEqual((receipt['cleanup'], receipt['cleanup_phase'], receipt['cleanup_ack']),
                                 ('finished', 'ACKNOWLEDGED', 204))
                with control_store.connect() as db:
                    self.assertEqual(dm.get_object(db, target.object_id), original)
                    stage = db.execute('''SELECT b.current FROM object_bindings b JOIN dataops_owned_objects o
                        ON b.id=o.object_id WHERE o.operation_id=?''', (result['id'],)).fetchone()
                    self.assertEqual(stage['current'], 0)
                for name, metadata in contents.items():
                    self.assertEqual(self.fixture.objects[name], metadata)
                count = len(self.unregister_calls)
                replay = self.run_ingest('partial-' + str(index), mode, **options)
                self.assertEqual(replay['result'], receipt)
                self.assertEqual(len(self.unregister_calls), count)
                self.assertFalse(any(s.startswith(('CREATE OR REPLACE', 'MERGE INTO')) or
                    (s.startswith('INSERT INTO') and '."raw".' in s) for s in self.sql()[start:]))
        self.assertFalse(any(s.startswith('DROP TABLE') for s in self.sql()))
        self.assertFalse(any(q['action'] == 'cleanup' for q in result['queries']))

    def test_post_load_schema_failure_needs_no_failed_child(self):
        self.schema_change = True
        result = self.run_ingest(mode='replace', batch_size=1)
        self.assertEqual(result['state'], 'FAILED')
        self.assertEqual(result['result']['loaded_batches'], 3)
        self.assertEqual(result['result']['error'], 'TARGET_SCHEMA_CHANGED')
        self.assertEqual(result['result']['cleanup_phase'], 'ACKNOWLEDGED')
        self.assertTrue(all(q['state'] == 'FINISHED' for q in result['queries']))

    def test_unknown_load_never_enters_cleanup(self):
        self.fail_load(unknown=True)
        result = self.run_ingest(mode='replace', batch_size=1)
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertEqual(self.unregister_calls, [])
        self.assertNotIn('cleanup_phase', result['result'])
        self.assertFalse(any(s.startswith('DROP TABLE') for s in self.sql()))

    def test_rejection_loss_invalid_ack_and_ack_ledger_failure(self):
        for index, outcome in enumerate(('rejected', 'lost', 'invalid', 'ledger')):
            with self.subTest(outcome=outcome):
                execution, stage, receipt = self.ready(str(index))
                self.unregister_error = (ds.inventory.TableDropTransportError(outcome == 'lost')
                                         if outcome in ('rejected', 'lost') else None)
                self.unregister_ack = 204.0 if outcome == 'invalid' else 204
                if outcome == 'ledger':
                    with control_store.connect() as db:
                        db.execute("""CREATE TRIGGER reject_ack BEFORE UPDATE OF result ON dataops_operations
                            WHEN json_extract(NEW.result, '$.cleanup_phase') = 'ACKNOWLEDGED'
                            BEGIN SELECT RAISE(ABORT, 'ledger failure'); END""")
                execution.cleanup_failed_ingest_stage(stage)
                result = execution._inspect()['result']
                phase = 'REJECTED' if outcome == 'rejected' else 'SENT'
                self.assertEqual(result['cleanup_phase'], phase)
                self.assertEqual(self.current(stage), 1)
                for key in ('error', 'loaded_batches', 'accepted_rows', 'committed_rows', 'committed_batches'):
                    self.assertEqual(result[key], receipt[key])
                calls = len(self.unregister_calls)
                with self.assertRaises(QueryError):
                    execution.cleanup_failed_ingest_stage(stage)
                self.assertEqual(len(self.unregister_calls), calls)
                execution.fail()

    def test_receipt_preconditions(self):
        execution, stage, receipt = self.ready()
        for change in (dict(operation_id='foreign'), dict(atomic=False), dict(atomic=1),
                       dict(cleanup='finished'), dict(error=''), dict(cleanup_phase='SENT'),
                       dict(cleanup_mode='CATALOG_UNREGISTER_KEEP_DATA')):
            with self.subTest(change=change):
                manual._persist(execution, dict(receipt, **change))
                with self.assertRaises(QueryError):
                    execution.cleanup_failed_ingest_stage(stage)
        self.assertEqual(self.unregister_calls, [])
        manual._persist(execution, receipt)
        with control_store.connect() as db:
            db.execute("UPDATE dataops_operations SET route='other' WHERE id=?", (execution.id,))
        with self.assertRaises(QueryError):
            execution.cleanup_failed_ingest_stage(stage)
        with control_store.connect() as db:
            db.execute("UPDATE dataops_operations SET route='manual-ingest',committed=1 WHERE id=?", (execution.id,))
        with self.assertRaises(QueryError):
            execution.cleanup_failed_ingest_stage(stage)
        execution.fail()

    def test_owned_stage_recorded_as_target_is_never_cleaned(self):
        for committed in (False, True):
            for forged_location in (False, True):
                with self.subTest(committed=committed, forged_location=forged_location):
                    execution, stage, receipt = self.ready(f'target-{committed}-{forged_location}')
                    target = asdict(stage)
                    if forged_location:
                        target.update(database_id=self.fixture.databases[1],
                                      schema_id=self.fixture.schemas[self.fixture.databases[1]]['raw'])
                    receipt = dict(receipt, target=target)
                    manual._persist(execution, receipt)
                    if committed:
                        execution.record_commit(receipt)
                    cleanup = (execution.cleanup_committed_ingest_stage if committed
                               else execution.cleanup_failed_ingest_stage)
                    with patch.object(ds.inventory, 'table_drop_api') as drop_api, \
                            patch.object(execution, '_catalog') as catalog:
                        cleanup(stage)
                        result = execution._inspect()
                        self.assertEqual(result['cleanup_error'], 'OBJECT_NOT_OWNED')
                        self.assertEqual(result['result'], dict(receipt, cleanup='failed',
                            cleanup_phase='REJECTED', cleanup_mode='CATALOG_UNREGISTER_KEEP_DATA'))
                        self.assertEqual(self.current(stage), 1)
                        with self.assertRaises(QueryError) as rejected:
                            cleanup(stage)
                        self.assertEqual(rejected.exception.code, 'OPERATION_OBSERVATION_ONLY')
                        self.assertEqual(execution._inspect()['result'], result['result'])
                        drop_api.assert_not_called()
                        catalog.assert_not_called()
                    self.assertEqual(self.unregister_calls, [])
                    execution.fail()

    def test_failed_adapter_preserves_rejected_and_sent_receipts(self):
        for outcome in ('rejected', 'lost', 'invalid', 'ledger'):
            with self.subTest(outcome=outcome):
                self.fail_load(accepted=2)
                self.unregister_error = (ds.inventory.TableDropTransportError(outcome == 'lost')
                                         if outcome in ('rejected', 'lost') else None)
                self.unregister_ack = '204' if outcome == 'invalid' else 204
                if outcome == 'ledger':
                    with control_store.connect() as db:
                        db.execute("""CREATE TRIGGER reject_adapter_ack BEFORE UPDATE OF result ON dataops_operations
                            WHEN json_extract(NEW.result, '$.cleanup_phase') = 'ACKNOWLEDGED'
                            BEGIN SELECT RAISE(ABORT, 'ack ledger failure'); END""")
                options = dict(target=self.fixture.existing('target_' + outcome), batch_size=1)
                result = self.run_ingest(outcome, mode='replace', **options)
                receipt = result['result']
                self.assertEqual((result['state'], result['committed']), ('FAILED', False))
                self.assertEqual(receipt['cleanup_phase'], 'REJECTED' if outcome == 'rejected' else 'SENT')
                self.assertEqual(receipt['error'], 'DATAOPS_STEP_FAILED')
                self.assertEqual((receipt['loaded_batches'], receipt['accepted_rows'],
                                  receipt['committed_rows'], receipt['committed_batches']), (2, 3, 0, 0))
                self.assertNotIn('cleanup_ack', receipt)
                with control_store.connect() as db:
                    current = db.execute('''SELECT b.current FROM object_bindings b
                        JOIN dataops_owned_objects o ON b.id=o.object_id WHERE o.operation_id=?''',
                        (result['id'],)).fetchone()[0]
                self.assertEqual(current, 1)
                calls = len(self.unregister_calls)
                self.assertEqual(self.run_ingest(outcome, mode='replace', **options)['result'], receipt)
                self.assertEqual(len(self.unregister_calls), calls)
                self.assertFalse(any(q['action'] == 'cleanup' for q in result['queries']))
        self.assertFalse(any(s.startswith('DROP TABLE') for s in self.sql()))

    def test_ownership_resource_and_uuid_guards(self):
        cases = ('unowned', 'target', 'foreign', 'backup', 'stale', 'incarnation', 'uuid',
                 'invalid-uuid', 'crossdatabase', 'schema', 'kind', 'sandbox-purpose')
        for case in cases:
            with self.subTest(case=case):
                execution, stage, _ = self.ready(case)
                candidate = stage
                with control_store.connect() as db:
                    obj = dm.get_object(db, stage.object_id)
                    if case == 'unowned':
                        db.execute('DELETE FROM dataops_owned_objects WHERE object_id=?', (stage.object_id,))
                    elif case == 'backup':
                        db.execute("UPDATE dataops_owned_objects SET purpose='backup' WHERE object_id=?", (stage.object_id,))
                    elif case == 'stale':
                        db.execute('UPDATE object_bindings SET current=0 WHERE id=?', (stage.object_id,))
                    elif case == 'kind':
                        db.execute("UPDATE object_bindings SET kind='VIEW' WHERE id=?", (stage.object_id,))
                    elif case == 'invalid-uuid':
                        db.execute("UPDATE object_bindings SET backend_identity='invalid' WHERE id=?", (stage.object_id,))
                    elif case == 'sandbox-purpose':
                        db.execute("UPDATE schemas SET purpose='raw' WHERE id=?", (stage.schema_id,))
                if case == 'target':
                    candidate = self.fixture.existing('user_table', schema='sandbox')
                elif case == 'foreign':
                    foreign = self.fixture.begin('foreign-owner')
                    with control_store.connect() as db:
                        db.execute('UPDATE dataops_owned_objects SET operation_id=? WHERE object_id=?',
                                   (foreign.id, stage.object_id))
                    foreign.fail()
                elif case == 'uuid':
                    self.fixture.objects[('sales', 'sandbox', obj.physical_name, 'tables')]['table-uuid'] = str(uuid.uuid4())
                elif case == 'crossdatabase':
                    candidate = replace(stage, database_id=self.fixture.databases[1])
                elif case == 'schema':
                    candidate = replace(stage, schema_id=self.fixture.schemas[self.fixture.databases[0]]['raw'])
                elif case == 'incarnation':
                    candidate = replace(stage, incarnation=stage.incarnation + 1)
                execution.cleanup_failed_ingest_stage(candidate)
                self.assertEqual(execution._inspect()['result']['cleanup_phase'], 'REJECTED')
                self.assertEqual(self.unregister_calls, [])
                if case != 'stale':
                    self.assertEqual(self.current(stage), 1)
                execution.fail()

    def metadata_guard(self, case):
        execution, stage, _ = self.ready(case)
        calls = []
        @contextmanager
        def api(database, binding):
            with self.fixture.catalog(database, binding) as reader:
                def read(path):
                    metadata = reader(path)
                    with control_store.connect() as db:
                        if case == 'ownership':
                            db.execute("UPDATE dataops_owned_objects SET purpose='backup' WHERE object_id=?",
                                       (stage.object_id,))
                        elif case == 'grant':
                            db.execute("UPDATE grants SET revision=revision+1 WHERE principal_id='alice'")
                        else:
                            db.execute('UPDATE catalog_bindings SET revision=revision+1 WHERE database_id=?',
                                       (stage.database_id,))
                    return metadata
                yield read, lambda *_: calls.append('unregister')
        with patch.object(ds.inventory, 'table_drop_api', api):
            execution.cleanup_failed_ingest_stage(stage)
        self.assertEqual(calls, [])
        self.assertEqual(self.current(stage), 1)
        self.assertEqual(json.loads(execution._row()['result'])['cleanup_phase'], 'REJECTED')

    def test_metadata_read_rechecks_ownership(self):
        self.metadata_guard('ownership')

    def test_metadata_read_rechecks_grant(self):
        self.metadata_guard('grant')

    def test_metadata_read_rechecks_catalog(self):
        self.metadata_guard('catalog')

    def test_failure_receipt_write_failure_prevents_cleanup(self):
        original = manual._persist
        def persist(execution, receipt):
            if receipt.get('error'):
                raise OSError('failure receipt unavailable')
            return original(execution, receipt)
        self.fail_load()
        with patch.object(manual, '_persist', side_effect=persist):
            result = self.run_ingest(mode='replace', batch_size=1)
        self.assertFalse(result['committed'])
        self.assertEqual(result['result']['loaded_batches'], 1)
        self.assertEqual(result['result']['committed_rows'], 0)
        self.assertEqual(self.unregister_calls, [])
        self.assertNotIn('cleanup_phase', result['result'])

    def test_actor_replay_and_live_owner_guards(self):
        execution, stage, _ = self.ready()
        other = replace(self.principal, principal_id='bob')
        for denied in (ds.Execution(other, execution.id), ds.Execution(self.principal, execution.id, replayed=True)):
            with self.assertRaises(QueryError):
                denied.cleanup_failed_ingest_stage(stage)
        live = ds._live.pop(execution.id)
        try:
            with self.assertRaises(QueryError):
                execution.cleanup_failed_ingest_stage(stage)
        finally:
            ds._live[execution.id] = live
        self.assertEqual(self.unregister_calls, [])
        execution.fail()

    def test_unresolved_query_or_admission_prevents_send(self):
        execution, stage, _ = self.ready()
        query = execution._inspect()['queries'][0]['id']
        for table in ('query_records', 'warehouse_admissions'):
            field = 'id' if table == 'query_records' else 'subject_id'
            for state in (('UNKNOWN', 'RUNNING', 'QUEUED') if table == 'query_records' else ('UNKNOWN', 'ACTIVE')):
                with self.subTest(table=table, state=state):
                    with control_store.connect() as db:
                        db.execute(f'UPDATE {table} SET state=? WHERE {field}=?', (state, query))
                    with self.assertRaises(QueryError):
                        execution.cleanup_failed_ingest_stage(stage)
                    self.assertNotIn('cleanup_phase', json.loads(execution._row()['result']))
                    with control_store.connect() as db:
                        db.execute(f"UPDATE {table} SET state='FINISHED' WHERE {field}=?", (query,))
        self.assertEqual(self.unregister_calls, [])
        execution.cleanup_failed_ingest_stage(stage)
        self.assertEqual(self.current(stage), 0)
        execution.fail()


if __name__ == '__main__':
    unittest.main()
