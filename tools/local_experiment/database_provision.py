"""Fixed Polaris provisioning leaves for one frozen, arbitrary database generation.

Every mutation has a private write-ahead receipt. Issued credentials are fsynced by
the caller before any subsequent API call; an ambiguous principal POST is never
reissued, even when a later GET returns 404.
"""
import hashlib
from urllib.parse import quote

import database_generation
import polaris_permissions as permissions


def grants(consumer, schemas):
    if consumer == 'trino':
        return [dict(type='catalog', privilege=p) for p in permissions.TRINO]
    return [dict(type='catalog', privilege='CATALOG_READ_PROPERTIES')] + [
        dict(type='namespace', namespace=[schema], privilege=p)
        for schema in sorted(schemas) for p in permissions.BOOTSTRAP]


def _equal_grants(actual, wanted):
    return len(actual) == len(wanted) and all(actual.count(g) == wanted.count(g) for g in wanted)


def provision(request, database_id, entry, schemas, state, persist, supplied=None, *, verify_only=False):
    """Request is an authenticated root API; state is a private per-operation map."""
    supplied = supplied or {}
    database = state.setdefault(database_id, dict(leaves={}, consumers={}))
    leaves = database['leaves']
    if any(leaf.get('state') != 'ACKNOWLEDGED' for leaf in leaves.values()):
        raise RuntimeError('Polaris mutation outcome UNKNOWN; no retry')

    def get(path):
        try:
            result = request('GET', path)
            for key in ('catalog', 'principal', 'principalRole', 'catalogRole'):
                if isinstance(result, dict) and set(result) == {key}:
                    return result[key]
            return result
        except permissions.APIError as error:
            if error.status == 404:
                return None
            raise

    def mutate(key, method, path, body, *, credentials=False):
        if verify_only or key in leaves:
            raise RuntimeError('Polaris mutation outcome requires explicit observation')
        leaves[key] = {'state': 'DISPATCHED'}
        persist(state)
        result = request(method, path, body)
        if credentials:
            issued = result['credentials']
            principal = result['principal']
            saved = dict(client_id=issued['clientId'], client_secret=issued['clientSecret'],
                         realm=entry['realm'], principal_name=principal['name'])
            if not all(isinstance(v, str) and v for v in saved.values()):
                raise RuntimeError('Polaris credential acknowledgement unavailable')
            database['consumers'][key] = saved
        leaves[key] = {'state': 'ACKNOWLEDGED'}
        try:
            persist(state)  # No API call between credential response and durable save.
        except Exception:
            leaves[key] = {'state': 'DISPATCHED'}
            raise
        return result

    catalog_name = quote(entry['catalog'], safe='')
    management = permissions.MANAGEMENT
    path = management+'/catalogs/'+catalog_name
    definition = database_generation._catalog(entry)
    catalog = get(path)
    if catalog is None:
        mutate('catalog', 'POST', management+'/catalogs', {'catalog': definition})
        catalog = get(path)
    if catalog is None or any(catalog.get(k) != definition[k] for k in ('name', 'type')) or any(
            catalog.get(k, {}).get(field) != value for k in ('properties', 'storageConfigInfo')
            for field, value in definition[k].items()):
        raise RuntimeError('Polaris catalog binding mismatch')
    namespaces = '/api/catalog/v1/'+catalog_name+'/namespaces'
    present = request('GET', namespaces)['namespaces']
    if any(not isinstance(row, list) or len(row) != 1 or row[0] not in schemas for row in present):
        raise RuntimeError('Polaris namespace set mismatch')
    for schema in sorted(schemas):
        if [schema] not in present:
            mutate('namespace:'+schema, 'POST', namespaces, {'namespace': [schema], 'properties': {}})
        actual = request('GET', namespaces+'/'+quote(schema, safe=''))
        if actual.get('namespace') != [schema]:
            raise RuntimeError('Polaris namespace identity mismatch')
    for consumer in ('trino', 'iceberg-bootstrap'):
        saved = database['consumers'].get(consumer) or supplied.get(consumer)
        base = 'shirokuma_'+hashlib.sha256(database_id.encode()).hexdigest()[:16]+'_'+consumer.replace('-', '_')
        if saved is not None:
            if saved.get('realm') != entry['realm'] or not saved.get('client_id') or not saved.get('client_secret'):
                raise RuntimeError('Polaris supplied principal mismatch')
            principals = request('GET', management+'/principals')['principals']
            matches = [p for p in principals if p.get('clientId') == saved['client_id']]
            if len(matches) != 1:
                raise RuntimeError('Polaris supplied principal unavailable')
            name = matches[0]['name']
            principal = get(management+'/principals/'+quote(name, safe=''))
        else:
            name = base
            principal = get(management+'/principals/'+quote(name, safe=''))
            if principal is not None or consumer in leaves:
                raise RuntimeError('Polaris principal issuance outcome UNKNOWN; no retry')
            created = mutate(consumer, 'POST', management+'/principals', {
                'principal': {'name': name, 'properties': permissions.OWNER}, 'credentialRotationRequired': False}, credentials=True)
            principal = created['principal']
            saved = database['consumers'][consumer]
        permissions.owned(principal, name)
        if principal.get('clientId') != saved['client_id']:
            raise RuntimeError('Polaris principal credential identity mismatch')
        database['consumers'][consumer] = dict(saved, principal_name=name)
        persist(state)
        # Retain existing owned role names when adopting supplied credentials.
        principal_path = management+'/principals/'+quote(name, safe='')
        memberships = request('GET', principal_path+'/principal-roles')['roles']
        if len(memberships) > 1:
            raise RuntimeError('Unexpected principal memberships')
        role = memberships[0]['name'] if memberships else name+'_principal'
        role_path = management+'/principal-roles/'+quote(role, safe='')
        entity = get(role_path)
        if entity is None:
            mutate(consumer+':role', 'POST', management+'/principal-roles',
                   {'principalRole': {'name': role, 'properties': permissions.OWNER}})
            entity = get(role_path)
        permissions.owned(entity, role)
        assigned = request('GET', role_path+'/catalog-roles/'+catalog_name)['roles']
        if len(assigned) > 1:
            raise RuntimeError('Unexpected catalog memberships')
        cat_role = assigned[0]['name'] if assigned else name+'_content'
        catalog_roles = path+'/catalog-roles'
        entity = get(catalog_roles+'/'+quote(cat_role, safe=''))
        if entity is None:
            mutate(consumer+':catalog-role', 'POST', catalog_roles,
                   {'catalogRole': {'name': cat_role, 'properties': permissions.OWNER}})
            entity = get(catalog_roles+'/'+quote(cat_role, safe=''))
        permissions.owned(entity, cat_role)
        for item in request('GET', management+'/catalogs')['catalogs']:
            roles = request('GET', role_path+'/catalog-roles/'+quote(item['name'], safe=''))['roles']
            if any(item['name'] != entry['catalog'] or r['name'] != cat_role for r in roles):
                raise RuntimeError('Unexpected cross-catalog membership')
        grant_path = catalog_roles+'/'+quote(cat_role, safe='')+'/grants'
        wanted = grants(consumer, schemas)
        actual = request('GET', grant_path)['grants']
        if any(g not in wanted for g in actual):
            raise RuntimeError('Unexpected consumer grants')
        for index, grant in enumerate(wanted):
            if grant not in actual:
                mutate(consumer+':grant:'+str(index), 'PUT', grant_path, {'grant': grant})
        if not _equal_grants(request('GET', grant_path)['grants'], wanted):
            raise RuntimeError('Consumer grant verification failed')
        for member_path, key, wanted_name in (
                (role_path+'/catalog-roles/'+catalog_name, 'catalogRole', cat_role),
                (principal_path+'/principal-roles', 'principalRole', role)):
            names = [r['name'] for r in request('GET', member_path)['roles']]
            if not names:
                mutate(consumer+':membership:'+key, 'PUT', member_path, {key: {'name': wanted_name}})
                names = [r['name'] for r in request('GET', member_path)['roles']]
            if names != [wanted_name]:
                raise RuntimeError('Consumer membership verification failed')
    return database['consumers']
