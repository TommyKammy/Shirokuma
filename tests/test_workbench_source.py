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
        self.assertEqual(len(parsed.sources), 2)
        self.assertEqual(parsed.sources[0], (WORKBENCH / 'result_chart.js').read_text())
        self.assertIn('function populateChartColumns()', parsed.sources[0])
        self.assertIn('function renderChart()', parsed.sources[0])
        self.assertIn('startWorkbench();', parsed.sources[1])
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
const sources=''' + json.dumps([parsed.sources[0], parsed.source]) + ''';
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
                          '/assets/../result_chart.js', '/assets/result_chart.js?v=1', ''):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                WorkbenchSource('<body><script src="' + reference + '"></script></body>')

    def test_missing_fixed_asset_reports_missing_file(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(FileNotFoundError):
            WorkbenchSource('<body><script src="/assets/result_chart.js"></script></body>', directory)
