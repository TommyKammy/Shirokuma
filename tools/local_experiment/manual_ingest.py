"""Bounded manual CSV/JSON adapter. No resident, CLI, or HTTP entry point."""
import codecs
import csv
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal, localcontext, ROUND_HALF_UP
import hashlib
import io
import json
import math
from pathlib import Path
import re

import control_store
import data_management as registry
import dataops_service as dataops
from query_context import QueryError


def sql_type(value):
    if not isinstance(value, str):
        raise ValueError('unsupported schema hint')
    value = re.sub(r'\s+', '', value.upper())
    value = {'INT': 'INTEGER', 'TIMESTAMP': 'TIMESTAMP(6)', 'DECIMAL': 'DECIMAL(38,0)'}.get(value, value)
    match = re.fullmatch(r'DECIMAL\((\d+)\)', value)
    if match:
        value = 'DECIMAL(' + match[1] + ',0)'
    return dataops._type(value)


def typed_value(value, kind):
    """Convert exactly as emitted; the converted value is also the dedup key."""
    import lake_ingest as legacy
    kind = sql_type(kind)
    if value is None or value == '' and kind != 'VARCHAR':
        return None
    if kind == 'VARCHAR':
        text = legacy.json_text(value) if isinstance(value, (dict, list)) else str(value)
        if '\x00' in text:
            raise ValueError('NUL in value')
        return text
    if kind == 'BOOLEAN':
        if type(value) is bool:
            return value
        if isinstance(value, str) and value.lower() in ('true', 'false'):
            return value.lower() == 'true'
        raise ValueError('invalid BOOLEAN')
    if kind in legacy.INTEGER_TYPES:
        if type(value) is bool:
            raise ValueError('invalid integer')
        return legacy.integer_value(value, kind)
    if kind in ('DOUBLE', 'REAL'):
        if type(value) is bool:
            raise ValueError('invalid float')
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('nonfinite float')
        if kind == 'REAL':
            import struct
            number = struct.unpack('!f', struct.pack('!f', number))[0]
            if not math.isfinite(number):
                raise ValueError('nonfinite REAL')
        return number
    if kind.startswith('DECIMAL'):
        if type(value) in (bool, float):
            raise ValueError('decimal requires exact input')
        precision, scale = map(int, re.findall(r'\d+', kind))
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError('nonfinite decimal')
        with localcontext() as ctx:
            ctx.prec = max(80, len(number.as_tuple().digits) + abs(number.as_tuple().exponent) + scale)
            number = number.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)
            if abs(number) >= Decimal(10) ** (precision - scale):
                raise ValueError('decimal out of range')
        return number
    if kind == 'DATE':
        if type(value) is date:
            return value
        if not legacy.is_date(value):
            raise ValueError('invalid DATE')
        return date.fromisoformat(value)
    if kind.startswith('TIMESTAMP'):
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                raise ValueError('offset timestamps unsupported')
            value = value.isoformat(sep=' ', timespec='microseconds')
        return datetime.fromisoformat(legacy.timestamp_value(value, kind))
    raise ValueError('unsupported type')


