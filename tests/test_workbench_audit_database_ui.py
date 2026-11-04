"""DB-P6-1 audit tests execute the actual DATABASE source in the shared DOM adapter."""
import unittest
from unittest.mock import patch

import test_workbench_database_ui as database_ui


AUDIT = r'''
const auditText=node=>[node.textContent,...node.descendants().map(n=>n.textContent)].join(' | ');
const auditContext=(path,role)=>{
 const p=new URLSearchParams(path.split('?')[1]),scope=p.get('scope'),d=inventory.find(d=>d.id===p.get('database_id'));
 return {scope,principal_id:session.username,selected_role:role,database_id:scope==='platform'?null:d?.id,
 binding_revision:scope==='platform'?null:d?.binding_revision,generation_id:scope==='platform'?null:d?.generation_id,
 grant_revision:scope==='platform'?null:d?.grant_revision,platform_admin:session.platform_admin};
};
const auditEvent=(id='opaque /?#',extra={})=>({id,source:'query',source_id:'same-name',action:'execute',actor:'owner',database_id:dbAudit.database,
 schema_id:'schema',warehouse_id:'LOCAL',saved_role:null,state:'UNKNOWN',created_at:null,updated_at:null,finished_at:null,
 revision:2,committed:true,has_cleanup_error:true,links:[{relation:'query',source:'query',source_id:'related',event_id:'query:related',url:'https://never',sql:'SECRET'}, {relation:'job',source:'job',source_id:'resource-only',event_id:null}],
 sql:'SECRET_SQL',prompt:'SECRET_PROMPT',payload:{password:'SECRET_PASSWORD'},...extra});
let auditList=null,auditDetail=null,auditDownload=null,auditCapabilities=null,telemetry=null;
const auditCursor=(position=null)=>{
 const payload=Buffer.from(JSON.stringify({version:1,ceilings:{query:25,operation:12},position})).toString('base64url');
 return payload+'.'+require('node:crypto').createHmac('sha256','fixture-key').update(payload).digest('base64url');
};
function auditEnvelope(path,options,event=auditEvent()){
 const common={context:auditContext(path,options.headers['X-Shirokuma-Role']),coverage:{metadata_only:true}};
 if(!path.startsWith('/api/v1/audit/events?'))return {...common,event};
 const params=new URLSearchParams(path.split('?')[1]);
 const filters=Object.fromEntries(['source','state','search','from','to'].map(key=>[key,params.get(key)||'']));filters.limit=Number(params.get('limit')||50);
 return {...common,items:[event],page_cursor:params.get('cursor')||auditCursor(),next_cursor:null,filters,observed_at:'2026-10-29T00:00:00Z'};
}
async function auditBoot(platform=false){
 session.platform_admin=platform;
 route=async(path,options)=>{
  if(path==='/api/v1/audit/capabilities')return auditCapabilities?auditCapabilities(path,options):{
   scopes:['self',...(options.headers['X-Shirokuma-Role']==='admin'?['database',...(session.platform_admin?['platform']:[])]:[])],sources:['query','operation'],platform_admin:session.platform_admin};
  if(path.startsWith('/api/v1/audit/events?'))return auditList?auditList(path,options):auditEnvelope(path,options);
  if(path.includes('/download?'))return auditDownload?auditDownload(path,options):auditEnvelope(path,options);
  if(path.startsWith('/api/v1/audit/events/'))return auditDetail?auditDetail(path,options):auditEnvelope(path,options,auditEvent(dbAudit.selected));
  if(path==='/api/v1/audit/telemetry')return telemetry?telemetry(path,options):{availability:'unavailable',code:'SOURCE_UNAVAILABLE',limit:100,truncated:false,items:[]};
  return {};
 };
 await dualBoot();await dbAuditOpen();
}
async function auditChange(key,value){$('db-audit-'+key).value=value;await $('db-audit-'+key).onchange();}
const auditRequests=()=>calls.filter(c=>c.path.startsWith('/api/v1/audit/events?'));
const auditNoLegacy=()=>{assert.equal(calls.filter(c=>c.options?.method==='POST').length,0);assert.equal(calls.filter(c=>/^\/api\/(pawprints|history|query|load)/.test(c.path)).length,0);};
'''


