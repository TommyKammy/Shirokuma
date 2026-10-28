"""Synthetic successful backend verification for service fixtures, never live state."""
import uuid
import data_management as dm
import grant_management as grants


def verified_grants(db):
    for binding in db.execute('SELECT * FROM catalog_bindings').fetchall():
        generation = binding['applied_generation_id']
        if generation is None:
            continue
        operation_id = str(uuid.uuid4())
        dm.record_operation(db, dm.Operation(operation_id, 'test-operator', 'WAREHOUSE_CONFIG_APPLY',
            (), None, 'APPLIED', (), generation, (), ()))
        spec = {'grant_revisions': [dict(r) for r in db.execute(
            'SELECT * FROM grants WHERE database_id=?', (binding['database_id'],))]}
        grants.promote(db, spec, generation, operation_id, 'test-operator', 'test:verified-backend')
