"""Public generation tools and explicit one-shot warehouse management."""
import argparse
import json
import os
import re
from pathlib import Path
import sys


PLAN_ROUTE = '/api/v1/warehouses/LOCAL/plans'
CREATE_ROUTE = '/api/v1/databases'
REGISTER_ROUTE = '/api/v1/databases/register-spec'


def register_spec(principal, role, public_spec, idempotency_key):
    """Reconstruct approved declarative identities only in an empty registry."""
    import control_store
    import data_management as dm
    import database_generation as generation
    import warehouse_operations as operations
    import warehouse_admission as admission
    import uuid
    require_admin(principal, role)
    if not operations._text(idempotency_key):
        raise operations.OperationError('INVALID_REGISTER_SPEC', 400)
    try:
        spec = generation.validate_spec(public_spec)
    except (ValueError, TypeError):
        raise operations.OperationError('INVALID_REGISTER_SPEC', 400) from None
    payload = operations._json(spec)
    actor = principal.principal_id
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        saved = db.execute('SELECT * FROM registry_register_requests WHERE actor=? AND route=? AND idempotency_key=?',
                           (actor, REGISTER_ROUTE, idempotency_key)).fetchone()
        if saved:
            if saved['payload'] != payload:
                raise operations.OperationError('IDEMPOTENCY_CONFLICT')
            return dict(operation_id=saved['operation_id'], database_ids=sorted(spec['databases']), state='REGISTERED')
        import database_creations
        database_creations.guard_reservation(db, 'LOCAL')
        control = admission._ensure(db, 'LOCAL')
        if control['state'] != 'OPEN' or control['operation_id'] is not None:
            raise operations.OperationError('WAREHOUSE_OPERATION_CONFLICT')
        for table in ('databases', 'catalog_bindings', 'schemas', 'grants', 'object_bindings',
                      'warehouse_config_generations', 'query_records', 'admission_records'):
            if db.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone():
                raise operations.OperationError('REGISTER_SPEC_REQUIRES_EMPTY_REGISTRY')
        for key, value in spec['databases'].items():
            db.execute('INSERT INTO databases VALUES (?,?,?,?,?,?,?)',
                (key, value['sql_catalog'], value['sql_catalog'], '', 'user', 'registered', value['database_revision']))
            binding = value['binding']
            dm.create_binding(db, key, **{field: setting for field, setting in binding.items() if field != 'desired_revision'})
            db.execute('UPDATE catalog_bindings SET desired_revision=? WHERE database_id=?', (binding['desired_revision'], key))
            for schema in value['schemas']:
                db.execute('INSERT INTO schemas VALUES (?,?,?,?,?,?,?)', (schema['id'], key, schema['sql_name'],
                    schema['sql_name'], schema['purpose'], 'user', schema['revision']))
        for grant in spec['grant_revisions']:
            dm.record_grant(db, dm.Grant(**grant))
        operation_id = str(uuid.uuid4())
        dm.record_operation(db, dm.Operation(operation_id, actor, 'DATABASE_SPEC_REGISTER', (), None,
            'REGISTERED', (), None, (), ()))
        db.execute('INSERT INTO registry_register_requests VALUES (?,?,?,?,?)',
                   (actor, REGISTER_ROUTE, idempotency_key, payload, operation_id))
        return dict(operation_id=operation_id, database_ids=sorted(spec['databases']), state='REGISTERED')


def create_database(principal, role, payload, idempotency_key):
    """Register one empty database and its create intent in a single transaction."""
    import control_store
    import data_management as dm
    import warehouse_operations as operations
    import warehouse_admission as admission
    import uuid
    require_admin(principal, role)
    if (type(payload) is not dict or set(payload) != {'sql_catalog', 'profile_id'}
            or payload['profile_id'] != 'local-polaris-1.8.0'
            or not operations._text(idempotency_key)):
        raise operations.OperationError('INVALID_DATABASE_CREATE', 400)
    try:
        sql_name = dm.validate_sql_name(payload['sql_catalog'])
        if sql_name == 'iceberg':
            raise ValueError()
    except (TypeError, ValueError):
        raise operations.OperationError('INVALID_DATABASE_CREATE', 400) from None
    normalized = operations._json(payload)
    actor = principal.principal_id
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT * FROM database_create_requests WHERE actor=? AND route=? AND idempotency_key=?',
                              (actor, CREATE_ROUTE, idempotency_key)).fetchone()
        if previous:
            if previous['payload'] != normalized:
                raise operations.OperationError('IDEMPOTENCY_CONFLICT')
            return dict(database_id=previous['database_id'], operation_id=previous['operation_id'], state='REGISTERED')
        import database_creations
        database_creations.guard_reservation(db, 'LOCAL')
        control = admission._ensure(db, 'LOCAL')
        if control['state'] != 'OPEN' or control['operation_id'] is not None:
            raise operations.OperationError('WAREHOUSE_OPERATION_CONFLICT')
        return _create_database(db, actor, sql_name, idempotency_key, normalized)


