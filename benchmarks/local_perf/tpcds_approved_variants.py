"""Pinned old Spark-derived alternatives; every dataset needs a raw Spark oracle.

SQL bodies are supplied privately. Decimal rounding adaptation is empirical,
not a universal equivalence proof or current TPC template identity.
"""
import copy
import hashlib
import json
import math
from pathlib import Path
import time

from . import tpcds_sql as ds
from .results import Reader, Writer, column, compare, decoded, validate_order_contract

IDS = ('5a', '10a', '14a', '18a', '22a', '27a', '35a', '36a', '51a', '67a', '70a', '77a', '80a', '86a')
SUITE = 'TPC-derived approved alternatives: Spark v4.0.1 tpcds-v2.7.0 pinned 14; per-dataset Spark qualification required'
SOURCE = dict(repository='https://github.com/apache/spark', release='v4.0.1',
              commit='29434ea766b0fc3c3bf6eaadb43a8f931133649e', resource_version='tpcds-v2.7.0',
              resource_pattern='sql/core/src/test/resources/tpcds-v2.7.0/q<ID>.sql')
OVERRIDES = {i: dict(op='case_equal', condition=3, value=0, then=1) for i in ('36a', '70a', '86a')}


def fingerprint(value):
    return ds.digest(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False))


def metadata():
    doc = json.loads(Path(__file__).with_name('tpcds_approved_variants_metadata.json').read_text())
    if (type(doc.get('format')) is not int or doc.get('format') != 1 or doc.get('suite') != SUITE or doc.get('provenance') != SOURCE
            or doc.get('input_schema_sha256') != fingerprint(ds.table_schemas())
            or len(doc['queries']) != 14 or [q['id'] for q in doc['queries']] != list(IDS)
            or doc.get('current_v4_template_identity') is not False
            or doc.get('universal_algebraic_equivalence') is not False):
        raise ValueError('exact approved alternative contracts required')
    for q in doc['queries']:
        validate_order_contract(q['columns'], q['order_by'])
        if (len({c['name'] for c in q['columns']}) != len(q['columns'])
                or any(column(c['name'], c['type']) != c for c in q['columns'])
                or any(not isinstance(q[k], str) or len(q[k]) != 64 or
                       any(c not in '0123456789abcdef' for c in q[k]) for k in
                       ('raw_source_sha256', 'normalized_body_sha256', 'effective_body_sha256',
                        'source_sql_sha256', 'effective_sql_sha256', 'cast_sql_sha256', 'portable_sql_sha256', 'portable_body_sha256'))):
            raise ValueError('invalid approved structural contract')
    return doc


def read_sources(source_dir, check=lambda: None):
    check()
    root = Path(source_dir).resolve()
    files = []
    if root.is_dir():
        for path in root.rglob('*'):
            check()
            if path.is_file() and path.suffix.lower() == '.sql':
                files.append(path)
    if len(files) != 14 or {p.name for p in files} != {'q'+i+'.sql' for i in IDS} or any(p.parent != root for p in files):
        raise ValueError('source-dir requires exactly fourteen direct q<ID>.sql files')
    rules = {q['id']: q for q in metadata()['queries']}
    sources = {}
    for i in IDS:
        check()
        raw = (root / ('q'+i+'.sql')).read_bytes()
        check()
        if hashlib.sha256(raw).hexdigest() != rules[i]['raw_source_sha256']:
            raise ValueError('pinned source bytes mismatch: '+i)
        sources[i] = raw.decode('utf8')
    return sources


