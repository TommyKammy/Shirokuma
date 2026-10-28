"""Authorized registered database exploration through scoped Iceberg REST.

Public entry points take (principal, selected_role, resource_id, ...).
No function discovers or activates a runtime profile.
"""
import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
import re
import socket
import subprocess
import time
from urllib.parse import quote, urlencode
import urllib.request
import urllib.error
import uuid

import control_store
import data_management as registry
import request_auth
import runtime_profile

_CURSOR_KEY = os.urandom(32)


class InventoryError(ValueError):
    def __init__(self, code, status, message):
        super().__init__(message)
        self.code, self.status, self.details = code, status, {}


def missing():
    raise InventoryError('NOT_FOUND', 404, 'Resource not found')


def _database(principal, role, database_id):
    with control_store.connect() as db:
        database = registry.get_database(db, database_id)
        binding = registry.get_binding(db, database_id)
    if database is None or binding is None:
        missing()
    try:
        auth = request_auth.authorize_database(principal, database_id, role,
            expected_generation_id=binding.applied_generation_id)
    except PermissionError:
        missing()
    if (database.state != 'READY' or binding.desired_revision <= 0 or
            binding.desired_revision != binding.applied_revision or not binding.applied_generation_id):
        missing()
    return database, binding, auth


def _permissions(role, schema):
    name = schema.sql_name
    if role != 'admin' and (name not in ('raw', 'sandbox', 'staging', 'marts') or schema.purpose != name):
        return {'browse': False, 'query': False, 'write': False}
    read = role == 'admin' or (role == 'bi_viewer' and name == 'marts') or (
        role == 'data_engineer' and name in ('raw', 'sandbox', 'staging', 'marts'))
    write = role == 'admin' or (role == 'data_engineer' and name in ('raw', 'sandbox'))
    return {'browse': read, 'query': read, 'write': write}


def _allowed_modes(role, schema):
    modes = ['read-only']
    if role in ('data_engineer', 'admin') and schema.sql_name in ('raw', 'sandbox'):
        modes.append('sandbox-write')
    if role == 'admin':
        modes.append('admin-write')
    return modes


def _schema(principal, role, schema_id, database_id=None):
    with control_store.connect() as db:
        schema = registry.get_schema(db, schema_id)
    if schema is None or (database_id is not None and schema.database_id != database_id):
        missing()
    database, binding, auth = _database(principal, role, schema.database_id)
    if not _permissions(role, schema)['browse']:
        missing()
    return schema, database, binding, auth


def list_databases(principal, selected_role):
    with control_store.connect() as db:
        ids = [row['id'] for row in db.execute('SELECT id FROM databases ORDER BY sql_catalog')]
    items = []
    for database_id in ids:
        try:
            database, binding, auth = _database(principal, selected_role, database_id)
        except InventoryError:
            continue
        allowed = _allowed_roles(principal, database_id)
        items.append(dict(id=database.id, sql_catalog=database.sql_catalog,
            display_name=database.display_name, state=database.state, revision=database.revision,
            binding_revision=binding.desired_revision, generation_id=binding.applied_generation_id,
            grant_revision=auth.grant_revision, allowed_roles=allowed,
            capabilities=dict(browse=True, query=True, manage=False,
                job_sql=selected_role in ('admin', 'data_engineer'), job_admin=selected_role == 'admin',
                stage_admin=selected_role == 'admin')))
    return {'items': items}


def _allowed_roles(principal, database_id):
    result = []
    for role in request_auth.ROLES:
        try:
            _database(principal, role, database_id)
            result.append(role)
        except InventoryError:
            pass
    return result


