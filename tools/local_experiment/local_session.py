#!/usr/bin/env python3
"""Run a local lakehouse experiment through an automatically managed tunnel."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import threading
import time

from local_experiment import CONTEXT, ROOT, ExperimentError, check_context


class SessionInterrupted(BaseException):
    def __init__(self, signum: int):
        self.signum = signum


@contextmanager
def interruption_handlers():
    """Unwind normally on cancellation; let bounded cleanup finish once started."""
    previous = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}

    def interrupted(signum, _frame):
        for handled in previous:
            signal.signal(handled, signal.SIG_IGN)
        raise SessionInterrupted(signum)

    try:
        for signum in previous:
            signal.signal(signum, interrupted)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def signal_group(process: subprocess.Popen, signum: int) -> None:
    # macOS may report EPERM for an exited, unreaped group leader. Reap it
    # before signaling any surviving descendants of the session we created.
    process.poll()
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass
    except PermissionError:
        if process.poll() is None:
            raise
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass


def wait_for_exit(process: subprocess.Popen, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            process.wait(timeout=min(10, max(0, deadline - time.monotonic())))
            return
        except subprocess.TimeoutExpired:
            if time.monotonic() >= deadline:
                raise


def stop_group(process: subprocess.Popen, first: int, grace: float, termination_timeout: float = 5) -> None:
    phases = [(first, grace)]
    if first != signal.SIGTERM:
        phases.append((signal.SIGTERM, termination_timeout))
    phases.append((signal.SIGKILL, 5))
    try:
        for signum, timeout in phases:
            signal_group(process, signum)
            try:
                wait_for_exit(process, timeout)
                break
            except subprocess.TimeoutExpired:
                continue
    finally:
        # A finished group leader can still leave descendants holding pipes.
        signal_group(process, signal.SIGKILL)
        process.wait(timeout=5)


def run_experiment(command: list[str], shutdown_timeout: float = 180) -> int:
    process = subprocess.Popen(command, cwd=ROOT, start_new_session=True)
    try:
        return process.wait()
    except SessionInterrupted:
        # Keep the tunnel alive while the child stops dbt and cleans its tables.
        stop_group(process, signal.SIGINT, shutdown_timeout)
        raise
    finally:
        if process.poll() is None:
            stop_group(process, signal.SIGTERM, 5)
        else:
            signal_group(process, signal.SIGKILL)


@contextmanager
def trino_tunnel(timeout: float = 30):
    """Let kubectl allocate a free loopback port; always reap our own child."""
    process = subprocess.Popen(
        ["kubectl", "--context", CONTEXT, "-n", "shirokuma-dev", "port-forward",
         "service/trino-local", ":8080", "--address", "127.0.0.1"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
    )
    selector = selectors.DefaultSelector()
    drainer = None
    try:
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout
        output = b""
        endpoint = None
        while endpoint is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ExperimentError("local Trino port-forward did not become ready")
            if not selector.select(min(remaining, 0.25)):
                if process.poll() is not None:
                    raise ExperimentError("local Trino port-forward exited before readiness")
                continue
            chunk = process.stdout.read1(4096)
            if not chunk:
                raise ExperimentError("local Trino port-forward exited before readiness")
            output += chunk
            match = re.search(rb"(?:^|\n)Forwarding from 127\.0\.0\.1:([0-9]+) -> 8080\r?\n", output)
            if match:
                port = int(match.group(1))
                if not 1 <= port <= 65535:
                    raise ExperimentError("invalid local Trino forwarded port")
                endpoint = f"http://127.0.0.1:{port}"
            elif len(output) > 65536:
                raise ExperimentError("unexpected local Trino port-forward output")
        # Discard connection notices so a busy dbt run cannot fill the pipe.
        def drain():
            while process.stdout.read(4096):
                pass

        drainer = threading.Thread(target=drain, daemon=True)
        drainer.start()
        yield endpoint
    finally:
        selector.close()
        stop_group(process, signal.SIGTERM, 5)
        if drainer is not None:
            drainer.join(timeout=5)
        process.stdout.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    smoke = commands.add_parser("smoke")
    smoke.add_argument("--write-test", action="store_true")
    dataops = commands.add_parser("dataops")
    dataops.add_argument("--exercise-failure", action="store_true")
    benchmark = commands.add_parser("benchmark")
    benchmark.add_argument("--cleanup-run")
    args = parser.parse_args()
    try:
        with interruption_handlers():
            check_context()
            with trino_tunnel() as url:
                if args.command == "smoke":
                    command = [sys.executable, str(Path(__file__).with_name("local_trino_smoke.py")), "--url", url]
                    if args.write_test:
                        command.append("--write-test")
                elif args.command == "dataops":
                    project = Path(__file__).with_name("dataops")
                    python = project / ".venv" / "bin" / "python"
                    if not python.is_file():
                        raise ExperimentError("DataOps environment is missing; run make local-dataops-sync")
                    command = [str(python), str(project / "run.py"), "--url", url]
                    if args.exercise_failure:
                        command.append("--exercise-failure")
                else:
                    project = Path(__file__).with_name("benchmark")
                    command = [sys.executable, str(project / "run.py"), "--url", url,
                               "--scale-factor", "10", "--query-timeout", "900"]
                    if args.cleanup_run:
                        command.extend(["--cleanup-run", args.cleanup_run])
                return run_experiment(command, shutdown_timeout=330 if args.command == "benchmark" else 180)
    except SessionInterrupted as error:
        print(json.dumps({"result": "interrupted", "signal": error.signum}), file=sys.stderr)
        return 128 + error.signum
    except (ExperimentError, OSError) as error:
        print(json.dumps({"result": "failed", "error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
