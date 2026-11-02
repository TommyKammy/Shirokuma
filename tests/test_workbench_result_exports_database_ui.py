"""Server byte result saves and stale authorization/download guards."""
import unittest
import test_workbench_database_ui as harness

SETUP = r"""
const downloads=[],blobs=[];
Node.prototype.remove=function(){};
document.body=new Node('body');
const originalClick=Node.prototype.click;
Node.prototype.click=function(){if(this.tagName==='A')downloads.push(this.download);else return originalClick.call(this);};
URL.createObjectURL=blob=>{blobs.push(blob);return 'blob:test';};URL.revokeObjectURL=()=>{};
const exported=(run,format='csv',extra={})=>({format,filename:'query-result-'+run.id+'.'+format,content_type:dbExportTypes[format],content_base64:btoa('"n"\r\n"9007199254740993"\r\n'),query_id:run.id,request_id:run.origin.request_id,worksheet_id:run.origin.saved_worksheet_id||run.origin.worksheet_id,context:{...run.origin.context},returned_rows:run.rows.length,saved_rows:run.rows.length,state:run.state,partial:run.state!=='FINISHED'||!!run.truncated,truncated:!!run.truncated,...extra});
const exportCalls=()=>calls.filter(c=>c.path.endsWith('/results/export'));
const inspection=(run,extra={})=>({id:run.id,backend_query_id:'engine-id',request_id:run.origin.request_id,worksheet_id:run.origin.saved_worksheet_id||run.origin.worksheet_id,context:{...run.origin.context},state:run.state,execution_closure:null,sql:'SELECT private',mode:'read-only',created_at:'2026-11-02T00:00:00Z',...extra});
const inspectionCalls=()=>calls.filter(c=>c.path==='/api/v1/queries/query-uuid'&&!c.options?.method);
async function ready(){await boot();inspectRoute=async()=>inspection(dbRuns.get('a'));route=async()=>envelope(dbRuns.get('a'),{result_page_id:'page-1'});await dbRunQuery();return dbRuns.get('a');}
"""

