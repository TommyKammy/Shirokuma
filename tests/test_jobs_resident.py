"""Focused state, authorization, and process-loss checks for manual jobs."""
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / 'tools/local_experiment'
sys.path.insert(0, str(TOOLS))
import jobs_resident as jobs
import request_auth


class JobsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.environment = patch.dict(os.environ, {'SHIROKUMA_STATE_DIR': self.directory.name,
            'SHIROKUMA_AUTH_MODE': 'local', 'SHIROKUMA_AUTH_FILE': ''})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.telemetry = patch.object(jobs.wr, 'telemetry', return_value='skipped')
        self.telemetry.start()
        self.addCleanup(self.telemetry.stop)

    def definition(self, name='example', steps=None, parameters=None):
        return dict(name=name, description='Synthetic local job', parameters=parameters or {},
                    steps=steps or [dict(action='sql', sql='SELECT 1', mode='read-only')])

    def done(self, run_id, actor='local', role='admin'):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = jobs.get_run(run_id, actor, role)
            if result['status'] not in jobs.ACTIVE:
                for thread in threading.enumerate():
                    if thread.name == 'job-' + run_id[:8]:
                        thread.join(2)
                return result
            time.sleep(.01)
        self.fail('worker did not finish')

    def test_parameter_values_are_typed_literals_not_sql_fragments(self):
        parameters = {'text': {'type': 'string', 'required': True},
                      'count': {'type': 'integer', 'default': 7},
                      'enabled': {'type': 'boolean', 'default': False},
                      'day': {'type': 'date', 'required': True}}
        definition = self.definition(parameters=parameters, steps=[dict(action='sql', mode='read-only',
            sql='SELECT {{text}}, {{count}}, {{enabled}}, {{day}}')])
        jobs.save_job(definition, 'local', 'admin')
        captured = []
        def execute(step):
            captured.append(step['sql'])
            return dict(result='verified', rows=[['do not store']], columns=[{'name': 'sensitive'}])
        value = "x'); DROP TABLE iceberg.raw.t; -- {{literal}}"
        with patch.object(jobs, '_execute_step', side_effect=execute):
            run = jobs.submit_run('example', {'text': value, 'day': '2026-10-18'}, 'local', 'admin')
            result = self.done(run['id'])
        self.assertEqual(result['status'], 'success')
        self.assertEqual(captured, ["SELECT 'x''); DROP TABLE iceberg.raw.t; -- {{literal}}', 7, FALSE, DATE '2026-10-18'"])
        self.assertEqual(result['arguments']['count'], 7)
        self.assertNotIn('do not store', json.dumps(result))
        self.assertNotIn('columns', result['steps'][0]['summary'])
        self.assertEqual((Path(self.directory.name) / 'jobs.sqlite3').stat().st_mode & 0o777, 0o600)

    def test_rejects_quoted_unknown_unused_and_identifier_slots_before_registration(self):
        parameters = {'value': {'type': 'integer'}}
        for sql in ("SELECT '{{value}}'", 'SELECT "{{value}}"', 'SELECT 1 -- {{value}}',
                    'SELECT 1 /* {{value}} */', 'SELECT {{unknown}}', 'SELECT 1',
                    'SELECT prefix{{value}}', 'SELECT {{value}}suffix',
                    'SELECT raw.{{value}}', 'SELECT {{bad-name}}', "SELECT 'broken {{value}}",
                    'SELECT * FROM {{value}}', 'SELECT 1 AS {{value}}',
                    'SELECT CAST(1 AS {{value}})', 'CREATE TABLE IF NOT EXISTS {{value}} AS SELECT 1'):
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                jobs.save_job(self.definition(parameters=parameters, steps=[dict(action='sql', sql=sql, mode='read-only')]), 'local', 'admin')
        self.assertEqual(jobs.list_jobs('local', 'admin'), [])

    def test_all_parameter_errors_precede_any_step_execution(self):
        params = {'id': {'type': 'integer', 'required': True}}
        jobs.save_job(self.definition(parameters=params, steps=[dict(action='sql', sql='SELECT {{id}}', mode='read-only')]), 'local', 'admin')
        for arguments in ({}, {'id': True}, {'id': 1.1}, {'id': '2'}, {'id': 2**63},
                          {'id': 1, 'extra': 2}, {'id': [1]}, {'id': None}):
            with self.subTest(arguments=arguments), patch.object(jobs, '_execute_step') as execute:
                with self.assertRaises(ValueError):
                    jobs.submit_run('example', arguments, 'local', 'admin')
                execute.assert_not_called()
        self.assertEqual(jobs.list_runs('local', 'admin'), [])

    def test_date_boolean_types_optional_null_and_all_steps_validation(self):
        for value in ('2026-02-30', True, 123):
            with self.assertRaises(ValueError):
                jobs._typed(value, 'date')
        with self.assertRaises(ValueError):
            jobs._typed('true', 'boolean')
        jobs.save_job(self.definition(parameters={'x': {'type': 'string'}},
            steps=[dict(action='sql', sql='SELECT {{x}}', mode='read-only')]), 'local', 'admin')
        with patch.object(jobs, '_execute_step', return_value={}):
            run = jobs.submit_run('example', {}, 'local', 'admin')
            self.assertEqual(self.done(run['id'])['arguments'], {'x': None})
        bad = self.definition(steps=[dict(action='sql', sql='SELECT 1', mode='read-only'), dict(action='shell', command='echo unsafe')])
        with self.assertRaises(ValueError):
            jobs.save_job(bad, 'local', 'admin')

    def test_owner_boundaries_and_engineer_action_ceiling(self):
        jobs.save_job(self.definition(), 'alice', 'data_engineer')
        self.assertEqual(jobs.list_jobs('bob', 'data_engineer'), [])
        self.assertEqual(jobs.list_jobs('root', 'admin')[0]['can_manage'], False)
        with self.assertRaises(PermissionError):
            jobs.save_job(self.definition(), 'bob', 'admin')
        with self.assertRaises(jobs.JobNotFound):
            jobs.submit_run('example', {}, 'bob', 'data_engineer')
        for action in ('dbt_build', 'dbt_test', 'ingest', 'wap_refresh', 'wap_publish'):
            with self.subTest(action=action), self.assertRaises(PermissionError):
                jobs.save_job(self.definition(steps=[dict(action=action)]), 'alice', 'data_engineer')
        for mode, sql in [('admin-write', 'SELECT 1'),
                          ('sandbox-write', 'INSERT INTO iceberg.marts.private VALUES (1)')]:
            with self.assertRaises(ValueError):
                jobs.save_job(self.definition(steps=[dict(action='sql', sql=sql, mode=mode)]), 'alice', 'data_engineer')
        for function, args in [(jobs.list_jobs, ()), (jobs.list_runs, ()),
                               (jobs.save_job, (self.definition(),)),
                               (jobs.submit_run, ('example', {})), (jobs.get_run, ('missing',))]:
            with self.assertRaises(PermissionError):
                function(*args, actor='viewer', role='bi_viewer')

    def test_global_busy_and_execution_snapshot_survive_definition_edits(self):
        jobs.save_job(self.definition(), 'local', 'admin')
        jobs.save_job(self.definition(name='other'), 'local', 'admin')
        entered, release = threading.Event(), threading.Event()
        def execute(step):
            entered.set()
            release.wait(3)
            return dict(result='verified')
        with patch.object(jobs, '_execute_step', side_effect=execute):
            run = jobs.submit_run('example', {}, 'local', 'admin')
            try:
                self.assertTrue(entered.wait(2))
                for name in ('example', 'other'):
                    with self.assertRaises(jobs.JobConflict):
                        jobs.submit_run(name, {}, 'local', 'admin')
                jobs.save_job(self.definition(steps=[dict(action='sql', sql='SELECT 2', mode='read-only')]), 'local', 'admin')
                self.assertEqual(jobs.get_run(run['id'], 'local', 'admin')['definition']['steps'][0]['sql'], 'SELECT 1')
            finally:
                release.set()
                self.done(run['id'])

    def test_engineer_role_propagates_to_worker_and_history_is_private(self):
        jobs.save_job(self.definition(), 'alice', 'data_engineer')
        roles = []
        def query(*args, **kwargs):
            roles.append(request_auth.current_role())
            self.assertIsNone(kwargs['max_rows'])
            return dict(result='verified', rows=[[1]], row_count=1)
        with patch.object(jobs.wr, 'query', side_effect=query):
            run = jobs.submit_run('example', {}, 'alice', 'data_engineer')
            result = self.done(run['id'], 'alice', 'data_engineer')
        self.assertEqual(roles, ['data_engineer'])
        self.assertEqual(result['role'], 'data_engineer')
        self.assertEqual(result['actor'], 'alice')
        self.assertEqual(jobs.list_runs('bob', 'data_engineer'), [])
        with self.assertRaises(jobs.JobNotFound):
            jobs.get_run(run['id'], 'bob', 'data_engineer')
        self.assertEqual(jobs.get_run(run['id'], 'root', 'admin')['status'], 'success')

    def test_failfast_keeps_successful_steps_and_sanitizes_driver_error(self):
        steps = [dict(action='sql', sql='SELECT 1', mode='read-only')] * 3
        jobs.save_job(self.definition(steps=steps), 'local', 'admin')
        with patch.object(jobs, '_execute_step', side_effect=[{}, RuntimeError('secret-driver-password'), {}]) as execute:
            result = self.done(jobs.submit_run('example', {}, 'local', 'admin')['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual([s['status'] for s in result['steps']], ['success', 'failed', 'skipped'])
        self.assertEqual(execute.call_count, 2)
        self.assertNotIn('secret-driver-password', json.dumps(result))
        with patch.object(jobs, '_execute_step', side_effect=TimeoutError('sensitive')):
            result = self.done(jobs.submit_run('example', {}, 'local', 'admin')['id'])
        self.assertEqual(result['status'], 'unknown')

    def test_telemetry_failure_does_not_change_committed_success_or_emit_arguments(self):
        jobs.save_job(self.definition(), 'local', 'admin')
        with patch.object(jobs, '_execute_step', return_value={}), \
             patch.object(jobs.wr, 'telemetry', side_effect=RuntimeError('unavailable')) as telemetry:
            result = self.done(jobs.submit_run('example', {}, 'local', 'admin')['id'])
        self.assertEqual(result['status'], 'success')
        self.assertEqual(telemetry.call_args.kwargs['task_id'], result['id'])
        self.assertNotIn('arguments', telemetry.call_args.kwargs)
        self.assertNotIn('sql', telemetry.call_args.kwargs)

    def test_trino_error_code_is_visible_without_raw_driver_values(self):
        jobs.save_job(self.definition(steps=[dict(action='sql', sql='SELECT 1', mode='read-only')] * 2), 'local', 'admin')
        for error_name, expected in [('DIVISION_BY_ZERO', 'DIVISION_BY_ZERO'),
                                     ('TABLE_NOT_FOUND', 'TABLE_NOT_FOUND'),
                                     ('unsafe secret value', 'UNKNOWN')]:
            with self.subTest(error_name=error_name), patch.object(jobs, '_execute_step',
                    side_effect=jobs.wr.TrinoQueryError('raw-driver-secret-value', error_name)):
                result = self.done(jobs.submit_run('example', {}, 'local', 'admin')['id'])
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['error'], 'Trino query failed (' + expected + ')')
            self.assertEqual(result['steps'][0]['error'], result['error'])
            self.assertEqual(result['steps'][1]['status'], 'skipped')
            self.assertNotIn('raw-driver-secret-value', json.dumps(result))
            self.assertNotIn('unsafe secret value', json.dumps(result))

    def test_budget_is_checked_before_next_step_and_never_claims_to_cancel_running_step(self):
        jobs.save_job(self.definition(steps=[dict(action='sql', sql='SELECT 1', mode='read-only')] * 2), 'local', 'admin')
        original = time.monotonic
        budget = {'value': original()}
        def operation(step):
            budget['value'] += 901
            return {}
        with patch.object(jobs.time, 'monotonic', side_effect=lambda: budget['value']), \
             patch.object(jobs, '_execute_step', side_effect=operation) as execute:
            run = jobs.submit_run('example', {}, 'local', 'admin')
            for thread in threading.enumerate():
                if thread.name == 'job-' + run['id'][:8]:
                    thread.join(3)
            result = jobs.get_run(run['id'], 'local', 'admin')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(execute.call_count, 1)
        self.assertEqual([step['status'] for step in result['steps']], ['success', 'failed'])
        self.assertIn('between steps', result['error'])

    def test_shared_account_downgrade_removal_and_mode_change_stop_between_steps(self):
        auth = Path(self.directory.name) / 'accounts.json'
        for change in ('downgrade', 'remove', 'local_mode'):
            auth.write_text(json.dumps({'alice': {'role': 'admin'}}))
            with self.subTest(change=change), patch.dict(os.environ, {
                    'SHIROKUMA_AUTH_MODE': 'shared', 'SHIROKUMA_AUTH_FILE': str(auth)}):
                jobs.save_job(self.definition(steps=[dict(action='sql', sql='SELECT 1', mode='read-only')] * 2), 'alice', 'admin')
                def execute(step):
                    if change == 'downgrade':
                        auth.write_text(json.dumps({'alice': {'role': 'data_engineer'}}))
                    elif change == 'remove':
                        auth.write_text('{}')
                    else:
                        os.environ['SHIROKUMA_AUTH_MODE'] = 'local'
                        os.environ['SHIROKUMA_AUTH_FILE'] = ''
                    return {}
                with patch.object(jobs, '_execute_step', side_effect=execute) as operation:
                    result = self.done(jobs.submit_run('example', {}, 'alice', 'admin')['id'], 'alice', 'admin')
                self.assertEqual(operation.call_count, 1)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual([s['status'] for s in result['steps']], ['success', 'failed'])

    def test_action_results_are_not_mistaken_for_success(self):
        import lake_pipeline
        import bears_resident
        import lake_ingest
        for action in ('dbt_build', 'dbt_test'):
            with patch.object(lake_pipeline, 'run', return_value={'result': 'failed'}), self.assertRaises(ValueError):
                jobs._execute_step({'action': action})
        for report in ({'valid': False}, {'error': 'failure'}, {'valid': True, 'error': 'failure'}):
            with patch.object(bears_resident, 'data', return_value=report), self.assertRaises(ValueError):
                jobs._execute_step({'action': 'wap_publish', 'table': 'model'})
        with patch.object(bears_resident, 'data', return_value={'valid': True}):
            self.assertTrue(jobs._execute_step({'action': 'wap_refresh', 'table': 'model'})['valid'])
        with patch.object(lake_ingest, 'ingest', return_value={'rows_ingested': 0}) as ingest:
            self.assertEqual(jobs._execute_step(dict(action='ingest', file='/tmp/input.csv', table='raw.t', mode='append')), {'rows_ingested': 0})
        self.assertTrue(ingest.call_args.kwargs['atomic_append'])

    def test_ingest_preflight_blocks_missing_later_input_before_sql(self):
        definition = self.definition(steps=[dict(action='sql', sql='SELECT 1', mode='read-only'),
            dict(action='ingest', file=str(Path(self.directory.name) / 'missing.csv'), table='raw.t', mode='replace')])
        jobs.save_job(definition, 'local', 'admin')
        with patch.object(jobs, '_execute_step') as execute, self.assertRaises(FileNotFoundError):
            jobs.submit_run('example', {}, 'local', 'admin')
        execute.assert_not_called()
        self.assertEqual(jobs.list_runs('local', 'admin'), [])

    def test_expected_ingest_parser_errors_are_safe_value_errors_before_acceptance(self):
        for filename, content, message in (
                ('large.csv', 'payload\n' + 'x' * (csv.field_size_limit() + 1) + '\n', 'ingest CSV could not be parsed'),
                ('large.jsonl', '{"value":1e9999999999999999999}', 'ingest JSON number could not be parsed')):
            path = Path(self.directory.name) / filename
            path.write_text(content)
            definition = self.definition(steps=[dict(action='sql', sql='SELECT 1', mode='read-only'),
                dict(action='ingest', file=str(path), table='raw.input', mode='replace')])
            jobs.save_job(definition, 'local', 'admin')
            with self.subTest(filename=filename), patch.object(jobs, '_execute_step') as execute:
                with self.assertRaises(ValueError) as caught:
                    jobs.submit_run('example', {}, 'local', 'admin')
                self.assertEqual(str(caught.exception), message)
                execute.assert_not_called()
                self.assertEqual(jobs.list_runs('local', 'admin'), [])

    def test_process_loss_preserves_successful_alert_and_acknowledgement_without_replay(self):
        definition = self.definition(steps=[dict(action='alert', sql='SELECT TRUE', message='Synthetic condition'),
            dict(action='sql', sql='SELECT 1', mode='read-only'), dict(action='sql', sql='SELECT 2', mode='read-only')])
        jobs.save_job(definition, 'local', 'admin')
        run_marker = Path(self.directory.name) / 'crash-run-id'
        blocked_marker = Path(self.directory.name) / 'crash-sql-blocked'
        code = f'''
import sys,threading,os
from pathlib import Path
sys.path.insert(0,{str(TOOLS)!r})
import jobs_resident as jobs
ready=threading.Event()
def execute(step):
    ready.wait(5)
    if step['action']=='alert': return {{'condition_met':True}}
    Path({str(blocked_marker)!r}).write_text('ready')
    sys.stdin.readline()
    return {{}}
jobs._execute_step=execute
jobs.wr.telemetry=lambda *a,**k:'skipped'
run=jobs.submit_run('example',{{}},'local','admin')
Path({str(run_marker)!r}).write_text(run['id'])
ready.set()
threading.Event().wait()
'''
        child = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not blocked_marker.is_file():
                if child.poll() is not None:
                    break
                time.sleep(.01)
            self.assertTrue(run_marker.is_file() and blocked_marker.is_file(),
                            'child did not reach the SQL boundary within five seconds')
            run_id = run_marker.read_text().strip()
            self.assertTrue(run_id)
            alert = jobs.list_alerts('local', 'admin')['alerts'][0]
            acknowledged = jobs.acknowledge(alert['id'], 'local', 'admin')
            child.kill()
            child.communicate(timeout=5)
            # Fresh process performs normal restart recovery and reads durable state.
            restart = f'''
import sys,json
sys.path.insert(0,{str(TOOLS)!r})
import jobs_resident as jobs
run=jobs.get_run({run_id!r},'local','admin')
print(json.dumps({{'run':run,'alerts':jobs.list_alerts('local','admin'),'run_count':len(jobs.list_runs('local','admin'))}}))
'''
            reader = subprocess.run([sys.executable, '-c', restart], capture_output=True, text=True, timeout=5)
            self.assertEqual(reader.returncode, 0, reader.stderr)
            result = json.loads(reader.stdout)
            self.assertEqual(result['run']['status'], 'unknown')
            self.assertEqual([step['status'] for step in result['run']['steps']], ['success', 'unknown', 'skipped'])
            self.assertEqual(result['run_count'], 1)
            self.assertEqual(result['alerts']['unacknowledged_count'], 0)
            restored = result['alerts']['alerts'][0]
            self.assertEqual(restored['id'], alert['id'])
            self.assertEqual(restored['acknowledged_at'], acknowledged['acknowledged_at'])
            self.assertEqual(restored['acknowledged_by'], 'local')
            self.assertIsNone(restored['resolved_at'])
            self.assertEqual(restored['occurrences'], 1)
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)

    def test_process_loss_is_unknown_without_replay_and_cross_process_claim_is_exclusive(self):
        jobs.save_job(self.definition(steps=[dict(action='sql', sql='SELECT 1', mode='read-only')] * 2), 'local', 'admin')
        code = f'''
import sys, os, threading
sys.path.insert(0, {str(TOOLS)!r})
import jobs_resident as jobs
def execute(step):
    sys.stdin.readline()
    os._exit(17)
jobs._execute_step = execute
run = jobs.submit_run('example', {{}}, 'local', 'admin')
print(run['id'], flush=True)
threading.Event().wait()
'''
        child = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            run_id = child.stdout.readline().strip()
            self.assertTrue(run_id)
            with self.assertRaises(jobs.JobConflict):
                jobs.submit_run('example', {}, 'local', 'admin')
            child.stdin.write('exit\n')
            child.stdin.flush()
            child.wait(5)
            result = jobs.get_run(run_id, 'local', 'admin')
            self.assertEqual(result['status'], 'unknown')
            self.assertEqual([s['status'] for s in result['steps']], ['unknown', 'skipped'])
            with patch.object(jobs, '_execute_step', return_value={}):
                next_run = self.done(jobs.submit_run('example', {}, 'local', 'admin')['id'])
                self.assertEqual(next_run['status'], 'success')
            self.assertEqual(len(jobs.list_runs('local', 'admin')), 2)
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
