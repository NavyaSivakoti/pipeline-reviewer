"""
End-to-end smoke test of the CLI entry point (run.py). This actually invokes
the Gemini agent, so it costs API calls and is OFF by default - it only runs
when RUN_LIVE=1 is set, so CI (which has no key/quota) skips it.

  RUN_LIVE=1 python -m pytest tests/test_smoke.py
"""

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.live
@pytest.mark.skipif(os.getenv("RUN_LIVE") != "1", reason="calls the real Gemini API; set RUN_LIVE=1 to run")
def test_cli_produces_a_review():
    out = subprocess.run(
        [sys.executable, "run.py", "sample_data/github_actions_failure.log"],
        cwd=ROOT, capture_output=True, text=True, timeout=240,
    )
    assert out.returncode == 0, out.stderr[-500:]
    # print_report writes the review (with its sections) to stdout
    assert "Failure Type" in out.stdout, out.stdout[-500:]
