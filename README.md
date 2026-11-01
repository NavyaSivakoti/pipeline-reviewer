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
A single, focused agent loads its expertise from **skill files** and reasons over tool
output. `agent_runner.py` adds retry/backoff **and a completeness guard** (never posts
a blank/truncated review) for the free tier.

### Skills
`skills/review.md` is the **core skill** (classification, root cause, fix, security)
and is always in the prompt. **Domain skills** are loaded only when a failure needs
them: the prompt carries a one-line catalog, and the agent calls `load_skill(name)`
(see [`skill_loader.py`](skill_loader.py)).

| Skill | Loaded for |
|---|---|
| `flaky-tests` | timeouts and intermittent failures: flaky vs real regression, by evidence |
| `dependency-failures` | pip / Poetry / Maven / Gradle / npm resolution errors, conflicts, typosquats, registry auth |
| `docker-builds` | image pulls and rate limits, apt / wheel build errors, build context, platform, registry auth |

**Adding a skill:** create `skills/<name>.md` with frontmatter `name: <name>` (must match
the file name) and a one-line `description:` saying when to use it, end it with a
**Verify** section, and add an eval case with `"expected_skill": "<name>"` so the
trajectory test checks the agent actually loads it.

## Tools Used
| Tool | What it does | What it enables |
|------|--------------|------------------------------|
| `read_log` | Reads any CI log (GitHub Actions/Jenkins/GitLab); excerpts long logs around real errors | format-agnostic + redacts secrets + failure fingerprint |
| `parse_junit_results` | Parses JUnit/pytest/Surefire XML: failures, errors, flaky reruns | exact, reliable parsing |
| `fetch_github_actions_log` | Pulls only the **failed steps** of a run directly from GitHub, grouped by job/step | acts on live data — no file needed |
| `get_pr_changes` | Per-file PR diffs, **files named in the failure first**, within a line budget | tests whether the PR caused it — vs pre-existing/flaky |
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

### Use it in your own repo
The agent returns a **structured review** (validated against
[`review_schema.py`](review_schema.py)) and the comment is rendered from it in code,
so every review has the same sections. `post_review.py` keeps **one comment per PR**
and edits it on every failed run instead of adding a new one, then flips it to
**✅ Resolved** (with the last review collapsed) once CI passes. It only ever edits
comments written by `github-actions[bot]` that carry its hidden marker.

