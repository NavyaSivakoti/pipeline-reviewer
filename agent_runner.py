"""
agent_runner.py — reusable harness with automatic retry/backoff.

The free Gemini tier throws transient 429 (rate limit) and 503 (overloaded)
errors, and can even return a blank or truncated review; we retry with backoff
in both cases, and never post a mangled comment.
"""

import asyncio
import json
import os
import re
import sys
import time
import warnings

warnings.filterwarnings("ignore")

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic import ValidationError

import tools
from agent import root_agent
from review_schema import NO_REVIEW_PREFIX, coerce_review

APP = "pipeline_reviewer"
USER = "local_user"
_TRANSIENT = ("RESOURCE_EXHAUSTED", "UNAVAILABLE", "503", "429")

# A complete review has its first (Failure Type) and last (Confidence) sections.
# If either is missing, the free tier returned a blank or truncated response.
_REQUIRED = ("Failure Type", "Confidence")
# Every "no review" notice starts with NO_REVIEW_PREFIX, so post_review.py can
# recognise one and keep the previous review instead of overwriting it.
_FALLBACK = (
    f"{NO_REVIEW_PREFIX} a complete review this run "
    "(the model was rate-limited or returned an incomplete response). "
    "Please re-run the job to try again._"
)
_QUOTA_FALLBACK = (
    f"{NO_REVIEW_PREFIX} a review this run: the Gemini API's **daily quota is used up**. "
    "Re-run the job after the quota resets, or use a paid API key._"
)

# A daily quota can't recover within a CI job, so retrying only holds the job.
# Google reports the quota in the error, e.g. GenerateRequestsPerDayPerProjectPerModel.
_DAILY_QUOTA = re.compile(r"PerDay|per[ _-]day\b|daily (?:quota|limit)", re.I)

# Upper bound on the total time spent waiting between retries in one review,
# so a CI job never sits for many minutes on a busy or rate-limited model.
MAX_RETRY_WAIT = float(os.environ.get("REVIEW_MAX_RETRY_WAIT", "300"))


def _budget_fallback(seconds: float) -> str:
    return (f"{NO_REVIEW_PREFIX} a review this run: the model stayed rate-limited or busy "
            f"past the {seconds:.0f}s retry budget. Please re-run the job later._")


def _is_complete(review: str) -> bool:
    return bool(review) and all(section in review for section in _REQUIRED)


def _finalize(state: dict) -> bool:
    """Render the markdown review from the agent's structured answer.

    Sets state["review"] (markdown, what gets posted) and, when the agent
    returned structured data, state["review_structured"] (a dict, for --json).
    A plain markdown answer is still accepted. Returns False if the answer was
    JSON that doesn't fit the schema, so the caller retries.
    """
    raw = state.get("review_data", state.get("review"))
    try:
        data, markdown = coerce_review(raw)
    except ValueError as err:
        print(f"   {err}", file=sys.stderr)
        return False
    state["review"] = markdown
    if data is not None:
        state["review_structured"] = data
    return True


def build_message(paths: list[str], pr_ref: str | None = None) -> str:
    msg = "A CI/CD pipeline failed. Review it.\nArtifacts: " + ", ".join(paths)
    if pr_ref:
        repo, _, num = pr_ref.partition("#")
        msg += (
            f"\n\nThis failure occurred on pull request #{num} of {repo}. "
            f'After reading the evidence, call get_pr_changes with repo="{repo}", '
            f'pr_number="{num}" and focus_paths set to the files named in the failure, '
            "then state whether the failure is in code this PR "
            "changed (likely introduced by it) or in code it did not touch (likely "
            "pre-existing, flaky, or environmental)."
        )
    return msg


def _is_transient(err: Exception) -> bool:
    return any(tok in str(err) for tok in _TRANSIENT)


def _is_daily_quota(err: Exception) -> bool:
    s = str(err)
    return ("429" in s or "RESOURCE_EXHAUSTED" in s) and bool(_DAILY_QUOTA.search(s))


def _retry_delay(err: Exception, fallback: float) -> float:
    s = str(err)
    m = re.search(r"retry in ([\d.]+)s", s) or re.search(r"'retryDelay': '(\d+)s'", s)
    return (float(m.group(1)) + 2) if m else fallback


