"""Durable local state paths and atomic JSON replacement."""
import json
import os
from pathlib import Path
import tempfile


def state_path(filename):
    """Require writable durable storage, including explicit overrides."""
    root = Path(__file__).resolve().parents[2]
    durable = Path(os.environ.get('SHIROKUMA_STATE_DIR') or root / 'var' / 'shirokuma')
    try:
        durable.mkdir(parents=True, exist_ok=True)
        if os.access(durable, os.W_OK):
            return durable / filename
    except OSError as err:
        raise RuntimeError(f'State directory is not writable: {durable}') from err
    raise RuntimeError(f'State directory is not writable: {durable}')


def persist_json(path, value):
    """Replace state atomically, keeping credentials private and old state intact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        temporary.write_text(json.dumps(value, indent=2) + '\n')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
