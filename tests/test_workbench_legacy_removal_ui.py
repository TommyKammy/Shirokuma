"""UX-A7: removed legacy controls stay out of the Atlas shell."""
import subprocess
import unittest

import test_workbench_database_ui as harness
from workbench_source import WorkbenchSource

HTML = harness.HTML
SOURCE = WorkbenchSource(HTML).source
BASE_I18N = WorkbenchSource(HTML).asset_sources['/assets/base_i18n.js']

REMOVED_IDS = [
    'refresh-pawprints',
    'refresh-history',
    'history-error',
    'load-history-results',
    'runtime-queries-results',
    'pawprint-error',
    'pawprint-results',
    'pawprint-meta',
    'pawprint-filter',
    'pawprint-status-filter',
    'jobs-view-definitions',
    'jobs-view-runs',
    'jobs-view-alerts',
    'jobs-alert-tab-count',
    'db-audit-telemetry',
    'db-audit-preview',
    'db-audit-preview-controls',
    'db-audit-preview-search',
    'db-audit-preview-status',
    'lineage',
    'ingestion-mode-guidance',
    'transformation-mode-guidance',
    'bears-mode-guidance',
    'compute-mode-guidance',
    'audit-mode-guidance',
    'jobs-definitions',
    'jobs-runs',
    'jobs-alerts',
    'job-form',
    'job-save',
    'job-save-state',
    'job-schedule-form',
    'job-schedule-enabled',
    'job-schedule-save',
    'job-schedule-stop',
    'job-schedule-message',
    'job-alerts',
    'job-alerts-refresh',
    'job-new',
    'job-add-step',
    'job-run',
    'job-run-form',
    'job-run-open',
    'job-run-name',
    'job-run-state',
    'job-runs-filter',
    'job-run-detail',
    'job-detail-state',
    'job-failure',
    'job-parameters',
    'job-parameter-example',
    'job-steps',
]

REMOVED_FUNCTIONS = [
    'applyRole',
    'catalogIdentity',
    'catalogKey',
    'cachedCatalogDetail',
    'invalidateCatalogDetails',
    'resetCatalogIdentity',
    'validateCatalogSession',
    'catalogMutation',
    'observeCatalogRuns',
    'fetchCatalogDetail',
    'describeTable',
    'loadCatalog',
    'resetCatalogSelection',
    'renderCatalogTrees',
    'selectInspector',
    'cloneTable',
    'downloadFile',
    'renderHistory',
    'rememberQuery',
    'actionButton',
    'renderData',
    'renderLineage',
    'jobTrigger',
    'jobSummary',
    'jobEmpty',
    'jobAlertCanAcknowledge',
    'jobAlertResolution',
    'jobAlertTimestamp',
    'renderJobAlerts',
    'jobsVisible',
    'scheduleJobsPolling',
    'stopJobsPolling',
    'jobDuration',
    'jobField',
    'syncJobSteps',
    'renderJobSteps',
    'editJob',
    'markJobDirty',
    'updateJobRunControls',
    'confirmDiscardJob',
    'clearManualRun',
    'openJob',
    'clearSelectedJob',
    'resetJobsUI',
    'updateRunFilter',
    'renderJobList',
    'selectJob',
    'loadJobs',
    'refreshJobRuns',
    'renderJobRuns',
    'showJobRun',
    'readJobArguments',
    'jobSchedulePayload',
    'jobScheduleEditable',
    'updateJobScheduleKind',
    'renderJobSchedule',
    'saveJobSchedule',
    'stopJobSchedule',
    'loadJobSchedules',
    'openWorksheet',
    'invalidateStatus',
    'statusTone',
    'readScheduleFields',
    'fillScheduleFields',
    'updateScheduleKind',
    'scheduleConfiguration',
    'schedulePayload',
    'scheduleSummary',
    'scheduleNextTime',
]

REMOVED_STATE = [
    'let columnRequest=',
    'let catalogSession=',
    'let catalogMetadata=',
    'const catalogCache=',
    'const catalogPending=',
    'const catalogRunStates=',
    'let catalogSessionValidation=',
    'let selectedTable=',
    'const liveJobStates=',
    'const jobActions=',
    'const scheduleFields=',
    'let jobEntries=',
    'let jobSchedules=',
    'let jobSteps=',
    'let selectedJob=',
    'let selectedRunId=',
    'jobsPoll=',
    'const worksheetKey=',
    'const historyKey=',
    'let saved=readStorage(worksheetKey',
    'function saveWorksheet(',
    'function addWorksheet(',
    'function download(',
    'function activate(',
    'function refreshStatus(',
]

REMOVED_I18N = [
    'Ingestion & Stages is available in Database mode.',
    'Transformation is available in Database mode.',
    'AI Bears is available in Database mode.',
    'Compute & Governance is available in Database mode.',
    'Audit log is available in Database mode.',
    'Select a job to edit, run or schedule it.',
    'All jobs',
    'Refresh runs',
    'Step history',
    'Scheduled for (JST)',
]


