import unittest
from unittest.mock import patch

from tools.local_experiment.finops import contract as C, history as H, host_storage as S
from test_local_finops_docker_storage import projected
from test_local_finops_history import encoded
from test_local_finops_process_report import report
from test_local_finops_run import (host_storage_complete,host_usage_complete,
                                   mlflow_usage_complete,docker_storage_complete)
from test_local_finops_serving_usage import serving_usage_complete,public
from test_local_finops_serving_tmp_usage import serving_tmp_usage_complete


def coverage(reports):
    html=H.render(H.parse(encoded(reports)))
    return html.split('<section id="storage-coverage">',1)[1].split('</section>',1)[0]


def row(matrix,label):
    return matrix.split('<th scope="row">'+label+'</th>',1)[1].split('</tr>',1)[0]


class StorageCoverageHistoryTests(unittest.TestCase):
    def test_saved_versions_preserve_absent_reference_only_unknown_and_exact_zero(self):
        reports=[report(),public(host_storage_complete()),public(host_usage_complete()),
                 public(mlflow_usage_complete()),public(serving_usage_complete()),
                 public(serving_tmp_usage_complete())]
        self.assertEqual([item['schema_version'] for item in reports],[1,5,6,7,8,9])
        with patch('tools.local_experiment.finops.run.collect',side_effect=AssertionError('collection')):
            matrix=coverage(reports)
        self.assertIn('保存領域の観測範囲',matrix)
        self.assertEqual(matrix.count('scope="col"'),7)  # target + six saved reports
        self.assertEqual(matrix.count('scope="row"'),5)
        self.assertIn('#6 / v9',matrix)
        self.assertIn('AgentHouse Docker volume',matrix)
        serving_tmp=row(matrix,'Serving TMP')
        self.assertEqual(serving_tmp.count('このschemaには含まれません'),5)
        self.assertEqual(serving_tmp.count('論理 0 bytes'),1)
        home=row(matrix,'Serving HOME')
        self.assertEqual(home.count('論理 0 bytes'),2)
        self.assertIn('参照確認 / 未計測（NOT_MEASURED）',home)
        self.assertIn('参照確認 / 未計測（NOT_MEASURED）',row(matrix,'MLflow store'))
        self.assertNotIn('増加',matrix)
        self.assertNotIn('物理占有',matrix)

    def test_independent_unknown_rows_and_docker_never_become_zero_or_delta(self):
        host=serving_tmp_usage_complete()
        tmp=host['components'][3]
        tmp.update(usage_state='unknown',usage_reason='TREE_CHANGED',used_bytes=None,
                   regular_file_count=None,directory_count=None)
        host=S.validate(host)
        v9=public(host)
        prior=public(serving_usage_complete())
        matrix=coverage([prior,v9])
        self.assertIn('参照確認 / 論理bytes未取得',row(matrix,'Serving TMP'))
        self.assertIn('TREE_CHANGED',row(matrix,'Serving TMP'))
        self.assertNotIn('論理 0 bytes',row(matrix,'Serving TMP'))
        self.assertEqual(row(matrix,'Serving HOME').count('論理 0 bytes'),2)
        docker=docker_storage_complete()
        docker['agenthouse']=C.D.unknown('READ_FAILED')
        docker=C.D.validate(docker)
        old=prior
        newest=C.build_report(v9['components'],v9['disks'],v9['inference'],
             observed_at=v9['observed_at'],local_processes=v9['local_processes'],
             storage=v9['storage'],docker_storage=docker,host_storage=host)
        matrix=coverage([old,newest])
        self.assertIn('参照未取得 / 使用量未計測（NOT_MEASURED）',row(matrix,'AgentHouse Docker volume'))
        self.assertNotIn('論理 0 bytes',row(matrix,'AgentHouse Docker volume'))
        observed=C.D.validate(dict(schema_version=1,agenthouse=projected(),limits=C.D.LIMITS[:]))
        newest=C.build_report(v9['components'],v9['disks'],v9['inference'],
             observed_at=v9['observed_at'],local_processes=v9['local_processes'],
             storage=v9['storage'],docker_storage=observed,host_storage=host)
        matrix=coverage([old,newest])
        self.assertIn('参照確認 / 使用量未計測（NOT_MEASURED）',row(matrix,'AgentHouse Docker volume'))
        self.assertNotIn('論理 0 bytes',row(matrix,'AgentHouse Docker volume'))

    def test_failed_reference_in_old_schema_does_not_imply_attempted_usage(self):
        old=host_storage_complete()
        old['components'][0]=S.unknown('mlflow','READ_FAILED')
        old=S.validate(old)
        newer=serving_tmp_usage_complete()
        newer['components'][0]=S.measured_row(S.unknown('mlflow','READ_FAILED'),version=5)
        newer=S.validate(newer)
        matrix=coverage([public(old),public(newer)])
        mlflow=row(matrix,'MLflow store')
        self.assertIn('参照未取得 / 未計測（NOT_MEASURED）',mlflow)
        self.assertIn('参照未取得 / 論理bytes未取得',mlflow)
        self.assertEqual(mlflow.count('論理 0 bytes'),0)

    def test_report_order_is_time_order_and_input_is_never_mutated(self):
        old=public(serving_usage_complete());new=public(serving_tmp_usage_complete())
        self.assertEqual(old['observed_at'],new['observed_at'])
        first=H.parse(encoded([new,old]));self.assertEqual([i for i,_ in first],[0,1])
        before=encoded([old,new]);matrix=coverage([old,new])
        self.assertEqual(matrix,coverage([old,new]))
        self.assertEqual(encoded([old,new]),before)
        self.assertIn('#1 / v8',matrix)
        self.assertIn('#2 / v9',matrix)

    def test_reordered_reports_keep_storage_scope_with_original_observation(self):
        old=public(serving_usage_complete())
        new=public(serving_tmp_usage_complete())
        # Preserve every original sample window; only the enclosing report's
        # timestamp moves forward via the public builder.
        new=C.build_report(new['components'],new['disks'],new['inference'],
            observed_at='2026-09-20T23:59:59Z',local_processes=new['local_processes'],
            storage=new['storage'],docker_storage=new['docker_storage'],
            host_storage=new['host_storage'])
        C.validate_public(old)
        C.validate_public(new)
        entries=H.parse(encoded([new,old]))
        self.assertEqual([index for index,_ in entries],[1,0])
        matrix=H._storage_coverage(entries)
        self.assertIn('#1 / v8',matrix)
        self.assertIn('#2 / v9',matrix)
        tmp=row(matrix,'Serving TMP')
        self.assertLess(tmp.index('このschemaには含まれません'),tmp.index('論理 0 bytes'))


if __name__=='__main__':unittest.main()
