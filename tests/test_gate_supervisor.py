import copy
import json
from datetime import datetime, timezone, timedelta
import unittest
import tempfile
from contextlib import ExitStack
from pathlib import Path
from unittest import mock
from tools.local_experiment import gate_supervisor as S


def fixture():
    def obj(kind, name, ns=None, **spec):
        return {'kind': kind, 'metadata': {'uid': kind+name, 'name': name,
                'namespace': ns, 'generation': 1}, 'spec': spec, 'status': {}}
    items = [obj('Node', 'colima-mac-studio-solo')]
    for name in sorted(S.FLUX):
        items.append(obj('Deployment', name, 'flux-system', replicas=0,
                         template={'spec': {'containers': [{'image': 'test@sha256:'+'a'*64}]}}))
    items.append(obj('Deployment', 'polaris', 'shirokuma-dev', replicas=0))
    items += [obj('Kustomization', 'k'+str(n), 'flux-system', suspend=True) for n in range(7)]
    items.append(obj('GitRepository', 'source', 'flux-system', suspend=True))
    items += [obj('Job', 'j'+str(n), 'shirokuma-dev', suspend=True) for n in range(3)]
    items.append(obj('PersistentVolume', 'pv', claimRef={'uid': 'claim'}))
    items.append(obj('PersistentVolumeClaim', 'claim', 'shirokuma-dev', volumeName='pv'))
    for deploy in list(items):
        if deploy['kind'] == 'Deployment':
            rs = obj('ReplicaSet', deploy['metadata']['name']+'-rs', deploy['metadata']['namespace'], replicas=0)
            rs['metadata']['ownerReferences'] = [{'uid': deploy['metadata']['uid'], 'controller': True}]
            items.append(rs)
    return items


