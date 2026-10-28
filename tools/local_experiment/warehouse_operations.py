"""Fixed warehouse configuration protocol. All external effects are operator callbacks.

The ledger holds public descriptors and receipts only. RUNNING is a durable intent,
not permission to replay: only explicit trusted observation can resolve uncertainty.
"""
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from typing import Optional
import copy
import fcntl
import json
import re
import time
import uuid

import control_store
import data_management as dm
import warehouse_admission as admission


STEPS = ('publish_snapshot', 'apply_secrets', 'reconcile_storage', 'reconcile_polaris', 'reconcile_trino')
_ALL_STEPS = STEPS + ('verify',)


class OperationError(RuntimeError):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)


@dataclass(frozen=True)
class ReconciliationEvidence:
    step: str
    outcome: str
    evidence_ref: str
    basis: str


@dataclass(frozen=True)
class ReadonlyVerifyPermit:
    """A single standalone claim; never a P5 or mutation authority."""
    operation_id: str
    actor: str
    warehouse_id: str
    attempt_id: str
    revision: int
    delivery_digest: str
    config_digest: str
    receipt_digest: str
    original_operation_digest: str
    previous_attempt_id: Optional[str]


_READONLY_ROUTE = 'operator:verify-readonly'


def _readonly_eligible(db, plan):
    _p5_guard(db, plan['id'], plan['warehouse_id'])
    request = db.execute('SELECT * FROM operation_requests WHERE operation_id=?', (plan['id'],)).fetchone()
    control = admission.control(db, plan['warehouse_id'])
    if (plan['phase'] != 'full' or plan['predecessor_id'] is not None
            or request['actor'] != plan['actor']
            or plan['state'] != 'UNKNOWN' or plan['steps'][-1]['state'] != 'UNKNOWN'
            or any(s['state'] != 'SUCCEEDED' for s in plan['steps'][:-1])
            or not request['mutation_started'] or request['secret_after'] is None
            or request['observation_failure'] is not None
            or control['state'] != 'UNKNOWN' or control['operation_id'] != plan['id']
            or db.execute('SELECT 1 FROM db_creation_phase_plans WHERE operation_id=?', (plan['id'],)).fetchone()):
        raise OperationError('READONLY_VERIFY_INELIGIBLE')
    _check_registry(db, plan['manifest']['spec'])
    return request


def check_readonly_claim(permit, *, current=True):
    if type(permit) is not ReadonlyVerifyPermit:
        raise OperationError('READONLY_VERIFY_PERMIT_REQUIRED', 403)
    with _transaction() as db:
        plan = _view(db, permit.operation_id, permit.actor)
        _p5_guard(db, plan['id'], plan['warehouse_id'])
        _check_registry(db, plan['manifest']['spec'])
        audit = db.execute('SELECT * FROM warehouse_action_requests WHERE id=?', (permit.attempt_id,)).fetchone()
        if (audit is None or audit['route'] != _READONLY_ROUTE or audit['actor'] != permit.actor
                or audit['operation_id'] != permit.operation_id or audit['payload'] != _json(permit.__dict__)
                or plan['delivery_digest'] != permit.delivery_digest or plan['warehouse_id'] != permit.warehouse_id):
            raise OperationError('READONLY_VERIFY_CLAIM_CHANGED')
        if (plan['phase'] != 'full' or plan['predecessor_id'] is not None
                or db.execute('SELECT 1 FROM db_creation_phase_plans WHERE operation_id=?', (plan['id'],)).fetchone()
                or any(s['state'] != 'SUCCEEDED' for s in plan['steps'][:-1])):
            raise OperationError('READONLY_VERIFY_INELIGIBLE')
        control = admission.control(db, plan['warehouse_id'])
        if plan['state'] != 'APPLIED' and (control['operation_id'] != plan['id']
                or control['state'] not in ('UNKNOWN', 'APPLYING', 'DRAINING')):
            raise OperationError('READONLY_VERIFY_CLAIM_CHANGED')
        if current:
            request = _readonly_eligible(db, plan)
            if request['attempt_id'] != permit.attempt_id or plan['revision'] != permit.revision:
                raise OperationError('READONLY_VERIFY_CLAIM_CHANGED')
    return plan


def verify_readonly(operation_id, *, actor, expected_revision, delivery_digest, receipt_digest, operator):
    """Claim once under the real locks; uncertainty never authorizes a replacement."""
    import warehouse_operator
    import database_creation_auth
    import database_creation_recovery
    if (database_creation_auth._permit.get() is not None or database_creation_recovery.active()
            or database_creation_recovery.observing()):
        raise OperationError('READONLY_VERIFY_INELIGIBLE')
    if type(operator) is not warehouse_operator.Operator:
        raise OperationError('INVALID_OPERATOR', 400)
    with _operator_gate(), admission.lifecycle_gate(exclusive=True, deadline=time.monotonic() + 10):
        _authorize(operator.authorize, actor)
        plan = inspect(operation_id, actor=actor)
        if type(expected_revision) is not int or expected_revision != plan['revision']:
            raise OperationError('REVISION_CONFLICT')
        if delivery_digest != plan['delivery_digest']:
            raise OperationError('DELIVERY_CONFLICT')
        with _transaction() as db:
            request = _readonly_eligible(db, plan)
            after = json.loads(request['secret_after'])
            if db.execute('SELECT 1 FROM warehouse_action_requests WHERE operation_id=? AND route=?',
                          (operation_id, _READONLY_ROUTE)).fetchone():
                raise OperationError('READONLY_VERIFY_HOLD')
        operator.validate_readonly_parent(plan, receipt_digest)
        _preflight(plan, operator.bundle, operator.observe_secrets, after=after, require_present=True)
        operator.verify_runtime_handoff(plan)
        with _transaction() as db:
            latest = _view(db, operation_id, actor)
            request = _readonly_eligible(db, latest)
            if latest['revision'] != expected_revision:
                raise OperationError('REVISION_CONFLICT')
            if db.execute('SELECT 1 FROM warehouse_action_requests WHERE operation_id=? AND route=?',
                          (operation_id, _READONLY_ROUTE)).fetchone():
                raise OperationError('READONLY_VERIFY_HOLD')
            permit = ReadonlyVerifyPermit(operation_id, actor, plan['warehouse_id'], str(uuid.uuid4()),
                expected_revision + 1, delivery_digest, operator.binding['config'], receipt_digest,
                warehouse_operator.digest(warehouse_operator.canonical(latest)), request['attempt_id'])
            changed = db.execute('UPDATE operation_requests SET attempt_id=?,revision=revision+1 WHERE operation_id=? AND revision=?',
                                 (permit.attempt_id, operation_id, expected_revision))
            if changed.rowcount != 1:
                raise OperationError('REVISION_CONFLICT')
            db.execute("INSERT INTO warehouse_action_requests VALUES (?,?,?,?,?,?,?,'REQUESTED')",
                (permit.attempt_id, operation_id, actor, _READONLY_ROUTE, operation_id,
                 _json(permit.__dict__), admission._now()))
        # A crash after claim, even before child creation, is a permanent HOLD.
        plan = latest
        operator.create_readonly_child(plan, permit)
        try:
            operator.verify(plan)
            operator.verify_runtime_handoff(plan)
            operator.guard_readonly(plan)
        finally:
            operator._readonly_permit = None
        return inspect(operation_id, actor=actor)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def _text(value):
    return isinstance(value, str) and 0 < len(value) <= 256 and not any(ord(c) < 32 for c in value)