def _read(raw, detected, encoding, delimiter, hints, target_columns=None, *, allow_new_columns=False):
    import lake_ingest as legacy
    text = raw.decode(encoding)
    if detected == 'csv':
        reader = csv.reader(io.StringIO(text, newline=''), delimiter=delimiter, strict=True)
        names = next(reader, [])
        rows = []
        for row in reader:
            if not row:
                continue
            if len(row) != len(names):
                raise ValueError('CSV row width differs from header')
            rows.append(row)
    else:
        def invalid_constant(value):
            raise ValueError('nonfinite JSON number: ' + value)
        def load(value):
            return json.loads(value, parse_float=Decimal, parse_constant=invalid_constant)
        rows = (load(text) if detected == 'json' else
                [load(line) for line in text.splitlines() if line.strip()])
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError('JSON input must contain objects')
        names = list(dict.fromkeys(k for row in rows for k in row))
        rows = [[row.get(n) for n in names] for row in rows]
    if not names or len(set(names)) != len(names):
        raise ValueError('distinct nonempty input columns required')
    for name in names:
        dataops._column(name)
    if set(hints) - set(names):
        raise ValueError('schema hint column absent from input')
    normalized_hints = {n: sql_type(t) for n, t in hints.items()}
    target_types = dict(target_columns or ())
    if target_columns is not None:
        if not allow_new_columns and set(names) - target_types.keys():
            raise ValueError('input contains columns absent from target')
        if any(n in target_types and t != sql_type(target_types[n]) for n, t in normalized_hints.items()):
            raise ValueError('schema hint conflicts with existing target type')
    def infer(values):
        present = [v for v in values if v is not None and v != '']
        numeric_text = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?')
        exact_numeric = (present and not any(legacy.leading_zero(v) for v in present) and
            all(type(v) in (int, Decimal) or type(v) is str and numeric_text.fullmatch(v.strip()) for v in present))
        if exact_numeric:
            if all(legacy.integer(v) for v in present):
                return 'BIGINT'
            numbers = [Decimal(str(v)) for v in present]
            if not all(n.is_finite() for n in numbers):
                raise ValueError('nonfinite number')
            scale = max(max(0, -n.as_tuple().exponent) for n in numbers)
            whole = max(max(0, n.adjusted() + 1) for n in numbers)
            return sql_type(f'DECIMAL({max(1, whole + scale)},{scale})')
        return legacy.infer_type(values)
    # Existing target types precede both inference and value conversion.
    columns = [(n, target_types[n] if n in target_types else
                normalized_hints[n] if n in hints else infer([r[i] for r in rows]))
               for i, n in enumerate(names)]
    return columns, rows


def _persist(execution, receipt):
    with control_store.connect() as db:
        db.execute('UPDATE dataops_operations SET result=? WHERE id=?', (dataops._json(receipt), execution.id))


def capture_input(*, filename, content, format='auto', encoding=None, delimiter=None):
    """Snapshot upload bytes and effective decoding options without backend access."""
    if (not isinstance(filename, str) or not filename.strip() or Path(filename).name in ('', '.', '..')
            or not isinstance(content, (str, bytes))):
        raise ValueError('filename and content required')
    raw = content.encode('utf-8') if isinstance(content, str) else bytes(content)
    suffix = Path(filename).suffix.lower()
    detected = {'.csv': 'csv', '.tsv': 'csv', '.json': 'json', '.jsonl': 'jsonl', '.ndjson': 'jsonl'}.get(suffix) if format == 'auto' else format
    codec = codecs.lookup(encoding or 'utf-8-sig').name
    if encoding is None:
        try:
            raw.decode(codec)
        except UnicodeDecodeError:
            if detected == 'csv':
                codec = codecs.lookup('cp932').name
    if detected in ('json', 'jsonl'):
        detected = 'json' if raw.decode(codec, errors='ignore').lstrip('\ufeff \t\r\n').startswith('[') else 'jsonl'
    separator = delimiter if delimiter is not None else ('\t' if suffix == '.tsv' else ',')
    if detected not in ('csv', 'json', 'jsonl') or not isinstance(separator, str) or len(separator) != 1:
        raise ValueError('invalid input format or delimiter')
    return raw, detected, codec, separator


