"""
judge.py - LLM-as-judge for the subjective eval fields.

The deterministic checks in run_eval.py verify objective things (the failure
label, whether a fix/flag is present, no secret leaked). They CANNOT tell
whether the reviewer's prose is actually right: is the stated root cause the
real cause, and would the suggested fix work?

Those two questions need reasoning, so we grade them with an LLM judge
(a Claude reasoning model - see JUDGE_MODEL) against the reference answers
in dataset.json.
Each field is scored 0/1/2 with a one-line justification:

    2 = correct   - identifies the real cause / a fix that would resolve it
    1 = partial   - right area but misses the specific cause / plausible but
                    incomplete
    0 = wrong     - incorrect, hallucinated, or missing

The judge is a SEPARATE model from the agent under test (the agent is Gemini;
the judge is Claude), so a model never grades its own work. We force a tool
call so the output is always valid structured JSON; that plus a clear rubric
keeps grades stable across runs.

Requires:  pip install anthropic   and   ANTHROPIC_API_KEY in the environment
(or in the project .env - loaded below).

Run standalone over cached reviews (no Gemini calls):  python eval/judge.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

from run_eval import extract_field  # reuse the section extractor

JUDGE_MODEL = "claude-sonnet-5"
DATASET = os.path.join(HERE, "dataset.json")
REVIEWS_DIR = os.path.join(HERE, "reviews")
JUDGE_MD = os.path.join(HERE, "judge_results.md")

SYSTEM = (
    "You are a strict, fair evaluator of an AI CI/CD pipeline-reviewer agent. "
    "You are given (a) the correct answer for a failed pipeline (a reference "
    "root cause and reference fix) and (b) the agent's actual review. Judge "
    "ONLY whether the agent's ROOT CAUSE matches the real cause and whether "
    "its SUGGESTED FIX would actually resolve the failure. Grade the substance, "
    "not the wording, phrasing, or formatting - a differently worded but "
    "correct answer scores full marks. Do not reward confident prose that is "
    "wrong. Score each field 0, 1, or 2:\n"
    "  2 = correct: identifies the real root cause / proposes a fix that would "
    "resolve the failure.\n"
    "  1 = partial: right general area but misses the specific cause / a "
    "plausible but incomplete or not-clearly-correct fix.\n"
    "  0 = wrong: incorrect, hallucinated, contradicts the evidence, or absent.\n"
    "Keep each reason to one sentence. Always call submit_grade."
)

GRADE_TOOL = {
    "name": "submit_grade",
    "description": "Submit the grade for the reviewer's root cause and fix.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["root_cause_score", "root_cause_reason", "fix_score", "fix_reason"],
        "properties": {
            "root_cause_score": {"type": "integer", "enum": [0, 1, 2]},
            "root_cause_reason": {"type": "string"},
            "fix_score": {"type": "integer", "enum": [0, 1, 2]},
            "fix_reason": {"type": "string"},
        },
    },
}


def _build_prompt(review: str, case: dict) -> str:
    got_root = extract_field(review, "Root Cause") or "(no Root Cause section found)"
    got_fix = extract_field(review, "Suggested Fix")
    if not got_fix and "```diff" in (review or ""):
        got_fix = "(fix given as a diff - see the full review below)"
    got_fix = got_fix or "(no Suggested Fix section found)"

    return (
        f"REFERENCE root cause (the truth):\n{case['reference_root_cause']}\n\n"
        f"REFERENCE fix (what would actually work):\n{case['reference_fix']}\n\n"
        f"AGENT's stated root cause:\n{got_root}\n\n"
        f"AGENT's suggested fix:\n{got_fix}\n\n"
        f"AGENT's full review (for context):\n{review or '(empty review)'}\n\n"
        "Grade the agent's root cause and fix against the reference. Call submit_grade."
    )


def judge_case(review: str, case: dict, client=None, model: str = JUDGE_MODEL) -> dict:
    """Grade one review. Returns the submit_grade payload, or an 'error' key."""
    if client is None:
        import anthropic
        client = anthropic.Anthropic()

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=1024,
            # No `temperature`: it's rejected on Sonnet 5 / Opus 4.7+. The forced
            # tool call + a clear rubric keep grades stable without it.
            system=SYSTEM,
            tools=[GRADE_TOOL],
            tool_choice={"type": "tool", "name": "submit_grade"},
            messages=[{"role": "user", "content": _build_prompt(review, case)}],
        )
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:150]}

    for block in resp.content:
        if block.type == "tool_use" and block.name == "submit_grade":
            return dict(block.input)
    return {"error": "judge did not return a grade"}


def _load_cached_review(case_id: str):
    path = os.path.join(REVIEWS_DIR, f"{case_id}.md")
    return open(path).read() if os.path.exists(path) else None


def main() -> None:
    import anthropic

    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Add it to .env and re-run.")

    cases = json.load(open(DATASET))
    client = anthropic.Anthropic()

    rows, missing = [], []
    for c in cases:
        review = _load_cached_review(c["id"])
        if review is None:
            missing.append(c["id"])
            continue
        print(f"judging {c['id']} ...", flush=True)
        g = judge_case(review, c, client=client)
        if "error" in g:
            print(f"    ERROR: {g['error']}")
        else:
            print(f"    root_cause={g['root_cause_score']}/2  fix={g['fix_score']}/2")
        rows.append({"id": c["id"], **g})

    if missing:
        print(f"\nNo cached review for: {', '.join(missing)}\n"
              "Run `python eval/run_eval.py` first to populate eval/reviews/.",
              file=sys.stderr)

    write_report(rows)


def write_report(rows: list) -> None:
    ok = [r for r in rows if "error" not in r]
    m = len(ok)
    rc = sum(r["root_cause_score"] for r in ok)
    fx = sum(r["fix_score"] for r in ok)

    with open(JUDGE_MD, "w") as f:
        f.write(f"# LLM-as-judge results ({JUDGE_MODEL})\n\n")
        f.write(f"_Model: {JUDGE_MODEL}. Scored {m} cached reviews._\n\n")
        if m:
            f.write(f"- Root-cause correctness: {rc}/{2 * m}\n")
            f.write(f"- Fix quality: {fx}/{2 * m}\n\n")
        f.write("| Case | Root cause | Fix | Notes |\n")
        f.write("|------|:----------:|:---:|-------|\n")
        for r in rows:
            if "error" in r:
                f.write(f"| {r['id']} | - | - | ERROR: {r['error']} |\n")
            else:
                note = r["root_cause_reason"].replace("|", "\\|")
                f.write(f"| {r['id']} | {r['root_cause_score']}/2 | {r['fix_score']}/2 | {note} |\n")
    print(f"\nSaved -> {JUDGE_MD}")


if __name__ == "__main__":
    main()
