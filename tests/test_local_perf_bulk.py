"""One-shot bulk contracts, without required optional SDKs or runtime access."""
import copy
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.parse
import uuid

from benchmarks.local_perf import bulk
from benchmarks.local_perf.bulk import SDK as RealSDK
from benchmarks.local_perf.capacity import Campaign, write_json
from benchmarks.local_perf.public import PublicAdapter

CTX = dict(database_id="db", schema_id="schema", warehouse_id="LOCAL", sql_catalog="iceberg",
           sql_schema="raw", selected_role="data_engineer", binding_revision=1,
           generation_id="g1", grant_revision=1)
REF = dict(database_id="db", schema_id="schema", object_id="opaque-id", incarnation=1)
COLS = [dict(name="key", type="integer"), dict(name="amount", type="decimal(18,4)"),
        dict(name="day", type="date"), dict(name="at", type="timestamp(6)")]
AUTH = dict(format=1, uri="http://127.0.0.1:8181/api/catalog", warehouse="iceberg", realm="lab",
            client_id="db-consumer", client_secret="secret-do-not-record", storage_endpoint="http://127.0.0.1:8333",
            region="us-east-1", database_id="db", storage_prefix="s3://bucket/db/")


class FakePublic(PublicAdapter):
    def __init__(self):
        super().__init__("http://127.0.0.1:8888", "consumer", "private", "data_engineer", CTX)
        self.calls, self.selects, self.on_select = [], 0, lambda: None
        self.exists, self.resolve_error = False, False
        self.schemas = [dict(id="schema", sql_name="raw", capabilities=dict(manual_ingest=True))]

    def select(self, database, schema, timeout=5):
        assert (database, schema) == (CTX["database_id"], CTX["schema_id"])
        assert 0 < timeout <= 30
        self.selects += 1
        self.on_select()
        return self.identity()

    def request(self, method, url, deadline, body=None):
        assert method == "GET", "no public mutations"
        self.calls.append(url)
        p = urllib.parse.urlsplit(url)
        if p.path.endswith("/schemas"):
            return dict(items=copy.deepcopy(self.schemas))
        if p.path.endswith("/objects"):
            return dict(items=[dict(physical_name="p_events")] if self.exists else [], next_cursor=None)
        if p.path.endswith("/resolve"):
            if self.resolve_error:
                raise OSError("secret-do-not-record")
            physical = urllib.parse.parse_qs(p.query)["name"][0]
            return dict(ref=REF, object=dict(physical_name=physical, kind="TABLE"))
        raise AssertionError("unexpected public route")


class Output:
    def __init__(self, sdk, location):
        self.sdk, self.location = sdk, location

    def create(self, overwrite=False):
        assert overwrite is False
        self.sdk.assert_intent("upload")
        if self.location in self.sdk.objects:
            raise FileExistsError()
        self.sdk.objects[self.location] = bytearray()
        output = self
        class Stream:
            def __enter__(self):
                return self

            def write(self, block):
                output.sdk.chunks.append(len(block))
                output.sdk.objects[output.location].extend(block)
                output.sdk.on_write()
                if output.sdk.fail == "upload":
                    raise OSError("secret-do-not-record")

            def __exit__(self, *args):
                return False
        return Stream()


class FakeTable:
    def __init__(self, sdk, identifier, schema, properties):
        self.sdk, self.identifier, self.cols, self.properties = sdk, identifier, schema, properties
        self.metadata = SimpleNamespace(table_uuid=uuid.uuid4(), format_version=2, current_schema_id=0)
        def output(path):
            sdk.assert_intent("upload")
            return Output(sdk, path)
        self.io = SimpleNamespace(new_output=output)
        self.snap = None

    def location(self):
        return self.sdk.location or ("s3://bucket/db/" + str(self.metadata.table_uuid))

    def current_snapshot(self):
        return self.snap

    def add_files(self, paths, snapshot_properties, check_duplicate_files):
        self.sdk.assert_intent("commit")
        assert paths and len(paths) == len(set(paths)) and check_duplicate_files is True
        assert all(p.startswith(self.location() + "/data/") for p in paths)
        self.sdk.commit_table = self
        self.sdk.commits += 1
        if self.sdk.fail == "commit":
            raise OSError("secret-do-not-record")
        self.files = [SimpleNamespace(file=SimpleNamespace(file_path=p, record_count=self.sdk.part_rows[i]
                      if hasattr(self.sdk, "part_rows") else self.sdk.rows,
                      file_size_in_bytes=len(self.sdk.objects[p]))) for i, p in enumerate(paths)]
        self.snap = SimpleNamespace(snapshot_id=42, summary=SimpleNamespace(operation="append", additional_properties={
            **snapshot_properties, "total-records": str(self.sdk.rows), "added-records": str(self.sdk.rows),
            "total-data-files": str(len(paths)), "added-data-files": str(len(paths))}))
        self.sdk.on_commit()


    def scan(self, **kwargs):
        assert not kwargs or kwargs == {"options": {"include_empty_files": "true"}}
        return SimpleNamespace(plan_files=lambda: iter(self.files))


class FakeSDK:
    def __init__(self, test):
        self.test, self.rows, self.cols = test, 3, copy.deepcopy(COLS)
        self.creates, self.commits, self.objects, self.chunks = 0, 0, {}, []
        self.fail, self.location, self.tables = None, None, []
        self.on_write, self.on_commit, self.on_footer = lambda: None, lambda: None, lambda: None
        self._session = SimpleNamespace(close=Mock())

    def footer(self, path):
        self.on_footer()
        return copy.deepcopy(self.cols), copy.deepcopy(self.cols), self.rows

    def catalog(self, auth, deadline, check):
        assert auth == AUTH
        check()
        return self

    def create_table(self, identifier, schema, properties):
        self.assert_intent("create")
        assert set(properties) == {"format-version", "commit.retry.num-retries", bulk.OWNER, bulk.SOURCE}
        assert properties["format-version"] == "2" and properties["commit.retry.num-retries"] == "0"
        self.creates += 1
        if self.fail == "create":
            raise OSError("secret-do-not-record")
        table = FakeTable(self, identifier, schema, properties)
        self.tables.append(table)
        return table

    def schema(self, table):
        return table.cols

    def assert_intent(self, kind):
        j = self.test.journal()
        assert j["operations"][-1]["kind"] == kind
        assert j["operations"][-1]["status"] == "INTENT"
        assert j["status"] == "INCOMPLETE"


class BulkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.campaign = Campaign.create(self.root / "campaign")
        self.source = self.campaign.own("dataset", "dataset")
        self.raw = b"P" * (bulk.CHUNK * 2 + 17)
        self.parquet = self.source / "events.parquet"
        self.parquet.write_bytes(self.raw)
        self.meta = dict(format=1, status="SUCCESS", dataset_id="test/dataset", scale=.01,
                         mode="qualification", measured_logical_bytes=123,
                         tables=[dict(name="events", file=self.parquet.name, schema=COLS, rows=3,
                                      sha256=bulk.file_hash(self.parquet), file_bytes=len(self.raw))])
        self.write_meta()
        self.campaign.mark(self.source, "SUCCESS")
        self.auth_file = self.root / "auth.json"
        self.write_auth(AUTH)
        self.adapter, self.sdk = FakePublic(), FakeSDK(self)
        self.addCleanup(patch.stopall)
        patch.object(bulk, "SDK", return_value=self.sdk).start()
        patch.object(self.campaign, "check", return_value={}).start()

    def distinct_sources(self):
        for i, table in enumerate(self.meta["tables"][1:], 1):
            path = self.source / ("table_%d.parquet" % i)
            path.write_bytes(self.raw)
            table["file"] = path.name

    def write_meta(self):
        write_json(self.source / "dataset.json", self.meta)

    def write_auth(self, auth):
        self.auth_file.write_text(json.dumps(auth))
        self.auth_file.chmod(0o600)

    def journal(self):
        return json.loads((self.campaign.root / "load" / "load.json").read_text())

    def load(self, **kw):
        return bulk.load_bulk(self.campaign, self.source, "load", "p", self.adapter, self.auth_file, **kw)

    def test_success_streams_owned_copy_and_one_registration(self):
        path = self.load(timeout=1200)
        j = self.journal()
        self.assertEqual(path.name, "load.json")
        self.assertEqual((self.sdk.creates, self.sdk.commits), (1, 1))
        self.assertEqual(j["status"], "SUCCESS")
        self.assertEqual(self.sdk.chunks, [bulk.CHUNK, bulk.CHUNK, 17])
        self.assertEqual(bytes(next(iter(self.sdk.objects.values()))), self.raw)
        self.assertTrue(self.parquet.exists())
        t = j["tables"][0]
        self.assertEqual((t["committed_rows"], t["ref"], t["schema"]), (3, REF, COLS))
        self.assertEqual(t["snapshot"], 42)
        self.assertEqual(t["registeredfiles"], [dict(location=next(iter(self.sdk.objects)), record_count=3,
                                                  file_size_in_bytes=len(self.raw))])
        self.assertEqual(set(j["sdk_versions"]), {"pyiceberg", "pyarrow"})
        self.assertTrue(all(v == 1 for v in j["retry_attempts"].values()))
        self.assertIs(t["cleanup_clean"], True)
        self.assertEqual(self.sdk.tables[0].identifier, ("raw", "p_events"))
        self.assertEqual(self.sdk.tables[0].cols, COLS)
        self.assertEqual(j["source_manifest_sha256"], bulk.file_hash(self.source / "dataset.json"))
        self.assertGreaterEqual(self.adapter.selects, 5)
        self.assertNotIn(AUTH["client_secret"], path.read_text())
        self.assertNotIn(str(self.auth_file), path.read_text())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_empty_table_is_registered_without_rows_materialized(self):
        self.meta["tables"][0]["rows"] = self.sdk.rows = 0
        self.write_meta()
        self.load()
        self.assertEqual(self.journal()["tables"][0]["committed_rows"], 0)
        self.assertEqual(self.sdk.commits, 1)

    def source_size(self, size):
        original = Path.stat
        def stat(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path == self.parquet:
                fields = {k: getattr(result, k) for k in dir(result) if k.startswith("st_")}
                fields["st_size"] = size
                return SimpleNamespace(**fields)
            return result
        return patch.object(Path, "stat", stat)

    def test_source_above_multipart_limit_rejected_before_read_or_mutation(self):
        self.assertEqual(bulk.MAX_SOURCE_FILE_BYTES, 104857600000)
        with self.source_size(bulk.MAX_SOURCE_FILE_BYTES + 1), \
                patch.object(bulk, "file_hash", wraps=bulk.file_hash) as hashes, \
                patch.object(self.sdk, "footer", wraps=self.sdk.footer) as footer:
            with self.assertRaisesRegex(ValueError, "multipart size limit"):
                self.load()
        footer.assert_not_called()
        self.assertTrue(hashes.call_args_list)
        self.assertTrue(all(call.args[0] == self.source / "dataset.json"
                            for call in hashes.call_args_list))
        self.assertEqual((self.sdk.creates, self.sdk.commits), (0, 0))
        self.assertFalse(self.sdk.objects)
        self.assertEqual(self.journal()["operations"], [])
        self.assertEqual(self.parquet.read_bytes(), self.raw)

    def test_source_growth_after_raw_stat_is_rejected_before_hash(self):
        original = Path.stat
        calls = 0
        def growing(path, *args, **kwargs):
            nonlocal calls
            result = original(path, *args, **kwargs)
            if path == self.parquet:
                calls += 1
                if calls > 1:
                    fields = {k: getattr(result, k) for k in dir(result) if k.startswith("st_")}
                    fields["st_size"] = bulk.MAX_SOURCE_FILE_BYTES + 1
                    return SimpleNamespace(**fields)
            return result
        with patch.object(Path, "stat", growing), patch.object(bulk, "file_hash", wraps=bulk.file_hash) as hashes:
            with self.assertRaisesRegex(ValueError, "multipart size limit"):
                self.load()
        self.assertTrue(all(call.args[0].name == "dataset.json" for call in hashes.call_args_list))
        self.assertEqual(self.sdk.creates, 0)

    def test_source_growth_during_footer_is_rejected_before_second_hash(self):
        self.sdk.on_footer = lambda: self.parquet.write_bytes(self.raw + b"grown")
        with patch.object(bulk, "file_hash", wraps=bulk.file_hash) as hashes:
            with self.assertRaisesRegex(ValueError, "source schema/rows/hash mismatch"):
                self.load()
        self.assertEqual(sum(call.args[0] == self.parquet for call in hashes.call_args_list), 1)
        self.assertEqual(self.sdk.creates, 0)

    def test_source_at_multipart_limit_reaches_create(self):
        self.meta["tables"][0]["file_bytes"] = bulk.MAX_SOURCE_FILE_BYTES
        self.write_meta()
        self.sdk.create_table = Mock(side_effect=RuntimeError("exact cap reached create"))
        with self.source_size(bulk.MAX_SOURCE_FILE_BYTES), \
                patch.object(self.sdk, "footer", wraps=self.sdk.footer) as footer:
            with self.assertRaisesRegex(RuntimeError, "exact cap reached create"):
                self.load(peak_bytes=bulk.MAX_SOURCE_FILE_BYTES)
        footer.assert_called_once_with(self.parquet)
        self.sdk.create_table.assert_called_once()
        self.assertEqual(self.sdk.commits, 0)
        self.assertFalse(self.sdk.objects)

    def test_arbitrary_tables_and_scalar_contracts_share_target_naming(self):
        kinds = ("integer", "bigint", "boolean", "real", "double", "varchar", "date",
                 "decimal(38,12)", "timestamp(6)", "timestamp(6) with time zone")
        cols = [dict(name="field_" + str(i), type=k) for i, k in enumerate(kinds)]
        self.sdk.cols = cols
        self.meta["tables"] = [dict(self.meta["tables"][0], name=n, schema=cols)
                               for n in ("Mixed Case/table", "a_very_long_source_table_name_which_needs_hashing")]
        self.distinct_sources()
        self.write_meta()
        self.load()
        tables = self.journal()["tables"]
        self.assertEqual([t["physical_name"] for t in tables],
                         [bulk.target_name("p", t["name"]) for t in tables])
        self.assertTrue(all(len(t["physical_name"]) <= 31 and t["schema"] == cols for t in tables))
        self.assertEqual((self.sdk.creates, self.sdk.commits), (2, 2))

    def test_optional_dependency_failure_is_before_mutation(self):
        with patch.object(bulk, "SDK", side_effect=ImportError("optional SDK missing")):
            with self.assertRaises(ImportError):
                self.load()
        self.assertEqual(self.sdk.creates, 0)
        self.assertEqual(self.journal()["status"], "FAILED")

    def test_all_inputs_preflight_before_first_mutation(self):
        self.meta["tables"].append(dict(self.meta["tables"][0], name="other", rows=99))
        self.write_meta()
        with self.assertRaises(ValueError):
            self.load()
        self.assertEqual(self.sdk.creates, 0)
        self.assertEqual(self.journal()["status"], "FAILED")

    def test_preflight_mismatches_never_mutate(self):
        for field, value in (("rows", 4), ("file_bytes", 1), ("sha256", "0" * 64),
                             ("schema", [dict(name="key", type="bigint")])):
            with self.subTest(field=field):
                # Use fresh campaign output name for each failed preflight.
                table = copy.deepcopy(self.meta["tables"][0])
                self.meta["tables"][0][field] = value
                self.write_meta()
                with self.assertRaises(ValueError):
                    bulk.load_bulk(self.campaign, self.source, "bad_" + field, "p", self.adapter, self.auth_file)
                self.meta["tables"][0] = table
        self.assertEqual(self.sdk.creates, 0)

    def test_admin_account_and_exact_admin_role_are_allowed(self):
        self.adapter.account = "admin"
        self.adapter.role = "admin"
        self.adapter.context["selected_role"] = "admin"
        self.load()
        self.assertEqual(self.journal()["status"], "SUCCESS")
        self.assertGreaterEqual(sum(u.endswith("/schemas") for u in self.adapter.calls), 5)

    def test_viewer_or_unsupported_namespace_rejected_before_mutation(self):
        for role, namespace in (("bi_viewer", "raw"), ("admin", "private"), ("ADMIN", "raw")):
            self.adapter.role = role
            self.adapter.context.update(selected_role=role, sql_schema=namespace)
            with self.subTest(role=role, namespace=namespace), self.assertRaises(ValueError):
                self.load()
        self.assertEqual(self.sdk.creates, 0)

    def test_fresh_schema_exact_identity_and_capability(self):
        for schema in (dict(id="other", sql_name="raw", capabilities=dict(manual_ingest=True)),
                       dict(id="schema", sql_name="sandbox", capabilities=dict(manual_ingest=True)),
                       dict(id="schema", sql_name="raw", capabilities=dict(manual_ingest=1)),
                       dict(id="schema", sql_name="raw", capabilities={})):
            self.adapter.schemas = [schema]
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                bulk.load_bulk(self.campaign, self.source, "denied_" + str(len(self.campaign.state["owned"])),
                               "p", self.adapter, self.auth_file)
        self.assertEqual(self.sdk.creates, 0)

    def test_revocation_after_create_blocks_upload(self):
        original = self.sdk.create_table
        def create(*args, **kwargs):
            table = original(*args, **kwargs)
            self.adapter.schemas[0]["capabilities"]["manual_ingest"] = False
            return table
        self.sdk.create_table = create
        with self.assertRaises(ValueError):
            self.load()
        self.assertFalse(self.sdk.objects)
        self.assertEqual(self.sdk.commits, 0)

    def test_revocation_after_upload_blocks_commit(self):
        self.sdk.on_write = lambda: self.adapter.schemas[0]["capabilities"].update(manual_ingest=False)
        with self.assertRaises(ValueError):
            self.load()
        self.assertEqual(self.sdk.commits, 0)

    def test_wrong_commit_proofs_never_claim_rows(self):
        mutations = [lambda t: setattr(t.metadata, "table_uuid", uuid.uuid4()),
                     lambda t: setattr(t.snap, "snapshot_id", 0),
                     lambda t: setattr(t.snap, "snapshot_id", True),
                     lambda t: setattr(t.snap.summary, "operation", "overwrite"),
                     lambda t: setattr(t.files[0].file, "file_path", "s3://bucket/db/other/file"),
                     lambda t: setattr(t.files[0].file, "record_count", 4),
                     lambda t: setattr(t.files[0].file, "file_size_in_bytes", 1),
                     lambda t: t.files.append(t.files[0])]
        for key in (bulk.OWNER, bulk.SOURCE, "total-records", "added-records", "total-data-files", "added-data-files"):
            mutations.append(lambda t, key=key: t.snap.summary.additional_properties.update({key: "wrong"}))
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                # Each uncertain result intentionally blocks reuse of that campaign.
                self.campaign = Campaign.create(self.root / ("proof_" + str(index)))
                self.source = self.campaign.own("dataset", "dataset")
                self.parquet = self.source / "events.parquet"
                self.parquet.write_bytes(self.raw)
                self.write_meta()
                self.campaign.mark(self.source, "SUCCESS")
                self.campaign.check = Mock(return_value={})
                self.sdk.on_commit = lambda: mutate(self.sdk.tables[-1])
                with self.assertRaises(ValueError):
                    self.load()
                j = self.journal()
                self.assertEqual(j["status"], "UNKNOWN")
                self.assertEqual(j["tables"][0]["committed_rows"], 0)
                self.assertIsNone(j["tables"][0]["cleanup_clean"])
                self.assertEqual(j["operations"][-1]["acknowledgement"], "RETURNED")

    def test_invalid_initial_schema_id_stops_before_upload(self):
        for schema_id in (True, False, "0", -1):
            with self.subTest(schema_id=schema_id):
                self.setUp()
                create = self.sdk.create_table
                def invalid(*args, **kwargs):
                    table = create(*args, **kwargs)
                    table.metadata.current_schema_id = schema_id
                    return table
                with patch.object(self.sdk, "create_table", side_effect=invalid):
                    with self.assertRaisesRegex(ValueError, "current schema ID"):
                        self.load()
                self.assertEqual(self.sdk.objects, {})
                self.assertEqual(self.sdk.commits, 0)
                journal = self.journal()
                item = journal["tables"][0]
                self.assertEqual(item["uuid"], str(self.sdk.tables[-1].metadata.table_uuid))
                self.assertEqual(item["uuid_candidate"], item["uuid"])
                self.assertEqual(journal["operations"][0]["kind"], "create")
                self.assertEqual(journal["operations"][0]["status"], "RETURNED")
                self.assertTrue(all(op["status"] == "RETURNED" for op in journal["operations"]))
                self.assertEqual(journal["status"], "FAILED")
                self.assertEqual(item["uploadedfiles"], [])
                self.assertEqual(item["committed_rows"], 0)

    def test_changed_schema_id_in_commit_proof_never_claims_rows(self):
        self.sdk.on_commit = lambda: setattr(self.sdk.tables[-1].metadata, "current_schema_id", 1)
        with self.assertRaisesRegex(ValueError, "committed table identity/schema changed"):
            self.load()
        self.assertEqual(self.sdk.schema(self.sdk.tables[-1]), COLS)
        self.assertEqual(self.sdk.commits, 1)
        j = self.journal()
        self.assertEqual(j["status"], "UNKNOWN")
        self.assertEqual(j["tables"][0]["committed_rows"], 0)
        self.assertEqual(j["operations"][-1]["acknowledgement"], "RETURNED")

    @unittest.skipUnless(importlib.util.find_spec("pyiceberg"), "optional pinned SDK")
    def test_real_snapshot_operation_enum(self):
        from pyiceberg.table.snapshots import Operation
        self.sdk.on_commit = lambda: setattr(self.sdk.tables[-1].snap.summary, "operation", Operation.APPEND)
        self.load()
        self.assertEqual(self.journal()["status"], "SUCCESS")

    @unittest.skipUnless(importlib.util.find_spec("pyiceberg"), "optional pinned SDK")
    def test_real_overwrite_enum_rejected(self):
        from pyiceberg.table.snapshots import Operation
        self.sdk.on_commit = lambda: setattr(self.sdk.tables[-1].snap.summary, "operation", Operation.OVERWRITE)
        with self.assertRaisesRegex(ValueError, "summary mismatch"):
            self.load()
        self.assertEqual(self.journal()["tables"][0]["committed_rows"], 0)
        self.assertEqual(self.sdk.commits, 1)

    def assert_committed_evidence_retained(self, count=1):
        journal = self.journal()
        self.assertEqual(journal["status"], "FAILED")
        self.assertEqual((self.sdk.creates, self.sdk.commits), (count, count))
        self.assertEqual(len(self.sdk.objects), count)
        for item in journal["tables"]:
            self.assertEqual((item["committed_rows"], item["snapshot"]), (3, 42))
            self.assertEqual(item["ref"], REF)
            self.assertEqual(item["uploadedfiles"][0]["status"], "SUCCESS")
            self.assertEqual(len(item["registeredfiles"]), 1)
        self.assertTrue(all(op["status"] == "RETURNED" for op in journal["operations"]))
        self.assertEqual(journal["operations"][-1]["acknowledgement"], "RETURNED")

    def test_last_commit_source_mutation_retains_committed_evidence(self):
        self.sdk.on_commit = lambda: self.parquet.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "source changed after commit"):
            self.load()
        self.assert_committed_evidence_retained()

    def test_last_commit_manifest_mutation_retains_committed_evidence(self):
        self.sdk.on_commit = lambda: (self.source / "dataset.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "manifest changed after commit"):
            self.load()
        self.assert_committed_evidence_retained()

    def test_final_public_refresh_mutation_blocks_success(self):
        def select():
            if self.adapter.selects == 6:
                self.parquet.write_bytes(b"changed on final refresh")
        self.adapter.on_select = select
        with self.assertRaisesRegex(ValueError, "source changed after commit"):
            self.load()
        self.assertEqual(self.adapter.selects, 6)
        self.assert_committed_evidence_retained()

    def test_final_hash_check_includes_earlier_sources(self):
        second = self.source / "second.parquet"
        second.write_bytes(self.raw)
        self.meta["tables"].append(dict(self.meta["tables"][0], name="second", file=second.name))
        self.write_meta()
        def commit():
            if self.sdk.commits == 2:
                self.parquet.write_bytes(b"earlier source changed")
        self.sdk.on_commit = commit
        with self.assertRaisesRegex(ValueError, "source changed after commit"):
            self.load()
        self.assert_committed_evidence_retained(count=2)

    def test_recaptured_created_uuid_mismatch_retains_original(self):
        original = uuid.uuid4()
        create = self.sdk.create_table
        def mismatch(*args, **kwargs):
            table = create(*args, **kwargs)
            self.sdk.capture_create(original)
            return table
        self.sdk.create_table = mismatch
        with self.assertRaisesRegex(ValueError, "created/returned UUID mismatch"):
            self.load()
        item = self.journal()["tables"][0]
        self.assertEqual(item["uuid"], str(original))
        self.assertEqual(item["uuid_candidate"], str(self.sdk.tables[0].metadata.table_uuid))
        self.assertEqual((self.sdk.creates, self.sdk.commits), (1, 0))
        self.assertFalse(self.sdk.objects)

    def test_recaptured_same_created_uuid_is_idempotent(self):
        create = self.sdk.create_table
        def same(*args, **kwargs):
            table = create(*args, **kwargs)
            self.sdk.capture_create(table.metadata.table_uuid)
            return table
        self.sdk.create_table = same
        self.load()
        self.assertEqual(self.journal()["status"], "SUCCESS")
        self.assertEqual((self.sdk.creates, self.sdk.commits), (1, 1))

    def test_invalid_created_uuid_retains_safe_candidate_before_upload(self):
        create = self.sdk.create_table
        def invalid(*args, **kwargs):
            table = create(*args, **kwargs)
            table.metadata.table_uuid = uuid.UUID(int=0)
            return table
        self.sdk.create_table = invalid
        with self.assertRaises(ValueError):
            self.load()
        j = self.journal()
        self.assertIsNone(j["tables"][0]["uuid"])
        self.assertEqual(j["tables"][0]["uuid_candidate"], str(uuid.UUID(int=0)))
        self.assertEqual(j["status"], "UNKNOWN")
        self.assertFalse(self.sdk.objects)

    def test_metadata_read_failure_retains_returned_commit_snapshot(self):
        def fail():
            self.sdk.tables[-1].scan = Mock(side_effect=OSError("secret-do-not-record"))
        self.sdk.on_commit = fail
        with self.assertRaises(OSError):
            self.load()
        j = self.journal()
        self.assertEqual((j["tables"][0]["snapshot"], j["tables"][0]["committed_rows"]), (42, 0))
        self.assertEqual((j["status"], j["error_type"]), ("UNKNOWN", "OSError"))
        self.assertNotIn("secret-do-not-record", json.dumps(j))

    def test_manifest_format_identity_and_mode_budget(self):
        for index, change in enumerate(({"format": True}, {"format": 2}, {"dataset_id": " "},
                                        {"measured_logical_bytes": 1_000_000_001},
                                        {"measured_logical_bytes": True})):
            self.meta.update(change)
            self.write_meta()
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.load()
            self.meta.update(format=1, dataset_id="arbitrary/suite", measured_logical_bytes=123)
        self.assertEqual(self.sdk.creates, 0)

    def test_oauth_client_names_do_not_prove_scope(self):
        for client in ("root", "db-provisioner", "random-admin-oauth"):
            self.write_auth(dict(AUTH, client_id=client))
            self.assertEqual(bulk._auth(self.auth_file, CTX)["client_id"], client)

    def test_manifest_change_during_preflight(self):
        self.sdk.on_footer = lambda: (self.source / "dataset.json").write_text("{}")
        with self.assertRaises(ValueError):
            self.load()
        self.assertEqual(self.sdk.creates, 0)

    def test_changed_source_during_copy_prevents_commit_and_retains_objects(self):
        def change():
            self.parquet.write_bytes(b"Q" * len(self.raw))
        self.sdk.on_write = change
        with self.assertRaises(ValueError):
            self.load()
        self.assertEqual(self.sdk.commits, 0)
        self.assertEqual(self.journal()["status"], "UNKNOWN")
        self.assertTrue(self.sdk.objects)
        self.assertTrue(self.parquet.exists())

    def test_changed_source_after_copy_prevents_commit(self):
        original = bulk._copy
        def change(*args):
            count = original(*args)
            self.parquet.write_bytes(b"Q" * len(self.raw))
            return count
        with patch.object(bulk, "_copy", side_effect=change), self.assertRaises(ValueError):
            self.load()
        self.assertEqual(self.sdk.commits, 0)
        self.assertEqual(self.journal()["status"], "FAILED")
        self.assertTrue(self.sdk.objects)

    def assert_lost_mutation(self, failure):
        self.sdk.fail = failure
        with self.assertRaises(OSError):
            self.load()
        j = self.journal()
        self.assertEqual(j["status"], "UNKNOWN")
        self.assertEqual(self.sdk.creates, 1)
        self.assertEqual(self.sdk.commits, int(failure == "commit"))
        self.assertEqual(j["operations"][-1]["status"], "INTENT")
        self.assertNotIn("secret-do-not-record", json.dumps(j))
        self.assertTrue(self.parquet.exists())

    def test_lost_create_is_never_retried(self):
        self.assert_lost_mutation("create")

    def test_lost_upload_is_never_retried(self):
        self.assert_lost_mutation("upload")

    def test_lost_commit_is_never_retried(self):
        self.assert_lost_mutation("commit")

    def test_prior_unknown_and_incomplete_block_without_replay(self):
        for status in ("UNKNOWN", "INCOMPLETE"):
            prior = self.campaign.own(status.lower(), "bulk-load")
            self.campaign.mark(prior, status)
            with self.assertRaisesRegex(ValueError, "prior bulk"):
                self.load()
        self.assertEqual(self.sdk.creates, 0)
        self.assertFalse((self.campaign.root / "load").exists())

    def test_load_name_cannot_be_reused(self):
        self.load()
        with self.assertRaises(FileExistsError):
            self.load()
        self.assertEqual(self.sdk.creates, 1)

    def test_inventory_existing_target_fails_closed(self):
        self.adapter.exists = True
        with self.assertRaises(ValueError):
            self.load()
        self.assertEqual(self.sdk.creates, 0)

    def test_context_or_account_drift_before_mutation(self):
        self.adapter.on_select = lambda: self.adapter.context.update(generation_id="new")
        with self.assertRaisesRegex(ValueError, "frozen public"):
            self.load()
        self.assertEqual(self.sdk.creates, 0)

    def test_identity_drift_during_copy_leaves_unknown(self):
        self.sdk.on_write = lambda: setattr(self.adapter, "account", "different-consumer")
        with self.assertRaises(ValueError):
            self.load()
        self.assertEqual(self.sdk.commits, 0)
        self.assertEqual(self.journal()["status"], "UNKNOWN")

    def test_post_commit_observation_failure_preserves_count(self):
        self.adapter.resolve_error = True
        with self.assertRaises(OSError):
            self.load()
        self.assertEqual(self.journal()["tables"][0]["committed_rows"], 3)
        self.assertEqual(self.journal()["tables"][0]["snapshot"], 42)
        self.assertEqual(self.sdk.commits, 1)

    def test_later_table_loss_keeps_completed_table_evidence(self):
        self.meta["tables"].append(dict(self.meta["tables"][0], name="second"))
        self.distinct_sources()
        self.write_meta()
        self.sdk.on_commit = lambda: setattr(self.sdk, "fail", "create")
        with self.assertRaises(OSError):
            self.load()
        tables = self.journal()["tables"]
        self.assertEqual([t["committed_rows"] for t in tables], [3, 0])
        self.assertEqual(self.journal()["status"], "UNKNOWN")
        self.assertEqual(self.sdk.commits, 1)

    def test_outside_table_location_never_uploaded(self):
        self.sdk.location = "s3://bucket/unowned/table"
        with self.assertRaises(ValueError):
            self.load()
        self.assertFalse(self.sdk.objects)
        self.assertEqual(self.sdk.commits, 0)
        self.assertEqual(self.journal()["tables"][0]["uuid"], str(self.sdk.tables[0].metadata.table_uuid))

    def test_deadline_and_capacity_before_mutation(self):
        self.campaign.check.side_effect = RuntimeError("capacity")
        with self.assertRaises(RuntimeError):
            self.load()
        self.assertEqual(self.sdk.creates, 0)
        self.campaign.check.side_effect = None
        with self.assertRaises(TimeoutError):
            self.load(timeout=1e-12)
        self.assertEqual(self.sdk.creates, 0)

    def test_capacity_failure_during_upload_retains_unknown(self):
        self.sdk.on_write = lambda: setattr(self.campaign.check, "side_effect", RuntimeError("capacity"))
        with self.assertRaises(RuntimeError):
            self.load()
        self.assertEqual(self.sdk.commits, 0)
        self.assertEqual(self.journal()["status"], "UNKNOWN")

    def test_deadline_during_upload_prevents_commit(self):
        expired = time.monotonic() + 700
        clock = patch.object(bulk.time, "monotonic", return_value=expired)
        self.sdk.on_write = clock.start
        self.addCleanup(clock.stop)
        with self.assertRaises(TimeoutError):
            self.load()
        self.assertEqual(self.sdk.commits, 0)
        self.assertEqual(self.journal()["status"], "UNKNOWN")

    def test_performance_uses_manifest_peak_and_explicit_large_timeout(self):
        self.meta.update(mode="performance", scale=2, peak_bytes=14_000_000_000,
                         measured_logical_bytes=2_000_000_001, peak_evidence="pilot estimate")
        self.write_meta()
        self.load(timeout=7200)
        self.assertEqual(self.journal()["settings"]["peak_bytes"], 14_000_000_000)
        self.assertEqual(self.journal()["settings"]["timeout_s"], 7200)

    def test_timeout_validation_and_peak_override(self):
        for timeout in (True, 0, -1, float("inf"), float("nan"), "600"):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.load(timeout=timeout)
        self.load(peak_bytes=300_000_000)
        self.assertEqual(self.journal()["settings"]["peak_bytes"], 300_000_000)

    def test_source_peak_limit_and_duplicate_names_preflight(self):
        self.meta["tables"].append(copy.deepcopy(self.meta["tables"][0]))
        self.write_meta()
        with self.assertRaises(ValueError):
            self.load()
        self.assertEqual(self.sdk.creates, 0)

    def test_auth_strict_keys_mode_and_consumer_identity(self):
        for change in ({"extra": "x"}, {"format": True}, {"warehouse": "other"}, {"database_id": "other"},
                       {"storage_prefix": "s3://bucket/db/../other/"},
                       {"storage_prefix": "s3://user:secret@bucket/db/"}, {"storage_prefix": "s3://bucket/db/nested/"}):
            with self.subTest(change=change):
                self.write_auth(dict(AUTH, **change))
                with self.assertRaises(ValueError):
                    bulk._auth(self.auth_file, CTX)
        self.write_auth(AUTH)
        self.auth_file.chmod(0o644)
        with self.assertRaises(ValueError):
            bulk._auth(self.auth_file, CTX)
        self.auth_file.chmod(0o600)
        self.adapter.account = "platform-admin"
        self.load()
        self.assertEqual(self.sdk.creates, 1)

    def test_auth_missing_and_duplicate_fields(self):
        for key in bulk.AUTH_KEYS:
            a = dict(AUTH)
            del a[key]
            self.write_auth(a)
            with self.assertRaises(ValueError):
                bulk._auth(self.auth_file, CTX)
        self.auth_file.write_text(json.dumps(AUTH)[:-1] + ',"realm":"other"}')
        with self.assertRaises(ValueError):
            bulk._auth(self.auth_file, CTX)

    def test_endpoint_rejections(self):
        for endpoint in ("http://remote:80", "http://127.0.0.1", "http://127.0.0.1:80/",
                         "http://u:p@127.0.0.1:80", "http://127.0.0.1:80?x=1",
                         "http://127.0.0.1:80#secret", "file:///tmp/x", "http://127.0.0.1:bad"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                bulk._endpoint(endpoint)
        for endpoint in ("http://127.0.0.1:80/api/catalog/", "http://127.0.0.1:80/api/catalog/../admin",
                         "https://127.0.0.1:80/api/catalog?tls=false"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                bulk._endpoint(endpoint, True)
        self.assertEqual(bulk._endpoint("https://[::1]:8333"), "https://[::1]:8333")

    def test_unsupported_schema_is_not_widened(self):
        for kind in ("tinyint", "smallint", "hugeint", "ubigint", "timestamp(3)"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                bulk._columns([dict(name="x", type=kind)])
        self.assertEqual(bulk._columns([dict(name="x", type="INT32")]), [dict(name="x", type="integer")])


class MultifileTests(unittest.TestCase):
    setUp = BulkTests.setUp
    write_meta = BulkTests.write_meta
    write_auth = BulkTests.write_auth
    journal = BulkTests.journal
    load = BulkTests.load

    def shards(self, rows=(1, 2, 0)):
        files = []
        for i, count in enumerate(rows):
            path = self.source / ("part-%d.parquet" % i)
            # Deliberately identical content for distinct file names.
            path.write_bytes(b"parquet-footer-placeholder")
            files.append(dict(file=path.name, rows=count, file_bytes=path.stat().st_size,
                              physical_bytes=path.stat().st_size, sha256=bulk.file_hash(path)))
        self.meta["format"] = 2
        self.meta["tables"] = [dict(name="events", schema=COLS, rows=sum(rows), files=files,
            file_bytes=sum(f["file_bytes"] for f in files), physical_bytes=sum(f["physical_bytes"] for f in files))]
        self.sdk.rows = sum(rows)
        self.sdk.part_rows = rows
        self.sdk.footer = lambda p: (COLS, COLS, rows[int(p.stem.split("-")[-1])])
        self.loads, self.used_io = [], []
        self.on_load = lambda t: None
        def load(identifier):
            self.loads.append(identifier)
            table = copy.copy(self.sdk.tables[-1])
            table.metadata = copy.copy(table.metadata)
            table.properties = dict(table.properties)
            table.cols = copy.deepcopy(table.cols)
            self.on_load(table)
            generation = len(self.loads)
            def output(path):
                self.sdk.assert_intent("upload")
                self.used_io.append(generation)
                return Output(self.sdk, path)
            table.io = SimpleNamespace(new_output=output)
            table.io_generation = generation
            self.sdk.tables[-1] = table
            return table
        self.sdk.load_table = load
        self.write_meta()

    def test_three_parts_equal_hash_one_commit_fresh_io_and_ordered_receipt(self):
        self.shards()
        self.load()
        j = self.journal()
        t = j["tables"][0]
        self.assertEqual(j["status"], "SUCCESS")
        self.assertEqual((self.sdk.creates, self.sdk.commits, len(self.loads)), (1, 1, 3))
        self.assertEqual(self.used_io, [1, 2])
        self.assertEqual(self.sdk.commit_table.io_generation, 3)
        self.assertEqual(len(self.sdk.objects), 3)
        self.assertEqual([p["location"] for p in t["registeredfiles"]], list(self.sdk.objects))
        self.assertEqual(t["source_files"], self.meta["tables"][0]["files"])
        self.assertEqual(t["source_sha256"], bulk.file_identity(t["source_files"], version=2))
        self.assertEqual(t["committed_rows"], 3)

    def test_v1_optional_hash_and_sizes_preserve_digest_and_target_shape(self):
        self.meta["tables"][0].pop("sha256")
        self.meta["tables"][0].pop("file_bytes")
        self.write_meta()
        self.load()
        j = self.journal()
        t = j["tables"][0]
        sha = bulk.file_hash(self.parquet)
        self.assertEqual(t["source_sha256"], sha)
        self.assertNotIn("source_files", t)
        self.assertTrue(next(iter(self.sdk.objects)).endswith(j["ownership_token"] + "-" + sha + ".parquet"))
        self.assertEqual(self.adapter.selects, 6)

    def test_zero_rows_and_empty_part(self):
        self.shards((0, 0, 0))
        self.load()
        self.assertEqual(self.journal()["status"], "SUCCESS")
        self.assertEqual(self.sdk.commits, 1)
        self.assertEqual(self.journal()["tables"][0]["committed_rows"], 0)

    def test_preflight_rejects_unequal_fake_schemas_with_same_canonical_columns(self):
        self.shards((1, 2))
        schemas = [COLS, COLS + [dict(name="extra", type="integer")]]
        self.sdk.footer = lambda p: (schemas[int(p.stem.split("-")[-1])], COLS,
                                     self.sdk.part_rows[int(p.stem.split("-")[-1])])
        with self.assertRaisesRegex(ValueError, "source part schema mismatch"):
            self.load()
        self.assertEqual((self.sdk.creates, self.sdk.commits, self.sdk.objects), (0, 0, {}))

    def arrow_shards(self, schemas):
        import pyarrow as pa
        import pyarrow.parquet as pq
        self.shards((0,) * len(schemas))
        for part, schema in zip(self.meta["tables"][0]["files"], schemas):
            path = self.source / part["file"]
            with pq.ParquetWriter(path, schema):
                pass
            part.update(file_bytes=path.stat().st_size, physical_bytes=path.stat().st_size,
                        sha256=bulk.file_hash(path))
        table = self.meta["tables"][0]
        table["file_bytes"] = table["physical_bytes"] = sum(p["file_bytes"] for p in table["files"])
        self.write_meta()
        sdk = object.__new__(RealSDK)
        sdk.pa, sdk.pq = pa, pq
        self.sdk.footer = sdk.footer
        self.sdk.schema = lambda table: bulk._arrow_columns(table.cols, pa)

    @unittest.skipUnless(importlib.util.find_spec("pyarrow"), "optional real PyArrow footer")
    def test_preflight_rejects_part_two_nullability_mismatch(self):
        import pyarrow as pa
        optional = pa.schema([("key", pa.int32()), ("amount", pa.decimal128(18, 4)),
                              ("day", pa.date32()), ("at", pa.timestamp("us"))])
        required = optional.set(0, pa.field("key", pa.int32(), nullable=False))
        self.assertEqual(bulk._arrow_columns(optional, pa), bulk._arrow_columns(required, pa))
        self.arrow_shards([optional, required])
        with self.assertRaisesRegex(ValueError, "source part schema mismatch"):
            self.load()
        self.assertEqual((self.sdk.creates, self.sdk.commits, self.sdk.objects), (0, 0, {}))

    @unittest.skipUnless(importlib.util.find_spec("pyarrow"), "optional real PyArrow footer")
    def test_equal_arrow_schemas_ignore_metadata_and_create_with_first_schema(self):
        import pyarrow as pa
        first = pa.schema([("key", pa.int32()), ("amount", pa.decimal128(18, 4)),
                           ("day", pa.date32()), ("at", pa.timestamp("us"))],
                          metadata={b"source": b"first"})
        second = first.with_metadata({b"source": b"second"})
        self.assertFalse(first.equals(second, check_metadata=True))
        self.arrow_shards([first, second])
        with patch.object(self.sdk, "create_table", wraps=self.sdk.create_table) as create:
            self.load()
        self.assertTrue(create.call_args.kwargs["schema"].equals(first, check_metadata=True))
        self.assertEqual(self.journal()["status"], "SUCCESS")
        self.assertEqual((self.sdk.creates, self.sdk.commits, len(self.sdk.objects)), (1, 1, 2))

    def test_preflight_invalid_last_part_and_layout(self):
        changes = [lambda t: t["files"][-1].update(rows=5),
                   lambda t: t["files"][-1].update(sha256="0" * 64),
                   lambda t: t["files"][-1].update(file_bytes=1),
                   lambda t: t["files"][-1].update(extra="unexpected"),
                   lambda t: t.update(rows=True), lambda t: t.update(file="events.parquet"),
                   lambda t: t.update(physical_bytes=0), lambda t: t.update(files=[]),
                   lambda t: t.update(schema=[dict(name="key", type="bigint")]),
                   lambda t: t["files"][-1].update(file=t["files"][0]["file"])]
        for i, change in enumerate(changes):
            with self.subTest(i=i):
                self.shards()
                change(self.meta["tables"][0])
                self.write_meta()
                with self.assertRaises(ValueError):
                    bulk.load_bulk(self.campaign, self.source, "bad%d" % i, "p", self.adapter, self.auth_file)
                self.assertEqual(self.sdk.creates, 0)

    def test_duplicate_path_across_distinct_tables_rejected(self):
        self.shards()
        self.meta["tables"].append(dict(self.meta["tables"][0], name="second"))
        self.write_meta()
        with self.assertRaisesRegex(ValueError, "duplicate source path"):
            self.load()
        self.assertEqual(self.sdk.creates, 0)

    def test_all_raw_file_sizes_checked_before_any_footer_or_file_hash(self):
        self.shards()
        last = self.source / self.meta["tables"][0]["files"][-1]["file"]
        original = Path.stat
        def stat(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path == last:
                fields = list(result)
                fields[6] = bulk.MAX_SOURCE_FILE_BYTES + 1
                return type(result)(fields)
            return result
        with patch.object(Path, "stat", stat), patch.object(self.sdk, "footer") as footer, \
                patch.object(bulk, "file_hash", wraps=bulk.file_hash) as hashes:
            with self.assertRaisesRegex(ValueError, "multipart size limit"):
                self.load()
        footer.assert_not_called()
        self.assertTrue(all(c.args[0].name == "dataset.json" for c in hashes.call_args_list))
        self.assertEqual(self.sdk.creates, 0)

    def test_refresh_drifts_before_next_part_and_commit(self):
        changes = [lambda t: setattr(t.metadata, "table_uuid", uuid.uuid4()),
                   lambda t: setattr(self.sdk, "location", "s3://bucket/db/foreign"),
                   lambda t: t.properties.update({bulk.OWNER: "foreign"}),
                   lambda t: t.properties.update({bulk.SOURCE: "foreign"}),
                   lambda t: t.properties.update({"commit.retry.num-retries": "1"}),
                   lambda t: setattr(t.metadata, "format_version", 1),
                   lambda t: setattr(t, "cols", []), lambda t: setattr(t, "snap", object())]
        for boundary in (1, 3):
            for i, change in enumerate(changes):
                with self.subTest(boundary=boundary, i=i):
                    self.setUp()
                    self.shards()
                    self.sdk.location = None
                    self.sdk.objects = {}
                    def drift(t):
                        if len(self.loads) == boundary:
                            change(t)
                    self.on_load = drift
                    with self.assertRaises(ValueError):
                        self.load()
                    j = self.journal()
                    self.assertEqual(j["tables"][0]["uuid"], j["tables"][0]["uuid_candidate"])
                    self.assertEqual(len(self.sdk.objects), boundary)
                    self.assertEqual(self.sdk.commits, 0)
                    self.assertEqual(j["status"], "FAILED")

    def test_schema_id_change_with_same_columns_stops_before_next_part_or_commit(self):
        for boundary in (1, 3):
            with self.subTest(boundary=boundary):
                self.setUp()
                self.shards()
                def drift(table):
                    if len(self.loads) == boundary:
                        table.metadata.current_schema_id = 1
                self.on_load = drift
                with self.assertRaisesRegex(ValueError, "refreshed table identity/schema"):
                    self.load()
                self.assertEqual(self.sdk.schema(self.sdk.tables[-1]), COLS)
                self.assertEqual(len(self.sdk.objects), boundary)
                self.assertEqual(self.sdk.commits, 0)
                self.assertEqual(self.journal()["status"], "FAILED")
                self.assertEqual(self.journal()["tables"][0]["committed_rows"], 0)

    def test_valid_nonzero_schema_id_succeeds_for_both_manifest_versions(self):
        for version in (1, 2):
            with self.subTest(version=version):
                self.setUp()
                if version == 2:
                    self.shards()
                create = self.sdk.create_table
                def nonzero(*args, **kwargs):
                    table = create(*args, **kwargs)
                    table.metadata.current_schema_id = 7
                    return table
                with patch.object(self.sdk, "create_table", side_effect=nonzero):
                    self.load()
                self.assertEqual(self.journal()["status"], "SUCCESS")
                self.assertEqual(self.journal()["tables"][0]["committed_rows"], 3)

    def test_lost_second_upload_retains_first_and_blocks_replay(self):
        self.shards()
        def fail(t):
            self.sdk.fail = "upload"
        self.on_load = fail
        with self.assertRaises(OSError):
            self.load()
        j = self.journal()
        self.assertEqual(j["status"], "UNKNOWN")
        self.assertEqual([p["status"] for p in j["tables"][0]["uploadedfiles"]], ["SUCCESS", "INTENT"])
        self.assertEqual(len(self.sdk.objects), 2)
        self.assertEqual(self.sdk.commits, 0)
        with self.assertRaisesRegex(ValueError, "prior bulk"):
            bulk.load_bulk(self.campaign, self.source, "retry", "p", self.adapter, self.auth_file)
        self.assertEqual(self.sdk.creates, 1)

    def test_lost_commit_retains_all_parts_no_replay(self):
        self.shards()
        self.sdk.fail = "commit"
        with self.assertRaises(OSError):
            self.load()
        self.assertEqual(self.journal()["status"], "UNKNOWN")
        self.assertEqual((len(self.sdk.objects), self.sdk.commits), (3, 1))
        self.assertEqual(self.journal()["tables"][0]["committed_rows"], 0)

    def test_strict_manifest_format(self):
        for version in (True, False, 1.0, 2.0, "2", 0, 3):
            self.meta["format"] = version
            self.write_meta()
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.load()
        self.assertEqual(self.sdk.creates, 0)

    def test_read_refresh_failure_retains_known_partial_uploads(self):
        self.shards()
        self.sdk.load_table = Mock(side_effect=OSError("read failed"))
        with self.assertRaises(OSError):
            self.load()
        self.assertEqual(self.journal()["status"], "FAILED")
        self.assertEqual(len(self.sdk.objects), 1)
        self.assertEqual(self.sdk.commits, 0)
        self.sdk.load_table.assert_called_once()

    def test_later_table_failure_preserves_completed_multifile_count(self):
        self.shards()
        other = copy.deepcopy(self.meta["tables"][0])
        other["name"] = "later"
        for i, part in enumerate(other["files"]):
            path = self.source / ("later-%d.parquet" % i)
            path.write_bytes((self.source / part["file"]).read_bytes())
            part["file"] = path.name
        self.meta["tables"].append(other)
        self.write_meta()
        self.sdk.on_commit = lambda: setattr(self.sdk, "fail", "create")
        with self.assertRaises(OSError):
            self.load()
        self.assertEqual([t["committed_rows"] for t in self.journal()["tables"]], [3, 0])
        self.assertEqual(self.sdk.commits, 1)

    def test_source_mutation_after_commit_retains_multifile_rows(self):
        self.shards()
        last = self.source / self.meta["tables"][0]["files"][-1]["file"]
        self.sdk.on_commit = lambda: last.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "source changed after commit"):
            self.load()
        self.assertEqual(self.journal()["tables"][0]["committed_rows"], 3)
        self.assertEqual(self.journal()["status"], "FAILED")
        self.assert_proven_commit()

    def assert_proven_commit(self, tables=1):
        j = self.journal()
        self.assertEqual(j["status"], "FAILED")
        self.assertEqual(self.sdk.commits, tables)
        for item in j["tables"]:
            self.assertEqual((item["committed_rows"], item["snapshot"]), (3, 42))
            self.assertEqual(len(item["registeredfiles"]), 3)
            self.assertTrue(all(f["status"] == "SUCCESS" for f in item["uploadedfiles"]))
        self.assertTrue(all(op["status"] == "RETURNED" for op in j["operations"]))
        self.assertEqual(j["operations"][-1]["acknowledgement"], "RETURNED")

    def second_table(self):
        other = copy.deepcopy(self.meta["tables"][0])
        other["name"] = "later"
        for i, part in enumerate(other["files"]):
            path = self.source / ("later-%d.parquet" % i)
            path.write_bytes((self.source / part["file"]).read_bytes())
            part["file"] = path.name
        self.meta["tables"].append(other)
        self.write_meta()

    def test_source_hash_calls_scale_linearly(self):
        counts = []
        for n in (4, 8):
            self.setUp()
            self.shards((1,) * n)
            with patch.object(bulk, "file_hash", wraps=bulk.file_hash) as hashes:
                self.load()
            counts.append(sum(call.args[0].suffix == ".parquet" for call in hashes.call_args_list))
        self.assertGreater(counts[0], 0)
        self.assertLessEqual(counts[1], 2 * counts[0])
        self.assertLessEqual(counts[1], 9 * 8)

    def test_earlier_uploaded_part_tamper_caught_before_commit(self):
        self.shards()
        first = self.source / self.meta["tables"][0]["files"][0]["file"]
        def mutate(t):
            if len(self.loads) == 2:
                first.write_bytes(b"x" * first.stat().st_size)
        self.on_load = mutate
        with self.assertRaisesRegex(ValueError, "source changed before commit"):
            self.load()
        self.assertEqual(self.sdk.commits, 0)
        self.assertEqual(len(self.sdk.objects), 3)
        self.assertEqual(self.journal()["status"], "FAILED")

    def test_postcommit_manifest_mutation_retains_multifile_evidence(self):
        self.shards()
        self.sdk.on_commit = lambda: (self.source / "dataset.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "manifest changed after commit"):
            self.load()
        self.assert_proven_commit()

    def test_later_commit_source_mutation_retains_both_table_counts(self):
        self.shards()
        self.second_table()
        first = self.source / self.meta["tables"][0]["files"][0]["file"]
        def mutate():
            if self.sdk.commits == 2:
                first.write_bytes(b"x" * first.stat().st_size)
        self.sdk.on_commit = mutate
        with self.assertRaisesRegex(ValueError, "source changed after commit"):
            self.load()
        self.assert_proven_commit(tables=2)

    def test_postcommit_observation_failure_retains_multifile_evidence(self):
        self.shards()
        self.adapter.resolve_error = True
        with self.assertRaises(OSError):
            self.load()
        self.assert_proven_commit()

    def test_zero_summary_counters_are_strict_in_both_formats(self):
        for version in (1, 2):
            for key in ("added-records", "total-records"):
                for value in (None, "1", 0, False, "00", "bad"):
                    with self.subTest(version=version, key=key, value=value):
                        self.setUp()
                        if version == 2:
                            self.shards((0, 0, 0))
                        else:
                            self.meta["tables"][0]["rows"] = self.sdk.rows = 0
                            self.write_meta()
                        def change():
                            self.sdk.tables[-1].snap.summary.additional_properties[key] = value
                        self.sdk.on_commit = change
                        with self.assertRaisesRegex(ValueError, "summary mismatch"):
                            self.load()
                        self.assertEqual(self.journal()["status"], "UNKNOWN")
                        self.assertEqual(self.journal()["tables"][0]["committed_rows"], 0)

    @unittest.skipUnless(importlib.util.find_spec("pyiceberg"), "optional pinned SDK")
    def test_actual_zero_datafile_summary_proves_both_formats(self):
        from pyiceberg.manifest import DataFile, DataFileContent, FileFormat
        from pyiceberg.typedef import Record
        from pyiceberg.schema import Schema
        from pyiceberg.table.snapshots import SnapshotSummaryCollector, Summary, Operation, update_snapshot_summaries
        for version in (1, 2):
            with self.subTest(version=version):
                self.setUp()
                if version == 2:
                    self.shards((0, 0, 0))
                else:
                    self.meta["tables"][0]["rows"] = self.sdk.rows = 0
                    self.write_meta()
                def actual_summary():
                    table = self.sdk.tables[-1]
                    collector = SnapshotSummaryCollector()
                    files = []
                    for task in table.files:
                        f = task.file
                        data = DataFile.from_args(content=DataFileContent.DATA, file_path=f.file_path,
                            file_format=FileFormat.PARQUET, partition=Record(), record_count=0,
                            file_size_in_bytes=f.file_size_in_bytes)
                        collector.add_file(data, Schema())
                        files.append(SimpleNamespace(file=data))
                    props = {bulk.OWNER: table.properties[bulk.OWNER], bulk.SOURCE: table.properties[bulk.SOURCE]}
                    summary = update_snapshot_summaries(Summary(operation=Operation.APPEND, **collector.build(), **props))
                    self.assertNotIn("added-records", summary.additional_properties)
                    table.files = files
                    table.snap.summary = summary
                self.sdk.on_commit = actual_summary
                self.load()
                self.assertEqual(self.journal()["status"], "SUCCESS")
                self.assertEqual(len(self.journal()["tables"][0]["registeredfiles"]), 3 if version == 2 else 1)

    def test_registered_file_proofs_never_claim_rows(self):
        changes = [lambda t: t.files.pop(), lambda t: t.files.append(t.files[0]),
                   lambda t: setattr(t.files[-1].file, "record_count", 99),
                   lambda t: setattr(t.files[-1].file, "file_path", "s3://bucket/db/foreign"),
                   lambda t: setattr(t.files[-1].file, "file_size_in_bytes", True),
                   lambda t: setattr(t, "cols", [])]
        for i, change in enumerate(changes):
            with self.subTest(i=i):
                self.setUp()
                self.shards()
                self.sdk.on_commit = lambda: change(self.sdk.tables[-1])
                with self.assertRaises(ValueError):
                    self.load()
                self.assertEqual(self.journal()["tables"][0]["committed_rows"], 0)
                self.assertEqual(self.journal()["status"], "UNKNOWN")


@unittest.skipUnless(importlib.util.find_spec("pyiceberg") and importlib.util.find_spec("pyarrow"), "optional pinned SDK")
class SDKBoundaryTests(unittest.TestCase):
    """Use actual SDK serialization, response parsing, and Arrow filesystem API."""
    def setUp(self):
        import requests
        self.requests = []
        self.status = 200
        self.response_body = {}
        test = self
        def request(session, method, url, **kwargs):
            test.requests.append((method, url, kwargs, session.trust_env))
            response = requests.Response()
            response.status_code = 200 if url.endswith(("/config", "/tokens")) else test.status
            response.url = url
            response._content = json.dumps({"defaults": {}, "overrides": {}} if url.endswith("/config")
                else {"access_token": "fake-token", "token_type": "bearer"} if url.endswith("/tokens")
                else {**test.response_body, **getattr(test, "load_override", {})} if method == "GET"
                else test.response_body).encode()
            return response
        self.patcher = patch.object(requests.Session, "request", request)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.sdk = bulk.SDK()
        self.catalog = self.sdk.catalog(AUTH, time.monotonic() + 600, Mock())
        from pyiceberg.schema import Schema
        from pyiceberg.types import NestedField, LongType
        from pyiceberg.table.metadata import new_table_metadata
        self.schema = Schema(NestedField(1, "key", LongType()))
        from pyiceberg.partitioning import UNPARTITIONED_PARTITION_SPEC
        from pyiceberg.table.sorting import UNSORTED_SORT_ORDER
        self.metadata = new_table_metadata(self.schema, UNPARTITIONED_PARTITION_SPEC, UNSORTED_SORT_ORDER,
                                           location="s3://bucket/db/table", properties={})
        self.sts = {"s3.access-key-id": "access", "s3.secret-access-key": "secret", "s3.session-token": "session"}
        self.response_body = {"metadata": self.metadata.model_dump(mode="json", by_alias=True),
            "metadata-location": "s3://bucket/db/table/metadata/v1.json", "config": self.sts}
        self.captured = []
        self.catalog.capture_create = self.captured.append

    def test_real_create_commit_401_are_each_one_request(self):
        from pyiceberg.exceptions import UnauthorizedError
        self.status = 401
        self.response_body = {"error": {"message": "denied", "type": "UnauthorizedException", "code": 401}}
        with self.assertRaises(UnauthorizedError):
            self.catalog.create_table(("raw", "events"), self.schema)
        table = SimpleNamespace(name=lambda: ("raw", "events"), config={})
        with self.assertRaises(UnauthorizedError):
            self.catalog.commit_table(table, (), ())
        self.assertEqual(sum(method == "POST" and not url.endswith("/tokens") for method, url, *_ in self.requests), 2)
        for _, _, kw, trust_env in self.requests:
            self.assertFalse(trust_env)
            self.assertFalse(kw["allow_redirects"])
            self.assertTrue(0 < kw["timeout"] <= 30)
        self.assertTrue(all(a.max_retries.total == 0 for a in self.catalog._session.adapters.values()))

    def test_real_create_sts_and_actual_s3_filesystem(self):
        table = self.catalog.create_table(("raw", "events"), self.schema)
        self.assertEqual(self.captured, [self.metadata.table_uuid])
        from pyarrow.fs import S3FileSystem
        self.assertIsInstance(table.io._initialize_s3_fs("bucket"), S3FileSystem)
        self.assertEqual(table.io.properties, self.sts)
        for key in self.sts:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "STS"):
                self.catalog._load_file_io({k: v for k, v in self.sts.items() if k != key})
        with self.assertRaises(ValueError):
            table.io.new_output("s3://bucket/other/file")

    def test_create_without_sts_captures_uuid_then_loads_once(self):
        self.response_body["config"] = {}
        with self.assertRaisesRegex(ValueError, "STS"):
            self.catalog.create_table(("raw", "events"), self.schema)
        self.assertEqual(self.captured, [self.metadata.table_uuid])
        self.assertEqual([m for m, u, *_ in self.requests if not u.endswith(("/config", "/tokens"))], ["POST", "GET"])

    def test_create_without_sts_loads_vended_credentials_once(self):
        self.response_body["config"] = {}
        self.load_override = {"config": self.sts}
        table = self.catalog.create_table(("raw", "events"), self.schema)
        self.assertEqual(table.io.properties, self.sts)
        self.assertEqual(self.captured, [self.metadata.table_uuid])
        self.assertEqual([m for m, u, *_ in self.requests if not u.endswith(("/config", "/tokens"))], ["POST", "GET"])

    def test_load_uuid_mismatch_rejected_before_io(self):
        self.response_body["config"] = {}
        metadata = dict(self.response_body["metadata"], **{"table-uuid": str(uuid.uuid4())})
        self.load_override = {"metadata": metadata, "config": self.sts}
        with self.assertRaisesRegex(ValueError, "UUID mismatch"):
            self.catalog.create_table(("raw", "events"), self.schema)
        self.assertEqual(self.captured, [self.metadata.table_uuid])

    def test_load_location_mismatch_retains_original_uuid(self):
        self.response_body["config"] = {}
        metadata = dict(self.response_body["metadata"], location="s3://bucket/db/other-table")
        self.load_override = {"metadata": metadata, "config": self.sts}
        with self.assertRaisesRegex(ValueError, "location mismatch"):
            self.catalog.create_table(("raw", "events"), self.schema)
        self.assertEqual(self.captured, [self.metadata.table_uuid])
        self.assertEqual([m for m, u, *_ in self.requests if not u.endswith(("/config", "/tokens"))], ["POST", "GET"])

    def test_load_location_trailing_slash_is_normalized(self):
        self.response_body["config"] = {}
        metadata = dict(self.response_body["metadata"], location="s3://bucket/db/table/")
        self.load_override = {"metadata": metadata, "config": self.sts}
        table = self.catalog.create_table(("raw", "events"), self.schema)
        self.assertEqual(table.location().rstrip("/"), self.metadata.location)
        self.assertEqual(self.captured, [self.metadata.table_uuid])

    def test_real_commit_response_captured_before_return(self):
        captured = []
        self.catalog.capture_commit = captured.append
        table = SimpleNamespace(name=lambda: ("raw", "events"), config={})
        response = self.catalog.commit_table(table, (), ())
        self.assertEqual(captured, [response.metadata])
        self.assertEqual(sum(m == "POST" and not u.endswith("/tokens") for m, u, *_ in self.requests), 1)

    def test_same_origin_redirect_and_deadline(self):
        before = len(self.requests)
        with self.assertRaises(ValueError):
            self.catalog._session.get("http://evil:80/api/catalog/v1/config")
        self.assertEqual(len(self.requests), before)
        self.status = 307
        with self.assertRaisesRegex(ValueError, "redirect"):
            self.catalog._session.get(AUTH["uri"] + "/v1/tables")
        expired = self.sdk.catalog(AUTH, time.monotonic() + 1, Mock())
        with patch.object(bulk.time, "monotonic", return_value=time.monotonic() + 3):
            with self.assertRaises(TimeoutError):
                expired._session.get(AUTH["uri"] + "/v1/tables")


@unittest.skipUnless(importlib.util.find_spec("pyarrow"), "optional real PyArrow footer")
class ArrowFooterTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("pyiceberg"), "optional pinned SDK")
    def test_real_sdk_registers_multiple_local_parquet_files_once(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        from pyiceberg.table import Table
        from pyiceberg.catalog import CommitTableResponse
        from pyiceberg.io.pyarrow import PyArrowFileIO
        from pyiceberg.schema import Schema
        from pyiceberg.types import NestedField, IntegerType
        from pyiceberg.table.metadata import new_table_metadata
        from pyiceberg.table.update import update_table_metadata
        from pyiceberg.partitioning import UNPARTITIONED_PARTITION_SPEC
        from pyiceberg.table.sorting import UNSORTED_SORT_ORDER
        for rowsets in (([1, 2], [], [3]), ([], [], [])):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "metadata").mkdir()
                paths = []
                for i, rows in enumerate(rowsets):
                    path = root / ("part%d.parquet" % i)
                    pq.write_table(pa.table({"key": pa.array(rows, type=pa.int32())}), path)
                    paths.append(str(path))
                metadata = new_table_metadata(Schema(NestedField(1, "key", IntegerType(), required=False)),
                    UNPARTITIONED_PARTITION_SPEC, UNSORTED_SORT_ORDER, location=str(root),
                    properties={"commit.retry.num-retries": "0"})
                def commit(table, requirements, updates):
                    return CommitTableResponse(metadata=update_table_metadata(table.metadata, updates),
                        metadata_location=str(root / "metadata" / "v2.json"))
                catalog = SimpleNamespace(commit_table=Mock(side_effect=commit), _delete_old_metadata=Mock(),
                                          supports_server_side_planning=lambda: False)
                table = Table(("raw", "events"), metadata, str(root / "metadata" / "v1.json"),
                              PyArrowFileIO(), catalog)
                table.add_files(paths, snapshot_properties={}, check_duplicate_files=True)
                catalog.commit_table.assert_called_once()
                files = {task.file.file_path: task.file for task in table.scan(options={"include_empty_files": "true"}).plan_files()}
                self.assertEqual(set(files), set(paths))
                self.assertEqual(sum(f.record_count for f in files.values()), sum(map(len, rowsets)))
                summary = table.current_snapshot().summary.additional_properties
                self.assertEqual(summary["total-data-files"], "3")
                self.assertEqual(summary["added-data-files"], "3")
                self.assertEqual(summary["total-records"], str(sum(map(len, rowsets))))
                if any(rowsets):
                    self.assertEqual(summary["added-records"], str(sum(map(len, rowsets))))
                else:
                    self.assertNotIn("added-records", summary)
                self.assertEqual(table.current_snapshot().summary.operation.value, "append")

    def test_exact_scalar_footer_and_reject_nested_or_int_widening(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        from datetime import date, datetime
        schema = pa.schema([("key", pa.int32()), ("amount", pa.decimal128(18, 4)),
                            ("day", pa.date32()), ("at", pa.timestamp("us"))])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scalar.parquet"
            pq.write_table(pa.Table.from_pylist([dict(key=3, amount=Decimal("2.0100"),
                            day=date(2026, 1, 1), at=datetime(2026, 1, 1))], schema=schema), path)
            sdk = object.__new__(bulk.SDK)
            sdk.pa, sdk.pq = pa, pq
            _, cols, rows = sdk.footer(path)
            self.assertEqual((cols, rows), (COLS, 1))
            self.assertNotEqual(cols[0], dict(name="key", type="bigint"))
            pq.write_table(pa.Table.from_pylist([], schema=schema), path)
            self.assertEqual(sdk.footer(path)[2], 0)
            with self.assertRaises(ValueError):
                bulk._arrow_columns(pa.schema([("nested", pa.list_(pa.int32()))]), pa)
            with self.assertRaises(ValueError):
                bulk._arrow_columns(pa.schema([("ts", pa.timestamp("ns"))]), pa)


if __name__ == "__main__":
    unittest.main()
