import importlib.util
from pathlib import Path
import subprocess
import unittest

from tests.gitops_render import load_yaml_text

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('local_experiment', ROOT / 'tools/local_experiment/local_experiment.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class LocalExperimentCatalogTests(unittest.TestCase):
    def test_catalog_allowlist_matches_local_render_exactly(self):
        rendered = subprocess.run(['rtk', 'proxy', 'kubectl', 'kustomize', str(ROOT / M.COMPONENTS['catalog'][0])],
                                  text=True, capture_output=True, check=True)
        objects = load_yaml_text(rendered.stdout)
        M.validate_objects('catalog', objects)
        with self.assertRaises(M.ExperimentError):
            M.validate_objects('catalog', objects + [{'apiVersion': 'v1', 'kind': 'ConfigMap',
                'metadata': {'namespace': 'shirokuma-dev', 'name': 'unreviewed'}}])
        with self.assertRaises(M.ExperimentError):
            M.validate_objects('catalog', objects[:-1])


if __name__ == '__main__':
    unittest.main()
