"""Self-contained Japanese HTML for a bounded historical-lineage observation."""
from __future__ import annotations

from html import escape
from datetime import datetime
import math
import re

STATUSES = ('present', 'missing', 'conflict', 'unknown')
LABELS = {'present': '登録を確認', 'missing': '欠落', 'conflict': '競合', 'unknown': '不明'}
SYMBOLS = {'present': '✓', 'missing': '−', 'conflict': '!', 'unknown': '?'}
OPERATIONS = {'uppercase_names': '名前の大文字化', 'name_lengths': '名前の文字数', 'row_count': '行数の集計'}
SOURCE_LABELS = {'valid': '有効', 'failed': '実行失敗', 'incomplete': '未完了', 'invalid': '無効'}
# Known collector reason codes must not contradict the displayed edge status.
# Unknown codes remain printable for forwards-compatible offline reports.
EDGE_REASON_STATUS = {'VERIFIED_PRESENT': 'present', 'VERIFIED_ABSENT': 'missing',
                      'CATALOG_CONFLICT': 'conflict',
                      **dict.fromkeys(('CATALOG_RESPONSE_INVALID', 'CATALOG_UNAVAILABLE',
                                       'CATALOG_CHANGED', 'NOT_OBSERVED', 'SOURCE_CHANGED',
                                       'SOURCE_INVENTORY_CHANGED'), 'unknown')}
REASONS = {
    'VERIFIED_PRESENT': '期待する登録と関係を確認しました',
    'VERIFIED_ABSENT': '期待する登録が見つかりません',
    'CATALOG_CONFLICT': '登録内容が期待する所有者・schema・関係と一致しません',
    'CATALOG_UNAVAILABLE': 'metadataの読み取りを完了できませんでした',
    'CATALOG_CHANGED': '前後のmetadata観測が一致しません',
    'SOURCE_VERIFIED': '保存済みの実験証跡を検証しました',
    'SOURCE_INVALID': '保存証跡の検証に失敗しました',
    'SOURCE_CHANGED': '観測中に保存証跡の変化を検出しました',
    'SOURCE_CHANGED_OR_UNVERIFIABLE': '保存証跡の前後一致を確認できません',
    'SOURCE_UNAVAILABLE': '保存証跡を読み取れませんでした',
    'SUMMARY_MISSING': '実行結果のsummaryがありません',
    'SOURCE_IDENTITY_INVALID': '実験の識別子が一致しません',
    'RECORDED_RUN_FAILED': '実験の失敗が記録されています',
    'RUN_NOT_COMPLETE': '実験の完了を確認できません',
    'ARTIFACT_VERIFICATION_FAILED': '成果物の整合検証に失敗しました',
    'UNRECOGNIZED_SOURCE_ENTRIES': '実験として識別できない項目があります',
    'SOURCE_RUN_LIMIT': '一度に検証できる実験数の上限に達しました',
    'NOT_OBSERVED': 'このエッジはまだ観測していません',
    'SOURCE_INVENTORY_CHANGED': '観測中に保存証跡の一覧が変化しました',
    'SOURCE_INVENTORY_UNAVAILABLE': '保存証跡の一覧取得を完了できません',
    'UNVERIFIED_SOURCE_RUNS': '失敗・無効または未完了の証跡があります',
    'UNKNOWN_EXPECTED_EDGES': '状態を確認できない期待エッジがあります',
}
LIMITS_JA = {
    'Only verified historical Data Plan runs define the expected lineage domain; this is not whole-catalog coverage.':
        '期待するlineageの範囲は、検証済みData Planの実験履歴に限定されます。catalog全体は対象外です。',
    'Generated SQL tables were removed by verified cleanup. Historical catalog entities do not imply live tables.':
        'SQLの生成テーブルは検証済みcleanupで削除されています。履歴entityの登録は、現在のテーブルの存在を意味しません。',
    'Eligible runs are fixed at the first artifact verification. Later drift preserves their two expected edges as unknown.':
        '集計対象は最初の成果物検証で固定します。その後に証跡が変化したrunも、期待する2エッジを不明として分母に残します。',
    'A run-level catalog ownership, schema or lineage conflict conservatively marks both expected edges as conflict.':
        'runの所有者・schema・lineageに競合があれば、期待する2エッジをともに競合として扱います。',
    'Unknown observations are not missing. Coverage rate is unavailable while any expected edge is unknown.':
        '不明と欠落を区別します。期待するエッジに不明が1件でもあれば、登録率は算出しません。',
    'Two bounded observations detect observed changes, not changes that occur and revert between reads.':
        '前後2回の読み取りで確認できた変化を検出します。読み取りの間に変化して元へ戻った場合は検出できません。',
    'Artifacts establish local consistency, not cryptographic provenance against an actor replacing all evidence.':
        '成果物の照合はローカルの整合性を確認します。すべての証跡を置き換える改変に対して、暗号学的な来歴を保証しません。',
}

