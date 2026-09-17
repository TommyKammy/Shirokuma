"""Fixed non-production configuration; generated credentials stay private."""
import json
import secrets

from .support import DEPLOY, PORTS, canonical, digest, require, token, write

DATABASE = 'vw_gateway'
RESTORE_DATABASE = 'vw_restored'
DATABASE_USER = 'vw_owner'
ROUTING_HEADER = 'X-Shirokuma-Warehouse'


def backend_records(run_token):
    token(run_token)
    return [{'name': f'shirokuma-vw-{run_token}-{size}', 'proxyTo': f'http://127.0.0.1:{PORTS[size]}',
             'externalUrl': f'http://127.0.0.1:{PORTS[size]}', 'active': True, 'routingGroup': 'local-' + size}
            for size in ('xs', 's')]


def trino_config(run_token, size):
    token(run_token)
    require(size in ('xs', 's'), 'WAREHOUSE_INVALID')
    small = size == 'xs'
    return {
        'config.properties': '\n'.join([
            'coordinator=true', 'node-scheduler.include-coordinator=true',
            f'http-server.http.port={PORTS[size]}', f'discovery.uri=http://127.0.0.1:{PORTS[size]}',
            'http-server.process-forwarded=true', 'query.max-memory=256MB' if small else 'query.max-memory=512MB',
            'query.max-memory-per-node=256MB' if small else 'query.max-memory-per-node=512MB',
            'memory.heap-headroom-per-node=256MB' if small else 'memory.heap-headroom-per-node=512MB',
            'query.max-run-time=30s', 'query.max-execution-time=20s',
            'access-control.config-files=/etc/trino/readonly.properties', '']),
        'jvm.config': '\n'.join(['-server', '-Xmx1G' if small else '-Xmx2G', '-XX:+ExitOnOutOfMemoryError',
            '-XX:ReservedCodeCacheSize=256M', '-Djdk.nio.maxCachedBufferSize=2000000', '-Djava.io.tmpdir=/tmp',
            '--add-modules=jdk.incubator.vector', '--enable-native-access=ALL-UNNAMED', '']),
        'node.properties': '\n'.join([f'node.environment=shirokuma_vw_{size}',
            f'node.id=shirokuma-vw-{run_token}-{size}', 'node.data-dir=/var/trino', '']),
        'log.properties': 'io.trino=INFO\n',
        'tpch.properties': 'connector.name=tpch\ntpch.column-naming=SIMPLIFIED\n',
        'readonly.properties': 'access-control.name=read-only\n',
    }


def gateway_config(password):
    require(type(password) is str and len(password) == 64 and all(c in '0123456789abcdef' for c in password),
            'CREDENTIAL_INVALID')
    return {
        'serverConfig': {'node.environment': 'shirokuma_vw_local', 'http-server.http.port': PORTS['gateway'],
                         'tracing.enabled': False},
        'routing': {'defaultRoutingGroup': 'local-unrouted', 'forwardedHeadersEnabled': True, 'asyncTimeout': '40s'},
        'routingRules': {'rulesEngineEnabled': True, 'rulesType': 'FILE',
                         'rulesConfigPath': '/etc/trino-gateway/routing-rules.yaml', 'rulesRefreshPeriod': '30s'},
        'requestAnalyzerConfig': {'analyzeRequest': False},
        'dataStore': {'jdbcUrl': 'jdbc:postgresql://127.0.0.1:5432/' + DATABASE, 'user': DATABASE_USER,
                      'password': password, 'driver': 'org.postgresql.Driver', 'queryHistoryEnabled': False,
                      'queryHistoryHoursRetention': 1, 'runMigrationsEnabled': True},
        'clusterStatsConfiguration': {'monitorType': 'INFO_API'},
        'monitor': {'taskDelay': '2s', 'clusterMetricsRegistryRefreshPeriod': '30s'},
        'databaseCache': {'enabled': False},
    }


def create_fixtures(run_token, output):
    """Return secret-bearing material in memory; persist only private hashed metadata."""
    token(run_token)
    password = secrets.token_hex(32)
    public = {size + '.' + key: value for size in ('xs', 's') for key, value in trino_config(run_token, size).items()}
    public['routing-rules.yaml'] = (DEPLOY / 'routing-rules.yaml').read_text()
    private = {'postgres-user': DATABASE_USER, 'postgres-password': password, 'postgres-db': DATABASE,
               'gateway-config.yaml': canonical(gateway_config(password)) + '\n'}
    receipt = {'schema_version': 1, 'run_token': run_token, 'public_sha256': digest(public),
               'secret_sha256': digest(private), 'backends': backend_records(run_token),
               'history_enabled': False, 'query_history_retention_tested': False}
    write(output / 'fixture-receipt.json', receipt)
    return {'public_data': public, 'secret_data': private, 'receipt': receipt, 'redaction_values': [password]}
