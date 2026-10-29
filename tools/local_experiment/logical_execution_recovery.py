"""Private logical-v1 current managed compute fence, never a SQL retry.

The existing QueryRecovery owns all ledger transitions. This adapter freezes a
distributed Docker cutset; Kubernetes keeps its desired replicas and heals it.
"""
from contextlib import contextmanager
import base64
import copy
import hashlib
import io
import json
import os
import re
import selectors
import shlex
import subprocess
import tarfile
import time
from urllib.parse import urlsplit

import control_store
import logical_creations
import logical_foundation
from logical_provider import Provider
import logical_registry
import runtime_profile
import warehouse_lifecycle
from query_context import QueryError
from execution_recovery import (DockerAdapter, QueryRecovery, _require, _json,
                                _labels, _time, _nano, MANAGED_LABELS)


class LogicalQueryRecovery(QueryRecovery):
    def _logical_blockers(self, db):
        if logical_creations._config is not None:
            _require(logical_creations._config == runtime_profile.logical_config(), 'RECOVERY_RUNTIME_CHANGED')
        _require(not warehouse_lifecycle._management_operation.get(), 'UNRELATED_UNRESOLVED_WORK')
        _require(all(value == 0 for value in logical_creations.compute_blockers(db).values()) and
                 not warehouse_lifecycle.runtime_blockers(db), 'UNRELATED_UNRESOLVED_WORK')

    def graph(self, db, query_id, *, closure=''):
        result = super().graph(db, query_id, closure=closure)
        self._logical_blockers(db)
        return result

    def _binding(self, db, frozen):
        super()._binding(db, frozen)
        self._logical_blockers(db)

    def __init__(self, config_path):
        super().__init__(config_path)
        _require(self.profile.mode == 'logical_v1')
        os.environ['SHIROKUMA_RUNTIME_PROFILE'] = self.config['runtime_profile']
        runtime_profile.configure_process()
        _require(runtime_profile.current() == self.profile and
                 runtime_profile.private_binding(self.config['runtime_profile']) == self.seal,
                 'RECOVERY_RUNTIME_CHANGED')

    def plan(self, query_id, key, **selectors):
        _require(not any(value is not None for value in selectors.values()),
                 'RECOVERY_HISTORICAL_SELECTOR')
        return super().plan(query_id, key)

    def adapter(self, frozen):
        _require(runtime_profile.private_binding(self.config['runtime_profile']) == self.seal,
                 'RECOVERY_RUNTIME_CHANGED')
        runtime_profile.generation_attestation()
        c = runtime_profile.logical_config(self.profile)
        with control_store.connect() as db:
            registered = logical_registry.get_foundation(db, c['environment_id'], c['warehouse_id'])
        _require(registered is not None and registered['status'] == 'READY' and
                 registered['generation_id'] == frozen['generation']['id'] == c['foundation_generation_id'] and
                 registered['revision'] == c['foundation_revision'] and
                 registered['config_ref'] == c['provider_config_file'], 'RECOVERY_BINDING_CHANGED')
        receipt = runtime_profile.private_json(registered['verification_ref'])
        _require(receipt.get('kind') == 'foundation_probe' and
                 receipt.get('generation_id') == c['foundation_generation_id'] and
                 receipt.get('revision') == c['foundation_revision'], 'RECOVERY_BINDING_CHANGED')
        saved = receipt.get('result')
        _require(isinstance(saved, dict) and saved.get('ready') is True and
                 all(saved.get('readiness', {}).get(k) is True for k in
                     ('storage', 'polaris', 'trino', 'runtime_files', 'spi')),
                 'RECOVERY_BINDING_CHANGED')
        foundation = logical_foundation.load(c['provider_config_file'])
        _require(foundation['foundation_id'] == c['foundation_generation_id'] and
                 foundation['revision'] == c['foundation_revision'] and
                 foundation['environment_id'] == c['environment_id'] and
                 foundation['warehouse_id'] == c['warehouse_id'] and
                 foundation['kube']['context'] == self.config['kube_context'])
        return LogicalDockerAdapter(self.config, self.profile, foundation, saved, self.authenticate)


