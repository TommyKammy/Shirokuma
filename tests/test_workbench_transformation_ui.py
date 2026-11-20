"""UX-A4 transformation flow: order, gating, impact review and published MARTS."""
import re
import unittest
from html.parser import HTMLParser

import test_workbench_dataops_ui as dataops_ui
import test_workbench_database_ui as database_ui
from workbench_source import WorkbenchSource


class Markup(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.stack, self.nodes = [], []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        node = dict(tag=tag, attrs=dict(attrs), parents=list(self.stack))
        self.nodes.append(node)
        if tag not in {'meta', 'input', 'link', 'br', 'hr', 'img', 'use', 'path', 'circle', 'rect', 'ellipse'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'] == tag:
                del self.stack[index:]
                break


def belongs_to(node, identifier):
    return any(parent['attrs'].get('id') == identifier for parent in node['parents'])


class TransformationMarkup(unittest.TestCase):
    def setUp(self):
        self.source = WorkbenchSource(database_ui.HTML)
        self.document = Markup(database_ui.HTML)

    def by_id(self, identifier):
        return next(node for node in self.document.nodes if node['attrs'].get('id') == identifier)

    def test_single_transformation_screen_and_no_lead_copy(self):
        self.assertNotIn('id="transformation-lead"', database_ui.HTML)
        self.assertIn('id="transformation-models"', database_ui.HTML)
        self.assertIn('id="transformation-wap"', database_ui.HTML)
        self.assertIn('id="transformation-marts"', database_ui.HTML)
        wap_tab = self.by_id('transformation-view-wap')
        self.assertIn('hidden', wap_tab['attrs'])
        self.assertIn('data-admin-view', self.by_id('transformation-view-marts')['attrs'])
        self.assertEqual(self.by_id('transformation-view-models')['attrs'].get('aria-selected'), 'true')

    def test_published_marts_review_contains_legacy_target_controls(self):
        review = self.by_id('marts-update-review')
        self.assertEqual(review['tag'], 'details')
        self.assertIn('open', review['attrs'])
        self.assertTrue(belongs_to(review, 'marts-admin'))
        for identifier in ('marts-list', 'mart-form', 'mart-run', 'mart-schedule-save'):
            self.assertTrue(belongs_to(self.by_id(identifier), 'marts-update-review'), identifier)

    def test_worksheet_keeps_one_transformation_handoff(self):
        handoff = self.by_id('open-transformation')
        self.assertTrue(belongs_to(handoff, 'warehouse'))
        for identifier in ('copilot-stage', 'copilot-publish'):
            self.assertFalse(belongs_to(self.by_id(identifier), 'warehouse'), identifier)
            self.assertTrue(belongs_to(self.by_id(identifier), 'transformation-models'), identifier)
        self.assertIn('hidden', self.by_id('copilot-stage-table')['parents'][-1]['attrs'])

    def test_transformation_css_wraps_and_keeps_touch_targets(self):
        css = self.source.css
        self.assertIn('#ingestion .subnav button,#transformation .subnav button{min-height:44px}', css)
        self.assertIn('#p35-wap-form :is(button,select,input,textarea,summary),', css)
        self.assertIn('#transformation :is(button,select,input:not([type=checkbox]),textarea,summary){min-height:44px}', css)
        self.assertIn(':is(#p35-wap,#db-marts-panel){min-width:0;max-width:100%}', css)
        self.assertIn(':focus-visible{outline:2px solid var(--focus);outline-offset:2px}', css)


class TransformationUI(unittest.TestCase):
    javascript = dataops_ui.DataopsUI.javascript

    def test_flow_order_one_primary_and_published_open_link(self):
        self.javascript(r'''
const p=await ready('p35-wap');
const primaries=()=>p.form.descendants().filter(n=>n&&!n.hidden&&String(n.className).split(' ').includes('primary'));
const ids=()=>primaries().map(n=>n.id),order=id=>p.form.descendants().indexOf($(id));
assert.ok(order('p35-wap-save')<order('p35-wap-build-action'));
assert.ok(order('p35-wap-build-action')<order('p35-wap-audit-action'));
assert.ok(order('p35-wap-audit-action')<order('copilot-publish'));
assert.ok(order('copilot-publish')<order('p35-wap-publish-review'));
assert.ok(order('p35-wap-publish-review')<order('p35-wap-publish'));
const original=route;let version=0;
route=async(path,options)=>{
 const body=options?.body?JSON.parse(options.body):null,context=body?.context||JSON.parse(new URL('http://local'+path).searchParams.get('context'));
 if(!body)return {context,snapshots:[]};
 if(path==='/api/v1/wap/models')return {context,model:{...body,version:++version}};
 const kind=path.endsWith('/publish')?'publication':path.split('/').at(-1);
 return wapReceipt(kind,context,{id:kind+'-record',operation_id:'op-'+kind,version,kind:'publish',...(kind==='build'?{destination:{database_id:'db',schema_id:'schema',physical_name:'daily_orders'}}:kind==='audit'?{build_id:body.build_id}:{build_id:body.build_id,audit_id:body.audit_id})});
};
assert.deepEqual(ids(),['p35-wap-save']);
p.sources[0].table.value='0';$('p35-wap-model-id').value='daily_orders';$('p35-wap-name').value='daily_orders';$('p35-wap-target-schema').value='schema';
await $('p35-wap-save').onclick();assert.deepEqual(ids(),['p35-wap-build-action']);
await $('p35-wap-build-action').onclick();assert.deepEqual(ids(),['p35-wap-audit-action']);
await $('p35-wap-audit-action').onclick();assert.deepEqual(ids(),['copilot-publish']);
await $('copilot-publish').onclick();assert.deepEqual(ids(),['p35-wap-publish']);
await $('p35-wap-publish').onclick();
assert.deepEqual(ids().filter(id=>id!=='p35-wap-open-table'),[]);
assert.equal($('p35-wap-open-table').className,'primary');
assert.equal($('p35-wap-open-table').getAttribute('href'),'#explore/sales.marts.daily_orders');
assert.match($('p35-wap-audit-link').getAttribute('href'),/^#audit\//);
route=original;
''')

    def test_previous_stage_blocks_next_and_input_change_invalidates_candidate(self):
        self.javascript(r'''
const p=await ready('p35-wap'),id=key=>$('p35-wap-'+key);
assert.equal(id('build-action').disabled,true);assert.match(id('stage-reason').textContent,/Save the model/);
let version=0;const original=route;
route=async(path,options)=>{const body=options?.body?JSON.parse(options.body):null;if(!body)return original(path,options);const context=body.context;if(path==='/api/v1/wap/models')return {context,model:{...body,version:++version}};const kind=path.endsWith('/publish')?'publication':path.split('/').at(-1);return wapReceipt(kind,context,{id:kind+'-record',operation_id:'op-'+kind,version,kind:'publish',...(kind==='build'?{destination:{database_id:'db',schema_id:'schema',physical_name:'daily_orders'}}:kind==='audit'?{build_id:body.build_id}:{build_id:body.build_id,audit_id:body.audit_id})});};
p.sources[0].table.value='0';id('model-id').value='daily_orders';id('name').value='daily_orders';id('target-schema').value='schema';
await id('save').onclick();assert.equal(id('build-action').disabled,false);assert.equal(id('audit-action').disabled,true);assert.match(id('stage-reason').textContent,/Build a candidate/);
assert.equal(id('guide-next').textContent,'');assert.equal(id('build-action').getAttribute('aria-describedby'),'p35-wap-stage-reason');
await id('build-action').onclick();assert.equal(id('audit-action').disabled,false);assert.equal(id('publish').disabled,true);assert.match(id('stage-reason').textContent,/Pass the quality check/);
id('sql').value='SELECT 2 FROM source';id('sql').oninput();
assert.match(id('candidate').textContent,/Input changed; the candidate is invalid/);
assert.equal(id('build-action').disabled,true);assert.match(id('stage-reason').textContent,/Save the model/);
dataopsInvalidate(p);assert.equal(id('availability').hidden,false,'availability');assert.equal(id('stage-reason').hidden,true,'stage reason');assert.equal(id('save').getAttribute('aria-describedby'),'p35-wap-availability','described');
route=original;
''')

    def test_publish_impact_existing_new_and_unknown_rows(self):
        cases = [
            ('existing', "route=async(path,options)=>path.startsWith('/api/v1/objects/resolve')?{ref:{object_id:'object-1'}}:path.includes('/api/v1/objects/object-1')?{snapshots:[{timestamp_ms:'2',summary:{'total-records':'12'}}]}:original(path,options);",
             r'Replace 12 published rows with 3 rows\.', 'Replace and publish', True, True),
            ('new', "route=async(path,options)=>path.startsWith('/api/v1/objects/resolve')?{http:404,body:{code:'NOT_FOUND'}}:original(path,options);",
             r'Create a new MARTS table with 3 rows\.', 'Publish to MARTS', False, False),
            ('unknown', "route=async(path,options)=>path.startsWith('/api/v1/objects/resolve')?{ref:{object_id:'object-2'}}:path.includes('/api/v1/objects/object-2')?{snapshots:[{timestamp_ms:'2',summary:{}}]}:original(path,options);",
             r'The published row count is unavailable\.', 'Replace and publish', True, True),
        ]
        for name, stub, copy, label, danger, roles in cases:
            with self.subTest(case=name):
                self.javascript("const original=route;const p=await ready('p35-wap');dataopsLoadModel(p,{model_id:'saved',version:1,sql:'SELECT * FROM source',sources:{source:ref('orders')},target:{database_id:'db',schema_id:'schema',physical_name:'daily_orders'},min_rows:1,not_null:[]});" + stub + r'''
dataopsCaptureWap(p,wapReceipt('build',p.context,{id:'build-1',model_id:p.model.model_id,version:1,destination:p.model.target}).operation);
dataopsCaptureWap(p,wapReceipt('audit',p.context,{id:'audit-1',model_id:p.model.model_id,version:1,build_id:'build-1',count:3,null_counts:{code:0},passed:true}).operation);
await $('copilot-publish').onclick();
''' + "const text=$('p35-wap-publish-review').textContent;assert.match(text,/" + copy + r"/);" + ("assert.match(text,/Referencing roles: Admin, Data Engineer, BI Viewer/);" if roles else "") + "assert.equal($('p35-wap-publish').textContent,'" + label + r"');assert.equal(String($('p35-wap-publish').className).includes('danger')," + ('true' if danger else 'false') + r");route=original;" )

    def test_after_publish_the_open_link_is_the_only_primary_action(self):
        self.javascript(r'''
const p=await ready('p35-wap'),original=route;
dataopsLoadModel(p,{model_id:'saved',version:1,sql:'SELECT * FROM source',sources:{source:ref('orders')},target:{database_id:'db',schema_id:'schema',physical_name:'daily_orders'},min_rows:1,not_null:[]});
dataopsCaptureWap(p,wapReceipt('build',p.context,{id:'build-1',model_id:p.model.model_id,version:1,destination:p.model.target}).operation);
dataopsCaptureWap(p,wapReceipt('audit',p.context,{id:'audit-1',model_id:p.model.model_id,version:1,build_id:'build-1',count:3,null_counts:{code:0},passed:true}).operation);
route=async(path,options)=>{if(path==='/api/v1/objects/resolve')return {http:404,body:{code:'NOT_FOUND'}};if(path.endsWith('/publish'))return wapReceipt('publication',JSON.parse(options.body).context,{id:'publication-1',operation_id:'op-publish',model_id:p.model.model_id,version:1,kind:'publish',build_id:'build-1',audit_id:'audit-1',destination:p.model.target});return original(path,options);};
await $('copilot-publish').onclick();await $('p35-wap-publish').onclick();
const visiblePrimary=p.form.descendants().filter(n=>n&&!n.hidden&&String(n.className).split(' ').includes('primary'));
assert.deepEqual(visiblePrimary.map(n=>n.id),['p35-wap-open-table']);
assert.equal($('p35-wap-open-table').getAttribute('href'),'#explore/sales.marts.daily_orders');
assert.equal($('p35-wap-audit-link').getAttribute('href'),'#audit/publication-1');
route=original;
''')

    def test_visible_transformation_copy_has_no_wap_terminology(self):
        self.javascript(r'''
const flatten=node=>[node.textContent||'',...node.children.map(flatten)].join(' ');
const p=await ready('p35-wap');
for(const language of ['ja','en']){setLanguage(language);dataopsRender(p);assert.doesNotMatch(flatten(p.root),/WAP/);assert.doesNotMatch(flatten($('warehouse')),/WAP/);}
''')

    def test_copilot_handoff_controls_are_in_transformation(self):
        self.javascript(r'''
const p=await ready('p35-wap');
assert.ok(p.root.descendants().includes($('copilot-stage')));assert.ok(p.root.descendants().includes($('copilot-publish')));
assert.ok(!$('warehouse').descendants().includes($('copilot-stage')));assert.ok(!$('warehouse').descendants().includes($('copilot-publish')));
assert.ok($('warehouse').descendants().includes($('open-transformation')));
await $('open-transformation').onclick();
assert.equal(activeTab,'transformation');assert.equal(document.activeElement,p.root);
''')

    def test_published_marts_tab_and_panel_keep_existing_control_ids(self):
        self.javascript(r'''
const p=await ready('p35-wap');
assert.equal($('transformation-view-wap').hidden,true);
assert.equal($('transformation-view-marts').textContent,'Published MARTS');
assert.equal($('db-marts-panel').parentElement,$('transformation-marts'));
for(const id of ['db-marts-list','db-marts-save','db-marts-check','db-marts-refresh-run','db-marts-history-refresh'])assert.ok($('db-marts-panel').descendants().includes($(id)),id);
''')

    def test_published_marts_uses_ready_databases_role_labels_and_japanese_copy(self):
        self.javascript(r'''
const p=await ready('p35-wap'),original=route;
inventory.push({...inventory[0],id:'retired',display_name:'Legacy',sql_catalog:'legacy',state:'RETIRING',allowed_roles:['admin']});
await dbRefreshAccess();
route=async(path,options)=>{
 const c=options?.body?JSON.parse(options.body).context:JSON.parse(new URL('http://local'+path).searchParams.get('context'));
 if(path.startsWith('/api/v1/marts?'))return {context:c,items:[]};
 if(path.startsWith('/api/v1/wap/models?'))return {context:c,items:[]};
 return original(path,options);
};
dbMarts.database='db';dbMarts.schema='schema';dbMarts.role='admin';await dbMartsRefresh();
assert.equal([...$('db-marts-database').options].some(o=>o.value==='retired'),false);
assert.equal($('db-marts-database').options.find(o=>o.value==='db').textContent,'Sales · sales');
dbMarts.database='retired';dbMartsRender();assert.equal([...$('db-marts-database').options].some(o=>o.value==='retired'||o.textContent.includes('Legacy')),false);
dbMarts.database='db';dbMartsRender();
setLanguage('ja');
assert.equal($('db-marts-role').options.find(o=>o.value==='admin').textContent,'管理者');
assert.equal(t('Saved Marts'),'保存済みマート');assert.equal(t('Refresh MARTS'),'MARTS を更新');
assert.equal(t('Watch sources'),'監視するソース');assert.equal(t('Full refresh'),'全件更新');
assert.equal(t('Check only when changed'),'変更がある場合のみ更新');
assert.doesNotMatch($('db-marts-panel').descendants().map(n=>n.textContent||'').join(' '),/Choose the database and schema/);
route=original;
''')

    def test_transformation_japanese_copy_has_no_leftover_english_sentences(self):
        self.javascript(r'''
const visible=node=>node.hidden?'':[node.textContent||'',...node.children.map(visible)].join(' ');
const p=await ready('p35-wap');setLanguage('ja');dataopsRender(p);dbMartsRender();
for(const key of ['Candidate and quality checks','Saved transformation metadata','Saved transformation target: {target}','Choose or refresh the transformation context.','Loading publication impact…','Quality failed. Edit the model or build again.','Open transformation recovery','Refresh MARTS','Saved Marts','Load Mart','New Mart','Save Mart','Watch sources','Manual-only Marts allow unconditional refresh. Check and conditional refresh require watches (WATCH_REQUIRED).','Mart history','Full refresh','Check changes'])assert.notEqual(t(key),key,key);
assert.equal(p.body.children[0].textContent,'変換モデル');
const text=visible($('transformation')),marts=visible($('db-marts-panel'));
for(const phrase of ['Candidate and quality checks','Saved transformation metadata','Saved transformation target','Choose or refresh the transformation context.','Loading publication impact','Quality failed. Edit','Open transformation recovery','Choose the database and schema','Refresh MARTS','Saved Marts','Load Mart','New Mart','Save Mart','Watch sources','Manual-only Marts','Mart history','Full refresh','Check changes'])assert.equal(text.includes(phrase),false,phrase);
for(const phrase of ['Refresh MARTS','Saved Marts','Load Mart','New Mart','Save Mart','Watch sources','Manual-only Marts','Mart history','Full refresh','Check changes'])assert.equal(marts.includes(phrase),false,phrase);
''')

    def test_dbt_plan_is_closed_by_default_and_opens_for_dbt_deep_links(self):
        self.javascript(r'''
storage.set(dbWorksheetKey,JSON.stringify({active:'a',tabs:[input()]}));
globalThis.location={href:'http://local/#transformation'};
globalThis.history={replaceState:(state,title,url)=>{globalThis.location.href=String(url);},pushState:(state,title,url)=>{globalThis.location.href=String(url);}};
await initializeDatabaseUI(session);await dbPageHydration;await dbRuntimeHydration;
assert.equal($('transformation-dbt').open,false);
''')
        self.javascript(r'''
storage.set(dbWorksheetKey,JSON.stringify({active:'a',tabs:[input()]}));
globalThis.location={href:'http://local/#transformation?dbt_db=db&dbt_plan=plan-1'};
globalThis.history={replaceState:(state,title,url)=>{globalThis.location.href=String(url);},pushState:(state,title,url)=>{globalThis.location.href=String(url);}};
await initializeDatabaseUI(session);await dbPageHydration;await dbRuntimeHydration;
assert.equal($('transformation-dbt').open,true);
''')

    def test_deep_links_restore_flow_and_published_marts(self):
        self.javascript(r'''
storage.set(dbWorksheetKey,JSON.stringify({active:'a',tabs:[input()]}));
globalThis.location={href:'http://local/#transformation/marts'};
globalThis.history={replaceState:(state,title,url)=>{globalThis.location.href=String(url);},pushState:(state,title,url)=>{globalThis.location.href=String(url);}};
await initializeDatabaseUI(session);await dbPageHydration;await dbRuntimeHydration;await tick();await tick();
assert.equal(activeTab,'transformation');
assert.equal($('transformation-marts').hidden,false);assert.equal($('transformation-models').hidden,true);
assert.match(globalThis.location.href,/#transformation\/marts$/);
globalThis.location.href='http://local/#transformation';
await workbenchRestorePage(session,{history:true});await tick();
assert.equal($('transformation-models').hidden,false);assert.equal($('transformation-marts').hidden,true);
''')


if __name__ == '__main__':
    unittest.main()
