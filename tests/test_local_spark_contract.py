"""Offline evidence and resource boundaries for the fixed Spark experiment."""
import copy
import json
import unittest

from tools.local_experiment.spark import contract as C


TOKEN = '0123456789abcdef0123456789abcdef'
API = [{'ip': '10.43.0.1', 'port': 443}, {'ip': '192.168.5.1', 'port': 55737}]
EXPECTED_GROUPS = [{'bucket': 0, 'count': 5, 'total': 567},
                   {'bucket': 1, 'count': 6, 'total': 672},
                   {'bucket': 2, 'count': 5, 'total': 553},
                   {'bucket': 3, 'count': 5, 'total': 672}]


def result(mode='success'):
    value = C.expected_output(TOKEN, mode)
    value['partitions'] = [
        {'partition_id': number, 'stage_id': 2, 'task_attempt_id': 4 + number,
         'attempt_number': 0, 'executor_id': '1', 'hostname': C.executor_name(TOKEN)}
        for number in range(2)]
    return value


def validate(value, mode='success', executor=None):
    return C.validate_output(value, TOKEN, mode,
                             executor_pod_name=executor or C.executor_name(TOKEN))


def objects(mode='success'):
    return C.render(TOKEN, mode, api_endpoints=API)


def one(kind, name=None):
    return next(item for item in objects() if item['kind'] == kind
                and (name is None or item['metadata']['name'] == name))


