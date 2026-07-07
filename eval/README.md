# Evaluation & Testing

The agent has two kinds of parts, so it's tested two ways:

- **Tools + plumbing** (plain code, same output every time) -> **`pytest`** tests with exact assertions.
- **The AI's review** (Gemini text, never identical twice) -> **evaluation**: score the objective bits by rule, and grade the subjective bits (root cause, fix) with an LLM judge.

## How to run

```bash
pytest                                    # all the code tests (fast, no API key)
python eval/run_eval.py --judge           # run the agent on all 11 cases + judge -> eval/results.md
python eval/run_eval.py --cached --judge   # re-score the last run without calling the agent
```
Needs a `.env` with `GEMINI_API_KEY` (agent) and `ANTHROPIC_API_KEY` (judge).

## When each runs

| When | What runs |
|---|---|
| **Every PR** | the code tests **+ 5 key eval cases** (tagged `pr_gate`) with the judge |
| **Weekly / before a release / after a prompt or model change** | the **full 11-case** eval |

The PR runs a representative 5 (one per failure type) so it stays quick as the dataset grows; the full set runs less often. A PR passes if the average score is **≥ 0.80**.

## The dataset

`dataset.json` — **11 labelled pipeline failures** (dependency, test, build, deploy, config, infra, lint, flaky) plus one **"unknown"** case where the agent must say it can't tell instead of inventing a cause. Each case stores the correct `reference_root_cause` and `reference_fix` for the judge to grade against. The 5 cases tagged `pr_gate` are the per-PR subset.

## How scoring works

Each review gets **one score from 0 to 1** — a weighted mix of the checks below. A **leaked secret is an automatic 0**. The agent passes if the average across cases is **≥ 0.80**. `results.md` shows the score per case plus the breakdown.

- **Rule-based checks:** right failure type · security flag correct · fix present · all sections present · no secret leaked
- **Judge (0–2 each):** is the root cause correct · would the fix actually work

## Good to know

- **Sanity-check the judge once:** grade ~4 reviews yourself and compare to `results.md`. If it agrees, trust it; only re-check if you change the judge's model or prompt.
- **AI output varies slightly run-to-run** — re-run for confidence.
- Secret redaction is pattern-based (catches known key formats), and `check_package` does a live PyPI/Maven lookup during a run.
