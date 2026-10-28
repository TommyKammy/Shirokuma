"""Private typed P5 delegation and principal stop gates.

This module accepts no HTTP payloads or browser sessions. Permits are resolved
from durable approval, a separately authenticated executor and private enrollment.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
import uuid

import control_store
import database_creations as creations
import request_auth
import warehouse_admission as admission
from warehouse_operations import OperationError, _json, _text


@dataclass(frozen=True)
class CreationPermit:
    environment_id: str
    warehouse_id: str
    enrollment_id: str
    enrollment_revision: int
    principal_gate_revision: int
    creation_id: str
    database_id: str
    phase: str
    plan_id: str
    approval_request_id: str
    operation_id: str
    approval_operation_revision: int
    delivery_digest: str
    candidate_binding: str
    requester_id: str
    executor_id: str
    owner_id: str
    claim_id: str
    executor_gate_revision: int


@dataclass(frozen=True)
class PreparationFence:
    config: creations.Enrollment
    executor: request_auth.AuthenticatedPrincipal
    request_id: str
    owner_id: str
    claim_id: str
    gate_revision: int
    executor_gate_revision: int
    initial_claim: bool = False


def preparation_guard(db, fence):
    """Private preparation authority, independent of an execution approval."""
    if type(fence) is not PreparationFence or fence.config != creations.enrollment():
        raise OperationError('STALE_PLAN')
    config = fence.config
    managed_fence(config)
    current_authority(config, config.executor_id)
    _executor(config, fence.executor)
    executor_gate = creations._gate(db, config, config.executor_id)
    request = db.execute('SELECT * FROM db_creation_requests WHERE id=?', (fence.request_id,)).fetchone()
    if request is None or request['action'] not in ('PREPARE_PLAN', 'REPLAN'):
        raise OperationError('STALE_PLAN')
    root = creations._root(db, config, request['creation_id'])
    gate = creations._gate(db, config, root['actor'])
    if (request['environment_id'] != config.environment_id or request['actor'] != root['actor']
            or root['enrollment_id'] != config.enrollment_id or root['enrollment_revision'] != config.revision
            or root['warehouse_id'] != config.warehouse_id or not root['reservation_active']
            or gate['revision'] != fence.gate_revision or request['gate_revision'] != fence.gate_revision
            or executor_gate['revision'] != fence.executor_gate_revision
            or not fence.claim_id.startswith('preparation:'+str(fence.executor_gate_revision)+':')
            or request['claim_id'] != fence.claim_id
            or request['owner_id'] != fence.owner_id or request['executor'] != config.executor_id
            or request['dispatch_state'] != 'CLAIMED'
            or root['revision'] != json.loads(request['ack'])['accepted_revision']):
        raise OperationError('STALE_PLAN')
    creations.guard_reservation(db, config.warehouse_id, creation_id=root['id'])
    return root, request


def claim_preparation(config, executor, request_id, *, owner_id):
    if not _text(owner_id) or config != creations.enrollment():
        raise OperationError('STALE_PLAN')
    with transaction() as db:
        current_authority(config, config.executor_id)
        _executor(config, executor)
        executor_gate = creations._gate(db, config, config.executor_id)
        request = db.execute('SELECT * FROM db_creation_requests WHERE id=?', (request_id,)).fetchone()
        if request is None or request['action'] not in ('PREPARE_PLAN', 'REPLAN'):
            raise OperationError('STALE_PLAN')
        root = creations._root(db, config, request['creation_id'])
        if (request['environment_id'] != config.environment_id or request['actor'] != root['actor']
                or root['warehouse_id'] != config.warehouse_id or root['enrollment_id'] != config.enrollment_id
                or root['enrollment_revision'] != config.revision):
            raise OperationError('STALE_PLAN')
        gate = creations._gate(db, config, root['actor'])
        # The private claim carries the executor epoch without changing the
        # immutable requester's gate_revision or the canonical schema.
        prefix = 'preparation:'+str(executor_gate['revision'])+':'
        claim_id = request['claim_id'] or prefix+str(uuid.uuid4())
        if not claim_id.startswith(prefix):
            raise OperationError('STALE_PLAN')
        if request['claim_id'] and (request['owner_id'] != owner_id or request['executor'] != config.executor_id):
            raise OperationError('CREATION_BUSY')
        if request['dispatch_state'] == 'COMPLETE':
            return None
        if request['gate_revision'] != gate['revision']:
            raise OperationError('STALE_PLAN')
        db.execute("""UPDATE db_creation_requests SET dispatch_state='CLAIMED',executor=?,owner_id=?,claim_id=?,
            updated_at=? WHERE id=?""", (config.executor_id,owner_id,claim_id,
                admission._now(),request_id))
        fence = PreparationFence(config,executor,request_id,owner_id,claim_id,gate['revision'],executor_gate['revision'],
            initial_claim=request['claim_id'] is None)
        preparation_guard(db, fence)
        return fence


def _preflight_receipt_path(request_id):
    # The canonical store is private enrollment storage, never a client path.
    if str(uuid.UUID(request_id)) != request_id:
        raise OperationError('RECONCILIATION_REQUIRED')
    return control_store.root().resolve()/'database-creation-preflight-failures'/(request_id+'.json')


def _preflight_unstarted(db, root, request, directory):
    from pathlib import Path
    database = db.execute('SELECT state FROM databases WHERE id=?',(root['database_id'],)).fetchone()
    binding = db.execute('SELECT applied_revision FROM catalog_bindings WHERE database_id=?',
        (root['database_id'],)).fetchone()
    control = db.execute('SELECT state,operation_id FROM warehouse_controls WHERE warehouse_id=?',
        (root['warehouse_id'],)).fetchone()
    if (request['action'] != 'PREPARE_PLAN' or request['requested_sequence'] != 1
            or json.loads(request['payload'])['phase'] != 'bootstrap' or request['plan_id'] is not None
            or root['terminal_outcome'] is not None or not root['reservation_active']
            or database['state'].upper() != 'REGISTERED' or binding['applied_revision'] != 0
            or control['state'] != 'OPEN' or control['operation_id'] is not None
            or db.execute('SELECT 1 FROM db_creation_phase_plans WHERE creation_id=?',(root['id'],)).fetchone()
            or Path(directory).exists() or Path(directory).is_symlink()):
        raise OperationError('RECONCILIATION_REQUIRED')


def _preflight_receipt(root, request, fence, directory):
    return dict(version=1, error='UNAPPLIED_CHANGES', request_id=request['id'],
        creation_id=root['id'], database_id=root['database_id'], environment_id=root['environment_id'],
        warehouse_id=root['warehouse_id'], enrollment_id=root['enrollment_id'],
        enrollment_revision=root['enrollment_revision'], phase='bootstrap', sequence=1,
        accepted_revision=json.loads(request['ack'])['accepted_revision'],
        requester=root['actor'], executor=request['executor'], owner_id=request['owner_id'],
        claim_id=request['claim_id'], gate_revision=request['gate_revision'],
        executor_gate_revision=fence.executor_gate_revision, payload=request['payload'], ack=request['ack'],
        directory=str(directory))


def known_preflight_failure(db, config, root, request):
    """Positive private proof only; historical CLAIMED work cannot qualify."""
    from warehouse_operator import private_file, canonical, digest
    try:
        if (request['dispatch_state'] != 'COMPLETE' or request['stop_reason'] != 'UNAPPLIED_CHANGES'
                or request['private_receipt_ref'] is None):
            return False
        receipt = json.loads(private_file(_preflight_receipt_path(request['id'])))
        if request['private_receipt_ref'] != 'preflight-failure:'+digest(canonical(receipt)):
            return False
        executor_gate = db.execute('SELECT * FROM db_creation_principal_gates WHERE environment_id=? AND principal_id=?',
            (config.environment_id,config.executor_id)).fetchone()
        gate = db.execute('SELECT * FROM db_creation_principal_gates WHERE environment_id=? AND principal_id=?',
            (config.environment_id,root['actor'])).fetchone()
        if (executor_gate is None or gate is None or executor_gate['state'] != 'ACTIVE' or gate['state'] != 'ACTIVE'
                or gate['revision'] != request['gate_revision'] or not request['claim_id']
                or not request['claim_id'].startswith('preparation:'+str(executor_gate['revision'])+':')
                or request['executor'] != config.executor_id or not _text(request['owner_id'])
                or root['enrollment_id'] != config.enrollment_id or root['enrollment_revision'] != config.revision
                or root['environment_id'] != config.environment_id or root['warehouse_id'] != config.warehouse_id
                or root['revision'] != json.loads(request['ack'])['accepted_revision']+1):
            return False
        current_authority(config,root['actor'])
        current_authority(config,config.executor_id)
        managed_fence(config)
        fence = PreparationFence(config,None,request['id'],request['owner_id'],request['claim_id'],
            gate['revision'],executor_gate['revision'])
        expected = _preflight_receipt(root,request,fence,receipt['directory'])
        _preflight_unstarted(db,root,request,receipt['directory'])
        return receipt == expected and request['private_receipt_ref'] == 'preflight-failure:'+digest(canonical(receipt))
    except (OSError,ValueError,KeyError,TypeError,OperationError):
        return False


def complete_preflight_failure(fence, error, directory):
    """Called only by the initial pre-journal preflight's concrete error handler."""
    from warehouse_operator import private_file, canonical, digest
    import os
    if (type(fence) is not PreparationFence or not fence.initial_claim
            or type(error) is not OperationError or error.code != 'UNAPPLIED_CHANGES'):
        raise OperationError('RECONCILIATION_REQUIRED')
    with transaction() as db:
        request = db.execute('SELECT * FROM db_creation_requests WHERE id=?',(fence.request_id,)).fetchone()
        if request is not None and request['dispatch_state'] == 'COMPLETE':
            root = creations._root(db,fence.config,request['creation_id'])
            if (request['claim_id'] == fence.claim_id and request['owner_id'] == fence.owner_id
                    and request['gate_revision'] == fence.gate_revision
                    and known_preflight_failure(db,fence.config,root,request)):
                _executor(fence.config,fence.executor)
                return
            raise OperationError('STALE_PLAN')
        root, request = preparation_guard(db,fence)
        if request['private_receipt_ref'] is not None:
            raise OperationError('RECONCILIATION_REQUIRED')
        _preflight_unstarted(db,root,request,directory)
        receipt = _preflight_receipt(root,request,fence,directory)
        path = _preflight_receipt_path(request['id'])
        path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        if path.parent.stat().st_mode & 0o777 != 0o700:
            raise OperationError('RECONCILIATION_REQUIRED')
        data = canonical(receipt)
        # Receipt first, DB second. A lost DB commit may reuse only identical
        # immutable evidence. Partial or different files always block recovery.
        try:
            with open(path,'xb',opener=lambda p,f:os.open(p,f,0o600)) as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
        except FileExistsError:
            if private_file(path) != data:
                raise OperationError('RECONCILIATION_REQUIRED') from None
        db.execute("""UPDATE db_creation_requests SET dispatch_state='COMPLETE',stop_reason='UNAPPLIED_CHANGES',
            private_receipt_ref=?,updated_at=? WHERE id=?""",
            ('preflight-failure:'+digest(data),admission._now(),request['id']))
        db.execute('UPDATE db_creations SET revision=revision+1,updated_at=? WHERE id=? AND revision=?',
            (admission._now(),root['id'],root['revision']))


