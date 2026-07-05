"""
tools.py — the agent's TOOLS (Day 2).

Deterministic Python functions the agent calls. Parsing is exact work best done
in code; the LLM does the reasoning. `check_package` reaches LIVE data
(PyPI + Maven Central) at review time.
"""

import datetime
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

BASE = os.path.dirname(os.path.abspath(__file__))
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
    re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)[ \t]*[=:][ \t]*\S+"),
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
# Error/failure markers for Python + Java CI logs. Case-insensitive. Kept broad
# on purpose so a root cause is surfaced even when phrased without "error".
_ERROR_LINE = re.compile(
    r"error|fail|assert|traceback|exception|caused by|fatal|"
    r"segfault|segmentation fault|core dumped|killed|out of memory|"
    r"timeout|timed out|cannot find symbol|could not (?:find|resolve)|"
    r"no module named|connection refused",
    re.I,
)


def read_log(file_path: str) -> dict:
    """Read any CI/CD or build/console log and return its redacted text.

    Use for any non-XML log from any CI tool. Secrets are stripped first. Long
    logs are trimmed to the first 120 + last 80 lines, but any error/failure
    lines from the omitted middle are ALSO surfaced, so a mid-log root cause is
    never dropped just because of where it appears.

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
        head, tail, middle = lines[:120], lines[-80:], lines[120:total - 80]
        errors = [ln for ln in middle if _ERROR_LINE.search(ln)]
        if errors:
            lines = (head
                     + [f"... ({len(middle)} lines omitted; {len(errors)} error line(s) surfaced) ..."]
                     + errors[:40]
                     + ["..."]
                     + tail)
        else:
            lines = head + [f"... ({len(middle)} lines omitted) ..."] + tail
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
# Supply-chain check (Day 4): LIVE PyPI lookup + typosquat heuristic
# --------------------------------------------------------------------------
# Popular PyPI (Python) package names — used to spot typosquats.
_POPULAR = [
    "requests", "flask", "django", "numpy", "pandas", "pytest", "boto3",
    "urllib3", "pyyaml", "fastapi", "sqlalchemy", "click", "jinja2", "werkzeug",
    "pillow", "scipy", "setuptools", "cryptography", "certifi",
]

# Popular Maven Central (Java) artifactIds — used to spot typosquats of Java deps.
_POPULAR_MAVEN = [
    "jackson-databind", "guava", "junit", "junit-jupiter", "slf4j-api",
    "log4j-core", "logback-classic", "commons-lang3", "commons-io", "gson",
    "spring-core", "spring-context", "spring-boot", "mockito-core", "okhttp",
    "netty-all", "hibernate-core", "snakeyaml",
]


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _typosquat_of(name: str, popular: list) -> list:
    """Popular package names that are exactly one edit away from `name`."""
    return [p for p in popular if p != name and _edit_distance(name, p) == 1]


def _detect_ecosystem(package_name: str) -> str:
    """Guess the dependency ecosystem from how the name is written.

    Maven coordinates look like 'groupId:artifactId[:version]' (a colon) or use a
    reverse-domain group id like 'com.google.guava'. Everything else -> PyPI.
    """
    n = package_name.strip()
    if ":" in n:
        return "maven"
    if "==" not in n and re.match(r"^[a-z]+(\.[a-z0-9_]+){2,}$", n.lower()):
        return "maven"
    return "pypi"


def _grade_supply_chain(result: dict, name: str, popular: list) -> None:
    """Set possible_typosquat_of + supply_chain_risk on `result`, in place."""
    close = _typosquat_of(name, popular)
    if close:
        result["possible_typosquat_of"] = close
        result["supply_chain_risk"] = "high" if result.get("exists") is False else "medium"
    elif result.get("exists") is False:
        result["supply_chain_risk"] = "medium"
    else:
        result["supply_chain_risk"] = "low"


def _check_pypi(package_name: str) -> dict:
    name = re.split(r"[=<>!~ ]", package_name.strip())[0].lower()
    result = {"package": name, "ecosystem": "pypi"}
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=6) as r:
            data = json.load(r)
            result["exists"] = True
            result["latest_version"] = data["info"]["version"]
    except urllib.error.HTTPError as e:
        result["exists"] = False if e.code == 404 else None
        if e.code != 404:
            result["lookup_error"] = f"HTTP {e.code}"
    except Exception as e:  # network off / timeout — degrade gracefully
        result["exists"] = None
        result["lookup_error"] = str(e)[:80]
    result["exists_on_pypi"] = result["exists"]  # backward-compatible key
    _grade_supply_chain(result, name, _POPULAR)
    return result


def _check_maven(package_name: str) -> dict:
    """Live lookup on Maven Central for a Java/Maven dependency."""
    raw = package_name.strip()
    parts = raw.split(":")
    group = parts[0] if len(parts) >= 2 else ""
    artifact = (parts[1] if len(parts) >= 2 else parts[0]).lower()
    result = {"package": raw, "ecosystem": "maven", "artifact": artifact}
    query = f'g:"{group}" AND a:"{artifact}"' if group else f'a:"{artifact}"'
    url = "https://search.maven.org/solrsearch/select?" + urllib.parse.urlencode(
        {"q": query, "rows": "1", "wt": "json"}
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "pipeline-reviewer"})
        with urllib.request.urlopen(req, timeout=6) as r:
            resp = json.load(r).get("response", {})
            result["exists"] = resp.get("numFound", 0) > 0
            if result["exists"]:
                doc = resp["docs"][0]
                result["latest_version"] = doc.get("latestVersion") or doc.get("v")
    except Exception as e:  # network off / timeout — degrade gracefully
        result["exists"] = None
        result["lookup_error"] = str(e)[:80]
    result["exists_on_maven"] = result["exists"]
    _grade_supply_chain(result, artifact, _POPULAR_MAVEN)
    return result


def check_package(package_name: str, ecosystem: str = "auto") -> dict:
    """Check a dependency for supply-chain risk (Day 4 concept).

    Does a LIVE lookup to see whether a package exists, its latest version, and
    whether it's a likely typosquat of a popular package. Supports two
    ecosystems:
      - **PyPI** (Python) — e.g. "requests", "reqests==2.31.0"
      - **Maven Central** (Java) — e.g. "com.google.guava:guava" or
        "org.apache.logging.log4j:log4j-core:2.14.1"

    The ecosystem is auto-detected from the name (a Maven coordinate has a ':' or
    a reverse-domain group id). Pass ecosystem="pypi" or "maven" to force it.

    Args:
        package_name: the dependency name from the failing step.
        ecosystem: "auto" (default), "pypi", or "maven".
    """
    eco = ecosystem if ecosystem in ("pypi", "maven") else _detect_ecosystem(package_name)
    return _check_maven(package_name) if eco == "maven" else _check_pypi(package_name)


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
    fix suggested LAST time. This is the agent's persistent memory across runs,
    so a recurring failure is recognised.

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
    important = [ln for ln in lines if _ERROR_LINE.search(ln)]
    shown = important[:80] or lines[-80:]
    return {
        "artifact_type": "github_actions_log",
        "repo": repo,
        "run_id": str(run_id),
        "line_count": len(lines),
        "log_text": "\n".join(shown[:120]),
    }


# --------------------------------------------------------------------------
# Inspect the files changed in a PR — tie the failure to the actual change
# --------------------------------------------------------------------------
def get_pr_changes(repo: str, pr_number: str) -> dict:
    """List the files changed in a pull request (and a trimmed diff), so the
    review can tie the failure to the specific code change that caused it.

    Args:
        repo: 'owner/repo' (e.g. 'NavyaSivakoti/demo-app').
        pr_number: the pull-request number.
    """
    try:
        names = subprocess.run(
            ["gh", "pr", "diff", str(pr_number), "--repo", repo, "--name-only"],
            capture_output=True, text=True, timeout=60,
        )
        diff = subprocess.run(
            ["gh", "pr", "diff", str(pr_number), "--repo", repo],
            capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError:
        return {"error": "gh CLI not available in this environment"}
    except subprocess.TimeoutExpired:
        return {"error": "gh timed out"}
    if names.returncode != 0:
        return {"error": f"gh failed: {names.stderr[:200]}"}

    changed = [f for f in names.stdout.splitlines() if f.strip()]
    diff_lines = redact_secrets(diff.stdout).splitlines()
    if len(diff_lines) > 150:
        diff_lines = diff_lines[:150] + [f"... ({len(diff_lines) - 150} more diff lines) ..."]
    return {
        "repo": repo,
        "pr_number": str(pr_number),
        "changed_files": changed[:50],
        "diff": "\n".join(diff_lines),
    }
