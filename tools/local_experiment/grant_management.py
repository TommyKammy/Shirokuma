"""Desired grants and verified effective authority, in the existing control ledger.

No connection or backend is opened at import time. Epoch tombstones survive deletes.
"""
import json
import uuid
from contextlib import nullcontext

import control_store
import data_management as dm
from warehouse_operations import OperationError, _json, _text, _receipt

ROLES = {'revoked': -1, 'bi_viewer': 0, 'data_engineer': 1, 'admin': 2}


def migrate(db):
    db.execute('''CREATE TABLE effective_grants (
        principal_id TEXT NOT NULL, database_id TEXT NOT NULL REFERENCES databases(id),
        maximum_role TEXT NOT NULL CHECK(maximum_role IN ('revoked','bi_viewer','data_engineer')),
        epoch INTEGER NOT NULL CHECK(epoch > 0), applied_revision INTEGER,
        applied_generation_id TEXT REFERENCES warehouse_config_generations(id),
        PRIMARY KEY(principal_id,database_id))''')
    db.execute('''CREATE TABLE grant_requests (
        operation_id TEXT PRIMARY KEY REFERENCES operations(id), actor TEXT NOT NULL,
        route TEXT NOT NULL, idempotency_key TEXT NOT NULL, payload TEXT NOT NULL,
        UNIQUE(actor,route,idempotency_key))''')
    db.execute('''CREATE TABLE grant_audit (
        id INTEGER PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES operations(id),
        apply_operation_id TEXT REFERENCES operations(id), actor TEXT NOT NULL,
        principal_id TEXT NOT NULL, database_id TEXT NOT NULL,
        before_role TEXT NOT NULL, after_role TEXT NOT NULL,
        effective_role TEXT NOT NULL,
        expected_revision INTEGER NOT NULL, desired_revision INTEGER NOT NULL,
        effective_epoch INTEGER NOT NULL, generation_id TEXT, receipt TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))''')
    for row in db.execute('SELECT * FROM grants').fetchall():
        proof = migration_proof(db, dict(row))
        db.execute('INSERT INTO effective_grants VALUES (?,?,?,?,?,?)',
            (row['principal_id'], row['database_id'], row['maximum_role'] if proof else 'revoked',
             row['revision'], row['revision'] if proof else None, proof))
    # Keep direct registry writers safe as well as the authenticated HTTP mutation.
    # Any desired edit invalidates frozen authority. Promotions retain the old ceiling.
    db.execute('''CREATE TRIGGER grant_insert AFTER INSERT ON grants BEGIN
        INSERT INTO effective_grants VALUES (NEW.principal_id,NEW.database_id,'revoked',NEW.revision,NULL,NULL)
        ON CONFLICT(principal_id,database_id) DO UPDATE SET
            epoch=max(epoch+1,NEW.revision); END''')
    db.execute('''CREATE TRIGGER grant_update AFTER UPDATE ON grants BEGIN
        UPDATE effective_grants SET epoch=max(epoch+1,NEW.revision),
            maximum_role=CASE WHEN NEW.maximum_role='revoked' THEN 'revoked'
                WHEN NEW.maximum_role='bi_viewer' AND maximum_role='data_engineer' THEN 'bi_viewer'
                ELSE maximum_role END
        WHERE principal_id=OLD.principal_id AND database_id=OLD.database_id; END''')
    db.execute('''CREATE TRIGGER grant_delete AFTER DELETE ON grants BEGIN
        UPDATE effective_grants SET maximum_role='revoked',epoch=max(epoch+1,OLD.revision+1)
        WHERE principal_id=OLD.principal_id AND database_id=OLD.database_id; END''')
    migrate_database_admin(db)


