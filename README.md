# CI/CD Pipeline Reviewer Agent

> An AI agent (**Google ADK + Gemini**) that reviews a failed CI/CD pipeline —
> what broke, why, and how to fix it — and runs **automatically as a
> GitHub Action**, commenting the review on your commit/PR.

Built for the **Kaggle × Google "AI Agents: Intensive Vibe Coding" capstone.**

`Python 3.14` · `google-adk 2.3` · `Gemini 2.5 Flash` · MIT

---

## Problem
When a CI/CD pipeline fails, an engineer has to stop and dig through build logs,
test reports, and configs to answer three questions: *what failed, why, and how do
I fix it?* Depending on the failure and how familiar they are with the code, that
can take a couple of minutes or well over half an hour — and it's repetitive and
inconsistent.

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
     check_package (live PyPI/Maven) · check_recurrence
                                                        │
                                                        ▼
                    review: type · root cause · fix diff ·
                    security flag · recurrence · confidence
                                                        │
                     printed locally  ──OR──  posted as a commit/PR comment
                                              by the GitHub Action (autonomy)
```
A single, focused agent loads its expertise from a **skill file** (`skills/review.md`)
and reasons over tool output. `agent_runner.py` adds retry/backoff **and a
completeness guard** (never posts a blank/truncated review) for the free tier.

## Tools Used
| Tool | What it does | What it enables |
|------|--------------|------------------------------|
| `read_log` | Reads any CI log (GitHub Actions/Jenkins/GitLab) | format-agnostic + redacts secrets |
| `parse_junit_results` | Parses JUnit/pytest XML (universal) | exact, reliable parsing |
| `fetch_github_actions_log` | Pulls a run's logs **directly from GitHub** | acts on live data — no file needed |
| `get_pr_changes` | Inspects the **files changed in the PR** | tests whether the PR caused it — vs pre-existing/flaky |
| `check_package` | **Live PyPI + Maven Central** lookup for typosquat/missing packages (Python & Java) | fetches live registry data at review time |
| `check_recurrence` | Remembers past failures, flags recurrences | **persistent memory** across runs |

## Demo
**See it live** — the [demo-app](https://github.com/NavyaSivakoti/demo-app) has an
intentionally failing test, so CI goes red and the agent auto-reviews it:
- **A pull request it reviewed** — [demo-app#2](https://github.com/NavyaSivakoti/demo-app/pull/2): the agent read the PR diff (`get_pr_changes`), tied the failure to the **newly-added `app/auth.py`**, proposed the fix as a patch, and **@-mentioned the PR author** ([see the posted comment](https://github.com/NavyaSivakoti/demo-app/pull/2#issuecomment-4885142136))
- **An earlier PR / commit it reviewed:** [demo-app#1](https://github.com/NavyaSivakoti/demo-app/pull/1) · [commit comment](https://github.com/NavyaSivakoti/demo-app/commit/bc5816ff0d5d88efac0ccbe1904f2d1ee87bb2b8#commitcomment-191213335)
- **A failed run:** <https://github.com/NavyaSivakoti/demo-app/actions/runs/28732392827>
- **The workflow (actions pinned to SHAs):** [demo-app/.github/workflows/ci.yml](https://github.com/NavyaSivakoti/demo-app/blob/main/.github/workflows/ci.yml)

On a **pull request**, the workflow passes the PR to the reviewer (`ci_review.py --pr owner/repo#number`),
so it calls `get_pr_changes`, inspects the diff, and ties the failure to the specific change
(the **PR author** is the one who fixes it) — or clears the PR when the failure is pre-existing.

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
`eval/run_eval.py` scores the agent on **11 labelled scenarios**. Each review is graded
by **rule-based checks** (failure-type, security-flag, fix-suggested, all sections
present, no secret leaked) plus an **LLM-as-judge** (root-cause correctness + fix
quality). These combine into a **weighted composite score (0–1)**; the agent passes if
the average is **≥ 0.80**.

| Scenario | Expected type | Security flag |
|----------|---------------|---------------|
| GitHub Actions dependency (typosquat) | dependency | flag |
| Payments test failure | test_failure | none |
| Jenkins auth test failure | test_failure | none |
| Flaky test | flaky | none |
| Lint-only | lint | none |
| Docker build (missing build deps) | build | none |
| Integration test (DB not ready) | infra | none |
| Maven/Java dependency (typosquat) | dependency | flag |
| Deploy readiness probe (rollback) | deploy | none |
| Config drift (missing env var) | config | none |
| Ambiguous failure (no clear cause) | unknown | none |

Run `python eval/run_eval.py --judge` → writes `eval/results.md`. A curated 5-case
subset runs on every PR; the full 11 run before a release. **See
[`eval/README.md`](eval/README.md)** for how the evaluation and testing works.

## Security Guardrails
- **Secret redaction before model input.** API keys, tokens, passwords, and AWS/GitHub
  keys are stripped inside the tools, so a leaked credential in a log **never reaches
  the model.**
- **Proven by tests:** [`tests/test_redaction.py`](tests/test_redaction.py) verifies
  redaction on every model-input path (`redact_secrets`, `read_log`,
  `parse_junit_results`). Run `pytest` — 40 tests pass (redaction, supply-chain, log
  handling, orchestration, tool-trajectory).
- **Supply-chain awareness:** flags typosquatted / missing packages.
- **Secrets stay out of git:** the API key lives in `.env` (git-ignored); in CI it's a
  GitHub Actions secret.

## Limitations
- Root cause and fixes are **best-effort LLM suggestions** — a human reviews and applies them.
- **No release GO/NO-GO** decision: a single pipeline failure can't determine release
  readiness (that needs coverage, security scans, e2e, sign-off). We deliberately scoped that out.
- **Recurrence memory is local**, not shared across CI runs (each Action run is a fresh
  checkout, so in CI it reports "first occurrence").
- Free-tier Gemini is rate-limited and occasionally overloaded (429/503), and can
  return a blank/truncated review; the runner retries on all of these and posts an
  honest "re-run" placeholder rather than a mangled comment.

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
| 2 | Tools + interoperability | 6 tools incl. live PyPI + Maven Central; GitHub PR/run integration |
| 3 | Agent Skills | `skills/review.md`, loaded by the agent |
| 4 | Security + evaluation | redaction (tested) + supply-chain flag + `eval/` |
| 5 | Spec-driven + human-in-the-loop | `spec.md` first; agent suggests, human applies |

### Team
- **Mohan Vamshi Appana (DevOps):** the agent, tools, and GitHub Action.
- **Lakshmi Navya Sivakoti (AI build):** the evaluation harness + the security-flag logic and tests.
