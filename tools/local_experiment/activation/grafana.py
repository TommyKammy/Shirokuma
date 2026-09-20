"""Fixed local Grafana rule and no-delivery route, operated under the owner lock.

All writes retain their prior input and intent before the single HTTP attempt.
An uncertain write is not retried. This module never sends test notifications.
"""
from __future__ import annotations

import base64
import copy
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import urllib.error
import urllib.request
import uuid

from tools.local_experiment.activation import sensor as S
from tools.local_experiment.grafana import manage as G

RULE_UID = S.RULE_UID
FOLDER_UID = RULE_UID
GROUP = 'local-task-failure'
TITLE = 'Shirokuma local activation fixture'
FOLDER_TITLE = 'Shirokuma local activation'
RECEIVER = 'shirokuma-local-activation-no-delivery'
CONFIG_PATH = '/api/alertmanager/grafana/config/api/v1/alerts'
NOTIFICATION_API = 'notifications.alerting.grafana.app/v1beta1'
NOTIFICATION_BASE = '/apis/' + NOTIFICATION_API + '/namespaces/default'
RECEIVERS_PATH = NOTIFICATION_BASE + '/receivers'
ROUTINGTREE_PATH = NOTIFICATION_BASE + '/routingtrees/user-defined'
STATE_PATH = '/api/prometheus/grafana/api/v1/rules'
FOLDER_PATH = '/api/folders/' + FOLDER_UID
RULE_PATH = '/api/v1/provisioning/alert-rules/' + RULE_UID
GROUP_PATH = '/api/v1/provisioning/folder/' + FOLDER_UID + '/rule-groups/' + GROUP
MAX_HTTP = 1024 * 1024
METHODS = {CONFIG_PATH: {'GET'}, RECEIVERS_PATH: {'GET', 'POST'}, ROUTINGTREE_PATH: {'GET', 'PUT'},
           STATE_PATH: {'GET'}, FOLDER_PATH: {'GET'},
           '/api/folders': {'POST'}, RULE_PATH: {'GET'}, GROUP_PATH: {'GET', 'PUT'}}


class GrafanaError(ValueError):
    pass


def require(value, code='ACTIVATION_GRAFANA_INVALID'):
    if not value:
        raise GrafanaError(code)


def task_id(value):
    require(type(value) is str and re.fullmatch(r'activation-[0-9a-f]{32}', value), 'ACTIVATION_TASK_INVALID')
    return value


def labels(task):
    return {'shirokuma_activation': 'local-v1', 'activation_task': task_id(task), 'activation_rule': RULE_UID}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise GrafanaError('ACTIVATION_HTTP_REDIRECT')


class Client:
    def __init__(self, credential=None):
        self.credential = credential

    def request(self, path, *, method='GET', data=None, missing_ok=False):
        require(type(path) is str and method in METHODS.get(path, set()), 'ACTIVATION_HTTP_SCOPE_INVALID')
        require(type(missing_ok) is bool and (not missing_ok or method == 'GET'))
        require((data is None) == (method == 'GET'))
        try:
            credential = G.credentials() if self.credential is None else self.credential
        except Exception:
            raise GrafanaError('ACTIVATION_CREDENTIAL_INVALID') from None
        password = credential.get('admin_password') if type(credential) is dict else None
        require(type(password) is str and re.fullmatch(r'[0-9a-f]{64}', password), 'ACTIVATION_CREDENTIAL_INVALID')
        body = None if data is None else S.canonical(data)
        require(body is None or len(body) <= MAX_HTTP, 'ACTIVATION_HTTP_LIMIT')
        authorization = base64.b64encode(('admin:' + password).encode('ascii')).decode('ascii')
        request = urllib.request.Request(G.URL + path, method=method, data=body,
            headers={'Content-Type': 'application/json', 'Authorization': 'Basic ' + authorization})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        response, first, raw = None, None, None
        try:
            response = opener.open(request, timeout=10)
            require(response.geturl() == G.URL + path, 'ACTIVATION_HTTP_REDIRECT')
            raw = response.read(MAX_HTTP + 1)
        except BaseException as error:
            first = error
        finally:
            target = response if response is not None else first if isinstance(first, urllib.error.HTTPError) else None
            if target is not None:
                try:
                    target.close()
                except BaseException as error:
                    first = S._keep(first, error)
        if first is not None:
            if isinstance(first, urllib.error.HTTPError) and missing_ok and first.code == 404:
                return None
            if isinstance(first, (urllib.error.URLError, OSError, TimeoutError)):
                raise GrafanaError('ACTIVATION_HTTP_FAILED') from None
            raise first
        try:
            require(len(raw) <= MAX_HTTP, 'ACTIVATION_HTTP_LIMIT')
            try:
                return S.N._parse(raw)
            except Exception:
                raise GrafanaError('ACTIVATION_HTTP_JSON_INVALID') from None
        except (OSError, TimeoutError):
            raise GrafanaError('ACTIVATION_HTTP_FAILED') from None


