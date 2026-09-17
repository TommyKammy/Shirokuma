#!/usr/bin/env python3
"""Generate the staged, read-only retained-history dashboard (no runtime calls).

The output deliberately stays outside the bind-mounted dashboards directory.
Native table options follow Grafana 13.2.2's table panel and field configuration.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / 'history_dashboard.json'
UID = 'shirokuma-query-history-local'
DATASOURCE = {'type': 'grafana-clickhouse-datasource', 'uid': 'shirokuma-agenthouse-readonly'}
PLUGIN_VERSION = '4.21.3'
REQUIRED_TABLES = ('benchmark_runs', 'benchmark_queries', 'query_history_runs', 'query_history_queries')
LIMITS = {'benchmark_runs': 128, 'benchmark_queries': 1000, 'query_runs': 128, 'workload_history': 512, 'auxiliary_reads': 384}
QUERIES = {
    'benchmark_runs': '''SELECT result, provenance_status, recorded_pairs, comparison_unknown_pairs,
expected_pairs, wall_seconds, started_at, run_token
FROM agenthouse.benchmark_runs FINAL
ORDER BY started_at DESC NULLS LAST, run_token LIMIT 128''',
    'benchmark_queries': '''SELECT query_number, side, comparison_status, wall_seconds,
engine_elapsed_ms, cpu_ms, peak_memory_bytes, provenance_status, run_token
FROM agenthouse.benchmark_queries FINAL
ORDER BY run_token, query_number, side LIMIT 1000''',
    'query_runs': '''SELECT workflow_result, observer_outcome, observed_history_count,
history_missing_count, history_unobserved_count, auxiliary_query_count,
uncaptured_count, query_run_token
FROM agenthouse.query_history_runs FINAL
ORDER BY query_run_token LIMIT 128''',
    'workload_history': '''SELECT case_id, kind, response_state, history_status,
created_to_end_ms, started_to_end_ms, query_id
FROM agenthouse.query_history_queries FINAL
WHERE kind IN ('select', 'explain')
ORDER BY query_run_token, request_index LIMIT 512''',
    'auxiliary_reads': '''SELECT kind, response_state, response_complete, history_status,
created_to_end_ms, query_id
FROM agenthouse.query_history_queries FINAL
WHERE kind IN ('own_query_history', 'fixture_stats')
ORDER BY query_run_token, request_index LIMIT 384''',
}
COLORS = {'success': '#087443', 'failure': '#b91c1c', 'unknown': '#855a00', 'neutral': '#475569'}


def field(name, label, width=110, *, unit=None, decimals=None, mapping=None):
    values = [('displayName', label), ('custom.width', width)]
    if unit:
        values.append(('unit', unit))
    if decimals is not None:
        values.append(('decimals', decimals))
    if mapping:
        values.extend([('mappings', [{'type': 'value', 'options': {
            key: {'text': text, 'color': COLORS[color]} for key, (text, color) in mapping.items()}}]),
            ('custom.cellOptions', {'type': 'color-text'})])
    return {'matcher': {'id': 'byName', 'options': name}, 'properties': [{'id': key, 'value': value} for key, value in values]}


def dashboard():
    outcomes = {'passed': ('成功', 'success'), 'completed': ('完了', 'success'), 'failed': ('失敗', 'failure'),
                'interrupted': ('中断', 'failure'), 'incomplete': ('未完了', 'unknown'), 'running': ('実行中', 'unknown'),
                'unobserved': ('未観測', 'unknown')}
    overrides = [
        field('result', '実験結果', 100, mapping=outcomes),
        field('workflow_result', '診断の結果', 110, mapping=outcomes),
        field('observer_outcome', '読取の終了', 110, mapping=outcomes),
        field('provenance_status', '来歴', 130, mapping={'recorded': ('記録あり', 'neutral'), 'unknown': ('不明・旧証跡', 'unknown')}),
        field('comparison_status', '結果の照合', 120, mapping={'matched': ('一致確認', 'success'), 'unobserved': ('照合未観測', 'unknown')}),
        field('recorded_pairs', '比較記録数', 120), field('comparison_unknown_pairs', '照合未観測', 120),
        field('expected_pairs', '予定クエリ数', 130), field('wall_seconds', '実測・壁時計', 150, unit='s', decimals=3),
        field('started_at', '開始時刻', 200, unit='dateTimeAsIso'),
        field('run_token', '実験ID', 300), field('query_run_token', '診断実行ID', 300),
        field('query_number', 'クエリ番号', 110),
        field('side', '実行先', 100, mapping={'reference': ('生成データ', 'neutral'), 'iceberg': ('Iceberg', 'neutral')}),
        field('engine_elapsed_ms', 'Trino経過時間', 150, unit='ms', decimals=3),
        field('cpu_ms', 'CPU時間', 130, unit='ms', decimals=3),
        field('peak_memory_bytes', '最大メモリ', 140, unit='bytes', decimals=2),
        field('observed_history_count', '履歴あり', 100), field('history_missing_count', '履歴に不在', 120),
        field('history_unobserved_count', '履歴未観測', 120), field('auxiliary_query_count', '補助読取', 100),
        field('uncaptured_count', 'ID未捕捉', 110),
        field('case_id', '対象SELECT', 140, mapping={'projection': ('ID列の読取', 'neutral'), 'filtered': ('id=1の読取', 'neutral')}),
        field('kind', '種類', 160, mapping={'select': ('SELECT', 'neutral'), 'explain': ('EXPLAIN', 'neutral'),
            'own_query_history': ('自身の履歴読取', 'neutral'), 'fixture_stats': ('統計の読取', 'neutral')}),
        field('response_state', 'Trino状態', 130, mapping={'FINISHED': ('完了', 'success'), 'FAILED': ('失敗', 'failure'),
            'RUNNING': ('実行中の観測', 'unknown'), 'QUEUED': ('待機中の観測', 'unknown')}),
        field('response_complete', '応答の受取', 130, mapping={'true': ('受取完了', 'neutral'), 'false': ('未完了', 'unknown')}),
        field('history_status', '履歴の観測', 150, mapping={'observed': ('観測済み', 'neutral'), 'missing': ('履歴に不在', 'unknown'),
            'unobserved': ('未観測', 'unknown'), 'not_target': ('測定対象外', 'neutral')}),
        field('created_to_end_ms', '作成→終了', 150, unit='ms', decimals=3),
        field('started_to_end_ms', '開始→終了', 150, unit='ms', decimals=3),
        field('query_id', 'Trino query ID', 320),
    ]
    panels = [{
        'id': 1, 'type': 'text', 'title': '保存されたクエリ実験',
        'gridPos': {'x': 0, 'y': 0, 'w': 24, 'h': 7},
        'options': {'mode': 'markdown', 'content':
            '保存済みの全期間を表示します。列のフィルターは、取得した行だけを絞り込みます。'
            '行数上限と対象範囲は各表の説明を確認してください。\n\n'
            '**ローカル実験の記録です。TPC-H の公式性能値や、実行間の性能改善を示しません。** '
            '「未観測」と実測の 0 は区別します。旧証跡の来歴不明もそのまま表示します。\n\n'
            'Query Doctor は固定2行データの読取診断です。自身が捕捉した ID の保存証跡であり、'
            'サーバー監査・クラスタ全体の履歴ではありません。Trino Gateway は未導入です。'},
    }]
    specs = [
        ('benchmark_runs', 'Benchmark · 実行と失敗', 5, 8,
         '失敗を含む保存済み実行。最大128件、開始時刻の降順（時刻未観測は末尾）。比較記録数は完了・成功を意味しません。予定数との差と照合未観測を確認してください。'),
        ('benchmark_queries', 'Benchmark · クエリごとの観測', 13, 11,
         '実験ID・クエリ番号順に最大1000件。生成データとIcebergは別行です。来歴や環境が異なる実行を合算・平均しません。各時間は同じ指標ではありません。'),
        ('query_runs', 'Query Doctor · 履歴の取得範囲', 24, 8,
         '最大128実行。SELECT・EXPLAINの履歴あり／不在／未観測を分け、補助読取とID未捕捉を別に数えます。対象外・未捕捉を成功や0件の履歴に置き換えません。'),
        ('workload_history', 'Query Doctor · SELECT と EXPLAIN', 32, 9,
         '診断実行順に最大512件。最新の保存履歴を表示します。作成→終了と開始→終了は待ち時間の含み方が異なり、合計しません。NULLは未観測です。'),
        ('auxiliary_reads', 'Query Doctor · 補助読取', 41, 8,
         '自身の履歴・統計を読む補助リクエスト。最大384件。Trino応答は保存していますが履歴時間の測定対象外なので、時間はNULLのままです。性能集計に混ぜません。'),
    ]
    for index, (key, title, y, height, description) in enumerate(specs, 2):
        columns = {column.strip() for column in QUERIES[key].split('FROM', 1)[0][len('SELECT '):].split(',')}
        panels.append({'id': index, 'type': 'table', 'title': title, 'description': description,
            'gridPos': {'x': 0, 'y': y + 2, 'w': 24, 'h': height}, 'datasource': dict(DATASOURCE),
            'targets': [{'refId': 'A', 'datasource': dict(DATASOURCE), 'editorType': 'sql', 'queryType': 'table',
                         'format': 1, 'rawSql': QUERIES[key], 'pluginVersion': PLUGIN_VERSION}],
            'fieldConfig': {'defaults': {'noValue': '未観測', 'decimals': 0,
                            'custom': {'filterable': True, 'minWidth': 80, 'inspect': True}},
                            'overrides': [override for override in overrides if override['matcher']['options'] in columns]},
            'options': {'showHeader': True, 'cellHeight': 'sm', 'enablePagination': True,
                        'frozenColumns': {'left': 1}, 'footer': {'show': False}}})
    return {'uid': UID, 'title': 'Shirokuma · クエリ実験の履歴', 'description': '保持したローカル実験と自身のクエリ履歴の読み取り専用ビュー',
        'tags': ['shirokuma-local', 'retained-history'], 'timezone': 'browser', 'schemaVersion': 39, 'version': 1,
        'editable': False, 'refresh': '', 'time': {'from': 'now-30d', 'to': 'now'}, 'timepicker': {'hidden': True},
        'templating': {'list': []}, 'annotations': {'list': []},
        'links': [{'title': 'AgentHouse のイベント', 'type': 'link', 'url': '/d/shirokuma-agenthouse-local?theme=light',
                   'targetBlank': False}], 'panels': panels}


if __name__ == '__main__':
    OUTPUT.write_text(json.dumps(dashboard(), ensure_ascii=False, indent=2) + '\n')
    print(OUTPUT.name)
