"""URL reload navigation through production handlers and fresh Node boots."""
import json
import unittest

import test_workbench_database_ui as harness
from test_workbench_dbt_database_ui import SETUP as DBT_SETUP


URL_SETUP = r"""
globalThis.location={href:START_URL};
const replacements=[];
globalThis.history={replaceState:(_state,_title,url)=>{location.href=String(url);replacements.push(location.href);}};
const selected=()=>tabs.find(tab=>tab.getAttribute('aria-selected')==='true')?.dataset.tab;
const noPosts=()=>assert.equal(calls.filter(c=>c.options?.method==='POST').length,0);
"""


class NavigationUI(unittest.TestCase):
    javascript = harness.DatabaseUI.javascript

    def run_url(self, url, body):
        return self.javascript(URL_SETUP.replace('START_URL', json.dumps(url)) + body)

    def test_all_pages_roundtrip_in_a_fresh_boot(self):
        for page in ['warehouse', 'catalog', 'jobs', 'ingestion', 'transformation',
                     'bears', 'db-admin', 'pawprints', 'compute']:
            with self.subTest(page=page):
                url = self.run_url('http://local/workbench?keep=query#warehouse?keep=fragment',
                    "session.platform_admin=true;await boot();await activate($('tab-" + page + "'));"
                    "noPosts();console.log(location.href);").strip()
                self.assertIn('#' + page + '?keep=fragment', url)
                self.run_url(url, "session.platform_admin=true;await boot();"
                    "assert.equal(selected()," + json.dumps(page) + ");"
                    "assert.equal(new URL(location.href).searchParams.get('keep'),'query');noPosts();")

    def test_workspace_switches_and_background_renders(self):
        self.run_url('http://local/#catalog', r"""
await boot();assert.equal(selected(),'catalog');assert.equal(dbWorkspacePane,'details');
dbWireWorkspaceTabs();assert.equal(location.href,'http://local/#catalog');
await $('worksheet-'+worksheetId).onclick();assert.equal(selected(),'warehouse');assert.equal(new URL(location.href).hash,'#warehouse');
$('workspace-details').onclick();assert.equal(new URL(location.href).hash,'#catalog');
await activate($('tab-jobs'));renderWorksheets();dbShowWorkspace('worksheet');
assert.equal(new URL(location.href).hash,'#jobs');assert.equal(selected(),'jobs');noPosts();
""")

    def test_denied_admin_unknown_and_malformed_routes(self):
        for route in ['db-admin', 'unknown', '%E0%A4%A', 'jobs/../../db-admin', 'jobs%3Fcreation=x']:
            with self.subTest(route=route):
                self.run_url('http://local/#' + route + '?creation=old&dbt_db=db&dbt_plan=old', r"""
await boot();assert.equal(selected(),'warehouse');assert.equal(new URL(location.href).hash,'#warehouse');
assert.equal(dbAdmin.installed,false);assert.equal(calls.some(c=>c.path.startsWith('/api/v1/admin/')),false);noPosts();
""")

    def test_hidden_disabled_and_unauthenticated_restore(self):
        for constraint in ["$('tab-jobs').hidden=true", "$('tab-jobs').disabled=true"]:
            self.run_url('http://local/#jobs', constraint + ";await boot();assert.equal(selected(),'warehouse');noPosts();")
        self.run_url('http://local/#db-admin', r"""
session.authenticated=false;session.platform_admin=true;await boot();
assert.notEqual(selected(),'db-admin');assert.equal(dbAdmin.installed,false);
assert.equal(calls.some(c=>c.path.startsWith('/api/v1/admin/')),false);noPosts();
""")

    def test_explicit_page_wins_over_unrelated_details_and_clears_them(self):
        for page in ['jobs', 'catalog', 'warehouse', 'ingestion']:
            url = self.run_url('http://local/?keep=q&creation=old&creation_environment=lab#' + page +
                '?keep=f&dbt_db=db&dbt_plan=old&creation=old',
                "session.platform_admin=true;await boot();assert.equal(selected()," + json.dumps(page) + ");"
                "assert.equal(calls.some(c=>c.path.includes('/dbt/plans/old')||c.path.includes('/creations/old')),false);"
                "noPosts();console.log(location.href);").strip()
            self.assertEqual(url, 'http://local/?keep=q#' + page + '?keep=f')
            self.run_url(url, "session.platform_admin=true;await boot();assert.equal(selected()," + json.dumps(page) + ");noPosts();")

    def test_details_are_restored_only_on_their_explicit_page(self):
        for page in ['db-admin', 'transformation']:
            self.run_url('http://local/?creation=old#' + page + '?dbt_db=db&dbt_plan=old', r"""
session.platform_admin=true;let dbtRestores=0;dbDbtRestore=async()=>{dbtRestores++;};
await boot();noPosts();
""" + ("assert.equal(selected(),'db-admin');assert.equal(dbtRestores,0);assert.equal(dbDbtURL().database,null);" if page == 'db-admin' else
       "assert.equal(selected(),'transformation');assert.equal(dbtRestores,1);assert.equal(dbCreationURL().id,null);assert.equal(calls.some(c=>c.path.startsWith('/api/v1/admin/')),false);"))

    def test_legacy_creation_links_and_leaving_reload(self):
        for link in ['?creation=old&creation_environment=lab&keep=q#?keep=f',
                     '?keep=q#creation=old&creation_environment=lab&keep=f',
                     '?keep=q#db-admin?creation=old&creation_environment=lab&keep=f',
                     '?keep=q#worksheet-saved?creation=old&creation_environment=lab&keep=f',
                     '?keep=q&creation=old&creation_environment=lab#sql?keep=f']:
            url = self.run_url('http://local/' + link, r"""
session.platform_admin=true;
const creationPath='/api/v1/admin/database-creations';
route=async path=>path===creationPath+'/capabilities'?{environment_id:'lab',warehouse_id:'LOCAL',configured:true,available:true}:
 path===creationPath+'/old'?{creation_id:'old',database_id:'db',revision:1,sql_catalog:'sales',lifecycle:'READY',progress_code:'READY',plans:[],next_actions:[]}:
 path===creationPath?{creations:[],cursor:null}:{};
await boot();assert.equal(selected(),'db-admin');assert.equal(dbCreation.process.creation_id,'old');
assert.ok(calls.some(c=>c.path===creationPath+'/old'));
assert.equal(dbCreationURL().id,'old');await activate($('tab-ingestion'));noPosts();console.log(location.href);
""").strip()
            self.assertNotIn('creation', url)
            self.run_url(url, "session.platform_admin=true;await boot();assert.equal(selected(),'ingestion');noPosts();")

    def test_dbt_links_restore_only_on_transformation_and_clear_on_departure(self):
        for prefix in ['', 'transformation?']:
            for kind in ['plan', 'run']:
                url = self.run_url('http://local/?keep=q#' + prefix + 'dbt_db=db&dbt_' + kind + '=' + kind + '-1&keep=f', DBT_SETUP + r"""
const baseRoute=route;
route=async(path,options)=>{
 if(/\/(plans|runs)\//.test(path)){
  const context=JSON.parse(new URL('http://local'+path).searchParams.get('context'));
  plan=makePlan(context);run=makeRun(context);
 }
 return baseRoute(path,options);
};
await boot();assert.equal(selected(),'transformation');assert.equal(p.plan.id,'plan-1');
""" + ("assert.equal(p.run.id,'run-1');" if kind == 'run' else '') + r"""
assert.ok(calls.some(c=>/\/(plans|runs)\//.test(c.path)));
await activate($('tab-jobs'));assert.equal(dbDbtURL().database,null);noPosts();console.log(location.href);
""").strip()
                self.assertEqual(url, 'http://local/?keep=q#jobs?keep=f')
                self.run_url(url, "await boot();assert.equal(selected(),'jobs');noPosts();")

    def test_publication_only_link_restores_get_only_in_fresh_boot(self):
        self.run_url('http://local/#transformation?keep=f&dbt_db=db&dbt_publication=published-1',
                     DBT_SETUP + r"""
const baseRoute=route;
route=async(path,options)=>{
 assert.equal(options?.method,undefined);
 assert.equal(/\/runs\//.test(path),false);
 if(path.includes('/publications/published-1?')){
  plan=makePlan(p.context);
  pubs['published-1']={id:'published-1',kind:'publication',actor:dbDbtOwner(),context:structuredClone(p.context),state:'PUBLISHED',origin:'sql_wap',run_id:'run-1',parent_run_id:'run-1',plan_id:plan.id,node_id:ids[2],source_digest:plan.source_digest};
 }
 return baseRoute(path,options);
};
await boot();assert.equal(selected(),'transformation');assert.equal(p.standalone.id,'published-1');
assert.equal(p.run,null);assert.equal(p.plan.id,'plan-1');noPosts();
await activate($('tab-ingestion'));assert.equal(new URL(location.href).hash,'#ingestion?keep=f');
clearTimeout(p.timer);
""")

    def test_legacy_mode_reuses_routes_without_changing_admin_access(self):
        self.run_url('http://local/#jobs?keep=f', r"""
databaseMode=false;activate=legacyActivate;
globalThis.loadJobs=()=>{};globalThis.loadStages=()=>{};globalThis.loadMarts=()=>{};
await workbenchRestorePage(session);assert.equal(activeTab,'jobs');assert.equal(new URL(location.href).hash,'#jobs?keep=f');
$('tab-db-admin').hidden=true;location.href='http://local/#db-admin';
await workbenchRestorePage(session);assert.equal(activeTab,'warehouse');noPosts();
""")


if __name__ == '__main__':
    unittest.main()
