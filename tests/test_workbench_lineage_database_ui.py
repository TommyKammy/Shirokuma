"""Execute saved-lineage actions in the mounted Database workspace."""
from html.parser import HTMLParser
import json
import re
import unittest
import test_workbench_database_ui as database_ui
from test_workbench_dataops_ui import SETUP as DATAOPS_SETUP

class CatalogMarkup(HTMLParser):
    """Keep the shipped Catalog ancestors and Inspector panes in the DOM adapter."""
    def __init__(self):
        super().__init__()
        self.root = None
        self.stack = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if not self.stack and attrs.get('id') != 'catalog':
            return
        node = {'tag': tag, 'attrs': attrs, 'children': []}
        if self.stack:
            self.stack[-1]['children'].append(node)
        else:
            self.root = node
        if tag not in {'input', 'br', 'hr', 'img', 'meta', 'link'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1]['tag'] == tag:
            self.stack.pop()


catalog_markup = CatalogMarkup()
catalog_markup.feed(database_ui.HTML)

SETUP = DATAOPS_SETUP + 'const catalogMarkup=' + json.dumps(catalog_markup.root) + r'''
// Mount the original markup before initializeDatabaseUI moves its Inspector.
function mountCatalog(spec){
 const attrs=spec.attrs;
 const node=attrs['data-inspect']?inspectorTabs.find(b=>b.dataset.inspect===attrs['data-inspect']):attrs.id?$(attrs.id):document.createElement(spec.tag);
 node.tagName=spec.tag.toUpperCase();
 for(const [key,value] of Object.entries(attrs)){
  if(key==='class')node.className=value;
  else if(key==='hidden')node.hidden=true;
  else if(key==='style')node.style={cssText:value,display:/display:\s*none/.test(value)?'none':''};
  else if(key.startsWith('data-'))node.dataset[key.slice(5)]=value;
  else node.setAttribute(key,value);
 }
 node.replaceChildren(...spec.children.map(mountCatalog));return node;
}
mountCatalog(catalogMarkup);
''' + r'''
const lineageText=id=>[$(id).textContent,...$(id).descendants().map(n=>n.textContent)].join(' ');
const lineageReply=()=>({focus:{label:'sales.raw.orders',kind:'TABLE',ref:ref('orders'),snapshot_id:'9007199254740993'},
 confirmed:[{category:'native_graph',temporal:'historical',source:{label:'sales.raw.source',kind:'VIEW',ref:ref('source'),view_version:1},target:{label:'sales.raw.orders',kind:'TABLE',ref:ref('orders'),snapshot_id:'9007199254740993'}}],
 declared:[{category:'declared_model',source:{label:'sales.raw.declared',kind:'TABLE',ref:ref('declared'),snapshot_id:'12'},target:{label:'sales.raw.orders',kind:'TABLE',ref:ref('orders'),snapshot_id:'9007199254740993'},model:{id:'model.arbitrary'}}],
 coverage:{scope:'visible_saved_records',limit:200,limited:true,empty_reason:null}});
async function lineageReady(){
 await boot();
 Object.assign(dbExplorer,{database_id:'db',schema_id:'raw',binding_revision:1,generation_id:'gen',grant_revision:4,invalid:false,schemas:structuredClone(schemas),schemaScope:dbSchemaKey(currentRole,'db')});
 dbObject=stable('orders');dbShowWorkspace('details');dbExplorerControls();
 route=async(path,options)=>{submissions.push({path,options});return lineageReply();};
}
'''