def rule_group(task, *, paused=True):
    task_id(task)
    require(type(paused) is bool)
    sql = ("SELECT toFloat64(argMax(status,tuple(timestamp,event_id)) IN ('failed','blocked')) AS failing "
           "FROM agenthouse.pawprints FINAL WHERE task_id='" + task + "' "
           "AND agent_id='local-activation-fixture' AND action_type='activation_fixture'")
    query = {'refId': 'A', 'queryType': 'table', 'relativeTimeRange': {'from': 300, 'to': 0},
        'datasourceUid': G.DATASOURCE_UID, 'model': {'refId': 'A', 'format': 1,
        'queryType': 'table', 'editorType': 'sql', 'pluginVersion': '4.21.3', 'rawSql': sql,
        'datasource': {'type': 'grafana-clickhouse-datasource', 'uid': G.DATASOURCE_UID},
        'intervalMs': 1000, 'maxDataPoints': 43200}}
    threshold = {'refId': 'B', 'queryType': '', 'relativeTimeRange': {'from': 0, 'to': 0},
        'datasourceUid': '__expr__', 'model': {'refId': 'B', 'type': 'threshold', 'expression': 'A',
        'datasource': {'type': '__expr__', 'uid': '__expr__'}, 'intervalMs': 1000, 'maxDataPoints': 43200,
        'conditions': [{'type': 'query', 'evaluator': {'params': [0], 'type': 'gt'},
                        'operator': {'type': 'and'}, 'query': {'params': ['B']},
                        'reducer': {'params': [], 'type': 'last'}}]}}
    rule = {'uid': RULE_UID, 'orgID': 1, 'folderUID': FOLDER_UID, 'ruleGroup': GROUP, 'title': TITLE,
            'condition': 'B', 'data': [query, threshold], 'noDataState': 'NoData', 'execErrState': 'Error',
            'for': '0s', 'keep_firing_for': '0s', 'isPaused': paused, 'labels': labels(task),
            'annotations': {}, 'notification_settings': None, 'record': None}
    return {'title': GROUP, 'folderUid': FOLDER_UID, 'interval': 10, 'rules': [rule]}


def validate_group(value, task, *, paused):
    expected = rule_group(task, paused=paused)
    require(type(value) is dict and set(value) == set(expected)
            and {k: value[k] for k in ('title', 'folderUid', 'interval')} ==
            {k: expected[k] for k in ('title', 'folderUid', 'interval')})
    rules = value['rules']
    require(type(rules) is list and len(rules) == 1 and type(rules[0]) is dict)
    current = dict(rules[0])
    # Only the provisioning API's documented server-owned metadata is ignored.
    for key in ('id', 'updated', 'provenance'):
        current.pop(key, None)
    if current.get('annotations') is None:
        current['annotations'] = {}
    require(type(current.get('isPaused')) is bool and type(current.get('orgID')) is int)
    require(current == expected['rules'][0], 'ACTIVATION_RULE_READBACK_INVALID')
    return value


def owned_route(task):
    task_id(task)
    return {'receiver': RECEIVER, 'object_matchers': [['activation_rule', '=', RULE_UID],
                                                    ['shirokuma_activation', '=', 'local-v1']],
            'continue': False}


def _generated_branch(root):
    routes = root.get('routes', [])
    require(type(routes) is list)
    branches = [r for r in routes if type(r) is dict
                and r.get('object_matchers') == [['__grafana_autogenerated__', '=', 'true']]]
    require(len(branches) <= 1 and (not branches or routes[0] is branches[0]))
    if not branches:
        return None
    branch = branches[0]
    children = branch.get('routes')
    require(type(children) is list and all(type(c) is dict and type(c.get('receiver')) is str
            and c.get('object_matchers') == [['__grafana_receiver__', '=', c['receiver']]] for c in children))
    require(len({c['receiver'] for c in children}) == len(children))
    # These exact equality matches are mutually exclusive. Grafana constructs
    # them from receiver maps, so only this generated sibling order is ignored.
    children.sort(key=S.canonical)
    return branch


