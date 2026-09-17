"""Pure fixed Spark workload and manifest contract; performs no cluster or image I/O.

render without API endpoints is a plan only: default-deny blocks the Kubernetes
API, so it must not be described as ready to run. The host supplies read-only
observed API service/endpoint addresses and later verifies real Pod UIDs, image
IDs, exit status and cleanup. Job output alone is not runtime attestation.
"""
from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import re
import textwrap

VERSION = '4.1.3'
IMAGE = 'docker.io/library/spark@sha256:82d7d3c2315a8b826d83485728013c01d1f2542708c83c0c641a363924d065b7'
OWNER = 'shirokuma-local-spark'
TOKEN = re.compile(r'[0-9a-f]{32}')
RESULT_PREFIX = 'SHIROKUMA_SPARK_RESULT='
ROWS = 32
PARTITIONS = 2
MAX_LOG_BYTES = 256 * 1024


class ContractError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise ContractError(code)


def validate_token(token):
    require(type(token) is str and TOKEN.fullmatch(token), 'INVALID_RUN_TOKEN')
    return token


def validate_mode(mode):
    require(type(mode) is str and mode in ('success', 'failure'), 'INVALID_JOB_MODE')
    return mode


def namespace(token):
    return 'shirokuma-spark-' + validate_token(token)


def executor_name(token):
    return 'spark-' + validate_token(token) + '-exec-1'


def labels(token, role=None):
    result = {'shirokuma.io/experiment': 'spark-native', 'shirokuma.io/run-token': validate_token(token)}
    if role is not None:
        require(role in ('driver', 'executor'), 'INVALID_ROLE')
        result['shirokuma.io/role'] = role
    return result


def metadata(token, name, *, role=None, cluster=False):
    result = {'name': name, 'labels': labels(token, role),
              'annotations': {'shirokuma.io/owner': OWNER}}
    if not cluster:
        result['namespace'] = namespace(token)
    return result


def oracle():
    """Independent Python arithmetic; no Spark or SQL dependency."""
    groups = {bucket: {'bucket': bucket, 'count': 0, 'total': 0} for bucket in range(4)}
    for identity in range(ROWS):
        if identity % 3 != 0:
            group = groups[identity % 4]
            group['count'] += 1
            group['total'] += (identity + 1) * 7
    return list(groups.values())


def expected_exit_code(mode):
    return 0 if validate_mode(mode) == 'success' else 17


def expected_output(token, mode):
    groups = oracle()
    return {'schema_version': 1, 'run_token': validate_token(token), 'namespace': namespace(token),
            'mode': validate_mode(mode), 'result': 'passed' if mode == 'success' else 'controlled-failure',
            'spark_version': VERSION, 'configured_image': IMAGE, 'source_rows': ROWS,
            'filtered_rows': sum(group['count'] for group in groups), 'groups': groups,
            'shuffle_observed': True}