_permit = ContextVar('database_creation_permit', default=None)


class AuthorityLost(OperationError):
    def __init__(self, config, target, reason='AUTHORITY_LOST'):
        self.config, self.target = config, target
        self.reason = reason
        super().__init__('ADMIN_REQUIRED',403)


@contextmanager
def transaction():
    try:
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            yield db
    except AuthorityLost as error:
        # Roll back every caller write, then durably stop in a fresh transaction.
        stop_lost_authority(error.config,error.target,reason=error.reason)
        raise


def current_authority(config, actor):
    if not creations._admin_account(_accounts().get(actor)):
        raise AuthorityLost(config,actor)


def managed_fence(config):
    require = getattr(creations._foundation,'require',None)
    if callable(require):
        try:
            require()
        except Exception:
            raise AuthorityLost(config,config.executor_id,'MANAGED_IDENTITY_LOST') from None


def _accounts():
    try:
        return request_auth._principal_accounts()
    except request_auth.AuthenticationError:
        raise OperationError('AUTHORIZATION_UNAVAILABLE',503) from None


def _executor_credentials(config, principal):
    if (type(config) is not creations.Enrollment or type(principal) is not request_auth.AuthenticatedPrincipal
            or principal.principal_id != config.executor_id or principal.maximum_role != 'admin'
            or principal.platform_admin is not True):
        raise OperationError('ADMIN_REQUIRED',403)