STYLE = '''
:root{color-scheme:light;--bg:#f7f9fc;--surface:#fff;--ink:#17233a;--muted:#52627a;
 --line:#dbe3ef;--accent:#2563eb;--present:#10634e;--present-bg:#e6f4ee;
 --missing:#8a4b00;--missing-bg:#fff3d9;--conflict:#9c2d34;--conflict-bg:#fff0f1;
 --unknown:#52627a;--unknown-bg:#edf1f6}
*{box-sizing:border-box}html{scroll-padding-top:1.5rem}body{margin:0;background:var(--bg);
 color:var(--ink);font:14px/1.65 -apple-system,BlinkMacSystemFont,"Hiragino Kaku Gothic ProN",
 "Noto Sans JP",Meiryo,system-ui,sans-serif}a{color:#1d4ed8;text-underline-offset:.2em}
a:hover{color:#173e9f}a:focus-visible,summary:focus-visible,[tabindex]:focus-visible{
 outline:3px solid var(--accent);outline-offset:4px}h1,h2,h3,p{margin:0}h1,h2,h3{text-wrap:balance}
h1{font-size:1.75rem;line-height:1.4;letter-spacing:-.015em}h2{font-size:1.2rem;line-height:1.5}
h3{font-size:1rem}p{max-width:75ch;text-wrap:pretty}.muted{color:var(--muted)}
.masthead{background:var(--surface);border-bottom:1px solid var(--line)}
.header-inner,main,footer{max-width:1180px;margin:auto;padding:1.5rem 2rem}
.brand{font-size:.8rem;font-weight:650;color:var(--muted);margin-bottom:.7rem}
.title-row{display:flex;align-items:flex-start;justify-content:space-between;gap:1rem;flex-wrap:wrap}
.subtitle{margin-top:.6rem;color:var(--muted)}.scope-label{display:inline-flex;align-items:center;
 gap:.4rem;font-size:.8rem;color:var(--muted);border:1px solid var(--line);border-radius:4px;padding:.25rem .6rem}
.meta{display:flex;gap:.6rem 2rem;flex-wrap:wrap;margin:1.2rem 0 0;color:var(--muted);font-size:.8rem}
.meta div{display:flex;gap:.5rem;flex-wrap:wrap}.meta dt{font-weight:650}.meta dd{margin:0}
nav{display:flex;gap:1.5rem;flex-wrap:wrap;margin-top:1rem;font-size:.85rem}
main{display:grid;gap:1.75rem;padding-top:1.75rem;padding-bottom:2.5rem}
section{min-width:0;scroll-margin-top:1.5rem}.section-heading{display:flex;align-items:baseline;
 justify-content:space-between;gap:1rem;flex-wrap:wrap;margin-bottom:.8rem}
.section-heading p{font-size:.82rem;color:var(--muted)}
.overview{padding:1.25rem 1.5rem;background:var(--surface);border:1px solid var(--line);border-radius:6px}
.coverage-line{display:flex;align-items:baseline;gap:.4rem .75rem;flex-wrap:wrap;margin-top:.8rem}
.coverage-line strong{font-size:1.45rem;font-variant-numeric:tabular-nums;letter-spacing:-.015em}
.coverage-line span{font-size:.86rem;color:var(--muted)}
.bar{display:flex;height:12px;overflow:hidden;background:var(--unknown-bg);border-radius:3px;margin:.85rem 0}
.segment{height:100%;min-width:0}.segment.present{background:var(--present)}
.segment.missing{background:#b37828}.segment.conflict{background:var(--conflict)}
.segment.unknown{background:#748297}.legend{display:flex;gap:.5rem 1.35rem;flex-wrap:wrap;
 list-style:none;margin:0;padding:0;font-size:.86rem}.legend li{display:flex;align-items:center;gap:.4rem}
.legend b{font-variant-numeric:tabular-nums;font-weight:650}.legend-symbol{font-weight:750}
.denominator{margin-top:1rem;padding-top:.85rem;border-top:1px solid var(--line);
 font-size:.83rem;color:var(--muted)}.denominator strong{color:var(--ink);font-weight:650}
.notice{padding:.9rem 1rem;background:var(--unknown-bg);border-radius:4px;font-size:.87rem}
.notice p+p{margin-top:.4rem}.notice.warning{background:var(--missing-bg);color:var(--missing)}
.table-wrap{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:6px}
table{width:100%;border-collapse:collapse;font-size:.85rem;text-align:left}
caption{text-align:left;padding:.8rem 1rem;color:var(--muted);font-size:.8rem}
thead{background:#f0f4f9}th{font-weight:650;color:var(--muted);white-space:nowrap}
th,td{padding:.75rem 1rem;vertical-align:top;border-top:1px solid var(--line)}
thead th{border-top:0}td.number,th.number{text-align:right;font-variant-numeric:tabular-nums}
.runs-table{min-width:620px}.run-title{font-weight:650}.token{display:block;margin-top:.2rem;
 overflow-wrap:anywhere;font:12px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace;color:var(--muted)}
.status{display:inline-flex;align-items:center;gap:.35rem;white-space:nowrap;font-size:.78rem;
 line-height:1.5;font-weight:650;border-radius:4px;padding:.15rem .45rem}
.status.present{color:var(--present);background:var(--present-bg)}
.status.missing{color:var(--missing);background:var(--missing-bg)}
.status.conflict{color:var(--conflict);background:var(--conflict-bg)}
.status.unknown{color:var(--unknown);background:var(--unknown-bg)}
.details-row>td{padding:0 1rem .8rem;border-top:0}details{font-size:.84rem}summary{
 cursor:pointer;width:fit-content;max-width:100%;color:#1d4ed8;padding:.3rem 0}
summary:hover{color:#173e9f}.edges{display:grid;gap:.6rem;padding:.55rem 0 .2rem}
.edge{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:.35rem 1rem;
 padding:.8rem 0;border-top:1px solid var(--line)}.edge-title{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap}
.edge-path{display:grid;grid-template-columns:3.5rem minmax(0,1fr);gap:.2rem .6rem;grid-column:1/-1;
 margin:.2rem 0 0;font-size:.8rem}.edge-path dt{color:var(--muted)}.edge-path dd{margin:0;
 overflow-wrap:anywhere;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
.reason{font-size:.78rem;color:var(--muted);overflow-wrap:anywhere;grid-column:1/-1}
.source-table{min-width:560px}.source-table td:first-child{max-width:300px}.source-table code{
 font-size:.76rem;overflow-wrap:anywhere}.source-counts{display:flex;gap:.5rem 1.5rem;flex-wrap:wrap;
 padding:.75rem 0;font-size:.85rem}.source-counts strong{font-variant-numeric:tabular-nums}
.empty{padding:1.1rem;background:var(--surface);border:1px solid var(--line);border-radius:6px}
.empty p+p{margin-top:.45rem}.limits{font-size:.83rem;color:var(--muted)}.limits ul{
 margin:.65rem 0 0;padding-left:1.3rem;max-width:85ch}.limits li+li{margin-top:.4rem}
footer{padding-top:1rem;padding-bottom:1.5rem;border-top:1px solid var(--line);font-size:.76rem;color:var(--muted)}
@media(max-width:640px){.header-inner,main,footer{padding-left:1rem;padding-right:1rem}
 h1{font-size:1.4rem}.overview{padding:1rem}.meta{gap:.5rem}.meta div{width:100%}
 .edge{grid-template-columns:minmax(0,1fr)}.edge-path{grid-template-columns:3rem minmax(0,1fr)}
 .coverage-line strong{font-size:1.3rem}th,td{padding:.65rem .75rem}}
@media print{body{background:#fff}.header-inner,main,footer{max-width:none;padding-left:0;padding-right:0}
 nav{display:none}.table-wrap{overflow:visible}section{break-inside:avoid}.token{font-size:10px}}
'''


