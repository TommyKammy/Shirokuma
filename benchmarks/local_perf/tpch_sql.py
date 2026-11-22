"""Pinned TPC-H portability and snapshot SQL, with full result provenance."""
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import time

from .capacity import write_json
from .engine import bounded, connect, ident, quote
from .results import Writer, column, compare

TABLES = ('customer', 'lineitem', 'nation', 'orders', 'part', 'partsupp', 'region', 'supplier')


def digest(text):
    return hashlib.sha256(text.encode('utf8')).hexdigest()


def contracts():
    entries = json.loads(Path(__file__).with_name('tpch_contracts.json').read_text())['queries']
    if len(entries) != 22 or {q['id'] for q in entries} != {str(i) for i in range(1, 23)}:
        raise ValueError('all 22 contracts required')
    for q in entries:
        if not re.fullmatch(r'[0-9a-f]{64}', q['source_sql_sha256']):
            raise ValueError('invalid source hash')
        if not q['columns'] or any(column(c['name'], c['type']) != c for c in q['columns']):
            raise ValueError('invalid output contract')
        seen = set()
        for o in q['order_by']:
            if (set(o) != {'index', 'descending', 'nulls'} or type(o['index']) is not int
                    or not 0 <= o['index'] < len(q['columns']) or o['index'] in seen
                    or type(o['descending']) is not bool or o['nulls'] != 'last'):
                raise ValueError('invalid ordering contract')
            seen.add(o['index'])
        if q['limit'] is not None and (type(q['limit']) is not int or q['limit'] <= 0 or not seen):
            raise ValueError('invalid limit contract')
    return {q['id']: q for q in entries}


def replace(sql, old, new, count=1):
    if sql.count(old) != count:
        raise ValueError('pinned SQL rewrite occurrence mismatch')
    return sql.replace(old, new)


def projection(sql, rule):
    aliases = ', '.join('c' + str(i) for i in range(len(rule['columns'])))
    fields = ', '.join(f"CAST(c{i} AS {c['type']}) AS {ident(c['name'])}"
                       for i, c in enumerate(rule['columns']))
    result = f"SELECT {fields} FROM ({sql.strip().removesuffix(';')}) AS result({aliases})"
    if rule['order_by']:
        result += ' ORDER BY ' + ', '.join(str(o['index'] + 1) + (' DESC' if o['descending'] else ' ASC')
                                           + ' NULLS LAST' for o in rule['order_by'])
    return result


def portable(sql, rule):
    """Limited transformations only after checking the pinned canonical hash."""
    if digest(sql) != rule['source_sql_sha256']:
        raise ValueError('unexpected canonical source SQL')
    nr = rule['id']
    if nr in ('1', '17', '22'):
        fields = {'1': ('l_quantity', 'l_extendedprice', 'l_discount'),
                  '17': ('l_quantity',), '22': ('c_acctbal',)}[nr]
        for field in fields:
            sql = replace(sql, f'avg({field})', f'avg(CAST({field} AS DOUBLE))')
    if nr == '8':
        sql = replace(sql, '    sum(\n', '    CAST(sum(\n')
        sql = replace(sql, 'END) / sum(volume)', 'END) AS DOUBLE) / CAST(sum(volume) AS DOUBLE)')
    if nr == '14':
        sql = replace(sql, '100.00 * sum(', 'CAST(100.00 * sum(')
        sql = replace(sql, 'END) / sum(l_extendedprice * (1 - l_discount))',
                      'END) AS DOUBLE) / CAST(sum(l_extendedprice * (1 - l_discount)) AS DOUBLE)')
    if nr == '17':
        sql = replace(sql, 'sum(l_extendedprice) / 7.0', 'CAST(sum(l_extendedprice) AS DOUBLE) / 7.0')
    if nr == '15':
        sql = replace(sql, 'GROUP BY\n        supplier_no', 'GROUP BY\n        l_suppkey')
    if nr == '12':
        sql = replace(sql, '    sum(\n', '    CAST(sum(\n', 2)
        for label in ('high_line_count', 'low_line_count'):
            sql = replace(sql, 'END) AS ' + label, 'END) AS BIGINT) AS ' + label)
    return projection(sql, rule)


