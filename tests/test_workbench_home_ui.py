"""Atlas Home: default route, attention aggregation, independent sources and links."""
import json
import re
import unittest

import test_workbench_database_ui as harness
from test_workbench_navigation_ui import MARKUP, URL_SETUP
from workbench_source import WorkbenchSource

HOME = r"""
globalThis.location=globalThis.location||{href:'http://local/#home'};
globalThis.history=globalThis.history||{replaceState:()=>{}};
const homeLinks=id=>$(id).descendants().filter(node=>node.tagName==='A');
const homeButtons=id=>$(id).descendants().filter(node=>node.tagName==='BUTTON');
const homeErrors=id=>$(id).descendants().filter(node=>node.className==='home-source-error');
const homeText=id=>{const node=$(id);return [node.textContent,...node.descendants().map(child=>child.textContent)].join('');};
const nodeText=node=>[node.textContent,...node.descendants().map(child=>child.textContent)].join('');
async function homeOpen(){
 workbenchShellReady=true;activeTab='home';
 for(const schema of dbSchemasFor(dbCurrent()))schema.capabilities.job_sql=true;
 await loadHome();await tick();
}
const homeContext=()=>structuredClone(dbContext(dbCurrent()));
const jobsEnvelope=runs=>({context:homeContext(),runs});
const alertsEnvelope=(alerts=[],unacknowledged_count=0)=>({context:homeContext(),alerts,unacknowledged_count});
const emptySources=async path=>path.startsWith('/api/v1/jobs/runs')?jobsEnvelope([]):path.startsWith('/api/v1/alerts')?alertsEnvelope():path==='/api/v1/databases'?{items:[]}:{};
"""


