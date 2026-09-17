"""Catalog writes are bounded to generated tables/edges and conflicts fail closed."""
import copy
from pathlib import Path
import sys
import unittest
from urllib.parse import unquote
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/local_experiment"))
from metadata import curate
from metadata.client import APIError
from metadata.data_plan_evidence import EvidenceError, KINDS, _base_projection


def projection(operation="name_lengths"):
    value = _base_projection("a" * 32, operation)
    value["evidence"] = [{"id": f"e{i:02}", "kind": kind, "sha256": str(i) * 64}
                         for i, kind in enumerate(KINDS, 1)]
    value["dagster_run_ids"] = [str(uuid.UUID(int=i)) for i in (20, 21)]
    return value


class FakeClient:
    def __init__(self):
        self.calls, self.entities, self.edges = [], {}, {}
        self.next_id = 1
        self.fail_create_number = None
        self.drop_edge_writes = False
        self.hidden_lineage_target = None
        team = self.add("teams", curate.TEAM, teamType="Group")
        service = self.add("services/databaseServices", curate.SERVICE, serviceType="Trino")
        db = self.add("databases", curate.DATABASE, service=self.ref(service, "databaseService"))
        source_schema = self.add("databaseSchemas", curate.SOURCE_SCHEMA, database=self.ref(db, "database"))
        self.add("databaseSchemas", curate.SCHEMA, database=self.ref(db, "database"))
        self.add("tables", curate.SOURCE, databaseSchema=self.ref(source_schema, "databaseSchema"),
                 owners=[self.ref(team, "team")],
                 columns=[{"name": "id", "dataType": "BIGINT"}, {"name": "name", "dataType": "VARCHAR"}])

    def add(self, collection, name, **fields):
        value = {"id": str(uuid.UUID(int=self.next_id)), "name": name.rsplit(".", 1)[-1],
                 "fullyQualifiedName": name, "deleted": False, "version": 0.1, "updatedAt": 1, **fields}
        self.next_id += 1
        self.entities[(collection, name)] = value
        return value

    @staticmethod
    def ref(entity, kind):
        return {"id": entity["id"], "type": kind, "fullyQualifiedName": entity["fullyQualifiedName"]}

    @property
    def writes(self):
        return [call for call in self.calls if call[0] != "GET"]

    def request(self, method, path, data=None):
        self.calls.append((method, path, copy.deepcopy(data)))
        if method == "GET" and "/name/" in path:
            collection, name = path[1:].split("/name/", 1)
            value = self.entities.get((collection, unquote(name.split("?", 1)[0])))
            if value is None:
                raise APIError(method, path, 404)
            return copy.deepcopy(value)
        if method == "GET" and path.startswith("/lineage/table/"):
            identity = path.split("/")[3].split("?")[0]
            entity = next(value for value in self.entities.values() if value["id"] == identity)
            edges = [] if self.hidden_lineage_target == identity else list(self.edges.values())
            return {"entity": self.ref(entity, "table"), "nodes": [],
                    "upstreamEdges": copy.deepcopy([e for e in edges if e["toEntity"] == identity]),
                    "downstreamEdges": copy.deepcopy([e for e in edges if e["fromEntity"] == identity])}
        if method == "POST" and path == "/tables":
            number = sum(call[:2] == ("POST", "/tables") for call in self.calls)
            if self.fail_create_number == number:
                raise APIError(method, path, 409)
            name = data["databaseSchema"] + "." + data["name"]
            if ("tables", name) in self.entities:
                raise APIError(method, path, 409)
            fields = copy.deepcopy(data)
            del fields["name"]
            fields["databaseSchema"] = self.ref(self.entities[("databaseSchemas", curate.SCHEMA)], "databaseSchema")
            return copy.deepcopy(self.add("tables", name, **fields))
        if method == "PUT" and path == "/lineage":
            edge = data["edge"]
            pair = (edge["fromEntity"]["id"], edge["toEntity"]["id"])
            if not self.drop_edge_writes:
                self.edges[pair] = {"fromEntity": pair[0], "toEntity": pair[1],
                                    "lineageDetails": copy.deepcopy(edge["lineageDetails"])}
            return None  # pinned OpenMetadata returns an empty 200
        raise AssertionError(f"forbidden/unexpected request {method} {path}")


class CatalogCurateTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.projection = projection()

    def target(self, suffix):
        return self.client.entities[("tables", next(e["name"] for e in self.projection["entities"] if e["id"] == suffix))]

    def test_preflight_is_read_only_and_missing_fields_are_exact(self):
        state = curate.preflight(self.client, self.projection)
        self.assertEqual(state["targets"], {key: {"description": False, "owner": False, "lineage": False} for key in ("stg", "mart")})
        self.assertEqual(self.client.writes, [])
        self.assertNotIn("connection", str(state))

    def test_create_readback_noop_preserves_all_anchors(self):
        original = copy.deepcopy(self.client.entities)
        state = curate.preflight(self.client, self.projection)
        result = curate.apply(self.client, self.projection, state)
        self.assertEqual((result["created_tables"], result["created_edges"], result["writes"]), (2, 2, 4))
        for key, value in original.items():
            self.assertEqual(self.client.entities[key], value)
        self.assertEqual([call[:2] for call in self.client.writes],
                         [("POST", "/tables"), ("POST", "/tables"), ("PUT", "/lineage"), ("PUT", "/lineage")])
        self.client.calls.clear()
        repeated = curate.apply(self.client, self.projection)
        self.assertTrue(repeated["noop"])
        self.assertEqual(repeated["writes"], 0)
        self.assertEqual(self.client.writes, [])
        self.assertEqual((repeated["verified_tables"], repeated["verified_edges"]), (2, 2))

    def test_all_fixed_operations_generate_correct_column_schema(self):
        for operation in ("uppercase_names", "name_lengths", "row_count"):
            with self.subTest(operation=operation):
                client, evidence = FakeClient(), projection(operation)
                curate.apply(client, evidence)
                mart = client.entities[("tables", evidence["entities"][1]["name"])]
                self.assertEqual([(x["name"], x["dataType"]) for x in mart["columns"]],
                                 [(x["name"], x["type"]) for x in evidence["columns"]])

    def test_unsafe_projection_rejected_before_network(self):
        for change in ({"source": "elsewhere.fixture"}, {"operation": "unsupported"}, {"sql": "DROP"}):
            with self.subTest(change=change):
                value = {**self.projection, **change}
                with self.assertRaises(EvidenceError):
                    curate.apply(self.client, value)
                self.assertEqual(self.client.calls, [])

    def test_missing_or_deleted_anchor_is_not_recreated(self):
        del self.client.entities[("teams", curate.TEAM)]
        with self.assertRaises(APIError):
            curate.apply(self.client, self.projection)
        self.assertEqual(self.client.writes, [])
        self.client = FakeClient()
        self.client.entities[("tables", curate.SOURCE)]["deleted"] = True
        with self.assertRaises(curate.CurateError):
            curate.apply(self.client, self.projection)
        self.assertEqual(self.client.writes, [])

    def test_anchor_schema_owner_or_parent_conflicts_prevent_all_writes(self):
        for mutation in (lambda c: c.entities[("tables", curate.SOURCE)]["columns"].reverse(),
                         lambda c: c.entities[("tables", curate.SOURCE)].update(owners=[]),
                         lambda c: c.entities[("databaseSchemas", curate.SCHEMA)]["database"].update(id=str(uuid.UUID(int=99)))):
            self.client = FakeClient()
            mutation(self.client)
            with self.assertRaises(curate.CurateError):
                curate.apply(self.client, self.projection)
            self.assertEqual(self.client.writes, [])

    def test_existing_human_edits_rejected_before_missing_peer_is_created(self):
        for field, value in (("description", "human change"), ("owners", []),
                             ("columns", [{"name": "unexpected", "dataType": "BIGINT"}]), ("deleted", True)):
            with self.subTest(field=field):
                self.client = FakeClient()
                curate.apply(self.client, self.projection)
                del self.client.entities[("tables", self.projection["entities"][0]["name"])]
                self.client.edges.clear()
                self.target("mart")[field] = value
                self.client.calls.clear()
                with self.assertRaises(curate.CurateError):
                    curate.apply(self.client, self.projection)
                self.assertEqual(self.client.writes, [])

    def test_changed_artifact_digest_cannot_reuse_same_run_table(self):
        curate.apply(self.client, self.projection)
        self.projection["evidence"][0]["sha256"] = "f" * 64
        self.client.calls.clear()
        with self.assertRaises(curate.CurateError):
            curate.apply(self.client, self.projection)
        self.assertEqual(self.client.writes, [])

    def test_observation_version_drift_is_rejected_before_writes(self):
        state = curate.preflight(self.client, self.projection)
        self.client.entities[("teams", curate.TEAM)]["version"] = 0.2
        with self.assertRaises(curate.CurateError):
            curate.apply(self.client, self.projection, state)
        self.assertEqual(self.client.writes, [])

    def test_existing_foreign_or_edited_lineage_rejected_before_writes(self):
        for field, value in (("description", "edited"), ("source", "OpenLineage"), ("sqlQuery", "SELECT secret")):
            self.client = FakeClient()
            curate.apply(self.client, self.projection)
            next(iter(self.client.edges.values()))["lineageDetails"][field] = value
            self.client.calls.clear()
            with self.assertRaises(curate.CurateError):
                curate.apply(self.client, self.projection)
            self.assertEqual(self.client.writes, [])

    def test_extra_incident_lineage_rejected(self):
        curate.apply(self.client, self.projection)
        edge = copy.deepcopy(next(iter(self.client.edges.values())))
        edge["fromEntity"] = str(uuid.UUID(int=99))
        self.client.edges[(edge["fromEntity"], edge["toEntity"])] = edge
        self.client.calls.clear()
        with self.assertRaises(curate.CurateError):
            curate.apply(self.client, self.projection)
        self.assertEqual(self.client.writes, [])

    def test_failed_partial_create_is_retained_and_retry_completes(self):
        self.client.fail_create_number = 2
        progress = []
        with self.assertRaises(curate.CurateError) as caught:
            curate.apply(self.client, self.projection, progress=progress.append)
        self.assertEqual(caught.exception.partial_receipt["confirmed_tables"], {"stg": self.target("stg")["id"]})
        self.assertEqual(caught.exception.partial_receipt["pending"],
                         {"operation": "create_table", "target": "mart", "outcome": "unknown"})
        self.assertEqual(progress[-1], caught.exception.partial_receipt)
        self.assertEqual(len([key for key in self.client.entities if key[0] == "tables"]), 2)
        self.assertEqual(self.client.edges, {})
        self.client.fail_create_number = None
        result = curate.apply(self.client, self.projection)
        self.assertEqual((result["created_tables"], result["reused_tables"], result["created_edges"]), (1, 1, 2))

    def test_empty_successful_put_is_verified_by_readback(self):
        self.client.drop_edge_writes = True
        with self.assertRaises(curate.CurateError) as caught:
            curate.apply(self.client, self.projection)
        self.assertEqual(caught.exception.partial_receipt["confirmed_edges"], [])
        self.assertEqual(caught.exception.partial_receipt["pending"]["outcome"], "unknown")

    def test_progress_is_emitted_before_writes_and_after_confirmed_readback(self):
        journal = []

        def record(value):
            journal.append((copy.deepcopy(value), len(self.client.writes)))

        curate.apply(self.client, self.projection, progress=record)
        pending = [(item["pending"]["operation"], count) for item, count in journal if item["pending"]]
        self.assertEqual(pending, [("create_table", 0), ("create_table", 1), ("create_edge", 2), ("create_edge", 3)])
        self.assertEqual(journal[-1][0]["result"], "passed")
        self.assertTrue(journal[-1][0]["verified_complete"])
        self.assertEqual(journal[-1][0]["confirmed_edges"], ["stg", "mart"])

    def test_progress_storage_failure_prevents_first_mutation(self):
        def broken_store(value):
            raise OSError("private secret-bearing storage failure")

        with self.assertRaises(curate.CurateError) as caught:
            curate.apply(self.client, self.projection, progress=broken_store)
        self.assertEqual(self.client.writes, [])
        self.assertNotIn("secret-bearing", str(caught.exception))

    def test_inconsistent_two_endpoint_graph_cannot_claim_verified(self):
        curate.apply(self.client, self.projection)
        self.client.hidden_lineage_target = self.target("mart")["id"]
        self.client.calls.clear()
        with self.assertRaises(curate.CurateError):
            curate.apply(self.client, self.projection)
        self.assertEqual(self.client.writes, [])


if __name__ == "__main__":
    unittest.main()
