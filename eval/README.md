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
| Deterministic eval (failure-type, security, fix, sections, no-secret) | `eval/run_eval.py` | Gemini | no |
| LLM-as-judge (root-cause + fix quality) | `eval/judge.py` | Claude | no |

## Commands

```bash
# --- free / deterministic (what CI runs) ---
pytest -q -m "not live"                          # all free tests
pytest --cov=tools --cov-report=term-missing     # with coverage (tools.py ~92%)

# --- paid: run the agent over the golden set (costs Gemini quota) ---
python eval/run_eval.py                           # runs missing cases, caches to eval/reviews/, writes results.md
#   Re-run it after adding a fresh key: it RESUMES (reuses cached reviews,
#   only calls Gemini for cases not yet cached). Incomplete/rate-limited
#   reviews are NOT cached, so they retry next run.

# --- free after the agent has run once (re-score the cache) ---
python eval/run_eval.py --cached                  # re-score deterministically, no Gemini
python eval/run_eval.py --cached --judge          # + Claude Haiku judge -> full scorecard in results.md
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

## Acceptance thresholds (the pass/fail line)

Set in `run_eval.py`. Deterministic = fraction of cases; judge = average score
per case (each field scored 0-2). `results.md` prints PASS/FAIL per metric and
an OVERALL verdict. Tune as the set grows.

## Quota reality

Gemini free tier is ~5 requests/minute AND ~20/day per model; each agent run is
~5-6 calls, so the full 11-case run does not fit one free day. Mitigations built
in: every review is **cached** (`eval/reviews/`, git-ignored), the run
**resumes** so a fresh key just fills in the rest, and the eval memory is
**reset per run** so results are reproducible.

## Judge validation (manual - do this once)

The judge is only trustworthy if it agrees with a human. To validate: pick ~4
cached reviews, grade root-cause and fix 0/1/2 yourself, and compare to
`results.md`. Agreement within +/-1 on >=3 of 4 = trust it; otherwise tighten
the `SYSTEM` prompt in `judge.py`. (Observed so far: the judge is
discriminating - it gives 0/2 to vague fixes and 2/2 to exact ones - which is
the behaviour we want, but a human pass should still confirm it.)

## Known limitations (honest caveats)

- **Redaction is pattern-based** - `redact_secrets` catches known secret
  formats (Google/AWS/GitHub keys, bearer tokens, `password=`). A format not in
  the pattern list would slip through. It is sampled, not proven.
- **`check_package` hits the live network during the eval** - PyPI/Maven
  lookups make that one signal time-dependent (a package's latest version can
  change; offline runs degrade to "unknown"). The eval resets memory but not the
  network.
- **LLM output varies run to run** - results here are a single run. Re-run for
  confidence; a case can flip on wording. `integration_db` is a known borderline
  label (`infra_error` vs `test_failure`).
- **Coverage** - `tools.py` ~92%; the uncovered lines are HTTP-error-code and
  `gh`-nonzero-return branches that aren't worth mocking.
