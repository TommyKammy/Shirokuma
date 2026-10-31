"""HTTP-only compiled SQL to WAP client. Mutations return durable receipts."""
import argparse
import base64
import http.client
import json
from pathlib import Path
import re
import sys
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request

from dataops_client import Client, configured_profile, saved_context


class RequestUncertain(Exception):
    pass


class RequestRejected(ValueError):
    pass


class DbtClient(Client):
    def __init__(self, profile, api_url='http://127.0.0.1:8888'):
        super().__init__(profile, api_url)
        # Use the saved identity actually sent for Basic authentication. Native
        # API targets do not expose Workbench's /api/session endpoint.
        self.principal = base64.b64decode(self.headers['Authorization'].split(' ', 1)[1]).decode().split(':', 1)[0]

    def request(self, method, path, *, body=None, params=None, key=None, binary=False):
        headers = dict(self.headers)
        if params:
            path += '?' + urlencode(params)
        raw = None
        if body is not None:
            raw = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        if key is not None:
            headers['Idempotency-Key'] = key
        try:
            with self.opener.open(Request(self.base + path, data=raw, headers=headers, method=method), timeout=60) as response:
                raw = response.read()
                if response.headers.get('Content-Length') is not None and len(raw) != int(response.headers['Content-Length']):
                    raise ValueError('incomplete response')
                return json.loads(raw)
        except HTTPError as error:
            if method == 'POST' and body and 'expected_target' in body:
                try:
                    payload = json.loads(error.read())
                    code = payload.get('error', payload.get('code')) if isinstance(payload, dict) else None
                    code = code.get('code') if isinstance(code, dict) else code
                except (ValueError, OSError):
                    code = None
                if error.code == 409 and code == 'DBT_PUBLICATION_REVIEW_CHANGED':
                    raise RequestRejected(code) from None
                raise RequestUncertain() from None
            if method == 'POST' and (error.code == 409 or error.code >= 500):
                raise RequestUncertain() from None
            raise RequestRejected('DBT_REQUEST_FAILED') from None
        except (URLError, OSError, http.client.HTTPException, ValueError):
            if method == 'POST':
                raise RequestUncertain() from None
            raise ValueError('DBT_REQUEST_FAILED') from None

    def checked(self, reply):
        if not isinstance(reply, dict) or reply.get('context') != self.context:
            raise ValueError('response context mismatch')
        return reply

    def get(self, path, **params):
        return self.checked(super().get(path, **params))

    def post(self, path, body, key=None):
        return self.checked(super().post(path, body, key))


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value):
        raise ValueError('invalid artifact ID')
    return value


def artifact(value, context, kind, principal, expected=None, *, observe=False):
    if not isinstance(value, dict) or value.get('kind') != kind:
        raise ValueError('artifact kind mismatch')
    identifier(value.get('id'))
    if expected is not None and value['id'] != expected:
        raise ValueError('artifact ID mismatch')
    saved = saved_context(value.get('context'))
    # Admin GETs and reconciliation may return an engineer's saved parent.
    admin_observation = context['selected_role'] == 'admin' and observe
    if any(saved[k] != context[k] for k in ('database_id', 'schema_id', 'warehouse_id', 'sql_catalog', 'sql_schema', 'generation_id', 'binding_revision')):
        raise ValueError('artifact database/context mismatch')
    if not admin_observation and saved != context:
        raise ValueError('artifact context mismatch')
    if not isinstance(value.get('actor'), str) or not value['actor'] or not isinstance(value.get('state'), str):
        raise ValueError('invalid artifact metadata')
    if not admin_observation and value['actor'] != principal:
        raise ValueError('artifact owner mismatch')
    def same_database(item):
        if isinstance(item, dict):
            if 'database_id' in item and item['database_id'] != context['database_id']:
                raise ValueError('artifact reference database mismatch')
            for child in item.values():
                same_database(child)
        elif isinstance(item, list):
            for child in item:
                same_database(child)
    same_database(value)
    if kind == 'run' and value.get('executor') == 'native_graph':
        models, tests = value.get('models'), value.get('tests')
        if (value.get('selected_node_id') is not None
                or type(value.get('native_tests')) is not bool
                or type(value.get('native_verified')) is not bool
                or type(value.get('publication_allowed')) is not bool
                or not isinstance(value.get('source_digest'), str) or not value['source_digest']
                or not isinstance(models, list) or not models or not isinstance(tests, list)
                or any(not isinstance(m, dict) or not isinstance(m.get('unique_id'), str) or not m['unique_id']
                    or m.get('materialized') not in ('table', 'ephemeral') for m in models)
                or len({m['unique_id'] for m in models}) != len(models)
                or not any(m['materialized'] == 'table' for m in models)
                or any(not isinstance(t, dict) or not isinstance(t.get('unique_id'), str) or not t['unique_id'] for t in tests)
                or len({t['unique_id'] for t in tests}) != len(tests)
                or (not value['native_tests'] and tests)):
            raise ValueError('invalid graph receipt')
    return value


