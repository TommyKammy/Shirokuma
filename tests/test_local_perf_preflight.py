"""Per-run preflight preparation remains complete and fresh; no live engines."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmarks.local_perf import runner
from benchmarks.local_perf.capacity import Campaign, write_json
from test_local_perf_bindings import Local, bound
from test_local_perf_runner import ScriptedTrino
import test_local_perf_public as public_tests
from test_local_perf_public import ScriptedPublic


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.campaign = Campaign.create(self.root / 'campaign')
        self.small = dict(format=1, dataset_id='new', queries=[dict(id='new', sql='SELECT 1')])

    def tearDown(self):
        self.tmp.cleanup()

    def saved(self, attempts=121, bound_run=False):
        manifest = bound() if bound_run else dict(format=1, dataset_id='old')
        if bound_run:
            # Representative large binding; each pin must contribute to its hash.
            manifest['snapshot_pins']['tables'] = [dict(name='table%02d' % i, snapshot_id=str(i)) for i in range(24)]
            manifest['snapshot_set_sha256'] = runner.sql_identity(json.dumps(manifest['snapshot_pins'], sort_keys=True, separators=(',', ':'), ensure_ascii=False))
            template = manifest['queries'][0]
        else:
            template = dict(sql='SELECT 1')
        manifest['queries'] = [dict(copy.deepcopy(template), id='q%d' % i) for i in range(1, 100)]
        for q in manifest['queries']:
            runner.validate_sql_contract(manifest, q)
        adapter = ScriptedTrino('ok')
        identity = runner.adapter_identity(adapter)
        root = self.campaign.own('old', 'run')
        report = dict(query_manifest_sha256=runner.sql_identity(json.dumps(manifest, sort_keys=True)),
                      adapter=adapter.name, adapter_identity=identity, sql_variant='sql', attempts=[])
        for i in range(attempts):
            q = manifest['queries'][97 if i == attempts - 1 else 0]
            intent = dict(status='SUCCESS', request_id='request%d' % i, query_id=q['id'], dataset_id=manifest['dataset_id'],
                          sql=q['sql'], sql_sha256=q['sql_sha256'], sql_variant='sql', adapter=adapter.name,
                          adapter_identity=identity, handle=dict(id='backend%d' % i))
            if bound_run:
                intent.update(target_sql=q['sql'], target_sql_sha256=q['sql_sha256'],
                              logical_sql_sha256=q['logical_sha256'], reference_sql_sha256=q['reference_sha256'])
            path = root / ('attempt%03d' % i) / 'attempt.json'
            path.parent.mkdir()
            write_json(path, intent)
            report['attempts'].append(dict(path=path.parent.name, request_id=intent['request_id']))
        write_json(root / 'queries.json', manifest)
        write_json(root / 'run.json', report)
        return root, manifest, report

    def dispatch(self, name='next'):
        adapter = Local()
        result = runner.run(self.campaign, name, self.small, adapter, reference_label='scripted metadata')
        self.assertEqual(adapter.sent, ['SELECT 1'])
        return result

    def blocked(self, error=ValueError):
        adapter = ScriptedTrino('ok')
        with self.assertRaises(error):
            runner.run(self.campaign, 'blocked', self.small, adapter, reference_label='scripted')
        self.assertEqual(adapter.posts, 0)

    def rewrite(self, root, manifest, report, outer=True):
        if outer:
            report['query_manifest_sha256'] = runner.sql_identity(json.dumps(manifest, sort_keys=True))
        write_json(root / 'queries.json', manifest)
        write_json(root / 'run.json', report)

    def test_one_read_and_contract_pass_per_saved_run_not_per_attempt(self):
        root, manifest, report = self.saved(bound_run=True)
        reads = []
        read_text, read_bytes = Path.read_text, Path.read_bytes
        def text(path, *args, **kwargs):
            reads.append(path)
            return read_text(path, *args, **kwargs)
        def raw(path, *args, **kwargs):
            reads.append(path)
            return read_bytes(path, *args, **kwargs)
        with patch.object(Path, 'read_text', text), patch.object(Path, 'read_bytes', raw), patch.object(
                runner, '_validate_query_sql_contract', wraps=runner._validate_query_sql_contract) as contracts, patch.object(
                runner, '_validate_snapshot_binding', wraps=runner._validate_snapshot_binding) as bindings, patch.object(
                runner, '_validate_frozen_intent', wraps=runner._validate_frozen_intent) as intents, patch.object(
                runner, 'sql_identity', wraps=runner.sql_identity) as hashes:
            self.dispatch()
        self.assertEqual(reads.count(root / 'queries.json'), 1)
        self.assertEqual(reads.count(root / 'run.json'), 1)
        self.assertEqual(sum(p.name == 'attempt.json' for p in reads), 121)
        self.assertEqual(contracts.call_count, 100)  # 99 old queries and one new query.
        self.assertEqual(bindings.call_count, 2)
        self.assertEqual(intents.call_count, 121)
        pins = json.dumps(manifest['snapshot_pins'], sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        self.assertEqual(sum(c.args[0] == pins for c in hashes.call_args_list), 1)
        canonical = json.dumps(manifest, sort_keys=True)
        self.assertEqual(sum(c.args[0] == canonical for c in hashes.call_args_list), 1)
        self.assertEqual(sum(c.args[0] == manifest['queries'][0]['sql'] for c in hashes.call_args_list), 99)

    def test_unattempted_last_query_tampering_even_with_updated_outer_hash(self):
        root, manifest, report = self.saved()
        for field, value in [('sql', 'SELECT 99'), ('sql_sha256', 'wrong')]:
            for outer in (False, True):
                bad, r = copy.deepcopy(manifest), copy.deepcopy(report)
                bad['queries'][-1][field] = value
                self.rewrite(root, bad, r, outer)
                self.blocked()

    def test_duplicate_unattempted_query_id_rejected(self):
        root, manifest, report = self.saved()
        manifest['queries'][-1]['id'] = manifest['queries'][-2]['id']
        self.rewrite(root, manifest, report)
        self.blocked()

    def test_zero_attempts_still_validates_all_queries_and_binding(self):
        root, manifest, report = self.saved(attempts=0, bound_run=True)
        bad = copy.deepcopy(manifest)
        bad['queries'][-1]['reference_sha256'] = 'wrong'
        self.rewrite(root, bad, report)
        self.blocked()
        self.rewrite(root, manifest, report)
        self.dispatch()

    def test_late_attempt_identity_sql_and_bound_fields_all_checked(self):
        root, _, _ = self.saved(bound_run=True)
        path = root / 'attempt120/attempt.json'
        original = json.loads(path.read_text())
        for key, value in [('query_id', 'unknown'), ('dataset_id', 'wrong'), ('sql', 'SELECT 88'),
                           ('sql_sha256', 'wrong'), ('sql_variant', 'reference_sql'), ('adapter', 'wrong'),
                           ('adapter_identity', dict(kind='trino-loopback', user='other')),
                           ('target_sql', 'SELECT 9'), ('target_sql_sha256', 'wrong'),
                           ('logical_sql_sha256', 'wrong'), ('reference_sql_sha256', 'wrong')]:
            with self.subTest(key=key):
                write_json(path, dict(original, **{key: value}))
                self.blocked()
        write_json(path, original)
        self.dispatch()

    def test_unresolved_statuses_and_scoped_terminal_reconciliation(self):
        root, _, _ = self.saved(attempts=1)
        path = root / 'attempt000/attempt.json'
        original = json.loads(path.read_text())
        for status in ('INTENT', 'RUNNING', 'UNKNOWN'):
            intent = dict(original, status=status)
            write_json(path, intent)
            self.blocked(RuntimeError)
        sidecar = path.parent / 'reconciliation.json'
        record = dict(intent, status='FAILED', reconciliation='CANCELED')
        write_json(sidecar, dict(record, request_id='wrong'))
        self.blocked(RuntimeError)
        write_json(sidecar, record)
        self.dispatch()

    def test_next_invocation_rereads_manifest_and_pins(self):
        root, manifest, report = self.saved(bound_run=True)
        self.dispatch()
        manifest['snapshot_pins']['tables'][-1]['snapshot_id'] = 'mutated-late-pin'
        self.rewrite(root, manifest, report)
        self.blocked()

    def test_public_sql_contract_rechecks_binding_each_call_and_report_variant(self):
        manifest = bound()
        runner.validate_sql_contract(manifest, manifest['queries'][0])
        manifest['snapshot_pins']['tables'][0]['snapshot_id'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'snapshot fingerprint'):
            runner.validate_sql_contract(manifest, manifest['queries'][0])
        root, manifest, report = self.saved(bound_run=True)
        report['sql_variant'] = 'reference_sql'
        write_json(root / 'run.json', report)
        self.blocked()

    def test_public_frozen_wrapper_is_full_fresh_and_does_not_mutate(self):
        snapshot = dict(format=1, dataset_id='d', queries=[dict(id='q', sql='SELECT 1')])
        report = dict(query_manifest_sha256=runner.sql_identity(json.dumps(snapshot, sort_keys=True)))
        intent = dict(query_id='q', dataset_id='d', sql='SELECT 1', sql_sha256=runner.sql_identity('SELECT 1'))
        original = copy.deepcopy(snapshot)
        with patch.object(runner, '_validate_query_sql_contract', wraps=runner._validate_query_sql_contract) as validate:
            runner.validate_frozen(snapshot, report, intent)
            runner.validate_frozen(snapshot, report, intent)
        self.assertEqual(validate.call_count, 2)
        self.assertEqual(snapshot, original)
        snapshot['queries'].append(dict(id='unattempted', sql='SELECT 2', sql_sha256='bad'))
        report['query_manifest_sha256'] = runner.sql_identity(json.dumps(snapshot, sort_keys=True))
        with self.assertRaises(ValueError):
            runner.validate_frozen(snapshot, report, intent)


class RejectionPreflightTests(unittest.TestCase):
    """Reuse the existing scoped legacy-audit fixture without inheriting its suite."""
    def test_prepared_audit_reuses_manifest_but_revalidates_raw_attempt_and_evidence(self):
        fixture = public_tests.PublicTests('test_legacy_operator_audit_sidecar_preserves_original_bytes_and_reuses_without_http')
        fixture.setUp()
        try:
            adapter, path, audit, evidence = fixture.legacy_audit()
            runner.reconcile(path, adapter, rejection_evidence=audit)
            run_root = path.parent.parent
            read_text, read_bytes, reads = Path.read_text, Path.read_bytes, []
            def text(p, *args, **kwargs):
                reads.append(p)
                return read_text(p, *args, **kwargs)
            def raw(p, *args, **kwargs):
                reads.append(p)
                return read_bytes(p, *args, **kwargs)
            with patch.object(Path, 'read_text', text), patch.object(Path, 'read_bytes', raw):
                fixture.run_adapter(ScriptedPublic(), 'fresh')
            self.assertEqual(reads.count(run_root / 'queries.json'), 1)
            self.assertEqual(reads.count(run_root / 'run.json'), 1)
            self.assertIn(path, reads)  # Original byte hash is still checked.
            self.assertIn(Path(evidence['evidence_files'][0]['path']), reads)
            sidecar = path.parent / 'reconciliation.json'
            original = json.loads(sidecar.read_text())
            for key, value in [('request_id', 'wrong'), ('manifest_sha256', 'wrong'), ('original_attempt_sha256', 'wrong')]:
                bad = copy.deepcopy(original)
                bad['no_dispatch_evidence'][key] = value
                write_json(sidecar, bad)
                with self.subTest(key=key), self.assertRaises(RuntimeError):
                    fixture.run_adapter(ScriptedPublic(), 'blocked')
            write_json(sidecar, original)
            manifest_path = run_root / 'queries.json'
            manifest_bytes = manifest_path.read_bytes()
            manifest_path.write_bytes(manifest_bytes + b' ')
            with self.assertRaises(RuntimeError):
                fixture.run_adapter(ScriptedPublic(), 'blocked')
            manifest_path.write_bytes(manifest_bytes)
            Path(evidence['evidence_files'][0]['path']).write_text('{}')
            with self.assertRaises(RuntimeError):
                fixture.run_adapter(ScriptedPublic(), 'blocked')
        finally:
            fixture.tearDown()


if __name__ == '__main__':
    unittest.main()
