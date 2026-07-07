# Evaluation & Testing

How the Pipeline Reviewer agent is tested. Two kinds of testing, because the
system has two kinds of component:

- **Deterministic parts** (the 6 tools, the orchestration layer) -> **assert-based tests** (`pytest`). Fast, free, run in CI.
- **The LLM agent** (Gemini) -> **evaluation**, because its output is generated text and can't be asserted against an exact string. We score the objective bits deterministically and grade the subjective bits with an LLM judge.

## Test layers

| Layer | Where | Cost | Runs in CI? |
|-------|-------|------|-------------|
| Tool unit tests | `tests/test_redaction.py`, `test_read_log.py`, `test_supply_chain.py`, `test_tools_extra.py` | free | yes |
| Orchestration (retry/blank-guard, mocked) | `tests/test_orchestration.py` | free | yes |
| Tool-trajectory (right tool + arg, from cache) | `tests/test_trajectory.py` | free | yes (skips w/o cache) |
| Entry-point smoke (CLI end-to-end) | `tests/test_smoke.py` | Gemini | no (gated `RUN_LIVE=1`) |
| Deterministic eval (failure-type, security, fix, sections, no-secret) | `eval/run_eval.py` | Gemini | subset on every PR |
| LLM-as-judge (root-cause + fix quality) | `eval/judge.py` | Claude | subset on every PR |

## When each runs (cadence)

| Trigger | What runs | Where |
|---|---|---|
| **Every push / PR** | Free tests (unit + orchestration + trajectory) | `.github/workflows/tests.yml` |
| **Every PR** | 5 important eval cases (agent + judge), live | `.github/workflows/pr-eval.yml` |
| **Weekly / before a release / on a prompt or model change** | **Full 11-case** eval + judge (manual) | `python eval/run_eval.py --judge` |

The **PR gate** runs a curated subset - `gha_dependency`, `payments_test`,
`docker_build`, `deploy_readiness`, `ambiguous_unknown` (one per key category) -
so it stays fast and scales as the golden set grows. **Nothing is removed:** the
full 11-case suite always runs on the weekly/release cadence; the PR just runs
the important few. The PR gate fails if the average composite score < 0.80.

CI secrets: `GEMINI_API_KEY` (fed into `GOOGLE_API_KEY`) and `ANTHROPIC_API_KEY`.

## Commands

```bash
# --- free / deterministic (what CI runs) ---
pytest -m "not live"                             # all free tests (names + summary via pytest.ini)
pytest --cov=tools --cov-report=term-missing     # with coverage (tools.py ~92%)

# --- paid: run the agent over the golden set (costs Gemini quota) ---
python eval/run_eval.py                           # runs missing cases, caches to eval/reviews/, writes results.md
#   Re-run it after adding a fresh key: it RESUMES (reuses cached reviews,
#   only calls Gemini for cases not yet cached). Incomplete/rate-limited
#   reviews are NOT cached, so they retry next run.

# --- free after the agent has run once (re-score the cache) ---
python eval/run_eval.py --cached                  # re-score deterministically, no Gemini
python eval/run_eval.py --cached --judge          # + LLM judge (model in judge.py) -> full scorecard
python eval/judge.py                              # judge only -> judge_results.md

# --- live smoke test (costs 1 Gemini run) ---
RUN_LIVE=1 pytest tests/test_smoke.py
```

Requires `.env` with `GOOGLE_API_KEY` (agent) and, for the judge, `ANTHROPIC_API_KEY`.

## The golden set

`dataset.json` - 11 labelled failures covering dependency, test, flaky, lint,
build, deploy, config, plus one **adversarial** `ambiguous_unknown` case (a
failure with no clear cause; the agent should say "unknown", not invent one).
Each case carries a `reference_root_cause` and `reference_fix` that the judge
grades against.

## Scoring - the pass/fail line (weighted composite)

Each response gets a **weighted composite score (0-1)**, like promptfoo's
weighted assertions. Deterministic checks + the judge (0-2, scaled) contribute
points; a **leaked secret is a hard 0** (gate). Weights live in `run_eval.py`
(`COMPOSITE_WEIGHTS` / `JUDGE_WEIGHTS`). The agent **PASSes if the average
composite >= 0.80** (`COMPOSITE_PASS`). `run_eval.py` exits non-zero on FAIL so
CI can gate on it. `results.md` shows the composite per case, the overall
verdict, and a per-metric breakdown.

Run a subset (e.g. the PR gate) with `--only`:
`python eval/run_eval.py --only gha_dependency,docker_build --judge`

## Quota reality

Gemini free tier is ~5 requests/minute AND ~20/day per model; each agent run is
~5-6 calls, so the full 11-case run does not fit one free day. Mitigations built
in: every review is **cached** (`eval/reviews/`, git-ignored), the run
**resumes** so a fresh key just fills in the rest, and the eval memory is
**reset per run** so results are reproducible.

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
