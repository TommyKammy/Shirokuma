"""Private execution abandonment for a failed atomic ingest cleanup.

A fresh current compute cutset closes execution, never the historical UNKNOWN
DROP outcome. The registered stage and target reservation remain untouched.
"""
import copy
import hashlib
import json
import re
import uuid

import control_store
import data_management as registry
import dataops_service as ds
import query_service as queries
from query_context import ResolvedQueryContext
import warehouse_admission as admission
from execution_recovery import Recovery, _require, _one, _json, _time
from logical_execution_recovery import LogicalQueryRecovery

ROUTE = "recover-ingest"
CLOSURE = "FENCED_STAGE_CLEANUP"


def _rows(db, sql, args=()):
    return [dict(r) for r in db.execute(sql, args)]


def _snapshot(db, ident):
    op = _one(db, "SELECT * FROM dataops_operations WHERE id=?", (ident,))
    context = ResolvedQueryContext(**json.loads(op["context"]))
    links = _rows(db, "SELECT * FROM dataops_queries WHERE operation_id=? ORDER BY step", (ident,))
    _require({r[0] for r in db.execute(
        "SELECT query_id FROM query_execution_contexts WHERE request_id=?", (ident,))} ==
        {c["query_id"] for c in links})
    owned = _rows(db, "SELECT * FROM dataops_owned_objects WHERE operation_id=? ORDER BY object_id", (ident,))
    parent = _one(db, "SELECT * FROM warehouse_admissions WHERE kind='dataops' AND subject_id=?", (ident,))
    tree = _rows(db, """WITH RECURSIVE tree AS (
        SELECT * FROM warehouse_admissions WHERE id=? UNION ALL
        SELECT a.* FROM warehouse_admissions a JOIN tree t ON a.parent_id=t.id)
        SELECT * FROM tree ORDER BY id""", (parent["id"],))
    target = json.loads(op["payload"])["payload"]["target"]
    target_schema = _one(db, "SELECT * FROM schemas WHERE id=?", (target["schema_id"],))
    target_object = (_one(db, "SELECT * FROM object_bindings WHERE id=?", (target["object_id"],))
                     if "object_id" in target else None)
    return dict(subject="ingest", operation=op, query_links=links, owned_objects=owned,
        admissions=tree, parent_id=parent["id"], target_schema=target_schema, target_object=target_object,
        claims=_rows(db, "SELECT * FROM dataops_target_claims WHERE operation_id=? ORDER BY catalog,schema_name,table_name", (ident,)),
        stage_objects=[_one(db, "SELECT * FROM object_bindings WHERE id=?", (o["object_id"],)) for o in owned],
        stage_schemas=[_one(db, "SELECT * FROM schemas WHERE id=(SELECT schema_id FROM object_bindings WHERE id=?)", (o["object_id"],)) for o in owned],
        queries=[_one(db, "SELECT * FROM query_records WHERE id=?", (c["query_id"],)) for c in links],
        executions=[_one(db, "SELECT * FROM query_execution_contexts WHERE query_id=?", (c["query_id"],)) for c in links],
        binding=_one(db, "SELECT * FROM catalog_bindings WHERE database_id=?", (context.database_id,)),
        database=_one(db, "SELECT * FROM databases WHERE id=?", (context.database_id,)),
        schema=_one(db, "SELECT * FROM schemas WHERE id=?", (context.schema_id,)),
        generation=_one(db, "SELECT * FROM warehouse_config_generations WHERE id=?", (context.generation_id,)),
        query_ids=sorted(q["id"] for q in _rows(db, "SELECT q.id FROM query_records q JOIN dataops_queries c ON c.query_id=q.id WHERE c.operation_id=? AND q.state='UNKNOWN'", (ident,))),
        operation_ids=[ident])


def _summary(ident):
    return _json(dict(code="INGEST_EXECUTION_ABANDONED", recovery_operation_id=ident,
                      query_outcome="UNKNOWN", execution_closure=CLOSURE))