def _preview_request(context, *, mode, destination=None, target=None, schema_hints=None,
                  key_columns=(), batch_size=500, atomic_append=False, **upload):
    """Capture fingerprint material without parsing rows before idempotency."""
    raw, detected, codec, delimiter = capture_input(**upload)
    hints = json.loads(json.dumps(schema_hints if schema_hints is not None else {}))
    if not isinstance(hints, dict) or mode not in ('create', 'replace', 'append', 'upsert'):
        raise ValueError('invalid preview options')
    chosen = destination if mode == 'create' else target
    if (type(chosen) is not (registry.DestinationLocator if mode == 'create' else registry.ResourceRef)
            or (target is not None if mode == 'create' else destination is not None)
            or type(batch_size) is not int or batch_size < 1 or type(atomic_append) is not bool
            or not isinstance(key_columns, (list, tuple)) or any(not isinstance(k, str) for k in key_columns)
            or len(set(key_columns)) != len(key_columns)):
        raise ValueError('invalid preview target or options')
    payload = dict(input_sha256=hashlib.sha256(raw).hexdigest(), input_length=len(raw),
        format=detected, encoding=codec, delimiter=delimiter if detected == 'csv' else None,
        schema_hints=hints, mode=mode, key_columns=tuple(key_columns), batch_size=batch_size,
        atomic_append=atomic_append, target=asdict(chosen))
    fingerprint = hashlib.sha256(dataops._json(dict(payload=payload, context=asdict(context))).encode()).hexdigest()
    return raw, detected, codec, delimiter, hints, payload, fingerprint


def preview_fingerprint(context, **options):
    return _preview_request(context, **options)[-1]


def input_preview(context, **options):
    """Input-only preview; existing target types are validated at ingestion time."""
    raw, detected, codec, delimiter, hints, payload, fingerprint = _preview_request(context, **options)
    columns, rows = _read(raw, detected, codec, delimiter, hints)
    sample = []
    for row in rows:
        converted = [typed_value(v, t) for v, (_, t) in zip(row, columns)]
        if len(sample) < 50:
            sample.append([str(v) if v is not None and type(v) is not bool else v for v in converted])
    if payload['mode'] == 'upsert' and (not payload['key_columns'] or set(payload['key_columns']) - {n for n, _ in columns}):
        raise ValueError('upsert requires input key columns')
    return dict(input_fingerprint=payload['input_sha256'], request_fingerprint=fingerprint,
        format=detected, encoding=codec, columns=[dict(name=n, type=t) for n, t in columns],
        rows=sample, total_rows=len(rows))


def ingest(principal, context, *, idempotency_key, mode, destination=None, target=None,
           file=None, filename=None, content=None, format='auto', delimiter=None,
           encoding=None, schema_hints=None, key_columns=(), batch_size=500, atomic_append=False,
           _execution=None, _defer_finalization=False):
    """Run accepted ingest work and authorize its public return after settlement."""
    observation = _ingest(principal, context, idempotency_key=idempotency_key, mode=mode,
        destination=destination, target=target, file=file, filename=filename, content=content,
        format=format, delimiter=delimiter, encoding=encoding, schema_hints=schema_hints,
        key_columns=key_columns, batch_size=batch_size, atomic_append=atomic_append,
        _execution=_execution, _defer_finalization=_defer_finalization)
    if _execution is None:
        dataops._validated_context(principal, context)
    return observation