def migrate_database_admin(db):
    """Schema 13 -> 14 helper; caller owns connection, transaction and version.

    Copy authority verbatim: a schema upgrade is never backend verification.
    Repeated calls leave epochs, proofs and applied bindings untouched.
    """
    definition = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='effective_grants'").fetchone()[0]
    for name in ('grant_insert', 'grant_update', 'grant_delete'):
        db.execute('DROP TRIGGER IF EXISTS ' + name)
    if "'admin'" not in definition:
        db.execute('''CREATE TABLE effective_grants_admin (
            principal_id TEXT NOT NULL, database_id TEXT NOT NULL REFERENCES databases(id),
            maximum_role TEXT NOT NULL CHECK(maximum_role IN ('revoked','bi_viewer','data_engineer','admin')),
            epoch INTEGER NOT NULL CHECK(epoch > 0), applied_revision INTEGER,
            applied_generation_id TEXT REFERENCES warehouse_config_generations(id),
            PRIMARY KEY(principal_id,database_id))''')
        db.execute('INSERT INTO effective_grants_admin SELECT * FROM effective_grants')
        db.execute('DROP TABLE effective_grants')
        db.execute('ALTER TABLE effective_grants_admin RENAME TO effective_grants')
    db.execute('''CREATE TRIGGER grant_insert AFTER INSERT ON grants BEGIN
        INSERT INTO effective_grants VALUES (NEW.principal_id,NEW.database_id,'revoked',NEW.revision,NULL,NULL)
        ON CONFLICT(principal_id,database_id) DO UPDATE SET epoch=max(epoch+1,NEW.revision); END''')
    rank = "CASE %s WHEN 'admin' THEN 2 WHEN 'data_engineer' THEN 1 WHEN 'bi_viewer' THEN 0 ELSE -1 END"
    db.execute('''CREATE TRIGGER grant_update AFTER UPDATE ON grants BEGIN
        UPDATE effective_grants SET epoch=max(epoch+1,NEW.revision),
            maximum_role=CASE WHEN (%s) < (%s) THEN NEW.maximum_role ELSE maximum_role END
        WHERE principal_id=OLD.principal_id AND database_id=OLD.database_id; END''' % (
            rank % 'NEW.maximum_role', rank % 'maximum_role'))
    db.execute('''CREATE TRIGGER grant_delete AFTER DELETE ON grants BEGIN
        UPDATE effective_grants SET maximum_role='revoked',epoch=max(epoch+1,OLD.revision+1)
        WHERE principal_id=OLD.principal_id AND database_id=OLD.database_id; END''')


def migration_proof(db, grant):
    """Only the CURRENT binding's linked, fully verified APPLIED manifest proves authority."""
    row = db.execute('''SELECT b.applied_generation_id,b.applied_revision,g.state,g.warehouse_id,
        p.manifest,p.delivery_digest
        FROM catalog_bindings b JOIN warehouse_config_generations g ON g.id=b.applied_generation_id
        JOIN generation_plans p ON p.generation_id=g.id WHERE b.database_id=?''',
        (grant['database_id'],)).fetchone()
    if row is None or row['state'] != 'APPLIED':
        return None
    generation = row['applied_generation_id']
    try:
        manifest = json.loads(row['manifest'])
        # Validate the complete public snapshot, not a revision-only projection.
        import database_generation
        if (database_generation.render(manifest['spec'])['manifest'] != manifest or
                manifest['delivery_digest'] != row['delivery_digest'] or
                manifest['generation_id'] != generation or
                manifest['spec']['generation_id'] != generation or
                manifest['spec']['warehouse_id'] != row['warehouse_id'] or
                grant['database_id'] not in manifest['spec']['databases'] or
                manifest['spec']['databases'][grant['database_id']]['binding']['desired_revision'] != row['applied_revision'] or
                grant not in manifest['spec']['grant_revisions']):
            return None
        proof = db.execute('''SELECT s.evidence_ref FROM operation_requests r
            JOIN operations o ON o.id=r.operation_id
            JOIN operation_steps s ON s.operation_id=o.id AND s.step='verify'
            WHERE r.generation_id=? AND r.phase='full' AND o.state='APPLIED'
            AND o.action='WAREHOUSE_CONFIG_APPLY' AND o.apply_id=?
            AND s.state='SUCCEEDED' ''', (generation, generation)).fetchall()
        return generation if any(_receipt(p['evidence_ref']) for p in proof) else None
    except (ValueError, KeyError, TypeError, ImportError, OSError):
        return None


