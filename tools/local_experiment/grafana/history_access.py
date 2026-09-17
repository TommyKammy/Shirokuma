"""Explicit migration between the two fixed Grafana reader scopes."""
import hashlib
import importlib.util
import json
import os
import time
import uuid


def sha(content):
    return hashlib.sha256(content.encode()).hexdigest()


def verify_history_tables(runtime):
    # These existing importers verify exact owner comments, columns, engines and
    # physical row hashes. Their stores only SELECT during this preflight.
    for name in ('benchmark_events', 'query_history'):
        path = runtime.HERE.parent / name / 'import_receipts.py'
        spec = importlib.util.spec_from_file_location('grafana_history_' + name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        store = module.runtime_store()
        runtime.require(store.inspect() == set(module.SCHEMAS), 'history tables must already be imported')
        for table in module.SCHEMAS:
            runtime.require(bool(store.rows(table)), 'history table has no retained evidence')


def change_scope(runtime, enabled):
    """Caller holds the existing runtime lock; no service or credential rotation."""
    credential = runtime.credentials()
    current = runtime.agenthouse()
    old = runtime.docker('exec', '--user', 'root', current['Id'], 'cat', runtime.CH_CONFIG)
    base = runtime.reader_xml(credential)
    history = runtime.reader_xml(credential, runtime.HISTORY_READER_TABLES)
    runtime.require(old in (base, history), 'unknown reader config cannot be migrated')
    runtime.verify_reader(credential)
    if enabled:
        verify_history_tables(runtime)
    tables = runtime.HISTORY_READER_TABLES if enabled else runtime.BASE_READER_TABLES
    desired = history if enabled else base
    output = runtime.STATE / ('reader-scope-' + uuid.uuid4().hex)
    output.mkdir(mode=0o700)
    runtime.private_write(output / 'before.xml', old)
    receipt = {'schema_version': 1, 'result': 'running', 'agenthouse_id': current['Id'],
               'owner_token': credential['owner_token'], 'before_sha256': sha(old),
               'after_sha256': sha(desired), 'tables': list(tables), 'changed': old != desired,
               'pending': None}

    def save():
        runtime.private_write(output / 'receipt.json', receipt)

    save()
    try:
        if old != desired:
            receipt['pending'] = 'replace-exact-owned-reader-config'
            save()
            runtime.require(runtime.agenthouse()['Id'] == current['Id'], 'AgentHouse identity changed')
            # Replace only the known reader file, after an exact digest check.
            # Interrupted attempts leave either the old or new complete XML;
            # both are recognizable on retry. The original is retained above.
            script = ('set -eu; umask 077; test -f ' + runtime.CH_CONFIG + '; test ! -L ' + runtime.CH_CONFIG + '; '
                      'f=$(mktemp /etc/clickhouse-server/users.d/.shirokuma-grafana.XXXXXX); '
                      'trap \'rm -f "$f"\' EXIT; cat > "$f"; chown clickhouse:clickhouse "$f"; chmod 600 "$f"; '
                      'set -- $(sha256sum ' + runtime.CH_CONFIG + '); test "$1" = ' + sha(old) + '; '
                      'mv "$f" ' + runtime.CH_CONFIG)
            runtime.docker('exec', '--user', 'root', '-i', current['Id'], '/bin/sh', '-c', script, input_text=desired)
        runtime.require(runtime.agenthouse()['Id'] == current['Id'], 'AgentHouse identity changed')
        actual = runtime.docker('exec', '--user', 'root', current['Id'], 'cat', runtime.CH_CONFIG)
        runtime.require(actual == desired, 'reader config readback differs')
        receipt['pending'] = 'reload-and-verify-reader'
        save()
        runtime.AH.Client().query('SYSTEM RELOAD CONFIG')
        deadline = time.monotonic() + 30
        while True:
            try:
                verified = runtime.verify_reader(credential)
                runtime.require(set(verified['logical_rows']) == set(tables), 'reader scope is not active')
                break
            except (runtime.GrafanaError, runtime.AH.AgentHouseError):
                if time.monotonic() >= deadline:
                    raise runtime.GrafanaError('reader scope migration was not verified') from None
                time.sleep(1)
        runtime.require(runtime.agenthouse()['Id'] == current['Id'], 'AgentHouse identity changed')
        runtime.private_write(runtime.STATE / 'reader-users.xml', desired)
        runtime.private_write(runtime.STATE / 'reader-config-receipt.json', {
            'agenthouse_id': current['Id'], 'config_path': runtime.CH_CONFIG,
            'config_sha256': sha(desired), 'owner_token': credential['owner_token'],
            'outcome': 'verified', **verified})
        receipt.update(result='passed', pending=None, reader_permissions=verified)
        save()
        return {'result': 'passed', 'history_enabled': enabled, 'changed': old != desired,
                'reader_permissions': verified, 'evidence_token': output.name}
    except BaseException:
        receipt['result'] = 'failed'
        # Do not overwrite or roll back an uncertain config after a failure.
        # Preserve the exact backup and pending operation for read-only diagnosis.
        save()
        raise


def publish_dashboard(runtime):
    """Activate the checked-in dashboard only after the fixed reader is ready."""
    import history_dashboard as dashboard
    verified = runtime.verify_reader(runtime.credentials())
    runtime.require(set(verified['logical_rows']) == set(runtime.HISTORY_READER_TABLES), 'history reader is not enabled')
    raw = dashboard.OUTPUT.read_bytes()
    runtime.require(json.loads(raw) == dashboard.dashboard(), 'staged dashboard differs from its generator')
    target = runtime.HERE / 'dashboards/history.json'
    runtime.require(not target.is_symlink(), 'dashboard destination is a symlink')
    if target.exists():
        runtime.require(json.loads(target.read_bytes()).get('uid') == dashboard.UID, 'dashboard destination has another identity')
    temporary = target.with_name('.history-' + uuid.uuid4().hex)
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644), 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {'result': 'passed', 'dashboard_uid': dashboard.UID, 'dashboard_sha256': hashlib.sha256(raw).hexdigest()}