def job_script(token, mode):
    """Fixed DataFrame aggregation; executor Python tasks return only typed evidence."""
    validate_token(token)
    validate_mode(mode)
    return textwrap.dedent(f'''\
        import json
        import os
        import socket
        import sys
        from pyspark import TaskContext
        from pyspark.sql import SparkSession, functions as F

        def observe_partition(partition_id, rows):
            context = TaskContext.get()
            if context is None:
                raise RuntimeError('EXECUTOR_TASK_CONTEXT_MISSING')
            yield {{'partition_id': partition_id, 'stage_id': context.stageId(),
                   'task_attempt_id': context.taskAttemptId(), 'attempt_number': context.attemptNumber(),
                   'executor_id': os.environ.get('SPARK_EXECUTOR_ID'),
                   'hostname': socket.gethostname(),
                   'rows': [{{'bucket': row.bucket, 'count': row['count'], 'total': row.total}} for row in rows]}}

        spark = SparkSession.builder.appName('shirokuma-spark-{token}').getOrCreate()
        try:
            source = spark.range(0, {ROWS}, 1, 4)
            filtered = source.where(F.col('id') % 3 != 0).select(
                (F.col('id') % 4).alias('bucket'), ((F.col('id') + 1) * 7).alias('value'))
            grouped = filtered.repartition({PARTITIONS}, 'bucket').groupBy('bucket').agg(
                F.count('*').alias('count'), F.sum('value').alias('total'))
            shuffled = 'Exchange' in grouped._jdf.queryExecution().executedPlan().toString()
            packets = grouped.rdd.mapPartitionsWithIndex(observe_partition).collect()
            groups = sorted((row for packet in packets for row in packet['rows']), key=lambda row: row['bucket'])
            partitions = sorted(({{key: value for key, value in packet.items() if key != 'rows'}} for packet in packets),
                                key=lambda packet: packet['partition_id'])
            result = {{'schema_version': 1, 'run_token': '{token}', 'namespace': '{namespace(token)}',
                      'mode': '{mode}', 'result': '{'passed' if mode == 'success' else 'controlled-failure'}',
                      'spark_version': spark.version,
                      'configured_image': spark.sparkContext.getConf().get('spark.kubernetes.container.image'),
                      'source_rows': {ROWS}, 'filtered_rows': sum(row['count'] for row in groups),
                      'groups': groups, 'shuffle_observed': shuffled, 'partitions': partitions}}
            print('{RESULT_PREFIX}' + json.dumps(result, sort_keys=True, separators=(',', ':')), flush=True)
        finally:
            spark.stop()
        sys.exit({expected_exit_code(mode)})
        ''')


def validate_output(value, token, mode, *, executor_pod_name):
    expected = expected_output(token, mode)
    require(type(value) is dict and set(value) == set(expected) | {'partitions'}, 'INVALID_RESULT_FIELDS')
    require(type(executor_pod_name) is str and executor_pod_name == executor_name(token), 'UNEXPECTED_EXECUTOR_IDENTITY')
    for key, wanted in expected.items():
        require(type(value[key]) is type(wanted) and value[key] == wanted, 'RESULT_ORACLE_MISMATCH')
    require(all(type(row) is dict and set(row) == {'bucket', 'count', 'total'}
                and all(type(x) is int for x in row.values()) for row in value['groups']), 'INVALID_GROUP_TYPES')
    rows = value['partitions']
    require(type(rows) is list and len(rows) == PARTITIONS, 'PARTITION_EVIDENCE_MISSING')
    task_ids, stages = set(), set()
    for index, row in enumerate(rows):
        require(type(row) is dict and set(row) == {'partition_id', 'stage_id', 'task_attempt_id', 'attempt_number',
                                                  'executor_id', 'hostname'}, 'INVALID_TASK_FIELDS')
        require(all(type(row[key]) is int and 0 <= row[key] <= 2147483647
                    for key in ('partition_id', 'stage_id', 'task_attempt_id', 'attempt_number')), 'INVALID_TASK_NUMBERS')
        require(row['partition_id'] == index and row['attempt_number'] == 0
                and row['executor_id'] == '1' and row['hostname'] == executor_pod_name, 'EXECUTOR_TASK_MISMATCH')
        task_ids.add(row['task_attempt_id'])
        stages.add(row['stage_id'])
    require(len(task_ids) == PARTITIONS and len(stages) == 1, 'DUPLICATE_OR_MIXED_TASK_EVIDENCE')
    return copy.deepcopy(value)


def parse_output(logs, token, mode, *, executor_pod_name):
    require(type(logs) is str, 'JOB_LOG_BOUND')
    try:
        length = len(logs.encode('utf-8'))
    except UnicodeError:
        raise ContractError('JOB_LOG_ENCODING') from None
    require(length <= MAX_LOG_BYTES, 'JOB_LOG_BOUND')
    lines = [line[len(RESULT_PREFIX):] for line in logs.splitlines() if line.startswith(RESULT_PREFIX)]
    require(len(lines) == 1 and len(lines[0]) <= 8192, 'RESULT_LINE_COUNT')
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, 'DUPLICATE_RESULT_KEY')
            result[key] = value
        return result
    try:
        value = json.loads(lines[0], object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ContractError('NONFINITE_RESULT')))
    except (ValueError, UnicodeError):
        raise ContractError('INVALID_RESULT_JSON') from None
    return validate_output(value, token, mode, executor_pod_name=executor_pod_name)