def _executor(config, principal):
    _executor_credentials(config, principal)
    if not creations._admin_account(_accounts().get(principal.principal_id)):
        raise OperationError('ADMIN_REQUIRED',403)


def _stop(db, config, target, key, reason, *, actor=None):
    route = 'private/STOP_PRINCIPAL/'+target
    payload = dict(principal_id=target,reason=reason)
    actor = actor or config.executor_id
    saved = creations._saved(db,config,actor,route,key,payload)
    if saved:
        return saved
    ack = creations._request(db,config,actor,route,key,payload,None,'STOP_PRINCIPAL')
    db.execute('''INSERT INTO db_creation_principal_gates VALUES (?,?,'STOPPED',1,?,?,?)
        ON CONFLICT(environment_id,principal_id) DO UPDATE SET state='STOPPED',revision=revision+1,
        reason_code=excluded.reason_code,request_id=excluded.request_id,updated_at=excluded.updated_at''',
        (config.environment_id,target,reason,ack['request_id'],admission._now()))
    # Pending approvals have no executor column yet: their enrollment binds them
    # to the dedicated executor just as firmly as an already claimed approval.
    affected = '''SELECT id FROM db_creations WHERE environment_id=? AND
        (actor=? OR (?=? AND enrollment_id=? AND enrollment_revision=? AND warehouse_id=?))'''
    bindings = (config.environment_id,target,target,config.executor_id,
        config.enrollment_id,config.revision,config.warehouse_id)
    db.execute('''UPDATE db_creations SET revision=revision+1,updated_at=?
        WHERE reservation_active=1 AND id IN ('''+affected+''') AND
        (actor=? OR id IN (SELECT creation_id FROM db_creation_requests WHERE approval_state='VALID'))''',
        (admission._now(),*bindings,target))
    db.execute("""UPDATE db_creation_requests SET approval_state='REVOKED',stop_reason=?,updated_at=?
        WHERE environment_id=? AND approval_state='VALID' AND (actor=? OR creation_id IN ("""+affected+'))',
        (reason,admission._now(),config.environment_id,target,*bindings))
    return ack


