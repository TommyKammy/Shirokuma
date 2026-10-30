"""Private, trusted-local-project offline dbt feasibility compiler; never executes SQL.

``compile_project`` accepts a SQL dbt project, current SQL ``catalog`` and source
``source_schema``. ``staging_materialization`` may explicitly select table or
ephemeral; omission preserves the project's defaults (including unsupported views).
The copied project receives vars shirokuma_source_database,
shirokuma_source_schema and shirokuma_candidate_schema='sandbox', and optionally
shirokuma_staging_materialization. These are the only project overrides.

Local project macros are trusted code, not an upload security boundary. External
packages, custom materializations and SQL-executing custom macros are outside the
supported subset. This module provides no execution, credentials or runtime API.
"""
from contextlib import contextmanager
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

import yaml

import data_management as registry
import manual_wap


class DbtError(ValueError):
    """Fixed private-operator error code; no captured dbt output is exposed."""

    def __init__(self, code, message, issues=None):
        self.compatibility = compatibility(issues or [], supported=False)
        self.code = code
        super().__init__(f'{code}: {message}')


def compatibility(issues=None, *, supported=True):
    return dict(version=2, executor="sql_wap", status="supported" if supported else "blocked",
                issues=issues or [])


def _fail(code, message):
    raise DbtError(code, message)


_CONFIGS = {'pre-hook', 'post-hook', 'on-run-start', 'on-run-end', 'hooks',
            'grants', 'persist_docs', 'sql_header', 'store_failures',
            'store_failures_as', 'incremental_strategy', 'full_refresh'}
_TESTS = {'unique', 'not_null', 'relationships', 'accepted_values'}
_SYSTEM = {'information_schema', 'system', 'sys', 'pg_catalog', 'dbt_test__audit'}


def _check_config(value):
    if isinstance(value, dict):
        for key, item in value.items():
            key = str(key).lstrip('+').replace('_', '-')
            if key in ('meta', 'docs', 'columns'):
                continue
            if key in {k.replace('_', '-') for k in _CONFIGS} and item:
                _fail('UNSUPPORTED_CONFIG', 'hooks or write-side configuration unsupported')
            if key == 'contract' and isinstance(item, dict) and item.get('enforced'):
                _fail('UNSUPPORTED_CONFIG', 'contracts unsupported')
            _check_config(item)
    elif isinstance(value, list):
        for item in value:
            _check_config(item)


