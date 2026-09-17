#!/usr/bin/env python3
"""Run the fixed platform scope check and status read within one parent deadline."""
import importlib.util
import json
from pathlib import Path


LOCAL = Path(__file__).resolve().parent.parent


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    try:
        scope = load_module('gateway_platform_scope', LOCAL / 'local_experiment.py')
        scope.check_context()
        platform = load_module('gateway_platform_status', LOCAL / 'platform_status.py')
        return platform.main()
    except Exception:
        # Do not expose kubeconfig, Docker details, backend errors or credentials.
        print(json.dumps({'error_code': 'LOCAL_PLATFORM_SCOPE_OR_STATUS_FAILED'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
