"""UX-R8: retired Atlas rebuild scaffolding is gone and every screen still boots."""
import re
import unittest

import test_workbench_database_ui as database_ui
from workbench_source import WorkbenchSource

HTML = database_ui.HTML
SOURCE = WorkbenchSource(HTML).source
CSS = WorkbenchSource(HTML).css

RETIRED_IDS = '''
ingestion-legacy ingestion-legacy-file ingestion-legacy-stages
transformation-legacy transformation-models transformation-wap transformation-marts
transformation-legacy-models transformation-view-models transformation-view-wap
transformation-view-marts bears-legacy bears-legacy-compute-link bear-cards
stage-legacy-notice compute-legacy compute-legacy-overview compute-legacy-status
compute-legacy-auto-chill compute-refresh compute-sre-check compute-policy-check
compute-check-result compute-summary compute-warehouse compute-components
auto-chill-minutes save-auto-chill auto-chill-state stages-area stage-form
stage-name stage-directory stage-table stage-mode stage-format stage-key stage-save
stage-error stage-detail-hint stage-schedule-area stage-schedule-state
stage-schedule-stop stage-schedule-form stage-schedule-kind stage-schedule-minutes
stage-schedule-once stage-schedule-date stage-schedule-monthly stage-schedule-day
stage-schedule-daily stage-schedule-time stage-schedule-timezone
stage-schedule-weekly stage-schedule-enabled stage-schedule-save stage-scan
stage-files-area stage-files-title stage-files-refresh stage-files stage-file
stage-preview-button stage-import stage-file-status stage-confirmation
stage-append-notice stage-retry-area stage-retry stage-preview stage-view-run
stage-export-area stage-exports-refresh stage-export-directory-label
stage-export-directory stage-export-form stage-export-table stage-export-format
stage-export-file stage-export-submit stage-exports ingest-form ingest-dropzone
ingest-upload-input upload-state clear-upload ingest-file ingest-table ingest-mode
ingest-key ingest-key-help preview-ingest run-ingest ingest-state ingest-preview
ingest-error ingest-result refresh-models run-dbt-build run-dbt-test models-state
dbt-models wap-models model-title open-model-sql refresh-wap-model model-lineage
model-sql-preview transformation-copilot-actions copilot-stage-table marts-admin
marts-title marts-reload mart-new mart-error marts-update-review marts-list
mart-form mart-definition-state mart-name mart-sql mart-min-rows mart-not-null
mart-watch-tables mart-watch-help mart-save mart-state mart-run mart-view-run
mart-schedule-state mart-schedule-next mart-schedule-last mart-schedule-stop
mart-schedule-help mart-schedule-form mart-schedule-kind mart-schedule-minutes
mart-schedule-once mart-schedule-date mart-schedule-monthly mart-schedule-day
mart-schedule-daily mart-schedule-time mart-schedule-timezone
mart-schedule-weekly mart-schedule-enabled mart-schedule-save
'''.split()


class RebuildCleanupStatic(unittest.TestCase):
    def test_index_has_no_legacy_ids(self):
        self.assertEqual(re.findall(r'id="[^"]*legacy[^"]*"', HTML), [])

    def test_removed_ids_are_not_read_from_javascript(self):
        for identifier in RETIRED_IDS:
            with self.subTest(identifier=identifier):
                self.assertNotIn("$('" + identifier + "')", SOURCE)
                self.assertNotIn('getElementById("' + identifier + '")', SOURCE)
                self.assertNotIn("getElementById('" + identifier + "')", SOURCE)

    def test_removed_css_selectors_are_gone(self):
        for selector in ('legacy-tabs', '#ingestion-legacy', '#transformation-legacy',
                         '#bears-legacy', '#compute-legacy', '#stages-area',
                         '#marts-admin', '.model-layout', '.bear-layout',
                         '#auto-chill-minutes', '#stage-preview', '#ingest-dropzone'):
            with self.subTest(selector=selector):
                self.assertNotIn(selector, CSS)

    def test_compute_resource_grid_and_summary_rules_are_intact(self):
        self.assertIn(
            '#compute-usage-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(320px,100%),1fr));gap:12px;align-items:start}',
            CSS,
        )
        self.assertIn('.panel-head>.compute-kind{flex-basis:100%', CSS)
        self.assertRegex(
            CSS,
            r'#compute-atlas #compute-usage > summary,\s*#compute-atlas #compute-auto-chill > summary,\s*'
            r'#compute-atlas #compute-diagnostics > summary\s*\{\s*font-size:15px;\s*font-weight:600;?\s*\}',
        )

    def test_atlas_structure_and_shared_context_remain(self):
        for identifier in ('ingestion-atlas', 'ingestion-file', 'ingestion-stages',
                           'transformation-atlas', 'transformation-atlas-models',
                           'transformation-publications', 'transformation-atlas-marts',
                           'transformation-atlas-dbt', 'bears-atlas',
                           'compute-atlas', 'compute-diagnostics', 'ai-settings',
                           'audit-mode-guidance'):
            with self.subTest(identifier=identifier):
                self.assertIn('id="' + identifier + '"', HTML)
        for selector in ('.atlas-flow', '.atlas-fields', '.kv', '.section', '.panel',
                         '.impact', '.status-line'):
            with self.subTest(selector=selector):
                self.assertIn(selector, CSS)
        for identifier in ('ingestion-mode-guidance', 'transformation-mode-guidance',
                           'bears-mode-guidance', 'compute-mode-guidance',
                           'audit-mode-guidance'):
            with self.subTest(guidance=identifier):
                self.assertIn('id="' + identifier + '"', HTML)
        base = WorkbenchSource(HTML).asset_sources['/assets/base_i18n.js']
        for key in ('Ingestion & Stages is available in Database mode.',
                    'Transformation is available in Database mode.',
                    'AI Bears is available in Database mode.',
                    'Compute & Governance is available in Database mode.',
                    'Audit log is available in Database mode.'):
            with self.subTest(key=key):
                self.assertIn(key, base)
                self.assertRegex(base, re.escape(key) + r"\':\'[^\']*Database モード")


