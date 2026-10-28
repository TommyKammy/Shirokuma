"""Schedule CLI exercises real HTTP serialization and strict public receipts."""
from contextlib import ExitStack, redirect_stderr
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import schedules_client as schedules

C = dict(database_id='db', schema_id='sc', warehouse_id='LOCAL', sql_catalog='sales',
         sql_schema='raw', selected_role='admin', binding_revision=2, generation_id='gen', grant_revision=7)
ID = '00000000-0000-0000-0000-000000000001'
TARGET = '00000000-0000-0000-0000-000000000002'


class SchedulesClient(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.context = self.root / 'context.json'; self.context.write_text(json.dumps(C))
        self.definition = dict(target_kind='job', target_id=TARGET, target_revision=3, expected_revision=0,
                               spec=dict(kind='interval', every_minutes=60), intent={'arguments': {'n': 7, 'flag': False}})
        self.file = self.root / 'definition.json'; self.file.write_text(json.dumps(self.definition))
        self.record = dict(id=ID, owner='owner', database_id='db', context=C, selected_role='admin',
                           revision=1, enabled=False, next_run_at=None, last_status=None, paused_reason=None,
                           **{k: v for k, v in self.definition.items() if k != 'expected_revision'})
        self.reply = dict(context=C, schedule=self.record)
        self.calls = []; self.failure = None

    def call(self, group='schedule', action='save', *extra):
        test = self
        class Response(io.BytesIO):
            headers = {}
        class Opener:
            def open(self, req, **kwargs):
                test.calls.append(req)
                if test.failure:
                    raise test.failure
                reply = dict(context=C, schedule=test.record) if urlsplit(req.full_url).path == '/api/v1/schedules/' + ID else test.reply
                return Response(json.dumps(reply).encode())
        args = [group, action, '--context-file', str(self.context), *extra]
        if action == 'save': args += ['--definition', str(self.file), '--idempotency-key', 'original-key']
        with ExitStack() as stack:
            stack.enter_context(patch('dataops_client.runtime_profile.private_json', return_value=dict(username='owner', password='withheld', selected_role='admin')))
            stack.enter_context(patch('dataops_client.build_opener', return_value=Opener()))
            return schedules.main(args, profile=SimpleNamespace(client_auth_file='auth'))

    def test_save_frozen_typed_intent_and_x_header(self):
        self.assertEqual(self.call(), self.reply)
        req = self.calls[0]
        self.assertEqual(req.method, 'POST')
        self.assertEqual(dict(req.header_items())['X-idempotency-key'], 'original-key')
        self.assertNotIn('Idempotency-key', dict(req.header_items()))
        self.assertEqual(json.loads(req.data), dict(context=C, **self.definition))
        self.assertIs(json.loads(req.data)['intent']['arguments']['flag'], False)

    def test_lists_show_ticks_and_alert_ack(self):
        self.reply = dict(context=C, schedules=[self.record])
        self.call('schedule', 'list', '--target-kind', 'job', '--target-id', TARGET)
        query = parse_qs(urlsplit(self.calls[-1].full_url).query)
        self.assertEqual(json.loads(query['context'][0]), C)
        self.assertEqual(query['target_id'], [TARGET])
        self.reply = dict(context=C, schedule=self.record)
        self.call('schedule', 'show', '--schedule-id', ID)
        tick = dict(id=TARGET, schedule_id=ID, schedule_revision=1, target_revision=3, scheduled_for='2026-10-22T00:00:00Z', status='succeeded', code=None, run_id=None, owner='owner', database_id='db', context=C, selected_role='admin', results=[{'triggered': False}])
        self.reply = dict(context=C, ticks=[tick])
        self.call('schedule', 'ticks', '--schedule-id', ID)
        self.reply = dict(context=C, alerts=[tick], unacknowledged_count=1)
        self.call('alert', 'list')
        self.reply = dict(context=C, alert=tick)
        self.call('alert', 'ack', '--alert-id', TARGET, '--idempotency-key', 'ack-key')
        self.assertEqual(json.loads(self.calls[-1].data), dict(context=C))
        self.assertTrue(urlsplit(self.calls[-1].full_url).path.endswith('/' + TARGET + '/ack'))

    def test_invalid_definitions_never_send(self):
        for changes in ({'owner': 'override'}, {'target_id': 'bad'}, {'expected_revision': True},
                        {'enabled': 'true'}, {'expected_revision': 1}, {'schedule_id': ID},
                        {'intent': {'arguments': [], 'inputs': {}}}, {'spec': {'kind': 'weekly', 'weekdays': [1, 1]}}):
            self.file.write_text(json.dumps(dict(self.definition, **changes)))
            with self.subTest(changes=changes), self.assertRaises(Exception): self.call()
        self.assertEqual(self.calls, [])

    def test_owner_role_context_and_revision_receipts_rejected(self):
        for change in ({'owner': 'another'}, {'selected_role': 'data_engineer'}, {'database_id': 'other'},
                       {'context': dict(C, schema_id='other')}, {'revision': 4}, {'enabled': True},
                       {'target_id': ID}, {'revision': True}, {'spec': {'kind': 'interval', 'every_minutes': 5}},
                       {'intent': {'arguments': {'n': 'changed'}}}):
            self.reply = dict(context=C, schedule=dict(self.record, **change))
            with redirect_stderr(io.StringIO()), self.assertRaises(ValueError): self.call()
        self.reply = dict(context=dict(C, selected_role='data_engineer'), schedule=self.record)
        with redirect_stderr(io.StringIO()), self.assertRaises(ValueError): self.call()

    def test_response_loss_does_not_retry_and_prints_only_observation(self):
        self.failure = OSError('secret upstream detail')
        stream = io.StringIO()
        with redirect_stderr(stream), self.assertRaises(ValueError): self.call()
        self.assertEqual(len(self.calls), 1)
        recovery = json.loads(stream.getvalue())
        self.assertEqual(recovery['context'], C)
        self.assertEqual(recovery['idempotency_key'], 'original-key')
        self.assertEqual(recovery['observe'], '/api/v1/schedules')
        self.assertNotIn('secret', stream.getvalue())
        self.assertNotIn('withheld', stream.getvalue())

    def test_calendar_specs_and_exact_intents(self):
        for spec in (dict(kind='daily', time='08:15', timezone='UTC'),
                     dict(kind='once', time='09:00', timezone='Asia/Tokyo', date='2028-12-10'),
                     dict(kind='weekly', time='09:00', timezone='UTC', weekdays=[1, 7]),
                     dict(kind='monthly', time='12:30', timezone='UTC', day_of_month='last')):
            self.assertEqual(schedules.spec_value(spec), spec)
        for kind, intent in [('stage', {'action': 'scan'}), ('mart', {'action': 'refresh', 'check_changes': True})]:
            schedules.definition_value(dict(self.definition, target_kind=kind, intent=intent))
        with self.assertRaises(ValueError): schedules.spec_value(dict(kind='daily', time='25:00'))
        with self.assertRaises(ValueError):
            schedules.definition_value(dict(self.definition, target_kind='mart', intent={'action': 'refresh', 'check_changes': False}))

    def test_old_saved_scope_lists_shows_updates_reenables_and_acknowledges(self):
        old = dict(C, binding_revision=1, generation_id='old-generation', grant_revision=2)
        self.record = dict(self.record, context=old, paused_reason='STALE_BINDING')
        self.reply = dict(context=C, schedules=[self.record])
        self.call('schedule', 'list')
        self.call('schedule', 'show', '--schedule-id', ID)
        for revision, enabled in ((1, False), (2, True)):
            definition = dict(self.definition, schedule_id=ID, expected_revision=revision, enabled=enabled)
            self.file.write_text(json.dumps(definition))
            self.record = dict(self.record, context=C, revision=revision + 1, enabled=enabled)
            self.reply = dict(context=C, schedule=self.record)
            self.call()
            self.assertEqual(json.loads(self.calls[-1].data), dict(definition, context=C))
        alert = dict(id=TARGET, owner='owner', database_id='db', selected_role='admin', context=old)
        self.reply = dict(context=C, alerts=[alert], unacknowledged_count=1)
        self.call('alert', 'list')
        self.reply = dict(context=C, alert=alert)
        self.call('alert', 'ack', '--alert-id', TARGET, '--idempotency-key', 'ack-old')
        self.assertEqual(json.loads(self.calls[-1].data), dict(context=C))
        self.reply = dict(context=old, alerts=[alert], unacknowledged_count=1)
        with self.assertRaises(ValueError): self.call('alert', 'list')

    def test_compact_tick_contract_and_malformed_fields(self):
        tick = dict(id=TARGET, schedule_id=ID, schedule_revision=1, target_revision=3,
                    scheduled_for='2026-10-22T00:00:00Z', status='submitted', code=None, run_id=None)
        self.reply = dict(context=C, ticks=[tick])
        self.call('schedule', 'ticks', '--schedule-id', ID)
        for change in ({'id': 'bad'}, {'schedule_id': TARGET}, {'schedule_revision': True},
                       {'target_revision': 0}, {'scheduled_for': 'bad'}, {'scheduled_for': '2026-10-22'},
                       {'status': []}, {'code': 3}, {'run_id': 'bad'}):
            self.reply = dict(context=C, ticks=[dict(tick, **change)])
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.call('schedule', 'ticks', '--schedule-id', ID)


if __name__ == '__main__': unittest.main()
