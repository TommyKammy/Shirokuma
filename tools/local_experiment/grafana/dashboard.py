#!/usr/bin/env python3
"""Build the provisioned read-only AgentHouse operator dashboard."""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATASOURCE = {'type': 'grafana-clickhouse-datasource', 'uid': 'shirokuma-agenthouse-readonly'}
UID = 'shirokuma-agenthouse-local'
# Status text needs sufficient contrast on the provisioned light theme.
STATUS_COLORS = {'success': '#1B6B3A', 'failed': '#B42318', 'blocked': '#9A4D00'}
FILTER = """$__timeFilter_ms(timestamp)
AND (decodeURLComponent('${task:percentencode}') = '' OR task_id = decodeURLComponent('${task:percentencode}'))
AND (decodeURLComponent('${trace:percentencode}') = '' OR trace_id = decodeURLComponent('${trace:percentencode}'))"""

QUERIES = {
    'attention': f"""SELECT timestamp, status, action_type, task_id, duration_ms, trace_id
FROM agenthouse.pawprints FINAL
WHERE {FILTER} AND status IN ('failed', 'blocked')
ORDER BY timestamp DESC, event_id LIMIT 100""",
    'events_over_time': f"""SELECT $__timeInterval_ms(timestamp) AS time,
countIf(status = 'success') AS success,
countIf(status = 'failed') AS failed,
countIf(status = 'blocked') AS blocked
FROM agenthouse.pawprints FINAL
WHERE {FILTER}
GROUP BY time ORDER BY time LIMIT 1000""",
    'phase_latency': f"""SELECT action_type, model_provider, model_name,
count() AS recorded_events,
countIf(status = 'failed') AS failed_events,
countIf(status = 'blocked') AS blocked_events,
quantileExact(0.50)(duration_ms) AS p50_ms,
quantileExact(0.95)(duration_ms) AS p95_ms
FROM agenthouse.pawprints FINAL
WHERE {FILTER}
GROUP BY action_type, model_provider, model_name
ORDER BY p95_ms DESC, action_type LIMIT 100""",
    'model_usage': f"""SELECT model_provider, model_name, count() AS inference_events,
countIf(isNotNull(prompt_tokens)) AS prompt_known_calls,
countIf(isNull(prompt_tokens)) AS prompt_unknown_calls,
if(countIf(isNotNull(prompt_tokens)) = 0, NULL, sum(prompt_tokens)) AS known_prompt_tokens,
countIf(isNotNull(completion_tokens)) AS completion_known_calls,
countIf(isNull(completion_tokens)) AS completion_unknown_calls,
if(countIf(isNotNull(completion_tokens)) = 0, NULL, sum(completion_tokens)) AS known_completion_tokens,
countIf(isNotNull(inference_cost_estimate)) AS cost_known_calls,
countIf(isNull(inference_cost_estimate)) AS cost_unknown_calls,
if(countIf(isNotNull(inference_cost_estimate)) = 0, NULL, sum(inference_cost_estimate)) AS known_cost_estimate
FROM agenthouse.pawprints FINAL
WHERE {FILTER} AND model_provider != 'none'
GROUP BY model_provider, model_name
ORDER BY inference_events DESC, model_provider, model_name LIMIT 50""",
    'timeline': f"""SELECT timestamp, status, action_type, task_id, trace_id, target_system,
blocked_by_policy, model_provider, model_name, duration_ms,
prompt_tokens, completion_tokens, inference_cost_estimate, event_id
FROM agenthouse.pawprints FINAL
WHERE {FILTER}
ORDER BY timestamp DESC, event_id LIMIT 250""",
}


def field(name, *properties):
    return {'matcher': {'id': 'byName', 'options': name}, 'properties': list(properties)}


def prop(name, value):
    return {'id': name, 'value': value}


