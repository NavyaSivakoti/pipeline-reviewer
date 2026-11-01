"""
post_review.py — post the review as ONE comment that is updated on every run.

Instead of adding a new comment for each failed run, the reviewer keeps a single
"sticky" comment per PR (or per commit) and edits it in place. When CI goes
green again, `--resolved` flips that comment to "✅ Resolved" and keeps the last
review collapsed underneath. Uses the gh CLI with GH_TOKEN.

Usage (in GitHub Actions):
    # CI failed
    python post_review.py --repo "$REPO" --pr "$PR_NUMBER" --review review.md --author "$PR_AUTHOR"
    python post_review.py --repo "$REPO" --commit "$GITHUB_SHA" --review review.md
    # CI passed: mark the existing comment resolved (no-op if there is none)
    python post_review.py --repo "$REPO" --pr "$PR_NUMBER" --resolved

Only comments that carry the hidden marker AND were written by the bot account
(--bot-login, default github-actions[bot]) are ever edited, so the script can't
be pointed at someone else's comment by copying the marker.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys

from sanitize import sanitize_review

MARKER = "<!-- pipeline-reviewer:sticky -->"
REVIEW_START = "<!-- pipeline-reviewer:review-start -->"
REVIEW_END = "<!-- pipeline-reviewer:review-end -->"
HEADER = "## 🤖 AI Pipeline Reviewer"
MAX_PAGES = 30

_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
_NUMBER = re.compile(r"^[0-9]{1,20}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?$")


class GhError(RuntimeError):
    pass


def gh_api(path: str, method: str = "GET", body: dict = None):
    """Call the GitHub REST API through gh. The body goes in on stdin as JSON,
    so review text never touches a command line or a shell."""
    cmd = ["gh", "api", "-X", method, "-H", "Accept: application/vnd.github+json", path]
    if body is not None:
        cmd += ["--input", "-"]
    r = subprocess.run(cmd, input=json.dumps(body) if body is not None else None,
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise GhError(f"gh api {method} {path} failed: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout) if r.stdout.strip() else None


# --------------------------------------------------------------------------
# Where the comment lives
# --------------------------------------------------------------------------
def _target_paths(repo: str, pr: str = None, commit: str = None) -> dict:
    if pr:
        return {"list": f"repos/{repo}/issues/{pr}/comments",
                "create": f"repos/{repo}/issues/{pr}/comments",
                "update": f"repos/{repo}/issues/comments/{{id}}"}
    return {"list": f"repos/{repo}/commits/{commit}/comments",
            "create": f"repos/{repo}/commits/{commit}/comments",
            "update": f"repos/{repo}/comments/{{id}}"}


def find_sticky(list_path: str, bot_login: str):
    """The most recent comment that has our marker and was written by the bot."""
    found = None
    for page in range(1, MAX_PAGES + 1):
        batch = gh_api(f"{list_path}?per_page=100&page={page}") or []
        for c in batch:
            user = (c.get("user") or {}).get("login", "")
            if (c.get("body") or "").startswith(MARKER) and user.lower() == bot_login.lower():
                found = c
        if len(batch) < 100:
            break
    return found


# --------------------------------------------------------------------------
# Comment bodies
# --------------------------------------------------------------------------
def _footer(sha: str = None, run_url: str = None) -> str:
    when = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts = [f"Updated {when}"]
    if sha:
        parts.append(f"commit {sha[:7]}")
    if run_url:
        parts.append(f"[workflow run]({run_url})")
    return "<sub>" + " · ".join(parts) + "</sub>"


def build_failure_body(review: str, author: str = None, sha: str = None, run_url: str = None) -> str:
    intro = (f"cc @{author} — CI failed on your PR. Here's the review:" if author
             else "CI failed. Here's the review:")
    return "\n\n".join([MARKER, HEADER, intro,
                        f"{REVIEW_START}\n{sanitize_review(review)}\n{REVIEW_END}",
                        _footer(sha, run_url)])


def extract_review(body: str) -> str:
    start, end = body.find(REVIEW_START), body.find(REVIEW_END)
    if start == -1 or end == -1 or end < start:
        return ""
    return body[start + len(REVIEW_START):end].strip()


def build_resolved_body(previous_body: str, sha: str = None, run_url: str = None) -> str:
    when = f" on commit {sha[:7]}" if sha else ""
    run = f" ([workflow run]({run_url}))" if run_url else ""
    parts = [MARKER, HEADER, f"✅ **Resolved** — CI passed{when}{run}."]
    previous = extract_review(previous_body)
    if previous:
        parts.append("<details><summary>Previous review</summary>\n\n"
                     f"{REVIEW_START}\n{sanitize_review(previous)}\n{REVIEW_END}\n\n</details>")
    parts.append(_footer(sha, None))
    return "\n\n".join(parts)


def default_run_url():
    """Link to this workflow run, built from the Actions environment."""
    server = os.environ.get("GITHUB_SERVER_URL", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if re.match(r"^https://[A-Za-z0-9.-]+$", server) and _REPO.match(repo) and _NUMBER.match(run_id):
        return f"{server}/{repo}/actions/runs/{run_id}"
    return None


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def upsert(repo, pr=None, commit=None, review=None, resolved=False, author=None,
           sha=None, run_url=None, bot_login="github-actions[bot]", dry_run=False) -> str:
    """Create or update the sticky comment. Returns what it did."""
    paths = _target_paths(repo, pr, commit)
    existing = None if (dry_run and not resolved) else find_sticky(paths["list"], bot_login)

    if resolved:
        if not existing:
            return "nothing to resolve (no reviewer comment found)"
        body = build_resolved_body(existing.get("body") or "", sha, run_url)
    else:
        body = build_failure_body(review or "", author, sha, run_url)

    if dry_run:
        print(body)
        return "dry run: printed the comment body"
    if existing:
        try:
            gh_api(paths["update"].format(id=int(existing["id"])), "PATCH", {"body": body})
            return f"updated comment {existing['id']}"
        except GhError as err:
            if resolved:
                raise
            print(f"warning: could not update comment {existing['id']} ({err}); posting a new one",
                  file=sys.stderr)
    created = gh_api(paths["create"], "POST", {"body": body}) or {}
    return f"created comment {created.get('id', '?')}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Post or update the AI Pipeline Reviewer comment.")
    ap.add_argument("--repo", required=True, help="owner/repo")
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument("--pr", help="pull request number")
    where.add_argument("--commit", help="full 40-char commit SHA (when there is no PR)")
    ap.add_argument("--review", help="markdown file written by ci_review.py")
    ap.add_argument("--resolved", action="store_true", help="CI passed: mark the comment resolved")
    ap.add_argument("--author", help="PR author login to cc (take it from the PR payload)")
    ap.add_argument("--sha", help="commit SHA shown in the footer (defaults to GITHUB_SHA / --commit)")
    ap.add_argument("--bot-login", default="github-actions[bot]",
                    help="account whose comments may be edited (default: github-actions[bot])")
    ap.add_argument("--dry-run", action="store_true", help="print the comment instead of posting")
    a = ap.parse_args(argv)

    problems = []
    if not _REPO.match(a.repo):
        problems.append("--repo must be owner/repo")
    if a.pr and not _NUMBER.match(a.pr):
        problems.append("--pr must be a number")
    if a.commit and not _SHA.match(a.commit):
        problems.append("--commit must be a full 40-character SHA")
    if a.author and not _LOGIN.match(a.author):
        problems.append("--author is not a valid GitHub login")
    if not _LOGIN.match(a.bot_login):
        problems.append("--bot-login is not a valid GitHub login")
    sha = a.sha or a.commit or os.environ.get("GITHUB_SHA") or None
    if sha and not _SHA.match(sha):
        problems.append("--sha must be a full 40-character SHA")
    review = None
    if not a.resolved:
        if not a.review:
            problems.append("--review is required unless --resolved")
        else:
            try:
                with open(a.review, encoding="utf-8", errors="replace") as f:
                    review = f.read(200_000)
            except OSError as err:
                problems.append(f"cannot read --review: {err}")
    if problems:
        print("post_review.py: " + "; ".join(problems), file=sys.stderr)
        return 2

    try:
        result = upsert(a.repo, a.pr, a.commit, review, a.resolved, a.author, sha,
                        default_run_url(), a.bot_login, a.dry_run)
    except (GhError, FileNotFoundError, subprocess.TimeoutExpired) as err:
        print(f"post_review.py: {err}", file=sys.stderr)
        return 1
    print(result, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
