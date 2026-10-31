"""
Regression tests for the security review of the accuracy-fixes branch:
gh argument injection, PEM redaction that wiped evidence, a predictable temp
file, and unbounded reads.
"""

import os
import time
import types

import pytest

import tools

_FILL = "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv1Wx2Yz3"


# --------------------------------------------------------------------------
# 1. gh argument injection: the agent picks repo / run id / PR number
# --------------------------------------------------------------------------
@pytest.mark.parametrize("repo,num", [
    ("owner/repo", "--web"),
    ("--repo=attacker/other", "123"),
    ("owner/repo", "123 --log"),
    ("owner/repo;rm -rf /", "1"),
    ("owner/..", "1"),
    ("owner", "1"),
    ("owner/repo", ""),
])
def test_gh_tools_reject_unsafe_arguments(monkeypatch, repo, num):
    calls = []
    monkeypatch.setattr(tools.subprocess, "run", lambda cmd, **k: calls.append(cmd))
    assert "error" in tools.fetch_github_actions_log(repo, num)
    assert "error" in tools.get_pr_changes(repo, num)
    assert calls == [], "gh must not be called with unvalidated arguments"


def test_gh_tools_accept_normal_arguments(monkeypatch):
    ok = types.SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(tools.subprocess, "run", lambda cmd, **k: ok)
    assert "error" not in tools.fetch_github_actions_log("Navya-Sivakoti/demo.app_v2", "28732392827")
    assert "error" not in tools.get_pr_changes("NavyaSivakoti/demo-app", 2)


# --------------------------------------------------------------------------
# 2. PEM redaction must not wipe the evidence after a lone header
# --------------------------------------------------------------------------
def test_lone_pem_header_keeps_following_evidence():
    log = ("ERROR: -----BEGIN " + "PRIVATE KEY----- is not a supported format\n"
           + "\n".join(f"line {i}: real error evidence" for i in range(5)))
    out = tools.redact_secrets(log)
    assert out.count("real error evidence") == 5
    assert out.startswith("ERROR: [REDACTED]")


def test_pem_with_ci_prefixes_is_fully_redacted():
    # gh run view prefixes every line with job<TAB>step<TAB>timestamp
    pre = "deploy\tWrite key\t2026-10-31T22:00:00.1234567Z "
    body = [pre + _FILL + _FILL[:24] for _ in range(4)] + [pre + "AbCd=="]
    log = "\n".join([pre + "-----BEGIN " + "RSA PRIVATE KEY-----"] + body
                    + [pre + "-----END " + "RSA PRIVATE KEY-----", pre + "next step ran"])
    out = tools.redact_secrets(log)
    assert _FILL not in out and "AbCd==" not in out
    assert "next step ran" in out


def test_one_line_pem_in_json_keeps_the_rest_of_the_line():
    line = ('{"private_key": "-----BEGIN ' + 'PRIVATE KEY-----\\n' + _FILL
            + '\\n-----END ' + 'PRIVATE KEY-----\\n", "client_email": "svc@proj.iam"}')
    out = tools.redact_secrets(line)
    assert _FILL not in out
    assert "client_email" in out


def test_pem_redaction_is_linear_on_hostile_input():
    hostile = "\n".join("-----BEGIN " + "PRIVATE KEY-----" for _ in range(20000))
    start = time.time()
    tools.redact_secrets(hostile)
    assert time.time() - start < 1.0


# --------------------------------------------------------------------------
# 3. Memory writes can't be redirected by a planted symlink
# --------------------------------------------------------------------------
def test_planted_symlink_cannot_redirect_memory_write(tmp_path, monkeypatch):
    mem = tmp_path / "failures.json"
    victim = tmp_path / "victim.txt"
    victim.write_text("important")
    # The old code wrote to "<memory>.<pid>.tmp"; plant a symlink there.
    os.symlink(victim, f"{mem}.{os.getpid()}.tmp")
    monkeypatch.setattr(tools, "MEMORY_FILE", str(mem))
    tools.check_recurrence("x", "", "0123456789ab")
    assert victim.read_text() == "important"
    assert mem.exists() and not mem.is_symlink()


# --------------------------------------------------------------------------
# 4. Reads are bounded and only touch regular files
# --------------------------------------------------------------------------
@pytest.mark.skipif(not os.path.exists("/dev/zero"), reason="needs /dev/zero")
def test_device_files_are_refused():
    assert "not a regular file" in tools.read_log("/dev/zero")["error"]
    assert "not a regular file" in tools.parse_junit_results("/dev/zero")["error"]


def test_directories_are_refused(tmp_path):
    assert "error" in tools.read_log(str(tmp_path))
    assert "error" in tools.parse_junit_results(str(tmp_path))


def test_huge_log_is_read_head_and_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_MAX_LOG_BYTES", 4000)
    p = tmp_path / "huge.log"
    p.write_text("FIRST LINE\n" + ("filler line\n" * 2000) + "ERROR: the real failure\n")
    out = tools.read_log(str(p))
    assert "FIRST LINE" in out["log_text"]
    assert "the real failure" in out["log_text"]
    assert out["line_count"] < 400


def test_oversized_junit_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_MAX_XML_BYTES", 100)
    p = tmp_path / "big.xml"
    p.write_text("<testsuite>" + "<testcase name='t'/>" * 50 + "</testsuite>")
    assert "too large" in tools.parse_junit_results(str(p))["error"]


def test_fingerprint_hash_is_marked_non_security():
    # Bandit B324: the SHA-1 is an ID, not a security control.
    import inspect
    assert "usedforsecurity=False" in inspect.getsource(tools._fingerprint)
