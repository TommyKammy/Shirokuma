"""Transactional bootstrap for the local control ledger (historically jobs.sqlite3)."""
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3


VERSION = 21

_CREATION_SCHEMA = '''
CREATE TABLE db_creations (
    id TEXT PRIMARY KEY, environment_id TEXT NOT NULL, warehouse_id TEXT NOT NULL,
    actor TEXT NOT NULL, enrollment_id TEXT NOT NULL, enrollment_revision INTEGER NOT NULL CHECK(enrollment_revision>0),
    database_id TEXT NOT NULL UNIQUE REFERENCES databases(id),
    create_operation_id TEXT NOT NULL UNIQUE REFERENCES operations(id),
    intent_json TEXT NOT NULL, child_ids_json TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision>0),
    reservation_active INTEGER NOT NULL CHECK(reservation_active IN (0,1)),
    terminal_outcome TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX db_creation_reservation ON db_creations(environment_id,warehouse_id) WHERE reservation_active=1;
CREATE INDEX db_creation_listing ON db_creations(environment_id,actor,created_at,id);
CREATE TABLE db_creation_phase_plans (
    id TEXT PRIMARY KEY, creation_id TEXT NOT NULL REFERENCES db_creations(id),
    phase TEXT NOT NULL CHECK(phase IN ('bootstrap','full')),
    purpose TEXT NOT NULL CHECK(purpose IN ('APPLY','VERIFY_RECOVERY')),
    sequence INTEGER NOT NULL CHECK(sequence>0),
    operation_id TEXT NOT NULL REFERENCES operations(id),
    generation_id TEXT NOT NULL REFERENCES warehouse_config_generations(id),
    predecessor_operation_id TEXT REFERENCES operations(id), delivery_digest TEXT NOT NULL,
    recovery_digest TEXT, private_ref TEXT NOT NULL, public_summary TEXT NOT NULL,
    approval_request_id TEXT REFERENCES db_creation_requests(id),
    preparation_request_id TEXT UNIQUE REFERENCES db_creation_requests(id),
    UNIQUE(creation_id,phase,purpose,sequence));
CREATE UNIQUE INDEX db_creation_apply_operation ON db_creation_phase_plans(operation_id) WHERE purpose='APPLY';
CREATE TABLE db_creation_requests (
    id TEXT PRIMARY KEY, environment_id TEXT NOT NULL, actor TEXT NOT NULL,
    route TEXT NOT NULL, idempotency_key TEXT NOT NULL, payload TEXT NOT NULL,
    creation_id TEXT REFERENCES db_creations(id), plan_id TEXT REFERENCES db_creation_phase_plans(id),
    action TEXT NOT NULL CHECK(action IN ('CREATE','PREPARE_PLAN','APPROVE','WITHDRAW','REPLAN','RECONCILE','RECOVER_VERIFY','STOP_PRINCIPAL','RELEASE_PRINCIPAL','OBSERVE')),
    ack TEXT NOT NULL, approval_state TEXT CHECK(approval_state IN ('VALID','REVOKED','CONSUMED')),
    dispatch_state TEXT NOT NULL CHECK(dispatch_state IN ('ACCEPTED','CLAIMED','COMPLETE','BLOCKED','UNKNOWN')),
    executor TEXT, owner_id TEXT, claim_id TEXT, gate_revision INTEGER, operation_revision INTEGER,
    revision_cursor INTEGER, stop_reason TEXT, private_receipt_ref TEXT,
    action_request_id TEXT REFERENCES warehouse_action_requests(id),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    requested_sequence INTEGER CHECK(requested_sequence>0),
    UNIQUE(environment_id,actor,route,idempotency_key));
CREATE TABLE db_creation_principal_gates (
    environment_id TEXT NOT NULL, principal_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('ACTIVE','STOPPED')),
    revision INTEGER NOT NULL CHECK(revision>0), reason_code TEXT,
    request_id TEXT REFERENCES db_creation_requests(id), updated_at TEXT NOT NULL,
    PRIMARY KEY(environment_id,principal_id));
'''

_RETIREMENT_SCHEMA = '''
CREATE TABLE IF NOT EXISTS database_retirements (
    operation_id TEXT PRIMARY KEY REFERENCES operations(id),
    database_id TEXT NOT NULL UNIQUE REFERENCES databases(id),
    revision INTEGER NOT NULL CHECK(revision > 0),
    state TEXT NOT NULL CHECK(state IN ('RETIRING','RETIRED')),
    stopped_schedule_ids TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS database_retirement_requests (
    actor TEXT NOT NULL, route TEXT NOT NULL, idempotency_key TEXT NOT NULL,
    payload TEXT NOT NULL, operation_id TEXT NOT NULL REFERENCES database_retirements(operation_id),
    response TEXT NOT NULL, PRIMARY KEY(actor,route,idempotency_key));
'''

_DB_SCHEDULE_SCHEMA = '''
CREATE TABLE IF NOT EXISTS db_schedules (
    id TEXT PRIMARY KEY, database_id TEXT NOT NULL REFERENCES databases(id),
    owner TEXT NOT NULL, selected_role TEXT NOT NULL, auth_mode TEXT NOT NULL,
    context TEXT NOT NULL, revision INTEGER NOT NULL,
    target_kind TEXT NOT NULL, target_id TEXT NOT NULL, target_revision INTEGER NOT NULL,
    spec TEXT NOT NULL, intent TEXT NOT NULL, enabled INTEGER NOT NULL,
    next_run_at TEXT, last_status TEXT, paused_reason TEXT);
CREATE TABLE IF NOT EXISTS db_schedule_ticks (
    id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL REFERENCES db_schedules(id),
    schedule_revision INTEGER NOT NULL, target_revision INTEGER NOT NULL,
    scheduled_for TEXT NOT NULL, status TEXT NOT NULL, code TEXT, run_id TEXT,
    context TEXT NOT NULL, auth_mode TEXT NOT NULL,
    UNIQUE(schedule_id,scheduled_for,schedule_revision,target_revision));
CREATE TABLE IF NOT EXISTS db_schedule_requests (
    actor TEXT NOT NULL, route TEXT NOT NULL, idempotency_key TEXT NOT NULL,
    payload TEXT NOT NULL, response TEXT NOT NULL,
    PRIMARY KEY(actor,route,idempotency_key));
CREATE TABLE IF NOT EXISTS db_alert_subjects (
    subject TEXT PRIMARY KEY, acceptance_order INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS db_alerts (
    id TEXT PRIMARY KEY, subject TEXT NOT NULL, database_id TEXT NOT NULL,
    owner TEXT NOT NULL, selected_role TEXT NOT NULL, context TEXT NOT NULL,
    job_id TEXT NOT NULL, job_revision INTEGER NOT NULL, step_position INTEGER NOT NULL,
    step_name TEXT NOT NULL, arguments TEXT NOT NULL, first_run_id TEXT NOT NULL,
    last_run_id TEXT NOT NULL, occurrences INTEGER NOT NULL, first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL, acknowledged_at TEXT, resolved_at TEXT, resolution TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS db_alert_open ON db_alerts(subject) WHERE resolved_at IS NULL;
'''

