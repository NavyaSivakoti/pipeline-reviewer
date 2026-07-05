"""
tools.py — the agent's TOOLS (Day 2).

Deterministic Python functions the agent calls. Parsing is exact work best done
in code; the LLM does the reasoning. `check_package` reaches LIVE data (PyPI) —
something a chatbot can't do on its own.
"""

import datetime
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

BASE = os.path.dirname(os.path.abspath(__file__))
OWNERSHIP_FILE = os.path.join(BASE, "sample_data", "ownership_map.json")
MEMORY_FILE = os.path.join(BASE, "memory", "failures.json")


def _resolve(path: str) -> str:
    if os.path.isabs(path) or os.path.exists(path):
        return path
    return os.path.join(BASE, path)


# --------------------------------------------------------------------------
# Security guardrail (Day 4): redact secrets before any text reaches the model
# --------------------------------------------------------------------------
_SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z\-_]{20,}"),
    re.compile(r"sk-[0-9A-Za-z]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[0-9A-Za-z]{20,}"),
    re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)\s*[=:]\s*\S+"),
    re.compile(r"(?i)bearer\s+[0-9A-Za-z\.\-_]{12,}"),
]


def redact_secrets(text: str) -> str:
    """Replace anything that looks like a secret with [REDACTED]."""
    for pat in _SECRET_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text


# --------------------------------------------------------------------------
# Generic log reader — works for ANY CI tool (GitHub Actions, Jenkins, GitLab)
# --------------------------------------------------------------------------
def read_log(file_path: str) -> dict:
    """Read any CI/CD or build/console log and return its redacted text.

    Use for any non-XML log from any CI tool. Secrets are stripped first.

    Args:
        file_path: path to the log / console-output file.
    """
    try:
        with open(_resolve(file_path)) as f:
            raw = f.read()
    except FileNotFoundError:
        return {"error": f"log file not found: {file_path}"}

    text = redact_secrets(raw)
    lines = text.splitlines()
    total = len(lines)
    if total > 200:
        omitted = total - 200
        lines = lines[:120] + [f"... ({omitted} lines omitted) ..."] + lines[-80:]
    return {"artifact_type": "raw_log", "line_count": total, "log_text": "\n".join(lines)}


# --------------------------------------------------------------------------
# JUnit / pytest XML parser (universal across CI tools)
# --------------------------------------------------------------------------
def parse_junit_results(file_path: str) -> dict:
    """Parse a JUnit/pytest XML report and list the failing tests.

    Args:
        file_path: path to the JUnit XML results file.
    """
    try:
        tree = ET.parse(_resolve(file_path))
    except FileNotFoundError:
        return {"error": f"results file not found: {file_path}"}
    except ET.ParseError as e:
        return {"error": f"could not parse XML: {e}"}

    root = tree.getroot()
    total = failures = 0
    failed_tests = []
    for case in root.iter("testcase"):
        total += 1
        fail = case.find("failure")
        if fail is not None:
            failures += 1
            failed_tests.append({
                "name": case.get("name"),
                "classname": case.get("classname"),
                "message": redact_secrets(fail.get("message") or ""),
                "trace": redact_secrets((fail.text or "").strip())[:500],
            })
    return {
        "artifact_type": "junit_results",
        "total_tests": total,
        "failures": failures,
        "failed_tests": failed_tests,
    }


# --------------------------------------------------------------------------
# Ownership lookup: evidence text -> responsible team
# --------------------------------------------------------------------------
def lookup_owner(context_text: str) -> dict:
    """Find the responsible team for a failure by scanning evidence text for
    keywords in the ownership map (e.g. 'payments' -> team-billing).

    Args:
        context_text: error lines / file paths / failed test names to scan.
    """
    with open(OWNERSHIP_FILE) as f:
        data = json.load(f)
    rules = data.get("rules", {})
    haystack = context_text.lower()
    for keyword, team in rules.items():
        if keyword.lower() in haystack:
            return {"owner": team, "matched_keyword": keyword}
    return {"owner": data.get("default", "team-platform"), "matched_keyword": None}


