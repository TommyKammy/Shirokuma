"""Fixed one-shot managed P5 product worker; inputs are durable IDs only."""
import argparse
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import sys

import control_store
import database_creation_auth as auth
import database_creation_bridge as bridge
import database_creation_preparation as preparation
import database_creations as creations
import request_auth
import runtime_profile
import warehouse_operations as core
from warehouse_operator import Operator, canonical, digest


_callback_stage = ContextVar('callback_failure_stage', default='OTHER')
_CALLBACK_STAGES = frozenset(('runtime-profile','configure','observe','decode','resolve','receipt',
    'authority-select','authority','assert-original','activation-journal','leaf-intent',
    'descriptor','sealed-descriptor'))
_CALLBACK_EXCEPTIONS = {cls: cls.__name__ for cls in (
    ValueError, TypeError, KeyError, AttributeError, OSError, FileNotFoundError,
    PermissionError, RuntimeError, core.OperationError, auth.AuthorityLost)}
_CALLBACK_CODES = frozenset(('STALE_PLAN','RECONCILIATION_REQUIRED','OPERATOR_UNAVAILABLE',
    'AUTHORIZATION_UNAVAILABLE','ADMIN_REQUIRED','CREATION_NOT_FOUND','CREATION_BUSY',
    'PRINCIPAL_STOPPED','OBSERVATION_ONLY','RECOVERY_SCOPE_VIOLATION','REVISION_CONFLICT',
    'INVALID_REQUEST','UNAPPLIED_CHANGES'))