_MARTS_SCHEMA = '''
CREATE TABLE IF NOT EXISTS db_marts (
    id TEXT PRIMARY KEY, database_id TEXT NOT NULL REFERENCES databases(id),
    owner TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0),
    context TEXT NOT NULL, definition TEXT NOT NULL, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS db_mart_runs (
    id TEXT PRIMARY KEY, mart_id TEXT NOT NULL REFERENCES db_marts(id),
    record TEXT NOT NULL, admission_id TEXT NOT NULL UNIQUE REFERENCES warehouse_admissions(id));
CREATE TABLE IF NOT EXISTS db_mart_operations (
    run_id TEXT NOT NULL REFERENCES db_mart_runs(id), phase TEXT NOT NULL,
    operation_id TEXT NOT NULL UNIQUE REFERENCES dataops_operations(id),
    PRIMARY KEY(run_id,phase));
CREATE TABLE IF NOT EXISTS db_mart_requests (
    actor TEXT NOT NULL, route TEXT NOT NULL, idempotency_key TEXT NOT NULL,
    payload TEXT NOT NULL, response TEXT NOT NULL,
    PRIMARY KEY(actor,route,idempotency_key));
'''

_STAGE_SCANS_SCHEMA = '''
CREATE TABLE IF NOT EXISTS db_stage_scans (
    id TEXT PRIMARY KEY, stage_id TEXT NOT NULL REFERENCES db_stages(id),
    record TEXT NOT NULL, definition TEXT NOT NULL, admission_id TEXT NOT NULL,
    cancel_requested INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS db_stage_scan_items (
    id TEXT PRIMARY KEY, scan_id TEXT NOT NULL REFERENCES db_stage_scans(id),
    position INTEGER NOT NULL, record TEXT NOT NULL, input BLOB NOT NULL,
    content_key TEXT NOT NULL, UNIQUE(scan_id,position));
CREATE TABLE IF NOT EXISTS db_stage_content (
    content_key TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES db_stage_runs(id));
CREATE TABLE IF NOT EXISTS db_stage_scan_operations (
    scan_id TEXT NOT NULL REFERENCES db_stage_scans(id),
    item_id TEXT NOT NULL REFERENCES db_stage_scan_items(id), attempt INTEGER NOT NULL,
    operation_id TEXT NOT NULL UNIQUE REFERENCES dataops_operations(id),
    PRIMARY KEY(item_id,attempt));
'''

_STAGE_IMMUTABLE = '''CREATE TRIGGER db_stage_immutable BEFORE UPDATE ON db_stages
    WHEN NEW.id != OLD.id OR NEW.database_id != OLD.database_id
      OR NEW.owner != OLD.owner OR NEW.selected_role != OLD.selected_role
      OR NEW.directory != OLD.directory
      OR json_extract(NEW.context,'$.database_id') IS NOT json_extract(OLD.context,'$.database_id')
      OR json_extract(NEW.context,'$.schema_id') IS NOT json_extract(OLD.context,'$.schema_id')
      OR json_extract(NEW.context,'$.warehouse_id') IS NOT json_extract(OLD.context,'$.warehouse_id')
      OR json_extract(NEW.context,'$.selected_role') IS NOT json_extract(OLD.context,'$.selected_role')
      OR json_extract(NEW.context,'$.sql_catalog') IS NOT json_extract(OLD.context,'$.sql_catalog')
      OR json_extract(NEW.context,'$.sql_schema') IS NOT json_extract(OLD.context,'$.sql_schema')
    BEGIN SELECT RAISE(ABORT,'immutable Stage binding'); END'''

_DB_STAGES_SCHEMA = '''
CREATE TABLE db_stages (
    id TEXT PRIMARY KEY, database_id TEXT NOT NULL REFERENCES databases(id),
    owner TEXT NOT NULL, selected_role TEXT NOT NULL, name TEXT NOT NULL,
    directory TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0),
    context TEXT NOT NULL, definition TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(database_id,owner,name));
CREATE TABLE db_stage_claims (
    directory TEXT PRIMARY KEY, stage_id TEXT NOT NULL REFERENCES db_stages(id));
CREATE TABLE db_stage_runs (
    id TEXT PRIMARY KEY, stage_id TEXT NOT NULL REFERENCES db_stages(id),
    operation_id TEXT NOT NULL UNIQUE REFERENCES dataops_operations(id),
    record TEXT NOT NULL, definition TEXT NOT NULL, config_fingerprint TEXT NOT NULL, input BLOB);
CREATE TABLE db_stage_requests (
    actor TEXT NOT NULL, route TEXT NOT NULL, idempotency_key TEXT NOT NULL,
    payload TEXT NOT NULL, response TEXT NOT NULL,
    PRIMARY KEY(actor,route,idempotency_key));
CREATE TABLE db_stage_publications (
    stage_id TEXT NOT NULL REFERENCES db_stages(id), filename TEXT NOT NULL,
    run_id TEXT NOT NULL UNIQUE REFERENCES db_stage_runs(id),
    state TEXT NOT NULL, receipt TEXT,
    PRIMARY KEY(stage_id,filename));
'''

