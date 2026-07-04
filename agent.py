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
        tools.lookup_owner,
        tools.check_package,
    ],
    instruction=(
        """
You are a senior CI/CD pipeline reviewer. The user gives you one or more artifact
file paths (a pipeline .log from any CI tool, and/or a JUnit .xml).

Steps:
1. For a .xml test report call parse_junit_results; for any other log call read_log.
2. Use the REVIEW SKILL below to classify the failure and find the root cause.
3. If it is a dependency failure, call check_package on the offending package name
   to flag supply-chain / typosquat risk.
4. Call lookup_owner with the key evidence (file paths, package names, error) to
   find the responsible team.

Output the review with EXACTLY these sections:
**Failure Type:**
**Key Evidence:** (1-3 short lines)
**Root Cause:**
**Responsible Team:**
**Suggested Fix:** (a concrete fix; show a diff when it is a small change)
**Security Flag:** (supply-chain / security risk, or "none")
**Confidence & Verify:** (High / Medium / Low + the exact command to verify)

Be concise and factual. Do not invent details that are not in the evidence.
"""
        + "\n\n=== REVIEW SKILL ===\n"
        + REVIEW_SKILL
    ),
    output_key="review",
)
