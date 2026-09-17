#!/usr/bin/env python3
"""Compare the five fixed history panels with before/after reader SELECTs."""
import json
from pathlib import Path
import time
import uuid

import history_dashboard as H
import verify_dashboard as V

M = V.MANAGE


def targets(value):
    V.require(value.get('uid') == H.UID and value.get('templating', {}).get('list') == []
              and value.get('timepicker', {}).get('hidden') is True, 'HISTORY_DASHBOARD_SCOPE_DIFFERS')
    panels = [p for p in value.get('panels', []) if p.get('targets')]
    V.require([p.get('id') for p in panels] == list(range(2, 7)), 'HISTORY_PANEL_SET_DIFFERS')
    result = {}
    for key, panel in zip(H.LIMITS, panels):
        V.require(panel.get('datasource') == H.DATASOURCE and len(panel['targets']) == 1,
                  'HISTORY_DATASOURCE_DIFFERS')
        target = panel['targets'][0]
        V.require(target.get('rawSql') == H.QUERIES[key] and target.get('format') == 1
                  and target.get('pluginVersion') == M.PINS['plugin']['version'], 'HISTORY_QUERY_DIFFERS')
        result[key] = target
    return result


def run(output):
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    report = {'result': 'running', 'cases': [], 'pending': None, 'database_mutations': 0,
              'anonymous_grafana_queries': True}
    save = lambda: M.private_write(output / 'summary.json', report)
    try:
        save()
        M.check_context()
        V.require(M.status()['ready'], 'GRAFANA_NOT_READY')
        credential = M.credentials()
        V.require(set(M.verify_reader(credential)['logical_rows']) == set(M.HISTORY_READER_TABLES),
                  'HISTORY_READER_SCOPE_DIFFERS')
        path = Path(__file__).resolve().parent / 'dashboards/history.json'
        staged = H.OUTPUT.read_bytes()
        V.require(path.read_bytes() == staged and json.loads(staged) == H.dashboard(), 'HISTORY_LOCAL_INPUT_DIFFERS')
        wanted = targets(json.loads(staged))
        live = json.loads(M.request(M.URL + '/api/dashboards/uid/' + H.UID))
        V.require(live.get('meta', {}).get('provisioned') is True and targets(live['dashboard']) == wanted,
                  'HISTORY_LIVE_TARGETS_DIFFER')
        before_hash = V.digest(staged)
        now = int(time.time() * 1000)
        for key, target in wanted.items():
            report['pending'] = key
            save()
            sql = H.QUERIES[key]
            V.require(sql.startswith('SELECT ') and ';' not in sql and '$' not in sql
                      and sql.endswith('LIMIT ' + str(H.LIMITS[key])), 'HISTORY_SQL_SCOPE_DIFFERS')
            read = lambda: V.reference_table(json.loads(M.reader_query(credential, sql + '\nFORMAT JSON')), H.LIMITS[key])
            before = read()
            body = {'queries': [{**target, 'intervalMs': 60000, 'maxDataPoints': H.LIMITS[key]}],
                    'from': str(now - 86400000), 'to': str(now)}
            reply = json.loads(M.request(M.URL + '/api/ds/query', data=json.dumps(body).encode()))
            actual = V.frame_table(reply, before, H.LIMITS[key])
            after = read()
            V.require(before == actual == after, 'HISTORY_PANEL_VALUES_DIFFER')
            if key == 'auxiliary_reads':
                rows = [dict(zip(actual['columns'], row)) for row in actual['rows']]
                V.require(all(row['history_status'] == 'not_target' and row['created_to_end_ms'] is None for row in rows),
                          'AUXILIARY_MEASUREMENTS_INVENTED')
            M.private_write(output / (key + '.json'), {'before': before, 'grafana': actual, 'after': after})
            report['cases'].append({'panel': key, 'rows': len(actual['rows']), 'columns': actual['columns'],
                                    'values_sha256': V.digest(actual), 'matched': True})
            report['pending'] = None
            save()
        V.require(V.digest(path.read_bytes()) == before_hash and V.digest(H.OUTPUT.read_bytes()) == before_hash,
                  'HISTORY_LOCAL_INPUT_CHANGED')
        live = json.loads(M.request(M.URL + '/api/dashboards/uid/' + H.UID))
        V.require(targets(live['dashboard']) == wanted, 'HISTORY_LIVE_INPUT_CHANGED')
        report.update(result='passed', dashboard_sha256=before_hash, panel_count=len(wanted))
        save()
        return report
    except BaseException as error:
        report.update(result='failed', failure_code=str(error) if isinstance(error, V.VerificationError) else 'HISTORY_VERIFICATION_FAILED')
        save()
        raise


def main():
    token = uuid.uuid4().hex
    output = M.ROOT / 'tmp/local-experiment/grafana-history-verification' / token
    try:
        run(output)
        print(json.dumps({'result': 'passed', 'run_token': token, 'panels': 5}))
        return 0
    except BaseException:
        print(json.dumps({'result': 'failed', 'run_token': token}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
