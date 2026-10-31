"""
Fills the unit-test gaps in tools.py: JUnit parsing (happy path + error paths),
read_log edge cases, the recurrence memory, offline check_package, PR-diff
parsing, and supply-chain risk grading. All offline (subprocess/network mocked).
"""

import io
import json
import types
from datetime import datetime, timedelta

import tools


class _FakeResp(io.BytesIO):
    """A urlopen() stand-in: a readable byte stream usable as a context manager."""
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(payload):
    return lambda *a, **k: _FakeResp(json.dumps(payload).encode())


# --------------------------------------------------------------------------
# parse_junit_results
# --------------------------------------------------------------------------
def test_parse_junit_happy_path(tmp_path):
    xml = (
        '<?xml version="1.0"?>'
        '<testsuites><testsuite name="s" tests="3" failures="1">'
        '<testcase classname="c" name="test_a"/>'
        '<testcase classname="c" name="test_b"/>'
        '<testcase classname="c" name="test_c">'
        '<failure message="boom">trace</failure></testcase>'
        "</testsuite></testsuites>"
    )
    p = tmp_path / "j.xml"
    p.write_text(xml)
    out = tools.parse_junit_results(str(p))
    assert out["total_tests"] == 3
    assert out["failures"] == 1
    assert out["failed_tests"][0]["name"] == "test_c"


def test_parse_junit_reports_errors_separately(tmp_path):
    # <error> = the test crashed (setup/fixture/import), not an assertion.
    # It used to be silently ignored.
    xml = (
        '<testsuite name="s">'
        '<testcase classname="c" name="test_ok"/>'
        '<testcase classname="c" name="test_db" file="tests/test_db.py" line="12">'
        '<error type="OperationalError" message="connection refused">'
        "conftest.py:8: in db\nE OperationalError</error>"
        "<system-err>could not connect to server</system-err></testcase>"
        '<testcase classname="c" name="test_skip"><skipped/></testcase>'
        "</testsuite>"
    )
    p = tmp_path / "j.xml"
    p.write_text(xml)
    out = tools.parse_junit_results(str(p))
    assert out["total_tests"] == 3
    assert out["failures"] == 0
    assert out["errors"] == 1
    assert out["skipped"] == 1
    assert out["passed"] == 1
    e = out["errored_tests"][0]
    assert e["name"] == "test_db"
    assert e["kind"] == "error"
    assert e["type"] == "OperationalError"
    assert e["file"] == "tests/test_db.py" and e["line"] == "12"
    assert "could not connect" in e["stderr_tail"]


def test_parse_junit_surefire_flaky(tmp_path):
    xml = (
        '<testsuite name="s"><testcase classname="c" name="testRetry">'
        '<flakyFailure type="java.net.SocketTimeoutException" message="Read timed out">'
        "trace</flakyFailure></testcase></testsuite>"
    )
    p = tmp_path / "j.xml"
    p.write_text(xml)
    out = tools.parse_junit_results(str(p))
    assert out["failures"] == 0
    assert out["flaky_passed_on_rerun"] == 1
    assert out["flaky_tests"][0]["kind"] == "flaky"


def test_parse_junit_keeps_trace_head_and_tail(tmp_path):
    trace = "TOP_OF_TRACE\n" + ("x" * 5000) + "\nCaused by: RealRootCause"
    xml = (
        '<testsuite><testcase classname="c" name="t">'
        f'<failure message="m">{trace}</failure></testcase></testsuite>'
    )
    p = tmp_path / "j.xml"
    p.write_text(xml)
    t = tools.parse_junit_results(str(p))["failed_tests"][0]["trace"]
    assert "TOP_OF_TRACE" in t and "Caused by: RealRootCause" in t
    assert len(t) < 1700


def test_parse_junit_caps_mass_failures(tmp_path):
    cases = "".join(
        f'<testcase classname="c" name="t{i}"><failure message="m"/></testcase>'
        for i in range(60)
    )
    p = tmp_path / "j.xml"
    p.write_text(f"<testsuite>{cases}</testsuite>")
    out = tools.parse_junit_results(str(p))
    assert out["failures"] == 60
    assert len(out["failed_tests"]) == 25
    assert "25 of 60" in out["note"]


def test_parse_junit_redacts_captured_output(tmp_path):
    xml = (
        '<testsuite><testcase classname="c" name="t"><failure message="m"/>'
        "<system-out>token=ghp_abcdefghijklmnopqrstuvwxyz123</system-out>"
        "</testcase></testsuite>"
    )
    p = tmp_path / "j.xml"
    p.write_text(xml)
    out = tools.parse_junit_results(str(p))
    assert "ghp_" not in json.dumps(out)


def test_parse_junit_malformed_xml(tmp_path):
    p = tmp_path / "bad.xml"
    p.write_text("<not valid xml")
    assert "error" in tools.parse_junit_results(str(p))  # no crash


def test_parse_junit_missing_file():
    assert "error" in tools.parse_junit_results("does_not_exist_12345.xml")


# --------------------------------------------------------------------------
# read_log edge cases
# --------------------------------------------------------------------------
def test_read_log_missing_file():
    assert "error" in tools.read_log("does_not_exist_12345.log")


def test_read_log_empty_file(tmp_path):
    p = tmp_path / "empty.log"
    p.write_text("")
    out = tools.read_log(str(p))
    assert out["line_count"] == 0
    assert out["log_text"] == ""


