"""Additional request semantics and privacy; no model, HTTP or runtime calls."""
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parents[1] / 'tools/local_experiment/bears'
sys.path.insert(0, str(HERE))
import evaluate_data as E
from providers import ProviderError


def config():
    value = json.loads((HERE.parent / 'llm/providers.example.json').read_text())
    value['active_provider'] = 'local'
    value['providers']['local'].update(enabled=True, model='qwen3.8-flash-next',
        max_output_tokens=512, timeout_seconds=60, enable_thinking=False)
    return value


class Model:
    def __init__(self, cases, *, wrong=False, invalid=False, fail=False):
        self.cases, self.calls = cases, []
        self.wrong, self.invalid, self.fail = wrong, invalid, fail

    def complete(self, phase, evidence):
        self.calls.append((phase, deepcopy(evidence)))
        if self.fail:
            raise ProviderError('PRIVATE-RAW-ERROR', {'prompt_tokens': 10, 'completion_tokens': 5,
                                                     'duration_ms': 3, 'content': 'PRIVATE-RAW-RESPONSE'})
        operation = next(case['expected_operation'] for case in self.cases if case['request'] == evidence['request'])
        if self.wrong:
            operation = 'row_count' if operation != 'row_count' else 'unsupported'
        plan = {'schema_version': 1, 'source': 'fixture_v1', 'operation': operation}
        if self.invalid:
            plan['sql'] = 'PRIVATE-RAW-MODEL-SQL'
        return {'content': plan, 'prompt_tokens': 10, 'completion_tokens': 5,
                'duration_ms': 3, 'inference_cost_estimate': None}


class AuditClient:
    def __init__(self, *, wrong_readback=False):
        self.events = []
        self.wrong_readback = wrong_readback

    def query(self, sql):
        if sql == 'SELECT 1':
            return '1\n'
        if sql.startswith('INSERT INTO agenthouse.pawprints FORMAT JSONEachRow\n'):
            self.events.append(json.loads(sql.split('\n', 1)[1]))
            return ''
        match = re.search(r"WHERE event_id = '([a-f0-9-]{36})' FORMAT JSON$", sql)
        if not match:
            raise AssertionError('unexpected audit query')
        rows = [deepcopy(event) for event in self.events if event['event_id'] == match.group(1)]
        for row in rows:
            for key in ('prompt_tokens', 'completion_tokens', 'duration_ms'):
                if row[key] is not None:
                    row[key] = str(row[key])
            if self.wrong_readback:
                row['completion_tokens'] = '999'
        return json.dumps({'data': rows})


