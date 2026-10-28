"""Typed local registry records. No engine resolution or execution lives here.

Writes use the caller's control_store transaction; they never commit it.
Configuration snapshots contain reference names, never credential material.
"""
from dataclasses import asdict, dataclass
import json
import re
from typing import Optional, Tuple
import uuid


class RevisionConflict(RuntimeError):
    pass


def validate_sql_name(name):
    if (not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,30}', name)
            or name in {'system', 'information_schema', 'tpch', 'tpcds'}):
        raise ValueError('SQL name must be 1..31 lowercase letters, digits, or underscores; reserved names are excluded')
    return name


def quote_identifier(name):
    if not isinstance(name, str) or not name or '\x00' in name:
        raise ValueError('identifier must be nonempty text without NUL')
    return '"' + name.replace('"', '""') + '"'


def _text(*values):
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError('identity and reference fields require nonempty text')


@dataclass(frozen=True)
class QueryContext:
    database_id: str
    schema_id: str
    warehouse_id: str
    generation_id: Optional[str]

    def __post_init__(self):
        _text(self.database_id, self.schema_id, self.warehouse_id)
        if self.generation_id is not None:
            _text(self.generation_id)


@dataclass(frozen=True)
class ResourceRef:
    database_id: str
    schema_id: str
    object_id: str
    incarnation: int

    def __post_init__(self):
        _text(self.database_id, self.schema_id, self.object_id)
        if type(self.incarnation) is not int or self.incarnation < 1:
            raise ValueError('incarnation must be a positive integer')


@dataclass(frozen=True)
class DestinationLocator:
    database_id: str
    schema_id: str
    physical_name: str

    def __post_init__(self):
        _text(self.database_id, self.schema_id, self.physical_name)


@dataclass(frozen=True)
class Database:
    id: str
    sql_catalog: str
    display_name: str
    description: str
    managed_by: str
    state: str
    revision: int


@dataclass(frozen=True)
class CatalogBinding:
    database_id: str
    polaris_service_ref: str
    polaris_realm: str
    polaris_catalog: str
    storage_profile_ref: str
    credential_profile_ref: str
    base_location: str
    desired_revision: int
    applied_revision: int
    applied_generation_id: Optional[str]
    revision: int


@dataclass(frozen=True)
class Schema:
    id: str
    database_id: str
    sql_name: str
    display_name: str
    purpose: str
    managed_by: str
    revision: int


@dataclass(frozen=True)
class Grant:
    principal_id: str
    database_id: str
    maximum_role: str
    revision: int


@dataclass(frozen=True)
class ObjectBinding:
    id: str
    schema_id: str
    physical_name: str
    kind: str
    backend_identity: str
    incarnation: int
    display_name: str
    current: bool


def _immutable(value):
    if isinstance(value, tuple):
        for item in value:
            _immutable(item)
    elif type(value) not in (str, int, bool, type(None)):
        raise ValueError('snapshots require immutable tuples and scalar values')


@dataclass(frozen=True)
class WarehouseConfigGeneration:
    id: str
    warehouse_id: str
    apply_id: str
    database_revisions: Tuple[Tuple[str, int], ...]
    grant_revisions: Tuple[Tuple[str, str, int], ...]
    configuration_refs: Tuple[str, ...]
    state: str

    def __post_init__(self):
        for value in (self.database_revisions, self.grant_revisions, self.configuration_refs):
            if not isinstance(value, tuple):
                raise ValueError('snapshots require tuples')
            _immutable(value)


@dataclass(frozen=True)
class Operation:
    id: str
    actor: str
    action: str
    targets: Tuple[ResourceRef, ...]
    expected_revision: Optional[int]
    state: str
    query_ids: Tuple[str, ...]
    apply_id: Optional[str]
    before_after: Tuple[Tuple[str, str], ...]
    timestamps: Tuple[Tuple[str, str], ...]

    def __post_init__(self):
        for value in (self.query_ids, self.before_after, self.timestamps):
            if not isinstance(value, tuple):
                raise ValueError('snapshots require tuples')
            _immutable(value)
        if (not isinstance(self.targets, tuple)
                or any(not isinstance(target, ResourceRef) for target in self.targets)):
            raise ValueError('operation targets require ResourceRef values')


