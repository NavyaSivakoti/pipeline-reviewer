"""Shared test setup."""

import pytest


@pytest.fixture(autouse=True)
def _no_ci_commit_env(monkeypatch):
    """GitHub Actions sets GITHUB_SHA in every job, and check_recurrence uses it
    to spot re-runs of the same commit. Clear it (and the explicit override) so
    tests behave the same locally and in CI; tests that need a commit set it."""
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.delenv("PIPELINE_REVIEWER_COMMIT", raising=False)
