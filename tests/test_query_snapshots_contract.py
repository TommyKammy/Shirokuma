"""Historical read contracts and v1 compatibility; no service/model calls."""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.query_snapshots import contract as C
from test_query_read_contract import schema, inventory, metadata as reader_metadata, result as reader_result

OLD = 8088807033386546123
HEAD = 1234567890123456789  # Smaller ID deliberately proves IDs are not a clock.
TOKEN = 'e' * 32


def request(columns=None, limit=2, snapshot_id=None):
    return C.request('select', table='smoke.fixture_v1', columns=columns or ['id', 'name'],
                     limit=limit, snapshot_id=str(OLD) if snapshot_id is None else snapshot_id)


def listing_request():
    return C.request('snapshots', table='smoke.fixture_v1')


def metadata():
    value = reader_metadata()
    value['metadata']['current-snapshot-id'] = HEAD
    value['metadata']['refs']['main']['snapshot-id'] = HEAD
    value['metadata']['snapshots'] = [
        {'snapshot-id':OLD, 'sequence-number':1, 'timestamp-ms':1760000000000,
         'schema-id':0, 'summary':{'operation':'append', 'private-marker':'not-public'}},
        {'snapshot-id':HEAD, 'parent-snapshot-id':OLD, 'sequence-number':2,
         'timestamp-ms':1760000001000, 'schema-id':0, 'summary':{'operation':'append'}}]
    return value


def window(value=None, req=None):
    return C.project_window(value or metadata(), req or request(), schema())


def result(req=None, rows=None):
    return reader_result(C.base_request(req or request()), rows)


class RequestTests(unittest.TestCase):
    def test_only_separate_two_shapes_with_exact_int64_text(self):
        self.assertEqual(C.parse_request(C.canonical(request())), request())
        self.assertEqual(C.base_request(listing_request()), C.R.request('describe', table='smoke.fixture_v1'))
        self.assertNotIn('snapshot_id', C.base_request(request()))
        self.assertEqual(C.normalize_request(request(snapshot_id=str(2**63-1)))['snapshot_id'], str(2**63-1))
        for sid in (None, True, 1, 1.0, '01', '+1', '-1', '0', '1e3', str(2**63), '1\n'):
            value = request(); value['snapshot_id'] = sid
            with self.subTest(sid=sid), self.assertRaises(C.ContractError): C.normalize_request(value)

    def test_no_named_refs_timestamps_sql_or_extra_parameters(self):
        bad = [C.base_request(request()), {'command':'tables','schema':'smoke'},
               dict(request(), reference='main'), dict(request(), timestamp='2026-01-01'),
               dict(listing_request(), limit=10), dict(request(), sql='SELECT secret'),
               dict(request(), snapshot_id='branch_x'), dict(request(), table='smoke.x;DROP TABLE y'),
               dict(request(), columns=['id', 'id']), dict(request(), columns=['count(*)'])]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(C.ContractError): C.normalize_request(value)

    def test_duplicate_json_and_input_budget(self):
        for value in (b'{"command":"snapshots","table":"smoke.x","table":"smoke.y"}',
                      b' '*4097, b'\xff', b'[]', b'{"command":NaN}', b''):
            with self.subTest(value=value[:30]), self.assertRaises(C.ContractError): C.parse_request(value)

    def test_limit_and_selected_columns_are_bounded(self):
        for edits in ({'limit':True}, {'limit':101}, {'limit':0}, {'columns':['x'+str(i) for i in range(17)]}):
            with self.subTest(edits=edits), self.assertRaises(C.ContractError): C.normalize_request(dict(request(), **edits))


