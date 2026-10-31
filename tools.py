"""
tools.py — the agent's tools.

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
# Security guardrail: redact secrets before any text reaches the model
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


# High-confidence failure markers across CI tools and languages. A line that
# matches one of these is almost certainly part of the failure.
_STRONG_ERROR = re.compile(
    r"##\[error\]|^\s*E\s{2,}|^FAILED\b|\bFAILED\s+\S+::|Traceback \(most recent call last\)|"
    r"^\s*Caused by:|\[ERROR\]|BUILD FAILURE|BUILD FAILED|npm ERR!|^\s*fatal:|"
    r"\bexit(?:ed with)? code [1-9]\d*|returned a non-zero code|"
    r"\b\w+(?:Error|Exception)\b:|\berror(?:\[\w+\])?:|ERROR:|"
    r"^Killed$|out of memory|OOMKilled|segmentation fault|core dumped|"
    r"cannot find symbol|no module named|connection refused|"
    r"ImagePullBackOff|CrashLoopBackOff|"
    r"Execution failed for task|\bTask :\S+ FAILED|could not (?:find|resolve)\b|"
    r"^Finished: FAILURE|<<< (?:FAILURE|ERROR)!",
    re.I | re.M,
)

# Lines that contain an error word but report success or name a file/flag.
_FALSE_POSITIVE = re.compile(
    r"\b(?:0|no) (?:errors?|failures?|failed|tests? failed)\b|"
    r"\b(?:errors?|failures?|failed)\s*[:=]\s*0\b|"
    r"\b\w*(?:error|fail)\w*\.(?:py|java|js|ts|go|rb|kt|cs)\b|"
    r"continue-on-error|fail-fast|ignore[-_]errors|--fail-on|-Werror|"
    r"\bpassed\b.*\b0 failed\b",
    re.I,
)

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_LEADING_TIMESTAMP = re.compile(
    r"^﻿?(?:\[?\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\]?) ?"
)


def _clean_line(line: str) -> str:
    return _LEADING_TIMESTAMP.sub("", _ANSI.sub("", line))


def _error_strength(line: str) -> int:
    """2 = strong failure marker, 1 = weak hint (an error-ish word), 0 = none."""
    if _FALSE_POSITIVE.search(line):
        return 0
    if _STRONG_ERROR.search(line):
        return 2
    if _ERROR_LINE.search(line):
        return 1
    return 0


_LOG_LIMIT = 200          # logs at or under this many lines are returned whole
_LOG_HEAD = 10            # first lines (tool / runner versions)
_LOG_TAIL = 60            # last lines (the final failure + exit code)
_WIN_BEFORE, _WIN_AFTER = 6, 3
_LOG_BUDGET = 260         # max lines returned for a long log
_FIRST_ERRORS = 5         # earliest strong errors always kept (root cause of cascades)


def _select_log_lines(lines: list) -> tuple:
    """Pick which lines of a long log to keep. Returns (kept_indices, stats)."""
    n = len(lines)
    strengths = [_error_strength(ln) for ln in lines]
    strong = [i for i, s in enumerate(strengths) if s == 2]
    weak = [i for i, s in enumerate(strengths) if s == 1]
    hits = strong or weak   # fall back to weak hints only if nothing strong

    keep = set(range(min(_LOG_HEAD, n))) | set(range(max(0, n - _LOG_TAIL), n))

    def window(i):
        return set(range(max(0, i - _WIN_BEFORE), min(n, i + _WIN_AFTER + 1)))

    # The earliest errors usually hold the root cause (later ones cascade);
    # after those, add windows from the END backwards until the budget is spent.
    order = hits[:_FIRST_ERRORS] + list(reversed(hits[_FIRST_ERRORS:]))
    for i in order:
        w = window(i)
        if len(keep | w) > _LOG_BUDGET:
            continue
        keep |= w
    return keep, {"strong_error_lines": len(strong), "weak_error_lines": len(weak)}


def _render(lines: list, keep: set) -> list:
    out, prev = [], -1
    for i in sorted(keep):
        if i != prev + 1:
            out.append(f"... ({i - prev - 1} lines omitted) ...")
        out.append(lines[i])
        prev = i
    if prev < len(lines) - 1:
        out.append(f"... ({len(lines) - 1 - prev} lines omitted) ...")
    return out


def read_log(file_path: str) -> dict:
    """Read any CI/CD or build/console log and return its redacted text.

    Use for any non-XML log from any CI tool. Secrets are stripped first, along
    with ANSI colour codes and leading timestamps. Logs over 200 lines are
    excerpted: the first 10 lines, the last 60, and a window of context around
    each real error line (strong markers like '##[error]', 'Traceback',
    'BUILD FAILURE', 'Caused by:'; weak hints only if there are no strong
    ones). Lines like '0 errors' or 'error_handler.py' are not treated as
    errors. The earliest errors are always kept, since later ones often cascade
    from the first.

    Args:
        file_path: path to the log / console-output file.
    """
    try:
        with open(_resolve(file_path), errors="replace") as f:
            raw = f.read()
    except FileNotFoundError:
        return {"error": f"log file not found: {file_path}"}

    lines = [_clean_line(ln) for ln in redact_secrets(raw).splitlines()]
    total = len(lines)
    result = {"artifact_type": "raw_log", "line_count": total}
    if total <= _LOG_LIMIT:
        result["log_text"] = "\n".join(lines)
        return result

    keep, stats = _select_log_lines(lines)
    result.update(stats)
    result["lines_shown"] = len(keep)
    result["log_text"] = "\n".join(_render(lines, keep))
    return result


# --------------------------------------------------------------------------
# JUnit / pytest XML parser (universal across CI tools)
# --------------------------------------------------------------------------
_MAX_REPORTED_TESTS = 25   # cap per category so a mass failure can't flood the context
_TRACE_HEAD, _TRACE_TAIL = 1000, 500
_OUTPUT_TAIL = 600


def _clip_trace(text: str) -> str:
    """Keep the top of a stack trace (where it failed) AND the bottom (the
    'Caused by' chain in Java), dropping only the middle."""
    text = redact_secrets((text or "").strip())
    if len(text) <= _TRACE_HEAD + _TRACE_TAIL:
        return text
    return text[:_TRACE_HEAD] + "\n... (trace trimmed) ...\n" + text[-_TRACE_TAIL:]


def _captured_output(case, tag: str) -> str:
    """Last part of a testcase's <system-out>/<system-err>, redacted."""
    el = case.find(tag)
    if el is None or not (el.text or "").strip():
        return ""
    return redact_secrets(el.text.strip())[-_OUTPUT_TAIL:]