def _receipt(value):
    # The adapter must return a public reference, never an error body or secret value.
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,255}', value) is not None


@contextmanager
def _transaction():
    import database_creation_auth
    with database_creation_auth.transaction() as db:
        yield db


@contextmanager
def _operator_gate():
    """Serialize operator lifetimes without obstructing query lifecycle locks."""
    deadline = time.monotonic() + .2
    with (control_store.root() / 'warehouse-operator.lock').open('a') as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise OperationError('WAREHOUSE_OPERATOR_BUSY') from None
                time.sleep(min(.02, remaining))
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _render(manifest):
    # YAML is an optional plan/operator dependency, never an import-time dependency.
    try:
        import database_generation
        result = database_generation.render(manifest['spec'])
        if result['manifest'] != manifest:
            raise ValueError()
        return result
    except (KeyError, TypeError, ValueError, OSError):
        raise OperationError('INVALID_GENERATION', 400) from None


def _bundle(bundle):
    try:
        if type(bundle) is not dict or set(bundle) != {'manifest', 'files', 'tofu'}:
            raise ValueError()
        expected = _render(bundle['manifest'])
        if expected != bundle:
            raise ValueError()
        return expected
    except (KeyError, TypeError, ValueError):
        raise OperationError('INVALID_GENERATION', 400) from None


def _observations(bundle, values):
    refs = bundle['tofu']['bootstrap_secret_refs'] + bundle['tofu']['deferred_secret_refs']
    ref_keys = {'namespace', 'name', 'version', 'keys'}
    keys = ref_keys | {'exists', 'uid', 'resource_version', 'immutable'}
    try:
        if type(values) is not list or len(values) != len(refs):
            raise ValueError()
        expected = {(ref['namespace'], ref['name']): ref for ref in refs}
        result, seen = [], set()
        for value in values:
            if type(value) is not dict or set(value) != keys or type(value['keys']) is not list:
                raise ValueError()
            identity = (value['namespace'], value['name'])
            if identity in seen or identity not in expected:
                raise ValueError()
            seen.add(identity)
            normalized = dict(value, keys=sorted(value['keys']))
            if {key: normalized[key] for key in ref_keys} != expected[identity]:
                raise ValueError()
            if value['exists'] is True:
                if value['immutable'] is not True or not _text(value['uid']) or not _text(value['resource_version']):
                    raise ValueError()
            elif value['exists'] is False:
                if any(value[key] is not None for key in ('uid', 'resource_version', 'immutable')):
                    raise ValueError()
            else:
                raise ValueError()
            result.append(normalized)
        return sorted(result, key=lambda row: (row['namespace'], row['name'], row['version']))
    except (KeyError, TypeError, ValueError):
        raise OperationError('INVALID_SECRET_OBSERVATIONS', 400) from None


def _check_registry(db, spec):
    """Compare desired configuration, never binding display/CAS or applied revisions."""
    # All registered bindings belong to the single approved LOCAL warehouse.
    # Omitting a database would silently remove its configuration; P2-5 cannot delete.
    if {row[0] for row in db.execute('SELECT database_id FROM catalog_bindings')} != set(spec['databases']):
        raise OperationError('STALE_REGISTRY')
    for key, expected in spec['databases'].items():
        database = db.execute('SELECT * FROM databases WHERE id=?', (key,)).fetchone()
        binding = db.execute('SELECT * FROM catalog_bindings WHERE database_id=?', (key,)).fetchone()
        if (database is None or binding is None or database['sql_catalog'] != expected['sql_catalog']
                or database['revision'] != expected['database_revision']
                or any(binding[field] != value for field, value in expected['binding'].items())):
            raise OperationError('STALE_REGISTRY')
        actual = [dict(row) for row in db.execute(
            'SELECT id,sql_name,purpose,revision FROM schemas WHERE database_id=? ORDER BY sql_name', (key,))]
        if actual != sorted(expected['schemas'], key=lambda row: row['sql_name']):
            raise OperationError('STALE_REGISTRY')
    import grant_management
    actual_grants = [row for row in grant_management.desired_projection(db)
                     if row['database_id'] in spec['databases']]
    if actual_grants != sorted(spec['grant_revisions'], key=lambda row: (row['principal_id'], row['database_id'])):
        raise OperationError('STALE_REGISTRY')


