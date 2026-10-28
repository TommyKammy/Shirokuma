"""P5 creation admission and durable approvals; no provider or owner dispatch.

The private preparation/owner implementations supply a live foundation adapter.
Without it new admission fails closed. Existing requests remain inspectable.
"""
import base64
import binascii
from dataclasses import dataclass
import json
import re
import uuid
from contextlib import contextmanager
from urllib.parse import parse_qs, unquote, urlsplit

import catalog_admin
import control_store
import data_management as dm
import display_management
import grant_management
import request_auth
import warehouse_admission as admission
from warehouse_operations import OperationError, _json, _text

COLLECTION = '/api/v1/admin/database-creations'


@dataclass(frozen=True)
class Enrollment:
    environment_id: str
    warehouse_id: str
    enrollment_id: str
    revision: int
    executor_id: str

    def __post_init__(self):
        if (not all(_text(v) for v in (self.environment_id, self.warehouse_id,
                self.enrollment_id, self.executor_id)) or type(self.revision) is not int or self.revision < 1):
            raise ValueError('Invalid enrollment')


@dataclass(frozen=True)
class FoundationReadiness:
    foundation_ready: bool
    owner_ready: bool


_enrollment = None
_foundation = None


def install_foundation(enrollment, adapter):
    """Private composition boundary for future preparation and canonical owners.

    An adapter must inspect actual foundation and owner state on every admission;
    a configured flag or the existence of this object is never readiness.
    """
    global _enrollment, _foundation
    if type(enrollment) is not Enrollment or not callable(getattr(adapter, 'probe', None)):
        raise TypeError('Typed enrollment and live foundation probe required')
    _enrollment, _foundation = enrollment, adapter


def enrollment():
    if type(_enrollment) is not Enrollment:
        raise OperationError('FOUNDATION_NOT_READY', 503)
    return _enrollment


def _readiness():
    config = enrollment()
    if _foundation is None:
        raise OperationError('FOUNDATION_NOT_READY', 503)
    try:
        ready = _foundation.probe(config)
    except Exception:
        raise OperationError('FOUNDATION_NOT_READY', 503) from None
    if type(ready) is not FoundationReadiness or ready.foundation_ready is not True:
        raise OperationError('FOUNDATION_NOT_READY', 503)
    if ready.owner_ready is not True:
        raise OperationError('OPERATOR_UNAVAILABLE', 503)
    return config


def current_admin(principal, role='admin'):
    """Selected role denial is distinct from loss of account authority."""
    if role != 'admin' or type(principal) is not request_auth.AuthenticatedPrincipal:
        catalog_admin.require_admin(principal, role)
    try:
        account = request_auth._principal_accounts().get(principal.principal_id)
    except request_auth.AuthenticationError:
        raise OperationError('ADMIN_REQUIRED', 403) from None
    if not _admin_account(account):
        # Authenticated current-account loss stops durable work. A bad password
        # or a browser's role selector never reaches this branch.
        if type(_enrollment) is Enrollment:
            from database_creation_auth import stop_lost_authority
            stop_lost_authority(_enrollment, principal.principal_id)
        raise OperationError('ADMIN_REQUIRED', 403)
    catalog_admin.require_admin(principal, role)
    return account


def _admin_account(account):
    return (isinstance(account, dict) and account.get('role') == 'admin'
            and account.get('platform_admin') is True and account.get('enabled', True) is True
            and account.get('disabled', False) is False)


def principals(principal, role):
    current_admin(principal, role)
    accounts = request_auth._principal_accounts()
    return {'principals': [dict(principal_id=name, display_name=account.get('display_name', name),
        maximum_role=account['role']) for name, account in sorted(accounts.items())
        if account.get('enabled', True) is True and account.get('disabled', False) is False]}


def capabilities(principal, role):
    current_admin(principal, role)
    config = _enrollment
    blocker = None
    try:
        _readiness()
    except OperationError as error:
        blocker = error.code
    revision = None
    if type(config) is Enrollment:
        _projection_authority(config, {principal.principal_id, config.executor_id})
        with control_store.connect() as db:
            row = db.execute('SELECT revision FROM warehouse_controls WHERE warehouse_id=?', (config.warehouse_id,)).fetchone()
            revision = row[0] if row else None
            if db.execute("""SELECT 1 FROM db_creation_principal_gates WHERE environment_id=?
                    AND principal_id IN (?,?) AND state='STOPPED'""",
                    (config.environment_id,principal.principal_id,config.executor_id)).fetchone():
                blocker = 'PRINCIPAL_STOPPED'
    return dict(environment_id=config.environment_id if config else None,
        warehouse_id=config.warehouse_id if config else None, warehouse_revision=revision,
        configured=type(config) is Enrollment, available=blocker is None, blocker=blocker)


def validate_key(key):
    if not isinstance(key, str) or not 1 <= len(key) <= 256 or not key.strip() or any(c in key for c in '\x00\r\n'):
        raise OperationError('INVALID_REQUEST', 400)


def _intent(payload):
    if type(payload) is not dict or 'sql_catalog' not in payload or set(payload) - {
            'sql_catalog', 'display_name', 'description', 'initial_grants'}:
        raise OperationError('INVALID_REQUEST', 400)
    try:
        name = dm.validate_sql_name(payload['sql_catalog'])
        if name == 'iceberg':
            raise ValueError()
    except (ValueError, TypeError):
        raise OperationError('INVALID_NAME', 400) from None
    value = dict(sql_catalog=name, display_name=payload.get('display_name', name),
                 description=payload.get('description', ''), initial_grants=payload.get('initial_grants', []))
    display_management.validate_payload('database', dict(display_name=value['display_name'],
        description=value['description'], expected_revision=1))
    grants, seen = value['initial_grants'], set()
    if type(grants) is not list:
        raise OperationError('INVALID_INITIAL_GRANTS', 400)
    for grant in grants:
        if (type(grant) is not dict or set(grant) != {'principal_id', 'maximum_role'}
                or not _text(grant.get('principal_id')) or grant['principal_id'] in seen
                or not isinstance(grant.get('maximum_role'), str) or grant['maximum_role'] not in request_auth.ROLES):
            raise OperationError('INVALID_INITIAL_GRANTS', 400)
        seen.add(grant['principal_id'])
    value['initial_grants'] = sorted(grants, key=lambda g: g['principal_id'])
    return value


def _validate_current_grants(intent):
    accounts = request_auth._principal_accounts()
    for grant in intent['initial_grants']:
        account = accounts.get(grant['principal_id'])
        if (not account or account.get('enabled', True) is not True or account.get('disabled', False) is not False
                or request_auth.ROLES[grant['maximum_role']] > request_auth.ROLES[account['role']]):
            raise OperationError('INVALID_INITIAL_GRANTS',400)


