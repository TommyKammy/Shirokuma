"""Bounded reading of the fixed LOCAL deployment projections."""
import selectors
import subprocess
import time


def read_fixed_deployments(kube_argv, jsonpath, *, max_stdout_bytes) -> bytes:
    deadline = time.monotonic() + 8
    process = subprocess.Popen(kube_argv + ['get', 'deployments', 'trino-local',
        'trino-local-worker', '-o', jsonpath],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False)
    raw = b''
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise subprocess.TimeoutExpired(kube_argv, 8)
                chunk = process.stdout.read1(max_stdout_bytes + 1 - len(raw))
                if not chunk:
                    break
                raw += chunk
                if len(raw) > max_stdout_bytes:
                    raise ValueError()
        remaining = deadline - time.monotonic()
        if remaining <= 0 or process.wait(timeout=remaining) != 0:
            raise ValueError()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
    return raw
