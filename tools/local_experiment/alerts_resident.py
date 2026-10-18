"""Local SQL alert notifications, separate acknowledgement and resolution."""
import hashlib
import json
import uuid

import jobs_resident as jobs


def _key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def rule_key(step, parameters):
    if step.get('action') != 'alert':
        return None
    return _key(dict(sql=step['sql'], schema=step.get('schema', 'marts'),
                     message=step['message'], severity=step.get('severity', 'warning'),
                     name=step.get('name', ''), parameters=parameters))


def _rules(definition):
    return {position: rule_key(step, definition.get('parameters', {}))
            for position, step in enumerate(definition.get('steps', []), 1)
            if step.get('action') == 'alert'}


def retire_changed(connection, definition, owner):
    """Called in the definition-save transaction; retirement is not recovery."""
    rules = _rules(definition)
    for row in connection.execute('SELECT id,step_position,rule_key FROM alerts '
                                  'WHERE job_name=? AND owner=? AND resolved_at IS NULL',
                                  (definition['name'], owner)).fetchall():
        if rules.get(row['step_position']) != row['rule_key']:
            connection.execute("UPDATE alerts SET resolved_at=?,resolution='definition_changed' WHERE id=?",
                               (jobs._now(), row['id']))


def evaluate(step):
    try:
        result = jobs.wr.query('LOCAL', step['sql'], catalog='iceberg',
                              schema=step.get('schema', 'marts'), mode='read-only', max_rows=2)
    except OSError as exc:
        raise ValueError('alert SQL could not be evaluated') from exc
    rows = result.get('rows')
    if (result.get('result') != 'verified' or result.get('truncated') is not False
            or not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], (list, tuple))
            or len(rows[0]) != 1 or type(rows[0][0]) is not bool
            or len(result.get('columns', [])) != 1):
        raise ValueError('alert SQL must complete with exactly one row and one non-null boolean column')
    return dict(condition_met=rows[0][0])


def record(connection, name, position, step_name, owner, step, arguments, run_id, condition):
    """Share the worker's success transaction; never save SQL result rows."""
    current = connection.execute('SELECT definition,owner FROM jobs WHERE name=?', (name,)).fetchone()
    signature = step['_rule_key']
    if (current is None or current['owner'] != owner or
            _rules(json.loads(current['definition'])).get(position) != signature):
        return dict(condition_met=condition, alert_id=None, alert_status='definition_changed')
    arguments_key = _key(arguments)
    existing = connection.execute('SELECT * FROM alerts WHERE job_name=? AND step_position=? '
        'AND owner=? AND rule_key=? AND arguments_key=? AND resolved_at IS NULL',
        (name, position, owner, signature, arguments_key)).fetchone()
    now = jobs._now()
    alert_id = existing['id'] if existing else None
    if condition and existing:
        connection.execute('UPDATE alerts SET last_run_id=?,last_seen_at=?,occurrences=occurrences+1 WHERE id=?',
                           (run_id, now, alert_id))
        status = 'ongoing'
    elif condition:
        alert_id = uuid.uuid4().hex
        connection.execute('INSERT INTO alerts(id,job_name,step_position,step_name,owner,severity,message,'
            'first_run_id,last_run_id,first_seen_at,last_seen_at,occurrences,rule_key,arguments_key) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (alert_id, name, position, step_name, owner, step.get('severity', 'warning'), step['message'],
             run_id, run_id, now, now, 1, signature, arguments_key))
        status = 'opened'
    elif existing:
        connection.execute("UPDATE alerts SET last_run_id=?,resolved_at=?,resolution='condition_cleared' WHERE id=?",
                           (run_id, now, alert_id))
        status = 'resolved'
    else:
        status = 'clear'
    return dict(condition_met=condition, alert_id=alert_id, alert_status=status)


def _public(row, actor):
    result = dict(row)
    result.pop('rule_key')
    result.pop('arguments_key')
    result['can_manage'] = result['owner'] == actor
    return result


def list_alerts(actor, role):
    jobs._identity(actor, role)
    with jobs._db() as connection:
        rows = connection.execute("SELECT * FROM alerts WHERE owner=? OR ?='admin' "
            'ORDER BY (acknowledged_at IS NULL) DESC,last_seen_at DESC,id DESC LIMIT 100', (actor, role)).fetchall()
        count = connection.execute("SELECT COUNT(*) FROM alerts WHERE (owner=? OR ?='admin') "
                                   'AND acknowledged_at IS NULL', (actor, role)).fetchone()[0]
        return dict(alerts=[_public(row, actor) for row in rows], unacknowledged_count=count)


def acknowledge(alert_id, actor, role):
    jobs._identity(actor, role)
    jobs._check_account(actor, role)
    with jobs._db() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT * FROM alerts WHERE id=?', (alert_id,)).fetchone()
        if row is None:
            raise jobs.JobNotFound('Alert not found')
        if row['owner'] != actor:
            raise PermissionError('Only the alert owner can acknowledge it')
        if row['acknowledged_at'] is None:
            connection.execute('UPDATE alerts SET acknowledged_at=?,acknowledged_by=? WHERE id=?',
                               (jobs._now(), actor, alert_id))
        return _public(connection.execute('SELECT * FROM alerts WHERE id=?', (alert_id,)).fetchone(), actor)
