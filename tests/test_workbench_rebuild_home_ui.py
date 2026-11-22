"""UX-R1: Home rebuild from the Atlas mock-up (structure, data and requests)."""
import re
import unittest

import test_workbench_database_ui as harness
from test_workbench_navigation_ui import MARKUP
from workbench_source import WorkbenchSource

CSS = WorkbenchSource(harness.HTML).css
COMPACT_CSS = re.sub(r'\s+', '', re.sub(r'/\*[\s\S]*?\*/', '', CSS))

HOME = r"""
globalThis.location=globalThis.location||{href:'http://local/#home'};
globalThis.history=globalThis.history||{replaceState:()=>{}};
const homeText=id=>{const node=$(id);return [node.textContent,...node.descendants().map(child=>child.textContent)].join('');};
const homeLinks=id=>$(id).descendants().filter(node=>node.tagName==='A');
const nodeText=node=>[node.textContent,...node.descendants().map(child=>child.textContent)].join('');
const homeRows=(id,className)=>$(id).descendants().filter(node=>(node.className||'').split(' ').includes(className));
async function homeOpen(){
 workbenchShellReady=true;activeTab='home';
 for(const schema of dbSchemasFor(dbCurrent()))schema.capabilities.job_sql=true;
 await loadHome();await tick();
}
const homeContext=()=>structuredClone(dbContext(dbCurrent()));
const jobsEnvelope=runs=>({context:homeContext(),runs});
const alertsEnvelope=(alerts=[])=>({context:homeContext(),alerts,unacknowledged_count:alerts.filter(a=>!a.acknowledged_at).length});
const emptySources=async path=>path.startsWith('/api/v1/jobs/runs')?jobsEnvelope([]):path.startsWith('/api/v1/alerts')?alertsEnvelope():path==='/api/v1/databases'?{items:[]}:{};
const databaseReads=from=>calls.slice(from).filter(call=>call.path.split('?')[0]==='/api/v1/databases').length;
"""


def classes(node):
    return (node['attrs'].get('class') or '').split()


def children(parent):
    return [node for node in MARKUP.elements
            if node['parents'] and node['parents'][-1] is parent]


def direct_child(parent, css_class):
    matches = [node for node in children(parent) if css_class in classes(node)]
    assert len(matches) == 1, (parent['attrs'].get('id'), css_class, len(matches))
    return matches[0]