_WAP_SCHEMA = '''
CREATE TABLE wap_models (
    database_id TEXT NOT NULL REFERENCES databases(id), model_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version > 0), materialized_ref TEXT,
    current_publication_id TEXT, operation_id TEXT REFERENCES dataops_operations(id),
    PRIMARY KEY(database_id,model_id));
CREATE TABLE wap_model_versions (
    database_id TEXT NOT NULL, model_id TEXT NOT NULL, version INTEGER NOT NULL,
    actor TEXT NOT NULL, record TEXT NOT NULL,
    PRIMARY KEY(database_id,model_id,version),
    FOREIGN KEY(database_id,model_id) REFERENCES wap_models(database_id,model_id));
CREATE TABLE wap_builds (
    id TEXT PRIMARY KEY, database_id TEXT NOT NULL, model_id TEXT NOT NULL,
    version INTEGER NOT NULL, operation_id TEXT NOT NULL UNIQUE REFERENCES dataops_operations(id),
    record TEXT NOT NULL,
    FOREIGN KEY(database_id,model_id,version) REFERENCES wap_model_versions(database_id,model_id,version));
CREATE TABLE wap_audits (
    id TEXT PRIMARY KEY, database_id TEXT NOT NULL, model_id TEXT NOT NULL,
    version INTEGER NOT NULL, operation_id TEXT NOT NULL UNIQUE REFERENCES dataops_operations(id),
    record TEXT NOT NULL,
    FOREIGN KEY(database_id,model_id,version) REFERENCES wap_model_versions(database_id,model_id,version));
CREATE TABLE wap_publications (
    id TEXT PRIMARY KEY, database_id TEXT NOT NULL, model_id TEXT NOT NULL,
    version INTEGER NOT NULL, operation_id TEXT NOT NULL UNIQUE REFERENCES dataops_operations(id),
    record TEXT NOT NULL,
    FOREIGN KEY(database_id,model_id,version) REFERENCES wap_model_versions(database_id,model_id,version));
'''

# Keep the Jobs definitions intact; the five existing completion columns below
# are deliberately separate from these definitions.
_JOBS_SCHEMA = '''
CREATE TABLE IF NOT EXISTS jobs (
    name TEXT PRIMARY KEY, owner TEXT NOT NULL, definition TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stages (
    name TEXT PRIMARY KEY, owner TEXT NOT NULL, definition TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stage_files (
    stage_name TEXT NOT NULL, content_key TEXT NOT NULL, status TEXT NOT NULL,
    last_run_id TEXT NOT NULL, position INTEGER NOT NULL,
    PRIMARY KEY(stage_name,content_key));
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, job_name TEXT NOT NULL, actor TEXT NOT NULL,
    role TEXT NOT NULL, definition TEXT NOT NULL, arguments TEXT NOT NULL,
    status TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT,
    finished_at TEXT, error TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_job ON runs((1))
    WHERE status IN ('queued', 'running');
CREATE TABLE IF NOT EXISTS steps (
    run_id TEXT NOT NULL, position INTEGER NOT NULL, name TEXT NOT NULL,
    action TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT,
    finished_at TEXT, error TEXT, summary TEXT,
    PRIMARY KEY(run_id, position));
CREATE TABLE IF NOT EXISTS schedules (
    name TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
    every_minutes INTEGER, time TEXT, timezone TEXT, arguments TEXT NOT NULL,
    enabled INTEGER NOT NULL, role TEXT NOT NULL, auth_mode TEXT NOT NULL,
    next_run_at TEXT, last_status TEXT, last_run_id TEXT, last_scheduled_for TEXT);
CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY, job_name TEXT NOT NULL, step_position INTEGER NOT NULL,
    step_name TEXT NOT NULL, owner TEXT NOT NULL, severity TEXT NOT NULL,
    message TEXT NOT NULL, first_run_id TEXT NOT NULL, last_run_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, occurrences INTEGER NOT NULL,
    acknowledged_at TEXT, acknowledged_by TEXT, resolved_at TEXT, resolution TEXT,
    rule_key TEXT NOT NULL, arguments_key TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_alert ON alerts
    (job_name,step_position,owner,rule_key,arguments_key) WHERE resolved_at IS NULL;
'''

