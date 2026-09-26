#!/usr/bin/env python3
"""Render an existing FinOps JSON snapshot; never collect or write observations."""
from __future__ import annotations

import argparse
from decimal import Decimal, ROUND_HALF_UP, localcontext
from html import escape
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.finops import contract as C

MAX_INPUT_BYTES = C.MAX_PUBLIC_BYTES
ERROR = 'FINOPS_REPORT_INVALID'
NAMES = {'host': 'macOS · 作業領域', 'colima': 'Colima · Kubernetes保存領域',
         'trino': 'Trino', 'polaris': 'Polaris', 'seaweedfs': 'SeaweedFS',
         'mlflow': 'MLflow', 'notebook-lab': 'JupyterLab', 'model-serving': 'Model Serving'}
SEVERITIES = {'normal': ('normal', '25%超'), 'warning': ('warning', '注意 · 25%以下'),
              'low': ('warning', '残量低下 · 20%以下'), 'critical': ('critical', '要対応 · 15%以下'),
              'unknown': ('unknown', '未取得')}
REASONS = {'READ_FAILED': '取得に失敗', 'READ_LIMIT': '取得上限を超過', 'READ_TIMEOUT': '取得が時間切れ',
           'CLEANUP_UNVERIFIED': '取得処理の終了を確認できず', 'SCOPE_INVALID': '対象を確認できず',
           'TARGET_MISSING': '対象が見つからず', 'TARGET_AMBIGUOUS': '対象が複数あり特定できず',
           'IDENTITY_CHANGED': '取得中に対象が変化', 'SAMPLE_INVALID': '観測値を確認できず',
           'SAMPLE_STALE': '観測時刻が許容範囲外', 'DISK_INVALID': '容量を確認できず',
           'INFERENCE_INVALID': '宣言eventの集計を確認できず',
           'STOPPED': '停止中', 'UNINITIALIZED': '未初期化',
           'OWNERSHIP_UNKNOWN': 'Shirokumaのプロセスと確認できず'}
METRICS = [('prompt_tokens', '入力トークン', 'トークン'), ('completion_tokens', '出力トークン', 'トークン'),
           ('inference_cost_estimate', '費用見積り', 'producer定義値・通貨未記録'),
           ('event_duration_ms', 'event duration', 'ms・推論latencyとは限りません'),
           ('cpu_seconds', 'CPU時間proxy', 'seconds'),
           ('memory_mb_seconds', 'memory時間proxy', 'MB-seconds・MBの定義未記録')]

