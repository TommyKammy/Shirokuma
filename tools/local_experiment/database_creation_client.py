"""Name-only public database creation client; no operator or backend access."""
import argparse
import base64
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request
import uuid

from dataops_client import Client
import runtime_profile

COLLECTION = '/api/v1/admin/database-creations'
ACTIONS = {'plans', 'approve', 'withdraw', 'replan', 'reconcile', 'recover-verify', 'resume'}
PROGRESS = {'PREPARATION_PENDING', 'PLAN_REVIEW_REQUIRED', 'APPLYING',
            'FULL_REVIEW_REQUIRED', 'VERIFYING', 'READY', 'REPLAN_REQUIRED',
            'RECONCILE_REQUIRED', 'STOPPED', 'BLOCKED', 'CREATING', 'UNKNOWN'}
ROLES = {'bi_viewer': 0, 'data_engineer': 1, 'admin': 2}
STEPS = {'publish_snapshot', 'apply_secrets', 'reconcile_storage',
         'reconcile_polaris', 'reconcile_trino', 'verify'}
# These exact status/code pairs are raised before _request in create/_mutate
# (or by their validators/_saved); their transaction is rolled back. Status
# alone, unknown codes, authorization failures and 5xx never prove rejection.
NO_ADMISSION = {400: {'INVALID_REQUEST', 'INVALID_NAME', 'INVALID_INITIAL_GRANTS'},
                409: {'REVISION_CONFLICT', 'STALE_PLAN', 'RECONCILIATION_REQUIRED',
                      'CREATION_BUSY', 'UNAPPLIED_CHANGES', 'IDEMPOTENCY_CONFLICT'}}
ORDINARY = {'create', 'plan', 'approve', 'replan', 'reconcile', 'withdraw', 'resume'}


class Failure(Exception):
    def __init__(self, code, *, no_admission=False):
        self.code = code
        self.no_admission = no_admission


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def profile_references():
    path = Path(os.environ.get('SHIROKUMA_RUNTIME_PROFILE',
                runtime_profile.ROOT / 'var/shirokuma/runtime-profile.json'))
    value = runtime_profile.private_json(path)
    if value.get('mode') not in ('database', 'logical_v1'):
        raise Failure('CLIENT_PROFILE_REQUIRED')
    return SimpleNamespace(client_auth_file=value['client_auth_file'],
                           sql_endpoint_url=value['sql_endpoint_url'])


class CreationClient(Client):
    def __init__(self, profile, origin=None):
        super().__init__(profile, origin or profile.sql_endpoint_url)
        # This is the same real account used by Client's Basic header, not a
        # user-selected account substitute or a backend credential fingerprint.
        userpass = base64.b64decode(self.headers['Authorization'][6:]).decode()
        self.principal = userpass.split(':', 1)[0]

    def request(self, method, path, *, body=None, key=None, params=None, environment=None):
        if params:
            path += '?' + urlencode(params)
        headers = dict(self.headers)
        if key:
            headers['Idempotency-Key'] = key
        if method == 'POST' and environment is not None:
            headers['X-Shirokuma-Environment'] = environment
        raw = None
        if body is not None:
            headers['Content-Type'] = 'application/json'
            raw = canonical(body).encode()
        try:
            with self.opener.open(Request(self.base + path, data=raw,
                    headers=headers, method=method), timeout=15) as response:
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise ValueError()
                length = response.headers.get('Content-Length')
                if length is not None and len(raw) != int(length):
                    raise ValueError()
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError()
                return value
        except HTTPError as error:
            code = 'HTTP_' + str(error.code)
            try:
                value = json.loads(error.read(8192))
                candidate = value.get('error', value.get('code'))
                if isinstance(candidate, str) and re.fullmatch('[A-Z][A-Z0-9_]{0,79}', candidate):
                    code = candidate
            except Exception:
                pass
            raise Failure(code, no_admission=code in NO_ADMISSION.get(error.code, set())) from None
        except Exception:
            raise Failure('RESPONSE_UNCERTAIN' if method == 'POST' else 'HTTP_RESPONSE_FAILED') from None


