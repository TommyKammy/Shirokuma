"""Create only verified, historical Data Plan catalog entities and lineage.

The caller supplies an authenticated loopback Client and the strict evidence
projection. Existing anchors and previously curated entities are never updated.
Partial creates remain owned historical evidence and may be completed on retry.
"""
from __future__ import annotations

import hashlib
import json
import math
from urllib.parse import quote
import uuid

from metadata.client import APIError
from metadata.data_plan_evidence import validate_projection

TEAM = "ShirokumaLocalExperiment"
SERVICE = "shirokuma_local"
DATABASE = SERVICE + ".iceberg"
SCHEMA = DATABASE + ".local_experiment"
SOURCE_SCHEMA = DATABASE + ".smoke"
SOURCE = SOURCE_SCHEMA + ".fixture_v1"
TARGETS = ("stg", "mart")


class CurateError(ValueError):
    """A catalog precondition, conflict or saved readback failed."""

    def __init__(self, message, *, partial_receipt=None):
        super().__init__(message)
        self.partial_receipt = partial_receipt


def _uuid(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise CurateError("catalog entity has invalid identity") from None
    return value


def _identity(value, name):
    if (not isinstance(value, dict) or value.get("fullyQualifiedName") != name
            or value.get("name") != name.rsplit(".", 1)[-1]
            or value.get("deleted", False) is not False):
        raise CurateError("catalog entity identity or deletion state differs")
    return _uuid(value.get("id"))


def _ref(value, expected_id, expected_type, expected_name):
    if (not isinstance(value, dict) or value.get("id") != expected_id
            or value.get("type") != expected_type
            or value.get("fullyQualifiedName") != expected_name
            or value.get("deleted", False) is not False):
        raise CurateError("catalog parent reference differs")


def _owners(value, owner_id):
    owners = value.get("owners")
    if (not isinstance(owners, list) or len(owners) != 1 or not isinstance(owners[0], dict)
            or owners[0].get("id") != owner_id or owners[0].get("type") != "team"
            or owners[0].get("deleted", False) is not False):
        raise CurateError("catalog owner differs")


def _revision(value):
    version, updated = value.get("version"), value.get("updatedAt")
    if (type(version) not in (int, float) or not math.isfinite(version) or version <= 0
            or type(updated) is not int or updated <= 0):
        raise CurateError("catalog entity revision is missing or invalid")
    return {"version": version, "updated_at": updated}


def _columns(value, expected, *, generated=False):
    columns = value.get("columns")
    if not isinstance(columns, list) or len(columns) != len(expected):
        raise CurateError("catalog column schema differs")
    for saved, wanted in zip(columns, expected):
        if (not isinstance(saved, dict) or saved.get("name") != wanted["name"]
                or saved.get("dataType") != wanted["type"] or saved.get("children")):
            raise CurateError("catalog column schema differs")
        if generated and (saved.get("description") not in (None, "")
                          or saved.get("dataTypeDisplay") != wanted["type"].lower()
                          or (wanted["type"] == "VARCHAR" and saved.get("dataLength") != 2147483647)):
            raise CurateError("generated catalog column was edited")


def _get(client, collection, name, *, optional=False, owners=False):
    path = f"/{collection}/name/{quote(name, safe='')}?include=all"
    if owners:
        path += "&fields=owners"
    try:
        return client.request("GET", path)
    except APIError as error:
        if optional and error.status == 404:
            return None
        raise


def _digest(projection):
    return hashlib.sha256(json.dumps(projection, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


def _description(projection, target):
    return (f"Shirokuma local catalog v1; run {projection['run_token']}; target {target}; "
            f"operation {projection['operation']}; evidence sha256 {_digest(projection)}. "
            "Historical successful Data Plan evidence. Generated SQL relation was removed "
            "by verified cleanup; this is not a live table or production acceptance.")


def _edge_description(projection, target):
    return (f"Shirokuma local catalog lineage v1; run {projection['run_token']}; target {target}; "
            f"evidence sha256 {_digest(projection)}. Historical dependency from verified dbt "
            "and Dagster artifacts; generated SQL relations have been removed.")


def _table_payload(projection, entity, owner_id):
    columns = []
    for column in entity["columns"]:
        result = {"name": column["name"], "dataType": column["type"],
                  "dataTypeDisplay": column["type"].lower()}
        if column["type"] == "VARCHAR":
            result["dataLength"] = 2147483647
        columns.append(result)
    return {"name": entity["name"].rsplit(".", 1)[-1], "databaseSchema": SCHEMA,
            "description": _description(projection, entity["id"]), "columns": columns,
            "owners": [{"id": owner_id, "type": "team"}]}


def _table(value, projection, entity, owner_id, schema_id):
    identity = _identity(value, entity["name"])
    _ref(value.get("databaseSchema"), schema_id, "databaseSchema", SCHEMA)
    _owners(value, owner_id)
    _columns(value, entity["columns"], generated=True)
    if value.get("description") != _description(projection, entity["id"]):
        raise CurateError("generated catalog description or evidence marker differs")
    return identity


def _expected_edges(source_id, table_ids):
    return {target: pair for target, pair in {
        "stg": (source_id, table_ids["stg"]),
        "mart": (table_ids["stg"], table_ids["mart"]),
    }.items() if all(pair)}


def _lineage(client, projection, source_id, table_ids):
    expected = _expected_edges(source_id, table_ids)
    found = {}
    for target, identity in table_ids.items():
        if identity is None:
            continue
        graph = client.request("GET", f"/lineage/table/{identity}?upstreamDepth=1&downstreamDepth=1")
        if not isinstance(graph, dict):
            raise CurateError("catalog lineage response is malformed")
        entity = next(item for item in projection["entities"] if item["id"] == target)
        _ref(graph.get("entity"), identity, "table", entity["name"])
        for direction in ("upstreamEdges", "downstreamEdges"):
            edges = graph.get(direction) or []
            if not isinstance(edges, list) or len(edges) > 2:
                raise CurateError("catalog lineage exceeds the generated scope")
            for edge in edges:
                if not isinstance(edge, dict):
                    raise CurateError("catalog lineage edge is malformed")
                pair = (edge.get("fromEntity"), edge.get("toEntity"))
                if identity not in pair or pair not in expected.values():
                    raise CurateError("generated catalog has conflicting lineage")
                edge_target = next(key for key, wanted in expected.items() if pair == wanted)
                details = edge.get("lineageDetails")
                if (not isinstance(details, dict) or details.get("source") != "Manual"
                        or details.get("description") != _edge_description(projection, edge_target)
                        or any(details.get(key) for key in ("sqlQuery", "pipeline", "columnsLineage", "assetEdges"))
                        or edge.get("description") not in (None, "")):
                    raise CurateError("generated catalog lineage was edited or has another source")
                found.setdefault(edge_target, set()).add(identity)
    for target, seen in found.items():
        required = set(expected[target]) & set(table_ids.values())
        if seen != required:
            raise CurateError("catalog lineage observations are inconsistent")
    return {target: target in found for target in TARGETS}


def preflight(client, projection):
    """Read and validate every existing anchor/target before any mutation.

    targets booleans mean the exact host-generated field already exists. This
    safe snapshot may be used for a model's bounded missing-field selection.
    """
    projection = validate_projection(projection)
    team = _get(client, "teams", TEAM)
    owner_id = _identity(team, TEAM)
    revisions = {"team": _revision(team)}
    if team.get("teamType") != "Group":
        raise CurateError("catalog owner is not the existing experiment group")
    service = _get(client, "services/databaseServices", SERVICE)
    service_id = _identity(service, SERVICE)
    revisions["service"] = _revision(service)
    if service.get("serviceType") != "Trino":
        raise CurateError("catalog service is not the existing Trino service")
    database = _get(client, "databases", DATABASE)
    database_id = _identity(database, DATABASE)
    revisions["database"] = _revision(database)
    _ref(database.get("service"), service_id, "databaseService", SERVICE)
    schema_ids = {}
    for name in (SOURCE_SCHEMA, SCHEMA):
        schema = _get(client, "databaseSchemas", name)
        schema_ids[name] = _identity(schema, name)
        revisions[name] = _revision(schema)
        _ref(schema.get("database"), database_id, "database", DATABASE)
    fixture = _get(client, "tables", SOURCE, owners=True)
    source_id = _identity(fixture, SOURCE)
    revisions["source"] = _revision(fixture)
    _ref(fixture.get("databaseSchema"), schema_ids[SOURCE_SCHEMA], "databaseSchema", SOURCE_SCHEMA)
    _owners(fixture, owner_id)
    _columns(fixture, [{"name": "id", "type": "BIGINT"}, {"name": "name", "type": "VARCHAR"}])
    table_ids = {}
    for entity in projection["entities"]:
        value = _get(client, "tables", entity["name"], optional=True, owners=True)
        table_ids[entity["id"]] = (None if value is None else
                                  _table(value, projection, entity, owner_id, schema_ids[SCHEMA]))
        revisions[entity["id"]] = None if value is None else _revision(value)
    identities = [owner_id, service_id, database_id, *schema_ids.values(), source_id,
                  *(value for value in table_ids.values() if value is not None)]
    if len(identities) != len(set(identities)):
        raise CurateError("catalog identities are not distinct")
    lineage = _lineage(client, projection, source_id, table_ids)
    return {"schema_version": 1, "run_token": projection["run_token"],
            "evidence_sha256": _digest(projection), "owner_id": owner_id,
            "service_id": service_id, "database_id": database_id, "schema_ids": schema_ids,
            "source_id": source_id, "table_ids": table_ids,
            "revisions": revisions,
            "targets": {target: {"description": table_ids[target] is not None,
                                 "owner": table_ids[target] is not None,
                                 "lineage": lineage[target]} for target in TARGETS}}


def apply(client, projection, state=None, *, progress=None):
    """Create missing generated entities/edges; reject drift and verify readback.

    No replacement, deletion, anchor updates, raw SQL, events, or global settings.
    This is not a multi-request transaction: any partial owned creation is kept
    for a verified retry, and API failures never imply rollback or completion.
    """
    projection = validate_projection(projection)
    journal = {"schema_version": 1, "run_token": projection["run_token"],
               "evidence_sha256": _digest(projection), "result": "in-progress",
               "confirmed_tables": {}, "confirmed_edges": [], "pending": None,
               "verified_complete": False}

    def emit():
        if progress is not None:
            progress(json.loads(json.dumps(journal)))

    try:
        result = _apply(client, projection, state, journal, emit)
        journal.update(result="passed", verified_complete=True)
        emit()
        return result
    except Exception:
        if journal["pending"] is None and not journal["confirmed_tables"] and not journal["confirmed_edges"]:
            raise
        journal["result"] = "failed"
        if journal["pending"] is not None:
            # A timeout/transport failure does not prove that the server did
            # not save the request. Keep that uncertainty for the next GET.
            journal["pending"]["outcome"] = "unknown"
        try:
            emit()
        except Exception:
            pass  # Preserve the safe in-memory receipt if storage also fails.
        raise CurateError("catalog mutation or readback failed; inspect the partial receipt",
                          partial_receipt=json.loads(json.dumps(journal))) from None


def _apply(client, projection, state, journal, emit):
    current = preflight(client, projection)
    if state is not None and current != state:
        raise CurateError("catalog changed since plan observation")
    created_tables = 0
    table_ids = dict(current["table_ids"])
    for entity in projection["entities"]:
        target = entity["id"]
        if table_ids[target] is None:
            # POST is create-only: a concurrent same-name entity must conflict,
            # never be overwritten through the upsert PUT /tables endpoint.
            journal["pending"] = {"operation": "create_table", "target": target, "outcome": "pending"}
            emit()  # Durable host receipt before any request may be accepted.
            value = client.request("POST", "/tables", _table_payload(projection, entity, current["owner_id"]))
            table_ids[target] = _table(value, projection, entity, current["owner_id"], current["schema_ids"][SCHEMA])
            created_tables += 1
            journal["confirmed_tables"][target] = table_ids[target]
            journal["pending"] = None
            emit()
    # Recheck complete target ownership and all incident edges before PUT.
    before_edges = preflight(client, projection)
    if (before_edges["table_ids"] != table_ids
            or any(before_edges[key] != current[key] for key in
                   ("owner_id", "service_id", "database_id", "schema_ids", "source_id"))):
        raise CurateError("catalog changed while creating generated entities")
    created_edges = 0
    for target, (upstream, downstream) in _expected_edges(current["source_id"], table_ids).items():
        # The pinned 1.12.6 LineageResource accepts PUT /v1/lineage and returns
        # an empty 200 response; use GET for authoritative saved verification.
        # https://github.com/open-metadata/OpenMetadata/blob/1.12.6-release/openmetadata-service/src/main/java/org/openmetadata/service/resources/lineage/LineageResource.java
        if not before_edges["targets"][target]["lineage"]:
            journal["pending"] = {"operation": "create_edge", "target": target, "outcome": "pending"}
            emit()
            client.request("PUT", "/lineage", {"edge": {
                "fromEntity": {"id": upstream, "type": "table"},
                "toEntity": {"id": downstream, "type": "table"},
                "lineageDetails": {"source": "Manual", "description": _edge_description(projection, target)}}})
            created_edges += 1
            # A 200 alone is insufficient to call a saved edge confirmed.
            if not _lineage(client, projection, current["source_id"], table_ids)[target]:
                raise CurateError("catalog lineage write is not present in readback")
            journal["confirmed_edges"].append(target)
            journal["pending"] = None
            emit()
    saved = preflight(client, projection)
    if saved["table_ids"] != table_ids or not all(all(fields.values()) for fields in saved["targets"].values()):
        raise CurateError("catalog saved readback is incomplete")
    if any(saved[key] != current[key] for key in
           ("owner_id", "service_id", "database_id", "schema_ids", "source_id")):
        raise CurateError("catalog anchors changed during readback")
    return {"result": "passed", "run_token": projection["run_token"],
            "evidence_sha256": saved["evidence_sha256"], "created_tables": created_tables,
            "reused_tables": 2 - created_tables, "created_edges": created_edges,
            "verified_tables": 2, "verified_edges": 2,
            "writes": created_tables + created_edges, "noop": created_tables + created_edges == 0,
            "historical_models_removed_from_trino": True,
            "table_ids": table_ids, "targets": saved["targets"]}