_REGISTRY_SCHEMA = '''
CREATE TABLE databases (
    id TEXT PRIMARY KEY NOT NULL, sql_catalog TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL, description TEXT NOT NULL, managed_by TEXT NOT NULL,
    state TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0));
CREATE TABLE warehouse_config_generations (
    id TEXT PRIMARY KEY NOT NULL, warehouse_id TEXT NOT NULL, apply_id TEXT NOT NULL,
    database_revisions TEXT NOT NULL, grant_revisions TEXT NOT NULL,
    configuration_refs TEXT NOT NULL, state TEXT NOT NULL,
    UNIQUE(id, warehouse_id));
CREATE TABLE catalog_bindings (
    database_id TEXT PRIMARY KEY NOT NULL REFERENCES databases(id),
    polaris_service_ref TEXT NOT NULL, polaris_realm TEXT NOT NULL,
    polaris_catalog TEXT NOT NULL, storage_profile_ref TEXT NOT NULL,
    credential_profile_ref TEXT NOT NULL, base_location TEXT NOT NULL,
    desired_revision INTEGER NOT NULL CHECK(desired_revision > 0),
    applied_revision INTEGER NOT NULL CHECK(applied_revision >= 0 AND applied_revision <= desired_revision),
    applied_generation_id TEXT REFERENCES warehouse_config_generations(id),
    revision INTEGER NOT NULL CHECK(revision > 0),
    UNIQUE(polaris_service_ref, polaris_realm, polaris_catalog));
CREATE TABLE schemas (
    id TEXT PRIMARY KEY NOT NULL, database_id TEXT NOT NULL REFERENCES databases(id),
    sql_name TEXT NOT NULL, display_name TEXT NOT NULL, purpose TEXT NOT NULL,
    managed_by TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0),
    UNIQUE(database_id, sql_name), UNIQUE(id, database_id));
CREATE TABLE grants (
    principal_id TEXT NOT NULL, database_id TEXT NOT NULL REFERENCES databases(id),
    maximum_role TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0),
    PRIMARY KEY(principal_id, database_id));
CREATE TABLE object_bindings (
    id TEXT PRIMARY KEY NOT NULL, schema_id TEXT NOT NULL REFERENCES schemas(id),
    physical_name TEXT NOT NULL, kind TEXT NOT NULL, backend_identity TEXT NOT NULL,
    incarnation INTEGER NOT NULL CHECK(incarnation > 0), display_name TEXT NOT NULL,
    current INTEGER NOT NULL CHECK(current IN (0,1)),
    UNIQUE(schema_id, physical_name, incarnation),
    UNIQUE(schema_id, physical_name, backend_identity), UNIQUE(id, schema_id, incarnation));
CREATE UNIQUE INDEX one_current_object ON object_bindings(schema_id, physical_name) WHERE current=1;
CREATE TABLE operations (
    id TEXT PRIMARY KEY NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
    targets TEXT NOT NULL, expected_revision INTEGER, state TEXT NOT NULL,
    query_ids TEXT NOT NULL, apply_id TEXT, before_after TEXT NOT NULL, timestamps TEXT NOT NULL);
CREATE TABLE query_records (
    id TEXT PRIMARY KEY NOT NULL, actor TEXT NOT NULL,
    database_id TEXT NOT NULL REFERENCES databases(id), schema_id TEXT NOT NULL,
    warehouse_id TEXT NOT NULL, generation_id TEXT, state TEXT NOT NULL, created_at TEXT NOT NULL,
    FOREIGN KEY(schema_id, database_id) REFERENCES schemas(id, database_id),
    FOREIGN KEY(generation_id, warehouse_id) REFERENCES warehouse_config_generations(id, warehouse_id),
    UNIQUE(id, database_id, schema_id));
CREATE TABLE admission_records (
    id TEXT PRIMARY KEY NOT NULL, query_id TEXT NOT NULL,
    database_id TEXT NOT NULL, schema_id TEXT NOT NULL, object_id TEXT NOT NULL,
    incarnation INTEGER NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL,
    FOREIGN KEY(query_id, database_id, schema_id) REFERENCES query_records(id, database_id, schema_id),
    FOREIGN KEY(schema_id, database_id) REFERENCES schemas(id, database_id),
    FOREIGN KEY(object_id, schema_id, incarnation) REFERENCES object_bindings(id, schema_id, incarnation));
'''


_QUERY_SCHEMA = '''CREATE TABLE query_execution_contexts (
    query_id TEXT PRIMARY KEY NOT NULL REFERENCES query_records(id),
    selected_role TEXT NOT NULL, mode TEXT NOT NULL,
    sql_catalog TEXT NOT NULL, sql_schema TEXT NOT NULL,
    binding_revision INTEGER NOT NULL, grant_revision INTEGER,
    backend_username TEXT NOT NULL, credential_snapshot_ref TEXT NOT NULL,
    backend_query_id TEXT, request_id TEXT, worksheet_id TEXT,
    sql TEXT NOT NULL)'''


_WAREHOUSE_SCHEMA = '''
CREATE TABLE warehouse_controls (
    warehouse_id TEXT PRIMARY KEY NOT NULL, state TEXT NOT NULL CHECK(state IN ('OPEN','DRAINING','APPLYING','UNKNOWN')),
    operation_id TEXT REFERENCES operations(id), revision INTEGER NOT NULL CHECK(revision > 0),
    applied_generation_id TEXT REFERENCES warehouse_config_generations(id));
CREATE TABLE warehouse_admissions (
    id TEXT PRIMARY KEY NOT NULL, warehouse_id TEXT NOT NULL REFERENCES warehouse_controls(warehouse_id),
    actor TEXT NOT NULL, kind TEXT NOT NULL, subject_id TEXT NOT NULL,
    parent_id TEXT REFERENCES warehouse_admissions(id), generation_id TEXT,
    state TEXT NOT NULL CHECK(state IN ('ACTIVE','FINISHED','FAILED','CANCELED','UNKNOWN')),
    created_at TEXT NOT NULL, finished_at TEXT, evidence_ref TEXT,
    UNIQUE(warehouse_id,kind,subject_id));
CREATE INDEX warehouse_blockers ON warehouse_admissions(warehouse_id,state);
CREATE TABLE generation_plans (
    generation_id TEXT PRIMARY KEY NOT NULL REFERENCES warehouse_config_generations(id),
    manifest TEXT NOT NULL, delivery_digest TEXT NOT NULL, secret_observations TEXT NOT NULL);
CREATE TABLE operation_requests (
    operation_id TEXT PRIMARY KEY NOT NULL REFERENCES operations(id),
    actor TEXT NOT NULL, route TEXT NOT NULL, idempotency_key TEXT NOT NULL,
    payload TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0),
    generation_id TEXT NOT NULL REFERENCES generation_plans(generation_id),
    initial_database_states TEXT NOT NULL, mutation_started INTEGER NOT NULL DEFAULT 0 CHECK(mutation_started IN (0,1)),
    secret_after TEXT, attempt_id TEXT,
    observation_failure TEXT CHECK(observation_failure IN
        ('STALE_SECRETS','SECRETS_NOT_VERIFIED','INVALID_SECRET_OBSERVATIONS','STALE_REGISTRY')),
    UNIQUE(actor,route,idempotency_key));
CREATE TABLE operation_steps (
    operation_id TEXT NOT NULL REFERENCES operations(id),
    step TEXT NOT NULL CHECK(step IN ('publish_snapshot','apply_secrets','reconcile_storage','reconcile_polaris','reconcile_trino','verify')),
    state TEXT NOT NULL CHECK(state IN ('PENDING','RUNNING','SUCCEEDED','UNKNOWN')),
    evidence_ref TEXT, updated_at TEXT NOT NULL,
    PRIMARY KEY(operation_id,step));
CREATE TABLE warehouse_action_requests (
    id TEXT PRIMARY KEY NOT NULL, operation_id TEXT NOT NULL REFERENCES operations(id),
    actor TEXT NOT NULL, route TEXT NOT NULL, idempotency_key TEXT NOT NULL,
    payload TEXT NOT NULL, created_at TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state='REQUESTED'),
    UNIQUE(actor,route,idempotency_key));
'''


