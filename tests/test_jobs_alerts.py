"""SQL alert shape, lifecycle, ownership and atomic finalization regressions."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import alerts_resident as alerts
import jobs_resident as jobs
import request_auth


class JobsAlertsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = patch.dict(os.environ, SHIROKUMA_STATE_DIR=temporary.name,
                                 SHIROKUMA_AUTH_MODE='local', SHIROKUMA_AUTH_FILE='')
        environment.start()
        self.addCleanup(environment.stop)
        telemetry = patch.object(jobs.wr, 'telemetry', return_value='skipped')
        telemetry.start()
        self.addCleanup(telemetry.stop)

    def definition(self, **overrides):
        return dict(name='alert_job', description='Local synthetic checks', parameters={},
                    steps=[dict(action='alert', sql='SELECT TRUE', message='Investigate the synthetic condition')], **overrides)

    def save(self, definition=None, actor='local', role='admin'):
        return jobs.save_job(definition or self.definition(), actor, role)

    def result(self, value):
        return dict(result='verified', rows=[[value]], columns=[{'name': 'condition', 'type': 'boolean'}], truncated=False)

    def terminal(self, run_id, actor='local', role='admin'):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            run = jobs.get_run(run_id, actor, role)
            if run['status'] not in jobs.ACTIVE:
                for thread in threading.enumerate():
                    if thread.name == 'job-' + run_id[:8]:
                        thread.join(2)
                return run
            time.sleep(.01)
        self.fail('alert job did not finish')

    def execute(self, value=True, arguments=None, actor='local', role='admin', report=None, error=None):
        with patch.object(jobs.wr, 'query', return_value=self.result(value) if report is None else report,
                          side_effect=error):
            return self.terminal(jobs.submit_run('alert_job', arguments or {}, actor, role)['id'], actor, role)

    def test_true_deduplicates_ack_is_independent_false_resolves_and_retrigger_opens_new(self):
        self.save()
        first = self.execute()
        first_id = first['steps'][0]['summary']['alert_id']
        self.assertEqual(first['steps'][0]['summary'], dict(condition_met=True, alert_id=first_id, alert_status='opened'))
        again = self.execute()
        self.assertEqual(again['steps'][0]['summary']['alert_id'], first_id)
        self.assertEqual(again['steps'][0]['summary']['alert_status'], 'ongoing')
        row = alerts.list_alerts('local', 'admin')['alerts'][0]
        self.assertEqual(row['occurrences'], 2)
        self.assertIsNone(row['resolved_at'])
        acknowledged = alerts.acknowledge(first_id, 'local', 'admin')
        self.assertIsNotNone(acknowledged['acknowledged_at'])
        self.assertIsNone(acknowledged['resolved_at'])
        self.execute()
        listing = alerts.list_alerts('local', 'admin')
        self.assertEqual(len(listing['alerts']), 1)
        self.assertEqual(listing['unacknowledged_count'], 0)
        self.assertEqual(listing['alerts'][0]['occurrences'], 3)
        cleared = self.execute(False)
        self.assertEqual(cleared['steps'][0]['summary']['alert_status'], 'resolved')
        row = alerts.list_alerts('local', 'admin')['alerts'][0]
        self.assertEqual(row['resolution'], 'condition_cleared')
        self.assertIsNotNone(row['resolved_at'])
        self.assertEqual(row['acknowledged_at'], acknowledged['acknowledged_at'])
        self.assertEqual(row['last_run_id'], cleared['id'])
        retrigger = self.execute()
        self.assertNotEqual(retrigger['steps'][0]['summary']['alert_id'], first_id)
        self.assertEqual(alerts.list_alerts('local', 'admin')['unacknowledged_count'], 1)

    def test_initial_false_creates_no_notification_and_result_rows_are_never_saved(self):
        self.save()
        run = self.execute(False)
        self.assertEqual(run['status'], 'success')
        self.assertEqual(run['steps'][0]['summary'], dict(condition_met=False, alert_id=None, alert_status='clear'))
        self.assertEqual(alerts.list_alerts('local', 'admin'), dict(alerts=[], unacknowledged_count=0))
        self.assertNotIn('rows', run['steps'][0]['summary'])
        self.assertNotIn('columns', run['steps'][0]['summary'])

    def test_false_resolution_preserves_last_true_detection_time_and_occurrences(self):
        self.save()
        first_seen = '2026-10-18T00:00:00+00:00'
        last_detected = '2026-10-18T00:01:00+00:00'
        cleared_at = '2026-10-18T00:02:00+00:00'
        with patch.object(jobs, '_now', return_value=first_seen):
            self.execute()
        with patch.object(jobs, '_now', return_value=last_detected):
            self.execute()
        with patch.object(jobs, '_now', return_value=cleared_at):
            cleared = self.execute(False)
        row = alerts.list_alerts('local', 'admin')['alerts'][0]
        self.assertEqual(row['first_seen_at'], first_seen)
        self.assertEqual(row['last_seen_at'], last_detected)
        self.assertEqual(row['resolved_at'], cleared_at)
        self.assertEqual(row['resolution'], 'condition_cleared')
        self.assertEqual(row['occurrences'], 2)
        self.assertEqual(row['last_run_id'], cleared['id'])

    def test_arguments_distinguish_subjects_and_false_only_resolves_same_subject(self):
        definition = self.definition()
        definition['parameters'] = {'id': {'type': 'integer', 'required': True}}
        definition['steps'][0]['sql'] = 'SELECT {{id}} > 0'
        self.save(definition)
        one = self.execute(arguments={'id': 1})['steps'][0]['summary']['alert_id']
        two = self.execute(arguments={'id': 2})['steps'][0]['summary']['alert_id']
        self.assertNotEqual(one, two)
        self.execute(arguments={'id': 1})
        self.execute(False, arguments={'id': 1})
        rows = {row['id']: row for row in alerts.list_alerts('local', 'admin')['alerts']}
        self.assertEqual(rows[one]['occurrences'], 2)
        self.assertIsNotNone(rows[one]['resolved_at'])
        self.assertIsNone(rows[two]['resolved_at'])
        self.assertNotIn('arguments_key', rows[one])
        self.assertNotIn('rule_key', rows[one])

    def test_definition_edit_retires_old_rule_instead_of_claiming_recovery(self):
        definition = self.definition()
        self.save(definition)
        first_id = self.execute()['steps'][0]['summary']['alert_id']
        definition['description'] = 'A description edit leaves the rule unchanged'
        self.save(definition)
        self.assertIsNone(alerts.list_alerts('local', 'admin')['alerts'][0]['resolved_at'])
        definition['steps'][0]['severity'] = 'critical'
        self.save(definition)
        previous = alerts.list_alerts('local', 'admin')['alerts'][0]
        self.assertEqual(previous['resolution'], 'definition_changed')
        self.assertIsNotNone(previous['resolved_at'])
        self.assertIsNone(previous['acknowledged_at'])
        second_id = self.execute()['steps'][0]['summary']['alert_id']
        self.assertNotEqual(first_id, second_id)
        definition['steps'] = [dict(action='sql', sql='SELECT 1', mode='read-only')]
        self.save(definition)
        self.assertTrue(all(row['resolution'] == 'definition_changed' for row in alerts.list_alerts('local', 'admin')['alerts']))

    def test_parameter_declaration_type_changes_retire_even_when_values_match(self):
        definition = self.definition()
        definition['parameters'] = {'day': {'type': 'date', 'required': True}}
        definition['steps'][0]['sql'] = 'SELECT {{day}} IS NOT NULL'
        self.save(definition)
        first_id = self.execute(arguments={'day': '2026-10-18'})['steps'][0]['summary']['alert_id']
        definition['parameters']['day']['type'] = 'string'
        self.save(definition)
        self.assertEqual(alerts.list_alerts('local', 'admin')['alerts'][0]['resolution'], 'definition_changed')
        second_id = self.execute(arguments={'day': '2026-10-18'})['steps'][0]['summary']['alert_id']
        self.assertNotEqual(first_id, second_id)

    def test_old_inflight_definition_cannot_resurrect_retired_notification(self):
        definition = self.definition()
        self.save(definition)
        old_id = self.execute()['steps'][0]['summary']['alert_id']
        entered, release = threading.Event(), threading.Event()
        def query(*args, **kwargs):
            entered.set()
            release.wait(3)
            return self.result(True)
        with patch.object(jobs.wr, 'query', side_effect=query):
            run = jobs.submit_run('alert_job', {}, 'local', 'admin')
            try:
                self.assertTrue(entered.wait(2))
                definition['steps'][0]['message'] = 'New rule message'
                self.save(definition)
            finally:
                release.set()
                completed = self.terminal(run['id'])
        self.assertEqual(completed['steps'][0]['summary']['alert_status'], 'definition_changed')
        self.assertIsNone(completed['steps'][0]['summary']['alert_id'])
        listing = alerts.list_alerts('local', 'admin')['alerts']
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]['id'], old_id)
        self.assertEqual(listing[0]['resolution'], 'definition_changed')
        self.assertEqual(listing[0]['occurrences'], 1)
        self.assertEqual(self.execute()['steps'][0]['summary']['alert_status'], 'opened')

    def test_invalid_shapes_fail_stop_without_resolving_existing_alert(self):
        definition = self.definition()
        definition['steps'].append(dict(action='sql', sql='SELECT 1', mode='read-only'))
        self.save(definition)
        first = self.execute()
        original = alerts.list_alerts('local', 'admin')['alerts'][0]
        invalid = [self.result(None), self.result(1), self.result('true'),
                   {**self.result(True), 'rows': []},
                   {**self.result(True), 'rows': [[True], [True]]},
                   {**self.result(True), 'truncated': True},
                   {**self.result(True), 'rows': [[True, False]]},
                   {**self.result(True), 'columns': [{}, {}]},
                   {**self.result(True), 'columns': []},
                   {**self.result(True), 'result': 'failed'}]
        for report in invalid:
            with self.subTest(report=report):
                run = self.execute(report=report)
                self.assertEqual(run['status'], 'failed')
                self.assertEqual([step['status'] for step in run['steps']], ['failed', 'skipped'])
                row = alerts.list_alerts('local', 'admin')['alerts'][0]
                self.assertIsNone(row['resolved_at'])
                self.assertEqual(row['last_run_id'], first['id'])
                self.assertEqual(row['last_seen_at'], original['last_seen_at'])

    def test_query_and_authorization_failures_do_not_change_notifications(self):
        self.save()
        first = self.execute()
        for error in (jobs.wr.TrinoQueryError('sensitive-row-value', 'TABLE_NOT_FOUND'), PermissionError('denied')):
            run = self.execute(error=error)
            self.assertEqual(run['status'], 'failed')
            row = alerts.list_alerts('local', 'admin')['alerts'][0]
            self.assertIsNone(row['resolved_at'])
            self.assertEqual(row['last_run_id'], first['id'])
            self.assertNotIn('sensitive-row-value', json.dumps(run))

    def test_readonly_transport_failure_is_failed_and_preserves_notification(self):
        definition = self.definition()
        definition['steps'].append(dict(action='sql', sql='SELECT 1', mode='read-only'))
        self.save(definition)
        first = self.execute()
        before = alerts.list_alerts('local', 'admin')['alerts'][0]
        for error in (OSError('sensitive-driver-value'), TimeoutError('sensitive-driver-value')):
            with self.subTest(error=type(error).__name__):
                run = self.execute(error=error)
                self.assertEqual(run['status'], 'failed')
                self.assertEqual([step['status'] for step in run['steps']], ['failed', 'skipped'])
                self.assertEqual(run['error'], 'alert SQL could not be evaluated')
                self.assertNotIn('sensitive-driver-value', json.dumps(run))
                row = alerts.list_alerts('local', 'admin')['alerts'][0]
                self.assertEqual(row['last_run_id'], first['id'])
                self.assertEqual(row['occurrences'], before['occurrences'])
                self.assertIsNone(row['resolved_at'])

    def test_notification_and_step_success_share_one_transaction(self):
        self.save()
        self.execute()
        before = alerts.list_alerts('local', 'admin')['alerts'][0]
        original = alerts.record
        def fail_after_write(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('synthetic finalize failure')
        with patch.object(alerts, 'record', side_effect=fail_after_write):
            run = self.execute()
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(run['steps'][0]['status'], 'failed')
        after = alerts.list_alerts('local', 'admin')['alerts'][0]
        self.assertEqual(after['occurrences'], before['occurrences'])
        self.assertEqual(after['last_run_id'], before['last_run_id'])

    def test_engineer_alert_is_readonly_scoped_and_uses_bounded_two_row_check(self):
        self.save(actor='alice', role='data_engineer')
        observed = []
        def query(*args, **kwargs):
            observed.append((request_auth.current_role(), kwargs))
            return self.result(True)
        with patch.object(jobs.wr, 'query', side_effect=query):
            run = self.terminal(jobs.submit_run('alert_job', {}, 'alice', 'data_engineer')['id'], 'alice', 'data_engineer')
        self.assertEqual(run['status'], 'success')
        self.assertEqual(observed[0][0], 'data_engineer')
        self.assertEqual(observed[0][1], dict(catalog='iceberg', schema='marts', mode='read-only', max_rows=2))

    def test_alert_validation_uses_shared_typed_sql_slots_and_rejects_mode(self):
        definition = self.definition()
        definition['parameters'] = {'id': {'type': 'integer', 'required': True}}
        definition['steps'][0]['sql'] = 'SELECT {{id}} > 1'
        self.save(definition)
        self.assertEqual(self.execute(arguments={'id': 2})['status'], 'success')
        invalid = [{'message': ''}, {'message': '  '}, {'message': 'x' * 501},
                   {'severity': 'info'}, {'severity': []}, {'message': True},
                   {'mode': 'read-only'}, {'sql': 'INSERT INTO iceberg.raw.t VALUES (1)'},
                   {'sql': "SELECT '{{id}}'"}, {'schema': 'private'}]
        for changes in invalid:
            definition = self.definition()
            definition['steps'][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.save(definition)

    def test_acknowledgement_owner_only_idempotent_and_viewer_rejected(self):
        self.save(actor='alice', role='data_engineer')
        run = self.execute(actor='alice', role='data_engineer')
        alert_id = run['steps'][0]['summary']['alert_id']
        self.assertEqual(alerts.list_alerts('bob', 'data_engineer')['unacknowledged_count'], 0)
        self.assertEqual(alerts.list_alerts('bob', 'data_engineer')['alerts'], [])
        self.assertFalse(alerts.list_alerts('root', 'admin')['alerts'][0]['can_manage'])
        with self.assertRaises(PermissionError):
            alerts.acknowledge(alert_id, 'root', 'admin')
        with self.assertRaises(jobs.JobNotFound):
            alerts.acknowledge('missing', 'alice', 'data_engineer')
        first = alerts.acknowledge(alert_id, 'alice', 'data_engineer')
        second = alerts.acknowledge(alert_id, 'alice', 'data_engineer')
        self.assertEqual(first['acknowledged_at'], second['acknowledged_at'])
        self.assertEqual(first['acknowledged_by'], 'alice')
        self.assertIsNone(second['resolved_at'])
        for function, arguments in ((alerts.list_alerts, ()), (alerts.acknowledge, (alert_id,))):
            with self.assertRaises(PermissionError):
                function(*arguments, actor='alice', role='bi_viewer')

    def test_latest100_prioritizes_unacknowledged_and_count_includes_resolved(self):
        definition = self.definition()
        definition['parameters'] = {'id': {'type': 'integer', 'required': True}}
        definition['steps'][0]['sql'] = 'SELECT {{id}} > 0'
        self.save(definition)
        _, prepared = jobs._validate(definition, 'admin', {'id': 1})
        ids = []
        with jobs._db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            for number in range(1, 111):
                result = alerts.record(connection, 'alert_job', 1, 'Step 1', 'local', prepared[0], {'id': number}, 'synthetic-run', True)
                ids.append(result['alert_id'])
            for number in (10, 20):
                alerts.record(connection, 'alert_job', 1, 'Step 1', 'local', prepared[0], {'id': number}, 'synthetic-clear', False)
        for alert_id in ids[:5]:
            alerts.acknowledge(alert_id, 'local', 'admin')
        listing = alerts.list_alerts('local', 'admin')
        self.assertEqual(len(listing['alerts']), 100)
        self.assertEqual(listing['unacknowledged_count'], 105)
        self.assertTrue(all(row['acknowledged_at'] is None for row in listing['alerts']))
        self.assertTrue(any(row['resolution'] == 'condition_cleared' for row in listing['alerts']))


if __name__ == '__main__':
    unittest.main()
