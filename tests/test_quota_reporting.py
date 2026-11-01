"""
When the model's daily quota runs out, say so clearly: the eval stops at once
with its own exit code instead of scoring every case 0, and CI gets a skip
reason (step output + run summary) instead of a silent placeholder.
"""

import os
import sys

import pytest

import agent_runner
import ci_review

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "eval"))
import run_eval  # noqa: E402


# --------------------------------------------------------------------------
# Eval
# --------------------------------------------------------------------------
def test_get_review_raises_on_daily_quota(monkeypatch, tmp_path):
    monkeypatch.setattr(run_eval, "REVIEWS_DIR", str(tmp_path))
    monkeypatch.setattr(agent_runner, "run_agent",
                        lambda *a, **k: {"review": agent_runner._QUOTA_FALLBACK, "review_error": "daily_quota"})
    with pytest.raises(run_eval.QuotaExhausted):
        run_eval.get_review({"id": "case1", "artifacts": ["x.log"]}, cached=False)
    assert os.listdir(tmp_path) == [], "a quota notice must never be cached as a review"


def test_eval_stops_at_the_first_quota_error_with_exit_code_3(monkeypatch, tmp_path, capsys):
    results = tmp_path / "results.md"
    monkeypatch.setattr(run_eval, "RESULTS_MD", str(results))
    monkeypatch.setattr(run_eval, "_reset_memory_for_hermetic_eval", lambda: None)
    monkeypatch.setattr(run_eval.time, "sleep", lambda s: None)
    calls = []

    def fake_get_review(case, cached):
        calls.append(case["id"])
        if len(calls) == 2:
            raise run_eval.QuotaExhausted(case["id"])
        return "**Failure Type:** dependency_failure\n**Confidence & Verify:** High", []

    monkeypatch.setattr(run_eval, "get_review", fake_get_review)
    monkeypatch.setattr(sys, "argv", ["run_eval.py", "--tag", "pr_gate"])
    with pytest.raises(SystemExit) as exc:
        run_eval.main()
    assert exc.value.code == run_eval.QUOTA_EXIT_CODE == 3
    assert len(calls) == 2, "no cases are attempted after the quota runs out"
    text = results.read_text()
    assert "daily quota is used up" in text and "after 1 of 5 cases" in text
    assert "not" in text.lower() and "quality" in text.lower()


def test_rate_limit_notices_are_not_treated_as_quota(monkeypatch, tmp_path):
    monkeypatch.setattr(run_eval, "REVIEWS_DIR", str(tmp_path))
    monkeypatch.setattr(agent_runner, "run_agent",
                        lambda *a, **k: {"review": agent_runner._FALLBACK, "review_error": "incomplete"})
    review, _ = run_eval.get_review({"id": "c", "artifacts": ["x.log"]}, cached=False)
    assert review.startswith("_The AI Pipeline Reviewer could not produce")


def test_eval_workflow_explains_exit_code_3():
    text = open(os.path.join(ROOT, ".github/workflows/eval.yml")).read()
    assert "3) echo \"::error title=Gemini daily quota used up::" in text


# --------------------------------------------------------------------------
# CI (ci_review.py)
# --------------------------------------------------------------------------
@pytest.fixture
def gh_files(tmp_path, monkeypatch):
    out, summary = tmp_path / "out", tmp_path / "summary"
    out.write_text("")
    summary.write_text("")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    return out, summary


def test_skip_reason_goes_to_step_output_and_summary(gh_files, capsys):
    out, summary = gh_files
    ci_review.report_skip("daily_quota")
    assert out.read_text() == "review_error=daily_quota\n"
    assert "AI review skipped" in summary.read_text() and "daily quota" in summary.read_text()
    assert capsys.readouterr().out == "", "stdout is the review; nothing else may go there"


def test_successful_review_sets_an_empty_reason(gh_files):
    out, summary = gh_files
    ci_review.report_skip(None)
    assert out.read_text() == "review_error=\n" and summary.read_text() == ""


def test_unexpected_codes_are_not_echoed_into_outputs(gh_files):
    out, _ = gh_files
    ci_review.report_skip("evil\nmalicious=1")
    assert out.read_text() == "review_error=unknown\n"


def test_outside_ci_nothing_is_written(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    ci_review.report_skip("daily_quota")
    assert "AI review skipped" in capsys.readouterr().err
