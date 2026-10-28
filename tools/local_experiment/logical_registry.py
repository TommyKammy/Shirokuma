"""DB-local logical authority. Imports are inert; no provider or connection creation.

Mutators use the caller's open transaction. Rows contain private references and
public proof references only, never credential values. Callers own publication
and verification; this module cannot attest external effects.
"""
from pathlib import Path
import runtime_profile


FOUNDATION = ('environment_id', 'warehouse_id', 'generation_id', 'revision',
              'status', 'config_ref', 'verification_ref')
BINDING = ('database_id', 'environment_id', 'warehouse_id', 'foundation_revision',
           'desired_policy_revision', 'applied_policy_revision',
           'catalog_credential_ref', 'verification_ref', 'revision')
IDENTITY = ('id', 'principal_id', 'database_id', 'selected_role', 'grant_epoch',
            'policy_revision', 'backend_username', 'credential_ref', 'state',
            'verification_ref', 'authority_source', 'account_gate_revision', 'revision')
IMMUTABLE_IDENTITY = set(IDENTITY) - {'state', 'verification_ref', 'revision'}
ROLE_RANK = {'revoked': -1, 'bi_viewer': 0, 'data_engineer': 1, 'admin': 2}


def _one(db, sql, args):
    cursor = db.execute(sql, args)
    row = cursor.fetchone()
    return dict(zip((column[0] for column in cursor.description), row)) if row is not None else None


def _transaction(db):
    if not db.in_transaction:
        raise ValueError('Caller transaction required')


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and not any(c in value for c in '\x00\r\n')


def _positive(value):
    return type(value) is int and value > 0


def _values(values, columns):
    if not isinstance(values, dict) or set(values) != set(columns):
        raise ValueError('Invalid logical row fields')
    value = dict(values)
    for name in columns:
        item = value[name]
        if name in ('grant_epoch', 'account_gate_revision'):
            if item is not None and not _positive(item):
                raise ValueError('Invalid logical authority revision')
        elif name == 'applied_policy_revision':
            if type(item) is not int or item < 0:
                raise ValueError('Invalid logical revision')
        elif name.endswith('revision'):
            if not _positive(item):
                raise ValueError('Invalid logical revision')
        elif name in ('verification_ref', 'config_ref'):
            if not isinstance(item, str) and not (name == 'verification_ref' and item is None):
                raise ValueError('Invalid logical proof reference')
        elif not _text(item):
            raise ValueError('Invalid logical row')
    return value


def creation_mode(db, database_id):
    """Persisted root decides mode even before its candidate binding exists."""
    row = _one(db, 'SELECT creation_mode FROM db_creations WHERE database_id=?', (database_id,))
    return row['creation_mode'] if row else 'legacy'


def get_foundation(db, environment_id, warehouse_id):
    return _one(db, 'SELECT * FROM logical_foundations WHERE environment_id=? AND warehouse_id=?',
                (environment_id, warehouse_id))


def _verified_foundation(db, value):
    generation = _one(db, 'SELECT state,warehouse_id FROM warehouse_config_generations WHERE id=?',
                      (value['generation_id'],))
    if (value['status'] != 'READY' or not _text(value['config_ref']) or
            not _text(value['verification_ref']) or generation is None or
            generation['warehouse_id'] != value['warehouse_id'] or generation['state'] != 'APPLIED'):
        raise ValueError('Verified applied foundation required')


def _put(db, table, columns, keys, value, expected_revision):
    _transaction(db)
    where = ' AND '.join(name + '=?' for name in keys)
    args = tuple(value[name] for name in keys)
    old = _one(db, f'SELECT * FROM {table} WHERE {where}', args)
    if old is None:
        if expected_revision is not None or value['revision'] != 1:
            raise ValueError('Stale logical revision')
        db.execute(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',
                   tuple(value[name] for name in columns))
    else:
        if (not _positive(expected_revision) or old['revision'] != expected_revision or
                value['revision'] != expected_revision + 1):
            raise ValueError('Stale logical revision')
        names = [name for name in columns if name not in keys]
        updated = db.execute(f'UPDATE {table} SET {",".join(name+"=?" for name in names)} WHERE {where} AND revision=?',
                            tuple(value[name] for name in names) + args + (expected_revision,))
        if updated.rowcount != 1:
            raise ValueError('Stale logical revision')
    return _one(db, f'SELECT * FROM {table} WHERE {where}', args)


