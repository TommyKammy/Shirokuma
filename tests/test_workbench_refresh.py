"""Focused refresh contracts, executed in Node without browser/services."""
import json
from pathlib import Path
import subprocess
import unittest

from workbench_source import WorkbenchSource



HTML = Path(__file__).resolve().parents[1] / 'tools/local_experiment/workbench/index.html'


class AuditRefreshTests(unittest.TestCase):
    def test_no_browser_auto_chill_checks_and_full_script_syntax(self):
        script = WorkbenchSource(HTML.read_text()).source
        self.assertNotIn("api('/api/warehouse/autochill',{check:true})", script)
        self.assertNotIn("$('save-auto-chill').onclick", script)
        self.assertNotIn('if(data.auto_chill)updateAutoChill(data.auto_chill)', script)
        result = subprocess.run(['node'], input='new Function(' + json.dumps(script) + ')', capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
