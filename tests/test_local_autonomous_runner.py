"""Offline, disposable Git/CLI fixtures; never invoke the actual model."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from tools.local_experiment import autonomous_runner as R
from tools.local_experiment import astra_preflight as A


FAKE_CLI = '''#!/usr/bin/env python3
import json,os,pathlib,re,subprocess,sys,time
if sys.argv[1:]==['auth','list','--format','json']:
    mode=os.environ.get('FAKE_AUTH_MODE','oauth')
    if mode=='legacy_text':
        print('OpenAI OAuth')
        sys.exit(0)
    if mode=='expires':
        counter=pathlib.Path.cwd()/'tmp/auth-count'
        counter.parent.mkdir(exist_ok=True)
        count=int(counter.read_text())+1 if counter.exists() else 1
        counter.write_text(str(count))
        mode='oauth' if count==1 else 'missing'
    accounts={'oauth':[{'method':'oauth'}], 'missing':[],
              'api':[{'method':'key'}], 'multiple':[{'method':'oauth'},{'method':'key'}],
              'untyped':[{'label':'connected'}]}[mode]
    integrations=[{'id':'openai','connections':accounts}] if accounts else []
    print(json.dumps(integrations))
    sys.exit(0)
assert sys.argv[1:6]==['run','--model','openai/fixture','--format','json']
match=re.search(r'^CONTROL_JSON=(.*)$',sys.argv[6],re.M)
assert match is not None
c=json.loads(match.group(1));root=pathlib.Path.cwd();mode=os.environ.get('FAKE_OPENCODE_MODE','continue')
if mode=='slow':
    time.sleep(20)
if mode=='failure':
    sys.exit(3)
if mode not in ('stop','no_commit'):
    subprocess.run(['git','-C',str(root),'commit','--allow-empty','--quiet','-m','fixture increment'],check=True)
if mode=='dirty':
    (root/'unfinished.txt').write_text('preserve me')
if mode=='missing' or mode=='dirty':
    sys.exit(0)
head=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
checkpoint=root/c['checkpoint']
if mode=='symlink':
    checkpoint.symlink_to(root/'AGENTS.md')
    sys.exit(0)
value=dict(schema_version=1,nonce=c['nonce'],iteration=c['iteration'],head=head,
           decision='stop' if mode=='stop' else 'continue',
           reason='no_safe_work' if mode=='stop' else 'more_work')
fd=os.open(checkpoint,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as out:json.dump(value,out)
print(json.dumps({'type':'fixture','iteration':c['iteration']}))
'''


class AutonomousRunnerTests(unittest.TestCase):
    model = 'openai/fixture'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=R.ROOT / 'tmp/local-experiment')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'checkout'
        self.root.mkdir(mode=0o700)
        self.vault = Path(self.temp.name) / 'handoff.md'
        self.vault.write_text('saved handoff')
        (self.root / 'AGENTS.md').write_text('fixture instructions')
        docs = self.root / 'docs/design/04_Development'
        docs.mkdir(parents=True)
        (docs / '04B_Autonomous_Local_Development.md').write_text('fixture scope')
        (self.root / '.gitignore').write_text('tmp/\n')
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('checkout', '-q', '-b', R.BRANCH)
        self.git('add', 'AGENTS.md', '.gitignore', 'docs')
        self.git('commit', '-q', '-m', 'fixture baseline')
        self.cli = Path(self.temp.name) / 'fake-opencode'
        self.cli.write_text(FAKE_CLI)
        self.cli.chmod(0o700)

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def write_mission(self):
        directory = self.root / 'tmp/local-experiment/gate-batches'
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        (self.root / 'tmp').chmod(0o700)
        (self.root / 'tmp/local-experiment').chmod(0o700)
        parent = self.root / 'tmp/local-experiment/autonomous-runner'
        parent.mkdir(mode=0o700, exist_ok=True)
        (parent / R.FIRST_RUN).mkdir(mode=0o700, exist_ok=True)
        mission = dict(schema_version=1, wp_id=R.MISSION_WP,
                       gate_id='L0_GITOPS_READINESS', head_before=self.git('rev-parse', 'HEAD'),
                       baseline='The dedicated cluster has no fresh live gate observations.',
                       target_evidence='Record dedicated scope and fresh controller status or a concrete blocker.',
                       allowed_scope='Inspect only isolated disposable fixture state in this test.',
                       stop_conditions='Stop on unknown retained state, auth change or any failed request.',
                       recovery='Preserve old fixture evidence and restore only test-owned state.')
        path = directory / 'mission.json'
        path.write_text(json.dumps(mission))
        path.chmod(0o600)
        return path

    def invoke_fixture(self, *args, **kwargs):
        path = self.write_mission()
        kwargs['mission'] = R.mission_file(path, self.root, self.git('rev-parse', 'HEAD'))
        with mock.patch.object(R, 'gate_preflight'):
            return R.run(*args, **kwargs)

    def write_private_json(self, path, value):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def test_gate_preflight_refuses_unreviewed_source_unknown_run_and_spent_budget(self):
        self.write_mission()
        head = self.git('rev-parse', 'HEAD')
        base = self.root / 'tmp/local-experiment/autonomous-runner'
        with self.assertRaisesRegex(R.RunnerError, 'REVIEW_HOLD_UNVERIFIED'):
            R.gate_preflight(self.root, head, 1, 1)
        self.write_private_json(base / 'cap-1000-bridge/bridge-stop.json',
                                {'source_run': R.SOURCE_RUN, 'reason': 'POLICY_REVIEW_HOLD'})
        source = base / R.SOURCE_RUN
        self.write_private_json(source / 'summary.json', {'schema_version': 1,
                                'result': 'stopped', 'reason': 'run_budget_reached', 'attempts': 240})
        self.write_private_json(source / 'attempt-240/result.json',
                                {'head_after': head, 'reason': 'more_work'})
        self.write_private_json(source / 'attempt-240/checkpoint.json',
                                {'decision': 'continue', 'reason': 'more_work',
                                 'head': head, 'iteration': 240})
        with self.assertRaisesRegex(R.RunnerError, 'GATE_NOT_RECONCILED'):
            R.gate_preflight(self.root, head, 1, 1)
        self.write_private_json(self.root / 'tmp/local-experiment/gate-batches/ready.json',
                                {'schema_version': 1, 'source_run': R.SOURCE_RUN,
                                 'source_head': head, 'series_started': 500,
                                 'last_session_id': 'ses_fixture123',
                                 'server_outcome': 'succeeded_idle_nonactive'})
        R.gate_preflight(self.root, head, 6, 1)
        unknown = base / 'gate-unknown'
        unknown.mkdir(mode=0o700)
        with self.assertRaisesRegex(R.RunnerError, 'PRIOR_GATE_RUN_UNVERIFIED'):
            R.gate_preflight(self.root, head, 1, 1)
        unknown.rmdir()  # only disposable fixture-owned directory
        with mock.patch.object(R, 'SERIES_LIMIT', 505):
            with self.assertRaisesRegex(R.RunnerError, 'SERIES_BUDGET_EXCEEDED'):
                R.gate_preflight(self.root, head, 6, 1)
        self.write_private_json(base / 'cap-1000-bridge/bridge-stop.json',
                                {'source_run': R.SOURCE_RUN, 'reason': 'OPENCODE_RUN_FAILED'})
        with self.assertRaisesRegex(R.RunnerError, 'REVIEW_HOLD_UNVERIFIED'):
            R.gate_preflight(self.root, head, 1, 1)

    def test_resolved_safety_stop_needs_terminal_review_and_preserves_original(self):
        self.write_mission()
        head = self.git('rev-parse', 'HEAD')
        prior = self.root / 'tmp/local-experiment/autonomous-runner/gate-safetytest'
        summary = {'schema_version': 1, 'result': 'stopped', 'attempts': 1,
                   'reason': 'agent_safety_boundary_claim'}
        result = {'head_before': head, 'head_after': head, 'reason': 'safety_boundary'}
        checkpoint = dict(schema_version=1, nonce='a'*32, iteration=1, head=head,
                          decision='stop', reason='safety_boundary')
        self.write_private_json(prior / 'summary.json', summary)
        self.write_private_json(prior / 'attempt-001/result.json', result)
        self.write_private_json(prior / 'attempt-001/checkpoint.json', checkpoint)
        original = (prior / 'summary.json').read_bytes()
        with self.assertRaisesRegex(R.RunnerError, 'SAFETY_STOP_NOT_REVIEWED'):
            R.reviewed_safety_stop(self.root, prior, summary, head)
        path = self.root / 'tmp/local-experiment/gate-batches/review-gate-safetytest.json'
        review = {'schema_version': 1, 'source_run': prior.name,
                  'original_summary': summary, 'original_result': result,
                  'decision': 'blocker_resolved_new_mission_no_replay',
                  'last_session_id': 'ses_fixture123',
                  'server_outcome': 'succeeded_idle_nonactive', 'resolution_head': head}
        self.write_private_json(path, review)
        self.assertTrue(R.reviewed_safety_stop(self.root, prior, summary, head))
        path.unlink()  # Disposable test fixture only, not retained run evidence.
        diagnostic = dict(review, decision='terminal_safety_stop_repair_only_no_replay',
                          allowed_gate='L0_OWNER_CHAIN_REPAIR')
        repair_path = path.with_name('repair-review-gate-safetytest.json')
        self.write_private_json(repair_path, diagnostic)
        self.assertTrue(R.reviewed_safety_stop(self.root, prior, summary, head, True))
        with self.assertRaises(R.RunnerError):
            R.reviewed_safety_stop(self.root, prior, summary, head)
        self.write_private_json(repair_path, dict(diagnostic, allowed_gate='L0_CONTROLLER_READINESS'))
        with self.assertRaises(R.RunnerError):
            R.reviewed_safety_stop(self.root, prior, summary, head, True)
        self.write_private_json(path, review)
        for bad in (dict(review, server_outcome='failed'),
                    dict(review, original_result=dict(result, reason='more_work')),
                    dict(review, resolution_head='0'*40)):
            self.write_private_json(path, bad)
            with self.assertRaises(R.RunnerError):
                R.reviewed_safety_stop(self.root, prior, summary, head)
        self.assertFalse(R.reviewed_safety_stop(self.root, prior,
                         dict(summary, reason='OPENCODE_RUN_FAILED'), head))
        self.assertEqual((prior / 'summary.json').read_bytes(), original)

    def test_transport_review_keeps_missing_result_and_original_failure(self):
        before = self.git('rev-parse', 'HEAD')
        self.git('commit', '--allow-empty', '-m', 'transport fixture descendant')
        after = self.git('rev-parse', 'HEAD')
        prior = self.root/'tmp/local-experiment/autonomous-runner/gate-transport'
        summary = dict(schema_version=1, result='stopped', attempts=1, reason='OPENCODE_RUN_FAILED')
        cp = dict(schema_version=1, nonce='a'*32, iteration=1, head=after,
                  decision='continue', reason='more_work')
        self.write_private_json(prior/'summary.json', summary)
        self.write_private_json(prior/'attempt-001/checkpoint.json', cp)
        with self.assertRaises(R.RunnerError): R.reviewed_transport_stop(self.root, prior, summary, after)
        review = dict(schema_version=1, source_run=prior.name, original_summary=summary,
                      checkpoint=cp, before_head=before, last_session_id='ses_fixture',
                      server_outcome='succeeded_idle_nonactive', decision='server_success_new_mission_no_replay')
        path = self.root/'tmp/local-experiment/gate-batches/transport-review-gate-transport.json'
        self.write_private_json(path, review)
        self.assertTrue(R.reviewed_transport_stop(self.root, prior, summary, after))
        self.assertFalse((prior/'attempt-001/result.json').exists())
        self.assertEqual(json.loads((prior/'summary.json').read_text()), summary)
        for bad in (dict(review, server_outcome='failed'), dict(review, before_head=after),
                    dict(review, checkpoint=dict(cp, nonce='b'*32))):
            self.write_private_json(path, bad)
            with self.assertRaises(R.RunnerError): R.reviewed_transport_stop(self.root, prior, summary, after)

    def test_no_safe_scope_can_only_admit_independent_finding_gate(self):
        head = self.git('rev-parse', 'HEAD')
        prior = self.root/'tmp/local-experiment/autonomous-runner/gate-nosafe'
        summary = dict(schema_version=1, result='stopped', attempts=1, reason='agent_no_safe_work_claim')
        result = dict(head_before=head, head_after=head, reason='no_safe_work')
        cp = dict(schema_version=1, nonce='a'*32, iteration=1, head=head, decision='stop', reason='no_safe_work')
        self.write_private_json(prior/'summary.json', summary)
        self.write_private_json(prior/'attempt-001/result.json', result)
        self.write_private_json(prior/'attempt-001/checkpoint.json', cp)
        gate = 'L0_FLUX_FINDINGS_DIAGNOSIS'
        self.assertFalse(R.reviewed_independent_stop(self.root,prior,summary,head,None))
        with self.assertRaises(R.RunnerError): R.reviewed_independent_stop(self.root,prior,summary,head,gate)
        path = self.root/'tmp/local-experiment/gate-batches/independent-review-gate-nosafe.json'
        review = dict(schema_version=1, source_run=prior.name, original_summary=summary,
            original_result=result, checkpoint=cp, reviewed_head=head, last_session_id='ses_fixture',
            server_outcome='succeeded_idle_nonactive', held_scope='local_admission/repository_repair',
            allowed_gate=gate, decision='held_scope_new_independent_diagnosis_no_replay')
        self.write_private_json(path,review)
        self.assertTrue(R.reviewed_independent_stop(self.root,prior,summary,head,gate))
        self.assertFalse(R.reviewed_independent_stop(self.root,prior,summary,head,'L0_DEPENDENCY_CONTINUATION'))
        self.write_private_json(path,dict(review,server_outcome='failed'))
        with self.assertRaises(R.RunnerError): R.reviewed_independent_stop(self.root,prior,summary,head,gate)
        self.assertEqual(json.loads((prior/'summary.json').read_text()),summary)

    def test_refresh_review_is_known_run_gate_ledger_and_original_bound(self):
        head=self.git('rev-parse','HEAD')
        ledger=self.root/'security/resident-images.json'
        ledger.parent.mkdir(parents=True,exist_ok=True); ledger.write_text('{}')
        prior=self.root/'tmp/local-experiment/autonomous-runner/gate-5d3ur67c'
        summary=dict(schema_version=1,result='stopped',attempts=1,reason='agent_no_safe_work_claim')
        result=dict(head_before=head,head_after=head,reason='no_safe_work')
        cp=dict(schema_version=1,nonce='a'*32,iteration=1,head=head,decision='stop',reason='no_safe_work')
        self.write_private_json(prior/'attempt-001/result.json',result)
        self.write_private_json(prior/'attempt-001/checkpoint.json',cp)
        gate='L0_FLUX_EVIDENCE_REFRESH'
        self.assertFalse(R.reviewed_refresh_stop(self.root,prior,summary,head,'L0_DEPENDENCY_CONTINUATION'))
        self.assertFalse(R.reviewed_refresh_stop(self.root,prior.with_name('gate-unknown'),summary,head,gate))
        with self.assertRaises(R.RunnerError): R.reviewed_refresh_stop(self.root,prior,summary,head,gate)
        review=dict(schema_version=1,source_run=prior.name,original_summary=summary,original_result=result,
            checkpoint=cp,reviewed_head=head,last_session_id='ses_fixture',server_outcome='succeeded_idle_nonactive',
            allowed_gate=gate,ledger_sha256=R.hashlib.sha256(ledger.read_bytes()).hexdigest(),
            decision='held_scope_exact_digest_evidence_refresh_no_replay')
        p=self.root/'tmp/local-experiment/gate-batches/refresh-review-gate-5d3ur67c.json'
        self.write_private_json(p,review)
        self.assertTrue(R.reviewed_refresh_stop(self.root,prior,summary,head,gate))
        for bad in (dict(review,server_outcome='failed'),dict(review,ledger_sha256='b'*64),
                    dict(review,checkpoint=dict(cp,reason='complete'))):
            self.write_private_json(p,bad)
            with self.assertRaises(R.RunnerError): R.reviewed_refresh_stop(self.root,prior,summary,head,gate)
        candidate=self.root/'tmp/local-experiment/flux-pin-candidate/candidate-hn94027w/summary.json'
        candidate.parent.mkdir(parents=True);candidate.write_text('{}')
        build=dict(review,allowed_gate='L0_FLUX_LOCAL_BUILD',
                   decision='held_scope_isolated_source_build_no_replay',
                   candidate_summary_sha256=R.hashlib.sha256(candidate.read_bytes()).hexdigest())
        self.write_private_json(p.with_name('build-review-gate-5d3ur67c.json'),build)
        self.assertTrue(R.reviewed_refresh_stop(self.root,prior,summary,head,'L0_FLUX_LOCAL_BUILD'))
        candidate.write_text('{"changed":true}')
        with self.assertRaises(R.RunnerError):R.reviewed_refresh_stop(self.root,prior,summary,head,'L0_FLUX_LOCAL_BUILD')

    def test_toolchain_repair_review_binds_original_binary(self):
        head=self.git('rev-parse','HEAD')
        ledger=self.root/'security/resident-images.json';ledger.parent.mkdir(parents=True,exist_ok=True);ledger.write_text('{}')
        candidate=self.root/'tmp/local-experiment/flux-pin-candidate/candidate-hn94027w/summary.json'
        candidate.parent.mkdir(parents=True);candidate.write_text('{}')
        binary=self.root/'tmp/local-experiment/flux-local-build/build-aoa39q7w/source-controller'
        binary.parent.mkdir(parents=True);binary.write_bytes(b'prior-build')
        prior=self.root/'tmp/local-experiment/autonomous-runner/gate-xezx_z6k'
        summary=dict(schema_version=1,result='stopped',attempts=1,reason='agent_no_safe_work_claim')
        result=dict(head_before=head,head_after=head,reason='no_safe_work')
        cp=dict(schema_version=1,nonce='a'*32,iteration=1,head=head,decision='stop',reason='no_safe_work')
        self.write_private_json(prior/'attempt-001/result.json',result)
        self.write_private_json(prior/'attempt-001/checkpoint.json',cp)
        review=dict(schema_version=1,source_run=prior.name,original_summary=summary,original_result=result,
            checkpoint=cp,reviewed_head=head,last_session_id='ses_fixture',server_outcome='succeeded_idle_nonactive',
            allowed_gate='L0_FLUX_LOCAL_BUILD',decision='held_scope_isolated_source_build_no_replay',
            ledger_sha256=R.hashlib.sha256(ledger.read_bytes()).hexdigest(),
            candidate_summary_sha256=R.hashlib.sha256(candidate.read_bytes()).hexdigest(),
            prior_binary_sha256=R.hashlib.sha256(binary.read_bytes()).hexdigest())
        self.write_private_json(self.root/'tmp/local-experiment/gate-batches/build-review-gate-xezx_z6k.json',review)
        self.assertTrue(R.reviewed_refresh_stop(self.root,prior,summary,head,'L0_FLUX_LOCAL_BUILD'))
        self.assertFalse(R.reviewed_refresh_stop(self.root,prior,summary,head,'L0_FLUX_EVIDENCE_REFRESH'))
        binary.write_bytes(b'changed')
        with self.assertRaises(R.RunnerError):R.reviewed_refresh_stop(self.root,prior,summary,head,'L0_FLUX_LOCAL_BUILD')

    def test_restart_review_leaves_old_scope_held_and_gate_bound(self):
        head=self.git('rev-parse','HEAD')
        ledger=self.root/'security/resident-images.json';ledger.parent.mkdir(parents=True,exist_ok=True);ledger.write_text('{}')
        prior=self.root/'tmp/local-experiment/autonomous-runner/gate-ayqdekwa'
        summary=dict(schema_version=1,result='stopped',attempts=1,reason='agent_no_safe_work_claim')
        result=dict(head_before=head,head_after=head,reason='no_safe_work')
        cp=dict(schema_version=1,nonce='a'*32,iteration=1,head=head,decision='stop',reason='no_safe_work')
        self.write_private_json(prior/'attempt-001/result.json',result)
        self.write_private_json(prior/'attempt-001/checkpoint.json',cp)
        gate='L0_SUPERVISION_CAPACITY_REPAIR'
        self.assertFalse(R.reviewed_restart_stop(self.root,prior,summary,head,'L0_FLUX_LOCAL_BUILD'))
        self.assertFalse(R.reviewed_restart_stop(self.root,prior.with_name('gate-unknown'),summary,head,gate))
        with self.assertRaises(R.RunnerError):R.reviewed_restart_stop(self.root,prior,summary,head,gate)
        review=dict(schema_version=1,source_run=prior.name,original_summary=summary,original_result=result,
            checkpoint=cp,reviewed_head=head,last_session_id='ses_fixture',server_outcome='succeeded_idle_nonactive',
            allowed_gate=gate,decision='old_scope_held_repository_capacity_repair_no_replay',
            ledger_sha256=R.hashlib.sha256(ledger.read_bytes()).hexdigest())
        self.write_private_json(self.root/'tmp/local-experiment/gate-batches/restart-review-gate-ayqdekwa.json',review)
        self.assertTrue(R.reviewed_restart_stop(self.root,prior,summary,head,gate))
        planning='L0_FLUX_TEST_CAPACITY_PLAN'
        evidence=self.root/'tmp/local-experiment/safe-restart-review/review-mv27dels'
        self.write_private_json(evidence/'summary.json',dict(classification='RESTART_RESUME_REVIEW_COMPLETED',
            capacity_review=True,worker_replayed=False))
        self.write_private_json(evidence/'pilot-review.json',dict(classification='API_PACKAGE_TESTS_PASSED',exit_code=0))
        planned=dict(review,allowed_gate=planning,decision='old_scope_held_test_capacity_planning_no_execution',
            completed_review_sha256=R.hashlib.sha256((evidence/'summary.json').read_bytes()).hexdigest(),
            pilot_review_sha256=R.hashlib.sha256((evidence/'pilot-review.json').read_bytes()).hexdigest())
        self.write_private_json(self.root/'tmp/local-experiment/gate-batches/test-plan-review-gate-ayqdekwa.json',planned)
        self.assertTrue(R.reviewed_restart_stop(self.root,prior,summary,head,planning))
        hold=self.root/'tmp/local-experiment/test-capacity-plan/plan-nu7t5m6c/parent-independent-review.json'
        self.write_private_json(hold,dict(decision='hold_test_execution_combined_retention_budget',
            retained_hashes_match=True,profiles_stopped=True))
        minimal=dict(planned,allowed_gate='L0_FLUX_MINIMAL_TEST_REPAIR',
            decision='old_scope_held_minimum_seed_executor_repair_no_replay',
            planning_review_sha256=R.hashlib.sha256(hold.read_bytes()).hexdigest())
        self.write_private_json(self.root/'tmp/local-experiment/gate-batches/minimal-test-review-gate-ayqdekwa.json',minimal)
        self.assertTrue(R.reviewed_restart_stop(self.root,prior,summary,head,'L0_FLUX_MINIMAL_TEST_REPAIR'))
        self.write_private_json(hold,dict(decision='unknown'))
        with self.assertRaises(R.RunnerError):
            R.reviewed_restart_stop(self.root,prior,summary,head,'L0_FLUX_MINIMAL_TEST_REPAIR')
        self.write_private_json(evidence/'pilot-review.json',dict(classification='API_PACKAGE_TESTS_FAILED',exit_code=1))
        with self.assertRaises(R.RunnerError):R.reviewed_restart_stop(self.root,prior,summary,head,planning)
        ledger.write_text('{"changed":true}')
        with self.assertRaises(R.RunnerError):R.reviewed_restart_stop(self.root,prior,summary,head,gate)

    def test_plan_is_zero_model_calls_and_requires_explicit_budget(self):
        with mock.patch.object(R, 'ROOT', self.root), mock.patch.object(R, 'VAULT', self.vault), \
             mock.patch.object(R.shutil, 'which', return_value=str(self.cli)), \
             contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(R.main([]), 0)
            self.assertEqual(json.loads(out.getvalue())['active_calls'], 0)
            self.assertEqual(R.main(['--execute']), 1)
            self.assertIn('BUDGET_REQUIRED', err.getvalue())
        self.assertFalse((self.root/'tmp').exists())

    def test_execute_cli_requires_weekly_limit_flag_and_uses_private_handoff(self):
        with mock.patch.object(R, 'ROOT', self.root), mock.patch.object(R, 'VAULT', self.vault), \
             mock.patch.object(R.shutil, 'which', return_value=str(self.cli)), \
             mock.patch.dict(os.environ, {'FAKE_OPENCODE_MODE': 'stop'}), \
             mock.patch.object(R, 'gate_preflight'), \
             contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(R.main(['--execute', '--model', self.model, '--max-runs', '2', '--max-minutes', '1']), 1)
            self.assertIn('BUDGET_REQUIRED', err.getvalue())
            path = self.write_mission()
            self.assertEqual(R.main(['--execute', '--use-pro-weekly-limit', '--model', self.model,
                                     '--max-runs', '2', '--max-minutes', '1',
                                     '--mission-file', str(path)]), 0)
        self.assertEqual(json.loads(out.getvalue().splitlines()[-1])['completion_verified'], False)
        parent = self.root/'tmp/local-experiment/autonomous-runner'
        output = next(parent.glob('gate-*/attempt-001/stdout.jsonl'))
        self.assertIn('fixture', output.read_text())
        self.assertFalse(list(parent.glob('gate-*/attempt-002')))

    def test_short_batch_bound_is_allowed_but_not_unbounded(self):
        with mock.patch.object(R, 'ROOT', self.root), mock.patch.object(R, 'VAULT', self.vault), \
             mock.patch.object(R.shutil, 'which', return_value=str(self.cli)), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(R.main(['--max-runs', '6', '--max-minutes', '120']), 0)
        plan = json.loads(out.getvalue())
        self.assertEqual((plan['planned_max_runs'], plan['planned_max_minutes']), (6, 120))
        self.assertEqual(plan['subscription_preflight'], 'openai_oauth_label_present')
        self.assertEqual(plan['model'], 'openai/gpt-6.1-sol')
        with self.assertRaisesRegex(R.RunnerError, 'BUDGET_INVALID'):
            self.invoke_fixture(self.root, self.vault, str(self.cli), max_runs=7, max_minutes=120, model=self.model)
        with self.assertRaisesRegex(R.RunnerError, 'BUDGET_INVALID'):
            self.invoke_fixture(self.root, self.vault, str(self.cli), max_runs=6, max_minutes=121, model=self.model)

    def test_execute_rejects_missing_or_unsafe_mission_without_model_call(self):
        with mock.patch.object(R, 'ROOT', self.root), mock.patch.object(R, 'VAULT', self.vault), \
             mock.patch.object(R.shutil, 'which', return_value=str(self.cli)), \
             contextlib.redirect_stderr(io.StringIO()) as err, contextlib.redirect_stdout(io.StringIO()):
            args = ['--execute', '--use-pro-weekly-limit', '--model', self.model,
                    '--max-runs', '1', '--max-minutes', '1']
            self.assertEqual(R.main(args), 1)
            self.assertIn('MISSION_REQUIRED', err.getvalue())
            path = self.write_mission()
            alias = path.with_name('alias.json')
            alias.symlink_to(path)
            self.assertEqual(R.main(args + ['--mission-file', str(alias)]), 1)
            path.chmod(0o644)
            self.assertEqual(R.main(args + ['--mission-file', str(path)]), 1)
            path.chmod(0o600)
            wrong = json.loads(path.read_text())
            wrong['head_before'] = 'a'*40
            path.write_text(json.dumps(wrong))
            self.assertEqual(R.main(args + ['--mission-file', str(path)]), 1)
            self.assertFalse(list((self.root/'tmp/local-experiment/autonomous-runner').glob('gate-*')))

    def test_missing_or_ambiguous_oauth_cannot_start_run(self):
        for mode in ('missing', 'api', 'multiple', 'untyped', 'legacy_text'):
            with self.subTest(mode=mode), mock.patch.dict(os.environ, {'FAKE_AUTH_MODE': mode}), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(R.RunnerError, 'SUBSCRIPTION_AUTH_UNVERIFIED'):
                    self.invoke_fixture(self.root, self.vault, str(self.cli), max_runs=6,
                             max_minutes=120, model=self.model)
        parent = self.root/'tmp/local-experiment/autonomous-runner'
        self.assertFalse(list(parent.glob('gate-*/attempt-001')))
        self.assertEqual(len(list(parent.glob('gate-*/summary.json'))), 5)
        with self.assertRaisesRegex(R.RunnerError, 'SUBSCRIPTION_MODEL_REQUIRED'):
            R.subscription_preflight(str(self.cli), 'openrouter/paid-model', self.root)
        with mock.patch.dict(os.environ, {'OPENAI_API_KEY': 'redacted'}):
            with self.assertRaisesRegex(R.RunnerError, 'SUBSCRIPTION_ROUTE_AMBIGUOUS'):
                R.subscription_preflight(str(self.cli), self.model, self.root)

    def test_plan_reports_oauth_label_without_model_request(self):
        with mock.patch.object(R, 'ROOT', self.root), mock.patch.object(R, 'VAULT', self.vault), \
             mock.patch.object(R.shutil, 'which', return_value=str(self.cli)), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(R.main(['--model', self.model]), 0)
        self.assertEqual(json.loads(out.getvalue())['subscription_preflight'], 'openai_oauth_label_present')
        with mock.patch.object(R, 'ROOT', self.root), mock.patch.object(R, 'VAULT', self.vault), \
             mock.patch.object(R.shutil, 'which', return_value=str(self.cli)), \
             mock.patch.dict(os.environ, {'FAKE_AUTH_MODE': 'api'}), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(R.main(['--model', self.model]), 0)
        self.assertEqual(json.loads(out.getvalue())['subscription_preflight'], 'SUBSCRIPTION_AUTH_UNVERIFIED')

    def test_lost_oauth_between_commits_stops_before_second_invocation(self):
        with mock.patch.dict(os.environ, {'FAKE_AUTH_MODE': 'expires'}), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(R.RunnerError, 'SUBSCRIPTION_AUTH_UNVERIFIED'):
                self.invoke_fixture(self.root, self.vault, str(self.cli), max_runs=6,
                         max_minutes=120, model=self.model)
        parent = self.root/'tmp/local-experiment/autonomous-runner'
        directory = next(parent.glob('gate-*'))
        summary = json.loads((directory/'summary.json').read_text())
        count = self.root/'tmp/auth-count'
        self.assertEqual(summary['attempts'], 1,
                         f"reason={summary['reason']} auth_count={count.read_text() if count.exists() else 'none'}")
        self.assertEqual(json.loads((directory/'attempt-001/result.json').read_text())['reason'], 'more_work')
        self.assertFalse((directory/'attempt-002').exists())

    def test_two_new_sessions_continue_only_after_new_clean_commits(self):
        initial = R.checkout_head(self.root, self.vault)
        with contextlib.redirect_stdout(io.StringIO()):
            reason, directory = self.invoke_fixture(self.root, self.vault, str(self.cli), max_runs=2, max_minutes=1, model=self.model)
        self.assertEqual(reason, 'run_budget_reached')
        self.assertEqual(json.loads((directory/'summary.json').read_text())['attempts'], 2)
        self.assertNotEqual(initial, R.checkout_head(self.root, self.vault))
        self.assertEqual(len(self.git('log', '--oneline', '-2').splitlines()), 2)
        for number in (1, 2):
            attempt = directory / f'attempt-{number:03d}'
            self.assertEqual(json.loads((attempt/'result.json').read_text())['reason'], 'more_work')
            self.assertEqual(attempt.stat().st_mode & 0o777, 0o700)
            for name in ('checkpoint.json', 'stdout.jsonl', 'stderr.log', 'result.json', 'handoff-snapshot.md'):
                self.assertEqual((attempt/name).stat().st_mode & 0o777, 0o600)
            self.assertEqual((attempt/'handoff-snapshot.md').read_text(), self.vault.read_text())

    def test_snapshot_rejects_symlink_and_oversized_handoff(self):
        attempt = Path(self.temp.name)/'snapshot-attempt'
        attempt.mkdir(mode=0o700)
        snapshot = R.snapshot_handoff(self.vault, attempt)
        self.assertEqual(snapshot.read_text(), 'saved handoff')
        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
        alias = Path(self.temp.name)/'vault-alias'
        alias.symlink_to(self.vault)
        with self.assertRaisesRegex(R.RunnerError, 'HANDOFF_UNAVAILABLE'):
            R.snapshot_handoff(alias, attempt)
        self.vault.write_bytes(b'a' * (R.MAX_HANDOFF_BYTES + 1))
        with self.assertRaisesRegex(R.RunnerError, 'HANDOFF_UNAVAILABLE'):
            R.snapshot_handoff(self.vault, attempt)

    def test_project_provider_policy_blocks_non_openai_model_selection(self):
        config = json.loads((R.ROOT/'opencode.jsonc').read_text())
        statements = config['experimental']['policies']
        self.assertEqual(statements[:2], [
            {'action': 'provider.use', 'resource': '*', 'effect': 'deny'},
            {'action': 'provider.use', 'resource': 'openai', 'effect': 'allow'},
        ])
        text = R.prompt({'nonce': 'a'*32, 'iteration': 1, 'checkpoint': 'unused'},
                        Path('tmp/runner-test/handoff-snapshot.md'), self.model)
        self.assertIn('stop solely because a provider billing ledger', text)
        self.assertNotIn('Stop if the subscription route or entitlement is unclear', text)

    def test_astra_is_a_bounded_toolless_consultant_not_the_primary_model(self):
        config = json.loads((R.ROOT/'opencode.jsonc').read_text())
        self.assertEqual(config['model'], 'openai/gpt-6.1-sol')
        adviser = config['agents']['astra-advisor']
        self.assertEqual((adviser['mode'], adviser['model']),
                         ('subagent', 'openai/gpt-6-astra'))
        self.assertEqual(adviser['permissions'],
                         [{'action': '*', 'resource': '*', 'effect': 'deny'}])
        self.assertNotIn('apiKey', json.dumps(config))
        text = R.prompt({'nonce': 'a'*32, 'iteration': 1, 'checkpoint': 'unused'},
                        Path('tmp/runner-test/handoff-snapshot.md'), self.model)
        self.assertIn('primary model from '+self.model, text)
        self.assertIn('`astra-advisor` subagent using `openai/gpt-6-astra`', text)
        self.assertRegex(text, r'at most two Astra calls total\s+in this parent session')
        self.assertIn('tools.local_experiment.astra_preflight', text)
        self.assertIn('ASTRA_CONSULT_PREFLIGHT_OK', text)
        self.assertIn('do not consult', text)
        self.assertIn('not the runner\'s session count', text)
        self.assertNotIn('Do not switch providers/accounts/models', text)

    def test_astra_preflight_checks_oauth_and_catalog_without_model_request(self):
        completed = subprocess.CompletedProcess(['opencode', 'models'], 0,
                                                b'openai/gpt-6-sol\nopenai/gpt-6-astra\n', b'')
        with mock.patch.object(A.runner, 'subscription_preflight') as auth, \
             mock.patch.object(A.subprocess, 'run', return_value=completed) as command:
            A.check('/fixed/opencode', self.root)
        auth.assert_called_once_with('/fixed/opencode', 'openai/gpt-6-astra', self.root)
        command.assert_called_once_with(['/fixed/opencode', 'models'], cwd=self.root,
                                        capture_output=True, timeout=20, check=False)
        for payload in (b'openai/gpt-6-astra-fast\n', b'openai/gpt-6-astra?\n'):
            with self.subTest(payload=payload), mock.patch.object(A.runner, 'subscription_preflight'), \
                 mock.patch.object(A.subprocess, 'run', return_value=subprocess.CompletedProcess(
                     ['opencode', 'models'], 0, payload, b'')):
                with self.assertRaisesRegex(R.RunnerError, 'ASTRA_MODEL_UNVERIFIED'):
                    A.check('/fixed/opencode', self.root)

    def test_agent_stop_claim_never_becomes_verified_completion(self):
        with mock.patch.dict(os.environ, {'FAKE_OPENCODE_MODE': 'stop'}), \
             contextlib.redirect_stdout(io.StringIO()):
            reason, directory = self.invoke_fixture(self.root, self.vault, str(self.cli), max_runs=3, max_minutes=1, model=self.model)
        self.assertEqual(reason, 'agent_no_safe_work_claim')
        self.assertEqual(json.loads((directory/'summary.json').read_text())['attempts'], 1)
        self.assertFalse((directory/'attempt-002').exists())

    def test_missing_or_symlink_checkpoint_and_no_commit_fail_closed(self):
        for mode, code in [('missing', 'CHECKPOINT_INVALID'), ('symlink', 'CHECKPOINT_INVALID'),
                           ('no_commit', 'NO_NEW_COMMIT')]:
            with self.subTest(mode=mode), mock.patch.dict(os.environ, {'FAKE_OPENCODE_MODE': mode}), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(R.RunnerError, code):
                    self.invoke_fixture(self.root, self.vault, str(self.cli), max_runs=2, max_minutes=1, model=self.model)
        parent = self.root/'tmp/local-experiment/autonomous-runner'
        self.assertEqual(len(list(parent.glob('gate-*/summary.json'))), 3)
        self.assertFalse(list(parent.glob('gate-*/attempt-002')))

    def test_dirty_or_nonzero_exit_preserves_evidence_and_never_retries(self):
        for mode, code in [('dirty', 'DIRTY_WORKTREE'), ('failure', 'OPENCODE_RUN_FAILED')]:
            with self.subTest(mode=mode), mock.patch.dict(os.environ, {'FAKE_OPENCODE_MODE': mode}), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(R.RunnerError, code):
                    self.invoke_fixture(self.root, self.vault, str(self.cli), max_runs=2, max_minutes=1, model=self.model)
            if mode == 'dirty':
                self.assertEqual((self.root/'unfinished.txt').read_text(), 'preserve me')
                # Clear only this test-owned disposable file for the next fixture.
                (self.root/'unfinished.txt').unlink()
        parent = self.root/'tmp/local-experiment/autonomous-runner'
        self.assertFalse(list(parent.glob('gate-*/attempt-002')))

    def test_scope_lock_and_timeout_do_not_launch_another_session(self):
        self.git('checkout', '-q', '-b', 'wrong-branch')
        with self.assertRaisesRegex(R.RunnerError, 'WRONG_BRANCH'):
            R.checkout_head(self.root, self.vault)
        self.git('checkout', '-q', R.BRANCH)
        fd = R.lock_directory(self.root)
        try:
            with self.assertRaisesRegex(R.RunnerError, 'RUNNER_ALREADY_ACTIVE'):
                R.lock_directory(self.root)
        finally:
            os.close(fd)
        attempt = self.root/'tmp/local-experiment/autonomous-runner/timeout-fixture'
        attempt.mkdir(mode=0o700)
        with mock.patch.dict(os.environ, {'FAKE_OPENCODE_MODE': 'slow'}):
            with self.assertRaisesRegex(R.RunnerError, 'PROCESS_STOPPED_REMOTE_UNCONFIRMED'):
                R.invoke(str(self.cli), self.root, attempt,
                         R.prompt({'nonce': 'a'*32, 'iteration': 1, 'checkpoint': 'unused'}, self.vault, self.model),
                         time.monotonic()+0.5, self.model)
        self.assertTrue((attempt/'stdout.jsonl').exists())

    def test_checkpoint_rejects_duplicate_stale_and_nonprivate_entries(self):
        path = Path(self.temp.name)/'checkpoint.json'
        control = {'nonce': 'a'*32, 'iteration': 1, 'checkpoint': 'unused'}
        head = self.git('rev-parse', 'HEAD')
        correct = dict(schema_version=1, nonce=control['nonce'], iteration=1,
                       head=head, decision='stop', reason='no_safe_work')
        path.write_text(json.dumps(correct))
        path.chmod(0o600)
        self.assertEqual(R.read_checkpoint(path, control, head), 'no_safe_work')
        for bad in (dict(correct, nonce='b'*32), dict(correct, iteration=True),
                    dict(correct, head='0'*40), dict(correct, reason='more_work')):
            with self.subTest(bad=bad):
                path.write_text(json.dumps(bad))
                with self.assertRaisesRegex(R.RunnerError, 'CHECKPOINT_INVALID'):
                    R.read_checkpoint(path, control, head)
        path.write_text(json.dumps(correct)[:-1]+',"nonce":"'+control['nonce']+'"}')
        with self.assertRaisesRegex(R.RunnerError, 'CHECKPOINT_INVALID'):
            R.read_checkpoint(path, control, head)
        path.write_text(json.dumps(correct))
        path.chmod(0o644)
        with self.assertRaisesRegex(R.RunnerError, 'CHECKPOINT_INVALID'):
            R.read_checkpoint(path, control, head)

    def test_unreaped_local_process_is_never_confirmed_stopped(self):
        process = mock.Mock(pid=123)
        process.wait.side_effect = [subprocess.TimeoutExpired('fixture', 3),
                                    subprocess.TimeoutExpired('fixture', 3)]
        with mock.patch.object(R.os, 'killpg') as kill:
            with self.assertRaisesRegex(R.RunnerError, 'PROCESS_STOPPED_REMOTE_UNCONFIRMED'):
                R.terminate(process)
        self.assertEqual(kill.call_count, 2)

    def test_termination_signal_races_are_classified_without_replay(self):
        for error in (ProcessLookupError(), PermissionError()):
            with self.subTest(error=type(error).__name__):
                process = mock.Mock(pid=123)
                with mock.patch.object(R.os, 'killpg', side_effect=error) as kill:
                    if isinstance(error, PermissionError):
                        with self.assertRaisesRegex(R.RunnerError, 'PROCESS_STOPPED_REMOTE_UNCONFIRMED'):
                            R.terminate(process)
                        process.wait.assert_not_called()
                    else:
                        R.terminate(process)
                        process.wait.assert_called_once_with(timeout=3)
                kill.assert_called_once()

        process = mock.Mock(pid=123)
        process.wait.side_effect = [subprocess.TimeoutExpired('fixture', 3)]
        with mock.patch.object(R.os, 'killpg', side_effect=[None, PermissionError()]):
            with self.assertRaisesRegex(R.RunnerError, 'PROCESS_STOPPED_REMOTE_UNCONFIRMED'):
                R.terminate(process)


if __name__ == '__main__':
    unittest.main()
