"""Pure rule/config/state contracts and fake HTTP lifecycle; no actual service."""
from __future__ import annotations

import copy
import base64
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

from tools.local_experiment.activation import grafana as A

TASK = 'activation-' + 'a' * 32
NOW = datetime(2026, 9, 20, 12, 0, 30, tzinfo=timezone.utc)


def notification_config():
    return {'template_files': {'kept': 'template body'}, 'alertmanager_config': {
        'route': {'receiver': 'existing', 'group_by': ['grafana_folder', 'alertname'],
                  'routes': [{'receiver': 'other', 'object_matchers': [['team', '=', 'kept']]}]},
        'receivers': [{'name': 'existing', 'grafana_managed_receiver_configs': [
            {'uid': 'kept', 'type': 'email', 'name': 'existing', 'settings': {'addresses': 'saved@example.invalid'},
             'secureFields': {}}]}, {'name': 'other', 'grafana_managed_receiver_configs': []}],
        'mute_time_intervals': [{'name': 'unchanged', 'time_intervals': []}]}}


def state(state='inactive'):
    rule = {'uid': A.RULE_UID, 'name': A.TITLE, 'type': 'alerting', 'state': state, 'health': 'ok',
            'labels': A.labels(TASK), 'lastEvaluation': '2026-09-20T12:00:25Z', 'alerts': []}
    if state == 'firing':
        rule['alerts'] = [{'state': 'Alerting', 'labels': dict(A.labels(TASK), alertname=A.TITLE),
                           'activeAt': '2026-09-20T12:00:20Z'}]
    return {'status': 'success', 'data': {'groups': [{'name': A.GROUP, 'file': A.FOLDER_UID, 'rules': [rule]}]}}


class FakeClient:
    def __init__(self):
        self.config = notification_config()
        self.folder = None
        self.group = None
        self.calls = []
        self.fail_put = False
        self.fail_receiver = False
        self.fail_route = False
        self.tree_version = 'tree-before'
        self.owned_receiver = None

    def tree(self):
        root = self.config['alertmanager_config']['route']
        routes = []
        for item in root.get('routes', []):
            route = copy.deepcopy(item)
            route['matchers'] = [{'label': k, 'type': op, 'value': val} for k, op, val in route.pop('object_matchers', [])]
            route.setdefault('continue', False)
            routes.append(route)
        return {'apiVersion': A.NOTIFICATION_API, 'kind': 'RoutingTree',
                'metadata': {'name': 'user-defined', 'namespace': 'default', 'uid': 'tree-uid',
                             'resourceVersion': self.tree_version, 'annotations': {'grafana.com/access/canWrite': 'true'}},
                'spec': {'defaults': {k: copy.deepcopy(v) for k, v in root.items() if k != 'routes'}, 'routes': routes}}

    def request(self, path, *, method='GET', data=None, missing_ok=False):
        self.calls.append((path, method, copy.deepcopy(data)))
        if path == A.CONFIG_PATH:
            if method != 'GET':raise AssertionError('removed legacy write endpoint')
            return copy.deepcopy(self.config)
        if path == A.RECEIVERS_PATH:
            if method == 'POST':
                if data['metadata'].get('name'):raise AssertionError('pinned API rejects client name')
                if self.fail_receiver:raise A.GrafanaError('ACTIVATION_HTTP_FAILED')
                self.owned_receiver = copy.deepcopy(data)
                self.owned_receiver['metadata'].update(name=base64.urlsafe_b64encode(A.RECEIVER.encode()).decode().rstrip('='),
                                                       uid='receiver-uid', resourceVersion='receiver-version')
                self.config['alertmanager_config']['receivers'].append({'name': A.RECEIVER})
                return copy.deepcopy(self.owned_receiver)
            items = [{'spec': {'title': r['name']}} for r in self.config['alertmanager_config']['receivers'] if r['name'] != A.RECEIVER]
            if self.owned_receiver:items.append(copy.deepcopy(self.owned_receiver))
            return {'apiVersion': A.NOTIFICATION_API, 'kind': 'ReceiverList', 'metadata': {}, 'items': items}
        if path == A.ROUTINGTREE_PATH:
            if method == 'PUT':
                if self.fail_route:raise A.GrafanaError('ACTIVATION_HTTP_FAILED')
                if data['metadata']['resourceVersion'] != self.tree_version:raise A.GrafanaError('ACTIVATION_HTTP_FAILED')
                root=copy.deepcopy(data['spec']['defaults']);routes=[]
                for item in data['spec']['routes']:
                    route=copy.deepcopy(item)
                    route['object_matchers']=[[m['label'],m['type'],m['value']] for m in route.pop('matchers', [])]
                    if route.get('continue') is False:route.pop('continue')
                    routes.append(route)
                root['routes']=routes;self.config['alertmanager_config']['route']=root
                self.tree_version='tree-after'
            return self.tree()
        if path == '/api/folders' and method == 'POST':
            self.folder = dict(data, id=12, version=1)
            return copy.deepcopy(self.folder)
        if path == A.FOLDER_PATH:
            return copy.deepcopy(self.folder)
        if path == A.RULE_PATH:
            return copy.deepcopy(self.group['rules'][0]) if self.group else None
        if path == A.GROUP_PATH:
            if method == 'PUT':
                if self.fail_put:
                    raise A.GrafanaError('ACTIVATION_HTTP_FAILED')
                self.group = copy.deepcopy(data)
                self.group['rules'][0].update(id=7, updated='2026-09-20T12:00:00Z', provenance='api')
            return copy.deepcopy(self.group)
        raise AssertionError((path, method))