_CANCEL = 'desired_grant_cancellation_v1'


def _verified_snapshot(db, generation_id):
    """Canonical full-apply provenance; no registry equality or admission side effects."""
    import database_generation
    import warehouse_operations as core
    row = db.execute('SELECT * FROM warehouse_config_generations WHERE id=?', (generation_id,)).fetchone()
    plan = db.execute('SELECT * FROM generation_plans WHERE generation_id=?', (generation_id,)).fetchone()
    if row is None or plan is None or row['state'] != 'APPLIED' or row['apply_id'] != generation_id:
        raise ValueError('Unverified generation')
    manifest = json.loads(plan['manifest'])
    bundle = database_generation.render(manifest['spec'])
    spec = manifest['spec']
    if (bundle['manifest'] != manifest or manifest['generation_id'] != generation_id
            or spec['generation_id'] != generation_id or spec['warehouse_id'] != row['warehouse_id']
            or manifest['delivery_digest'] != plan['delivery_digest']
            or json.loads(row['database_revisions']) != [[k, v['binding']['desired_revision']] for k,v in sorted(spec['databases'].items())]
            or json.loads(row['grant_revisions']) != [[g['principal_id'],g['database_id'],g['revision']] for g in spec['grant_revisions']]
            or json.loads(row['configuration_refs']) != sorted(bundle['files'])):
        raise ValueError('Invalid generation identity')
    candidates = db.execute("""SELECT o.id,o.actor AS operation_actor,o.expected_revision AS operation_expected_revision,
        o.apply_id,r.* FROM operations o LEFT JOIN operation_requests r ON r.operation_id=o.id
        WHERE o.action='WAREHOUSE_CONFIG_APPLY' AND o.state='APPLIED'
        AND (r.generation_id=? OR o.apply_id=?) ORDER BY o.id""", (generation_id,generation_id)).fetchall()
    if not candidates:
        raise ValueError('Missing full operation')
    proofs = []
    for op in candidates:
        if (op['generation_id'] != generation_id or op['phase'] != 'full'
                or not all(_text(op[k]) for k in ('operation_actor','actor','route','idempotency_key'))
                or op['operation_actor'] != op['actor'] or op['operation_expected_revision'] is not None
                or op['apply_id'] != generation_id):
            raise ValueError('Invalid operation identity')
        payload = json.loads(op['payload'])
        if payload != dict(manifest=manifest, secret_observations=json.loads(op['secret_observations']),
                phase='full',predecessor_id=op['predecessor_id']):
            raise ValueError('Invalid operation payload')
        if op['predecessor_id'] is not None:
            try:
                prior = core._view(db, op['predecessor_id'], op['actor'])
                core.validate_prepared_handoff(prior, manifest)
            except core.OperationError as error:
                raise ValueError('Invalid operation predecessor') from error
        steps = {r['step']: dict(r) for r in db.execute('SELECT * FROM operation_steps WHERE operation_id=?', (op['id'],))}
        if set(steps) != set(core._ALL_STEPS) or any(r['state'] != 'SUCCEEDED' or not _receipt(r['evidence_ref']) for r in steps.values()):
            raise ValueError('Incomplete full operation')
        proofs.append(dict(generation_id=generation_id,delivery_digest=plan['delivery_digest'],
            operation_id=op['id'],verify_ref=steps['verify']['evidence_ref']))
    return spec, proofs


def _current_snapshot(db, database_id):
    import warehouse_admission as admission
    binding = db.execute('SELECT * FROM catalog_bindings WHERE database_id=?', (database_id,)).fetchone()
    if binding is None:
        raise ValueError('Missing binding')
    spec, proofs = _verified_snapshot(db, binding['applied_generation_id'])
    control = admission.control(db, spec['warehouse_id'])
    if control['applied_generation_id'] != binding['applied_generation_id'] or database_id not in spec['databases']:
        raise ValueError('Invalid current warehouse generation')
    for key, entry in spec['databases'].items():
        current = db.execute('SELECT * FROM catalog_bindings WHERE database_id=?', (key,)).fetchone()
        if (current is None or current['applied_generation_id'] != binding['applied_generation_id']
                or current['applied_revision'] != entry['binding']['desired_revision']):
            raise ValueError('Invalid current binding')
    return spec, proofs


