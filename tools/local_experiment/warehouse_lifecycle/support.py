"""Private state for the disposable warehouse lifecycle controller experiment."""
from pathlib import Path
import re
import sys
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
STATE = ROOT / 'tmp/local-experiment/warehouse-lifecycle'
DEPLOY = ROOT / 'deploy/local-experiment/warehouse-lifecycle'
CONTEXT = 'colima-mac-studio-solo'
NAMESPACE = 'shirokuma-dev'
PORTS = {'gateway': 18580, 'xs': 18581, 's': 18582}
TOKEN = re.compile(r'[0-9a-f]{32}')
QUERY_ID = re.compile(r'\d{8}_\d{6}_\d{5}_[a-z0-9]{5}')
sys.path.insert(0, str(ROOT))
from tools.local_experiment.trino_opa.common import (ExperimentError, canonical, command, digest,
    directory, interruption_handlers, read, require, run_lock, stop_group, token, write)


def new_output(kind='runs'):
    require(kind == 'runs', 'OUTPUT_KIND_INVALID')
    directory(STATE, create=True)
    directory(STATE / kind, create=True)
    run_token = uuid.uuid4().hex
    output = STATE / kind / run_token
    directory(output, create=True)
    return run_token, output
