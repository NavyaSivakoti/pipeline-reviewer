"""
agent.py — the Pipeline Reviewer agent (single, focused ADK agent).

It loads the review skill (Day 3) and calls the tools (Day 2) to review a
failed pipeline: classify -> root cause -> owner -> fix -> security flag.
"""

import os

from google.adk.agents import Agent

import tools

# Pinned stable model; override with GEMINI_MODEL env var if rate-limited.
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")


def load_skill(name: str) -> str:
    """Load a packaged Agent Skill (Day 3) from skills/."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills", f"{name}.md")
    with open(path) as f:
        return f.read()


REVIEW_SKILL = load_skill("review")

root_agent = Agent(
    name="pipeline_reviewer",
    model=MODEL,
    tools=[
        tools.read_log,
        tools.parse_junit_results,
        tools.fetch_github_actions_log,
        tools.get_pr_changes,
        tools.lookup_owner,
        tools.check_package,
        tools.check_recurrence,
    ],
    instruction=(
        """
You are a senior CI/CD pipeline reviewer. The user gives you either artifact file
paths (a .log from any CI tool and/or a JUnit .xml), OR a GitHub Actions run
reference ('owner/repo' plus a numeric run id).

Steps:
1. Get the failure evidence:
   - For a .xml test report call parse_junit_results.
   - For a GitHub Actions run reference call fetch_github_actions_log.
   - For any other log file call read_log.
2. Use the REVIEW SKILL below to classify the failure and find the root cause.
3. If it is a dependency failure, call check_package on the offending package name
   (works for both Python/PyPI names and Java/Maven coordinates like group:artifact).
4. Call lookup_owner with the key evidence to find the responsible team.
5. If a pull request is referenced (owner/repo + a PR number), call get_pr_changes,
   then COMPARE the changed files with where the failure actually occurs:
   - Failure in a file this PR changed  -> likely introduced by this PR; say so.
   - Failure in code the PR did NOT touch -> probably NOT caused by this PR; call it
     out as likely pre-existing, flaky, or environmental (dependency update, unready
     service, infra), not the author's change.
   Never assume the change caused the failure.
6. Call check_recurrence with a short STABLE signature for this failure (the
   failing test name, or "dependency: <package>") AND your suggested_fix. Use the
   result for the Recurrence line: if seen before, state how many times, when it
   first appeared, how often it recurs, and the PREVIOUS fix; else "first occurrence".

Output the review with EXACTLY these sections:
**Failure Type:**
**Key Evidence:** (1-3 short lines)
**Root Cause:**
**Responsible Team:**
**Suggested Fix:** (a concrete fix; show a diff when it is a small change)
**Security Flag:** (supply-chain / security risk, or "none")
**Recurrence:** (either "first occurrence", or "⚠️ seen N times before (first on <date>, recurs <interval>) — previous fix: <...>")
**Confidence & Verify:** (High / Medium / Low + the exact command to verify)

Then output a fenced code block labelled json containing an object with the keys:
failure_type, root_cause, responsible_team, suggested_fix, security_flag,
recurrence_count (integer), confidence.

Be concise and factual. Do not invent details that are not in the evidence.
"""
        + "\n\n=== REVIEW SKILL ===\n"
        + REVIEW_SKILL
    ),
    output_key="review",
)