class ManifestTests(unittest.TestCase):
    def test_shutdown_leaves_host_owned_service_and_never_requests_pvc_cleanup(self):
        # Spark's defaults perform deletecollection even when no PVC was used.
        # Keep those actions in the host UID cleanup, with no broader RBAC.
        properties = dict(line.split(' ', 1) for line in one('ConfigMap')['data']['spark-defaults.conf'].splitlines())
        for key in ('spark.kubernetes.driver.service.deleteOnTermination',
                    'spark.kubernetes.driver.ownPersistentVolumeClaim',
                    'spark.kubernetes.executor.deleteOnTermination'):
            self.assertEqual(properties[key], 'false')
        self.assertEqual(properties['spark.kubernetes.executor.disableConfigMap'], 'true')
        self.assertTrue(all('persistentvolumeclaims' not in rule['resources']
                            and 'deletecollection' not in rule['verbs'] for rule in one('Role')['rules']))

    def test_pinned_native_image_no_pull_and_fixed_psa(self):
        image = 'docker.io/library/spark@sha256:82d7d3c2315a8b826d83485728013c01d1f2542708c83c0c641a363924d065b7'
        self.assertEqual(C.IMAGE, image)
        labels = one('Namespace')['metadata']['labels']
        for action in ('enforce', 'audit', 'warn'):
            self.assertEqual(labels['pod-security.kubernetes.io/' + action], 'restricted')
            self.assertEqual(labels['pod-security.kubernetes.io/' + action + '-version'], 'v1.33')
        for pod in (one('Pod'), C.executor_template(TOKEN)):
            self.assertEqual(pod['spec']['nodeSelector'],
                             {'kubernetes.io/arch': 'arm64', 'kubernetes.io/os': 'linux'})
            container = pod['spec']['containers'][0]
            self.assertEqual(container['image'], image)
            self.assertEqual(container['imagePullPolicy'], 'Never')

    def test_total_resource_caps_include_heap_and_overhead(self):
        quota = one('ResourceQuota')['spec']['hard']
        self.assertEqual({key: quota[key] for key in ('pods', 'requests.cpu', 'limits.cpu',
                                                     'requests.memory', 'limits.memory')},
                         {'pods': '2', 'requests.cpu': '2', 'limits.cpu': '2',
                          'requests.memory': '4Gi', 'limits.memory': '4Gi'})
        for role, pod in [('driver', one('Pod')), ('executor', C.executor_template(TOKEN))]:
            limits = pod['spec']['containers'][0]['resources']['limits']
            self.assertEqual(limits['cpu'], '1')
            self.assertEqual(limits['memory'], '2Gi')
            self.assertEqual(C.spark_properties(TOKEN)['spark.' + role + '.memory'], '1g')
            self.assertEqual(C.spark_properties(TOKEN)['spark.' + role + '.memoryOverhead'], '1g')
        self.assertEqual(C.spark_properties(TOKEN)['spark.executor.instances'], '1')
        self.assertEqual(C.spark_properties(TOKEN)['spark.dynamicAllocation.enabled'], 'false')

    def test_rbac_is_only_driver_namespace_and_no_secret_or_storage(self):
        role = one('Role')
        self.assertEqual(role['metadata']['namespace'], C.namespace(TOKEN))
        self.assertEqual(role['rules'], [{'apiGroups': [''], 'resources': ['pods', 'services', 'configmaps'],
                                          'verbs': ['get', 'list', 'watch', 'create', 'delete']}])
        binding = one('RoleBinding')
        self.assertEqual(binding['subjects'], [{'kind': 'ServiceAccount', 'name': 'driver',
                                               'namespace': C.namespace(TOKEN)}])
        self.assertTrue(one('ServiceAccount', 'driver')['automountServiceAccountToken'])
        self.assertFalse(one('ServiceAccount', 'executor')['automountServiceAccountToken'])
        self.assertFalse(C.executor_template(TOKEN)['spec']['automountServiceAccountToken'])
        self.assertFalse({'Secret', 'PersistentVolumeClaim', 'ClusterRole', 'ClusterRoleBinding'}
                         & {obj['kind'] for obj in objects()})
        for obj in objects()[1:]:
            self.assertEqual(obj['metadata']['namespace'], C.namespace(TOKEN))

    def test_pod_security_and_writable_paths_are_bounded(self):
        for pod in (one('Pod'), C.executor_template(TOKEN)):
            spec = pod['spec']
            self.assertEqual(spec['restartPolicy'], 'Never')
            self.assertEqual(spec['activeDeadlineSeconds'], 240)
            self.assertTrue(spec['securityContext']['runAsNonRoot'])
            self.assertEqual(spec['securityContext']['runAsUser'], 185)
            self.assertEqual(spec['securityContext']['seccompProfile'], {'type': 'RuntimeDefault'})
            container = spec['containers'][0]
            self.assertFalse(container['securityContext']['allowPrivilegeEscalation'])
            self.assertTrue(container['securityContext']['readOnlyRootFilesystem'])
            self.assertEqual(container['securityContext']['capabilities'], {'drop': ['ALL']})
            self.assertTrue(all(set(volume) in ({'name', 'emptyDir'}, {'name', 'configMap'})
                                for volume in spec['volumes']))
            mounts = {mount['mountPath'] for mount in container['volumeMounts']}
            self.assertTrue({'/opt/spark/work-dir', '/tmp', '/var/data/spark-local'} <= mounts)
            self.assertNotIn('hostNetwork', spec)
            self.assertNotIn('hostPID', spec)
        self.assertTrue(one('ConfigMap')['immutable'])

    def test_network_only_same_run_fixed_rpc_dns_and_observed_api(self):
        policies = C.network_policies(TOKEN, API)
        self.assertEqual(policies[0]['spec']['ingress'], [])
        self.assertEqual(policies[0]['spec']['egress'], [])
        rpc = policies[1]['spec']
        self.assertEqual(rpc['ingress'][0]['from'], [{'podSelector': {'matchLabels': C.labels(TOKEN)}}])
        self.assertEqual([port['port'] for port in rpc['ingress'][0]['ports']], [7078, 7079])
        dns = policies[2]['spec']['egress'][0]
        self.assertEqual(dns['to'][0]['namespaceSelector']['matchLabels'],
                         {'kubernetes.io/metadata.name': 'kube-system'})
        api = policies[3]['spec']
        self.assertEqual(api['podSelector'], {'matchLabels': C.labels(TOKEN, 'driver')})
        self.assertEqual(api['egress'], [
            {'to': [{'ipBlock': {'cidr': '10.43.0.1/32'}}], 'ports': [{'protocol': 'TCP', 'port': 443}]},
            {'to': [{'ipBlock': {'cidr': '192.168.5.1/32'}}], 'ports': [{'protocol': 'TCP', 'port': 55737}]}])
        self.assertEqual(one('Service')['spec']['clusterIP'], 'None')
        self.assertEqual(one('Service')['spec']['selector'], C.labels(TOKEN, 'driver'))

    def test_missing_api_addresses_are_plan_only(self):
        plan = C.render(TOKEN, 'success')
        self.assertEqual(len(plan), 13)
        self.assertNotIn('driver-api', [item['metadata']['name'] for item in plan])
        self.assertEqual(len(objects()), 14)

    def test_unsafe_or_ambiguous_api_targets_rejected(self):
        for endpoints in ([], API + API, [{'ip': '0.0.0.0', 'port': 443}],
                          [{'ip': '127.0.0.1', 'port': 443}], [{'ip': '8.8.8.8', 'port': 443}],
                          [{'ip': '169.254.1.1', 'port': 443}], [{'ip': '::1', 'port': 443}],
                          [{'ip': '10.43.0.1/16', 'port': 443}], [{'ip': '10.43.0.1', 'port': 0}],
                          [{'ip': '10.43.0.1', 'port': -1}], [{'ip': '10.43.0.1', 'port': 65536}],
                          [{'ip': '10.43.0.1', 'port': '55737'}],
                          [{'ip': '10.43.0.1', 'port': True}], [{'ip': '10.43.0.1', 'port': 443, 'extra': 1}]):
            with self.subTest(endpoints=endpoints), self.assertRaises(C.ContractError):
                C.render(TOKEN, 'success', api_endpoints=endpoints)

    def test_invalid_run_or_mode_cannot_change_workload_scope(self):
        for token in ('a' * 31, 'A' * 32, '../' + 'a' * 29, None, 1):
            with self.subTest(token=token), self.assertRaises(C.ContractError):
                C.render(token, 'success')
        for mode in ('unknown', 'success;command', 0, None):
            with self.subTest(mode=mode), self.assertRaises(C.ContractError):
                C.render(TOKEN, mode)

    def test_saved_plan_tamper_and_boolean_integer_equivalence_rejected(self):
        original = objects()
        self.assertEqual(C.validate_plan(original, TOKEN, 'success', api_endpoints=API), original)
        for mutate in (lambda value: value[-1]['spec'].__setitem__('hostNetwork', True),
                       lambda value: value[-1]['spec']['containers'][0].__setitem__('image', 'spark:latest'),
                       lambda value: value[-1]['spec']['containers'][0]['volumeMounts'].append({'name': 'host', 'mountPath': '/'}),
                       lambda value: value[-2]['spec']['egress'][0]['ports'][0].__setitem__('port', True)):
            altered = copy.deepcopy(original)
            mutate(altered)
            with self.assertRaises(C.ContractError):
                C.validate_plan(altered, TOKEN, 'success', api_endpoints=API)
        self.assertEqual(C.plan_sha256(original), C.plan_sha256(objects()))
        self.assertNotEqual(C.plan_sha256(original), C.plan_sha256(objects('failure')))

    def test_generated_script_compiles_and_exit_mode_follows_evidence(self):
        for mode, exit_code in [('success', 0), ('failure', 17)]:
            script = C.job_script(TOKEN, mode)
            compile(script, '<fixed-spark-workload>', 'exec')
            self.assertLess(script.index('.collect()'), script.index('print('))
            self.assertLess(script.index('print('), script.index('spark.stop()'))
            self.assertLess(script.index('spark.stop()'), script.index('sys.exit('))
            self.assertIn('sys.exit(' + str(exit_code) + ')', script)
            self.assertEqual(C.expected_exit_code(mode), exit_code)

    def test_heartbeat_check_interval_fits_bounded_network_timeout(self):
        # Spark 4.1.3 HeartbeatReceiver rejects the default 60s interval with a 45s timeout.
        properties = C.spark_properties(TOKEN)
        timeout = int(properties['spark.network.timeout'].removesuffix('s'))
        check = int(properties['spark.network.timeoutInterval'].removesuffix('s'))
        heartbeat = int(properties['spark.executor.heartbeatInterval'].removesuffix('s'))
        self.assertGreater(check, 0)
        self.assertGreater(heartbeat, 0)
        self.assertLessEqual(check, timeout)
        self.assertLessEqual(heartbeat, timeout)