def _create_database(db, actor, sql_name, key, normalized):
    """Internal registration helper; the caller owns admission and transaction."""
    import data_management as dm
    import warehouse_operations as operations
    import uuid
    if db.execute('SELECT 1 FROM databases WHERE sql_catalog=?', (sql_name,)).fetchone():
        raise operations.OperationError('DATABASE_ALREADY_EXISTS')
    database = dm.create_database(db, sql_name, sql_name, '', 'user')
    token = database.id.replace('-', '')
    dm.create_binding(db, database.id, 'polaris-'+token[:24], 'DB_'+token,
        'db_'+database.id, 'local-s3', 'local-polaris',
        's3://shirokuma-lakehouse/databases/'+database.id+'/')
    for name in ('raw', 'staging', 'marts', 'sandbox'):
        dm.create_schema(db, database.id, name, name, name, 'user')
    operation_id = str(uuid.uuid4())
    dm.record_operation(db, dm.Operation(operation_id, actor, 'DATABASE_CREATE', (),
        None, 'REGISTERED', (), None, (('database_id', database.id),), ()))
    db.execute('INSERT INTO database_create_requests VALUES (?,?,?,?,?,?)',
        (actor, CREATE_ROUTE, key, normalized, database.id, operation_id))
    return dict(database_id=database.id, operation_id=operation_id, state='REGISTERED')


def export_spec(generation_id, reference_spec):
    """Export all durable identities using a previously approved public reference profile.

    Existing Secret references are retained byte for byte; only database-set dependent
    derived shared references change when a database is added. The authentication
    reference also changes when upgrading the backend identity role set.
    """
    import copy
    import hashlib
    import control_store
    import database_generation as generation
    # Export is a new rendering against today's approved template. The reference
    # supplies historical public Secret identities, not authority to run old code.
    current_reference = copy.deepcopy(reference_spec)
    if current_reference.get('profile', {}).get('id') != generation.PROFILE_ID:
        raise ValueError('Registered profile required')
    current_reference['version'] = 2
    current_reference['profile']['template_fingerprint'] = generation.profile_fingerprint(version=2)
    reference = generation.validate_spec(current_reference)
    result = copy.deepcopy(reference)
    result['generation_id'] = generation_id
    result['databases'] = {}
    binding_fields = ('desired_revision', 'polaris_service_ref', 'polaris_realm', 'polaris_catalog',
        'storage_profile_ref', 'credential_profile_ref', 'base_location')
    with control_store.connect() as db:
        for row in db.execute('SELECT * FROM databases ORDER BY id'):
            key = row['id']
            binding = db.execute('SELECT * FROM catalog_bindings WHERE database_id=?', (key,)).fetchone()
            if binding is None:
                raise ValueError('Database binding required')
            old = reference['databases'].get(key)
            refs = copy.deepcopy(old['secret_refs']) if old else {
                role: dict(namespace='shirokuma-dev', name='db-'+generation._token(key)+'-'+role.replace('_','-')+'-v1',
                           version='v1', keys=list(keys)) for role, keys in generation.DB_KEYS.items()}
            result['databases'][key] = dict(database_revision=row['revision'], sql_catalog=row['sql_catalog'],
                binding={name: binding[name] for name in binding_fields}, secret_refs=refs,
                schemas=[dict(s) for s in db.execute('SELECT id,sql_name,purpose,revision FROM schemas WHERE database_id=? ORDER BY sql_name', (key,))])
        import grant_management
        result['grant_revisions'] = grant_management.desired_projection(db)
    if set(result['databases']) != set(reference['databases']):
        version = 'v'+hashlib.sha256(operations_json(sorted(result['databases'])).encode()).hexdigest()[:12]
        for role in ('polaris_bootstrap', 'seaweed_server'):
            ref = result['shared_secret_refs'][role]
            ref.update(name=role.replace('_','-')+'-'+version, version=version)
    if set(result['databases']) != set(reference['databases']) or reference_spec['version'] != 2:
        version = 'v'+hashlib.sha256(operations_json(dict(databases=sorted(result['databases']),
            roles=['bi_viewer', 'data_engineer', 'admin'])).encode()).hexdigest()[:12]
        result['shared_secret_refs']['trino_auth'].update(name='trino-auth-'+version, version=version)
    result['shared_secret_refs']['polaris_bootstrap']['keys'] = sorted(generation.bootstrap_key(key) for key in result['databases'])
    return generation.validate_spec(result)


