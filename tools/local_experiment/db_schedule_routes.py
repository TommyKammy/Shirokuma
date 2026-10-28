"""Strict authenticated database schedule and alert HTTP dispatcher."""
import re

import db_schedules as schedules
import dataops_routes as common
from query_context import QueryError
import warehouse_admission as admission


def dispatch(handler, method, path, params, principal, role):
    if not any(path == '/api/v1/' + name or path.startswith('/api/v1/' + name + '/')
               for name in ('schedules', 'alerts')):
        return False
    if '#' in handler.path or handler.headers.get_all('Transfer-Encoding', []):
        raise ValueError('invalid framing')
    if method == 'GET':
        if handler.headers.get_all('Content-Length', []) not in ([], ['0']):
            raise ValueError('GET body forbidden')
        allowed = {'context', 'target_kind', 'target_id'} if path == '/api/v1/schedules' else {'context'}
        if (set(params) - allowed or 'context' not in params or
                any(len(v) != 1 or not v[0] for v in params.values())):
            raise ValueError('exact context and unique parameters required')
        context = common.context_value(common._json_context(params['context'][0]), role)
        body, key = {}, None
    elif method == 'POST':
        if params or len(handler.headers.get_all('Content-Type', [])) != 1:
            raise ValueError('invalid POST framing')
        keys = handler.headers.get_all('X-Idempotency-Key', [])
        if (len(keys) != 1 or not keys[0].strip() or len(keys[0]) > 256 or
                handler.headers.get_all('Idempotency-Key', [])):
            raise ValueError('one X-Idempotency-Key required')
        key = keys[0]
        body = handler.database_body()
        context = common.context_value(body.get('context'), role)
    else:
        raise QueryError('NOT_FOUND', 404)
    envelope = dict(context=context.public_metadata())
    ident = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
    match = re.fullmatch(r'/api/v1/(schedules|alerts)/(' + ident + r')(?:/(ticks|ack))?', path)
    try:
        if path == '/api/v1/schedules':
            if method == 'GET':
                options = {k: v[0] for k, v in params.items() if k != 'context'}
                if (options.get('target_kind', 'job') not in ('job', 'stage', 'mart') or
                        'target_id' in options and not re.fullmatch(ident, options['target_id'])):
                    raise ValueError('invalid target filter')
                envelope['schedules'] = schedules.list_schedules(principal, context, **options)
            else:
                common._keys(body, {'context', 'target_kind', 'target_id', 'target_revision',
                    'expected_revision', 'spec', 'intent'}, {'enabled', 'schedule_id'})
                envelope['schedule'] = schedules.save_schedule(principal, context, key=key,
                    **{k: v for k, v in body.items() if k != 'context'})
        elif path == '/api/v1/alerts' and method == 'GET':
            envelope.update(schedules.list_alerts(principal, context))
            envelope['context'] = context.public_metadata()
        elif match:
            kind, record_id, action = match.groups()
            if kind == 'schedules' and method == 'GET' and action in (None, 'ticks'):
                field, function = ('ticks', schedules.list_ticks) if action else ('schedule', schedules.get_schedule)
                envelope[field] = function(principal, context, record_id)
            elif kind == 'alerts' and method == 'POST' and action == 'ack':
                common._keys(body, {'context'})
                envelope['alert'] = schedules.acknowledge_alert(principal, context, record_id, key=key)
            else:
                raise QueryError('NOT_FOUND', 404)
        else:
            raise QueryError('NOT_FOUND', 404)
    except (admission.AdmissionDenied, admission.AdmissionConflict, admission.GateTimeout) as error:
        raise QueryError(error.code, error.status, details=dict(context=context.public_metadata())) from None
    handler.respond(envelope)
    return True