class MetadataTests(unittest.TestCase):
    def test_two_snapshots_truthful_head_and_old_selection_without_secrets(self):
        value = window()
        self.assertEqual(value['identity']['current_snapshot_id'], str(HEAD))
        self.assertEqual(C.select_snapshot(value, request())['snapshot_id'], str(OLD))
        self.assertFalse(value['snapshots'][0]['is_current'])
        self.assertTrue(value['snapshots'][1]['parent_retained'])
        self.assertTrue(value['snapshots'][1]['is_current'])
        public = C.project_snapshots(value, listing_request())
        self.assertEqual([s['snapshot_id'] for s in public['snapshots']], [str(OLD), str(HEAD)])
        text = C.canonical(public).decode()
        for secret in ('never-persist-credential', 'not-public', 's3://', 'metadata.json'):
            self.assertNotIn(secret, text)

    def test_snapshot_list_order_is_canonical_not_wire_order_or_numeric_ID(self):
        raw = metadata(); raw['metadata']['snapshots'].reverse()
        self.assertEqual(window(raw)['snapshots'], window()['snapshots'])
        self.assertGreater(OLD, HEAD)

    def test_exact_metadata_numbers_no_float_or_bool_coercion(self):
        for key, value in (('snapshot-id',True), ('snapshot-id',float(OLD)), ('parent-snapshot-id',True),
                           ('sequence-number',True), ('sequence-number',str(1)), ('sequence-number',2**63),
                           ('timestamp-ms',True), ('timestamp-ms',1760000000000.0), ('timestamp-ms',C.MAX_TIMESTAMP_MS+1),
                           ('schema-id',True), ('schema-id','0')):
            raw = metadata(); raw['metadata']['snapshots'][0][key] = value
            with self.subTest(key=key,value=value), self.assertRaises(C.ContractError): window(raw)

    def test_nonretained_parent_is_not_fabricated_or_rejected(self):
        raw = metadata(); raw['metadata']['snapshots'][0]['parent-snapshot-id'] = 42
        record = window(raw)['snapshots'][0]
        self.assertEqual(record['parent_snapshot_id'], '42')
        self.assertFalse(record['parent_retained'])
        self.assertEqual(C.select_snapshot(window(raw), request()), record)

    def test_cycles_duplicate_snapshots_and_wrong_current_ref_rejected(self):
        mutations = [lambda m:m['snapshots'][0].update({'parent-snapshot-id':HEAD}),
                     lambda m:m['snapshots'][0].update({'parent-snapshot-id':OLD}),
                     lambda m:m['snapshots'].append(deepcopy(m['snapshots'][0])),
                     lambda m:m['refs']['main'].update({'snapshot-id':OLD}),
                     lambda m:m.update({'current-snapshot-id':999})]
        for mutate in mutations:
            raw = metadata(); mutate(raw['metadata'])
            with self.subTest(mutate=mutate), self.assertRaises(C.ContractError): window(raw)

    def test_schema_id_missing_and_not_retained_are_listed_but_not_selectable(self):
        for sid, reason in ((None,'schema-missing'), (17,'schema-not-retained')):
            raw = metadata(); raw['metadata']['snapshots'][0]['schema-id'] = sid
            before = window(raw)
            self.assertEqual(C.project_snapshots(before, listing_request())['snapshots'][0]['schema_compatibility'], reason)
            with self.assertRaisesRegex(C.ContractError, '^SNAPSHOT_SCHEMA_UNSUPPORTED$'):
                C.select_snapshot(before, request())

    def test_same_field_schema_with_new_id_is_compatible(self):
        raw = metadata(); old_schema = deepcopy(raw['metadata']['schemas'][0]); old_schema['schema-id'] = 7
        raw['metadata']['schemas'].append(old_schema); raw['metadata']['snapshots'][0]['schema-id'] = 7
        self.assertEqual(C.select_snapshot(window(raw), request())['schema_id'], 7)

    def test_field_id_name_type_required_and_order_must_match(self):
        edits = [lambda f:f[0].update(id=99), lambda f:f[0].update(name='old_id'),
                 lambda f:f[0].update(type='int'), lambda f:f[0].update(required=False),
                 lambda f:f.reverse()]
        for edit in edits:
            raw = metadata(); old_schema = deepcopy(raw['metadata']['schemas'][0]); old_schema['schema-id'] = 7
            edit(old_schema['fields']); raw['metadata']['schemas'].append(old_schema)
            raw['metadata']['snapshots'][0]['schema-id'] = 7
            before = window(raw)
            self.assertEqual(before['snapshots'][0]['schema_compatibility'], 'schema-mismatch')
            with self.subTest(edit=edit), self.assertRaisesRegex(C.ContractError, '^SNAPSHOT_SCHEMA_UNSUPPORTED$'):
                C.select_sql(request(), inventory(), schema(), before)

    def test_complex_historical_schema_is_listable_not_readable(self):
        raw = metadata(); old_schema = deepcopy(raw['metadata']['schemas'][0]); old_schema['schema-id'] = 7
        old_schema['fields'][0]['type'] = {'type':'list','element-id':99,'element':'long','element-required':False}
        raw['metadata']['schemas'].append(old_schema); raw['metadata']['snapshots'][0]['schema-id'] = 7
        before = window(raw)
        self.assertEqual(before['snapshots'][0]['schema_compatibility'], 'schema-unsupported')
        with self.assertRaisesRegex(C.ContractError, '^SNAPSHOT_SCHEMA_UNSUPPORTED$'): C.select_snapshot(before, request())

    def test_duplicate_schema_ids_field_ids_and_bad_required_rejected(self):
        for edit in (lambda m:m['schemas'].append(deepcopy(m['schemas'][0])),
                     lambda m:m['schemas'][0]['fields'][1].update(id=1),
                     lambda m:m['schemas'][0]['fields'][0].update(required=1)):
            raw = metadata(); edit(raw['metadata'])
            with self.subTest(edit=edit), self.assertRaises(C.ContractError): window(raw)

    def test_unknown_operation_and_snapshot_count_budget_rejected(self):
        raw = metadata(); raw['metadata']['snapshots'][0]['summary']['operation'] = 'private-message'
        with self.assertRaises(C.ContractError): window(raw)
        raw = metadata()
        raw['metadata']['snapshots'] = [dict(raw['metadata']['snapshots'][0], **{'snapshot-id':i+1}) for i in range(65)]
        raw['metadata']['current-snapshot-id'] = 1; raw['metadata']['refs']['main']['snapshot-id'] = 1
        with self.assertRaisesRegex(C.ContractError, '^SNAPSHOT_LIMIT$'): window(raw)

    def test_metadata_byte_budget_before_projection(self):
        raw = metadata(); raw['metadata']['properties'] = {'private': 'x'*C.MAX_METADATA_BYTES}
        with self.assertRaisesRegex(C.ContractError, '^METADATA_LIMIT$'): window(raw)

    def test_empty_table_is_complete_empty_listing_not_selectable(self):
        raw = metadata(); raw['metadata'].update({'current-snapshot-id':-1,'snapshots':[],'refs':{}})
        before = window(raw)
        self.assertEqual(C.project_snapshots(before, listing_request())['snapshots'], [])
        self.assertIsNone(before['identity']['current_snapshot_id'])
        with self.assertRaisesRegex(C.ContractError, '^SNAPSHOT_NOT_RETAINED$'): C.select_snapshot(before, request())

    def test_two_windows_reject_all_metadata_and_location_drift(self):
        before = window()
        self.assertTrue(C.verify_window(before, deepcopy(before)))
        for edit in (lambda v:v['metadata'].update(properties={'changed':'true'}),
                     lambda v:v.update({'metadata-location':v['metadata']['location']+'/metadata/00002-def.metadata.json'}),
                     lambda v:v['metadata']['snapshots'][0].update({'timestamp-ms':1760000000001}),
                     lambda v:v['metadata']['snapshots'].pop(0)):
            raw = metadata(); edit(raw)
            with self.subTest(edit=edit), self.assertRaisesRegex(C.ContractError, '^SNAPSHOT_WINDOW_CHANGED$'):
                C.verify_window(before, window(raw))

    def test_projected_record_tampering_cannot_make_self_inconsistent_success(self):
        edits = [lambda w:w['snapshots'][0].update(is_current=True),
                 lambda w:w['snapshots'][1].update(parent_retained=False),
                 lambda w:w['snapshots'][0].update(sequence_number=1),
                 lambda w:w['snapshots'][0].update(schema_id=None),
                 lambda w:w['snapshots'].reverse()]
        for edit in edits:
            before = window(); edit(before)
            with self.subTest(edit=edit), self.assertRaises(C.ContractError): C.validate_window(before, request())


