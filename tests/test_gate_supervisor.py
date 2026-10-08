import copy
from datetime import datetime, timezone
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
                    with self.assertRaisesRegex(S.R.RunnerError, 'SESSION_NOT_TERMINAL'):
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


if __name__ == '__main__': unittest.main()