class RenderError(ValueError):
    pass


def _text(value, *, limit=2048):
    if type(value) is not str or len(value) > limit:
        raise RenderError('INVALID_REPORT_TEXT')
    return escape(value, quote=True)


def _count(value):
    if type(value) is not int or not 0 <= value <= 100000:
        raise RenderError('INVALID_REPORT_COUNT')
    return value


def _badge(status):
    if status not in STATUSES:
        raise RenderError('INVALID_EDGE_STATUS')
    return f'<span class="status {status}"><span aria-hidden="true">{SYMBOLS[status]}</span>{LABELS[status]}</span>'


def _require(condition, code='INVALID_COVERAGE_REPORT'):
    if not condition:
        raise RenderError(code)


def _rate(value, expected):
    if expected is None:
        _require(value is None, 'UNSUPPORTED_COVERAGE_RATE')
    else:
        _require(type(value) in (float, int) and math.isfinite(value) and
                 math.isclose(value, expected, rel_tol=1e-9, abs_tol=1e-9), 'INCONSISTENT_COVERAGE_RATE')


def _codes(value):
    _require(type(value) is list and len(value) <= 32, 'INVALID_REASON_CODES')
    return ' / '.join(_reason(code) for code in value)


def _reason(code):
    safe = _text(code, limit=256)
    explanation = REASONS.get(code)
    return f'{explanation} (<code>{safe}</code>)' if explanation else f'<code>{safe}</code>'