def _ingest(principal, context, *, idempotency_key, mode, destination=None, target=None,
            file=None, filename=None, content=None, format='auto', delimiter=None,
            encoding=None, schema_hints=None, key_columns=(), batch_size=500, atomic_append=False,
            _execution=None, _defer_finalization=False):
    """Capture once, accept durably, validate fully, then execute generated SQL.

    Execution failures return the durable observation (possibly UNKNOWN or a
    partial ordinary append). Invalid requests raise after settling an accepted
    operation; replay always returns the original observation without SQL.
    """
    # Snapshot mutable caller options before accepting anything.
    hints = json.loads(json.dumps(schema_hints if schema_hints is not None else {}))
    keys = tuple(key_columns)
    if mode not in ('create', 'replace', 'append', 'upsert'):
        raise ValueError('explicit ingest mode required')
    if mode == 'create':
        if type(destination) is not registry.DestinationLocator or target is not None:
            raise ValueError('create requires only DestinationLocator')
        chosen = destination
    else:
        if type(target) is not registry.ResourceRef or destination is not None:
            raise ValueError('existing mode requires only ResourceRef')
        chosen = target
    if type(batch_size) is not int or batch_size < 1 or type(atomic_append) is not bool:
        raise ValueError('invalid batch/atomic options')
    if not isinstance(hints, dict) or any(not isinstance(k, str) for k in keys) or len(set(keys)) != len(keys):
        raise ValueError('invalid schema hints or keys')
    # Match the current upload contract: text is UTF-8, bytes are verbatim.
    if filename is not None or content is not None:
        if (file is not None or not isinstance(filename, str) or not filename.strip()
                or Path(filename).name in ('', '.', '..') or not isinstance(content, (str, bytes))):
            raise ValueError('filename and content required; file is mutually exclusive')
        raw = content.encode('utf-8') if isinstance(content, str) else bytes(content)
        suffix = Path(filename).suffix.lower()
    else:
        if file is None:
            raise ValueError('input file required')
        path = Path(file)
        raw, suffix = path.read_bytes(), path.suffix.lower()
    raw, detected, codec, effective_delimiter = capture_input(filename='input' + suffix,
        content=raw, format=format, encoding=encoding, delimiter=delimiter)
    # Type hint normalization remains after begin, so bad hints cannot hide a conflict.
    payload = dict(input_sha256=hashlib.sha256(raw).hexdigest(), input_length=len(raw),
        format=detected, encoding=codec, delimiter=effective_delimiter if detected == 'csv' else None,
        schema_hints=hints, mode=mode, key_columns=keys, batch_size=batch_size,
        atomic_append=atomic_append, target=asdict(chosen))
    execution = _execution or dataops.begin(principal, context, route='manual-ingest',
                                           idempotency_key=idempotency_key, payload=payload)
    if execution.replayed:
        return execution.inspect()
    with dataops._accepted(execution):
        if _execution is not None:
            if execution.principal != principal or execution._context() != context:
                raise QueryError('CONTEXT_MISMATCH', 409)
            execution._active()
        receipt = dict(operation_id=execution.id, input_fingerprint=payload['input_sha256'],
            request_fingerprint=execution._row()['fingerprint'],
            input_length=len(raw), format=detected, encoding=codec, delimiter=payload['delimiter'],
            mode=mode, atomic=mode != 'append' or atomic_append, target=None,
            columns=[], accepted_rows=0, committed_rows=0, committed_batches=0,
            loaded_batches=0, cleanup='not-needed')
        stage = None
        started_writes = False
        try:
            locator = execution.ingest_claim(chosen)
            if mode == 'create':
                execution._absent(locator)
            expected = () if mode == 'create' else execution.ingest_columns(target, step='target-schema')
            columns, rows = _read(raw, detected, codec, effective_delimiter, hints,
                                  expected if mode != 'create' else None, allow_new_columns=mode == 'replace')
            if mode != 'create':
                incoming = {n for n, _ in columns}
                if mode != 'replace' and incoming - {n for n, _ in expected}:
                    raise ValueError('input contains columns absent from target')
                positions = {n: i for i, (n, _) in enumerate(columns)}
                columns = ([(n, t) for n, t in expected if n in incoming] +
                           [(n, t) for n, t in columns if n not in dict(expected)])
                # Reorder input to the target column order before conversion.
                rows = [[r[positions[n]] for n, _ in columns] for r in rows]
            columns = tuple((n, sql_type(t)) for n, t in columns)
            rows = [tuple(typed_value(v, t) for v, (_, t) in zip(row, columns)) for row in rows]
            receipt['accepted_rows'] = len(rows)
            if mode == 'upsert':
                names = [n for n, _ in columns]
                if not keys or any(k not in names for k in keys):
                    raise ValueError('upsert requires input key columns')
                indices = [names.index(k) for k in keys]
                unique = {}
                for row in rows:
                    key = tuple(row[i] for i in indices)
                    if any(v is None for v in key):
                        raise ValueError('upsert input keys must be non-null')
                    unique[key] = row
                rows = list(unique.values())
                execution.ingest_validate_keys(target, keys, step='target-keys')
            receipt['columns'] = [dict(name=n, type=t) for n, t in columns]
            receipt['schema_hints'] = dict(hints)
            receipt['observed_columns'] = None
            if target is not None:
                receipt['target'] = asdict(target)
            _persist(execution, receipt)
            if receipt['atomic']:
                with control_store.connect() as db:
                    schemas = list(db.execute('SELECT id FROM schemas WHERE database_id=? AND sql_name=?',
                                              (context.database_id, 'sandbox')))
                if len(schemas) != 1:
                    raise ValueError('registered SANDBOX schema required')
                execution._ingest_schema(schemas[0]['id'])
                receipt['cleanup'] = 'observation-required'
                _persist(execution, receipt)
                started_writes = True
                stage = execution.create_stage(schemas[0]['id'], columns, step='stage-create')
                receipt['cleanup'] = 'pending'
                _persist(execution, receipt)
                for offset in range(0, len(rows), batch_size):
                    execution.ingest_rows(stage, columns, rows[offset:offset + batch_size], step='load-' + str(offset))
                    receipt['loaded_batches'] += 1
                    _persist(execution, receipt)
                committed = dict(receipt, committed_rows=len(rows), committed_batches=1)
                execution.ingest_publish(chosen, stage, columns, mode=mode, expected_columns=expected,
                    keys=keys, step='commit', result=committed)
                # FINISHED commit is already durable even if this observation fails.
                receipt = committed
                if mode in ('create', 'replace', 'upsert'):
                    receipt['target'] = asdict(execution._observe(locator))
                _persist(execution, receipt)
            else:
                for offset in range(0, len(rows), batch_size):
                    if execution.ingest_columns(target, step='schema-' + str(offset)) != expected:
                        raise QueryError('TARGET_SCHEMA_CHANGED', 409)
                    batch = rows[offset:offset + batch_size]
                    committed = dict(receipt, committed_rows=receipt['committed_rows'] + len(batch),
                                     committed_batches=receipt['committed_batches'] + 1)
                    started_writes = True
                    execution.ingest_rows(target, columns, batch, step='append-' + str(offset), commit_result=committed)
                    receipt = committed
            observed_target = registry.ResourceRef(**receipt['target'])
            receipt['observed_columns'] = [dict(name=n, type=t) for n, t in
                execution.ingest_columns(observed_target, step='observed-schema')]
            _persist(execution, receipt)
            if stage is not None:
                try:
                    execution.cleanup(stage, step='cleanup')
                    receipt['cleanup'] = 'finished'
                    stage = None
                except Exception:
                    receipt['cleanup'] = 'unknown' if execution._row()['state'] == 'UNKNOWN' else 'failed'
                _persist(execution, receipt)
            # Empty ordinary append is a valid no-op (schema read is still a child).
            return execution._inspect() if _defer_finalization else execution.complete(receipt)
        except Exception as error:
            # Never overwrite the durable commit receipt with stale local counters.
            observed = execution._inspect()
            if observed['result'] is not None:
                receipt = observed['result']
            receipt['error'] = getattr(error, 'code', type(error).__name__)
            if observed['state'] == 'ACTIVE':
                if stage is not None:
                    try:
                        execution.cleanup(stage, step='failure-cleanup')
                        receipt['cleanup'] = 'finished'
                    except Exception:
                        receipt['cleanup'] = 'unknown' if execution._row()['state'] == 'UNKNOWN' else 'failed'
                try:
                    _persist(execution, receipt)
                except Exception:
                    # A FINISHED commit already contains counts in the query-state
                    # transaction. Later bookkeeping cannot erase that evidence.
                    return execution._inspect()
                try:
                    execution.fail()
                except QueryError:
                    pass  # An unresolved child keeps the claim/admission held.
            if not started_writes:
                raise
            return execution._inspect()
