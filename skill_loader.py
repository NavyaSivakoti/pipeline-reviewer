"""
skill_loader.py — domain skills, loaded on demand.

`skills/review.md` is the core skill and is always in the agent's prompt.
Every other `skills/<name>.md` is a domain skill (flaky tests, dependencies,
Docker, ...). The prompt only carries a one-line catalog built from each
skill's frontmatter; the agent calls `load_skill(name)` when a failure matches,
so a review pays for the one or two skills it needs instead of all of them.

A domain skill file looks like:

    ---
    name: docker-builds            # must equal the file name (docker-builds.md)
    description: one line saying when to use it
    ---
    ...instructions...
"""

import os
import re

SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")
CORE_SKILL = "review"
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.S)
MAX_SKILL_CHARS = 12000


def parse_skill(path: str) -> tuple:
    """Return (metadata dict, body) for a skill file."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = _FRONTMATTER.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    return meta, m.group(2).strip()


def available_skills() -> dict:
    """Domain skills by name -> {"description", "path"}. The core skill and any
    file whose frontmatter name doesn't match its file name are left out."""
    skills = {}
    if not os.path.isdir(SKILLS_DIR):
        return skills
    for fname in sorted(os.listdir(SKILLS_DIR)):
        stem, ext = os.path.splitext(fname)
        if ext != ".md" or stem == CORE_SKILL or not _NAME.match(stem):
            continue
        path = os.path.join(SKILLS_DIR, fname)
        meta, _ = parse_skill(path)
        if meta.get("name") == stem and meta.get("description"):
            skills[stem] = {"description": meta["description"], "path": path}
    return skills


def skill_catalog() -> str:
    """The catalog lines that go into the agent's prompt."""
    return "\n".join(f"- {name}: {info['description']}" for name, info in available_skills().items())


def load_skill(name: str) -> dict:
    """Load a domain skill: extra review instructions for one kind of failure.

    Call it after you have a first idea of the failure type, when a skill in
    the DOMAIN SKILLS catalog matches (for example docker-builds for a failed
    image build). Load at most two skills per review, and follow their guidance
    when choosing the failure type, root cause, fix and verify command.

    Args:
        name: the skill name exactly as listed in the catalog.
    """
    skills = available_skills()
    key = str(name or "").strip().lower()
    if not _NAME.match(key) or key not in skills:
        return {"error": f"unknown skill {str(name)[:60]!r}", "available": sorted(skills)}
    _, body = parse_skill(skills[key]["path"])
    return {"skill": key, "instructions": body[:MAX_SKILL_CHARS]}
