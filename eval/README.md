# Evaluation & Testing

How the Pipeline Reviewer agent is tested. Two kinds of testing, because the
system has two kinds of component:

- **Deterministic parts** (the 6 tools, the orchestration layer) -> **assert-based tests** (`pytest`). Fast, deterministic, run in CI.
- **The LLM agent** (Gemini) -> **evaluation**, because its output is generated text and can't be asserted against an exact string. We score the objective bits deterministically and grade the subjective bits with an LLM judge.

## Test layers

| Layer | Where | Kind | Runs in CI? |
|-------|-------|------|-------------|
| Tool unit tests | `tests/test_redaction.py`, `test_read_log.py`, `test_supply_chain.py`, `test_tools_extra.py` | deterministic | yes |
| Orchestration (retry/blank-guard, mocked) | `tests/test_orchestration.py` | deterministic | yes |
| Tool-trajectory (right tool + arg, from cache) | `tests/test_trajectory.py` | deterministic | yes (skips w/o cache) |
| Deterministic eval (failure-type, security, fix, sections, no-secret) | `eval/run_eval.py` | Gemini agent | subset on every PR |
| LLM-as-judge (root-cause + fix quality) | `eval/judge.py` | LLM judge | subset on every PR |

## When each runs (cadence)

| Trigger | What runs | Where |
|---|---|---|
| **Every push / PR** | Deterministic tests (unit + orchestration + trajectory) | `.github/workflows/tests.yml` |
| **Every PR** | 5 important eval cases (agent + judge), live | `.github/workflows/pr-eval.yml` |
| **Weekly / before a release / on a prompt or model change** | **Full 11-case** eval + judge (manual) | `python eval/run_eval.py --judge` |

The **PR gate** runs a curated subset - the cases tagged **`pr_gate`** in
`dataset.json` (`gha_dependency`, `payments_test`, `docker_build`,
`deploy_readiness`, `ambiguous_unknown` - one per key category) - so it scales
as the golden set grows. **Nothing is removed:** the full 11-case suite always
runs on the weekly/release cadence; the PR just runs the important few. The PR
gate fails if the average composite score < 0.80.

CI secrets: `GEMINI_API_KEY` (fed into `GOOGLE_API_KEY`) and `ANTHROPIC_API_KEY`.

## Commands

```bash
# --- deterministic tests (what CI runs on every push/PR) ---
pytest                                           # all tests, with names + summary (config in pytest.ini)
pytest --cov=tools --cov-report=term-missing     # with coverage (tools.py ~92%)

# --- run the agent over the golden set ---
python eval/run_eval.py                           # runs missing cases, caches to eval/reviews/, writes results.md
#   Re-run to RESUME: reuses cached reviews, only runs cases not yet done.
python eval/run_eval.py --judge                   # + LLM judge -> full scorecard
python eval/run_eval.py --tag pr_gate --judge     # just the PR-gate subset (5 cases)

# --- re-score cached reviews (no agent calls) ---
python eval/run_eval.py --cached                  # re-score deterministically
python eval/run_eval.py --cached --judge          # + LLM judge (model in judge.py)
python eval/judge.py                              # judge only -> judge_results.md
```

Requires `.env` with `GOOGLE_API_KEY` (agent) and, for the judge, `ANTHROPIC_API_KEY`.

## The golden set

`dataset.json` - 11 labelled failures covering dependency, test, flaky, lint,
build, deploy, config, infra, plus one **adversarial** `ambiguous_unknown` case
(a failure with no clear cause; the agent should say "unknown", not invent one).
Each case carries a `reference_root_cause` and `reference_fix` that the judge
grades against. The 5 cases tagged **`pr_gate`** are the per-PR subset.

## Scoring - the pass/fail line (weighted composite)

Each response gets a **weighted composite score (0-1)**, like promptfoo's
weighted assertions. Deterministic checks + the judge (0-2, scaled) contribute
points; a **leaked secret is a hard 0** (gate). Weights live in `run_eval.py`
(`COMPOSITE_WEIGHTS` / `JUDGE_WEIGHTS`). The agent **PASSes if the average
composite >= 0.80** (`COMPOSITE_PASS`). `run_eval.py` exits non-zero on FAIL so
CI can gate on it. `results.md` shows the composite per case, the overall
verdict, and a per-metric breakdown.

Run a subset by tag or id:
`python eval/run_eval.py --tag pr_gate --judge` or `--only gha_dependency,docker_build`.

## Reproducibility

The recurrence tool writes state and the agent hits live registries, so the eval
takes care to stay reproducible: every review is **cached** (`eval/reviews/`,
git-ignored), a re-run **resumes** (reuses cached reviews, only runs the cases
not yet done), and the recurrence memory is **reset per run** so a stale memory
file can't change results between runs. The runner also retries with backoff on
transient rate-limit / overload responses.

## Judge validation (do this once, whenever the judge changes)

The judge is only trustworthy if it agrees with a human. To validate: pick ~4
cached reviews, grade root-cause and fix 0/1/2 yourself, and compare to
`results.md`. Agreement within +/-1 on >=3 of 4 = trust it; otherwise tighten
the `SYSTEM` prompt in `judge.py`, or use a stronger `JUDGE_MODEL` (one line in
`judge.py`). The judge model is a reasoning model; it is discriminating (it docks
vague fixes to 1/2 while giving exact ones 2/2). Re-validate only when you change
the judge's model or rubric.

## Known limitations (honest caveats)

- **Redaction is pattern-based** - `redact_secrets` catches known secret
  formats (Google/AWS/GitHub keys, bearer tokens, `password=`). A format not in
  the pattern list would slip through. It is sampled, not proven.
- **`check_package` hits the live network during the eval** - PyPI/Maven
  lookups make that one signal time-dependent (a package's latest version can
  change; offline runs degrade to "unknown"). The eval resets memory but not the
  network.
- **LLM output varies run to run** - results here are a single run. Re-run for
  confidence; a case can flip on wording. (`integration_db` was a borderline
  label - the agent calls it `infra`, which we adopted as the expected type.)
- **Coverage** - `tools.py` ~92%; the uncovered lines are HTTP-error-code and
  `gh`-nonzero-return branches that aren't worth mocking.