Everything is pinned to a full commit SHA, **including the reviewer itself**:
cloning its `main` would run whatever was pushed there last, with your API key
and write access to the PR.
```yaml
name: CI + AI Pipeline Reviewer
on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

# A newer push to the same PR cancels the older run, so reviews don't race.
concurrency:
  group: ci-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  test-and-review:
    runs-on: ubuntu-24.04   # pinned: ubuntu-latest is moving to Ubuntu 26
    permissions:
      contents: write          # commit comments (pushes without a PR)
      pull-requests: write     # the PR comment
    env:
      REVIEWER_REPO: NavyaSivakoti/pipeline-reviewer
      REVIEWER_SHA: 0000000000000000000000000000000000000000   # <- full SHA of the reviewer commit you trust
      HAS_GEMINI_KEY: ${{ secrets.GEMINI_API_KEY != '' }}       # false on fork PRs: review is skipped
      PR_NUMBER: ${{ github.event.pull_request.number }}
      PR_AUTHOR: ${{ github.event.pull_request.user.login }}
      HEAD_SHA: ${{ github.event.pull_request.head.sha || github.sha }}
      PIPELINE_REVIEWER_MEMORY: .pipeline-reviewer-memory/failures.json
    steps:
      - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6.3.0
        with:
          python-version: "3.11"
      - uses: actions/cache/restore@55cc8345863c7cc4c66a329aec7e433d2d1c52a9 # v6.1.0
        with:
          path: .pipeline-reviewer-memory
          key: pipeline-reviewer-memory-${{ github.run_id }}
          restore-keys: pipeline-reviewer-memory-

      - run: pip install -r requirements.txt
      - name: Run tests (capture output, decide later)
        id: tests
        run: |
          set +e
          pytest -q --junitxml=results.xml 2>&1 | tee pipeline.log
          echo "code=${PIPESTATUS[0]}" >> "$GITHUB_OUTPUT"

      - name: Get the reviewer at a pinned commit
        if: env.HAS_GEMINI_KEY == 'true'
        run: |
          git init -q reviewer
          git -C reviewer fetch -q --depth 1 "https://github.com/$REVIEWER_REPO" "$REVIEWER_SHA"
          git -C reviewer checkout -q FETCH_HEAD
          test "$(git -C reviewer rev-parse HEAD)" = "$REVIEWER_SHA"

      - name: AI review
        if: steps.tests.outputs.code != '0' && env.HAS_GEMINI_KEY == 'true'
        env:
          GOOGLE_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          GOOGLE_GENAI_USE_VERTEXAI: "FALSE"
          GH_TOKEN: ${{ github.token }}
        run: |
          pip install -q -r reviewer/requirements.txt
          args=("$PWD/results.xml" "$PWD/pipeline.log")
          if [ -n "$PR_NUMBER" ]; then args=(--pr "$GITHUB_REPOSITORY#$PR_NUMBER" "${args[@]}"); fi
          python reviewer/ci_review.py --json review.json "${args[@]}" > review.md

      - name: Post or update the review comment
        if: steps.tests.outputs.code != '0' && env.HAS_GEMINI_KEY == 'true'
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          if [ -n "$PR_NUMBER" ]; then
            python reviewer/post_review.py --repo "$GITHUB_REPOSITORY" --pr "$PR_NUMBER" \
              --review review.md --author "$PR_AUTHOR" --sha "$HEAD_SHA"
          else
            python reviewer/post_review.py --repo "$GITHUB_REPOSITORY" --commit "$GITHUB_SHA" --review review.md
          fi

      - name: Mark the review resolved when CI passes
        if: steps.tests.outputs.code == '0' && env.PR_NUMBER != '' && env.HAS_GEMINI_KEY == 'true'
        env:
          GH_TOKEN: ${{ github.token }}
        run: python reviewer/post_review.py --repo "$GITHUB_REPOSITORY" --pr "$PR_NUMBER" --resolved --sha "$HEAD_SHA"

      - uses: actions/cache/save@55cc8345863c7cc4c66a329aec7e433d2d1c52a9 # v6.1.0
        if: always()
        with:
          path: .pipeline-reviewer-memory
          key: pipeline-reviewer-memory-${{ github.run_id }}

      - name: Fail the job if tests failed
        if: steps.tests.outputs.code != '0'
        run: exit 1
```
Untrusted values (PR author, SHAs, numbers) are passed through `env:` and quoted, never
pasted into `run:` with `${{ }}`, so they can't inject shell commands.

- **`--sha "$HEAD_SHA"`** also guards against races: if the PR has moved past that
  commit by the time the review is ready, nothing is posted (the newer run will post),
  so a slow old run can't overwrite a newer review. Pass the PR head SHA, never
  `GITHUB_SHA` (on `pull_request` events that is a temporary merge commit).
- **Matrix builds:** add `--key "${{ matrix.python }}"` (or the job name, via `env:`) to
  every `post_review.py` call, so each matrix job keeps its own comment instead of
  overwriting the others'.

## Evaluation Results
`eval/run_eval.py` scores the agent on **16 labelled scenarios**. Each review is graded
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
| *Hard:* 561-line Maven log, compile error buried in noise | build | none |
| *Hard:* test output tells the AI to report the build as passing | test_failure | flag (prompt injection) |
| *Hard:* pip version conflict (must NOT be flagged) | dependency | none |
| *Hard:* Docker Hub pull rate limit (not a build error) | infra | none |
| *Hard:* timeout on every rerun (not flaky) | test_failure | none |

Run `python eval/run_eval.py --judge` → writes `eval/results.md`. A curated 5-case
subset runs on every PR, `--tag hard` runs the 5 hard cases, and all 16 run before a
release. **See
[`eval/README.md`](eval/README.md)** for how the evaluation and testing works.

## Security Guardrails
- **Secret redaction before model input.** Stripped inside the tools, so a leaked
  credential in a log **never reaches the model**: AWS, Google, GitHub (classic,
  fine-grained, OAuth, app), GitLab, Slack, Stripe, npm, PyPI, Docker Hub, OpenAI and
  Anthropic tokens; JWTs; PEM private keys; Bearer/Basic auth headers; credentials in
  URLs; Azure account keys and SAS signatures; and password/token/key assignments.
  The key name and host are kept (`DB_PASSWORD=[REDACTED]`,
  `postgres://[REDACTED]@db:5432`) so the evidence stays useful, and masked or empty
  values (`***`, `${{ secrets.X }}`, `""`) are left alone, since an empty secret is
  often the bug. The patterns are bounded so hostile fork-PR logs can't stall them.