def validate_reply(reply, context, kind, expected, args, principal):
    action = args.action
    if action == 'publish-preview':
        publication_preview(reply, context, args.run_id, args.node_id)
        return reply
    if action == 'projects':
        if not isinstance(reply.get('projects'), list) or any(not isinstance(p, dict) or not isinstance(p.get('id'), str) for p in reply['projects']):
            raise ValueError('invalid projects')
    else:
        records = reply.get('items') if action in ('plans', 'runs') else [reply.get(kind)]
        if not isinstance(records, list):
            raise ValueError('invalid history')
        for record in records:
            artifact(record, context, kind, principal, expected,
                     observe=action not in ('compile', 'run', 'cancel', 'publish'))
            if action == 'compile' and record.get('project_id') != args.project:
                raise ValueError('project association mismatch')
            if action == 'run' or action == 'runs' and args.plan_id:
                if record.get('plan_id') != args.plan_id:
                    raise ValueError('plan association mismatch')
            if action == 'run':
                if (record.get('executor', 'sql_wap') != args.executor
                        or record.get('selected_node_id') != args.node_id
                        or type(record.get('native_tests', False)) is not bool
                        or record.get('native_tests', False) is not args.native_tests):
                    raise ValueError('executor/node association mismatch')
            if action == 'publish' and (record.get('run_id') != args.run_id or record.get('node_id') != args.node_id):
                raise ValueError('publication association mismatch')
            if action == 'publish' and hasattr(args, 'expected_target') and (record.get('origin') != args.review_origin
                    or json.dumps(record.get('expected_target'), sort_keys=True) != json.dumps(args.expected_target, sort_keys=True) or record.get('parent_run_id') != args.run_id
                    or record.get('plan_id') != args.review_plan_id):
                raise ValueError('native publication review ACK mismatch')
    return reply


