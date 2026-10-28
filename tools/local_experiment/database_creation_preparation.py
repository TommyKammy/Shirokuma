"""Private P5-2 preparation. No public admission adapter, apply or runtime handoff.

Enrollment is supplied by trusted composition, never by a request payload. The
only per-call input is the canonical request ID and independently authenticated
executor/owner. An interrupted unsealed attempt requires operator reconciliation.
"""
import copy
from dataclasses import dataclass, fields
import fcntl
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import uuid

import catalog_admin
import control_store
import database_creation_auth as auth
import database_creations as creations
import database_generation as generation
import database_material as material
import local_gitops
import request_auth
import warehouse_operations as core
from warehouse_operations import OperationError
from warehouse_operator import (Operator, canonical, digest, private_file,
                                save_private, tofu_environment, validate_bootstrap_receipt)


@dataclass(frozen=True)
class PreparationConfig:
    enrollment: creations.Enrollment
    base_bundle: Path
    base_private: Path
    tofu_source: Path
    tofu_state: Path
    kubeconfig: Path
    auth_headers: Path
    s3_credentials: Path
    output_root: Path
    trino_ca: Path
    trino_endpoint: str
    polaris_endpoints: tuple  # (service_ref, loopback endpoint), fixed at enrollment
    managed_enrollment: str = None
    polaris_port_range: tuple = None

    def __post_init__(self):
        if type(self.enrollment) is not creations.Enrollment:
            raise TypeError('Typed enrollment required')
        for field in fields(self):
            value = getattr(self, field.name)
            if field.type is Path and (type(value) is not type(Path()) or not value.is_absolute()):
                raise ValueError('Explicit absolute enrolled paths required')
        Operator.endpoint(self.trino_endpoint, tls=True)
        if (type(self.polaris_endpoints) is not tuple or not self.polaris_endpoints
                or len(dict(self.polaris_endpoints)) != len(self.polaris_endpoints)):
            raise ValueError('Explicit enrolled Polaris endpoints required')
        for service, endpoint in self.polaris_endpoints:
            if not isinstance(service, str) or not service:
                raise ValueError('Explicit enrolled service required')
            Operator.endpoint(endpoint)
        if self.polaris_port_range is not None and (type(self.polaris_port_range) is not tuple
                or len(self.polaris_port_range) != 2 or any(type(v) is not int for v in self.polaris_port_range)
                or not 1024 <= self.polaris_port_range[0] <= self.polaris_port_range[1] <= 65535):
            raise ValueError('Explicit private transport port range required')


def _tree(directory):
    return {p.relative_to(directory).as_posix(): digest(p.read_bytes())
            for p in sorted(directory.rglob('*')) if p.is_file() and '.terraform' not in p.parts}


def _private_tree(directory):
    directory.chmod(0o700)
    for path in directory.rglob('*'):
        if '.terraform' not in path.parts:
            path.chmod(0o700 if path.is_dir() else 0o600)


def _run(argv, *, cwd=None, input=None, tofu_data_dir=None):
    try:
        result = subprocess.run(['rtk', 'proxy', *argv], cwd=cwd, input=input,
            text=True, capture_output=True, timeout=180, umask=0o077, **tofu_environment(tofu_data_dir))
        if result.returncode:
            raise RuntimeError()
        return result.stdout
    except Exception:
        raise RuntimeError('Private preparation command failed; reconciliation required') from None


