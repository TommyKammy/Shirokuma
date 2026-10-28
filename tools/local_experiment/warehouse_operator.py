"""Explicit private, one-shot P5 runner. Importing this module has no effects."""
import base64
import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import ssl
import time
from urllib.parse import quote, urlencode, urlsplit
import urllib.request
import urllib.error

import catalog_admin
import database_generation
import local_gitops
import request_auth

STEPS = ('publish_snapshot', 'apply_secrets', 'reconcile_storage', 'reconcile_polaris', 'reconcile_trino')
VERSION_ANNOTATION = 'shirokuma.dev/secret-version'
GROUPS = {'reconcile_storage': ('dev', 'object-storage', 'catalog-database'),
          'reconcile_polaris': ('catalog-bootstrap', 'catalog', 'iceberg-bootstrap'),
          'reconcile_trino': ('trino',)}


def tofu_environment(data_dir):
    return {'env': dict(os.environ, TF_DATA_DIR=str(data_dir))} if data_dir is not None else {}


def _apply_saved_plan(plan_path, state_path, workdir, *, tofu_data_dir=None):
    """Fixed local saved-plan apply leaf; callers own all operation authorization/checks."""
    try:
        result = subprocess.run(['rtk', 'proxy', 'tofu', 'apply', '-input=false', '-no-color',
            '-state='+str(state_path), str(plan_path)], cwd=workdir, capture_output=True,
            text=True, timeout=180, umask=0o077, **tofu_environment(tofu_data_dir))
        if result.returncode:
            raise RuntimeError()
    except Exception:
        raise RuntimeError('Saved ToFu apply outcome unavailable (details withheld; no retry)') from None


