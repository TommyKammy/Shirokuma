"""Database-scoped creation admission and bounded, receipt-first dispatch.

Imports are inert. Only private server composition opens a provider. SQLite is
the authority; files retain irreversible responses before the next effect.
"""
from contextlib import contextmanager
from collections import OrderedDict
from datetime import date, datetime
import base64
import binascii
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import threading
import uuid

import catalog_admin
import control_store
import database_creations as legacy
import display_management
import grant_management
import logical_registry as registry
import request_auth
import runtime_profile
import warehouse_admission as admission
from warehouse_operations import OperationError, _json

COLLECTION = legacy.COLLECTION
_provider = None
_config = None
_executor_id = None
_verifier_cache = None
_owner_id = str(uuid.uuid4())
_HASH_POLICY = ('sha1', 100000, 16, 32)


def _password_record(username, password, policy):
    algorithm, iterations, salt_size, digest_size = policy
    salt = secrets.token_bytes(salt_size)
    digest = hashlib.pbkdf2_hmac(algorithm, password.encode(), salt, iterations, dklen=digest_size)
    return username+':'+str(iterations)+':'+salt.hex()+':'+digest.hex()


class _VerifierCache:
    """Bounded derived records only; never a credential or authority snapshot."""
    def __init__(self, foundation_id, revision, limit=256):
        self._foundation = (foundation_id, revision)
        self._limit = limit
        self._key = secrets.token_bytes(32)
        self._records = OrderedDict()
        self._lock = threading.Lock()
        self._closed = False

    def record(self, ref, username, password):
        policy = _HASH_POLICY
        with self._lock:
            key = None if self._closed else (*self._foundation, ref, username, policy,
                hmac.digest(self._key, password.encode(), 'sha256'))
            if key in self._records:
                self._records.move_to_end(key)
                return self._records[key]
        record = _password_record(username, password, policy)
        with self._lock:
            if not self._closed:
                self._records[key] = record
                self._records.move_to_end(key)
                while len(self._records) > self._limit:
                    self._records.popitem(last=False)
        return record

    def close(self):
        with self._lock:
            self._closed = True
            self._records.clear()
            self._key = b''


def enabled():
    profile = runtime_profile.current()
    return profile is not None and profile.mode == 'logical_v1'


def config():
    if _config is None:
        raise OperationError('FOUNDATION_NOT_READY', 503)
    return _config


@contextmanager
def lifetime(*, register=False, refresh_interval=2):
    """One provider/tunnel owner per normal HTTP process, never per database."""
    import logical_foundation
    from logical_provider import Provider
    global _provider, _config, _executor_id, _verifier_cache
    value = runtime_profile.logical_config()
    foundation = logical_foundation.load(value['provider_config_file'])
    if any(foundation[k] != value[v] for k, v in (
            ('environment_id', 'environment_id'), ('warehouse_id', 'warehouse_id'),
            ('foundation_id', 'foundation_generation_id'), ('revision', 'foundation_revision'))):
        raise ValueError('Logical foundation differs')
    executor = value['executor_id']
    previous = _provider, _config, _executor_id, _verifier_cache
    with Provider(foundation) as provider:
        _provider, _config, _executor_id = provider, value, executor
        cache = _VerifierCache(foundation['foundation_id'], foundation['revision'])
        _verifier_cache = cache
        stop = threading.Event()
        task = None
        try:
            if register:
                register_foundation()
            with control_store.connect() as db:
                registered = dict(_foundation(db))
            # Close the metadata connection before the full native UID binding.
            try:
                saved = runtime_profile.private_json(registered['verification_ref'])
                if (type(saved) is not dict or saved.get('kind') != 'foundation_probe'
                        or type(saved.get('generation_id')) is not str
                        or saved['generation_id'] != value['foundation_generation_id']
                        or type(saved.get('revision')) is not int
                        or saved['revision'] != value['foundation_revision']):
                    raise ValueError()
                _validate_probe(saved.get('result'))
                provider.bind_registered(saved['result'])
            except Exception:
                raise OperationError('FOUNDATION_NOT_READY', 503) from None
            with transaction() as db:
                _gate(db, executor)
            # The runtime profile seals provider/config bytes; authority stays fresh.
            baseline = request_auth._principal_accounts()
            task = threading.Thread(target=_account_refresh_loop,
                args=(stop, baseline, refresh_interval), name='logical-authority-refresh', daemon=True)
            task.start()
            yield provider
        finally:
            stop.set()
            if task is not None:
                task.join()
            cache.close()
            _provider, _config, _executor_id, _verifier_cache = previous


@contextmanager
def transaction():
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        yield db


def _id():
    return uuid.uuid4().hex


def _private_dir():
    directory = Path(config()['credentials_dir']) / 'creations'
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.stat().st_mode & 0o077:
        raise ValueError('Private receipt directory required')
    return directory


