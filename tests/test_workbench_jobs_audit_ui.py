"""UX-A6a: Atlas jobs and audit log, JST times and the notification popover."""
import json
import re
import unittest

import test_workbench_audit_database_ui as audit_ui
import test_workbench_database_ui as harness
from test_workbench_jobs_database_ui import SETUP as JOBS_SETUP
from test_workbench_navigation_ui import URL_SETUP
from test_workbench_schedules_database_ui import SETUP as SCHEDULES_SETUP
from workbench_source import WorkbenchSource, function as source_function


HTML = harness.HTML
CSS = WorkbenchSource(HTML).css
SOURCE = WorkbenchSource(HTML)
LOAD_JOB_ALERTS = source_function('loadJobAlerts', SOURCE.sources)


class JobsAuditAtlasUI(unittest.TestCase):
    javascript = harness.DatabaseUI.javascript

    def run_url(self, url, body):
        output = self.javascript(URL_SETUP.replace('START_URL', json.dumps(url)) + body +
                                 '\nconsole.log("NAVIGATION_COMPLETE");')
        self.assertTrue(output.endswith('NAVIGATION_COMPLETE\n'), 'Initialization did not complete: ' + output)
        return output.removesuffix('NAVIGATION_COMPLETE\n')

    def test_database_jobs_do_not_call_legacy_jobs_or_show_legacy_errors(self):
        self.javascript(JOBS_SETUP + r'''
await openJobs();await activate($('tab-jobs'));
assert.equal(calls.filter(c=>c.path.startsWith('/api/jobs')).length,0);
document.dispatchEvent?.(new Event('visibilitychange'));
assert.equal(calls.filter(c=>c.path.startsWith('/api/jobs')).length,0);
assert.equal($('jobs-error').hidden,true);assert.equal($('jobs-error').textContent,'');
''')

    def test_job_labels_schedule_options_order_and_recent_runs(self):
        self.javascript(JOBS_SETUP + r'''
const context={database_id:'db',schema_id:'schema',warehouse_id:'LOCAL',sql_catalog:'sales',sql_schema:'marts',selected_role:'admin',binding_revision:1,generation_id:'gen',grant_revision:4};
jobRuns=[{id:'run-1',job_id:'job-1',job_name:'daily',revision:1,status:'succeeded',context:structuredClone(context),created_at:'2026-10-29T01:02:00Z'}];
await openJobs();await tick();await tick();
assert.ok($('db-jobs-recent-runs').children.length>=1,'recent runs load with the list');
assert.ok($('db-jobs-recent-runs').descendants().some(n=>n.textContent==='daily'));
assert.ok($('db-jobs-recent-runs').descendants().some(n=>String(n.textContent).includes('2026-10-29 10:02 JST')));
$('db-jobs-name').value='daily';await dbJobsSave();
setLanguage('ja');
const labelFor=id=>$('db-jobs-panel').descendants().find(n=>n.tagName==='LABEL'&&n.htmlFor===id);
const labelText=node=>[node.textContent,...node.descendants().map(n=>n.textContent)].join(' ');
assert.equal($('db-jobs-save').textContent,'ジョブを保存');
assert.equal($('db-jobs-run').textContent,'ジョブを実行');
assert.ok(labelText(labelFor('db-jobs-name')).includes('ジョブ名'));
assert.ok(labelText(labelFor('db-jobs-description')).includes('ジョブの説明'));
for(const n of $('db-jobs-panel').descendants().filter(n=>['BUTTON','LABEL','P','H1','H2','H3'].includes(n.tagName)&&n.textContent.includes('Job')))throw Error('untranslated Job: '+n.textContent);
const kinds=$('db-schedule-job-kind').options;
assert.deepEqual(kinds.map(o=>o.value),['interval','daily','once','weekly','monthly']);
assert.deepEqual(kinds.map(o=>o.textContent),['一定間隔','毎日','1回だけ','毎週','毎月']);
$('db-jobs-steps').value=JSON.stringify([{name:'write',action:'sql',sql:'SELECT 1',mode:'read-write'}]);$('db-jobs-steps').oninput();
assert.ok($('db-jobs-steps-list').descendants().some(n=>n.textContent.includes('読み書き')));
const detailView=$('db-jobs-detail'),edit=$('db-jobs-edit-details');
assert.equal(detailView.children.some(n=>['db-jobs-editor-panel','db-schedule-job-slot','db-jobs-history-panel'].includes(n.id)),false);
assert.equal(edit.parentElement,$('db-jobs-list-panel'));
const order=['db-jobs-editor-panel','db-schedule-job-slot','db-jobs-history-panel'].map(id=>edit.children.indexOf($(id)));
assert.equal($('db-schedule-job-slot').descendants().includes($('db-schedule-job-panel')),true);
assert.ok(order.every(index=>index>=0),'edit sections live under the list details');
assert.ok(order[0]<order[1]&&order[1]<order[2],JSON.stringify(order));
assert.equal($('db-jobs-steps-view').parentElement.parentElement.className,'jobdetailgrid');
assert.equal(p.form.parentElement.parentElement.id,'db-jobs-editor-panel');
const before=calls.filter(c=>c.path.startsWith('/api/v1/jobs/runs')).length;
currentRole='bi_viewer';dbApplyRole();await dbJobsShowList({route:false});
assert.equal(calls.filter(c=>c.path.startsWith('/api/v1/jobs/runs')).length,before,'viewer does not request runs');
''')

    def test_database_job_list_detail_and_deep_link_restore(self):
        self.run_url('http://local/#jobs/job-1', JOBS_SETUP + r'''
const context={database_id:'db',schema_id:'schema',warehouse_id:'LOCAL',sql_catalog:'sales',sql_schema:'marts',selected_role:'admin',binding_revision:1,generation_id:'gen',grant_revision:4};
savedJobs=[{id:'job-1',owner:'owner',selected_role:'admin',revision:1,context,definition:{name:'daily',description:'Refresh sales',parameters:{n:{type:'integer',required:true}},steps:[{name:'check',action:'sql',sql:'SELECT {{n}}',mode:'read-only'}]}}];
await boot();
assert.equal(selected(),'jobs');
assert.equal(dbJobs.view,'detail');assert.equal(dbJobs.job.id,'job-1');
assert.equal($('db-jobs-detail-title').textContent,'daily');
assert.equal($('db-jobs-list-panel').hidden,true);assert.equal($('db-jobs-detail').hidden,false);
assert.ok(location.href.endsWith('#jobs/job-1'));
assert.equal($('db-jobs-steps-list').children.length,1);
dbJobsShowList();
assert.equal(dbJobs.view,'list');assert.equal($('db-jobs-list-panel').hidden,false);assert.equal($('db-jobs-detail').hidden,true);
assert.ok(location.href.endsWith('#jobs'));
await dbJobsSelect('job-1');
assert.equal(dbJobs.view,'detail');assert.equal(dbJobs.job.id,'job-1');assert.ok(location.href.endsWith('#jobs/job-1'));
// Reload/history restore reads the same job id from the hash.
dbJobsShowList();workbenchShellReady=true;workbenchSession=session;workbenchHistoryHref=location.href;
location.href='http://local/#jobs/job-1';await workbenchHistoryRestore();
assert.equal(dbJobs.view,'detail');assert.equal(dbJobs.job.id,'job-1');assert.equal($('db-jobs-detail-title').textContent,'daily');
location.href='http://local/#jobs';await workbenchHistoryRestore();
assert.equal(dbJobs.view,'list');assert.equal($('db-jobs-detail').hidden,true);
''')

    def test_database_job_create_steps_arguments_run_and_edit_form(self):
        self.javascript(JOBS_SETUP + r'''
const visible=node=>{for(let n=node;n;n=n.parentElement){if(n.hidden)return false;if(n.tagName==='DETAILS'&&!n.open&&node!==n.children[0])return false;}return true;};
const primaries=()=>$('db-jobs-panel').descendants().filter(node=>String(node.className).split(' ').includes('primary')&&visible(node));
await openJobs();await activate($('tab-jobs'));
assert.equal(primaries().length,1);assert.equal(primaries()[0].id,'db-jobs-new');
$('db-jobs-name').value='daily';
$('db-jobs-parameters').value=JSON.stringify({n:{type:'integer',required:true}});
$('db-jobs-steps').value=JSON.stringify([{name:'check',action:'sql',sql:'SELECT {{n}} AS n',mode:'read-only'}]);
$('db-jobs-steps').oninput();
assert.equal($('db-jobs-steps-list').children.length,1);
await dbJobsSave();
assert.equal(dbJobs.job.id,'job-1');assert.equal(dbJobs.view,'detail');
assert.equal(primaries().length,0);assert.equal($('db-jobs-detail').descendants().some(n=>n.id==='db-jobs-editor-panel'),false);
dbJobsShowList();$('db-jobs-list').value='job-1';await dbJobsEdit();
assert.equal(dbJobs.view,'list');assert.equal(dbJobs.editOpen,true);assert.equal($('db-jobs-edit-details').open,true);assert.equal($('db-jobs-detail').hidden,true);
assert.equal($('db-jobs-edit-details').descendants().includes($('db-jobs-form')),true);
assert.equal(primaries().some(node=>node.id==='db-jobs-save'),true);
$('db-jobs-arguments').value=JSON.stringify({n:7});
await dbJobsRun();
assert.equal(p.run.id,'run-1');
assert.equal(JSON.parse(jobPosts().at(-1).options.body).arguments.n,7);
assert.equal($('db-jobs-observe').disabled,false);
await dbJobsListHistory();
assert.equal(p.history.length,1);assert.equal(p.history[0].id,'run-1');
assert.ok($('db-jobs-history').children.length>0);
''')

    def test_schedule_is_jst_and_alert_ids_remain(self):
        self.javascript(JOBS_SETUP + r'''
await createJob();
dbScheduleSync('job');
const s=dbSchedules.get('job');assert.ok(s);
assert.ok($('db-schedule-job-panel'));assert.ok($('db-schedule-job-alerts-refresh'));
assert.ok($('db-schedule-job-time').tagName==='INPUT');assert.ok($('db-schedule-job-timezone').tagName==='INPUT');
assert.equal($('db-schedule-job-timezone').value,'Asia/Tokyo');
const disabledHelp=t('Schedules start disabled. Save changes before explicitly enabling.');
assert.equal($('db-jobs-edit-details').descendants().filter(n=>n.textContent===disabledHelp).length,1);
const display=dbScheduleDate('2028-10-23T00:00:00Z',{spec:{kind:'daily',timezone:'Asia/Tokyo'}},'job');
assert.match(display,/2028-10-23 09:00:00 Asia\/Tokyo/);assert.match(display,/2028-10-23 09:00 JST/);
assert.match(dbJobsRenderRecentRuns.toString(),/fmtJST/);
''')

    def test_schedule_save_stop_and_alert_refresh_after_tab_merge(self):
        self.javascript(SCHEDULES_SETUP + r'''
const s=await ready();
node('kind').value='daily';node('kind').onchange();
node('time').value='09:30';node('timezone').value='Asia/Tokyo';
await dbScheduleSave(s,'save',false);
assert.equal(s.record.enabled,false);
await dbScheduleSave(s,'enable',true);assert.equal(s.record.enabled,true);
assert.match(node('detail').textContent,/2028-10-23 09:00:00 Asia\/Tokyo.*JST/);
await dbScheduleSave(s,'stop',false);assert.equal(s.record.enabled,false);
alerts=[{id:'alert-1',owner:'owner',database_id:dbJobs.context.database_id,selected_role:dbJobs.context.selected_role,context:structuredClone(dbJobs.context),message:'Disk low',acknowledged_at:null,resolved_at:null,occurrences:2}];
await dbScheduleAlerts(s);
assert.equal(s.alerts.length,1);assert.equal(workbenchAlertCount,1);
const alertText=node=>[node.textContent,...node.descendants().map(n=>n.textContent)].join(' | ');
const rows=$('workbench-alert-list').children;assert.equal(rows.length,1);assert.equal(rows[0].id,'workbench-alert-latest');assert.ok(alertText(rows[0]).includes('Disk low'));
assert.match($('workbench-alert-total').textContent,/1/);
assert.equal(schedulePosts().every(c=>!!c.options.headers['X-Idempotency-Key']),true);
assert.equal(calls.some(c=>c.path.startsWith('/api/jobs/schedules')),false);
''')

    def test_notification_popover_count_latest_list_and_jobs_link(self):
        self.javascript(JOBS_SETUP + LOAD_JOB_ALERTS + r'''
let stageEpoch=0;
await openJobs();await activate($('tab-jobs'));
const context=dbJobs.context;
const alert=(message,extra={})=>({id:message,owner:'owner',database_id:context.database_id,selected_role:context.selected_role,context:structuredClone(context),message,severity:'critical',job_name:'daily',last_seen_at:'2026-10-29T01:02:00Z',acknowledged_at:null,resolved_at:null,...extra});
const alerts=[alert('Disk low'),alert('Orders late'),alert('Old alert',{acknowledged_at:'2026-10-28T00:00:00Z'}),alert('Resolved alert',{resolved_at:'2026-10-28T00:00:00Z',severity:'warning'})];
const previous=route;
route=async(path,options)=>path.startsWith('/api/v1/alerts')?{context:structuredClone(context),alerts:structuredClone(alerts),unacknowledged_count:2}:previous(path,options);
const before=calls.filter(c=>c.path.startsWith('/api/v1/alerts')).length;
await loadJobAlerts(false);
assert.equal(workbenchAlertCount,2);assert.equal(workbenchAlerts.length,4);
assert.equal($('workbench-alert-count').textContent,'2');assert.equal($('workbench-alert-count').hidden,false);
assert.match($('workbench-alert-total').textContent,/2/);
const alertText=node=>[node.textContent,...node.descendants().map(n=>n.textContent)].join(' | ');
const rows=$('workbench-alert-list').children;assert.equal(rows.length,2,'acknowledged and resolved alerts are excluded');
assert.equal(rows[0].id,'workbench-alert-latest');assert.ok(alertText(rows[0]).includes('critical'));assert.ok(alertText(rows[0]).includes('Disk low'));assert.match(alertText(rows[0]),/daily.*2026-10-29 10:02 JST/);
assert.ok(alertText(rows[1]).includes('Orders late'));assert.equal(rows[1].id,undefined);
assert.equal(calls.filter(c=>c.path.startsWith('/api/v1/alerts')).length,before+1);
assert.equal(calls.some(c=>c.path==='/api/alerts'||c.path.startsWith('/api/alerts?')),false);
$('workbench-alerts-popover').hidden=true;$('workbench-alerts-button').onclick();
assert.equal($('workbench-alerts-popover').hidden,false);
assert.ok($('workbench-alerts-open-jobs'));
const current=$('db-schedule-job-alerts-refresh');assert.ok(current);
$('workbench-alerts-open-jobs').onclick();assert.equal($('workbench-alerts-popover').hidden,true);assert.equal(activeTab,'jobs');
currentRole='bi_viewer';dbApplyRole();const after=calls.filter(c=>c.path.startsWith('/api/v1/alerts')).length;
await loadJobAlerts(false);
assert.equal(calls.filter(c=>c.path.startsWith('/api/v1/alerts')).length,after);
assert.equal(workbenchAlertCount,0);assert.equal($('workbench-alert-count').hidden,true);
''')

    def test_audit_role_state_action_and_applied_summary(self):
        self.javascript(audit_ui.AUDIT + r'''
auditCapabilities=()=>({scopes:['self','database'],sources:['query','wap_publication','wap_build','wap_audit','wap_model_version'],platform_admin:false});
await auditBoot();
const roleOptions=[...$('db-audit-role').options].filter(o=>o.value);
assert.deepEqual(roleOptions.map(o=>o.value),['bi_viewer','data_engineer','admin']);
assert.deepEqual(roleOptions.map(o=>o.textContent),[t('BI Viewer'),t('Data Engineer'),t('Admin')]);
setLanguage('ja');
assert.equal($('db-audit-role').options.find(o=>o.value==='admin').textContent,'管理者');
assert.equal(t('WAP publications'),'データ変換の公開');
assert.equal(t('WAP builds'),'データ変換のビルド');
assert.equal(t('WAP audits'),'データ変換の監査');
assert.equal(t('WAP model versions'),'データ変換のモデル版');
const sourceOption=value=>$('db-audit-source').options.find(o=>o.value===value);
assert.equal(sourceOption('wap_publication').textContent,'データ変換の公開');
assert.equal($('audit-filters').children[0].tagName,'SUMMARY');
assert.equal($('audit-filters').children[0].textContent,t('Filters'));
assert.equal($('db-audit-panel').descendants().some(n=>String(n.id||'').startsWith('db-audit-preset-')),false);
await dbAuditSearch();
const cell=document.createElement('td');dbAuditValue(cell,'SUCCEEDED');assert.equal(cell.textContent,'完了');
dbAuditValue(cell,'succeeded');assert.equal(cell.textContent,'成功');
dbAuditValue(cell,'custom_state');assert.equal(cell.textContent,'custom_state');
const event=auditEvent('state',{source:'query',action:'execute',state:'SUCCEEDED'});
auditList=(p,o)=>auditEnvelope(p,o,event);await dbAuditSearch();
const rowText=auditText($('db-audit-rows'));
assert.ok(rowText.includes('完了'));assert.ok(rowText.includes('実行'));assert.ok(rowText.includes('クエリ実行'));
assert.equal($('audit-applied').hidden,false);assert.match(auditText($('audit-applied')),/適用中.*自分の DB 記録.*1 件/);
assert.equal($('db-audit-panel').descendants().some(n=>n.id==='db-audit-timezone-note'),false);
''')

    def test_audit_japanese_sources_jst_list_utc_detail_and_restore(self):
        self.run_url('http://local/#audit/event-1', audit_ui.AUDIT + r'''
route=async(path,options)=>{
 if(path==='/api/v1/audit/capabilities')return {scopes:['self','database'],sources:['query','dataops','job_run','stage_run'],platform_admin:false};
 if(path.startsWith('/api/v1/audit/events/'))return auditEnvelope(path,options,auditEvent('event-1',{created_at:'2026-10-29T01:02:00Z'}));
 if(path.startsWith('/api/v1/audit/events?'))return auditEnvelope(path,options,auditEvent('event-1',{created_at:'2026-10-29T01:02:00Z'}));
 return {};
};
await boot();
assert.equal(selected(),'pawprints');assert.equal(dbAudit.detail.id,'event-1');
assert.ok(location.href.endsWith('#audit/event-1'));
assert.equal($('db-audit-detail-title').textContent,t('Audit record details'));
assert.ok(auditText($('db-audit-detail')).includes('event-1'));
assert.match(auditText($('db-audit-detail')),/2026-10-29 10:02:00 JST/);
assert.match(auditText($('db-audit-detail')),/UTC 2026-10-29T01:02:00Z/);
await dbAuditSearch();
assert.match(auditText($('db-audit-rows')),/2026-10-29 10:02:00 JST/);
assert.doesNotMatch(auditText($('db-audit-rows')),/UTC/);
const option=value=>$('db-audit-source').options.find(o=>o.value===value);
assert.equal(option('query').textContent,t('Query executions'));
assert.equal(option('dataops').textContent,t('DataOps operations'));
assert.equal(option('job_run').textContent,t('Job runs'));
assert.equal(option('stage_run').textContent,t('Stage runs'));
setLanguage('ja');
assert.equal($('db-audit-title').textContent,'監査ログ');
assert.equal(option('query').textContent,'クエリ実行');
assert.equal(option('job_run').textContent,'ジョブ実行');
assert.equal($('audit-filters').children[0].textContent,'絞り込み条件');
assert.match(auditText($('audit-applied')),/適用中.*1 件/);
''')

    def test_audit_filters_fold_at_600_and_summary_count(self):
        for narrow in (True, False):
            with self.subTest(narrow=narrow):
                self.javascript(audit_ui.AUDIT + r'''
narrow=NARROW;
await auditBoot();
assert.equal($('audit-filters').tagName,'DETAILS');
assert.equal($('audit-filters').open,!NARROW);
$('db-audit-from').value='2026-10-28T14:30';$('db-audit-from').oninput();
$('db-audit-source').value='query';$('db-audit-source').oninput();
$('db-audit-limit').value='2';$('db-audit-limit').oninput();
await dbAuditSearch();
assert.match(auditText($('audit-applied')),/Applied:.*2026-10-28 14:30 JST.*Query executions.*1/);
setLanguage('ja');
assert.equal($('audit-filters').children[0].textContent,'絞り込み条件');
assert.match(auditText($('audit-applied')),/適用中.*2026-10-28 14:30 JST.*クエリ実行.*1 件/);
'''.replace('NARROW', 'true' if narrow else 'false'))

    def test_audit_utc_is_absent_from_list_and_present_only_in_detail(self):
        self.javascript(audit_ui.AUDIT + r'''
await auditBoot();
const timed=auditEvent('timed',{created_at:'2026-10-29T01:02:00Z'});
auditList=(p,o)=>auditEnvelope(p,o,timed);auditDetail=(p,o)=>auditEnvelope(p,o,timed);
await dbAuditSearch();
assert.match(auditText($('db-audit-rows')),/Time \(JST\)/);
assert.doesNotMatch(auditText($('db-audit-rows')),/Time UTC/);
assert.match(auditText($('db-audit-rows')),/2026-10-29 10:02:00 JST/);
await dbAuditDetail(timed.id);
assert.match(auditText($('db-audit-detail')),/2026-10-29 10:02:00 JST/);
assert.match(auditText($('db-audit-detail')),/UTC 2026-10-29T01:02:00Z/);
''')

    def test_touch_targets_and_no_page_level_horizontal_overflow(self):
        self.assertRegex(CSS, r'@media\(max-width:600px\)\{[\s\S]*?#jobs :is\(button,select,input:not\(\[type=checkbox\]\):not\(\[type=radio\]\),textarea,summary,a\)[\s\S]*?min-height:44px')
        self.assertRegex(CSS, r'#workbench-alerts-popover button,#workbench-alerts-popover a\{min-height:44px\}')
        self.assertIn('#audit-filters>summary', CSS)
        self.assertIn('#db-jobs-list-rows .objectrow', CSS)
        self.assertIn('#db-jobs-panel,#db-audit-panel{min-width:0}', CSS)


if __name__ == '__main__':
    unittest.main()
