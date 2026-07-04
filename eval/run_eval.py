"""
run_eval.py — the evaluation harness (Day 4).

Runs the Pipeline Reviewer on labelled CI/CD failures and scores:
  1. Failure-type accuracy
  2. Owner-routing accuracy
  3. Security-flag accuracy (did it flag supply-chain risk when it should?)
  4. Fix-suggested (did it propose a fix?)

Usage:  python eval/run_eval.py
"""

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DATASET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset.json")
RESULTS_MD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.md")


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
    got_owner = extract_field(review, "Responsible Team").lower()
    return {
        "type_ok": case["expected_type"].lower() in got_type,
        "owner_ok": case["expected_owner"].lower() in got_owner,
        "security_ok": security_flagged(review) == (case["expected_security"] == "flag"),
        "fix_ok": has_fix(review),
        "got_type": got_type,
        "got_owner": got_owner,
    }


def main() -> None:
    from agent_runner import run_agent  # imported here so offline tests don't need it

    cases = json.load(open(DATASET))
    n = len(cases)
    rows, tallies = [], {"type_ok": 0, "owner_ok": 0, "security_ok": 0, "fix_ok": 0}

    for i, c in enumerate(cases, 1):
        print(f"\n[{i}/{n}] {c['id']} ...", flush=True)
        try:
            state = run_agent(c["artifacts"])
            s = score_review(state.get("review", ""), c)
        except Exception as e:
            print(f"    ERROR: {str(e)[:90]}")
            s = {"type_ok": False, "owner_ok": False, "security_ok": False,
                 "fix_ok": False, "got_type": "ERROR", "got_owner": ""}
        for k in tallies:
            tallies[k] += bool(s[k])
        rows.append({"id": c["id"], **s})
        print(f"    type:{s['got_type']!r} ok={s['type_ok']} owner_ok={s['owner_ok']} "
              f"sec_ok={s['security_ok']} fix_ok={s['fix_ok']}")
        time.sleep(3)

    print("\n" + "=" * 60)
    print(f"Failure-type accuracy : {tallies['type_ok']}/{n}")
    print(f"Owner-routing accuracy: {tallies['owner_ok']}/{n}")
    print(f"Security-flag accuracy: {tallies['security_ok']}/{n}")
    print(f"Fix suggested         : {tallies['fix_ok']}/{n}")

    with open(RESULTS_MD, "w") as f:
        f.write("# Evaluation results\n\n")
        f.write(f"- Failure-type accuracy: {tallies['type_ok']}/{n}\n")
        f.write(f"- Owner-routing accuracy: {tallies['owner_ok']}/{n}\n")
        f.write(f"- Security-flag accuracy: {tallies['security_ok']}/{n}\n")
        f.write(f"- Fix suggested: {tallies['fix_ok']}/{n}\n\n")
        f.write("| Case | Type ✓ | Owner ✓ | Security ✓ | Fix ✓ |\n")
        f.write("|------|:------:|:-------:|:----------:|:-----:|\n")
        for r in rows:
            f.write(f"| {r['id']} | {'✅' if r['type_ok'] else '❌'} | "
                    f"{'✅' if r['owner_ok'] else '❌'} | {'✅' if r['security_ok'] else '❌'} | "
                    f"{'✅' if r['fix_ok'] else '❌'} |\n")
    print(f"\nSaved -> {RESULTS_MD}")


if __name__ == "__main__":
    main()