def _view(db, operation_id, actor, is_admin=False):
    if type(is_admin) is not bool:
        raise OperationError('INVALID_ADMIN_FLAG', 400)
    row = db.execute('''SELECT o.id,o.actor,o.action,o.state,r.revision,r.generation_id,
                       g.manifest,g.delivery_digest,r.secret_observations,r.phase,r.predecessor_id
                       FROM operations o JOIN operation_requests r ON r.operation_id=o.id
                       JOIN generation_plans g ON g.generation_id=r.generation_id WHERE o.id=?''',
                     (operation_id,)).fetchone()
    if row is None or row['actor'] != actor and not is_admin:
        raise OperationError('OPERATION_NOT_FOUND', 404)
    result = dict(row)
    for key in ('manifest', 'secret_observations'):
        result[key] = json.loads(result[key])
    result['warehouse_id'] = result['manifest']['spec']['warehouse_id']
    rows = {row['step']: dict(row) for row in db.execute(
        'SELECT step,state,evidence_ref,updated_at FROM operation_steps WHERE operation_id=?', (operation_id,))}
    result['steps'] = [rows[step] for step in _ALL_STEPS]
    return result


def inspect(operation_id, *, actor, is_admin=False):
    with control_store.connect() as db:
        return _view(db, operation_id, actor, is_admin)


def _action_view(row):
    result = {key: row[key] for key in ('id', 'operation_id', 'actor', 'route', 'payload', 'state', 'created_at')}
    result['payload'] = json.loads(result['payload'])
    result['requires_operator'] = True
    return result


def request_action(operation_id, *, actor, route, idempotency_key, payload, is_admin=False):
    """Persist an HTTP handoff only; no dispatch or runtime assertion occurs here."""
    if not all(_text(value) for value in (operation_id, actor, route, idempotency_key)):
        raise OperationError('INVALID_ACTION_REQUEST', 400)
    routes = {'/api/v1/operations/' + operation_id + '/apply': {'expected_revision', 'delivery_digest'},
              '/api/v1/operations/' + operation_id + '/reconcile': {'expected_revision'}}
    if (route not in routes or type(payload) is not dict or set(payload) != routes[route]
            or type(payload.get('expected_revision')) is not int or payload['expected_revision'] < 1
            or 'delivery_digest' in payload and not _text(payload['delivery_digest'])):
        raise OperationError('INVALID_ACTION_REQUEST', 400)
    normalized = _json(payload)
    with _transaction() as db:
        plan = _view(db, operation_id, actor, is_admin)
        _p5_guard(db, operation_id, plan['warehouse_id'])
        saved = db.execute('''SELECT * FROM warehouse_action_requests
                              WHERE actor=? AND route=? AND idempotency_key=?''',
                           (actor, route, idempotency_key)).fetchone()
        if saved:
            if saved['payload'] != normalized:
                raise OperationError('IDEMPOTENCY_CONFLICT')
            return _action_view(saved)
        if payload['expected_revision'] != plan['revision']:
            raise OperationError('REVISION_CONFLICT')
        if 'delivery_digest' in payload and payload['delivery_digest'] != plan['delivery_digest']:
            raise OperationError('DELIVERY_CONFLICT')
        request_id = str(uuid.uuid4())
        db.execute('INSERT INTO warehouse_action_requests VALUES (?,?,?,?,?,?,?,\'REQUESTED\')',
                   (request_id, operation_id, actor, route, idempotency_key, normalized, admission._now()))
        return _action_view(db.execute('SELECT * FROM warehouse_action_requests WHERE id=?', (request_id,)).fetchone())


def inspect_actions(operation_id, *, actor, is_admin=False):
    with control_store.connect() as db:
        _view(db, operation_id, actor, is_admin)
        return tuple(_action_view(row) for row in db.execute(
            'SELECT * FROM warehouse_action_requests WHERE operation_id=? ORDER BY created_at,id', (operation_id,)))


def validate_prepared_handoff(prior, manifest):
    """Permit a fresh full plan to reseal revisions, grants and Secret references.

    The already prepared physical databases and their four namespace identities
    cannot change beneath the closed reservation or issued principal credentials.
    """
    if prior['phase'] != 'bootstrap' or prior['state'] != 'PREPARED':
        raise OperationError('INVALID_PREPARED_PREDECESSOR')
    def physical(spec):
        return dict(warehouse_id=spec['warehouse_id'], profile=spec['profile'], databases={
            key: dict(sql_catalog=value['sql_catalog'], binding={
                field: setting for field, setting in value['binding'].items() if field != 'desired_revision'},
                schemas=sorted(({field: schema[field] for field in ('id', 'sql_name', 'purpose')}
                                for schema in value['schemas']), key=lambda row: row['id']))
            for key, value in spec['databases'].items()})
    if physical(prior['manifest']['spec']) != physical(manifest['spec']):
        raise OperationError('PREPARED_PHYSICAL_CHANGE_REQUIRES_RECOVERY')


def _prepared_reservation(db, prior, manifest, actor, *, operation_id=None):
    """Validate a prepared reservation without transferring its closed gate."""
    validate_prepared_handoff(prior, manifest)
    control = admission.control(db, prior['warehouse_id'])
    if operation_id is not None and control['operation_id'] == operation_id:
        return control
    if control['state'] not in ('APPLYING', 'UNKNOWN'):
        raise OperationError('INVALID_PREPARED_PREDECESSOR')
    if control['operation_id'] == prior['id']:
        if control['state'] != 'APPLYING':
            raise OperationError('INVALID_PREPARED_PREDECESSOR')
    else:
        owner = _view(db, control['operation_id'], actor)
        attempt = db.execute('SELECT attempt_id FROM operation_requests WHERE operation_id=?',
                             (owner['id'],)).fetchone()[0]
        publication = owner['steps'][0]
        undispatched = (owner['state'] == 'AWAITING_APPLY'
                        and all(row['state'] == 'PENDING' for row in owner['steps'][1:])
                        and (publication['state'] == 'PENDING'
                             or publication['state'] == 'SUCCEEDED' and _receipt(publication['evidence_ref'])))
        verified_stale = (control['state'] == 'APPLYING' and owner['state'] == 'VERIFIED_STALE'
                          and all(row['state'] == 'SUCCEEDED' for row in owner['steps']))
        if (owner['phase'] != 'full' or owner['predecessor_id'] != prior['id']
                or attempt is not None or not (undispatched or verified_stale)):
            raise OperationError('INVALID_PREPARED_PREDECESSOR')
        validate_prepared_handoff(prior, owner['manifest'])
    return control


