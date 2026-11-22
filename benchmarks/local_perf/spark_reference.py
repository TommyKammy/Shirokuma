"""Raw Spark reference evidence, not engine attestation or official TPC acceptance.

SQL resources originate in Spark v4.0.1; execution requires Apache Spark 4.1.3.
The pure validator trusts the caller's reference evidence and checks its contents.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
import uuid

from .dataset_layout import table_files
from .results import Reader, Writer, column

SPARK_VERSION = '4.1.3'
SOURCE_COMMIT = '29434ea766b0fc3c3bf6eaadb43a8f931133649e'
EXPECTED_IDS = ('5a', '10a', '14a', '18a', '22a', '27a', '35a', '36a',
                '51a', '67a', '70a', '77a', '80a', '86a')
EXPECTED_PROFILE = {
    'spark.sql.ansi.enabled': 'true',
    'spark.sql.session.timeZone': 'UTC',
    'spark.sql.caseSensitive': 'false',
    'spark.sql.shuffle.partitions': '2',
    'spark.sql.decimalOperations.allowPrecisionLoss': 'true',
    'spark.sql.ansi.enforceReservedKeywords': 'false',
    'spark.sql.legacy.allowNegativeScaleOfDecimal': 'false',
    'spark.sql.storeAssignmentPolicy': 'ANSI',
}
REPORT_FILE = 'report.json'


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf8')


def _fingerprint(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _input_schemas(doc):
    # Keep the shared contract provider lazy to avoid sibling import cycles.
    from .tpcds_sql import table_schemas
    schemas = table_schemas()
    if len(schemas) != 24 or doc.get('input_schema_sha256') != _fingerprint(schemas):
        raise ValueError('approved input schema fingerprint mismatch')
    return schemas


def _source_hashes(sources, check):
    hashes = {}
    for qid, text in sources.items():
        check()
        hashes[qid] = hashlib.sha256(text.encode('utf8')).hexdigest()
        check()
    return hashes


def _hash(path, check):
    check()
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while True:
            check()
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    check()
    return digest.hexdigest()


def _document(path, check):
    check()
    raw = Path(path).read_bytes()
    check()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def dataset_identity(dataset, check=lambda: None):
    """Hash explicit dataset files without reading Parquet rows or importing SDKs."""
    check()
    dataset = Path(dataset)
    meta, manifest_sha = _document(dataset / 'dataset.json', check)
    queries, queries_sha = _document(dataset / 'queries.json', check)
    version = meta.get('format', 1)
    if (type(version) is not int or version not in (1, 2)
            or meta.get('status') != 'SUCCESS' or meta.get('suite') != 'tpcds'
            or not isinstance(meta.get('dataset_id'), str) or not meta['dataset_id']
            or queries.get('dataset_id') != meta['dataset_id']
            or not isinstance(meta.get('tables'), list) or not meta['tables']):
        raise ValueError('successful TPC-DS dataset v1/v2 required')
    files, names, physical_total = {}, set(), 0
    for table in meta['tables']:
        name = table['name']
        if not isinstance(name, str) or not name or name in names:
            raise ValueError('duplicate/invalid table')
        names.add(name)
        parts = table_files(dataset, table, version, check)
        physical_total += sum(p['physical_bytes'] for p in parts)
        for part in parts:
            check()
            if part['file'] in files:
                raise ValueError('reused dataset file')
            size = part['path'].stat().st_size
            digest = _hash(part['path'], check)
            if (size != part['file_bytes'] or part['path'].stat().st_size != size
                    or (part['sha256'] is not None and digest != part['sha256'])):
                raise ValueError('dataset file bytes/hash mismatch')
            files[part['file']] = dict(table=name, rows=part['rows'], file_bytes=size, sha256=digest)
    if 'parquet_physical_bytes' in meta:
        total = meta['parquet_physical_bytes']
        if (type(total) is not int or total < 0
                or total != physical_total):
            raise ValueError('invalid dataset physical byte total')
    check()
    return dict(dataset_id=meta['dataset_id'], manifest_sha256=manifest_sha,
                queries_manifest_sha256=queries_sha, files=files)


def _columns(schema):
    if not isinstance(schema, dict) or schema.get('type') != 'struct' or not isinstance(schema.get('fields'), list):
        raise ValueError('native Spark struct schema required')
    columns, used = [], set()
    for i, field in enumerate(schema['fields']):
        if (not isinstance(field.get('name'), str) or type(field.get('nullable')) is not bool
                or not isinstance(field.get('type'), str)):
            raise ValueError('invalid native Spark field')
        name = field['name'] if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', field['name']) else 'c' + str(i)
        if name in used:
            name = 'column_' + str(i)
            while name in used:
                name += '_'
        used.add(name)
        kind = {'long': 'bigint', 'integer': 'integer', 'string': 'varchar',
                'byte': 'tinyint', 'short': 'smallint', 'timestamp_ntz': 'timestamp(6)'}.get(field['type'], field['type'])
        columns.append(column(name, kind))
    if not columns:
        raise ValueError('empty result schema')
    return columns


def _result_metadata(dataset, fingerprint, qid, source_hashes):
    return dict(dataset_id=dataset['dataset_id'], dataset_fingerprint=fingerprint,
                query_id=qid, source_sha256=source_hashes[qid],
                engine='Apache Spark 4.1.3 raw source')


def validate_reference(reference_dir, dataset_meta, source_hashes, *, expected_columns=None,
                       check=lambda: None):
    """Validate all fourteen streams; hashes establish consistency, not execution."""
    check()
    root = Path(reference_dir).resolve()
    report, report_sha = _document(root / REPORT_FILE, check)
    fingerprint = _fingerprint(dataset_meta)
    if (set(source_hashes) != set(EXPECTED_IDS)
            or any(not isinstance(s, str) or re.fullmatch(r'[0-9a-f]{64}', s) is None for s in source_hashes.values())):
        raise ValueError('exact fourteen source hashes required')
    if expected_columns is not None and set(expected_columns) != set(EXPECTED_IDS):
        raise ValueError('exact fourteen expected schemas required')
    if (type(report.get('format')) is not int or report['format'] != 1
            or report.get('status') != 'SUCCESS' or report.get('engine') != 'Apache Spark'
            or report.get('engine_version') != SPARK_VERSION or report.get('source_release') != 'Spark v4.0.1'
            or report.get('source_commit') != SOURCE_COMMIT
            or report.get('config') != EXPECTED_PROFILE or _canonical(report.get('dataset')) != _canonical(dataset_meta)
            or report.get('dataset_fingerprint') != fingerprint or report.get('source_hashes') != source_hashes
            or report.get('inputs_unchanged') is not True
            or type(report.get('planned_queries')) is not int or report['planned_queries'] != 14
            or type(report.get('max_rows')) is not int or report['max_rows'] <= 0
            or not isinstance(report.get('queries'), list) or len(report['queries']) != 14
            or report.get('cleanup_errors') or report.get('error')):
        raise ValueError('invalid/incomplete Spark reference report')
    results, filenames = {}, set()
    for query in report['queries']:
        check()
        qid = query.get('id')
        if qid not in EXPECTED_IDS or qid in results:
            raise ValueError('unknown/duplicate reference query')
        name = query.get('result_file')
        if (not isinstance(name, str) or name != qid + '-raw.jsonl'
                or Path(name).name != name or name in filenames):
            raise ValueError('unique direct-child result required')
        filenames.add(name)
        path = (root / name).resolve()
        if path.parent != root or not path.is_file():
            raise ValueError('missing/nonlocal result')
        columns = _columns(query.get('native_schema'))
        if (query.get('status') != 'SUCCESS' or query.get('raw_source_unmodified') is not True
                or query.get('source_sha256') != source_hashes[qid]
                or query.get('canonical_columns') != columns
                or query.get('error') or query.get('cleanup_errors')
                or (expected_columns is not None and columns != expected_columns[qid])
                or type(query.get('rows')) is not int or not 0 <= query['rows'] <= report['max_rows']
                or query.get('truncated', False) is not False
                or _hash(path, check) != query.get('result_sha256')):
            raise ValueError('invalid source/schema/rows/result digest')
        reader = Reader(path)
        try:
            if (reader.header['columns'] != columns
                    or reader.header['metadata'] != _result_metadata(dataset_meta, fingerprint, qid, source_hashes)):
                raise ValueError('reference header mismatch')
            count = 0
            check()
            for count, _ in enumerate(reader, 1):
                if count > report['max_rows']:
                    raise ValueError('reference row limit exceeded')
                if count % 256 == 0:
                    check()
            check()
            if count != query['rows']:
                raise ValueError('reference row count mismatch')
        finally:
            reader.f.close()
        results[qid] = dict(path=path, columns=columns, rows=count, sha256=query['result_sha256'])
    check()
    return dict(report=report, report_sha256=report_sha, results=results)


@contextmanager
def _job(session, deadline, check):
    """Bound blocking Spark actions; local properties belong to this calling thread."""
    check()
    context = session.sparkContext
    original_properties = {
        key: context.getLocalProperty(key)
        for key in ('spark.jobGroup.id', 'spark.job.description', 'spark.job.interruptOnCancel')
    }
    group = 'spark-reference-' + uuid.uuid4().hex
    expired, cancellation_errors = threading.Event(), []

    def cancel():
        expired.set()
        try:
            context.cancelJobGroup(group)
        except Exception as exc:
            cancellation_errors.append(str(exc))

    def job_check():
        check()
        if expired.is_set() or time.monotonic() >= deadline:
            raise TimeoutError('Spark action deadline exceeded')

    timer, started = None, False
    failure = None
    try:
        context.setJobGroup(group, 'raw Spark reference', interruptOnCancel=True)
        timer = threading.Timer(max(0, deadline - time.monotonic()), cancel)
        timer.daemon = True
        timer.start()
        started = True
        job_check()
        yield job_check
        job_check()
    except BaseException as exc:
        failure = exc
        raise
    finally:
        errors = []
        if timer is not None:
            try:
                timer.cancel()
            except Exception as exc:
                errors.append(str(exc))
            if started:
                try:
                    timer.join(timeout=1)
                except Exception as exc:
                    errors.append(str(exc))
                try:
                    if timer.is_alive():
                        errors.append('Spark cancellation thread still running after cleanup timeout')
                except Exception as exc:
                    errors.append(str(exc))
        errors.extend(cancellation_errors)
        for key, value in original_properties.items():
            try:
                context.setLocalProperty(key, value)
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            if failure is None:
                raise RuntimeError('Spark job cleanup failed: ' + '; '.join(errors))
            # The primary failure remains the cause; cleanup is also visible in the report.
            failure.spark_cleanup_errors = errors


def _plan(frame, check):
    plan = frame._jdf.queryExecution().analyzed()
    text = plan.toString()
    if len(text) > 65536:
        raise ValueError('analyzed plan exceeds reference metadata limit')
    expressions, visits = [], 0

    def sequence(seq):
        return (seq.apply(i) for i in range(seq.size()))

    def walk(node, expression=False):
        nonlocal visits
        check()
        visits += 1
        if visits > 8192:
            raise ValueError('plan traversal exceeds reference metadata limit')
        if expression:
            kind = node.getClass().getSimpleName().lower()
            if kind in ('average', 'avg', 'divide', 'cast', 'sum', 'coalesce', 'casewhen', 'sortorder'):
                record = dict(kind=kind, sql=node.sql(), dataType=node.dataType().catalogString(),
                              children=[dict(sql=c.sql(), dataType=c.dataType().catalogString())
                                        for c in sequence(node.children())])
                if kind == 'sortorder':
                    record.update(direction=node.direction().sql(), null_ordering=node.nullOrdering().sql())
                expressions.append(record)
                if len(expressions) > 512 or len(_canonical(expressions)) > 262144:
                    raise ValueError('expression metadata limit exceeded')
        else:
            for expr in sequence(node.expressions()):
                walk(expr, True)
        for child in sequence(node.children()):
            walk(child, expression)

    walk(plan)
    return dict(analyzed_plan=text, expressions=expressions)


def export_reference(dataset, source_dir, output, *, timeout=600, query_timeout=60,
                     max_rows=1000, image_receipt=None, spark=None):
    """Execute pinned, unchanged SELECT resources in a private Spark SQL session."""
    started = time.monotonic()
    if (any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0
            for v in (timeout, query_timeout)) or type(max_rows) is not int or max_rows <= 0):
        raise ValueError('positive finite deadlines and max_rows required')
    if image_receipt is not None:
        if not isinstance(image_receipt, (str, dict)):
            raise ValueError('image_receipt must be a caller-provided string or dict')
        _canonical(image_receipt)
    deadline = started + timeout

    def check():
        if time.monotonic() >= deadline:
            raise TimeoutError('Spark reference overall deadline exceeded')

    check()
    dataset, source_dir, output = Path(dataset).resolve(), Path(source_dir).resolve(), Path(output).resolve()
    repo = Path(__file__).resolve().parents[2]
    if output == repo or repo in output.parents:
        raise ValueError('output must be outside repository')
    if any(output == root or root in output.parents for root in (dataset, source_dir)):
        raise ValueError('output must be outside input directories')
    output.mkdir()  # Exclusive ownership; never overwrite an existing bundle.
    report = dict(format=1, status='FAILED', engine='Apache Spark', engine_version=SPARK_VERSION,
                  source_release='Spark v4.0.1', source_commit=SOURCE_COMMIT,
                  config=dict(EXPECTED_PROFILE), planned_queries=14,
                  max_rows=max_rows, queries=[], evidence='caller-provided reference; no engine attestation')
    if image_receipt is not None:
        report['image_receipt'] = dict(provenance='caller supplied; informational', value=image_receipt)
    session, scratch, failure = None, None, None
    cleanup_errors = []
    try:
        check()
        from .tpcds_approved_variants import metadata, read_sources, IDS
        check()
        doc = metadata()
        sources = read_sources(source_dir, check)
        check()
        if tuple(IDS) != EXPECTED_IDS or set(sources) != set(EXPECTED_IDS):
            raise ValueError('approved fourteen-query corpus required')
        hashes = _source_hashes(sources, check)
        check()
        identity = dataset_identity(dataset, check)
        report.update(dataset=identity, dataset_fingerprint=_fingerprint(identity), source_hashes=hashes)
        meta, _ = _document(dataset / 'dataset.json', check)
        schemas = _input_schemas(doc)
        declared = {t['name']: [column(c['name'], c['type']) for c in t['schema']] for t in meta['tables']}
        if len(schemas) != 24 or len(meta['tables']) != 24 or declared != schemas:
            raise ValueError('strict approved twenty-four table schemas required')
        scratch = Path(tempfile.mkdtemp(prefix='.spark-scratch-', dir=output))
        check()
        if spark is None:
            from pyspark import SparkContext
            from pyspark.sql import SparkSession
            check()
            if SparkContext._active_spark_context is not None:
                raise ValueError('Existing SparkContext detected; pass an explicit spark argument to preserve caller ownership')
            builder = SparkSession.builder.appName('Shirokuma raw Spark reference')
            builder = builder.config('spark.sql.warehouse.dir', str(scratch / 'warehouse'))
            builder = builder.config('spark.local.dir', str(scratch / 'local'))
            builder = builder.config('spark.sql.catalogImplementation', 'in-memory')
            for key, value in EXPECTED_PROFILE.items():
                builder = builder.config(key, value)
            session = builder.getOrCreate()
        else:
            session = spark.newSession()
        check()
        if session.version != SPARK_VERSION:
            raise ValueError('Apache Spark 4.1.3 required')
        for key, value in EXPECTED_PROFILE.items():
            check()
            session.conf.set(key, value)
        for key, value in EXPECTED_PROFILE.items():
            if session.conf.get(key) != value:
                raise ValueError('effective Spark profile mismatch: ' + key)
        check()
        for table in meta['tables']:
            frames = []
            with _job(session, min(deadline, time.monotonic() + query_timeout), check) as job_check:
                for part in table_files(dataset, table, meta.get('format', 1), job_check):
                    job_check()
                    frame = session.read.option('mergeSchema', 'false').parquet(str(part['path']))
                    job_check()
                    native_input = frame.schema.jsonValue()
                    if ([f['name'] for f in native_input['fields']] != [c['name'] for c in schemas[table['name']]]
                            or _columns(native_input) != schemas[table['name']]):
                        raise ValueError('Parquet native schema mismatch: ' + table['name'])
                    count = frame.count()
                    job_check()
                    if count != part['rows']:
                        raise ValueError('Parquet row count mismatch: ' + table['name'])
                    frames.append(frame)
                frame = frames[0]
                for extra in frames[1:]:
                    job_check()
                    frame = frame.union(extra)  # Positional, never unionByName.
                frame.createOrReplaceTempView(table['name'])
                job_check()
        for qid in EXPECTED_IDS:
            query = dict(id=qid, status='FAILED', source_sha256=hashes[qid], raw_source_unmodified=True,
                         result_file=qid + '-raw.jsonl')
            report['queries'].append(query)
            writer = None
            try:
                with _job(session, min(deadline, time.monotonic() + query_timeout), check) as job_check:
                    frame = session.sql(sources[qid])
                    job_check()
                    schema = frame.schema.jsonValue()
                    columns = _columns(schema)
                    query.update(native_schema=schema, canonical_columns=columns)
                    query.update(_plan(frame, job_check))
                    writer = Writer(output / query['result_file'], columns,
                                    _result_metadata(identity, report['dataset_fingerprint'], qid, hashes))
                    for row in frame.toLocalIterator():
                        job_check()
                        if writer.count >= max_rows:
                            raise ValueError('reference max_rows exceeded; no success truncation')
                        writer.row(tuple(row))
                    job_check()
                    writer.finish()
                    query.update(rows=writer.count, result_sha256=_hash(output / query['result_file'], job_check))
                query['status'] = 'SUCCESS'
            except BaseException as exc:
                query['error'] = dict(type=type(exc).__name__, message=str(exc))
                raise
            finally:
                if writer is not None:
                    try:
                        writer.close()
                    except Exception as exc:
                        cleanup_errors.append(str(exc))
        check()
        after_sources = read_sources(source_dir, check)
        after_hashes = _source_hashes(after_sources, check)
        check()
        if after_hashes != hashes or dataset_identity(dataset, check) != identity:
            raise ValueError('dataset/source changed during Spark export')
        report['inputs_unchanged'] = True
        check()
    except BaseException as exc:
        failure = exc
        cleanup_errors.extend(getattr(exc, 'spark_cleanup_errors', []))
    finally:
        if session is not None and spark is None:
            try:
                session.stop()
            except Exception as exc:
                cleanup_errors.append(str(exc))
        if scratch is not None:
            try:
                shutil.rmtree(scratch)
            except Exception as exc:
                cleanup_errors.append(str(exc))
    try:
        check()
    except BaseException as exc:
        if failure is None:
            failure = exc
    if cleanup_errors:
        report['cleanup_errors'] = cleanup_errors
        if failure is None:
            failure = RuntimeError('Spark reference cleanup failed')
    if failure is None:
        report['status'] = 'SUCCESS'
    else:
        report['error'] = dict(type=type(failure).__name__, message=str(failure))
    (output / REPORT_FILE).write_bytes(_canonical(report) + b'\n')
    if failure is not None:
        raise failure
    try:
        validate_reference(output, identity, hashes, check=check)
        check()
    except BaseException as exc:
        report.update(status='FAILED', error=dict(type=type(exc).__name__, message=str(exc)))
        (output / REPORT_FILE).write_bytes(_canonical(report) + b'\n')
        raise
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('dataset', 'source_dir', 'output'):
        parser.add_argument(name)
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--query-timeout', type=float, default=60)
    parser.add_argument('--max-rows', type=int, default=1000)
    parser.add_argument('--image-receipt')
    output = export_reference(**vars(parser.parse_args()))
    print(json.dumps(dict(status='SUCCESS', output=str(output))))


if __name__ == '__main__':
    main()