def put_foundation(db, values, expected_revision=None):
    """All FOUNDATION keys required; insert revision=1, update expected+1 CAS."""
    _transaction(db)
    value = _values(values, FOUNDATION)
    if value['status'] == 'READY':
        _verified_foundation(db, value)
    return _put(db, 'logical_foundations', FOUNDATION, ('environment_id', 'warehouse_id'), value, expected_revision)


def get_binding(db, database_id):
    return _one(db, 'SELECT * FROM logical_bindings WHERE database_id=?', (database_id,))


def put_binding(db, values, expected_revision=None):
    """All BINDING keys required; insert revision=1, update expected+1 CAS.

    Catalog desired/applied revisions remain owned by the DB binding writer.
    """
    _transaction(db)
    value = _values(values, BINDING)
    runtime_profile.credential_ref(value['catalog_credential_ref'])
    foundation = get_foundation(db, value['environment_id'], value['warehouse_id'])
    if foundation is None or value['foundation_revision'] != foundation['revision']:
        raise ValueError('Stale foundation')
    if value['applied_policy_revision'] > value['desired_policy_revision']:
        raise ValueError('Invalid policy revision')
    return _put(db, 'logical_bindings', BINDING, ('database_id',), value, expected_revision)


def get_identity(db, identity_id):
    return _one(db, 'SELECT * FROM logical_backend_identities WHERE id=?', (identity_id,))


def _gate(db, binding, identity):
    gate = _one(db, '''SELECT state,revision FROM db_creation_principal_gates
        WHERE environment_id=? AND principal_id=?''', (binding['environment_id'], identity['principal_id']))
    if (gate is None or gate['state'] != 'ACTIVE' or
            gate['revision'] != identity['account_gate_revision']):
        raise ValueError('Stale identity authority')


def put_identity(db, values):
    """All IDENTITY keys required (authority defaults to grant, gate to None).

    New rows start CANDIDATE revision 1. Updates change only state, proof and
    revision (old+1); identical retries return the row. REVOKED is terminal.
    ACTIVE requires a proof and a published policy. No regrant reuses an ID/ref.
    """
    _transaction(db)
    value = _values({**dict(authority_source='grant', account_gate_revision=None), **values}, IDENTITY)
    runtime_profile.credential_ref(value['credential_ref'])
    if value['authority_source'] == 'grant':
        if not _positive(value['grant_epoch']):
            raise ValueError('Invalid grant epoch')
    elif (value['authority_source'] != 'platform_admin' or value['grant_epoch'] is not None or
          value['selected_role'] != 'admin' or not _positive(value['account_gate_revision'])):
        raise ValueError('Invalid identity authority')
    old = get_identity(db, value['id'])
    if old is not None:
        if value == old:
            return old
        if (any(value[name] != old[name] for name in IMMUTABLE_IDENTITY) or
                old['state'] == 'REVOKED' or value['revision'] != old['revision'] + 1 or
                value['state'] not in ('CANDIDATE', 'ACTIVE', 'REVOKED') or
                (old['state'] == 'ACTIVE' and value['state'] == 'CANDIDATE')):
            raise ValueError('Immutable identity or stale revision')
    elif value['state'] != 'CANDIDATE' or value['revision'] != 1:
        raise ValueError('New identity must be candidate revision 1')
    binding = get_binding(db, value['database_id'])
    if binding is None:
        raise ValueError('Known logical binding required')
    if value['authority_source'] == 'platform_admin' and value['state'] != 'REVOKED':
        _gate(db, binding, value)
    if value['state'] == 'ACTIVE':
        effective = resolve_binding(db, value['database_id'])
        if not _text(value['verification_ref']) or value['policy_revision'] > effective['applied_policy_revision']:
            raise ValueError('Verified published identity required')
        _authority(db, effective, value)
    if old is None:
        db.execute(f'INSERT INTO logical_backend_identities ({",".join(IDENTITY)}) VALUES ({",".join("?" for _ in IDENTITY)})',
                   tuple(value[name] for name in IDENTITY))
    else:
        updated = db.execute('''UPDATE logical_backend_identities SET state=?,verification_ref=?,revision=?
            WHERE id=? AND revision=?''', (value['state'], value['verification_ref'], value['revision'], value['id'], old['revision']))
        if updated.rowcount != 1:
            raise ValueError('Stale identity revision')
    return get_identity(db, value['id'])


