"""Typed observation of original effects. Missing receipts never prove unsent."""
import copy
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
import os
import time
from pathlib import Path
import uuid

import control_store
import database_creation_auth as auth
import database_creations as creations
import runtime_profile
import warehouse_admission as admission
import warehouse_operations as core
from warehouse_operator import canonical, digest, save_private

_scope = ContextVar('p5_verify_recovery',default=None)
_observation = ContextVar('p5_private_observation',default=None)
_observation_request = ContextVar('p5_observation_request',default=None)
_terminal_observation = ContextVar('p5_terminal_observation',default=None)


@contextmanager
def observation_request(request):
    token = _observation_request.set(request)
    try:
        yield
    finally:
        _observation_request.reset(token)


@contextmanager
def _observing_verified(permit, request):
    # Only execute enters this after verify and its final current guard succeed.
    token = _terminal_observation.set((permit,request))
    try:
        yield
    finally:
        _terminal_observation.reset(token)


def owner_journal_digests(config, root, plan, operator):
    """Freeze only the exact original activations' local journal bytes."""
    from database_creation_worker import activation_id
    result = {}
    for role in ('workbench','native'):
        path = Path(config.value['owners'][role]['directory'])/'activations'/(
            activation_id(root,plan,operator,role)+'.json')
        result[role] = digest(canonical(runtime_profile.private_json(path) if path.exists() else None))
    return result


def projection_ready(db, root, plan, original):
    """Read-only local proof of a current, executable frozen recovery."""
    try:
        if (plan['phase'] != 'full' or original['phase'] != 'full'
                or plan['purpose'] != 'VERIFY_RECOVERY' or original['purpose'] != 'APPLY'
                or plan['operation_id'] != original['operation_id']):
            return False
        op = original_unknown(db, original['operation_id'])
        frozen = runtime_profile.private_json(plan['private_ref'])
        journal = control_store.root().resolve()/'warehouse-operator-receipts'/(original['operation_id']+'.json')
        receipt = runtime_profile.private_json(journal)
        declaration = receipt['verify_declaration']
        from types import SimpleNamespace
        leaves = classify(SimpleNamespace(receipt=receipt, binding=receipt['binding']))
        approval = db.execute('SELECT * FROM db_creation_requests WHERE id=?',
            (declaration['original_request_id'],)).fetchone()
        matching = (digest(canonical(frozen)) == plan['recovery_digest']
            and frozen['purpose'] == 'VERIFY_RECOVERY' and frozen['version'] == 1
            and frozen['creation_id'] == root['id'] and frozen['operation_id'] == original['operation_id']
            and frozen['original_plan_id'] == original['id'] and frozen['canonical_attempt'] == op['attempt_id']
            and frozen['candidate'] == receipt['binding']
            and frozen['candidate']['generation_id'] == original['generation_id']
            and frozen['candidate']['delivery_digest'] == original['delivery_digest']
            and plan['generation_id'] == original['generation_id'] and plan['delivery_digest'] == original['delivery_digest']
            and frozen['journal_path'] == str(journal) and frozen['journal_digest'] == digest(canonical(receipt))
            and declaration['state'] == 'SEALED' and declaration['original_plan_id'] == original['id']
            and declaration['original_claim_id'] == frozen['original_attempt']
            and approval is not None and approval['plan_id'] == original['id']
            and approval['claim_id'] == frozen['original_attempt']
            and frozen['executable'] is True and frozen['leaves'] == leaves and bool(leaves)
            and 'UNKNOWN' not in leaves.values())
        if not matching:
            return False
        # The parent receipt can lag an owner after a lost response. Reuse the
        # release verifier's local seal, descriptor and actual owner-journal
        # checks; no observer or health/SQL request is involved.
        config = getattr(creations._foundation, 'config', None)
        if config is None or config.enrollment != creations.enrollment():
            return False
        snapshot = release_snapshot(db,config,original['operation_id'])
        return (snapshot['recovery_plan']['id'] == plan['id']
            and snapshot['original_plan']['id'] == original['id']
            and frozen.get('owner_journals') == {role:owner['journal_digest'] for role,owner in snapshot['owners'].items()})
    except (OSError, ValueError, KeyError, TypeError, core.OperationError):
        return False


def projection_running(db, root, plan):
    """A claimed recovery becomes running only with its exact durable intent."""
    request = db.execute("""SELECT * FROM db_creation_requests WHERE plan_id=? AND action='RECOVER_VERIFY'
        AND approval_state='VALID' AND dispatch_state='CLAIMED' ORDER BY rowid DESC LIMIT 1""", (plan['id'],)).fetchone()
    if request is None or not request['claim_id']:
        return False
    try:
        path = Path(plan['private_ref']).with_name(request['claim_id'].split(':')[-1]+'.intent.json')
        intent = runtime_profile.private_json(path)
        return intent == dict(version=1,request_id=request['id'],claim_id=request['claim_id'],
            original_operation_id=plan['operation_id'],recovery_digest=plan['recovery_digest'],state='RUNNING')
    except (OSError, ValueError, TypeError):
        return False


def complete_reconcile(db, root, original, result):
    """Complete only this public observation, from current durable evidence."""
    request = _observation_request.get()
    if request is None or request['action'] != 'RECONCILE' or request['creation_id'] != root['id']:
        return
    positive = False
    if result.get('plan_id'):
        plan = db.execute('SELECT * FROM db_creation_phase_plans WHERE id=? AND creation_id=?',
            (result['plan_id'],root['id'])).fetchone()
        positive = plan is not None and projection_ready(db,root,plan,original)
    else:
        state = db.execute('SELECT state FROM operations WHERE id=?',(original['operation_id'],)).fetchone()
        positive = state is not None and state[0] in ('AWAITING_APPLY','PREPARED','VERIFIED_STALE','APPLIED') and result.get('state') == state[0]
    if positive:
        db.execute("""UPDATE db_creation_requests SET dispatch_state='COMPLETE',stop_reason=NULL,updated_at=?
            WHERE id=? AND creation_id=? AND action='RECONCILE' AND dispatch_state IN ('BLOCKED','ACCEPTED','CLAIMED')
            AND ack=? AND plan_id IS NULL AND claim_id IS NULL""",
            (admission._now(),request['id'],root['id'],request['ack']))


@dataclass(frozen=True)
class RecoveryPermit:
    config: creations.Enrollment
    creation_id: str
    operation_id: str
    original_plan_id: str
    recovery_plan_id: str
    approval_request_id: str
    claim_id: str
    owner_id: str
    requester_id: str
    gate_revision: int
    executor_gate_revision: int
    operation_revision: int
    recovery_path: str
    recovery_digest: str


def active():
    return type(_scope.get()) is RecoveryPermit


def observing():
    return _observation.get() is not None


def request_id():
    if not active():
        raise core.OperationError('P5_PERMIT_REQUIRED',403)
    return _scope.get().approval_request_id


@contextmanager
def delegated(permit):
    if type(permit) is not RecoveryPermit:
        raise core.OperationError('P5_PERMIT_REQUIRED',403)
    token = _scope.set(permit)
    try:
        yield
    finally:
        _scope.reset(token)


def original_unknown(db, operation_id):
    row = db.execute('''SELECT o.state,r.revision,r.attempt_id,s.state AS verify_state
        FROM operations o JOIN operation_requests r ON r.operation_id=o.id
        JOIN operation_steps s ON s.operation_id=o.id AND s.step='verify' WHERE o.id=?''',(operation_id,)).fetchone()
    if row is None or row['state'] != 'UNKNOWN' or row['verify_state'] != 'UNKNOWN':
        raise core.OperationError('RECONCILIATION_REQUIRED')
    if db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND step!='verify' AND state!='SUCCEEDED'",(operation_id,)).fetchone():
        raise core.OperationError('RECONCILIATION_REQUIRED')
    return row


def preverify_boundary(db, operation_id):
    """Exactly one owned uncertain boundary in the fixed canonical step order."""
    operation = core._view(db,operation_id,None,True)
    uncertain = [s for s in operation['steps'] if s['state'] in ('RUNNING','UNKNOWN')]
    if operation['state'] != 'UNKNOWN' or len(uncertain) != 1 or uncertain[0]['step'] not in core.STEPS:
        raise core.OperationError('RECONCILIATION_REQUIRED')
    step = uncertain[0]['step']
    for row in operation['steps']:
        if row['step'] == step:
            break
        if row['state'] != 'SUCCEEDED':
            raise core.OperationError('RECONCILIATION_REQUIRED')
    for row in operation['steps'][core.STEPS.index(step)+1:]:
        if row['state'] != 'PENDING' and not (operation['phase']=='bootstrap' and row['step']=='reconcile_trino' and row['state']=='SUCCEEDED'):
            raise core.OperationError('RECONCILIATION_REQUIRED')
    cursor = db.execute('SELECT * FROM operation_requests WHERE operation_id=?',(operation_id,)).fetchone()
    if not cursor['attempt_id']:
        raise core.OperationError('RECONCILIATION_REQUIRED')
    return operation,dict(cursor),step


