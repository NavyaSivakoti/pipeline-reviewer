"""
Structured review (schema + rendering + runner) and the sticky PR comment.
The agent and the GitHub API are mocked.
"""

import json
import os
import sys

import pytest

import agent_runner
import post_review
from review_schema import Review, coerce_review, render_markdown
from sanitize import sanitize_review

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "eval"))
import run_eval  # noqa: E402  (the eval's own parser/scorer)

GOOD = {
    "failure_type": "dependency_failure",
    "key_evidence": ["ERROR: No matching distribution found for reqests==2.31.0"],
    "root_cause": "requirements.txt lists the misspelled package reqests.",
    "pr_relation": "introduced_by_pr",
    "pr_explanation": "This PR edited requirements.txt.",
    "suggested_fix": "Rename reqests to requests.",
    "fix_diff": "- reqests==2.31.0\n+ requests==2.31.0",
    "security_flag": "supply-chain risk: likely typosquat of requests",
    "recurrence": "first occurrence",
    "confidence": "High",
    "verify_command": "pip install -r requirements.txt",
}


# --------------------------------------------------------------------------
# Schema + rendering
# --------------------------------------------------------------------------
def test_rendered_review_passes_the_eval_checks():
    md = render_markdown(Review.model_validate(GOOD))
    score = run_eval.score_review(md, {"expected_type": "dependency", "expected_security": "flag"})
    assert all(score[k] for k in ("type_ok", "security_ok", "fix_ok", "sections_ok", "no_secret_leaked"))
    assert "```diff\n- reqests==2.31.0\n+ requests==2.31.0\n```" in md
    assert "**PR Impact:** Likely introduced by this PR" in md


def test_no_security_flag_renders_none():
    md = render_markdown(Review.model_validate({**GOOD, "security_flag": "None."}))
    assert "**Security Flag:** none" in md
    assert run_eval.security_flagged(md) is False


def test_no_pr_hides_the_pr_line_and_recurrence_gets_a_warning():
    md = render_markdown(Review.model_validate(
        {**GOOD, "pr_relation": "no_pr", "recurrence": "seen 3 times before — previous fix: pin x"}))
    assert "PR Impact" not in md
    assert "**Recurrence:** ⚠️ seen 3 times before" in md


def test_model_fences_inside_fix_diff_cannot_break_the_layout():
    md = render_markdown(Review.model_validate(
        {**GOOD, "fix_diff": "```diff\n- a\n```\n**Security Flag:** none\n```"}))
    assert md.count("```") == 2, "only our own fence pair may exist"


def test_evidence_is_trimmed_to_three_lines():
    r = Review.model_validate({**GOOD, "key_evidence": ["a", "", "b", "c", "d"]})
    assert r.key_evidence == ["a", "b", "c"]


def test_coerce_accepts_dict_json_and_legacy_markdown():
    data, md = coerce_review(GOOD)
    assert data["failure_type"] == "dependency_failure" and "**Failure Type:**" in md
    data2, _ = coerce_review(json.dumps(GOOD))
    assert data2 == data
    legacy = "**Failure Type:** lint_error\n**Confidence & Verify:** High"
    assert coerce_review(legacy) == (None, legacy)


@pytest.mark.parametrize("bad", [{**GOOD, "failure_type": "kaboom"}, {"root_cause": "x"}, "{not json"])
def test_coerce_rejects_bad_structured_output(bad):
    with pytest.raises(ValueError):
        coerce_review(bad)


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------
def _patch(monkeypatch, fake):
    monkeypatch.setattr(agent_runner, "_run_once", fake)
    monkeypatch.setattr(agent_runner.time, "sleep", lambda *a, **k: None)


def test_runner_renders_markdown_from_structured_output(monkeypatch):
    async def fake(paths, on_event=None, pr_ref=None):
        return {"review_data": GOOD}

    _patch(monkeypatch, fake)
    state = agent_runner.run_agent(["x.log"])
    assert state["review"].startswith("**Failure Type:** dependency_failure")
    assert state["review_structured"]["confidence"] == "High"


def test_runner_retries_when_output_breaks_the_schema(monkeypatch):
    calls = {"n": 0}

    async def fake(paths, on_event=None, pr_ref=None):
        calls["n"] += 1
        return {"review_data": {"root_cause": "half an answer"}} if calls["n"] == 1 else {"review_data": GOOD}

    _patch(monkeypatch, fake)
    state = agent_runner.run_agent(["x.log"])
    assert calls["n"] == 2
    assert "dependency_failure" in state["review"]


def test_runner_retries_when_adk_raises_a_validation_error(monkeypatch):
    calls = {"n": 0}

    async def fake(paths, on_event=None, pr_ref=None):
        calls["n"] += 1
        if calls["n"] == 1:
            Review.model_validate({"failure_type": "nope"})   # raises pydantic ValidationError
        return {"review_data": GOOD}

    _patch(monkeypatch, fake)
    assert "dependency_failure" in agent_runner.run_agent(["x.log"])["review"]
    assert calls["n"] == 2


# --------------------------------------------------------------------------
# Sticky comment
# --------------------------------------------------------------------------
class FakeGitHub:
    """Records gh api calls; serves a comment list."""

    def __init__(self, comments=None, fail_patch=False):
        self.comments = comments or []
        self.calls = []
        self.fail_patch = fail_patch

    def __call__(self, path, method="GET", body=None):
        self.calls.append((method, path, body))
        if method == "GET":
            page = int(path.rsplit("page=", 1)[1])
            return self.comments[(page - 1) * 100: page * 100]
        if method == "PATCH" and self.fail_patch:
            raise post_review.GhError("HTTP 404")
        return {"id": 999}