def root():
    path = Path(os.environ.get('SHIROKUMA_STATE_DIR') or
                Path(__file__).resolve().parents[2] / 'var/shirokuma')
    path.mkdir(parents=True, exist_ok=True)
    return path


def _version(connection):
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if version > VERSION:
        raise RuntimeError('unsupported control store version: ' + str(version))
    return version


def _bootstrap(connection):
    if _version(connection) == VERSION and not _v14_repairs(connection):
        return
    connection.execute('BEGIN IMMEDIATE')
    # Another first opener may have completed bootstrap while we waited.
    if _version(connection) == 0:
        for statement in _JOBS_SCHEMA.split(';'):
            if statement.strip():
                connection.execute(statement)
        columns = {row['name'] for row in connection.execute('PRAGMA table_info(runs)')}
        if 'trigger' not in columns:
            connection.execute("ALTER TABLE runs ADD COLUMN trigger TEXT NOT NULL DEFAULT 'manual'")
        if 'scheduled_for' not in columns:
            connection.execute('ALTER TABLE runs ADD COLUMN scheduled_for TEXT')
        schedule_columns = {row['name'] for row in connection.execute('PRAGMA table_info(schedules)')}
        for column in ('date', 'weekdays', 'day_of_month'):
            if column not in schedule_columns:
                connection.execute(f'ALTER TABLE schedules ADD COLUMN {column} TEXT')
        for statement in _REGISTRY_SCHEMA.split(';'):
            if statement.strip():
                connection.execute(statement)
        connection.execute('PRAGMA user_version=1')
    if _version(connection) == 1:
        connection.execute(_QUERY_SCHEMA)
        connection.execute('PRAGMA user_version=2')
    if _version(connection) == 2:
        for statement in _WAREHOUSE_SCHEMA.split(';'):
            if statement.strip():
                connection.execute(statement)
        connection.execute('PRAGMA user_version=3')
    if _version(connection) == 3:
        connection.execute("ALTER TABLE operation_requests ADD COLUMN phase TEXT NOT NULL DEFAULT 'full' CHECK(phase IN ('bootstrap','full'))")
        connection.execute('ALTER TABLE operation_requests ADD COLUMN predecessor_id TEXT REFERENCES operation_requests(operation_id)')
        connection.execute('ALTER TABLE operation_requests ADD COLUMN secret_observations TEXT')
        connection.execute('''UPDATE operation_requests SET secret_observations=(SELECT secret_observations
                              FROM generation_plans WHERE generation_id=operation_requests.generation_id)''')
        connection.execute('''CREATE TABLE database_create_requests (
            actor TEXT NOT NULL, route TEXT NOT NULL, idempotency_key TEXT NOT NULL,
            payload TEXT NOT NULL, database_id TEXT NOT NULL REFERENCES databases(id),
            operation_id TEXT NOT NULL REFERENCES operations(id),
            PRIMARY KEY(actor,route,idempotency_key))''')
        connection.execute('PRAGMA user_version=4')
    if _version(connection) == 4:
        connection.execute('''CREATE TABLE registry_register_requests (
            actor TEXT NOT NULL, route TEXT NOT NULL, idempotency_key TEXT NOT NULL,
            payload TEXT NOT NULL, operation_id TEXT NOT NULL REFERENCES operations(id),
            PRIMARY KEY(actor,route,idempotency_key))''')
        connection.execute('PRAGMA user_version=5')
    if _version(connection) == 5:
        connection.execute('''CREATE TABLE dataops_operations (
            id TEXT PRIMARY KEY, actor TEXT NOT NULL, route TEXT NOT NULL,
            idempotency_key TEXT NOT NULL, payload TEXT NOT NULL, fingerprint TEXT NOT NULL,
            context TEXT NOT NULL, backend_username TEXT NOT NULL, credential_snapshot_ref TEXT NOT NULL,
            state TEXT NOT NULL, committed INTEGER NOT NULL DEFAULT 0,
            result TEXT, cleanup_error TEXT, created_at TEXT NOT NULL,
            UNIQUE(actor,route,idempotency_key))''')
        connection.execute('''CREATE TABLE dataops_queries (
            operation_id TEXT NOT NULL REFERENCES dataops_operations(id),
            step TEXT NOT NULL, query_id TEXT NOT NULL UNIQUE REFERENCES query_records(id),
            action TEXT NOT NULL, PRIMARY KEY(operation_id,step))''')
        connection.execute('''CREATE TABLE dataops_owned_objects (
            operation_id TEXT NOT NULL REFERENCES dataops_operations(id),
            object_id TEXT NOT NULL REFERENCES object_bindings(id),
            incarnation INTEGER NOT NULL, purpose TEXT NOT NULL CHECK(purpose IN ('stage','backup')),
            PRIMARY KEY(operation_id,object_id))''')
        connection.execute('PRAGMA user_version=6')
    if _version(connection) == 6:
        connection.execute('ALTER TABLE dataops_queries ADD COLUMN commit_result TEXT')
        connection.execute('''CREATE TABLE dataops_target_claims (
            catalog TEXT NOT NULL, schema_name TEXT NOT NULL, table_name TEXT NOT NULL,
            operation_id TEXT NOT NULL REFERENCES dataops_operations(id),
            PRIMARY KEY(catalog,schema_name,table_name))''')
        connection.execute('PRAGMA user_version=7')
    if _version(connection) == 7:
        for statement in _WAP_SCHEMA.split(';'):
            if statement.strip():
                connection.execute(statement)
        connection.execute('PRAGMA user_version=8')
    if _version(connection) == 8:
        connection.execute('''CREATE TABLE export_artifacts (
            export_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL UNIQUE REFERENCES dataops_operations(id),
            owner TEXT NOT NULL, context TEXT NOT NULL, record TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('INTENT','COMPLETE','FAILED')),
            relative_name TEXT NOT NULL, profile_ref TEXT NOT NULL)''')
        connection.execute('PRAGMA user_version=9')
    if _version(connection) == 9:
        import grant_management
        grant_management.migrate(connection)
        connection.execute('PRAGMA user_version=10')
    if _version(connection) == 10:
        connection.execute('''CREATE TABLE metadata_requests (
            operation_id TEXT PRIMARY KEY REFERENCES operations(id),
            actor TEXT NOT NULL, route TEXT NOT NULL, idempotency_key TEXT NOT NULL,
            payload TEXT NOT NULL, response TEXT NOT NULL,
            UNIQUE(actor,route,idempotency_key))''')
        connection.execute('''CREATE TABLE metadata_audit (
            operation_id TEXT PRIMARY KEY REFERENCES metadata_requests(operation_id),
            resource_type TEXT NOT NULL CHECK(resource_type IN ('database','schema')),
            resource_id TEXT NOT NULL, before_metadata TEXT NOT NULL,
            after_metadata TEXT NOT NULL)''')
        connection.execute('PRAGMA user_version=11')
    if _version(connection) == 11:
        for statement in '''
            CREATE TABLE db_jobs (
                id TEXT PRIMARY KEY, database_id TEXT NOT NULL REFERENCES databases(id),
                name TEXT NOT NULL, owner TEXT NOT NULL, selected_role TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision > 0), context TEXT NOT NULL,
                definition TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(database_id,name));
            CREATE TABLE db_job_runs (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES db_jobs(id),
                owner TEXT NOT NULL, selected_role TEXT NOT NULL, revision INTEGER NOT NULL,
                context TEXT NOT NULL, definition TEXT NOT NULL, arguments TEXT NOT NULL,
                inputs TEXT NOT NULL, backend_username TEXT NOT NULL, credential_snapshot_ref TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','canceled','unknown')),
                cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1)),
                created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT);
            CREATE UNIQUE INDEX one_active_db_job ON db_job_runs((1))
                WHERE status IN ('queued','running','unknown');
            CREATE TABLE db_job_steps (
                run_id TEXT NOT NULL REFERENCES db_job_runs(id), position INTEGER NOT NULL,
                name TEXT NOT NULL, action TEXT NOT NULL, status TEXT NOT NULL,
                summary TEXT, PRIMARY KEY(run_id,position));
            CREATE TABLE db_job_operations (
                run_id TEXT NOT NULL, position INTEGER NOT NULL,
                operation_id TEXT NOT NULL UNIQUE REFERENCES dataops_operations(id),
                FOREIGN KEY(run_id,position) REFERENCES db_job_steps(run_id,position),
                PRIMARY KEY(run_id,position,operation_id));
            CREATE TABLE db_job_requests (
                actor TEXT NOT NULL, route TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                payload TEXT NOT NULL, response TEXT NOT NULL,
                PRIMARY KEY(actor,route,idempotency_key));
        '''.split(';'):
            if statement.strip():
                connection.execute(statement)
        connection.execute('PRAGMA user_version=12')
    if _version(connection) == 12:
        connection.execute("""ALTER TABLE warehouse_admissions ADD COLUMN execution_closure
            TEXT NOT NULL DEFAULT '' CHECK(execution_closure IN ('','FENCED_READ_ONLY'))""")
        connection.execute('''CREATE TABLE execution_recovery_requests (
            operation_id TEXT PRIMARY KEY REFERENCES operations(id),
            actor TEXT NOT NULL, route TEXT NOT NULL, idempotency_key TEXT NOT NULL,
            payload TEXT NOT NULL, intent TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision > 0),
            phase TEXT NOT NULL CHECK(phase IN ('PLANNED','STOP_INTENT','PROVED','CLOSED','OPEN')),
            proof TEXT, control_revision INTEGER NOT NULL,
            UNIQUE(actor,route,idempotency_key))''')
        connection.execute('''CREATE TRIGGER recovery_immutable BEFORE UPDATE OF
            operation_id,actor,route,idempotency_key,payload,intent ON execution_recovery_requests
            BEGIN SELECT RAISE(ABORT,'immutable recovery intent'); END''')
        connection.execute('PRAGMA user_version=13')
    if _version(connection) == 13:
        import grant_management
        grant_management.migrate_database_admin(connection)
        for statement in _DB_STAGES_SCHEMA.split(';'):
            if statement.strip():
                connection.execute(statement)
        connection.execute(_STAGE_IMMUTABLE)
        connection.execute('''CREATE TRIGGER db_stage_claim_immutable BEFORE UPDATE ON db_stage_claims
            BEGIN SELECT RAISE(ABORT,'immutable Stage directory claim'); END''')
        connection.execute('''CREATE TRIGGER db_stage_claim_retained BEFORE DELETE ON db_stage_claims
            BEGIN SELECT RAISE(ABORT,'retained Stage directory claim'); END''')
        connection.execute('PRAGMA user_version=14')
    if _version(connection) >= 14:
        # Only interim synthetic v14 schemas need repair. Normal reads acquire
        # no writer transaction, and this never promotes authority or old proof.
        roles, stage_binding = _v14_repairs(connection, details=True)
        if roles:
            import grant_management
            grant_management.migrate_database_admin(connection)
        if stage_binding:
            connection.execute('DROP TRIGGER db_stage_immutable')
            connection.execute(_STAGE_IMMUTABLE)
    if _version(connection) == 14:
        for statement in _STAGE_SCANS_SCHEMA.split(';'):
            if statement.strip():
                connection.execute(statement)
        connection.execute('PRAGMA user_version=15')
    if _version(connection) == 15:
        for statement in _MARTS_SCHEMA.split(';'):
            if statement.strip():
                connection.execute(statement)
        connection.execute('PRAGMA user_version=16')
    if _version(connection) == 16:
        for statement in _DB_SCHEDULE_SCHEMA.split(';'):
            if statement.strip():
                connection.execute(statement)
        for kind, table in [('job', 'db_jobs'), ('stage', 'db_stages'), ('mart', 'db_marts')]:
            connection.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_pause_schedules AFTER UPDATE OF revision ON {table}
                WHEN NEW.revision != OLD.revision BEGIN
                UPDATE db_schedules SET enabled=0,next_run_at=NULL,last_status='paused',
                    paused_reason='TARGET_REVISION_CHANGED'
                    WHERE target_kind='{kind}' AND target_id=OLD.id;
                END''')
        connection.execute('''CREATE TRIGGER IF NOT EXISTS db_jobs_retire_alerts AFTER UPDATE OF revision ON db_jobs
            WHEN NEW.revision != OLD.revision BEGIN
            UPDATE db_alerts SET resolved_at=NEW.updated_at,resolution='definition_changed'
                WHERE job_id=OLD.id AND resolved_at IS NULL;
            END''')
        connection.execute('PRAGMA user_version=17')
    if _version(connection) == 17:
        columns = {row['name'] for row in connection.execute('PRAGMA table_info(warehouse_admissions)')}
        if 'database_id' not in columns:
            connection.execute('ALTER TABLE warehouse_admissions ADD COLUMN database_id TEXT REFERENCES databases(id)')
        for statement in _RETIREMENT_SCHEMA.split(';'):
            if statement.strip():
                connection.execute(statement)
        connection.execute('PRAGMA user_version=18')
    if _version(connection) == 18:
        for statement in _CREATION_SCHEMA.split(';'):
            if statement.strip():
                connection.execute(statement)
        connection.execute('''CREATE TRIGGER db_creation_request_immutable BEFORE UPDATE OF
            id,environment_id,actor,route,idempotency_key,payload,creation_id,plan_id,action,ack,
            gate_revision,operation_revision,requested_sequence ON db_creation_requests
            BEGIN SELECT RAISE(ABORT,'immutable creation request'); END''')
        connection.execute('PRAGMA user_version=19')
    if _version(connection) == 19:
        _migrate_logical(connection)
        connection.execute('PRAGMA user_version=20')
    if _version(connection) == 20:
        _migrate_observed_effects(connection)
        connection.execute('PRAGMA user_version=21')
    connection.commit()


def _migrate_observed_effects(db):
    """Expand effect outcomes inside bootstrap's transaction, retaining authority."""
    # No table references logical_effects, so it can be rebuilt with FKs enabled.
    objects = db.execute("""SELECT sql FROM sqlite_master
        WHERE tbl_name='logical_effects' AND type IN ('index','trigger')
            AND sql IS NOT NULL ORDER BY type,name""").fetchall()
    db.execute('''CREATE TABLE logical_effects_v21 (
        creation_id TEXT NOT NULL REFERENCES db_creations(id), effect_key TEXT NOT NULL,
        intent_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('INTENT','ACKNOWLEDGED','UNKNOWN','REJECTED','OBSERVED_APPLIED')),
        private_ref TEXT, outcome_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(creation_id,effect_key))''')
    db.execute('''INSERT INTO logical_effects_v21
        (rowid,creation_id,effect_key,intent_json,state,private_ref,outcome_json,created_at,updated_at)
        SELECT rowid,creation_id,effect_key,intent_json,state,private_ref,outcome_json,created_at,updated_at
        FROM logical_effects''')
    db.execute('DROP TABLE logical_effects')
    db.execute('ALTER TABLE logical_effects_v21 RENAME TO logical_effects')
    for row in objects:
        db.execute(row[0])


def _migrate_logical(db):
    """Add logical authority without rewriting any legacy receipt or generation."""
    db.execute("ALTER TABLE db_creations ADD COLUMN creation_mode TEXT NOT NULL DEFAULT 'legacy' CHECK(creation_mode IN ('legacy','logical_v1'))")
    db.execute('ALTER TABLE db_creations ADD COLUMN logical_state TEXT')
    db.execute('DROP INDEX db_creation_reservation')
    db.execute("CREATE UNIQUE INDEX db_creation_reservation ON db_creations(environment_id,warehouse_id) WHERE reservation_active=1 AND creation_mode='legacy'")
    for statement in _LOGICAL_SCHEMA.split(';'):
        if statement.strip():
            db.execute(statement)
    for table, other in [('logical_requests', 'db_creation_requests'),
                         ('db_creation_requests', 'logical_requests')]:
        db.execute(f'''CREATE TRIGGER {table}_scope BEFORE INSERT ON {table}
            WHEN EXISTS (SELECT 1 FROM {other} WHERE environment_id=NEW.environment_id
                AND actor=NEW.actor AND route=NEW.route AND idempotency_key=NEW.idempotency_key)
            BEGIN SELECT RAISE(ABORT,'creation request scope conflict'); END''')
    for table, columns in [
        ('logical_requests', 'id,creation_id,environment_id,actor,route,idempotency_key,payload,ack,action,requester_gate_revision,executor_gate_revision,pins_json,created_at'),
        ('logical_effects', 'creation_id,effect_key,intent_json,created_at'),
        ('logical_backend_identities', 'id,principal_id,database_id,selected_role,grant_epoch,policy_revision,backend_username,credential_ref,authority_source,account_gate_revision')]:
        db.execute(f'''CREATE TRIGGER {table}_immutable BEFORE UPDATE OF {columns} ON {table}
            BEGIN SELECT RAISE(ABORT,'immutable logical identity or intent'); END''')
    db.execute('''CREATE TRIGGER logical_identity_transition BEFORE UPDATE ON logical_backend_identities
        WHEN (OLD.state='REVOKED' AND NEW.state!='REVOKED') OR
             (OLD.state='ACTIVE' AND NEW.state='CANDIDATE')
        BEGIN SELECT RAISE(ABORT,'terminal identity revocation'); END''')
    for table in ('logical_requests', 'logical_effects', 'logical_backend_identities'):
        db.execute(f'''CREATE TRIGGER {table}_retained BEFORE DELETE ON {table}
            BEGIN SELECT RAISE(ABORT,'retained logical authority'); END''')
    db.execute('''CREATE TRIGGER db_creation_mode_immutable BEFORE UPDATE OF creation_mode ON db_creations
        BEGIN SELECT RAISE(ABORT,'immutable creation mode'); END''')
    for event in ('INSERT', 'UPDATE'):
        db.execute(f'''CREATE TRIGGER logical_foundation_{event.lower()} BEFORE {event} ON logical_foundations
            WHEN NEW.status='READY' AND (length(trim(NEW.config_ref))=0 OR
                length(trim(NEW.verification_ref))=0 OR NOT EXISTS (
                SELECT 1 FROM warehouse_config_generations WHERE id=NEW.generation_id
                    AND warehouse_id=NEW.warehouse_id AND state='APPLIED'))
            BEGIN SELECT RAISE(ABORT,'verified applied foundation required'); END''')
        db.execute(f'''CREATE TRIGGER logical_identity_{event.lower()} BEFORE {event} ON logical_backend_identities
            WHEN (NEW.state='ACTIVE' AND (NEW.verification_ref IS NULL OR length(trim(NEW.verification_ref))=0))
                OR (NEW.authority_source='platform_admin' AND NEW.state!='REVOKED' AND NOT EXISTS (
                    SELECT 1 FROM db_creation_principal_gates g JOIN logical_bindings b
                        ON b.environment_id=g.environment_id
                    WHERE b.database_id=NEW.database_id AND g.principal_id=NEW.principal_id
                        AND g.state='ACTIVE' AND g.revision=NEW.account_gate_revision))
            BEGIN SELECT RAISE(ABORT,'verified identity authority required'); END''')


_LOGICAL_SCHEMA = '''
CREATE TABLE logical_requests (
    id TEXT PRIMARY KEY, creation_id TEXT NOT NULL REFERENCES db_creations(id),
    environment_id TEXT NOT NULL, actor TEXT NOT NULL, route TEXT NOT NULL,
    idempotency_key TEXT NOT NULL, payload TEXT NOT NULL, ack TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('CREATE','RESUME','RECONCILE','WITHDRAW','AUTH_REFRESH')),
    dispatch_state TEXT NOT NULL CHECK(dispatch_state IN ('ACCEPTED','CLAIMED','COMPLETE','BLOCKED','UNKNOWN')),
    executor_id TEXT, owner_id TEXT, claim_id TEXT, requester_gate_revision INTEGER,
    executor_gate_revision INTEGER, pins_json TEXT NOT NULL, result_json TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(environment_id,actor,route,idempotency_key));