def _preflight(project):
    """Capture checked source bytes; deliberately ignore generated artifacts."""
    try:
        project_bytes = (project / 'dbt_project.yml').read_bytes()
        config = yaml.safe_load(project_bytes)
    except (OSError, yaml.YAMLError):
        _fail('PREFLIGHT_UNSUPPORTED', 'readable dbt_project.yml required')
    if not isinstance(config, dict) or not isinstance(config.get('name'), str):
        _fail('PREFLIGHT_UNSUPPORTED', 'named SQL project required')
    _check_config(config)
    for name in ('packages.yml', 'dependencies.yml'):
        path = project / name
        if path.exists():
            try:
                dependencies = yaml.safe_load(path.read_text())
            except (OSError, yaml.YAMLError):
                _fail('PREFLIGHT_UNSUPPORTED', 'invalid dependency declaration')
            if dependencies:
                if not isinstance(dependencies, dict) or any(dependencies.values()):
                    _fail('PREFLIGHT_UNSUPPORTED', 'external dependencies unsupported')
    excluded = {'target', 'logs', 'dbt_packages', '.git', '.venv', '__pycache__'}
    for key in ('target-path', 'log-path', 'packages-install-path'):
        if isinstance(config.get(key), str):
            excluded.add(config[key])
    roots = {'dbt_project.yml'}
    for key, default in (('model-paths', ['models']), ('macro-paths', ['macros']),
                         ('test-paths', ['tests']), ('seed-paths', ['seeds']),
                         ('snapshot-paths', ['snapshots']), ('analysis-paths', ['analyses'])):
        paths = config.get(key, default)
        if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
            _fail('PREFLIGHT_UNSUPPORTED', 'local relative source paths required')
        for path in paths:
            rel = Path(path)
            if rel.is_absolute() or '..' in rel.parts or not rel.parts:
                _fail('PREFLIGHT_UNSUPPORTED', 'local relative source paths required')
            for source in (project / rel).rglob('*'):
                if not source.is_file() or any(p in excluded for p in source.relative_to(project).parts):
                    continue
                if source.name == 'profiles.yml':
                    continue
                if key in ('seed-paths', 'snapshot-paths'):
                    _fail('PREFLIGHT_UNSUPPORTED', 'seeds and snapshots unsupported')
                if source.suffix not in ('.sql', '.yml', '.yaml', '.md'):
                    _fail('PREFLIGHT_UNSUPPORTED', 'SQL-only project required')
                roots.add(source.relative_to(project).as_posix())
    digest = hashlib.sha256()
    files = {}
    for rel in sorted(roots):
        raw = project_bytes if rel == 'dbt_project.yml' else (project / rel).read_bytes()
        files[rel] = raw
        digest.update(rel.encode() + b'\0' + raw + b'\0')
        text = raw.decode()
        if rel.endswith(('.yml', '.yaml')):
            try:
                _check_config(yaml.safe_load(text))
            except yaml.YAMLError:
                _fail('PREFLIGHT_UNSUPPORTED', 'invalid project YAML')
        if rel.endswith('.sql'):
            if re.search(r'{%[-+]?\s*(materialization|snapshot)\b', text):
                _fail('PREFLIGHT_UNSUPPORTED', 'custom materializations and snapshots unsupported')
            # Practical preflight for inline config; evaluated config is also checked.
            if re.search(r'''\b(pre_hook|post_hook|sql_header|store_failures|grants|persist_docs)\b\s*['"]?\s*[:=]''', text):
                _fail('UNSUPPORTED_CONFIG', 'inline write-side configuration unsupported')
            if re.search(r'\b(run_query\s*\(|statement\s*\(|adapter\s*\.\s*(execute|add_query)\s*\()', text):
                _fail('PREFLIGHT_UNSUPPORTED', 'custom macro SQL execution unsupported')
    return config, files, digest.hexdigest()


def _sql_tree(sql):
    import sqlglot
    from sqlglot import exp
    if sqlglot.__version__ != '28.0.0':
        _fail('INVALID_SELECT', 'sqlglot 28.0.0 required')
    try:
        trees = sqlglot.parse(sql, read='trino', error_level=sqlglot.ErrorLevel.RAISE)
        if len(trees) != 1 or not isinstance(trees[0], (exp.Select, exp.SetOperation)):
            _fail('INVALID_SELECT', 'one compiled read SELECT required')
        return trees[0]
    except sqlglot.errors.SqlglotError:
        _fail('INVALID_SELECT', 'compiled SQL could not be parsed')


def _physical(tree):
    from sqlglot import exp
    from sqlglot.optimizer.scope import traverse_scope
    try:
        result = []
        for scope in traverse_scope(tree):
            for _, (_, source) in scope.selected_sources.items():
                if isinstance(source, exp.Table):
                    result.append((scope, source))
        return result
    except Exception:
        _fail('INVALID_SELECT', 'ambiguous SQL source scope')


def _identity(table):
    from sqlglot import exp
    if any(not isinstance(table.args.get(k), exp.Identifier) for k in ('catalog', 'db', 'this')):
        _fail('INVALID_RELATION', 'exact three-part physical relation required')
    result = tuple(table.args[k].name if table.args[k].args.get('quoted')
                   else table.args[k].name.lower() for k in ('catalog', 'db', 'this'))
    if result[1].lower() in _SYSTEM or '$' in result[2]:
        _fail('INVALID_RELATION', 'metadata and system relations unsupported')
    return result