class Receipts:
    def __init__(self, directory, scope):
        self.root = Path(directory) / hashlib.sha256(canonical(scope).encode()).hexdigest()
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.scope = scope

    def path(self, label):
        return self.root / (label + '.json')

    def read(self, label):
        path = self.path(label)
        def unique(items):
            value = {}
            for key, item in items:
                if key in value:
                    raise ValueError()
                value[key] = item
            return value
        try:
            if not path.exists():
                return None
            value = json.loads(path.read_text(), object_pairs_hook=unique)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, OSError):
            raise Failure('INVALID_CLIENT_RECEIPT') from None

    def save(self, label, value):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=self.root, delete=False) as stream:
                temporary = Path(stream.name)
                os.chmod(temporary, 0o600)
                stream.write(canonical(value))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path(label))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @contextmanager
    def admission(self, label):
        lock = self.root / (label + '.lock')
        try:
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise Failure('CLIENT_ACTION_BUSY') from None
        try:
            os.close(fd)
            yield
        finally:
            lock.unlink(missing_ok=True)

    def mutate(self, client, label, path, body, *, replace_complete=False, metadata=None):
        with self.admission(label):
            saved = self.read(label)
            if saved is not None:
                validate_receipt(saved, self.scope, label)
                if saved['status'] == 'rejected':
                    # Preserve evidence when a later explicit ordinary command
                    # submits corrected input. No correction is sent here.
                    self.save(label + '-rejected-' + saved['key'], saved)
                    saved = None
            if saved is not None:
                if saved['body'] != body or saved['path'] != path:
                    if not replace_complete or saved.get('status') != 'complete':
                        raise Failure('FROZEN_INTENT_CONFLICT')
                    saved = None
                elif saved.get('status') == 'complete':
                    return saved['ack']
                else:
                    # Only the separate retry command authorizes frozen replay.
                    raise Failure('RESPONSE_UNCERTAIN')
            receipt = dict(scope=self.scope, path=path, body=body,
                           key=str(uuid.uuid4()), status='uncertain', metadata=metadata)
            self.save(label, receipt)
            return self.send(client, label, receipt)

    def send(self, client, label, receipt):
        try:
            ack = client.request('POST', receipt['path'], body=receipt['body'], key=receipt['key'],
                                 environment=receipt['scope']['environment'])
            validate_ack(ack, label, receipt['path'])
            if 'ack' in receipt and any(ack.get(k) != receipt['ack'][k] for k in
                    ('request_id', 'creation_id', 'accepted_revision')):
                raise Failure('RESPONSE_UNCERTAIN')
        except Failure as error:
            if error.no_admission:
                receipt.update(status='rejected', rejection=error.code)
                self.save(label, receipt)
                if label.startswith('approve-'):
                    self.path('review-' + label[len('approve-'):]).unlink(missing_ok=True)
            raise
        except Exception:
            raise Failure('RESPONSE_UNCERTAIN') from None
        receipt.update(status='complete', ack={k: ack[k] for k in
            ('request_id', 'creation_id', 'accepted_revision', 'phase', 'action') if k in ack})
        self.save(label, receipt)
        return receipt['ack']

    def replay(self, client, label):
        with self.admission(label):
            receipt = self.read(label)
            if receipt is None:
                raise Failure('NO_FROZEN_INTENT')
            validate_receipt(receipt, self.scope, label)
            if receipt['status'] == 'rejected':
                raise Failure('INTENT_REJECTED')
            # Persist uncertainty before every explicit delivery, including an
            # ACK lookup for an already completed receipt. Never replace key/body.
            receipt['status'] = 'uncertain'
            self.save(label, receipt)
            return self.send(client, label, receipt)


def intent(action, phase=None, recovery=False):
    if action not in ORDINARY or recovery and (action != 'approve' or phase != 'full'):
        raise Failure('INVALID_RETRY_SELECTION')
    if action in ('plan', 'approve', 'replan'):
        if phase not in ('bootstrap', 'full'):
            raise Failure('INVALID_RETRY_SELECTION')
        return action + '-' + phase + ('-recovery' if recovery else '-apply')
    if phase is not None or recovery:
        raise Failure('INVALID_RETRY_SELECTION')
    return action


