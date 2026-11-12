"""VIEW preparation characterization through the existing private execution entrypoint.

The harness uses temporary SQLite/WAP/admission with fake SQL transport. These
cases also run against the pre-facade DataOps and dbt implementation.
"""
from copy import deepcopy
import hashlib
import unittest
from unittest.mock import patch

import test_db_dbt as harness


dbt = harness.dbt
ds = harness.ds
wap = harness.wap
compiler = harness.compiler
qs = harness.qs


class ViewBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.h = harness.DbtTests()
        self.addCleanup(self.h.doCleanups)
        self.h.setUp()
        self.h.view_project()
        self.admitted = None
        real_node = dbt._view_node

        def capture(execution):
            result = real_node(execution)
            self.admitted = result
            return result

        node_patch = patch.object(dbt, '_view_node', side_effect=capture)
        self.node = node_patch.start()
        self.addCleanup(node_patch.stop)

    def saved_model(self):
        run, node, _ = self.admitted
        saved = self.h.record(run['id'])
        return saved, next(m for m in saved['models'] if m['unique_id'] == node['unique_id'])

    def assert_no_digest_or_send(self):
        self.assertTrue(self.node.called)
        self.assertNotIn('_view_definition_digest', self.saved_model()[1])
        self.assertEqual(self.h.w.posts, [])

    def test_sql_wap_original_snapshot_and_canonical_digest_saved_before_send(self):
        source = self.h.sources[self.h.context.database_id]
        original = str(self.h.w.metadata(source)['current-snapshot-id'])
        real_compile = wap._compile_model
        real_transport = self.h.w.transport
        sent = []

        def compile_and_append(*args, **kwargs):
            sql = real_compile(*args, **kwargs)
            if kwargs.get('execution') is not None and self.admitted and not sent:
                self.h.w.snapshot(self.h.w.metadata(source), [(9, 'later')])
            return sql

        def transport(url, method, pin, body=None):
            sql = body.decode()
            if sql.startswith('CREATE VIEW '):
                select = sql.split(' SECURITY INVOKER AS ', 1)[1]
                self.assertIn('FOR VERSION AS OF ' + original, select)
                canonical = compiler._sql_tree(select).sql(dialect='trino')
                self.assertEqual(self.saved_model()[1]['_view_definition_digest'],
                                 hashlib.sha256(canonical.encode()).hexdigest())
                sent.append(sql)
            return real_transport(url, method, pin, body)

        with patch.object(wap, '_compile_model', side_effect=compile_and_append), \
                patch.object(qs, '_transport', side_effect=transport):
            run = self.h.build_run()
        self.assertEqual(run['state'], 'READY', run)
        self.assertEqual(self.node.call_count, 1)
        self.assertEqual(len(sent), 1)

    def test_view_input_rejection_precedes_compile_digest_and_send(self):
        real_pinned = dbt._pinned

        def view_input(*args):
            inputs = deepcopy(real_pinned(*args))
            next(iter(inputs.values()))['kind'] = 'VIEW'
            return inputs

        with patch.object(dbt, '_pinned', side_effect=view_input), \
                patch.object(wap, '_compile_model', wraps=wap._compile_model) as compile_model:
            run = self.h.build_run()
        self.assertEqual(run['error_code'], 'UNSUPPORTED_VIEW_DEPENDENCY', run)
        self.assertFalse(any(call.kwargs.get('execution') for call in compile_model.call_args_list))
        self.assert_no_digest_or_send()

    def test_compiler_failure_precedes_digest_and_send(self):
        real_compile = wap._compile_model
        failures = []

        def fail(*args, **kwargs):
            if kwargs.get('execution') is not None and self.admitted:
                failures.append(True)
                raise harness.QueryError('INVALID_SELECT', 400)
            return real_compile(*args, **kwargs)

        with patch.object(wap, '_compile_model', side_effect=fail):
            run = self.h.build_run()
        self.assertEqual(run['error_code'], 'INVALID_SELECT', run)
        self.assertEqual(failures, [True])
        self.assert_no_digest_or_send()

    def test_cancel_after_compile_survives_digest_write_and_blocks_send(self):
        real_compile = wap._compile_model
        real_write = dbt._write
        writes = []

        def cancel(*args, **kwargs):
            sql = real_compile(*args, **kwargs)
            if kwargs.get('execution') is not None and self.admitted:
                run = self.admitted[0]
                dbt.cancel_run(self.h.engineer, self.h.context, run['id'], key='cancel-after-compile')
            return sql

        def write(db, record):
            real_write(db, record)
            model = next((m for m in record.get('models', [])
                          if m.get('_view_definition_digest')), None)
            if model:
                writes.append((record['cancel_requested'], record['state']))

        with patch.object(wap, '_compile_model', side_effect=cancel), \
                patch.object(dbt, '_write', side_effect=write):
            run = self.h.build_run()
        self.assertEqual(run['state'], 'CANCELED', run)
        self.assertTrue(run['cancel_requested'])
        self.assertIn((True, 'CANCELING'), writes)
        self.assertIn('_view_definition_digest', self.saved_model()[1])
        self.assertEqual(self.h.w.posts, [])

    def test_native_graph_additional_plan_read_rejects_before_bindings_pins_write_send(self):
        import database_dbt_graph as graph
        real_node = self.node.side_effect
        real_check = dbt._check_plan
        armed = False
        reads = []

        def node(execution):
            nonlocal armed
            run, model, locator = real_node(execution)
            # All admission checks run against the real sql_wap child first.
            # Choose the native branch only after _view_node has returned.
            run = deepcopy(run)
            run['executor'] = 'native_graph'
            armed = True
            return run, model, locator

        def check(db, run):
            if armed:
                reads.append(run['id'])
                write.assert_not_called()
                raise harness.QueryError('DBT_PLAN_CHANGED', 409)
            return real_check(db, run)

        self.node.side_effect = node
        with patch.object(dbt, '_check_plan', side_effect=check), \
                patch.object(graph, 'bindings_for', wraps=graph.bindings_for) as bindings, \
                patch.object(dbt, '_pinned', wraps=dbt._pinned) as pins, \
                patch.object(dbt, '_write', wraps=dbt._write) as write:
            # Only writes after admission are relevant to the preparation seam.
            def arm_write(execution):
                result = node(execution)
                write.reset_mock()
                return result
            self.node.side_effect = arm_write
            run = self.h.build_run()
        self.assertEqual(run['error_code'], 'DBT_PLAN_CHANGED', run)
        self.assertEqual(reads, [run['id']])
        bindings.assert_not_called()
        pins.assert_not_called()
        # Parent failure handling may write; preparation must never write a digest.
        self.assertFalse(any(any(m.get('_view_definition_digest') for m in call.args[1].get('models', []))
                             for call in write.call_args_list))
        self.assert_no_digest_or_send()