CREATE TABLE logical_effects (
    creation_id TEXT NOT NULL REFERENCES db_creations(id), effect_key TEXT NOT NULL,
    intent_json TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('INTENT','ACKNOWLEDGED','UNKNOWN','REJECTED')),
    private_ref TEXT, outcome_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY(creation_id,effect_key));
CREATE TABLE logical_foundations (
    environment_id TEXT NOT NULL, warehouse_id TEXT NOT NULL,
    generation_id TEXT NOT NULL REFERENCES warehouse_config_generations(id),
    revision INTEGER NOT NULL CHECK(revision>0),
    status TEXT NOT NULL CHECK(status IN ('READY','MAINTENANCE','BLOCKED')),
    config_ref TEXT NOT NULL, verification_ref TEXT NOT NULL,
    PRIMARY KEY(environment_id,warehouse_id));
CREATE TABLE logical_bindings (
    database_id TEXT PRIMARY KEY REFERENCES catalog_bindings(database_id),
    environment_id TEXT NOT NULL, warehouse_id TEXT NOT NULL,
    foundation_revision INTEGER NOT NULL CHECK(foundation_revision>0),
    desired_policy_revision INTEGER NOT NULL CHECK(desired_policy_revision>0),
    applied_policy_revision INTEGER NOT NULL CHECK(applied_policy_revision>=0 AND applied_policy_revision<=desired_policy_revision),
    catalog_credential_ref TEXT NOT NULL, verification_ref TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision>0),
    FOREIGN KEY(environment_id,warehouse_id) REFERENCES logical_foundations(environment_id,warehouse_id));
