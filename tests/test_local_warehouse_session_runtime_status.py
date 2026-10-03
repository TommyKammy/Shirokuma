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

    def test_completed_rounds_cannot_publish_after_deadline(self):
        empty = {'named_present': 0, 'labelled_present': 0, 'service_endpoints_present': 0,
                 'snapshot_sha256': '0' * 64}
        with mock.patch.object(R, 'snapshot', return_value=empty) as snapshots, \
             mock.patch.object(R.time, 'monotonic', return_value=101):
            with self.assertRaises(R.RuntimeStatusError):
                R.observe(TOKEN, FakeKube(), deadline=100)
            self.assertEqual(snapshots.call_count, 2)

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

    def test_noncanonical_projected_dns_labels_cannot_count_as_resources(self):
        for name in ('-child', 'child-', '-', 'a' * 64):
            with self.subTest(name=name):
                kube = FakeKube()
                kube.owned = [obj('ReplicaSet', name, owner=TOKEN)]
                with self.assertRaises(R.RuntimeStatusError):
                    R.observe(TOKEN, kube)
                with self.assertRaises(R.RuntimeStatusError):
                    R.row('EndpointSlice\tchild\t' + R.W.NAMESPACE + '\t' + str(uuid.uuid4()) +
                          '\t\t' + name)
        self.assertEqual(R.observe(TOKEN, FakeKube())['current_state'], 'scoped-absence-observed')

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

    def test_fixed_name_label_change_between_rounds_cannot_look_stable(self):
        class Relabel(FakeKube):
            def get(self, resource, name):
                value = super().get(resource, name)
                if (resource, name) == ('deployments', PREFIX + '-xs') and len(self.calls) > 14:
                    value['metadata']['labels'][R.OWNER] = 'b' * 32
                return value

        kube = Relabel()
        kube.names['deployments', PREFIX + '-xs'] = obj('Deployment', PREFIX + '-xs')
        steady = FakeKube()
        steady.names = copy.deepcopy(kube.names)
        steady.names['deployments', PREFIX + '-xs']['metadata']['labels'][R.OWNER] = 'b' * 32
        self.assertEqual(R.observe(TOKEN, steady)['current_state'], 'scoped-resources-present')
        with self.assertRaises(R.RuntimeStatusError):
            R.observe(TOKEN, kube)

    def test_labelled_child_metadata_change_between_rounds_fails(self):
        class Relabel(FakeKube):
            def labelled(self, token):
                result = super().labelled(token)
                if self.calls.count(('labelled', token)) == 2:
                    result['items'][0]['metadata']['labels']['kubernetes.io/service-name'] = PREFIX + '-s'
                return result

        kube = Relabel()
        kube.owned = [obj('ReplicaSet', PREFIX + '-xs-child', owner=TOKEN, service=PREFIX + '-xs')]
        with self.assertRaises(R.RuntimeStatusError):
            R.observe(TOKEN, kube)

    def test_same_uid_with_conflicting_labels_across_views_fails(self):
        kube = FakeKube()
        name = PREFIX + '-xs'
        fixed = obj('Deployment', name, owner=TOKEN)
        kube.names['deployments', name] = fixed
        kube.owned = [copy.deepcopy(fixed)]
        self.assertEqual(R.observe(TOKEN, kube)['current_state'], 'scoped-resources-present')
        kube.names['deployments', name]['metadata']['labels'][R.OWNER] = 'b' * 32
        with self.assertRaises(R.RuntimeStatusError):
            R.observe(TOKEN, kube)

    def test_conflicting_identity_between_projections_fails_in_one_round(self):
        kube = FakeKube()
        name = PREFIX + '-xs'
        original = obj('Deployment', name, owner=TOKEN)
        kube.names['deployments', name] = original
        kube.owned = [copy.deepcopy(original)]
        self.assertEqual(R.observe(TOKEN, kube)['current_state'], 'scoped-resources-present')

        kube.owned[0]['metadata']['uid'] = str(uuid.uuid4())
        with self.assertRaises(R.RuntimeStatusError):
            R.observe(TOKEN, kube)

        kube.owned = [copy.deepcopy(original), copy.deepcopy(original)]
        kube.owned[1]['metadata']['uid'] = str(uuid.uuid4())
        with self.assertRaises(R.RuntimeStatusError):
            R.observe(TOKEN, kube)

    def test_one_uid_cannot_identify_two_resources_in_one_round(self):
        kube = FakeKube()
        first = obj('Deployment', PREFIX + '-xs', owner=TOKEN)
        kube.names['deployments', PREFIX + '-xs'] = first
        kube.owned = [copy.deepcopy(first)]
        self.assertEqual(R.observe(TOKEN, kube)['named_present'], 1)

        for kind, name in [('Deployment', PREFIX + '-s'),
                           ('ReplicaSet', PREFIX + '-xs-child')]:
            with self.subTest(kind=kind):
                kube.owned.append(obj(kind, name, owner=TOKEN, uid=first['metadata']['uid']))
                with self.assertRaises(R.RuntimeStatusError):
                    R.observe(TOKEN, kube)
                kube.owned.pop()

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
            'v1\tList\t0\tCOMPLETE\t42\n',
            'discovery.k8s.io/v1\tEndpointSliceList\t1\tCOMPLETE\t43\nEndpointSlice\t' + server + '-abcde\tshirokuma-dev\t' +
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

    def test_named_get_distinguishes_missing_from_malformed_object(self):
        self.assertIn('{{if .}}{{if .metadata}}', R.ONE)
        self.assertIn('{{else}}INVALID{{end}}{{end}}', R.ONE)
        name = PREFIX + '-secret'
        with mock.patch.object(R.W, 'command', return_value=''):
            self.assertIsNone(R.ReadOnlyKubernetes().get('secrets', name))
        for raw in ('INVALID', 'INVALID\n', 'Secret\t' + name + '\n'):
            with self.subTest(raw=raw), mock.patch.object(R.W, 'command', return_value=raw):
                with self.assertRaises(R.RuntimeStatusError):
                    R.ReadOnlyKubernetes().get('secrets', name)

    def test_blank_truncated_or_malformed_lists_are_not_empty(self):
        # A Kubernetes list may report remaining items even if the continuation
        # token is absent/empty; the metadata-only projection must flag it.
        self.assertIn('or .metadata.continue .metadata.remainingItemCount', R.MANY)
        self.assertIn('eq (printf "%T" .items) "<nil>"', R.MANY)
        # Without list metadata, an empty projection is not evidence of absence.
        self.assertIn('eq (printf "%T" .metadata) "<nil>"', R.MANY)
        # An empty metadata map also cannot establish a versioned list read.
        self.assertIn('(not .metadata.resourceVersion)', R.MANY)
        with mock.patch.object(R.W, 'command', return_value='v1\tList\t0\tINVALID\n'):
            with self.assertRaises(R.RuntimeStatusError):
                R.ReadOnlyKubernetes().endpoints(PREFIX + '-xs')
        # Missing resourceVersion is projected as INVALID even if items=[] and
        # list metadata exists; no empty-scope result may follow that marker.
        with mock.patch.object(R.W, 'command', return_value='v1\tList\t0\tINVALID\n'):
            with self.assertRaises(R.RuntimeStatusError):
                R.ReadOnlyKubernetes().labelled(TOKEN)
        self.assertIn('.metadata.resourceVersion', R.MANY)
        for raw in ('', 'v1\tList\t0\tINVALID\n', 'v1\tList\t0\tTRUNCATED\n', 'v1\tList\t1\tCOMPLETE\t42\n',
                     'v1\tList\t0\tCOMPLETE\t  \n', 'v1\tList\t0\tCOMPLETE\t42 43\n',
                     'v1\tList\t0\tCOMPLETE\t' + 'x' * 1025 + '\n',
                      'v1\tList\t0\tCOMPLETE\t42\x00\n',
                      'v1\tList\t0\tCOMPLETE\t42\u200b\n',
                      'v1\tList\t0\tCOMPLETE\t42\u00a0\n',
                      'v1\tList\t0\tCOMPLETE\t' + '\u00e9' * 513 + '\n',
                     'v1\tList\t0\tCOMPLETE\t42\r\n', 'v1\tList\t0\tCOMPLETE\t42\n\r\n',
                     'v2\tList\t0\tCOMPLETE\n', 'discovery.k8s.io/v1\tList\t0\tCOMPLETE\n',
                    'v1\tPodList\t0\tCOMPLETE\n', 'v1\tList\t0\tCOMPLETE\nextra\n'):
            with self.subTest(raw=raw), mock.patch.object(R.W, 'command', return_value=raw):
                with self.assertRaises(R.RuntimeStatusError):
                    R.ReadOnlyKubernetes().labelled(TOKEN)
        with mock.patch.object(R.W, 'command', return_value='v1\tPodList\t0\tCOMPLETE\n'):
            with self.assertRaises(R.RuntimeStatusError):
                R.ReadOnlyKubernetes().endpoints(PREFIX + '-xs')
        for raw in ('v1\tEndpointSliceList\t0\tCOMPLETE\n',
                    'discovery.k8s.io/v1\tList\t0\tCOMPLETE\n'):
            with self.subTest(raw=raw), mock.patch.object(R.W, 'command', return_value=raw):
                with self.assertRaises(R.RuntimeStatusError):
                    R.ReadOnlyKubernetes().endpoints(PREFIX + '-xs')
        with mock.patch.object(R.W, 'command', return_value='v1\tList\t0\tCOMPLETE\t42\n'):
            self.assertEqual(R.ReadOnlyKubernetes().endpoints(PREFIX + '-xs')['items'], [])
        with mock.patch.object(R.W, 'command', return_value='discovery.k8s.io/v1\tEndpointSliceList\t0\tCOMPLETE\t43\n'):
            self.assertEqual(R.ReadOnlyKubernetes().endpoints(PREFIX + '-xs')['items'], [])

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

    def test_cli_rechecks_deadline_after_saved_claim_reread(self):
        session = {'journal_sha256': 'b'*64}
        claim = {'journal_sha256': 'b'*64, 'recorded_completion': 'correlated-saved-claim'}
        with mock.patch.object(R, 'check_context'), mock.patch.object(R.S, 'read', return_value=session) as saved, \
             mock.patch.object(R.C, 'read', return_value=claim) as completion, \
             mock.patch.object(R, 'observe', return_value={'current_state': 'scoped-absence-observed'}) as observe, \
             mock.patch.object(R.time, 'monotonic', side_effect=[100, 102]), \
             mock.patch.object(R, 'MAX_SECONDS', 1), \
             mock.patch('sys.stdout', new_callable=io.StringIO) as output, \
             mock.patch('sys.stderr', new_callable=io.StringIO) as errors:
            self.assertEqual(R.main(['--session', TOKEN]), 1)
            self.assertEqual(saved.call_count, 2)
            self.assertEqual(completion.call_count, 2)
            self.assertEqual(observe.call_count, 1)
            self.assertEqual(observe.call_args.kwargs['deadline'], 101)
            self.assertEqual(output.getvalue(), '')
            self.assertEqual(errors.getvalue().strip(), 'RUNTIME_OBSERVATION_UNVERIFIED')

        with mock.patch.object(R, 'check_context'), mock.patch.object(R.S, 'read', return_value=session), \
             mock.patch.object(R.C, 'read', return_value=claim), \
             mock.patch.object(R, 'observe', return_value={'current_state': 'scoped-absence-observed'}), \
             mock.patch.object(R.time, 'monotonic', side_effect=[100, 100.5]), \
             mock.patch.object(R, 'MAX_SECONDS', 1), \
             mock.patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(R.main(['--session', TOKEN]), 0)
            self.assertIn('scoped-absence-observed', output.getvalue())

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