def revoke_identity(db, identity_id, verification_ref=None):
    """Retain identity metadata and credentials ref forever; no provider calls."""
    _transaction(db)
    identity = get_identity(db, identity_id)
    if identity is None:
        raise ValueError('Unknown identity')
    if identity['state'] == 'REVOKED':
        return identity
    identity.update(state='REVOKED', verification_ref=verification_ref, revision=identity['revision'] + 1)
    return put_identity(db, identity)


def resolve_binding(db, database_id, expected_generation_id=None):
    """Public locator + foundation/binding/policy pins; never credentials/paths.

    A pending policy for another principal does not invalidate applied authority.
    """
    root = _one(db, 'SELECT * FROM db_creations WHERE database_id=?', (database_id,))
    binding = get_binding(db, database_id)
    catalog = _one(db, 'SELECT * FROM catalog_bindings WHERE database_id=?', (database_id,))
    database = _one(db, 'SELECT state,sql_catalog FROM databases WHERE id=?', (database_id,))
    if (root is None or root['creation_mode'] != 'logical_v1' or root['logical_state'] != 'READY' or
            database is None or binding is None or catalog is None):
        raise ValueError('Logical database not ready')
    foundation = get_foundation(db, binding['environment_id'], binding['warehouse_id'])
    if foundation is None:
        raise ValueError('Missing foundation')
    _verified_foundation(db, foundation)
    if database['state'] != 'READY':
        import warehouse_admission
        handle = warehouse_admission.current()
        if (database['state'] != 'retiring' or
                type(handle) is not warehouse_admission.AdmissionHandle or
                handle.database_id != database_id or handle.warehouse_id != foundation['warehouse_id'] or
                handle.generation_id != foundation['generation_id'] or
                not warehouse_admission.continuing_database(db, handle.actor, database_id, foundation['generation_id'])):
            raise ValueError('Logical database not ready')
    if (root['environment_id'] != binding['environment_id'] or root['warehouse_id'] != binding['warehouse_id'] or
            binding['foundation_revision'] != foundation['revision'] or
            catalog['desired_revision'] != catalog['applied_revision'] or
            catalog['applied_generation_id'] != foundation['generation_id'] or
            (expected_generation_id is not None and expected_generation_id != foundation['generation_id']) or
            binding['applied_policy_revision'] <= 0 or not _text(binding['verification_ref'])):
        raise ValueError('Stale logical binding')
    profile = runtime_profile.current()
    if profile is not None:
        config = runtime_profile.logical_config(profile)
        if any(config[key] != expected for key, expected in (
                ('environment_id', foundation['environment_id']), ('warehouse_id', foundation['warehouse_id']),
                ('foundation_generation_id', foundation['generation_id']), ('foundation_revision', foundation['revision']))):
            raise ValueError('Stale runtime foundation')
    return dict(database_id=database_id, sql_catalog=database['sql_catalog'],
                service_ref=catalog['polaris_service_ref'], realm=catalog['polaris_realm'],
                catalog=catalog['polaris_catalog'], generation_id=foundation['generation_id'],
                foundation_id=foundation['generation_id'], foundation_revision=foundation['revision'],
                binding_revision=catalog['applied_revision'], logical_binding_revision=binding['revision'],
                environment_id=binding['environment_id'], warehouse_id=binding['warehouse_id'],
                desired_policy_revision=binding['desired_policy_revision'],
                applied_policy_revision=binding['applied_policy_revision'],
                catalog_credential_ref=binding['catalog_credential_ref'])


def _authority(db, binding, identity):
    if identity['authority_source'] == 'platform_admin':
        _gate(db, binding, identity)
        return
    grant = _one(db, '''SELECT g.maximum_role AS desired_role,g.revision AS desired_revision,
        e.maximum_role,e.epoch,e.applied_revision,e.applied_generation_id
        FROM grants g JOIN effective_grants e USING(principal_id,database_id)
        WHERE g.principal_id=? AND g.database_id=?''', (identity['principal_id'], identity['database_id']))
    if (grant is None or grant['epoch'] != identity['grant_epoch'] or
            grant['applied_revision'] != grant['desired_revision'] or
            grant['applied_generation_id'] != binding['generation_id'] or
            min(ROLE_RANK.get(grant['maximum_role'], -1), ROLE_RANK.get(grant['desired_role'], -1)) <
            ROLE_RANK[identity['selected_role']]):
        raise ValueError('Stale identity authority')


