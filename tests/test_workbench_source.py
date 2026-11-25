"""Source loading follows the production document's classic script order."""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest

from workbench_source import INDEX, WORKBENCH, SCRIPT_BOUNDARY, WorkbenchSource, function


class WorkbenchSourceTests(unittest.TestCase):
    def test_production_order_excludes_head_bootstrap(self):
        parsed = WorkbenchSource(INDEX.read_text())
        self.assertEqual(len(parsed.sources), 6 + len(parsed.asset_sources))
        self.assertEqual(parsed.sources[0], (WORKBENCH / 'result_chart.js').read_text())
        self.assertIn('function populateChartColumns()', parsed.sources[0])
        self.assertIn('function renderChart()', parsed.sources[0])
        self.assertIn('const $ =', parsed.sources[1])
        self.assertEqual(parsed.sources[2], (WORKBENCH / 'base_i18n.js').read_text())
        self.assertIn('startWorkbench();', parsed.sources[-1])
        self.assertEqual(parsed.asset_sources['/assets/saved_lineage.js'], (WORKBENCH / 'saved_lineage.js').read_text())
        self.assertEqual(parsed.sources[3:5], [parsed.asset_sources['/assets/saved_lineage.js'], parsed.asset_sources['/assets/result_export.js']])
        self.assertEqual(parsed.asset_sources['/assets/result_export.js'], (WORKBENCH / 'result_export.js').read_text())
        self.assertEqual(parsed.asset_sources['/assets/db_admin.js'], (WORKBENCH / 'db_admin.js').read_text())
        self.assertEqual(parsed.asset_sources['/assets/db_jobs.js'], (WORKBENCH / 'db_jobs.js').read_text())
        self.assertEqual(parsed.asset_sources['/assets/db_stages.js'], (WORKBENCH / 'db_stages.js').read_text())
        self.assertEqual(parsed.asset_sources['/assets/db_marts.js'], (WORKBENCH / 'db_marts.js').read_text())
        self.assertEqual(parsed.asset_sources['/assets/db_dbt.js'], (WORKBENCH / 'db_dbt.js').read_text())
        self.assertEqual(parsed.asset_sources['/assets/db_creation.js'], (WORKBENCH / 'db_creation.js').read_text())
        self.assertEqual(parsed.asset_sources['/assets/db_audit.js'], (WORKBENCH / 'db_audit.js').read_text())
        self.assertEqual(parsed.asset_sources['/assets/db_runtime.js'], (WORKBENCH / 'db_runtime.js').read_text())
        self.assertLess(parsed.sources.index(parsed.asset_sources['/assets/db_admin.js']), parsed.sources.index(parsed.asset_sources['/assets/db_jobs.js']))
        self.assertLess(parsed.sources.index(parsed.asset_sources['/assets/db_jobs.js']), parsed.sources.index(parsed.asset_sources['/assets/db_stages.js']))
        self.assertLess(parsed.sources.index(parsed.asset_sources['/assets/db_stages.js']), parsed.sources.index(parsed.asset_sources['/assets/db_marts.js']))
        self.assertLess(parsed.sources.index(parsed.asset_sources['/assets/db_marts.js']), parsed.sources.index(parsed.asset_sources['/assets/db_dbt.js']))
        self.assertEqual(parsed.sources.index(parsed.asset_sources['/assets/db_stages.js']), parsed.sources.index(parsed.asset_sources['/assets/db_jobs.js']) + 1)
        self.assertEqual(parsed.sources.index(parsed.asset_sources['/assets/db_marts.js']), parsed.sources.index(parsed.asset_sources['/assets/db_stages.js']) + 1)
        self.assertLess(parsed.sources.index(parsed.asset_sources['/assets/db_dbt.js']), parsed.sources.index(parsed.asset_sources['/assets/db_creation.js']))
        self.assertLess(parsed.sources.index(parsed.asset_sources['/assets/db_creation.js']), parsed.sources.index(parsed.asset_sources['/assets/db_audit.js']))
        self.assertLess(parsed.sources.index(parsed.asset_sources['/assets/db_audit.js']), parsed.sources.index(parsed.asset_sources['/assets/db_runtime.js']))
        # The runtime block follows the audit block in index.html, so the tail is runtime, audit, creation.
        self.assertEqual(parsed.sources[-2], parsed.asset_sources['/assets/db_runtime.js'])
        self.assertEqual(parsed.sources[-3], parsed.asset_sources['/assets/db_audit.js'])
        self.assertEqual(parsed.sources[-4], parsed.asset_sources['/assets/db_creation.js'])
        lineage_source = next(source for source in parsed.sources if 'const dbLineage=createSavedLineagePanel(' in source)
        self.assertNotIn('function dbLineageRefresh(){', lineage_source.split('const dbProfileKey=')[0].split('const dbLineage=')[0])
        self.assertLess(parsed.source.index('const $ ='), parsed.source.index('const i18n ='))
        self.assertLess(parsed.source.index('const i18n ='), parsed.source.index("let language = 'en';"))
        self.assertEqual(parsed.style_sources, [(WORKBENCH / 'workbench.css').read_text()])
        self.assertNotIn('<style>', INDEX.read_text())
        self.assertNotIn('function renderChart()', parsed.sources[1])
        self.assertNotIn('data-workbench-language', parsed.source)
        self.assertNotIn('const lang=localStorage.getItem', parsed.source)
        self.assertIn('function renderChart()', function('renderChart', parsed.source))

    def test_inline_external_order_and_script_types(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'result_chart.js').write_text('external();')
            parsed = WorkbenchSource('''<head><script>early();</script></head><body>
                <script>first();</script>
                <script src="/assets/result_chart.js"></script>
                <script type="application/json">{"data":true}</script>
                <script type="module">moduleOnly();</script>
                <script type="text/javascript">last();</script></body>''', directory)
            self.assertEqual(parsed.sources, ['first();', 'external();', 'last();'])
            self.assertEqual(parsed.asset_sources, {'/assets/result_chart.js': 'external();'})
            self.assertEqual(parsed.source.split(SCRIPT_BOUNDARY), parsed.sources)

    def test_chart_extraction_compiles_and_runs_with_caller_globals(self):
        from test_catalog_ui import function as catalog_function
        from test_jobs_ui import function as jobs_function

        parsed = WorkbenchSource()
        declarations = [function('renderChart'), function('renderChart', parsed.sources),
                        function('renderChart', parsed.source),
                        catalog_function('renderChart'), jobs_function('renderChart')]
        for index, declaration in enumerate(declarations):
            with self.subTest(extractor=index):
                harness = '''
const assert=require('node:assert/strict');
let cleared=0;
const $=id=>{assert.equal(id,'query-chart');return {replaceChildren(){cleared++;}};};
const i18n={},language='en',queryResult=null;
''' + declaration + '''
renderChart();assert.equal(cleared,1);
'''
                result = subprocess.run(['node'], input=harness, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_chart_and_compatibility_source_remain_strict(self):
        parsed = WorkbenchSource()
        harness = '''const assert=require('node:assert/strict');
const sources=''' + json.dumps([*parsed.sources, parsed.source]) + ''';
for(const source of sources){
 new Function(source);
 assert.throws(()=>new Function(source+'\\nwith ({}) {}'),SyntaxError);
}
assert.equal(new Function(sources[0]+'\\nreturn this;')(),undefined);
'''
        result = subprocess.run(['node'], input=harness, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unsupported_references_are_not_fetched(self):
        for reference in ('https://example.com/chart.js', '/assets/other.js',
                          '/assets/../result_chart.js', '/assets/result_chart.js?v=1',
                          '/assets/base_i18n.js?v=1', '/assets/workbench.css', ''):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                WorkbenchSource('<body><script src="' + reference + '"></script></body>')

    def test_missing_fixed_asset_reports_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ('result_chart.js', 'base_i18n.js', 'saved_lineage.js', 'result_export.js', 'db_admin.js', 'db_jobs.js', 'db_stages.js', 'db_marts.js', 'db_dbt.js', 'db_creation.js', 'db_audit.js', 'db_runtime.js'):
                with self.subTest(name=name), self.assertRaises(FileNotFoundError):
                    WorkbenchSource('<body><script src="/assets/' + name + '"></script></body>', directory)
            with self.assertRaises(FileNotFoundError):
                WorkbenchSource('<head><link rel="stylesheet" href="/assets/workbench.css"></head>', directory)

    def test_styles_follow_head_and_body_document_order(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'workbench.css').write_text('external {}')
            parsed = WorkbenchSource('''<head><style>head {}</style>
                <link rel="alternate stylesheet" href="/assets/workbench.css"></head>
                <body><style>body {}</style></body>''', directory)
            self.assertEqual(parsed.style_sources, ['head {}', 'external {}', 'body {}'])
            self.assertEqual(parsed.css, 'head {}\nexternal {}\nbody {}')
            self.assertEqual(parsed.sources, [])

    def test_unknown_stylesheets_are_not_resolved(self):
        for reference in ('https://example.com/workbench.css', '/assets/other.css',
                          '/assets/../workbench.css', '/assets/workbench.css?v=1', ''):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                WorkbenchSource('<head><link rel="stylesheet" href="' + reference + '"></head>')

    def test_base_dictionary_executes_between_prelude_and_main(self):
        parsed = WorkbenchSource()
        # Execute separate classic scripts in one browser-like global environment.
        harness = '''const vm=require('node:vm'),assert=require('node:assert/strict');
const context=vm.createContext({document:{getElementById:id=>id}});
''' + 'const sources=' + json.dumps(parsed.sources[1:3]) + ''';
for(const source of sources)vm.runInContext(source,context);
vm.runInContext("assertion = $('element') === 'element' && i18n.en['SQL Worksheets'] === 'SQL Worksheets' && !!i18n.ja['SQL Worksheets']",context);
assert.equal(context.assertion,true);
assert.throws(()=>vm.runInContext('language',context),{name:'ReferenceError'});
'''
        result = subprocess.run(['node'], input=harness, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