@dataclass(frozen=True)
class QueryRecord:
    id: str
    actor: str
    context: QueryContext
    state: str
    created_at: str

    def __post_init__(self):
        if not isinstance(self.context, QueryContext):
            raise ValueError('query context requires QueryContext')


@dataclass(frozen=True)
class AdmissionRecord:
    id: str
    query_id: str
    resource: ResourceRef
    state: str
    created_at: str

    def __post_init__(self):
        if not isinstance(self.resource, ResourceRef):
            raise ValueError('admission resource requires ResourceRef')


def _insert(db, table, values):
    # Table names are private constants supplied only by the typed functions below.
    db.execute('INSERT INTO ' + table + ' VALUES (' + ','.join('?' for _ in values) + ')', values)


def _get(db, sql, parameters, record):
    row = db.execute(sql, parameters).fetchone()
    return record(**dict(row)) if row is not None else None


def _cas(cursor):
    if cursor.rowcount != 1:
        raise RevisionConflict('record missing or revision changed')


def create_database(db, sql_catalog, display_name, description, managed_by):
    record = Database(str(uuid.uuid4()), validate_sql_name(sql_catalog), display_name,
                      description, managed_by, 'registered', 1)
    _insert(db, 'databases', tuple(vars(record).values()))
    return record


def get_database(db, database_id):
    return _get(db, 'SELECT * FROM databases WHERE id=?', (database_id,), Database)


def retire_database_state(db, database_id, expected_revision, *, finalize=False):
    """Nonpurge state CAS; caller owns the IMMEDIATE transaction."""
    if not db.in_transaction:
        raise RevisionConflict('database retirement requires a transaction')
    if type(expected_revision) is not int or expected_revision < 1:
        raise ValueError('expected revision must be a positive integer')
    previous, following = ('retiring', 'retired') if finalize else ('READY', 'retiring')
    _cas(db.execute('''UPDATE databases SET state=?,revision=revision+1
        WHERE id=? AND state=? AND revision=?''',
        (following, database_id, previous, expected_revision)))
    return get_database(db, database_id)


def update_database_display(db, database_id, expected_revision, display_name, description):
    _cas(db.execute('''UPDATE databases SET display_name=?, description=?, revision=revision+1
                       WHERE id=? AND revision=?''',
                    (display_name, description, database_id, expected_revision)))
    return get_database(db, database_id)


def create_schema(db, database_id, sql_name, display_name, purpose, managed_by):
    record = Schema(str(uuid.uuid4()), database_id, validate_sql_name(sql_name),
                    display_name, purpose, managed_by, 1)
    _insert(db, 'schemas', tuple(vars(record).values()))
    return record


def get_schema(db, schema_id):
    return _get(db, 'SELECT * FROM schemas WHERE id=?', (schema_id,), Schema)


def update_schema_display(db, schema_id, expected_revision, display_name):
    _cas(db.execute('''UPDATE schemas SET display_name=?, revision=revision+1
                       WHERE id=? AND revision=?''',
                    (display_name, schema_id, expected_revision)))
    return get_schema(db, schema_id)


def create_binding(db, database_id, polaris_service_ref, polaris_realm, polaris_catalog,
                   storage_profile_ref, credential_profile_ref, base_location):
    _text(polaris_service_ref, polaris_realm, polaris_catalog, storage_profile_ref,
          credential_profile_ref, base_location)
    record = CatalogBinding(database_id, polaris_service_ref, polaris_realm, polaris_catalog,
                            storage_profile_ref, credential_profile_ref, base_location, 1, 0, None, 1)
    _insert(db, 'catalog_bindings', tuple(vars(record).values()))
    return record


def get_binding(db, database_id):
    return _get(db, 'SELECT * FROM catalog_bindings WHERE database_id=?', (database_id,), CatalogBinding)


