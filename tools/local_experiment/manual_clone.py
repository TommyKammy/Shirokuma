"""One explicit, durable physical snapshot copy into a new SANDBOX table."""
from dataclasses import asdict
import json
import re

import data_management as registry
import dataops_service as ds
import manual_wap
from query_context import QueryError


def _authorize(execution, source, destination):
    context = execution._context()
    if context.selected_role not in ('data_engineer', 'admin'):
        raise QueryError('SQL_PERMISSION_DENIED', 403)
    # Check BOTH permissions before the first catalog request.
    if source.database_id != context.database_id or destination.database_id != context.database_id:
        raise QueryError('CROSS_DATABASE_TARGET', 409)
    execution._schema(source.schema_id)
    target = execution._schema(destination.schema_id, write=True)
    if (target.sql_name != 'sandbox' or target.purpose != 'sandbox'
            or context.schema_id != destination.schema_id):
        raise QueryError('CLONE_SANDBOX_CONTEXT_REQUIRED', 409)
    execution._locator(destination)


def _logical_type(value):
    """CTAS preserves names/types, not Iceberg IDs, docs or nullability."""
    if isinstance(value, str):
        return value
    kind = value['type']
    if kind == 'struct':
        return (kind, tuple((f['name'], _logical_type(f['type'])) for f in value['fields']))
    if kind == 'list':
        return (kind, _logical_type(value['element']))
    if kind == 'map':
        return (kind, _logical_type(value['key']), _logical_type(value['value']))
    return kind


def clone_table(principal, context, *, key, source, destination, snapshot_id=None):
    observation = _clone_table(principal, context, key=key, source=source,
        destination=destination, snapshot_id=snapshot_id)
    ds._validated_context(principal, context)
    return observation


def _clone_table(principal, context, *, key, source, destination, snapshot_id=None):
    if type(source) is not registry.ResourceRef or type(destination) is not registry.DestinationLocator:
        raise ValueError('explicit source reference and destination locator required')
    if snapshot_id is not None and (type(snapshot_id) is not str or not re.fullmatch('[0-9]+', snapshot_id)):
        raise ValueError('ASCII decimal snapshot required')
    execution = ds.begin(principal, context, route='manual-table-clone', idempotency_key=key,
        payload=dict(source=asdict(source), destination=asdict(destination), snapshot_id=snapshot_id))
    if execution.replayed:
        return execution.inspect()
    with ds._accepted(execution):
        try:
            _authorize(execution, source, destination)
            execution._active()
            execution._target_claim(destination)
            try:
                measured = execution.export_measure(source, snapshot_id=snapshot_id)
            except QueryError as error:
                if error.code in ('VIEW_EXPORT_NOT_SUPPORTED', 'EXPORT_SNAPSHOT_UNAVAILABLE'):
                    raise QueryError({'VIEW_EXPORT_NOT_SUPPORTED': 'VIEW_CLONE_NOT_SUPPORTED',
                        'EXPORT_SNAPSHOT_UNAVAILABLE': 'CLONE_SNAPSHOT_UNAVAILABLE'}[error.code], error.status) from None
                raise
            result = dict(source=measured, destination=asdict(destination),
                semantics='PHYSICAL_SNAPSHOT_ROW_COPY', phase='PENDING', target=None)
            manual_wap._receipt_intent(execution, result)
            generated = manual_wap._compile_model('SELECT * FROM clone_source', {'clone_source': source},
                execution=execution, measurements={'clone_source': measured})
            execution._absent(destination)
            _authorize(execution, source, destination)
            execution.export_measure(source, measured)
            # commit_result is atomically recorded with the successful SQL child,
            # before any optional catalog observation. The target is never owned.
            result = dict(result, phase='COPIED_UNVERIFIED')
            execution._execute('clone_create', 'CREATE TABLE ' + execution._locator(destination) +
                " WITH (format='PARQUET') AS " + generated, 'clone', commit_result=result)
            try:
                execution.export_measure(source, measured)
                ref = execution._observe(destination)
                result = dict(result, target_ref=asdict(ref))
                target = execution.export_measure(ref)
                if [(c['name'], _logical_type(c['type'])) for c in target['columns']] != [(c['name'], _logical_type(c['type'])) for c in measured['columns']]:
                    raise QueryError('CLONE_TARGET_SCHEMA_MISMATCH', 409)
                result = dict(result, target=target, phase='VERIFIED')
            except Exception as error:
                result = dict(result, warning=getattr(error, 'code', type(error).__name__))
            return execution.complete(result)
        except Exception as error:
            row = execution._row()
            if row['committed']:
                result = dict(json.loads(row['result']), warning=getattr(error, 'code', type(error).__name__))
                manual_wap._receipt_intent(execution, result)
            if row['state'] == 'ACTIVE':
                try:
                    if row['committed']:
                        return execution.complete()
                    execution.fail()
                except Exception:
                    manual_wap._settle_definite(execution, context, error)
            if row['committed'] or row['state'] == 'UNKNOWN':
                return execution._inspect()
            raise