def _never_applied(db, principal, database, warehouse):
    """Every relevant historical APPLIED public snapshot must prove pair absence."""
    if db.execute("""SELECT 1 FROM grant_audit WHERE principal_id=? AND database_id=?
            AND (apply_operation_id IS NOT NULL OR generation_id IS NOT NULL OR receipt IS NOT NULL)""",
            (principal,database)).fetchone():
        raise ValueError('Prior promotion')
    for operation in db.execute("""SELECT o.id,r.generation_id,r.phase,r.payload,g.state,g.warehouse_id FROM operations o
            LEFT JOIN operation_requests r ON r.operation_id=o.id
            LEFT JOIN warehouse_config_generations g ON g.id=r.generation_id
            WHERE o.action='WAREHOUSE_CONFIG_APPLY' AND o.state='APPLIED'"""):
        body = json.loads(operation['payload']) if operation['payload'] else {}
        if (operation['warehouse_id'] == warehouse or operation['generation_id'] is None
                or body.get('manifest',{}).get('spec',{}).get('warehouse_id') == warehouse):
            if operation['phase'] != 'full' or operation['state'] != 'APPLIED':
                raise ValueError('Orphan applied operation')
            _verified_snapshot(db, operation['generation_id'])
    for row in db.execute("SELECT id FROM warehouse_config_generations WHERE warehouse_id=? AND state='APPLIED'", (warehouse,)):
        spec, _ = _verified_snapshot(db, row['id'])
        if any(g['principal_id'] == principal and g['database_id'] == database for g in spec['grant_revisions']):
            raise ValueError('Pair in applied snapshot')


def _intent_chain(db, principal, database, revision):
    rows = db.execute("""SELECT a.*,o.action,o.state,o.apply_id,o.expected_revision AS operation_revision,
        r.payload,r.actor AS request_actor,o.actor AS operation_actor,r.route FROM grant_audit a
        LEFT JOIN operations o ON o.id=a.operation_id LEFT JOIN grant_requests r ON r.operation_id=o.id
        WHERE a.principal_id=? AND a.database_id=? ORDER BY a.desired_revision,a.id""", (principal,database)).fetchall()
    if len(rows) != revision:
        raise ValueError('Incomplete normal mutation chain')
    role, ids = 'revoked', []
    for number, r in enumerate(rows, 1):
        payload = dict(principal_id=principal,maximum_role=r['after_role'],expected_revision=number-1)
        if (r['action'] != 'GRANT_CHANGE' or r['apply_id'] is not None
                or r['operation_actor'] != r['actor'] or r['effective_epoch'] != number
                or r['after_role'] not in ROLES
                or r['state'] not in ('AWAITING_APPLY','SUPERSEDED','CANCELED')
                or r['operation_revision'] != number-1 or r['expected_revision'] != number-1
                or r['desired_revision'] != number or r['before_role'] != role
                or r['effective_role'] != 'revoked' or r['apply_operation_id'] is not None
                or r['generation_id'] is not None or r['receipt'] is not None
                or r['request_actor'] != r['actor'] or r['route'] != '/api/v1/databases/'+database+'/grants'
                or json.loads(r['payload']) != payload):
            raise ValueError('Invalid mutation chain')
        role = r['after_role']
        ids.append(r['operation_id'])
    requests = []
    for request in db.execute('SELECT operation_id,payload FROM grant_requests WHERE route=?',
            ('/api/v1/databases/'+database+'/grants',)):
        body = json.loads(request['payload'])
        if body['principal_id'] == principal:
            requests.append(request['operation_id'])
    if set(requests) != set(ids) or len(requests) != len(ids):
        raise ValueError('Incomplete pair requests')
    return rows, ids