# Preserve the established metadata/coverage_render.py report palette and system typography.
STYLE = '''
:root{color-scheme:light;--bg:#f7f9fc;--surface:#fff;--ink:#17233a;--muted:#52627a;
 --line:#dbe3ef;--accent:#2563eb;--normal:#10634e;--normal-bg:#e6f4ee;
 --warning:#8a4b00;--warning-bg:#fff3d9;--critical:#9c2d34;--critical-bg:#fff0f1;
 --unknown:#52627a;--unknown-bg:#edf1f6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
 font:14px/1.65 -apple-system,BlinkMacSystemFont,"Hiragino Kaku Gothic ProN",Meiryo,system-ui,sans-serif}
header{background:var(--surface);border-bottom:1px solid var(--line)}
.inner,main,footer{max-width:1180px;margin:auto;padding:1.5rem 2rem}
h1,h2,h3,p{margin:0}h1{font-size:1.75rem;line-height:1.4;letter-spacing:-.015em}
h2{font-size:1.2rem}h3{font-size:1rem;margin:1rem 0 .5rem}h1,h2,h3{text-wrap:balance}
p{max-width:75ch;text-wrap:pretty}.brand,.muted,caption,.sub{color:var(--muted)}
.brand{font-weight:650;font-size:.8rem;margin-bottom:.6rem}.intro{margin:.7rem 0}
.meta,nav{display:flex;flex-wrap:wrap;gap:.5rem 1.5rem;font-size:.85rem}
nav{margin-top:1rem}a{color:#1d4ed8;text-underline-offset:.2em}a:hover{color:#173e9f}
a:focus-visible,summary:focus-visible,[tabindex]:focus-visible{outline:3px solid var(--accent);outline-offset:4px}
section{margin-bottom:2.3rem}section>p{margin:.5rem 0 1rem}.notice{padding:.85rem 1rem;margin-bottom:1.5rem;
 border-radius:4px;background:var(--unknown-bg)}.notice.warning{background:var(--warning-bg);color:var(--warning)}
.notice p+p{margin-top:.35rem}.table-wrap{overflow-x:auto;background:var(--surface);
 border:1px solid var(--line);border-radius:6px}
table{width:100%;border-collapse:collapse;text-align:left;font-size:.85rem}
caption{padding:.75rem 1rem;text-align:left;font-size:.8rem}thead{background:#f0f4f9}
th,td{padding:.75rem 1rem;border-top:1px solid var(--line);vertical-align:top}
thead th{border-top:0;color:var(--muted);font-weight:650}th[scope=row]{font-weight:650}
.numeric{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.sub{display:block;font-size:.78rem;font-weight:400;margin-top:.2rem}.model{min-width:10rem;max-width:23rem;overflow-wrap:anywhere}
.status{display:inline-block;font-size:.78rem;font-weight:650;white-space:nowrap;padding:.15rem .5rem;border-radius:4px}
.normal{color:var(--normal);background:var(--normal-bg)}.warning{color:var(--warning);background:var(--warning-bg)}
.critical{color:var(--critical);background:var(--critical-bg)}.unknown{color:var(--unknown);background:var(--unknown-bg)}
.reason{white-space:normal;min-width:10rem}details{margin:1rem 0}summary{cursor:pointer;font-weight:600;color:#1d4ed8}
summary:hover{color:#173e9f}details[open]>summary{margin-bottom:.8rem}pre{padding:1rem;background:var(--surface);
 border:1px solid var(--line);overflow:auto;max-height:28rem;font-size:.75rem;white-space:pre-wrap;overflow-wrap:anywhere}
code{font:12px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}
.thresholds{margin-top:.8rem;font-size:.8rem;color:var(--muted)}footer{border-top:1px solid var(--line);font-size:.8rem;color:var(--muted)}
@media(max-width:640px){.inner,main,footer{padding:1.1rem}h1{font-size:1.5rem}th,td{padding:.65rem .7rem}
 .table-wrap table{min-width:580px}.meta{display:block}.meta>span{display:block;margin-top:.3rem}}
@media print{body{background:white}.inner,main,footer{padding:1rem 0}nav{display:none}.table-wrap{overflow:visible}}
'''


class RenderError(ValueError):
    def __init__(self): super().__init__(ERROR)


def _validated(value):
    try: return C.validate_public(value)
    except Exception: raise RenderError() from None


def parse_report(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_INPUT_BYTES: raise RenderError()
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value: raise RenderError()
            value[key] = item
        return value
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(RenderError()))
    except Exception: raise RenderError() from None
    return _validated(value)


def _e(value): return escape(str(value), quote=True)


def _number(value):
    if value is None: return '不明'
    result = format(Decimal(str(value)), ',f')
    return result.rstrip('0').rstrip('.') if '.' in result else result


def _scaled(value, divisor, unit, places=3):
    with localcontext() as context:
        context.prec = 80
        exact = Decimal(value) / Decimal(divisor)
        rounded = exact.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    prefix = '≈ ' if exact != rounded else ''
    return f'<span title="原値: {_e(value)}">{prefix}{_number(rounded)} {_e(unit)}</span>'


def _reason(value):
    return f'<span class="sub reason">{_e(REASONS[value])}<br><code>{_e(value)}</code></span>'


def _time(value):
    whole, separator, fraction = value[:-1].partition('.')
    fraction = fraction.rstrip('0')
    display = whole.replace('T', ' ') + ('.' + fraction if fraction else '')
    return f'<time datetime="{_e(value)}">{_e(display)} UTC</time>'