def create_plan(actor, route, idempotency_key, bundle, secret_observations, *, phase='full', predecessor_id=None):
    return _create_plan(actor, route, idempotency_key, bundle, secret_observations,
        phase=phase, predecessor_id=predecessor_id)


def _create_plan(actor, route, idempotency_key, bundle, secret_observations, *, phase='full', predecessor_id=None,
                 connection=None, creation_id=None):
    if not all(_text(v) for v in (actor, route, idempotency_key)):
        raise OperationError('INVALID_OPERATION_IDENTITY', 400)
    if phase not in ('bootstrap', 'full') or predecessor_id is not None and not _text(predecessor_id) or phase == 'bootstrap' and predecessor_id is not None:
        raise OperationError('INVALID_OPERATION_PHASE', 400)
    bundle = _bundle(bundle)
    manifest = bundle['manifest']
    observations = _observations(bundle, secret_observations)
    payload = _json(dict(manifest=manifest, secret_observations=observations, phase=phase, predecessor_id=predecessor_id))
    spec = manifest['spec']
    generation_id = manifest['generation_id']
    with (nullcontext(connection) if connection is not None else _transaction()) as db:
        existing = db.execute('''SELECT operation_id,payload FROM operation_requests
                                 WHERE actor=? AND route=? AND idempotency_key=?''',
                              (actor, route, idempotency_key)).fetchone()
        if existing:
            if existing['payload'] != payload:
                raise OperationError('IDEMPOTENCY_CONFLICT')
            return _view(db, existing['operation_id'], actor)
        import database_creations
        database_creations.guard_reservation(db, spec['warehouse_id'], creation_id=creation_id)
        _check_registry(db, spec)
        created_unprepared = db.execute('''SELECT 1 FROM database_create_requests c
                JOIN databases d ON d.id=c.database_id WHERE upper(d.state) IN ('REGISTERED','PREPARING') LIMIT 1''').fetchone()
        imported_unprepared = db.execute('''SELECT 1 FROM registry_register_requests
                WHERE EXISTS (SELECT 1 FROM databases WHERE upper(state) IN ('REGISTERED','PREPARING')) LIMIT 1''').fetchone()
        if phase == 'full' and predecessor_id is None and (created_unprepared or imported_unprepared):
            raise OperationError('PREPARED_PREDECESSOR_REQUIRED')
        if predecessor_id is not None:
            prior = _view(db, predecessor_id, actor)
            _prepared_reservation(db, prior, manifest, actor)
            if phase != 'full':
                raise OperationError('INVALID_PREPARED_PREDECESSOR')
        saved = db.execute('SELECT * FROM generation_plans WHERE generation_id=?', (generation_id,)).fetchone()
        if saved:
            if (saved['manifest'] != _json(manifest)
                    or saved['delivery_digest'] != manifest['delivery_digest']):
                raise OperationError('GENERATION_CONFLICT')
        else:
            if dm.get_generation(db, generation_id) is not None:
                raise OperationError('GENERATION_CONFLICT')
            dm.record_generation(db, dm.WarehouseConfigGeneration(generation_id, spec['warehouse_id'], generation_id,
                tuple((key, value['binding']['desired_revision']) for key, value in sorted(spec['databases'].items())),
                tuple((g['principal_id'], g['database_id'], g['revision']) for g in spec['grant_revisions']),
                tuple(sorted(bundle['files'])), 'PLANNED'))
            db.execute('INSERT INTO generation_plans VALUES (?,?,?,?)',
                       (generation_id, _json(manifest), manifest['delivery_digest'], _json(observations)))
        operation_id = str(uuid.uuid4())
        dm.record_operation(db, dm.Operation(operation_id, actor, 'WAREHOUSE_CONFIG_APPLY', (), None,
                            'AWAITING_APPLY', (), generation_id, (), (('created_at', admission._now()),)))
        db.execute('''INSERT INTO operation_requests
                      (operation_id,actor,route,idempotency_key,payload,revision,generation_id,initial_database_states,
                       phase,predecessor_id,secret_observations)
                      VALUES (?,?,?,?,?,1,?,?,?,?,?)''', (operation_id, actor, route, idempotency_key, payload,
                      generation_id, '{}', phase, predecessor_id, _json(observations)))
        for step in _ALL_STEPS:
            db.execute('INSERT INTO operation_steps VALUES (?,?,\'PENDING\',NULL,?)', (operation_id, step, admission._now()))
        return _view(db, operation_id, actor)


def _p5_guard(db, operation_id, warehouse_id=None):
    import database_creation_auth
    return database_creation_auth.guard(db, operation_id, warehouse_id)


def _state(db, operation_id, state, *, bookkeeping=False):
    if not bookkeeping:
        _p5_guard(db, operation_id)
    db.execute('UPDATE operations SET state=? WHERE id=?', (state, operation_id))
    db.execute('UPDATE operation_requests SET revision=revision+1 WHERE operation_id=?', (operation_id,))
    import database_creation_auth
    database_creation_auth.advance(db, operation_id)


def _owned(db, plan, attempt, *, bookkeeping=False):
    request = db.execute('SELECT * FROM operation_requests WHERE operation_id=?', (plan['id'],)).fetchone()
    control = admission.control(db, plan['warehouse_id'])
    if request['attempt_id'] != attempt or control['operation_id'] != plan['id']:
        raise OperationError('OPERATION_IN_PROGRESS')
    if not bookkeeping:
        _p5_guard(db, plan['id'], plan['warehouse_id'])
    return request


def _closed(db, plan, state='UNKNOWN'):
    db.execute('UPDATE warehouse_controls SET state=?,revision=revision+1 WHERE warehouse_id=? AND operation_id=?',
               (state, plan['warehouse_id'], plan['id']))