def _cancel_eligibility(db, previous):
    if previous is None or previous.maximum_role == 'revoked':
        return None
    try:
        import warehouse_admission as admission
        principal, database = previous.principal_id, previous.database_id
        current = effective(db, principal, database)
        if (current is None or current['maximum_role'] != 'revoked' or current['applied_revision'] is not None
                or current['applied_generation_id'] is not None):
            return None
        spec, proofs = _current_snapshot(db, database)
        # Every candidate proves the same canonical snapshot; anchor new markers
        # deterministically. Existing markers retain their original op/ref binding.
        proof = min(proofs, key=lambda proof: proof['operation_id'])
        control = admission.control(db, spec['warehouse_id'])
        if control['state'] != 'OPEN' or control['operation_id'] is not None or admission.blockers(db, spec['warehouse_id']):
            return None
        _never_applied(db, principal, database, spec['warehouse_id'])
        rows, ids = _intent_chain(db, principal, database, previous.revision)
        if rows[-1]['after_role'] != previous.maximum_role or rows[-1]['effective_epoch'] != current['epoch']:
            return None
        return dict(base=proof,intent_ids=ids)
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, ImportError, OSError):
        return None


def _canceled(db, grant):
    """A marker hides only its exact revoked row, epoch, and retained provenance."""
    try:
        principal, database = grant['principal_id'], grant['database_id']
        current = effective(db, principal, database)
        if (grant['maximum_role'] != 'revoked' or current is None or current['maximum_role'] != 'revoked'
                or current['applied_revision'] is not None or current['applied_generation_id'] is not None):
            return False
        rows, ids = _intent_chain(db, principal, database, grant['revision'])
        last = rows[-1]
        if (last['state'] != 'CANCELED' or last['after_role'] != grant['maximum_role']
                or last['effective_epoch'] != current['epoch']):
            return False
        op = db.execute('SELECT before_after FROM operations WHERE id=?', (ids[-1],)).fetchone()
        snapshot = json.loads(op['before_after'])
        marker = json.loads(snapshot[2][1])
        if snapshot != [['database_id',database],['principal_id',principal],[_CANCEL,_json(marker)]]:
            return False
        if set(marker) != {'base','intent_ids','principal_id','database_id','desired_revision','effective_epoch'}:
            return False
        if (marker['intent_ids'] != ids[:-1] or marker['principal_id'] != principal or marker['database_id'] != database
                or type(marker['desired_revision']) is not int or marker['desired_revision'] != grant['revision']
                or type(marker['effective_epoch']) is not int or marker['effective_epoch'] != current['epoch']):
            return False
        original, proofs = _verified_snapshot(db, marker['base']['generation_id'])
        if marker['base'] not in proofs or database not in original['databases']:
            return False
        spec, _ = _current_snapshot(db, database)
        if spec['warehouse_id'] != original['warehouse_id']:
            return False
        _never_applied(db, principal, database, spec['warehouse_id'])
        return True
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, ImportError, OSError):
        return False


def desired_projection(db):
    """One fail-closed public desired grant projection, including ordinary tombstones."""
    return [dict(row) for row in db.execute('SELECT * FROM grants ORDER BY principal_id,database_id')
            if not _canceled(db, dict(row))]


def pending_desired(db, *, excluding_database=None):
    return any(g['database_id'] != excluding_database and
        (effective(db,g['principal_id'],g['database_id']) or {}).get('applied_revision') != g['revision']
        for g in desired_projection(db))


def effective(db, principal_id, database_id):
    row = db.execute('SELECT * FROM effective_grants WHERE principal_id=? AND database_id=?',
                     (principal_id, database_id)).fetchone()
    return dict(row) if row else None


def list_grants(principal, role, database_id):
    from catalog_admin import require_admin
    require_admin(principal, role)
    with control_store.connect() as db:
        if dm.get_database(db, database_id) is None:
            raise OperationError('DATABASE_NOT_FOUND', 404)
        return {'grants': [dict(row) for row in db.execute('''SELECT g.*,
            e.maximum_role AS effective_role,e.epoch AS grant_revision,
            e.applied_revision,e.applied_generation_id FROM grants g
            JOIN effective_grants e USING(principal_id,database_id)
            WHERE g.database_id=? ORDER BY principal_id''', (database_id,))]}


