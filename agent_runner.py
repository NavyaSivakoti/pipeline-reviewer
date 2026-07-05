"""
agent_runner.py — reusable harness with automatic retry/backoff.

The free Gemini tier throws transient 429 (rate limit) and 503 (overloaded)
errors; we retry with backoff instead of crashing (Day 4 reliability).
"""

import asyncio
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

from agent import root_agent

APP = "pipeline_reviewer"
USER = "local_user"
_TRANSIENT = ("RESOURCE_EXHAUSTED", "UNAVAILABLE", "503", "429")


def build_message(paths: list[str], pr_ref: str | None = None) -> str:
    msg = "A CI/CD pipeline failed. Review it.\nArtifacts: " + ", ".join(paths)
    if pr_ref:
        repo, _, num = pr_ref.partition("#")
        msg += (
            f"\n\nThis failure occurred on pull request #{num} of {repo}. "
            f'Call get_pr_changes with repo="{repo}" and pr_number="{num}" to see the '
            "files this PR changed, then state whether the failure is in code this PR "
            "changed (likely introduced by it) or in code it did not touch (likely "
            "pre-existing, flaky, or environmental)."
        )
    return msg


def _is_transient(err: Exception) -> bool:
    return any(tok in str(err) for tok in _TRANSIENT)


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
    delay = 10.0
    loop = _ensure_loop()
    for attempt in range(1, max_retries + 1):
        try:
            return loop.run_until_complete(_run_once(paths, on_event, pr_ref))
        except Exception as err:  # noqa: BLE001
            if _is_transient(err) and attempt < max_retries:
                wait = _retry_delay(err, delay)
                kind = "429 rate-limit" if ("429" in str(err) or "RESOURCE_EXHAUSTED" in str(err)) else "503 busy"
                print(f"   {kind}; waiting {wait:.0f}s then retry ({attempt}/{max_retries})", file=sys.stderr)
                time.sleep(wait)
                delay = min(delay * 1.5, 65)
            else:
                raise
    raise RuntimeError("exhausted retries")


def print_report(state: dict) -> None:
    print("\n" + "=" * 70)
    print("PIPELINE REVIEW")
    print("=" * 70)
    print(state.get("review", "(none)"))
    print()