def update_binding(db, database_id, expected_revision, applied_revision, applied_generation_id):
    """CAS on the row revision; recording applied metadata leaves desired revision intact."""
    _cas(db.execute('''UPDATE catalog_bindings SET revision=revision+1,
                       applied_revision=?, applied_generation_id=?
                       WHERE database_id=? AND revision=?''',
                    (applied_revision, applied_generation_id, database_id, expected_revision)))
    return get_binding(db, database_id)


def observe_object(db, schema_id, physical_name, kind, backend_identity, display_name):
    """Record a supplied backend identity; retired identities cannot become current again."""
    _text(schema_id, physical_name, kind, backend_identity)
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    db.execute('SAVEPOINT object_observation')
    try:
        previous = db.execute('''SELECT * FROM object_bindings
                                 WHERE schema_id=? AND physical_name=? ORDER BY incarnation DESC''',
                              (schema_id, physical_name)).fetchall()
        current = next((row for row in previous if row['current']), None)
        if current and current['backend_identity'] == backend_identity:
            if current['kind'] != kind:
                raise RevisionConflict('kind changed for the same backend identity')
            db.execute('UPDATE object_bindings SET display_name=? WHERE id=?', (display_name, current['id']))
            result = get_object(db, current['id'])
        else:
            if any(row['backend_identity'] == backend_identity for row in previous):
                raise RevisionConflict('retired backend identity cannot be rebound')
            incarnation = previous[0]['incarnation'] + 1 if previous else 1
            if current:
                db.execute('UPDATE object_bindings SET current=0 WHERE id=?', (current['id'],))
            result = ObjectBinding(str(uuid.uuid4()), schema_id, physical_name, kind,
                                   backend_identity, incarnation, display_name, True)
            _insert(db, 'object_bindings', tuple(vars(result).values()))
        db.execute('RELEASE object_observation')
        return result
    except BaseException:
        db.execute('ROLLBACK TO object_observation')
        db.execute('RELEASE object_observation')
        raise


def get_object(db, object_id):
    return _get(db, 'SELECT * FROM object_bindings WHERE id=? AND current=1', (object_id,), ObjectBinding)


def record_grant(db, grant):
    _insert(db, 'grants', tuple(vars(grant).values()))


def get_grant(db, principal_id, database_id):
    return _get(db, 'SELECT * FROM grants WHERE principal_id=? AND database_id=?',
                (principal_id, database_id), Grant)


def _grant_values(principal_id, database_id, maximum_role):
    _text(principal_id, database_id)
    if maximum_role not in ('bi_viewer', 'data_engineer', 'admin', 'revoked'):
        raise ValueError('invalid database grant role')


def create_grant(db, principal_id, database_id, maximum_role):
    _grant_values(principal_id, database_id, maximum_role)
    grant = Grant(principal_id, database_id, maximum_role, 1)
    record_grant(db, grant)
    return grant


def update_grant(db, principal_id, database_id, expected_revision, maximum_role):
    _grant_values(principal_id, database_id, maximum_role)
    if type(expected_revision) is not int or expected_revision < 1:
        raise ValueError('expected revision must be a positive integer')
    _cas(db.execute('''UPDATE grants SET maximum_role=?, revision=revision+1
                       WHERE principal_id=? AND database_id=? AND revision=?''',
                    (maximum_role, principal_id, database_id, expected_revision)))
    return get_grant(db, principal_id, database_id)


def _json(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False, default=asdict)


def _tuples(value):
    return tuple(_tuples(item) for item in value) if isinstance(value, list) else value


def record_generation(db, generation):
    _insert(db, 'warehouse_config_generations',
            (generation.id, generation.warehouse_id, generation.apply_id,
             _json(generation.database_revisions), _json(generation.grant_revisions),
             _json(generation.configuration_refs), generation.state))


def get_generation(db, generation_id):
    row = db.execute('SELECT * FROM warehouse_config_generations WHERE id=?', (generation_id,)).fetchone()
    if row is None:
        return None
    fields = dict(row)
    for name in ('database_revisions', 'grant_revisions', 'configuration_refs'):
        fields[name] = _tuples(json.loads(fields[name]))
    return WarehouseConfigGeneration(**fields)