class IngestRecovery(LogicalQueryRecovery):
    route = ROUTE
    action = "INGEST_EXECUTION_RECOVERY"
    locks = Recovery.locks
    plan = Recovery.plan

    @staticmethod
    def subject(frozen):
        return frozen["operation"]["id"]

    def _request(self, ident):
        return _json(dict(subject="ingest", ingest_operation_id=ident,
                          config=self.config, runtime_binding=self.seal))

    def _binding(self, db, frozen):
        Recovery._binding(self, db, frozen)
        self._logical_blockers(db)
        principal, _ = self.authenticate()
        op = frozen["operation"]
        _require(op["actor"] == self.actor, "RECOVERY_ACTOR_CHANGED")
        context = ResolvedQueryContext(**json.loads(op["context"]))
        queries.revalidate_admission(db, principal, context)
        decision, headers = queries.trino_auth.prepare_backend_v2(principal=principal,
            database_id=context.database_id, selected_role=context.selected_role)
        _require(headers["X-Trino-User"] == op["backend_username"] and
                 decision.generation_id == context.generation_id and
                 decision.grant_revision == context.grant_revision, "RECOVERY_BINDING_CHANGED")

    def graph(self, db, ident):
        try:
            return self._graph(db, ident)
        except (KeyError, TypeError, ValueError, AttributeError, StopIteration, IndexError):
            _require(False)

    def _graph(self, db, ident):
        g = _snapshot(db, ident)
        op = g["operation"]
        context_data = json.loads(op["context"])
        context = ResolvedQueryContext(**context_data)
        payload = json.loads(op["payload"])
        p = payload["payload"]
        receipt = json.loads(op["result"])
        _require(set(p) == {"input_sha256", "input_length", "format", "encoding", "delimiter",
                 "schema_hints", "mode", "key_columns", "batch_size", "atomic_append", "target"} and
                 isinstance(p["input_sha256"], str) and re.fullmatch("[0-9a-f]{64}", p["input_sha256"]) and
                 type(p["input_length"]) is int and p["input_length"] >= 0 and
                 p["format"] in ("csv", "json", "jsonl") and isinstance(p["encoding"], str) and
                 isinstance(p["schema_hints"], dict) and isinstance(p["key_columns"], list) and
                 all(isinstance(k, str) for k in p["key_columns"]) and
                 len(set(p["key_columns"])) == len(p["key_columns"]) and
                 type(p["batch_size"]) is int and p["batch_size"] > 0 and type(p["atomic_append"]) is bool and
                 (isinstance(p["delimiter"], str) and len(p["delimiter"]) == 1 if p["format"] == "csv"
                  else p["delimiter"] is None))
        _require(op["route"] == "manual-ingest" and op["state"] == "UNKNOWN" and op["actor"] == self.actor and
                 not op["committed"] and op["context"] == ds._json(context_data) and
                 op["payload"] == ds._json(dict(context=context_data, payload=p)) and payload["context"] == context_data and
                 op["fingerprint"] == hashlib.sha256(op["payload"].encode()).hexdigest() and
                 isinstance(op["credential_snapshot_ref"], str) and bool(op["credential_snapshot_ref"]))
        _require(p["mode"] in ("create", "replace", "append", "upsert") and
                 (p["mode"] != "append" or p["atomic_append"] is True) and
                 receipt["atomic"] is True and receipt["operation_id"] == ident and
                 receipt["request_fingerprint"] == op["fingerprint"] and
                 receipt["input_fingerprint"] == p["input_sha256"] and receipt["mode"] == p["mode"] and
                 type(receipt["committed_rows"]) is int and receipt["committed_rows"] == 0 and
                 type(receipt["committed_batches"]) is int and receipt["committed_batches"] == 0 and
                 receipt["cleanup"] == "unknown" and bool(receipt["error"]))
        _require(context.warehouse_id == "LOCAL" and context.selected_role == "admin" and
                 g["binding"]["desired_revision"] == g["binding"]["applied_revision"] == context.binding_revision and
                 g["binding"]["applied_generation_id"] == context.generation_id == admission.control(db)["applied_generation_id"] and
                 g["generation"]["state"] == "APPLIED" and g["generation"]["warehouse_id"] == "LOCAL" and
                 g["database"]["state"] == "READY" and g["database"]["sql_catalog"] == context.sql_catalog and
                 g["schema"]["database_id"] == context.database_id and g["schema"]["sql_name"] == context.sql_schema)
        _require(len(g["owned_objects"]) == 1)
        owned, stage, schema = (g[k][0] for k in ("owned_objects", "stage_objects", "stage_schemas"))
        _require(owned["purpose"] == "stage" and owned["incarnation"] == stage["incarnation"] and
                 stage["kind"] == "TABLE" and stage["current"] == 1 and
                 str(uuid.UUID(stage["backend_identity"])) == stage["backend_identity"] and
                 schema["database_id"] == context.database_id and schema["purpose"] == "sandbox" and
                 schema["sql_name"].upper() == "SANDBOX")
        _require(not db.execute("SELECT 1 FROM dataops_owned_objects WHERE object_id=? AND operation_id!=?",
                               (stage["id"], ident)).fetchone())
        target, ts, obj = p["target"], g["target_schema"], g["target_object"]
        _require(target["database_id"] == ts["database_id"] == context.database_id)
        if p["mode"] == "create":
            registry.DestinationLocator(**target)
            _require(obj is None)
            name = target["physical_name"]
        else:
            registry.ResourceRef(**target)
            _require(obj["schema_id"] == ts["id"] and obj["incarnation"] == target["incarnation"] and
                     obj["current"] == 1 and obj["kind"] == "TABLE")
            name = obj["physical_name"]
        _require((ts["id"], name) != (schema["id"], stage["physical_name"]) and
                 g["claims"] == [dict(catalog=context.sql_catalog, schema_name=ts["sql_name"], table_name=name, operation_id=ident)])
        for table in ("db_job_operations", "dbt_run_operations", "db_mart_operations", "db_stage_scan_operations", "db_stage_runs", "wap_models", "wap_builds", "wap_audits", "wap_publications"):
            _require(not db.execute("SELECT 1 FROM " + table + " WHERE operation_id=?", (ident,)).fetchone())
        _require(len(g["query_ids"]) == 1 and len(g["admissions"]) == len(g["queries"]) + 1)
        expected = {g["parent_id"]}
        parent = next(a for a in g["admissions"] if a["id"] == g["parent_id"])
        _require(parent["parent_id"] is None and parent["state"] == "UNKNOWN" and not parent["execution_closure"])
        failed_load = False
        _require(sum(c["step"] == "stage-create" and c["action"] == "create" and q["state"] == "FINISHED"
                     for c, q in zip(g["query_links"], g["queries"])) == 1)
        for child, q, e in zip(g["query_links"], g["queries"], g["executions"]):
            qa = _one(db, "SELECT * FROM warehouse_admissions WHERE kind='query' AND subject_id=?", (q["id"],))
            expected.add(qa["id"])
            _require(qa["parent_id"] == parent["id"] and q["actor"] == self.actor and
                     all({**q, **e}[k] == v for k, v in context_data.items()) and
                     e["backend_username"] == op["backend_username"] and e["credential_snapshot_ref"] == op["credential_snapshot_ref"] and
                     e["request_id"] == ident and e["worksheet_id"] == child["step"] and e["mode"] == "dataops" and
                     child["commit_result"] is None and child["step"] != "commit" and
                     child["action"] in ("create", "describe", "validate_keys", "ingest_rows", "cleanup") and
                     qa["state"] == q["state"] and not qa["execution_closure"])
            if q["state"] == "UNKNOWN":
                sql = "DROP TABLE " + ".".join(registry.quote_identifier(n) for n in
                    (context.sql_catalog, schema["sql_name"], stage["physical_name"]))
                _require(child["step"] == "failure-cleanup" and child["action"] == "cleanup" and e["sql"] == sql and
                         (e["backend_query_id"] is None or queries._backend_id(e["backend_query_id"])))
            else:
                _require(q["state"] in admission.TERMINAL and queries._backend_id(e["backend_query_id"]) and qa["finished_at"])
                _require((child["action"] == "create" and child["step"] == "stage-create") or
                         (child["action"] == "describe" and child["step"] == "target-schema") or
                         (child["action"] == "validate_keys" and child["step"] == "target-keys") or
                         (child["action"] == "ingest_rows" and re.fullmatch("load-[0-9]+", child["step"])))
                failed_load |= q["state"] == "FAILED" and child["step"].startswith("load-") and child["action"] == "ingest_rows"
        _require(failed_load and expected == {a["id"] for a in g["admissions"]})
        _require(all(a["actor"] == self.actor and a["database_id"] == context.database_id and
                     a["warehouse_id"] == "LOCAL" and a["generation_id"] == context.generation_id for a in g["admissions"]))
        self._binding(db, g)
        _require({a["id"] for a in admission.blockers(db)} ==
                 {a["id"] for a in g["admissions"] if admission.execution_unresolved(a)}, "UNRELATED_UNRESOLVED_WORK")
        _require(not any(admission.execution_unresolved(a) for a in _rows(db,
            "SELECT * FROM warehouse_admissions WHERE warehouse_id!='LOCAL' AND state IN ('ACTIVE','UNKNOWN')")),
            "UNRELATED_UNRESOLVED_WORK")
        for table, field, terminal, excluded in (("runs", "status", ("succeeded", "failed", "canceled"), ""),
                ("db_job_runs", "status", ("succeeded", "failed", "canceled"), ""),
                ("dataops_operations", "state", tuple(admission.TERMINAL), ident)):
            _require(not db.execute("SELECT 1 FROM " + table + " WHERE " + field + " NOT IN (" +
                ",".join("?" for _ in terminal) + ") AND id!=?", (*terminal, excluded)).fetchone(), "UNRELATED_UNRESOLVED_WORK")
        _require(not db.execute("""SELECT 1 FROM query_records q WHERE q.id!=? AND
            q.state NOT IN ('FINISHED','FAILED','CANCELED') AND NOT EXISTS (SELECT 1 FROM
            warehouse_admissions a WHERE a.kind='query' AND a.subject_id=q.id AND
            a.execution_closure IN ('FENCED_READ_ONLY','FENCED_STAGE_CLEANUP'))""", (g["query_ids"][0],)).fetchone(), "UNRELATED_UNRESOLVED_WORK")
        _require(not db.execute("""SELECT 1 FROM dbt_runs WHERE
            json_extract(record,'$.state') IN ('QUEUED','BUILDING','TESTING','AUDITING','PUBLISHING',
                'CANCELING','COMPILING','UNKNOWN','COMMITTED_RECORDING_FAILED') AND NOT EXISTS (
                SELECT 1 FROM warehouse_admissions a WHERE a.id=dbt_runs.admission_id
                AND a.state IN ('FINISHED','FAILED','CANCELED'))""").fetchone(), "UNRELATED_UNRESOLVED_WORK")
        return g

    @staticmethod
    def digest(row):
        return hashlib.sha256(_json(dict(contract="recover-ingest-intent-v1",
            **{k: row[k] for k in ("operation_id", "actor", "route", "payload", "intent")})).encode()).hexdigest()

    def inspect(self, operation_id):
        self.authenticate()
        with control_store.connect() as db:
            row = _one(db, "SELECT * FROM execution_recovery_requests WHERE operation_id=?", (operation_id,))
        _require(row["route"] == self.route and row["actor"] == self.actor, "RECOVERY_SUBJECT_MISMATCH")
        intent = json.loads(row["intent"])
        return dict(operation_id=operation_id, ingest_operation_id=self.subject(intent["graph"]), actor=row["actor"],
            revision=row["revision"], phase=row["phase"], mode="FENCE", intent_digest=self.digest(row),
            scope=intent["scope"], proof=json.loads(row["proof"]) if row["proof"] is not None else None,
            query_ids=intent["graph"]["query_ids"], outcome="UNKNOWN",
            execution_closure=CLOSURE if row["phase"] in ("CLOSED", "OPEN") else "")

    def apply(self, operation_id, expected_revision, *, expected_intent_digest=None, reconcile=False):
        self.authenticate()
        with control_store.connect() as db:
            row = _one(db, "SELECT * FROM execution_recovery_requests WHERE operation_id=?", (operation_id,))
            _require(row["actor"] == self.actor and row["route"] == self.route, "RECOVERY_SUBJECT_MISMATCH")
            _require(expected_intent_digest == self.digest(row), "RECOVERY_INTENT_MISMATCH")
            if row["phase"] in ("CLOSED", "OPEN"):
                self._closed_graph(db, operation_id, json.loads(row["intent"])["graph"])
        return Recovery.apply(self, operation_id, expected_revision, reconcile=reconcile)

    def _transition(self, db, row, phase, *, proof=None, control_revision=None):
        Recovery._transition(self, db, row, phase, proof=proof, control_revision=control_revision)
        db.execute("UPDATE operations SET before_after=? WHERE id=?",
            (_json([["execution_closure", CLOSURE if phase in ("CLOSED", "OPEN") else ""]]), row["operation_id"]))

    def _close(self, db, operation_id, frozen, mode="FENCE"):
        _require(self.graph(db, self.subject(frozen)) == frozen, "RECOVERY_GRAPH_CHANGED")
        db.execute("UPDATE warehouse_admissions SET execution_closure=?,evidence_ref=? WHERE kind='query' AND subject_id=?",
                   (CLOSURE, operation_id, frozen["query_ids"][0]))
        db.execute("UPDATE dataops_operations SET state='FAILED',cleanup_error=? WHERE id=?",
                   (_summary(operation_id), self.subject(frozen)))
        a = next(a for a in frozen["admissions"] if a["id"] == frozen["parent_id"])
        admission.finish(db, admission.AdmissionHandle(**{k: a[k] for k in
            ("id", "warehouse_id", "actor", "kind", "subject_id", "generation_id", "database_id")}),
            state="FAILED", evidence_ref=operation_id)

    def _closed_graph(self, db, operation_id, frozen, mode="FENCE"):
        try:
            self._check_closed_graph(db, operation_id, frozen)
        except (KeyError, TypeError, ValueError, AttributeError, StopIteration, IndexError):
            _require(False)

    def _check_closed_graph(self, db, operation_id, frozen):
        self._binding(db, frozen)
        actual = _snapshot(db, self.subject(frozen))
        expected = copy.deepcopy(frozen)
        expected["operation"].update(state="FAILED", cleanup_error=_summary(operation_id))
        for a in expected["admissions"]:
            if a["kind"] == "query" and a["subject_id"] in frozen["query_ids"]:
                a.update(execution_closure=CLOSURE, evidence_ref=operation_id)
            elif a["id"] == frozen["parent_id"]:
                finished = next(r["finished_at"] for r in actual["admissions"] if r["id"] == a["id"])
                _require(isinstance(finished, str) and _time(finished) >= _time(a["created_at"]), "RECOVERY_GRAPH_CHANGED")
                a.update(state="FAILED", evidence_ref=operation_id, finished_at=finished)
        _require(actual == expected, "RECOVERY_GRAPH_CHANGED")


def run(args):
    _require(args.config.is_absolute())
    operator = IngestRecovery(args.config)
    if args.command.endswith("-plan"):
        return operator.plan(args.ingest_operation_id, args.idempotency_key)
    if args.command.endswith("-inspect"):
        return operator.inspect(args.operation_id)
    return operator.apply(args.operation_id, args.expected_revision,
        expected_intent_digest=args.expected_intent_digest, reconcile=args.command.endswith("-reconcile"))
