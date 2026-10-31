"""A2-only metadata fences for INVOKER VIEW closure.

Original-SQL EXPLAIN and REST fences provide optimistic validation, not a
serializable catalog snapshot. No definition or catalog location is retained.
"""
import hashlib
import re
import uuid

import sqlglot

import control_store
import db_ai as ai
import logical_registry
from query_context import QueryError


def _unsupported():
    ai._fail('AI_PLAN_VIEW_UNSUPPORTED')


def _digest(value):
    return hashlib.sha256(ai._json(value).encode()).hexdigest()


def bearing(pins):
    return any(pin.get('kind') == 'VIEW' for pin in pins)


def _frontier(principal, context, refs):
    pins = []
    with control_store.connect() as db:
        for schema, table in refs:
            row = db.execute('SELECT id FROM schemas WHERE database_id=? AND sql_name=?',
                (context.database_id, schema)).fetchone()
            value = ai.registry.get_schema(db, row[0]) if row else None
            if value is None:
                ai._fail('SQL_PERMISSION_DENIED', 403)
            pins.append(dict(schema_id=value.id, schema=schema,
                schema_revision=value.revision, table=table))
    # Establish the entire frontier before any of its REST reads.
    ai._authority(principal, context, pins)
    return pins


def _columns(fields):
    def supported(value):
        if isinstance(value, str):
            decimal = re.fullmatch(r'decimal\(([0-9]+),\s*([0-9]+)\)', value)
            if decimal:
                precision, scale = map(int, decimal.groups())
                return 1 <= precision <= 38 and 0 <= scale <= precision
            return bool(re.fullmatch(r'(boolean|int|long|float|double|date|time|timestamp|timestamptz|'
                r'string|uuid|binary|fixed\[[1-9][0-9]*\])', value))
        if not isinstance(value, dict):
            return False
        kind = value.get('type')
        if kind == 'struct':
            nested = value.get('fields')
            return isinstance(nested, list) and bool(_columns(nested))
        if kind == 'list':
            return supported(value.get('element'))
        if kind == 'map':
            return supported(value.get('key')) and supported(value.get('value'))
        return False
    if not isinstance(fields, list) or not fields:
        _unsupported()
    columns = []
    for field in fields:
        if (not isinstance(field, dict) or not isinstance(field.get('name'), str)
                or not field['name'] or not supported(field.get('type'))):
            _unsupported()
        columns.append(dict(name=field['name'], type=ai._type(field['type'])))
    if len({c['name'] for c in columns}) != len(columns):
        _unsupported()
    return columns


