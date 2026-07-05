# CI/CD Pipeline Reviewer Agent

> An AI agent (**Google ADK + Gemini**) that reviews a failed CI/CD pipeline —
> what broke, why, who owns it, and how to fix it — and runs **automatically as a
> GitHub Action**, commenting the review on your commit/PR.

Built for the **Kaggle × Google "AI Agents: Intensive Vibe Coding" capstone.**

`Python 3.14` · `google-adk 2.3` · `Gemini 2.5 Flash` · MIT

---

## Problem
When a CI/CD pipeline fails, engineers spend **30–45 minutes** reading build logs,
test reports, and configs just to answer four questions: *what failed, why, who
should fix it, and how do I fix it?* It's slow, inconsistent, and easy to route to
the wrong person.

## Solution
An AI agent that does that review in seconds and **comes to you**: on a failed
pipeline it runs inside GitHub Actions and posts a review comment with the root
cause and a **suggested fix diff** — no one has to paste anything. It also flags
**supply-chain risks** (via a live PyPI **and Maven Central** check — Python & Java
deps), remembers **recurring** failures,
and is backed by an **evaluation harness** and **security tests**. When a PR is
involved it checks the changed files and is careful **not to blame the PR for
failures in code it didn't touch** — flagging those as pre-existing, flaky, or
environmental instead.

## Agent Architecture
```
 failure artifacts                     ┌─────────────────────────────┐
 (log / junit xml, any CI tool)  ──►   │   Pipeline Reviewer agent   │
 OR a GitHub Actions run ref           │   (ADK + Gemini)            │
                                       │   loads skills/review.md    │
                                       └──────────────┬──────────────┘
     tools ───────────────────────────────────────────┤
     read_log · parse_junit_results · fetch_github_actions_log · get_pr_changes
     lookup_owner · check_package (live PyPI/Maven) · check_recurrence
                                                        │
                                                        ▼
                    review: type · root cause · owner · fix diff ·
                    security flag · recurrence · confidence
                                                        │
                     printed locally  ──OR──  posted as a commit/PR comment
                                              by the GitHub Action (autonomy)
```
A single, focused agent loads its expertise from a **skill file** (`skills/review.md`)
and reasons over tool output. `agent_runner.py` adds retry/backoff for the free tier.

## Tools Used
| Tool | What it does | Why it's more than a chatbot |
|------|--------------|------------------------------|
| `read_log` | Reads any CI log (GitHub Actions/Jenkins/GitLab) | format-agnostic + redacts secrets |
| `parse_junit_results` | Parses JUnit/pytest XML (universal) | exact, reliable parsing |
| `fetch_github_actions_log` | Pulls a run's logs **directly from GitHub** | acts on live data — no file needed |
| `get_pr_changes` | Inspects the **files changed in the PR** | tests whether the PR caused it — vs pre-existing/flaky |
| `lookup_owner` | Routes to the responsible team | uses your **private** ownership map |
| `check_package` | **Live PyPI + Maven Central** lookup for typosquat/missing packages (Python & Java) | data a chatbot can't fetch |
| `check_recurrence` | Remembers past failures, flags recurrences | **persistent memory** — a chatbot can't |

