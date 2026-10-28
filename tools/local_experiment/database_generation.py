"""Pure, public configuration generation for the approved local database profile.

This module renders files only. It does not create credentials, contact a backend,
record an Operation, or change registry readiness. Secret values belong exclusively
to the private material adapter.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

import yaml

from data_management import validate_sql_name
from database_security import render_file_acl_identities, render_opa_policy_identities
from trino_auth import scoped_username

ROOT = Path(__file__).resolve().parents[2]
PROFILE_ID = 'local-polaris-1.8.0'
SCHEMAS = ('raw', 'marts', 'sandbox', 'staging')
DB_KEYS = {
    'root': ('client_id', 'client_secret', 'realm'),
    's3': ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_REGION', 'S3_ENDPOINT', 'S3_BUCKET', 'S3_REGION', 'S3_PATH_STYLE'),
    'jwt': ('private.pem', 'public.pem'),
    'polaris_trino': ('client_id', 'client_secret', 'realm'),
    'polaris_bootstrap': ('client_id', 'client_secret', 'realm'),
}
SHARED_KEYS = {
    'postgres': ('database', 'username', 'password'),
    'polaris_bootstrap': (),  # One derived JSON file per database, never all realms in one database.
    'seaweed_server': ('s3.json',),
    'trino_auth': ('password.db', 'shared-secret', 'tls.pem'),
    'gitops_source': ('accesskey', 'secretkey'),
    'operator': ('access_key', 'secret_key'),
}
SHARED_NAMES = dict(postgres='postgres_secret', polaris_bootstrap='polaris_bootstrap_secret',
    seaweed_server='seaweedfs_server_secret', trino_auth='trino_auth_secret',
    gitops_source='gitops_source_secret', operator='operator_secret')
PROFILE_DIRS = ('deploy/gitops/dev', 'deploy/gitops/object-storage', 'deploy/gitops/catalog/database')
PROFILE_FILES = (
    'deploy/gitops/catalog/server/deployment.yaml', 'deploy/gitops/catalog/server/service.yaml',
    'deploy/gitops/catalog/server/networkpolicy.yaml', 'deploy/catalog/bootstrap/job.yaml',
    'deploy/catalog/bootstrap/networkpolicy.yaml', 'deploy/local-experiment/trino/deployment.yaml',
    'deploy/local-experiment/trino/service.yaml', 'deploy/local-experiment/trino/networkpolicy.yaml',
    'deploy/local-experiment/trino/node.properties', 'deploy/local-experiment/trino/jvm.config',
    'deploy/local-experiment/trino/log.properties', 'deploy/local-experiment/trino/tpch.properties',
    'deploy/local-experiment/trino/secure/deployment.yaml', 'deploy/local-experiment/trino/secure/kustomization.yaml',
    'deploy/local-experiment/trino/secure/config.properties',
    'deploy/local-experiment/trino/secure/password-authenticator.properties',
    'deploy/local-experiment/trino/secure/access-control.properties',
    'deploy/local-experiment/trino/secure/opa-access-control.properties',
    'deploy/local-experiment/trino/secure/opa/api.rego',
)
BUCKET = 'shirokuma-lakehouse'
ENDPOINT = 'http://seaweedfs-s3.shirokuma-storage.svc.cluster.local:8333'
REGION = 'us-east-1'
ANNOTATION = 'shirokuma.dev/database-generation'


def _json(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + '\n').encode()


def _digest(files):
    value = hashlib.sha256()
    for name, content in sorted(files.items()):
        value.update(name.encode() + b'\0' + str(len(content)).encode() + b'\0' + content)
    return value.hexdigest()


def _profile_files(root):
    root = Path(root)
    paths = set(PROFILE_FILES)
    for directory in PROFILE_DIRS:
        paths.update(p.relative_to(root).as_posix() for p in (root/directory).glob('*.yaml'))
    paths.update(('tools/local_experiment/database_generation.py', 'tools/local_experiment/database_security.py',
                  'tools/local_experiment/trino_auth.py', 'tools/local_experiment/requirements-generation.txt'))
    return {path: (root/path).read_bytes() for path in sorted(paths)}


# Version 1 included renderer source bytes in its approved profile. Freeze that
# released profile while still rejecting delivery-template/dependency drift.
_V1_TEMPLATES = '33b99558776a42339f37d34d37f906cc1dc203ed124e85557c25c2c4d2c06196'
_V1_PROFILE = '8a7e1d501d8c3c0acdd55bcbb6b3e77b773edfbb69af5486d3e0773939ca2f60'


def profile_fingerprint(root=ROOT, *, version=1):
    files = _profile_files(root)
    if version == 1:
        templates = {p: v for p, v in files.items() if p not in (
            'tools/local_experiment/database_generation.py',
            'tools/local_experiment/database_security.py', 'tools/local_experiment/trino_auth.py')}
        if _digest(templates) == _V1_TEMPLATES:
            return _V1_PROFILE
    return _digest(files)


def _token(database_id):
    return hashlib.sha256(database_id.encode()).hexdigest()[:16]


def bootstrap_key(database_id):
    return 'credentials.' + _token(database_id) + '.json'


def _exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError('invalid public generation structure')


def _text(value, pattern=None):
    if (not isinstance(value, str) or not value or any(c in value for c in '\x00\r\n')
            or (pattern and re.fullmatch(pattern, value) is None)):
        raise ValueError('invalid public generation value')
    return value


def _positive(value):
    if type(value) is not int or value <= 0:
        raise ValueError('invalid revision')


def _ref(value, keys, namespace, seen):
    _exact(value, ('namespace', 'name', 'version', 'keys'))
    name = _text(value['name'], r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?')
    version = _text(value['version'], r'[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?')
    if (value['namespace'] != namespace or not name.endswith('-' + version)
            or not isinstance(value['keys'], list) or len(value['keys']) != len(keys)
            or set(value['keys']) != set(keys) or (namespace, name) in seen):
        raise ValueError('invalid or reused Secret reference')
    seen.add((namespace, name))


def validate_spec(spec, root=ROOT):
    """Reject unknown fields, aliases, mutable secret names and unsupported profiles."""
    try:
        _exact(spec, ('version', 'generation_id', 'warehouse_id', 'profile', 'shared_secret_refs', 'databases', 'grant_revisions'))
        if type(spec['version']) is not int or spec['version'] not in (1, 2) or spec['warehouse_id'] != 'LOCAL':
            raise ValueError()
        _text(spec['generation_id'], r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')
        _exact(spec['profile'], ('id', 'template_fingerprint'))
        if spec['profile'] != dict(id=PROFILE_ID, template_fingerprint=profile_fingerprint(root, version=spec['version'])):
            raise ValueError('approved profile changed')
        if not isinstance(spec['databases'], dict) or not spec['databases']:
            raise ValueError()
        _exact(spec['shared_secret_refs'], SHARED_KEYS)
        secret_names, catalogs, services, realms, locations, schema_ids = set(), set(), set(), set(), [], set()
        for role, keys in SHARED_KEYS.items():
            namespace = 'flux-system' if role == 'gitops_source' else 'shirokuma-storage' if role in ('operator', 'seaweed_server') else 'shirokuma-dev'
            if role == 'polaris_bootstrap':
                keys = [bootstrap_key(key) for key in spec['databases']]
            _ref(spec['shared_secret_refs'][role], keys, namespace, secret_names)
        for key, database in sorted(spec['databases'].items()):
            _text(key, r'[a-z0-9][a-z0-9_-]{0,58}')
            _exact(database, ('database_revision', 'sql_catalog', 'binding', 'schemas', 'secret_refs'))
            _positive(database['database_revision'])
            catalog = validate_sql_name(database['sql_catalog'])
            if catalog == 'iceberg' or catalog in catalogs:
                raise ValueError('duplicate or legacy SQL catalog')
            catalogs.add(catalog)
            binding = database['binding']
            _exact(binding, ('desired_revision', 'polaris_service_ref', 'polaris_realm', 'polaris_catalog',
                'storage_profile_ref', 'credential_profile_ref', 'base_location'))
            _positive(binding['desired_revision'])
            service = _text(binding['polaris_service_ref'], r'[a-z](?:[a-z0-9-]{0,47}[a-z0-9])?')
            realm = _text(binding['polaris_realm'], r'[A-Za-z][A-Za-z0-9_]{0,62}')
            if (service in services or service in ('polaris-postgresql', 'trino-local', 'seaweedfs') or realm in realms
                    or binding['polaris_catalog'] != 'db_' + key
                    or binding['storage_profile_ref'] != 'local-s3' or binding['credential_profile_ref'] != 'local-polaris'):
                raise ValueError('unsupported or duplicate binding')
            services.add(service)
            realms.add(realm)
            location = _text(binding['base_location'], r's3://shirokuma-lakehouse/databases/[a-z0-9][a-z0-9_/-]*/')
            if '//' in location[len('s3://'):] or any(location.startswith(other) or other.startswith(location) for other in locations):
                raise ValueError('overlapping storage prefixes')
            locations.append(location)
            if not isinstance(database['schemas'], list) or len(database['schemas']) != len(SCHEMAS):
                raise ValueError()
            schemas = set()
            for schema in database['schemas']:
                _exact(schema, ('id', 'sql_name', 'purpose', 'revision'))
                _text(schema['id'])
                _positive(schema['revision'])
                if schema['id'] in schema_ids or schema['sql_name'] in schemas or schema['sql_name'] != schema['purpose']:
                    raise ValueError()
                schema_ids.add(schema['id'])
                schemas.add(schema['sql_name'])
            if schemas != set(SCHEMAS):
                raise ValueError('unsupported schema profile')
            _exact(database['secret_refs'], DB_KEYS)
            for role, keys in DB_KEYS.items():
                _ref(database['secret_refs'][role], keys, 'shirokuma-dev', secret_names)
        grants = set()
        if not isinstance(spec['grant_revisions'], list):
            raise ValueError()
        for grant in spec['grant_revisions']:
            _exact(grant, ('principal_id', 'database_id', 'maximum_role', 'revision'))
            _text(grant['principal_id'])
            _positive(grant['revision'])
            pair = (grant['principal_id'], grant['database_id'])
            roles = ('bi_viewer', 'data_engineer', 'revoked') + (('admin',) if spec['version'] == 2 else ())
            if pair in grants or grant['database_id'] not in spec['databases'] or grant['maximum_role'] not in roles:
                raise ValueError()
            grants.add(pair)
        # Canonical ordering makes reordered equivalent input byte-identical.
        result = json.loads(_json(spec))
        result['grant_revisions'].sort(key=lambda row: (row['principal_id'], row['database_id']))
        for database in result['databases'].values():
            database['schemas'].sort(key=lambda row: row['sql_name'])
        for ref in list(result['shared_secret_refs'].values()) + [ref for db in result['databases'].values() for ref in db['secret_refs'].values()]:
            ref['keys'].sort()
        return result
    except (KeyError, TypeError, ValueError, OSError):
        raise ValueError('invalid public generation specification') from None


def _identity(spec):
    return dict(version=spec['version'], platform_admin='shirokuma_admin', databases={key: dict(
        sql_catalog=db['sql_catalog'], generation_id=spec['generation_id'],
        roles={role: scoped_username(key, role) for role in (
            ('bi_viewer', 'data_engineer', 'admin') if spec['version'] == 2 else ('bi_viewer', 'data_engineer'))})
        for key, db in spec['databases'].items()})


def _tofu(spec):
    result = dict(version=1, generation_id=spec['generation_id'], databases={},
        shared={SHARED_NAMES[key]: ref for key, ref in spec['shared_secret_refs'].items()},
        derived_secret_roles=['polaris_bootstrap_secret', 'seaweedfs_server_secret'],
        s3_policy=dict(bucket=BUCKET, endpoint=ENDPOINT, region=REGION,
            operator=dict(name='shirokuma-local-lite-operator', actions=['Admin']),
            gitops=dict(name='shirokuma-local-gitops', actions=['Read:shirokuma-gitops', 'List:shirokuma-gitops'])))
    for key, db in spec['databases'].items():
        binding, refs = db['binding'], db['secret_refs']
        prefix = binding['base_location'].split('/', 3)[3]
        result['databases'][key] = dict(realm=binding['polaris_realm'], catalog=binding['polaris_catalog'],
            sql_catalog=db['sql_catalog'], service_ref=binding['polaris_service_ref'], prefix=prefix,
            base_location=binding['base_location'], desired_revision=binding['desired_revision'],
            postgres_database='polaris_' + _token(key), bootstrap_credentials_key=bootstrap_key(key),
            s3_identity_name='shirokuma-db-' + _token(key),
            s3_actions=[action + ':' + BUCKET + '/' + prefix + '*' for action in ('Read', 'List', 'Write', 'Tagging')],
            root_secret=refs['root'], s3_secret=refs['s3'], jwt_secret=refs['jwt'],
            trino_secret=refs['polaris_trino'], bootstrap_secret=refs['polaris_bootstrap'])
    deferred = [spec['shared_secret_refs']['trino_auth']]
    deferred.extend(ref for db in spec['databases'].values()
                    for role, ref in db['secret_refs'].items() if role in ('polaris_trino', 'polaris_bootstrap'))
    all_refs = list(spec['shared_secret_refs'].values()) + [ref for db in spec['databases'].values() for ref in db['secret_refs'].values()]
    sort_ref = lambda ref: (ref['namespace'], ref['name'], ref['version'])
    result['deferred_secret_refs'] = sorted(deferred, key=sort_ref)
    result['bootstrap_secret_refs'] = sorted((ref for ref in all_refs if ref not in deferred), key=sort_ref)
    return result


def _yaml(profile, path):
    return yaml.safe_load(profile[path])


def _env(container, name, value=None, secret=None, key=None):
    entries = container.setdefault('env', [])
    entries[:] = [entry for entry in entries if entry['name'] != name]
    entry = dict(name=name)
    if secret is None:
        entry['value'] = value
    else:
        entry['valueFrom'] = dict(secretKeyRef=dict(name=secret['name'], key=key))
    entries.append(entry)


def _references(node, names):
    if isinstance(node, dict):
        if 'secretKeyRef' in node:
            reference = node['secretKeyRef']
            reference['name'] = names.get(reference['name'], reference['name'])
        if 'secretName' in node:
            node['secretName'] = names.get(node['secretName'], node['secretName'])
        for value in node.values():
            _references(value, names)
    elif isinstance(node, list):
        for value in node:
            _references(value, names)


def _mark(document, generation):
    document.setdefault('metadata', {}).setdefault('annotations', {})[ANNOTATION] = generation
    template = document.get('spec', {}).get('template')
    if template and document.get('kind') != 'Job':
        template.setdefault('metadata', {})['annotations'] = {ANNOTATION: generation}


def _kustomization(resources, namespace='shirokuma-dev', **extra):
    return dict(apiVersion='kustomize.config.k8s.io/v1beta1', kind='Kustomization',
                namespace=namespace, resources=resources, **extra)


def _catalog(entry):
    return dict(name=entry['catalog'], type='INTERNAL',
        properties={'default-base-location': entry['base_location'].rstrip('/'), 'polaris.config.drop-with-purge.enabled': 'true'},
        storageConfigInfo=dict(storageType='S3', allowedLocations=[entry['base_location'].rstrip('/')],
            region=REGION, endpoint=ENDPOINT, endpointInternal=ENDPOINT, stsUnavailable=True, pathStyleAccess=True))


def _render_polaris(files, profile, spec, tofu):
    servers, bootstrap, definitions, server_health, bootstrap_health, definition_health = [], [], [], [], [], []
    pg = spec['shared_secret_refs']['postgres']
    combined = spec['shared_secret_refs']['polaris_bootstrap']
    for key, entry in tofu['databases'].items():
        service, token = entry['service_ref'], _token(key)
        jdbc = 'jdbc:postgresql://polaris-postgresql.shirokuma-dev.svc.cluster.local:5432/' + entry['postgres_database'] + '?currentSchema=polaris_schema'
        deployment = _yaml(profile, 'deploy/gitops/catalog/server/deployment.yaml')
        deployment['metadata']['name'] = service
        deployment['metadata']['labels']['app.kubernetes.io/name'] = service
        deployment['spec']['selector']['matchLabels']['app.kubernetes.io/name'] = service
        deployment['spec']['template']['metadata']['labels']['app.kubernetes.io/name'] = service
        pod = deployment['spec']['template']['spec']
        container = pod['containers'][0]
        _env(container, 'POLARIS_REALM_CONTEXT_REALMS', entry['realm'])
        _env(container, 'QUARKUS_DATASOURCE_JDBC_URL', jdbc)
        _references(deployment, {'polaris-postgresql-credentials': pg['name'],
            'seaweedfs-s3-sales-credentials': entry['s3_secret']['name'], 'polaris-jwt-signing-key': entry['jwt_secret']['name']})
        _mark(deployment, spec['generation_id'])
        service_doc = _yaml(profile, 'deploy/gitops/catalog/server/service.yaml')
        service_doc['metadata']['name'] = service
        service_doc['metadata']['labels']['app.kubernetes.io/name'] = service
        service_doc['spec']['selector']['app.kubernetes.io/name'] = service
        policy = _yaml(profile, 'deploy/gitops/catalog/server/networkpolicy.yaml')
        policy['metadata']['name'] = service
        policy['spec']['podSelector']['matchLabels']['app.kubernetes.io/name'] = service
        for suffix, doc in [('deployment', deployment), ('service', service_doc), ('networkpolicy', policy)]:
            _mark(doc, spec['generation_id'])
            filename = service + '-' + suffix + '.yaml'
            files['deploy/gitops/catalog/server/' + filename] = _json(doc)
            servers.append(filename)
        server_health.append(dict(apiVersion='apps/v1', kind='Deployment', name=service, namespace='shirokuma-dev'))

        job = _yaml(profile, 'deploy/catalog/bootstrap/job.yaml')
        job_name = 'polaris-bootstrap-' + token
        job['metadata']['name'] = job_name
        job['metadata']['labels']['app.kubernetes.io/name'] = job_name
        job['spec']['template']['metadata']['labels']['app.kubernetes.io/name'] = job_name
        job['spec']['template']['metadata'].pop('annotations', None)
        pod = job['spec']['template']['spec']
        creator = pod['initContainers'][0]
        creator['name'] = 'create-database'
        creator['args'] = ['--no-psqlrc', '--set', 'ON_ERROR_STOP=1', '--file', '/bootstrap/create-database.sql']
        creator['volumeMounts'].append(dict(name='sql', mountPath='/bootstrap', readOnly=True))
        schema = deepcopy(creator)
        schema['name'] = 'create-schema'
        schema['args'] = ['--no-psqlrc', '--set', 'ON_ERROR_STOP=1', '--command', 'CREATE SCHEMA IF NOT EXISTS polaris_schema;']
        _env(schema, 'PGDATABASE', entry['postgres_database'])
        pod['initContainers'].append(schema)
        admin = pod['containers'][0]
        _env(admin, 'POLARIS_REALM_CONTEXT_REALMS', entry['realm'])
        _env(admin, 'QUARKUS_DATASOURCE_JDBC_URL', jdbc)
        _references(job, {'polaris-postgresql-credentials': pg['name'], 'polaris-bootstrap-credentials': combined['name']})
        credential_volume = next(volume for volume in pod['volumes'] if volume['name'] == 'credentials')
        credential_volume['secret']['items'] = [dict(key=entry['bootstrap_credentials_key'], path='credentials.json')]
        sql_name = 'polaris-bootstrap-sql-' + token
        pod['volumes'].append(dict(name='sql', configMap=dict(name=sql_name)))
        sql = ("SELECT format('CREATE DATABASE %I', '" + entry['postgres_database'] + "')\n"
               "WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '" + entry['postgres_database'] + "')\n\\gexec\n")
        sql_doc = dict(apiVersion='v1', kind='ConfigMap', metadata=dict(name=sql_name, namespace='shirokuma-dev'),
                       data={'create-database.sql': sql})
        np = _yaml(profile, 'deploy/catalog/bootstrap/networkpolicy.yaml')
        np['metadata']['name'] = job_name
        np['spec']['podSelector']['matchLabels']['app.kubernetes.io/name'] = job_name
        # Job pod templates are immutable. Global generation changes must not
        # recreate an unchanged database bootstrap; actual template/input changes
        # receive a new name instead of attempting an invalid in-place update.
        bootstrap_digest = hashlib.sha256(_json(dict(template=job['spec']['template'], sql=sql))).hexdigest()[:16]
        job_name += '-' + bootstrap_digest
        job['metadata']['name'] = job_name
        job['spec']['template']['metadata']['annotations'] = {'shirokuma.dev/bootstrap-config-digest': bootstrap_digest}
        for suffix, doc in [('job', job), ('sql', sql_doc), ('networkpolicy', np)]:
            _mark(doc, spec['generation_id'])
            filename = job_name + '-' + suffix + '.yaml'
            files['deploy/catalog/bootstrap/' + filename] = _json(doc)
            bootstrap.append(filename)
        bootstrap_health.append(dict(apiVersion='batch/v1', kind='Job', name=job_name, namespace='shirokuma-dev'))

        # P2-4 declares future provisioning inputs; it does not run a fixture or
        # pretend that catalog/namespace provisioning has already succeeded.
        definition_name = 'catalog-definition-' + token
        definition = dict(apiVersion='v1', kind='ConfigMap',
            metadata=dict(name=definition_name, namespace='shirokuma-dev'), data={
                'catalog.json': _json(dict(catalog=_catalog(entry))).decode(),
                'namespaces.json': _json([dict(namespace=[name], properties={}) for name in SCHEMAS]).decode(),
                'binding.json': _json(dict(database_id=key, generation_id=spec['generation_id'],
                    service_ref=service, realm=entry['realm'], catalog=entry['catalog'],
                    root_secret=entry['root_secret'], trino_secret=entry['trino_secret'],
                    bootstrap_secret=entry['bootstrap_secret'])).decode()})
        _mark(definition, spec['generation_id'])
        filename = definition_name + '.yaml'
        files['deploy/gitops/iceberg/bootstrap/' + filename] = _json(definition)
        definitions.append(filename)
        definition_health.append(dict(apiVersion='v1', kind='ConfigMap', name=definition_name, namespace='shirokuma-dev'))
    files['deploy/gitops/catalog/server/kustomization.yaml'] = _json(_kustomization(servers))
    files['deploy/catalog/bootstrap/kustomization.yaml'] = _json(_kustomization(bootstrap))
    files['deploy/gitops/iceberg/bootstrap/kustomization.yaml'] = _json(_kustomization(definitions))
    return server_health, bootstrap_health, definition_health


def _properties(entry, environment):
    uri = 'http://' + entry['service_ref'] + '.shirokuma-dev.svc.cluster.local:8181/api/catalog'
    values = [('connector.name', 'iceberg'), ('iceberg.catalog.type', 'rest'),
        ('iceberg.rest-catalog.uri', uri), ('iceberg.rest-catalog.warehouse', entry['catalog']),
        ('iceberg.rest-catalog.security', 'OAUTH2'),
        ('iceberg.rest-catalog.oauth2.credential', '${ENV:' + environment + '_CLIENT_ID}:${ENV:' + environment + '_CLIENT_SECRET}'),
        ('iceberg.rest-catalog.oauth2.scope', 'PRINCIPAL_ROLE:ALL'),
        ('iceberg.rest-catalog.oauth2.server-uri', uri + '/v1/oauth/tokens'),
        ('iceberg.rest-catalog.oauth2.token-exchange-enabled', 'false'),
        ('iceberg.rest-catalog.http-headers', 'Polaris-Realm:' + entry['realm']),
        ('iceberg.rest-catalog.vended-credentials-enabled', 'false'), ('fs.s3.enabled', 'true'),
        ('s3.endpoint', ENDPOINT), ('s3.region', '${ENV:' + environment + '_REGION}'),
        ('s3.path-style-access', 'true'), ('s3.aws-access-key', '${ENV:' + environment + '_ACCESS_KEY}'),
        ('s3.aws-secret-key', '${ENV:' + environment + '_SECRET_KEY}')]
    return ''.join(name + '=' + value + '\n' for name, value in values).encode()


def _render_trino(files, profile, spec, tofu, identities):
    base = 'deploy/local-experiment/trino/'
    deployment = _yaml(profile, base + 'deployment.yaml')
    patch = _yaml(profile, base + 'secure/deployment.yaml')
    pod = deployment['spec']['template']['spec']
    container = pod['containers'][0]
    secure = patch['spec']['template']['spec']
    secure_container = secure['containers'][0]
    image = _yaml(profile, base + 'secure/kustomization.yaml')['images'][0]
    container['image'] = image['newName'] + '@' + image['digest']
    container['env'] = []
    container['command'] = secure_container['command']
    container['ports'] = secure_container['ports']
    container['volumeMounts'].extend(secure_container['volumeMounts'])
    auth = spec['shared_secret_refs']['trino_auth']
    _env(container, 'TRINO_INTERNAL_SHARED_SECRET', secret=auth, key='shared-secret')
    for probe in ('startupProbe', 'readinessProbe', 'livenessProbe'):
        container[probe]['httpGet'].update(port='https', scheme='HTTPS')
    pod['containers'].extend(secure['containers'][1:])
    pod['volumes'] = [volume for volume in pod['volumes'] if volume['name'] != 'config']
    pod['volumes'].extend(secure['volumes'])
    _references(deployment, {'trino-local-auth': auth['name']})
    config_files = ['config.properties', 'node.properties', 'jvm.config', 'log.properties', 'tpch.properties',
                    'password-authenticator.properties', 'access-control.properties', 'opa-access-control.properties', 'rules.json']
    catalog_paths = {'tpch.properties': 'catalog/tpch.properties'}
    for key, entry in tofu['databases'].items():
        environment = 'DB_' + _token(key).upper()
        for suffix, ref, secret_key in [('CLIENT_ID', entry['trino_secret'], 'client_id'),
                ('CLIENT_SECRET', entry['trino_secret'], 'client_secret'), ('REGION', entry['s3_secret'], 'S3_REGION'),
                ('ACCESS_KEY', entry['s3_secret'], 'AWS_ACCESS_KEY_ID'), ('SECRET_KEY', entry['s3_secret'], 'AWS_SECRET_ACCESS_KEY')]:
            _env(container, environment + '_' + suffix, secret=ref, key=secret_key)
        filename = 'catalog-' + entry['sql_catalog'] + '.properties'
        files[base + filename] = _properties(entry, environment)
        config_files.append(filename)
        catalog_paths[filename] = 'catalog/' + entry['sql_catalog'] + '.properties'
    config_volume = next(volume for volume in pod['volumes'] if volume['name'] == 'config')
    config_volume['configMap']['items'] = [dict(key=name, path=catalog_paths.get(name, name)) for name in config_files]
    _mark(deployment, spec['generation_id'])
    files[base + 'deployment.yaml'] = _json(deployment)
    service = _yaml(profile, base + 'service.yaml')
    service['spec']['ports'][0]['targetPort'] = 'https'
    _mark(service, spec['generation_id'])
    files[base + 'service.yaml'] = _json(service)
    network = _yaml(profile, base + 'networkpolicy.yaml')
    network['spec']['ingress'] = []
    catalog_egress = next(rule for rule in network['spec']['egress']
                          if any(port['port'] == 8181 for port in rule.get('ports', [])))
    catalog_egress['to'] = [dict(podSelector=dict(matchLabels={
        'app.kubernetes.io/name': entry['service_ref'], 'app.kubernetes.io/component': 'catalog-server'}))
        for entry in tofu['databases'].values()]
    _mark(network, spec['generation_id'])
    files[base + 'networkpolicy.yaml'] = _json(network)
    for filename in ('node.properties', 'jvm.config', 'log.properties', 'tpch.properties'):
        files[base + filename] = profile[base + filename]
    for filename in ('config.properties', 'password-authenticator.properties', 'access-control.properties', 'opa-access-control.properties'):
        files[base + filename] = profile[base + 'secure/' + filename]
    files[base + 'rules.json'] = _json(render_file_acl_identities(identities))
    files[base + 'opa/guard.rego'] = render_opa_policy_identities(identities).encode()
    files[base + 'opa/api.rego'] = profile[base + 'secure/opa/api.rego']
    # Both delivery roots use the same authenticated deployment and policy. Base
    # must not accidentally reopen the unauthenticated P1 configuration.
    for directory, prefix in [('', ''), ('secure/', '../')]:
        config = _kustomization([prefix + name for name in ('deployment.yaml', 'service.yaml', 'networkpolicy.yaml')],
            generatorOptions=dict(annotations={'kustomize.toolkit.fluxcd.io/substitute': 'disabled'}),
            configMapGenerator=[dict(name='trino-local-config', files=[prefix + name for name in config_files]),
                dict(name='trino-local-opa', files=[prefix + 'opa/guard.rego', prefix + 'opa/api.rego'])])
        files[base + directory + 'kustomization.yaml'] = _json(config)


def _children(files, spec, health):
    servers, bootstrap, definitions = health
    rows = [
        ('dev', 'deploy/gitops/dev', [], []),
        ('object-storage', 'deploy/gitops/object-storage', ['dev'], [dict(apiVersion='apps/v1', kind='StatefulSet', name='seaweedfs', namespace='shirokuma-storage')]),
        ('catalog-database', 'deploy/gitops/catalog/database', ['object-storage'], [dict(apiVersion='apps/v1', kind='StatefulSet', name='polaris-postgresql', namespace='shirokuma-dev')]),
        ('catalog-bootstrap', 'deploy/catalog/bootstrap', ['catalog-database'], bootstrap),
        ('catalog', 'deploy/gitops/catalog/server', ['catalog-bootstrap'], servers),
        ('iceberg-bootstrap', 'deploy/gitops/iceberg/bootstrap', ['catalog'], definitions),
        ('trino', 'deploy/local-experiment/trino/secure', ['iceberg-bootstrap'], [dict(apiVersion='apps/v1', kind='Deployment', name='trino-local', namespace='shirokuma-dev')]),
    ]
    paths = []
    for name, target, dependencies, checks in rows:
        document = dict(apiVersion='kustomize.toolkit.fluxcd.io/v1', kind='Kustomization',
            metadata=dict(name='shirokuma-' + name, namespace='flux-system', annotations={ANNOTATION: spec['generation_id']}),
            spec=dict(interval='10m', retryInterval='1m', timeout='10m', path='./' + target, prune=True,
                      sourceRef=dict(kind='Bucket', name='shirokuma-local-source'), wait=True))
        if dependencies:
            document['spec']['dependsOn'] = [dict(name='shirokuma-' + item) for item in dependencies]
        if checks:
            document['spec']['healthChecks'] = checks
        path = 'deploy/gitops/clusters/local-lite/' + name + '.yaml'
        files[path] = _json(document)
        paths.append(path)
    return paths


def render(spec, root=ROOT):
    spec = validate_spec(spec, root)
    profile = _profile_files(root)
    tofu, identities = _tofu(spec), _identity(spec)
    files = {}
    names = {'polaris-postgresql-credentials': spec['shared_secret_refs']['postgres']['name'],
             'seaweedfs-s3-credentials': spec['shared_secret_refs']['seaweed_server']['name']}
    for path, content in profile.items():
        if any(path.startswith(directory + '/') for directory in PROFILE_DIRS):
            document = yaml.safe_load(content)
            _references(document, names)
            if document.get('kind') != 'Kustomization':
                _mark(document, spec['generation_id'])
            files[path] = _json(document)
    health = _render_polaris(files, profile, spec, tofu)
    _render_trino(files, profile, spec, tofu, identities)
    children = _children(files, spec, health)
    files['tofu-public.json'] = _json(tofu)
    files['identity-public.json'] = _json(identities)
    files['catalog-auth-public.json'] = _json(dict(version=1, databases={key: dict(
        service_ref=entry['service_ref'], realm=entry['realm'], catalog=entry['catalog'],
        generation_id=spec['generation_id'], credential_ref=entry['trino_secret'])
        for key, entry in tofu['databases'].items()}))
    manifest = dict(version=1, generation_id=spec['generation_id'], spec=spec,
        profile_fingerprint=spec['profile']['template_fingerprint'], child_files=children,
        file_digests={path: hashlib.sha256(content).hexdigest() for path, content in sorted(files.items())},
        delivery_digest=_digest(files))
    return dict(manifest=manifest, files=files, tofu=tofu)


def _path(name):
    value = PurePosixPath(name)
    if value.is_absolute() or value.as_posix() != name or '..' in value.parts or not value.parts:
        raise ValueError('invalid bundle path')
    return name


def write_bundle(bundle, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    for name, content in sorted(bundle['files'].items()):
        target = directory/_path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (directory/'generation.json').write_bytes(_json(bundle['manifest']))
    return directory


def load_bundle(directory, root=None):
    """Verify exact files and re-render against the current approved profile."""
    try:
        directory = Path(directory)
        manifest = json.loads((directory/'generation.json').read_bytes())
        expected = render(manifest['spec'], ROOT if root is None else root)
        if manifest != expected['manifest']:
            raise ValueError()
        paths = {path.relative_to(directory).as_posix() for path in directory.rglob('*') if path.is_file()}
        if paths != set(expected['files']) | {'generation.json'}:
            raise ValueError()
        for name, content in expected['files'].items():
            if (directory/_path(name)).read_bytes() != content:
                raise ValueError()
        return expected
    except (OSError, KeyError, TypeError, ValueError):
        raise ValueError('invalid or stale generation bundle') from None