def list_schemas(principal, selected_role, database_id):
    _database(principal, selected_role, database_id)
    with control_store.connect() as db:
        ids = [row['id'] for row in db.execute('SELECT id FROM schemas WHERE database_id=? ORDER BY sql_name', (database_id,))]
        schemas = [registry.get_schema(db, sid) for sid in ids]
    items = []
    for schema in schemas:
        capabilities = _permissions(selected_role, schema)
        writable = capabilities['write'] and schema.sql_name in ('raw', 'sandbox') and schema.purpose == schema.sql_name
        capabilities.update(manual_ingest=writable, wap_build=selected_role in ('admin', 'data_engineer'),
            job_sql=selected_role in ('admin', 'data_engineer'), job_admin=selected_role == 'admin',
            stage_admin=selected_role == 'admin',
            wap_audit=selected_role in ('admin', 'data_engineer'), wap_models=selected_role in ('admin', 'data_engineer'),
            wap_publish=selected_role == 'admin', wap_rollback=selected_role == 'admin')
        if capabilities['browse']:
            items.append(dict(id=schema.id, database_id=database_id, sql_name=schema.sql_name,
                display_name=schema.display_name, purpose=schema.purpose, revision=schema.revision,
                capabilities=capabilities, allowed_modes=_allowed_modes(selected_role, schema)))
    return {'items': items}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http(request):
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            missing()
        raise InventoryError('CATALOG_UNAVAILABLE', 502, 'Catalog request failed') from None
    except Exception:
        raise InventoryError('CATALOG_UNAVAILABLE', 502, 'Catalog request failed') from None


def _credentials(database, binding):
    import logical_registry
    try:
        with control_store.connect() as db:
            if logical_registry.creation_mode(db, database.id) == 'logical_v1':
                resolved = logical_registry.resolve_binding(db, database.id, binding.applied_generation_id)
                return dict(service_ref=resolved['service_ref'], realm=resolved['realm'],
                    catalog=resolved['catalog'], generation_id=resolved['generation_id'],
                    **logical_registry.catalog_credentials(resolved))
    except ValueError:
        raise InventoryError('CATALOG_UNAVAILABLE', 502, 'Catalog binding credentials unavailable') from None
    profile = runtime_profile.current()
    if profile is None:
        raise InventoryError('FEATURE_UNAVAILABLE', 409, 'Database mode is not configured')
    try:
        config = runtime_profile.validate_catalog_manifest(runtime_profile.private_json(profile.catalog_auth_file))
        entry = config['databases'][database.id]
        if (entry['service_ref'], entry['realm'], entry['catalog'], entry['generation_id']) != (
                binding.polaris_service_ref, binding.polaris_realm, binding.polaris_catalog, binding.applied_generation_id):
            raise ValueError()
    except (ValueError, KeyError):
        raise InventoryError('CATALOG_UNAVAILABLE', 502, 'Catalog binding credentials unavailable') from None
    return entry


