"""Ownership chain, scale preconditions and network boundaries for local warehouses."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.gateway_warehouse import build
from tools.local_experiment.warehouse_lifecycle import fixtures as F, manifest as M
from tools.local_experiment.warehouse_lifecycle.support import ExperimentError, digest

TOKEN = '1234567890abcdef1234567890abcdef'
DEP_UID, RS_UID, POD_UID, SVC_UID = [str(uuid.uuid4()) for _ in range(4)]


def current(obj, uid):
    result = copy.deepcopy(obj)
    result['metadata'].update(uid=uid, resourceVersion='7')
    return result


def pod_defaults(spec):
    value = copy.deepcopy(spec)
    value.update(dnsPolicy='ClusterFirst', schedulerName='default-scheduler', serviceAccountName='default',
                 serviceAccount='default', nodeName='colima', priority=0, preemptionPolicy='PreemptLowerPriority')
    value['tolerations'] = [
        {'key': 'node.kubernetes.io/not-ready', 'operator': 'Exists', 'effect': 'NoExecute', 'tolerationSeconds': 300},
        {'key': 'node.kubernetes.io/unreachable', 'operator': 'Exists', 'effect': 'NoExecute', 'tolerationSeconds': 300}]
    for container in value['containers']:
        container.update(terminationMessagePath='/dev/termination-log', terminationMessagePolicy='File')
        for port in container.get('ports', []):
            port['protocol'] = 'TCP'
        for key in ('startupProbe', 'readinessProbe'):
            probe = container[key]
            probe.update(initialDelaySeconds=0, timeoutSeconds=1, successThreshold=1)
            probe.setdefault('failureThreshold', 3)
            if 'httpGet' in probe:
                probe['httpGet']['scheme'] = 'HTTP'
    return value


def children(deployment):
    pod_hash = 'abc123def4'
    rs_name = deployment['metadata']['name'] + '-' + pod_hash
    template = copy.deepcopy(deployment['spec']['template'])
    template['metadata']['labels']['pod-template-hash'] = pod_hash
    template['metadata']['creationTimestamp'] = None
    rs_labels = copy.deepcopy(template['metadata']['labels'])
    selector = copy.deepcopy(deployment['spec']['selector'])
    selector['matchLabels']['pod-template-hash'] = pod_hash
    rs = {'apiVersion': 'apps/v1', 'kind': 'ReplicaSet', 'metadata': {'name': rs_name, 'namespace': M.NAMESPACE,
        'uid': RS_UID, 'resourceVersion': '9', 'labels': rs_labels,
        'ownerReferences': [{'apiVersion': 'apps/v1', 'kind': 'Deployment', 'name': deployment['metadata']['name'],
                             'uid': DEP_UID, 'controller': True, 'blockOwnerDeletion': True}]},
        'spec': {'replicas': 1, 'selector': selector, 'template': template, 'minReadySeconds': 0}}
    pod = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': (rs_name + '-')[:58] + 'a1b2c', 'generateName': rs_name + '-',
        'namespace': M.NAMESPACE, 'uid': POD_UID, 'resourceVersion': '11', 'labels': rs_labels,
        'annotations': copy.deepcopy(deployment['spec']['template']['metadata']['annotations']),
        'ownerReferences': [{'apiVersion': 'apps/v1', 'kind': 'ReplicaSet', 'name': rs_name, 'uid': RS_UID,
                             'controller': True, 'blockOwnerDeletion': True}]},
        'spec': pod_defaults(deployment['spec']['template']['spec']),
        'status': {'phase': 'Running', 'podIP': '10.42.0.55', 'conditions': [{'type': 'Ready', 'status': 'True'}]}}
    return rs, pod


class LifecycleCoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.output = Path(self.temporary.name).resolve()
        self.material = F.create_fixtures(TOKEN, self.output)
        self.images = copy.deepcopy(build.PINS['images'])
        self.objects = M.render(self.images, TOKEN, self.material)
        self.plan = {'run_token': TOKEN, 'images': self.images, 'objects': self.objects}
        self.deployment = self.objects[-2]
        self.dep_current = current(self.deployment, DEP_UID)
        self.rs, self.pod = children(self.deployment)
        self.service = self.objects[6]
        self.svc_current = current(self.service, SVC_UID)
        self.svc_current['spec'].update(clusterIP='10.43.0.22', clusterIPs=['10.43.0.22'],
            ipFamilies=['IPv4'], ipFamilyPolicy='SingleStack', internalTrafficPolicy='Cluster')

    def tearDown(self):
        self.temporary.cleanup()

    def test_eleven_resources_only_new_run(self):
        self.assertEqual(len(M.validate_plan(self.plan)), 11)
        self.assertEqual([o['kind'] for o in self.objects].count('NetworkPolicy'), 4)
        for item in self.objects:
            self.assertEqual(item['metadata']['labels'][M.OWNER], TOKEN)
            self.assertTrue(item['metadata']['name'].startswith(F.name(TOKEN)))
        self.assertEqual([c['name'] for c in self.objects[8]['spec']['containers']], ['postgres', 'gateway'])
        self.assertEqual([c['name'] for c in self.deployment['spec']['template']['spec']['containers']], ['xs'])
        self.assertNotIn('persistentVolumeClaim', json.dumps(self.objects))
        self.assertNotIn('hostPath', json.dumps(self.objects))

    def test_direction_and_size_specific_network_rules(self):
        deny, gateway, xs, small = self.objects[:4]
        self.assertEqual(deny['spec']['ingress'], [])
        self.assertEqual(deny['spec']['egress'], [])
        self.assertEqual(gateway['spec']['policyTypes'], ['Egress'])
        for size, item, port in [('xs', xs, 18581), ('s', small, 18582)]:
            self.assertEqual(item['spec']['podSelector']['matchLabels'][M.SIZE], size)
            self.assertEqual(item['spec']['ingress'][0]['ports'], [{'protocol': 'TCP', 'port': port}])
            self.assertEqual(item['spec']['ingress'][0]['from'], [{'podSelector': {'matchLabels': {
                M.OWNER: TOKEN, M.ROLE: 'gateway'}}}])
        self.assertNotIn('namespaceSelector', json.dumps(self.objects[:4]))
        self.assertNotIn('ipBlock', json.dumps(self.objects[:4]))

    def test_private_secret_timeout_and_cluster_ips(self):
        secret = self.material['secret_data']['postgres-password']
        self.assertNotIn(secret, (self.output / 'fixture-receipt.json').read_text())
        self.assertEqual((self.output / 'fixture-receipt.json').stat().st_mode & 0o777, 0o600)
        for size in ('xs', 's'):
            self.assertIn('query.client.timeout=20s\n', self.material['public_data'][size + '.config.properties'])
            self.assertIn(F.name(TOKEN) + '-' + size, self.material['public_data'][size + '.node.properties'])
        records = F.backend_records(TOKEN, {'xs': '10.43.0.2', 's': '10.43.0.3'})
        self.assertEqual(records[0]['proxyTo'], 'http://10.43.0.2:18581')
        for ips in [{'xs': '127.0.0.1', 's': '10.43.0.3'}, {'xs': '8.8.8.8', 's': '10.43.0.3'},
                    {'xs': '10.43.0.2', 's': '10.43.0.2'}, {'xs': '10.43.0.2'}]:
            with self.assertRaises(ExperimentError):
                F.backend_records(TOKEN, ips)

    def test_saved_recovery_independent_of_current_renderer(self):
        with patch.object(M, 'gateway_pod_spec', side_effect=AssertionError), \
             patch.object(M, 'backend_pod_spec', side_effect=AssertionError), \
             patch.object(M.previous, 'pod_spec', side_effect=AssertionError):
            self.assertEqual(M.validate_plan(self.plan), self.objects)

    def test_created_workloads_and_service_default_normalization(self):
        M.assert_owned(self.dep_current, self.deployment, DEP_UID)
        M.assert_owned(self.svc_current, self.service, SVC_UID)
        gateway = current(self.objects[8], str(uuid.uuid4()))
        gateway['spec'] = pod_defaults(gateway['spec'])
        M.assert_owned(gateway, self.objects[8], gateway['metadata']['uid'])
        self.dep_current['spec']['replicas'] = 0
        with self.assertRaises(ExperimentError):
            M.assert_owned(self.dep_current, self.deployment, DEP_UID)

    def test_documented_memory_pressure_default_is_narrow(self):
        pod = copy.deepcopy(self.pod)
        pod['spec']['tolerations'].insert(0, {'key': 'node.kubernetes.io/memory-pressure',
                                             'operator': 'Exists', 'effect': 'NoSchedule'})
        M.assert_child(pod, self.deployment, RS_UID, POD_UID)
        pod['spec']['tolerations'][0]['key'] = ''
        with self.assertRaises(ExperimentError):
            M.assert_child(pod, self.deployment, RS_UID, POD_UID)

    def test_scale_patch_tests_uid_rv_and_current_replicas(self):
        patch_ops = M.scale_patch(self.dep_current, self.deployment, DEP_UID, 0)
        self.assertEqual([x['path'] for x in patch_ops], ['/metadata/uid', '/metadata/resourceVersion',
                                                       '/spec/replicas', '/spec/replicas'])
        self.assertEqual(patch_ops[-1]['value'], 0)
        for target in (True, -1, 2, '0'):
            with self.assertRaises(ExperimentError):
                M.scale_patch(self.dep_current, self.deployment, DEP_UID, target)
        with self.assertRaises(ExperimentError):
            M.scale_patch(self.dep_current, self.deployment, str(uuid.uuid4()), 0)
        self.dep_current['metadata'].pop('resourceVersion')
        with self.assertRaises(ExperimentError):
            M.scale_patch(self.dep_current, self.deployment, DEP_UID, 0)

    def test_children_verified_by_full_owner_chain_and_safe_projection(self):
        value = M.child_inventory(self.deployment, self.dep_current, [self.rs], [self.pod], deployment_uid=DEP_UID)
        self.assertEqual([c['parent_uid'] for c in value['children']], [DEP_UID, RS_UID])
        self.assertEqual(value['children'][1]['pod_ip'], '10.42.0.55')
        self.assertTrue(value['children'][1]['ready'])
        self.assertNotIn('spec', value['children'][1])
        self.assertNotIn('annotations', value['children'][1])

    def test_pod_generation_truncates_prefix_but_preserves_full_generate_name(self):
        self.assertEqual(len(self.pod['metadata']['name']), 63)
        self.assertGreater(len(self.pod['metadata']['generateName']), 58)
        self.assertFalse(self.pod['metadata']['name'].startswith(self.rs['metadata']['name'] + '-'))
        M.assert_child(self.pod, self.deployment, RS_UID, POD_UID)
        untruncated = copy.deepcopy(self.pod)
        untruncated['metadata']['name'] = self.rs['metadata']['name'] + '-a1b2c'
        with self.assertRaises(ExperimentError):
            M.assert_child(untruncated, self.deployment, RS_UID, POD_UID)
        bad_generate = copy.deepcopy(self.pod)
        bad_generate['metadata']['generateName'] = bad_generate['metadata']['generateName'][:58]
        with self.assertRaises(ExperimentError):
            M.assert_child(bad_generate, self.deployment, RS_UID, POD_UID)

    def test_observed_long_name_and_short_prefix_match_api_generator(self):
        rs_name = 'shirokuma-wl-cc7643f1aba44aa8a4e4e9f3aed2793c-xs-56cd9fdbbf'
        actual = 'shirokuma-wl-cc7643f1aba44aa8a4e4e9f3aed2793c-xs-56cd9fdbbfczqj'
        self.assertTrue(M._generated_name_matches(actual, rs_name + '-'))
        self.assertTrue(M._generated_name_matches('short-rs-a1b2c', 'short-rs-'))
        self.assertFalse(M._generated_name_matches(actual[:-1], rs_name + '-'))
        self.assertFalse(M._generated_name_matches('x' + actual[1:], rs_name + '-'))
        self.assertFalse(M._generated_name_matches(actual, rs_name.replace('cc7643', 'dd7643') + '-'))

    def test_matching_labels_do_not_adopt_foreign_parent(self):
        for kind in ('ReplicaSet', 'Pod'):
            rs, pod = copy.deepcopy(self.rs), copy.deepcopy(self.pod)
            (rs if kind == 'ReplicaSet' else pod)['metadata']['ownerReferences'][0]['uid'] = str(uuid.uuid4())
            with self.assertRaises(ExperimentError):
                M.child_inventory(self.deployment, self.dep_current, [rs], [pod], deployment_uid=DEP_UID)

    def test_extra_owner_reference_or_noncontroller_is_rejected(self):
        for mutation in ('extra', 'controller'):
            pod = copy.deepcopy(self.pod)
            if mutation == 'extra':
                pod['metadata']['ownerReferences'].append(copy.deepcopy(pod['metadata']['ownerReferences'][0]))
            else:
                pod['metadata']['ownerReferences'][0]['controller'] = False
            with self.assertRaises(ExperimentError):
                M.child_inventory(self.deployment, self.dep_current, [self.rs], [pod], deployment_uid=DEP_UID)

    def test_template_or_image_or_volume_or_privilege_drift_is_rejected(self):
        for mutation in ('annotation', 'image', 'volume', 'privilege', 'extra-container'):
            pod = copy.deepcopy(self.pod)
            if mutation == 'annotation':
                pod['metadata']['annotations'][M.TEMPLATE_HASH] = '0' * 64
            elif mutation == 'image':
                pod['spec']['containers'][0]['image'] = 'different'
            elif mutation == 'volume':
                pod['spec']['volumes'].append({'name': 'retained', 'persistentVolumeClaim': {'claimName': 'retained'}})
            elif mutation == 'privilege':
                pod['spec']['containers'][0]['securityContext']['privileged'] = True
            else:
                pod['spec']['containers'].append(copy.deepcopy(pod['spec']['containers'][0]))
            with self.assertRaises(ExperimentError):
                M.child_inventory(self.deployment, self.dep_current, [self.rs], [pod], deployment_uid=DEP_UID)

    def test_rs_template_drift_and_unknown_rs_pod_rejected(self):
        rs = copy.deepcopy(self.rs)
        rs['spec']['template']['spec']['containers'][0]['command'] = ['unexpected']
        with self.assertRaises(ExperimentError):
            M.child_inventory(self.deployment, self.dep_current, [rs], [self.pod], deployment_uid=DEP_UID)
        with self.assertRaises(ExperimentError):
            M.child_inventory(self.deployment, self.dep_current, [], [self.pod], deployment_uid=DEP_UID)

    def test_orphan_cleanup_requires_previously_recorded_exact_uid(self):
        pod = copy.deepcopy(self.pod)
        pod['metadata'].pop('ownerReferences')
        with self.assertRaises(ExperimentError):
            M.assert_child(pod, self.deployment, RS_UID)
        with self.assertRaises(ExperimentError):
            M.assert_child(pod, self.deployment, RS_UID, allow_orphan=True)
        with self.assertRaises(ExperimentError):
            M.assert_child(pod, self.deployment, RS_UID, str(uuid.uuid4()), allow_orphan=True)
        M.assert_child(pod, self.deployment, RS_UID, POD_UID, allow_orphan=True)
        rs = copy.deepcopy(self.rs)
        rs['metadata'].pop('ownerReferences')
        M.assert_child(rs, self.deployment, DEP_UID, RS_UID, allow_orphan=True)

    def test_child_replacement_after_journal_is_rejected(self):
        pod = copy.deepcopy(self.pod)
        pod['metadata']['uid'] = str(uuid.uuid4())
        with self.assertRaises(ExperimentError):
            M.assert_child(pod, self.deployment, RS_UID, POD_UID)

    def slice(self):
        return {'apiVersion': 'discovery.k8s.io/v1', 'kind': 'EndpointSlice',
                'metadata': {'name': self.service['metadata']['name'] + '-pqrst', 'namespace': M.NAMESPACE,
                    'uid': str(uuid.uuid4()), 'resourceVersion': '17', 'labels': {
                        'kubernetes.io/service-name': self.service['metadata']['name'],
                        'endpointslice.kubernetes.io/managed-by': 'endpointslice-controller.k8s.io'},
                    'ownerReferences': [{'apiVersion': 'v1', 'kind': 'Service', 'name': self.service['metadata']['name'],
                                         'uid': SVC_UID, 'controller': True, 'blockOwnerDeletion': True}]},
                'addressType': 'IPv4', 'ports': [{'name': 'xs', 'protocol': 'TCP', 'port': 18581}],
                'endpoints': [{'addresses': ['10.42.0.55'], 'conditions': {'ready': True, 'serving': True, 'terminating': False},
                    'nodeName': 'colima', 'targetRef': {'kind': 'Pod', 'namespace': M.NAMESPACE,
                        'name': self.pod['metadata']['name'], 'uid': POD_UID}}]}

    def test_endpoints_match_known_service_and_verified_ready_pod(self):
        result = M.validate_endpoints(self.service, self.svc_current, SVC_UID, [self.slice()], self.pod)
        self.assertTrue(result['verified'])
        self.assertEqual(result['pod_uid'], POD_UID)
        self.assertEqual(result['cluster_ip'], '10.43.0.22')

    def test_endpoints_reject_foreign_ips_uid_ports_extra_or_unready(self):
        for mutation in ('address', 'pod-uid', 'service-uid', 'port', 'extra', 'ready'):
            value = self.slice()
            if mutation == 'address':
                value['endpoints'][0]['addresses'] = ['10.42.0.99']
            elif mutation == 'pod-uid':
                value['endpoints'][0]['targetRef']['uid'] = str(uuid.uuid4())
            elif mutation == 'service-uid':
                value['metadata']['ownerReferences'][0]['uid'] = str(uuid.uuid4())
            elif mutation == 'port':
                value['ports'][0]['port'] = 18582
            elif mutation == 'extra':
                value['endpoints'].append(copy.deepcopy(value['endpoints'][0]))
            else:
                value['endpoints'][0]['conditions']['ready'] = False
            with self.assertRaises(ExperimentError):
                M.validate_endpoints(self.service, self.svc_current, SVC_UID, [value], self.pod)

    def test_endpoint_cleanup_accepts_empty_or_terminating_owned_targets(self):
        value = self.slice()
        value['endpoints'][0]['conditions'] = {'ready': False, 'serving': False, 'terminating': True}
        M.assert_endpoint(value, self.service, SVC_UID, value['metadata']['uid'], pod_uids=[POD_UID])
        value['endpoints'] = []
        M.assert_endpoint(value, self.service, SVC_UID, value['metadata']['uid'])
        value['metadata'].pop('ownerReferences')
        with self.assertRaises(ExperimentError):
            M.assert_endpoint(value, self.service, SVC_UID, value['metadata']['uid'])
        M.assert_endpoint(value, self.service, SVC_UID, value['metadata']['uid'], allow_orphan=True)

    def test_controller_empty_null_slice_is_cleanup_only(self):
        fixed_ports = self.slice()['ports']
        for endpoints in (None, []):
            for ports in (None, [], fixed_ports):
                value = self.slice()
                value.update(endpoints=endpoints, ports=ports)
                M.assert_endpoint(value, self.service, SVC_UID, value['metadata']['uid'])
                with self.assertRaises(ExperimentError):
                    M.validate_endpoints(self.service, self.svc_current, SVC_UID, [value], self.pod)
        for ports in (None, [], [{'name': 'xs', 'protocol': 'TCP', 'port': 18582}]):
            value = self.slice()
            value['ports'] = ports
            with self.assertRaises(ExperimentError):
                M.assert_endpoint(value, self.service, SVC_UID, value['metadata']['uid'], pod_uids=[POD_UID])
        value = self.slice()
        value.update(endpoints=None, ports=[{'name': 'xs', 'protocol': 'TCP', 'port': 18582}])
        with self.assertRaises(ExperimentError):
            M.assert_endpoint(value, self.service, SVC_UID, value['metadata']['uid'])

    def test_endpoint_cleanup_refuses_unknown_pod_even_with_correct_service(self):
        value = self.slice()
        with self.assertRaises(ExperimentError):
            M.assert_endpoint(value, self.service, SVC_UID, value['metadata']['uid'])
        with self.assertRaises(ExperimentError):
            M.assert_endpoint(value, self.service, SVC_UID, str(uuid.uuid4()), pod_uids=[POD_UID])
        value['endpoints'][0]['targetRef']['uid'] = str(uuid.uuid4())
        with self.assertRaises(ExperimentError):
            M.assert_endpoint(value, self.service, SVC_UID, value['metadata']['uid'], pod_uids=[POD_UID])

    def test_endpoint_long_generated_name_keeps_uid_and_run_bounds(self):
        value = self.slice()
        target = value['endpoints'][0]['targetRef']
        self.assertEqual(len(target['name']), 63)
        M.assert_endpoint(value, self.service, SVC_UID, value['metadata']['uid'], pod_uids=[POD_UID])
        for field, replacement in [('uid', str(uuid.uuid4())), ('name', 'x' + target['name'][1:]),
                                   ('name', self.rs['metadata']['name'] + '-a1b2c')]:
            bad = copy.deepcopy(value)
            bad['endpoints'][0]['targetRef'][field] = replacement
            with self.assertRaises(ExperimentError):
                M.assert_endpoint(bad, self.service, SVC_UID, bad['metadata']['uid'], pod_uids=[POD_UID])

    def test_service_selector_and_external_exposure_drift_rejected(self):
        for mutation in ('selector', 'external', 'type'):
            svc = copy.deepcopy(self.svc_current)
            if mutation == 'selector':
                svc['spec']['selector'][M.OWNER] = 'f' * 32
            elif mutation == 'external':
                svc['spec']['externalIPs'] = ['127.0.0.1']
            else:
                svc['spec']['type'] = 'NodePort'
            with self.assertRaises(ExperimentError):
                M.assert_owned(svc, self.service, SVC_UID)

    def test_resealed_saved_plan_does_not_gain_host_or_extra_network_access(self):
        for mutation in ('host', 'network'):
            plan = copy.deepcopy(self.plan)
            obj = plan['objects'][-2] if mutation == 'host' else plan['objects'][1]
            if mutation == 'host':
                obj['spec']['template']['spec']['hostNetwork'] = True
                template = copy.deepcopy(obj['spec']['template'])
                template['metadata'].pop('annotations')
                obj['spec']['template']['metadata']['annotations'][M.TEMPLATE_HASH] = digest(template)
            else:
                obj['spec']['egress'].append({})
            raw = copy.deepcopy(obj)
            raw['metadata'].pop('annotations')
            obj['metadata']['annotations'][M.HASH] = digest(raw)
            with self.assertRaises(ExperimentError):
                M.validate_plan(plan)


if __name__ == '__main__':
    unittest.main()
