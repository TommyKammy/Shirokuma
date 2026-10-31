"""Selected TABLE publication commits every verified native INVOKER VIEW lineage."""
from copy import deepcopy
import unittest
import uuid
from unittest.mock import patch

import test_database_dbt_graph_views as views
import test_database_dbt_graph_publication as publication
import test_db_dbt as native_publication
import control_store
import database_dbt as compiler
import database_dbt_graph as graph
import data_management as registry
import db_dbt as dbt
import query_service as queries
from query_context import QueryError


class ViewPublicationTests(unittest.TestCase):
    clear_live = views.ViewGraphTests.clear_live
    mutate_plan = views.ViewGraphTests.mutate_plan
    record = views.ViewGraphTests.record
    mutate = views.ViewGraphTests.mutate
    execute = views.ViewGraphTests.execute
    transport = views.ViewGraphTests.transport
    save_plan = views.ViewGraphTests.save_plan
    view_metadata = views.ViewGraphTests.view_metadata
    publisher = native_publication.NativePublicationTests.publisher
    preview = native_publication.NativePublicationTests.preview
    accept = native_publication.NativePublicationTests.accept
    finish = native_publication.NativePublicationTests.finish
    code = native_publication.DbtTests.code

    def setUp(self):
        views.ViewGraphTests.setUp(self)
        self.uid = 'model.general_native.final_result'
        self.mutate_plan(lambda p: p['models'].sort(key=lambda m: {'ephemeral': 0, 'view': 1, 'table': 2}[m['materialized']]))
        self.mutate_plan(lambda p: p.update(_plan_digest=dbt._hash_plan(p)))

    def candidate(self):
        self.mutate_plan(lambda p: p['models'].sort(key=lambda m: {'ephemeral': 0, 'view': 1, 'table': 2}[m['materialized']]))
        self.mutate_plan(lambda p: p.update(_plan_digest=dbt._hash_plan(p)))
        ack = views.ViewGraphTests.accept(self, tests=True)
        run = self.execute(ack)
        self.assertEqual(run['state'], 'NATIVE_VERIFIED', run)
        self.assertTrue(run['publication_allowed'])
        self.assertEqual(run['models'][0]['materialized'], 'ephemeral')
        self.assertEqual(run['models'][1]['materialized'], 'view')
        run['native_receipt'] = next(m['native_receipt'] for m in run['models'] if m['unique_id'] == self.uid)
        return run

    def test_selected_table_public_views_frozen_lineage_credentials_and_collection(self):
        run = self.candidate()
        original = self.record(run['id'])
        view = next(m for m in original['models'] if m['materialized'] == 'view')
        with patch.object(compiler, '_compiled_project', side_effect=AssertionError('compile')), \
                patch.object(graph, '_run_subprocess', side_effect=AssertionError('SDK')), \
                patch.object(queries, '_transport', side_effect=AssertionError('SQL')):
            preview = self.preview(run)
        self.assertNotIn('views', run)
        summary = dict(node_id=view['unique_id'], candidate=dbt._view_public(view['_measurement']),
            definition_digest=view['_measurement']['_canonical_definition_digest'])
        self.assertEqual(preview['views'], [summary])
        self.b.credential = 'Basic view-publisher'
        ack = self.accept(run, preview['expected_target'])
        self.assertEqual(ack['views'], [summary])
        self.mutate(ack['id'], lambda r: r.update(views=[dict(untrusted='stored projection')]))
        self.assertEqual(dbt.get_publication(*self.publisher(), ack['id'])['views'], [summary])
        frozen = self.record(ack['id'])['_native_intent']
        self.assertEqual(frozen['views'], [dict(node_id=view['unique_id'], receipt=view['native_receipt'],
            dependency_snapshots=view['dependency_snapshots'])])
        self.assertNotIn('snapshot', frozen['views'][0]['receipt']['candidate'])
        before = len(self.w.posts)
        published = self.finish(ack)
        self.assertEqual(published['state'], 'PUBLISHED', published)
        self.assertEqual(published['views'], [summary])
        self.assertEqual(len(self.w.posts), before + 2)
        self.assertTrue(all(headers['Authorization'] == 'Basic view-publisher' for _, headers in self.w.posts[before:]))
        with control_store.connect() as db:
            source = db.execute('SELECT * FROM dataops_operations WHERE id=?', (frozen['build_operation_id'],)).fetchone()
            children = list(db.execute('SELECT o.* FROM dataops_operations o JOIN dbt_run_operations l ON l.operation_id=o.id WHERE l.run_id=?', (ack['id'],)))
            self.assertTrue(all(c['credential_snapshot_ref'] != source['credential_snapshot_ref'] for c in children))
            self.assertEqual(set(__import__('json').loads(source['context'])), set(run['context']))
        with patch.object(queries, '_transport', side_effect=AssertionError('SQL')):
            self.assertEqual(dbt.list_publications(*self.publisher())[0]['views'], [summary])
        self.assertEqual(self.accept(run, preview['expected_target'])['id'], ack['id'])

    def test_missing_extra_duplicate_and_mutated_frozen_views_invalidate_history(self):
        run = self.candidate()
        ack = self.accept(run, self.preview(run)['expected_target'])
        self.assertEqual(self.finish(ack)['state'], 'PUBLISHED')
        saved = self.record(ack['id'])
        changes = [lambda r: r['_native_intent'].pop('views'),
            lambda r: r['_native_intent']['views'].clear(),
            lambda r: r['_native_intent']['views'].append(deepcopy(r['_native_intent']['views'][0])),
            lambda r: r['_native_intent']['views'][0].update(node_id='model.unknown'),
            lambda r: r['_native_intent']['views'][0]['dependency_snapshots'].clear(),
            lambda r: r['_native_intent']['views'][0]['receipt']['candidate'].update(version=True),
            lambda r: r['_native_intent']['views'][0]['receipt']['candidate'].update(_head_digest='A' * 64),
            lambda r: r['_native_intent']['views'][0]['receipt']['candidate'].update(security='DEFINER'),
            lambda r: next(p for p in r['_native_intent']['tables'][0]['dependency_snapshots'].values() if p.get('kind') == 'VIEW').update(version=True),
            lambda r: next(p for p in r['_native_intent']['tables'][0]['dependency_snapshots'].values() if p.get('kind') == 'VIEW').update(_head_digest='broken')]
        for change in changes:
            corrupted = deepcopy(saved)
            change(corrupted)
            with control_store.connect() as db:
                self.assertIsNone(dbt._native_publication_saved(db, corrupted))
                dbt._write(db, corrupted)
            with self.assertRaises(QueryError):
                dbt.get_publication(*self.publisher(), ack['id'])
            with control_store.connect() as db:
                dbt._write(db, deepcopy(saved))

    def test_saved_view_measurements_require_strict_digest_version_and_identity(self):
        run = self.candidate()
        saved = self.record(run['id'])
        view = next(m for m in saved['models'] if m['materialized'] == 'view')
        measurement = view['_measurement']
        for field, value in [('version', True), ('version', 0), ('view_uuid', 'invalid'),
                ('schema_id', True), ('security', 'invoker'), ('_head_digest', 'g' * 64),
                ('_definition_digest', 'f' * 63), ('_canonical_definition_digest', 'A' * 64),
                ('schema_fingerprint', None), ('snapshot', '1')]:
            altered = deepcopy(measurement)
            altered[field] = value
            self.assertFalse(graph.valid_view_measurement(altered), (field, value))
        self.assertTrue(graph.valid_view_measurement(measurement))
        for field in ('candidate', '_measurement'):
            self.mutate(run['id'], lambda r: next(m for m in r['models'] if m['materialized'] == 'view')[field].update(version=True))
            with self.assertRaises(QueryError):
                self.preview(run)
            with control_store.connect() as db:
                dbt._write(db, deepcopy(saved))

    def test_view_drift_before_preview_and_post_has_no_acceptance_or_sql(self):
        run = self.candidate()
        expected = self.preview(run)['expected_target']
        metadata = deepcopy(self.view_metadata())
        faults = [lambda m: m.update(**{'view-uuid': str(uuid.uuid4())}),
            lambda m: m.update(**{'current-version-id': 99}),
            lambda m: m['versions'][0]['representations'][0].update(sql='SELECT 99 AS id'),
            lambda m: m.update(**{'version-log': []})]
        for index, fault in enumerate(faults):
            current = self.view_metadata()
            current.clear(); current.update(deepcopy(metadata))
            fault(current)
            before = len(self.w.posts)
            with control_store.connect() as db:
                count = db.execute("SELECT count(*) FROM dbt_runs WHERE json_extract(record,'$.kind')='publication'").fetchone()[0]
            with self.assertRaises((QueryError, __import__('database_inventory').InventoryError)):
                self.preview(run)
            with self.assertRaises((QueryError, __import__('database_inventory').InventoryError)):
                self.accept(run, expected, key='drift-' + str(index))
            self.assertEqual(len(self.w.posts), before)
            with control_store.connect() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM dbt_runs WHERE json_extract(record,'$.kind')='publication'").fetchone()[0], count)

    def test_post_acceptance_boolean_frozen_view_version_cannot_reach_sql(self):
        run = self.candidate()
        ack = self.accept(run, self.preview(run)['expected_target'])
        self.mutate(ack['id'], lambda r: r['_native_intent']['views'][0]['receipt']['candidate'].update(version=True))
        before = len(self.w.posts)
        with self.assertRaises(QueryError):
            self.finish(ack)
        self.assertEqual(len(self.w.posts), before)
        record = self.record(ack['id'])
        self.assertEqual(record['state'], 'FAILED', record)
        self.assertEqual(record['error_code'], 'DBT_NATIVE_PUBLICATION_INVALID')
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM dataops_queries q JOIN dbt_run_operations l '
                'ON l.operation_id=q.operation_id WHERE l.run_id=?', (ack['id'],)).fetchone()[0], 0)

    def test_pre_audit_view_drift_sends_no_sql(self):
        run = self.candidate()
        ack = self.accept(run, self.preview(run)['expected_target'])
        self.view_metadata()['current-version-id'] = 99
        before = len(self.w.posts)
        result = self.finish(ack)
        self.assertEqual(result['state'], 'FAILED', result)
        self.assertEqual(len(self.w.posts), before)
        self.assertNotIn('native_audit', result)

    def test_after_audit_view_drift_retains_audit_and_sends_no_formal_sql(self):
        run = self.candidate()
        ack = self.accept(run, self.preview(run)['expected_target'])
        original = dbt._native_publication_child
        def drift(ident, principal, context, phase):
            result = original(ident, principal, context, phase)
            if phase == 'audit':
                self.view_metadata()['current-version-id'] = 99
            return result
        before = len(self.w.posts)
        with patch.object(dbt, '_native_publication_child', side_effect=drift):
            result = self.finish(ack)
        self.assertEqual(result['state'], 'FAILED', result)
        self.assertTrue(result['native_audit']['passed'])
        self.assertEqual(len(self.w.posts), before + 1)

    def test_historical_publication_survives_view_drop_retirement_cancel_and_no_rpc(self):
        self.artifact_patch.stop()
        (self.project / 'models/extra_result.sql').write_text("SELECT id, label FROM {{ ref('final_result') }}")
        self.save_plan()
        run = self.candidate()
        expected = self.preview(run)['expected_target']
        ack = self.accept(run, expected)
        published = self.finish(ack)
        self.assertEqual(published['state'], 'PUBLISHED', published)
        selected = self.uid
        self.uid = 'model.general_native.extra_result'
        other = self.accept(run, self.preview(run)['expected_target'], key='other-table')
        self.assertEqual(other['state'], 'QUEUED')
        self.uid = selected
        self.w.snapshot(self.w.metadata(registry.ResourceRef(**published['native_publication']['target']['ref'])), [(8, 'later target')])
        metadata = self.view_metadata()
        version = deepcopy(metadata['versions'][0])
        version['version-id'] = 2
        metadata['versions'].append(version)
        metadata['current-version-id'] = 2
        self.assertEqual(dbt.get_publication(*self.publisher(), ack['id'])['state'], 'PUBLISHED')
        with control_store.connect() as db:
            db.execute('DELETE FROM grants WHERE principal_id=? AND database_id=?', (self.principal.principal_id, self.context.database_id))
        view = next(m for m in self.record(run['id'])['models'] if m['materialized'] == 'view')
        ref = registry.ResourceRef(**view['candidate']['ref'])
        with control_store.connect() as db:
            db.execute('UPDATE object_bindings SET current=0 WHERE id=?', (ref.object_id,))
        for key in list(self.b.objects):
            if key[3] == 'views':
                del self.b.objects[key]
        self.mutate(run['id'], lambda r: r.update(cancel_requested=True, state='CANCELED'))
        with patch.object(queries, '_transport', side_effect=AssertionError('SQL')), \
                patch.object(__import__('database_inventory'), 'catalog_api', side_effect=AssertionError('Catalog RPC')), \
                patch.object(graph, '_run_subprocess', side_effect=AssertionError('SDK')), \
                patch.object(dbt, '_native_publisher', side_effect=AssertionError('source current grant')):
            self.assertEqual(dbt.get_publication(*self.publisher(), ack['id'])['state'], 'PUBLISHED')
            self.assertEqual(dbt.list_publications(*self.publisher())[0]['views'], published['views'])
            self.assertEqual(self.accept(run, expected)['id'], ack['id'])

    def test_unrelated_view_is_frozen_and_guarded(self):
        self.artifact_patch.stop()
        (self.project / 'models/unrelated.sql').write_text("{{ config(materialized='view', properties={}) }} SELECT id FROM {{ ref('prepared_rows') }}")
        self.save_plan()
        run = self.execute(views.ViewGraphTests.accept(self, tests=True))
        self.assertTrue(run['publication_allowed'], run)
        preview = self.preview(run)
        self.assertEqual(len(preview['views']), 2)
        unrelated = next(m for m in self.record(run['id'])['models'] if m['unique_id'].endswith('.unrelated'))
        with control_store.connect() as db:
            obj = registry.get_object(db, unrelated['candidate']['ref']['object_id'])
        metadata = next(value for key, value in self.b.objects.items() if key[3] == 'views' and key[2] == obj.physical_name)
        original = deepcopy(metadata)
        metadata['current-version-id'] = 99
        with self.assertRaises(QueryError):
            self.accept(run, preview['expected_target'])
        metadata.clear(); metadata.update(original)
        ack = self.accept(run, preview['expected_target'])
        self.assertEqual(len(self.record(ack['id'])['_native_intent']['views']), 2)
        self.assertEqual(self.finish(ack)['state'], 'PUBLISHED')

    def test_view_ephemeral_and_global_quality_failures_denied(self):
        run = self.candidate()
        saved = self.record(run['id'])
        for model in saved['models']:
            if model['materialized'] != 'table':
                with self.assertRaises(QueryError):
                    dbt.get_publication_preview(*self.publisher(), run['id'], node_id=model['unique_id'])
        for change in [lambda r: r.update(tests_status='NOT_RUN'), lambda r: r.update(state='UNKNOWN'),
                lambda r: r['tests'].clear(), lambda r: r['tests'][0].update(state='FAILED', failure_count=1),
                lambda r: r['task_receipts']['test'].update(success=False)]:
            self.mutate(run['id'], change)
            with self.assertRaises(QueryError):
                self.preview(run)
            with control_store.connect() as db:
                dbt._write(db, deepcopy(saved))

    def test_acceptance_race_rechecks_view_without_reservation(self):
        run = self.candidate()
        expected = self.preview(run)['expected_target']
        accept = dbt._accept
        def race(*args, **kwargs):
            self.view_metadata()['current-version-id'] = 99
            return accept(*args, **kwargs)
        before = len(self.w.posts)
        with patch.object(dbt, '_accept', side_effect=race), self.assertRaises(QueryError):
            self.accept(run, expected)
        self.assertEqual(len(self.w.posts), before)
        selected = next(m for m in self.record(run['id'])['models'] if m['unique_id'] == self.uid)
        self.assertNotIn('publication_id', selected)

    def test_final_send_after_wake_rechecks_view_before_audit_and_publish(self):
        run = self.candidate()
        original = deepcopy(self.view_metadata())
        wake = queries._ensure_query_awake
        for phase in ('audit', 'publish'):
            metadata = self.view_metadata()
            metadata.clear(); metadata.update(deepcopy(original))
            if phase == 'publish':
                # Use a fresh run because the first accepted publication reserves its TABLE.
                run = self.execute(views.ViewGraphTests.accept(self, key='second-run', tests=True))
            ack = self.accept(run, self.preview(run)['expected_target'], key=phase)
            def changed(principal, query_id):
                value = wake(principal, query_id)
                with control_store.connect() as db:
                    step = db.execute('SELECT step FROM dataops_queries WHERE query_id=?', (query_id,)).fetchone()[0]
                if step == phase:
                    # Drift every VIEW, including the fresh second run.
                    for key, value in self.b.objects.items():
                        if key[3] == 'views':
                            value['current-version-id'] = 99
                return value
            before = len(self.w.posts)
            with patch.object(queries, '_ensure_query_awake', side_effect=changed):
                result = self.finish(ack)
            self.assertEqual(result['state'], 'FAILED', result)
            self.assertEqual(len(self.w.posts), before + (phase == 'publish'))
            if phase == 'publish':
                self.assertTrue(result['native_audit']['passed'])

    def test_raw_append_after_acceptance_preserves_pinned_view_lineage(self):
        run = self.candidate()
        ack = self.accept(run, self.preview(run)['expected_target'])
        self.w.snapshot(self.w.metadata(self.source), [(7, 'new raw append')])
        self.assertEqual(self.finish(ack)['state'], 'PUBLISHED')

    def test_no_view_graph_keeps_old_wire_and_rejects_injected_private_views(self):
        self.artifact_patch.stop()
        model = self.project / 'models/arbitrary_result.sql'
        model.write_text(model.read_text().replace("materialized='view', properties={}", "materialized='table'"))
        self.save_plan()
        run = self.execute(views.ViewGraphTests.accept(self, tests=True))
        self.assertTrue(run['publication_allowed'])
        preview = self.preview(run)
        self.assertNotIn('views', preview)
        ack = self.accept(run, preview['expected_target'])
        self.assertNotIn('views', ack)
        self.assertNotIn('views', self.record(ack['id'])['_native_intent'])
        published = self.finish(ack)
        self.assertEqual(published['state'], 'PUBLISHED', published)
        self.assertNotIn('views', published)
        self.mutate(ack['id'], lambda r: r['_native_intent'].update(views=[]))
        with patch.object(queries, '_transport', side_effect=AssertionError('SQL')):
            with self.assertRaises(QueryError):
                dbt.get_publication(*self.publisher(), ack['id'])
            with self.assertRaises(QueryError):
                dbt.list_publications(*self.publisher())

    def test_pure_view_graph_never_allows_publication(self):
        with patch.object(self, 'accept', side_effect=lambda **kwargs: views.ViewGraphTests.accept(self, **kwargs)):
            views.ViewGraphTests.test_pure_view_physical_graph_with_ephemeral_and_complete_tests(self)
        with control_store.connect() as db:
            ident = db.execute("SELECT id FROM dbt_runs WHERE json_extract(record,'$.kind')='run' ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        result = dbt.get_run(self.principal, self.context, ident)
        self.assertFalse(result['publication_allowed'])
        for model in result['models']:
            with self.assertRaises(QueryError):
                dbt.get_publication_preview(*self.publisher(), ident, node_id=model['unique_id'])

    test_role_database_context_and_cancellation_boundaries = publication.GraphPublicationTests.test_role_database_context_and_cancellation_boundaries
    test_installed_no_tests_and_failed_source_quality_never_authorize_a_table = publication.GraphPublicationTests.test_installed_no_tests_and_failed_source_quality_never_authorize_a_table
    def test_existing_target_review_alias_head_and_uuid_compare_then_replace(self):
        self.artifact_patch.stop()
        model = self.project / 'models/final_result.sql'
        model.write_text("{{ config(alias='formal_alias') }} " + model.read_text())
        ref = self.w.existing('formal_alias', schema='marts')
        self.save_plan()
        run = self.candidate()
        preview = self.preview(run)
        self.assertEqual(preview['target']['name'], 'formal_alias')
        self.w.snapshot(self.w.metadata(ref), [(8, 'advanced')])
        self.code('DBT_PUBLICATION_REVIEW_CHANGED', self.accept, run, preview['expected_target'])
        expected = self.preview(run)['expected_target']
        metadata = self.w.metadata(ref)
        identity = metadata['table-uuid']
        metadata['table-uuid'] = str(uuid.uuid4())
        self.code('DBT_PUBLICATION_REVIEW_CHANGED', self.accept, run, expected)
        metadata['table-uuid'] = identity
        self.assertEqual(self.finish(self.accept(run, expected))['state'], 'PUBLISHED')
        self.assertTrue(self.w.posts[-1][0].startswith('CREATE OR REPLACE TABLE '))

    def test_final_send_after_wake_rechecks_target_before_any_sql(self):
        run = self.candidate()
        ack = self.accept(run, self.preview(run)['expected_target'])
        wake = queries._ensure_query_awake
        def changed(*args, **kwargs):
            result = wake(*args, **kwargs)
            self.w.existing('final_result', schema='marts')
            return result
        before = len(self.w.posts)
        with patch.object(queries, '_ensure_query_awake', side_effect=changed):
            result = self.finish(ack)
        self.assertEqual(result['state'], 'FAILED', result)
        self.assertEqual(result['error_code'], 'DESTINATION_EXISTS')
        self.assertEqual(len(self.w.posts), before)

    test_publisher_or_source_privilege_loss_denies_publication_before_sql = publication.GraphPublicationTests.test_publisher_or_source_privilege_loss_denies_publication_before_sql
    test_publish_ack_loss_is_unknown_and_same_key_does_not_send_again = publication.GraphPublicationTests.test_publish_ack_loss_is_unknown_and_same_key_does_not_send_again


if __name__ == '__main__':
    unittest.main()