def _json_value(value):
    """SDK dates are explicit ISO values; arbitrary objects fail closed."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {'_private_bytes_v1': base64.b64encode(value).decode('ascii')}
    if isinstance(value, dict):
        return {k: _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if value is None or type(value) in (str, bool, int, float):
        return value
    raise ValueError('Unsupported private receipt value')


def _restore_json(value):
    if isinstance(value, dict):
        if set(value) == {'_private_bytes_v1'}:
            return base64.b64decode(value['_private_bytes_v1'], validate=True)
        return {k: _restore_json(v) for k,v in value.items()}
    if isinstance(value, list):
        return [_restore_json(v) for v in value]
    return value


def _receipt(value):
    """Exclusive immutable 0600 receipt; callers never publish its contents."""
    path = _private_dir() / (_id() + '.json')
    with open(path, 'x', opener=lambda p, f: os.open(p, f, 0o600)) as stream:
        stream.write(_json(_json_value(value)))
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return str(path)


@contextmanager
def _lock(root_id, *, blocking=False):
    if not re.fullmatch('[a-f0-9-]{1,64}', root_id):
        raise ValueError('Invalid internal root')
    with open(_private_dir() / (root_id + '.lock'), 'a', opener=lambda p, f: os.open(p, f, 0o600)) as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _gate(db, actor, *, read_only=False):
    accounts = request_auth._principal_accounts()
    if not legacy._admin_account(accounts.get(actor)):
        raise OperationError('PRINCIPAL_STOPPED', 403)
    c = config()
    if not read_only:
        db.execute("""INSERT INTO db_creation_principal_gates
            VALUES (?,?,'ACTIVE',1,NULL,NULL,?) ON CONFLICT DO NOTHING""",
            (c['environment_id'], actor, admission._now()))
    row = db.execute('SELECT * FROM db_creation_principal_gates WHERE environment_id=? AND principal_id=?',
                     (c['environment_id'], actor)).fetchone()
    if row is None and read_only:
        return 1
    if row['state'] != 'ACTIVE':
        raise OperationError('PRINCIPAL_STOPPED', 403)
    return row['revision']


def _foundation(db):
    c = config()
    row = registry.get_foundation(db, c['environment_id'], c['warehouse_id'])
    if (not row or row['status'] != 'READY' or row['revision'] != c['foundation_revision']
            or row['generation_id'] != c['foundation_generation_id']
            or row['config_ref'] != c['provider_config_file'] or not row['verification_ref']):
        raise OperationError('FOUNDATION_NOT_READY', 503)
    generation = db.execute('SELECT state FROM warehouse_config_generations WHERE id=?',
                            (row['generation_id'],)).fetchone()
    if not generation or generation[0] != 'APPLIED':
        raise OperationError('FOUNDATION_NOT_READY', 503)
    control = db.execute('SELECT * FROM warehouse_controls WHERE warehouse_id=?',
                         (c['warehouse_id'],)).fetchone()
    if (not control or control['state'] != 'OPEN' or control['operation_id'] is not None
            or control['applied_generation_id'] != row['generation_id']):
        raise OperationError('WAREHOUSE_START_REQUIRED', 409)
    return row


def _validate_hashes(hashes):
    names = {'target.'+name for name in
             ('credentials.properties', 'password.db', 'rules.json', 'guard.rego')}
    if (type(hashes) is not dict or set(hashes) != names
            or any(type(v) is not str or not re.fullmatch('[0-9a-f]{64}', v)
                   for v in hashes.values())):
        raise ValueError('Invalid publication hashes')
    return {name.removeprefix('target.'): value for name, value in hashes.items()}


def _validate_probe(result):
    if (type(result) is not dict or result.get('ready') is not True
            or type(result.get('incarnation')) is not dict or not result['incarnation']
            or type(result.get('readiness')) is not dict
            or any(result['readiness'].get(k) is not True for k in
                   ('storage', 'polaris', 'trino', 'runtime_files', 'spi'))
            or type(result.get('foundation_id')) is not str
            or result['foundation_id'] != config()['foundation_generation_id']
            or type(result.get('revision')) is not int
            or result['revision'] != config()['foundation_revision']):
        raise ValueError('Invalid foundation proof')
    hashes = result.get('hashes')
    if type(hashes) is not dict:
        raise ValueError('Invalid foundation hashes')
    _validate_hashes({k:v for k,v in hashes.items() if k.startswith('target.')})
    return result


def _probe():
    """Full diagnostic/registration only; never part of request admission."""
    _bound_profile()
    try:
        return _validate_probe(_provider.probe())
    except Exception:
        raise OperationError('FOUNDATION_NOT_READY', 503) from None


def _bound_profile(*, alive=False):
    try:
        if runtime_profile.current() is not None:
            runtime_profile.generation_attestation()
        if _provider is None:
            raise OperationError('OPERATOR_UNAVAILABLE', 503)
        if alive and _provider.bound_alive() is not True:
            raise ValueError()
    except OperationError:
        raise
    except Exception:
        raise OperationError('FOUNDATION_NOT_READY', 503) from None


def _live_health():
    from logical_provider import Rejected
    _bound_profile()
    with control_store.connect() as db:
        _foundation(db)
    try:
        result = _provider.live_health()
    except Rejected as error:
        if type(error.code) is str and error.code == 'WAREHOUSE_START_REQUIRED':
            raise OperationError('WAREHOUSE_START_REQUIRED', 409) from None
        raise OperationError('FOUNDATION_NOT_READY', 503) from None
    except Exception:
        raise OperationError('FOUNDATION_NOT_READY', 503) from None
    try:
        if (type(result) is not dict or result.get('ready') is not True
                or type(result.get('foundation_id')) is not str
                or result['foundation_id'] != config()['foundation_generation_id']
                or type(result.get('revision')) is not int
                or result['revision'] != config()['foundation_revision']
                or _provider.bound_alive() is not True):
            raise ValueError()
    except Exception:
        raise OperationError('FOUNDATION_NOT_READY', 503) from None


def register_foundation():
    """Explicit private post-apply registration; never called by CREATE or GET.

    A real positive probe and its immutable receipt establish WCG provenance.
    No legacy apply operation or saved plan is invented.
    """
    observed = _probe()
    c = config()
    proof = _receipt(dict(kind='foundation_probe', generation_id=c['foundation_generation_id'],
                          revision=c['foundation_revision'], result=observed))
    with transaction() as db:
        old = registry.get_foundation(db, c['environment_id'], c['warehouse_id'])
        if old:
            if old['generation_id'] == c['foundation_generation_id'] and old['revision'] == c['foundation_revision']:
                _foundation(db)
                if not old['verification_ref'] or old['config_ref'] != c['provider_config_file']:
                    raise OperationError('FOUNDATION_NOT_READY')
                return old
            raise OperationError('FOUNDATION_NOT_READY')
        control = db.execute('SELECT * FROM warehouse_controls WHERE warehouse_id=?',
                             (c['warehouse_id'],)).fetchone()
        # Only schema metadata and pristine initial control rows are reusable.
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        occupied = any(db.execute('SELECT 1 FROM "'+table+'" LIMIT 1').fetchone()
                       for table in tables if table != 'warehouse_controls')
        dirty_control = db.execute("SELECT 1 FROM warehouse_controls WHERE state!='OPEN' OR operation_id IS NOT NULL OR applied_generation_id IS NOT NULL LIMIT 1").fetchone()
        if (occupied or dirty_control
                or db.execute('SELECT 1 FROM databases LIMIT 1').fetchone()
                or db.execute('SELECT 1 FROM warehouse_config_generations WHERE warehouse_id=?',
                              (c['warehouse_id'],)).fetchone()
                or db.execute('SELECT 1 FROM warehouse_admissions WHERE warehouse_id=?',
                              (c['warehouse_id'],)).fetchone()):
            raise OperationError('FOUNDATION_NOT_READY', 409)
        db.execute('''INSERT INTO warehouse_config_generations
            (id,warehouse_id,apply_id,database_revisions,grant_revisions,configuration_refs,state)
            VALUES (?,?,?,'[]','[]',?,'APPLIED')''',
            (c['foundation_generation_id'], c['warehouse_id'], 'foundation:'+c['foundation_generation_id'],
             _json([c['provider_config_file'], proof])))
        admission._ensure(db, c['warehouse_id'])
        db.execute('''UPDATE warehouse_controls SET state='OPEN',operation_id=NULL,applied_generation_id=?
            WHERE warehouse_id=?''', (c['foundation_generation_id'], c['warehouse_id']))
        return registry.put_foundation(db, dict(environment_id=c['environment_id'], warehouse_id=c['warehouse_id'],
            generation_id=c['foundation_generation_id'], revision=c['foundation_revision'], status='READY',
            config_ref=c['provider_config_file'], verification_ref=proof))


def _root(db, root_id, actor=None):
    row = db.execute('SELECT * FROM db_creations WHERE id=? AND environment_id=?',
                     (root_id, config()['environment_id'])).fetchone()
    if row is None:
        raise OperationError('CREATION_NOT_FOUND', 404)
    if row['creation_mode'] != 'logical_v1':
        raise OperationError('CREATION_MODE_MISMATCH', 409)
    if actor is not None and row['actor'] != actor:
        raise OperationError('CREATION_OWNER_REQUIRED', 403)
    return dict(row)


def _saved(db, actor, route, key, payload):
    for table in ('logical_requests', 'db_creation_requests'):
        row = db.execute(f'''SELECT payload,ack FROM {table}
            WHERE environment_id=? AND actor=? AND route=? AND idempotency_key=?''',
            (config()['environment_id'], actor, route, key)).fetchone()
        if row:
            if row['payload'] != _json(payload):
                raise OperationError('IDEMPOTENCY_CONFLICT')
            return json.loads(row['ack'])


def _pins(db, root, *, ready=True):
    foundation = (_foundation(db) if ready else registry.get_foundation(
        db, config()['environment_id'], config()['warehouse_id']))
    binding = registry.get_binding(db, root['database_id'])
    catalog = db.execute('SELECT desired_revision,revision FROM catalog_bindings WHERE database_id=?',
                         (root['database_id'],)).fetchone()
    grants = [dict(r) for r in db.execute('''SELECT g.principal_id,g.maximum_role,g.revision,e.epoch
        FROM grants g JOIN effective_grants e USING(principal_id,database_id)
        WHERE g.database_id=? ORDER BY g.principal_id''', (root['database_id'],))]
    return dict(generation_id=foundation['generation_id'], foundation_revision=foundation['revision'],
        binding_revision=binding['revision'], policy_revision=binding['desired_policy_revision'],
        catalog_revision=catalog['revision'], desired_revision=catalog['desired_revision'], grants=grants,
        accounts={p:_account_authority(a) for p,a in request_auth._principal_accounts().items()
                  if legacy._admin_account(a) or p in {g['principal_id'] for g in grants}})


def _request(db, root, actor, route, key, payload, action):
    request_id, now = _id(), admission._now()
    ack = dict(request_id=request_id, creation_id=root['id'], action=action,
               phase=None, accepted_revision=root['revision'])
    requester_gate = _gate(db, actor)
    if action in ('WITHDRAW', 'RECONCILE'):
        gate = db.execute('SELECT revision FROM db_creation_principal_gates WHERE environment_id=? AND principal_id=?',
                          (config()['environment_id'], _executor_id)).fetchone()
        executor_gate = gate[0] if gate else None
    else:
        executor_gate = _gate(db, _executor_id)
    db.execute('''INSERT INTO logical_requests
        (id,creation_id,environment_id,actor,route,idempotency_key,payload,ack,action,dispatch_state,
         executor_id,requester_gate_revision,executor_gate_revision,pins_json,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,'ACCEPTED',?,?,?,?,?,?)''',
        (request_id, root['id'], config()['environment_id'], actor, route, key, _json(payload), _json(ack),
         action, _executor_id, requester_gate, executor_gate, _json(_pins(db, root, ready=action not in ('WITHDRAW','RECONCILE'))), now, now))
    return ack


def create(principal, role, payload, key):
    legacy.current_admin(principal, role)
    legacy.validate_key(key)
    intent = legacy._intent(payload)
    c = config()
    with transaction() as db:
        saved = _saved(db, principal.principal_id, COLLECTION, key, intent)
        if saved:
            return saved, False
        _gate(db, principal.principal_id, read_only=True)
        _gate(db, _executor_id, read_only=True)
    _live_health()  # No provider I/O inside a SQLite transaction.
    provider_config = _provider.config
    with transaction() as db:
        saved = _saved(db, principal.principal_id, COLLECTION, key, intent)
        if saved:
            return saved, False
        _gate(db, principal.principal_id)
        _gate(db, _executor_id)
        _foundation(db)
        legacy._validate_current_grants(intent)
        root_id, now = _id(), admission._now()
        registered = catalog_admin._create_database(db, principal.principal_id, intent['sql_catalog'],
            root_id+'/CREATE/1', _json(dict(sql_catalog=intent['sql_catalog'], creation_mode='logical_v1')))
        dbid = registered['database_id']
        metadata = display_management._mutate(principal, role, 'database', dbid,
            dict(expected_revision=1, display_name=intent['display_name'], description=intent['description']),
            root_id+'/METADATA/1', connection=db)
        grants = {}
        for grant in intent['initial_grants']:
            result = grant_management._mutate(principal, role, dbid, dict(grant, expected_revision=0),
                root_id+'/GRANT/'+grant['principal_id']+'/1', connection=db)
            grants[grant['principal_id']] = result['id']
        db.execute('''INSERT INTO db_creations
            (id,environment_id,warehouse_id,actor,enrollment_id,enrollment_revision,database_id,
             create_operation_id,intent_json,child_ids_json,revision,reservation_active,created_at,updated_at,
             creation_mode,logical_state) VALUES (?,?,?,?,?,?,?,?,?,?,1,0,?,?,'logical_v1','CREATING')''',
            (root_id, c['environment_id'], c['warehouse_id'], principal.principal_id,
             c['foundation_generation_id'], c['foundation_revision'], dbid, registered['operation_id'],
             _json(intent), _json(dict(metadata=metadata['operation_id'], grants=grants)), now, now))
        credential_ref = 'catalog_'+_id()
        db.execute('''UPDATE catalog_bindings SET polaris_service_ref=?,polaris_realm=?,polaris_catalog=?,
            storage_profile_ref=?,credential_profile_ref=?,base_location=? WHERE database_id=?''',
            (provider_config['prefix']+'-polaris', provider_config['polaris']['realm'], intent['sql_catalog'],
             provider_config['prefix']+'-seaweed', credential_ref,
             's3://'+provider_config['storage']['bucket']+'/'+dbid+'/', dbid))
        registry.put_binding(db, dict(database_id=dbid, environment_id=c['environment_id'], warehouse_id=c['warehouse_id'],
            foundation_revision=c['foundation_revision'], desired_policy_revision=1, applied_policy_revision=0,
            catalog_credential_ref=credential_ref, verification_ref='', revision=1))
        return _request(db, _root(db, root_id), principal.principal_id, COLLECTION, key, intent, 'CREATE'), True


def mutate(principal, role, root_id, action, payload, key):
    legacy.current_admin(principal, role)
    legacy.validate_key(key)
    if action not in ('WITHDRAW', 'RECONCILE', 'RESUME'):
        raise OperationError('CREATION_MODE_MISMATCH')
    if (type(payload) is not dict or set(payload) != {'expected_revision'}
            or type(payload['expected_revision']) is not int or payload['expected_revision'] < 1):
        raise OperationError('INVALID_REQUEST', 400)
    route = COLLECTION+'/'+root_id+'/'+action.lower()
    if action == 'RESUME':
        with control_store.connect() as db:
            root = _root(db, root_id, principal.principal_id)
            saved = _saved(db, principal.principal_id, route, key, payload)
            if saved:
                return saved, False
            _gate(db, principal.principal_id, read_only=True)
            _gate(db, _executor_id, read_only=True)
            if root['revision'] != payload['expected_revision']:
                raise OperationError('REVISION_CONFLICT')
            if root['logical_state'] != 'BLOCKED' or db.execute("""SELECT 1 FROM logical_effects
                WHERE creation_id=? AND state IN ('INTENT','UNKNOWN')""", (root_id,)).fetchone():
                raise OperationError('RECONCILIATION_REQUIRED')
        _live_health()
    with transaction() as db:
        root = _root(db, root_id, principal.principal_id)
        saved = _saved(db, principal.principal_id, route, key, payload)
        if saved:
            return saved, False
        _gate(db, principal.principal_id)
        if root['revision'] != payload['expected_revision']:
            raise OperationError('REVISION_CONFLICT')
        if action == 'WITHDRAW':
            if root['logical_state'] in ('STOPPED', 'READY'):
                raise OperationError('INVALID_CREATION_STATE')
            db.execute("UPDATE db_creations SET logical_state='STOPPED',terminal_outcome='STOPPED',revision=revision+1 WHERE id=?", (root_id,))
        elif action == 'RESUME':
            if root['logical_state'] != 'BLOCKED' or db.execute("""SELECT 1 FROM logical_effects
                WHERE creation_id=? AND state IN ('INTENT','UNKNOWN')""", (root_id,)).fetchone():
                raise OperationError('RECONCILIATION_REQUIRED')
            db.execute("UPDATE db_creations SET logical_state='CREATING',revision=revision+1 WHERE id=?", (root_id,))
        elif root['logical_state'] == 'READY':
            raise OperationError('INVALID_CREATION_STATE')
        else:
            db.execute('UPDATE db_creations SET revision=revision+1 WHERE id=?', (root_id,))
        ack = _request(db, _root(db, root_id), principal.principal_id, route, key, payload, action)
        if action == 'WITHDRAW':
            db.execute("UPDATE logical_requests SET dispatch_state='COMPLETE' WHERE id=?", (ack['request_id'],))
        return ack, True


def _assert_claim(db, request, *, allow_stopped=False):
    if runtime_profile.current() is not None:
        runtime_profile.generation_attestation()
    root = _root(db, request['creation_id'])
    live = db.execute('SELECT * FROM logical_requests WHERE id=?', (request['id'],)).fetchone()
    if (not live or live['claim_id'] != request['claim_id'] or live['owner_id'] != _owner_id
            or live['dispatch_state'] != 'CLAIMED'):
        raise OperationError('RECONCILIATION_REQUIRED')
    if allow_stopped:
        return root
    if root['logical_state'] == 'STOPPED':
        raise OperationError('PRINCIPAL_STOPPED')
    if (request['executor_id'] != _executor_id or request['executor_id'] != config()['executor_id']
            or _gate(db, request['actor']) != request['requester_gate_revision']
            or _gate(db, request['executor_id']) != request['executor_gate_revision']):
        raise OperationError('PRINCIPAL_STOPPED')
    if request['action'] == 'AUTH_REFRESH':
        _assert_refresh(db, request, root)
        return root
    if _pins(db, root) != json.loads(request['pins_json']):
        raise OperationError('RECONCILIATION_REQUIRED')
    legacy._validate_current_grants(json.loads(root['intent_json']))
    return root


def _effect(request, key, intent, function, *, leaf=False):
    with transaction() as db:
        _assert_claim(db, request)
        prior = db.execute("SELECT * FROM logical_effects WHERE creation_id=? AND (effect_key=? OR (effect_key GLOB ? AND effect_key NOT GLOB ?)) ORDER BY rowid DESC LIMIT 1",
                           (request['creation_id'], key, key+'/attempt/*', key+'/attempt/*/*')).fetchone()
        if prior and prior['state'] == 'REJECTED' and request['action'] == 'RESUME':
            key += '/attempt/' + request['id']
            prior = db.execute('SELECT * FROM logical_effects WHERE creation_id=? AND effect_key=?',
                               (request['creation_id'], key)).fetchone()
        if prior:
            if prior['intent_json'] != _json(intent) or prior['state'] not in ('ACKNOWLEDGED','OBSERVED_APPLIED') or not prior['private_ref']:
                raise OperationError('RECONCILIATION_REQUIRED')
            return _restore_json(runtime_profile.private_json(prior['private_ref'])['result'])
        now = admission._now()
        db.execute("""INSERT INTO logical_effects
            (creation_id,effect_key,intent_json,state,created_at,updated_at) VALUES (?,?,?,'INTENT',?,?)""",
            (request['creation_id'], key, _json(intent), now, now))
    try:
        if leaf:
            result = function()
            if (isinstance(result, (tuple,list)) and len(result) == 2 and type(result[0]) is int
                    and result[0] in (400,401,403,404,405,409,412,422)):
                from logical_provider import Rejected
                _receipt(dict(rejected=True, result=result))
                raise Rejected('PROVIDER_HTTP_'+str(result[0]))
        else:
            def callback(subkey, safe_identity, call):
                return _effect(request, key.split('/attempt/')[0]+'/leaf/'+subkey, safe_identity, call, leaf=True)
            with _provider.effects(callback):
                result = function()
        # Even a late STOP response is saved; it never confers effective authority.
        receipt = _receipt(dict(result=result))
    except Exception as error:
        from logical_provider import Rejected
        status = getattr(error, 'response', {}).get('ResponseMetadata', {}).get('HTTPStatusCode')
        known = isinstance(error, Rejected) or status in (400,401,403,404,409,412,422) or (isinstance(error, OperationError) and error.code == 'CREATION_FAILED')
        if not leaf:
            with control_store.connect() as db:
                children = db.execute("SELECT effect_key,state FROM logical_effects WHERE creation_id=? AND effect_key GLOB ? ORDER BY rowid",
                                      (request['creation_id'], key.split('/attempt/')[0]+'/leaf/*')).fetchall()
            children = {r['effect_key'].split('/attempt/')[0]: r['state'] for r in children}
            if any(state in ('INTENT','UNKNOWN') for state in children.values()):
                known = False
            elif 'REJECTED' in children.values():
                known = True
        if isinstance(error, OperationError) and error.code == 'RECONCILIATION_REQUIRED':
            known = False
        rejection = _receipt(dict(rejected=True, code=getattr(error, 'code', 'PROVIDER_REJECTED'))) if known else None
        with transaction() as db:
            db.execute("UPDATE logical_effects SET state=?,private_ref=?,updated_at=? WHERE creation_id=? AND effect_key=? AND state='INTENT'",
                       ('REJECTED' if known else 'UNKNOWN', rejection, admission._now(), request['creation_id'], key))
        raise OperationError('CREATION_FAILED' if known else 'RECONCILIATION_REQUIRED') from None
    with transaction() as db:
        db.execute("""UPDATE logical_effects SET state='ACKNOWLEDGED',private_ref=?,outcome_json='{"acknowledged":true}',updated_at=?
            WHERE creation_id=? AND effect_key=? AND state='INTENT'""",
            (receipt, admission._now(), request['creation_id'], key))
    return result


def dispatch_async(request_id):
    thread = threading.Thread(target=dispatch, args=(request_id,), name='logical-create', daemon=True)
    thread.start()
    return thread


def dispatch(request_id):
    """Single bounded pass. No polling, TTL reclaim, mutation retry, or reissue."""
    with control_store.connect() as db:
        row = db.execute('SELECT * FROM logical_requests WHERE id=?', (request_id,)).fetchone()
        if not row or row['action'] not in ('CREATE', 'RESUME', 'RECONCILE', 'AUTH_REFRESH'):
            return
        root_id = row['creation_id']
    with _lock(root_id) as owned:
        if not owned:
            return
        with transaction() as db:
            # An abandoned dispatched request is fenced only by the free root lock.
            db.execute("""UPDATE logical_requests SET dispatch_state='UNKNOWN'
                WHERE creation_id=? AND dispatch_state='CLAIMED'""", (root_id,))
            changed = db.execute("""UPDATE logical_requests SET dispatch_state='CLAIMED',owner_id=?,claim_id=?,updated_at=?
                WHERE id=? AND dispatch_state='ACCEPTED'""", (_owner_id, _id(), admission._now(), request_id))
            if changed.rowcount != 1:
                return
            request = dict(db.execute('SELECT * FROM logical_requests WHERE id=?', (request_id,)).fetchone())
        try:
            if request['action'] == 'RECONCILE':
                _observe(request)
            elif request['action'] == 'AUTH_REFRESH':
                with transaction() as db:
                    request['authority'] = _account_authority(request_auth._principal_accounts().get(json.loads(request['pins_json'])['target_principal_id']))
                    _assert_claim(db, request)
                _run_refresh(request)
            else:
                _run(request)
        except Exception as error:
            with transaction() as db:
                root = _root(db, root_id)
                unknown = db.execute("SELECT 1 FROM logical_effects WHERE creation_id=? AND state IN ('INTENT','UNKNOWN')", (root_id,)).fetchone()
                state = 'UNKNOWN' if unknown else 'BLOCKED'
                if isinstance(error, OperationError) and error.code == 'PRINCIPAL_STOPPED':
                    state = 'STOPPED'
                if request['action'] != 'AUTH_REFRESH' and root['logical_state'] != 'STOPPED':
                    db.execute('UPDATE db_creations SET logical_state=?,revision=revision+1 WHERE id=?', (state, root_id))
                db.execute("""UPDATE logical_requests SET dispatch_state=?,updated_at=?
                    WHERE id=? AND claim_id=? AND dispatch_state='CLAIMED'""",
                    ('UNKNOWN' if unknown else 'BLOCKED', admission._now(), request_id, request['claim_id']))


def _descriptor(db, root):
    database = db.execute('SELECT sql_catalog FROM databases WHERE id=?', (root['database_id'],)).fetchone()
    c = config()
    return dict(database_id=root['database_id'], sql_catalog=database[0],
                generation_id=c['foundation_generation_id'], foundation_revision=c['foundation_revision'])


def _positive(result):
    return isinstance(result, dict) and result.get('verified') is True


def _complete_observation(result, kind, intent):
    return (_positive(result) and result.get('complete') is True and result.get('kind') == kind
            and all(k in result and k in intent and type(result[k]) is type(intent[k]) and result[k] == intent[k] for k in
                    ('database_id', 'sql_catalog', 'generation_id', 'foundation_revision')))


def _observe(request):
    with control_store.connect() as db:
        root = _root(db, request['creation_id'])
        effects = [dict(r) for r in db.execute('SELECT * FROM logical_effects WHERE creation_id=?', (root['id'],))]
    for effect in effects:
        if effect['state'] not in ('INTENT', 'UNKNOWN'):
            continue
        key = effect['effect_key']
        # Refresh has separate authority and recovery; creation reconcile cannot replay it.
        if key.startswith('refresh/'):
            continue
        base = key.split('/attempt/')[0]
        kind = base.rsplit('/', 1)[-1]
        if '/leaf/' in base:
            continue
        intent = json.loads(effect['intent_json'])
        descriptor = {k:intent[k] for k in
                      ('database_id','sql_catalog','generation_id','foundation_revision') if k in intent}
        if len(descriptor) != 4 or descriptor['database_id'] != root['database_id']:
            continue
        candidate = None
        if base.startswith('verify_'):
            with control_store.connect() as db:
                root = _assert_claim(db, request)
                candidate = _verification_candidate(db, root, base, intent)
            if candidate is None:
                continue
            result = _provider.check_verify_target(descriptor, _engine_identity(candidate))
            if not _verification_proof(result, descriptor, candidate):
                continue
        else:
            result = None
        if kind == 'consumer':
            _recover_consumer(request, descriptor, effect)
            continue
        observer = dict(role='check_role', catalog='check_catalog', namespaces='check_namespaces',
                        publication='check_material', attach='check_attached').get(kind)
        if observer is None and candidate is None:
            continue
        arguments = []
        if kind == 'publication':
            arguments = [runtime_profile.private_json(intent['material_ref'])]
        elif kind == 'namespaces':
            with control_store.connect() as db:
                previous = db.execute("SELECT private_ref FROM logical_effects WHERE creation_id=? AND effect_key='consumer' AND state='ACKNOWLEDGED'",
                                      (root['id'],)).fetchone()
            if not previous:
                continue
            arguments = [runtime_profile.private_json(previous[0])['result']]
        elif kind == 'attach':
            arguments = [intent['credential_ref']]
        if candidate is None:
            result = getattr(_provider, observer)(descriptor, *arguments)
        if candidate is None and not _complete_observation(result, kind, descriptor):
            continue
        if kind == 'namespaces' and result.get('grants_verified') is not True:
            continue
        if kind == 'attach' and (result.get('binding_verified') is not True
                                 or result.get('credential_ref') != intent['credential_ref']):
            continue
        # A complete semantic parent proves its own children; it is not an SDK ACK.
        receipt = _receipt(dict(result=result, observed=True))
        with transaction() as db:
            _assert_claim(db, request, allow_stopped=True)
            if candidate is not None:
                root = _assert_claim(db, request)
                if _verification_candidate(db, root, base, intent) != candidate:
                    continue
                # Only the deterministic marker PUT is proven by this observation.
                marker = hashlib.sha256((descriptor['generation_id']+':'+str(descriptor['foundation_revision'])+':'+descriptor['database_id']).encode()).digest()
                leaf_intent = dict(method='put_object', service='s3',
                    target=_provider.config['storage']['bucket']+'/'+descriptor['database_id']+'/.shirokuma/creation-marker',
                    payload_sha256=hashlib.sha256(marker).hexdigest())
                leaf_key = hashlib.sha256(_json(leaf_intent).encode()).hexdigest()
                db.execute("""UPDATE logical_effects SET state='OBSERVED_APPLIED',private_ref=?,outcome_json='{"observed":true}'
                    WHERE creation_id=? AND (effect_key=? OR effect_key GLOB ?) AND intent_json=? AND state IN ('INTENT','UNKNOWN')""",
                    (receipt, root['id'], base+'/leaf/'+leaf_key, base+'/leaf/'+leaf_key+'/attempt/*', _json(leaf_intent)))
            else:
                db.execute("""UPDATE logical_effects SET state='OBSERVED_APPLIED',private_ref=?,outcome_json='{"observed":true}'
                    WHERE creation_id=? AND effect_key GLOB ? AND state IN ('INTENT','UNKNOWN')""",
                    (receipt, root['id'], base+'/leaf/*'))
            db.execute("""UPDATE logical_effects SET state='OBSERVED_APPLIED',private_ref=?,outcome_json='{"observed":true}'
                WHERE creation_id=? AND effect_key=? AND intent_json=? AND state IN ('INTENT','UNKNOWN')""",
                (receipt, root['id'], key, effect['intent_json']))
    with transaction() as db:
        root = _assert_claim(db, request, allow_stopped=True)
        unknown = db.execute("SELECT 1 FROM logical_effects WHERE creation_id=? AND state IN ('INTENT','UNKNOWN')", (root['id'],)).fetchone()
        if root['logical_state'] != 'STOPPED':
            db.execute('UPDATE db_creations SET logical_state=?,revision=revision+1 WHERE id=?',
                       ('UNKNOWN' if unknown else 'BLOCKED', root['id']))
        db.execute("UPDATE logical_requests SET dispatch_state='COMPLETE' WHERE id=? AND claim_id=?", (request['id'], request['claim_id']))


def _recover_consumer(request, descriptor, effect):
    """Reconstruct only from the saved issuance ACK; never allow another send."""
    def replay(subkey, safe_identity, call):
        with control_store.connect() as db:
            prior = db.execute("SELECT * FROM logical_effects WHERE creation_id=? AND effect_key=? AND state='ACKNOWLEDGED'",
                (request['creation_id'], 'consumer/leaf/'+subkey)).fetchone()
        if not prior or prior['intent_json'] != _json(safe_identity) or not prior['private_ref']:
            raise OperationError('RECONCILIATION_REQUIRED')
        return _restore_json(runtime_profile.private_json(prior['private_ref'])['result'])
    try:
        with control_store.connect() as db:
            if not db.execute("SELECT 1 FROM logical_effects WHERE creation_id=? AND effect_key GLOB 'consumer/leaf/*' AND state='ACKNOWLEDGED'", (request['creation_id'],)).fetchone():
                return
        if json.loads(effect['intent_json']) != descriptor:
            return
        with _provider.effects(replay):
            consumer = _provider.issue_consumer(descriptor)
        if (not isinstance(consumer, dict) or set(consumer) != {'client_id','client_secret','realm','principal_name'}
                or any(not isinstance(v, str) or not v for v in consumer.values())
                or not _complete_observation(_provider.check_consumer(descriptor, consumer), 'consumer', descriptor)):
            return
        receipt = _receipt(dict(result=consumer))
        with transaction() as db:
            _assert_claim(db, request, allow_stopped=True)
            db.execute("UPDATE logical_effects SET state='ACKNOWLEDGED',private_ref=? WHERE creation_id=? AND effect_key='consumer' AND intent_json=? AND state IN ('INTENT','UNKNOWN')",
                       (receipt, request['creation_id'], effect['intent_json']))
    except Exception:
        return


def _run(request):
    with transaction() as db:
        root = _assert_claim(db, request)
        descriptor = _descriptor(db, root)
    _effect(request, 'role', descriptor, lambda: _provider.create_role(descriptor))
    _effect(request, 'catalog', descriptor, lambda: _provider.create_catalog(descriptor))
    consumer = _effect(request, 'consumer', descriptor, lambda: _provider.issue_consumer(descriptor))
    _effect(request, 'namespaces', descriptor, lambda: _provider.create_namespaces(descriptor, consumer))
    material = _effect(request, 'material', descriptor, lambda: _material(request, descriptor, consumer))
    _publication(request, descriptor, material)
    with control_store.connect() as db:
        binding = registry.get_binding(db, root['database_id'])
    _effect(request, 'attach', dict(descriptor, credential_ref=binding['catalog_credential_ref']),
            lambda: _provider.attach_catalog(descriptor, binding['catalog_credential_ref']))
    with transaction() as db:
        _assert_claim(db, request)
        db.execute("UPDATE db_creations SET logical_state='VERIFYING',revision=revision+1 WHERE id=?", (root['id'],))
    identities = material['identities']
    for identity in identities:
        engine_identity = _engine_identity(identity)
        result = _effect(request, 'verify_'+identity['id'], _verification_intent(descriptor, identity),
            lambda engine_identity=engine_identity: _provider.verify_target(descriptor, engine_identity))
        if not _verification_proof(result, descriptor, identity):
            raise OperationError('CREATION_FAILED')
    _finish(request, material)


def _engine_identity(identity):
    value = {k:identity[k] for k in ('principal_id','database_id','selected_role','grant_epoch',
        'policy_revision','credential_ref')}
    value.update(registry.identity_credentials(identity))
    return value


def _verification_intent(descriptor, identity):
    return dict(descriptor, identity_id=identity['id'], username=identity['backend_username'],
                **{k: identity[k] for k in ('principal_id','selected_role','grant_epoch','policy_revision','credential_ref')})


def _verification_proof(result, descriptor, identity):
    pins = _verification_intent(descriptor, identity)
    return (_complete_observation(result, 'verification', descriptor)
            and all(result.get(k) is True for k in ('identity_positive','identity_negative','prefix_positive','prefix_negative'))
            and all(k in result and type(result[k]) is type(v) and result[k] == v
                    for k,v in pins.items() if k != 'identity_id'))


def _verification_candidate(db, root, base, intent):
    candidate = registry.get_identity(db, base[len('verify_'):])
    if not candidate or candidate['state'] != 'CANDIDATE' or candidate['database_id'] != root['database_id']:
        return None
    if _json(intent) != _json(_verification_intent(_descriptor(db, root), candidate)):
        return None
    material = db.execute("SELECT private_ref FROM logical_effects WHERE creation_id=? AND effect_key='material' AND state='ACKNOWLEDGED'",
                          (root['id'],)).fetchone()
    if not material or not material[0]:
        return None
    identities = runtime_profile.private_json(material[0])['result']['identities']
    return candidate if any(_json(candidate) == _json(saved) for saved in identities) else None


def _account_authority(account):
    if not account:
        return None
    return {key: account.get(key, default) for key, default in
            (('role', None), ('platform_admin', False), ('enabled', True), ('disabled', False))}


def _assert_refresh(db, request, root):
    pins = json.loads(request['pins_json'])
    binding = registry.resolve_binding(db, root['database_id'])
    if any(pins[key] != binding[other] for key, other in (
            ('database_id','database_id'), ('environment_id','environment_id'),
            ('warehouse_id','warehouse_id'), ('generation_id','generation_id'),
            ('foundation_revision','foundation_revision'), ('binding_revision','binding_revision'))):
        raise OperationError('RECONCILIATION_REQUIRED')
    target = pins['target_principal_id']
    grant = db.execute('SELECT revision FROM grants WHERE database_id=? AND principal_id=?',
                       (root['database_id'], target)).fetchone()
    effective = db.execute('SELECT epoch FROM effective_grants WHERE database_id=? AND principal_id=?',
                           (root['database_id'], target)).fetchone()
    if (pins['desired_revision'] != (grant[0] if grant else None)
            or pins['effective_epoch'] != (effective[0] if effective else None)
            or request['authority'] != _account_authority(request_auth._principal_accounts().get(target))):
        raise OperationError('RECONCILIATION_REQUIRED')
    _foundation(db)


def _refresh_material(request):
    import trino_auth
    pins = json.loads(request['pins_json'])
    target = pins['target_principal_id']
    with transaction() as db:
        root = _assert_claim(db, request)
        old = [dict(r) for r in db.execute("SELECT * FROM logical_backend_identities WHERE database_id=? AND principal_id=? AND state!='REVOKED'",
                                          (root['database_id'], target))]
        for identity in old:
            registry.revoke_identity(db, identity['id'])
        account = request_auth._principal_accounts().get(target)
        authorities = []
        allowed = account and account.get('enabled', True) is True and account.get('disabled', False) is False
        if allowed and legacy._admin_account(account):
            gate = db.execute('SELECT state FROM db_creation_principal_gates WHERE environment_id=? AND principal_id=?',
                              (config()['environment_id'], target)).fetchone()
            if gate is None or gate[0] == 'ACTIVE':
                authorities.append(('admin', None, 'platform_admin', _gate(db, target)))
        else:
            # Account regrant never releases an administrator tombstone.
            db.execute("""UPDATE db_creation_principal_gates SET state='STOPPED',revision=revision+1,
                reason_code='ACCOUNT_AUTHORITY_CHANGED',updated_at=? WHERE environment_id=? AND principal_id=? AND state='ACTIVE'""",
                (admission._now(), config()['environment_id'], target))
        grant = db.execute('SELECT maximum_role FROM grants WHERE database_id=? AND principal_id=?',
                           (root['database_id'], target)).fetchone()
        if allowed and grant and grant[0] in request_auth.ROLES:
            ceiling = min(request_auth.ROLES[grant[0]], request_auth.ROLES.get(account['role'], -1))
            authorities.extend((role, pins['effective_epoch'], 'grant', None)
                               for role, rank in request_auth.ROLES.items() if rank <= ceiling)
        identities = []
        seen = set()
        for role, epoch, source, gate in authorities:
            if role in seen:
                continue
            seen.add(role)
            identity_id = _id()
            identity = dict(id=identity_id, principal_id=target,
                database_id=root['database_id'], selected_role=role, grant_epoch=epoch, policy_revision=1,
                backend_username=trino_auth.scoped_username(identity_id, role), credential_ref='identity_'+_id(),
                state='CANDIDATE', verification_ref=None, authority_source=source, account_gate_revision=gate, revision=1)
            _credential(identity['credential_ref'], dict(username=identity['backend_username'], password=secrets.token_urlsafe(32)))
            identities.append(registry.put_identity(db, identity))
    return dict(identities=identities, revoked=old)


def _run_refresh(request):
    prefix = 'refresh/'+request['id']+'/'
    with transaction() as db:
        root = _assert_claim(db, request)
        descriptor = _descriptor(db, root)
        # Any unresolved publication/issuance blocks fresh execution, across requests.
        if db.execute("SELECT 1 FROM logical_effects WHERE creation_id=? AND state IN ('UNKNOWN','INTENT')", (root['id'],)).fetchone():
            raise OperationError('RECONCILIATION_REQUIRED')
    material = _effect(request, prefix+'material', dict(descriptor, target=json.loads(request['pins_json'])['target_principal_id']),
                       lambda: _refresh_material(request))
    _publication(request, descriptor, material, key=prefix+'publication')
    for identity in material['identities']:
        result = _effect(request, prefix+'verify_'+identity['id'], _verification_intent(descriptor, identity),
                         lambda identity=identity: _provider.verify_target(descriptor, _engine_identity(identity)))
        if not _verification_proof(result, descriptor, identity):
            raise OperationError('CREATION_FAILED')
    for identity in material['revoked']:
        result = _effect(request, prefix+'revoked_'+identity['id'], dict(descriptor, identity_id=identity['id']),
                         lambda identity=identity: _provider.verify_revoked(descriptor, _engine_identity(identity)))
        if not _positive(result) or result.get('control_positive') is not True or result.get('identity_denied') is not True:
            raise OperationError('CREATION_FAILED')
    with transaction() as db:
        _assert_claim(db, request)
        pins = json.loads(request['pins_json'])
        if pins['desired_revision'] is not None:
            grant = db.execute('SELECT maximum_role FROM grants WHERE database_id=? AND principal_id=?',
                               (root['database_id'], pins['target_principal_id'])).fetchone()
            account = request['authority']
            role = 'revoked'
            if account and account['enabled'] is True and account['disabled'] is False and grant[0] in request_auth.ROLES:
                ceiling = min(request_auth.ROLES[grant[0]], request_auth.ROLES.get(account['role'], -1))
                role = next((r for r,v in request_auth.ROLES.items() if v == ceiling), 'revoked')
            db.execute("""UPDATE effective_grants SET maximum_role=?,applied_revision=?,applied_generation_id=?
                WHERE database_id=? AND principal_id=? AND epoch=?""", (role, pins['desired_revision'], pins['generation_id'],
                root['database_id'], pins['target_principal_id'], pins['effective_epoch']))
        for identity in material['identities']:
            if registry.get_identity(db, identity['id']) != identity:
                raise OperationError('RECONCILIATION_REQUIRED')
            proof = db.execute("SELECT private_ref FROM logical_effects WHERE creation_id=? AND effect_key=? AND state='ACKNOWLEDGED'",
                (root['id'], prefix+'verify_'+identity['id'])).fetchone()
            registry.put_identity(db, dict(identity, state='ACTIVE', revision=2, verification_ref=proof[0]))
        db.execute("UPDATE logical_requests SET dispatch_state='COMPLETE',result_json=? WHERE id=? AND claim_id=?",
                   (_json(dict(engine_verified=True)), request['id'], request['claim_id']))


def _account_refresh_loop(stop, baseline, interval):
    while not stop.wait(interval):
        try:
            baseline = _enqueue_account_changes(baseline)
        except Exception:
            # Retain baseline for a later valid account file/executor; never log credentials.
            continue


def _enqueue_account_changes(baseline):
    current = request_auth._principal_accounts()
    affected = {p for p in set(current) | set(baseline)
                if _account_authority(current.get(p)) != _account_authority(baseline.get(p))}
    with transaction() as db:
        _gate(db, _executor_id)
        c = config()
        ready = {r[0] for r in db.execute("""SELECT d.id FROM databases d
            JOIN db_creations root ON root.database_id=d.id
            JOIN logical_bindings b ON b.database_id=d.id
            JOIN logical_foundations f ON f.environment_id=b.environment_id AND f.warehouse_id=b.warehouse_id
            JOIN catalog_bindings catalog ON catalog.database_id=d.id
            WHERE d.state='READY' AND root.logical_state='READY' AND root.creation_mode='logical_v1'
              AND b.environment_id=? AND b.warehouse_id=?
              AND root.environment_id=b.environment_id AND root.warehouse_id=b.warehouse_id
              AND f.status='READY' AND f.generation_id=? AND f.revision=?
              AND b.foundation_revision=f.revision AND catalog.applied_generation_id=f.generation_id
              AND catalog.desired_revision=catalog.applied_revision
              AND b.applied_policy_revision>0 AND b.verification_ref IS NOT NULL""",
            (c['environment_id'], c['warehouse_id'], c['foundation_generation_id'], c['foundation_revision']))}
        for principal in sorted(affected):
            dbids = {r[0] for r in db.execute("SELECT DISTINCT database_id FROM logical_backend_identities WHERE principal_id=? AND state='ACTIVE'", (principal,))}
            # Desired authority survives a completed disable, even when effective
            # authority and every historical identity have already been revoked.
            dbids.update(r[0] for r in db.execute('SELECT database_id,maximum_role FROM grants WHERE principal_id=?',
                         (principal,)) if r[1] in request_auth.ROLES)
            dbids.intersection_update(ready)
            if legacy._admin_account(current.get(principal)):
                gate = db.execute('SELECT state FROM db_creation_principal_gates WHERE environment_id=? AND principal_id=?',
                                  (config()['environment_id'], principal)).fetchone()
                if gate is None or gate[0] == 'ACTIVE':
                    _gate(db, principal)
                    dbids.update(ready)
            for dbid in sorted(dbids):
                registry.request_auth_refresh(db, _executor_id, principal, None,
                    dict(database_id=dbid, reason='ACCOUNT_AUTHORITY_CHANGED', idempotency_key=_id()))
        # Snapshot once, including earlier passes that met a busy root lock.
        # Metadata limits the scan; dispatch still validates every authority pin.
        requests = [r[0] for r in db.execute("""SELECT r.id FROM logical_requests r
            JOIN db_creations root ON root.id=r.creation_id
            JOIN logical_bindings b ON b.database_id=root.database_id
            JOIN logical_foundations f ON f.environment_id=b.environment_id AND f.warehouse_id=b.warehouse_id
            JOIN catalog_bindings catalog ON catalog.database_id=b.database_id
            WHERE r.action='AUTH_REFRESH' AND r.dispatch_state='ACCEPTED'
              AND root.creation_mode='logical_v1'
              AND r.environment_id=? AND root.environment_id=r.environment_id
              AND root.warehouse_id=? AND b.environment_id=root.environment_id AND b.warehouse_id=root.warehouse_id
              AND f.generation_id=? AND f.revision=? AND b.foundation_revision=f.revision
              AND catalog.applied_generation_id=f.generation_id
              AND json_extract(r.pins_json,'$.environment_id')=f.environment_id
              AND json_extract(r.pins_json,'$.warehouse_id')=f.warehouse_id
              AND json_extract(r.pins_json,'$.generation_id')=f.generation_id
              AND json_extract(r.pins_json,'$.foundation_revision')=f.revision
            ORDER BY r.created_at,r.id""", (c['environment_id'], c['warehouse_id'],
                c['foundation_generation_id'], c['foundation_revision']))]
    # The owned lifetime task performs finite passes and is joined before provider close.
    for request_id in requests:
        dispatch(request_id)
    return current


def _credential(ref, value):
    runtime_profile.credential_ref(ref)
    path = Path(config()['credentials_dir']) / (ref + '.json')
    expected = dict(version=1, **value)
    try:
        with open(path, 'x', opener=lambda p, f: os.open(p, f, 0o600)) as stream:
            stream.write(_json(expected))
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if _json(runtime_profile.private_json(path)) != _json(expected):
            raise OperationError('RECONCILIATION_REQUIRED') from None
        with open(path, 'rb') as stream:
            os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _material(request, descriptor, consumer):
    """Mint per-principal identities; only private material contains passwords."""
    import trino_auth
    pins = json.loads(request['pins_json'])
    with transaction() as db:
        root = _assert_claim(db, request)
        binding = registry.get_binding(db, root['database_id'])
        identities = []
        credentials = {binding['catalog_credential_ref']: dict(client_id=consumer['client_id'], client_secret=consumer['client_secret'])}
        _credential(binding['catalog_credential_ref'], credentials[binding['catalog_credential_ref']])
        accounts = request_auth._principal_accounts()
        authorities = [(p, 'admin', None, 'platform_admin', _gate(db, p))
                       for p, a in sorted(accounts.items()) if legacy._admin_account(a)]
        for grant in pins['grants']:
            account = accounts.get(grant['principal_id'])
            if not account or account.get('enabled', True) is not True or account.get('disabled', False) is not False:
                continue
            ceiling = min(request_auth.ROLES[grant['maximum_role']], request_auth.ROLES.get(account['role'], -1))
            for role, rank in request_auth.ROLES.items():
                if rank <= ceiling:
                    authorities.append((grant['principal_id'], role, grant['epoch'], 'grant', None))
        # Platform admins use their individual admin identity, not a duplicate grant identity.
        seen = set()
        for principal, role, epoch, source, gate in authorities:
            if (principal, role) in seen:
                continue
            seen.add((principal, role))
            identity_id = _id()
            identity = dict(id=identity_id, principal_id=principal, database_id=root['database_id'],
                selected_role=role, grant_epoch=epoch, policy_revision=pins['policy_revision'],
                backend_username=trino_auth.scoped_username(identity_id, role), credential_ref='identity_'+_id(),
                state='CANDIDATE', verification_ref=None, authority_source=source, account_gate_revision=gate, revision=1)
            value = dict(username=identity['backend_username'], password=secrets.token_urlsafe(32))
            _credential(identity['credential_ref'], value)
            credentials[identity['credential_ref']] = value
            identities.append(registry.put_identity(db, identity))
    return dict(credentials=credentials, identities=identities)


def _publication(request, descriptor, material, *, key='publication'):
    # One short artifact lock, independent of admission and the root locks.
    path = _private_dir() / 'publication.lock'
    with open(path, 'a', opener=lambda p, f: os.open(p, f, 0o600)) as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        with control_store.connect() as db:
            old = db.execute("""SELECT * FROM logical_effects WHERE creation_id=?
                AND (effect_key=? OR (effect_key GLOB ? AND effect_key NOT GLOB ?)) ORDER BY rowid DESC LIMIT 1""",
                (request['creation_id'], key, key+'/attempt/*', key+'/attempt/*/*')).fetchone()
        if old and not (old['state'] == 'REJECTED' and request['action'] == 'RESUME'):
            return _effect(request, key, json.loads(old['intent_json']), lambda: None)
        # Metadata snapshot only. Preserve previously published and unresolved
        # material without probing any other database or using a mutable manifest.
        with control_store.connect() as db:
            target_binding = registry.get_binding(db, descriptor['database_id'])
            catalog_binding = db.execute('SELECT credential_profile_ref FROM catalog_bindings WHERE database_id=?',
                                         (descriptor['database_id'],)).fetchone()
            if (not target_binding or target_binding['database_id'] != descriptor['database_id'] or
                    target_binding['foundation_revision'] != descriptor['foundation_revision'] or not catalog_binding or
                    target_binding['catalog_credential_ref'] != catalog_binding[0]):
                raise ValueError('Invalid target catalog binding')
            target_ref = runtime_profile.credential_ref(target_binding['catalog_credential_ref'])
            target_pair = runtime_profile.private_json(Path(config()['credentials_dir']) / (target_ref+'.json'))
            if not isinstance(target_pair, dict):
                raise ValueError('Invalid target catalog credentials')
            target_pair = {k:v for k,v in target_pair.items() if k != 'version'}
            if (set(target_pair) != {'client_id', 'client_secret'} or
                    any(not isinstance(v, str) or not v.strip() for v in target_pair.values())):
                raise ValueError('Invalid target catalog credentials')
            rows = db.execute('''SELECT i.*,d.sql_catalog FROM logical_backend_identities i
                JOIN databases d ON d.id=i.database_id JOIN db_creations c ON c.database_id=i.database_id
                WHERE i.state IN ('ACTIVE','CANDIDATE') AND (c.id=? OR EXISTS
                (SELECT 1 FROM logical_effects e WHERE e.creation_id=c.id AND e.effect_key='publication'))''',
                (request['creation_id'],)).fetchall()
            covered = set()
            for effect in db.execute("SELECT private_ref FROM logical_effects WHERE (effect_key='material' OR effect_key GLOB 'refresh/*/material') AND state='ACKNOWLEDGED' AND private_ref IS NOT NULL"):
                saved = runtime_profile.private_json(effect[0])['result']
                covered.update(i['id'] for i in saved['identities'])
            identities = []
            descriptors, credentials = {}, {}
            for row in rows:
                item = dict(row)
                if item['state'] == 'CANDIDATE' and item['id'] not in covered:
                    continue
                catalog = item.pop('sql_catalog')
                identities.append(item)
                descriptors[item['database_id']] = dict(descriptor, database_id=item['database_id'], sql_catalog=catalog)
                ref = item['credential_ref']
                value = runtime_profile.private_json(Path(config()['credentials_dir']) / (ref+'.json'))
                credentials[ref] = {k:v for k,v in value.items() if k != 'version'}
            for row in db.execute('SELECT database_id FROM logical_bindings'):
                dbid = row[0]
                if dbid not in descriptors:
                    other = db.execute('SELECT * FROM db_creations WHERE database_id=?', (dbid,)).fetchone()
                    # Only material with a durable publication intent belongs in shared artifacts.
                    if not other or not db.execute("SELECT 1 FROM logical_effects WHERE creation_id=? AND (effect_key='publication' OR effect_key GLOB 'refresh/*/publication')", (other['id'],)).fetchone():
                        continue
                    descriptors[dbid] = _descriptor(db, dict(other))
            for dbid in descriptors:
                binding = registry.get_binding(db, dbid)
                ref = binding['catalog_credential_ref']
                value = runtime_profile.private_json(Path(config()['credentials_dir']) / (ref+'.json'))
                credentials[ref] = {k:v for k,v in value.items() if k != 'version'}
            credentials[target_ref] = target_pair
        try:
            hashes = _validate_hashes(_provider.publication_hashes(descriptor))
        except Exception:
            raise OperationError('FOUNDATION_NOT_READY', 503) from None
        payload = _render_material(identities, descriptors, credentials, verifier_cache=_verifier_cache)
        payload['credentials'] = {target_ref: target_pair}
        payload['rules_json'] = _json(payload['rules_json'])
        payload['expected_hashes'] = hashes
        material_ref = _receipt(payload)
        def publish_and_verify():
            # Nested business leaf ACKs are durable before semantic readback.
            _provider.publish_material(descriptor, payload)
            try:
                result = _provider.check_material(descriptor, payload)
                if not _complete_observation(result, 'publication', descriptor):
                    raise ValueError()
                return result
            except Exception:
                # The write returned: a failed observation cannot prove no effects.
                raise OperationError('RECONCILIATION_REQUIRED') from None
        return _effect(request, key, dict(descriptor, material_ref=material_ref), publish_and_verify)


def _render_material(identities, descriptors, credentials, *, verifier_cache=None):
    """Render the existing ACL/OPA semantics for principal-specific usernames.

    A short artifact lock serializes the local snapshot. Publication must merge
    this target delta atomically with the provider's current shared material.
    """
    import database_security
    import trino_auth
    admin = registry.platform_admin_credentials()
    rules = None
    known = {admin['username']: True}
    engineers, admins = {}, {}
    for identity in identities:
        descriptor = descriptors[identity['database_id']]
        user, role, token = identity['backend_username'], identity['selected_role'], identity['id']
        entry = dict(version=2, platform_admin='shirokuma_admin', databases={token: dict(
            sql_catalog=descriptor['sql_catalog'], generation_id=descriptor['generation_id'],
            roles={r: trino_auth.scoped_username(token, r) for r in request_auth.ROLES})})
        rendered = database_security.render_file_acl_identities(entry)
        if rules is None:
            rules = {key: [] for key in rendered}
        for key, entries in rendered.items():
            rules[key].extend(r for r in entries if r.get('user') == re.escape(user))
            # Trino CREATE CATALOG requires owner for the internal provisioner.
            rules[key].extend(dict(r, user=re.escape(admin['username']))
                              if key != 'catalogs' else dict(r, user=re.escape(admin['username']), allow='owner')
                              for r in entries if r.get('user') == 'shirokuma_admin')
        known[user] = True
        if role == 'data_engineer':
            engineers[user] = descriptor['sql_catalog']
        if role == 'admin':
            admins[user] = descriptor['sql_catalog']
    if rules is None:
        entry = dict(version=2, platform_admin='shirokuma_admin', databases={
            dbid: dict(sql_catalog=d['sql_catalog'], generation_id=d['generation_id'],
                       roles={r: trino_auth.scoped_username(dbid.replace('-', ''), r) for r in request_auth.ROLES})
            for dbid,d in descriptors.items()})
        rendered = database_security.render_file_acl_identities(entry)
        rules = {key: [] for key in rendered}
    # Internal readiness queries need exactly this system table after publication.
    internal = re.escape(admin['username'])
    rules['catalogs'].append(dict(user=internal, catalog='system', allow='read-only'))
    rules['schemas'].append(dict(user=internal, catalog='system', schema='runtime', owner=False))
    rules['tables'].append(dict(user=internal, catalog='system', schema='runtime', table='nodes', privileges=['SELECT']))
    # Retain the renderer's internal provisioner and deny defaults once.
    for key, entries in rendered.items():
        rules[key].extend(r for r in entries if r.get('user') is None)
    opa = database_security.render_opa_policy_identities(entry)
    for name, value in [('known_users', known), ('registered_catalogs', {d['sql_catalog']: True for d in descriptors.values()}),
                        ('engineer_catalogs', engineers), ('scoped_admin_catalogs', admins)]:
        opa = re.sub(r'^'+name+r' := .*$', lambda _: name+' := '+_json(value), opa, flags=re.M)
    opa = opa.replace('"shirokuma_admin"', json.dumps(admin['username']))
    # Trino 483's file authenticator verifies PBKDF2WithHmacSHA1 records.
    lines = []
    records = [(config()['platform_admin_credential_ref'] if verifier_cache is not None else None, admin),
               *((i['credential_ref'], credentials[i['credential_ref']]) for i in identities)]
    for ref, value in records:
        lines.append(verifier_cache.record(ref, value['username'], value['password']) if verifier_cache is not None
                     else _password_record(value['username'], value['password'], _HASH_POLICY))
    return dict(password_db='\n'.join(lines)+'\n', rules_json=rules, guard_rego=opa)


def _finish(request, material):
    with transaction() as db:
        root = _assert_claim(db, request)
        pins = json.loads(request['pins_json'])
        proofs = {r['effect_key'].split('/attempt/')[0]: dict(r) for r in db.execute('SELECT * FROM logical_effects WHERE creation_id=? ORDER BY rowid', (root['id'],))}
        required = ['role','catalog','consumer','namespaces','material','publication','attach'] + ['verify_'+i['id'] for i in material['identities']]
        if any(k not in proofs or proofs[k]['state'] not in ('ACKNOWLEDGED','OBSERVED_APPLIED') or not proofs[k]['private_ref'] for k in required):
            raise OperationError('RECONCILIATION_REQUIRED')
        for identity in material['identities']:
            current = registry.get_identity(db, identity['id'])
            if current != identity:
                raise OperationError('RECONCILIATION_REQUIRED')
        proof = proofs['publication']['private_ref']
        binding = registry.get_binding(db, root['database_id'])
        binding.update(applied_policy_revision=pins['policy_revision'], verification_ref=proof, revision=binding['revision']+1)
        registry.put_binding(db, binding, expected_revision=pins['binding_revision'])
        db.execute('''UPDATE catalog_bindings SET applied_revision=desired_revision,applied_generation_id=?
            WHERE database_id=? AND revision=?''', (pins['generation_id'], root['database_id'], pins['catalog_revision']))
        db.execute("UPDATE databases SET state='READY' WHERE id=?", (root['database_id'],))
        db.execute("UPDATE db_creations SET logical_state='READY',terminal_outcome='READY',revision=revision+1 WHERE id=? AND logical_state!='STOPPED'", (root['id'],))
        for grant in pins['grants']:
            db.execute('''UPDATE effective_grants SET maximum_role=?,applied_revision=?,applied_generation_id=?
                WHERE database_id=? AND principal_id=? AND epoch=?''',
                (grant['maximum_role'], grant['revision'], pins['generation_id'], root['database_id'], grant['principal_id'], grant['epoch']))
        for identity in material['identities']:
            identity = dict(identity, state='ACTIVE', verification_ref=proofs['verify_'+identity['id']]['private_ref'], revision=2)
            registry.put_identity(db, identity)
        db.execute("UPDATE operations SET state='READY' WHERE id=?", (root['create_operation_id'],))
        db.execute('''UPDATE logical_requests SET dispatch_state='COMPLETE',result_json='{"ready":true}' WHERE id=? AND claim_id=?''',
                   (request['id'], request['claim_id']))


def capabilities(principal, role):
    legacy.current_admin(principal, role)
    c = config()
    blocker, revision, warehouse_state = None, None, None
    try:
        _bound_profile()
        with control_store.connect() as db:
            _foundation(db)
            _gate(db, principal.principal_id, read_only=True)
            _gate(db, _executor_id, read_only=True)
            row = db.execute('SELECT revision FROM warehouse_controls WHERE warehouse_id=?', (c['warehouse_id'],)).fetchone()
            revision = row[0]
        # Only cheap native compute metadata is allowed here, after closing SQLite.
        try:
            state = _provider.compute_state()
            if type(state) is not str or state not in ('ACTIVE', 'STOPPED', 'TRANSITIONING'):
                raise ValueError()
            warehouse_state = state
            if state == 'STOPPED':
                blocker = 'WAREHOUSE_START_REQUIRED'
            elif state != 'ACTIVE' or _provider.bound_alive() is not True:
                blocker = 'FOUNDATION_NOT_READY'
        except Exception:
            blocker = 'FOUNDATION_NOT_READY'
    except OperationError as error:
        blocker = error.code
    if _provider is None:
        blocker = 'OPERATOR_UNAVAILABLE'
    result = dict(environment_id=c['environment_id'], warehouse_id=c['warehouse_id'], warehouse_revision=revision,
        configured=True, available=blocker is None, blocker=blocker, creation_mode='logical_v1',
        foundation_id=c['foundation_generation_id'], foundation_revision=c['foundation_revision'],
        can_create=blocker is None, can_resume=blocker is None, can_reconcile=_provider is not None)
    if warehouse_state is not None:
        result['warehouse_state'] = warehouse_state
    return result


def _summary(db, root, principal):
    database = db.execute('SELECT * FROM databases WHERE id=?', (root['database_id'],)).fetchone()
    binding = registry.get_binding(db, root['database_id'])
    state = root['logical_state']
    # GET only projects abandoned work. It never changes or claims a request.
    if state in ('CREATING', 'VERIFYING'):
        with _lock(root['id']) as free:
            if free and db.execute("SELECT 1 FROM logical_requests WHERE creation_id=? AND dispatch_state='CLAIMED'", (root['id'],)).fetchone():
                state = 'UNKNOWN'
    actions = [] if state in ('READY','STOPPED') or database['state'].upper() in ('RETIRING','RETIRED','RECONCILING') else ['withdraw']
    if state in ('UNKNOWN','BLOCKED'):
        actions.append('reconcile')
    if state == 'BLOCKED' and not db.execute("SELECT 1 FROM logical_effects WHERE creation_id=? AND state IN ('INTENT','UNKNOWN')", (root['id'],)).fetchone():
        actions.append('resume')
    grants = [dict(r) for r in db.execute('''SELECT g.principal_id,g.maximum_role,e.maximum_role AS effective_role,
        g.revision,e.epoch FROM grants g JOIN effective_grants e USING(principal_id,database_id) WHERE g.database_id=?''', (root['database_id'],))]
    return dict(creation_id=root['id'], database_id=root['database_id'], sql_catalog=database['sql_catalog'],
        display_name=database['display_name'], description=database['description'], requester_id=root['actor'],
        revision=root['revision'], lifecycle=database['state'].upper(), initial_grants=grants, plans=[],
        blocker={'UNKNOWN':'RECONCILIATION_REQUIRED','BLOCKED':'CREATION_FAILED','STOPPED':'PRINCIPAL_STOPPED'}.get(state),
        progress_code=state, next_actions=actions if principal.principal_id == root['actor'] else [],
        access_source='platform_admin', creation_mode='logical_v1', foundation_id=root['enrollment_id'],
        foundation_revision=root['enrollment_revision'], binding_revision=binding['revision'],
        policy_revision=binding['desired_policy_revision'])


def get(principal, role, root_id):
    legacy.current_admin(principal, role)
    with control_store.connect() as db:
        return _summary(db, _root(db, root_id), principal)


def get_by_name(principal, role, name):
    legacy.current_admin(principal, role)
    with control_store.connect() as db:
        root_id = legacy._name_id(db, config()['environment_id'], 'logical_v1', name)
    return get(principal, role, root_id)


def listing(principal, role, *, limit=50, cursor=None):
    legacy.current_admin(principal, role)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise OperationError('INVALID_REQUEST', 400)
    c, after = config(), ('','')
    if cursor is not None:
        try:
            value = json.loads(base64.urlsafe_b64decode(cursor.encode('ascii')))
            if type(value) is not list or len(value) != 4 or value[:2] != [c['environment_id'], principal.principal_id] or not all(isinstance(v,str) for v in value):
                raise ValueError()
            after = tuple(value[2:])
        except (ValueError, TypeError, UnicodeError, binascii.Error):
            raise OperationError('INVALID_REQUEST', 400) from None
    with control_store.connect() as db:
        rows = db.execute('''SELECT * FROM db_creations WHERE environment_id=? AND creation_mode='logical_v1'
            AND (created_at,id)>(?,?) ORDER BY created_at,id LIMIT ?''', (c['environment_id'], *after, limit+1)).fetchall()
        token = None
        if len(rows) > limit:
            last = rows[limit-1]
            token = base64.urlsafe_b64encode(_json([c['environment_id'], principal.principal_id, last['created_at'],last['id']]).encode()).decode()
        return dict(creations=[_summary(db, dict(r), principal) for r in rows[:limit]], cursor=token)


def check_environment(headers):
    values = headers.get_all('X-Shirokuma-Environment', [])
    if not values:
        return
    if len(values) != 1 or re.fullmatch('[A-Za-z0-9_-]{1,128}', values[0]) is None:
        raise OperationError('INVALID_REQUEST', 400)
    if values[0] != config()['environment_id']:
        raise OperationError('ENVIRONMENT_MISMATCH', 409)
