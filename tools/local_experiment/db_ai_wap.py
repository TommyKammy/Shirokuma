"""Read-only preparation of an owned SQL proposal for a new Manual WAP draft."""
from copy import deepcopy

import control_store
import data_management as registry
import database_inventory as inventory
import db_ai as ai
import db_ai_sql_proposal as b1
import manual_wap


def _fail(code):
    ai._fail(code, 409)


def _shape(record, pins, ident):
    try:
        if (record['id'] != ident or record['state'] != 'SUCCEEDED'
                or not isinstance(record['sql'], str) or not record['sql'].strip()
                or len(record['sql'].encode('utf-8')) > 16384
                or not isinstance(record['created_at'], str) or not record['created_at']
                or not isinstance(record['input']['request'], str)
                or not record['input']['request'].strip()
                or len(record['input']['request'].encode('utf-8')) > 4096):
            raise ValueError()
        refs = record['input']['relations']
        b1._refs(refs)
        public = record['metadata']['relations']
        if not isinstance(pins, list) or len(pins) != len(refs) or len(public) != len(refs):
            raise ValueError()
        selected = {}
        for pin in pins:
            key = (pin['schema'], pin['table'])
            if (key in selected or pin['ref'] not in refs or pin['schema_id'] != pin['ref']['schema_id']
                    or pin['kind'] not in ('TABLE', 'VIEW')
                    or type(pin['schema_revision']) is not int
                    or not all(isinstance(v, str) and v for v in key)):
                raise ValueError()
            descriptor = dict(schema=pin['schema'], table=pin['table'], kind=pin['kind'], columns=pin['columns'])
            if public.count(descriptor) != 1 or not isinstance(pin['columns'], list):
                raise ValueError()
            for column in pin['columns']:
                if (not isinstance(column, dict) or set(column) != {'name', 'type'}
                        or not all(isinstance(v, str) and v for v in column.values())):
                    raise ValueError()
            selected[key] = pin
        if sorted(ai._json(p['ref']) for p in pins) != sorted(ai._json(r) for r in refs):
            raise ValueError()
        return selected
    except Exception:
        _fail('AI_WAP_UNAVAILABLE')


def _translate(sql, context, selected):
    # Reject parser fallbacks locally before the shared parser can emit diagnostics.
    import sqlglot
    from sqlglot import exp
    from sqlglot.dialects.trino import Trino
    from sqlglot.optimizer.scope import Scope, traverse_scope
    class QuietParser(Trino.Parser):
        def _warn_unsupported(self):
            raise ValueError()
    try:
        trees = QuietParser(error_level=sqlglot.ErrorLevel.RAISE, error_message_context=0).parse(
            Trino().tokenize(sql), sql)
        if len(trees) != 1 or not isinstance(trees[0], (exp.Select, exp.SetOperation)):
            raise ValueError()
        tree = trees[0]
        if any(isinstance(n, exp.Command) for n in tree.walk()):
            raise ValueError()
        ai._parse(sql, context, stored_defaults=(context.sql_catalog, context.sql_schema))
        if any(n.comments for n in tree.walk()):
            raise ValueError()
        for node in tree.find_all(exp.Identifier):
            if not node.args.get('quoted'):
                node.set('this', node.this.lower())
        scopes = list(traverse_scope(tree))
        physical, used = {}, set()
        for scope in scopes:
            for name, (_, source) in scope.selected_sources.items():
                if isinstance(source, Scope):
                    continue
                if (not isinstance(source, exp.Table) or not isinstance(source.this, exp.Identifier)
                        or any(v for k, v in source.args.items() if k not in ('this', 'db', 'catalog', 'alias'))
                        or source.catalog and source.catalog != context.sql_catalog):
                    raise ValueError()
                key = (source.db or context.sql_schema, source.name)
                if key not in selected or selected[key]['kind'] != 'TABLE':
                    raise ValueError()
                physical[id(source)] = (source, key)
                used.add(key)
        if not 1 <= len(used) <= 8:
            raise ValueError()
        # Qualified columns must identify one physical, unaliased source in the
        # nearest scope that binds their table name (including correlated scopes).
        by_expression = {id(s.expression): s for s in scopes}
        for column in tree.find_all(exp.Column):
            if not column.db and not column.catalog:
                continue
            ancestor = column.parent
            while ancestor is not None and id(ancestor) not in by_expression:
                ancestor = ancestor.parent
            scope = by_expression.get(id(ancestor))
            while scope is not None and column.table not in scope.selected_sources:
                scope = scope.parent
            if scope is None:
                raise ValueError()
            _, source = scope.selected_sources[column.table]
            entry = physical.get(id(source))
            if entry is None:
                raise ValueError()
            table, key = entry
            if (column.table != table.name or table.alias and table.alias != table.name
                    or column.db != key[0] or column.catalog and column.catalog != context.sql_catalog):
                raise ValueError()
            column.set('db', None)
            column.set('catalog', None)
        reserved = {cte.alias.lower() for cte in tree.find_all(exp.CTE)}
        aliases = {}
        candidate = 1
        for key in sorted(used):
            while 'source_' + str(candidate) in reserved:
                candidate += 1
            aliases[key] = 'source_' + str(candidate)
            candidate += 1
        for table, key in physical.values():
            if not table.args.get('alias'):
                table.set('alias', exp.TableAlias(this=exp.Identifier(this=table.name, quoted=True)))
            table.set('this', exp.Identifier(this=aliases[key]))
            table.set('db', None)
            table.set('catalog', None)
        translated = tree.sql(dialect='trino', unsupported_level=sqlglot.ErrorLevel.RAISE)
        bindings = {aliases[key]: registry.ResourceRef(**{k: selected[key]['ref'][k] for k in
            ('database_id', 'schema_id', 'object_id', 'incarnation')}) for key in sorted(used)}
        manual_wap._compile_model(translated, bindings)
        if len(translated.encode('utf-8')) > 65536:
            raise ValueError()
        sources = [dict(alias=aliases[key], ref=deepcopy(selected[key]['ref']),
            schema=key[0], table=key[1]) for key in sorted(used)]
        return translated, sources
    except Exception:
        _fail('AI_WAP_UNSUPPORTED')


