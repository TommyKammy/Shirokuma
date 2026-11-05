"""Bounded HTTP-only small-load acceptance; receipts contain private test results.

Run with a new --output-dir. POSTs are never retried, including lost responses.
A failed run leaves accepted work and data for an operator to reconcile.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import ipaddress
import json
import os
from pathlib import Path
import re
import statistics
import tempfile
import threading
import time
from urllib.parse import quote, urlsplit
import uuid

from database_creation_client import CreationClient, Failure, profile_references

RUNTIME = '/api/v1/warehouses/LOCAL/runtime'
CREATIONS = '/api/v1/admin/database-creations'
SQL_FIELDS = ('database_id', 'schema_id', 'warehouse_id', 'binding_revision', 'generation_id')
TERMINAL_QUERY = {'FINISHED', 'FAILED', 'CANCELED', 'UNKNOWN'}


def require(condition, code):
    if not condition:
        raise Failure(code)


def safe_code(error):
    code = getattr(error, 'code', '')
    return code if isinstance(code, str) and re.fullmatch('[A-Z][A-Z0-9_]{0,79}', code) else 'RUNNER_FAILED'


def origin(value):
    try:
        parsed = urlsplit(value)
        local = parsed.hostname == 'localhost' or ipaddress.ip_address(parsed.hostname).is_loopback
        parsed.port
    except (ValueError, TypeError):
        local = False
    if (not local or parsed.scheme != 'http' or parsed.username or parsed.password
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise argparse.ArgumentTypeError('loopback HTTP origin required')
    return value.rstrip('/')


def bounded_int(maximum):
    def parse(value):
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError('positive integer required') from None
        if not 1 <= number <= maximum:
            raise argparse.ArgumentTypeError('value must be between 1 and ' + str(maximum))
        return number
    return parse


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--api-url', type=origin, default='http://127.0.0.1:8888')
    result.add_argument('--database', default='m1_' + uuid.uuid4().hex[:20])
    result.add_argument('--rows', type=bounded_int(10000), default=1000)
    result.add_argument('--rounds', type=bounded_int(5), default=2)
    result.add_argument('--output-dir', required=True, type=Path)
    result.add_argument('--lifecycle', action='store_true', help='also test cold wake/cancel/Chill; permit initial wake and success-only restore')
    return result


class Evidence:
    def __init__(self, directory):
        self.root = Path(directory)
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        require(not any(self.root.iterdir()), 'OUTPUT_DIRECTORY_NOT_EMPTY')
        os.chmod(self.root, 0o700)
        self.lock = threading.Lock()
        self.sequence = 0

    def save(self, label, value):
        with tempfile.NamedTemporaryFile(mode='w', dir=self.root, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                os.chmod(temporary, 0o600)
                json.dump(value, stream, sort_keys=True, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        os.replace(temporary, self.root / (label + '.json'))

    def label(self):
        with self.lock:
            self.sequence += 1
            return 'request-%05d' % self.sequence


class Runner:
    def __init__(self, args, client, evidence):
        self.args, self.client, self.evidence = args, client, evidence
        self.context = None
        self.environment = None
        self.job = None
        self.expected = [args.rows, args.rows * (args.rows + 1) // 2,
                         sum(i % 97 for i in range(1, args.rows + 1))]
        self.summary = dict(ok=False, database=args.database, rows=args.rows, rounds=args.rounds,
            phases={'setup': 'selected', 'warm': 'selected',
                    'lifecycle': 'selected' if args.lifecycle else 'not_selected', 'final': 'selected'},
            expected_tables={}, handles=[], workloads=[],
            measurement='Small sample elapsed seconds; medians are observations, not an SLA.')
        self.work_lock = threading.Lock()
        self.local = threading.local()

    def request(self, method, route, body=None, *, key=True, params=None):
        # Only locally returned relative API paths may be followed with credentials.
        require(isinstance(route, str) and route.startswith('/api/v1/')
                and not route.startswith('//') and not urlsplit(route).netloc, 'INVALID_PUBLIC_ROUTE')
        label = self.evidence.label()
        request_id = body.get('request_id') if isinstance(body, dict) else None
        record = dict(method=method, route=route, body=body, params=params,
                      key=str(uuid.uuid4()) if method == 'POST' and key else None,
                      request_id=request_id or str(uuid.uuid4()), sent_at=time.time(),
                      status='uncertain' if method == 'POST' else 'pending')
        # Durable intent is written before the network call, even for SQL/cancel/preview.
        self.evidence.save(label, record)
        if hasattr(self.local, 'receipts'):
            self.local.receipts.append(label)
        started = time.monotonic()
        try:
            reply = self.client.request(method, route, body=body, key=record['key'],
                params=params, environment=self.environment if method == 'POST' else None)
        except Exception as error:
            record.update(error=safe_code(error), elapsed_seconds=time.monotonic() - started,
                          failed_at=time.time())
            if method != 'POST':
                record['status'] = 'failed'
            elif route == RUNTIME + '/chill' and record['error'] == 'ADMISSION_CONFLICT':
                record['status'] = 'rejected'
            self.evidence.save(label, record)
            raise Failure(record['error']) from None
        record.update(status='acknowledged', ack_at=time.time(),
                      elapsed_seconds=time.monotonic() - started, result=reply)
        terminal = reply.get('state') in TERMINAL_QUERY or reply.get('phase') in ('SUCCEEDED', 'FAILED', 'UNKNOWN')
        terminal = terminal or (reply.get('run') or {}).get('status') in ('succeeded', 'failed', 'canceled')
        terminal = terminal or (reply.get('operation') or {}).get('state') in TERMINAL_QUERY
        if terminal:
            record['terminal_at'] = record['ack_at']
        self.evidence.save(label, record)
        if method == 'POST':
            with self.work_lock:
                self.summary['handles'].append(dict(receipt=label, route=route,
                    id=reply.get('id'), operation_id=reply.get('operation_id'),
                    creation_id=reply.get('creation_id'), operation=(reply.get('operation') or {}).get('id'),
                    request_id=reply.get('request_id', request_id), cancel_uri=reply.get('cancel_uri'),
                    next_uri=reply.get('next_uri'), job=reply.get('job'), run=reply.get('run')))
        return reply

    def get(self, route, **params):
        return self.request('GET', route, params=params or None)

    def post(self, route, body, *, key=True):
        return self.request('POST', route, body, key=key)

    def poll(self, fetch, done):
        deadline = time.monotonic() + 180
        while True:
            require(time.monotonic() < deadline, 'POLL_TIMEOUT')
            value = fetch()
            if done(value):
                return value
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))

    def runtime(self):
        return self.get(RUNTIME)

    @staticmethod
    def unblocked(value):
        counts = value.get('blockers', {})
        require(set(counts) == {'runtime_operations', 'admissions', 'unknown', 'creation'}, 'INVALID_BLOCKER_RESPONSE')
        def zero(item):
            return all(zero(v) for v in item.values()) if isinstance(item, dict) else type(item) is int and item == 0
        require(isinstance(counts['creation'], dict) and set(counts['creation']) == {'requests', 'effects', 'roots'},
                'INVALID_BLOCKER_RESPONSE')
        require(zero(counts), 'RUNTIME_BLOCKED')

    @staticmethod
    def physical_active(value):
        return (value.get('physical_state') == 'active' and value.get('replicas') == 2
                and value.get('ready_replicas') == 2)

    @classmethod
    def active(cls, value):
        return cls.physical_active(value) and value.get('engine_verified') is True

    def wait_active(self, *, verified=True):
        return self.poll(self.runtime, self.active if verified else self.physical_active)

    def runtime_operation(self, action):
        ack = self.post(RUNTIME + '/' + action, {})
        require(ack.get('phase') == 'ACCEPTED' and ack.get('operation_id'), 'INVALID_RUNTIME_ACK')
        return self.poll(lambda: self.get(RUNTIME + '/operations/' + quote(ack['operation_id'], safe='')),
                         lambda value: value.get('phase') not in ('ACCEPTED', 'RUNNING'))

    def chill(self):
        self.unblocked(self.runtime())
        result = self.runtime_operation('chill')
        require(result.get('phase') == 'SUCCEEDED', 'CHILL_FAILED')
        return self.poll(self.runtime, lambda value: value.get('physical_state') == 'chilled'
                         and value.get('replicas') == 0 and value.get('ready_replicas') == 0)

    def setup(self):
        before = self.runtime()
        self.summary['runtime_before'] = before
        self.unblocked(before)
        require(before.get('policy', {}).get('minutes') == 0, 'AUTO_CHILL_MUST_BE_DISABLED')
        self.policy = before['policy']
        self.initially_chilled = before.get('physical_state') == 'chilled'
        if self.initially_chilled:
            require(self.args.lifecycle and before.get('replicas') == 0 and before.get('ready_replicas') == 0,
                    'INITIAL_WAKE_REQUIRES_LIFECYCLE')
            require(self.runtime_operation('wake').get('phase') == 'SUCCEEDED', 'INITIAL_WAKE_FAILED')
        else:
            require(before.get('physical_state') == 'active' and before.get('replicas') == 2
                    and before.get('ready_replicas') == 2, 'INITIAL_RUNTIME_NOT_ACTIVE')
        capabilities = self.get(CREATIONS + '/capabilities')
        require(capabilities.get('available') is True and capabilities.get('creation_mode') == 'logical_v1',
                'LOGICAL_CREATION_UNAVAILABLE')
        self.environment = capabilities.get('environment_id')
        require(isinstance(self.environment, str) and self.environment, 'ENVIRONMENT_REQUIRED')
        inventory = self.get('/api/v1/databases')['items']
        require(not any(item['sql_catalog'] == self.args.database for item in inventory), 'DATABASE_ALREADY_EXISTS')
        try:
            self.get(CREATIONS + '/by-name/' + quote(self.args.database, safe=''))
        except Failure as error:
            require(error.code == 'DATABASE_NOT_FOUND', 'DATABASE_FRESHNESS_UNVERIFIED')
        else:
            raise Failure('DATABASE_ALREADY_EXISTS')
        ack = self.post(CREATIONS, dict(sql_catalog=self.args.database, display_name=self.args.database,
            description='Bounded pre-M1 small-load acceptance', initial_grants=[
                dict(principal_id=self.client.principal, maximum_role='admin')]))
        require(ack.get('creation_id'), 'INVALID_CREATION_ACK')
        detail = self.poll(lambda: self.get(CREATIONS + '/by-name/' + quote(self.args.database, safe='')),
                           lambda value: value.get('progress_code') == 'READY' or value.get('progress_code') in
                           ('BLOCKED', 'UNKNOWN', 'STOPPED', 'RECONCILE_REQUIRED', 'REPLAN_REQUIRED'))
        require(detail.get('progress_code') == 'READY' and detail.get('creation_id') == ack['creation_id'], 'CREATION_NOT_READY')
        matches = [item for item in self.get('/api/v1/databases')['items'] if item['sql_catalog'] == self.args.database]
        require(len(matches) == 1 and matches[0]['id'] == detail['database_id'], 'CREATION_INVENTORY_MISMATCH')
        database = matches[0]
        schemas = self.get('/api/v1/databases/' + quote(database['id'], safe='') + '/schemas')['items']
        raw = [item for item in schemas if item['sql_name'] == 'raw']
        require(len(raw) == 1, 'RAW_SCHEMA_REQUIRED')
        self.context = dict(database_id=database['id'], schema_id=raw[0]['id'], warehouse_id='LOCAL',
            sql_catalog=database['sql_catalog'], sql_schema='raw', selected_role=self.client.role,
            binding_revision=database['binding_revision'], generation_id=database['generation_id'],
            grant_revision=database['grant_revision'])
        self.evidence.save('context', self.context)
        self.ingest('seed')
        self.verify(self.sql('seed'))
        self.save_job()

    def query_body(self, table):
        return dict(sql='SELECT COUNT(*), SUM(id), SUM(value) FROM ' + table,
                    request_id=str(uuid.uuid4()), worksheet_id=str(uuid.uuid4()),
                    context={field: self.context[field] for field in SQL_FIELDS}, mode='read-only', max_rows=100)

    def submit_sql(self, table='seed'):
        return self.post('/api/v1/queries', self.query_body(table), key=False)

    def collect_sql(self, page):
        rows = []
        deadline = time.monotonic() + 180
        while True:
            rows.extend(page.get('data', []))
            require(not page.get('error'), 'SQL_FAILED')
            state = page.get('state')
            require(state not in {'FAILED', 'CANCELED', 'UNKNOWN'}, 'SQL_NOT_FINISHED')
            if not page.get('next_uri'):
                require(state == 'FINISHED', 'SQL_NOT_FINISHED')
                return rows
            require(time.monotonic() < deadline, 'POLL_TIMEOUT')
            time.sleep(0.5)
            page = self.get(page['next_uri'])

    def sql(self, table='seed'):
        return self.collect_sql(self.submit_sql(table))

    def verify(self, rows):
        require(rows == [self.expected] and all(type(value) is int for value in rows[0]), 'AGGREGATE_MISMATCH')

    def ingest(self, table):
        self.wait_active(verified=False)
        with self.work_lock:
            self.summary['expected_tables'][table] = self.expected
        content = 'id,bucket,value\n' + ''.join('%d,%d,%d\n' % (i, i % 7, i % 97)
                                                     for i in range(1, self.args.rows + 1))
        body = dict(context=self.context, filename=table + '.csv', content_base64=base64.b64encode(content.encode()).decode(),
            mode='create', format='csv', encoding='utf-8', delimiter=',', schema_hints={}, key_columns=[],
            batch_size=500, atomic_append=False, destination=dict(database_id=self.context['database_id'],
            schema_id=self.context['schema_id'], physical_name=table))
        preview = self.post('/api/v1/ingests/preview', body)['preview']
        fingerprint = preview.get('request_fingerprint')
        require(isinstance(fingerprint, str) and re.fullmatch('[a-f0-9]{64}', fingerprint), 'INVALID_PREVIEW')
        self.wait_active(verified=False)
        operation = self.post('/api/v1/ingests', dict(body, expected_preview_fingerprint=fingerprint))['operation']
        if operation.get('state') not in ('FINISHED', 'FAILED', 'CANCELED', 'UNKNOWN'):
            identifier = operation['id']
            operation = self.poll(lambda: self.get('/api/v1/dataops/' + quote(identifier, safe=''),
                context=json.dumps(self.context))['operation'],
                lambda value: value.get('state') in ('FINISHED', 'FAILED', 'CANCELED', 'UNKNOWN'))
        require(operation.get('state') == 'FINISHED' and operation.get('committed') is True
                and not operation.get('cleanup_error') and not (operation.get('result') or {}).get('cleanup_error'), 'INGEST_NOT_COMMITTED')
        return operation

    def save_job(self):
        n, sum_id, sum_value = self.expected
        integrity = ('SELECT (count(*)<>%d OR COALESCE(sum(id),0)<>%d OR COALESCE(sum(value),0)<>%d) AS bad FROM seed'
                     % (n, sum_id, sum_value))
        definition = dict(name='m1_small_load', description='Small deterministic integrity/read job', parameters={}, steps=[
            dict(name='integrity', action='alert', sql=integrity, mode='read-only'),
            dict(name='read', action='sql', sql=self.query_body('seed')['sql'], mode='read-only')])
        self.job = self.post('/api/v1/jobs', dict(context=self.context, expected_revision=0, definition=definition))['job']
        require(self.job.get('id') and self.job.get('revision') == 1, 'INVALID_JOB_ACK')

    def submit_job(self):
        return self.post('/api/v1/jobs/' + quote(self.job['id'], safe='') + '/runs',
            dict(context=self.context, expected_revision=1, arguments={}))['run']

    def collect_job(self, run):
        require(run.get('id'), 'INVALID_JOB_RUN_ACK')
        if run.get('status') in ('queued', 'running'):
            run = self.poll(lambda: self.get('/api/v1/jobs/runs/' + quote(run['id'], safe=''),
                context=json.dumps(self.context))['run'], lambda value: value.get('status') not in ('queued', 'running'))
        require(run.get('status') == 'succeeded', 'JOB_FAILED')
        steps = run.get('steps', [])
        require(len(steps) == 2 and [step.get('name') for step in steps] == ['integrity', 'read']
                and all(step.get('status') == 'succeeded' for step in steps), 'JOB_STEP_FAILED')
        require((steps[0].get('summary') or {}).get('triggered') is False, 'JOB_INTEGRITY_FAILED')
        summary = steps[1].get('summary') or {}
        require(summary.get('row_count') == 1, 'JOB_READ_FAILED')
        if 'data' in summary:
            self.verify(summary['data'])
        return run

    def job_run(self):
        return self.collect_job(self.submit_job())

    def measured(self, kind, action, barrier=None):
        if barrier is not None:
            barrier.wait(timeout=10)
        self.local.receipts = []
        record = dict(kind=kind, sent_at=time.time(), status='failed')
        started = time.monotonic()
        try:
            value = action()
            record['status'] = 'passed'
            return value
        except Exception as error:
            record['error'] = safe_code(error)
            raise
        finally:
            record.update(terminal_at=time.time(), elapsed_seconds=time.monotonic() - started,
                          receipts=self.local.receipts)
            del self.local.receipts
            with self.work_lock:
                self.summary['workloads'].append(record)

    def warm(self):
        self.measured('baseline_sql', lambda: self.verify(self.sql()))
        for number in range(1, self.args.rounds + 1):
            table = 'mix_' + str(number)
            barrier = threading.Barrier(3)
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = [pool.submit(self.measured, kind, action, barrier) for kind, action in (
                    ('sql', lambda: self.verify(self.sql())), ('ingest', lambda: self.ingest(table)), ('job', self.job_run))]
                failures = []
                for future in futures:
                    try:
                        future.result()
                    except Exception as error:
                        failures.append(error)
                if failures:
                    raise failures[0]
            self.verify(self.sql(table))

    def lifecycle(self):
        self.chill()
        a = self.submit_sql()
        b = self.submit_sql()
        require(all(page.get('state') == 'QUEUED' and page.get('backend_query_id') is None for page in (a, b)),
                'COLD_SQL_NOT_QUEUED')
        canceled = self.post(b['cancel_uri'], {})
        require(canceled.get('state') == 'CANCELED', 'COLD_CANCEL_FAILED')
        run = self.submit_job()
        require(run.get('status') in ('queued', 'running'), 'COLD_JOB_NOT_PENDING')
        # One attempted stop during outstanding work; never issue a fallback stop.
        try:
            rejected = self.runtime_operation('chill')
        except Failure as error:
            require(error.code == 'ADMISSION_CONFLICT', 'CHILL_CONFLICT_UNVERIFIED')
        else:
            require(rejected.get('phase') == 'FAILED' and rejected.get('error_code') == 'ADMISSION_CONFLICT',
                    'CHILL_CONFLICT_UNVERIFIED')
        self.verify(self.collect_sql(a))
        self.collect_job(run)
        after_cancel = self.get('/api/v1/queries/' + quote(b['id'], safe=''))
        require(after_cancel.get('state') == 'CANCELED' and after_cancel.get('backend_query_id') is None,
                'CANCELED_SQL_EXECUTED')
        active = self.wait_active()
        self.unblocked(active)
        require(active['policy'] == self.policy, 'POLICY_CHANGED')

    def final(self):
        for table in self.summary['expected_tables']:
            self.verify(self.sql(table))
        active = self.wait_active()
        self.unblocked(active)
        require(active['policy'] == self.policy, 'POLICY_CHANGED')
        if self.initially_chilled:
            self.chill()
        after = self.runtime()
        self.summary['runtime_after'] = after
        self.unblocked(after)
        require(after['policy'] == self.policy, 'POLICY_CHANGED')
        require(self.initially_chilled and after.get('physical_state') == 'chilled'
                and after.get('replicas') == 0 and after.get('ready_replicas') == 0 or
                not self.initially_chilled and self.active(after), 'INITIAL_STATE_NOT_RESTORED')

    def execute(self):
        phase = 'setup'
        try:
            for phase, action in [('setup', self.setup), ('warm', self.warm),
                    *([('lifecycle', self.lifecycle)] if self.args.lifecycle else []), ('final', self.final)]:
                action()
                self.summary['phases'][phase] = 'passed'
            self.summary['ok'] = True
        except Exception as error:
            self.summary['phases'][phase] = 'failed'
            self.summary['error'] = safe_code(error)
            for name, status in self.summary['phases'].items():
                if status == 'selected':
                    self.summary['phases'][name] = 'not_run'
            try:
                self.summary['runtime_after'] = self.runtime()
            except Exception as observation_error:
                self.summary['runtime_after'] = dict(unavailable=True, error=safe_code(observation_error))
        finally:
            self.summary['failure_count'] = sum(status == 'failed' for status in self.summary['phases'].values())
            workloads = self.summary['workloads']
            self.summary['metrics'] = {kind: dict(samples=len(items), failures=sum(item['status'] != 'passed' for item in items),
                median_seconds=statistics.median(item['elapsed_seconds'] for item in items))
                for kind in sorted({item['kind'] for item in workloads})
                for items in [[item for item in workloads if item['kind'] == kind]]}
            self.evidence.save('summary', self.summary)
        return 0 if self.summary['ok'] else 1


def main(argv=None):
    args = parser().parse_args(argv)
    evidence = None
    try:
        evidence = Evidence(args.output_dir)
        require(re.fullmatch('[a-z][a-z0-9_]{0,30}', args.database) is not None
                and args.database not in {'system', 'information_schema', 'tpch', 'tpcds', 'iceberg'}, 'INVALID_DATABASE_NAME')
        client = CreationClient(profile_references(), args.api_url)
        return Runner(args, client, evidence).execute()
    except Exception as error:
        if evidence is not None:
            evidence.save('summary', dict(ok=False, error=safe_code(error), failure_count=1, phases=dict(
                setup='failed', warm='not_run', lifecycle='not_run' if args.lifecycle else 'not_selected', final='not_run')))
        print(json.dumps(dict(ok=False, error=safe_code(error))))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
