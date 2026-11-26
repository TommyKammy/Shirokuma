"""Shared AI provider configuration, credentials, and completion transport."""
import json
import os
from pathlib import Path

import state_files
import warehouse_resident as wr

AI_CONFIG_PATH = state_files.state_path('ai_config.json')
_ai_config = {}


def load_ai_config(*, path=None):
    config = dict(provider='local-openai',
                  model=os.environ.get('BEAR_MODEL', 'qwen3.8-flash-next'),
                  api_url=os.environ.get('BEAR_MODEL_URL', 'http://127.0.0.1:8001/v1'),
                  api_key_env='BEAR_API_KEY' if os.environ.get('BEAR_API_KEY') else 'OPENAI_API_KEY',
                  api_key='', timeout=30, max_tokens=1024, temperature=0.0)
    if path is None:
        config.update(_ai_config)
        path = AI_CONFIG_PATH
    try:
        saved = json.loads(Path(path).read_text())
        if isinstance(saved, dict):
            config.update(saved)
    except (OSError, ValueError):
        pass
    return config


def merged_ai_config(config):
    """Merge settings without carrying credentials to another endpoint."""
    active = load_ai_config()
    config = dict(config)
    if config.get('api_key') == '********':
        del config['api_key']
    if any(key in config and config[key] != active.get(key) for key in ('api_url', 'provider')):
        active['api_key'] = ''
        active['api_key_env'] = ''
    active.update(config)
    return active


def resolve_api_key(config):
    if config.get('api_key'):
        return config['api_key']
    key_env = config.get('api_key_env')
    if not key_env:
        return None
    if config.get('provider') in {'custom', 'local-openai'} and key_env == 'OPENAI_API_KEY':
        return None
    return os.environ.get(key_env)


def _ai_completion(config, body):
    headers = {'Content-Type': 'application/json'}
    key = resolve_api_key(config)
    if key:
        headers['Authorization'] = 'Bearer ' + key
    return wr.http_json(config['api_url'].rstrip('/') + '/chat/completions',
                        json.dumps(body).encode(), headers, timeout=config['timeout'])
