"""Allowlisted local operations and normalized, content-free audit dispatch."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
LOCAL = HERE.parent
CONTEXT = 'colima-mac-studio-solo'
STATE = ROOT / 'tmp/local-experiment/gateway'
NAMESPACE = 'shirokuma-dev'
PLATFORM_COMPONENTS = ('lakehouse', 'superset', 'metadata', 'agenthouse', 'loki')
NO_ARGS = {'type': 'object', 'properties': {}, 'additionalProperties': False}
SCOPED_ARGS = {'type': 'object', 'properties': {
    'namespace': {'type': 'string', 'enum': [NAMESPACE], 'default': NAMESPACE},
    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50, 'default': 20}},
    'additionalProperties': False}
RECENT_ARGS = {'type': 'object', 'properties': {
    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20, 'default': 10},
    'status': {'type': 'string', 'enum': ['any', 'success', 'failed', 'blocked'], 'default': 'any'}},
    'additionalProperties': False}
TOOLS = {
    'shirokuma_doctor': ('Report bounded local experiment readiness.', 'shirokuma', NO_ARGS),
    'local_platform_status': ('Observe five fixed local runtime groups; unknown is distinct from not-ready.', 'shirokuma', NO_ARGS),
    'k8s_read_pods': ('List sanitized pod readiness in the Shirokuma development namespace.', 'kubernetes', SCOPED_ARGS),
    'k8s_read_events': ('List event reasons and object identities; omit free-text messages and Secret events.', 'kubernetes', SCOPED_ARGS),
    'dbt_list_models': ('List the fixed local dbt project models and latest verified run dependencies.', 'dbt', NO_ARGS),
    'trino_explain_fixture': ('EXPLAIN the fixed retained Iceberg fixture SELECT; accepts no SQL.', 'trino', NO_ARGS),
    'openmetadata_get_lineage': ('Read lineage for the fixed retained Iceberg fixture.', 'openmetadata', NO_ARGS),
    'agenthouse_recent': ('Read a bounded projection of structured Pawprints.', 'clickhouse', RECENT_ARGS),
    'gateway_policy': ('Describe this local allowlist and inactive external delivery tools.', 'gateway', NO_ARGS),
}


class PolicyDenied(RuntimeError):
    pass


class BackendError(RuntimeError):
    pass


def validate(name: str, arguments: Any) -> dict[str, Any]:
    if name not in TOOLS:
        raise PolicyDenied('tool is not in the local allowlist')
    if not isinstance(arguments, dict):
        raise PolicyDenied('arguments must be an object')
    properties = TOOLS[name][2]['properties']
    if set(arguments) - set(properties):
        raise PolicyDenied('unexpected arguments are not allowed')
    result = {}
    for key, spec in properties.items():
        value = arguments.get(key, spec['default'])
        if spec['type'] == 'integer':
            if type(value) is not int or not spec['minimum'] <= value <= spec['maximum']:
                raise PolicyDenied('integer argument is outside its allowed bounds')
        elif not isinstance(value, str) or value not in spec['enum']:
            raise PolicyDenied('argument is outside the fixed local scope')
        result[key] = value
    return result


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONTAINER_STATUS = load_module('gateway_container_status', HERE / 'container_status.py')
POD_USAGE = load_module('gateway_pod_usage', HERE / 'pod_usage.py')


def safe_name(value: Any, maximum: int = 128) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        return 'unavailable'
    return value if re.fullmatch(r'[A-Za-z0-9_.:/-]+', value) else 'redacted'


def safe_uid(value: Any) -> str | None:
    # UIDs are opaque identifiers; do not merge an unknown identity with a
    # literal "unavailable" marker, or normalize it as if every UID were a UUID.
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value) else None


def safe_timestamp(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    matched = re.fullmatch(
        r'([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:\.([0-9]{1,9}))?(Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])', value)
    if matched is None:
        return None
    try:
        # Python 3.9 accepts only three or six fractional digits. Normalize to
        # microseconds explicitly; sub-microsecond digits remain truncated as
        # in newer datetime versions and in this fixed-microsecond contract.
        clock, fraction, zone = matched.groups()
        normalized = clock + '.' + (fraction or '').ljust(6, '0')[:6] + zone.replace('Z', '+00:00')
        parsed = datetime.fromisoformat(normalized).astimezone(timezone.utc)
        return parsed.isoformat(timespec='microseconds').replace('+00:00', 'Z')
    except (ValueError, OverflowError):
        return None


def observation_time() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def platform_projection(report: Any) -> dict:
    """Validate fixed readiness facts and omit component reasons and other text."""
    required = {'profile', 'observed_at', 'ready', 'components'}
    if (not isinstance(report, dict) or not required <= set(report)
            or set(report) - required - {'scope'} or report['profile'] != 'local-experiment'
            or type(report['ready']) is not bool):
        raise BackendError('invalid local platform report')
    observed_at = safe_timestamp(report['observed_at'])
    components = report['components']
    if observed_at is None or not isinstance(components, dict) or set(components) != set(PLATFORM_COMPONENTS):
        raise BackendError('invalid local platform observation scope')
    projected = {}
    for name in PLATFORM_COMPONENTS:
        value = components[name]
        if (not isinstance(value, dict) or not {'ready', 'status'} <= set(value)
                or set(value) - {'ready', 'status', 'reason'} or type(value['ready']) is not bool
                or not isinstance(value['status'], str) or value['status'] not in {'ready', 'not-ready', 'unknown'}
                or value['ready'] != (value['status'] == 'ready')):
            raise BackendError('invalid local platform component readiness')
        projected[name] = {'ready': value['ready'], 'status': value['status']}
    if report['ready'] != all(value['ready'] for value in projected.values()):
        raise BackendError('local platform aggregate readiness disagrees')
    return {'profile': 'local-experiment', 'observed_at': observed_at,
            'ready': report['ready'], 'components': projected}


def stop_platform_worker(process) -> None:
    """Reap only the freshly created worker group, including its CLI children."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        # Detached descendants are not expected from these fixed read helpers;
        # never extend cleanup into a name-based kill of other host processes.
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        process.poll()