def normalize_select(compiled_sql, bindings, *, database_id):
    """Replace exact physical relations with manual-WAP aliases, without execution.

    Bindings must include every used relation and no unused entries. Repeated
    reads of a relation reuse a source alias. Distinct relation keys may not bind
    the same resource, since that would make relation identity ambiguous.
    """
    from sqlglot import exp
    if not isinstance(bindings, dict) or not isinstance(database_id, str) or not database_id:
        _fail('INVALID_BINDING', 'exact bindings and database_id required')
    seen = set()
    for key, ref in bindings.items():
        if (not isinstance(key, tuple) or len(key) != 3
                or any(not isinstance(p, str) or not p for p in key)
                or not isinstance(ref, registry.ResourceRef) or ref.database_id != database_id):
            _fail('INVALID_BINDING', 'exact same-database ResourceRef bindings required')
        if ref in seen:
            _fail('INVALID_BINDING', 'duplicate resource binding')
        seen.add(ref)
    tree = _sql_tree(compiled_sql)
    physical = _physical(tree)
    identities = {_identity(table) for _, table in physical}
    if identities != set(bindings):
        _fail('INVALID_BINDING', 'missing or unused relation binding')
    catalogs = {key[0] for key in identities}
    if len(catalogs) > 1:
        _fail('INVALID_BINDING', 'cross-catalog SELECT unsupported')
    occupied = {n.name.lower() for n in tree.find_all(exp.Identifier)}
    aliases = {}
    index = 1
    for key in sorted(identities):
        while f'dbt_source_{index}' in occupied:
            index += 1
        aliases[key] = f'dbt_source_{index}'
        occupied.add(aliases[key])
        index += 1
    for scope, table in physical:
        key = _identity(table)
        original = table.this.copy()
        if not table.args.get('alias'):
            table.set('alias', exp.TableAlias(this=original))
        # Qualified column references need their physical catalog/schema removed;
        # the original relation identifier remains the visible table alias.
        for column in scope.columns:
            if column.catalog or column.db:
                parts = tuple(column.args.get(k) for k in ('catalog', 'db', 'table')
                              if column.args.get(k) is not None)
                names = tuple(p.name if p.args.get('quoted') else p.name.lower()
                              for p in parts) if all(isinstance(p, exp.Identifier) for p in parts) else None
                if names != key[-len(parts):]:
                    continue
                column.set('catalog', None)
                column.set('db', None)
                column.set('table', table.args['alias'].this.copy())
        table.set('this', exp.to_identifier(aliases[key]))
        table.set('db', None)
        table.set('catalog', None)
    if any(column.catalog or column.db for column in tree.find_all(exp.Column)):
        _fail('INVALID_SELECT', 'unresolved qualified column reference')
    sources = {aliases[key]: bindings[key] for key in sorted(identities)}
    try:
        sql = manual_wap._compile_model(tree.sql(dialect='trino'), sources)
    except (ValueError, RuntimeError):
        _fail('INVALID_SELECT', 'compiled SELECT exceeds manual WAP read subset')
    return {'sql': sql, 'sources': sources}


def _semantic_config(nodes):
    """Inspect resolved execution config only; issue fields never contain values."""
    issues, properties = [], {}
    def issue(uid, code, setting):
        issues.append(dict(node_id=uid, code=code, setting=setting))
    for uid, node in sorted(nodes.items()):
        kind = node.get('resource_type')
        if kind not in ('model', 'test'):
            continue
        config = node.get('config', {})
        code = 'UNSUPPORTED_MODEL_CONFIG' if kind == 'model' else 'UNSUPPORTED_TEST_CONFIG'
        prefix = '' if kind == 'model' else 'test.'
        # Empty/default dbt-injected settings have no write effect.
        for setting in sorted(_CONFIGS | {'column_types', 'unique_key', 'partition_by',
                'cluster_by', 'table_format', 'file_format', 'location', 'catalog_name',
                'indexes', 'bind', 'dist', 'sort', 'sql_where'}):
            value = config.get(setting)
            if value and (not isinstance(value, dict) or any(value.values())):
                issue(uid, code, prefix + setting.replace('-', '_'))
        if config.get('contract', {}).get('enforced'):
            issue(uid, code, prefix + 'contract')
        if kind == 'model':
            if config.get('materialized') not in ('table', 'ephemeral', 'view'):
                issue(uid, 'UNSUPPORTED_MATERIALIZATION', 'materialized')
            if config.get('materialized') == 'view' and config.get('view_security') not in (None, 'invoker'):
                issue(uid, 'UNSUPPORTED_VIEW_SECURITY', 'view_security')
            if config.get('on_table_exists') is not None:
                issue(uid, code, 'on_table_exists')
            for setting, default in (('on_schema_change', 'ignore'),
                    ('on_configuration_change', 'apply'), ('lookback', 1)):
                if config.get(setting, default) != default:
                    issue(uid, code, setting)
            for setting in ('batch_size', 'begin', 'event_time', 'concurrent_batches', 'on_error'):
                if config.get(setting) is not None:
                    issue(uid, code, setting)
            props = config.get('properties')
            properties[uid] = {}
            if props not in (None, {}):
                if config.get('materialized') == 'view':
                    issue(uid, 'UNSUPPORTED_VIEW_PROPERTIES', 'properties')
                elif (isinstance(props, dict) and set(props) == {'format'}
                        and isinstance(props['format'], str)
                        and re.fullmatch(r"\s*'PARQUET'\s*", props['format'], re.IGNORECASE)):
                    if config.get('materialized') == 'table':
                        properties[uid] = {'format': 'PARQUET'}
                else:
                    issue(uid, 'UNSUPPORTED_TABLE_PROPERTIES', 'properties')
        else:
            for setting, default in (('severity', 'error'), ('fail_calc', 'count(*)'),
                    ('error_if', '!=0'), ('warn_if', '!=0')):
                value = config.get(setting, default)
                if not isinstance(value, str) or re.sub(r'\s+', '', value).lower() != default:
                    issue(uid, code, 'test.' + setting)
            if config.get('limit') is not None:
                issue(uid, code, 'test.limit')
    if issues:
        raise DbtError(issues[0]['code'],
            'SQL/WAP supports table, sandbox invoker view and ephemeral models with zero-failure COUNT tests', issues)
    return properties


