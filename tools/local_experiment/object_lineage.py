"""Read-only, bounded saved provenance for an exact registered object identity.

No catalog discovery, SQL, operation observation, or ledger reconciliation occurs.
A missing fact says nothing about catalog-wide dependencies.
"""
from dataclasses import asdict
import json
import hashlib
import uuid
from copy import deepcopy

import control_store
import data_management as registry
import database_inventory as inventory
import db_dbt
import database_dbt_graph as graph
import manual_wap as wap
from db_marts import _closed_child
from query_context import QueryError

LIMIT = 200
EDGE_LIMIT = 200


def _read(db, table, database_id):
    rows = list(db.execute('SELECT record FROM ' + table +
        ' WHERE database_id=? ORDER BY rowid DESC LIMIT ?', (database_id, LIMIT + 1)))
    result = []
    for row in rows[:LIMIT]:
        try:
            record = json.loads(row[0])
            if isinstance(record, dict):
                result.append(record)
        except (TypeError, ValueError):
            pass
    return result, len(rows) > LIMIT


def _ref(value):
    return asdict(registry.ResourceRef(**value))


def _current(metadata, ref):
    """General read identifiers are URL quoted by inventory, never write parsed."""
    ref = registry.ResourceRef(**ref)
    context = metadata._context()
    schema = metadata._schema(ref.schema_id)
    if ref.database_id != context.database_id:
        raise QueryError('CROSS_DATABASE_TARGET', 409)
    with control_store.connect() as db:
        obj = registry.get_object(db, ref.object_id)
    if (obj is None or obj.schema_id != ref.schema_id or obj.incarnation != ref.incarnation
            or obj.kind not in ('TABLE', 'VIEW')):
        raise QueryError('STALE_RESOURCE', 409)
    database, binding = metadata._catalog()
    with inventory.catalog_api(database, binding) as api:
        response = api(inventory._path(schema, obj.kind, obj.physical_name))
        raw = deepcopy(response.get('metadata', {}))
        pointer = response.get('metadata-location')
    try:
        identity = raw['table-uuid' if obj.kind == 'TABLE' else 'view-uuid']
        uuid.UUID(identity)
        if identity != obj.backend_identity:
            raise ValueError()
        if obj.kind == 'TABLE':
            snapshot = raw.get('current-snapshot-id')
            if snapshot is None or type(snapshot) is int and snapshot == -1:
                if raw.get('snapshots') != []:
                    raise ValueError()
                snapshot = None
            elif type(snapshot) is not int or snapshot < 0:
                raise ValueError()
            schema_id = raw['current-schema-id']
            version = dict(snapshot_id=str(snapshot) if snapshot is not None else None)
        else:
            if not isinstance(pointer, str) or not pointer.strip():
                raise ValueError()
            number = raw['current-version-id']
            versions = [v for v in raw['versions'] if v.get('version-id') == number]
            properties = raw.get('properties', {})
            if (type(number) is not int or number < 1 or len(versions) != 1
                    or not isinstance(properties, dict) or 'trino.run-as-owner' in properties
                    or any(k in properties for k in ('trino.owner', 'owner'))
                    or properties.get('trino.security', 'INVOKER') != 'INVOKER'):
                raise ValueError()
            representations = [r for r in versions[0].get('representations', [])
                if r.get('type') == 'sql' and r.get('dialect') == 'trino']
            if len(representations) != 1 or not isinstance(representations[0].get('sql'), str) or not representations[0]['sql'].strip():
                raise ValueError()
            schema_id = versions[0]['schema-id']
            version = dict(view_version=number)
        schemas = [s for s in raw['schemas'] if s.get('schema-id') == schema_id]
        if type(schema_id) is not int or len(schemas) != 1 or not isinstance(schemas[0]['fields'], list):
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError):
        raise QueryError('STALE_RESOURCE', 409) from None
    endpoint = dict(label='.'.join((context.sql_catalog, schema.sql_name, obj.physical_name)),
        kind=obj.kind, ref=asdict(ref), **version)
    # The private guard also detects schema/security/definition drift during projection.
    return endpoint, (identity, schema_id, raw, pointer)