def security_context():
    return {'runAsNonRoot': True, 'runAsUser': 185, 'runAsGroup': 185, 'fsGroup': 185,
            'seccompProfile': {'type': 'RuntimeDefault'}}


def container_security():
    return {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}}


def resources(role):
    return {'requests': {'cpu': '500m' if role == 'driver' else '1', 'memory': '2Gi', 'ephemeral-storage': '256Mi'},
            'limits': {'cpu': '1', 'memory': '2Gi', 'ephemeral-storage': '2Gi'}}


def pod_spec(role):
    return {'restartPolicy': 'Never', 'activeDeadlineSeconds': 240, 'terminationGracePeriodSeconds': 15,
            'serviceAccountName': role, 'automountServiceAccountToken': role == 'driver',
            'enableServiceLinks': False, 'nodeSelector': {'kubernetes.io/arch': 'arm64', 'kubernetes.io/os': 'linux'},
            'securityContext': security_context(),
            'volumes': [{'name': 'work', 'emptyDir': {'sizeLimit': '1Gi'}},
                        {'name': 'tmp', 'emptyDir': {'sizeLimit': '256Mi'}},
                        {'name': 'spark-local-dir-1', 'emptyDir': {'sizeLimit': '512Mi'}}],
            'containers': [{'name': 'spark-' + role, 'image': IMAGE, 'imagePullPolicy': 'Never',
                            'securityContext': container_security(), 'resources': resources(role),
                            'workingDir': '/opt/spark/work-dir',
                            'env': [{'name': 'HOME', 'value': '/opt/spark/work-dir'},
                                    {'name': 'TMPDIR', 'value': '/tmp'},
                                    {'name': 'PYTHONDONTWRITEBYTECODE', 'value': '1'}],
                            'volumeMounts': [{'name': 'work', 'mountPath': '/opt/spark/work-dir'},
                                             {'name': 'tmp', 'mountPath': '/tmp'},
                                             {'name': 'spark-local-dir-1', 'mountPath': '/var/data/spark-local'}]}]}


def executor_template(token):
    return {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': metadata(token, 'executor-template', role='executor'),
            'spec': pod_spec('executor')}


def spark_properties(token):
    ns = namespace(token)
    return {'spark.master': 'k8s://https://kubernetes.default.svc:443', 'spark.submit.deployMode': 'client',
            'spark.kubernetes.namespace': ns, 'spark.kubernetes.driver.pod.name': 'driver',
            'spark.kubernetes.executor.podNamePrefix': 'spark-' + token,
            'spark.kubernetes.container.image': IMAGE, 'spark.kubernetes.container.image.pullPolicy': 'Never',
            'spark.kubernetes.executor.podTemplateFile': '/opt/shirokuma/executor-template.json',
            'spark.kubernetes.executor.podTemplateContainerName': 'spark-executor',
            'spark.kubernetes.authenticate.executor.serviceAccountName': 'executor',
            'spark.kubernetes.executor.disableConfigMap': 'true',
            'spark.kubernetes.executor.deleteOnTermination': 'false',
            'spark.kubernetes.driver.service.deleteOnTermination': 'false',
            'spark.kubernetes.driver.ownPersistentVolumeClaim': 'false',
            'spark.kubernetes.executor.terminationGracePeriodSeconds': '15',
            'spark.kubernetes.driver.annotateExitException': 'false', 'spark.kubernetes.trust.certificates': 'false',
            'spark.executor.instances': '1', 'spark.dynamicAllocation.enabled': 'false', 'spark.executor.cores': '1',
            'spark.kubernetes.executor.request.cores': '1', 'spark.kubernetes.executor.limit.cores': '1',
            'spark.driver.memory': '1g', 'spark.driver.memoryOverhead': '1g',
            'spark.executor.memory': '1g', 'spark.executor.memoryOverhead': '1g',
            'spark.kubernetes.allocation.batch.size': '1', 'spark.task.maxFailures': '1',
            'spark.driver.host': 'driver.' + ns + '.svc', 'spark.driver.bindAddress': '0.0.0.0',
            'spark.driver.port': '7078', 'spark.blockManager.port': '7079', 'spark.port.maxRetries': '0',
            'spark.network.timeout': '45s', 'spark.network.timeoutInterval': '10s',
            'spark.executor.heartbeatInterval': '10s',
            'spark.kubernetes.driver.connectionTimeout': '10000', 'spark.kubernetes.driver.requestTimeout': '10000',
            'spark.sql.shuffle.partitions': str(PARTITIONS), 'spark.sql.adaptive.enabled': 'false',
            'spark.local.dir': '/var/data/spark-local', 'spark.sql.warehouse.dir': '/tmp/warehouse',
            'spark.ui.enabled': 'false', 'spark.eventLog.enabled': 'false',
            'spark.pyspark.python': 'python3', 'spark.pyspark.driver.python': 'python3'}