class HomeUI(unittest.TestCase):
    javascript = harness.DatabaseUI.javascript

    def run_url(self, url, body):
        output = self.javascript(URL_SETUP.replace('START_URL', json.dumps(url)) + body + '\nconsole.log("HOME_COMPLETE");')
        self.assertTrue(output.endswith('HOME_COMPLETE\n'), 'Initialization did not complete: ' + output)
        return output.removesuffix('HOME_COMPLETE\n')

    def test_static_home_route_is_first_and_initially_current(self):
        home = MARKUP.ids['home']
        self.assertEqual(home['tag'], 'section')
        self.assertNotIn('hidden', home['attrs'])
        self.assertEqual(home['attrs']['aria-labelledby'], 'home-review-heading')
        self.assertIn('hidden', MARKUP.ids['warehouse']['attrs'])
        heading = MARKUP.ids['home-review-heading']
        self.assertEqual(heading['tag'], 'h1')
        self.assertEqual(heading['attrs']['role'], 'status')
        self.assertEqual(heading['attrs']['aria-live'], 'polite')
        self.assertNotIn('home-title', MARKUP.ids)
        self.assertFalse(any(node['tag'] == 'h1' and node.get('text', '').strip() == 'Home'
                             for node in MARKUP.elements if home in node['parents']))
        links = [node for node in MARKUP.elements if 'data-tab' in node['attrs']]
        self.assertEqual(links[0]['attrs']['id'], 'tab-home')
        self.assertEqual(links[0]['attrs']['href'], '#home')
        self.assertEqual(links[0]['attrs']['aria-controls'], 'home')
        self.assertEqual(links[0]['attrs']['aria-current'], 'page')
        current = [node for node in links if node['attrs'].get('aria-current') == 'page']
        self.assertEqual([node['attrs']['id'] for node in current], ['tab-home'])
        mobile = [node for node in MARKUP.elements if 'data-mobile-tab' in node['attrs']]
        self.assertEqual(mobile[0]['attrs']['data-mobile-tab'], 'home')
        self.assertEqual(mobile[0]['attrs']['href'], '#home')
        self.assertEqual(MARKUP.ids['home-open-sql']['attrs']['href'], '#sql')
        self.assertEqual(MARKUP.ids['home-open-explore']['attrs']['href'], '#explore')
        self.assertEqual(MARKUP.ids['home-jobs-all']['attrs']['href'], '#jobs')

    def test_default_screen_is_home_for_empty_and_unknown_hashes(self):
        for url in ['http://local/', 'http://local/#unknown-route', 'http://local/#jobs/../../unknown']:
            with self.subTest(url=url):
                self.run_url(url, r"""
await boot();
assert.equal(selected(),'home');
assert.equal(new URL(location.href).hash,'#home');
assert.equal($('home').hidden,false);assert.equal($('warehouse').hidden,true);
assert.equal(activeTab,'home');
noPosts();
""")

    def test_legacy_mode_defaults_to_home_and_keeps_old_hashes(self):
        self.run_url('http://local/', r"""
databaseMode=false;activate=legacyActivate;
globalThis.loadJobs=()=>{};globalThis.loadStages=()=>{};globalThis.loadMarts=()=>{};
await workbenchRestorePage(session);
assert.equal(activeTab,'home');assert.equal(new URL(location.href).hash,'#home');
location.href='http://local/#warehouse';await workbenchRestorePage(session);
assert.equal(activeTab,'warehouse');assert.equal(new URL(location.href).hash,'#sql');
location.href='http://local/#catalog';await workbenchRestorePage(session);
assert.equal(activeTab,'catalog');assert.equal(new URL(location.href).hash,'#explore');
location.href='http://local/#unknown-route';await workbenchRestorePage(session);
assert.equal(activeTab,'home');assert.equal(new URL(location.href).hash,'#home');
noPosts();
""")

    def test_attention_aggregates_sql_compute_database_jobs_and_alerts(self):
        self.javascript(HOME + r"""
await boot();dbAdmin.platform=true;
workbenchSetComputeStatus('paused','','');
dbRuns.set('a',{id:'query-1',state:'UNKNOWN',origin:{worksheet_id:'a',request_id:'request-1',principal:dbPrincipal,role:'admin'}});
route=async path=>
 path.startsWith('/api/v1/jobs/runs')?jobsEnvelope([
  {id:'run-failed',job_name:'daily_sales_refresh',status:'failed',started_at:'2026-11-18T00:02:00Z',finished_at:'2026-11-18T00:03:00Z'},
  {id:'run-ok',job_name:'customers_sync',status:'succeeded',started_at:'2026-11-18T00:00:00Z'},
 ]):
 path.startsWith('/api/v1/alerts')?alertsEnvelope([{id:'alert-1',message:'Disk warning',job_name:'daily_sales_refresh',acknowledged_at:null}],1):{};
inventoryRoute=async()=>({items:[{id:'db-old',display_name:'Sales',state:'RETIRING'}]});
await homeOpen();
assert.equal($('home-review-heading').textContent,t('{count} items to review today',{count:5}));
const text=homeText('home-attention-list');
for(const expected of ['Worksheet 1','request-1','Compute is chilled','Sales','daily_sales_refresh · Disk warning'])assert.ok(text.includes(t(expected)),expected);
const hrefs=homeLinks('home-attention-list').map(node=>node.href);
assert.deepEqual(hrefs,['#sql','#compute','#databases','#jobs','#jobs']);
assert.equal(homeButtons('home-attention-list').length,0);
for(const status of ['Awaiting reconciliation','Paused','In progress','Failed','Unacknowledged'])
 assert.ok(text.includes(t(status)),status);
assert.equal(homeErrors('home-attention-list').length,0);
""")

    def test_zero_review_and_recent_jobs_use_jst_and_existing_status(self):
        self.javascript(HOME + r"""
await boot();
workbenchSetComputeStatus('available','tone-ok','');
dbRuns.clear();
route=async path=>
 path.startsWith('/api/v1/jobs/runs')?jobsEnvelope([
  {id:'r1',job_name:'first',status:'succeeded',started_at:'2026-11-18T00:02:00Z'},
  {id:'r2',job_name:'second',status:'interrupted',started_at:'2026-11-18T00:00:00Z'},
  {id:'r3',job_name:'third',status:'running',started_at:'2026-11-17T23:59:00Z'},
  {id:'r4',job_name:'fourth',status:'queued',started_at:'2026-11-17T23:00:00Z'},
 ]):
 path.startsWith('/api/v1/alerts')?alertsEnvelope([]):{};
inventoryRoute=async()=>({items:[]});
await homeOpen();
assert.equal($('home-review-heading').textContent,t('Nothing to review today'));
assert.equal(homeText('home-attention-list'),'');
const rows=homeLinks('home-jobs-list');
assert.equal(rows.length,3);
assert.deepEqual(rows.map(node=>node.href),['#jobs','#jobs','#jobs']);
assert.ok(nodeText(rows[0]).includes('2026-11-18 09:02 JST'),nodeText(rows[0]));
assert.ok(nodeText(rows[0]).includes(t('Succeeded')),nodeText(rows[0]));
assert.ok(nodeText(rows[1]).includes(t('Interrupted')),nodeText(rows[1]));
assert.ok(nodeText(rows[2]).includes(t('Running')),nodeText(rows[2]));
""")

    def test_database_job_failure_is_neutral_and_next_load_recovers(self):
        self.javascript(HOME + r"""
await boot();dbAdmin.platform=true;
workbenchSetComputeStatus('available','tone-ok','');
dbRuns.clear();
route=async path=>{
 if(path.startsWith('/api/v1/jobs/runs'))throw Error('runs down');
 if(path.startsWith('/api/v1/alerts'))return alertsEnvelope([{id:'alert-1',message:'Disk warning',acknowledged_at:null}],1);
 return {};
};
inventoryRoute=async()=>({items:[{id:'db-old',display_name:'Sales',state:'RETIRING'}]});
await homeOpen();
const attention=homeText('home-attention-list');
assert.ok(attention.includes('Disk warning'));assert.ok(attention.includes(t('Sales')));
assert.ok(!attention.includes(t('Could not load job runs')));
assert.equal(homeErrors('home-attention-list').length,0);
const jobsText=homeText('home-jobs-list');
assert.ok(jobsText.includes(t('Job access unavailable. Refresh an authorized context.')),jobsText);
assert.equal(homeErrors('home-jobs-list').length,0);
assert.ok(!jobsText.includes(t('Loading…')));
route=async path=>path.startsWith('/api/v1/jobs/runs')?jobsEnvelope([{id:'run-ok',job_name:'recovered',status:'succeeded',started_at:'2026-11-18T00:00:00Z'}]):
 path.startsWith('/api/v1/alerts')?alertsEnvelope([]):{};
inventoryRoute=async()=>({items:[]});
await homeOpen();await tick();
assert.ok(homeText('home-jobs-list').includes('recovered'));
assert.ok(!homeText('home-jobs-list').includes(t('Job access unavailable. Refresh an authorized context.')));
assert.equal(homeErrors('home-attention-list').length,0);
assert.equal(homeErrors('home-jobs-list').length,0);
""")

    def test_database_job_failures_are_neutral_but_database_errors_remain_visible(self):
        self.javascript(HOME + r"""
await boot();dbAdmin.platform=true;dbRuns.clear();
workbenchSetComputeStatus('available','tone-ok','');
route=async path=>{
 if(path.startsWith('/api/v1/alerts'))throw Error('alerts down');
 if(path.startsWith('/api/v1/jobs/runs'))throw Error('runs down');
 return {};
};
inventoryRoute=async()=>{throw Error('databases down');};
await homeOpen();
const text=homeText('home-attention-list');
assert.ok(text.includes(t('Could not load databases')),text);
assert.ok(!text.includes(t('Could not load notifications')),text);
assert.ok(!text.includes(t('Could not load job runs')),text);
assert.equal(homeErrors('home-attention-list').length,1);
assert.equal(homeErrors('home-jobs-list').length,0);
assert.ok(homeText('home-jobs-list').includes(t('Job access unavailable. Refresh an authorized context.')));
setLanguage('ja');
assert.ok(homeText('home-attention-list').includes('データベース一覧を取得できませんでした'));
assert.ok(!homeText('home-attention-list').includes('通知を取得できませんでした'));
assert.ok(!homeText('home-attention-list').includes('ジョブの実行履歴を取得できませんでした'));
""")

    def test_legacy_source_errors_name_the_source_that_failed(self):
        self.javascript(HOME + r"""
await boot();databaseMode=false;workbenchShellReady=true;activeTab='home';
route=async path=>{
 if(path==='/api/alerts')throw Error('alerts down');
 if(path==='/api/jobs/runs')throw Error('runs down');
 return {};
};
await loadHome();await tick();
const text=homeText('home-attention-list');
assert.ok(text.includes(t('Could not load notifications')),text);
assert.ok(text.includes(t('Could not load job runs')),text);
assert.equal(homeErrors('home-attention-list').length,2);
setLanguage('ja');
assert.ok(homeText('home-attention-list').includes('通知を取得できませんでした'));
assert.ok(homeText('home-attention-list').includes('ジョブの実行履歴を取得できませんでした'));
""")

    def test_job_sources_are_not_read_or_rendered_for_a_viewer(self):
        self.javascript(HOME + r"""
await boot();currentRole='bi_viewer';dbApplyRole();
const before=calls.length;
route=emptySources;
await homeOpen();
assert.equal($('home-jobs-section').hidden,true);
assert.equal(calls.slice(before).some(call=>call.path.startsWith('/api/v1/jobs/runs')||call.path.startsWith('/api/v1/alerts')),false);
assert.equal(homeLinks('home-actions-list').some(node=>node.href==='#ingestion'),false);
assert.ok(!homeText('home-actions-list').includes(t('Import a file')));
assert.equal(homeLinks('home-actions-list').length,2);
""")

    def test_retiring_database_requires_database_management_access(self):
        self.javascript(HOME + r"""
await boot();dbAdmin.platform=false;
workbenchSetComputeStatus('available','tone-ok','');dbRuns.clear();
const before=calls.length;
route=emptySources;
await homeOpen();
assert.equal($('home-review-heading').textContent,t('Nothing to review today'));
assert.equal(calls.slice(before).some(call=>call.path==='/api/v1/databases'),false);
assert.equal(homeLinks('home-attention-list').length,0);
dbAdmin.platform=true;
inventoryRoute=async()=>({items:[{id:'db-old',display_name:'Sales',state:'RETIRING'}]});
await homeOpen();
assert.ok(homeText('home-attention-list').includes(t('Sales')));
assert.equal(homeLinks('home-attention-list').filter(node=>node.href==='#databases').length,1);
assert.ok(calls.slice(before).some(call=>call.path==='/api/v1/databases'));
""")

    def test_database_home_reads_v1_jobs_and_alerts_with_the_existing_context(self):
        self.javascript(HOME + r"""
await boot();workbenchSetComputeStatus('available','tone-ok','');dbRuns.clear();
route=async path=>path.startsWith('/api/v1/jobs/runs')?jobsEnvelope([
 {id:'run-1',job_name:'database-job',status:'succeeded',started_at:'2026-11-18T00:02:00Z'},
]):path.startsWith('/api/v1/alerts')?alertsEnvelope([
 {id:'alert-1',message:'Database warning',job_name:'database-job',acknowledged_at:null},
],1):path==='/api/v1/databases'?{items:[]}:{};
await homeOpen();
const jobRequests=calls.filter(call=>call.path.startsWith('/api/v1/jobs/runs'));
const alertRequests=calls.filter(call=>call.path.startsWith('/api/v1/alerts'));
assert.equal(jobRequests.length,1);assert.equal(alertRequests.length,1);
for(const call of [...jobRequests,...alertRequests])assert.match(call.path,/\?context=/);
assert.equal(calls.some(call=>call.path==='/api/jobs/runs'||call.path==='/api/alerts'),false);
assert.ok(homeText('home-jobs-list').includes('database-job'));
assert.ok(homeText('home-attention-list').includes('Database warning'));
assert.equal(homeErrors('home-jobs-list').length,0);
assert.equal(homeErrors('home-attention-list').length,0);
""")

    def test_database_home_context_failure_is_neutral_and_does_not_read_jobs(self):
        self.javascript(HOME + r"""
await boot();workbenchSetComputeStatus('available','tone-ok','');dbRuns.clear();
dbCurrent().database_id='';dbCurrent().schema_id='';
const before=calls.length;
route=async path=>{
 if(path.startsWith('/api/v1/jobs/runs')||path.startsWith('/api/v1/alerts'))throw Error('must not be read');
 return {};
};
inventoryRoute=async()=>({items:[]});
await homeOpen();
assert.equal(calls.slice(before).some(call=>call.path.startsWith('/api/v1/jobs/runs')||call.path.startsWith('/api/v1/alerts')),false);
const text=homeText('home-jobs-list');
assert.ok(text.includes(t('Job access unavailable. Refresh an authorized context.')),text);
assert.ok(!text.includes(t('Loading…')));assert.equal(homeErrors('home-jobs-list').length,0);
assert.equal(homeErrors('home-attention-list').length,0);
""")

    def test_late_responses_are_discarded_after_role_change(self):
        self.javascript(HOME + r"""
await boot();
workbenchShellReady=true;activeTab='home';
for(const schema of dbSchemasFor(dbCurrent()))schema.capabilities.job_sql=true;
const gate=deferred();
route=async path=>path.startsWith('/api/v1/jobs/runs')?gate.promise:path.startsWith('/api/v1/alerts')?alertsEnvelope([]):path==='/api/v1/databases'?{items:[]}:{};
const first=loadHome();await tick();
currentRole='data_engineer';dbCurrent().selected_role='data_engineer';
dbInventories.set('data_engineer',{items:structuredClone(inventory)});
dbSchemaCache.set(dbSchemaKey('data_engineer','db'),structuredClone(schemas));
for(const schema of dbSchemasFor(dbCurrent()))schema.capabilities.job_sql=true;
route=async path=>path.startsWith('/api/v1/jobs/runs')?jobsEnvelope([{id:'new',job_name:'new-job',status:'succeeded',started_at:'2026-11-18T00:00:00Z'}]):
 path.startsWith('/api/v1/alerts')?alertsEnvelope([]):path==='/api/v1/databases'?{items:[]}:{};
await loadHome();
gate.resolve(jobsEnvelope([{id:'old',job_name:'old-job',status:'failed',started_at:'2026-11-18T00:00:00Z'}]));
await first;await tick();
assert.ok(homeText('home-jobs-list').includes('new-job'));
assert.ok(!homeText('home-jobs-list').includes('old-job'));
assert.ok(!homeText('home-attention-list').includes('old-job'));
""")

    def test_context_actions_links_and_recent_objects_require_real_state(self):
        self.javascript(HOME + r"""
await boot();
dbCurrent().database_id='db';dbCurrent().schema_id='schema';
dbExplorer.database_id='db';dbExplorer.schema_id='schema';
dbObject={display_name:'Orders',physical_name:'orders',kind:'TABLE'};
route=emptySources;
await homeOpen();
assert.equal($('home-context-database').textContent,'Sales');
assert.equal($('home-context-schema').textContent,'Mart');
assert.equal($('home-context-warehouse').textContent,'LOCAL');
assert.equal($('home-context-role').textContent,t('Admin'));
assert.equal($('home-objects-section').hidden,false);
assert.ok(homeText('home-objects-list').includes('Orders'));
assert.equal(homeLinks('home-actions-list').map(node=>node.href).join('|'),'#explore|#sql|#ingestion');
dbObject=null;await homeOpen();
assert.equal($('home-objects-section').hidden,true);
assert.equal(homeLinks('home-objects-list').length,0);
dbCurrent().database_id='';dbCurrent().schema_id='';await homeOpen();
assert.equal($('home-context-database').textContent,t('Not selected'));
assert.equal($('home-context-schema').textContent,t('Not selected'));
""")

    def test_compute_attention_uses_the_same_chip_state_and_omits_unsupported(self):
        self.javascript(HOME + r"""
await boot();dbRuns.clear();route=emptySources;
const cases=[
 ['unknown','Reconcile compute state','Unknown'],
 ['switching','Confirm the compute switch result','Switching'],
 ['paused','Compute is chilled','Paused'],
 ['stale','Recheck compute state','Stale'],
];
for(const [state,title,status] of cases){
 workbenchSetComputeStatus(state);
 await homeOpen();
 const links=homeLinks('home-attention-list');
 assert.equal(links.length,1,state);assert.equal(links[0].href,'#compute');
 assert.ok(homeText('home-attention-list').includes(t(title)),state);
 assert.ok(homeText('home-attention-list').includes(t(status)),state);
}
for(const state of ['available','unsupported']){
 workbenchSetComputeStatus(state);
 await homeOpen();
 assert.equal(homeLinks('home-attention-list').length,0,state);
 assert.equal($('home-review-heading').textContent,t('Nothing to review today'),state);
}
""")

    def test_japanese_and_english_copy_share_the_attention_and_action_vocabulary(self):
        self.javascript(HOME + r"""
await boot();
dbRuns.set('a',{id:'query-1',state:'UNKNOWN',origin:{worksheet_id:'a',request_id:'request-1',principal:dbPrincipal,role:'admin'}});
workbenchSetComputeStatus('paused','','');
route=emptySources;
await homeOpen();setLanguage('ja');
assert.equal($('home-review-heading').textContent,'今日の確認は 2 件です');
assert.ok(homeText('home-attention-list').includes('照合待ち'));
assert.ok(homeText('home-attention-list').includes('再送せずに状態を確認'));
assert.ok(homeText('home-attention-list').includes('コンピュートが休止中'));
assert.ok(nodeText(homeLinks('home-actions-list')[0]).includes('データを探索'));
assert.ok(nodeText(homeLinks('home-actions-list')[1]).includes('SQL ワークシートを開く'));
setLanguage('en');
assert.equal($('home-review-heading').textContent,t('{count} items to review today',{count:2}));
assert.ok(homeText('home-attention-list').includes('Awaiting reconciliation'));
assert.ok(homeText('home-attention-list').includes('Compute is chilled'));
""")

    def test_quality_failure_is_not_inferred_without_a_persistent_source(self):
        self.javascript(HOME + r"""
await boot();dbRuns.clear();workbenchSetComputeStatus('available','tone-ok','');
route=async path=>{
 if(path.includes('marts'))throw Error('mart source must not be guessed');
 return emptySources(path);
};
await homeOpen();
assert.equal(calls.some(call=>call.path==='/api/marts'||call.path.startsWith('/api/v1/marts')),false);
assert.ok(!homeText('home-attention-list').includes('品質'));
assert.equal($('home-review-heading').textContent,t('Nothing to review today'));
""")

    def test_home_layout_and_tap_targets_are_declared_in_the_stylesheet(self):
        css = re.sub(r'/\*[\s\S]*?\*/', '', WorkbenchSource(harness.HTML).css)
        self.assertRegex(css, r'\.homegrid\{display:grid;grid-template-columns:minmax\(0,1\.55fr\) minmax\(280px,\.9fr\)')
        self.assertRegex(css, r'@media\(max-width:900px\)\{[\s\S]*?\.homegrid\{grid-template-columns:minmax\(0,1fr\)')
        self.assertRegex(css, r'@media\(max-width:600px\)\{[\s\S]*?\.actionrow,.run,.objectrow,.secondaryaction,.home-context a\.primary\.full,.textlink,.home-source-error button\{min-height:44px\}')
        self.assertRegex(css, r'\.actionrow\{[^}]*min-height:44px[^}]*min-width:0')
        self.assertRegex(css, r':focus-visible\{outline:2px solid var\(--focus\)')


if __name__ == '__main__':
    unittest.main()