def preverify_material(db, config, operation_id):
    """Local seal/receipt/claim checks; deliberately no current grant preflight."""
    from database_creation_preparation import Preparation
    from warehouse_operator import Operator
    operation,cursor,step = preverify_boundary(db,operation_id)
    plans = db.execute("SELECT * FROM db_creation_phase_plans WHERE operation_id=? AND purpose='APPLY'",(operation_id,)).fetchall()
    if len(plans) != 1:
        raise core.OperationError('STALE_PLAN')
    plan = dict(plans[0])
    root = dict(creations._root(db,config.enrollment,plan['creation_id']))
    request = db.execute('SELECT * FROM db_creation_requests WHERE id=?',(plan['approval_request_id'],)).fetchone()
    if (not root['reservation_active'] or root['enrollment_id'] != config.enrollment.enrollment_id
            or root['enrollment_revision'] != config.enrollment.revision or root['warehouse_id'] != config.enrollment.warehouse_id
            or request is None or request['plan_id'] != plan['id'] or request['creation_id'] != root['id']
            or request['actor'] != root['actor'] or request['executor'] != config.enrollment.executor_id
            or request['action'] != 'APPROVE' or not request['owner_id']
            or request['claim_id'] != cursor['attempt_id'] or request['revision_cursor'] != cursor['revision']):
        raise core.OperationError('STALE_PLAN')
    directory = config.preparation().output_root/root['id']/plan['phase']/str(plan['sequence'])
    journal = Preparation._journal(directory/'journal.json')
    Preparation._validate_seal(directory,journal,plan['private_ref'])
    operator = Operator(directory/'operator.json')
    operator.check_receipt(operation)
    expected = control_store.root().resolve()/'warehouse-operator-receipts'/(operation_id+'.json')
    if (operator.receipt_path != expected or journal['request_id'] != plan['preparation_request_id']
            or journal['binding'] != operator.binding or operator.binding['generation_id'] != plan['generation_id']
            or operator.binding['delivery_digest'] != plan['delivery_digest']
            or step not in operator.receipt['dispatches']
            or operator.config['preparation_inputs'].get(config.path) != config.binding):
        raise core.OperationError('STALE_PLAN')
    snapshot = dict(root=root,operation=operation,cursor=cursor,original_plan=plan,request=dict(request),step=step,
        candidate=operator.binding,seal=digest(canonical(journal)),receipt_digest=digest(expected.read_bytes()),
        enrollment_digest=config.binding,owner_records={role:digest(canonical(config.owner(role)))
            for role in ('workbench','native')},gates=[dict(row) for row in db.execute(
            'SELECT * FROM db_creation_principal_gates WHERE environment_id=? AND principal_id IN (?,?) ORDER BY principal_id',
            (config.enrollment.environment_id,root['actor'],config.enrollment.executor_id))])
    return snapshot,operator


def preverify_proof_path(config, snapshot):
    return config.preparation().output_root/snapshot['root']['id']/'recovery'/(
        'preverify-'+digest(canonical(snapshot)))


def preverify_release_snapshot(db, config, operation_id):
    snapshot,operator = preverify_material(db,config,operation_id)
    paths = list(preverify_proof_path(config,snapshot).glob('*.json'))
    if len(paths) != 1:
        raise core.OperationError('RECONCILIATION_REQUIRED')
    path = paths[0]
    proof = runtime_profile.private_json(path)
    evidence = proof['evidence']
    if (path.stem != digest(path.read_bytes()) or proof['version'] != 1 or proof['purpose'] != 'PREVERIFY_OBSERVATION' or proof['snapshot'] != snapshot
            or set(evidence) != {'step','outcome','evidence_ref','basis'}
            or evidence['step'] != snapshot['step'] or evidence['outcome'] != 'SUCCEEDED'
            or evidence['basis'] != 'observed_configuration' or not evidence['evidence_ref']):
        raise core.OperationError('RECONCILIATION_REQUIRED')
    owners = {}
    for role in ('workbench','native'):
        record = config.owner(role)
        expected = previous_attestation(config,snapshot['root'],snapshot['original_plan'],operator,role)
        owners[role] = dict(record_digest=digest(canonical(record)),runtime_id=record['runtime_instance_id'],
            owner_id=record['owner_instance_id'],attestation=expected,states=dict(stop='UNSENT',start='UNSENT',readiness='UNSENT'))
    if snapshot['request']['owner_id'] != owners['workbench']['owner_id']:
        raise core.OperationError('STALE_PLAN')
    return dict(root=snapshot['root'],operation=snapshot['operation'],original_plan=snapshot['original_plan'],
        original_request_id=snapshot['request']['id'],preverify=snapshot,
        proof_digest=digest(path.read_bytes()),owners=owners)


@dataclass(frozen=True)
class ReleaseObservation:
    enrollment_path: str
    enrollment_digest: str
    principal_id: str
    gate_revision: int
    operations: tuple  # (operation ID, canonical frozen snapshot), no authority
    observed_at: float


