"""Catalog drafts against isolated SQLite authority and synthetic REST/provider I/O."""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import replace
from email.message import Message
from http.client import HTTPResponse
import importlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).parents[1] / "tools/local_experiment"))
with tempfile.TemporaryDirectory() as _bootstrap:
    with patch.dict(os.environ, {"SHIROKUMA_STATE_DIR": _bootstrap}):
        import test_database_ai_plan_views as fixtures
        import db_ai as ai
        import db_ai_catalog as catalog
        import db_ai_sql_proposal as b1
        import control_store as store
        import data_management as dm
        import database_inventory as inv
        import ai_routes
        import workbench_server as wb
        import warehouse_resident as wr
        import runtime_profile
        import request_auth as auth
        from query_context import QueryError

_COMPLETE = ai._complete
PUBLIC = {"id", "kind", "state", "actor", "context", "input", "model", "metadata", "proposal",
          "error_code", "created_at", "updated_at", "cancel_requested", "inference_state"}


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ViewPlanTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f = self.fixture.f
        self.principal, self.context = self.f.principal, self.f.context
        self.relation = self.ref()
        self.f.catalog_calls.clear()
        self.f.complete.return_value = self.output()
        self.owners = []
        self.addCleanup(lambda: [o.close() for o in self.owners])
        for target in ("db_ai._capture", "db_ai_sql_proposal._capture", "database_inventory._item",
                       "database_inventory.resolve_object", "database_inventory.object_detail",
                       "data_management.observe_object",
                       "query_service.submit_explain", "query_service._transport", "trino_auth.prepare_backend_v2"):
            self.patch(target, side_effect=AssertionError(target))

    def patch(self, target, **kw):
        p = patch(target, **kw)
        value = p.start()
        self.addCleanup(p.stop)
        return value

    def ref(self, table="orders", kind="TABLE", context=None):
        c = context or self.context
        metadata = self.f.objects[(c.sql_catalog, c.sql_schema, table, kind)]
        with store.connect() as db:
            # Fixture registration is explicit; product operations must never observe/register.
            existing = db.execute("SELECT id FROM object_bindings WHERE schema_id=? AND physical_name=? AND current=1",
                                  (c.schema_id, table)).fetchone()
            obj = dm.get_object(db, existing[0]) if existing else dm.observe_object(
                db, c.schema_id, table, kind, metadata["table-uuid" if kind == "TABLE" else "view-uuid"], table)
        return dict(database_id=c.database_id, schema_id=c.schema_id, object_id=obj.id,
                    incarnation=obj.incarnation, binding_revision=c.binding_revision, generation_id=c.generation_id)

    @staticmethod
    def output(**kw):
        return ai._json(dict(summary="Tentative summary", description="Tentative description", columns=[]) | kw)

    def accept(self, key="key", **kw):
        with patch.object(catalog.threading, "Thread") as worker:
            record = catalog.submit(kw.pop("principal", self.principal), kw.pop("context", self.context),
                key=key, relation=kw.pop("relation", self.relation), language=kw.pop("language", "en"), **kw)
            if worker.called:
                self.args = worker.call_args.kwargs["args"]
                self.owners.append(self.args[-1])
        return record

    def finish(self):
        catalog._run(*self.args)
        return json.loads(ai._row(self.args[2])["record"])

    def error(self, code, fn, status=None):
        with self.assertRaises(QueryError) as raised:
            fn()
        if code is not None:
            self.assertEqual(raised.exception.code, code)
        if status is not None:
            self.assertEqual(raised.exception.status, status)
        return raised.exception

    def metadata(self, table="orders", kind="TABLE"):
        return self.f.objects[(self.context.sql_catalog, self.context.sql_schema, table, kind)]

    def test_public_exact_record_and_provider_projection(self):
        record = self.accept()
        self.assertEqual(set(record), PUBLIC)
        self.assertEqual(record["input"], dict(relation=self.relation, language="en"))
        self.assertEqual(set(record["metadata"]), {"relation", "captured_at"})
        self.assertIsNone(record["proposal"])
        def complete(config, sql, relations, language, **extra):
            self.assertIsNone(sql)
            self.assertEqual(extra, {"catalog_proposal": True})
            self.assertEqual(relations, [record["metadata"]["relation"]])
            self.assertNotIn("SECRET", ai._json(relations))
            with store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
            return self.output()
        self.f.complete.side_effect = complete
        done = self.finish()
        self.assertEqual(set(done), PUBLIC)
        self.assertEqual(done["state"], "SUCCEEDED", done)
        self.assertEqual(done["proposal"], json.loads(self.output()))
        self.assertTrue(all(not m.called for m in self.f.forbidden))

    def test_two_databases_three_roles_tables_and_views(self):
        for database in self.f.databases:
            for role in ("bi_viewer", "data_engineer", "admin"):
                c = self.f.ctx(role=role, schema="marts" if role == "bi_viewer" else "raw", database=database)
                for name, kind in (("orders", "TABLE"), ("order_view", "VIEW")):
                    # Use the original registry constructor only in fixture preparation.
                    with store.connect() as db:
                        row = db.execute("SELECT id FROM object_bindings WHERE schema_id=? AND physical_name=? AND current=1",
                                         (c.schema_id, name)).fetchone()
                        if row:
                            obj = dm.get_object(db, row[0])
                        else:
                            constructor = self._constructor
                            m = self.f.objects[(c.sql_catalog, c.sql_schema, name, kind)]
                            obj = constructor(db, c.schema_id, name, kind, m["table-uuid" if kind == "TABLE" else "view-uuid"], name)
                    ref = dict(database_id=c.database_id, schema_id=c.schema_id, object_id=obj.id,
                               incarnation=obj.incarnation, binding_revision=c.binding_revision, generation_id=c.generation_id)
                    self.accept(key=database.id+role+kind, context=c, relation=ref)
                    self.assertEqual(self.finish()["state"], "SUCCEEDED")

    def test_full9_and_ref6_invalid_before_rest(self):
        for field, value in self.context.public_metadata().items():
            bad = replace(self.context, **{field: value+1 if type(value) is int else "wrong"})
            self.error(None, lambda: self.accept(context=bad))
        for field, value in self.relation.items():
            bad = dict(self.relation, **{field: value+1 if type(value) is int else "wrong"})
            self.error(None, lambda: self.accept(relation=bad))
            bad = {k:v for k,v in self.relation.items() if k != field}
            self.error("AI_INPUT_INVALID", lambda: self.accept(relation=bad))
            bad = dict(self.relation, **{field: True})
            self.error("AI_INPUT_INVALID", lambda: self.accept(relation=bad))
        for relation in ([], None, dict(self.relation, extra=1)):
            self.error("AI_INPUT_INVALID", lambda: self.accept(relation=relation))
        for field in ("database_id", "schema_id", "object_id", "generation_id"):
            for text in ("\ud800", "a\x00b"):
                self.error("AI_INPUT_INVALID", lambda: self.accept(relation=dict(self.relation, **{field:text})))
        self.assertEqual(self.f.catalog_calls, [])
        self.f.complete.assert_not_called()

    def test_crossdb_and_cross_primary_schema_before_rest(self):
        for c in (self.f.ctx(database=self.f.databases[1]), self.f.ctx(schema="sandbox")):
            with store.connect() as db:
                m = self.f.objects[(c.sql_catalog,c.sql_schema,"orders","TABLE")]
                obj = self._constructor(db,c.schema_id,"orders","TABLE",m["table-uuid"],"orders")
            ref = dict(self.relation, database_id=c.database_id, schema_id=c.schema_id, object_id=obj.id,
                       generation_id=c.generation_id)
            self.error("AI_METADATA_CHANGED",lambda:self.accept(relation=ref))
        self.assertEqual(self.f.catalog_calls, [])

    def test_current_identity_revoked_before_rest(self):
        with store.connect() as db:
            db.execute("UPDATE logical_backend_identities SET state=\x27REVOKED\x27 WHERE principal_id=\x27alice\x27")
        self.error("DB_ACCESS_REVOKED", self.accept)
        self.assertEqual(self.f.catalog_calls, [])

    def test_backend_identity_only_drift_before_capture_and_final_writer(self):
        with store.connect() as db:
            db.execute("UPDATE object_bindings SET backend_identity=? WHERE id=?", ("replacement", self.relation["object_id"]))
        self.error("AI_METADATA_CHANGED", self.accept)
        with store.connect() as db:
            db.execute("UPDATE object_bindings SET backend_identity=? WHERE id=?", (self.metadata()["table-uuid"], self.relation["object_id"]))
        self.accept()
        refresh = catalog._refresh
        calls = 0
        def drift(*a):
            nonlocal calls
            refresh(*a)
            calls += 1
            if calls == 2:
                with store.connect() as db:
                    db.execute("UPDATE object_bindings SET backend_identity=? WHERE id=?", ("same-incarnation",self.relation["object_id"]))
        with patch.object(catalog,"_refresh",side_effect=drift):
            done = self.finish()
        self.assertEqual((done["state"],done["error_code"],done["proposal"]), ("FAILED","AI_METADATA_CHANGED",None))

    def test_postrest_identity_and_authority_checked(self):
        original = self.fixture.catalog
        @contextmanager
        def changed(*args):
            with original(*args) as api:
                def call(path):
                    result = api(path)
                    with store.connect() as db:
                        db.execute("UPDATE object_bindings SET backend_identity=? WHERE id=?", ("drift",self.relation["object_id"]))
                    return result
                yield call
        with patch.object(inv,"catalog_api",changed):
            self.error("AI_METADATA_CHANGED",self.accept)
        self.f.complete.assert_not_called()

    def test_empty_table_append_and_properties_are_valid(self):
        m = self.metadata()
        m["schemas"][0]["fields"] = []
        self.accept()
        def append(*a,**kw):
            m["current-snapshot-id"] += 1
            m["properties"] = {"comment":"PRIVATE SENTINEL"}
            return self.output()
        self.f.complete.side_effect = append
        done = self.finish()
        self.assertEqual(done["state"],"SUCCEEDED")
        self.assertEqual(done["metadata"]["relation"]["columns"],[])
        self.assertNotIn("PRIVATE",ai._row(done["id"])["pins"])

    def test_view_uses_version_schema_not_table_schema_and_ignores_definition(self):
        c = self.context
        m = self.metadata("order_view","VIEW")
        m["current-schema-id"] = 999
        m["schemas"].append({"schema-id":2,"fields":[dict(name="new",type="string")]})
        m["versions"][0]["schema-id"] = 2
        m["versions"][0].pop("representations",None)
        with store.connect() as db:
            obj = self._constructor(db,c.schema_id,"order_view","VIEW",m["view-uuid"],"order_view")
        ref = dict(self.relation, object_id=obj.id, incarnation=obj.incarnation)
        record = self.accept(relation=ref)
        self.assertEqual(record["metadata"]["relation"]["columns"], [dict(name="new",type="string")])
        def change(*a,**kw):
            m["current-version-id"] = 2
            m["versions"].append({"version-id":2,"schema-id":2,"representations":[{"sql":"PRIVATE UNSUPPORTED"}]})
            return self.output()
        self.f.complete.side_effect = change
        done = self.finish()
        self.assertEqual(done["state"],"SUCCEEDED",done)
        self.assertNotIn("PRIVATE",ai._row(done["id"])["pins"])
        self.assertEqual(len(self.f.catalog_calls),3)

    def test_normalized_nested_types_no_private_fields(self):
        self.metadata()["schemas"][0]["fields"] = [dict(name="構造",doc="PRIVATE",type={"type":"struct","doc":"PRIVATE",
            "fields":[dict(name="items",doc="PRIVATE",type={"type":"list","element":{"type":"map","key":"string","value":"long","doc":"PRIVATE"}})]})]
        r = self.accept()
        projected = r["metadata"]["relation"]["columns"][0]["type"]
        self.assertEqual(json.loads(projected),dict(type="struct", fields=[dict(name="items",type=dict(type="list",element=dict(type="map",key="string",value="long")))]))
        self.assertNotIn("PRIVATE",ai._row(r["id"])["pins"])
        self.assertEqual(self.finish()["state"],"SUCCEEDED")

    def test_invalid_metadata_shapes_and_catalog_errors_fixed(self):
        original = deepcopy(self.metadata())
        bad = [{}, dict(original, **{"table-uuid":None}), dict(original, **{"schemas":None}),
               dict(original, **{"current-schema-id":True}), dict(original, **{"schemas":[{"schema-id":1,"fields":None}]}),
               dict(original, **{"schemas":[{"schema-id":1,"fields":[{"name":"x"}]}]}),
               dict(original, **{"schemas":[{"schema-id":1,"fields":[{"name":"x","type":"long"}]*2}]}),
               dict(original, **{"schemas":[{"schema-id":1,"fields":[{"name":"x","type":{"type":"struct","fields":[{"name":"","type":"string"}]}}]}]})]
        for value in bad:
            self.f.objects[(self.context.sql_catalog,"raw","orders","TABLE")] = value
            self.error("AI_METADATA_UNAVAILABLE", self.accept, 502)
        with patch.object(inv,"_metadata",side_effect=RuntimeError("PRIVATE EXCEPTION")):
            error = self.error("AI_METADATA_UNAVAILABLE",self.accept,502)
            self.assertNotIn("PRIVATE",str(error))
        self.f.complete.assert_not_called()

    def test_metadata_exact_49152_utf8_bound(self):
        fields = self.metadata()["schemas"][0]["fields"]
        fields[:] = [dict(name="x",type="string")]
        public = dict(schema="raw",table="orders",kind="TABLE",columns=[dict(name="x",type="string")])
        size = len(ai._json(public).encode())
        fields[0]["name"] = "x" * (49152-size+1)
        r = self.accept()
        self.assertEqual(len(ai._json(r["metadata"]["relation"]).encode()),49152)
        fields[0]["name"] += "x"
        self.error("AI_METADATA_UNAVAILABLE",lambda:self.accept(key="over"),502)

    def test_preprovider_and_postprovider_structural_drift(self):
        self.accept()
        fields = self.metadata()["schemas"][0]["fields"]
        fields.append(dict(name="new",type="long"))
        done = self.finish()
        self.assertEqual(done["error_code"],"AI_METADATA_CHANGED")
        self.f.complete.assert_not_called()
        self.accept(key="post")
        def change(*a,**kw):
            fields[0]["type"] = "string"
            return self.output()
        self.f.complete.side_effect = change
        done = self.finish()
        self.assertEqual((done["state"],done["error_code"],done["proposal"]),("FAILED","AI_METADATA_CHANGED",None))

    def test_strict_closed_json_and_utf8_rejections_never_persist_excerpt(self):
        valid = self.output()
        candidates = ["null", "[]", "Here: "+valid, valid+" prose", "```json\n"+valid+"\n```", valid[:-1],
            valid[:-1]+",\"extra\":0}", "{\"summary\":\"x\",\"summary\":\"y\",\"description\":\"x\",\"columns\":[]}",
            self.output(summary=""),self.output(summary="  "),self.output(description=None),self.output(columns={}),
            self.output(summary="界"*1366),self.output(description="x"*4097),self.output(summary="\x00"),
            json.dumps(dict(summary="\ud800",description="x",columns=[])),
            self.output(columns=[dict(name="unknown",description="PRIVATE SENTINEL")]),
            self.output(columns=[dict(name="id",description="x")]*2),
            self.output(columns=[dict(name="id",description="x",type="long")]),
            self.output(columns=[dict(name="id",description="")]),
            self.output(columns=[dict(name="id",description="界"*342)]),
            self.output(columns=[dict(name="id",description="\x00")]),
            self.output(columns=[dict(name="id",description="x")]*9),
            "{\"summary\":\"x\",\"description\":\"x\",\"columns\":[{\"name\":\"id\",\"name\":\"amount\",\"description\":\"x\"}]}"]
        for i,value in enumerate(candidates):
            with self.subTest(i=i):
                self.accept(key=str(i))
                self.f.complete.return_value = value
                done = self.finish()
                self.assertEqual((done["state"],done["error_code"],done["proposal"]),("FAILED","AI_OUTPUT_INVALID",None))
                self.assertNotIn("PRIVATE SENTINEL",ai._row(done["id"])["record"])

    def test_partial_suggestions_canonical_order_zero_and_exact_limits(self):
        self.accept()
        columns = [dict(name="amount",description="界"*341+"x"),dict(name="id",description="id")]
        self.f.complete.return_value = self.output(summary="界"*1365+"x",description="d"*4096,columns=columns)
        done = self.finish()
        self.assertEqual(done["state"],"SUCCEEDED")
        self.assertEqual([c["name"] for c in done["proposal"]["columns"]],["id","amount"])
        self.accept(key="zero")
        self.f.complete.return_value = self.output()
        self.assertEqual(self.finish()["proposal"]["columns"],[])

    def test_total_output_bound_16384(self):
        names = ["n"+str(i) for i in range(8)]
        relation = dict(columns=[dict(name=n,type="string") for n in names])
        value = dict(summary="s"*4096,description="d"*4096,columns=[dict(name=n,description="x"*1024) for n in names])
        self.error("AI_OUTPUT_INVALID",lambda:catalog._output(ai._json(value),relation),502)
        value["description"] = "d"*(4096-(len(ai._json(value).encode())-16384))
        self.assertEqual(len(ai._json(value).encode()),16384)
        self.assertEqual(catalog._output(ai._json(value),relation),value)

    def test_final_writer_grant_revocation_discards_output(self):
        self.accept()
        refresh = catalog._refresh
        calls = 0
        def revoke(*args):
            nonlocal calls
            refresh(*args)
            calls += 1
            if calls == 2:
                with store.connect() as db:
                    db.execute("UPDATE grants SET revision=revision+1 WHERE principal_id=\x27alice\x27")
        with patch.object(catalog,"_refresh",side_effect=revoke):
            done = self.finish()
        self.assertEqual(done["state"],"FAILED")
        self.assertIsNone(done["proposal"])
        self.assertIsNotNone(done["error_code"])

    def test_cancel_queued_and_during_provider(self):
        r = self.accept()
        catalog.cancel(self.principal,self.context,r["id"])
        self.assertEqual(self.finish()["state"],"CANCELED")
        self.f.complete.assert_not_called()
        r = self.accept(key="late")
        def late(*a,**kw):
            catalog.cancel(self.principal,self.context,r["id"])
            return self.output()
        self.f.complete.side_effect = late
        done = self.finish()
        self.assertEqual((done["state"],done["proposal"]),("CANCELED",None))

    def test_stale_running_ui_cancel_post_after_completion_preserves_entire_row(self):
        record = self.accept()
        def complete(*args, **kwargs):
            self.assertEqual(json.loads(ai._row(record['id'])['record'])['state'], 'RUNNING')
            return self.output(columns=[dict(name='id', description='Tentative identifier')])
        self.f.complete.side_effect = complete
        done = self.finish()
        self.assertEqual(done['state'], 'SUCCEEDED')
        before = tuple(value.encode('utf-8') for value in ai._row(record['id']))
        path = '/api/v1/bears/catalog-proposals/' + record['id'] + '/cancel'
        headers = Message()
        headers['Content-Type'] = 'application/json'
        replies = []
        handler = SimpleNamespace(path=path, headers=headers,
            database_body=lambda: dict(context=self.context.public_metadata()),
            respond=lambda result, status: replies.append((result, status)))
        with patch.object(catalog, '_save', side_effect=AssertionError('terminal write')), \
                patch.object(inv, 'catalog_api', side_effect=AssertionError('catalog')):
            self.assertTrue(ai_routes.dispatch(handler, 'POST', path, {}, self.principal,
                self.context.selected_role))
        result, status = replies[0]
        self.assertEqual(status, 200)
        self.assertEqual(result['explanation'], done)
        self.assertFalse(result['explanation']['cancel_requested'])
        self.assertIsNotNone(result['explanation']['proposal'])
        self.assertEqual(tuple(value.encode('utf-8') for value in ai._row(record['id'])), before)
        self.assertEqual(self.f.complete.call_count, 1)

    def test_failed_canceled_unknown_cancel_is_noop_and_replay_never_reinfers(self):
        for state in ('FAILED', 'CANCELED', 'UNKNOWN'):
            with self.subTest(state=state):
                record = self.accept(key=state)
                if state == 'CANCELED':
                    catalog.cancel(self.principal, self.context, record['id'])
                else:
                    code = 'AI_OUTPUT_INVALID' if state == 'FAILED' else 'AI_PROVIDER_TIMEOUT'
                    self.f.complete.side_effect = QueryError(code, 502)
                done = self.finish()
                self.assertEqual(done['state'], state)
                before = tuple(value.encode('utf-8') for value in ai._row(record['id']))
                calls = self.f.complete.call_count
                with patch.object(catalog, '_save', side_effect=AssertionError('terminal write')), \
                        patch.object(inv, 'catalog_api', side_effect=AssertionError('catalog')):
                    self.assertEqual(catalog.cancel(self.principal, self.context, record['id']), done)
                    self.assertEqual(self.accept(key=state), done)
                    self.assertEqual(self.finish(), done)
                self.assertEqual(tuple(value.encode('utf-8') for value in ai._row(record['id'])), before)
                self.assertEqual(self.f.complete.call_count, calls)

    def test_replay_crosskind_conflict_and_no_recapture(self):
        r = self.accept()
        self.finish()
        self.f.catalog_calls.clear()
        self.assertEqual(self.accept()["id"],r["id"])
        self.error("IDEMPOTENCY_CONFLICT",lambda:self.accept(language="ja"))
        with store.connect() as db:
            saved = json.loads(ai._row(r["id"])["record"])
            saved["kind"] = "sql_proposal"
            db.execute("UPDATE ai_explanations SET record=? WHERE id=?",(ai._json(saved),r["id"]))
        self.error("IDEMPOTENCY_CONFLICT",self.accept)
        self.assertEqual(self.f.catalog_calls,[])
        self.assertEqual(self.f.complete.call_count,1)

    def test_owner_loss_reload_unknown_never_reinfers(self):
        r = self.accept()
        self.owners[-1].close()
        importlib.reload(catalog)
        done = catalog.get(self.principal,self.context,r["id"])
        self.assertEqual((done["state"],done["error_code"]),("UNKNOWN","AI_OWNER_LOST"))
        self.assertEqual(self.accept()["id"],r["id"])
        self.assertEqual(self.finish()["state"],"UNKNOWN")
        self.f.complete.assert_not_called()

    def test_provider_fixed_failures_and_unknown(self):
        for i,(code,state) in enumerate((("AI_PROVIDER_TIMEOUT","UNKNOWN"),("AI_PROVIDER_OUTCOME_UNKNOWN","UNKNOWN"),
                                         ("AI_PROVIDER_UNAVAILABLE","FAILED"))):
            self.accept(key=str(i))
            self.f.complete.side_effect = QueryError(code,502)
            done = self.finish()
            self.assertEqual((done["state"],done["error_code"],done["proposal"]),(state,code,None))
        self.accept(key="exception")
        self.f.complete.side_effect = RuntimeError("PRIVATE")
        done = self.finish()
        self.assertEqual(done["error_code"],"AI_PROVIDER_OUTCOME_UNKNOWN")
        self.assertNotIn("PRIVATE",ai._row(done["id"])["record"])

    def test_history_frozen_after_source_replaced_no_catalog_current_acl(self):
        r = self.accept()
        self.finish()
        self.metadata()["schemas"][0]["fields"].append(dict(name="later",type="string"))
        with store.connect() as db:
            db.execute("UPDATE object_bindings SET backend_identity=\x27replaced\x27 WHERE id=?",(self.relation["object_id"],))
        self.f.catalog_calls.clear()
        self.assertEqual(catalog.history(self.principal,self.context)[0]["metadata"],r["metadata"])
        self.error("AI_EXPLANATION_NOT_FOUND",lambda:catalog.get(self.f.bob,self.context,r["id"]))
        for field,value in self.context.public_metadata().items():
            c = replace(self.context,**{field:value+1 if type(value) is int else "other"})
            self.error("AI_EXPLANATION_NOT_FOUND",lambda:catalog.get(self.principal,c,r["id"]))
            self.error("AI_EXPLANATION_NOT_FOUND",lambda:catalog.cancel(self.principal,c,r["id"]))
        with store.connect() as db:
            db.execute("UPDATE schemas SET purpose=\x27restricted\x27 WHERE id=?",(self.context.schema_id,))
        self.error("SQL_PERMISSION_DENIED",lambda:catalog.get(self.principal,self.context,r["id"]))
        self.error("SQL_PERMISSION_DENIED",lambda:catalog.history(self.principal,self.context))
        self.error("SQL_PERMISSION_DENIED",lambda:catalog.cancel(self.principal,self.context,r["id"]))
        self.assertEqual(self.f.catalog_calls,[])

    def test_admission_after_config_rechecks_before_insert(self):
        original = ai._config
        def changed():
            config = original()
            with store.connect() as db:
                db.execute("UPDATE object_bindings SET backend_identity=\x27drift\x27 WHERE id=?",(self.relation["object_id"],))
            return config
        with patch.object(ai,"_config",side_effect=changed):
            self.error("AI_METADATA_CHANGED",self.accept)
        with store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM ai_explanations").fetchone()[0],0)

    def test_catalog_and_provider_outside_writer_transaction(self):
        original = self.fixture.catalog
        @contextmanager
        def unlocked(*args):
            with store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
            with original(*args) as api:
                yield api
        with patch.object(inv,"catalog_api",unlocked):
            self.accept()
            self.assertEqual(self.finish()["state"],"SUCCEEDED")

    def test_concurrent_same_key_one_owner_and_provider(self):
        original, real_thread = catalog._capture, threading.Thread
        barrier = threading.Barrier(2)
        records, errors, workers = [], [], []
        def capture(*args):
            result = original(*args)
            barrier.wait(timeout=5)
            return result
        class Queued:
            def __init__(self,**kw): workers.append(kw["args"])
            def start(self): pass
        def submit():
            try:
                records.append(catalog.submit(self.principal,self.context,key="race",relation=self.relation,language="en"))
            except Exception as error:
                errors.append(error)
        with patch.object(catalog,"_capture",side_effect=capture), patch.object(catalog.threading,"Thread",Queued):
            threads = [real_thread(target=submit) for _ in range(2)]
            for t in threads: t.start()
            for t in threads: t.join(10)
        self.owners.extend(args[-1] for args in workers)
        self.assertEqual(errors,[])
        self.assertEqual(len(records),2)
        self.assertEqual(records[0]["id"],records[1]["id"])
        self.assertEqual(len(workers),1)
        catalog._run(*workers[0])
        self.assertEqual(self.f.complete.call_count,1)

    def test_uuid_replacement_and_view_column_change_rejected(self):
        self.accept()
        self.metadata()['table-uuid'] = 'different-uuid'
        done = self.finish()
        self.assertEqual((done['error_code'],done['proposal']),('AI_METADATA_CHANGED',None))
        self.f.complete.assert_not_called()
        m = self.metadata('order_view','VIEW')
        with store.connect() as db:
            obj = self._constructor(db,self.context.schema_id,'order_view','VIEW',m['view-uuid'],'order_view')
        ref = dict(self.relation,object_id=obj.id,incarnation=obj.incarnation)
        self.accept(key='view',relation=ref)
        def change(*args,**kwargs):
            m['current-version-id'] = 2
            m['schemas'].append({'schema-id':2,'fields':[dict(name='different',type='string')]})
            m['versions'].append({'version-id':2,'schema-id':2})
            return self.output()
        self.f.complete.side_effect = change
        done = self.finish()
        self.assertEqual((done['error_code'],done['proposal']),('AI_METADATA_CHANGED',None))

    def test_authority_revoked_during_rest(self):
        original = self.fixture.catalog
        @contextmanager
        def revoked(*args):
            with original(*args) as api:
                def read(path):
                    result = api(path)
                    with store.connect() as db:
                        db.execute("UPDATE logical_backend_identities SET state='REVOKED' WHERE principal_id='alice'")
                    return result
                yield read
        with patch.object(inv,'catalog_api',revoked):
            self.error('DB_ACCESS_REVOKED',self.accept)
        self.f.complete.assert_not_called()

    def test_authority_revoked_before_inference(self):
        self.accept()
        with store.connect() as db:
            db.execute("UPDATE logical_backend_identities SET state='REVOKED' WHERE principal_id='alice'")
        done = self.finish()
        self.assertEqual((done['error_code'],done['proposal']),('DB_ACCESS_REVOKED',None))
        self.f.complete.assert_not_called()

    def test_cancellation_after_catalog_and_final_refresh(self):
        original = catalog._refresh
        for phase in (1,2):
            record = self.accept(key=str(phase))
            calls = 0
            def cancel(*args):
                nonlocal calls
                original(*args)
                calls += 1
                if calls == phase:
                    catalog.cancel(self.principal,self.context,record['id'])
            self.f.complete.reset_mock()
            with patch.object(catalog,'_refresh',side_effect=cancel):
                done = self.finish()
            self.assertEqual((done['state'],done['proposal']),('CANCELED',None))
            self.assertEqual(self.f.complete.call_count,phase-1)

    def test_arbitrary_unicode_and_literal_escape_names(self):
        # A literal backslash-u sequence is valid metadata; it is not a NUL.
        name = '日本語 " \\u0000'
        self.metadata()['schemas'][0]['fields'] = [dict(name=name,type='string')]
        record = self.accept()
        self.f.complete.return_value = self.output(columns=[dict(name=name,description='推測です')])
        done = self.finish()
        self.assertEqual(done['state'],'SUCCEEDED')
        self.assertEqual(record['metadata']['relation']['columns'][0]['name'],name)

    def test_view_metadata_requires_current_version_schema_only(self):
        m = self.metadata('order_view','VIEW')
        with store.connect() as db:
            obj = self._constructor(db,self.context.schema_id,'order_view','VIEW',m['view-uuid'],'order_view')
        ref = dict(self.relation,object_id=obj.id,incarnation=obj.incarnation)
        original = deepcopy(m)
        for update in ({'current-version-id':None}, {'current-version-id':True}, {'versions':[]},
                       {'versions':[{'version-id':1,'schema-id':99}]}, {'schemas':[]},
                       {'versions':[{'version-id':1,'schema-id':1}]*2}):
            self.f.objects[(self.context.sql_catalog,'raw','order_view','VIEW')] = dict(original,**update)
            self.error('AI_METADATA_UNAVAILABLE',lambda:self.accept(relation=ref),502)
        self.f.complete.assert_not_called()


