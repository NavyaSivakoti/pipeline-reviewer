"""
run_eval.py - the evaluation harness.

Runs the Pipeline Reviewer on labelled CI/CD failures and scores it on two
kinds of check:

DETERMINISTIC (objective, string checks - free):
  1. Failure-type accuracy
  2. Security-flag accuracy (did it flag supply-chain risk when it should?)
  3. Fix suggested (did it propose a fix?)
  4. All output sections present (format contract)
  5. No secret leaked into the review (security guardrail)

LLM-AS-JUDGE (subjective, needs reasoning - optional, via --judge):
  6. Root-cause correctness   } graded 0/1/2 by Claude Haiku against the
  7. Fix quality              } reference answers in dataset.json (see judge.py)

Every review is cached under eval/reviews/ so the deterministic scoring, the
judge, and the tool-trajectory tests can all re-run offline for free.

Usage:
  python eval/run_eval.py                  # run the agent (costs Gemini calls), score, cache
  python eval/run_eval.py --cached         # re-score cached reviews (no Gemini calls)
  python eval/run_eval.py --cached --judge # re-score + run the Claude Haiku judge
"""

import argparse
import json
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))  # so --judge sees ANTHROPIC_API_KEY

HERE = os.path.dirname(os.path.abspath(__file__))
DATASET = os.path.join(HERE, "dataset.json")
RESULTS_MD = os.path.join(HERE, "results.md")
REVIEWS_DIR = os.path.join(HERE, "reviews")
EVAL_MEMORY = os.path.join(HERE, "eval_memory.json")

# The 7 sections every review must contain (format contract).
REQUIRED_SECTIONS = [
    "Failure Type", "Key Evidence", "Root Cause", "Suggested Fix",
    "Security Flag", "Recurrence", "Confidence",
]

# Raw secret values from sample_data/log_with_secret.log that must NEVER appear
# in a review (mirrors tests/test_redaction.py::LEAKED_MARKERS).
SECRET_MARKERS = [
    "AIzaSyA1234567890", "AKIAIOSFODNN7EXAMPLE",
    "ghp_abcdefghijklmnopqrstuvwxyz", "SuperSecretPassw0rd", "abcdeftokenvalue",
]

# Pass/fail thresholds. Deterministic = fraction of cases; judge = average
# score per case (each field scored 0-2). Tune after the first real run.
DET_THRESHOLDS = {
    "type_ok":          ("Failure-type accuracy", 0.80),
    "security_ok":      ("Security-flag accuracy", 0.90),
    "fix_ok":           ("Fix suggested",          0.90),
    "sections_ok":      ("All sections present",   1.00),
    "no_secret_leaked": ("No secret leaked",       1.00),
}
JUDGE_THRESHOLDS = {
    "root_cause_score": ("Root-cause correctness", 1.4),  # avg per case (max 2)
    "fix_score":        ("Fix quality",            1.2),
}


def extract_field(text: str, label: str) -> str:
    for line in (text or "").splitlines():
        clean = line.strip().lstrip("*# ").strip()
        if clean.lower().startswith(label.lower()):
            return clean.split(":", 1)[1].strip().strip("*").strip() if ":" in clean else ""
    return ""


def has_fix(text: str) -> bool:
    return bool(extract_field(text, "Suggested Fix")) or "```diff" in (text or "")


def security_flagged(text: str) -> bool:
    line = extract_field(text, "Security Flag").lower()
    if not line or "none" in line:
        return False
    return any(w in line for w in ("risk", "typosquat", "⚠", "malicious", "vuln"))


def score_review(review: str, case: dict) -> dict:
    got_type = extract_field(review, "Failure Type").lower()
    return {
        "type_ok": case["expected_type"].lower() in got_type,
        "security_ok": security_flagged(review) == (case["expected_security"] == "flag"),
        "fix_ok": has_fix(review),
        "sections_ok": all(h in (review or "") for h in REQUIRED_SECTIONS),
        "no_secret_leaked": not any(m in (review or "") for m in SECRET_MARKERS),
        "got_type": got_type,
    }