def contains(actual, expected):
    """Match generated fields while allowing API-server defaulted dictionary fields."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and contains(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(contains(a, e) for a, e in zip(actual, expected))
    return actual == expected


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def private_file(path):
    path = Path(path).resolve()
    if path.stat().st_mode & 0o777 != 0o600 or path.parent.stat().st_mode & 0o777 != 0o700:
        raise ValueError('Private inputs require mode 0600 in a mode 0700 directory')
    return path.read_bytes()


def save_private(path, value):
    path.parent.mkdir(mode=0o700, exist_ok=True)
    if path.parent.stat().st_mode & 0o777 != 0o700:
        raise ValueError('Private receipt directory permissions')
    temporary = path.with_suffix('.tmp')
    with open(temporary, 'wb', opener=lambda p, flags: os.open(p, flags, 0o600)) as output:
        output.write(canonical(value))
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def validate_bootstrap_receipt(previous, prior, *, require_seal=False):
    """Pure validation against the canonical ledger, never proof of issuance by itself."""
    try:
        binding = previous['binding']
        completion = previous['bootstrap_complete']
        proof = completion['proof']
        fields = {'operation_id', 'binding', 'configuration'}
        sealed = set(proof) == fields | {'provisioning_digest'}
        verified = next(s for s in prior['steps'] if s['step'] == 'verify')
        if (set(proof) not in (fields, fields | {'provisioning_digest'})
                or require_seal and not sealed
                or prior['phase'] != 'bootstrap' or prior['state'] != 'PREPARED'
                or previous['actor'] != prior['actor'] or binding['phase'] != 'bootstrap'
                or binding['predecessor_id'] is not None
                or binding['generation_id'] != prior['generation_id']
                or binding['delivery_digest'] != prior['delivery_digest']
                or proof['operation_id'] != prior['id'] or proof['binding'] != binding
                or proof['configuration']['generation_id'] != prior['generation_id']
                or completion['evidence_ref'] != 'bootstrap-prepared:'+digest(canonical(proof))
                or verified['state'] != 'SUCCEEDED' or verified['evidence_ref'] != completion['evidence_ref']
                or sealed and proof['provisioning_digest'] != digest(canonical(previous['provisioning']))):
            raise ValueError()
        if require_seal:
            provisioning = previous['provisioning']
            if set(provisioning) != set(prior['manifest']['spec']['databases']):
                raise ValueError()
            for key, entry in provisioning.items():
                consumers = entry['consumers']
                if (set(consumers) != {'trino', 'iceberg-bootstrap'}
                        or any(leaf != {'state': 'ACKNOWLEDGED'} for leaf in entry['leaves'].values())):
                    raise ValueError()
                realm = prior['manifest']['spec']['databases'][key]['binding']['polaris_realm']
                for consumer in consumers.values():
                    if (consumer.get('realm') != realm or any(not isinstance(consumer.get(k), str)
                            or not consumer[k].strip() for k in ('client_id', 'client_secret', 'principal_name'))):
                        raise ValueError()
                for field in ('client_id', 'client_secret', 'principal_name'):
                    if len({c[field] for c in consumers.values()}) != 2:
                        raise ValueError()
        return previous
    except (KeyError, TypeError, ValueError, StopIteration):
        raise RuntimeError('Private prepared predecessor does not match its original ledger') from None


class Operator:
    def __init__(self, config_path):
        self.path = Path(config_path).resolve()
        self.config = json.loads(private_file(self.path))
        required = {'version', 'bundle', 'snapshot', 'tofu_plan', 'tofu_workdir', 'tofu_state',
                    'state_lineage', 'state_serial', 'candidate_credentials',
                    'kube_context', 's3_credentials', 'auth_headers'}
        if not required <= set(self.config) or set(self.config) - required - {'phase', 'predecessor_id', 'runtime_profile', 'runtime_endpoint', 'kubeconfig', 'preparation_inputs', 'tofu_data_dir', 'managed_handoff', 'managed_transport'} or self.config['version'] != 1:
            raise ValueError('Invalid private operator config')
        self.phase = self.config.get('phase', 'full')
        self.predecessor_id = self.config.get('predecessor_id')
        if (self.phase not in ('bootstrap', 'full') or
                self.phase == 'bootstrap' and self.predecessor_id is not None):
            raise ValueError('Invalid explicit operation phase')
        if self.config['kube_context'] != local_gitops.CONTEXT:
            raise ValueError('Dedicated context required')
        if (not isinstance(self.config['state_lineage'], str) or not self.config['state_lineage'] or
                type(self.config['state_serial']) is not int or self.config['state_serial'] < 0):
            raise ValueError('Explicit state lineage and serial required')
        for key in required - {'version', 'kube_context', 'state_lineage', 'state_serial'}:
            if not isinstance(self.config[key], str) or not Path(self.config[key]).is_absolute():
                raise ValueError('Explicit absolute input paths required')
        self.bundle = database_generation.load_bundle(self.config['bundle'])
        self.files = {name: data for name, data in self.bundle['files'].items() if name.startswith('deploy/')}
        snapshot = Path(self.config['snapshot'])
        actual = {p.relative_to(snapshot).as_posix(): p.read_bytes() for p in snapshot.rglob('*') if p.is_file()}
        if actual != self.files:
            raise ValueError('Prepared public snapshot must exactly match the generation deploy files')
        if not Path(self.config['tofu_workdir']).is_dir():
            raise ValueError('Explicit ToFu source working directory required')
        if 'tofu_data_dir' in self.config and (not isinstance(self.config['tofu_data_dir'], str)
                or not Path(self.config['tofu_data_dir']).is_absolute()):
            raise ValueError('Explicit absolute ToFu data directory required')
        self.state = self.read_state()
        self.binding = dict(config_path=str(self.path), config=digest(canonical(self.config)),
            tofu_plan=digest(private_file(self.config['tofu_plan'])),
            snapshot=local_gitops.content_digest(actual),
            s3_credentials=digest(private_file(self.config['s3_credentials'])),
            auth_headers=digest(private_file(self.config['auth_headers'])),
            candidate_credentials=digest(private_file(self.config['candidate_credentials'])),
            state_path=self.config['tofu_state'], state_lineage=self.config['state_lineage'],
            state_serial=self.config['state_serial'],
            generation_id=self.bundle['manifest']['generation_id'],
            delivery_digest=self.bundle['manifest']['delivery_digest'],
            phase=self.phase, predecessor_id=self.predecessor_id)
        if 'kubeconfig' in self.config:
            if not Path(self.config['kubeconfig']).is_absolute():
                raise ValueError('Explicit enrolled kubeconfig required')
            self.binding['kubeconfig'] = digest(private_file(self.config['kubeconfig']))
        if 'preparation_inputs' in self.config:
            inputs = self.config['preparation_inputs']
            if not isinstance(inputs, dict) or not inputs or any(not Path(p).is_absolute()
                    or digest(Path(p).read_bytes()) != expected for p,expected in inputs.items()):
                raise ValueError('Sealed preparation inputs changed')
            self.binding['preparation_inputs'] = digest(canonical(inputs))
        candidate = self.candidate_credentials()
        if 'managed_transport' in self.config:
            self.validate_managed_config(candidate)
        if self.phase == 'full':
            self.binding['verification_ca'] = digest(Path(candidate['trino']['ca_file']).read_bytes())
            if 'runtime_profile' in self.config:
                import runtime_profile
                runtime_profile.validate_generation(self.config['runtime_profile'], self.bundle)
                self.binding['runtime_profile'] = runtime_profile.private_binding(self.config['runtime_profile'])
        self.prefix = local_gitops.generation_prefix(self.binding['delivery_digest'])
        self.children = local_gitops.generation_children(self.files,
            self.bundle['manifest']['child_files'], self.prefix)
        for child in self.children:
            child['spec']['suspend'] = False
        self.receipt = None
        self.receipt_path = None
        self.saved_plan = None

    def validate_managed_config(self,candidate):
        import database_creation_bridge as bridge
        transport = bridge.exact(self.config['managed_transport'],{'enrollment_path','endpoints'})
        enrolled = bridge.Configuration(transport['enrollment_path'])
        if self.config.get('preparation_inputs',{}).get(enrolled.path) != enrolled.binding:
            raise ValueError('Pinned enrollment bytes required; replan')
        expected = {e['service_ref']:candidate['databases'][key]['polaris']['endpoint_url']
                    for key,e in self.bundle['tofu']['databases'].items()}
        if transport['endpoints'] != expected or len(set(expected.values())) != len(expected):
            raise ValueError('Canonical per-database sealed transport required')
        for service,endpoint in expected.items():
            url = urlsplit(endpoint)
            if (not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?',service)
                    or url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port):
                raise ValueError('Fixed Polaris transport required')
        if self.phase == 'bootstrap':
            if 'managed_handoff' in self.config or 'runtime_profile' in self.config:
                raise ValueError('Bootstrap cannot activate a runtime')
            return
        handoff = bridge.exact(self.config.get('managed_handoff'),{'enrollment_path','candidate_id','owners','runtime_attestation'})
        if handoff['enrollment_path'] != transport['enrollment_path'] or handoff['candidate_id'] != self.binding['generation_id']:
            raise ValueError('Managed candidate identity differs')
        bridge.exact(handoff['owners'],{'workbench','native'})
        for owner in handoff['owners'].values():
            bridge.exact(owner,{'owner_instance_id','old_runtime_instance_id','candidate_runtime_instance_id'})
            for key,value in owner.items():
                bridge.request_id(value)
            if owner['old_runtime_instance_id'] == owner['candidate_runtime_instance_id']:
                raise ValueError('New immutable runtime identity required')
        import runtime_profile
        if handoff['runtime_attestation'] != runtime_profile.validate_generation(self.config['runtime_profile'],self.bundle):
            raise ValueError('Sealed managed attestation differs')
        profile = runtime_profile.load(self.config['runtime_profile'])
        if (self.config['runtime_endpoint'] != enrolled.value['origins']['workbench']
                or profile.sql_endpoint_url != enrolled.value['origins']['native']
                or candidate['trino']['endpoint_url'] != enrolled.value['origins']['backend']
                or profile.auth_file != enrolled.base.auth_file or profile.client_auth_file != enrolled.base.client_auth_file
                or profile.trino_ca_file != enrolled.base.trino_ca_file or profile.state_dir != enrolled.base.state_dir):
            raise ValueError('Fixed runtime references changed')

    def read_state(self):
        state = json.loads(private_file(self.config['tofu_state']))
        if (state.get('lineage') != self.config['state_lineage'] or
                type(state.get('serial')) is not int or state['serial'] < self.config['state_serial']):
            raise ValueError('Explicit private state identity changed; replan required')
        return state

    def check_state_before_apply(self):
        state = self.read_state()
        if (state['serial'] != self.config['state_serial'] or self.receipt is not None and
                digest(private_file(self.config['tofu_state'])) != self.receipt['state_digest']):
            raise ValueError('Explicit private state changed; replan required')

    def save(self):
        save_private(self.receipt_path, self.receipt)

    def assert_intent(self, plan, step):
        import warehouse_operations as core
        if getattr(self, '_readonly_permit', None) is not None:
            if step != 'verify':
                raise RuntimeError('Read-only verification authority required')
            self.guard_readonly(plan)
            self._active_intent = (plan, step)
            return
        import database_creation_recovery as recovery
        if recovery.active():
            if step != 'verify':
                raise RuntimeError('Recovery is limited to original verify')
            recovery.assert_original(plan)
            self._active_intent = (plan,step)
            return
        if self.receipt is None or step not in self.receipt['dispatches']:
            raise RuntimeError('Private dispatch receipt required')
        current = core.inspect(plan['id'], actor=plan['actor'])
        if not any(row['step'] == step and row['state'] == 'RUNNING' for row in current['steps']):
            raise RuntimeError('Persisted core step intent required')
        self._active_intent = (plan, step)
        import database_creation_auth
        database_creation_auth.record_leaf_intent(plan, 'private-intent:'+digest(canonical(self.receipt)))

    def leaf_intent(self, identity, *, plan=None):
        """Persist this exact send's private intent, then serialize its P5 gate.

        Only a digest reaches the control store. The caller sends after this
        method returns, with no SQLite transaction held over the external call.
        """
        import database_creation_auth
        if self.receipt is None:
            raise RuntimeError('Bound private receipt required')
        if plan is None:
            active = getattr(self, '_active_intent', None)
            plan = active[0] if active else dict(id=self.receipt_path.stem, actor=self.receipt['actor'])
        if getattr(self, '_readonly_permit', None) is not None:
            self.guard_readonly(plan)
            if not (identity.get('kind') == 'verify-http' and identity.get('key') in self.read_auth_identities()
                    or identity.get('kind') == 'sql-post' and identity.get('receipt_key') in self.readonly_sql_keys()):
                raise RuntimeError('Read-only verification leaf required')
        import database_creation_bridge as bridge
        if self.config.get('managed_handoff') or self.config.get('managed_transport'):
            database_creation_auth.assert_leaf(plan)
            bridge.current().require()
        import database_creation_recovery as recovery
        recovery.authorize_leaf(plan,identity,self.receipt)
        self.receipt.setdefault('leaf_intents', []).append(digest(canonical(identity)))
        self.save()
        database_creation_auth.record_leaf_intent(plan, 'private-intent:'+digest(canonical(self.receipt)))

    def post(self, request, *, ca_file=None):
        if (self.receipt is not None and 'claim' in self.receipt
                and getattr(self, '_readonly_permit', None) is None):
            self.guard_readonly_observation()
            saved = self.receipt.get('verify_http', {}).get(self.http_identity(request), {})
            if (request.get_method() != 'POST' or self.http_identity(request) not in self.read_auth_identities()
                    or saved.get('state') != 'ACK'):
                raise RuntimeError('Completed read-only authentication receipt required; HOLD')
            return copy.deepcopy(saved['response'])
        import database_creation_recovery as recovery
        if recovery.observing() and self.phase == 'bootstrap':
            endpoints = self.config.get('managed_transport',{}).get('endpoints',{}).values()
            if request.full_url not in {url+'/api/catalog/v1/oauth/tokens' for url in endpoints}:
                raise RuntimeError('Observation permits only sealed read authentication')
            recovery.guard_observation(self, self._observation_operation)
            return self.http(request,ca_file=ca_file)
        journal = self.receipt.get('verify_http') if self.receipt is not None else None
        import database_creation_auth as auth
        permit = auth._permit.get()
        creation = type(permit) is auth.CreationPermit and permit.phase == 'full'
        if getattr(self,'phase',None) == 'full' and (recovery.observing() or (recovery.active() or creation) and
                journal is not None and journal.get(self.http_identity(request),{}).get('state') == 'ACK'):
            return self.read_auth(request,ca_file=ca_file)
        if journal is not None:
            key = self.http_identity(request)
            saved = journal.get(key)
            if saved is None:
                raise RuntimeError('Verify HTTP leaf was not predeclared')
            if saved['state'] == 'ACK':
                return copy.deepcopy(saved['response'])
            if saved['state'] != 'UNSENT':
                raise RuntimeError('Verify HTTP outcome UNKNOWN; no retry')
            self.leaf_intent(dict(kind='verify-http',key=key))
            saved['state'] = 'INTENT'
            self.save()
            result = self.http(request,ca_file=ca_file)
            saved.update(state='ACK',response=result)
            self.save()
            return result
        self.leaf_intent(dict(kind='http-post', target=request.full_url,
                              payload_digest=digest(request.data or b'')))
        return self.http(request, ca_file=ca_file)

    @staticmethod
    def oauth_expiry(response):
        """A retained JWT ACK must positively state its absolute expiry."""
        try:
            token = response['access_token']
            parts = token.split('.')
            if len(parts) != 3 or not all(parts):
                raise ValueError()
            payload = parts[1]
            value = json.loads(base64.b64decode(payload+'='*(-len(payload)%4),altchars=b'-_',validate=True))['exp']
            if type(value) not in (int,float) or not math.isfinite(value) or value <= 0:
                raise ValueError()
            return value
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            raise RuntimeError('Read authentication expiry unavailable; HOLD') from None

    def read_auth(self, request, *, ca_file=None):
        """One fixed existing-credential exchange per current typed authority.

        This auxiliary journal never rewrites a business HTTP ACK or authorizes
        another business leaf. An unresolved send has no time-based escape.
        """
        import database_creation_recovery as recovery
        try:
            key = self.http_identity(request)
            valid = request.get_method() == 'POST' and key in self.read_auth_identities()
        except (KeyError, TypeError, ValueError, OSError):
            raise RuntimeError('Sealed read authentication ACK required; HOLD') from None
        saved = (self.receipt or {}).get('verify_http',{}).get(key)
        if not valid or saved is None or saved.get('state') != 'ACK':
            raise RuntimeError('Sealed read authentication ACK required; HOLD')
        attempts = self.receipt.get('read_auth',{})
        try:
            recovery.require_read_auth_resolved(self.receipt)
        except Exception:
            raise RuntimeError('Read authentication outcome unresolved; HOLD') from None
        expiry = self.oauth_expiry(saved.get('response'))
        # Even reuse is read-only and fenced by the current authority.
        context = recovery.read_auth_context(self,key)
        if expiry > time.time():
            return copy.deepcopy(saved['response'])
        attempt_key = context+'/'+key
        previous = attempts.get(attempt_key)
        for acknowledged in attempts.values():
            if acknowledged['http_identity'] == key and self.oauth_expiry(acknowledged['response']) > time.time():
                return copy.deepcopy(acknowledged['response'])
        if previous is not None:
            raise RuntimeError('Read authentication approval exhausted; HOLD')
        self.require_owned_transports()
        attempt = dict(state='INTENT',http_identity=key,context=context)
        self.receipt.setdefault('read_auth',{})[attempt_key] = attempt
        self.save()
        # The durable INTENT precedes the final current-authority check and send.
        try:
            if recovery.read_auth_context(self,key) != context:
                raise RuntimeError('Read authentication context changed; HOLD')
            self.require_owned_transports()
        except Exception as error:
            self.read_auth_failure(attempt,'final-guard',error)
            raise
        try:
            result = self.http(request,ca_file=ca_file)
        except Exception as error:
            self.read_auth_failure(attempt,'http',error)
            raise RuntimeError('Read authentication outcome unavailable; HOLD') from None
        try:
            if self.oauth_expiry(result) <= time.time():
                raise RuntimeError('Read authentication approval exhausted; HOLD')
        except Exception as error:
            self.read_auth_failure(attempt,'response-validity',error)
            raise RuntimeError('Read authentication response unavailable; HOLD') from None
        attempt.update(state='ACK',response=copy.deepcopy(result))
        self.save()
        return result

    def read_auth_failure(self, attempt, stage, error):
        """Best effort INTENT metadata; never persist exception args or a response."""
        try:
            from database_creation_worker import _CALLBACK_CODES, _CALLBACK_EXCEPTIONS
            import warehouse_operations as core
            import database_creation_auth as auth
            code = error.code if type(error) in (core.OperationError,auth.AuthorityLost) else None
            exceptions = dict(_CALLBACK_EXCEPTIONS)
            exceptions.update({cls:cls.__name__ for cls in (ConnectionError,TimeoutError,
                urllib.error.URLError,urllib.error.HTTPError)})
            attempt['diagnostic'] = dict(
                stage=stage if stage in ('final-guard','http','response-validity') else 'OTHER',
                exception=exceptions.get(type(error),'OTHER'),
                code=code if type(code) is str and code in _CALLBACK_CODES else None)
            self.save()
        except Exception:
            pass

    def require_owned_transports(self):
        """Only this instance's exact, ready, live fixed forwards permit a new mint."""
        try:
            endpoints = self.config['managed_transport']['endpoints']
            ready = self._ready_transports
            children = self._transports
            if (not endpoints or len(set(endpoints.values())) != len(endpoints)
                    or set(ready) != set(endpoints.values()) or len(children) != len(ready)
                    or len({id(child) for child in children}) != len(children)
                    or {id(child) for owner,child in ready.values()} != {id(child) for child in children}):
                raise ValueError()
            for owner,child in ready.values():
                if owner is not self or child.poll() is not None:
                    raise ValueError()
        except Exception:
            raise RuntimeError('Owned sealed read authentication transports unavailable; HOLD') from None


    @staticmethod
    def http_identity(request):
        return digest(canonical(dict(url=request.full_url,data=(request.data or b'').decode(),
            headers=dict(request.header_items()))))

    def declare_verify(self, plan):
        """Positive UNSENT records precede every managed full verify effect."""
        if not self.config.get('managed_handoff'):
            return
        if 'verify_declaration' in self.receipt:
            if self.receipt['verify_declaration'].get('state') != 'SEALED':
                raise RuntimeError('Incomplete verify declaration requires reconciliation')
            return
        roles = ['data_engineer','bi_viewer']
        if self.bundle['manifest']['spec']['version'] == 2:
            roles.append('admin')
        sql = [k if r=='data_engineer' else k+'/'+r for k in sorted(self.bundle['tofu']['databases']) for r in roles]
        self.receipt['verify_declaration'] = dict(state='DECLARING',operation_id=plan['id'],binding=self.binding,
            sql={k:'UNSENT' for k in sql})
        self.receipt['verify_http'] = {}
        self.declare_activations(plan)
        for key in self.read_auth_identities():
            self.receipt['verify_http'][key] = dict(state='UNSENT')
        self.receipt['verify_declaration']['state'] = 'SEALED'
        self.save()

    def read_auth_identities(self):
        """Derive only the original fixed OAuth requests from sealed material."""
        identities = set()
        candidates = self.candidate_credentials()
        for key,entry in self.bundle['tofu']['databases'].items():
            endpoint = candidates['databases'][key]['polaris']['endpoint_url']
            credentials = []
            for name in ('root_secret','trino_secret','bootstrap_secret'):
                ref = entry[name]
                obj = self.secret_objects[(ref['namespace'],ref['name'])]
                credentials.append({k:base64.b64decode(v,validate=True).decode() for k,v in obj['data'].items()})
            for credential in credentials:
                basic = base64.b64encode((credential['client_id']+':'+credential['client_secret']).encode()).decode()
                request = urllib.request.Request(self.endpoint(endpoint)+'/api/catalog/v1/oauth/tokens',
                    data=urlencode({'grant_type':'client_credentials','scope':'PRINCIPAL_ROLE:ALL'}).encode(),
                    headers={'Authorization':'Basic '+basic,'Polaris-Realm':entry['realm'],
                             'Content-Type':'application/x-www-form-urlencoded'})
                if credential.get('realm') != entry['realm']:
                    raise ValueError('Sealed read authentication realm mismatch')
                identities.add(self.http_identity(request))
        return identities

    def declare_activations(self, plan):
        if 'runtime_activations' in self.receipt:
            return
        import database_creation_bridge as bridge
        import database_creation_worker as worker
        import database_creation_auth as auth
        import database_creation_recovery as recovery
        if recovery.active():
            raise RuntimeError('Original activation declaration required')
        permit = auth._permit.get()
        managed = bridge.current()
        request,root,original,ignored = worker.resolve(managed,permit.approval_request_id)
        worker.seal_activation_plans(managed,request,root,original,self)
        self.receipt['verify_declaration'].update(original_plan_id=original['id'],
            original_request_id=request['id'],original_claim_id=permit.claim_id)
        self.receipt['runtime_activations'] = {role:dict(state='UNSENT',owner=owner,
            leaves={leaf:'UNSENT' for leaf in ('stop','start','readiness')},
            activation_id=worker.activation_id(root,original,self,role))
            for role,owner in self.config['managed_handoff']['owners'].items()}
        self.save()

    def activate_runtimes(self, plan):
        handoff = self.config.get('managed_handoff')
        if not handoff:
            return
        import database_creation_bridge as bridge
        import database_creation_worker as worker
        import database_creation_auth as auth
        managed = bridge.current()
        permit = auth._permit.get()
        import database_creation_recovery as recovery
        rid = recovery.request_id() if recovery.active() else permit.approval_request_id
        request,root,original,ignored = worker.resolve(managed,rid)
        if recovery.active():
            rid = self.receipt['verify_declaration']['original_request_id']
        self.declare_activations(plan)
        for role in ('workbench','native'):
            saved = self.receipt['runtime_activations'][role]
            if saved['state'] == 'ACK':
                continue
            if saved['state'] not in ('UNSENT','PARTIAL'):
                raise RuntimeError('Runtime activation outcome UNKNOWN; observe before recovery')
            self.leaf_intent(dict(kind='activation',role=role),plan=plan)
            saved['state'] = 'INTENT'
            self.save()
            response = managed.ipc(role,'ACTIVATE_CANDIDATE',rid,saved['activation_id'])
            if (response['state'] not in ('ACK','SUCCEEDED','READY','COMPLETE')
                    or response['runtime_instance_id'] != saved['owner']['candidate_runtime_instance_id']
                    or response.get('previous_runtime_instance_id') != saved['owner']['old_runtime_instance_id']):
                raise RuntimeError('Actual runtime activation acknowledgement unavailable')
            self.check_receipt(plan)
            saved = self.receipt['runtime_activations'][role]
            saved.update(state='ACK',response=response)
            self.save()

    def start_transports(self, plan):
        import database_creation_recovery as recovery
        observing = recovery.observing()
        if observing:
            recovery.guard_observation(self, plan)
        transport = self.config.get('managed_transport')
        if not transport:
            return
        if getattr(self,'_transports',None):
            self.require_owned_transports()
            return
        import socket
        import selectors
        import threading
        self._transports = []
        self._ready_transports = {}
        try:
            for service,endpoint in sorted(transport['endpoints'].items()):
                if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?',service):
                    raise ValueError('Canonical Polaris service required')
                url = urlsplit(endpoint)
                if url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port:
                    raise ValueError('Fixed loopback Polaris transport required')
                with socket.socket() as test:
                    test.bind(('127.0.0.1',url.port))  # a listener is a conflict, never adopted
                self.kubectl('get','service',service,'-n','shirokuma-dev','-o','json')
                if observing:
                    recovery.guard_observation(self, plan)
                else:
                    self.leaf_intent(dict(kind='polaris-transport',service_ref=service,endpoint=endpoint),plan=plan)
                child = subprocess.Popen(['kubectl','--kubeconfig',self.config['kubeconfig'],
                    '--context',local_gitops.CONTEXT,'-n','shirokuma-dev','port-forward',
                    'service/'+service,str(url.port)+':8181','--address=127.0.0.1'],
                    stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
                self._transports.append(child)
                with selectors.DefaultSelector() as selector:
                    selector.register(child.stdout,selectors.EVENT_READ)
                    for attempt in range(50):
                        if child.poll() is not None:
                            raise RuntimeError('Known transport failed')
                        if selector.select(.2):
                            line = child.stdout.readline().strip()
                            if line != 'Forwarding from 127.0.0.1:'+str(url.port)+' -> 8181':
                                raise RuntimeError('Known transport acknowledgement differs')
                            break
                    else:
                        raise RuntimeError('Known transport readiness unavailable')
                self._ready_transports[endpoint] = (self,child)
                # Only this owned kubectl's acknowledgement can establish the
                # listener. Drain its ordinary connection notices privately.
                def drain(stream):
                    try:
                        for ignored in stream:
                            pass
                    finally:
                        stream.close()
                threading.Thread(target=drain,args=(child.stdout,),daemon=True).start()
        except Exception:
            self.close_transports()
            raise RuntimeError('Sealed Polaris transport unavailable; no fallback') from None

    def close_transports(self):
        self._ready_transports = {}
        for child in getattr(self,'_transports',[]):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        self._transports = []

    def principal(self):
        headers = json.loads(private_file(self.config['auth_headers']))
        if set(headers) != {'Authorization', 'X-Shirokuma-Role'}:
            raise ValueError('Explicit HTTP authentication headers required')
        principal = request_auth.authenticate_principal(headers)
        catalog_admin.require_admin(principal, headers['X-Shirokuma-Role'])
        return principal

    def authorize(self, actor):
        principal = self.principal()
        import database_creation_auth
        if database_creation_auth.authorize_actor(principal,actor):
            return True
        if principal.principal_id != actor:
            raise PermissionError('Actor changed')
        return True

    def kubectl(self, *args, payload=None):
        enrolled = ['--kubeconfig',self.config['kubeconfig']] if 'kubeconfig' in self.config else []
        return self.command(['rtk', 'proxy', 'kubectl', *enrolled, '--context', local_gitops.CONTEXT,
                             '--request-timeout=60s', *args], payload=payload)

    def command(self, argv, *, payload=None, cwd=None):
        try:
            environment = tofu_environment(self.config.get('tofu_data_dir')) if argv[:3] == ['rtk', 'proxy', 'tofu'] else {}
            result = subprocess.run(argv, input=payload, cwd=cwd, capture_output=True,
                                    text=True, timeout=180, umask=0o077, **environment)
            if result.returncode:
                raise RuntimeError()
            return result.stdout
        except Exception:
            raise RuntimeError('Operator action unavailable; outcome requires trusted observation') from None

    def observe_secrets(self, plan):
        refs = self.bundle['tofu']['bootstrap_secret_refs'] + self.bundle['tofu']['deferred_secret_refs']
        observations = []
        self.secret_objects = {}
        for ref in refs:
            # --ignore-not-found applies only to this named GET; an error is never absence.
            output = self.kubectl('-n', ref['namespace'], 'get', 'secret', ref['name'],
                                  '--ignore-not-found', '-o', 'json')
            obj = json.loads(output) if output.strip() else None
            row = dict(ref, exists=obj is not None, uid=None, resource_version=None, immutable=None)
            if obj is not None:
                meta = obj['metadata']
                if (meta['name'] != ref['name'] or meta['namespace'] != ref['namespace'] or
                        meta.get('annotations', {}).get(VERSION_ANNOTATION) != ref['version'] or
                        set(obj.get('data', {})) != set(ref['keys']) or obj.get('immutable') is not True or
                        not meta.get('uid') or not meta.get('resourceVersion')):
                    raise ValueError('Secret metadata differs; replan required')
                row.update(uid=meta['uid'], resource_version=meta['resourceVersion'], immutable=True)
                self.secret_objects[(ref['namespace'], ref['name'])] = obj
            observations.append(row)
        return observations

    def receipt_for(self, operation_id):
        import control_store
        if not isinstance(operation_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', operation_id):
            raise ValueError('Invalid operation identifier')
        self.receipt_path = control_store.root().resolve() / 'warehouse-operator-receipts' / (operation_id + '.json')
        return self.receipt_path

    def bind(self, operation):
        path = self.receipt_for(operation['id'])
        receipt = dict(binding=self.binding, actor=operation['actor'], dispatches=[], acknowledgements={},
                       state_digest=digest(private_file(self.config['tofu_state'])), children_before={},
                       child_dispatches=[], resource_uids={}, sql={})
        if (operation['generation_id'] != self.binding['generation_id'] or
                operation['delivery_digest'] != self.binding['delivery_digest'] or
                operation.get('phase', 'full') != self.phase or operation.get('predecessor_id') != self.predecessor_id):
            raise ValueError('Operation and private inputs differ')
        path.parent.mkdir(mode=0o700, exist_ok=True)
        if path.parent.stat().st_mode & 0o777 != 0o700:
            raise ValueError('Private receipt directory permissions')
        # Only bind takes this small lock; apply/reconcile retain the existing core gates.
        with open(path.with_suffix('.lock'), 'a', opener=lambda p, flags: os.open(p, flags, 0o600)) as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if path.exists():
                previous = json.loads(private_file(path))
                if previous['binding'] != self.binding or previous['actor'] != operation['actor']:
                    raise ValueError('Private artifacts changed; replan required')
            else:
                self.check_state_before_apply()
                save_private(path, receipt)

    def check_receipt(self, operation):
        self.receipt = json.loads(private_file(self.receipt_for(operation['id'])))
        fresh = Operator(self.path)
        if (self.receipt['binding'] != fresh.binding or self.receipt['binding'] != self.binding or
                self.receipt['actor'] != operation['actor'] or
                operation['generation_id'] != self.binding['generation_id'] or
                operation['delivery_digest'] != self.binding['delivery_digest'] or
                operation.get('phase', 'full') != self.phase or operation.get('predecessor_id') != self.predecessor_id):
            raise ValueError('Private artifacts changed; replan required')
        if 'readonly_recovery' in self.receipt:
            path, child, permit = self.readonly_child(operation)
            if getattr(self, '_readonly_permit', None) is not None:
                if permit != self._readonly_permit:
                    raise RuntimeError('Read-only claim differs; HOLD')
                self.receipt_path = path
                self.guard_readonly(operation)
            elif 'verification_complete' not in child:
                raise RuntimeError('Read-only child incomplete; HOLD')
            self.receipt_path, self.receipt = path, child

    def readonly_sql_keys(self):
        roles = ['data_engineer', 'bi_viewer']
        if self.bundle['manifest']['spec']['version'] == 2:
            roles.append('admin')
        return {key if role == 'data_engineer' else key+'/'+role
                for key in self.bundle['tofu']['databases'] for role in roles}

    def validate_readonly_parent(self, plan, expected_digest):
        self.check_receipt(plan)
        raw = private_file(self.receipt_path)
        if (digest(raw) != expected_digest or self.phase != 'full' or self.predecessor_id is not None
                or self.bundle['manifest'] != plan['manifest']
                or 'managed_handoff' in self.config or 'managed_transport' in self.config
                or self.receipt.get('sql') != {} or 'readonly_recovery' in self.receipt
                or 'verification_complete' in self.receipt or 'verify_declaration' in self.receipt
                or 'read_auth' in self.receipt or 'claim' in self.receipt or 'original' in self.receipt
                or any(self.receipt_path.parent.glob(plan['id']+'-verify-*'))
                or 'verify' not in self.receipt['dispatches']):
            raise RuntimeError('Standalone pre-SQL UNKNOWN receipt required')
        for step in STEPS:
            expected = 'operator-ack:'+digest(canonical(dict(operation_id=plan['id'], binding=self.binding, step=step)))
            saved = next(s for s in plan['steps'] if s['step'] == step)
            if (step not in self.receipt['dispatches'] or self.receipt['acknowledgements'].get(step) != expected
                    or saved['state'] != 'SUCCEEDED' or saved['evidence_ref'] != expected):
                raise RuntimeError('Genuine original mutation acknowledgements required')
        self.check_readonly_material(plan, self.receipt)

    def check_readonly_material(self, plan, baseline):
        fresh = Operator(self.path)
        if fresh.binding != self.binding or baseline['binding'] != self.binding:
            raise RuntimeError('Read-only material changed; HOLD')
        state = self.read_state()
        if baseline.get('state_after') != dict(serial=state['serial'], digest=digest(private_file(self.config['tofu_state']))):
            raise RuntimeError('Original applied state changed; HOLD')
        import warehouse_operations as core
        with core.control_store.connect() as db:
            request = db.execute('SELECT secret_after FROM operation_requests WHERE operation_id=?', (plan['id'],)).fetchone()
            after = json.loads(request['secret_after'])
        if core._observations(self.bundle, baseline.get('secret_after')) != after:
            raise RuntimeError('Original Secret baseline differs; HOLD')
        core._preflight(plan, self.bundle, self.observe_secrets, after=after, require_present=True)

    def readonly_child(self, plan):
        """Paths derive exclusively from a canonical claim and the fixed parent pointer."""
        import warehouse_operations as core
        parent_path = self.receipt_for(plan['id'])
        parent = json.loads(private_file(parent_path))
        pointer = parent['readonly_recovery']
        permit = core.ReadonlyVerifyPermit(**pointer)
        current = core.check_readonly_claim(permit, current=False)
        if (permit.operation_id != plan['id'] or permit.actor != plan['actor']
                or permit.config_digest != self.binding['config'] or self.phase != 'full'
                or self.predecessor_id is not None or 'managed_handoff' in self.config
                or 'managed_transport' in self.config):
            raise RuntimeError('Read-only parent identity differs; HOLD')
        directory = parent_path.parent / (plan['id']+'-verify-'+permit.attempt_id)
        raw = private_file(directory/'parent-before.json')
        before = json.loads(raw)
        if digest(raw) != permit.receipt_digest or parent != dict(before, readonly_recovery=pointer):
            raise RuntimeError('Original parent receipt changed; HOLD')
        path = directory/'receipt.json'
        child = json.loads(private_file(path))
        if (child.get('original') != before or child.get('claim') != pointer
                or digest(canonical(child.get('original_operation'))) != permit.original_operation_digest
                or child['original_operation']['steps'][:-1] != current['steps'][:-1]
                or any(child.get(k) != before[k] for k in ('binding', 'actor', 'state_digest',
                    'children_before', 'child_dispatches', 'resource_uids', 'source_uid', 'state_after', 'secret_after'))):
            raise RuntimeError('Read-only child baseline changed; HOLD')
        return path, child, permit

    def guard_readonly(self, plan):
        import warehouse_operations as core
        permit = self._readonly_permit
        self.authorize(permit.actor)
        core.check_readonly_claim(permit)
        # Selection validates durable bytes without replacing in-flight journal objects.
        previous_path = self.receipt_path
        try:
            path, child, actual = self.readonly_child(plan)
            if actual != permit or path != previous_path:
                raise RuntimeError('Read-only child selection differs; HOLD')
        finally:
            self.receipt_path = previous_path

    def guard_readonly_observation(self):
        import warehouse_operations as core
        permit = core.ReadonlyVerifyPermit(**self.receipt['claim'])
        self.authorize(permit.actor)
        plan = core.check_readonly_claim(permit, current=False)
        previous_path = self.receipt_path
        try:
            path, child, actual = self.readonly_child(plan)
            if (actual != permit or path != previous_path or 'verification_complete' not in child
                    or child['verification_complete'] != self.receipt.get('verification_complete')
                    or child['sql'] != self.receipt['sql']):
                raise RuntimeError('Completed read-only child changed; HOLD')
        finally:
            self.receipt_path = previous_path

    def create_readonly_child(self, plan, permit):
        import warehouse_operations as core
        core.check_readonly_claim(permit)
        parent_path = self.receipt_for(plan['id'])
        raw = private_file(parent_path)
        if digest(raw) != permit.receipt_digest:
            raise RuntimeError('Parent receipt changed after claim; HOLD')
        before = json.loads(raw)
        directory = parent_path.parent / (plan['id']+'-verify-'+permit.attempt_id)
        directory.mkdir(mode=0o700)  # never adopt or replace an existing child
        with open(directory/'parent-before.json', 'xb', opener=lambda p, f: os.open(p, f, 0o600)) as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        child = {k: copy.deepcopy(before[k]) for k in ('binding', 'actor', 'state_digest',
            'children_before', 'child_dispatches', 'resource_uids', 'source_uid', 'state_after', 'secret_after')}
        child.update(original=before, original_operation=copy.deepcopy(plan), claim=permit.__dict__,
                     dispatches=[], acknowledgements={}, sql={},
                     verify_http={key:dict(state='UNSENT') for key in self.read_auth_identities()})
        save_private(directory/'receipt.json', child)
        save_private(parent_path, dict(before, readonly_recovery=permit.__dict__))
        self.receipt_path, self.receipt = directory/'receipt.json', child
        self._readonly_permit = permit

    def upload(self):
        # Reuse the conditional uploader with a client that fences each object,
        # including later objects after an earlier PUT has completed.
        import boto3
        from botocore.config import Config
        if self.bundle != database_generation.render(self.bundle['manifest']['spec']):
            raise ValueError('Invalid public generation')
        files = {name: data for name, data in self.bundle['files'].items() if name.startswith('deploy/')}
        for name, data in files.items():
            local_gitops.checked_path(name)
            local_gitops.validate_content(name, data)
        local_gitops.validate_references(files)
        credentials = self.s3_credentials()
        client = boto3.client('s3', endpoint_url=credentials['endpoint_url'], region_name=credentials['region'],
            aws_access_key_id=credentials['access_key_id'], aws_secret_access_key=credentials['secret_access_key'],
            config=Config(signature_version='s3v4', retries={'max_attempts': 0}, s3={'addressing_style': 'path'}))
        operator = self
        class GuardedClient:
            def put_object(self, **values):
                operator.leaf_intent(dict(kind='snapshot-put', key=values['Key'],
                                           payload_digest=digest(values['Body'])))
                response = client.put_object(**values)
                operator.receipt.setdefault('snapshot_puts', {})[digest(values['Key'].encode())] = {
                    name: response[name] for name in ('ETag', 'VersionId') if name in response}
                operator.save()  # Retain known acknowledgements even if STOP won during the PUT.
                return response

            def get_object(self, **values):
                return client.get_object(**values)
        try:
            local_gitops.upload_files(GuardedClient(), files, self.prefix)
        except Exception as error:
            from warehouse_operations import OperationError
            if isinstance(error, OperationError):
                raise
            raise RuntimeError('Public generation upload outcome unavailable (details withheld)') from None
        finally:
            client.close()

    def s3_credentials(self):
        credentials = json.loads(private_file(self.config['s3_credentials']))
        if set(credentials) != {'endpoint_url', 'region', 'access_key_id', 'secret_access_key'}:
            raise ValueError('Invalid explicit S3 credential input')
        endpoint = urlsplit(credentials['endpoint_url'])
        if endpoint.scheme not in ('http', 'https') or endpoint.hostname not in ('localhost', '127.0.0.1') or endpoint.username or endpoint.password:
            raise ValueError('Explicit loopback S3 endpoint required')
        return credentials

    def validate_saved_plan(self):
        """Inspect the saved bytes; never generate a plan or accept non-Secret mutations."""
        document = json.loads(self.command(['rtk', 'proxy', 'tofu', 'show', '-json',
            self.config['tofu_plan']], cwd=self.config['tofu_workdir']))
        providers = document.get('configuration', {}).get('provider_config', {})
        provider = providers.get('kubernetes', {})
        context = provider.get('expressions', {}).get('config_context', {})
        if context.get('references') == ['var.kube_context']:
            selected = document.get('variables', {}).get('kube_context', {}).get('value')
        else:
            selected = context.get('constant_value')
        if (selected != local_gitops.CONTEXT or
                document.get('variables', {}).get('kube_context', {}).get('value', selected) != local_gitops.CONTEXT):
            raise ValueError('Saved plan does not prove the dedicated Kubernetes context')
        if 'kubeconfig' in self.config:
            expression = provider.get('expressions', {}).get('config_path', {})
            path = document.get('variables', {}).get('kubeconfig_path', {}).get('value')
            if (expression.get('references') != ['var.kubeconfig_path']
                    or path != self.config['kubeconfig']):
                raise ValueError('Saved plan does not prove the enrolled kubeconfig')
        refs = self.bundle['tofu']['bootstrap_secret_refs'] + self.bundle['tofu']['deferred_secret_refs']
        expected = {(r['namespace'], r['name']): r for r in refs}
        if document.get('errored') or document.get('deferred_changes'):
            raise ValueError('Incomplete saved plan')
        for resource in document.get('resource_changes', []):
            change = resource['change']
            if change['actions'] == ['no-op'] or resource.get('mode') == 'data':
                continue
            if (resource.get('type') != 'kubernetes_secret_v1' or
                    resource.get('provider_name') != 'registry.opentofu.org/hashicorp/kubernetes' or
                    resource.get('provider_name') is None or resource.get('provider_config_key', 'kubernetes') != 'kubernetes' or
                    change['actions'] not in (['create'], ['update'], ['delete', 'create'], ['create', 'delete'])):
                raise ValueError('Saved plan contains a non-Secret mutation')
            after = change['after']
            metadata = after['metadata'][0]
            ref = expected.get((metadata['namespace'], metadata['name']))
            if (ref is None or after.get('immutable') is not True or
                    set(after.get('data', {})) != set(ref['keys']) or
                    metadata.get('annotations', {}).get(VERSION_ANNOTATION) != ref['version'] or
                    after.get('binary_data')):
                raise ValueError('Saved Secret plan differs from public references')
            before = change.get('before')
            if before:
                previous = before['metadata'][0]
                same_name = (previous['namespace'], previous['name']) == (metadata['namespace'], metadata['name'])
                if same_name:
                    # Immutable same-name data never changes, including during a metadata update.
                    if change['actions'] != ['update'] or any(before.get(k) != after.get(k) for k in
                            set(before) | set(after) if k not in {'metadata', 'id'}):
                        raise ValueError('Same-name Secret values cannot change')
                    if any(previous.get(k) != metadata.get(k) for k in set(previous) | set(metadata)
                           if k not in {'annotations', 'labels', 'generation', 'resource_version'}):
                        raise ValueError('Secret identity cannot change in place')
                elif (change['actions'] not in (['delete', 'create'], ['create', 'delete']) or
                      previous.get('namespace') != metadata['namespace'] or
                      previous.get('annotations', {}).get(VERSION_ANNOTATION) == ref['version'] or
                      not previous.get('annotations', {}).get(VERSION_ANNOTATION) or
                      not previous.get('name', '').endswith('-'+previous['annotations'][VERSION_ANNOTATION])):
                    raise ValueError('Replacement must name a new versioned Secret')
        self.saved_plan = document
        return digest(canonical(document))

    def get_child(self, desired):
        meta = desired['metadata']
        return json.loads(self.kubectl('-n', meta['namespace'], 'get',
            'kustomization.kustomize.toolkit.fluxcd.io', meta['name'], '-o', 'json'))

    def group(self, step):
        names = GROUPS[step]
        if self.phase == 'bootstrap' and step == 'reconcile_polaris':
            names = ('catalog-bootstrap', 'catalog')
        children = [d for d in self.children if d['metadata']['name'] in
                    {'shirokuma-'+name for name in names}]
        if len(children) != len(names):
            raise ValueError('Generated child group incomplete')
        return children

    def resource(self, kind, namespace, name):
        obj = json.loads(self.kubectl('-n', namespace, 'get', kind, name, '-o', 'json'))
        meta = obj['metadata']
        if (obj['kind'] != kind or meta['namespace'] != namespace or meta['name'] != name or
                not meta.get('uid') or not meta.get('resourceVersion')):
            raise RuntimeError('Resource identity unavailable')
        return obj

    def expected_resource(self, health):
        import yaml
        matches = []
        for name, data in self.files.items():
            if name.endswith(('.yaml', '.yml')):
                for obj in yaml.safe_load_all(data):
                    if (isinstance(obj, dict) and obj.get('kind') == health['kind'] and
                            obj.get('metadata', {}).get('name') == health['name'] and
                            obj['metadata'].get('namespace', health['namespace']) == health['namespace']):
                        matches.append(obj)
        if not matches or any(obj != matches[0] for obj in matches):
            raise ValueError('Unique generated health definition required')
        return copy.deepcopy(matches[0])

    def resolve_configmaps(self, expected, actual):
        """Validate Kustomize's content-named ConfigMaps against the generated file bytes."""
        import posixpath
        import yaml
        if isinstance(expected, dict) and isinstance(actual, dict):
            for key, value in expected.items():
                other = actual.get(key)
                if key == 'configMap' and isinstance(value, dict) and isinstance(other, dict):
                    base, resolved = value.get('name'), other.get('name')
                    if base and resolved and (resolved == base or resolved.startswith(base+'-')):
                        options = []
                        for path, data in self.files.items():
                            if Path(path).name not in ('kustomization.yaml', 'kustomization.yml', 'Kustomization'):
                                continue
                            document = yaml.safe_load(data)
                            for generator in document.get('configMapGenerator', []):
                                if generator['name'] != base:
                                    continue
                                contents = {}
                                for item in generator.get('files', []):
                                    key_name, filename = item.split('=', 1) if '=' in item else (Path(item).name, item)
                                    filename = posixpath.normpath(posixpath.join(posixpath.dirname(path), filename))
                                    contents[key_name] = self.files[filename].decode()
                                options.append(contents)
                        cm = self.resource('ConfigMap', 'shirokuma-dev', resolved)
                        if not options:
                            direct = self.expected_resource(dict(kind='ConfigMap', name=base, namespace='shirokuma-dev'))
                            if resolved != base or not contains(cm['metadata'], direct['metadata']):
                                raise RuntimeError('Generated ConfigMap identity mismatch')
                            options.append(direct['data'])
                        if any(cm.get('data') != option for option in options):
                            raise RuntimeError('Generated ConfigMap content mismatch')
                        value['name'] = resolved
                self.resolve_configmaps(value, other)
        elif isinstance(expected, list) and isinstance(actual, list):
            for e, a in zip(expected, actual):
                self.resolve_configmaps(e, a)

    def observe_health(self, health):
        expected = self.expected_resource(health)
        actual = self.resource(health['kind'], health['namespace'], health['name'])
        meta, status = actual['metadata'], actual.get('status', {})
        identity = '/'.join((health['kind'], health['namespace'], health['name']))
        before_uid = self.receipt['resource_uids'].get(identity)
        if before_uid is not None and meta['uid'] != before_uid:
            raise RuntimeError('Captured health resource identity changed')
        if meta.get('annotations', {}).get(database_generation.ANNOTATION) != self.binding['generation_id']:
            raise RuntimeError('Expected resource generation not observed')
        self.resolve_configmaps(expected, actual)
        for key in ('spec', 'data'):
            if key in expected and not contains(actual.get(key), expected[key]):
                raise RuntimeError('Exact generated resource configuration mismatch')
        if health['kind'] in ('Deployment', 'StatefulSet'):
            replicas = actual['spec'].get('replicas', 1)
            if (replicas < 1 or status.get('observedGeneration') != meta.get('generation') or
                    status.get('readyReplicas', 0) != replicas or
                    health['kind'] == 'Deployment' and status.get('updatedReplicas', 0) != replicas or
                    health['kind'] == 'StatefulSet' and
                    (status.get('currentRevision') != status.get('updateRevision') or not status.get('updateRevision'))):
                raise RuntimeError('Generated workload health not observed')
        elif health['kind'] == 'Job' and not any(c.get('type') == 'Complete' and c.get('status') == 'True'
                                                for c in status.get('conditions', [])):
            raise RuntimeError('Generated job completion not observed')
        return dict(kind=health['kind'], namespace=health['namespace'], name=health['name'], uid=meta['uid'],
                    generation=meta.get('generation'), resource_version=meta['resourceVersion'])

    def observe_child(self, desired, *, health_observations=None):
        """Validate the child and optionally collect this call's strict health observations."""
        actual = self.get_child(desired)
        meta = actual['metadata']
        before = self.receipt['children_before'].get(meta['name']) if self.receipt else None
        spec = copy.deepcopy(actual['spec'])
        # Flux may persist these API defaults; configuration-affecting extras are not authoritative generation fields.
        for key, value in (('force', False), ('deletionPolicy', 'MirrorPrune')):
            if key not in desired['spec'] and spec.get(key) == value:
                spec.pop(key)
        if ('namespace' not in desired['spec']['sourceRef'] and
                spec.get('sourceRef', {}).get('namespace') == desired['metadata']['namespace']):
            spec['sourceRef'].pop('namespace')
        if (not before or meta.get('uid') != before['uid'] or spec != desired['spec'] or
                any(meta.get('annotations', {}).get(k) != v for k, v in desired['metadata'].get('annotations', {}).items()) or
                not any(c.get('type') == 'Ready' and c.get('status') == 'True' and
                        c.get('observedGeneration') == meta['generation']
                        for c in actual.get('status', {}).get('conditions', []))):
            raise RuntimeError('Exact generated child configuration not observed')
        for health in desired['spec'].get('healthChecks', []):
            observation = self.observe_health(health)
            if health_observations is not None:
                health_observations.append(observation)
        return meta['uid']

    def apply_children(self, children, *, plan, step):
        import yaml
        self.assert_intent(plan, step)
        # Configure while suspended, then resume through the guarded Update owner.
        # One fixed apply, then read/wait, never resubmit.
        before = self.receipt['children_before']
        for desired in children:
            current = self.get_child(desired)
            if (current['metadata']['uid'] != before[desired['metadata']['name']]['uid'] or
                    current['spec'].get('suspend') is not True):
                raise RuntimeError('Captured suspended child required')
            for health in desired['spec'].get('healthChecks', []):
                raw = self.kubectl('-n', health['namespace'], 'get', health['kind'], health['name'],
                                   '--ignore-not-found', '-o', 'json')
                current = json.loads(raw) if raw.strip() else None
                identity = '/'.join((health['kind'], health['namespace'], health['name']))
                self.receipt['resource_uids'][identity] = current['metadata']['uid'] if current else None
        self.save()
        suspended_children = copy.deepcopy(children)
        for child in suspended_children:
            child['spec']['suspend'] = True
        self.leaf_intent(dict(kind='children-apply', payload=suspended_children), plan=plan)
        self.kubectl('apply', '--server-side', '--field-manager=shirokuma-local-gitops',
                     '-f', '-', payload=yaml.safe_dump_all(suspended_children))
        for desired in children:
            meta = desired['metadata']
            self.set_child_suspended(meta['name'], False, plan=plan, step=step)
            self.kubectl('-n', meta['namespace'], 'wait', 'kustomization/'+meta['name'],
                         '--for=condition=ready', '--timeout=90s')
            if self.observe_child(desired) != before[meta['name']]['uid']:
                raise RuntimeError('Child identity changed')

    def set_child_suspended(self, name, suspended, *, plan, step):
        """Explicit existing-child control, usable for limited operator acceptance."""
        if type(suspended) is not bool:
            raise ValueError('Boolean suspension required')
        desired = next((d for d in self.children if d['metadata']['name'] == name), None)
        if desired is None:
            raise ValueError('Generated child required')
        self.assert_intent(plan, step)
        current = self.get_child(desired)
        meta = current['metadata']
        captured = self.receipt['children_before'].get(name)
        if captured is None:
            captured = dict(uid=meta['uid'], spec=current['spec'], metadata=meta)
            self.receipt['children_before'][name] = captured
            self.save()
        if current['metadata']['uid'] != captured['uid']:
            raise RuntimeError('Captured child identity changed')
        target = copy.deepcopy(current['spec'])
        target['suspend'] = suspended
        dispatch = name+':'+str(suspended)
        if dispatch in self.receipt['child_dispatches']:
            raise RuntimeError('Child control already dispatched; observe before proceeding')
        if current['spec'].get('suspend', False) is suspended:
            return {'evidence_ref': 'child-observed:'+digest(canonical(dict(name=name, uid=meta['uid'], suspend=suspended)))}
        self.receipt['child_dispatches'].append(dispatch)
        self.save()
        patch = [{'op': 'test', 'path': '/metadata/uid', 'value': meta['uid']},
                 {'op': 'test', 'path': '/metadata/resourceVersion', 'value': meta['resourceVersion']},
                 {'op': 'add', 'path': '/spec/suspend', 'value': suspended}]
        self.leaf_intent(dict(kind='child-suspension', name=name, patch=patch), plan=plan)
        self.kubectl('-n', 'flux-system', 'patch', 'kustomization/'+name, '--type=json',
                     '--field-manager=shirokuma-local-gitops', '-p', json.dumps(patch))
        actual = self.get_child(desired)
        if actual['metadata'].get('uid') != meta['uid'] or actual['spec'] != target:
            raise RuntimeError('Child suspension acknowledgement unavailable')
        return {'evidence_ref': 'child-control:' + digest(canonical(dict(name=name, uid=meta['uid'], suspend=suspended)))}

    def suspend_children(self, plan):
        # Called only inside apply_secrets, after the core has drained admission.
        for child in self.children:
            self.set_child_suspended(child['metadata']['name'], True, plan=plan, step='apply_secrets')

    def restore_children(self, plan, step):
        """Limited existing-spec acceptance: restore only captured suspend values, never candidate specs."""
        self.assert_intent(plan, step)
        for name, before in self.receipt['children_before'].items():
            desired = next(d for d in self.children if d['metadata']['name'] == name)
            actual = self.get_child(desired)
            expected = dict(before['spec'], suspend=True)
            if actual['metadata']['uid'] != before['uid'] or actual['spec'] != expected:
                raise RuntimeError('Captured child configuration changed; no automatic restoration')
            self.set_child_suspended(name, before['spec'].get('suspend', False), plan=plan, step=step)

    def execute_step(self, step, plan):
        if step not in STEPS or self.receipt is None:
            raise ValueError('Bound operation required')
        if step in self.receipt['dispatches']:
            raise RuntimeError('Mutation already dispatched; trusted reconciliation required')
        self.receipt['dispatches'].append(step)
        # A crash on either side of send remains ambiguous. Never infer NOT_EXECUTED.
        save_private(self.receipt_path, self.receipt)
        self.assert_intent(plan, step)
        if step == 'publish_snapshot':
            self.upload()
        elif step == 'apply_secrets':
            self.check_receipt(plan)
            self.check_state_before_apply()
            self.validate_saved_plan()
            self.suspend_children(plan)
            self.check_receipt(plan)
            self.check_state_before_apply()
            self.receipt['tofu_dispatched'] = True
            self.save()
            self.leaf_intent(dict(kind='tofu-apply', binding=self.binding), plan=plan)
            _apply_saved_plan(self.config['tofu_plan'], self.config['tofu_state'], self.config['tofu_workdir'],
                **({'tofu_data_dir': self.config['tofu_data_dir']} if 'tofu_data_dir' in self.config else {}))
            self.state = self.read_state()
            self.receipt['state_after'] = dict(serial=self.state['serial'], digest=digest(private_file(self.config['tofu_state'])))
            self.receipt['secret_after'] = self.observe_secrets(plan)
            self.save()
        else:
            children = self.group(step)
            # Public source prefix is switched only after drain, as part of storage delivery.
            if step == 'reconcile_storage':
                self.switch_source()
            # Apply each dependency only after the previous child and workload
            # have acknowledged the exact generated configuration.
            for child in children:
                self.apply_children([child], plan=plan, step=step)
            if self.phase == 'bootstrap' and step == 'reconcile_polaris':
                self.start_transports(plan)
                self.provision_databases(plan)
        evidence = 'operator-ack:' + digest(canonical(dict(operation_id=plan['id'], binding=self.binding, step=step)))
        self.receipt['acknowledgements'][step] = evidence
        save_private(self.receipt_path, self.receipt)
        return {'evidence_ref': evidence}

    def switch_source(self):
        import yaml
        ref = self.bundle['manifest']['spec']['shared_secret_refs']['gitops_source']
        root = json.loads(self.kubectl('-n', 'flux-system', 'get',
            'kustomization.kustomize.toolkit.fluxcd.io', 'flux-system', '-o', 'json'))
        if root['spec'].get('suspend') is not True:
            raise ValueError('Legacy Flux root must be suspended')
        source = json.loads(self.kubectl('-n', 'flux-system', 'get',
            'bucket.source.toolkit.fluxcd.io', local_gitops.SOURCE, '-o', 'json'))
        self.receipt['source_uid'] = source['metadata']['uid']
        self.save()
        # No template lookup or legacy activate; preserve the existing dedicated source.
        source = {k: source[k] for k in ('apiVersion', 'kind', 'metadata', 'spec')}
        source['metadata'] = {k: source['metadata'][k] for k in ('name', 'namespace')}
        source['spec'].update(prefix=self.prefix, secretRef={'name': ref['name']})
        self.leaf_intent(dict(kind='source-apply', payload=source))
        self.kubectl('apply', '--server-side', '--field-manager=shirokuma-local-gitops',
                     '-f', '-', payload=yaml.safe_dump(source))
        self.kubectl('-n', 'flux-system', 'wait', 'bucket/'+local_gitops.SOURCE,
                     '--for=condition=ready', '--timeout=90s')
        self.observe_source()

    def observe_source(self):
        ref = self.bundle['manifest']['spec']['shared_secret_refs']['gitops_source']
        actual = self.resource('Bucket', 'flux-system', local_gitops.SOURCE)
        if (actual['spec'].get('prefix') != self.prefix or
                actual['spec'].get('secretRef') != {'name': ref['name']} or
                actual['metadata']['uid'] != self.receipt.get('source_uid') or
                not any(c.get('type') == 'Ready' and c.get('status') == 'True' and
                        c.get('observedGeneration') == actual['metadata']['generation']
                        for c in actual.get('status', {}).get('conditions', []))):
            raise RuntimeError('Exact public source configuration not observed')
        return actual['metadata']['uid']

    def candidate_credentials(self):
        value = json.loads(private_file(self.config['candidate_credentials']))
        if self.phase == 'bootstrap':
            if (set(value) != {'version', 'generation_id', 'databases'} or value['version'] != 1
                    or value['generation_id'] != self.binding['generation_id']
                    or set(value['databases']) != set(self.bundle['manifest']['spec']['databases'])):
                raise ValueError('Explicit bootstrap endpoint map required')
            for entry in value['databases'].values():
                if set(entry) != {'polaris'} or set(entry['polaris']) != {'endpoint_url'}:
                    raise ValueError('Invalid bootstrap endpoint map')
                self.endpoint(entry['polaris']['endpoint_url'])
            return value
        if (set(value) != {'version', 'generation_id', 'trino', 'databases'} or value['version'] != 1 or
                value['generation_id'] != self.binding['generation_id'] or
                set(value['databases']) != set(self.bundle['manifest']['spec']['databases']) or
                set(value['trino']) != {'endpoint_url', 'ca_file'}):
            raise ValueError('Explicit candidate verification credentials required')
        self.endpoint(value['trino']['endpoint_url'], tls=True)
        if not Path(value['trino']['ca_file']).is_absolute():
            raise ValueError('Explicit Trino verification CA required')
        identities = json.loads(self.bundle['files']['identity-public.json'])
        for key, entry in value['databases'].items():
            if (set(entry) != {'polaris', 'trino'} or
                    set(entry['polaris']) != {'endpoint_url', 'client_id', 'client_secret'} or
                    set(entry['trino']) != {'username', 'password'} or
                    entry['trino']['username'] != identities['databases'][key]['roles']['data_engineer'] or
                    any(not isinstance(v, str) or not v for v in (*entry['polaris'].values(), *entry['trino'].values()))):
                raise ValueError('Dedicated candidate credentials required')
            self.endpoint(entry['polaris']['endpoint_url'])
        return value

    def prepared_predecessor(self, plan):
        """Validate prior private credentials against their own frozen public ledger."""
        import warehouse_operations as core
        prior = core.inspect(self.predecessor_id, actor=plan['actor'])
        core.validate_prepared_handoff(prior, plan['manifest'])
        previous = json.loads(private_file(core.control_store.root().resolve() /
            'warehouse-operator-receipts' / (prior['id']+'.json')))
        return validate_bootstrap_receipt(previous, prior)

    def provision_databases(self, plan, *, verify_only=False):
        import database_provision
        # Refresh exact immutable objects; credentials never cross the public ledger.
        self.observe_secrets(plan)
        candidates = self.candidate_credentials()
        state = self.receipt.setdefault('provisioning', {})
        prior = None
        if self.predecessor_id is not None:
            previous = self.prepared_predecessor(plan)
            prior = previous.get('provisioning', {})
        for key, entry in self.bundle['tofu']['databases'].items():
            def values(ref):
                obj = self.secret_objects.get((ref['namespace'], ref['name']))
                if obj is None:
                    return None
                return {k: base64.b64decode(v, validate=True).decode() for k, v in obj['data'].items()}
            root = values(entry['root_secret'])
            if root is None or root['realm'] != entry['realm'] or root['client_id'] != 'root':
                raise RuntimeError('Exact root material required for declared database')
            supplied = {}
            for consumer, name in (('trino', 'trino_secret'), ('iceberg-bootstrap', 'bootstrap_secret')):
                supplied_value = values(entry[name])
                if supplied_value is not None:
                    if prior is not None:
                        issued = prior.get(key, {}).get('consumers', {}).get(consumer)
                        if issued is None or any(supplied_value.get(k) != issued.get(k) for k in ('client_id', 'client_secret', 'realm')):
                            raise RuntimeError('Issued predecessor principal identity changed')
                    supplied[consumer] = supplied_value
                elif verify_only and self.phase == 'full':
                    raise RuntimeError('Full consumer material is incomplete')
            request = self.root_api(candidates['databases'][key]['polaris']['endpoint_url'], entry['realm'], root)
            schemas = [s['sql_name'] for s in self.bundle['manifest']['spec']['databases'][key]['schemas']]
            consumers = database_provision.provision(request, key, entry, schemas, state,
                lambda ignored: self.save(), supplied, verify_only=verify_only)
            # Credential validity is independent of clientId/role metadata. OAuth
            # does not issue or rotate a service principal and can be observed safely.
            for credential in consumers.values():
                self.consumer_token(candidates['databases'][key]['polaris']['endpoint_url'], entry['realm'], credential)

    def consumer_token(self, endpoint, realm, credentials):
        origin = self.endpoint(endpoint)
        basic = base64.b64encode((credentials['client_id']+':'+credentials['client_secret']).encode()).decode()
        value = self.post(urllib.request.Request(origin+'/api/catalog/v1/oauth/tokens',
            data=urlencode({'grant_type': 'client_credentials', 'scope': 'PRINCIPAL_ROLE:ALL'}).encode(),
            headers={'Authorization': 'Basic '+basic, 'Polaris-Realm': realm,
                     'Content-Type': 'application/x-www-form-urlencoded'}))
        if not isinstance(value.get('access_token'), str) or not value['access_token']:
            raise RuntimeError('Dedicated consumer credential authentication unavailable')

    def root_api(self, endpoint, realm, credentials):
        import database_provision
        origin = self.endpoint(endpoint)
        basic = base64.b64encode((credentials['client_id']+':'+credentials['client_secret']).encode()).decode()
        token = self.post(urllib.request.Request(origin+'/api/catalog/v1/oauth/tokens',
            data=urlencode({'grant_type': 'client_credentials', 'scope': 'PRINCIPAL_ROLE:ALL'}).encode(),
            headers={'Authorization': 'Basic '+basic, 'Polaris-Realm': realm,
                     'Content-Type': 'application/x-www-form-urlencoded'}))['access_token']
        if not isinstance(token, str) or not token:
            raise RuntimeError('Root token acknowledgement unavailable')
        from database_inventory import _NoRedirect
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        def request(method, path, body=None):
            if getattr(self, '_readonly_permit', None) is not None:
                if method not in ('GET', 'HEAD') or body is not None:
                    raise RuntimeError('Read-only provisioning required')
                self.guard_readonly(plan=self._active_intent[0])
            elif 'claim' in self.receipt:
                if method not in ('GET', 'HEAD') or body is not None:
                    raise RuntimeError('Completed child observation is read only')
                self.guard_readonly_observation()
            import database_creation_recovery as recovery
            if recovery.observing() and self.phase == 'bootstrap':
                recovery.guard_observation(self, self._observation_operation)
                if method not in ('GET','HEAD') or body is not None:
                    raise RuntimeError('Observation is read only')
            if not path.startswith(('/api/management/v1/', '/api/catalog/v1/')):
                raise ValueError('Fixed Polaris API path required')
            data = canonical(body) if body is not None else None
            req = urllib.request.Request(origin+path, method=method, data=data,
                headers={'Authorization': 'Bearer '+token, 'Polaris-Realm': realm, 'Content-Type': 'application/json'})
            if method not in ('GET', 'HEAD'):
                self.leaf_intent(dict(kind='polaris-mutation', method=method, path=path,
                                      payload_digest=digest(data or b'')))
            try:
                with opener.open(req, timeout=20) as response:
                    content = response.read()
                    return json.loads(content) if content else {}
            except urllib.error.HTTPError as error:
                raise database_provision.permissions.APIError(error.code) from None
            except Exception:
                raise RuntimeError('Polaris action outcome unavailable; no retry') from None
        return request

    def verify_runtime_handoff(self, plan):
        import runtime_profile
        import control_store
        import database_material
        path = self.config.get('runtime_profile')
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise RuntimeError('Explicit restarted runtime profile required before full verification')
        expected = runtime_profile.validate_generation(path, self.bundle)
        if self.binding.get('runtime_profile') != runtime_profile.private_binding(path):
            raise RuntimeError('Private runtime inputs changed; fresh plan required')
        profile = runtime_profile.load(path)
        backend = runtime_profile.private_json(profile.trino_auth_file)
        catalog = runtime_profile.private_json(profile.catalog_auth_file)
        candidate = self.candidate_credentials()
        if (Path(profile.state_dir).resolve() != control_store.root().resolve() or
                Path(profile.trino_ca_file).read_bytes() != Path(candidate['trino']['ca_file']).read_bytes()):
            raise RuntimeError('Runtime state or endpoint differs from explicit candidate')
        passwords = {backend['platform_admin']['username']: backend['platform_admin']['password']}
        for key, entry in self.bundle['tofu']['databases'].items():
            obj = self.secret_objects[(entry['trino_secret']['namespace'], entry['trino_secret']['name'])]
            secret = {k: base64.b64decode(v, validate=True).decode() for k, v in obj['data'].items()}
            if any(catalog['databases'][key][k] != secret[k] for k in ('client_id', 'client_secret', 'realm')):
                raise RuntimeError('Runtime Polaris credential differs from applied identity')
            for role in backend['databases'][key]['roles'].values():
                passwords[role['username']] = role['password']
            engineer = backend['databases'][key]['roles']['data_engineer']
            if engineer != candidate['databases'][key]['trino']:
                raise RuntimeError('Runtime backend credential differs from verified candidate')
        ref = self.bundle['tofu']['shared']['trino_auth_secret']
        auth = self.secret_objects[(ref['namespace'], ref['name'])]
        database_material._verify_passwords(base64.b64decode(auth['data']['password.db'], validate=True).decode(), passwords)
        workbench = self.endpoint(self.config.get('runtime_endpoint', ''))
        native = self.endpoint(profile.sql_endpoint_url)
        backend_origin = self.endpoint(candidate['trino']['endpoint_url'], tls=True)
        if len({workbench, native, backend_origin}) != 3:
            raise RuntimeError('Separate Workbench, native SQL and direct backend endpoints required')
        headers = json.loads(private_file(self.config['auth_headers']))
        for origin in (workbench, native):
            observed = self.http(urllib.request.Request(origin+'/api/v1/runtime', headers=headers))
            if observed != expected:
                raise RuntimeError('Both restarted runtimes must match the frozen generation')
        handoff = self.config.get('managed_handoff')
        if handoff:
            import database_creations
            import database_creation_bridge as bridge
            readbacks = {}
            for role,origin in (('workbench',workbench),('native',native)):
                owner = handoff['owners'][role]
                observed = self.http(urllib.request.Request(origin+database_creations.COLLECTION+'/runtime-instance',headers=headers))
                bridge.exact(observed,bridge.INSTANCE)
                if observed != dict(version=1,environment_id=bridge.current().config.enrollment.environment_id,
                        owner_role=role,owner_instance_id=owner['owner_instance_id'],
                        runtime_instance_id=owner['candidate_runtime_instance_id'],runtime_attestation=expected):
                    raise RuntimeError('Actual managed runtime instance differs')
                saved = self.receipt.get('runtime_activations',{}).get(role,{})
                if saved.get('state') != 'ACK' or saved.get('owner') != owner:
                    raise RuntimeError('Private activation acknowledgement unavailable')
                import database_creation_worker as worker
                managed = bridge.current()
                original_request_id = self.receipt['verify_declaration']['original_request_id']
                original_request,root,original,ignored = worker.resolve(managed,original_request_id)
                owner_journal = worker.activation_journal(managed,original_request,root,original,self,role)
                if owner_journal['state'] != 'ACK':
                    raise RuntimeError('Private owner activation completion unavailable')
                for leaf in ('stop','start','readiness'):
                    value = bridge.exact(owner_journal[leaf],{'state','intent_id'})
                    bridge.request_id(value['intent_id'])
                    if value['state'] != 'ACK':
                        raise RuntimeError('Private owner leaf acknowledgement unavailable')
                readbacks[role] = observed
            return readbacks

    @staticmethod
    def endpoint(value, *, tls=False):
        url = urlsplit(value)
        if (url.scheme not in (('https',) if tls else ('http', 'https')) or
                url.hostname not in ('localhost', '127.0.0.1') or url.username or url.password or
                url.path not in ('', '/') or url.query or url.fragment):
            raise ValueError('Explicit loopback verification endpoint required')
        return value.rstrip('/')

    def http(self, request, *, ca_file=None):
        if getattr(self, '_readonly_permit', None) is not None:
            self.guard_readonly(self._active_intent[0])
        elif getattr(self, 'receipt', None) is not None and 'claim' in self.receipt:
            self.guard_readonly_observation()
            if request.get_method() not in ('GET', 'HEAD') or request.data is not None:
                raise RuntimeError('Completed child observation is read only')
        # Same no-proxy/no-redirect boundary as database_inventory; no retry handler.
        from database_inventory import _NoRedirect
        handlers = [urllib.request.ProxyHandler({}), _NoRedirect()]
        if request.full_url.startswith('https:'):
            handlers.append(urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)))
        try:
            with urllib.request.build_opener(*handlers).open(request, timeout=20) as response:
                return json.load(response)
        except Exception:
            raise RuntimeError('Candidate observation unavailable (details withheld)') from None

    def _reconciled_secret_after(self, plan):
        """Bind a fresh core baseline to the original positive Secret observation."""
        import warehouse_operations as core
        database = self.receipt_path.parent.parent / 'jobs.sqlite3'
        if not database.exists():
            return None
        # One SELECT supplies a consistent private snapshot. No connection is
        # retained while inspecting the saved plan or reading Kubernetes. Avoid
        # the control-store writer, including its root/bootstrap side effects.
        db = sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            try:
                row = db.execute('''SELECT o.id,o.actor,r.actor AS request_actor,
                    r.generation_id,g.delivery_digest,g.manifest,r.phase,r.predecessor_id,
                    r.secret_after,s.state,s.evidence_ref
                    FROM operations o JOIN operation_requests r ON r.operation_id=o.id
                    JOIN generation_plans g ON g.generation_id=r.generation_id
                    JOIN operation_steps s ON s.operation_id=o.id AND s.step='apply_secrets'
                    WHERE o.id=?''', (plan['id'],)).fetchone()
            except sqlite3.OperationalError as error:
                # Older stores may predate this private metadata. Other read
                # failures (including corruption/I/O) must remain failures.
                if str(error).startswith(('no such table: ', 'no such column: ')):
                    return None
                raise
            saved = dict(row) if row is not None else None
        finally:
            db.close()
        # Ordinary/legacy receipts still use their initial identities. Only the
        # production observer's proof makes a canonical baseline eligible; once
        # present, every binding and digest below is mandatory.
        if saved is None or not isinstance(saved['evidence_ref'], str) or not saved['evidence_ref'].startswith('secrets-observed:'):
            return None
        try:
            if (self.phase != 'full' or saved is None or
                    self.receipt_path.stem != plan['id'] or
                    self.receipt['binding'] != self.binding or
                    saved['actor'] != self.receipt['actor'] or
                    saved['request_actor'] != saved['actor'] or
                    any(saved[key] != plan.get(key) for key in
                        ('id', 'actor', 'generation_id', 'delivery_digest', 'phase', 'predecessor_id')) or
                    any(saved[key] != self.binding[key] for key in
                        ('generation_id', 'delivery_digest', 'phase', 'predecessor_id')) or
                    json.loads(saved['manifest']) != self.bundle['manifest'] or
                    plan.get('manifest') != self.bundle['manifest'] or
                    saved['state'] != 'SUCCEEDED' or saved['secret_after'] is None):
                raise ValueError()
            observations = core._observations(self.bundle, json.loads(saved['secret_after']))
            baseline = {(r['namespace'], r['name']): r for r in observations}
            self.validate_saved_plan()
            intended = []
            # Keep exactly observe_secret_apply's ordering and selection rules,
            # including no-op Secrets. The digest binds captured UIDs, not merely
            # the existence of a fresh authorized baseline.
            for resource in self.saved_plan.get('resource_changes', []):
                if resource.get('mode') == 'data' or resource.get('type') != 'kubernetes_secret_v1':
                    continue
                after = resource['change'].get('after')
                if not after:
                    continue
                meta = after['metadata'][0]
                key = meta['namespace'], meta['name']
                observed = baseline[key]
                if not observed['exists']:
                    raise ValueError()
                intended.append((key, observed['uid']))
            if not intended or saved['evidence_ref'] != 'secrets-observed:'+digest(canonical(intended)):
                raise ValueError()
            return observations
        except (KeyError, TypeError, ValueError, core.OperationError):
            raise RuntimeError('Bound reconciled Secret baseline unavailable') from None

    def verify_secret_identities(self, plan):
        after_rows = self.receipt.get('secret_after', [])
        if self.phase == 'full' and 'secret_after' not in self.receipt:
            reconciled = self._reconciled_secret_after(plan)
            if reconciled is not None:
                after_rows = reconciled
        observed = self.observe_secrets(plan)
        baseline = {(r['namespace'], r['name']): r for r in plan['secret_observations']}
        after = {(r['namespace'], r['name']): r for r in after_rows}
        for row in observed:
            previous = after.get((row['namespace'], row['name'])) or baseline[(row['namespace'], row['name'])]
            obj = self.secret_objects.get((row['namespace'], row['name']))
            if (not row['exists'] or obj['metadata'].get('annotations', {}).get(database_generation.ANNOTATION) != self.binding['generation_id'] or
                    previous['exists'] and previous['uid'] != row['uid'] or
                    not previous['exists']):
                raise RuntimeError('Exact immutable Secret identity not observed')
        return observed

    def verify_polaris(self, key, credentials):
        entry = self.bundle['tofu']['databases'][key]
        def secret_value(ref, name):
            return base64.b64decode(self.secret_objects[(ref['namespace'], ref['name'])]['data'][name], validate=True).decode()
        if (credentials['client_id'] != secret_value(entry['trino_secret'], 'client_id') or
                credentials['client_secret'] != secret_value(entry['trino_secret'], 'client_secret') or
                secret_value(entry['trino_secret'], 'realm') != entry['realm'] or
                credentials['client_id'] == secret_value(entry['root_secret'], 'client_id')):
            raise ValueError('Dedicated Polaris service principal mismatch')
        origin = self.endpoint(credentials['endpoint_url'])
        basic = base64.b64encode((credentials['client_id']+':'+credentials['client_secret']).encode()).decode()
        token = self.post(urllib.request.Request(origin+'/api/catalog/v1/oauth/tokens',
            data=urlencode({'grant_type': 'client_credentials', 'scope': 'PRINCIPAL_ROLE:ALL'}).encode(),
            headers={'Authorization': 'Basic '+basic, 'Polaris-Realm': entry['realm'],
                     'Content-Type': 'application/x-www-form-urlencoded'}))['access_token']
        if not isinstance(token, str) or not token:
            raise RuntimeError('Polaris credential acknowledgement unavailable')
        headers = {'Authorization': 'Bearer '+token, 'Polaris-Realm': entry['realm']}
        base = origin+'/api/catalog/v1/'+quote(entry['catalog'], safe='')
        catalog = self.http(urllib.request.Request(origin+'/api/management/v1/catalogs/'+quote(entry['catalog'], safe=''),
                                                   headers=headers))
        catalog = catalog.get('catalog', catalog)
        if not contains(catalog, database_generation._catalog(entry)):
            raise RuntimeError('Expected existing Polaris catalog configuration not observed')
        # Access to the exact catalog path with the bound realm/SP proves existing catalog resolution.
        namespaces = self.http(urllib.request.Request(base+'/namespaces', headers=headers))
        expected = {r['sql_name'] for r in self.bundle['manifest']['spec']['databases'][key]['schemas']}
        if expected != {v[0] for v in namespaces.get('namespaces', []) if isinstance(v, list) and len(v) == 1}:
            raise RuntimeError('Expected existing Polaris namespaces not observed')
        for name in sorted(expected):
            value = self.http(urllib.request.Request(base+'/namespaces/'+quote(name, safe=''), headers=headers))
            if value.get('namespace') != [name]:
                raise RuntimeError('Polaris namespace identity mismatch')
        return dict(database_id=key, realm=entry['realm'], catalog=entry['catalog'], namespaces=sorted(expected))

    def verify_trino(self, key, credentials, common, plan, *, role='data_engineer'):
        database = self.bundle['manifest']['spec']['databases'][key]
        expected = {(credentials['username'], database['sql_catalog'], schema['sql_name'])
                    for schema in database['schemas'] if role != 'bi_viewer' or schema['sql_name'] == 'marts'}
        receipt_key = key if role == 'data_engineer' else key + '/' + role
        origin = self.endpoint(common['endpoint_url'], tls=True)
        sql = 'SELECT current_user AS verified_user, catalog_name, schema_name FROM "'+database['sql_catalog'].replace('"', '""')+'".information_schema.schemata'
        basic = base64.b64encode((credentials['username']+':'+credentials['password']).encode()).decode()
        headers = {'Authorization': 'Basic '+basic, 'X-Trino-User': credentials['username'],
                   'X-Trino-Source': 'shirokuma-warehouse-verify/'+plan['id']+'/'+receipt_key}
        saved = self.receipt['sql'].get(receipt_key)
        if saved is None:
            self.assert_intent(plan, 'verify')
            declaration = self.receipt.get('verify_declaration')
            if declaration is not None:
                if declaration['sql'].get(receipt_key) != 'UNSENT':
                    raise RuntimeError('SQL leaf lacks positive unsent proof')
                declaration['sql'][receipt_key] = 'INTENT'
            saved = dict(dispatched=True, rows=[], complete=False)
            self.receipt['sql'][receipt_key] = saved
            self.save()  # No second POST, even if the first response is lost.
            self.leaf_intent(dict(kind='sql-post', receipt_key=receipt_key), plan=plan)
            page = self.http(urllib.request.Request(origin+'/v1/statement', data=sql.encode(),
                headers=dict(headers, **{'Content-Type': 'text/plain; charset=utf-8'})), ca_file=common['ca_file'])
        elif saved.get('complete'):
            if not expected <= {tuple(r) for r in saved['rows']}:
                raise RuntimeError('Saved Trino metadata resolution mismatch')
            return dict(database_id=key, backend_id=saved['backend_id'], schemas=sorted(r[2] for r in expected))
        elif saved.get('next_uri'):
            page = None
        else:
            raise RuntimeError('Trino verify submission outcome UNKNOWN; no retry')
        for _ in range(64):
            if page is None:
                page = self.http(urllib.request.Request(saved['next_uri'], headers=headers), ca_file=common['ca_file'])
            backend_id = page.get('id')
            if (not isinstance(backend_id, str) or not backend_id or page.get('error') or
                    saved.get('backend_id', backend_id) != backend_id):
                raise RuntimeError('Trino metadata statement did not complete successfully')
            saved['backend_id'] = backend_id
            if page.get('data'):
                if [c.get('name') for c in page.get('columns', [])] != ['verified_user', 'catalog_name', 'schema_name']:
                    raise RuntimeError('Trino metadata column mismatch')
                saved['rows'].extend(page['data'])
            next_uri = page.get('nextUri')
            if next_uri:
                url = urlsplit(next_uri)
                if ((url.scheme, url.netloc) != (urlsplit(origin).scheme, urlsplit(origin).netloc) or
                        not url.path.startswith('/v1/statement/') or url.username or url.fragment):
                    raise RuntimeError('Trino verification page origin mismatch')
            saved['next_uri'] = next_uri
            saved['complete'] = not bool(next_uri)
            self.save()
            if saved['complete']:
                if not expected <= {tuple(r) for r in saved['rows']}:
                    raise RuntimeError('Actual Trino Catalog/Schema resolution mismatch')
                return dict(database_id=key, backend_id=backend_id, schemas=sorted(r[2] for r in expected))
            page = None
        raise RuntimeError('Trino metadata statement remains unresolved')

    def verify(self, plan):
        self.check_receipt(plan)
        if 'claim' in self.receipt and getattr(self, '_readonly_permit', None) is None:
            self.authorize(plan['actor'])
            evidence = self.observe_completed_verify(plan)
            self.authorize(plan['actor'])
            return dict(verified=True, evidence_ref=evidence)
        # verify is an external-effect callback too: its read-only SQL has a durable core intent.
        if 'verify' not in self.receipt['dispatches']:
            self.receipt['dispatches'].append('verify')
            self.save()
        self.assert_intent(plan, 'verify')
        credentials = self.candidate_credentials()
        if self.phase == 'bootstrap':
            configuration = self.verification_configuration(plan)
            self.provision_databases(plan, verify_only=True)
            proof = dict(operation_id=plan['id'], binding=self.binding, configuration=configuration,
                         provisioning_digest=digest(canonical(self.receipt['provisioning'])))
            evidence_ref = 'bootstrap-prepared:'+digest(canonical(proof))
            self.receipt['bootstrap_complete'] = dict(proof=proof, evidence_ref=evidence_ref)
            self.save()
            return dict(verified=True, evidence_ref=evidence_ref)
        configuration = self.verification_configuration(plan)
        self.declare_verify(plan)
        self.start_transports(plan)
        self.provision_databases(plan, verify_only=True)
        self.activate_runtimes(plan)
        readbacks = self.verify_runtime_handoff(plan)
        if self.config.get('managed_handoff'):
            self.receipt['runtime_readbacks'] = readbacks
            self.save()
        catalogs, schemas = [], []
        for key, entry in credentials['databases'].items():
            catalogs.append(self.verify_polaris(key, entry['polaris']))
            schemas.append(self.verify_trino(key, entry['trino'], credentials['trino'], plan))
        viewer_schemas = self.verify_viewer_roles(plan, credentials['trino'])
        if self.bundle['manifest']['spec']['version'] == 2:
            admin_schemas = self.verify_admin_roles(plan, credentials['trino'])
        proof = dict(operation_id=plan['id'], binding=self.binding, configuration=configuration,
                     catalogs=catalogs, schemas=schemas, viewer_schemas=viewer_schemas,
                     sql=copy.deepcopy(self.receipt['sql']))
        if self.bundle['manifest']['spec']['version'] == 2:
            proof['admin_schemas'] = admin_schemas
        if self.config.get('managed_handoff'):
            proof['runtime_readbacks'] = readbacks
        evidence_ref = 'generation-verified:'+digest(canonical(proof))
        completion = dict(proof=proof, evidence_ref=evidence_ref)
        if self.receipt.get('verification_complete', completion) != completion:
            raise RuntimeError('Historical verification completion changed; replan required')
        self.receipt['verification_complete'] = completion
        if getattr(self, '_readonly_permit', None) is not None:
            self.verify_runtime_handoff(plan)
            self.check_readonly_material(plan, self.receipt['original'])
            self.guard_readonly(plan)
        self.save()  # Persist completion before returning to the core ledger acknowledgement.
        return dict(verified=True, evidence_ref=evidence_ref)

    def viewer_credentials(self):
        # The generation-pinned private backend manifest is checked by
        # verify_runtime_handoff; no newly granted principal is used here.
        import runtime_profile
        profile = runtime_profile.load(self.config['runtime_profile'])
        backend = runtime_profile.private_json(profile.trino_auth_file)
        return {key: entry['roles']['bi_viewer'] for key, entry in backend['databases'].items()}

    def verify_viewer_roles(self, plan, common):
        return [self.verify_trino(key, credential, common, plan, role='bi_viewer')
                for key, credential in sorted(self.viewer_credentials().items())]

    def admin_credentials(self):
        """Exact scoped identities from the generation-pinned runtime manifest."""
        import runtime_profile
        import trino_auth
        try:
            profile = runtime_profile.load(self.config['runtime_profile'])
            backend = trino_auth.validate_database_config(runtime_profile.private_json(profile.trino_auth_file))
            spec = self.bundle['manifest']['spec']
            if set(backend['databases']) != set(spec['databases']):
                raise ValueError()
            result = {}
            for key, entry in spec['databases'].items():
                database = backend['databases'][key]
                credential = database['roles']['admin']
                if (database['generation_id'] != spec['generation_id'] or
                        database['sql_catalog'] != entry['sql_catalog'] or
                        credential['username'] != trino_auth.scoped_username(key, 'admin')):
                    raise ValueError()
                result[key] = credential
            return result
        except (KeyError, TypeError, ValueError, OSError, RuntimeError):
            raise RuntimeError('Generation-pinned scoped admin credentials unavailable') from None

    def verify_admin_roles(self, plan, common):
        results = [self.verify_trino(key, credential, common, plan, role='admin')
                   for key, credential in sorted(self.admin_credentials().items())]
        # Validate the durable child receipts before producing a completion,
        # including when verify_trino resumes a previously completed statement.
        if results != self.completed_admin_schemas():
            raise RuntimeError('Scoped admin verification does not match durable SQL evidence')
        return results

    def completed_admin_schemas(self):
        """Read-only validation shared by normal completion and reconciliation."""
        result = []
        for key, credential in sorted(self.admin_credentials().items()):
            entry = self.bundle['manifest']['spec']['databases'][key]
            saved = self.receipt['sql'].get(key + '/admin', {})
            expected = {(credential['username'], entry['sql_catalog'], s['sql_name']) for s in entry['schemas']}
            if (saved.get('complete') is not True or saved.get('dispatched') is not True or
                    not isinstance(saved.get('backend_id'), str) or not saved['backend_id'].strip() or
                    saved.get('next_uri') or not expected <= {tuple(r) for r in saved.get('rows', [])}):
                raise RuntimeError('Completed scoped admin verification unavailable')
            result.append(dict(database_id=key, backend_id=saved['backend_id'],
                               schemas=sorted(s['sql_name'] for s in entry['schemas'])))
        return result

    def verification_configuration(self, plan):
        source_uid = self.observe_source()
        children = []
        for child in self.children:
            if self.phase == 'bootstrap' and child['metadata']['name'] in ('shirokuma-trino', 'shirokuma-iceberg-bootstrap'):
                actual = self.get_child(child)
                before = self.receipt['children_before'][child['metadata']['name']]
                if actual['metadata']['uid'] != before['uid'] or actual['spec'] != dict(before['spec'], suspend=True):
                    raise RuntimeError('Consumers must stay suspended through bootstrap')
                continue
            observations = []
            uid = self.observe_child(child, health_observations=observations)
            health = [{k: v for k, v in observation.items() if k != 'resource_version'}
                      for observation in observations]
            children.append(dict(name=child['metadata']['name'], uid=uid, health=health))
        if self.phase == 'full':
            secrets = self.verify_secret_identities(plan)
        else:
            secrets = self.observe_secrets(plan)
            required = {(r['namespace'], r['name']) for r in self.bundle['tofu']['bootstrap_secret_refs']}
            if any(not row['exists'] for row in secrets if (row['namespace'], row['name']) in required):
                raise RuntimeError('Complete bootstrap Secret material required')
            secrets = [r for r in secrets if r['exists']]
        return dict(generation_id=self.binding['generation_id'], source_uid=source_uid,
                    children=children, secrets=secrets)

    def observe_completed_verify(self, plan):
        if 'claim' in self.receipt:
            import warehouse_operations as core
            self.authorize(plan['actor'])
            core.check_readonly_claim(core.ReadonlyVerifyPermit(**self.receipt['claim']), current=False)
            self.check_readonly_material(plan, self.receipt['original'])
        if self.phase == 'bootstrap':
            completion = self.receipt.get('bootstrap_complete')
            expected = dict(operation_id=plan['id'], binding=self.binding,
                            configuration=self.verification_configuration(plan))
            if completion and set(completion['proof']) == set(expected) | {'provisioning_digest'}:
                expected['provisioning_digest'] = digest(canonical(self.receipt['provisioning']))
            if (not completion or completion['proof'] != expected or
                    completion['evidence_ref'] != 'bootstrap-prepared:'+digest(canonical(completion['proof']))):
                raise RuntimeError('Completed bootstrap verification unavailable')
            self.provision_databases(plan, verify_only=True)
            return completion['evidence_ref']
        completion = self.receipt.get('verification_complete')
        if not isinstance(completion, dict):
            raise RuntimeError('No completed verification receipt')
        proof = completion['proof']
        if (proof['operation_id'] != plan['id'] or proof['binding'] != self.binding or
                completion['evidence_ref'] != 'generation-verified:'+digest(canonical(proof)) or
                proof['configuration'] != self.verification_configuration(plan) or
                proof['sql'] != self.receipt['sql']):
            raise RuntimeError('Completed verification identity/configuration changed')
        credentials = self.candidate_credentials()
        expected_catalogs, expected_schemas = [], []
        for key, entry in sorted(self.bundle['manifest']['spec']['databases'].items()):
            saved = self.receipt['sql'].get(key, {})
            expected_rows = {(credentials['databases'][key]['trino']['username'], entry['sql_catalog'], s['sql_name'])
                             for s in entry['schemas']}
            if (saved.get('complete') is not True or not saved.get('dispatched') or not saved.get('backend_id') or
                    saved.get('next_uri') or not expected_rows <= {tuple(r) for r in saved.get('rows', [])}):
                raise RuntimeError('Completed Trino resolution evidence unavailable')
            expected_catalogs.append(dict(database_id=key, realm=entry['binding']['polaris_realm'],
                catalog=entry['binding']['polaris_catalog'], namespaces=sorted(s['sql_name'] for s in entry['schemas'])))
            expected_schemas.append(dict(database_id=key, backend_id=saved['backend_id'],
                schemas=sorted(s['sql_name'] for s in entry['schemas'])))
        if (sorted(proof['catalogs'], key=lambda e: e['database_id']) != expected_catalogs or
                sorted(proof['schemas'], key=lambda e: e['database_id']) != expected_schemas):
            raise RuntimeError('Completed Polaris/Trino proof does not match the bound generation')
        expected_viewers = []
        for key, credential in sorted(self.viewer_credentials().items()):
            saved = self.receipt['sql'].get(key + '/bi_viewer', {})
            expected_row = (credential['username'], self.bundle['manifest']['spec']['databases'][key]['sql_catalog'], 'marts')
            if (saved.get('complete') is not True or not saved.get('dispatched') or
                    not saved.get('backend_id') or saved.get('next_uri') or
                    expected_row not in {tuple(r) for r in saved.get('rows', [])}):
                raise RuntimeError('Completed viewer verification unavailable')
            expected_viewers.append(dict(database_id=key, backend_id=saved['backend_id'], schemas=['marts']))
        if proof.get('viewer_schemas') != expected_viewers:
            raise RuntimeError('Completed viewer proof does not match the bound generation')
        if self.bundle['manifest']['spec']['version'] == 2:
            if proof.get('admin_schemas') != self.completed_admin_schemas():
                raise RuntimeError('Completed scoped admin proof does not match the bound generation')
        local_gitops.observe_public_generation(self.bundle, self.s3_credentials())
        self.provision_databases(plan, verify_only=True)
        readbacks = self.verify_runtime_handoff(plan)
        if self.config.get('managed_handoff') and proof.get('runtime_readbacks') != readbacks:
            raise RuntimeError('Actual managed runtime readbacks differ from completion')
        if 'claim' in self.receipt:
            self.check_readonly_material(plan, self.receipt['original'])
            self.guard_readonly_observation()
        return completion['evidence_ref']

    def reconcile(self, operation, step):
        import warehouse_operations as core
        try:
            self.check_receipt(operation)
        except RuntimeError:
            if 'readonly_recovery' not in json.loads(private_file(self.receipt_for(operation['id']))):
                raise
            return core.ReconciliationEvidence(step, 'UNKNOWN', 'operator-observation:unavailable', 'observed_configuration')
        evidence = None if step == 'verify' else self.receipt['acknowledgements'].get(step)
        observed = None
        if evidence is None:
            try:
                if step not in self.receipt['dispatches']:
                    raise RuntimeError('No recorded step dispatch')
                if step == 'publish_snapshot':
                    observed = local_gitops.observe_public_generation(self.bundle, self.s3_credentials())['evidence_ref']
                elif step == 'apply_secrets':
                    observed = self.observe_secret_apply(operation)
                elif step == 'verify':
                    observed = self.observe_completed_verify(operation)
                elif step in GROUPS:
                    self.observe_source()
                    identities = [(d['metadata']['name'], self.observe_child(d)) for d in self.group(step)]
                    if self.phase == 'bootstrap' and step == 'reconcile_polaris':
                        self.provision_databases(operation, verify_only=True)
                    observed = 'children-observed:'+digest(canonical(identities))
            except Exception:
                # []/404/timeout or incomplete effects prove neither success nor non-dispatch.
                observed = None
        return core.ReconciliationEvidence(step, 'SUCCEEDED' if evidence or observed else 'UNKNOWN',
            evidence or observed or 'operator-observation:unavailable',
            'backend_ack' if evidence else 'observed_configuration')

    def state_secrets(self):
        result = {}
        for resource in self.read_state().get('resources', []):
            if resource.get('mode') != 'managed' or resource.get('type') != 'kubernetes_secret_v1':
                continue
            for instance in resource.get('instances', []):
                value = instance['attributes']
                meta = value['metadata'][0]
                result[(meta['namespace'], meta['name'])] = value
        return result

    def observe_secret_apply(self, plan):
        if not self.receipt.get('tofu_dispatched'):
            raise RuntimeError('ToFu dispatch is not recorded')
        self.validate_saved_plan()
        state = self.state_secrets()
        # The suspend guard is part of this step, not inferred from Secret presence.
        for desired in self.children:
            actual = self.get_child(desired)
            before = self.receipt['children_before'][desired['metadata']['name']]
            if (actual['metadata']['uid'] != before['uid'] or
                    actual['spec'] != dict(before['spec'], suspend=True)):
                raise RuntimeError('Complete child suspension not observed')
        observations = self.observe_secrets(plan)
        intended = []
        for resource in self.saved_plan.get('resource_changes', []):
            if resource.get('mode') == 'data' or resource.get('type') != 'kubernetes_secret_v1':
                continue
            after = resource['change'].get('after')
            if not after:
                continue
            meta = after['metadata'][0]
            key = meta['namespace'], meta['name']
            current = self.secret_objects.get(key)
            recorded = state.get(key)
            if current is None or recorded is None:
                raise RuntimeError('Exact private Secret apply evidence unavailable')
            recorded_meta = recorded['metadata'][0]
            # State UID ties a positive observation to the explicit saved-plan resource.
            if (not recorded_meta.get('uid') or current['metadata']['uid'] != recorded_meta['uid'] or
                    not contains(recorded, {k: v for k, v in after.items() if k not in {'id', 'metadata'}}) or
                    not contains(current['metadata'].get('annotations', {}), meta.get('annotations', {})) or
                    current.get('immutable') is not True):
                raise RuntimeError('Applied Secret identity/configuration mismatch')
            actual_data = {k: base64.b64decode(v, validate=True).decode() for k, v in current['data'].items()}
            if actual_data != after.get('data'):
                raise RuntimeError('Applied Secret material mismatch')
            intended.append((key, recorded_meta['uid']))
        if not intended:
            # A saved no-op may have no resource_changes; require explicit state/material matches.
            raise RuntimeError('Saved plan has no positively observable Secret resources')
        self.receipt['secret_after'] = observations
        self.receipt['state_after'] = dict(serial=self.read_state()['serial'], digest=digest(private_file(self.config['tofu_state'])))
        self.save()
        return 'secrets-observed:'+digest(canonical(intended))