class ResultExportsDatabaseUI(unittest.TestCase):
    def javascript(self, body):
        harness.DatabaseUI.javascript(self, SETUP + body)

    def test_saved_status_relocalizes_without_changing_download_or_capture(self):
        self.javascript(r"""
const run=await ready();setLanguage('ja');
const capture=dbExportCapture(run),origin=run.origin,receipts=[...run.receivedPages],rows=structuredClone(run.rows);
route=async()=>exported(run);await download('csv');
const requestCount=calls.length,binding=uiBindings.get($('result-action-state'));
assert.equal(run.saveNotice.values.state,'FINISHED');
for(const [locale,expected] of [
 ['ja','保存 1 行 · 完了 · 部分結果: false · 上限到達: false。'],
 ['en','Saved 1 rows · FINISHED · partial: false · truncated: false.'],
 ['ja','保存 1 行 · 完了 · 部分結果: false · 上限到達: false。']
]){
 setLanguage(locale);
 assert.equal($('result-action-state').textContent,expected);
 // The retained binding must also translate before setLanguage rerenders controls.
 assert.equal(t(binding.key,binding.values),expected);
 assert.equal(calls.length,requestCount);
 assert.equal(dbExportCapture(run),capture);assert.equal(run.origin,origin);
 assert.deepEqual(run.receivedPages,receipts);assert.deepEqual([...run.receivedPageSet],receipts);
 assert.deepEqual(run.rows,rows);assert.deepEqual(queryResult.rows,rows);
 assert.equal(downloads.length,1);assert.equal(blobs.length,1);
 assert.equal(await blobs[0].text(),'"n"\r\n"9007199254740993"\r\n');
 assert.equal(run.saveNotice.values.rows,1);
}
assert.equal(downloads[0],'query-result-query-uuid.csv');assert.equal(exportCalls().length,1);
assert.equal(calls.filter(c=>c.path==='/api/v1/queries'&&c.options?.method==='POST').length,1);
""")

    def test_pending_save_completes_in_current_locale(self):
        self.javascript(r"""
const run=await ready();setLanguage('ja');
const capture=dbExportCapture(run),held=deferred();route=()=>held.promise;
const pending=download('csv');await tick();assert.equal(exportCalls().length,1);
assert.equal($('result-action-state').textContent,'受領済みの行を保存中…');
const requestCount=calls.length;setLanguage('en');
assert.equal($('result-action-state').textContent,'Saving received rows…');
assert.equal(calls.length,requestCount);assert.equal(dbExportCapture(run),capture);
held.resolve(exported(run));await pending;
assert.equal($('result-action-state').textContent,'Saved 1 rows · FINISHED · partial: false · truncated: false.');
const completedCount=calls.length;setLanguage('ja');
assert.equal($('result-action-state').textContent,'保存 1 行 · 完了 · 部分結果: false · 上限到達: false。');
assert.equal(calls.length,completedCount);assert.equal(dbExportCapture(run),capture);
assert.deepEqual(run.receivedPages,['page-1']);assert.deepEqual([...run.receivedPageSet],['page-1']);
assert.deepEqual(run.rows,[[1]]);assert.deepEqual(queryResult.rows,[[1]]);
assert.equal(downloads.length,1);assert.equal(blobs.length,1);assert.equal(exportCalls().length,1);
assert.equal(await blobs[0].text(),'"n"\r\n"9007199254740993"\r\n');
""")

    def test_three_server_formats_exact_request_and_original_bytes(self):
        self.javascript(r"""
const run=await ready();$('result-filter').value='nothing';
for(const format of ['csv','json','tsv']){
 route=async(path,options)=>exported(run,JSON.parse(options.body).format);
 assert.equal($('export-'+format).disabled,false);await download(format);
 assert.deepEqual(JSON.parse(exportCalls().at(-1).options.body),{format,request_id:run.origin.request_id,worksheet_id:'a',received_pages:['page-1']});
 assert.equal(exportCalls().at(-1).options.headers['X-Shirokuma-Role'],'admin');
 assert.equal(await blobs.at(-1).text(),'"n"\r\n"9007199254740993"\r\n');
 assert.equal(downloads.at(-1),'query-result-query-uuid.'+format);
}
assert.equal($('copy-tsv').hidden,true);assert.equal($('export-tsv').hidden,false);
assert.match($('result-action-state').textContent,/Saved 1 rows/);
for(const locale of ['en','ja']){setLanguage(locale);assert.equal($('request-provenance').children[0].textContent,t(dbExportHelp));}
assert.equal(calls.filter(c=>c.path==='/api/v1/queries'&&c.options?.method==='POST').length,1);
assert.equal(inspectionCalls().length,6);
assert.ok(inspectionCalls().every(c=>c.options.headers['X-Shirokuma-Role']===run.origin.role));
assert.equal(run.origin.sql,'SELECT 1');assert.deepEqual(run.rows,[[1]]);
""")

    def test_history_restoration_no_receipts_and_completed_ledger_prefix(self):
        self.javascript(r"""
const run=await ready();run.state='RUNNING';run.settled=false;run.pending=false;dbRenderRun();
route=async()=>exported(run,'json',{state:'FINISHED',partial:true});await download('json');assert.equal(downloads.length,1);assert.match($('result-action-state').textContent,/partial: true/);
run.restored=true;delete run.receivedPages;delete run.receivedPageSet;dbRenderRun();
for(const format of ['csv','json','tsv']){assert.equal($('export-'+format).disabled,true);await download(format);}
assert.equal(exportCalls().length,1);
""")

    def test_replay_receipts_keep_identical_rows_and_old_envelope_display_only(self):
        self.javascript(r"""
const run=await ready();dbAcceptPage(run,envelope(run,{result_page_id:'page-1'}));assert.deepEqual(run.rows,[[1]]);
dbAcceptPage(run,envelope(run,{result_page_id:'page-2'}));assert.deepEqual(run.rows,[[1],[1]]);assert.deepEqual(run.receivedPages,['page-1','page-2']);
dbAcceptPage(run,envelope(run));dbRenderRun();assert.deepEqual(queryResult.rows,[[1],[1],[1]]);assert.equal($('export-csv').disabled,true);await download('csv');assert.equal(exportCalls().length,0);
dbHideRows();assert.equal(run.receivedPages,undefined);assert.equal(run.saveBusy,undefined);assert.deepEqual(run.rows,[]);
""")

    def test_pending_without_receipt_does_not_poison_actual_page(self):
        self.javascript(r"""
const run=await ready();run.rows=[];delete run.receivedPages;delete run.receivedPageSet;
dbAcceptPage(run,envelope(run,{backend_query_id:null,result_page_id:undefined,state:'QUEUED',columns:[],data:[],next_uri:'/api/v1/queries/query-uuid/pages/pending'}));
assert.equal(run.exportUnavailable,undefined);
dbAcceptPage(run,envelope(run,{result_page_id:'actual'}));dbRenderRun();assert.equal($('export-json').disabled,false);
""")

    def test_reply_changes_worksheet_role_context_receipts_or_close_discard(self):
        for change in ['worksheet','role','schema','page','close','purge','epoch']:
            with self.subTest(change=change):
                self.javascript("const change="+repr(change)+r""";
const run=await ready();const held=deferred();route=()=>held.promise;
const pending=download('csv');await tick();assert.equal(exportCalls().length,1);
await download('json');assert.equal(exportCalls().length,1);
for(const format of ['csv','json','tsv'])assert.equal($('export-'+format).disabled,true);
const reply=exported(run);
if(change==='worksheet'){worksheets.push({...input('b'),selected_role:'admin'});worksheetId='b';dbControls();}
if(change==='role'){currentRole='data_engineer';dbApplyRole();}
if(change==='schema'){dbCurrent().schema_id='raw';dbControls();}
if(change==='page')dbAcceptPage(run,envelope(run,{result_page_id:'page-2'}));
if(change==='close')worksheets=[];
if(change==='purge')dbHideRows();
if(change==='epoch')++dbAuthorizationVersion;
held.resolve(reply);await pending;assert.equal(downloads.length,0);
assert.equal(calls.filter(c=>c.path==='/api/v1/queries'&&c.options?.method==='POST').length,1);
""")

    def test_current_session_and_inventory_loss_before_and_after_post(self):
        for phase in ['before','after']:
            for loss in ['principal','ceiling','grant','denial']:
                with self.subTest(phase=phase, loss=loss):
                    self.javascript("const phase="+repr(phase)+",loss="+repr(loss)+r""";
const run=await ready(),held=deferred();route=()=>held.promise;
const lose=()=>{if(loss==='principal')session.username='other';if(loss==='ceiling')session.maximum_role='bi_viewer';if(loss==='grant')inventory[0].grant_revision++;if(loss==='denial')inventoryRoute=async()=>({http:401,body:{code:'AUTHENTICATION_REQUIRED'}});};
if(phase==='before')lose();const pending=download('csv');await tick();
if(phase==='after'){assert.equal(exportCalls().length,1);lose();held.resolve(exported(run));}
await pending;assert.equal(downloads.length,0);assert.equal(exportCalls().length,phase==='before'?0:1);assert.equal(run.receivedPages,undefined);
""")

    def test_new_page_during_final_inventory_and_transient_worksheet_switch_block(self):
        self.javascript(r"""
const run=await ready();route=async()=>exported(run);const held=deferred();let reads=0;
inventoryRoute=()=>++reads===2?held.promise:Promise.resolve({items:structuredClone(inventory)});
const pending=download('csv');await tick();assert.equal(reads,2);
dbAcceptPage(run,envelope(run,{result_page_id:'new-page'}));held.resolve({items:structuredClone(inventory)});await pending;assert.equal(downloads.length,0);
inventoryRoute=null;const reply=deferred();route=()=>reply.promise;const next=download('csv');await tick();
worksheets.push({...input('b'),selected_role:'admin'});worksheetId='b';dbControls();worksheetId='a';dbControls();reply.resolve(exported(run));await next;assert.equal(downloads.length,0);
""")

    def test_invalid_metadata_or_base64_no_download_and_cache_loss_no_retry(self):
        self.javascript(r"""
const run=await ready();for(const extra of [{extra:true},{filename:'../secret.csv'},{content_type:'text/html'},{saved_rows:9},{returned_rows:9},{partial:'yes'},{state:'RUNNING',partial:false},{truncated:true},{context:{}},{content_base64:'??'},{query_id:'other'}]){
 route=async()=>exported(run,'csv',extra);await download('csv');assert.equal(downloads.length,0);
}
route=async()=>({http:409,body:{code:'RESULT_EXPORT_UNAVAILABLE'}});await download('csv');const before=exportCalls().length;
await download('tsv');assert.equal(exportCalls().length,before);assert.match($('result-action-state').textContent,/cached results are unavailable/);
for(const format of ['csv','json','tsv'])assert.equal($('export-'+format).disabled,true);
assert.equal(calls.filter(c=>c.path==='/api/v1/queries'&&c.options?.method==='POST').length,1);
""")

    def test_primary_inspection_denials_before_and_after_export(self):
        for phase in ['before', 'after']:
            for loss in ['401', '403', 'schema', 'backend', 'id', 'request', 'worksheet', 'context', 'state', 'closure']:
                with self.subTest(phase=phase, loss=loss):
                    self.javascript("const phase="+repr(phase)+",loss="+repr(loss)+r"""
const run=await ready();route=async()=>exported(run);let reads=0;
inspectRoute=async()=>{
 const bad=phase==='before'||++reads===2;
 if(!bad)return inspection(run);
 if(loss==='401'||loss==='403')return {http:Number(loss),body:{}};
 if(loss==='schema'||loss==='backend')return {http:403,body:{code:'DB_ACCESS_REVOKED'}};
 const extra={};
 if(loss==='id')extra.id='other';
 if(loss==='request')extra.request_id='other';
 if(loss==='worksheet')extra.worksheet_id='other';
 if(loss==='context')extra.context={...run.origin.context,sql_schema:'other'};
 if(loss==='state')extra.state='NEW';
 if(loss==='closure')extra.execution_closure='CLOSED';
 return inspection(run,extra);
};
await download('csv');
assert.equal(downloads.length,0);assert.equal(exportCalls().length,phase==='before'?0:1);
assert.equal(run.receivedPages,undefined);assert.deepEqual(run.rows,[]);
assert.equal(calls.filter(c=>c.path.includes('/pages/')||c.path.includes('/cancel')).length,0);
assert.equal(calls.filter(c=>c.path==='/api/v1/queries'&&c.options?.method==='POST').length,1);
""")

    def test_every_inspection_context_field_is_exact(self):
        for key in ['database_id','schema_id','warehouse_id','sql_catalog','sql_schema','selected_role','binding_revision','generation_id','grant_revision']:
            with self.subTest(key=key):
                self.javascript("const key="+repr(key)+r"""
const run=await ready();const context={...run.origin.context,[key]:'different'};
inspectRoute=async()=>inspection(run,{context});await download('csv');
assert.equal(downloads.length,0);assert.equal(exportCalls().length,0);assert.equal(run.receivedPages,undefined);
""")

    def test_navigation_and_explorer_round_trips_discard_delayed_download(self):
        for phase in ['inspection_before', 'transfer', 'inspection_after']:
            for surface in ['main', 'explorer']:
                with self.subTest(phase=phase, surface=surface):
                    self.javascript("const phase="+repr(phase)+",surface="+repr(surface)+r"""
const run=await ready(),held=deferred(),receipts=[...run.receivedPages],origin=run.origin;let reads=0;
route=()=>phase==='transfer'?held.promise:Promise.resolve(exported(run));
inspectRoute=()=>{++reads;return (phase==='inspection_before'&&reads===1||phase==='inspection_after'&&reads===2)?held.promise:Promise.resolve(inspection(run));};
const pending=download('csv');await tick();
if(surface==='main'){activate($('tab-jobs'));activate($('tab-warehouse'));}
else {dbShowWorkspace('details');assert.equal(dbExportReady(run),false);dbShowWorkspace('worksheet');}
held.resolve(phase==='transfer'?exported(run):inspection(run));await pending;
assert.equal(downloads.length,0);assert.equal(dbRuns.get('a'),run);assert.equal(run.origin,origin);
assert.deepEqual(run.receivedPages,receipts);assert.deepEqual(run.rows,[[1]]);
assert.equal(exportCalls().length,phase==='inspection_before'?0:1);
""")

    def test_stale_inspection_denial_does_not_purge_returned_worksheet(self):
        for phase in ['before', 'after']:
            with self.subTest(phase=phase):
                self.javascript("const phase="+repr(phase)+r"""
const run=await ready(),held=deferred();let reads=0;
route=async()=>exported(run);
inspectRoute=()=>++reads===(phase==='before'?1:2)?held.promise:Promise.resolve(inspection(run));
const pending=download('csv');await tick();
dbShowWorkspace('details');dbShowWorkspace('worksheet');
held.resolve({http:403,body:{code:'DB_ACCESS_REVOKED'}});await pending;
assert.equal(downloads.length,0);assert.deepEqual(run.rows,[[1]]);assert.deepEqual(run.receivedPages,['page-1']);
assert.equal(run.rowsPurged,undefined);
""")

    def test_replacement_run_survives_stale_inspection_denial(self):
        for phase in ['before', 'after']:
            with self.subTest(phase=phase):
                self.javascript("const phase="+repr(phase)+r"""
const run=await ready(),held=deferred();let reads=0;route=async()=>exported(run);
inspectRoute=()=>++reads===(phase==='before'?1:2)?held.promise:Promise.resolve(inspection(run));
const pending=download('csv');await tick();
const replacement={...run,token:'new-run-token',rows:[[99]],receivedPages:['new-page'],receivedPageSet:new Set(['new-page'])};delete replacement.saveBusy;
dbRuns.set('a',replacement);dbRenderRun();
held.resolve({http:403,body:{code:'DB_ACCESS_REVOKED'}});await pending;
assert.equal(downloads.length,0);assert.equal(dbRuns.get('a'),replacement);
assert.deepEqual(replacement.rows,[[99]]);assert.deepEqual(replacement.receivedPages,['new-page']);assert.equal(replacement.rowsPurged,undefined);
""")

    def test_saved_worksheet_identity_inspection_does_not_adopt_run_or_editor(self):
        self.javascript(r"""
const run=await ready();run.origin=Object.freeze({...run.origin,saved_worksheet_id:'original-worksheet'});
const worksheet=structuredClone(dbCurrent()),origin=run.origin,state=run.state,columns=run.columns;
inspectRoute=async()=>inspection(run,{state:'RUNNING',sql:'SELECT should_not_be_adopted',stats:{rows:999}});
route=async()=>exported(run);await download('csv');
assert.equal(downloads.length,1);assert.equal(inspectionCalls().length,2);
assert.equal(JSON.parse(exportCalls()[0].options.body).worksheet_id,'original-worksheet');
assert.deepEqual(dbCurrent(),worksheet);assert.equal(run.origin,origin);assert.equal(run.state,state);assert.equal(run.columns,columns);assert.deepEqual(run.rows,[[1]]);
assert.equal(run.stats,undefined);assert.equal($('sql').value,'SELECT 1');
""")

if __name__ == '__main__': unittest.main()