class AuditDatabaseUI(unittest.TestCase):
    def javascript(self, source):
        # Keep the shared fixture unchanged; Node 25 receives this suite via stdin.
        run = database_ui.subprocess.run

        def stdin_run(args, **kwargs):
            if list(args[:2]) == ['node', '-e']:
                return run(['node', '-'], input=args[2], **kwargs)
            return run(args, **kwargs)

        with patch.object(database_ui.subprocess, 'run', stdin_run):
            database_ui.DatabaseUI.javascript(self, source)

    def run_audit(self, source):
        self.javascript(AUDIT + source)

    def test_independent_context_two_databases_and_metadata_only(self):
        self.run_audit(r'''
await auditBoot();inventory[1].display_name=inventory[0].display_name;
assert.equal($('db-audit-search').tagName,'INPUT');assert.equal($('db-audit-submit').tagName,'BUTTON');
const inputs=JSON.stringify(dbInput(dbCurrent())),explorer=JSON.stringify(dbExplorer);
assert.equal(auditRequests().length,0);assert.deepEqual($('db-audit-source').options.map(o=>o.value),['','query','operation']);
await auditChange('database','finance');await dbAuditSearch();
assert.equal(new URLSearchParams(auditRequests().at(-1).path.split('?')[1]).get('database_id'),'finance');
assert.equal(JSON.stringify(dbInput(dbCurrent())),inputs);assert.equal(JSON.stringify(dbExplorer),explorer);
const event=dbAudit.items[0];await dbAuditDetail(event.id);
assert.ok(calls.some(c=>c.path.includes('/events/'+encodeURIComponent(event.id)+'?')));
assert.match(auditText($('db-audit-detail')),/Committed.*Yes.*Cleanup failure.*Yes/);
assert.match(auditText($('db-audit-rows')),/UNKNOWN/);assert.match(auditText($('db-audit-rows')),/Not recorded/);
assert.doesNotMatch(auditText($('db-audit-panel')),/SECRET|https:\/\/never/);
assert.ok(auditText($('db-audit-detail')).includes('query · '+t('Saved SQL queries')+' · related'));
assert.match(auditText($('db-audit-context')),/finance/);auditNoLegacy();
assert.equal(dbAuditTime('2026-10-29T09:00:00+09:00'),'2026-10-29T00:00:00.000Z');
''')

    def test_explicit_drafts_utc_bounds_and_cursor_binding(self):
        self.run_audit(r'''
await auditBoot();let number=0;
auditList=(p,o)=>({...auditEnvelope(p,o,auditEvent('page'+(++number))),next_cursor:number===1?'opaque cursor':null});
$('db-audit-from').value='2026-10-28T14:30';$('db-audit-to').value='2026-10-29T14:30';
$('db-audit-source').value='query';$('db-audit-state').value='UNKNOWN';$('db-audit-search').value='first';$('db-audit-limit').value='2';
$('db-audit-search').oninput();assert.equal(auditRequests().length,0);assert.match($('db-audit-status').textContent,/pending/);
await dbAuditSearch();const first=new URLSearchParams(auditRequests()[0].path.split('?')[1]);
assert.equal(first.get('from'),'2026-10-28T14:30:00.000Z');assert.equal(first.get('to'),'2026-10-29T14:30:00.000Z');
assert.equal(first.get('source'),'query');assert.equal(first.get('limit'),'2');
$('db-audit-search').value='second';$('db-audit-search').oninput();await dbAuditSearch('next');
const next=new URLSearchParams(auditRequests().at(-1).path.split('?')[1]);assert.equal(next.get('search'),'first');assert.equal(next.get('cursor'),'opaque cursor');
assert.deepEqual(dbAudit.cursors,[auditCursor()]);assert.match($('db-audit-status').textContent,/pending/);
await dbAuditSearch('previous');assert.equal(new URLSearchParams(auditRequests().at(-1).path.split('?')[1]).get('cursor'),auditCursor());assert.deepEqual(dbAudit.cursors,[]);
await dbAuditSearch();assert.equal(dbAudit.applied.search,'second');assert.deepEqual(dbAudit.cursors,[]);
const count=auditRequests().length;$('db-audit-limit').value='101';await dbAuditSearch();assert.equal(auditRequests().length,count);
$('db-audit-limit').value='50';$('db-audit-from').value='2026-02-30T12:00';await dbAuditSearch();assert.equal(auditRequests().length,count);
auditNoLegacy();
''')

    def test_previous_reuses_snapshot_after_insert_and_missing_cursor_fails_closed(self):
        self.run_audit(r'''
await auditBoot();await auditChange('scope','database');
const original=[auditEvent('query:25'),auditEvent('query:24')],second=[auditEvent('query:23')];
let live=[...original,...second];const start=auditCursor(),next=auditCursor(['query:24']);
auditList=(p,o)=>{
 const cursor=new URLSearchParams(p.split('?')[1]).get('cursor');
 const data=auditEnvelope(p,o);return {...data,items:cursor===next?second:cursor===start?original:live.slice(0,2),page_cursor:cursor||start,next_cursor:cursor===next?null:next};
};
$('db-audit-limit').value='2';$('db-audit-source').value='query';$('db-audit-search').value='owner';
await dbAuditSearch();assert.deepEqual(dbAudit.items.map(e=>e.id),original.map(e=>e.id));
await dbAuditSearch('next');live.unshift(auditEvent('query:26'));await dbAuditSearch('previous');
assert.deepEqual(dbAudit.items.map(e=>e.id),original.map(e=>e.id));
const requests=auditRequests(),first=new URLSearchParams(requests[0].path.split('?')[1]),previous=new URLSearchParams(requests.at(-1).path.split('?')[1]);
assert.equal(previous.get('cursor'),start);previous.delete('cursor');assert.equal(previous.toString(),first.toString());
assert.equal(requests.at(-1).options.headers['X-Shirokuma-Role'],'admin');assert.equal(previous.get('scope'),'database');assert.equal(previous.get('database_id'),'db');
auditList=(p,o)=>{const data=auditEnvelope(p,o);delete data.page_cursor;return data;};
await dbAuditSearch();assert.equal(dbAudit.items.length,0);assert.equal(dbAudit.applied,null);
const count=auditRequests().length;await dbAuditSearch('previous');await dbAuditSearch('next');assert.equal(auditRequests().length,count);auditNoLegacy();
''')

    def test_relations_navigate_only_authorized_metadata_and_drop_stale_links(self):
        self.run_audit(r'''
await auditBoot();await dbAuditSearch();await dbAuditDetail(dbAudit.items[0].id);
const buttons=()=>$('db-audit-detail').descendants().filter(n=>n.tagName==='BUTTON'&&n.textContent==='View linked audit metadata');
assert.equal(buttons().length,1);assert.match(auditText($('db-audit-detail')),/job · job · resource-only/);
const oldButton=buttons()[0];await oldButton.onclick();assert.equal(dbAudit.detail.id,'query:related');
const request=calls.filter(c=>c.path.startsWith('/api/v1/audit/events/')&&!c.path.includes('/download?')).at(-1);
assert.equal(request.path,'/api/v1/audit/events/query%3Arelated?scope=self&database_id=db');assert.equal(request.options.headers['X-Shirokuma-Role'],'admin');
const count=calls.length;await oldButton.onclick();assert.equal(calls.length,count);
const inversion=deferred();auditDetail=()=>inversion.promise;const older=buttons()[0].onclick();await tick();
auditDetail=(p,o)=>auditEnvelope(p,o,auditEvent('query:latest'));await dbAuditDetail('query:latest');
inversion.resolve(auditEnvelope('/api/v1/audit/events/query%3Arelated?scope=self&database_id=db',{headers:{'X-Shirokuma-Role':'admin'}},auditEvent('query:related')));await older;
assert.equal(dbAudit.detail.id,'query:latest');
const held=deferred();auditDetail=()=>held.promise;const link=buttons()[0],pending=link.onclick();await tick();
await auditChange('scope','database');await auditChange('database','finance');await dbAuditSearch();
held.resolve(auditEnvelope('/api/v1/audit/events/query%3Arelated?scope=self&database_id=db',{headers:{'X-Shirokuma-Role':'admin'}},auditEvent('query:related')));await pending;
assert.equal(dbAudit.detail,null);assert.equal(dbAudit.scope,'database');assert.equal(dbAudit.database,'finance');assert.equal($('db-audit-detail').children.length,0);
const after=calls.length;await link.onclick();assert.equal(calls.length,after);
assert.doesNotMatch(auditText($('db-audit-panel')),/SECRET|https:\/\/never/);auditNoLegacy();
''')

    def test_same_scope_races_stale_deny_and_cursor_stack(self):
        self.run_audit(r'''
await auditBoot();const old=deferred();let first=true;
auditList=(p,o)=>first?(first=false,old.promise):({...auditEnvelope(p,o,auditEvent('new')),next_cursor:'new-next'});
const a=dbAuditSearch();await tick();await dbAuditSearch();
old.resolve({http:403,body:{code:'DB_ACCESS_REVOKED',message:'SECRET'}});await a;
assert.equal(dbAudit.items[0].id,'new');assert.equal(dbAudit.next,'new-next');assert.deepEqual(dbAudit.cursors,[]);
const page=deferred();auditList=()=>page.promise;const pending=dbAuditSearch('next');await tick();
auditList=(p,o)=>auditEnvelope(p,o,auditEvent('restart'));await dbAuditSearch();
page.resolve(auditEnvelope('/api/v1/audit/events?scope=self&database_id=db',{headers:{'X-Shirokuma-Role':'admin'}},auditEvent('old-page')));await pending;
assert.equal(dbAudit.items[0].id,'restart');assert.deepEqual(dbAudit.cursors,[]);auditNoLegacy();
''')

    def test_a_b_a_and_detail_selection_inversion(self):
        self.run_audit(r'''
await auditBoot();const old=deferred();let first=true;
auditList=(p,o)=>first?(first=false,old.promise):auditEnvelope(p,o,auditEvent('current'));
const a=dbAuditSearch();await tick();await auditChange('database','finance');await auditChange('database','db');await dbAuditSearch();
old.resolve(auditEnvelope('/api/v1/audit/events?scope=self&database_id=db',{headers:{'X-Shirokuma-Role':'admin'}},auditEvent('old')));await a;
assert.equal(dbAudit.items[0].id,'current');const d=deferred();
auditDetail=(p,o)=>p.includes('/one?')?d.promise:auditEnvelope(p,o,auditEvent('two'));
const one=dbAuditDetail('one');await tick();await dbAuditDetail('two');
d.resolve(auditEnvelope('/api/v1/audit/events/one?scope=self&database_id=db',{headers:{'X-Shirokuma-Role':'admin'}},auditEvent('one')));await one;
assert.equal(dbAudit.detail.id,'two');assert.doesNotMatch(auditText($('db-audit-detail')),/one/);auditNoLegacy();
''')

    def test_role_and_real_platform_authority_are_separate(self):
        self.run_audit(r'''
await auditBoot();await auditChange('scope','database');await dbAuditSearch();assert.equal(dbAudit.items.length,1);
await auditChange('role','bi_viewer');assert.equal(dbAudit.items.length,0);const count=auditRequests().length;
await dbAuditSearch();assert.equal(dbAudit.scope,'self');assert.equal(auditRequests().length,count+1);
await auditChange('role','admin');await auditChange('scope','platform');assert.equal(dbAudit.scope,'self');
session.platform_admin=true;await dbReadInventory('admin');await dbAuditOpen();await auditChange('scope','platform');await dbAuditSearch();assert.equal(dbAudit.items.length,1);
assert.equal(new URLSearchParams(auditRequests().at(-1).path.split('?')[1]).has('database_id'),false);
session.platform_admin=false;await dbAuditRefreshAuthorization();assert.equal(dbAudit.items.length,0);
auditNoLegacy();
''')

    def test_header_downgrade_reconciles_controls_immediately_and_rejects_late_rows(self):
        for scope in ['database', 'platform']:
            with self.subTest(scope=scope):
                self.run_audit("const scope=" + repr(scope) + ";" + r'''
await auditBoot(true);await auditChange('scope',scope);await dbAuditSearch();await dbAuditDetail(dbAudit.items[0].id);
$('sql').value='SELECT 42 AS saved_draft';dbSave();const worksheet=JSON.stringify(dbInput(dbCurrent())),saved=storage.get(dbWorksheetKey);
const held=deferred();let oldResponse;auditList=(p,o)=>{oldResponse=auditEnvelope(p,o,auditEvent('late-admin'));return held.promise;};
const old=dbAuditSearch();await tick();$('role-selector').value='bi_viewer';const changed=$('role-selector').onchange();
const restricted=()=>{
 assert.equal(dbAudit.role,'bi_viewer');assert.equal(dbAudit.scope,'self');
 assert.equal($('db-audit-role').value,'bi_viewer');assert.equal($('db-audit-scope').value,'self');
 for(const role of ['admin','data_engineer'])assert.equal($('db-audit-role').options.find(o=>o.value===role).disabled,true);
 for(const scope of ['database','platform'])assert.equal($('db-audit-scope').options.find(o=>o.value===scope).disabled,true);
 assert.equal($('db-audit-telemetry').disabled,true);assert.equal($('db-audit-telemetry').hidden,true);
 assert.equal(dbAudit.items.length,0);assert.equal(dbAudit.detail,null);
 assert.equal(JSON.stringify(dbInput(dbCurrent())),worksheet);assert.equal($('sql').value,'SELECT 42 AS saved_draft');assert.equal(storage.get(dbWorksheetKey),saved);
};
restricted();assert.equal($('db-audit-database').options.some(o=>o.value&&!o.disabled),false);
await changed;await dbRefreshAccess();restricted();
held.resolve(oldResponse);await old;restricted();auditList=null;
await dbAuditSearch();assert.equal(dbAudit.items.length,1);assert.equal(auditRequests().at(-1).options.headers['X-Shirokuma-Role'],'bi_viewer');
$('role-selector').value='admin';await $('role-selector').onchange();await dbRefreshAccess();
assert.equal(dbAudit.role,'bi_viewer');assert.equal(dbAudit.scope,'self');
await auditChange('role','admin');assert.equal($('db-audit-scope').options.find(o=>o.value==='platform').disabled,false);
await auditChange('scope','platform');assert.equal(dbAudit.scope,'platform');assert.equal($('db-audit-telemetry').disabled,false);
await dbAuditSearch();assert.equal(auditRequests().at(-1).options.headers['X-Shirokuma-Role'],'admin');
assert.equal(JSON.stringify(dbInput(dbCurrent())),worksheet);assert.equal(storage.get(dbWorksheetKey),saved);auditNoLegacy();
''')

    def test_header_refresh_offers_newly_permitted_db_when_old_db_is_unavailable(self):
        self.run_audit(r'''
await auditBoot(true);await auditChange('scope','database');await dbAuditSearch();await dbAuditDetail(dbAudit.items[0].id);
const oldDatabase=dbAudit.database;
const viewerDatabase={...inventory[1],id:'viewer-logical-db',display_name:'Viewer logical database',allowed_roles:['bi_viewer']};inventory.push(viewerDatabase);
inventoryRoute=(p,o)=>({items:structuredClone(o.headers['X-Shirokuma-Role']==='bi_viewer'?[viewerDatabase]:inventory.filter(d=>d.id!==viewerDatabase.id))});
$('sql').value='SELECT 57 AS untouched_draft';dbSave();const worksheet=JSON.stringify(dbInput(dbCurrent())),saved=storage.get(dbWorksheetKey);
const held=deferred();let oldResponse;auditList=(p,o)=>{oldResponse=auditEnvelope(p,o,auditEvent('late-owner-db'));return held.promise;};
const old=dbAuditSearch();await tick();const before=auditRequests().length;
$('role-selector').value='bi_viewer';const changed=$('role-selector').onchange();
assert.equal(dbAudit.items.length,0);assert.equal(dbAudit.detail,null);assert.equal(dbAudit.role,'bi_viewer');assert.equal(dbAudit.scope,'self');
assert.equal($('db-audit-database').options.some(o=>o.value&&!o.disabled),false);
await changed;
const available=()=>$('db-audit-database').options.find(o=>o.value===viewerDatabase.id);
assert.ok(available(),'Header handler must expose the current viewer inventory without opening audit or searching');assert.equal(available().disabled,false);
assert.equal($('db-audit-database').options.find(o=>o.value===oldDatabase).disabled,true);
assert.equal(dbAudit.database,oldDatabase);assert.equal(auditRequests().length,before);
assert.equal($('db-audit-scope').options.find(o=>o.value==='platform').disabled,true);assert.equal($('db-audit-telemetry').disabled,true);
held.resolve(oldResponse);await old;assert.equal(dbAudit.items.length,0);assert.equal(dbAudit.detail,null);assert.equal(available().disabled,false);
await dbRefreshAccess();assert.equal(available().disabled,false);assert.equal(auditRequests().length,before);
auditList=null;await auditChange('database',viewerDatabase.id);await dbAuditSearch();
assert.equal(dbAudit.items.length,1);assert.equal(dbAudit.items[0].database_id,viewerDatabase.id);
const request=auditRequests().at(-1),params=new URLSearchParams(request.path.split('?')[1]);
assert.equal(request.options.headers['X-Shirokuma-Role'],'bi_viewer');assert.equal(params.get('scope'),'self');assert.equal(params.get('database_id'),viewerDatabase.id);
assert.equal(JSON.stringify(dbInput(dbCurrent())),worksheet);assert.equal(storage.get(dbWorksheetKey),saved);assert.equal($('sql').value,'SELECT 57 AS untouched_draft');auditNoLegacy();
''')

    def test_header_change_preserves_permitted_independent_reference_role(self):
        self.run_audit(r'''
await auditBoot(true);await auditChange('role','bi_viewer');await dbAuditSearch();
const worksheet=JSON.stringify(dbInput(dbCurrent()));
$('role-selector').value='data_engineer';const changed=$('role-selector').onchange();
assert.equal(dbAudit.role,'bi_viewer');assert.equal($('db-audit-role').value,'bi_viewer');
assert.equal($('db-audit-role').options.find(o=>o.value==='admin').disabled,true);
await changed;await dbRefreshAccess();assert.equal(dbAudit.role,'bi_viewer');assert.equal(dbAudit.scope,'self');
assert.equal(JSON.stringify(dbInput(dbCurrent())),worksheet);auditNoLegacy();
''')

    def test_authorization_failure_and_platform_revocation_restrict_controls(self):
        for loss in ['network', 'inventory_network', 'revocation', 'platform', 'maximum']:
            with self.subTest(loss=loss):
                self.run_audit("const loss=" + repr(loss) + ";" + r'''
await auditBoot(true);await auditChange('scope','platform');await dbAuditSearch();
const worksheet=JSON.stringify(dbInput(dbCurrent()));
if(loss==='inventory_network')inventoryRoute=()=>{throw Error('SECRET');};
else if(loss==='network')auditCapabilities=()=>{throw Error('SECRET');};
else if(loss==='revocation')auditCapabilities=()=>({http:403,body:{code:'DB_ACCESS_REVOKED'}});
else if(loss==='platform')session.platform_admin=false;else session.maximum_role='bi_viewer';
if(loss==='inventory_network')await dbRefreshAccess();else await dbAuditRefreshAuthorization();
assert.equal(dbAudit.scope,'self');assert.equal(dbAudit.items.length,0);assert.equal(dbAudit.detail,null);
for(const scope of ['database','platform'])assert.equal($('db-audit-scope').options.find(o=>o.value===scope).disabled,true);
assert.equal($('db-audit-telemetry').disabled,true);assert.equal($('db-audit-database').options.some(o=>o.value&&!o.disabled),false);
if(loss==='maximum'){assert.equal(dbAudit.role,'bi_viewer');assert.equal($('db-audit-role').options.find(o=>o.value==='admin').disabled,true);}
assert.equal(JSON.stringify(dbInput(dbCurrent())),worksheet);assert.doesNotMatch(auditText($('db-audit-panel')),/SECRET/);auditNoLegacy();
''')

    def test_missing_mismatched_context_and_unsafe_errors_fail_closed(self):
        for variant in ['missing', 'other_db', 'other_principal', 'binding']:
            with self.subTest(variant=variant):
                self.run_audit("const variant=" + repr(variant) + ";" + r'''
await auditBoot();auditList=(p,o)=>{const data=auditEnvelope(p,o);if(variant==='missing')delete data.context;
else if(variant==='other_db')data.context.database_id='finance';else if(variant==='other_principal')data.context.principal_id='other';else data.context.binding_revision=999;return data;};
await dbAuditSearch();assert.equal(dbAudit.items.length,0);assert.match($('db-audit-status').textContent,/unavailable/);
auditList=()=>({http:403,body:{code:'DENIED',message:'SECRET_CREDENTIAL'}});await dbAuditSearch();
assert.doesNotMatch(auditText($('db-audit-panel')),/SECRET/);auditNoLegacy();
''')

    def test_revocation_and_late_allow_cannot_restore_rows(self):
        for loss in ['grant', 'binding', 'retire', 'identity', 'maximum', 'unavailable']:
            with self.subTest(loss=loss):
                self.run_audit("const loss=" + repr(loss) + ";" + r'''
await auditBoot();await dbAuditSearch();await dbAuditDetail(dbAudit.items[0].id);
const old=deferred(),allow=structuredClone(inventory);let once=true;
inventoryRoute=()=>once?(once=false,old.promise):{items:structuredClone(inventory)};
const pending=dbAuditSearch();await tick();
if(loss==='grant')inventory[0].grant_revision++;else if(loss==='binding')inventory[0].generation_id='new';else if(loss==='retire')inventory[0].state='RETIRING';
else if(loss==='identity')session.username='other';else if(loss==='maximum')session.maximum_role='bi_viewer';else inventoryRoute=()=>{throw Error('SECRET_NETWORK');};
await dbRefreshAccess();assert.equal(dbAudit.items.length,0);assert.equal(dbAudit.detail,null);assert.equal(dbAudit.applied,null);
old.resolve({items:allow});await pending;assert.equal(dbAudit.items.length,0);assert.equal(dbAudit.detail,null);
assert.doesNotMatch(auditText($('db-audit-panel')),/SECRET/);auditNoLegacy();
''')

    def test_stale_authorization_denial_cannot_erase_new_scope(self):
        self.run_audit(r'''
await auditBoot();const held=deferred();let once=true;
inventoryRoute=()=>once?(once=false,held.promise):{items:structuredClone(inventory)};
const old=dbAuditSearch();await tick();await auditChange('database','finance');await dbAuditSearch();
assert.equal(dbAudit.items.length,1);held.resolve({http:403,body:{code:'DB_ACCESS_REVOKED'}});await old;
assert.equal(dbAudit.database,'finance');assert.equal(dbAudit.items.length,1);auditNoLegacy();
''')

    def test_download_get_blob_cleanup_and_old_scope_abort(self):
        self.run_audit(r'''
await auditBoot();await dbAuditSearch();await dbAuditDetail(dbAudit.items[0].id);
const blobs=[],revoked=[];URL.createObjectURL=blob=>{blobs.push(blob);return 'blob:audit';};URL.revokeObjectURL=url=>revoked.push(url);
auditDownload=(p,o)=>{const data=auditEnvelope(p,o);data.context.sql='SECRET_CONTEXT_SQL';return data;};
await dbAuditDownload();assert.equal(blobs.length,1);assert.deepEqual(revoked,['blob:audit']);assert.equal(dbAudit.url,null);
const payload=await blobs[0].text();assert.doesNotMatch(payload,/SECRET|https:\/\/never/);assert.match(payload,/UNKNOWN/);
const request=calls.find(c=>c.path.includes('/download?'));assert.equal(request.options.headers['X-Shirokuma-Role'],'admin');assert.equal(request.options.method,undefined);
const held=deferred();auditDownload=()=>held.promise;const pending=dbAuditDownload();await tick();
await auditChange('database','finance');held.resolve(auditEnvelope('/api/v1/audit/events/x/download?scope=self&database_id=db',{headers:{'X-Shirokuma-Role':'admin'}}));await pending;
assert.equal(blobs.length,1);assert.equal(dbAudit.detail,null);auditNoLegacy();
''')

    def test_pawprints_outage_is_separate_from_durable_state(self):
        self.run_audit(r'''
await auditBoot(true);await auditChange('scope','platform');await dbAuditSearch();const rows=dbAudit.items,applied=dbAudit.applied;
assert.equal(calls.filter(c=>c.path==='/api/v1/audit/telemetry').length,0);
await dbAuditTelemetry();assert.equal(dbAudit.items,rows);assert.equal(dbAudit.applied,applied);assert.match(auditText($('db-audit-preview')),/unavailable/);
telemetry=()=>({availability:'available',limit:100,truncated:true,source:'pawprints',items:[{event_id:'telemetry-1',timestamp:'2026-10-29T00:00:00Z',agent_id:'sre-bear',action_type:'inspect <safe>',target_system:'trino',status:'SUCCEEDED',trace_id:'trace-1',duration_ms:0,sql:'SECRET'}]});
await dbAuditTelemetry();assert.match(auditText($('db-audit-preview')),/Global, unscoped.*limit 100.*Yes/);assert.doesNotMatch(auditText($('db-audit-preview')),/SECRET/);
assert.match(auditText($('db-audit-preview')),/sre-bear.*inspect <safe>.*trino.*SUCCEEDED.*trace-1.*0/);assert.doesNotMatch(auditText($('db-audit-preview')),/Not recorded/);
telemetry=()=>{throw Error('SECRET');};await dbAuditTelemetry();assert.equal(dbAudit.items,rows);assert.equal(dbAudit.applied,applied);auditNoLegacy();
''')

    def test_preview_search_status_filters_safe_dto_locally_and_localizes_controls(self):
        self.run_audit(r'''
await auditBoot(true);await auditChange('scope','platform');await dbAuditSearch();
const durableRows=dbAudit.items,applied=dbAudit.applied;
$('db-audit-search').value='independent durable draft';$('db-audit-search').oninput();
const first={event_id:'paw-first',timestamp:'2031-02-03T04:05:06Z',agent_id:'sre-bear',action_type:'inspect <safe>',target_system:'trino',status:'SUCCEEDED',trace_id:'trace-first',duration_ms:9874,
 sql:'ONLY_PRIVATE_SENTINEL',payload:{password:'ONLY_PRIVATE_SENTINEL'},model_id:'ONLY_PRIVATE_SENTINEL'};
const second={event_id:'paw-second',timestamp:'2032-03-04T05:06:07Z',agent_id:'query-doctor',action_type:'diagnose',target_system:'polaris',status:'FAILED',trace_id:'trace-second',duration_ms:8219};
telemetry=()=>({availability:'available',limit:100,truncated:false,items:[first,second]});
await $('db-audit-telemetry').onclick();const count=calls.length;
assert.equal($('db-audit-preview-search').tagName,'INPUT');assert.equal($('db-audit-preview-status').tagName,'SELECT');
assert.equal($('db-audit-preview-controls').hidden,false);assert.equal($('db-audit-preview-search').disabled,false);
assert.deepEqual($('db-audit-preview-status').options.map(o=>o.value),['','FAILED','SUCCEEDED']);
assert.match(auditText($('db-audit-preview')),/Showing 2 of 2/);
for(const value of ['paw-first','2031-02-03T04:05:06Z','sre-bear','inspect <safe>','trino','sUcCeEdEd','trace-first','9874']){
 $('db-audit-preview-search').value=value;$('db-audit-preview-search').oninput();
 assert.match(auditText($('db-audit-preview')),/Showing 1 of 2/);assert.match(auditText($('db-audit-preview-rows')),/paw-first/);assert.doesNotMatch(auditText($('db-audit-preview-rows')),/paw-second/);
}
$('db-audit-preview-search').value='';$('db-audit-preview-search').oninput();
$('db-audit-preview-status').value='FAILED';$('db-audit-preview-status').onchange();
assert.match(auditText($('db-audit-preview-rows')),/paw-second/);assert.doesNotMatch(auditText($('db-audit-preview-rows')),/paw-first/);
$('db-audit-preview-search').value='trino';$('db-audit-preview-search').oninput();assert.match(auditText($('db-audit-preview')),/Showing 0 of 2.*No fetched preview records/);
$('db-audit-preview-status').value='';$('db-audit-preview-status').onchange();
$('db-audit-preview-search').value='ONLY_PRIVATE_SENTINEL';$('db-audit-preview-search').oninput();
assert.match(auditText($('db-audit-preview')),/Showing 0 of 2/);assert.doesNotMatch(JSON.stringify(dbAudit.preview),/ONLY_PRIVATE_SENTINEL|sql|payload|model_id/);
assert.doesNotMatch(auditText($('db-audit-preview')),/ONLY_PRIVATE_SENTINEL/);
const search=$('db-audit-preview-search'),status=$('db-audit-preview-status'),all=status.options[0];
search.focus();search.setSelectionRange(2,5);
for(const lang of ['ja','en']){setLanguage(lang);assert.equal($('db-audit-preview-search'),search);assert.equal($('db-audit-preview-status'),status);assert.equal(document.activeElement,search);assert.equal(search.selectionStart,2);assert.equal(search.selectionEnd,5);
 assert.equal(auditText(all),lang==='ja'?'すべてのステータス':'All statuses');assert.match(auditText($('db-audit-preview-controls')),lang==='ja'?/取得済みプレビューを検索.*プレビューのステータス/:/Search fetched preview.*Preview status/);
 assert.match(auditText($('db-audit-preview')),lang==='ja'?/2 件中 0 件/:/Showing 0 of 2/);assert.match(auditText($('db-audit-preview')),lang==='ja'?/上限100件.*いいえ/:/limit 100.*No/);}
assert.equal(calls.length,count);assert.equal(dbAudit.items,durableRows);assert.equal(dbAudit.applied,applied);assert.equal($('db-audit-search').value,'independent durable draft');auditNoLegacy();
''')

    def test_preview_bounds_empty_and_explicit_refresh(self):
        self.run_audit(r'''
await auditBoot(true);await auditChange('scope','platform');
telemetry=()=>({availability:'available',limit:100,truncated:true,items:Array.from({length:102},(_,i)=>({event_id:'event-'+i,timestamp:null,agent_id:'bear',action_type:'inspect',target_system:'trino',status:i<100?'SUCCEEDED':'OUTSIDE_LIMIT',trace_id:'trace',duration_ms:0}))});
await dbAuditTelemetry();assert.equal(dbAudit.preview.items.length,100);assert.equal($('db-audit-preview-rows').children.length,100);
assert.match(auditText($('db-audit-preview')),/limit 100.*Yes.*Showing 100 of 100/);assert.equal($('db-audit-preview-status').options.some(o=>o.value==='OUTSIDE_LIMIT'),false);
$('db-audit-preview-search').value='OUTSIDE_LIMIT';$('db-audit-preview-search').oninput();assert.match(auditText($('db-audit-preview')),/Showing 0 of 100/);
const count=calls.filter(c=>c.path==='/api/v1/audit/telemetry').length;telemetry=()=>({availability:'available',limit:100,truncated:false,items:[]});
await $('db-audit-telemetry').onclick();assert.equal(calls.filter(c=>c.path==='/api/v1/audit/telemetry').length,count+1);
assert.match(auditText($('db-audit-preview')),/Global, unscoped.*Showing 0 of 0.*No fetched preview records/);assert.equal($('db-audit-preview-rows').children.length,0);auditNoLegacy();
''')

    def test_preview_purges_on_invalidation_and_ignores_late_refresh(self):
        for loss in ['header', 'scope', 'role', 'authority']:
            with self.subTest(loss=loss):
                self.run_audit("const loss=" + repr(loss) + ";" + r'''
await auditBoot(true);await auditChange('scope','platform');
const response={availability:'available',limit:100,truncated:false,items:[{event_id:'old-platform-event',timestamp:null,agent_id:'bear',action_type:'inspect',target_system:'trino',status:'SUCCEEDED',trace_id:'trace',duration_ms:0}]};
telemetry=()=>response;await dbAuditTelemetry();assert.equal(dbAudit.preview.items.length,1);
const held=deferred();telemetry=()=>held.promise;const pending=dbAuditTelemetry();await tick();
if(loss==='header'){$('role-selector').value='bi_viewer';const changed=$('role-selector').onchange();assert.equal(dbAudit.preview,null);await changed;}
else if(loss==='scope')await auditChange('scope','self');else if(loss==='role')await auditChange('role','bi_viewer');
else{session.platform_admin=false;await dbAuditRefreshAuthorization();}
assert.equal(dbAudit.preview,null);assert.equal($('db-audit-preview').children.length,0);assert.equal($('db-audit-preview-controls').hidden,true);
assert.equal($('db-audit-preview-search').disabled,true);assert.equal($('db-audit-preview-status').disabled,true);
held.resolve(response);await pending;assert.equal(dbAudit.preview,null);assert.doesNotMatch(auditText($('db-audit-panel')),/old-platform-event/);
$('db-audit-preview-search').oninput();assert.equal($('db-audit-preview').children.length,0);auditNoLegacy();
''')

    def test_periodic_authorization_preserves_nodes_focus_caret_and_detail_in_both_languages(self):
        self.run_audit(r'''
await auditBoot();await dbAuditSearch();await dbAuditDetail(dbAudit.items[0].id);
const row=$('db-audit-rows').children[0],detail=$('db-audit-detail').children[0],form=$('db-audit-form');
const controls=['scope','role','database','source'].map(key=>$('db-audit-'+key));
const options=controls.map(node=>[...node.options]);$('db-audit-source').value='query';
const values=controls.map(node=>node.value);
$('db-audit-search').value='draft text';$('db-audit-search').focus();$('db-audit-search').setSelectionRange(2,5);
const disclosure=document.createElement('details');disclosure.open=true;$('db-audit-detail').append(disclosure);
for(const lang of ['ja','en']){
 setLanguage(lang);
 controls.forEach((node,i)=>{assert.equal(node,$('db-audit-'+['scope','role','database','source'][i]));assert.equal(node.value,values[i]);node.options.forEach((option,j)=>assert.equal(option,options[i][j]));});
 const label=node=>auditText(node);
 assert.equal(label(options[0][1]),lang==='ja'?'自分の DB 記録':'My DB records');
 assert.equal(label(options[0][2]),lang==='ja'?'DB 管理情報':'DB administration');
 assert.equal(label(options[0][3]),lang==='ja'?'基盤管理記録':'Platform management');
 for(const [i,key] of [[0,'Scope'],[1,'Reference role'],[2,'Choose database'],[3,'All sources']])assert.equal(label(options[i][0]),t(key));
 assert.equal(label(options[3][1]),t('Saved SQL queries'));
 const before=auditRequests().length;await periodicRefresh();
 assert.equal(auditRequests().length,before);assert.equal(document.activeElement,$('db-audit-search'));assert.equal($('db-audit-search').selectionStart,2);assert.equal($('db-audit-search').selectionEnd,5);
 assert.equal($('db-audit-rows').children[0],row);assert.equal($('db-audit-detail').children[0],detail);assert.equal($('db-audit-form'),form);assert.equal(disclosure.open,true);
 assert.match(auditText($('db-audit-detail')),lang==='ja'?/未記録/:/Not recorded/);
}
auditNoLegacy();
''')

    def test_missing_db_and_backend_scope_eligibility_are_enforced(self):
        self.run_audit(r'''
await auditBoot();assert.equal($('db-audit-scope').options.find(o=>o.value==='platform').disabled,true);
await auditChange('database','');
const database=$('db-audit-database');
for(let i=0;i<2;i++){
 await dbAuditSearch();await periodicRefresh();
 assert.equal(auditRequests().length,0);assert.equal(dbAudit.items.length,0);
 assert.match($('db-audit-status').textContent,/Choose a currently available database/);
 assert.equal(database.options.find(o=>o.value==='db').disabled,false);
}
await auditChange('database','db');await dbAuditSearch();
assert.equal(auditRequests().length,1);assert.equal(dbAudit.items.length,1);
await dbAuditDetail(dbAudit.items[0].id);assert.ok(dbAudit.detail);
inventory=inventory.filter(d=>d.id!=='db');await dbAuditSearch();dbAuditValidate();
assert.equal(auditRequests().length,1);assert.equal(dbAudit.items.length,0);assert.equal(dbAudit.detail,null);
assert.equal(database.options.find(o=>o.value==='db').disabled,true);
assert.equal(database.options.find(o=>o.value==='finance').disabled,false);
await auditChange('database','finance');auditCapabilities=()=>({scopes:[],sources:['query']});await dbAuditSearch();assert.equal(auditRequests().length,1);
assert.equal(dbAudit.items.length,0);
auditCapabilities=null;inventory=[{...inventory[0],id:'replacement'}];await dbAuditOpen();
assert.equal($('db-audit-database').options.find(o=>o.value==='finance').disabled,true);
assert.equal($('db-audit-database').options.find(o=>o.value==='replacement').disabled,false);auditNoLegacy();
''')

    def test_download_revocation_during_final_validation_and_telemetry_auth_failure_purge(self):
        self.run_audit(r'''
await auditBoot(true);await auditChange('scope','platform');await dbAuditSearch();await dbAuditDetail(dbAudit.items[0].id);
const blobs=[];URL.createObjectURL=blob=>{blobs.push(blob);return 'blob:audit';};URL.revokeObjectURL=()=>{};
auditDownload=(p,o)=>{const data=auditEnvelope(p,o);session.platform_admin=false;return data;};
await dbAuditDownload();assert.equal(blobs.length,0);assert.equal(dbAudit.items.length,0);assert.equal(dbAudit.detail,null);
session.platform_admin=true;await dbReadInventory('admin');await dbAuditOpen();await auditChange('scope','platform');await dbAuditSearch();assert.equal(dbAudit.items.length,1);
auditCapabilities=()=>{throw Error('SECRET_AUTH_NETWORK');};await dbAuditTelemetry();
assert.equal(dbAudit.items.length,0);assert.doesNotMatch(auditText($('db-audit-panel')),/SECRET/);auditNoLegacy();
''')

    def test_draft_edited_during_search_remains_pending_and_own_role_is_independent(self):
        self.run_audit(r'''
await auditBoot();await auditChange('role','bi_viewer');assert.equal(dbCurrent().selected_role,'admin');
const held=deferred();let response;
auditList=(p,o)=>{response=auditEnvelope(p,o);return held.promise;};
const pending=dbAuditSearch();await tick();$('db-audit-state').value='FAILED';$('db-audit-state').oninput();held.resolve(response);await pending;
assert.equal(dbAudit.applied.state,'');assert.match($('db-audit-status').textContent,/pending/);
assert.equal(auditRequests().at(-1).options.headers['X-Shirokuma-Role'],'bi_viewer');
await exploreFinance();assert.equal(dbAudit.database,'db');assert.equal(dbAudit.role,'bi_viewer');assert.equal(dbAudit.items.length,1);auditNoLegacy();
''')
    def test_source_presets_are_native_local_drafts_and_preserve_context(self):
        self.run_audit(r'''
const sources=['query','dataops','stage_run','job_run','wap_build','wap_audit','wap_publication','ai'];
auditCapabilities=()=>({scopes:['self','database'],sources});await auditBoot();await dbAuditSearch();await dbAuditDetail(dbAudit.items[0].id);
const bar=$('db-audit-presets'),buttons=bar.descendants().filter(n=>n.tagName==='BUTTON');
assert.deepEqual(buttons.map(b=>b.id),sources.map(s=>'db-audit-preset-'+s));
assert.equal(bar.getAttribute('role'),'group');assert.equal(bar.getAttribute('aria-describedby'),'db-audit-presets-help');
for(const [key,value] of Object.entries({from:'2026-10-28T14:30',to:'2026-10-29T14:30',state:'UNKNOWN',search:'keep text',limit:'2'})){$('db-audit-'+key).value=value;$('db-audit-'+key).oninput();}
$('sql').value='SELECT 42 AS unsaved';$('p35-wap-sql').value='SELECT 7 AS wap_draft';
const preserved=()=>JSON.stringify({audit:Object.fromEntries(Object.entries(dbAudit).filter(([k])=>k!=='draft')),draft:Object.fromEntries(Object.entries(dbAudit.draft).filter(([k])=>k!=='source')),fields:['from','to','state','search','limit'].map(k=>$('db-audit-'+k).value),worksheet:dbInput(dbCurrent()),explorer:dbExplorer,sql:$('sql').value,wap:$('p35-wap-sql').value,storage:[...storage],writes:[...writes],tab:activeTab});
const before=preserved(),rows=$('db-audit-rows').children[0],detail=$('db-audit-detail').children[0],proof=dbAudit.proof,count=calls.length;
let forbidden=0;dbAuditSearch=()=>{forbidden++;};dbAuditDetail=()=>{forbidden++;};dbAuditOpen=()=>{forbidden++;};dbAuditDownload=()=>{forbidden++;};activate=()=>{forbidden++;};download=()=>{forbidden++;};
for(const [i,button] of buttons.entries()){
 assert.equal(button.tagName,'BUTTON');assert.equal(button.type,'button');assert.equal(button.disabled,false);assert.equal(button.dataset.source,sources[i]);assert.equal(button.getAttribute('aria-describedby'),'db-audit-presets-help');
 button.focus();assert.equal(document.activeElement,button);button.click();assert.equal($('db-audit-source').value,sources[i]);assert.equal(dbAudit.draft.source,sources[i]);assert.equal(document.activeElement,$('db-audit-source'));
 assert.match($('db-audit-status').textContent,/pending/);assert.equal(preserved(),before);assert.equal(dbAudit.proof,proof);assert.equal($('db-audit-rows').children[0],rows);assert.equal($('db-audit-detail').children[0],detail);
}
assert.equal(forbidden,0);assert.equal(calls.length,count);auditNoLegacy();
''')

    def test_source_preset_during_search_keeps_original_request_and_pending_draft(self):
        self.run_audit(r'''
auditCapabilities=()=>({scopes:['self'],sources:['query','dataops']});await auditBoot();
$('db-audit-source').value='query';$('db-audit-source').oninput();const held=deferred();let reply;
auditList=(p,o)=>{reply=auditEnvelope(p,o,auditEvent('original'));return held.promise;};
const pending=dbAuditSearch();await tick();const count=calls.length;
$('db-audit-preset-dataops').click();assert.equal(calls.length,count);assert.equal($('db-audit-source').value,'dataops');
held.resolve(reply);await pending;
assert.equal(auditRequests().length,1);assert.equal(new URLSearchParams(auditRequests()[0].path.split('?')[1]).get('source'),'query');assert.equal(dbAudit.applied.source,'query');assert.equal(dbAudit.items[0].id,'original');
assert.equal($('db-audit-source').value,'dataops');assert.equal(dbAudit.draft.source,'dataops');assert.match($('db-audit-status').textContent,/pending/);auditNoLegacy();
''')

    def test_source_preset_rechecks_offered_options_capability_and_authorization(self):
        self.run_audit(r'''
auditCapabilities=()=>({scopes:['self','database'],sources:['query','dataops']});await auditBoot();
const button=$('db-audit-preset-query'),source=$('db-audit-source');
const denied=()=>{const count=calls.length,value=source.value,draft=JSON.stringify(dbAudit.draft);dbAuditPresets();assert.equal(button.disabled,true);button.click();button.onclick();assert.equal(source.value,value);assert.equal(JSON.stringify(dbAudit.draft),draft);assert.equal(calls.length,count);};
const proof=dbAudit.proof,cap=dbAudit.capabilities;
dbAudit.capabilities=null;denied();dbAudit.capabilities=cap;
cap.sources=['dataops'];denied();cap.sources=['query','dataops'];
let option=source.options.find(o=>o.value==='query');option.disabled=true;denied();option.disabled=false;
dbAuditSelectOptions('db-audit-source',[{value:'dataops',label:'dataops'}],'','All sources');denied();await dbAuditOpen();
dbAudit.role='bi_viewer';denied();dbAudit.role=proof.role;
dbAudit.scope='platform';denied();dbAudit.scope=proof.scope;
dbAudit.database='missing';denied();dbAudit.database=proof.database;
const item=dbInventories.get(dbAudit.role).items.find(d=>d.id===dbAudit.database),binding=item.binding_revision;
item.binding_revision++;denied();item.binding_revision=binding;
await dbAuditOpen();assert.equal(button.disabled,false);
auditCapabilities=()=>({scopes:['self'],sources:['dataops']});await dbAuditRefreshAuthorization();denied();
dbAuditPurge();denied();auditNoLegacy();
''')

    def test_all_twenty_sources_localize_in_place_and_unknowns_keep_wire_strings(self):
        self.run_audit(r'''
const expected={query:'Saved SQL queries',dataops:'DataOps operations',job_run:'Job runs',job_step:'Job steps',stage_run:'Stage runs',stage_scan:'Stage scans',mart_run:'Managed Mart runs',dbt:'Database dbt runs',schedule:'Saved schedules',schedule_tick:'Schedule ticks',alert:'Saved alerts',wap_model_version:'WAP model versions',wap_build:'WAP builds',wap_audit:'WAP audits',wap_publication:'WAP publications',database_creation:'Database creation records',database_request:'Database requests',logical_request:'Logical resource requests',management:'Management operations',ai:'Saved AI metadata'};
const sources=[...Object.keys(expected),'operation','future_source'];auditCapabilities=()=>({scopes:['self'],sources});await auditBoot();
const events=sources.map((source,i)=>auditEvent('source-'+i,{source,action:source==='ai'?'sql_proposal':'execute',state:source==='ai'?'SUCCEEDED':'UNKNOWN',links:[]}));
auditList=(p,o)=>({...auditEnvelope(p,o),items:events});await dbAuditSearch();
assert.equal(Object.keys(expected).length,20);assert.deepEqual($('db-audit-source').options.map(o=>o.value),['',...sources]);
for(const event of events){
 auditDetail=(p,o)=>auditEnvelope(p,o,event);await dbAuditDetail(event.id);
 const options=[...$('db-audit-source').options],row=$('db-audit-rows').children[0],detail=$('db-audit-detail').children[0];$('db-audit-source').value=event.source;$('db-audit-source').focus();
 for(const lang of ['ja','en']){
  const count=calls.length;
  setLanguage(lang);const label=expected[event.source]?t(expected[event.source]):event.source;
  assert.equal(options.find(o=>o.value===event.source).textContent,label);assert.ok(auditText(row).includes(label+' / '));assert.ok(auditText(detail).includes(label));
  if(lang==='ja'&&expected[event.source])assert.notEqual(label,expected[event.source]);
  assert.equal($('db-audit-rows').children[0],row);assert.equal($('db-audit-detail').children[0],detail);assert.equal(document.activeElement,$('db-audit-source'));assert.equal(calls.length,count);
  await dbAuditRefreshAuthorization();options.forEach((option,i)=>assert.equal($('db-audit-source').options[i],option));assert.equal($('db-audit-source').value,event.source);assert.equal($('db-audit-rows').children[0],row);assert.equal($('db-audit-detail').children[0],detail);
 }
 assert.equal(dbAudit.detail.source,event.source);assert.equal(dbAudit.items[Number(event.id.split('-')[1])].source,event.source);
}
assert.equal($('db-audit-presets').descendants().some(n=>n.dataset.source==='future_source'),false);auditNoLegacy();
''')

    def test_history_guide_crosswalk_is_text_only_and_coverage_is_separate(self):
        self.run_audit(r'''
await auditBoot();const guide=$('db-audit-guide'),count=calls.length;
assert.equal(guide.tagName,'DETAILS');assert.equal(guide.parentElement,$('db-audit-panel'));assert.equal(guide.children[0].tagName,'SUMMARY');
assert.equal(guide.descendants().filter(n=>n.tagName==='LI').length,4);assert.equal(guide.descendants().some(n=>['A','BUTTON','INPUT','SELECT'].includes(n.tagName)),false);
for(const lang of ['en','ja']){
 setLanguage(lang);const text=auditText(guide),panel=auditText($('db-audit-panel'));
 for(const term of ['query','lake.ingest','lake.export','catalog.clone','catalog.drop','bear.data_engineer','Worksheet','Ingestion','Stages','Manual WAP','Managed Marts','Jobs','Database dbt','QUEUED/RUNNING','50','100'])assert.ok(text.includes(term),term);
 if(lang==='en'){for(const term of ['Worksheet Recent Query History restores SQL/context only; it does not execute SQL or retrieve result rows.','ingestion/export records (stage_run)','bear.data_engineer → Saved AI metadata (ai)','data kept','no physical deletion or automatic Undrop','not arbitrary snapshot rollback','not a new DataOps write operation','not imported into DB audit','schedules and ticks'])assert.ok(text.includes(term),term);assert.match(panel,/not a full transition history/);assert.match(panel,/AI success does not prove a SQL data commit/);}
 if(lang==='ja'){for(const term of ['SQL とコンテキストだけを復元','SQL の実行や結果行の取得は行いません','取込・出力記録（stage_run）','保存済み AI メタデータ（ai）'])assert.ok(text.includes(term),term);}
 assert.doesNotMatch(text,/History → detail|SQL and results access|saved AI drafts|保存済み AI 下書き|履歴 → 詳細/);
 assert.doesNotMatch(text,/SECRET|\/api\/|SELECT |https?:/);assert.equal(calls.length,count);
}
auditNoLegacy();
''')

    def test_ai_fresh_capability_five_localized_kinds_and_wire_preserving_private_free_download(self):
        self.run_audit(r'''
await auditBoot();assert.equal($('db-audit-source').options.some(o=>o.value==='ai'),false);
auditCapabilities=()=>({scopes:['self','database'],sources:['query','operation','ai'],platform_admin:false});
assert.equal($('db-audit-source').options.some(o=>o.value==='ai'),false);await dbAuditOpen();
const initialOption=$('db-audit-source').options.find(o=>o.value==='ai');assert.ok(initialOption);assert.equal(initialOption.disabled,false);$('db-audit-source').value='ai';$('db-audit-state').value='SUCCEEDED';$('db-audit-search').value='opaque /?#';
const expected=[['sql_explanation','Saved SQL explanation'],['sql_plan_explanation','Saved SQL execution plan explanation'],['saved_plan_explanation','Saved-plan re-explanation'],['sql_proposal','Saved SQL proposal'],['catalog_proposal','Saved catalog description proposal']];
const fields=['id','source','source_id','action','actor','database_id','schema_id','warehouse_id','saved_role','state','created_at','updated_at','finished_at','revision','committed','has_cleanup_error','links'];
const blobs=[],revoked=[];URL.createObjectURL=b=>{blobs.push(b);return 'blob:ai-audit';};URL.revokeObjectURL=u=>revoked.push(u);const count=calls.length;
for(const [action,label] of expected){
 const event=auditEvent('opaque /?#',{source:'ai',action,state:'SUCCEEDED',links:[],saved_role:null,created_at:null,updated_at:null,finished_at:null,revision:null,committed:null,has_cleanup_error:null,request:'SECRET_REQUEST',generated_sql:'SECRET_GENERATED_SQL',text:'SECRET_AI_OUTPUT',plan:{sql:'SECRET_PLAN'},provider:'SECRET_PROVIDER',raw_error:'SECRET_ERROR',credentials:'SECRET_CREDENTIALS'});
 auditList=(p,o)=>auditEnvelope(p,o,event);auditDetail=(p,o)=>auditEnvelope(p,o,event);auditDownload=(p,o)=>({...auditEnvelope(p,o,event),context:{...auditContext(p,o.headers['X-Shirokuma-Role']),private:'SECRET_CONTEXT'},debug:'SECRET_ENVELOPE'});
 await dbAuditSearch();await dbAuditDetail(event.id);const option=$('db-audit-source').options.find(o=>o.value==='ai'),row=$('db-audit-rows').children[0],detail=$('db-audit-detail').children[0],requestCount=calls.length;
 for(const locale of ['ja','en']){setLanguage(locale);assert.equal($('db-audit-source').options.find(o=>o.value==='ai'),option);assert.equal(option.textContent,t('Saved AI metadata'));assert.equal($('db-audit-source').value,'ai');assert.equal($('db-audit-rows').children[0],row);assert.equal($('db-audit-detail').children[0],detail);assert.ok(auditText(row).includes(t('Saved AI metadata')+' / '+t(label)));assert.ok(auditText(detail).includes(t(label)));assert.ok(auditText(detail).includes(t('Not recorded')));assert.doesNotMatch(auditText($('db-audit-panel')),/SECRET|sql_explanation|sql_plan_explanation|saved_plan_explanation|sql_proposal|catalog_proposal/);if(locale==='ja'){assert.notEqual(t(label),label);assert.notEqual(t('Saved AI metadata'),'Saved AI metadata');}assert.equal(calls.length,requestCount);}
 assert.equal($('db-audit-detail').descendants().filter(n=>n.tagName==='BUTTON').length,1);await dbAuditDownload();
 const payload=await blobs.at(-1).text(),saved=JSON.parse(payload);assert.doesNotMatch(payload,/SECRET|request|generated_sql|provider|credentials|raw_error/);assert.deepEqual(Object.keys(saved.event),fields);assert.equal(saved.event.source,'ai');assert.equal(saved.event.action,action);assert.equal(saved.event.state,'SUCCEEDED');assert.deepEqual(saved.event.links,[]);for(const key of ['saved_role','created_at','updated_at','finished_at','revision','committed','has_cleanup_error'])assert.equal(saved.event[key],null);assert.equal(saved.event.id,event.id);assert.equal(dbAudit.url,null);
}
assert.equal(blobs.length,5);assert.equal(revoked.length,5);
for(const c of calls.slice(count)){assert.equal(c.options?.method,undefined);assert.equal(c.options?.body,undefined);assert.ok(['/api/session','/api/v1/databases','/api/v1/audit/capabilities'].includes(c.path)||c.path.startsWith('/api/v1/audit/events?')||c.path.startsWith('/api/v1/audit/events/'+encodeURIComponent('opaque /?#')));}
for(const c of auditRequests()){const params=new URLSearchParams(c.path.split('?')[1]);assert.equal(params.get('source'),'ai');assert.equal(params.get('state'),'SUCCEEDED');assert.equal(params.get('search'),'opaque /?#');}
setLanguage('en');assert.match(auditText($('db-audit-panel')),/AI records are latest saved metadata summaries only; AI success does not prove a SQL data commit/);setLanguage('ja');assert.match(auditText($('db-audit-panel')),/AI の成功は SQL によるデータのコミットを証明しません/);
auditCapabilities=()=>({scopes:['self'],sources:['query']});await dbAuditOpen();assert.notEqual($('db-audit-source').options.find(o=>o.value==='ai')?.disabled,false);auditNoLegacy();
''')

    def test_ai_closed_action_state_and_empty_link_framing_without_broadening_legacy_projection(self):
        self.run_audit(r'''
const base=auditEvent('ai:opaque',{source:'ai',action:'sql_explanation',state:'UNKNOWN',links:[],committed:null});
for(const action of ['sql_explanation','sql_plan_explanation','saved_plan_explanation','sql_proposal','catalog_proposal'])for(const state of ['QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELED','UNKNOWN']){const clean=dbAuditEvent({...base,action,state});assert.equal(clean.source,'ai');assert.equal(clean.action,action);assert.equal(clean.state,state);assert.deepEqual(clean.links,[]);assert.equal(clean.committed,null);assert.equal(Object.keys(clean).length,17);}
for(const action of ['execute','SQL_EXPLANATION','sql_explanation_extra','toString','__proto__','',null,{}, {toString:()=> 'sql_explanation'}])assert.throws(()=>dbAuditEvent({...base,action}));
for(const state of ['FINISHED','SUCCESS','succeeded','SUCCEEDED_WITH_WARNINGS','',null,1,{}])assert.throws(()=>dbAuditEvent({...base,state}));
for(const links of [undefined,null,{},'[]',[{}],[{relation:'ai',source:'ai',source_id:'content',event_id:'content',url:'/api/v1/bears/explanations/content'}]])assert.throws(()=>dbAuditEvent({...base,links}));
const legacy=dbAuditEvent(auditEvent());assert.equal(legacy.action,'execute');assert.equal(legacy.links.length,2);assert.equal(legacy.links[0].event_id,'query:related');assert.equal(legacy.sql,undefined);assert.equal(legacy.links[0].url,undefined);
await auditBoot();const blobs=[];URL.createObjectURL=b=>{blobs.push(b);return 'blob:invalid';};URL.revokeObjectURL=()=>{};
for(const bad of [{...base,action:'execute'},{...base,state:'FINISHED'},{...base,links:[{}]}]){auditList=(p,o)=>auditEnvelope(p,o,bad);await dbAuditSearch();assert.equal(dbAudit.items.length,0);assert.equal(dbAudit.applied,null);assert.equal($('db-audit-status').role,'alert');assert.equal($('db-audit-rows').children.length,0);}
auditList=(p,o)=>auditEnvelope(p,o,base);await dbAuditSearch();auditDetail=(p,o)=>auditEnvelope(p,o,{...base,state:'FINISHED'});await dbAuditDetail(base.id);assert.equal(dbAudit.detail,null);
await dbAuditSearch();auditDetail=(p,o)=>auditEnvelope(p,o,base);await dbAuditDetail(base.id);auditDownload=(p,o)=>auditEnvelope(p,o,{...base,links:[{}]});await dbAuditDownload();assert.equal(blobs.length,0);assert.equal(dbAudit.detail,null);auditNoLegacy();
''')

    def test_ai_signed_cursor_pages_and_stale_denials_keep_current_metadata(self):
        self.run_audit(r'''
await auditBoot();auditCapabilities=()=>({scopes:['self'],sources:['ai']});await dbAuditOpen();$('db-audit-source').value='ai';
const event=id=>auditEvent(id,{source:'ai',action:'catalog_proposal',state:'UNKNOWN',links:[],committed:null});const first=auditCursor(),next=auditCursor(['ai:second']);
auditList=(p,o)=>{const cursor=new URLSearchParams(p.split('?')[1]).get('cursor');return {...auditEnvelope(p,o,event(cursor===next?'ai:second':'ai:first')),page_cursor:cursor||first,next_cursor:cursor===next?null:next};};
await dbAuditSearch();await dbAuditSearch('next');assert.deepEqual(dbAudit.cursors,[first]);assert.equal(dbAudit.items[0].id,'ai:second');await dbAuditSearch('previous');assert.equal(dbAudit.items[0].id,'ai:first');assert.deepEqual(dbAudit.cursors,[]);assert.equal(new URLSearchParams(auditRequests().at(-1).path.split('?')[1]).get('cursor'),first);
const old=deferred();auditList=()=>old.promise;const pending=dbAuditSearch();await tick();auditList=(p,o)=>auditEnvelope(p,o,event('ai:newer'));await dbAuditSearch();const items=dbAudit.items;
old.resolve({http:403,body:{code:'DB_ACCESS_REVOKED',message:'SECRET'}});await pending;assert.equal(dbAudit.items,items);assert.equal(dbAudit.items[0].id,'ai:newer');assert.doesNotMatch(auditText($('db-audit-panel')),/SECRET/);auditNoLegacy();
''')

    def test_ai_download_final_authorization_loss_blocks_blob_and_old_context_detail(self):
        self.run_audit(r'''
await auditBoot(true);await auditChange('scope','platform');const event=auditEvent('ai:opaque',{source:'ai',action:'sql_proposal',state:'SUCCEEDED',links:[],committed:null});auditList=(p,o)=>auditEnvelope(p,o,event);auditDetail=(p,o)=>auditEnvelope(p,o,event);await dbAuditSearch();await dbAuditDetail(event.id);
const blobs=[];URL.createObjectURL=b=>{blobs.push(b);return 'blob:ai';};URL.revokeObjectURL=()=>{};auditDownload=(p,o)=>{const reply=auditEnvelope(p,o,event);session.platform_admin=false;return reply;};await dbAuditDownload();assert.equal(blobs.length,0);assert.equal(dbAudit.detail,null);assert.equal(dbAudit.items.length,0);
session.platform_admin=true;await dbReadInventory('admin');await dbAuditOpen();await auditChange('scope','self');await dbAuditSearch();const old=deferred();let reply;auditDetail=(p,o)=>{reply=auditEnvelope(p,o,event);return old.promise;};const pending=dbAuditDetail(event.id);await tick();await auditChange('database','finance');old.resolve(reply);await pending;assert.equal(dbAudit.detail,null);assert.equal($('db-audit-detail').children.length,0);auditNoLegacy();
''')

    def test_ai_reads_preserve_same_auth_bears_platform_sql_drafts_unknown_and_runtime_receipt(self):
        import test_workbench_bears_database_ui as bears_ui
        import test_workbench_sre_policy_database_ui as observations_ui
        import test_workbench_platform_observations_database_ui as platform_ui
        import test_workbench_warehouse_runtime_ui as runtime_ui

        self.run_audit(bears_ui.COMMON + observations_ui.OBSERVATIONS + platform_ui.PLATFORM + runtime_ui.SETUP.replace('const posts=', 'const runtimePosts=') + r'''
const runtimeFixtureRoute=route;await auditBoot(true);const auditFixtureRoute=route;
await readyObservation('sre');await readyObservation('policy','admin','finance','finance-marts');await fetchObservation('sre');await fetchObservation('policy');const sre=dbObservations.sre.record,policy=dbObservations.policy.record;
dbPlatform.record=dbPlatformRecord(platformReply());dbPlatformSync();const platform=dbPlatform.record;
route=async()=>envelope(dbRuns.get(worksheetId),{state:'UNKNOWN'});await dbRunQuery();assert.equal(dbRuns.get(worksheetId).state,'UNKNOWN');editSQL('SELECT kept SQL');editDoctor('SELECT kept Doctor');$('copilot-request').value='kept Copilot';$('p35-wap-sql').value='SELECT kept WAP';
route=runtimeFixtureRoute;await refreshStatus();$('runtime-minutes').value='47';$('runtime-minutes').oninput();actionRoute=async()=>{throw Error('lost ACK');};await dbRuntimeAct('wake');assert.ok(dbRuntime.intent);const receipt=dbRuntime.intent;
route=auditFixtureRoute;await dbAuditOpen();const event=auditEvent('ai:metadata',{source:'ai',action:'saved_plan_explanation',state:'UNKNOWN',links:[],committed:null});auditList=(p,o)=>auditEnvelope(p,o,event);auditDetail=(p,o)=>auditEnvelope(p,o,event);auditDownload=(p,o)=>auditEnvelope(p,o,event);
URL.createObjectURL=()=> 'blob:ai';URL.revokeObjectURL=()=>{};const before=otherState(),runtimeBefore=JSON.stringify(dbRuntime),count=calls.length;
await dbAuditSearch();await dbAuditDetail(event.id);await dbAuditDownload();const presetCalls=calls.length;$('db-audit-preset-query').click();assert.equal(calls.length,presetCalls);assert.equal(otherState(),before);assert.equal(JSON.stringify(dbRuntime),runtimeBefore);assert.equal(dbRuntime.intent,receipt);assert.equal(dbObservations.sre.record,sre);assert.equal(dbObservations.policy.record,policy);assert.equal(dbPlatform.record,platform);assert.equal(dbRuns.get(worksheetId).state,'UNKNOWN');assert.equal($('sql').value,'SELECT kept SQL');assert.equal($('bear-query-sql').value,'SELECT kept Doctor');assert.equal($('copilot-request').value,'kept Copilot');assert.equal($('p35-wap-sql').value,'SELECT kept WAP');
assert.ok(calls.slice(count).every(c=>['/api/session','/api/v1/databases','/api/v1/audit/capabilities'].includes(c.path)||c.path.startsWith('/api/v1/audit/events')));assert.ok(calls.slice(count).every(c=>!c.options?.method&&!c.options?.body));
''')


if __name__ == '__main__':
    unittest.main()
