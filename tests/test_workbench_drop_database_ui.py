"""Execute shipped removal UI through the actual moved Details mount."""
import unittest
import json
import test_workbench_database_ui as database_ui
from test_workbench_dataops_ui import SETUP as DATAOPS_SETUP
from test_workbench_lineage_database_ui import catalog_markup

SETUP = DATAOPS_SETUP + 'const removalMarkup='+json.dumps(catalog_markup.root)+r''';
function mountRemovalMarkup(spec){
 const a=spec.attrs,n=a['data-inspect']?inspectorTabs.find(b=>b.dataset.inspect===a['data-inspect']):a.id?$(a.id):document.createElement(spec.tag);
 n.tagName=spec.tag.toUpperCase();
 for(const [k,v] of Object.entries(a)){
  if(k==='class')n.className=v;else if(k==='hidden')n.hidden=true;
  else if(k==='style')n.style={cssText:v,display:/display:\s*none/.test(v)?'none':''};
  else if(k.startsWith('data-'))n.dataset[k.slice(5)]=v;else n.setAttribute(k,v);
 }
 n.replaceChildren(...spec.children.map(mountRemovalMarkup));return n;
}
mountRemovalMarkup(removalMarkup);
function removalReachable(node){let warehouse=false;for(let p=node;p;p=p.parentElement){assert.equal(p.hidden,false,p.id||p.tagName);assert.notEqual(p.style?.display,'none',p.id||p.tagName);if(p===$('warehouse'))warehouse=true;}assert.equal(warehouse,true);}
''' + r'''
for(const s of schemas)s.purpose=s.sql_name;
const removalText=id=>[$(id).textContent,...$(id).descendants().map(n=>n.textContent)].join(' ');
const removalReply=(context,extra={})=>accepted(context,{id:'drop-op',state:'FINISHED',committed:true,acknowledged:true,phase:'ACKNOWLEDGED',context:structuredClone(context),source:ref('orders'),label:'orders',mode:dbDropMode,head:'0',queries:[],...extra});
async function dropReady(platform=false){
 if(platform){session.platform_admin=true;inventory[0].grant_revision=null;}
 await boot();
 Object.assign(dbExplorer,{database_id:'db',schema_id:'raw',binding_revision:1,generation_id:'gen',grant_revision:inventory[0].grant_revision,invalid:false,schemas:structuredClone(schemas),schemaScope:dbSchemaKey(currentRole,'db')});
 dbObject=stable('orders');activate($('tab-catalog'));dbExplorerControls();
 route=async(path,options)=>{
  const body=options?.body?JSON.parse(options.body):null;
  const context=body?.context||JSON.parse(new URL('http://localhost'+path).searchParams.get('context'));
  submissions.push({path,body,headers:options?.headers});
  if(path.startsWith('/api/v1/table-drops/preview'))return {context,preview:{label:'orders',source:ref('orders'),mode:dbDropMode,scope:'ALL_SNAPSHOTS',head:'0',preview_fingerprint:'a'.repeat(64),confirmation_name:'orders'}};
  return removalReply(context);
 };
 assert.equal($('drop-table-btn').disabled,false);
 await $('drop-table-btn').onclick();
 assert.equal($('db-drop-submit').disabled,true);
 assert.ok(dbDrop.preview);
}
function dropConfirm(name='orders'){$('db-drop-name').value=name;$('db-drop-name').oninput();$('db-drop-ack').checked=true;$('db-drop-ack').onchange();}
'''