def stop_principal(config, executor, target, key, *, reason='PRIVATE_STOP'):
    _executor(config,executor)
    creations.validate_key(key)
    if not _text(target) or not _text(reason):
        raise OperationError('INVALID_REQUEST',400)
    with transaction() as db:
        _executor(config,executor)
        return _stop(db,config,target,key,reason,actor=executor.principal_id)


def stop_lost_authority(config, target, *, reason='AUTHORITY_LOST'):
    """Detection path commits denial bookkeeping before its caller raises."""
    with transaction() as db:
        gate = db.execute('SELECT state FROM db_creation_principal_gates WHERE environment_id=? AND principal_id=?',
            (config.environment_id,target)).fetchone()
        if gate is None or gate[0] != 'STOPPED':
            _stop(db,config,target,str(uuid.uuid4()),reason)


def release_principal(config, executor, target, key, *, observation=None):
    """Explicit release requires current authority and no unclassified effects."""
    _executor(config,executor)
    creations.validate_key(key)
    if not creations._admin_account(_accounts().get(target)):
        raise OperationError('ADMIN_REQUIRED',403)
    payload, route = dict(principal_id=target),'private/RELEASE_PRINCIPAL/'+target
    with transaction() as db:
        _executor(config,executor)
        current_authority(config,target)
        saved = creations._saved(db,config,executor.principal_id,route,key,payload)
        if saved:
            return saved
        if observation is not None:
            import database_creation_recovery as recovery
            recovery.validate_release_observation(db,config,target,observation)
        uncertain = db.execute('''SELECT DISTINCT p.operation_id FROM db_creations c JOIN db_creation_phase_plans p ON p.creation_id=c.id
            JOIN operations o ON o.id=p.operation_id
            LEFT JOIN operation_steps s ON s.operation_id=p.operation_id WHERE c.environment_id=?
            AND (c.actor=? OR (?=? AND c.enrollment_id=? AND c.enrollment_revision=? AND c.warehouse_id=?))
            AND (s.state IN ('RUNNING','UNKNOWN') OR o.state='UNKNOWN')''', (config.environment_id,target,target,
                config.executor_id,config.enrollment_id,config.revision,config.warehouse_id)).fetchall()
        import database_creation_recovery as recovery
        if any(not recovery.release_safe(db,config,row['operation_id'],observation=observation) for row in uncertain):
            raise OperationError('RECONCILIATION_REQUIRED')
        gate = db.execute('SELECT * FROM db_creation_principal_gates WHERE environment_id=? AND principal_id=?',
            (config.environment_id,target)).fetchone()
        if gate is None or gate['state'] != 'STOPPED':
            raise OperationError('REVISION_CONFLICT')
        ack = creations._request(db,config,executor.principal_id,route,key,payload,None,'RELEASE_PRINCIPAL')
        db.execute("""UPDATE db_creation_principal_gates SET state='ACTIVE',revision=revision+1,
            reason_code=NULL,request_id=?,updated_at=? WHERE environment_id=? AND principal_id=?""",
            (ack['request_id'],admission._now(),config.environment_id,target))
        return ack


