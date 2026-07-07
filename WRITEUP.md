# CI/CD Pipeline Reviewer Agent
### Kaggle × Google — AI Agents: Intensive Vibe Coding — Capstone Writeup

## 1. The problem
When a CI/CD pipeline fails, an engineer has to stop what they're doing and read the
logs to work out *what broke, why, and how to fix it* — a repetitive, inconsistent
task that can take a couple of minutes or well over half an hour. The signal is buried:
a single red build can mean a typosquatted dependency, a genuinely broken test, a flaky
test that will pass on re-run, a missing environment variable, or an infra hiccup that
has nothing to do with the code at all. Each calls for a completely different response,
and the engineer reconstructs that context from scratch every time — often across CI
tools (GitHub Actions, Jenkins, GitLab) with different log formats. It's exactly the
judgement-heavy, context-switch-heavy triage that drains a team's time.

## 2. Why an agent (not a script)?
A regex or a lint rule can catch *known* strings, but it can't reason about a novel
log, decide *which* evidence to pull next, or weigh whether a failure was actually
caused by the change under review. This problem needs something that can **choose
tools, act on live data, and form a judgement** — which is precisely what an agent
does. Our agent decides on its own whether to fetch a live run, parse a JUnit report,
look up a package on PyPI, or inspect a PR diff, then synthesises those signals into a
verdict with a confidence level. That autonomy is the whole point: it turns "here are
some logs" into "here is what broke, why, and the exact fix."

## 3. What we built
**An AI agent that reviews a failed pipeline** (Google ADK + Gemini) and produces:
failure type · root cause · a **suggested fix (as a diff)** ·
a **security/supply-chain flag** · a **recurrence check** · confidence + how to verify.

Crucially, it **runs automatically as a GitHub Action** — on a failed pipeline it
comments the review on the commit/PR. No one pastes anything.

## 4. What makes it agentic
- **Tools on live/external data:** a **live PyPI + Maven Central lookup**
  to flag typosquatted/missing packages (Python & Java), and **PR-diff inspection**
  to check whether the change actually caused the failure.
- **Persistent memory:** it remembers past failures and flags **recurrences**
  across runs.
- **Autonomy:** it lives *inside the pipeline* (GitHub Action) and acts on its own.
- **Evaluated:** an eval harness scores it against labelled cases.
- **Security guardrail:** secrets in logs are redacted before reaching the model.

## 5. How it works
A single ADK agent loads a **review skill** (`skills/review.md`, its domain expertise)
and reasons over the output of **6 tools**: `read_log` (reads any CI format and
**redacts secrets** before the text ever reaches the model), `parse_junit_results`
(universal JUnit/pytest XML parsing), `fetch_github_actions_log` (pulls a live run's
logs straight from GitHub — no file needed), `get_pr_changes` (the PR diff),
`check_package` (a **live PyPI + Maven Central** lookup), and `check_recurrence`
(persistent memory of past failures). The agent isn't a fixed pipeline — it picks the
tools each failure needs, so a dependency error triggers a registry lookup while a test
failure triggers XML parsing. Around it, `agent_runner.py` adds a retry/backoff **and a
completeness guard** for the free tier: it retries transient 429/503 responses and
never posts a blank or truncated review, falling back to an honest "re-run" note
instead of a mangled comment.

**It doesn't blame the PR for code it didn't touch.** When a pull request is involved,
the agent calls `get_pr_changes`, compares the failure to the *actual* changed files,
and only pins the blame on the PR when the evidence supports it — otherwise it clears
the PR and flags the failure as pre-existing, flaky, or environmental. This is the
difference between a helpful reviewer and a noisy one, and it's the behaviour we lean
on hardest in the demo.

## 6. Live demo (it really runs)
The [demo-app](https://github.com/NavyaSivakoti/demo-app) has an intentionally
failing test. On PR/push, CI fails and the agent auto-posts a review comment:
- **PR review comment** — [demo-app#2](https://github.com/NavyaSivakoti/demo-app/pull/2): the agent read the PR diff, tied the failure to the **newly-added `app/auth.py`**, proposed the fix as a patch, and **@-mentioned the PR author** ([see the posted comment](https://github.com/NavyaSivakoti/demo-app/pull/2#issuecomment-4885142136)).


## 7. Evaluation
The agent is evaluated on **11 labelled failure scenarios** (`eval/dataset.json`) —
dependency, test, build, deploy, config, infra, lint, flaky, plus an adversarial
"unknown" case (the agent must not invent a cause). Each review is scored two ways:
**rule-based checks** (right failure type, security flag, fix present, all sections,
no secret leaked) and an **LLM-as-judge** that grades root-cause correctness and fix
quality against a reference answer. These roll up into a **weighted composite score
(0–1)**; the agent passes if the average is **≥ 0.80**. A curated 5-case subset runs
on every pull request; the full 11 run before a release. Deterministic tests (unit +
orchestration + tool-trajectory — 40 in total) run on every PR as well.

This two-track design is deliberate: objective properties (right failure type?
supply-chain risk flagged? secret leaked?) can be checked by rule, while subjective ones
(is the root cause right? would the fix work?) need a judge that reads for meaning. A
**leaked secret is an automatic zero** — a hard gate, not a deduction. The judge is
validated against human grading before we trust it. See
**[`eval/README.md`](eval/README.md)** for the full details; scores land in
`eval/results.md`.

## 8. Security
Because CI logs routinely contain credentials, secret redaction happens **inside the
tools, before the model sees anything** — API keys, tokens, passwords, and AWS/GitHub
keys are stripped on every model-input path (`read_log`, `parse_junit_results`). This
is proven by `tests/test_redaction.py`, so a leaked credential in a log never reaches
Gemini and never reaches the posted comment. The API key itself lives in a git-ignored
`.env` locally and a GitHub Actions secret in CI — never in the code.

## 9. Course concepts demonstrated
We demonstrate **four** of the course's key concepts (the rubric asks for ≥ 3):
- **Agent system (ADK):** a single Google ADK + Gemini agent that reasons over tool output — *code*.
- **Security features:** secrets in logs are redacted before they reach the model, proven by tests — *code*.
- **Agent skills:** the agent loads its review expertise from a skill file (`skills/review.md`) — *code*.
- **Deployability:** it runs autonomously inside a GitHub Action, commenting reviews on the commit/PR — *video + code*.

Mapped to the 5-day whitepaper: Day 1 agent + context engineering + the Action as a
harness · Day 2 tools (live PyPI + Maven Central) + GitHub interoperability · Day 3 the
review skill · Day 4 security guardrail + supply-chain flag + evaluation · Day 5
spec-driven development + human-in-the-loop (agent suggests, human applies).

## 10. Who built what
- **Mohan Vamshi Appana (DevOps):** the agent, the tools, and the GitHub Action.
- **Lakshmi Navya Sivakoti (AI build):** the evaluation harness and the security/supply-chain logic + tests.

## 11. Limitations & future work
- Root cause / fixes are best-effort LLM suggestions; a human applies them.
- Out of scope: release GO/NO-GO (a single failure can't determine release readiness).
- Next: persist recurrence memory across CI runs, an MCP server (tools usable from Cursor/Claude).

**Code (pipeline-reviewer-agent):** https://github.com/NavyaSivakoti/pipeline-reviewer
**Code (demo-app):** https://github.com/NavyaSivakoti/demo-app 
**Video:** YouTube link -