def resolve_identity(db, principal_id, database_id, selected_role, grant_epoch, platform_admin=False):
    """Internal resolver AFTER fresh caller account/role/platform authorization.

    platform_admin is a trusted authorization decision, never public API input.
    Platform SQL authority remains per principal and checks its current gate.
    """
    binding = resolve_binding(db, database_id)
    return _identity_for_binding(db, binding, principal_id, database_id, selected_role, grant_epoch, platform_admin)


def _identity_for_binding(db, binding, principal_id, database_id, selected_role, grant_epoch, platform_admin):
    identity = _one(db, '''SELECT * FROM logical_backend_identities WHERE principal_id=?
        AND database_id=? AND selected_role=? AND state='ACTIVE' ''', (principal_id, database_id, selected_role))
    if (identity is None or identity['authority_source'] != ('platform_admin' if platform_admin else 'grant') or
            identity['grant_epoch'] != grant_epoch or
            identity['policy_revision'] > binding['applied_policy_revision'] or not _text(identity['verification_ref'])):
        raise ValueError('No verified identity authority')
    _authority(db, binding, identity)
    return identity


class _Credentials(dict):
    """Private adapter mapping with no credential values in incidental logging."""
    def __repr__(self):
        return '<private credentials>'

    __str__ = __repr__


def _credentials(ref, names):
    try:
        runtime_profile.generation_attestation()
        config = runtime_profile.logical_config()
        runtime_profile.credential_ref(ref)
        value = runtime_profile.private_json(Path(config['credentials_dir']) / (ref + '.json'))
        if (not isinstance(value, dict) or set(value) != {'version', *names} or
                type(value['version']) is not int or value['version'] != 1 or
                any(not _text(value[name]) for name in names)):
            raise ValueError()
        return _Credentials({name: value[name] for name in names})
    except (ValueError, OSError, TypeError, KeyError, AttributeError):
        raise ValueError('Private credential reference unavailable or invalid') from None


def identity_credentials(identity):
    """Private SQL adapter only; never use the internal provisioner credential."""
    config = runtime_profile.logical_config()
    if identity.get('credential_ref') == config['platform_admin_credential_ref']:
        raise ValueError('Internal credential cannot authorize user SQL')
    result = _credentials(identity.get('credential_ref'), ('username', 'password'))
    if result['username'] != identity.get('backend_username'):
        raise ValueError('Private credential identity mismatch')
    return result


def catalog_credentials(binding):
    """Private catalog adapter only; returns client_id/client_secret."""
    return _credentials(binding.get('catalog_credential_ref'), ('client_id', 'client_secret'))


def platform_admin_credentials():
    """INTERNAL observer/provisioner only. Never a user SQL identity."""
    config = runtime_profile.logical_config()
    return _credentials(config['platform_admin_credential_ref'], ('username', 'password'))


