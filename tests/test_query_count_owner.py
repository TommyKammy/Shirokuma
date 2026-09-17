"""Count v4 owner/protocol and old profile compatibility; no external calls."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools.local_experiment.query_count import contract as C, run as RUN
from tools.local_experiment.query_snapshots import run as SHARED
from tools.local_experiment.query_filter import run as FILTER
from tools.local_experiment.query_read import run as READER
import test_local_query_count as F
import test_query_snapshots_run as S
import test_query_filter_profile as FF

TOKEN = 'a' * 32


def observed(value=None, count=2):
    """Pure projections plus the existing owner fixture's rowless journal."""
    value = value or F.request()
    window = F.window(value)
    public = C.project_count(F.result(count), value, F.schema(), window)
    journal, _ = S.observed()
    journal.update(request_sha256=C.digest(value), identity_before=window['identity'],
                   identity_after=deepcopy(window['identity']), snapshot_catalog_before=window['snapshots'],
                   snapshot_catalog_after=deepcopy(window['snapshots']), selection=C.receipt_projection(public))
    return journal, public


def success(value, token, progress):
    assert token == TOKEN
    journal, public = observed(value)
    progress(journal)
    return journal, public


class CountOwnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name).resolve()
        self.output.chmod(0o700)
        self.source_patch = patch.object(SHARED, 'sources', return_value={'fixture.py': 'b' * 64})
        self.source_patch.start(); self.addCleanup(self.source_patch.stop)

    def owner(self, value=None): return RUN.Owner(TOKEN, value or F.request(), self.output)

    def test_profile_four_saves_count_without_individual_rows_or_model_calls(self):
        value = F.request(snapshot_id=str(F.OLD), column='id', literal='1')
        report, public = self.owner(value).execute(observer=success)
        saved = json.loads((self.output / 'summary.json').read_bytes())
        self.assertEqual(saved, report)
        self.assertEqual(report['schema_version'], 4)
        self.assertEqual(report['profile'], C.PROFILE)
        self.assertEqual(report['observation']['selection'], C.receipt_projection(public))
        self.assertEqual(public['count'], '2')
        self.assertEqual(public['snapshot_id'], str(F.OLD))
        self.assertEqual(public['current_snapshot_id'], str(F.HEAD))
        self.assertNotIn('rows', public)
        self.assertNotIn('"rows":', json.dumps(saved))
        self.assertEqual([report[x] for x in ('model_calls', 'cloud_calls', 'retries')], [0, 0, 0])
        self.assertIn('EXACT_COUNT_MAY_SCAN_ALL_ROWS', report['limits'])
        self.assertEqual((self.output / 'summary.json').stat().st_mode & 0o777, 0o600)

    def test_unverified_cleanup_and_mismatched_count_release_no_public(self):
        for kind in ('cleanup', 'selection'):
            with self.subTest(kind=kind):
                owner = self.owner()
                def observe(value, token, progress):
                    journal, public = observed(value)
                    if kind == 'cleanup': journal['cleanup_verified'] = False
                    else: journal['selection']['count'] = '999'
                    progress(journal)
                    return journal, public
                report, public = owner.execute(observer=observe)
                self.assertEqual(report['result'], 'failed')
                self.assertIsNone(public)

    def test_one_invocation_no_resume_and_pending_save_failure_has_no_observation(self):
        owner = self.owner(); observer = Mock(side_effect=success)
        owner.execute(observer=observer)
        with self.assertRaisesRegex(RUN.SnapshotError, 'SNAPSHOT_READ_RETRY_FORBIDDEN'):
            owner.execute(observer=observer)
        self.assertEqual(observer.call_count, 1)
        owner = self.owner(); observer = Mock()
        with patch.object(owner, 'save', side_effect=OSError('fsync')):
            report, public = owner.execute(observer=observer)
        self.assertFalse(observer.called)
        self.assertEqual(report['result'], 'failed'); self.assertIsNone(public)

    def test_source_drift_and_final_save_failure_never_publish_count(self):
        for kind in ('sources', 'save'):
            with self.subTest(kind=kind):
                owner = self.owner(); original = owner.save
                if kind == 'sources': owner.sources = lambda: {'fixture.py': 'c' * 64}
                else:
                    def final_failure():
                        if owner.report['result'] == 'passed': raise OSError('sync')
                        original()
                    owner.save = final_failure
                report, public = owner.execute(observer=success)
                self.assertEqual(report['result'], 'failed'); self.assertIsNone(public)

    def test_original_cancel_survives_secondary_save_failure(self):
        owner = self.owner(); cancel = KeyboardInterrupt(); original = owner.save
        def save():
            if owner.report['result'] != 'running': raise OSError('secondary save')
            original()
        owner.save = save
        with self.assertRaises(KeyboardInterrupt) as caught:
            owner.execute(observer=Mock(side_effect=cancel))
        self.assertIs(caught.exception, cancel)
        self.assertIsNone(owner.public)
        self.assertEqual(owner.report['result'], 'interrupted')

    def test_real_main_and_owner_emit_only_schema_four_after_cleanup(self):
        events, text = [], io.StringIO()
        def invoker(token, request, emit):
            def capture(event):
                events.append(deepcopy(event)); emit(event)
            return RUN.invoke(token, request, capture, observer=success)
        state = self.output / 'query-count'
        with patch.object(RUN, 'STATE', state):
            status = RUN.main(['count', '--evidence-token', TOKEN, '--request-stdin'],
                              stdin=io.BytesIO(C.canonical(F.request())), stdout=text, invoker=invoker)
        self.assertEqual(status, 0)
        self.assertEqual([x['event'] for x in events], ['allocated', 'result'])
        self.assertTrue(all(x['schema_version'] == 4 and x['command'] == 'query count' for x in events))
        self.assertEqual(set(events[0]), {'schema_version', 'event', 'command', 'evidence_token'})
        self.assertEqual(events[-1]['observation']['count'], '2')
        self.assertTrue(events[-1]['cleanup_verified'])
        self.assertEqual([json.loads(line) for line in text.getvalue().splitlines()], events)
        self.assertEqual(events[-1]['evidence'], 'tmp/local-experiment/query-count/' + TOKEN + '/summary.json')
        repeated = io.StringIO()
        with patch.object(RUN, 'STATE', state):
            self.assertEqual(RUN.main(['count', '--evidence-token', TOKEN, '--request-stdin'],
                stdin=io.BytesIO(C.canonical(F.request())), stdout=repeated, invoker=invoker), 1)
        self.assertEqual(json.loads(repeated.getvalue())['error_code'], 'EVIDENCE_ALREADY_EXISTS')

    def test_owner_with_real_observer_and_fake_http_closes_before_public(self):
        from test_query_count_observe import CountObservationTests, S as TRANSPORT
        fixture = CountObservationTests('test_zero_is_successful_exact_zero_not_missing_observation')
        fixture.setup()
        owner = RUN.Owner(TRANSPORT.TOKEN, fixture.request, self.output)
        def observe(request, token, progress):
            def save(value):
                fixture.progress(value)
                owner.progress(value)
                self.assertIsNone(owner.public)
            return RUN.observe(request, token, save, runtime_factory=fixture.runtime,
                               drain_fn=fixture.drain)
        report, public = owner.execute(observer=observe)
        self.assertEqual(report['result'], 'passed')
        self.assertEqual(public['count'], '2')
        self.assertEqual(fixture.events[-2:], ['drain', 'close'])
        self.assertEqual(fixture.events.count('select'), 1)
        self.assertTrue(report['cleanup_verified'])

    def test_bad_command_and_extra_sql_refused_before_invoker(self):
        for action, value in [('select', F.request()), ('count', dict(F.request(), sql='SELECT secret'))]:
            with self.subTest(action=action):
                invoker, out = Mock(), io.StringIO()
                status = RUN.main([action, '--evidence-token', TOKEN, '--request-stdin'],
                                  stdin=io.BytesIO(C.canonical(value)), stdout=out, invoker=invoker)
                self.assertEqual(status, 1); self.assertFalse(invoker.called)
                result = json.loads(out.getvalue())
                self.assertEqual(result['schema_version'], 4)
                self.assertIsNone(result['observation'])

    def test_old_snapshot_filter_and_reader_profile_shapes_remain_distinct(self):
        for cls, value, observer, version, profile in [
                (SHARED.Owner, S.request(), S.success, 2, None),
                (FILTER.Owner, FF.request(), lambda req, token, progress: self._filter(req, progress), 3, FF.C.PROFILE)]:
            with self.subTest(version=version):
                owner = cls(TOKEN, value, self.output)
                report, public = owner.execute(observer=observer)
                self.assertEqual(report['result'], 'passed')
                self.assertEqual(public['schema_version'], version)
                self.assertIn('rows', public); self.assertNotIn('count', public)
                self.assertEqual(report.get('profile'), profile)
        self.assertEqual(READER.C.project_rows(FF.F.result(), FF.F.request(), FF.F.schema(), FF.F.identity())['schema_version'], 1)

    @staticmethod
    def _filter(request, progress):
        value = FF.observed(request); progress(value[0]); return value


class CountSourcesTests(unittest.TestCase):
    def test_hashes_include_both_count_and_reused_filter_dependencies(self):
        count, old = RUN.sources(), SHARED.sources()
        additions = {'tools/local_experiment/query_' + profile + '/' + name
                     for profile in ('count', 'filter') for name in ('contract.py', 'run.py')}
        self.assertEqual(set(count) - set(old), additions)
        self.assertEqual({name: value for name, value in count.items() if name in old}, old)
        for path in additions:
            self.assertEqual(count[path], hashlib.sha256((RUN.ROOT / path).read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()