def previous_attestation(config,root,plan,operator,role):
    """Prove the original selected parent, independently of mixed live owners."""
    from database_creation_preparation import Preparation, _tree
    from database_creation_worker import activation_id, descriptor
    from warehouse_operator import Operator, private_file
    c = config.preparation()
    e = config.enrollment
    directory = c.output_root/root['id']/plan['phase']/str(plan['sequence'])
    journal = Preparation._journal(directory/'journal.json')
    Preparation._validate_seal(directory,journal,plan['private_ref'])
    fresh = Operator(directory/'operator.json')
    with control_store.connect() as db:
        linked = db.execute("SELECT * FROM db_creation_phase_plans WHERE id=? AND purpose='APPLY'",
                            (plan['id'],)).fetchone()
        canonical_root = dict(creations._root(db,e,root['id']))
    if (linked is None or dict(linked) != dict(plan) or not canonical_root['reservation_active']
            or any(canonical_root[k] != root[k] for k in
                   ('id','actor','environment_id','warehouse_id','enrollment_id','enrollment_revision'))
            or plan['creation_id'] != root['id'] or journal['request_id'] != plan['preparation_request_id']
            or fresh.binding != journal['binding'] or fresh.binding != operator.binding
            or fresh.config != operator.config or root['enrollment_id'] != e.enrollment_id
            or root['enrollment_revision'] != e.revision or root['warehouse_id'] != e.warehouse_id):
        raise core.OperationError('STALE_PLAN')
    pins = journal['pins']
    selected = pins['applied_material']
    inputs = fresh.config['preparation_inputs']
    def pinned(path):
        if inputs.get(str(path)) != digest(private_file(path)):
            raise core.OperationError('STALE_PLAN')
    owner = fresh.config.get('managed_handoff',{}).get('owners',{}).get(role)
    if selected.get('base') is True:
        profile = config.value['base_runtime_profile']
        pinned(profile)
        pinned(c.base_private)
        expected = dict(generation_id=runtime_profile._public_identity(config.base)['generation_id'],
                        base=True,profile=runtime_profile.private_binding(profile))
        if selected != expected or pins['base'] != _tree(c.base_bundle) or pins['base_private'] != digest(private_file(c.base_private)):
            raise core.OperationError('STALE_PLAN')
        # The same owner may already serve its sealed child candidate. Current
        # runtime admissibility is checked by the projection/recovery consumer.
        if owner is not None and config.owner(role)['owner_instance_id'] != owner['owner_instance_id']:
            raise core.OperationError('STALE_PLAN')
        return runtime_profile._public_identity(config.base)
    with control_store.connect() as db:
        parent = db.execute("""SELECT p.* FROM db_creation_phase_plans p
            JOIN db_creations c ON c.id=p.creation_id JOIN operations o ON o.id=p.operation_id
            JOIN warehouse_config_generations g ON g.id=p.generation_id AND g.warehouse_id=c.warehouse_id
            WHERE p.id=? AND p.operation_id=? AND p.generation_id=? AND p.phase='full' AND p.purpose='APPLY'
            AND c.environment_id=? AND c.warehouse_id=? AND c.enrollment_id=? AND c.enrollment_revision=?
            AND c.terminal_outcome='APPLIED' AND c.reservation_active=0 AND o.state='APPLIED' AND g.state='APPLIED'""",
            (selected['plan_id'],selected['operation_id'],selected['generation_id'],
             e.environment_id,e.warehouse_id,e.enrollment_id,e.revision)).fetchone()
        if parent is None or parent['creation_id'] == root['id']:
            raise core.OperationError('STALE_PLAN')
        operation = core._view(db,parent['operation_id'],None,is_admin=True)
    parent_dir = c.output_root/parent['creation_id']/'full'/str(parent['sequence'])
    receipt_path = control_store.root().resolve()/'warehouse-operator-receipts'/(parent['operation_id']+'.json')
    for path in (parent_dir/'private-input.json',parent_dir/'runtime-profile.json',
                 parent_dir/'journal.json',parent_dir/'operator.json',receipt_path):
        pinned(path)
    previous_journal = Preparation._journal(parent_dir/'journal.json')
    Preparation._validate_seal(parent_dir,previous_journal,parent['private_ref'])
    previous = Operator(parent_dir/'operator.json')
    receipt = runtime_profile.private_json(receipt_path)
    declaration = receipt['verify_declaration']
    with control_store.connect() as db:
        original_request = db.execute('SELECT * FROM db_creation_requests WHERE id=?',
                                      (declaration['original_request_id'],)).fetchone()
    if (declaration['state'] != 'SEALED' or declaration['original_plan_id'] != parent['id']
            or original_request is None or original_request['plan_id'] != parent['id']
            or original_request['claim_id'] != declaration['original_claim_id']):
        raise core.OperationError('STALE_PLAN')
    completion = receipt['verification_complete']
    proof = completion['proof']
    expected_pin = dict(generation_id=parent['generation_id'],plan_id=parent['id'],operation_id=parent['operation_id'],
        private_ref=parent['private_ref'],seal=digest(canonical(previous_journal)),binding=previous.binding,
        verification=digest(canonical(completion)))
    evidence = 'generation-verified:'+digest(canonical(proof))
    if (selected != expected_pin or previous.binding != previous_journal['binding']
            or previous_journal['request_id'] != parent['preparation_request_id']
            or previous.config['runtime_profile'] != str(parent_dir/'runtime-profile.json')
            or pins['base'] != _tree(parent_dir/'bundle') or pins['base_private'] != digest(private_file(parent_dir/'private-input.json'))
            or operation['manifest'] != previous.bundle['manifest']
            or operation['generation_id'] != parent['generation_id'] or operation['delivery_digest'] != parent['delivery_digest']
            or previous.binding['generation_id'] != parent['generation_id'] or previous.binding['delivery_digest'] != parent['delivery_digest']
            or receipt['actor'] != operation['actor'] or receipt['binding'] != previous.binding
            or proof['binding'] != previous.binding or proof['operation_id'] != parent['operation_id'] or proof['sql'] != receipt['sql']
            or completion['evidence_ref'] != evidence
            or not any(s['step']=='verify' and s['state']=='SUCCEEDED' and s['evidence_ref']==evidence for s in operation['steps'])):
        raise core.OperationError('STALE_PLAN')
    handoff = previous.config['managed_handoff']
    attestation = runtime_profile.validate_generation(parent_dir/'runtime-profile.json',previous.bundle)
    if attestation != handoff['runtime_attestation']:
        raise core.OperationError('STALE_PLAN')
    for previous_role,previous_owner in handoff['owners'].items():
        aid = activation_id(dict(id=parent['creation_id']),parent,previous,previous_role)
        activation = runtime_profile.private_json(Path(config.value['owners'][previous_role]['directory'])/'activations'/(aid+'.json'))
        sealed = descriptor(dict(id=activation['descriptor']['request_id']),dict(id=parent['creation_id']),parent,previous,previous_role)
        if (sealed['request_id'] != declaration['original_request_id'] or activation['descriptor'] != sealed
                or runtime_profile.private_json(Path(config.value['state_dir'])/'activation-plans'/sealed['request_id']/(aid+'.json')) != sealed
                or activation['state'] != 'ACK' or any(activation[k]['state'] != 'ACK' for k in ('stop','start','readiness'))
                or proof['runtime_readbacks'][previous_role] != dict(version=1,environment_id=e.environment_id,
                    owner_role=previous_role,owner_instance_id=previous_owner['owner_instance_id'],
                    runtime_instance_id=previous_owner['candidate_runtime_instance_id'],runtime_attestation=attestation)):
            raise core.OperationError('STALE_PLAN')
    previous_owner = handoff['owners'][role]
    record = config.owner(role)
    if (owner is not None and (owner['owner_instance_id'] != previous_owner['owner_instance_id']
            or owner['old_runtime_instance_id'] != previous_owner['candidate_runtime_instance_id'])
            or owner is None and (record['owner_instance_id'] != previous_owner['owner_instance_id']
                or record['runtime_instance_id'] != previous_owner['candidate_runtime_instance_id'])):
        raise core.OperationError('STALE_PLAN')
    return attestation


def release_snapshot(db,config,operation_id):
    """Local reads only; called again inside RELEASE's existing transaction."""
    from database_creation_worker import descriptor
    from warehouse_operator import Operator
    import database_creation_bridge as bridge
    uncertain = db.execute("SELECT step FROM operation_steps WHERE operation_id=? AND state IN ('RUNNING','UNKNOWN')",(operation_id,)).fetchall()
    if any(row['step'] in core.STEPS for row in uncertain):
        return preverify_release_snapshot(db,config,operation_id)
    op = dict(original_unknown(db,operation_id))
    row = db.execute("""SELECT * FROM db_creation_phase_plans WHERE operation_id=?
        AND purpose='VERIFY_RECOVERY' ORDER BY sequence DESC LIMIT 1""",(operation_id,)).fetchone()
    if row is None:
        raise core.OperationError('RECONCILIATION_REQUIRED')
    root = dict(creations._root(db,config.enrollment,row['creation_id']))
    original = db.execute("SELECT * FROM db_creation_phase_plans WHERE operation_id=? AND purpose='APPLY'",(operation_id,)).fetchone()
    frozen = runtime_profile.private_json(row['private_ref'])
    expected = control_store.root().resolve()/'warehouse-operator-receipts'/(operation_id+'.json')
    if (not root['reservation_active'] or root['enrollment_id'] != config.enrollment.enrollment_id
            or root['enrollment_revision'] != config.enrollment.revision or root['warehouse_id'] != config.enrollment.warehouse_id
            or original is None or frozen['original_plan_id'] != original['id']
            or frozen['operation_id'] != operation_id or frozen['creation_id'] != root['id']
            or frozen['journal_path'] != str(expected)
            or frozen.get('canonical_attempt',frozen['original_attempt']) != op['attempt_id']
            or frozen['executable'] is not True or 'UNKNOWN' in frozen['leaves'].values()
            or digest(canonical(frozen)) != row['recovery_digest']):
        raise core.OperationError('RECONCILIATION_REQUIRED')
    directory = config.preparation().output_root/root['id']/'full'/str(original['sequence'])
    journal = runtime_profile.private_json(directory/'journal.json')
    from database_creation_preparation import Preparation
    Preparation._validate_seal(directory,journal,original['private_ref'])
    operator = Operator(directory/'operator.json')
    receipt = runtime_profile.private_json(expected)
    operator.receipt = receipt
    declaration = receipt.get('verify_declaration',{})
    if (operator.binding != frozen['candidate'] or receipt['binding'] != operator.binding
            or journal['binding'] != operator.binding or declaration.get('state') != 'SEALED'
            or declaration.get('original_plan_id') != original['id']
            or declaration.get('original_claim_id') != frozen['original_attempt']
            or digest(canonical(receipt)) != frozen['journal_digest'] or classify(operator) != frozen['leaves']
            or operator.config['preparation_inputs'].get(config.path) != config.binding):
        raise core.OperationError('STALE_PLAN')
    owners = {}
    for role,saved in receipt['runtime_activations'].items():
        record = config.owner(role)
        approved = operator.config['managed_handoff']['owners'][role]
        if saved['owner'] != approved or record['owner_instance_id'] != approved['owner_instance_id']:
            raise core.OperationError('STALE_PLAN')
        aid = saved['activation_id']
        expected_descriptor = descriptor(dict(id=declaration['original_request_id']),root,original,operator,role)
        if aid != expected_descriptor['activation_id']:
            raise core.OperationError('STALE_PLAN')
        sealed = runtime_profile.private_json(Path(config.value['state_dir'])/'activation-plans'/declaration['original_request_id']/(aid+'.json'))
        if sealed != expected_descriptor:
            raise core.OperationError('STALE_PLAN')
        path = Path(config.value['owners'][role]['directory'])/'activations'/(aid+'.json')
        owner_journal = runtime_profile.private_json(path) if path.exists() else None
        states = {leaf:frozen['leaves']['activation/'+role+'/'+leaf] for leaf in ('stop','start','readiness')}
        if owner_journal is None:
            if saved['state'] != 'UNSENT' or set(states.values()) != {'UNSENT'}:
                raise core.OperationError('RECONCILIATION_REQUIRED')
        else:
            bridge.exact(owner_journal,{'descriptor','stop','start','readiness','state'})
            if owner_journal['descriptor'] != expected_descriptor:
                raise core.OperationError('STALE_PLAN')
            for leaf,state in states.items():
                entry = owner_journal[leaf]
                if (type(entry) is not dict or set(entry)-{'state','intent_id'}
                        or state not in ('COMPLETE','UNSENT') or entry.get('state') != ('ACK' if state=='COMPLETE' else 'UNSENT')):
                    raise core.OperationError('RECONCILIATION_REQUIRED')
                if state == 'COMPLETE' or 'intent_id' in entry:
                    bridge.request_id(entry.get('intent_id'))
        if states['start']=='COMPLETE' and states['stop']!='COMPLETE' or states['readiness']=='COMPLETE' and states['start']!='COMPLETE':
            raise core.OperationError('RECONCILIATION_REQUIRED')
        current_id = approved['candidate_runtime_instance_id'] if states['start']=='COMPLETE' else approved['old_runtime_instance_id']
        if record['runtime_instance_id'] != current_id:
            raise core.OperationError('STALE_PLAN')
        owners[role] = dict(record_digest=digest(canonical(record)),runtime_id=current_id,states=states,
            journal_digest=digest(canonical(owner_journal)),activation_id=aid,owner_id=approved['owner_instance_id'])
    return dict(root=root,operation=op,original_plan=dict(original),recovery_plan=dict(row),
        original_request_id=declaration['original_request_id'],
        frozen_digest=digest(canonical(frozen)),receipt_digest=digest(canonical(receipt)),owners=owners)