class ContractTests(unittest.TestCase):
    def test_inactive_empty_alerts_may_be_omitted_but_firing_may_not(self):
        payload = state()
        del payload['data']['groups'][0]['rules'][0]['alerts']
        self.assertEqual(A.observe(payload, TASK, now=NOW)['state'], 'normal')
        payload['data']['groups'][0]['rules'][0]['state'] = 'firing'
        with self.assertRaises(A.GrafanaError):
            A.observe(payload, TASK, now=NOW)

    def test_fixed_rule_and_bounded_task_sql(self):
        group = A.rule_group(TASK)
        self.assertEqual(group['interval'], 10)
        self.assertEqual(group['folderUid'], A.FOLDER_UID)
        self.assertEqual(len(group['rules']), 1)
        rule = group['rules'][0]
        self.assertTrue(rule['isPaused'])
        self.assertEqual((rule['for'], rule['noDataState'], rule['execErrState']), ('0s', 'NoData', 'Error'))
        query, condition = rule['data']
        self.assertEqual(query['datasourceUid'], A.G.DATASOURCE_UID)
        self.assertIn("argMax(status,tuple(timestamp,event_id)) IN ('failed','blocked')", query['model']['rawSql'])
        self.assertIn("task_id='" + TASK + "'", query['model']['rawSql'])
        self.assertIn("agent_id='local-activation-fixture' AND action_type='activation_fixture'", query['model']['rawSql'])
        self.assertEqual(condition['model']['type'], 'threshold')
        self.assertEqual(condition['model']['conditions'][0]['evaluator'], {'params': [0], 'type': 'gt'})
        for bad in ('activation-' + 'a' * 31, TASK + "' OR 1=1", 'other-' + 'a' * 32, None):
            with self.assertRaises(A.GrafanaError):
                A.rule_group(bad)

    def test_rule_readback_accepts_only_documented_metadata(self):
        group = A.rule_group(TASK)
        group['rules'][0].update(id=1, updated='2026-09-20T12:00:00Z', provenance='api')
        group['rules'][0].pop('annotations')
        A.validate_group(group, TASK, paused=True)
        group['rules'][0]['data'][0]['model']['rawSql'] = 'SELECT 1'
        with self.assertRaises(A.GrafanaError):
            A.validate_group(group, TASK, paused=True)

    def test_grafana_13_2_empty_annotations_and_table_query_type(self):
        group = A.rule_group(TASK)
        group['rules'][0]['annotations'] = None
        self.assertEqual(group['rules'][0]['data'][0]['queryType'], 'table')
        A.validate_group(group, TASK, paused=True)

    def test_notification_transform_preserves_every_existing_field_and_input(self):
        before = notification_config()
        raw = A.S.canonical(before)
        after = A.notifications(before, TASK)
        self.assertEqual(A.S.canonical(before), raw)
        self.assertEqual(after['alertmanager_config']['route']['routes'][1:], before['alertmanager_config']['route']['routes'])
        self.assertEqual(after['alertmanager_config']['receivers'][:-1], before['alertmanager_config']['receivers'])
        self.assertTrue(A.verify_notifications(before, after, TASK))
        after['alertmanager_config']['receivers'][-1].pop('grafana_managed_receiver_configs')
        after['alertmanager_config']['route']['routes'][0].pop('continue')
        self.assertTrue(A.verify_notifications(before, after, TASK))

    def test_notification_collision_delivery_and_old_change_refused(self):
        before = notification_config()
        collision = A.notifications(before, TASK)
        with self.assertRaises(A.GrafanaError):
            A.notifications(collision, TASK)
        mutations = [
            lambda v: v['alertmanager_config']['receivers'][-1].update(grafana_managed_receiver_configs=[{'type': 'webhook'}]),
            lambda v: v['alertmanager_config']['route']['routes'][0].update({'continue': True}),
            lambda v: v['alertmanager_config']['route']['routes'].reverse(),
            lambda v: v['template_files'].update(kept='changed'),
        ]
        for mutate in mutations:
            after = A.notifications(before, TASK)
            mutate(after)
            with self.assertRaises(A.GrafanaError):
                A.verify_notifications(before, after, TASK)

    def test_actual_generated_receiver_branch_only_allows_exact_child_and_distinct_order(self):
        child={'group_by':['grafana_folder','alertname'],
               'object_matchers':[['__grafana_receiver__','=','empty']], 'receiver':'empty'}
        before={'template_files':{},'alertmanager_config':{'receivers':[{'name':'empty'}],
            'route':{'receiver':'empty','group_by':['grafana_folder','alertname'],'routes':[
                {'receiver':'empty','object_matchers':[['__grafana_autogenerated__','=','true']], 'routes':[child]}]}}}
        intermediate=A.notifications(before,TASK,route=False)
        branch=intermediate['alertmanager_config']['route']['routes'][0]
        branch['routes'].reverse()
        self.assertTrue(A.verify_notifications(before,intermediate,TASK,route=False))
        after=A.notifications(before,TASK)
        self.assertEqual(after['alertmanager_config']['route']['routes'][1],A.owned_route(TASK))
        self.assertTrue(A.verify_notifications(before,after,TASK))
        proof=A._notification_proof(before,after,{}, {})
        after['alertmanager_config']['route']['routes'][0]['routes'].reverse()
        self.assertEqual(A._notification_proof(before,after,{},{}),proof)
        for mutation in ('new-extra','old-change','duplicate','non-equality'):
            value=copy.deepcopy(after);children=value['alertmanager_config']['route']['routes'][0]['routes']
            new=next(c for c in children if c['receiver']==A.RECEIVER)
            old=next(c for c in children if c['receiver']=='empty')
            if mutation=='new-extra':new['continue']=True
            elif mutation=='old-change':old['group_by']=['changed']
            elif mutation=='duplicate':children.append(copy.deepcopy(old))
            else:old['object_matchers'][0][1]='=~'
            with self.subTest(mutation=mutation),self.assertRaises(A.GrafanaError):
                A.verify_notifications(before,value,TASK)

    def test_fresh_normal_and_firing_stable_fingerprint(self):
        normal = A.observe(state(), TASK, now=NOW)
        self.assertEqual(normal['state'], 'normal')
        self.assertIsNone(normal['fingerprint'])
        value = state('firing')
        first = A.observe(value, TASK, now=NOW)
        value['data']['groups'][0]['rules'][0]['alerts'][0]['activeAt'] = '2026-09-20T21:00:20+09:00'
        second = A.observe(value, TASK, now=NOW)
        self.assertEqual(first, second)
        self.assertEqual(len(first['fingerprint']), 64)
        A.S.normalize_observation(first)

    def test_pinned_normal_instance_is_healthy_without_firing_fingerprint(self):
        value = state()
        rule = value['data']['groups'][0]['rules'][0]
        rule.update(totals={'normal': 1}, totalsFiltered={'normal': 1})
        rule['alerts'] = [{'state': 'Normal', 'activeAt': rule['lastEvaluation'], 'annotations': {},
                           'value': '', 'labels': dict(A.labels(TASK), alertname=A.TITLE,
                                                       grafana_folder=A.FOLDER_TITLE)}]
        original = copy.deepcopy(value)
        self.assertEqual(A.observe(value, TASK, now=NOW), A.observe(state(), TASK, now=NOW))
        self.assertEqual(value, original)

    def test_instance_state_reason_labels_time_and_parent_must_agree(self):
        for parent, expected in [('inactive', 'Normal'), ('firing', 'Alerting')]:
            base = state('firing')
            rule = base['data']['groups'][0]['rules'][0]
            rule['state'] = parent
            rule['alerts'][0]['state'] = expected
            mutations = [
                lambda a, value=value: a.update(state=value)
                for value in ('NoData', 'Error', 'Pending', 'Recovering', 'firing',
                              'Normal (NoData)', 'Alerting (Error)',
                              'Alerting' if parent == 'inactive' else 'Normal')]
            mutations.extend([
                lambda a: a['labels'].update(activation_task='activation-' + 'b' * 32),
                lambda a: a['labels'].pop('activation_rule'),
                lambda a: a['labels'].pop('alertname'),
                lambda a: a.update(activeAt='2026-09-20T12:00:31Z'),
            ])
            for mutate in mutations:
                value = copy.deepcopy(base)
                mutate(value['data']['groups'][0]['rules'][0]['alerts'][0])
                with self.subTest(parent=parent, value=value), self.assertRaises(A.GrafanaError):
                    A.observe(value, TASK, now=NOW)
            rule['alerts'] *= 2
            with self.subTest(parent=parent, duplicate=True), self.assertRaises(A.GrafanaError):
                A.observe(base, TASK, now=NOW)

    def test_unhealthy_stale_unknown_and_ambiguous_rules_never_trigger(self):
        cases = []
        for key, val in [('health', 'error'), ('health', 'nodata'), ('lastError', 'data unavailable'),
                         ('state', 'pending'), ('lastEvaluation', '2026-09-20T11:00:00Z')]:
            value = state('firing'); value['data']['groups'][0]['rules'][0][key] = val; cases.append(value)
        value = state('firing'); value['data']['groups'][0]['rules'] *= 2; cases.append(value)
        value = state('firing'); value['data']['groups'][0]['rules'][0]['alerts'] *= 2; cases.append(value)
        value = state('firing'); value['data']['groups'][0]['rules'][0]['alerts'][0]['labels']['alertname'] = 'DatasourceNoData'; cases.append(value)
        value = state('firing'); value['data']['groups'][0]['rules'][0]['labels']['activation_task'] = 'activation-' + 'b' * 32; cases.append(value)
        value = state('firing'); value['data']['groups'][0]['rules'][0]['labels'] = None; cases.append(value)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(A.GrafanaError):
                A.observe(value, TASK, now=NOW)

    def test_known_initial_unevaluated_and_pending_are_retryable_without_observation(self):
        cases = [{'status': 'success', 'data': {'groups': []}}]
        for timestamp in (None, '0001-01-01T00:00:00Z'):
            value = state(); rule = value['data']['groups'][0]['rules'][0]
            rule.update(lastEvaluation=timestamp, health='unknown'); cases.append(value)
        value = state(); value['data']['groups'][0]['rules'][0]['state'] = 'pending'; cases.append(value)
        for value in cases:
            with self.assertRaisesRegex(A.GrafanaError, '^ACTIVATION_STATE_NOT_READY$'):
                A.observe(value, TASK, now=NOW)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.client = FakeClient()

    def test_create_paused_then_no_delivery_verification_then_unpause_and_pause(self):
        original = copy.deepcopy(self.client.config)
        A.create_rule(TASK, self.directory, client=self.client)
        self.assertTrue(self.client.group['rules'][0]['isPaused'])
        A.configure_notifications(TASK, self.directory, client=self.client)
        A.set_paused(TASK, self.directory, False, client=self.client)
        self.assertFalse(self.client.group['rules'][0]['isPaused'])
        A.set_paused(TASK, self.directory, True, client=self.client)
        self.assertTrue(self.client.group['rules'][0]['isPaused'])
        self.assertTrue(A.verify_notifications(original, self.client.config, TASK))
        self.assertEqual(A.S._read(self.directory / 'rule-owned.json'), self.client.group)
        self.assertEqual(len(list(self.directory.glob('rule-update-*'))), 2)
        self.assertFalse(any('test' in path for path, _, _ in self.client.calls))
        for file in self.directory.rglob('*'):
            self.assertEqual(file.stat().st_mode & 0o777, 0o700 if file.is_dir() else 0o600)

    def test_unknown_existing_rule_or_folder_is_not_modified(self):
        self.client.folder = {'uid': A.FOLDER_UID, 'title': 'retained by another task'}
        with self.assertRaisesRegex(A.GrafanaError, 'ACTIVATION_RULE_COLLISION'):
            A.create_rule(TASK, self.directory, client=self.client)
        self.assertTrue(all(method == 'GET' for _, method, _ in self.client.calls))

    def test_next_task_retargets_only_owned_rule_while_paused_and_reuses_no_delivery_route(self):
        A.create_rule(TASK, self.directory, client=self.client)
        A.configure_notifications(TASK, self.directory, client=self.client)
        A.set_paused(TASK, self.directory, False, client=self.client)
        next_task = 'activation-' + 'b' * 32
        count_posts = len([c for c in self.client.calls if c[1] == 'POST'])
        original_notifications = (self.directory / 'notifications-v1beta1/notifications-before.json').read_bytes()
        A.create_rule(next_task, self.directory, client=self.client)
        self.assertTrue(self.client.group['rules'][0]['isPaused'])
        self.assertEqual(self.client.group['rules'][0]['labels']['activation_task'], next_task)
        A.configure_notifications(next_task, self.directory, client=self.client)
        A.set_paused(next_task, self.directory, False, client=self.client)
        self.assertEqual(len([c for c in self.client.calls if c[1] == 'POST']), count_posts)
        self.assertEqual((self.directory / 'notifications-v1beta1/notifications-before.json').read_bytes(), original_notifications)
        with self.assertRaisesRegex(A.GrafanaError, 'ACTIVATION_RULE_OWNER_CHANGED'):
            A.set_paused(TASK, self.directory, False, client=self.client)

    def test_modified_owned_rule_is_not_retargeted_to_new_task(self):
        A.create_rule(TASK, self.directory, client=self.client)
        self.client.group['rules'][0]['data'][0]['model']['rawSql'] = 'SELECT 99'
        before = len(self.client.calls)
        with self.assertRaisesRegex(A.GrafanaError, 'ACTIVATION_RULE_OWNER_CHANGED'):
            A.create_rule('activation-' + 'b' * 32, self.directory, client=self.client)
        self.assertTrue(all(method == 'GET' for _, method, _ in self.client.calls[before:]))

    def test_unpause_needs_verified_notification_config_and_exact_owned_rule(self):
        A.create_rule(TASK, self.directory, client=self.client)
        count = len([c for c in self.client.calls if c[:2] == (A.GROUP_PATH, 'PUT')])
        with self.assertRaises(FileNotFoundError):
            A.set_paused(TASK, self.directory, False, client=self.client)
        self.assertEqual(len([c for c in self.client.calls if c[:2] == (A.GROUP_PATH, 'PUT')]), count)
        A.configure_notifications(TASK, self.directory, client=self.client)
        self.client.group['rules'][0]['title'] = 'changed'
        with self.assertRaisesRegex(A.GrafanaError, 'ACTIVATION_RULE_OWNER_CHANGED'):
            A.set_paused(TASK, self.directory, False, client=self.client)
        self.assertEqual(len([c for c in self.client.calls if c[:2] == (A.GROUP_PATH, 'PUT')]), count)

    def test_notification_change_blocks_unpause_without_put(self):
        A.create_rule(TASK, self.directory, client=self.client)
        A.configure_notifications(TASK, self.directory, client=self.client)
        self.client.config['alertmanager_config']['receivers'][-1]['grafana_managed_receiver_configs'] = [{'type': 'email'}]
        before = len(self.client.calls)
        with self.assertRaises(A.GrafanaError):
            A.set_paused(TASK, self.directory, False, client=self.client)
        self.assertTrue(all(method == 'GET' for _, method, _ in self.client.calls[before:]))

    def test_unknown_put_is_not_automatically_repeated_even_when_server_still_unchanged(self):
        A.create_rule(TASK, self.directory, client=self.client)
        A.configure_notifications(TASK, self.directory, client=self.client)
        self.client.fail_put = True
        with self.assertRaisesRegex(A.GrafanaError, 'ACTIVATION_HTTP_FAILED'):
            A.set_paused(TASK, self.directory, False, client=self.client)
        self.client.fail_put = False
        before = len(self.client.calls)
        with self.assertRaisesRegex(A.GrafanaError, 'ACTIVATION_RULE_UPDATE_UNKNOWN'):
            A.set_paused(TASK, self.directory, False, client=self.client)
        self.assertEqual(len(self.client.calls), before)

    def test_supported_resource_writes_preserve_failed_legacy_journal_and_versions(self):
        A.S.N._write(self.directory/'notifications-before.json', {'legacy':'retained'})
        A.S.N._write(self.directory/'notifications-intent.json', {'failed':'retained'})
        before={p.name:p.read_bytes() for p in self.directory.iterdir()}
        A.configure_notifications(TASK,self.directory,client=self.client)
        for name,raw in before.items():self.assertEqual((self.directory/name).read_bytes(),raw)
        writes=[c for c in self.client.calls if c[1]!='GET']
        self.assertEqual([(p,m) for p,m,_ in writes],[(A.RECEIVERS_PATH,'POST'),(A.ROUTINGTREE_PATH,'PUT')])
        self.assertNotIn('name',writes[0][2]['metadata'])
        self.assertEqual(writes[0][2]['spec']['integrations'],[])
        self.assertEqual(writes[1][2]['metadata']['resourceVersion'],'tree-before')
        self.assertEqual(writes[1][2]['spec']['routes'][0]['receiver'],A.RECEIVER)

    def test_unknown_receiver_or_route_write_never_retries(self):
        for field in ('fail_receiver','fail_route'):
            with self.subTest(field=field),tempfile.TemporaryDirectory() as name:
                client=FakeClient();setattr(client,field,True)
                directory=Path(name).resolve()
                with self.assertRaises(A.GrafanaError):A.configure_notifications(TASK,directory,client=client)
                calls=len(client.calls);setattr(client,field,False)
                with self.assertRaisesRegex(A.GrafanaError,'ACTIVATION_NOTIFICATION_UPDATE_UNKNOWN'):
                    A.configure_notifications(TASK,directory,client=client)
                self.assertEqual(len(client.calls),calls)

    def test_changed_modern_receiver_identity_blocks_unpause(self):
        A.create_rule(TASK,self.directory,client=self.client)
        A.configure_notifications(TASK,self.directory,client=self.client)
        self.client.owned_receiver['metadata']['uid']='changed'
        calls=len(self.client.calls)
        with self.assertRaises(A.GrafanaError):A.set_paused(TASK,self.directory,False,client=self.client)
        self.assertTrue(all(m=='GET' for _,m,_ in self.client.calls[calls:]))