def publication_preview(value, context, run_id, node_id):
    """Validate a saved review without refreshing or changing its descriptor."""
    if not isinstance(value, dict):
        raise ValueError('invalid publication preview')
    if 'preview' in value:
        if value.get('context') != context:
            raise ValueError('preview context mismatch')
        value = value['preview']
    if (not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1
            or value.get('origin') not in ('native_table', 'native_graph') or value.get('run_id') != run_id
            or value.get('node_id') != node_id or value.get('context') != context):
        raise ValueError('preview association mismatch')
    identifier(value.get('plan_id'))
    d = value.get('expected_target')
    if (not isinstance(d, dict) or set(d) != {'version', 'destination', 'target'}
            or type(d['version']) is not int or d['version'] != 1
            or not isinstance(d['destination'], dict)):
        raise ValueError('invalid reviewed target')
    dest, target = d['destination'], d['target']
    if dest.get('database_id') != context['database_id'] or not isinstance(dest.get('schema_id'), str) or not dest['schema_id']:
        raise ValueError('invalid reviewed destination')
    if 'object_id' in dest:
        if (set(dest) != {'database_id', 'schema_id', 'object_id', 'incarnation'}
                or not isinstance(dest['object_id'], str) or not dest['object_id']
                or type(dest['incarnation']) is not int or dest['incarnation'] < 1
                or not isinstance(target, dict) or set(target) != {'ref', 'snapshot', 'schema_id', 'columns', 'schema_fingerprint'}
                or target.get('ref') != dest or not isinstance(target.get('snapshot'), str)
                or not re.fullmatch(r'[0-9]+', target['snapshot'])
                or not isinstance(target.get('columns'), list) or not target['columns']
                or type(target.get('schema_id')) is not int or target['schema_id'] < 0
                or not isinstance(target.get('schema_fingerprint'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', target['schema_fingerprint'])
                or any(not isinstance(c, dict) or set(c) != {'name', 'type'}
                    or not isinstance(c['name'], str) or not c['name'] or not isinstance(c['type'], (str, dict))
                    for c in target['columns'])):
            raise ValueError('invalid reviewed measurement')
    elif set(dest) != {'database_id', 'schema_id', 'physical_name'} or target is not None or not isinstance(dest.get('physical_name'), str) or not dest['physical_name']:
        raise ValueError('invalid absent destination')
    if value['origin'] == 'native_graph':
        quality = value.get('quality')
        if (not isinstance(quality, dict) or quality.get('status') != 'PASSED'
                or type(quality.get('count')) is not int or quality['count'] < 1
                or not isinstance(quality.get('test_ids'), list)
                or len(quality['test_ids']) != quality['count']
                or any(not isinstance(i, str) or not i for i in quality['test_ids'])
                or len(set(quality['test_ids'])) != quality['count']):
            raise ValueError('invalid graph quality review')
    return value


def frozen_publication_review(preview, context, principal):
    """Check frozen associations without putting current eligibility before replay."""
    # B5 v1 descriptor-only files remain valid inputs.
    if preview['origin'] == 'native_table' and not any(k in preview for k in ('source_context', 'source_actor')):
        return
    source = saved_context(preview.get('source_context'))
    candidate = preview.get('candidate')
    ref = candidate.get('ref') if isinstance(candidate, dict) else None
    target = preview.get('target')
    quality = preview.get('quality')
    if (any(source[k] != context[k] for k in ('database_id', 'schema_id', 'warehouse_id', 'sql_catalog', 'sql_schema', 'generation_id', 'binding_revision'))
            or preview.get('publisher_actor') != principal
            or not isinstance(preview.get('source_actor'), str) or not preview['source_actor']
            or not isinstance(preview.get('source_digest'), str) or not preview['source_digest']
            or not isinstance(ref, dict) or set(ref) != {'database_id', 'schema_id', 'object_id', 'incarnation'}
            or ref.get('database_id') != source['database_id'] or ref.get('schema_id') != source['schema_id']
            or not isinstance(ref.get('object_id'), str) or not ref['object_id']
            or type(ref.get('incarnation')) is not int or not 1 <= ref['incarnation'] <= 9007199254740991
            or not isinstance(candidate.get('snapshot'), str) or not candidate['snapshot']
            or not isinstance(target, dict) or set(target) != {'catalog', 'schema', 'name'}
            or any(not isinstance(v, str) or not v for v in target.values())
            or target['catalog'] != source['sql_catalog']
            or preview['expected_target']['target'] is None and preview['expected_target']['destination']['physical_name'] != target['name']
            or not isinstance(quality, dict) or quality.get('status') != 'PASSED'
            or type(quality.get('count')) is not int or quality['count'] < 1
            or not isinstance(quality.get('test_ids'), list) or len(quality['test_ids']) != quality['count']
            or any(not isinstance(i, str) or not i for i in quality['test_ids'])
            or len(set(quality['test_ids'])) != quality['count']):
        raise ValueError('invalid frozen publication review')


def graph_publication_source(run, plan, preview, *, allow_reserved=False):
    """Check public saved task/model scopes before exposing a graph review."""
    def safe_zero(value):
        return type(value) is int and value == 0
    def same_ids(values, expected):
        return (isinstance(values, list) and len(values) == len(expected)
            and all(isinstance(v, str) for v in values) and len(set(values)) == len(expected)
            and set(values) == set(expected))
    def equal(left, right):
        return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)
    models, tests = run.get('models'), run.get('tests')
    planned_models, planned_tests = plan.get('models'), plan.get('tests')
    if (run.get('state') != 'NATIVE_VERIFIED' or run.get('native_verified') is not True
            or run.get('native_tests') is not True or run.get('tests_status') != 'PASSED'
            or run.get('cancel_requested') or run.get('actor') != plan.get('actor')
            or run.get('context') != plan.get('context') or run.get('source_digest') != plan.get('source_digest')
            or not isinstance(planned_models, list) or not isinstance(planned_tests, list)
            or not planned_models or not planned_tests or not isinstance(models, list) or not isinstance(tests, list)
            or any(not isinstance(n, dict) or not isinstance(n.get('unique_id'), str) for n in planned_models + planned_tests + models + tests)):
        raise ValueError('invalid graph publication source')
    mids, tids = [n['unique_id'] for n in planned_models], [n['unique_id'] for n in planned_tests]
    if (not same_ids([m['unique_id'] for m in models], mids) or len(set(mids)) != len(mids)
            or not same_ids([t['unique_id'] for t in tests], tids) or len(set(tids)) != len(tids)):
        raise ValueError('graph publication scope mismatch')
    task_results = {}
    tasks = run.get('task_receipts')
    for phase, expected in (('build', mids), ('test', tids)):
        task = tasks.get(phase) if isinstance(tasks, dict) else None
        actual = [m['unique_id'] for m in planned_models if m.get('materialized') == 'table'] if phase == 'build' else tids
        status = 'success' if phase == 'build' else 'pass'
        if (not isinstance(task, dict) or task.get('task') != ('RunTask' if phase == 'build' else 'TestTask')
                or task.get('completed') is not True or task.get('success') is not True
                or not same_ids(task.get('selected'), expected)
                or not isinstance(task.get('nodes'), list) or not isinstance(task.get('results'), list)
                or any(not isinstance(n, dict) or set(n) != {'node_id', 'status', 'failures', 'adapter_response'}
                    or n.get('status') != status or not isinstance(n.get('adapter_response'), dict)
                    or set(n['adapter_response']) - {'code', 'rows_affected'}
                    or not (n.get('failures') is None or type(n['failures']) is int and 0 <= n['failures'] <= 9007199254740991)
                    for n in task['nodes'] + task['results'])
                or not same_ids([n['node_id'] for n in task['nodes']], expected)
                or not same_ids([n['node_id'] for n in task['results']], actual)):
            raise ValueError('invalid graph publication task')
        task_results[phase] = {n['node_id']: n for n in task['nodes']}
        if any(not equal(n, task_results[phase][n['node_id']]) for n in task['results']):
            raise ValueError('graph task result mismatch')
    operations, used, digests = run.get('operations'), set(), set()
    if not isinstance(operations, list):
        raise ValueError('invalid graph publication operations')
    for phase, items in (('build', models), ('test', tests)):
        for item in items:
            uid = item['unique_id']
            sdk = task_results[phase][uid]
            if phase == 'build':
                pm = next(m for m in planned_models if m['unique_id'] == uid)
                if (item.get('materialized') != pm.get('materialized')
                        or not equal(item.get('table_properties'), pm.get('table_properties'))
                        or 'depends_on' in item and not equal(item['depends_on'], pm.get('depends_on'))):
                    raise ValueError('graph model scope mismatch')
                if item.get('materialized') == 'ephemeral':
                    if item.get('state') != 'EXPANDED' or item.get('candidate') is not None or item.get('operation_ids') != [] or sdk['failures'] is not None or sdk['adapter_response']:
                        raise ValueError('invalid graph ephemeral proof')
                    continue
                if item.get('materialized') != 'table' or item.get('state') != 'NATIVE_VERIFIED' or not isinstance(item.get('operation_ids'), list) or len(item['operation_ids']) != 1:
                    raise ValueError('invalid graph TABLE proof')
                operation_id = item['operation_ids'][0]
            else:
                if item.get('state') != 'PASSED' or not safe_zero(item.get('failure_count')) or not safe_zero(sdk['failures']):
                    raise ValueError('invalid graph quality proof')
                operation_id = item.get('operation_id')
            proof = item.get('native_receipt')
            if (not isinstance(operation_id, str) or not operation_id or operation_id in used
                    or not isinstance(proof, dict) or proof.get('executor') != 'native_graph'
                    or proof.get('plan_id') != plan['id'] or proof.get('node_id') != uid or proof.get('phase') != phase
                    or not isinstance(proof.get('digest'), str) or not proof['digest']
                    or not equal(proof.get('sdk_result'), sdk)):
                raise ValueError('invalid graph child proof')
            used.add(operation_id); digests.add(proof['digest'])
            linked = [o for o in operations if isinstance(o, dict) and (o.get('id') == operation_id or o.get('phase') == phase and o.get('node_id') == uid)]
            if (len(linked) != 1 or linked[0].get('id') != operation_id or linked[0].get('phase') != phase
                    or linked[0].get('node_id') != uid or linked[0].get('state') != 'FINISHED'
                    or linked[0].get('committed') is not (phase == 'build')):
                raise ValueError('graph operation association mismatch')
            if phase == 'build':
                candidate = item.get('candidate')
                ref = candidate.get('ref') if isinstance(candidate, dict) else None
                if (proof.get('native_verified') is not True or not equal(proof.get('candidate'), candidate)
                        or not isinstance(ref, dict) or set(ref) != {'database_id', 'schema_id', 'object_id', 'incarnation'}
                        or ref.get('database_id') != run['context']['database_id'] or ref.get('schema_id') != run['context']['schema_id']
                        or not isinstance(ref.get('object_id'), str) or not ref['object_id']
                        or type(ref.get('incarnation')) is not int or not 1 <= ref['incarnation'] <= 9007199254740991
                        or not isinstance(candidate.get('snapshot'), str) or not candidate['snapshot']):
                    raise ValueError('graph candidate mismatch')
            elif proof.get('test_verified') is not True or proof.get('status') != 'PASSED' or not safe_zero(proof.get('failures')):
                raise ValueError('graph quality receipt mismatch')
    selected = next(m for m in models if m['unique_id'] == preview['node_id'])
    target = preview.get('target')
    pm = next(m for m in planned_models if m['unique_id'] == preview['node_id'])
    if (len(digests) != 1 or len(operations) != len(used)
            or any(not isinstance(o, dict) or o.get('id') not in used or o.get('phase') not in ('build', 'test') for o in operations)
            or run.get('publication_allowed') is not True and not (allow_reserved and selected.get('publication_id'))
            or preview['expected_target']['destination']['schema_id'] != plan.get('target_schema')
            or not isinstance(target, dict) or set(target) != {'catalog', 'schema', 'name'}
            or target['catalog'] != run['context']['sql_catalog'] or not isinstance(target['schema'], str) or not target['schema']
            or not isinstance(pm.get('relation'), list) or len(pm['relation']) != 3
            or pm['relation'][0] != run['context']['sql_catalog'] or target['name'] != pm['relation'][2]
            or preview['expected_target']['target'] is None and preview['expected_target']['destination'].get('physical_name') != target['name']):
        raise ValueError('graph target or operation scope mismatch')


def publication_source(client, preview, context, *, allow_reserved=False):
    """Read saved source proof; never refresh the reviewed target or execute work."""
    # Older native TABLE descriptor files predate source facts. Keep that exact
    # B5 input form; complete envelopes always bind the review to its saved run.
    if preview['origin'] == 'native_table' and not any(k in preview for k in ('source_context', 'source_actor')):
        return
    reply = client.get('/api/v1/dbt/runs/' + identifier(preview['run_id']))
    run = artifact(reply.get('run'), context, 'run', client.principal, preview['run_id'], observe=True)
    model = next((m for m in run.get('models', []) if m.get('unique_id') == preview['node_id']), None)
    tests, quality = run.get('tests'), preview.get('quality')
    if (run.get('executor') != preview['origin'] or run.get('plan_id') != preview['plan_id']
            or run.get('source_digest') != preview.get('source_digest')
            or run.get('actor') != preview.get('source_actor')
            or run.get('context') != preview.get('source_context')
            or preview.get('publisher_actor') != client.principal
            or model is None or model.get('materialized') != 'table'
            or json.dumps(model.get('candidate'), sort_keys=True) != json.dumps(preview.get('candidate'), sort_keys=True)
            or not isinstance(tests, list) or not tests or not isinstance(quality, dict)
            or quality.get('status') != 'PASSED' or type(quality.get('count')) is not int
            or quality['count'] != len(tests) or not isinstance(quality.get('test_ids'), list)
            or any(not isinstance(t, dict) or not isinstance(t.get('unique_id'), str)
                or t.get('state') != 'PASSED' or type(t.get('failure_count')) is not int
                or t['failure_count'] != 0 for t in tests)
            or len({t['unique_id'] for t in tests}) != len(tests)
            or any(not isinstance(i, str) for i in quality['test_ids'])
            or sorted(quality['test_ids']) != sorted(t['unique_id'] for t in tests)):
        raise ValueError('publication source mismatch')
    if preview['origin'] == 'native_graph':
        plan_reply = client.get('/api/v1/dbt/plans/' + identifier(preview['plan_id']))
        plan = artifact(plan_reply.get('plan'), context, 'plan', client.principal, preview['plan_id'], observe=True)
        graph_publication_source(run, plan, preview, allow_reserved=allow_reserved)
        schemas = client.request('GET', '/api/v1/databases/' + quote(context['database_id'], safe='') + '/schemas').get('items')
        registered = [s for s in schemas if isinstance(s, dict) and s.get('id') == plan.get('target_schema')] if isinstance(schemas, list) else []
        if len(registered) != 1 or registered[0].get('sql_name') != preview['target']['schema']:
            raise ValueError('graph publication registered target mismatch')


def parser():
    root = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = root.add_subparsers(dest='action', required=True)
    for action in ('projects', 'compile', 'plans', 'plan', 'run', 'runs', 'status', 'cancel', 'reconcile', 'publish', 'publish-preview', 'publication'):
        p = commands.add_parser(action, allow_abbrev=False)
        p.add_argument('--database')
        p.add_argument('--schema', default=None, help='schema name or ID (default: sandbox)')
        p.add_argument('--api-url', default='http://127.0.0.1:8080')
        group = p.add_mutually_exclusive_group()
        group.add_argument('--context')
        group.add_argument('--context-file')
        if action in ('compile', 'run', 'cancel', 'reconcile', 'publish'):
            p.add_argument('--idempotency-key')
        if action == 'compile':
            p.add_argument('--project', default='lake_marts')
            p.add_argument('--source-schema', default='raw')
            p.add_argument('--target-schema', default='marts')
            p.add_argument('--staging-materialization', choices=('default', 'ephemeral', 'table'), default='default')
        if action in ('plan', 'run', 'runs'):
            p.add_argument('--plan-id', required=action != 'runs')
        if action in ('status', 'cancel', 'reconcile', 'publish', 'publish-preview'):
            p.add_argument('--run-id', required=True)
        if action == 'run':
            p.add_argument('--executor', choices=('sql_wap', 'native_table', 'native_graph'), default='sql_wap')
            p.add_argument('--node-id', help='required only for native_table; forbidden for native_graph')
            p.add_argument('--native-tests', action='store_true', help='native_table attached suite or native_graph complete saved suite')
        if action in ('publish', 'publish-preview'):
            p.add_argument('--node-id', required=True)
        if action == 'publish':
            p.add_argument('--expected-target-file', help='JSON returned by publish-preview')
        if action == 'publication':
            p.add_argument('--publication-id', required=True)
    return root


def main(argv=None, *, profile=None):
    args = parser().parse_args(argv)
    if any(isinstance(v, str) and not v.strip() for v in vars(args).values()):
        raise ValueError('nonempty flags required')
    if (args.context or args.context_file) and (args.database is not None or args.schema is not None):
        raise ValueError('context conflicts with database/schema')
    if args.action == 'publish-preview' and not (args.context or args.context_file):
        raise ValueError('explicit saved context required for publication review')
    if args.action == 'run':
        if args.native_tests and args.executor not in ('native_table', 'native_graph'):
            raise ValueError('native tests require native executor')
        if args.executor == 'native_table':
            if args.node_id is None or not re.fullmatch(r'[A-Za-z0-9_.-]{1,512}', args.node_id):
                raise ValueError('native node ID required')
        elif args.node_id is not None:
            raise ValueError('node ID requires native executor')
    profile = profile or configured_profile()
    if profile is None:
        raise ValueError('configured private profile required')
    client = DbtClient(profile, args.api_url)
    if args.context or args.context_file:
        raw = args.context or Path(args.context_file).read_text()
        client.context = saved_context(json.loads(raw))
        client.role = client.context['selected_role']
        client.headers['X-Shirokuma-Role'] = client.role
    else:
        if not args.database:
            raise ValueError('explicit database or context required')
        client.select(args.database, args.schema or 'sandbox')
    context = saved_context(client.context)
    if client.role not in ('admin', 'data_engineer'):
        raise ValueError('DBT role required')
    action, body, params = args.action, {}, {}
    kind, expected = None, None
    path = '/api/v1/dbt/'
    if action == 'projects':
        path += 'projects'
    elif action in ('compile', 'plans', 'plan'):
        path += 'plans'
        kind = 'plan'
        if action == 'plan':
            expected = identifier(args.plan_id)
            path += '/' + expected
        elif action == 'compile':
            body = dict(project_id=args.project, source_schema=args.source_schema, target_schema=args.target_schema,
                        staging_materialization=None if args.staging_materialization == 'default' else args.staging_materialization)
    elif action == 'publication':
        kind, expected = 'publication', identifier(args.publication_id)
        path += 'publications/' + expected
    else:
        path += 'runs'
        kind = 'run'
        if action == 'run':
            body = dict(plan_id=identifier(args.plan_id))
            if args.executor in ('native_table', 'native_graph'):
                body.update(executor=args.executor)
                if args.executor == 'native_table':
                    body['node_id'] = args.node_id
                if args.native_tests:
                    body['native_tests'] = True
        elif action == 'runs':
            if args.plan_id:
                params['plan_id'] = identifier(args.plan_id)
        else:
            expected = identifier(args.run_id)
            path += '/' + expected
            if action in ('cancel', 'reconcile', 'publish', 'publish-preview'):
                path += '/' + ('publication-preview' if action == 'publish-preview' else action)
            if action == 'publish':
                kind, expected = 'publication', None
                body['node_id'] = args.node_id
                if args.expected_target_file:
                    preview = publication_preview(json.loads(Path(args.expected_target_file).read_text()), context, args.run_id, args.node_id)
                    frozen_publication_review(preview, context, client.principal)
                    args.review_origin = preview['origin']
                    args.review_plan_id = preview['plan_id']
                    args.expected_target = preview['expected_target']
                    body['expected_target'] = args.expected_target
            if action == 'publish-preview':
                params['node_id'] = args.node_id
    mutation = action in ('compile', 'run', 'cancel', 'reconcile', 'publish')
    if mutation:
        key = args.idempotency_key or str(uuid.uuid4())
        try:
            reply = client.post(path, body, key)
            return validate_reply(reply, context, kind, expected, args, client.principal)
        except RequestRejected:
            raise
        except (RequestUncertain, ValueError):
            # A POST may already be accepted even when its response is unusable.
            # Withhold that response and retain only the original safe intent.
            return dict(request_uncertain=True, retry=dict(action=action, method='POST', path=path,
                idempotency_key=key, context=context, intent=body, body=dict(body, context=context)))
    else:
        reply = client.get(path, **params)
    result = validate_reply(reply, context, kind, expected, args, client.principal)
    if action == 'publish-preview':
        publication_source(client, publication_preview(reply, context, args.run_id, args.node_id), context)
    return result


if __name__ == '__main__':
    try:
        print(json.dumps(main(), ensure_ascii=False))
    except Exception:
        print('DBT request failed (details withheld). Retain the original context and idempotency key.', file=sys.stderr)
        sys.exit(1)