def _validate_manifest(manifest, *, catalog, source_schema):
    if any(manifest.get(k) for k in ('exposures', 'metrics', 'semantic_models', 'saved_queries', 'unit_tests')):
        _fail('INVALID_MANIFEST', 'unexpected resource type')
    nodes = manifest.get('nodes', {})
    source_nodes = manifest.get('sources', {})
    if not isinstance(nodes, dict) or not isinstance(source_nodes, dict):
        _fail('INVALID_MANIFEST', 'nodes and sources required')
    table_properties = _semantic_config(nodes)
    models, tests, sources = [], [], []
    known = set(nodes) | set(source_nodes)
    relations = {}
    for uid, node in sorted(source_nodes.items()):
        _check_config(node.get('config', {}))
        relation = (node.get('database'), node.get('schema'), node.get('identifier'))
        if node.get('resource_type') != 'source':
            _fail('INVALID_MANIFEST', 'unexpected source resource')
        _validate_relation(relation, catalog)
        if relation[1] != source_schema:
            _fail('INVALID_RELATION', 'source relations must use requested source schema')
        if relation in relations:
            _fail('INVALID_RELATION', 'ambiguous manifest relation')
        relations[relation] = uid
        sources.append({'unique_id': uid, 'name': node.get('name'), 'relation': list(relation)})
    for uid, node in sorted(nodes.items()):
        kind = node.get('resource_type')
        if kind not in ('model', 'test'):
            _fail('INVALID_MANIFEST', 'only SQL models and generic tests supported')
        config = node.get('config', {})
        _check_config(config)
        if node.get('language', 'sql') != 'sql':
            _fail('INVALID_MANIFEST', 'SQL-only models required')
        dependencies = node.get('depends_on', {}).get('nodes', [])
        if any(dep not in known or (dep in nodes and nodes[dep].get('resource_type') != 'model')
               for dep in dependencies):
            _fail('INVALID_DEPENDENCY', 'unknown or unsupported dependency')
        sql = node.get('compiled_code')
        if not isinstance(sql, str) or not sql.strip():
            _fail('INVALID_MANIFEST', 'compiled SQL required for every selected node')
        selected = {'unique_id': uid, 'name': node.get('name'),
                    'depends_on': sorted(dependencies), 'compiled_sql': sql}
        if kind == 'model':
            materialized = config.get('materialized')
            if materialized not in ('table', 'ephemeral', 'view'):
                _fail('UNSUPPORTED_MATERIALIZATION', 'only table, sandbox invoker view and ephemeral models supported')
            relation = (node.get('database'), node.get('schema'), node.get('alias'))
            _validate_relation(relation, catalog)
            if relation[1] != 'sandbox':
                _fail('INVALID_RELATION', 'model relations must use sandbox')
            if relation in relations:
                _fail('INVALID_RELATION', 'ambiguous manifest relation')
            # Ephemeral identities still cannot collide with physical resources.
            relations[relation] = uid
            selected.update(materialized=materialized, table_properties=table_properties[uid],
                            relation=list(relation) if materialized != 'ephemeral' else None)
            if materialized == 'view':
                selected['view_security'] = 'INVOKER'
            models.append(selected)
        else:
            metadata = node.get('test_metadata', {})
            if metadata.get('name') not in _TESTS or metadata.get('namespace') not in (None, 'dbt'):
                _fail('INVALID_MANIFEST', 'only built-in generic tests supported')
            selected.update(test_type=metadata['name'], relation=None)
            tests.append(selected)
    remaining = {m['unique_id']: set(m['depends_on']) & set(nodes) for m in models}
    order = []
    while remaining:
        ready = sorted(uid for uid, deps in remaining.items() if not deps)
        if not ready:
            _fail('INVALID_DEPENDENCY', 'model dependency cycle')
        for uid in ready:
            order.append(uid)
            remaining.pop(uid)
        for deps in remaining.values():
            deps.difference_update(ready)
    model_by_id = {m['unique_id']: m for m in models}
    physical_relations = {tuple(s['relation']): s['unique_id'] for s in sources}
    physical_relations.update({tuple(m['relation']): m['unique_id'] for m in models if m['relation']})
    def ancestors(uid):
        found, pending = set(), list(nodes[uid].get('depends_on', {}).get('nodes', []))
        while pending:
            dep = pending.pop()
            if dep in found:
                continue
            found.add(dep)
            if dep in nodes:
                pending.extend(nodes[dep].get('depends_on', {}).get('nodes', []))
        return found
    for item in models + tests:
        tree = _sql_tree(item['compiled_sql'])
        used = {_identity(table) for _, table in _physical(tree)}
        permitted = ancestors(item['unique_id'])
        view_deps, pending = set(), list(item['depends_on'])
        while pending:
            dep = model_by_id.get(pending.pop(), {})
            if dep.get('materialized') == 'view':
                view_deps.add(dep['unique_id'])
            elif dep.get('materialized') == 'ephemeral':
                pending.extend(dep['depends_on'])
        if item.get('materialized') == 'view' and (view_deps or any(
                model_by_id.get(physical_relations.get(rel), {}).get('materialized') == 'view' for rel in used)):
            raise DbtError('UNSUPPORTED_VIEW_DEPENDENCY', 'VIEW to VIEW dependencies are unsupported',
                [dict(node_id=item['unique_id'], code='UNSUPPORTED_VIEW_DEPENDENCY', setting='depends_on')])
        for relation in used:
            if relation not in physical_relations or physical_relations[relation] not in permitted:
                _fail('INVALID_RELATION', 'unbound physical relation or undeclared dependency')
        # Validate the existing read-model guard with synthetic same-DB refs;
        # these are parser bindings only and never touch the registry/runtime.
        bindings = {rel: registry.ResourceRef('offline', 'source', str(i), 1)
                    for i, rel in enumerate(sorted(used))}
        normalize_select(item['compiled_sql'], bindings, database_id='offline')
    return {'compatibility': compatibility(), 'models': [model_by_id[uid] for uid in order], 'tests': tests,
            'sources': sources, 'test_owners': {t['unique_id']: nodes[t['unique_id']].get('attached_node') for t in tests},
            'materialized_order': [uid for uid in order
                if model_by_id[uid]['materialized'] == 'table']}


