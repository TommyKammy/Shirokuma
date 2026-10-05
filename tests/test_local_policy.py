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
    def test_local_source_decoder_refuses_diagnostic_stderr_on_success(self):
        source = [{'kind': 'ValidatingPolicy', 'metadata': {'name': name},
                   'spec': {}} for name in policy.BASE_NAMES]
        stdout = '\n'.join(json.dumps(item) for item in source)
        for stderr in ('Warning: local decoder ignored a field', ' ', b''):
            with self.subTest(stderr=stderr):
                reply = SimpleNamespace(stdout=stdout, stderr=stderr, returncode=0)
                with mock.patch.object(policy.subprocess, 'run', return_value=reply):
                    with self.assertRaises(policy.PolicyError):
                        policy.source_policies()
        reply = SimpleNamespace(stdout=stdout, stderr='', returncode=0)
        with mock.patch.object(policy.subprocess, 'run', return_value=reply):
            self.assertEqual(len(policy.source_policies()), len(policy.BASE_NAMES))

    def test_local_source_decoder_refuses_nonrepresentable_floats(self):
        source = [{'kind': 'ValidatingPolicy', 'metadata': {'name': name},
                   'spec': {}} for name in policy.BASE_NAMES]
        for number in ('1e999', '-1e999', '1e-999'):
            with self.subTest(number=number):
                encoded = [json.dumps(item) for item in source]
                encoded[0] = encoded[0].replace('"spec": {}', '"spec": {"value": ' + number + '}')
                reply = SimpleNamespace(stdout='\n'.join(encoded), stderr='', returncode=0)
                with mock.patch.object(policy.subprocess, 'run', return_value=reply):
                    with self.assertRaises(policy.PolicyError):
                        policy.source_policies()
        encoded = [json.dumps(item) for item in source]
        encoded[0] = encoded[0].replace('"spec": {}', '"spec": {"value": 1.25e-2}')
        reply = SimpleNamespace(stdout='\n'.join(encoded), stderr='', returncode=0)
        with mock.patch.object(policy.subprocess, 'run', return_value=reply):
            self.assertEqual(policy.source_policies()[0]['spec']['value'], 0.0125)

    def test_local_source_decoder_refuses_malformed_or_ambiguous_output(self):
        for stdout, status in (
            ('{', 0), ('null', 0), ('{"metadata":null}', 0),
            ('{"metadata":{"name":"x","name":"y"}}', 0),
            ('{"metadata":{"name":"x"},"spec":{"value":NaN}}', 0),
            ('é' * (128 * 1024 + 1), 0), ('\ud800', 0), (b'{}', 0),
            ('{}', False),
        ):
            with self.subTest(status=status, size=len(stdout)):
                reply = SimpleNamespace(stdout=stdout, stderr='', returncode=status)
                with mock.patch.object(policy.subprocess, 'run', return_value=reply):
                    with self.assertRaises(policy.PolicyError):
                        policy.source_policies()

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

    def test_malformed_empty_warning_shapes_never_certify_typecheck(self):
        value = admitted(items()[0])
        for warnings in ('', {}, None, False, 0):
            with self.subTest(warnings=warnings):
                value['status']['typeChecking']['expressionWarnings'] = warnings
                self.assertNotEqual(policy.typecheck(value), 'complete')
                kube = FakeKubernetes(ready=True)
                kube.objects[(policy.POLICY, items()[0]['metadata']['name'])] = copy.deepcopy(value)
                self.assertFalse(policy.inventory_status(kube, items())['ready'])

    def test_malformed_typecheck_status_fails_closed(self):
        original = admitted(items()[0])
        for status, metadata in (([], original['metadata']), (None, original['metadata']),
                                 (original['status'], []), (original['status'], None)):
            with self.subTest(status=status, metadata=metadata):
                value = copy.deepcopy(original)
                value['status'], value['metadata'] = status, metadata
                self.assertEqual(policy.typecheck(value), 'pending')

    def test_empty_server_selector_defaults_do_not_require_overwrite(self):
        value, expected = admitted(items()[0]), items()[0]
        value['spec']['matchConstraints']['objectSelector'] = {}
        value['spec']['matchConstraints']['excludeResourceRules'] = []
        policy.assert_owned(value, expected)
        value['spec']['matchConstraints']['objectSelector'] = {'matchLabels': {'unapproved': 'scope'}}
        with self.assertRaises(policy.PolicyError):
            policy.assert_owned(value, expected)