class DropUI(unittest.TestCase):
    def javascript(self, body):
        database_ui.DatabaseUI.javascript(self, SETUP+'\n'+body)

    def test_preview_failure_keeps_visible_guidance_outside_operation_details(self):
        self.javascript(r'''
await dropReady();const posts=()=>submissions.filter(r=>r.body).length,before=posts();
route=async()=>{throw Error('preview transport failed');};await dbDropRefresh();
assert.equal(dbDrop.preview,null);assert.equal($('db-drop-submit').disabled,true);
assert.equal($('db-drop-notice').parentElement,$('db-drop-panel'));
assert.match($('db-drop-notice').textContent,/preview transport failed.*Refresh the preview/);
assert.ok(!$('db-drop-operation-details').open);assert.equal(posts(),before);
''')

    def test_collapsed_identification_visible_decisions_and_original_receipt_identity(self):
        self.javascript(r'''
await dropReady();
const visibleText=node=>[node.textContent,...node.children.filter(c=>node.tagName!=='DETAILS'||node.open||c.tagName==='SUMMARY').map(visibleText)].join(' ');
const instruction='Before removing this registration, pause other applications and tools that could create or replace a TABLE with the same name. Catalog removal cannot atomically check the UUID or current snapshot.';
const impact='Removes the whole TABLE registration, including all snapshots. Data files and metadata files are retained. There is no automatic Undrop. VIEW, SQL and Job references are not rewritten. Dependency completeness is not established.';
const source=structuredClone(dbDrop.source),fingerprint=dbDrop.preview.preview_fingerprint;
for(const locale of ['ja','en']){
 $('db-drop-identification').open=false;setLanguage(locale);dbDropControls();
 const identity=$('db-drop-identification'),text=visibleText($('db-drop-panel'));
 assert.equal(identity.tagName,'DETAILS');assert.equal(identity.open,false);assert.equal(identity.children[0].tagName,'SUMMARY');assert.equal(identity.children[0].textContent,locale==='ja'?'識別情報':'Identification details');
 for(const expected of ['sales / raw / orders',t('Whole TABLE / all snapshots'),t('Snapshot'),': 0',t('Removes the whole TABLE registration and all snapshots. Data and metadata files are kept. No automatic Undrop.'),t(instruction)])assert.ok(text.includes(expected),expected);
 assert.equal(text.includes(fingerprint),false);assert.equal(text.includes(JSON.stringify(source)),false);assert.equal(text.includes('"warehouse_id"'),false);
 assert.ok(!visibleText($('db-drop-panel')).includes(t(impact)));$('db-drop-more').open=true;assert.ok(visibleText($('db-drop-panel')).includes(t(impact)));$('db-drop-more').open=false;
 identity.open=true;dbDropControls();assert.equal($('db-drop-identification').open,true);assert.ok(visibleText($('db-drop-identification')).includes(fingerprint));assert.ok(visibleText(identity).includes(JSON.stringify(source)));assert.ok(visibleText(identity).includes('"warehouse_id"'));
 assert.equal(submissions.length,1);
}
dropConfirm(' orders');assert.equal($('db-drop-submit').disabled,true);dropConfirm();
const controls=$('db-drop-panel').descendants().filter(n=>['INPUT','SELECT'].includes(n.tagName));
assert.equal(controls.filter(n=>n.type==='checkbox').length,1);
await dbDropRun();assert.equal(dbObject,null);assert.ok(dbDrop.op.operation);
assert.deepEqual(Object.keys(submissions[1].body).sort(),['confirm_name','context','expected_preview_fingerprint','mode','source']);
const receipt=$('db-drop-receipt-identification');assert.equal(receipt.open,false);
assert.ok(visibleText($('db-drop-status')).includes('sales / raw / orders'));
assert.equal(visibleText($('db-drop-status')).includes(JSON.stringify(source)),false);
receipt.open=true;assert.ok(visibleText(receipt).includes(t('Original source Ref')));assert.ok(visibleText(receipt).includes(JSON.stringify(source)));
dbObject={...stable('orders'),ref:{...source,object_id:'replacement',incarnation:2}};dbSelectionGeneration++;dbDropControls();
assert.equal(dbDrop.op.operation,null);assert.equal(removalText('db-drop-status').includes(JSON.stringify(source)),false);
''')

    def test_actual_details_mount_header_inline_keyboard_localization_and_contract(self):
        self.javascript(r'''
await dropReady();
removalReachable($('drop-table-btn'));removalReachable($('db-drop-panel'));
assert.ok($('warehouse').descendants().includes($('database-details')));
assert.ok($('database-details').descendants().includes($('drop-table-btn')));
assert.ok($('database-details').descendants().includes($('db-drop-panel')));
assert.equal($('catalog').descendants().includes($('drop-table-btn')),false);
assert.equal($('db-drop-name'),document.activeElement);
assert.equal($('db-drop-panel').hidden,false);
for(const locale of ['ja','en']){
 setLanguage(locale);dbDropControls();assert.equal($('drop-table-btn').textContent,t('Remove from catalog (keep data)'));
 assert.ok(removalText('db-drop-panel').includes(t('Whole TABLE / all snapshots')));
 assert.ok(removalText('db-drop-panel').includes(t('Removes the whole TABLE registration, including all snapshots. Data files and metadata files are retained. There is no automatic Undrop. VIEW, SQL and Job references are not rewritten. Dependency completeness is not established.')));
}
assert.equal($('db-drop-panel').descendants().some(n=>n.tagName==='SELECT'),false);
assert.equal(submissions.length,1);assert.equal(submissions[0].body,null);
dropConfirm('Orders');assert.equal($('db-drop-submit').disabled,true);
dropConfirm();assert.equal($('db-drop-submit').disabled,false);assert.equal(submissions.length,1);
const before=JSON.stringify(worksheets),sql=$('sql').value,ingest=$('p35-ingest-name').value,wap=$('p35-wap-sql').value;
await $('db-drop-submit').onclick();assert.equal(submissions.length,2);
assert.equal(submissions[1].path,'/api/v1/table-drops');
assert.deepEqual(Object.keys(submissions[1].body).sort(),['confirm_name','context','expected_preview_fingerprint','mode','source']);
assert.equal(submissions[1].body.mode,dbDropMode);assert.equal(submissions[1].body.context.schema_id,'raw');
assert.equal(JSON.stringify(worksheets),before);assert.equal($('sql').value,sql);assert.equal($('p35-ingest-name').value,ingest);assert.equal($('p35-wap-sql').value,wap);
assert.equal(dbDrop.op.state,'FINISHED');assert.ok(dbDrop.op.operation);
assert.equal(JSON.stringify(JSON.parse(storage.get(dbDropKey))).includes('acknowledged'),false);
''')

    def test_precise_unavailable_roles_view_and_scope(self):
        for change in ("currentRole='data_engineer'", "currentRole='bi_viewer'", "dbObject.kind='VIEW'", "dbExplorer.schemas[1].purpose='RAW'", "dbExplorer.schemas[1].capabilities.write=false", "dbObject.ref.schema_id='schema'", "dbDatabases[0].grant_revision=null"):
            self.javascript(r'''
await dropReady();const before=submissions.length;
''' + change + r''';dbDropControls();
assert.equal($('drop-table-btn').disabled,true);assert.equal($('db-drop-submit').disabled,true);
assert.ok($('drop-table-btn').title.length);await dbDropRun();assert.equal(submissions.length,before);
''')

    def test_preview_context_source_head_nav_auth_revision_invalidation(self):
        for change in ("dbObject=stable('other')", "dbObject.head='new'", "dbSelectionGeneration++", "dbObjectEpoch++", "dbAuthorizationVersion++", "dbRoleAuthorizationVersions.set('admin',dbRoleVersion('admin')+1)", "dbExplorer.binding_revision++", "dbWorkspacePane='worksheet'", "activeTab='jobs'"):
            self.javascript(r'''
await dropReady();dropConfirm();assert.equal($('db-drop-submit').disabled,false);
''' + change + r''';dbDropControls();
assert.equal(dbDrop.preview,null);assert.equal($('db-drop-name').value,'');assert.equal($('db-drop-ack').checked,false);
assert.equal($('db-drop-submit').disabled,true);assert.equal(submissions.length,1);
''')

    def test_preview_stale_reply_and_source_aba_cannot_restore_confirmation(self):
        self.javascript(r'''
await dropReady();dropConfirm();const wait=deferred(),original=route;route=async()=>wait.promise;
const task=dbDropRefresh();await tick();dbObject=stable('other');dbDropControls();dbObject=stable('orders');dbSelectionGeneration++;dbDropControls();
wait.resolve({context:dbDropContext(),preview:{label:'orders',source:ref('orders'),mode:dbDropMode,scope:'ALL_SNAPSHOTS',head:'0',preview_fingerprint:'b'.repeat(64),confirmation_name:'orders'}});await task;
assert.equal(dbDrop.preview,null);assert.equal($('db-drop-submit').disabled,true);
''')

    def test_unknown_same_key_reload_no_source_recovery_and_manual_observe(self):
        self.javascript(r'''
await dropReady();dropConfirm();route=async(path,options)=>{submissions.push({path,body:JSON.parse(options.body),headers:options.headers});throw Error('lost');};
await dbDropRun();const op=dbDrop.op,key=op.key,body=JSON.stringify(op.payload);
assert.equal(op.ambiguous,true);assert.equal($('db-drop-new').disabled,true);assert.equal($('db-drop-cancel').disabled,true);
dbObject=null;dbDrop.owner=null;dbDrop.handles.clear();dbDropControls();
assert.equal(dbDrop.op.key,key);assert.equal(JSON.stringify(dbDrop.op.payload),body);assert.equal($('db-drop-observe').disabled,false);
route=async(path,options)=>{assert.equal(options?.method,undefined);assert.equal(options?.body,undefined);submissions.push({path,body:null,headers:options.headers});return removalReply(dbDrop.op.context);};
await dbDropRun(true);assert.equal(submissions.length,3);
const lookup=new URL('http://localhost'+submissions[2].path);assert.equal(lookup.pathname,'/api/v1/table-drops/by-key');assert.equal(lookup.searchParams.get('key'),key);
assert.deepEqual(JSON.parse(lookup.searchParams.get('context')),dbDrop.op.context);assert.equal(submissions[2].headers['Idempotency-Key'],undefined);assert.equal(submissions[2].body,null);
assert.equal(JSON.stringify(dbDrop.op.payload),body);
assert.equal(dbDrop.op.state,'FINISHED');assert.equal(dbDrop.op.ambiguous,false);assert.equal($('db-drop-new').disabled,false);
await dbDropRun(true);assert.ok(submissions.at(-1).path.includes('/dataops/drop-op?'));
''')

    def test_id_null_observe_is_lookup_only_missing_stays_held_until_original_arrives(self):
        self.javascript(r'''
await dropReady(true);dropConfirm();let posts=0;
route=async(path,options)=>{assert.equal(path,'/api/v1/table-drops');assert.equal(options.method,'POST');posts++;throw Error('lost before admission');};
await dbDropRun();const op=dbDrop.op,owner=dbPrincipal,key=op.key,body=JSON.stringify(op.payload),context=structuredClone(op.context);
assert.equal(posts,1);assert.equal(op.id,null);assert.equal(op.ambiguous,true);
dbClearExplorer();dbDrop.owner=null;dbDrop.handles.clear();dbDropControls();activate($('tab-catalog'));const restored=dbDrop.op;
let lookups=0,record=null;
route=async(path,options)=>{
 assert.equal(options?.method,undefined);assert.equal(options?.body,undefined);assert.equal(options.headers['Idempotency-Key'],undefined);
 const url=new URL('http://localhost'+path);assert.equal(url.pathname,'/api/v1/table-drops/by-key');assert.equal(url.searchParams.get('key'),key);assert.deepEqual(JSON.parse(url.searchParams.get('context')),context);
 lookups++;return record||{http:404,body:{code:'OPERATION_NOT_FOUND',details:{}}};
};
await dbDropRun(true);await dbDropRun(true);
assert.equal(lookups,2);assert.equal(posts,1);assert.equal(restored.id,null);assert.equal(restored.state,'UNKNOWN');assert.equal(restored.ambiguous,true);
assert.equal($('db-drop-new').disabled,true);assert.equal($('db-drop-cancel').disabled,true);assert.equal(JSON.stringify(restored.payload),body);assert.equal(restored.key,key);assert.equal(restored.principal,owner);
// The already submitted original can be admitted later; observing only finds it.
record=removalReply(context,{state:'UNKNOWN',committed:false,acknowledged:false,phase:'SENT',queries:[]});
await dbDropRun(true);assert.equal(posts,1);assert.equal(restored.id,'drop-op');assert.equal(restored.state,'UNKNOWN');assert.equal($('db-drop-new').disabled,true);assert.equal($('db-drop-cancel').disabled,true);
route=async(path,options)=>{assert.equal(options?.method,undefined);assert.equal(options?.body,undefined);assert.equal(new URL('http://localhost'+path).pathname,'/api/v1/dataops/drop-op');return record;};
await dbDropRun(true);assert.equal(posts,1);assert.equal(restored.ambiguous,true);assert.equal(JSON.stringify(restored.payload),body);
''')

    def test_unknown_and_sent_cancel_unavailable_minimal_observation_releases_only_terminal(self):
        self.javascript(r'''
await dropReady();dropConfirm();route=async()=>removalReply(dbDrop.context,{state:'ACTIVE',committed:false,acknowledged:false,phase:'SENT'});
await dbDropRun();const op=dbDrop.op;
assert.equal($('db-drop-cancel').disabled,true);assert.equal($('db-drop-new').disabled,true);
route=async()=>accepted(op.context,{id:op.id,state:'UNKNOWN',phase:'SENT',queries:[]});await dbDropRun(true);
assert.equal(op.ambiguous,true);assert.equal($('db-drop-cancel').disabled,true);
route=async()=>accepted(op.context,{id:op.id,state:'FAILED',phase:'REJECTED',queries:[]});await dbDropRun(true);
assert.equal(op.ambiguous,false);assert.equal($('db-drop-new').disabled,false);
$('db-drop-new').onclick();assert.equal(dbDrop.op,null);assert.equal(dbDrop.preview,null);assert.equal($('db-drop-submit').disabled,true);
''')

    def test_current_auth_denial_before_submit_purges_preview_and_keeps_handle(self):
        self.javascript(r'''
await dropReady();dropConfirm();const count=submissions.length;
inventoryRoute=()=>{throw Object.assign(Error('denied'),{status:403,code:'DB_ACCESS_REVOKED'});};
await dbDropRun();assert.equal(submissions.length,count);assert.equal(dbDrop.preview,null);assert.equal(dbDrop.source,null);
assert.equal($('db-drop-name').value,'');assert.equal(removalText('db-drop-preview'),'');
assert.ok(dbDrop.op.key);assert.equal(dbDrop.op.operation,null);
''')

    def test_post_request_auth_failure_hides_committed_dto_preserves_original_handle(self):
        self.javascript(r'''
await dropReady();dropConfirm();const original=route;
route=async(path,options)=>{const r=await original(path,options);if(path==='/api/v1/table-drops')inventoryRoute=()=>{throw Object.assign(Error('revoked'),{status:403,code:'DB_ACCESS_REVOKED'});};return r;};
await dbDropRun();assert.equal(dbDrop.op.state,'FINISHED');assert.equal(dbDrop.op.operation,null);
assert.equal(dbDrop.preview,null);assert.equal(removalText('db-drop-preview'),'');
assert.ok(JSON.parse(storage.get(dbDropKey)).owners[dbPrincipal].id);
''')

    def test_late_owner_denial_never_purges_new_owner_draft(self):
        self.javascript(r'''
await dropReady();dropConfirm();const wait=deferred();route=async()=>wait.promise;
const task=dbDropRun();await tick();const op=dbDrop.op;
dbPrincipal='foreign';dbDropControls();dbDrop.notice='new owner draft';
wait.reject(Object.assign(Error('old denied'),{status:403,code:'DB_ACCESS_REVOKED'}));await task;
assert.equal(dbDrop.op,null);assert.equal(dbDrop.notice,'new owner draft');assert.equal(op.operation,null);
assert.equal($('db-drop-observe').disabled,true);
''')

    def test_foreign_owner_header_context_binding_grant_and_generation_guard_observation(self):
        for change in ("dbPrincipal='foreign'", "currentRole='data_engineer'", "dbInventories.get('admin').items[0].grant_revision++", "dbInventories.get('admin').items[0].binding_revision++", "dbInventories.get('admin').items[0].generation_id='other'"):
            self.javascript(r'''
await dropReady();dropConfirm();await dbDropRun();const op=dbDrop.op,count=submissions.length;
''' + change + r''';dbDropControls();
assert.equal($('db-drop-observe').disabled,true);await dbDropRun(true);assert.equal(submissions.length,count);assert.equal(op.operation,null);
''')

    def test_long_quoted_unicode_name_exact_mode_ack_and_scoped_wrapping(self):
        self.javascript(r'''
await dropReady();const name='大文字 A/"?%#'+'Long'.repeat(50);dbDrop.preview.label=name;dbDrop.preview.confirmation_name=name;
dropConfirm(name.toLowerCase());assert.equal($('db-drop-submit').disabled,true);
dropConfirm(name);assert.equal($('db-drop-submit').disabled,false);
$('db-drop-ack').checked=false;dbDropControls();assert.equal($('db-drop-submit').disabled,true);
''')
        self.assertIn('#db-drop-panel :is(p,div,label,button){overflow-wrap:anywhere;', database_ui.HTML)

    def test_verified_platform_bootstrap_null_context_and_source_gone_recovery(self):
        self.javascript(r'''
await dropReady(true);assert.equal(dbAdmin.platform,true);assert.equal(dbAdmin.installed,false);assert.equal(activeTab,'warehouse');
assert.equal(dbDrop.context.grant_revision,null);assert.equal($('drop-table-btn').disabled,false);
for(const lang of ['ja','en']){setLanguage(lang);dbDropControls();assert.equal($('drop-table-btn').title,'');}
dropConfirm();await dbDropRun();const op=dbDrop.op,body=JSON.stringify(op.payload),key=op.key;
assert.equal(submissions[1].body.context.grant_revision,null);assert.equal(dbObject,null);assert.ok(op.operation);assert.equal(op.ambiguous,false);
dbClearExplorer();dbDrop.owner=null;dbDrop.handles.clear();dbDropControls();activate($('tab-catalog'));
assert.equal(dbDrop.op.key,key);assert.equal(JSON.stringify(dbDrop.op.payload),body);assert.equal($('db-drop-observe').disabled,false);removalReachable($('db-drop-observe'));
await dbDropRun(true);assert.ok(dbDrop.op.operation);assert.ok(submissions.at(-1).path.includes('/dataops/drop-op?'));
assert.equal(JSON.parse(new URL('http://localhost'+submissions.at(-1).path).searchParams.get('context')).grant_revision,null);
dbPrincipal='foreign';dbDropControls();assert.equal(dbDrop.op,null);assert.equal($('db-drop-observe').disabled,true);assert.equal(removalText('db-drop-status').includes('orders'),false);
''')

    def test_null_context_requires_real_verified_platform_authority_and_header_admin(self):
        self.javascript(r'''
await dropReady(true);dropConfirm();
session.platform_admin=false;await dbReadInventory('admin');dbDropControls();
assert.equal(dbAdmin.platform,false);assert.equal(dbDrop.preview,null);assert.equal($('db-drop-name').value,'');assert.equal($('db-drop-ack').checked,false);
assert.equal($('drop-table-btn').disabled,true);assert.equal($('db-drop-submit').disabled,true);assert.equal(submissions.length,1);
assert.match($('drop-table-btn').title,/Admin database access/);
session.platform_admin=true;await dbReadInventory('admin');dbDropControls();assert.equal($('drop-table-btn').disabled,false);
currentRole='data_engineer';dbDropControls();assert.equal($('drop-table-btn').disabled,true);
currentRole='admin';dbMaximumRole='data_engineer';dbDropControls();assert.equal($('drop-table-btn').disabled,true);
''')

    def test_fresh_session_platform_loss_prevents_null_post_with_cached_inventory(self):
        self.javascript(r'''
await dropReady(true);dropConfirm();session.platform_admin=false;await dbDropRun();
assert.equal(submissions.length,1);assert.equal(dbAdmin.platform,false);assert.equal(dbDrop.preview,null);assert.equal(dbDrop.source,null);
assert.equal(dbDrop.op.state,'REJECTED');assert.equal(dbDrop.op.ambiguous,false);assert.equal($('drop-table-btn').disabled,true);
''')

    def test_platform_loss_and_aba_ignore_late_preview_and_clear_confirmation(self):
        for aba in (False,True):
            self.javascript(r'''
await dropReady(true);dropConfirm();const context=structuredClone(dbDrop.context),v=structuredClone(dbDrop.preview),epoch=dbAdmin.epoch;
const wait=deferred();route=async()=>wait.promise;const task=dbDropRefresh();await tick();
session.platform_admin=false;await dbReadInventory('admin');dbDropControls();
''' + ("session.platform_admin=true;await dbReadInventory('admin');dbDropControls();" if aba else '') + r'''
assert.ok(dbAdmin.epoch>epoch);wait.resolve({context,preview:v});await task;
assert.equal(dbDrop.preview,null);assert.equal($('db-drop-name').value,'');assert.equal($('db-drop-ack').checked,false);assert.equal($('db-drop-submit').disabled,true);
''')

    def test_platform_loss_after_ack_hides_protected_receipt_but_keeps_terminal_handle(self):
        self.javascript(r'''
await dropReady(true);dropConfirm();const original=route;
route=async(path,options)=>{const reply=await original(path,options);if(path==='/api/v1/table-drops')session.platform_admin=false;return reply;};
await dbDropRun();const op=dbDrop.op;
assert.equal(dbAdmin.platform,false);assert.equal(op.state,'FINISHED');assert.equal(op.ambiguous,false);assert.equal(op.operation,null);
assert.equal(dbDrop.preview,null);assert.equal($('db-drop-observe').disabled,true);assert.equal(removalText('db-drop-status').includes('sales / raw / orders'),false);
assert.equal(JSON.parse(storage.get(dbDropKey)).owners[dbPrincipal].payload.context.grant_revision,null);
''')

    def test_live_owner_aba_pending_handle_keeps_identity_and_releases(self):
        self.javascript(r'''
await dropReady();dropConfirm();const wait=deferred();route=async()=>wait.promise;
const owner=dbPrincipal,task=dbDropRun();await tick();const op=dbDrop.op,token=op.pending,key=op.key,body=JSON.stringify(op.payload);
dbPrincipal='foreign';dbDropControls();assert.equal(dbDrop.op,null);assert.equal(removalText('db-drop-status').includes('orders'),false);
dbPrincipal=owner;dbDropControls();assert.equal(dbDrop.op,op);assert.equal(op.pending,token);assert.equal($('db-drop-submit').disabled,true);
wait.resolve(removalReply(op.context));await task;
assert.equal(dbDrop.op,op);assert.equal(op.pending,null);assert.equal(dbDrop.busy,null);assert.equal(op.state,'FINISHED');
assert.equal(op.key,key);assert.equal(JSON.stringify(op.payload),body);assert.equal($('db-drop-observe').disabled,false);
assert.equal(op.operation,null);await dbDropRun(true);assert.ok(op.operation);
''')

    def test_obsolete_preview_does_not_clear_new_pending_token(self):
        self.javascript(r'''
await dropReady();const one=deferred(),two=deferred();let count=0;
const context=structuredClone(dbDrop.context),v=structuredClone(dbDrop.preview);
route=async()=>++count===1?one.promise:two.promise;
const first=dbDropRefresh();await tick();dbDropInvalidate();assert.equal(dbDrop.busy,null);
const second=dbDropRefresh();await tick();const token=dbDrop.busy;assert.ok(token);
one.resolve({context,preview:v});await first;assert.equal(dbDrop.busy,token);assert.equal(dbDrop.preview,null);
two.resolve({context,preview:v});await second;assert.equal(dbDrop.busy,null);assert.ok(dbDrop.preview);
''')

    def test_known_terminal_get_failures_never_create_mutation_uncertainty(self):
        for state in ('FINISHED','FAILED','CANCELED'):
            for status in (403,409,404):
                self.javascript(r'''
await dropReady();dropConfirm();route=async()=>removalReply(dbDrop.context,{state:''' + repr(state) + r''',committed:false,acknowledged:false});
await dbDropRun();const op=dbDrop.op,key=op.key,body=JSON.stringify(op.payload);
route=async()=>{throw Object.assign(Error('observe denied'),{status:''' + str(status) + r''',code:''' + repr({403:'SQL_PERMISSION_DENIED',409:'CONTEXT_MISMATCH',404:'NOT_FOUND'}[status]) + r'''});};
await dbDropRun(true);assert.equal(op.state,''' + repr(state) + r''');assert.equal(op.ambiguous,false);assert.equal(op.operation,null);
assert.equal($('db-drop-new').disabled,false);assert.equal(op.key,key);assert.equal(JSON.stringify(op.payload),body);
assert.equal(removalText('db-drop-status').includes(t('Outcome unknown. Observe with the saved key; do not submit again.')),false);
$('db-drop-new').onclick();assert.equal(dbDrop.op,null);assert.equal($('db-drop-submit').disabled,true);
''')

    def test_first_http400_releases_but_lost_post_remains_held(self):
        self.javascript(r'''
await dropReady();dropConfirm();route=async()=>({http:400,body:{code:'INVALID_INPUT',details:{}}});
await dbDropRun();assert.equal(dbDrop.op.state,'REJECTED');assert.equal(dbDrop.op.ambiguous,false);assert.equal($('db-drop-new').disabled,false);
$('db-drop-new').onclick();const context=dbDropContext();
route=async()=>({context,preview:{label:'orders',source:ref('orders'),mode:dbDropMode,scope:'ALL_SNAPSHOTS',head:null,preview_fingerprint:'a'.repeat(64),confirmation_name:'orders'}});
await dbDropRefresh();dropConfirm();route=async()=>{throw Error('lost');};await dbDropRun();
assert.equal(dbDrop.op.ambiguous,true);assert.equal($('db-drop-new').disabled,true);
''')

    def test_rejected_no_id_lookup404_preserves_definite_rejection_and_original_handle(self):
        for rejection in ('http400','never_dispatched'):
            self.javascript(r'''
await dropReady();dropConfirm();let posts=0,effects=0;
route=async(path,options)=>{assert.equal(path,'/api/v1/table-drops');assert.equal(options.method,'POST');posts++;return {http:400,body:{code:'INVALID_INPUT',details:{}}};};
''' + ("const verify=dbDropVerify;dbDropVerify=async()=>false;await dbDropRun();dbDropVerify=verify;assert.equal(posts,0);" if rejection=='never_dispatched' else "await dbDropRun();assert.equal(posts,1);") + r'''
const op=dbDrop.op,key=op.key,body=JSON.stringify(op.payload),owner=op.principal,initialPosts=posts;
assert.equal(op.id,null);assert.equal(op.state,'REJECTED');assert.equal(op.ambiguous,false);assert.equal($('db-drop-new').disabled,false);
let lookups=0;route=async(path,options)=>{
 if(options?.method==='POST'){posts++;effects++;throw Error('Observe must not mutate');}
 assert.equal(options?.method,undefined);assert.equal(options?.body,undefined);assert.equal(options.headers['Idempotency-Key'],undefined);
 const url=new URL('http://localhost'+path);assert.equal(url.pathname,'/api/v1/table-drops/by-key');assert.equal(url.searchParams.get('key'),key);assert.deepEqual(JSON.parse(url.searchParams.get('context')),op.context);
 lookups++;return {http:404,body:{code:'OPERATION_NOT_FOUND',details:{}}};
};
await dbDropRun(true);assert.equal(lookups,1);assert.equal(posts,initialPosts);assert.equal(effects,0);
assert.equal(dbDrop.op,op);assert.equal(op.id,null);assert.equal(op.state,'REJECTED');assert.equal(op.ambiguous,false);assert.equal($('db-drop-new').disabled,false);
assert.equal(op.key,key);assert.equal(op.principal,owner);assert.equal(JSON.stringify(op.payload),body);
assert.equal(JSON.parse(storage.get(dbDropKey)).owners[dbPrincipal].key,key);
''')

    def test_visible_recovery_after_ack_object_clear_refresh_and_persisted_reload(self):
        self.javascript(r'''
await dropReady();dropConfirm();const original=structuredClone(dbDrop.source),draft=JSON.stringify(worksheets),sql=$('sql').value;
const detailsKey=JSON.stringify([dbCatalogScope(),'orders',original,'columns',null]);dbDetails.set(detailsKey,{ref:original});dbObjects=[stable('orders'),stable('other')];
dbTreeObjects.set('test',{items:[stable('orders'),stable('other')],next_cursor:null});
assert.equal($('open-table-sql').disabled,false);assert.equal($('clone-table-btn').disabled,false);
await dbDropRun();const op=dbDrop.op,key=op.key,body=JSON.stringify(op.payload);
assert.equal(dbObject,null);assert.equal(dbDetails.has(detailsKey),false);assert.deepEqual(dbObjects.map(o=>o.id),['other']);
for(const id of ['open-table-sql','insert-table-sql','clone-table-btn','explorer-open-lineage'])assert.equal($(id).disabled,true,id);
for(const name of ['profile','lineage'])assert.equal(inspectorTabs.find(b=>b.dataset.inspect===name).disabled,true,name);
assert.ok(op.operation);assert.equal($('db-drop-observe').disabled,false);
assert.deepEqual(dbTreeObjects.get('test').items.map(o=>o.id),['other']);
removalReachable($('db-drop-panel'));removalReachable($('db-drop-observe'));
assert.ok(removalText('db-drop-status').includes(JSON.stringify(original)));assert.ok(removalText('db-drop-status').includes('sales / raw / orders'));
objects=[];await dbRefreshExplorer();dbClearExplorer();activate($('tab-catalog'));
assert.equal($('db-drop-panel').parentElement,$('db-table-manage'));assert.equal($('db-table-manage').open,false);assert.equal($('db-drop-recovery').hidden,false);$('db-drop-recovery').onclick();assert.equal($('db-drop-panel').open,true);removalReachable($('db-drop-observe'));
dbDrop.op=null;dbDrop.owner=null;dbDrop.handles.clear();dbDropControls();
assert.equal(dbObject,null);assert.equal(dbDrop.op.key,key);assert.equal(JSON.stringify(dbDrop.op.payload),body);
activate($('tab-warehouse'));activate($('tab-catalog'));removalReachable($('db-drop-observe'));
await dbDropRun(true);assert.ok(dbDrop.op.operation);assert.ok(submissions.at(-1).path.includes('/dataops/drop-op?'));
assert.equal(JSON.stringify(worksheets),draft);assert.equal($('sql').value,sql);
''')

    def test_late_ack_evicts_only_original_ref_preserves_recreated_selection_and_drafts(self):
        self.javascript(r'''
await dropReady();dropConfirm();const wait=deferred();route=async()=>wait.promise;
const original=stable('orders'),replacement={...stable('orders'),ref:{...ref('orders'),object_id:'replacement',incarnation:2},id:'replacement'};
const task=dbDropRun();await tick();const op=dbDrop.op;
dbObject=replacement;dbSelectionGeneration++;dbObjectEpoch++;dbObjects=[original,replacement,stable('other')];
dbTreeObjects.set('test',{items:[original,replacement],next_cursor:null});
const oldKey=JSON.stringify(['scope','orders',original.ref,'columns',null]),newKey=JSON.stringify(['scope','replacement',replacement.ref,'columns',null]);
dbDetails.set(oldKey,{ref:original.ref});dbDetails.set(newKey,{ref:replacement.ref});
$('p35-ingest-name').value='draft';$('p35-wap-sql').value='saved SQL';dbClone.notice='clone draft';
$('sql').value='SELECT preserved';$('bear-query-sql').value='AI draft';$('export-table-format').value='jsonl';$('db-clone-name').value='clone_target';
dbProfile.scope=dbProfileScope();dbProfile.context=structuredClone(op.context);dbProfile.source=replacement.ref;dbProfile.snapshots=['0'];dbProfile.snapshot='0';
wait.resolve(removalReply(op.context));await task;
assert.equal(dbObject,replacement);assert.deepEqual(dbObjects.map(o=>o.id),['replacement','other']);
for(const id of ['open-table-sql','insert-table-sql','clone-table-btn','explorer-open-lineage'])assert.equal($(id).disabled,false,id);
for(const name of ['profile','lineage'])assert.equal(inspectorTabs.find(b=>b.dataset.inspect===name).disabled,false,name);
assert.equal(dbDetails.has(oldKey),false);assert.equal(dbDetails.has(newKey),true);assert.equal(dbTreeObjects.get('test').items[0],replacement);
assert.equal($('p35-ingest-name').value,'draft');assert.equal($('p35-wap-sql').value,'saved SQL');assert.equal(dbClone.notice,'clone draft');
assert.equal($('sql').value,'SELECT preserved');assert.equal($('bear-query-sql').value,'AI draft');assert.equal($('export-table-format').value,'jsonl');assert.equal($('db-clone-name').value,'clone_target');assert.equal(dbProfile.snapshot,'0');
assert.equal(op.operation,null);assert.equal(op.state,'FINISHED');
''')


if __name__ == '__main__': unittest.main()
