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


def _canonical_bytes(report):
    # JSON preserves the spelling of signed floating zero after decoding even
    # though the two values compare equal. It must not create a second sample.
    def normalize(value):
        if type(value) is float and value == 0.0:
            return 0.0
        if type(value) is dict:
            return {key: normalize(item) for key, item in value.items()}
        if type(value) is list:
            return [normalize(item) for item in value]
        return value

    return json.dumps(normalize(report), ensure_ascii=True, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode()


def parse(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_INPUT_BYTES: raise HistoryError()

    def pairs(items):
        value = {}
        for key, item in items:
            if key in value: raise HistoryError()
            value[key] = item
        return value

    try:
        values = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_float=C.finite_json_float,
                            parse_constant=lambda _: (_ for _ in ()).throw(HistoryError()))
        if type(values) is not list or not 2 <= len(values) <= MAX_REPORTS: raise HistoryError()
        reports = []
        seen = set()
        for item in values:
            canonical = _canonical_bytes(item)
            if len(canonical) > C.MAX_PUBLIC_BYTES: raise HistoryError()
            C.validate_public(item)
            digest = hashlib.sha256(canonical).hexdigest()
            if digest in seen: raise HistoryError()
            seen.add(digest)
            reports.append((item, digest))
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
    if 'host_storage' in report:
        for item in report['host_storage']['components']:
            detail = ('所有digest <code>' + H._e(item['owner_sha256']) + '</code> · '
                      + H._e(item['directory']) + '<span class="sub">'
                      + H._time(item['request_started_at']) + ' → ' + H._time(item['observed_at']) + '</span>'
                      if item['state'] == 'observed' else '未取得')
            if 'usage_state' in item and item['usage_basis'] is not None:
                label = {'mlflow':'MLflow store','notebook-lab':'Workspace',
                         'model-serving':'Serving HOME','model-serving-tmp':'Serving TMP'}[item['component']]
                detail += (' · ' + label + ' logical regular-file bytes: ' + H._e(item['used_bytes'])
                           + ' · files ' + str(item['regular_file_count']) + ' / directories ' + str(item['directory_count'])
                           + ' · ' + H._time(item['usage_started_at']) + ' → ' + H._time(item['usage_observed_at'])
                           if item['usage_state'] == 'observed' else ' · 測定未取得')
            rows.append(f'<tr><th scope="row">{H.NAMES[item["component"]]} · ホスト保存先</th><td>{detail}</td></tr>')
    else:
        rows.append('<tr><th scope="row">ホスト保存先</th><td>このschemaには含まれません（ディレクトリ不在を意味しません）</td></tr>')
    return H._table('保存された対象identity。digest一致は期間中の連続稼働の証明ではありません。', ['対象', '保存identity / 範囲'], rows)


def _storage_coverage(entries):
    """Per-report scope only: no totals, deltas or claims between samples."""
    targets = (('docker','AgentHouse Docker volume'),('mlflow','MLflow store'),
               ('notebook-lab','Notebook Lab workspace'),('model-serving','Serving HOME'),
               ('model-serving-tmp','Serving TMP'))

    def cell(report, component):
        if component == 'docker':
            if 'docker_storage' not in report: return 'このschemaには含まれません'
            item=report['docker_storage']['agenthouse']
            return ('参照確認 / 使用量未計測（NOT_MEASURED）' if item['state']=='observed' else
                    '参照未取得 / 使用量未計測（NOT_MEASURED）'+H._reason(item['reason']))
        if 'host_storage' not in report: return 'このschemaには含まれません'
        item=next((row for row in report['host_storage']['components']
                   if row['component']==component),None)
        if item is None: return 'このschemaには含まれません'
        if item['state']=='unknown':
            return ('参照未取得 / '+('未計測（NOT_MEASURED）' if item.get('usage_basis') is None
                    else '論理bytes未取得') + H._reason(item['reason']))
        if item.get('usage_basis') is None: return '参照確認 / 未計測（NOT_MEASURED）'
        if item['usage_state']=='unknown':
            return '参照確認 / 論理bytes未取得'+H._reason(item['usage_reason'])
        return ('参照確認 / 論理 '+H._e(item['used_bytes'])+' bytes'
                +f'<span class="sub">通常ファイル {item["regular_file_count"]}件 / 子ディレクトリ {item["directory_count"]}件</span>')

    rows=[]
    for component,label in targets:
        rows.append('<tr><th scope="row">'+H._e(label)+'</th>'+''.join(
            '<td>'+cell(report,component)+'</td>' for _,(report,_) in entries)+'</tr>')
    headers=['対象']+[f'#{order} / v{pair[1][0]["schema_version"]}'
                    for order,pair in enumerate(entries,1)]
    return ('<section id="storage-coverage"><h2>保存領域の観測範囲</h2>'
            '<p>選択した各レポートの記録範囲だけを並べます。対象外・未取得・未計測と論理0 bytesは異なります。'
            '合計・増減・連続稼働・削除可能量は判定しません。</p>'
            +H._table('保存した各観測の固定保存領域の範囲。合計や差分ではありません。',headers,rows)
            +'</section>')


def render(entries):
    if type(entries) is not list or not 2 <= len(entries) <= MAX_REPORTS: raise HistoryError()
    # render() is also called directly by offline tooling. Bind each displayed
    # digest to its validated report before interpolating it into HTML.
    try:
        if any(type(entry) not in (tuple, list) or len(entry) != 2 or
               type(entry[1]) not in (tuple, list) or len(entry[1]) != 2
               for entry in entries): raise HistoryError()
        if {item[0] for item in entries} != set(range(len(entries))): raise HistoryError()
        previous = None
        seen = set()
        pinned = []
        for selected_index, (report, saved_digest) in entries:
            if type(selected_index) is not int or not 0 <= selected_index < len(entries): raise HistoryError()
            C.validate_public(report)
            order_key = (C.stamp(report['observed_at'])[1], selected_index)
            if previous is not None and order_key < previous: raise HistoryError()
            previous = order_key
            canonical = _canonical_bytes(report)
            if len(canonical) > C.MAX_PUBLIC_BYTES or type(saved_digest) is not str or \
                    saved_digest != hashlib.sha256(canonical).hexdigest(): raise HistoryError()
            if saved_digest in seen: raise HistoryError()
            seen.add(saved_digest)
            # Direct callers own mutable dicts. Render only detached bytes
            # bound to the checked digest, never those caller-owned objects.
            pinned.append((selected_index, (
                json.loads(canonical, parse_float=C.finite_json_float), saved_digest)))
    except (TypeError, ValueError, UnicodeError, OverflowError, KeyError, IndexError, RecursionError):
        raise HistoryError() from None
    entries = pinned
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
        host_storage = H._host_storage(report).replace('id="host-storage"', f'id="{anchor}-host-storage"')
        if report['schema_version'] < 3:
            note += '<p>このschemaにはPVC参照は含まれません（容量0や保存領域なしを意味しません）。</p>'
        if report['schema_version'] < 4:
            note += '<p>このschemaにはDocker volume参照は含まれません（volume不在を意味しません）。</p>'
        if report['schema_version'] < 5:
            note += '<p>このschemaにはホスト保存先参照は含まれません（ディレクトリ不在を意味しません）。</p>'
        sections.append(f'''<section id="{anchor}"><h2>保存観測 #{order}</h2>
<p>観測時刻 {H._time(report['observed_at'])} · schema v{report['schema_version']} · {status} · 選択入力 #{selected_index + 1}
<span class="sub">内容SHA-256 <code>{digest}</code>（真正性の証明ではありません）</span></p>
{note}<h3>対象identity</h3>{_identities(report)}
<h3>空き容量（当時の判定）</h3>{H._disks(report)}
<h3>Kubernetes主コンテナ</h3>{H._components(report)}
{local}
{storage}
{docker_storage}
{host_storage}
<h3>宣言model eventの24時間window</h3><p>{H._time(inference['window']['start'])} 以上、{H._time(inference['window']['end'])} 未満。
重複windowの件数を合算・差し引きしません。</p>{H._inference(report)}
</section>''')
    html = f'''<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<meta name="referrer" content="no-referrer"><title>保存FinOps観測履歴 | Shirokuma</title><style>{H.STYLE}</style></head>
<body><header><div class="inner"><p class="brand">Shirokuma · Local experiment</p><h1>保存FinOps観測履歴</h1>
<p class="intro">明示選択した保存値のみ。並びは観測時刻の昇順で、現在の稼働や期間中の変化を示しません。</p>
    <nav aria-label="保存観測へ移動"><a href="#storage-coverage">保存領域の範囲</a>{''.join(navigation)}</nav></div></header>
<main><div class="notice"><p>数値の差分・合計は算出しません。CPUは別々の短い観測区間です。RSSの共有ページとコンテナworking set、ホストとColimaの空き容量を合算しません。</p>
<p>disk助言は観測当時のもので現在の警報ではありません。宣言eventは実呼出し数とは限らず、費用見積りは通貨未記録です。</p></div>{_storage_coverage(entries)}{''.join(sections)}</main>
<footer>保存JSONの表示専用。自動収集・更新・停止・予算変更は行いません。見た目のブラウザ検証は別の受入です。</footer></body></html>'''
    if len(html.encode('utf-8')) > MAX_OUTPUT_BYTES: raise HistoryError()
    return html


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        html = render(parse(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)))
        # The CLI emits a trailing newline in addition to the direct renderer's
        # HTML; count the complete wire payload before writing any bytes.
        if len(html.encode('utf-8')) + 1 > MAX_OUTPUT_BYTES: raise HistoryError()
    except Exception:
        print(ERROR, file=sys.stderr)
        return 1
    sys.stdout.write(html + '\n')
    return 0


if __name__ == '__main__': raise SystemExit(main())