def normalize(source, qid):
    """ANSI fixes plus narrowly reviewed intermediate decimal rounding."""
    import sqlglot
    from sqlglot import exp
    if sqlglot.__version__ != '30.31.0':
        raise RuntimeError('requires isolated SQLGlot 30.31.0')
    trees = sqlglot.parse(source, read='spark')
    if len(trees) != 1 or not isinstance(trees[0], exp.Query):
        raise ValueError('single Spark query required')
    tree = trees[0]
    counts = {kind: len(list(tree.find_all(cls))) for kind, cls in
              [('try_cast', exp.TryCast), ('avg', exp.Avg), ('div', exp.Div), ('order', exp.Order),
               ('ordered', exp.Ordered), ('union', exp.Union), ('window', exp.Window)]}
    for node in list(tree.find_all(exp.TryCast)):
        node.replace(exp.Cast(**node.args))
    for node in tree.find_all(exp.Div):
        node.set('safe', False)
    normalized = tree.sql(dialect='duckdb', comments=False)
    changes = dict(decimal_avg_cast=0, decimal_division_cast=0)
    for node in list(tree.find_all(exp.Avg)):
        typ = None
        if qid == '18a':
            typ = 'DECIMAL(16,6)'
        elif qid == '27a' and node.this.name.lower() in ('agg2', 'agg3', 'agg4'):
            typ = 'DECIMAL(11,6)'
        if typ:
            node.replace(exp.Cast(this=node.copy(), to=exp.DataType.build(typ)))
            changes['decimal_avg_cast'] += 1
    if qid == '36a':
        for node in list(tree.find_all(exp.Div)):
            node.replace(exp.Cast(this=node.copy(), to=exp.DataType.build('DECIMAL(38,11)')))
            changes['decimal_division_cast'] += 1
    expected = (35, 0) if qid == '18a' else (9, 0) if qid == '27a' else (0, 3) if qid == '36a' else (0, 0)
    if tuple(changes.values()) != expected:
        raise ValueError('reviewed intermediate AST counts mismatch: '+qid)
    return normalized, tree.sql(dialect='duckdb', comments=False), counts, changes


def compile_sources(sources, doc, check, deadline):
    import duckdb
    if duckdb.__version__ != '1.4.3':
        raise RuntimeError('requires isolated DuckDB 1.4.3')
    schemas = ds.table_schemas()
    con = duckdb.connect(':memory:')
    bodies, inventory, facts = {}, [], {}
    try:
        with ds.bounded(con, check, deadline):
            for table, cols in schemas.items():
                check()
                con.execute('CREATE TABLE '+ds.ident(table)+' ('+', '.join(ds.ident(c['name'])+' '+c['type'] for c in cols)+')')
            for i in IDS:
                check()
                normalized, body, counts, changes = normalize(sources[i], i)
                desc = [(d[0], str(d[1])) for d in con.execute('DESCRIBE '+body).fetchall()]
                bodies[i] = body
                facts[i] = dict(raw_source_sha256=ds.digest(sources[i]), normalized_body_sha256=ds.digest(normalized),
                                effective_body_sha256=ds.digest(body), source_ast_counts=counts, transformations=changes,
                                effective_native_schema=[column(n, t) for n, t in desc])
                inventory.append(dict(id=i, sql=body, description=desc))
        generated = ds.generate_contracts(inventory, {t: {c['name']: c['type'] for c in cols} for t, cols in schemas.items()}, OVERRIDES)
        check()
        rules, entries = {}, []
        for rule, static in zip(generated['queries'], doc['queries']):
            i = rule['id']
            # Canonical Spark names and dtypes are ordinal; native DDB kept above.
            rule['columns'] = copy.deepcopy(static['columns'])
            body = rule['portable_body']
            rule['rewrite_provenance']['source'] = 'Apache Spark v4.0.1 tpcds-v2.7.0; raw Spark 4.1.3 ANSI=true oracle'
            effective, portable = ds.projection(bodies[i], rule), ds.projection(body, rule)
            actual = dict(id=i, **facts[i], columns=rule['columns'], order_by=rule['order_by'], limit=rule['limit'],
                          rewrite_provenance=rule['rewrite_provenance'], source_sql_sha256=ds.digest(effective),
                          effective_sql_sha256=ds.digest(effective), cast_sql_sha256=ds.digest(effective), portable_sql_sha256=ds.digest(portable), portable_body_sha256=ds.digest(body),
                          spark_dtype_evidence=static['spark_dtype_evidence'])
            if actual != static:
                raise ValueError('regenerated approved contract differs: '+i)
            rules[i] = rule
            entries.append(_entry(i, effective, portable, static))
        return entries, rules, bodies
    finally:
        con.close()