def observe_release(config,target):
    """Authenticated read-only observations, with no SQLite write lock held."""
    import database_creation_bridge as bridge
    import request_auth
    import urllib.request
    from warehouse_operator import Operator
    started = time.monotonic()
    with auth.transaction() as db:
        auth.current_authority(config.enrollment,config.enrollment.executor_id)
        auth.current_authority(config.enrollment,target)
        gate = db.execute('SELECT * FROM db_creation_principal_gates WHERE environment_id=? AND principal_id=?',
                          (config.enrollment.environment_id,target)).fetchone()
        if gate is None or gate['state'] != 'STOPPED':
            raise core.OperationError('REVISION_CONFLICT')
        revision = gate['revision']
        rows = db.execute("""SELECT DISTINCT p.operation_id FROM db_creations c JOIN db_creation_phase_plans p ON p.creation_id=c.id
            JOIN operations o ON o.id=p.operation_id LEFT JOIN operation_steps s ON s.operation_id=o.id
            WHERE c.environment_id=? AND (c.actor=? OR (?=? AND c.enrollment_id=? AND c.enrollment_revision=? AND c.warehouse_id=?))
            AND (s.state IN ('RUNNING','UNKNOWN') OR o.state='UNKNOWN')""",(config.enrollment.environment_id,target,target,
                config.enrollment.executor_id,config.enrollment.enrollment_id,config.enrollment.revision,config.enrollment.warehouse_id)).fetchall()
    headers = runtime_profile.private_json(config.value['executor_auth_file'])
    principal = request_auth.authenticate_principal(headers)
    auth._executor(config.enrollment,principal)
    snapshots = []
    for row in rows:
        with control_store.connect() as db:
            snapshot = release_snapshot(db,config,row['operation_id'])
        original = snapshot['original_plan']
        directory = config.preparation().output_root/original['creation_id']/original['phase']/str(original['sequence'])
        operator = Operator(directory/'operator.json')
        if 'preverify' in snapshot:
            frozen = snapshot['preverify']
            with auth.transaction():
                auth.current_authority(config.enrollment,frozen['root']['actor'])
                auth.current_authority(config.enrollment,config.enrollment.executor_id)
            # This bootstrap leaf needs a genuine managed observation context
            # and its sealed transport. Standalone RELEASE cannot create either.
            if operator.phase == 'bootstrap' and frozen['step'] == 'reconcile_polaris':
                raise core.OperationError('RECONCILIATION_REQUIRED')
            reader = preverify_reader(operator,frozen)
            try:
                evidence = reader.reconcile(frozen['operation'],frozen['step'])
                core._validate_reconciliation(evidence)
                if (evidence.step != frozen['step'] or evidence.outcome != 'SUCCEEDED'
                        or evidence.basis != 'observed_configuration'):
                    raise core.OperationError('RECONCILIATION_REQUIRED')
            except Exception:
                raise core.OperationError('RECONCILIATION_REQUIRED') from None
            finally:
                reader.close_transports()
        for role,owner in snapshot['owners'].items():
            response = bridge.owner_get(config,role,str(uuid.UUID(int=0)))
            if response['state'] not in ('NOT_FOUND','ACK','COMPLETE','READY') or response['runtime_instance_id'] != owner['runtime_id']:
                raise core.OperationError('RECONCILIATION_REQUIRED')
            if owner['states']['stop'] == 'COMPLETE':
                activation = bridge.owner_get(config,role,snapshot['original_request_id'],owner['activation_id'])
                if (activation['runtime_instance_id'] != owner['runtime_id']
                        or activation.get('previous_runtime_instance_id') != operator.config['managed_handoff']['owners'][role]['old_runtime_instance_id']
                        or set(owner['states'].values())=={'COMPLETE'} and activation['state']!='ACK'):
                    raise core.OperationError('RECONCILIATION_REQUIRED')
            # Each role has its own classified old/new identity. Mixed cutover is
            # legitimate and RELEASE does not assert full Foundation readiness.
            stopped = owner['states']['stop']=='COMPLETE' and owner['states']['start']=='UNSENT'
            if not stopped:
                expected = (owner['attestation'] if 'preverify' in snapshot else
                            operator.config['managed_handoff']['runtime_attestation'] if owner['states']['start']=='COMPLETE'
                            else previous_attestation(config,snapshot['root'],original,operator,role))
                origin = config.value['origins'][role]
                instance = Operator.http(operator,urllib.request.Request(origin+creations.COLLECTION+'/runtime-instance',headers=headers))
                actual = Operator.http(operator,urllib.request.Request(origin+'/api/v1/runtime',headers=headers))
                if instance != dict(version=1,environment_id=config.enrollment.environment_id,owner_role=role,
                        owner_instance_id=owner['owner_id'],runtime_instance_id=owner['runtime_id'],runtime_attestation=expected) or actual != expected:
                    raise core.OperationError('RECONCILIATION_REQUIRED')
        if 'preverify' in snapshot:
            with auth.transaction():
                auth.current_authority(config.enrollment,snapshot['root']['actor'])
                auth.current_authority(config.enrollment,config.enrollment.executor_id)
            with control_store.connect() as db:
                current,ignored = preverify_material(db,config,row['operation_id'])
            if current != snapshot['preverify']:
                raise core.OperationError('STALE_PLAN')
        snapshots.append((row['operation_id'],canonical(snapshot)))
    return ReleaseObservation(config.path,config.binding,target,revision,tuple(snapshots),started)


def validate_release_observation(db,config,target,observation):
    import database_creation_bridge as bridge
    if type(observation) is not ReleaseObservation or observation.principal_id != target or not 0 <= time.monotonic()-observation.observed_at <= 30:
        raise core.OperationError('RECONCILIATION_REQUIRED')
    enrolled = bridge.Configuration(observation.enrollment_path)
    gate = db.execute('SELECT * FROM db_creation_principal_gates WHERE environment_id=? AND principal_id=?',
                      (config.environment_id,target)).fetchone()
    if (enrolled.enrollment != config or enrolled.binding != observation.enrollment_digest
            or gate is None or gate['state'] != 'STOPPED' or gate['revision'] != observation.gate_revision):
        raise core.OperationError('STALE_PLAN')
    import request_auth
    principal = request_auth.authenticate_principal(runtime_profile.private_json(enrolled.value['executor_auth_file']))
    auth._executor(config,principal)
    return enrolled


def release_safe(db,config,operation_id,*,observation=None):
    try:
        import database_creation_bridge as bridge
        if type(observation) is not ReleaseObservation:
            return False
        enrolled = validate_release_observation(db,config,observation.principal_id,observation)
        gate = db.execute('SELECT * FROM db_creation_principal_gates WHERE environment_id=? AND principal_id=?',
                          (config.environment_id,observation.principal_id)).fetchone()
        snapshots = dict(observation.operations)
        return (enrolled.enrollment == config and enrolled.binding == observation.enrollment_digest
            and gate['state']=='STOPPED' and gate['revision']==observation.gate_revision
            and snapshots.get(operation_id)==canonical(release_snapshot(db,enrolled,operation_id)))
    except Exception:
        return False


def release_local(path,target,key):
    import database_creation_bridge as bridge
    import request_auth
    config = bridge.Configuration(path)
    # This process-local auth context confers no managed identity or Foundation.
    os.environ.update(SHIROKUMA_STATE_DIR=config.value['state_dir'],SHIROKUMA_AUTH_MODE='shared',SHIROKUMA_AUTH_FILE=config.base.auth_file)
    with auth.transaction():
        auth.current_authority(config.enrollment,config.enrollment.executor_id)
        auth.current_authority(config.enrollment,target)
    principal = request_auth.authenticate_principal(runtime_profile.private_json(config.value['executor_auth_file']))
    auth._executor(config.enrollment,principal)
    creations.validate_key(key)
    with control_store.connect() as db:
        saved = creations._saved(db,config.enrollment,principal.principal_id,'private/RELEASE_PRINCIPAL/'+target,key,dict(principal_id=target))
    if saved:
        return auth.release_principal(config.enrollment,principal,target,key)
    observation = observe_release(config,target)
    return auth.release_principal(config.enrollment,principal,target,key,observation=observation)


