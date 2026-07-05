# CI/CD Pipeline Reviewer Agent
### Kaggle × Google — AI Agents: Intensive Vibe Coding — Capstone Writeup

> Paste into the Kaggle writeup. Fill the video link at the bottom; the repo
> links are already correct.

---

## 1. The problem
When a CI/CD pipeline fails, engineers spend 30–45 min reading logs to work out
*what broke, why, who owns it, and how to fix it.* It's slow and inconsistent.

## 2. What we built
**An AI agent that reviews a failed pipeline** (Google ADK + Gemini) and produces:
failure type · root cause · responsible team · a **suggested fix (as a diff)** ·
a **security/supply-chain flag** · confidence + how to verify.

Crucially, it **runs automatically as a GitHub Action** — on a failed pipeline it
comments the review on the commit/PR. No one pastes anything.

## 3. Why it's a real agent (not "just ChatGPT")
- **Tools on data a chatbot can't reach:** a **live PyPI lookup** to flag
  typosquatted/missing packages, a private **ownership map** for routing, and
  **PR-diff inspection** to check whether the change actually caused the failure.
- **Persistent memory:** it remembers past failures and flags **recurrences** —
  state a chatbot can't hold.
- **Autonomy:** it lives *inside the pipeline* (GitHub Action) and acts on its own.
- **Evaluated:** an eval harness scores it against labelled cases.
- **Security guardrail:** secrets in logs are redacted before reaching the model.

## 4. How it works
A single ADK agent loads a **review skill** (Day 3) and calls **7 tools** (Day 2):
`read_log` (any CI format), `parse_junit_results` (universal),
`fetch_github_actions_log` (pulls a live run), `get_pr_changes` (PR diff),
`lookup_owner`, `check_package` (live PyPI), and `check_recurrence` (memory).
A retry/backoff harness handles free-tier 429/503.

## 5. Live demo (it really runs)
The [demo-app](https://github.com/NavyaSivakoti/demo-app) has an intentionally
failing test. On push, CI fails and the agent auto-posts a review comment, e.g.:

> **Failure Type:** test_failure · **Root Cause:** `create_charge` returns 'USD'
> even when 'EUR' is passed · **Owner:** team-billing · **Suggested Fix:**
> `- "currency": "USD"` → `+ "currency": currency` · **Confidence:** High.

## 6. Evaluation
`eval/run_eval.py` scores the agent on labelled CI failures for failure-type,
owner-routing, security-flag, and fix-suggested accuracy. *(Fill the numbers from
`eval/results.md` after a run; the free-tier daily cap means running the full
sweep when quota is fresh.)*

## 7. Whitepaper concepts (all 5 days)
- **Day 1** agent + context engineering + the Action as a harness
- **Day 2** tools (incl. live PyPI) + GitHub interoperability
- **Day 3** the review skill, loaded by the agent
- **Day 4** security guardrail + supply-chain flag + evaluation
- **Day 5** spec-driven development + human-in-the-loop (agent suggests, human applies)

## 8. Who built what
- **Mohan (DevOps):** the agent, the tools, and the GitHub Action.
- **Navya (AI build):** the evaluation harness and the security-flag logic.

## 9. Limitations & future work
- Root cause / fixes are best-effort LLM suggestions; a human applies them.
- Out of scope: release GO/NO-GO (a single failure can't determine release readiness).
- Next: persist recurrence memory across CI runs, an MCP server (tools usable from Cursor/Claude).

---

**Code (agent):** https://github.com/NavyaSivakoti/pipeline-reviewer
**Code (demo):** https://github.com/NavyaSivakoti/demo-app
**Video:** <your 3–5 min demo video URL>
