"""Isolated Kubernetes metadata responses; no VM, owner, SQL or saved files."""
import copy
import io
import os
from unittest import TestCase, mock
import uuid

from tools.local_experiment.warehouse_lifecycle import runtime_status as R


TOKEN = 'a' * 32
PREFIX = R.M.name(TOKEN)


def obj(kind, name, *, owner=None, service=None, uid=None):
    labels = {}
    if owner is not None:
        labels[R.OWNER] = owner
    if service is not None:
        labels['kubernetes.io/service-name'] = service
    return {'kind': kind, 'metadata': {'name': name, 'namespace': R.W.NAMESPACE,
            'uid': uid or str(uuid.uuid4()), 'labels': labels}}


class FakeKube:
    def __init__(self):
        self.names = {}
        self.owned = []
        self.slices = {}
        self.calls = []

    def get(self, resource, name):
        self.calls.append(('get', resource, name))
        return copy.deepcopy(self.names.get((resource, name)))

    def labelled(self, token):
        self.calls.append(('labelled', token))
        return {'kind': 'List', 'items': copy.deepcopy(self.owned)}

    def endpoints(self, service):
        self.calls.append(('endpoints', service))
        return {'kind': 'EndpointSliceList', 'items': copy.deepcopy(self.slices.get(service, []))}


