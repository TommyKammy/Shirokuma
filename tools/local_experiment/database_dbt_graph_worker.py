"""One credential-free installed RunTask / TestTask process, serial typed RPC."""
from copy import deepcopy
import json
import os
import sys


def manifest_copy(raw):
    import database_dbt_native as native
    result = native._builtin_manifest(raw)
    builtin_names = {m['name'] for m in result['macros'].values()}
    # Preserve pure project helpers, excluding execution/dispatch overrides.
    result['macros'].update({uid: deepcopy(m) for uid, m in raw['macros'].items()
        if m['package_name'] == raw['metadata']['project_name'] and m['name'] not in builtin_names
        and m['name'] not in {'generate_database_name', 'generate_schema_name', 'generate_alias_name'}
        and not m['name'].startswith(('materialization_', 'trino__', 'default__'))})
    return result


def result_dict(result):
    value = dict(node_id=result.node.unique_id, status=str(result.status), failures=result.failures,
        adapter_response=result.adapter_response)
    if result.node.resource_type == 'model' and result.node.config.materialized == 'view':
        value.update(model_type='view', view_security=result.node.config.get('view_security'))
    return value


def run_tasks(artifacts, targets, tests, call):
    import database_dbt_native as native
    import database_dbt as compiler
    from dbt.task.run import RunTask, ModelRunner
    from dbt.task.test import TestTask, TestRunner
    from dbt.flags import get_flags
    from dbt.adapters.contracts.connection import AdapterResponse
    import agate

    raw = deepcopy(artifacts[1])
    compiler._semantic_config(raw['nodes'])
    for uid, node in raw['nodes'].items():
        if node['resource_type'] not in ('model', 'test'):
            raise ValueError('non SQL node')
        if node['resource_type'] == 'model' and node['config']['materialized'] not in ('table', 'view', 'ephemeral'):
            raise ValueError('unsupported model')
        if node['resource_type'] == 'model' and node['config']['materialized'] == 'view':
            # Saved omission means INVOKER; the installed Trino macro defaults
            # to DEFINER. Set the effective SDK config before compilation.
            if node['config'].get('view_security') not in (None, 'invoker'):
                raise ValueError('unsupported view security')
            node['config']['view_security'] = 'invoker'
        if uid in targets:
            node['database'], node['schema'], node['alias'] = targets[uid]
            node['relation_name'] = '.'.join('"' + p.replace('"', '""') + '"' for p in targets[uid])
        # Raw SQL remains the original raw_code. Compile/ref/CTE injection is dbt's.
        node.update(compiled=False, compiled_code=None, extra_ctes=[], extra_ctes_injected=False)
        node.pop('_pre_injected_sql', None)
    artifacts = (artifacts[0], raw, artifacts[2], artifacts[3])
    with native._runtime(artifacts, manifest_filter=manifest_copy) as (config, manifest, adapter):
        flags = get_flags()
        for key, value in dict(single_threaded=True, threads=1, write_json=False, select=(), exclude=(), resource_types=(), exclude_resource_types=(), populate_cache=True, cache_selected_only=True).items():
            object.__setattr__(flags, key, value)
            object.__setattr__(flags, key.upper(), value)
        config.threads = 1
        phase = 'build'

        def deny(*args, **kwargs):
            raise ValueError('unsupported SDK I/O')

        def clear_transaction():
            connection = adapter.connections.get_if_exists()
            if connection is not None and (connection.transaction_open or connection.state != 'init'):
                deny()
            # No real connection has ever opened; hooks need only this inert shim.

        def relation(database, schema, identifier):
            value = call(dict(op='get_relation', database=database, schema=schema, identifier=identifier))
            return adapter.Relation.create(**value) if value else None

        def schemas(database):
            return call(dict(op='list_schemas', database=database))

        def relations(schema_relation):
            return [adapter.Relation.create(**v) for v in call(dict(op='list_relations',
                database=schema_relation.database, schema=schema_relation.schema))]

        def execute(sql, auto_begin=False, fetch=False, limit=None):
            value = call(dict(op='execute', sql=sql, auto_begin=auto_begin, fetch=fetch, limit=limit))
            if value == {'_graph_error': 'DATAOPS_STEP_FAILED'}:
                raise RuntimeError('parent query failed')
            if phase == 'build':
                return AdapterResponse(**value), agate.Table([])
            return AdapterResponse(**value['adapter_response']), agate.Table(value['data'],
                column_names=['failures', 'should_warn', 'should_error'],
                column_types=[agate.Number(), agate.Boolean(), agate.Boolean()])

        adapter.execute, adapter.get_relation = execute, relation
        adapter.list_schemas, adapter.list_relations_without_caching = schemas, relations
        adapter.connections.clear_transaction = clear_transaction
        for name in ('open', 'add_query', 'execute', 'commit', 'begin', 'rollback'):
            setattr(adapter.connections, name, deny)
        for name in ('create_schema', 'drop_schema', 'drop_relation', 'rename_relation',
                'get_columns_in_relation', 'cache_dropped', 'cache_renamed'):
            setattr(adapter, name, deny)
        original_cache_added = adapter.cache_added

        def cache_added(value):
            # Cache only graph-owned candidate tuples positively known to parent.
            call(dict(op='get_relation', database=value.database, schema=value.schema, identifier=value.identifier))
            if (value.database, value.schema, value.identifier) not in {tuple(t) for t in targets.values()}:
                deny()
            return original_cache_added(value)
        adapter.cache_added = cache_added

        class Lifecycle:
            def run_with_hooks(self, manifest):
                call(dict(op='enter_node', phase=phase, node_id=self.node.unique_id, skipped=self.skip))
                result = super().run_with_hooks(manifest)
                call(dict(op='end_node', phase=phase, result=result_dict(result)))
                return result

        class GraphModelRunner(Lifecycle, ModelRunner):
            def _get_materialization_macro(self, model, manifest):
                macro = super()._get_materialization_macro(model, manifest)
                if (model.config.materialized not in ('table', 'view')
                        or macro.unique_id != 'macro.dbt_trino.materialization_' + model.config.materialized + '_trino'
                        or model.config.materialized == 'view' and model.config.get('view_security') != 'invoker'):
                    deny()
                return macro

        class GraphTestRunner(Lifecycle, TestRunner):
            def execute(self, node, manifest):
                macro = manifest.find_materialization_macro_by_name(config.project_name, node.get_materialization(), adapter.type())
                if macro.unique_id != 'macro.dbt.materialization_test_default':
                    deny()
                return super().execute(node, manifest)

        # Only runner lifecycle is wrapped. Installed task.run, selection, graph
        # queue, scheduler, before/after and all materializations remain intact.
        task = RunTask(flags, config, manifest)
        task.get_runner_type = lambda node: GraphModelRunner
        result = task.run()
        success = task.interpret_results(result.results)
        call(dict(op='task_done', phase=phase, selected=sorted(task.job_queue.get_selected_nodes()),
            results=[result_dict(r) for r in result.results], success=success))
        if success and tests:
            phase = 'test'
            for node in manifest.nodes.values():
                if node.resource_type == 'test':
                    node.compiled = False
                    node.compiled_code = None
                    node.extra_ctes = []
                    node.extra_ctes_injected = False
                    node._pre_injected_sql = None
            task = TestTask(flags, config, manifest)
            task.get_runner_type = lambda node: GraphTestRunner
            result = task.run()
            call(dict(op='task_done', phase=phase, selected=sorted(task.job_queue.get_selected_nodes()),
                results=[result_dict(r) for r in result.results], success=task.interpret_results(result.results)))


def main():
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), 'w')
    with open(os.devnull, 'w') as quiet:
        os.dup2(quiet.fileno(), sys.stdout.fileno())
        os.dup2(quiet.fileno(), sys.stderr.fileno())
    request = json.loads(sys.stdin.readline())
    import database_dbt_native as native
    for package, version in (('dbt-core', '1.12.5'), ('dbt-trino', '1.10.3'), ('sqlglot', '28.0.0')):
        if native.importlib.metadata.version(package) != version:
            raise ValueError('SDK version')

    def call(message):
        protocol.write(json.dumps(message, allow_nan=False) + '\n')
        protocol.flush()
        answer = json.loads(sys.stdin.readline())
        if set(answer) != {'result'}:
            raise ValueError('invalid reply')
        return answer['result']

    artifacts = (None, request['manifest'], request['command'], dict(os.environ))
    run_tasks(artifacts, request['targets'], request['native_tests'], call)
    protocol.write('{"op":"done"}\n')
    protocol.flush()
    protocol.close()


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        sys.exit(1)