def claim(config, executor, request_id, *, owner_id):
    _executor_credentials(config,executor)
    if config != creations.enrollment() or not _text(owner_id):
        raise OperationError('STALE_PLAN')
    # Recheck current requester outside the write transaction so authority-loss
    # STOP cannot be rolled back by the rejected dispatch.
    with control_store.connect() as db:
        request = db.execute('SELECT actor,claim_id FROM db_creation_requests WHERE id=?', (request_id,)).fetchone()
    if request is None:
        raise OperationError('CREATION_NOT_FOUND',404)
    # Once claimed, owner authorization must survive an intentional HTTP child
    # stop. Require the live inherited owner, not an HTTP readiness probe against
    # the child which this very intent is replacing.
    if request['claim_id'] and callable(getattr(creations._foundation,'require',None)):
        creations._foundation.require()
    else:
        creations._readiness()
    if not creations._admin_account(_accounts().get(request['actor'])):
        stop_lost_authority(config,request['actor'])
        raise OperationError('ADMIN_REQUIRED',403)
    with transaction() as db:
        current_authority(config,config.executor_id)
        executor_gate = creations._gate(db,config,config.executor_id)
        current_authority(config,request['actor'])
        request = db.execute('SELECT * FROM db_creation_requests WHERE id=?', (request_id,)).fetchone()
        if (request['environment_id'] != config.environment_id or request['approval_state'] != 'VALID'
                or request['action'] not in ('APPROVE','RECOVER_VERIFY')):
            raise OperationError('STALE_PLAN')
        if request['action'] == 'RECOVER_VERIFY':
            raise OperationError('OPERATOR_UNAVAILABLE',503)
        root = creations._root(db,config,request['creation_id'])
        gate = creations._gate(db,config,root['actor'])
        plan = db.execute('SELECT * FROM db_creation_phase_plans WHERE id=?', (request['plan_id'],)).fetchone()
        op = db.execute('''SELECT r.*,o.state AS operation_state FROM operation_requests r JOIN operations o ON o.id=r.operation_id
            WHERE r.operation_id=?''', (plan['operation_id'],)).fetchone()
        if (not root['reservation_active'] or root['warehouse_id'] != config.warehouse_id
                or root['revision'] != json.loads(request['ack'])['accepted_revision']
                or root['enrollment_id'] != config.enrollment_id or root['enrollment_revision'] != config.revision
                or gate['revision'] != request['gate_revision'] or plan['approval_request_id'] != request_id
                or op['revision'] != request['revision_cursor']
                or not request['claim_id'] and op['operation_state'] != 'AWAITING_APPLY'
                or not request['claim_id'] and request['revision_cursor'] != request['operation_revision']):
            raise OperationError('STALE_PLAN')
        if request['claim_id']:
            if not request['claim_id'].startswith('apply:'+str(executor_gate['revision'])+':'):
                raise OperationError('STALE_PLAN')
            if request['executor'] != executor.principal_id or request['owner_id'] != owner_id:
                raise OperationError('CREATION_BUSY')
            # Duplicate dispatch resolves the same claim; it never creates a retry.
            claim_id = request['claim_id']
        else:
            claim_id = 'apply:'+str(executor_gate['revision'])+':'+str(uuid.uuid4())
            db.execute("""UPDATE db_creation_requests SET dispatch_state='CLAIMED',stop_reason=NULL,executor=?,owner_id=?,
                claim_id=?,updated_at=? WHERE id=? AND claim_id IS NULL""",
                (executor.principal_id,owner_id,claim_id,admission._now(),request_id))
        return CreationPermit(config.environment_id,config.warehouse_id,config.enrollment_id,config.revision,
            gate['revision'],root['id'],root['database_id'],plan['phase'],plan['id'],request_id,plan['operation_id'],
            request['operation_revision'],plan['delivery_digest'],plan['private_ref'],root['actor'],executor.principal_id,owner_id,claim_id,
            executor_gate['revision'])


