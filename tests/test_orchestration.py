"""
Orchestration tests for agent_runner.run_agent - the retry/backoff + blank-review
guard. The agent (Gemini) is mocked, so these are free, fast, and deterministic:
we make the underlying call raise a transient error or return a bad review, and
assert run_agent handles it correctly.
"""

import agent_runner

# A complete review has the first (Failure Type) and last (Confidence) sections.
GOOD = {"review": "**Failure Type:** test_failure\n**Confidence & Verify:** High"}


def _patch(monkeypatch, fake_run_once):
    monkeypatch.setattr(agent_runner, "_run_once", fake_run_once)
    monkeypatch.setattr(agent_runner.time, "sleep", lambda *a, **k: None)  # no real waiting


def test_retries_then_succeeds_on_429(monkeypatch):
    calls = {"n": 0}

    async def fake(paths, on_event=None, pr_ref=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("429 RESOURCE_EXHAUSTED: rate limited, retry in 5s")
        return GOOD

    _patch(monkeypatch, fake)
    state = agent_runner.run_agent(["x"])
    assert calls["n"] == 2, "should have retried once after the 429"
    assert "Failure Type" in state["review"]


def test_retries_on_503_overload(monkeypatch):
    calls = {"n": 0}

    async def fake(paths, on_event=None, pr_ref=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("503 UNAVAILABLE: model overloaded")
        return GOOD

    _patch(monkeypatch, fake)
    assert "Failure Type" in agent_runner.run_agent(["x"])["review"]
    assert calls["n"] == 2


def test_blank_review_is_not_posted(monkeypatch):
    async def fake(paths, on_event=None, pr_ref=None):
        return {"review": ""}  # always blank/incomplete

    _patch(monkeypatch, fake)
    out = agent_runner.run_agent(["x"], max_retries=2)["review"]
    # never posts a mangled/blank review - posts an honest placeholder instead
    assert "could not produce a complete review" in out


def test_good_review_passes_through_unchanged(monkeypatch):
    async def fake(paths, on_event=None, pr_ref=None):
        return GOOD

    _patch(monkeypatch, fake)
    assert agent_runner.run_agent(["x"])["review"] == GOOD["review"]