class LogicalDockerAdapter(DockerAdapter):
    # Reconcile observes exact IDs/events and may close execution, but full
    # Provider binding uses diagnostic SQL. Only explicit apply performs that
    # probe and releases the drain. No original statement is ever dispatched.
    reconcile_read_only = True

    @staticmethod
    def _pod_history(birth, restart_count):
        _require(isinstance(birth, str) and type(restart_count) is int and restart_count >= 0,
                 'RECOVERY_POD_HISTORY_UNPROVED')
        try:
            _nano(birth)  # Requires a timezone; never infer a missing timestamp.
        except (ValueError, TypeError, AttributeError, OverflowError, QueryError):
            raise QueryError('RECOVERY_POD_HISTORY_UNPROVED', 409) from None
        return birth, restart_count

    def _replacement_history(self, scope, member, proofs):
        birth, count = self._pod_history(member.get('pod_created_at'), member.get('restart_count'))
        cutoff = self._cutoff(scope, proofs)
        _require(cutoff is not None and _nano(member['started_at']) > cutoff and
                 _nano(member['started_at']) >= _nano(birth), 'RECOVERY_REPLACEMENT_BEFORE_FENCE')
        prior = [(old, proof) for old, proof in zip(scope['members'], proofs)
                 if old['pod_uid'] == member['pod_uid']]
        if prior:
            _require(len(prior) == 1, 'RECOVERY_POD_HISTORY_UNPROVED')
            old, proof = prior[0]
            _require(proof is not None and member['container_id'] != old['container_id'] and
                     birth == old['pod_created_at'] and count == old['restart_count'] + 1 and
                     all(member[k] == old[k] for k in
                         ('labels', 'deployment', 'deployment_uid', 'replica_set_uid')),
                     'RECOVERY_POD_HISTORY_UNPROVED')
        else:
            # A post-fence container in a pre-fence untracked Pod cannot
            # establish that the Pod's earlier incarnations carried no work.
            _require(_nano(birth) > cutoff, 'RECOVERY_REPLACEMENT_BEFORE_FENCE')

    def __init__(self, config, profile, foundation, saved, authenticate):
        self.config, self.profile = config, profile
        self.foundation, self.saved = copy.deepcopy(foundation), copy.deepcopy(saved)
        self.namespace = foundation['kube']['namespace']
        self.authenticate = authenticate
        self.pins = Provider(foundation)._stable_pins(saved['incarnation'])
        self.members = 1 + foundation['trino']['workers']
        self.rendered, self.private = logical_foundation.render(self.foundation)
        self.material_digest = hashlib.sha256(_json([self.rendered, self.private, saved]).encode()).hexdigest()

    def _run(self, program, *args, input=None):
        if program == 'kubectl':
            # The sealed kubeconfig, rather than ambient KUBECONFIG, selects
            # every metadata target including DockerAdapter.engine's view.
            args = ('--kubeconfig', self.foundation['kube']['kubeconfig'], *args)
        return super()._run(program, *args, input=input)

    @contextmanager
    def _provider(self, *, full=False):
        provider = Provider(self.foundation)
        try:
            provider._trust()
            if full:
                provider.open()
                provider.bind_registered(self.saved)
            else:
                # GET-only checks remain available during partial autohealing.
                provider._incarnation.update(self.pins)
                provider._bound_pins = dict(self.pins)
                provider._stable_health()
                self._static(provider)
            yield provider
        finally:
            provider.close()

    def _static(self, provider):
        """GET-only canonical configuration proof, also while compute heals."""
        provider._stopped_credentials()
        for expected in self.rendered + self.private:
            kind = expected['kind'].lower()
            if kind not in ('deployment', 'service', 'configmap', 'secret'):
                continue
            actual = provider._owned(kind, expected['metadata']['name'])
            if kind in ('deployment', 'service'):
                _require(provider._matches_spec(actual['spec'], expected['spec']), 'RECOVERY_SCOPE_CHANGED')
            elif kind == 'configmap':
                _require(all(actual.get(k, {}) == expected.get(k, {}) for k in ('data', 'binaryData')),
                         'RECOVERY_SCOPE_CHANGED')
            else:
                data = {k: base64.b64encode(v.encode()).decode() for k, v in expected['stringData'].items()}
                _require(actual.get('type') == expected['type'] and actual.get('data') == data,
                         'RECOVERY_SCOPE_CHANGED')

    def engine(self):
        with self._provider() as provider:
            node = provider._json(provider._cmd(['get', 'node',
                self.foundation['kube']['node_name'], '-o', 'json']))
            engine = super().engine()
            info = self._docker('info', '--format', '{{json .}}')
            _require(info['ID'] == engine['engine_id'] and
                     info['Name'] == node['metadata']['name'] == self.foundation['kube']['node_name'] and
                     node['metadata']['uid'] == self.foundation['kube']['node_uids'][info['Name']] and
                     node['status']['nodeInfo']['containerRuntimeVersion'].startswith('docker://'),
                     'RECOVERY_NODE_ENGINE_MISMATCH')
            return dict(engine, node_uid=node['metadata']['uid'], node_name=info['Name'])

    def _archive(self, cid):
        """Fixed Docker Archive GET only, with bounded binary stdout in memory."""
        _require(re.fullmatch('[0-9a-f]{64}', cid), 'RECOVERY_UNFENCED_BACKEND')
        command = ['rtk', 'proxy', 'docker', '--context', self.config['docker_context'],
                   'cp', '-L', cid + ':/etc/trino/config.properties', '-']
        env = {k: v for k, v in os.environ.items() if k not in
               ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH')}
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
        raw, deadline = bytearray(), time.monotonic() + 20
        try:
            with selectors.DefaultSelector() as reader:
                reader.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    _require(remaining > 0 and reader.select(remaining), 'RECOVERY_BACKEND_UNAVAILABLE')
                    chunk = os.read(process.stdout.fileno(), min(65536, 262145 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                    _require(len(raw) <= 262144, 'RECOVERY_FOREIGN_CONFIG_INVALID')
            _require(process.wait(timeout=max(.01, deadline - time.monotonic())) == 0,
                     'RECOVERY_BACKEND_UNAVAILABLE')
            return bytes(raw)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
            process.stdout.close()

    @staticmethod
    def _archive_config(raw):
        _require(isinstance(raw, bytes) and 0 < len(raw) <= 262144, 'RECOVERY_FOREIGN_CONFIG_INVALID')
        try:
            with tarfile.open(fileobj=io.BytesIO(raw), mode='r:', ignore_zeros=True) as archive:
                files = archive.getmembers()
                _require(len(files) == 1 and files[0].name == 'config.properties' and files[0].isfile() and
                         0 < files[0].size <= 65536,
                         'RECOVERY_FOREIGN_CONFIG_INVALID')
                content = archive.extractfile(files[0]).read(65537)
                _require(len(content) == files[0].size, 'RECOVERY_FOREIGN_CONFIG_INVALID')
                return content.decode('utf-8')
        except (tarfile.TarError, ValueError, UnicodeError, OSError):
            raise QueryError('RECOVERY_FOREIGN_CONFIG_INVALID', 409) from None

    def _foreign_get(self, namespace, kind, name):
        _require(all(isinstance(v, str) and re.fullmatch('[a-z0-9][a-z0-9.-]{0,252}', v)
                     for v in (namespace, name)), 'RECOVERY_UNFENCED_BACKEND')
        return json.loads(self._run('kubectl', '-n', namespace, 'get', kind, name, '-o', 'json'))

    def _foreign_standalone(self, row, process, members):
        """Exclude only a positively bound, isolated loopback coordinator."""
        _require(isinstance(row.get('Config', {}).get('Labels'), dict), 'RECOVERY_UNFENCED_BACKEND')
        labels, cid = _labels(row), row['Id']
        namespace = labels['io.kubernetes.pod.namespace']
        _require(namespace and namespace != self.namespace and labels['io.kubernetes.container.name'] == 'trino',
                 'RECOVERY_UNFENCED_BACKEND')
        engine = self.engine()
        pod = self._foreign_get(namespace, 'pod', labels['io.kubernetes.pod.name'])
        rs_owners = pod['metadata'].get('ownerReferences', [])
        _require(len(rs_owners) == 1 and rs_owners[0]['kind'] == 'ReplicaSet' and rs_owners[0].get('controller') is True,
                 'RECOVERY_UNFENCED_BACKEND')
        rs = self._foreign_get(namespace, 'replicaset', rs_owners[0]['name'])
        owners = rs['metadata'].get('ownerReferences', [])
        _require(rs['metadata']['uid'] == rs_owners[0]['uid'] and len(owners) == 1 and
                 owners[0]['kind'] == 'Deployment' and owners[0].get('controller') is True, 'RECOVERY_UNFENCED_BACKEND')
        deployment = self._foreign_get(namespace, 'deployment', owners[0]['name'])
        ns = self._foreign_get(namespace, 'namespace', namespace)
        current = self._kube('pods')['items']
        current_uids = set(self.pins.values()) | {p['metadata']['uid'] for p in current} | {m.get('pod_uid') for m in members}
        current_ips = {p.get('status', {}).get('podIP') for p in current}
        current_ips.update(ip['ip'] for p in current for ip in p.get('status', {}).get('podIPs', []))
        for obj in (ns, pod, rs, deployment):
            meta = obj['metadata']
            _require(meta.get('uid') and meta['uid'] not in current_uids and
                     meta.get('labels', {}).get(logical_foundation.LABEL) != self.foundation['foundation_id'] and
                     meta.get('annotations', {}).get('shirokuma.dev/database-generation') != self.foundation['foundation_id'] and
                     (obj is ns or meta.get('namespace') == namespace), 'RECOVERY_UNFENCED_BACKEND')
        _require(deployment['metadata']['uid'] == owners[0]['uid'] and deployment['spec']['replicas'] == 1 and
                 pod['metadata']['uid'] == labels['io.kubernetes.pod.uid'] and
                 pod['metadata']['name'] == labels['io.kubernetes.pod.name'] and
                 pod['spec']['nodeName'] == engine['node_name'] and pod['status'].get('podIP') and
                 pod['status']['podIP'] not in current_ips and
                 all(ip['ip'] not in current_ips for ip in pod['status'].get('podIPs', [])), 'RECOVERY_UNFENCED_BACKEND')
        for selector in (deployment['spec']['selector']['matchLabels'], rs['spec']['selector']['matchLabels']):
            _require(selector and all(pod['metadata']['labels'].get(k) == v for k, v in selector.items()),
                     'RECOVERY_UNFENCED_BACKEND')
        _require(Provider._matches_spec(pod['spec'], deployment['spec']['template']['spec']) and
                 Provider._matches_spec(pod['spec'], rs['spec']['template']['spec']) and
                 not any(pod['spec'].get(k) for k in
                     ('hostNetwork', 'hostPID', 'hostIPC', 'shareProcessNamespace', 'ephemeralContainers', 'initContainers')),
                 'RECOVERY_UNFENCED_BACKEND')
        trino = [c for c in pod['spec']['containers'] if c['name'] == 'trino']
        statuses = [s for s in pod['status']['containerStatuses'] if s['name'] == 'trino']
        launcher = ['/usr/lib/trino/bin/run-trino']
        _require(len(trino) == len(statuses) == 1 and statuses[0]['containerID'] == 'docker://' + cid and
                 trino[0].get('command') == launcher and not trino[0].get('args') and not trino[0].get('envFrom') and
                 row.get('Path') == launcher[0] and not row.get('Args') and
                 row['Config'].get('Entrypoint') == launcher and not row['Config'].get('Cmd') and
                 row['State']['Running'] is True and row['HostConfig']['RestartPolicy']['Name'] == 'no',
                 'RECOVERY_UNFENCED_BACKEND')
        _require(re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}', trino[0]['image']) and
                 statuses[0]['imageID'].endswith(trino[0]['image'].split('@')[1]), 'RECOVERY_IMAGE_CHANGED')
        image = self._docker('image', 'inspect', trino[0]['image'])
        _require(len(image) == 1 and row['Image'] == image[0]['Id'] and
                 trino[0]['image'].removeprefix('docker.io/') in
                     {d.removeprefix('docker.io/') for d in image[0].get('RepoDigests', [])}, 'RECOVERY_IMAGE_CHANGED')
        forbidden_env = {'JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS', 'JDK_JAVA_OPTIONS', 'JAVA_OPTS', 'TRINO_JVM_OPTS', 'TRINO_CONFIG_DIR'}
        _require(not any(e['name'] in forbidden_env for e in trino[0].get('env', [])) and
                 not any(e.split('=', 1)[0] in forbidden_env for e in row['Config'].get('Env', [])), 'RECOVERY_UNFENCED_BACKEND')
        java = [line for line in process.splitlines()[1:] if 'java' in line]
        _require(len(java) == 1 and 'io.trino.server.TrinoServer' in java[0], 'RECOVERY_UNFENCED_BACKEND')
        argv = shlex.split(java[0])
        _require(argv[-1] == 'io.trino.server.TrinoServer' and argv.count('-Dconfig=/etc/trino/config.properties') == 1 and
                 not any(a.startswith(('-Dcoordinator', '-Dnode-scheduler', '-Ddiscovery', '--config', '--etc-dir',
                                       '@', '-javaagent', '-agentlib', '-agentpath')) or
                         (a.startswith('-Dconfig') and a != '-Dconfig=/etc/trino/config.properties') for a in argv),
                 'RECOVERY_UNFENCED_BACKEND')
        network = row['HostConfig'].get('NetworkMode', '')
        _require(re.fullmatch('container:[0-9a-f]{64}', network), 'RECOVERY_UNFENCED_BACKEND')
        sandbox = self._docker('inspect', network.split(':', 1)[1])
        _require(len(sandbox) == 1 and sandbox[0]['Id'] == network.split(':', 1)[1] and
                 _labels(sandbox[0]) == dict(labels, **{'io.kubernetes.container.name': 'POD'}) and
                 sandbox[0]['State']['Running'] is True and
                 sandbox[0]['HostConfig']['NetworkMode'] in ('none', 'bridge', 'default'), 'RECOVERY_UNFENCED_BACKEND')
        mounts = [m for m in trino[0].get('volumeMounts', []) if
                  m['mountPath'] == '/etc/trino' or m['mountPath'].startswith('/etc/trino/') or
                  '/etc/trino'.startswith(m['mountPath'].rstrip('/') + '/')]
        _require(len(mounts) == 1 and mounts[0]['mountPath'] == '/etc/trino' and mounts[0].get('readOnly') is True and
                 not mounts[0].get('subPath') and not mounts[0].get('subPathExpr'), 'RECOVERY_UNFENCED_BACKEND')
        volumes = [v for v in pod['spec']['volumes'] if v['name'] == mounts[0]['name']]
        _require(len(volumes) == 1 and 'configMap' in volumes[0], 'RECOVERY_UNFENCED_BACKEND')
        actual = [m for m in row['Mounts'] if m['Destination'] == '/etc/trino' or
                  m['Destination'].startswith('/etc/trino/') or '/etc/trino'.startswith(m['Destination'].rstrip('/') + '/')]
        _require(len(actual) == 1 and actual[0]['Destination'] == '/etc/trino' and actual[0]['RW'] is False and
                 actual[0]['Type'] == 'bind' and actual[0]['Source'].startswith('/') and
                 actual[0]['Source'].endswith('/pods/' + pod['metadata']['uid'] + '/volumes/kubernetes.io~configmap/' + mounts[0]['name']),
                 'RECOVERY_UNFENCED_BACKEND')
        cm = self._foreign_get(namespace, 'configmap', volumes[0]['configMap']['name'])
        _require(cm['metadata'].get('uid') and cm['metadata']['uid'] not in current_uids and
                 cm['metadata'].get('namespace') == namespace and
                 cm['metadata'].get('labels', {}).get(logical_foundation.LABEL) != self.foundation['foundation_id'],
                 'RECOVERY_UNFENCED_BACKEND')
        content = self._archive_config(self._archive(cid))
        _require(content == cm['data']['config.properties'], 'RECOVERY_FOREIGN_CONFIG_INVALID')
        properties = {}
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith(('#', '!')):
                continue
            _require('\\' not in line and '=' in line, 'RECOVERY_FOREIGN_CONFIG_INVALID')
            key, value = (part.strip() for part in line.split('=', 1))
            _require(re.fullmatch('[A-Za-z0-9_.-]+', key) and key not in properties, 'RECOVERY_FOREIGN_CONFIG_INVALID')
            properties[key] = value
        _require(properties.get('coordinator') == properties.get('node-scheduler.include-coordinator') == 'true' and
                 properties.get('discovery.uri') in ('http://localhost:8080', 'http://127.0.0.1:8080', 'http://[::1]:8080'),
                 'RECOVERY_UNFENCED_BACKEND')
        def mounts_by_destination(mounts):
            _require(isinstance(mounts, list), 'RECOVERY_UNFENCED_BACKEND')
            result = {}
            for mount in mounts:
                _require(isinstance(mount, dict) and isinstance(mount.get('Destination'), str) and
                         mount['Destination'].startswith('/') and mount['Destination'] not in result,
                         'RECOVERY_UNFENCED_BACKEND')
                result[mount['Destination']] = mount
            return result
        fresh = self._docker('inspect', cid)
        _require(len(fresh) == 1 and all(fresh[0].get(k) == row.get(k) for k in
                 ('Id', 'Image', 'Config', 'HostConfig')) and
                 mounts_by_destination(fresh[0].get('Mounts')) == mounts_by_destination(row.get('Mounts')) and
                 fresh[0]['State']['Running'] is True and
                 fresh[0]['State']['StartedAt'] == row['State']['StartedAt'] and
                 self._foreign_get(namespace, 'pod', pod['metadata']['name']) == pod and
                 self._foreign_get(namespace, 'configmap', cm['metadata']['name']) == cm,
                 'RECOVERY_UNFENCED_BACKEND')

    def _inventory(self, members, *, images=None):
        ids = self._run('docker', 'ps', '--no-trunc', '--format', '{{.ID}}').splitlines()
        _require(len(set(ids)) == len(ids) <= 256 and
                 all(re.fullmatch('[0-9a-f]{64}', cid) for cid in ids), 'RECOVERY_SCOPE_CHANGED')
        rows = self._docker('inspect', *ids) if ids else []
        _require(len(rows) == len(ids) and {r['Id'] for r in rows} == set(ids), 'RECOVERY_SCOPE_CHANGED')
        expected = {m['container_id']: m for m in members}
        _require(expected.keys() <= set(ids), 'RECOVERY_SCOPE_CHANGED')
        images = images or {m['image'] for m in members}
        for row in rows:
            process = self._run('docker', 'top', row['Id'], '-eo', 'pid,comm,args')
            if row['Id'] in expected:
                member = expected[row['Id']]
                _require(self._matches(row, member) and row['State']['Running'] is True and
                         sum('java' in p for p in process.splitlines()[1:]) == 1 and
                         sum('java' in p and 'io.trino.server.TrinoServer' in p
                             for p in process.splitlines()[1:]) == 1, 'RECOVERY_SCOPE_CHANGED')
            else:
                if ('io.trino.server.' in process or 'run-trino' in process or
                        row.get('Path') == '/usr/lib/trino/bin/run-trino' or
                        ('java' in process and (row['Image'] in images or 'trino' in row['Config'].get('Image', '').lower()))):
                    self._foreign_standalone(row, process, members)

    def scope(self, graph, *, replacement=False):
        engine = self.engine()
        with self._provider(full=True) as provider:
            desired = {d['metadata']['name']: d for d in self.rendered if d['kind'] == 'Deployment'}
            # Full bind checks engine behavior; render comparison also rejects
            # edited ConfigMap/Secret contents behind unchanged object UIDs.
            self._static(provider)
            images = self._docker('image', 'inspect', self.foundation['images']['trino'])
            _require(len(images) == 1 and self.foundation['images']['trino'].removeprefix('docker.io/') in
                     {d.removeprefix('docker.io/') for d in images[0].get('RepoDigests', [])},
                     'RECOVERY_IMAGE_CHANGED')
            pods = provider._compute()
            runtime_nodes = provider._sql('SELECT node_id, http_uri, coordinator, state FROM system.runtime.nodes',
                                          provider._provisioner())
            _require(isinstance(runtime_nodes, list) and len(runtime_nodes) == self.members,
                     'RECOVERY_UNFENCED_BACKEND')
            backends = {}
            for node in runtime_nodes:
                _require(isinstance(node, list) and len(node) == 4 and isinstance(node[0], str) and node[0] and
                         isinstance(node[1], str) and type(node[2]) is bool and node[3] == 'active',
                         'RECOVERY_UNFENCED_BACKEND')
                uri = urlsplit(node[1])
                # The sealed render exposes HTTPS SQL on 8080 and internal
                # discovery HTTP on 8081. Trino may advertise either URI.
                _require(uri.scheme in ('http', 'https') and
                         uri.port == {'http': 8081, 'https': 8080}[uri.scheme] and not uri.username and
                         not uri.password and not uri.query and not uri.fragment and
                         uri.hostname not in backends, 'RECOVERY_UNFENCED_BACKEND')
                backends[uri.hostname] = (node[0], node[2])
            _require(len({n[0] for n in runtime_nodes}) == self.members and
                     set(backends) == {p['status']['podIP'] for p in pods}, 'RECOVERY_UNFENCED_BACKEND')
            members = []
            for pod in pods:
                owner = provider._pod(pod)
                backend_id, coordinator = backends[pod['status']['podIP']]
                _require(coordinator is (owner == 'trino-local'), 'RECOVERY_UNFENCED_BACKEND')
                deployment = provider._owned('deployment', owner)
                spec = desired[owner]['spec']
                _require(provider._matches_spec(deployment['spec'], spec) and
                         provider._matches_spec(pod['spec'], spec['template']['spec']) and
                         not pod['metadata'].get('deletionTimestamp'), 'RECOVERY_SCOPE_CHANGED')
                rs_owner = pod['metadata']['ownerReferences'][0]
                rs = provider._owned('replicaset', rs_owner['name'])
                _require(provider._matches_spec(rs['spec']['template']['spec'], spec['template']['spec']) and
                         all(pod['metadata']['labels'].get(k) == v for k, v in
                             deployment['spec']['selector']['matchLabels'].items()), 'RECOVERY_SCOPE_CHANGED')
                for selector in (rs['spec']['selector']['matchLabels'], spec['template']['metadata']['labels']):
                    _require(selector and all(pod['metadata']['labels'].get(k) == v for k, v in selector.items()),
                             'RECOVERY_SCOPE_CHANGED')
                status = pod['status']['containerStatuses'][0]
                birth, restart_count = self._pod_history(pod['metadata'].get('creationTimestamp'), status.get('restartCount'))
                cid = status['containerID']
                _require(cid.startswith('docker://') and re.fullmatch('[0-9a-f]{64}', cid[9:]),
                         'RECOVERY_SCOPE_CHANGED')
                cid = cid[9:]
                rows = self._docker('inspect', cid)
                _require(len(rows) == 1, 'RECOVERY_SCOPE_CHANGED')
                row = rows[0]
                labels = dict(zip(MANAGED_LABELS, (pod['metadata']['uid'], self.namespace,
                    pod['metadata']['name'], 'trino')))
                _require(row['Id'] == cid and _labels(row) == labels and
                         row['State']['Running'] is True and
                         row['HostConfig']['RestartPolicy']['Name'] == 'no' and
                         row['Image'] == images[0]['Id'] and
                         _time(row['State']['StartedAt']) >= _time(birth),
                         'RECOVERY_SCOPE_CHANGED')
                # Mounts are checked in Pod/render/probe and again at Docker's
                # actual boundary. No host bind may replace a required target.
                mounts = {m['Destination']: m for m in row['Mounts']}
                for mount in pod['spec']['containers'][0]['volumeMounts']:
                    actual = mounts.get(mount['mountPath'])
                    _require(actual is not None and bool(actual['Source']) and
                             actual['RW'] is (not mount.get('readOnly', False)), 'RECOVERY_SCOPE_CHANGED')
                members.append(dict(**engine, container_id=cid, labels=labels, image=row['Image'],
                    started_at=row['State']['StartedAt'], pod_uid=pod['metadata']['uid'],
                    pod_created_at=birth, restart_count=restart_count,
                    backend_node_id=backend_id,
                    deployment=owner, deployment_uid=deployment['metadata']['uid'],
                    replica_set_uid=rs['metadata']['uid']))
            _require(len(members) == self.members and
                     len({m['container_id'] for m in members}) == self.members, 'RECOVERY_SCOPE_CHANGED')
            # Namespace-wide census includes nonselected and terminating Pods.
            all_pods = provider._json(provider._cmd(['get', 'pods', '-o', 'json']))['items']
            selected = {m['pod_uid'] for m in members}
            for pod in all_pods:
                if pod['metadata']['uid'] not in selected:
                    _require(not any(c.get('name') == 'trino' or 'trino' in c.get('image', '').lower()
                        for c in pod['spec']['containers']) and
                        pod['metadata'].get('labels', {}).get('shirokuma.dev/logical-compute') != 'true',
                        'RECOVERY_UNFENCED_BACKEND')
            service = provider._owned('service', 'trino-local')
            endpoints = self._kube('endpoints', 'trino-local')
            addresses = [a for s in endpoints.get('subsets', []) for a in s.get('addresses', [])]
            coordinator = [m for m in members if m['deployment'] == 'trino-local']
            _require(len(coordinator) == 1 and len(addresses) == 1 and
                     addresses[0]['targetRef']['uid'] == coordinator[0]['pod_uid'] and
                     addresses[0]['targetRef']['namespace'] == self.namespace and
                     not any(s.get('notReadyAddresses') for s in endpoints.get('subsets', [])),
                     'RECOVERY_SCOPE_CHANGED')
            self._inventory(members)
            _require(self.engine() == engine, 'RECOVERY_ENGINE_CHANGED')
            return dict(**engine, kind='logical-current-managed-scope-v1',
                foundation_id=self.foundation['foundation_id'], revision=self.foundation['revision'],
                pins=self.pins, material_digest=self.material_digest, service_uid=service['metadata']['uid'],
                members=sorted(members, key=lambda m: (m['deployment'] != 'trino-local', m['container_id'])),
                binding_basis='all current managed runtime members identified; no additional runnable backend in this foundation; '
                              'no claim that current containers existed at query admission')

    def _scope(self, scope):
        _require(scope['kind'] == 'logical-current-managed-scope-v1' and
                 scope['foundation_id'] == self.foundation['foundation_id'] and
                 scope['revision'] == self.foundation['revision'] and scope['pins'] == self.pins and
                 scope['material_digest'] == self.material_digest and
                 len(scope['members']) == self.members and
                 len({m['container_id'] for m in scope['members']}) == self.members and
                 len({m['pod_uid'] for m in scope['members']}) == self.members and
                 all(m['engine_id'] == scope['engine_id'] and m['node_uid'] == scope['node_uid']
                     for m in scope['members']), 'RECOVERY_SCOPE_CHANGED')
        _require(sum(m['deployment'] == 'trino-local' for m in scope['members']) == 1 and
                 all(m['deployment'] in ('trino-local', 'trino-local-worker') for m in scope['members']),
                 'RECOVERY_SCOPE_CHANGED')
        for member in scope['members']:
            birth, _ = self._pod_history(member.get('pod_created_at'), member.get('restart_count'))
            _require(_nano(member['started_at']) >= _nano(birth), 'RECOVERY_POD_HISTORY_UNPROVED')
        _require(self.engine() == {k: scope[k] for k in ('engine_id', 'socket', 'node_uid', 'node_name')},
                 'RECOVERY_ENGINE_CHANGED')

    def proof(self, scope, since):
        self._scope(scope)
        proofs = [super(LogicalDockerAdapter, self).proof(m, since) for m in scope['members']]
        if any(p is None for p in proofs):
            self.validate_membership(scope, since, proofs)
            return None
        result = dict(kind='logical-all-members-stopped-v1', engine_id=scope['engine_id'],
            scope_digest=hashlib.sha256(_json(scope).encode()).hexdigest(), members=proofs)
        self.validate_proof(scope, result, since)
        return result

    def validate_proof(self, scope, proof, since):
        self._scope(scope)
        _require(isinstance(proof, dict) and proof.get('kind') == 'logical-all-members-stopped-v1' and
                 proof.get('engine_id') == scope['engine_id'] and
                 proof.get('scope_digest') == hashlib.sha256(_json(scope).encode()).hexdigest() and
                 isinstance(proof.get('members'), list) and len(proof['members']) == self.members,
                 'RECOVERY_STOP_UNPROVED')
        for member, p in zip(scope['members'], proof['members']):
            _require(isinstance(p, dict) and p.get('engine_id') == scope['engine_id'], 'RECOVERY_STOP_UNPROVED')
            receipt = p.get('receipt', {})
            _require(receipt.get('container_id') == member['container_id'] and
                     receipt.get('labels') == member['labels'], 'RECOVERY_STOP_UNPROVED')
            if p.get('kind') == 'stopped-inspect':
                state = receipt.get('state', {})
                _require(receipt.get('image') == member['image'] and
                         receipt.get('started_at') == member['started_at'] and receipt.get('restart_policy') == 'no' and
                         state.get('Running') is False and state.get('Status') == 'exited' and
                         _time(state['FinishedAt']) >= _time(since) and
                         type(state.get('ExitCode')) is int and 0 <= state['ExitCode'] <= 255,
                         'RECOVERY_STOP_UNPROVED')
            else:
                _require(p.get('kind') == 'exact-event' and receipt.get('action') in ('die', 'destroy') and
                         type(receipt.get('time_nano')) is int and receipt['time_nano'] >= _nano(since),
                         'RECOVERY_STOP_UNPROVED')
        cutoff = self._cutoff(scope, proof['members'])
        _require(all(self._stopped_at(p) >= cutoff for p in proof['members']), 'RECOVERY_STOP_UNPROVED')
        self.validate_membership(scope, since, proof['members'])
        self._proved = (scope, proof, since)

    @staticmethod
    def _stopped_at(proof):
        if proof['kind'] == 'stopped-inspect':
            return _nano(proof['receipt']['state']['FinishedAt'])
        return proof['receipt']['time_nano']

    def _cutoff(self, scope, proofs):
        coordinator = next(i for i, m in enumerate(scope['members']) if m['deployment'] == 'trino-local')
        return self._stopped_at(proofs[coordinator]) if proofs[coordinator] is not None else None

    def validate_membership(self, scope, since, proofs):
        """GET-only cutset census; no readiness, tunnel, SQL, or target selection.

        A replacement is evidence of current membership, never a stop target.
        Unproved originals must still be identified exactly; an unknown birth
        or an uncontained runnable peer leaves the operation draining.
        """
        self._scope(scope)
        originals = {m['container_id']: (m, p) for m, p in zip(scope['members'], proofs)}
        running, seen = [], set()
        counts = {'trino-local': 0, 'trino-local-worker': 0}
        coordinators = {}
        with self._provider() as provider:
            desired = {d['metadata']['name']: d['spec']['template']['spec']
                       for d in self.rendered if d['kind'] == 'Deployment'}
            pods = provider._json(provider._cmd(['get', 'pods', '-o', 'json']))['items']
            for pod in pods:
                containers = pod['spec'].get('containers', [])
                if not (pod['metadata'].get('labels', {}).get('shirokuma.dev/logical-compute') == 'true' or
                        any(c.get('name') == 'trino' or 'trino' in c.get('image', '').lower() for c in containers)):
                    continue
                provider._labels(pod)
                _require(pod['metadata']['namespace'] == self.namespace and
                         pod['spec'].get('nodeName') == scope['node_name'], 'RECOVERY_SCOPE_CHANGED')
                owners = pod['metadata'].get('ownerReferences', [])
                _require(len(owners) == 1 and owners[0]['kind'] == 'ReplicaSet' and owners[0].get('controller') is True,
                         'RECOVERY_SCOPE_CHANGED')
                rs = provider._owned('replicaset', owners[0]['name'])
                parents = rs['metadata'].get('ownerReferences', [])
                _require(rs['metadata']['uid'] == owners[0]['uid'] and len(parents) == 1 and
                         parents[0]['kind'] == 'Deployment' and parents[0].get('controller') is True,
                         'RECOVERY_SCOPE_CHANGED')
                name = parents[0]['name']
                _require(name in {m['deployment'] for m in scope['members']} and
                         parents[0]['uid'] == self.pins['deployment/'+name] and
                         provider._matches_spec(pod['spec'], desired[name]) and
                         provider._matches_spec(rs['spec']['template']['spec'], desired[name]), 'RECOVERY_SCOPE_CHANGED')
                if name == 'trino-local':
                    coordinators[pod['metadata']['uid']] = pod.get('status', {}).get('podIP')
                for selector in (rs['spec']['selector']['matchLabels'],):
                    _require(selector and all(pod['metadata']['labels'].get(k) == v for k, v in selector.items()),
                             'RECOVERY_SCOPE_CHANGED')
                statuses = pod.get('status', {}).get('containerStatuses', [])
                _require(len(statuses) == 1 and statuses[0]['name'] == 'trino' and
                         re.fullmatch('docker://[0-9a-f]{64}', statuses[0].get('containerID', '')),
                         'RECOVERY_SCOPE_CHANGED')
                cid = statuses[0]['containerID'][9:]
                birth, restart_count = self._pod_history(pod['metadata'].get('creationTimestamp'), statuses[0].get('restartCount'))
                _require(cid not in seen, 'RECOVERY_SCOPE_CHANGED')
                seen.add(cid)
                labels = dict(zip(MANAGED_LABELS, (pod['metadata']['uid'], self.namespace, pod['metadata']['name'], 'trino')))
                if cid in originals:
                    original, _ = originals[cid]
                    _require(labels == original['labels'] and original['deployment'] == name and
                             birth == original['pod_created_at'] and restart_count == original['restart_count'],
                             'RECOVERY_SCOPE_CHANGED')
                # A positively proved old Pod may outlive Docker GC. Missing
                # replacement inspect, in contrast, never establishes birth.
                if cid in originals and originals[cid][1] is not None:
                    try:
                        rows = self._docker('inspect', cid)
                    except QueryError:
                        rows = []
                    if not rows:
                        continue
                else:
                    rows = self._docker('inspect', cid)
                _require(len(rows) == 1, 'RECOVERY_SCOPE_CHANGED')
                row = rows[0]
                _require(row['Id'] == cid and _labels(row) == labels and
                         row['HostConfig']['RestartPolicy']['Name'] == 'no', 'RECOVERY_SCOPE_CHANGED')
                if cid in originals:
                    original, proof = originals[cid]
                    _require(self._matches(row, original) and original['deployment'] == name,
                             'RECOVERY_SCOPE_CHANGED')
                    _require(row['State']['Running'] is (proof is None), 'RECOVERY_SCOPE_CHANGED')
                else:
                    self._replacement_history(scope, dict(container_id=cid, pod_uid=pod['metadata']['uid'],
                        pod_created_at=birth, restart_count=restart_count, started_at=row['State']['StartedAt'],
                        labels=labels, deployment=name, deployment_uid=parents[0]['uid'],
                        replica_set_uid=rs['metadata']['uid']), proofs)
                    # The planned resolved image ID came from inspecting the
                    # sealed full reference. Config.Image may be a digest input.
                    _require(row['Image'] in {m['image'] for m in scope['members']}, 'RECOVERY_IMAGE_CHANGED')
                    for mount in pod['spec']['containers'][0]['volumeMounts']:
                        matches = [m for m in row['Mounts'] if m['Destination'] == mount['mountPath']]
                        _require(len(matches) == 1 and matches[0]['Source'] and
                                 matches[0]['RW'] is (not mount.get('readOnly', False)), 'RECOVERY_SCOPE_CHANGED')
                if row['State']['Running']:
                    counts[name] += 1
                    running.append(dict(container_id=cid, labels=labels, image=row['Image'], started_at=row['State']['StartedAt']))
            _require(all(cid in seen or p is not None for cid, (_, p) in originals.items()), 'RECOVERY_SCOPE_CHANGED')
            _require(counts['trino-local'] <= 1 and counts['trino-local-worker'] <= self.members - 1,
                     'RECOVERY_UNFENCED_BACKEND')
            endpoints = self._kube('endpoints', 'trino-local')
            for subset in endpoints.get('subsets', []):
                for address in subset.get('addresses', []) + subset.get('notReadyAddresses', []):
                    ref = address.get('targetRef', {})
                    _require(ref.get('uid') in coordinators and ref.get('namespace') == self.namespace and
                             ref.get('kind') == 'Pod' and isinstance(address.get('ip'), str) and address['ip'] and
                             address['ip'] == coordinators[ref['uid']],
                             'RECOVERY_UNFENCED_BACKEND')
            self._inventory(running, images={m['image'] for m in scope['members']})

    def validate_stop_scope(self, graph, scope, since):
        self._scope(scope)
        self.since = since
        fresh = self.scope(graph, replacement=True)
        _require(all(fresh[k] == scope[k] for k in
                     ('engine_id', 'socket', 'node_uid', 'node_name', 'pins', 'service_uid')),
                 'RECOVERY_SCOPE_CHANGED')
        current = {m['container_id']: m for m in fresh['members']}
        for member in scope['members']:
            if super().proof(member, since) is None:
                _require(current.get(member['container_id']) == member, 'RECOVERY_SCOPE_CHANGED')
        proofs = [super(LogicalDockerAdapter, self).proof(m, since) for m in scope['members']]
        original_ids = {m['container_id'] for m in scope['members']}
        for member in fresh['members']:
            if member['container_id'] not in original_ids:
                self._replacement_history(scope, member, proofs)
        self.validate_membership(scope, since, proofs)
        # Replacement members are validated by the complete fresh scope; they
        # never become stop targets. The planned old IDs remain immutable.

    def stop(self, scope):
        with warehouse_lifecycle._lock():
            self._scope(scope)
            ordered = sorted(scope['members'], key=lambda m: (m['deployment'] != 'trino-local', m['container_id']))
            for member in ordered:
                proofs = [super(LogicalDockerAdapter, self).proof(m, self.since) for m in scope['members']]
                self.validate_membership(scope, self.since, proofs)
                if member['deployment'] != 'trino-local':
                    _require(self._cutoff(scope, proofs) is not None, 'RECOVERY_STOP_UNPROVED')
                if super().proof(member, self.since) is not None:
                    continue
                self.authenticate()
                rows = self._docker('inspect', member['container_id'])
                _require(len(rows) == 1 and self._matches(rows[0], member) and
                         rows[0]['State']['Running'] is True, 'RECOVERY_SCOPE_CHANGED')
                if member['deployment'] == 'trino-local':
                    warehouse_lifecycle.invalidate_proof()
                super().stop(member)
                proofs = [super(LogicalDockerAdapter, self).proof(m, self.since) for m in scope['members']]
                _require(super().proof(member, self.since) is not None, 'RECOVERY_STOP_UNPROVED')
                self.validate_membership(scope, self.since, proofs)

    def old_present(self, scope):
        self._scope(scope)
        return any([super(LogicalDockerAdapter, self).old_present(m) for m in scope['members']])

    def remove(self, scope):
        # Recovery persisted and validated the entire bundle before this call.
        self._scope(scope)
        for member in scope['members']:
            self.validate_proof(*self._proved)
            self.authenticate()
            # DockerAdapter.remove calls self.old_present; use the leaf
            # explicitly so its single-container check cannot dispatch to the
            # distributed old_present override.
            if DockerAdapter.old_present(self, member):
                self._run('docker', 'rm', member['container_id'])
            _require(not DockerAdapter.old_present(self, member), 'RECOVERY_OLD_CONTAINER_PRESENT')

    def healthy(self, graph, old_scope):
        with warehouse_lifecycle._lock():
            self._scope(old_scope)
            self.validate_proof(*self._proved)
            fresh = self.scope(graph, replacement=True)
            for member in fresh['members']:
                self._replacement_history(old_scope, member, self._proved[1]['members'])
            cutoff = self._cutoff(old_scope, self._proved[1]['members'])
            _require(all(fresh[k] == old_scope[k] for k in
                ('engine_id', 'socket', 'node_uid', 'node_name', 'pins', 'service_uid')) and
                not ({m['container_id'] for m in fresh['members']} &
                     {m['container_id'] for m in old_scope['members']}) and
                all(_nano(m['started_at']) > cutoff for m in fresh['members']), 'RECOVERY_SCOPE_CHANGED')
            self.authenticate()
            warehouse_lifecycle._save_proof()
            return fresh
