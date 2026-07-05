# Spec — CI/CD Pipeline Reviewer Agent

> Spec written first (spec-driven development). We build to this spec.

## 1. Problem
When a CI/CD pipeline fails, engineers spend 30–45 min reading logs to work out
*what failed, why, and how to fix it.* It's slow and inconsistent.

## 2. Goal
An AI agent that **reviews a failed pipeline** and returns a clear review —
and runs **automatically as a GitHub Action**, commenting the review on the PR
(so no one has to paste anything).

## 3. Users
Developers, DevOps/release engineers, QA, tech leads.

## 4. Inputs
One or more of: a CI/CD **log** (GitHub Actions / Jenkins / GitLab / plain), and/or
**JUnit/pytest** results.

## 5. Output — the review
- **Failure type** (test · build · dependency · lint · deploy · config · infra · flaky · unknown) + key evidence
- **Root cause**
- **Suggested fix** (as a patch/diff where possible)
- **Security / supply-chain flag** (e.g. typosquatted or outdated package)
- **Recurrence** (has this failure been seen before?)
- **Confidence** + **how to verify**

## 6. Architecture
Single ADK + Gemini agent that loads a **review skill** and calls **6 tools**:
`read_log`, `parse_junit_results`, `fetch_github_actions_log`, `get_pr_changes`,
`check_package`, `check_recurrence`.
Wrapped in a **GitHub Action** for autonomy (runs on failure → comments on PR).

## 7. Scope
**MVP (by Jul 6):** the review (incl. failure memory/recurrence) on logs + JUnit,
runnable from the CLI and as a GitHub Action, with an evaluation harness.
**Stretch:** MCP server (tools reusable in Cursor/Claude).
**Out of scope:** a release GO/NO-GO decision (a single failure can't determine
release readiness); auto-merging fixes.

## 8. Success criteria
- Correct failure type on the labelled eval set
- Never echoes a secret found in a log (guardrail)
- Produces a usable suggested fix
- Runs end-to-end from the CLI and as a GitHub Action comment

## 9. Whitepaper concepts
- Day 1: agent + context engineering + harness (the Action)
- Day 2: tools + GitHub interoperability
- Day 3: the review skill (packaged, loaded)
- Day 4: security guardrail + supply-chain flag + the eval harness
- Day 5: this spec (SDD) + human-in-the-loop (human applies the fix)

## 10. Team
- **Mohan (DevOps):** agent, tools, GitHub Action, demo-app
- **Navya (AI build):** evaluation harness + the security-flag skill
