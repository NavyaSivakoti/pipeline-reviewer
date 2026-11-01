"""
Failure fingerprints: the same failure must get the same ID on every run
(timestamps, line numbers, paths and hashes change), and different failures
must get different IDs. Plus check_recurrence keyed on that fingerprint.
"""

import importlib
import json
import os

import tools


def _log(tmp_path, name, lines):
    p = tmp_path / name
    p.write_text("\n".join(lines))
    return tools.read_log(str(p))


# --------------------------------------------------------------------------
# Same failure, different run noise -> same fingerprint
# --------------------------------------------------------------------------
def test_same_failure_survives_run_noise(tmp_path):
    run1 = [
        "2026-10-30T10:00:01.123Z Run pytest",
        "2026-10-30T10:00:09.001Z E   KeyError: 'order_id' in /home/runner/work/shop/shop/app/orders.py:42",
        "2026-10-30T10:00:09.002Z   while handling request 3f9a2b7c1d in 0.84s from 10.1.2.3",
        "##[error]Process completed with exit code 1.",
    ]
    run2 = [
        "2026-10-31T22:14:55.987Z Run pytest",
        "2026-10-31T22:15:03.400Z E   KeyError: 'order_id' in /home/runner/work/shop/shop/app/orders.py:57",
        "2026-10-31T22:15:03.401Z   while handling request 9be81f00aa in 1.27s from 10.9.8.7",
        "##[error]Process completed with exit code 1.",
    ]
    a, b = _log(tmp_path, "a.log", run1), _log(tmp_path, "b.log", run2)
    assert a["fingerprint"] and a["fingerprint"] == b["fingerprint"]


def test_same_failure_on_jenkins_and_actions_paths(tmp_path):
    gha = _log(tmp_path, "g.log", ["ERROR: cannot open /home/runner/work/app/app/config/prod.yaml"])
    jen = _log(tmp_path, "j.log", ["ERROR: cannot open /var/lib/jenkins/workspace/app/config/prod.yaml"])
    assert gha["fingerprint"] == jen["fingerprint"]


def test_temp_paths_and_uuids_are_normalized(tmp_path):
    a = _log(tmp_path, "a.log", ["fatal: could not lock /tmp/build-8812/.git/index for job 1b4e28ba-2fa1-11d2-883f-0016d3cca427"])
    b = _log(tmp_path, "b.log", ["fatal: could not lock /tmp/build-0003/.git/index for job 6fa459ea-ee8a-3ca4-894e-db77e160355e"])
    assert a["fingerprint"] == b["fingerprint"]


# --------------------------------------------------------------------------
# Different failures -> different fingerprints
# --------------------------------------------------------------------------
def test_different_package_different_fingerprint(tmp_path):
    a = _log(tmp_path, "a.log", ["ERROR: No matching distribution found for reqests==2.31.0"])
    b = _log(tmp_path, "b.log", ["ERROR: No matching distribution found for numpyy==1.26.0"])
    assert a["fingerprint"] != b["fingerprint"]


def test_http_status_codes_stay_distinct(tmp_path):
    a = _log(tmp_path, "a.log", ["ERROR: registry push failed (401 Unauthorized)"])
    b = _log(tmp_path, "b.log", ["ERROR: registry push failed (500 Internal Server Error)"])
    assert a["fingerprint"] != b["fingerprint"]


def test_lint_rule_codes_stay_distinct(tmp_path):
    a = _log(tmp_path, "a.log", ["[ERROR] app/x.py:3:1: F401 'os' imported but unused"])
    b = _log(tmp_path, "b.log", ["[ERROR] app/x.py:3:1: F841 'os' imported but unused"])
    assert a["fingerprint"] != b["fingerprint"]


def test_generic_failure_lines_are_not_the_fingerprint(tmp_path):
    # Both end in the same generic lines; only the specific error differs.
    tail = ["BUILD FAILED in 11s", "##[error]Process completed with exit code 1."]
    a = _log(tmp_path, "a.log", ["error: cannot find symbol: class Order"] + tail)
    b = _log(tmp_path, "b.log", ["error: cannot find symbol: class Invoice"] + tail)
    assert a["fingerprint"] != b["fingerprint"]
    assert not any("exit code" in ln for ln in a["fingerprint_basis"])


def test_only_generic_lines_gives_no_fingerprint(tmp_path):
    out = _log(tmp_path, "a.log", ["[INFO] done", "[ERROR] Pipeline exited with code 1"])
    assert out["fingerprint"] == ""


def test_gradle_fingerprint_keys_on_the_missing_artifact():
    out = tools.read_log("sample_data/maven_dependency_failure.log")
    assert any("jackson-databin" in ln for ln in out["fingerprint_basis"])