def operations_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def require_admin(principal, role):
    import request_auth
    if (type(principal) is not request_auth.AuthenticatedPrincipal or role != 'admin' or
            principal.maximum_role != 'admin' or principal.platform_admin is not True):
        raise PermissionError('Platform administrator with selected admin role required')


def registered_bundle(generation_id):
    import database_generation
    if not isinstance(generation_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', generation_id):
        raise ValueError('Invalid generation identifier')
    root = Path(os.environ['SHIROKUMA_PUBLIC_BUNDLE_ROOT']).resolve()
    bundle = database_generation.load_bundle(root / generation_id)
    if bundle['manifest']['generation_id'] != generation_id:
        raise ValueError('Generation identifier mismatch')
    return bundle


def public_plan(principal, role, generation_id, idempotency_key, *, phase='full', predecessor_id=None):
    import warehouse_operations
    require_admin(principal, role)
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 256:
        raise ValueError('Idempotency key required')
    bundle = registered_bundle(generation_id)
    observations = json.loads((Path(os.environ['SHIROKUMA_PUBLIC_OBSERVATIONS_ROOT']) /
                               (generation_id + '.json')).read_bytes())
    return warehouse_operations.create_plan(principal.principal_id, PLAN_ROUTE,
        idempotency_key, bundle, observations, phase=phase, predecessor_id=predecessor_id)


def inspect_operation(principal, role, operation_id):
    import warehouse_operations
    import control_store
    require_admin(principal, role)
    admin = role == 'admin' and principal.platform_admin and principal.maximum_role == 'admin'
    with control_store.connect() as db:
        import grant_management
        if db.execute('SELECT 1 FROM execution_recovery_requests WHERE operation_id=?', (operation_id,)).fetchone():
            # Recovery has its own narrow request and proof contract.
            import execution_recovery
            # Generic inspection authenticates current admin without any owner result access.
            account = __import__('request_auth')._principal_accounts().get(principal.principal_id)
            if not account or account.get('role') != 'admin' or account.get('platform_admin') is not True:
                raise PermissionError('Current platform administrator required')
            recovery = db.execute('SELECT * FROM execution_recovery_requests WHERE operation_id=?', (operation_id,)).fetchone()
            intent = json.loads(recovery['intent'])
            if recovery['route'] == execution_recovery.QueryRecovery.route:
                return dict(operation_id=operation_id, actor=recovery['actor'], phase=recovery['phase'],
                    revision=recovery['revision'], query_id=intent['graph']['queries'][0]['id'],
                    intent_digest=execution_recovery.QueryRecovery.digest(dict(recovery)),
                    outcome='UNKNOWN', execution_closure=execution_recovery.CLOSURE if recovery['phase'] in ('CLOSED','OPEN') else '')
            return dict(operation_id=operation_id, actor=recovery['actor'], phase=recovery['phase'],
                revision=recovery['revision'], run_id=intent['graph']['run']['id'],
                outcome='UNKNOWN', execution_closure=execution_recovery.CLOSURE if recovery['phase'] in ('CLOSED','OPEN') else '')
        if db.execute('SELECT 1 FROM metadata_requests WHERE operation_id=?', (operation_id,)).fetchone():
            require_admin(principal, role)
            import display_management
            return display_management.inspect(db, operation_id)
        if db.execute('SELECT 1 FROM grant_requests WHERE operation_id=?', (operation_id,)).fetchone():
            require_admin(principal, role)
            return grant_management.inspect(db, operation_id, principal.principal_id, admin)
        registered = db.execute('''SELECT o.id,o.actor,o.action,o.state,r.payload FROM operations o
            JOIN registry_register_requests r ON r.operation_id=o.id
            WHERE o.id=? AND o.action='DATABASE_SPEC_REGISTER' ''', (operation_id,)).fetchone()
        if registered is not None:
            if registered['actor'] != principal.principal_id and not admin:
                raise warehouse_operations.OperationError('OPERATION_NOT_FOUND', 404)
            return dict(id=registered['id'], actor=registered['actor'], action=registered['action'],
                state=registered['state'], database_ids=sorted(json.loads(registered['payload'])['databases']))
        row = db.execute('''SELECT o.id,o.actor,o.action,o.state,c.database_id FROM operations o
                            JOIN database_create_requests c ON c.operation_id=o.id
                            WHERE o.id=? AND o.action='DATABASE_CREATE' ''', (operation_id,)).fetchone()
        if row is not None:
            if row['actor'] != principal.principal_id and not admin:
                raise warehouse_operations.OperationError('OPERATION_NOT_FOUND', 404)
            return dict(row)
    operation = warehouse_operations.inspect(operation_id, actor=principal.principal_id, is_admin=admin)
    return dict(operation, handoffs=warehouse_operations.inspect_actions(operation_id,
        actor=principal.principal_id, is_admin=admin))


def request_action(principal, role, operation_id, route, idempotency_key, payload):
    import warehouse_operations
    require_admin(principal, role)
    return warehouse_operations.request_action(operation_id, actor=principal.principal_id,
        route=route, idempotency_key=idempotency_key, payload=payload, is_admin=True)


def reconcile_query(auth_headers, query_id):
    # This path does not need the optional generation dependencies or operator config.
    principal, role = authenticate_headers(auth_headers)
    import query_service
    result = query_service.reconcile_query_from_backend(principal, query_id, selected_role=role)
    # Only the public query view returned by query_service crosses this boundary.
    return result


def validate_selected_role(principal, role):
    import request_auth
    if (type(principal) is not request_auth.AuthenticatedPrincipal or
            not isinstance(role, str) or role not in request_auth.ROLES or
            request_auth.ROLES[role] > request_auth.ROLES[principal.maximum_role]):
        raise PermissionError('Single canonical role within current maximum required')


def authenticate_headers(path):
    import request_auth
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate authentication header')
            result[key] = value
        return result
    headers = json.loads(private_auth_file(path), object_pairs_hook=pairs)
    if not isinstance(headers, dict) or set(headers) != {'Authorization', 'X-Shirokuma-Role'}:
        raise ValueError('Explicit authentication and selected role headers required')
    principal = request_auth.authenticate_principal(headers)
    role = headers['X-Shirokuma-Role']
    validate_selected_role(principal, role)
    return principal, role


def inspect_from_auth(auth_headers, operation_id):
    principal, role = authenticate_headers(auth_headers)
    return inspect_operation(principal, role, operation_id)


def private_auth_file(path):
    path = Path(path).resolve()
    if path.stat().st_mode & 0o777 != 0o600 or path.parent.stat().st_mode & 0o777 != 0o700:
        raise ValueError('Private authentication file permissions required')
    return path.read_bytes()

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('plan', 'apply', 'reconcile', 'inspect'):
        recovery = commands.add_parser('recover-query-' + name)
        recovery.add_argument('--config', required=True, type=Path)
        if name == 'plan':
            recovery.add_argument('--query-id', required=True)
            recovery.add_argument('--historical-pod-snapshot', type=Path)
            recovery.add_argument('--historical-container-id')
            recovery.add_argument('--historical-predecessor-pod-snapshot', type=Path)
            recovery.add_argument('--historical-predecessor-container-id')
            recovery.add_argument('--historical-query-intent', type=Path)
            recovery.add_argument('--historical-query-result', type=Path)
            recovery.add_argument('--idempotency-key', required=True)
        else:
            recovery.add_argument('--operation-id', required=True)
        if name in ('apply', 'reconcile'):
            recovery.add_argument('--expected-revision', required=True, type=int)
            recovery.add_argument('--expected-intent-digest', required=True)
    for name in ('plan', 'apply', 'reconcile', 'inspect'):
        recovery = commands.add_parser('recover-job-' + name)
        recovery.add_argument('--config', required=True, type=Path)
        if name == 'plan':
            recovery.add_argument('--run-id', required=True)
            recovery.add_argument('--idempotency-key', required=True)
        else:
            recovery.add_argument('--operation-id', required=True)
        if name in ('apply', 'reconcile'):
            recovery.add_argument('--expected-revision', required=True, type=int)
    render = commands.add_parser('render')
    render.add_argument('--spec', required=True, type=Path)
    render.add_argument('--output', required=True, type=Path)
    render.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    check = commands.add_parser('check')
    check.add_argument('--bundle', required=True, type=Path)
    check.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    query = commands.add_parser('reconcile-query')
    query.add_argument('--query-id', required=True)
    query.add_argument('--auth-headers', type=Path, required=True)
    inspect = commands.add_parser('inspect-operation')
    inspect.add_argument('--operation-id', required=True)
    inspect.add_argument('--auth-headers', type=Path, required=True)
    create = commands.add_parser('create')
    create.add_argument('--sql-catalog', required=True)
    create.add_argument('--profile-id', default='local-polaris-1.8.0')
    create.add_argument('--idempotency-key', required=True)
    create.add_argument('--auth-headers', type=Path, required=True)
    export = commands.add_parser('export-spec')
    export.add_argument('--reference-spec', type=Path, required=True)
    export.add_argument('--generation-id', required=True)
    register = commands.add_parser('register-spec')
    register.add_argument('--spec', type=Path, required=True)
    register.add_argument('--idempotency-key', required=True)
    register.add_argument('--auth-headers', type=Path, required=True)
    for name in ('plan', 'apply', 'reconcile'):
        command = commands.add_parser(name)
        command.add_argument('--config', required=True, type=Path)
        if name == 'plan':
            command.add_argument('--idempotency-key', required=True)
        else:
            command.add_argument('--operation-id', required=True)
        if name == 'apply':
            command.add_argument('--expected-revision', required=True, type=int)
        if name == 'reconcile':
            command.add_argument('--step', required=True, choices=('publish_snapshot', 'apply_secrets',
                'reconcile_storage', 'reconcile_polaris', 'reconcile_trino', 'verify'))
            command.add_argument('--verify-readonly', action='store_true')
            command.add_argument('--expected-revision', type=int)
            command.add_argument('--delivery-digest')
            command.add_argument('--receipt-digest')
    args = parser.parse_args(argv)
    if args.command == 'reconcile':
        supplied = [args.expected_revision is not None, args.delivery_digest is not None, args.receipt_digest is not None]
        if (args.verify_readonly and (args.step != 'verify' or not all(supplied)
                or args.expected_revision < 1 or not all(re.fullmatch(r'[0-9a-f]{64}', v or '')
                    for v in (args.delivery_digest, args.receipt_digest)))
                or not args.verify_readonly and any(supplied)):
            parser.error('read-only verify requires --step verify and all revision/digest flags together')
    try:
        if args.command.startswith(('recover-job-', 'recover-query-')):
            import execution_recovery
            print(json.dumps(execution_recovery.run(args), sort_keys=True))
            return 0
        if args.command == 'register-spec':
            principal, role = authenticate_headers(args.auth_headers)
            print(json.dumps(register_spec(principal, role, json.loads(args.spec.read_bytes()), args.idempotency_key), sort_keys=True))
            return 0
        if args.command == 'create':
            principal, role = authenticate_headers(args.auth_headers)
            print(json.dumps(create_database(principal, role, dict(sql_catalog=args.sql_catalog,
                profile_id=args.profile_id), args.idempotency_key), sort_keys=True))
            return 0
        if args.command == 'export-spec':
            print(json.dumps(export_spec(args.generation_id, json.loads(args.reference_spec.read_bytes())), sort_keys=True))
            return 0
        if args.command == 'reconcile-query':
            print(json.dumps(reconcile_query(args.auth_headers, args.query_id), sort_keys=True))
            return 0
        if args.command == 'inspect-operation':
            print(json.dumps(inspect_from_auth(args.auth_headers, args.operation_id), sort_keys=True))
            return 0
        if args.command not in ('render', 'check'):
            import warehouse_operator
            print(json.dumps(warehouse_operator.run(args), sort_keys=True))
            return 0
        import database_generation as generation
        if args.command == 'render':
            bundle = generation.render(json.loads(args.spec.read_bytes()), args.root)
            generation.write_bundle(bundle, args.output)
        else:
            bundle = generation.load_bundle(args.bundle, args.root)
        print(json.dumps(dict(generation_id=bundle['manifest']['generation_id'],
            delivery_digest=bundle['manifest']['delivery_digest'], files=len(bundle['files'])), sort_keys=True))
        return 0
    except ModuleNotFoundError as error:
        if error.name == 'yaml':
            print('PyYAML is required; use the generation venv with tools/local_experiment/requirements-generation.txt.', file=sys.stderr)
        else:
            print('Database command dependency unavailable (details withheld)', file=sys.stderr)
        return 1
    except Exception:
        print('Database command failed; validate authorized inputs (details withheld; no automatic retry)', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