def _endpoint(current, guard, measurement):
    endpoint = dict(current)
    if current['kind'] == 'VIEW':
        if not graph.valid_view_measurement(measurement):
            raise ValueError()
        if measurement['view_uuid'] != guard[0]:
            raise ValueError()
        if measurement['version'] == current['view_version']:
            version = next(v for v in guard[2]['versions'] if v['version-id'] == current['view_version'])
            sql = next(r['sql'] for r in version['representations'] if r.get('type') == 'sql' and r.get('dialect') == 'trino')
            schema = next(s for s in guard[2]['schemas'] if s['schema-id'] == guard[1])
            head = dict(metadata=guard[2], metadata_location=guard[3])
            if (measurement['_head_digest'] != hashlib.sha256(json.dumps(head, sort_keys=True,
                    separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
                    or measurement['_definition_digest'] != hashlib.sha256(sql.encode()).hexdigest()
                    or measurement['schema_fingerprint'] != hashlib.sha256(
                        json.dumps(schema, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()):
                raise ValueError()
        endpoint['view_version'] = measurement['version']
    else:
        value = measurement['snapshot']
        if type(value) is not str or not value.isascii() or not value.isdecimal():
            raise ValueError()
        endpoint['snapshot_id'] = value
    return endpoint


def _at_current(current, guard, endpoint, measurement):
    schema = next(s for s in guard[2]['schemas'] if s['schema-id'] == guard[1])
    columns = [dict(name=f['name'], type=f['type']) for f in schema['fields']]
    fingerprint = hashlib.sha256(json.dumps(schema, sort_keys=True,
        separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    return (endpoint == current and measurement.get('schema_id') == guard[1]
        and measurement.get('columns') == columns and measurement.get('schema_fingerprint') == fingerprint)


def _wap_child(db, record, phase):
    op = db.execute('SELECT * FROM dataops_operations WHERE id=?', (record['operation_id'],)).fetchone()
    if (op is None or op['route'] != 'manual-wap-' + phase or op['state'] != 'FINISHED'
            or not op['committed'] or op['actor'] != record['actor']
            or json.loads(op['context']) != record['context'] or op['cleanup_error']
            or not _closed_child(db, op['id'], settle=False)):
        return None
    rows = list(db.execute('SELECT d.*,q.*,e.* FROM dataops_queries d '
        'JOIN query_records q ON q.id=d.query_id JOIN query_execution_contexts e ON e.query_id=q.id WHERE d.operation_id=?', (op['id'],)))
    if (len(rows) != 1 or rows[0]['action'] != 'wap_' + phase or rows[0]['state'] != 'FINISHED'
            or rows[0]['actor'] != record['actor'] or not rows[0]['backend_query_id']
            or not rows[0]['commit_result'] or rows[0]['mode'] != 'dataops'
            or rows[0]['request_id'] != op['id'] or rows[0]['worksheet_id'] != phase
            or any(rows[0][k] != v for k, v in record['context'].items())
            or any(rows[0][k] != op[k] for k in ('backend_username', 'credential_snapshot_ref'))):
        return None
    result = json.loads(op['result'] or '{}')
    if result.get('build' if phase == 'build' else 'publication') != record:
        return None
    return json.loads(rows[0]['commit_result'])


def _wap_build(db, built):
    marker = _wap_child(db, built, 'build')
    if (marker is None or marker.get('build_id') != built['id']
            or marker.get('model_id') != built['model_id'] or marker.get('version') != built['version']
            or marker.get('input_refs') != built['input_refs']
            or marker.get('generated_sql') != built['generated_sql']):
        return False
    ref = _ref(built['stage']['ref'])
    row = db.execute('SELECT * FROM object_bindings WHERE id=?', (ref['object_id'],)).fetchone()
    owned = db.execute('SELECT incarnation FROM dataops_owned_objects WHERE operation_id=? AND object_id=?',
        (built['operation_id'], ref['object_id'])).fetchone()
    return (row is not None and row['schema_id'] == ref['schema_id'] and row['incarnation'] == ref['incarnation']
        and owned is not None and owned[0] == ref['incarnation'] and marker.get('stage_locator') ==
        dict(database_id=ref['database_id'], schema_id=ref['schema_id'], physical_name=row['physical_name']))


def _wap_publication(db, publication, builds, audits):
    marker = _wap_child(db, publication, 'publish')
    built, audit = builds.get(publication.get('build_id')), audits.get(publication.get('audit_id'))
    if (marker is None or built is None or audit is None or publication.get('kind') != 'publish'
            or not _wap_build(db, built) or not audit.get('passed') or audit.get('build_id') != built['id']
            or any(publication.get(k) != v for k, v in marker.items())
            or publication['stage'] != built['stage'] or audit['stage'] != built['stage']
            or any(built[k] != publication[k] or audit[k] != built[k] for k in ('database_id', 'model_id', 'version'))):
        return None
    # Audit is read-only and has no commit marker; exact saved successful closure is required.
    op = db.execute('SELECT * FROM dataops_operations WHERE id=?', (audit['operation_id'],)).fetchone()
    if (op is None or op['route'] != 'manual-wap-audit' or op['state'] != 'FINISHED' or op['committed']
            or op['actor'] != audit['actor'] or json.loads(op['context']) != audit['context']
            or not _closed_child(db, op['id'], settle=False)
            or json.loads(op['result'] or '{}').get('audit') != audit):
        return None
    rows = list(db.execute('SELECT q.state,d.action FROM dataops_queries d JOIN query_records q ON q.id=d.query_id '
        'WHERE d.operation_id=?', (op['id'],)))
    if len(rows) != 1 or rows[0]['state'] != 'FINISHED' or rows[0]['action'] != 'wap_audit':
        return None
    return built


def get_lineage(principal, context, object_id):
    metadata = wap._access(principal, context, write=True)
    metadata._schema(context.schema_id)
    with control_store.connect() as db:
        obj = registry.get_object(db, object_id)
    if obj is None or obj.schema_id != context.schema_id or obj.kind not in ('TABLE', 'VIEW'):
        raise QueryError('STALE_RESOURCE', 409)
    focus_ref = dict(database_id=context.database_id, schema_id=obj.schema_id,
        object_id=obj.id, incarnation=obj.incarnation)
    focus, guard = _current(metadata, focus_ref)
    measured = {json.dumps(focus_ref, sort_keys=True): (focus, guard)}
    measurement_errors = {}
    def measure(ref):
        key = json.dumps(ref, sort_keys=True)
        if key in measurement_errors:
            raise measurement_errors[key]
        if key not in measured:
            try:
                measured[key] = _current(metadata, ref)
            except (QueryError, inventory.InventoryError) as error:
                measurement_errors[key] = error
                raise
        return measured[key]
    candidates, seen, limited = [], set(), False
    definition_schemas = set()
    with control_store.connect() as db:
        runs, run_limit = _read(db, 'dbt_runs', context.database_id)
        limited |= run_limit
        by_id = {r.get('id'): r for r in runs}
        parents, malformed = {}, False
        # Ownership covers every same-DB run, independently of the projected page.
        # Canonical run records establish model ownership before operation links.
        for row in db.execute('SELECT record FROM dbt_runs WHERE database_id=?', (context.database_id,)):
            try:
                run = json.loads(row[0])
                if not isinstance(run, dict):
                    raise ValueError()
                if run.get('kind') != 'run':
                    continue
                if not isinstance(run.get('models'), list):
                    raise ValueError()
                for model in run['models']:
                    if not isinstance(model, dict):
                        raise ValueError()
                    ident = model.get('wap_model_id')
                    if ident is not None:
                        if not isinstance(ident, str) or not ident:
                            raise ValueError()
                        parents.setdefault(ident, []).append(run)
            except (KeyError, TypeError, ValueError):
                malformed = True
        def visible(record):
            return db_dbt.lineage_visible(principal, context, record)
        def wap_visible(record):
            ident = record.get('model_id')
            if not isinstance(ident, str):
                return False
            owners = parents.get(ident, [])
            return (not malformed and (not owners or len(owners) == 1 and visible(owners[0])))
        def publication_visible(record):
            owners = parents.get(record.get('model_id'), [])
            if malformed or len(owners) > 1:
                return False
            links = list(db.execute('SELECT run_id,phase,node_id FROM dbt_run_operations WHERE operation_id=?',
                (record['operation_id'],)))
            if not owners:
                return not links  # A standalone manual WAP has no dbt parent.
            if len(links) != 1:
                return False
            row = db.execute('SELECT record FROM dbt_runs WHERE id=? AND database_id=?',
                (links[0]['run_id'], context.database_id)).fetchone()
            parent = json.loads(row[0]) if row else None
            return (isinstance(parent, dict) and parent.get('kind') == 'publication'
                and parent.get('parent_run_id') == owners[0].get('id')
                and links[0]['phase'] == 'publish'
                and parent.get('node_id') == links[0]['node_id']
                and len([m for m in owners[0]['models'] if m.get('wap_model_id') == record.get('model_id')
                    and m.get('unique_id') == links[0]['node_id']]) == 1
                and visible(parent) and visible(owners[0]))
        def add(category, source, target, model=None):
            if not isinstance(source, dict) or not isinstance(target, dict):
                return
            refs = [_ref(p['ref']) for p in (source, target) if 'ref' in p]
            if focus_ref in refs:
                key = json.dumps([category, source, target, model], sort_keys=True)
                if key not in seen:
                    seen.add(key)
                    candidates.append((category, source, target, model))
        records = {}
        for table in ('wap_model_versions', 'wap_builds', 'wap_audits', 'wap_publications'):
            rows, bounded = _read(db, table, context.database_id)
            records[table] = rows
            limited |= bounded
        builds = {r.get('id'): r for r in records['wap_builds']}
        audits = {r.get('id'): r for r in records['wap_audits']}
        for model in records['wap_model_versions']:
            try:
                if not wap_visible(model) or model.get('target_kind') != 'ref':
                    continue
                for source in model['sources'].values():
                    add('declared_model', dict(ref=source), dict(ref=model['target']),
                        dict(id=model['model_id'], version=model['version']))
            except (KeyError, TypeError, ValueError):
                continue
        for built in records['wap_builds']:
            try:
                if wap_visible(built) and _wap_build(db, built):
                    for source in built['input_refs'].values():
                        add('wap_build', source, built['stage'])
            except (KeyError, TypeError, ValueError, AttributeError, IndexError, QueryError, inventory.InventoryError):
                continue
        for publication in records['wap_publications']:
            try:
                built = builds.get(publication.get('build_id'))
                if built is None or not wap_visible(built) or not publication_visible(publication):
                    continue
                if _wap_publication(db, publication, builds, audits):
                    add('wap_publication', publication['stage'], publication['target'])
            except (KeyError, TypeError, ValueError, AttributeError, IndexError, QueryError, inventory.InventoryError):
                continue
        for run in runs:
            try:
                if not visible(run):
                    continue
                if run.get('kind') in ('plan', 'run'):
                    plan = run if run['kind'] == 'plan' else db_dbt._check_plan(db, run)
                    if not visible(plan):
                        continue
                    if plan.get('state') != 'READY' or db_dbt._hash_plan(plan) != plan['_plan_digest']:
                        continue
                    nodes = {m['unique_id']: m for m in plan['models']}
                    # Validate every typed source and target schema before definitions.
                    metadata._schema(plan['target_schema'])
                    definition_schemas.add(plan['target_schema'])
                    bindings = {}
                    for binding in plan['source_bindings']:
                        ref = _ref(binding['ref'])
                        if ref['database_id'] != context.database_id:
                            raise ValueError()
                        metadata._schema(ref['schema_id'])
                        bindings[binding['unique_id']] = ref
                    for ref in bindings.values():
                        measure(ref)
                    closed = {}
                    if run.get('kind') == 'run' and run.get('executor') == 'native_graph':
                        for uid, node in nodes.items():
                            if node.get('materialized') in ('table', 'view'):
                                proof = graph.child_proof(db, run, uid, 'build', historical=True, settle=False)
                                if proof:
                                    closed[uid] = proof['candidate']['ref']
                    elif run.get('kind') == 'run' and run.get('executor') == 'native_table':
                        links = list(db.execute("SELECT operation_id FROM dbt_run_operations WHERE run_id=? AND phase='build'", (run['id'],)))
                        if len(links) == 1 and _closed_child(db, links[0][0], settle=False):
                            proof = db_dbt._native_saved(db, run, links[0][0])
                            if proof:
                                closed[run['selected_node_id']] = proof['candidate']['ref']
                    bindings.update(closed)
                    def declared_sources(uid, seen):
                        if uid in seen:
                            return []
                        if uid in bindings:
                            return [bindings[uid]]
                        node = nodes.get(uid, {})
                        return [ref for dep in node.get('depends_on', [])
                            for ref in declared_sources(dep, seen | {uid})]
                    for uid, node in nodes.items():
                        materialization = node.get('materialized')
                        if materialization not in ('table', 'view'):
                            continue
                        model = dict(id=uid, plan_id=plan['id'], materialization=materialization)
                        if uid in closed:
                            target = dict(ref=closed[uid])
                            model['run_id'] = run['id']
                        else:
                            # A planned name/locator is display text, never physical identity.
                            relation = node.get('relation')
                            label = '.'.join(relation) if isinstance(relation, list) and all(isinstance(v, str) for v in relation) else node.get('name', uid)
                            target = dict(kind='LOGICAL', label=label, unique_id=uid,
                                materialization=materialization)
                        for dependency in node.get('depends_on', []):
                            for source in declared_sources(dependency, {uid}):
                                add('declared_model', dict(ref=source), target, model)
                if run.get('kind') == 'publication':
                    parent = by_id.get(run.get('parent_run_id'))
                    if parent is None and isinstance(run.get('parent_run_id'), str):
                        row = db.execute('SELECT record FROM dbt_runs WHERE id=? AND database_id=?',
                            (run['parent_run_id'], context.database_id)).fetchone()
                        parent = json.loads(row[0]) if row else None
                    if parent is None or not visible(parent) or not visible(db_dbt._check_plan(db, parent)):
                        continue
                    receipt = db_dbt._native_publication_saved(db, run, settle=False)
                    if receipt:
                        add('native_publication', run['_native_intent']['candidate'], receipt['target'])
                        for entry in run['_native_intent'].get('tables', []) + run['_native_intent'].get('views', []):
                            frozen = entry['receipt']
                            for source in frozen['pins'].values():
                                add('native_graph', source, frozen['candidate'])
                elif run.get('kind') == 'run' and run.get('executor') == 'native_table':
                    links = list(db.execute("SELECT operation_id FROM dbt_run_operations WHERE run_id=? AND phase='build'",
                        (run['id'],)))
                    if len(links) == 1 and _closed_child(db, links[0][0], settle=False):
                        receipt = db_dbt._native_saved(db, run, links[0][0])
                        if receipt:
                            for source in receipt['pins'].values():
                                add('native_graph', source, receipt['candidate'])
                elif run.get('kind') == 'run' and run.get('executor') == 'native_graph':
                    for model in run['models']:
                        if model.get('materialized') not in ('table', 'view'):
                            continue
                        receipt = graph.child_proof(db, run, model['unique_id'], 'build', historical=True, settle=False)
                        if receipt:
                            for source in receipt['pins'].values():
                                add('native_graph', source, receipt['candidate'])
            except (KeyError, TypeError, ValueError, AttributeError, IndexError, QueryError, inventory.InventoryError):
                continue
    confirmed, declared = [], []
    guards = {json.dumps(focus_ref, sort_keys=True): (focus_ref, guard)}
    for category, source, target, model in candidates:
        if len(confirmed) + len(declared) >= EDGE_LIMIT:
            break
        try:
            points = (source, target)
            refs = [_ref(p['ref']) for p in points if 'ref' in p]
            # Authorize the entire fact before loading any endpoint metadata.
            for ref in refs:
                if ref['database_id'] != context.database_id:
                    raise ValueError()
                metadata._schema(ref['schema_id'])
            endpoints, fact_guards, currentness = [], [], []
            for measurement in points:
                if 'ref' not in measurement:
                    if category != 'declared_model' or measurement.get('kind') != 'LOGICAL':
                        raise ValueError()
                    endpoints.append(measurement)
                    continue
                ref = _ref(measurement['ref'])
                current, check = measure(ref)
                fact_guards.append((ref, check))
                endpoint = current if category == 'declared_model' else _endpoint(current, check, measurement)
                endpoints.append(endpoint)
                if category != 'declared_model':
                    currentness.append(_at_current(current, check, endpoint, measurement))
            fact = dict(category=category, source=endpoints[0], target=endpoints[1])
            if category == 'declared_model':
                fact['model'] = model
                declared.append(fact)
            else:
                fact['temporal'] = 'current' if all(currentness) else 'historical'
                confirmed.append(fact)
            for ref, check in fact_guards:
                guards[json.dumps(ref, sort_keys=True)] = (ref, check)
        except (KeyError, TypeError, ValueError, QueryError, inventory.InventoryError):
            continue
    # Revalidate all measurements used by definitions, as well as projected endpoints.
    for key, (current, check) in measured.items():
        guards[key] = (current['ref'], check)
    metadata._context()
    for schema_id in definition_schemas:
        metadata._schema(schema_id)
    for ref, expected in guards.values():
        metadata._schema(ref['schema_id'])
    for ref, expected in guards.values():
        if _current(metadata, ref)[1] != expected:
            raise QueryError('STALE_RESOURCE', 409)
    metadata._context()
    metadata._schema(context.schema_id)
    return dict(focus=focus, confirmed=confirmed, declared=declared,
        coverage=dict(scope='visible_saved_records', limited=True,
            empty_reason=None if confirmed or declared else 'no_visible_saved_evidence'))
