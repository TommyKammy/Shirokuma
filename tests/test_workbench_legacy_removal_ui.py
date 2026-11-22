"""UX-A7: removed legacy controls stay out of the Atlas shell."""
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
        self.assertNotIn("api('/api/pawprints')", SOURCE)
        self.assertNotIn("api('/api/history", SOURCE)
        self.assertNotIn('/api/v1/audit/telemetry', SOURCE)

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

    def test_non_database_mode_keeps_the_audit_guidance(self):
        self.assertIn('id="audit-mode-guidance"', HTML)
        self.assertNotIn('id="audit-mode-guidance" hidden', HTML)
        self.assertIn('Audit log is available in Database mode.', HTML)
        self.assertIn("'Audit log is available in Database mode.':'監査ログは Database モードで利用できます。'", BASE_I18N)
        # Database mode replaces the static guidance with the audited panel; the
        # non-Database branch never installs that panel and leaves the guidance visible.
        self.assertIn("if(session.data_mode==='database'){databaseMode=true;await initializeDatabaseUI(session);return;}", SOURCE)
        self.assertIn("for(const child of host.children)child.hidden=true", SOURCE)


if __name__ == '__main__':
    unittest.main()
