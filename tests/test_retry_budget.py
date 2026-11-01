"""
Retry behaviour in CI: stop at once on an exhausted DAILY quota, cap the total
time spent waiting, never crash instead of posting, and never overwrite a good
review in the sticky comment with a "couldn't review" notice.
"""

import os
import subprocess
import sys

import pytest

import agent_runner
import post_review
from notices import NO_REVIEW_PREFIX, is_no_review

GOOD = {"review": "**Failure Type:** test_failure\n**Confidence & Verify:** High"}
DAILY = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota', "
         "'details': [{'violations': [{'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', "
         "'quotaValue': '20'}]}, {'retryDelay': '41s'}]}}")
PER_MINUTE = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'details': [{'violations': [{'quotaId': "
              "'GenerateRequestsPerMinutePerProjectPerModel-FreeTier'}]}, {'retryDelay': '20s'}]}}")


@pytest.fixture
def sleeps(monkeypatch):
    slept = []
    monkeypatch.setattr(agent_runner.time, "sleep", lambda s: slept.append(s))
    return slept


def _fake(monkeypatch, outcomes):
    """outcomes: a list of exceptions (raised) or dicts (returned), in order."""
    calls = {"n": 0}

    async def fake(paths, on_event=None, pr_ref=None):
        out = outcomes[min(calls["n"], len(outcomes) - 1)]
        calls["n"] += 1
        if isinstance(out, Exception):
            raise out
        return dict(out)

    monkeypatch.setattr(agent_runner, "_run_once", fake)
    return calls


def test_daily_quota_stops_immediately(monkeypatch, sleeps):
    calls = _fake(monkeypatch, [RuntimeError(DAILY)])
    state = agent_runner.run_agent(["x.log"])
    assert calls["n"] == 1 and sleeps == [], "no retries against a daily quota"
    assert state["review_error"] == "daily_quota"
    assert is_no_review(state["review"]) and "daily quota" in state["review"]


def test_per_minute_rate_limit_is_still_retried(monkeypatch, sleeps):
    calls = _fake(monkeypatch, [RuntimeError(PER_MINUTE), GOOD])
    state = agent_runner.run_agent(["x.log"])
    assert calls["n"] == 2 and sleeps == [22.0]
    assert "Failure Type" in state["review"]


def test_total_wait_is_capped(monkeypatch, sleeps):
    monkeypatch.setattr(agent_runner, "MAX_RETRY_WAIT", 30.0)
    calls = _fake(monkeypatch, [RuntimeError("429 rate limited, retry in 20s")] * 10)
    state = agent_runner.run_agent(["x.log"])
    assert sum(sleeps) <= 30.0
    assert calls["n"] == 2, "second wait would exceed the budget"
    assert state["review_error"] == "retries_exhausted"
    assert "30s retry budget" in state["review"]


def test_running_out_of_retries_returns_a_notice_instead_of_crashing(monkeypatch, sleeps):
    _fake(monkeypatch, [RuntimeError("503 UNAVAILABLE: overloaded")] * 10)
    state = agent_runner.run_agent(["x.log"], max_retries=3)
    assert is_no_review(state["review"]), "CI must get something to post"


def test_schema_errors_end_in_a_notice(monkeypatch, sleeps):
    _fake(monkeypatch, [{"review_data": {"root_cause": "half"}}] * 10)
    state = agent_runner.run_agent(["x.log"], max_retries=2)
    assert state["review_error"] == "incomplete"
    assert is_no_review(state["review"])


def test_budget_comes_from_the_environment(monkeypatch):
    import importlib
    monkeypatch.setenv("REVIEW_MAX_RETRY_WAIT", "45")
    try:
        importlib.reload(agent_runner)
        assert agent_runner.MAX_RETRY_WAIT == 45.0
    finally:
        monkeypatch.delenv("REVIEW_MAX_RETRY_WAIT")
        importlib.reload(agent_runner)


# --------------------------------------------------------------------------
# The sticky comment keeps the last real review
# --------------------------------------------------------------------------
class FakeGitHub:
    def __init__(self, comments):
        self.comments, self.calls = comments, []

    def __call__(self, path, method="GET", body=None):
        self.calls.append((method, path, body))
        if method == "GET":
            return self.comments if path.endswith("page=1") else []
        return {"id": 1}


def _sticky(review):
    return {"id": 9, "user": {"login": "github-actions[bot]"},
            "body": post_review.build_failure_body(review)}


NOTICE = agent_runner._QUOTA_FALLBACK
REAL = "**Failure Type:** lint_error\n\n**Root Cause:** unused import os"


def test_notice_keeps_the_previous_review(monkeypatch):
    gh = FakeGitHub([_sticky(REAL)])
    monkeypatch.setattr(post_review, "gh_api", gh)
    post_review.upsert("o/r", pr="3", review=NOTICE, author="octocat", sha="b" * 40)
    method, path, body = gh.calls[-1]
    assert method == "PATCH"
    assert "No new review for commit bbbbbbb" in body["body"]
    assert "daily quota" in body["body"]
    assert "unused import os" in body["body"], "the last real review must survive"
    assert post_review.extract_review(body["body"]).startswith("**Failure Type:** lint_error")


def test_kept_review_survives_a_second_notice(monkeypatch):
    first = post_review.build_kept_review_body(post_review.build_failure_body(REAL), NOTICE)
    gh = FakeGitHub([{"id": 9, "user": {"login": "github-actions[bot]"}, "body": first}])
    monkeypatch.setattr(post_review, "gh_api", gh)
    post_review.upsert("o/r", pr="3", review=NOTICE)
    assert "unused import os" in gh.calls[-1][2]["body"]


def test_notice_without_a_previous_review_is_posted(monkeypatch):
    gh = FakeGitHub([])
    monkeypatch.setattr(post_review, "gh_api", gh)
    post_review.upsert("o/r", pr="3", review=NOTICE)
    method, _, body = gh.calls[-1]
    assert method == "POST" and "daily quota" in body["body"]


def test_a_real_review_replaces_a_kept_one(monkeypatch):
    kept = post_review.build_kept_review_body(post_review.build_failure_body(REAL), NOTICE)
    gh = FakeGitHub([{"id": 9, "user": {"login": "github-actions[bot]"}, "body": kept}])
    monkeypatch.setattr(post_review, "gh_api", gh)
    post_review.upsert("o/r", pr="3", review="**Failure Type:** build_error\n\n**Root Cause:** new")
    body = gh.calls[-1][2]["body"]
    assert "No new review" not in body and "**Root Cause:** new" in body


def test_all_fallbacks_are_recognised_as_notices():
    for text in (agent_runner._FALLBACK, agent_runner._QUOTA_FALLBACK, agent_runner._budget_fallback(300)):
        assert text.startswith(NO_REVIEW_PREFIX) and is_no_review(text)
    assert not is_no_review(REAL)


def test_post_review_imports_without_the_agent_dependencies():
    # The "mark resolved" CI step runs post_review.py without installing the
    # reviewer's requirements, so it must only need the standard library.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = ("import sys; [sys.modules.__setitem__(m, None) for m in "
            "('pydantic', 'google', 'google.adk', 'dotenv')]; import post_review; print('ok')")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stderr