def guard(db, operation_id):
    observation = _observation.get()
    if observation is not None:
        managed, root_id, original_id = observation
        managed.require()
        root = creations._root(db,managed.config.enrollment,root_id)
        auth.current_authority(managed.config.enrollment,managed.config.enrollment.executor_id)
        auth.current_authority(managed.config.enrollment,root['actor'])
        creations._gate(db,managed.config.enrollment,root['actor'])
        creations._gate(db,managed.config.enrollment,managed.config.enrollment.executor_id)
        if operation_id != original_id or not root['reservation_active']:
            raise core.OperationError('STALE_PLAN')
        request = _observation_request.get()
        terminal = _terminal_observation.get()
        if terminal is not None:
            permit,verified_request = terminal
            if (request != verified_request or type(permit) is not RecoveryPermit
                    or request is None or request['id'] != permit.approval_request_id
                    or request['creation_id'] != root_id or managed.config.enrollment != permit.config
                    or permit.creation_id != root_id or permit.operation_id != original_id
                    or managed.hello['owner_instance_id'] != permit.owner_id):
                raise core.OperationError('STALE_PLAN')
            current = _guard_recovery(db,operation_id,permit,terminal=True)
            if dict(current) != verified_request:
                raise core.OperationError('STALE_PLAN')
            return current
        if request is not None and request['action'] != 'RECONCILE':
            # Private OBSERVE may reconcile canonical evidence but must not
            # rewrite the approval whose original ID selected that evidence.
            return None
        # Observation can feed only production reconciliation, never a leaf send.
        return db.execute("SELECT r.* FROM db_creation_phase_plans p JOIN db_creation_requests r ON r.id=p.approval_request_id WHERE p.operation_id=? AND p.purpose='APPLY'",
                          (operation_id,)).fetchone()
    return _guard_recovery(db,operation_id,_scope.get())


def _guard_recovery(db, operation_id, permit, *, terminal=False):
    if type(permit) is not RecoveryPermit or operation_id != permit.operation_id or permit.config != creations.enrollment():
        raise core.OperationError('P5_PERMIT_REQUIRED',403)
    import database_creation_bridge as bridge
    auth.managed_fence(permit.config)
    bridge.current().require()
    root = creations._root(db,permit.config,permit.creation_id)
    request = db.execute('SELECT * FROM db_creation_requests WHERE id=?',(permit.approval_request_id,)).fetchone()
    plan = db.execute('SELECT * FROM db_creation_phase_plans WHERE id=?',(permit.recovery_plan_id,)).fetchone()
    original = db.execute("SELECT * FROM db_creation_phase_plans WHERE id=? AND purpose='APPLY'",(permit.original_plan_id,)).fetchone()
    if terminal:
        op = db.execute('''SELECT o.state,r.revision,r.attempt_id,s.state AS verify_state
            FROM operations o JOIN operation_requests r ON r.operation_id=o.id
            JOIN operation_steps s ON s.operation_id=o.id AND s.step='verify' WHERE o.id=?''',
            (operation_id,)).fetchone()
        if (op is None or not ((op['state'] == 'UNKNOWN' and op['verify_state'] == 'UNKNOWN')
                or (op['state'] in ('UNKNOWN','AWAITING_APPLY') and op['verify_state'] == 'SUCCEEDED'
                    and op['attempt_id'] is None))
                or db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND step!='verify' AND state!='SUCCEEDED'",
                    (operation_id,)).fetchone()):
            raise core.OperationError('RECONCILIATION_REQUIRED')
        intent = runtime_profile.private_json(intent_path(permit))
        if intent != dict(version=1,request_id=permit.approval_request_id,claim_id=permit.claim_id,
                original_operation_id=operation_id,recovery_digest=permit.recovery_digest,state='RUNNING'):
            raise core.OperationError('RECONCILIATION_REQUIRED')
    else:
        op = original_unknown(db,operation_id)
    auth.current_authority(permit.config,permit.requester_id)
    auth.current_authority(permit.config,permit.config.executor_id)
    gate = creations._gate(db,permit.config,permit.requester_id)
    executor_gate = creations._gate(db,permit.config,permit.config.executor_id)
    if (request is None or plan is None or original is None or request['action'] != 'RECOVER_VERIFY'
            or original['phase'] != 'full' or original['operation_id'] != operation_id
            or original['creation_id'] != root['id'] or plan['creation_id'] != root['id']
            or root['actor'] != permit.requester_id or root['warehouse_id'] != permit.config.warehouse_id
            or request['actor'] != root['actor'] or request['environment_id'] != permit.config.environment_id
            or request['approval_state'] != 'VALID' or request['dispatch_state'] != 'CLAIMED'
            or request['claim_id'] != permit.claim_id or request['owner_id'] != permit.owner_id
            or request['executor'] != permit.config.executor_id or request['plan_id'] != plan['id']
            or plan['approval_request_id'] != request['id'] or plan['purpose'] != 'VERIFY_RECOVERY'
            or plan['operation_id'] != operation_id or plan['recovery_digest'] != permit.recovery_digest
            or plan['private_ref'] != permit.recovery_path or not root['reservation_active']
            or root['enrollment_id'] != permit.config.enrollment_id or root['enrollment_revision'] != permit.config.revision
            or root['revision'] != json.loads(request['ack'])['accepted_revision']
            or gate['revision'] != permit.gate_revision or request['gate_revision'] != permit.gate_revision
            or executor_gate['revision'] != permit.executor_gate_revision
            or op['revision'] != permit.operation_revision + (1 if terminal and op['state'] == 'AWAITING_APPLY' else 0)
            or request['operation_revision'] != permit.operation_revision):
        raise core.OperationError('STALE_PLAN')
    frozen = runtime_profile.private_json(permit.recovery_path)
    if terminal and op['verify_state'] == 'UNKNOWN' and op['attempt_id'] != frozen.get('canonical_attempt',frozen['original_attempt']):
        raise core.OperationError('STALE_PLAN')
    if (digest(canonical(frozen)) != permit.recovery_digest or frozen['executable'] is not True
            or 'UNKNOWN' in frozen['leaves'].values()
            or frozen['operation_id'] != operation_id or frozen['creation_id'] != root['id']
            or frozen['original_plan_id'] != original['id']
            or frozen['candidate'].get('generation_id') != original['generation_id']
            or frozen['candidate'].get('delivery_digest') != original['delivery_digest']):
        raise core.OperationError('RECONCILIATION_REQUIRED')
    expected_journal = control_store.root().resolve()/'warehouse-operator-receipts'/(operation_id+'.json')
    if frozen['journal_path'] != str(expected_journal):
        raise core.OperationError('STALE_PLAN')
    receipt = runtime_profile.private_json(expected_journal)
    declaration = receipt.get('verify_declaration',{})
    if (receipt['binding'] != frozen['candidate'] or declaration.get('state') != 'SEALED'
            or declaration.get('original_claim_id') != frozen['original_attempt']
            or declaration.get('original_plan_id') != original['id']):
        raise core.OperationError('STALE_PLAN')
    creations.guard_reservation(db,permit.config.warehouse_id,creation_id=permit.creation_id)
    return request


def assert_original(plan):
    with auth.transaction() as db:
        guard(db,plan['id'])
    permit = _scope.get()
    value = runtime_profile.private_json(intent_path(permit))
    if value != dict(version=1,request_id=permit.approval_request_id,claim_id=permit.claim_id,
            original_operation_id=permit.operation_id,recovery_digest=permit.recovery_digest,state='RUNNING'):
        raise core.OperationError('RECONCILIATION_REQUIRED')


def intent_path(permit):
    return Path(permit.recovery_path).with_name(permit.claim_id.split(':')[-1]+'.intent.json')


def require_read_auth_resolved(receipt):
    """Auxiliary sends cannot disappear from recovery's executable decision."""
    from warehouse_operator import Operator
    attempts = receipt.get('read_auth',{})
    if not isinstance(attempts,dict):
        raise core.OperationError('RECONCILIATION_REQUIRED')
    for key,saved in attempts.items():
        try:
            if (saved['state'] != 'ACK' or key != saved['context']+'/'+saved['http_identity']
                    or saved['http_identity'] not in receipt.get('verify_http',{})):
                raise ValueError()
            Operator.oauth_expiry(saved['response'])
        except (KeyError, TypeError, ValueError, RuntimeError):
            raise core.OperationError('RECONCILIATION_REQUIRED') from None


