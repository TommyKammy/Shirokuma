"""Typed equality, real bounded HTTP transport and owner tests; no live calls."""
from copy import deepcopy
from decimal import Decimal
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.query_filter import contract as C, run as RUN
from tools.local_experiment.query_read import contract as R, client as Q
from tools.local_experiment.query_snapshots import contract as S, observe as O, run as SHARED
import test_query_read_contract as F
import test_query_snapshots_observe as OF

TOKEN = OF.TOKEN
OLD = str(OF.OLD)


def request(*, column='id', literal='1', columns=None, snapshot_id=None, limit=2):
    return C.normalize_request({'command':'select', 'table':'smoke.fixture_v1',
        'columns':columns or ['id','name'], 'limit':limit, 'snapshot_id':snapshot_id,
        'where':{'column':column, 'literal':literal}})


def observed(value=None, rows=None):
    """Owner fixture: real pure projections with a complete rowless journal."""
    value = value or request()
    window = C.project_window(OF.metadata(), value, F.schema())
    raw = F.result(C.base_request(value), rows if rows is not None else [[1,'private-row-sentinel']])
    public = C.project_rows(raw, value, F.schema(), window)
    journal = {'state':'verified', 'request_sha256':R.digest(value), 'inventory':F.inventory(),
        'identity_before':window['identity'], 'identity_after':deepcopy(window['identity']),
        'snapshot_catalog_before':window['snapshots'], 'snapshot_catalog_after':deepcopy(window['snapshots']),
        'selection':C.receipt_projection(public), 'queries':{'worker':[], 'control':[]},
        'query_drain':{'verified':True}, 'runtime':{'state':'verified','cleanup':{'verified':True}},
        'cleanup_verified':True, 'error_code':None}
    return journal, public


def success(value, token, progress):
    if token != TOKEN:
        raise AssertionError('unexpected token')
    journal, public = observed(value)
    progress(journal)
    return journal, public


