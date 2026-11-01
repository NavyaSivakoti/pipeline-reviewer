"""
Recurrence counts distinct commits: CI re-runs of the same commit must not
inflate "seen N times before", and old failures are forgotten.
"""

import datetime
import json

import pytest

import tools

FP = "0123456789ab"
C1, C2, C3 = "1" * 40, "2" * 40, "3" * 40


@pytest.fixture
def mem(tmp_path, monkeypatch):
    path = tmp_path / "failures.json"
    monkeypatch.setattr(tools, "MEMORY_FILE", str(path))
    return path


def _run(monkeypatch, commit, fix):
    monkeypatch.setenv("PIPELINE_REVIEWER_COMMIT", commit)
    return tools.check_recurrence("t", fix, FP)


def test_reruns_of_the_same_commit_are_not_counted(mem, monkeypatch):
    first = _run(monkeypatch, C1, "fix A")
    rerun1 = _run(monkeypatch, C1, "fix A again")
    rerun2 = _run(monkeypatch, C1, "fix A once more")
    assert first["times_seen_before"] == 0
    for r in (rerun1, rerun2):
        assert r["times_seen_before"] == 0 and r["is_recurring"] is False
        assert r["same_commit_rerun"] is True
    assert json.loads(mem.read_text())[f"fp:{FP}"]["count"] == 1


def test_a_new_commit_is_a_new_occurrence(mem, monkeypatch):
    _run(monkeypatch, C1, "fix A")
    second = _run(monkeypatch, C2, "fix B")
    assert second["times_seen_before"] == 1 and second["is_recurring"] is True
    assert second["previous_fix"] == "fix A"
    assert "same_commit_rerun" not in second


def test_rerun_reports_the_fix_from_the_earlier_commit(mem, monkeypatch):
    _run(monkeypatch, C1, "fix A")
    _run(monkeypatch, C2, "fix B")
    rerun = _run(monkeypatch, C2, "fix B v2")
    assert rerun["times_seen_before"] == 1, "only C1 counts as before"
    assert rerun["previous_fix"] == "fix A", "not this commit's own earlier suggestion"
    third = _run(monkeypatch, C3, "fix C")
    assert third["times_seen_before"] == 2 and third["previous_fix"] == "fix B v2"


def test_github_sha_is_the_fallback(mem, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", C1)
    tools.check_recurrence("t", "", FP)
    assert tools.check_recurrence("t", "", FP)["same_commit_rerun"] is True


def test_explicit_commit_wins_over_github_sha(mem, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", C1)   # e.g. a pull_request merge commit
    monkeypatch.setenv("PIPELINE_REVIEWER_COMMIT", C2)
    tools.check_recurrence("t", "", FP)
    assert json.loads(mem.read_text())[f"fp:{FP}"]["commits"] == [C2]


def test_without_any_commit_every_call_counts(mem):
    tools.check_recurrence("t", "", FP)
    assert tools.check_recurrence("t", "", FP)["times_seen_before"] == 1


@pytest.mark.parametrize("bad", ["main", "not-a-sha", "zz" * 20, "abc"])
def test_garbage_commit_values_are_ignored(mem, monkeypatch, bad):
    monkeypatch.setenv("PIPELINE_REVIEWER_COMMIT", bad)
    tools.check_recurrence("t", "", FP)
    assert "commits" not in json.loads(mem.read_text())[f"fp:{FP}"]


def test_old_entries_are_forgotten_and_the_store_is_bounded(mem, monkeypatch):
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=400)).isoformat()
    recent = datetime.datetime.now(datetime.timezone.utc).isoformat()
    store = {"fp:aaaaaaaaaaaa": {"count": 3, "first_seen": old, "last_seen": old, "occurrences": []}}
    store.update({f"fp:{i:012x}": {"count": 1, "first_seen": recent, "last_seen": recent,
                                   "occurrences": []} for i in range(30)})
    mem.write_text(json.dumps(store))
    monkeypatch.setattr(tools, "_MAX_ENTRIES", 10)
    tools.check_recurrence("t", "", FP)
    after = json.loads(mem.read_text())
    assert "fp:aaaaaaaaaaaa" not in after, "not seen for 400 days"
    assert len(after) <= 11 and f"fp:{FP}" in after


def test_commit_list_is_capped(mem, monkeypatch):
    for i in range(60):
        _run(monkeypatch, f"{i:040x}", "")
    entry = json.loads(mem.read_text())[f"fp:{FP}"]
    assert entry["count"] == 60 and len(entry["commits"]) == tools._MAX_COMMITS
