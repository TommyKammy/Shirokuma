"""Private filtered request and registered template; no Reader or services."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from tools.local_experiment.notebook import selection as S, filtered_selection as FS
from test_query_filter_profile import request


class FilteredSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.directory=Path(temporary.name).resolve(); self.path=self.directory/'request.json'

    def save(self,value):
        raw=FS.canonical_bytes(value); self.path.write_bytes(raw); self.path.chmod(0o600)
        return hashlib.sha256(raw).hexdigest()

    def test_current_head_request_reuses_filter_contract_without_mutating_input(self):
        value=request(column='id',literal='9007199254740993',columns=['name','id'],limit=1)
        original=deepcopy(value); result=FS.normalize_request(value)
        self.assertEqual(result,original); self.assertIsNot(result,value)
        self.assertEqual(FS.parse_request(FS.canonical_bytes(value)),value)
        self.assertEqual(FS.digest(value),hashlib.sha256(FS.canonical_bytes(value)).hexdigest())
        for literal in ('false','2024-02-29','1.2300','-0', '9223372036854775807'):
            self.assertEqual(FS.normalize_request(request(literal=literal))['where']['literal'],literal)

    def test_history_unknown_fields_null_and_noncanonical_literals_are_refused(self):
        invalid=[dict(request(),snapshot_id='1'),dict(request(),snapshot_id=1),
                 dict(request(),where={'column':'id','literal':'1','type':'bigint'}),
                 dict(request(),sql='DO_NOT_ECHO'),S.normalize_request(FS.F.base_request(request()))]
        for literal in ('NULL','1e2','+1','01',' 1','1 ','１',"1';DROP",'1'*129,'2026-02-29',''):
            value=request();value['where']['literal']=literal;invalid.append(value)
        for value in invalid:
            with self.subTest(value=value),self.assertRaisesRegex(FS.SelectionError,'^NOTEBOOK_SELECTION_INVALID$'):
                FS.normalize_request(value)

    def test_parser_rejects_duplicates_nan_bad_utf8_and_byte_limit(self):
        valid=FS.canonical_bytes(request())
        bad=[b'',b'\xff',b' '*4097,valid.replace(b'"limit":2',b'"limit":2,"limit":2'),
             valid.replace(b'"limit":2',b'"limit":NaN')]
        for raw in bad:
            with self.assertRaises(FS.SelectionError): FS.parse_request(raw)

    def test_arguments_use_actual_equals_flag_and_keep_literal_one_argument(self):
        value=request(literal='-123.4500')
        expected=S.arguments('/private/bin/shirokuma','/private/repo',FS.F.base_request(value))
        self.assertEqual(FS.arguments('/private/bin/shirokuma','/private/repo',value),
                         expected+['--where-column','id','--equals','-123.4500'])
        self.assertNotIn('--snapshot-id',FS.arguments('/private/bin/shirokuma','/private/repo',value))

    def test_shared_private_loader_binds_exact_profile_hash_and_bytes(self):
        value=request(); sha=self.save(value); before=self.path.stat()
        self.assertEqual(FS.load_request(self.path,sha),value)
        self.assertEqual(S._signature(self.path.stat()),S._signature(before))
        with self.assertRaises(S.SelectionError): S.load_request(self.path,sha)
        for mode in ('hash','noncanonical','permission','symlink'):
            self.path.unlink(); sha=self.save(value); path=self.path
            if mode=='hash': sha='0'*64
            if mode=='noncanonical':
                self.path.write_bytes(json.dumps(value).encode());sha=hashlib.sha256(self.path.read_bytes()).hexdigest()
            if mode=='permission': self.path.chmod(0o644)
            if mode=='symlink':
                path=self.directory/'alias';path.symlink_to(self.path)
            with self.subTest(mode=mode),self.assertRaises(FS.SelectionError): FS.load_request(path,sha)
        self.path.unlink(); base=FS.F.base_request(value)
        self.path.write_bytes(S.canonical_bytes(base));self.path.chmod(0o600)
        with self.assertRaises(FS.SelectionError): FS.load_request(self.path,S.digest(base))
        self.assertEqual(S.load_request(self.path,S.digest(base)),base)

    def test_template_is_registered_three_code_cells_with_no_input_or_outputs(self):
        root=Path(__file__).resolve().parents[1]
        raw=(root/FS.TEMPLATE_PATH).read_bytes(); value=FS.validate_template(raw)
        self.assertEqual(hashlib.sha256(raw).hexdigest(),FS.TEMPLATE_SHA256)
        codes=[c for c in value['cells'] if c['cell_type']=='code']
        self.assertEqual(tuple(''.join(c['source']) for c in codes),FS.CODE_CELLS)
        self.assertTrue(all(c['execution_count'] is None and c['outputs']==[] for c in codes))
        with self.assertRaises(FS.SelectionError): S.validate_template(raw)
        with self.assertRaises(FS.SelectionError): FS.validate_template((root/S.TEMPLATE_PATH).read_bytes())
        with self.assertRaises(FS.SelectionError): FS.validate_template(raw+b' ')


if __name__=='__main__': unittest.main()
