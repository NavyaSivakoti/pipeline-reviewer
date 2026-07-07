# CI/CD Pipeline Reviewer Agent
### Kaggle × Google — AI Agents: Intensive Vibe Coding — Capstone Writeup

## 1. The problem
When a CI/CD pipeline fails, an engineer has to stop and read the logs to work out
*what broke, why, and how to fix it* — a repetitive, inconsistent task that can take
a couple of minutes or well over half an hour depending on the failure.

## 2. What we built
**An AI agent that reviews a failed pipeline** (Google ADK + Gemini) and produces:
failure type · root cause · a **suggested fix (as a diff)** ·
a **security/supply-chain flag** · a **recurrence check** · confidence + how to verify.

Crucially, it **runs automatically as a GitHub Action** — on a failed pipeline it
comments the review on the commit/PR. No one pastes anything.

## 3. What makes it agentic
- **Tools on live/private data:** a **live PyPI + Maven Central lookup**
  to flag typosquatted/missing packages (Python & Java), and **PR-diff inspection**
  to check whether the change actually caused the failure.
- **Persistent memory:** it remembers past failures and flags **recurrences**
  across runs.
- **Autonomy:** it lives *inside the pipeline* (GitHub Action) and acts on its own.
- **Evaluated:** an eval harness scores it against labelled cases.
- **Security guardrail:** secrets in logs are redacted before reaching the model.

## 4. How it works
A single ADK agent loads a **review skill** and calls **6 tools**:
`read_log` (any CI format), `parse_junit_results` (universal),
`fetch_github_actions_log` (pulls a live run), `get_pr_changes` (PR diff),
`check_package` (live PyPI/Maven), and `check_recurrence` (memory).
A retry/backoff harness handles free-tier 429/503.

## 5. Live demo (it really runs)
The [demo-app](https://github.com/NavyaSivakoti/demo-app) has an intentionally
failing test. On PR/push, CI fails and the agent auto-posts a review comment:
- **PR review comment** — [demo-app#2](https://github.com/NavyaSivakoti/demo-app/pull/2): the agent read the PR diff, tied the failure to the **newly-added `app/auth.py`**, proposed the fix as a patch, and **@-mentioned the PR author** ([see the posted comment](https://github.com/NavyaSivakoti/demo-app/pull/2#issuecomment-4885142136)).


## 6. Evaluation
The agent is evaluated on **11 labelled failure scenarios** (`eval/dataset.json`) —
dependency, test, build, deploy, config, infra, lint, flaky, plus an adversarial
"unknown" case (the agent must not invent a cause). Each review is scored two ways:
**rule-based checks** (right failure type, security flag, fix present, all sections,
no secret leaked) and an **LLM-as-judge** that grades root-cause correctness and fix
quality against a reference answer. These roll up into a **weighted composite score
(0–1)**; the agent passes if the average is **≥ 0.80**. A curated 5-case subset runs
on every pull request; the full 11 run before a release. Deterministic tests (unit +
orchestration + tool-trajectory — 40 in total) run on every PR as well.

See **[`eval/README.md`](eval/README.md)** for how to run it and the full details;
the scores land in `eval/results.md`.

## 7. Whitepaper concepts (all 5 days)
- **Day 1** agent + context engineering + the Action as a harness
- **Day 2** tools (incl. live PyPI + Maven Central) + GitHub interoperability
- **Day 3** the review skill, loaded by the agent
- **Day 4** security guardrail + supply-chain flag + evaluation
- **Day 5** spec-driven development + human-in-the-loop (agent suggests, human applies)

## 8. Who built what
- **Mohan Vamshi Appana (DevOps):** the agent, the tools, and the GitHub Action.
- **Lakshmi Navya Sivakoti (AI build):** the evaluation harness and the security/supply-chain logic + tests.

## 9. Limitations & future work
- Root cause / fixes are best-effort LLM suggestions; a human applies them.
- Out of scope: release GO/NO-GO (a single failure can't determine release readiness).
- Next: persist recurrence memory across CI runs, an MCP server (tools usable from Cursor/Claude).

**Code (pipeline-reviewer-agent):** https://github.com/NavyaSivakoti/pipeline-reviewer
**Code (demo-app):** https://github.com/NavyaSivakoti/demo-app 
**Video:** <your 3–5 min demo video URL>
