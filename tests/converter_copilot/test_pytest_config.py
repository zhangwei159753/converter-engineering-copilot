"""Root-level discovery contracts for the inherited research test suite."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_legacy_live_test_collects_with_root_cli_option():
    """Keep legacy test collection visible when the upstream CLI option is passed."""
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", "--collect-only", "-q",
            "src/legacy/tests/test_report_quality.py", "--research-agent=multi_agent",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "test_report_quality" in result.stdout
