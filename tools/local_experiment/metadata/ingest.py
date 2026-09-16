#!/usr/bin/env python3
"""Ingest live fixture metadata and retained dbt/Dagster evidence into OpenMetadata."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.parse
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_experiment import check_context
from local_session import trino_tunnel, interruption_handlers, SessionInterrupted
from local_trino_smoke import TrinoClient
from metadata.client import Client
from metadata.runtime import ROOT, STATE, check_runtime

SERVICE = "shirokuma_local"
PIPELINES = "shirokuma_dagster"
NAMESPACE = "trino://shirokuma-local/iceberg"
PRODUCER = "https://github.com/TommyKammy/Shirokuma/tree/main/tools/local_experiment/metadata"
MODEL_IDS = {"model.shirokuma_local_dataops.stg_fixture", "model.shirokuma_local_dataops.mart_fixture"}


def read_evidence(directory: Path):
    directory = directory.resolve()
    if not directory.is_relative_to(ROOT / "tmp/local-experiment/dataops"):
        raise ValueError("DataOps evidence must be inside this checkout's generated evidence directory")
    summary = json.loads((directory / "summary.json").read_text())
    token = summary.get("run_token", "")
    if not re.fullmatch("[0-9a-f]{32}", token) or directory.name != token:
        raise ValueError("invalid DataOps run identity")
    if summary.get("result") != "passed" or not summary.get("fixture_unchanged") or not summary.get("query_drain", {}).get("verified"):
        raise ValueError("require successful DataOps evidence with fixture preservation and query drain")
    attempt = next((x for x in summary["attempts"] if x.get("success") and x.get("label") == "first"), None)
    if not attempt:
        raise ValueError("successful first Dagster attempt is missing")
    uuid.UUID(attempt["run_id"])
    manifest = json.loads((directory / "first/build-target/manifest.json").read_text())
    results = json.loads((directory / "first/build-target/run_results.json").read_text())
    models = {k: v for k, v in manifest["nodes"].items() if v["resource_type"] == "model"}
    if set(models) != MODEL_IDS:
        raise ValueError("unexpected model scope in DataOps evidence")
    successful = {x["unique_id"] for x in results["results"] if x["status"] == "success"}
    if not MODEL_IDS <= successful:
        raise ValueError("dbt results do not prove both models succeeded")
    for model in models.values():
        suffix = "stg" if model["name"] == "stg_fixture" else "mart"
        if (model["database"], model["schema"], model["alias"]) != ("iceberg", "local_experiment", f"dataops_{token}_{suffix}"):
            raise ValueError("unexpected generated table identity")
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in
              [directory / "summary.json", directory / "first/build-target/manifest.json", directory / "first/build-target/run_results.json"]}
    return summary, attempt, manifest, results, models, hashes


def table_name(node):
    return ".".join([node["database"], node["schema"], node.get("alias") or node.get("identifier") or node["name"]])


def events_for(summary, attempt, manifest, results, models):
    """Replay actual successful historical executions, without claiming a new job ran."""
    all_nodes = {**manifest["sources"], **models}
    events = []
    executions = {result["unique_id"]: next(t for t in result["timing"] if t["name"] == "execute")
                  for result in results["results"] if result["unique_id"] in models}
    for unique_id, node in sorted(models.items()):
        job_name = f"dataops_{summary['run_token']}_{node['name']}"
        datasets = lambda ids: [{"namespace": NAMESPACE, "name": table_name(all_nodes[key])} for key in ids]
        event = {"producer": PRODUCER, "schemaURL": "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent",
                 "run": {"runId": str(uuid.uuid5(uuid.UUID(attempt["run_id"]), unique_id)),
                         "facets": {"parent": {"_producer": PRODUCER,
                             "_schemaURL": "https://openlineage.io/spec/facets/1-0-0/ParentRunFacet.json#/$defs/ParentRunFacet",
                             "run": {"runId": attempt["run_id"]},
                             "job": {"namespace": PIPELINES, "name": "local_dataops"}}}},
                 "job": {"namespace": PIPELINES, "name": job_name},
                 "inputs": datasets(node["depends_on"]["nodes"]), "outputs": datasets([unique_id])}
        timing = executions[unique_id]
        for event_type, key in [("START", "started_at"), ("COMPLETE", "completed_at")]:
            events.append({**event, "eventType": event_type, "eventTime": timing[key]})
    return events


def ingest(client, directory, trino):
    summary, attempt, manifest, results, models, hashes = read_evidence(directory)
    fixture = trino.query("SELECT id, name FROM iceberg.smoke.fixture_v1 ORDER BY id")
    if fixture["rows"] != [[1, "shirokuma"], [2, "polaris"]]:
        raise ValueError("retained fixture differs")
    schema = trino.query("DESCRIBE iceberg.smoke.fixture_v1")
    live_columns = []
    for row in schema["rows"]:
        data_type = row[1].split("(")[0].upper()
        if data_type not in {"BIGINT", "VARCHAR", "INTEGER", "INT"}:
            raise ValueError("unexpected fixture column type")
        column = {"name": row[0], "dataType": "INT" if data_type == "INTEGER" else data_type,
                  "dataTypeDisplay": row[1]}
        if data_type == "VARCHAR": column["dataLength"] = 2147483647
        live_columns.append(column)
    if [x["name"] for x in live_columns] != ["id", "name"]:
        raise ValueError("unexpected fixture schema")
    team = client.request("PUT", "/teams", {"name": "ShirokumaLocalExperiment", "teamType": "Group",
                "description": "Owner of generated Shirokuma local experiment metadata only."})
    owners = [{"id": team["id"], "type": "team"}]
    client.request("PUT", "/services/databaseServices", {"name": SERVICE, "serviceType": "Trino",
        "description": "Experimental Trino metadata observed through a managed host loopback tunnel.",
        "owners": owners, "connection": {"config": {"type": "Trino", "hostPort": "trino-local.shirokuma-dev.svc.cluster.local:8080",
                                              "username": "shirokuma-local-metadata", "catalog": "iceberg"}}})
    client.request("PUT", "/databases", {"name": "iceberg", "service": SERVICE, "owners": owners})
    for name in ("smoke", "local_experiment"):
        client.request("PUT", "/databaseSchemas", {"name": name, "database": SERVICE + ".iceberg", "owners": owners})
    fixture_entity = client.request("PUT", "/tables", {"name": "fixture_v1", "databaseSchema": SERVICE + ".iceberg.smoke",
        "columns": live_columns, "owners": owners, "description": "Retained fixture: schema and rows observed live through Trino."})
    tables = {"iceberg.smoke.fixture_v1": fixture_entity}
    for node in models.values():
        # dbt manifest lacks materialized SQL types; preserve that uncertainty.
        columns = [{"name": column["name"], "dataType": "UNKNOWN",
                    "description": "Column from retained dbt manifest; SQL type was not retained."} for column in node["columns"].values()]
        name = table_name(node)
        tables[name] = client.request("PUT", "/tables", {"name": node["alias"], "databaseSchema": SERVICE + ".iceberg.local_experiment",
            "columns": columns, "owners": owners,
            "description": f"Historical successful DataOps run {summary['run_token']}; generated SQL relation was removed by verified cleanup. Metadata retained for lineage; not a live table."})
    client.request("PUT", "/services/pipelineServices", {"name": PIPELINES, "serviceType": "Dagster",
        "description": "Local Dagster runs retained as evidence; no persistent Dagster server is claimed.", "owners": owners})
    parent_pipeline = client.request("PUT", "/pipelines", {"name": "local_dataops", "service": PIPELINES,
        "description": "Local Dagster asset-materialization runs; exact run IDs are retained in OpenLineage parent facets and ingestion evidence.", "owners": owners})
    events = events_for(summary, attempt, manifest, results, models)
    for event in events:
        client.request("PUT", "/pipelines", {"name": event["job"]["name"], "service": PIPELINES,
            "description": f"Historical dbt model execution in Dagster run {attempt['run_id']}; replayed from retained evidence.", "owners": owners})
    settings = {"enabled": True, "autoCreateEntities": False, "eventTypeFilter": ["COMPLETE"],
                "defaultPipelineService": PIPELINES, "namespaceToServiceMapping": {NAMESPACE: SERVICE}}
    client.request("PUT", "/system/settings", {"config_type": "openLineageSettings", "config_value": settings})
    responses = [client.request("POST", "/openlineage/lineage", event) for event in events]
    if any(response.get("status") != "success" or response.get("lineageEdgesCreated", 0) < 1
           for event, response in zip(events, responses) if event["eventType"] == "COMPLETE"):
        raise ValueError("OpenMetadata did not confirm native lineage creation")
    if any(response.get("status") != "success" or response.get("lineageEdgesCreated") != 0 for event, response in zip(events, responses)
           if event["eventType"] == "START"):
        raise ValueError("OpenMetadata did not confirm its configured START event filter")
    saved = {}
    for name, entity in tables.items():
        value = client.request("GET", "/tables/" + entity["id"] + "?fields=owners")
        if team["id"] not in {owner["id"] for owner in value.get("owners", [])}:
            raise ValueError("saved metadata owner differs")
        if [(c["name"], c["dataType"]) for c in value["columns"]] != [(c["name"], c["dataType"]) for c in entity["columns"]]:
            raise ValueError("saved metadata schema differs")
        saved[name] = {key: value[key] for key in ("id", "fullyQualifiedName", "columns", "owners")}
    lineage = client.request("GET", "/lineage/table/" + fixture_entity["id"] + "?upstreamDepth=3&downstreamDepth=3")
    edges = {(edge["fromEntity"], edge["toEntity"]) for edge in lineage["downstreamEdges"]}
    expected = {(tables[event["inputs"][0]["name"]]["id"], tables[event["outputs"][0]["name"]]["id"]) for event in events}
    if not expected <= edges:
        raise ValueError("native saved lineage graph does not contain the expected model chain")
    for edge in lineage["downstreamEdges"]:
        if (edge["fromEntity"], edge["toEntity"]) in expected:
            details = edge.get("lineageDetails", {})
            if details.get("source") != "OpenLineage" or details.get("pipeline", {}).get("id") != parent_pipeline["id"]:
                raise ValueError("saved lineage is not bound to the native OpenLineage Dagster pipeline")
    saved_pipeline = client.request("GET", "/pipelines/" + parent_pipeline["id"] + "?fields=owners")
    return {"result": "passed", "scope": "local-experiment-only", "openmetadata_version": client.request("GET", "/system/version"),
            "source_artifact_sha256": hashes, "dataops_run_token": summary["run_token"], "dagster_run_id": attempt["run_id"],
            "live_fixture": fixture, "live_schema": schema, "tables": saved, "events": events,
            "openlineage_responses": responses, "lineage": lineage, "dagster_pipeline": saved_pipeline,
            "historical_models_removed_from_trino": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataops-evidence", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    directory = args.dataops_evidence
    if directory is None:
        candidates = sorted((ROOT / "tmp/local-experiment/dataops").glob("*/summary.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        directory = next((p.parent for p in candidates if json.loads(p.read_text()).get("result") == "passed" and json.loads(p.read_text()).get("query_drain", {}).get("verified")), None)
    if directory is None: raise ValueError("successful DataOps evidence is required")
    read_evidence(directory)
    check_runtime()
    client = Client()
    client.authenticate(STATE)
    with interruption_handlers():
        check_context()
        with trino_tunnel() as url:
            report = ingest(client, directory, TrinoClient(url))
    target = STATE / ("ingestion-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    target.write_text(json.dumps(report, indent=2) + "\n")
    target.chmod(0o600)
    print(json.dumps({"result": "passed", "evidence": str(target.relative_to(ROOT)), "tables": len(report["tables"]), "native_lineage_edges": len(report["lineage"]["downstreamEdges"])}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SessionInterrupted as error:
        raise SystemExit(128 + error.signum)
    except Exception as error:
        print(f"metadata ingestion: {error}", file=sys.stderr)
        raise SystemExit(1)
