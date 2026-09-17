"""Private local helpers for the disposable JWT identity experiment."""
from pathlib import Path
import sys
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
STATE = ROOT / 'tmp/local-experiment/trino-identity'
CONTEXT = 'colima-mac-studio-solo'
NAMESPACE = 'shirokuma-dev'
sys.path.insert(0, str(ROOT))
from tools.local_experiment.trino_opa.common import (ExperimentError, canonical, command, digest,
    directory, interruption_handlers, read, require, run_lock, stop_group, token, write)


def new_output(kind):
    require(kind in {'builds', 'runs'}, 'OUTPUT_KIND_INVALID')
    directory(STATE, create=True)
    directory(STATE / kind, create=True)
    run_token = uuid.uuid4().hex
    output = STATE / kind / run_token
    directory(output, create=True)
    return run_token, output