def run(args):
    if args.command == 'inspect-operation':
        return catalog_admin.inspect_from_auth(args.auth_headers, args.operation_id)
    import warehouse_operations as core
    operator = Operator(args.config)
    principal = operator.principal()
    actor = principal.principal_id
    if args.command == 'plan':
        if operator.phase == 'full' and 'runtime_profile' not in operator.binding:
            raise ValueError('Explicit runtime profile must be sealed before full planning')
        if operator.predecessor_id is not None:
            operator.prepared_predecessor(dict(actor=actor, manifest=operator.bundle['manifest']))
        operator.check_state_before_apply()
        operator.validate_saved_plan()
        observations = operator.observe_secrets(None)
        operation = core.create_plan(actor, catalog_admin.PLAN_ROUTE, args.idempotency_key,
            operator.bundle, observations, phase=operator.phase, predecessor_id=operator.predecessor_id)
        operator.bind(operation)
        public_root = os.environ.get('SHIROKUMA_PUBLIC_OBSERVATIONS_ROOT')
        if public_root:
            # This contains descriptors only; HTTP never reads private config or Secret values.
            target = Path(public_root) / (operator.binding['generation_id'] + '.json')
            target.write_bytes(canonical(observations))
        return operation
    if args.command == 'reconcile':
        if getattr(args, 'verify_readonly', False):
            return core.verify_readonly(args.operation_id, actor=actor, expected_revision=args.expected_revision,
                delivery_digest=args.delivery_digest, receipt_digest=args.receipt_digest, operator=operator)
        return core.reconcile_from_observer(args.operation_id, actor=actor,
            observer=lambda current: operator.reconcile(current, args.step), is_admin=True)
    operation = core.inspect(args.operation_id, actor=actor, is_admin=True)
    operator.check_receipt(operation)
    if args.command == 'apply':
        operator.authorize(actor)
        if operator.predecessor_id is not None:
            operator.prepared_predecessor(operation)
        if not any(row['step'] == 'apply_secrets' and row['state'] == 'SUCCEEDED' for row in operation['steps']):
            operator.check_state_before_apply()
        operator.validate_saved_plan()
        return core.apply(operation['id'], actor=actor, expected_revision=args.expected_revision,
            delivery_digest=operator.binding['delivery_digest'], authorize=operator.authorize,
            observe_secrets=operator.observe_secrets, execute_step=operator.execute_step, verify=operator.verify)
    raise ValueError('Unknown operator action')
