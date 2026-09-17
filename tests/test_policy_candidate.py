"""Pure bounded caller Pod parsing; no cluster, subprocess or model calls."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.policy import candidate as C

TOKEN = '1' * 32
IMAGE = 'private-registry.example/test-candidate@sha256:' + 'a' * 64
DEFAULT = object()


def pod():
    return {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': 'private-candidate-name'},
            'spec': {'containers': [{'name': 'private-container-name', 'image': IMAGE,
                'resources': {'requests': {'cpu': '50m', 'memory': '32Mi'},
                              'limits': {'cpu': '100m', 'memory': '64Mi'}}}]}}


def encoded(value):
    return json.dumps(value, ensure_ascii=True).encode()


def prepared(value=DEFAULT):
    return C.prepare(encoded(pod() if value is DEFAULT else value), TOKEN)


def codes(value):
    return value.host_truth()['candidates'][0]['violation_ids']


class CandidateTests(unittest.TestCase):
    def assert_invalid(self, value, code='CANDIDATE_INVALID'):
        with self.assertRaises(C.CandidateError) as caught:
            prepared(value)
        self.assertEqual(str(caught.exception), code)
        self.assertEqual(caught.exception.code, code)

    def test_compliant_pod_receipt_has_safe_features_and_exact_hashes(self):
        original = pod()
        raw = encoded(original)
        result = C.prepare(raw, TOKEN)
        receipt = result.receipt()
        self.assertEqual(set(receipt), {'schema_version', 'input_sha256', 'canonical_input_sha256',
                                      'body_sha256', 'transform_ids', 'candidate', 'host_truth'})
        self.assertEqual(receipt['input_sha256'], hashlib.sha256(raw).hexdigest())
        for key, value in [('canonical_input_sha256', original), ('body_sha256', result.body())]:
            self.assertEqual(receipt[key], hashlib.sha256(json.dumps(value, sort_keys=True,
                separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest())
        self.assertEqual(receipt['transform_ids'], list(C.TRANSFORM_IDS))
        self.assertEqual(codes(result), [])
        self.assertEqual(result.candidate()['id'], 'c01')
        self.assertEqual(result.candidate()['namespace_scope'], 'protected')
        self.assertEqual(result.candidate()['operation'], 'server_dry_run')

    def test_body_applies_only_declared_fixed_defaults(self):
        result = prepared()
        body = result.body()
        expected = pod()
        expected['metadata'].update(name=C.NAME_PREFIX + TOKEN, namespace=C.NAMESPACE)
        expected['spec'].update(restartPolicy='Never', automountServiceAccountToken=False)
        expected['spec']['containers'][0]['imagePullPolicy'] = 'Never'
        self.assertEqual(body, expected)
        self.assertEqual(result.original(), pod())
        self.assertNotIn('command', body['spec']['containers'][0])
        self.assertNotIn('securityContext', body['spec']['containers'][0])
        self.assertLessEqual(len(body['metadata']['name']), 63)

    def test_explicit_fixed_defaults_do_not_claim_extra_transformations(self):
        value = pod()
        value['metadata'].update(name=C.NAME_PREFIX + TOKEN, namespace=C.NAMESPACE)
        value['spec'].update(restartPolicy='Never', automountServiceAccountToken=False)
        value['spec']['containers'][0]['imagePullPolicy'] = 'Never'
        result = prepared(value)
        self.assertEqual(result.body(), value)
        self.assertEqual(result.receipt()['transform_ids'], [])

    def test_each_native_pod_violation_is_preserved_and_classified(self):
        for code in ['P01', 'P02', 'P03', 'P04', 'P05', 'P06']:
            with self.subTest(code=code):
                value = pod()
                spec, container = value['spec'], value['spec']['containers'][0]
                if code == 'P01': container['securityContext'] = {'privileged': True}
                if code == 'P02': spec['hostPID'] = True
                if code == 'P03': spec['volumes'] = [{'name': 'host', 'hostPath': {'path': '/tmp'}}]
                if code == 'P04': container.pop('resources')
                if code == 'P05': container['ports'] = [{'containerPort': 8080, 'hostPort': 8080}]
                if code == 'P06': container['image'] = 'private-registry.example/test-candidate:latest'
                result = prepared(value)
                self.assertEqual(codes(result), [code])
                self.assertEqual(result.original(), value)
                body = result.body()
                for key in ('securityContext', 'ports', 'resources', 'image'):
                    self.assertEqual(body['spec']['containers'][0].get(key), container.get(key))
                for key in ('hostPID', 'volumes'):
                    self.assertEqual(body['spec'].get(key), spec.get(key))

    def test_all_violations_coexist_without_repair_or_duplicate_rule_ids(self):
        value = pod()
        value['spec'].update(hostNetwork=True, hostIPC=True, hostPID=True,
                             volumes=[{'name': 'host', 'hostPath': {'path': '/tmp'}}])
        container = value['spec']['containers'][0]
        container.update(securityContext={'privileged': True, 'allowPrivilegeEscalation': True},
            resources={}, ports=[{'containerPort': 8080, 'hostPort': 8080}], image='registry.example/test:mutable')
        result = prepared(value)
        self.assertEqual(codes(result), ['P01', 'P02', 'P03', 'P04', 'P05', 'P06'])
        self.assertTrue(result.body()['spec']['containers'][0]['securityContext']['privileged'])
        self.assertEqual(result.body()['spec']['containers'][0]['resources'], {})

    def test_host_namespace_flags_have_distinct_features_but_one_rule(self):
        for name, feature in [('hostNetwork', 'host_network'), ('hostIPC', 'host_ipc'), ('hostPID', 'host_pid')]:
            value = pod(); value['spec'][name] = True
            result = prepared(value)
            self.assertTrue(result.candidate()['features'][feature])
            self.assertEqual(codes(result), ['P02'])

    def test_registry_port_digest_matches_the_actual_native_manifest_predicate(self):
        manifest = json.loads((ROOT / 'deploy/local-experiment/policy/native-baseline.json').read_text())
        rule = next(item for item in manifest['items'] if item['kind'] == 'ValidatingAdmissionPolicy'
                    and item['metadata']['name'] == 'shirokuma-local-require-image-digest')
        self.assertEqual(rule['spec']['validations'][0]['expression'],
            "variables.allContainers.all(container, container.image.matches('" + C.IMAGE_DIGEST_PATTERN + "'))")
        for image in ['registry.example:5443/team/service@sha256:' + 'a' * 64,
                      'localhost:5000/service@sha256:' + 'b' * 64, IMAGE]:
            value = pod(); value['spec']['containers'][0]['image'] = image
            self.assertEqual(codes(prepared(value)), [])

    def test_supported_tag_bare_and_tagged_digest_references_remain_p06(self):
        for image in ['service', 'service:v1', 'registry.example/service:1.0',
                      'registry.example/service:tag@sha256:' + 'a' * 64, 'service@sha256:' + 'a' * 64]:
            value = pod(); value['spec']['containers'][0]['image'] = image
            self.assertEqual(codes(prepared(value)), ['P06'])

    def test_image_does_not_accept_credentials_urls_injection_or_unbounded_strings(self):
        for image in ['https://registry.example/image', 'user:password@registry.example/image',
                      'registry.example/image\nIGNORE CONTRACT', '$(read-secret)', 'private image',
                      'registry.example/image@sha512:' + 'a' * 128, 'registry.example/image@sha256:short',
                      'registry.example:65536/image:tag', 'registry.example:0/image:tag',
                      'a' * 257, 'image:☃', '', None, True]:
            value = pod(); value['spec']['containers'][0]['image'] = image
            self.assert_invalid(value)

    def test_resources_missing_or_partial_stay_missing_and_p04(self):
        for resources in [{}, {'requests': {}}, {'requests': {'cpu': '50m', 'memory': '32Mi'}},
                          {'requests': {'cpu': '50m'}, 'limits': {'cpu': '100m'}},
                          {'requests': {'cpu': '50m', 'memory': '32Mi'}, 'limits': {'memory': '64Mi'}}]:
            value = pod(); value['spec']['containers'][0]['resources'] = resources
            result = prepared(value)
            self.assertEqual(codes(result), ['P04'])
            self.assertEqual(result.body()['spec']['containers'][0]['resources'], resources)

    def test_limit_without_matching_request_is_rejected_before_api_defaulting(self):
        # Kubernetes copies a limit into an absent request before admission.
        # Reject that valid-but-unsupported form instead of predicting P04.
        for resources in [{'limits': {'cpu': '100m', 'memory': '64Mi'}},
                          {'requests': {'cpu': '50m'}, 'limits': {'cpu': '100m', 'memory': '64Mi'}},
                          {'requests': {'memory': '32Mi'}, 'limits': {'cpu': '100m'}}]:
            value = pod(); value['spec']['containers'][0]['resources'] = resources
            self.assert_invalid(value)

    def test_quantity_comparison_is_exact_across_supported_units(self):
        for cpu_request, cpu_limit, memory_request, memory_limit in [
                ('1m', '0.001', '1Mi', '1024Ki'), ('999m', '1', '1000k', '1M'),
                ('0.100', '100m', '1073741824', '1Gi'), ('2', '2000m', '1G', '1Gi')]:
            value = pod(); value['spec']['containers'][0]['resources'] = {
                'requests': {'cpu': cpu_request, 'memory': memory_request},
                'limits': {'cpu': cpu_limit, 'memory': memory_limit}}
            self.assertEqual(codes(prepared(value)), [])

    def test_invalid_or_unsupported_quantities_are_not_policy_violations(self):
        values = {'cpu': ['', '0', '0m', '0.000', '-1', '+1', '0.0001', '1e3', '1u', '1.5m',
                          '1.0000', '01', '001m', 'NaN', 'Infinity', ' 1', '1 ', None, 1, 0.5, True],
                  'memory': ['', '0', '0Mi', '-1Gi', '1.5Gi', '1e6', '1Ti', '1mi', '100m',
                             '01Mi', '+1Mi', '1000K', 'Inf', ' 32Mi', None, 1024, 1.0, False]}
        for resource, invalid in values.items():
            for bad in invalid:
                value = pod(); value['spec']['containers'][0]['resources']['requests'][resource] = bad
                with self.subTest(resource=resource, bad=bad):
                    with self.assertRaises(C.CandidateError) as caught: prepared(value)
                    self.assertIn(str(caught.exception), ('CANDIDATE_INVALID', 'INPUT_JSON_INVALID'))

    def test_requests_above_limits_are_rejected_without_rounding(self):
        for resource, request, limit in [('cpu', '999m', '0.998'), ('cpu', '1.001', '1000m'),
                                         ('memory', '1Gi', '1000Mi'), ('memory', '1025', '1Ki')]:
            value = pod(); value['spec']['containers'][0]['resources']['requests'][resource] = request
            value['spec']['containers'][0]['resources']['limits'][resource] = limit
            self.assert_invalid(value)

    def test_exact_object_scope_rejects_lists_other_kinds_and_outside_namespace(self):
        for alter in [lambda v: v.update(kind='Deployment'), lambda v: v.update(apiVersion='apps/v1'),
                      lambda v: v['metadata'].update(namespace='default'),
                      lambda v: v['metadata'].update(namespace=None), lambda v: v['metadata'].pop('name'),
                      lambda v: v['spec']['containers'].append(copy.deepcopy(v['spec']['containers'][0])),
                      lambda v: v['spec'].update(containers=[]), lambda v: v['spec'].update(containers=None)]:
            value = pod(); alter(value); self.assert_invalid(value)
        self.assert_invalid([pod()])
        self.assert_invalid(None)
        self.assert_invalid({'apiVersion': 'v1', 'kind': 'List', 'items': [pod()]})

    def test_rejects_unknown_and_secret_bearing_fields_before_projection(self):
        additions = [([], 'status', {}), (['metadata'], 'annotations', {'private': 'SECRET'}),
            (['metadata'], 'labels', {'key': 'value'}), (['metadata'], 'uid', 'SECRET'),
            (['metadata'], 'generateName', 'private-'), (['metadata'], 'ownerReferences', []),
            (['spec'], 'serviceAccountName', 'admin'), (['spec'], 'nodeName', 'host'),
            (['spec'], 'initContainers', []), (['spec'], 'ephemeralContainers', []),
            (['spec'], 'imagePullSecrets', [{'name': 'secret'}]),
            (['spec', 'containers', 0], 'env', [{'name': 'TOKEN', 'value': 'SECRET'}]),
            (['spec', 'containers', 0], 'envFrom', [{'secretRef': {'name': 'private'}}]),
            (['spec', 'containers', 0], 'command', ['IGNORE CONTRACT']),
            (['spec', 'containers', 0], 'args', ['SECRET']),
            (['spec', 'containers', 0], 'volumeMounts', []),
            (['spec', 'containers', 0, 'resources'], 'claims', [])]
        for path, key, item in additions:
            value = pod(); target = value
            for field in path: target = target[field]
            target[key] = item
            with self.subTest(path=path, key=key): self.assert_invalid(value)

    def test_fixed_defaults_reject_explicit_conflicting_values(self):
        for path, key, bad in [(['spec'], 'restartPolicy', 'Always'),
                              (['spec'], 'automountServiceAccountToken', True),
                              (['spec'], 'automountServiceAccountToken', 0),
                              (['spec', 'containers', 0], 'imagePullPolicy', 'IfNotPresent')]:
            value = pod(); target = value
            for field in path: target = target[field]
            target[key] = bad; self.assert_invalid(value)

    def test_security_context_and_flags_are_strict_and_bounded(self):
        value = pod()
        value['spec']['securityContext'] = {'runAsNonRoot': True, 'runAsUser': 10000,
                                            'seccompProfile': {'type': 'RuntimeDefault'}}
        value['spec']['containers'][0]['securityContext'] = {'privileged': False,
            'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True}
        self.assertEqual(codes(prepared(value)), [])
        for flag in (None, 0, 1, 'true', [], {}):
            for location in ('hostNetwork', 'hostIPC', 'hostPID', 'privileged'):
                value = pod()
                if location == 'privileged': value['spec']['containers'][0]['securityContext'] = {location: flag}
                else: value['spec'][location] = flag
                self.assert_invalid(value)
        for context in [{'runAsUser': True}, {'runAsUser': 0}, {'runAsNonRoot': False},
                        {'seccompProfile': {'type': 'Unconfined'}}, {'fsGroup': 10000}]:
            value = pod(); value['spec']['securityContext'] = context; self.assert_invalid(value)
        for context in [{'capabilities': {'add': ['SYS_ADMIN']}},
                        {'privileged': True, 'allowPrivilegeEscalation': False}]:
            value = pod(); value['spec']['containers'][0]['securityContext'] = context; self.assert_invalid(value)

    def test_host_path_is_only_an_unmounted_fixed_tmp_violation_candidate(self):
        for volumes in [[{'name': 'host', 'hostPath': {'path': '/private'}}],
                        [{'name': 'data', 'persistentVolumeClaim': {'claimName': 'retained'}}],
                        [{'name': 'secret', 'secret': {'secretName': 'retained'}}],
                        [{'name': 'host', 'hostPath': {'path': '/tmp', 'type': 'Directory'}}],
                        [{'name': 'a', 'hostPath': {'path': '/tmp'}}, {'name': 'b', 'hostPath': {'path': '/tmp'}}], None]:
            value = pod(); value['spec']['volumes'] = volumes; self.assert_invalid(value)
        value = pod(); value['spec']['volumes'] = []
        self.assertFalse(prepared(value).candidate()['features']['host_path'])

    def test_ports_are_single_typed_bounded_and_hostnetwork_consistent(self):
        for ports in [[{'containerPort': True}], [{'containerPort': 0}], [{'containerPort': 65536}],
                      [{'containerPort': 80, 'hostPort': -1}], [{'containerPort': 80, 'hostPort': True}],
                      [{'containerPort': 80, 'protocol': 'UDP'}], [{'hostPort': 80}],
                      [{'containerPort': 80}, {'containerPort': 81}], None]:
            value = pod(); value['spec']['containers'][0]['ports'] = ports; self.assert_invalid(value)
        value = pod(); value['spec']['hostNetwork'] = True
        value['spec']['containers'][0]['ports'] = [{'containerPort': 80, 'hostPort': 81}]
        self.assert_invalid(value)
        value = pod(); value['spec']['containers'][0]['ports'] = [{'containerPort': 80, 'hostPort': 0}]
        self.assertEqual(codes(prepared(value)), [])

    def test_host_network_port_defaulting_cannot_hide_p05(self):
        for port in [{'containerPort': 80}, {'containerPort': 80, 'hostPort': 0}]:
            value = pod(); value['spec']['hostNetwork'] = True
            value['spec']['containers'][0]['ports'] = [port]
            self.assert_invalid(value)
        value = pod(); value['spec']['hostNetwork'] = True
        value['spec']['containers'][0]['ports'] = [{'containerPort': 80, 'hostPort': 80}]
        self.assertEqual(codes(prepared(value)), ['P02', 'P05'])
        self.assertEqual(prepared(value).body()['spec']['containers'][0]['ports'], value['spec']['containers'][0]['ports'])
        value['spec']['containers'][0]['ports'] = []
        self.assertEqual(codes(prepared(value)), ['P02'])

    def test_json_one_document_duplicate_nonfinite_unicode_and_size_boundaries(self):
        raw = encoded(pod())
        self.assertEqual(C.parse(raw + b' ' * (C.MAX_BYTES - len(raw))), pod())
        for broken, expected in [(raw + b' ' * (C.MAX_BYTES - len(raw) + 1), 'INPUT_TOO_LARGE'),
                                 (b'\xff', 'INPUT_ENCODING_INVALID'), (raw + raw, 'INPUT_JSON_INVALID'),
                                 (b'{"apiVersion":"v1","apiVersion":"v1"}', 'INPUT_JSON_INVALID'),
                                 (b'{"spec":{"same":1,"same":2}}', 'INPUT_JSON_INVALID'),
                                 (b'NaN', 'INPUT_JSON_INVALID'), (b'Infinity', 'INPUT_JSON_INVALID'),
                                 (b'-Infinity', 'INPUT_JSON_INVALID'), (b'1.0', 'INPUT_JSON_INVALID'),
                                 (b'999999999999999', 'INPUT_JSON_INVALID'),
                                 # Parser recursion handling varies by Python version
                                 # and ambient recursion limit; either safe refusal is valid.
                                 (b'[' * 1100 + b']' * 1100, ('INPUT_JSON_INVALID', 'CANDIDATE_INVALID')),
                                 (b'', 'INPUT_JSON_INVALID'), (raw.decode(), 'INPUT_JSON_INVALID')]:
            with self.assertRaises(C.CandidateError) as caught: C.prepare(broken, TOKEN)
            self.assertIn(str(caught.exception), expected if type(expected) is tuple else (expected,))

    def test_all_unknown_or_null_containers_are_rejected(self):
        for path in [('metadata',), ('spec',), ('spec', 'containers', 0),
                     ('spec', 'containers', 0, 'resources'), ('spec', 'containers', 0, 'securityContext')]:
            value = pod(); target = value
            for key in path[:-1]: target = target[key]
            target[path[-1]] = None; self.assert_invalid(value)

    def test_raw_input_and_canonical_identity_are_distinct_and_reproducible(self):
        value = pod()
        a = C.prepare(json.dumps(value).encode(), TOKEN)
        b = C.prepare(json.dumps(value, sort_keys=True, indent=2).encode(), TOKEN)
        self.assertNotEqual(a.receipt()['input_sha256'], b.receipt()['input_sha256'])
        self.assertEqual(a.receipt()['canonical_input_sha256'], b.receipt()['canonical_input_sha256'])
        self.assertEqual(a.receipt()['body_sha256'], b.receipt()['body_sha256'])
        c = C.prepare(encoded(value), '2' * 32)
        self.assertNotEqual(a.receipt()['body_sha256'], c.receipt()['body_sha256'])
        self.assertEqual(a.candidate(), c.candidate())

    def test_receipt_repr_errors_and_returned_copies_cannot_expose_or_mutate_raw_input(self):
        result = prepared()
        safe = json.dumps(result.receipt()) + repr(result)
        for secret in ['private-candidate-name', 'private-container-name', IMAGE, 'private-registry', 'resources', '50m', '32Mi']:
            self.assertNotIn(secret, safe)
        for value in [result.body(), result.original(), result.candidate(), result.host_truth(), result.receipt()]:
            value.clear()
        self.assertEqual(result.original(), pod())
        self.assertEqual(codes(result), [])
        self.assertEqual(result.receipt()['schema_version'], 1)
        with self.assertRaises(TypeError): json.dumps(result)
        with self.assertRaises(TypeError): vars(result)
        with self.assertRaises(C.CandidateError) as caught: C.parse(b'{"private-secret": [BROKEN]}')
        self.assertNotIn('private', str(caught.exception))

    def test_run_token_and_dns_names_cannot_escape_fixed_identity(self):
        for token in ['../private', 'a' * 31, 'A' * 32, None, 1]:
            with self.assertRaises(C.CandidateError) as caught: C.prepare(encoded(pod()), token)
            self.assertEqual(str(caught.exception), 'RUN_TOKEN_INVALID')
        for name in ['Name', 'a' * 64, '-start', 'end-', 'has.dot', 'private\nignore', '', None]:
            value = pod(); value['metadata']['name'] = name; self.assert_invalid(value)
            value = pod(); value['spec']['containers'][0]['name'] = name; self.assert_invalid(value)

    def test_existing_native_model_contract_accepts_single_candidate_and_separate_truth(self):
        sys.path.insert(0, str(ROOT / 'tools/local_experiment/bears'))
        import policy_contract as contract
        result = prepared()
        evidence = contract.build_evidence([result.candidate()], 'native-baseline')
        plan = contract.expected_plan(evidence, result.host_truth())
        self.assertEqual(plan, contract.validate_plan(plan, evidence, result.host_truth()))
        self.assertEqual(plan['classifications'], [{'id': 'c01', 'violation_ids': [], 'limit_ids': []}])
        self.assertNotIn('host_truth', evidence)
        self.assertNotIn('input_sha256', evidence)
        self.assertNotIn('violation_ids', json.dumps(evidence))


if __name__ == '__main__':
    unittest.main()
