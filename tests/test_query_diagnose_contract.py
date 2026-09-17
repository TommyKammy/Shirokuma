"""Pure saved-Reader diagnosis cases; no filesystem, service, or inference calls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import unittest

from tools.local_experiment.query_diagnose import contract as C
from tools.local_experiment.query_diagnose.provider import DiagnoseProvider, ProviderError
from test_query_read_contract import identity, inventory, request, result, schema

TOKEN = 'a' * 32
IDS = ['20260918_000000_0000' + str(n) + '_abcde' for n in range(1, 5)]


def source(*, limit=2):
    req = request(limit=limit)
    selected = C.R.receipt_projection(C.R.project_rows(result(req), req, schema(), identity()))
    return C.source_context(TOKEN, req, inventory(), identity(), selected, IDS[0], 'b' * 64)


def plan_result(src=None):
    src = src or source()
    def node(index, kind, children, descriptor=None):
        return {'id':str(index), 'name':kind, 'descriptor':descriptor or {},
            'outputs':[], 'details':'private-plan-detail-sentinel',
            'estimates':[{key:0 for key in C.ESTIMATES}], 'children':children}
    scan = node(2, 'TableScan', [], {'table':'iceberg:' + src['request']['table'] + '$data@'
                                  + src['identity']['current_snapshot_id']})
    limit = node(1, 'Limit', [scan], {'count':str(src['request']['limit']+1)})
    raw = {'0':node(0, 'Output', [limit])}
    return {'query_id':IDS[1], 'columns':['Query Plan'], 'column_types':['varchar'],
            'rows':[[json.dumps(raw)]]}


def history_result(src=None, *, missing=False, duration_ms=1001):
    src = src or source()
    begin = datetime(2026,9,18,tzinfo=timezone.utc)
    end = begin + timedelta(milliseconds=duration_ms)
    stamp = lambda t:t.isoformat(timespec='microseconds').replace('+00:00','Z')
    select = C.R.select_sql(src['request'], src['inventory'], src['identity']['columns'], src['identity'])
    return {'query_id':IDS[2], 'columns':list(C.HISTORY_COLUMNS), 'column_types':list(C.HISTORY_TYPES),
            'rows':[] if missing else [[src['query_id'],'FINISHED',src['user'],src['source'],select,
                                      0,0,0,stamp(begin),stamp(begin),stamp(end),None,None]]}


def stats_result(src=None):
    src = src or source()
    rows = [[c['name'],Decimal('0'),Decimal('2'),Decimal('0'),None,
             'private-low-value-sentinel','private-high-value-sentinel'] for c in src['identity']['columns']]
    rows.append([None,None,None,None,Decimal('2'),None,None])
    return {'query_id':IDS[3], 'columns':list(C.STATS_COLUMNS), 'column_types':list(C.STATS_TYPES), 'rows':rows}


def projections(src=None, *, missing=False):
    src = src or source()
    return {'plan':C.project_plan(plan_result(src),src),
            'history':C.project_history(history_result(src,missing=missing),src),
            'stats':C.project_stats(stats_result(src),src)}


def evidence(src=None, *, missing=False):
    src = src or source()
    return C.build_evidence(src, **projections(src,missing=missing))


class SourceContractTests(unittest.TestCase):
    def test_exact_saved_scope_int64_identity_and_no_current_source_hash_dependency(self):
        value = source()
        self.assertEqual(set(value), C.SOURCE_KEYS)
        self.assertEqual(value['identity']['current_snapshot_id'], '8088807033386546123')
        self.assertEqual(C.validate_source(value), value)
        self.assertEqual(value['sql_sha256'], C.hashlib.sha256(
            C.statements(value)['explain'].removeprefix('EXPLAIN (TYPE DISTRIBUTED, FORMAT JSON) ').encode()).hexdigest())
        self.assertNotIn('rows', value['selection'])

    def test_nonselect_incomplete_inventory_and_altered_selection_rejected(self):
        for edit in (lambda v:v['request'].update(command='describe'),
                     lambda v:v['inventory'].update(complete=False),
                     lambda v:v['selection'].update(snapshot_id='1'),
                     lambda v:v['selection'].update(row_count=True),
                     lambda v:v['selection'].update(truncated=True),
                     lambda v:v['selection'].update(rows=[]),
                     lambda v:v.update(sql_sha256='0'*64),
                     lambda v:v.update(source='foreign'),
                     lambda v:v.update(schema_version=True)):
            value = source(); edit(value)
            with self.subTest(value=value), self.assertRaises(C.ContractError): C.validate_source(value)

    def test_input_id_path_sql_and_receipt_hash_boundaries(self):
        for key, val in (('read_token','../elsewhere'),('query_id','x; SELECT secret'),
                         ('summary_sha256','x'*64),('user','other'),('extra',None)):
            value = source(); value[key] = val
            with self.subTest(key=key), self.assertRaises(C.ContractError): C.validate_source(value)

    def test_statements_exact_five_reads_no_original_data_select(self):
        value = source(); sql = C.statements(value)
        self.assertEqual(tuple(sql), C.PHASES)
        self.assertEqual(sql['schema_before'], sql['schema_after'])
        self.assertTrue(sql['stats'].startswith('SHOW STATS FOR "iceberg"."smoke"."fixture_v1"'))
        self.assertIn("WHERE query_id = '" + IDS[0] + "' LIMIT 2",sql['history'])
        self.assertNotIn("WHERE \"user\"",sql['history'])
        original = C.R.select_sql(value['request'],value['inventory'],schema(),identity())
        self.assertNotIn(original, sql.values())
        self.assertEqual(sql['explain'], 'EXPLAIN (TYPE DISTRIBUTED, FORMAT JSON) ' + original)


class ProjectionTests(unittest.TestCase):
    def test_plan_discards_private_text_retains_zero_and_known_structure(self):
        projected = C.project_plan(plan_result(),source())
        self.assertEqual(projected['node_count'],3)
        self.assertEqual(projected['estimate_slots'],15)
        self.assertEqual(projected['estimate_zero_count'],15)
        self.assertTrue(projected['estimates_complete'])
        text = C.canonical(projected).decode()
        for secret in ('private-plan-detail','fixture_v1', '8088807033386546123'): self.assertNotIn(secret,text)

    def test_plan_unknown_and_empty_are_missing_but_zero_is_known(self):
        raw = plan_result(); tree = json.loads(raw['rows'][0][0])
        tree['0']['estimates'][0]['cpuCost'] = 'NaN'
        tree['0']['children'][0]['estimates'] = []
        raw['rows'][0][0] = json.dumps(tree)
        value = C.project_plan(raw,source())
        self.assertEqual(value['estimate_unknown_count'],1)
        self.assertEqual(value['empty_estimate_nodes'],1)
        self.assertFalse(value['estimates_complete'])

    def test_multifragment_limitpartial_is_supported_without_conflating_node_counts(self):
        raw = plan_result(); tree = json.loads(raw['rows'][0][0])
        child = tree['0']['children'][0]
        tree['1'] = child; child['name'] = 'LimitPartial'
        tree['0']['children'] = [{'id':'3','name':'RemoteSource','descriptor':{},'estimates':[], 'children':[]}]
        raw['rows'][0][0] = json.dumps(tree)
        value = C.project_plan(raw,source())
        self.assertEqual(value['fragment_count'],2)
        self.assertEqual(value['node_count'],4)

    def test_plan_foreign_table_limit_mutation_unsupported_operator_and_duplicate_id_fail(self):
        def changed(which):
            raw = plan_result(); tree = json.loads(raw['rows'][0][0]); limit = tree['0']['children'][0]
            if which == 'table': limit['children'][0]['descriptor']['table'] += 'other'
            elif which == 'limit': limit['descriptor']['count'] = '500'
            elif which == 'kind': tree['0']['name'] = 'TableWriter'
            else: limit['id'] = '0'
            raw['rows'][0][0] = json.dumps(tree); return raw
        for which in ('table','limit','kind','id'):
            with self.subTest(which=which), self.assertRaises(C.ContractError): C.project_plan(changed(which),source())

    def test_exact_iceberg_limit_pushdown_suffix_is_supported(self):
        def pushed(suffix):
            raw=plan_result(); tree=json.loads(raw['rows'][0][0])
            tree['0']['children'][0]['children'][0]['descriptor']['table'] += suffix
            raw['rows'][0][0]=json.dumps(tree); return raw
        self.assertEqual(C.project_plan(pushed(' LIMIT 3'),source()),C.project_plan(plan_result(),source()))
        for suffix in (' LIMIT 2',' LIMIT 4',' LIMIT 03',' LIMIT 3 secret',' LIMIT 3\n','@1 LIMIT 3'):
            with self.subTest(suffix=suffix), self.assertRaises(C.ContractError):
                C.project_plan(pushed(suffix),source())

    def test_plan_duplicate_json_and_size_boundaries(self):
        for raw in ('{"0":{},"0":{}}', ' '* (C.MAX_PLAN_BYTES+1), '{"0":NaN}'):
            value=plan_result(); value['rows'][0][0]=raw
            with self.subTest(raw=raw[:20]), self.assertRaises(C.ContractError): C.project_plan(value,source())

    def test_history_integer_milliseconds_no_float_rounding(self):
        for elapsed in (0,1001,1003,1011):
            with self.subTest(elapsed=elapsed):
                value = C.project_history(history_result(duration_ms=elapsed),source())
                self.assertEqual(value['created_to_end_ms'],elapsed)
                self.assertEqual(value['started_to_end_ms'],elapsed)
                self.assertEqual(value['queued_ms'],0)

    def test_absent_history_is_natural_missing_without_expiry_claim(self):
        value = C.project_history(history_result(missing=True),source())
        self.assertEqual(value['status'],'not-retained')
        self.assertTrue(all(value[k] is None for k in C.TIMINGS))
        self.assertNotIn('expired',C.canonical(value).decode())

    def test_history_foreign_id_user_source_sql_or_failed_state_rejected(self):
        for index, val in ((0,IDS[3]),(1,'FAILED'),(2,'foreign'),(3,'foreign'),(4,'SELECT secret'),
                           (5,True),(6,-1),(7,2**63),(8,'not-a-date'),(10,'2025-01-01T00:00:00Z'),
                           (11,'USER_ERROR'),(12,'SOME_ERROR')):
            value = history_result(); value['rows'][0][index]=val
            with self.subTest(index=index), self.assertRaises(C.ContractError): C.project_history(value,source())

    def test_history_missing_timestamps_remain_unknown_and_multiple_rows_fail(self):
        raw = history_result(); raw['rows'][0][9]=None
        value=C.project_history(raw,source())
        self.assertIsNone(value['started_to_end_ms'])
        self.assertEqual(value['created_to_end_ms'],1001)
        raw['rows'].append(deepcopy(raw['rows'][0]))
        with self.assertRaises(C.ContractError): C.project_history(raw,source())

    def test_stats_drop_low_high_names_decimal_estimates_and_zero_preserved(self):
        value=C.project_stats(stats_result(),source())
        self.assertEqual(value['row_count_estimate'],2)
        self.assertEqual(value['numeric_slots'],16)
        self.assertEqual(value['zero_count'],10)
        self.assertTrue(value['estimates_complete'])
        text=C.canonical(value).decode()
        for secret in ('private-low','private-high','fixture_v1','amount'): self.assertNotIn(secret,text)

    def test_stats_unknown_vs_malformed_schema_column_or_fraction(self):
        raw=stats_result(); raw['rows'][0][1]=None
        value=C.project_stats(raw,source())
        self.assertEqual(value['unknown_count'],1)
        self.assertFalse(value['estimates_complete'])
        for edit in (lambda r:r['rows'][0].__setitem__(0,'foreign'),
                     lambda r:r['rows'][0].__setitem__(3,Decimal('1.1')),
                     lambda r:r['rows'][0].__setitem__(1,True),
                     lambda r:r['rows'].pop(),
                     lambda r:r['column_types'].__setitem__(1,'bigint')):
            bad=stats_result();edit(bad)
            with self.assertRaises(C.ContractError):C.project_stats(bad,source())


class EvidenceTests(unittest.TestCase):
    def test_evidence_has_no_source_identifiers_rows_or_query_text(self):
        value=evidence(); text=C.canonical(value).decode()
        for secret in (TOKEN,IDS[0],'fixture_v1','8088807033386546123','SELECT ','private-','Shirokuma'):
            self.assertNotIn(secret,text)
        self.assertEqual(value['findings'],[])
        self.assertLess(len(C.canonical(value)),C.MAX_EVIDENCE_BYTES)

    def test_missing_truncated_and_estimate_findings_exact_and_deduplicated(self):
        src=source(limit=1); values=projections(src,missing=True)
        values['plan'].update(estimate_unknown_count=1,estimate_zero_count=14,estimates_complete=False)
        values['stats'].update(unknown_count=1,zero_count=9,estimates_complete=False)
        ev=C.build_evidence(src,**values); expected=C.expected_diagnosis(ev)
        self.assertEqual(expected['finding_ids'],['HISTORY_NOT_RETAINED','PLAN_ESTIMATES_INCOMPLETE',
                                                'RESULT_LIMIT_REACHED','TABLE_STATISTICS_INCOMPLETE'])
        self.assertEqual(expected['next_step_ids'].count('REVIEW_TABLE_STATISTICS'),1)
        self.assertIn('MEASURE_BEFORE_OPTIMIZING',expected['next_step_ids'])
        self.assertEqual(C.validate_diagnosis(expected,ev),expected)

    def test_positive_queue_and_missing_timing_are_observations_without_bottleneck_claim(self):
        values=projections(); values['history'].update(queued_ms=1,planning_ms=None)
        ev=C.build_evidence(source(),**values)
        self.assertEqual(C.expected_diagnosis(ev)['finding_ids'],['HISTORY_TIMING_INCOMPLETE','QUEUE_TIME_OBSERVED'])
        self.assertEqual(C.expected_diagnosis(ev)['root_cause'],'undetermined')

    def test_projection_counter_boolean_and_reused_query_id_are_rejected(self):
        for group,key,val in (('plan','node_count',True),('plan','estimate_unknown_count',16),
                              ('stats','estimates_complete',1),('stats','zero_count',17),
                              ('history','queued_ms',False),('history','query_id',IDS[1])):
            values=projections(); values[group][key]=val
            with self.subTest(group=group,key=key), self.assertRaises(C.ContractError):C.build_evidence(source(),**values)

    def test_model_missing_extra_recommendations_cause_and_boolean_schema_are_rejected(self):
        ev=evidence(missing=True)
        for edit in (lambda v:v.update(schema_version=True),lambda v:v.update(finding_ids=[]),
                     lambda v:v['finding_ids'].append('HISTORY_NOT_RETAINED'),
                     lambda v:v['next_step_ids'].append('CREATE_INDEX'),
                     lambda v:v.update(root_cause='queue'),lambda v:v.update(sql='ANALYZE secret'),
                     lambda v:v.update(performance_gain='proven')):
            value=C.expected_diagnosis(ev);edit(value)
            with self.assertRaises(C.ContractError):C.validate_diagnosis(value,ev)

    def test_evidence_foreign_free_text_finding_tamper_or_missing_rule_rejected(self):
        for edit in (lambda v:v.update(comment='private'),lambda v:v['reader'].update(table='secret'),
                     lambda v:v.update(findings=[]),lambda v:v['history'].update(queued_ms=0),
                     lambda v:v['limitations'].pop()):
            value=evidence(missing=True);edit(value)
            with self.assertRaises(C.ContractError):C.validate_evidence(value)

    def test_public_summary_exact_bound_with_natural_missing_success(self):
        src=source();ev=evidence(src,missing=True)
        value=C.public_summary(src,ev,C.expected_diagnosis(ev))
        self.assertEqual(len(value),10)
        self.assertEqual(value['history'],'not-retained')
        self.assertFalse(value['original_select_reexecuted'])
        self.assertEqual(value['model_calls'],1)
        self.assertLess(len(C.canonical(value)),1024)
        with self.assertRaises(C.ContractError):C.public_summary(source(limit=1),ev,C.expected_diagnosis(ev))


class ProviderTests(unittest.TestCase):
    def provider(self):
        return DiagnoseProvider({'kind':'local-compatible','enabled':True,
            'endpoint':'http://127.0.0.1:18000/v1','model':'local-test',
            'timeout_seconds':30,'max_output_tokens':256,'enable_thinking':False})

    def test_provider_fixed_phase_local_payload_no_tools_or_source_text(self):
        body=self.provider().request_body('query_diagnose',evidence(missing=True))
        self.assertEqual(body['temperature'],0)
        self.assertFalse(body['stream'])
        self.assertFalse(body['enable_thinking'])
        self.assertNotIn('tools',body)
        self.assertLess(len(json.dumps(body).encode()),8192)
        message=json.loads(body['messages'][1]['content'])
        self.assertEqual(message['evidence'],evidence(missing=True))
        for secret in ('fixture_v1',TOKEN,IDS[0],'private-', 'SELECT "'):
            self.assertNotIn(secret,json.dumps(body))

    def test_provider_unsupported_phase_and_invalid_evidence_fail_before_transport(self):
        with self.assertRaises(ProviderError):self.provider().request_body('query_plan',evidence())
        with self.assertRaises(C.ContractError):self.provider().request_body('query_diagnose',{'sql':'SELECT secret'})


if __name__ == '__main__':
    unittest.main()
