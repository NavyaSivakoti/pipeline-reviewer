"""
ci_review.py — run the reviewer and print ONLY the review markdown.

Used by the GitHub Action so the review can be posted cleanly as a PR/commit
comment (retry/progress messages go to stderr, not stdout).
"""

import sys

from agent_runner import run_agent


def main() -> None:
    paths = sys.argv[1:]
    if not paths:
        print("usage: ci_review.py <artifact> [<artifact> ...]", file=sys.stderr)
        sys.exit(2)
    state = run_agent(paths)
    print(state.get("review", "_(no review produced)_"))


if __name__ == "__main__":
    main()
