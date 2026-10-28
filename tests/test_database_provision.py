import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools/local_experiment'))
import database_provision as provision
from tests.test_database_generation import spec
from tests.test_polaris_permissions import Polaris, M
import database_generation as generation


class API(Polaris):
    def __call__(self, method, path, body=None):
        if method == 'GET' and path == M.MANAGEMENT+'/principals':
            return {'principals': [copy.deepcopy(v) for k, v in self.entities.items()
                                   if k.startswith(M.MANAGEMENT+'/principals/')]}
        if method == 'GET' and '/namespaces/' in path:
            parent, name = path.rsplit('/', 1)
            if [name] not in self.namespaces.get(parent, []):
                raise provision.permissions.APIError(404)
            return {'namespace': [name], 'properties': {}}
        try:
            return super().__call__(method, path, body)
        except M.APIError as error:
            raise provision.permissions.APIError(error.status) from None


class ProvisionTests(unittest.TestCase):
    def setUp(self):
        self.entry = generation.render(spec(3))['tofu']['databases']['opaque_2']
        self.api, self.state, self.persisted = API(), {}, []

    def run_provision(self, request=None, **kwargs):
        return provision.provision(request or self.api, 'arbitrary_third', self.entry,
            generation.SCHEMAS, self.state, lambda value: self.persisted.append(copy.deepcopy(value)), **kwargs)

    def test_four_schemas_both_owned_principals_and_reuse(self):
        result = self.run_provision()
        self.assertEqual(set(result), {'trino', 'iceberg-bootstrap'})
        self.assertEqual(self.api.namespaces['/api/catalog/v1/'+self.entry['catalog']+'/namespaces'],
                         [[s] for s in sorted(generation.SCHEMAS)])
        writes = copy.deepcopy(self.api.writes)
        self.run_provision(verify_only=True)
        self.assertEqual(writes, self.api.writes)
        saved = copy.deepcopy(result)
        self.state = {}
        self.assertEqual(self.run_provision(supplied=saved, verify_only=True), saved)
        self.assertEqual(writes, self.api.writes)

    def test_issued_secret_is_persisted_before_next_api(self):
        issued = []
        def request(method, path, body=None):
            if issued:
                self.assertTrue(any('trino' in row['arbitrary_third']['consumers'] for row in self.persisted))
            result = self.api(method, path, body)
            if method == 'POST' and path == M.MANAGEMENT+'/principals':
                issued.append(True)
            return result
        self.run_provision(request)

    def test_lost_principal_post_and_missing_resource_never_reissues(self):
        def request(method, path, body=None):
            if method == 'POST' and path == M.MANAGEMENT+'/principals':
                # Dispatch may have reached Polaris; no ack or credentials survive.
                raise RuntimeError('lost acknowledgement')
            return self.api(method, path, body)
        with self.assertRaises(RuntimeError):
            self.run_provision(request)
        writes = copy.deepcopy(self.api.writes)
        with self.assertRaisesRegex(RuntimeError, 'UNKNOWN'):
            self.run_provision()
        self.assertEqual(writes, self.api.writes)
        self.assertEqual(self.state['arbitrary_third']['leaves']['trino']['state'], 'DISPATCHED')

    def test_full_verifier_rejects_grant_and_membership_drift_without_writes(self):
        self.run_provision()
        writes = copy.deepcopy(self.api.writes)
        path = next(k for k in self.api.grants if 'iceberg_bootstrap' in k)
        self.api.grants[path].pop()
        with self.assertRaises(RuntimeError):
            self.run_provision(verify_only=True)
        self.assertEqual(writes, self.api.writes)
        self.api.grants[path] = provision.grants('iceberg-bootstrap', generation.SCHEMAS)
        path = next(k for k in self.api.roles if '/principals/' in k)
        self.api.roles[path].append({'name': 'foreign'})
        with self.assertRaises(RuntimeError):
            self.run_provision(verify_only=True)
        self.assertEqual(writes, self.api.writes)


"""Credential issuance ambiguity must survive both same-process and restart retry."""
import copy
import unittest
from unittest.mock import patch
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools/local_experiment'))
import database_provision as provision


class IssuanceTests(unittest.TestCase):
    def test_lost_malformed_or_unsaved_response_never_reissues_even_after_404(self):
        for failure in ('lost','malformed','save'):
            state, durable, posts = {}, {}, []
            entry = dict(catalog='target',realm='REALM')
            def persist(value):
                if failure=='save' and value['db']['consumers']:
                    raise OSError('credential persistence failed')
                durable.clear()
                durable.update(copy.deepcopy(value))
            def request(method,path,body=None):
                if method=='POST':
                    posts.append(path)
                    if failure=='lost': raise ConnectionError('lost')
                    if failure=='malformed': return {'credentials':{}}
                    return dict(credentials=dict(clientId='issued',clientSecret='issued-secret'),
                        principal=dict(name='issued-principal'))
                if path.endswith('/catalogs/target'):
                    return {'name':'target','type':'EXTERNAL','properties':{},'storageConfigInfo':{}}
                if path.endswith('/namespaces'): return {'namespaces':[]}
                raise provision.permissions.APIError(404)
            with patch.object(provision.database_generation,'_catalog',return_value={
                    'name':'target','type':'EXTERNAL','properties':{},'storageConfigInfo':{}}):
                with self.assertRaises(Exception):
                    provision.provision(request,'db',entry,[],state,persist)
                self.assertEqual(len(posts),1)
                self.assertEqual(durable['db']['leaves']['trino']['state'],'DISPATCHED')
                for retry in (state,copy.deepcopy(durable)):
                    with self.assertRaisesRegex(RuntimeError,'UNKNOWN'):
                        provision.provision(request,'db',entry,[],retry,persist)
                self.assertEqual(len(posts),1)