@contextmanager
def delegated(permit):
    if type(permit) is not CreationPermit:
        raise OperationError('P5_PERMIT_REQUIRED',403)
    token = _permit.set(permit)
    try:
        yield
    finally:
        _permit.reset(token)


def guard(db, operation_id, warehouse_id=None):
    """Core checks association and reservation even without a callback/context."""
    import database_creation_recovery as recovery
    if recovery.active() or recovery.observing():
        return recovery.guard(db,operation_id)
    canonical = db.execute('''SELECT g.warehouse_id FROM operation_requests r
        JOIN warehouse_config_generations g ON g.id=r.generation_id WHERE r.operation_id=?''', (operation_id,)).fetchone()
    if canonical:
        warehouse_id = canonical[0]
    plan = db.execute('SELECT * FROM db_creation_phase_plans WHERE operation_id=? AND purpose=\'APPLY\'', (operation_id,)).fetchone()
    if plan is None:
        if warehouse_id:
            creations.guard_reservation(db,warehouse_id)
        return None
    permit = _permit.get()
    if type(permit) is not CreationPermit:
        raise OperationError('P5_PERMIT_REQUIRED',403)
    config = creations.enrollment()
    managed_fence(config)
    root = creations._root(db,config,plan['creation_id'])
    request = db.execute('SELECT * FROM db_creation_requests WHERE id=?', (permit.approval_request_id,)).fetchone()
    gate = db.execute('SELECT * FROM db_creation_principal_gates WHERE environment_id=? AND principal_id=?',
        (config.environment_id,root['actor'])).fetchone()
    op = db.execute('SELECT revision,attempt_id FROM operation_requests WHERE operation_id=?', (operation_id,)).fetchone()
    expected = (config.environment_id,config.warehouse_id,config.enrollment_id,config.revision,
        root['id'],root['database_id'],plan['id'],plan['phase'],operation_id,plan['delivery_digest'],plan['private_ref'],root['actor'],config.executor_id)
    actual = (permit.environment_id,permit.warehouse_id,permit.enrollment_id,permit.enrollment_revision,
        permit.creation_id,permit.database_id,permit.plan_id,permit.phase,permit.operation_id,permit.delivery_digest,
        permit.candidate_binding,permit.requester_id,permit.executor_id)
    if (expected != actual or not root['reservation_active'] or root['warehouse_id'] != config.warehouse_id
            or root['enrollment_revision'] != config.revision or root['enrollment_id'] != config.enrollment_id
            or request is None or request['plan_id'] != plan['id'] or plan['approval_request_id'] != request['id']
            or root['revision'] != json.loads(request['ack'])['accepted_revision']
            or request['approval_state'] != 'VALID' or request['dispatch_state'] != 'CLAIMED'
            or request['claim_id'] != permit.claim_id or request['owner_id'] != permit.owner_id
            or request['executor'] != permit.executor_id or request['operation_revision'] != permit.approval_operation_revision
            or request['revision_cursor'] != op['revision']
            or op['attempt_id'] is not None and op['attempt_id'] != permit.claim_id):
        raise OperationError('STALE_PLAN')
    if gate is None or gate['state'] != 'ACTIVE' or gate['revision'] != permit.principal_gate_revision or gate['revision'] != request['gate_revision']:
        raise OperationError('PRINCIPAL_STOPPED',403)
    executor_gate = creations._gate(db,config,config.executor_id)
    if (executor_gate['revision'] != permit.executor_gate_revision
            or not request['claim_id'].startswith('apply:'+str(executor_gate['revision'])+':')):
        raise OperationError('STALE_PLAN')
    accounts = _accounts()
    if not creations._admin_account(accounts.get(root['actor'])):
        # The shared transaction boundary rolls back caller writes, then records
        # the durable STOP in a fresh transaction before propagating this denial.
        raise AuthorityLost(config,root['actor'])
    if not creations._admin_account(accounts.get(config.executor_id)):
        raise AuthorityLost(config,config.executor_id)
    creations.guard_reservation(db,config.warehouse_id,creation_id=root['id'])
    return request


