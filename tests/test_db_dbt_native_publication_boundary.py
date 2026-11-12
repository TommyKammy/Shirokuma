"""Characterize admitted native publication through the existing Execution actions."""
import json
import unittest
from unittest.mock import patch

import test_db_dbt as fixtures

cs = fixtures.control_store
ds = fixtures.ds
dbt = fixtures.dbt
QueryError = fixtures.QueryError


class NativePublicationBoundaryTests(unittest.TestCase):
    setUp = fixtures.NativePublicationTests.setUp
    clear_live = fixtures.NativePublicationTests.clear_live
    quality_run = fixtures.NativePublicationTests.quality_run
    quality_transport = fixtures.NativePublicationTests.quality_transport
    mutate_plan = fixtures.NativePublicationTests.mutate_plan
    candidate = fixtures.NativePublicationTests.candidate
    publisher = fixtures.NativePublicationTests.publisher
    preview = fixtures.NativePublicationTests.preview
    accept = fixtures.NativePublicationTests.accept
    finish = fixtures.NativePublicationTests.finish
    record = fixtures.NativePublicationTests.record
    mutate = fixtures.NativePublicationTests.mutate
    code = fixtures.NativePublicationTests.code

    def operation(self, ident):
        with cs.connect() as db:
            return dict(db.execute('SELECT * FROM dataops_operations WHERE id=?', (ident,)).fetchone())

    def test_pending_is_durable_before_source_failure_and_no_sql(self):
        run = self.candidate()
        ack = self.accept(run, self.preview(run)['expected_target'])
        before = len(self.w.posts)
        action, resolve = ds.Execution._dbt_native_audit, ds.Execution._ref
        pending = []

        def audit(execution):
            def source(current, *args, **kwargs):
                saved = json.loads(self.operation(current.id)['result'] or '{}') or {}
                if 'audit_id' in saved:
                    self.assertEqual(set(saved), {'origin', 'audit_id', 'intent'})
                    self.assertEqual(saved['intent'], self.record(ack['id'])['_native_intent'])
                    self.assertEqual(saved['origin'], 'native_table')
                    pending.append(saved)
                    raise QueryError('DBT_CANDIDATE_CHANGED', 409)
                return resolve(current, *args, **kwargs)
            with patch.object(ds.Execution, '_ref', source):
                return action(execution)

        with patch.object(ds.Execution, '_dbt_native_audit', audit):
            result = self.finish(ack)
        self.assertEqual(result['error_code'], 'DBT_CANDIDATE_CHANGED')
        self.assertEqual(len(pending), 1)
        self.assertEqual(len(self.w.posts), before)
        self.assertEqual(json.loads(self.operation(result['operations'][0]['id'])['result']), pending[0])

    def test_audit_strict_shape_type_and_zero_normal_receipt(self):
        run = self.candidate()
        ack = self.accept(run, self.preview(run)['expected_target'])
        action = ds.Execution._dbt_native_audit
        receipts = []

        def audit(execution):
            for rows in ([[True]], [[False]], [[-1]], [['1']], [], [[1, 2]], [[1], [2]]):
                with self.subTest(rows=rows), patch.object(execution, '_execute', return_value={'data': rows}):
                    self.code('INVALID_AUDIT_RESULT', action, execution)
            with patch.object(execution, '_execute', return_value={'data': [[0]], 'id': 'zero-query'}):
                receipt = action(execution)
            saved = json.loads(self.operation(execution.id)['result'])
            self.assertEqual(receipt, dict(audit_id=saved['audit_id'], audit=dict(
                id=saved['audit_id'], origin=saved['origin'], intent=saved['intent'],
                publication_id=ack['id'], operation_id=execution.id, query_id='zero-query',
                count=0, passed=False)))
            receipts.append(receipt)
            return receipt

        before = len(self.w.posts)
        with patch.object(ds.Execution, '_dbt_native_audit', audit):
            self.finish(ack)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(len(self.w.posts), before)

    def check_publication_sql(self, replace):
        if replace:
            self.w.existing('arbitrary_result', schema='marts')
            with patch.object(dbt, '_start'), patch.object(fixtures.compiler, 'compile_project', return_value=self.artifacts[0]):
                plan = dbt.submit_plan(self.principal, self.context, key='replace-plan', project_id='general',
                    source_schema='raw', target_schema='marts', staging_materialization=None)
                dbt._execute(plan['id'])
            self.plan_id = plan['id']
        run = self.candidate()
        ack = self.accept(run, self.preview(run)['expected_target'])
        execute, claim, resolve = ds.Execution._execute, ds.Execution.wap_claim, ds.Execution._ref
        events = []
        sent = []

        def claimed(execution, target):
            locator = claim(execution, target)
            events.append('claim')
            self.assertNotIn('intent', (json.loads(self.operation(execution.id)['result'] or '{}') or {}))
            return locator

        def source(execution, ref, **kwargs):
            name = resolve(execution, ref, **kwargs)
            if events == ['claim'] and not kwargs and ref == fixtures.dm.ResourceRef(**run['native_receipt']['candidate']['ref']):
                events.append('source')
            return name

        def send(execution, action, sql, step, **kwargs):
            if action == 'wap_publish':
                events.append('send')
                publication = self.record(ack['id'])
                expected = dict(origin=publication['origin'], publication_id=ack['id'],
                    audit_id=publication['audit_id'], operation_id=execution.id, intent=publication['_native_intent'])
                self.assertEqual(kwargs, {'commit_result': expected})
                self.assertEqual(self.operation(execution.id)['result'], ds._json(expected))
                candidate = expected['intent']['candidate']
                destination = publication['expected_target']['destination']
                target = fixtures.dm.ResourceRef(**destination) if replace else fixtures.dm.DestinationLocator(**destination)
                name = resolve(execution, target, write=True, admin=True) if replace else execution._locator(target, admin=True)
                source_name = resolve(execution, fixtures.dm.ResourceRef(**candidate['ref']))
                columns = ', '.join('"' + c['name'].replace('"', '""') + '"' for c in candidate['columns'])
                self.assertEqual(sql, ('CREATE OR REPLACE TABLE ' if replace else 'CREATE TABLE ') + name
                    + " WITH (format='PARQUET') AS SELECT " + columns + ' FROM ' + source_name
                    + ' FOR VERSION AS OF ' + candidate['snapshot'])
                sent.append(expected)
            return execute(execution, action, sql, step, **kwargs)

        with patch.object(ds.Execution, 'wap_claim', claimed), patch.object(ds.Execution, '_ref', source), patch.object(ds.Execution, '_execute', send):
            result = self.finish(ack)
        self.assertEqual(result['state'], 'PUBLISHED', result)
        self.assertEqual(events, ['claim', 'source', 'send'])
        self.assertEqual(len(sent), 1)
        receipt = result['native_publication']
        self.assertEqual({k: receipt[k] for k in sent[0]}, sent[0])
        self.assertEqual(receipt['actor'], self.publisher()[0].principal_id)

    def test_create_sql_claim_intent_send_and_receipt(self):
        self.check_publication_sql(False)

    def test_replace_sql_claim_intent_send_and_receipt(self):
        self.check_publication_sql(True)

    def test_late_context_failure_precedes_page_id_and_retains_commit(self):
        run = self.candidate()
        ack = self.accept(run, self.preview(run)['expected_target'])
        action, execute = ds.Execution._dbt_native_publish, ds.Execution._execute
        captured = []
        accesses = []
        failures = []

        class Page(dict):
            def __getitem__(self, key):
                if key == 'id':
                    accesses.append(key)
                    raise AssertionError('page id read before context')
                return super().__getitem__(key)

        def publish(execution):
            measure = execution.wap_measure
            late = []
            context = execution._context

            def measured(ref):
                result = measure(ref)
                if captured:
                    late.append(True)
                return result

            def checked_context():
                if late:
                    raise QueryError('SQL_PERMISSION_DENIED', 403)
                return context()

            def send(*args, **kwargs):
                page = execute(execution, *args, **kwargs)
                captured.append((execution.id, kwargs['commit_result']))
                return Page(page)

            with patch.object(execution, '_execute', side_effect=send), patch.object(execution, 'wap_measure', side_effect=measured), patch.object(execution, '_context', side_effect=checked_context):
                try:
                    return action(execution)
                except QueryError as error:
                    failures.append(error.code)
                    raise

        with patch.object(ds.Execution, '_dbt_native_publish', publish):
            result = self.finish(ack)
        self.assertEqual(failures, ['SQL_PERMISSION_DENIED'])
        self.assertTrue(result['committed'])
        self.assertEqual(accesses, [])
        self.assertEqual(len(captured), 1)
        child = self.operation(captured[0][0])
        self.assertTrue(child['committed'])
        self.assertEqual(child['result'], ds._json(captured[0][1]))
        self.assertNotIn('publication', json.loads(child['result']))


if __name__ == '__main__':
    unittest.main()
