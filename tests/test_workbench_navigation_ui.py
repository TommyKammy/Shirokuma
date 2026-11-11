"""URL reload navigation through production handlers and fresh Node boots."""
import json
import re
from html.parser import HTMLParser
import unittest

import test_workbench_database_ui as harness
from test_workbench_dbt_database_ui import SETUP as DBT_SETUP


class Markup(HTMLParser):
    """Track the static ancestry that gates controls before any JavaScript runs."""
    def __init__(self):
        super().__init__()
        self.stack, self.elements, self.ids = [], [], {}

    def handle_starttag(self, tag, attrs):
        node = dict(tag=tag, attrs=dict(attrs), parents=list(self.stack), text='')
        self.elements.append(node)
        if 'id' in node['attrs']:
            self.ids[node['attrs']['id']] = node
        if tag not in {'meta', 'input', 'link', 'br', 'hr', 'img', 'use', 'path', 'circle', 'rect', 'ellipse'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        for node in self.stack:
            node['text'] += data


MARKUP = Markup()
MARKUP.feed(harness.HTML)
SHELL_IDS = ['workbench-header', 'workbench-nav', 'workbench-main']
BOOT_MARKUP = {key: node['attrs'] for key, node in MARKUP.ids.items() if key.startswith('workbench-')}


URL_SETUP = r"""
// Seed boot attributes from production markup, including the initially selected SQL nav.
for(const [id,attrs] of Object.entries(BOOT_MARKUP)){
 for(const [key,value] of Object.entries(attrs))$(id).setAttribute(key,value===null?true:value);
}
$('tab-warehouse').setAttribute('aria-selected','true');
const shell=()=>SHELL_IDS.map($);
const loading=()=>{
 assert.ok(shell().every(node=>node.hasAttribute('inert')));
 assert.equal($('workbench-boot').hidden,false);
 assert.equal($('workbench-boot-loading').hidden,false);
 assert.equal($('workbench-boot-error').hidden,true);
 assert.equal($('workbench-boot-reload').hidden,true);
};
const released=[];
for(const node of shell()){
 const remove=node.removeAttribute.bind(node);
 node.removeAttribute=key=>{if(key==='inert')released.push(selected());remove(key);};
}

globalThis.location={href:START_URL};
const replacements=[];
globalThis.history={replaceState:(_state,_title,url)=>{location.href=String(url);replacements.push(location.href);}};
const selected=()=>tabs.find(tab=>tab.getAttribute('aria-selected')==='true')?.dataset.tab;
const noPosts=()=>assert.equal(calls.filter(c=>c.options?.method==='POST').length,0);
""".replace('BOOT_MARKUP', json.dumps(BOOT_MARKUP)).replace('SHELL_IDS', json.dumps(SHELL_IDS))


class NavigationUI(unittest.TestCase):
    javascript = harness.DatabaseUI.javascript

    def run_url(self, url, body):
        output = self.javascript(URL_SETUP.replace('START_URL', json.dumps(url)) + body + '\nconsole.log("NAVIGATION_COMPLETE");')
        self.assertTrue(output.endswith('NAVIGATION_COMPLETE\n'), 'Initialization did not complete: ' + output)
        return output.removesuffix('NAVIGATION_COMPLETE\n')

    def test_static_shell_blocks_initial_sql_and_privileged_controls(self):
        # The rule applies to actual static ancestors, before script execution.
        css = re.search(r'<style>([\s\S]*?)</style>', harness.HTML)[1]
        css = re.sub(r'/\*[\s\S]*?\*/', '', css)
        rules = {selector.strip(): body for selector, body in re.findall(r'([^{}]+)\{([^{}]*)\}', css)}
        self.assertRegex(rules['[data-workbench-shell][inert]'], r'visibility\s*:\s*hidden')
        for key, tag in zip(SHELL_IDS, ['header', 'nav', 'main']):
            node = MARKUP.ids[key]
            self.assertEqual(node['tag'], tag)
            self.assertIn('inert', node['attrs'])
            self.assertIn('data-workbench-shell', node['attrs'])
        for key in ['tab-warehouse', 'quick-wake', 'quick-chill', 'role-selector', 'ai-settings-button', 'warehouse', 'jobs', 'catalog']:
            self.assertTrue(any(parent['attrs'].get('id') in SHELL_IDS for parent in MARKUP.ids[key]['parents']), key)
        self.assertEqual(MARKUP.ids['tab-warehouse']['attrs']['aria-selected'], 'true')
        boot = MARKUP.ids['workbench-boot']
        self.assertNotIn('hidden', boot['attrs'])
        self.assertFalse(any('inert' in parent['attrs'] for parent in boot['parents']))
        retry = MARKUP.ids['workbench-boot-reload']
        self.assertEqual(retry['tag'], 'button')
        self.assertEqual(retry['attrs']['type'], 'button')
        self.assertNotIn('tabindex', retry['attrs'])
        self.assertIn('hidden', retry['attrs'])

    def test_held_production_session_and_initialization_release_only_final_route(self):
        for page, final in [('jobs', 'jobs'), ('catalog', 'catalog'), ('db-admin', 'warehouse')]:
            with self.subTest(page=page):
                self.run_url('http://local/#' + page, r"""
loading();
const sessionGate=deferred(),inventoryGate=deferred(),atRestore=deferred(),restoreGate=deferred();
sessionRoute=()=>sessionGate.promise;inventoryRoute=()=>inventoryGate.promise;
const restore=workbenchRestorePage;
workbenchRestorePage=async auth=>{atRestore.resolve();await restoreGate.promise;await restore(auth);loading();};
const pending=startWorkbench();await tick();loading();
assert.equal(selected(),'warehouse');assert.deepEqual(calls.map(c=>c.path),['/api/session']);
assert.equal(dbAdmin.installed,false);assert.deepEqual(released,[]);
sessionGate.resolve(session);await tick();loading();
assert.ok(calls.some(c=>c.path==='/api/v1/databases'));assert.deepEqual(released,[]);
inventoryGate.resolve({items:inventory});await atRestore.promise;loading();assert.deepEqual(released,[]);
restoreGate.resolve();await pending;
assert.equal(selected(),FINAL);assert.deepEqual(released,[FINAL,FINAL,FINAL]);
assert.ok(shell().every(node=>!node.hasAttribute('inert')));assert.equal($('workbench-boot').hidden,true);
assert.equal(dbAdmin.installed,false);assert.equal(calls.some(c=>c.path.startsWith('/api/v1/admin/')),false);noPosts();
""".replace('FINAL', json.dumps(final)))

    def test_boot_failure_stays_unavailable_with_reload(self):
        for lang in ['ja', 'en']:
            for failure in [
                "sessionRoute=async()=>{throw Error('Network unavailable');};",
                "sessionRoute=async()=>({http:401,body:{error:'Denied'}});",
                "sessionRoute=async()=>({malformed:true});",
                "session.authenticated=false;",
                "dbJobsInstall=()=>{throw Error('Initialization failed');};",
            ]:
                with self.subTest(lang=lang, failure=failure):
                    self.run_url('http://local/#jobs', "setLanguage(" + json.dumps(lang) + ");" + failure + r"""
let reloads=0;location.reload=()=>reloads++;
await startWorkbench();
assert.deepEqual(released,[]);assert.ok(shell().every(node=>node.hasAttribute('inert')));
assert.equal($('workbench-boot').hidden,false);assert.equal($('workbench-boot')['aria-busy'],'false');
assert.equal($('workbench-boot-loading').hidden,true);assert.equal($('workbench-boot-error').hidden,false);
assert.equal($('workbench-boot-reload').hidden,false);assert.equal($('workbench-boot-reload').disabled,false);
$('workbench-boot-reload').focus();assert.equal(document.activeElement,$('workbench-boot-reload'));
$('workbench-boot-reload').click();assert.equal(reloads,1);noPosts();
""")

    def test_legacy_anonymous_session_retains_existing_initialization(self):
        self.run_url('http://local/', r"""
databaseMode=false;session={username:null,maximum_role:'admin',authenticated:false};
globalThis.loadJobAlerts=()=>{};
await startWorkbench();
assert.equal($('workbench-boot').hidden,true);
assert.ok(shell().every(node=>!node.hasAttribute('inert')));
assert.equal(calls.filter(c=>c.path==='/api/session').length,1);
assert.equal(dbAdmin.installed,false);noPosts();
""")

    def test_boot_surface_localizes_before_app_initialization(self):
        early = re.search(r'<script data-workbench-language>([\s\S]*?)</script>', harness.HTML)[1]
        for lang in ['ja', 'en']:
            for key in ['workbench-boot-loading', 'workbench-boot-error', 'workbench-boot-reload']:
                copies = [node for node in MARKUP.elements if node['attrs'].get('data-boot-lang') == lang and MARKUP.ids[key] in node['parents']]
                self.assertEqual(len(copies), 1)
                self.assertTrue(copies[0]['text'].strip())
            self.run_url('http://local/#jobs', r"""
const localize=new Function('document','localStorage','navigator',EARLY);
const doc={documentElement:{lang:'en'}};
localize(doc,{getItem:()=>LANG},{language:LANG==='ja'?'en':'ja'});assert.equal(doc.documentElement.lang,LANG);
localize(doc,{getItem:()=>null},{language:LANG+'-JP'});assert.equal(doc.documentElement.lang,LANG);
localize(doc,{getItem:()=>{throw Error('Storage disabled');}},{language:LANG});assert.equal(doc.documentElement.lang,LANG);
setLanguage(LANG);await startWorkbench();assert.equal(document.documentElement.lang,LANG);
assert.equal($('workbench-boot').hidden,true);assert.deepEqual(released,['jobs','jobs','jobs']);noPosts();
""".replace('EARLY', json.dumps(early)).replace('LANG', json.dumps(lang)))

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
