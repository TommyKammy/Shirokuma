"""Real recovery ledger/locks, distributed Docker observations mocked only."""
import copy
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import control_store
import execution_recovery as recovery
import logical_execution_recovery as logical
from logical_provider import _KubeMetadata
import query_service
import warehouse_admission as admission
from query_context import QueryError
import test_execution_recovery as fixtures


class DistributedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.StandaloneQueryRecoveryTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.operator = self.f.operator
        self.operator.__class__ = logical.LogicalQueryRecovery
        lifecycle = logical.warehouse_lifecycle
        self.real_invalidate, self.real_publish = lifecycle.invalidate_proof, lifecycle._save_proof
        invalidate = patch.object(lifecycle, 'invalidate_proof')
        publish = patch.object(lifecycle, '_save_proof')
        self.invalidate, self.publish = invalidate.start(), publish.start()
        self.addCleanup(invalidate.stop)
        self.addCleanup(publish.stop)
        self.counts = dict(requests=0, effects=0, roots=0)
        self.runtime_operations = []
        counts = patch.object(logical.logical_creations, 'compute_blockers', side_effect=lambda db: self.counts)
        runtime = patch.object(logical.warehouse_lifecycle, 'runtime_blockers', side_effect=lambda db: self.runtime_operations)
        counts.start()
        runtime.start()
        self.addCleanup(counts.stop)
        self.addCleanup(runtime.stop)
        self.adapter = logical.LogicalDockerAdapter.__new__(logical.LogicalDockerAdapter)
        self.adapter.foundation = dict(foundation_id='foundation', revision=1, images=dict(trino='trino@sha256:pin'))
        self.adapter.namespace = 'logical-ns'
        self.adapter.pins = {'namespace': 'original-namespace', 'deployment/trino-local': 'coordinator',
                             'deployment/trino-local-worker': 'worker'}
        self.adapter.members = 2
        self.adapter.material_digest = 'fixed-material'
        self.adapter.authenticate = self.operator.authenticate
        self.engine = dict(engine_id='engine', socket='unix:///colima/docker.sock', node_uid='node-uid', node_name='node')
        self.adapter.engine = Mock(return_value=self.engine)
        self.scope = dict(**self.engine, kind='logical-current-managed-scope-v1', foundation_id='foundation', revision=1,
            pins=self.adapter.pins, material_digest=self.adapter.material_digest, service_uid='service', members=[])
        self.rows, self.events, self.calls, self.replacements = {}, {}, [], {}
        self.extra_pods = []
        self.birth_override = None
        self.pod_birth_override = None
        self.same_pod = False
        self.restart_override = None
        self.unproved_stop = None
        for index, role in enumerate(('coordinator', 'worker')):
            cid = ('a' if index == 0 else 'b') * 64
            member = dict(**self.engine, container_id=cid, image='sha256:image',
                started_at='2026-01-01T00:00:00Z', labels=dict(zip(recovery.MANAGED_LABELS,
                    ('pod-'+role, 'logical-ns', role, 'trino'))), pod_uid='pod-'+role,
                pod_created_at='2025-12-01T00:00:00Z', restart_count=2,
                deployment_uid=role, deployment=('trino-local' if index == 0 else 'trino-local-worker'),
                replica_set_uid='rs-'+('trino-local' if index == 0 else 'trino-local-worker'))
            self.scope['members'].append(member)
            self.rows[cid] = dict(Id=cid, Image=member['image'], Config=dict(Labels=member['labels'], Image='trino@sha256:pin'), Mounts=[],
                pod_birth=member['pod_created_at'], restart_count=member['restart_count'],
                HostConfig=dict(RestartPolicy=dict(Name='no')),
                State=dict(Running=True, Status='running', StartedAt=member['started_at'], FinishedAt='', ExitCode=0))
        self.adapter._run = Mock(side_effect=self.command)
        self.adapter._docker = lambda *args: json.loads(self.adapter._run('docker', *args))
        self.adapter.scope = Mock(side_effect=self.current_scope)
        self.operator.adapter = Mock(return_value=self.adapter)
        self.fail_stop = None
        self.no_events = False
        self.provider = Mock()
        self.provider._matches_spec = logical.Provider._matches_spec
        self.endpoints = {}
        self.provider._cmd.side_effect = self.metadata
        self.provider._json.side_effect = json.loads
        self.provider._owned.side_effect = self.owned
        self.adapter.rendered = [dict(kind='Deployment', metadata=dict(name=name),
            spec=dict(template=dict(spec=dict(containers=[dict(name='trino', volumeMounts=[])]))))
            for name in ('trino-local', 'trino-local-worker')]
        @contextmanager
        def provider(**kwargs):
            self.assertFalse(kwargs.get('full', False))
            yield self.provider
        self.adapter._provider = provider

    def metadata(self, args):
        # Match the real native transport's target gate, rather than allowing
        # arbitrary resource GETs through an injected Provider command seam.
        _KubeMetadata._target(args, {'namespace': self.adapter.namespace}, 'logical')
        self.assertEqual(args, ['get', 'pods', '-o', 'json'])
        return json.dumps(dict(items=self.current_pods()))

    def owned(self, kind, name):
        self.assertEqual(kind, 'replicaset')
        return self.replica_set(name)

    def replica_set(self, name):
        return dict(metadata=dict(uid='rs-'+name, ownerReferences=[dict(kind='Deployment', name=name,
            uid=self.adapter.pins['deployment/'+name], controller=True)]),
            spec=dict(template=dict(spec=dict(containers=[dict(name='trino', volumeMounts=[])])), selector=dict(matchLabels={'app': name})))

    def current_pods(self):
        pods = []
        for member in self.scope['members']:
            row = self.rows.get(member['container_id'])
            if row is None or not row['State']['Running']:
                cid = ('c' if member['deployment'] == 'trino-local' else 'd')*64
                if cid not in self.replacements:
                    replacement = copy.deepcopy(self.rows.get(member['container_id']) or self.original_rows[member['container_id']])
                    replacement['Id'] = cid
                    replacement['State'].update(Running=True, Status='running', StartedAt=self.birth_override or admission._now())
                    if self.same_pod:
                        replacement['restart_count'] = member['restart_count'] + 1 if self.restart_override is None else self.restart_override
                    else:
                        replacement['Config']['Labels']['io.kubernetes.pod.uid'] = 'new-'+member['pod_uid']
                        replacement['pod_birth'] = self.pod_birth_override or admission._now()
                        # The container cannot have started before its new Pod.
                        if self.birth_override is None:
                            replacement['State']['StartedAt'] = admission._now()
                        replacement['restart_count'] = 0 if self.restart_override is None else self.restart_override
                    self.replacements[cid] = replacement
                row = self.replacements[cid]
            labels = row['Config']['Labels']
            name = member['deployment']
            pods.append(dict(metadata=dict(uid=labels['io.kubernetes.pod.uid'], name=labels['io.kubernetes.pod.name'], namespace=self.adapter.namespace,
                creationTimestamp=row.get('pod_birth'),
                labels={'app': name}, ownerReferences=[dict(kind='ReplicaSet', name=name, uid='rs-'+name, controller=True)]),
                spec=dict(nodeName=self.engine['node_name'], containers=[dict(name='trino', volumeMounts=[])]),
                status=dict(containerStatuses=[dict(name='trino', containerID='docker://'+row['Id'], restartCount=row.get('restart_count'))])))
        return pods + self.extra_pods

    def current_scope(self, *args, **kwargs):
        self.original_rows = getattr(self, 'original_rows', copy.deepcopy(self.rows))
        self.current_pods()
        result = copy.deepcopy(self.scope)
        for member in result['members']:
            row = self.rows.get(member['container_id'])
            if row is None or not row['State']['Running']:
                member['container_id'] = ('c' if member['deployment'] == 'trino-local' else 'd') * 64
                replacement = self.replacements[member['container_id']]
                member.update(started_at=replacement['State']['StartedAt'], pod_created_at=replacement['pod_birth'],
                    restart_count=replacement['restart_count'], labels=copy.deepcopy(replacement['Config']['Labels']),
                    pod_uid=replacement['Config']['Labels']['io.kubernetes.pod.uid'])
        return result

    def command(self, program, *args):
        if program == 'kubectl':
            self.assertEqual(args, ('-n', self.adapter.namespace, 'get', 'endpoints', 'trino-local', '-o', 'json'))
            return json.dumps(self.endpoints)
        self.assertEqual(program, 'docker')
        action, cid = args[0], args[-1]
        if action == 'inspect':
            rows = {**self.rows, **self.replacements}
            if any(target not in rows for target in args[1:]):
                raise QueryError('RECOVERY_BACKEND_UNAVAILABLE', 409)
            return json.dumps([rows[target] for target in args[1:]])
        if action == 'events':
            target = next(a.split('=', 1)[1] for a in args if a.startswith('container='))
            return '\n'.join(json.dumps(e) for e in ([] if self.no_events else self.events.get(target, [])))
        if action == 'ps':
            if not any(a.startswith('id=') for a in args):
                return '\n'.join(cid for cid, row in {**self.rows, **self.replacements}.items() if row['State']['Running'])
            target = next(a.split('=', 1)[1] for a in args if a.startswith('id='))
            return target if target in self.rows else ''
        if action == 'stop':
            with control_store.connect() as db:
                self.assertEqual(admission.control(db)['state'], 'DRAINING')
                row = db.execute('SELECT * FROM execution_recovery_requests').fetchone()
                self.assertEqual(row['phase'], 'STOP_INTENT')
                self.assertIsNone(row['proof'])
            if self.fail_stop == cid:
                raise QueryError('RECOVERY_BACKEND_UNAVAILABLE', 409)
            if self.unproved_stop == cid:
                self.calls.append((action, cid))
                return ''
            self.rows[cid]['State'].update(Running=False, Status='exited', FinishedAt=admission._now())
            labels = self.rows[cid]['Config']['Labels']
            self.events[cid] = [dict(Type='container', Action='die', Actor=dict(ID=cid, Attributes=labels),
                                      timeNano=recovery._nano(admission._now()))]
        elif action == 'top':
            return 'PID COMMAND ARGS\n1 java io.trino.server.TrinoServer'
        elif action == 'rm':
            with control_store.connect() as db:
                row = dict(db.execute('SELECT * FROM execution_recovery_requests').fetchone())
                self.assertEqual(row['phase'], 'PROVED')
                proof = json.loads(row['proof'])
                self.assertEqual(len(proof['members']), self.adapter.members)
                self.adapter.validate_proof(self.scope, proof, json.loads(row['intent'])['planned_at'])
            self.assertFalse(self.rows[cid]['State']['Running'])
            del self.rows[cid]
        else:
            self.fail('unexpected Docker call '+str(args))
        self.calls.append((action, cid))
        return ''

    def plan(self):
        return self.operator.plan(self.f.query_id, 'logical-recovery')

    @contextmanager
    def epoch_store(self):
        lifecycle = logical.warehouse_lifecycle
        path = self.f.accounts.root / 'recovery-engine-ready.json'
        with patch.object(lifecycle, '_proof_path', return_value=path), \
             patch.object(lifecycle, '_LOCAL_PROOF_ID', None), \
             patch.object(lifecycle, 'applicable', return_value=True), \
             patch.object(logical.runtime_profile, 'logical_config', return_value=dict(
                 foundation_generation_id='foundation', foundation_revision=1)):
            self.invalidate.side_effect, self.publish.side_effect = self.real_invalidate, self.real_publish
            yield path

    def apply(self, result, **kwargs):
        return self.operator.apply(result['operation_id'], result['revision'],
            expected_intent_digest=result['intent_digest'], **kwargs)

    def inspect(self, plan):
        return self.operator.inspect(plan['operation_id'])

    def test_all_members_proved_durably_before_any_removal_no_sql_replay(self):
        before = self.f.snapshot()
        with patch.object(query_service, '_transport', side_effect=AssertionError('SQL replay')):
            plan = self.plan()
            self.assertEqual(self.plan(), plan)
            result = self.apply(plan)
            self.assertEqual(result['phase'], 'OPEN')
            self.assertEqual(self.f.snapshot(), before)
            self.assertEqual(self.calls, [('stop', 'a'*64), ('stop', 'b'*64), ('rm', 'a'*64), ('rm', 'b'*64)])
            self.assertEqual(self.apply(result), result)
        with control_store.connect() as db:
            a = db.execute("SELECT execution_closure FROM warehouse_admissions WHERE kind='query' AND subject_id=?",
                           (self.f.query_id,)).fetchone()
            self.assertEqual(a[0], recovery.CLOSURE)
            self.assertEqual(admission.control(db)['state'], 'OPEN')

    def test_shared_epoch_invalidated_before_coordinator_dispatch_published_before_reopen(self):
        lifecycle = logical.warehouse_lifecycle
        def assert_physical_lock():
            import fcntl
            with (control_store.root() / 'local-lifecycle.lock').open('a') as stream:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.epoch_store() as path:
            self.real_publish()
            before = json.loads(path.read_text())
            peer_epoch = lifecycle._LOCAL_PROOF_ID
            self.assertFalse(lifecycle.requires_wake())
            plan = self.plan()
            self.assertEqual(json.loads(path.read_text()), before)  # Planning is not a fence.
            self.publish.assert_not_called()
            def invalidate():
                assert_physical_lock()
                self.real_invalidate()
            self.invalidate.side_effect = invalidate
            command = self.adapter._run.side_effect
            def dispatch(program, *args):
                if program == 'docker' and args[0] == 'stop':
                    assert_physical_lock()
                    self.assertFalse(path.exists())
                    self.invalidate.assert_called_once()
                return command(program, *args)
            self.adapter._run.side_effect = dispatch
            scope = self.adapter.scope.side_effect
            def validated_scope(*args, **kwargs):
                if not self.rows:
                    assert_physical_lock()
                return scope(*args, **kwargs)
            self.adapter.scope.side_effect = validated_scope
            def publish():
                assert_physical_lock()
                with control_store.connect() as db:
                    self.assertEqual(admission.control(db)['state'], 'DRAINING')
                    self.assertEqual(db.execute('SELECT phase FROM execution_recovery_requests').fetchone()[0], 'CLOSED')
                self.assertFalse(self.rows)
                self.real_publish()
            self.publish.side_effect = publish
            result = self.apply(plan)
            self.assertEqual(result['phase'], 'OPEN')
            self.publish.assert_called_once()
            after = json.loads(path.read_text())
            self.assertNotEqual(before['epoch'], after['epoch'])
            with patch.object(lifecycle, '_LOCAL_PROOF_ID', peer_epoch):
                self.assertTrue(lifecycle.requires_wake())  # Idle HTTP peer must rebind its own tunnel.
            self.assertEqual(self.apply(result), result)
            self.invalidate.assert_called_once()
            self.publish.assert_called_once()  # OPEN never replays healthy/publication.

    def test_epoch_not_published_on_failed_health_or_pre_fence_block(self):
        with self.epoch_store() as path:
            self.real_publish()
            original = path.read_bytes()
            plan = self.plan()
            self.counts['effects'] = 1
            with self.assertRaises(QueryError):
                self.apply(plan)
            self.assertEqual(path.read_bytes(), original)
            self.invalidate.assert_not_called()
            self.publish.assert_not_called()
            self.counts['effects'] = 0
            scope = self.adapter.scope.side_effect
            def failed_health(*args, **kwargs):
                if not self.rows:
                    raise QueryError('FOUNDATION_COMPUTE_NOT_READY', 409)
                return scope(*args, **kwargs)
            self.adapter.scope.side_effect = failed_health
            with self.assertRaises(QueryError):
                self.apply(plan)
            self.assertEqual(self.inspect(plan)['phase'], 'CLOSED')
            self.assertFalse(path.exists())
            self.publish.assert_not_called()

    def test_no_epoch_publication_when_final_health_authority_lost(self):
        plan = self.plan()
        authenticate = self.adapter.authenticate
        def current_auth():
            if not self.rows:
                raise QueryError('RECOVERY_ADMIN_REQUIRED', 409)
            return authenticate()
        self.adapter.authenticate = current_auth
        with self.assertRaises(QueryError):
            self.apply(plan)
        self.assertEqual(self.inspect(plan)['phase'], 'CLOSED')
        self.publish.assert_not_called()

    def test_partial_reconcile_keeps_proof_invalid_and_resume_does_not_refence_coordinator(self):
        with self.epoch_store() as path:
            self.real_publish()
            plan = self.plan()
            self.fail_stop = 'b'*64
            with self.assertRaises(QueryError):
                self.apply(plan)
            self.assertFalse(path.exists())
            partial, calls = self.inspect(plan), list(self.calls)
            self.assertEqual(self.apply(partial, reconcile=True), partial)
            self.assertEqual(self.calls, calls)
            self.invalidate.assert_called_once()
            self.publish.assert_not_called()
            self.fail_stop = None
            self.assertEqual(self.apply(partial)['phase'], 'OPEN')
            self.invalidate.assert_called_once()
            self.publish.assert_called_once()

    def test_coordinator_stopped_and_proved_first_even_with_worker_id_first(self):
        self.scope['members'].reverse()  # worker b sorts before coordinator f
        coordinator = self.scope['members'][1]
        row = self.rows.pop(coordinator['container_id'])
        coordinator['container_id'] = row['Id'] = 'f'*64
        self.rows[row['Id']] = row
        result = self.apply(self.plan())
        self.assertEqual(result['phase'], 'OPEN')
        self.assertEqual([cid for action, cid in self.calls if action == 'stop'], ['f'*64, 'b'*64])

    def test_no_worker_dispatch_without_positive_coordinator_stop(self):
        plan = self.plan()
        self.unproved_stop = 'a'*64
        with self.assertRaises(QueryError):
            self.apply(plan)
        self.assertEqual(self.calls, [('stop', 'a'*64)])
        self.assertEqual(self.inspect(plan)['phase'], 'STOP_INTENT')

    def test_coordinator_only_scope_requires_one_exact_proof(self):
        self.scope['members'] = [self.scope['members'][0]]
        self.adapter.members = 1
        del self.rows['b'*64]
        result = self.apply(self.plan())
        self.assertEqual(result['phase'], 'OPEN')
        self.assertEqual(self.calls, [('stop', 'a'*64), ('rm', 'a'*64)])
        self.assertEqual(len(result['proof']['members']), 1)

    def test_pre_fence_replacement_rejected_before_aggregate_proof_or_removal(self):
        plan = self.plan()
        self.birth_override = '2026-01-01T00:00:00Z'
        with self.assertRaises(QueryError):
            self.apply(plan)
        result = self.inspect(plan)
        self.assertEqual(result['phase'], 'STOP_INTENT')
        self.assertIsNone(result['proof'])
        self.assertEqual(self.calls, [('stop', 'a'*64)])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT execution_closure FROM warehouse_admissions').fetchone()[0], '')

    def test_pre_fence_unplanned_pod_with_post_fence_container_rejected_before_proved(self):
        before = self.f.snapshot()
        plan = self.plan()
        self.pod_birth_override = '2026-01-01T00:00:00Z'
        with self.assertRaises(QueryError) as caught:
            self.apply(plan)
        self.assertEqual(caught.exception.code, 'RECOVERY_REPLACEMENT_BEFORE_FENCE')
        self.assertEqual(self.inspect(plan)['phase'], 'STOP_INTENT')
        self.assertIsNone(self.inspect(plan)['proof'])
        self.assertEqual(self.calls, [('stop', 'a'*64)])
        self.assertEqual(self.f.snapshot(), before)
        self.assertGreater(recovery._nano(self.replacements['c'*64]['State']['StartedAt']),
                           recovery._nano(self.rows['a'*64]['State']['FinishedAt']))
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT execution_closure FROM warehouse_admissions').fetchone()[0], '')

    def test_same_original_pod_one_proved_restart_is_allowed(self):
        self.same_pod = True
        before = self.f.snapshot()
        result = self.apply(self.plan())
        self.assertEqual(result['phase'], 'OPEN')
        self.assertEqual(self.f.snapshot(), before)
        self.assertTrue(all(row['restart_count'] == 3 for row in self.replacements.values()))
        self.assertEqual([cid for action, cid in self.calls if action == 'stop'], ['a'*64, 'b'*64])

    def test_same_original_pod_multiple_restarts_are_unproved_history(self):
        self.same_pod, self.restart_override = True, 4
        plan = self.plan()
        with self.assertRaises(QueryError) as caught:
            self.apply(plan)
        self.assertEqual(caught.exception.code, 'RECOVERY_POD_HISTORY_UNPROVED')
        self.assertEqual(self.inspect(plan)['phase'], 'STOP_INTENT')
        self.assertEqual(self.calls, [('stop', 'a'*64)])

    def test_same_pod_restart_without_original_container_proof_reconcile_has_no_mutations(self):
        self.same_pod = True
        plan = self.plan()
        self.fail_stop = 'b'*64
        with self.assertRaises(QueryError):
            self.apply(plan)
        del self.rows['b'*64]
        self.no_events = True
        before = list(self.calls)
        with self.assertRaises(QueryError) as caught:
            self.apply(self.inspect(plan), reconcile=True)
        self.assertEqual(caught.exception.code, 'RECOVERY_POD_HISTORY_UNPROVED')
        self.assertEqual(self.calls, before)
        self.assertEqual(self.inspect(plan)['phase'], 'STOP_INTENT')

    def test_post_fence_new_pod_with_later_restarts_is_allowed(self):
        self.restart_override = 5
        self.assertEqual(self.apply(self.plan())['phase'], 'OPEN')
        self.assertTrue(all(row['restart_count'] == 5 for row in self.replacements.values()))

    def test_worker_replacement_before_fence_rejected_on_partial_reconcile_without_mutation(self):
        plan = self.plan()
        self.fail_stop = 'b'*64
        with self.assertRaises(QueryError):
            self.apply(plan)
        self.rows['b'*64]['State'].update(Running=False, Status='exited', FinishedAt=admission._now())
        self.birth_override = '2026-01-01T00:00:00Z'
        before = list(self.calls)
        with self.assertRaises(QueryError):
            self.apply(self.inspect(plan), reconcile=True)
        self.assertEqual(self.calls, before)
        self.assertEqual(self.inspect(plan)['phase'], 'STOP_INTENT')

    def test_unexplained_engine_peer_and_foreign_endpoint_rejected_before_proof(self):
        plan = self.plan()
        self.fail_stop = 'b'*64
        with self.assertRaises(QueryError):
            self.apply(plan)
        peer = copy.deepcopy(self.rows['b'*64])
        peer['Id'] = 'e'*64
        self.replacements[peer['Id']] = peer
        before = list(self.calls)
        with self.assertRaises(QueryError):
            self.apply(self.inspect(plan), reconcile=True)
        del self.replacements[peer['Id']]
        self.extra_pods = [copy.deepcopy(self.current_pods()[1])]
        with self.assertRaises(QueryError):
            self.apply(self.inspect(plan), reconcile=True)
        self.extra_pods = []
        self.endpoints = dict(subsets=[dict(addresses=[dict(targetRef=dict(uid='foreign', namespace='logical-ns'))])])
        with self.assertRaises(QueryError):
            self.apply(self.inspect(plan), reconcile=True)
        self.assertEqual(self.calls, before)

    def test_membership_rechecked_after_removals_before_execution_closure(self):
        plan = self.plan()
        remove = self.adapter.remove
        def remove_then_drift(scope):
            remove(scope)
            self.replacements['d'*64]['State']['StartedAt'] = '2026-01-01T00:00:00Z'
        self.adapter.remove = remove_then_drift
        with self.assertRaises(QueryError):
            self.apply(plan)
        self.assertEqual(self.inspect(plan)['phase'], 'PROVED')
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT execution_closure FROM warehouse_admissions').fetchone()[0], '')

    def test_creation_and_runtime_blockers_reject_graph_binding_and_final_reopen(self):
        plan = self.plan()
        for field in self.counts:
            self.counts[field] = 1
            with self.assertRaises(QueryError):
                self.apply(plan)
            self.counts[field] = 0
        self.runtime_operations.append(dict(id='foreign-unknown', state='UNKNOWN'))
        with self.assertRaises(QueryError):
            self.apply(plan)
        self.runtime_operations.clear()
        healthy = self.adapter.healthy
        def healthy_then_foreign_work(*args):
            result = healthy(*args)
            self.counts['effects'] = 1
            return result
        self.adapter.healthy = healthy_then_foreign_work
        with self.assertRaises(QueryError):
            self.apply(plan)
        self.assertEqual(self.inspect(plan)['phase'], 'CLOSED')
        with control_store.connect() as db:
            self.assertEqual(admission.control(db)['state'], 'DRAINING')
        self.counts['effects'] = 0
        self.adapter.healthy = healthy
        self.assertEqual(self.apply(self.inspect(plan))['phase'], 'OPEN')

    def with_foreign_standalone(self):
        foreign = ForeignFixture()
        self.replacements[foreign.cid] = foreign.row
        original = self.adapter._run
        def command(program, *args):
            if program == 'docker' and args[:2] == ('top', foreign.cid):
                return foreign.process
            return original(program, *args)
        self.adapter._run = command
        self.adapter._foreign_standalone = foreign.adapter._foreign_standalone
        return foreign

    def test_foreign_standalone_preserved_partial_reconcile_revalidates_without_mutations(self):
        foreign = self.with_foreign_standalone()
        plan = self.plan()
        self.fail_stop = 'b'*64
        with self.assertRaises(QueryError):
            self.apply(plan)
        partial = self.inspect(plan)
        calls = list(self.calls)
        foreign.adapter._archive.reset_mock()
        self.assertEqual(self.apply(partial, reconcile=True), partial)
        foreign.adapter._archive.assert_called()
        self.assertEqual(self.calls, calls)
        content = foreign.content
        foreign.content = foreign.cm['data']['config.properties'] = content.replace('coordinator=true', 'coordinator=false', 1)
        with self.assertRaises(QueryError):
            self.apply(partial, reconcile=True)
        self.assertEqual(self.calls, calls)
        foreign.content = foreign.cm['data']['config.properties'] = content
        self.fail_stop = None
        self.assertEqual(self.apply(partial)['phase'], 'OPEN')
        self.assertTrue(foreign.row['State']['Running'])
        self.assertTrue(all(cid in ('a'*64, 'b'*64) for _, cid in self.calls))

    def test_foreign_revalidation_after_removal_blocks_closure_on_drift(self):
        foreign = self.with_foreign_standalone()
        plan = self.plan()
        remove = self.adapter.remove
        def remove_then_drift(scope):
            remove(scope)
            foreign.content = foreign.cm['data']['config.properties'] = foreign.content.replace('localhost', 'external')
        self.adapter.remove = remove_then_drift
        with self.assertRaises(QueryError):
            self.apply(plan)
        self.assertEqual(self.inspect(plan)['phase'], 'PROVED')
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT execution_closure FROM warehouse_admissions').fetchone()[0], '')
        self.assertTrue(foreign.row['State']['Running'])

    def test_partial_stop_restart_reconcile_never_stops_and_explicit_resume_only_old_ids(self):
        plan = self.plan()
        self.fail_stop = 'b'*64
        with self.assertRaises(QueryError):
            self.apply(plan)
        partial = self.inspect(plan)
        self.assertEqual(partial['phase'], 'STOP_INTENT')
        self.assertEqual(self.calls, [('stop', 'a'*64)])
        self.assertEqual(self.apply(partial, reconcile=True), partial)
        self.assertEqual(self.calls, [('stop', 'a'*64)])
        # Fresh operator and adapter, retaining only durable ledger + daemon data.
        operator = logical.LogicalQueryRecovery.__new__(logical.LogicalQueryRecovery)
        for name in ('actor', 'config', 'seal', 'adapter'):
            setattr(operator, name, getattr(self.operator, name))
        self.operator = operator
        restarted = logical.LogicalDockerAdapter.__new__(logical.LogicalDockerAdapter)
        restarted.__dict__.update(self.adapter.__dict__)
        restarted.__dict__.pop('since', None)
        self.adapter = restarted
        self.operator.adapter = Mock(return_value=restarted)
        self.fail_stop = None
        self.assertEqual(self.apply(partial)['phase'], 'OPEN')
        self.assertEqual([cid for action, cid in self.calls if action == 'stop'], ['a'*64, 'b'*64])

    def test_absence_is_not_proof_keeps_global_drain(self):
        plan = self.plan()
        self.fail_stop = 'b'*64
        with self.assertRaises(QueryError):
            self.apply(plan)
        del self.rows['a'*64]
        self.no_events = True
        partial = self.inspect(plan)
        with self.assertRaises(QueryError):
            self.apply(partial, reconcile=True)
        with self.assertRaises(QueryError):
            self.apply(partial)
        with control_store.connect() as db:
            self.assertEqual(admission.control(db)['state'], 'DRAINING')
            self.assertTrue(admission.blockers(db))
        self.assertFalse(any(action == 'rm' for action, _ in self.calls))

    def test_reconcile_positive_events_closes_but_explicit_apply_required_for_full_probe(self):
        plan = self.plan()
        self.fail_stop = 'b'*64
        with self.assertRaises(QueryError):
            self.apply(plan)
        self.fail_stop = None
        self.command('docker', 'stop', '--time', '10', 'b'*64)
        # Simulate Kube GC after positive die events; GET-only reconcile may
        # consume the events, but never remove containers or run a SQL probe.
        self.rows.clear()
        self.adapter.scope.reset_mock()
        result = self.apply(self.inspect(plan), reconcile=True)
        self.assertEqual(result['phase'], 'CLOSED')
        self.adapter.scope.assert_not_called()
        with control_store.connect() as db:
            self.assertEqual(admission.control(db)['state'], 'DRAINING')
        self.assertEqual(self.apply(result)['phase'], 'OPEN')

    def test_saved_bundle_corruption_refused_before_deletion(self):
        plan = self.plan()
        original = self.adapter.remove
        self.adapter.remove = Mock(side_effect=QueryError('interruption', 409))
        with self.assertRaises(QueryError):
            self.apply(plan)
        proved = self.inspect(plan)
        self.assertEqual(proved['phase'], 'PROVED')
        for mutate in (lambda p: p['members'].pop(),
                       lambda p: p['members'].__setitem__(1, copy.deepcopy(p['members'][0])),
                       lambda p: p['members'][0]['receipt'].update(container_id='f'*64),
                       lambda p: p.update(scope_digest='forged')):
            proof = copy.deepcopy(proved['proof'])
            mutate(proof)
            with control_store.connect() as db:
                db.execute('UPDATE execution_recovery_requests SET proof=?', (json.dumps(proof),))
            self.adapter.remove = Mock(wraps=original)
            with self.assertRaises(QueryError):
                self.apply(proved)
            self.adapter.remove.assert_not_called()
        self.assertFalse(any(action == 'rm' for action, _ in self.calls))

    def test_replacement_startup_delay_keeps_closed_drain_and_retry_never_stops_again(self):
        plan = self.plan()
        def scope(*args, **kwargs):
            if not self.rows:
                raise QueryError('FOUNDATION_COMPUTE_NOT_READY', 409)
            return self.current_scope(*args, **kwargs)
        self.adapter.scope.side_effect = scope
        with self.assertRaises(QueryError):
            self.apply(plan)
        closed = self.inspect(plan)
        self.assertEqual(closed['phase'], 'CLOSED')
        with control_store.connect() as db:
            self.assertEqual(admission.control(db)['state'], 'DRAINING')
        stopped = list(self.calls)
        self.adapter.scope.side_effect = self.current_scope
        self.assertEqual(self.apply(closed)['phase'], 'OPEN')
        self.assertEqual(self.calls, stopped)

    def test_engine_foundation_or_new_unproved_member_refused(self):
        plan = self.plan()
        for name, value in (('pins', {'namespace': 'replaced'}), ('material_digest', 'changed')):
            old = getattr(self.adapter, name)
            setattr(self.adapter, name, value)
            with self.assertRaises(QueryError):
                self.apply(self.inspect(plan))
            setattr(self.adapter, name, old)
        self.adapter.engine.return_value = dict(self.engine, engine_id='other-engine')
        with self.assertRaises(QueryError):
            self.apply(self.inspect(plan))
        self.assertEqual(self.calls, [])

    def test_digest_cas_graph_write_and_foreign_actor_refusals(self):
        plan = self.plan()
        for args in (dict(expected_revision=plan['revision']+1, expected_intent_digest=plan['intent_digest']),
                     dict(expected_revision=plan['revision'], expected_intent_digest='forged')):
            with self.assertRaises(QueryError):
                self.operator.apply(plan['operation_id'], **args)
        with control_store.connect() as db:
            db.execute("UPDATE query_execution_contexts SET mode='sandbox-write' WHERE query_id=?", (self.f.query_id,))
        with self.assertRaises(QueryError):
            self.apply(plan)
        self.assertEqual(self.calls, [])

    def test_foreign_unresolved_work_blocks_planning(self):
        self.f.unknown(sql='SELECT 19')
        with self.assertRaises(QueryError):
            self.plan()
        self.assertEqual(self.calls, [])

    def test_owner_lease_and_lost_authority_before_second_stop_hold_global_drain(self):
        with query_service._acquire_query_owner(self.f.query_id):
            with self.assertRaises(QueryError):
                self.plan()
        plan = self.plan()
        original = self.adapter.authenticate
        def authenticate():
            if ('stop', 'a'*64) in self.calls:
                raise QueryError('RECOVERY_ADMIN_REQUIRED', 409)
            return original()
        self.adapter.authenticate = authenticate
        with self.assertRaises(QueryError):
            self.apply(plan)
        self.assertEqual(self.calls, [('stop', 'a'*64)])
        with control_store.connect() as db:
            self.assertEqual(admission.control(db)['state'], 'DRAINING')
            self.assertTrue(admission.blockers(db))