class HTTPTests(unittest.TestCase):
    def response(self, raw=b'{}', url=None):
        response = Mock()
        response.read.return_value = raw
        response.geturl.return_value = A.G.URL + A.CONFIG_PATH if url is None else url
        return response

    def test_http_fixed_local_auth_method_size_and_no_proxy_redirect(self):
        response = self.response()
        opener = Mock(); opener.open.return_value = response
        with patch.object(A.urllib.request, 'build_opener', return_value=opener) as build:
            actual = A.Client({'admin_password': 'a' * 64}).request(A.CONFIG_PATH)
        self.assertEqual(actual, {})
        handlers = build.call_args.args
        self.assertIsInstance(handlers[0], urllib.request.ProxyHandler)
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], A.NoRedirect)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, A.G.URL + A.CONFIG_PATH)
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(opener.open.call_args.kwargs, {'timeout': 10})
        response.read.assert_called_once_with(A.MAX_HTTP + 1)
        response.close.assert_called_once()

    def test_external_paths_test_notifications_and_redirects_refused(self):
        client = A.Client({'admin_password': 'a' * 64})
        with patch.object(A.urllib.request, 'build_opener') as build:
            for path in ['https://example.invalid/', '/api/alertmanager/grafana/config/api/v1/receivers/test', A.CONFIG_PATH + '?x=1']:
                with self.assertRaises(A.GrafanaError): client.request(path)
            with self.assertRaises(A.GrafanaError):client.request(A.CONFIG_PATH,method='POST',data={})
        build.assert_not_called()
        with self.assertRaisesRegex(A.GrafanaError, 'ACTIVATION_HTTP_REDIRECT'):
            A.NoRedirect().redirect_request(None)

    def test_http_failures_and_oversized_or_invalid_json_are_safe_fixed_errors(self):
        client = A.Client({'admin_password': 'a' * 64})
        for raw in (b'x' * (A.MAX_HTTP + 1), b'{"secret":1,"secret":2}', b'{"x":NaN}'):
            opener = Mock(); opener.open.return_value = self.response(raw)
            with patch.object(A.urllib.request, 'build_opener', return_value=opener), self.assertRaises(A.GrafanaError) as caught:
                client.request(A.CONFIG_PATH)
            self.assertNotIn('secret', str(caught.exception))
        error = urllib.error.HTTPError(A.G.URL + A.RULE_PATH, 404, 'private body', {}, io.BytesIO(b'private'))
        opener = Mock(); opener.open.side_effect = error
        with patch.object(A.urllib.request, 'build_opener', return_value=opener):
            self.assertIsNone(client.request(A.RULE_PATH, missing_ok=True))

    def test_first_cancellation_survives_response_close_failure(self):
        original = KeyboardInterrupt('first')
        response = self.response(); response.read.side_effect = original; response.close.side_effect = OSError('second')
        opener = Mock(); opener.open.return_value = response
        with patch.object(A.urllib.request, 'build_opener', return_value=opener), self.assertRaises(KeyboardInterrupt) as caught:
            A.Client({'admin_password': 'a' * 64}).request(A.CONFIG_PATH)
        self.assertIs(caught.exception, original)


if __name__ == '__main__':
    unittest.main()