class RebuildCleanupBoot(unittest.TestCase):
    def run_js(self, body):
        database_ui.DatabaseUI.javascript(self, body)

    def test_every_screen_opens_without_an_exception(self):
        self.run_js(r'''
await boot();
for(const id of ['home','ingestion','transformation','jobs','bears','pawprints','compute']){
 const tab=$('tab-'+id);if(tab.hidden||tab.disabled)continue;
 await activate(tab);
 assert.equal($(id).hidden,false,id);
}
await activate($('tab-warehouse'));assert.equal($('warehouse').hidden,false);
await activate($('tab-catalog'));assert.equal($('warehouse').hidden,false);assert.equal(dbWorkspacePane,'details');
assert.equal(activeTab,'warehouse');
''')

    def test_bears_does_not_revive_old_bear_cards(self):
        self.run_js(r'''
await boot();await activate($('tab-bears'));
const visibleText=node=>{
 if(node.hidden)return '';
 if(node.tagName==='DETAILS'&&!node.open)return visibleText(node.children[0]||new Node());
 return [node.textContent,...node.children.map(visibleText)].join(' ');
};
const text=visibleText($('bears-atlas'));
for(const name of ['SRE Bear','Query Doctor Bear','Data Engineer Bear','Catalog Curator Bear','Policy Gatekeeper Bear'])assert.equal(text.includes(name),false,name);
assert.equal($('bear-panel-catalog').hidden,true);
const covered=id=>{for(let node=$(id);node;node=node.parentElement){if(node.hidden)return true;if(node.tagName==='DETAILS'&&!node.open)return true;}return false;};
for(const id of ['bear-panel-sre','bear-panel-query','bear-panel-data','bear-panel-policy'])assert.equal(covered(id),true,id);
for(const id of ['bears-explanation-title','bears-proposal-title','bears-curator-title','bears-policy-title'])assert.ok($(id)&&$(id).textContent,id);
''')

    def test_compute_diagnostics_and_resource_panels_are_in_the_atlas_grid(self):
        self.run_js(r'''
await boot();await activate($('tab-compute'));
for(const id of ['compute-usage','compute-auto-chill','compute-diagnostics'])assert.equal($(id).parentElement,$('compute-atlas'),id);
for(const id of ['observation-resources-panel','observation-usage-panel','observation-capacity-panel'])assert.equal($(id).parentElement,$('compute-usage-list'),id);
for(const id of ['observation-resources-panel','observation-usage-panel','observation-capacity-panel'])assert.equal($(id).children[0].tagName,'DIV',id);
for(const id of ['compute-usage','compute-auto-chill','compute-diagnostics'])assert.equal($(id).children[0].tagName,'SUMMARY',id);
''')

    def test_top_context_is_the_only_curator_and_observation_context(self):
        self.run_js(r'''
await boot();await activate($('tab-bears'));
for(const id of ['curator-role','curator-database','curator-schema','curator-object','curator-refresh','curator-load','curator-more','curator-create'])assert.equal(documentRoot.descendants().some(n=>n.id===id),false,id);
for(const kind of ['sre','policy'])for(const name of ['role','database','schema','refresh'])assert.equal(documentRoot.descendants().some(n=>n.id==='observation-'+kind+'-'+name),false,kind+'-'+name);
assert.equal($('bears-context-fields').descendants().includes($('bears-database')),true);
assert.equal($('bears-context-fields').descendants().includes($('bears-schema')),true);
assert.equal($('bears-context-fields').descendants().includes($('bears-role')),true);
assert.equal($('bears-target-fields').descendants().includes($('bears-table')),true);
''')

    def test_retired_stage_and_compute_controls_are_not_recreated(self):
        self.run_js(r'''
await boot();await activate($('tab-ingestion'));await activate($('tab-compute'));
for(const id of ['stages-area','stage-form','stage-name','stage-preview','stage-files','stage-exports','ingest-form','ingest-preview','ingest-result'])assert.equal(documentRoot.descendants().some(n=>n.id===id),false,id);
for(const id of ['compute-legacy','compute-refresh','compute-sre-check','compute-policy-check','compute-summary','compute-warehouse','compute-components','auto-chill-minutes','save-auto-chill','auto-chill-state'])assert.equal(documentRoot.descendants().some(n=>n.id===id),false,id);
assert.ok($('runtime-panel'));assert.ok($('runtime-minutes'));assert.ok($('runtime-save'));
''')