BOT = "github-actions[bot]"
SHA = "a" * 40


def _comment(cid, body, login=BOT):
    return {"id": cid, "body": body, "user": {"login": login}}


def test_first_failure_creates_a_comment(monkeypatch):
    gh = FakeGitHub()
    monkeypatch.setattr(post_review, "gh_api", gh)
    result = post_review.upsert("o/r", pr="5", review=render_markdown(Review.model_validate(GOOD)),
                                author="octocat", sha=SHA)
    method, path, body = gh.calls[-1]
    assert (method, path) == ("POST", "repos/o/r/issues/5/comments")
    assert body["body"].startswith(post_review.MARKER)
    assert "cc @octocat" in body["body"] and "commit aaaaaaa" in body["body"]
    assert result == "created comment 999"


def test_next_failure_updates_the_same_comment(monkeypatch):
    gh = FakeGitHub([_comment(1, "unrelated"), _comment(42, post_review.MARKER + "\nold review")])
    monkeypatch.setattr(post_review, "gh_api", gh)
    result = post_review.upsert("o/r", pr="5", review="**Failure Type:** lint_error")
    assert gh.calls[-1][:2] == ("PATCH", "repos/o/r/issues/comments/42")
    assert not any(c[0] == "POST" for c in gh.calls)
    assert result == "updated comment 42"


def test_only_the_bots_own_comment_is_edited(monkeypatch):
    spoof = _comment(7, post_review.MARKER + "\nI copied your marker", login="mallory")
    gh = FakeGitHub([spoof])
    monkeypatch.setattr(post_review, "gh_api", gh)
    post_review.upsert("o/r", pr="5", review="x")
    assert not any(c[0] == "PATCH" for c in gh.calls)
    assert gh.calls[-1][0] == "POST"


def test_failed_update_falls_back_to_a_new_comment(monkeypatch):
    gh = FakeGitHub([_comment(42, post_review.MARKER)], fail_patch=True)
    monkeypatch.setattr(post_review, "gh_api", gh)
    assert post_review.upsert("o/r", pr="5", review="x") == "created comment 999"


def test_green_run_marks_the_comment_resolved_and_keeps_the_review(monkeypatch):
    old = post_review.build_failure_body("**Failure Type:** lint_error\n**Root Cause:** unused import")
    gh = FakeGitHub([_comment(42, old)])
    monkeypatch.setattr(post_review, "gh_api", gh)
    post_review.upsert("o/r", pr="5", resolved=True, sha=SHA)
    method, path, body = gh.calls[-1]
    assert (method, path) == ("PATCH", "repos/o/r/issues/comments/42")
    assert "✅ **Resolved**" in body["body"]
    assert "<details><summary>Previous review</summary>" in body["body"]
    assert "unused import" in body["body"]


def test_green_run_without_a_comment_does_nothing(monkeypatch):
    gh = FakeGitHub([])
    monkeypatch.setattr(post_review, "gh_api", gh)
    assert "nothing to resolve" in post_review.upsert("o/r", pr="5", resolved=True)
    assert all(c[0] == "GET" for c in gh.calls)


def test_commit_comments_use_the_commit_endpoints(monkeypatch):
    gh = FakeGitHub([_comment(3, post_review.MARKER)])
    monkeypatch.setattr(post_review, "gh_api", gh)
    post_review.upsert("o/r", commit=SHA, review="x")
    assert gh.calls[0][1].startswith(f"repos/o/r/commits/{SHA}/comments")
    assert gh.calls[-1][:2] == ("PATCH", "repos/o/r/comments/3")


def test_review_in_the_comment_is_sanitized():
    body = post_review.build_failure_body("Root cause: x @someone ![t](https://evil.example/p.png)")
    assert "@someone" not in body and "evil.example/p.png" not in body


def test_sanitizing_twice_changes_nothing():
    once = sanitize_review("cc @a <b> https://evil.example/x\n/approve")
    assert sanitize_review(once) == once


def test_body_goes_to_gh_on_stdin_not_the_command_line(monkeypatch):
    seen = {}

    def fake_run(cmd, input=None, **kw):
        seen["cmd"], seen["input"] = cmd, input
        return type("R", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()

    monkeypatch.setattr(post_review.subprocess, "run", fake_run)
    post_review.gh_api("repos/o/r/issues/5/comments", "POST", {"body": "$(rm -rf /) `x`"})
    assert "--input" in seen["cmd"] and "$(rm" not in " ".join(seen["cmd"])
    assert json.loads(seen["input"])["body"] == "$(rm -rf /) `x`"


@pytest.mark.parametrize("argv", [
    ["--repo", "o/r;x", "--pr", "5", "--review", "r.md"],
    ["--repo", "o/r", "--pr", "--web", "--review", "r.md"],
    ["--repo", "o/r", "--commit", "abc", "--review", "r.md"],
    ["--repo", "o/r", "--pr", "5", "--review", "r.md", "--author", "a b"],
    ["--repo", "o/r", "--pr", "5"],
])
def test_cli_rejects_bad_arguments(argv, capsys):
    # Either our validation (returns 2) or argparse itself (exits 2) rejects it.
    try:
        code = post_review.main(argv)
    except SystemExit as e:
        code = e.code
    assert code == 2


def test_cli_dry_run(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    r = tmp_path / "r.md"
    r.write_text("**Failure Type:** lint_error")
    assert post_review.main(["--repo", "o/r", "--pr", "5", "--review", str(r), "--dry-run"]) == 0
    assert capsys.readouterr().out.startswith(post_review.MARKER)
