"""One SDK invocation with dummy artifacts and synchronous anonymous-pipe callbacks."""
import json
import os
import sys
from types import SimpleNamespace


def main():
    # dbt logs (including import-time logs) cannot enter the protocol stream.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), 'w')
    with open(os.devnull, 'w') as quiet:
        os.dup2(quiet.fileno(), sys.stdout.fileno())
        os.dup2(quiet.fileno(), sys.stderr.fileno())
    request = json.loads(sys.stdin.readline())

    import database_dbt_native as native
    import data_management as registry
    from dbt.adapters.contracts.connection import AdapterResponse
    import agate

    class Callbacks:
        aborted, called, response, failures = False, False, None, None
        target = tuple(request['target'])
        locator = SimpleNamespace(database_id=request['database_id'])
        bindings = {tuple(b['relation']): registry.ResourceRef(**b['ref']) for b in request['bindings']}
        approved = dict(request['approved'], sources={k: registry.ResourceRef(**v)
                        for k, v in request['approved']['sources'].items()})

        def call(self, message):
            protocol.write(json.dumps(message, allow_nan=False) + '\n')
            protocol.flush()
            answer = json.loads(sys.stdin.readline())
            if set(answer) != {'result'}:
                raise ValueError('invalid reply')
            return answer['result']

        def relation(self, database, schema, identifier):
            result = self.call(dict(op='get_relation', database=database, schema=schema, identifier=identifier))
            if result is not None:
                self.deny()
            return None

        def execute(self, sql, auto_begin=False, fetch=False, limit=None):
            result = self.call(dict(op='execute', sql=sql, auto_begin=auto_begin, fetch=fetch, limit=limit))
            self.called = True
            if request.get('mode') == 'test':
                self.response = AdapterResponse(**result['adapter_response'])
                self.failures = result['data'][0][0]
                return self.response, agate.Table(result['data'],
                    column_names=['failures', 'should_warn', 'should_error'],
                    column_types=[agate.Number(), agate.Boolean(), agate.Boolean()])
            self.response = AdapterResponse(**result)
            return self.response, agate.Table([])

        def deny(self, *args, **kwargs):
            self.aborted = True
            raise ValueError('unsupported SDK operation')

    for package, version in (('dbt-core', '1.12.5'), ('dbt-trino', '1.10.3'), ('sqlglot', '28.0.0')):
        if native.importlib.metadata.version(package) != version:
            raise ValueError('SDK version')
    artifacts = (None, request['manifest'], request['command'], dict(os.environ))
    bridge = Callbacks()
    if request.get('mode') == 'test':
        native._run_test_native(artifacts, request['node_id'], bridge)
        done = dict(op='done', status='PASSED' if bridge.failures == 0 else 'FAILED', failures=bridge.failures)
    else:
        native._run_native(artifacts, request['node_id'], bridge)
        done = dict(op='done', status='success')
    protocol.write(json.dumps(done) + '\n')
    protocol.flush()
    protocol.close()


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        # Parent accepts only positive normal termination; raw SDK errors stay private.
        sys.exit(1)