class ContractTests(unittest.TestCase):
    def test_exact_request_and_duplicate_or_injection_refused_before_runtime(self):
        self.assertEqual(C.parse_request(R.canonical(request())), request())
        bad = [dict(request(), sql='SELECT secret'), {k:v for k,v in request().items() if k!='snapshot_id'},
               dict(request(), snapshot_id=1), dict(request(), where={'column':'id','literal':'1','type':'bigint'})]
        for text in ('', 'NULL', '1.0e0', '+1', '01', '1 ', ' 1', '１', "1';DROP", '1\n', '1'*129,
                     '2026-02-29', '0000-01-01'):
            bad.append(dict(request(), where={'column':'id','literal':text}))
        for value in bad:
            with self.subTest(value=value), self.assertRaises(C.ContractError): C.normalize_request(value)
        for raw in (b'{"command":"select","command":"select"}', b'\xff', b' '*4097):
            with self.assertRaises(C.ContractError): C.parse_request(raw)

    def test_exact_scalar_values_without_float_rounding(self):
        cases = [('integer','-2147483648','-2147483648'), ('integer','-0','0'),
                 ('bigint','9223372036854775807','9223372036854775807'),
                 ('bigint','9007199254740993','9007199254740993'),
                 ('decimal(5,2)','1.2300','1.23'), ('decimal(5,2)','-0.00','0.00'),
                 ('decimal(38,6)','12345678901234567890123456789012.123456','12345678901234567890123456789012.123456'),
                 ('boolean','false',False), ('boolean','true',True), ('date','2024-02-29','2024-02-29')]
        for kind, text, expected in cases:
            with self.subTest(kind=kind, text=text):
                self.assertEqual(C.resolve_predicate(request(literal=text),
                    [{'name':'id','type':kind,'nullable':True}]), {'column':'id','type':kind,'value':expected})

    def test_type_precision_scale_and_nonnull_boundaries(self):
        for kind, literal in [('integer','2147483648'),('bigint','9223372036854775808'),
                ('bigint','1.0'),('bigint','true'),('boolean','1'),('date','1'),
                ('decimal(5,2)','1.234'),('decimal(5,2)','1000'),('varchar','1'),('double','1')]:
            with self.subTest(kind=kind, literal=literal), self.assertRaises(C.ContractError):
                C.resolve_predicate(request(literal=literal), [{'name':'id','type':kind,'nullable':True}])
        with self.assertRaisesRegex(C.ContractError, 'FILTER_COLUMN_INVALID'):
            C.resolve_predicate(request(column='missing'), F.schema())

    def test_fixed_sql_and_current_head_resolution_preserve_truthful_window(self):
        for snapshot, expected in [(None,str(F.SNAPSHOT)),(OLD,OLD)]:
            req=request(snapshot_id=snapshot)
            window=C.project_window(OF.metadata(),req,F.schema())
            sql=C.select_sql(req,F.inventory(),F.schema(),window)
            self.assertEqual(sql, 'SELECT "id", "name" FROM "iceberg"."smoke"."fixture_v1" '
                + "FOR VERSION AS OF BIGINT '"+expected+"' WHERE \"id\" = BIGINT '1' LIMIT 3")
            self.assertEqual(window['identity']['current_snapshot_id'],str(F.SNAPSHOT))
        self.assertEqual(C.predicate_sql(request(column='amount',literal='1.2300'),F.schema()),
                         '"amount" = CAST(\'1.230000\' AS DECIMAL(38,6))')

    def test_outside_projection_filter_has_full_schema_historical_binding(self):
        req=request(column='ok',literal='true',snapshot_id=OLD)
        window=C.project_window(OF.metadata(),req,F.schema())
        self.assertIn(' WHERE "ok" = TRUE ',C.select_sql(req,F.inventory(),F.schema(),window))
        public=C.project_rows(F.result(rows=[[1,'one']]),req,F.schema(),window)
        self.assertEqual(public['where'],{'column':'ok','type':'boolean','operator':'eq'})
        raw=OF.metadata()
        old=deepcopy(raw['metadata']['schemas'][0]);old['schema-id']=1
        old['fields'][-1]['id']=99
        raw['metadata']['schemas'].append(old);raw['metadata']['snapshots'][0]['schema-id']=1
        incompatible=C.project_window(raw,req,F.schema())
        with self.assertRaisesRegex(C.ContractError,'SNAPSHOT_SCHEMA_UNSUPPORTED'):
            C.select_sql(req,F.inventory(),F.schema(),incompatible)

    def test_projected_column_checks_all_fetched_rows_including_sentinel(self):
        req=request(limit=1);window=C.project_window(OF.metadata(),req,F.schema())
        for rows in ([[2,'wrong']],[[None,'null']],[[1,'returned'],[2,'sentinel']]):
            with self.subTest(rows=rows),self.assertRaises(C.ContractError):
                C.project_rows(F.result(rows=rows),req,F.schema(),window)
        self.assertEqual(C.project_rows(F.result(rows=[]),req,F.schema(),window)['row_count'],0)
        public=C.project_rows(F.result(rows=[[1,'one'],[1,'sentinel']]),req,F.schema(),window)
        self.assertTrue(public['truncated']);self.assertEqual(public['fetched_row_count'],2)
        self.assertEqual(len(public['rows']),1)

    def test_public_metadata_and_rowless_receipt_do_not_include_literal(self):
        req=request(literal='9007199254740993',columns=['name'])
        window=C.project_window(OF.metadata(),req,F.schema())
        public=C.project_rows(F.result(C.base_request(req),rows=[['private-row-sentinel']]),req,F.schema(),window)
        self.assertEqual(set(public),C.RESULT_KEYS);self.assertEqual(len(public),16)
        receipt=C.receipt_projection(public)
        for forbidden in ('9007199254740993','private-row-sentinel','"literal"','SELECT '):
            self.assertNotIn(forbidden,json.dumps(receipt))
        bad=deepcopy(public);bad['where']['literal']='secret'
        with self.assertRaises(C.ContractError):C.validate_public(bad,req)

    def test_public_projected_predicate_cannot_be_spoofed(self):
        _, public=observed()
        for mutation in ('value','column','type','head'):
            bad=deepcopy(public)
            if mutation=='value':bad['rows'][0][0]['value']='2'
            elif mutation=='column':bad['where']['column']='amount'
            elif mutation=='type':bad['where']['type']='integer'
            else:bad['current_snapshot_id']=OLD
            with self.subTest(mutation=mutation),self.assertRaises(C.ContractError):C.validate_public(bad,request())