def receipt_route(label):
    if label in ('create', 'reconcile', 'withdraw', 'resume'):
        return label, None, False, '' if label == 'create' else '/' + label
    match = re.fullmatch('(plan|approve|replan)-(bootstrap|full)-(apply|recovery)', label)
    if not match:
        raise Failure('INVALID_CLIENT_RECEIPT')
    action, phase, purpose = match.groups()
    recovery = purpose == 'recovery'
    if intent(action, phase, recovery) != label:
        raise Failure('INVALID_CLIENT_RECEIPT')
    suffix = '/plans' if action == 'plan' else '/replan' if action == 'replan' else '/phases/' + phase + ('/recover-verify' if recovery else '/approve')
    return action, phase, recovery, suffix


def validate_ack(ack, label, path):
    action, phase, recovery, _ = receipt_route(label)
    expected_action = {'create': 'CREATE', 'plan': 'PREPARE_PLAN', 'approve': 'RECOVER_VERIFY' if recovery else 'APPROVE',
                       'replan': 'REPLAN', 'reconcile': 'RECONCILE', 'withdraw': 'WITHDRAW', 'resume': 'RESUME'}[action]
    if (not isinstance(ack, dict) or type(ack.get('accepted_revision')) is not int or ack['accepted_revision'] < 1
            or any(not isinstance(ack.get(k), str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}', ack[k]) for k in ('request_id', 'creation_id'))
            or action != 'create' and path.split('/')[5] != ack['creation_id']
            or 'phase' in ack and ack['phase'] != phase
            or 'action' in ack and ack['action'] != expected_action):
        raise Failure('RESPONSE_UNCERTAIN')


def validate_receipt(receipt, scope, label):
    try:
        action, phase, recovery, suffix = receipt_route(label)
        if (not isinstance(receipt, dict) or receipt.get('scope') != scope
                or set(receipt) - {'scope', 'path', 'body', 'key', 'status', 'metadata', 'ack', 'reviewed', 'rejection'}
                or receipt.get('status') not in ('uncertain', 'complete', 'rejected')
                or str(uuid.UUID(receipt['key'])) != receipt['key']):
            raise ValueError()
        if receipt['status'] == 'rejected' and receipt.get('rejection') not in set().union(*NO_ADMISSION.values()):
            raise ValueError()
        path, body = receipt['path'], receipt['body']
        if action == 'create':
            if (path != COLLECTION or set(body) != {'sql_catalog', 'display_name', 'description', 'initial_grants'}
                    or body['sql_catalog'] != scope['target'] or not isinstance(body['display_name'], str)
                    or not isinstance(body['description'], str) or not isinstance(body['initial_grants'], list)):
                raise ValueError()
            principals = set()
            for grant in body['initial_grants']:
                if (set(grant) != {'principal_id', 'maximum_role'} or not isinstance(grant['principal_id'], str)
                        or not grant['principal_id'].strip() or grant['principal_id'] in principals or grant['maximum_role'] not in ROLES):
                    raise ValueError()
                principals.add(grant['principal_id'])
        else:
            if not re.fullmatch(re.escape(COLLECTION) + '/[A-Za-z0-9_-]{1,128}' + re.escape(suffix), path):
                raise ValueError()
            fields = {'expected_revision'}
            if action in ('plan', 'replan'):
                fields.add('phase')
                if body.get('phase') != phase:
                    raise ValueError()
            if action == 'approve':
                fields |= {'plan_id', 'operation_id', 'expected_operation_revision', 'delivery_digest'}
                if recovery:
                    fields.add('recovery_digest')
            if set(body) != fields:
                raise ValueError()
            for key, value in body.items():
                if key.startswith('expected_'):
                    if type(value) is not int or value < 1:
                        raise ValueError()
                elif not isinstance(value, str) or not value.strip():
                    raise ValueError()
        if 'ack' in receipt:
            validate_ack(receipt['ack'], label, path)
        if receipt['status'] == 'complete' and 'ack' not in receipt:
            raise ValueError()
        if action in ('plan', 'replan'):
            metadata = receipt['metadata']
            if set(metadata) != {'sequence'} or type(metadata['sequence']) is not int or metadata['sequence'] < 0:
                raise ValueError()
    except Exception:
        raise Failure('INVALID_CLIENT_RECEIPT') from None


def creations(client):
    items, cursor, seen = [], None, set()
    while True:
        result = client.request('GET', COLLECTION, params={'limit': 100, **({'cursor': cursor} if cursor else {})})
        items.extend(result['creations'])
        cursor = result.get('cursor')
        if cursor is None:
            return items
        if not isinstance(cursor, str) or cursor in seen:
            raise Failure('INVALID_PUBLIC_RESPONSE')
        seen.add(cursor)


def resolve(client, name):
    detail = client.request('GET', COLLECTION + '/by-name/' + quote(name, safe=''))
    if (type(detail) is not dict or detail.get('sql_catalog') != name
            or any(not isinstance(detail.get(field), str)
                   or re.fullmatch('[A-Za-z0-9_-]{1,128}', detail[field]) is None
                   for field in ('creation_id', 'database_id'))):
        raise Failure('INVALID_PUBLIC_RESPONSE')
    return detail


def eligible(detail, action):
    if detail.get('progress_code') not in PROGRESS:
        raise Failure('PUBLIC_PROJECTION_REQUIRED')
    if action not in ACTIONS or action not in detail.get('next_actions', []):
        raise Failure('ACTION_NOT_AVAILABLE')
    if detail.get('creation_mode') == 'logical_v1':
        if (action not in {'withdraw', 'reconcile', 'resume'} or detail['progress_code'] == 'STOPPED'
                or detail.get('blocker') == 'PRINCIPAL_STOPPED'
                or detail['progress_code'] == 'UNKNOWN' and action != 'reconcile'
                or action == 'resume' and detail['progress_code'] != 'BLOCKED'):
            raise Failure('ACTION_NOT_AVAILABLE')
    elif action == 'resume':
        raise Failure('ACTION_NOT_AVAILABLE')
    if action in {'plans', 'approve', 'replan', 'recover-verify'} and detail['progress_code'] == 'STOPPED':
        raise Failure('ACTION_NOT_AVAILABLE')


def latest(detail, phase, recovery):
    purpose = 'VERIFY_RECOVERY' if recovery else 'APPLY'
    plans = [p for p in detail.get('plans', []) if p.get('phase') == phase and p.get('purpose') == purpose]
    if not plans:
        return None
    sequence = max(p['sequence'] for p in plans)
    matches = [p for p in plans if p['sequence'] == sequence]
    if len(matches) != 1:
        raise Failure('INVALID_PUBLIC_RESPONSE')
    return matches[0]


def review(detail, plan, recovery):
    summary = plan.get('public_summary', {})
    if (summary.get('code') != ('RECOVERY_READY' if recovery else 'PLAN_READY')
            or summary.get('impact') != ('VERIFY_RECOVERY_LIMITED' if recovery else
                'BOOTSTRAP_MAINTENANCE' if plan['phase'] == 'bootstrap' else 'FULL_RUNTIME_HANDOFF')
            or not isinstance(summary.get('fixed_steps'), list)
            or any(not isinstance(s, str) or s not in STEPS for s in summary['fixed_steps'])):
        raise Failure('PUBLIC_PROJECTION_REQUIRED')
    if plan['state'] != ('UNKNOWN' if recovery else 'AWAITING_APPLY'):
        raise Failure('PLAN_NOT_REVIEWABLE')
    if not recovery and any(s.get('state') in ('UNKNOWN', 'RUNNING') for s in plan.get('steps', [])):
        raise Failure('PLAN_NOT_REVIEWABLE')
    body = dict(expected_revision=detail['revision'], plan_id=plan['plan_id'],
                operation_id=plan['operation_id'], expected_operation_revision=plan['operation_revision'],
                delivery_digest=plan['delivery_digest'])
    if recovery:
        body['recovery_digest'] = plan['recovery_digest']
    if (type(body['expected_revision']) is not int or body['expected_revision'] < 1
            or type(body['expected_operation_revision']) is not int or body['expected_operation_revision'] < 1
            or any(not isinstance(v, str) or not v for k, v in body.items() if not k.startswith('expected_'))):
        raise Failure('INVALID_PUBLIC_RESPONSE')
    return dict(body=body, creation_id=detail['creation_id'], phase=plan['phase'],
                purpose=plan['purpose'], sequence=plan['sequence'], state=plan['state'],
                recovery_digest=plan.get('recovery_digest'),
                summary={k: summary[k] for k in ('code', 'impact', 'fixed_steps')},
                grants=public(detail.get('initial_grants', [])))


# Explicit public field projection: unexpected server fields never reach stdout.
FIELDS = {'creation_id', 'database_id', 'sql_catalog', 'display_name', 'description',
          'requester_id', 'revision', 'lifecycle', 'progress_code', 'initial_grants',
          'principal_id', 'maximum_role', 'desired_role', 'effective_role', 'principals',
          'creations', 'plans', 'plan_id', 'phase', 'purpose', 'sequence', 'operation_id',
          'operation_revision', 'delivery_digest', 'recovery_digest', 'state', 'steps',
          'step', 'public_summary', 'code', 'impact', 'fixed_steps', 'next_actions',
          'request_id', 'accepted_revision', 'action', 'acknowledgment', 'current', 'current_status',
          'blocker', 'access_source', 'creation_mode', 'foundation_id',
          'foundation_revision', 'binding_revision', 'policy_revision', 'warehouse_state'}


def public(value):
    if isinstance(value, dict):
        if ('warehouse_state' in value and (not isinstance(value['warehouse_state'], str)
                or value['warehouse_state'] not in {'ACTIVE', 'STOPPED', 'TRANSITIONING'})):
            raise Failure('PUBLIC_PROJECTION_REQUIRED')
        if ('blocker' in value and value['blocker'] is not None
                and (not isinstance(value['blocker'], str) or value['blocker'] not in
                     {'PRINCIPAL_STOPPED', 'OPERATOR_UNAVAILABLE', 'FOUNDATION_NOT_READY', 'WAREHOUSE_START_REQUIRED',
                      'RECONCILIATION_REQUIRED', 'CREATION_FAILED'})):
            raise Failure('PUBLIC_PROJECTION_REQUIRED')
        if ('access_source' in value and (not isinstance(value['access_source'], str)
                or value['access_source'] not in {'effective_grant', 'platform_admin', 'none'})):
            raise Failure('PUBLIC_PROJECTION_REQUIRED')
        if 'fixed_steps' in value and (not isinstance(value['fixed_steps'], list)
                or any(not isinstance(step, str) or step not in STEPS for step in value['fixed_steps'])):
            raise Failure('PUBLIC_PROJECTION_REQUIRED')
        return {k: public(v) for k, v in value.items() if k in FIELDS}
    if isinstance(value, list):
        return [public(v) for v in value]
    return value


def success(value, message=None):
    value = public(value)
    # Human output includes the same public changes/steps/grants as JSON review.
    return dict(ok=True, data=value, message=message or json.dumps(value, sort_keys=True, ensure_ascii=True))


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest='action', required=True)
    for action in ('create', 'principals', 'list', 'show', 'progress', 'plan', 'approve', 'replan', 'reconcile', 'withdraw', 'resume', 'retry'):
        p = sub.add_parser(action)
        p.add_argument('--base-url')
        p.add_argument('--json', action='store_true')
        if action == 'create':
            p.add_argument('--sql-catalog', required=True)
            p.add_argument('--grant', action='append', default=[])
            p.add_argument('--display-name')
            p.add_argument('--description', default='')
        elif action not in ('principals', 'list'):
            p.add_argument('--database', required=True)
        if action in ('plan', 'approve', 'replan'):
            p.add_argument('--phase', choices=('bootstrap', 'full'), default='bootstrap')
            if action != 'replan':
                p.add_argument('--recovery', action='store_true')
        if action == 'retry':
            p.add_argument('--action', dest='retry_action', choices=sorted(ORDINARY), required=True)
            p.add_argument('--phase', choices=('bootstrap', 'full'))
            p.add_argument('--recovery', action='store_true')
    return root