def _preflight(plan, bundle, observe_secrets, *, after=None, allow_secret_update=False, require_present=False):
    import database_creation_auth
    database_creation_auth.assert_leaf(plan)
    try:
        values = observe_secrets(copy.deepcopy(plan))
    except Exception:
        raise OperationError('SECRET_OBSERVATION_UNAVAILABLE', 503) from None
    observed = _observations(bundle, values)
    baseline = after or plan['secret_observations']
    for old, new in zip(baseline, observed):
        allowed = (allow_secret_update and
                   (old['exists'] is False and new['exists'] is True
                    or old['exists'] is True and dict(old, resource_version=new['resource_version']) == new))
        if old != new and not allowed:
            raise OperationError('STALE_SECRETS')
    if require_present and not all(row['exists'] for row in observed):
        raise OperationError('SECRETS_NOT_VERIFIED')
    return observed


def _authorize(authorize, actor):
    try:
        allowed = callable(authorize) and authorize(actor) is True
    except Exception:
        raise OperationError('AUTHORIZATION_UNAVAILABLE', 503) from None
    if not allowed:
        raise OperationError('OPERATION_FORBIDDEN', 403)


def _release_before_mutation(plan, attempt, state='AWAITING_APPLY'):
    with _transaction() as db:
        request = _owned(db, plan, attempt, bookkeeping=True)
        _state(db, plan['id'], state, bookkeeping=True)
        db.execute('UPDATE operation_requests SET attempt_id=NULL WHERE operation_id=?', (plan['id'],))
        if request['mutation_started'] or request['predecessor_id'] is not None:
            _closed(db, plan)
        else:
            admission.reopen(db, warehouse_id=plan['warehouse_id'], operation_id=plan['id'])


def _run_step(plan, step, attempt, callback):
    with _transaction() as db:
        _owned(db, plan, attempt)
        saved = db.execute('SELECT state FROM operation_steps WHERE operation_id=? AND step=?',
                           (plan['id'], step)).fetchone()[0]
        if saved == 'SUCCEEDED' and step != 'verify':
            return True
        if saved != 'PENDING' and not (step == 'verify' and saved == 'SUCCEEDED'):
            raise OperationError('RECONCILIATION_REQUIRED')
        db.execute("UPDATE operation_steps SET state='RUNNING',evidence_ref=NULL,updated_at=? WHERE operation_id=? AND step=?",
                   (admission._now(), plan['id'], step))
        if step == 'verify':
            db.execute('UPDATE operation_requests SET observation_failure=NULL WHERE operation_id=?', (plan['id'],))
        _state(db, plan['id'], 'PREPARING' if step == 'publish_snapshot' else 'APPLYING')
    # No SQLite connection/transaction survives this boundary.
    try:
        result = callback(copy.deepcopy(plan))
        if (type(result) is not dict or not _receipt(result.get('evidence_ref'))
                or step == 'verify' and result.get('verified') is not True):
            raise ValueError()
        receipt = result['evidence_ref']
    except BaseException as error:
        with _transaction() as db:
            _owned(db, plan, attempt, bookkeeping=True)
            db.execute("UPDATE operation_steps SET state='UNKNOWN',updated_at=? WHERE operation_id=? AND step=?",
                       (admission._now(), plan['id'], step))
            _state(db, plan['id'], 'UNKNOWN', bookkeeping=True)
            _closed(db, plan)
        if not isinstance(error, Exception):
            raise
        return False
    with _transaction() as db:
        _owned(db, plan, attempt, bookkeeping=True)
        try:
            _p5_guard(db, plan['id'], plan['warehouse_id'])
        except OperationError as error:
            import database_creation_auth
            if isinstance(error, database_creation_auth.AuthorityLost):
                database_creation_auth._stop(db,error.config,error.target,str(uuid.uuid4()),'AUTHORITY_LOST')
            # STOP after an effect preserves its receipt and uncertainty. It
            # cannot authorize another leaf or the final READY transaction.
            db.execute("UPDATE operation_steps SET state='UNKNOWN',evidence_ref=?,updated_at=? WHERE operation_id=? AND step=?",
                (receipt, admission._now(), plan['id'], step))
            _state(db, plan['id'], 'UNKNOWN', bookkeeping=True)
            _closed(db, plan)
            return False
        db.execute("UPDATE operation_steps SET state='SUCCEEDED',evidence_ref=?,updated_at=? WHERE operation_id=? AND step=?",
                   (receipt, admission._now(), plan['id'], step))
        db.execute('UPDATE operation_requests SET revision=revision+1 WHERE operation_id=?', (plan['id'],))
        import database_creation_auth
        database_creation_auth.advance(db, plan['id'])
    return True


def apply(operation_id, *, actor, expected_revision, delivery_digest, authorize,
          observe_secrets, execute_step, verify, drain_timeout=30.0, gate_timeout=10.0):
    with _operator_gate():
        return _apply(operation_id, actor=actor, expected_revision=expected_revision, delivery_digest=delivery_digest,
                      authorize=authorize, observe_secrets=observe_secrets, execute_step=execute_step,
                      verify=verify, drain_timeout=drain_timeout, gate_timeout=gate_timeout)


def _database_state_cas(db, key, revision, source, target):
    """Lifecycle and registry revision must both survive each finalization write."""
    if source.upper() not in ('READY', 'RECONCILING', 'REGISTERED', 'PREPARING'):
        raise OperationError('STALE_REGISTRY')
    changed = db.execute('UPDATE databases SET state=? WHERE id=? AND revision=? AND state=?',
                         (target, key, revision, source))
    current = db.execute('SELECT state,revision FROM databases WHERE id=?', (key,)).fetchone()
    if (changed.rowcount != 1 or current is None or current['state'] != target
            or current['revision'] != revision):
        raise OperationError('STALE_REGISTRY')


def _ready_states(db, spec):
    initial = {key: db.execute('SELECT state FROM databases WHERE id=?', (key,)).fetchone()[0]
               for key in spec['databases']}
    # VERIFIED_STALE retained positive verification but deliberately withheld READY.
    # Only its explicit formerly-READY record permits a later successful replan
    # to restore a reconciling database. Registered databases never qualify.
    eligible = {}
    for row in db.execute('''SELECT r.initial_database_states FROM operation_requests r
                             JOIN operations o ON o.id=r.operation_id WHERE o.state IN ('VERIFIED_STALE','PREPARED') '''):
        for key, state in json.loads(row[0]).items():
            if state.upper() == 'READY':
                eligible[key] = state
    for key, state in initial.items():
        if state == 'reconciling' and key in eligible:
            initial[key] = eligible[key]
    return initial