def _entry(i, source, portable, rule):
    return dict(id=i, source_sql=source, source_sha256=ds.digest(source), logical_sql=portable,
                logical_sha256=ds.digest(portable), reference_sql=portable, reference_sha256=ds.digest(portable),
                sql=portable, sql_sha256=ds.digest(portable), ordered=False,
                **{k: copy.deepcopy(rule[k]) for k in ('columns', 'order_by', 'limit', 'rewrite_provenance')},
                source_normalization=copy.deepcopy(rule))


def _validate_structure(manifest):
    """Static SQL and lineage validation; no optional SDK imports."""
    doc = metadata()
    if (manifest.get('suite') != SUITE or type(manifest.get('format')) is not int or manifest.get('format') != 1
            or not isinstance(manifest.get('source_dataset_id'), str) or not manifest['source_dataset_id']
            or manifest.get('dataset_id') != manifest['source_dataset_id']
            or manifest.get('table_schemas') != ds.table_schemas() or manifest.get('provenance') != SOURCE
            or len(manifest.get('queries', [])) != 14 or [q['id'] for q in manifest['queries']] != list(IDS)):
        raise ValueError('exact separate approved suite required')
    for q, rule in zip(manifest['queries'], doc['queries']):
        if ds.digest(q['source_sql']) != rule['source_sql_sha256'] or ds.digest(q['logical_sql']) != rule['portable_sql_sha256']:
            raise ValueError('approved SQL pin mismatch')
        if q != _entry(q['id'], q['source_sql'], q['logical_sql'], rule):
            raise ValueError('approved entry/lineage mismatch')
    if manifest.get('source_hashes') != {q['id']: q['source_sha256'] for q in manifest['queries']}:
        raise ValueError('approved source hashes mismatch')
    proof = manifest.get('spark_qualification', {})
    if (proof.get('status') not in ('INCOMPLETE', 'SUCCESS')
            or proof.get('dataset_identity', {}).get('dataset_id') != manifest['source_dataset_id']
            or proof.get('dataset_identity', {}).get('queries_manifest_sha256') != manifest.get('source_manifest_sha256')
            or proof.get('profile') != doc['oracle_profile'] or proof.get('engine_version') != '4.1.3'
            or proof.get('raw_source_hashes') != {q['id']: q['raw_source_sha256'] for q in doc['queries']}
            or not isinstance(proof.get('report_sha256'), str) or len(proof['report_sha256']) != 64):
        raise ValueError('approved Spark qualification mismatch')
    return copy.deepcopy(manifest)


def _body(manifest):
    body = copy.deepcopy(manifest)
    body.pop('acceptance', None)
    return body


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _load(path):
    return json.loads(Path(path).read_text())


