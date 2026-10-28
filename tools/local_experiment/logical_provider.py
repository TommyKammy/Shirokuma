"""Bounded private logical-v1 adapter. No effects at import or construction.

Core owns durable intents, claims, credential receipts and final READY CAS.
An UNKNOWN result is never a reason to resend a mutation through this adapter.
"""
from __future__ import annotations

import base64
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
import fcntl
import hashlib
import http.client
import json
from pathlib import Path
import queue
import re
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

if __package__:
    from . import logical_foundation as foundation
else:
    import logical_foundation as foundation

SCHEMAS = ('raw', 'staging', 'marts', 'sandbox')
MANAGEMENT = '/api/management/v1'
PUBLISHER = ['java', '-cp', '/opt/logical-secrets/logical-secrets.jar', 'org.shirokuma.logical.secrets.FixedPublisher']
TARGETS = {'credentials': 'credentials.properties', 'password_db': 'password.db', 'rules_json': 'rules.json', 'guard_rego': 'guard.rego'}
REF = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,191}\Z')
KNOWN_REJECTIONS = (400, 401, 403, 404, 405, 409, 412, 422)
SDK_UNCERTAIN = ('RequestTimeout', 'RequestTimeoutException', 'PriorRequestNotComplete')


def _proof(db, kind, verified, **evidence):
    return {**evidence, **db, 'kind': kind, 'verified': bool(verified), 'complete': bool(verified)}


def _sdk_rejected(error):
    response = getattr(error, 'response', {})
    return (response.get('ResponseMetadata', {}).get('HTTPStatusCode') in KNOWN_REJECTIONS and
        response.get('Error', {}).get('Code') not in SDK_UNCERTAIN)


SQL_NAME = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,62}\Z')


class ProviderError(RuntimeError):
    """Only fixed safe codes escape the private boundary."""
    def __init__(self, code, *, unknown=False):
        self.code, self.unknown = code, unknown
        super().__init__(code)


class Rejected(ProviderError):
    def __init__(self, code='PROVIDER_REJECTED'):
        super().__init__(code)


class Unknown(ProviderError):
    def __init__(self, code='PROVIDER_OUTCOME_UNKNOWN'):
        super().__init__(code, unknown=True)


class StaleCAS(Rejected):
    def __init__(self):
        super().__init__('MATERIAL_CAS_STALE')


def _require(ok, code='PROVIDER_INPUT_INVALID'):
    if not ok:
        raise Rejected(code)


def _command(argv, *, input=None):
    return subprocess.run(argv, input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http(method, url, *, headers, data, ca_file):
    context = ssl.create_default_context(cafile=ca_file) if ca_file else ssl.create_default_context()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect(), urllib.request.HTTPSHandler(context=context))
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener.open(request, timeout=20) as response:
            return response.status, response.read(4 * 1024 * 1024)
    except urllib.error.HTTPError as error:
        # Error bodies may contain credentials or SQL; discard them.
        return error.code, b''


def _sdk(service, **options):
    # Lazy import: an inert import never probes credential providers or the network.
    import boto3
    from botocore.config import Config
    return boto3.client(service, config=Config(retries={'total_max_attempts': 1, 'mode': 'standard'},
        proxies={}, connect_timeout=10, read_timeout=20, s3={'addressing_style': 'path'}), **options)


def _properties(values):
    def escape(value):
        result = ''
        for ch in value:
            if ch in '\\=:# !':
                result += '\\'+ch
            elif ch == '\n':
                result += '\\n'
            elif ch == '\r':
                result += '\\r'
            elif ch == '\t':
                result += '\\t'
            elif ord(ch) > 126 or ord(ch) < 32:
                raw = ch.encode('utf-16-be')
                result += ''.join('\\u'+raw[i:i+2].hex() for i in range(0, len(raw), 2))
            else:
                result += ch
        return result
    return ''.join(escape(k)+'='+escape(v)+'\n' for k, v in sorted(values.items())).encode()


def _catalog_digest(properties):
    return hashlib.sha256(''.join(k+'='+properties[k]+'\n' for k in sorted(properties)).encode()).hexdigest()


def _semantic_policy(value):
    if isinstance(value, str):
        try:
            try:
                value = json.loads(value)
            except ValueError:
                value = json.loads(urllib.parse.unquote(value))
        except ValueError:
            raise Rejected('IAM_POLICY_INVALID') from None

    def unordered(items):
        items = items if isinstance(items, list) else [items]
        return sorted({json.dumps(item, sort_keys=True, separators=(',', ':')) for item in items})

    if not isinstance(value, dict) or 'Statement' not in value:
        return value
    result = copy.deepcopy(value)
    statements = result['Statement'] if isinstance(result['Statement'], list) else [result['Statement']]
    for item in statements:
        if not isinstance(item, dict):
            continue
        for field in ('Action', 'NotAction', 'Resource', 'NotResource'):
            if field in item:
                item[field] = unordered(item[field])
        for field in ('Principal', 'NotPrincipal'):
            if isinstance(item.get(field), dict):
                item[field] = {k: unordered(v) for k, v in item[field].items()}
        if isinstance(item.get('Condition'), dict):
            item['Condition'] = {op: {k: unordered(v) for k, v in values.items()} if isinstance(values, dict) else values
                for op, values in item['Condition'].items()}
    result['Statement'] = unordered(statements)
    return result


class _KubeMetadata:
    """Connection reuse only; canonical credentials and GET results are never cached."""
    LIMIT = 8 * 1024 * 1024

    def __init__(self):
        self._lock = threading.RLock()
        self._connection = None
        self._fingerprint = None
        self._epoch = None

    def close(self, *, epoch=None):
        with self._lock:
            if epoch is not None and self._epoch != epoch:
                return
            connection, self._connection = self._connection, None
            self._fingerprint = None
            self._epoch = None
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    def configure(self, view, kube, *, epoch):
        with self._lock:
            try:
                _require(isinstance(view, dict) and set(view) <= {'apiVersion', 'kind', 'preferences',
                    'current-context', 'contexts', 'clusters', 'users'}, 'FOUNDATION_TRUST_MISMATCH')
                _require(view.get('current-context') == kube['context'] and not view.get('preferences'),
                    'FOUNDATION_TRUST_MISMATCH')
                entries = []
                for group, field in (('contexts', 'context'), ('clusters', 'cluster'), ('users', 'user')):
                    values = view[group]
                    _require(isinstance(values, list) and len(values) == 1 and
                        isinstance(values[0], dict) and set(values[0]) == {'name', field} and
                        isinstance(values[0]['name'], str) and bool(values[0]['name']),
                        'FOUNDATION_TRUST_MISMATCH')
                    entries.append(values[0])
                context, cluster_entry, user_entry = entries
                refs, cluster, user = context['context'], cluster_entry['cluster'], user_entry['user']
                _require(context['name'] == kube['context'] and isinstance(refs, dict) and
                    set(refs) <= {'cluster', 'user', 'namespace'} and
                    refs['cluster'] == cluster_entry['name'] and refs['user'] == user_entry['name'],
                    'FOUNDATION_TRUST_MISMATCH')
                _require(isinstance(cluster, dict) and set(cluster) <=
                    {'server', 'certificate-authority-data', 'insecure-skip-tls-verify'} and
                    cluster.get('insecure-skip-tls-verify', False) is False and
                    isinstance(user, dict) and set(user) == {'client-certificate-data', 'client-key-data'},
                    'FOUNDATION_TRUST_MISMATCH')
                server = cluster['server']
                parsed = urllib.parse.urlsplit(server)
                _require(server == kube['api_server'] and server.startswith('https://') and
                    parsed.hostname and re.fullmatch('[A-Za-z0-9.:-]+', parsed.hostname) and
                    parsed.username is None and parsed.password is None and
                    not (parsed.path or parsed.query or parsed.fragment) and
                    0 < (parsed.port or 443) <= 65535, 'FOUNDATION_TRUST_MISMATCH')
                material = [base64.b64decode(value, validate=True) for value in
                    (cluster['certificate-authority-data'], user['client-certificate-data'], user['client-key-data'])]
                ca, cert, key = material
                _require(all(material) and hashlib.sha256(ca).hexdigest() == kube['ca_sha256'],
                    'FOUNDATION_TRUST_MISMATCH')
                fingerprint = (server, *(hashlib.sha256(value).digest() for value in material))
                if fingerprint == self._fingerprint and self._connection is not None and self._epoch == epoch:
                    return
                self.close()
                context = ssl.create_default_context(cadata=ca.decode('ascii'))
                _require(context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True,
                    'FOUNDATION_TRUST_MISMATCH')
                # TemporaryDirectory is 0700; NamedTemporaryFile creates 0600 files.
                # OpenSSL retains the loaded material, so delete files immediately.
                with tempfile.TemporaryDirectory(prefix='shirokuma-kube-') as scratch:
                    with tempfile.NamedTemporaryFile(dir=scratch) as cert_file, \
                            tempfile.NamedTemporaryFile(dir=scratch) as key_file:
                        cert_file.write(cert)
                        key_file.write(key)
                        cert_file.flush()
                        key_file.flush()
                        context.load_cert_chain(cert_file.name, key_file.name, password='')
                self._connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443,
                    timeout=15, context=context)
                self._fingerprint = fingerprint
                self._epoch = epoch
            except Exception:
                self.close()
                raise Rejected('FOUNDATION_TRUST_MISMATCH') from None

    @staticmethod
    def _target(args, kube, prefix):
        _require(isinstance(args, (list, tuple)) and all(isinstance(arg, str) for arg in args) and
            args[:1] == ['get'], 'PROVIDER_METADATA_TARGET_INVALID')
        name = None
        if len(args) == 5 and args[-2:] == ['-o', 'json']:
            kind, name = args[1:3]
            _require(len(name) <= 253 and re.fullmatch('[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?', name),
                'PROVIDER_METADATA_TARGET_INVALID')
            cluster = {'node': 'nodes', 'namespace': 'namespaces', 'pv': 'persistentvolumes'}
            core = {'service': 'services', 'pod': 'pods', 'pvc': 'persistentvolumeclaims', 'configmap': 'configmaps'}
            apps = {'deployment': 'deployments', 'replicaset': 'replicasets'}
            _require(kind not in ('node', 'namespace') or name == kube[
                'node_name' if kind == 'node' else 'namespace'], 'PROVIDER_METADATA_TARGET_INVALID')
            segment = urllib.parse.quote(name, safe='')
            if kind in cluster:
                return '/api/v1/'+cluster[kind]+'/'+segment, name
            namespace = urllib.parse.quote(kube['namespace'], safe='')
            if kind in core:
                return '/api/v1/namespaces/'+namespace+'/'+core[kind]+'/'+segment, name
            if kind in apps:
                return '/apis/apps/v1/namespaces/'+namespace+'/'+apps[kind]+'/'+segment, name
        if args == ['get', 'pods', '-o', 'json'] or (len(args) == 6 and
                args[:3] == ['get', 'pods', '-l'] and args[-2:] == ['-o', 'json']):
            path = '/api/v1/namespaces/'+urllib.parse.quote(kube['namespace'], safe='')+'/pods'
            if len(args) == 6:
                _require(args[3] in {'app.kubernetes.io/name='+workload for workload in
                    (prefix+'-seaweed', prefix+'-postgres', prefix+'-polaris', 'trino-local')},
                    'PROVIDER_METADATA_TARGET_INVALID')
                path += '?'+urllib.parse.urlencode({'labelSelector': args[3]})
            return path, None
        raise Rejected('PROVIDER_METADATA_TARGET_INVALID')

    def get(self, args, kube, prefix, *, epoch):
        with self._lock:
            # An old caller must neither use nor close a newer lifecycle's TLS.
            _require(self._epoch == epoch, 'FOUNDATION_CLOSED')
            try:
                path, name = self._target(args, kube, prefix)
                _require(self._connection is not None, 'FOUNDATION_TRUST_MISMATCH')
                self._connection.request('GET', path, headers={'Accept': 'application/json'})
                response = self._connection.getresponse()
                _require(response.status == 200, 'PROVIDER_METADATA_OBSERVATION_FAILED')
                raw = response.read(self.LIMIT+1)
                _require(len(raw) <= self.LIMIT, 'PROVIDER_METADATA_OBSERVATION_FAILED')
                value = json.loads(raw)
                _require(isinstance(value, dict) and isinstance(value.get('metadata'), dict),
                    'PROVIDER_METADATA_OBSERVATION_FAILED')
                items = value.get('items') if name is None else [value]
                _require(isinstance(items, list), 'PROVIDER_METADATA_OBSERVATION_FAILED')
                for item in items:
                    metadata = item.get('metadata', {})
                    _require(isinstance(metadata.get('uid'), str) and bool(metadata['uid'].strip()) and
                        isinstance(metadata.get('name'), str) and bool(metadata['name']) and
                        (name is None or metadata['name'] == name), 'PROVIDER_METADATA_OBSERVATION_FAILED')
                return raw
            except Exception:
                self.close()
                raise Rejected('PROVIDER_METADATA_OBSERVATION_FAILED') from None