def _apply(operation_id, *, actor, expected_revision, delivery_digest, authorize,
           observe_secrets, execute_step, verify, drain_timeout, gate_timeout):
    with _transaction() as db:
        initial = _view(db, operation_id, actor)
        _p5_guard(db, operation_id, initial['warehouse_id'])
    _authorize(authorize, actor)
    plan = inspect(operation_id, actor=actor)
    if delivery_digest != plan['delivery_digest']:
        raise OperationError('DELIVERY_CONFLICT')
    if plan['state'] in ('APPLIED', 'PREPARED'):
        return plan
    if type(expected_revision) is not int or expected_revision != plan['revision']:
        raise OperationError('REVISION_CONFLICT')
    if plan['state'] != 'AWAITING_APPLY':
        raise OperationError('RECONCILIATION_REQUIRED')
    if not all(callable(fn) for fn in (observe_secrets, execute_step, verify)):
        raise OperationError('INVALID_CALLBACK', 400)
    if any(type(value) not in (int, float) or not 0 <= value < float('inf') for value in (drain_timeout, gate_timeout)):
        raise OperationError('INVALID_TIMEOUT', 400)
    bundle = _render(plan['manifest'])
    with control_store.connect() as db:
        request = db.execute('SELECT * FROM operation_requests WHERE operation_id=?', (operation_id,)).fetchone()
        after = json.loads(request['secret_after']) if request['secret_after'] else None
        secret_step = db.execute("SELECT state FROM operation_steps WHERE operation_id=? AND step='apply_secrets'", (operation_id,)).fetchone()[0]
        _check_registry(db, plan['manifest']['spec'])
    capture_after = after is None and secret_step == 'SUCCEEDED'
    observed = _preflight(plan, bundle, observe_secrets, after=after, allow_secret_update=capture_after)
    import database_creation_auth
    attempt = database_creation_auth.core_attempt(operation_id)
    with _transaction() as db:
        latest = _view(db, operation_id, actor)
        _p5_guard(db, operation_id, latest['warehouse_id'])
        if latest['revision'] != expected_revision or latest['state'] != 'AWAITING_APPLY':
            raise OperationError('REVISION_CONFLICT')
        _check_registry(db, plan['manifest']['spec'])
        state = admission._ensure(db, plan['warehouse_id'])
        predecessor = plan['predecessor_id']
        if predecessor is not None:
            prior = _view(db, predecessor, actor)
            _prepared_reservation(db, prior, plan['manifest'], actor, operation_id=operation_id)
            previous = db.execute('SELECT initial_database_states FROM operation_requests WHERE operation_id=?', (predecessor,)).fetchone()
            db.execute('UPDATE operation_requests SET mutation_started=1,initial_database_states=? WHERE operation_id=?',
                       (previous['initial_database_states'], operation_id))
        if (predecessor is None and state['operation_id'] not in (None, operation_id)
                or state['operation_id'] is None and state['state'] != 'OPEN'):
            raise OperationError('WAREHOUSE_OPERATION_CONFLICT')
        if predecessor is not None and state['operation_id'] not in (predecessor, operation_id):
            # Supersede only at explicit apply, in the ownership-transfer transaction.
            # A stale old apply must not reclaim a subsequent undispatched attempt.
            db.execute("UPDATE operations SET state='REPLAN_REQUIRED' WHERE id=? AND state='AWAITING_APPLY'",
                       (state['operation_id'],))
            if db.execute('SELECT changes()').fetchone()[0]:
                db.execute('UPDATE operation_requests SET revision=revision+1 WHERE operation_id=?',
                           (state['operation_id'],))
        db.execute('UPDATE warehouse_controls SET operation_id=?,revision=revision+1 WHERE warehouse_id=?',
                   (operation_id, plan['warehouse_id']))
        db.execute('UPDATE operation_requests SET attempt_id=? WHERE operation_id=?', (attempt, operation_id))
        if capture_after:
            db.execute('UPDATE operation_requests SET secret_after=? WHERE operation_id=? AND secret_after IS NULL',
                       (_json(observed), operation_id))
            after = observed
        _state(db, operation_id, 'PREPARING')
    if not _run_step(plan, 'publish_snapshot', attempt, lambda p: execute_step('publish_snapshot', p)):
        return inspect(operation_id, actor=actor)
    with _transaction() as db:
        request = _owned(db, plan, attempt)
        # A resumed operation retains its closed gate until fresh verification.
        if not request['mutation_started']:
            if admission.control(db, plan['warehouse_id'])['state'] in ('UNKNOWN', 'APPLYING'):
                # Explicit reconciliation resolved the publication intent. This is
                # the same reservation, not an automatic takeover of UNKNOWN work.
                _closed(db, plan, 'DRAINING')
            else:
                admission.begin_drain(db, warehouse_id=plan['warehouse_id'], operation_id=operation_id)
        _state(db, operation_id, 'DRAINING')
    deadline = time.monotonic() + drain_timeout
    while True:
        with control_store.connect() as db:
            _owned(db, plan, attempt)
            blocked = admission.blockers(db, plan['warehouse_id'])
        if not blocked:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _release_before_mutation(plan, attempt)
            return inspect(operation_id, actor=actor)
        time.sleep(min(.02, remaining))
    verified = False
    try:
        with admission.lifecycle_gate(exclusive=True, deadline=time.monotonic() + gate_timeout):
            # Observe externally first; then validate the same durable reservation in a short transaction.
            observed = _preflight(plan, bundle, observe_secrets, after=observed)
            _authorize(authorize, actor)
            with _transaction() as db:
                request = _owned(db, plan, attempt)
                if admission.blockers(db, plan['warehouse_id']):
                    raise OperationError('WAREHOUSE_BLOCKED')
                _check_registry(db, plan['manifest']['spec'])
                if not request['mutation_started']:
                    initial = _ready_states(db, plan['manifest']['spec'])
                    db.execute('UPDATE operation_requests SET initial_database_states=?,mutation_started=1 WHERE operation_id=?',
                               (_json(initial), operation_id))
                    for key, state in initial.items():
                        if state.upper() == 'READY':
                            current = db.execute('SELECT state FROM databases WHERE id=?', (key,)).fetchone()[0]
                            _database_state_cas(db, key,
                                plan['manifest']['spec']['databases'][key]['database_revision'],
                                current, 'reconciling')
                _closed(db, plan, 'APPLYING')
                _state(db, operation_id, 'APPLYING')
            for step in STEPS[1:]:
                if plan['phase'] == 'bootstrap' and step == 'reconcile_trino':
                    continue
                if not _run_step(plan, step, attempt, lambda p, s=step: execute_step(s, p)):
                    return inspect(operation_id, actor=actor)
                # Only apply_secrets may change descriptors, and only its first
                # confirmed observation becomes the immutable post-step baseline.
                capture_after = step == 'apply_secrets' and after is None
                observed = _preflight(plan, bundle, observe_secrets, after=observed,
                                      allow_secret_update=capture_after)
                if capture_after:
                    with _transaction() as db:
                        _owned(db, plan, attempt)
                        db.execute('UPDATE operation_requests SET secret_after=? WHERE operation_id=? AND secret_after IS NULL',
                                   (_json(observed), operation_id))
                    after = observed
            if not _run_step(plan, 'verify', attempt, verify):
                return inspect(operation_id, actor=actor)
            observed = _preflight(plan, bundle, observe_secrets, after=observed, require_present=plan['phase'] == 'full')
            verified = True
            with _transaction() as db:
                request = _owned(db, plan, attempt)
                _check_registry(db, plan['manifest']['spec'])
                if plan['phase'] == 'bootstrap':
                    db.execute('UPDATE operation_requests SET attempt_id=NULL,secret_after=? WHERE operation_id=?', (_json(observed), operation_id))
                    _closed(db, plan, 'APPLYING')
                    _state(db, operation_id, 'PREPARED')
                    import database_creation_auth
                    database_creation_auth.complete(db, operation_id, 'PREPARED')
                    return _view(db, operation_id, actor)
                import grant_management
                receipt = db.execute("SELECT evidence_ref FROM operation_steps WHERE operation_id=? AND step='verify' AND state='SUCCEEDED'",
                                     (operation_id,)).fetchone()
                grant_management.promote(db, plan['manifest']['spec'], plan['generation_id'],
                    operation_id, actor, receipt['evidence_ref'] if receipt else None)
                for key, expected in plan['manifest']['spec']['databases'].items():
                    db.execute('''UPDATE catalog_bindings SET applied_revision=?,applied_generation_id=?,revision=revision+1
                                  WHERE database_id=?''', (expected['binding']['desired_revision'], plan['generation_id'], key))
                initial = json.loads(request['initial_database_states'])
                for key, state in initial.items():
                    if state.upper() == 'READY':
                        _database_state_cas(db, key,
                            plan['manifest']['spec']['databases'][key]['database_revision'],
                            'reconciling', state)
                    elif plan['predecessor_id'] is not None and state.upper() in ('REGISTERED', 'PREPARING'):
                        _database_state_cas(db, key,
                            plan['manifest']['spec']['databases'][key]['database_revision'],
                            state, 'READY')
                db.execute("UPDATE warehouse_config_generations SET state='APPLIED' WHERE id=?", (plan['generation_id'],))
                db.execute("UPDATE warehouse_controls SET state='OPEN',operation_id=NULL,revision=revision+1,applied_generation_id=? WHERE warehouse_id=?",
                           (plan['generation_id'], plan['warehouse_id']))
                db.execute('UPDATE operation_requests SET attempt_id=NULL,secret_after=? WHERE operation_id=?', (_json(observed), operation_id))
                _state(db, operation_id, 'APPLIED')
                import database_creation_auth
                database_creation_auth.complete(db, operation_id, 'APPLIED')
    except admission.GateTimeout:
        _release_before_mutation(plan, attempt)
    except Exception as error:
        with _transaction() as db:
            request = _owned(db, plan, attempt, bookkeeping=True)
            mutated = bool(request['mutation_started'])
            if mutated:
                verified_stale = verified and isinstance(error, OperationError) and error.code == 'STALE_REGISTRY'
                _state(db, operation_id, 'VERIFIED_STALE' if verified_stale else 'UNKNOWN', bookkeeping=True)
                if verified_stale:
                    if plan['predecessor_id'] is not None:
                        _closed(db, plan, 'APPLYING')
                    else:
                        db.execute("UPDATE warehouse_controls SET state='OPEN',operation_id=NULL,revision=revision+1 WHERE warehouse_id=? AND operation_id=?",
                                   (plan['warehouse_id'], operation_id))
                    db.execute('UPDATE operation_requests SET attempt_id=NULL WHERE operation_id=?', (operation_id,))
                else:
                    _closed(db, plan)
                if not verified_stale:
                    # Configuration effects have known receipts, but observation
                    # was incomplete or stale. Keep an observable reconciliation
                    # target even if the original verifier had already succeeded.
                    db.execute("UPDATE operation_steps SET state='UNKNOWN',updated_at=? WHERE operation_id=? AND step='verify'",
                               (admission._now(), operation_id))
                    reason = error.code if isinstance(error, OperationError) and error.code in (
                        'STALE_SECRETS', 'SECRETS_NOT_VERIFIED', 'INVALID_SECRET_OBSERVATIONS', 'STALE_REGISTRY') else None
                    db.execute('UPDATE operation_requests SET observation_failure=? WHERE operation_id=?', (reason, operation_id))
                db.execute('UPDATE warehouse_config_generations SET state=? WHERE id=?',
                           ('VERIFIED_STALE' if verified_stale else 'OBSERVED_UNAVAILABLE', plan['generation_id']))
        if not mutated:
            _release_before_mutation(plan, attempt, 'REPLAN_REQUIRED' if isinstance(error, OperationError)
                                     and error.code.startswith('STALE_') else 'AWAITING_APPLY')
            raise
    return inspect(operation_id, actor=actor)


