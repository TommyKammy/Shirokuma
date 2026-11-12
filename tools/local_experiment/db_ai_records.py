"""Shared actor-owned record persistence and observation; feature policy stays local."""
from copy import deepcopy
import json

import control_store
import db_ai as ai
from query_context import QueryError


def owned(db, principal, context, ident, *, kind):
    row = db.execute('SELECT * FROM ai_explanations WHERE id=?', (ident,)).fetchone()
    if (row is None or row['actor'] != principal.principal_id
            or json.loads(row['context']) != context.public_metadata()
            or json.loads(row['record']).get('kind') != kind):
        ai._fail('AI_EXPLANATION_NOT_FOUND', 404)
    return json.loads(row['record']), json.loads(row['pins'])


def save(db, record):
    record['updated_at'] = ai._now()
    db.execute('UPDATE ai_explanations SET record=? WHERE id=?', (ai._json(record), record['id']))


def get(principal, context, ident, *, owned, authority, settle):
    with control_store.connect() as db:
        record, pins = owned(db, principal, context, ident)
    authority(principal, context, pins)
    if record['state'] in ai._ACTIVE:
        try:
            owner = ai._owner(ident)
        except BlockingIOError:
            pass
        else:
            try:
                settle(principal, context, ident, 'UNKNOWN', 'AI_OWNER_LOST')
            finally:
                owner.close()
        with control_store.connect() as db:
            record, pins = owned(db, principal, context, ident)
    authority(principal, context, pins)
    return deepcopy(record)


def history(principal, context, *, kind, owned, authority, get_record):
    authority(principal, context)
    with control_store.connect() as db:
        ids = [r[0] for r in db.execute("""SELECT id FROM ai_explanations WHERE actor=? AND context=?
            AND json_extract(record,'$.kind')=? ORDER BY json_extract(record,'$.created_at') DESC,id DESC""",
            (principal.principal_id, ai._json(context.public_metadata()), kind))]
    records = []
    for ident in ids:
        try:
            records.append(get_record(principal, context, ident))
        except QueryError:
            continue
        if len(records) == 50:
            break
    for record in records:
        with control_store.connect() as db:
            _, pins = owned(db, principal, context, record['id'])
        authority(principal, context, pins)
    authority(principal, context)
    return records