def logical_manifest(source):
    rules = contracts()
    queries = source['queries']
    if (source.get('format') != 1 or not isinstance(source.get('dataset_id'), str)
            or len(queries) != 22 or {q['id'] for q in queries} != set(rules)):
        raise ValueError('complete source manifest required')
    result = dict(format=1, dataset_id=source['dataset_id'], source_dataset_id=source['dataset_id'], queries=[])
    for q in queries:
        rule = rules[q['id']]
        if q.get('sql_sha256') != digest(q['sql']):
            raise ValueError('source SQL fingerprint mismatch')
        sql = portable(q['sql'], rule)
        result['queries'].append(dict(id=q['id'], source_sql=q['sql'], source_sha256=digest(q['sql']),
            logical_sql=sql, logical_sha256=digest(sql), reference_sql=sql, reference_sha256=digest(sql),
            sql=sql, sql_sha256=digest(sql), ordered=False, columns=copy.deepcopy(rule['columns']),
            order_by=copy.deepcopy(rule['order_by']), limit=rule['limit'],
            rewrite_provenance=dict(source='DuckDB 1.4.3 tpch_queries', reasons=rule['rewrite_reasons'],
                                    output='explicit ordinal casts and outer ORDER BY; BIGINT range checked')))
    result['source_hashes'] = {q['id']: q['source_sha256'] for q in result['queries']}
    return result


def _stream(con, sql, path, columns, metadata, check):
    cursor = con.execute(sql)
    native = [column(d[0], str(d[1])) for d in cursor.description]
    if len(native) != len(columns) or any(a['type'] != b['type'] and
            (a['type'], b['type']) != ('hugeint', 'bigint') for a, b in zip(native, columns)):
        raise ValueError('unexpected native output types')
    writer = Writer(path, columns, metadata)
    rows = 0
    try:
        while True:
            batch = cursor.fetchmany(2048)
            check()
            if not batch:
                break
            for row in batch:
                writer.row(row)  # Includes signed-64 range and exact DECIMAL validation.
                rows += 1
        writer.finish()
    finally:
        writer.close()
    return dict(executed_sql_sha256=digest(sql), native_columns=native, rows=rows, file=path.name,
                ordinal_binding=[dict(index=i, source=a, canonical=b) for i, (a, b) in enumerate(zip(native, columns))])


def audit_limit(con, sql, rule):
    n = rule['limit']
    if n is None:
        return None
    expanded = replace(sql.strip().removesuffix(';'), f'LIMIT {n}', f'LIMIT {n + 1}')
    rows = con.execute(expanded).fetchmany(n + 1)
    indices = [o['index'] for o in rule['order_by']]
    if len(rows) > n and all(rows[n - 1][i] == rows[n][i] for i in indices):
        raise ValueError('LIMIT boundary tie requires dataset re-audit')
    return dict(limit=n, observed_rows=len(rows), boundary_tie=False, executed_sql_sha256=digest(expanded))


