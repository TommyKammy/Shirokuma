"""UX-A5 database management: Atlas list, team, route and retirement impact."""
import re
import unittest
from pathlib import Path

import test_workbench_admin_ui as admin
import test_workbench_creations as creations
import test_workbench_retirement_ui as retirement
from workbench_source import WorkbenchSource


class DatabaseManagementUI(unittest.TestCase):
    javascript = admin.harness.DatabaseUI.javascript

    def test_list_uses_retirement_terms_team_paging_and_canonical_routes(self):
        self.javascript(admin.SETUP + r'''
adminItems[0].state='RETIRING';adminItems[0].team='Data Team';adminItems[1].state='RETIRED';adminItems[1].team=null;
dbAdmin.databases[0].state='RETIRING';dbAdmin.databases[0].team='Data Team';dbAdmin.databases[1].state='RETIRED';dbAdmin.databases[1].team=null;
dbAdminSelect('');await dbAdminRefresh();setLanguage('ja');dbAdminRenderList();
const rows=$('db-admin-list-rows').children;
const rowFor=id=>rows.find(row=>row.children[0].children[0].id==='db-admin-link-'+id);
assert.equal(rowFor('db').children[1].children[0].textContent,'利用終了（処理中）');
assert.equal(rowFor('db').children[2].textContent,'Data Team');
assert.equal(rowFor('finance').children[1].children[0].textContent,'利用終了（完了）');
assert.equal(rowFor('finance').children[2].textContent,'未設定');
assert.equal(rowFor('db').children[3].textContent,'—');
assert.equal(rowFor('db').children[3].children.length,0);
assert.equal(rowFor('db').children[3].title,'更新日時はサーバーから取得できません');
assert.equal($('db-admin-list-updated-note').hidden,false);
assert.equal($('db-admin-list-updated-note').descendants().some(node=>node.className?.includes('visually-hidden')),false);
assert.equal(rowFor('db').children[1].children[0].className,'status warning');
assert.equal(rowFor('finance').children[1].children[0].className,'status');
assert.equal(new URL('http://local/'+rowFor('db').children[0].children[0].getAttribute('href')).hash,'#databases/db');
assert.equal($('db-admin-list-team').options.some(option=>option.value==='Data Team'),true);
assert.equal($('db-admin-list-team').options.some(option=>option.value==='__unset__'&&option.textContent==='未設定'),true);
assert.equal($('db-admin-list-head-0').textContent,'DB名');
assert.equal($('db-admin-list-head-3').textContent,'最終更新');
assert.equal($('db-admin-list-head-4').textContent,'ピン留め');
assert.match($('db-admin-range').textContent,/1–2 \/ 2件 · 1 \/ 1ページ/);
assert.equal($('db-admin-page-first').getAttribute('aria-disabled'),'true');
assert.equal($('db-admin-page-last').getAttribute('aria-disabled'),'true');
globalThis.location={href:'http://localhost/#db-admin',origin:'http://localhost'};
globalThis.history={replaceState:(_,__,url)=>{location.href=String(url);}};
workbenchRememberPage('db-admin');
assert.equal(new URL(location.href).hash,'#databases');
for(const [hash,expected] of [['#databases',['list']],['#databases/db',['detail','db','info']],['#databases/create',['create']],['#databases/access~db',['detail','db','access']],['#databases/retire~db',['retire','db']]]){
 location.href='http://localhost/'+hash;const route=dbAdminRoute();
 assert.equal(route.view,expected[0]);
 if(expected[0]==='detail'){assert.equal(route.id,expected[1]);assert.equal(route.panel,expected[2]);}
 if(expected[0]==='retire'){assert.equal(route.id,expected[1]);assert.equal(route.confirm,false);}
}
location.href='http://localhost/#databases/confirm~db';assert.equal(dbAdminRoute().confirm,true);
location.href='http://localhost/#databases/access~db';$('db-admin-detail-view').hidden=false;dbAdminApplyRoute();
assert.equal($('db-admin-access-panel').hidden,false);assert.equal($('db-admin-info-panel').hidden,true);
location.href='http://localhost/#databases/metadata~db';dbAdminApplyRoute();
assert.equal($('db-admin-metadata-panel').hidden,false);assert.equal($('db-admin-access-panel').hidden,true);
location.href='http://localhost/#databases/operation~db';dbAdminApplyRoute();
assert.equal($('db-admin-operation-panel').hidden,false);assert.equal($('db-admin-metadata-panel').hidden,true);
''')

    def test_creation_requires_team_uses_existing_team_suggestions_and_review(self):
        self.javascript(creations.SETUP + creations.LOGICAL_SETUP + r'''
adminItems[0].team='Data Team';adminItems[1].team='Finance';
dbAdmin.databases[0].team='Data Team';dbAdmin.databases[1].team='Finance';
await logicalBegin();dbCreation.stage=0;dbCreationRender();
setLanguage('ja');assert.match($('db-creation-team-help').textContent,/必須/);
setLanguage('en');assert.match($('db-creation-team-help').textContent,/Required/);
assert.deepEqual($('db-creation-team-options').options.map(option=>option.value),['Data Team','Finance']);
edit('db-creation-team','');await $('db-creation-next').onclick();
assert.equal(creationPosts.length,0);assert.equal(document.activeElement,$('db-creation-team'));assert.match($('db-creation-status').textContent,/1 to 64/);
edit('db-creation-team','x'.repeat(65));await $('db-creation-next').onclick();assert.equal(creationPosts.length,0);
edit('db-creation-team','bad\u0001team');await $('db-creation-next').onclick();assert.equal(creationPosts.length,0);
edit('db-creation-team','  Data Team  ');await $('db-creation-next').onclick();
assert.equal(dbCreation.stage,2);assert.match(contents($('db-creation-form')),/Data Team/);
creation=logical();creationRoute=async()=>({creation_id:'creation',request_id:'ack',accepted_revision:3,action:'CREATE',phase:null});
await $('db-creation-submit').onclick();
assert.deepEqual(JSON.parse(creationPosts[0].options.body).team,'Data Team');
''')

    def test_detail_primary_sql_management_team_edit_and_access_link(self):
        self.javascript(admin.SETUP + r'''
adminItems[0].state='READY';await dbAdminRefresh();
assert.equal($('db-admin-use-sql').hidden,false);
assert.equal($('db-admin-detail-actions').children[0].id,'db-admin-use-sql');
assert.equal($('db-admin-detail-actions').children[0].textContent,'Use in SQL');
assert.equal($('db-admin-use-sql').className,'primary');
assert.equal($('db-admin-management').tagName,'DETAILS');assert.equal(!!$('db-admin-management').open,false);
assert.equal($('db-admin-management').descendants().some(node=>node.id==='db-admin-register-name'),true);
assert.equal($('db-admin-retire-view').descendants().some(node=>node.id==='db-admin-retire-preview'),true);
assert.equal($('db-admin-access-open').getAttribute('href'),'#databases/access~db');
assert.equal($('db-admin-operation-open').getAttribute('href'),'#databases/operation~db');
assert.equal($('db-admin-metadata-open').getAttribute('href'),'#databases/metadata~db');
assert.equal($('db-admin-retire-open').getAttribute('href'),'#databases/retire~db');
assert.equal($('db-admin-summary').textContent,'売上 <img onerror=x> 🐻 · sales');
assert.equal($('db-admin-summary').textContent.includes(' · db'),false);
$('db-admin-database-db-team').value='  Data Team  ';adminRoute=async()=>metadataResponse;await save();
const call=writeCalls().at(-1);
assert.equal(call.path,'/api/v1/databases/db/display');assert.equal(JSON.parse(call.options.body).team,'Data Team');
assert.equal($('db-admin-info').children.some(node=>node.textContent==='Not set'),true);
''')

    def test_retirement_confirmation_summary_users_and_red_button(self):
        self.javascript(retirement.SETUP + r'''
grantItems=[
 {principal_id:'alice',maximum_role:'bi_viewer',effective_role:'bi_viewer',revision:1,applied_revision:1,grant_revision:1},
 {principal_id:'bob',maximum_role:'data_engineer',effective_role:'data_engineer',revision:1,applied_revision:1,grant_revision:1},
 {principal_id:'carol',maximum_role:'revoked',effective_role:'revoked',revision:2,applied_revision:2,grant_revision:2}];
const preview=retirePreview();
adminRoute=async(path,options)=>path.endsWith('/grants')?{grants:structuredClone(grantItems)}:preview;
await $('db-admin-retire-preview').onclick();
const summary=$('db-admin-retire-impact-summary');
assert.equal(summary.hidden,false);assert.match(summary.textContent,/will be retired.*2 users will lose access/);
assert.deepEqual($('db-admin-retire-users').rows.map(row=>row[0]),['alice','bob']);
assert.deepEqual($('db-admin-retire-users').rows.map(row=>row[1]),['BI Viewer','Data Engineer']);
assert.equal($('db-admin-retire-save').textContent,'Retire database');
assert.match($('db-admin-retire-save').className,/primary/);assert.match($('db-admin-retire-save').className,/danger/);
assert.match($('db-admin-retire-scope').textContent,/\d{4}-\d{2}-\d{2} \d{2}:\d{2} JST/);
setLanguage('ja');
assert.match(summary.textContent,/2 人の利用者がアクセスできなくなります。/);
assert.equal($('db-admin-retire-save').textContent,'利用を終了する');
assert.deepEqual($('db-admin-retire-users').rows.map(row=>row[1]),['閲覧者','データ作成者']);
assert.equal(t('Principal ID'),'利用者 ID');
assert.match($('db-admin-retire-scope').textContent,/ロール: 管理者/);
assert.equal($('db-admin-retire-summary').textContent.includes(' · db ·'),false);
setLanguage('en');
assert.equal($('db-admin-retire-save').textContent,'Retire database');
assert.deepEqual($('db-admin-retire-users').rows.map(row=>row[1]),['BI Viewer','Data Engineer']);
assert.equal(t('Principal ID'),'Principal ID');
assert.match($('db-admin-retire-scope').textContent,/Role: Admin/);
assert.ok(calls.some(call=>call.path==='/api/v1/databases/db/grants'));
''')

    def test_list_and_detail_markup_supports_narrow_focus_and_status_regions(self):
        self.assertIn('id="db-admin-error" class="error" role="alert"', admin.HTML)
        self.javascript(admin.SETUP + r'''
assert.equal($('db-admin-current-target').getAttribute('role'),'status');
assert.equal($('db-admin-management').tagName,'DETAILS');
assert.equal($('db-admin-list').getAttribute('role'),'region');
assert.equal($('db-admin-list').tabIndex,0);
''')

    def test_language_switch_redraws_list_detail_creation_and_retirement(self):
        self.javascript(admin.SETUP + r'''
adminItems[0].state='RETIRING';adminItems[0].team='Data Team';
dbAdmin.databases[0].state='RETIRING';dbAdmin.databases[0].team='Data Team';
dbAdminSelect('');await dbAdminRefresh();setLanguage('ja');
const listRows=()=>$('db-admin-list-rows').children.map(row=>({state:row.children[1].children[0].textContent,team:row.children[2].textContent}));
assert.equal(listRows().some(row=>row.state==='登録済み'&&row.team==='未設定'),true);
assert.equal(listRows().some(row=>row.state==='利用終了（処理中）'&&row.team==='Data Team'),true);
assert.equal($('db-admin-list-sort').options.map(option=>option.textContent).join('|'),'DB名 A–Z|DB名 Z–A');
assert.equal($('db-admin-list-sort').value,'name-asc');
const headers=()=>[0,1,2,3,4].map(index=>$('db-admin-list-head-'+index).textContent);
assert.equal(headers()[1],'状態');
assert.match($('db-admin-select').options.find(option=>option.value==='db').textContent,/利用終了（処理中）$/);
assert.equal($('db-admin-select').options.find(option=>option.value==='db').textContent.includes(' · db'),false);
setLanguage('en');
assert.equal(listRows().find(row=>row.team==='Data Team').state,'Retiring');
assert.match($('db-admin-select').options.find(option=>option.value==='db').textContent,/Retiring$/);
assert.equal($('db-admin-list-sort').options.map(option=>option.textContent).join('|'),'DB name A–Z|DB name Z–A');
assert.equal(headers()[1],'State');
await choose('db');setLanguage('ja');
assert.equal($('db-admin-detail-state').children[0].textContent,'利用終了（処理中）');
assert.deepEqual($('db-admin-info').children.filter(node=>node.tagName==='DT').map(node=>node.textContent),['表示名','説明','担当チーム','状態','リビジョン']);
setLanguage('en');
assert.equal($('db-admin-detail-state').children[0].textContent,'Retiring');
assert.deepEqual($('db-admin-info').children.filter(node=>node.tagName==='DT').map(node=>node.textContent),['Display name','Description','Team','State','Revision']);
''')

    def test_empty_team_for_configured_database_is_rejected_inline_and_not_sent(self):
        self.javascript(admin.SETUP + r'''
adminItems[0].team='Data Team';dbAdmin.databases[0].team='Data Team';dbAdmin.drafts.clear();dbAdmin.metadataID=null;$('db-admin-metadata').replaceChildren();dbAdminRenderDetail();
const team=$('db-admin-database-db-team'),error=$('db-admin-database-db-team-error');
assert.equal(team.value,'Data Team');team.value='';team.oninput();
const before=writeCalls().length;await save();
assert.equal(writeCalls().length,before);assert.equal(error.hidden,false);
assert.equal(error.textContent,'Team must be 1 to 64 characters without control characters.');
assert.equal(document.activeElement,team);assert.equal(team.getAttribute('aria-invalid'),'true');
team.value='  Next Team  ';team.oninput();adminRoute=async()=>metadataResponse;await save();
assert.equal(JSON.parse(writeCalls().at(-1).options.body).team,'Next Team');
''')

    def test_dom_collections_are_not_used_as_arrays(self):
        array_only = r'(?:find|filter|map|some|every|forEach|reduce|slice|indexOf|includes|sort|reverse|concat|flatMap)'
        array_only_without_foreach = r'(?:find|filter|map|some|every|reduce|slice|indexOf|includes|sort|reverse|concat|flatMap)'
        properties = ('children', 'childNodes', 'options', 'selectedOptions', 'rows', 'cells')
        problems = []
        for number, line in enumerate(WorkbenchSource().source.splitlines(), 1):
            for prop in properties:
                if re.search(r'\.' + prop + r'\.' + array_only + r'\s*\(', line):
                    # Lineage graph nodes are plain objects, not DOM collections.
                    if prop == 'children' and ('node.children.map' in line or 'node.children.flatMap' in line):
                        continue
                    problems.append((number, prop, line.strip()))
            if re.search(r'querySelectorAll\s*\([^\n;]*?\)\s*\.' + array_only_without_foreach + r'\s*\(', line):
                problems.append((number, 'querySelectorAll', line.strip()))
            if re.search(r'getElementsBy[A-Za-z]+\s*\([^\n;]*?\)\s*\.' + array_only_without_foreach + r'\s*\(', line):
                problems.append((number, 'getElementsBy*', line.strip()))
        self.assertEqual(problems, [], 'DOM collections must be spread or Array.from before array methods')

    def test_styles_cover_narrow_tap_targets_focus_and_horizontal_table_scroll(self):
        css = (Path(__file__).resolve().parents[1] / 'tools/local_experiment/workbench/workbench.css').read_text()
        compact = ''.join(css.split())
        self.assertIn('#db-admin:is(button,a,input,select,textarea,summary):focus{outline:2pxsolidvar(--focus)', compact)
        self.assertIn('.db-table{table-layout:fixed;min-width:690px;}', compact)
        self.assertIn('.db-table{min-width:590px;}', compact)
        self.assertIn('table-layout:fixed', compact)
        self.assertIn('.db-filters[data-open="false"].db-filter-controls{display:none;}', compact)
        self.assertIn('.db-filter-toggle{display:inline-flex', compact)
        self.assertIn('.tablewrap{position:relative;overflow:auto', compact)
        self.assertNotIn('.db-table thead{position:absolute', compact)
        self.assertIn('.db-pin{min-height:36px', compact)
        self.assertIn('#db-admin:is(button,select,input:not([type=checkbox]):not([type=radio]),textarea,summary,a)', compact)
        self.assertIn('min-height:44px', compact)


if __name__ == '__main__':
    unittest.main()