## Demo
**See it live** — the [demo-app](https://github.com/NavyaSivakoti/demo-app) has an
intentionally failing test, so CI goes red and the agent auto-reviews it:
- **A pull request it reviewed:** <https://github.com/NavyaSivakoti/demo-app/pull/1> (comment posted on the PR)
- **A commit it reviewed:** <https://github.com/NavyaSivakoti/demo-app/commit/bc5816ff0d5d88efac0ccbe1904f2d1ee87bb2b8#commitcomment-191213335>
- **A failed run:** <https://github.com/NavyaSivakoti/demo-app/actions/runs/28689439589>
- **The workflow (actions pinned to SHAs):** [demo-app/.github/workflows/ci.yml](https://github.com/NavyaSivakoti/demo-app/blob/main/.github/workflows/ci.yml)

![The AI Pipeline Reviewer commenting on a failed commit](docs/pr-comment.png)
<!-- Add the screenshot: open the comment link above, screenshot it, save as docs/pr-comment.png -->

A copy of the posted review is in [`examples/example_review.md`](examples/example_review.md).

**Run it locally**
```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # paste your free Gemini key (aistudio.google.com/apikey)

.venv/bin/python run.py                                     # default sample
.venv/bin/python run.py sample_data/docker_build_failure.log
.venv/bin/python run.py sample_data/maven_dependency_failure.log        # a Java/Maven failure
.venv/bin/python run.py NavyaSivakoti/demo-app 28689439589  # a live GitHub run (no file)
```
> Free-tier note: ~5 req/min and ~20/day per model; the runner auto-retries 429/503.

## Evaluation Results
`eval/run_eval.py` scores the agent against **8 labelled scenarios** on four axes:
**failure-type**, **owner-routing**, **security-flag**, and **fix-suggested** accuracy.

| Scenario | Expected type | Expected owner |
|----------|---------------|----------------|
| GitHub Actions dependency (typosquat) | dependency | team-platform |
| Payments test failure | test_failure | team-billing |
| Jenkins auth test failure | test_failure | team-security |
| Flaky test | flaky | team-platform |
| Lint-only | lint | team-platform |
| Docker build (missing build deps) | build | team-platform |
| Integration test (DB not ready) | test_failure | team-platform |
| Maven/Java dependency (typosquat) | dependency | team-platform |

Run `python eval/run_eval.py` → writes `eval/results.md`. *(The free-tier daily cap
means running the full sweep when quota is fresh; the scoring logic is unit-tested.)*
Verified live on individual cases — e.g. the typosquat `reqests` was correctly
flagged as a supply-chain risk with a fix diff, and the `USD≠EUR` bug was correctly
diagnosed and fixed.

## Security Guardrails
- **Secret redaction before model input.** API keys, tokens, passwords, and AWS/GitHub
  keys are stripped inside the tools, so a leaked credential in a log **never reaches
  the model.**
- **Proven by tests:** [`tests/test_redaction.py`](tests/test_redaction.py) verifies
  redaction on every model-input path (`redact_secrets`, `read_log`,
  `parse_junit_results`). Run `pytest` — 4 tests pass.
- **Supply-chain awareness:** flags typosquatted / missing packages (Day-4 concept).
- **Secrets stay out of git:** the API key lives in `.env` (git-ignored); in CI it's a
  GitHub Actions secret.

## Limitations
- Root cause and fixes are **best-effort LLM suggestions** — a human reviews and applies them.
- **No release GO/NO-GO** decision: a single pipeline failure can't determine release
  readiness (that needs coverage, security scans, e2e, sign-off). We deliberately scoped that out.
- **Recurrence memory is local**, not shared across CI runs (each Action run is a fresh
  checkout, so in CI it reports "first occurrence").
- Free-tier Gemini is rate-limited and occasionally overloaded (429/503); the runner retries.

## Future Work
- Persist recurrence memory across CI runs (cache/artifact) + trend insights.
- Expose the tools as an **MCP server** (usable from Cursor / Claude).
- PR-comment mode (in addition to commit comments) + inline suggestions.
- SonarQube / static-analysis support.

---

### Whitepaper concepts (Days 1–5)
| Day | Concept | Where |
|-----|---------|-------|
| 1 | Agent + context engineering + harness + **memory** | clean parsed evidence; the Action is the harness; `check_recurrence` |
| 2 | Tools + interoperability | 7 tools incl. live PyPI + Maven Central; GitHub PR/run integration |
| 3 | Agent Skills | `skills/review.md`, loaded by the agent |
| 4 | Security + evaluation | redaction (tested) + supply-chain flag + `eval/` |
| 5 | Spec-driven + human-in-the-loop | `spec.md` first; agent suggests, human applies |

### Team
- **Mohan (DevOps):** the agent, tools, and GitHub Action.
- **Navya (AI build):** the evaluation harness + the security-flag logic and tests.