@contextmanager
def catalog_api(database, binding):
    entry = _credentials(database, binding)
    command = ['rtk', 'proxy', 'kubectl', '--context', 'colima-mac-studio-solo', '-n', 'shirokuma-dev']
    import logical_registry
    import logical_foundation
    try:
        with control_store.connect() as db:
            if logical_registry.creation_mode(db, database.id) == 'logical_v1':
                resolved = logical_registry.resolve_binding(db, database.id, binding.applied_generation_id)
                logical = runtime_profile.logical_config()
                config = logical_foundation.load(logical['provider_config_file'])
                if any(config[key] != logical[profile_key] or config[key] != resolved[binding_key]
                       for key, profile_key, binding_key in (
                           ('foundation_id', 'foundation_generation_id', 'foundation_id'),
                           ('revision', 'foundation_revision', 'foundation_revision'),
                           ('environment_id', 'environment_id', 'environment_id'),
                           ('warehouse_id', 'warehouse_id', 'warehouse_id'))):
                    raise ValueError()
                if (entry['service_ref'] != resolved['service_ref'] or
                        entry['service_ref'] != config['prefix'] + '-polaris' or
                        entry['realm'] != resolved['realm'] or entry['realm'] != config['polaris']['realm']):
                    raise ValueError()
                command = logical_foundation.kube_argv(config)
    except (ValueError, KeyError, OSError):
        raise InventoryError('CATALOG_UNAVAILABLE', 502, 'Catalog binding transport unavailable') from None
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    command += ['port-forward', '--address', '127.0.0.1', 'svc/' + entry['service_ref'], str(port) + ':8181']
    forward = None
    try:
        forward = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if forward.poll() is not None:
                raise OSError()
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.2):
                    break
            except OSError:
                time.sleep(.1)
        else:
            raise OSError()
        origin = 'http://127.0.0.1:' + str(port)
        basic = base64.b64encode((entry['client_id'] + ':' + entry['client_secret']).encode()).decode()
        token = _http(urllib.request.Request(origin + '/api/catalog/v1/oauth/tokens',
            urlencode({'grant_type': 'client_credentials', 'scope': 'PRINCIPAL_ROLE:ALL'}).encode(),
            {'Authorization': 'Basic ' + basic, 'Polaris-Realm': entry['realm'],
             'Content-Type': 'application/x-www-form-urlencoded'}))['access_token']
        if not isinstance(token, str) or not token:
            raise ValueError()
        base = origin + '/api/catalog/v1/' + quote(entry['catalog'], safe='')
        def request(path):
            return _http(urllib.request.Request(base + path, headers={
                'Authorization': 'Bearer ' + token, 'Polaris-Realm': entry['realm']}))
        yield request
    except InventoryError:
        raise
    except Exception:
        raise InventoryError('CATALOG_UNAVAILABLE', 502, 'Catalog connection failed') from None
    finally:
        if forward is not None:
            forward.terminate()
            try:
                forward.wait(timeout=5)
            except subprocess.TimeoutExpired:
                forward.kill()
                forward.wait()


def _path(schema, kind, name=None):
    path = '/namespaces/' + quote(schema.sql_name, safe='') + ('/tables' if kind == 'TABLE' else '/views')
    return path if name is None else path + '/' + quote(name, safe='')


def _metadata(api, schema, kind, name):
    result = api(_path(schema, kind, name))
    metadata = result.get('metadata')
    if not isinstance(metadata, dict):
        raise InventoryError('CATALOG_UNAVAILABLE', 502, 'Catalog metadata unavailable')
    return metadata


def _item(schema, role, name, kind, metadata, *, binding):
    identity = metadata.get('table-uuid' if kind == 'TABLE' else 'view-uuid')
    record = None
    if isinstance(identity, str) and identity:
        with control_store.connect() as db:
            record = registry.observe_object(db, schema.id, name, kind, identity, name)
    elif kind == 'TABLE':
        raise InventoryError('CATALOG_UNAVAILABLE', 502, 'Table identity unavailable')
    stable = record is not None
    ref = dict(database_id=schema.database_id, schema_id=schema.id, object_id=record.id,
        incarnation=record.incarnation, binding_revision=binding.desired_revision,
        generation_id=binding.applied_generation_id) if stable else None
    return dict(id=record.id if stable else None, physical_name=name, display_name=name, kind=kind, ref=ref,
        capabilities=dict(resolve=stable, columns=stable, preview=stable,
            export=stable and kind == 'TABLE' and _permissions(role, schema)['query'],
            snapshots=stable and kind == 'TABLE', ddl=stable and _permissions(role, schema)['write']))