# --------------------------------------------------------------------------
# Supply-chain check (Day 4): LIVE PyPI lookup + typosquat heuristic
# --------------------------------------------------------------------------
_POPULAR = [
    "requests", "flask", "django", "numpy", "pandas", "pytest", "boto3",
    "urllib3", "pyyaml", "fastapi", "sqlalchemy", "click", "jinja2", "werkzeug",
    "pillow", "scipy", "setuptools", "cryptography", "certifi",
]


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def check_package(package_name: str) -> dict:
    """Check a package for supply-chain risk (Day 4 concept).

    Does a LIVE lookup on PyPI: does the package exist, what's the latest
    version, and is it a likely typosquat of a popular package? Use this on a
    dependency failure to flag typo'd or malicious package names.

    Args:
        package_name: the package name from the failing dependency step.
    """
    name = re.split(r"[=<>!~ ]", package_name.strip())[0].lower()
    result = {"package": name}
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=6) as r:
            data = json.load(r)
            result["exists_on_pypi"] = True
            result["latest_version"] = data["info"]["version"]
    except urllib.error.HTTPError as e:
        result["exists_on_pypi"] = False if e.code == 404 else None
        if e.code != 404:
            result["lookup_error"] = f"HTTP {e.code}"
    except Exception as e:  # network off / timeout — degrade gracefully
        result["exists_on_pypi"] = None
        result["lookup_error"] = str(e)[:80]

    close = [p for p in _POPULAR if p != name and _edit_distance(name, p) == 1]
    if close:
        result["possible_typosquat_of"] = close
        result["supply_chain_risk"] = "high" if result.get("exists_on_pypi") is False else "medium"
    elif result.get("exists_on_pypi") is False:
        result["supply_chain_risk"] = "medium"
    else:
        result["supply_chain_risk"] = "low"
    return result


# --------------------------------------------------------------------------
# Memory / recurrence detection (Day 1 state) — persists across runs
# --------------------------------------------------------------------------
def _avg_interval_str(occurrences: list) -> str:
    """Average time between occurrences (a real recurrence signal)."""
    if len(occurrences) < 2:
        return ""
    ts = [datetime.datetime.fromisoformat(t) for t in occurrences]
    gaps = [(ts[i] - ts[i - 1]).total_seconds() for i in range(1, len(ts))]
    avg = sum(gaps) / len(gaps)
    if avg < 3600:
        return f"~{avg / 60:.0f} min between occurrences"
    if avg < 86400:
        return f"~{avg / 3600:.1f} h between occurrences"
    return f"~{avg / 86400:.1f} days between occurrences"


def check_recurrence(signature: str, suggested_fix: str = "") -> dict:
    """Record this failure and report how often it has happened before, plus the
    fix suggested LAST time. This is the agent's memory across runs — a chatbot
    can't remember your past pipeline failures.

    Args:
        signature: a short STABLE identifier (failing test name, or
            "dependency: <package>"). Must be the same each time the failure recurs.
        suggested_fix: the fix you are proposing now (stored for next time).
    """
    os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
    try:
        store = json.load(open(MEMORY_FILE))
    except (FileNotFoundError, json.JSONDecodeError):
        store = {}

    key = re.sub(r"\s+", " ", signature.strip().lower())[:200]
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    entry = store.get(key, {"count": 0, "first_seen": now, "occurrences": [], "last_fix": ""})

    times_before = entry["count"]
    previous_fix = entry.get("last_fix", "")

    entry["count"] += 1
    entry["last_seen"] = now
    entry.setdefault("occurrences", []).append(now)
    if suggested_fix:
        entry["last_fix"] = suggested_fix.strip()[:300]
    store[key] = entry
    json.dump(store, open(MEMORY_FILE, "w"), indent=2)

    return {
        "signature": key,
        "times_seen_before": times_before,
        "is_recurring": times_before > 0,
        "first_seen": entry["first_seen"],
        "last_seen": now,
        "previous_fix": previous_fix or None,
        "recurrence_interval": _avg_interval_str(entry["occurrences"]) or None,
    }


# --------------------------------------------------------------------------
# Fetch a run's logs DIRECTLY from GitHub Actions (no local file needed)
# --------------------------------------------------------------------------
def fetch_github_actions_log(repo: str, run_id: str) -> dict:
    """Fetch a failed GitHub Actions run's logs directly, via the gh CLI.

    Lets the agent review a run WITHOUT downloading a file first. Secrets are
    redacted; output is trimmed to the error/failure lines.

    Args:
        repo: 'owner/repo' (e.g. 'NavyaSivakoti/demo-app').
        run_id: the numeric run id.
    """
    try:
        out = subprocess.run(
            ["gh", "run", "view", str(run_id), "--repo", repo, "--log"],
            capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError:
        return {"error": "gh CLI not available in this environment"}
    except subprocess.TimeoutExpired:
        return {"error": "gh run view timed out"}
    if out.returncode != 0:
        return {"error": f"gh failed: {out.stderr[:200]}"}

    text = redact_secrets(out.stdout)
    lines = text.splitlines()
    important = [ln for ln in lines if re.search(r"error|fail|assert|traceback|exception", ln, re.I)]
    shown = important[:80] or lines[-80:]
    return {
        "artifact_type": "github_actions_log",
        "repo": repo,
        "run_id": str(run_id),
        "line_count": len(lines),
        "log_text": "\n".join(shown[:120]),
    }
