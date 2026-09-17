"""Fixed Pod acquisition routing; all subprocess and API paths remain fake."""
import json
import unittest
from unittest.mock import Mock, patch

from test_local_gateway import MODULE


class FixedPodDispatchTests(unittest.TestCase):
    def setUp(self):
        self.backend = MODULE.Backends.__new__(MODULE.Backends)
        self.arguments = ['kubectl', '--context', MODULE.CONTEXT, '-n', MODULE.NAMESPACE,
                          'get', 'pods', '--chunk-size=50', '-o', 'json']

    def test_fixed_pods_use_one_bounded_config_node_list_path(self):
        reader = Mock()
        reader.fixed_list.return_value = {'items': []}
        with patch.object(MODULE, 'load_module', return_value=reader) as load, \
                patch.object(MODULE.subprocess, 'run') as generic:
            self.assertEqual(self.backend.command_json(self.arguments), {'items': []})
        load.assert_called_once_with('gateway_pod_read', MODULE.HERE / 'pod_read.py')
        reader.fixed_list.assert_called_once_with()
        generic.assert_not_called()

    def test_failed_bounded_pods_do_not_fall_back_to_long_generic_reads(self):
        reader = Mock()
        reader.fixed_list.side_effect = RuntimeError('fixed read refused')
        with patch.object(MODULE, 'load_module', return_value=reader), \
                patch.object(MODULE.subprocess, 'run') as generic:
            with self.assertRaises(RuntimeError):
                self.backend.command_json(self.arguments)
        generic.assert_not_called()
        reader.fixed_list.assert_called_once_with()

    def test_other_reads_retain_their_existing_scope_guard(self):
        scope = Mock()
        response = Mock(returncode=0, stdout='{"items":[]}')
        with patch.object(MODULE, 'load_module', return_value=scope), \
                patch.object(MODULE.subprocess, 'run', return_value=response) as generic:
            result = self.backend.command_json(['kubectl', '--context', MODULE.CONTEXT,
                                                '-n', MODULE.NAMESPACE, 'get', 'events', '-o', 'json'])
        self.assertEqual(result, {'items': []})
        scope.check_context.assert_called_once_with()
        scope.fixed_list.assert_not_called()
        generic.assert_called_once()

    def test_server_continuation_marks_both_scopes_incomplete_without_reads(self):
        self.backend.command_json = Mock(return_value={'metadata': {'continue': 'PRIVATE_CONTINUATION'}, 'items': []})
        self.backend.pod_usage_get = Mock(side_effect=AssertionError('unexpected usage read'))
        self.backend.pod_logs_get = Mock(side_effect=AssertionError('unexpected log read'))
        result = self.backend.k8s_read_pods(MODULE.NAMESPACE, 50)
        self.assertTrue(result['truncated'])
        for key in ('resource_usage', 'log_signals'):
            self.assertEqual(result[key]['state'], 'unknown')
            self.assertEqual(result[key]['reason'], 'POD_LIST_INCOMPLETE')
            self.assertEqual(result[key]['get_attempts'], 0)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.backend.pod_usage_get.assert_not_called()
        self.backend.pod_logs_get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