class AdapterBoundaryTests(unittest.TestCase):
    def test_endpoint_get_uses_sealed_adapter_transport_not_provider_allowlist(self):
        from types import SimpleNamespace
        adapter = logical.LogicalDockerAdapter.__new__(logical.LogicalDockerAdapter)
        adapter.config = dict(kube_context='colima-mac-studio-solo')
        adapter.foundation = dict(kube=dict(kubeconfig='/private/sealed-kubeconfig'))
        adapter.namespace = 'sealed-logical'
        args = ['get', 'endpoints', 'trino-local', '-o', 'json']
        with self.assertRaises(RuntimeError):
            _KubeMetadata._target(args, {'namespace': adapter.namespace}, 'logical')
        with patch.object(recovery.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout='{"subsets":[]}')) as command:
            self.assertEqual(adapter._kube('endpoints', 'trino-local'), {'subsets': []})
        self.assertEqual(command.call_args.args[0], ['rtk', 'proxy', 'kubectl', '--context',
            'colima-mac-studio-solo', '--kubeconfig', '/private/sealed-kubeconfig',
            '-n', 'sealed-logical', *args])

    def test_exact_profile_configures_logical_auth_context_before_use(self):
        from types import SimpleNamespace
        profile = SimpleNamespace(mode='logical_v1')
        config = dict(runtime_profile='/private/exact-profile')
        def base_init(operator, path):
            operator.config, operator.profile, operator.seal = config, profile, {'seal': 'exact'}
        with patch.object(recovery.QueryRecovery, '__init__', base_init), \
             patch.object(logical.runtime_profile, 'configure_process', return_value=True) as configure, \
             patch.object(logical.runtime_profile, 'current', return_value=profile), \
             patch.object(logical.runtime_profile, 'private_binding', return_value={'seal': 'exact'}), \
             patch.dict(logical.os.environ, {}, clear=False):
            logical.LogicalQueryRecovery(Path('/private/recovery-config'))
            configure.assert_called_once_with()
            self.assertEqual(logical.os.environ['SHIROKUMA_RUNTIME_PROFILE'], config['runtime_profile'])

    def test_registered_logical_adapter_never_reads_legacy_generation_plan(self):
        from types import SimpleNamespace
        operator = logical.LogicalQueryRecovery.__new__(logical.LogicalQueryRecovery)
        operator.config = dict(runtime_profile='/private/profile', kube_context='colima-mac-studio-solo')
        operator.profile, operator.seal = SimpleNamespace(mode='logical_v1'), {'sealed': True}
        c = dict(environment_id='environment', warehouse_id='LOCAL', foundation_generation_id='foundation',
            foundation_revision=1, provider_config_file='/private/foundation')
        registered = dict(status='READY', generation_id='foundation', revision=1, config_ref='/private/foundation',
                          verification_ref='/private/probe')
        foundation = dict(foundation_id='foundation', revision=1, environment_id='environment', warehouse_id='LOCAL',
                          kube=dict(context='colima-mac-studio-solo'))
        saved = dict(ready=True, readiness=dict.fromkeys(('storage', 'polaris', 'trino', 'runtime_files', 'spi'), True))
        receipt = dict(kind='foundation_probe', generation_id='foundation', revision=1, result=saved)
        with patch.object(logical.runtime_profile, 'private_binding', return_value=operator.seal), \
             patch.object(logical.runtime_profile, 'generation_attestation'), \
             patch.object(logical.runtime_profile, 'logical_config', return_value=c), \
             patch.object(logical.runtime_profile, 'private_json', return_value=receipt), \
             patch.object(logical.control_store, 'connect') as connect, \
             patch.object(logical.logical_registry, 'get_foundation', return_value=registered), \
             patch.object(logical.logical_foundation, 'load', return_value=foundation), \
             patch.object(logical, 'LogicalDockerAdapter') as adapter:
            operator.adapter(dict(generation=dict(id='foundation')))
            adapter.assert_called_once_with(operator.config, operator.profile, foundation, saved, operator.authenticate)
            connect.return_value.__enter__.return_value.execute.assert_not_called()
            registered['revision'] = 2
            with self.assertRaises(QueryError):
                operator.adapter(dict(generation=dict(id='foundation')))

    def test_full_bind_required_and_always_closed(self):
        adapter = logical.LogicalDockerAdapter.__new__(logical.LogicalDockerAdapter)
        adapter.foundation, adapter.saved = {}, {'ready': True}
        provider = Mock()
        with patch.object(logical, 'Provider', return_value=provider):
            with adapter._provider(full=True):
                pass
            self.assertEqual(provider.method_calls[:3], [unittest.mock.call._trust(),
                unittest.mock.call.open(), unittest.mock.call.bind_registered(adapter.saved)])
            provider.close.assert_called_once()
            provider.reset_mock()
            provider.bind_registered.side_effect = ValueError('invalid saved pins')
            with self.assertRaises(ValueError):
                with adapter._provider(full=True):
                    self.fail('must not acquire scope')
            provider.close.assert_called_once()

    def test_get_only_binding_during_reconcile_never_opens_tunnels_or_probes_sql(self):
        adapter = logical.LogicalDockerAdapter.__new__(logical.LogicalDockerAdapter)
        adapter.foundation, adapter.pins, adapter.rendered, adapter.private = {}, {'namespace': 'uid'}, [], []
        provider = Mock()
        provider._incarnation = {}
        with patch.object(logical, 'Provider', return_value=provider):
            with adapter._provider():
                pass
        provider._trust.assert_called_once()
        provider._stable_health.assert_called_once()
        provider.open.assert_not_called()
        provider.bind_registered.assert_not_called()
        provider._sql.assert_not_called()
        provider.close.assert_called_once()

    def test_real_inventory_rejects_extra_digest_only_trino_and_process(self):
        adapter = logical.LogicalDockerAdapter.__new__(logical.LogicalDockerAdapter)
        member = dict(container_id='a'*64, image='image', labels={}, started_at='start')
        expected = dict(Id='a'*64, Image='image', Config=dict(Labels={}),
            State=dict(Running=True, StartedAt='start'), HostConfig=dict(RestartPolicy=dict(Name='no')))
        member['labels'] = recovery._labels(expected)
        other = dict(Id='b'*64, Image='other', Config=dict(Image='sha256:unclassified'))
        adapter._docker = Mock(return_value=[expected, other])
        def command(program, *args):
            if args[0] == 'ps':
                return 'a'*64+'\n'+'b'*64
            return 'PID COMMAND ARGS\n1 java io.trino.server.TrinoServer' if args[1] == 'a'*64 else other_process
        adapter._run = command
        for other_process, other_image in (('PID COMMAND ARGS\n2 java io.trino.server.TrinoServer', 'other'),
                                            ('PID COMMAND ARGS\n2 java unknown.Main', 'image')):
            other['Image'] = other_image
            with self.assertRaises(QueryError):
                adapter._inventory([member])
        other_process = 'PID COMMAND ARGS\n2 harmless'
        adapter._inventory([member])  # Image identity alone does not classify a backend.


