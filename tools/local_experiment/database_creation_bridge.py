"""Private managed enrollment and fixed owner IPC. No process or listener adoption."""
from dataclasses import fields
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import select
import socket
import stat
import subprocess
import threading
import uuid
import urllib.request

import database_creations as creations
import request_auth
import runtime_profile
from warehouse_operations import OperationError

HELLO = {'version', 'purpose', 'environment_id', 'enrollment_id', 'enrollment_revision',
         'owner_role', 'owner_instance_id', 'runtime_instance_id', 'socket_path',
         'capability', 'private_enrollment_path', 'profile_path'}
RECORD = HELLO - {'purpose', 'private_enrollment_path', 'profile_path'}
ACTIONS = {'DISPATCH_REQUEST', 'GET_REQUEST', 'GET_ACTIVATION', 'ACTIVATE_CANDIDATE', 'OBSERVE_REQUEST'}
INSTANCE = {'version', 'environment_id', 'owner_role', 'owner_instance_id',
            'runtime_instance_id', 'runtime_attestation'}
_managed = None


def exact(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError('Invalid private managed configuration')
    return value


def text(value):
    return type(value) is str and bool(value.strip()) and not any(c in value for c in '\r\n\x00')


def directory(value):
    if not text(value) or not Path(value).is_absolute() or stat.S_IMODE(Path(value).stat().st_mode) != 0o700 or not Path(value).is_dir():
        raise ValueError('Private directory required')
    return Path(value)


def request_id(value):
    if not text(value) or str(uuid.UUID(value)) != value:
        raise ValueError('Canonical request identifier required')
    return value


class Configuration:
    def __init__(self, path):
        self.path = str(runtime_profile.private_file(path))
        self.binding = hashlib.sha256(runtime_profile.private_file(path).read_bytes()).hexdigest()
        value = decode(runtime_profile.private_file(path).read_bytes())
        exact(value, {'version', 'environment_id', 'warehouse_id', 'enrollment_id', 'revision',
                      'executor_id', 'state_dir', 'base_runtime_profile', 'executor_auth_file',
                      'owners', 'origins', 'foundation', 'preparation'})
        if type(value['version']) is not int or value['version'] != 1:
            raise ValueError('Invalid enrollment version')
        self.enrollment = creations.Enrollment(**{f.name: value[f.name] for f in fields(creations.Enrollment)})
        self.value = value
        directory(value['state_dir'])
        self.base = runtime_profile.load(value['base_runtime_profile'])
        runtime_profile.private_file(value['executor_auth_file'])
        if Path(self.base.state_dir).resolve() != Path(value['state_dir']).resolve():
            raise ValueError('Runtime state differs from enrollment')
        exact(value['owners'], {'workbench', 'native'})
        for owner in value['owners'].values():
            exact(owner, {'directory', 'socket_path', 'record_path'})
            parent = directory(owner['directory'])
            for key in ('socket_path', 'record_path'):
                p = Path(owner[key])
                if not p.is_absolute() or p.parent != parent or len(str(p).encode()) > 100:
                    raise ValueError('Explicit short private owner path required')
        from warehouse_operator import Operator
        exact(value['origins'], {'workbench', 'native', 'backend'})
        origins = value['origins']
        for key, origin in origins.items():
            Operator.endpoint(origin, tls=key == 'backend')
        if len(set(origins.values())) != 3 or self.base.sql_endpoint_url != origins['native']:
            raise ValueError('Distinct enrolled origins required')
        f = exact(value['foundation'], {'kube_context', 'api_server', 'ca_sha256', 'node_uids', 'tofu_lineage'})
        import local_gitops
        if (f['kube_context'] != local_gitops.CONTEXT or not text(f['api_server'])
                or not re.fullmatch('[0-9a-f]{64}', f['ca_sha256'])
                or type(f['node_uids']) is not dict or not f['node_uids']
                or not all(text(k) and text(v) for k,v in f['node_uids'].items()) or not text(f['tofu_lineage'])):
            raise ValueError('Explicit foundation identity required')
        self.preparation()  # reject nested drift at enrollment, before READY

    def preparation(self):
        from database_creation_preparation import PreparationConfig
        keys = {f.name for f in fields(PreparationConfig)} - {'enrollment', 'managed_enrollment'}
        p = dict(exact(self.value['preparation'], keys))
        for f in fields(PreparationConfig):
            if f.type is Path:
                p[f.name] = Path(p[f.name])
        endpoints = p['polaris_endpoints']
        if type(endpoints) is not list or any(type(e) is not list or len(e) != 2 for e in endpoints):
            raise ValueError('Explicit service endpoint pairs required')
        p['polaris_endpoints'] = tuple(tuple(e) for e in endpoints)
        ports = p['polaris_port_range']
        if type(ports) is not list:
            raise ValueError('Enrolled transport port range required')
        p['polaris_port_range'] = tuple(ports)
        if str(p['auth_headers']) != self.value['executor_auth_file']:
            raise ValueError('Executor authentication source differs')
        return PreparationConfig(self.enrollment, **p, managed_enrollment=self.path)

    def owner(self, role):
        owner = self.value['owners'][role]
        record = exact(runtime_profile.private_json(owner['record_path']), RECORD)
        expected = dict(version=1, environment_id=self.enrollment.environment_id,
            enrollment_id=self.enrollment.enrollment_id, enrollment_revision=self.enrollment.revision,
            owner_role=role, socket_path=owner['socket_path'])
        if (any(type(record[k]) is not type(v) or record[k] != v for k,v in expected.items())
                or not all(text(record[k]) for k in ('owner_instance_id','runtime_instance_id','capability'))):
            raise ValueError('Owner identity differs')
        return record


def decode(raw):
    def pairs(items):
        result = {}
        for k,v in items:
            if k in result:
                raise ValueError('Duplicate IPC field')
            result[k] = v
        return result
    return json.loads(raw,object_pairs_hook=pairs)


def line(sock):
    data = bytearray()
    while len(data) < 65536:
        part = sock.recv(1)
        if not part:
            raise ValueError('Managed channel closed')
        if part == b'\n':
            return decode(data)
        data.extend(part)
    raise ValueError('Managed message too large')


class Managed:
    def __init__(self, channel, hello, config):
        self.channel, self.hello, self.config = channel, hello, config
        self.alive = True
        self.invalidated = threading.Event()

    def require(self):
        try:
            if not self.alive:
                raise OperationError('OPERATOR_UNAVAILABLE',503)
            # Peek synchronously: a monitor thread need not have observed EOF yet.
            if select.select([self.channel], [], [], 0)[0]:
                if self.channel.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT) == b'':
                    self.alive = False
                    self.invalidated.set()
                raise OperationError('OPERATOR_UNAVAILABLE',503)
            if hashlib.sha256(runtime_profile.private_file(self.config.path).read_bytes()).hexdigest() != self.config.binding:
                raise OperationError('OPERATOR_UNAVAILABLE',503)
            record = self.config.owner(self.hello['owner_role'])
            # Workers span an HTTP generation change; HTTP children do not.
            for key in RECORD - {'runtime_instance_id'}:
                if not hmac.compare_digest(str(record[key]), str(self.hello[key])):
                    raise OperationError('OPERATOR_UNAVAILABLE',503)
            if self.hello['purpose']=='http' and record['runtime_instance_id'] != self.hello['runtime_instance_id']:
                raise OperationError('OPERATOR_UNAVAILABLE',503)
        except Exception:
            raise OperationError('OPERATOR_UNAVAILABLE',503) from None
        return self

    def monitor(self):
        try:
            self.channel.recv(1)
        except OSError:
            pass
        finally:
            self.alive = False
            self.invalidated.set()

    def ipc(self, role, action, rid, activation_id=None):
        self.require()
        return owner_get_or_action(self.config, role, action, rid, activation_id,managed=self)

    def projection_probe(self, enrollment):
        return _probe(self, enrollment, projection=True)

    def instance(self):
        self.require()
        try:
            attestation = runtime_profile.generation_attestation()
        except Exception:
            raise OperationError('OPERATOR_UNAVAILABLE',503) from None
        return dict(version=1,environment_id=self.config.enrollment.environment_id,
            **{k:self.hello[k] for k in ('owner_role','owner_instance_id','runtime_instance_id')},
            runtime_attestation=attestation)

    def probe(self, enrollment):
        return probe(self, enrollment)


