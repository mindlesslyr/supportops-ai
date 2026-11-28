import os
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run-n8n-contract-test.sh"


@pytest.mark.parametrize(("signal", "expected_code"), [("INT", 130), ("TERM", 143)])
def test_contract_runner_preserves_signal_exit_and_cleans_once(
    tmp_path, signal, expected_code
):
    script = RUNNER.read_text()
    preamble = script.split('cd "$ROOT_DIR"', 1)[0]
    preamble = preamble.replace(
        'ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)',
        f"ROOT_DIR={shlex.quote(str(ROOT))}",
    )
    marker = tmp_path / "cleanup-count"
    probe = f"""
{preamble}
compose() {{ printf x >> "$MARKER"; }}
kill -{signal} $$
"""

    result = subprocess.run(
        ["bash", "-c", probe],
        env={
            **os.environ,
            "KEEP_CONTRACT_STACK": "0",
            "MARKER": str(marker),
        },
        check=False,
        timeout=10,
    )

    assert result.returncode == expected_code
    assert marker.read_text() == "x"