def _validate(report):
    _require(type(report) is dict and type(report.get('schema_version')) is int and
             report['schema_version'] == 1 and report.get('scope') == 'verified-historical-data-plan-lineage')
    stamp = report.get('observed_at')
    _text(stamp, limit=64)
    try:
        _require(datetime.fromisoformat(stamp.replace('Z', '+00:00')).tzinfo is not None,
                 'INVALID_OBSERVATION_TIME')
    except ValueError:
        raise RenderError('INVALID_OBSERVATION_TIME') from None
    observation = report.get('observation')
    _require(type(observation) is dict and observation.get('status') in ('complete', 'incomplete')
             and type(observation.get('source_stable')) is bool
             and (observation.get('metadata_stable') is None or type(observation['metadata_stable']) is bool))
    _codes(observation.get('reason_codes'))
    # The collector completes an observation whenever no failure reason remains
    # and its final source inventory agrees with the initial snapshot.
    _require(observation['status'] != 'incomplete' or observation['reason_codes'] or
             not observation['source_stable'], 'INCONSISTENT_OBSERVATION_COMPLETION')
    inventory, coverage, runs = report.get('inventory'), report.get('coverage'), report.get('runs')
    _require(type(inventory) is dict and type(coverage) is dict and type(runs) is list and len(runs) <= 512)
    for key in ('entries', 'uuid_runs', 'eligible', 'valid', 'failed', 'incomplete', 'invalid', 'unrecognized_entries'):
        _count(inventory.get(key))
    _require(type(inventory.get('complete')) is bool, 'INVALID_INVENTORY_COMPLETENESS')
    _require(inventory['complete'] or
              any(code in observation['reason_codes'] for code in
                  ('SOURCE_INVENTORY_CHANGED', 'SOURCE_INVENTORY_UNAVAILABLE')),
              'INCONSISTENT_INVENTORY_REASON')
    _require(not inventory['complete'] or not any(
              code in observation['reason_codes'] for code in
              ('SOURCE_INVENTORY_CHANGED', 'SOURCE_INVENTORY_UNAVAILABLE')),
              'INCONSISTENT_INVENTORY_REASON')
    _require(inventory['complete'] or not observation['source_stable'],
             'INCONSISTENT_SOURCE_STABILITY')
    _require(inventory['eligible'] != 0 or observation['metadata_stable'] is None,
             'INCONSISTENT_CATALOG_STABILITY')
    if observation['status'] == 'complete':
        _require(inventory['complete'] and observation['source_stable'] and
                  (observation['metadata_stable'] is True or inventory['eligible'] == 0 and
                   observation['metadata_stable'] is None) and not observation['reason_codes'] and
                  not any(inventory[key] for key in ('failed', 'incomplete', 'invalid', 'unrecognized_entries')),
                  'INCONSISTENT_OBSERVATION_COMPLETION')
    for key in ('denominator', 'known', *STATUSES):
        _count(coverage.get(key))
    _require(type(coverage.get('complete')) is bool)
    counts, sources, tokens, eligible = dict.fromkeys(STATUSES, 0), dict.fromkeys(SOURCE_LABELS, 0), set(), 0
    for run in runs:
        _require(type(run) is dict)
        token = run.get('run_token')
        _require(type(token) is str and re.fullmatch('[0-9a-f]{32}', token) and token not in tokens,
                 'INVALID_OR_DUPLICATE_RUN_TOKEN')
        tokens.add(token)
        operation = run.get('operation')
        _require(operation is None or type(operation) is str and operation in OPERATIONS, 'INVALID_OPERATION')
        source = run.get('source_status')
        _require(type(source) is str and source in SOURCE_LABELS and type(run.get('eligible')) is bool)
        # Initial validation admits only valid runs. An eligible run can later
        # become incomplete when its saved source drifts, but not invalid/failed.
        _require((source == 'valid' and run['eligible']) or
                 (source == 'incomplete') or
                 (source in ('failed', 'invalid') and not run['eligible']),
                 'INCONSISTENT_SOURCE_ELIGIBILITY')
        _require(not run['eligible'] or operation is not None, 'INCONSISTENT_SOURCE_ELIGIBILITY')
        sources[source] += 1
        _codes(run.get('reason_codes'))
        # A recorded failed execution has a specific first-verification result;
        # the aggregate unverified-run reason must not hide its missing detail.
        _require(source != 'failed' or 'RECORDED_RUN_FAILED' in run['reason_codes'],
                   'INCONSISTENT_SOURCE_REASON')
        # First verification records a result even for excluded runs; a bare
        # invalid/incomplete label cannot explain why evidence was excluded.
        _require(source not in ('invalid', 'incomplete') or run['reason_codes'],
                 'INCONSISTENT_SOURCE_REASON')
        # A valid source has passed the collector's first artifact verification;
        # aggregate stability alone cannot substitute for that per-run evidence.
        _require(not run['eligible'] or 'SOURCE_VERIFIED' in run['reason_codes'],
                   'INCONSISTENT_SOURCE_REASON')
        # An initially verified run can only be demoted after its final source
        # snapshot changed or became unavailable; retain the per-run explanation.
        _require(source != 'incomplete' or not run['eligible'] or
                 'SOURCE_CHANGED_OR_UNVERIFIABLE' in run['reason_codes'],
                 'INCONSISTENT_SOURCE_REASON')
        for key in ('source_sha256_before', 'source_sha256_after', 'metadata_sha256_before', 'metadata_sha256_after'):
            value = run.get(key)
            _require(value is None or type(value) is str and re.fullmatch('[0-9a-f]{64}', value), 'INVALID_EVIDENCE_HASH')
        _require(not run['eligible'] or run['source_sha256_before'] is not None,
                 'INCONSISTENT_SOURCE_ELIGIBILITY')
        # A valid source survived the final snapshot; a failed final read is
        # classified incomplete by the collector, even if the observation as
        # a whole is incomplete for some other reason.
        _require(source != 'valid' or
                  run['source_sha256_before'] == run['source_sha256_after'],
                  'INCONSISTENT_SOURCE_STATUS')
        # Initially eligible incomplete runs are the ones whose final saved
        # evidence changed or could not be read; identical final evidence
        # cannot explain demoting a verified run to incomplete.
        _require(source != 'incomplete' or not run['eligible'] or
                  run['source_sha256_after'] is None or
                  run['source_sha256_before'] != run['source_sha256_after'],
                  'INCONSISTENT_SOURCE_STATUS')
        if observation['source_stable']:
            _require(run['source_sha256_before'] is not None and
                     run['source_sha256_before'] == run['source_sha256_after'],
                     'INCONSISTENT_SOURCE_STABILITY')
        if observation['metadata_stable'] is True and run['eligible']:
            _require(run['metadata_sha256_before'] is not None and
                     run['metadata_sha256_before'] == run['metadata_sha256_after'],
                     'INCONSISTENT_CATALOG_STABILITY')
        edges = run.get('edges')
        _require(type(edges) is list, 'INVALID_EDGES')
        if run['eligible']:
            eligible += 1
            _require(len(edges) == 2 and all(type(edge) is dict for edge in edges) and
                     all(sum(edge.get('target') == target for edge in edges) == 1 for target in ('stg', 'mart')),
                      'ELIGIBLE_RUN_REQUIRES_TWO_EDGES')
            # Known edges come only from matching first/final catalog reads.
            # The aggregate stability field can be null/false for other runs.
            if any(type(edge) is dict and edge.get('status') in ('present', 'missing', 'conflict') for edge in edges):
                _require(run['metadata_sha256_before'] is not None and
                         run['metadata_sha256_before'] == run['metadata_sha256_after'],
                         'INCONSISTENT_CATALOG_EVIDENCE')
        else:
            _require(not edges, 'INELIGIBLE_RUN_HAS_EDGES')
        for edge in edges:
            _require(type(edge) is dict and type(edge.get('status')) is str and edge['status'] in STATUSES)
            suffix = f'shirokuma_local.iceberg.local_experiment.dataops_{token}_'
            expected_paths = {'stg': ('shirokuma_local.iceberg.smoke.fixture_v1', suffix + 'stg'),
                              'mart': (suffix + 'stg', suffix + 'mart')}
            _require((edge.get('from'), edge.get('to')) == expected_paths[edge['target']],
                     'INCONSISTENT_EDGE_PATH')
            # The collector retains the denominator on source drift and on a
            # failed final inventory, but cannot certify any of those edges.
            _require((inventory['complete'] and source == 'valid') or edge['status'] == 'unknown',
                     'UNVERIFIED_EDGE_CLAIM')
            for key in ('from', 'to', 'reason_code'):
                _text(edge.get(key), limit=2048 if key != 'reason_code' else 256)
            _require(EDGE_REASON_STATUS.get(edge['reason_code'], edge['status']) == edge['status'],
                     'INCONSISTENT_EDGE_REASON')
            counts[edge['status']] += 1
    _require(inventory['uuid_runs'] == len(runs) and inventory['eligible'] == eligible and
              inventory['entries'] == inventory['uuid_runs'] + inventory['unrecognized_entries'] and
              all(inventory[key] == value for key, value in sources.items()), 'INCONSISTENT_SOURCE_COUNTS')
    _require(('UNVERIFIED_SOURCE_RUNS' in observation['reason_codes']) ==
              (inventory['failed'] + inventory['invalid'] + inventory['incomplete'] > 0),
              'INCONSISTENT_SOURCE_REASON')
    _require(('UNRECOGNIZED_SOURCE_ENTRIES' in observation['reason_codes']) ==
              (inventory['unrecognized_entries'] > 0),
              'INCONSISTENT_INVENTORY_REASON')
    # The collector marks an inventory incomplete when its final listing
    # changes or fails. With a complete inventory and identical saved snapshots
    # for every run, its source-stability result cannot be false.
    _require(not inventory['complete'] or observation['source_stable'] or
             any(row['source_sha256_before'] is None or
                 row['source_sha256_before'] != row['source_sha256_after'] for row in runs),
             'INCONSISTENT_SOURCE_STABILITY')
    denominator = eligible * 2
    known = denominator - counts['unknown']
    _require(not counts['unknown'] or 'UNKNOWN_EXPECTED_EDGES' in observation['reason_codes'],
             'INCONSISTENT_UNKNOWN_REASON')
    _require(counts['unknown'] > 0 or 'UNKNOWN_EXPECTED_EDGES' not in observation['reason_codes'],
              'INCONSISTENT_UNKNOWN_REASON')
    # A changed catalog observation or unknown target always leaves at least
    # one expected edge unknown; it cannot yield fully known coverage.
    _require(observation['metadata_stable'] is not False or counts['unknown'] > 0,
             'INCONSISTENT_CATALOG_STABILITY')
    _require(coverage['denominator'] == denominator and coverage['known'] == known and
             all(coverage[key] == value for key, value in counts.items()), 'INCONSISTENT_EDGE_COUNTS')
    _require(observation['status'] != 'complete' or not counts['unknown'],
             'INCONSISTENT_OBSERVATION_COMPLETION')
    _require(coverage['complete'] is (denominator > 0 and counts['present'] == denominator),
             'INCONSISTENT_COVERAGE_COMPLETION')
    _rate(coverage.get('rate'), counts['present'] / denominator if denominator and not counts['unknown'] else None)
    _rate(coverage.get('observation_rate'), known / denominator if denominator else None)
    limits = report.get('limits')
    _require(type(limits) is list and len(limits) <= 32)
    for limit in limits:
        _text(limit)


