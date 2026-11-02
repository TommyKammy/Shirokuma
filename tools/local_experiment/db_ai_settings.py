"""Shared AI settings, independently authorized from Database data access."""
import time
from urllib.parse import urlsplit

import bears_resident as bears
from db_platform_observations import _authority
import wap_resident as wap
from warehouse_operations import OperationError

FIELDS = {'provider', 'model', 'api_url', 'api_key', 'timeout'}
PROVIDERS = {'local-openai', 'openai', 'bedrock', 'custom'}


def _validate(config):
    if (type(config.get('provider')) is not str or config['provider'] not in PROVIDERS
            or any(type(config.get(k)) is not str or not config[k].strip()
                   or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in config[k])
                   for k in ('model', 'api_url'))
            or len(config['model']) > 512 or len(config['api_url']) > 4096
            or type(config.get('timeout')) is not int or not 1 <= config['timeout'] <= 600
            or type(config.get('api_key', '')) is not str):
        raise OperationError('INVALID_INPUT', 400)
    try:
        url = urlsplit(config['api_url'])
        if (url.scheme not in ('http', 'https') or not url.hostname or url.username is not None
                or url.password is not None or '?' in config['api_url'] or '#' in config['api_url']
                or '\\' in config['api_url'] or any(c.isspace() for c in config['api_url'])
                or (url.port is not None and not 1 <= url.port <= 65535)
                or (url.port is None and url.netloc.endswith(':'))):
            raise ValueError()
    except ValueError:
        raise OperationError('INVALID_INPUT', 400) from None


def _public(config):
    return {k: ('********' if bears.resolve_api_key(config) else '') if k == 'api_key'
            else config[k] for k in FIELDS}


def _fence(principal, role, original):
    if _authority(principal, role) != original:
        raise OperationError('PLATFORM_ACCESS_DENIED', 403)


def perform(principal, role, action, changes=None):
    authority = _authority(principal, role)
    # Explicit path bypasses the legacy module path and in-memory cache.
    config = bears.load_ai_config(path=wap.state_path('ai_config.json'))
    if action != 'get':
        if (type(changes) is not dict or set(changes) - FIELDS
                or not FIELDS - {'api_key'} <= set(changes)):
            raise OperationError('INVALID_INPUT', 400)
        _validate(changes)
        changes = changes.copy()
        if changes.get('api_key') == '********':
            changes.pop('api_key')
        if any(changes[k] != config.get(k) for k in ('provider', 'api_url')):
            config.update(api_key='', api_key_env='')
        config.update(changes)
    _validate(config)
    if action == 'save':
        # Persist only known settings and the existing credential source.
        saved = {k: config[k] for k in FIELDS}
        saved['api_key_env'] = config.get('api_key_env', '')
        for key in ('max_tokens', 'temperature'):
            saved[key] = config[key]
        _fence(principal, role, authority)
        try:
            wap.persist_json(wap.state_path('ai_config.json'), saved)
        except OSError:
            _fence(principal, role, authority)
            raise OperationError('AI_CONFIG_UNAVAILABLE', 503) from None
        result = _public(config)
    elif action == 'test':
        started = time.monotonic()
        _fence(principal, role, authority)
        try:
            body = dict(model=config['model'], temperature=0, max_tokens=16,
                        messages=[dict(role='user', content='Reply with pong.')])
            if config['provider'] == 'local-openai':
                body['enable_thinking'] = False
            completion = bears._ai_completion(config, body)
            content = completion['choices'][0]['message']['content']
            if type(content) is not str or not content.strip():
                raise ValueError()
            result = dict(status='connected', latency_ms=int((time.monotonic()-started)*1000))
        except Exception:
            result = dict(status='failed', error='AI_TEST_FAILED',
                          latency_ms=int((time.monotonic()-started)*1000))
    else:
        result = _public(config)
    _fence(principal, role, authority)
    return result