class LineageUI(unittest.TestCase):
    def javascript(self, body):
        database_ui.DatabaseUI.javascript(self, SETUP + '\n' + body)

    def test_same_focus_get_only_categories_versions_locale_and_drafts(self):
        self.javascript(r'''
await lineageReady();
const drafts=JSON.stringify(worksheets),sql=$('sql').value,wap=$('p35-wap-sql').value;
await dbDescribe(dbObject,'lineage');assert.equal(submissions.length,0);
assert.equal(inspectorTabs.find(b=>b.dataset.inspect==='lineage').disabled,false);
await $('db-lineage-refresh').onclick();assert.equal(submissions.length,1);
const request=submissions[0];assert.ok(request.path.startsWith('/api/v1/objects/orders/lineage?context='));assert.ok(!request.options.method);
const context=JSON.parse(new URL('http://localhost'+request.path).searchParams.get('context'));
assert.equal(Object.keys(context).length,9);assert.equal(context.schema_id,'raw');assert.equal(context.selected_role,'admin');
for(const locale of ['ja','en']){
 setLanguage(locale);dbLineageControls();
 assert.equal($('lineage').children.length,0);assert.ok(!nodes['explorer-lineage-refresh']);
 assert.equal($('explorer-open-lineage').textContent,t('Saved lineage'));
 for(const word of ['9007199254740993','sales.raw.source','sales.raw.orders','model.arbitrary'])assert.ok(lineageText('table-lineage').includes(word));
 assert.ok(lineageText('table-lineage').includes(t('Native dbt graph')));assert.ok(!lineageText('table-lineage').includes('native_graph'));
 assert.ok(lineageText('table-lineage').includes(t('Historical version')));assert.ok(lineageText('table-lineage').includes(t('Confirmed saved evidence')));assert.ok(lineageText('table-lineage').includes(t('Declared model references')));
}
assert.equal(submissions.length,1);assert.equal(JSON.stringify(worksheets),drafts);assert.equal($('sql').value,sql);assert.equal($('p35-wap-sql').value,wap);
assert.equal([...storage.keys()].some(k=>k.includes('lineage')),false);
''')

    def test_explorer_action_is_mounted_opens_focuses_and_reads_once(self):
        for role, kind, locale in [('admin', 'TABLE', 'ja'), ('admin', 'VIEW', 'en'),
                                   ('data_engineer', 'TABLE', 'en'), ('data_engineer', 'VIEW', 'ja')]:
            with self.subTest(role=role, kind=kind, locale=locale):
                self.javascript('const [role,kind,locale]=' + json.dumps([role, kind, locale]) + ';currentRole=role;' + r'''
await lineageReady();assert.equal(calls.filter(c=>c.path.includes('/lineage?')).length,0);
const action=$('explorer-open-lineage');
assert.equal(action.type,'button');assert.equal(action.className,'small');assert.equal(action.getAttribute('aria-controls'),'inspect-lineage');
assert.equal($('catalog').hidden,true);
const ancestors=[];for(let node=action;node;node=node.parentElement){ancestors.push(node.id);assert.equal(node.hidden,false,'hidden ancestor '+node.id);assert.notEqual(node.style?.display,'none');}
assert.ok(ancestors.includes('database-details'));assert.ok(ancestors.includes('warehouse'));assert.ok(!ancestors.includes('catalog'));
const tab=inspectorTabs.find(b=>b.dataset.inspect==='lineage');
 dbObject={...stable('orders'),kind};dbExplorerControls();setLanguage(locale);
 await dbSelectInspector(inspectorTabs.find(b=>b.dataset.inspect==='columns'));
 assert.equal(activeInspector,'columns');assert.equal($('inspect-lineage').hidden,true);
 const before=calls.length,count=submissions.length;
 route=async(path,options)=>{
  assert.equal(dbWorkspacePane,'details');assert.equal(activeInspector,'lineage');assert.equal(tab.getAttribute('aria-selected'),'true');assert.equal($('inspect-lineage').hidden,false,'lineage panel');assert.equal(document.activeElement,tab);
  assert.equal(action.disabled,true);assert.equal($('db-lineage-refresh').disabled,true);
  submissions.push({path,options});return {...lineageReply(),focus:{...lineageReply().focus,kind}};
 };
 assert.equal(action.disabled,false,'action '+role+' '+dbLineageReason());assert.equal(action.textContent,t('Saved lineage'));
 await action.onclick();
 assert.equal(submissions.length,count+1);assert.equal(calls.slice(before).filter(c=>c.path.includes('/lineage?')).length,1);
 for(const path of ['/api/session','/api/v1/databases','/api/v1/databases/db/schemas'])assert.ok(calls.slice(before).filter(c=>c.path===path).length>=2,path);
 assert.ok(lineageText('table-lineage').includes('sales.raw.source'));assert.ok($('table-lineage').parentElement===$('inspect-lineage'));
 assert.equal($('lineage').children.length,0);assert.ok(!nodes['explorer-lineage-refresh']);
currentRole='bi_viewer';dbLineageControls();
assert.equal(action.disabled,true);assert.equal(action.title,t('Lineage requires data_engineer or admin.'));
const deniedCount=submissions.length;await action.onclick();assert.equal(submissions.length,deniedCount);
''')

    def test_database_lineage_wraps_root_direct_children_and_long_text(self):
        css = database_ui.HTML.split('</style>', 1)[0]
        root = re.search(r'html\[data-data-mode="database"\] #table-lineage\{([^}]+)\}', css)[1]
        children = re.search(r'html\[data-data-mode="database"\] #table-lineage>\*\{([^}]+)\}', css)[1]
        self.assertIn('grid-template-columns:minmax(0,1fr)', root)
        for rule in (root, children):
            self.assertIn('min-width:0', rule)
            self.assertIn('overflow-wrap:anywhere', rule)
        self.javascript(r'''
await lineageReady();const physical='physical_'+ 'x'.repeat(300),plan='plan_'+ 'y'.repeat(300);
dbObject.physical_name=physical;
route=async()=>({...lineageReply(),focus:{...lineageReply().focus,label:physical},declared:[{category:'declared_model',source:lineageReply().focus,target:{kind:'LOGICAL',label:'<img src=x>'+plan,materialization:'view'},model:{id:plan,plan_id:plan}}]});
await $('explorer-open-lineage').onclick();
for(const locale of ['ja','en']){setLanguage(locale);dbLineageControls();
 const root=$('table-lineage'),direct=root.children.filter(n=>n.tagName==='P');
 assert.ok(direct.some(n=>n.textContent.includes(physical)&&n.textContent.includes(' / ')));
 assert.ok(direct.some(n=>n.textContent.startsWith(physical+' · TABLE')));
 assert.ok(lineageText('table-lineage').includes(plan));assert.ok(lineageText('table-lineage').includes('9007199254740993'));
 assert.ok(root.descendants().some(n=>n.tagName==='P'&&n.textContent.includes('<img src=x>'+plan)));
 assert.ok(!root.descendants().some(n=>n.tagName==='IMG'||n.innerHTML));
}
''')

    def test_stale_away_back_navigation_source_auth_and_control_rebuild(self):
        self.javascript(r'''
await lineageReady();const held=deferred();route=()=>held.promise;
const pending=dbLineageRefresh();await tick();
dbShowWorkspace('worksheet');dbShowWorkspace('details');held.resolve(lineageReply());await pending;
assert.equal(dbLineage.result,null);assert.ok(!lineageText('table-lineage').includes('sales.raw.source'));
route=async()=>lineageReply();await dbLineageRefresh();assert.ok(dbLineage.result);
dbObject=stable('other');dbExplorerControls();assert.equal(dbLineage.result,null);assert.ok(!lineageText('table-lineage').includes('sales.raw.source'));
$('table-lineage').replaceChildren();dbLineageControls();assert.ok($('db-lineage-refresh').onclick);
currentRole='bi_viewer';dbExplorerControls();assert.equal($('db-lineage-refresh').disabled,true);assert.equal($('explorer-open-lineage').disabled,true);assert.equal($('explorer-open-lineage').title,t('Lineage requires data_engineer or admin.'));
assert.equal(inspectorTabs.find(b=>b.dataset.inspect==='lineage').disabled,true);assert.ok(lineageText('table-lineage').includes(t('Lineage requires data_engineer or admin.')));
currentRole='admin';dbExplorerControls();route=async()=>({http:403,body:{error:{code:'SQL_PERMISSION_DENIED'}}});await dbLineageRefresh();assert.equal(dbLineage.result,null);
''')

    def test_pending_owner_role_grant_context_navigation_source_and_edit_aba(self):
        cases = {
            'principal': "session.username='other';await dbRefreshContext();session.username='owner';await dbRefreshContext();",
            'role': "$('role-selector').value='data_engineer';await $('role-selector').onchange();$('role-selector').value='admin';await $('role-selector').onchange();",
            'grant': "inventory[0].grant_revision=5;await dbRefreshExplorer();inventory[0].grant_revision=4;await dbRefreshExplorer();",
            'binding': "inventory[0].binding_revision=2;await dbRefreshExplorer();inventory[0].binding_revision=1;await dbRefreshExplorer();",
            'generation': "inventory[0].generation_id='next';await dbRefreshExplorer();inventory[0].generation_id='gen';await dbRefreshExplorer();",
            'source': "await dbDescribe(stable('other'),'lineage');await dbDescribe(stable('orders'),'lineage');",
            'schema': "const d=dbDatabases[0];await dbSelectTreeSchema(d,schemas[0]);await dbSelectTreeSchema(d,schemas[1]);await dbDescribe(stable('orders'),'lineage');",
            'inspector': "await dbSelectInspector(inspectorTabs.find(b=>b.dataset.inspect==='columns'));await dbSelectInspector(inspectorTabs.find(b=>b.dataset.inspect==='lineage'));",
            'mainnav': "activate(tabs.find(b=>b.dataset.tab==='jobs'));activate(tabs.find(b=>b.dataset.tab==='catalog'));",
            'edit': "const draft=$('sql').value;$('sql').value='SELECT changed';$('sql').dispatchEvent({type:'input'});$('sql').value=draft;$('sql').dispatchEvent({type:'input'});",
            'reload': "await initializeDatabaseUI(session);"
        }
        for label, change in cases.items():
            with self.subTest(label=label):
                self.javascript(r'''
await lineageReady();activeInspector='lineage';
const held=deferred();route=path=>path.includes('/lineage?')?held.promise:Promise.resolve({items:[],columns:[]});
const pending=dbLineageRefresh();await tick();
''' + change + r'''
held.resolve(lineageReply());await pending;
assert.equal(dbLineage.result,null);assert.ok(!lineageText('table-lineage').includes('sales.raw.source'));
''')

    def test_held_200_rechecks_fresh_session_inventory_and_schema(self):
        changes = {
            'identity': "session.username='different';",
            'maximum': "session.maximum_role='bi_viewer';",
            'grant': "inventory[0].grant_revision=5;",
            'binding': "inventory[0].binding_revision=2;",
            'generation': "inventory[0].generation_id='new';",
            'query': "inventory[0].capabilities.query=false;",
            'role': "inventory[0].allowed_roles=['bi_viewer'];",
            'gone': "inventory=[];",
            'schema': "schemas=schemas.filter(s=>s.id!=='raw');",
        }
        for label, change in changes.items():
            with self.subTest(label=label):
                self.javascript(r'''
await lineageReady();const held=deferred();route=()=>held.promise;
const pending=dbLineageRefresh();await tick();
assert.ok(calls.some(c=>c.path.includes('/lineage?')));
''' + change + r'''
held.resolve(lineageReply());await pending;
assert.equal(dbLineage.result,null);assert.ok(!lineageText('table-lineage').includes('sales.raw.source'));
''')

    def test_auth_loss_then_fresh_normal_explorer_context_and_wrong_focus(self):
        self.javascript(r'''
await lineageReady();const held=deferred();route=()=>held.promise;
const pending=dbLineageRefresh();await tick();inventory[0].capabilities.query=false;
held.resolve(lineageReply());await pending;assert.equal(dbLineage.result,null);
inventory[0].capabilities.query=true;route=async()=>({items:[]});
await dbRefreshExplorer();await dbReselectExplorer();dbObject=stable('orders');dbExplorerControls();
route=async()=>({...lineageReply(),focus:{...lineageReply().focus,ref:ref('wrong')}});
await dbLineageRefresh();assert.equal(dbLineage.result,null);
route=async()=>lineageReply();await dbLineageRefresh();assert.ok(dbLineage.result);
''')

    def test_navigation_aba_old_response_cannot_clear_new_request_or_result(self):
        self.javascript(r'''
await lineageReady();const old=deferred(),fresh=deferred();let count=0;
route=()=>++count===1?old.promise:fresh.promise;
const first=dbLineageRefresh();await tick();
dbShowWorkspace('worksheet');dbShowWorkspace('details');
const second=dbLineageRefresh();await tick();const token=dbLineage.busy;
old.resolve(lineageReply());await first;assert.equal(dbLineage.busy,token);assert.equal(dbLineage.result,null);
fresh.resolve(lineageReply());await second;assert.ok(dbLineage.result);assert.equal(dbLineage.busy,null);
''')

    def test_declared_logical_model_native_plan_identity_localized_safe_text(self):
        self.javascript(r'''
await lineageReady();
route=async()=>({...lineageReply(),confirmed:[],declared:[{category:'declared_model',
 source:lineageReply().focus,target:{kind:'LOGICAL',label:'<img src=x>',unique_id:'model.native.planned',materialization:'view'},
 model:{id:'model.native.planned',plan_id:'saved-plan-id',materialization:'view'}}]});
await dbLineageRefresh();
for(const locale of ['ja','en']){setLanguage(locale);dbLineageControls();
 const text=lineageText('table-lineage');assert.ok(text.includes('<img src=x>'));assert.ok(text.includes(t('Planned model')));assert.ok(text.includes(t('Saved plan')+' saved-plan-id'));assert.ok(!text.includes('undefined'));assert.ok(!text.includes('[object Object]'));assert.ok(!text.includes('WAP version'));}
''')

    def test_declared_physical_refs_are_unversioned_and_logical_targets_are_planned(self):
        self.javascript(r'''
await lineageReady();
const snapshot='9007199254740993',table={...lineageReply().focus,label:'declared.table'},view={kind:'VIEW',label:'declared.view',ref:{...ref('<view-id>'),incarnation:7},view_version:42};
const logical={kind:'LOGICAL',label:'planned.model',unique_id:'model.planned',materialization:'view'};
route=async(path,options)=>{submissions.push({path,options});return {...lineageReply(),
 confirmed:[{...lineageReply().confirmed[0],source:view}],
 declared:[{category:'declared_model',source:table,target:view},{category:'declared_model',source:view,target:logical}]};};
await dbLineageRefresh();assert.equal(submissions.length,1);const before=calls.length;
const note='Model definitions describe dependencies between objects. Pinned runtime versions appear in Confirmed saved evidence.';
for(const locale of ['ja','en']){
 setLanguage(locale);dbLineageControls();
 const root=$('table-lineage'),[confirmed,declared]=root.children.filter(n=>n.tagName==='SECTION');
 const text=node=>[node.textContent,...node.descendants().map(n=>n.textContent)].join(' ');
 const declarations=text(declared),evidence=text(confirmed);
 assert.ok(declarations.includes(t(note)));
 if(locale==='ja')assert.ok(declarations.includes('モデル定義はオブジェクト間の依存関係です。実行時の固定版は『確認済みの保存証跡』に表示します。'));
 else assert.ok(declarations.includes(note));
 assert.ok(declarations.includes('declared.table · TABLE · '+t('Ref')+': '+t('Object ID')+' orders · '+t('Incarnation')+' 1'));
 assert.ok(declarations.includes('declared.view · VIEW · '+t('Ref')+': '+t('Object ID')+' <view-id> · '+t('Incarnation')+' 7'));
 assert.ok(!declarations.includes(t('Snapshot')));assert.ok(!declarations.includes(t('VIEW version')));
 assert.ok(!declarations.includes(snapshot));assert.ok(!declarations.includes('42'));
 const planned=declared.descendants().find(n=>n.tagName==='P'&&n.textContent.includes('planned.model'));
 const target=planned.textContent.split(' → ')[1];
 assert.equal(target,'planned.model · '+t('Planned model')+' · view');assert.ok(!target.includes(t('Ref')));
 assert.ok(evidence.includes(t('Snapshot')+' '+snapshot));assert.ok(evidence.includes(t('VIEW version')+' 42'));
 assert.ok(evidence.includes(t('Historical version')));
 assert.ok(root.children.some(n=>n.tagName==='P'&&n.textContent.includes(t('Snapshot')+' '+snapshot)));
 assert.ok(!declared.descendants().some(n=>n.tagName==='IMG'||n.innerHTML));
}
assert.equal(calls.length,before);assert.equal(submissions.length,1);
''')

    def test_view_focus_empty_bounded_and_keyboard_skip(self):
        self.javascript(r'''
await lineageReady();dbObject={...dbObject,kind:'VIEW'};dbExplorerControls();assert.equal(inspectorTabs.find(b=>b.dataset.inspect==='lineage').disabled,false);
route=async()=>({...lineageReply(),focus:{label:'sales.raw.orders',kind:'VIEW',ref:ref('orders'),view_version:2},confirmed:[],declared:[],coverage:{scope:'visible_saved_records',limit:200,limited:true,empty_reason:'no_visible_saved_evidence'}});
await dbLineageRefresh();assert.ok(lineageText('table-lineage').includes(t('No visible saved evidence for this object.')));assert.ok(lineageText('table-lineage').includes(t('VIEW version')+' 2'));
currentRole='bi_viewer';dbLineageControls();const last=inspectorTabs.find(b=>b.dataset.inspect==='snapshots');let prevented=false;last.onkeydown({key:'ArrowRight',preventDefault(){prevented=true;}});assert.ok(prevented);assert.notEqual(document.activeElement.dataset.inspect,'lineage');
''')
