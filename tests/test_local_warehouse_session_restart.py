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
import socket
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
    if boundary == 'returned' and writes == 1:
        os._exit(76)  # Handler returned, but its response was not saved.
    persist()
    writes += 1
    if boundary in ('intent', 'handler', 'reply') and writes == {'intent': 1, 'handler': 2, 'reply': 3}[boundary]:
        os._exit(73)
owner._persist = exit_after_write
if boundary in ('reply', 'sent'):
    owner._read_request = lambda connection: request
    if boundary == 'reply':
        owner._send = lambda *args: os._exit(75)  # No response may precede the exit.
    else:
        send = owner._send
        def exit_after_send(*args):
            send(*args)
            os._exit(77)  # A successful send is not proof of caller receipt.
        owner._send = exit_after_send
    sender, receiver = socket.socketpair()
    with sender:
        owner._connection(receiver)
else:
    owner._dispatch(request)
os._exit(74)
"""


class DisposableOwnerExitTest(unittest.TestCase):
    def test_new_process_cannot_adopt_exited_owners_unknown_request(self):
        for boundary in ('intent', 'returned', 'handler', 'reply', 'sent'):
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
                self.assertEqual(child.returncode, {'returned': 76, 'sent': 77}.get(boundary, 73),
                                 child.stderr.decode(errors='replace'))
                journal = output / 'rpc' / 'journal.json'
                original = journal.read_bytes()
                result = S.classify_recovery(token, root, request_id)
                self.assertEqual(result['request_lookup']['recorded_result'],
                                  'pending' if boundary in ('intent', 'returned') else 'verified')
                self.assertEqual(result['recovery_classification'], 'unknown-no-replay')
                self.assertEqual(result['runtime_state'], 'unknown')
                self.assertEqual(result['reply_count'], 1 if boundary in ('reply', 'sent') else 0)
                # An unrelated new UUID is not a way around an exited owner's
                # occupied session identity; do not start a replacement owner.
                fresh_id = str(uuid.uuid4())
                fresh = S.classify_recovery(token, root, fresh_id)
                self.assertEqual(fresh['request_lookup']['observation'], 'not-recorded-in-snapshot')
                self.assertEqual(fresh['recovery_classification'], 'unknown-no-replay')
                self.assertEqual(fresh['runtime_state'], 'unknown')
                self.assertEqual(fresh['journal_sha256'], result['journal_sha256'])
                calls = []
                with mock.patch.object(R, 'SOCKET_ROOT', root / 'sockets'):
                    with self.assertRaisesRegex(R.RPCError, 'JOURNAL_COLLISION'):
                        R.SessionRPC(token, output, lambda *args: calls.append(args)).__enter__()
                self.assertEqual(calls, [])
                self.assertEqual(journal.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