def _source_label(status):
    semantic = {'valid': 'present', 'failed': 'conflict', 'incomplete': 'unknown', 'invalid': 'missing'}[status]
    return f'<span class="status {semantic}">{SOURCE_LABELS[status]}</span>'


def _edge_detail(edge):
    name = 'fixture → stg' if edge['target'] == 'stg' else 'stg → mart'
    return f'''<div class="edge">
<h3 class="edge-title">{name}</h3>{_badge(edge['status'])}
<dl class="edge-path"><dt>起点</dt><dd>{_text(edge['from'])}</dd>
<dt>到達先</dt><dd>{_text(edge['to'])}</dd></dl>
<p class="reason">照合理由: {_reason(edge['reason_code'])}</p></div>'''


def _runs(report):
    runs = [run for run in report['runs'] if run['eligible']]
    if not runs:
        return '<div class="empty"><p>集計対象の実験履歴はありません。</p><p class="muted">保存済みData Planの証跡を検証できたrunが、ここに表示されます。対象外の理由は下の証跡一覧で確認できます。</p></div>'
    rows = []
    for run in runs:
        counts = {status: sum(edge['status'] == status for edge in run['edges']) for status in STATUSES}
        label = OPERATIONS.get(run['operation'], '変換内容は未確認')
        numbers = ''.join(f'<td class="number">{counts[status]}</td>' for status in STATUSES)
        note = '' if run['source_status'] == 'valid' else '<span class="status unknown">観測途中に証跡が変化</span>'
        rows.append(f'''<tr><td><span class="run-title">{_text(label)}</span>
<code class="token">{_text(run['run_token'])}</code>{note}</td>{numbers}</tr>''')
        opened = ' open' if any(counts[status] for status in ('missing', 'conflict', 'unknown')) else ''
        details = ''.join(_edge_detail(edge) for edge in sorted(run['edges'], key=lambda value: value['target'] != 'stg'))
        rows.append(f'''<tr class="details-row"><td colspan="5"><details{opened}>
<summary>2エッジの照合詳細</summary><div class="edges">{details}</div></details></td></tr>''')
    headers = ''.join(f'<th class="number" scope="col">{LABELS[status]}</th>' for status in STATUSES)
    return f'''<div class="table-wrap" tabindex="0" role="region" aria-label="実験ごとのlineage照合表">
<table class="runs-table"><caption>対象は検証済みの実験履歴。期待する関係はrunごとに2エッジです。</caption>
<thead><tr><th scope="col">実験 / run token</th>{headers}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>'''