def notifications(before, task, *, route=True):
    task_id(task)
    require(type(before) is dict and type(before.get('alertmanager_config')) is dict)
    result = copy.deepcopy(before)
    config = result['alertmanager_config']
    require(type(config.get('route')) is dict and type(config.get('receivers')) is list)
    receivers = config['receivers']
    require(all(type(v) is dict and type(v.get('name')) is str for v in receivers))
    names = [v['name'] for v in receivers]
    require(len(names) == len(set(names)) and RECEIVER not in names, 'ACTIVATION_NOTIFICATION_COLLISION')
    routes = config['route'].get('routes', [])
    require(type(routes) is list)
    # Refuse a preexisting reference to our reserved receiver anywhere.
    def references(value):
        if type(value) is dict:
            return value.get('receiver') == RECEIVER or any(references(v) for v in value.values())
        return type(value) is list and any(references(v) for v in value)
    require(not references(config['route']), 'ACTIVATION_NOTIFICATION_COLLISION')
    generated = _generated_branch(config['route'])
    if generated is not None:
        generated['routes'].append({'group_by': ['grafana_folder', 'alertname'],
            'object_matchers': [['__grafana_receiver__', '=', RECEIVER]], 'receiver': RECEIVER})
        generated['routes'].sort(key=S.canonical)
    if route:
        routes.insert(1 if generated is not None else 0, owned_route(task))
        config['route']['routes'] = routes
    receivers.append({'name': RECEIVER, 'grafana_managed_receiver_configs': []})
    return result


def verify_notifications(before, after, task, *, route=True):
    expected = notifications(before, task, route=route)
    require(type(after) is dict and type(after.get('alertmanager_config')) is dict)
    actual = copy.deepcopy(after)
    config = actual['alertmanager_config']
    require(type(config.get('route')) is dict and type(config.get('receivers')) is list)
    added = [r for r in config['receivers'] if type(r) is dict and r.get('name') == RECEIVER]
    require(len(added) == 1 and set(added[0]) <= {'name', 'grafana_managed_receiver_configs'}
            and added[0].get('grafana_managed_receiver_configs', []) == [], 'ACTIVATION_NOTIFICATION_UNSAFE')
    # Empty integrations and continue=false may be omitted by Go's omitempty.
    added[0].setdefault('grafana_managed_receiver_configs', [])
    generated = _generated_branch(config['route'])
    if route:
        require(type(config['route'].get('routes')) is list and config['route']['routes'])
        index = 1 if generated is not None else 0
        require(len(config['route']['routes']) > index)
        added_route = config['route']['routes'][index]
        require(type(added_route) is dict and added_route.get('continue', False) is False, 'ACTIVATION_NOTIFICATION_UNSAFE')
        added_route.setdefault('continue', False)
    require(actual == expected, 'ACTIVATION_NOTIFICATION_READBACK_CHANGED')
    return True


def _directory(directory):
    path = Path(directory)
    S._private(path)
    return path


def _save(directory, name, value):
    S.N._write(directory / name, value)


def notification_resource(value, kind):
    require(type(value) is dict and value.get('apiVersion') == NOTIFICATION_API and value.get('kind') == kind
            and type(value.get('metadata')) is dict and type(value.get('spec')) is dict)
    metadata = value['metadata']
    require(metadata.get('namespace') == 'default'
            and all(type(metadata.get(k)) is str and 0 < len(metadata[k]) <= 256
                    for k in ('name', 'uid', 'resourceVersion')))
    if kind == 'Receiver':
        require(value['spec'] == {'title': RECEIVER, 'integrations': []}, 'ACTIVATION_NOTIFICATION_UNSAFE')
    else:
        require(kind == 'RoutingTree' and metadata['name'] == 'user-defined'
                and type(value['spec'].get('defaults')) is dict and type(value['spec'].get('routes')) is list)
    # Access/in-use annotations are server observations, not configuration.
    return {'apiVersion': NOTIFICATION_API, 'kind': kind,
            'metadata': {k: metadata[k] for k in ('name', 'namespace', 'uid', 'resourceVersion')},
            'spec': copy.deepcopy(value['spec'])}


