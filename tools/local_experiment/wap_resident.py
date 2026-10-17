"""Write-audit-publish engine using the calling Bears module for runtime hooks."""
import json
from pathlib import Path
import re

import warehouse_resident as wr

WAP_REGISTRY = Path(__file__).resolve().parents[2] / 'tmp' / 'local-experiment' / 'wap_models.json'


def extract_source_tables(statement: str) -> list[str]:
    """Collect FROM/JOIN identifiers while ignoring SQL strings and comments."""
    tokens = re.findall(r'--[^\n]*|/\*.*?\*/|\'(?:\'\'|[^\'])*\'|"(?:""|[^"])*"|[A-Za-z_][\w$]*|[^\s]',
                        statement, re.DOTALL)
    tokens = [t for t in tokens if not t.startswith(("'", '--', '/*'))]

    def is_identifier(token):
        return token.startswith('"') or re.fullmatch(r'[A-Za-z_][\w$]*', token) is not None

    def normalize(token):
        return token[1:-1].replace('""', '"') if token.startswith('"') else token.lower()

    ctes = set()
    for i, token in enumerate(tokens):
        if token.lower() not in ('with', ','):
            continue
        j = i + 1
        if j < len(tokens) and tokens[j].lower() == 'recursive':
            j += 1
        if (j + 2 < len(tokens) and is_identifier(tokens[j]) and
                tokens[j + 1].lower() == 'as' and tokens[j + 2] == '('):
            ctes.add(normalize(tokens[j]).lower())
    sources = set()
    for i, token in enumerate(tokens):
        if token.lower() not in ('from', 'join'):
            continue
        j = i + 1
        if j >= len(tokens) or not is_identifier(tokens[j]):
            continue
        parts = [normalize(tokens[j])]
        j += 1
        while j + 1 < len(tokens) and tokens[j] == '.' and is_identifier(tokens[j + 1]):
            parts.append(normalize(tokens[j + 1]))
            j += 2
        if j < len(tokens) and tokens[j] == '(':
            continue  # Table functions are not source tables.
        if len(parts) == 1 and parts[0].lower() in ctes:
            continue
        if len(parts) > 1 and parts[0].lower() == 'iceberg':
            parts = parts[1:]
        sources.add('.'.join(parts))
    return sorted(sources)


def load_wap_registry(b) -> dict:
    try:
        registry = json.loads(b.WAP_REGISTRY.read_text())
        return {name: entry for name, entry in registry.items() if isinstance(entry, dict)} if isinstance(registry, dict) else {}
    except (OSError, ValueError):
        return {}


def save_wap_entry(b, name: str, **fields) -> dict:
    registry = b.load_wap_registry()
    entry = dict(registry.get(name) or {}, **fields)
    registry[name] = entry
    try:
        b.WAP_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        b.WAP_REGISTRY.write_text(json.dumps(registry, indent=2) + '\n')
    except OSError:
        pass
    return entry


def latest_snapshot(b, url, table):
    rows = b.sql(url, 'SELECT snapshot_id FROM ' + b.identifier(table + '$snapshots') +
               ' ORDER BY committed_at DESC LIMIT 1')['rows']
    return rows[0][0] if rows else None


def audit_table(b, url, full_table_name):
    table = b.identifier(full_table_name)
    columns = [dict(name=row[0], type=row[1]) for row in
               b.sql(url, 'SHOW COLUMNS FROM ' + table)['rows']]
    aggregates = ['COUNT(*)'] + [
        'COUNT(*) - COUNT("' + c['name'].replace('"', '""') + '")' for c in columns]
    counts = b.sql(url, 'SELECT ' + ', '.join(aggregates) + ' FROM ' + table)['rows'][0]
    row_count = counts[0]
    null_counts = dict(zip([c['name'] for c in columns], counts[1:]))
    sample_rows = b.sql(url, 'SELECT * FROM ' + table + ' LIMIT 5')['rows']
    return dict(table=full_table_name, row_count=row_count, column_count=len(columns),
                columns=columns, null_counts=null_counts, sample_rows=sample_rows,
                snapshot_id=b.latest_snapshot(url, full_table_name), quality_passed=row_count > 0)


