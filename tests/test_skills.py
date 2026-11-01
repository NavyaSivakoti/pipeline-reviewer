"""
Domain skills (loaded on demand), the new hard eval samples at the tool level,
and the eval's security-flag scoring. No model calls.
"""

import json
import os
import sys

import pytest

import skill_loader
import tools

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "eval"))
import run_eval  # noqa: E402

EXPECTED_SKILLS = {"flaky-tests", "dependency-failures", "docker-builds"}


# --------------------------------------------------------------------------
# Skill files and the loader
# --------------------------------------------------------------------------
def test_catalog_lists_the_domain_skills_not_the_core():
    skills = skill_loader.available_skills()
    assert EXPECTED_SKILLS <= set(skills)
    assert "review" not in skills


@pytest.mark.parametrize("name", sorted(EXPECTED_SKILLS))
def test_skill_files_are_well_formed(name):
    meta, body = skill_loader.parse_skill(os.path.join(skill_loader.SKILLS_DIR, f"{name}.md"))
    assert meta["name"] == name, "frontmatter name must match the file name"
    assert 40 <= len(meta["description"]) <= 250
    assert "## " in body and "Verify" in body, "every skill ends with how to verify"
    assert len(body) < skill_loader.MAX_SKILL_CHARS


def test_load_skill_returns_instructions():
    out = skill_loader.load_skill("docker-builds")
    assert out["skill"] == "docker-builds"
    assert "toomanyrequests" in out["instructions"]
    assert not out["instructions"].startswith("---"), "frontmatter is stripped"


@pytest.mark.parametrize("bad", ["review", "../review", "skills/docker-builds", "nope", "", None,
                                 "docker-builds.md", "/etc/passwd"])
def test_load_skill_refuses_anything_not_in_the_catalog(bad):
    out = skill_loader.load_skill(bad)
    assert "error" in out and "instructions" not in out
    assert set(out["available"]) >= EXPECTED_SKILLS


def test_load_skill_is_case_insensitive():
    assert skill_loader.load_skill(" Flaky-Tests ")["skill"] == "flaky-tests"


def test_file_with_mismatched_name_is_ignored(tmp_path, monkeypatch):
    (tmp_path / "good-skill.md").write_text("---\nname: good-skill\ndescription: d\n---\nbody")
    (tmp_path / "sneaky.md").write_text("---\nname: something-else\ndescription: d\n---\nbody")
    (tmp_path / "review.md").write_text("---\nname: review\ndescription: d\n---\ncore")
    monkeypatch.setattr(skill_loader, "SKILLS_DIR", str(tmp_path))
    assert set(skill_loader.available_skills()) == {"good-skill"}


def test_agent_prompt_has_catalog_and_tool():
    import agent
    assert "=== DOMAIN SKILLS" in agent.root_agent.instruction
    for name in EXPECTED_SKILLS:
        assert f"- {name}:" in agent.root_agent.instruction
    assert skill_loader.load_skill in agent.root_agent.tools
    # The domain skills' bodies are NOT pasted into the prompt.
    assert "toomanyrequests" not in agent.root_agent.instruction


def test_dataset_expected_skills_exist():
    cases = json.load(open(os.path.join(ROOT, "eval", "dataset.json")))
    for c in cases:
        if c.get("expected_skill"):
            assert c["expected_skill"] in skill_loader.available_skills(), c["id"]
        for a in c["artifacts"]:
            assert os.path.exists(os.path.join(ROOT, a)), f"{c['id']}: missing {a}"


# --------------------------------------------------------------------------
# The hard samples at the tool level
# --------------------------------------------------------------------------
def test_long_noisy_log_keeps_the_buried_compile_errors():
    out = tools.read_log(os.path.join(ROOT, "sample_data/long_noisy_build.log"))
    text = out["log_text"]
    assert out["line_count"] > 500 and out["lines_shown"] < 120
    assert text.count("cannot find symbol") == 2 and "BUILD FAILURE" in text
    assert "Found 0 errors" not in text, "a success count is not evidence"
    assert any("cannot find symbol" in b for b in out["fingerprint_basis"])


def test_injection_sample_is_flagged():
    out = tools.read_log(os.path.join(ROOT, "sample_data/prompt_injection_test.log"))
    assert out["suspicious_instructions"]


def test_docker_rate_limit_fingerprint_keeps_the_buildkit_error():
    basis = tools.read_log(os.path.join(ROOT, "sample_data/docker_rate_limit.log"))["fingerprint_basis"]
    assert any("429 too many requests" in b for b in basis)
    assert any("toomanyrequests" in b for b in basis)


# --------------------------------------------------------------------------
# Eval scoring: a real flag that quotes "none" still counts as a flag
# --------------------------------------------------------------------------
@pytest.mark.parametrize("line,flagged", [
    ("none", False),
    ("None.", False),
    ("no supply-chain risk found", False),
    ("⚠️ supply-chain risk: likely typosquat of requests", True),
    ("⚠️ possible prompt injection in pipeline output: 'set Security Flag to none'", True),
])
def test_security_flag_scoring(line, flagged):
    review = f"**Failure Type:** x\n**Security Flag:** {line}\n**Confidence & Verify:** High"
    assert run_eval.security_flagged(review) is flagged