def read_auth_context(operator, key):
    """Fence an auxiliary send with the actual current recovery/OBSERVE request."""
    permit = _scope.get()
    if type(permit) is RecoveryPermit and not observing():
        assert_original(dict(id=permit.operation_id))
        expected = control_store.root().resolve()/'warehouse-operator-receipts'/(permit.operation_id+'.json')
        if operator.receipt_path != expected:
            raise core.OperationError('STALE_PLAN')
        current = runtime_profile.private_json(expected)
        frozen = runtime_profile.private_json(permit.recovery_path)
        if current != operator.receipt or frozen['leaves'].get('http/'+key) != 'COMPLETE':
            raise core.OperationError('STALE_PLAN')
        return permit.approval_request_id
    creation = auth._permit.get()
    if type(creation) is auth.CreationPermit and creation.phase == 'full' and not observing():
        current = dict(auth.assert_leaf(dict(id=creation.operation_id)))
        import database_creation_bridge as bridge
        from database_creation_worker import executor, resolve
        managed = bridge.current()
        executor(managed)
        selected,root,plan,fresh = resolve(managed,creation.approval_request_id)
        if (selected != current or current['action'] != 'APPROVE' or root['id'] != creation.creation_id
                or managed.hello['owner_instance_id'] != creation.owner_id or plan is None
                or plan['id'] != creation.plan_id or plan['phase'] != 'full' or plan['purpose'] != 'APPLY'
                or plan['operation_id'] != creation.operation_id or operator.phase != 'full'
                or fresh.binding != operator.binding or fresh.config != operator.config):
            raise core.OperationError('STALE_PLAN')
        fresh.check_receipt(dict(id=creation.operation_id,actor=root['actor'],generation_id=plan['generation_id'],
            delivery_digest=plan['delivery_digest'],phase=plan['phase'],predecessor_id=plan['predecessor_operation_id']))
        declaration = fresh.receipt.get('verify_declaration',{})
        if (fresh.receipt_path != operator.receipt_path or fresh.receipt != operator.receipt
                or declaration.get('state') != 'SEALED' or declaration.get('binding') != fresh.binding
                or declaration.get('original_plan_id') != plan['id']
                or fresh.receipt.get('verify_http',{}).get(key,{}).get('state') != 'ACK'):
            raise core.OperationError('STALE_PLAN')
        return current['id']
    if observing():
        request = _observation_request.get()
        if request is None or operator.phase != 'full':
            raise core.OperationError('OBSERVATION_IDENTITY_REQUIRED',403)
        managed,root_id,operation_id = _observation.get()
        from database_creation_worker import executor, resolve
        executor(managed)
        selected,root,plan,fresh = resolve(managed,request['id'],observation=True)
        if (selected != request or root['id'] != root_id or plan is None or plan['phase'] != 'full'
                or plan['operation_id'] != operation_id or fresh.binding != operator.binding
                or fresh.config != operator.config):
            raise core.OperationError('STALE_PLAN')
        fresh.check_receipt(dict(id=operation_id,actor=root['actor'],generation_id=plan['generation_id'],
            delivery_digest=plan['delivery_digest'],phase=plan['phase'],predecessor_id=plan['predecessor_operation_id']))
        declaration = fresh.receipt.get('verify_declaration',{})
        if (fresh.receipt != operator.receipt or declaration.get('state') != 'SEALED'
                or declaration.get('binding') != fresh.binding or declaration.get('original_plan_id') != plan['id']):
            raise core.OperationError('STALE_PLAN')
        with auth.transaction() as db:
            guard(db,operation_id)
            current = db.execute('SELECT * FROM db_creation_requests WHERE id=?',(request['id'],)).fetchone()
            if (current is None or dict(current) != request or request['creation_id'] != root_id
                    or request['environment_id'] != managed.config.enrollment.environment_id):
                raise core.OperationError('STALE_PLAN')
        return request['id'] if _terminal_observation.get() is not None else 'observe:'+request['id']
    raise core.OperationError('P5_PERMIT_REQUIRED',403)


def authorize_leaf(plan,identity,receipt):
    if observing():
        raise core.OperationError('OBSERVATION_ONLY',403)
    if not active():
        return
    require_read_auth_resolved(receipt)
    assert_original(plan)
    permit = _scope.get()
    frozen = runtime_profile.private_json(permit.recovery_path)
    kind = identity.get('kind')
    if kind == 'activation':
        key = 'activation/'+identity['role']
        leaves = [v for k,v in frozen['leaves'].items() if k.startswith(key+'/')]
        if leaves and all(v in ('UNSENT','COMPLETE') for v in leaves) and 'UNSENT' in leaves:
            return
    elif kind == 'managed-activation':
        matches = [role for role,item in receipt['runtime_activations'].items()
                   if item['activation_id']==identity['activation_id']]
        if len(matches) != 1:
            raise core.OperationError('STALE_PLAN')
        key = 'activation/'+matches[0]+'/'+identity['leaf']
    elif kind == 'sql-post':
        key = 'sql/'+identity['receipt_key']
    elif kind == 'verify-http':
        key = 'http/'+identity['key']
    elif kind == 'polaris-transport':
        key = 'transport/'+identity['service_ref']
        if frozen['transports'].get(identity['service_ref']) != identity['endpoint']:
            raise core.OperationError('STALE_PLAN')
        return
    else:
        raise core.OperationError('RECOVERY_SCOPE_VIOLATION',403)
    if frozen['leaves'].get(key) != 'UNSENT':
        raise core.OperationError('RECOVERY_SCOPE_VIOLATION',403)


def claim(managed,rid,executor,*,authorization=False):
    auth._executor(managed.config.enrollment,executor)
    with auth.transaction() as db:
        request = db.execute('SELECT * FROM db_creation_requests WHERE id=?',(rid,)).fetchone()
        if request is None or request['action'] != 'RECOVER_VERIFY' or request['approval_state'] != 'VALID':
            raise core.OperationError('STALE_PLAN')
        if authorization:
            if not request['claim_id'] or request['dispatch_state'] != 'CLAIMED':
                raise core.OperationError('STALE_PLAN')
        elif request['owner_id'] is not None and request['owner_id'] != managed.hello['owner_instance_id']:
            raise core.OperationError('CREATION_BUSY')
        config = managed.config.enrollment
        root = creations._root(db,config,request['creation_id'])
        auth.current_authority(config,root['actor'])
        auth.current_authority(config,config.executor_id)
        gate, eg = creations._gate(db,config,root['actor']),creations._gate(db,config,config.executor_id)
        plan = db.execute('SELECT * FROM db_creation_phase_plans WHERE id=?',(request['plan_id'],)).fetchone()
        if plan is None or plan['phase'] != 'full' or plan['purpose'] != 'VERIFY_RECOVERY':
            raise core.OperationError('STALE_PLAN')
        op = original_unknown(db,plan['operation_id'])
        frozen = runtime_profile.private_json(plan['private_ref'])
        claim_id = request['claim_id'] or 'verify-recovery:'+str(eg['revision'])+':'+str(uuid.uuid4())
        owner = request['owner_id'] or managed.hello['owner_instance_id']
        if not claim_id.startswith('verify-recovery:'+str(eg['revision'])+':'):
            raise core.OperationError('STALE_PLAN')
        db.execute("UPDATE db_creation_requests SET dispatch_state='CLAIMED',executor=?,owner_id=?,claim_id=?,stop_reason=NULL,updated_at=? WHERE id=? AND (claim_id IS NULL OR claim_id=?)",
            (config.executor_id,owner,claim_id,admission._now(),rid,claim_id))
        permit = RecoveryPermit(config,root['id'],plan['operation_id'],frozen['original_plan_id'],plan['id'],rid,
            claim_id,owner,root['actor'],gate['revision'],eg['revision'],op['revision'],plan['private_ref'],plan['recovery_digest'])
        with delegated(permit):
            guard(db,plan['operation_id'])
        return permit


def classify(operator):
    receipt = operator.receipt
    require_read_auth_resolved(receipt)
    declaration = receipt.get('verify_declaration')
    if not declaration or declaration.get('state') != 'SEALED' or declaration['binding'] != operator.binding:
        raise core.OperationError('RECONCILIATION_REQUIRED')
    leaves = {}
    if set(receipt.get('runtime_activations',{})) != {'workbench','native'}:
        raise core.OperationError('RECONCILIATION_REQUIRED')
    for role,saved in receipt['runtime_activations'].items():
        leaves['activation/'+role] = 'COMPLETE' if saved['state']=='ACK' else 'UNSENT' if saved['state']=='UNSENT' else 'UNKNOWN'
        if saved['state'] == 'PARTIAL':
            leaves['activation/'+role] = 'PARTIAL'
        for name in ('stop','start','readiness'):
            state = saved.get('leaves',{}).get(name)
            leaves['activation/'+role+'/'+name] = 'COMPLETE' if saved['state']=='ACK' or state=='ACK' else 'UNSENT' if state=='UNSENT' else 'UNKNOWN'
    for key,state in declaration['sql'].items():
        saved = receipt['sql'].get(key)
        leaves['sql/'+key] = ('COMPLETE' if saved and saved.get('complete') and saved.get('backend_id')
            else 'GET_ONLY' if saved and saved.get('next_uri') else 'UNKNOWN' if saved
            else 'UNSENT' if state=='UNSENT' else 'UNKNOWN')
    for key,saved in receipt.get('verify_http',{}).items():
        leaves['http/'+key] = 'COMPLETE' if saved['state']=='ACK' else 'UNSENT' if saved['state']=='UNSENT' else 'UNKNOWN'
    return leaves