class LegacyRemovalUI(unittest.TestCase):
    javascript = harness.DatabaseUI.javascript

    def test_removed_ids_and_handlers_are_absent(self):
        for identifier in REMOVED_IDS:
            with self.subTest(identifier=identifier):
                self.assertNotIn('id="' + identifier + '"', HTML)
                self.assertNotIn('$(' + repr(identifier) + ')', SOURCE)
        for handler in ('loadPawprints', 'loadRuntimeHistory', 'renderPawprints',
                        'renderLoadHistory', 'resetAuditIdentity', 'dbAuditTelemetry',
                        'dbAuditRenderTelemetry'):
            with self.subTest(handler=handler):
                self.assertNotIn('function ' + handler + '(', SOURCE)
        for name in REMOVED_FUNCTIONS:
            with self.subTest(function=name):
                self.assertNotIn('function ' + name + '(', SOURCE)
                self.assertNotIn('function ' + name + '(', BASE_I18N)
        for state in REMOVED_STATE:
            with self.subTest(state=state):
                self.assertNotIn(state, SOURCE)
        for key in REMOVED_I18N:
            with self.subTest(key=key):
                self.assertNotIn("'" + key + "'", BASE_I18N)
        self.assertNotIn("api('/api/pawprints')", SOURCE)
        self.assertNotIn("api('/api/history", SOURCE)
        self.assertNotIn('/api/v1/audit/telemetry', SOURCE)
        for legacy in ("api('/api/catalog')", "api('/api/catalog?", "api('/api/jobs')",
                       "api('/api/jobs/runs')", "api('/api/jobs/detail')",
                       "api('/api/jobs/save')", "api('/api/jobs/schedule')",
                       "api('/api/jobs/schedules')", "api('/api/alerts')",
                       "/api/warehouse/query", "'/api/warehouse/'+action",
                       "api('/api/catalog/clone'", "api('/api/catalog/drop'",
                       "api('/api/lake/export'", "api('/api/bears/run'"):
            with self.subTest(legacy_api=legacy):
                self.assertNotIn(legacy, SOURCE)

    def test_database_pages_do_not_request_legacy_apis(self):
        self.javascript(r'''
await boot();
session.platform_admin=true;dbAdminSession(session);
assert.equal(currentRole,'admin');
$('tab-jobs').hidden=false;$('tab-jobs').disabled=false;
workbenchShellReady=true;
await dbRefreshContext();
for(const schema of dbSchemasFor(dbCurrent()))schema.capabilities.job_sql=true;
await activate($('tab-home'));await tick();await tick();
assert.ok(calls.some(call=>call.path.startsWith('/api/v1/jobs/runs')),'home reads Database job runs');
assert.ok(calls.some(call=>call.path.startsWith('/api/v1/alerts')),'home reads Database alerts');
const pages=['tab-home','tab-catalog','tab-warehouse','tab-ingestion','tab-transformation','tab-jobs','tab-bears','tab-db-admin','tab-pawprints','tab-compute'];
for(const id of pages){
 const tab=$(id);tab.hidden=false;tab.disabled=false;
 await activate(tab);await tick();
}
const legacy=calls.filter(call=>call.path!=='/api/session'&&call.path!=='/api/status'&&!call.path.startsWith('/api/v1/'));
assert.deepEqual(legacy.map(call=>call.path),[]);
assert.equal(calls.some(call=>call.path==='/api/pawprints'||call.path.startsWith('/api/history')||call.path.startsWith('/api/jobs')||call.path.startsWith('/api/catalog')),false);
''')

    def test_initialize_session_rejects_non_database_profiles(self):
        bootstrap = 'async function initializeSession() {' + SOURCE.split(
            'async function initializeSession() {', 1)[1].split('\nstartWorkbench();', 1)[0]
        self.assertIn("if(session.data_mode!=='database')throw new Error('Database runtime profile required');", SOURCE)
        self.assertNotIn("for(const id of ['ingestion-atlas'", SOURCE)
        self.assertNotIn("for(const id of ['ingestion-mode-guidance'", SOURCE)
        harness_source = """
const assert=require('node:assert/strict');
let databaseMode=false,currentRole='',workbenchSession=null,initialized=null;
const $=()=>({options:[],value:''});
async function initializeDatabaseUI(session){initialized=session;await Promise.resolve();}
async function fetch(){return {ok:true,status:200,json:async()=>({data_mode:'legacy',authenticated:true,maximum_role:'bi_viewer'})};}
""" + bootstrap + """
(async()=>{
 await assert.rejects(initializeSession(),/Database runtime profile required/);
 assert.equal(databaseMode,false);assert.equal(initialized,null);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        result = subprocess.run(['node', '-'], input=harness_source, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

        harness_source = """
const assert=require('node:assert/strict');
let databaseMode=false,currentRole='',workbenchSession=null,initialized=null;
const $=()=>({options:[],value:''});
async function initializeDatabaseUI(session){initialized=session;await Promise.resolve();}
const session={data_mode:'database',username:'owner',authenticated:true,maximum_role:'admin'};
async function fetch(){return {ok:true,status:200,json:async()=>session};}
""" + bootstrap + """
(async()=>{
 await initializeSession();
 assert.equal(databaseMode,true);assert.equal(initialized,session);
 assert.equal(currentRole,'admin');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        result = subprocess.run(['node', '-'], input=harness_source, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