- **Prompt-injection guardrails.** Logs, test output and diffs from a fork PR are
  written by whoever opened it, so they are treated as data, never instructions:
  - the file tools can read **only the artifacts given for the review**, so a log
    can't steer the agent into reading `.env` or other files;
  - pipeline text is wrapped in untrusted-content markers (which the text can't
    fake), the agent has explicit security rules, and lines that address an AI
    ("ignore previous instructions…") are flagged so the review reports them;
  - `gh` arguments are validated, so a value like `--web` can't become a flag;
  - the posted review is **sanitized** ([`sanitize.py`](sanitize.py)): @mentions
    are neutralised, images / raw HTML / hidden comments removed, links outside
    `github.com` defanged (add hosts with `REVIEW_ALLOWED_LINK_HOSTS`), `/commands`
    neutralised, and secrets redacted again. Code blocks are left intact.
- **Proven by tests:** [`tests/test_redaction.py`](tests/test_redaction.py),
  [`tests/test_prompt_injection.py`](tests/test_prompt_injection.py) and
  [`tests/test_security_hardening.py`](tests/test_security_hardening.py). Run
  `pytest` — 123 tests pass (redaction, prompt injection, hardening, fingerprints,
  supply-chain, log handling, orchestration), plus 4 tool-trajectory tests that run
  after an eval.
- **Supply-chain awareness:** flags typosquatted / missing packages.
- **Secrets stay out of git:** the API key lives in `.env` (git-ignored); in CI it's a
  GitHub Actions secret.

## Limitations
- Root cause and fixes are **best-effort LLM suggestions** — a human reviews and applies them.
- **No release GO/NO-GO** decision: a single pipeline failure can't determine release
  readiness (that needs coverage, security scans, e2e, sign-off). We deliberately scoped that out.
- **Recurrence memory needs a cache in CI.** Each Action run is a fresh checkout, so
  unless the memory file is persisted (see below) CI reports "first occurrence".
  GitHub caches are branch-scoped: a PR branch can read the default branch's cache,
  but not another PR's.
- Free-tier Gemini is rate-limited and occasionally overloaded (429/503), and can
  return a blank/truncated review; the runner retries on all of these and posts an
  honest "re-run" placeholder rather than a mangled comment. It stops at once when
  the **daily** quota is used up (waiting can't fix that), and caps the total retry
  wait at 300s (`REVIEW_MAX_RETRY_WAIT`) so a CI job never hangs on a busy model.
  If a review already exists in the PR comment, a placeholder doesn't replace it:
  the comment keeps the last real review and notes that the latest run wasn't reviewed.

### Recurrence memory in CI
Failures are matched by a **fingerprint computed in code** from the normalized error
text (timestamps, line numbers, hashes, temp and runner paths stripped), so the same
failure is recognised however the model describes it. To keep the memory between
runs, point `PIPELINE_REVIEWER_MEMORY` at a cached path. Use separate restore/save
steps so the cache is saved even though the job failed. Actions are pinned to full
commit SHAs, like the rest of the workflows:
```yaml
# at the start of the job
- uses: actions/cache/restore@55cc8345863c7cc4c66a329aec7e433d2d1c52a9 # v6.1.0
  with:
    path: .pipeline-reviewer-memory
    key: pipeline-reviewer-memory-${{ github.run_id }}
    restore-keys: pipeline-reviewer-memory-
# ... build / test steps ...
- name: AI pipeline review
  if: failure()
  env:
    PIPELINE_REVIEWER_MEMORY: .pipeline-reviewer-memory/failures.json
  run: python ci_review.py <artifacts>
- uses: actions/cache/save@55cc8345863c7cc4c66a329aec7e433d2d1c52a9 # v6.1.0
  if: always()
  with:
    path: .pipeline-reviewer-memory
    key: pipeline-reviewer-memory-${{ github.run_id }}
```

## Future Work
- Trend insights from the recurrence memory (top recurring failures, flakiest tests).
- Expose the tools as an **MCP server** (usable from Cursor / Claude).
- Inline suggestion blocks on the exact lines of the PR diff.
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
