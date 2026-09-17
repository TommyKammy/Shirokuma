"""Fixed TPCH/read-only warehouse configs and private per-run PostgreSQL secret."""
import ipaddress
import secrets

from tools.local_experiment.gateway_warehouse import fixtures as previous
from .support import DEPLOY, PORTS, canonical, digest, require, token, write

DATABASE = previous.DATABASE
DATABASE_USER = previous.DATABASE_USER
ROUTING_HEADER = previous.ROUTING_HEADER


def name(run_token):
    return 'shirokuma-wl-' + token(run_token)


def service_ips_valid(service_ips):
    require(type(service_ips) is dict and set(service_ips) == {'xs', 's'}, 'SERVICE_IPS_INVALID')
    addresses = []
    for value in service_ips.values():
        require(type(value) is str, 'SERVICE_IPS_INVALID')
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            require(False, 'SERVICE_IPS_INVALID')
        require(address.version == 4 and str(address) == value and address.is_private
                and not address.is_loopback and not address.is_link_local and not address.is_unspecified
                and not address.is_multicast, 'SERVICE_IPS_INVALID')
        addresses.append(address)
    require(len(set(addresses)) == 2, 'SERVICE_IPS_INVALID')
    return service_ips


def backend_records(run_token, service_ips):
    """IPs must first come from the runner's known-UID Service readback."""
    service_ips_valid(service_ips)
    return [{'name': name(run_token) + '-' + size, 'proxyTo': f'http://{service_ips[size]}:{PORTS[size]}',
             'externalUrl': f'http://{service_ips[size]}:{PORTS[size]}', 'active': True,
             'routingGroup': 'local-' + size} for size in ('xs', 's')]


def trino_config(run_token, size):
    result = previous.trino_config(run_token, size)
    result['config.properties'] += 'query.client.timeout=20s\n'
    result['node.properties'] = '\n'.join([f'node.environment=shirokuma_wl_{size}',
        f'node.id={name(run_token)}-{size}', 'node.data-dir=/var/trino', ''])
    return result


def gateway_config(password):
    result = previous.gateway_config(password)
    result['serverConfig']['node.environment'] = 'shirokuma_wl_local'
    return result


def create_fixtures(run_token, output):
    token(run_token)
    password = secrets.token_hex(32)
    public = {size + '.' + key: value for size in ('xs', 's') for key, value in trino_config(run_token, size).items()}
    public['routing-rules.yaml'] = (DEPLOY / 'routing-rules.yaml').read_text()
    private = {'postgres-user': DATABASE_USER, 'postgres-password': password, 'postgres-db': DATABASE,
               'gateway-config.yaml': canonical(gateway_config(password)) + '\n'}
    receipt = {'schema_version': 1, 'run_token': run_token, 'public_sha256': digest(public),
               'secret_sha256': digest(private), 'history_enabled': False, 'query_client_timeout_seconds': 20,
               'service_ips': 'unobserved-until-created-service-readback'}
    write(output / 'fixture-receipt.json', receipt)
    return {'public_data': public, 'secret_data': private, 'receipt': receipt, 'redaction_values': [password]}