# Preserve the real constructor for fixture setup, never the operation under test.
CatalogTests._constructor = staticmethod(dm.observe_object)


class CatalogWireTests(unittest.TestCase):
    setUp = CatalogTests.setUp
    patch = CatalogTests.patch
    ref = CatalogTests.ref
    output = staticmethod(CatalogTests.output)
    error = CatalogTests.error

    def test_provider_exact_descriptor_flags_mutual_exclusion_and_default_compatibility(self):
        bodies = []
        class Response:
            status = 200
            def __init__(self):
                self.payload = json.dumps(dict(choices=[dict(finish_reason="stop",message=dict(content="result"))])).encode()
            def read1(self,count):
                part,self.payload = self.payload[:count],self.payload[count:]
                return part
        class Connection:
            sock = None
            def __init__(self,*a,**kw): pass
            def connect(self): pass
            def request(self,*a,**kw): bodies.append(json.loads(kw["body"]))
            def getresponse(self): return Response()
            def close(self): pass
        config = dict(host="127.0.0.1",port=1,timeout=2,max_tokens=1024,model="local")
        relation = dict(schema="raw",table="untrusted 日本語",kind="TABLE",columns=[dict(name="id",type="long")])
        with patch.object(ai.http.client,"HTTPConnection",Connection):
            for language in ("ja","en"):
                self.assertEqual(_COMPLETE(config,None,[relation],language,catalog_proposal=True),"result")
                body = bodies[-1]
                self.assertEqual(json.loads(body["messages"][1]["content"]),relation)
                self.assertEqual(set(body),{"model","max_tokens","temperature","enable_thinking","messages"})
                self.assertEqual((body["max_tokens"],body["temperature"],body["enable_thinking"]),(1024,0,False))
                prompt = body["messages"][0]["content"]
                for text in (language,"untrusted data","tentative","summary, description, columns","zero to eight","No tools, SQL"):
                    self.assertIn(text,prompt)
            for options in ({},{"plan":{"fragments":[]}},{"plan":{},"view_dependencies":[]},{"proposal_request":"request"}):
                _COMPLETE(config,"SELECT ?",[relation],"en",**options)
                before = bodies[-1]
                _COMPLETE(config,"SELECT ?",[relation],"en",catalog_proposal=False,**options)
                self.assertEqual(bodies[-1],before)
                self.assertIn("relations",json.loads(before["messages"][1]["content"]))
        with patch.object(ai.http.client,"HTTPConnection",side_effect=AssertionError("network")):
            for options in ({"plan":{}},{"view_dependencies":[]},{"proposal_request":"x"}):
                self.error("AI_INPUT_INVALID",lambda:_COMPLETE(config,None,[relation],"en",catalog_proposal=True,**options))
            self.error("AI_INPUT_INVALID",lambda:_COMPLETE(config,"SELECT 1",[relation],"en",catalog_proposal=True))
            for relations in ([],[relation,relation],[dict(relation,private="secret")]):
                self.error("AI_INPUT_INVALID",lambda:_COMPLETE(config,None,relations,"en",catalog_proposal=True))
            oversized = dict(relation,table="x"*131072)
            self.error("AI_INPUT_INVALID",lambda:_COMPLETE(config,None,[oversized],"en",catalog_proposal=True))

    def test_both_real_http_families_strict_body_and_read_cancel_framing(self):
        handlers = [wb.WorkbenchHandler]
        class Capture:
            def __init__(self,address,handler): handlers.append(handler)
            def __enter__(self): return self
            def __exit__(self,*a): pass
            def handle_request(self): pass
        with ExitStack() as stack:
            stack.enter_context(patch("warehouse_resident._profile_active",return_value=True))
            stack.enter_context(patch("logical_creations.enabled",return_value=False))
            stack.enter_context(patch("http.server.ThreadingHTTPServer",Capture))
            stack.enter_context(patch("database_creation_bridge.configure"))
            stack.enter_context(patch.object(wr,"warehouse",return_value="LOCAL"))
            wr.serve_endpoint(once=True)
        class Connection:
            def __init__(self,data): self.incoming,self.outgoing = io.BytesIO(data),io.BytesIO()
            def makefile(self,*a): return self.incoming
            def sendall(self,data): self.outgoing.write(data)
        base = "/api/v1/bears/catalog-proposals"
        ident = "00000000-0000-0000-0000-000000000001"
        body = json.dumps(dict(context=self.context.public_metadata(),relation=self.relation,language="en"))
        ctx = json.dumps(dict(context=self.context.public_metadata()))
        query = "?"+urlencode(dict(context=json.dumps(self.context.public_metadata())))
        key = "Idempotency-Key: k\r\n"
        cases = [("POST",base,body,key,202,"submit"),
            ("POST",base,body,"",400,None),
            ("POST",base,body,key+key,400,None),
            ("POST",base,body,key+"Transfer-Encoding: chunked\r\n",400,None),
            ("POST",base,body,key+"Origin: http://evil.invalid\r\n",403,None),
            ("POST",base+"?extra=1",body,key,400,None),
            ("POST",base,body[:-1]+",\"language\":\"ja\"}",key,400,None),
            ("POST",base,body,key+"Content-Length: "+str(len(body.encode()))+"\r\n",400,None),
            ("GET",base+query,"","",200,"history"),
            ("GET",base+"/"+ident+query,"","",200,"get"),
            ("GET",base+query,"x","",400,None),
            ("GET",base+query,"","Transfer-Encoding: \r\n",400,None),
            ("GET",base+query+"&extra=1","","",400,None),
            ("GET",base+query+"&"+query[1:],"","",400,None),
            ("POST",base+"/"+ident+"/cancel",ctx,"",200,"cancel"),
            ("POST",base+"/"+ident+"/cancel",body,"",400,None)]
        for field,value in (("sql","SELECT 1"),("request","explain"),("refs",[self.relation]),("relations",[self.relation])):
            cases.append(("POST",base,body[:-1]+","+json.dumps(field)+":"+json.dumps(value)+"}",key,400,None))
        for field in self.context.public_metadata():
            bad = dict(self.context.public_metadata());bad.pop(field)
            cases.append(("POST",base,json.dumps(dict(context=bad,relation=self.relation,language="en")),key,400,None))
        for handler in handlers:
            for method,path,data,extra,status,called in cases:
                with self.subTest(surface=handler.__name__,method=method,path=path,status=status):
                    wire = (method+" "+path+" HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n"
                        "X-Shirokuma-Role: data_engineer\r\nContent-Type: application/json\r\nContent-Length: "
                        +str(len(data.encode()))+"\r\n"+extra+"\r\n"+data).encode()
                    conn = Connection(wire)
                    with ExitStack() as stack:
                        stack.enter_context(patch.object(runtime_profile,"active",return_value=True))
                        stack.enter_context(patch.object(auth,"authenticate_principal",return_value=self.principal))
                        mocks = {name:stack.enter_context(patch.object(catalog,name,return_value=[] if name == "history" else {"id":ident}))
                                 for name in ("submit","get","history","cancel")}
                        stack.enter_context(patch.object(handler,"log_message"))
                        handler(conn,("127.0.0.1",1),SimpleNamespace())
                    reply = HTTPResponse(Connection(conn.outgoing.getvalue()));reply.begin()
                    result = json.loads(reply.read())
                    self.assertEqual(reply.status,status,result)
                    for name,mock in mocks.items():
                        self.assertEqual(mock.call_count,int(name == called))
                    if called == "submit":
                        self.assertEqual(mocks[called].call_args.kwargs,dict(key="k",relation=self.relation,language="en"))
                    if called:
                        self.assertEqual(set(result),{"context","explanations" if called == "history" else "explanation"})
        self.assertEqual(self.f.catalog_calls,[])
        self.f.complete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