CREATE TABLE logical_backend_identities (
    id TEXT PRIMARY KEY, principal_id TEXT NOT NULL, database_id TEXT NOT NULL REFERENCES databases(id),
    selected_role TEXT NOT NULL CHECK(selected_role IN ('bi_viewer','data_engineer','admin')),
    grant_epoch INTEGER, policy_revision INTEGER NOT NULL CHECK(policy_revision>0),
    backend_username TEXT NOT NULL UNIQUE, credential_ref TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK(state IN ('CANDIDATE','ACTIVE','REVOKED')), verification_ref TEXT,
    authority_source TEXT NOT NULL DEFAULT 'grant' CHECK(authority_source IN ('grant','platform_admin')),
    account_gate_revision INTEGER, revision INTEGER NOT NULL CHECK(revision>0),
    CHECK((authority_source='grant' AND grant_epoch>0 AND grant_epoch IS NOT NULL) OR
          (authority_source='platform_admin' AND grant_epoch IS NULL AND selected_role='admin'
           AND account_gate_revision IS NOT NULL AND account_gate_revision>0)));
CREATE UNIQUE INDEX logical_active_identity ON logical_backend_identities(principal_id,database_id,selected_role)
    WHERE state='ACTIVE';
'''


def _v14_repairs(connection, *, details=False):
    roles = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='effective_grants'").fetchone()
    binding = connection.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='db_stage_immutable'").fetchone()
    values = (roles is not None and "'admin'" not in roles[0],
              binding is not None and 'json_extract' not in binding[0])
    return values if details else any(values)


@contextmanager
def connect():
    path = root() / 'jobs.sqlite3'
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(descriptor)
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA foreign_keys=ON')
        _bootstrap(connection)
        path.chmod(0o600)
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