def _problem_entry(case, el, kind: str) -> dict:
    entry = {
        "name": case.get("name"),
        "classname": case.get("classname"),
        "kind": kind,                       # "failure" | "error" | "flaky"
        "type": el.get("type") or "",       # e.g. AssertionError, NullPointerException
        "message": redact_secrets(el.get("message") or ""),
        "trace": _clip_trace(el.text),
    }
    if case.get("file"):
        entry["file"] = case.get("file")
    if case.get("line"):
        entry["line"] = case.get("line")
    out, err = _captured_output(case, "system-out"), _captured_output(case, "system-err")
    if out:
        entry["stdout_tail"] = out
    if err:
        entry["stderr_tail"] = err
    return entry


def parse_junit_results(file_path: str) -> dict:
    """Parse a JUnit/pytest/Surefire XML report and list the problem tests.

    Distinguishes:
      - failures: an assertion did not hold (<failure>).
      - errors:   the test crashed before/while running, e.g. an exception in
                  setup, a fixture, or an import (<error>) — often an
                  environment or config problem rather than wrong logic.
      - flaky:    failed at first but passed on rerun (Maven Surefire
                  <flakyFailure>/<flakyError>).
      - skipped:  counted, not listed.

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
    total = failures = errors = skipped = flaky = 0
    failed_tests, errored_tests, flaky_tests = [], [], []
    for case in root.iter("testcase"):
        total += 1
        fail, err = case.find("failure"), case.find("error")
        flaky_el = case.find("flakyFailure")
        if flaky_el is None:
            flaky_el = case.find("flakyError")
        if fail is not None:
            failures += 1
            if len(failed_tests) < _MAX_REPORTED_TESTS:
                failed_tests.append(_problem_entry(case, fail, "failure"))
        elif err is not None:
            errors += 1
            if len(errored_tests) < _MAX_REPORTED_TESTS:
                errored_tests.append(_problem_entry(case, err, "error"))
        elif flaky_el is not None:
            flaky += 1
            if len(flaky_tests) < _MAX_REPORTED_TESTS:
                flaky_tests.append(_problem_entry(case, flaky_el, "flaky"))
        elif case.find("skipped") is not None:
            skipped += 1

    result = {
        "artifact_type": "junit_results",
        "total_tests": total,
        "passed": total - failures - errors - skipped,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "flaky_passed_on_rerun": flaky,
        "failed_tests": failed_tests,
        "errored_tests": errored_tests,
    }
    if flaky_tests:
        result["flaky_tests"] = flaky_tests
    listed = len(failed_tests) + len(errored_tests)
    if failures + errors > listed:
        result["note"] = f"showing {listed} of {failures + errors} failing/erroring tests"
    return result


# --------------------------------------------------------------------------
# Supply-chain check: LIVE PyPI lookup + typosquat heuristic
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
    """Check a dependency for supply-chain risk.

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
# Memory / recurrence detection — persists across runs
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
_CONTEXT_BEFORE, _CONTEXT_AFTER = 8, 4   # lines kept around each error line
_STEP_TAIL = 25                           # last lines of a failed step (exit code lives here)
_MAX_STEP_LINES = 120
_MAX_TOTAL_LINES = 300


def _gh(args: list, timeout: int = 60):
    return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)