async def _run_once(paths: list[str], on_event=None, pr_ref=None) -> dict:
    runner = InMemoryRunner(agent=root_agent, app_name=APP)
    session = await runner.session_service.create_session(app_name=APP, user_id=USER)
    message = types.Content(role="user", parts=[types.Part(text=build_message(paths, pr_ref))])
    async for event in runner.run_async(user_id=USER, session_id=session.id, new_message=message):
        if on_event:
            on_event(event)
    final = await runner.session_service.get_session(app_name=APP, user_id=USER, session_id=session.id)
    return dict(final.state)


_LOOP = None


def _ensure_loop():
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_LOOP)
        _LOOP.set_exception_handler(lambda loop, ctx: None)
    return _LOOP


def run_agent(paths: list[str], on_event=None, max_retries: int = 6, pr_ref: str | None = None) -> dict:
    # The file tools may read ONLY the artifacts given for this review, so text
    # in a log can't steer the agent into reading other files (e.g. .env).
    # (A GitHub run reference like "owner/repo 123" also lands here; harmless.)
    # The previous setting is restored afterwards so the limit never outlives
    # this review.
    previous = tools._ALLOWED_ARTIFACTS
    tools.set_allowed_artifacts(paths)
    try:
        return _run_with_retries(paths, on_event, max_retries, pr_ref)
    finally:
        tools._ALLOWED_ARTIFACTS = previous


def _run_with_retries(paths, on_event, max_retries, pr_ref) -> dict:
    delay = 10.0
    waited = 0.0
    loop = _ensure_loop()

    def wait(seconds: float, why: str, attempt: int) -> bool:
        """Sleep before the next attempt, unless that would blow the budget."""
        nonlocal waited
        if waited + seconds > MAX_RETRY_WAIT:
            print(f"   {why}; next wait ({seconds:.0f}s) would exceed the {MAX_RETRY_WAIT:.0f}s "
                  "retry budget; giving up", file=sys.stderr)
            return False
        print(f"   {why}; waiting {seconds:.0f}s then retry ({attempt}/{max_retries})", file=sys.stderr)
        time.sleep(seconds)
        waited += seconds
        return True

    for attempt in range(1, max_retries + 1):
        try:
            state = loop.run_until_complete(_run_once(paths, on_event, pr_ref))
        except Exception as err:  # noqa: BLE001
            if _is_daily_quota(err):
                print("   429: the API's daily quota is used up; not retrying", file=sys.stderr)
                return {"review": _QUOTA_FALLBACK, "review_error": "daily_quota"}
            if _is_transient(err):
                kind = "429 rate-limit" if ("429" in str(err) or "RESOURCE_EXHAUSTED" in str(err)) else "503 busy"
                if attempt < max_retries and wait(_retry_delay(err, delay), kind, attempt):
                    delay = min(delay * 1.5, 65)
                    continue
                return {"review": _budget_fallback(MAX_RETRY_WAIT), "review_error": "retries_exhausted"}
            # ADK validates the final answer against the Review schema and
            # raises if the model's JSON doesn't fit: retry like a bad review.
            if isinstance(err, (ValidationError, json.JSONDecodeError)):
                if attempt < max_retries and wait(delay, "review did not match the schema", attempt):
                    delay = min(delay * 1.5, 65)
                    continue
                return {"review": _FALLBACK, "review_error": "invalid_output"}
            raise

        # The call succeeded, but the model can still return a blank,
        # truncated or schema-breaking review. Treat that like a transient
        # error and retry.
        if _finalize(state) and _is_complete(state.get("review", "")):
            return state
        if attempt < max_retries and wait(delay, "review blank/incomplete", attempt):
            delay = min(delay * 1.5, 65)
            continue

        # Still incomplete after every retry: post an honest placeholder,
        # never a mangled half-review.
        state["review"] = _FALLBACK
        state["review_error"] = "incomplete"
        return state
    return {"review": _FALLBACK, "review_error": "incomplete"}


def print_report(state: dict) -> None:
    print("\n" + "=" * 70)
    print("PIPELINE REVIEW")
    print("=" * 70)
    print(state.get("review", "(none)"))
    print()