def incremental_input(base_bundle, base_private, bundle, database_id):
    """Keep every old private value and verifier; generate only missing local data."""
    material.build_material(base_bundle, base_private, phase='full')
    old = base_bundle['tofu']
    new = bundle['tofu']
    if set(new['databases']) != set(old['databases']) | {database_id} or database_id in old['databases']:
        raise ValueError('Only one new database is permitted')
    result = copy.deepcopy(base_private)
    result['generation_id'] = bundle['manifest']['generation_id']
    values = result['secrets']
    for key, entry in old['databases'].items():
        if new['databases'][key] != entry:
            # generation identity lives outside these metadata entries.
            raise ValueError('Existing database material references changed')
    for role, ref in new['shared'].items():
        if role in material.DERIVED:
            continue
        previous = old['shared'][role]
        data = values.pop(material.ref_identity(previous))
        if role != 'trino_auth_secret' and previous != ref:
            raise ValueError('Shared credentials cannot rotate')
        values[material.ref_identity(ref)] = data
    entry = new['databases'][database_id]
    def put(role, data):
        values[material.ref_identity(entry[role])] = {'data': data}
    put('root_secret', dict(client_id='root',client_secret=secrets.token_urlsafe(48),realm=entry['realm']))
    key = _run(['openssl','genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:2048'])
    public = _run(['openssl','pkey','-pubout'], input=key)
    put('jwt_secret', {'private.pem': key, 'public.pem': public})
    policy = new['s3_policy']
    put('s3_secret', dict(AWS_ACCESS_KEY_ID=secrets.token_hex(16),AWS_SECRET_ACCESS_KEY=secrets.token_urlsafe(48),
        AWS_REGION=policy['region'],S3_REGION=policy['region'],S3_ENDPOINT=policy['endpoint'],
        S3_BUCKET=policy['bucket'],S3_PATH_STYLE='true'))
    identity = json.loads(bundle['files']['identity-public.json'])
    shared = values[material.ref_identity(new['shared']['trino_auth_secret'])]['data']
    password_db = shared['password.db']
    if password_db and not password_db.endswith('\n'):
        password_db += '\n'
    for db in (identity['databases'][database_id],):
        for user in db['roles'].values():
            if user not in result['passwords']:
                password = secrets.token_urlsafe(36)
                line = _run(['htpasswd','-n','-i','-B','-C','10',user], input=password+'\n')
                if line.strip().split(':',1)[0] != user:
                    raise ValueError('Invalid local password verifier')
                password_db += line.strip()+'\n'
                result['passwords'][user] = password
    shared['password.db'] = password_db
    material.build_material(bundle, result, phase='bootstrap')
    return result


def target_only(db, base_spec, spec, root, phase, *, request=None):
    """Reject unrelated desired work, including work already pending at intake."""
    database_id = root['database_id']
    accepted = {g['principal_id']: g['maximum_role'] for g in json.loads(root['intent_json'])['initial_grants']}
    desired = {r['principal_id']: r['maximum_role'] for r in db.execute(
        "SELECT principal_id,maximum_role FROM grants WHERE database_id=? AND maximum_role!='revoked'", (database_id,))}
    if request is not None and request['action'] == 'REPLAN':
        creations._replan_target_grants(db, root)
    elif desired != accepted:
        raise OperationError('UNAPPLIED_CHANGES')
    core._check_registry(db, spec)
    if set(spec['databases']) != set(base_spec['databases']) | {database_id}:
        raise OperationError('UNAPPLIED_CHANGES')
    initial = {}
    if phase == 'full':
        try:
            prior = creations._prepared_bootstrap(db, root, dict(spec=spec))
        except OperationError:
            raise OperationError('UNAPPLIED_CHANGES') from None
        initial = json.loads(db.execute('SELECT initial_database_states FROM operation_requests WHERE operation_id=?',
            (prior['id'],)).fetchone()[0])
    for key, previous in base_spec['databases'].items():
        row = db.execute('SELECT state,revision FROM databases WHERE id=?',(key,)).fetchone()
        if spec['databases'][key] != previous:
            retirement = db.execute('''SELECT r.state,r.revision,o.action,o.state AS operation_state,
                o.expected_revision,o.before_after FROM database_retirements r
                JOIN operations o ON o.id=r.operation_id WHERE r.database_id=?''',(key,)).fetchone()
            # request_retire/reconcile use the non-purge state-only CAS: +1 at
            # admission, +1 at completion. No purge mode exists in this schema.
            # Drain reconciliations may advance the retirement revision past 2.
            if not (spec['databases'][key] == dict(previous,database_revision=previous['database_revision']+2)
                    and row['state'].upper() == 'RETIRED' and row['revision'] == previous['database_revision']+2
                    and retirement is not None and retirement['state'] == 'RETIRED' and retirement['revision'] >= 2
                    and retirement['action'] == 'DATABASE_RETIRE' and retirement['operation_state'] == 'RETIRED'
                    and retirement['expected_revision'] == previous['database_revision']
                    and json.loads(retirement['before_after']) == [['database_id',key]]):
                raise OperationError('UNAPPLIED_CHANGES')
        binding = db.execute('SELECT desired_revision,applied_revision FROM catalog_bindings WHERE database_id=?',(key,)).fetchone()
        allowed = row['state'].upper() in ('READY','RETIRED') or (
            row['state'].upper() == 'RECONCILING' and initial.get(key,'').upper() == 'READY')
        if not allowed or binding['desired_revision'] != binding['applied_revision']:
            raise OperationError('UNAPPLIED_CHANGES')
    expected = [r for r in base_spec['grant_revisions'] if r['database_id'] != database_id]
    actual = [r for r in spec['grant_revisions'] if r['database_id'] != database_id]
    import grant_management
    if actual != expected or grant_management.pending_desired(db, excluding_database=database_id):
        raise OperationError('UNAPPLIED_CHANGES')


