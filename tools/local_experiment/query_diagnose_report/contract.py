"""Pure saved diagnosis inspection; never open sources, tables or current config.

Workflow outcomes, inspection integrity and recorded model-call intent differ.
Only embedded safe evidence and saved spans are verified. Producer audit claims
are not refreshed and unsaved model responses are not independently rescored.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import math
import re

from tools.local_experiment.query_diagnose import contract as D

MAX_RUNS = 128
MAX_SUMMARY_BYTES = 2 * 1024**2
MAX_SPANS_BYTES = 1024**2
MAX_TOTAL_BYTES = 32 * 1024**2
MAX_PUBLIC_BYTES = 256 * 1024
MAX_METRIC = 10**12
STATES = ('passed','failed','interrupted','active','incomplete','invalid','missing')
READ_STATES = ('stable','active','incomplete','invalid','missing')
VERIFICATIONS = ('saved-evidence','journal-only','not-checked')
PROFILES = ('reader-v1','snapshot-reader-v2','filtered-snapshot-reader-v3')
INFERENCE_STATES = ('success','failed','blocked','not-recorded')
CORRELATIONS = ('matched','partial','not-recorded')
PRODUCER_CODES = frozenset('''ARGUMENTS_INVALID REQUEST_INVALID SOURCE_NOT_FOUND SOURCE_BUSY SOURCE_INVALID
SOURCE_CHANGED ENVIRONMENT_NOT_READY OBSERVATION_FAILED MODEL_FAILED MODEL_REJECTED AUDIT_FAILED
DIAGNOSE_FAILED INTERRUPTED EVIDENCE_ALREADY_EXISTS'''.split())
ERROR_CODES = frozenset(('REPORT_RECEIPT_INVALID','REPORT_INCOMPLETE','REPORT_NOT_FOUND'))
PUBLIC_CODES = PRODUCER_CODES | ERROR_CODES
INVENTORY_CODES = frozenset('REPORT_'+name for name in ('ROOT_INVALID','ROOT_CHANGED','ENTRY_LIMIT',
    'READ_LIMIT','PUBLIC_LIMIT','UNRECOGNIZED_ENTRIES','REQUEST_INVALID'))
LIMITATIONS = ['SAVED_DIAGNOSIS_EVIDENCE_ONLY','ORIGINAL_READER_NOT_REOPENED',
               'AUDIT_DELIVERY_CLAIMS_NOT_REFRESHED','RAW_MODEL_RESPONSE_NOT_RETAINED',
               'NO_MODEL_QUALITY_OR_CURRENT_TABLE_STATE_CLAIM']
ENTRY_KEYS = frozenset('''evidence_token state recorded_result verification read_token query_id source_profile
started_at finished_at cleanup_verified error_code history findings next_steps model audit evidence'''.split())
MODEL_KEYS = frozenset('''call_intents recorded_inference provider name prompt_tokens completion_tokens duration_ms inference_cost_estimate'''.split())
AUDIT_KEYS = frozenset('events delivered_events saved_spans producer_verified correlation'.split())
GROUP_KEYS = frozenset('''source_profile provider model terminal_runs passed failed interrupted call_intents
recorded_inference_runs known_prompt_tokens known_completion_tokens missing_prompt_usage missing_completion_usage inference_cost_estimate'''.split())
TOP_KEYS = frozenset('schema_version command selection command_result inventory runs groups limitations'.split())
SUMMARY_KEYS = frozenset('''schema_version evidence_token read_token started_at finished_at result error_code source
observation model_evidence diagnosis model_calls invocations original_select_reexecuted cloud_calls retries
audit cleanup_verified source_hashes limits'''.split())
OBSERVATION_KEYS = frozenset('''state source_sha256 plan history stats identity_before identity_after queries query_drain
runtime cleanup_verified error_code'''.split())
ACTIONS = ('bear.query_diagnose.observe','llm.query_diagnose','bear.query_diagnose.assess')
AUDIT_RECORD_KEYS = frozenset('''event_id task_id trace_id action_type status model_provider model_name prompt_tokens
completion_tokens inference_cost_estimate duration_ms span_id audit_delivery'''.split())
SPAN_KEYS = frozenset('name trace_id span_id start_time_unix_nano end_time_unix_nano status attributes'.split())
QUERY_KEYS = frozenset('''operation_id label query_id sql_sha256 started_at result response_complete post_attempted
server_max_run_seconds http_status row_count response_bytes pages error_code'''.split())
QUERY_ERROR_CODES = frozenset('''AUTHENTICATION_RETRY_FORBIDDEN CANCEL_ID_NOT_OWNED
CANCEL_PROVENANCE_INVALID CANCEL_RESPONSE_INVALID CANCEL_RETRY_FORBIDDEN CANCEL_UNCERTAIN
CATALOG_AUTHENTICATION_FAILED CATALOG_CONTENT_TYPE_INVALID CATALOG_HTTP_FAILED
CATALOG_HTTP_ORIGIN_INVALID CATALOG_ORIGIN_INVALID CATALOG_READ_FAILED CATALOG_READ_LIMIT
CATALOG_READ_NOT_READY CATALOG_RESPONSE_INVALID CATALOG_RESPONSE_LIMIT CATALOG_SCOPE_INVALID
CONTROL_INVALID CONTROL_SQL_INVALID DRAIN_IDENTITY_INVALID DRAIN_INVENTORY_INVALID
DRAIN_PROVENANCE_INVALID DRAIN_SCHEMA_INVALID DRAIN_TIMEOUT_INVALID DRAIN_UNTRACKED_QUERY
DRAIN_DEADLINE DRAIN_UNVERIFIED JOURNAL_AND_GUARD_REQUIRED JOURNAL_REQUIRED
QUERY_CELL_TYPE_INVALID QUERY_COLUMNS_CHANGED QUERY_COLUMNS_INVALID QUERY_CONTENT_TYPE_INVALID
QUERY_CONTINUATION_CHANGED QUERY_DEADLINE QUERY_ERROR_INVALID QUERY_HTTP_ORIGIN_INVALID
QUERY_ID_INVALID QUERY_INPUT_INVALID QUERY_LABEL_INVALID QUERY_NEXT_INVALID QUERY_NOT_READ_ONLY
QUERY_ORIGIN_CHANGED QUERY_OUTCOME_UNRESOLVED QUERY_PAGE_BOUND QUERY_PAGE_BYTES_BOUND
QUERY_RESPONSE_INVALID QUERY_ROWS_INVALID QUERY_ROW_BOUND QUERY_SCHEMA_MISSING QUERY_STATE_INVALID
QUERY_TERMINAL_INVALID QUERY_TOTAL_BYTES_BOUND READ_SQL_INVALID RESPONSE_BYTES_BOUND
RESPONSE_DEADLINE RESPONSE_DUPLICATE_KEY RESPONSE_JSON_INVALID RESPONSE_NONFINITE
TIMEOUT_INVALID TOKEN_INVALID TRINO_QUERY_FAILED TRINO_SERVICE_CHANGED TRINO_SERVICE_INVALID
URL_INVALID QUERY_FAILED INTERRUPTED'''.split())
HEADER_LIMITS = ['LOCAL_EXPERIMENT_ONLY','HISTORICAL_QUERY_NOT_REPLAYED','OBSERVATION_WINDOW_BEFORE_INFERENCE',
                 'NO_RAW_SQL_ROWS_OR_ENTITY_NAMES_TO_MODEL','NO_ROOT_CAUSE_OR_PERFORMANCE_CLAIM']
HISTORICAL_23 = frozenset('tools/local_experiment/'+p for p in '''bears/run_catalog.py bears/run_sre.py bears/run_sre_llm.py
iceberg_refs/client.py iceberg_refs/contract.py iceberg_refs/run.py iceberg_sandbox/run.py llm/evaluation.py
llm/providers.py llm/workflow.py local_experiment.py local_session.py local_trino_smoke.py polaris_permissions.py
query_diagnose/audit.py query_diagnose/contract.py query_diagnose/observe.py query_diagnose/provider.py
query_diagnose/run.py query_read/client.py query_read/contract.py query_read/run.py trino_opa/common.py'''.split())
HISTORICAL_27 = HISTORICAL_23 | frozenset('tools/local_experiment/'+p for p in
    ('query_diagnose/snapshot_source.py','query_snapshots/contract.py','query_snapshots/observe.py','query_snapshots/run.py'))
FILTERED_29 = HISTORICAL_27 | frozenset('tools/local_experiment/'+p for p in
    ('query_filter/contract.py','query_filter/run.py'))
UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')


class ReportError(ValueError): pass
class PublicLimitError(ReportError): pass


def require(value):
    if not value: raise ReportError('REPORT_RECEIPT_INVALID')


def canonical(value): return D.canonical(value)
def same(a,b): return canonical(a) == canonical(b)
def digest(value): return D.digest(value)
def token(value):
    require(type(value) is str and D.TOKEN.fullmatch(value)); return value
def keys(value, expected): require(type(value) is dict and set(value) == set(expected))
def number(value, maximum=MAX_METRIC): require(type(value) is int and 0 <= value <= maximum); return value
def nullable_number(value, maximum=MAX_METRIC): return None if value is None else number(value,maximum)
def boolean(value): require(type(value) is bool); return value
def stamp(value):
    require(type(value) is str and len(value) <= 40)
    parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None)
    return parsed.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')
def cost(value):
    require(value is None or type(value) in (int,float) and math.isfinite(value) and 0 <= value <= 10**9)
    return value


def minimal(identity, state):
    token(identity); require(state in STATES)
    result = {k:None for k in ENTRY_KEYS}
    result.update(evidence_token=identity,state=state,verification='not-checked',
                  model={k:None for k in MODEL_KEYS},audit={k:None for k in AUDIT_KEYS},
                  error_code={'invalid':'REPORT_RECEIPT_INVALID','incomplete':'REPORT_INCOMPLETE',
                              'missing':'REPORT_NOT_FOUND'}.get(state))
    return result


def _queries(value, identity, source, complete):
    keys(value,('worker','control'))
    require(type(value['worker']) is list and len(value['worker']) <= 5
            and type(value['control']) is list and len(value['control']) <= 32)
    statements = D.statements(source)
    require([r['label'] for r in value['worker']] == list(D.PHASES[:len(value['worker'])]))
    ids, operations = [], []
    for role in ('worker','control'):
        for row in value[role]:
            keys(row,QUERY_KEYS); require(UUID.fullmatch(row['operation_id']))
            operations.append(row['operation_id']); stamp(row['started_at'])
            D._sha(row['sql_sha256']); boolean(row['post_attempted']); boolean(row['response_complete'])
            number(row['row_count'],1024);number(row['pages'],128);number(row['response_bytes'],16*1024**2)
            require(type(row['server_max_run_seconds']) is int and row['server_max_run_seconds'] == 30)
            require(row['result'] in ('pending','finished','failed','unknown','not-sent','interrupted')
                    and (row['http_status'] is None or type(row['http_status']) is int and 100 <= row['http_status'] <= 599)
                    and (row['error_code'] is None or row['error_code'] in QUERY_ERROR_CODES))
            if row['result']=='not-sent':require(not row['post_attempted'] and row['query_id'] is None and not row['response_complete']
                and row['pages']==row['response_bytes']==row['row_count']==0 and row['error_code'] is not None)
            if row['result']=='interrupted':require(row['error_code']=='INTERRUPTED')
            if row['result']=='unknown':require(row['post_attempted'] and not row['response_complete'] and row['error_code'] is not None)
            if row['result']=='failed':require(row['post_attempted'] and row['response_complete'] and row['error_code'] is not None)
            if row['query_id'] is not None:
                D._query_id(row['query_id']);ids.append(row['query_id']);require(row['post_attempted'])
            if role == 'worker':
                require(row['sql_sha256'] == hashlib.sha256(statements[row['label']].encode()).hexdigest()
                        and row['sql_sha256'] != source['sql_sha256'] and row['query_id'] != source['query_id'])
            else:
                require(row['label'] == 'drain')
                # Fixed existing QueryClient drain compiler, reproduced as a
                # pure string below; importing a transport is unnecessary.
                expected = inspection_sql(identity)
                require(row['sql_sha256'] == hashlib.sha256(expected.encode()).hexdigest())
            if row['result'] == 'finished' or complete:
                require(row['result'] == 'finished' and row['post_attempted'] and row['response_complete']
                        and row['query_id'] is not None and row['http_status'] == 200 and row['error_code'] is None
                        and row['pages'] > 0 and row['response_bytes'] > 0)
    require(len(ids) == len(set(ids)) and len(operations) == len(set(operations)))
    require(all(r['result']=='finished' for r in value['worker'][:-1]))
    if complete: require(len(value['worker']) == 5 and len(value['control']) >= 1)


def inspection_sql(identity):
    owner = 'shirokuma-query-read-'+token(identity)
    return ('SELECT query_id, state, "user", source FROM system.runtime.queries '
            "WHERE \"user\" = '"+owner+"' ORDER BY query_id LIMIT 129")


def _runtime(observation, identity, source, complete):
    runtime = observation['runtime']; drain = observation['query_drain']; queries = observation['queries']
    require(type(drain) is dict and type(drain.get('verified')) is bool)
    if runtime is None: require(not complete);return
    require(type(runtime) is dict and runtime.get('state') in ('pending','ready','verified','failed'))
    require(type(runtime.get('cleanup')) is dict and type(runtime['cleanup'].get('verified')) is bool)
    if complete: require(runtime['state'] == 'verified' and runtime['cleanup']['verified'] and drain['verified'])
    for role, control in (('worker',False),('control',True)):
        saved = runtime.get(role)
        if saved is None: require(not complete);continue
        owner = 'shirokuma-query-read-'+('control-' if control else '')+identity
        require(saved['user'] == owner and saved['source'] == owner and same(saved['records'],queries[role])
                and type(saved['cancellations']) is list)
        known_ids={r['query_id'] for r in queries[role] if r['query_id'] is not None}
        cancel_ids=[]
        for row in saved['cancellations']:
            keys(row,('query_id','result','http_status'));require(row['query_id'] in known_ids
                and row['result'] in ('pending','accepted','unknown'))
            cancel_ids.append(row['query_id'])
            require(row['http_status'] is None or type(row['http_status']) is int and 100 <= row['http_status'] <= 599)
            if row['result']=='accepted':require(row['http_status']==204)
        require(len(cancel_ids)==len(set(cancel_ids)))
        if complete: require(saved['cancellations'] == [])
    if drain['verified']:
        owner = 'shirokuma-query-read-'+identity
        require(drain['user'] == owner and drain['source'] == owner
                and all(drain[k] == [] for k in ('active','errors')))
        known = sorted(r['query_id'] for r in queries['worker'] if r['query_id'] is not None)
        require(sorted(drain['observed_query_ids']) == known)
        for field in ('cancelled','cancel_errors'):
            require(type(drain[field]) is list and len(set(drain[field]))==len(drain[field]) and set(drain[field])<=set(known))
        if complete:require(drain['cancelled']==drain['cancel_errors']==[])
    if complete:
        for component,name in (('trino','trino-local'),('polaris','polaris')):
            saved = runtime[component]; service = saved['service']
            require(saved['state'] == 'verified' and service['name'] == name and service['namespace'] == 'shirokuma-dev'
                    and UUID.fullmatch(service['uid'])); D._sha(service['spec_sha256'])
        catalog = runtime['catalog']; reads = catalog['reads']
        require(catalog['authenticated'] is True and catalog['authentication_attempted'] is True
                and len(reads) == 2 and same(reads[0],reads[1]))
        for row in reads:
            keys(row,('result','request_sha256','metadata_sha256'))
            require(row['result'] == 'verified' and row['request_sha256'] == digest(D.base_request(source)))
            D._sha(row['metadata_sha256'])


def _observation(value, source, identity, complete):
    if value is None: require(not complete);return
    require(source is not None)
    expected = OBSERVATION_KEYS | ({'snapshot_catalog_before','snapshot_catalog_after'} if source['schema_version'] >= 2 else set())
    keys(value,expected);require(value['state'] in ('pending','observing','verified','failed','interrupted'))
    require(value['source_sha256'] in (None,digest(source)) and type(value['cleanup_verified']) is bool
            and value['error_code'] in (None,'OBSERVATION_FAILED','INTERRUPTED'))
    if complete: require(value['state'] == 'verified' and value['cleanup_verified'] and value['error_code'] is None
                         and value['source_sha256']==digest(source))
    for phase in ('before','after'):
        observed = value['identity_'+phase]
        if observed is not None:
            D.verify_source_window(source,{'schema_version':2 if source['schema_version'] >= 2 else 1,'identity':observed,
                'snapshots':value.get('snapshot_catalog_'+phase)})
        elif complete: require(False)
    _queries(value['queries'],identity,source,complete);_runtime(value,identity,source,complete)
    for kind,index,validator in (('plan',1,D.validate_plan),('history',2,D.validate_history),('stats',3,D.validate_stats)):
        if value[kind] is not None:
            validator(value[kind]); require(len(value['queries']['worker']) > index
                and value[kind]['query_id'] == value['queries']['worker'][index]['query_id'])
            if kind == 'plan':
                require(value[kind]['format'] == (D.FILTER_PLAN_FORMAT if source['schema_version'] == 3 else D.PLAN_FORMAT))
        elif complete: require(False)


def _audit(saved, spans, identity, intent, complete, terminal_failure=False):
    model = dict.fromkeys(MODEL_KEYS); model.update(call_intents=intent,recorded_inference='not-recorded')
    projected = dict(events=0,delivered_events=0,saved_spans=0,producer_verified=False,correlation='not-recorded')
    if same(saved,{'verified':False}):
        require(not complete and spans in (None,[]));return model,projected,False
    keys(saved,('task_id','trace_id','records','verified','saved_spans_verified'))
    boolean(saved['verified']);boolean(saved['saved_spans_verified'])
    if saved['trace_id'] is None:
        require(not complete and saved['task_id']=='workflow-'+identity and saved['records']==[]
                and not saved['verified'] and not saved['saved_spans_verified'] and spans in (None,[]))
        return model,projected,False
    require(saved['task_id'] == 'workflow-'+identity and type(saved['trace_id']) is str
            and re.fullmatch('[0-9a-f]{32}',saved['trace_id']))
    records = saved['records'];require(type(records) is list and len(records) <= 3)
    actions = [r['action_type'] for r in records]
    require(actions in ([],[ACTIONS[0]],[ACTIONS[0],ACTIONS[1]],[ACTIONS[0],ACTIONS[2]],list(ACTIONS),[ACTIONS[2]]))
    event_ids, span_ids = [], []
    for row in records:
        keys(row,AUDIT_RECORD_KEYS);require(UUID.fullmatch(row['event_id']) and re.fullmatch('[0-9a-f]{16}',row['span_id']))
        event_ids.append(row['event_id']);span_ids.append(row['span_id'])
        require(row['task_id'] == saved['task_id'] and row['trace_id'] == saved['trace_id']
                and row['status'] in ('success','failed','blocked') and row['audit_delivery'] in ('pending','delivered','failed'))
        nullable_number(row['duration_ms']);cost(row['inference_cost_estimate'])
        if row['action_type'] == ACTIONS[1]:
            require(intent == 1 and row['model_provider'] == 'mtplx' and row['model_name'] == 'qwen3.8-flash-next')
            nullable_number(row['prompt_tokens']);nullable_number(row['completion_tokens'])
            model.update(recorded_inference=row['status'],provider=row['model_provider'],name=row['model_name'],
                **{k:row[k] for k in ('prompt_tokens','completion_tokens','duration_ms','inference_cost_estimate')})
        else:
            require(row['model_provider'] == 'none' and row['model_name'] == '' and row['prompt_tokens'] is None
                    and row['completion_tokens'] is None and row['inference_cost_estimate'] is None and row['status'] != 'blocked')
    require(len(set(event_ids)) == len(event_ids) and len(set(span_ids)) == len(span_ids))
    if ACTIONS[1] in actions:require(records[0]['action_type']==ACTIONS[0] and records[0]['status']=='success')
    projected.update(events=len(records),delivered_events=sum(r['audit_delivery']=='delivered' for r in records),
                     producer_verified=saved['verified'])
    if spans is None:
        projected.update(saved_spans=None,correlation='partial' if records else 'not-recorded')
        return model,projected,bool(records)
    require(type(spans) is list and len(spans) <= 3 and len(canonical(spans)) <= MAX_SPANS_BYTES)
    expected = {r['span_id']:r for r in records};seen=set()
    for span in spans:
        keys(span,SPAN_KEYS);sid=span['span_id'];require(sid in expected and sid not in seen);seen.add(sid)
        row = expected[sid]
        # finish records an intended successful assessment before remote audit
        # verification. A later failure/cancellation closes that root as ERROR;
        # it must remain a failed workflow, not a corrupt or successful one.
        failed_finish = (terminal_failure and not saved['verified'] and not saved['saved_spans_verified']
            and row['action_type']==ACTIONS[2] and row['status']=='success' and span['status']=='ERROR')
        require(span['trace_id'] == saved['trace_id'] and span['name'] == row['action_type']
                and (span['status'] == ('OK' if row['status']=='success' else 'ERROR') or failed_finish))
        start,end = span['start_time_unix_nano'],span['end_time_unix_nano']
        require(type(start) is int and type(end) is int and 0 < start <= end < 2**63)
        require(same(span['attributes'], {'task_id':saved['task_id']} if span['name']==ACTIONS[2] else {}))
    matched = len(seen) == len(records)
    projected.update(saved_spans=len(spans),correlation='matched' if matched and records else 'partial' if records else 'not-recorded')
    if complete:
        require(actions == list(ACTIONS) and saved['verified'] and saved['saved_spans_verified']
                and intent == 1 and all(r['status']=='success' and r['audit_delivery']=='delivered' for r in records))
    return model,projected,not matched


def project_run(token_value, summary, spans, read_state='stable'):
    """Return one safe entry; malformed input is visible, never a success.

    UTC timestamps are normalized to six fractional digits with Z. A busy lock
    supplies read_state=active and no concurrently changing fields are trusted.
    Missing spans use None, a known empty export uses []. Cancellation propagates.
    """
    identity=token(token_value);require(read_state in READ_STATES)
    if read_state != 'stable': return minimal(identity,read_state)
    try:
        require(len(canonical(summary)) <= MAX_SUMMARY_BYTES);keys(summary,SUMMARY_KEYS)
        v=summary;require(type(v['schema_version']) is int and v['schema_version']==1 and v['evidence_token']==identity)
        token(v['read_token']);require(v['read_token'] != identity and v['result'] in ('running','passed','failed','interrupted'))
        require(v['error_code'] is None or v['error_code'] in PRODUCER_CODES)
        start=stamp(v['started_at']);end=None if v['finished_at'] is None else stamp(v['finished_at'])
        require(end is None or end >= start)
        number(v['model_calls'],1);number(v['invocations'],1);boolean(v['cleanup_verified'])
        if v['invocations']==0:
            require(v['result']=='running' and v['model_calls']==0 and v['source'] is None and v['observation'] is None
                    and v['model_evidence'] is None and v['diagnosis'] is None and same(v['audit'],{'verified':False}))
        require(v['original_select_reexecuted'] is False and type(v['cloud_calls']) is int and v['cloud_calls']==0
                and type(v['retries']) is int and v['retries']==0 and same(v['limits'],HEADER_LIMITS))
        hashes=v['source_hashes'];require(type(hashes) is dict and set(hashes) in (HISTORICAL_23,HISTORICAL_27,FILTERED_29))
        for h in hashes.values():D._sha(h)
        source = None if v['source'] is None else D.validate_source(v['source'])
        if source is not None: require(source['read_token']==v['read_token'])
        if source is not None and source['schema_version']==2:require(set(hashes) in (HISTORICAL_27,FILTERED_29))
        if source is not None and source['schema_version']==3:require(set(hashes)==FILTERED_29)
        complete = v['result']=='passed' and end is not None
        if complete: require(source is not None and v['invocations']==v['model_calls']==1 and v['cleanup_verified'] and v['error_code'] is None)
        if v['result']=='running':require(end is None and v['error_code'] is None)
        if v['result']=='interrupted' and end is not None:require(v['error_code']=='INTERRUPTED')
        if v['result']=='failed' and end is not None:require(v['error_code'] in PRODUCER_CODES)
        _observation(v['observation'],source,identity,complete)
        if v['model_evidence'] is not None:
            require(source is not None and v['observation'] is not None)
            o=v['observation'];expected=D.build_evidence(source,o['plan'],o['history'],o['stats'])
            require(same(expected,v['model_evidence']))
        elif complete:require(False)
        if v['diagnosis'] is not None:
            require(v['model_evidence'] is not None and v['model_calls']==1)
            D.validate_diagnosis(v['diagnosis'],v['model_evidence'])
        elif complete:require(False)
        model,audit,partial = _audit(v['audit'],spans,identity,v['model_calls'],complete,
                                    v['result'] in ('failed','interrupted') and end is not None)
        state = 'incomplete' if end is None or partial else v['result']
        require(state in STATES)
        result=minimal(identity,state)
        result.update(recorded_result=v['result'],verification='saved-evidence' if state!='incomplete' and source is not None and audit['correlation']=='matched' else 'journal-only',
            read_token=v['read_token'],query_id=source['query_id'] if source else None,
            source_profile=PROFILES[source['schema_version']-1] if source else None,
            started_at=start,finished_at=end,cleanup_verified=v['cleanup_verified'],error_code=v['error_code'] if state!='incomplete' else 'REPORT_INCOMPLETE',
            history=v['observation']['history']['status'] if v['observation'] and v['observation']['history'] else None,
            model=model,audit=audit,evidence=f'tmp/local-experiment/query-diagnose/{identity}/summary.json')
        if state=='passed':result.update(findings=v['diagnosis']['finding_ids'],next_steps=v['diagnosis']['next_step_ids'])
        return _entry(result)
    except (ValueError,KeyError,TypeError,OverflowError,RecursionError):
        return minimal(identity,'invalid')


def _entry(value):
    keys(value,ENTRY_KEYS);identity=token(value['evidence_token']);require(value['state'] in STATES and value['verification'] in VERIFICATIONS)
    require(value['recorded_result'] in (None,'running','passed','failed','interrupted'))
    for field in ('read_token','query_id'):
        if value[field] is not None:(token if field=='read_token' else D._query_id)(value[field])
    require(value['source_profile'] is None or value['source_profile'] in PROFILES)
    for field in ('started_at','finished_at'):
        if value[field] is not None: require(stamp(value[field])==value[field])
    require(value['cleanup_verified'] is None or type(value['cleanup_verified']) is bool)
    require(value['error_code'] is None or value['error_code'] in PUBLIC_CODES)
    require(value['history'] in (None,'observed','not-retained'))
    require(value['evidence'] in (None,f'tmp/local-experiment/query-diagnose/{identity}/summary.json'))
    model=value['model'];keys(model,MODEL_KEYS);nullable_number(model['call_intents'],1)
    require(model['recorded_inference'] is None or model['recorded_inference'] in INFERENCE_STATES)
    require((model['provider'],model['name']) in ((None,None),('mtplx','qwen3.8-flash-next')))
    for key in ('prompt_tokens','completion_tokens','duration_ms'):nullable_number(model[key])
    cost(model['inference_cost_estimate'])
    if model['recorded_inference'] in (None,'not-recorded'):
        require(all(model[k] is None for k in ('provider','name','prompt_tokens','completion_tokens','duration_ms','inference_cost_estimate')))
    else:require(model['call_intents']==1 and model['provider'] is not None)
    audit=value['audit'];keys(audit,AUDIT_KEYS)
    for key in ('events','delivered_events','saved_spans'):nullable_number(audit[key],3)
    require(audit['producer_verified'] is None or type(audit['producer_verified']) is bool)
    require(audit['correlation'] is None or audit['correlation'] in CORRELATIONS)
    if audit['events'] is not None:require(audit['delivered_events'] <= audit['events'])
    if audit['correlation']=='matched':require(audit['events']==audit['saved_spans'] and audit['events']>0)
    if value['state']=='passed':
        require(value['recorded_result']=='passed' and value['verification']=='saved-evidence' and value['cleanup_verified'] is True
                and value['source_profile'] is not None and value['read_token'] is not None and value['query_id'] is not None
                and value['finished_at'] is not None and value['history'] is not None and value['error_code'] is None
                and model['recorded_inference']=='success' and audit['producer_verified'] is True
                and audit['correlation']=='matched' and audit['events']==audit['delivered_events']==3)
        findings=value['findings'];steps=value['next_steps']
        require(type(findings) is list and findings==sorted(set(findings)) and set(findings)<=D.FINDING_STEPS.keys())
        require(type(steps) is list and steps==sorted({D.FINDING_STEPS[f] for f in findings}|{'MEASURE_BEFORE_OPTIMIZING'}))
        require(('HISTORY_NOT_RETAINED' in findings)==(value['history']=='not-retained'))
    else:require(value['findings'] is None and value['next_steps'] is None)
    if value['state'] in ('failed','interrupted'):
        require(value['recorded_result']==value['state'] and value['finished_at'] is not None and value['started_at'] is not None
                and value['read_token'] is not None and value['evidence'] is not None and type(value['cleanup_verified']) is bool
                and value['error_code'] in PRODUCER_CODES and model['call_intents'] is not None
                and value['verification'] in ('saved-evidence','journal-only'))
        if value['state']=='interrupted':require(value['error_code']=='INTERRUPTED')
        if value['verification']=='saved-evidence':require(value['source_profile'] is not None and audit['correlation']=='matched')
    if value['state']=='incomplete':
        require(value['error_code']=='REPORT_INCOMPLETE' and value['verification'] in ('not-checked','journal-only'))
    if value['finished_at'] is not None: require(value['started_at'] is not None and value['finished_at']>=value['started_at'])
    if value['state'] in ('active','invalid','missing'):require(same(value,minimal(identity,value['state'])))
    return deepcopy(value)


def _groups(entries):
    groups={}
    for row in entries:
        if row['state'] not in ('passed','failed','interrupted') or row['verification']!='saved-evidence':continue
        m=row['model'];key=(row['source_profile'],m['provider'],m['name'])
        if key not in groups:
            groups[key]={'source_profile':key[0],'provider':key[1],'model':key[2],
                **{k:0 for k in GROUP_KEYS-{'source_profile','provider','model','inference_cost_estimate'}},'inference_cost_estimate':None}
        g=groups[key];g['terminal_runs']+=1;g[row['state']]+=1;g['call_intents']+=m['call_intents']
        g['recorded_inference_runs']+=m['recorded_inference'] not in (None,'not-recorded')
        for field in ('prompt','completion'):
            value=m[field+'_tokens'];g['known_'+field+'_tokens']+=value if value is not None else 0
            g['missing_'+field+'_usage']+=m['call_intents']==1 and value is None
    # Costs stay unknown if any attempted inference is unobserved or missing a
    # cost estimate. No observations also means unknown, not free inference.
    for key,g in groups.items():
        costs=[r['model']['inference_cost_estimate'] for r in entries if r['state'] in ('passed','failed','interrupted')
            and r['verification']=='saved-evidence' and (r['source_profile'],r['model']['provider'],r['model']['name'])==key
            and r['model']['call_intents']==1]
        if costs and all(c is not None for c in costs):
            # build_report sorts entries by token. Match the public Go reader's
            # ordered float64 additions, independent of Python sum's algorithm.
            total=0.0
            for estimate in costs:total+=estimate
            g['inference_cost_estimate']=total
    return [groups[k] for k in sorted(groups,key=lambda k:tuple(x or '' for x in k))]


def build_report(entries, requested_token, inventory):
    """Aggregate inspected entries only; never silently claim truncated coverage."""
    require(requested_token is None or token(requested_token));keys(inventory,('root','complete','inspected_entries','unrecognized_entries','error_code'))
    require(inventory['root'] in ('present','absent','not-observed'));boolean(inventory['complete'])
    require(inventory['error_code'] is None or inventory['error_code'] in INVENTORY_CODES)
    if inventory['error_code'] is not None:require(not inventory['complete'])
    number(inventory['inspected_entries'],MAX_RUNS);number(inventory['unrecognized_entries'],10**6)
    require(type(entries) is list and len(entries)<=MAX_RUNS)
    rows=[_entry(r) for r in entries];require(len({r['evidence_token'] for r in rows})==len(rows))
    if requested_token is not None:require(len(rows)==1 and rows[0]['evidence_token']==requested_token)
    require(inventory['inspected_entries']==len(rows))
    if inventory['root']=='absent':require(not rows or requested_token is not None and rows[0]['state']=='missing')
    rows.sort(key=lambda r:r['evidence_token'])
    counts={state:sum(r['state']==state for r in rows) for state in STATES}
    complete=(inventory['complete'] and inventory['root']!='not-observed' and inventory['unrecognized_entries']==0
              and not any(counts[k] for k in ('incomplete','invalid','missing')))
    result={'schema_version':1,'command':'query diagnose report','selection':{'mode':'all' if requested_token is None else 'single','token':requested_token},
            'command_result':'complete' if complete else 'incomplete','inventory':{**inventory,'counts':counts},
            'runs':rows,'groups':_groups(rows),'limitations':list(LIMITATIONS)}
    if len(canonical(result))>MAX_PUBLIC_BYTES:raise PublicLimitError('REPORT_PUBLIC_LIMIT')
    return result


def validate_public(report):
    keys(report,TOP_KEYS);require(type(report['schema_version']) is int and report['schema_version']==1
        and report['command']=='query diagnose report' and same(report['limitations'],LIMITATIONS))
    selection=report['selection'];keys(selection,('mode','token'))
    require(selection['mode']==('all' if selection['token'] is None else 'single'))
    inventory=report['inventory'];keys(inventory,('root','complete','inspected_entries','unrecognized_entries','error_code','counts'))
    expected=build_report(report['runs'],selection['token'],{k:v for k,v in inventory.items() if k!='counts'})
    require(same(report,expected));return deepcopy(report)