class ObservationTests(OF.ObservationFixture,unittest.TestCase):
    def setUp(self):
        self.setup();self.request=request();self.rows=[[1,'private-row-sentinel']]

    def clients(self, req, progress):
        original=O.SnapshotClient
        with patch.object(O,'SnapshotClient',side_effect=lambda *a,**k: original(*a,_contract=C,**k)):
            return super().clients(req,progress)

    def invoke(self,*,progress=None,drain_fn=None):
        return O.observe(self.request,TOKEN,progress or self.progress,
            runtime_factory=self.runtime,drain_fn=drain_fn or self.drain,_contract=C)

    def test_real_transport_current_and_history_send_one_compiled_filtered_select(self):
        for snapshot in (None,OLD):
            self.setUp();self.request=request(snapshot_id=snapshot)
            journal,public=self.invoke(drain_fn=Q.drain)
            self.assertEqual(public['snapshot_id'],snapshot or str(F.SNAPSHOT))
            self.assertEqual(public['current_snapshot_id'],str(F.SNAPSHOT))
            self.assertTrue(journal['cleanup_verified'])
            self.assertEqual(len(self.opener.requests),4)
            selects=[r.data.decode() for r in self.opener.requests if ' WHERE "id" = ' in r.data.decode()]
            self.assertEqual(len(selects),1)
            self.assertEqual([r['label'] for r in journal['queries']['worker']],
                             ['inventory','schema_before','select','schema_after'])
            self.assertEqual(len(journal['queries']['control']),1)
            for forbidden in ('private-row-sentinel','SELECT ','"literal"','never-persist-credential'):
                self.assertNotIn(forbidden,json.dumps(self.saved))

    def test_discovery_and_schema_failures_never_send_data_select(self):
        for column,literal,expected in [('missing','1','FILTER_COLUMN_INVALID'),('name','1','FILTER_TYPE_UNSUPPORTED'),
                                        ('id','1.0','FILTER_VALUE_INVALID')]:
            self.setUp();self.request=request(column=column,literal=literal)
            with self.subTest(column=column,literal=literal),self.assertRaisesRegex(O.ObservationError,expected):self.invoke()
            self.assertNotIn('select',self.events)
            self.assertEqual([r['label'] for r in self.saved[-1]['queries']['worker']],['inventory','schema_before'])
            self.assertTrue(self.saved[-1]['cleanup_verified'])

    def test_window_drift_or_wrong_rows_discards_candidate_after_cleanup(self):
        for mode in ('drift','wrong'):
            self.setUp()
            if mode=='drift':self.metadata[1]['metadata-location']=self.metadata[1]['metadata-location'].replace('00001','00002')
            else:self.rows=[[2,'wrong']]
            with self.subTest(mode=mode),self.assertRaises(O.ObservationError):self.invoke()
            self.assertEqual(self.events[-2:],['drain','close'])
            self.assertTrue(self.saved[-1]['cleanup_verified'])
            self.assertEqual(self.saved[-1]['state'],'failed')

    def test_first_cancellation_survives_secondary_teardown_and_save(self):
        cancel=KeyboardInterrupt('original')
        self.query_error=cancel;self.teardown_error=OSError('secondary')
        def progress(value):
            self.progress(value)
            if value['state']=='interrupted':raise OSError('save secondary')
        with self.assertRaises(KeyboardInterrupt) as caught:self.invoke(progress=progress)
        self.assertIs(caught.exception,cancel)
        self.assertEqual(self.events.count('select'),1)


class OwnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.output=Path(self.tmp.name).resolve();self.output.chmod(0o700)
        for mocked in (patch.object(RUN,'STATE',self.output/'state'),
                       patch.object(SHARED,'sources',return_value={'fixed.py':'b'*64})):
            mocked.start();self.addCleanup(mocked.stop)

    def test_real_main_owner_schema3_and_private_literal_rowless_summary(self):
        out=io.StringIO()
        def invoke(token,value,emit):return RUN.invoke(token,value,emit,observer=success)
        status=RUN.main(['select','--evidence-token',TOKEN,'--request-stdin'],
            stdin=io.BytesIO(R.canonical(request())),stdout=out,invoker=invoke)
        events=[json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(status,0);self.assertEqual([len(x) for x in events],[4,9])
        self.assertTrue(all(x['schema_version']==3 for x in events))
        self.assertEqual(events[-1]['evidence'],f'tmp/local-experiment/query-filter/{TOKEN}/summary.json')
        path=self.output/'state'/TOKEN/'summary.json';raw=path.read_text();saved=json.loads(raw)
        self.assertEqual(saved['profile'],C.PROFILE);self.assertEqual(saved['request']['where'],request()['where'])
        self.assertEqual(saved['schema_version'],3);self.assertEqual(path.stat().st_mode&0o777,0o600)
        self.assertNotIn('private-row-sentinel',raw);self.assertNotIn('"rows":',raw);self.assertNotIn('SELECT ',raw)

    def test_fixed_error_and_failed_cleanup_never_publish_rows(self):
        for mode in ('error','cleanup'):
            def observer(value,token,progress):
                if mode=='error':raise C.ContractError('FILTER_TYPE_UNSUPPORTED')
                journal,public=observed(value);journal['cleanup_verified']=False
                return journal,public
            report,public=RUN.Owner(TOKEN,request(),self.output).execute(observer=observer)
            self.assertEqual(report['result'],'failed');self.assertIsNone(public)
            self.assertEqual(report['error_code'],'FILTER_TYPE_UNSUPPORTED' if mode=='error' else 'SNAPSHOT_CLEANUP_UNVERIFIED')

    def test_no_resume_or_retry_after_existing_evidence(self):
        RUN.invoke(TOKEN,request(),lambda _:None,observer=success)
        path=self.output/'state'/TOKEN/'summary.json';before=path.read_bytes()
        observer=Mock()
        with self.assertRaisesRegex(RUN.SnapshotError,'EVIDENCE_ALREADY_EXISTS'):
            RUN.invoke(TOKEN,request(),lambda _:None,observer=observer)
        observer.assert_not_called();self.assertEqual(before,path.read_bytes())

    def test_invalid_input_has_no_intent_or_observer(self):
        invoker=Mock();bad=dict(request(),where={'column':'id','literal':'NULL'})
        status=RUN.main(['select','--evidence-token',TOKEN,'--request-stdin'],
            stdin=io.BytesIO(R.canonical(bad)),stdout=io.StringIO(),invoker=invoker)
        self.assertEqual(status,1);invoker.assert_not_called();self.assertFalse((self.output/'state').exists())

    def test_cancel_object_survives_final_save_failure(self):
        cancel=KeyboardInterrupt('original');owner=RUN.Owner(TOKEN,request(),self.output)
        owner.save=Mock(side_effect=[None,OSError('secondary'),OSError('again')])
        with self.assertRaises(KeyboardInterrupt) as caught:owner.execute(observer=Mock(side_effect=cancel))
        self.assertIs(caught.exception,cancel)


if __name__=='__main__':unittest.main()
