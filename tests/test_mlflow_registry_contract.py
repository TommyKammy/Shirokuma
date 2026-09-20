"""Pure fixed Registry schema shared by the writer and cold restore."""
from copy import deepcopy
import unittest
from tools.local_experiment.mlflow import registry_contract as C


def fixture():
    bound=dict(training_token='a'*32,run_id='b'*32,experiment_id='4',store_id='00000000-0000-0000-0000-000000000001',
        workload_sha256='c'*64,model_sha256='d'*64,artifact_relpath='iceberg-training/'+'b'*32+'/artifacts/safe-projection.json')
    model=dict(name=C.MODEL_NAME,creation_timestamp=1000,last_updated_timestamp=1001,description=None,
               tags=C.model_tags(bound['store_id']),aliases={})
    version=dict(name=C.MODEL_NAME,version='1',creation_timestamp=1001,last_updated_timestamp=1001,description=None,
        user_id=None,current_stage='None',source='file:///private/artifacts/'+bound['artifact_relpath'],run_id=bound['run_id'],
        status='READY',status_message=None,run_link=None,tags=C.version_tags(bound),aliases=[])
    evidence='e'*32
    index=dict(schema_version=1,profile=C.PROFILE,store_id=bound['store_id'],model_name=C.MODEL_NAME,model_pending=None,
               model=model,entries={bound['training_token']:dict(state='complete',binding=bound,evidence_token=evidence,version=version)})
    source=dict(schema_version=1,profile=C.PROFILE,binding=bound)
    result=dict(schema_version=1,profile=C.PROFILE,evidence_token=evidence,result='verified',binding=bound,model=model,
        model_version=version,created_model=True,created_version=True,source_unchanged=True,error_code=None,started_ms=1000,finished_ms=1002)
    return bound,index,source,result


class RegistryContractTests(unittest.TestCase):
    def test_completed_shared_contract_and_later_model_update(self):
        bound,index,source,result=fixture()
        self.assertEqual(C.validate_completed(index,result['evidence_token'],source,result,bound),result)
        index=deepcopy(index); index['model']['last_updated_timestamp']=2000
        C.validate_completed(index,result['evidence_token'],source,result,bound)
        self.assertTrue(C.canonical(source).endswith(b'\n'))

    def test_pinned_sdk_integer_and_rest_string_versions_without_precision_loss(self):
        for value in (1,'1',9223372036854775807,'9223372036854775807'):
            self.assertEqual(C.version(value),str(value))
        for value in (True,1.0,0,-1,'01','latest','9223372036854775808'):
            with self.subTest(value=value),self.assertRaises(C.RegistryError):C.version(value)

    def test_stage_null_alias_external_storage_and_tags_are_refused(self):
        bound,index,_,_=fixture(); original=index['entries'][bound['training_token']]['version']
        for key,value in [('current_stage',None),('current_stage','Production'),('aliases',['champion']),
                          ('status','PENDING_REGISTRATION'),('tags',{})]:
            with self.subTest(key=key,value=value),self.assertRaises(C.RegistryError):
                C.validate_version(dict(original,**{key:value}),bound,source_uri=original['source'])
        C.validate_version(original,bound,source_uri=original['source'],storage_location=None)
        C.validate_version(original,bound,source_uri=original['source'],storage_location=original['source'])
        with self.assertRaises(C.RegistryError):C.validate_version(original,bound,source_uri=original['source'],storage_location='file:///other')

    def test_pending_other_token_and_duplicate_version_block_registry(self):
        bound,index,_,_=fixture(); entry=deepcopy(index['entries'][bound['training_token']])
        entry['binding']['training_token']='f'*32; entry['evidence_token']='f'*32
        entry['version']['tags']=C.version_tags(entry['binding'])
        index['entries']['f'*32]=entry
        with self.assertRaises(C.RegistryError):C.validate_index(index,store_id=bound['store_id'])
        entry.update(state='pending',version=None)
        with self.assertRaisesRegex(C.RegistryError,'REGISTRY_PRIOR_OUTCOME_UNKNOWN'):C.validate_index(index,store_id=bound['store_id'])

    def test_receipt_and_source_cannot_be_rebound_to_another_training(self):
        bound,index,source,result=fixture()
        for target,key,value in [('result','created_version',False),('result','evidence_token','f'*32),
                                 ('source','schema_version',True)]:
            changed_result,changed_source=deepcopy(result),deepcopy(source)
            (changed_result if target=='result' else changed_source)[key]=value
            with self.subTest(key=key),self.assertRaises(C.RegistryError):
                C.validate_completed(index,result['evidence_token'],changed_source,changed_result,bound)
        bad=deepcopy(bound); bad['artifact_relpath']='../safe-projection.json'
        with self.assertRaises(C.RegistryError):C.validate_binding(bad)


if __name__=='__main__':unittest.main()