def get_review(case: dict, cached: bool):
    """Return (review_text, tools_called). From disk if cached, else run the agent.

    A live run costs ~5-6 Gemini calls; the free tier is ~20/day. So we cache
    both the review text and the list of tools the LLM called, letting the
    scorer, judge, and trajectory tests re-run offline for free.
    """
    md = os.path.join(REVIEWS_DIR, f"{case['id']}.md")
    tj = os.path.join(REVIEWS_DIR, f"{case['id']}.tools.json")

    def _load():
        tools_called = json.load(open(tj)) if os.path.exists(tj) else []
        return open(md).read(), tools_called

    if cached:
        if not os.path.exists(md):
            raise FileNotFoundError(f"no cached review at {md}; run without --cached first")
        return _load()

    # Resume: a normal run reuses any review already cached and only calls the
    # agent for the missing cases, so a quota-limited eval can be filled in over
    # several runs (add a fresh key, re-run, it picks up where it left off).
    if os.path.exists(md):
        print("    (reused cached review - already done)")
        return _load()

    tools_called = []

    def on_event(ev):  # capture which tools the LLM decided to call
        content = getattr(ev, "content", None)
        for part in getattr(content, "parts", None) or []:
            fc = getattr(part, "function_call", None)
            if fc and getattr(fc, "name", None):
                try:
                    args = dict(fc.args) if getattr(fc, "args", None) else {}
                except Exception:  # noqa: BLE001
                    args = {}
                tools_called.append({"name": fc.name, "args": args})

    from agent_runner import run_agent, _is_complete  # lazy import so --cached needs no ADK

    review = run_agent(case["artifacts"], on_event=on_event).get("review", "")
    if _is_complete(review):
        os.makedirs(REVIEWS_DIR, exist_ok=True)
        open(md, "w").write(review)
        json.dump(tools_called, open(tj, "w"), indent=2)
    else:
        # rate-limited / blank placeholder - don't cache it, so resume retries it
        print("    (incomplete/rate-limited review - not cached; will retry next run)")
    return review, tools_called


def _reset_memory_for_hermetic_eval() -> None:
    """Point the recurrence memory at a throwaway file and clear it, so the eval
    is reproducible: check_recurrence writes state, and a stale memory file would
    otherwise change reviews on re-runs (every distinct failure -> 'first occurrence')."""
    import tools
    tools.MEMORY_FILE = EVAL_MEMORY
    if os.path.exists(EVAL_MEMORY):
        os.remove(EVAL_MEMORY)


def _det_scorecard(tallies: dict, n: int) -> list:
    """[(label, value, bar, passed)] for the deterministic metrics."""
    rows = []
    for key, (label, frac) in DET_THRESHOLDS.items():
        bar = math.ceil(frac * n)
        val = tallies[key]
        rows.append((label, val, bar, n, val >= bar))
    return rows


