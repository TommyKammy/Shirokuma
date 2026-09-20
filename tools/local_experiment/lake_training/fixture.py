"""One disposable Iceberg training source, with owned prepare/read/cleanup phases.

The Reader callback runs only after the preparation tunnels close. No source row
is returned here: training must consume the independent Reader observation.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time
import uuid

from tools.local_experiment.iceberg_refs import run as REFS
from tools.local_experiment.iceberg_sandbox import run as SB
from tools.local_experiment.query_read import client as Q, contract as C

PROFILE = 'fixed-iceberg-regression-source-v1'
NAMESPACE = 'iceberg.local_experiment'
COLUMNS = [{'name':name, 'type':'bigint' if name == 'row_id' else 'double', 'nullable':True}
           for name in ('row_id','x1','x2','x3','target')]


class FixtureError(ValueError):
    pass


def require(value, code):
    if not value: raise FixtureError(code)


def keep(first, later):
    return later if first is None or isinstance(first, Exception) and not isinstance(later, Exception) else first


def stamp(): return datetime.now(timezone.utc).isoformat()


def table_name(token):
    require(type(token) is str and re.fullmatch(r'[0-9a-f]{32}', token), 'LAKE_FIXTURE_TOKEN_INVALID')
    return 'training_' + token


def marker(token): return PROFILE + ':' + table_name(token)


def generated_rows():
    rows = []
    for i in range(64):
        x1, x2, x3 = (i % 8 - 3.5) / 2, (i // 8 - 3.5) / 2, ((i * 7) % 13 - 6) / 3
        rows.append([i, x1, x2, x3, 4 + 2.5*x1 - 1.75*x2 + 0.5*x3 + 0.2*math.sin(i)])
    return rows


def create_sql(token):
    name = table_name(token)
    values = ', '.join('(' + ', '.join(["BIGINT '" + str(row[0]) + "'"] +
        ["DOUBLE '" + repr(value) + "'" for value in row[1:]]) + ')' for row in generated_rows())
    return (f"CREATE TABLE {NAMESPACE}.{name} COMMENT '{marker(token)}' "
            "WITH (format = 'PARQUET', format_version = 2) AS "
            "SELECT row_id, x1, x2, x3, target FROM (VALUES " + values +
            ") AS fixture(row_id, x1, x2, x3, target)")


def save(path, value):
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    raw = C.canonical(value) + b'\n'
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    stream, original = None, None
    try:
        stream = os.fdopen(fd, 'wb')
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    except BaseException as error: original = error
    finally:
        try:
            if stream is None: os.close(fd)
            else: stream.close()
        except BaseException as error: original = keep(original,error)
    if original is not None: raise original
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    original = None
    try: os.fsync(fd)
    except BaseException as error: original = error
    finally:
        try: os.close(fd)
        except BaseException as error: original = keep(original,error)
    if original is not None: raise original


@contextmanager
def runtime(token, progress):
    """A bounded tunnel session; prepare and cleanup use separate instances."""
    table_name(token)
    journal = {'state':'pending','cleanup_verified':False,'trino':None,'polaris':None}
    resources, original, catalog, guard = [], None, None, None
    def enter(context):
        value = context.__enter__(); resources.append(context)
        return value
    def emit(): progress(deepcopy(journal))
    def polaris_progress(value):
        journal['polaris'] = deepcopy(value); emit()
    try:
        REFS.SESSION.check_context()
        before = Q.trino_service_identity()
        journal['trino'] = before; emit()
        url = enter(REFS.SESSION.trino_tunnel())
        polaris_guard = enter(REFS.polaris_tunnel(polaris_progress))
        def guard():
            require(Q.trino_service_identity() == before, 'LAKE_TRINO_SERVICE_CHANGED')
            polaris_guard()
        guard()
        worker, control = SB.SandboxClient(url, token), SB.SandboxClient(url, token, control=True, timeout=15)
        catalog = Q.CatalogRead(guard)
        journal['state'] = 'ready'; emit()
        yield worker, control, catalog, guard
    except BaseException as error:
        original = error
    finally:
        if guard is not None:
            try: guard()
            except BaseException as error: original = keep(original,error)
        closed = True
        for context in reversed(resources):
            try: context.__exit__(None,None,None)
            except BaseException as error:
                closed = False; original = keep(original,error)
        if catalog is not None:
            try: catalog.close()
            except BaseException as error:
                closed = False; original = keep(original,error)
        journal.update(state='verified' if original is None else 'failed',cleanup_verified=closed)
        try: emit()
        except BaseException as error: original = keep(original,error)
    if original is not None: raise original


class FixtureOwner:
    def __init__(self, output, token, *, reader_closed, _runtime=None, _drain=None):
        self.name, self.token = table_name(token), token
        require(callable(reader_closed), 'LAKE_READER_CLOSURE_REQUIRED')
        self.output = Path(output)
        require(self.output.is_absolute() and all(not p.is_symlink() for p in (self.output,*self.output.parents)), 'LAKE_OUTPUT_INVALID')
        info = self.output.parent.stat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700, 'LAKE_OUTPUT_INVALID')
        self.output.mkdir(mode=0o700, exist_ok=False)
        self.table, self.request = NAMESPACE + '.' + self.name, dict(command='select',table='local_experiment.'+self.name,columns=[x['name'] for x in COLUMNS],limit=100)
        self.reader_closed, self.runtime = reader_closed, _runtime or runtime
        self.drain = _drain or SB.drain_queries
        self.worker = self.control = self.catalog = None
        self.guard = lambda: None
        self.ledgers = {'worker':[], 'control':[]}
        self.created = self.attempted = self.uncertain = self.callback_started = False
        self.identity = self.before = self.value = None
        self.deadline = time.monotonic() + 180
        self.report = dict(schema_version=1,profile=PROFILE,fixture_token=token,table=self.table,marker=marker(token),
            result='running',failure_code=None,started_at=stamp(),steps=[],pending=None,runtime={},catalog={},
            creation_attempted=False,table_created=False,sql_outcome_unknown=False,callback_completed=False,
            reader_cleanup_verified=True,query_drain={'verified':False},cleanup={'verified':False,'removed':[], 'deferred':[]},
            fixture_unchanged=False,table_inventory_unchanged=False)

    def persist(self):
        self.report.update(creation_attempted=self.attempted,table_created=self.created,sql_outcome_unknown=self.uncertain)
        self.report['queries'] = {key:[{k:deepcopy(v) for k,v in row.items() if not k.startswith('_')} for row in rows]
                                  for key,rows in self.ledgers.items()}
        if self.catalog is not None: self.report['catalog'][self.phase] = self.catalog.receipt()
        save(self.output/'report.json',self.report)

    @contextmanager
    def connected(self, phase):
        def progress(value):
            self.report['runtime'][phase] = deepcopy(value); self.persist()
        self.phase = phase
        with self.runtime(self.token,progress) as clients:
            self.worker,self.control,self.catalog,self.guard = clients
            require(self.worker.user == SB.run_user(self.token) and self.control.user == SB.run_user(self.token,True), 'LAKE_QUERY_IDENTITIES_DIFFER')
            self.phase = phase
            for key,client in (('worker',self.worker),('control',self.control)):
                client.records = self.ledgers[key]; client.progress = self.persist
            self.catalog.progress = self.persist
            try: yield
            finally:
                self.worker.progress = self.control.progress = self.catalog.progress = None
                self.catalog = None

    def step(self, label, call, *, mutation=False):
        require(time.monotonic() < self.deadline, 'LAKE_FIXTURE_DEADLINE')
        item = dict(name=label,mutation=mutation,outcome='pending')
        self.report['steps'].append(item); self.report['pending'] = label; self.persist()
        called, original, value = False, None, None
        try:
            self.guard()
            if mutation: require(not self.uncertain, 'LAKE_MUTATION_OUTCOME_UNKNOWN')
            if label == 'create': self.attempted = True
            called = True
            value = call()
            if label == 'create': self.created = True
            item['outcome'] = 'confirmed'; self.report['pending'] = None
        except BaseException as error:
            original = error
            item['outcome'] = 'unknown' if mutation and called else 'failed'
            self.uncertain |= mutation and called
        try: self.persist()
        except BaseException as error:
            original = keep(original,error)
            self.uncertain |= mutation and called
        if original is not None: raise original
        return value

    def sql(self, label, text, *, mutation=False, worker=False):
        client = self.worker if worker else self.control
        try:
            return self.step(label,lambda:SB.bounded_query(client,text,self.deadline),mutation=mutation)
        except BaseException:
            if any(not item.get('response_complete') for item in client.records): self.uncertain = True
            raise

    def fixture(self, label):
        observed = {}
        for key,text in REFS.FIXTURE_SQL.items():
            value = self.sql(label+'_'+key,text)
            observed[key] = {name:value[name] for name in ('columns','column_types','rows')}
        SB.check_rows(observed['rows'],REFS.ROWS_A)
        require(observed['schema']['rows'] == [['id','bigint'],['name','varchar']], 'LAKE_RETAINED_SCHEMA_CHANGED')
        require(observed['tables']['columns'] == ['table_catalog','table_schema','table_name'] and
                observed['tables']['column_types'] == ['varchar']*3 and
                all(type(row) is list and len(row)==3 and all(type(x) is str for x in row) for row in observed['tables']['rows']), 'LAKE_TABLE_INVENTORY_INVALID')
        self.report['fixture_'+label] = observed; self.persist()
        return observed

    def load_identity(self):
        response = self.step('load_identity',lambda:self.catalog.load(self.request))
        properties = response['metadata'].get('properties')
        require(type(properties) is dict and properties.get('comment') == marker(self.token), 'LAKE_OWNER_MARKER_CHANGED')
        identity = C.project_identity(response,self.request,COLUMNS)
        require(identity['current_snapshot_id'] is not None, 'LAKE_FIXTURE_SNAPSHOT_MISSING')
        return identity

    def owner_marker(self):
        ddl = self.sql('owner_marker','SHOW CREATE TABLE '+self.table)
        require(len(ddl['column_types'])==1 and type(ddl['column_types'][0]) is str and
                re.fullmatch(r'varchar(?:\([1-9][0-9]*\))?',ddl['column_types'][0]) and
                len(ddl['rows'])==1 and len(ddl['rows'][0])==1 and
                type(ddl['rows'][0][0]) is str and re.search(r"\bCOMMENT\s+'"+re.escape(marker(self.token))+"'",ddl['rows'][0][0]), 'LAKE_OWNER_MARKER_CHANGED')

    def prepare(self):
        self.before = self.fixture('before')
        require(['iceberg','local_experiment',self.name] not in self.before['tables']['rows'], 'LAKE_TABLE_COLLISION')
        self.step('authenticate',self.catalog.authenticate)
        self.sql('create',create_sql(self.token),mutation=True,worker=True)
        self.identity = self.load_identity()
        self.report['identity'] = deepcopy(self.identity); self.persist()
        self.owner_marker()

    def cleanup(self):
        original = None
        try:
            self.report['query_drain'] = self.drain(self.control,self.worker,self.token)
            self.persist()
            reason = ('LAKE_MUTATION_OUTCOME_UNKNOWN' if self.uncertain else
                      'LAKE_READER_CLEANUP_UNVERIFIED' if not self.report['reader_cleanup_verified'] else
                      'LAKE_QUERY_DRAIN_UNVERIFIED' if self.report['query_drain'].get('verified') is not True else
                      'LAKE_ORIGINAL_UUID_UNKNOWN' if self.attempted and self.identity is None else None)
            if reason:
                self.report['cleanup']['deferred'] = [reason]
            elif self.attempted:
                self.step('authenticate',self.catalog.authenticate)
                require(self.load_identity() == self.identity, 'LAKE_OWNED_METADATA_CHANGED')
                self.owner_marker()
                self.sql('drop','DROP TABLE '+self.table,mutation=True)
                self.report['cleanup'].update(verified=True,removed=[self.name])
            else:
                self.report['cleanup']['verified'] = True
        except BaseException as error:
            original = error
            self.report['cleanup']['deferred'] = ['LAKE_OWNED_CLEANUP_UNVERIFIED']
        try:
            after = self.fixture('after')
            if self.before is not None:
                self.report['fixture_unchanged'] = all(self.before[key] == after[key] for key in self.before if key != 'tables')
                self.report['table_inventory_unchanged'] = self.before['tables'] == after['tables']
            if ['iceberg','local_experiment',self.name] in after['tables']['rows'] and self.attempted:
                self.report['cleanup']['verified'] = False
        except BaseException as error:
            original = keep(original,error)
        try: self.persist()
        except BaseException as error: original = keep(original,error)
        if original is not None: raise original

    def run(self, callback):
        require(callable(callback), 'LAKE_CALLBACK_INVALID')
        original = None
        try:
            self.persist()
            with self.connected('prepare'): self.prepare()
            require(self.report['runtime']['prepare'].get('cleanup_verified') is True, 'LAKE_PREPARE_TUNNEL_UNVERIFIED')
            self.callback_started = True; self.report['reader_cleanup_verified'] = False; self.persist()
            self.value = callback(dict(profile=PROFILE,fixture_token=self.token,request=deepcopy(self.request),
                                       identity=deepcopy(self.identity),expected_rows=64))
            self.report['callback_completed'] = True
        except BaseException as error:
            original = error
        finally:
            if self.callback_started:
                try: self.report['reader_cleanup_verified'] = self.reader_closed() is True
                except BaseException as error: original = keep(original,error)
            self.deadline = time.monotonic() + 180
            if self.before is not None or self.attempted:
                try:
                    with self.connected('cleanup'): self.cleanup()
                except BaseException as error: original = keep(original,error)
            else:
                self.report['cleanup']['verified'] = True
            closed = self.report['cleanup']['verified'] and self.report['reader_cleanup_verified'] and all(
                value.get('cleanup_verified') is True for value in self.report['runtime'].values())
            passed = (original is None and closed and self.report['callback_completed'] and
                self.report['query_drain'].get('verified') is True and self.report['fixture_unchanged'] and self.report['table_inventory_unchanged'] and
                set(self.report['runtime']) == {'prepare','cleanup'} and all(v.get('state') == 'verified' for v in self.report['runtime'].values()))
            self.report.update(result='passed' if passed else 'unknown' if not closed else
                'interrupted' if original is not None and not isinstance(original,Exception) else 'failed',
                failure_code=None if passed else 'LAKE_FIXTURE_INTERRUPTED' if original is not None and not isinstance(original,Exception) else 'LAKE_FIXTURE_FAILED',finished_at=stamp())
            try: self.persist()
            except BaseException as error:
                original=keep(original,error);self.report.update(result='unknown',failure_code='LAKE_FIXTURE_EVIDENCE_FAILED')
        if original is not None and not isinstance(original,Exception): raise original
        return deepcopy(self.report),self.value if self.report['result']=='passed' else None


def run(output, token, callback, *, reader_closed, _runtime=None, _drain=None):
    return FixtureOwner(output,token,reader_closed=reader_closed,_runtime=_runtime,_drain=_drain).run(callback)