def _measure(principal, context, pin, snapshot_versions=None):
    inv = ai.inventory
    schema, database, binding, _ = inv._schema(principal, context.selected_role,
        pin['schema_id'], context.database_id)
    with inv.catalog_api(database, binding) as api:
        response = api(inv._path(schema, 'VIEW', pin['table']))
    try:
        metadata, pointer = response['metadata'], response['metadata-location']
        if not isinstance(metadata, dict) or not isinstance(pointer, str) or not pointer.strip():
            raise ValueError()
        identity = metadata['view-uuid']
        uuid.UUID(identity)
        with control_store.connect() as db:
            obj = ai.registry.get_object(db, pin['ref']['object_id'])
        if (obj is None or obj.kind != 'VIEW' or obj.schema_id != pin['schema_id']
                or obj.incarnation != pin['ref']['incarnation'] or obj.backend_identity != identity):
            ai._fail('AI_METADATA_CHANGED', 409)
        version = metadata['current-version-id']
        versions = [v for v in metadata['versions'] if v['version-id'] == version]
        if type(version) is not int or version < 1 or len(versions) != 1:
            raise ValueError()
        current = versions[0]
        if type(current['version-id']) is not int:
            raise ValueError()
        schema_id = current['schema-id']
        schemas = [s for s in metadata['schemas'] if s['schema-id'] == schema_id]
        reps = [r for r in current['representations']
            if r['type'] == 'sql' and r.get('dialect') == 'trino']
        properties = metadata.get('properties', {})
        if (type(schema_id) is not int or len(schemas) != 1 or type(schemas[0]['schema-id']) is not int
                or len(reps) != 1
                or not isinstance(properties, dict)
                or any(k in properties for k in ('trino.run-as-owner', 'trino.owner', 'owner'))
                or properties.get('trino.security', 'INVOKER') != 'INVOKER'):
            raise ValueError()
        definition = reps[0]['sql']
        if not isinstance(definition, str) or not definition.strip() or len(definition.encode()) > 65536:
            raise ValueError()
        columns = _columns(schemas[0]['fields'])
        if columns != pin['columns'] or version != pin.get('view_version', version):
            ai._fail('AI_METADATA_CHANGED', 409)
        catalog = current.get('default-catalog')
        namespace = current.get('default-namespace', [])
        if catalog is not None and (not isinstance(catalog, str) or not catalog):
            raise ValueError()
        if catalog is not None and catalog != context.sql_catalog:
            ai._fail('SQL_PERMISSION_DENIED', 403)
        if (not isinstance(namespace, list) or len(namespace) > 1
                or any(not isinstance(n, str) or not n for n in namespace)):
            raise ValueError()
        default_schema = namespace[0] if catalog and namespace else None
        refs, _ = ai._parse(definition, context, stored_defaults=(catalog, default_schema),
            _stored_versions=snapshot_versions)
        canonical = sqlglot.parse_one(definition, read='trino', error_message_context=0).sql(
            dialect='trino', unsupported_level=sqlglot.ErrorLevel.RAISE, comments=False)
        evidence = dict(view_uuid=identity, view_version=version, view_schema_id=schema_id,
            security='INVOKER', defaults_digest=_digest([catalog, namespace]),
            definition_digest=hashlib.sha256(definition.encode()).hexdigest(),
            canonical_definition_digest=hashlib.sha256(canonical.encode()).hexdigest(),
            head_digest=_digest(dict(metadata=metadata, metadata_location=pointer)),
            dependencies=[list(ref) for ref in refs])
        return evidence, refs, len(definition.encode())
    except QueryError as error:
        if error.code == 'AI_INPUT_INVALID':
            _unsupported()
        raise
    except (KeyError, ValueError, TypeError, AttributeError, RecursionError, UnicodeError,
            sqlglot.errors.SqlglotError):
        _unsupported()


def _snapshots(principal, context, pin, requested):
    """Pin only named historical schemas, never the mutable TABLE head."""
    inv = ai.inventory
    schema, database, binding, _ = inv._schema(principal, context.selected_role,
        pin['schema_id'], context.database_id)
    with inv.catalog_api(database, binding) as api:
        response = api(inv._path(schema, 'TABLE', pin['table']))
    try:
        metadata = response['metadata']
        identity = metadata['table-uuid']
        uuid.UUID(identity)
        with control_store.connect() as db:
            obj = ai.registry.get_object(db, pin['ref']['object_id'])
        if (obj is None or obj.kind != 'TABLE' or obj.schema_id != pin['schema_id']
                or obj.incarnation != pin['ref']['incarnation'] or obj.backend_identity != identity):
            ai._fail('AI_METADATA_CHANGED', 409)
        snapshots, schemas = metadata['snapshots'], metadata['schemas']
        if not isinstance(snapshots, list) or not isinstance(schemas, list):
            _unsupported()
        evidence = []
        for ident in sorted(requested):
            matches = [s for s in snapshots if isinstance(s, dict) and s.get('snapshot-id') == ident]
            if len(matches) != 1 or type(matches[0]['snapshot-id']) is not int:
                _unsupported()
            schema_id = matches[0]['schema-id']
            matches = [s for s in schemas if isinstance(s, dict) and s.get('schema-id') == schema_id]
            if (type(schema_id) is not int or schema_id < 0 or len(matches) != 1
                    or type(matches[0]['schema-id']) is not int):
                _unsupported()
            columns = _columns(matches[0]['fields'])
            if columns != pin['columns']:
                _unsupported()
            evidence.append(dict(snapshot_id=ident,
                schema_fingerprint=_digest(dict(schema_id=schema_id, columns=columns))))
        pin['snapshots'] = evidence
    except (KeyError, ValueError, TypeError, AttributeError, RecursionError, UnicodeError):
        _unsupported()


