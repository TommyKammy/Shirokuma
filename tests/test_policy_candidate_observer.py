"""Single-candidate admission boundaries using a fake Kubernetes API only."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools/local_experiment'))
from policy import candidate as P
from policy import candidate_observer as O

TOKEN = 'a'*32
PRIVATE = 'private.example/workload@sha256:'+'b'*64


def pod(*, privileged=False):
    return dict(apiVersion='v1',kind='Pod',metadata={'name':'private-pod'},spec={'containers':[
        dict(name='private-container',image=PRIVATE,securityContext={'privileged':privileged},
             resources={'requests':{'cpu':'10m','memory':'16Mi'},'limits':{'cpu':'100m','memory':'32Mi'}})]})


def prepared(value=None):
    return P.prepare(json.dumps(pod() if value is None else value).encode(),TOKEN)


class FakeKube:
    def __init__(self):
        self.items=O.M.load_manifest();self.resources={}
        for index,expected in enumerate(self.items,1):
            value=copy.deepcopy(expected);value['metadata'].update(uid=str(uuid.UUID(int=index)),generation=1)
            if value['kind']==O.M.POLICY:value['status']={'observedGeneration':1,'typeChecking':{'expressionWarnings':[]}}
            self.resources[(value['kind'],value['metadata']['name'])]=value
        self.namespace=dict(apiVersion='v1',kind='Namespace',metadata=dict(name=O.M.NAMESPACE,
            uid=str(uuid.UUID(int=99)),labels={'kubernetes.io/metadata.name':O.M.NAMESPACE}))
        self.limits=dict(apiVersion='v1',kind='LimitRangeList',items=[])
        self.present=False;self.requests=[];self.reads=[];self.respond=None

    def get(self,kind,name):
        self.reads.append((kind,name));return copy.deepcopy(self.resources[(kind,name)])

    def json_request(self,args):
        self.reads.append(args)
        if args==['get','namespace',O.M.NAMESPACE,'-o','json']:return copy.deepcopy(self.namespace)
        if args==['-n',O.M.NAMESPACE,'get','limitranges','-o','json']:return copy.deepcopy(self.limits)
        if args==['-n',O.M.NAMESPACE,'get','pod','shirokuma-policy-candidate-'+TOKEN,'--ignore-not-found','-o','json']:
            return {} if self.present else None
        raise AssertionError('Unexpected fake request')

    def request(self,args,body):
        assert args==['-n',O.M.NAMESPACE,'create','--dry-run=server','-f','-','-o','json']
        self.requests.append(copy.deepcopy(body))
        if self.respond:return self.respond(body)
        return SimpleNamespace(returncode=0,stdout=json.dumps(body),stderr='')

    def denial(self,body,ids):
        pieces=[]
        for rule in ids:
            item=next(v for v in self.items if v['kind']==O.M.POLICY and O.G.RULE_IDS[v['metadata']['name']]==rule)
            name=item['metadata']['name'];message=item['spec']['validations'][0]['message']
            pieces.append(f"ValidatingAdmissionPolicy '{name}' with binding '{name}' denied request: {message}")
        suffix=pieces[0] if len(pieces)==1 else '['+', '.join(pieces)+']'
        return SimpleNamespace(returncode=1,stdout='',stderr=f'Error from server (Forbidden): error when creating "STDIN": pods "{body["metadata"]["name"]}" is forbidden: '+suffix+'\n')

    def invalid_denial(self,body,rule):
        item=next(v for v in self.items if v['kind']==O.M.POLICY and O.G.RULE_IDS[v['metadata']['name']]==rule)
        policy=item['metadata']['name'];message=item['spec']['validations'][0]['message']
        # apiserver Invalid/422 has one StatusCause whose Field is empty.
        return SimpleNamespace(returncode=1,stdout='',stderr=(
            f'The pods "{body["metadata"]["name"]}" is invalid: : '
            f"ValidatingAdmissionPolicy '{policy}' with binding '{policy}' denied request: {message}\n"))


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.kube=FakeKube();self.input=prepared();self.observer=O.CandidateObserver(self.input,self.kube)

    def ready(self):
        return self.observer.observe()

    def test_allowed_exact_body_one_attempt_and_all_guards(self):
        observed=self.ready();receipts=[]
        def save(value):
            if value['pending']:self.assertEqual(self.kube.requests,[])
            receipts.append(value)
        actual=self.observer.verify(progress=save)
        self.assertEqual(self.kube.requests,[self.input.body()]);self.assertEqual(actual['result'],'passed')
        self.assertEqual(actual['schema_version'],2)
        self.assertEqual(actual['response_content_check'],'matched')
        self.assertTrue(actual['absence_before'] and actual['absence_after'] and actual['baseline_stable'])
        self.assertEqual(actual['baseline_before'],actual['baseline_after'])
        self.assertEqual(len(actual['baseline_before']['resources']),16)
        self.assertEqual(observed['body_sha256'],self.input.receipt()['body_sha256'])
        text=json.dumps([observed,actual]);self.assertNotIn(PRIVATE,text);self.assertNotIn('private-pod',text)
        with self.assertRaises(O.CandidateObservationError):self.observer.verify(progress=save)
        self.assertEqual(len(self.kube.requests),1)

    def test_allowed_response_must_represent_the_submitted_pod(self):
        changes = (
            lambda body: body['spec']['containers'][0].update(image='private.example/other@sha256:'+'c'*64),
            lambda body: body['spec']['containers'].append(copy.deepcopy(body['spec']['containers'][0])),
            lambda body: body['spec'].update(initContainers=[copy.deepcopy(body['spec']['containers'][0])]),
            lambda body: body['spec']['containers'][0]['resources']['requests'].pop('cpu'),
            lambda body: body['spec'].update(hostNetwork=True),
            lambda body: body['spec'].update(volumes=[{'name':'mount','hostPath':{'path':'/tmp'}}]),
            lambda body: body['spec'].update(securityContext={'runAsUser':0}),
        )
        for change in changes:
            with self.subTest(change=change):
                kube=FakeKube(); observer=O.CandidateObserver(self.input,kube); observer.observe()
                def respond(body):
                    altered=copy.deepcopy(body);change(altered)
                    return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
                kube.respond=respond
                with self.assertRaises(O.CandidateObservationError):observer.verify(progress=lambda v:None)
                receipt=observer.receipt()
                self.assertEqual(receipt['admission'],'unknown')
                self.assertEqual(receipt['response_content_check'],'not-checked')
                self.assertIsNotNone(receipt['pending'])
                self.assertEqual(receipt['request_count'],1)
                self.assertFalse(receipt['matched_host_expectation'])

    def test_allowed_response_cannot_change_pod_security_context(self):
        value=pod();value['spec']['securityContext']={'runAsNonRoot':True,'runAsUser':10000}
        source=prepared(value)
        for context in ({'runAsNonRoot':True,'runAsUser':0},
                        {'runAsNonRoot':False,'runAsUser':10000},
                        {'runAsNonRoot':True}):
            with self.subTest(context=context):
                kube=FakeKube();observer=O.CandidateObserver(source,kube);observer.observe()
                def respond(body):
                    altered=copy.deepcopy(body);altered['spec']['securityContext']=context
                    return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
                kube.respond=respond
                with self.assertRaises(O.CandidateObservationError):observer.verify(progress=lambda v:None)
                self.assertEqual(observer.receipt()['admission'],'unknown')
                self.assertEqual(observer.receipt()['request_count'],1)
        kube=FakeKube();observer=O.CandidateObserver(source,kube);observer.observe()
        self.assertEqual(observer.verify(progress=lambda v:None)['response_content_check'],'matched')

    def test_allowed_response_cannot_change_security_boolean_to_number(self):
        value=pod();value['spec']['securityContext']={'runAsNonRoot':True}
        value['spec']['containers'][0]['securityContext']['allowPrivilegeEscalation']=True
        source=prepared(value)
        for level in ('pod', 'container'):
            with self.subTest(level=level):
                kube=FakeKube();observer=O.CandidateObserver(source,kube);observer.observe()
                def respond(body):
                    altered=copy.deepcopy(body)
                    if level == 'pod':
                        altered['spec']['securityContext']['runAsNonRoot']=1
                    else:
                        altered['spec']['containers'][0]['securityContext']['allowPrivilegeEscalation']=1
                    return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
                kube.respond=respond
                with self.assertRaises(O.CandidateObservationError):observer.verify(progress=lambda v:None)
                receipt=observer.receipt()
                self.assertEqual(receipt['admission'],'unknown')
                self.assertIsNotNone(receipt['pending'])
                self.assertEqual(receipt['request_count'],1)

    def test_allowed_response_cannot_claim_persisted_identity(self):
        for metadata in ({'uid':str(uuid.UUID(int=42))}, {'resourceVersion':'123'},
                         {'generation':1}, {'creationTimestamp':'2026-10-04T00:00:00Z'},
                         {'managedFields':[{'manager':'other'}]}, {'unexpected':'value'}):
            with self.subTest(metadata=metadata):
                kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
                def respond(body):
                    altered=copy.deepcopy(body);altered['metadata'].update(metadata)
                    return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
                kube.respond=respond
                with self.assertRaises(O.CandidateObservationError):observer.verify(progress=lambda v:None)
                self.assertEqual(observer.receipt()['admission'],'unknown')
                self.assertIsNotNone(observer.receipt()['pending'])
                self.assertEqual(observer.receipt()['request_count'],1)

        kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
        def defaults(body):
            altered=copy.deepcopy(body)
            altered['metadata'].update(uid='',resourceVersion='',generation=0,
                                       creationTimestamp=None,managedFields=[])
            return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
        kube.respond=defaults
        self.assertEqual(observer.verify(progress=lambda v:None)['response_content_check'],'matched')

    def test_allowed_response_cannot_add_top_level_claims(self):
        for extra in ({'items':[{'kind':'Pod'}]}, {'unexpected':{'persisted':True}}):
            with self.subTest(extra=extra):
                kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
                def respond(body):
                    altered=copy.deepcopy(body);altered.update(extra)
                    return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
                kube.respond=respond
                with self.assertRaises(O.CandidateObservationError):observer.verify(progress=lambda v:None)
                receipt=observer.receipt()
                self.assertEqual(receipt['admission'],'unknown')
                self.assertIsNotNone(receipt['pending'])
                self.assertEqual(receipt['request_count'],1)

    def test_allowed_response_rejects_duplicate_json_keys_at_any_depth(self):
        for suffix in ('"kind":"Other",', '"metadata":{"uid":"persisted"},',
                       '"spec":{"hostNetwork":true},'):
            with self.subTest(suffix=suffix):
                kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
                kube.respond=lambda body: SimpleNamespace(returncode=0,
                    stdout='{'+suffix+json.dumps(body)[1:],stderr='')
                with self.assertRaises(O.CandidateObservationError):observer.verify(progress=lambda v:None)
                self.assertEqual(observer.receipt()['admission'],'unknown')
                self.assertIsNotNone(observer.receipt()['pending'])
                self.assertEqual(observer.receipt()['request_count'],1)
        kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
        kube.respond=lambda body: SimpleNamespace(returncode=0,
            stdout=json.dumps(body).replace('"containers": [', '"containers": [], "containers": [', 1),stderr='')
        with self.assertRaises(O.CandidateObservationError):observer.verify(progress=lambda v:None)
        self.assertIsNotNone(observer.receipt()['pending'])

    def test_allowed_response_cannot_inject_pod_identity_or_scheduling(self):
        for changes in ({'serviceAccountName':'admin'}, {'nodeName':'other-node'},
                        {'nodeSelector':{'role':'privileged'}},
                        {'tolerations':[{'operator':'Exists'}]},
                        {'affinity':{'nodeAffinity':{}}},
                        {'schedulerName':'other-scheduler'}):
            with self.subTest(changes=changes):
                kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
                def respond(body):
                    altered=copy.deepcopy(body);altered['spec'].update(changes)
                    return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
                kube.respond=respond
                with self.assertRaises(O.CandidateObservationError):observer.verify(progress=lambda v:None)
                self.assertEqual(observer.receipt()['admission'],'unknown')
                self.assertIsNotNone(observer.receipt()['pending'])
                self.assertEqual(observer.receipt()['request_count'],1)

        kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
        def defaults(body):
            altered=copy.deepcopy(body)
            altered['spec'].update(serviceAccountName='default', nodeName='', nodeSelector={},
                                   tolerations=[], schedulerName='default-scheduler')
            return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
        kube.respond=defaults
        self.assertEqual(observer.verify(progress=lambda v:None)['response_content_check'],'matched')

    def test_allowed_response_cannot_change_runtime_or_priority(self):
        for changes in ({'runtimeClassName':'alternate'}, {'priorityClassName':'system-node-critical'},
                        {'priority':1000000000}, {'preemptionPolicy':'Never'}):
            with self.subTest(changes=changes):
                kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
                def respond(body):
                    altered=copy.deepcopy(body);altered['spec'].update(changes)
                    return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
                kube.respond=respond
                with self.assertRaises(O.CandidateObservationError):observer.verify(progress=lambda v:None)
                self.assertEqual(observer.receipt()['admission'],'unknown')
                self.assertIsNotNone(observer.receipt()['pending'])
                self.assertEqual(observer.receipt()['request_count'],1)

        kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
        def defaults(body):
            altered=copy.deepcopy(body)
            altered['spec'].update(runtimeClassName='', priority=0, priorityClassName='',
                                   preemptionPolicy='PreemptLowerPriority')
            return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
        kube.respond=defaults
        self.assertEqual(observer.verify(progress=lambda v:None)['response_content_check'],'matched')

    def test_allowed_response_cannot_add_process_sharing_or_host_aliases(self):
        for changes in ({'shareProcessNamespace':True},
                        {'hostAliases':[{'ip':'127.0.0.1','hostnames':['metadata.internal']}]}):
            with self.subTest(changes=changes):
                kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
                def respond(body):
                    altered=copy.deepcopy(body);altered['spec'].update(changes)
                    return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
                kube.respond=respond
                with self.assertRaises(O.CandidateObservationError):observer.verify(progress=lambda v:None)
                receipt=observer.receipt()
                self.assertEqual(receipt['admission'],'unknown')
                self.assertIsNotNone(receipt['pending'])
                self.assertEqual(receipt['request_count'],1)

        kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
        def defaults(body):
            altered=copy.deepcopy(body)
            altered['spec'].update(shareProcessNamespace=False, hostAliases=[])
            return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
        kube.respond=defaults
        self.assertEqual(observer.verify(progress=lambda v:None)['response_content_check'],'matched')

    def test_allowed_response_cannot_add_scheduling_or_lifecycle_controls(self):
        for changes in ({'topologySpreadConstraints':[{'maxSkew':1,'topologyKey':'zone',
                            'whenUnsatisfiable':'DoNotSchedule','labelSelector':{}}]},
                        {'readinessGates':[{'conditionType':'example.test/Ready'}]},
                        {'overhead':{'cpu':'1'}}, {'activeDeadlineSeconds':10}):
            with self.subTest(changes=changes):
                kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
                def respond(body):
                    altered=copy.deepcopy(body);altered['spec'].update(changes)
                    return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
                kube.respond=respond
                with self.assertRaises(O.CandidateObservationError):observer.verify(progress=lambda v:None)
                receipt=observer.receipt()
                self.assertEqual(receipt['admission'],'unknown')
                self.assertIsNotNone(receipt['pending'])
                self.assertEqual(receipt['request_count'],1)

        kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
        def defaults(body):
            altered=copy.deepcopy(body)
            altered['spec'].update(topologySpreadConstraints=[], readinessGates=[], overhead={})
            return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
        kube.respond=defaults
        self.assertEqual(observer.verify(progress=lambda v:None)['response_content_check'],'matched')

    def test_allowed_response_cannot_add_unrecognized_pod_controls(self):
        for changes in ({'hostUsers':False},
                        {'resourceClaims':[{'name':'claim','source':{'resourceClaimName':'other'}}]},
                        {'terminationGracePeriodSeconds':0}):
            with self.subTest(changes=changes):
                kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
                def respond(body):
                    altered=copy.deepcopy(body);altered['spec'].update(changes)
                    return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
                kube.respond=respond
                with self.assertRaises(O.CandidateObservationError):observer.verify(progress=lambda v:None)
                self.assertEqual(observer.receipt()['admission'],'unknown')
                self.assertIsNotNone(observer.receipt()['pending'])
                self.assertEqual(observer.receipt()['request_count'],1)

        kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
        def defaults(body):
            altered=copy.deepcopy(body)
            altered['spec'].update(hostUsers=True, resourceClaims=[], terminationGracePeriodSeconds=30)
            return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
        kube.respond=defaults
        self.assertEqual(observer.verify(progress=lambda v:None)['response_content_check'],'matched')

    def test_allowed_response_cannot_add_container_resource_claims(self):
        for extra in ({'claims':[{'name':'accelerator'}]}, {'claims':[]}):
            with self.subTest(extra=extra):
                kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
                def respond(body):
                    altered=copy.deepcopy(body)
                    altered['spec']['containers'][0]['resources'].update(extra)
                    return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
                kube.respond=respond
                with self.assertRaises(O.CandidateObservationError):observer.verify(progress=lambda v:None)
                self.assertEqual(observer.receipt()['admission'],'unknown')
                self.assertIsNotNone(observer.receipt()['pending'])
                self.assertEqual(observer.receipt()['request_count'],1)

    def test_allowed_response_cannot_claim_live_pod_status(self):
        for status in ({'phase':'Running'}, {'conditions':[{'type':'Ready','status':'True'}]}):
            with self.subTest(status=status):
                kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
                def respond(body):
                    altered=copy.deepcopy(body);altered['status']=status
                    return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
                kube.respond=respond
                with self.assertRaises(O.CandidateObservationError):observer.verify(progress=lambda v:None)
                self.assertEqual(observer.receipt()['admission'],'unknown')
                self.assertIsNotNone(observer.receipt()['pending'])
                self.assertEqual(observer.receipt()['request_count'],1)

        kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
        kube.respond=lambda body: SimpleNamespace(returncode=0,
            stdout=json.dumps(dict(body,status={})),stderr='')
        self.assertEqual(observer.verify(progress=lambda v:None)['response_content_check'],'matched')

    def test_allowed_response_cannot_add_metadata_lifecycle_controls(self):
        for changes in ({'generateName':'different-'},
                        {'deletionTimestamp':'2026-10-04T00:00:00Z'},
                        {'deletionGracePeriodSeconds':0}):
            with self.subTest(changes=changes):
                kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
                def respond(body):
                    altered=copy.deepcopy(body);altered['metadata'].update(changes)
                    return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
                kube.respond=respond
                with self.assertRaises(O.CandidateObservationError):observer.verify(progress=lambda v:None)
                self.assertEqual(observer.receipt()['admission'],'unknown')
                self.assertIsNotNone(observer.receipt()['pending'])
                self.assertEqual(observer.receipt()['request_count'],1)

        kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
        def defaults(body):
            altered=copy.deepcopy(body)
            altered['metadata'].update(generateName='', deletionTimestamp=None,
                                       deletionGracePeriodSeconds=None)
            return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
        kube.respond=defaults
        self.assertEqual(observer.verify(progress=lambda v:None)['response_content_check'],'matched')

    def test_allowed_response_cannot_redirect_dns_or_hostname(self):
        for changes in ({'dnsPolicy':'None'},
                        {'dnsConfig':{'nameservers':['127.0.0.1']}},
                        {'hostname':'alternate'}, {'subdomain':'internal'},
                        {'enableServiceLinks':False}, {'setHostnameAsFQDN':True}):
            with self.subTest(changes=changes):
                kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
                def respond(body):
                    altered=copy.deepcopy(body);altered['spec'].update(changes)
                    return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
                kube.respond=respond
                with self.assertRaises(O.CandidateObservationError):observer.verify(progress=lambda v:None)
                receipt=observer.receipt()
                self.assertEqual(receipt['admission'],'unknown')
                self.assertIsNotNone(receipt['pending'])
                self.assertEqual(receipt['request_count'],1)

        kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
        def defaults(body):
            altered=copy.deepcopy(body)
            altered['spec'].update(dnsPolicy='ClusterFirst', hostname='', subdomain='',
                                   dnsConfig={}, enableServiceLinks=True, setHostnameAsFQDN=False)
            return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
        kube.respond=defaults
        self.assertEqual(observer.verify(progress=lambda v:None)['response_content_check'],'matched')

    def test_allowed_response_cannot_inject_metadata_selectors(self):
        for changes in ({'labels':{'access':'privileged'}},
                        {'annotations':{'scheduler.example/instruction':'alternate'}},
                        {'finalizers':['foreign.example/protect']}):
            with self.subTest(changes=changes):
                kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
                def respond(body):
                    altered=copy.deepcopy(body);altered['metadata'].update(changes)
                    return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
                kube.respond=respond
                with self.assertRaises(O.CandidateObservationError):observer.verify(progress=lambda v:None)
                self.assertEqual(observer.receipt()['admission'],'unknown')
                self.assertIsNotNone(observer.receipt()['pending'])
                self.assertEqual(observer.receipt()['request_count'],1)

        kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
        def defaults(body):
            altered=copy.deepcopy(body)
            altered['metadata'].update(labels={}, annotations={}, finalizers=[])
            return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
        kube.respond=defaults
        self.assertEqual(observer.verify(progress=lambda v:None)['response_content_check'],'matched')

    def test_allowed_response_cannot_acquire_owner_or_image_pull_credentials(self):
        for location, changes in (('metadata', {'ownerReferences':[{'uid':str(uuid.uuid4()),
                                      'kind':'Deployment','name':'foreign','apiVersion':'apps/v1'}]}),
                                  ('spec', {'imagePullSecrets':[{'name':'private-registry'}]})):
            with self.subTest(location=location):
                kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
                def respond(body):
                    altered=copy.deepcopy(body);altered[location].update(changes)
                    return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
                kube.respond=respond
                with self.assertRaises(O.CandidateObservationError):observer.verify(progress=lambda v:None)
                receipt=observer.receipt()
                self.assertEqual(receipt['admission'],'unknown')
                self.assertEqual(receipt['response_content_check'],'not-checked')
                self.assertIsNotNone(receipt['pending'])
                self.assertEqual(receipt['request_count'],1)

        kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
        def defaults(body):
            altered=copy.deepcopy(body)
            altered['metadata']['ownerReferences']=[]
            altered['spec']['imagePullSecrets']=[]
            return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
        kube.respond=defaults
        self.assertEqual(observer.verify(progress=lambda v:None)['response_content_check'],'matched')

    def test_allowed_response_cannot_inject_container_security_context(self):
        for context in ({'privileged':False,'allowPrivilegeEscalation':True},
                        {'privileged':False,'runAsUser':0},
                        {'privileged':False,'capabilities':{'add':['SYS_ADMIN']}}):
            with self.subTest(context=context):
                kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
                def respond(body):
                    altered=copy.deepcopy(body)
                    altered['spec']['containers'][0]['securityContext']=context
                    return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
                kube.respond=respond
                with self.assertRaises(O.CandidateObservationError):observer.verify(progress=lambda v:None)
                receipt=observer.receipt()
                self.assertEqual(receipt['admission'],'unknown')
                self.assertEqual(receipt['response_content_check'],'not-checked')
                self.assertIsNotNone(receipt['pending'])
                self.assertEqual(receipt['request_count'],1)

    def test_allowed_response_cannot_mutate_port_attributes(self):
        value=pod();value['spec']['containers'][0]['ports']=[{'containerPort':8080}]
        source=prepared(value)
        for changes in ({'protocol':'UDP'}, {'hostIP':'0.0.0.0'},
                        {'name':'unexpected'}, {'containerPort':True}):
            with self.subTest(changes=changes):
                kube=FakeKube();observer=O.CandidateObserver(source,kube);observer.observe()
                def respond(body):
                    altered=copy.deepcopy(body)
                    altered['spec']['containers'][0]['ports'][0].update(changes)
                    return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
                kube.respond=respond
                with self.assertRaises(O.CandidateObservationError):observer.verify(progress=lambda v:None)
                self.assertEqual(observer.receipt()['admission'],'unknown')
                self.assertIsNotNone(observer.receipt()['pending'])
                self.assertEqual(observer.receipt()['request_count'],1)

        kube=FakeKube();observer=O.CandidateObserver(source,kube);observer.observe()
        def defaulted(body):
            effective=copy.deepcopy(body)
            effective['spec']['containers'][0]['ports'][0]['protocol']='TCP'
            return SimpleNamespace(returncode=0,stdout=json.dumps(effective),stderr='')
        kube.respond=defaulted
        self.assertEqual(observer.verify(progress=lambda v:None)['response_content_check'],'matched')

    def test_allowed_response_cannot_inject_container_execution_fields(self):
        for changes in ({'workingDir':'/tmp'}, {'stdin':True},
                        {'lifecycle':{'postStart':{'exec':{'command':['/bin/true']}}}},
                        {'terminationMessagePath':'/tmp/result'}):
            with self.subTest(changes=changes):
                kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
                def respond(body):
                    altered=copy.deepcopy(body)
                    altered['spec']['containers'][0].update(changes)
                    return SimpleNamespace(returncode=0,stdout=json.dumps(altered),stderr='')
                kube.respond=respond
                with self.assertRaises(O.CandidateObservationError):observer.verify(progress=lambda v:None)
                self.assertEqual(observer.receipt()['admission'],'unknown')
                self.assertIsNotNone(observer.receipt()['pending'])
                self.assertEqual(observer.receipt()['request_count'],1)

        kube=FakeKube();observer=O.CandidateObserver(self.input,kube);observer.observe()
        def defaulted(body):
            effective=copy.deepcopy(body)
            effective['spec']['containers'][0].update(
                terminationMessagePath='/dev/termination-log', terminationMessagePolicy='File',
                stdin=False, stdinOnce=False, tty=False, workingDir='')
            return SimpleNamespace(returncode=0,stdout=json.dumps(effective),stderr='')
        kube.respond=defaulted
        self.assertEqual(observer.verify(progress=lambda v:None)['response_content_check'],'matched')

    def test_allowed_response_accepts_harmless_server_defaults_and_equivalent_quantities(self):
        self.ready()
        def respond(body):
            effective=copy.deepcopy(body)
            effective['spec'].update(hostNetwork=False, volumes=[])
            effective['spec']['securityContext']={}
            effective['spec']['containers'][0]['resources']['requests']['cpu']='0.01'
            effective['spec']['containers'][0]['resources']['limits']['memory']='33554432'
            return SimpleNamespace(returncode=0,stdout=json.dumps(effective),stderr='')
        self.kube.respond=respond
        self.assertEqual(self.observer.verify(progress=lambda v:None)['result'],'passed')

    def test_denial_reports_subset_without_claiming_complete_actual_violations(self):
        value=pod(privileged=True);value['spec']['hostNetwork']=True
        self.observer=O.CandidateObserver(prepared(value),self.kube);self.ready()
        self.kube.respond=lambda body:self.kube.denial(body,['P01'])
        actual=self.observer.verify(progress=lambda v:None)
        self.assertEqual(actual['expected_violation_ids'],['P01','P02'])
        self.assertEqual(actual['reported_violation_ids'],['P01']);self.assertEqual(actual['admission'],'denied')
        self.assertEqual(actual['response_content_check'],'not-applicable')

    def test_exact_multiple_owned_denials(self):
        value=pod(privileged=True);value['spec']['hostPID']=True
        self.observer=O.CandidateObserver(prepared(value),self.kube);self.ready()
        self.kube.respond=lambda body:self.kube.denial(body,['P01','P02'])
        self.assertEqual(self.observer.verify(progress=lambda v:None)['reported_violation_ids'],['P01','P02'])

    def test_denial_with_stdout_is_unknown_and_never_retried(self):
        for payload in ('{}', json.dumps(pod(privileged=True))):
            with self.subTest(payload=payload):
                kube=FakeKube();observer=O.CandidateObserver(prepared(pod(privileged=True)),kube)
                observer.observe()
                def respond(body):
                    response=kube.denial(body,['P01'])
                    response.stdout=payload
                    return response
                kube.respond=respond
                with self.assertRaises(O.CandidateObservationError):
                    observer.verify(progress=lambda v:None)
                receipt=observer.receipt()
                self.assertEqual(receipt['admission'],'unknown')
                self.assertIsNotNone(receipt['pending'])
                self.assertEqual(receipt['request_count'],1)
                with self.assertRaises(O.CandidateObservationError):
                    observer.verify(progress=lambda v:None)
                self.assertEqual(len(kube.requests),1)

    def test_invalid_422_exact_single_known_cause_for_each_policy(self):
        body=self.input.body();name=body['metadata']['name']
        for rule in (f'P{i:02}' for i in range(1,7)):
            response=self.kube.invalid_denial(body,rule)
            for text in (response.stderr,response.stderr.rstrip('\n'),response.stderr.replace('\n','\r\n')):
                with self.subTest(rule=rule,text=text):
                    self.assertEqual(O.reported_denials(text,name,self.kube.items),[rule])

    def test_invalid_422_denial_keeps_host_superset_and_one_durable_attempt(self):
        value=pod(privileged=True);value['spec']['hostPID']=True
        prepared_input=prepared(value);self.observer=O.CandidateObserver(prepared_input,self.kube);self.ready()
        self.kube.respond=lambda body:self.kube.invalid_denial(body,'P01')
        receipts=[]
        def save(receipt):
            if receipt['request_count']==0:self.assertEqual(self.kube.requests,[])
            receipts.append(receipt)
        actual=self.observer.verify(progress=save)
        self.assertTrue(any(receipt['pending'] for receipt in receipts))
        self.assertEqual(self.kube.requests,[prepared_input.body()])
        self.assertEqual(actual['result'],'passed');self.assertEqual(actual['admission'],'denied')
        self.assertEqual(actual['expected_violation_ids'],['P01','P02'])
        self.assertEqual(actual['reported_violation_ids'],['P01'])
        self.assertTrue(actual['absence_before'] and actual['absence_after'] and actual['baseline_stable'])
        text=json.dumps(receipts);self.assertNotIn(PRIVATE,text);self.assertNotIn('private-pod',text)
        with self.assertRaises(O.CandidateObservationError):self.observer.verify(progress=save)
        self.assertEqual(len(self.kube.requests),1)

    def test_invalid_422_other_errors_and_foreign_trailing_clauses_stay_unknown(self):
        body=prepared(pod(privileged=True)).body();name=body['metadata']['name']
        valid=self.kube.invalid_denial(body,'P01').stderr
        policy='shirokuma-local-disallow-privileged-containers'
        clause=valid.split(' is invalid: : ',1)[1].rstrip('\n')
        variants=[valid+'RBAC denial',valid.rstrip('\n')+' foreign error\n',
            valid.replace(name,'foreign-pod'),valid.replace('The pods ','The Pod '),
            valid.replace(' is invalid: : ',' is invalid: spec.containers: '),
            valid.replace(' is invalid: : ',' is invalid: '),
            valid.replace("with binding '"+policy+"'","with binding 'foreign-binding'"),
            valid.replace("ValidatingAdmissionPolicy '"+policy+"'","ValidatingAdmissionPolicy 'foreign-policy'"),
            valid.replace('Privileged containers are forbidden.','Different validation error.'),
            valid.replace(clause,'['+clause+']'),valid.rstrip('\n')+', '+clause+'\n',
            valid+clause+'\n',valid.replace("'",'"'),
            'Error from server (Invalid): '+valid,
            f'The pods "{name}" is invalid: spec.containers: Required value\n']
        for text in variants:
            with self.subTest(text=text):
                self.assertEqual(O.reported_denials(text,name,self.kube.items),[])
                k=FakeKube();o=O.CandidateObserver(prepared(pod(privileged=True)),k);o.observe()
                k.respond=lambda body:SimpleNamespace(returncode=1,stdout='',stderr=text)
                with self.assertRaises(O.CandidateObservationError):o.verify(progress=lambda v:None)
                receipt=o.receipt();self.assertEqual(receipt['admission'],'unknown')
                self.assertIsNotNone(receipt['pending']);self.assertEqual(receipt['reported_violation_ids'],[])
                self.assertEqual(len(k.requests),1);self.assertNotIn(text,json.dumps(receipt))
                with self.assertRaises(O.CandidateObservationError):o.verify(progress=lambda v:None)
                self.assertEqual(len(k.requests),1)

    def test_policy_and_namespace_identity_and_generation_are_bound(self):
        edits=[lambda k:k.namespace['metadata'].update(uid=str(uuid.UUID(int=100))),
               lambda k:k.namespace['metadata']['labels'].update(extra='changed'),
               lambda k:next(iter(k.resources.values()))['metadata'].update(generation=2),
               lambda k:next(iter(k.resources.values()))['metadata'].update(uid=str(uuid.UUID(int=101)))]
        for edit in edits:
            with self.subTest(edit=edit):
                k=FakeKube();o=O.CandidateObserver(self.input,k);o.observe();edit(k)
                with self.assertRaises(Exception):o.verify(progress=lambda v:None)
                self.assertEqual(k.requests,[]);self.assertFalse(o.receipt()['matched_host_expectation'])

    def test_post_guard_drift_is_unknown_even_after_allowed_response(self):
        self.ready()
        def respond(body):
            self.kube.namespace['metadata']['labels']['extra']='changed'
            return SimpleNamespace(returncode=0,stdout=json.dumps(body),stderr='')
        self.kube.respond=respond
        with self.assertRaises(O.CandidateObservationError):self.observer.verify(progress=lambda v:None)
        actual=self.observer.receipt();self.assertEqual(actual['admission'],'unknown');self.assertIsNotNone(actual['pending'])

    def test_source_drift_stops_before_request(self):
        self.ready()
        with patch.object(O.G,'_source_hashes',return_value={}):
            with self.assertRaises(O.CandidateObservationError):self.observer.verify(progress=lambda v:None)
        self.assertEqual(self.kube.requests,[])

    def test_collision_never_adopted_or_deleted(self):
        self.kube.present=True
        with self.assertRaises(O.CandidateObservationError):self.ready()
        self.assertEqual(self.observer.receipt()['error_code'],'CANDIDATE_NAME_PRESENT')
        with self.assertRaises(O.CandidateObservationError):self.ready()
        self.assertEqual(self.kube.requests,[])

    def test_post_named_presence_is_unknown(self):
        self.ready()
        def respond(body):
            self.kube.present=True;return SimpleNamespace(returncode=0,stdout=json.dumps(body),stderr='')
        self.kube.respond=respond
        with self.assertRaises(O.CandidateObservationError):self.observer.verify(progress=lambda v:None)
        self.assertEqual(self.observer.receipt()['admission'],'unknown');self.assertEqual(len(self.kube.requests),1)

    def test_limit_range_defaults_and_malformed_list_stop_pre_model(self):
        for default in ({'default':{'cpu':'1'}},{'defaultRequest':{'cpu':'1'}}):
            k=FakeKube();k.limits['items']=[dict(kind='LimitRange',metadata=dict(namespace=O.M.NAMESPACE,
                uid=str(uuid.UUID(int=100))),spec={'limits':[dict(type='Container',**default)]})]
            with self.assertRaises(O.CandidateObservationError):O.CandidateObserver(self.input,k).observe()
            self.assertEqual(k.requests,[])
        self.kube.limits['items']={}
        with self.assertRaises(O.CandidateObservationError):self.ready()

    def test_generic_empty_list_is_supported_but_even_nondefault_limits_are_outside_scope(self):
        self.kube.limits['kind']='List'  # kubectl can wrap the typed API list in a generic v1 List.
        self.ready();self.assertEqual(self.observer.verify(progress=lambda v:None)['result'],'passed')
        k=FakeKube()
        k.limits['kind']='List'
        k.limits['items']=[dict(kind='LimitRange',metadata=dict(namespace=O.M.NAMESPACE,
            uid=str(uuid.UUID(int=100))),spec={'limits':[dict(type='Container',min={'cpu':'1m'})]})]
        with self.assertRaises(O.CandidateObservationError):O.CandidateObserver(self.input,k).observe()
        self.assertEqual(k.requests,[])

    def test_durable_intent_failure_means_zero_post(self):
        self.ready()
        with self.assertRaises(O.CandidateObservationError):self.observer.verify(progress=lambda v:(_ for _ in ()).throw(OSError('private')))
        self.assertEqual(self.kube.requests,[]);self.assertEqual(self.observer.receipt()['request_count'],0)

    def test_timeout_and_cancel_do_not_retry_or_promote_absence(self):
        for error in (TimeoutError('private'),KeyboardInterrupt()):
            k=FakeKube();o=O.CandidateObserver(self.input,k);o.observe()
            def fail(body):raise error
            k.respond=fail
            with self.assertRaises(BaseException) as caught:o.verify(progress=lambda v:None)
            if not isinstance(error,Exception):self.assertIs(caught.exception,error)
            self.assertEqual(len(k.requests),1);self.assertIsNotNone(o.receipt()['pending'])
            self.assertEqual(o.receipt()['admission'],'unknown')
            with self.assertRaises(O.CandidateObservationError):o.verify(progress=lambda v:None)

    def test_raw_response_errors_never_count_as_owned_denial(self):
        value=pod(privileged=True)
        for mutate in (lambda r:setattr(r,'stderr',r.stderr+'RBAC denial'),
                       lambda r:setattr(r,'stderr',r.stderr.replace('ValidatingAdmissionPolicy','OtherPolicy')),
                       lambda r:setattr(r,'stderr',r.stderr.replace('Forbidden','Invalid')),
                       lambda r:setattr(r,'stderr','private body'),
                       lambda r:setattr(r,'returncode',-1)):
            k=FakeKube();o=O.CandidateObserver(prepared(value),k);o.observe()
            def respond(body):
                r=k.denial(body,['P01']);mutate(r);return r
            k.respond=respond
            with self.assertRaises(O.CandidateObservationError):o.verify(progress=lambda v:None)
            self.assertEqual(o.receipt()['admission'],'unknown')
            self.assertNotIn('private body',json.dumps(o.receipt()))

    def test_schema_error_wrong_identity_and_oversize_are_unknown(self):
        for response in (SimpleNamespace(returncode=0,stdout='{}',stderr=''),
                         SimpleNamespace(returncode=0,stdout='{"kind":"Pod","metadata":null}',stderr=''),
                         SimpleNamespace(returncode=0,stdout='x'*(O.MAX_RESPONSE+1),stderr='')):
            k=FakeKube();o=O.CandidateObserver(self.input,k);o.observe();k.respond=lambda body:response
            with self.assertRaises(O.CandidateObservationError):o.verify(progress=lambda v:None)
            self.assertEqual(o.receipt()['admission'],'unknown')


if __name__=='__main__':unittest.main()