def network_policies(token, api_endpoints=None):
    """No endpoints means API-denied plan. Endpoints are observed host input, never model input."""
    peers = {'podSelector': {'matchLabels': labels(token)}}
    def policy(name, selector, **rules):
        return {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy', 'metadata': metadata(token, name),
                'spec': {'podSelector': selector, 'policyTypes': ['Ingress', 'Egress'], **rules}}
    ports = [{'protocol': 'TCP', 'port': number} for number in (7078, 7079)]
    result = [policy('default-deny', {}, ingress=[], egress=[]),
              policy('same-run-rpc', {'matchLabels': labels(token)},
                     ingress=[{'from': [peers], 'ports': ports}], egress=[{'to': [peers], 'ports': ports}]),
              policy('cluster-dns', {'matchLabels': labels(token)}, ingress=[], egress=[{
                  'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}},
                          'podSelector': {'matchLabels': {'k8s-app': 'kube-dns'}}}],
                  'ports': [{'protocol': protocol, 'port': 53} for protocol in ('TCP', 'UDP')]}])]
    if api_endpoints is None:
        return result
    require(type(api_endpoints) is list and 1 <= len(api_endpoints) <= 4, 'INVALID_API_ENDPOINTS')
    endpoints = set()
    for endpoint in api_endpoints:
        require(type(endpoint) is dict and set(endpoint) == {'ip', 'port'}, 'INVALID_API_ENDPOINT')
        try:
            address = ipaddress.IPv4Address(endpoint['ip'])
        except (ValueError, TypeError):
            raise ContractError('INVALID_API_ADDRESS') from None
        require(type(endpoint['ip']) is str and str(address) == endpoint['ip'] and address.is_private
                and not (address.is_loopback or address.is_unspecified or address.is_link_local or address.is_multicast)
                and type(endpoint['port']) is int and 1 <= endpoint['port'] <= 65535, 'UNSAFE_API_ENDPOINT')
        endpoints.add((str(address), endpoint['port']))
    require(len(endpoints) == len(api_endpoints), 'DUPLICATE_API_ENDPOINT')
    result.append(policy('driver-api', {'matchLabels': labels(token, 'driver')}, ingress=[], egress=[
        {'to': [{'ipBlock': {'cidr': ip + '/32'}}], 'ports': [{'protocol': 'TCP', 'port': port}]}
        for ip, port in sorted(endpoints)]))
    return result