class ScopedResponseTests(unittest.TestCase):
    def test_smoke_image_requires_one_well_formed_local_trino_container(self):
        client = object.__new__(policy.Kubernetes)
        valid = {'apiVersion': 'apps/v1', 'kind': 'Deployment',
                 'metadata': {'name': 'trino-local', 'namespace': policy.NAMESPACE},
                 'spec': {'template': {'spec': {'containers': [
            {'name': 'trino', 'image': 'shirokuma.local/trino@sha256:' + 'a' * 64}]}}}}
        client.json_request = mock.Mock(return_value=valid)
        self.assertEqual(client.image(), valid['spec']['template']['spec']['containers'][0]['image'])
        for reply in ({}, {'spec': None}, {'spec': {'template': {'spec': {'containers': None}}}},
                      {'spec': {'template': {'spec': {'containers': [{'name': 'trino'}]}}}},
                      {'spec': {'template': {'spec': {'containers': [None]}}}}):
            with self.subTest(reply=reply):
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()

        for field, value in (('apiVersion', 'v1'), ('kind', 'Pod'),
                             ('metadata', {'name': 'other', 'namespace': policy.NAMESPACE}),
                             ('metadata', {'name': 'trino-local', 'namespace': 'default'}),
                             ('metadata', {'name': 'trino-local', 'namespace': policy.NAMESPACE,
                                           'deletionTimestamp': '2026-10-05T00:00:00Z'}),
                             ('metadata', [])):
            with self.subTest(field=field, value=value):
                reply = copy.deepcopy(valid)
                reply[field] = value
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()

    def test_success_with_stderr_cannot_certify_policy_read_or_mutation(self):
        client = object.__new__(policy.Kubernetes)
        read = ['get', policy.RESOURCES[policy.POLICY], policy.NAMES[0],
                '--ignore-not-found', '-o', 'json']
        for args, stdout in ((read, ''), (read, '{}'),
                             (['create', '-f', '-', '-o', 'json'], '{}')):
            with self.subTest(args=args, stdout=stdout):
                client.request = mock.Mock(return_value=SimpleNamespace(
                    returncode=0, stdout=stdout, stderr='Warning: unexamined API output'))
                with self.assertRaises(policy.PolicyError):
                    client.json_request(args)
        client.request.return_value = SimpleNamespace(returncode=0, stdout='{}', stderr='')
        self.assertEqual(client.json_request(read), {})

    def test_only_exact_missing_resource_read_accepts_empty_success(self):
        client = object.__new__(policy.Kubernetes)
        client.request = mock.Mock(return_value=SimpleNamespace(
            stdout='', stderr='', returncode=0))
        read = ['get', policy.RESOURCES[policy.POLICY], policy.NAMES[0],
                '--ignore-not-found', '-o', 'json']
        self.assertIsNone(client.json_request(read))
        candidate = ['-n', policy.NAMESPACE, 'get', 'pod',
                     'shirokuma-policy-candidate-' + 'a' * 32,
                     '--ignore-not-found', '-o', 'json']
        # The candidate preflight performs this exact absent-Pod read before
        # it can durably record and send a server dry-run request.
        self.assertIsNone(client.json_request(candidate))
        for args, stdout in ((read, '  \n'),
                             (candidate, '  \n'),
                             (candidate[:5] + ['-o', 'json'], ''),
                             (candidate[:4] + ['other'] + candidate[5:], ''),
                             (['-n', 'default', *candidate[2:]], ''),
                             (['create', '-f', '-', '-o', 'json'], ''),
                             (['delete', '--raw=/dedicated', '-f', '-'], ''),
                             (['get', policy.RESOURCES[policy.POLICY], policy.NAMES[0], '-o', 'json'], '')):
            with self.subTest(args=args, stdout=stdout):
                client.request.return_value = SimpleNamespace(stdout=stdout, stderr='', returncode=0)
                with self.assertRaisesRegex(policy.PolicyError, 'no JSON object'):
                    client.json_request(args)

    def test_json_request_bounds_utf8_and_rejects_malformed_replies(self):
        client = object.__new__(policy.Kubernetes)
        for stdout, status in (
            ('é' * (1024 * 1024 + 1), 0),
            ('\ud800', 0),
            (b'{}', 0),
            ('{}', False),
            ('{', 0),
        ):
            with self.subTest(status=status, size=len(stdout)):
                client.request = mock.Mock(return_value=SimpleNamespace(
                    stdout=stdout, stderr='', returncode=status))
                with self.assertRaises(policy.PolicyError):
                    client.json_request(['get', 'pods'])
        client.request = mock.Mock(return_value=SimpleNamespace(
            stdout='{"value":"é"}', stderr='', returncode=0))
        self.assertEqual(client.json_request(['get', 'pods']), {'value': 'é'})

    def test_scoped_json_reply_requires_object(self):
        client = object.__new__(policy.Kubernetes)
        for stdout in ('null', 'false', '0', '"ok"', '[]', '[{}]'):
            with self.subTest(stdout=stdout):
                client.request = mock.Mock(return_value=SimpleNamespace(
                    stdout=stdout, stderr='', returncode=0))
                with self.assertRaisesRegex(policy.PolicyError, 'not a JSON object'):
                    client.json_request(['get', 'pods'])
        client.request = mock.Mock(return_value=SimpleNamespace(
            stdout='{}', stderr='', returncode=0))
        self.assertEqual(client.json_request(['get', 'pods']), {})

    def test_scoped_json_reply_refuses_nested_ambiguity_and_unrepresentable_numbers(self):
        client = object.__new__(policy.Kubernetes)
        # A top-level object is not sufficient: duplicate nested claims and
        # numeric tokens rounded to infinity or zero must not certify a read.
        for stdout in ('{"metadata":{"name":"other","name":"owned"}}',
                       '{"metadata":{"na\\u006de":"other","name":"owned"}}',
                       '{"status":{"generation":1e999}}',
                       '{"status":{"generation":-1e-999}}',
                       '{"status":{"generation":NaN}}'):
            with self.subTest(stdout=stdout):
                client.request = mock.Mock(return_value=SimpleNamespace(
                    stdout=stdout, stderr='', returncode=0))
                with self.assertRaises(policy.PolicyError):
                    client.json_request(['get', 'pods'])
        client.request = mock.Mock(return_value=SimpleNamespace(
            stdout='{"status":{"generation":0e-999}}', stderr='', returncode=0))
        self.assertEqual(client.json_request(['get', 'pods']),
                         {'status': {'generation': 0.0}})


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
    def test_pod_success_rejects_unexamined_spec_and_container_keys(self):
        for location in ('spec', 'container'):
            for value in (None, {'enabled': True}):
                kube = FakeKubernetes(ready=True)
                def changed(request):
                    reply = copy.deepcopy(request)
                    if reply['kind'] == 'Pod':
                        target = (reply['spec'] if location == 'spec'
                                  else reply['spec']['containers'][0])
                        target['unexaminedControl'] = value
                    return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
                kube.smoke_failure = changed
                with self.subTest(location=location, value=value), self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 1)
        self.assertEqual(policy.smoke(FakeKubernetes(ready=True), items())['passed_cases'], 15)

    def test_malformed_unicode_success_reply_fails_closed(self):
        kube = FakeKubernetes(ready=True)
        def changed(value):
            reply = copy.deepcopy(value)
            reply['metadata']['annotations'] = {'unexpected': '\ud800'}
            return SimpleNamespace(returncode=0, stdout=json.dumps(reply, ensure_ascii=False), stderr='')
        kube.smoke_failure = changed
        with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
            policy.smoke(kube, items())
        self.assertEqual(len(kube.calls), 1)

    def test_pod_success_refuses_unsubmitted_pod_level_resources(self):
        for resources in ({'requests': {'cpu': '8'}},
                          {'limits': {'memory': '8Gi'}}, True):
            kube = FakeKubernetes(ready=True)
            def changed(value):
                reply = copy.deepcopy(value)
                if reply['kind'] == 'Pod':
                    reply['spec']['resources'] = resources
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(resources=resources), self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                policy.smoke(kube, items())
            self.assertEqual(len(kube.calls), 1)
        submitted = policy.smoke_cases(IMAGE)[0]['object']
        for empty in (None, {}):
            reply = copy.deepcopy(submitted)
            reply['spec']['resources'] = empty
            self.assertTrue(policy.smoke_success_scope(reply, submitted))

    def test_pod_success_refuses_unsubmitted_container_restart_rules(self):
        for rules in ([{'action': 'Restart', 'exitCodes': {'operator': 'In', 'values': [1]}}],
                      [{'action': 'Restart', 'exitCodes': {'operator': 'NotIn', 'values': [0]}}]):
            kube = FakeKubernetes(ready=True)
            def changed(value):
                reply = copy.deepcopy(value)
                if reply['kind'] == 'Pod':
                    reply['spec']['containers'][0]['restartPolicyRules'] = rules
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(rules=rules), self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                policy.smoke(kube, items())
            self.assertEqual(len(kube.calls), 1)
        submitted = policy.smoke_cases(FakeKubernetes().image())[0]['object']
        for empty in (None, []):
            reply = copy.deepcopy(submitted)
            reply['spec']['containers'][0]['restartPolicyRules'] = empty
            self.assertTrue(policy.smoke_success_scope(reply, submitted))

    def test_pod_success_refuses_added_scheduling_tolerations(self):
        reference = FakeKubernetes(ready=True)
        defaults = [
            {'key': key, 'operator': 'Exists', 'effect': 'NoExecute', 'tolerationSeconds': 300}
            for key in ('node.kubernetes.io/not-ready', 'node.kubernetes.io/unreachable')
        ]
        for tolerations in ([{'key': 'dedicated', 'operator': 'Exists', 'effect': 'NoSchedule'}],
                            defaults + [{'key': 'dedicated', 'operator': 'Exists', 'effect': 'NoSchedule'}],
                            [defaults[0], defaults[0]],
                            [dict(defaults[0], tolerationSeconds=True)],
                            [dict(defaults[0], effect='NoSchedule')]):
            kube = FakeKubernetes(ready=True)
            def changed(value):
                reply = copy.deepcopy(value)
                if reply['kind'] == 'Pod':
                    reply['spec']['tolerations'] = tolerations
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(tolerations=tolerations), self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                policy.smoke(kube, items())
            self.assertEqual(len(kube.calls), 1)
        submitted = policy.smoke_cases(reference.image())[0]['object']
        for allowed in ([], defaults, list(reversed(defaults)), defaults[:1]):
            reply = copy.deepcopy(submitted)
            reply['spec']['tolerations'] = allowed
            with self.subTest(allowed=allowed):
                self.assertTrue(policy.smoke_success_scope(reply, submitted))

        # Exercise the complete 15-case path, not only the comparator helper:
        # these are API-defaulted values on the allowed Pod, not on denied cases.
        for allowed in (defaults, list(reversed(defaults))):
            kube = FakeKubernetes(ready=True)
            def defaulted(value):
                original = reference.smoke_request(value)
                if original.returncode != 0:
                    return original
                reply = copy.deepcopy(value)
                if reply['kind'] == 'Pod':
                    reply['spec']['tolerations'] = allowed
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = defaulted
            with self.subTest(allowed=allowed):
                self.assertEqual(policy.smoke(kube, items())['passed_cases'], 15)

    def test_pod_success_does_not_add_os_constraint(self):
        for os_value in ({'name': 'windows'}, {'name': 'linux'}, None, True):
            kube = FakeKubernetes(ready=True)
            def changed(value):
                reply = copy.deepcopy(value)
                if reply['kind'] == 'Pod':
                    reply['spec']['os'] = os_value
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(os_value=os_value), self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                policy.smoke(kube, items())
            self.assertEqual(len(kube.calls), 1)

    def test_success_rejects_boolean_numeric_aliases(self):
        mutations = (
            lambda pod: pod['spec']['securityContext'].update(runAsUser=True),
            lambda pod: pod['spec']['securityContext'].update(runAsNonRoot=1),
            lambda pod: pod['spec']['containers'][0]['securityContext'].update(allowPrivilegeEscalation=0),
            lambda pod: pod['spec']['containers'][0]['securityContext'].update(readOnlyRootFilesystem=1),
        )
        for mutate in mutations:
            kube = FakeKubernetes(ready=True)
            def changed(value):
                reply = copy.deepcopy(value)
                if reply['kind'] == 'Pod':
                    mutate(reply)
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(mutate=mutate), self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                policy.smoke(kube, items())
            self.assertEqual(len(kube.calls), 1)

    def test_pod_success_rejects_resource_json_type_alias(self):
        kube = FakeKubernetes(ready=True)
        submitted = policy.smoke_cases(kube.image())[0]['object']
        submitted['spec']['containers'][0]['resources']['requests']['cpu'] = 1
        reply = copy.deepcopy(submitted)
        self.assertTrue(policy.smoke_success_scope(reply, submitted))
        reply['spec']['containers'][0]['resources']['requests']['cpu'] = True
        self.assertFalse(policy.smoke_success_scope(reply, submitted))

    def test_success_rejects_unsubmitted_generate_name(self):
        reference = FakeKubernetes(ready=True)
        for kind, expected_calls in (('Pod', 1), ('Service', 2)):
            kube = FakeKubernetes(ready=True)
            def changed(value):
                if value['kind'] != kind:
                    return reference.smoke_request(value)
                reply = copy.deepcopy(value)
                reply['metadata']['generateName'] = 'other-'
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(kind=kind), self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                policy.smoke(kube, items())
            self.assertEqual(len(kube.calls), expected_calls)

    def test_service_success_rejects_boolean_node_port_alias(self):
        kube = FakeKubernetes(ready=True)
        reference = FakeKubernetes(ready=True)
        def changed(value):
            if value['kind'] != 'Service':
                return reference.smoke_request(value)
            reply = copy.deepcopy(value)
            reply['spec']['ports'][0]['nodePort'] = False
            return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
        kube.smoke_failure = changed
        with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
            policy.smoke(kube, items())
        self.assertEqual(len(kube.calls), 2)

    def test_pod_success_rejects_unsubmitted_pod_security_controls(self):
        for mutation in ({'fsGroup': 0},
                         {'supplementalGroups': [0]},
                         {'sysctls': [{'name': 'net.ipv4.ip_forward', 'value': '1'}]},
                         {'seLinuxOptions': {'level': 's0:c1,c2'}},
                         {'windowsOptions': {'hostProcess': True}}):
            kube = FakeKubernetes(ready=True)
            def changed(value):
                reply = copy.deepcopy(value)
                if reply['kind'] == 'Pod':
                    reply['spec']['securityContext'].update(mutation)
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(mutation=mutation), self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                policy.smoke(kube, items())
            self.assertEqual(len(kube.calls), 1)

    def test_success_does_not_change_submitted_scope_label(self):
        for kind in ('Pod', 'Service'):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def changed(value):
                if value['kind'] != kind:
                    return reference.smoke_request(value)
                reply = copy.deepcopy(value)
                reply['metadata']['labels']['shirokuma.dev/scope'] = 'other'
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(kind=kind), self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                policy.smoke(kube, items())
            self.assertEqual(len(kube.calls), 1 if kind == 'Pod' else 2)

    def test_success_does_not_add_unsubmitted_selector_labels(self):
        for kind in ('Pod', 'Service'):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def changed(value):
                if value['kind'] != kind:
                    return reference.smoke_request(value)
                reply = copy.deepcopy(value)
                reply['metadata']['labels']['app.kubernetes.io/name'] = 'other-workload'
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(kind=kind), self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                policy.smoke(kube, items())
            self.assertEqual(len(kube.calls), 1 if kind == 'Pod' else 2)

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

    def test_pod_success_does_not_add_container_io_or_mounts(self):
        mutations = (
            {'volumeMounts': [{'name': 'unexpected', 'mountPath': '/data'}]},
            {'volumeDevices': [{'name': 'unexpected', 'devicePath': '/dev/test'}]},
            {'stdinOnce': True},
        )
        for mutation in mutations:
            kube = FakeKubernetes(ready=True)
            def changed(value):
                reply = copy.deepcopy(value)
                if reply['kind'] == 'Pod':
                    reply['spec']['containers'][0].update(mutation)
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 1)

    def test_pod_success_does_not_add_lifecycle_controls(self):
        for mutation in ({'activeDeadlineSeconds': 1},
                         {'readinessGates': [{'conditionType': 'example.invalid/ready'}]}):
            kube = FakeKubernetes(ready=True)
            def changed(value):
                reply = copy.deepcopy(value)
                if reply['kind'] == 'Pod':
                    reply['spec'].update(mutation)
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 1)

    def test_pod_success_does_not_add_topology_or_overhead(self):
        for mutation in (
            {'topologySpreadConstraints': [{'maxSkew': 1, 'topologyKey': 'kubernetes.io/hostname',
                                            'whenUnsatisfiable': 'DoNotSchedule',
                                            'labelSelector': {'matchLabels': {'app': 'other'}}}]},
            {'overhead': {'cpu': '100m'}},
        ):
            kube = FakeKubernetes(ready=True)
            def changed(value):
                reply = copy.deepcopy(value)
                if reply['kind'] == 'Pod':
                    reply['spec'].update(mutation)
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(mutation=mutation):
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
                         lambda service: service['spec']['ports'][0].update(name='other-route'),
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

    def test_success_does_not_add_metadata_controls(self):
        for kind in ('Pod', 'Service'):
            for mutation in ({'annotations': {'example.invalid/injected': 'value'}},
                             {'finalizers': ['example.invalid/hold']}):
                kube = FakeKubernetes(ready=True)
                reference = FakeKubernetes(ready=True)
                def changed(value):
                    if value['kind'] != kind or len(kube.calls) != (1 if kind == 'Pod' else 2):
                        return reference.smoke_request(value)
                    reply = copy.deepcopy(value)
                    reply['metadata'].update(mutation)
                    return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
                kube.smoke_failure = changed
                with self.subTest(kind=kind, mutation=mutation):
                    with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                        policy.smoke(kube, items())
                    self.assertEqual(len(kube.calls), 1 if kind == 'Pod' else 2)

    def test_success_accepts_empty_metadata_defaults(self):
        kube = FakeKubernetes(ready=True)
        reference = FakeKubernetes(ready=True)
        def changed(value):
            reply = copy.deepcopy(value)
            if len(kube.calls) in (1, 2):
                reply['metadata'].update(annotations={}, finalizers=[])
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            return reference.smoke_request(value)
        kube.smoke_failure = changed
        self.assertEqual(policy.smoke(kube, items())['passed_cases'], len(kube.calls))

    def test_service_success_does_not_add_external_routing_controls(self):
        for mutation in (
            {'externalName': 'outside.example.invalid'},
            {'loadBalancerIP': '192.0.2.1'},
            {'loadBalancerSourceRanges': ['0.0.0.0/0']},
            {'loadBalancerClass': 'example.invalid/external'},
            {'allocateLoadBalancerNodePorts': True},
        ):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def changed(value):
                if value['kind'] != 'Service':
                    return reference.smoke_request(value)
                reply = copy.deepcopy(value)
                reply['spec'].update(mutation)
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 2)

    def test_service_success_does_not_add_internal_routing_controls(self):
        for mutation in ({'sessionAffinity': 'ClientIP'},
                          {'sessionAffinityConfig': {'clientIP': {'timeoutSeconds': 10800}}},
                          {'publishNotReadyAddresses': True},
                          {'internalTrafficPolicy': 'Local'},
                          {'externalTrafficPolicy': 'Local'},
                          {'healthCheckNodePort': 30001}):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def changed(value):
                if value['kind'] != 'Service':
                    return reference.smoke_request(value)
                reply = copy.deepcopy(value)
                reply['spec'].update(mutation)
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 2)

    def test_service_success_accepts_inert_external_routing_defaults(self):
        for defaults in ({}, {'externalTrafficPolicy': 'Cluster'},
                         {'healthCheckNodePort': 0},
                         {'externalTrafficPolicy': 'Cluster', 'healthCheckNodePort': 0}):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def changed(value):
                if (value['kind'] != 'Service' or value['spec'].get('type') != 'ClusterIP' or
                        value['spec'].get('externalIPs')):
                    return reference.smoke_request(value)
                reply = copy.deepcopy(value)
                reply['spec'].update(defaults)
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(defaults=defaults):
                self.assertEqual(policy.smoke(kube, items())['passed_cases'], len(kube.calls))

    def test_service_success_does_not_enable_dual_stack(self):
        for mutation in ({'ipFamilyPolicy': 'RequireDualStack'},
                         {'ipFamilyPolicy': 'PreferDualStack'},
                         {'ipFamilies': ['IPv4', 'IPv6']},
                         {'ipFamilies': ['IPv4', 'IPv4']},
                         {'ipFamilies': ['unexpected']}):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def changed(value):
                if value['kind'] != 'Service':
                    return reference.smoke_request(value)
                reply = copy.deepcopy(value)
                reply['spec'].update(mutation)
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 2)

    def test_service_success_accepts_single_defaulted_ip_family(self):
        for family in ('IPv4', 'IPv6'):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def changed(value):
                if (value['kind'] != 'Service' or
                        value['spec'].get('type') != 'ClusterIP' or
                        value['spec'].get('externalIPs')):
                    return reference.smoke_request(value)
                reply = copy.deepcopy(value)
                reply['spec']['ipFamilies'] = [family]
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(family=family):
                self.assertEqual(policy.smoke(kube, items())['passed_cases'], len(kube.calls))

    def test_service_success_rejects_multiple_cluster_addresses(self):
        for addresses in (['192.0.2.1', '2001:db8::1'],
                          ['192.0.2.1', '192.0.2.2'], '192.0.2.1'):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def changed(value):
                if value['kind'] != 'Service':
                    return reference.smoke_request(value)
                reply = copy.deepcopy(value)
                reply['spec']['clusterIPs'] = addresses
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(addresses=addresses):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 2)

    def test_service_success_accepts_one_defaulted_cluster_address(self):
        for addresses in ([], ['192.0.2.1'], ['2001:db8::1']):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def changed(value):
                if (value['kind'] != 'Service' or
                        value['spec'].get('type') != 'ClusterIP' or
                        value['spec'].get('externalIPs')):
                    return reference.smoke_request(value)
                reply = copy.deepcopy(value)
                reply['spec']['clusterIPs'] = addresses
                if addresses:
                    reply['spec']['ipFamilies'] = ['IPv6' if ':' in addresses[0] else 'IPv4']
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(addresses=addresses):
                self.assertEqual(policy.smoke(kube, items())['passed_cases'], len(kube.calls))

    def test_service_success_rejects_conflicting_cluster_address_fields(self):
        for cluster_ip, addresses in (('192.0.2.1', ['192.0.2.2']),
                                      ('None', ['192.0.2.1']),
                                      ('', ['192.0.2.1'])):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def changed(value):
                if value['kind'] != 'Service':
                    return reference.smoke_request(value)
                reply = copy.deepcopy(value)
                reply['spec'].update(clusterIP=cluster_ip, clusterIPs=addresses)
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(cluster_ip=cluster_ip):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 2)

    def test_service_success_accepts_matching_cluster_address_fields(self):
        kube = FakeKubernetes(ready=True)
        reference = FakeKubernetes(ready=True)
        def changed(value):
            if (value['kind'] != 'Service' or value['spec'].get('type') != 'ClusterIP' or
                    value['spec'].get('externalIPs')):
                return reference.smoke_request(value)
            reply = copy.deepcopy(value)
            reply['spec'].update(clusterIP='192.0.2.1', clusterIPs=['192.0.2.1'],
                                 ipFamilies=['IPv4'])
            return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
        kube.smoke_failure = changed
        self.assertEqual(policy.smoke(kube, items())['passed_cases'], len(kube.calls))

    def test_service_success_rejects_address_family_disagreement(self):
        for mutation in (
            {'clusterIP': 'not-an-ip'},
            {'clusterIPs': ['not-an-ip']},
            {'clusterIP': '2001:db8::1', 'ipFamilies': ['IPv4']},
            {'clusterIPs': ['192.0.2.1'], 'ipFamilies': ['IPv6']},
            {'clusterIP': '192.0.2.1', 'clusterIPs': ['192.0.2.1'], 'ipFamilies': ['IPv6']},
        ):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def changed(value):
                if value['kind'] != 'Service':
                    return reference.smoke_request(value)
                reply = copy.deepcopy(value)
                reply['spec'].update(mutation)
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 2)

    def test_service_success_rejects_scoped_ipv6_cluster_address(self):
        # ipaddress accepts an interface zone, but ClusterIP is an unscoped
        # address; a fake successful dry-run must not introduce that suffix.
        for mutation in ({'clusterIP': 'fe80::1%eth0'},
                         {'clusterIPs': ['fe80::1%eth0']},
                         {'clusterIP': 'fe80::1%eth0', 'clusterIPs': ['fe80::1%eth0'],
                          'ipFamilies': ['IPv6']}):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def changed(value):
                if value['kind'] != 'Service':
                    return reference.smoke_request(value)
                reply = copy.deepcopy(value)
                reply['spec'].update(mutation)
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 2)

    def test_service_success_rejects_address_without_family(self):
        # A claimed allocation without ipFamilies cannot be checked against
        # the fixed single-stack Service's address family.
        for field, address in (('clusterIP', '192.0.2.1'),
                               ('clusterIPs', ['192.0.2.1'])):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def changed(value):
                if (value['kind'] != 'Service' or value['spec'].get('type') != 'ClusterIP' or
                        value['spec'].get('externalIPs')):
                    return reference.smoke_request(value)
                reply = copy.deepcopy(value)
                reply['spec'][field] = address
                reply['spec'].pop('ipFamilies', None)
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(field=field):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 2)

    def test_service_success_rejects_ipv4_mapped_ipv6_cluster_address(self):
        # A mapped IPv4 address is not an ordinary IPv6 Service ClusterIP,
        # even though ipaddress reports version 6 for it.
        address = '::ffff:192.0.2.1'
        for mutation in ({'clusterIP': address},
                         {'clusterIPs': [address]},
                         {'clusterIP': address, 'clusterIPs': [address],
                          'ipFamilies': ['IPv6']}):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def changed(value):
                if (value['kind'] != 'Service' or value['spec'].get('type') != 'ClusterIP' or
                        value['spec'].get('externalIPs')):
                    return reference.smoke_request(value)
                reply = copy.deepcopy(value)
                reply['spec'].update(mutation)
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 2)

    def test_service_success_rejects_embedded_ipv4_ipv6_cluster_address(self):
        # IPv4-compatible and IPv4-translated forms are not ordinary IPv6
        # Service VIPs, even though ipaddress does not flag them as mapped.
        for address in ('::192.0.2.1', '::ffff:0:192.0.2.1'):
            for field in ('clusterIP', 'clusterIPs'):
                kube = FakeKubernetes(ready=True)
                reference = FakeKubernetes(ready=True)
                def changed(value):
                    if (value['kind'] != 'Service' or value['spec'].get('type') != 'ClusterIP' or
                            value['spec'].get('externalIPs')):
                        return reference.smoke_request(value)
                    reply = copy.deepcopy(value)
                    reply['spec'][field] = address if field == 'clusterIP' else [address]
                    reply['spec']['ipFamilies'] = ['IPv6']
                    return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
                kube.smoke_failure = changed
                with self.subTest(address=address, field=field):
                    with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                        policy.smoke(kube, items())
                    self.assertEqual(len(kube.calls), 2)

    def test_service_success_rejects_non_unicast_cluster_address(self):
        # A syntactically valid address is not necessarily a usable Service
        # destination. These cannot represent an allocated unicast ClusterIP.
        for address, family in (('0.0.0.0', 'IPv4'), ('224.0.0.1', 'IPv4'),
                                ('::', 'IPv6'), ('ff02::1', 'IPv6')):
            for field in ('clusterIP', 'clusterIPs'):
                kube = FakeKubernetes(ready=True)
                reference = FakeKubernetes(ready=True)
                def changed(value):
                    if (value['kind'] != 'Service' or value['spec'].get('type') != 'ClusterIP' or
                            value['spec'].get('externalIPs')):
                        return reference.smoke_request(value)
                    reply = copy.deepcopy(value)
                    reply['spec'][field] = address if field == 'clusterIP' else [address]
                    reply['spec']['ipFamilies'] = [family]
                    return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
                kube.smoke_failure = changed
                with self.subTest(address=address, field=field):
                    with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                        policy.smoke(kube, items())
                    self.assertEqual(len(kube.calls), 2)

    def test_service_success_rejects_host_local_cluster_address(self):
        # Neither loopback nor link-local addresses can be a Service VIP,
        # even when their syntax and ipFamilies agree.
        for address, family in (('127.0.0.1', 'IPv4'), ('127.255.255.254', 'IPv4'),
                                ('169.254.1.2', 'IPv4'),
                                ('::1', 'IPv6'), ('fe80::1', 'IPv6')):
            for field in ('clusterIP', 'clusterIPs'):
                kube = FakeKubernetes(ready=True)
                reference = FakeKubernetes(ready=True)
                def changed(value):
                    if (value['kind'] != 'Service' or value['spec'].get('type') != 'ClusterIP' or
                            value['spec'].get('externalIPs')):
                        return reference.smoke_request(value)
                    reply = copy.deepcopy(value)
                    reply['spec'][field] = address if field == 'clusterIP' else [address]
                    reply['spec']['ipFamilies'] = [family]
                    return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
                kube.smoke_failure = changed
                with self.subTest(address=address, field=field):
                    with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                        policy.smoke(kube, items())
                    self.assertEqual(len(kube.calls), 2)

    def test_service_success_rejects_limited_broadcast_cluster_address(self):
        # ipaddress does not classify the IPv4 limited broadcast destination
        # as multicast, unspecified, loopback or link-local.
        for field in ('clusterIP', 'clusterIPs'):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def changed(value):
                if (value['kind'] != 'Service' or value['spec'].get('type') != 'ClusterIP' or
                        value['spec'].get('externalIPs')):
                    return reference.smoke_request(value)
                reply = copy.deepcopy(value)
                reply['spec'][field] = '255.255.255.255' if field == 'clusterIP' else ['255.255.255.255']
                reply['spec']['ipFamilies'] = ['IPv4']
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(field=field):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 2)

    def test_service_success_rejects_reserved_ipv4_cluster_address(self):
        # 0/8 (other than the already rejected unspecified address) and 240/4
        # are not ordinary allocatable IPv4 unicast Service destinations.
        for address in ('0.1.2.3', '0.255.255.255', '240.0.0.1', '255.255.255.254'):
            for field in ('clusterIP', 'clusterIPs'):
                kube = FakeKubernetes(ready=True)
                reference = FakeKubernetes(ready=True)
                def changed(value):
                    if (value['kind'] != 'Service' or value['spec'].get('type') != 'ClusterIP' or
                            value['spec'].get('externalIPs')):
                        return reference.smoke_request(value)
                    reply = copy.deepcopy(value)
                    reply['spec'][field] = address if field == 'clusterIP' else [address]
                    reply['spec']['ipFamilies'] = ['IPv4']
                    return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
                kube.smoke_failure = changed
                with self.subTest(address=address, field=field):
                    with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                        policy.smoke(kube, items())
                    self.assertEqual(len(kube.calls), 2)

    def test_service_success_accepts_address_matching_family(self):
        for address, family in (('192.0.2.1', 'IPv4'), ('2001:db8::1', 'IPv6')):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def changed(value):
                if (value['kind'] != 'Service' or value['spec'].get('externalIPs') or
                        value['spec'].get('type') != 'ClusterIP'):
                    return reference.smoke_request(value)
                reply = copy.deepcopy(value)
                reply['spec'].update(clusterIP=address, clusterIPs=[address], ipFamilies=[family])
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(family=family):
                self.assertEqual(policy.smoke(kube, items())['passed_cases'], len(kube.calls))

    def test_service_success_rejects_empty_cluster_ips_with_allocated_ip(self):
        kube = FakeKubernetes(ready=True)
        reference = FakeKubernetes(ready=True)
        def changed(request):
            if (request['kind'] != 'Service' or request['spec'].get('type') != 'ClusterIP' or
                    request['spec'].get('externalIPs')):
                return reference.smoke_request(request)
            reply = copy.deepcopy(request)
            reply['spec'].update(clusterIP='192.0.2.1', clusterIPs=[], ipFamilies=['IPv4'])
            return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
        kube.smoke_failure = changed
        with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
            policy.smoke(kube, items())
        self.assertEqual(len(kube.calls), 2)

    def test_service_success_rejects_unsubmitted_port_routing_fields(self):
        # A fake successful ServicePort must not carry Pod-style host routing
        # fields or future unexamined keys while claiming the fixed port passed.
        for field, value in (('hostIP', '0.0.0.0'), ('hostPort', 18080),
                             ('hostIP', None), ('hostPort', None),
                             ('containerPort', 8080)):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def changed(request):
                if (request['kind'] != 'Service' or request['spec'].get('type') != 'ClusterIP' or
                        request['spec'].get('externalIPs')):
                    return reference.smoke_request(request)
                reply = copy.deepcopy(request)
                reply['spec']['ports'][0][field] = value
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(field=field):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 2)

    def test_service_success_rejects_unexamined_spec_fields(self):
        # A newly introduced Service spec field is not automatically safe just
        # because the known routing fields still match the submitted request.
        for value in (None, {'enabled': True}):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def changed(request):
                if (request['kind'] != 'Service' or request['spec'].get('type') != 'ClusterIP' or
                        request['spec'].get('externalIPs')):
                    return reference.smoke_request(request)
                reply = copy.deepcopy(request)
                reply['spec']['unexaminedRouting'] = value
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(value=value):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 2)

    def test_smoke_success_rejects_unexamined_envelope_and_metadata(self):
        # A successful response with a new claim is not evidence that the
        # submitted fixed object was the only effective object inspected.
        for location, field, payload in (('envelope', 'items', []),
                                         ('envelope', 'unexpected', None),
                                         ('metadata', 'unexpected', None),
                                         ('metadata', 'managedFieldsAlias', [])):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def changed(request):
                if request['kind'] != 'Pod':
                    return reference.smoke_request(request)
                reply = copy.deepcopy(request)
                (reply if location == 'envelope' else reply['metadata'])[field] = payload
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(location=location, field=field):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 1)

    def test_service_success_does_not_add_traffic_hints(self):
        for mutation in ('distribution', 'application-protocol'):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def changed(value):
                if value['kind'] != 'Service':
                    return reference.smoke_request(value)
                reply = copy.deepcopy(value)
                if mutation == 'distribution':
                    reply['spec']['trafficDistribution'] = 'PreferClose'
                else:
                    reply['spec']['ports'][0]['appProtocol'] = 'kubernetes.io/h2c'
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(mutation=mutation):
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

    def test_pod_success_does_not_add_dns_or_hostname_controls(self):
        for mutation in ({'dnsConfig': {'nameservers': ['192.0.2.1']}},
                         {'hostname': 'unexpected'},
                         {'subdomain': 'outside'},
                         {'setHostnameAsFQDN': True},
                         {'enableServiceLinks': False},
                         {'hostUsers': False}):
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

    def test_pod_success_does_not_accept_scheduling_or_environment_injection(self):
        for field, injected in (
            ('nodeSelector', {'zone': 'other'}),
            ('affinity', {'nodeAffinity': {}}),
            ('schedulerName', 'other-scheduler'),
            ('priorityClassName', 'system-node-critical'),
            ('runtimeClassName', 'other-runtime'),
        ):
            kube = FakeKubernetes(ready=True)
            def changed(value):
                reply = copy.deepcopy(value)
                reply['spec'][field] = injected
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(field=field):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 1)

    def test_pod_success_does_not_accept_container_execution_injection(self):
        for field, injected in (
            ('args', ['unexpected-command']),
            ('workingDir', '/host'),
            ('lifecycle', {'postStart': {'exec': {'command': ['/bin/sh', '-c', 'exit 1']}}}),
            ('stdin', True),
            ('tty', True),
        ):
            kube = FakeKubernetes(ready=True)
            def changed(value):
                reply = copy.deepcopy(value)
                if reply['kind'] == 'Pod':
                    reply['spec']['containers'][0][field] = injected
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(field=field):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 1)

        for field, injected in (
            ('env', [{'name': 'INJECTED', 'value': '1'}]),
            ('envFrom', [{'secretRef': {'name': 'other-secret'}}]),
        ):
            kube = FakeKubernetes(ready=True)
            def changed(value):
                reply = copy.deepcopy(value)
                if reply['kind'] == 'Pod':
                    reply['spec']['containers'][0][field] = injected
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(field=field):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 1)

    def test_pod_success_does_not_accept_extra_ports_or_security_controls(self):
        for mutate in (
            lambda pod: pod['spec']['containers'][0].update(ports=[{'containerPort': 8080}]),
            lambda pod: pod['spec']['containers'][0]['securityContext'].update(runAsUser=0),
            lambda pod: pod['spec']['containers'][0]['securityContext'].update(
                seccompProfile={'type': 'Unconfined'}),
        ):
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

    def test_pod_success_does_not_add_container_probes(self):
        for field in ('livenessProbe', 'readinessProbe', 'startupProbe'):
            kube = FakeKubernetes(ready=True)
            def changed(value):
                reply = copy.deepcopy(value)
                if reply['kind'] == 'Pod':
                    reply['spec']['containers'][0][field] = {
                        'exec': {'command': ['/bin/true']}, 'periodSeconds': 1}
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(field=field):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 1)

    def test_pod_success_does_not_add_container_restart_or_resize_controls(self):
        for field, injected in (
            ('restartPolicy', 'Always'),
            ('resizePolicy', [{'resourceName': 'cpu', 'restartPolicy': 'RestartContainer'}]),
        ):
            kube = FakeKubernetes(ready=True)
            def changed(value):
                reply = copy.deepcopy(value)
                if reply['kind'] == 'Pod':
                    reply['spec']['containers'][0][field] = injected
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(field=field):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 1)

        submitted = policy.smoke_cases('example.test/image@sha256:' + 'a' * 64)[0]['object']
        for defaults in ({}, {'resizePolicy': []}):
            reply = copy.deepcopy(submitted)
            reply['spec']['containers'][0].update(defaults)
            self.assertTrue(policy.smoke_success_scope(reply, submitted))

    def test_pod_success_does_not_change_preemption_policy(self):
        for injected in ('Never', False, None):
            kube = FakeKubernetes(ready=True)
            def changed(value):
                reply = copy.deepcopy(value)
                if reply['kind'] == 'Pod':
                    reply['spec']['preemptionPolicy'] = injected
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(injected=injected):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 1)

        submitted = policy.smoke_cases('example.test/image@sha256:' + 'a' * 64)[0]['object']
        for default in ({}, {'preemptionPolicy': 'PreemptLowerPriority'}):
            reply = copy.deepcopy(submitted)
            reply['spec'].update(default)
            self.assertTrue(policy.smoke_success_scope(reply, submitted))

    def test_pod_success_does_not_add_resource_claims_or_scheduling_gates(self):
        for field, injected in (
            ('resourceClaims', [{'name': 'device', 'resourceClaimName': 'device-claim'}]),
            ('schedulingGates', [{'name': 'example.test/hold'}]),
        ):
            kube = FakeKubernetes(ready=True)
            def changed(value):
                reply = copy.deepcopy(value)
                if reply['kind'] == 'Pod':
                    reply['spec'][field] = injected
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(field=field):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 1)

        for defaults in ({}, {'resourceClaims': [], 'schedulingGates': []}):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def defaulted(value):
                original = reference.smoke_request(value)
                if original.returncode != 0:
                    return original
                reply = copy.deepcopy(value)
                if reply['kind'] == 'Pod':
                    reply['spec'].update(defaults)
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = defaulted
            with self.subTest(defaults=defaults):
                self.assertEqual(policy.smoke(kube, items())['passed_cases'], 15)

    def test_pod_success_does_not_redirect_termination_messages(self):
        for field, injected in (
            ('terminationMessagePath', '/var/log/other'),
            ('terminationMessagePolicy', 'FallbackToLogsOnError'),
        ):
            kube = FakeKubernetes(ready=True)
            def changed(value):
                reply = copy.deepcopy(value)
                if reply['kind'] == 'Pod':
                    reply['spec']['containers'][0][field] = injected
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(field=field):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 1)

        for defaults in ({}, {'terminationMessagePath': '/dev/termination-log',
                               'terminationMessagePolicy': 'File'}):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def defaulted(value):
                original = reference.smoke_request(value)
                if original.returncode != 0:
                    return original
                reply = copy.deepcopy(value)
                if reply['kind'] == 'Pod':
                    reply['spec']['containers'][0].update(defaults)
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = defaulted
            with self.subTest(defaults=defaults):
                self.assertEqual(policy.smoke(kube, items())['passed_cases'], 15)

    def test_pod_success_does_not_add_container_stop_signal(self):
        # An API response must not silently change how the fixed container exits.
        for injected in ('SIGKILL', 'SIGTERM', 9):
            kube = FakeKubernetes(ready=True)
            def changed(value):
                reply = copy.deepcopy(value)
                if reply['kind'] == 'Pod':
                    reply['spec']['containers'][0]['stopSignal'] = injected
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(injected=injected):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 1)

        submitted = policy.smoke_cases('example.test/image@sha256:' + 'a' * 64)[0]['object']
        reply = copy.deepcopy(submitted)
        reply['spec']['containers'][0]['stopSignal'] = None
        self.assertTrue(policy.smoke_success_scope(reply, submitted))

    def test_pod_port_comparison_preserves_submitted_routing(self):
        submitted = policy.smoke_cases('example.test/image@sha256:' + 'a' * 64)[0]['object']
        submitted['spec']['containers'][0]['ports'] = [
            {'containerPort': 8080, 'protocol': 'TCP'}]
        reply = copy.deepcopy(submitted)
        self.assertTrue(policy.smoke_success_scope(reply, submitted))
        reply['spec']['containers'][0]['ports'][0]['hostPort'] = 0
        self.assertTrue(policy.smoke_success_scope(reply, submitted))
        for field, injected in (('hostPort', False), ('containerPort', 9090),
                                ('protocol', 'UDP'), ('hostIP', '0.0.0.0')):
            changed = copy.deepcopy(reply)
            changed['spec']['containers'][0]['ports'][0][field] = injected
            with self.subTest(field=field):
                self.assertFalse(policy.smoke_success_scope(changed, submitted))

    def test_pod_port_comparison_preserves_explicit_host_binding(self):
        submitted = policy.smoke_cases('example.test/image@sha256:' + 'a' * 64)[0]['object']
        port = {'containerPort': 8080, 'hostPort': 18080, 'hostIP': '127.0.0.1'}
        submitted['spec']['containers'][0]['ports'] = [port]
        reply = copy.deepcopy(submitted)
        self.assertTrue(policy.smoke_success_scope(reply, submitted))
        for field, injected in (('hostPort', 0), ('hostPort', False),
                                ('hostIP', ''), ('hostIP', '0.0.0.0')):
            changed = copy.deepcopy(reply)
            changed['spec']['containers'][0]['ports'][0][field] = injected
            with self.subTest(field=field, injected=injected):
                self.assertFalse(policy.smoke_success_scope(changed, submitted))

    def test_pod_success_does_not_change_lifetime_or_priority(self):
        for field, injected in (('terminationGracePeriodSeconds', 0),
                                ('terminationGracePeriodSeconds', 3600),
                                ('priority', 100000)):
            kube = FakeKubernetes(ready=True)
            def changed(value):
                reply = copy.deepcopy(value)
                if reply['kind'] == 'Pod':
                    reply['spec'][field] = injected
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(field=field, injected=injected):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items())
                self.assertEqual(len(kube.calls), 1)

        for defaults in ({}, {'terminationGracePeriodSeconds': 30, 'priority': 0}):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def defaulted(value):
                original = reference.smoke_request(value)
                if original.returncode != 0:
                    return original
                reply = copy.deepcopy(value)
                if reply['kind'] == 'Pod':
                    reply['spec'].update(defaults)
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = defaulted
            with self.subTest(defaults=defaults):
                self.assertEqual(policy.smoke(kube, items())['passed_cases'], 15)

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

    def test_nontext_denial_is_a_recorded_smoke_failure(self):
        name = policy.NAMES[0]
        message = 'Privileged containers are forbidden.'
        self.assertFalse(policy.own_denial(None, name, message))
        self.assertFalse(policy.own_denial(b'denied', name, message))

        kube = FakeKubernetes(ready=True)
        reference = FakeKubernetes(ready=True)
        def nontext(request):
            response = reference.smoke_request(request)
            if response.returncode != 0:
                return SimpleNamespace(returncode=1, stdout='', stderr=None)
            return response
        kube.smoke_failure = nontext
        events = []
        with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
            policy.smoke(kube, items(), record=events.append)
        self.assertEqual(len(kube.calls), 3)
        self.assertEqual(events[-1]['result'], 'failed')

    def test_signalled_dry_run_is_not_a_verified_denial(self):
        for exit_code in (-9, None, True):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def interrupted(request):
                response = reference.smoke_request(request)
                if response.returncode != 0:
                    return SimpleNamespace(returncode=exit_code, stdout='', stderr=response.stderr)
                return response
            kube.smoke_failure = interrupted
            events = []
            with self.subTest(exit_code=exit_code):
                with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                    policy.smoke(kube, items(), record=events.append)
                self.assertEqual(len(kube.calls), 3)
                self.assertEqual(events[-1]['result'], 'failed')

    def test_boolean_success_status_is_not_an_allowed_dry_run(self):
        kube = FakeKubernetes(ready=True)
        reference = FakeKubernetes(ready=True)
        def malformed(request):
            response = reference.smoke_request(request)
            return SimpleNamespace(returncode=False, stdout=response.stdout, stderr=response.stderr)
        kube.smoke_failure = malformed
        events = []
        with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
            policy.smoke(kube, items(), record=events.append)
        self.assertEqual(len(kube.calls), 1)
        self.assertEqual(events[-1]['result'], 'failed')

    def test_denial_with_additional_error_lines_is_not_a_policy_pass(self):
        name = policy.NAMES[0]
        message = 'Privileged containers are forbidden.'
        denial = f"ValidatingAdmissionPolicy '{name}' with binding '{name}' denied request: {message}"
        for stderr in (f'Forbidden: unrelated failure\n{denial}',
                       f'{denial}\nForbidden: unrelated failure',
                       f'{denial}\n{denial}'):
            with self.subTest(stderr=stderr):
                self.assertFalse(policy.own_denial(stderr, name, message))
        self.assertTrue(policy.own_denial(f'Error from server (Forbidden): {denial}\n', name, message))

        kube = FakeKubernetes(ready=True)
        reference = FakeKubernetes(ready=True)
        def mixed(value):
            result = reference.smoke_request(value)
            if result.returncode != 0:
                return SimpleNamespace(returncode=1, stdout='',
                                       stderr=result.stderr + '\nForbidden: unrelated failure')
            return result
        kube.smoke_failure = mixed
        events = []
        with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
            policy.smoke(kube, items(), record=events.append)
        self.assertEqual(len(kube.calls), 3)
        self.assertEqual(events[-1]['result'], 'failed')

    def test_denial_with_unrelated_single_line_prefix_is_not_a_policy_pass(self):
        name = policy.NAMES[0]
        message = 'Privileged containers are forbidden.'
        denial = f"ValidatingAdmissionPolicy '{name}' with binding '{name}' denied request: {message}"
        for stderr in (f'RBAC denied this request; {denial}',
                       f'Error from server (BadRequest): {denial}',
                       f'warning: admission was bypassed; {denial}'):
            with self.subTest(stderr=stderr):
                self.assertFalse(policy.own_denial(stderr, name, message))
        self.assertTrue(policy.own_denial(denial, name, message))
        self.assertTrue(policy.own_denial(f'Error from server (Forbidden): {denial}\n', name, message))

    def test_denial_requires_balanced_policy_and_binding_quotes(self):
        name = policy.NAMES[0]
        message = 'Privileged containers are forbidden.'
        for opening, closing in (("'", '"'), ('"', "'")):
            for location in ('policy', 'binding'):
                policy_quote = (opening, closing) if location == 'policy' else ("'", "'")
                binding_quote = (opening, closing) if location == 'binding' else ("'", "'")
                denial = (f'ValidatingAdmissionPolicy {policy_quote[0]}{name}{policy_quote[1]} '
                          f'with binding {binding_quote[0]}{name}{binding_quote[1]} '
                          f'denied request: {message}')
                with self.subTest(location=location, opening=opening):
                    self.assertFalse(policy.own_denial(denial, name, message))
        for quote in ("'", '"'):
            denial = (f'ValidatingAdmissionPolicy {quote}{name}{quote} with binding '
                      f'{quote}{name}{quote} denied request: {message}')
            self.assertTrue(policy.own_denial(denial, name, message))

        kube = FakeKubernetes(ready=True)
        reference = FakeKubernetes(ready=True)
        def malformed(request):
            response = reference.smoke_request(request)
            if response.returncode == 0:
                return response
            return SimpleNamespace(returncode=response.returncode, stdout='',
                                   stderr=response.stderr.replace(f"'{name}'", f"'{name}\"", 1))
        kube.smoke_failure = malformed
        events = []
        with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
            policy.smoke(kube, items(), record=events.append)
        self.assertEqual(len(kube.calls), 3)
        self.assertEqual(events[-1]['result'], 'failed')

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

    def test_success_cannot_claim_a_persisted_dry_run_object(self):
        for mutation in (
            lambda reply: reply['metadata'].update(uid='unexpected-uid'),
            lambda reply: reply['metadata'].update(resourceVersion='42'),
            lambda reply: reply['metadata'].update(generation=1),
            lambda reply: reply['metadata'].update(creationTimestamp='2026-10-04T00:00:00Z'),
            lambda reply: reply['metadata'].update(ownerReferences=[{'uid': 'unexpected-uid'}]),
            lambda reply: reply.update(status={'phase': 'Running'}),
        ):
            kube = FakeKubernetes(ready=True)
            reference = FakeKubernetes(ready=True)
            def changed(value):
                original = reference.smoke_request(value)
                if original.returncode != 0:
                    return original
                reply = copy.deepcopy(value)
                mutation(reply)
                return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
            kube.smoke_failure = changed
            with self.subTest(mutation=mutation), self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
                policy.smoke(kube, items())
            self.assertEqual(len(kube.calls), 1)

    def test_success_allows_empty_dry_run_identity_defaults(self):
        kube = FakeKubernetes(ready=True)
        reference = FakeKubernetes(ready=True)
        def defaulted(value):
            original = reference.smoke_request(value)
            if original.returncode != 0:
                return original
            reply = copy.deepcopy(value)
            reply['metadata'].update(uid='', resourceVersion='', generation=0,
                                     creationTimestamp=None, managedFields=[])
            reply['status'] = {}
            return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
        kube.smoke_failure = defaulted
        self.assertEqual(policy.smoke(kube, items())['passed_cases'], 15)

    def test_service_dry_run_identity_is_checked_too(self):
        kube = FakeKubernetes(ready=True)
        reference = FakeKubernetes(ready=True)
        def persisted_service(value):
            original = reference.smoke_request(value)
            if original.returncode != 0 or value['kind'] != 'Service':
                return original
            reply = copy.deepcopy(value)
            reply['metadata']['resourceVersion'] = '42'
            return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')
        kube.smoke_failure = persisted_service
        with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
            policy.smoke(kube, items())
        self.assertEqual(len(kube.calls), 2)

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
