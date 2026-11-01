"""
Sticky comments in real CI: one comment per matrix job (--key), and stale runs
(the PR head moved on) never overwrite a newer review.
"""

import pytest

import post_review

BOT = "github-actions[bot]"
HEAD = "a" * 40
OLD = "b" * 40


class FakeGitHub:
    """Serves a PR (head SHA) and its comments; records writes."""

    def __init__(self, comments=None, head=HEAD, pulls_fail=False):
        self.comments = comments or []
        self.head, self.pulls_fail = head, pulls_fail
        self.calls = []

    def __call__(self, path, method="GET", body=None):
        self.calls.append((method, path, body))
        if method == "GET" and "/pulls/" in path:
            if self.pulls_fail:
                raise post_review.GhError("HTTP 502")
            return {"head": {"sha": self.head}}
        if method == "GET":
            return self.comments if path.endswith("page=1") else []
        if method == "POST":
            new = {"id": 100 + len(self.comments), "user": {"login": BOT}, "body": body["body"]}
            self.comments.append(new)
            return {"id": new["id"]}
        if method == "PATCH":
            cid = int(path.rsplit("/", 1)[1])
            next(c for c in self.comments if c["id"] == cid)["body"] = body["body"]
            return {}

    def writes(self):
        return [c for c in self.calls if c[0] in ("POST", "PATCH")]


# --------------------------------------------------------------------------
# --key: one comment per matrix job
# --------------------------------------------------------------------------
def test_each_key_gets_its_own_comment(monkeypatch):
    gh = FakeGitHub()
    monkeypatch.setattr(post_review, "gh_api", gh)
    post_review.upsert("o/r", pr="1", review="py310 review", key="test (3.10)")
    post_review.upsert("o/r", pr="1", review="py311 review", key="test (3.11)")
    post_review.upsert("o/r", pr="1", review="py310 again", key="test (3.10)")
    assert len(gh.comments) == 2
    bodies = {c["body"].split("\n", 1)[0]: c["body"] for c in gh.comments}
    assert "py310 again" in bodies["<!-- pipeline-reviewer:sticky key=test (3.10) -->"]
    assert "py311 review" in bodies["<!-- pipeline-reviewer:sticky key=test (3.11) -->"]
    assert "## 🤖 AI Pipeline Reviewer — test (3.11)" in bodies["<!-- pipeline-reviewer:sticky key=test (3.11) -->"]


def test_keyed_and_default_comments_never_mix(monkeypatch):
    gh = FakeGitHub([{"id": 1, "user": {"login": BOT}, "body": post_review.build_failure_body("default")}])
    monkeypatch.setattr(post_review, "gh_api", gh)
    post_review.upsert("o/r", pr="1", review="keyed", key="lint")
    assert gh.writes()[-1][0] == "POST", "a keyed post must not edit the default comment"
    post_review.upsert("o/r", pr="1", review="default again")
    assert gh.writes()[-1][:2] == ("PATCH", "repos/o/r/issues/comments/1")


def test_a_key_that_is_a_prefix_of_another_does_not_match(monkeypatch):
    gh = FakeGitHub([{"id": 1, "user": {"login": BOT},
                      "body": post_review.build_failure_body("x", key="test (3.11)")}])
    monkeypatch.setattr(post_review, "gh_api", gh)
    post_review.upsert("o/r", pr="1", review="y", key="test")
    assert gh.writes()[-1][0] == "POST"


def test_resolved_only_touches_its_own_key(monkeypatch):
    gh = FakeGitHub([
        {"id": 1, "user": {"login": BOT}, "body": post_review.build_failure_body("a", key="unit")},
        {"id": 2, "user": {"login": BOT}, "body": post_review.build_failure_body("b", key="e2e")},
    ])
    monkeypatch.setattr(post_review, "gh_api", gh)
    post_review.upsert("o/r", pr="1", resolved=True, key="unit")
    assert gh.writes() == [gh.writes()[0]] and gh.writes()[0][1].endswith("/comments/1")
    assert "Resolved" in gh.comments[0]["body"] and "Resolved" not in gh.comments[1]["body"]