def _validate_relation(relation, catalog):
    if (any(not isinstance(p, str) or not p for p in relation)
            or relation[0] != catalog or relation[1].lower() in _SYSTEM or '$' in relation[2]):
        _fail('INVALID_RELATION', 'exact current-catalog non-system relation required')


@contextmanager
def _compiled_project(project, *, catalog, source_schema, staging_materialization=None):
    """Compile copied trusted SQL sources with installed dbt and return JSON data.

    No runtime profile is read. A mode-0600 dummy localhost:9/no-auth profile,
    fresh sandbox target, telemetry-off environment and all artifacts live only
    inside TemporaryDirectory. dbt stdout/stderr stay private, including errors.
    """
    if any(not isinstance(p, str) or not p for p in (catalog, source_schema)):
        _fail('INVALID_RELATION', 'catalog and source_schema required')
    if source_schema.lower() in _SYSTEM:
        _fail('INVALID_RELATION', 'system source schemas unsupported')
    if staging_materialization not in (None, 'table', 'ephemeral'):
        _fail('UNSUPPORTED_MATERIALIZATION', 'staging override must be table or ephemeral')
    project = Path(project).resolve()
    config, files, digest = _preflight(project)
    dbt = Path(__file__).resolve().parent / 'dataops' / '.venv' / 'bin' / 'dbt'
    if not dbt.is_file():
        _fail('COMPILE_FAILED', 'installed dataops dbt required')
    with tempfile.TemporaryDirectory(prefix='shirokuma-dbt-') as temporary:
        work = Path(temporary)
        copied = work / 'project'
        copied.mkdir()
        for rel, raw in files.items():
            destination = copied / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        # Never honor project-selected output locations, even in a copied project.
        config.update({'target-path': str(work / 'target'), 'log-path': str(work / 'logs'),
                       'packages-install-path': str(work / 'packages'), 'profile': 'offline'})
        (copied / 'dbt_project.yml').write_text(yaml.safe_dump(config))
        profiles = work / 'profiles'
        profiles.mkdir()
        profile = profiles / 'profiles.yml'
        profile_data = {'config': {'send_anonymous_usage_stats': False},
            'offline': {'target': 'offline', 'outputs': {'offline': {
                'type': 'trino', 'method': 'none', 'host': '127.0.0.1', 'port': 9,
                'http_scheme': 'http', 'user': 'offline-no-credentials',
                'database': catalog, 'schema': 'sandbox', 'threads': 1, 'retries': 0}}}}
        with os.fdopen(os.open(profile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
            yaml.safe_dump(profile_data, stream)
        variables = {'shirokuma_source_database': catalog,
                     'shirokuma_source_schema': source_schema,
                     'shirokuma_candidate_schema': 'sandbox'}
        if staging_materialization:
            variables['shirokuma_staging_materialization'] = staging_materialization
        env = {'PATH': str(dbt.parent) + os.pathsep + '/usr/bin:/bin',
               'HOME': str(work), 'TMPDIR': str(work), 'LANG': 'en_US.UTF-8',
               'DBT_SEND_ANONYMOUS_USAGE_STATS': 'false', 'DO_NOT_TRACK': '1',
               'PYTHON_DOTENV_DISABLED': '1',
               'DBT_PROFILES_DIR': str(profiles)}
        command = [str(dbt), 'compile', '--project-dir', str(copied),
                   '--profile', 'offline', '--target', 'offline',
                   '--profiles-dir', str(profiles), '--target-path', str(work / 'target'),
                   '--log-path', str(work / 'logs'), '--no-introspect',
                   '--no-populate-cache', '--no-partial-parse', '--threads', '1',
                   '--no-send-anonymous-usage-stats', '--vars', json.dumps(variables)]
        try:
            result = subprocess.run(command, cwd=work, env=env, capture_output=True,
                                    text=True, timeout=120, check=False)
            if result.returncode:
                _fail('COMPILE_FAILED', 'offline dbt compile failed')
            manifest = json.loads((work / 'target' / 'manifest.json').read_text())
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            if isinstance(error, DbtError):
                raise
            _fail('COMPILE_FAILED', 'offline dbt compile did not produce a manifest')
        summary = _validate_manifest(manifest, catalog=catalog, source_schema=source_schema)
        summary['source_digest'] = digest
        summary['versions'] = {'dbt': manifest.get('metadata', {}).get('dbt_version'),
                               'dbt_trino': importlib.metadata.version('dbt-trino'),
                               'sqlglot': importlib.metadata.version('sqlglot')}
        yield summary, manifest, command, env


def compile_project(project, *, catalog, source_schema, staging_materialization=None):
    """Compile trusted copied sources offline; return only the validated summary."""
    with _compiled_project(project, catalog=catalog, source_schema=source_schema,
                           staging_materialization=staging_materialization) as artifacts:
        return artifacts[0]
