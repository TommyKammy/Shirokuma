"""Atlas shell: navigation, hash routes, command palette, messages and time helpers."""
import json
import re
import unittest

import test_workbench_database_ui as harness
from test_workbench_navigation_ui import MARKUP, URL_SETUP
from workbench_source import WorkbenchSource


class AtlasShellUI(unittest.TestCase):
    javascript = harness.DatabaseUI.javascript

    def run_url(self, url, body):
        output = self.javascript(URL_SETUP.replace('START_URL', json.dumps(url)) + body + '\nconsole.log("NAVIGATION_COMPLETE");')
        self.assertTrue(output.endswith('NAVIGATION_COMPLETE\n'), 'Initialization did not complete: ' + output)
        return output.removesuffix('NAVIGATION_COMPLETE\n')

    def test_navigation_groups_labels_routes_and_nowrap(self):
        sidebar = MARKUP.ids['workbench-nav']
        self.assertEqual(sidebar['attrs']['class'], 'sidebar')
        brand = next(node for node in MARKUP.elements
                     if node['tag'] == 'a' and sidebar in node['parents']
                     and 'brand' in (node['attrs'].get('class') or '').split())
        self.assertEqual(brand['attrs']['href'], '#home')
        self.assertIn('Shirokuma', brand['text'])
        self.assertIn('Data workspace', brand['text'])
        navlist = next(node for node in MARKUP.elements
                       if node['tag'] == 'nav' and sidebar in node['parents']
                       and 'navlist' in (node['attrs'].get('class') or '').split())
        links = [node for node in MARKUP.elements
                 if navlist in node['parents'] and 'data-tab' in node['attrs']]
        self.assertEqual([node['attrs']['data-tab'] for node in links], [
            'home', 'catalog', 'warehouse', 'ingestion', 'transformation', 'jobs', 'bears',
            'db-admin', 'pawprints', 'compute',
        ])
        self.assertEqual([node['attrs']['href'] for node in links], [
            '#home', '#explore', '#sql', '#ingestion', '#transformation', '#jobs', '#bears',
            '#databases', '#audit', '#compute',
        ])
        self.assertEqual([node['attrs']['id'] for node in links], [
            'tab-home', 'tab-catalog', 'tab-warehouse', 'tab-ingestion', 'tab-transformation',
            'tab-jobs', 'tab-bears', 'tab-db-admin', 'tab-pawprints', 'tab-compute',
        ])
        icons = [next(n for n in MARKUP.elements
                      if n['parents'] and n['parents'][-1] is node
                      and 'icon' in (n['attrs'].get('class') or '').split())['text'].strip()
                 for node in links]
        self.assertEqual(icons, ['⌂', '▦', '⌘', '↓', '⇄', '◷', '✧', '▱', '≡', '◈'])
        navtitles = [node['text'].strip() for node in MARKUP.elements
                     if 'navtitle' in (node['attrs'].get('class') or '').split()
                     and sidebar in node['parents']]
        self.assertIn('Administration', navtitles)
        self.assertIn('Favorites', navtitles)
        self.assertIn('Workspace', navtitles)
        self.assertIn('workbench-favorites', MARKUP.ids)
        self.assertIn('hidden', MARKUP.ids['workbench-favorites']['attrs'])
        self.assertEqual(MARKUP.ids['nav-workspace-context']['tag'], 'a')
        self.assertEqual(MARKUP.ids['nav-workspace-context']['attrs']['href'], '#explore')
        self.assertEqual(MARKUP.ids['rail-warehouse']['attrs']['href'], '#compute')
        self.assertIn('theme-light', MARKUP.ids)
        self.assertIn('theme-dark', MARKUP.ids)
        self.assertEqual(MARKUP.ids['theme-light']['attrs']['aria-pressed'], 'true')
        self.assertEqual(MARKUP.ids['theme-dark']['attrs']['aria-pressed'], 'false')
        mobile = [node for node in MARKUP.elements if 'data-mobile-tab' in node['attrs']]
        self.assertEqual(len(mobile), 10)
        self.assertIn('workbench-mobile-nav', MARKUP.ids)
        self.assertEqual([node['attrs']['data-mobile-tab'] for node in mobile], [
            'home', 'catalog', 'warehouse', 'ingestion', 'transformation', 'jobs', 'bears',
            'db-admin', 'pawprints', 'compute',
        ])
        topbar = MARKUP.ids['workbench-header']
        self.assertEqual(topbar['attrs']['class'], 'topbar')
        self.assertEqual(MARKUP.ids['workbench-breadcrumb']['tag'], 'span')
        self.assertEqual(MARKUP.ids['database-compute-status']['attrs']['class'], 'sample compute-chip')
        self.assertIn('searchbutton', MARKUP.ids['workbench-command-button']['attrs']['class'].split())
        self.assertIn('primary', MARKUP.ids['workbench-open-sql']['attrs']['class'].split())
        command = MARKUP.ids['command']
        self.assertEqual(command['tag'], 'dialog')
        self.assertIn('command-search', MARKUP.ids)
        self.assertIn('command-list', MARKUP.ids)
        self.assertIn('command-empty', MARKUP.ids)
        self.assertEqual(MARKUP.ids['command-list']['attrs']['class'], 'commandlist')
        css = WorkbenchSource(harness.HTML).css
        css = re.sub(r'/\*[\s\S]*?\*/', '', css)
        rules = {selector.strip(): body for selector, body in re.findall(r'([^{}]+)\{([^{}]*)\}', css)}
        self.assertRegex(rules['.navlink'], r'white-space\s*:\s*nowrap')
        self.assertRegex(css, r'\.app\s*\{[^}]*grid-template-columns\s*:\s*244px')
        self.assertRegex(css, r'\.mobilemenu\s*\{\s*display\s*:\s*none')
        self.assertRegex(css, r'\.topbar\s*\{[^}]*min-height\s*:\s*77px')
        self.assertRegex(css, r'\.panel\s*\{[^}]*border-radius\s*:\s*8px')
        self.assertRegex(rules['.impact'], r'border-left')
        self.assertRegex(rules['.impact.neutral'], r'border-left-color')
        danger = next(body for selector, body in rules.items() if 'button.primary.danger' in selector)
        self.assertRegex(danger, r'background\s*:\s*var\(--(?:bad|red)\)')
        primary = rules['button.primary,a.primary']
        self.assertRegex(primary, r'background\s*:\s*var\(--button\)')
        self.assertRegex(css, r'@media\(max-width:600px\)\{[\s\S]*?\.sidebar\{display:none\}')
        self.assertRegex(css, r'@media\(max-width:600px\)\{[\s\S]*?\.mobilemenu\{display:block')
        command_button = MARKUP.ids['workbench-command-button']
        self.assertEqual(command_button['attrs']['aria-label'], 'Search and commands')
        self.assertEqual(command_button['attrs']['aria-expanded'], 'false')
        self.assertEqual(command_button['attrs']['aria-controls'], 'command')
        self.assertEqual(MARKUP.ids['workbench-open-sql']['attrs']['href'], '#sql')

    def test_legacy_hashes_are_replaced_and_details_are_preserved(self):
        self.run_url('http://local/#warehouse?keep=1', r"""
await boot();workbenchShellReady=true;workbenchSession=session;assert.equal(selected(),'warehouse');
assert.equal(new URL(location.href).hash,'#sql?keep=1');
workbenchHistoryHref=location.href;
location.href='http://local/#catalog';await workbenchHistoryRestore();
assert.equal(selected(),'catalog');assert.equal(new URL(location.href).hash,'#explore');
location.href='http://local/#pawprints/old-event?keep=2';await workbenchHistoryRestore();
assert.equal(selected(),'pawprints');assert.equal(new URL(location.href).hash,'#audit/old-event?keep=2');
noPosts();
""")

    def test_db_admin_legacy_hash_and_route_mapping_helpers(self):
        self.run_url('http://local/#db-admin', r"""
session.platform_admin=true;await boot();
assert.equal(selected(),'db-admin');assert.equal(new URL(location.href).hash,'#databases');
for(const [page,tab] of [['warehouse','warehouse'],['catalog','catalog'],['pawprints','pawprints'],['db-admin','db-admin'],['sql','warehouse'],['explore','catalog'],['audit','pawprints'],['databases','db-admin']])
 assert.equal(workbenchTabForPage(page),tab,page);
for(const [tab,route] of [['warehouse','sql'],['catalog','explore'],['pawprints','audit'],['db-admin','databases']])
 assert.equal(workbenchRouteForTab(tab),route,tab);
assert.equal(workbenchTabForPage('jobs/../../db-admin'),'');
noPosts();
""")

    def test_command_palette_pages_actions_filters_and_keyboard(self):
        self.javascript(r"""
await boot();
globalThis.location={href:'http://local/#sql'};
$('command').showModal=function(){this.open=true;};
$('command').close=function(){this.open=false;};
catalogMetadata={marts:[['orders']]};
dbDatabases=[
 {id:'db',display_name:'Sales',sql_catalog:'sales',state:'READY',capabilities:{browse:true}},
 {id:'old',display_name:'Retired DB',sql_catalog:'retired',state:'RETIRED',capabilities:{browse:true}},
];
const schema={id:'schema',sql_name:'marts',display_name:'Marts'};
dbSchemaCache.set(dbSchemaKey(currentRole,'db'),[schema]);
dbTreeObjects.set(JSON.stringify([dbSchemaKey(currentRole,'db'),'schema','']),{items:[
 {id:'obj',ref:{database_id:'db',schema_id:'schema',id:'obj'},physical_name:'orders',display_name:'Orders',capabilities:{columns:true}},
 {id:null,ref:null,physical_name:'hidden',display_name:'Hidden'},
],next_cursor:null});
dbSchemaCache.set(dbSchemaKey(currentRole,'old'),[schema]);
dbTreeObjects.set(JSON.stringify([dbSchemaKey(currentRole,'old'),'schema','']),{items:[
 {id:'old-obj',ref:{database_id:'old',schema_id:'schema',id:'old-obj'},physical_name:'orders',display_name:'Orders',capabilities:{columns:true}},
],next_cursor:null});
$('command-search').value='';
workbenchCommandRender();
const entries=()=>[...$('command-list').children];
assert.deepEqual(entries().map(node=>node.children[1].textContent),Array(10).fill(t('Page')+' →').concat(Array(4).fill(t('Action')+' →')));
assert.deepEqual(entries().slice(0,10).map(node=>node.children[0].textContent),[
 'Home','Data Explorer','SQL Worksheets','Ingestion & Stages','Transformation','Jobs','AI Bears','Database Management','Audit log','Compute & Governance',
]);
$('command-search').value='orders';
route=async path=>path.startsWith('/api/v1/objects/search?')?{items:[
 {database_id:'db',sql_catalog:'sales',database_display_name:'Sales',schema_id:'schema',schema_sql_name:'marts',name:'orders',kind:'TABLE'},
],truncated:false,unavailable_databases:[]}:{};
workbenchCommandRender();workbenchCommandCancelTimer();await workbenchCommandSearchTables('orders');workbenchCommandPaint();
const labels=entries().map(node=>node.children[0].textContent);
assert.ok(labels.includes('sales.marts.orders'),labels.join('|'));
assert.ok(!labels.includes('marts.orders'),labels.join('|'));
assert.ok(!labels.some(label=>label.includes('hidden')));
assert.ok(!labels.some(label=>label.includes('Retired DB')));
assert.ok(!labels.some(label=>label.includes('retired.marts.orders')));
setLanguage('ja');
for(const [query,label] of [['監査','Audit log'],['ログ','Audit log'],['ＳＱＬ','SQL Worksheets'],['audit','Audit log']]){
 $('command-search').value=query;workbenchCommandRender();workbenchCommandCancelTimer();
 assert.ok(entries().some(node=>node.children[0].textContent===t(label)),query+' → '+entries().map(node=>node.children[0].textContent).join('|'));
}
setLanguage('en');
$('workbench-command-button').focus();workbenchCommandOpen();
assert.equal($('command').open,true);assert.equal(document.activeElement,$('command-search'));
workbenchCommandKeydown({key:'ArrowDown',preventDefault(){}});
assert.equal(document.activeElement,entries()[0]);
workbenchCommandKeydown({key:'ArrowDown',preventDefault(){}});
assert.equal(document.activeElement,entries()[1]);
workbenchCommandKeydown({key:'ArrowUp',preventDefault(){}});
assert.equal(document.activeElement,entries()[0]);
$('command-search').value='SQL Worksheets';workbenchCommandRender();
workbenchCommandKeydown({key:'Enter',preventDefault(){}});
assert.equal(activeTab,'warehouse');assert.equal($('command').open,false);
assert.equal(document.activeElement,$('workbench-command-button'));
$('workbench-command-button').focus();workbenchCommandOpen();
workbenchCommandKeydown({key:'Escape',preventDefault(){}});
assert.equal($('command').open,false);assert.equal(document.activeElement,$('workbench-command-button'));
$('command-search').value='zzzz';workbenchCommandRender();workbenchCommandCancelTimer();workbenchCommandSearching=false;workbenchCommandPaint();
assert.equal(entries().length,0);assert.equal($('command-empty').hidden,false);assert.equal($('command-empty').textContent,t('No matching destinations.'));
""")

    def test_header_overlays_close_each_other_and_escape(self):
        self.javascript(r"""
await boot();
$('command').showModal=function(){this.open=true;};
$('command').close=function(){this.open=false;};
$('workbench-alerts-popover').hidden=true;$('user-menu').open=false;
$('workbench-alerts-button').onclick();
assert.equal($('workbench-alerts-popover').hidden,false);
assert.equal($('workbench-alerts-button').getAttribute('aria-expanded'),'true');
$('user-menu-summary').focus();workbenchSetUserMenuOpen(true);
assert.equal($('workbench-alerts-popover').hidden,true);
assert.equal($('workbench-alerts-button').getAttribute('aria-expanded'),'false');
assert.equal($('user-menu').open,true);assert.equal($('user-menu-summary').getAttribute('aria-expanded'),'true');
$('workbench-command-button').focus();workbenchCommandOpen();
assert.equal($('user-menu').open,false);assert.equal($('user-menu-summary').getAttribute('aria-expanded'),'false');
assert.equal($('workbench-command-button').getAttribute('aria-expanded'),'true');
workbenchCommandKeydown({key:'Escape',preventDefault(){}});
assert.equal($('workbench-command-button').getAttribute('aria-expanded'),'false');
$('user-menu-summary').focus();workbenchSetUserMenuOpen(true);
assert.equal(workbenchOverlayEscape({key:'Escape'}),true);
assert.equal($('user-menu').open,false);assert.equal($('user-menu-summary').getAttribute('aria-expanded'),'false');
assert.equal(document.activeElement,$('user-menu-summary'));
$('workbench-alerts-button').focus();workbenchToggleAlerts(true);
assert.equal(workbenchOverlayEscape({key:'Escape'}),true);
assert.equal($('workbench-alerts-popover').hidden,true);assert.equal(document.activeElement,$('workbench-alerts-button'));
$('workbench-alerts-button').onclick();
workbenchOverlayOutsideClick({});
assert.equal($('workbench-alerts-popover').hidden,true);
$('user-menu-summary').focus();workbenchSetUserMenuOpen(true);
workbenchOverlayOutsideClick({});
assert.equal($('user-menu').open,false);assert.equal(document.activeElement,$('user-menu-summary'));
$('user-menu-summary').focus();workbenchSetUserMenuOpen(true);
$('workbench-alerts-button').onclick();
assert.equal($('user-menu').open,true);assert.equal($('user-menu-summary').getAttribute('aria-expanded'),'true');
assert.equal($('workbench-alerts-popover').hidden,false);
workbenchSetMobileOpen(true);assert.equal($('workbench-mobile-nav').open,true);
assert.equal($('workbench-mobile-summary').getAttribute('aria-expanded'),'true');
$('workbench-alerts-button').onclick();
assert.equal($('workbench-mobile-nav').open,false);
assert.equal($('workbench-mobile-summary').getAttribute('aria-expanded'),'false');
""")

    def test_mobile_menu_tracks_current_page_and_categories(self):
        self.javascript(r"""
const mobileTabs=['home','catalog','warehouse','ingestion','transformation','jobs','bears','db-admin','pawprints','compute'];
const mobileLinks=mobileTabs.map(tab=>{const link=document.createElement('a');link.dataset.mobileTab=tab;return link;});
const baseQuery=document.querySelectorAll;
document.querySelectorAll=selector=>selector==='[data-mobile-tab]'?mobileLinks:baseQuery(selector);
await boot();
const current=()=>mobileLinks.filter(link=>link.getAttribute('aria-current')==='page').map(link=>link.dataset.mobileTab);
assert.deepEqual(current(),['warehouse']);
await activate($('tab-pawprints'));assert.deepEqual(current(),['pawprints']);
await activate($('tab-catalog'));assert.deepEqual(current(),['catalog']);
dbShowWorkspace('worksheet');assert.deepEqual(current(),['warehouse']);
dbShowWorkspace('details');assert.deepEqual(current(),['catalog']);
setLanguage('ja');assert.equal(mobileLinks.find(link=>link.dataset.mobileTab==='catalog').getAttribute('aria-current'),'page');
""")

    def test_messages_belong_to_their_screen_and_handoffs_carry(self):
        self.javascript(r"""
await boot();
globalThis.location={href:'http://local/#sql'};
globalThis.history={replaceState:(_state,_title,url)=>{location.href=String(url);}};
await activate($('tab-warehouse'));
workbenchScreenMessage('catalog-error','Handoff message');
await activate($('tab-catalog'));
assert.equal($('catalog-error').textContent,'Handoff message');assert.equal($('catalog-error').hidden,false);
let now=Date.now();Date.now=()=>now;
workbenchScreenMessage('catalog-error','Catalog only');now+=1000;
await activate($('tab-warehouse'));
assert.equal($('catalog-error').textContent,'');assert.equal($('catalog-error').hidden,true);
now+=1000;await activate($('tab-catalog'));
assert.equal($('catalog-error').textContent,'Catalog only');assert.equal($('catalog-error').hidden,false);
now+=1000;await activate($('tab-compute'));
assert.equal($('catalog-error').hidden,true);
now+=1000;await activate($('tab-catalog'));
assert.equal($('catalog-error').hidden,false);
""")

    def test_fmt_jst_and_jst_input_helpers(self):
        self.javascript(r"""
assert.equal(fmtJST('2026-11-18T00:00:00Z'),'2026-11-18 09:00 JST');
assert.equal(fmtJST('2026-11-18T00:00:00Z',true),'2026-11-18 09:00:00 JST');
assert.equal(fmtJST('2026-11-18T15:30:00Z'),'2026-11-19 00:30 JST');
assert.equal(fmtJST('not-a-date'),'not-a-date');
assert.equal(fmtInputJST('2026-11-18T09:30'),'2026-11-18 09:30 JST');
assert.equal(fmtInputJST(''),'');
assert.equal(jstInputToISO('2026-11-18T09:00'),'2026-11-18T00:00:00.000Z');
assert.equal(jstInputToISO('2026-11-18T09:00:30'),'2026-11-18T00:00:30.000Z');
assert.equal(jstInputToDate('2026-11-18T09:00').toISOString(),'2026-11-18T00:00:00.000Z');
assert.equal(jstInputToDate(''),null);assert.equal(jstInputToISO('not-a-date'),'');
""")

    def test_user_menu_keeps_language_role_and_ai_settings(self):
        user_menu = MARKUP.ids['user-menu']
        for identifier in ('role-selector', 'role-selector-label', 'role-indicator',
                           'lang-en', 'lang-ja', 'ai-settings-button'):
            self.assertTrue(any(parent['attrs'].get('id') == 'user-menu'
                                for parent in MARKUP.ids[identifier]['parents']), identifier)
            self.assertTrue(any(parent is user_menu for parent in MARKUP.ids[identifier]['parents']), identifier)
        script = WorkbenchSource(harness.HTML).source
        for code in ('ja', 'en'):
            self.assertIn(f"$('lang-{code}').onclick = () => setLanguage('{code}');", script)
        self.javascript(r"""
await boot();
setLanguage('ja');assert.equal($('lang-ja').getAttribute('aria-pressed'),'true');
assert.equal($('lang-en').getAttribute('aria-pressed'),'false');assert.equal(document.documentElement.lang,'ja');
setLanguage('en');assert.equal($('lang-en').getAttribute('aria-pressed'),'true');
$('role-selector').value='bi_viewer';await $('role-selector').onchange();
assert.equal(currentRole,'bi_viewer');assert.equal($('role-indicator').hidden,false);
$('role-selector').value='admin';await $('role-selector').onchange();
assert.equal(currentRole,'admin');assert.equal($('role-indicator').hidden,true);
""")

    def test_notification_bell_uses_job_alert_source_and_opens_jobs(self):
        source = WorkbenchSource(harness.HTML).source
        loader = source[source.index('async function loadJobAlerts('):source.index('$(\'job-alerts-refresh\').onclick')]
        self.assertIn('workbenchAlertCount=count', loader)
        self.assertIn('workbenchAlerts=Array.isArray(data.alerts)?data.alerts:[]', loader)
        self.assertIn('workbenchRenderAlerts();', loader)
        self.javascript(r"""
await boot();
const alertText=node=>[node.textContent,...node.descendants().map(n=>n.textContent)].join(' | ');
workbenchAlertCount=2;workbenchAlerts=[
 {message:'Disk low',acknowledged_at:null,resolved_at:null,severity:'critical',job_name:'inventory',last_seen_at:'2026-11-18T01:02:00Z'},
 {message:'Old alert',acknowledged_at:'2026-11-18T00:00:00Z',resolved_at:null,severity:'warning',job_name:'daily'},
 {message:'Resolved alert',acknowledged_at:null,resolved_at:'2026-11-18T00:00:00Z',severity:'warning',job_name:'daily'},
 {message:'Orders late',acknowledged_at:null,resolved_at:null,severity:'warning',job_name:'daily',last_seen_at:'2026-11-18T02:03:00Z'},
];workbenchRenderAlerts();
const rows=$('workbench-alert-list').children;
assert.equal(rows.length,2,'confirmed and resolved alerts are excluded');
assert.equal(rows[0].id,'workbench-alert-latest');assert.ok(alertText(rows[0]).includes('critical'));assert.ok(alertText(rows[0]).includes('Disk low'));
assert.match(alertText(rows[0]),/inventory.*2026-11-18 10:02 JST/);
assert.ok(alertText(rows[1]).includes('Orders late'));
assert.equal($('workbench-alert-count').textContent,'2');assert.equal($('workbench-alert-count').hidden,false);
assert.match($('workbench-alerts-button').getAttribute('aria-label'),/2/);
assert.match($('workbench-alert-total').textContent,/2/);
$('workbench-alerts-popover').hidden=true;
$('workbench-alerts-button').onclick();
assert.equal($('workbench-alerts-popover').hidden,false);
$('workbench-alerts-open-jobs').onclick();
assert.equal($('workbench-alerts-popover').hidden,true);assert.equal(activeTab,'jobs');
workbenchAlertCount=0;workbenchAlerts=[];workbenchRenderAlerts();
assert.equal($('workbench-alert-count').hidden,true);
assert.equal($('workbench-alert-total').textContent,t('No unread alerts.'));
assert.equal($('workbench-alert-list').hidden,true);assert.equal($('workbench-alert-list').children.length,0);
""")
