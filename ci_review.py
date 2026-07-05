"""
ci_review.py — run the reviewer and print ONLY the review markdown.

Used by the GitHub Action so the review can be posted cleanly as a PR/commit
comment (retry/progress messages go to stderr, not stdout).

Pass "--pr owner/repo#number" so the agent inspects the PR's changed files
(get_pr_changes) and ties the failure to the actual change.
"""

import sys

from agent_runner import run_agent


def main() -> None:
    args = sys.argv[1:]
    pr_ref = None
    if "--pr" in args:
        i = args.index("--pr")
        pr_ref = args[i + 1]
        del args[i:i + 2]
    paths = args
    if not paths:
        print("usage: ci_review.py [--pr owner/repo#number] <artifact> [<artifact> ...]", file=sys.stderr)
        sys.exit(2)
    state = run_agent(paths, pr_ref=pr_ref)
    print(state.get("review", "_(no review produced)_"))


if __name__ == "__main__":
    main()