def main(argv=None, *, profile=None, state_dir=None):
    args = parser().parse_args(argv)
    try:
        return execute(args, profile=profile, state_dir=state_dir)
    except Failure as error:
        return dict(ok=False, error=error.code)
    except Exception:
        return dict(ok=False, error='CLIENT_REQUEST_FAILED')


def execute(args, *, profile=None, state_dir=None):
    action = args.action
    name = getattr(args, 'sql_catalog', getattr(args, 'database', None))
    if name is not None and (not re.fullmatch('[a-z][a-z0-9_]{0,30}', name)
            or name in {'system', 'information_schema', 'tpch', 'tpcds', 'iceberg'}):
        raise Failure('INVALID_NAME')
    recovery = getattr(args, 'recovery', False)
    phase = getattr(args, 'phase', None)
    if recovery and phase != 'full':
        raise Failure('INVALID_RECOVERY_PHASE')
    retry_label = intent(args.retry_action, phase, recovery) if action == 'retry' else None
    if args.base_url is not None:
        url = urlsplit(args.base_url)
        try:
            local = url.hostname == 'localhost' or ipaddress.ip_address(url.hostname).is_loopback
        except ValueError:
            local = False
        if (not local or url.scheme != 'http' or url.username or url.password
                or url.path not in ('', '/') or url.query or url.fragment):
            raise Failure('INVALID_MANAGEMENT_ORIGIN')
    grants = []
    seen = set()
    for grant in getattr(args, 'grant', []):
        parts = grant.split(':')
        if len(parts) != 2 or not parts[0].strip() or parts[1] not in ROLES or parts[0] in seen:
            raise Failure('INVALID_INITIAL_GRANTS')
        seen.add(parts[0])
        grants.append(dict(principal_id=parts[0], maximum_role=parts[1]))
    client = CreationClient(profile or profile_references(), args.base_url)
    if action == 'principals':
        return success(client.request('GET', COLLECTION + '/principals'))
    if action == 'list':
        return success(dict(creations=creations(client)))
    if action in ('show', 'progress'):
        return success(resolve(client, name))
    capability = client.request('GET', COLLECTION + '/capabilities')
    environment = capability.get('environment_id')
    if not isinstance(environment, str) or not environment:
        raise Failure('FOUNDATION_NOT_READY')
    receipts = Receipts(state_dir or Path.home() / '.local/state/shirokuma/database-creation',
                        dict(origin=client.base, environment=environment,
                             principal=client.principal, role=client.role, target=name))
    if action == 'retry':
        # capabilities authenticates the current real requester and supplies the
        # environment scope. Executor availability/next_actions do not gate a
        # saved ACK lookup; the server decides admission if no ACK exists yet.
        ack = receipts.replay(client, retry_label)
        try:
            current = resolve(client, name)
            if current.get('creation_id') != ack['creation_id'] or current.get('sql_catalog') != name:
                raise Failure('ACK_TARGET_MISMATCH')
            if current.get('progress_code') not in PROGRESS:
                raise Failure('PUBLIC_PROJECTION_REQUIRED')
            current = public(current)
        except Exception:
            return success(dict(acknowledgment=ack, current=None, current_status='UNAVAILABLE'),
                           'Historical acknowledgment recovered; current status unavailable. No additional request sent.')
        return success(dict(acknowledgment=ack, current=current, current_status='AVAILABLE'),
                       'Historical acknowledgment recovered; current progress: ' + current['progress_code'] +
                       '. ' + canonical(public(current)))
    if action == 'create':
        if capability.get('available') is not True:
            if capability.get('creation_mode') == 'logical_v1' and capability.get('blocker') == 'WAREHOUSE_START_REQUIRED':
                raise Failure('WAREHOUSE_START_REQUIRED')
            raise Failure('FOUNDATION_NOT_READY')
        if grants:
            accounts = client.request('GET', COLLECTION + '/principals')['principals']
            available = {a['principal_id']: a['maximum_role'] for a in accounts}
            if any(g['principal_id'] not in available or ROLES[g['maximum_role']] > ROLES.get(available[g['principal_id']], -1) for g in grants):
                raise Failure('INVALID_INITIAL_GRANTS')
        body = dict(sql_catalog=name, display_name=args.display_name if args.display_name is not None else name,
                    description=args.description, initial_grants=sorted(grants, key=lambda g: g['principal_id']))
        return success(receipts.mutate(client, 'create', COLLECTION, body), 'Creation accepted; use show to inspect progress.')
    detail = resolve(client, name)
    path = COLLECTION + '/' + quote(detail['creation_id'], safe='')
    label = (phase or 'root') + ('-recovery' if recovery else '-apply')
    plan = latest(detail, phase, recovery) if phase else None
    if action in ('plan', 'replan'):
        if recovery:
            eligible(detail, 'recover-verify')
        elif action == 'replan' or plan is None:
            token = 'replan' if action == 'replan' else 'plans'
            old = receipts.read(action + '-' + label)
            if old is not None:
                validate_receipt(old, receipts.scope, action + '-' + label)
            if old is None or old.get('status') == 'rejected' or action == 'replan' and old.get('status') == 'complete' and old.get('reviewed'):
                eligible(detail, token)
                before = latest(detail, phase, False)
                receipts.mutate(client, action + '-' + label, path + '/' + token,
                    dict(expected_revision=detail['revision'], phase=phase), replace_complete=action == 'replan',
                    metadata=dict(sequence=before['sequence'] if before else 0))
                old = receipts.read(action + '-' + label)
            # A repeated pending/uncertain plan is observation only.
            detail = client.request('GET', path)
            plan = latest(detail, phase, False)
            if plan is not None and plan['sequence'] <= old['metadata']['sequence']:
                plan = None
        if plan is None:
            return success(detail, 'Preparation pending; repeat plan to inspect with GET only.'
                           if detail.get('progress_code') == 'PREPARATION_PENDING' and not detail.get('blocker') else None)
        eligible(detail, 'recover-verify' if recovery else 'approve')
        frozen = review(detail, plan, recovery)
        receipts.save('review-' + label, frozen)
        old = receipts.read(action + '-' + label)
        if old and old.get('status') == 'complete':
            old['reviewed'] = True
            receipts.save(action + '-' + label, old)
        return success(dict(phase=phase, public_summary=plan['public_summary'],
                            initial_grants=detail.get('initial_grants', []), plans=[plan]),
                       'Review pinned: ' + canonical(public(dict(phase=phase, public_summary=plan['public_summary'],
                            initial_grants=detail.get('initial_grants', [])))) )
    if action == 'approve':
        frozen = receipts.read('review-' + label)
        if frozen is None:
            raise Failure('PRIOR_REVIEW_REQUIRED')
        eligible(detail, 'recover-verify' if recovery else 'approve')
        if plan is None or review(detail, plan, recovery) != frozen:
            raise Failure('STALE_REVIEW')
        suffix = '/phases/' + phase + ('/recover-verify' if recovery else '/approve')
        return success(receipts.mutate(client, 'approve-' + label, path + suffix, frozen['body'],
                                       replace_complete=True), 'Approval accepted; use show to inspect progress.')
    eligible(detail, action)
    return success(receipts.mutate(client, action, path + '/' + action,
                   dict(expected_revision=detail['revision']), replace_complete=True))


if __name__ == '__main__':
    # Handled failures intentionally exit zero so the Go runner preserves the
    # sanitized envelope; Go turns ok=false into its own nonzero exit status.
    print(json.dumps(main(), sort_keys=True))
