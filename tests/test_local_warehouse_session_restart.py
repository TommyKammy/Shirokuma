"""Disposable process-exit boundaries; no cluster, SQL, or archived sessions."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid

from tools.local_experiment.warehouse_lifecycle import session_rpc as R, status as S


CHILD = """
import os
from pathlib import Path
import sys
from tools.local_experiment.warehouse_lifecycle import session_rpc as R

root, token, request_id, boundary = sys.argv[1:]
R.SOCKET_ROOT = Path(root) / 'sockets'
output = Path(root) / token
request = {'schema_version': 1, 'session_id': token, 'request_id': request_id,
           'warehouse': 'XS', 'action': 'chill'}
def handler(action, identity):
    return {**request, 'result': 'verified', 'before': R.TARGETS['wake'],
            'after': R.TARGETS['chill'], 'operation_id': '11111111-1111-4111-8111-111111111111',
            'receipt_sha256': 'a' * 64}
owner = R.SessionRPC(token, output, handler)
owner.__enter__()
persist = owner._persist
writes = 0
def exit_after_write():
    global writes
    persist()
    writes += 1
    if writes == (1 if boundary == 'intent' else 2):
        os._exit(73)
owner._persist = exit_after_write
owner._dispatch(request)
os._exit(74)
"""


class DisposableOwnerExitTest(unittest.TestCase):
    def test_new_process_cannot_adopt_exited_owners_unknown_request(self):
        for boundary in ('intent', 'handler'):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                root.chmod(0o700)
                (root / 'sockets').mkdir(mode=0o700)
                token = uuid.uuid4().hex
                request_id = str(uuid.uuid4())
                output = root / token
                output.mkdir(mode=0o700)
                child = subprocess.run([sys.executable, '-c', CHILD, str(root), token,
                                        request_id, boundary], cwd=Path(__file__).resolve().parent.parent,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False)
                self.assertEqual(child.returncode, 73, child.stderr.decode(errors='replace'))
                journal = output / 'rpc' / 'journal.json'
                original = journal.read_bytes()
                result = S.classify_recovery(token, root, request_id)
                self.assertEqual(result['request_lookup']['recorded_result'],
                                 'pending' if boundary == 'intent' else 'verified')
                self.assertEqual(result['recovery_classification'], 'unknown-no-replay')
                self.assertEqual(result['runtime_state'], 'unknown')
                self.assertEqual(result['reply_count'], 0)
                calls = []
                with mock.patch.object(R, 'SOCKET_ROOT', root / 'sockets'):
                    with self.assertRaisesRegex(R.RPCError, 'JOURNAL_COLLISION'):
                        R.SessionRPC(token, output, lambda *args: calls.append(args)).__enter__()
                self.assertEqual(calls, [])
                self.assertEqual(journal.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
