"""Native Gatekeeper feature projections and fake server dry-run verification."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('gatekeeper_observe', ROOT / 'tools/local_experiment/policy/gatekeeper.py')
G = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(G)
IMAGE = 'shirokuma.local/trino@sha256:' + 'a' * 64


class FakeKube:
    def __init__(self):
        self.image_value = IMAGE
        self.items = {}
        for item in G.MANAGE.load_manifest():
            value = copy.deepcopy(item)
            key = (item['kind'], item['metadata']['name'])
            value['metadata'].update(uid=str(uuid.uuid5(uuid.NAMESPACE_DNS, ':'.join(key))), generation=1)
            if item['kind'] == G.MANAGE.POLICY:
                value['status'] = {'observedGeneration': 1, 'typeChecking': {}}
            self.items[key] = value
        self.requests = []
        self.override = None

    def get(self, kind, name):
        return copy.deepcopy(self.items.get((kind, name)))

    def image(self):
        return self.image_value

    def smoke_request(self, value):
        self.requests.append(copy.deepcopy(value))
        if self.override:
            return self.override(value)
        def identity(obj):
            obj = copy.deepcopy(obj)
            obj['metadata'].pop('name')
            return obj
        case = next(case for case in G.MANAGE.smoke_cases(IMAGE) if identity(case['object']) == identity(value))
        policy = case['deny_policy']
        if policy:
            message = self.items[(G.MANAGE.POLICY, policy)]['spec']['validations'][0]['message']
            return SimpleNamespace(returncode=1, stdout='', stderr=f'ValidatingAdmissionPolicy \'{policy}\' with binding \'{policy}\' denied request: {message}\n')
        return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr='')

    def create(self, *args, **kwargs):
        raise AssertionError('no policy mutation is permitted')

    def delete_binding(self, *args, **kwargs):
        raise AssertionError('no policy deletion is permitted')

    def request(self, *args, **kwargs):
        raise AssertionError('no generic runtime command is permitted')


class GatekeeperTests(unittest.TestCase):
    def test_neutral_features_do_not_reveal_host_answers_names_or_raw_manifests(self):
        observer = G.GatekeeperObserver(FakeKube())
        value = observer.observe()
        self.assertEqual(len(value['candidates']), 15)
        self.assertEqual([x['id'] for x in value['candidates']], [f'c{i:02}' for i in range(1, 16)])
        encoded = json.dumps(value)
        for forbidden in ['deny_policy', 'deny-privileged', 'allow-pod', 'shirokuma-policy-smoke-',
                          'shirokuma.local/trino@', '"hostPath":', 'apiVersion', 'violation_ids', 'expected_policy']:
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(value['candidates'][0]['features']['containers'][0]['image_digest_syntax'], True)
        self.assertEqual(value['candidates'][9]['features']['containers'][0]['image_digest_syntax'], False)
        self.assertEqual(value['authority'], 'advisory_only')
        self.assertIn('DIGEST_SYNTAX_NOT_ATTESTATION', value['limits'])

    def test_host_truth_is_separate_and_outside_namespace_is_not_image_safety(self):
        value = G.candidate_inputs(IMAGE)
        truth = G.host_expectations()
        self.assertEqual(len(truth['candidates']), 15)
        self.assertEqual(truth['candidates'][9]['violation_ids'], ['P06'])
        self.assertEqual(value[-1]['namespace_scope'], 'outside')
        self.assertFalse(value[-1]['features']['containers'][0]['image_digest_syntax'])
        self.assertEqual(truth['candidates'][-1]['violation_ids'], [])
        self.assertEqual(truth['candidates'][-1]['scope_limit_ids'], ['OUTSIDE_BASELINE_NAMESPACE'])
        truth['candidates'][0]['violation_ids'].append('P01')
        self.assertEqual(G.host_expectations()['candidates'][0]['violation_ids'], [])

    def test_authorization_boundaries_do_not_restore_per_attempt_approval(self):
        inputs = G.boundary_inputs()
        truth = G.host_expectations()['boundaries']
        self.assertEqual(len(inputs), 10)
        self.assertEqual(truth[:2], [{'id': 'a01', 'boundary_ids': []}, {'id': 'a02', 'boundary_ids': []}])
        self.assertEqual(truth[2]['boundary_ids'], ['B_RETAINED_DATA'])
        self.assertTrue(all(item['operation'] == 'classify_only' for item in inputs))
        self.assertNotIn('approval_required', json.dumps(inputs))
        self.assertEqual({x['boundary_ids'][0] for x in truth if x['boundary_ids']}, {x['id'] for x in G.BOUNDARY_RULES})

    def test_all_fifteen_server_dry_runs_match_without_persisting_or_granting_authority(self):
        kube = FakeKube()
        observer = G.GatekeeperObserver(kube)
        observer.observe()
        receipt = observer.verify()
        self.assertEqual(len(kube.requests), 15)
        self.assertEqual(receipt['result'], 'passed')
        self.assertEqual(receipt['passed_cases'], 15)
        self.assertEqual(receipt['namespace_cases'], 14)
        self.assertEqual(receipt['outside_namespace_controls'], 1)
        self.assertEqual(receipt['persisted_workloads'], 0)
        self.assertEqual(receipt['boundary_actions_executed'], 0)
        self.assertFalse(receipt['authority_granted'])
        self.assertIsNone(receipt['pending'])
        self.assertEqual(sum(row['admission'] == 'denied' for row in receipt['results']), 12)
        with self.assertRaises(G.GatekeeperError):
            observer.verify()

    def test_missing_identity_typechecking_or_foreign_owner_stops_before_probes(self):
        for mode in ['missing', 'missing_uid', 'pending', 'warning', 'owner']:
            kube = FakeKube()
            key = next(k for k in kube.items if k[0] == G.MANAGE.POLICY)
            if mode == 'missing':
                del kube.items[key]
            elif mode == 'missing_uid':
                del kube.items[key]['metadata']['uid']
            elif mode == 'pending':
                kube.items[key]['status']['observedGeneration'] = 0
            elif mode == 'warning':
                kube.items[key]['status']['typeChecking'] = {'expressionWarnings': [{'warning': 'not typed'}]}
            else:
                kube.items[key]['metadata']['labels']['shirokuma.dev/policy-owner'] = 'foreign'
            with self.subTest(mode=mode), self.assertRaises((G.GatekeeperError, G.MANAGE.PolicyError)):
                G.GatekeeperObserver(kube).observe()
            self.assertEqual(kube.requests, [])

    def test_inventory_image_and_source_drift_prevent_all_probes(self):
        for mode in ['uid', 'image', 'source']:
            kube = FakeKube()
            observer = G.GatekeeperObserver(kube)
            observer.observe()
            if mode == 'uid':
                next(iter(kube.items.values()))['metadata']['uid'] = str(uuid.uuid4())
            elif mode == 'image':
                kube.image_value = IMAGE[:-1] + 'b'
            if mode == 'source':
                with patch.object(G, '_source_hashes', return_value={'changed': True}), self.assertRaises(G.GatekeeperError):
                    observer.verify()
            else:
                with self.assertRaises(G.GatekeeperError):
                    observer.verify()
            self.assertEqual(kube.requests, [])
            self.assertEqual(observer.receipt()['result'], 'failed')

    def test_wrong_policy_binding_message_or_rbac_error_is_not_a_valid_denial(self):
        for error in ['Forbidden PRIVATE raw credentials',
                      "ValidatingAdmissionPolicy 'shirokuma-local-disallow-privileged-containers' with binding 'wrong' denied request: Privileged containers are forbidden.\n",
                      "ValidatingAdmissionPolicy 'shirokuma-local-disallow-privileged-containers' with binding 'shirokuma-local-disallow-privileged-containers' denied request: evaluation error PRIVATE\n"]:
            kube = FakeKube()
            observer = G.GatekeeperObserver(kube)
            observer.observe()
            original = kube.smoke_request
            def wrong(value):
                if len(kube.requests) >= 2:
                    kube.requests.append(copy.deepcopy(value))
                    return SimpleNamespace(returncode=1, stdout='', stderr=error)
                return original(value)
            kube.smoke_request = wrong
            with self.assertRaises(G.GatekeeperError) as caught:
                observer.verify()
            receipt = caught.exception.receipt
            self.assertEqual(receipt['result'], 'failed')
            self.assertEqual(receipt['results'][-1]['admission'], 'unknown')
            self.assertFalse(receipt['results'][-1]['matched_host_expectation'])
            self.assertNotIn('PRIVATE', json.dumps(receipt))
            self.assertEqual(len(kube.requests), 3)

    def test_wrong_allowed_identity_cannot_pass_or_become_authorization(self):
        kube = FakeKube()
        observer = G.GatekeeperObserver(kube)
        observer.observe()
        kube.override = lambda value: SimpleNamespace(returncode=0, stdout=json.dumps({'kind': 'Pod', 'metadata': {'name': 'different', 'namespace': 'default'}}), stderr='')
        with self.assertRaises(G.GatekeeperError) as caught:
            observer.verify()
        self.assertFalse(caught.exception.receipt['authority_granted'])
        self.assertEqual(caught.exception.receipt['results'][0]['admission'], 'unknown')

    def test_transport_failure_and_signal_keep_unknown_pending_candidate(self):
        class Signal(BaseException):
            pass
        for kind in [RuntimeError, Signal]:
            kube = FakeKube()
            observer = G.GatekeeperObserver(kube)
            observer.observe()
            def fail(value):
                raise kind('PRIVATE failure')
            kube.override = fail
            with self.assertRaises(G.GatekeeperError if kind is RuntimeError else Signal):
                observer.verify()
            receipt = observer.receipt()
            self.assertEqual(receipt['result'], 'failed' if kind is RuntimeError else 'interrupted')
            self.assertEqual(receipt['pending']['candidate_id'], 'c01')
            self.assertEqual(receipt['pending']['outcome'], 'unknown')
            self.assertNotIn('PRIVATE', json.dumps(receipt))

    def test_native_policy_change_during_probes_fails_final_confirmation(self):
        kube = FakeKube()
        observer = G.GatekeeperObserver(kube)
        observer.observe()
        original = kube.smoke_request
        def changed(value):
            result = original(value)
            if len(kube.requests) == 15:
                next(iter(kube.items.values()))['metadata']['uid'] = str(uuid.uuid4())
            return result
        kube.smoke_request = changed
        with self.assertRaises(G.GatekeeperError) as caught:
            observer.verify()
        self.assertEqual(len(caught.exception.receipt['results']), 15)
        self.assertEqual(caught.exception.receipt['result'], 'failed')

    def test_progress_persists_pending_and_confirmed_each_request(self):
        kube = FakeKube()
        observer = G.GatekeeperObserver(kube)
        observer.observe()
        progress = []
        def save(value):
            progress.append((len(kube.requests), copy.deepcopy(value)))
            value['results'].clear()  # Caller cannot erase internal evidence.
        receipt = observer.verify(progress=save)
        self.assertEqual(len(progress), 31)
        for i in range(15):
            before_count, before = progress[2 * i]
            after_count, after = progress[2 * i + 1]
            self.assertEqual(before_count, i)
            self.assertEqual(before['pending']['candidate_id'], f'c{i + 1:02}')
            self.assertEqual(len(before['results']), i)
            self.assertEqual(after_count, i + 1)
            self.assertIsNone(after['pending'])
            self.assertEqual(len(after['results']), i + 1)
        self.assertEqual(progress[-1][1]['result'], 'passed')
        self.assertEqual(receipt['passed_cases'], 15)
        self.assertEqual(observer.receipt()['passed_cases'], 15)

    def test_progress_failure_before_probe_stops_and_preserves_safe_receipt(self):
        kube = FakeKube()
        observer = G.GatekeeperObserver(kube)
        observer.observe()
        def failed_save(value):
            raise OSError('PRIVATE evidence path')
        with self.assertRaises(G.GatekeeperError) as caught:
            observer.verify(progress=failed_save)
        self.assertEqual(kube.requests, [])
        self.assertEqual(caught.exception.receipt['result'], 'failed')
        self.assertEqual(caught.exception.receipt['pending']['candidate_id'], 'c01')
        self.assertNotIn('PRIVATE', json.dumps(caught.exception.receipt))


if __name__ == '__main__':
    unittest.main()