def _sources(report):
    inventory = report['inventory']
    counts = ''.join(f'<span>{SOURCE_LABELS[status]} <strong>{inventory[status]}</strong>件</span>'
                     for status in SOURCE_LABELS)
    counts += f'<span>識別できない項目 <strong>{inventory["unrecognized_entries"]}</strong>件</span>'
    rows = []
    # Non-valid sources precede ordinary retained evidence.
    for run in sorted(report['runs'], key=lambda value: (value['source_status'] == 'valid', value['run_token'])):
        included = '集計対象' if run['eligible'] else '分母から除外'
        if run['eligible'] and run['source_status'] != 'valid':
            included = '分母を保持 / 2エッジ不明'
        reason = _codes(run['reason_codes']) or '追加の理由コードなし'
        source_hash = run.get('source_sha256_before')
        fingerprint = f'<code>{_text(source_hash)}</code>' if source_hash else '未確認'
        rows.append(f'''<tr><th scope="row"><code class="token">{_text(run['run_token'])}</code></th>
<td>{_source_label(run['source_status'])}</td><td>{included}</td>
<td><span class="reason">{reason}</span><details><summary>証跡の識別子</summary>
<p class="reason">観測開始時のsource SHA256</p>{fingerprint}</details></td></tr>''')
    if not rows:
        table = '<div class="empty"><p>UUIDで識別できる実験証跡はありません。</p></div>'
    else:
        table = f'''<div class="table-wrap" tabindex="0" role="region" aria-label="保存証跡の分類表">
<table class="source-table"><caption>無効・未完了・実行失敗も一覧に残します。観測途中の変化は分母から取り除きません。</caption>
<thead><tr><th scope="col">run token</th><th scope="col">証跡の状態</th><th scope="col">集計上の扱い</th>
<th scope="col">理由 / 証跡</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>'''
    return f'<div class="source-counts">{counts}</div>{table}'