def _judge_scorecard(judged: list) -> list:
    ok = [g for g in judged if "error" not in g]
    m = len(ok)
    rows = []
    for key, (label, avg) in JUDGE_THRESHOLDS.items():
        bar = math.ceil(avg * m)
        val = sum(g[key] for g in ok)
        rows.append((label, val, bar, 2 * m, val >= bar))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Score the Pipeline Reviewer on labelled failures.")
    ap.add_argument("--cached", action="store_true",
                    help="score reviews saved in eval/reviews/ instead of calling Gemini")
    ap.add_argument("--judge", action="store_true",
                    help="also run the Claude Haiku judge (root-cause + fix quality)")
    args = ap.parse_args()

    cases = json.load(open(DATASET))
    n = len(cases)

    if not args.cached:
        _reset_memory_for_hermetic_eval()

    judge_case = judge_client = None
    if args.judge:
        if not os.getenv("ANTHROPIC_API_KEY"):
            print("!! --judge given but ANTHROPIC_API_KEY is not set; skipping the judge.\n"
                  "   Add ANTHROPIC_API_KEY to .env and re-run.", file=sys.stderr)
        else:
            import anthropic
            from judge import judge_case as _judge_case
            judge_case = _judge_case
            judge_client = anthropic.Anthropic()

    tallies = {k: 0 for k in DET_THRESHOLDS}
    rows, judged = [], []

    for i, c in enumerate(cases, 1):
        print(f"\n[{i}/{n}] {c['id']} ...", flush=True)
        review = ""
        try:
            review, _tools = get_review(c, args.cached)
            s = score_review(review, c)
        except Exception as e:  # noqa: BLE001
            print(f"    ERROR: {str(e)[:90]}")
            s = {k: False for k in DET_THRESHOLDS}
            s["got_type"] = "ERROR"
        for k in tallies:
            tallies[k] += bool(s[k])
        rows.append({"id": c["id"], **s})
        print(f"    type:{s['got_type']!r} type_ok={s['type_ok']} sec_ok={s['security_ok']} "
              f"fix_ok={s['fix_ok']} sections_ok={s['sections_ok']} clean={s['no_secret_leaked']}")

        if judge_case and review:
            g = judge_case(review, c, client=judge_client)
            if "error" in g:
                print(f"    judge ERROR: {g['error']}")
            else:
                print(f"    judge: root_cause={g['root_cause_score']}/2 fix={g['fix_score']}/2")
            judged.append({"id": c["id"], **g})

        if not args.cached:
            time.sleep(3)

    write_report(rows, judged, tallies, n)


def write_report(rows: list, judged: list, tallies: dict, n: int) -> None:
    det = _det_scorecard(tallies, n)
    jud = _judge_scorecard(judged) if judged else []
    overall = all(p for *_, p in det) and all(p for *_, p in jud)

    print("\n" + "=" * 64)
    for label, val, bar, tot, passed in det + jud:
        print(f"{'PASS' if passed else 'FAIL'}  {label:24s} {val}/{tot}  (need >= {bar})")
    print(f"\nOVERALL: {'PASS' if overall else 'FAIL'}")

    with open(RESULTS_MD, "w") as f:
        f.write("# Evaluation results\n\n")

        f.write("## Per-case (deterministic)\n\n")
        f.write("| Case | Type | Security | Fix | Sections | No-secret |\n")
        f.write("|------|:----:|:--------:|:---:|:--------:|:---------:|\n")
        tick = lambda b: "PASS" if b else "FAIL"  # noqa: E731
        for r in rows:
            f.write(f"| {r['id']} | {tick(r['type_ok'])} | {tick(r['security_ok'])} | "
                    f"{tick(r['fix_ok'])} | {tick(r['sections_ok'])} | {tick(r['no_secret_leaked'])} |\n")

        if judged:
            f.write("\n## Per-case (Claude Haiku judge)\n\n")
            f.write("| Case | Root cause | Fix | Notes |\n")
            f.write("|------|:----------:|:---:|-------|\n")
            for g in judged:
                if "error" in g:
                    f.write(f"| {g['id']} | - | - | ERROR: {g['error']} |\n")
                else:
                    note = g["root_cause_reason"].replace("|", "\\|")
                    f.write(f"| {g['id']} | {g['root_cause_score']}/2 | {g['fix_score']}/2 | {note} |\n")

        f.write("\n## Scorecard (metric vs threshold)\n\n")
        f.write("| Metric | Score | Threshold | Result |\n")
        f.write("|--------|:-----:|:---------:|:------:|\n")
        for label, val, bar, tot, passed in det + jud:
            f.write(f"| {label} | {val}/{tot} | >= {bar} | {'PASS' if passed else 'FAIL'} |\n")
        f.write(f"\n**OVERALL: {'PASS' if overall else 'FAIL'}**\n")

    print(f"\nSaved -> {RESULTS_MD}")


if __name__ == "__main__":
    main()