class SelectionTests(unittest.TestCase):
    def test_numeric_sql_binds_fresh_table_columns_snapshot_and_limit_plus_one(self):
        sql = C.select_sql(request(), inventory(), schema(), window())
        self.assertEqual(sql, f'SELECT "id", "name" FROM "iceberg"."smoke"."fixture_v1" FOR VERSION AS OF BIGINT \'{OLD}\' LIMIT 3')
        self.assertTrue(C.discovery_sql(request()).endswith('LIMIT 101'))
        self.assertTrue(C.schema_sql(request()).endswith('LIMIT 33'))
        with self.assertRaisesRegex(C.ContractError,'^SNAPSHOT_NOT_RETAINED$'):
            C.select_sql(request(snapshot_id='55'), inventory(), schema(), window())
        with self.assertRaisesRegex(C.ContractError,'^COLUMN_NOT_DISCOVERED$'):
            C.select_sql(request(['absent']), inventory(), schema(), window())
        bad = inventory(); bad['complete'] = False
        with self.assertRaisesRegex(C.ContractError,'^INVENTORY_INCOMPLETE$'):
            C.select_sql(request(), bad, schema(), window())

    def test_all_int64_sizes_compile_as_bigint_version_literals(self):
        for sid in (1, 2147483647, 2147483648, 9223372036854775807):
            value = metadata()
            value['metadata']['snapshots'][0]['snapshot-id'] = sid
            value['metadata']['snapshots'][1]['parent-snapshot-id'] = sid
            req = request(snapshot_id=str(sid))
            with self.subTest(snapshot_id=sid):
                sql = C.select_sql(req, inventory(), schema(), window(value, req))
                self.assertEqual(sql, f'SELECT "id", "name" FROM "iceberg"."smoke"."fixture_v1" FOR VERSION AS OF BIGINT \'{sid}\' LIMIT 3')

    def test_historical_rows_current_identity_and_receipt_are_separate(self):
        before = window(); original = deepcopy(before)
        public = C.project_rows(result(), request(), schema(), before)
        self.assertEqual(before, original)
        self.assertEqual(public['snapshot_id'], str(OLD))
        self.assertEqual(public['current_snapshot_id'], str(HEAD))
        self.assertEqual(public['selected_snapshot'], before['snapshots'][0])
        self.assertEqual(public['schema_version'], 2)
        saved = C.receipt_projection(public)
        self.assertNotIn('rows', saved); self.assertNotIn('Shirokuma', C.canonical(saved).decode())
        self.assertEqual(saved['rows_sha256'], C.digest(public['rows']))

    def test_head_can_be_selected_explicitly_without_changing_default_reader(self):
        req = request(snapshot_id=str(HEAD))
        public = C.project_rows(result(req), req, schema(), window())
        self.assertTrue(public['selected_snapshot']['is_current'])
        legacy = C.R.project_rows(result(req), C.base_request(req), schema(), window()['identity'])
        self.assertEqual(legacy['schema_version'], 1)
        self.assertEqual(legacy['snapshot_id'], str(HEAD))
        with self.assertRaises(C.ContractError): C.R.normalize_request(req)

    def test_decimal_int64_date_null_and_bool_exactness(self):
        req = request([s['name'] for s in schema()])
        rows = [[-(2**63), '日本語', Decimal('12345678901234567890123456789012.123456'), '2024-02-29', True],
                [2**63-1, None, None, None, None]]
        public = C.project_rows(result(req, rows), req, schema(), window())
        self.assertEqual(public['rows'][0][0]['value'], str(-(2**63)))
        self.assertEqual(public['rows'][0][2]['value'], '12345678901234567890123456789012.123456')
        self.assertIs(public['rows'][0][4]['value'], True)
        self.assertIsNone(public['rows'][1][2]['value'])
        self.assertEqual(C.validate_public(public, req), public)

    def test_bad_scalar_values_and_invalid_hidden_sentinel_are_rejected(self):
        for rows in ([[True,'x']], [[2**63,'x']], [[1,'x'],[2,'y'],[3,'x'*4097]]):
            with self.subTest(rows=str(rows)[:40]), self.assertRaises(C.ContractError):
                C.project_rows(result(rows=rows), request(), schema(), window())
        req = request(['amount'])
        with self.assertRaisesRegex(C.ContractError,'^CELL_DECIMAL_INVALID$'):
            C.project_rows(result(req, [[0.1]]), req, schema(), window())

    def test_truncation_sentinel_validated_but_omitted(self):
        public = C.project_rows(result(rows=[[1,'a'],[2,'b'],[3,'c']]), request(), schema(), window())
        self.assertTrue(public['truncated']); self.assertEqual(public['fetched_row_count'], 3)
        self.assertEqual(public['row_count'], 2)
        self.assertNotIn('c', [row[1]['value'] for row in public['rows']])

    def test_public_shape_binding_tamper_and_budget_rejected(self):
        original = C.project_rows(result(), request(), schema(), window())
        edits = [lambda p:p.update(snapshot_id=str(HEAD)), lambda p:p.update(current_snapshot_id=str(OLD)),
                 lambda p:p.update(schema_version=1), lambda p:p.update(rows_sha256='0'*64),
                 lambda p:p['selected_snapshot'].update(schema_compatibility='schema-mismatch'),
                 lambda p:p.update(extra='private'), lambda p:p.update(requested_limit=True)]
        for edit in edits:
            public = deepcopy(original); edit(public)
            with self.subTest(edit=edit), self.assertRaises(C.ContractError): C.receipt_projection(public)
        with self.assertRaises(C.ContractError): C.validate_public(original, request(snapshot_id=str(HEAD)))
        with patch.object(C, 'MAX_PUBLIC_BYTES', len(C.canonical(original))-1):
            with self.assertRaisesRegex(C.ContractError, '^PUBLIC_RESULT_LIMIT$'): C.validate_public(original)

    def test_two_snapshot_fixture_distinguishes_old_and_current_rows(self):
        old = C.project_rows(result(rows=[[1,'A']]), request(), schema(), window())
        req = request(snapshot_id=str(HEAD))
        head = C.project_rows(result(req, [[1,'A'],[2,'B']]), req, schema(), window())
        self.assertEqual(old['row_count'], 1); self.assertEqual(head['row_count'], 2)
        self.assertNotEqual(old['rows_sha256'], head['rows_sha256'])
        self.assertEqual(old['current_snapshot_id'], head['current_snapshot_id'])

    def test_diagnose_legacy_source_remains_valid_and_schema2_refuses_before_runtime(self):
        from tools.local_experiment.query_diagnose import run as D
        from test_query_diagnose_run import receipt
        value = receipt(); token = value['evidence_token']
        self.assertEqual(D.validate_source(token, value, 'f'*64)['read_token'], token)
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve(); base.chmod(0o700)
            folder = base/token; folder.mkdir(mode=0o700)
            (folder/'run.lock').touch(mode=0o600)
            value['schema_version'] = 2
            raw = C.canonical(value); path = folder/'summary.json'; path.write_bytes(raw); path.chmod(0o600)
            before = D.fingerprint(path.stat())
            with patch.object(D, 'READ_STATE', base), patch.object(D, 'environment', side_effect=AssertionError('model prohibited')) as env:
                with self.assertRaisesRegex(D.DiagnoseError, '^SOURCE_INVALID$'):
                    with D.SourceLease(token): pass
                env.assert_not_called()
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(D.fingerprint(path.stat()), before)


if __name__ == '__main__':
    unittest.main()