class RuntimeStatusTests(TestCase):
    def test_empty_scope_requires_two_complete_rounds_and_no_mutation(self):
        kube = FakeKube()
        report = R.observe(TOKEN, kube)
        self.assertEqual(report['current_state'], 'scoped-absence-observed')
        self.assertFalse(report['historical_cleanup_proven'])
        self.assertFalse(report['all_namespace_resources_checked'])
        self.assertEqual(report['backend_query_state'], 'unknown')
        self.assertEqual((report['named_present'], report['labelled_present'],
                          report['service_endpoints_present']), (0, 0, 0))
        self.assertEqual(len(kube.calls), 2 * (11 + 1 + 2))
        self.assertEqual(kube.calls[:14], kube.calls[14:])

    def test_same_name_even_without_owner_label_is_present(self):
        kube = FakeKube()
        kube.names['deployments', PREFIX + '-xs'] = obj('Deployment', PREFIX + '-xs')
        report = R.observe(TOKEN, kube)
        self.assertEqual(report['current_state'], 'scoped-resources-present')
        self.assertEqual(report['named_present'], 1)
        self.assertEqual(report['labelled_present'], 0)

    def test_labelled_child_and_controller_endpoint_count_as_present(self):
        kube = FakeKube()
        kube.owned.append(obj('ReplicaSet', PREFIX + '-xs-abcde', owner=TOKEN))
        service = PREFIX + '-xs'
        kube.slices[service] = [obj('EndpointSlice', service + '-abcde', service=service)]
        report = R.observe(TOKEN, kube)
        self.assertEqual(report['current_state'], 'scoped-resources-present')
        self.assertEqual((report['labelled_present'], report['service_endpoints_present']), (1, 1))

    def test_mutation_between_rounds_and_equally_sized_uid_replacement_fail(self):
        class Changing(FakeKube):
            def endpoints(self, service):
                if sum(call[0] == 'endpoints' for call in self.calls) == 1:
                    self.names['deployments', PREFIX + '-s'] = obj('Deployment', PREFIX + '-s')
                return super().endpoints(service)

        with self.assertRaises(R.RuntimeStatusError):
            R.observe(TOKEN, Changing())

        class Replacing(FakeKube):
            def get(self, resource, name):
                if (resource, name) == ('deployments', PREFIX + '-xs') and self.calls:
                    self.names[resource, name] = obj('Deployment', name)
                return super().get(resource, name)

        kube = Replacing()
        kube.names['deployments', PREFIX + '-xs'] = obj('Deployment', PREFIX + '-xs')
        with self.assertRaises(R.RuntimeStatusError):
            R.observe(TOKEN, kube)

    def test_invalid_or_incomplete_response_fails_closed(self):
        base = FakeKube()
        wrong_namespace = obj('Deployment', PREFIX + '-xs')
        wrong_namespace['metadata']['namespace'] = 'different-namespace'
        cases = [
            (('deployments', PREFIX + '-xs'), obj('Pod', PREFIX + '-xs')),
            (('deployments', PREFIX + '-xs'), obj('Deployment', PREFIX + '-xs', uid='not-a-uuid')),
            (('deployments', PREFIX + '-xs'), wrong_namespace),
        ]
        for key, value in cases:
            with self.subTest(key=key, kind=value['kind']):
                kube = copy.deepcopy(base)
                kube.names[key] = value
                with self.assertRaises((R.RuntimeStatusError, ValueError)):
                    R.observe(TOKEN, kube)

        kube = FakeKube()
        kube.owned.append(obj('ReplicaSet', PREFIX + '-xs-abcde', owner='b'*32))
        with self.assertRaises(R.RuntimeStatusError):
            R.observe(TOKEN, kube)
        kube = FakeKube()
        kube.owned.append(obj('ReplicaSet', PREFIX + '-xs-abcde', owner=TOKEN))
        kube.owned.append(copy.deepcopy(kube.owned[0]))
        with self.assertRaises(R.RuntimeStatusError):
            R.observe(TOKEN, kube)
        kube = FakeKube()
        kube.slices[PREFIX + '-s'] = [obj('EndpointSlice', PREFIX + '-s-abcde', service=PREFIX + '-xs')]
        with self.assertRaises(R.RuntimeStatusError):
            R.observe(TOKEN, kube)

        class BadList(FakeKube):
            def labelled(self, token):
                return None

        with self.assertRaises(R.RuntimeStatusError):
            R.observe(TOKEN, BadList())
        with self.assertRaises(R.RuntimeStatusError):
            R.observe(TOKEN, base, deadline=0)

    def test_kubectl_stdout_contains_only_metadata_and_no_secret_data(self):
        server = PREFIX + '-xs'
        responses = [
            'Secret\t' + PREFIX + '-secret\tshirokuma-dev\t' + str(uuid.uuid4()) + '\t\t\n',
            'List\t0\tCOMPLETE\n',
            'List\t1\tCOMPLETE\nEndpointSlice\t' + server + '-abcde\tshirokuma-dev\t' +
                str(uuid.uuid4()) + '\t\t' + server + '\n',
        ]
        with mock.patch.object(R.W, 'command', side_effect=responses) as command:
            kube = R.ReadOnlyKubernetes()
            self.assertEqual(kube.get('secrets', PREFIX + '-secret')['kind'], 'Secret')
            self.assertEqual(kube.labelled(TOKEN)['items'], [])
            self.assertEqual(len(kube.endpoints(server)['items']), 1)
        for call in command.call_args_list:
            args, = call.args
            self.assertEqual(args[:7], ['kubectl', '--context', R.W.CONTEXT, '-n', R.W.NAMESPACE,
                                        '--request-timeout=10s', 'get'])
            self.assertIn(args[7], (R.LABELLED, 'secrets', 'endpointslices'))
            self.assertIn('--chunk-size=0', args)
            self.assertTrue(args[-1].startswith('-o=go-template='))
            self.assertNotIn('.data', args[-1])
            self.assertNotIn('.spec', args[-1])
            self.assertIsNone(call.kwargs.get('body'))
            self.assertIsNone(call.kwargs.get('log_path'))

    def test_blank_truncated_or_malformed_lists_are_not_empty(self):
        for raw in ('', 'List\t0\tTRUNCATED\n', 'List\t1\tCOMPLETE\n',
                    'PodList\t0\tCOMPLETE\n', 'List\t0\tCOMPLETE\nextra\n'):
            with self.subTest(raw=raw), mock.patch.object(R.W, 'command', return_value=raw):
                with self.assertRaises(R.RuntimeStatusError):
                    R.ReadOnlyKubernetes().labelled(TOKEN)
        with mock.patch.object(R.W, 'command', return_value='PodList\t0\tCOMPLETE\n'):
            with self.assertRaises(R.RuntimeStatusError):
                R.ReadOnlyKubernetes().endpoints(PREFIX + '-xs')

    def test_cli_only_contacts_kubernetes_after_saved_completion_gate(self):
        session = {'journal_sha256': 'b'*64}
        claim = {'journal_sha256': 'b'*64, 'recorded_completion': 'correlated-saved-claim'}
        with mock.patch.object(R, 'check_context'), mock.patch.object(R.S, 'read', return_value=session), \
             mock.patch.object(R.C, 'read', return_value=claim), \
             mock.patch.object(R, 'ReadOnlyKubernetes') as kubernetes, \
             mock.patch('sys.stdout', new_callable=io.StringIO) as output, \
             mock.patch('sys.stderr', new_callable=io.StringIO):
            kubernetes.return_value = FakeKube()
            self.assertEqual(R.main(['--session', TOKEN]), 0)
            self.assertIn('scoped-absence-observed', output.getvalue())
            self.assertEqual(kubernetes.call_count, 1)

        with mock.patch.object(R, 'check_context'), mock.patch.object(R.S, 'read', return_value=session), \
             mock.patch.object(R.C, 'read', return_value={**claim, 'journal_sha256': 'c'*64}), \
             mock.patch.object(R, 'ReadOnlyKubernetes') as kubernetes, \
             mock.patch('sys.stdout', new_callable=io.StringIO) as output, \
             mock.patch('sys.stderr', new_callable=io.StringIO) as errors:
            self.assertEqual(R.main(['--session', TOKEN]), 1)
            kubernetes.assert_not_called()
            self.assertEqual(output.getvalue(), '')
            self.assertEqual(errors.getvalue().strip(), 'RUNTIME_OBSERVATION_UNVERIFIED')

        with mock.patch.object(R, 'check_context'), \
             mock.patch.object(R.S, 'read', side_effect=[session, {**session, 'journal_sha256': 'd'*64}]), \
             mock.patch.object(R.C, 'read', return_value=claim), \
             mock.patch.object(R, 'ReadOnlyKubernetes', return_value=FakeKube()), \
             mock.patch('sys.stdout', new_callable=io.StringIO) as output, \
             mock.patch('sys.stderr', new_callable=io.StringIO) as errors:
            self.assertEqual(R.main(['--session', TOKEN]), 1)
            self.assertEqual(output.getvalue(), '')
            self.assertEqual(errors.getvalue().strip(), 'RUNTIME_OBSERVATION_UNVERIFIED')

    def test_bad_context_or_missing_saved_claim_never_queries_runtime(self):
        with mock.patch.object(R, 'check_context', side_effect=R.ContextError('unavailable')), \
             mock.patch.object(R.S, 'read') as saved, mock.patch.object(R, 'ReadOnlyKubernetes') as kube, \
             mock.patch('sys.stdout', new_callable=io.StringIO) as output, \
             mock.patch('sys.stderr', new_callable=io.StringIO) as errors:
            self.assertEqual(R.main(['--session', TOKEN]), 1)
            saved.assert_not_called()
            kube.assert_not_called()
            self.assertEqual(output.getvalue(), '')
            self.assertEqual(errors.getvalue().strip(), 'RUNTIME_OBSERVATION_UNVERIFIED')
        with mock.patch.dict(os.environ, {'KUBECONFIG': 'untrusted'}, clear=False), \
             mock.patch.object(R, 'check_context') as check:
            with mock.patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(R.main(['--session', TOKEN]), 1)
            check.assert_not_called()
        with mock.patch.dict(os.environ, {'KUBECONFIG': 'untrusted'}, clear=False), \
             mock.patch.object(R, 'check_context') as check, \
             mock.patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(R.main(['--session', 'invalid;do-not-run']), 1)
            check.assert_not_called()
        with mock.patch.dict(os.environ, {'WAREHOUSE_LIFECYCLE_RUN_TOKEN': 'invalid;do-not-run'}, clear=False), \
             mock.patch.object(R, 'check_context') as check, \
             mock.patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(R.main(['--session-from-env']), 1)
            check.assert_not_called()