def advance(db, operation_id):
    """Only the owned core transition advances its immutable-approval cursor."""
    permit = _permit.get()
    if type(permit) is CreationPermit and permit.operation_id == operation_id:
        db.execute('''UPDATE db_creation_requests SET revision_cursor=(SELECT revision FROM operation_requests WHERE operation_id=?),updated_at=?
            WHERE id=? AND claim_id=?''', (operation_id,admission._now(),permit.approval_request_id,permit.claim_id))


def core_attempt(operation_id):
    permit = _permit.get()
    return permit.claim_id if type(permit) is CreationPermit and permit.operation_id == operation_id else str(uuid.uuid4())


def reconciled(db, operation_id):
    """End the old approval atomically; resumption needs explicit fresh approval."""
    request = guard(db,operation_id)
    if request is None:
        return
    db.execute("""UPDATE db_creation_requests SET approval_state='REVOKED',dispatch_state='COMPLETE',
        stop_reason='RECONCILED',updated_at=? WHERE id=?""", (admission._now(),request['id']))
    db.execute('UPDATE db_creations SET revision=revision+1,updated_at=? WHERE id=?',
        (admission._now(),request['creation_id']))
    import database_creation_recovery as recovery
    if recovery.observing():
        db.execute("""UPDATE db_creation_requests SET approval_state='REVOKED',dispatch_state='COMPLETE',
            stop_reason='RECONCILED',updated_at=? WHERE approval_state='VALID' AND plan_id IN
            (SELECT id FROM db_creation_phase_plans WHERE operation_id=?)""",(admission._now(),operation_id))


def complete(db, operation_id, outcome):
    permit = _permit.get()
    if type(permit) is not CreationPermit or permit.operation_id != operation_id:
        return
    guard(db,operation_id)
    db.execute("UPDATE db_creation_requests SET approval_state='CONSUMED',dispatch_state='COMPLETE',updated_at=? WHERE id=?", (admission._now(),permit.approval_request_id))
    if outcome == 'APPLIED':
        db.execute("UPDATE db_creations SET reservation_active=0,terminal_outcome='APPLIED',revision=revision+1,updated_at=? WHERE id=?", (admission._now(),permit.creation_id))


def assert_leaf(plan):
    with transaction() as db:
        return guard(db,plan['id'],plan.get('warehouse_id') or plan.get('manifest',{}).get('spec',{}).get('warehouse_id'))


def record_leaf_intent(plan, private_receipt_ref):
    """Serialize a sealed private journal intent reference with its P5 gate.

    The journal remains the effect ledger; this reference is a dispatch fence,
    never a success receipt. The external send starts only after this returns.
    """
    if not _text(private_receipt_ref):
        raise OperationError('INVALID_REQUEST',400)
    with transaction() as db:
        import database_creation_recovery as recovery
        if recovery.observing():
            raise OperationError('OBSERVATION_ONLY',403)
        request = guard(db,plan['id'],plan.get('warehouse_id') or plan.get('manifest',{}).get('spec',{}).get('warehouse_id'))
        if request:
            attempt = db.execute('SELECT attempt_id FROM operation_requests WHERE operation_id=?', (plan['id'],)).fetchone()[0]
            if not recovery.active() and attempt != request['claim_id']:
                raise OperationError('STALE_PLAN')
            db.execute('UPDATE db_creation_requests SET private_receipt_ref=?,updated_at=? WHERE id=?',
                (private_receipt_ref,admission._now(),request['id']))


def authorize_actor(executor, actor):
    import database_creation_recovery as recovery
    if recovery.active():
        permit = recovery._scope.get()
        _executor_credentials(creations.enrollment(),executor)
        if permit.requester_id != actor:
            raise OperationError('ADMIN_REQUIRED',403)
        recovery.assert_original(dict(id=permit.operation_id))
        return True
    permit = _permit.get()
    if type(permit) is not CreationPermit:
        return False
    _executor_credentials(creations.enrollment(),executor)
    if permit.requester_id != actor or permit.executor_id != executor.principal_id:
        raise OperationError('ADMIN_REQUIRED',403)
    with transaction() as db:
        guard(db,permit.operation_id,permit.warehouse_id)
    return True