class RebuildHomeUI(unittest.TestCase):
    javascript = harness.DatabaseUI.javascript

    def test_two_column_mockup_structure_and_classes(self):
        home = MARKUP.ids['home']
        grids = [node for node in MARKUP.elements if 'homegrid' in classes(node) and home in node['parents']]
        self.assertEqual(len(grids), 1)
        grid = grids[0]
        self.assertEqual(classes(grid), ['homegrid'])
        self.assertEqual([classes(node) for node in children(grid)], [['home-main'], ['homeright']])
        left, right = children(grid)
        self.assertEqual([node['tag'] for node in children(left)], ['h1', 'div', 'div', 'section'])
        self.assertEqual([node['attrs'].get('id') for node in children(left)],
                         ['home-review-heading', 'home-attention-group', None, 'home-jobs-section'])
        self.assertEqual(classes(children(left)[1]), ['taskgroup'])
        self.assertEqual(classes(children(left)[2]), ['taskgroup'])
        self.assertEqual(classes(children(left)[3]), ['section'])
        self.assertEqual([node['attrs'].get('id') for node in children(right)],
                         ['home-context', 'home-objects-section', 'home-activity-section'])
        self.assertEqual(classes(MARKUP.ids['home-context']), ['target'])
        self.assertNotIn('home-heading', MARKUP.ids)
        self.assertNotIn('home-section', [value for node in MARKUP.elements
                                          for value in classes(node)])

    def test_groups_and_right_column_cards_keep_the_mockup_order(self):
        attention = MARKUP.ids['home-attention-group']
        self.assertEqual(attention['attrs']['aria-labelledby'], 'home-attention-label')
        self.assertEqual(classes(MARKUP.ids['home-attention-label']), ['grouplabel', 'urgent'])
        self.assertIn('Review required', MARKUP.ids['home-attention-label']['text'])
        actions = MARKUP.ids['home-actions-label']
        self.assertEqual(actions['parents'][-1]['attrs'].get('class'), 'taskgroup')
        self.assertEqual(classes(actions), ['grouplabel'])
        self.assertIn('Next actions', actions['text'])

        jobs = MARKUP.ids['home-jobs-section']
        head = direct_child(jobs, 'sectionhead')
        self.assertEqual(children(head)[0]['attrs']['id'], 'home-jobs-heading')
        self.assertEqual(children(head)[1]['attrs']['id'], 'home-jobs-all')
        self.assertEqual(classes(children(head)[1]), ['textlink'])
        self.assertEqual(children(head)[1]['attrs']['href'], '#jobs')

        context = MARKUP.ids['home-context']
        self.assertEqual(classes(direct_child(context, 'eyebrow')), ['eyebrow'])
        self.assertEqual(classes(direct_child(context, 'kv')), ['kv'])
        self.assertEqual(MARKUP.ids['home-context-heading']['tag'], 'h2')
        labels = [node['text'].strip() for node in children(direct_child(context, 'kv')) if node['tag'] == 'dt']
        self.assertEqual(labels, ['Catalog', 'Schema', 'Warehouse', 'Role'])
        self.assertEqual(classes(MARKUP.ids['home-open-sql']), ['primary', 'full'])
        self.assertEqual(MARKUP.ids['home-open-sql']['attrs']['href'], '#sql')
        self.assertEqual(classes(MARKUP.ids['home-open-explore']), ['secondaryaction'])
        self.assertEqual(MARKUP.ids['home-open-explore']['attrs']['href'], '#explore')

        objects = direct_child(MARKUP.ids['home-objects-section'], 'sectionhead')
        self.assertEqual([node['attrs'].get('id') for node in children(objects)],
                         ['home-objects-heading', 'home-objects-all'])
        self.assertEqual(MARKUP.ids['home-objects-all']['attrs']['href'], '#explore')
        activity = direct_child(MARKUP.ids['home-activity-section'], 'sectionhead')
        self.assertEqual([node['attrs'].get('id') for node in children(activity)],
                         ['home-activity-heading', 'home-activity-date'])
        self.assertEqual(MARKUP.ids['home-activity-date']['tag'], 'span')
        self.assertEqual(classes(MARKUP.ids['home-activity-date']), ['tiny', 'muted'])

    def test_zero_review_items_hide_the_group_heading(self):
        self.javascript(HOME + r"""
await boot();dbRuns.clear();workbenchSetComputeStatus('available','tone-ok','');
route=emptySources;inventoryRoute=async()=>({items:[]});
await homeOpen();
assert.equal($('home-review-heading').textContent,t('Nothing to review today'));
assert.equal($('home-attention-group').hidden,true);
assert.equal(homeText('home-attention-list'),'');
setLanguage('ja');
assert.equal($('home-review-heading').textContent,'今日の確認はありません');
assert.equal($('home-attention-group').hidden,true);
""")

    def test_unavailable_context_is_neutral_and_never_shows_ids(self):
        self.javascript(HOME + r"""
await boot();dbRuns.clear();workbenchSetComputeStatus('available','tone-ok','');
route=emptySources;inventoryRoute=async()=>({items:[]});
dbCurrent().database_id='ghost';dbCurrent().schema_id='missing';
await homeOpen();setLanguage('ja');
assert.equal($('home-context-fields').hidden,true);
assert.equal($('home-context-empty').hidden,false);
const text=['home-context-heading','home-context-empty','home-context-database','home-context-schema','home-context-warehouse','home-context-role'].map(homeText).join(' ');
assert.ok(text.includes('作業コンテキストが選択されていません'),text);
assert.ok(text.includes('データ探索でデータベースとスキーマを選ぶと、ここに表示されます。'),text);
assert.ok(!text.includes('利用不可'),text);
assert.ok(!text.includes('ghost')&&!text.includes('missing'),text);
assert.ok(!text.includes('Unavailable'),text);
dbCurrent().database_id='';dbCurrent().schema_id='';
await homeOpen();
assert.equal($('home-context-fields').hidden,true);
assert.ok(homeText('home-context-heading').includes(t('No work context selected')));
""")

    def test_jobs_unavailable_uses_one_mockup_empty_row(self):
        self.javascript(HOME + r"""
await boot();dbRuns.clear();workbenchSetComputeStatus('available','tone-ok','');
dbCurrent().database_id='';dbCurrent().schema_id='';
route=async path=>{
 if(path.startsWith('/api/v1/jobs/runs')||path.startsWith('/api/v1/alerts'))throw Error('must not be read');
 return {};
};
inventoryRoute=async()=>({items:[]});
await homeOpen();
const list=$('home-jobs-list');
assert.equal(list.children.length,1);
assert.equal(list.children[0].className,'empty');
assert.equal(list.children[0].getAttribute('role'),'status');
assert.equal(homeText('home-jobs-list'),t('Job access unavailable. Refresh an authorized context.'));
setLanguage('ja');
assert.equal(homeText('home-jobs-list'),'ジョブのアクセスを利用できません。許可されたコンテキストを更新してください。');
""")

    def test_each_source_is_requested_once_per_display_language_and_role_change(self):
        self.javascript(HOME + r"""
await boot();dbAdmin.platform=true;dbRuns.clear();
session.platform_admin=true;
workbenchSetComputeStatus('available','tone-ok','');
route=emptySources;
const count=from=>['/api/v1/jobs/runs','/api/v1/alerts','/api/v1/databases'].map(prefix=>
 calls.slice(from).filter(call=>call.path.split('?')[0]===prefix).length);
// Start the display from a cold inventory so the shared context read is in scope.
dbInventories.clear();dbSchemaCache.clear();dbSchemaScope='';dbDatabases=[];
const before=calls.length;
await homeOpen();await tick();
assert.deepEqual(count(before),[1,1,1]);
const afterDisplay=calls.length;
setLanguage('ja');await tick();
assert.equal(calls.length,afterDisplay);
setLanguage('en');await tick();
assert.equal(calls.length,afterDisplay);
const afterLanguage=calls.length;
currentRole='data_engineer';dbCurrent().selected_role='data_engineer';
dbInventories.set('data_engineer',{items:structuredClone(inventory)});
dbSchemaCache.set(dbSchemaKey('data_engineer','db'),structuredClone(schemas));
dbSchemaScope='';dbSchemas=[];
for(const schema of dbSchemasFor(dbCurrent()))schema.capabilities.job_sql=true;
workbenchHomeRoleChanged();await loadHome();await tick();
assert.deepEqual(count(afterLanguage),[1,1,1]);
const afterEngineer=calls.length;
currentRole='admin';dbCurrent().selected_role='admin';
dbSchemaScope='';dbSchemas=[];
for(const schema of dbSchemasFor(dbCurrent()))schema.capabilities.job_sql=true;
workbenchHomeRoleChanged();await loadHome();await tick();
assert.deepEqual(count(afterEngineer),[1,1,1]);
""")

    def test_initial_home_shares_one_inventory_read_with_retiring_rows(self):
        self.javascript(HOME + r"""
await boot();dbAdmin.platform=true;dbRuns.clear();
session.platform_admin=true;
workbenchSetComputeStatus('available','tone-ok','');
route=emptySources;
inventoryRoute=async()=>({items:[...structuredClone(inventory),{id:'db-old',display_name:'Sales',state:'RETIRING'}]});
dbInventories.clear();dbSchemaCache.clear();dbSchemaScope='';dbDatabases=[];
const before=calls.length;
await homeOpen();
assert.equal(databaseReads(before),1);
assert.ok(homeText('home-attention-list').includes(t('Sales')));
""")

    def test_unselected_context_still_shares_the_inventory_read(self):
        self.javascript(HOME + r"""
await boot();dbAdmin.platform=true;dbRuns.clear();
session.platform_admin=true;
workbenchSetComputeStatus('available','tone-ok','');
route=emptySources;
inventoryRoute=async()=>({items:[...structuredClone(inventory),{id:'db-old',display_name:'Sales',state:'RETIRING'}]});
workbenchShellReady=true;activeTab='home';
dbCurrent().database_id='';dbCurrent().schema_id='';
dbInventories.clear();dbSchemaCache.clear();dbSchemaScope='';dbDatabases=[];
const before=calls.length;
await loadHome();await tick();
assert.equal(databaseReads(before),1);
assert.ok(homeText('home-attention-list').includes(t('Sales')));
const warm=calls.length;
await loadHome();await tick();
assert.equal(databaseReads(warm),0);
assert.ok(homeText('home-attention-list').includes(t('Sales')));
""")

    def test_home_waits_for_an_in_flight_inventory_read(self):
        self.javascript(HOME + r"""
await boot();dbAdmin.platform=true;dbRuns.clear();
session.platform_admin=true;
workbenchSetComputeStatus('available','tone-ok','');
route=emptySources;
const gate=deferred();
inventoryRoute=async()=>{await gate.promise;return {items:[...structuredClone(inventory),{id:'db-old',display_name:'Sales',state:'RETIRING'}]};};
workbenchShellReady=true;activeTab='home';
dbInventories.clear();dbDatabases=[];
dbSchemaScope=dbSchemaKey('admin','db');dbSchemas=[];
const before=calls.length;
const shared=dbReadInventory('admin');
const pending=loadHome();
await tick();
assert.equal(databaseReads(before),1);
gate.resolve();
await shared;await pending;await tick();
assert.equal(databaseReads(before),1);
assert.ok(homeText('home-attention-list').includes(t('Sales')));
""")

    def test_late_responses_are_discarded_after_a_role_change(self):
        self.javascript(HOME + r"""
await boot();dbRuns.clear();workbenchSetComputeStatus('available','tone-ok','');
workbenchShellReady=true;activeTab='home';
const gate=deferred();
route=async path=>path.startsWith('/api/v1/jobs/runs')?gate.promise:path.startsWith('/api/v1/alerts')?alertsEnvelope():path==='/api/v1/databases'?{items:[]}:{};
const first=loadHome();await tick();
currentRole='data_engineer';dbCurrent().selected_role='data_engineer';
dbInventories.set('data_engineer',{items:structuredClone(inventory)});
dbSchemaCache.set(dbSchemaKey('data_engineer','db'),structuredClone(schemas));
for(const schema of dbSchemasFor(dbCurrent()))schema.capabilities.job_sql=true;
route=async path=>path.startsWith('/api/v1/jobs/runs')?jobsEnvelope([{id:'new',job_name:'new-job',status:'succeeded',started_at:'2026-11-18T00:00:00Z'}]):
 path.startsWith('/api/v1/alerts')?alertsEnvelope():path==='/api/v1/databases'?{items:[]}:{};
await loadHome();
gate.resolve(jobsEnvelope([{id:'old',job_name:'old-job',status:'failed',started_at:'2026-11-18T00:00:00Z'}]));
await first;await tick();
assert.ok(homeText('home-jobs-list').includes('new-job'));
assert.ok(!homeText('home-jobs-list').includes('old-job'));
assert.ok(!homeText('home-attention-list').includes('old-job'));
""")

    def test_programmatic_heading_focus_has_no_outline_but_keyboard_focus_does(self):
        self.assertRegex(COMPACT_CSS, r'h1\[tabindex="-1"\]:focus[^{]*\{outline:none\}')
        self.assertIn('outline:none', COMPACT_CSS)
        self.assertRegex(CSS, r':focus-visible\{outline:2px solid var\(--focus\)')
        self.assertIn('button:focus-visible', COMPACT_CSS)
        self.assertIn('[tabindex]:focus-visible', COMPACT_CSS)
        self.assertEqual(MARKUP.ids['main']['attrs']['tabindex'], '-1')

    def test_japanese_and_english_home_copy(self):
        self.javascript(HOME + r"""
await boot();
dbRuns.set('a',{id:'query-1',state:'UNKNOWN',origin:{worksheet_id:'a',request_id:'request-1',principal:dbPrincipal,role:'admin'}});
workbenchSetComputeStatus('paused','','');route=emptySources;
await homeOpen();setLanguage('ja');
assert.equal($('home-review-heading').textContent,'今日の確認は 2件です');
assert.equal($('home-attention-group').hidden,false);
assert.ok(homeText('home-attention-list').includes('照合待ち'));
assert.equal($('home-context-heading').textContent,'Sales');
assert.equal($('home-context-database').textContent,'Sales');
assert.equal($('home-context-schema').textContent,'Mart');
assert.equal($('home-context-role').textContent,'管理者');
assert.equal(t('Recent job runs'),'最近のジョブ実行');
assert.equal(t('Recent objects'),'最近のオブジェクト');
assert.equal(t('Activity'),'アクティビティ');
assert.equal(t('Review required'),'確認が必要');
assert.equal(t('Next actions'),'次のアクション');
assert.ok(nodeText(homeLinks('home-actions-list')[0]).includes('データを探索'));
assert.ok(nodeText(homeLinks('home-actions-list')[1]).includes('SQL ワークシートを開く'));
setLanguage('en');
assert.equal($('home-review-heading').textContent,t('{count} items to review today',{count:2}));
assert.equal(t('Recent job runs'),'Recent job runs');
assert.equal(t('Recent objects'),'Recent objects');
assert.equal(t('Activity'),'Activity');
""")

    def test_mobile_single_column_and_forty_four_pixel_targets(self):
        self.assertRegex(COMPACT_CSS, r'@media\(max-width:600px\)\{[\s\S]*?\.homegrid\{grid-template-columns:minmax\(0,1fr\)')
        self.assertIn('.actionrow,.run,.objectrow,.target.primary.full,.secondaryaction,.textlink,.home-source-errorbutton{min-height:44px}', COMPACT_CSS)
        self.assertIn('.secondaryaction{display:flex;justify-content:center;width:100%}', COMPACT_CSS)
        self.assertIn('.textlink{display:inline-flex;align-items:center;justify-content:center}', COMPACT_CSS)

    def test_recent_objects_empty_row_and_activity_from_existing_runs(self):
        self.javascript(HOME + r"""
await boot();dbRuns.clear();workbenchSetComputeStatus('available','tone-ok','');
route=async path=>path.startsWith('/api/v1/jobs/runs')?jobsEnvelope([
 {id:'run-1',job_name:'daily_sales_refresh',status:'succeeded',started_at:'2026-11-18T00:02:00Z',finished_at:'2026-11-18T00:03:00Z'},
 {id:'run-2',job_name:'customers_sync',status:'failed',started_at:'2026-11-18T00:00:00Z'},
]):path.startsWith('/api/v1/alerts')?alertsEnvelope():{};
inventoryRoute=async()=>({items:[]});
dbObject=null;await homeOpen();
assert.equal(homeLinks('home-objects-list').length,0);
assert.equal($('home-objects-list').children[0].className,'empty');
assert.ok(homeText('home-objects-list').includes(t('No recent objects. Open a table in Data Explorer to see it here.')));
const activity=homeRows('home-activity-list','activity');
assert.equal(activity.length,2);
assert.ok(nodeText(activity[0]).includes('daily_sales_refresh'));
assert.ok(nodeText(activity[0]).includes(t('run completed')));
assert.ok(nodeText(activity[1]).includes(t('run failed')));
assert.equal($('home-activity-date').textContent,'2026-11-18');
const runs=homeRows('home-jobs-list','run');
assert.equal(runs.length,2);
assert.ok(nodeText(runs[0]).includes(t('Succeeded')));
assert.ok(nodeText(runs[1]).includes(t('Failed')));
assert.ok(nodeText(runs[0]).includes('2026-11-18 09:03 JST'));
dbObject={display_name:'Orders',physical_name:'orders',kind:'TABLE',id:'sales-object'};
dbExplorer.database_id='db';dbExplorer.schema_id='schema';
await homeOpen();
const objects=homeLinks('home-objects-list');
assert.equal(objects.length,1);
assert.equal(objects[0].href,'#explore/sales.marts.orders');
assert.ok(nodeText(objects[0]).includes('Orders'));
assert.ok(nodeText(objects[0]).includes('sales.marts'),nodeText(objects[0]));
""")

    def test_legacy_mode_keeps_the_existing_context_guidance(self):
        self.javascript(HOME + r"""
await boot();databaseMode=false;workbenchShellReady=true;activeTab='home';
route=async path=>path==='/api/jobs/runs'?{runs:[]}:path==='/api/alerts'?{alerts:[]}:{};
await loadHome();await tick();
assert.equal($('home-context-fields').hidden,false);
assert.equal($('home-context-empty').hidden,true);
assert.equal($('home-context-heading').textContent,t('Workspace'));
assert.equal($('home-context-database').textContent,'iceberg');
assert.equal($('home-context-warehouse').textContent,'LOCAL');
assert.equal($('home-context-role').textContent,t('Admin'));
assert.equal($('home-jobs-section').hidden,false);
""")

    def test_viewer_keeps_job_and_import_permissions(self):
        self.javascript(HOME + r"""
await boot();currentRole='bi_viewer';dbApplyRole();
const before=calls.length;route=emptySources;inventoryRoute=async()=>({items:[]});
await homeOpen();
assert.equal($('home-jobs-section').hidden,true);
assert.equal(calls.slice(before).some(call=>call.path.startsWith('/api/v1/jobs/runs')||call.path.startsWith('/api/v1/alerts')),false);
assert.equal(homeLinks('home-actions-list').some(node=>node.href==='#ingestion'),false);
assert.equal(homeLinks('home-actions-list').length,2);
""")

    def test_japanese_display_has_no_english_only_labels_in_the_home_chrome(self):
        self.javascript(HOME + r"""
await boot();dbRuns.clear();workbenchSetComputeStatus('available','tone-ok','');
route=emptySources;inventoryRoute=async()=>({items:[]});
dbCurrent().database_id='ghost';dbCurrent().schema_id='missing';
await homeOpen();setLanguage('ja');
for(const id of ['home-review-heading','home-attention-list','home-actions-list','home-jobs-heading','home-jobs-list',
 'home-context-heading','home-context-empty','home-objects-heading','home-objects-list','home-activity-heading','home-activity-list']){
 const text=homeText(id);
 assert.ok(!/Nothing to review|Review required|Next actions|Recent|Activity|No work context|Choose a database|No recent|Job access|Unavailable/.test(text),id+': '+text);
}
""")

    def test_stylesheet_contains_the_mockup_home_grid(self):
        self.assertRegex(COMPACT_CSS, r'\.homegrid\{display:grid;grid-template-columns:minmax\(0,1\.55fr\)minmax\(300px,1fr\);gap:40px;\}')
        self.assertRegex(COMPACT_CSS, r'@media\(max-width:850px\)\{[\s\S]*?\.homegrid,\.jobdetailgrid\{grid-template-columns:1fr;\}')
        self.assertIn('.actionrow{display:grid;grid-template-columns:21pxminmax(0,1fr)auto;', COMPACT_CSS)
        self.assertIn('.target{border:1pxsolidvar(--line);background:var(--surface);border-radius:10px;padding:18px20px;}', COMPACT_CSS)
        self.assertIn('.objectrow{display:flex;gap:10px;align-items:center;padding:10px0;', COMPACT_CSS)
        self.assertIn('.activity{padding:10px0;border-bottom:1pxsolidvar(--line);font-size:11px;}', COMPACT_CSS)


if __name__ == '__main__':
    unittest.main()
