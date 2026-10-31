"""
ci_review.py — run the reviewer and print ONLY the review markdown.

Used by the GitHub Action so the review can be posted cleanly as a PR/commit
comment (retry/progress messages go to stderr, not stdout).

Pass "--pr owner/repo#number" so the agent inspects the PR's changed files
(get_pr_changes) and ties the failure to the actual change.

The printed review is sanitized for public posting (see sanitize.py): no
@mentions, images, raw HTML, or links outside github.com (add hosts with
REVIEW_ALLOWED_LINK_HOSTS), and secrets are redacted again.
"""

import re
import sys

from agent_runner import run_agent
from sanitize import sanitize_review

_PR_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}#[0-9]{1,20}$")
USAGE = "usage: ci_review.py [--pr owner/repo#number] <artifact> [<artifact> ...]"


def main() -> None:
    args = sys.argv[1:]
    pr_ref = None
    if "--pr" in args:
        i = args.index("--pr")
        if i + 1 >= len(args) or not _PR_REF.match(args[i + 1]):
            print(f"invalid --pr value; expected owner/repo#number\n{USAGE}", file=sys.stderr)
            sys.exit(2)
        pr_ref = args[i + 1]
        del args[i:i + 2]
    paths = args
    if not paths:
        print(USAGE, file=sys.stderr)
        sys.exit(2)
    state = run_agent(paths, pr_ref=pr_ref)
    print(sanitize_review(state.get("review", "_(no review produced)_")))


if __name__ == "__main__":
    main()