def prepare_tpch(campaign, dataset, name, timeout=120, abs_tol=1e-8, rel_tol=1e-8):
    """Prove source/cast/portable equivalence using owned disk-backed results."""
    if any(not math.isfinite(v) or v < 0 for v in (abs_tol, rel_tol)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('invalid deadline/tolerance')
    from .runner import dataset_read, dataset_version
    dataset = Path(dataset).resolve()
    dataset_text = (dataset / "dataset.json").read_text()
    meta = json.loads(dataset_text)
    version = dataset_version(meta)
    source_text = (dataset / 'queries.json').read_text()
    source = json.loads(source_text)
    if (meta['status'] != 'SUCCESS' or meta['suite'] != 'tpch' or meta['generator_version'] != '1.4.3'
            or meta['dataset_id'] != source['dataset_id']
            or len(meta['tables']) != 8 or {t['name'] for t in meta['tables']} != set(TABLES)):
        raise ValueError('successful DuckDB 1.4.3 TPC-H dataset required')
    manifest = logical_manifest(source)
    manifest['source_manifest_sha256'] = digest(source_text)
    rules = contracts()
    campaign.check()
    out = campaign.own(name, 'tpch-plan')
    record = dict(format=1, status='INCOMPLETE', source_dataset_id=source['dataset_id'],
                  source_manifest_sha256=digest(source_text), abs_tol=abs_tol, rel_tol=rel_tol,
                  equivalence='paired source/portable SQL; same-engine full multiset, not independent oracle', queries=[])
    write_json(out / 'plan.json', record)
    write_json(out / 'queries.json', manifest)
    deadline = time.monotonic() + timeout
    con = None

    def check():
        if time.monotonic() >= deadline:
            raise TimeoutError('TPC-H plan deadline')
        campaign.check()

    try:
        con = connect(out / 'parity.duckdb', out / 'spill')
        with bounded(con, check, deadline):
            for t in meta['tables']:
                if version == 1:
                    file = (dataset / t['file']).resolve()
                    if file.parent != dataset.resolve():
                        raise ValueError('dataset table must be a direct child')
                    con.execute("CREATE VIEW " + ident(t["name"]) + " AS SELECT * FROM read_parquet(" + quote(file) + ")")
                else:
                    con.execute("CREATE VIEW " + ident(t["name"]) + " AS " + dataset_read(con, dataset, t, version, check))
            for q in manifest['queries']:
                check()
                rule = rules[q['id']]
                pair = dict(source_hash=q['source_sha256'], portable_hash=q['logical_sha256'],
                            source_dataset_id=source['dataset_id'])
                audit = dict(id=q['id'], status='INCOMPLETE', equivalence_contract=pair)
                record['queries'].append(audit)
                audit['limit_audit'] = audit_limit(con, q['source_sql'], rule)
                variants = {'source': q['source_sql'], 'source_cast': projection(q['source_sql'], rule), 'portable': q['sql']}
                for label, sql in variants.items():
                    path = out / (q['id'] + '-' + label + '.jsonl')
                    audit[label] = _stream(con, sql, path, q['columns'], pair, check)
                for label in ('source_cast', 'portable'):
                    compare(out / audit['source']['file'], out / audit[label]['file'], ordered=False,
                            abs_tol=abs_tol, rel_tol=rel_tol, scratch=out, check=check)
                audit['status'] = 'SUCCESS'
                write_json(out / 'plan.json', record)
        if ((dataset / "dataset.json").read_text() != dataset_text
                or (dataset / "queries.json").read_text() != source_text):
            raise ValueError("source manifest changed")
        record['status'] = 'SUCCESS'
        campaign.mark(out, 'SUCCESS')
        return out
    except BaseException as exc:
        record.update(status='FAILED', error_type=type(exc).__name__)
        campaign.mark(out, 'FAILED')
        raise
    finally:
        if con is not None:
            con.close()
        for path in (out / 'parity.duckdb', out / 'parity.duckdb.wal', out / 'spill'):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        write_json(out / 'plan.json', record)


def _safe(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value):
        raise ValueError('simple SQL identifier required')
    return value


def bind_snapshots(manifest, pins):
    """Bind canonical relations through CTEs without rewriting scalar identifiers."""
    from .public import context
    ctx = context(pins['context'])
    catalog, schema = _safe(ctx['sql_catalog']), _safe(ctx['sql_schema'])
    tables = pins['tables']
    if (pins.get('format') != 1 or pins.get('dataset_id') != manifest.get('source_dataset_id')
            or len(tables) != 8 or {t['name'] for t in tables} != set(TABLES)):
        raise ValueError('exact source dataset and eight snapshot tables required')
    if manifest.get('context') is not None and manifest['context'] != ctx:
        raise ValueError('snapshot context mismatch')
    ctes, physical = [], set()
    for t in sorted(tables, key=lambda t: t['name']):
        name = _safe(t['physical_name'])
        if name in physical or name in TABLES:
            raise ValueError('unique physical prefix table names required')
        physical.add(name)
        sid = t['snapshot_id']
        if not isinstance(sid, str) or not re.fullmatch(r'[0-9]+', sid):
            raise ValueError('ASCII decimal snapshot ID required')
        ref = t['ref']
        if (ref.get('database_id') != ctx['database_id'] or ref.get('schema_id') != ctx['schema_id']
                or not isinstance(t['table_uuid'], str) or not t['table_uuid']):
            raise ValueError('snapshot database/schema identity mismatch')
        for key, expected in (('sql_catalog', catalog), ('sql_schema', schema)):
            if key in ref and ref[key] != expected:
                raise ValueError('snapshot SQL context mismatch')
        ctes.append(ident(t['name']) + ' AS (SELECT * FROM ' + '.'.join(map(ident, (catalog, schema, name)))
                    + ' FOR VERSION AS OF ' + sid + ')')
    stable = copy.deepcopy(pins)
    stable['tables'] = sorted(stable['tables'], key=lambda t: t['name'])
    snapshot_hash = digest(json.dumps(stable, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False))
    result = copy.deepcopy(manifest)
    result.update(context=ctx, snapshot_set_sha256=snapshot_hash, snapshot_pins=stable,
                  dataset_id=manifest['source_dataset_id'] + '/snapshot/' + snapshot_hash)
    rules = contracts()
    if len(result['queries']) != 22 or {q['id'] for q in result['queries']} != set(rules):
        raise ValueError('all 22 logical queries required')
    for q in result['queries']:
        expected = portable(q['source_sql'], rules[q['id']])
        if (q['logical_sql'] != expected or q['logical_sha256'] != digest(expected)
                or q['source_sha256'] != digest(q['source_sql'])
                or q['columns'] != rules[q['id']]['columns'] or q['order_by'] != rules[q['id']]['order_by']
                or q['limit'] != rules[q['id']]['limit'] or q['ordered'] is not False):
            raise ValueError('logical/source SQL fingerprint mismatch')
        q.update(reference_sql=expected, reference_sha256=digest(expected),
                 sql='WITH ' + ',\n'.join(ctes) + '\n' + expected)
        q['sql_sha256'] = q['target_sha256'] = digest(q['sql'])
    return result