def _validate_reconciliation(evidence):
    if (type(evidence) is not ReconciliationEvidence or evidence.step not in _ALL_STEPS
            or evidence.outcome not in ('SUCCEEDED', 'NOT_EXECUTED', 'UNKNOWN')
            or evidence.basis not in ('backend_ack', 'observed_configuration', 'confirmed_not_dispatched')
            or evidence.outcome == 'NOT_EXECUTED' and evidence.basis != 'confirmed_not_dispatched'
            or evidence.outcome == 'SUCCEEDED' and evidence.basis == 'confirmed_not_dispatched'
            or not _receipt(evidence.evidence_ref)):
        raise OperationError('INVALID_RECONCILIATION', 400)


def reconcile(operation_id, *, actor, evidence, is_admin=False):
    _validate_reconciliation(evidence)
    with _operator_gate(), admission.lifecycle_gate(exclusive=True, deadline=time.monotonic() + 10):
        return _reconcile_locked(operation_id, actor=actor, evidence=evidence, is_admin=is_admin)


def reconcile_from_observer(operation_id, *, actor, observer, is_admin=False):
    """Observe and reconcile under the same operator and query lifecycle locks."""
    with _operator_gate(), admission.lifecycle_gate(exclusive=True, deadline=time.monotonic() + 10):
        # inspect closes its connection before observation, which may itself need
        # SQLite or private receipt writes. Authorization precedes those effects.
        plan = inspect(operation_id, actor=actor, is_admin=is_admin)
        with _transaction() as db:
            # Resolve warehouse from the canonical Operation/generation, never
            # from a caller-supplied or cached observer projection.
            _p5_guard(db, operation_id)
        if not callable(observer):
            raise OperationError('INVALID_RECONCILIATION_OBSERVER', 400)
        try:
            evidence = observer(plan)
        except Exception:
            raise OperationError('RECONCILIATION_OBSERVER_FAILED', 503) from None
        _validate_reconciliation(evidence)
        return _reconcile_locked(operation_id, actor=actor, evidence=evidence, is_admin=is_admin)


