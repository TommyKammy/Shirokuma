"""Pure request/compiler/projection tests; never connect to services."""
from copy import deepcopy
from decimal import Decimal
import json
import unittest

from tools.local_experiment.query_read import contract as C

UUID = '00000000-0000-4000-8000-000000000001'
SNAPSHOT = 8088807033386546123


def schema():
    return [{'name':'id','type':'bigint','nullable':False},
            {'name':'name','type':'varchar','nullable':True},
            {'name':'amount','type':'decimal(38,6)','nullable':True},
            {'name':'day','type':'date','nullable':True},
            {'name':'ok','type':'boolean','nullable':True}]


def metadata():
    location = 's3://shirokuma-lakehouse/l1/smoke/fixture_v1'
    return {'metadata-location':location + '/metadata/00001-abc.metadata.json',
            'config':{'token':'never-persist-credential'}, 'storage-credentials':[{'secret':'never-persist-credential'}],
            'metadata':{'table-uuid':UUID, 'format-version':2, 'location':location,
                'current-schema-id':0, 'schemas':[{'type':'struct','schema-id':0,'fields':[
                    {'id':i,'name':s['name'],'type':kind,'required':not s['nullable']}
                    for i,(s,kind) in enumerate(zip(schema(), ['long','string','decimal(38,6)','date','boolean']),1)]}],
                'default-spec-id':0, 'partition-specs':[{'spec-id':0,'fields':[]}],
                'current-snapshot-id':SNAPSHOT, 'snapshots':[{'snapshot-id':SNAPSHOT}],
                'refs':{'main':{'type':'branch','snapshot-id':SNAPSHOT}}}}


def request(columns=None, limit=2):
    return C.request('select', table='smoke.fixture_v1', columns=columns or ['id','name'], limit=limit)


def inventory():
    return C.project_inventory({'columns':['table_name'],'column_types':['varchar'],
                                'rows':[['fixture_v1'],['other']]}, 'smoke')


def identity():
    return C.project_identity(metadata(), request(), schema())


def result(req=None, rows=None):
    req = req or request()
    selected = C.selected_columns(req, schema())
    return {'columns':[s['name'] for s in selected], 'column_types':[s['type'] for s in selected],
            'rows':rows if rows is not None else [[1,'Shirokuma'],[2,'Polaris']]}


