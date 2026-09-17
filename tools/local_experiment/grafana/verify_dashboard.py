#!/usr/bin/env python3
"""Compare anonymous Grafana panel frames with bounded, SELECT-only Pawprints reads.

No URL, SQL, credentials, filters or output paths are accepted from the CLI.
Time macros follow the pinned 4.21.3 source:
https://github.com/grafana/clickhouse-datasource/blob/v4.21.3/pkg/macros/macros.go
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys
import time
import urllib.parse
import uuid

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MANAGE = load('grafana_dashboard_verifier_runtime', HERE / 'manage.py')
DASH = load('grafana_dashboard_verifier_source', HERE / 'dashboard.py')
SESSION = load('grafana_dashboard_verifier_session', HERE.parent / 'local_session.py')
ROOT = HERE.parents[2]
INTERVAL_MS = 60000
BOUNDS = {'attention': 100, 'events_over_time': 1000, 'phase_latency': 100, 'model_usage': 50, 'timeline': 250}


class VerificationError(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise VerificationError(code)


def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def panel_targets(value):
    require(type(value) is dict and value.get('uid') == DASH.UID, 'DASHBOARD_IDENTITY_DIFFERS')
    panels = [panel for panel in value.get('panels', []) if panel.get('targets')]
    require(len(panels) == 5 and [panel.get('id') for panel in panels] == list(range(2, 7)), 'PANEL_SET_DIFFERS')
    require(all(panel.get('datasource') == DASH.DATASOURCE and len(panel['targets']) == 1 for panel in panels), 'DATASOURCE_SCOPE_DIFFERS')
    return {key: copy.deepcopy(panel['targets'][0]) for key, panel in zip(BOUNDS, panels)}


def load_inputs():
    path = HERE / 'dashboards/agenthouse.json'
    raw = path.read_bytes()
    value = json.loads(raw)
    expected = panel_targets(DASH.dashboard())
    targets = panel_targets(value)
    require(targets == expected, 'CHECKED_IN_PANEL_TARGETS_DIFFER')
    require(all(target['rawSql'] == DASH.QUERIES[key] and target['pluginVersion'] == MANAGE.PINS['plugin']['version']
                for key, target in targets.items()), 'PINNED_QUERY_INPUTS_DIFFER')
    return targets, {'dashboard_sha256': digest(raw), 'source_sha256': digest((HERE / 'dashboard.py').read_bytes()),
                     'plugin_version': MANAGE.PINS['plugin']['version'], 'targets_sha256': digest(targets)}


def interpolate(sql, task, trace):
    require(all(type(value) is str and len(value) <= 256 for value in (task, trace)), 'FILTER_INPUT_INVALID')
    # Like Grafana's percentencode formatter, preserve no SQL delimiters.
    for key, value in [('task', task), ('trace', trace)]:
        sql = sql.replace('${' + key + ':percentencode}', urllib.parse.quote(value, safe=''))
    require('${' not in sql, 'UNRESOLVED_TEMPLATE')
    return sql


def reference_sql(raw_sql, task, trace, start, end):
    require(type(start) is int and type(end) is int and 0 < end - start <= 7 * 86400000, 'TIME_RANGE_INVALID')
    sql = interpolate(raw_sql, task, trace)
    sql = sql.replace('$__timeFilter_ms(timestamp)', f'timestamp >= fromUnixTimestamp64Milli({start}) AND timestamp <= fromUnixTimestamp64Milli({end})')
    sql = sql.replace('$__timeInterval_ms(timestamp)', f'toStartOfInterval(toDateTime64(timestamp, 3), INTERVAL {INTERVAL_MS} millisecond)')
    require('$__' not in sql and sql.startswith('SELECT ') and 'FROM agenthouse.pawprints FINAL' in sql
            and ';' not in sql, 'REFERENCE_QUERY_SCOPE_DIFFERS')
    return sql + '\nFORMAT JSON'


def kind(data_type):
    nullable = data_type.startswith('Nullable(')
    value = data_type[9:-1] if nullable else data_type
    if value.startswith('LowCardinality('):
        value = value[15:-1]
    if value in ('String', 'UUID'):
        return ('string', nullable)
    if value == 'Bool':
        return ('boolean', nullable)
    if re.fullmatch(r'(?:U?Int)(?:8|16|32|64)', value):
        return ('integer', nullable)
    if value == 'Float64':
        return ('float', nullable)
    if re.fullmatch(r"DateTime64\(3(?:,\s*'UTC')?\)", value):
        return ('time', nullable)
    raise VerificationError('UNEXPECTED_REFERENCE_TYPE')


def cell(value, expected_kind, nullable, *, reference):
    if value is None:
        require(nullable, 'UNEXPECTED_NULL_VALUE')
        return None
    if expected_kind == 'string':
        require(type(value) is str, 'STRING_TYPE_DIFFERS')
        return value
    if expected_kind == 'boolean':
        require(type(value) is bool, 'BOOLEAN_TYPE_DIFFERS')
        return value
    if expected_kind == 'time':
        if reference:
            require(type(value) is str, 'REFERENCE_TIMESTAMP_TYPE_DIFFERS')
            parsed = datetime.fromisoformat(value.replace(' ', 'T').replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            elapsed = parsed - datetime(1970, 1, 1, tzinfo=timezone.utc)
            require(elapsed.microseconds % 1000 == 0, 'TIMESTAMP_PRECISION_DIFFERS')
            return (elapsed.days * 86400 + elapsed.seconds) * 1000 + elapsed.microseconds // 1000
        require(type(value) in (int, float) and math.isfinite(value) and int(value) == value, 'FRAME_TIMESTAMP_TYPE_DIFFERS')
        return int(value)
    if reference and type(value) is str and re.fullmatch(r'-?[0-9]+', value) and expected_kind == 'integer':
        value = int(value)  # ClickHouse JSON quotes UInt64/Int64 by default.
    require(type(value) in (int, float) and math.isfinite(value), 'NUMBER_TYPE_DIFFERS')
    if expected_kind == 'integer':
        require(int(value) == value and abs(value) <= 2**53 - 1, 'INTEGER_PRECISION_DIFFERS')
        return int(value)
    return float(value)


def reference_table(value, maximum):
    require(type(value) is dict and type(value.get('meta')) is list and type(value.get('data')) is list, 'REFERENCE_RESPONSE_INVALID')
    columns = [entry['name'] for entry in value['meta']]
    types = [kind(entry['type']) for entry in value['meta']]
    require(len(set(columns)) == len(columns) and columns and len(value['data']) <= maximum, 'REFERENCE_SHAPE_INVALID')
    require(type(value.get('rows')) is int and value['rows'] == len(value['data']), 'REFERENCE_ROW_COUNT_DIFFERS')
    rows = []
    for row in value['data']:
        require(type(row) is dict and set(row) == set(columns), 'REFERENCE_COLUMNS_DIFFER')
        rows.append([cell(row[name], *typ, reference=True) for name, typ in zip(columns, types)])
    return {'columns': columns, 'types': [list(typ) for typ in types], 'rows': rows}


def frame_table(value, reference, maximum):
    require(type(value) is dict and set(value) == {'results'} and set(value['results']) == {'A'}, 'GRAFANA_RESULT_SET_DIFFERS')
    result = value['results']['A']
    require(result.get('status') == 200 and not result.get('error'), 'GRAFANA_QUERY_FAILED')
    frames = result.get('frames')
    require(type(frames) is list, 'GRAFANA_FRAMES_MISSING')
    if not frames:
        require(not reference['rows'], 'GRAFANA_DROPPED_ROWS')
        return {**reference, 'rows': []}
    require(len(frames) == 1, 'GRAFANA_FRAME_COUNT_DIFFERS')
    fields = frames[0]['schema']['fields']
    values = frames[0]['data']['values']
    # The pinned plugin emits a frame with no fields for an empty table result.
    # This proves only empty data; retain the independent reference schema.
    if fields == [] and values == []:
        require(not reference['rows'], 'GRAFANA_DROPPED_ROWS')
        return {**reference, 'rows': []}
    require(type(fields) is list and [field.get('name') for field in fields] == reference['columns'], 'GRAFANA_COLUMNS_DIFFER')
    for field, (expected_kind, _nullable) in zip(fields, reference['types']):
        require(field.get('type') == ('number' if expected_kind in ('integer', 'float') else expected_kind), 'GRAFANA_FIELD_TYPE_DIFFERS')
    require(type(values) is list and len(values) == len(fields) and all(type(column) is list for column in values), 'GRAFANA_VALUES_INVALID')
    count = len(values[0])
    require(count <= maximum and all(len(column) == count for column in values), 'GRAFANA_COLUMN_LENGTH_DIFFERS')
    rows = [[cell(value, *typ, reference=False) for value, typ in zip(row, reference['types'])] for row in zip(*values)]
    return {**reference, 'rows': rows}


def comparable(value, panel):
    result = copy.deepcopy(value)
    # phase_latency's ORDER BY does not specify the provider/model tie order.
    if panel == 'phase_latency':
        result['rows'].sort(key=lambda row: json.dumps(row, ensure_ascii=False))
    return result


def usage_invariants(table):
    for values in table['rows']:
        row = dict(zip(table['columns'], values))
        total = row['inference_events']
        require(total > 0, 'MODEL_USAGE_EMPTY_GROUP')
        for prefix, subtotal in [('prompt', 'known_prompt_tokens'), ('completion', 'known_completion_tokens'), ('cost', 'known_cost_estimate')]:
            known, unknown = row[prefix + '_known_calls'], row[prefix + '_unknown_calls']
            require(known >= 0 and unknown >= 0 and known + unknown == total, 'MODEL_USAGE_KNOWN_UNKNOWN_DIFFERS')
            require((known == 0 and row[subtotal] is None) or (known > 0 and type(row[subtotal]) in (int, float) and row[subtotal] >= 0), 'MODEL_USAGE_NULL_WAS_LOST')


def run(output, *, reader=None, grafana=None, now_ms=None):
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    report = {'schema_version': 1, 'result': 'running', 'started_at': datetime.now(timezone.utc).isoformat(),
              'cases': [], 'pending': None, 'anonymous_grafana_queries': True, 'database_mutations': 0}
    save = lambda: MANAGE.private_write(output / 'summary.json', report)
    deadline = time.monotonic() + 240
    try:
        save()
        targets, inputs = load_inputs()
        report['inputs'] = inputs
        if reader is None:
            MANAGE.check_context()
            require(MANAGE.status()['ready'] is True, 'GRAFANA_NOT_READY')
            credential = MANAGE.credentials()
            reader = lambda sql: json.loads(MANAGE.reader_query(credential, sql))
        if grafana is None:
            # Explicitly omit credentials: test the same anonymous Viewer path.
            grafana = lambda path, body=None: json.loads(MANAGE.request(MANAGE.URL + path, data=None if body is None else json.dumps(body).encode()))
        live = grafana('/api/dashboards/uid/' + DASH.UID)
        require(panel_targets(live['dashboard']) == targets and live.get('meta', {}).get('provisioned') is True, 'PROVISIONED_PANEL_TARGETS_DIFFER')
        end = int(time.time() * 1000) - 2000 if now_ms is None else now_ms
        start = end - 7 * 86400000
        report['time_range'] = {'from_ms': start, 'to_ms': end, 'interval_ms': INTERVAL_MS}
        choose = ('SELECT task_id, trace_id FROM agenthouse.pawprints FINAL '
                  f'WHERE timestamp >= fromUnixTimestamp64Milli({start}) AND timestamp <= fromUnixTimestamp64Milli({end}) '
                  "AND model_provider != 'none' AND task_id != '' AND trace_id != '' ORDER BY timestamp DESC, event_id LIMIT 1 FORMAT JSON")
        selected = reference_table(reader(choose), 1)
        require(selected['columns'] == ['task_id', 'trace_id'] and len(selected['rows']) == 1, 'NO_KNOWN_TASK_TRACE')
        task, trace = selected['rows'][0]
        require(re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', task) and re.fullmatch('[0-9a-f]{32}', trace), 'KNOWN_IDENTITY_SHAPE_DIFFERS')
        report['known_identity'] = {'task': task, 'trace': trace}
        missing = 'shirokuma-dashboard-missing-' + uuid.uuid4().hex
        cases = [('all', '', '', False), ('task', task, '', False), ('trace', '', trace, False), ('task_trace', task, trace, False),
                 ('missing_task', missing, '', True), ('missing_trace', '', missing, True),
                 ('malicious_task', "' OR 1=1 --", '', True), ('malicious_trace', '', "x'); SELECT 1; --%\\", True)]
        for case, task_filter, trace_filter, empty in cases:
            for panel, target in targets.items():
                require(time.monotonic() < deadline, 'VERIFICATION_DEADLINE_EXHAUSTED')
                report['pending'] = {'case': case, 'panel': panel}
                save()
                sql = reference_sql(target['rawSql'], task_filter, trace_filter, start, end)
                expected = reference_table(reader(sql), BOUNDS[panel])
                submitted = {**target, 'rawSql': interpolate(target['rawSql'], task_filter, trace_filter),
                             'intervalMs': INTERVAL_MS, 'maxDataPoints': 1000}
                reply = grafana('/api/ds/query', {'from': str(start), 'to': str(end), 'queries': [submitted]})
                actual = frame_table(reply, expected, BOUNDS[panel])
                schema_omitted = not reply['results']['A']['frames'] or not reply['results']['A']['frames'][0]['schema']['fields']
                confirmed = reference_table(reader(sql), BOUNDS[panel])
                artifact = {'case': case, 'panel': panel, 'reference': expected, 'grafana': actual, 'reference_after': confirmed,
                            'grafana_empty_schema_omitted': schema_omitted}
                MANAGE.private_write(output / (case + '-' + panel + '.json'), artifact)
                require(comparable(expected, panel) == comparable(confirmed, panel), 'REFERENCE_CHANGED_DURING_QUERY')
                require(comparable(actual, panel) == comparable(expected, panel), 'GRAFANA_FRAME_DATA_DIFFERS')
                if empty:
                    require(not actual['rows'], 'UNMATCHED_FILTER_RETURNED_ROWS')
                elif panel in ('timeline', 'events_over_time', 'phase_latency', 'model_usage'):
                    require(bool(actual['rows']), 'KNOWN_FILTER_LOST_ROWS')
                if panel == 'model_usage':
                    usage_invariants(actual)
                report['cases'].append({'case': case, 'panel': panel, 'result': 'passed', 'rows': len(actual['rows']),
                    'columns': actual['columns'], 'data_sha256': digest(comparable(actual, panel)), 'query_sha256': digest(sql.encode()),
                    'grafana_empty_schema_omitted': schema_omitted,
                    'null_values': sum(value is None for row in actual['rows'] for value in row)})
                report['pending'] = None
                save()
        require(load_inputs()[1] == inputs, 'LOCAL_INPUTS_CHANGED_DURING_VERIFICATION')
        require(panel_targets(grafana('/api/dashboards/uid/' + DASH.UID)['dashboard']) == targets, 'LIVE_INPUTS_CHANGED_DURING_VERIFICATION')
        report.update(result='passed', passed_panel_cases=len(report['cases']))
    except BaseException as error:
        report.update(result='interrupted' if not isinstance(error, Exception) else 'failed',
                      error_code=str(error) if isinstance(error, VerificationError) else 'DASHBOARD_QUERY_OR_EVIDENCE_FAILED')
        if not isinstance(error, Exception):
            report['finished_at'] = datetime.now(timezone.utc).isoformat()
            save()
            raise
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    save()
    return report


def main():
    token = uuid.uuid4().hex
    output = ROOT / 'tmp/local-experiment/grafana-verification' / token
    try:
        require(len(sys.argv) == 1, 'NO_EXTERNAL_QUERY_ARGUMENTS_ALLOWED')
        with SESSION.interruption_handlers():
            report = run(output)
    except SESSION.SessionInterrupted as error:
        print(json.dumps({'result': 'interrupted', 'run_token': token, 'signal': error.signum}))
        return 128 + error.signum
    except Exception:
        print(json.dumps({'result': 'failed', 'run_token': token, 'error_code': 'DASHBOARD_VERIFIER_SETUP_FAILED'}))
        return 1
    print(json.dumps({'result': report['result'], 'run_token': token, 'passed_panel_cases': len(report['cases']),
                      'error_code': report.get('error_code')}))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
