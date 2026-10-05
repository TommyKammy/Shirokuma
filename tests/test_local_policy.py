"""Scoped native admission bridge tests; no cluster requests or mutations."""
import copy
import hashlib
import importlib.util
import json
import os
import stat
from pathlib import Path
import tempfile
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
    def test_cli_uses_certified_items_for_digest_and_kubernetes_allowlist(self):
        certified = items()
        digest = certified[0]['metadata']['annotations']['shirokuma.dev/source-sha256']
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(policy, 'STATE', Path(directory)), \
                mock.patch.object(policy, 'load_manifest_snapshot', return_value=(certified, 'b' * 64)) as load, \
                mock.patch.object(policy, 'Kubernetes') as kube, \
                mock.patch.object(policy, 'inventory_status', return_value={'ready': True}), \
                mock.patch.object(policy, 'SOURCE') as source, \
                mock.patch('sys.argv', ['manage.py', 'status']), \
                mock.patch('builtins.print') as output:
            self.assertEqual(policy.main(), 0)
        load.assert_called_once_with()
        kube.assert_called_once_with(certified)
        source.read_bytes.assert_not_called()
        self.assertEqual(json.loads(output.call_args.args[0])['source_sha256'], digest)

    def test_kubernetes_allowlist_does_not_reload_when_given_certified_items(self):
        certified = items()
        with mock.patch.object(policy, 'load_manifest', side_effect=AssertionError('second manifest read')):
            kube = policy.Kubernetes(certified)
        self.assertEqual(len(kube.expected), len(certified))
        self.assertEqual(set(kube.expected), {(item['kind'], item['metadata']['name']) for item in certified})

    def test_kubernetes_allowlist_owns_certified_items_after_construction(self):
        certified = items()
        original = copy.deepcopy(certified[0])
        kube = policy.Kubernetes(certified)
        certified[0]['spec']['failurePolicy'] = 'Ignore'
        certified[0]['metadata']['annotations']['shirokuma.dev/source-sha256'] = '0' * 64
        key = (original['kind'], original['metadata']['name'])
        self.assertEqual(kube.expected[key], original)
        with self.assertRaisesRegex(policy.PolicyError, 'outside the dedicated inventory'):
            kube.create(certified[0], dry_run=True)
        with mock.patch.object(kube, 'json_request', return_value={}) as request:
            kube.create(original, dry_run=True)
        self.assertEqual(request.call_args.args[1], original)

    def test_kubernetes_allowlist_refuses_incomplete_or_duplicate_inventory(self):
        certified = items()
        for altered in (certified[:-1], certified[:-1] + [copy.deepcopy(certified[0])],
                        certified[:-1] + [dict(certified[-1], kind='Pod')],
                        certified[:-1] + [{'kind': policy.POLICY, 'metadata': {'name': []}}]):
            with self.subTest(last=altered[-1]), mock.patch.object(
                    policy, 'load_manifest', side_effect=AssertionError('second read')):
                with self.assertRaises(policy.PolicyError):
                    policy.Kubernetes(altered)
        with mock.patch.object(policy, 'load_manifest', side_effect=AssertionError('second read')):
            self.assertEqual(len(policy.Kubernetes(certified).expected), 16)

    def test_kubernetes_create_refuses_json_type_aliases_and_malformed_identity(self):
        certified = items()
        # A certified snapshot may contain JSON boolean/number fields even if
        # today's generated inventory happens to contain only string fields.
        certified[0]['spec']['testBoundary'] = {'enabled': False, 'count': 1}
        kube = policy.Kubernetes(certified)
        original = copy.deepcopy(certified[0])
        with mock.patch.object(kube, 'json_request', side_effect=AssertionError('request')):
            for malformed in ([], {}, False, None):
                changed = copy.deepcopy(original)
                changed['metadata']['name'] = malformed
                with self.subTest(identity=malformed), self.assertRaises(policy.PolicyError):
                    kube.create(changed, dry_run=True)
            changed = copy.deepcopy(original)
            # JSON false and the number zero are distinct, even though Python
            # compares them as equal in nested dictionaries.
            changed['spec']['testBoundary']['enabled'] = 0
            self.assertEqual(changed, original)
            with self.assertRaises(policy.PolicyError):
                kube.create(changed, dry_run=True)
            changed = copy.deepcopy(original)
            changed['spec']['testBoundary']['count'] = True
            self.assertEqual(changed, original)
            with self.assertRaises(policy.PolicyError):
                kube.create(changed, dry_run=True)

    def test_binding_delete_refuses_malformed_expected_identity_without_request(self):
        kube = policy.Kubernetes(items())
        binding = next(item for item in items() if item['kind'] == policy.BINDING)
        with mock.patch.object(kube, 'json_request', side_effect=AssertionError('request')):
            for malformed in ([], {}, False, None):
                changed = copy.deepcopy(binding)
                changed['metadata']['name'] = malformed
                with self.subTest(identity=malformed), self.assertRaises(policy.PolicyError):
                    kube.delete_binding({}, changed)

    def test_default_namespace_smoke_is_only_the_fixed_mutable_image_control(self):
        kube = policy.Kubernetes(items())
        control = policy.smoke_cases(IMAGE)[-1]['object']
        with mock.patch.object(kube, 'request', return_value='dry-run') as request:
            self.assertEqual(kube.smoke_request(control), 'dry-run')
            request.assert_called_once()
            for field, changed in (
                ('privileged', lambda x: x['spec']['containers'][0]['securityContext'].update(privileged=True)),
                ('extra container', lambda x: x['spec']['containers'].append(copy.deepcopy(x['spec']['containers'][0]))),
                ('host network', lambda x: x['spec'].update(hostNetwork=True)),
                ('extra metadata', lambda x: x['metadata'].update(annotations={'unexpected': 'claim'})),
                ('wrong image', lambda x: x['spec']['containers'][0].update(image=IMAGE)),
                ('boolean alias', lambda x: x['spec'].update(automountServiceAccountToken=0)),
            ):
                altered = copy.deepcopy(control)
                changed(altered)
                with self.subTest(field=field), self.assertRaises(policy.PolicyError):
                    kube.smoke_request(altered)
            self.assertEqual(request.call_count, 1)

    def test_protected_namespace_smoke_is_only_the_fixed_corpus(self):
        kube = policy.Kubernetes(items())
        corpus = policy.smoke_cases(IMAGE)[:-1]
        with mock.patch.object(kube, 'request', return_value='dry-run') as request:
            for case in corpus:
                self.assertEqual(kube.smoke_request(case['object']), 'dry-run')
            accepted = request.call_count
            for changed in (
                    lambda x: x['spec'].update(hostNetwork='yes'),
                    lambda x: x['metadata'].update(annotations={'extra': 'value'}),
                    lambda x: x['spec']['containers'][0].update(image='other/image@sha256:' + 'a' * 64),
                    lambda x: x['spec'].update(automountServiceAccountToken=0),
                    lambda x: x['spec']['containers'].append(copy.deepcopy(x['spec']['containers'][0]))):
                altered = copy.deepcopy(corpus[0]['object'])
                changed(altered)
                with self.assertRaises(policy.PolicyError):
                    kube.smoke_request(altered)
            for kind, change in (
                    ('Service', lambda x: x['spec'].update(type='ExternalName')),
                    ('Ingress', lambda x: x['spec'].update(ingressClassName='other'))):
                altered = copy.deepcopy(next(case['object'] for case in corpus
                                             if case['object']['kind'] == kind))
                change(altered)
                with self.subTest(kind=kind), self.assertRaises(policy.PolicyError):
                    kube.smoke_request(altered)
            self.assertEqual(request.call_count, accepted)

    def test_smoke_scope_refuses_unhashable_identity_without_request(self):
        kube = policy.Kubernetes(items())
        valid = policy.smoke_cases(IMAGE)[0]['object']
        with mock.patch.object(kube, 'request', side_effect=AssertionError('request')):
            for field in ('kind', 'namespace'):
                for malformed in ([], {}, None, False):
                    value = copy.deepcopy(valid)
                    target = value if field == 'kind' else value['metadata']
                    target[field] = malformed
                    with self.subTest(field=field, malformed=malformed), self.assertRaises(policy.PolicyError):
                        kube.smoke_request(value)

    def test_local_source_decoder_requires_document_separators(self):
        source = [{'kind': 'ValidatingPolicy', 'metadata': {'name': name},
                   'spec': {}} for name in policy.BASE_NAMES]
        encoded = [json.dumps(item) for item in source]
        for separator in ('', ',', '\u00a0', '\u2028', '\u2003'):
            reply = SimpleNamespace(stdout=separator.join(encoded), stderr='', returncode=0)
            with self.subTest(separator=separator), mock.patch.object(
                    policy.subprocess, 'run', return_value=reply):
                with self.assertRaises(policy.PolicyError):
                    policy.source_policies()
        reply = SimpleNamespace(stdout='\n'.join(encoded), stderr='', returncode=0)
        with mock.patch.object(policy.subprocess, 'run', return_value=reply):
            self.assertEqual(len(policy.source_policies()), len(source))
        reply = SimpleNamespace(stdout=' \t\r\n'.join(encoded), stderr='', returncode=0)
        with mock.patch.object(policy.subprocess, 'run', return_value=reply):
            self.assertEqual(len(policy.source_policies()), len(source))
        for edge in ('\u00a0' + '\n'.join(encoded), '\n'.join(encoded) + '\u2028'):
            reply = SimpleNamespace(stdout=edge, stderr='', returncode=0)
            with mock.patch.object(policy.subprocess, 'run', return_value=reply):
                with self.assertRaises(policy.PolicyError):
                    policy.source_policies()

    def test_local_source_decoder_refuses_escaped_lone_surrogates(self):
        source = [{'kind': 'ValidatingPolicy', 'metadata': {'name': name},
                   'spec': {}} for name in policy.BASE_NAMES]
        encoded = [json.dumps(item) for item in source]
        for escaped in ('\\ud800', '\\udc00'):
            changed = encoded.copy()
            changed[0] = changed[0].replace('"spec": {}',
                                             '"spec": {"' + escaped + '": "value"}')
            reply = SimpleNamespace(stdout='\n'.join(changed), stderr='', returncode=0)
            with self.subTest(escaped=escaped), mock.patch.object(
                    policy.subprocess, 'run', return_value=reply):
                with self.assertRaises(policy.PolicyError):
                    policy.source_policies()
        changed = encoded.copy()
        changed[0] = changed[0].replace('"spec": {}',
                                         '"spec": {"\\ud83d\\ude00": "value"}')
        reply = SimpleNamespace(stdout='\n'.join(changed), stderr='', returncode=0)
        with mock.patch.object(policy.subprocess, 'run', return_value=reply):
            self.assertEqual(policy.source_policies()[0]['spec'], {'😀': 'value'})

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
        mutated = policy.render_native(source, hashlib.sha256(policy.source_snapshot()[0]).hexdigest())
        mutated['items'][0]['spec']['matchConstraints']['namespaceSelector'] = {}
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(policy, 'source_policies', return_value=source):
            manifest = Path(directory) / 'manifest.json'
            manifest.write_text(json.dumps(mutated))
            with mock.patch.object(policy, 'MANIFEST', manifest):
                with self.assertRaisesRegex(policy.PolicyError, 'exact repository'):
                    policy.load_manifest()

    def test_renderer_requires_explicit_checked_digest_without_reopening_source(self):
        source = policy.source_policies()
        with mock.patch.object(policy.Path, 'read_bytes', side_effect=AssertionError('path reopened')):
            with self.assertRaises(TypeError):
                policy.render_native(source)
            for invalid in (None, '', '0' * 63, 'G' * 64):
                with self.subTest(invalid=invalid), self.assertRaises(policy.PolicyError):
                    policy.render_native(source, invalid)
            rendered = policy.render_native(source, 'a' * 64)
        self.assertTrue(all(item['metadata']['annotations']['shirokuma.dev/source-sha256'] == 'a' * 64
                            for item in rendered['items']))

    def test_manifest_cannot_hide_duplicate_equal_keys(self):
        source = policy.source_policies()
        original = policy.MANIFEST.read_text()
        # The ordinary JSON reader overwrites the first value with the second;
        # structural equality alone must not certify an ambiguous file.
        for altered in (original.replace('"kind": "List"', '"kind": "List", "kind": "List"', 1),
                        original.replace('"kind": "List"', '"kind": "List", "k\\u0069nd": "List"', 1)):
            with self.subTest(altered=altered[:100]):
                self.assertNotEqual(altered, original)
                self.assertEqual(json.loads(altered), json.loads(original))
                with tempfile.TemporaryDirectory() as directory:
                    manifest = Path(directory) / 'manifest.json'
                    manifest.write_text(altered)
                    with mock.patch.object(policy, 'MANIFEST', manifest), \
                         mock.patch.object(policy, 'source_policies', return_value=source):
                         with self.assertRaises(policy.PolicyError):
                              policy.load_manifest()

    def test_manifest_refuses_boolean_number_alias_in_projection(self):
        original = policy.render_native
        source_bytes, _ = policy.source_snapshot()
        source = policy.source_policies(source_bytes)
        digest = hashlib.sha256(source_bytes).hexdigest()
        expected = original(source, digest)
        expected['items'][0]['spec']['syntheticBoundary'] = {'enabled': False, 'count': 1}
        for field, replacement in (('enabled', 0), ('count', True)):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                altered = copy.deepcopy(expected)
                altered['items'][0]['spec']['syntheticBoundary'][field] = replacement
                self.assertEqual(altered, expected)  # Python equality aliases bool and number.
                manifest = Path(directory) / 'manifest.json'
                manifest.write_text(json.dumps(altered))
                with mock.patch.object(policy, 'MANIFEST', manifest), \
                     mock.patch.object(policy, 'render_native', return_value=expected):
                    with self.assertRaisesRegex(policy.PolicyError, 'differs from'):
                        policy.load_manifest_snapshot()

    def test_manifest_rejects_non_json_and_nonrepresentable_numbers_before_source(self):
        original = policy.MANIFEST.read_text()
        for altered in (original.replace('"kind": "List"', '"kind": NaN', 1),
                        original.replace('"kind": "List"', '"kind": Infinity', 1),
                        original.replace('"kind": "List"', '"kind": 1e9999', 1),
                        original.replace('"kind": "List"', '"kind": 1e-9999', 1)):
            with self.subTest(value=altered[0:70]), tempfile.TemporaryDirectory() as directory:
                manifest = Path(directory) / 'manifest.json'
                manifest.write_text(altered)
                with mock.patch.object(policy, 'MANIFEST', manifest), \
                     mock.patch.object(policy, 'source_policies', side_effect=AssertionError('source reached')):
                    with self.assertRaises(policy.PolicyError):
                        policy.load_manifest()

    def test_manifest_digest_tracks_validated_bytes_not_later_path(self):
        source = policy.source_policies()
        original = policy.MANIFEST.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'manifest.json'
            manifest.write_bytes(original)

            def replace_after_read(_source_bytes):
                manifest.write_bytes(b'{}')
                return source

            with mock.patch.object(policy, 'MANIFEST', manifest), \
                 mock.patch.object(policy, 'source_policies', side_effect=replace_after_read):
                values, digest = policy.load_manifest_snapshot()
            self.assertEqual(len(values), 16)
            self.assertEqual(digest, hashlib.sha256(original).hexdigest())
            self.assertNotEqual(digest, hashlib.sha256(manifest.read_bytes()).hexdigest())

    def test_manifest_refuses_source_changed_during_local_decode(self):
        original = policy.SOURCE.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.yaml'
            source.write_bytes(original)

            decoded = policy.source_policies()
            with mock.patch.object(policy, 'SOURCE', source), \
                  mock.patch.object(policy, 'source_policies', side_effect=lambda _source_bytes: (
                     source.write_bytes(b'different source'), decoded)[1]):
                with self.assertRaisesRegex(policy.PolicyError, 'changed during projection'):
                    policy.load_manifest_snapshot()

    def test_local_decoder_receives_checked_source_bytes_not_a_path(self):
        original = policy.SOURCE.read_bytes()
        decoded = policy.source_policies()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.yaml'
            source.write_bytes(original)
            seen = []

            def decode(argv, **kwargs):
                seen.append((argv, kwargs['input']))
                # A pathname-based second read would see this new content.
                source.write_bytes(b'different source')
                return SimpleNamespace(stdout='\n'.join(json.dumps(item) for item in decoded),
                                       stderr='', returncode=0)

            with mock.patch.object(policy, 'SOURCE', source), \
                 mock.patch.object(policy.subprocess, 'run', side_effect=decode):
                with self.assertRaisesRegex(policy.PolicyError, 'changed during projection'):
                    policy.load_manifest_snapshot()
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0][0][-4:], ['-f', '-', '-o', 'json'])
            self.assertNotIn(str(source), seen[0][0])
            self.assertEqual(seen[0][1].encode('utf-8'), original)

    def test_manifest_refuses_same_bytes_source_replacement_during_decode(self):
        original = policy.SOURCE.read_bytes()
        decoded = policy.source_policies()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, replacement = root / 'source.yaml', root / 'replacement.yaml'
            source.write_bytes(original)
            replacement.write_bytes(original)

            def replace_after_first_read(_source_bytes):
                os.replace(replacement, source)
                return decoded

            with mock.patch.object(policy, 'SOURCE', source), \
                 mock.patch.object(policy, 'source_policies', side_effect=replace_after_first_read):
                with self.assertRaisesRegex(policy.PolicyError, 'changed during projection'):
                    policy.load_manifest_snapshot()

    def test_manifest_refuses_unbounded_or_linked_source_before_decode(self):
        original = policy.SOURCE.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.yaml'
            target = root / 'target.yaml'
            target.write_bytes(original)
            source.symlink_to(target)
            for replacement in ('symlink', 'fifo', 'oversize'):
                with self.subTest(replacement=replacement):
                    if replacement != 'symlink':
                        source.unlink()
                        if replacement == 'fifo':
                            os.mkfifo(source)
                        else:
                            source.write_bytes(b'x' * (256 * 1024 + 1))
                    with mock.patch.object(policy, 'SOURCE', source), \
                         mock.patch.object(policy, 'source_policies', side_effect=AssertionError('decoder reached')):
                        with self.assertRaises(policy.PolicyError):
                            policy.load_manifest_snapshot()
            source.write_bytes(original)
            with mock.patch.object(policy, 'SOURCE', source):
                self.assertEqual(policy.source_snapshot()[0], original)

    def test_source_refuses_linked_ancestor_and_post_read_relink(self):
        original = policy.SOURCE.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / 'real'
            real.mkdir()
            (real / 'source.yaml').write_bytes(original)
            link = root / 'link'
            link.symlink_to(real, target_is_directory=True)
            with mock.patch.object(policy, 'SOURCE', link / 'source.yaml'):
                with self.assertRaises(policy.PolicyError):
                    policy.source_snapshot()

            parent = root / 'parent'
            parent.mkdir()
            (parent / 'source.yaml').write_bytes(original)
            moved = root / 'moved'
            read = policy.os.read

            def relink(fd, size):
                raw = read(fd, size)
                parent.rename(moved)
                parent.symlink_to(moved, target_is_directory=True)
                return raw

            with mock.patch.object(policy, 'SOURCE', parent / 'source.yaml'), \
                 mock.patch.object(policy.os, 'read', side_effect=relink):
                with self.assertRaisesRegex(policy.PolicyError, 'ancestor changed'):
                    policy.source_snapshot()

    def test_source_refuses_in_place_change_after_descriptor_check(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.yaml'
            source.write_bytes(policy.SOURCE.read_bytes())
            real_fstat = os.fstat
            file_checks = 0

            def change_after_check(fd):
                nonlocal file_checks
                result = real_fstat(fd)
                if stat.S_ISREG(result.st_mode):
                    file_checks += 1
                    if file_checks == 2:
                        source.write_bytes(b'changed source')
                        return result
                return result

            with mock.patch.object(policy, 'SOURCE', source), \
                 mock.patch.object(policy.os, 'fstat', side_effect=change_after_check):
                with self.assertRaisesRegex(policy.PolicyError, 'changed during projection'):
                    policy.source_snapshot()
            self.assertEqual(file_checks, 2)

    def test_manifest_refuses_symlink_and_oversize_before_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'target.json'
            target.write_bytes(policy.MANIFEST.read_bytes())
            link = root / 'manifest.json'
            link.symlink_to(target)
            with mock.patch.object(policy, 'MANIFEST', link), \
                 mock.patch.object(policy, 'source_policies', side_effect=AssertionError('unexpected source read')):
                with self.assertRaises(policy.PolicyError):
                    policy.load_manifest()
            link.unlink()
            link.write_bytes(b' ' * (256 * 1024 + 1))
            with mock.patch.object(policy, 'MANIFEST', link), \
                 mock.patch.object(policy, 'source_policies', side_effect=AssertionError('unexpected source read')):
                with self.assertRaises(policy.PolicyError):
                    policy.load_manifest()

    def test_manifest_refuses_fifo_without_waiting_for_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'manifest.json'
            os.mkfifo(manifest)
            with mock.patch.object(policy, 'MANIFEST', manifest), \
                 mock.patch.object(policy, 'source_policies', side_effect=AssertionError('unexpected source read')):
                with self.assertRaises(policy.PolicyError):
                    policy.load_manifest()

    def test_manifest_refuses_post_read_symlink_to_same_inode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / 'manifest.json'
            moved = root / 'original.json'
            manifest.write_bytes(policy.MANIFEST.read_bytes())
            original_read = os.read
            changed = False

            def relink_after_open(fd, size):
                nonlocal changed
                raw = original_read(fd, size)
                if not changed:
                    changed = True
                    manifest.rename(moved)
                    manifest.symlink_to(moved)
                return raw

            with mock.patch.object(policy, 'MANIFEST', manifest), \
                 mock.patch.object(policy.os, 'read', side_effect=relink_after_open), \
                 mock.patch.object(policy, 'source_policies', side_effect=AssertionError('source reached')):
                with self.assertRaises(policy.PolicyError):
                    policy.load_manifest()
            self.assertTrue(changed)

    def test_manifest_refuses_in_place_change_after_descriptor_check(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'manifest.json'
            manifest.write_bytes(policy.MANIFEST.read_bytes())
            real_fstat = os.fstat
            file_checks = 0

            def change_after_check(fd):
                nonlocal file_checks
                result = real_fstat(fd)
                if stat.S_ISREG(result.st_mode):
                    file_checks += 1
                    if file_checks == 2:
                        manifest.write_bytes(b'{}')
                        return result
                return result

            with mock.patch.object(policy, 'MANIFEST', manifest), \
                 mock.patch.object(policy.os, 'fstat', side_effect=change_after_check), \
                 mock.patch.object(policy, 'source_policies', side_effect=AssertionError('source reached')):
                with self.assertRaisesRegex(policy.PolicyError, 'path changed'):
                    policy.load_manifest()
            self.assertEqual(file_checks, 2)

    def test_manifest_refuses_symlink_parent_before_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / 'real'
            real.mkdir()
            (real / 'manifest.json').write_bytes(policy.MANIFEST.read_bytes())
            (root / 'linked').symlink_to(real, target_is_directory=True)
            with mock.patch.object(policy, 'MANIFEST', root / 'linked' / 'manifest.json'), \
                 mock.patch.object(policy, 'source_policies', side_effect=AssertionError('unexpected source read')):
                with self.assertRaises(policy.PolicyError):
                    policy.load_manifest()

    def test_manifest_refuses_symlink_ancestor_before_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / 'real' / 'parent'
            real.mkdir(parents=True)
            (real / 'manifest.json').write_bytes(policy.MANIFEST.read_bytes())
            (root / 'linked').symlink_to(root / 'real', target_is_directory=True)
            with mock.patch.object(policy, 'MANIFEST', root / 'linked' / 'parent' / 'manifest.json'), \
                 mock.patch.object(policy, 'source_policies', side_effect=AssertionError('source reached')):
                with self.assertRaises(policy.PolicyError):
                    policy.load_manifest()

    def test_manifest_refuses_ancestor_relinked_during_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            slot = root / 'slot'
            parent = slot / 'parent'
            parent.mkdir(parents=True)
            (parent / 'manifest.json').write_bytes(policy.MANIFEST.read_bytes())
            real_read = os.read
            changed = False

            def relink_after_open(fd, size):
                nonlocal changed
                raw = real_read(fd, size)
                if not changed:
                    changed = True
                    slot.rename(root / 'original')
                    slot.symlink_to(root / 'original', target_is_directory=True)
                return raw

            # The final parent still has the same inode via the new symlink;
            # checking only that parent would certify the replaced path.
            with mock.patch.object(policy, 'MANIFEST', parent / 'manifest.json'), \
                 mock.patch.object(policy.os, 'read', side_effect=relink_after_open), \
                 mock.patch.object(policy, 'source_policies', side_effect=AssertionError('source reached')):
                with self.assertRaises(policy.PolicyError):
                    policy.load_manifest()
            self.assertTrue(changed)


class ActivationTests(unittest.TestCase):
    def test_ready_inventory_requires_persistent_object_identity(self):
        for kind in (policy.POLICY, policy.BINDING):
            for field, value in (('uid', None), ('uid', ''), ('resourceVersion', None),
                                 ('resourceVersion', 'not-a-version')):
                with self.subTest(kind=kind, field=field, value=value):
                    kube = FakeKubernetes(ready=True)
                    kube.objects[(kind, policy.NAMES[-1])]['metadata'][field] = value
                    with self.assertRaises(policy.PolicyError):
                        policy.inventory_status(kube, items())
                    with self.assertRaises(policy.PolicyError):
                        policy.apply_policies(kube, items())
                    self.assertEqual(kube.calls, [])

    def test_terminating_policy_or_binding_cannot_certify_ready_inventory(self):
        for kind in (policy.POLICY, policy.BINDING):
            with self.subTest(kind=kind):
                kube = FakeKubernetes(ready=True)
                key = (kind, policy.NAMES[-1])
                kube.objects[key]['metadata']['deletionTimestamp'] = '2026-10-06T00:00:00Z'
                with self.assertRaises(policy.PolicyError):
                    policy.inventory_status(kube, items())
                with self.assertRaises(policy.PolicyError):
                    policy.apply_policies(kube, items())
                self.assertEqual(kube.calls, [])

    def test_terminating_dry_run_reply_prevents_persistent_creation(self):
        kube = FakeKubernetes()
        original = kube.create
        def terminating(value, *, dry_run=False):
            reply = original(value, dry_run=dry_run)
            if dry_run and value['kind'] == policy.BINDING and value['metadata']['name'] == policy.NAMES[-1]:
                reply['metadata']['deletionTimestamp'] = '2026-10-06T00:00:00Z'
            return reply
        kube.create = terminating
        with self.assertRaises(policy.PolicyError):
            policy.apply_policies(kube, items())
        self.assertEqual(kube.objects, {})
        self.assertTrue(all(action == 'dry-run' for action, _, _ in kube.calls))

    def test_direct_status_refuses_incomplete_inventory_before_read(self):
        kube = FakeKubernetes(ready=True)
        for supplied in ([], items()[:-1], items() + [items()[0]]):
            with self.subTest(count=len(supplied)), self.assertRaises(policy.PolicyError):
                policy.inventory_status(kube, supplied)
            self.assertEqual(kube.calls, [])
        self.assertTrue(policy.inventory_status(kube, items())['ready'])

    def test_mismatched_dry_run_reply_prevents_any_persistent_creation(self):
        for mismatch in ('missing', 'foreign', 'spec'):
            with self.subTest(mismatch=mismatch):
                kube = FakeKubernetes()
                original = kube.create
                def corrupt(value, *, dry_run=False):
                    reply = original(value, dry_run=dry_run)
                    if dry_run and value['kind'] == policy.BINDING and value['metadata']['name'] == policy.NAMES[-1]:
                        if mismatch == 'missing':
                            return None
                        if mismatch == 'foreign':
                            reply['metadata']['labels']['shirokuma.dev/policy-owner'] = 'foreign'
                        else:
                            reply['spec']['policyName'] = 'other-policy'
                    return reply
                kube.create = corrupt
                with self.assertRaises(policy.PolicyError):
                    policy.apply_policies(kube, items())
                self.assertEqual(kube.objects, {})
                self.assertTrue(all(action == 'dry-run' for action, _, _ in kube.calls))

    def test_binding_change_during_dry_runs_prevents_policy_creation(self):
        kube = FakeKubernetes()
        original = kube.create
        target = items()[-1]

        def late_foreign(value, *, dry_run=False):
            reply = original(value, dry_run=dry_run)
            if dry_run and value['kind'] == policy.BINDING and value['metadata']['name'] == target['metadata']['name']:
                foreign = admitted(target)
                foreign['metadata']['labels']['shirokuma.dev/policy-owner'] = 'foreign'
                kube.objects[(target['kind'], target['metadata']['name'])] = foreign
            return reply

        kube.create = late_foreign
        with self.assertRaisesRegex(policy.PolicyError, 'refusing overwrite'):
            policy.apply_policies(kube, items())
        self.assertFalse(any(action == 'create' for action, _, _ in kube.calls))
        self.assertFalse(any(kind == policy.POLICY for kind, _ in kube.objects))

    def test_matching_binding_appearing_during_dry_runs_prevents_policy_creation(self):
        kube = FakeKubernetes()
        original = kube.create
        target = items()[-1]

        def late_owned(value, *, dry_run=False):
            reply = original(value, dry_run=dry_run)
            if dry_run and value['kind'] == target['kind'] and value['metadata']['name'] == target['metadata']['name']:
                kube.objects[(target['kind'], target['metadata']['name'])] = admitted(target)
            return reply

        kube.create = late_owned
        with self.assertRaisesRegex(policy.PolicyError, 'changed since activation preflight'):
            policy.apply_policies(kube, items())
        self.assertFalse(any(action == 'create' for action, _, _ in kube.calls))
        self.assertFalse(any(kind == policy.POLICY for kind, _ in kube.objects))

    def test_existing_policy_disappearing_during_dry_runs_prevents_creation(self):
        kube = FakeKubernetes()
        target = items()[0]
        key = (target['kind'], target['metadata']['name'])
        kube.objects[key] = admitted(target)
        original = kube.get
        reads = 0

        def disappearing(kind, name):
            nonlocal reads
            if (kind, name) == key:
                reads += 1
                if reads == 2:
                    kube.objects.pop(key)
            return original(kind, name)

        kube.get = disappearing
        with self.assertRaisesRegex(policy.PolicyError, 'changed since activation preflight'):
            policy.apply_policies(kube, items())
        self.assertFalse(any(action == 'create' for action, _, _ in kube.calls))

    def test_policy_presence_drift_after_final_scan_prevents_creation(self):
        target = items()[0]
        key = (target['kind'], target['metadata']['name'])
        for initially_present in (False, True):
            with self.subTest(initially_present=initially_present):
                kube = FakeKubernetes()
                if initially_present:
                    kube.objects[key] = admitted(target)
                original = kube.get
                reads = 0

                def changed(kind, name):
                    nonlocal reads
                    if (kind, name) == key:
                        reads += 1
                        if reads == 3:
                            if initially_present:
                                kube.objects.pop(key)
                            else:
                                kube.objects[key] = admitted(target)
                    return original(kind, name)

                kube.get = changed
                with self.assertRaisesRegex(policy.PolicyError, 'changed since activation preflight'):
                    policy.apply_policies(kube, items())
                self.assertFalse(any(action == 'create' for action, _, _ in kube.calls))

    def test_existing_binding_disappearing_after_final_scan_is_not_recreated(self):
        kube = FakeKubernetes(ready=True)
        key = (policy.BINDING, policy.NAMES[0])
        original = kube.get
        reads = 0

        def disappearing(kind, name):
            nonlocal reads
            if (kind, name) == key:
                reads += 1
                if reads == 3:
                    kube.objects.pop(key)
            return original(kind, name)

        kube.get = disappearing
        with self.assertRaisesRegex(policy.PolicyError, 'changed since activation preflight'):
            policy.apply_policies(kube, items())
        self.assertFalse(any(action == 'create' for action, _, _ in kube.calls))

    def test_partial_or_duplicate_activation_inventory_cannot_report_ready(self):
        baseline = items()
        for supplied in (baseline[:-1], baseline + [baseline[0]],
                         [x for x in baseline if x['kind'] == policy.POLICY]):
            with self.subTest(count=len(supplied)):
                kube = FakeKubernetes(ready=False)
                with self.assertRaises(policy.PolicyError):
                    policy.apply_policies(kube, supplied)
                self.assertEqual(kube.calls, [])

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

    def test_policy_replacement_during_typecheck_prevents_binding_activation(self):
        kube = FakeKubernetes(ready=True)
        for key in list(kube.objects):
            if key[0] == policy.BINDING:
                del kube.objects[key]
        original = kube.get
        target = (policy.POLICY, policy.NAMES[0])
        reads = 0

        def replaced(kind, name):
            nonlocal reads
            value = original(kind, name)
            if (kind, name) == target:
                reads += 1
                if reads >= 3:
                    value['metadata']['uid'] = 'replacement-uid'
            return value

        kube.get = replaced
        with self.assertRaisesRegex(policy.PolicyError, 'changed|identity'):
            policy.apply_policies(kube, items())
        self.assertFalse(any(call[0] == 'create' and call[1] == policy.BINDING
                              for call in kube.calls))

    def test_policy_drift_during_binding_scan_prevents_binding_creation(self):
        kube = FakeKubernetes(ready=True)
        for key in list(kube.objects):
            if key[0] == policy.BINDING:
                del kube.objects[key]
        original = kube.get
        changed = False
        binding_reads = 0
        target = (policy.POLICY, policy.NAMES[0])

        def drift(kind, name):
            nonlocal changed, binding_reads
            if kind == policy.BINDING:
                binding_reads += 1
                if binding_reads == 17:
                    changed = True
                    kube.objects[target]['metadata']['labels']['shirokuma.dev/policy-owner'] = 'foreign'
            return original(kind, name)

        kube.get = drift
        with self.assertRaises(policy.PolicyError):
            policy.apply_policies(kube, items())
        self.assertTrue(changed)
        self.assertFalse(any(call[0] == 'create' and call[1] == policy.BINDING
                             for call in kube.calls))

    def test_policy_typecheck_stale_at_binding_phase_prevents_binding_creation(self):
        kube = FakeKubernetes(ready=True)
        for key in list(kube.objects):
            if key[0] == policy.BINDING:
                del kube.objects[key]
        original = kube.get
        binding_reads = 0

        def stale(kind, name):
            nonlocal binding_reads
            if kind == policy.BINDING:
                binding_reads += 1
                if binding_reads == 17:
                    kube.objects[(policy.POLICY, policy.NAMES[0])]['metadata']['generation'] = 2
            return original(kind, name)

        kube.get = stale
        with self.assertRaisesRegex(policy.PolicyError, 'type checking changed'):
            policy.apply_policies(kube, items())
        self.assertFalse(any(call[0] == 'create' and call[1] == policy.BINDING
                              for call in kube.calls))

    def test_binding_appearing_during_policy_recheck_prevents_binding_creation(self):
        kube = FakeKubernetes(ready=True)
        target = next(x for x in items() if x['kind'] == policy.BINDING)
        key = (target['kind'], target['metadata']['name'])
        for binding in [key for key in kube.objects if key[0] == policy.BINDING]:
            del kube.objects[binding]
        original = kube.get
        policy_reads = 0

        def late_binding(kind, name):
            nonlocal policy_reads
            if kind == policy.POLICY:
                policy_reads += 1
                # The final policy pass follows the binding inventory pass.
                if policy_reads == 33:
                    kube.objects[key] = admitted(target)
            return original(kind, name)

        kube.get = late_binding
        with self.assertRaisesRegex(policy.PolicyError, 'changed since activation preflight'):
            policy.apply_policies(kube, items())
        self.assertFalse(any(call[0] == 'create' and call[1] == policy.BINDING
                             for call in kube.calls))

    def test_binding_replacement_at_final_inventory_cannot_certify_ready(self):
        kube = FakeKubernetes(ready=True)
        original = kube.get
        target = (policy.BINDING, policy.NAMES[0])
        reads = 0

        def replaced(kind, name):
            nonlocal reads
            value = original(kind, name)
            if (kind, name) == target:
                reads += 1
                if reads >= 3:
                    value['metadata']['uid'] = 'replacement-uid'
            return value

        kube.get = replaced
        with self.assertRaisesRegex(policy.PolicyError, 'identity changed'):
            policy.apply_policies(kube, items())
        self.assertEqual(kube.calls, [])

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

    def test_zero_resource_version_cannot_certify_persisted_inventory_or_activation(self):
        expected = items()[0]
        kube = FakeKubernetes(ready=True)
        current = kube.objects[(expected['kind'], expected['metadata']['name'])]
        for version in ('0', '000'):
            current['metadata']['resourceVersion'] = version
            with self.subTest(version=version), self.assertRaises(policy.PolicyError):
                policy.inventory_status(kube, items())
            with self.subTest(version=version), self.assertRaises(policy.PolicyError):
                policy.apply_policies(kube, items())
        self.assertEqual(kube.calls, [])

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

    def test_owned_spec_refuses_json_type_aliases_and_malformed_defaults(self):
        expected = copy.deepcopy(items()[0])
        expected['spec']['testBoundary'] = {'enabled': False, 'count': 1}
        for field, replacement in (('enabled', 0), ('count', True)):
            current = admitted(expected)
            current['spec']['testBoundary'][field] = replacement
            self.assertEqual(current['spec'], expected['spec'])  # Python equality is insufficient.
            with self.subTest(field=field), self.assertRaises(policy.PolicyError):
                policy.assert_owned(current, expected)
        for malformed in (None, [], {'matchConstraints': []}):
            current = admitted(expected)
            current['spec'] = malformed
            with self.subTest(spec=malformed), self.assertRaises(policy.PolicyError):
                policy.assert_owned(current, expected)

    def test_scoped_get_refuses_malformed_identity_before_request(self):
        client = object.__new__(policy.Kubernetes)
        client.json_request = mock.Mock(side_effect=AssertionError('request'))
        for malformed in ([], {}, None, False):
            with self.subTest(identity=malformed), self.assertRaises(policy.PolicyError):
                client.get(malformed, policy.NAMES[0])
            with self.subTest(name=malformed), self.assertRaises(policy.PolicyError):
                client.get(policy.POLICY, malformed)
        client.json_request.assert_not_called()


class ScopedResponseTests(unittest.TestCase):
    def test_smoke_image_requires_one_well_formed_local_trino_container(self):
        client = object.__new__(policy.Kubernetes)
        valid = {'apiVersion': 'apps/v1', 'kind': 'Deployment',
                  'metadata': {'name': 'trino-local', 'namespace': policy.NAMESPACE,
                               'uid': 'trino-uid', 'resourceVersion': '42',
                               'labels': {'app.kubernetes.io/name': 'trino-local',
                                          'app.kubernetes.io/part-of': 'shirokuma',
                                          'shirokuma.dev/scope': 'local-experiment'}},
                  'spec': {'strategy': {'type': 'Recreate'},
                           'selector': {'matchLabels': {'app.kubernetes.io/name': 'trino-local'}},
                            'template': {'metadata': {'labels': {'app.kubernetes.io/name': 'trino-local',
                                                                'app.kubernetes.io/part-of': 'shirokuma',
                                                                'shirokuma.dev/scope': 'local-experiment',
                                                                'shirokuma.dev/object-storage-client': 'true'}},
                                        'spec': {'automountServiceAccountToken': False,
                                                 'securityContext': {'runAsNonRoot': True, 'runAsUser': 10000,
                                                                     'seccompProfile': {'type': 'RuntimeDefault'}},
                                                  'volumes': [
                                                      {'name': 'config', 'configMap': {'name': 'trino-local-config'}},
                                                      {'name': 'tmp', 'emptyDir': {'sizeLimit': '1Gi'}},
                                                      {'name': 'data', 'emptyDir': {'sizeLimit': '2Gi'}}],
                                                  'containers': [
            {'name': 'trino', 'image': 'shirokuma.local/trino@sha256:' + 'a' * 64,
               'imagePullPolicy': 'Never', 'env': [
                   {'name': name, 'valueFrom': {'secretKeyRef': {'name': secret, 'key': key}}}
                   for name, secret, key in (
                       ('POLARIS_CLIENT_ID', 'polaris-root-credentials', 'client_id'),
                       ('POLARIS_CLIENT_SECRET', 'polaris-root-credentials', 'client_secret'),
                       ('POLARIS_REALM', 'polaris-root-credentials', 'realm'),
                       ('AWS_REGION', 'seaweedfs-s3-application-credentials', 'S3_REGION'),
                       ('AWS_ACCESS_KEY_ID', 'seaweedfs-s3-application-credentials', 'AWS_ACCESS_KEY_ID'),
                       ('AWS_SECRET_ACCESS_KEY', 'seaweedfs-s3-application-credentials', 'AWS_SECRET_ACCESS_KEY'))],
               'volumeMounts': [
                   {'name': 'config', 'mountPath': '/etc/trino', 'readOnly': True},
                   {'name': 'tmp', 'mountPath': '/tmp'},
                   {'name': 'data', 'mountPath': '/var/trino'}], 'securityContext': {
                  'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                  'capabilities': {'drop': ['ALL']}}}]}}}}
        client.json_request = mock.Mock(return_value=valid)
        self.assertEqual(client.image(), valid['spec']['template']['spec']['containers'][0]['image'])
        for extra in ({'status': {'phase': 'Running'}},
                      {'unknown': {'image': 'foreign'}}):
            reply = copy.deepcopy(valid)
            reply['spec']['template'].update(extra)
            client.json_request.return_value = reply
            with self.subTest(template_extra=extra), self.assertRaises(policy.PolicyError):
                client.image()
        config_paths = ('config.properties', 'node.properties', 'jvm.config',
                        'log.properties', 'catalog/iceberg.properties',
                        'catalog/tpch.properties')
        explicit = copy.deepcopy(valid)
        config = explicit['spec']['template']['spec']['volumes'][0]['configMap']
        config['items'] = [{'key': path.removeprefix('catalog/'), 'path': path}
                           for path in config_paths]
        client.json_request.return_value = explicit
        self.assertEqual(client.image(), valid['spec']['template']['spec']['containers'][0]['image'])
        for items in (config['items'][:-1], config['items'] + [config['items'][0]],
                      config['items'][:1] * 6):
            reply = copy.deepcopy(explicit)
            reply['spec']['template']['spec']['volumes'][0]['configMap']['items'] = items
            client.json_request.return_value = reply
            with self.subTest(items=items), self.assertRaises(policy.PolicyError):
                client.image()
        client.json_request.return_value = valid
        for where in ('metadata', 'template'):
            reply = copy.deepcopy(valid)
            labels = (reply['metadata']['labels'] if where == 'metadata' else
                      reply['spec']['template']['metadata']['labels'])
            labels['app.kubernetes.io/component'] = 'foreign-service'
            client.json_request.return_value = reply
            with self.subTest(extra_label=where), self.assertRaises(policy.PolicyError):
                client.image()
        for annotations in ({'sidecar.istio.io/inject': 'true'},
                            {'shirokuma.dev/foreign': 'true'}, None, []):
            reply = copy.deepcopy(valid)
            reply['spec']['template']['metadata']['annotations'] = annotations
            client.json_request.return_value = reply
            with self.subTest(annotations=annotations), self.assertRaises(policy.PolicyError):
                client.image()
        reply = copy.deepcopy(valid)
        reply['spec']['template']['metadata']['annotations'] = {}
        client.json_request.return_value = reply
        self.assertEqual(client.image(), valid['spec']['template']['spec']['containers'][0]['image'])
        reply['spec']['template']['metadata']['creationTimestamp'] = None
        client.json_request.return_value = reply
        self.assertEqual(client.image(), valid['spec']['template']['spec']['containers'][0]['image'])
        for extra in ({'finalizers': ['example.invalid/foreign']},
                      {'ownerReferences': [{'name': 'foreign'}]},
                      {'namespace': 'foreign'}, {'name': 'foreign'},
                      {'generateName': 'foreign-'}):
            reply = copy.deepcopy(valid)
            reply['spec']['template']['metadata'].update(extra)
            client.json_request.return_value = reply
            with self.subTest(template_metadata=extra), self.assertRaises(policy.PolicyError):
                client.image()
        # A pinned image from a paused, scaled-out or rolling-update source is
        # not the dedicated one-replica Recreate smoke workload.
        reply = copy.deepcopy(valid)
        reply['spec'].pop('strategy')
        client.json_request.return_value = reply
        with self.assertRaises(policy.PolicyError):
            client.image()
        for override in ({'replicas': 0}, {'replicas': 2}, {'replicas': True},
                         {'strategy': {'type': 'RollingUpdate'}},
                         {'paused': True}, {'paused': None},
                         {'minReadySeconds': 30}, {'minReadySeconds': True},
                         {'minReadySeconds': -1}):
            reply = copy.deepcopy(valid)
            reply['spec'].update(override)
            client.json_request.return_value = reply
            with self.subTest(deployment=override), self.assertRaises(policy.PolicyError):
                client.image()
        reply = copy.deepcopy(valid)
        reply['spec'].update(replicas=1, strategy={'type': 'Recreate'}, paused=False,
                             minReadySeconds=0)
        client.json_request.return_value = reply
        self.assertEqual(client.image(), valid['spec']['template']['spec']['containers'][0]['image'])
        for label_value in (None, 'false', False):
            reply = copy.deepcopy(valid)
            labels = reply['spec']['template']['metadata']['labels']
            if label_value is None:
                labels.pop('shirokuma.dev/object-storage-client')
            else:
                labels['shirokuma.dev/object-storage-client'] = label_value
            client.json_request.return_value = reply
            with self.subTest(object_storage_label=label_value), self.assertRaises(policy.PolicyError):
                client.image()
        client.json_request.return_value = valid
        with_groups = copy.deepcopy(valid)
        with_groups['spec']['template']['spec']['securityContext'].update(runAsGroup=10000, fsGroup=10000)
        client.json_request.return_value = with_groups
        self.assertEqual(client.image(), valid['spec']['template']['spec']['containers'][0]['image'])
        client.json_request.return_value = valid
        for grace in (0, 31, False, '30', None):
            reply = copy.deepcopy(valid)
            reply['spec']['template']['spec']['terminationGracePeriodSeconds'] = grace
            client.json_request.return_value = reply
            with self.subTest(termination_grace=grace), self.assertRaises(policy.PolicyError):
                client.image()
        reply = copy.deepcopy(valid)
        reply['spec']['template']['spec']['terminationGracePeriodSeconds'] = 30
        client.json_request.return_value = reply
        self.assertEqual(client.image(), valid['spec']['template']['spec']['containers'][0]['image'])
        client.json_request.return_value = valid
        # A Deployment's default Pod restart policy is Always.
        for restart in ('Never', 'OnFailure', None, False):
            reply = copy.deepcopy(valid)
            reply['spec']['template']['spec']['restartPolicy'] = restart
            client.json_request.return_value = reply
            with self.subTest(restart_policy=restart), self.assertRaises(policy.PolicyError):
                client.image()
        reply = copy.deepcopy(valid)
        reply['spec']['template']['spec']['restartPolicy'] = 'Always'
        client.json_request.return_value = reply
        self.assertEqual(client.image(), valid['spec']['template']['spec']['containers'][0]['image'])
        client.json_request.return_value = valid
        # A same-image Pod can change its DNS identity without custom dnsConfig
        # or host networking. The fixed smoke source does not set these fields.
        for override in ({'hostname': 'foreign'}, {'subdomain': 'foreign'},
                         {'setHostnameAsFQDN': True}, {'hostUsers': False},
                         {'shareProcessNamespace': True},
                         {'imagePullSecrets': [{'name': 'foreign'}]},
                         {'enableServiceLinks': False}):
            with self.subTest(pod_identity=override):
                reply = copy.deepcopy(valid)
                reply['spec']['template']['spec'].update(override)
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()
        client.json_request.return_value = valid
        for resources in ({'requests': {'cpu': '100', 'memory': '2Gi'}},
                          {'limits': {'cpu': '8', 'memory': '16Gi'},
                           'requests': {'cpu': '1', 'memory': '2Gi'},
                           'claims': [{'name': 'device'}]},
                          {'limits': {'cpu': '8', 'memory': '16Gi'},
                           'requests': {'cpu': '1', 'memory': '2Gi', 'example.com/gpu': '1'}}):
            with self.subTest(resources=resources):
                reply = copy.deepcopy(valid)
                reply['spec']['template']['spec']['containers'][0]['resources'] = resources
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()
        reply = copy.deepcopy(valid)
        reply['spec']['template']['spec']['containers'][0]['resources'] = {
            'requests': {'cpu': '1', 'memory': '2Gi'},
            'limits': {'cpu': '8', 'memory': '16Gi'}}
        client.json_request.return_value = reply
        self.assertEqual(client.image(), valid['spec']['template']['spec']['containers'][0]['image'])
        client.json_request.return_value = valid
        for override in ({'hostAliases': [{'ip': '127.0.0.1', 'hostnames': ['metadata']}]},
                         {'dnsConfig': {'nameservers': ['8.8.8.8']}},
                         {'dnsPolicy': 'Default'},
                         {'runtimeClassName': 'foreign-runtime'},
                         {'nodeName': 'foreign-node'},
                         {'nodeSelector': {'kubernetes.io/hostname': 'foreign-node'}},
                         {'schedulerName': 'foreign-scheduler'},
                         {'tolerations': [{'operator': 'Exists'}]},
                         {'affinity': {'nodeAffinity': {}}},
                          {'topologySpreadConstraints': [{'maxSkew': 1}]},
                          {'priorityClassName': 'high-priority'},
                          {'priority': 1000}, {'preemptionPolicy': 'Never'},
                           {'overhead': {'cpu': '1'}},
                           {'readinessGates': [{'conditionType': 'foreign'}]},
                           {'schedulingGates': [{'name': 'foreign'}]},
                            {'resourceClaims': [{'name': 'device', 'resourceClaimName': 'foreign'}]},
                            {'os': {'name': 'windows'}}, {'os': {'name': 'linux'}}):
            with self.subTest(pod_override=override):
                reply = copy.deepcopy(valid)
                reply['spec']['template']['spec'].update(override)
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()
        reply = copy.deepcopy(valid)
        reply['spec']['template']['spec']['activeDeadlineSeconds'] = 1
        client.json_request.return_value = reply
        with self.assertRaises(policy.PolicyError):
            client.image()
        client.json_request.return_value = valid
        reply = copy.deepcopy(valid)
        reply['spec']['template']['spec']['dnsPolicy'] = 'ClusterFirst'
        client.json_request.return_value = reply
        self.assertEqual(client.image(), valid['spec']['template']['spec']['containers'][0]['image'])
        client.json_request.return_value = valid
        for field, value in (('command', ['/bin/sh']), ('args', ['-c', 'sleep 999']),
                             ('workingDir', '/host'), ('lifecycle', {'postStart': {'exec': {'command': ['id']}}}),
                             ('envFrom', [{'secretRef': {'name': 'foreign'}}]),
                             ('volumeDevices', [{'name': 'data', 'devicePath': '/dev/data'}]),
                             ('terminationMessagePath', '/etc/trino/catalog/iceberg.properties'),
                             ('terminationMessagePolicy', 'FallbackToLogsOnError'),
                             ('resizePolicy', [{'resourceName': 'memory', 'restartPolicy': 'RestartContainer'}]),
                             ('stdin', True), ('stdinOnce', True), ('tty', True)):
            with self.subTest(container_execution=field):
                reply = copy.deepcopy(valid)
                reply['spec']['template']['spec']['containers'][0][field] = value
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()
        reply = copy.deepcopy(valid)
        reply['spec']['template']['spec']['containers'][0].update(
            terminationMessagePath='/dev/termination-log', terminationMessagePolicy='File')
        client.json_request.return_value = reply
        self.assertEqual(client.image(), valid['spec']['template']['spec']['containers'][0]['image'])
        for probe in ({'exec': {'command': ['/bin/sh', '-c', 'id']}},
                      {'httpGet': {'path': '/foreign', 'port': 'http'}},
                      {'httpGet': {'path': '/v1/info', 'port': 'http',
                                   'httpHeaders': [{'name': 'Host', 'value': 'foreign'}]}},
                      {'httpGet': {'path': '/v1/info', 'port': 'http'}, 'periodSeconds': True}):
            with self.subTest(probe=probe):
                reply = copy.deepcopy(valid)
                reply['spec']['template']['spec']['containers'][0]['startupProbe'] = probe
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()
        reply = copy.deepcopy(valid)
        reply['spec']['template']['spec']['containers'][0]['startupProbe'] = {
            'httpGet': {'path': '/v1/info', 'port': 'http'}, 'periodSeconds': 5,
            'failureThreshold': 60}
        client.json_request.return_value = reply
        self.assertEqual(client.image(), valid['spec']['template']['spec']['containers'][0]['image'])
        for ports in ([{'name': 'http', 'containerPort': 8080, 'hostPort': 8080}],
                      [{'name': 'http', 'containerPort': 8080, 'hostIP': '0.0.0.0'}],
                      [{'name': 'http', 'containerPort': 8080, 'protocol': 'UDP'}],
                      [{'name': 'http', 'containerPort': True}],
                      [{'name': 'http', 'containerPort': 8080},
                       {'name': 'other', 'containerPort': 9000}]):
            with self.subTest(ports=ports):
                reply = copy.deepcopy(valid)
                reply['spec']['template']['spec']['containers'][0]['ports'] = ports
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()
        reply = copy.deepcopy(valid)
        reply['spec']['template']['spec']['containers'][0]['ports'] = [
            {'name': 'http', 'containerPort': 8080}]
        client.json_request.return_value = reply
        self.assertEqual(client.image(), valid['spec']['template']['spec']['containers'][0]['image'])
        for env in ([{'name': 'AWS_ACCESS_KEY_ID', 'value': 'foreign'}],
                    [{'name': 'AWS_ACCESS_KEY_ID', 'valueFrom': {'secretKeyRef':
                        {'name': 'foreign', 'key': 'AWS_ACCESS_KEY_ID'}}}],
                    [{'name': 'EXTRA', 'valueFrom': {'secretKeyRef':
                        {'name': 'polaris-root-credentials', 'key': 'client_id'}}}],
                    [{'name': 'AWS_ACCESS_KEY_ID', 'valueFrom': {'configMapKeyRef':
                        {'name': 'foreign', 'key': 'AWS_ACCESS_KEY_ID'}}}]):
            with self.subTest(env=env):
                reply = copy.deepcopy(valid)
                reply['spec']['template']['spec']['containers'][0]['env'] = env
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()
        for change in ('missing', 'duplicate', 'literal'):
            with self.subTest(env_change=change):
                reply = copy.deepcopy(valid)
                entries = reply['spec']['template']['spec']['containers'][0]['env']
                if change == 'missing':
                    entries.pop()
                elif change == 'duplicate':
                    entries[-1] = copy.deepcopy(entries[0])
                else:
                    entries[0]['valueFrom']['secretKeyRef']['optional'] = True
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()
        reply = copy.deepcopy(valid)
        reply['spec']['template']['spec']['containers'][0]['env'].reverse()
        client.json_request.return_value = reply
        self.assertEqual(client.image(), valid['spec']['template']['spec']['containers'][0]['image'])
        client.json_request.return_value = valid
        for account in ('foreign-admin', None, 0, []):
            with self.subTest(legacy_service_account=account):
                reply = copy.deepcopy(valid)
                reply['spec']['template']['spec']['serviceAccount'] = account
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()
        client.json_request.return_value = valid
        for account in ('foreign-admin', None, 0, []):
            with self.subTest(service_account=account):
                reply = copy.deepcopy(valid)
                reply['spec']['template']['spec']['serviceAccountName'] = account
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()
        client.json_request.return_value = valid
        reply = copy.deepcopy(valid)
        reply['spec']['template']['spec']['serviceAccountName'] = 'default'
        client.json_request.return_value = reply
        self.assertEqual(client.image(), valid['spec']['template']['spec']['containers'][0]['image'])
        reply['spec']['template']['spec']['serviceAccount'] = 'default'
        self.assertEqual(client.image(), valid['spec']['template']['spec']['containers'][0]['image'])
        client.json_request.return_value = valid
        for volumes in ([{'name': 'host', 'hostPath': {'path': '/var/run'}}],
                         [{'name': 'token', 'projected': {'sources': [
                             {'serviceAccountToken': {'path': 'token'}}]}}],
                         [{'name': 'external', 'persistentVolumeClaim': {'claimName': 'retained'}}],
                         [{'name': 'external', 'csi': {'driver': 'example.local'}}],
                          [{'name': 'mixed', 'emptyDir': {}, 'secret': {'secretName': 'other'}}],
                          [{'name': 'config', 'configMap': {'name': 'other-config'}}],
                          [{'name': 'config', 'configMap': {}}],
                          [{'name': 'config', 'configMap': {'name': 'trino-local-config', 'optional': True}}],
                          [{'name': 'config', 'configMap': {'name': 'trino-local-config', 'defaultMode': 0o777}}],
                          [{'name': 'config', 'configMap': {'name': 'trino-local-config', 'items':
                              [{'key': 'config.properties', 'path': 'catalog/iceberg.properties'}]}}],
                          [{'name': 'config', 'configMap': {'name': 'trino-local-config', 'items': None}}],
                          [{'name': 'tmp', 'emptyDir': {'medium': 'Memory'}}],
                          [{'name': 'data', 'emptyDir': {'sizeLimit': '10Gi'}}],
                          [{'name': 'other', 'emptyDir': {}}],
                          [{'name': 'tmp', 'emptyDir': {}}, {'name': 'tmp', 'emptyDir': {}}],
                          [{'name': 'untyped'}], [None], None, {}):
            with self.subTest(volumes=volumes):
                reply = copy.deepcopy(valid)
                reply['spec']['template']['spec']['volumes'] = volumes
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()
        reply = copy.deepcopy(valid)
        reply['spec']['template']['spec']['volumes'][0]['configMap']['items'] = [
            {'key': path.removeprefix('catalog/'), 'path': path} for path in config_paths]
        client.json_request.return_value = reply
        self.assertEqual(client.image(), valid['spec']['template']['spec']['containers'][0]['image'])
        for missing in ('config', 'tmp', 'data'):
            with self.subTest(missing_volume=missing):
                reply = copy.deepcopy(valid)
                reply['spec']['template']['spec']['volumes'] = [
                    item for item in reply['spec']['template']['spec']['volumes'] if item['name'] != missing]
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()
            with self.subTest(missing_mount=missing):
                reply = copy.deepcopy(valid)
                reply['spec']['template']['spec']['containers'][0]['volumeMounts'] = [
                    item for item in reply['spec']['template']['spec']['containers'][0]['volumeMounts']
                    if item['name'] != missing]
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()
        for mounts in ([{'name': 'foreign', 'mountPath': '/etc/trino'}],
                       [{'name': 'config', 'mountPath': '/etc/trino', 'subPath': 'secret'}],
                       [{'name': 'config', 'mountPath': '/etc/trino', 'readOnly': False}],
                       [{'name': 'config', 'mountPath': '/host', 'readOnly': True}],
                       [{'name': 'config', 'mountPath': '/etc/trino', 'readOnly': True},
                        {'name': 'config', 'mountPath': '/tmp'}], None):
            with self.subTest(mounts=mounts):
                reply = copy.deepcopy(valid)
                reply['spec']['template']['spec']['containers'][0]['volumeMounts'] = mounts
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()
        for volume_index in (1, 2):
            reply = copy.deepcopy(valid)
            reply['spec']['template']['spec']['volumes'][volume_index]['emptyDir'] = {}
            client.json_request.return_value = reply
            with self.subTest(unbounded_scratch=volume_index), self.assertRaises(policy.PolicyError):
                client.image()
        client.json_request.return_value = valid
        reply = copy.deepcopy(valid)
        reply['spec']['template']['spec']['containers'][0]['volumeMounts'][0]['name'] = []
        client.json_request.return_value = reply
        with self.assertRaises(policy.PolicyError):
            client.image()
        for field, value in (('runAsNonRoot', False), ('runAsNonRoot', 1),
                              ('runAsUser', 0), ('runAsUser', True),
                              ('seccompProfile', {'type': 'Unconfined'}),
                              ('sysctls', [{'name': 'kernel.shm_rmid_forced', 'value': '0'}]),
                              ('fsGroup', 0), ('fsGroup', True), ('runAsGroup', False)):
            with self.subTest(pod_security=field, value=value):
                reply = copy.deepcopy(valid)
                reply['spec']['template']['spec']['securityContext'][field] = value
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()
        for field in ('runAsNonRoot', 'runAsUser', 'seccompProfile'):
            with self.subTest(missing_pod_security=field):
                reply = copy.deepcopy(valid)
                del reply['spec']['template']['spec']['securityContext'][field]
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()
        for field, value in (('privileged', True), ('runAsUser', 0),
                              ('runAsNonRoot', False), ('seccompProfile', {'type': 'Unconfined'}),
                              ('procMount', 'Unmasked'), ('capabilities', {'drop': ['ALL'], 'add': []}),
                              ('allowPrivilegeEscalation', True),
                             ('readOnlyRootFilesystem', False),
                             ('capabilities', {'add': ['SYS_ADMIN'], 'drop': ['ALL']})):
            with self.subTest(container_security=field, value=value):
                reply = copy.deepcopy(valid)
                reply['spec']['template']['spec']['containers'][0]['securityContext'][field] = value
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()
        for field in ('allowPrivilegeEscalation', 'readOnlyRootFilesystem', 'capabilities'):
            with self.subTest(missing_container_security=field):
                reply = copy.deepcopy(valid)
                del reply['spec']['template']['spec']['containers'][0]['securityContext'][field]
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()
        for field, value in (('automountServiceAccountToken', None),
                             ('automountServiceAccountToken', True),
                             ('automountServiceAccountToken', 0),
                             ('hostNetwork', True), ('hostPID', True), ('hostIPC', True),
                             ('hostNetwork', None), ('hostPID', 0), ('hostIPC', [])):
            with self.subTest(pod_field=field, value=value):
                reply = copy.deepcopy(valid)
                pod = reply['spec']['template']['spec']
                if value is None and field == 'automountServiceAccountToken':
                    del pod[field]
                else:
                    pod[field] = value
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()
        for field in ('hostNetwork', 'hostPID', 'hostIPC'):
            reply = copy.deepcopy(valid)
            reply['spec']['template']['spec'][field] = False
            client.json_request.return_value = reply
            self.assertEqual(client.image(), valid['spec']['template']['spec']['containers'][0]['image'])
        for references in ([{'apiVersion': 'v1', 'kind': 'ConfigMap', 'name': 'other',
                             'uid': 'foreign', 'controller': True}],
                           [None], None, {}):
            with self.subTest(ownerReferences=references):
                reply = copy.deepcopy(valid)
                reply['metadata']['ownerReferences'] = references
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()
        reply = copy.deepcopy(valid)
        reply['metadata']['ownerReferences'] = []
        client.json_request.return_value = reply
        self.assertEqual(client.image(), valid['spec']['template']['spec']['containers'][0]['image'])
        for location in ('metadata', 'template'):
            for key, value in (('shirokuma.dev/scope', None),
                               ('shirokuma.dev/scope', 'other'),
                               ('app.kubernetes.io/part-of', None),
                               ('app.kubernetes.io/part-of', 'other')):
                with self.subTest(location=location, key=key, value=value):
                    reply = copy.deepcopy(valid)
                    labels = (reply['metadata']['labels'] if location == 'metadata' else
                              reply['spec']['template']['metadata']['labels'])
                    if value is None:
                        del labels[key]
                    else:
                        labels[key] = value
                    client.json_request.return_value = reply
                    with self.assertRaises(policy.PolicyError):
                        client.image()
        for selector, labels in (({'matchLabels': {'app.kubernetes.io/name': 'other'}},
                                  {'app.kubernetes.io/name': 'other'}),
                                 ({'matchLabels': {'app.kubernetes.io/name': 'trino-local'}},
                                  {'app.kubernetes.io/name': 'other'}),
                                 ({'matchExpressions': []}, {'app.kubernetes.io/name': 'trino-local'}),
                                 ({'matchLabels': {'app.kubernetes.io/name': 'trino-local',
                                                   'unexpected': 'other'}},
                                  {'app.kubernetes.io/name': 'trino-local', 'unexpected': 'other'})):
            with self.subTest(selector=selector, labels=labels):
                reply = copy.deepcopy(valid)
                reply['spec']['selector'] = selector
                reply['spec']['template']['metadata']['labels'] = labels
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()
        for field in ('selector', 'template'):
            with self.subTest(missing=field):
                reply = copy.deepcopy(valid)
                del reply['spec'][field]
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()
        for labels in (None, [], {}, {'app.kubernetes.io/name': False}):
            with self.subTest(labels=labels):
                reply = copy.deepcopy(valid)
                reply['spec']['template']['metadata']['labels'] = labels
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()
        for field, value in (('uid', None), ('uid', []), ('resourceVersion', None),
                             ('resourceVersion', '0'), ('resourceVersion', 'not-persisted')):
            with self.subTest(identity=field, value=value):
                reply = copy.deepcopy(valid)
                reply['metadata'][field] = value
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()
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

        for containers in (
                [valid['spec']['template']['spec']['containers'][0]] * 2,
                [valid['spec']['template']['spec']['containers'][0],
                 {'name': 'sidecar', 'image': 'unrelated:mutable'}],
                [{'name': 'trino', 'image': 'shirokuma.local/trino:latest'}],
                [{'name': 'trino', 'image': 'shirokuma.local/trino@sha256:' + 'A' * 64}],
                [{'name': 'trino', 'image': 'shirokuma.local/trino@sha256:' + 'a' * 63}]):
            with self.subTest(containers=containers):
                reply = copy.deepcopy(valid)
                reply['spec']['template']['spec']['containers'] = containers
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()

        for pull_policy in (None, 'Always', 'IfNotPresent', False, []):
            with self.subTest(imagePullPolicy=pull_policy):
                reply = copy.deepcopy(valid)
                container = reply['spec']['template']['spec']['containers'][0]
                if pull_policy is None:
                    del container['imagePullPolicy']
                else:
                    container['imagePullPolicy'] = pull_policy
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()

        for extra in ({'initContainers': [{'name': 'setup', 'image': 'unrelated:mutable'}]},
                      {'ephemeralContainers': [{'name': 'debug', 'image': 'unrelated:mutable'}]},
                      {'initContainers': {}}, {'initContainers': None},
                      {'ephemeralContainers': {}}, {'ephemeralContainers': None}):
            with self.subTest(extra=extra):
                reply = copy.deepcopy(valid)
                reply['spec']['template']['spec'].update(extra)
                client.json_request.return_value = reply
                with self.assertRaises(policy.PolicyError):
                    client.image()
        reply = copy.deepcopy(valid)
        reply['spec']['template']['spec'].update(initContainers=[], ephemeralContainers=[])
        client.json_request.return_value = reply
        self.assertEqual(client.image(), valid['spec']['template']['spec']['containers'][0]['image'])

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

    def test_scoped_json_refuses_decoded_lone_surrogates(self):
        client = object.__new__(policy.Kubernetes)
        for stdout in ('{"metadata":{"name":"\\ud800"}}',
                       '{"metadata":{"\\udc00":"value"}}'):
            with self.subTest(stdout=stdout):
                client.request = mock.Mock(return_value=SimpleNamespace(
                    stdout=stdout, stderr='', returncode=0))
                with self.assertRaises(policy.PolicyError):
                    client.json_request(['get', 'pods'])
        client.request.return_value = SimpleNamespace(
            stdout='{"metadata":{"name":"\\ud83d\\ude00"}}', stderr='', returncode=0)
        self.assertEqual(client.json_request(['get', 'pods'])['metadata']['name'], '😀')

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
    def test_binding_reappearing_during_policy_retention_cannot_certify_rollback(self):
        kube = FakeKubernetes(ready=True)
        key = (policy.BINDING, policy.NAMES[0])
        original = copy.deepcopy(kube.objects[key])
        original_get = kube.get

        def reappear_during_policy_read(kind, name):
            if kind == policy.POLICY and name == policy.NAMES[0]:
                kube.objects[key] = original
            return original_get(kind, name)

        kube.get = reappear_during_policy_read
        with self.assertRaisesRegex(policy.PolicyError, 'rollback is incomplete'):
            policy.rollback_bindings(kube, items())
        self.assertIn(key, kube.objects)

    def test_reappearing_earlier_binding_cannot_certify_rollback(self):
        kube = FakeKubernetes(ready=True)
        first = (policy.BINDING, policy.NAMES[0])
        last = policy.NAMES[-1]
        original = copy.deepcopy(kube.objects[first])
        original_delete = kube.delete_binding

        def reappear_after_last_delete(current, expected):
            original_delete(current, expected)
            if expected['metadata']['name'] == last:
                kube.objects[first] = original

        kube.delete_binding = reappear_after_last_delete
        with self.assertRaisesRegex(policy.PolicyError, 'rollback is incomplete'):
            policy.rollback_bindings(kube, items())
        self.assertIn(first, kube.objects)

    def test_incomplete_or_duplicate_binding_inventory_cannot_report_success(self):
        baseline = items()
        bindings = [x for x in baseline if x['kind'] == policy.BINDING]
        for supplied in (baseline[:-1], baseline + [bindings[0]],
                         [x for x in baseline if x['kind'] != policy.BINDING]):
            with self.subTest(count=len(supplied)):
                kube = FakeKubernetes(ready=True)
                with self.assertRaises(policy.PolicyError):
                    policy.rollback_bindings(kube, supplied)
                self.assertEqual(kube.calls, [])
                self.assertEqual(len([key for key in kube.objects if key[0] == policy.BINDING]), 8)

    def test_unhashable_binding_name_fails_before_any_read(self):
        supplied = items()
        next(x for x in supplied if x['kind'] == policy.BINDING)['metadata']['name'] = []
        kube = FakeKubernetes(ready=True)
        with self.assertRaises(policy.PolicyError):
            policy.rollback_bindings(kube, supplied)
        self.assertEqual(kube.calls, [])

    def test_replaced_binding_between_preflight_and_delete_is_preserved(self):
        for field, replacement in (('uid', 'replacement-uid'), ('resourceVersion', '13')):
            with self.subTest(field=field):
                kube = FakeKubernetes(ready=True)
                first = next(x for x in items() if x['kind'] == policy.BINDING)
                key = (policy.BINDING, first['metadata']['name'])
                original_get = kube.get
                reads = 0

                def replace_after_preflight(kind, name):
                    nonlocal reads
                    if kind == policy.BINDING and name == first['metadata']['name']:
                        reads += 1
                        if reads == 2:
                            kube.objects[key]['metadata'][field] = replacement
                    return original_get(kind, name)

                kube.get = replace_after_preflight
                events = []
                with self.assertRaisesRegex(policy.PolicyError, 'changed since rollback preflight'):
                    policy.rollback_bindings(kube, items(), record=events.append)
                self.assertEqual(kube.calls, [])
                self.assertEqual(events, [])
                self.assertEqual(kube.objects[key]['metadata'][field], replacement)

    def test_binding_created_after_absence_preflight_is_not_deleted(self):
        kube = FakeKubernetes(ready=True)
        first = next(x for x in items() if x['kind'] == policy.BINDING)
        key = (policy.BINDING, first['metadata']['name'])
        new_binding = kube.objects.pop(key)
        original_get = kube.get
        reads = 0

        def appear_after_preflight(kind, name):
            nonlocal reads
            if (kind, name) == key:
                reads += 1
                if reads == 2:
                    kube.objects[key] = new_binding
            return original_get(kind, name)

        kube.get = appear_after_preflight
        with self.assertRaisesRegex(policy.PolicyError, 'changed since rollback preflight'):
            policy.rollback_bindings(kube, items())
        self.assertEqual(kube.calls, [])
        self.assertEqual(kube.objects[key], new_binding)

    def test_later_binding_changed_on_second_scan_prevents_earlier_delete(self):
        for field, replacement in (('uid', 'new-uid'), ('resourceVersion', '13')):
            with self.subTest(field=field):
                kube = FakeKubernetes(ready=True)
                key = (policy.BINDING, policy.NAMES[-1])
                original_get = kube.get
                reads = 0

                def change_later_binding(kind, name):
                    nonlocal reads
                    if (kind, name) == key:
                        reads += 1
                        if reads == 2:
                            kube.objects[key]['metadata'][field] = replacement
                    return original_get(kind, name)

                kube.get = change_later_binding
                events = []
                with self.assertRaisesRegex(policy.PolicyError, 'changed since rollback preflight'):
                    policy.rollback_bindings(kube, items(), record=events.append)
                self.assertEqual(kube.calls, [])
                self.assertEqual(events, [])
                self.assertEqual(len([x for x in kube.objects if x[0] == policy.BINDING]), 8)

    def test_later_binding_missing_delete_precondition_prevents_partial_rollback(self):
        for field, invalid in (('uid', None), ('resourceVersion', 'not-a-version')):
            with self.subTest(field=field):
                kube = FakeKubernetes(ready=True)
                key = (policy.BINDING, policy.NAMES[-1])
                kube.objects[key]['metadata'][field] = invalid
                events = []
                with self.assertRaisesRegex(policy.PolicyError, 'UID and resource version'):
                    policy.rollback_bindings(kube, items(), record=events.append)
                self.assertEqual(kube.calls, [])
                self.assertEqual(events, [])
                self.assertEqual(len([key for key in kube.objects if key[0] == policy.BINDING]), 8)

    def test_malformed_binding_after_delete_cannot_confirm_rollback(self):
        kube = FakeKubernetes(ready=True)
        first = next(x for x in items() if x['kind'] == policy.BINDING)
        original_delete = kube.delete_binding

        def replace_with_malformed(current, expected):
            original_delete(current, expected)
            kube.objects[(policy.BINDING, expected['metadata']['name'])] = {'metadata': None}

        kube.delete_binding = replace_with_malformed
        events = []
        with self.assertRaises(policy.PolicyError):
            policy.rollback_bindings(kube, items(), record=events.append)
        self.assertEqual(events, [])
        self.assertEqual(kube.objects[(policy.BINDING, first['metadata']['name'])], {'metadata': None})
        self.assertEqual(len([x for x in kube.objects if x[0] == policy.BINDING]), len(policy.NAMES))

    def test_foreign_same_uid_after_delete_does_not_wait_as_owned(self):
        kube = FakeKubernetes(ready=True)
        first = next(x for x in items() if x['kind'] == policy.BINDING)
        original_delete = kube.delete_binding

        def replace_with_foreign(current, expected):
            original_delete(current, expected)
            current['spec']['validationActions'] = ['Warn']
            kube.objects[(policy.BINDING, expected['metadata']['name'])] = current

        kube.delete_binding = replace_with_foreign
        events = []
        with self.assertRaises(policy.PolicyError):
            policy.rollback_bindings(kube, items(), record=events.append, pause=lambda _: self.fail('waited on foreign binding'))
        self.assertEqual(events, [])
        self.assertEqual(kube.objects[(policy.BINDING, first['metadata']['name'])]['spec']['validationActions'], ['Warn'])

    def test_only_owned_bindings_are_removed_policies_and_workloads_untouched(self):
        kube = FakeKubernetes(ready=True)
        result = policy.rollback_bindings(kube, items())
        self.assertTrue(result['bindings_removed'])
        self.assertTrue(result['policy_definitions_retained'])
        self.assertEqual(len(kube.objects), 8)
        self.assertEqual({kind for kind, _ in kube.objects}, {policy.POLICY})
        self.assertEqual({(action, kind) for action, kind, _ in kube.calls}, {('delete', policy.BINDING)})

    def test_rollback_does_not_claim_missing_or_replaced_policies_retained(self):
        for replacement in (None, 'foreign', 'unpersisted'):
            with self.subTest(replacement=replacement):
                kube = FakeKubernetes(ready=True)
                key = (policy.POLICY, policy.NAMES[0])
                if replacement is None:
                    kube.objects.pop(key)
                elif replacement == 'foreign':
                    kube.objects[key]['metadata']['labels'].clear()
                else:
                    kube.objects[key]['metadata'].pop('uid')
                result = policy.rollback_bindings(kube, items())
                self.assertTrue(result['bindings_removed'])
                self.assertFalse(result['policy_definitions_retained'])
                self.assertEqual(len([key for key in kube.objects if key[0] == policy.BINDING]), 0)

    def test_rollback_does_not_claim_same_spec_new_uid_policy_retained(self):
        kube = FakeKubernetes(ready=True)
        key = (policy.POLICY, policy.NAMES[0])
        original_delete = kube.delete_binding

        def replace_definition(current, expected):
            original_delete(current, expected)
            kube.objects[key]['metadata']['uid'] = 'replacement-policy-uid'

        kube.delete_binding = replace_definition
        result = policy.rollback_bindings(kube, items())
        self.assertTrue(result['bindings_removed'])
        self.assertFalse(result['policy_definitions_retained'])

    def test_rollback_retention_uses_uid_not_status_resource_version(self):
        kube = FakeKubernetes(ready=True)
        key = (policy.POLICY, policy.NAMES[0])
        original_delete = kube.delete_binding

        def update_status(current, expected):
            original_delete(current, expected)
            kube.objects[key]['metadata']['resourceVersion'] = '42'

        kube.delete_binding = update_status
        result = policy.rollback_bindings(kube, items())
        self.assertTrue(result['bindings_removed'])
        self.assertTrue(result['policy_definitions_retained'])

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

    def test_binding_delete_requires_json_exact_certified_snapshot(self):
        certified = items()
        binding = next(x for x in certified if x['kind'] == policy.BINDING)
        binding['spec']['testBoundary'] = {'enabled': False}
        client = policy.Kubernetes(certified)
        changed = copy.deepcopy(binding)
        changed['spec']['testBoundary']['enabled'] = 0
        self.assertEqual(changed, binding)  # Python's boolean/number alias.
        with mock.patch.object(client, 'json_request', side_effect=AssertionError('delete')):
            with self.assertRaises(policy.PolicyError):
                client.delete_binding(admitted(changed), changed)


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

    def test_smoke_success_refuses_unexamined_invalid_unicode(self):
        kube = FakeKubernetes(ready=True)
        def response(value):
            raw = json.dumps(value)
            return SimpleNamespace(returncode=0, stdout=raw[:-1] + ',"unexamined":"\\ud800"}', stderr='')
        kube.smoke_failure = response
        self.assertEqual(policy.smoke_success_object(response(items()[0])), {})
        with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
            policy.smoke(kube, items())
        self.assertEqual(len(kube.calls), 1)

        valid = SimpleNamespace(returncode=0, stdout='{"unexamined":"\\ud83d\\ude00"}', stderr='')
        self.assertEqual(policy.smoke_success_object(valid)['unexamined'], '\U0001f600')

    def test_smoke_success_refuses_escaped_duplicate_keys_at_nested_depth(self):
        kube = FakeKubernetes(ready=True)
        def response(value):
            raw = json.dumps(value)
            return SimpleNamespace(returncode=0, stderr='', stdout=raw[:-1] +
                                   ',"unexamined":{"name":1,"\\u006eame":2}}')
        kube.smoke_failure = response
        self.assertEqual(policy.smoke_success_object(response(items()[0])), {})
        with self.assertRaisesRegex(policy.PolicyError, 'dry-run result'):
            policy.smoke(kube, items())
        self.assertEqual(len(kube.calls), 1)
        valid = SimpleNamespace(returncode=0, stderr='',
                                stdout='{"unexamined":{"name":1,"other":2}}')
        self.assertEqual(policy.smoke_success_object(valid)['unexamined'],
                         {'name': 1, 'other': 2})

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
