#!/usr/bin/env python3
"""Render explicitly selected saved FinOps reports without collecting new data."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.finops import contract as C, render as H

ERROR = 'FINOPS_HISTORY_INVALID'
MAX_REPORTS = 8
MAX_INPUT_BYTES = MAX_REPORTS * C.MAX_PUBLIC_BYTES + 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024


class HistoryError(ValueError):
    pass


def parse(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_INPUT_BYTES: raise HistoryError()

    def pairs(items):
        value = {}
        for key, item in items:
            if key in value: raise HistoryError()
            value[key] = item
        return value

    try:
        values = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(HistoryError()))
        if type(values) is not list or not 2 <= len(values) <= MAX_REPORTS: raise HistoryError()
        reports = []
        for item in values:
            canonical = json.dumps(item, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
            if len(canonical) > C.MAX_PUBLIC_BYTES: raise HistoryError()
            C.validate_public(item)
            reports.append((item, hashlib.sha256(canonical).hexdigest()))
        return sorted(enumerate(reports), key=lambda pair: (C.stamp(pair[1][0]['observed_at'])[1], pair[0]))
    except Exception: raise HistoryError() from None


def _identities(report):
    rows = []
    for item in report['components']:
        identity = item['identity']
        detail = ('<code>' + H._e(identity['pod_uid']) + '</code> · container digest <code>'
                  + identity['container_id_sha256'] + '</code> · start ' + H._time(identity['started_at'])) if identity else '未取得'
        rows.append(f'<tr><th scope="row">{H.NAMES[item["component"]]}</th><td>{detail}</td></tr>')
    if 'local_processes' in report:
        for item in report['local_processes']['components']:
            identity = item['identity']
            detail = ('owned digest <code>' + identity['sha256'] + '</code> · '
                      + str(identity['process_count']) + ' processes') if identity else (
                      '停止中（数値なし）' if item['state'] == 'stopped' else '未取得（数値なし）')
            rows.append(f'<tr><th scope="row">{H.NAMES[item["component"]]}</th><td>{detail}</td></tr>')
    else:
        rows.append('<tr><th scope="row">ローカル学習サービス</th><td>このschemaには含まれません（停止・0使用量を意味しません）</td></tr>')
    if 'storage' in report:
        for item in report['storage']['components']:
            detail = ('workload <code>' + H._e(item['workload_uid']) + '</code>' if item['workload_uid'] else '未取得')
            if item['pvc']:
                detail += (' · PVC <code>' + H._e(item['pvc']['uid']) + '</code>'
                           + (' · PV <code>' + H._e(item['pv']['uid']) + '</code>' if item['pv'] else ' · PV未確認'))
            if item['request_started_at']:
                detail += '<span class="sub">' + H._time(item['request_started_at']) + ' → ' + H._time(item['observed_at']) + '</span>'
            label = {'polaris-postgresql':'Polaris PostgreSQL', **H.NAMES}[item['component']]
            rows.append(f'<tr><th scope="row">{label} · 保存領域</th><td>{detail}</td></tr>')
    else:
        rows.append('<tr><th scope="row">保存領域</th><td>このschemaには含まれません（容量0を意味しません）</td></tr>')
    if 'docker_storage' in report:
        item = report['docker_storage']['agenthouse']
        detail = ('container <code>' + H._e(item['container_id']) + '</code> · volume '
                  + H._e(item['volume_name']) + ' · created ' + H._time(item['volume_created_at'])
                  + '<span class="sub">' + H._time(item['request_started_at']) + ' → '
                  + H._time(item['observed_at']) + '</span>') if item['state'] == 'observed' else '未取得'
        rows.append(f'<tr><th scope="row">AgentHouse Docker volume</th><td>{detail}</td></tr>')
    else:
        rows.append('<tr><th scope="row">Docker volume</th><td>このschemaには含まれません（volume不在を意味しません）</td></tr>')
    return H._table('保存された対象identity。digest一致は期間中の連続稼働の証明ではありません。', ['対象', '保存identity / 範囲'], rows)


def render(entries):
    if type(entries) is not list or not 2 <= len(entries) <= MAX_REPORTS: raise HistoryError()
    sections, navigation = [], []
    for order, (selected_index, (report, digest)) in enumerate(entries, 1):
        C.validate_public(report)
        anchor = 'observation-' + str(order)
        navigation.append(f'<a href="#{anchor}">#{order} · {H._time(report["observed_at"])}</a>')
        inference = report['inference']
        status = '取得完了' if report['result'] == 'complete' else '一部未取得'
        note = ('<p>このschemaにはローカルprocess群は含まれません。停止・0使用量を意味しません。</p>'
                if report['schema_version'] == 1 else '')
        local = H._local_processes(report).replace('id="local-processes"', f'id="{anchor}-local-processes"')
        storage = H._storage(report).replace('id="storage"', f'id="{anchor}-storage"')
        docker_storage = H._docker_storage(report).replace('id="docker-storage"', f'id="{anchor}-docker-storage"')
        if report['schema_version'] < 3:
            note += '<p>このschemaにはPVC参照は含まれません（容量0や保存領域なしを意味しません）。</p>'
        if report['schema_version'] < 4:
            note += '<p>このschemaにはDocker volume参照は含まれません（volume不在を意味しません）。</p>'
        sections.append(f'''<section id="{anchor}"><h2>保存観測 #{order}</h2>
<p>観測時刻 {H._time(report['observed_at'])} · schema v{report['schema_version']} · {status} · 選択入力 #{selected_index + 1}
<span class="sub">内容SHA-256 <code>{digest}</code>（真正性の証明ではありません）</span></p>
{note}<h3>対象identity</h3>{_identities(report)}
<h3>空き容量（当時の判定）</h3>{H._disks(report)}
<h3>Kubernetes主コンテナ</h3>{H._components(report)}
{local}
{storage}
{docker_storage}
<h3>宣言model eventの24時間window</h3><p>{H._time(inference['window']['start'])} 以上、{H._time(inference['window']['end'])} 未満。
重複windowの件数を合算・差し引きしません。</p>{H._inference(report)}
</section>''')
    html = f'''<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><title>保存FinOps観測履歴 | Shirokuma</title><style>{H.STYLE}</style></head>
<body><header><div class="inner"><p class="brand">Shirokuma · Local experiment</p><h1>保存FinOps観測履歴</h1>
<p class="intro">明示選択した保存値のみ。並びは観測時刻の昇順で、現在の稼働や期間中の変化を示しません。</p>
<nav aria-label="保存観測へ移動">{''.join(navigation)}</nav></div></header>
<main><div class="notice"><p>数値の差分・合計は算出しません。CPUは別々の短い観測区間です。RSSの共有ページとコンテナworking set、ホストとColimaの空き容量を合算しません。</p>
<p>disk助言は観測当時のもので現在の警報ではありません。宣言eventは実呼出し数とは限らず、費用見積りは通貨未記録です。</p></div>{''.join(sections)}</main>
<footer>保存JSONの表示専用。自動収集・更新・停止・予算変更は行いません。見た目のブラウザ検証は別の受入です。</footer></body></html>'''
    if len(html.encode('utf-8')) > MAX_OUTPUT_BYTES: raise HistoryError()
    return html


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        html = render(parse(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)))
    except Exception:
        print(ERROR, file=sys.stderr)
        return 1
    sys.stdout.write(html + '\n')
    return 0


if __name__ == '__main__': raise SystemExit(main())