def capture(principal, context, sql):
    """Keep the TABLE fast path byte-compatible with A2's original pins."""
    try:
        pins, relations, prompt = ai._capture(principal, context, sql, tables_only=False)
    except QueryError as error:
        if error.code != 'AI_METADATA_UNAVAILABLE':
            raise
        # A1 reports a missing/zero VIEW version as unavailable. Classify a
        # readable malformed VIEW with A3's stricter validator, without changing
        # A1's capture contract. Genuine catalog failures keep the original 502.
        refs, _ = ai._parse(sql, context)
        try:
            for pin in _frontier(principal, context, refs):
                inv = ai.inventory
                resolved = inv.resolve_object(principal, context.selected_role, context.database_id,
                    pin['schema_id'], ai.registry.quote_identifier(pin['table']))
                if resolved['object']['kind'] != 'VIEW':
                    continue
                pin['ref'] = resolved['ref']
                detail = inv.object_detail(principal, context.selected_role, pin['ref']['object_id'], detail='columns')
                pin['columns'] = [dict(name=c['name'], type=ai._type(c['type'])) for c in detail['columns']]
                _measure(principal, context, pin)
        except ai.inventory.InventoryError:
            pass
        except (KeyError, ValueError, TypeError, AttributeError, RecursionError, UnicodeError):
            _unsupported()
        raise error
    except (KeyError, ValueError, TypeError, AttributeError, RecursionError, UnicodeError):
        _unsupported()
    if not bearing(pins):
        return pins, relations, prompt, None
    with control_store.connect() as db:
        if logical_registry.creation_mode(db, context.database_id) != 'logical_v1':
            _unsupported()
    direct = {(p['schema'], p['table']) for p in pins}
    known = {(p['schema'], p['table']): p for p in pins}
    visiting, visited, heights = set(), set(), {}
    versions = {}
    total_bytes = 0

    def visit(key, depth):
        nonlocal total_bytes
        if key in visiting:
            _unsupported()
        if key in visited:
            if depth + heights[key] - 1 > 16:
                _unsupported()
            return heights[key]
        pin = known[key]
        pin['direct'] = key in direct
        if pin['kind'] == 'VIEW':
            if depth > 16:
                _unsupported()
            visiting.add(key)
            evidence, refs, size = _measure(principal, context, pin, versions)
            total_bytes += size
            if total_bytes > 262144:
                _unsupported()
            pin.update(evidence)
            missing = [ref for ref in refs if ref not in known]
            if len(known) + len(missing) > 64:
                _unsupported()
            frontier = _frontier(principal, context, refs)
            for child in frontier:
                child_key = (child['schema'], child['table'])
                if child_key in known:
                    continue
                inv = ai.inventory
                resolved = inv.resolve_object(principal, context.selected_role, context.database_id,
                    child['schema_id'], ai.registry.quote_identifier(child['table']))
                ref = resolved['ref']
                detail = inv.object_detail(principal, context.selected_role, ref['object_id'], detail='columns')
                if ref != detail['ref'] or ref['database_id'] != context.database_id:
                    ai._fail('AI_METADATA_CHANGED', 409)
                kind = resolved['object']['kind']
                if kind not in ('TABLE', 'VIEW'):
                    _unsupported()
                child.update(ref=ref, kind=kind,
                    columns=[dict(name=c['name'], type=ai._type(c['type'])) for c in detail['columns']])
                known[child_key] = child
            height = 1 + max((visit(ref, depth + 1) for ref in refs), default=0)
            visiting.remove(key)
        else:
            height = 0
        heights[key] = height
        visited.add(key)
        return height

    try:
        for key in sorted(direct):
            visit(key, 1)
        all_pins = [known[key] for key in sorted(known)]
        ai._authority(principal, context, all_pins)
        for key in sorted(versions):
            if known[key]['kind'] != 'TABLE':
                _unsupported()
        for key in sorted(versions):
            _snapshots(principal, context, known[key], versions[key])
        dependencies = [dict(schema=p['schema'], table=p['table'], kind=p['kind'], columns=p['columns'])
            for p in all_pins if not p['direct']]
        if len(ai._json(dict(relations=relations,
                view_dependencies=dependencies)).encode()) > 65536:
            _unsupported()
        return all_pins, relations, prompt, dependencies
    except ai.inventory.InventoryError:
        ai._fail('AI_METADATA_UNAVAILABLE', 502)
    except (RecursionError, UnicodeError):
        _unsupported()
