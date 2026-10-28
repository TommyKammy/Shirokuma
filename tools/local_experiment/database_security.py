"""Render complementary file ACL and OPA guards for a validated v2 manifest.

Install both access controls: their decisions compose with AND. The file ACL
owns ordinary object privileges; OPA closes the table-procedure escape hatch.
"""
import json
import re

from trino_auth import scoped_username, validate_database_config


def validate_identity_config(config):
    """Validate public identities without accepting or inventing credentials."""
    from data_management import validate_sql_name
    try:
        if (not isinstance(config, dict) or set(config) != {'version', 'platform_admin', 'databases'}
                or type(config['version']) is not int or config['version'] not in (1, 2)
                or config['platform_admin'] != 'shirokuma_admin'
                or not isinstance(config['databases'], dict) or not config['databases']):
            raise ValueError()
        catalogs, users = set(), {'shirokuma_admin'}
        for key, db in config['databases'].items():
            if (not isinstance(key, str) or not key.strip() or any(c in key for c in '\r\n\x00')
                    or not isinstance(db, dict) or set(db) != {'sql_catalog', 'generation_id', 'roles'}
                    or not isinstance(db['generation_id'], str) or not db['generation_id'].strip()
                    or any(c in db['generation_id'] for c in '\r\n\x00')
                    or not isinstance(db['roles'], dict) or set(db['roles']) not in (
                        ({'bi_viewer', 'data_engineer'}, {'bi_viewer', 'data_engineer', 'admin'})
                        if config['version'] == 2 else ({'bi_viewer', 'data_engineer'},))):
                raise ValueError()
            catalog = validate_sql_name(db['sql_catalog'])
            if catalog in catalogs:
                raise ValueError()
            catalogs.add(catalog)
            for role, user in db['roles'].items():
                if user != scoped_username(key, role) or user in users:
                    raise ValueError()
                users.add(user)
        return config
    except (KeyError, TypeError, ValueError):
        raise ValueError('invalid public database identities') from None


def public_identities(config):
    validate_database_config(config)
    modern = any('admin' in db['roles'] for db in config['databases'].values())
    return dict(version=2 if modern else 1, platform_admin=config['platform_admin']['username'], databases={
        key: dict(sql_catalog=db['sql_catalog'], generation_id=db['generation_id'],
                  roles={role: entry['username'] for role, entry in db['roles'].items()})
        for key, db in config['databases'].items()})


def render_file_acl(config):
    return render_file_acl_identities(public_identities(config))


def render_file_acl_identities(config):
    validate_identity_config(config)
    admin = re.escape(config['platform_admin'])
    catalogs, schemas, tables, queries = [], [], [], []
    functions, procedures, catalog_properties = [], [], []
    for database in config['databases'].values():
        catalog = re.escape(database['sql_catalog'])
        catalogs.append(dict(user=admin, catalog=catalog, allow='all'))
        schemas.append(dict(user=admin, catalog=catalog, owner=True))
        tables.append(dict(user=admin, catalog=catalog,
            privileges=['SELECT', 'INSERT', 'DELETE', 'UPDATE', 'OWNERSHIP', 'GRANT_SELECT']))
        functions.append(dict(user=admin, catalog=catalog,
            privileges=['EXECUTE', 'GRANT_EXECUTE', 'OWNERSHIP']))
        procedures.append(dict(user=admin, catalog=catalog, privileges=['EXECUTE', 'GRANT_EXECUTE']))
        catalog_properties.append(dict(user=admin, catalog=catalog, allow=True))
        for role, username in database['roles'].items():
            user = re.escape(username)
            if role == 'admin':
                registered = 'raw|sandbox|staging|marts'
                catalogs.append(dict(user=user, catalog=catalog, allow='all'))
                schemas.append(dict(user=user, catalog=catalog, schema=registered, owner=True))
                tables.append(dict(user=user, catalog=catalog, schema=registered,
                    privileges=['SELECT', 'INSERT', 'DELETE', 'UPDATE', 'OWNERSHIP']))
                tables.append(dict(user=user, catalog=catalog, schema='information_schema', privileges=['SELECT']))
                queries.append(dict(user=user, allow=['execute']))
                continue
            catalogs.append(dict(user=user, catalog=catalog, allow='all' if role == 'data_engineer' else 'read-only'))
            schemas.append(dict(user=user, catalog=catalog, owner=False))
            if role == 'data_engineer':
                tables.append(dict(user=user, catalog=catalog, schema='raw|sandbox',
                    privileges=['SELECT', 'INSERT', 'DELETE', 'UPDATE', 'OWNERSHIP']))
                tables.append(dict(user=user, catalog=catalog, schema='staging', privileges=['SELECT']))
            tables.append(dict(user=user, catalog=catalog, schema='marts', privileges=['SELECT']))
            queries.append(dict(user=user, allow=['execute']))
    catalogs.append(dict(allow='none'))
    schemas.append(dict(owner=False))
    tables.append(dict(privileges=[]))
    queries.extend([dict(user=admin, allow=['execute', 'view', 'kill']), dict(allow=[])])
    functions.append(dict(privileges=[]))
    procedures.append(dict(privileges=[]))
    catalog_properties.append(dict(allow=False))
    return dict(catalogs=catalogs, schemas=schemas, tables=tables, queries=queries,
        impersonation=[], procedures=procedures, functions=functions,
        authorization=[], system_information=[dict(user=admin, allow=['read', 'write']), dict(allow=[])],
        system_session_properties=[dict(user=admin, allow=True), dict(allow=False)],
        catalog_session_properties=catalog_properties)