def event_last_observed_at(item: dict) -> str | None:
    # core/v1 Event: series.lastObservedTime and lastTimestamp describe the
    # latest occurrence; eventTime describes the FIRST observed occurrence.
    # https://kubernetes.io/docs/reference/kubernetes-api/core/event-v1/
    series = item.get('series')
    if isinstance(series, dict):
        observed = safe_timestamp(series.get('lastObservedTime'))
        if observed is not None:
            return observed
    observed = safe_timestamp(item.get('lastTimestamp'))
    if observed is not None:
        return observed
    count = item.get('count', 1)
    if series is None and type(count) is int and count == 1:
        return safe_timestamp(item.get('eventTime'))
    # Repeated events with no usable last-occurrence field remain unknown.
    # metadata.creationTimestamp is API-object creation, not event recurrence.
    return None


class Backends:
    def __init__(self):
        self.agenthouse = load_module('gateway_agenthouse', LOCAL / 'agenthouse/manage.py')

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return getattr(self, name)(**arguments)

    def command_json(self, args: list[str], timeout: int = 60, accepted_codes=(0,)) -> dict:
        if args[0] == 'kubectl':
            # A named context can be edited; check the actual local endpoint
            # before sending even a read to that authenticated cluster.
            scope = load_module('gateway_local_scope', LOCAL / 'local_experiment.py')
            scope.check_context()
        result = subprocess.run(['rtk', 'proxy', *args], cwd=ROOT,
                                capture_output=True, text=True, timeout=timeout)
        if result.returncode not in accepted_codes or len(result.stdout) > 2 * 1024 * 1024:
            raise BackendError('local command failed or exceeded the output limit')
        return json.loads(result.stdout)

    def shirokuma_doctor(self) -> dict:
        report = self.command_json(['go', 'run', './cmd/shirokuma', 'doctor', '--profile',
                                    'local-experiment', '--output', 'json'], accepted_codes=(0, 1))
        # Bounded doctor data is already a repository-defined structured contract;
        # never return arbitrary CLI stderr or command text.
        return report

    def local_platform_status(self) -> dict:
        # Match the workflow's minimal child environment. Do not inherit proxy,
        # tracing, cloud credentials, or alternate Kubernetes/Docker settings.
        environment = {key: value for key, value in os.environ.items()
                       if key in {'PATH', 'HOME', 'USER', 'TMPDIR', 'LANG', 'LC_ALL'}}
        # The worker checks scope before any status read, inside this same
        # deadline. Its new process group lets timeout cleanup include kubectl,
        # Docker clients and helper subprocesses without touching their services.
        process = subprocess.Popen(['rtk', 'proxy', sys.executable, str(HERE / 'platform_worker.py')],
                                   cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, env=environment, start_new_session=True)
        try:
            stdout, _stderr = process.communicate(timeout=75)
        except subprocess.TimeoutExpired:
            stop_platform_worker(process)
            raise BackendError('local platform worker timed out') from None
        except BaseException:
            stop_platform_worker(process)
            raise
        if process.returncode not in (0, 1) or len(stdout) > 2 * 1024 * 1024:
            raise BackendError('local platform worker failed or exceeded the output limit')
        return platform_projection(json.loads(stdout))


    def k8s_read_pods(self, namespace: str, limit: int) -> dict:
        data = self.command_json(['kubectl', '--context', CONTEXT, '-n', namespace,
                                  'get', 'pods', '--chunk-size=50', '-o', 'json'])
        observed_at = observation_time()
        pods = []
        for item in data.get('items', [])[:limit]:
            status = item.get('status', {})
            containers = status.get('containerStatuses', [])
            pods.append({'name': safe_name(item.get('metadata', {}).get('name')),
                         'uid': safe_uid(item.get('metadata', {}).get('uid')),
                         'phase': safe_name(status.get('phase')),
                         'ready_containers': sum(x.get('ready') is True for x in containers),
                         'total_containers': len(containers),
                         'restarts': sum(x.get('restartCount', 0) for x in containers),
                         'container_observation': CONTAINER_STATUS.project_container_observation(
                             item, timestamp=safe_timestamp)})
        return {'namespace': namespace, 'observed_at': observed_at, 'pods': pods,
                'truncated': len(data.get('items', [])) > limit,
                'resource_usage': POD_USAGE.collect(data, limit=limit, fetch=self.pod_usage_get,
                                                     now=observation_time)}

    def pod_usage_get(self, operation: str, pod_name: str) -> dict:
        return POD_USAGE.fixed_get(operation, pod_name)

    def k8s_read_events(self, namespace: str, limit: int) -> dict:
        data = self.command_json(['kubectl', '--context', CONTEXT, '-n', namespace,
                                  'get', 'events', '--chunk-size=50', '-o', 'json'])
        observed_at = observation_time()
        items = [item for item in data.get('items', [])
                 if item.get('involvedObject', {}).get('kind') != 'Secret']
        events = [{'reason': safe_name(x.get('reason')), 'type': safe_name(x.get('type')),
                   'uid': safe_uid(x.get('metadata', {}).get('uid')),
                   'object_kind': safe_name(x.get('involvedObject', {}).get('kind')),
                   'object_name': safe_name(x.get('involvedObject', {}).get('name')),
                   'object_uid': safe_uid(x.get('involvedObject', {}).get('uid')),
                   'last_observed_at': event_last_observed_at(x),
                   'count': x.get('count', 1)} for x in items]
        events.sort(key=lambda event: (event['last_observed_at'] or '', event['uid'] or '', event['object_name']),
                    reverse=True)
        return {'namespace': namespace, 'observed_at': observed_at, 'events': events[:limit],
                'truncated': len(events) > limit, 'free_text_messages': 'omitted'}

    def dbt_list_models(self) -> dict:
        models = sorted(p.stem for p in (LOCAL / 'dataops/dbt/models').glob('*.sql'))
        candidates = sorted((ROOT / 'tmp/local-experiment/dataops').glob('*/summary.json'),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        evidence = None
        for candidate in candidates:
            if candidate.is_symlink() or candidate.parent.is_symlink():
                continue
            summary = json.loads(candidate.read_text())
            if summary.get('result') != 'passed':
                continue
            manifest_path = candidate.parent / 'first/build-target/manifest.json'
            if not manifest_path.is_file() or manifest_path.is_symlink():
                continue
            manifest = json.loads(manifest_path.read_text())
            evidence = {'run_token': safe_name(summary.get('run_token')), 'verified': True,
                        'models': [{'name': safe_name(node.get('name')),
                                    'depends_on': [safe_name(x) for x in node.get('depends_on', {}).get('nodes', [])]}
                                   for node in manifest.get('nodes', {}).values()
                                   if node.get('resource_type') == 'model' and node.get('name') in models]}
            break
        return {'project': 'shirokuma_local_dataops', 'models': models, 'latest_verified_run': evidence}

    def trino_explain_fixture(self) -> dict:
        # Import the existing tunnel lifecycle rather than leaving a persistent port.
        sys.path.insert(0, str(LOCAL)) if str(LOCAL) not in sys.path else None
        from local_session import trino_tunnel
        from local_experiment import check_context
        from local_trino_smoke import TrinoClient
        check_context()
        with trino_tunnel() as url:
            result = TrinoClient(url, timeout=30).query(
                'EXPLAIN SELECT id, name FROM iceberg.smoke.fixture_v1 ORDER BY id')
        plan = '\n'.join(str(row[0]) for row in result['rows'])
        if len(plan) > 32000:
            raise BackendError('query plan exceeded the bounded result size')
        return {'query_id': result['query_id'], 'statement': 'fixed-retained-fixture-select', 'plan': plan}

    def openmetadata_get_lineage(self) -> dict:
        sys.path.insert(0, str(LOCAL)) if str(LOCAL) not in sys.path else None
        from metadata.client import Client
        from metadata.runtime import STATE as METADATA_STATE
        client = Client()
        client.authenticate(METADATA_STATE)
        table = client.request('GET', '/tables/name/shirokuma_local.iceberg.smoke.fixture_v1?fields=owners')
        table_id = str(uuid.UUID(table['id']))
        lineage = client.request('GET', f'/lineage/table/{table_id}?upstreamDepth=3&downstreamDepth=3')
        nodes = [lineage.get('entity', {})] + lineage.get('nodes', [])
        return {'fixture': 'shirokuma_local.iceberg.smoke.fixture_v1',
                'nodes': [{'id': str(uuid.UUID(x['id'])), 'type': safe_name(x.get('type')),
                           'name': safe_name(x.get('fullyQualifiedName'), 256)} for x in nodes[:30] if x.get('id')],
                'upstream_edges': [{'from': str(uuid.UUID(x['fromEntity'])), 'to': str(uuid.UUID(x['toEntity']))}
                                   for x in lineage.get('upstreamEdges', [])[:30]],
                'downstream_edges': [{'from': str(uuid.UUID(x['fromEntity'])), 'to': str(uuid.UUID(x['toEntity']))}
                                     for x in lineage.get('downstreamEdges', [])[:30]]}

    def agenthouse_recent(self, limit: int, status: str) -> dict:
        where = '' if status == 'any' else f" WHERE status = '{status}'"
        sql = ('SELECT event_id, timestamp, task_id, action_type, target_system, status, blocked_by_policy, '
               'trace_id, duration_ms, prompt_tokens, completion_tokens, inference_cost_estimate '
               'FROM agenthouse.pawprints FINAL' + where +
               f' ORDER BY timestamp DESC, event_id DESC LIMIT {limit} FORMAT JSON')
        return {'events': json.loads(self.agenthouse.Client().query(sql))['data']}

    def gateway_policy(self) -> dict:
        return {'mode': 'local-experiment', 'transport': 'stdio', 'tools': sorted(TOOLS),
                'external_github': 'inactive', 'arbitrary_shell_sql_urls_paths': 'not accepted',
                'audit': 'ClickHouse Pawprints and Loki records; failure prevents a successful tool result',
                'credentials': 'local scoped state; never tool inputs or outputs'}


class Dispatcher:
    def __init__(self, backends, audit, tracer=None, task_id: str | None = None, parent_context=None):
        self.backends = backends
        self.audit = audit
        self.tracer = tracer
        self.task_id = task_id or 'gateway-' + uuid.uuid4().hex
        self.parent_context = parent_context

    def call(self, name: str, arguments: Any) -> dict[str, Any]:
        known_name = name if name in TOOLS else 'unknown_tool'
        manager = (self.tracer.start_as_current_span('mcp.' + known_name, context=self.parent_context, record_exception=False,
                                                    set_status_on_exception=False)
                   if self.tracer else nullcontext(None))
        with manager as span:
            context = span.get_span_context() if span else None
            trace_id = f'{context.trace_id:032x}' if context else uuid.uuid4().hex
            span_id = f'{context.span_id:016x}' if context else uuid.uuid4().hex[:16]
            start = time.monotonic()
            status, payload, code = 'success', None, None
            try:
                checked = validate(name, arguments)
                payload = self.backends.execute(name, checked)
            except PolicyDenied:
                status, code = 'blocked', 'POLICY_DENIED'
            except Exception:
                status, code = 'failed', 'BACKEND_FAILED'
            duration = max(0, int((time.monotonic() - start) * 1000))
            event = {'action_type': 'mcp.' + known_name,
                     'target_system': TOOLS[name][1] if name in TOOLS else 'gateway',
                     'status': status, 'blocked_by_policy': status == 'blocked',
                     'task_id': self.task_id, 'trace_id': trace_id, 'duration_ms': duration}
            if span:
                from opentelemetry.trace import Status, StatusCode
                for key, value in {'task_id': self.task_id, 'tool': known_name,
                                   'status': status, 'blocked_by_policy': status == 'blocked'}.items():
                    span.set_attribute(key, value)
                span.set_status(Status(StatusCode.OK if status == 'success' else StatusCode.ERROR,
                                       code if status != 'success' else None))
            envelope = {'status': status, 'task_id': self.task_id, 'trace_id': trace_id,
                        'span_id': span_id, 'duration_ms': duration}
            try:
                event_id = self.audit(event, span_id)
                envelope.update({'audit': 'delivered', 'event_id': event_id})
            except Exception:
                if span:
                    span.set_attribute('audit_delivery', 'failed')
                    span.set_status(Status(StatusCode.ERROR, 'AUDIT_DELIVERY_FAILED'))
                return {**envelope, 'status': 'failed', 'backend_status': status,
                        'audit': 'failed', 'error_code': 'AUDIT_DELIVERY_FAILED'}
            if code:
                return {**envelope, 'error_code': code}
            return {**envelope, 'data': payload}