def record_operation(db, operation):
    _insert(db, 'operations', (operation.id, operation.actor, operation.action, _json(operation.targets),
            operation.expected_revision, operation.state, _json(operation.query_ids), operation.apply_id,
            _json(operation.before_after), _json(operation.timestamps)))


def get_operation(db, operation_id):
    row = db.execute('SELECT * FROM operations WHERE id=?', (operation_id,)).fetchone()
    if row is None:
        return None
    fields = dict(row)
    fields['targets'] = tuple(ResourceRef(**item) for item in json.loads(fields['targets']))
    for name in ('query_ids', 'before_after', 'timestamps'):
        fields[name] = _tuples(json.loads(fields[name]))
    return Operation(**fields)


def record_query(db, query):
    context = query.context
    _insert(db, 'query_records', (query.id, query.actor, context.database_id, context.schema_id,
            context.warehouse_id, context.generation_id, query.state, query.created_at))


def get_query(db, query_id):
    row = db.execute('SELECT * FROM query_records WHERE id=?', (query_id,)).fetchone()
    if row is None:
        return None
    return QueryRecord(row['id'], row['actor'], QueryContext(row['database_id'], row['schema_id'],
                       row['warehouse_id'], row['generation_id']), row['state'], row['created_at'])


def record_admission(db, admission):
    resource = admission.resource
    _insert(db, 'admission_records', (admission.id, admission.query_id, resource.database_id,
            resource.schema_id, resource.object_id, resource.incarnation, admission.state, admission.created_at))


@dataclass(frozen=True)
class QueryExecutionContext:
    query_id: str
    selected_role: str
    mode: str
    sql_catalog: str
    sql_schema: str
    binding_revision: int
    grant_revision: Optional[int]
    backend_username: str
    credential_snapshot_ref: str
    backend_query_id: Optional[str]
    request_id: Optional[str]
    worksheet_id: Optional[str]
    sql: str = ''


def record_query_execution(db, execution):
    if not isinstance(execution, QueryExecutionContext) or execution.backend_query_id is not None:
        raise ValueError('new execution requires an immutable unbound context')
    _insert(db, 'query_execution_contexts', tuple(vars(execution).values()))


def get_query_execution(db, query_id):
    return _get(db, 'SELECT * FROM query_execution_contexts WHERE query_id=?',
                (query_id,), QueryExecutionContext)


def set_query_backend_id(db, query_id, backend_id):
    import warehouse_admission
    if warehouse_admission.query_closure(db, query_id):
        raise RevisionConflict('execution closed; historical outcome is immutable')
    _text(backend_id)
    execution = get_query_execution(db, query_id)
    if execution is None or execution.backend_query_id not in (None, backend_id):
        raise RevisionConflict('query backend identity is immutable')
    db.execute('UPDATE query_execution_contexts SET backend_query_id=? WHERE query_id=? AND backend_query_id IS NULL',
               (backend_id, query_id))


def set_query_state(db, query_id, state):
    import warehouse_admission
    if warehouse_admission.query_closure(db, query_id):
        raise RevisionConflict('execution closed; historical outcome is immutable')
    if state not in {'QUEUED', 'RUNNING', 'FINISHED', 'FAILED', 'CANCELED', 'UNKNOWN'}:
        raise ValueError('invalid query state')
    changed = db.execute('''UPDATE query_records SET state=? WHERE id=? AND NOT EXISTS
        (SELECT 1 FROM warehouse_admissions a WHERE a.kind='query' AND a.subject_id=query_records.id
         AND a.execution_closure!='')''', (state, query_id))
    if changed.rowcount == 0 and warehouse_admission.query_closure(db, query_id):
        raise RevisionConflict('execution closed; historical outcome is immutable')


def get_admission(db, admission_id):
    row = db.execute('SELECT * FROM admission_records WHERE id=?', (admission_id,)).fetchone()
    if row is None:
        return None
    return AdmissionRecord(row['id'], row['query_id'], ResourceRef(row['database_id'], row['schema_id'],
                           row['object_id'], row['incarnation']), row['state'], row['created_at'])
