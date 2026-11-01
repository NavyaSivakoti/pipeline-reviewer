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

import json
import re
import sys

from agent_runner import run_agent
from sanitize import sanitize_review
from tools import redact_secrets

_PR_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}#[0-9]{1,20}$")
USAGE = "usage: ci_review.py [--pr owner/repo#number] [--json out.json] <artifact> [<artifact> ...]"


def _take_option(args: list, name: str):
    if name not in args:
        return None
    i = args.index(name)
    if i + 1 >= len(args):
        print(f"{name} needs a value\n{USAGE}", file=sys.stderr)
        sys.exit(2)
    value = args[i + 1]
    del args[i:i + 2]
    return value


def _redact_values(obj):
    """Redact secrets in every string of the structured review."""
    if isinstance(obj, str):
        return redact_secrets(obj)
    if isinstance(obj, list):
        return [_redact_values(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _redact_values(v) for k, v in obj.items()}
    return obj


def main() -> None:
    args = sys.argv[1:]
    pr_ref = _take_option(args, "--pr")
    if pr_ref is not None and not _PR_REF.match(pr_ref):
        print(f"invalid --pr value; expected owner/repo#number\n{USAGE}", file=sys.stderr)
        sys.exit(2)
    json_out = _take_option(args, "--json")
    paths = args
    if not paths:
        print(USAGE, file=sys.stderr)
        sys.exit(2)
    state = run_agent(paths, pr_ref=pr_ref)
    markdown = sanitize_review(state.get("review", "_(no review produced)_"))
    if json_out:
        # The structured fields are raw model text: redacted, but NOT sanitized
        # for posting. Post the markdown, use the JSON for metrics / dashboards.
        data = {"review": _redact_values(state.get("review_structured")),
                "markdown": markdown}
        with open(json_out, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    print(markdown)


if __name__ == "__main__":
    main()
