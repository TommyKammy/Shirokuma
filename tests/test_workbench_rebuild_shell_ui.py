"""UX-R0: the Workbench shell follows the approved Atlas mock-up."""
import re
import unittest

import test_workbench_database_ui as harness
from test_workbench_navigation_ui import MARKUP
from workbench_source import WorkbenchSource

HTML = harness.HTML
CSS = WorkbenchSource(HTML).css
SOURCE = WorkbenchSource(HTML).source


class RebuildShellUI(unittest.TestCase):
    javascript = harness.DatabaseUI.javascript

    def test_sidebar_brand_navigation_favorites_workspace_theme_and_profile(self):
        sidebar = MARKUP.ids['workbench-nav']
        self.assertEqual(sidebar['tag'], 'aside')
        self.assertEqual(sidebar['attrs']['class'], 'sidebar')
        self.assertIn('Shirokuma', sidebar['text'])
        brand = next(node for node in MARKUP.elements
                     if node['tag'] == 'a' and sidebar in node['parents']
                     and 'brand' in (node['attrs'].get('class') or '').split())
        self.assertEqual(brand['attrs']['href'], '#home')
        self.assertIn('Data workspace', brand['text'])
        navlist = next(node for node in MARKUP.elements
                       if node['tag'] == 'nav' and sidebar in node['parents']
                       and 'navlist' in (node['attrs'].get('class') or '').split())
        links = [node for node in MARKUP.elements
                 if navlist in node['parents'] and 'data-tab' in node['attrs']]
        self.assertEqual([(node['attrs']['data-tab'], node['attrs']['href']) for node in links], [
            ('home', '#home'), ('catalog', '#explore'), ('warehouse', '#sql'),
            ('ingestion', '#ingestion'), ('transformation', '#transformation'),
            ('jobs', '#jobs'), ('bears', '#bears'), ('db-admin', '#databases'),
            ('pawprints', '#audit'), ('compute', '#compute'),
        ])
        titles = [node['text'].strip() for node in MARKUP.elements
                  if 'navtitle' in (node['attrs'].get('class') or '').split()
                  and sidebar in node['parents']]
        self.assertEqual(titles, ['Administration', 'Favorites', 'Workspace'])
        self.assertIn('workbench-favorites', MARKUP.ids)
        self.assertIn('hidden', MARKUP.ids['workbench-favorites']['attrs'])
        self.assertEqual(MARKUP.ids['nav-workspace-context']['attrs']['href'], '#explore')
        self.assertEqual(MARKUP.ids['rail-warehouse']['attrs']['href'], '#compute')
        for identifier in ('theme-light', 'theme-dark', 'mobile-theme-light', 'mobile-theme-dark'):
            self.assertIn(identifier, MARKUP.ids)
            self.assertIn(MARKUP.ids[identifier]['attrs']['data-theme'], ('light', 'dark'))
        profile = MARKUP.ids['user-menu']
        self.assertEqual(profile['tag'], 'details')
        self.assertIn('profile', profile['attrs']['class'].split())
        self.assertTrue(any(node['attrs'].get('id') == 'user-menu' for node in MARKUP.ids['role-selector']['parents']))
        self.assertEqual(MARKUP.ids['profile-name']['tag'], 'strong')
        self.assertEqual(MARKUP.ids['profile-role']['tag'], 'span')

    def test_topbar_has_breadcrumb_compute_environment_search_and_sql(self):
        header = MARKUP.ids['workbench-header']
        self.assertEqual(header['tag'], 'header')
        self.assertIn('topbar', header['attrs']['class'].split())
        self.assertEqual(MARKUP.ids['workbench-breadcrumb']['tag'], 'span')
        self.assertNotIn('mac-studio-solo', HTML)
        self.assertNotIn('Local lab · mac-studio-solo', HTML)
        self.assertIn('workbench-environment', MARKUP.ids)
        self.assertEqual(MARKUP.ids['database-compute-status']['attrs']['class'], 'sample compute-chip')
        self.assertIn('searchbutton', MARKUP.ids['workbench-command-button']['attrs']['class'].split())
        self.assertIn('⌘ K', MARKUP.ids['workbench-command-button']['text'])
        self.assertEqual(MARKUP.ids['workbench-open-sql']['attrs']['href'], '#sql')
        self.assertIn('primary', MARKUP.ids['workbench-open-sql']['attrs']['class'].split())
        command = MARKUP.ids['command']
        self.assertEqual(command['tag'], 'dialog')
        self.assertEqual(MARKUP.ids['command-list']['attrs']['class'], 'commandlist')
        self.assertEqual(MARKUP.ids['command-search']['tag'], 'input')
        self.assertEqual(MARKUP.ids['close-command']['tag'], 'button')
        self.assertIn('command-empty', MARKUP.ids)

    def test_css_carries_atlas_tokens_and_shell_components(self):
        for token in ('--bg:#fff', '--rail:#fafafb', '--line:#e4e6ea', '--ink:#1a1d21',
                      '--selected:#eff0f2', '--button:#202226', '--green:#39744e',
                      '--amber:#96611c', '--red:#b94c42', '--code:#f7f8fa', '--ring:#4976b2'):
            self.assertIn(token, CSS)
        self.assertIn(':root[data-theme=dark]', CSS)
        for component in ('.app', '.sidebar', '.brand', '.navlist', '.navlink', '.navtitle',
                          '.favorite', '.railbottom', '.theme', '.profile', '.mobilemenu',
                          '.topbar', '.breadcrumb', '.toptools', '.sample', '.compute-chip',
                          '.searchbutton', '.primary', '.commandlist', '.toast', '.pageheading',
                          '.section', '.panel', '.kv', '.field', 'dialog#command'):
            self.assertIn(component, CSS)
        self.assertRegex(CSS, r'\.sidebar\s*\{[^}]*position\s*:\s*sticky')
        self.assertRegex(CSS, r'@media\s*\(max-width:\s*600px\)[\s\S]*?\.sidebar\s*\{\s*display\s*:\s*none')
        self.assertRegex(CSS, r'@media\s*\(max-width:\s*600px\)[\s\S]*?\.mobilemenu\s*\{[^}]*display\s*:\s*block')
        self.assertRegex(CSS, r'min-height\s*:\s*44px')

    def test_breadcrumb_is_only_the_page_name(self):
        self.javascript(r"""
await boot();
for(const [tab,label] of [['tab-jobs','Jobs'],['tab-catalog','Data Explorer'],['tab-compute','Compute & Governance']]){
 await activate($(tab));
 assert.equal($('workbench-breadcrumb').textContent,label);
 assert.ok(!$('workbench-breadcrumb').textContent.includes('Local lab'));
 assert.ok(!$('workbench-breadcrumb').textContent.includes('202'));
}
setLanguage('ja');
assert.equal($('workbench-breadcrumb').textContent,t('Compute & Governance'));
assert.notEqual($('workbench-breadcrumb').textContent,'Compute & Governance');
""")

    def test_theme_is_saved_restored_and_survives_storage_failure(self):
        self.javascript(r"""
await boot();
storage.clear();
workbenchApplyTheme('dark');
assert.equal(document.documentElement.dataset.theme,'dark');
assert.equal($('theme-dark').getAttribute('aria-pressed'),'true');
assert.equal($('mobile-theme-dark').getAttribute('aria-pressed'),'true');
assert.equal($('theme-light').getAttribute('aria-pressed'),'false');
assert.equal(storage.get('shirokuma.theme.v1'),'dark');
workbenchApplyTheme('light');
assert.equal(document.documentElement.dataset.theme,'light');
assert.equal(storage.get('shirokuma.theme.v1'),'light');
storage.set('shirokuma.theme.v1','dark');workbenchInitTheme();
assert.equal(document.documentElement.dataset.theme,'dark');
localStorage.getItem=()=>{throw Error('storage disabled');};
workbenchInitTheme();
assert.equal(document.documentElement.dataset.theme,'light');
assert.equal($('theme-light').getAttribute('aria-pressed'),'true');
localStorage.setItem=()=>{throw Error('storage disabled');};
workbenchApplyTheme('dark');
assert.equal(document.documentElement.dataset.theme,'dark');
assert.equal($('theme-dark').getAttribute('aria-pressed'),'true');
""")

    def test_favorites_are_per_user_and_hide_the_heading_at_zero(self):
        self.javascript(r"""
await boot();
workbenchSession={username:'alice',authenticated:true,maximum_role:'admin'};
storage.clear();workbenchSetFavorites([]);
assert.equal($('workbench-favorites').hidden,true);
assert.equal($('workbench-favorite-list').children.length,0);
workbenchSetFavorites([{label:'orders',href:'#explore/sales.orders'}]);
assert.equal($('workbench-favorites').hidden,false);
assert.equal($('workbench-favorite-list').children.length,1);
assert.match($('workbench-favorite-list').children[0].children[1].textContent,/orders/);
assert.equal($('workbench-favorite-list').children[0].href,'#explore/sales.orders');
assert.equal(JSON.parse(storage.get('shirokuma.favorites.v1.alice')).length,1);
workbenchSession={username:'bob',authenticated:true,maximum_role:'admin'};
workbenchRenderFavorites();
assert.equal($('workbench-favorites').hidden,true);
workbenchSession={username:'alice',authenticated:true,maximum_role:'admin'};
workbenchSetFavorites([{label:'not a destination',href:'#compute'}]);
assert.equal($('workbench-favorites').hidden,true);
localStorage.getItem=()=>{throw Error('storage disabled');};
workbenchRenderFavorites();
assert.equal($('workbench-favorites').hidden,true);
""")

    def test_workspace_uses_display_names_and_never_raw_ids(self):
        self.javascript(r"""
await boot();
dbCurrent().database_id='db';dbCurrent().schema_id='schema';
workbenchRenderWorkspaceNav();
assert.equal($('nav-workspace-context-label').textContent,'Sales / Mart');
assert.ok(!$('nav-workspace-context-label').textContent.includes('db'));
assert.ok(!$('nav-workspace-context-label').textContent.includes('schema'));
assert.match($('rail-warehouse-state').textContent,/^LOCAL · /);
dbCurrent().schema_id='';
workbenchRenderWorkspaceNav();
assert.equal($('nav-workspace-context-label').textContent,'Sales');
dbCurrent().database_id='ghost';
workbenchRenderWorkspaceNav();
assert.equal($('nav-workspace-context-label').textContent,t('No workspace context'));
assert.ok(!$('nav-workspace-context-label').textContent.includes('ghost'));
""")

    def test_profile_shows_user_and_localized_role(self):
        self.javascript(r"""
await boot();
workbenchSession={username:'alice',display_name:'Alice',authenticated:true,maximum_role:'admin'};
currentRole='admin';workbenchRenderProfile();
assert.equal($('profile-name').textContent,'Alice');
assert.equal($('profile-role').textContent,t('Admin'));
setLanguage('ja');
assert.equal($('profile-role').textContent,t('Admin'));
assert.notEqual($('profile-role').textContent,'Admin');
setLanguage('en');
assert.equal($('profile-role').textContent,'Admin');
""")

    def test_command_palette_waits_300ms_needs_two_characters_and_discards_stale_responses(self):
        self.javascript(r"""
await boot();
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
globalThis.location={href:'http://local/#sql'};
$('command').showModal=function(){this.open=true;};
$('command').close=function(){this.open=false;};
$('command').open=true;
const entries=()=>[...$('command-list').children];
let searchCalls=0;
route=async path=>{if(path.startsWith('/api/v1/objects/search?'))searchCalls++;return {items:[],truncated:false,unavailable_databases:[]};};
$('command-search').value='o';workbenchCommandRender();
assert.equal(searchCalls,0);
$('command-search').value='or';workbenchCommandRender();
assert.equal(searchCalls,0);
assert.equal($('command-empty').textContent,t('Searching…'));
assert.equal($('command-empty').hidden,false);
await wait(150);assert.equal(searchCalls,0);
await wait(190);assert.equal(searchCalls,1);
await tick();
assert.equal($('command-empty').textContent,t('No matching destinations.'));
$('command-search').value='o';workbenchCommandRender();
await wait(340);assert.equal(searchCalls,1);
const gates=[];
route=path=>{if(path.startsWith('/api/v1/objects/search?')){let resolve;const promise=new Promise(r=>{resolve=r;});gates.push(resolve);return promise;}return {};};
$('command-search').value='old';workbenchCommandRender();
await wait(320);
$('command-search').value='new';workbenchCommandRender();
await wait(320);
assert.equal(gates.length,2);
gates[0]({items:[{database_id:'db',sql_catalog:'sales',schema_id:'s',schema_sql_name:'marts',name:'old_table',kind:'TABLE'}],truncated:false});
await tick();workbenchCommandPaint();
assert.ok(!entries().some(node=>node.children[0].textContent.includes('old_table')));
gates[1]({items:[{database_id:'db',sql_catalog:'sales',schema_id:'s',schema_sql_name:'marts',name:'new_table',kind:'TABLE'}],truncated:false});
await tick();workbenchCommandPaint();
assert.ok(entries().some(node=>node.children[0].textContent.includes('new_table')));
assert.equal($('command-empty').hidden,true);
""")

    def test_language_switch_updates_new_shell_copy(self):
        self.javascript(r"""
await boot();
dbCurrent().database_id='ghost';
setLanguage('ja');
for(const [id,text] of [['tab-home','ホーム'],['tab-catalog','データ探索'],['tab-pawprints','監査ログ']])
 assert.ok($(id).textContent.includes(text),id+': '+$(id).textContent);
assert.equal($('nav-workspace-context-label').textContent,t('No workspace context'));
assert.equal($('rail-warehouse-state').textContent,'LOCAL · '+t(workbenchComputeLabel(workbenchComputeStatusKey)));
assert.ok(!/No workspace context|Workspace|Favorites/.test($('workbench-nav').textContent));
setLanguage('en');
assert.ok($('tab-home').textContent.includes('Home'));
assert.equal($('nav-workspace-context-label').textContent,'No workspace context');
""")

    def test_hash_routes_reset_scroll_and_focus_with_prevent_scroll(self):
        start = SOURCE[SOURCE.index('async function startWorkbench'):SOURCE.index('async function initializeSession')]
        self.assertIn('workbenchResetRouteView({focus:true})', start)
        self.assertIn("scrollRestoration = 'manual'", HTML)
        self.javascript(r"""
await boot();
const calls=[];globalThis.scrollTo=(x,y)=>calls.push([x,y]);
const heading=$('bears-heading');
let focusOptions=null;
heading.focus=options=>{focusOptions=options;document.activeElement=heading;};
workbenchShellReady=true;workbenchSession=session;
workbenchHistoryHref='http://local/#home';
globalThis.location={href:'http://local/#bears'};
await workbenchHistoryRestore();
assert.equal(activeTab,'bears');
assert.deepEqual(calls.at(-1),[0,0]);
assert.deepEqual(focusOptions,{preventScroll:true});
assert.equal(document.activeElement,heading);
await activate($('tab-home'));
calls.length=0;focusOptions=null;
await activate($('tab-bears'));
assert.deepEqual(calls.at(-1),[0,0]);
assert.deepEqual(focusOptions,{preventScroll:true});
""")

    def test_mobile_topbar_is_one_row_and_does_not_overflow(self):
        six = next(block for block in re.findall(r'@media\s*\(max-width:\s*600px\)\s*\{(.*?)\n\}', CSS, re.S)
                   if '#database-compute-status{display:none}' in block)
        self.assertRegex(six, r'\.topbar\{[^}]*flex-wrap:nowrap')
        self.assertRegex(six, r'\.toptools\{[^}]*flex-wrap:nowrap')
        self.assertIn('#database-compute-status{display:none}', six)
        self.assertIn('body{overflow-x:hidden}', six)
        self.assertRegex(six, r'\.breadcrumb span\{[^}]*max-width:26vw')
        small = next(block for block in re.findall(r'@media\s*\(max-width:\s*360px\)\s*\{(.*?)\n\}', CSS, re.S)
                     if 'padding-inline:12px' in block)
        self.assertIn('padding-inline:12px', small)
        self.assertIn('max-width:22vw', small)

    def test_mobile_menu_reuses_the_single_role_and_language_controls(self):
        self.assertEqual(HTML.count('id="role-selector"'), 1)
        self.assertEqual(HTML.count('id="lang-ja"'), 1)
        self.assertEqual(HTML.count('id="lang-en"'), 1)
        self.assertIn('id="mobile-profile-slot"', HTML)
        self.assertEqual(MARKUP.ids['user-menu']['tag'], 'details')
        self.javascript(r"""
await boot();
narrow=true;workbenchPlaceProfile();
assert.equal($('user-menu').parentElement,$('mobile-profile-slot'));
$('user-menu').open=true;
setLanguage('ja');
assert.equal(document.documentElement.lang,'ja');
assert.equal($('lang-ja').getAttribute('aria-pressed'),'true');
$('role-selector').value='bi_viewer';
await $('role-selector').onchange();
assert.equal(currentRole,'bi_viewer');
assert.equal($('role-selector').value,'bi_viewer');
narrow=false;workbenchPlaceProfile();
assert.equal($('user-menu').parentElement,$('profile-slot'));
""")

    def test_skip_link_and_main_target(self):
        self.assertIn('class="skip" href="#main"', HTML)
        self.assertEqual(MARKUP.ids['main']['tag'], 'main')
        self.assertEqual(MARKUP.ids['main']['attrs']['tabindex'], '-1')


if __name__ == '__main__':
    unittest.main()
