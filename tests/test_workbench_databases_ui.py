"""UX-A5 database management: Atlas list, team, route and retirement impact."""
import re
import unittest
from pathlib import Path

import test_workbench_admin_ui as admin
import test_workbench_creations as creations
import test_workbench_retirement_ui as retirement


class DatabaseManagementUI(unittest.TestCase):
    javascript = admin.harness.DatabaseUI.javascript

    def test_list_uses_retirement_terms_team_and_canonical_routes(self):
        self.javascript(admin.SETUP + r'''
adminItems[0].state='RETIRING';adminItems[0].team='Data Team';adminItems[1].state='RETIRED';adminItems[1].team=null;
dbAdmin.databases[0].state='RETIRING';dbAdmin.databases[0].team='Data Team';dbAdmin.databases[1].state='RETIRED';dbAdmin.databases[1].team=null;
dbAdminSelect('');await dbAdminRefresh();setLanguage('ja');dbAdminRenderList();
const rows=$('db-admin-list').rows;
assert.deepEqual(rows.map(row=>row[2]),['未設定','Data Team']);
assert.deepEqual(rows.map(row=>row[1]),['利用終了（完了）','利用終了（処理中）']);
assert.equal($('db-admin-list').columns[0],'DB name');
assert.equal($('db-admin-list-team').options.some(option=>option.value==='Data Team'),true);
assert.equal($('db-admin-link-db').getAttribute('href'),'#databases/db');
globalThis.location={href:'http://localhost/#db-admin',origin:'http://localhost'};
globalThis.history={replaceState:(_,__,url)=>{location.href=String(url);}};
workbenchRememberPage('db-admin');
assert.equal(new URL(location.href).hash,'#databases');
for(const [hash,expected] of [['#databases',['list']],['#databases/db',['detail','db','info']],['#databases/create',['create']],['#databases/access~db',['detail','db','access']]]){
 location.href='http://localhost/'+hash;const route=dbAdminRoute();
 assert.equal(route.view,expected[0]);
 if(expected[0]==='detail'){assert.equal(route.id,expected[1]);assert.equal(route.panel,expected[2]);}
}
location.href='http://localhost/#databases/access~db';dbAdminApplyRoute();
assert.equal($('db-admin-access-panel').hidden,false);assert.equal($('db-admin-info-panel').hidden,true);
location.href='http://localhost/#databases/metadata~db';dbAdminApplyRoute();
assert.equal($('db-admin-metadata-panel').hidden,false);assert.equal($('db-admin-access-panel').hidden,true);
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
assert.equal($('db-admin-management').descendants().some(node=>node.id==='db-admin-retire-preview'),true);
assert.equal($('db-admin-management').descendants().some(node=>node.id==='db-admin-register-name'),true);
assert.equal($('db-admin-access-open').getAttribute('href'),'#databases/access~db');
assert.equal($('db-admin-metadata-open').getAttribute('href'),'#databases/metadata~db');
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
assert.deepEqual($('db-admin-list').rows.map(row=>row.slice(1,3)),[['登録済み','未設定'],['利用終了（処理中）','Data Team']]);
assert.equal($('db-admin-list-sort').options.map(option=>option.textContent).join('|'),'DB名 A–Z|DB名 Z–A');
const headers=()=>$('db-admin-list').descendants().filter(node=>node.tagName==='TH'&&node.scope==='col').map(node=>node.textContent);
assert.equal(headers()[1],'状態');
assert.match($('db-admin-select').options.find(option=>option.value==='db').textContent,/利用終了（処理中）$/);
setLanguage('en');
assert.equal($('db-admin-list').rows.find(row=>row[2]==='Data Team')[1],'Retiring');
assert.match($('db-admin-select').options.find(option=>option.value==='db').textContent,/Retiring$/);
assert.equal($('db-admin-list-sort').options.map(option=>option.textContent).join('|'),'DB name A–Z|DB name Z–A');
assert.equal(headers()[1],'State');
await choose('db');setLanguage('ja');
assert.equal($('db-admin-detail-state').children[0].textContent,'利用終了（処理中）');
assert.deepEqual($('db-admin-info').children.filter(node=>node.tagName==='DT').map(node=>node.textContent),['表示名','説明','担当チーム','リビジョン']);
setLanguage('en');
assert.equal($('db-admin-detail-state').children[0].textContent,'Retiring');
assert.deepEqual($('db-admin-info').children.filter(node=>node.tagName==='DT').map(node=>node.textContent),['Display name','Description','Team','Revision']);
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
        for number, line in enumerate(admin.HTML.splitlines(), 1):
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

    def test_styles_cover_narrow_tap_targets_focus_and_no_wide_tables(self):
        css = (Path(__file__).resolve().parents[1] / 'tools/local_experiment/workbench/workbench.css').read_text()
        self.assertIn('#db-admin :is(button,select,input:not([type=checkbox])', css)
        self.assertIn('min-height:44px', css)
        self.assertIn('outline:2px solid var(--focus)', css)
        self.assertIn('.db-table{width:100%', css)
        self.assertIn('table-layout:fixed', css)
        self.assertIn('.db-table thead{position:absolute', css)
        self.assertIn('.db-table a{display:inline-flex', css)
        self.assertIn('input:not([type=checkbox])', css)


if __name__ == '__main__':
    unittest.main()