class Provider:
    def __init__(self, config, *, command=None, http=None, sdk=None, popen=None):
        self._config = foundation.load(config) if isinstance(config, (str, Path)) else foundation.validate(config)
        self._parallel_reads = all(transport is None for transport in (command, http, sdk, popen))
        self._command = command or _command
        self._metadata_transport = _KubeMetadata() if command is None else None
        self._http_transport = http or _http
        self._sdk_factory = sdk or _sdk
        self._popen = popen or subprocess.Popen
        self._children, self._tunnels, self._incarnation = [], {}, {}
        self._opened = False
        self._startup_verified = False
        self._bound_pins = {}
        self._tokens = {}
        self._token_deadlines = {}
        self._lock = threading.RLock()
        self._close_epoch = 0
        self._closed = False
        self._effects = ContextVar("logical_provider_effects", default=None)

    @contextmanager
    def effects(self, callback):
        """Caller persists each leaf intent/ACK; no provider-owned durable ledger."""
        _require(callable(callback))
        token = self._effects.set(callback)
        try:
            yield self
        finally:
            self._effects.reset(token)

    def _effect(self, method, service, target, payload, call):
        callback = self._effects.get()
        _require(callback is not None, 'EFFECTS_REQUIRED_NO_EFFECTS')
        identity = {'method': method, 'service': service, 'target': target,
            'payload_sha256': hashlib.sha256(payload).hexdigest()}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        # The leaf validates/decodes its JSON-safe ACK inside call. A caller may
        # return a frozen persisted ACK without dispatching this or any later leaf.
        attempted = False
        def dispatch_once():
            nonlocal attempted
            if attempted:
                raise Unknown('EFFECT_ALREADY_ATTEMPTED')
            attempted = True
            raw = call()
            return raw
        return callback(key, identity, dispatch_once)

    @property
    def config(self):
        return copy.deepcopy(self._config)

    def _raw(self, argv, *, input=None, mutation=False):
        try:
            result = self._command(argv, input=input)
        except Exception:
            raise (Unknown() if mutation else Rejected('PROVIDER_OBSERVATION_FAILED')) from None
        if isinstance(result, (bytes, str)):
            return result.encode() if isinstance(result, str) else result
        if result.returncode:
            # kubectl can lose the response after exec dispatch. Never infer denial
            # from arbitrary stderr/JVM failure. Only publisher's exact sentinel.
            if result.returncode == 23 and b'LOGICAL_SECRETS_DENIED' in result.stderr.splitlines():
                raise Rejected('MATERIAL_PUBLICATION_DENIED')
            if result.returncode == 22 and b'LOGICAL_SECRETS_CAS_STALE' in result.stderr.splitlines():
                raise StaleCAS()
            raise (Unknown() if mutation else Rejected('PROVIDER_OBSERVATION_FAILED'))
        return result.stdout

    def _json(self, raw):
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            raise Rejected('PROVIDER_RESPONSE_INVALID') from None

    def _active_epoch(self, expected=None):
        with self._lock:
            _require(not self._closed and (expected is None or expected == self._close_epoch),
                'FOUNDATION_CLOSED')
            return self._close_epoch

    def _trust(self):
        epoch = self._active_epoch()
        try:
            self._trust_current(epoch)
        except Exception:
            if self._metadata_transport is not None:
                self._metadata_transport.close(epoch=epoch)
                raise Rejected('FOUNDATION_TRUST_MISMATCH') from None
            raise

    def _metadata(self, args, *, epoch=None):
        epoch = self._active_epoch(epoch)
        if self._metadata_transport is None:
            return self._raw(foundation.kube_argv(self._config) + args)
        return self._metadata_transport.get(args, self._config['kube'], self._config['prefix'], epoch=epoch)

    def _trust_current(self, epoch):
        c, k = self._config, self._config['kube']
        base = foundation.kube_argv(c)
        view = self._json(self._raw(base + ['config', 'view', '--minify', '--raw', '--flatten', '-o', 'json']))
        # Configure and close share the state lock; metadata HTTP does not.
        with self._lock:
            self._active_epoch(epoch)
            if self._metadata_transport is not None:
                self._metadata_transport.configure(view, k, epoch=epoch)
        try:
            cluster = view['clusters'][0]['cluster']
            ca = base64.b64decode(cluster['certificate-authority-data'], validate=True)
            _require(cluster['server'] == k['api_server'] and not cluster.get('insecure-skip-tls-verify'), 'FOUNDATION_TRUST_MISMATCH')
            _require(hashlib.sha256(ca).hexdigest() == k['ca_sha256'], 'FOUNDATION_TRUST_MISMATCH')
            node = self._json(self._metadata(['get', 'node', k['node_name'], '-o', 'json'], epoch=epoch))
            _require(node['metadata']['uid'] == k['node_uids'][k['node_name']], 'FOUNDATION_TRUST_MISMATCH')
        except (KeyError, ValueError, TypeError):
            raise Rejected('FOUNDATION_TRUST_MISMATCH') from None
        # The namespace UID, once observed, fences namespace replacement.
        if 'namespace' in self._incarnation:
            namespace = self._json(self._metadata(['get', 'namespace', k['namespace'], '-o', 'json'], epoch=epoch))
            _require(namespace['metadata']['uid'] == self._incarnation['namespace'], 'FOUNDATION_INCARNATION_CHANGED')
            self._labels(namespace)

    def _labels(self, resource):
        metadata = resource.get('metadata', {})
        labels = metadata.get('labels', {})
        _require(labels.get(foundation.LABEL) == self._config['foundation_id'] and
            labels.get(foundation.REVISION) == str(self._config['revision']), 'FOUNDATION_OWNERSHIP_MISMATCH')
        _require(bool(metadata.get('uid')), 'FOUNDATION_OWNERSHIP_MISMATCH')
        if metadata.get('name') == self._config['kube']['namespace'] and not metadata.get('namespace'):
            _require(labels.get('pod-security.kubernetes.io/enforce') == 'restricted' and
                labels.get('shirokuma.dev/native-admission') == 'logical-v1', 'FOUNDATION_ADMISSION_MISMATCH')
        return metadata['uid']

    def _cmd(self, args, *, input=None, mutation=False):
        # Public entries already establish trust before their metadata reads.
        # Exec and every other dispatch must refresh it independently.
        if not (mutation is False and args and args[0] == 'get'):
            self._trust()
        if self._metadata_transport is not None and args and args[0] == 'get':
            _require(input is None and mutation is False, 'PROVIDER_METADATA_TARGET_INVALID')
            return self._metadata(args)
        return self._raw(foundation.kube_argv(self._config) + args, input=input, mutation=mutation)

    def _owned(self, kind, name):
        resource = self._json(self._cmd(['get', kind, name, '-o', 'json']))
        uid = self._labels(resource)
        key = kind+'/'+name
        if kind not in ('pod', 'replicaset'):
            with self._lock:
                if key in self._incarnation:
                    _require(self._incarnation[key] == uid, 'FOUNDATION_INCARNATION_CHANGED')
                self._incarnation[key] = uid
        if kind == 'service':
            _require(resource.get('spec', {}).get('selector') == {'app.kubernetes.io/name': name}, 'FOUNDATION_SERVICE_TARGET_MISMATCH')
        if kind != 'namespace':
            _require(resource.get('metadata', {}).get('namespace') == self._config['kube']['namespace'], 'FOUNDATION_NAMESPACE_MISMATCH')
        return resource

    def _pod(self, pod):
        self._labels(pod)
        _require(pod['spec'].get('nodeName') == self._config['kube']['node_name'], 'FOUNDATION_NODE_MISMATCH')
        _require(pod.get('status', {}).get('phase') == 'Running' and
            any(x.get('type') == 'Ready' and x.get('status') == 'True' for x in pod.get('status', {}).get('conditions', [])), 'FOUNDATION_COMPUTE_NOT_READY')
        owners = pod['metadata'].get('ownerReferences', [])
        _require(len(owners) == 1 and owners[0].get('kind') == 'ReplicaSet' and owners[0].get('controller'), 'FOUNDATION_OWNERSHIP_MISMATCH')
        rs = self._owned('replicaset', owners[0]['name'])
        _require(rs['metadata']['uid'] == owners[0]['uid'], 'FOUNDATION_OWNERSHIP_MISMATCH')
        parents = rs['metadata'].get('ownerReferences', [])
        _require(len(parents) == 1 and parents[0].get('kind') == 'Deployment' and parents[0].get('controller'), 'FOUNDATION_OWNERSHIP_MISMATCH')
        deployment = self._owned('deployment', parents[0]['name'])
        _require(deployment['metadata']['uid'] == parents[0]['uid'], 'FOUNDATION_OWNERSHIP_MISMATCH')
        _require(pod['metadata'].get('namespace') == self._config['kube']['namespace'], 'FOUNDATION_NAMESPACE_MISMATCH')
        return deployment['metadata']['name']

    def _compute(self):
        # All fixed matching compute must be owned and ready, including a stale
        # serving worker when the configured worker count is zero.
        pods = self._json(self._cmd(['get', 'pods', '-o', 'json']))['items']
        items = [pod for pod in pods if pod.get('metadata', {}).get('labels', {}).get('shirokuma.dev/logical-compute') == 'true' or
            any(c.get('name') == 'trino' or c.get('image') == self._config['images']['trino'] for c in pod.get('spec', {}).get('containers', []))]
        expected = {'trino-local': 1, 'trino-local-worker': self._config['trino']['workers']}
        counts = dict.fromkeys(expected, 0)
        result = []
        for name, replicas in expected.items():
            deployment = self._owned('deployment', name)
            _require(deployment.get('spec', {}).get('replicas') == replicas, 'FOUNDATION_COMPUTE_MISMATCH')
        for pod in items:
            owner = self._pod(pod)
            _require(owner in expected, 'FOUNDATION_OWNERSHIP_MISMATCH')
            counts[owner] += 1
            containers = pod['spec']['containers']
            _require(len(containers) == 1 and containers[0]['name'] == 'trino' and
                containers[0]['image'] == self._config['images']['trino'], 'FOUNDATION_IMAGE_MISMATCH')
            status = pod.get('status', {}).get('containerStatuses', [])
            _require(len(status) == 1 and status[0].get('ready') is True and
                status[0].get('imageID', '').endswith(self._config['images']['trino'].split('@')[1]), 'FOUNDATION_IMAGE_MISMATCH')
            security = pod['spec'].get('securityContext', {})
            _require(security.get('runAsUser') == 10000 and security.get('runAsGroup') == 10001 and
                security.get('runAsNonRoot') is True, 'FOUNDATION_COMPUTE_MISMATCH')
            mounts = containers[0].get('volumeMounts', [])
            runtime = [m for m in mounts if m.get('mountPath') == '/etc/trino-private-secrets']
            _require(len(runtime) == 1 and runtime[0].get('readOnly', False) is (owner == 'trino-local-worker') and
                not runtime[0].get('subPath') and not runtime[0].get('subPathExpr'), 'FOUNDATION_COMPUTE_MISMATCH')
            volumes = {v['name']: v for v in pod['spec'].get('volumes', [])}
            _require(volumes.get(runtime[0]['name'], {}).get('persistentVolumeClaim', {}).get('claimName') == 'trino-runtime', 'FOUNDATION_COMPUTE_MISMATCH')
            required_mounts = {'/tmp': ('tmp', False), '/opt/logical-secrets': ('code', True),
                '/usr/lib/trino/secrets-plugin/logical-file': ('code', True),
                '/etc/trino': ('config', True), '/etc/foundation': ('private', True)}
            for path, (name, readonly) in required_mounts.items():
                match = [m for m in mounts if m.get('mountPath') == path]
                _require(len(match) == 1 and match[0].get('name') == name and match[0].get('readOnly', False) is readonly,
                    'FOUNDATION_COMPUTE_MISMATCH')
            _require(not any(m.get('subPath') or m.get('subPathExpr') for m in mounts) and
                volumes.get('code', {}).get('configMap', {}).get('name') == self._config['prefix']+'-secrets-code' and
                volumes.get('private', {}).get('secret', {}).get('secretName') == self._config['prefix']+'-private' and
                'emptyDir' in volumes.get('tmp', {}), 'FOUNDATION_COMPUTE_MISMATCH')
            config_name = self._config['prefix']+('-trino-worker-config' if owner == 'trino-local-worker' else '-trino-config')
            _require(volumes.get('config', {}).get('configMap', {}).get('name') == config_name, 'FOUNDATION_COMPUTE_MISMATCH')
            security = containers[0].get('securityContext', {})
            _require(security.get('readOnlyRootFilesystem') is True and security.get('allowPrivilegeEscalation') is False and
                security.get('runAsUser') == 10000 and security.get('runAsNonRoot') is True and
                security.get('capabilities', {}).get('drop') == ['ALL'], 'FOUNDATION_COMPUTE_MISMATCH')
            if owner == 'trino-local-worker':
                _require(not pod['spec'].get('initContainers') and
                    not any(v.get('persistentVolumeClaim', {}).get('claimName') == 'trino-catalogs' for v in volumes.values()) and
                    not any(m.get('mountPath') == '/var/trino-catalogs' for m in mounts), 'FOUNDATION_COMPUTE_MISMATCH')
            else:
                catalogs = [m for m in mounts if m.get('mountPath') == '/var/trino-catalogs']
                _require(len(catalogs) == 1 and catalogs[0].get('readOnly', False) is False and
                    volumes.get(catalogs[0]['name'], {}).get('persistentVolumeClaim', {}).get('claimName') == 'trino-catalogs', 'FOUNDATION_COMPUTE_MISMATCH')
            result.append(pod)
        _require(counts == expected, 'FOUNDATION_COMPUTE_NOT_READY')
        return result

    def _publisher(self):
        pods = [p for p in self._compute() if self._pod(p) == 'trino-local']
        _require(len(pods) == 1, 'FOUNDATION_COMPUTE_NOT_READY')
        pod = pods[0]
        bound = self._owned('pod', pod['metadata']['name'])
        _require(bound['metadata']['uid'] == pod['metadata']['uid'], 'FOUNDATION_INCARNATION_CHANGED')
        return bound

    def _exec(self, mode, data=b'', *, reader=None):
        _require(mode in ('publish', 'read', 'resolve', 'catalog'))
        _require(reader is None or mode in ('read', 'resolve', 'catalog'))
        pod = self._publisher() if reader is None else reader
        if reader is not None:
            owner = self._pod(reader)
            _require(owner in ('trino-local', 'trino-local-worker') and
                (mode != 'catalog' or owner == 'trino-local'), 'FOUNDATION_OWNERSHIP_MISMATCH')
        # Re-read immediately before dispatch. Kubernetes exec has no UID precondition;
        # namespace RBAC and the trusted executor are part of this private boundary.
        current = self._owned('pod', pod['metadata']['name'])
        _require(current['metadata']['uid'] == pod['metadata']['uid'], 'FOUNDATION_INCARNATION_CHANGED')
        def dispatch():
            output = self._cmd(['exec', '-i', pod['metadata']['name'], '-c', 'trino', '--', *PUBLISHER, mode], input=data, mutation=mode == 'publish')
            try:
                lines = [line.split('=', 1) for line in output.decode().splitlines()]
                pairs = dict(lines)
                _require(len(pairs) == len(lines) and pairs.pop('status', None) == 'OK', 'PUBLISHER_RECEIPT_INVALID')
                expected = {'ref.sha256'} if mode == 'resolve' else {'catalog.sha256'} if mode == 'catalog' else {'target.'+name for name in TARGETS.values()}
                _require(set(pairs) == expected, 'PUBLISHER_RECEIPT_INVALID')
                _require(all(re.fullmatch('[0-9a-f]{64}|MISSING', v) for v in pairs.values()), 'PUBLISHER_RECEIPT_INVALID')
                return pairs
            except (ValueError, UnicodeError, Rejected):
                raise (Unknown('PUBLISHER_RECEIPT_INVALID') if mode == 'publish' else Rejected('PUBLISHER_RECEIPT_INVALID')) from None
        return self._effect('publish', 'trino-local', '/etc/trino-private-secrets', data, dispatch) if mode == 'publish' else dispatch()

    def open(self):
        with self._lock:
            self._closed = False
            if self._opened:
                self._trust()
                _require(all(p.poll() is None for p in self._children), 'FOUNDATION_TUNNEL_CLOSED')
                return self
            try:
                self._trust()
                namespace = self._owned('namespace', self._config['kube']['namespace'])
                self._incarnation['namespace'] = namespace['metadata']['uid']
                for service, port, scheme in ((self._config['prefix']+'-seaweed', 8333, 'http'),
                        (self._config['prefix']+'-polaris', 8181, 'http'), ('trino-local', 8080, 'https')):
                    self._owned('service', service)
                    # kubectl selects an ephemeral loopback port itself. Never adopt a
                    # pre-existing listener or take over a user-managed forward.
                    child = self._popen(foundation.kube_argv(self._config) + ['port-forward', '--address=127.0.0.1',
                        'service/'+service, ':'+str(port)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                    self._children.append(child)
                    lines = queue.Queue(maxsize=32)
                    def drain(stream, sink):
                        for line in iter(stream.readline, b''):
                            try:
                                sink.put_nowait(line[:256])
                            except queue.Full:
                                pass
                    threading.Thread(target=drain, args=(child.stdout, lines), daemon=True).start()
                    deadline, local = time.monotonic()+10, None
                    while time.monotonic() < deadline and child.poll() is None:
                        try:
                            line = lines.get(timeout=0.1)
                        except queue.Empty:
                            continue
                        match = re.fullmatch(rb'Forwarding from 127\.0\.0\.1:(\d+) -> \d+\s*', line)
                        if match:
                            local = int(match[1])
                            break
                    _require(local is not None and child.poll() is None, 'FOUNDATION_TUNNEL_FAILED')
                    self._tunnels[service] = f'{scheme}://127.0.0.1:{local}'
                self._opened = True
                return self
            except Exception:
                self.close()
                raise Rejected('FOUNDATION_TUNNEL_FAILED') from None

    def close(self):
        with self._lock:
            self._closed = True
            self._close_epoch += 1
            if self._metadata_transport is not None:
                self._metadata_transport.close()
            self._startup_verified = False
            self._bound_pins = {}
            for child in reversed(self._children):
                try:
                    if child.poll() is None:
                        child.terminate()
                        try:
                            child.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            child.wait(timeout=3)
                    if child.stdout:
                        child.stdout.close()
                except (OSError, subprocess.SubprocessError):
                    pass
            self._children.clear()
            self._tunnels.clear()
            self._tokens.clear()
            self._token_deadlines.clear()
            self._opened = False

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    def _services(self):
        return (self._config['prefix']+'-seaweed', self._config['prefix']+'-polaris', 'trino-local')

    def _pvc_names(self):
        return ('trino-runtime', 'trino-catalogs', self._config['prefix']+'-seaweed-data',
            self._config['prefix']+'-postgres-data')

    def _stable_pins(self, incarnation):
        _require(isinstance(incarnation, dict), 'FOUNDATION_BINDING_INVALID')
        pins = {key: value for key, value in incarnation.items() if isinstance(key, str) and
            (key == 'namespace' or key in self._pvc_names() or
             key.startswith(('namespace/', 'service/', 'deployment/', 'pvc/', 'pv/', 'configmap/')))}
        _require(all(isinstance(value, str) and bool(value.strip()) for value in pins.values()),
            'FOUNDATION_BINDING_INVALID')
        return pins

    def bind_registered(self, expected_probe: dict) -> None:
        """Bind a saved result to our own full startup proof, never adopt its verdict."""
        with self._lock:
            self._startup_verified = False
            self._bound_pins = {}
            actual = self.probe()
            for proof in (actual, expected_probe):
                _require(isinstance(proof, dict) and proof.get('ready') is True and
                    proof.get('foundation_id') == self._config['foundation_id'] and
                    type(proof.get('revision')) is int and proof['revision'] == self._config['revision'],
                    'FOUNDATION_BINDING_INVALID')
                _require(isinstance(proof.get('spi_sha256'), str) and
                    re.fullmatch('[0-9a-f]{64}', proof['spi_sha256']) is not None,
                    'FOUNDATION_BINDING_INVALID')
                _require(isinstance(proof.get('incarnation'), dict) and
                    proof['incarnation'].get('node') == self._config['kube']['node_uids'],
                    'FOUNDATION_BINDING_INVALID')
            pins = self._stable_pins(actual['incarnation'])
            expected = self._stable_pins(expected_probe['incarnation'])
            mandatory = {'namespace', 'namespace/'+self._config['kube']['namespace'],
                'configmap/'+self._config['prefix']+'-secrets-code',
                *self._pvc_names(), *('pvc/'+name for name in self._pvc_names()),
                *('service/'+name for name in self._services()),
                *('deployment/'+name for name in ('trino-local', 'trino-local-worker', 'trino-opa',
                    self._config['prefix']+'-postgres', self._config['prefix']+'-seaweed',
                    self._config['prefix']+'-polaris'))}
            _require(mandatory <= pins.keys() and sum(key.startswith('pv/') for key in pins) == 4 and
                pins == expected and
                actual['spi_sha256'] == expected_probe['spi_sha256'], 'FOUNDATION_BINDING_MISMATCH')
            # PVs and direct PVC aliases were formerly returned only in the proof.
            self._incarnation.update(pins)
            self._bound_pins = dict(pins)
            _require(self._opened and len(self._children) == 3 and
                set(self._tunnels) == set(self._services()) and
                all(child.poll() is None for child in self._children), 'FOUNDATION_NOT_OPEN')
            self._startup_verified = True

    def bound_alive(self) -> bool:
        """Local capability gate: no observation, private reads or reconnects."""
        return (self._startup_verified and self._opened and len(self._children) == 3 and
            set(self._tunnels) == set(self._services()) and
            all(child.poll() is None for child in self._children))

    def _stable_health(self):
        for key, uid in self._bound_pins.items():
            if key == 'namespace' or key in self._pvc_names():
                continue  # Their namespaced keys cover these receipt aliases.
            kind, name = key.split('/', 1)
            if kind == 'pv':
                resource = self._json(self._cmd(['get', 'pv', name, '-o', 'json']))
            else:
                resource = self._owned(kind, name)
            _require(resource.get('metadata', {}).get('uid') == uid, 'FOUNDATION_INCARNATION_CHANGED')

    def compute_state(self) -> str:
        """Observe only the two fixed deployments, independently of port forwards."""
        try:
            self._trust()
            observed = []
            for name in ('trino-local', 'trino-local-worker'):
                deployment = self._owned('deployment', name)
                generation = deployment['metadata']['generation']
                desired = deployment['spec']['replicas']
                status = deployment.get('status', {})
                current = status.get('observedGeneration', 0)
                counts = [status.get(key, 0) for key in
                    ('readyReplicas', 'availableReplicas', 'updatedReplicas', 'replicas')]
                _require(type(generation) is int and generation > 0 and
                    all(type(value) is int and value >= 0 for value in (desired, current, *counts)),
                    'FOUNDATION_COMPUTE_STATE_INVALID')
                observed.append((desired, current == generation, counts))
            if all(desired == 0 and fresh and counts == [0]*4 for desired, fresh, counts in observed):
                return 'STOPPED'
            baseline = (1, self._config['trino']['workers'])
            if all(desired == expected and fresh and counts == [expected]*4
                    for (desired, fresh, counts), expected in zip(observed, baseline)):
                return 'ACTIVE'
            return 'TRANSITIONING'
        except Rejected:
            raise
        except Exception:
            raise Rejected('FOUNDATION_COMPUTE_STATE_INVALID') from None

    def live_health(self) -> dict:
        """Current read-only service/compute proof without SPI or material scans."""
        state = self.compute_state()
        _require(state != 'STOPPED', 'WAREHOUSE_START_REQUIRED')
        _require(state == 'ACTIVE', 'FOUNDATION_NOT_READY')
        ready = False
        try:
            _require(self.bound_alive(), 'FOUNDATION_NOT_BOUND')
            self._gate()
            self._stable_health()
            for service in self._services():
                self._owned('service', service)
            def compute_health():
                self._opa_health()
                nodes = self._compute()
                self._native_health(nodes)
                self._trino_health()
                self._sql_health()

            def catalog_health():
                self._polaris('GET', MANAGEMENT+'/principals/root')

            def storage_health():
                # boto3's shared default Session must create clients serially.
                self._iam('list_roles', MaxItems=1)
                self._storage('head_bucket', Bucket=self._config['storage']['bucket'])

            branches = (compute_health, catalog_health, storage_health)
            if self._parallel_reads:
                with ThreadPoolExecutor(max_workers=3) as pool:
                    futures = [pool.submit(branch) for branch in branches]
                    for future in futures:
                        future.result()
            else:
                for branch in branches:
                    branch()
            ready = True
        except Exception:
            pass
        return {'ready': ready, 'foundation_id': self._config['foundation_id'], 'revision': self._config['revision']}

    def publication_hashes(self, db_descriptor) -> dict:
        """Writer-only CAS input; complete all-replica proof remains check_material."""
        _require(self.bound_alive(), 'FOUNDATION_NOT_BOUND')
        self._gate(db_descriptor)
        hashes = self._exec('read')
        _require(isinstance(hashes, dict) and set(hashes) == {'target.'+name for name in TARGETS.values()} and
            all(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None
                for value in hashes.values()), 'PUBLISHER_RECEIPT_INVALID')
        return hashes

    def _gate(self, db=None):
        _require(self._opened and all(p.poll() is None for p in self._children), 'FOUNDATION_NOT_OPEN')
        if db is not None:
            _require(isinstance(db, dict) and set(db) == {'database_id', 'sql_catalog', 'generation_id', 'foundation_revision'})
            _require(db['generation_id'] == self._config['foundation_id'] and type(db['foundation_revision']) is int and
                db['foundation_revision'] == self._config['revision'], 'FOUNDATION_REVISION_MISMATCH')
            _require(isinstance(db['database_id'], str) and bool(REF.fullmatch(db['database_id'])))
            _require(isinstance(db['sql_catalog'], str) and bool(SQL_NAME.fullmatch(db['sql_catalog'])))
        self._trust()

    def _request(self, service, method, path, *, body=None, headers=None, raw=False, business=None, journal=True):
        self._gate()
        _require(service in self._tunnels and path.startswith('/') and not path.startswith('//'))
        self._owned('service', service)
        data = body if isinstance(body, bytes) else json.dumps(body, sort_keys=True, separators=(',', ':')).encode() if body is not None else None
        request_headers = dict(headers or {})
        if body is not None:
            request_headers.setdefault('Content-Type', 'application/json')
        mutation = method not in ('GET', 'HEAD') if business is None else business
        def dispatch():
            try:
                status, response = self._http_transport(method, self._tunnels[service]+path, headers=request_headers,
                    data=data, ca_file=self._config['trino']['ca_file'] if service == 'trino-local' else None)
            except Exception:
                raise (Unknown() if mutation else Rejected('PROVIDER_OBSERVATION_FAILED')) from None
            if type(status) is not int:
                raise (Unknown() if mutation else Rejected('PROVIDER_RESPONSE_INVALID'))
            if not 200 <= status < 300:
                if status in KNOWN_REJECTIONS:
                    raise Rejected('PROVIDER_HTTP_'+str(status))
                raise (Unknown() if mutation else Rejected('PROVIDER_OBSERVATION_FAILED'))
            try:
                # Mutation receipts are decoded inside the leaf, before durable ACK.
                # Read-only callers alone may request bytes.
                if raw and not mutation:
                    return response
                value = json.loads(response) if response else {}
                if not isinstance(value, dict):
                    raise ValueError()
                if mutation and service == 'trino-local' and path == '/v1/statement':
                    if (not isinstance(value.get('id'), str) or not value['id'] or
                            'error' in value and not isinstance(value['error'], dict) or
                            'data' in value and not isinstance(value['data'], list) or
                            'nextUri' in value and not isinstance(value['nextUri'], str)):
                        raise ValueError()
                return value
            except (TypeError, ValueError):
                raise (Unknown() if mutation else Rejected('PROVIDER_RESPONSE_INVALID')) from None
        return self._effect(method, service, path, data or b'', dispatch) if mutation and journal else dispatch()

    def _private(self, section, key):
        try:
            return json.loads(foundation.private_read(self._config[section][key]))
        except Exception:
            raise Rejected('FOUNDATION_PRIVATE_MATERIAL_INVALID') from None

    def _polaris(self, method, path, body=None, consumer=None):
        credentials = consumer or self._private('polaris', 'root_credentials_file')
        realm = self._config['polaris']['realm']
        _require(not consumer or consumer.get('realm') == realm, 'POLARIS_REALM_MISMATCH')
        client = credentials['client_id']
        with self._lock:
            token = self._tokens.get(client)
            if self._token_deadlines.get(client, float('inf')) <= time.monotonic():
                token = None
        service = self._config['prefix']+'-polaris'
        if token is None:
            response = self._request(service, 'POST', '/api/catalog/v1/oauth/tokens',
                body=urllib.parse.urlencode({'grant_type': 'client_credentials', 'client_id': client,
                    'client_secret': credentials['client_secret'], 'scope': 'PRINCIPAL_ROLE:ALL'}).encode(),
                headers={'Content-Type': 'application/x-www-form-urlencoded', 'Polaris-Realm': realm}, business=False)
            token = response.get('access_token')
            _require(isinstance(token, str) and token, 'POLARIS_TOKEN_INVALID')
            ttl = response.get('expires_in', 60)
            _require(type(ttl) in (int, float) and ttl > 0, 'POLARIS_TOKEN_INVALID')
            with self._lock:
                self._tokens[client] = token
                self._token_deadlines[client] = time.monotonic()+max(0, ttl-10)
        return self._request(service, method, path, body=body, headers={'Authorization': 'Bearer '+token,
            'Polaris-Realm': realm, 'X-Iceberg-Access-Delegation': 'vended-credentials'})

    def _iam(self, method, **params):
        return self._sdk_call('iam', method, params)

    def _issuer_identity(self):
        """Observe the current issuer with explicit credentials on our own tunnel."""
        self._gate()
        service = self._config['prefix']+'-seaweed'
        self._owned('service', service)
        issuer = self._private('storage', 'issuer_credentials_file')
        try:
            client = self._sdk_factory('sts', endpoint_url=self._tunnels[service],
                region_name=self._config['storage']['region'],
                aws_access_key_id=issuer['access_key'], aws_secret_access_key=issuer['secret_key'])
            identity = client.get_caller_identity()
        except Exception:
            raise Rejected('FOUNDATION_ISSUER_OBSERVATION_FAILED') from None
        _require(isinstance(identity, dict) and identity.get('Account') == '000000000000' and
            identity.get('Arn') == 'arn:aws:iam::000000000000:user/polaris-issuer',
            'FOUNDATION_ISSUER_IDENTITY_MISMATCH')

    def _storage(self, method, **params):
        return self._sdk_call('s3', method, params)

    def _sdk_call(self, service, method, params):
        self._gate()
        self._owned('service', self._config['prefix']+'-seaweed')
        credentials = self._private('storage', 'admin_credentials_file')
        readonly = method.startswith(('get_', 'list_', 'head_'))
        try:
            client = self._sdk_factory(service, endpoint_url=self._tunnels[self._config['prefix']+'-seaweed'],
                region_name=self._config['storage']['region'], aws_access_key_id=credentials['access_key'], aws_secret_access_key=credentials['secret_key'])
        except Exception:
            raise Rejected('PROVIDER_SDK_UNAVAILABLE_NO_EFFECTS') from None
        def dispatch():
            try:
                return getattr(client, method)(**params)
            except Exception as error:
                if _sdk_rejected(error):
                    status = error.response['ResponseMetadata']['HTTPStatusCode']
                    raise Rejected('PROVIDER_IAM_REJECTED' if service == 'iam' else 'PROVIDER_STORAGE_HTTP_'+str(status)) from None
                raise (Rejected('PROVIDER_OBSERVATION_FAILED') if readonly else Unknown()) from None
        if readonly:
            return dispatch()
        target = {k: params[k] for k in ('RoleName', 'PolicyName', 'PolicyArn', 'Bucket', 'Key') if k in params}
        payload = json.dumps(params, sort_keys=True, default=lambda v: hashlib.sha256(v).hexdigest()).encode()
        return self._effect(method, service, json.dumps(target, sort_keys=True), payload, dispatch)

    def _role(self, db):
        name = self._config['prefix']+'-'+db['database_id']
        if len(name) > 64:
            name = self._config['prefix'][:23]+'-'+hashlib.sha256(db['database_id'].encode()).hexdigest()[:40]
        return name

    def _location(self, db):
        return 's3://'+self._config['storage']['bucket']+'/'+db['database_id']+'/'

    def _arn(self, db, returned, role=None):
        account, name = self._config['storage']['account_id'], self._role(db)
        canonical = f'arn:aws:iam::{account}:role/{name}'
        # Seaweed 4.51 omits account from the legacy ARN. Only accept it from
        # this foundation's pinned IAM service, with a positively read RoleId
        # and RoleName. The account comes from its operator-pinned IAM config.
        legacy = f'arn:aws:iam::role/{name}'
        _require(returned in (canonical, legacy), 'IAM_ROLE_IDENTITY_MISMATCH')
        if returned == legacy:
            _require(isinstance(role, dict) and role.get('RoleName') == name and
                isinstance(role.get('RoleId'), str) and bool(role['RoleId']) and role.get('Arn') == legacy, 'IAM_ROLE_IDENTITY_MISMATCH')
        return canonical

    def _trust_policy(self):
        return {'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow',
            'Principal': {'AWS': 'arn:aws:iam::'+self._config['storage']['account_id']+':user/polaris-issuer'}, 'Action': 'sts:AssumeRole'}]}

    def _policy(self, db):
        bucket, key = self._config['storage']['bucket'], db['database_id']+'/'
        return {'Version': '2012-10-17', 'Statement': [
            {'Effect': 'Allow', 'Action': ['s3:ListBucket'], 'Resource': ['arn:aws:s3:::'+bucket],
                'Condition': {'StringLike': {'s3:prefix': [key, key+'*']}}},
            {'Effect': 'Allow', 'Action': ['s3:GetObject', 's3:PutObject', 's3:DeleteObject', 's3:AbortMultipartUpload', 's3:ListMultipartUploadParts'],
                'Resource': ['arn:aws:s3:::'+bucket+'/'+key+'*']}]}

    def create_role(self, db):
        self._gate(db)
        result = self._iam('create_role', RoleName=self._role(db), AssumeRolePolicyDocument=json.dumps(self._trust_policy()))
        if not isinstance(result, dict) or not isinstance(result.get('Role'), dict) or not isinstance(result['Role'].get('Arn'), str):
            raise Unknown('IAM_ROLE_RECEIPT_INVALID')
        role = result['Role']
        if role.get('Arn') == f'arn:aws:iam::role/{self._role(db)}':
            observed = self._iam('get_role', RoleName=self._role(db))['Role']
            _require(observed.get('RoleId') == role.get('RoleId'), 'IAM_ROLE_IDENTITY_MISMATCH')
            role = observed
        arn = self._arn(db, role['Arn'], role)
        result = self._iam('create_policy', PolicyName=self._role(db), PolicyDocument=json.dumps(self._policy(db)))
        if not isinstance(result, dict) or not isinstance(result.get('Policy'), dict) or not isinstance(result['Policy'].get('Arn'), str):
            raise Unknown('IAM_POLICY_RECEIPT_INVALID')
        policy = result['Policy']
        policy_arn = self._policy_identity(db, policy)
        self._iam('attach_role_policy', RoleName=self._role(db), PolicyArn=policy_arn)
        return {'role_arn': arn}

    def _policy_arn(self, db, returned):
        name, account = self._role(db), self._config['storage']['account_id']
        _require(returned in (f'arn:aws:iam::{account}:policy/{name}', f'arn:aws:iam:::policy/{name}'), 'IAM_POLICY_IDENTITY_MISMATCH')
        # Keep the exact known service identity for management API readback.
        return returned

    def _policy_identity(self, db, policy):
        _require(isinstance(policy, dict) and policy.get('PolicyName') == self._role(db) and
            isinstance(policy.get('PolicyId'), str) and bool(policy['PolicyId'].strip()),
            'IAM_POLICY_IDENTITY_MISMATCH')
        return self._policy_arn(db, policy.get('Arn'))

    def check_role(self, db):
        self._gate(db)
        role = self._iam('get_role', RoleName=self._role(db))['Role']
        arn = self._arn(db, role['Arn'], role)
        listing = self._iam('list_attached_role_policies', RoleName=self._role(db))
        if listing.get('IsTruncated'):
            verified = (False)
            return _proof(db, 'role', verified, role_arn=arn)
        attached = listing.get('AttachedPolicies', [])
        expected = [item for item in attached if item.get('PolicyName') == self._role(db)]
        if len(expected) != 1 or len(attached) != 1:
            verified = (False)
            return _proof(db, 'role', verified, role_arn=arn)
        policy_arn = self._policy_arn(db, expected[0]['PolicyArn'])
        policy = self._iam('get_policy', PolicyArn=policy_arn)['Policy']
        _require(self._policy_identity(db, policy) == policy_arn, 'IAM_POLICY_IDENTITY_MISMATCH')
        document = self._iam('get_policy_version', PolicyArn=policy_arn, VersionId=policy['DefaultVersionId'])['PolicyVersion']['Document']
        verified = (_semantic_policy(document) == _semantic_policy(self._policy(db)) and
            _semantic_policy(role.get('AssumeRolePolicyDocument')) == _semantic_policy(self._trust_policy()))
        return _proof(db, 'role', verified, role_arn=arn)

    def _catalog(self, db):
        s = self._config['storage']
        return {'name': db['sql_catalog'], 'type': 'INTERNAL',
            'properties': {'default-base-location': self._location(db)},
            'storageConfigInfo': {'storageType': 'S3', 'allowedLocations': [self._location(db)],
                'region': s['region'], 'endpoint': s['endpoint'], 'endpointInternal': s['endpoint'],
                'stsEndpoint': s['sts_endpoint'], 'stsUnavailable': False, 'kmsUnavailable': True,
                'pathStyleAccess': True, 'roleArn': self._arn(db, f"arn:aws:iam::{s['account_id']}:role/{self._role(db)}")}}

    def create_catalog(self, db):
        self._gate(db)
        return self._polaris('POST', MANAGEMENT+'/catalogs', {'catalog': self._catalog(db)})

    def check_catalog(self, db):
        self._gate(db)
        actual = self._polaris('GET', MANAGEMENT+'/catalogs/'+db['sql_catalog'])['catalog']
        expected = self._catalog(db)
        verified = (all(actual.get(k) == v for k, v in expected.items()))
        return _proof(db, 'catalog', verified)

    def issue_consumer(self, db):
        self._gate(db)
        name = self._role(db)+'-consumer'
        # NO follow-up API, publication or readback after the credential POST.
        response = self._polaris('POST', MANAGEMENT+'/principals', {'principal': {'name': name}, 'credentialRotationRequired': False})
        if not isinstance(response, dict):
            raise Unknown('CONSUMER_RECEIPT_INVALID')
        credentials = response.get('credentials', response)
        if not isinstance(credentials, dict) or not all(isinstance(credentials.get(k), str) and credentials[k] for k in ('clientId', 'clientSecret')):
            raise Unknown('CONSUMER_RECEIPT_INVALID')
        try:
            return {'client_id': credentials['clientId'], 'client_secret': credentials['clientSecret'],
                'realm': self._config['polaris']['realm'], 'principal_name': name}
        except KeyError:
            raise Unknown('CONSUMER_RECEIPT_INVALID') from None

    def check_consumer(self, db, consumer):
        self._gate(db)
        _require(isinstance(consumer, dict) and set(consumer) == {'client_id', 'client_secret', 'realm', 'principal_name'})
        _require(consumer.get('principal_name') == self._role(db)+'-consumer' and consumer.get('realm') == self._config['polaris']['realm'])
        p = self._polaris('GET', MANAGEMENT+'/principals/'+consumer['principal_name'])['principal']
        verified = (p.get('name') == consumer['principal_name'] and p.get('clientId') == consumer['client_id'])
        return _proof(db, 'consumer', verified)

    def create_namespaces(self, db, consumer):
        self._gate(db)
        _require(consumer.get('principal_name') == self._role(db)+'-consumer')
        role, cat = self._role(db), db['sql_catalog']
        self._polaris('POST', MANAGEMENT+'/principal-roles', {'principalRole': {'name': role}})
        self._polaris('POST', MANAGEMENT+'/catalogs/'+cat+'/catalog-roles', {'catalogRole': {'name': role}})
        self._polaris('PUT', MANAGEMENT+'/principals/'+consumer['principal_name']+'/principal-roles', {'principalRole': {'name': role}})
        self._polaris('PUT', MANAGEMENT+'/principal-roles/'+role+'/catalog-roles/'+cat, {'catalogRole': {'name': role}})
        for privilege in ('CATALOG_MANAGE_CONTENT',):
            self._polaris('PUT', MANAGEMENT+'/catalogs/'+cat+'/catalog-roles/'+role+'/grants', {'grant': {'type': 'catalog', 'privilege': privilege}})
        for schema in SCHEMAS:
            self._polaris('POST', '/api/catalog/v1/'+cat+'/namespaces', {'namespace': [schema], 'properties': {}}, consumer=consumer)
        return {'namespaces': list(SCHEMAS)}

    def check_namespaces(self, db, consumer):
        self._gate(db)
        _require(self.check_consumer(db, consumer)['verified'], 'CONSUMER_IDENTITY_MISMATCH')
        role, cat = self._role(db), db['sql_catalog']
        principal_roles = self._polaris('GET', MANAGEMENT+'/principals/'+consumer['principal_name']+'/principal-roles')['roles']
        catalog_roles = self._polaris('GET', MANAGEMENT+'/principal-roles/'+role+'/catalog-roles/'+cat)['roles']
        grants = self._polaris('GET', MANAGEMENT+'/catalogs/'+cat+'/catalog-roles/'+role+'/grants')['grants']
        result = self._polaris('GET', '/api/catalog/v1/'+cat+'/namespaces', consumer=consumer)
        def single_role(roles):
            return isinstance(roles, list) and len(roles) == 1 and roles[0].get('name') == role
        verified = (single_role(principal_roles) and single_role(catalog_roles) and
            grants == [{'type': 'catalog', 'privilege': 'CATALOG_MANAGE_CONTENT'}] and
            sorted(result.get('namespaces', [])) == sorted([[s] for s in SCHEMAS]))
        return _proof(db, 'namespaces', verified, grants_verified=verified)

    def _material(self, material):
        _require(isinstance(material, dict) and set(material) <= set(TARGETS) | {'expected_hashes'} and 'credentials' in material)
        credentials = material['credentials']
        _require(isinstance(credentials, dict))
        values = {}
        for ref, value in credentials.items():
            if isinstance(value, dict):
                _require(set(value) in ({'client_id', 'client_secret'}, {'version', 'client_id', 'client_secret'}))
                _require('version' not in value or type(value['version']) is int and value['version'] == 1)
                _require(all(isinstance(value[k], str) and value[k] for k in ('client_id', 'client_secret')))
                _require(':' not in value['client_id'])
                value = value['client_id']+':'+value['client_secret']
            _require(isinstance(ref, str) and bool(REF.fullmatch(ref)) and isinstance(value, str) and 0 < len(value) <= 16384)
            values['credential.'+ref] = value
        expected = material.get('expected_hashes', {})
        _require(isinstance(expected, dict) and set(expected) <= set(TARGETS.values()))
        for key, filename in TARGETS.items():
            if key == 'credentials' or key not in material:
                continue
            _require(isinstance(material[key], str) and len(material[key]) <= 2*1024*1024)
            _require(filename in expected and re.fullmatch('[0-9a-f]{64}|MISSING', expected[filename]) is not None)
            values[key] = base64.b64encode(material[key].encode()).decode()
            values['expected.'+filename] = expected[filename]
        return values

    def publish_material(self, db, material):
        self._gate(db)
        values = self._material(material)
        # Same-host processes share a short publication lock, independent of DB
        # transactions/admission. Publisher also locks on the PVC and checks CAS.
        path = Path(self._config['private_dir']) / 'publication.lock'
        import os
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            current = self._exec('read')
            for filename, digest in material.get('expected_hashes', {}).items():
                if current.get('target.'+filename) != digest:
                    raise StaleCAS()
            return self._exec('publish', _properties(values)) if values else current
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def check_material(self, db, material):
        self._gate(db)
        normalized = self._material(material)
        nodes = self._compute()
        def observe(node):
            digests = self._exec('read', reader=node)
            resolved = {ref: self._exec('resolve', _properties({'ref': ref}), reader=node)
                for ref in material['credentials']}
            return digests, resolved

        if self._parallel_reads:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(observe, node) for node in nodes]
                observations = [future.result() for future in futures]
        else:
            observations = [observe(node) for node in nodes]
        digests = observations[0][0]
        def valid_hashes(value):
            return (isinstance(value, dict) and set(value) == {'target.'+name for name in TARGETS.values()} and
                all(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest) is not None
                    for digest in value.values()))
        matches = valid_hashes(digests)
        for observed, resolved in observations:
            matches &= valid_hashes(observed) and observed == digests
            for key, filename in TARGETS.items():
                if key != 'credentials' and key in material:
                    matches &= (isinstance(observed, dict) and
                        observed.get('target.'+filename) == hashlib.sha256(material[key].encode()).hexdigest())
            for ref, value in material['credentials'].items():
                result = resolved[ref]
                matches &= result.get('ref.sha256') == hashlib.sha256(normalized['credential.'+ref].encode()).hexdigest()
        verified = (bool(matches))
        return _proof(db, 'publication', verified, hashes=digests, compute_uids=[node['metadata']['uid'] for node in nodes])

    def _sql(self, sql, identity):
        _require(isinstance(identity.get('username'), str) and isinstance(identity.get('password'), str))
        headers = {'Authorization': 'Basic '+base64.b64encode((identity['username']+':'+identity['password']).encode()).decode(),
            'X-Trino-User': identity['username'], 'Content-Type': 'text/plain', 'X-Trino-Source': 'shirokuma-logical'}
        mutation = re.match(r'\s*(SELECT|SHOW)\b', sql, re.IGNORECASE) is None
        def execute():
            # Transport keeps mutation uncertainty, but only this whole action is journaled.
            result = self._request('trino-local', 'POST', '/v1/statement', body=sql.encode(),
                headers=headers, business=mutation, journal=False)
            query_id = result.get('id')
            rows = []
            for _ in range(256):
                if ('error' in result and not isinstance(result['error'], dict) or
                        'data' in result and not isinstance(result['data'], list) or
                        'nextUri' in result and not isinstance(result['nextUri'], str)):
                    raise (Unknown() if mutation else Rejected('PROVIDER_RESPONSE_INVALID'))
                if 'error' in result:
                    if result['error'].get('errorName') in ('PERMISSION_DENIED', 'ACCESS_DENIED'):
                        raise Rejected('ENGINE_ACCESS_DENIED')
                    raise (Unknown('ENGINE_QUERY_FAILED') if mutation else Rejected('ENGINE_QUERY_FAILED'))
                rows += result.get('data', [])
                if 'nextUri' not in result:
                    return {'rows': rows, 'query_id': query_id, 'success': True}
                try:
                    uri = urllib.parse.urlsplit(result['nextUri'])
                except ValueError:
                    raise (Unknown('ENGINE_CONTINUATION_INVALID') if mutation else Rejected('ENGINE_CONTINUATION_INVALID')) from None
                local = urllib.parse.urlsplit(self._tunnels['trino-local'])
                native = urllib.parse.urlsplit(self._config['trino']['endpoint'])
                if not (uri.scheme == 'https' and uri.netloc in (local.netloc, native.netloc) and uri.path.startswith('/v1/statement/')):
                    raise (Unknown('ENGINE_CONTINUATION_INVALID') if mutation else Rejected('ENGINE_CONTINUATION_INVALID'))
                try:
                    result = self._request('trino-local', 'GET', uri.path+('?' + uri.query if uri.query else ''), headers=headers)
                except ProviderError:
                    if mutation:
                        raise Unknown('ENGINE_CONTINUATION_UNKNOWN') from None
                    raise
            raise Unknown('ENGINE_QUERY_INCOMPLETE')
        receipt = self._effect('POST', 'trino-local', '/v1/statement', sql.encode(), execute) if mutation else execute()
        return receipt['rows']

    def _attach_properties(self, db, credential_ref):
        # No static S3 keys/per-DB env; all endpoints are native service DNS.
        props = {'iceberg.catalog.type': 'rest', 'iceberg.rest-catalog.uri': self._config['polaris']['endpoint']+'/api/catalog',
            'iceberg.rest-catalog.warehouse': db['sql_catalog'], 'iceberg.rest-catalog.security': 'OAUTH2',
            'iceberg.rest-catalog.oauth2.credential': '${file:'+credential_ref+'}',
            'iceberg.rest-catalog.oauth2.server-uri': self._config['polaris']['endpoint']+'/api/catalog/v1/oauth/tokens',
            'iceberg.rest-catalog.oauth2.token-exchange-enabled': 'false',
            'iceberg.rest-catalog.http-headers': 'Polaris-Realm:'+self._config['polaris']['realm'],
            'iceberg.rest-catalog.oauth2.scope': 'PRINCIPAL_ROLE:ALL', 'iceberg.rest-catalog.vended-credentials-enabled': 'true',
            'fs.s3.enabled': 'true', 's3.endpoint': self._config['storage']['endpoint'], 's3.region': self._config['storage']['region'], 's3.path-style-access': 'true'}
        return {'connector.name': 'iceberg', **props}

    def attach_catalog(self, db, credential_ref):
        self._gate(db)
        _require(isinstance(credential_ref, str) and bool(REF.fullmatch(credential_ref)))
        props = self._attach_properties(db, credential_ref)
        props.pop('connector.name')
        # Config validation excludes SQL quote characters in all interpolated values.
        options = ', '.join('"'+k+'" = \''+v.replace("'", "''")+"'" for k, v in props.items())
        self._sql('CREATE CATALOG "'+db['sql_catalog']+'" USING iceberg WITH ('+options+')', self._provisioner())
        return {'catalog': db['sql_catalog'], 'credential_ref': credential_ref}

    def _provisioner(self):
        identity = self._private('trino', 'provisioner_credentials_file')
        _require(isinstance(identity, dict) and set(identity) == {'version', 'username', 'password'} and
            type(identity['version']) is int and identity['version'] == 1 and identity['username'] == 'logical_provisioner' and
            isinstance(identity['password'], str) and bool(identity['password']), 'FOUNDATION_PRIVATE_MATERIAL_INVALID')
        return identity

    def check_attached(self, db, credential_ref):
        self._gate(db)
        _require(isinstance(credential_ref, str) and bool(REF.fullmatch(credential_ref)))
        read = self._exec('resolve', _properties({'ref': credential_ref}))
        rows = self._sql('SHOW CATALOGS', self._provisioner())
        observed = self._exec('catalog', _properties({'catalog': db['sql_catalog']}))
        expected = _catalog_digest(self._attach_properties(db, credential_ref))
        verified = ([db['sql_catalog']] in rows and bool(re.fullmatch('[0-9a-f]{64}', read.get('ref.sha256', ''))) and
            observed.get('catalog.sha256') == expected)
        return _proof(db, 'attach', verified, credential_ref=credential_ref, catalog_sha256=observed.get('catalog.sha256'), binding_verified=verified)

    def _verify_prefix(self, db, write_marker=True):
        """Fixed marker I/O under the DB role, never under the S3 administrator.

        Core must journal the verification intent before calling verify_target.
        Marker key/payload are deterministic so an uncertain PUT is observable;
        this method does not retry it or remove evidence on failure.
        """
        self._gate(db)
        issuer = self._private('storage', 'issuer_credentials_file')
        options = {'endpoint_url': self._tunnels[self._config['prefix']+'-seaweed'], 'region_name': self._config['storage']['region']}
        self._owned('service', self._config['prefix']+'-seaweed')
        try:
            sts = self._sdk_factory('sts', **options, aws_access_key_id=issuer['access_key'], aws_secret_access_key=issuer['secret_key'])
            role = 'arn:aws:iam::'+self._config['storage']['account_id']+':role/'+self._role(db)
            credentials = sts.assume_role(RoleArn=role, RoleSessionName='logical-verify', DurationSeconds=900)['Credentials']
            client = self._sdk_factory('s3', **options, aws_access_key_id=credentials['AccessKeyId'],
                aws_secret_access_key=credentials['SecretAccessKey'], aws_session_token=credentials['SessionToken'])
        except Exception as error:
            status = getattr(error, 'response', {}).get('ResponseMetadata', {}).get('HTTPStatusCode')
            if status in (400, 401, 403):
                raise Rejected('STORAGE_SESSION_UNAVAILABLE') from None
            raise Unknown('STORAGE_SESSION_UNKNOWN') from None
        marker = hashlib.sha256((db['generation_id']+':'+str(db['foundation_revision'])+':'+db['database_id']).encode()).digest()
        bucket = self._config['storage']['bucket']
        key = db['database_id']+'/.shirokuma/creation-marker'
        self._gate(db)
        def put_marker():
            try:
                return client.put_object(Bucket=bucket, Key=key, Body=marker)
            except Exception as error:
                if _sdk_rejected(error):
                    raise Rejected('STORAGE_MARKER_REJECTED') from None
                raise Unknown('STORAGE_MARKER_UNKNOWN') from None
        if write_marker:
            self._effect('put_object', 's3', bucket+'/'+key, marker, put_marker)
        self._gate(db)
        try:
            body = client.get_object(Bucket=bucket, Key=key)['Body']
            try:
                observed = body.read(4096)
            finally:
                body.close()
            _require(observed == marker, 'STORAGE_MARKER_MISMATCH')
        except ProviderError:
            raise
        except Exception:
            raise Rejected('STORAGE_MARKER_OBSERVATION_FAILED') from None
        self._gate(db)
        try:
            # This prefix is reserved outside every valid database ID prefix.
            unexpected = client.get_object(Bucket=bucket, Key='_logical_denied/creation-marker')
        except Exception as error:
            response = getattr(error, 'response', {})
            _require(response.get('ResponseMetadata', {}).get('HTTPStatusCode') == 403 and
                response.get('Error', {}).get('Code') == 'AccessDenied', 'STORAGE_NEGATIVE_PROOF_FAILED')
            return {'prefix_positive': True, 'prefix_negative': True, 'marker_sha256': hashlib.sha256(marker).hexdigest()}
        if isinstance(unexpected, dict) and 'Body' in unexpected:
            unexpected['Body'].close()
        raise Rejected('STORAGE_NEGATIVE_PROOF_FAILED')

    def _engine_identity(self, db, engine_identity):
        required = {'principal_id', 'database_id', 'selected_role', 'grant_epoch', 'policy_revision', 'username', 'password', 'credential_ref'}
        _require(isinstance(engine_identity, dict) and set(engine_identity) == required and engine_identity['database_id'] == db['database_id'])
        _require(all(isinstance(engine_identity[k], str) and engine_identity[k] for k in
            ('username', 'password', 'principal_id', 'selected_role', 'credential_ref')))
        _require(engine_identity['username'] != 'logical_provisioner' and bool(REF.fullmatch(engine_identity['principal_id'])) and
            engine_identity['selected_role'] in ('bi_viewer', 'data_engineer', 'admin') and
            (engine_identity['grant_epoch'] is None and engine_identity['selected_role'] == 'admin' or
                type(engine_identity['grant_epoch']) is int and engine_identity['grant_epoch'] > 0) and
            type(engine_identity['policy_revision']) is int and engine_identity['policy_revision'] > 0)
        _require(bool(REF.fullmatch(engine_identity['credential_ref'])))

    def verify_revoked(self, db, engine_identity):
        """Observe a prior known-valid persisted identity after caller's authority gate.

        Caller supplies the intended current grant/account gate and the exact prior
        issued tuple. Invented bad credentials do not prove an authority transition.
        Positive INTERNAL control against the same DB must precede old-user denial.
        """
        self._gate(db)
        self._engine_identity(db, engine_identity)
        provisioner = self._provisioner()
        _require(self._sql('SELECT current_user', provisioner) == [[provisioner['username']]], 'ENGINE_CONTROL_FAILED')
        statement = 'SHOW SCHEMAS FROM "'+db['sql_catalog']+'"'
        control = self._sql(statement, provisioner)
        _require(all([schema] in control for schema in SCHEMAS), 'ENGINE_CONTROL_FAILED')
        def denied():
            try:
                self._sql(statement, engine_identity)
            except Rejected as error:
                if error.code in ('PROVIDER_HTTP_401', 'PROVIDER_HTTP_403', 'ENGINE_ACCESS_DENIED'):
                    return True
                raise
            return False
        verified = self._refresh_read(denied)
        return {'verified': verified, 'control_positive': True, 'identity_denied': verified}

    @staticmethod
    def _refresh_read(observe):
        """Five-second refresh window for actual read-only engine observations."""
        deadline = time.monotonic() + 5.0
        while True:
            try:
                if observe():
                    return True
            except Rejected as error:
                if error.code not in ('PROVIDER_HTTP_401', 'PROVIDER_HTTP_403', 'ENGINE_ACCESS_DENIED'):
                    raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.1, remaining))

    def verify_target(self, db, engine_identity):
        return self._verify_target(db, engine_identity, write_marker=True)

    def check_verify_target(self, db, engine_identity):
        return self._verify_target(db, engine_identity, write_marker=False)

    @staticmethod
    def _role_visibility(rows, role):
        visible = {row[0] for row in rows if isinstance(row, list) and len(row) == 1}
        expected = {'marts'} if role == 'bi_viewer' else set(SCHEMAS)
        return expected <= visible and not (set(SCHEMAS)-expected) & visible

    def _verify_target(self, db, engine_identity, *, write_marker):
        self._gate(db)
        self._engine_identity(db, engine_identity)
        positive = denied = False
        def observe():
            nonlocal positive, denied
            positive = denied = False
            actual = self._sql('SELECT current_user', engine_identity)
            _require(actual == [[engine_identity['username']]], 'ENGINE_IDENTITY_MISMATCH')
            rows = self._sql('SHOW SCHEMAS FROM "'+db['sql_catalog']+'"', engine_identity)
            positive = self._role_visibility(rows, engine_identity['selected_role'])
            # Only a real engine denial proves this fixed negative query.
            try:
                self._sql('SELECT node_id FROM system.runtime.nodes', engine_identity)
            except Rejected as error:
                if error.code == 'ENGINE_ACCESS_DENIED':
                    denied = True
                else:
                    raise
            return positive and denied
        observed = self._refresh_read(observe)
        prefix = self._verify_prefix(db, write_marker=write_marker) if observed else {'prefix_positive': False, 'prefix_negative': False}
        runtime = {}
        nodes_valid = True
        if self._config['trino']['workers'] == 1 and observed:
            nodes = self._sql('SELECT node_id, coordinator, state FROM system.runtime.nodes', self._provisioner())
            runtime = {'runtime_nodes': nodes}
            nodes_valid = (len(nodes) == 2 and all(isinstance(row, list) and len(row) == 3 and
                isinstance(row[0], str) and type(row[1]) is bool and row[2] == 'active' for row in nodes) and
                len({row[0] for row in nodes}) == 2 and sorted(row[1] for row in nodes) == [False, True])
        pins = {key: value for key, value in engine_identity.items() if key not in ('password', 'database_id')}
        return _proof(db, 'verification', observed and prefix['prefix_positive'] and prefix['prefix_negative'] and nodes_valid,
            **prefix, **runtime, identity_positive=positive, identity_negative=denied,
            **pins)

    def _opa_health(self):
        opa = self._owned('deployment', 'trino-opa')
        status, spec = opa.get('status', {}), opa.get('spec', {})
        generation = opa.get('metadata', {}).get('generation')
        _require(type(generation) is int and generation > 0 and
            status.get('observedGeneration') == generation and spec.get('replicas') == 1 and
            status.get('availableReplicas') == 1 and status.get('readyReplicas') == 1 and
            status.get('updatedReplicas') == 1 and status.get('replicas') == 1,
            'FOUNDATION_OPA_NOT_READY')
        _require(any(c.get('name') == 'opa' and c.get('image') == self._config['images']['opa']
            for c in spec.get('template', {}).get('spec', {}).get('containers', [])), 'FOUNDATION_IMAGE_MISMATCH')

    def _trino_health(self):
        info = self._request('trino-local', 'GET', '/v1/info')
        _require(info.get('starting') is False, 'FOUNDATION_COMPUTE_NOT_READY')
        _require(info.get('nodeVersion', {}).get('version') == '483', 'FOUNDATION_IMAGE_MISMATCH')

    def _sql_health(self):
        identity = self._provisioner()
        rows = self._sql('SELECT current_user, version()', identity)
        _require(len(rows) == 1 and type(rows[0]) is list and len(rows[0]) == 2 and
            type(rows[0][0]) is str and rows[0][0] == identity['username'] and
            type(rows[0][1]) is str and rows[0][1] == '483', 'FOUNDATION_SQL_IDENTITY_MISMATCH')
        rows = self._sql("SELECT coordinator, count(*) FROM system.runtime.nodes WHERE state='active' GROUP BY coordinator", identity)
        registered = {}
        for row in rows:
            _require(type(row) is list and len(row) == 2 and type(row[0]) is bool and
                type(row[1]) is int and row[1] > 0 and row[0] not in registered,
                'FOUNDATION_SQL_COMPUTE_MISMATCH')
            registered[row[0]] = row[1]
        expected = {True: 1}
        if self._config['trino']['workers']:
            expected[False] = self._config['trino']['workers']
        _require(registered == expected, 'FOUNDATION_SQL_COMPUTE_MISMATCH')

    def _native_health(self, nodes, *, hashes=None):
        images = {}
        workloads = {self._config['prefix']+'-seaweed': ('seaweedfs', 'seaweedfs'),
            self._config['prefix']+'-postgres': ('postgres', 'postgres'),
            self._config['prefix']+'-polaris': ('polaris', 'polaris'), 'trino-local': ('trino', 'trino')}
        compute = [node['metadata']['uid'] for node in nodes]
        for node in nodes:
            images[node['metadata']['uid']] = node['status']['containerStatuses'][0]['imageID']
        for workload, (container, key) in workloads.items():
            self._owned('deployment', workload)
            pods = self._json(self._cmd(['get', 'pods', '-l', 'app.kubernetes.io/name='+workload, '-o', 'json']))['items']
            _require(bool(pods), 'FOUNDATION_COMPUTE_NOT_READY')
            for active in pods:
                _require(self._pod(active) == workload, 'FOUNDATION_OWNERSHIP_MISMATCH')
                expected = self._config['images'][key]
                statuses = active.get('status', {}).get('containerStatuses', [])
                status = next((s for s in statuses if s.get('name') == container), {})
                _require(status.get('ready') is True and status.get('imageID', '').endswith(expected.split('@')[1]), 'FOUNDATION_IMAGE_MISMATCH')
                _require(any(s.get('name') == container and s.get('image') == expected for s in active['spec']['containers']), 'FOUNDATION_IMAGE_MISMATCH')
                images[active['metadata']['uid']] = status['imageID']
                if workload == 'trino-local' and hashes is not None:
                    # Read each owned active replica, not just the writer.
                    _require(self._exec('read', reader=active) == hashes, 'FOUNDATION_MATERIAL_NOT_READY')
                    _require(active['metadata']['uid'] in compute, 'FOUNDATION_COMPUTE_MISMATCH')
        return compute, images

    def probe(self):
        """Private readiness/incarnation evidence, never credentials/public readiness."""
        try:
            self._gate()
            self._issuer_identity()
            self._opa_health()
            resources = {}
            for name in ('trino-runtime', 'trino-catalogs', self._config['prefix']+'-seaweed-data', self._config['prefix']+'-postgres-data'):
                pvc = self._owned('pvc', name)
                _require(pvc.get('status', {}).get('phase') == 'Bound', 'FOUNDATION_PVC_NOT_READY')
                spec = pvc.get('spec', {})
                _require(spec.get('storageClassName') == 'local-path' and spec.get('volumeMode') == 'Filesystem' and
                    spec.get('accessModes') == ['ReadWriteOnce'] and bool(spec.get('volumeName')), 'FOUNDATION_PVC_MISMATCH')
                pv = self._json(self._cmd(['get', 'pv', spec['volumeName'], '-o', 'json']))
                claim = pv.get('spec', {}).get('claimRef', {})
                pv_spec = pv.get('spec', {})
                terms = pv_spec.get('nodeAffinity', {}).get('required', {}).get('nodeSelectorTerms', [])
                _require(pv.get('status', {}).get('phase') == 'Bound' and bool(pv.get('metadata', {}).get('uid')) and
                    claim.get('uid') == pvc['metadata']['uid'] and claim.get('name') == name and
                    claim.get('namespace') == self._config['kube']['namespace'] and
                    pv_spec.get('storageClassName') == 'local-path' and pv_spec.get('volumeMode') == 'Filesystem' and
                    isinstance(pv_spec.get('local', {}).get('path'), str) and pv_spec['local']['path'].startswith('/') and
                    bool(terms) and all(any(expr.get('key') == 'kubernetes.io/hostname' and expr.get('operator') == 'In' and
                        expr.get('values') == [self._config['kube']['node_name']] for expr in term.get('matchExpressions', []))
                        for term in terms), 'FOUNDATION_PV_MISMATCH')
                resources[name] = pvc['metadata']['uid']
                resources['pv/'+spec['volumeName']] = pv['metadata']['uid']
            code = self._owned('configmap', self._config['prefix']+'-secrets-code')
            jar = base64.b64decode(code['binaryData']['logical-secrets.jar'], validate=True)
            _require(code['data']['sha256'] == hashlib.sha256(jar).hexdigest() == hashlib.sha256(Path(self._config['artifacts']['jar']).read_bytes()).hexdigest(), 'FOUNDATION_SPI_MISMATCH')
            pod = self._publisher()
            self._trino_health()
            nodes = self._compute()
            self._sql_health()
            hashes = self._exec('read')
            for active in nodes:
                _require(self._exec('read', reader=active) == hashes, 'FOUNDATION_MATERIAL_NOT_READY')
            _require(all(hashes.get('target.'+filename) not in (None, 'MISSING') for filename in TARGETS.values()), 'FOUNDATION_MATERIAL_NOT_READY')
            compute, images = self._native_health(nodes, hashes=hashes)
            self._polaris('GET', MANAGEMENT+'/principals/root')
            self._iam('list_roles', MaxItems=1)
            self._storage('head_bucket', Bucket=self._config['storage']['bucket'])
            return {'ready': True, 'revision': self._config['revision'], 'foundation_id': self._config['foundation_id'],
                'readiness': {'opa': True, 'storage': True, 'polaris': True, 'trino': True, 'runtime_files': True, 'spi': True},
                'incarnation': {**self._incarnation, **resources, 'compute': compute, 'node': dict(self._config['kube']['node_uids']), 'images': images,
                    'tunnels': {service: {'endpoint': endpoint, 'service_uid': self._incarnation['service/'+service],
                        'child_pid': getattr(child, 'pid', None)} for (service, endpoint), child in zip(self._tunnels.items(), self._children)}},
                'hashes': hashes, 'spi_sha256': code['data']['sha256']}
        except Exception:
            return {'ready': False, 'revision': self._config['revision'], 'foundation_id': self._config['foundation_id'],
                'readiness': {'opa': False, 'storage': False, 'polaris': False, 'trino': False, 'runtime_files': False, 'spi': False},
                'incarnation': copy.deepcopy(self._incarnation)}