# --------------------------------------------------------------------------
# JUnit fingerprint
# --------------------------------------------------------------------------
def _junit(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(f"<testsuite>{body}</testsuite>")
    return tools.parse_junit_results(str(p))


def test_junit_fingerprint_ignores_message_details(tmp_path):
    a = _junit(tmp_path, "a.xml", '<testcase classname="t.pay" name="test_charge">'
               '<failure message="AssertionError: assert 41 == 42">x.py:10</failure></testcase>')
    b = _junit(tmp_path, "b.xml", '<testcase classname="t.pay" name="test_charge">'
               '<failure message="AssertionError: assert 7 == 9">x.py:88</failure></testcase>')
    assert a["fingerprint"] and a["fingerprint"] == b["fingerprint"]


def test_junit_fingerprint_changes_with_exception_type(tmp_path):
    a = _junit(tmp_path, "a.xml", '<testcase classname="t" name="x"><failure type="AssertionError"/></testcase>')
    b = _junit(tmp_path, "b.xml", '<testcase classname="t" name="x"><error type="java.net.ConnectException"/></testcase>')
    assert a["fingerprint"] != b["fingerprint"]
    assert b["fingerprint_basis"] == ["t::x|connectexception"]


def test_junit_fingerprint_ignores_flaky_tests(tmp_path):
    fail = '<testcase classname="t" name="real"><failure type="AssertionError"/></testcase>'
    flaky = '<testcase classname="t" name="wobbly"><flakyFailure type="TimeoutError"/></testcase>'
    assert _junit(tmp_path, "a.xml", fail)["fingerprint"] == _junit(tmp_path, "b.xml", fail + flaky)["fingerprint"]


# --------------------------------------------------------------------------
# check_recurrence keyed on the fingerprint
# --------------------------------------------------------------------------
def test_recurrence_matches_by_fingerprint_despite_different_wording(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "MEMORY_FILE", str(tmp_path / "mem.json"))
    first = tools.check_recurrence("checkout test fails on currency", "accept currency", "abc123def456")
    assert first["matched_by"] == "fingerprint"
    assert first["is_recurring"] is False

    second = tools.check_recurrence("payments: GatewayError 500", "other fix", "ABC123DEF456")
    assert second["is_recurring"] is True
    assert second["times_seen_before"] == 1
    assert second["previous_fix"] == "accept currency"
    assert second["previously_described_as"] == "checkout test fails on currency"


def test_invalid_fingerprint_falls_back_to_signature(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "MEMORY_FILE", str(tmp_path / "mem.json"))
    out = tools.check_recurrence("dependency: reqests", "", "not-a-fingerprint")
    assert out["matched_by"] == "signature"
    assert "invalid fingerprint" in out["note"]


def test_old_signature_keyed_memory_still_works(tmp_path, monkeypatch):
    mem = tmp_path / "mem.json"
    mem.write_text(json.dumps({"dependency: reqests": {
        "count": 2, "first_seen": "2026-07-01T00:00:00+00:00",
        "occurrences": ["2026-07-01T00:00:00+00:00", "2026-07-02T00:00:00+00:00"],
        "last_fix": "rename to requests"}}))
    monkeypatch.setattr(tools, "MEMORY_FILE", str(mem))
    out = tools.check_recurrence("dependency: reqests")
    assert out["times_seen_before"] == 2
    assert out["previous_fix"] == "rename to requests"


def test_memory_write_is_atomic_and_survives_corruption(tmp_path, monkeypatch):
    mem = tmp_path / "mem.json"
    mem.write_text("{ this is not json")
    monkeypatch.setattr(tools, "MEMORY_FILE", str(mem))
    out = tools.check_recurrence("x", "", "0123456789ab")
    assert out["is_recurring"] is False
    assert json.loads(mem.read_text())  # rewritten as valid JSON
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]


def test_occurrences_are_capped(tmp_path, monkeypatch):
    mem = tmp_path / "mem.json"
    monkeypatch.setattr(tools, "MEMORY_FILE", str(mem))
    for _ in range(60):
        tools.check_recurrence("x", "", "0123456789ab")
    entry = json.loads(mem.read_text())["fp:0123456789ab"]
    assert entry["count"] == 60
    assert len(entry["occurrences"]) == 50


def test_memory_path_from_env(tmp_path, monkeypatch):
    target = str(tmp_path / "cached" / "failures.json")
    monkeypatch.setenv("PIPELINE_REVIEWER_MEMORY", target)
    try:
        importlib.reload(tools)
        assert tools.MEMORY_FILE == target
        tools.check_recurrence("x", "", "0123456789ab")
        assert os.path.exists(target)
    finally:
        monkeypatch.delenv("PIPELINE_REVIEWER_MEMORY")
        importlib.reload(tools)