def _split_gha_line(line: str) -> tuple:
    """`gh run view --log` prints 'JOB<TAB>STEP<TAB>TIMESTAMP text'. Return
    (job, step, text) with the timestamp and ANSI colour codes stripped."""
    parts = line.split("\t", 2)
    if len(parts) == 3:
        job, step, text = parts
    else:
        job, step, text = "", "", line
    return job.strip(), step.strip(), _clean_line(text)


def _excerpt(lines: list) -> list:
    """Windows of context around error lines, plus the step's tail, with
    gaps marked. The tail matters: the real cause often sits just above
    'Process completed with exit code N'."""
    n = len(lines)
    keep = set(range(max(0, n - _STEP_TAIL), n))
    strengths = [_error_strength(ln) for ln in lines]
    best = 2 if 2 in strengths else 1   # weak hints only when nothing is strong
    for i, s in enumerate(strengths):
        if s == best:
            keep.update(range(max(0, i - _CONTEXT_BEFORE), min(n, i + _CONTEXT_AFTER + 1)))
    out, prev = [], -1
    for i in sorted(keep):
        if i != prev + 1:
            out.append(f"... ({i - prev - 1} lines omitted) ...")
        out.append(lines[i])
        prev = i
    if len(out) > _MAX_STEP_LINES:   # keep the end, where the failure surfaces
        out = [f"... (excerpt trimmed to last {_MAX_STEP_LINES} lines) ..."] + out[-_MAX_STEP_LINES:]
    return out


def _run_metadata(repo: str, run_id: str) -> dict:
    """Workflow name, branch, commit and which jobs/steps failed. Best-effort."""
    try:
        r = _gh(["run", "view", str(run_id), "--repo", repo, "--json",
                 "workflowName,event,headBranch,headSha,conclusion,jobs"])
        if r.returncode != 0:
            return {}
        data = json.loads(r.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        return {}
    failed_jobs = []
    for job in data.get("jobs") or []:
        if job.get("conclusion") in ("failure", "timed_out", "cancelled"):
            failed_jobs.append({
                "job": job.get("name"),
                "conclusion": job.get("conclusion"),
                "failed_steps": [s.get("name") for s in job.get("steps") or []
                                 if s.get("conclusion") in ("failure", "timed_out")],
            })
    return {
        "workflow": data.get("workflowName"),
        "event": data.get("event"),
        "branch": data.get("headBranch"),
        "commit": (data.get("headSha") or "")[:12],
        "conclusion": data.get("conclusion"),
        "failed_jobs": failed_jobs,
    }


def fetch_github_actions_log(repo: str, run_id: str) -> dict:
    """Fetch a failed GitHub Actions run's logs directly, via the gh CLI.

    Lets the agent review a run WITHOUT downloading a file first. Pulls only
    the FAILED steps (`--log-failed`, falling back to the full log), groups the
    output by job and step, and keeps context around each error plus the end
    of each failed step. Also returns run metadata (workflow, branch, commit,
    which jobs/steps failed). Secrets are redacted.

    Args:
        repo: 'owner/repo' (e.g. 'NavyaSivakoti/demo-app').
        run_id: the numeric run id.
    """
    source = "failed_steps"
    try:
        out = _gh(["run", "view", str(run_id), "--repo", repo, "--log-failed"])
        if out.returncode != 0 or not out.stdout.strip():
            source = "full_log"
            out = _gh(["run", "view", str(run_id), "--repo", repo, "--log"])
    except FileNotFoundError:
        return {"error": "gh CLI not available in this environment"}
    except subprocess.TimeoutExpired:
        return {"error": "gh run view timed out"}
    if out.returncode != 0:
        return {"error": f"gh failed: {out.stderr[:200]}"}

    raw_lines = redact_secrets(out.stdout).splitlines()
    steps: dict = {}   # (job, step) -> lines, in log order
    for line in raw_lines:
        job, step, text = _split_gha_line(line)
        steps.setdefault((job, step), []).append(text)

    failed_steps, rendered, budget = [], [], _MAX_TOTAL_LINES
    for (job, step), lines in steps.items():
        excerpt = _excerpt(lines)
        failed_steps.append({"job": job, "step": step, "line_count": len(lines)})
        header = f"### {job} / {step}" if (job or step) else "### log"
        rendered.append((header, excerpt))
    # Fill the line budget from the LAST step backwards: the failure is usually last.
    out_lines = []
    for header, excerpt in reversed(rendered):
        if budget <= 0:
            out_lines.insert(0, "... (earlier steps omitted to fit the budget) ...")
            break
        block = [header] + excerpt[-budget:]
        budget -= len(block)
        out_lines = block + out_lines

    result = {
        "artifact_type": "github_actions_log",
        "repo": repo,
        "run_id": str(run_id),
        "source": source,
        "line_count": len(raw_lines),
        "steps": failed_steps,
        "log_text": "\n".join(out_lines),
    }
    meta = _run_metadata(repo, run_id)
    if meta:
        result["run"] = meta
    return result


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