@pytest.mark.parametrize("bad", ["", "x-->y", "a<b", "a>b", "`code`", "a\nb", "-leading", "x" * 101])
def test_unsafe_keys_are_rejected(bad, monkeypatch):
    monkeypatch.setattr(post_review, "gh_api", FakeGitHub())
    assert not post_review.valid_key(bad)
    with pytest.raises(ValueError):
        post_review.upsert("o/r", pr="1", review="x", key=bad)


def test_cli_rejects_a_bad_key(tmp_path):
    r = tmp_path / "r.md"
    r.write_text("x")
    assert post_review.main(["--repo", "o/r", "--pr", "1", "--review", str(r), "--key", "a-->b"]) == 2


# --------------------------------------------------------------------------
# Stale runs
# --------------------------------------------------------------------------
def test_stale_run_does_not_post(monkeypatch):
    gh = FakeGitHub([{"id": 1, "user": {"login": BOT}, "body": post_review.build_failure_body("new")}],
                    head=HEAD)
    monkeypatch.setattr(post_review, "gh_api", gh)
    result = post_review.upsert("o/r", pr="1", review="old review", sha=OLD, expected_head=OLD)
    assert result.startswith("skipped: stale run")
    assert gh.writes() == [] and "new" in gh.comments[0]["body"]


def test_stale_resolve_does_not_flip_the_comment(monkeypatch):
    gh = FakeGitHub([{"id": 1, "user": {"login": BOT}, "body": post_review.build_failure_body("new")}])
    monkeypatch.setattr(post_review, "gh_api", gh)
    assert post_review.upsert("o/r", pr="1", resolved=True, expected_head=OLD).startswith("skipped")
    assert gh.writes() == []


def test_current_run_posts(monkeypatch):
    gh = FakeGitHub()
    monkeypatch.setattr(post_review, "gh_api", gh)
    assert post_review.upsert("o/r", pr="1", review="r", sha=HEAD, expected_head=HEAD.upper()
                              ).startswith("created")


def test_unreadable_pr_head_does_not_block_posting(monkeypatch):
    gh = FakeGitHub(pulls_fail=True)
    monkeypatch.setattr(post_review, "gh_api", gh)
    assert post_review.upsert("o/r", pr="1", review="r", expected_head=OLD).startswith("created")


def test_cli_uses_only_an_explicit_sha_for_the_stale_check(tmp_path, monkeypatch):
    # On pull_request events GITHUB_SHA is a temporary merge commit, never the
    # PR head; using it for the check would skip every post.
    monkeypatch.setenv("GITHUB_SHA", OLD)
    gh = FakeGitHub(head=HEAD)
    monkeypatch.setattr(post_review, "gh_api", gh)
    r = tmp_path / "r.md"
    r.write_text("**Failure Type:** lint_error")
    assert post_review.main(["--repo", "o/r", "--pr", "1", "--review", str(r)]) == 0
    assert any(c[0] == "POST" for c in gh.calls)
    assert not any("/pulls/" in c[1] for c in gh.calls), "no stale check without --sha"


def test_cli_stale_check_with_explicit_sha(tmp_path, monkeypatch):
    gh = FakeGitHub(head=HEAD)
    monkeypatch.setattr(post_review, "gh_api", gh)
    r = tmp_path / "r.md"
    r.write_text("x")
    assert post_review.main(["--repo", "o/r", "--pr", "1", "--review", str(r), "--sha", OLD]) == 0
    assert gh.writes() == []


def test_commit_comments_skip_the_pr_check(monkeypatch):
    gh = FakeGitHub()
    monkeypatch.setattr(post_review, "gh_api", gh)
    post_review.upsert("o/r", commit=OLD, review="r", sha=OLD)
    assert not any("/pulls/" in c[1] for c in gh.calls)