class RequestEvaluationTests(unittest.TestCase):
    def setUp(self):
        for target in ('urllib.request.urlopen', 'socket.create_connection'):
            patcher = patch(target, side_effect=AssertionError('HTTP forbidden in offline test'))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.document = json.loads(E.REQUEST_CASES.read_text())

    def evaluate(self, model, client, directory, **kwargs):
        return E.evaluate(config(), Path(directory) / 'evidence', model=model,
                          client=client, audit_module=E.agenthouse_module(), **kwargs)

    def test_default_baseline_keeps_six_cases_twice_and_api(self):
        self.assertEqual(E.load_cases(), E.model_cases())
        self.assertEqual(len(E.load_cases()), 6)
        model, client = Model(E.model_cases()), AuditClient()
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(model, client, directory)
        self.assertEqual(report['suite'], 'baseline')
        self.assertEqual(report['result'], 'passed')
        self.assertEqual((report['requested_inferences'], report['passed_inferences'], report['audited_inferences']), (12, 12, 12))
        self.assertEqual([item['plan']['operation'] for item in report['attempts']],
                         [case['expected_operation'] for case in E.model_cases() for _ in range(2)])

    def test_request_suite_seven_semantics_twice_exact_audit_and_no_raw_evidence(self):
        cases = E.load_cases('requests')
        model, client = Model(cases), AuditClient()
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(model, client, directory, suite='requests')
            output = Path(directory) / 'evidence'
            self.assertEqual(json.loads((output / 'result.json').read_text()), report)
            saved = '\n'.join(path.read_text() for path in output.iterdir())
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in output.iterdir()))
        self.assertEqual(report['suite'], 'requests')
        self.assertRegex(report['dataset_sha256'], r'^[0-9a-f]{64}$')
        self.assertEqual(report['result'], 'passed')
        self.assertEqual((report['requested_inferences'], report['passed_inferences'], report['audited_inferences']), (14, 14, 14))
        self.assertEqual(len(model.calls), 14)
        self.assertEqual(len({event['event_id'] for event in client.events}), 14)
        self.assertEqual(sum(item['plan']['operation'] == 'unsupported' for item in report['attempts']), 8)
        self.assertEqual(report['retries'], 0)
        self.assertEqual(report['usage']['prompt_tokens'], 140)
        self.assertIsNone(report['usage']['inference_cost_estimate'])
        for phase, evidence in model.calls:
            self.assertEqual(phase, 'data_plan')
            self.assertEqual(set(evidence), {'request', 'schema'})
            self.assertEqual(evidence['schema'], E.schema_evidence())
        for case in cases:
            self.assertNotIn(case['request'], saved)
            self.assertNotIn(case['request'], json.dumps(client.events, ensure_ascii=False))
        for event in client.events:
            self.assertEqual(event['task_id'], report['task_id'])
            self.assertEqual(event['trace_id'], report['trace_id'])

    def test_supported_paraphrases_are_new_and_four_refusals_are_fixed(self):
        cases = E.load_cases('requests')
        self.assertEqual({case['case_id']: case['expected_operation'] for case in cases}, E.REQUEST_EXPECTED)
        self.assertEqual(len({case['request'] for case in cases}), 7)
        self.assertFalse({case['request'] for case in cases} & {case['request'] for case in E.model_cases()})
        self.assertEqual(sum(case['expected_operation'] == 'unsupported' for case in cases), 4)
        self.assertEqual([case['expected_operation'] for case in cases[:3]], ['name_lengths', 'uppercase_names', 'row_count'])

    def test_unknown_suite_rejected_before_client_initialization_or_output(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(E, 'DataProvider') as provider, patch.object(E, 'agenthouse_module') as audit:
            output = Path(directory) / 'never-created'
            with self.assertRaisesRegex(ValueError, 'unknown Data Bear evaluation suite'):
                E.evaluate(config(), output, suite='arbitrary')
            provider.assert_not_called(); audit.assert_not_called()
            self.assertFalse(output.exists())

    def test_dataset_exact_keys_version_counts_and_expected_answers(self):
        variants = []
        for key, value in [('schema_version', True), ('schema_version', 2), ('suite', 'baseline'), ('cases', [])]:
            item = deepcopy(self.document); item[key] = value; variants.append(item)
        item = deepcopy(self.document); item['private_path'] = '/private'; variants.append(item)
        item = deepcopy(self.document); item['cases'][0]['sql'] = 'SELECT 1'; variants.append(item)
        item = deepcopy(self.document); del item['cases'][0]['expected_operation']; variants.append(item)
        item = deepcopy(self.document); item['cases'][0]['case_id'] = 'arbitrary'; variants.append(item)
        item = deepcopy(self.document); item['cases'][1]['case_id'] = item['cases'][0]['case_id']; variants.append(item)
        item = deepcopy(self.document); item['cases'][4]['expected_operation'] = 'row_count'; variants.append(item)
        for item in variants:
            with self.subTest(item=item), self.assertRaises(ValueError):
                E.validate_request_cases(item)

    def test_dataset_request_bound_plain_text_and_new_paraphrase(self):
        for value in ['', ' ' * 3, 'a' * 513, 'a\nb', 'a\x7fb', '\ud800', 12, None,
                      self.document['cases'][1]['request'], E.model_cases()[0]['request']]:
            item = deepcopy(self.document); item['cases'][0]['request'] = value
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                E.validate_request_cases(item)

    def test_duplicate_nonfinite_oversize_and_invalid_dataset_stop_before_clients(self):
        raw = json.dumps(self.document, ensure_ascii=False)
        samples = [raw[:-1] + ',"schema_version":1}', raw.replace('"schema_version": 1', '"schema_version": NaN'),
                   raw + '{}', ' ' * 16385, '{']
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'dataset.json'
            for sample in samples:
                path.write_text(sample)
                with self.subTest(sample=sample[:60]), patch.object(E, 'REQUEST_CASES', path), patch.object(E, 'DataProvider') as provider, patch.object(E, 'agenthouse_module') as audit:
                    with self.assertRaisesRegex(ValueError, 'fixed request evaluation dataset rejected'):
                        E.evaluate(config(), Path(directory) / 'never-created', suite='requests')
                    provider.assert_not_called(); audit.assert_not_called()

    def test_semantic_mismatch_and_free_sql_plan_cannot_pass_or_leak(self):
        for options in ({'wrong': True}, {'invalid': True}):
            model, client = Model(E.load_cases('requests'), **options), AuditClient()
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                report = self.evaluate(model, client, directory, suite='requests')
                saved = '\n'.join(path.read_text() for path in (Path(directory) / 'evidence').iterdir())
            self.assertEqual(report['result'], 'failed')
            self.assertEqual(report['passed_inferences'], 0)
            self.assertEqual(report['audited_inferences'], 14)
            self.assertNotIn('PRIVATE-RAW-MODEL-SQL', saved)

    def test_failed_usage_retained_and_audit_mismatch_stops_evaluation(self):
        cases = E.load_cases('requests')
        model, client = Model(cases, fail=True), AuditClient()
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(model, client, directory, suite='requests')
            saved = '\n'.join(path.read_text() for path in (Path(directory) / 'evidence').iterdir())
        self.assertEqual(report['result'], 'failed')
        self.assertEqual(report['audited_inferences'], 14)
        self.assertEqual(report['usage']['completion_tokens'], 70)
        self.assertNotIn('PRIVATE-RAW', saved)
        model = Model(cases)
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(model, AuditClient(wrong_readback=True), directory, suite='requests')
        self.assertEqual(report['result'], 'failed')
        self.assertEqual(report['audited_inferences'], 0)
        self.assertEqual(len(model.calls), 1)

    def test_cli_default_and_request_suite_and_unknown_rejected_before_setup(self):
        for arguments, suite in [([], 'baseline'), (['--suite', 'requests'], 'requests')]:
            returned = {'result': 'passed', 'suite': suite, 'attempts': [{'private': 'never-stdout'}]}
            with self.subTest(suite=suite), patch.object(sys, 'argv', ['evaluate_data.py'] + arguments), patch.object(E, 'load_config', return_value=config()), patch.object(E, 'evaluate', return_value=returned) as evaluate, contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(E.main(), 0)
                self.assertEqual(evaluate.call_args.kwargs['suite'], suite)
                self.assertEqual(json.loads(stdout.getvalue()), {'result': 'passed', 'suite': suite})
        with patch.object(sys, 'argv', ['evaluate_data.py', '--suite', 'other']), patch.object(E, 'load_config') as load, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                E.main()
            self.assertEqual(raised.exception.code, 2)
            load.assert_not_called()


if __name__ == '__main__':
    unittest.main()