def _table(caption, headers, rows):
    head = ''.join(f'<th scope="col">{_e(label)}</th>' for label in headers)
    return (f'<div class="table-wrap" role="region" aria-label="{_e(caption)}" tabindex="0"><table>'
            f'<caption>{_e(caption)}</caption><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def _disks(report):
    rows = []
    for disk in report['disks']:
        css, label = SEVERITIES[disk['severity']]
        cells = '<td>未取得</td><td>未取得</td><td>未取得</td>'
        if disk['state'] == 'observed':
            cells = (f'<td class="numeric">{_scaled(disk["free_bytes"], 2**30, "GiB")}</td>'
                     f'<td class="numeric">{_scaled(disk["total_bytes"], 2**30, "GiB")}</td>'
                     f'<td class="numeric">{_scaled(str(int(disk["free_bytes"])*100), disk["total_bytes"], "%", 2)}</td>')
        state = f'<span class="status {css}">{label}</span>' + (_reason(disk['reason']) if disk['reason'] else '')
        rows.append(f'<tr><th scope="row">{NAMES[disk["scope"]]}<span class="sub">{_time(disk["observed_at"])}</span></th>{cells}<td>{state}</td></tr>')
    return _table('2つのファイルシステムの空き容量（GiB = 1,073,741,824 bytes）', ['対象', '空き', '全容量', '空き率', '判定'], rows)


def _components(report):
    rows = []
    for item in report['components']:
        label = NAMES[item['component']]
        if item['state'] == 'unknown':
            cells = '<td>未取得</td><td>未取得</td><td>未取得</td>'
            state = '<span class="status unknown">未取得</span>' + _reason(item['reason'])
        else:
            sample = item['sample']
            cells = (f'<td class="numeric">{_scaled(sample["cpu_nanocores"], 10**9, "cores", 6)}</td>'
                     f'<td class="numeric">{_scaled(sample["memory_working_set_bytes"], 2**20, "MiB")}</td>'
                     f'<td>{_scaled(sample["window_ns"], 10**9, "秒", 9)}<span class="sub">{_time(sample["timestamp"])}</span></td>')
            state = '<span class="status normal">観測済み</span>'
        rows.append(f'<tr><th scope="row">{label}</th>{cells}<td>{state}</td></tr>')
    return _table('固定3サービスの主コンテナ。CPUは観測区間内の平均値。', ['対象', 'CPU平均', 'メモリ（working set）', '観測区間 / サンプル時刻', '取得状態'], rows)


def _local_processes(report):
    if 'local_processes' not in report: return ''
    rows = []
    states = {'stopped': '停止中', 'unavailable': '未取得', 'ownership-unknown': '所有確認不能'}
    for item in report['local_processes']['components']:
        label, detail = NAMES[item['component']], ''
        if item['state'] == 'observed':
            sample, identity = item['sample'], item['identity']
            detail = f'<span class="sub">{identity["process_count"]} processes'
            if item['component'] == 'notebook-lab': detail += f' · kernel {identity["kernel_count"]}個を含む'
            detail += '</span>'
            cells = (f'<td class="numeric">{_scaled(str(int(sample["cpu_time_delta_ms"])*10**6), sample["elapsed_ns"], "cores", 6)}</td>'
                     f'<td class="numeric">{_scaled(sample["rss_bytes"], 2**20, "MiB")}</td>'
                     f'<td>{_scaled(sample["elapsed_ns"], 10**9, "秒", 3)}<span class="sub">{_time(item["observed_at"])}</span></td>')
            state = '<span class="status normal">観測済み</span>'
        else:
            cells = '<td>対象なし</td><td>対象なし</td><td>—</td>' if item['state'] == 'stopped' else '<td>未取得</td><td>未取得</td><td>未取得</td>'
            state = f'<span class="status unknown">{states[item["state"]]}</span>'
            if item['reason'] != 'STOPPED': state += _reason(item['reason'])
        rows.append(f'<tr><th scope="row">{label}{detail}</th>{cells}<td>{state}</td></tr>')
    table = _table('Shirokumaが所有を確認したmacOSプロセス。JupyterLabにはkernelを含みます。',
                   ['対象', 'CPU平均', 'メモリ（RSS合計）', '観測区間 / 終了時刻', '取得状態'], rows)
    return ('<section id="local-processes"><h2>ローカル学習サービス</h2>'
            '<p>短い観測区間のCPU時間差と、区間終了時のRSS合計です。1 coreはCPU 1個を占有する量に相当します。</p>'
            + table + '<p class="thresholds">RSSは共有ページを重複して数えます。上のコンテナのworking setやホスト全体の使用量とは合算しません。'
            'CPU時間には計測の刻みがあり、0 coresでもidleとは判定できません。kernelが起動した子プロセスは対象外です。</p></section>')


def _storage(report):
    if 'storage' not in report: return ''
    names = {'polaris-postgresql': 'Polaris PostgreSQL', **NAMES}
    rows = []
    for item in report['storage']['components']:
        if item['state'] == 'unknown':
            detail = '未取得' + _reason(item['reason'])
            capacity = '<td>未取得</td><td>未取得</td>'
        elif item['state'] == 'no_direct_pvc':
            detail = '直接のPVC参照なし（disk不使用の証明ではありません）'
            capacity = '<td>対象なし</td><td>対象なし</td>'
        else:
            pvc = item['pvc']; pv = item['pv']
            detail = f'{_e(item["namespace"])} / {_e(pvc["name"])} <span class="sub">PVC {_e(pvc["phase"])}'
            if pv: detail += f' · PV {_e(pv["name"])} · reclaim {_e(pv["reclaim_policy"])}'
            detail += (' · StatefulSet削除/縮小 ' + _e(item['retention']['when_deleted']) + '/'
                       + _e(item['retention']['when_scaled']) + ' · template prune '
                       + _e(item['retention']['template_prune'] or '未設定') + '</span>')
            capacity = (f'<td class="numeric">{_scaled(pvc["requested_bytes"],2**30,"GiB")}</td>'
                        + (f'<td class="numeric">{_scaled(pvc["capacity_bytes"],2**30,"GiB")}</td>'
                           if pvc['capacity_bytes'] is not None else '<td>未取得</td>'))
            if pv: capacity += f'<td class="numeric">{_scaled(pv["capacity_bytes"],2**30,"GiB")}</td>'
            else: capacity += '<td>未取得</td>'
        if item['state'] == 'unknown': capacity += '<td>未取得</td>'
        if item['state'] == 'no_direct_pvc': capacity += '<td>対象なし</td>'
        rows.append(f'<tr><th scope="row">{names[item["component"]]}</th><td>{detail}</td>{capacity}'
                    '<td>未計測（NOT_MEASURED）</td></tr>')
    return ('<section id="storage"><h2>保存領域の参照関係</h2>'
            '<p>PVC要求・宣言容量は実使用量、物理予約やquotaではありません。使用bytesは計測していません。</p>'
            + _table('固定4workloadのKubernetes API参照。PVCは共有先を重複計上しません。',
                     ['対象','PVC/PV関係','PVC要求','PVC宣言容量','PV宣言容量','実使用量'],rows)
            + '<p class="thresholds">host/ColimaのdfやコンテナのRSSとも合算しません。参照関係はI/Oやmount健全性を証明しません。</p></section>')


def _metric(metric):
    headline = _number(metric['total']) if metric['total'] is not None else '全体不明'
    subtotal = '' if metric['total'] is not None else f'<span class="sub">既知小計 {_e(_number(metric["known_sum"]))}</span>'
    return (f'{_e(headline)}{subtotal}<span class="sub">既知 {_number(metric["known_events"])}件 / '
            f'未記録 {_number(metric["unknown_events"])}件</span>')


def _inference(report):
    inference = report['inference']
    if inference['state'] == 'unknown': return '<p class="notice">宣言eventの集計は未取得です。' + _reason(inference['reason']) + '</p>'
    if not inference['groups']: return '<p class="notice">この24時間に対象となるモデルの記録は0件です。実モデル呼出しが無かったことは示しません。</p>'
    rows, details = [], []
    for group in inference['groups']:
        name = f'{_e(group["model_name"])}<span class="sub">{_e(group["model_provider"])}</span>'
        cells = ''.join(f'<td class="numeric">{_metric(group[key])}</td>' for key in ('prompt_tokens', 'completion_tokens', 'inference_cost_estimate'))
        rows.append(f'<tr><th scope="row" class="model">{name}</th><td class="numeric">{_number(group["recorded_model_events"])}</td>{cells}</tr>')
        detail_rows = []
        for key, label, unit in METRICS:
            metric = group[key]
            detail_rows.append(f'<tr><th scope="row">{label}</th><td class="numeric">{_e(_number(metric["known_sum"]))}</td>'
                f'<td class="numeric">{_e(_number(metric["total"]))}</td><td>{metric["known_events"]} / {metric["unknown_events"]}</td><td>{unit}</td></tr>')
        details.append(f'<h3 class="model">{name}</h3>' + _table('保存された宣言値とcoverage', ['指標', '既知小計', '全体', '既知 / 未記録 event数', '単位・意味'], detail_rows))
    return (_table('provider / model別の宣言event。トークンは記録された個数、費用は記録元が定義した見積り値。',
                   ['モデル / provider', '宣言event数', '入力トークン', '出力トークン', '費用見積り（通貨未記録）'], rows)
            + '<details><summary>event durationと推論proxyの詳細</summary>' + ''.join(details) + '</details>')


def render(report):
    report = _validated(report)
    complete = report['result'] == 'complete'
    status = ('全取得項目を確認' if report['schema_version'] >= 2 else '全取得項目を観測') if complete else '一部の取得項目が未取得'
    warnings = [f'{NAMES[item["scope"]]}: {_e(item["message"])}' for item in report['recommendations']]
    notice = ('<p><strong>次の大容量実験の前に</strong></p><p>' + '<br>'.join(warnings) + '</p>') if warnings else '<p>容量の判定と未取得項目を確認して、次の実験を判断してください。</p>'
    window = report['inference']['window']
    raw = _e(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
    local_link = '<a href="#local-processes">ローカル学習サービス</a>' if report['schema_version'] >= 2 else ''
    storage_link = '<a href="#storage">保存領域</a>' if report['schema_version'] == 3 else ''
    coverage = ('固定3コンテナ、所有するローカル3サービス（JupyterLabのkernelを含む）、2ファイルシステム、24時間の宣言event'
                if report['schema_version'] >= 2 else '固定3サービス、2ファイルシステム、24時間の宣言event')
    if report['schema_version'] == 3: coverage += '、固定4workloadのPVC参照'
    return f'''<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><title>実験環境の容量と使用量 | Shirokuma</title><style>{STYLE}</style></head>
<body><header><div class="inner"><p class="brand">Shirokuma · Local experiment</p><h1>実験環境の容量と使用量</h1>
<p class="intro">保存済みスナップショットです。このページを開いても再取得しません。</p>
<div class="meta"><span>観測時刻 {_time(report['observed_at'])}</span><span class="status {'normal' if complete else 'warning'}">{status}</span></div>
<nav aria-label="レポート内の移動"><a href="#capacity">容量</a><a href="#components">サービス使用量</a>{local_link}{storage_link}<a href="#inference">モデルの記録</a><a href="#scope">観測範囲</a></nav></div></header>
<main><div class="notice {'warning' if warnings else ''}">{notice}</div>
<section id="capacity"><h2>空き容量</h2><p>ホストとVMは物理ストレージを共有します。2行は合算せず、それぞれのファイルシステムで判断してください。</p>{_disks(report)}
<p class="thresholds">空き率: 25%以下は注意 / 20%以下は残量低下 / 15%以下は要対応。判定には丸め前のbytesを使います。≈ は表示上の丸めです。</p></section>
<section id="components"><h2>サービス使用量</h2><p>CPU平均とメモリ（working set）の点観測です。高負荷の原因や増減傾向は判定しません。</p>{_components(report)}</section>
{_local_processes(report)}
{_storage(report)}
<section id="inference"><h2>モデルの記録</h2><p>{_time(window['start'])} 以上、{_time(window['end'])} 未満の24時間。</p>
<p>synthetic記録を含むAgentHouseの宣言値です。event数は実モデル呼出し数とは限りません。費用は請求額ではなく、通貨は未記録です。</p>{_inference(report)}</section>
<section id="scope"><h2>観測範囲</h2><p>全取得項目を観測できても、FinOps全体の対応完了を示しません。対象は{coverage}です。</p>
<p>PVC別・サービス別のストレージ帰属、電力・価格換算、idle判断は未対応です。自動停止・削除・budget変更は行いません。</p>
<details><summary>対象identity・原値・制約を確認する</summary><pre>{raw}</pre></details></section></main>
<footer>保存値の表示専用。CPU cores = nanocores / 1,000,000,000、MiB = bytes / 1,048,576。未知値を0へ補いません。</footer></body></html>'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try: html = render(parse_report(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)))
    except Exception:
        print(ERROR, file=sys.stderr)
        return 1
    sys.stdout.write(html + '\n')
    return 0


if __name__ == '__main__': raise SystemExit(main())