def _evidence(manifest, locations, *, frozen=True, check=lambda: None):
    """Reread materialized qualification; deliberately uses only stdlib helpers."""
    from .spark_reference import dataset_identity, validate_reference
    _validate_structure(manifest)
    if manifest['spark_qualification']['status'] != 'SUCCESS':
        raise ValueError('accepted qualification required')
    out = Path(locations['out_dir']).resolve()
    campaign = Path(locations['campaign_dir']).resolve()
    if out.parent != campaign:
        raise ValueError('owning plan directory mismatch')
    owner = _load(campaign / 'campaign.json')
    entry = owner['owned'][out.name]
    plan_path = out / 'plan.json'
    plan = _load(plan_path)
    if (owner.get('root') != str(campaign) or owner.get('format') != 1
            or entry.get('kind') != 'tpcds-plan' or entry.get('status') != 'SUCCESS'
            or plan.get('status') != 'SUCCESS' or plan.get('suite') != SUITE
            or plan.get('query_count') != 14 or len(plan.get('queries', [])) != 14
            or [a['id'] for a in plan['queries']] != list(IDS)
            or plan.get('dataset_qualified') is not True
            or plan.get('qualification') != manifest['spark_qualification']
            or plan.get('source_dataset_id') != manifest['source_dataset_id']
            or plan.get('source_manifest_sha256') != manifest['source_manifest_sha256']):
        raise ValueError('successful owned fourteen-query plan required')
    for key in ('abs_tol', 'rel_tol'):
        if not math.isfinite(plan[key]) or not 0 <= plan[key] <= 1e-8:
            raise ValueError('invalid accepted tolerance')
    if frozen and _load(out / 'queries.json') != manifest:
        raise ValueError('frozen qualified manifest mismatch')
    check()
    sources = read_sources(locations['source_dir'], check)
    identity = dataset_identity(locations['dataset_dir'], check=check)
    qualification = manifest['spark_qualification']
    if identity != qualification['dataset_identity']:
        raise ValueError('qualification dataset changed')
    raw_hashes = {i: ds.digest(sources[i]) for i in IDS}
    proof = validate_reference(locations['reference_dir'], identity, raw_hashes,
                              expected_columns={q['id']: q['columns'] for q in manifest['queries']}, check=check)
    if proof['report_sha256'] != qualification['report_sha256']:
        raise ValueError('qualification reference changed')
    artifacts = {}
    # Compare uses temporary SQLite files; never write into frozen evidence.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='approved-revalidate-') as scratch:
        for q, audit in zip(manifest['queries'], plan['queries']):
            check()
            pair = dict(source_hash=q['source_sha256'], portable_hash=q['logical_sha256'],
                        source_dataset_id=manifest['source_dataset_id'])
            result = proof['results'][q['id']]
            expected_external = dict(engine='Apache Spark 4.1.3 raw source', raw_sha256=raw_hashes[q['id']],
                result_sha256=result['sha256'], report_sha256=proof['report_sha256'], rows=result['rows'],
                comparisons=['source', 'source_cast', 'portable'], source_cast_same_sql=True,
                independent_source_cast_proof=False)
            if (audit.get('status') != 'SUCCESS' or audit.get('spark_reference') != expected_external
                    or audit.get('equivalence_contract') != pair
                    or audit.get('source_normalization') != q['source_normalization']):
                raise ValueError('incomplete external comparison audit')
            paths = {}
            artifacts[q['id']] = {}
            for label in ('source', 'source_cast', 'portable', 'spark-ordinal'):
                path = out / (q['id']+'-'+label+'.jsonl')
                sql_hash = q['logical_sha256'] if label == 'portable' else q['source_sha256']
                if label != 'spark-ordinal':
                    item = audit[label]
                    if (item.get('file') != path.name or item.get('executed_sql_sha256') != sql_hash
                            or item.get('rows') != result['rows']):
                        raise ValueError('result SQL/rows identity mismatch')
                reader = Reader(path)
                try:
                    header = reader.header
                    if header['columns'] != q['columns'] or header['metadata'] != pair:
                        raise ValueError('qualified result header mismatch')
                    rows = sum(1 for _ in reader)
                finally:
                    reader.f.close()
                if rows != result['rows'] or (q['limit'] is not None and rows > q['limit']):
                    raise ValueError('qualified result rows mismatch')
                ds.validate_query_order(path, q, check)
                paths[label] = path
                artifacts[q['id']][label] = dict(sha256=_hash(path), rows=rows, header=header,
                    sql_sha256=raw_hashes[q['id']] if label == 'spark-ordinal' else sql_hash)
            # Raw Spark headers differ; compare through a freshly verified ordinal copy.
            raw = Reader(result['path'])
            neutral = Path(scratch) / (q['id']+'-raw.jsonl')
            writer = Writer(neutral, q['columns'], pair)
            try:
                for row in raw:
                    check()
                    writer.row([decoded(v) for v in row])
                writer.finish()
            finally:
                raw.f.close()
                writer.f.close()
            if _hash(neutral) != _hash(paths['spark-ordinal']):
                raise ValueError('ordinal copy differs from raw Spark')
            for label in ('source', 'source_cast', 'portable'):
                compare(neutral, paths[label], ordered=False, abs_tol=plan['abs_tol'],
                        rel_tol=plan['rel_tol'], scratch=scratch, check=check)
    check()
    return dict(format=1, kind='tpcds-plan', status='SUCCESS', locations=locations,
        manifest_body_sha256=fingerprint(_body(manifest)), plan_sha256=_hash(plan_path),
        campaign_entry=entry, dataset_identity=identity, raw_source_hashes=raw_hashes,
        report_sha256=proof['report_sha256'], profile=qualification['profile'],
        reference_results={i: {k: v for k, v in r.items() if k != 'path'} for i, r in proof['results'].items()},
        artifacts=artifacts)