class SupervisorTests(unittest.TestCase):
    def test_api_launch_accepts_rewritten_argv_only_with_matching_unit(self):
        processes = 'PID COMMAND\n1898 /usr/local/bin/k3s server\n'
        units = 'shirokuma-l0-owner-repair-test.service loaded active running test\n'
        props = ('ActiveState=active\nMainPID=1898\nRestart=no\nKillMode=control-group\n'
                 'ExecStart={ path=/usr/local/bin/k3s ; argv[]=/usr/local/bin/k3s server '
                 '--disable-agent --disable-scheduler --disable-controller-manager '
                 '--disable-cloud-controller --https-listen-port=55737 --bind-address=127.0.0.1 ; }\n')
        self.assertEqual(S.api_only_launch(processes, units, props), units.split()[0])
        for old, new in [('MainPID=1898', 'MainPID=999'), ('ActiveState=active', 'ActiveState=inactive'),
                         ('Restart=no', 'Restart=always'), ('--disable-agent', '--enable-agent'),
                         ('--bind-address=127.0.0.1', '--bind-address=0.0.0.0')]:
            with self.subTest(old=old), self.assertRaises(S.R.RunnerError):
                S.api_only_launch(processes, units, props.replace(old, new))
        for p, u in [(processes+processes, units), (processes, units+units),
                     ('PID COMMAND\n', units), (processes, '')]:
            with self.assertRaises(S.R.RunnerError):
                S.api_only_launch(p, u, props)

    def test_valid_repair(self):
        data = fixture()
        S.validate(data, copy.deepcopy(data), S.REPAIR)

    def test_repair_rejects_replica_race_and_unknown_owner(self):
        baseline = fixture()
        for edit in ('replicas', 'owner'):
            data = copy.deepcopy(baseline)
            rs = next(i for i in data if i['kind'] == 'ReplicaSet')
            if edit == 'replicas': rs['spec']['replicas'] = 1
            else: rs['metadata']['ownerReferences'][0]['uid'] = 'unknown'
            with self.assertRaises(S.R.RunnerError): S.validate(data, baseline, S.REPAIR)

    def test_repair_rejects_unsuspended_jobs_sources_storage_drift_and_pods(self):
        baseline = fixture()
        for kind in ('Job', 'GitRepository', 'Kustomization', 'PersistentVolumeClaim'):
            data = copy.deepcopy(baseline)
            target = next(i for i in data if i['kind'] == kind)
            target['spec']['suspend' if kind != 'PersistentVolumeClaim' else 'volumeName'] = False
            with self.assertRaises(S.R.RunnerError): S.validate(data, baseline, S.REPAIR)
        data = copy.deepcopy(baseline)
        data.append({'kind': 'Pod', 'metadata': {'uid': 'p', 'name': 'p', 'namespace': 'shirokuma-dev'}})
        with self.assertRaises(S.R.RunnerError): S.validate(data, baseline, S.REPAIR)

    def test_rejects_new_workload_and_owner_surfaces(self):
        baseline = fixture()
        for kind in ('Deployment', 'DaemonSet', 'HorizontalPodAutoscaler', 'ReplicationController', 'CronJob'):
            data = copy.deepcopy(baseline)
            data.append({'kind': kind, 'metadata': {'uid': 'new', 'name': 'new', 'namespace': 'shirokuma-dev'}, 'spec': {}})
            with self.assertRaises(S.R.RunnerError): S.validate(data, baseline, S.REPAIR)

    def test_ready_requires_fresh_node_and_available_pinned_flux(self):
        baseline = fixture(); data = copy.deepcopy(baseline)
        for i in data:
            if i['kind'] in ('Deployment', 'ReplicaSet') and i['metadata']['namespace'] == 'flux-system':
                i['spec']['replicas'] = 1
                i['status'] = {'observedGeneration': 1, 'availableReplicas': 1, 'readyReplicas': 1}
        node = data[0]
        node['status']['conditions'] = [{'type': 'Ready', 'status': 'True',
            'lastHeartbeatTime': datetime.now(timezone.utc).isoformat()}]
        data.append({'kind': 'Lease', 'metadata': {'uid': 'lease', 'name': node['metadata']['name'],
            'namespace': 'kube-node-lease', 'ownerReferences': [{'kind': 'Node', 'uid': node['metadata']['uid']}]},
            'spec': {'holderIdentity': node['metadata']['name'], 'leaseDurationSeconds': 40,
                     'renewTime': datetime.now(timezone.utc).isoformat()}})
        for rs in list(data):
            if rs['kind'] == 'ReplicaSet' and rs['metadata']['namespace'] == 'flux-system':
                data.append({'kind': 'Pod', 'metadata': {'uid': 'pod'+rs['metadata']['name'],
                    'name': 'pod'+rs['metadata']['name'], 'namespace': 'flux-system',
                    'ownerReferences': [{'uid': rs['metadata']['uid']}]},
                    'spec': {'containers': [{'image': 'test@sha256:'+'a'*64}]},
                    'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]}})
        S.validate(data, baseline, S.READY)
        node['status']['conditions'][0]['lastHeartbeatTime'] = '2020-01-01T00:00:00Z'
        with self.assertRaises(S.R.RunnerError): S.validate(data, baseline, S.READY)
        node['status']['conditions'][0]['lastHeartbeatTime'] = datetime.now(timezone.utc).isoformat()
        data[1]['spec']['template']['spec']['containers'][0]['image'] = 'test:latest'
        with self.assertRaises(S.R.RunnerError): S.validate(data, baseline, S.READY)

    def test_default_plan_never_launches(self):
        with mock.patch('sys.argv', ['supervisor']), mock.patch.object(S, 'supervise') as run:
            self.assertEqual(S.main(), 0)
            run.assert_not_called()

    def test_node_uses_uid_bound_fresh_lease_not_180_second_status_cutoff(self):
        node = fixture()[0]
        now = datetime.now(timezone.utc)
        node['status']['conditions'] = [{'type': 'Ready', 'status': 'True',
              'lastHeartbeatTime': (now-timedelta(seconds=240)).isoformat()}]
        lease = {'metadata': {'name': node['metadata']['name'],
                   'ownerReferences': [{'kind': 'Node', 'uid': node['metadata']['uid']}]},
                 'spec': {'holderIdentity': node['metadata']['name'], 'leaseDurationSeconds': 40,
                          'renewTime': (now-timedelta(seconds=10)).isoformat()}}
        S.node_freshness(node, [lease])
        for bad in ([], [dict(lease, spec=dict(lease['spec'], renewTime=(now-timedelta(seconds=50)).isoformat()))],
                    [dict(lease, spec=dict(lease['spec'], renewTime=(now+timedelta(seconds=20)).isoformat()))],
                    [dict(lease, spec=dict(lease['spec'], holderIdentity='foreign'))],
                    [dict(lease, metadata=dict(lease['metadata'], ownerReferences=[{'kind':'Node','uid':'other'}]))]):
            with self.assertRaises(S.R.RunnerError): S.node_freshness(node, bad)
        node['status']['conditions'][0]['status'] = 'False'
        with self.assertRaises(S.R.RunnerError): S.node_freshness(node, [lease])

    def test_budget_and_cross_invocation_repair_cap(self):
        with self.assertRaises(S.R.RunnerError): S.supervise(Path('/unused'), Path('/unused'), 4, 120)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for n in range(2):
                p = root/'tmp/local-experiment/gate-supervisor'/('loop-'+str(n))
                p.mkdir(parents=True)
                p.parent.chmod(0o700)
                (p/'repair-start-01.json').write_text('{}')
            with mock.patch.object(S.R, 'run') as run:
                with self.assertRaisesRegex(S.R.RunnerError, 'REPAIR_APPROACH_EXHAUSTED'):
                    S.supervise(root, root/'vault', 2, 120)
                run.assert_not_called()

    def test_active_same_worktree_blocks_launch(self):
        root = Path('/workspace')
        with mock.patch.object(S, 'api', side_effect=[{'data': {'ses_test': {}}},
                {'data': {'location': {'directory': str(root)}}}]):
            with self.assertRaisesRegex(S.R.RunnerError, 'WORKTREE_SESSION_ACTIVE'):
                S.no_active_worker(root)

    def test_terminal_does_not_accept_failed_or_active_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); prior = root/'gate-test'; attempt = prior/'attempt-001'
            attempt.mkdir(parents=True)
            S.R.save_json(prior/'summary.json', {'attempts': 1})
            (attempt/'stdout.jsonl').write_text('{"sessionID":"ses_test"}\n')
            base = {'outcome': 'succeeded', 'time': {'idle': 1},
                    'model': {'providerID': 'openai', 'id': 'gpt-6.1-sol'},
                    'location': {'directory': str(root)}}
            for session, active in [(dict(base, outcome='failed'), {}), (base, {'ses_test': {}})]:
                with mock.patch.object(S, 'api', side_effect=[{'data': session}, {'data': active}]):
                    with self.assertRaisesRegex(S.R.RunnerError, 'SESSION_NOT_TERMINAL|SERVER_SESSION_FAILED'):
                        S.terminal(root, prior)

    def test_automatic_successor_only_after_live_review(self):
        for failure in (None, 'probe', 'terminal'):
            with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
                root = Path(tmp)
                (root/'tmp/local-experiment/autonomous-runner').mkdir(parents=True)
                stack.enter_context(mock.patch.object(S.R, 'checkout_head', return_value='a'*40))
                stack.enter_context(mock.patch.object(S.R, 'subscription_preflight'))
                stack.enter_context(mock.patch.object(S, 'no_active_worker'))
                terminal = stack.enter_context(mock.patch.object(S, 'terminal'))
                probe = stack.enter_context(mock.patch.object(S, 'probe', return_value={'verified': True}))
                command = stack.enter_context(mock.patch.object(S, 'command'))
                run = stack.enter_context(mock.patch.object(S.R, 'run', return_value=('run_budget_reached', root/'fake-run')))
                if failure == 'probe': probe.side_effect = S.R.RunnerError('INHIBITION_DRIFT')
                if failure == 'terminal': terminal.side_effect = S.R.RunnerError('SESSION_NOT_TERMINAL')
                if failure:
                    with self.assertRaises(S.R.RunnerError): S.supervise(root, root/'vault', 2, 120)
                    self.assertEqual(run.call_count, 1)
                    if failure == 'terminal': command.assert_not_called()
                else:
                    S.supervise(root, root/'vault', 2, 120)
                    self.assertEqual([c.kwargs['mission']['gate_id'] for c in run.call_args_list], [S.REPAIR, S.READY])
                    self.assertEqual(probe.call_count, 2)

    def test_transport_success_needs_exact_error_checkpoint_and_server_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); prior = root/'gate-test'; attempt = prior/'attempt-001'
            attempt.mkdir(parents=True)
            S.R.save_json(prior/'summary.json', dict(attempts=1, reason='OPENCODE_RUN_FAILED'))
            S.R.save_json(attempt/'checkpoint.json', dict(schema_version=1, nonce='a'*32,
                iteration=1, head='b'*40, decision='continue', reason='more_work'))
            session = dict(id='ses_test', outcome='succeeded', time={'idle':1},
                          model={'providerID':'openai','id':'gpt-6.1-sol'},
                          location={'directory':str(root)})
            for kind, success in [('provider.transport',True),('provider.authentication',False)]:
                (attempt/'stdout.jsonl').write_text(json.dumps(dict(type='error',sessionID='ses_test',
                    error={'type':kind,'message':'WebSocket closed with code 1000'}))+'\n')
                with mock.patch.object(S,'api',side_effect=[{'data':session},{'data':{}}]), \
                     mock.patch.object(S.R,'git',side_effect=lambda root,*args: 'b'*40 if args[0]=='rev-parse' else ''):
                    if success:
                        _, result, _ = S.terminal(root,prior,expected_before='a'*40)
                        self.assertTrue(result['server_reconciled_only'])
                    else:
                        with self.assertRaises(S.R.RunnerError): S.terminal(root,prior,expected_before='a'*40)
            self.assertFalse((attempt/'result.json').exists())

    def test_completed_repair_resume_selects_next_gate_without_repair_worker(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            (root/'tmp/local-experiment/autonomous-runner').mkdir(parents=True)
            stack.enter_context(mock.patch.object(S.R, 'checkout_head', return_value='a'*40))
            stack.enter_context(mock.patch.object(S.R, 'subscription_preflight'))
            stack.enter_context(mock.patch.object(S, 'check_resume_budget'))
            stack.enter_context(mock.patch.object(S, 'no_active_worker'))
            stack.enter_context(mock.patch.object(S, 'terminal'))
            reopen = stack.enter_context(mock.patch.object(S, 'reopen_completed_repair'))
            stack.enter_context(mock.patch.object(S, 'probe', return_value={'verified': True}))
            stack.enter_context(mock.patch.object(S, 'command'))
            run = stack.enter_context(mock.patch.object(S.R, 'run', return_value=('run_budget_reached', root/'fake-run')))
            S.supervise(root, root/'vault', 1, 120, root/'prior')
            reopen.assert_called_once()
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.kwargs['mission']['gate_id'], S.READY)

    def test_resume_rejects_uncompleted_worker_before_runtime_command(self):
        with mock.patch.object(S, 'terminal', return_value=({'reason': 'agent_safety_boundary_claim'},
                  {'reason': 'safety_boundary'}, 'ses_test')), mock.patch.object(S, 'command') as cmd:
            root = Path('/workspace')
            prior = root/'tmp/local-experiment/autonomous-runner/gate-test'
            with self.assertRaisesRegex(S.R.RunnerError, 'RESUME_NOT_COMPLETED_REPAIR'):
                S.reopen_completed_repair(root, root/'vault', prior, root/'evidence')
            cmd.assert_not_called()


if __name__ == '__main__': unittest.main()