def config_archive(content, *, name='config.properties', kind=tarfile.REGTYPE, extra=False):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        info = tarfile.TarInfo(name)
        info.type, info.size = kind, len(content)
        archive.addfile(info, io.BytesIO(content))
        if extra:
            archive.addfile(tarfile.TarInfo('unexpected'))
    return output.getvalue()


class ForeignFixture:
    def __init__(self):
        self.adapter = logical.LogicalDockerAdapter.__new__(logical.LogicalDockerAdapter)
        a = self.adapter
        a.namespace, a.foundation = 'logical-ns', dict(foundation_id='foundation')
        a.pins = {'namespace': 'logical-namespace', 'deployment/trino-local': 'logical-deployment'}
        a.engine = Mock(return_value=dict(node_name='node', node_uid='node-uid', engine_id='engine'))
        self.cid, self.sandbox_id = 'e'*64, 'f'*64
        self.image = 'docker.io/trinodb/trino@sha256:' + 'a'*64
        labels = dict(zip(recovery.MANAGED_LABELS, ('foreign-pod', 'other-ns', 'standalone-pod', 'trino')))
        self.process = 'PID COMMAND ARGS\n2 java java -Dconfig=/etc/trino/config.properties io.trino.server.TrinoServer'
        spec = dict(nodeName='node', hostNetwork=False, containers=[dict(name='trino', image=self.image,
            command=['/usr/lib/trino/bin/run-trino'], args=None,
            volumeMounts=[dict(name='config', mountPath='/etc/trino', readOnly=True)])],
            volumes=[dict(name='config', configMap=dict(name='foreign-config'))])
        self.pod = dict(metadata=dict(uid='foreign-pod', name='standalone-pod', namespace='other-ns', labels={'app': 'standalone'},
            ownerReferences=[dict(kind='ReplicaSet', name='standalone-rs', uid='foreign-rs', controller=True)]),
            spec=copy.deepcopy(spec), status=dict(podIP='10.1.0.9', containerStatuses=[dict(name='trino',
            containerID='docker://'+self.cid, imageID=self.image)]))
        self.rs = dict(metadata=dict(uid='foreign-rs', name='standalone-rs', namespace='other-ns',
            ownerReferences=[dict(kind='Deployment', name='standalone', uid='foreign-deployment', controller=True)]),
            spec=dict(selector=dict(matchLabels={'app': 'standalone'}), template=dict(spec=copy.deepcopy(spec))))
        self.deployment = dict(metadata=dict(uid='foreign-deployment', name='standalone', namespace='other-ns'),
            spec=dict(replicas=1, selector=dict(matchLabels={'app': 'standalone'}), template=dict(spec=copy.deepcopy(spec))))
        self.content = 'coordinator=true\nnode-scheduler.include-coordinator=true\ndiscovery.uri=http://localhost:8080\n'
        self.cm = dict(metadata=dict(uid='foreign-cm', name='foreign-config', namespace='other-ns'),
                       data={'config.properties': self.content})
        self.objects = {('pod', 'standalone-pod'): self.pod, ('replicaset', 'standalone-rs'): self.rs,
            ('deployment', 'standalone'): self.deployment, ('configmap', 'foreign-config'): self.cm,
            ('namespace', 'other-ns'): dict(metadata=dict(name='other-ns', uid='foreign-namespace'))}
        self.row = dict(Id=self.cid, Image='resolved-image', Path='/usr/lib/trino/bin/run-trino', Args=[],
            Config=dict(Labels=labels, Image='sha256:'+'a'*64, Entrypoint=['/usr/lib/trino/bin/run-trino'], Cmd=None, Env=[]),
            HostConfig=dict(RestartPolicy=dict(Name='no'), NetworkMode='container:'+self.sandbox_id),
            State=dict(Running=True, StartedAt='2026-10-29T00:00:00Z'),
            Mounts=[dict(Type='bind', Destination='/etc/trino', RW=False,
                Source='/var/lib/kubelet/pods/foreign-pod/volumes/kubernetes.io~configmap/config')])
        self.sandbox = dict(Id=self.sandbox_id, Config=dict(Labels=dict(labels, **{'io.kubernetes.container.name': 'POD'})),
            State=dict(Running=True), HostConfig=dict(NetworkMode='none'))
        self.member = dict(container_id='a'*64, image='resolved-image', started_at='start', labels=dict.fromkeys(recovery.MANAGED_LABELS))
        self.target = dict(Id='a'*64, Image='resolved-image', Config=dict(Labels=self.member['labels']),
            State=dict(Running=True, StartedAt='start'), HostConfig=dict(RestartPolicy=dict(Name='no')))
        a._foreign_get = Mock(side_effect=lambda ns, kind, name: copy.deepcopy(self.objects[kind, name]))
        a._kube = Mock(return_value=dict(items=[dict(metadata=dict(uid='logical-pod'), status=dict(podIP='10.1.0.1'))]))
        a._archive = Mock(side_effect=lambda cid: config_archive(self.content.encode()))
        a._docker = Mock(side_effect=self.docker)
        a._run = Mock(side_effect=self.command)

    def docker(self, *args):
        if args[0] == 'image':
            return [dict(Id='resolved-image', RepoDigests=[self.image])]
        return [copy.deepcopy({self.cid: self.row, self.sandbox_id: self.sandbox, 'a'*64: self.target}[cid]) for cid in args[1:]]

    def command(self, program, *args):
        if program != 'docker':
            raise AssertionError('unexpected transport')
        if args[0] == 'ps':
            return 'a'*64+'\n'+self.cid
        if args[0] == 'top':
            return self.process if args[1] == self.cid else 'PID COMMAND ARGS\n1 java io.trino.server.TrinoServer'
        raise AssertionError('unexpected mutation or SQL: '+str(args))

    def inventory(self):
        self.adapter._inventory([self.member])