def owner_get_or_action(config, role, action, rid, activation_id=None, *, managed=None):
    """Fixed authenticated IPC; local RELEASE uses only the GET wrapper."""
    if action not in ('GET_REQUEST','GET_ACTIVATION'):
        if type(managed) is not Managed or managed.config is not config:
            raise OperationError('OPERATOR_UNAVAILABLE',503)
        managed.require()
    request_id(rid)
    if action not in ACTIONS or (activation_id is not None) != (action in {'GET_ACTIVATION','ACTIVATE_CANDIDATE'}):
        raise ValueError('Fixed owner action required')
    record = config.owner(role)
    owner = config.value['owners'][role]
    info = Path(owner['socket_path']).stat()
    if not stat.S_ISSOCK(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError('Private owner socket required')
    body = dict(version=1,action=action,request_id=rid,owner_role=role,
                owner_instance_id=record['owner_instance_id'],capability=record['capability'])
    if activation_id is not None:
        if not text(activation_id) or re.fullmatch('[0-9a-f]{64}',activation_id) is None:
            raise ValueError('Activation identifier required')
        body['activation_id'] = activation_id
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.settimeout(180 if action == 'ACTIVATE_CANDIDATE' else 10)
        sock.connect(owner['socket_path'])
        sock.sendall(json.dumps(body,separators=(',',':')).encode()+b'\n')
        result = line(sock)
    keys = {'version','action','request_id','state','owner_role','owner_instance_id','runtime_instance_id'}
    optional = {'code'}
    if activation_id is not None:
        keys |= {'activation_id'}
        optional.add('previous_runtime_instance_id')
    if (type(result) is not dict or set(result)-keys-optional or not keys <= set(result)
            or any(type(result[k]) is not type(body[k]) or result[k] != body[k]
                   for k in ('version','action','request_id','owner_role','owner_instance_id'))
            or not text(result['state']) or not text(result['runtime_instance_id'])
            or 'code' in result and not text(result['code'])
            or 'previous_runtime_instance_id' in result and (type(result['previous_runtime_instance_id']) is not str
                or result['previous_runtime_instance_id'] != '' and not text(result['previous_runtime_instance_id']))
            or activation_id is not None and result['activation_id'] != activation_id):
        raise OperationError('OPERATOR_UNAVAILABLE',503)
    return result

def owner_get(config, role, rid, activation_id=None):
    return owner_get_or_action(config,role,'GET_ACTIVATION' if activation_id else 'GET_REQUEST',rid,activation_id)


def probe(self, enrollment):
    return _probe(self, enrollment)


def _projection_identities(config):
    """Only the sealed active full candidate and its proven original parent."""
    import control_store
    from database_creation_worker import accepted_head, validate_candidate_profile
    from database_creation_recovery import previous_attestation
    from warehouse_operator import Operator
    with control_store.connect() as db:
        plan = db.execute("""SELECT p.* FROM db_creation_phase_plans p
            JOIN db_creations c ON c.id=p.creation_id JOIN operations o ON o.id=p.operation_id
            WHERE c.environment_id=? AND c.warehouse_id=? AND c.enrollment_id=? AND c.enrollment_revision=?
            AND c.reservation_active=1 AND p.phase='full' AND p.purpose='APPLY'
            AND p.approval_request_id IS NOT NULL AND o.state IN ('DRAINING','APPLYING','UNKNOWN','AWAITING_APPLY')
            ORDER BY p.sequence DESC LIMIT 1""", (config.enrollment.environment_id,config.enrollment.warehouse_id,
                config.enrollment.enrollment_id,config.enrollment.revision)).fetchone()
        root = dict(creations._root(db,config.enrollment,plan['creation_id'])) if plan else None
    if plan is None:
        attestation, instances = accepted_head(config)
        return {role:[(instances[role] if instances else config.owner(role)['runtime_instance_id'],attestation)]
            for role in ('workbench','native')}
    plan = dict(plan)
    directory = config.preparation().output_root/root['id']/'full'/str(plan['sequence'])
    operator = Operator(directory/'operator.json')
    allowed = {}
    for role in ('workbench','native'):
        record = config.owner(role)
        owner = operator.config['managed_handoff']['owners'][role]
        previous = previous_attestation(config,root,plan,operator,role)
        if record['owner_instance_id'] != owner['owner_instance_id']:
            raise ValueError('Original owner differs')
        if record['runtime_instance_id'] == owner['old_runtime_instance_id']:
            allowed[role] = [(owner['old_runtime_instance_id'],previous)]
        else:
            if record['runtime_instance_id'] != owner['candidate_runtime_instance_id']:
                raise ValueError('Runtime outside sealed handoff')
            # Validate this role's exact sealed activation and immutable profile;
            # the other role may still be on the positively proven parent.
            validate_candidate_profile(config,str(directory/'runtime-profile.json'),hello=dict(
                owner_role=role,owner_instance_id=record['owner_instance_id'],runtime_instance_id=record['runtime_instance_id']))
            allowed[role] = [(owner['candidate_runtime_instance_id'],operator.config['managed_handoff']['runtime_attestation'])]
    return allowed


def _probe(self, enrollment, *, projection=False):
    self.require()
    if enrollment != self.config.enrollment:
        raise ValueError('Enrollment changed')
    p, f = self.config.preparation(), self.config.value['foundation']
    if (os.environ.get('SHIROKUMA_AUTH_MODE') != 'shared'
            or os.environ.get('SHIROKUMA_AUTH_FILE') != self.config.base.auth_file):
        raise ValueError('Current authentication source differs')
    accounts = request_auth._principal_accounts()
    if not creations._admin_account(accounts.get(enrollment.executor_id)):
        raise ValueError('Executor authority unavailable')
    import control_store
    with control_store.connect() as db:
        for row in db.execute("SELECT principal_id,maximum_role FROM grants WHERE maximum_role!='revoked'"):
            account = accounts.get(row['principal_id'])
            if (not account or account.get('disabled',False) or not account.get('enabled',True)
                    or request_auth.ROLES[row['maximum_role']] > request_auth.ROLES[account['role']]):
                raise ValueError('Current grant ceiling differs')
    def kubectl(*args):
        result = subprocess.run(['rtk','proxy','kubectl','--kubeconfig',str(p.kubeconfig),
            '--context', f['kube_context'],'--request-timeout=10s',*args],capture_output=True,text=True,timeout=15)
        if result.returncode:
            raise ValueError('Foundation unavailable')
        return json.loads(result.stdout)
    view = kubectl('config','view','--minify','--raw','-o','json')
    cluster = view['clusters'][0]['cluster']
    import base64
    ca = base64.b64decode(cluster['certificate-authority-data'],validate=True)
    if (view['current-context'] != f['kube_context'] or cluster['server'] != f['api_server']
            or hashlib.sha256(ca).hexdigest() != f['ca_sha256'] or cluster.get('insecure-skip-tls-verify')):
        raise ValueError('Foundation identity differs')
    nodes = kubectl('get','nodes','-o','json')
    if {n['metadata']['name']:n['metadata']['uid'] for n in nodes['items']} != f['node_uids']:
        raise ValueError('Foundation nodes differ')
    if runtime_profile.private_json(p.tofu_state)['lineage'] != f['tofu_lineage']:
        raise ValueError('Foundation lineage differs')
    from warehouse_operator import Operator
    headers = runtime_profile.private_json(self.config.value['executor_auth_file'])
    principal = request_auth.authenticate_principal(headers)
    import database_creation_auth as auth
    auth._executor(enrollment,principal)
    live_attestation = None
    from database_creation_worker import accepted_head
    if projection:
        allowed = _projection_identities(self.config)
    else:
        expected_attestation, expected_instances = accepted_head(self.config)
    for role in ('workbench','native'):
        record = self.config.owner(role)
        response = self.ipc(role,'GET_REQUEST',str(uuid.UUID(int=0)))
        if response['state'] not in ('NOT_FOUND','ACK','COMPLETE','READY') or response['runtime_instance_id'] != record['runtime_instance_id']:
            raise ValueError('Current owner child differs')
        origin = self.config.value['origins'][role]
        instance = Operator.http(self,urllib.request.Request(origin+creations.COLLECTION+'/runtime-instance',headers=headers))
        exact(instance,INSTANCE)
        if (instance != dict(version=1,environment_id=enrollment.environment_id,owner_role=role,
                owner_instance_id=record['owner_instance_id'],runtime_instance_id=record['runtime_instance_id'],
                runtime_attestation=instance['runtime_attestation']) or
                instance['runtime_attestation'] != Operator.http(self,urllib.request.Request(origin+'/api/v1/runtime',headers=headers))):
            raise ValueError('Actual runtime owner proof differs')
        if projection:
            if (record['runtime_instance_id'],instance['runtime_attestation']) not in allowed[role]:
                raise ValueError('Runtime outside sealed handoff')
            continue
        if live_attestation is not None and instance['runtime_attestation'] != live_attestation:
            raise ValueError('Normal-origin runtime generations differ')
        if (instance['runtime_attestation'] != expected_attestation or
                expected_instances is not None and record['runtime_instance_id'] != expected_instances[role]):
            raise ValueError('Normal-origin runtime differs from accepted head')
        live_attestation = instance['runtime_attestation']
    return creations.FoundationReadiness(True,True)


def configure(purpose='http'):
    """Only a real inherited duplex FD and matching private owner confer identity."""
    global _managed
    _managed = None
    raw = os.environ.get('SHIROKUMA_MANAGED_FD')
    if raw is None:
        return None
    channel = None
    try:
        if raw != '3':
            raise ValueError('Inherited managed FD required')
        channel = socket.socket(fileno=os.dup(3))
        if channel.family != socket.AF_UNIX or channel.type != socket.SOCK_STREAM:
            raise ValueError('Duplex private channel required')
        channel.settimeout(10)
        hello = exact(line(channel),HELLO)
        if (hello['purpose'] != purpose or hello['owner_role'] not in ('workbench','native')
                or not all(text(hello[k]) for k in HELLO-{'version','enrollment_revision'})):
            raise ValueError('Managed identity required')
        config = Configuration(hello['private_enrollment_path'])
        record = config.owner(hello['owner_role'])
        if any(type(hello[k]) is not type(record[k]) or hello[k] != record[k] for k in RECORD):
            raise ValueError('Managed identity differs')
        if hello['profile_path'] != os.environ.get('SHIROKUMA_RUNTIME_PROFILE'):
            raise ValueError('Managed startup profile differs')
        if purpose == 'http' and hello['profile_path'] != config.value['base_runtime_profile']:
            from database_creation_worker import validate_candidate_profile
            validate_candidate_profile(config,hello['profile_path'],hello=hello)
        if (runtime_profile.current() is None or runtime_profile.current().auth_file != config.base.auth_file
                or runtime_profile.current().state_dir != config.value['state_dir']):
            raise ValueError('Current authentication or state differs')
        managed = Managed(channel,hello,config)
        channel.sendall(json.dumps(dict(version=1,state='READY',
            **{k:hello[k] for k in ('owner_role','owner_instance_id','runtime_instance_id')},
            runtime_attestation=runtime_profile.generation_attestation() if purpose=='http' else None)).encode()+b'\n')
        channel.settimeout(None)
        _managed = managed
        creations.install_foundation(config.enrollment,managed)
        threading.Thread(target=managed.monitor,daemon=True).start()
        return managed
    except Exception:
        if channel:
            channel.close()
        raise OperationError('OPERATOR_UNAVAILABLE',503) from None


def current():
    if _managed is None:
        raise OperationError('OPERATOR_UNAVAILABLE',503)
    return _managed.require()


def runtime_instance():
    return current().instance()


def dispatch(rid, *, observe=False):
    managed = current()
    return managed.ipc(managed.hello['owner_role'],'OBSERVE_REQUEST' if observe else 'DISPATCH_REQUEST',rid)