def observe(managed,root,plan,operator):
    result = observe_preverify(managed,root,plan,operator)
    if result is not None:
        return result
    if plan.get('phase') == 'bootstrap':
        return observe_bootstrap(managed,root,plan,operator)
    with core._operator_gate():
        result = _observe(managed,root,plan,operator)
    if result is not None:
        return result
    token = _observation.set((managed,root['id'],plan['operation_id']))
    try:
        try:
            result = core.reconcile_from_observer(plan['operation_id'],actor=root['actor'],
                observer=lambda p:operator.reconcile(p,'verify'))
        except core.OperationError as error:
            if error.code not in ('PRINCIPAL_STOPPED','ADMIN_REQUIRED'):
                raise
            result = dict(state='UNKNOWN')
    finally:
        _observation.reset(token)
    if result['state'] != 'UNKNOWN':
        return result
    # The one-shot worker's owned forwards may already be closed. Observation
    # never launches replacements. An explicit narrow recovery can establish
    # the same sealed transport and reuse all completed SQL/activation receipts.
    with core._operator_gate():
        return _observe(managed,root,plan,operator,plan_completed=True)


def preverify_reader(operator, snapshot):
    """Use production reads without changing the original dispatch receipt."""
    reader = copy.copy(operator)
    reader._observation_request_id = snapshot['request']['id']
    reader._observation_operation = snapshot['operation']
    reader.save = lambda: None
    check = reader.check_receipt
    def check_original(current):
        check(current)
        reader.receipt = copy.deepcopy(reader.receipt)
        reader.receipt['acknowledgements'].pop(snapshot['step'],None)
    reader.check_receipt = check_original
    return reader


def observe_preverify(managed, root, plan, operator):
    """Observe a fixed original configuration boundary, never issue that step."""
    from database_creation_worker import executor
    operation = core.inspect(plan['operation_id'],actor=root['actor'])
    if operation['state'] != 'UNKNOWN' or not any(
            s['step'] in core.STEPS and s['state'] in ('RUNNING','UNKNOWN') for s in operation['steps']):
        return None
    executor(managed)
    # Real authority is required even when its durable gate remains STOPPED.
    with auth.transaction():
        auth.current_authority(managed.config.enrollment,root['actor'])
    with control_store.connect() as db:
        snapshot,fresh = preverify_material(db,managed.config,operation['id'])
    if (snapshot['root']['id'] != root['id'] or snapshot['original_plan'] != plan
            or fresh.binding != operator.binding or fresh.config != operator.config):
        raise core.OperationError('STALE_PLAN')
    # The production Secret reader normally caches readbacks in the dispatch
    # receipt. Preserve that original ledger and capture only separate evidence.
    reader = preverify_reader(fresh,snapshot)
    def observe_original(current):
        if current != snapshot['operation']:
            raise core.OperationError('STALE_PLAN')
        if reader.phase == 'bootstrap' and snapshot['step']=='reconcile_polaris':
            reader.start_transports(current)
        evidence = reader.reconcile(current,snapshot['step'])
        core._validate_reconciliation(evidence)
        if evidence.step != snapshot['step']:
            raise core.OperationError('RECONCILIATION_REQUIRED')
        with auth.transaction():
            auth.current_authority(managed.config.enrollment,root['actor'])
            auth.current_authority(managed.config.enrollment,managed.config.enrollment.executor_id)
        with control_store.connect() as db:
            after,ignored = preverify_material(db,managed.config,current['id'])
        if after != snapshot:
            raise core.OperationError('STALE_PLAN')
        return evidence
    token = _observation.set((managed,root['id'],operation['id']))
    try:
        stopped = False
        with auth.transaction() as db:
            for principal in (root['actor'],managed.config.enrollment.executor_id):
                auth.current_authority(managed.config.enrollment,principal)
                try:
                    creations._gate(db,managed.config.enrollment,principal)
                except core.OperationError as error:
                    if error.code != 'PRINCIPAL_STOPPED':
                        raise
                    stopped = True
        if not stopped:
            def positive(current):
                evidence = observe_original(current)
                if evidence.outcome != 'SUCCEEDED' or evidence.basis != 'observed_configuration':
                    raise core.OperationError('RECONCILIATION_REQUIRED')
                return evidence
            try:
                result = core.reconcile_from_observer(operation['id'],actor=root['actor'],observer=positive)
                with auth.transaction() as db:
                    complete_reconcile(db,root,plan,result)
                return result
            except core.OperationError as error:
                if error.code != 'RECONCILIATION_OBSERVER_FAILED':
                    raise
                return dict(state='UNKNOWN')
        try:
            with core._operator_gate():
                evidence = observe_original(operation)
        except Exception:
            return dict(state='UNKNOWN')
        if evidence.outcome == 'SUCCEEDED' and evidence.basis == 'observed_configuration':
            proof = dict(version=1,purpose='PREVERIFY_OBSERVATION',snapshot=snapshot,evidence=evidence.__dict__)
            directory = preverify_proof_path(managed.config,snapshot)
            directory.mkdir(mode=0o700,parents=True,exist_ok=True)
            path = directory/(digest(canonical(proof))+'.json')
            if path.exists():
                if runtime_profile.private_json(path) != proof:
                    raise core.OperationError('STALE_PLAN')
            else:
                save_private(path,proof)
        return dict(state='UNKNOWN')
    finally:
        reader.close_transports()
        _observation.reset(token)


def guard_observation(operator, operation):
    """Revalidate the sealed original candidate and live read-only authority."""
    if not observing():
        raise core.OperationError('OBSERVATION_ONLY',403)
    managed, root_id, operation_id = _observation.get()
    if operation['id'] != operation_id or operator.phase != 'bootstrap':
        raise core.OperationError('STALE_PLAN')
    from database_creation_worker import executor, resolve
    executor(managed)
    request,root,plan,fresh = resolve(managed,operator._observation_request_id,observation=True)
    if (root['id'] != root_id or plan['operation_id'] != operation_id
            or fresh.binding != operator.binding or fresh.config != operator.config):
        raise core.OperationError('STALE_PLAN')
    # Validate using a fresh instance without replacing in-memory observation data.
    fresh.check_receipt(operation)


def observe_bootstrap(managed,root,plan,operator):
    """Observe original completed bootstrap receipts without issuing any work."""
    from database_creation_worker import executor
    executor(managed)
    with control_store.connect() as db:
        request = db.execute('SELECT approval_request_id FROM db_creation_phase_plans WHERE id=?',
                             (plan['id'],)).fetchone()
    if request is None or not request['approval_request_id']:
        raise core.OperationError('STALE_PLAN')
    operator._observation_request_id = request['approval_request_id']
    token = _observation.set((managed,root['id'],plan['operation_id']))
    try:
        operation = core.inspect(plan['operation_id'],actor=root['actor'])
        if operation['state'] != 'UNKNOWN' or not any(
                step['step'] == 'verify' and step['state'] == 'UNKNOWN' for step in operation['steps']):
            return operation
        operator._observation_operation = operation
        guard_observation(operator,operation)
        operator.check_receipt(operation)
        # Observe real health before opening any fixed private transport.
        for child in operator.group('reconcile_polaris'):
            operator.observe_child(child)
            for health in child['spec'].get('healthChecks',[]):
                operator.observe_health(health)
        operator.start_transports(operation)
        try:
            return core.reconcile_from_observer(plan['operation_id'],actor=root['actor'],
                observer=lambda p:operator.reconcile(p,'verify'))
        except core.OperationError as error:
            if error.code not in ('PRINCIPAL_STOPPED','ADMIN_REQUIRED'):
                raise
            # STOP permits completed read observations, never canonical final CAS.
            operator.observe_completed_verify(operation)
            return dict(state='UNKNOWN')
    finally:
        operator.close_transports()
        _observation.reset(token)


