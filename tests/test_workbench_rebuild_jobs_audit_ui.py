"""UX-R6: Jobs and Audit rebuilt from the Atlas mock-up."""
import json
import re
import unittest

import test_workbench_audit_database_ui as audit_ui
import test_workbench_database_ui as harness
from test_workbench_jobs_database_ui import SETUP as JOBS_SETUP
from test_workbench_navigation_ui import URL_SETUP
from workbench_source import WorkbenchSource

HTML = harness.HTML
CSS = WorkbenchSource(HTML).css
COMPACT_CSS = re.sub(r'\s+', '', re.sub(r'/\*[\s\S]*?\*/', '', CSS))

CONTEXT = r'''
const context={database_id:'db',schema_id:'schema',warehouse_id:'LOCAL',sql_catalog:'sales',sql_schema:'marts',selected_role:'admin',binding_revision:1,generation_id:'gen',grant_revision:4};
const jobId='9f1c4b2e-1111-4222-8333-944455556666',runId='a7b8c9d0-1111-4222-8333-944455556666';
const job={id:jobId,owner:'owner',selected_role:'admin',revision:1,context:structuredClone(context),definition:{name:'daily',description:'Refresh sales',parameters:{},steps:[{name:'check',action:'sql',sql:'SELECT 1',mode:'read-only'}]}};
const run={id:runId,job_id:jobId,job_name:'daily',revision:1,owner:'owner',selected_role:'admin',status:'succeeded',context:structuredClone(context),steps:[{name:'check',action:'sql',status:'succeeded',summary:{count:3}}],created_at:'2026-10-29T01:02:00Z',started_at:'2026-10-29T01:02:00Z',finished_at:'2026-10-29T01:02:01.800Z'};
const visibleText=node=>[node.textContent,...node.descendants().map(child=>child.textContent)].join(' ');
'''