def owned_receiver(value, *, absent=False):
    require(type(value) is dict and value.get('apiVersion') == NOTIFICATION_API
            and value.get('kind') == 'ReceiverList' and type(value.get('items')) is list
            and len(value['items']) <= 128 and not value.get('metadata', {}).get('continue'))
    items = value['items']
    require(all(type(v) is dict and type(v.get('spec')) is dict and type(v['spec'].get('title')) is str for v in items))
    require(len({v['spec']['title'] for v in items}) == len(items))
    selected = [v for v in items if v['spec']['title'] == RECEIVER]
    require(len(selected) == (0 if absent else 1), 'ACTIVATION_NOTIFICATION_COLLISION')
    return None if absent else notification_resource(selected[0], 'Receiver')


def routingtree_update(before, task):
    notification_resource(before, 'RoutingTree')
    result = copy.deepcopy(before)
    route = owned_route(task)
    added = {'receiver': RECEIVER, 'continue': False,
             'matchers': [{'label': key, 'type': operation, 'value': value}
                          for key, operation, value in route['object_matchers']]}
    result['spec']['routes'] = [added] + result['spec']['routes']
    return result


def _notification_proof(before, after, receiver, tree):
    after = copy.deepcopy(after)
    _generated_branch(after['alertmanager_config']['route'])
    return {'schema_version': 1, 'rule_uid': RULE_UID, 'no_delivery': True,
            **{key + '_sha256': hashlib.sha256(S.canonical(value)).hexdigest()
               for key, value in [('before', before), ('after', after), ('receiver', receiver), ('routingtree', tree)]}}


def _verified_notifications(task, directory, client):
    saved = directory / 'notifications-v1beta1'
    S._private(saved)
    before = S._read(saved / 'notifications-before.json', MAX_HTTP)
    verified = S._read(saved / 'notifications-verified.json')
    after = client.request(CONFIG_PATH)
    receiver = owned_receiver(client.request(RECEIVERS_PATH))
    tree = notification_resource(client.request(ROUTINGTREE_PATH), 'RoutingTree')
    verify_notifications(before, after, task)
    require(verified == _notification_proof(before, after, receiver, tree), 'ACTIVATION_NOTIFICATION_READBACK_CHANGED')
    return after


def configure_notifications(task, directory, *, client=None):
    directory, client = _directory(directory), client or Client()
    task_id(task)
    saved = directory / 'notifications-v1beta1'
    if saved.exists():
        S._private(saved)
        require((saved / 'notifications-verified.json').is_file(), 'ACTIVATION_NOTIFICATION_UPDATE_UNKNOWN')
        return _verified_notifications(task, directory, client)
    saved.mkdir(mode=0o700)
    before = client.request(CONFIG_PATH)
    notifications(before, task)  # Refuse reserved title/references before a write.
    receivers = client.request(RECEIVERS_PATH)
    owned_receiver(receivers, absent=True)
    tree = client.request(ROUTINGTREE_PATH)
    desired = routingtree_update(tree, task)
    _save(saved, 'notifications-before.json', before)
    _save(saved, 'receivers-before.json', receivers)
    _save(saved, 'routingtree-before.json', tree)
    # Grafana 13.2.2 receiver Create requires an empty metadata.name; its
    # title-derived server identity is bound by the response and list readback.
    receiver_input = {'apiVersion': NOTIFICATION_API, 'kind': 'Receiver', 'metadata': {'namespace': 'default'},
                      'spec': {'title': RECEIVER, 'integrations': []}}
    _save(saved, 'receiver-intent.json', receiver_input)
    response = client.request(RECEIVERS_PATH, method='POST', data=receiver_input)
    _save(saved, 'receiver-response.json', response)
    receiver = notification_resource(response, 'Receiver')
    require(owned_receiver(client.request(RECEIVERS_PATH)) == receiver, 'ACTIVATION_NOTIFICATION_READBACK_CHANGED')
    intermediate = client.request(CONFIG_PATH)
    _save(saved, 'notifications-after-receiver.json', intermediate)
    verify_notifications(before, intermediate, task, route=False)
    require(client.request(ROUTINGTREE_PATH) == tree, 'ACTIVATION_NOTIFICATION_READBACK_CHANGED')
    _save(saved, 'routingtree-intent.json', desired)
    response = client.request(ROUTINGTREE_PATH, method='PUT', data=desired)
    _save(saved, 'routingtree-response.json', response)
    observed_tree = client.request(ROUTINGTREE_PATH)
    projected_tree = notification_resource(observed_tree, 'RoutingTree')
    expected_tree = notification_resource(desired, 'RoutingTree')
    expected_tree['metadata']['resourceVersion'] = projected_tree['metadata']['resourceVersion']
    require(projected_tree == expected_tree, 'ACTIVATION_NOTIFICATION_READBACK_CHANGED')
    require(notification_resource(response, 'RoutingTree') == projected_tree, 'ACTIVATION_NOTIFICATION_READBACK_CHANGED')
    require(owned_receiver(client.request(RECEIVERS_PATH)) == receiver, 'ACTIVATION_NOTIFICATION_READBACK_CHANGED')
    after = client.request(CONFIG_PATH)
    _save(saved, 'notifications-after.json', after)
    _save(saved, 'routingtree-after.json', observed_tree)
    verify_notifications(before, after, task)
    _save(saved, 'notifications-verified.json', _notification_proof(before, after, receiver, projected_tree))
    return after