def _observe(managed,root,plan,operator,*,plan_completed=False):
    """No launches or activation. Classify original durable evidence, never absence."""
    operation = core.inspect(plan['operation_id'],actor=root['actor'])
    operator.check_receipt(operation)
    if not operator.receipt.get('verification_complete'):
        with control_store.connect() as db:
            original_unknown(db,operation['id'])
    # Known owner receipts may positively acknowledge an activation whose IPC
    # response was lost. Validate real HTTP identity before preserving that ACK.
    headers = runtime_profile.private_json(managed.config.value['executor_auth_file'])
    import urllib.request
    import database_creation_bridge as bridge
    def observe_role(role,saved):
        owner = saved['owner']
        record = managed.config.owner(role)
        if record['owner_instance_id'] != owner['owner_instance_id']:
            saved['state'] = 'UNKNOWN'
            saved['leaves'] = {}
            operator.save()
            return
        original_request_id = operator.receipt['verify_declaration']['original_request_id']
        owner_path = Path(managed.config.value['owners'][role]['directory'])/'activations'/(
            saved['activation_id']+'.json')
        # Inspect only this known owner's exact original private journal. An
        # absent file proves nothing once the parent has written its send intent.
        if owner_path.exists():
            from database_creation_worker import descriptor
            original_request = dict(id=original_request_id)
            owner_journal = runtime_profile.private_json(owner_path)
            bridge.exact(owner_journal,{'descriptor','stop','start','readiness','state'})
            if owner_journal['descriptor'] != descriptor(original_request,root,plan,operator,role):
                raise core.OperationError('STALE_PLAN')
            states = {}
            for leaf in ('stop','start','readiness'):
                item = owner_journal[leaf]
                if (type(item) is not dict or set(item)-{'state','intent_id'} or 'state' not in item
                        or item['state'] not in ('ACK','UNSENT')
                        or item['state']=='ACK' and not bridge.text(item.get('intent_id'))):
                    states[leaf] = 'UNKNOWN'
                else:
                    if item['state']=='ACK' or 'intent_id' in item:
                        bridge.request_id(item['intent_id'])
                    states[leaf] = item['state']
            if states['start']=='ACK' and states['stop']!='ACK' or states['readiness']=='ACK' and states['start']!='ACK':
                states = {leaf:'UNKNOWN' for leaf in states}
            saved['leaves'] = states
            if 'UNKNOWN' in states.values():
                saved['state'] = 'UNKNOWN'
            elif all(v=='UNSENT' for v in states.values()):
                saved['state'] = 'UNSENT'
            elif all(v=='ACK' for v in states.values()):
                saved['state'] = 'INTENT'  # requires the actual readback below
            else:
                saved['state'] = 'PARTIAL'
            operator.save()
        elif saved['state'] != 'UNSENT':
            saved['state'],saved['leaves'] = 'UNKNOWN',{}
            operator.save()
        states = saved.get('leaves',{})
        if saved['state'] == 'UNSENT':
            # A parent UNSENT record proves no IPC was sent. It does not prove
            # the originally approved old child still belongs to this owner.
            try:
                origin = managed.config.value['origins'][role]
                expected = previous_attestation(managed.config,root,plan,operator,role)
                instance = operator.http(urllib.request.Request(origin+creations.COLLECTION+'/runtime-instance',headers=headers))
                actual = operator.http(urllib.request.Request(origin+'/api/v1/runtime',headers=headers))
                if (record['runtime_instance_id'] != owner['old_runtime_instance_id'] or
                        instance != dict(version=1,environment_id=managed.config.enrollment.environment_id,
                            owner_role=role,owner_instance_id=owner['owner_instance_id'],
                            runtime_instance_id=owner['old_runtime_instance_id'],runtime_attestation=expected)
                        or actual != expected):
                    raise ValueError()
            except Exception:
                saved['state'],saved['leaves'] = 'UNKNOWN',{}
                operator.save()
        elif saved['state']=='PARTIAL':
            expected_id = owner['candidate_runtime_instance_id'] if states.get('start')=='ACK' else owner['old_runtime_instance_id']
            if states.get('start') != 'ACK':
                previous_attestation(managed.config,root,plan,operator,role)
            if record['runtime_instance_id'] != expected_id:
                saved['state'],saved['leaves'] = 'UNKNOWN',{}
                operator.save()
        response = managed.ipc(role,'GET_ACTIVATION',original_request_id,saved['activation_id'])
        if owner_path.exists() and states and all(v=='ACK' for v in states.values()):
            origin = managed.config.value['origins'][role]
            instance = operator.http(urllib.request.Request(origin+creations.COLLECTION+'/runtime-instance',headers=headers))
            attestation = operator.http(urllib.request.Request(origin+'/api/v1/runtime',headers=headers))
            expected = operator.config['managed_handoff']['runtime_attestation']
            if (instance == dict(version=1,environment_id=managed.config.enrollment.environment_id,
                    owner_role=role,owner_instance_id=owner['owner_instance_id'],
                    runtime_instance_id=owner['candidate_runtime_instance_id'],runtime_attestation=expected)
                    and attestation==expected and response['runtime_instance_id']==owner['candidate_runtime_instance_id']
                    and response.get('previous_runtime_instance_id')==owner['old_runtime_instance_id']):
                saved.update(state='ACK',response=response)
                operator.save()
    for role,saved in operator.receipt.get('runtime_activations',{}).items():
        try:
            observe_role(role,saved)
        except Exception:
            saved['state'],saved['leaves'] = 'UNKNOWN',{}
            operator.save()
    if operator.receipt.get('verification_complete') and not plan_completed:
        return None
    with auth.transaction() as db:
        op = original_unknown(db,operation['id'])
        auth.current_authority(managed.config.enrollment,managed.config.enrollment.executor_id)
        leaves = classify(operator)
        frozen = dict(version=1,purpose='VERIFY_RECOVERY',creation_id=root['id'],
            operation_id=operation['id'],original_plan_id=plan['id'],candidate=operator.binding,
            original_attempt=operator.receipt['verify_declaration']['original_claim_id'],canonical_attempt=op['attempt_id'],
            owner_journals=owner_journal_digests(managed.config,root,plan,operator),
            journal_path=str(operator.receipt_path),
            journal_digest=digest(canonical(operator.receipt)),leaves=leaves,
            transports=operator.config.get('managed_transport',{}).get('endpoints',{}),
            executable='UNKNOWN' not in leaves.values())
        value = digest(canonical(frozen))
        directory = managed.config.preparation().output_root/root['id']/'recovery'
        directory.mkdir(mode=0o700,exist_ok=True)
        path = directory/(value+'.json')
        if path.exists() and runtime_profile.private_json(path) != frozen:
            raise core.OperationError('STALE_PLAN')
        if not path.exists():
            save_private(path,frozen)
        existing = db.execute("SELECT id FROM db_creation_phase_plans WHERE creation_id=? AND purpose='VERIFY_RECOVERY' AND recovery_digest=?",
                              (root['id'],value)).fetchone()
        pid = existing[0] if existing else creations._attach_plan(db,managed.config.enrollment,root['id'],operation['id'],
            private_ref=str(path),public_summary=dict(code='RECOVERY_READY' if frozen['executable'] else 'RECONCILIATION_REQUIRED'),
            purpose='VERIFY_RECOVERY',recovery_digest=value)
        complete_reconcile(db,root,plan,dict(plan_id=pid))
    return dict(state='AWAITING_RECOVERY_APPROVAL' if frozen['executable'] else 'UNKNOWN',plan_id=pid,recovery_digest=value)


def execute(managed,rid):
    from database_creation_worker import resolve,executor
    request,root,plan,operator = resolve(managed,rid)
    permit = claim(managed,rid,executor(managed))
    operation = core.inspect(plan['operation_id'],actor=root['actor'])
    operator.check_receipt(operation)
    frozen = runtime_profile.private_json(permit.recovery_path)
    path = intent_path(permit)
    with core._operator_gate(),delegated(permit):
        with auth.transaction() as db:
            guard(db,operation['id'])
        if path.exists():
            # Crash/repeated dispatch cannot start another recovery attempt.
            raise core.OperationError('RECONCILIATION_REQUIRED')
        if digest(canonical(operator.receipt)) != frozen['journal_digest'] or classify(operator) != frozen['leaves']:
            raise core.OperationError('STALE_PLAN')
        save_private(path,dict(version=1,request_id=rid,claim_id=permit.claim_id,
            original_operation_id=operation['id'],recovery_digest=permit.recovery_digest,state='RUNNING'))
        assert_original(operation)
        try:
            operator.verify(operation)
            with auth.transaction() as db:
                request = dict(guard(db,operation['id']))
        except BaseException:
            operator.close_transports()
            raise
    # The observer takes the existing flock itself. Execution authority ends
    # before reconciliation; the original UNKNOWN step was never reset.
    token = _observation.set((managed,root['id'],operation['id']))
    try:
        with _observing_verified(permit,request),observation_request(request):
            result = core.reconcile_from_observer(operation['id'],actor=root['actor'],
                observer=lambda p:operator.reconcile(p,'verify'))
    finally:
        _observation.reset(token)
        operator.close_transports()
    save_private(path,dict(version=1,request_id=rid,claim_id=permit.claim_id,
        original_operation_id=operation['id'],recovery_digest=permit.recovery_digest,state=result['state']))
    return result
