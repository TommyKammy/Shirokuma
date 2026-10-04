"""Scoped native admission bridge tests; no cluster requests or mutations."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('local_policy', ROOT / 'tools/local_experiment/policy/manage.py')
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)
IMAGE = 'shirokuma.local/trino@sha256:' + 'a' * 64


def items():
    return json.loads(policy.MANIFEST.read_text())['items']


def admitted(value, *, warning=False, pending=False):
    result = copy.deepcopy(value)
    result['metadata'].update(uid='uid-' + value['metadata']['name'], resourceVersion='12', generation=1)
    if value['kind'] == policy.POLICY:
        result['status'] = {'observedGeneration': 1, 'typeChecking': {'expressionWarnings':
            [{'fieldRef': 'spec.validations[0]', 'warning': 'synthetic type warning'}] if warning else []}}
        if pending:
            result['status'] = {}
    return result


class FakeKubernetes:
    def __init__(self, *, ready=False, warning=False, pending=False):
        self.objects = {(x['kind'], x['metadata']['name']): admitted(x) for x in items()} if ready else {}
        self.calls = []
        self.warning, self.pending = warning, pending
        self.smoke_failure = None

    def get(self, kind, name):
        return copy.deepcopy(self.objects.get((kind, name)))

    def create(self, value, *, dry_run=False):
        self.calls.append(('dry-run' if dry_run else 'create', value['kind'], value['metadata']['name']))
        result = admitted(value, warning=self.warning, pending=self.pending)
        if not dry_run:
            key = (value['kind'], value['metadata']['name'])
            if key in self.objects:
                raise policy.PolicyError('name already exists')
            self.objects[key] = result
        return result

    def delete_binding(self, current, expected):
        policy.assert_owned(current, expected)
        self.calls.append(('delete', expected['kind'], expected['metadata']['name']))
        self.objects.pop((expected['kind'], expected['metadata']['name']))

    def image(self):
        return IMAGE

    def smoke_request(self, value):
        self.calls.append(('smoke', value['kind'], value['metadata']['namespace']))
        if self.smoke_failure:
            return self.smoke_failure(value)
        deny = None
        if value['metadata']['namespace'] == policy.NAMESPACE:
            spec = value['spec']
            if value['kind'] == 'Pod':
                c = spec['containers'][0]
                if c.get('securityContext', {}).get('privileged'):
                    deny = 'disallow-privileged-containers'
                elif any(spec.get(key) for key in ('hostNetwork', 'hostPID', 'hostIPC')):
                    deny = 'disallow-host-namespaces'
                elif spec.get('volumes'):
                    deny = 'disallow-host-path'
                elif not c.get('resources'):
                    deny = 'require-pod-resources'
                elif any(x.get('hostPort') for x in c.get('ports', [])):
                    deny = 'disallow-host-ports'
                elif '@sha256:' not in c['image']:
                    deny = 'require-image-digest'
            elif value['kind'] == 'Ingress':
                deny = 'deny-ingress'
            elif spec.get('type') in {'LoadBalancer', 'NodePort'} or spec.get('externalIPs'):
                deny = 'deny-public-service'
        if deny:
            name = 'shirokuma-local-' + deny
            message = next(x['spec']['validations'][0]['message'] for x in items() if x['kind'] == policy.POLICY and x['metadata']['name'] == name)
            return SimpleNamespace(returncode=1, stdout='', stderr=f"ValidatingAdmissionPolicy '{name}' with binding '{name}' denied request: {message}")
        return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps(value))


class Clock:
    def __init__(self):
        self.value = 0

    def __call__(self):
        self.value += 50
        return self.value


class ManifestTests(unittest.TestCase):
    def test_manifest_reuses_all_source_cel_without_kyverno_autogen(self):
        sources = policy.source_policies()  # kubectl local decoding only; no API connection.
        actual = policy.load_manifest()
        self.assertEqual(len(actual), 16)
        self.assertEqual({x['kind'] for x in actual}, {policy.POLICY, policy.BINDING})
        for source, target in zip(sources, [x for x in actual if x['kind'] == policy.POLICY]):
            self.assertEqual(target['spec']['validations'], source['spec']['validations'])
            self.assertEqual(target['spec'].get('variables'), source['spec'].get('variables'))
            self.assertNotIn('autogen', target['spec'])
            self.assertEqual(target['spec']['failurePolicy'], 'Fail')
            self.assertEqual(target['spec']['matchConstraints']['namespaceSelector'], policy.SELECTOR)
            for rule in target['spec']['matchConstraints']['resourceRules']:
                self.assertEqual(rule['scope'], 'Namespaced')
                self.assertEqual(rule['operations'], ['CREATE', 'UPDATE'])
        for value in actual:
            self.assertEqual(value['metadata']['labels'], policy.LABELS)
            self.assertIn(value['metadata']['name'], policy.NAMES)
            if value['kind'] == policy.BINDING:
                self.assertEqual(value['spec']['validationActions'], ['Deny'])
                self.assertEqual(value['spec']['matchResources']['namespaceSelector'], policy.SELECTOR)
        ephemeral = [x['metadata']['name'] for x in actual if x['kind'] == policy.POLICY and any(
            'pods/ephemeralcontainers' in rule['resources'] for rule in x['spec']['matchConstraints']['resourceRules'])]
        self.assertCountEqual(ephemeral, ['shirokuma-local-disallow-privileged-containers', 'shirokuma-local-require-image-digest'])

    def test_service_ingress_split_preserves_each_original_predicate_without_dynamic_cast(self):
        source = policy.source_policies()[-1]
        values = {x['metadata']['name']: x for x in items() if x['kind'] == policy.POLICY}
        service, ingress = values[policy.PUBLIC_SERVICE], values[policy.INGRESS]
        self.assertEqual(service['spec']['validations'], source['spec']['validations'])
        self.assertEqual(ingress['spec']['validations'], [{'expression': "object.kind != 'Ingress'",
            'message': source['spec']['validations'][0]['message']}])
        for value, resource in ((service, 'services'), (ingress, 'ingresses')):
            self.assertEqual([rule['resources'] for rule in value['spec']['matchConstraints']['resourceRules']], [[resource]])
            self.assertEqual(value['metadata']['annotations']['shirokuma.dev/source-policy'], 'deny-public-service')
            self.assertEqual(value['spec']['failurePolicy'], 'Fail')
            self.assertEqual(value['spec']['matchConstraints']['namespaceSelector'], policy.SELECTOR)
            self.assertNotIn('dyn(', str(value['spec']['validations']))
        for name in policy.NAMES[:6]:
            self.assertNotIn('shirokuma.dev/native-adaptation', values[name]['metadata']['annotations'])

    def test_source_or_manifest_drift_fails_closed(self):
        source = policy.source_policies()
        mutated = policy.render_native(source)
        mutated['items'][0]['spec']['matchConstraints']['namespaceSelector'] = {}
        with mock.patch.object(policy.MANIFEST.__class__, 'read_text', return_value=json.dumps(mutated)), \
             mock.patch.object(policy, 'source_policies', return_value=source):
            with self.assertRaisesRegex(policy.PolicyError, 'exact repository'):
                policy.load_manifest()


class ActivationTests(unittest.TestCase):
    def test_all_policies_compile_before_any_binding_is_activated(self):
        kube = FakeKubernetes()
        events = []
        result = policy.apply_policies(kube, items(), record=events.append)
        self.assertTrue(result['ready'])
        first_binding = next(i for i, event in enumerate(events) if event.get('kind') == policy.BINDING)
        compiled = next(i for i, event in enumerate(events) if event['action'] == 'type_check_complete')
        self.assertGreater(first_binding, compiled)
        self.assertEqual(sum(x[0] == 'create' for x in kube.calls), 16)
        first_create = next(i for i, x in enumerate(kube.calls) if x[0] == 'create')
        self.assertEqual(first_create, 16)
        self.assertTrue(all(x[0] == 'dry-run' for x in kube.calls[:first_create]))
        before = len(kube.calls)
        self.assertTrue(policy.apply_policies(kube, items())['ready'])
        self.assertEqual(len(kube.calls), before)

    def test_wrong_owner_or_content_preflight_prevents_all_creates(self):
        for field in ('owner', 'spec'):
            kube = FakeKubernetes()
            expected = items()[-1]
            value = admitted(expected)
            if field == 'owner':
                value['metadata']['labels']['shirokuma.dev/policy-owner'] = 'somebody-else'
            else:
                value['spec']['matchResources']['namespaceSelector'] = {}
            kube.objects[(expected['kind'], expected['metadata']['name'])] = value
            with self.assertRaisesRegex(policy.PolicyError, 'refusing overwrite'):
                policy.apply_policies(kube, items())
            self.assertEqual(kube.calls, [])

    def test_binding_server_validation_failure_prevents_even_inert_policy_creation(self):
        kube = FakeKubernetes()
        create = kube.create
        def reject_last_binding(value, *, dry_run=False):
            if dry_run and value['kind'] == policy.BINDING and value['metadata']['name'] == policy.NAMES[-1]:
                raise policy.PolicyError('server rejected binding schema')
            return create(value, dry_run=dry_run)
        kube.create = reject_last_binding
        with self.assertRaises(policy.PolicyError):
            policy.apply_policies(kube, items())
        self.assertEqual(kube.objects, {})
        self.assertTrue(all(action == 'dry-run' for action, _, _ in kube.calls))

    def test_type_warnings_and_unconfirmed_checks_never_activate_bindings(self):
        for kwargs in ({'warning': True}, {'pending': True}):
            kube = FakeKubernetes(**kwargs)
            with self.assertRaises(policy.PolicyError):
                policy.apply_policies(kube, items(), clock=Clock(), pause=lambda _: None)
            self.assertFalse(any(key[0] == policy.BINDING for key in kube.objects))
            self.assertFalse(any(action == 'create' and kind == policy.BINDING for action, kind, _ in kube.calls))

    def test_stale_or_missing_generation_is_not_confirmed_typecheck(self):
        value = admitted(items()[0])
        self.assertEqual(policy.typecheck(value), 'complete')
        value['metadata']['generation'] = 2
        self.assertEqual(policy.typecheck(value), 'pending')
        value['metadata'].pop('generation')
        value['status'].pop('observedGeneration')
        self.assertEqual(policy.typecheck(value), 'pending')

    def test_empty_server_selector_defaults_do_not_require_overwrite(self):
        value, expected = admitted(items()[0]), items()[0]
        value['spec']['matchConstraints']['objectSelector'] = {}
        value['spec']['matchConstraints']['excludeResourceRules'] = []
        policy.assert_owned(value, expected)
        value['spec']['matchConstraints']['objectSelector'] = {'matchLabels': {'unapproved': 'scope'}}
        with self.assertRaises(policy.PolicyError):
            policy.assert_owned(value, expected)


class RollbackTests(unittest.TestCase):
    def test_only_owned_bindings_are_removed_policies_and_workloads_untouched(self):
        kube = FakeKubernetes(ready=True)
        result = policy.rollback_bindings(kube, items())
        self.assertTrue(result['bindings_removed'])
        self.assertEqual(len(kube.objects), 8)
        self.assertEqual({kind for kind, _ in kube.objects}, {policy.POLICY})
        self.assertEqual({(action, kind) for action, kind, _ in kube.calls}, {('delete', policy.BINDING)})

    def test_any_foreign_binding_prevents_partial_rollback(self):
        kube = FakeKubernetes(ready=True)
        key = (policy.BINDING, policy.NAMES[-1])
        kube.objects[key]['metadata']['labels'].clear()
        with self.assertRaises(policy.PolicyError):
            policy.rollback_bindings(kube, items())
        self.assertEqual(kube.calls, [])

    def test_delete_request_has_atomic_uid_and_version_preconditions(self):
        client = object.__new__(policy.Kubernetes)
        client.expected = {(x['kind'], x['metadata']['name']): x for x in items()}
        client.json_request = mock.Mock(return_value={})
        expected = next(x for x in items() if x['kind'] == policy.BINDING)
        current = admitted(expected)
        client.delete_binding(current, expected)
        args, options = client.json_request.call_args.args
        self.assertEqual(args, ['delete', '--raw=/apis/' + policy.API + '/validatingadmissionpolicybindings/' + expected['metadata']['name'], '-f', '-'])
        self.assertEqual(options['preconditions'], {'uid': current['metadata']['uid'], 'resourceVersion': '12'})
        with self.assertRaises(policy.PolicyError):
            client.delete_binding(admitted(items()[0]), items()[0])
        current['metadata'].pop('uid')
        with self.assertRaises(policy.PolicyError):
            client.delete_binding(current, expected)


class SmokeTests(unittest.TestCase):
    def test_success_does_not_accept_changed_policy_relevant_fields(self):
        mutations = (
            lambda pod: pod['spec']['containers'][0].update(image='unexpected:latest'),
            lambda pod: pod['spec'].update(hostNetwork=True),
            lambda pod: pod['spec']['containers'][0].update(resources={}),
            lambda pod: pod['spec'].update(initContainers=[{'name': 'extra'}]),
            lambda pod: pod['spec']['containers'][0]['securityContext'].update(privileged=True),
            lambda pod: pod['spec']['securityContext'].update(runAsUser=0),
            lambda pod: pod['spec']['securityContext'].update(runAsNonRoot=False),
            lambda pod: pod['spec']['securityContext'].update(seccompProfile={'type': 'Unconfined'}),
            lambda pod: pod['spec']['containers'][0]['securityContext'].update(capabilities={'add': ['SYS_ADMIN'], 'drop': ['ALL']}),
            lambda pod: pod['spec']['containers'][0].update(ports=[{'hostPort': 8080}]),
        )
        for mutate in mutations:
            kube = FakeKubernetes(ready=True)
            def changed(value):
                reply = copy.deepcopy(value)
                if reply['kind'] == 'Pod':
                    mutate(reply)
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(mutation=mutate):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 1)

    def test_service_success_does_not_accept_public_service_mutation(self):
        for mutate in (lambda service: service['spec'].update(type='LoadBalancer'),
                        lambda service: service['spec'].update(externalIPs=['192.0.2.1']),
                        lambda service: service['spec']['ports'][0].update(nodePort=30001),
                        lambda service: service['spec']['ports'][0].update(protocol='UDP'),
                        lambda service: service['spec']['ports'][0].update(port=True),
                        lambda service: service['spec']['ports'][0].update(targetPort=True),
                        lambda service: service['spec'].update(selector={'app': 'other'})):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def changed(value):
                if value['kind'] != 'Service':
                    return reference.smoke_request(value)
                reply = copy.deepcopy(value)
                mutate(reply)
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(mutation=mutate):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 2)

    def test_pod_success_does_not_accept_namespace_or_host_alias_mutation(self):
        for mutation in ({'shareProcessNamespace': True},
                         {'hostAliases': [{'ip': '192.0.2.1', 'hostnames': ['internal.local']}]},
                         {'hostNetwork': 1}):
            kube = FakeKubernetes(ready=True)
            def changed(value):
                reply = copy.deepcopy(value)
                reply['spec'].update(mutation)
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 1)

    def test_pod_success_does_not_accept_injected_identity_or_placement(self):
        for mutation in ({'serviceAccountName': 'privileged'},
                         {'nodeName': 'other-node'},
                         {'imagePullSecrets': [{'name': 'other-secret'}]},
                         {'dnsPolicy': 'Default'}):
            kube = FakeKubernetes(ready=True)
            def changed(value):
                reply = copy.deepcopy(value)
                reply['spec'].update(mutation)
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 1)

    def test_deep_success_extension_is_a_failed_case(self):
        kube = FakeKubernetes(ready=True)
        def deep(value):
            raw = json.dumps(value)
            return SimpleNamespace(returncode=0, stderr='',
                                   stdout=raw[:-1] + ',"extension":' + '[' * 1100 + '0' + ']' * 1100 + '}')
        kube.smoke_failure = deep
        with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
            policy.smoke(kube, items())
        self.assertEqual(len(kube.calls), 1)

    def test_success_with_wrong_api_version_is_not_a_pass(self):
        kube = FakeKubernetes(ready=True)
        reference = FakeKubernetes(ready=True)
        def wrong_version(value):
            original = reference.smoke_request(value)
            if original.returncode != 0:
                return original
            changed = copy.deepcopy(value)
            changed['apiVersion'] = 'example.invalid/v1'
            return SimpleNamespace(returncode=0, stdout=json.dumps(changed), stderr='')
        kube.smoke_failure = wrong_version
        with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
            policy.smoke(kube, items())
        self.assertEqual(len(kube.calls), 1)

    def test_success_with_conflicting_transport_data_is_not_a_pass(self):
        for variant in ('stderr', 'duplicate', 'oversize', 'constant'):
            kube = FakeKubernetes(ready=True)
            def response(value):
                raw = json.dumps(value)
                stdout = {'stderr': raw,
                          'duplicate': raw.replace('"kind": "Pod"', '"kind": "Service", "kind": "Pod"'),
                          'oversize': raw + ' ' * (2 * 1024 * 1024),
                          'constant': raw[:-1] + ',"ext":NaN}'}[variant]
                return SimpleNamespace(returncode=0, stdout=stdout,
                                       stderr='warning' if variant == 'stderr' else '')
            kube.smoke_failure = response
            with self.subTest(variant=variant):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 1)

    def test_corpus_uses_server_dry_run_and_exact_own_policy_denials(self):
        kube = FakeKubernetes(ready=True)
        result = policy.smoke(kube, items())
        self.assertEqual(result['passed_cases'], 15)
        self.assertEqual(result['namespace_cases'], 14)
        self.assertEqual(result['outside_namespace_scope_controls'], 1)
        self.assertEqual(result['persisted_workloads'], 0)
        self.assertEqual({action for action, _, _ in kube.calls}, {'smoke'})
        self.assertTrue(all(row['server_dry_run'] for row in result['cases']))

    def test_generic_rbac_schema_and_other_policy_errors_are_not_a_policy_pass(self):
        name = policy.NAMES[0]
        message = 'Privileged containers are forbidden.'
        for stderr in ['Forbidden: user cannot create Pods', 'BadRequest: unknown field privileged',
                       f"Error: could not find {name}",
                       f"ValidatingAdmissionPolicy '{name}' with binding 'somebody-else' denied request: blocked",
                       f"ValidatingAdmissionPolicy 'different-policy' with binding '{name}' denied request: blocked",
                       f"ValidatingAdmissionPolicy '{name}' with binding '{name}' denied request: expression evaluation error",
                       f"ValidatingAdmissionPolicy '{name}' with binding '{name}' denied request: {message} evaluation error"]:
            self.assertFalse(policy.own_denial(stderr, name, message))
        self.assertTrue(policy.own_denial(f'ValidatingAdmissionPolicy "{name}" with binding "{name}" denied request: {message}', name, message))

    def test_unrelated_rejection_halts_without_claiming_negative_success(self):
        kube = FakeKubernetes(ready=True)
        def bad(value):
            if value['kind'] == 'Pod' and value['spec']['containers'][0]['securityContext'].get('privileged'):
                return SimpleNamespace(returncode=1, stdout='', stderr='Forbidden: user cannot create pods')
            return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr='')
        kube.smoke_failure = bad
        events = []
        with self.assertRaises(policy.PolicyError):
            policy.smoke(kube, items(), record=events.append)
        self.assertEqual(events[-1]['case'], 'deny-privileged')
        self.assertEqual(events[-1]['result'], 'failed')
        self.assertEqual(len(events), 3)

    def test_denial_with_success_payload_does_not_count_as_policy_pass(self):
        kube = FakeKubernetes(ready=True)
        reference = FakeKubernetes(ready=True)
        def mixed(value):
            result = reference.smoke_request(value)
            if result.returncode != 0:
                return SimpleNamespace(returncode=result.returncode,
                                       stderr=result.stderr, stdout=json.dumps(value))
            return result
        kube.smoke_failure = mixed
        with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
            policy.smoke(kube, items())
        self.assertEqual(len(kube.calls), 3)

    def test_malformed_success_metadata_fails_with_policy_error(self):
        for metadata in (None, [], 'bad'):
            kube = FakeKubernetes(ready=True)
            def malformed(value):
                changed = copy.deepcopy(value)
                changed['metadata'] = metadata
                return SimpleNamespace(returncode=0, stdout=json.dumps(changed), stderr='')
            kube.smoke_failure = malformed
            with self.subTest(metadata=metadata):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 1)

    def test_smoke_cannot_create_real_objects_or_access_other_namespaces(self):
        client = object.__new__(policy.Kubernetes)
        client.request = mock.Mock(return_value=SimpleNamespace(returncode=0))
        corpus = policy.smoke_cases(IMAGE)
        for case in (corpus[0], corpus[-1]):
            client.smoke_request(case['object'])
            args, _ = client.request.call_args.args
            self.assertIn('--dry-run=server', args)
            self.assertEqual(args[1], case['object']['metadata']['namespace'])
        bad = copy.deepcopy(corpus[0]['object'])
        bad['metadata']['namespace'] = 'kube-system'
        with self.assertRaises(policy.PolicyError):
            client.smoke_request(bad)
        bad['metadata']['namespace'] = 'default'
        with self.assertRaises(policy.PolicyError):
            client.smoke_request(bad)

    def test_dedicated_create_method_rejects_modified_scope(self):
        client = object.__new__(policy.Kubernetes)
        client.expected = {(x['kind'], x['metadata']['name']): x for x in items()}
        client.json_request = mock.Mock()
        value = copy.deepcopy(items()[0])
        value['spec']['matchConstraints']['namespaceSelector'] = {}
        with self.assertRaises(policy.PolicyError):
            client.create(value)
        client.json_request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