def check_quality_assertions(audit, min_rows=1, not_null=''):
    minimum = max(1, int(min_rows or 1))
    errors = []
    if audit['row_count'] < minimum:
        errors.append(f'row_count must be > 0 and >= {minimum} (got {audit["row_count"]})')
    for column in (c.strip() for c in not_null.split(',') if c.strip()):
        if column not in audit['null_counts']:
            errors.append('not-null column missing: ' + column)
        elif audit['null_counts'][column] != 0:
            errors.append('not-null assertion failed: ' + column)
    audit['quality_passed'] = not errors
    audit.pop('assertion_error', None)
    if errors:
        audit['assertion_error'] = '; '.join(errors)


def data_table(value, rollback=False):
    """Normalize explicit lab table names without accepting SQL fragments."""
    parts = value.split('.')
    if parts[0] == 'iceberg':
        parts = parts[1:]
    if not 1 <= len(parts) <= 2 or not all(re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', p) for p in parts):
        raise ValueError('invalid data table name')
    if rollback:
        return 'iceberg.' + ('.'.join(parts) if len(parts) == 2 else 'marts.' + parts[0])
    if len(parts) == 2 and parts[0] not in wr.WRITABLE_SCHEMAS:
        raise ValueError('stage/publish table must use raw, sandbox, staging, or marts')
    return parts[-1]


def data(b, args):
    request = getattr(args, 'request', '')
    rollback = getattr(args, 'rollback', None)
    publish = getattr(args, 'publish', None)
    refresh = getattr(args, 'refresh', None)
    if rollback:
        snapshot = str(getattr(args, 'snapshot_id', ''))
        if not re.fullmatch(r'[0-9]+', snapshot):
            raise ValueError('rollback requires a digits-only snapshot-id')
        target = b.data_table(rollback, rollback=True)
        statement = b.wr.check_sql_policy('ALTER TABLE ' + b.identifier(target) +
            f' EXECUTE rollback_to_snapshot({int(snapshot)})', mode='admin-write')
        with b.wr.trino() as url:
            b.sql(url, statement)
            audit = b.audit_table(url, target)
        return dict(wap_action='rollback', table=target, restored_snapshot_id=int(snapshot), audit=audit, valid=True)
    if publish or refresh:
        action = 'refresh' if refresh else 'publish'
        name = b.data_table(refresh or publish)
        source, target = 'iceberg.sandbox.' + name, 'iceberg.marts.' + name
        saved_sql = None
        if refresh:
            entry = b.load_wap_registry().get(name) or {}
            saved_sql = getattr(args, 'sql', None) or entry.get('sql')
            if not saved_sql:
                raise ValueError(f'no recorded WAP model SQL for {name}')
            saved_sql = b.wr.check_sql_policy(saved_sql, mode='read-only')
            if not re.match(r'(?i)^(SELECT|WITH)\b', saved_sql):
                raise ValueError('SELECT model required')
        with b.wr.trino() as url:
            if refresh:
                b.sql(url, b.wr.check_sql_policy('CREATE SCHEMA IF NOT EXISTS iceberg.sandbox', mode='sandbox-write'))
                b.sql(url, b.wr.check_sql_policy('CREATE OR REPLACE TABLE ' + b.identifier(source) +
                    ' AS ' + saved_sql, mode='sandbox-write'))
            source_audit = b.audit_table(url, source)
            b.check_quality_assertions(source_audit, getattr(args, 'min_rows', 1), getattr(args, 'not_null', ''))
            if not source_audit['quality_passed']:
                return dict(wap_action=action, table=target, source_table=source, valid=False,
                            audit=source_audit, error=source_audit.get('assertion_error') or
                            'sandbox table failed quality audit (row_count must be > 0)')
            previous = b.attempt(lambda: dict(snapshot_id=b.latest_snapshot(url, target))).get('snapshot_id')
            if previous is not None and not getattr(args, 'allow_shrink', False) and 'row_count' in source_audit:
                try:
                    baseline = dict(
                        row_count=b.sql(url, 'SELECT COUNT(*) FROM ' + b.identifier(target))['rows'][0][0],
                        columns=[r[0] for r in b.sql(url, 'SHOW COLUMNS FROM ' + b.identifier(target))['rows']])
                except Exception as exc:
                    return dict(wap_action=action, table=target, source_table=source,
                                previous_snapshot_id=previous, valid=False, audit=source_audit,
                                error='publish blocked: failed to query baseline table for regression check (' + str(exc) + ')')
                target_count, target_cols = baseline.get('row_count'), baseline.get('columns', [])
                source_cols = {col['name'] for col in source_audit.get('columns', [])}
                missing = [c for c in target_cols if c not in source_cols]
                error = None
                if missing:
                    error = 'publish blocked by regression guard: missing columns ' + ', '.join(missing)
                elif isinstance(target_count, int) and target_count > 0 and source_audit['row_count'] < target_count * 0.5:
                    error = (f'publish blocked by regression guard: row_count dropped from {target_count} to '
                             f'{source_audit["row_count"]} (>50% shrink; pass --allow-shrink to override)')
                if error:
                    return dict(wap_action=action, table=target, source_table=source,
                                previous_snapshot_id=previous, valid=False, audit=source_audit, error=error)
            statement = 'CREATE OR REPLACE TABLE ' + b.identifier(target) + ' AS SELECT * FROM ' + b.identifier(source)
            if staged_snapshot := source_audit.get('snapshot_id'):
                statement += f' FOR VERSION AS OF {staged_snapshot}'
            statement = b.wr.check_sql_policy(statement, mode='admin-write')
            b.sql(url, statement)
            audit = b.audit_table(url, target)
        fields = dict(table=target, source_table=source, published=True,
                      published_snapshot_id=audit.get('snapshot_id'), previous_snapshot_id=previous)
        extra = {}
        if refresh:
            extra = dict(generated_sql=saved_sql, sources=b.extract_source_tables(saved_sql))
            fields.update(sql=saved_sql, sources=extra['sources'], staged_table=source,
                          staged_snapshot_id=source_audit.get('snapshot_id'))
        b.save_wap_entry(name, **fields)
        return dict(wap_action=action, source_table=source, table=target, previous_snapshot_id=previous,
                    published_snapshot_id=audit['snapshot_id'], audit=audit, valid=True, **extra)
    explicit_sql = getattr(args, 'sql', None)
    if not explicit_sql and not request:
        raise ValueError('data bear requires request, --sql, --publish, --rollback, or --refresh')
    stage = getattr(args, 'stage_table', None)
    staged_table = 'iceberg.sandbox.' + b.data_table(stage) if stage else None
    llm_usage = {}
    sources_rows, self_corrected = [], False
    with b.wr.trino() as url:
        statement = explicit_sql.strip() if explicit_sql else b.generate_data_sql(url, request, llm_usage, sources_rows)
        def validate():
            if not re.match(r'(?i)^(SELECT|WITH)\b', statement):
                raise ValueError('SELECT model required')
            return b.sql(url, 'EXPLAIN ' + b.wr.check_sql_policy(statement, mode='read-only'))
        validation = b.attempt(validate)
        if not explicit_sql and 'error' in validation:
            retry = b.attempt(lambda: dict(sql=b.generate_data_sql_retry(
                url, request, sources_rows, failed_sql=statement, error_message=validation['error'])))
            retry_sql = retry.get('sql')
            if isinstance(retry_sql, str) and retry_sql.strip() and retry_sql.strip() != statement:
                statement = retry_sql.strip()
                validation = b.attempt(validate)
                self_corrected = 'error' not in validation
        if staged_table and 'error' not in validation:
            b.sql(url, b.wr.check_sql_policy('CREATE SCHEMA IF NOT EXISTS iceberg.sandbox', mode='sandbox-write'))
            b.sql(url, b.wr.check_sql_policy('CREATE OR REPLACE TABLE ' + b.identifier(staged_table) +
                ' AS ' + b.wr.check_sql_policy(statement), mode='sandbox-write'))
            audit = b.audit_table(url, staged_table)
            if audit['quality_passed']:
                b.save_wap_entry(b.data_table(stage), staged_table=staged_table,
                               table='iceberg.marts.' + b.data_table(stage), sql=statement, intent=request,
                               sources=b.extract_source_tables(statement), staged_snapshot_id=audit.get('snapshot_id'))
            return dict(wap_action='stage', intent=request, generated_sql=statement,
                        valid=audit['quality_passed'], validation=validation, staged_table=staged_table, audit=audit,
                        **({'self_corrected': True} if self_corrected else {}),
                        **({'llm_usage': llm_usage} if llm_usage else {}))
    return dict(intent=request, generated_sql=statement, valid='error' not in validation, validation=validation,
                **({'self_corrected': True} if self_corrected else {}),
                **({'llm_usage': llm_usage} if llm_usage else {}))