def _replan_target_grants(db, root):
    """Bound fresh desired access by the immutable intake and live accounts."""
    accepted = {g['principal_id']: g['maximum_role']
        for g in json.loads(root['intent_json'])['initial_grants']}
    desired = {r['principal_id']: r['maximum_role'] for r in db.execute(
        "SELECT principal_id,maximum_role FROM grants WHERE database_id=? AND maximum_role!='revoked'",
        (root['database_id'],))}
    if any(p not in accepted or request_auth.ROLES[role] > request_auth.ROLES[accepted[p]]
            for p, role in desired.items()):
        raise OperationError('UNAPPLIED_CHANGES')
    try:
        _validate_current_grants(dict(initial_grants=[dict(principal_id=p,maximum_role=role)
            for p, role in desired.items()]))
    except (OperationError, request_auth.AuthenticationError):
        raise OperationError('UNAPPLIED_CHANGES') from None
    return desired


def _prepared_bootstrap(db, root, manifest=None):
    """Resolve this creation's exact prepared predecessor and closed owner."""
    import warehouse_operations as core
    rows = db.execute('''SELECT p.operation_id FROM db_creation_phase_plans p
        JOIN db_creations c ON c.id=p.creation_id JOIN operations o ON o.id=p.operation_id
        JOIN operation_requests r ON r.operation_id=o.id
        WHERE c.id=? AND c.reservation_active=1 AND o.actor=c.actor AND r.actor=c.actor
        AND p.phase='bootstrap' AND p.purpose='APPLY' AND o.state='PREPARED' ''', (root['id'],)).fetchall()
    if len(rows) != 1:
        raise OperationError('INVALID_PREPARED_PREDECESSOR')
    prior = core._view(db, rows[0]['operation_id'], root['actor'])
    if prior['warehouse_id'] != root['warehouse_id']:
        raise OperationError('INVALID_PREPARED_PREDECESSOR')
    manifest = prior['manifest'] if manifest is None else manifest
    core.validate_prepared_handoff(prior, manifest)
    control = core._prepared_reservation(db, prior, manifest, root['actor'])
    if not db.execute('''SELECT 1 FROM db_creation_phase_plans
            WHERE creation_id=? AND operation_id=? AND purpose='APPLY' ''',
            (root['id'],control['operation_id'])).fetchone():
        raise OperationError('INVALID_PREPARED_PREDECESSOR')
    return prior


def guard_reservation(db, warehouse_id, *, creation_id=None):
    """Mandatory warehouse guard, independent of optional operator callbacks."""
    rows = db.execute('SELECT id FROM db_creations WHERE warehouse_id=? AND reservation_active=1',
                      (warehouse_id,)).fetchall()
    if any(row['id'] != creation_id for row in rows):
        raise OperationError('CREATION_BUSY')


def _gate(db, config, actor):
    import database_creation_auth
    database_creation_auth.current_authority(config,actor)
    db.execute('''INSERT INTO db_creation_principal_gates VALUES (?,?,'ACTIVE',1,NULL,NULL,?)
        ON CONFLICT(environment_id,principal_id) DO NOTHING''', (config.environment_id, actor, admission._now()))
    row = db.execute('SELECT * FROM db_creation_principal_gates WHERE environment_id=? AND principal_id=?',
                     (config.environment_id, actor)).fetchone()
    if row['state'] != 'ACTIVE':
        raise OperationError('PRINCIPAL_STOPPED', 403)
    return row


@contextmanager
def _transaction():
    import database_creation_auth
    with database_creation_auth.transaction() as db:
        yield db


def _saved(db, config, actor, route, key, payload):
    row = db.execute('''SELECT * FROM db_creation_requests
        WHERE environment_id=? AND actor=? AND route=? AND idempotency_key=?''',
        (config.environment_id, actor, route, key)).fetchone()
    if row:
        if row['payload'] != _json(payload):
            raise OperationError('IDEMPOTENCY_CONFLICT')
        return json.loads(row['ack'])