def create_rule(task, directory, *, client=None):
    directory, client = _directory(directory), client or Client()
    desired = rule_group(task, paused=True)
    if (directory / 'rule-owned.json').exists():
        return _update_rule(task, directory, True, client=client, retarget=True)
    before = {name: client.request(path, missing_ok=True) for name, path in
              [('rule', RULE_PATH), ('folder', FOLDER_PATH), ('group', GROUP_PATH)]}
    _save(directory, 'rule-before.json', before)
    require(all(value is None for value in before.values()), 'ACTIVATION_RULE_COLLISION')
    folder = {'uid': FOLDER_UID, 'title': FOLDER_TITLE}
    _save(directory, 'folder-intent.json', folder)
    response = client.request('/api/folders', method='POST', data=folder)
    _save(directory, 'folder-response.json', response)
    current = client.request(FOLDER_PATH)
    _save(directory, 'folder-owned.json', current)
    require(type(current) is dict and current.get('uid') == FOLDER_UID and current.get('title') == FOLDER_TITLE)
    _save(directory, 'rule-create-intent.json', desired)
    response = client.request(GROUP_PATH, method='PUT', data=desired)
    _save(directory, 'rule-create-response.json', response)
    current = client.request(GROUP_PATH)
    _save(directory, 'rule-create-after.json', current)
    validate_group(current, task, paused=True)
    _save(directory, 'rule-owned.json', current)
    return current


def _update_rule(task, directory, paused, *, client, retarget=False):
    directory, client = _directory(directory), client or Client()
    task_id(task)
    require(type(paused) is bool)
    for update in directory.glob('rule-update-*'):
        S._private(update)
        require(re.fullmatch(r'rule-update-[0-9a-f]{32}', update.name)
                and (update / 'completed.json').is_file(), 'ACTIVATION_RULE_UPDATE_UNKNOWN')
    original = S._read(directory / 'rule-owned.json', MAX_HTTP)
    require(type(original) is dict and type(original.get('rules')) is list and len(original['rules']) == 1)
    previous_paused = original['rules'][0].get('isPaused')
    require(type(previous_paused) is bool)
    previous_task = original['rules'][0].get('labels', {}).get('activation_task')
    validate_group(original, task_id(previous_task), paused=previous_paused)
    require(previous_task == task or retarget and paused, 'ACTIVATION_RULE_OWNER_CHANGED')
    require(client.request(FOLDER_PATH) == S._read(directory / 'folder-owned.json', MAX_HTTP),
            'ACTIVATION_RULE_OWNER_CHANGED')
    current = client.request(GROUP_PATH)
    require(current == original, 'ACTIVATION_RULE_OWNER_CHANGED')
    if not paused:
        _verified_notifications(task, directory, client)
    if paused == previous_paused and previous_task == task:
        return current
    update = directory / ('rule-update-' + uuid.uuid4().hex)
    update.mkdir(mode=0o700)
    desired = rule_group(task, paused=paused)
    _save(update, 'before.json', current)
    _save(update, 'intent.json', desired)
    response = client.request(GROUP_PATH, method='PUT', data=desired)
    _save(update, 'response.json', response)
    after = client.request(GROUP_PATH)
    _save(update, 'after.json', after)
    validate_group(after, task, paused=paused)
    S.N._write(directory / 'rule-owned.json', after, replace=True)
    _save(update, 'completed.json', {'schema_version': 1,
            'after_sha256': hashlib.sha256(S.canonical(after)).hexdigest()})
    return after


