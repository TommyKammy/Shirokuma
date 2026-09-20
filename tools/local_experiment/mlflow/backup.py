"""Copy the complete stopped Tracking state into a new private cold export.

This writes no source data and neither restores nor activates the copy. Existing
restore profile restrictions still apply independently to any later restore.
"""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from tools.local_experiment.mlflow import import_evaluations as E
from tools.local_experiment.mlflow import restore as D
from tools.local_experiment.mlflow import runtime as R


def _database_ok(path):
    connection = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        connection.execute('PRAGMA query_only=ON')
        E.require(connection.execute('PRAGMA quick_check').fetchall() == [('ok',)], 'BACKUP_DATABASE_INVALID')
    finally:
        D.close_preserving(connection.close)


def export_stopped(destination, *, runtime=R, root=E.ROOT):
    """Create one manifest and copy, under the same lock as supported writers."""
    root, destination = Path(root), Path(destination)
    E.require(destination.is_absolute(), 'BACKUP_PATH_INVALID')
    relative = destination.relative_to(root)
    E.require(relative.parts[:2] == ('tmp', 'local-experiment')
              and destination.name == 'cold-backup', 'BACKUP_PATH_INVALID')
    # Both input and output remain beneath the caller's resolved checkout.
    D.parents(root, destination.parent)
    E.private_directory(destination.parent)
    manifest_path = destination.parent / 'backup-manifest.json'
    E.require(not destination.exists() and not destination.is_symlink()
              and not manifest_path.exists() and not manifest_path.is_symlink(), 'BACKUP_ALREADY_EXISTS')
    with runtime.store_lock():
        status = runtime.require_stopped()
        source = runtime.STATE
        E.require(not destination.is_relative_to(source) and not source.is_relative_to(destination),
                  'BACKUP_SOURCE_OVERLAP')
        D.parents(root, source)
        E.require(not any((runtime.STORE / ('tracking.sqlite' + suffix)).exists()
                          or (runtime.STORE / ('tracking.sqlite' + suffix)).is_symlink()
                          for suffix in ('-wal', '-journal', '-shm')), 'BACKUP_ACTIVE_SIDECAR')
        before, seal = D.inventory(source)
        _database_ok(runtime.STORE / 'tracking.sqlite')
        D.copy_tree(source, destination, before)
        _database_ok(destination / 'store/tracking.sqlite')
        E.require(D.inventory(source) == (before, seal), 'BACKUP_SOURCE_CHANGED')
        E.require(runtime.require_stopped()['store_id'] == status['store_id'], 'BACKUP_STORE_CHANGED')
        # No result manifest is released until source, copy and store agree.
        value = {'result': 'verified', 'observed_at': datetime.now(timezone.utc).isoformat(),
                 'store_id': status['store_id'], 'source': str(source.relative_to(root)),
                 'backup': str(destination.relative_to(root)), 'inventory': before}
        D.write(manifest_path, E.canonical(value))
        D.sync(destination.parent)
        return value