def dashboard():
    common = [
        field('timestamp', prop('displayName', '記録時刻'), prop('unit', 'dateTimeAsIso'), prop('custom.width', 165)),
        field('duration_ms', prop('displayName', '処理時間'), prop('unit', 'ms')),
        field('p50_ms', prop('displayName', 'p50'), prop('unit', 'ms')),
        field('p95_ms', prop('displayName', 'p95'), prop('unit', 'ms')),
        field('known_cost_estimate', prop('decimals', 6)),
        field('inference_cost_estimate', prop('decimals', 6)),
        field('task_id', prop('displayName', 'Task'), prop('custom.width', 250)),
        field('trace_id', prop('displayName', 'Trace'), prop('custom.width', 230)),
        field('action_type', prop('displayName', '処理'), prop('custom.width', 230)),
        field('target_system', prop('displayName', '対象')),
        field('model_provider', prop('displayName', 'Provider')),
        field('model_name', prop('displayName', 'モデル')),
        field('recorded_events', prop('displayName', '記録数')),
        field('failed_events', prop('displayName', '失敗数')),
        field('blocked_events', prop('displayName', 'ブロック数')),
        field('inference_events', prop('displayName', '推論記録数')),
        field('prompt_known_calls', prop('displayName', '入力・既知calls')),
        field('prompt_unknown_calls', prop('displayName', '入力・欠測calls')),
        field('completion_known_calls', prop('displayName', '出力・既知calls')),
        field('completion_unknown_calls', prop('displayName', '出力・欠測calls')),
        field('known_prompt_tokens', prop('displayName', '入力tokens・既知小計')),
        field('known_completion_tokens', prop('displayName', '出力tokens・既知小計')),
        field('cost_known_calls', prop('displayName', '費用・既知calls')),
        field('cost_unknown_calls', prop('displayName', '費用・欠測calls')),
        field('known_cost_estimate', prop('displayName', '費用・既知小計')),
        field('prompt_tokens', prop('displayName', '入力tokens')),
        field('completion_tokens', prop('displayName', '出力tokens')),
        field('inference_cost_estimate', prop('displayName', '費用観測値')),
        field('event_id', prop('displayName', 'イベントID')),
        field('status', prop('displayName', '結果'), prop('custom.width', 100), prop('mappings', [
            {'type': 'value', 'options': {
                'success': {'text': '成功', 'color': STATUS_COLORS['success']},
                'failed': {'text': '失敗', 'color': STATUS_COLORS['failed']},
                'blocked': {'text': 'ブロック', 'color': STATUS_COLORS['blocked']},
            }}]), prop('custom.cellOptions', {'type': 'color-text'})),
    ]
    panels = [{
        'id': 1, 'type': 'text', 'title': 'ローカル実行の記録',
        'gridPos': {'x': 0, 'y': 0, 'w': 24, 'h': 4},
        'options': {'mode': 'markdown', 'content':
            'Task・Traceを入力すると、その実行に絞り込めます。空欄はすべての記録です。\n\n'
            '成功・失敗は各監査イベントの結果です。Task全体の完了判定やPR成功率ではありません。'
            '推論の未観測tokens・費用は「未観測」のまま表示します。'},
    }]
    specs = [
        ('attention', '失敗・ブロックされた記録', 'table', 0, 4, 24, 8,
         '新しい記録から最大100件。記録がない場合は対象期間とTask・Traceの絞り込みを確認してください。'),
        ('events_over_time', '記録数の推移', 'timeseries', 0, 12, 24, 7,
         'イベント数です。監査経路外の失敗や未記録の実行は含まれません。'),
        ('phase_latency', '処理別の時間', 'table', 0, 19, 24, 8,
         '処理・モデルごとの観測時間。親処理と子処理は重なるため、合計して実行時間にはしません。'),
        ('model_usage', 'モデル別の使用量と欠測', 'table', 0, 27, 24, 8,
         '既知tokensの小計と既知・未知callsを併記。mockは別行です。費用は測定値のみで通貨換算しません。'),
        ('timeline', 'Task・Traceのイベント', 'table', 0, 35, 24, 11,
         '最大250件。列から時刻・処理・モデル・イベントIDを確認できます。絞り込みは上部のTask・Traceを使用します。'),
    ]
    for index, (key, title, kind, x, y, w, h, description) in enumerate(specs, 2):
        overrides = list(common) if kind == 'table' else [
            field('success', prop('displayName', '成功'), prop('color', {'mode': 'fixed', 'fixedColor': STATUS_COLORS['success']})),
            field('failed', prop('displayName', '失敗'), prop('color', {'mode': 'fixed', 'fixedColor': STATUS_COLORS['failed']})),
            field('blocked', prop('displayName', 'ブロック'), prop('color', {'mode': 'fixed', 'fixedColor': STATUS_COLORS['blocked']})),
        ]
        panel = {'id': index, 'type': kind, 'title': title, 'description': description,
            'gridPos': {'x': x, 'y': y, 'w': w, 'h': h}, 'datasource': dict(DATASOURCE),
            'targets': [{'refId': 'A', 'datasource': dict(DATASOURCE), 'editorType': 'sql',
                'queryType': 'timeseries' if kind == 'timeseries' else 'table',
                'format': 0 if kind == 'timeseries' else 1, 'rawSql': QUERIES[key],
                'pluginVersion': '4.21.3'}],
            'fieldConfig': {'defaults': {'noValue': '未観測', 'decimals': 0}, 'overrides': overrides},
            'options': {'showHeader': True, 'cellHeight': 'sm', 'footer': {'show': False}}
                if kind == 'table' else {'legend': {'displayMode': 'list', 'placement': 'bottom'},
                                       'tooltip': {'mode': 'multi', 'sort': 'none'}}}
        if kind == 'timeseries':
            panel['fieldConfig']['defaults']['custom'] = {
                'drawStyle': 'bars', 'barAlignment': 0, 'lineWidth': 1, 'fillOpacity': 70,
                'spanNulls': False, 'stacking': {'mode': 'normal', 'group': 'A'},
                'axisLabel': '監査イベント', 'axisBorderShow': False}
        panels.append(panel)
    return {'uid': UID, 'title': 'Shirokuma · AgentHouse', 'description': 'ローカルPawprintの読み取り専用ビュー',
        'tags': ['shirokuma-local', 'agenthouse'], 'timezone': 'browser', 'schemaVersion': 39,
        'version': 1, 'editable': False, 'graphTooltip': 1, 'refresh': '',
        'time': {'from': 'now-7d', 'to': 'now'}, 'links': [], 'annotations': {'list': []},
        'templating': {'list': [
            {'name': name, 'label': label, 'type': 'textbox', 'hide': 0, 'query': '',
             'current': {'text': '', 'value': ''}, 'options': []}
            for name, label in [('task', 'Task'), ('trace', 'Trace')]]}, 'panels': panels}


if __name__ == '__main__':
    output = HERE / 'dashboards/agenthouse.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dashboard(), ensure_ascii=False, indent=2) + '\n')
    print(output.relative_to(HERE))
