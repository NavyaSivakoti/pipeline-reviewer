"""
Tool-trajectory checks: did the LLM call the RIGHT tools for each scenario?

These read the tool calls cached by run_eval.py (eval/reviews/<id>.tools.json),
so they cost nothing - but they need a real eval run first to populate the
cache. Without it, they skip (not fail).

  Populate:  python eval/run_eval.py
  Then:      pytest tests/test_trajectory.py
"""

import json
import os

import pytest

REVIEWS = os.path.join(os.path.dirname(__file__), "..", "eval", "reviews")


def _tool_calls(case_id: str) -> list:
    path = os.path.join(REVIEWS, f"{case_id}.tools.json")
    if not os.path.exists(path):
        pytest.skip("no cached tool calls; run `python eval/run_eval.py` first")
    return json.load(open(path))


def _names(case_id: str) -> list:
    return [c.get("name") for c in _tool_calls(case_id)]


def test_dependency_cases_call_check_package():
    for cid in ("gha_dependency", "maven_dependency"):
        assert "check_package" in _names(cid), f"{cid} did not call check_package"


def test_dependency_case_checks_the_offending_package():
    # deeper than 'called it' - it must pass the actual bad package name.
    args_blob = json.dumps(_tool_calls("gha_dependency")).lower()
    assert "reqests" in args_blob, "check_package was not called with the offending package"


def test_xml_case_calls_parse_junit():
    assert "parse_junit_results" in _names("payments_test")


def test_every_case_calls_recurrence():
    for cid in ("flaky_test", "lint_only", "docker_build"):
        assert "check_recurrence" in _names(cid), f"{cid} did not call check_recurrence"