def render_opa_policy(config):
    return render_opa_policy_identities(public_identities(config))


def render_opa_policy_identities(config):
    validate_identity_config(config)
    users = [config['platform_admin']]
    for database in config['databases'].values():
        users.extend(database['roles'].values())
    known = json.dumps({user: True for user in users}, sort_keys=True)
    catalogs = json.dumps({db['sql_catalog']: True for db in config['databases'].values()}, sort_keys=True)
    engineers = json.dumps({db['roles']['data_engineer']: db['sql_catalog']
        for db in config['databases'].values()}, sort_keys=True)
    policy = '''package shirokuma
import rego.v1

default allow := false
known_users := %s
registered_catalogs := %s
engineer_catalogs := %s

allow if {
    known_users[input.context.identity.user]
    is_string(input.action.operation)
    input.action.operation != ""
    input.action.operation != "ExecuteTableProcedure"
}

allow if {
    input.context.identity.user == "shirokuma_admin"
    table_procedure
    registered_catalogs[input.action.resource.table.catalogName]
}

allow if {
    table_procedure
    engineer_catalogs[input.context.identity.user] == input.action.resource.table.catalogName
    input.action.resource.table.schemaName in {"raw", "sandbox"}
}

table_procedure if {
    input.action.operation == "ExecuteTableProcedure"
    is_string(input.action.resource.table.catalogName)
    input.action.resource.table.catalogName != ""
    is_string(input.action.resource.table.schemaName)
    input.action.resource.table.schemaName != ""
    is_string(input.action.resource.table.tableName)
    input.action.resource.table.tableName != ""
}
''' % (known, catalogs, engineers)
    if config['version'] == 1:
        return policy
    admins = json.dumps({db['roles']['admin']: db['sql_catalog']
        for db in config['databases'].values() if 'admin' in db['roles']}, sort_keys=True)
    # The file ACL supplies object privileges. OPA independently confines the
    # scoped admin, including the table-procedure path outside ordinary ACLs.
    policy = policy.replace('    known_users[input.context.identity.user]\n',
        '    known_users[input.context.identity.user]\n    not scoped_admin_catalogs[input.context.identity.user]\n')
    return policy + '''
scoped_admin_catalogs := %s

allow if {
    scoped_admin_catalogs[input.context.identity.user]
    input.action.operation == "ExecuteQuery"
}

allow if {
    catalog := scoped_admin_catalogs[input.context.identity.user]
    input.action.operation in {"AccessCatalog", "ShowSchemas", "FilterCatalogs"}
    input.action.resource.catalog.name == catalog
}

allow if {
    catalog := scoped_admin_catalogs[input.context.identity.user]
    input.action.operation in {"CreateSchema", "ShowCreateSchema", "ShowTables", "FilterSchemas"}
    input.action.resource.schema.catalogName == catalog
    input.action.resource.schema.schemaName in {"raw", "sandbox", "staging", "marts"}
}

allow if {
    catalog := scoped_admin_catalogs[input.context.identity.user]
    input.action.operation in {"SelectFromColumns", "CreateTable", "DropTable", "InsertIntoTable", "UpdateTableColumns",
        "ShowCreateTable", "ShowColumns", "FilterTables", "FilterColumns", "ExecuteTableProcedure"}
    input.action.resource.table.catalogName == catalog
    input.action.resource.table.schemaName in {"raw", "sandbox", "staging", "marts"}
    is_string(input.action.resource.table.tableName)
    input.action.resource.table.tableName != ""
}

# Catalog-local metadata reads support normal schema discovery and verification.
# They confer no metadata write, system catalog, or other database authority.
allow if {
    catalog := scoped_admin_catalogs[input.context.identity.user]
    input.action.operation in {"SelectFromColumns", "ShowColumns", "FilterTables", "FilterColumns"}
    input.action.resource.table.catalogName == catalog
    input.action.resource.table.schemaName == "information_schema"
    is_string(input.action.resource.table.tableName)
    input.action.resource.table.tableName != ""
}
''' % admins