def _request(db, config, actor, route, key, payload, root, action, phase=None,
             *, plan=None, gate=None, op_revision=None, blocked=False, requested_sequence=None):
    request_id, now = str(uuid.uuid4()), admission._now()
    ack = dict(request_id=request_id, creation_id=root['id'] if root else None,
               action=action, phase=phase, accepted_revision=root['revision'] if root else 1)
    db.execute('''INSERT INTO db_creation_requests
        (id,environment_id,actor,route,idempotency_key,payload,creation_id,plan_id,action,ack,
         approval_state,dispatch_state,gate_revision,operation_revision,revision_cursor,stop_reason,created_at,updated_at,requested_sequence)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (request_id, config.environment_id, actor, route, key, _json(payload), root['id'] if root else None,
         plan['id'] if plan else None, action, _json(ack), 'VALID' if action in ('APPROVE','RECOVER_VERIFY') else None,
         'BLOCKED' if blocked else 'ACCEPTED', gate['revision'] if gate else None, op_revision, op_revision,
         'OPERATOR_UNAVAILABLE' if blocked else None, now, now, requested_sequence))
    return ack


def create(principal, role, payload, key):
    current_admin(principal, role)
    validate_key(key)
    intent = _intent(payload)
    config = enrollment()
    # Saved ACK remains observable even if the owner is currently unavailable.
    with _transaction() as db:
        import database_creation_auth
        database_creation_auth.current_authority(config,principal.principal_id)
        saved = _saved(db, config, principal.principal_id, COLLECTION, key, intent)
        if saved:
            return saved, False
    _readiness()  # No network or provider communication in the write transaction.
    with _transaction() as db:
        import database_creation_auth
        database_creation_auth.current_authority(config,principal.principal_id)
        saved = _saved(db, config, principal.principal_id, COLLECTION, key, intent)
        if saved:
            return saved, False
        _gate(db, config, principal.principal_id)
        _validate_current_grants(intent)
        guard_reservation(db, config.warehouse_id)
        control = admission._ensure(db, config.warehouse_id)
        if control['state'] != 'OPEN' or control['operation_id'] is not None:
            raise OperationError('CREATION_BUSY')
        if db.execute('''SELECT 1 FROM databases d JOIN catalog_bindings b ON b.database_id=d.id
                WHERE upper(d.state) IN ('REGISTERED','PREPARING') OR b.desired_revision!=b.applied_revision LIMIT 1''').fetchone():
            raise OperationError('UNAPPLIED_CHANGES')
        if grant_management.pending_desired(db):
            raise OperationError('UNAPPLIED_CHANGES')
        root_id, now = str(uuid.uuid4()), admission._now()
        registered = catalog_admin._create_database(db, principal.principal_id, intent['sql_catalog'],
            root_id+'/CREATE/1', _json(dict(sql_catalog=intent['sql_catalog'], profile_id='local-polaris-1.8.0')))
        database_id = registered['database_id']
        metadata = display_management._mutate(principal, role, 'database', database_id,
            dict(expected_revision=1, display_name=intent['display_name'], description=intent['description']),
            root_id+'/METADATA/1', connection=db)
        grants = {}
        for grant in intent['initial_grants']:
            result = grant_management._mutate(principal, role, database_id,
                dict(grant, expected_revision=0), root_id+'/GRANT/'+grant['principal_id']+'/1', connection=db)
            grants[grant['principal_id']] = result['id']
        db.execute('''INSERT INTO db_creations
            (id,environment_id,warehouse_id,actor,enrollment_id,enrollment_revision,database_id,
             create_operation_id,intent_json,child_ids_json,revision,reservation_active,
             terminal_outcome,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,1,1,NULL,?,?)''',
            (root_id, config.environment_id, config.warehouse_id, principal.principal_id, config.enrollment_id,
             config.revision, database_id, registered['operation_id'], _json(intent),
             _json(dict(metadata=metadata['operation_id'], grants=grants)), now, now))
        root = db.execute('SELECT * FROM db_creations WHERE id=?', (root_id,)).fetchone()
        _gate(db,config,principal.principal_id)
        return _request(db, config, principal.principal_id, COLLECTION, key, intent, root, 'CREATE'), True


def _root(db, config, root_id, actor=None):
    row = db.execute('SELECT * FROM db_creations WHERE id=? AND environment_id=?',
                     (root_id, config.environment_id)).fetchone()
    if row is None:
        raise OperationError('CREATION_NOT_FOUND', 404)
    if row['creation_mode'] != 'legacy':
        raise OperationError('CREATION_MODE_MISMATCH', 409)
    if actor is not None and row['actor'] != actor:
        raise OperationError('CREATION_OWNER_REQUIRED', 403)
    return row


def _projection_authority(config, actors):
    """Retain existing safety STOP writes without changing a durable gate."""
    import database_creation_auth as auth
    lost = set()
    for actor in actors:
        try:
            with auth.transaction():
                auth.current_authority(config, actor)
        except auth.AuthorityLost:
            lost.add(actor)
    return lost


def _projection_snapshot(config, roots):
    """Collect advisory availability once, with no caller SQLite connection held."""
    blocker = None
    try:
        sampler = getattr(_foundation, 'projection_probe', None)
        ready = sampler(config) if callable(sampler) else _foundation.probe(config)
        if type(ready) is not FoundationReadiness or ready.foundation_ready is not True:
            blocker = 'FOUNDATION_NOT_READY'
        elif ready.owner_ready is not True:
            blocker = 'OPERATOR_UNAVAILABLE'
    except Exception:
        blocker = 'OPERATOR_UNAVAILABLE'
    # Preserve the existing durable authority-loss stop, independently of health.
    lost = _projection_authority(config, {config.executor_id, *(r['actor'] for r in roots)})
    if config.executor_id in lost:
        blocker = 'PRINCIPAL_STOPPED'
    return blocker


def _stale_unstarted_registry(db, operation_id):
    """Only canonical all-PENDING work can expose registry drift as replan evidence."""
    import warehouse_operations as core
    row = db.execute('''SELECT g.manifest FROM operations o
        JOIN operation_requests r ON r.operation_id=o.id
        JOIN generation_plans g ON g.generation_id=r.generation_id
        WHERE o.id=? AND o.state='AWAITING_APPLY' ''', (operation_id,)).fetchone()
    if row is None:
        return False
    steps = list(db.execute('SELECT step,state FROM operation_steps WHERE operation_id=?', (operation_id,)))
    if (len(steps) != len(core._ALL_STEPS) or {s['step'] for s in steps} != set(core._ALL_STEPS)
            or any(s['state'] != 'PENDING' for s in steps)):
        return False
    manifest = json.loads(row['manifest'])
    try:
        core._check_registry(db, manifest['spec'])
    except core.OperationError as error:
        if error.code != 'STALE_REGISTRY':
            raise
        return True
    return False


def _summary(db, root, principal, availability=None):
    database = db.execute('SELECT * FROM databases WHERE id=?', (root['database_id'],)).fetchone()
    plans = []
    private_plans = list(db.execute("SELECT * FROM db_creation_phase_plans WHERE creation_id=? ORDER BY CASE phase WHEN 'bootstrap' THEN 0 ELSE 1 END,sequence,id", (root['id'],)))
    import database_creation_recovery as recovery
    import warehouse_operations as core
    latest_apply = next((p for p in reversed(private_plans) if p['purpose'] == 'APPLY'), None)
    latest_recovery = next((p for p in reversed(private_plans) if p['purpose'] == 'VERIFY_RECOVERY'), None)
    recovery_ready = (latest_recovery is not None and latest_apply is not None
        and recovery.projection_ready(db, root, latest_recovery, latest_apply))
    for plan in private_plans:
        operation = db.execute('SELECT state FROM operations WHERE id=?', (plan['operation_id'],)).fetchone()
        op = db.execute('SELECT revision FROM operation_requests WHERE operation_id=?', (plan['operation_id'],)).fetchone()
        plans.append(dict(plan_id=plan['id'], phase=plan['phase'], purpose=plan['purpose'], sequence=plan['sequence'],
            operation_id=plan['operation_id'], generation_id=plan['generation_id'], operation_revision=op[0],
            delivery_digest=plan['delivery_digest'], recovery_digest=plan['recovery_digest'], state=operation[0],
            steps=[dict(r) for r in db.execute('SELECT step,state FROM operation_steps WHERE operation_id=? ORDER BY rowid', (plan['operation_id'],))],
            public_summary=dict(code=('RECOVERY_READY' if recovery_ready and plan['id'] == latest_recovery['id']
                else 'RECONCILIATION_REQUIRED') if plan['purpose'] == 'VERIFY_RECOVERY' else
                    'RECONCILIATION_REQUIRED' if operation[0] == 'UNKNOWN' else 'PLAN_READY',
                impact='VERIFY_RECOVERY_LIMITED' if plan['purpose'] == 'VERIFY_RECOVERY' else
                    'BOOTSTRAP_MAINTENANCE' if plan['phase'] == 'bootstrap' else 'FULL_RUNTIME_HANDOFF',
                fixed_steps=['verify'] if plan['purpose'] == 'VERIFY_RECOVERY' else list(core._ALL_STEPS))))
    grants = [dict(r) for r in db.execute('''SELECT g.principal_id,g.maximum_role AS desired_role,e.maximum_role AS effective_role
        FROM grants g JOIN effective_grants e USING(principal_id,database_id) WHERE g.database_id=? ORDER BY g.principal_id''', (root['database_id'],))]
    access = 'platform_admin' if principal.platform_admin and principal.maximum_role == 'admin' else 'none'
    gates = {r['principal_id']:r['state'] for r in db.execute(
        'SELECT principal_id,state FROM db_creation_principal_gates WHERE environment_id=?', (root['environment_id'],))}
    stopped = gates.get(root['actor']) == 'STOPPED'
    executor_stopped = gates.get(enrollment().executor_id) == 'STOPPED'
    blocker = availability
    actions = []
    current = next((p for p in plans if latest_apply is not None and p['plan_id'] == latest_apply['id']), None)
    requests = list(db.execute('SELECT * FROM db_creation_requests WHERE creation_id=? ORDER BY rowid DESC', (root['id'],)))
    preparing = any(r['action'] in ('PREPARE_PLAN','REPLAN') and r['dispatch_state'] in ('ACCEPTED','BLOCKED','CLAIMED')
        and r['requested_sequence'] > max((p['sequence'] for p in private_plans
            if p['purpose'] == 'APPLY' and p['phase'] == json.loads(r['payload'])['phase']), default=0)
        and not any(p['preparation_request_id'] == r['id'] for p in private_plans) for r in requests)
    valid_approval = current is not None and current['state'] == 'AWAITING_APPLY' and any(r['plan_id'] == current['plan_id'] and r['approval_state'] == 'VALID'
        and r['dispatch_state'] in ('ACCEPTED','BLOCKED','CLAIMED') for r in requests)
    running_steps = [s for p in plans for s in p['steps'] if s['state'] == 'RUNNING']
    unknown = any(p['state'] == 'UNKNOWN' or any(s['state'] == 'UNKNOWN' for s in p['steps']) for p in plans)
    recovery_running = False
    if latest_recovery is not None:
        recovery_running = recovery.projection_running(db, root, latest_recovery)
    running = bool(running_steps) or current is not None and current['state'] in ('PREPARING','DRAINING','APPLYING','RUNNING')
    if not root['reservation_active']:
        progress = 'READY' if root['terminal_outcome'] == 'APPLIED' else 'STOPPED'
        blocker = None
    elif stopped:
        progress, blocker = 'STOPPED', 'PRINCIPAL_STOPPED'
    else:
        actions = ['withdraw'] if principal.principal_id == root['actor'] else []
        stale_registry = (availability is None and not executor_stopped and current is not None
            and not preparing and not running and not unknown and not recovery_running
            and _stale_unstarted_registry(db, current['operation_id']))
        if recovery_running or running:
            progress = 'VERIFYING' if recovery_running or any(s['step'] == 'verify' for s in running_steps) else 'APPLYING'
        elif unknown:
            progress = 'RECONCILE_REQUIRED'
            if availability is None and actions:
                actions = ['reconcile']
                if not executor_stopped and recovery_ready and not any(r['plan_id'] == latest_recovery['id'] and r['approval_state'] == 'VALID' for r in requests):
                    actions.append('recover-verify')
        elif preparing:
            progress = 'PREPARATION_PENDING'
        elif stale_registry:
            progress, blocker = 'REPLAN_REQUIRED', 'STALE_PLAN'
        elif valid_approval:
            progress = 'APPLYING'
        elif current is None:
            import database_creation_auth
            failed = next((r for r in requests if r['action'] in ('PREPARE_PLAN','REPLAN')),None)
            if failed is not None:
                progress, blocker = 'BLOCKED', None
                if database_creation_auth.known_preflight_failure(db,enrollment(),root,failed):
                    progress, blocker = 'REPLAN_REQUIRED', None
                    if actions:
                        actions.append('replan')
            else:
                progress = 'PREPARATION_PENDING'
                if actions:
                    actions.append('plans')
        elif current['state'] == 'AWAITING_APPLY':
            progress = 'PLAN_REVIEW_REQUIRED' if current['phase'] == 'bootstrap' else 'FULL_REVIEW_REQUIRED'
            if actions:
                actions.append('approve')
        elif current['phase'] == 'bootstrap' and current['state'] == 'PREPARED':
            progress = 'FULL_REVIEW_REQUIRED'
            if actions:
                actions.append('plans')
        else:
            progress = 'REPLAN_REQUIRED'
        # Match the existing POST replan guards, including VERIFIED_STALE.
        if current is not None and not preparing and (not valid_approval or stale_registry) and not running and not unknown and not recovery_running:
            full_grant_replan = False
            if current['phase'] == 'bootstrap' and current['state'] == 'PREPARED':
                try:
                    accepted = {g['principal_id']:g['maximum_role']
                        for g in json.loads(root['intent_json'])['initial_grants']}
                    if _replan_target_grants(db, root) != accepted:
                        _prepared_bootstrap(db, root)
                        full_grant_replan = True
                        actions = [a for a in actions if a != 'plans']
                except OperationError:
                    pass
            replan_denied = any(s['state'] == 'SUCCEEDED' and s['step'] != 'publish_snapshot'
                and p['state'] != 'VERIFIED_STALE' for p in plans if p['phase'] == current['phase'] for s in p['steps'])
            if current['phase'] == 'full' and not any(p['phase'] == 'bootstrap' and p['purpose'] == 'APPLY'
                    and p['state'] == 'PREPARED' for p in plans):
                replan_denied = True
            if current['phase'] == 'full' and not replan_denied:
                try:
                    _prepared_bootstrap(db, root)
                except OperationError:
                    replan_denied = True
            if (not replan_denied or full_grant_replan) and actions:
                actions.append('replan')
        if executor_stopped or availability is not None:
            actions = [a for a in actions if a == 'withdraw']
            if executor_stopped and availability is None and principal.principal_id == root['actor']:
                actions.append('reconcile')
            blocker = 'PRINCIPAL_STOPPED' if executor_stopped else availability
            if not running and not unknown and not recovery_running:
                progress = 'BLOCKED'
    return dict(creation_id=root['id'], database_id=root['database_id'], sql_catalog=database['sql_catalog'],
        display_name=database['display_name'], description=database['description'], requester_id=root['actor'],
        revision=root['revision'], lifecycle=database['state'], initial_grants=grants, plans=plans,
        blocker=blocker, progress_code=progress, next_actions=actions, access_source=access)


def get(principal, role, root_id):
    current_admin(principal, role)
    config = enrollment()
    with control_store.connect() as db:
        root = dict(_root(db, config, root_id))
    availability = _projection_snapshot(config, [root])
    with control_store.connect() as db:
        return _summary(db, _root(db, config, root_id), principal, availability)


def _name_id(db, environment_id, mode, name):
    try:
        dm.validate_sql_name(name)
    except (ValueError, TypeError):
        raise OperationError('INVALID_NAME', 400) from None
    rows = db.execute('''SELECT root.id FROM db_creations root
        JOIN databases d ON d.id=root.database_id
        WHERE root.environment_id=? AND root.creation_mode=? AND d.sql_catalog=? LIMIT 2''',
        (environment_id, mode, name)).fetchall()
    if len(rows) != 1:
        raise OperationError('DATABASE_NAME_AMBIGUOUS' if rows else 'DATABASE_NOT_FOUND',
                             409 if rows else 404)
    return rows[0]['id']


def get_by_name(principal, role, name):
    current_admin(principal, role)
    config = enrollment()
    with control_store.connect() as db:
        root_id = _name_id(db, config.environment_id, 'legacy', name)
    return get(principal, role, root_id)


def listing(principal, role, *, limit=50, cursor=None):
    current_admin(principal, role)
    config = enrollment()
    if type(limit) is not int or not 1 <= limit <= 100:
        raise OperationError('INVALID_REQUEST', 400)
    after = ('','')
    if cursor is not None:
        try:
            value = json.loads(base64.urlsafe_b64decode(cursor.encode('ascii')))
            if (type(value) is not list or len(value) != 4 or value[:2] != [config.environment_id, principal.principal_id]
                    or not all(isinstance(v,str) for v in value)):
                raise ValueError()
            after = tuple(value[2:])
        except (ValueError, TypeError, UnicodeError, binascii.Error):
            raise OperationError('INVALID_REQUEST', 400) from None
    with control_store.connect() as db:
        rows = db.execute('''SELECT * FROM db_creations WHERE environment_id=? AND creation_mode='legacy' AND (created_at,id)>(?,?)
            ORDER BY created_at,id LIMIT ?''', (config.environment_id, *after, limit+1)).fetchall()
    availability = _projection_snapshot(config, rows[:limit])
    with control_store.connect() as db:
        token = None
        if len(rows) > limit:
            last = rows[limit-1]
            token = base64.urlsafe_b64encode(_json([config.environment_id,principal.principal_id,last['created_at'],last['id']]).encode()).decode()
        return dict(creations=[_summary(db, _root(db, config, r['id']), principal, availability) for r in rows[:limit]], cursor=token)


def mutate(principal, role, root_id, action, payload, key, *, phase=None):
    return _mutate(principal, role, root_id, action, payload, key, phase=phase)[0]


def _mutate(principal, role, root_id, action, payload, key, *, phase=None):
    """Return the ACK and transaction-derived request insertion freshness."""
    current_admin(principal, role)
    config = enrollment()
    validate_key(key)
    fields = {'expected_revision'}
    if action in ('PREPARE_PLAN','REPLAN'):
        fields.add('phase')
        phase = payload.get('phase') if type(payload) is dict else None
    if action in ('APPROVE','RECOVER_VERIFY'):
        fields |= {'plan_id','operation_id','expected_operation_revision','delivery_digest'}
    if action == 'RECOVER_VERIFY':
        fields.add('recovery_digest')
    if (action not in ('PREPARE_PLAN','APPROVE','WITHDRAW','REPLAN','RECONCILE','RECOVER_VERIFY')
            or type(payload) is not dict or set(payload) != fields
            or type(payload.get('expected_revision')) is not int or payload['expected_revision'] < 1
            or phase is not None and phase not in ('bootstrap','full')):
        raise OperationError('INVALID_REQUEST', 400)
    if action in ('PREPARE_PLAN','REPLAN') and phase is None:
        raise OperationError('INVALID_REQUEST', 400)
    if action in ('APPROVE','RECOVER_VERIFY') and (phase not in ('bootstrap','full')
            or type(payload['expected_operation_revision']) is not int or payload['expected_operation_revision'] < 1
            or not all(_text(payload[k]) for k in fields - {'expected_revision','expected_operation_revision'})):
        raise OperationError('INVALID_REQUEST', 400)
    suffix = {'PREPARE_PLAN':'plans','WITHDRAW':'withdraw','REPLAN':'replan','RECONCILE':'reconcile'}.get(action)
    if suffix is None:
        suffix = 'phases/'+phase+('/approve' if action == 'APPROVE' else '/recover-verify')
    route = COLLECTION+'/'+root_id+'/'+suffix
    with _transaction() as db:
        import database_creation_auth
        database_creation_auth.current_authority(config,principal.principal_id)
        root = _root(db, config, root_id, principal.principal_id)
        saved = _saved(db, config, principal.principal_id, route, key, payload)
        if saved:
            return saved, False
        if not root['reservation_active']:
            raise OperationError('REVISION_CONFLICT')
        gate = _gate(db, config, principal.principal_id)
        if root['revision'] != payload['expected_revision']:
            raise OperationError('REVISION_CONFLICT')
        if root['enrollment_id'] != config.enrollment_id or root['enrollment_revision'] != config.revision:
            raise OperationError('STALE_PLAN')
        if phase == 'full' and action in ('PREPARE_PLAN','REPLAN') and not db.execute('''SELECT 1 FROM db_creation_phase_plans p
            JOIN operations o ON o.id=p.operation_id WHERE p.creation_id=? AND p.phase='bootstrap' AND p.purpose='APPLY' AND o.state='PREPARED' ''', (root_id,)).fetchone():
            raise OperationError('STALE_PLAN')
        if action == 'PREPARE_PLAN' and db.execute("SELECT 1 FROM db_creation_phase_plans WHERE creation_id=? AND phase=? AND purpose='APPLY' LIMIT 1", (root_id,phase)).fetchone():
            raise OperationError('RECONCILIATION_REQUIRED')
        if action in ('PREPARE_PLAN','REPLAN'):
            pending = list(db.execute("""SELECT r.* FROM db_creation_requests r
                WHERE r.creation_id=? AND r.action IN ('PREPARE_PLAN','REPLAN')
                AND NOT EXISTS (SELECT 1 FROM db_creation_phase_plans p WHERE p.preparation_request_id=r.id)
                ORDER BY r.rowid DESC""",(root_id,)))
            latest_preparation = db.execute('''SELECT id FROM db_creation_requests
                WHERE creation_id=? AND action IN ('PREPARE_PLAN','REPLAN') ORDER BY rowid DESC LIMIT 1''',
                (root_id,)).fetchone()
            for previous in pending:
                if previous['dispatch_state'] == 'COMPLETE' and previous['id'] != latest_preparation['id']:
                    continue  # A later admitted preparation already consumed this closure.
                # Keep unclaimed superseding semantics; never supersede an
                # uncertain claimed attempt or an untrusted terminal assertion.
                if previous['claim_id'] is not None or previous['dispatch_state'] in ('CLAIMED','COMPLETE','UNKNOWN'):
                    if (action != 'REPLAN' or not database_creation_auth.known_preflight_failure(db,config,root,previous)):
                        raise OperationError('RECONCILIATION_REQUIRED')
        if action == 'REPLAN' and db.execute('''SELECT 1 FROM db_creation_phase_plans p JOIN operation_steps s ON s.operation_id=p.operation_id
                WHERE p.creation_id=? AND s.state IN ('RUNNING','UNKNOWN')''', (root_id,)).fetchone():
            raise OperationError('RECONCILIATION_REQUIRED')
        if action == 'REPLAN' and db.execute('''SELECT 1 FROM db_creation_phase_plans p JOIN operation_steps s ON s.operation_id=p.operation_id
            JOIN operations o ON o.id=p.operation_id WHERE p.creation_id=? AND p.phase=?
            AND s.state='SUCCEEDED' AND s.step!='publish_snapshot' AND o.state!='VERIFIED_STALE' LIMIT 1''', (root_id,phase)).fetchone():
            raise OperationError('RECONCILIATION_REQUIRED')
        plan, op_revision = None, None
        if action in ('PREPARE_PLAN','REPLAN','APPROVE','RECOVER_VERIFY'):
            database_creation_auth.current_authority(config,config.executor_id)
            _gate(db, config, config.executor_id)
        if action in ('APPROVE','RECOVER_VERIFY'):
            plan = db.execute('SELECT * FROM db_creation_phase_plans WHERE id=? AND creation_id=?', (payload['plan_id'],root_id)).fetchone()
            if (plan is None or plan['phase'] != phase or plan['operation_id'] != payload['operation_id']
                    or plan['delivery_digest'] != payload['delivery_digest'] or not _text(plan['private_ref'])
                    or plan['purpose'] != ('VERIFY_RECOVERY' if action == 'RECOVER_VERIFY' else 'APPLY')
                    or action == 'RECOVER_VERIFY' and plan['recovery_digest'] != payload['recovery_digest']):
                raise OperationError('STALE_PLAN')
            latest = db.execute('SELECT max(sequence) FROM db_creation_phase_plans WHERE creation_id=? AND phase=? AND purpose=?',
                (root_id,phase,plan['purpose'])).fetchone()[0]
            if plan['sequence'] != latest:
                raise OperationError('STALE_PLAN')
            op = db.execute('''SELECT r.revision,o.state FROM operation_requests r JOIN operations o ON o.id=r.operation_id
                WHERE r.operation_id=?''', (plan['operation_id'],)).fetchone()
            if op is None or op['revision'] != payload['expected_operation_revision']:
                raise OperationError('STALE_PLAN')
            if op['state'] != ('UNKNOWN' if action=='RECOVER_VERIFY' else 'AWAITING_APPLY'):
                if action == 'APPROVE' and op['state'] in ('UNKNOWN','PREPARING','DRAINING','APPLYING'):
                    raise OperationError('RECONCILIATION_REQUIRED')
                raise OperationError('STALE_PLAN')
            if action == 'APPROVE' and _stale_unstarted_registry(db, plan['operation_id']):
                raise OperationError('STALE_PLAN')
            op_revision = op['revision']
            if plan['approval_request_id']:
                prior = db.execute('SELECT approval_state FROM db_creation_requests WHERE id=?', (plan['approval_request_id'],)).fetchone()
                if prior[0] == 'VALID':
                    raise OperationError('CREATION_BUSY')
        if action in ('WITHDRAW','REPLAN'):
            db.execute("UPDATE db_creation_requests SET approval_state='REVOKED',stop_reason=?,updated_at=? WHERE creation_id=? AND approval_state='VALID'", ('WITHDRAWN' if action=='WITHDRAW' else 'REPLANNED',admission._now(),root_id))
        db.execute('UPDATE db_creations SET revision=revision+1,updated_at=? WHERE id=?', (admission._now(),root_id))
        root = _root(db, config, root_id)
        requested_sequence = None
        if action in ('PREPARE_PLAN','REPLAN'):
            requested_sequence = db.execute('''SELECT coalesce(max(sequence),0)+1 FROM (
                SELECT sequence FROM db_creation_phase_plans WHERE creation_id=? AND phase=? AND purpose='APPLY'
                UNION ALL SELECT requested_sequence FROM db_creation_requests WHERE creation_id=?
                AND action IN ('PREPARE_PLAN','REPLAN') AND json_extract(payload,'$.phase')=?)''',
                (root_id,phase,root_id,phase)).fetchone()[0]
        ack = _request(db, config, principal.principal_id, route, key, payload, root, action, phase,
            plan=plan, gate=gate if plan or action in ('PREPARE_PLAN','REPLAN') else None, op_revision=op_revision, blocked=action != 'WITHDRAW',
            requested_sequence=requested_sequence)
        if plan:
            action_id = str(uuid.uuid4())
            db.execute("INSERT INTO warehouse_action_requests VALUES (?,?,?,?,?,?,?,'REQUESTED')", (action_id,
                plan['operation_id'],principal.principal_id,route,key,_json(payload),admission._now()))
            db.execute('UPDATE db_creation_requests SET action_request_id=? WHERE id=?', (action_id,ack['request_id']))
            db.execute('UPDATE db_creation_phase_plans SET approval_request_id=? WHERE id=?', (ack['request_id'],plan['id']))
        return ack, True


def _attach_plan(db, config, creation_id, operation_id, *, private_ref, public_summary, purpose='APPLY', recovery_digest=None,
                 requested_sequence=None, preparation_request_id=None):
    """Private preparation boundary. Caller created the Operation in this transaction.

    There is deliberately no public arbitrary-Operation attach route.
    """
    if not db.in_transaction or type(config) is not Enrollment or config != enrollment() or not _text(private_ref):
        raise OperationError('STALE_PLAN')
    root = _root(db, config, creation_id)
    if not root['reservation_active'] or root['warehouse_id'] != config.warehouse_id:
        raise OperationError('STALE_PLAN')
    guard_reservation(db, config.warehouse_id, creation_id=creation_id)
    op = db.execute('''SELECT r.*,o.actor FROM operation_requests r JOIN operations o ON o.id=r.operation_id
        WHERE r.operation_id=?''', (operation_id,)).fetchone()
    if (op is None or op['actor'] != root['actor'] or purpose not in ('APPLY','VERIFY_RECOVERY')
            or purpose == 'APPLY' and op['attempt_id'] is not None):
        raise OperationError('STALE_PLAN')
    public = db.execute('SELECT delivery_digest,manifest FROM generation_plans WHERE generation_id=?', (op['generation_id'],)).fetchone()
    spec = json.loads(public['manifest'])['spec']
    if root['database_id'] not in spec['databases'] or spec['warehouse_id'] != config.warehouse_id:
        raise OperationError('STALE_PLAN')
    if op['phase'] == 'full' and not db.execute('''SELECT 1 FROM db_creation_phase_plans p JOIN operations o ON o.id=p.operation_id
        WHERE p.creation_id=? AND p.operation_id=? AND p.phase='bootstrap' AND p.purpose='APPLY' AND o.state='PREPARED' ''',
        (creation_id,op['predecessor_id'])).fetchone():
        raise OperationError('STALE_PLAN')
    if purpose == 'VERIFY_RECOVERY' and (not _text(recovery_digest) or not db.execute(
            "SELECT 1 FROM db_creation_phase_plans WHERE creation_id=? AND operation_id=? AND purpose='APPLY'", (creation_id,operation_id)).fetchone()):
        raise OperationError('STALE_PLAN')
    sequence = requested_sequence or db.execute('SELECT coalesce(max(sequence),0)+1 FROM db_creation_phase_plans WHERE creation_id=? AND phase=? AND purpose=?', (creation_id,op['phase'],purpose)).fetchone()[0]
    plan_id = str(uuid.uuid4())
    db.execute('''INSERT INTO db_creation_phase_plans
        (id,creation_id,phase,purpose,sequence,operation_id,generation_id,predecessor_operation_id,
         delivery_digest,recovery_digest,private_ref,public_summary,preparation_request_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (plan_id,creation_id,op['phase'],purpose,sequence,operation_id,op['generation_id'],op['predecessor_id'],
         public['delivery_digest'],recovery_digest,private_ref,_json(public_summary),preparation_request_id))
    db.execute('UPDATE db_creations SET revision=revision+1,updated_at=? WHERE id=?', (admission._now(),creation_id))
    return plan_id


def _prepare_linked_plan(config, creation_id, bundle, secret_observations, *, private_ref,
                         public_summary, preparation_request_id, phase='bootstrap', predecessor_id=None,
                         preparation_fence=None, final_fence=None):
    """Future private preparation worker: generation+Operation+association commit together."""
    import warehouse_operations
    if type(config) is not Enrollment or config != enrollment():
        raise OperationError('STALE_PLAN')
    with _transaction() as db:
        if preparation_fence is not None:
            import database_creation_auth
            database_creation_auth.preparation_guard(db, preparation_fence)
            if preparation_fence.request_id != preparation_request_id:
                raise OperationError('STALE_PLAN')
        if final_fence is not None:
            final_fence(db)
        root = _root(db,config,creation_id)
        _gate(db,config,root['actor'])
        request = db.execute('SELECT * FROM db_creation_requests WHERE id=?', (preparation_request_id,)).fetchone()
        if (request is None or request['creation_id'] != creation_id or request['actor'] != root['actor']
                or request['environment_id'] != config.environment_id or request['action'] not in ('PREPARE_PLAN','REPLAN')
                or json.loads(request['payload'])['phase'] != phase or request['requested_sequence'] is None):
            raise OperationError('STALE_PLAN')
        saved = db.execute('SELECT id,operation_id FROM db_creation_phase_plans WHERE preparation_request_id=?', (preparation_request_id,)).fetchone()
        if saved:
            return dict(plan_id=saved['id'],operation_id=saved['operation_id'])
        if root['revision'] != json.loads(request['ack'])['accepted_revision']:
            raise OperationError('STALE_PLAN')
        if request['action'] == 'PREPARE_PLAN' and db.execute("SELECT 1 FROM db_creation_phase_plans WHERE creation_id=? AND phase=? AND purpose='APPLY' LIMIT 1", (creation_id,phase)).fetchone():
            raise OperationError('RECONCILIATION_REQUIRED')
        if db.execute('''SELECT 1 FROM db_creation_phase_plans p JOIN operation_steps s ON s.operation_id=p.operation_id
            WHERE p.creation_id=? AND s.state IN ('RUNNING','UNKNOWN') LIMIT 1''', (creation_id,)).fetchone():
            raise OperationError('RECONCILIATION_REQUIRED')
        if db.execute('''SELECT 1 FROM db_creation_phase_plans p JOIN operation_steps s ON s.operation_id=p.operation_id
            JOIN operations o ON o.id=p.operation_id WHERE p.creation_id=? AND p.phase=?
            AND s.state='SUCCEEDED' AND s.step!='publish_snapshot' AND o.state!='VERIFIED_STALE' LIMIT 1''', (creation_id,phase)).fetchone():
            raise OperationError('RECONCILIATION_REQUIRED')
        sequence = request['requested_sequence']
        operation = warehouse_operations._create_plan(root['actor'], 'private/P5/'+creation_id+'/'+phase,
            creation_id+'/'+phase+'/'+str(sequence),bundle,secret_observations,phase=phase,
            predecessor_id=predecessor_id,connection=db,creation_id=creation_id)
        plan_id = _attach_plan(db,config,creation_id,operation['id'],private_ref=private_ref,public_summary=public_summary,
            requested_sequence=sequence,preparation_request_id=preparation_request_id)
        db.execute("UPDATE db_creation_requests SET dispatch_state='COMPLETE',stop_reason=NULL,updated_at=? WHERE id=?", (admission._now(),preparation_request_id))
        return dict(plan_id=plan_id,operation_id=operation['id'])


def _check_environment(headers):
    """Optional expected environment, never a caller-selected target."""
    values = headers.get_all('X-Shirokuma-Environment', [])
    if not values:
        return
    if len(values) != 1 or re.fullmatch('[A-Za-z0-9_-]{1,128}', values[0]) is None:
        raise OperationError('INVALID_REQUEST', 400)
    if type(_enrollment) is not Enrollment:
        raise OperationError('OPERATOR_UNAVAILABLE', 503)
    if values[0] != _enrollment.environment_id:
        raise OperationError('ENVIRONMENT_MISMATCH', 409)


def dispatch(handler, method):
    """Strict normal HTTP routes. GET never prepares, claims or dispatches work."""
    parsed = urlsplit(handler.path)
    if parsed.path != COLLECTION and not parsed.path.startswith(COLLECTION+'/'):
        return False
    import logical_creations
    logical = logical_creations.enabled()
    coordinator = logical_creations if logical else None
    status = 200
    try:
        principal = request_auth.authenticate_principal(handler.headers)
        roles = handler.headers.get_all('X-Shirokuma-Role', [])
        if len(roles) != 1 or roles[0] not in request_auth.ROLES:
            raise OperationError('INVALID_REQUEST', 400)
        current_admin(principal, roles[0])
        if parsed.fragment or handler.headers.get('Transfer-Encoding'):
            raise OperationError('INVALID_REQUEST', 400)
        tail = parsed.path[len(COLLECTION):]
        if method == 'GET':
            if handler.headers.get_all('Content-Length', []) not in ([],['0']):
                raise OperationError('INVALID_REQUEST', 400)
            if tail == '':
                query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
                if set(query)-{'limit','cursor'} or any(len(v)!=1 for v in query.values()):
                    raise OperationError('INVALID_REQUEST', 400)
                value = query.get('limit',['50'])[0]
                if not re.fullmatch('[0-9]+',value):
                    raise OperationError('INVALID_REQUEST', 400)
                result = (coordinator.listing if logical else listing)(principal,roles[0],limit=int(value),cursor=query.get('cursor',[None])[0])
            elif parsed.query:
                raise OperationError('INVALID_REQUEST', 400)
            elif tail == '/capabilities':
                result = (coordinator.capabilities if logical else capabilities)(principal,roles[0])
            elif tail == '/principals':
                result = principals(principal,roles[0])
            elif tail == '/runtime-instance':
                if logical:
                    c = coordinator.config()
                    result = dict(creation_mode='logical_v1', environment_id=c['environment_id'], warehouse_id=c['warehouse_id'])
                else:
                    import database_creation_bridge
                    result = database_creation_bridge.runtime_instance()
            elif tail.startswith('/by-name/'):
                encoded = tail[len('/by-name/'):]
                if re.search(r'%(?![0-9A-Fa-f]{2})', encoded):
                    raise OperationError('INVALID_NAME', 400)
                try:
                    name = unquote(encoded, encoding='utf-8', errors='strict')
                except UnicodeError:
                    raise OperationError('INVALID_NAME', 400) from None
                result = (coordinator.get_by_name if logical else get_by_name)(principal, roles[0], name)
            elif re.fullmatch('/[A-Za-z0-9_-]{1,128}',tail):
                result = (coordinator.get if logical else get)(principal,roles[0],tail[1:])
            else:
                raise OperationError('CREATION_NOT_FOUND',404)
        elif method == 'POST':
            if parsed.query:
                raise OperationError('INVALID_REQUEST',400)
            import workbench_server
            if len(handler.headers.get_all('Origin',[])) > 1:
                raise OperationError('INVALID_REQUEST',400)
            if handler.headers.get('Origin') and not workbench_server.request_origin_allowed(handler.headers):
                raise OperationError('INVALID_ORIGIN',403)
            (coordinator.check_environment if logical else _check_environment)(handler.headers)
            keys = handler.headers.get_all('Idempotency-Key',[])
            if len(keys)!=1:
                raise OperationError('INVALID_REQUEST',400)
            validate_key(keys[0])
            body = handler.database_body()
            if tail == '':
                result, fresh = (coordinator.create if logical else create)(principal,roles[0],body,keys[0])
                status = (202 if logical else 201) if fresh else 200
            else:
                match = re.fullmatch('/([A-Za-z0-9_-]{1,128})/(plans|withdraw|replan|reconcile|resume|phases/(bootstrap|full)/(approve|recover-verify))',tail)
                if not match or match[4] == 'recover-verify' and match[3] != 'full':
                    raise OperationError('CREATION_NOT_FOUND',404)
                with control_store.connect() as db:
                    persisted = db.execute('SELECT creation_mode FROM db_creations WHERE id=?', (match[1],)).fetchone()
                if persisted and persisted[0] == 'logical_v1' and not logical:
                    raise OperationError('CREATION_MODE_MISMATCH', 409)
                if match[2] == 'resume' and not logical:
                    raise OperationError('CREATION_NOT_FOUND',404)
                action = {'plans':'PREPARE_PLAN','withdraw':'WITHDRAW','replan':'REPLAN','reconcile':'RECONCILE','resume':'RESUME'}.get(match[2],
                    'APPROVE' if match[4]=='approve' else 'RECOVER_VERIFY')
                if logical:
                    with control_store.connect() as db:
                        coordinator._root(db, match[1])
                    result, fresh = coordinator.mutate(principal,roles[0],match[1],action,body,keys[0])
                else:
                    result, fresh = _mutate(principal,roles[0],match[1],action,body,keys[0],phase=match[3])
                status = 202
            # Admission returns after its transaction commits. A failed ACK
            # never rolls back registration or substitutes another request ID.
            import database_creation_bridge
            if fresh and result.get('request_id') and (tail == '' or action != 'WITHDRAW'):
                try:
                    if logical:
                        coordinator.dispatch_async(result['request_id'])
                    else:
                        database_creation_bridge.dispatch(result['request_id'],
                            observe=tail != '' and action == 'RECONCILE')
                except Exception:
                    pass  # durable BLOCKED request remains discoverable by GET
        else:
            raise OperationError('INVALID_REQUEST',400)
    except Exception as error:
        if isinstance(error,request_auth.AuthenticationError):
            status,code = 401,'UNAUTHENTICATED'
        elif isinstance(error,OperationError):
            status,code = error.status,error.code
        elif isinstance(error,PermissionError):
            status,code = 403,'ADMIN_REQUIRED'
        elif isinstance(error,(ValueError,TypeError,UnicodeError)):
            status,code = 400,'INVALID_REQUEST'
        else:
            status,code = 503,'OPERATOR_UNAVAILABLE'
        result = dict(error=code,message='Database creation request rejected')
    if status in (201,202) and result.get('creation_id'):
        handler._creation_location = COLLECTION+'/'+result['creation_id']
    handler.respond(result,status)
    return True
