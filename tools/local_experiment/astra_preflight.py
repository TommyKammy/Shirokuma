"""Zero-model-request, fixed-output gate for the optional Astra consultant."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

from tools.local_experiment import autonomous_runner as runner


MODEL = 'openai/gpt-6-astra'


def check(binary: str, root: Path) -> None:
    runner.subscription_preflight(binary, MODEL, root)
    try:
        result = subprocess.run([binary, 'models'], cwd=root, capture_output=True,
                                timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise runner.RunnerError('ASTRA_MODEL_UNVERIFIED') from None
    runner.require(result.returncode == 0 and len(result.stdout) <= 65536 and
                   MODEL.encode() in result.stdout.splitlines(), 'ASTRA_MODEL_UNVERIFIED')


def main() -> int:
    binary = shutil.which('opencode')
    if binary is None:
        print('ASTRA_CONSULT_UNAVAILABLE')
        return 1
    try:
        check(binary, runner.ROOT)
    except runner.RunnerError:
        print('ASTRA_CONSULT_UNAVAILABLE')
        return 1
    print('ASTRA_CONSULT_PREFLIGHT_OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
