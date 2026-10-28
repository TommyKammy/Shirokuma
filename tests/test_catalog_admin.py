import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/local_experiment'))
sys.path.insert(0, str(ROOT / 'tests'))
import catalog_admin
from test_database_generation import spec


class CatalogAdminTests(unittest.TestCase):
    def test_render_check_only_and_safe_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, output = Path(temporary)/'spec.json', Path(temporary)/'bundle'
            source.write_text(json.dumps(spec()))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(catalog_admin.main(['render', '--spec', str(source), '--output', str(output), '--root', str(ROOT)]), 0)
                self.assertEqual(catalog_admin.main(['check', '--bundle', str(output), '--root', str(ROOT)]), 0)
            source.write_text('{"password":"private-value"}')
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                self.assertEqual(catalog_admin.main(['render', '--spec', str(source), '--output', str(output)+'2', '--root', str(ROOT)]), 1)
            self.assertNotIn('private-value', error.getvalue())
            with self.assertRaises(SystemExit):
                with contextlib.redirect_stderr(io.StringIO()):
                    catalog_admin.main(['apply'])

    def test_missing_optional_dependency_has_actionable_safe_error(self):
        error = ModuleNotFoundError("No module named 'yaml'", name='yaml')
        output = io.StringIO()
        original = __import__
        def missing_generation(name, *args, **kwargs):
            if name == 'database_generation':
                raise error
            return original(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=missing_generation), contextlib.redirect_stderr(output):
            self.assertEqual(catalog_admin.main(['check', '--bundle', '/public/fixture']), 1)
        self.assertIn('requirements-generation.txt', output.getvalue())