def inspect(db, operation_id, actor, admin=False):
    row = db.execute('''SELECT o.*,r.payload FROM operations o
        JOIN grant_requests r ON r.operation_id=o.id WHERE o.id=?''', (operation_id,)).fetchone()
    if row is None or row['actor'] != actor and not admin:
        raise OperationError('OPERATION_NOT_FOUND', 404)
    result = dict(id=row['id'], actor=row['actor'], action=row['action'], state=row['state'],
        request=json.loads(row['payload']), audit=[dict(a) for a in db.execute(
            'SELECT * FROM grant_audit WHERE operation_id=? ORDER BY id', (operation_id,))])
    import hashlib
    refresh = db.execute('''SELECT id FROM logical_requests
        WHERE actor=? AND route='private:logical-auth-refresh' AND idempotency_key=? AND action='AUTH_REFRESH'
        ''',
        (row['actor'], hashlib.sha256(('grant:' + operation_id).encode()).hexdigest())).fetchone()
    if refresh is not None:
        result['auth_refresh_request_id'] = refresh['id']
    return result


def mutate(principal, role, database_id, payload, key):
    return _mutate(principal, role, database_id, payload, key)


def _mutate(principal, role, database_id, payload, key, *, connection=None):
    """Desired grant mutation in the caller's existing registration transaction."""
    from catalog_admin import require_admin
    from warehouse_admission import _now
    require_admin(principal, role)
    if (type(payload) is not dict or set(payload) != {'principal_id','maximum_role','expected_revision'}
            or not _text(payload.get('principal_id')) or not _text(database_id) or not _text(key)
            or not isinstance(payload.get('maximum_role'), str) or payload['maximum_role'] not in ROLES
            or type(payload.get('expected_revision')) is not int or payload['expected_revision'] < 0):
        raise OperationError('INVALID_GRANT', 400)
    route = '/api/v1/databases/' + database_id + '/grants'
    normalized = _json(payload)
    actor, target, expected = principal.principal_id, payload['principal_id'], payload['expected_revision']
    if payload['maximum_role'] == 'admin':
        import request_auth
        try:
            account = request_auth._principal_accounts().get(target)
        except request_auth.AuthenticationError:
            raise OperationError('ADMIN_ACCOUNT_REQUIRED', 400) from None
        if not account or account['role'] != 'admin':
            raise OperationError('ADMIN_ACCOUNT_REQUIRED', 400)
    with (nullcontext(connection) if connection is not None else control_store.connect()) as db:
        if connection is None:
            db.execute('BEGIN IMMEDIATE')
        saved = db.execute('SELECT * FROM grant_requests WHERE actor=? AND route=? AND idempotency_key=?',
                           (actor, route, key)).fetchone()
        if saved:
            if saved['payload'] != normalized:
                raise OperationError('IDEMPOTENCY_CONFLICT')
            return inspect(db, saved['operation_id'], actor)
        database = dm.get_database(db, database_id)
        if database is None:
            raise OperationError('DATABASE_NOT_FOUND', 404)
        logical_root = db.execute("SELECT logical_state FROM db_creations WHERE database_id=? AND creation_mode='logical_v1'",
                                  (database_id,)).fetchone()
        previous = dm.get_grant(db, target, database_id)
        if (previous.revision if previous else 0) != expected:
            raise OperationError('REVISION_CONFLICT')
        cancellation = (_cancel_eligibility(db, previous)
                        if payload['maximum_role'] == 'revoked' and logical_root is None else None)
        if previous:
            desired = dm.update_grant(db, target, database_id, expected, payload['maximum_role'])
        else:
            desired = dm.create_grant(db, target, database_id, payload['maximum_role'])
        after = effective(db, target, database_id)
        db.execute('''UPDATE operations SET state='SUPERSEDED'
            WHERE action='GRANT_CHANGE' AND state='AWAITING_APPLY'
            AND id IN (SELECT a.operation_id FROM grant_audit a
                JOIN grant_requests r ON r.operation_id=a.operation_id
                WHERE a.principal_id=? AND a.database_id=?)''',
            (target,database_id))
        operation_id = str(uuid.uuid4())
        snapshot = (('database_id',database_id),('principal_id',target))
        if cancellation is not None:
            cancellation.update(principal_id=target,database_id=database_id,desired_revision=desired.revision,effective_epoch=after['epoch'])
            snapshot += ((_CANCEL,_json(cancellation)),)
        dm.record_operation(db, dm.Operation(operation_id, actor, 'GRANT_CHANGE', (), expected,
            'CANCELED' if cancellation is not None else 'AWAITING_APPLY', (), None, snapshot, (('created_at',_now()),)))
        db.execute('INSERT INTO grant_requests VALUES (?,?,?,?,?)', (operation_id, actor, route, key, normalized))
        db.execute('''INSERT INTO grant_audit
            (operation_id,actor,principal_id,database_id,before_role,after_role,effective_role,
             expected_revision,desired_revision,effective_epoch) VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (operation_id,actor,target,database_id,previous.maximum_role if previous else 'revoked',
             desired.maximum_role,after['maximum_role'],expected,desired.revision,after['epoch']))
        result = inspect(db, operation_id, actor)
        import logical_registry
        root = logical_root
        refresh_id = None
        if root is not None and root['logical_state'] == 'READY' and database.state == 'READY':
            refresh_id = logical_registry.request_auth_refresh(db, actor, target, operation_id,
                                                               dict(database_id=database_id))
            result['auth_refresh_request_id'] = refresh_id
    # Exiting our connection commits before the callback. Caller transactions
    # retain ownership of both commit and dispatch.
    if connection is None and refresh_id is not None:
        try:
            from logical_creations import dispatch_async
            dispatch_async(refresh_id)
        except Exception:
            pass  # Durable ACCEPTED request remains available for reconciliation.
    return result


def promote(db, spec, generation_id, apply_operation_id, actor, receipt):
    """Caller owns the final full-apply transaction and has rechecked the entire registry."""
    if not _receipt(receipt):
        raise OperationError('INVALID_VERIFICATION_RECEIPT')
    import logical_registry
    if any(logical_registry.creation_mode(db, g['database_id']) == 'logical_v1'
           for g in spec['grant_revisions']):
        raise OperationError('CREATION_MODE_MISMATCH')
    for grant in spec['grant_revisions']:
        principal, database = grant['principal_id'], grant['database_id']
        old = effective(db, principal, database)
        if old is None:
            raise OperationError('STALE_REGISTRY')
        epoch = old['epoch'] + (old['maximum_role'] != grant['maximum_role'])
        db.execute('''UPDATE effective_grants SET maximum_role=?,epoch=?,applied_revision=?,
            applied_generation_id=? WHERE principal_id=? AND database_id=?''',
            (grant['maximum_role'],epoch,grant['revision'],generation_id,principal,database))
        pending = db.execute('''SELECT o.id FROM operations o JOIN grant_requests r ON r.operation_id=o.id
            JOIN grant_audit a ON a.operation_id=o.id WHERE o.state='AWAITING_APPLY'
            AND a.principal_id=? AND a.database_id=? AND a.desired_revision=?''',
            (principal,database,grant['revision'])).fetchall()
        # The apply Operation also records promotion of imported/migrated grants.
        ids = [p['id'] for p in pending] or [apply_operation_id]
        for operation_id in ids:
            db.execute('''INSERT INTO grant_audit
                (operation_id,apply_operation_id,actor,principal_id,database_id,before_role,after_role,effective_role,
                 expected_revision,desired_revision,effective_epoch,generation_id,receipt)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (operation_id,apply_operation_id,actor,principal,database,old['maximum_role'],
                 grant['maximum_role'],grant['maximum_role'],grant['revision'],grant['revision'],epoch,generation_id,receipt))
        for p in pending:
            db.execute("UPDATE operations SET state='APPLIED',apply_id=? WHERE id=?", (apply_operation_id,p['id']))
