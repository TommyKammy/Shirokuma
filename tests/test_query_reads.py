"""Saved Reader listing: pure/fake filesystem only, without service calls."""
from copy import deepcopy
import fcntl
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.query_reads import contract as C, run as RUN
import test_query_read_contract as RF
import test_query_snapshots_run as SF
import test_query_filter_profile as FF
import test_query_count_owner as NF

TOKEN = 'a' * 32
SECOND = 'b' * 32


def summary(version=1, token=TOKEN, action=None, result='passed'):
    """Real pure producer projections, with no owner/runtime invocation."""
    action = action or ('count' if version == 4 else 'select')
    if version == 1:
        request = (C.R.request('tables', schema='smoke') if action == 'tables' else
                   C.R.request('describe', table='smoke.fixture_v1') if action == 'describe' else RF.request())
        identity = RF.identity()
        observation = (RF.inventory() if action == 'tables' else identity if action == 'describe' else
                       C.R.receipt_projection(C.R.project_rows(RF.result(), request, RF.schema(), identity)))
        value = dict.fromkeys(C.SUMMARY_V1 - C.V1_OPTIONAL)
        value.update(cleanup={'verified':True}, errors=[], catalog={}, inventory=RF.inventory(),
                     queries={}, runtime={}, query_drain={'verified':True}, observation=observation)
        if action != 'tables':
            value.update(identity_before=identity, identity_after=deepcopy(identity), identity_unchanged=True)
    else:
        request = SF.request(action) if version == 2 else FF.request() if version == 3 else NF.F.request()
        observation, _ = (SF.observed(request) if version == 2 else FF.observed(request) if version == 3 else NF.observed(request, count=0))
        value = dict.fromkeys(C.SUMMARY_NEW)
        value.update(cleanup_verified=True, cloud_calls=0, model_calls=0, retries=0, observation=observation)
        if version in C.PROFILES:
            value['profile'] = C.PROFILES[version]
    value.update(schema_version=version, evidence_token=token, request=request, request_sha256=C.R.digest(request),
                 started_at='2026-09-18T09:00:00+09:00', finished_at='2026-09-18T00:00:01.000000Z',
                 invocations=1, source_hashes={'historical/absent.py':'d'*64}, limits=[], result=result,
                 error_code=None if result in ('running','passed') else 'INTERRUPTED' if result == 'interrupted' else 'QUERY_FAILED')
    if result == 'running':
        value['finished_at'] = None
        value['invocations'] = 0
    return value


def put(base, version=1, token=TOKEN, document=None):
    root = base / C.ROOT_NAMES[version]
    root.mkdir(mode=0o700, exist_ok=True)
    directory = root / token
    directory.mkdir(mode=0o700)
    (directory / 'run.lock').touch(mode=0o600)
    if document is not False:
        data = C.canonical(summary(version, token) if document is None else document)
        (directory / 'summary.json').write_bytes(data)
        (directory / 'summary.json').chmod(0o600)
    return directory