def _encode_cursor(value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(hmac.digest(_CURSOR_KEY, raw, 'sha256') + raw).decode().rstrip('=')


def _decode_cursor(token, scope):
    try:
        if not isinstance(token, str) or len(token) > 8192:
            raise ValueError()
        raw = base64.b64decode(token + '=' * (-len(token) % 4), altchars=b'-_', validate=True)
        if not hmac.compare_digest(raw[:32], hmac.digest(_CURSOR_KEY, raw[32:], 'sha256')):
            raise ValueError()
        value = json.loads(raw[32:])
        if set(value) != {'scope', 'offset', 'digest'} or value['scope'] != scope or type(value['offset']) is not int or value['offset'] < 0:
            raise ValueError()
        return value
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise InventoryError('STALE_CURSOR', 409, 'Invalid or stale inventory cursor') from None


def list_objects(principal, selected_role, schema_id, *, kind=None, q='', limit=50, cursor=None):
    if kind not in (None, 'TABLE', 'VIEW') or not isinstance(q, str) or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Invalid object listing parameters')
    schema, database, binding, auth = _schema(principal, selected_role, schema_id)
    scope = [principal.principal_id, selected_role, database.id, schema.id, binding.applied_generation_id,
        schema.revision, binding.desired_revision, auth.grant_revision, kind, q, limit]
    previous = _decode_cursor(cursor, scope) if cursor is not None else None
    names = []
    with catalog_api(database, binding) as api:
        for object_kind in ([kind] if kind else ['TABLE', 'VIEW']):
            token, seen = None, set()
            while True:
                params = {'page-size': 100}
                if token:
                    params['page-token'] = token
                page = api(_path(schema, object_kind) + '?' + urlencode(params))
                for identifier in page.get('identifiers', []):
                    name = identifier.get('name')
                    if not isinstance(name, str) or not name or identifier.get('namespace') != [schema.sql_name]:
                        raise InventoryError('CATALOG_UNAVAILABLE', 502, 'Invalid catalog listing')
                    if q.casefold() in name.casefold():
                        names.append((object_kind, name))
                token = page.get('next-page-token')
                if not token:
                    break
                if not isinstance(token, str) or token in seen:
                    raise InventoryError('CATALOG_UNAVAILABLE', 502, 'Invalid catalog pagination')
                seen.add(token)
        names = sorted(set(names))
        digest = hashlib.sha256(json.dumps(names).encode()).hexdigest()
        if previous and previous['digest'] != digest:
            raise InventoryError('STALE_CURSOR', 409, 'Inventory changed; restart listing')
        offset = previous['offset'] if previous else 0
        items = [_item(schema, selected_role, name, typ, _metadata(api, schema, typ, name), binding=binding)
            for typ, name in names[offset:offset + limit]]
    offset += len(items)
    return {'items': items, 'next_cursor': _encode_cursor(dict(scope=scope, offset=offset, digest=digest)) if offset < len(names) else None}


def _native_name(name):
    """Parse a SQL identifier path, never a SQL expression or statement."""
    if not isinstance(name, str):
        raise ValueError('Object name is required')
    component = re.compile(r'\s*(?:"((?:""|[^"\x00-\x1f])+)"|([A-Za-z_][A-Za-z0-9_]*))\s*')
    parts, position = [], 0
    while True:
        match = component.match(name, position)
        if match is None or len(parts) == 3:
            raise ValueError('Invalid native object name')
        quoted, plain = match.groups()
        parts.append(quoted.replace('""', '"') if quoted is not None else plain.lower())
        position = match.end()
        if position == len(name):
            return parts
        if name[position] != '.':
            raise ValueError('Invalid native object name')
        position += 1


def resolve_object(principal, selected_role, database_id, schema_id, name):
    parts = _native_name(name)
    schema, database, binding, _ = _schema(principal, selected_role, schema_id, database_id)
    if len(parts) > 1:
        with control_store.connect() as db:
            if len(parts) == 3:
                target = db.execute('SELECT id FROM databases WHERE sql_catalog=?', (parts[0],)).fetchone()
                if target is None or (target['id'] != database_id and selected_role != 'admin'):
                    missing()
                database_id = target['id']
            target_schema = db.execute('SELECT id FROM schemas WHERE database_id=? AND sql_name=?',
                (database_id, parts[-2])).fetchone()
        if target_schema is None:
            missing()
        schema, database, binding, _ = _schema(principal, selected_role, target_schema['id'], database_id)
    name = parts[-1]
    # Listing determines the kind without treating arbitrary REST errors as absence.
    with catalog_api(database, binding) as api:
        for kind in ('TABLE', 'VIEW'):
            token, seen = None, set()
            while True:
                path = _path(schema, kind) + '?' + urlencode(dict({'page-size': 100}, **({'page-token': token} if token else {})))
                page = api(path)
                if any(i.get('name') == name and i.get('namespace') == [schema.sql_name] for i in page.get('identifiers', [])):
                    item = _item(schema, selected_role, name, kind, _metadata(api, schema, kind, name), binding=binding)
                    if not item['capabilities']['resolve']:
                        missing()
                    return {'object': item, 'ref': item['ref']}
                token = page.get('next-page-token')
                if not token:
                    break
                if not isinstance(token, str) or token in seen:
                    raise InventoryError('CATALOG_UNAVAILABLE', 502, 'Invalid catalog pagination')
                seen.add(token)
    missing()


def object_detail(principal, selected_role, object_id, *, detail, snapshot_id=None):
    if detail not in ('columns', 'preview', 'snapshots', 'ddl') or (snapshot_id is not None and (
            detail != 'preview' or not isinstance(snapshot_id, str) or not re.fullmatch(r'[0-9]+', snapshot_id))):
        raise ValueError('Invalid object detail parameters')
    with control_store.connect() as db:
        obj = registry.get_object(db, object_id)
    if obj is None:
        missing()
    schema, database, binding, _ = _schema(principal, selected_role, obj.schema_id)
    if (detail == 'ddl' and not _permissions(selected_role, schema)['write']) or (detail == 'snapshots' and obj.kind != 'TABLE'):
        missing()
    with catalog_api(database, binding) as api:
        metadata = _metadata(api, schema, obj.kind, obj.physical_name)
    identity = metadata.get('table-uuid' if obj.kind == 'TABLE' else 'view-uuid')
    if identity != obj.backend_identity:
        if isinstance(identity, str) and identity:
            _item(schema, selected_role, obj.physical_name, obj.kind, metadata, binding=binding)
        missing()
    item = _item(schema, selected_role, obj.physical_name, obj.kind, metadata, binding=binding)
    if not item['capabilities'][detail]:
        missing()
    result = {'object': item, 'ref': item['ref'], 'detail': detail}
    if detail == 'columns':
        current = metadata.get('current-schema-id')
        if obj.kind == 'VIEW':
            version = next((v for v in metadata.get('versions', []) if v.get('version-id') == metadata.get('current-version-id')), {})
            current = version.get('schema-id')
        schemas = metadata.get('schemas', [])
        sql_schema = next((s for s in schemas if s.get('schema-id') == current), None)
        if sql_schema is None:
            sql_schema = metadata.get('schema', {})
        result['columns'] = [dict(name=f['name'], type=f['type'], nullable=not f.get('required', False)) for f in sql_schema.get('fields', [])]
    elif detail == 'snapshots':
        result['snapshots'] = [dict(snapshot_id=str(s['snapshot-id']), parent_id=str(s['parent-snapshot-id']) if s.get('parent-snapshot-id') is not None else None,
            timestamp_ms=str(s['timestamp-ms']), summary=s.get('summary', {})) for s in metadata.get('snapshots', [])]
    else:
        if snapshot_id is not None and (obj.kind != 'TABLE' or snapshot_id not in {str(s['snapshot-id']) for s in metadata.get('snapshots', [])}):
            missing()
        import query_context
        import query_service
        context = query_context.resolve_context(principal, database.id, schema.id, selected_role,
            warehouse_id='LOCAL', expected_binding_revision=binding.desired_revision)
        if context.generation_id != binding.applied_generation_id:
            raise InventoryError('STALE_CONTEXT', 409, 'Database generation changed')
        qualified = '.'.join(registry.quote_identifier(s) for s in (context.sql_catalog, context.sql_schema, obj.physical_name))
        if detail == 'preview':
            statement = 'SELECT * FROM ' + qualified + (' FOR VERSION AS OF ' + snapshot_id if snapshot_id is not None else '') + ' LIMIT 50'
        else:
            statement = 'SHOW CREATE ' + obj.kind + ' ' + qualified
        result['query'] = query_service.submit(principal, context, statement, mode='read-only', max_rows=50 if detail == 'preview' else 5,
            request_id=str(uuid.uuid4()), worksheet_id='inventory:' + obj.id + ':' + detail)
    return result
