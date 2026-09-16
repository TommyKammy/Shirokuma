from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import unittest
from unittest.mock import patch


DIRECTORY = Path(__file__).resolve().parents[1] / "tools/local_experiment"
sys.path.insert(0, str(DIRECTORY))
SPEC = importlib.util.spec_from_file_location("local_session", DIRECTORY / "local_session.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LocalTunnelTests(unittest.TestCase):
    def process(self, script):
        return subprocess.Popen([sys.executable, "-u", "-c", script], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, start_new_session=True)

    def test_reaps_only_its_child_after_body_failure(self):
        process = self.process("import time; print('Forwarding from 127.0.0.1:32123 -> 8080'); time.sleep(60)")
        with patch.object(MODULE.subprocess, "Popen", return_value=process) as start:
            with self.assertRaisesRegex(RuntimeError, "experiment failed"):
                with MODULE.trino_tunnel(timeout=2) as endpoint:
                    self.assertEqual(endpoint, "http://127.0.0.1:32123")
                    raise RuntimeError("experiment failed")
        self.assertIsNotNone(process.poll())
        command = start.call_args.args[0]
        self.assertIn(":8080", command)
        self.assertEqual(command[-2:], ["--address", "127.0.0.1"])

    def test_rejects_early_process_exit(self):
        process = self.process("print('error: service unavailable')")
        with patch.object(MODULE.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(MODULE.ExperimentError, "before readiness"):
                with MODULE.trino_tunnel(timeout=2):
                    self.fail("unready tunnel yielded")
        self.assertIsNotNone(process.poll())

    def test_timeout_reaps_unready_child(self):
        process = self.process("import time; time.sleep(60)")
        with patch.object(MODULE.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(MODULE.ExperimentError, "did not become ready"):
                with MODULE.trino_tunnel(timeout=0.1):
                    self.fail("unready tunnel yielded")
        self.assertIsNotNone(process.poll())

    def test_drains_connection_notices_without_stalling_child(self):
        process = self.process(
            "import sys; print('Forwarding from 127.0.0.1:32123 -> 8080'); "
            "sys.stdout.flush(); sys.stdout.write('Handling connection\\n' * 100000); sys.stdout.flush()"
        )
        with patch.object(MODULE.subprocess, "Popen", return_value=process):
            with MODULE.trino_tunnel(timeout=2):
                self.assertEqual(process.wait(timeout=5), 0)


    def test_scoped_handlers_restore_previous_values(self):
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        with MODULE.interruption_handlers():
            self.assertTrue(callable(signal.getsignal(signal.SIGTERM)))
        for sig, handler in previous.items():
            self.assertEqual(signal.getsignal(sig), handler)

    def test_cancellation_reaps_children_after_cleanup_with_live_tunnel(self):
        for cancellation in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=cancellation), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                tunnel = directory / "tunnel.py"
                tunnel.write_text(
                    "import os,time\nfrom pathlib import Path\n"
                    f"Path({str(directory / 'tunnel.pid')!r}).write_text(str(os.getpid()))\n"
                    "print('Forwarding from 127.0.0.1:32123 -> 8080', flush=True)\n"
                    "time.sleep(60)\n"
                )
                child = directory / "child.py"
                child.write_text(
                    "import os,signal,subprocess,sys,time\nfrom pathlib import Path\n"
                    f"directory=Path({str(directory)!r})\n"
                    "grandchild=subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGINT,signal.SIG_IGN); time.sleep(60)'])\n"
                    "def cleanup(signum, frame):\n"
                    " os.kill(int((directory/'tunnel.pid').read_text()),0)\n"
                    " (directory/'cleanup').write_text('tunnel-alive')\n"
                    " grandchild.terminate(); grandchild.wait(timeout=5)\n"
                    " raise SystemExit(0)\n"
                    "signal.signal(signal.SIGINT,cleanup)\n"
                    "(directory/'children.json').write_text(__import__('json').dumps([os.getpid(),grandchild.pid]))\n"
                    "time.sleep(60)\n"
                )
                harness = directory / "harness.py"
                harness.write_text(
                    "import subprocess,sys\n"
                    f"sys.path.insert(0,{str(DIRECTORY)!r})\n"
                    "import local_session as session\n"
                    "session.check_context=lambda: None\n"
                    "real_start=subprocess.Popen\n"
                    "def start(args, **kwargs):\n"
                    f" if args[0]=='kubectl': args=[sys.executable,'-u',{str(tunnel)!r}]\n"
                    " return real_start(args,**kwargs)\n"
                    "session.subprocess.Popen=start\n"
                    "real_run=session.run_experiment\n"
                    f"session.run_experiment=lambda command, **kwargs: real_run([sys.executable,'-u',{str(child)!r}],shutdown_timeout=2)\n"
                    "sys.argv=['local_session.py','smoke']\n"
                    "raise SystemExit(session.main())\n"
                )
                wrapper = subprocess.Popen([sys.executable, str(harness)], stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, start_new_session=True)
                pids = []
                try:
                    deadline = time.monotonic() + 5
                    while not (directory / "children.json").exists():
                        if wrapper.poll() is not None or time.monotonic() > deadline:
                            self.fail("test wrapper did not become ready")
                        time.sleep(0.01)
                    pids = [int((directory / "tunnel.pid").read_text()),
                            *json.loads((directory / "children.json").read_text())]
                    wrapper.send_signal(cancellation)
                    _, stderr = wrapper.communicate(timeout=10)
                    self.assertEqual(wrapper.returncode, 128 + cancellation, stderr.decode())
                    self.assertEqual((directory / "cleanup").read_text(), "tunnel-alive")
                    for pid in pids:
                        with self.assertRaises(ProcessLookupError):
                            os.kill(pid, 0)
                finally:
                    if wrapper.poll() is None:
                        wrapper.kill()
                        wrapper.wait(timeout=5)
                    for pid in pids:
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    wrapper.stdout.close()
                    wrapper.stderr.close()

    def test_stubborn_child_is_killed_after_bounded_grace(self):
        process = self.process(
            "import signal,time; signal.signal(signal.SIGINT,signal.SIG_IGN); "
            "signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready'); time.sleep(60)"
        )
        self.assertEqual(process.stdout.readline().strip(), b"ready")
        try:
            MODULE.stop_group(process, signal.SIGINT, 0.05, termination_timeout=0.05)
            self.assertEqual(process.returncode, -signal.SIGKILL)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()


if __name__ == "__main__":
    unittest.main()