class ContractTests(unittest.TestCase):
    def test_all_seven_actions_and_historical_source_hashes(self):
        for version, actions in C.ACTIONS.items():
            for action in actions:
                with self.subTest(version=version, action=action):
                    row = C.project_run(version, TOKEN, summary(version, action=action))
                    self.assertEqual(row['inspection_state'], 'saved')
                    self.assertEqual(row['action'], action)
                    self.assertEqual(row['started_at'], '2026-09-18T00:00:00.000000Z')
                    self.assertEqual(set(row), C.ENTRY_KEYS)

    def test_failed_interrupted_and_running_are_not_success_or_zero_count(self):
        for version in C.ROOT_NAMES:
            for result in ('failed','interrupted','running'):
                with self.subTest(version=version, result=result):
                    value = summary(version, result=result)
                    value['observation'] = None
                    if version == 1:
                        value['cleanup']['verified'] = False
                        for key in C.V1_OPTIONAL: value.pop(key, None)
                    else: value['cleanup_verified'] = False
                    row = C.project_run(version, TOKEN, value)
                    self.assertEqual(row['recorded_result'], result)
                    self.assertEqual(row['inspection_state'], 'incomplete' if result == 'running' else 'saved')
                    self.assertIsNone(row['snapshot_id'])
                    self.assertNotIn('count', row)

    def test_terminal_missing_timestamp_is_incomplete(self):
        value = summary(4); value['finished_at'] = None
        row = C.project_run(4, TOKEN, value)
        self.assertEqual((row['inspection_state'],row['recorded_result']), ('incomplete','passed'))
        self.assertIsNone(row['snapshot_id'])

    def test_version_token_digest_cleanup_and_window_tampering_fail_closed(self):
        for version in C.ROOT_NAMES:
            mutations = [lambda x:x.update(schema_version=True), lambda x:x.update(evidence_token=SECOND),
                         lambda x:x.update(request_sha256='e'*64), lambda x:x.update(extra='SQL secret')]
            mutations += ([lambda x:x['cleanup'].update(verified=False),
                           lambda x:x['identity_after'].update(current_snapshot_id='1')] if version == 1 else
                          [lambda x:x.update(cleanup_verified=False),
                           lambda x:x['observation']['identity_after'].update(current_snapshot_id=None)])
            for mutate in mutations:
                value=summary(version); mutate(value)
                with self.subTest(version=version, mutate=mutate):
                    self.assertEqual(C.project_run(version,TOKEN,value), C.minimal(version,TOKEN,'invalid','READS_RECEIPT_INVALID'))

    def test_public_omits_values_and_rejects_extra_or_incoherent_fields(self):
        rows=[C.project_run(v,TOKEN,summary(v)) for v in C.ROOT_NAMES]
        roots=[dict(C.inventory(v,'present',True,None), inspected_entries=1) for v in C.ROOT_NAMES]
        report=C.build_report(rows,None,roots)
        raw=C.canonical(report)
        for text in (b'"columns":',b'"rows":',b'"count":',b'"where":',b'"literal":',b'"sql":',b'"request":'):
            self.assertNotIn(text,raw)
        for mutate in (lambda x:x['entries'][0].update(current_snapshot_id=None),
                       lambda x:x['entries'][0].update(rows=[]),
                       lambda x:x['inventory'][0].update(inspected_entries=2),
                       lambda x:x['inventory'][0].update(root='not-observed'),
                       lambda x:x.update(command_result='incomplete')):
            value=deepcopy(report); mutate(value)
            with self.assertRaises(C.ReadsError): C.validate_public(value)

    def test_request_duplicates_and_bad_tokens(self):
        for raw in (b'{"command":"reads","token":null,"token":null}',b'{"command":"reads","token":"bad"}',b' '*1025,b'\xff'):
            with self.assertRaises(C.ReadsError): C.parse_request(raw)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve(); self.base.chmod(0o700)

    def collect(self, token=None): return RUN.collect(token,state=self.base)

    def test_absent_all_empty_and_single_missing_are_distinct(self):
        empty=self.collect(); self.assertEqual((empty['command_result'],empty['entries']),('complete',[]))
        missing=self.collect(TOKEN)
        self.assertEqual((missing['command_result'],missing['entries']),('incomplete',[]))
        self.assertEqual({x['error_code'] for x in missing['inventory']},{'READS_TOKEN_MISSING'})

    def test_same_token_in_four_roots_and_all_single_equivalence(self):
        for version in C.ROOT_NAMES: put(self.base,version)
        put(self.base,1,SECOND,summary(1,SECOND,result='failed'))
        before={str(p):RUN.FS.fingerprint(p.stat()) for p in self.base.rglob('*')}
        report=self.collect(); single=self.collect(TOKEN)
        self.assertEqual(report['command_result'],'complete')
        self.assertEqual(single['entries'],[x for x in report['entries'] if x['token']==TOKEN])
        self.assertEqual(len(single['entries']),4)
        self.assertEqual(before,{str(p):RUN.FS.fingerprint(p.stat()) for p in self.base.rglob('*')})

    def test_busy_shared_lock_skips_payload_and_is_complete(self):
        directory=put(self.base)
        (directory/'summary.json').write_bytes(b'invalid private secret')
        fd=os.open(directory/'run.lock',os.O_RDWR)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with patch.object(RUN.FS,'read_file',side_effect=AssertionError('payload read')):
                report=self.collect()
            self.assertEqual(report['command_result'],'complete')
            self.assertEqual(report['entries'],[C.minimal(1,TOKEN,'active')])
        finally: os.close(fd)

    def test_missing_lock_summary_and_unfinished_are_incomplete(self):
        for case in ('lock','summary','running'):
            with self.subTest(case=case), tempfile.TemporaryDirectory(dir=self.base) as temp:
                base=Path(temp); base.chmod(0o700)
                directory=put(base,document=False if case=='summary' else summary(result='running') if case=='running' else None)
                if case=='lock': (directory/'run.lock').unlink()
                report=RUN.collect(state=base)
                self.assertEqual(report['command_result'],'incomplete')
                self.assertEqual(report['entries'][0]['inspection_state'],'incomplete')

    def test_malformed_private_paths_and_wrong_permissions_are_invalid(self):
        for kind in ('duplicate','symlink','hardlink','mode','dir-mode'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=self.base) as temp:
                base=Path(temp); base.chmod(0o700); directory=put(base); file=directory/'summary.json'
                if kind=='duplicate': file.write_bytes(b'{"x":1,"x":2}')
                if kind=='symlink':
                    file.rename(base/'target'); file.symlink_to(base/'target')
                if kind=='hardlink': os.link(file,base/'linked')
                if kind=='mode': file.chmod(0o644)
                if kind=='dir-mode': directory.chmod(0o755)
                report=RUN.collect(state=base)
                self.assertEqual(report['entries'][0]['inspection_state'],'invalid')

    def test_root_symlink_is_not_followed(self):
        target=self.base/'target'; target.mkdir(mode=0o700)
        (self.base/'query-read').symlink_to(target,target_is_directory=True)
        report=self.collect()
        self.assertEqual(report['inventory'][0]['root'],'not-observed')
        self.assertEqual(report['command_result'],'incomplete')

    def test_entry_listing_and_read_caps_never_claim_prefix_complete(self):
        put(self.base); put(self.base,1,SECOND,summary(1,SECOND))
        with patch.object(C,'MAX_RUNS',1):
            report=self.collect(); self.assertEqual(len(report['entries']),1)
            self.assertEqual(report['inventory'][0]['error_code'],'READS_ENTRY_LIMIT')
        with patch.object(RUN,'MAX_DIRECTORY_ENTRIES',1):
            self.assertEqual(self.collect()['inventory'][0]['error_code'],'READS_ENTRY_LIMIT')
        for maximum in ('MAX_SUMMARY_BYTES','MAX_TOTAL_BYTES'):
            with patch.object(C,maximum,1):
                report=self.collect(); self.assertEqual(report['inventory'][0]['error_code'],'READS_READ_LIMIT')
                self.assertTrue(all(x['error_code']=='READS_READ_LIMIT' for x in report['entries']))

    def test_unrecognized_names_report_incomplete(self):
        put(self.base)
        (self.base/'query-read'/'not-a-token').touch(mode=0o600)
        report=self.collect()
        self.assertEqual(report['inventory'][0]['unrecognized_entries'],1)
        self.assertEqual(report['inventory'][0]['error_code'],'READS_UNRECOGNIZED_ENTRIES')

    def test_public_limit_returns_explicit_incomplete_single_frame(self):
        for index in range(20):
            token=format(index,'032x'); put(self.base,1,token,summary(1,token))
        output=io.StringIO()
        with patch.object(RUN,'STATE',self.base),patch.object(C,'MAX_PUBLIC_BYTES',5000):
            code=RUN.main(['reads','--request-stdin'],stdin=io.BytesIO(b'{"command":"reads","token":null}'),stdout=output)
        self.assertEqual(code,1)
        report=json.loads(output.getvalue())
        self.assertEqual(report['entries'],[])
        self.assertEqual({x['error_code'] for x in report['inventory']},{'READS_PUBLIC_LIMIT'})

    def test_second_read_change_discards_saved_projection(self):
        directory=put(self.base); original=RUN.check_files
        def changed(item,budget):
            (directory/'summary.json').write_bytes(C.canonical(summary(result='failed')))
            return original(item,budget)
        with patch.object(RUN,'check_files',side_effect=changed): report=self.collect()
        self.assertEqual(report['entries'],[C.minimal(1,TOKEN,'incomplete','READS_RECEIPT_CHANGED')])

    def test_root_inventory_change_discards_projection(self):
        directory=put(self.base); original=RUN.check_files
        def changed(item,budget):
            (directory.parent/'new').touch(mode=0o600)
            return original(item,budget)
        with patch.object(RUN,'check_files',side_effect=changed): report=self.collect()
        self.assertEqual(report['inventory'][0]['error_code'],'READS_ROOT_CHANGED')
        self.assertEqual(report['entries'][0]['inspection_state'],'incomplete')

    def test_first_cancel_survives_secondary_close_and_output_failure(self):
        put(self.base); cancel=KeyboardInterrupt('first')
        close=RUN.FS.close_preserving
        def closing(fds,original=None):
            result=close(fds,original)
            return RUN.FS.retain(result,OSError('secondary')) if original is not None else result
        with patch.object(RUN.FS,'read_file',side_effect=cancel),patch.object(RUN.FS,'close_preserving',side_effect=closing):
            with self.assertRaises(KeyboardInterrupt) as caught: self.collect()
        self.assertIs(caught.exception,cancel)
        class Broken(io.StringIO):
            def write(self,value): raise OSError('output')
        with patch.object(RUN,'collect',side_effect=cancel):
            with self.assertRaises(KeyboardInterrupt) as caught:
                RUN.main(['reads','--request-stdin'],stdin=io.BytesIO(b'{"command":"reads","token":null}'),stdout=Broken())
        self.assertIs(caught.exception,cancel)

    def test_main_single_frame_exit_and_bad_input_before_inspection(self):
        put(self.base)
        for token,exit_code in ((None,0),(SECOND,1)):
            output=io.StringIO()
            with patch.object(RUN,'STATE',self.base):
                code=RUN.main(['reads','--request-stdin'],stdin=io.BytesIO(C.canonical({'command':'reads','token':token})),stdout=output)
            self.assertEqual(code,exit_code)
            self.assertEqual(len(output.getvalue().splitlines()),1)
            C.validate_public(json.loads(output.getvalue()))
        output=io.StringIO()
        with patch.object(RUN,'collect',side_effect=AssertionError('inspection')):
            code=RUN.main(['reads','--request-stdin'],stdin=io.BytesIO(b'{"command":"reads","token":"bad"}'),stdout=output)
        self.assertEqual(code,1)
        self.assertEqual({x['error_code'] for x in json.loads(output.getvalue())['inventory']},{'READS_REQUEST_INVALID'})


if __name__ == '__main__': unittest.main()