def _log_callback_failure(stage, error):
    """Best effort fixed metadata only, beneath an existing private profile root."""
    try:
        profile = runtime_profile.current()
        if profile is None:
            explicit = os.environ.get('SHIROKUMA_RUNTIME_PROFILE')
            if not explicit or not Path(explicit).is_absolute():
                return
            profile = runtime_profile.load(explicit)
        root = bridge.directory(profile.state_dir).resolve()
        path = root/'callback-failures.jsonl'
        if path.is_symlink():
            return
        if path.exists():
            runtime_profile.private_file(path)
        code = error.code if type(error) in (core.OperationError,auth.AuthorityLost) else None
        record = dict(stage=stage if type(stage) is str and stage in _CALLBACK_STAGES else 'OTHER',
            exception=_CALLBACK_EXCEPTIONS.get(type(error),'OTHER'),
            code=code if type(code) is str and code in _CALLBACK_CODES else None)
        line = (json.dumps(record,separators=(',',':'))+'\n').encode('ascii')
        fd = os.open(path,os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
        try:
            info = os.fstat(fd)
            if stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600:
                os.write(fd,line)
        finally:
            os.close(fd)
    except Exception:
        pass


def executor(managed):
    managed.require()
    with auth.transaction() as db:
        auth.current_authority(managed.config.enrollment,managed.config.enrollment.executor_id)
    principal = request_auth.authenticate_principal(runtime_profile.private_json(
        managed.config.value['executor_auth_file']))
    auth._executor(managed.config.enrollment,principal)
    return principal


def request_root(managed, rid, *, observation=False):
    bridge.request_id(rid)
    managed.require()
    config = managed.config.enrollment
    with control_store.connect() as db:
        row = db.execute('SELECT * FROM db_creation_requests WHERE id=?',(rid,)).fetchone()
        if row is None or row['environment_id'] != config.environment_id:
            raise core.OperationError('CREATION_NOT_FOUND',404)
        request = dict(row)
        root = dict(creations._root(db,config,row['creation_id']))
        if (root['enrollment_id'] != config.enrollment_id or root['enrollment_revision'] != config.revision
                or root['warehouse_id'] != config.warehouse_id):
            raise core.OperationError('STALE_PLAN')
    # Authority loss must be recorded before profile-byte validation can fail.
    with auth.transaction():
        auth.current_authority(config,config.executor_id)
        if not observation:
            auth.current_authority(config,root['actor'])
    return request,root


def resolve(managed, rid, *, observation=False):
    request,root = request_root(managed,rid,observation=observation)
    with control_store.connect() as db:
        selected = db.execute('SELECT operation_id FROM db_creation_phase_plans WHERE id=?',(request['plan_id'],)).fetchone() if request['plan_id'] else None
        plan = db.execute("SELECT * FROM db_creation_phase_plans WHERE creation_id=? AND purpose='APPLY' AND operation_id=?",
            (root['id'],selected['operation_id'])).fetchone() if selected else None
        if plan is None and request['action'] == 'RECONCILE':
            plan = db.execute("SELECT * FROM db_creation_phase_plans WHERE creation_id=? AND purpose='APPLY' ORDER BY phase DESC,sequence DESC LIMIT 1",
                              (root['id'],)).fetchone()
    if plan is None:
        return request,root,None,None
    plan = dict(plan)
    c = managed.config.preparation()
    directory = c.output_root/root['id']/plan['phase']/str(plan['sequence'])
    journal = preparation.Preparation._journal(directory/'journal.json')
    preparation.Preparation._validate_seal(directory,journal,plan['private_ref'])
    candidate_config = runtime_profile.private_json(directory/'operator.json')
    if (candidate_config.get('managed_transport') and
            candidate_config.get('preparation_inputs',{}).get(managed.config.path) != managed.config.binding):
        config = managed.config.enrollment
        auth.stop_lost_authority(config,config.executor_id,reason='ENROLLMENT_CHANGED')
        raise core.OperationError('STALE_PLAN')
    operator = Operator(directory/'operator.json')
    if operator.binding != journal['binding']:
        raise core.OperationError('STALE_PLAN')
    return request,root,plan,operator


def validate_candidate_profile(config, path, *, hello=None):
    """A replacement child accepts only an already sealed, linked full candidate."""
    p = Path(path)
    output = config.preparation().output_root
    if not p.is_relative_to(output) or p.name != 'runtime-profile.json':
        raise ValueError('Sealed candidate profile required')
    directory = p.parent
    journal = preparation.Preparation._journal(directory/'journal.json')
    with control_store.connect() as db:
        plans = db.execute("SELECT * FROM db_creation_phase_plans WHERE purpose='APPLY' AND phase='full' AND private_ref=?",
                           ('preparation:'+digest(canonical(journal)),)).fetchall()
    if len(plans) != 1:
        raise ValueError('Linked immutable candidate required')
    preparation.Preparation._validate_seal(directory,journal,plans[0]['private_ref'])
    operator = Operator(directory/'operator.json')
    if operator.config.get('runtime_profile') != path or operator.binding != journal['binding']:
        raise ValueError('Sealed profile differs')
    handoff = operator.config['managed_handoff']
    roles = (hello['owner_role'],) if hello else ('workbench','native')
    for role in roles:
        owner = handoff['owners'][role]
        record = config.owner(role)
        if (record['owner_instance_id'] != owner['owner_instance_id'] or
                record['runtime_instance_id'] != owner['candidate_runtime_instance_id'] or
                hello and (hello['owner_instance_id'] != owner['owner_instance_id'] or
                           hello['runtime_instance_id'] != owner['candidate_runtime_instance_id'])):
            raise ValueError('Approved successor owner required')
        with control_store.connect() as db:
            root = dict(creations._root(db,config.enrollment,plans[0]['creation_id']))
        if (root['enrollment_id'] != config.enrollment.enrollment_id or
                root['enrollment_revision'] != config.enrollment.revision or root['warehouse_id'] != config.enrollment.warehouse_id):
            raise ValueError('Original enrollment required')
        aid = activation_id(root,plans[0],operator,role)
        journal_path = Path(config.value['owners'][role]['directory'])/'activations'/(aid+'.json')
        activation_record = bridge.exact(runtime_profile.private_json(journal_path),{'descriptor','stop','start','readiness','state'})
        sealed = activation_record['descriptor']
        if (sealed != descriptor(dict(id=sealed['request_id']),root,plans[0],operator,role) or
                runtime_profile.private_json(Path(config.value['state_dir'])/'activation-plans'/sealed['request_id']/(aid+'.json')) != sealed
                or activation_record['stop']['state'] != 'ACK'
                or activation_record['start']['state'] not in ('INTENT','ACK')):
            raise ValueError('Sealed activation startup required')
        with control_store.connect() as db:
            original = db.execute('SELECT * FROM db_creation_requests WHERE id=?',(sealed['request_id'],)).fetchone()
            if original is None or original['plan_id'] != plans[0]['id'] or not original['claim_id']:
                raise ValueError('Original approved activation required')
    return operator


def accepted_head(config):
    """Use the latest accepted full activation, or the explicit enrolled base."""
    with control_store.connect() as db:
        plans = db.execute("""SELECT p.* FROM db_creation_phase_plans p JOIN db_creations c ON c.id=p.creation_id
            WHERE p.phase='full' AND p.purpose='APPLY' AND p.approval_request_id IS NOT NULL
            AND c.environment_id=? AND c.warehouse_id=? ORDER BY c.created_at DESC,p.sequence DESC""",
            (config.enrollment.environment_id,config.enrollment.warehouse_id)).fetchall()
    for plan in plans:
        directory = config.preparation().output_root/plan['creation_id']/plan['phase']/str(plan['sequence'])
        candidate = runtime_profile.private_json(directory/'operator.json')
        owners = candidate.get('managed_handoff',{}).get('owners',{})
        if not any(config.owner(role)['runtime_instance_id'] == owner['candidate_runtime_instance_id']
                   for role,owner in owners.items()):
            continue
        operator = validate_candidate_profile(config,str(directory/'runtime-profile.json'))
        for role in ('workbench','native'):
            aid = activation_id(dict(id=plan['creation_id']),plan,operator,role)
            journal = runtime_profile.private_json(Path(config.value['owners'][role]['directory'])/'activations'/(aid+'.json'))
            if journal['state'] != 'ACK' or any(journal[k]['state'] != 'ACK' for k in ('stop','start','readiness')):
                raise ValueError('Accepted runtime activation incomplete')
        return operator.config['managed_handoff']['runtime_attestation'],{
            role:owner['candidate_runtime_instance_id'] for role,owner in owners.items()}
    return runtime_profile._public_identity(config.base),None


@dataclass(frozen=True)
class AppliedMaterial:
    bundle: Path
    private: Path
    profile: str
    endpoints: dict
    pin: dict


def applied_material(config, *, db=None):
    """Resolve material from the canonical APPLIED head, never runtime observation.

    Historical seals prove immutable bytes without requiring their own parent
    head to still be current. Only this selection is fenced against controls.
    """
    if db is None:
        with control_store.connect() as connection:
            return applied_material(config, db=connection)
    c = config.preparation()
    e = config.enrollment
    head = db.execute('SELECT applied_generation_id FROM warehouse_controls WHERE warehouse_id=?',
                      (e.warehouse_id,)).fetchone()
    generation_id = head[0] if head else None
    generation = db.execute('SELECT * FROM warehouse_config_generations WHERE id=? AND warehouse_id=? AND state=\'APPLIED\'',
                            (generation_id,e.warehouse_id)).fetchone()
    if generation is None:
        raise core.OperationError('STALE_PLAN')
    plans = db.execute("""SELECT p.* FROM db_creation_phase_plans p
        JOIN db_creations c ON c.id=p.creation_id JOIN operations o ON o.id=p.operation_id
        WHERE p.generation_id=? AND p.phase='full' AND p.purpose='APPLY'
        AND c.environment_id=? AND c.warehouse_id=? AND c.enrollment_id=? AND c.enrollment_revision=?
        AND c.terminal_outcome='APPLIED' AND c.reservation_active=0 AND o.state='APPLIED'""",
        (generation_id,e.environment_id,e.warehouse_id,e.enrollment_id,e.revision)).fetchall()
    if not plans:
        from database_generation import load_bundle
        historical = db.execute('SELECT 1 FROM db_creation_phase_plans WHERE generation_id=?',
                                (generation_id,)).fetchone()
        if historical:
            prior = db.execute("""SELECT p.*,c.enrollment_revision FROM db_creation_phase_plans p
                JOIN db_creations c ON c.id=p.creation_id JOIN operations o ON o.id=p.operation_id
                WHERE p.generation_id=? AND p.phase='full' AND p.purpose='APPLY'
                AND c.environment_id=? AND c.warehouse_id=? AND c.enrollment_id=?
                AND c.enrollment_revision>0 AND c.enrollment_revision<?
                AND c.terminal_outcome='APPLIED' AND c.reservation_active=0 AND o.state='APPLIED'""",
                (generation_id,e.environment_id,e.warehouse_id,e.enrollment_id,e.revision)).fetchall()
            if len(prior) != 1:
                raise core.OperationError('STALE_PLAN')
            prior = dict(prior[0])
            directory = c.output_root/prior['creation_id']/'full'/str(prior['sequence'])
            journal = preparation.Preparation._journal(directory/'journal.json')
            preparation.Preparation._validate_seal(directory,journal,prior['private_ref'])
            sealed = runtime_profile.private_json(directory/'operator.json')
            old_path = sealed['managed_handoff']['enrollment_path']
            if sealed['preparation_inputs'].get(old_path) != digest(runtime_profile.private_file(old_path).read_bytes()):
                raise core.OperationError('STALE_PLAN')
            old = bridge.Configuration(old_path)
            old_c = old.preparation()
            if (old.enrollment != creations.Enrollment(e.environment_id,e.warehouse_id,e.enrollment_id,
                    prior['enrollment_revision'],old.enrollment.executor_id) or
                    old.value['foundation'] != config.value['foundation'] or
                    old.value['state_dir'] != config.value['state_dir'] or old_c.tofu_state != c.tofu_state or
                    sealed['tofu_state'] != str(c.tofu_state) or
                    sealed['state_lineage'] != config.value['foundation']['tofu_lineage'] or
                    json.loads(runtime_profile.private_file(c.tofu_state).read_bytes())['lineage'] != sealed['state_lineage']):
                raise core.OperationError('STALE_PLAN')
            # Reuse the full original seal/receipt/owner/activation validation.
            # Retained records are evidence; no historical owner is contacted.
            verified = applied_material(old,db=db)
            if (verified.pin.get('plan_id') != prior['id'] or
                    c.base_bundle != verified.bundle or c.base_private != verified.private or
                    config.value['base_runtime_profile'] != verified.profile or
                    dict(c.polaris_endpoints) != verified.endpoints):
                raise core.OperationError('STALE_PLAN')
        base = load_bundle(c.base_bundle)
        observed, owners = accepted_head(config)
        if (generation_id != base['manifest']['generation_id'] or
                generation_id != runtime_profile._public_identity(config.base)['generation_id'] or owners is not None or
                observed != runtime_profile._public_identity(config.base)):
            raise core.OperationError('STALE_PLAN')
        return AppliedMaterial(c.base_bundle,c.base_private,config.value['base_runtime_profile'],
            dict(c.polaris_endpoints),dict(generation_id=generation_id,base=True,
                profile=runtime_profile.private_binding(config.value['base_runtime_profile'])))
    if len(plans) != 1:
        raise core.OperationError('STALE_PLAN')
    plan = dict(plans[0])
    directory = c.output_root/plan['creation_id']/'full'/str(plan['sequence'])
    journal = preparation.Preparation._journal(directory/'journal.json')
    preparation.Preparation._validate_seal(directory,journal,plan['private_ref'])
    operator = validate_candidate_profile(config,str(directory/'runtime-profile.json'))
    operation = core._view(db,plan['operation_id'],None,is_admin=True)
    if (journal['request_id'] != plan['preparation_request_id'] or journal['phase'] != 'full' or
            operator.binding != journal['binding'] or operator.binding['generation_id'] != generation_id or
            operator.binding['delivery_digest'] != plan['delivery_digest'] or
            operation['generation_id'] != generation_id or operation['delivery_digest'] != plan['delivery_digest'] or
            operation['manifest'] != operator.bundle['manifest']):
        raise core.OperationError('STALE_PLAN')
    receipt_path = control_store.root().resolve()/'warehouse-operator-receipts'/(plan['operation_id']+'.json')
    receipt = runtime_profile.private_json(receipt_path)
    completion = receipt['verification_complete']
    proof = completion['proof']
    evidence = 'generation-verified:'+digest(canonical(proof))
    handoff = operator.config['managed_handoff']
    if (receipt['actor'] != operation['actor'] or receipt['binding'] != operator.binding or proof['binding'] != operator.binding or
            proof['operation_id'] != plan['operation_id'] or proof['sql'] != receipt['sql'] or
            completion['evidence_ref'] != evidence or
            not any(s['step']=='verify' and s['state']=='SUCCEEDED' and s['evidence_ref']==evidence
                    for s in operation['steps'])):
        raise core.OperationError('STALE_PLAN')
    for role,owner in handoff['owners'].items():
        aid = activation_id(dict(id=plan['creation_id']),plan,operator,role)
        activation = runtime_profile.private_json(Path(config.value['owners'][role]['directory'])/'activations'/(aid+'.json'))
        readback = proof['runtime_readbacks'][role]
        if (activation['state'] != 'ACK' or any(activation[k]['state'] != 'ACK' for k in ('stop','start','readiness')) or
                readback['runtime_instance_id'] != owner['candidate_runtime_instance_id'] or
                readback['owner_instance_id'] != owner['owner_instance_id'] or
                readback['runtime_attestation'] != handoff['runtime_attestation']):
            raise core.OperationError('STALE_PLAN')
    return AppliedMaterial(directory/'bundle',directory/'private-input.json',str(directory/'runtime-profile.json'),
        operator.config['managed_transport']['endpoints'],dict(generation_id=generation_id,plan_id=plan['id'],
            operation_id=plan['operation_id'],private_ref=plan['private_ref'],seal=digest(canonical(journal)),
            binding=operator.binding,verification=digest(canonical(completion))))


@contextmanager
def authority(managed, request, *, authorization=False):
    principal = executor(managed)
    if request['action'] == 'RECOVER_VERIFY':
        import database_creation_recovery as recovery
        permit = recovery.claim(managed,request['id'],principal,authorization=authorization)
        with recovery.delegated(permit):
            yield permit
    else:
        if authorization and (not request['claim_id'] or request['dispatch_state'] != 'CLAIMED'):
            raise core.OperationError('STALE_PLAN')
        permit = auth.claim(managed.config.enrollment,principal,request['id'],
            owner_id=request['owner_id'] if authorization else managed.hello['owner_instance_id'])
        with auth.delegated(permit):
            yield permit


def activation_id(root, plan, operator, role):
    return digest(canonical([root['id'],plan['id'],operator.binding['generation_id'],role]))


def descriptor(request,root,plan,operator,role):
    handoff = operator.config['managed_handoff']
    owner = handoff['owners'][role]
    profile = operator.config['runtime_profile']
    return dict(version=1,request_id=request['id'],activation_id=activation_id(root,plan,operator,role),owner_role=role,
        owner_instance_id=owner['owner_instance_id'],old_runtime_instance_id=owner['old_runtime_instance_id'],
        candidate_runtime_instance_id=owner['candidate_runtime_instance_id'],profile_path=profile,
        profile_binding=runtime_profile.private_binding(profile),runtime_attestation=handoff['runtime_attestation'],
        original_operation_id=plan['operation_id'],original_plan_id=plan['id'],candidate_id=handoff['candidate_id'])


def seal_activation_plans(managed,request,root,plan,operator):
    from warehouse_operator import save_private
    base = Path(managed.config.value['state_dir'])/'activation-plans'
    base.mkdir(mode=0o700,exist_ok=True)
    target = base/request['id']
    target.mkdir(mode=0o700,exist_ok=True)
    bridge.directory(str(base))
    bridge.directory(str(target))
    for role in ('workbench','native'):
        value = descriptor(request,root,plan,operator,role)
        path = target/(value['activation_id']+'.json')
        if path.exists():
            if runtime_profile.private_json(path) != value:
                raise core.OperationError('STALE_PLAN')
        else:
            save_private(path,value)


def activation_journal(managed,request,root,plan,operator,role):
    expected = descriptor(request,root,plan,operator,role)
    path = Path(managed.config.value['owners'][role]['directory'])/'activations'/(
        expected['activation_id']+'.json')
    journal = bridge.exact(runtime_profile.private_json(path),{'descriptor','stop','start','readiness','state'})
    if journal['descriptor'] != expected:
        raise core.OperationError('STALE_PLAN')
    return journal


def activation(managed, value, *, authorize=False):
    keys = {'version','request_id','activation_id','owner_role','owner_instance_id'}
    if authorize:
        keys |= {'leaf','intent_id'}
    bridge.exact(value,keys)
    if type(value['version']) is not int or value['version'] != 1:
        raise ValueError('Fixed activation version required')
    role = value['owner_role']
    if role != managed.hello['owner_role'] or value['owner_instance_id'] != managed.hello['owner_instance_id']:
        raise ValueError('Known activation owner required')
    _callback_stage.set('resolve')
    request,root,plan,operator = resolve(managed,value['request_id'])
    if operator is None or operator.phase != 'full' or not operator.config.get('managed_handoff'):
        raise ValueError('Managed full candidate required')
    if value['activation_id'] != activation_id(root,plan,operator,role):
        raise ValueError('Original activation identity required')
    _callback_stage.set('receipt')
    operation = core.inspect(plan['operation_id'],actor=root['actor'])
    operator.check_receipt(operation)
    handoff = operator.config['managed_handoff']
    owner = handoff['owners'][role]
    if owner['owner_instance_id'] != value['owner_instance_id']:
        raise ValueError('Owner generation changed')
    _callback_stage.set('authority-select')
    authority_request = request
    if operation['state'] == 'UNKNOWN' and request['action'] == 'APPROVE':
        # The IPC ID and owner journal remain the ORIGINAL APPLY request. A
        # fresh recovery approval authorizes only its frozen remaining leaves.
        with control_store.connect() as db:
            candidates = db.execute("""SELECT r.* FROM db_creation_requests r
                JOIN db_creation_phase_plans p ON p.id=r.plan_id WHERE p.operation_id=?
                AND r.action='RECOVER_VERIFY' AND r.approval_state='VALID' AND r.dispatch_state='CLAIMED'""",
                (operation['id'],)).fetchall()
        if len(candidates) != 1:
            raise core.OperationError('RECONCILIATION_REQUIRED')
        authority_request = dict(candidates[0])
    _callback_stage.set('authority')
    with authority(managed,authority_request,authorization=True):
        _callback_stage.set('assert-original')
        if authority_request['action'] != 'RECOVER_VERIFY':
            operator.assert_intent(operation,'verify')
        else:
            import database_creation_recovery as recovery
            recovery.assert_original(operation)
        _callback_stage.set('activation-journal')
        journal = operator.receipt.get('runtime_activations',{}).get(role)
        if not journal or journal['activation_id'] != value['activation_id'] or journal['owner'] != owner:
            raise ValueError('Predeclared original activation required')
        if authorize:
            if value['leaf'] not in ('stop','start','readiness'):
                raise ValueError('Fixed activation leaf required')
            bridge.request_id(value['intent_id'])
            owner_journal = activation_journal(managed,request,root,plan,operator,role)
            if owner_journal[value['leaf']] != dict(state='INTENT',intent_id=value['intent_id']):
                raise core.OperationError('RECONCILIATION_REQUIRED')
            # Owner journals UNSENT/INTENT/ACK. Python separately persists the
            # current authority fence before the owner may perform each effect.
            _callback_stage.set('leaf-intent')
            operator.leaf_intent(dict(kind='managed-activation',activation_id=value['activation_id'],
                leaf=value['leaf'],intent_id=value['intent_id']),plan=operation)
    _callback_stage.set('descriptor')
    profile = operator.config['runtime_profile']
    if authorize:
        return dict(version=1,state='AUTHORIZED',request_id=request['id'],activation_id=value['activation_id'],
            owner_role=role,owner_instance_id=owner['owner_instance_id'],leaf=value['leaf'],intent_id=value['intent_id'],
            candidate_runtime_instance_id=owner['candidate_runtime_instance_id'],
            profile_binding=runtime_profile.private_binding(profile))
    result = descriptor(request,root,plan,operator,role)
    _callback_stage.set('sealed-descriptor')
    sealed = Path(managed.config.value['state_dir'])/'activation-plans'/request['id']/(value['activation_id']+'.json')
    if runtime_profile.private_json(sealed) != result:
        raise core.OperationError('STALE_PLAN')
    return result


def dispatch(managed, rid, *, observe=False):
    request,root = request_root(managed,rid,observation=True)
    observing = observe or request['action'] == 'RECONCILE'
    if request['action'] == 'RECONCILE' and request['dispatch_state'] == 'COMPLETE':
        return dict(state='COMPLETE',request_id=rid)
    if not observing:
        request_root(managed,rid)
    principal = executor(managed)
    request,root,plan,operator = resolve(managed,rid,observation=True) if observing else resolve(managed,rid)
    if request['action'] == 'CREATE':
        return dict(state='REGISTERED',request_id=rid)
    if observe or request['action'] == 'RECONCILE':
        if operator is None:
            return dict(state='UNKNOWN',request_id=rid)
        import database_creation_recovery as recovery
        with recovery.observation_request(request):
            result = recovery.observe(managed,root,plan,operator)
            with auth.transaction() as db:
                recovery.complete_reconcile(db,root,plan,result)
            return result
    if request['action'] in ('PREPARE_PLAN','REPLAN'):
        return preparation.Preparation(managed.config.preparation()).prepare(rid,principal,
            owner_id=managed.hello['owner_instance_id'])
    if request['action'] == 'RECOVER_VERIFY':
        import database_creation_recovery as recovery
        return recovery.execute(managed,rid)
    if request['action'] != 'APPROVE' or operator is None:
        raise core.OperationError('STALE_PLAN')
    with authority(managed,request):
        operation = core.inspect(plan['operation_id'],actor=root['actor'])
        # A duplicate claim never turns UNKNOWN into a fresh apply attempt.
        if operation['state'] != 'AWAITING_APPLY':
            return dict(state=operation['state'],request_id=rid)
        operator.bind(operation)
        operator.check_receipt(operation)
        operator.authorize(root['actor'])
        if operator.predecessor_id:
            operator.prepared_predecessor(operation)
        if not any(s['step']=='apply_secrets' and s['state']=='SUCCEEDED' for s in operation['steps']):
            operator.check_state_before_apply()
        operator.validate_saved_plan()
        try:
            return core.apply(operation['id'],actor=root['actor'],expected_revision=request['operation_revision'],
                delivery_digest=operator.binding['delivery_digest'],authorize=operator.authorize,
                observe_secrets=operator.observe_secrets,execute_step=operator.execute_step,verify=operator.verify)
        finally:
            operator.close_transports()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('dispatch','observe','resolve-activation','authorize-leaf','release-principal'))
    parser.add_argument('request_id',nargs='?')
    parser.add_argument('--enrollment')
    parser.add_argument('--principal')
    parser.add_argument('--key')
    args = parser.parse_args()
    callback = args.command in ('resolve-activation','authorize-leaf')
    diagnostic_token = _callback_stage.set('runtime-profile')
    try:
        if args.command == 'release-principal':
            if args.request_id is not None or not all((args.enrollment,args.principal,args.key)):
                raise ValueError('Explicit local release inputs required')
            import database_creation_recovery as recovery
            result = recovery.release_local(args.enrollment,args.principal,args.key)
            print(json.dumps(dict(version=1,state='RELEASED',request_id=result['request_id']),separators=(',',':')))
            return 0
        if any((args.enrollment,args.principal,args.key)):
            raise ValueError('Local release inputs only')
        runtime_profile.configure_process()
        _callback_stage.set('configure')
        managed = bridge.configure('worker')
        if managed is None:
            raise ValueError('Inherited managed identity required')
        if args.command in ('dispatch','observe'):
            if args.command == 'observe':
                _callback_stage.set('observe')
            result = dispatch(managed,bridge.request_id(args.request_id),observe=args.command=='observe')
            # Ordinary worker stdout never includes private operator/SQL material.
            result = dict(version=1,state=result.get('state','COMPLETE'),request_id=args.request_id)
        else:
            _callback_stage.set('decode')
            if args.request_id is not None:
                raise ValueError('Fixed activation stdin required')
            raw = sys.stdin.buffer.readline(65537)
            if len(raw)>65536:
                raise ValueError('Fixed activation message required')
            result = activation(managed,bridge.decode(raw),authorize=args.command=='authorize-leaf')
        print(json.dumps(result,separators=(',',':')))
        return 0
    except Exception as error:
        if callback or args.command == 'observe':
            _log_callback_failure(_callback_stage.get(),error)
        print('OPERATOR_UNAVAILABLE',file=sys.stderr)
        return 1
    finally:
        _callback_stage.reset(diagnostic_token)


if __name__ == '__main__':
    sys.exit(main())