def _reconcile_locked(operation_id, *, actor, evidence, is_admin):
    """Atomic reconciliation; both public callers already hold the two locks."""
    with _transaction() as db:
        plan = _view(db, operation_id, actor, is_admin)
        _p5_guard(db, operation_id, plan['warehouse_id'])
        saved = db.execute('SELECT state FROM operation_steps WHERE operation_id=? AND step=?',
                           (operation_id, evidence.step)).fetchone()[0]
        unresolved = next((row['step'] for row in plan['steps'] if row['state'] != 'SUCCEEDED'), None)
        pending_boundary = (saved == 'PENDING' and evidence.outcome == 'NOT_EXECUTED'
                            and evidence.step == unresolved
                            and plan['state'] in ('PREPARING', 'DRAINING', 'APPLYING'))
        verified_boundary = saved == 'SUCCEEDED' and evidence.step == 'verify' and plan['state'] == 'APPLYING'
        if (plan['state'] == 'APPLIED' or not (saved in ('RUNNING', 'UNKNOWN')
                                             or pending_boundary or verified_boundary)):
            raise OperationError('NO_UNCERTAIN_STEP')
        control = admission.control(db, plan['warehouse_id'])
        if control['operation_id'] != operation_id:
            raise OperationError('WAREHOUSE_OPERATION_CONFLICT')
        reason = db.execute('SELECT observation_failure FROM operation_requests WHERE operation_id=?',
                            (operation_id,)).fetchone()[0]
        if reason is not None and evidence.step == 'verify':
            if (evidence.outcome == 'NOT_EXECUTED'
                    or evidence.outcome == 'SUCCEEDED' and evidence.basis != 'observed_configuration'):
                raise OperationError('CONFIGURATION_OBSERVATION_REQUIRED', 400)
            if evidence.outcome == 'SUCCEEDED':
                # This observation hands off the actual unavailable configuration
                # to a NEW plan. It never certifies the original apply or any
                # fixed step which did not finish.
                db.execute("UPDATE operation_steps SET state='SUCCEEDED',evidence_ref=?,updated_at=? WHERE operation_id=? AND step='verify'",
                           (evidence.evidence_ref, admission._now(), operation_id))
                _state(db, operation_id, 'VERIFIED_STALE')
                db.execute("UPDATE warehouse_config_generations SET state='VERIFIED_STALE' WHERE id=?", (plan['generation_id'],))
                if plan['predecessor_id'] is not None:
                    _closed(db, plan, 'APPLYING')
                else:
                    db.execute("UPDATE warehouse_controls SET state='OPEN',operation_id=NULL,revision=revision+1 WHERE warehouse_id=? AND operation_id=?",
                               (plan['warehouse_id'], operation_id))
                db.execute('UPDATE operation_requests SET attempt_id=NULL WHERE operation_id=?', (operation_id,))
                return _view(db, operation_id, actor, is_admin)
        state = {'SUCCEEDED': 'SUCCEEDED', 'NOT_EXECUTED': 'PENDING', 'UNKNOWN': 'UNKNOWN'}[evidence.outcome]
        db.execute('UPDATE operation_steps SET state=?,evidence_ref=?,updated_at=? WHERE operation_id=? AND step=?',
                   (state, evidence.evidence_ref, admission._now(), operation_id, evidence.step))
        _closed(db, plan)
        db.execute('UPDATE operation_requests SET attempt_id=NULL WHERE operation_id=?', (operation_id,))
        _state(db, operation_id, 'UNKNOWN' if state == 'UNKNOWN' else 'AWAITING_APPLY')
        if state != 'UNKNOWN':
            import database_creation_auth
            database_creation_auth.reconciled(db, operation_id)
        return _view(db, operation_id, actor, is_admin)