class OutputTests(unittest.TestCase):
    def test_oracle_is_fixed_and_both_modes_require_executor_evidence(self):
        self.assertEqual(C.oracle(), EXPECTED_GROUPS)
        self.assertEqual(C.expected_output(TOKEN, 'success')['filtered_rows'], 21)
        for mode in ('success', 'failure'):
            value = result(mode)
            self.assertEqual(validate(value, mode), value)
            value.pop('partitions')
            with self.assertRaises(C.ContractError):
                validate(value, mode)

    def test_result_cannot_claim_other_mode_token_namespace_version_image_or_rows(self):
        for key, bad in [('mode', 'failure'), ('run_token', 'f' * 32), ('namespace', 'shirokuma-dev'),
                         ('spark_version', '4.1.2'), ('configured_image', 'spark:latest'),
                         ('source_rows', 31), ('filtered_rows', 22), ('shuffle_observed', False)]:
            value = result()
            value[key] = bad
            with self.subTest(key=key), self.assertRaises(C.ContractError):
                validate(value)

    def test_group_types_and_oracle_mismatch_rejected(self):
        for change in (lambda row: row.__setitem__('bucket', False),
                       lambda row: row.__setitem__('count', 6),
                       lambda row: row.__setitem__('total', 567.0),
                       lambda row: row.__setitem__('extra', 1)):
            value = result()
            change(value['groups'][0])
            with self.assertRaises(C.ContractError):
                validate(value)

    def test_driver_only_or_unrecognized_executor_rejected(self):
        for field, identity in [('executor_id', 'driver'), ('executor_id', '2'), ('hostname', 'driver'),
                                ('hostname', 'other-executor')]:
            value = result()
            value['partitions'][0][field] = identity
            with self.subTest(field=field, identity=identity), self.assertRaises(C.ContractError):
                validate(value)
        with self.assertRaises(C.ContractError):
            validate(result(), executor='some-owned-but-unexpected-executor')

    def test_partition_and_task_identity_must_be_complete_unique_first_attempt(self):
        for change in (lambda rows: rows.pop(), lambda rows: rows.append(copy.deepcopy(rows[0])),
                       lambda rows: rows[1].__setitem__('partition_id', 0),
                       lambda rows: rows[1].__setitem__('task_attempt_id', 4),
                       lambda rows: rows[1].__setitem__('stage_id', 3),
                       lambda rows: rows[0].__setitem__('attempt_number', 1),
                       lambda rows: rows[0].__setitem__('stage_id', True),
                       lambda rows: rows[0].__setitem__('task_attempt_id', -1)):
            value = result()
            change(value['partitions'])
            with self.assertRaises(C.ContractError):
                validate(value)

    def test_exactly_one_bounded_json_result_line(self):
        line = C.RESULT_PREFIX + json.dumps(result())
        kwargs = {'executor_pod_name': C.executor_name(TOKEN)}
        self.assertEqual(C.parse_output('INFO harmless\n' + line + '\n', TOKEN, 'success', **kwargs), result())
        for logs in ('INFO only', line + '\n' + line, 'x' * (C.MAX_LOG_BYTES + 1), '\ud800'):
            with self.subTest(size=len(logs)), self.assertRaises(C.ContractError):
                C.parse_output(logs, TOKEN, 'success', **kwargs)

    def test_duplicate_keys_nan_and_arbitrary_result_fields_rejected(self):
        line = json.dumps(result())
        invalid = [line[:-1] + ',"schema_version":1}', line.replace('"source_rows": 32', '"source_rows": NaN'),
                   line[:-1] + ',"raw_query":"anything"}', '{not json}']
        for data in invalid:
            with self.subTest(data=data[:40]), self.assertRaises(C.ContractError):
                C.parse_output(C.RESULT_PREFIX + data, TOKEN, 'success', executor_pod_name=C.executor_name(TOKEN))


if __name__ == '__main__':
    unittest.main()