def validate_manifest(manifest):
    """Accept only current, materialized SUCCESS evidence; no optional SDKs."""
    _validate_structure(manifest)
    locator = manifest.get('acceptance', {})
    if set(locator) != {'path', 'sha256'}:
        raise ValueError('materialized acceptance required')
    path = Path(locator['path']).resolve()
    if _hash(path) != locator['sha256']:
        raise ValueError('acceptance digest mismatch')
    accepted = _load(path)
    if path != Path(accepted['locations']['out_dir']).resolve() / 'acceptance.json':
        raise ValueError('acceptance owner mismatch')
    if _evidence(manifest, accepted['locations']) != accepted:
        raise ValueError('qualification evidence changed')
    return copy.deepcopy(manifest)


def validate_bound_manifest(manifest):
    locator = manifest.get('acceptance', {})
    accepted = _load(locator['path'])
    unbound = _load(Path(accepted['locations']['out_dir']) / 'queries.json')
    # Strict binding performs accepted validation before constructing the wrapper.
    expected = ds.bind_snapshots(unbound, manifest['snapshot_pins'])
    if expected != manifest:
        raise ValueError('approved snapshot wrapper mismatch')
    return copy.deepcopy(manifest)


def prepare_approved(campaign, dataset, name, source_dir, spark_reference, timeout=300, abs_tol=1e-8, rel_tol=1e-8):
    if (any(not math.isfinite(v) or not 0 <= v <= 1e-8 for v in (abs_tol, rel_tol))
            or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError('invalid deadline/tolerance; maximum 1e-8')
    deadline = time.monotonic() + timeout

    def check():
        if time.monotonic() >= deadline:
            raise TimeoutError('approved alternatives deadline')
        campaign.check()
        if time.monotonic() >= deadline:
            raise TimeoutError('approved alternatives deadline')

    sources = read_sources(source_dir, check)
    from .runner import dataset_version
    from .spark_reference import dataset_identity, validate_reference, EXPECTED_PROFILE, SPARK_VERSION
    check()
    dataset = Path(dataset).resolve()
    dataset_text = (dataset / 'dataset.json').read_text()
    check()
    source_text = (dataset / 'queries.json').read_text()
    check()
    meta = json.loads(dataset_text)
    version = dataset_version(meta)
    original = ds.logical_manifest(json.loads(source_text))
    doc = metadata()
    schemas = ds.table_schemas()
    if (meta['status'] != 'SUCCESS' or meta['suite'] != 'tpcds' or meta['generator_version'] != '1.4.3'
            or meta['dataset_id'] != original['source_dataset_id'] or len(meta['tables']) != 24
            or {t['name']: [column(c['name'], c['type']) for c in t['schema']] for t in meta['tables']} != schemas):
        raise ValueError('successful pinned 24-table dataset required')
    if EXPECTED_PROFILE != doc['oracle_profile'] or SPARK_VERSION != '4.1.3':
        raise ValueError('oracle profile/version mismatch')
    campaign.check(additional=2 * 1024**3)
    check()
    identity = dataset_identity(dataset, check=check)
    raw_hashes = {q['id']: q['raw_source_sha256'] for q in doc['queries']}
    columns = {q['id']: q['columns'] for q in doc['queries']}
    proof = validate_reference(spark_reference, identity, raw_hashes, expected_columns=columns, check=check)
    check()
    entries, rules, bodies = compile_sources(sources, doc, check, deadline)
    qualification = dict(status='INCOMPLETE', dataset_identity=identity, report_sha256=proof['report_sha256'], profile=EXPECTED_PROFILE,
                         engine_version=SPARK_VERSION, raw_source_hashes=raw_hashes)
    manifest = dict(format=1, suite=SUITE, dataset_id=meta['dataset_id'], source_dataset_id=meta['dataset_id'],
                    table_schemas=schemas, provenance=SOURCE, queries=entries, spark_qualification=qualification,
                    source_manifest_sha256=ds.digest(source_text), source_hashes={q['id']: q['source_sha256'] for q in entries})
    _validate_structure(manifest)

    def external(out, q, audit, pair, callback):
        result = proof['results'][q['id']]
        reader = Reader(result['path'])
        path = out / (q['id']+'-spark-ordinal.jsonl')
        writer = Writer(path, q['columns'], pair)
        try:
            for row in reader:
                callback()
                writer.row([decoded(v) for v in row])  # Preserve native typed values by ordinal.
            writer.finish()
        finally:
            reader.f.close()
            writer.f.close()
        for label in ('source', 'source_cast', 'portable'):
            compare(path, out / audit[label]['file'], ordered=False, abs_tol=abs_tol, rel_tol=rel_tol, scratch=out, check=callback)
        ds.validate_query_order(path, q, callback)
        return dict(engine='Apache Spark 4.1.3 raw source', raw_sha256=raw_hashes[q['id']],
                    result_sha256=result['sha256'], report_sha256=proof['report_sha256'],
                    rows=result['rows'], comparisons=['source', 'source_cast', 'portable'],
                    source_cast_same_sql=True, independent_source_cast_proof=False)

    def recheck():
        check()
        if read_sources(source_dir, check) != sources or dataset_identity(dataset, check=check) != identity:
            raise ValueError('qualification input changed')
        refreshed = validate_reference(spark_reference, identity, raw_hashes, expected_columns=columns, check=check)
        if refreshed != proof:
            raise ValueError('Spark reference changed')
        check()

    out = ds._prepare_parity(campaign, dataset, name, meta, version, dataset_text, source_text, manifest,
                             rules, max(0, deadline-time.monotonic()), abs_tol, rel_tol, recheck, bodies,
                             absolute_deadline=deadline, external_compare=external)
    return _finalize_approved(out, campaign, manifest, source_dir, dataset, spark_reference, recheck, check)


def _finalize_approved(out, campaign, manifest, source_dir, dataset, spark_reference, recheck, check):
    qualification = manifest['spark_qualification']
    # The shared lifecycle has completed and written SUCCESS before acceptance.
    from .capacity import write_json
    try:
        recheck()
        qualification['status'] = 'SUCCESS'
        plan = _load(out / 'plan.json')
        plan['qualification'] = copy.deepcopy(qualification)
        write_json(out / 'plan.json', plan)
        locations = dict(out_dir=str(out.resolve()), campaign_dir=str(campaign.root.resolve()),
                         source_dir=str(Path(source_dir).resolve()), dataset_dir=str(dataset),
                         reference_dir=str(Path(spark_reference).resolve()))
        acceptance = _evidence(manifest, locations, frozen=False, check=check)
        write_json(out / 'acceptance.json', acceptance)
        manifest['acceptance'] = dict(path=str(out / 'acceptance.json'), sha256=_hash(out / 'acceptance.json'))
        recheck()
        write_json(out / 'queries.json', manifest)
        check()
        return out
    except BaseException as exc:
        # Even failure after publication invalidates acceptance through both owners.
        try:
            plan = _load(out / 'plan.json')
            plan.update(status='FAILED', error_type=type(exc).__name__, error=str(exc))
            write_json(out / 'plan.json', plan)
        except BaseException:
            pass
        try:
            campaign.mark(out, 'FAILED')
        except BaseException:
            pass  # Preserve the primary freeze exception.
        raise
