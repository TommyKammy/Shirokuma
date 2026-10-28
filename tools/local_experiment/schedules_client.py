"""Authenticated HTTP-only database schedules and alerts client."""
import argparse
import base64
from datetime import date, datetime
import json
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

from dataops_client import Client, ClientError, configured_profile, saved_context

UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
SPEC_FIELDS = {'kind', 'every_minutes', 'time', 'timezone', 'date', 'weekdays', 'day_of_month'}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(UUID, value):
        raise ValueError('UUID required')
    return value


def spec_value(spec):
    if not isinstance(spec, dict) or set(spec) - SPEC_FIELDS:
        raise ValueError('invalid schedule specification')
    kind = spec.get('kind')
    fields = {'interval': {'every_minutes'}, 'daily': {'time', 'timezone'},
              'once': {'time', 'timezone', 'date'}, 'weekly': {'time', 'timezone', 'weekdays'},
              'monthly': {'time', 'timezone', 'day_of_month'}}
    if not isinstance(kind, str) or kind not in fields:
        raise ValueError('invalid schedule kind')
    # Public schedules may include the backend-normalized null/default fields.
    active = {k for k, v in spec.items() if v is not None and v != []} - {'kind'}
    if active - fields[kind]:
        raise ValueError('irrelevant schedule fields')
    if kind == 'interval':
        if type(spec.get('every_minutes')) is not int or not 1 <= spec['every_minutes'] <= 10080:
            raise ValueError('invalid interval')
    else:
        if not isinstance(spec.get('time'), str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', spec['time']):
            raise ValueError('HH:MM required')
        if not isinstance(spec.get('timezone', 'Asia/Tokyo'), str):
            raise ValueError('IANA timezone required')
        ZoneInfo(spec.get('timezone', 'Asia/Tokyo'))
        if kind == 'once':
            date.fromisoformat(spec['date'])
        if kind == 'weekly':
            days = spec.get('weekdays')
            if not isinstance(days, list) or not days or any(type(d) is not int or not 1 <= d <= 7 for d in days) or len(set(days)) != len(days):
                raise ValueError('unique ISO weekdays required')
        if kind == 'monthly' and not (spec.get('day_of_month') == 'last' or
                type(spec.get('day_of_month')) is int and 1 <= spec['day_of_month'] <= 31):
            raise ValueError('invalid day of month')
    return spec


def definition_value(value):
    required = {'target_kind', 'target_id', 'target_revision', 'expected_revision', 'spec', 'intent'}
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - {'schedule_id', 'enabled'}:
        raise ValueError('exact schedule definition required')
    if value['target_kind'] not in ('job', 'stage', 'mart'):
        raise ValueError('invalid target kind')
    identifier(value['target_id'])
    if (type(value['target_revision']) is not int or value['target_revision'] < 1 or
            type(value['expected_revision']) is not int or value['expected_revision'] < 0 or
            ('schedule_id' in value) != (value['expected_revision'] > 0) or
            type(value.get('enabled', False)) is not bool):
        raise ValueError('invalid revision or enabled')
    if 'schedule_id' in value:
        identifier(value['schedule_id'])
    spec_value(value['spec'])
    intent = value['intent']
    kind = value['target_kind']
    if (not isinstance(intent, dict) or kind == 'job' and
            (set(intent) != {'arguments'} or not isinstance(intent['arguments'], dict)) or
            kind == 'stage' and intent != {'action': 'scan'} or
            kind == 'mart' and (set(intent) != {'action', 'check_changes'} or
                intent['action'] != 'refresh' or intent['check_changes'] is not True)):
        raise ValueError('invalid explicit schedule intent')
    return value


class ScheduleClient(Client):
    @property
    def owner(self):
        return base64.b64decode(self.headers['Authorization'].split()[1]).decode().split(':', 1)[0]

    def request(self, method, path, *, body=None, params=None, key=None, binary=False):
        if key is not None:
            self.headers['X-Idempotency-Key'] = key
        try:
            return super().request(method, path, body=body, params=params, binary=binary)
        finally:
            self.headers.pop('X-Idempotency-Key', None)

    def scoped(self, record):
        if (not isinstance(record, dict) or record.get('owner') != self.owner or
                record.get('database_id') != self.context['database_id'] or
                record.get('selected_role') != self.context['selected_role'] or
                not isinstance(record.get('context'), dict) or
                any(record['context'].get(k) != self.context[k] for k in
                    ('database_id', 'schema_id', 'warehouse_id', 'sql_catalog', 'sql_schema', 'selected_role'))):
            raise ValueError('record scope mismatch')
        identifier(record.get('id'))
        return record

    def schedule(self, record):
        self.scoped(record)
        definition_value({k: record[k] for k in ('target_kind', 'target_id', 'target_revision', 'spec', 'intent')} |
                         dict(expected_revision=record['revision'], schedule_id=record['id'], enabled=record['enabled']))
        for name in ('next_run_at', 'last_status', 'paused_reason'):
            if name not in record or record[name] is not None and not isinstance(record[name], str):
                raise ValueError('invalid schedule status')
        return record

    def checked(self, reply, field, *, record_id=None, filters=None):
        if not isinstance(reply, dict) or reply.get('context') != self.context:
            raise ValueError('response context mismatch')
        required = {'context', field} | ({'unacknowledged_count'} if field == 'alerts' else set())
        if set(reply) != required:
            raise ValueError('invalid response envelope')
        rows = reply[field] if field in ('schedules', 'ticks', 'alerts') else [reply[field]]
        if not isinstance(rows, list):
            raise ValueError('array required')
        seen = set()
        for row in rows:
            if field == 'ticks':
                if not isinstance(row, dict):
                    raise ValueError('invalid tick')
                identifier(row.get('id'))
                if (row.get('schedule_id') != record_id or
                        type(row.get('schedule_revision')) is not int or row['schedule_revision'] < 1 or
                        type(row.get('target_revision')) is not int or row['target_revision'] < 1 or
                        not isinstance(row.get('status'), str) or not re.fullmatch(r'[a-z][a-z0-9_]*', row['status']) or
                        not isinstance(row.get('scheduled_for'), str) or
                        'code' not in row or row['code'] is not None and (not isinstance(row['code'], str) or not row['code']) or
                        'run_id' not in row):
                    raise ValueError('invalid tick scope/revision')
                timestamp = datetime.fromisoformat(row['scheduled_for'].replace('Z', '+00:00'))
                if timestamp.tzinfo is None:
                    raise ValueError('aware tick timestamp required')
                if row['run_id'] is not None:
                    identifier(row['run_id'])
            else:
                (self.schedule if field in ('schedule', 'schedules') else self.scoped)(row)
            if row['id'] in seen:
                raise ValueError('duplicate record')
            seen.add(row['id'])
            if field == 'ticks' and row.get('schedule_id') != record_id:
                raise ValueError('tick schedule mismatch')
            if field in ('schedule', 'alert') and record_id and row['id'] != record_id:
                raise ValueError('record identity mismatch')
            if filters and any(row.get(k) != v for k, v in filters.items()):
                raise ValueError('target mismatch')
        if field == 'alerts' and (type(reply['unacknowledged_count']) is not int or
                not 0 <= reply['unacknowledged_count'] <= len(rows)):
            raise ValueError('invalid alert count')
        return reply


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    groups = root.add_subparsers(dest='group', required=True)
    for group, actions in (('schedule', ('list', 'show', 'save', 'ticks')), ('alert', ('list', 'ack'))):
        commands = groups.add_parser(group).add_subparsers(dest='action', required=True)
        for action in actions:
            p = commands.add_parser(action)
            for name in ('database', 'schema', 'context-file'):
                p.add_argument('--' + name)
            p.add_argument('--api-url', default='http://127.0.0.1:8080')
            if group == 'schedule' and action in ('show', 'ticks'):
                p.add_argument('--schedule-id', required=True)
            if group == 'schedule' and action == 'list':
                p.add_argument('--target-kind', choices=('job', 'stage', 'mart'))
                p.add_argument('--target-id')
            if action == 'save':
                p.add_argument('--definition', required=True)
            if action == 'ack':
                p.add_argument('--alert-id', required=True)
            if action in ('save', 'ack'):
                p.add_argument('--idempotency-key', required=True)
    return root


def json_file(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise ValueError('invalid JSON file') from None


def main(argv=None, *, profile=None):
    args = parser().parse_args(argv)
    if not args.context_file and (not args.database or not args.schema):
        raise ValueError('explicit database and schema or context-file required')
    for name, value in vars(args).items():
        if isinstance(value, str) and not value.strip():
            raise ValueError('nonempty input required')
    key = getattr(args, 'idempotency_key', None)
    if key is not None and (len(key) > 256 or any(ord(c) < 32 for c in key)):
        raise ValueError('invalid idempotency key')
    for name in ('schedule_id', 'alert_id', 'target_id'):
        if getattr(args, name, None):
            identifier(getattr(args, name))
    body = definition_value(json_file(args.definition)) if args.action == 'save' else {}
    context = saved_context(json_file(args.context_file)) if args.context_file else None
    if context and (args.database is not None and args.database not in (context['database_id'], context['sql_catalog']) or
                    args.schema is not None and args.schema not in (context['schema_id'], context['sql_schema'])):
        raise ValueError('saved context selection mismatch')
    selected_profile = profile or configured_profile()
    if selected_profile is None:
        raise ValueError('database runtime profile required')
    client = ScheduleClient(selected_profile, args.api_url)
    if context:
        client.context, client.role = context, context['selected_role']
        client.headers['X-Shirokuma-Role'] = client.role
    else:
        client.select(args.database, args.schema)
    base = '/api/v1/' + ('schedules' if args.group == 'schedule' else 'alerts')
    record_id = getattr(args, 'schedule_id', None) or getattr(args, 'alert_id', None)
    filters = {name: getattr(args, name) for name in ('target_kind', 'target_id') if getattr(args, name, None)}
    path = base + ('/' + record_id if record_id else '')
    if args.action in ('ticks', 'ack'):
        path += '/' + args.action
    mutation = args.action in ('save', 'ack')
    field = {'show': 'schedule', 'save': 'schedule', 'ticks': 'ticks', 'ack': 'alert'}.get(args.action, base.split('/')[-1])
    try:
        if args.action == 'ticks':
            client.checked(client.get(base + '/' + record_id), 'schedule', record_id=record_id)
        reply = client.post(path, body, key) if mutation else client.get(path, **filters)
        if args.action == 'save':
            record_id = body.get('schedule_id')
            filters = {k: body[k] for k in ('target_kind', 'target_id', 'target_revision')}
        reply = client.checked(reply, field, record_id=record_id, filters=filters)
        if args.action == 'save' and (reply['schedule']['revision'] != body['expected_revision'] + 1 or
                reply['schedule']['enabled'] != body.get('enabled', False) or reply['schedule']['intent'] != body['intent']):
            raise ValueError('save receipt mismatch')
        if args.action == 'save':
            expected_spec = {k: v for k, v in body['spec'].items() if v is not None and v != []}
            if expected_spec['kind'] != 'interval':
                expected_spec.setdefault('timezone', 'Asia/Tokyo')
            if expected_spec['kind'] == 'weekly':
                expected_spec['weekdays'] = sorted(expected_spec['weekdays'])
            actual_spec = {k: v for k, v in reply['schedule']['spec'].items() if v is not None and v != []}
            if actual_spec != expected_spec:
                raise ValueError('save specification mismatch')
        return reply
    except Exception:
        if mutation:
            # Retain only public observation coordinates. Never print arbitrary HTTP errors.
            observation = base + ('/' + body['schedule_id'] if body.get('schedule_id') else '')
            if args.action == 'ack':
                observation = base
            print(json.dumps(dict(context=client.context, idempotency_key=key,
                                  observe=observation, outcome='unknown')), file=sys.stderr)
        raise ValueError('schedule/alert request failed; observe original context without resending POST') from None


if __name__ == '__main__':
    try:
        print(json.dumps(main(), ensure_ascii=False))
    except Exception:
        print('Schedule/alert request failed. Inspect original context, key and observation path.', file=sys.stderr)
        sys.exit(1)