class RebuildJobsAuditUI(unittest.TestCase):
    javascript = harness.DatabaseUI.javascript

    def run_url(self, url, body):
        output = self.javascript(URL_SETUP.replace('START_URL', json.dumps(url)) + body +
                                 '\nconsole.log("NAVIGATION_COMPLETE");')
        self.assertTrue(output.endswith('NAVIGATION_COMPLETE\n'), 'Initialization did not complete: ' + output)
        return output.removesuffix('NAVIGATION_COMPLETE\n')

    def run_audit(self, body):
        self.javascript(audit_ui.AUDIT + body)

    def test_jobs_list_has_the_mock_heading_table_and_hint(self):
        self.javascript(JOBS_SETUP + CONTEXT + r'''
savedJobs=[structuredClone(job)];jobRuns=[structuredClone(run)];
await openJobs();await dbJobsListHistory();setLanguage('ja');
const root=$('db-jobs-panel'),listView=$('db-jobs-list-panel');assert.equal(root.className,'jobs-page');
assert.equal(listView.children[0].className,'pageheading');
assert.equal(listView.children[0].children[0].children[0],$('db-jobs-title'));
assert.equal($('db-jobs-title').textContent,'ジョブ');
assert.equal($('db-jobs-title').parentElement.parentElement.children[1].className,'tiny muted');
const recent=$('db-jobs-recent');assert.equal(recent.className,'panel');
assert.equal(recent.children[0].className,'panelhead');
assert.equal(recent.children[0].children[0].tagName,'H2');
assert.equal(recent.children[0].children[0].textContent,'最近の実行');
assert.equal(recent.children[1].className,'tablewrap');
assert.equal(recent.children[1].getAttribute('role'),'region');assert.equal(recent.children[1].tabIndex,0);
const table=recent.children[1].children[0];assert.equal(table.tagName,'TABLE');
const caption=table.children[0];assert.equal(caption.tagName,'CAPTION');assert.equal(caption.id,'db-jobs-recent-caption');assert.equal(caption.textContent,'実行履歴 · 1 件');
const heads=table.children[1].children[0].children;assert.deepEqual([...heads].map(node=>node.textContent),['ジョブ名','状態','開始時刻（JST）','所要時間','出力']);
const row=table.children[2].children[0],link=row.children[0].children[0];
assert.equal(link.tagName,'A');assert.equal(link.getAttribute('href'),'#jobs/run/'+encodeURIComponent(run.id));assert.equal(link.textContent,'daily');
assert.equal(row.children[1].children[0].className,'status success');assert.equal(row.children[1].children[0].textContent,'成功');
assert.equal(row.children[2].children[0].textContent,'2026-10-29 10:02 JST');
assert.equal(row.children[3].textContent,'1.8 秒');
assert.equal(row.children[4].textContent,'—');
assert.equal($('db-jobs-hint').className,'sqlhint');assert.equal($('db-jobs-hint').textContent,'実行詳細で処理ステップと出力データを確認できます。');
assert.equal(visibleText(table).includes(jobId),false);assert.equal(visibleText(table).includes(runId),false);
assert.equal($('db-jobs-saved-panel').className,'panel');
assert.equal($('db-jobs-saved-details').tagName,'DETAILS');
''')

    def test_jobs_empty_history_is_one_neutral_row(self):
        self.javascript(JOBS_SETUP + CONTEXT + r'''
savedJobs=[];jobRuns=[];await openJobs();setLanguage('ja');
const table=$('db-jobs-recent').children[1].children[0];
assert.equal(table.children[0].textContent,'実行履歴 · 0 件');
const rows=table.children[2].children;assert.equal(rows.length,1);
assert.equal(rows[0].children.length,1);assert.equal(rows[0].children[0].colSpan,5);
assert.equal(rows[0].children[0].children[0].className,'empty');
assert.equal(rows[0].children[0].children[0].textContent,'実行履歴はまだありません。');
''')

    def test_job_detail_uses_the_mock_grid_and_readable_eyebrow(self):
        self.javascript(JOBS_SETUP + CONTEXT + r'''
savedJobs=[structuredClone(job)];jobRuns=[structuredClone(run)];await openJobs();
await dbJobsSelect(jobId);setLanguage('ja');
assert.equal(dbJobs.view,'detail');
assert.equal($('db-jobs-back').className,'backlink');assert.equal($('db-jobs-back').textContent,'← ジョブ一覧');assert.equal($('db-jobs-back').getAttribute('href'),'#jobs');
assert.equal($('db-jobs-eyebrow').className,'eyebrow');assert.equal($('db-jobs-eyebrow').textContent,'実行詳細 / 2026-10-29 10:02 JST');
assert.equal($('db-jobs-eyebrow').textContent.includes(jobId),false);assert.equal($('db-jobs-eyebrow').textContent.includes(runId),false);
assert.equal($('db-jobs-detail-title').textContent,'daily');
const grid=$('db-jobs-steps-view').parentElement.parentElement;assert.equal(grid.className,'jobdetailgrid');assert.equal(grid.children.length,2);
const steps=$('db-jobs-steps-list');assert.equal(steps.children.length,1);assert.equal(steps.children[0].className,'step');
assert.equal(steps.children[0].children[0].textContent,'✓');assert.equal(steps.children[0].children[1].children[0].textContent,'check');assert.equal(steps.children[0].children[2].textContent,'成功');
assert.equal($('db-jobs-code').className,'codeblock');assert.match($('db-jobs-code').textContent,/SELECT 1/);
assert.ok($('db-jobs-facts').children.length>=10);
assert.match(visibleText($('db-jobs-facts')),/開始.*2026-10-29 10:02 JST/);assert.match(visibleText($('db-jobs-facts')),/所要時間.*1.8 秒/);assert.match(visibleText($('db-jobs-facts')),/実行者.*owner/);
assert.equal($('db-jobs-detail-title').textContent.includes(runId),false);
assert.equal($('db-jobs-output').textContent.includes(runId),false);
''')

    def test_unavailable_job_context_is_one_neutral_line_and_has_no_unavailable_option(self):
        self.javascript(JOBS_SETUP + CONTEXT + r'''
await boot([input()]);dbJobs.database='missing-database';dbJobs.schema='missing-schema';dbCurrent().database_id='';dbCurrent().schema_id='';dbExplorer.database_id='';dbExplorer.schema_id='';await dbJobsRefresh();setLanguage('ja');
assert.equal($('db-jobs-status').hidden,true);assert.equal($('db-jobs-saved-panel').hidden,true);
for(const id of ['db-jobs-database','db-jobs-schema'])assert.equal($(id).options.some(option=>/利用不可|Unavailable/.test(option.textContent)),false,id);
const tableText=visibleText($('db-jobs-recent').children[1].children[0]),guidance='ジョブを表示するデータベースを選んでください。';
assert.equal((tableText.match(new RegExp(guidance,'g'))||[]).length,1);
''')

    def test_jobs_labels_switch_language_without_losing_the_list(self):
        self.javascript(JOBS_SETUP + CONTEXT + r'''
savedJobs=[structuredClone(job)];jobRuns=[structuredClone(run)];await openJobs();await dbJobsListHistory();
for(const [lang,title,head,status,hint] of [['ja','ジョブ','ジョブ名','成功','実行詳細で処理ステップと出力データを確認できます。'],['en','Jobs','Job name','Succeeded','Open the run detail to inspect steps and output data.']]){
 setLanguage(lang);
 assert.equal($('db-jobs-title').textContent,title);assert.equal($('db-jobs-recent').children[0].children[0].textContent,lang==='ja'?'最近の実行':'Recent runs');
 assert.equal($('db-jobs-recent').children[1].children[0].children[1].children[0].children[0].textContent,head);
 assert.equal($('db-jobs-recent').children[1].children[0].children[2].children[0].children[1].children[0].textContent,status);
 assert.equal($('db-jobs-hint').textContent,hint);assert.equal(dbJobs.view,'list');
 assert.equal($('db-jobs-saved-panel').className,'panel');
}
''')

    def test_audit_has_the_mock_filters_actions_and_five_column_table(self):
        self.run_audit(r'''
auditCapabilities=()=>({scopes:['self','database'],sources:['query','ai'],platform_admin:false});await auditBoot();setLanguage('ja');
const event=auditEvent('event-1',{source:'query',action:'execute',state:'SUCCEEDED',created_at:'2026-10-29T01:02:00Z'});
auditList=(p,o)=>auditEnvelope(p,o,event);await dbAuditSearch();
const panel=$('db-audit-panel');assert.equal(panel.className,'audit-page');
assert.equal($('db-audit-title').parentElement.className,'atlas-flow');
assert.equal($('db-audit-title').textContent,'監査ログ');
const filters=$('audit-filters');assert.equal(filters.tagName,'DETAILS');assert.equal(filters.children[0].textContent,'絞り込み条件');assert.equal(filters.open,true);
const main=$('db-audit-main-fields');assert.equal(main.className,'atlas-fields');assert.deepEqual([...main.children].map(label=>label.htmlFor),['db-audit-scope','db-audit-database','db-audit-from','db-audit-to','db-audit-source','db-audit-search']);
for(const label of main.children){assert.equal(label.tagName,'LABEL');assert.equal(label.className,'atlas-field-label');assert.equal(label.children[0].tagName,'SPAN');assert.equal(label.children[1].className,'field');}
const advanced=$('db-audit-advanced-filters');assert.equal(advanced.tagName,'DETAILS');assert.equal(advanced.children[0].textContent,'詳細条件');assert.deepEqual([...advanced.children[1].children].map(label=>label.htmlFor),['db-audit-role','db-audit-state','db-audit-limit']);
const actions=$('db-audit-actions');assert.equal(actions.className,'atlas-actions');const buttons=actions.children.filter(node=>node.tagName==='BUTTON');
assert.deepEqual(buttons.map(node=>node.id),['db-audit-submit','db-audit-reset','db-audit-download']);
assert.deepEqual(buttons.map(node=>node.textContent),['絞り込む','条件をリセット','表示履歴の JSON を保存']);
assert.equal(panel.descendants().some(node=>String(node.id||'').startsWith('db-audit-preset-')),false);
assert.equal($('audit-applied').tagName,'P');assert.equal($('audit-applied').hidden,false);assert.match($('audit-applied').textContent,/適用中: /);
const table=$('db-audit-rows').children[0];assert.equal(table.tagName,'TABLE');
const heads=table.children[0].children[0].children;assert.deepEqual([...heads].map(node=>node.textContent),['日時（JST）','操作','対象','実行者','状態']);
const row=table.children[1].children[0];assert.equal(row.children.length,5);
assert.equal(row.children[0].children[0].tagName,'TIME');assert.equal(row.children[0].children[0].getAttribute('datetime'),'2026-10-29T01:02:00Z');
const action=row.children[1].children[0];assert.equal(action.tagName,'A');assert.equal(action.getAttribute('href'),'#audit/'+encodeURIComponent(event.id));assert.equal(action.textContent,'クエリ実行 / 実行');assert.equal(action.textContent.includes(event.id),false);
''')

    def test_audit_filters_fold_at_600(self):
        for narrow in (True, False):
            with self.subTest(narrow=narrow):
                self.run_audit(('narrow=' + ('true' if narrow else 'false') + ';\n') + r'''
await auditBoot();
assert.equal($('audit-filters').tagName,'DETAILS');assert.equal($('audit-filters').open,!narrow);
''')

    def test_audit_rows_and_headings_hide_raw_ids(self):
        self.run_audit(r'''
const databaseId='11111111-2222-4333-8444-555555555555',schemaId='66666666-7777-4888-9999-aaaaaaaaaaaa',eventId='bbbbbbbb-cccc-4ddd-8eee-ffffffffffff';
await auditBoot();setLanguage('ja');
auditList=(p,o)=>auditEnvelope(p,o,auditEvent(eventId,{source:'query',source_id:'cccccccc-dddd-4eee-8fff-000000000000',actor:'owner',database_id:databaseId,schema_id:schemaId,created_at:'2026-10-29T01:02:00Z',links:[]}));
await dbAuditSearch();
const rowText=auditText($('db-audit-rows'));
for(const id of [databaseId,schemaId,eventId,'cccccccc-dddd-4eee-8fff-000000000000'])assert.equal(rowText.includes(id),false,id);
auditDetail=(p,o)=>auditEnvelope(p,o,auditEvent(eventId,{source:'query',source_id:'cccccccc-dddd-4eee-8fff-000000000000',actor:'owner',database_id:databaseId,schema_id:schemaId,created_at:'2026-10-29T01:02:00Z',links:[]}));
await dbAuditDetail(eventId);
assert.equal($('db-audit-detail-title').textContent,'監査記録の詳細');assert.equal($('db-audit-detail-title').textContent.includes(eventId),false);
assert.ok(auditText($('db-audit-detail')).includes(eventId),'Event ID is an explicit reconciliation row');
assert.equal($('db-audit-detail').descendants().filter(node=>node.tagName==='BUTTON').length,1);
''')

    def test_audit_displayed_history_downloads_private_free_metadata(self):
        self.run_audit(r'''
const blobs=[],revoked=[];URL.createObjectURL=blob=>{blobs.push(blob);return 'blob:history';};URL.revokeObjectURL=url=>revoked.push(url);
await auditBoot();setLanguage('ja');const event=auditEvent('event-1',{created_at:'2026-10-29T01:02:00Z'});auditList=(p,o)=>auditEnvelope(p,o,event);await dbAuditSearch();
$('db-audit-download').onclick();assert.equal(blobs.length,1);assert.deepEqual(revoked,['blob:history']);
const payload=await blobs[0].text();assert.doesNotMatch(payload,/SECRET|https:\/\/never/);assert.match(payload,/event-1/);assert.match(payload,/2026-10-29T01:02:00Z/);
assert.match($('db-audit-status').textContent,/表示履歴の JSON を保存しました。/);
''')

    def test_audit_language_switch_keeps_applied_line_and_rows(self):
        self.run_audit(r'''
auditCapabilities=()=>({scopes:['self'],sources:['query','ai'],platform_admin:false});await auditBoot();
const event=auditEvent('event-1',{source:'query',action:'execute',state:'SUCCEEDED',created_at:'2026-10-29T01:02:00Z'});
auditList=(p,o)=>auditEnvelope(p,o,event);await dbAuditSearch();
const table=$('db-audit-rows').children[0],detail=table.parentElement;
setLanguage('ja');assert.equal($('audit-filters').children[0].textContent,'絞り込み条件');assert.match($('audit-applied').textContent,/適用中/);assert.equal($('db-audit-rows').children[0],table);assert.equal($('db-audit-rows').children[0].parentElement,detail);
setLanguage('en');assert.equal($('audit-filters').children[0].textContent,'Filters');assert.match($('audit-applied').textContent,/Applied/);assert.equal($('db-audit-rows').children[0],table);
''')

    def test_sidebar_jobs_link_returns_from_detail_to_list(self):
        self.run_url('http://local/#jobs/' + 'job-1', JOBS_SETUP + r'''
const context={database_id:'db',schema_id:'schema',warehouse_id:'LOCAL',sql_catalog:'sales',sql_schema:'marts',selected_role:'admin',binding_revision:1,generation_id:'gen',grant_revision:4};
savedJobs=[{id:'job-1',owner:'owner',selected_role:'admin',revision:1,context,definition:{name:'daily',description:'',parameters:{},steps:[{name:'check',action:'sql',sql:'SELECT 1',mode:'read-only'}]}}];
await boot();assert.equal(dbJobs.view,'detail');assert.ok(location.href.endsWith('#jobs/job-1'));
const result=$('tab-jobs').click();await result;await tick();await tick();
assert.equal(dbJobs.view,'list');assert.equal($('db-jobs-list-panel').hidden,false);assert.equal($('db-jobs-detail').hidden,true);
assert.equal(new URL(location.href).hash,'#jobs');
''')

    def test_sidebar_audit_link_returns_from_detail_to_list(self):
        self.run_url('http://local/#audit/event-1', audit_ui.AUDIT + r'''
route=async(path,options)=>{
 if(path==='/api/v1/audit/capabilities')return {scopes:['self','database'],sources:['query'],platform_admin:false};
 if(path.startsWith('/api/v1/audit/events/'))return auditEnvelope(path,options,auditEvent('event-1',{created_at:'2026-10-29T01:02:00Z'}));
 if(path.startsWith('/api/v1/audit/events?'))return auditEnvelope(path,options,auditEvent('event-1',{created_at:'2026-10-29T01:02:00Z'}));
 return {};
};
await boot();assert.equal(dbAudit.detail.id,'event-1');assert.ok(location.href.endsWith('#audit/event-1'));
const result=$('tab-pawprints').click();await result;await tick();await tick();
assert.equal(dbAudit.detail,null);assert.equal($('db-audit-detail').children.length,0);assert.equal(new URL(location.href).hash,'#audit');
''')

    def test_jobs_and_audit_open_at_scroll_top(self):
        self.run_url('http://local/#jobs', r'''
const scrolls=[];globalThis.scrollTo=(x,y)=>scrolls.push([x,y]);
await boot();await activate($('tab-pawprints'));await tick();await activate($('tab-jobs'));await tick();
assert.ok(scrolls.length>0);assert.equal(scrolls.every(([x,y])=>x===0&&y===0),true);assert.equal(document.documentElement.scrollTop,0);
''')

    def test_strict_connected_document_renders_jobs_and_audit(self):
        self.javascript(JOBS_SETUP + CONTEXT + r'''
savedJobs=[structuredClone(job)];jobRuns=[structuredClone(run)];
await boot([input(),input('b')]);documentRoot.append($('jobs'));connectedOnly=true;
await dbJobsOpen();await dbJobsListHistory();await dbJobsSelect(jobId);
const ids=['db-jobs-panel','db-jobs-pageheading','db-jobs-title','db-jobs-context-fields','db-jobs-recent','db-jobs-recent-runs','db-jobs-hint','db-jobs-saved-panel','db-jobs-list-rows','db-jobs-saved-details','db-jobs-detail','db-jobs-back','db-jobs-eyebrow','db-jobs-detail-title','db-jobs-steps-view','db-jobs-steps-list','db-jobs-code','db-jobs-output','db-jobs-facts','db-jobs-run-panel','db-jobs-run-detail','db-jobs-editor-panel','db-jobs-form','db-jobs-history'];
for(const id of ids){assert.ok(nodes[id],id);assert.equal(connected(nodes[id]),true,id);}
assert.equal($('db-jobs-title'),nodes['db-jobs-title']);assert.equal(dbJobs.view,'detail');
''')

    def test_strict_connected_document_renders_audit(self):
        self.run_audit(r'''
session.platform_admin=false;
route=async(path,options)=>{
 if(path==='/api/v1/audit/capabilities')return {scopes:['self','database'],sources:['query'],platform_admin:false};
 if(path.startsWith('/api/v1/audit/events?'))return auditEnvelope(path,options,auditEvent('event-1',{created_at:'2026-10-29T01:02:00Z'}));
 if(path.startsWith('/api/v1/audit/events/'))return auditEnvelope(path,options,auditEvent('event-1',{created_at:'2026-10-29T01:02:00Z'}));
 return {};
};
await dualBoot();documentRoot.append($('pawprints'));connectedOnly=true;
await dbAuditOpen();await dbAuditSearch();
const ids=['db-audit-panel','db-audit-flow','db-audit-title','audit-filters','db-audit-main-fields','db-audit-advanced-filters','db-audit-actions','db-audit-submit','db-audit-reset','db-audit-download','audit-applied','db-audit-status','db-audit-rows','db-audit-detail','db-audit-guide'];
for(const id of ids){assert.ok(nodes[id],id);assert.equal(connected(nodes[id]),true,id);}
assert.equal($('db-audit-rows'),nodes['db-audit-rows']);
''')

    def test_audit_resolves_database_and_runs_default_search_on_open(self):
        self.run_audit(r'''
auditCapabilities=()=>({scopes:['self','database'],sources:['query'],platform_admin:false});
await auditBoot();
dbAudit.database='';dbAudit.role='';dbAudit.autoUsed=false;dbAudit.applied=null;dbAudit.items=[];dbAudit.proof=null;dbAudit.capabilities=null;
activeTab='pawprints';
const before=auditRequests().length;
await dbAuditOpen();
assert.equal(auditRequests().length,before+1);
assert.equal(dbAudit.database,'db');
const option=$('db-audit-database').options.find(o=>o.value==='db');assert.ok(option);assert.equal(option.disabled,false);assert.equal(option.textContent.includes(t('Unavailable')),false);
assert.equal($('db-audit-submit').disabled,false);
assert.ok(dbAudit.applied);assert.equal(dbAudit.items.length,1);
assert.equal($('audit-applied').hidden,false);assert.match($('audit-applied').textContent,/Applied/);
assert.equal($('db-audit-rows').children[0].tagName,'TABLE');
$('db-audit-search').value='draft';$('db-audit-search').oninput();
const count=auditRequests().length;await tick();assert.equal(auditRequests().length,count);
''')

    def test_audit_target_column_never_shows_internal_ids(self):
        self.run_audit(r'''
await auditBoot();
const sources=[['job_run','job_run:run-2'],['query','query:q1'],['stage_run','stage_run:s1'],['wap_publication','wap_publication:p1']];
const events=sources.map(([source,source_id],i)=>auditEvent('event-'+i,{source,source_id,action:'execute',state:'SUCCEEDED',created_at:'2026-10-29T01:02:00Z'}));
auditList=(p,o)=>({...auditEnvelope(p,o),items:events});await dbAuditSearch();
const rows=$('db-audit-rows').children[0].children[1].children;
assert.equal(rows.length,4);
for(const row of rows){const text=auditText(row);for(const id of ['job_run:run-2','query:q1','stage_run:s1','wap_publication:p1','run-2','q1','s1','p1'])assert.equal(text.includes(id),false,id);}
assert.equal(auditText(rows[0]).includes(t('Job runs')),true);
assert.equal(auditText(rows[1]).includes(t('Query executions')),true);
''')

    def test_audit_empty_status_is_hidden_and_aria_label_is_localized(self):
        self.assertRegex(CSS, r'#db-audit-status:empty\s*\{\s*display\s*:\s*none')
        self.run_audit(r'''
await auditBoot();setLanguage('ja');await dbAuditSearch();
assert.equal($('db-audit-status').textContent,'');
assert.equal($('db-audit-rows').getAttribute('aria-label'),'監査履歴');
setLanguage('en');assert.equal($('db-audit-rows').getAttribute('aria-label'),'Audit history');
''')

    def test_audit_search_keeps_workspace_context(self):
        self.run_audit(r'''
await auditBoot();await dbApplyInventory(inventory,currentRole);workbenchRenderWorkspaceNav();
$('sql').value='SELECT 57 AS untouched';dbSave();
const worksheet=JSON.stringify(dbInput(dbCurrent())),label=$('nav-workspace-context-label').textContent;
const explorer=JSON.stringify(dbExplorer),databases=JSON.stringify(dbDatabases.map(d=>d.id));
const originalRestore=dbAuditWorkspaceRestore;let restored=0;dbAuditWorkspaceRestore=snapshot=>{restored++;return originalRestore(snapshot);};
activeTab='pawprints';await dbAuditOpen();await dbAuditSearch();
assert.ok(restored>0);
assert.equal($('nav-workspace-context-label').textContent,label);
assert.equal(JSON.stringify(dbInput(dbCurrent())),worksheet);
assert.equal(JSON.stringify(dbExplorer),explorer);
assert.equal(JSON.stringify(dbDatabases.map(d=>d.id)),databases);
''')

    def test_audit_uses_the_same_page_padding_as_other_screens(self):
        self.assertIn('#db-audit-panel{padding:0}', COMPACT_CSS)
        self.assertNotRegex(CSS, r'#db-audit-panel\s*\{\s*padding\s*:\s*(?:14|20)px')
        self.assertNotIn('@container db-audit', CSS)

    def test_job_detail_hides_list_context_and_edit_panels(self):
        self.javascript(JOBS_SETUP + CONTEXT + r'''
savedJobs=[structuredClone(job)];jobRuns=[structuredClone(run)];await openJobs();await dbJobsListHistory();await dbJobsSelect(jobId);
const detail=$('db-jobs-detail');
assert.equal($('db-jobs-list-panel').hidden,true);
assert.equal(detail.children[0],$('db-jobs-back'));
assert.equal(detail.descendants().some(node=>['db-jobs-title','db-jobs-context-fields','db-jobs-editor-panel','db-schedule-job-slot','db-jobs-history-panel','db-jobs-status'].includes(node.id)),false);
assert.equal($('db-jobs-edit-details').hidden,true);
assert.equal($('db-jobs-status').hidden,true);
assert.equal($('db-jobs-run-status').hidden,true);
''')

    def test_historical_run_shows_its_own_steps_and_revision(self):
        self.javascript(JOBS_SETUP + CONTEXT + r'''
const edited=structuredClone(job);edited.revision=2;
edited.definition.steps=[{name:'load',action:'sql',sql:'SELECT 2',mode:'read-only'},{name:'check',action:'sql',sql:'SELECT 1',mode:'read-only'}];
savedJobs=[edited];jobRuns=[structuredClone(run)];await openJobs();
await dbJobsSelectRun(structuredClone(run));
const steps=$('db-jobs-steps-list').children;
assert.equal(steps.length,1);
assert.equal(steps[0].children[1].children[0].textContent,'check');assert.equal(steps[0].children[0].textContent,'✓');
assert.equal(visibleText($('db-jobs-steps-list')).includes('load'),false);
assert.match(visibleText($('db-jobs-facts')),/v1/);
assert.equal($('db-jobs-code-note').textContent,t('Current definition, revision {revision}; this run used revision {run}',{revision:2,run:1}));
setLanguage('ja');assert.match($('db-jobs-code-note').textContent,/現在の定義（リビジョン 2）。この実行はリビジョン 1/);
''')

    def test_same_revision_run_never_borrows_status_by_position(self):
        self.javascript(JOBS_SETUP + CONTEXT + r'''
const renamed=structuredClone(run);renamed.steps=[{name:'other',action:'sql',status:'succeeded'}];
savedJobs=[structuredClone(job)];jobRuns=[renamed];await openJobs();
await dbJobsSelectRun(structuredClone(renamed));
const step=$('db-jobs-steps-list').children[0];
assert.equal(step.children[1].children[0].textContent,'check');assert.equal(step.children[0].textContent,'·');
assert.equal(step.children[2].textContent,t('No status'));
''')

    def test_step_icons_use_one_small_neutral_class_and_mark(self):
        self.assertIn('.step-icon{display:inline-flex', COMPACT_CSS)
        self.javascript(JOBS_SETUP + r'''
await createJob();await dbJobsRun();jobRuns[0].status='failed';jobRuns[0].steps[0].status='failed';await dbJobsObserve();
let step=$('db-jobs-steps-list').children[0],glyph=step.children[0];
assert.equal(glyph.className,'step-icon failed');assert.equal(glyph.textContent,'✕');assert.equal(glyph.className.includes('error'),false);
assert.equal(step.children[2].className,'status failed');
jobRuns[0].status='succeeded';jobRuns[0].steps[0].status='succeeded';await dbJobsObserve();
step=$('db-jobs-steps-list').children[0];glyph=step.children[0];
assert.equal(glyph.className,'step-icon success');assert.equal(glyph.textContent,'✓');assert.equal(step.children[2].className,'status success');
jobRuns[0].status='running';jobRuns[0].steps[0].status='running';await dbJobsObserve();
step=$('db-jobs-steps-list').children[0];glyph=step.children[0];
assert.equal(glyph.className,'step-icon muted');assert.equal(glyph.textContent,'·');
''')

    def test_job_edit_form_lives_below_the_list_and_is_aligned(self):
        self.assertIn('.jobs-edit-details .atlas-fields>.job-json{grid-column:1/-1', CSS)
        self.javascript(JOBS_SETUP + r'''
await openJobs();dbJobsNew();
assert.equal(dbJobs.view,'list');assert.equal(dbJobs.editOpen,true);assert.equal($('db-jobs-edit-details').open,true);
assert.equal($('db-jobs-edit-details').descendants().includes($('db-jobs-form')),true);
assert.equal($('db-jobs-detail').descendants().includes($('db-jobs-form')),false);
assert.equal($('db-jobs-detail').descendants().includes($('db-schedule-job-slot')),false);
assert.equal($('db-jobs-detail').descendants().includes($('db-jobs-history-panel')),false);
const labels=$('db-jobs-form').descendants().filter(node=>node.tagName==='LABEL'&&String(node.htmlFor||'').startsWith('db-jobs-'));
assert.ok(labels.length>=5);
for(const label of labels){assert.equal(label.className.startsWith('atlas-field-label'),true);assert.equal(label.children[0].tagName,'SPAN');assert.ok(['INPUT','TEXTAREA'].includes(label.children[1].tagName));}
const actions=$('db-jobs-save').parentElement;assert.equal(actions.className,'toolbar jobs-actions');assert.equal(actions.children.includes($('db-jobs-run')),true);
assert.equal($('db-jobs-save').parentElement.parentElement.id,'db-jobs-form');
assert.equal($('db-schedule-job-slot').parentElement.id,'db-jobs-edit-details');
''')

    def test_recent_runs_open_their_own_run_detail(self):
        self.javascript(JOBS_SETUP + CONTEXT + r'''
const second={...structuredClone(run),id:'b7b8c9d0-1111-4222-8333-944455556667',status:'failed',started_at:'2026-10-30T01:02:00Z',created_at:'2026-10-30T01:02:00Z',finished_at:'2026-10-30T01:02:02.000Z',steps:[{name:'check',action:'sql',status:'failed',summary:{code:'JOB_STEP_FAILED'}}]};
savedJobs=[structuredClone(job)];jobRuns=[structuredClone(run),second];await openJobs();await dbJobsListHistory();
let table=$('db-jobs-recent').children[1].children[0],links=table.children[2].children.map(row=>row.children[0].children[0]);
assert.notEqual(links[0].getAttribute('href'),links[1].getAttribute('href'));
assert.ok(links[0].getAttribute('href').includes(encodeURIComponent(run.id)));
assert.ok(links[1].getAttribute('href').includes(encodeURIComponent(second.id)));
await links[0].onclick();assert.equal(dbJobs.view,'detail');assert.equal(dbJobs.run.id,run.id);assert.match($('db-jobs-eyebrow').textContent,/2026-10-29 10:02 JST/);
dbJobsShowList();await tick();
table=$('db-jobs-recent').children[1].children[0];links=table.children[2].children.map(row=>row.children[0].children[0]);
await links[1].onclick();assert.equal(dbJobs.run.id,second.id);assert.match($('db-jobs-eyebrow').textContent,/2026-10-30 10:02 JST/);
assert.equal($('db-jobs-eyebrow').textContent.includes(second.id),false);
''')

    def test_run_deep_link_restores_the_selected_run(self):
        self.run_url('http://local/#jobs/run/' + 'a7b8c9d0-1111-4222-8333-944455556666', JOBS_SETUP + CONTEXT + r'''
savedJobs=[structuredClone(job)];jobRuns=[structuredClone(run)];
await boot();assert.equal(dbJobs.view,'detail');assert.equal(dbJobs.run.id,run.id);
assert.match($('db-jobs-eyebrow').textContent,/2026-10-29 10:02 JST/);assert.equal($('db-jobs-eyebrow').textContent.includes(run.id),false);
''')

    def test_jobs_use_workspace_context_and_show_one_guidance(self):
        self.javascript(JOBS_SETUP + CONTEXT + r'''
savedJobs=[];jobRuns=[];await openJobs();setLanguage('ja');
assert.equal(dbJobs.database,'db');assert.equal(dbJobs.schema,'schema');
assert.equal($('db-jobs-database').value,'db');assert.equal($('db-jobs-schema').value,'schema');
assert.equal($('db-jobs-status').hidden,true);assert.equal($('db-jobs-status').textContent,t('Ready'));
dbJobs.database='';dbJobs.schema='';dbCurrent().database_id='';dbCurrent().schema_id='';dbExplorer.database_id='';dbExplorer.schema_id='';
await dbJobsRefresh();
assert.equal($('db-jobs-status').hidden,true);assert.equal($('db-jobs-saved-panel').hidden,true);assert.equal($('db-jobs-edit-details').hidden,true);
const list=$('db-jobs-list-panel'),text=visibleText(list),guidance='ジョブを表示するデータベースを選んでください。';
assert.equal((text.match(new RegExp(guidance,'g'))||[]).length,1);
assert.equal(visibleText($('db-jobs-recent').children[1].children[0].children[2].children[0]).includes(guidance),true);
''')

    def test_r6_job_summaries_buttons_and_labels_are_localized(self):
        self.javascript(JOBS_SETUP + r'''
await openJobs();
setLanguage('ja');
assert.equal($('db-jobs-edit-details').children[0].textContent,'ジョブの登録・編集');
assert.equal($('db-jobs-edit').textContent,'ジョブを編集');
assert.equal($('db-jobs-new').textContent,'ジョブを登録');
assert.equal($('db-jobs-load').textContent,'ジョブを読み込む');
assert.equal($('db-jobs-save').textContent,'ジョブを保存');
assert.equal($('db-jobs-run').textContent,'ジョブを実行');
assert.equal($('db-jobs-observe').textContent,'実行を照会');
assert.equal($('db-jobs-cancel').textContent,'実行を取り消す');
assert.equal($('db-jobs-reconcile').textContent,'不明な実行を照合');
setLanguage('en');
assert.equal($('db-jobs-edit-details').children[0].textContent,'Job registration and edit');
assert.equal($('db-jobs-edit').textContent,'Edit job');
assert.equal($('db-jobs-save').textContent,'Save Job');
assert.equal($('db-jobs-run').textContent,'Run Job');
''')

    def test_r6_audit_summaries_buttons_and_aria_are_localized(self):
        self.run_audit(r'''
await auditBoot();
setLanguage('ja');
assert.equal($('audit-filters').children[0].textContent,'絞り込み条件');
assert.equal($('db-audit-advanced-filters').children[0].textContent,'詳細条件');
assert.deepEqual([...$('db-audit-actions').children].map(node=>node.textContent),['絞り込む','条件をリセット','表示履歴の JSON を保存']);
assert.equal($('db-audit-guide').children[0].textContent,'保存する情報');
assert.equal($('db-audit-rows').getAttribute('aria-label'),'監査履歴');
setLanguage('en');
assert.equal($('audit-filters').children[0].textContent,'Filters');
assert.equal($('db-audit-advanced-filters').children[0].textContent,'Advanced filters');
assert.deepEqual([...$('db-audit-actions').children].map(node=>node.textContent),['Filter','Reset filters','Save displayed history as JSON']);
assert.equal($('db-audit-guide').children[0].textContent,'What is saved');
assert.equal($('db-audit-rows').getAttribute('aria-label'),'Audit history');
''')

    def test_registered_job_status_uses_mock_status_not_error_badge(self):
        self.javascript(JOBS_SETUP + CONTEXT + r'''
const failed={...structuredClone(run),status:'failed',steps:[{name:'check',action:'sql',status:'failed',summary:{code:'JOB_STEP_FAILED'}}]};
savedJobs=[structuredClone(job)];jobRuns=[failed];await openJobs();await dbJobsListHistory();setLanguage('ja');
let row=$('db-jobs-list-rows').children[0],status=row.children[2].children[0];
assert.equal(status.tagName,'SPAN');assert.equal(status.className,'status failed');assert.equal(status.textContent,'失敗');
assert.equal(status.className.includes('badge'),false);assert.equal(status.className.includes('tone-'),false);assert.equal(status.className.includes('error'),false);
let recent=$('db-jobs-recent').children[1].children[0].children[2].children[0].children[1].children[0];
assert.equal(recent.className,'status failed');assert.equal(recent.textContent,'失敗');
await dbJobsSelectRun(failed);
assert.equal($('db-jobs-detail-status').className,'status failed');assert.equal($('db-jobs-detail-status').textContent,'失敗');
assert.equal($('db-jobs-steps-list').children[0].children[2].className,'status failed');
assert.equal(dbJobsRenderList.toString().includes('statusTone'),false);
assert.equal(dbJobsRenderList.toString().includes('dbJobsStatusClass'),true);
''')

    def test_audit_row_click_scrolls_and_focuses_detail_heading(self):
        self.run_audit(r'''
await auditBoot();activeTab='pawprints';setLanguage('ja');await dbAuditSearch();
const link=$('db-audit-rows').children[0].children[1].children[0].children[1].children[0];
await link.onclick();
const title=$('db-audit-detail-title');
assert.equal(document.activeElement,title);assert.deepEqual(title.scrolled,{block:'start'});
assert.equal($('db-audit-detail').children.length>0,true);
''')

    def test_job_new_and_edit_scroll_and_focus_edit_details(self):
        self.javascript(JOBS_SETUP + r'''
await openJobs();dbJobsNew();
let details=$('db-jobs-edit-details'),summary=details.children[0];
assert.equal(details.open,true);assert.equal(document.activeElement,summary);assert.deepEqual(details.scrolled,{block:'start'});
$('db-jobs-name').value='daily';await dbJobsSave();assert.equal(dbJobs.job.id,'job-1');
dbJobsShowList();$('db-jobs-list').value='job-1';details.scrolled=null;await dbJobsEdit();
details=$('db-jobs-edit-details');summary=details.children[0];
assert.equal(details.open,true);assert.equal(document.activeElement,summary);assert.deepEqual(details.scrolled,{block:'start'});
''')

    def test_mock_css_and_mobile_touch_rules_are_used(self):
        self.assertIn('.pageheading{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:24px}', COMPACT_CSS)
        self.assertIn('.jobdetailgrid{display:grid;grid-template-columns:minmax(0,1.7fr)minmax(240px,1fr);gap:26px}', COMPACT_CSS)
        self.assertIn('.step{display:grid;grid-template-columns:20pxminmax(0,1fr)auto;gap:10px;padding:15px0;border-bottom:1pxsolidvar(--line)}', COMPACT_CSS)
        self.assertIn('.codeblock{font:11px/1.9ui-monospace,SFMono-Regular,Consolas,monospace', COMPACT_CSS)
        self.assertIn('.status{display:inline-flex;align-items:center;gap:5px;font-size:10px;white-space:nowrap}', COMPACT_CSS)
        self.assertIn('@media(max-width:850px){.jobdetailgrid{grid-template-columns:1fr}}', COMPACT_CSS)
        self.assertRegex(CSS, r'@media\s*\(max-width:\s*600px\)\s*\{[\s\S]*?\.jobs-page :is\(button,select,input:not\(\[type=checkbox\]\):not\(\[type=radio\]\),textarea,summary\)[\s\S]*?min-height:\s*44px')
        self.assertRegex(CSS, r'@media\s*\(max-width:\s*600px\)\s*\{[\s\S]*?\.jobs-page a\[href\][\s\S]*?min-height:\s*44px')
        self.assertIn('overflow:auto', COMPACT_CSS)
        self.assertRegex(CSS, r'#audit-filters\s*\{')
        self.assertRegex(CSS, r'#audit-applied\s*\{')


if __name__ == '__main__':
    unittest.main()