class ForeignStandaloneTests(unittest.TestCase):
    def fresh_mounts(self, change):
        f = ForeignFixture()
        f.row['Mounts'].append(dict(Type='bind', Destination='/tmp', Source='/var/lib/kubelet/foreign-tmp',
                                    RW=True, Mode='rw', Propagation='rprivate'))
        def inspect(*args):
            rows = f.docker(*args)
            if args == ('inspect', f.cid):
                change(rows[0]['Mounts'])
            return rows
        f.adapter._docker.side_effect = inspect
        return f

    def test_foreign_fresh_inspect_accepts_reordered_full_mounts(self):
        f = self.fresh_mounts(lambda mounts: mounts.reverse())
        f.inventory()
        self.assertTrue(f.row['State']['Running'])

    def test_foreign_fresh_inspect_rejects_mount_changes_duplicates_and_untyped_destinations(self):
        changes = [
            lambda mounts: mounts[0].__setitem__('Source', '/changed-source'),
            lambda mounts: mounts[0].__setitem__('RW', True),
            lambda mounts: mounts[1].__setitem__('Mode', 'changed'),
            lambda mounts: mounts[1].__setitem__('Type', 'volume'),
            lambda mounts: mounts.append(copy.deepcopy(mounts[1])),
            lambda mounts: mounts[1].__setitem__('Destination', None),
            lambda mounts: mounts[1].__setitem__('Destination', 1),
            lambda mounts: mounts[1].pop('Destination'),
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(QueryError):
                self.fresh_mounts(change).inventory()

    def test_same_image_standalone_is_preserved_and_revalidated_each_inventory(self):
        f = ForeignFixture()
        f.inventory()
        f.inventory()
        self.assertEqual(f.adapter._archive.call_count, 2)
        self.assertTrue(f.row['State']['Running'])
        f.content = f.cm['data']['config.properties'] = f.content.replace('coordinator=true', 'coordinator=false', 1)
        with self.assertRaises(QueryError):
            f.inventory()
        self.assertFalse(any(call.args[1] in ('stop', 'rm', 'exec') for call in f.adapter._run.call_args_list))

    def test_foreign_predicate_rejects_identity_network_mount_role_and_launcher_drift(self):
        changes = [
            lambda f: f.row['Config']['Labels'].__setitem__('io.kubernetes.pod.namespace', 'logical-ns'),
            lambda f: f.pod['metadata'].update(labels={'app': 'standalone', logical.logical_foundation.LABEL: 'foundation'}),
            lambda f: f.pod['metadata'].__setitem__('uid', 'wrong-uid'),
            lambda f: f.rs['metadata'].__setitem__('uid', 'wrong-rs'),
            lambda f: f.pod['status']['containerStatuses'][0].__setitem__('containerID', 'docker://'+'b'*64),
            lambda f: f.pod['spec'].__setitem__('nodeName', 'other-node'),
            lambda f: f.pod['status'].__setitem__('podIP', '10.1.0.1'),
            lambda f: f.pod['spec'].__setitem__('hostNetwork', True),
            lambda f: f.row['HostConfig'].__setitem__('NetworkMode', 'host'),
            lambda f: f.sandbox['HostConfig'].__setitem__('NetworkMode', 'host'),
            lambda f: f.row['Mounts'][0].__setitem__('RW', True),
            lambda f: f.row['Mounts'][0].__setitem__('Source', '/var/lib/kubelet/pods/other/volumes/kubernetes.io~configmap/config'),
            lambda f: f.pod['spec']['containers'][0]['volumeMounts'][0].__setitem__('subPath', 'config.properties'),
            lambda f: f.row['Mounts'].append(dict(Destination='/etc/trino/config.properties', RW=False)),
            lambda f: f.pod['spec']['containers'][0].__setitem__('args', ['--config', '/other']),
            lambda f: setattr(f, 'process', f.process.replace('-Dconfig=', '-Dcoordinator=false -Dconfig=')),
            lambda f: setattr(f, 'process', f.process.replace('/etc/trino/config.properties', '/other/config.properties')),
            lambda f: setattr(f, 'process', f.process.replace('-Dconfig=', '@/tmp/override -Dconfig=')),
            lambda f: setattr(f, 'process', f.process + '\n3 java io.trino.server.TrinoServer'),
            lambda f: f.row['Config']['Env'].append('JAVA_TOOL_OPTIONS=-Dcoordinator=false'),
            lambda f: f.cm['metadata'].update(labels={logical.logical_foundation.LABEL: 'foundation'}),
            lambda f: f.sandbox['Config']['Labels'].__setitem__('io.kubernetes.pod.uid', 'other-pod'),
            lambda f: f.row.__setitem__('Image', 'wrong-resolved-image'),
            lambda f: f.cm['data'].__setitem__('config.properties', 'different'),
        ]
        for change in changes:
            with self.subTest(change=change):
                f = ForeignFixture()
                change(f)
                with self.assertRaises(QueryError):
                    f.inventory()
        for content in ('coordinator=false\nnode-scheduler.include-coordinator=true\ndiscovery.uri=http://localhost:8080\n',
                        'coordinator=true\nnode-scheduler.include-coordinator=false\ndiscovery.uri=http://localhost:8080\n',
                        'coordinator=true\nnode-scheduler.include-coordinator=true\ndiscovery.uri=http://other:8080\n',
                        'coordinator=true\ncoordinator=true\nnode-scheduler.include-coordinator=true\ndiscovery.uri=http://localhost:8080\n'):
            f = ForeignFixture()
            f.content = f.cm['data']['config.properties'] = content
            with self.assertRaises(QueryError):
                f.inventory()

    def test_archive_requires_one_bounded_fixed_regular_file(self):
        for raw in (b'bad', b'x'*262145, config_archive(b'x', extra=True), config_archive(b'x', name='../config.properties'),
                    config_archive(b'x', kind=tarfile.SYMTYPE), config_archive(b'x'*65537)):
            with self.subTest(size=len(raw)), self.assertRaises(QueryError):
                logical.LogicalDockerAdapter._archive_config(raw)
        # Standard extended timestamp metadata does not introduce another
        # file. Effective name/type/size and exact content are still checked.
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w', format=tarfile.PAX_FORMAT) as archive:
            info = tarfile.TarInfo('config.properties')
            info.size, info.pax_headers = 1, {'mtime': '123.456'}
            archive.addfile(info, io.BytesIO(b'x'))
        self.assertEqual(logical.LogicalDockerAdapter._archive_config(output.getvalue()), 'x')

    def test_fixed_archive_get_binary_transport_uses_trusted_context_and_no_exec(self):
        from types import SimpleNamespace
        f = ForeignFixture()
        adapter = logical.LogicalDockerAdapter.__new__(logical.LogicalDockerAdapter)
        adapter.config = dict(docker_context='colima-mac-studio-solo')
        read_fd, write_fd = os.pipe()
        raw = config_archive(f.content.encode())
        def write():
            with os.fdopen(write_fd, 'wb') as stream:
                stream.write(raw)
        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        stdout = os.fdopen(read_fd, 'rb')
        process = SimpleNamespace(stdout=stdout, wait=Mock(return_value=0), poll=Mock(return_value=0))
        with patch.object(logical.subprocess, 'Popen', return_value=process) as launch, \
             patch.dict(os.environ, {'DOCKER_HOST': 'untrusted', 'DOCKER_CONTEXT': 'untrusted'}):
            self.assertEqual(adapter._archive(f.cid), raw)
        writer.join(timeout=1)
        self.assertFalse(writer.is_alive())
        self.assertEqual(launch.call_args.args[0], ['rtk', 'proxy', 'docker', '--context', 'colima-mac-studio-solo',
            'cp', '-L', f.cid+':/etc/trino/config.properties', '-'])
        self.assertNotIn('DOCKER_HOST', launch.call_args.kwargs['env'])
        self.assertNotIn('DOCKER_CONTEXT', launch.call_args.kwargs['env'])


class ScopeTests(unittest.TestCase):
    """Exercise the real scope and Provider compute/ownership checks together."""
    def setUp(self):
        from test_logical_foundation import settings
        from test_logical_provider import Harness
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        c = settings(Path(self.tmp.name))
        c['trino']['workers'] = 1
        self.harness = Harness(c)
        self.provider = logical.Provider(c, command=self.harness.command)
        self.resources, self.pods, self.rows = {}, [], {}
        self.engine = dict(engine_id='engine', socket='socket', node_uid='node-uid', node_name='lab-node')
        for index, name in enumerate(('trino-local', 'trino-local-worker')):
            pod = self.harness.resource('pod', name+'-pod')
            pod['metadata'].update(creationTimestamp='2026-10-29T01:00:00Z',
                labels={**pod['metadata']['labels'], 'app.kubernetes.io/name': name})
            cid = ('a' if index == 0 else 'b')*64
            pod['status']['containerStatuses'][0]['containerID'] = 'docker://'+cid
            pod['status']['containerStatuses'][0]['restartCount'] = 0
            pod['status']['podIP'] = '10.0.0.'+str(index+1)
            self.pods.append(pod)
            deployment = self.harness.resource('deployment', name)
            deployment['spec'].update(template=dict(spec=copy.deepcopy(pod['spec']),
                metadata=dict(labels=copy.deepcopy(pod['metadata']['labels']))),
                selector=dict(matchLabels={'app.kubernetes.io/name': name}))
            rs = self.harness.resource('replicaset', name+'-rs')
            rs['spec'] = dict(template=dict(spec=copy.deepcopy(pod['spec'])),
                              selector=dict(matchLabels={'app.kubernetes.io/name': name}))
            self.resources['deployment', name] = deployment
            self.resources['replicaset', name+'-rs'] = rs
            labels = dict(zip(recovery.MANAGED_LABELS, (pod['metadata']['uid'], c['kube']['namespace'],
                pod['metadata']['name'], 'trino')))
            self.rows[cid] = dict(Id=cid, Image='sha256:actual-image', Config=dict(Labels=labels, Image=c['images']['trino']),
                HostConfig=dict(RestartPolicy=dict(Name='no')), State=dict(Running=True, StartedAt='2026-10-29T01:01:00Z'),
                Mounts=[dict(Destination=m['mountPath'], Source='/managed/'+m['name'], RW=not m.get('readOnly', False))
                        for m in pod['spec']['containers'][0]['volumeMounts']])
        service = self.harness.resource('service', 'trino-local')
        self.resources['service', 'trino-local'] = service
        self.endpoints = dict(subsets=[dict(addresses=[dict(targetRef=dict(uid=self.pods[0]['metadata']['uid'],
            namespace=c['kube']['namespace']))])])
        self.provider._cmd = Mock(side_effect=self.metadata)
        self.provider._provisioner = Mock(return_value={'username': 'provisioner'})
        self.provider._sql = Mock(return_value=[['coordinator', 'http://10.0.0.1:8081', True, 'active'],
                                                ['worker', 'http://10.0.0.2:8081', False, 'active']])
        self.provider._stopped_credentials = Mock()
        self.adapter = logical.LogicalDockerAdapter.__new__(logical.LogicalDockerAdapter)
        self.adapter.foundation, self.adapter.members = self.provider.config, 2
        self.adapter.namespace = c['kube']['namespace']
        self.adapter.pins, self.adapter.material_digest = {}, 'material'
        self.adapter.private = []
        self.adapter.rendered = [dict(kind='Deployment', metadata=dict(name=name), spec=copy.deepcopy(d['spec']))
            for (kind, name), d in self.resources.items() if kind == 'deployment']
        self.adapter.engine = Mock(return_value=self.engine)
        @contextmanager
        def bound(**kwargs):
            self.assertTrue(kwargs['full'])
            yield self.provider
        self.adapter._provider = bound
        self.adapter._docker = Mock(side_effect=self.docker)
        self.adapter._run = Mock(side_effect=self.command)

    def metadata(self, args):
        _KubeMetadata._target(args, self.provider.config['kube'], self.provider.config['prefix'])
        kind = args[1]
        if kind == 'pods':
            result = dict(items=self.pods)
        else:
            result = self.resources[kind, args[2]]
        return json.dumps(result).encode()

    def docker(self, *args):
        if args[0] == 'image':
            return [dict(Id='sha256:actual-image', RepoDigests=[self.adapter.foundation['images']['trino']])]
        return [self.rows[cid] for cid in args[1:]]

    def command(self, program, *args):
        if program == 'kubectl':
            self.assertEqual(args, ('-n', self.adapter.namespace, 'get', 'endpoints', 'trino-local', '-o', 'json'))
            return json.dumps(self.endpoints)
        if args[0] == 'ps':
            return '\n'.join(self.rows)
        return 'PID COMMAND ARGS\n1 java io.trino.server.TrinoServer'

    def test_cold_query_before_new_pod_start_is_valid_current_scope(self):
        scope = self.adapter.scope(dict(queries=[dict(created_at='2026-10-29T00:00:00Z')]))
        self.assertEqual(len(scope['members']), 2)
        self.assertIn('no claim', scope['binding_basis'])
        self.assertEqual({m['backend_node_id'] for m in scope['members']}, {'coordinator', 'worker'})
        self.assertTrue(all(m['pod_created_at'] == '2026-10-29T01:00:00Z' and m['restart_count'] == 0
                            for m in scope['members']))

    def test_digest_config_image_input_accepts_only_pinned_resolved_image(self):
        for row in self.rows.values():
            row['Config']['Image'] = self.adapter.foundation['images']['trino'].split('@')[1]
        self.assertEqual(len(self.adapter.scope({})['members']), 2)
        self.rows['a'*64]['Image'] = 'sha256:wrong-resolved-id'
        with self.assertRaises(QueryError):
            self.adapter.scope({})

    def test_pod_birth_and_restart_count_require_typed_complete_history(self):
        status = self.pods[0]['status']['containerStatuses'][0]
        for count in (None, True, -1, '0'):
            with self.subTest(count=count):
                status['restartCount'] = count
                with self.assertRaises(QueryError):
                    self.adapter.scope({})
        status['restartCount'] = 0
        for birth in (None, 123, 'unknown', '2026-10-29T01:00:00'):
            with self.subTest(birth=birth):
                self.pods[0]['metadata']['creationTimestamp'] = birth
                with self.assertRaises(QueryError):
                    self.adapter.scope({})

    def test_external_backend_ownership_node_endpoint_mount_image_and_spec_drift_refused(self):
        cases = [
            lambda: self.provider._sql.return_value[1].__setitem__(1, 'https://external:8080'),
            lambda: self.resources['replicaset', 'trino-local-worker-rs']['metadata'].__setitem__('uid', 'wrong-owner'),
            lambda: self.pods[1]['spec'].__setitem__('nodeName', 'external-node'),
            lambda: self.endpoints['subsets'][0]['addresses'][0]['targetRef'].__setitem__('uid', 'foreign'),
            lambda: self.rows['a'*64]['Mounts'][0].__setitem__('RW', False),
            lambda: self.rows['a'*64].__setitem__('Image', 'untrusted-image'),
            lambda: self.pods[1]['spec']['containers'][0].__setitem__('command', ['foreign-process']),
        ]
        for change in cases:
            with self.subTest(change=change):
                self.setUp()
                change()
                with self.assertRaises((QueryError, logical.logical_foundation.ConfigError, RuntimeError)):
                    self.adapter.scope({})


if __name__ == '__main__':
    unittest.main()