class Preparation:
    def __init__(self, config):
        if type(config) is not PreparationConfig:
            raise TypeError('Trusted typed preparation configuration required')
        self.config = config

    def _material_head(self, *, db=None):
        if self.config.managed_enrollment:
            from database_creation_bridge import Configuration
            from database_creation_worker import applied_material
            return applied_material(Configuration(self.config.managed_enrollment),db=db)
        return None

    def _pins(self, *, head=None, db=None):
        c = self.config
        head = head or self._material_head(db=db)
        # Only the enrolled approved source is copied; never reuse a mutable workdir.
        source = {p.name: digest(p.read_bytes()) for p in sorted(c.tofu_source.iterdir())
                  if p.is_file() and (p.suffix in ('.tf','.json') or p.name == '.terraform.lock.hcl')}
        if '.terraform.lock.hcl' not in source or 'main.tf' not in source:
            raise ValueError('Approved ToFu source and provider lock required')
        runtime = c.tofu_source.parents[1]/'deploy/gitops/clusters/local-lite/polaris-runtime-generation.yaml'
        pins = dict(base=_tree(head.bundle if head else c.base_bundle),
            base_private=digest(private_file(head.private if head else c.base_private)),
            source=source, runtime=digest(runtime.read_bytes()), state=digest(private_file(c.tofu_state)),
            kubeconfig=digest(private_file(c.kubeconfig)),auth_headers=digest(private_file(c.auth_headers)),
            s3_credentials=digest(private_file(c.s3_credentials)),ca=digest(c.trino_ca.read_bytes()),
            managed=digest(private_file(c.managed_enrollment)) if c.managed_enrollment else None,
            enrollment=canonical(dict(enrollment=c.enrollment.__dict__,paths={f.name:str(getattr(c,f.name))
                for f in fields(c) if f.name!='enrollment'})).decode())
        if head:
            pins['applied_material'] = head.pin
        return pins

    def _check(self, fence, base, spec, pins):
        with auth.transaction() as db:
            root, request = auth.preparation_guard(db, fence)
            target_only(db, base['manifest']['spec'], spec, root, json.loads(request['payload'])['phase'], request=request)
        if self._pins() != pins:
            raise OperationError('STALE_PLAN')

    def _predecessor(self, db, root, manifest):
        return creations._prepared_bootstrap(db, root, manifest)

    def prepare(self, request_id, executor, *, owner_id):
        c = self.config
        # Canonical opaque ID, no request-provided paths or receipt inputs.
        if str(uuid.UUID(request_id)) != request_id:
            raise ValueError('Canonical request ID required')
        # Validate the independently enrolled private authentication as well as
        # the current executor identity supplied by the canonical owner.
        with auth.transaction() as db:
            auth.current_authority(c.enrollment,c.enrollment.executor_id)
            auth._executor(c.enrollment,executor)
            creations._gate(db,c.enrollment,c.enrollment.executor_id)
        authenticated = request_auth.authenticate_principal(json.loads(private_file(c.auth_headers)))
        auth._executor(c.enrollment,authenticated)
        c.output_root.mkdir(mode=0o700,parents=True,exist_ok=True)
        if c.output_root.stat().st_mode & 0o777 != 0o700:
            raise ValueError('Private output root permissions')
        with open(c.output_root/'preparation.lock','a',opener=lambda p,f:os.open(p,f,0o600)) as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            fence = auth.claim_preparation(c.enrollment,executor,request_id,owner_id=owner_id)
            with control_store.connect() as db:
                request = dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',(request_id,)).fetchone())
                root = dict(creations._root(db,c.enrollment,request['creation_id']))
                linked = db.execute('SELECT id,operation_id,private_ref FROM db_creation_phase_plans WHERE preparation_request_id=?',
                    (request_id,)).fetchone()
            phase = json.loads(request['payload'])['phase']
            directory = c.output_root/root['id']/phase/str(request['requested_sequence'])
            journal_path = directory/'journal.json'
            if linked:
                journal = self._journal(journal_path)
                self._validate_seal(directory,journal,linked['private_ref'])
                with control_store.connect() as db:
                    if root['terminal_outcome'] != 'APPLIED':
                        head = self._material_head(db=db)
                        if journal['pins'].get('applied_material') != (head.pin if head else None):
                            raise OperationError('STALE_PLAN')
                return dict(plan_id=linked['id'],operation_id=linked['operation_id'])
            if fence is None:
                raise OperationError('RECONCILIATION_REQUIRED')
            head = self._material_head()
            base = generation.load_bundle(head.bundle if head else c.base_bundle)
            if directory.exists():
                journal = self._journal(journal_path)
                self._validate_seal(directory,journal,request['private_receipt_ref'])
                bundle = generation.load_bundle(directory/'bundle')
                self._check(fence,base,bundle['manifest']['spec'],journal['pins'])
            else:
                pins = self._pins(head=head)
                generation_id = 'p52-'+digest(request_id.encode())[:24]
                spec = catalog_admin.export_spec(generation_id,base['manifest']['spec'])
                try:
                    self._check(fence,base,spec,pins)
                except OperationError as error:
                    if (type(error) is OperationError and error.code == 'UNAPPLIED_CHANGES'
                            and fence.initial_claim and request['action'] == 'PREPARE_PLAN'
                            and phase == 'bootstrap' and request['requested_sequence'] == 1):
                        auth.complete_preflight_failure(fence,error,directory)
                    raise
                for parent in (c.output_root/root['id'], c.output_root/root['id']/phase):
                    parent.mkdir(mode=0o700,exist_ok=True)
                    if parent.stat().st_mode & 0o777 != 0o700:
                        raise ValueError('Private phase directory permissions')
                directory.mkdir(mode=0o700)
                # Persist before any local key generation or external planner I/O.
                save_private(journal_path,dict(state='STARTED',request_id=request_id))
                with auth.transaction() as db:
                    auth.preparation_guard(db,fence)
                    db.execute('UPDATE db_creation_requests SET private_receipt_ref=? WHERE id=?',
                        ('preparation-start:'+digest(private_file(journal_path)),request_id))
                bundle = generation.render(spec)
                generation.write_bundle(bundle,directory/'bundle')
                self._check(fence,base,spec,pins)
                private = None
                predecessor_id = None
                if phase == 'bootstrap':
                    private = incremental_input(base,json.loads(private_file(head.private if head else c.base_private)),bundle,root['database_id'])
                else:
                    with auth.transaction() as db:
                        auth.preparation_guard(db,fence)
                        prior = self._predecessor(db,root,bundle['manifest'])
                        previous_plan = db.execute('SELECT private_ref,preparation_request_id,sequence FROM db_creation_phase_plans WHERE operation_id=?',
                            (prior['id'],)).fetchone()
                    receipt_path = control_store.root().resolve()/'warehouse-operator-receipts'/(prior['id']+'.json')
                    previous = validate_bootstrap_receipt(json.loads(private_file(receipt_path)),prior,require_seal=True)
                    # Full must reuse the exact local keys/passwords prepared for bootstrap.
                    bootstrap_dir = c.output_root/root['id']/'bootstrap'/str(previous_plan['sequence'])
                    bootstrap_journal = json.loads(private_file(bootstrap_dir/'journal.json'))
                    self._validate_seal(bootstrap_dir,bootstrap_journal,previous_plan['private_ref'])
                    if previous['binding'] != bootstrap_journal['binding']:
                        raise ValueError('Prepared predecessor artifact binding changed')
                    private = json.loads(private_file(bootstrap_dir/'private-input.json'))
                    private['generation_id'] = generation_id
                    for key, entry in bundle['tofu']['databases'].items():
                        for consumer,role in (('trino','trino_secret'),('iceberg-bootstrap','bootstrap_secret')):
                            credential = previous['provisioning'][key]['consumers'][consumer]
                            data = {k:credential[k] for k in ('client_id','client_secret','realm')}
                            ref = material.ref_identity(entry[role])
                            if ref in private['secrets'] and private['secrets'][ref]['data'] != data:
                                raise ValueError('Previously issued credentials changed')
                            private['secrets'][ref] = {'data':data}
                    predecessor_id = prior['id']
                save_private(directory/'private-input.json',private)
                built = material.build_material(bundle,private,phase=phase)
                material.write_material(built,directory/'material')
                managed_inputs = {}
                if c.managed_enrollment and phase == 'full':
                    import runtime_profile
                    from database_creation_bridge import Configuration, current
                    managed = current()
                    managed.require()
                    enrolled = Configuration(c.managed_enrollment)
                    if enrolled.enrollment != c.enrollment:
                        raise ValueError('Managed enrollment changed')
                    # Keep account auth, client credential, CA, state and normal SQL
                    # origin. Only the newly built full backend/catalog references change.
                    runtime_profile.create_candidate(head.profile,directory/'runtime-profile.json',
                        backend=directory/'material/backend.json',catalog=directory/'material/catalog-auth.json')
                    attestation = runtime_profile.validate_generation(directory/'runtime-profile.json',bundle)
                    owners = {}
                    for role in ('workbench','native'):
                        record = enrolled.owner(role)
                        owners[role] = dict(owner_instance_id=record['owner_instance_id'],
                            old_runtime_instance_id=record['runtime_instance_id'],
                            candidate_runtime_instance_id=str(uuid.uuid4()))
                    managed_inputs = dict(runtime_profile=str(directory/'runtime-profile.json'),
                        runtime_endpoint=enrolled.value['origins']['workbench'],
                        managed_handoff=dict(enrollment_path=c.managed_enrollment,
                            candidate_id=generation_id,owners=owners,runtime_attestation=attestation))
                snapshot = directory/'snapshot'
                for name,data in bundle['files'].items():
                    if name.startswith('deploy/'):
                        path = snapshot/name
                        path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                        path.write_bytes(data)
                workdir = directory/'work/opentofu/dev'
                workdir.mkdir(mode=0o700,parents=True)
                for name in pins['source']:
                    shutil.copyfile(c.tofu_source/name,workdir/name)
                runtime = workdir.parents[1]/'deploy/gitops/clusters/local-lite/polaris-runtime-generation.yaml'
                runtime.parent.mkdir(mode=0o700,parents=True)
                shutil.copyfile(c.tofu_source.parents[1]/runtime.relative_to(workdir.parents[1]),runtime)
                tofu_data_dir = workdir/'.terraform'
                tofu_data_dir.mkdir(mode=0o700)
                candidate = dict(version=1,generation_id=generation_id,databases={})
                endpoints = dict(head.endpoints if head else c.polaris_endpoints)
                if c.managed_enrollment:
                    from urllib.parse import urlsplit
                    if c.polaris_port_range is None:
                        raise ValueError('Managed per-database transport policy required')
                    used = {urlsplit(v).port for v in endpoints.values()}
                    for service in sorted(e['service_ref'] for e in bundle['tofu']['databases'].values()):
                        if service not in endpoints:
                            port = next((p for p in range(c.polaris_port_range[0],c.polaris_port_range[1]+1) if p not in used),None)
                            if port is None:
                                raise ValueError('Enrolled transport port range exhausted')
                            used.add(port)
                            endpoints[service] = 'http://127.0.0.1:'+str(port)
                    if len({urlsplit(v).port for v in endpoints.values()}) != len(endpoints):
                        raise ValueError('Each Polaris service requires a separate sealed transport')
                for key,entry in bundle['tofu']['databases'].items():
                    endpoint = endpoints[entry['service_ref']]
                    polaris = dict(endpoint_url=endpoint)
                    value = dict(polaris=polaris)
                    if phase == 'full':
                        polaris.update(private['secrets'][material.ref_identity(entry['trino_secret'])]['data'])
                        polaris.pop('realm')
                        value['trino'] = built['runtime_auth']['databases'][key]['roles']['data_engineer']
                    candidate['databases'][key] = value
                if phase == 'full':
                    candidate['trino'] = dict(endpoint_url=c.trino_endpoint,ca_file=str(c.trino_ca))
                save_private(directory/'candidate.json',candidate)
                _private_tree(directory)
                for argv in (['tofu','init','-input=false','-lockfile=readonly'],
                        ['tofu','plan','-input=false','-no-color','-state='+str(c.tofu_state),
                         '-var-file='+str(directory/'material/terraform.tfvars.json'),
                         '-var=kubeconfig_path='+str(c.kubeconfig),'-var=kube_context='+local_gitops.CONTEXT,
                         '-out='+str(directory/'saved.plan')]):
                    self._check(fence,base,spec,pins)
                    _run(argv,cwd=workdir,tofu_data_dir=tofu_data_dir)
                state = json.loads(private_file(c.tofu_state))
                operator_config = dict(version=1,bundle=str(directory/'bundle'),snapshot=str(snapshot),
                    tofu_plan=str(directory/'saved.plan'),tofu_workdir=str(workdir),tofu_state=str(c.tofu_state),
                    tofu_data_dir=str(tofu_data_dir),
                    state_lineage=state['lineage'],state_serial=state['serial'],candidate_credentials=str(directory/'candidate.json'),
                    kube_context=local_gitops.CONTEXT,s3_credentials=str(c.s3_credentials),auth_headers=str(c.auth_headers),
                    phase=phase,predecessor_id=predecessor_id,kubeconfig=str(c.kubeconfig),
                    preparation_inputs={str(p):digest(p.read_bytes()) for p in
                        [directory/'private-input.json',directory/'material/terraform.tfvars.json',runtime,
                         c.auth_headers,c.s3_credentials,c.kubeconfig,c.trino_ca,
                         *[workdir/name for name in pins['source']]]})
                operator_config.update(managed_inputs)
                if c.managed_enrollment:
                    # Preserve the selected parent's immutable proof as operator
                    # inputs too; checking it must never recursively fence its
                    # historical parent against today's head.
                    parent_paths = [head.private,Path(head.profile)]
                    if not head.pin.get('base'):
                        parent_paths += [head.bundle.parent/'journal.json',head.bundle.parent/'operator.json',
                            control_store.root().resolve()/'warehouse-operator-receipts'/(head.pin['operation_id']+'.json')]
                    operator_config['preparation_inputs'].update({str(p):digest(private_file(p)) for p in parent_paths})
                    operator_config['preparation_inputs'][c.managed_enrollment] = digest(private_file(c.managed_enrollment))
                    operator_config['managed_transport'] = dict(enrollment_path=c.managed_enrollment,
                        endpoints={entry['service_ref']:endpoints[entry['service_ref']]
                                   for entry in bundle['tofu']['databases'].values()})
                save_private(directory/'operator.json',operator_config)
                _private_tree(directory)
                operator = Operator(directory/'operator.json')
                self._check(fence,base,spec,pins)
                plan_hash = operator.validate_saved_plan()
                self._check(fence,base,spec,pins)
                observations = operator.observe_secrets(dict(id=None))
                self._check(fence,base,spec,pins)
                # Provider cache is retained for apply; lock and source are sealed.
                artifacts = _tree(directory)
                artifacts.pop('journal.json')
                journal = dict(state='SEALED',request_id=request_id,claim_id=fence.claim_id,pins=pins,files=artifacts,
                    observations=observations,binding=operator.binding,saved_plan_digest=plan_hash,
                    phase=phase,predecessor_id=predecessor_id)
                save_private(journal_path,journal)
                ref = 'preparation:'+digest(canonical(journal))
                with auth.transaction() as db:
                    auth.preparation_guard(db,fence)
                    db.execute('UPDATE db_creation_requests SET private_receipt_ref=? WHERE id=?',(ref,request_id))
            self._check(fence,base,bundle['manifest']['spec'],journal['pins'])
            ref = 'preparation:'+digest(canonical(journal))
            def final(db):
                current_root, current_request = auth.preparation_guard(db,fence)
                target_only(db,base['manifest']['spec'],bundle['manifest']['spec'],current_root,
                    json.loads(current_request['payload'])['phase'],request=current_request)
                current = db.execute('SELECT private_receipt_ref FROM db_creation_requests WHERE id=?',(request_id,)).fetchone()
                if current[0] != ref or journal['claim_id'] != fence.claim_id:
                    raise OperationError('STALE_PLAN')
                self._validate_seal(directory,journal,ref)
                if self._pins(db=db) != journal['pins']:
                    raise OperationError('STALE_PLAN')
            return creations._prepare_linked_plan(c.enrollment,root['id'],bundle,journal['observations'],
                private_ref=ref,public_summary=dict(code='PLAN_READY'),preparation_request_id=request_id,
                phase=phase,predecessor_id=journal['predecessor_id'],preparation_fence=fence,final_fence=final)

    @staticmethod
    def _journal(path):
        try:
            return json.loads(private_file(path))
        except (OSError,ValueError):
            raise OperationError('RECONCILIATION_REQUIRED') from None

    @staticmethod
    def _validate_seal(directory,journal,ref):
        files = _tree(directory)
        files.pop('journal.json',None)
        if (journal.get('state') != 'SEALED' or ref != 'preparation:'+digest(canonical(journal))
                or files != journal['files']):
            raise OperationError('RECONCILIATION_REQUIRED')