def render(report) -> str:
    """Render an internally consistent schema-v1 report without IO or mutation."""
    _validate(report)
    coverage, inventory, observation = report['coverage'], report['inventory'], report['observation']
    denominator, present, known = coverage['denominator'], coverage['present'], coverage['known']
    rate = f'{coverage["rate"]:.1%}' if coverage['rate'] is not None else '算出しない'
    observation_rate = f'{coverage["observation_rate"]:.1%}' if coverage['observation_rate'] is not None else '算出しない'
    summary = f'{present} / {denominator} エッジ' if denominator else '集計対象なし'
    segments = ''.join(f'<span class="segment {status}" style="width:{coverage[status] / denominator * 100:.6f}%"></span>'
                       for status in STATUSES if denominator and coverage[status])
    legend = ''.join(f'<li><span class="legend-symbol" aria-hidden="true">{SYMBOLS[status]}</span>'
                     f'{LABELS[status]} <b>{coverage[status]}</b></li>' for status in STATUSES)
    reasons = _codes(observation['reason_codes'])
    observation_text = '観測完了' if observation['status'] == 'complete' else '観測未完了'
    warning = ''
    if observation['status'] == 'incomplete':
        warning = f'<aside class="notice warning"><p><strong>この観測には未確認の項目があります。</strong></p><p>理由: {reasons or "理由コードなし"}</p></aside>'
    if not inventory['complete']:
        warning += '<aside class="notice warning"><p><strong>保存証跡の一覧取得は未完了です。</strong></p><p>以下の件数と率は取得できた範囲の集計です。保存済みの全実験を網羅した結果とは扱えません。</p></aside>'
    excluded = inventory['uuid_runs'] - inventory['eligible']
    scope_notice = f'''<p class="denominator"><strong>分母:</strong> 観測開始時に証跡を検証できた
{inventory['eligible']} run × 2エッジ = {denominator}。UUIDのある証跡{inventory['uuid_runs']}件のうち
{excluded}件を対象外としています。識別できない項目は{inventory['unrecognized_entries']}件です。
途中で証跡が変化したrunは分母を保ち、不明として扱います。</p>'''
    if coverage['unknown']:
        rate_note = '不明なエッジがあるため登録率は算出しません。観測済みのエッジだけへ分母を縮小しません。'
    elif not denominator:
        rate_note = '分母が0のため登録率・観測率は算出できません。100%や0%とは扱いません。'
    else:
        rate_note = '登録率は登録を確認したエッジ数 ÷ 集計対象の全エッジ数です。対象外の証跡やcatalog全体の充足率を表しません。'
    limits = ''.join(f'<li>{_text(LIMITS_JA.get(value, value))}</li>' for value in report['limits'])
    stamp = _text(report['observed_at'], limit=64)
    source_stable = '前後一致' if observation['source_stable'] else '一致を未確認'
    metadata_stable = ('未観測' if observation['metadata_stable'] is None else
                       '前後一致' if observation['metadata_stable'] else '一致を未確認')
    return f'''<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<meta name="referrer" content="no-referrer"><title>Lineageの登録状況 | Shirokuma</title><style>{STYLE}</style></head>
<body><header class="masthead"><div class="header-inner"><p class="brand">Shirokuma / ローカル実験</p>
<div class="title-row"><h1>Lineageの登録状況</h1><span class="scope-label">実験履歴の照合</span></div>
<p class="subtitle">検証済みData Planの期待する関係を、OpenMetadataの登録内容と照合した記録です。</p>
<dl class="meta"><div><dt>観測時刻</dt><dd><time datetime="{stamp}">{stamp}</time></dd></div>
<div><dt>取得状態</dt><dd>{observation_text}</dd></div><div><dt>対象</dt><dd>fixture → stg → mart</dd></div>
<div><dt>保存証跡</dt><dd>{source_stable}</dd></div><div><dt>catalog</dt><dd>{metadata_stable}</dd></div></dl>
<nav aria-label="レポート内の移動"><a href="#coverage">集計</a><a href="#runs">実験ごとの照合</a><a href="#sources">証跡一覧</a><a href="#limits">範囲と制約</a></nav>
</div></header><main>{warning}
<aside class="notice"><p><strong>対象は実験の履歴metadataです。</strong> SQLの生成テーブルは実験後に削除されます。
ここにある登録状況は、live tableの存在・稼働状態・現在のデータ品質を表しません。</p></aside>
<section id="coverage" class="overview" aria-labelledby="coverage-title"><h2 id="coverage-title">履歴エッジの照合</h2>
<div class="coverage-line"><strong>{summary}</strong><span>登録を確認 / 登録率 {rate}</span></div>
<div class="bar" aria-hidden="true">{segments}</div><ul class="legend" aria-label="エッジの状態別件数">{legend}</ul>
{scope_notice}<p class="muted" style="font-size:.83rem;margin-top:.5rem">観測できたエッジ: {known} / {denominator}（観測率 {observation_rate}）。{rate_note}</p></section>
<section id="runs" aria-labelledby="runs-title"><div class="section-heading"><h2 id="runs-title">実験ごとの照合</h2>
<p>欠落・競合・不明がある実験は詳細を展開</p></div>{_runs(report)}</section>
<section id="sources" aria-labelledby="sources-title"><div class="section-heading"><h2 id="sources-title">保存証跡の分類</h2>
<p>検出した項目 {inventory['entries']}件</p></div>{_sources(report)}</section>
<section id="limits" class="limits" aria-labelledby="limits-title"><h2 id="limits-title">範囲と制約</h2>
<ul><li>このHTMLは観測時点の静的レポートです。自動更新・登録の修復・metadataの変更は行いません。</li>
<li>登録を確認できても、catalog全体の完全性や本番受入を保証するものではありません。</li>{limits}</ul></section>
</main><footer>Shirokuma · 履歴lineage coverage · schema 1 · 外部通信なし</footer></body></html>'''