def set_paused(task, directory, paused, *, client=None):
    return _update_rule(task, directory, paused, client=client or Client())


def _time(value):
    require(type(value) is str and len(value) <= 64, 'ACTIVATION_STATE_UNVERIFIED')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise GrafanaError('ACTIVATION_STATE_UNVERIFIED') from None
    require(result.tzinfo is not None, 'ACTIVATION_STATE_UNVERIFIED')
    return result.astimezone(timezone.utc)


def observe(payload, task, *, now=None, max_age=45):
    """Only a fresh, healthy, uniquely labelled rule can trigger a diagnostic."""
    owned_labels = labels(task)
    now = datetime.now(timezone.utc) if now is None else now
    require(isinstance(now, datetime) and now.tzinfo is not None and type(max_age) is int and 1 <= max_age <= 120)
    require(type(payload) is dict and payload.get('status') == 'success'
            and type(payload.get('data')) is dict and type(payload['data'].get('groups')) is list,
            'ACTIVATION_STATE_UNVERIFIED')
    matching = []
    for group in payload['data']['groups']:
        require(type(group) is dict and type(group.get('rules')) is list, 'ACTIVATION_STATE_UNVERIFIED')
        for rule in group['rules']:
            require(type(rule) is dict, 'ACTIVATION_STATE_UNVERIFIED')
            rule_labels = rule.get('labels', {})
            require(type(rule_labels) is dict, 'ACTIVATION_STATE_UNVERIFIED')
            if rule_labels.get('activation_rule') == RULE_UID or rule.get('uid') == RULE_UID:
                matching.append((group, rule))
    require(matching, 'ACTIVATION_STATE_NOT_READY')
    require(len(matching) == 1, 'ACTIVATION_STATE_UNVERIFIED')
    group, rule = matching[0]
    require(group.get('name') == GROUP and rule.get('name') == TITLE
            and rule.get('uid', RULE_UID) == RULE_UID and rule.get('type') == 'alerting'
            and type(rule.get('labels')) is dict and all(rule['labels'].get(k) == v for k, v in owned_labels.items()))
    unevaluated = rule.get('lastEvaluation') in (None, '0001-01-01T00:00:00Z')
    if (unevaluated and rule.get('health') in ('ok', 'unknown', '')
            and rule.get('lastError', '') == '' and rule.get('state') in ('inactive', 'pending')):
        raise GrafanaError('ACTIVATION_STATE_NOT_READY')
    require(rule.get('health') == 'ok' and rule.get('lastError', '') == '', 'ACTIVATION_STATE_UNVERIFIED')
    evaluated = _time(rule.get('lastEvaluation'))
    require(-5 <= (now - evaluated).total_seconds() <= max_age, 'ACTIVATION_STATE_STALE')
    # The rule uses Prometheus states, but Grafana 13.2.2 includes individual
    # instances as Normal/Alerting (possibly with a parenthesized reason).
    # An inactive rule may also omit its empty alerts slice.
    alerts = rule.get('alerts', []) if rule.get('state') == 'inactive' else rule.get('alerts')
    require(type(alerts) is list, 'ACTIVATION_STATE_UNVERIFIED')
    result = {'schema_version': 1, 'rule_uid': RULE_UID, 'task_id': task, 'state': 'normal', 'fingerprint': None}
    normal = rule.get('state') == 'inactive'
    if normal and alerts == []:
        return result
    require(normal or rule.get('state') == 'firing', 'ACTIVATION_STATE_NOT_READY')
    require(len(alerts) == 1 and type(alerts[0]) is dict, 'ACTIVATION_STATE_UNVERIFIED')
    alert = alerts[0]
    require(alert.get('state') == ('Normal' if normal else 'Alerting') and type(alert.get('labels')) is dict
            and all(alert['labels'].get(k) == v for k, v in owned_labels.items())
            and alert['labels'].get('alertname') == TITLE, 'ACTIVATION_STATE_UNVERIFIED')
    started = _time(alert.get('activeAt', alert.get('startsAt')))
    require((started - evaluated).total_seconds() <= 5, 'ACTIVATION_STATE_UNVERIFIED')
    if normal:
        return result
    identity = {'rule_uid': RULE_UID, 'task_id': task,
                'startsAt': started.isoformat(timespec='microseconds').replace('+00:00', 'Z')}
    result.update(state='firing', fingerprint=hashlib.sha256(S.canonical(identity)).hexdigest())
    return result
