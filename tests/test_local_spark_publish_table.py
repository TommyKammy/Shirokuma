"""Stateful offline Trino/REST proof; no runtime, network or model calls."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock

from tests.test_local_spark_branch_table import Catalog as BranchCatalog, Client, TOKEN, UUID, E, A, B, C
from tools.local_experiment.spark_iceberg.remote_access import AccessError
from tools.local_experiment.spark_publish import table as T
from tools.local_experiment.spark_publish import publish as P

D = 7100000000000000001


class Catalog(BranchCatalog):
    """Emulate Iceberg metadata commits, with snapshot time != publish time."""
    def __init__(self, initial=True):
        super().__init__(initial)
        self.value['metadata'].update({'last-updated-ms': 10000, 'last-sequence-number': 0,
                                       'snapshot-log': [], 'metadata-log': []})
        self.publish_posts, self.fail_publish, self.after_publish = [], None, None

    def changed(self):
        meta = self.value['metadata']
        meta['metadata-log'].append({'timestamp-ms': meta['last-updated-ms'],
                                     'metadata-file': self.value['metadata-location']})
        super().changed()

    def snapshot(self, snapshot, parent, rows, files):
        super().snapshot(snapshot, parent, rows, files)
        self.value['metadata']['last-sequence-number'] = len(self.value['metadata']['snapshots'])

    def head(self, snapshot):
        meta = self.value['metadata']
        meta['current-snapshot-id'] = snapshot
        meta['refs']['main'] = {'type': 'branch', 'snapshot-id': snapshot}
        meta['snapshot-log'].append({'timestamp-ms': meta['last-updated-ms'], 'snapshot-id': snapshot})

    def create(self):
        if self.initial:
            self.snapshot(E, None, [], [])
            self.head(E)
        self.exists = True

    def append(self):
        before = self.value['metadata']['current-snapshot-id']
        if A not in self.rows:
            snapshot, rows, files = A, T.C.ROWS_A, [self.file('a', 2)]
        elif B not in self.rows:
            snapshot, rows, files = B, T.C.ROWS_B, [self.file('a', 2), self.file('b', 1)]
        else:
            snapshot, rows, files = D, T.C.ROWS_D, [self.file('a', 2), self.file('b', 1), self.file('d', 1)]
        self.snapshot(snapshot, None if before == -1 else before, rows, files)
        self.head(snapshot)

    def spark(self, sealed):
        self.snapshot(C, B, T.C.ROWS_C, [self.file('a', 2), self.file('b', 1), self.file('c', 1)])
        self.value['metadata']['refs']['audit_branch']['snapshot-id'] = C
        current = P.project(self.value, TOKEN)
        observed = {'main': deepcopy(T.C.ROWS_B), 'baseline_tag': deepcopy(T.C.ROWS_A),
                    'audit_branch': deepcopy(T.C.ROWS_C)}
        return {'result': 'passed', 'write_outcome': 'verified', 'write_attempts': 1,
                'before': {'refs': deepcopy(sealed['refs']), 'snapshot_summaries': T.C.safe_snapshots(sealed),
                           'snapshot_files': deepcopy(sealed['data_files'])},
                'table_identity': {'table_uuid': UUID, 'table_location': self.scope['location'],
                    'marker': self.scope['marker'], 'metadata_location': current['metadata_location'],
                    'main_snapshot_id': B, 'tag_snapshot_id': A, 'branch_snapshot_id': C, 'parent_snapshot_id': B},
                'snapshot_summaries': [{k:v for k,v in r.items() if k != 'sha256'} for r in current['snapshots']],
                'snapshot_files': {'a': deepcopy(self.files[A]), 'b': deepcopy(self.files[B]), 'c': deepcopy(self.files[C])},
                'dataframe_rows': deepcopy(observed), 'sql_rows': deepcopy(observed)}

    def commit_publish(self, body):
        P.validate_body(body, TOKEN)
        self.publish_posts.append(deepcopy(body))
        if self.fail_publish:
            raise self.fail_publish
        meta = self.value['metadata']
        assert body['requirements'][0]['uuid'] == meta['table-uuid']
        for r in body['requirements'][1:]:
            assert meta['refs'][r['ref']]['snapshot-id'] == r['snapshot-id']
        self.changed()
        self.head(body['updates'][0]['snapshot-id'])
        if self.after_publish:
            self.after_publish()
        return deepcopy(self.value)


class HostTests(unittest.TestCase):
    def setUp(self):
        self.catalog = Catalog()
        self.worker, self.control = Client(self.catalog), Client(self.catalog, True)
        self.table = T.HostTable(TOKEN, self.worker, self.control, self.catalog)

    def written(self, mode='positive'):
        if mode != self.table.mode:
            self.table = T.HostTable(TOKEN, self.worker, self.control, self.catalog, mode=mode)
        sealed = self.table.prepare()
        self.table.mark_spark_pending()
        output = self.catalog.spark(sealed)
        verified = self.table.verify_written(output)
        return sealed, output, verified

    def plan(self):
        self.written()
        return self.table.plan_publish(B)

    def no_cleanup(self):
        self.table.progress = lambda: None
        self.assertFalse(self.table.cleanup(writers_stopped=True, queries_drained=True)['verified'])
        self.assertEqual(self.control.queries, [])

    def test_positive_metadata_only_main_move_keeps_all_snapshot_rows_and_files(self):
        sealed, _, written = self.written()
        self.assertEqual(sealed['refs']['audit_branch']['snapshot_id'], B)
        plan = self.table.plan_publish(B)
        self.assertEqual(plan['ancestry'], [C, B])  # C is numerically smaller; ancestry is not ordering.
        before = self.table.guard()
        result = self.table.publish(plan)
        self.assertEqual(result['outcome'], 'verified')
        proof = result['verification']
        self.assertEqual((proof['main_snapshot_id'], proof['branch_snapshot_id'], proof['tag_snapshot_id']), (C, C, A))
        self.assertTrue(proof['metadata_only'])
        self.assertEqual(proof['new_snapshots'], 0)
        self.assertEqual(proof['rows']['main'], T.C.ROWS_C)
        self.assertEqual(proof['rows']['snapshot_b'], T.C.ROWS_B)
        self.assertEqual(proof['rows']['baseline_tag'], T.C.ROWS_A)
        self.assertEqual(proof['snapshot_files'], written['snapshot_files'])
        self.assertEqual(proof['metadata']['snapshots'], before['snapshots'])
        self.assertEqual(len(self.catalog.publish_posts), 1)
        self.assertEqual([r.get('ref') for r in self.catalog.publish_posts[0]['requirements']],
                         [None, 'main', 'audit_branch', 'baseline_tag'])
        self.assertEqual(proof['metadata']['snapshot_log'][-1]['timestamp-ms'], proof['metadata']['last_updated_ms'])
        self.assertEqual(proof['metadata']['metadata_log'][-1],
                         {'timestamp-ms': before['last_updated_ms'], 'metadata-file': before['metadata_location']})
        self.assertTrue(self.table.cleanup(writers_stopped=True, queries_drained=True)['verified'])
        self.assertFalse(self.catalog.exists)
        self.assertNotIn('never-persist-this-secret', json.dumps(self.table.receipt()))

    def test_optional_initial_empty_snapshot(self):
        self.catalog.initial = False
        sealed, _, _ = self.written()
        self.assertIsNone(sealed['initial_snapshot_id'])
        self.assertEqual(self.table.publish(self.table.plan_publish(B))['outcome'], 'verified')

    def test_divergent_main_rejects_stale_and_nonancestor_without_post_and_proves_all_rows(self):
        self.written('negative')
        changed = self.table.negative_main_append()
        self.assertEqual(changed['snapshot_d'], D)
        for expected, reason in ((B, 'STALE_MAIN'), (D, 'MAIN_NOT_ANCESTOR')):
            plan = self.table.plan_publish(expected)
            self.assertEqual(plan['reason'], reason)
            self.assertIsNone(plan['body_sha256'])
        result = self.table.verify_negative()
        self.assertEqual(result['post_count'], 0)
        self.assertEqual(result['rows']['main'], T.C.ROWS_D)
        self.assertEqual(result['rows']['audit_branch'], T.C.ROWS_C)
        self.assertEqual(result['rows']['snapshot_b'], T.C.ROWS_B)
        self.assertEqual(len(result['rows']), 7)
        self.assertEqual(self.catalog.publish_posts, [])
        self.assertTrue(self.table.cleanup(writers_stopped=True, queries_drained=True)['verified'])

    def test_branch_sibling_history_is_refused_before_publish(self):
        sealed = self.table.prepare()
        self.table.mark_spark_pending()
        output = self.catalog.spark(sealed)
        self.catalog.value['metadata']['snapshots'][-1]['parent-snapshot-id'] = A
        with self.assertRaisesRegex(T.TableError, 'BRANCH_DESCENDANT_INVALID'):
            self.table.verify_written(output)
        self.assertEqual(self.catalog.publish_posts, [])
        self.no_cleanup()

    def test_before_ref_mutation_and_trino_oracle_are_independently_checked(self):
        sealed = self.table.prepare()
        self.table.mark_spark_pending()
        output = self.catalog.spark(sealed)
        bad = deepcopy(output)
        bad['before']['refs']['audit_branch']['snapshot_id'] = A
        with self.assertRaisesRegex(Exception, 'PREWRITE_MISMATCH'):
            self.table.verify_written(bad)
        self.catalog.rows[C] = deepcopy(T.C.ROWS_D)
        with self.assertRaisesRegex(T.TableError, 'TRINO_ORACLE_MISMATCH'):
            self.table.verify_written(output)
        self.no_cleanup()

    def test_main_files_and_old_snapshot_hashes_are_bound(self):
        for field in ('files', 'snapshot'):
            with self.subTest(field=field):
                self.setUp()
                sealed = self.table.prepare()
                self.table.mark_spark_pending()
                output = self.catalog.spark(sealed)
                if field == 'files':
                    self.catalog.files[B][0]['file_size_in_bytes'] += 1
                else:
                    self.catalog.value['metadata']['snapshots'][0]['timestamp-ms'] += 1
                with self.assertRaises(T.TableError):
                    self.table.verify_written(output)
                self.no_cleanup()

    def test_source_change_after_plan_prevents_post_and_drop(self):
        plan = self.plan()
        self.catalog.changed()
        with self.assertRaisesRegex(T.TableError, 'PUBLISH_SOURCE_CHANGED'):
            self.table.publish(plan)
        self.assertEqual(self.catalog.publish_posts, [])
        self.no_cleanup()

    def test_changed_plan_is_not_authority_and_can_never_submit_arbitrary_snapshot(self):
        plan = self.plan()
        for mutation in (lambda p:p.update(target_snapshot=D), lambda p:p.update(expected_main=A),
                         lambda p:p.update(body_sha256='0'*64), lambda p:p.update(extra='arbitrary')):
            bad = deepcopy(plan)
            mutation(bad)
            with self.assertRaisesRegex(T.TableError, 'PUBLISH_PLAN_CHANGED'):
                self.table.publish(bad)
        self.assertEqual(self.catalog.publish_posts, [])

    def test_publish_intent_write_failure_is_before_post(self):
        plan = self.plan()
        def save():
            if self.table.journal['publish_operations'] and self.table.journal['publish_operations'][0]['outcome'] == 'pending':
                raise OSError('private fsync error')
        self.table.progress = save
        with self.assertRaises(OSError):
            self.table.publish(plan)
        self.assertEqual(self.catalog.publish_posts, [])
        self.assertIsNone(self.table.journal['pending'])
        self.assertFalse(self.table.journal['uncertain'])

    def test_lost_post_response_is_not_adopted_from_later_desired_state(self):
        plan = self.plan()
        self.catalog.after_publish = lambda: (_ for _ in ()).throw(TimeoutError('raw private failure'))
        with self.assertRaisesRegex(T.TableError, 'PUBLISH_COMMIT_UNKNOWN'):
            self.table.publish(plan)
        self.assertEqual(self.table.guard()['snapshot_id'], C)
        self.assertEqual(self.table.journal['publish_operations'][0]['outcome'], 'unknown')
        with self.assertRaisesRegex(T.TableError, 'PUBLISH_RETRY_OR_PLAN_INVALID'):
            self.table.publish(plan)
        with self.assertRaisesRegex(T.TableError, 'PUBLISH_SOURCE_UNAVAILABLE'):
            self.table.plan_publish(B)
        self.assertEqual(len(self.catalog.publish_posts), 1)
        self.no_cleanup()
        self.assertNotIn('raw private failure', json.dumps(self.table.receipt()))

    def test_known_conflict_with_exact_unchanged_metadata_is_terminal_rejection(self):
        plan = self.plan()
        self.catalog.fail_publish = AccessError('POLARIS_HTTP_FAILED', outcome='rejected', http_status=409)
        result = self.table.publish(plan)
        self.assertEqual((result['outcome'], result['http_status']), ('rejected', 409))
        self.assertEqual(self.table.guard()['snapshot_id'], B)
        self.assertTrue(self.table.cleanup(writers_stopped=True, queries_drained=True)['verified'])
        self.assertEqual(len(self.catalog.publish_posts), 1)

    def test_conflict_after_concurrent_metadata_change_remains_unknown(self):
        plan = self.plan()
        def conflict(body):
            self.catalog.publish_posts.append(deepcopy(body))
            self.catalog.changed()
            raise AccessError('POLARIS_HTTP_FAILED', outcome='rejected', http_status=409)
        self.catalog.commit_publish = conflict
        with self.assertRaisesRegex(T.TableError, 'PUBLISH_COMMIT_UNKNOWN'):
            self.table.publish(plan)
        self.no_cleanup()

    def test_known_nonconflict_http_rejection_is_conservatively_unknown(self):
        plan = self.plan()
        self.catalog.fail_publish = AccessError('POLARIS_HTTP_FAILED', outcome='rejected', http_status=403)
        with self.assertRaisesRegex(T.TableError, 'PUBLISH_COMMIT_UNKNOWN'):
            self.table.publish(plan)
        self.no_cleanup()

    def test_first_cancellation_during_conflict_readback_survives_secondary_save_failure(self):
        for secondary_failure in (False, True):
            with self.subTest(secondary_failure=secondary_failure):
                self.setUp()
                plan = self.plan()
                cancellation = KeyboardInterrupt()
                def conflict(body):
                    self.catalog.publish_posts.append(deepcopy(body))
                    self.table.guard = Mock(side_effect=cancellation)
                    raise AccessError('POLARIS_HTTP_FAILED', outcome='rejected', http_status=409)
                self.catalog.commit_publish = conflict
                if secondary_failure:
                    def save():
                        if self.table.journal['publish_operations'][0]['outcome'] == 'unknown':
                            raise OSError('secondary write failure')
                    self.table.progress = save
                with self.assertRaises(KeyboardInterrupt) as caught:
                    self.table.publish(plan)
                self.assertIs(caught.exception, cancellation)
                op = self.table.receipt()['publish_operations'][0]
                self.assertEqual((op['outcome'], op['http_status']), ('unknown', 409))
                self.no_cleanup()

    def test_cancel_object_survives_secondary_persistence_failure(self):
        plan = self.plan()
        cancellation = KeyboardInterrupt()
        self.catalog.after_publish = lambda: (_ for _ in ()).throw(cancellation)
        def save():
            if self.table.journal['publish_operations'][0]['outcome'] == 'unknown':
                raise OSError('secondary write failure')
        self.table.progress = save
        with self.assertRaises(KeyboardInterrupt) as caught:
            self.table.publish(plan)
        self.assertIs(caught.exception, cancellation)
        self.no_cleanup()

    def test_post_success_receipt_write_failure_is_unknown_not_verified(self):
        plan = self.plan()
        def save():
            if self.table.journal['publish_operations'][0]['outcome'] == 'verified':
                raise OSError('receipt disk failure')
        self.table.progress = save
        with self.assertRaisesRegex(T.TableError, 'PUBLISH_COMMIT_UNKNOWN'):
            self.table.publish(plan)
        self.assertEqual(self.table.journal['publish_operations'][0]['outcome'], 'unknown')
        self.no_cleanup()

    def test_publish_rejects_unexpected_metadata_delta_and_wrong_history_timestamp(self):
        mutations = [lambda m:m['snapshots'][0].update({'timestamp-ms': 99}),
                     lambda m:m['properties'].update(unexpected='value'),
                     lambda m:m['refs']['baseline_tag'].update({'snapshot-id': C}),
                     lambda m:m['snapshot-log'][-1].update({'timestamp-ms': m['last-updated-ms'] - 1}),
                     lambda m:m['metadata-log'][-1].update({'timestamp-ms': m['last-updated-ms']}),
                     lambda m:m.update({'last-sequence-number': 1})]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.setUp()
                plan = self.plan()
                self.catalog.after_publish = lambda: mutate(self.catalog.value['metadata'])
                with self.assertRaisesRegex(T.TableError, 'PUBLISH_COMMIT_UNKNOWN'):
                    self.table.publish(plan)
                self.no_cleanup()

    def test_published_files_and_rows_are_read_back_from_trino(self):
        for field in ('files', 'rows'):
            with self.subTest(field=field):
                self.setUp()
                plan = self.plan()
                if field == 'files':
                    self.catalog.files[C][0]['file_size_in_bytes'] += 1
                else:
                    self.catalog.rows[C] = deepcopy(T.C.ROWS_D)
                with self.assertRaisesRegex(T.TableError, 'PUBLISH_COMMIT_UNKNOWN'):
                    self.table.publish(plan)
                self.no_cleanup()

    def test_no_double_publish_even_after_success(self):
        plan = self.plan()
        self.table.publish(plan)
        with self.assertRaisesRegex(T.TableError, 'PUBLISH_RETRY_OR_PLAN_INVALID'):
            self.table.publish(plan)
        self.assertEqual(len(self.catalog.publish_posts), 1)

    def test_failed_plan_persistence_never_authorizes_a_post(self):
        self.written()
        self.table.progress = Mock(side_effect=OSError('durable plan failure'))
        with self.assertRaises(OSError):
            self.table.plan_publish(B)
        with self.assertRaisesRegex(T.TableError, 'PUBLISH_RETRY_OR_PLAN_INVALID'):
            self.table.publish(deepcopy(self.table.journal['publish_plans'][0]))
        self.assertEqual(self.catalog.publish_posts, [])

    def test_failure_to_persist_pre_native_intent_does_not_start_spark(self):
        self.table.prepare()
        def save():
            if self.table.journal['spark_outcome'] == 'unknown':
                raise OSError('intent failure')
        self.table.progress = save
        with self.assertRaises(OSError):
            self.table.mark_spark_pending()
        self.assertEqual(self.table.journal['spark_outcome'], 'not-started')
        self.assertIsNone(self.table.journal['pending'])
        self.assertFalse(self.table.journal['uncertain'])

    def test_failed_or_unknown_spark_output_does_not_authorize_publish_or_drop(self):
        self.table.prepare()
        self.table.mark_spark_pending()
        for outcome in ('not-attempted', 'unknown', 'returned'):
            with self.subTest(outcome=outcome):
                with self.assertRaisesRegex(T.TableError, 'SPARK_WRITE_UNCONFIRMED'):
                    self.table.verify_written({'result':'failed', 'write_outcome':outcome, 'write_attempts':1})
        self.assertEqual(self.catalog.publish_posts, [])
        self.no_cleanup()

    def test_negative_append_unknown_prevents_plans_and_cleanup(self):
        self.written('negative')
        self.worker.fail = lambda sql: sql.startswith('INSERT')
        with self.assertRaises(TimeoutError):
            self.table.negative_main_append()
        with self.assertRaisesRegex(T.TableError, 'PUBLISH_SOURCE_UNAVAILABLE'):
            self.table.plan_publish(B)
        self.assertEqual(self.catalog.publish_posts, [])
        self.no_cleanup()

    def test_negative_verify_requires_both_refusals_and_unchanged_D(self):
        self.written('negative')
        self.table.negative_main_append()
        self.table.plan_publish(B)
        with self.assertRaisesRegex(T.TableError, 'NEGATIVE_REJECTIONS_UNVERIFIED'):
            self.table.verify_negative()
        self.table.plan_publish(D)
        self.catalog.rows[D] = deepcopy(T.C.ROWS_C)
        with self.assertRaisesRegex(T.TableError, 'TRINO_ORACLE_MISMATCH'):
            self.table.verify_negative()
        self.assertEqual(self.catalog.publish_posts, [])

    def test_uuid_location_marker_drift_never_authorizes_drop(self):
        for key, value in (('table-uuid', '00000000-0000-4000-8000-000000000002'),
                           ('location', 's3://retained/data'), ('properties', {'comment':'retained'})):
            with self.subTest(key=key):
                self.setUp()
                self.written()
                self.catalog.value['metadata'][key] = value
                with self.assertRaises(T.TableError):
                    self.table.cleanup(writers_stopped=True, queries_drained=True)
                self.assertEqual(self.control.queries, [])


class BoundaryTests(unittest.TestCase):
    def before(self):
        catalog = Catalog()
        owner = T.HostTable(TOKEN, Client(catalog), Client(catalog, True), catalog)
        seal = owner.prepare()
        owner.mark_spark_pending()
        owner.verify_written(catalog.spark(seal))
        return catalog, owner.guard()

    def test_four_CAS_body_is_exact_and_old_branch_admin_stays_restricted(self):
        _, before = self.before()
        body = P.commit_body(before, expected_main=B, branch=C, tag=A)
        client = object.__new__(P.PublishAdmin)
        client.run_token, client.p, client._exchange = TOKEN, {'table': T.names(TOKEN)['path']}, Mock(return_value={})
        client.commit_publish(body)
        client._exchange.assert_called_once_with('POST', T.names(TOKEN)['path'], body, statuses=(200,))
        with self.assertRaises(T.TableError):
            client.commit_reference(body)
        mutations = [lambda b:b['requirements'].pop(), lambda b:b['updates'].append({'action':'remove-snapshots'}),
                     lambda b:b['updates'][0].update({'snapshot-id': True}),
                     lambda b:b['requirements'][2].update(ref='other'),
                     lambda b:b['requirements'][0].update(uuid='arbitrary'),
                     lambda b:b['updates'][0].update({'ref-name':'audit_branch'})]
        for mutate in mutations:
            bad = deepcopy(body)
            mutate(bad)
            with self.assertRaises(T.TableError):
                client.commit_publish(bad)
        self.assertEqual(client._exchange.call_count, 1)

    def test_ancestry_refuses_sibling_but_missing_or_cycle_is_unknown(self):
        _, before = self.before()
        self.assertEqual(P.ancestry(before, B, C), [C, B])
        sibling = deepcopy(before)
        next(s for s in sibling['snapshots'] if s['snapshot_id'] == C)['parent_snapshot_id'] = A
        self.assertIsNone(P.ancestry(sibling, B, C))
        with self.assertRaisesRegex(T.TableError, 'MAIN_NOT_ANCESTOR'):
            P.commit_body(sibling, expected_main=B, branch=C, tag=A)
        for parent in (C, D):
            broken = deepcopy(before)
            next(s for s in broken['snapshots'] if s['snapshot_id'] == C)['parent_snapshot_id'] = parent
            with self.assertRaisesRegex(T.TableError, 'PUBLISH_LINEAGE_UNKNOWN'):
                P.ancestry(broken, B, C)

    def test_publish_update_snapshot_requires_exact_integer_not_equal_float_or_bool(self):
        _, before = self.before()
        body = P.commit_body(before, expected_main=B, branch=C, tag=A)
        for target, invalid in ((2, 2.0), (1, True)):
            bad = deepcopy(body)
            bad['requirements'][2]['snapshot-id'] = target
            bad['updates'][0]['snapshot-id'] = target
            P.validate_body(bad, TOKEN)
            bad['updates'][0]['snapshot-id'] = invalid
            with self.assertRaises(T.TableError):
                P.validate_body(bad, TOKEN)

    def test_projection_rejects_extra_refs_malformed_history_and_outside_paths(self):
        catalog, _ = self.before()
        mutations = [lambda v:v['metadata']['refs'].update(other={'type':'branch','snapshot-id':B}),
                     lambda v:v['metadata']['refs']['main'].update({'min-snapshots-to-keep':1}),
                     lambda v:v['metadata']['metadata-log'][0].update({'metadata-file':'s3://retained/m.json'}),
                     lambda v:v['metadata']['snapshot-log'][-1].update({'snapshot-id':C}),
                     lambda v:v['metadata'].update({'last-sequence-number': True})]
        for mutate in mutations:
            bad = deepcopy(catalog.value)
            mutate(bad)
            with self.assertRaises(T.TableError):
                P.project(bad, TOKEN)


if __name__ == '__main__':
    unittest.main()