def _authority(principal, source, destination, pins):
    ai._authority(principal, source, pins)
    ai._authority(principal, destination, pins)
    with control_store.connect() as db:
        db.execute('BEGIN')
        ai._transaction_authority(db, principal, source, pins)
        ai._transaction_authority(db, principal, destination, pins)
        schema = registry.get_schema(db, destination.schema_id)
        if schema.sql_name != 'sandbox' or schema.purpose != 'sandbox':
            _fail('AI_WAP_CONTEXT_MISMATCH')
        if not inventory._permissions(destination.selected_role, schema)['write']:
            ai._fail('SQL_PERMISSION_DENIED', 403)


def _local(principal, source, destination, pins, sources):
    _authority(principal, source, destination, pins)
    selected = {(p['schema'], p['table']): p for p in pins}
    result = []
    with control_store.connect() as db:
        db.execute('BEGIN')
        ai._transaction_authority(db, principal, source, pins)
        ai._transaction_authority(db, principal, destination, pins)
        for item in sources:
            pin, ref = selected[(item['schema'], item['table'])], item['ref']
            schema = registry.get_schema(db, ref['schema_id'])
            obj = registry.get_object(db, ref['object_id'])
            if (ref['database_id'] != source.database_id
                    or ref['binding_revision'] != source.binding_revision or ref['generation_id'] != source.generation_id
                    or schema is None or schema.database_id != source.database_id
                    or schema.sql_name != pin['schema'] or schema.revision != pin['schema_revision']
                    or obj is None or obj.schema_id != schema.id or obj.incarnation != ref['incarnation']
                    or obj.kind != 'TABLE' or obj.physical_name != pin['table']):
                _fail('AI_METADATA_CHANGED')
            result.append((schema, obj))
        database = registry.get_database(db, source.database_id)
        binding = registry.get_binding(db, source.database_id)
    return database, binding, result


def prepare(principal, context, destination_context, ident):
    with control_store.connect() as db:
        try:
            record, pins = b1._owned(db, principal, context, ident)
        except (ValueError, TypeError, KeyError, AttributeError):
            _fail('AI_WAP_UNAVAILABLE')
    if context.selected_role not in ('data_engineer', 'admin'):
        ai._fail('SQL_PERMISSION_DENIED', 403)
    source, destination = context.public_metadata(), destination_context.public_metadata()
    if (any(source[k] != destination[k] for k in source if k not in ('schema_id', 'sql_schema'))
            or context.warehouse_id != 'LOCAL' or destination_context.sql_schema != 'sandbox'):
        _fail('AI_WAP_CONTEXT_MISMATCH')
    selected = _shape(record, pins, ident)
    _authority(principal, context, destination_context, pins)
    sql, sources = _translate(record['sql'], context, selected)
    database, binding, local = _local(principal, context, destination_context, pins, sources)
    try:
        with inventory.catalog_api(database, binding) as api:
            for item, (schema, obj) in zip(sources, local):
                metadata = inventory._metadata(api, schema, 'TABLE', obj.physical_name)
                current = metadata.get('current-schema-id')
                schemas = [s for s in metadata['schemas'] if s['schema-id'] == current]
                if current is None or len(schemas) != 1:
                    _fail('AI_METADATA_CHANGED')
                columns = [dict(name=c['name'], type=ai._type(c['type'])) for c in schemas[0]['fields']]
                if (metadata.get('table-uuid') != obj.backend_identity
                        or columns != selected[(item['schema'], item['table'])]['columns']):
                    _fail('AI_METADATA_CHANGED')
    except Exception:
        _fail('AI_METADATA_CHANGED')
    if _local(principal, context, destination_context, pins, sources) != (database, binding, local):
        _fail('AI_METADATA_CHANGED')
    return dict(version=1, proposal_id=ident, source_context=source, destination_context=destination,
        original_sql=record['sql'], sql=sql, sources=sources, request=record['input']['request'],
        proposal_created_at=record['created_at'], prepared_at=ai._now())
