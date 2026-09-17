"""Offline quality-oracle/evaluator checks; all provider and audit calls are fake."""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tests.test_local_sre_llm import (EVALUATION as E, FixtureSelector, EvaluationClient,
                                     local_config, local_profile)
import sre_evaluation_cases as S
from sre_provider import SREProvider
from tools.local_experiment.evaluation import report as REPORT


def proposal(case):
    return FixtureSelector().complete('sre_triage', case['evidence'])['content']


class SignalCasesTests(unittest.TestCase):
    def test_fixed_inputs_literal_oracles_and_registered_hash(self):
        cases = S.signal_cases()
        self.assertEqual(tuple(c['case_id'] for c in cases), S.SIGNAL_CASE_IDS)
        self.assertEqual(S.dataset_sha256(), S.SIGNAL_DATASET_SHA256)
        self.assertEqual(S.SIGNAL_DATASET_SHA256,
                         'b2757e1e198c1212c0e21459a6f882967168db34e6371a3c647337922175c732')
        self.assertEqual([c['expected']['assessment'] for c in cases], [
            'healthy-observed-scope', 'unknown', 'healthy-observed-scope', 'healthy-observed-scope',
            'attention-required', 'attention-required', 'attention-required', 'attention-required'])
        for case in cases:
            self.assertEqual(S.validate_case(case), case)
            self.assertEqual(S.validate_signal_triage(case, proposal(case)), proposal(case))
        cases[0]['evidence']['observations']['pods']['resource_usage']['sample']['cpu_nanocores'] = '1'
        cases[0]['expected']['assessment'] = 'unknown'
        self.assertEqual(S.dataset_sha256(), S.SIGNAL_DATASET_SHA256)

    def test_default_sixteen_cases_are_byte_for_byte_unchanged(self):
        self.assertEqual(len(E.cases()), 16)
        self.assertEqual(E.cases(), E.cases('baseline'))
        self.assertEqual(S.dataset_sha256(E.cases()),
                         '2cc8d128b045a6e1171c7b0ce87734710b6564dca27bf7712cd094ee4c46eb77')
        self.assertTrue(all('expected' not in c for c in E.cases()))

    def test_missing_zero_and_noisy_logs_are_controlled_contrasts(self):
        cases = S.signal_cases()
        zero, missing, log_missing, noisy = [c['evidence'] for c in cases[:4]]
        self.assertEqual(zero['observations']['pods']['resource_usage']['sample']['cpu_nanocores'], '0')
        self.assertIsNone(missing['observations']['pods']['resource_usage']['sample'])
        self.assertEqual(zero['health'], log_missing['health'])
        self.assertEqual(zero['health'], noisy['health'])
        self.assertNotEqual(zero['health'], missing['health'])
        for evidence in (zero, missing, log_missing, noisy):
            self.assertEqual(evidence['findings'], [])
        self.assertEqual(log_missing['observations']['pods']['log_signals']['state'], 'unknown')
        self.assertEqual(noisy['observations']['pods']['log_signals']['fragment']['error_header_lines'], 50)
        self.assertGreater(int(noisy['observations']['pods']['resource_usage']['sample']['cpu_nanocores']), 0)

    def test_critical_coverage_has_no_check_or_finding_order_requirement(self):
        case = S.signal_cases()[-1]
        value = proposal(case)
        self.assertEqual({row['tool'] for row in value['checks']},
                         {'k8s_read_pods', 'local_platform_status'})
        value['checks'].reverse()
        for row in value['checks']:
            row['finding_ids'].reverse()
        self.assertEqual(S.validate_signal_triage(case, value), value)
        for index in range(2):
            bad = copy.deepcopy(value)
            bad['checks'].pop(index)
            with self.assertRaises(ValueError):
                S.validate_signal_triage(case, bad)
        bad = copy.deepcopy(value)
        next(x for x in bad['checks'] if x['tool'] == 'k8s_read_pods')['finding_ids'].pop()
        with self.assertRaises(ValueError):
            S.validate_signal_triage(case, bad)

    def test_historical_oom_is_not_current_failure_and_allows_warning_read_choice(self):
        case = S.signal_cases()[6]
        self.assertEqual({x['severity'] for x in case['evidence']['findings']}, {'warning'})
        self.assertEqual(case['expected']['findings'][0]['observation'], 'last-termination')
        alternative = {'assessment': 'attention-required', 'root_cause': 'undetermined',
                       'checks': [{'tool': 'k8s_read_events', 'finding_ids': ['f02']}]}
        self.assertEqual(S.validate_signal_triage(case, alternative), alternative)
        current = S.signal_cases()[5]['evidence']['findings']
        self.assertEqual(current[0]['code'], 'CONTAINER_EXIT_NONZERO')
        self.assertNotIn('CONTAINER_OOM_REPORTED', [x['code'] for x in current])

    def test_literal_oracle_drift_and_invalid_triage_are_rejected(self):
        case = S.signal_cases()[0]
        bad_case = copy.deepcopy(case)
        bad_case['expected']['assessment'] = 'unknown'
        with self.assertRaises(ValueError):
            S.validate_case(bad_case)
        for mutation in (
            lambda x: x.update(assessment='attention-required'),
            lambda x: x.update(root_cause='PRIVATE-UNFOUNDED-CAUSE'),
            lambda x: x.update(remediation='PRIVATE-COMMAND'),
            lambda x: x.update(checks=[{'tool': 'k8s_read_pods', 'finding_ids': ['f01']}]),
            lambda x: x.update(checks=[{'tool': 'exec', 'finding_ids': ['f01']}]),
        ):
            value = proposal(case)
            mutation(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                S.validate_signal_triage(case, value)

    def test_actual_provider_requests_are_bounded_and_exclude_oracle(self):
        model = SREProvider(local_profile())
        sizes = []
        for case in S.signal_cases():
            body = model.request_body('sre_triage', case['evidence'])
            sizes.append(len(json.dumps(body).encode()))
            user = json.loads(body['messages'][1]['content'])
            self.assertEqual(set(user), {'phase', 'evidence'})
            self.assertEqual(user['evidence'], case['evidence'])
            self.assertNotIn('expected', user)
            self.assertNotIn('required_tools', json.dumps(user))
            self.assertEqual(body['temperature'], 0)
        self.assertEqual(sizes, [5793, 4648, 5193, 5883, 6032, 5319, 6553, 5474])
        self.assertTrue(all(size <= 8192 for size in sizes))


class SignalEvaluatorTests(unittest.TestCase):
    def evaluate(self, output, model=None, client=None, **kwargs):
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('network forbidden')):
            return E.evaluate(local_config(), output, model=model or FixtureSelector(),
                              client=client or EvaluationClient(), audit_module=E.agenthouse_module(),
                              suite=S.SIGNAL_SUITE, **kwargs)

    def test_one_profile_sixteen_fake_calls_and_audits_only_safe_triage_saved(self):
        model, client = FixtureSelector(), EvaluationClient()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'evidence'
            report = self.evaluate(output, model, client)
            saved = json.loads((output / 'result.json').read_text())
            lines = [json.loads(x) for x in (output / 'attempts.jsonl').read_text().splitlines()]
        self.assertEqual(saved, report)
        self.assertEqual(report['suite'], S.SIGNAL_SUITE)
        self.assertEqual(report['dataset_sha256'], S.SIGNAL_DATASET_SHA256)
        self.assertEqual(report['result'], 'passed')
        self.assertEqual(report['requested_inferences'], 16)
        self.assertEqual(report['attempted_inferences'], 16)
        self.assertEqual(report['passed_inferences'], 16)
        self.assertEqual(report['audited_inferences'], 16)
        self.assertEqual(report['retries'], 0)
        self.assertEqual(len(client.events), 16)
        self.assertEqual(model.calls, [('sre_triage', c['evidence']) for c in S.signal_cases() for _ in range(2)])
        for line in lines:
            case = next(c for c in S.signal_cases() if c['case_id'] == line['case_id'])
            self.assertEqual(S.validate_signal_triage(case, line['triage']), line['triage'])
            self.assertEqual(line['selected_tools'], [x['tool'] for x in line['triage']['checks']])
            self.assertEqual(set(line['triage']), {'assessment', 'checks', 'root_cause'})
        self.assertIsNone(report['usage']['inference_cost_estimate'])

    def test_real_report_normalizes_evaluator_and_rejects_missing_critical_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            value = self.evaluate(Path(directory) / 'evidence')
        normalized = REPORT.normalize(value, 'sre')
        self.assertEqual(normalized['requested_inferences'], 16)
        self.assertEqual(normalized['case_ids'], list(S.SIGNAL_CASE_IDS))
        self.assertEqual(normalized['reported_result'], 'passed')
        bad = copy.deepcopy(value)
        attempt = next(a for a in bad['attempts'] if a['case_id'] == 'signals_dual_critical')
        next(c for c in attempt['triage']['checks'] if c['tool'] == 'k8s_read_pods')['finding_ids'].pop()
        with self.assertRaises(REPORT.ReceiptError):
            REPORT.normalize(bad, 'sre')

    def test_default_saved_shape_has_no_new_profile_or_triage_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            value = E.evaluate(local_config(), Path(directory) / 'evidence', model=FixtureSelector(),
                               client=EvaluationClient(), audit_module=E.agenthouse_module())
        self.assertEqual(value['requested_inferences'], 32)
        self.assertNotIn('suite', value)
        self.assertNotIn('dataset_sha256', value)
        self.assertTrue(all('triage' not in x for x in value['attempts']))

    def test_critical_omission_fails_without_extra_calls_or_fabricated_triage(self):
        model = FixtureSelector(omit_critical=True)
        with tempfile.TemporaryDirectory() as directory:
            value = self.evaluate(Path(directory) / 'evidence', model)
        self.assertEqual(len(model.calls), 16)
        failed = [x for x in value['attempts'] if x['result'] == 'failed']
        self.assertEqual(len(failed), 6)
        self.assertTrue(all(x['failure_code'] == 'triage_contract_rejected' for x in failed))
        self.assertTrue(all('triage' not in x and 'selected_tools' not in x for x in failed))
        self.assertEqual(value['result'], 'failed')

    def test_rejected_free_text_is_not_saved(self):
        model = FixtureSelector()
        original = model.complete
        def malicious(phase, evidence):
            response = original(phase, evidence)
            response['content']['root_cause'] = 'PRIVATE-RAW-MODEL-RESPONSE'
            return response
        model.complete = malicious
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'evidence'
            value = self.evaluate(output, model)
            content = ''.join(p.read_text() for p in output.iterdir())
        self.assertEqual(value['passed_inferences'], 0)
        self.assertNotIn('PRIVATE-RAW-MODEL-RESPONSE', content)
        self.assertNotIn('triage', ''.join(json.dumps(x) for x in value['attempts']).replace('triage_contract_rejected', ''))

    def test_invalid_profile_hash_or_request_size_is_rejected_before_clients_and_output(self):
        for failure in ('suite', 'hash', 'size'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'evidence'
                with patch.object(E, 'agenthouse_module') as audit:
                    if failure == 'suite':
                        with self.assertRaises(ValueError):
                            E.evaluate({}, output, suite='arbitrary')
                    elif failure == 'hash':
                        with patch.object(E, 'SIGNAL_DATASET_SHA256', '0' * 64), self.assertRaises(ValueError):
                            E.evaluate(local_config(), output, suite=S.SIGNAL_SUITE)
                    else:
                        with patch.object(E.SREProvider, 'request_body', side_effect=ValueError('size')), self.assertRaises(ValueError):
                            E.evaluate(local_config(), output, suite=S.SIGNAL_SUITE)
                    audit.assert_not_called()
                self.assertFalse(output.exists())

    def test_failed_provider_keeps_usage_null_or_known_without_retry(self):
        model = FixtureSelector(fail_first=True)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'evidence'
            value = self.evaluate(output, model)
            content = ''.join(p.read_text() for p in output.iterdir())
        first = value['attempts'][0]
        self.assertEqual(first['metrics']['prompt_tokens'], 123)
        self.assertNotIn('triage', first)
        self.assertNotIn('private-model-error', content)
        self.assertNotIn('private-output', content)
        self.assertEqual(len(model.calls), 16)
        self.assertEqual(value['passed_inferences'], 15)

    def test_audit_failure_stops_before_another_inference(self):
        model = FixtureSelector()
        with tempfile.TemporaryDirectory() as directory:
            value = self.evaluate(Path(directory) / 'evidence', model, EvaluationClient(insert_fails=True))
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(value['requested_inferences'], 16)
        self.assertEqual(value['attempted_inferences'], 1)
        self.assertEqual(value['audited_inferences'], 0)
        self.assertEqual(value['result'], 'failed')

    def test_cancel_is_original_and_partial_failure_save_cannot_replace_it(self):
        for failure in ('model', 'audit'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'evidence'
                cancel = KeyboardInterrupt('PRIVATE-CANCEL')
                model, client = FixtureSelector(), EvaluationClient()
                if failure == 'model':
                    model.complete = Mock(side_effect=cancel)
                else:
                    query = client.query
                    client.query = lambda sql: (_ for _ in ()).throw(cancel) if sql.startswith('INSERT') else query(sql)
                write = E.private_write
                def failing_save(path, *args, **kwargs):
                    if output.exists() and (output / 'result.json').exists():
                        if failure == 'model' or path.name == 'attempts.jsonl':
                            raise OSError('PRIVATE-SAVE-FAILURE')
                    return write(path, *args, **kwargs)
                with patch.object(E, 'private_write', side_effect=failing_save), self.assertRaises(KeyboardInterrupt) as caught:
                    self.evaluate(output, model, client)
                self.assertIs(caught.exception, cancel)
                saved = json.loads((output / 'result.json').read_text())
                self.assertEqual(saved['result'], 'failed')
                self.assertNotIn('PRIVATE', json.dumps(saved))

    def test_existing_output_refused_without_call_and_cli_suite_forwarded(self):
        model = FixtureSelector()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileExistsError):
                self.evaluate(Path(directory), model)
        self.assertEqual(model.calls, [])
        with patch('sys.argv', ['evaluate_sre', '--suite', S.SIGNAL_SUITE]), patch.object(E, 'load_config', return_value=local_config()), \
             patch.object(E, 'evaluate', return_value={'result': 'passed'}) as evaluate, patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(E.main(), 0)
        self.assertEqual(evaluate.call_args.kwargs, {'suite': S.SIGNAL_SUITE})
        with patch('sys.argv', ['evaluate_sre', '--suite', 'invalid']), patch.object(E, 'load_config') as config, \
             patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit):
            E.main()
        config.assert_not_called()


if __name__ == '__main__':
    unittest.main()