def render(token, mode, *, api_endpoints=None):
    """Fixed objects only. Missing API endpoints intentionally produces a non-runnable plan."""
    ns = namespace(token)
    validate_mode(mode)
    namespace_meta = metadata(token, ns, cluster=True)
    namespace_meta['labels'].update({f'pod-security.kubernetes.io/{action}': 'restricted' for action in ('enforce', 'audit', 'warn')})
    namespace_meta['labels'].update({f'pod-security.kubernetes.io/{action}-version': 'v1.33' for action in ('enforce', 'audit', 'warn')})
    result = [{'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': namespace_meta},
              {'apiVersion': 'v1', 'kind': 'ResourceQuota', 'metadata': metadata(token, 'experiment-cap'),
               'spec': {'hard': {'pods': '2', 'requests.cpu': '2', 'limits.cpu': '2', 'requests.memory': '4Gi',
                                 'limits.memory': '4Gi', 'requests.ephemeral-storage': '4Gi', 'limits.ephemeral-storage': '4Gi',
                                 'services': '1', 'persistentvolumeclaims': '0', 'secrets': '0'}}},
              {'apiVersion': 'v1', 'kind': 'LimitRange', 'metadata': metadata(token, 'container-cap'),
               'spec': {'limits': [{'type': 'Container', 'max': {'cpu': '1', 'memory': '2Gi', 'ephemeral-storage': '2Gi'},
                                   'default': {'cpu': '1', 'memory': '2Gi', 'ephemeral-storage': '2Gi'},
                                   'defaultRequest': {'cpu': '500m', 'memory': '2Gi', 'ephemeral-storage': '256Mi'}}]}}]
    result.extend({'apiVersion': 'v1', 'kind': 'ServiceAccount', 'metadata': metadata(token, role),
                   'automountServiceAccountToken': role == 'driver'} for role in ('driver', 'executor'))
    result.extend([
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'Role', 'metadata': metadata(token, 'spark-driver'),
         'rules': [{'apiGroups': [''], 'resources': ['pods', 'services', 'configmaps'],
                    'verbs': ['get', 'list', 'watch', 'create', 'delete']}]},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'RoleBinding', 'metadata': metadata(token, 'spark-driver'),
         'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': 'spark-driver'},
         'subjects': [{'kind': 'ServiceAccount', 'name': 'driver', 'namespace': ns}]},
        {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': metadata(token, 'workload'), 'immutable': True,
         'data': {'job.py': job_script(token, mode),
                  'executor-template.json': json.dumps(executor_template(token), sort_keys=True),
                  'spark-defaults.conf': ''.join(key + ' ' + value + '\n' for key, value in sorted(spark_properties(token).items()))}},
        {'apiVersion': 'v1', 'kind': 'Service', 'metadata': metadata(token, 'driver'),
         'spec': {'type': 'ClusterIP', 'clusterIP': 'None', 'publishNotReadyAddresses': True,
                  'selector': labels(token, 'driver'), 'ports': [{'name': 'rpc', 'port': 7078, 'targetPort': 7078},
                                                               {'name': 'block-manager', 'port': 7079, 'targetPort': 7079}]}}])
    driver = pod_spec('driver')
    driver['volumes'].append({'name': 'workload', 'configMap': {'name': 'workload', 'defaultMode': 0o444}})
    container = driver['containers'][0]
    container['volumeMounts'].append({'name': 'workload', 'mountPath': '/opt/shirokuma', 'readOnly': True})
    container['command'] = ['/opt/spark/bin/spark-submit']
    container['args'] = ['--properties-file', '/opt/shirokuma/spark-defaults.conf', '--driver-memory', '1g', '/opt/shirokuma/job.py']
    result.extend(network_policies(token, api_endpoints))
    result.append({'apiVersion': 'v1', 'kind': 'Pod', 'metadata': metadata(token, 'driver', role='driver'), 'spec': driver})
    return result


def validate_plan(value, token, mode, *, api_endpoints=None):
    expected = render(token, mode, api_endpoints=api_endpoints)
    try:
        matched = json.dumps(value, sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True, allow_nan=False)
    except (ValueError, TypeError):
        matched = False
    require(matched, 'MANIFEST_PLAN_CHANGED')
    return copy.deepcopy(value)


def plan_sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