def request_auth_refresh(db, actor, target, grant_operation_id, pins):
    """PRIVATE ONLY: enqueue authority work in the caller's transaction.

    pins requires database_id. Account edits also require reason and a private
    idempotency_key. Returns a durable request ID; never dispatches or verifies.
    """
    import hashlib
    import json
    import uuid
    import request_auth
    from warehouse_admission import _now
    _transaction(db)
    actor = actor.principal_id if type(actor) is request_auth.AuthenticatedPrincipal else actor
    if (not _text(actor) or not _text(target) or not isinstance(pins, dict) or
            not _text(pins.get('database_id'))):
        raise ValueError('Invalid refresh context')
    database_id = pins['database_id']
    binding = resolve_binding(db, database_id)
    root = _one(db, 'SELECT * FROM db_creations WHERE database_id=?', (database_id,))
    accounts = request_auth._principal_accounts()
    account = accounts.get(actor)
    if (not account or account['role'] != 'admin' or account['platform_admin'] is not True or
            account.get('enabled', True) is not True or account.get('disabled', False) is not False):
        raise PermissionError('Current platform administrator required')
    gate = _one(db, 'SELECT * FROM db_creation_principal_gates WHERE environment_id=? AND principal_id=?',
                (root['environment_id'], actor))
    if gate is not None and gate['state'] != 'ACTIVE':
        raise PermissionError('Active requester gate required')
    canonical = dict(database_id=database_id, target_principal_id=target,
        operation_id=grant_operation_id, environment_id=binding['environment_id'],
        warehouse_id=binding['warehouse_id'], generation_id=binding['generation_id'],
        foundation_revision=binding['foundation_revision'], binding_revision=binding['binding_revision'])
    desired = _one(db, 'SELECT * FROM grants WHERE database_id=? AND principal_id=?', (database_id, target))
    effective = _one(db, 'SELECT * FROM effective_grants WHERE database_id=? AND principal_id=?', (database_id, target))
    if grant_operation_id is not None:
        operation = _one(db, 'SELECT * FROM operations WHERE id=?', (grant_operation_id,))
        audit = _one(db, 'SELECT * FROM grant_audit WHERE operation_id=?', (grant_operation_id,))
        if (not operation or operation['action'] != 'GRANT_CHANGE' or operation['actor'] != actor or
                not audit or audit['actor'] != actor or audit['database_id'] != database_id or
                audit['principal_id'] != target or not desired or not effective or
                audit['desired_revision'] != desired['revision'] or audit['after_role'] != desired['maximum_role'] or
                audit['effective_epoch'] != effective['epoch']):
            raise ValueError('Refresh operation does not match current target authority')
        canonical.update(desired_revision=desired['revision'], effective_epoch=effective['epoch'])
        private_key = 'grant:' + grant_operation_id
    else:
        if pins.get('reason') != 'ACCOUNT_AUTHORITY_CHANGED' or not _text(pins.get('idempotency_key')):
            raise ValueError('Explicit private account refresh required')
        canonical.update(reason='ACCOUNT_AUTHORITY_CHANGED', desired_revision=desired['revision'] if desired else None,
                         effective_epoch=effective['epoch'] if effective else None)
        private_key = 'account:' + pins['idempotency_key']
    # Supplied authority pins cannot replace the freshly resolved values.
    if any(key in pins and pins[key] != value for key, value in canonical.items()):
        raise ValueError('Stale refresh pins')
    normalized = json.dumps(canonical, sort_keys=True, separators=(',', ':'))
    key = hashlib.sha256(private_key.encode()).hexdigest()
    route = 'private:logical-auth-refresh'
    for table in ('logical_requests', 'db_creation_requests'):
        saved = _one(db, f'SELECT id,payload,action FROM {table} WHERE environment_id=? AND actor=? AND route=? AND idempotency_key=?',
                     (root['environment_id'], actor, route, key))
        if saved:
            if table != 'logical_requests' or saved['action'] != 'AUTH_REFRESH' or saved['payload'] != normalized:
                raise ValueError('Refresh idempotency conflict')
            return saved['id']
    config = runtime_profile.logical_config()
    if any(config[key] != binding[other] for key, other in (
            ('environment_id', 'environment_id'), ('warehouse_id', 'warehouse_id'),
            ('foundation_generation_id', 'generation_id'), ('foundation_revision', 'foundation_revision'))):
        raise ValueError('Stale refresh foundation')
    executor_gate = _one(db, 'SELECT * FROM db_creation_principal_gates WHERE environment_id=? AND principal_id=?',
                         (root['environment_id'], config['executor_id']))
    if executor_gate is None or executor_gate['state'] != 'ACTIVE':
        raise PermissionError('Active executor gate required')
    if gate is None:
        db.execute('''INSERT INTO db_creation_principal_gates
            (environment_id,principal_id,state,revision,reason_code,request_id,updated_at)
            VALUES (?,?,'ACTIVE',1,NULL,NULL,?)''', (root['environment_id'], actor, _now()))
        gate = _one(db, 'SELECT * FROM db_creation_principal_gates WHERE environment_id=? AND principal_id=?',
                    (root['environment_id'], actor))
    request_id, now = str(uuid.uuid4()), _now()
    ack = json.dumps(dict(request_id=request_id, creation_id=root['id'], action='AUTH_REFRESH',
                         accepted_revision=root['revision']), sort_keys=True, separators=(',', ':'))
    db.execute('''INSERT INTO logical_requests
        (id,creation_id,environment_id,actor,route,idempotency_key,payload,ack,action,
         dispatch_state,executor_id,requester_gate_revision,executor_gate_revision,pins_json,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,'AUTH_REFRESH','ACCEPTED',?,?,?,?,?,?)''',
        (request_id, root['id'], root['environment_id'], actor, route, key, normalized, ack,
         config['executor_id'], gate['revision'], executor_gate['revision'], normalized, now, now))
    return request_id
