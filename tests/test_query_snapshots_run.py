from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.query_snapshots import contract as C, run as RUN
from tools.local_experiment.query_read import contract as R
import test_query_read_contract as F

TOKEN = 'a' * 32
OLD = '9223372036854775806'


def request(action='select'):
    return C.request('snapshots', table='smoke.fixture_v1') if action == 'snapshots' else C.request(
        'select', table='smoke.fixture_v1', columns=['id','name'], limit=2, snapshot_id=OLD)


def observed(value=None):
    value = value or request()
    raw = F.metadata()
    raw['metadata']['snapshots'] = [
        {'snapshot-id':int(OLD), 'sequence-number':1, 'timestamp-ms':1000, 'schema-id':0,
         'summary':{'operation':'append'}},
        {'snapshot-id':F.SNAPSHOT, 'parent-snapshot-id':int(OLD), 'sequence-number':2,
         'timestamp-ms':2000, 'schema-id':0, 'summary':{'operation':'append'}}]
    window = C.project_window(raw, value, F.schema())
    public = C.project_snapshots(window, value) if value['command'] == 'snapshots' else C.project_rows(
        F.result(rows=[[1,'private-row-sentinel'],[2,'second-row']]), value, F.schema(), window)
    journal = {'state':'verified', 'request_sha256':R.digest(value), 'inventory':F.inventory(),
        'identity_before':window['identity'], 'identity_after':window['identity'],
        'snapshot_catalog_before':window['snapshots'], 'snapshot_catalog_after':window['snapshots'],
        'selection':C.receipt_projection(public) if value['command'] == 'select' else None,
        'queries':{'worker':[], 'control':[]}, 'query_drain':{'verified':True},
        'runtime':{'state':'verified', 'cleanup':{'verified':True}}, 'cleanup_verified':True,
        'error_code':None}
    return journal, public


def success(value, token, progress):
    if token != TOKEN:
        raise AssertionError('unexpected token')
    journal, public = observed(value)
    progress(journal)
    return journal, public


class OwnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name).resolve()
        self.output.chmod(0o700)
        self.source_patch = patch.object(RUN, 'sources', return_value={'fixed.py':'b'*64})
        self.source_patch.start(); self.addCleanup(self.source_patch.stop)

    def owner(self, value=None):
        return RUN.Owner(TOKEN, value or request(), self.output)

    def test_historical_identity_and_explicit_rows_are_distinct_from_saved_evidence(self):
        report, public = self.owner().execute(observer=success)
        saved = (self.output/'summary.json').read_text()
        self.assertEqual(report['result'], 'passed')
        self.assertEqual(public['snapshot_id'], OLD)
        self.assertEqual(public['current_snapshot_id'], str(F.SNAPSHOT))
        self.assertEqual(report['observation']['identity_before']['current_snapshot_id'], str(F.SNAPSHOT))
        self.assertNotIn('private-row-sentinel', saved)
        self.assertNotIn('"rows":', saved)
        self.assertNotIn('never-persist-credential', saved)
        self.assertEqual((self.output/'summary.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual([report[k] for k in ('model_calls','cloud_calls','retries')], [0,0,0])

    def test_listing_publishes_safe_records_only_after_observer_cleanup(self):
        report, public = self.owner(request('snapshots')).execute(observer=success)
        self.assertEqual(public['schema_version'], 2)
        self.assertEqual(len(public['snapshots']), 2)
        self.assertIsNone(report['observation']['selection'])
        self.assertTrue(report['cleanup_verified'])

    def test_source_change_suppresses_public_result_after_cleanup(self):
        owner = self.owner()
        with patch.object(RUN, 'sources', return_value={'fixed.py':'c'*64}):
            report, public = owner.execute(observer=success)
        self.assertEqual(report['error_code'], 'SNAPSHOT_SOURCE_CHANGED')
        self.assertTrue(report['cleanup_verified'])
        self.assertIsNone(public)

    def test_failed_or_incomplete_observation_cannot_publish_candidate(self):
        for state, cleanup in [('failed',True), ('verified',False)]:
            def observer(value, token, progress):
                journal, public = observed(value)
                journal.update(state=state, cleanup_verified=cleanup)
                progress(journal)
                return journal, public
            with self.subTest(state=state):
                report, public = self.owner().execute(observer=observer)
                self.assertEqual(report['result'], 'failed')
                self.assertIsNone(public)

    def test_public_rows_request_or_selection_mismatch_is_refused(self):
        for mutation in ('request','selection'):
            def observer(value, token, progress):
                journal, public = observed(value)
                if mutation == 'request':
                    public['requested_limit'] = 3
                else:
                    journal['selection']['current_snapshot_id'] = OLD
                progress(journal)
                return journal, public
            with self.subTest(mutation=mutation):
                report, public = self.owner().execute(observer=observer)
                self.assertEqual(report['result'], 'failed')
                self.assertIsNone(public)

    def test_fixed_refusal_and_unknown_error_have_different_safe_codes(self):
        from tools.local_experiment.query_snapshots.observe import ObservationError
        for error, expected in [(ObservationError('SNAPSHOT_NOT_RETAINED'),'SNAPSHOT_NOT_RETAINED'),
                                (RuntimeError('private-upstream-secret'),'SNAPSHOT_READ_FAILED')]:
            with self.subTest(expected=expected):
                report, public = self.owner().execute(observer=Mock(side_effect=error))
                self.assertEqual(report['error_code'], expected)
                self.assertIsNone(public)
                self.assertNotIn('private-upstream-secret', (self.output/'summary.json').read_text())

    def test_original_cancel_survives_source_and_final_save_failures(self):
        cancel = KeyboardInterrupt('original')
        owner = self.owner()
        writes = []
        def save():
            writes.append(1)
            if len(writes) > 1:
                raise OSError('secondary')
        owner.save = save
        with patch.object(RUN, 'sources', side_effect=RuntimeError('source-secondary')):
            with self.assertRaises(KeyboardInterrupt) as raised:
                owner.execute(observer=Mock(side_effect=cancel))
        self.assertIs(raised.exception, cancel)
        self.assertEqual(owner.report['result'], 'interrupted')

    def test_workflow_systemexit_zero_remains_interrupted(self):
        cancel = SystemExit(0)
        owner = self.owner()
        with self.assertRaises(SystemExit) as raised:
            owner.execute(observer=Mock(side_effect=cancel))
        self.assertIs(raised.exception, cancel)
        self.assertEqual(json.loads((self.output/'summary.json').read_text())['error_code'], 'INTERRUPTED')

    def test_final_fsync_failure_marks_receipt_failed_and_does_not_repeat_observation(self):
        owner = self.owner(); observer = Mock(side_effect=success)
        save = owner.save
        def broken():
            save()
            if owner.report['result'] == 'passed':
                raise OSError('fsync uncertain')
        owner.save = broken
        report, public = owner.execute(observer=observer)
        self.assertEqual(observer.call_count, 1)
        self.assertEqual(report['result'], 'failed')
        self.assertIsNone(public)
        self.assertEqual(json.loads((self.output/'summary.json').read_text())['result'], 'failed')

    def test_execute_twice_rejected_before_second_observation(self):
        owner = self.owner(); observer = Mock(side_effect=success)
        owner.execute(observer=observer)
        with self.assertRaisesRegex(RUN.SnapshotError, 'SNAPSHOT_READ_RETRY_FORBIDDEN'):
            owner.execute(observer=observer)
        self.assertEqual(observer.call_count, 1)


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); self.root.chmod(0o700)
        self.state = self.root/'state'
        for mocked in (patch.object(RUN, 'STATE', self.state),
                       patch.object(RUN, 'sources', return_value={'fixed.py':'b'*64})):
            mocked.start(); self.addCleanup(mocked.stop)

    def args(self, action='select'):
        return [action,'--evidence-token',TOKEN,'--request-stdin']

    def invoke(self, token, value, emit):
        return RUN.invoke(token, value, emit, observer=success)

    def test_main_invokes_real_owner_with_exact_two_schema2_events(self):
        for action in ('select','snapshots'):
            with self.subTest(action=action), patch.object(RUN, 'STATE', self.root/action):
                out = io.StringIO()
                status = RUN.main(self.args(action), stdin=io.BytesIO(R.canonical(request(action))),
                                  stdout=out, invoker=self.invoke)
                events = [json.loads(line) for line in out.getvalue().splitlines()]
                self.assertEqual(status, 0)
                self.assertEqual([e['event'] for e in events], ['allocated','result'])
                self.assertEqual([len(e) for e in events], [4,9])
                self.assertTrue(all(e['schema_version'] == 2 for e in events))
                self.assertEqual(events[-1]['evidence'], f'tmp/local-experiment/query-snapshots/{TOKEN}/summary.json')

    def test_invalid_input_is_refused_before_allocation_or_observer(self):
        invoker = Mock()
        for raw in [b'{"command":"snapshots","table":"smoke.x","sql":"SELECT secret"}',
                    R.canonical(dict(request(), snapshot_id=9007199254740993)),
                    R.canonical(dict(request(), snapshot_id='01')), b' '*4097,
                    b'{"command":"select","command":"snapshots"}']:
            with self.subTest(raw=raw[:50]):
                self.assertEqual(RUN.main(self.args(), stdin=io.BytesIO(raw), stdout=io.StringIO(), invoker=invoker), 1)
        invoker.assert_not_called(); self.assertFalse(self.state.exists())

    def test_existing_evidence_is_not_adopted_or_replayed(self):
        RUN.invoke(TOKEN, request(), lambda _:None, observer=success)
        raw = (self.state/TOKEN/'summary.json').read_bytes()
        observer = Mock()
        with self.assertRaisesRegex(RUN.SnapshotError, 'EVIDENCE_ALREADY_EXISTS'):
            RUN.invoke(TOKEN, request(), lambda _:None, observer=observer)
        observer.assert_not_called()
        self.assertEqual((self.state/TOKEN/'summary.json').read_bytes(), raw)

    def test_allocated_output_failure_prevents_observation(self):
        observer = Mock()
        with self.assertRaises(OSError):
            RUN.invoke(TOKEN, request(), Mock(side_effect=OSError('closed')), observer=observer)
        observer.assert_not_called()
        report = json.loads((self.state/TOKEN/'summary.json').read_text())
        self.assertEqual((report['result'], report['invocations']), ('failed',0))

    def test_final_output_failure_durably_refuses_success(self):
        sent = []
        def emit(value):
            sent.append(value['event'])
            if value['event'] == 'result':
                raise OSError('stdout failed')
        with self.assertRaises(OSError):
            RUN.invoke(TOKEN, request(), emit, observer=success)
        self.assertEqual(sent, ['allocated','result'])
        self.assertEqual(json.loads((self.state/TOKEN/'summary.json').read_text())['result'], 'failed')

    def test_final_output_cancel_survives_secondary_failed_write(self):
        cancel = KeyboardInterrupt('original')
        real = RUN.FS.write
        def write(path, value):
            if value['result'] == 'interrupted':
                raise OSError('secondary')
            real(path, value)
        def emit(value):
            if value['event'] == 'result':
                raise cancel
        with patch.object(RUN.FS, 'write', side_effect=write), self.assertRaises(KeyboardInterrupt) as raised:
            RUN.invoke(TOKEN, request(), emit, observer=success)
        self.assertIs(raised.exception, cancel)

    def test_workflow_systemexit_zero_returns_130_and_never_passed(self):
        def invoke(token, value, emit):
            return RUN.invoke(token,value,emit,observer=Mock(side_effect=SystemExit(0)))
        out = io.StringIO()
        self.assertEqual(RUN.main(self.args(), stdin=io.BytesIO(R.canonical(request())), stdout=out, invoker=invoke), 130)
        events = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(events[-1]['result'], 'interrupted')
        self.assertIsNone(events[-1]['observation'])

    def test_owner_lock_teardown_cannot_replace_or_suppress_original_cancel(self):
        cancel = KeyboardInterrupt('original')
        for suppress in (True, False):
            class Lock:
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    if suppress:
                        return True
                    raise OSError('unlock-secondary')
            with self.subTest(suppress=suppress), patch.object(RUN.FS, 'run_lock', return_value=Lock()), \
                    self.assertRaises(KeyboardInterrupt) as raised:
                with RUN.owner_lock(self.root):
                    raise cancel
            self.assertIs(raised.exception, cancel)

    def test_lock_close_failure_prevents_successful_python_exit_and_marks_receipt(self):
        class Lock:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                raise OSError('unlock-secondary')
        with patch.object(RUN.FS, 'run_lock', return_value=Lock()), self.assertRaises(OSError):
            RUN.invoke(TOKEN, request(), lambda _:None, observer=success)
        self.assertEqual(json.loads((self.state/TOKEN/'summary.json').read_text())['result'], 'failed')


if __name__ == '__main__':
    unittest.main()
