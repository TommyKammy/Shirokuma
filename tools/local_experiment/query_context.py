"""Resolve explicit immutable query selections against the committed registry."""
from dataclasses import asdict, dataclass
from typing import Optional
import control_store
import data_management as registry
import request_auth


class QueryError(RuntimeError):
    def __init__(self, code, status=400, details=None, message=None):
        self.code, self.status, self.details = code, status, details or {}
        self.message = message or code.replace('_', ' ').capitalize()
        super().__init__(self.message)


@dataclass(frozen=True)
class ResolvedQueryContext:
    database_id: str
    schema_id: str
    warehouse_id: str
    sql_catalog: str
    sql_schema: str
    selected_role: str
    binding_revision: int
    generation_id: str
    grant_revision: Optional[int]

    def public_metadata(self):
        return asdict(self)


def revalidate_admission(db, principal, context, *, backend_username=None):
    """Local reads only, under the caller's BEGIN IMMEDIATE admission transaction."""
    try:
        decision = request_auth.authorize_database(principal, context.database_id,
            context.selected_role, expected_generation_id=context.generation_id, db=db)
        import logical_registry
        if logical_registry.creation_mode(db, context.database_id) == 'logical_v1':
            logical = logical_registry.resolve_binding(db, context.database_id, context.generation_id)
            if logical['warehouse_id'] != context.warehouse_id:
                raise PermissionError()
            identity = logical_registry.resolve_identity(db, principal.principal_id, context.database_id,
                context.selected_role, decision.grant_revision, decision.platform_admin)
            if backend_username is not None and identity['backend_username'] != backend_username:
                raise PermissionError()
        binding = registry.get_binding(db, context.database_id)
        schema = registry.get_schema(db, context.schema_id)
        if (decision.grant_revision != context.grant_revision or
                decision.sql_catalog != context.sql_catalog or binding is None or
                binding.desired_revision != context.binding_revision or schema is None or
                schema.database_id != context.database_id or schema.sql_name != context.sql_schema):
            raise PermissionError()
    except (PermissionError, ValueError):
        raise QueryError('DB_ACCESS_REVOKED', 403) from None


def resolve_context(principal, database_id, schema_id, selected_role, *,
                    warehouse_id='LOCAL', expected_binding_revision):
    return _resolve_context(principal, database_id, schema_id, selected_role,
        warehouse_id=warehouse_id, expected_binding_revision=expected_binding_revision)


def _resolve_context(principal, database_id, schema_id, selected_role, *,
                     warehouse_id, expected_binding_revision, decision=None):
    if (any(not isinstance(v, str) or not v.strip() for v in
            (database_id, schema_id, warehouse_id, selected_role)) or
            type(expected_binding_revision) is not int or expected_binding_revision < 1):
        raise QueryError('UNKNOWN_CONTEXT', 400)
    with control_store.connect() as db:
        db.execute('BEGIN')
        database = registry.get_database(db, database_id)
        schema = registry.get_schema(db, schema_id)
        binding = registry.get_binding(db, database_id)
        if database is None or schema is None or schema.database_id != database_id:
            raise QueryError('UNKNOWN_CONTEXT', 404)
        if binding is None or binding.desired_revision != expected_binding_revision:
            raise QueryError('STALE_BINDING', 409)
        import logical_registry
        if logical_registry.creation_mode(db, database_id) == 'logical_v1':
            try:
                logical = logical_registry.resolve_binding(db, database_id, binding.applied_generation_id)
                if logical['warehouse_id'] != warehouse_id:
                    raise ValueError()
                # Re-read current authority even when a backend snapshot was
                # supplied. Admission will compare its epoch before sending SQL.
                current_decision = request_auth.authorize_database(principal, database_id, selected_role,
                    expected_generation_id=logical['generation_id'], db=db)
                if decision is not None and decision != current_decision:
                    raise PermissionError()
                decision = current_decision
            except (ValueError, PermissionError):
                raise QueryError('DB_ACCESS_REVOKED', 403) from None
        generation = registry.get_generation(db, binding.applied_generation_id)
        import warehouse_admission
        ready = database.state == 'READY' or (
            type(principal) is request_auth.AuthenticatedPrincipal and warehouse_admission.continuing_database(
                db, principal.principal_id, database_id, binding.applied_generation_id))
        if (not ready or binding.applied_revision != binding.desired_revision or
                generation is None or generation.warehouse_id != warehouse_id):
            raise QueryError('STALE_BINDING', 409)
    try:
        if decision is None:
            decision = request_auth.authorize_database(principal, database_id, selected_role,
                expected_generation_id=generation.id)
        elif (decision.principal_id != principal.principal_id or decision.database_id != database_id or
              decision.selected_role != selected_role or decision.generation_id != generation.id or
              decision.sql_catalog != database.sql_catalog):
            raise QueryError('STALE_BINDING', 409)
    except QueryError:
        raise
    except (PermissionError, RuntimeError):
        raise QueryError('DB_ACCESS_REVOKED', 403) from None
    if (selected_role != 'admin' and
            (schema.purpose != schema.sql_name or
             schema.sql_name not in ('raw', 'sandbox', 'staging', 'marts') or
             selected_role == 'bi_viewer' and schema.sql_name != 'marts')):
        raise QueryError('SQL_PERMISSION_DENIED', 403)
    return ResolvedQueryContext(database_id, schema_id, warehouse_id, database.sql_catalog,
        schema.sql_name, selected_role, binding.desired_revision, generation.id, decision.grant_revision)