# --------------------------------------------------------------------------
# check_recurrence - persistent memory
# --------------------------------------------------------------------------
def test_recurrence_counts_and_remembers_previous_fix(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "MEMORY_FILE", str(tmp_path / "mem.json"))
    sig = "dependency: reqests"

    first = tools.check_recurrence(sig, "fix it by renaming to requests")
    assert first["times_seen_before"] == 0
    assert first["is_recurring"] is False
    assert first["previous_fix"] is None

    second = tools.check_recurrence(sig, "different fix this time")
    assert second["times_seen_before"] == 1
    assert second["is_recurring"] is True
    assert second["previous_fix"] == "fix it by renaming to requests"


# --------------------------------------------------------------------------
# check_package - offline degradation (network mocked to fail)
# --------------------------------------------------------------------------
def test_check_package_offline_degrades(monkeypatch):
    def boom(*a, **k):
        raise OSError("network unreachable")

    monkeypatch.setattr(tools.urllib.request, "urlopen", boom)
    out = tools.check_package("somepackage")  # no colon -> pypi
    assert out["exists"] is None
    assert "lookup_error" in out  # degraded gracefully, no crash


# --------------------------------------------------------------------------
# get_pr_changes - subprocess (gh) mocked
# --------------------------------------------------------------------------
def test_get_pr_changes_parses_files(monkeypatch):
    def fake_run(cmd, **kw):
        if "--name-only" in cmd:
            return types.SimpleNamespace(returncode=0, stdout="app/foo.py\napp/bar.py\n", stderr="")
        return types.SimpleNamespace(returncode=0, stdout="diff --git a/app/foo.py b/app/foo.py\n+x", stderr="")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    out = tools.get_pr_changes("owner/repo", "1")
    assert out["changed_files"] == ["app/foo.py", "app/bar.py"]


def test_get_pr_changes_handles_missing_gh(monkeypatch):
    def no_gh(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(tools.subprocess, "run", no_gh)
    assert "error" in tools.get_pr_changes("owner/repo", "1")


# --------------------------------------------------------------------------
# _grade_supply_chain - risk levels
# --------------------------------------------------------------------------
def test_grade_supply_chain_flags_missing_typosquat_as_high():
    r = {"exists": False}
    tools._grade_supply_chain(r, "reqests", tools._POPULAR)
    assert r["supply_chain_risk"] == "high"
    assert "requests" in r["possible_typosquat_of"]


def test_grade_supply_chain_legit_package_is_low():
    r = {"exists": True}
    tools._grade_supply_chain(r, "requests", tools._POPULAR)
    assert r["supply_chain_risk"] == "low"


# --------------------------------------------------------------------------
# check_package - happy paths (PyPI + Maven) with the network mocked
# --------------------------------------------------------------------------
def test_check_package_pypi_success(monkeypatch):
    monkeypatch.setattr(tools.urllib.request, "urlopen",
                        _fake_urlopen({"info": {"version": "2.31.0"}}))
    out = tools.check_package("requests")
    assert out["ecosystem"] == "pypi"
    assert out["exists"] is True
    assert out["latest_version"] == "2.31.0"


def test_check_package_maven_success(monkeypatch):
    monkeypatch.setattr(tools.urllib.request, "urlopen",
                        _fake_urlopen({"response": {"numFound": 1, "docs": [{"latestVersion": "2.15.2"}]}}))
    out = tools.check_package("com.fasterxml.jackson.core:jackson-databind")
    assert out["ecosystem"] == "maven"
    assert out["exists"] is True
    assert out["latest_version"] == "2.15.2"


# --------------------------------------------------------------------------
# fetch_github_actions_log - subprocess (gh) mocked
# --------------------------------------------------------------------------
def test_fetch_github_actions_log_surfaces_errors(monkeypatch):
    def fake_run(cmd, **kw):
        return types.SimpleNamespace(returncode=0, stdout="setup ok\nERROR: build failed\ndone", stderr="")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    out = tools.fetch_github_actions_log("owner/repo", "123")
    assert out["artifact_type"] == "github_actions_log"
    assert "build failed" in out["log_text"]


def test_fetch_github_actions_log_handles_missing_gh(monkeypatch):
    def no_gh(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(tools.subprocess, "run", no_gh)
    assert "error" in tools.fetch_github_actions_log("owner/repo", "123")


# --------------------------------------------------------------------------
# read_log truncation with NO error lines (the plain-trim branch)
# --------------------------------------------------------------------------
def test_read_log_truncates_long_log_without_errors(tmp_path):
    p = tmp_path / "big.log"
    p.write_text("\n".join(f"processing item {i}" for i in range(1, 301)))  # no error words
    out = tools.read_log(str(p))
    assert out["line_count"] == 300
    assert "omitted" in out["log_text"]


# --------------------------------------------------------------------------
# _avg_interval_str - recurrence interval formatting
# --------------------------------------------------------------------------
def test_avg_interval_hours():
    now = datetime(2026, 1, 1, 12, 0, 0)
    occ = [now.isoformat(), (now + timedelta(hours=2)).isoformat()]
    assert "between occurrences" in tools._avg_interval_str(occ)


def test_avg_interval_single_occurrence_is_blank():
    assert tools._avg_interval_str(["2026-01-01T12:00:00"]) == ""