class RequestTests(unittest.TestCase):
    def test_three_exact_request_shapes_and_case_preserved(self):
        self.assertEqual(C.parse_request(b'{"command":"tables","schema":"smoke"}'), C.request('tables', schema='smoke'))
        self.assertEqual(C.request('describe',table='local_experiment.Mixed_Name')['table'], 'local_experiment.Mixed_Name')
        self.assertEqual(C.normalize_request(request()), request())

    def test_unknown_missing_keys_commands_and_raw_SQL_are_rejected(self):
        invalid = [dict(request(), sql='SELECT secret'), {'command':'select','table':'smoke.fixture_v1'},
                   {'command':'tables','schema':'smoke','table':'smoke.fixture_v1'},
                   {'command':'drop','table':'smoke.fixture_v1'}, {'command':'describe','table':'iceberg.smoke.fixture_v1'}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(C.ContractError):
                C.normalize_request(value)

    def test_sql_syntax_endpoint_and_quoted_expression_are_not_identifiers(self):
        for table in ('smoke.x;DROP', 'smoke."x"', 'smoke.x--', 'system.runtime', 'smoke.x/y',
                      'smoke.x\n', 'smoke.日本語', 'smoke.' + 'x'*64):
            with self.subTest(table=table), self.assertRaises(C.ContractError):
                C.request('describe', table=table)
        for column in ('*','count(*)','id + 1','x.y','id"','1id','http://endpoint'):
            with self.subTest(column=column), self.assertRaises(C.ContractError):
                request([column])

    def test_limit_and_column_bounds_are_strict(self):
        for limit in (True, 0, 101, 1.0, '1', None):
            with self.subTest(limit=limit), self.assertRaises(C.ContractError):
                C.normalize_request({**request(), 'limit':limit})
        for columns in ([], ['id','id'], ['c'+str(i) for i in range(17)], 'id,name', [True]):
            with self.subTest(columns=columns), self.assertRaises(C.ContractError):
                C.normalize_request({**request(), 'columns':columns})

    def test_json_duplicates_invalid_unicode_nonfinite_and_size(self):
        for raw in (b'{"command":"tables","command":"select","schema":"smoke"}', b'\xff',
                    b'{"command":"select","limit":NaN}', b' '*4097, b'', b'[]'):
            with self.subTest(raw=raw[:60]), self.assertRaises(C.ContractError):
                C.parse_request(raw)

    def test_fixed_discovery_and_schema_SQL_have_bounded_sentinels(self):
        sql = C.discovery_sql(request())
        self.assertIn("table_type = 'BASE TABLE'", sql)
        self.assertTrue(sql.endswith('LIMIT 101'))
        self.assertIn("table_schema = 'smoke'", sql)
        self.assertTrue(C.schema_sql(request()).endswith('LIMIT 33'))
        self.assertIn("table_name = 'fixture_v1'", C.schema_sql(request()))


class DiscoveryTests(unittest.TestCase):
    def test_inventory_truncation_never_becomes_definite_absence_or_select_authority(self):
        raw = {'columns':['table_name'],'column_types':['varchar'],'rows':[['t%03d'%i] for i in range(101)]}
        observed = C.project_inventory(raw, 'smoke')
        self.assertFalse(observed['complete'])
        self.assertEqual(len(observed['tables']), 100)
        with self.assertRaisesRegex(C.ContractError, 'INVENTORY_INCOMPLETE'):
            C.select_sql(request(), observed, schema(), identity())

    def test_valid_syntax_absent_table_and_column_are_discovery_failures(self):
        req = C.request('select',table='smoke.absent',columns=['id'],limit=1)
        self.assertIn("table_schema = 'smoke'", C.discovery_sql(req))
        with self.assertRaisesRegex(C.ContractError,'TABLE_NOT_DISCOVERED'):
            C.select_sql(req, inventory(), schema(), identity())
        req = request(['absent'])
        with self.assertRaisesRegex(C.ContractError,'COLUMN_NOT_DISCOVERED'):
            C.select_sql(req, inventory(), schema(), identity())

    def test_inventory_order_duplicates_wrong_scope_and_nested_values_rejected(self):
        for rows in ([['b'],['a']], [['a'],['a']], [[{'name':'a'}]], [['a']]*102):
            with self.subTest(rows=rows[:2]), self.assertRaises(C.ContractError):
                C.project_inventory({'columns':['table_name'],'column_types':['varchar'],'rows':rows},'smoke')
        bad = inventory()
        bad['schema'] = 'local_experiment'
        with self.assertRaises(C.ContractError):
            C.select_sql(request(), bad, schema(), identity())

    def test_schema_normalizes_only_known_decimal_space_not_aliases(self):
        raw = {'columns':['column_name','data_type','ordinal_position','is_nullable'],
               'column_types':['varchar','varchar','bigint','varchar'],
               'rows':[['amount','decimal(38, 6)',1,'YES'], ['day','date',2,'NO']]}
        self.assertEqual(C.project_schema(raw), [{'name':'amount','type':'decimal(38,6)','nullable':True},
                                               {'name':'day','type':'date','nullable':False}])
        for kind in ('decimal(38,  6)','decimal(39,0)','decimal(2,3)','DECIMAL(3,2)','int','array(bigint)',
                     'row(a integer)','map(varchar,varchar)','timestamp(6)','varbinary'):
            with self.subTest(kind=kind), self.assertRaises(C.ContractError):
                C.scalar_type(kind)

    def test_schema_rejects_ordinal_bool_missing_columns_and_excess(self):
        raw = {'columns':['column_name','data_type','ordinal_position','is_nullable'],
               'column_types':['varchar','varchar','bigint','varchar'],'rows':[['id','integer',1,'YES']]}
        for mutation in (lambda v:v['rows'][0].__setitem__(2,True), lambda v:v['rows'][0].__setitem__(3,'UNKNOWN'),
                         lambda v:v.update(rows=[]), lambda v:v.update(rows=[['x'+str(i),'integer',i+1,'YES'] for i in range(33)])):
            bad = deepcopy(raw)
            mutation(bad)
            with self.assertRaises(C.ContractError):
                C.project_schema(bad)


class IdentityTests(unittest.TestCase):
    def test_uuid_schema_and_exact_snapshot_are_safe_public_and_credentials_discarded(self):
        observed = identity()
        self.assertEqual(observed['current_snapshot_id'], str(SNAPSHOT))
        self.assertEqual(observed['table_uuid'], UUID)
        self.assertEqual(set(observed), C.IDENTITY_KEYS)
        text = json.dumps(observed)
        self.assertNotIn('never-persist-credential', text)
        self.assertNotIn('s3://', text)
        self.assertNotIn('config', observed)
        self.assertEqual(C.validate_identity(observed, request(), schema()), observed)

    def test_metadata_scope_UUID_type_location_and_head_rejected(self):
        mutations = [lambda v:v['metadata'].update({'table-uuid':'notuuid'}),
                     lambda v:v['metadata'].update({'format-version':True}),
                     lambda v:v['metadata'].update({'current-snapshot-id':float(SNAPSHOT)}),
                     lambda v:v['metadata'].update({'current-snapshot-id':True}),
                     lambda v:v['metadata'].update({'current-snapshot-id':2**63}),
                     lambda v:v['metadata'].update({'location':'s3://other-bucket/t'}),
                     lambda v:v.update({'metadata-location':'s3://shirokuma-lakehouse/../metadata/a.metadata.json'}),
                     lambda v:v['metadata']['refs']['main'].update({'snapshot-id':99})]
        for mutate in mutations:
            bad = metadata()
            mutate(bad)
            with self.subTest(mutation=mutate), self.assertRaises(C.ContractError):
                C.project_identity(bad, request(), schema())

    def test_active_iceberg_and_trino_schema_must_match_and_complex_types_fail(self):
        for mutation in (lambda f:f.update(type={'type':'list','element':'int'}),
                         lambda f:f.update(type='int'), lambda f:f.update(required=True),
                         lambda f:f.update(name='different')):
            bad = metadata()
            mutation(bad['metadata']['schemas'][0]['fields'][1])
            with self.assertRaises(C.ContractError):
                C.project_identity(bad, request(), schema())

    def test_metadata_size_and_duplicate_snapshot_are_bounded(self):
        bad = metadata()
        bad['config']['token'] = 'x'*(C.MAX_METADATA_BYTES+1)
        with self.assertRaisesRegex(C.ContractError, 'METADATA_LIMIT'):
            C.project_identity(bad, request(), schema())
        bad = metadata()
        bad['metadata']['snapshots'] *= 2
        with self.assertRaisesRegex(C.ContractError, 'SNAPSHOT_INVALID'):
            C.project_identity(bad, request(), schema())

    def test_empty_table_is_describable_but_no_snapshot_SELECT_is_synthesized(self):
        raw = metadata()
        raw['metadata'].update({'current-snapshot-id':-1,'snapshots':[],'refs':{}})
        empty = C.project_identity(raw, C.request('describe',table='smoke.fixture_v1'), schema())
        self.assertIsNone(empty['current_snapshot_id'])
        with self.assertRaisesRegex(C.ContractError,'NO_CURRENT_SNAPSHOT'):
            C.select_sql(request(), inventory(), schema(), empty)

    def test_metadata_or_schema_or_UUID_drift_is_unknown_not_success(self):
        first = identity()
        self.assertTrue(C.verify_identity(first, deepcopy(first)))
        for key in ('table_uuid','schema_sha256','spec_sha256','refs_sha256','current_snapshot_id',
                    'metadata_sha256','metadata_location_sha256','table_location_sha256'):
            second = deepcopy(first)
            second[key] = 'changed'
            with self.subTest(key=key), self.assertRaises(C.ContractError):
                C.verify_identity(first, second)

    def test_equal_but_malformed_identity_is_not_verified(self):
        bad = identity()
        bad['current_snapshot_id'] = SNAPSHOT
        with self.assertRaises(C.ContractError):
            C.verify_identity(bad, deepcopy(bad))

    def test_SELECT_quotes_each_identifier_and_pins_exact_int64_snapshot(self):
        sql = C.select_sql(request(['name','id'],limit=100), inventory(), schema(), identity())
        self.assertEqual(sql, 'SELECT "name", "id" FROM "iceberg"."smoke"."fixture_v1" '
                         f'FOR VERSION AS OF {SNAPSHOT} LIMIT 101')
        bad = identity()
        bad['table'] = 'other'
        with self.assertRaisesRegex(C.ContractError, 'IDENTITY_INVALID'):
            C.select_sql(request(), inventory(), schema(), bad)


class ResultTests(unittest.TestCase):
    def project(self, rows=None, req=None):
        req = req or request()
        return C.project_rows(result(req,rows), req, schema(), identity())

    def test_typed_public_rows_are_explicit_output_receipt_contains_only_hash_and_counts(self):
        public = self.project()
        self.assertEqual(public['rows'][0], [{'type':'bigint','value':'1'},{'type':'varchar','value':'Shirokuma'}])
        self.assertFalse(public['truncated'])
        saved = C.receipt_projection(public)
        self.assertNotIn('rows', saved)
        self.assertNotIn('Shirokuma', json.dumps(saved))
        self.assertEqual(saved['rows_sha256'], C.digest(public['rows']))
        self.assertEqual(saved['snapshot_id'], str(SNAPSHOT))

    def test_limit_plus_one_marks_sample_truncation_and_hashes_only_returned_rows(self):
        public = self.project([[1,'one'],[2,'two'],[3,'sentinel']])
        self.assertTrue(public['truncated'])
        self.assertEqual((public['row_count'],public['fetched_row_count']), (2,3))
        self.assertEqual(len(public['rows']),2)
        self.assertNotIn('sentinel', json.dumps(public))
        C.receipt_projection(public)
        with self.assertRaises(C.ContractError):
            self.project([[i,'x'] for i in range(4)])

    def test_integer_decimal_date_boolean_null_exact_cells(self):
        req = request(['id','amount','day','ok','name'])
        decimal = '12345678901234567890123456789012.123456'
        public = self.project([[2**63-1,decimal,'2024-02-29',True,None],[-2**63,None,None,None,'値']],req)
        self.assertEqual(public['rows'][0][0]['value'], str(2**63-1))
        self.assertEqual(public['rows'][0][1]['value'], decimal)
        self.assertEqual(public['rows'][0][3]['value'], True)
        self.assertIsNone(public['rows'][1][1]['value'])
        C.receipt_projection(public)

    def test_transport_decimal_JSON_numbers_and_strings_preserve_all_38_digits(self):
        req = request(['amount'])
        decimal = '12345678901234567890123456789012.123456'
        raw = json.loads('{"columns":["amount"],"column_types":["decimal(38, 6)"],'
                         '"rows":[[' + decimal + '],["' + decimal + '"]]}', parse_float=Decimal)
        public = C.project_rows(raw,req,schema(),identity())
        self.assertEqual([r[0]['value'] for r in public['rows']], [decimal,decimal])
        self.assertIsInstance(raw['rows'][0][0], Decimal)
        C.receipt_projection(public)

    def test_decimal_exponent_bound_is_checked_without_expansion_or_rounding(self):
        req = request(['amount'])
        for value in (Decimal('1e1000000000'), Decimal('1e-1000000000'), Decimal('1.0000001'),
                      Decimal('NaN'), Decimal('Infinity')):
            with self.subTest(value=str(value)), self.assertRaises(C.ContractError):
                self.project([[value]],req)
        public = self.project([[Decimal('1.23000000')],[Decimal('-0e-1000000000')]],req)
        self.assertEqual([r[0]['value'] for r in public['rows']], ['1.230000','0.000000'])

    def test_real_and_double_JSON_decimal_decoder_reaches_typed_public_projection(self):
        for kind, iceberg_kind in (('real','float'),('double','double')):
            with self.subTest(kind=kind):
                columns = [{'name':'n','type':kind,'nullable':True}]
                req = request(['n'])
                source = metadata()
                source['metadata']['schemas'][0]['fields'] = [
                    {'id':1,'name':'n','type':iceberg_kind,'required':False}]
                observed = C.project_identity(source,req,columns)
                raw = json.loads('{"columns":["n"],"column_types":["' + kind + '"],"rows":[[1.25]]}',
                                 parse_float=Decimal)
                public = C.project_rows(raw,req,columns,observed)
                self.assertEqual(public['rows'], [[{'type':kind,'value':'1.25'}]])
                C.receipt_projection(public)

    def test_invalid_scalar_cells_never_become_coerced_success(self):
        cases = [('id', True), ('id', 1.0), ('id', 2**63), ('id', None), ('amount', 1.23),
                 ('amount','NaN'), ('amount','1.1234567'), ('amount','9'*33 + '.000000'),
                 ('amount','1e2'), ('day','2023-02-29'), ('ok',1), ('name',{'secret':'x'}),
                 ('name','\ud800'), ('name','x'*(C.MAX_CELL_BYTES+1))]
        for column, cell in cases:
            with self.subTest(column=column,cell=str(cell)[:40]), self.assertRaises(C.ContractError):
                self.project([[cell]],request([column]))

    def test_result_columns_precision_and_row_shape_must_match_requested_schema(self):
        req = request(['amount'])
        raw = result(req,[['1.2']])
        raw['column_types'] = ['decimal(38, 6)']
        self.assertEqual(C.project_rows(raw,req,schema(),identity())['rows'][0][0]['value'],'1.200000')
        for mutation in (lambda r:r.update(column_types=['decimal(37,6)']),
                         lambda r:r.update(columns=['other']),lambda r:r.update(rows=[[]])):
            bad = deepcopy(raw)
            mutation(bad)
            with self.assertRaises(C.ContractError):
                C.project_rows(bad,req,schema(),identity())

    def test_oversized_public_result_has_no_partial_success(self):
        req = request(['name'],limit=100)
        with self.assertRaisesRegex(C.ContractError, 'PUBLIC_RESULT_LIMIT'):
            self.project([['x'*4096] for _ in range(100)],req)

    def test_tampered_public_values_counts_types_hash_or_extra_fields_are_rejected(self):
        for mutation in (lambda p:p.update(sql='raw'),lambda p:p.update(row_count=True),
                         lambda p:p.update(truncated=True), lambda p:p.update(snapshot_id=SNAPSHOT),
                         lambda p:p.update(rows_sha256='0'*64),
                         lambda p:p['rows'][0][0].update(value=True)):
            bad = self.project()
            mutation(bad)
            with self.assertRaises(C.ContractError):
                C.receipt_projection(bad)

    def test_shortened_returned_rows_cannot_claim_limit_plus_one_truncation(self):
        bad = self.project([[1,'one']])
        bad.update(fetched_row_count=3,truncated=True)
        with self.assertRaisesRegex(C.ContractError,'PUBLIC_RESULT_INVALID'):
            C.receipt_projection(bad)

    def test_giant_public_integer_string_is_rejected_as_safe_contract_error(self):
        bad = self.project([[1,'one']])
        bad['rows'][0][0]['value'] = '1'*5000
        bad['rows_sha256'] = C.digest(bad['rows'])
        with self.assertRaises(C.ContractError):
            C.receipt_projection(bad)

    def test_float_values_are_finite_typed_strings_and_integer_is_not_boolean(self):
        for kind,value,expected in [('real',1.25,'1.25'),('double',1e100,'1e+100'),
                                    ('real',Decimal('1.25'),'1.25'),('double',Decimal('1e100'),'1e+100')]:
            spec = {'name':'number','type':kind,'nullable':True}
            self.assertEqual(C._cell(value,spec),{'type':kind,'value':expected})
            for invalid in (True,float('nan'),float('inf'),'1.0'):
                with self.assertRaises(C.ContractError):
                    C._cell(invalid,spec)
        with self.assertRaises(C.ContractError):
            C._cell(3.5e38,{'name':'number','type':'real','nullable':False})


if __name__ == '__main__':
    unittest.main()
