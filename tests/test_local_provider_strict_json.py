"""Strict local completion JSON checks; no inference or HTTP traffic."""
import json
import unittest

from tools.local_experiment.llm.providers import ProviderError, parse_completion

MODEL = 'qwen3.8-flash-next'


def response(content='{"ok":true}', usage=None, **extra):
    value = dict(model=MODEL, choices=[dict(finish_reason='stop', message=dict(content=content))])
    if usage is not None:
        value['usage'] = usage
    value.update(extra)
    return json.dumps(value, separators=(',', ':')).encode()


class StrictCompletionTests(unittest.TestCase):
    def rejected(self, raw):
        with self.assertRaises(ProviderError) as caught:
            parse_completion(raw, MODEL, 17)
        self.assertNotIn('private-value', str(caught.exception))
        self.assertEqual(caught.exception.metrics['duration_ms'], 17)
        self.assertIsNone(caught.exception.metrics['inference_cost_estimate'])
        return caught.exception.metrics

    def test_valid_structured_content_and_known_zero_counts_are_preserved(self):
        value = parse_completion(response('{"ok":true,"nested":{"score":1.25}}',
                                          {'prompt_tokens':0, 'completion_tokens':9}), MODEL, 17)
        self.assertEqual(value['content'], dict(ok=True, nested=dict(score=1.25)))
        self.assertEqual((value['prompt_tokens'], value['completion_tokens']), (0, 9))
        self.assertIsNone(value['inference_cost_estimate'])

    def test_outer_duplicate_and_nested_duplicate_rejected_with_unknown_usage(self):
        raw = response(usage={'prompt_tokens':3, 'completion_tokens':4})
        cases = [raw.replace(b'"model":', b'"model":"private-value","model":', 1),
                 raw.replace(b'"prompt_tokens":3', b'"prompt_tokens":3,"prompt_tokens":4'),
                 raw.replace(b'"finish_reason":"stop"', b'"finish_reason":"stop","finish_reason":"stop"')]
        for value in cases:
            with self.subTest(raw=value):
                metrics = self.rejected(value)
                self.assertIsNone(metrics['prompt_tokens']); self.assertIsNone(metrics['completion_tokens'])

    def test_content_duplicate_at_any_depth_rejected_and_known_usage_retained(self):
        for content in ('{"ok":true,"ok":false}', '{"item":{"id":1,"id":2}}'):
            with self.subTest(content=content):
                metrics = self.rejected(response(content, {'prompt_tokens':3, 'completion_tokens':4}))
                self.assertEqual((metrics['prompt_tokens'], metrics['completion_tokens']), (3, 4))

    def test_nonfinite_and_overflow_outer_numbers_rejected(self):
        for literal in ('NaN', 'Infinity', '-Infinity', '1e999'):
            raw = response(usage={'prompt_tokens':3, 'completion_tokens':4})
            raw = raw[:-1] + b',"extra":' + literal.encode() + b'}'
            with self.subTest(literal=literal):
                metrics = self.rejected(raw)
                self.assertIsNone(metrics['prompt_tokens']); self.assertIsNone(metrics['completion_tokens'])

    def test_nonfinite_and_overflow_content_numbers_rejected_with_partial_usage(self):
        for literal in ('NaN', 'Infinity', '-Infinity', '1e999'):
            with self.subTest(literal=literal):
                metrics = self.rejected(response('{"score":' + literal + '}', {'prompt_tokens':0}))
                self.assertEqual(metrics['prompt_tokens'], 0); self.assertIsNone(metrics['completion_tokens'])

    def test_model_mismatch_preserves_measured_usage(self):
        metrics = self.rejected(response(usage={'completion_tokens':7}, model='private-value'))
        self.assertIsNone(metrics['prompt_tokens']); self.assertEqual(metrics['completion_tokens'], 7)

    def test_unknown_usage_remains_unknown_and_invalid_counts_preserve_valid_other(self):
        value = parse_completion(response(), MODEL, 17)
        self.assertIsNone(value['prompt_tokens']); self.assertIsNone(value['completion_tokens'])
        metrics = self.rejected(response(usage={'prompt_tokens':True, 'completion_tokens':2}))
        self.assertIsNone(metrics['prompt_tokens']); self.assertEqual(metrics['completion_tokens'], 2)

    def test_incomplete_and_malformed_content_do_not_erase_measured_usage(self):
        for content in ('private-value', '[]'):
            with self.subTest(content=content):
                metrics = self.rejected(response(content, {'prompt_tokens':3, 'completion_tokens':0}))
                self.assertEqual((metrics['prompt_tokens'], metrics['completion_tokens']), (3, 0))


if __name__ == '__main__': unittest.main()
