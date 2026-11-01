"""read_log truncation: a mid-log error must survive; short logs are untouched."""

import tools


def test_mid_log_error_is_surfaced(tmp_path):
    # 300-line log with the real error stranded at line 210 — inside the
    # omitted middle for the first-120 / last-80 window.
    lines = [f"processing item {i}" for i in range(1, 301)]
    lines[209] = "ERROR: database connection refused at pool.py:42"  # line 210
    p = tmp_path / "big.log"
    p.write_text("\n".join(lines))

    out = tools.read_log(str(p))
    assert out["line_count"] == 300
    assert "database connection refused" in out["log_text"], "mid-log error was dropped"


def test_java_and_native_errors_are_surfaced(tmp_path):
    # Mid-log failures phrased WITHOUT a traditional error word must still be
    # caught by the widened Python/Java keyword set.
    lines = [f"processing item {i}" for i in range(1, 301)]
    lines[150] = "Caused by: the connection pool was exhausted"   # Java root cause
    lines[209] = "Killed"                                          # OOM kill
    p = tmp_path / "big.log"
    p.write_text("\n".join(lines))

    out = tools.read_log(str(p))["log_text"]
    assert "connection pool was exhausted" in out
    assert "Killed" in out


def test_short_log_is_not_truncated(tmp_path):
    p = tmp_path / "small.log"
    p.write_text("line one\nERROR here\nline three")
    out = tools.read_log(str(p))["log_text"]
    assert "ERROR here" in out
    assert "omitted" not in out  # no truncation marker for short logs


def _write(tmp_path, lines):
    p = tmp_path / "big.log"
    p.write_text("\n".join(lines))
    return str(p)


def test_setup_noise_at_top_is_skipped(tmp_path):
    # The old reader always sent the first 120 lines (checkout/setup noise).
    lines = [f"setup: downloading action {i}" for i in range(400)]
    lines[350] = "E   AssertionError: totals differ"
    out = tools.read_log(_write(tmp_path, lines))
    assert "totals differ" in out["log_text"]
    assert "setup: downloading action 50\n" not in out["log_text"]
    assert out["lines_shown"] < 120


def test_success_counts_and_filenames_are_not_errors(tmp_path):
    lines = [f"step {i}" for i in range(400)]
    lines[60] = "ruff: Found 0 errors."
    lines[90] = "Compiling src/error_handler.py"
    lines[120] = "pytest: 212 passed, 0 failed"
    lines[150] = "jobs: continue-on-error enabled"
    lines[300] = "FAILED tests/test_api.py::test_create - KeyError: 'id'"
    out = tools.read_log(_write(tmp_path, lines))
    text = out["log_text"]
    assert "KeyError: 'id'" in text
    for noise in ("Found 0 errors", "error_handler.py", "0 failed", "continue-on-error"):
        assert noise not in text, f"false positive surfaced: {noise}"
    assert out["strong_error_lines"] == 1


def test_strong_errors_win_over_weak_hints(tmp_path):
    lines = [f"work {i}" for i in range(400)]
    for i in range(20, 200, 10):
        lines[i] = f"config: request timeout set to {i}s"     # weak hint, not a failure
    lines[250] = "##[error]Process completed with exit code 2."
    text = tools.read_log(_write(tmp_path, lines))["log_text"]
    assert "exit code 2" in text
    assert "timeout set to 100s" not in text


def test_weak_hints_used_when_nothing_strong(tmp_path):
    lines = [f"work {i}" for i in range(400)]
    lines[200] = "waiting for service... timed out"
    text = tools.read_log(_write(tmp_path, lines))["log_text"]
    assert "timed out" in text


def test_first_error_kept_in_a_cascade(tmp_path):
    # The first error is the root cause; 150 later errors cascade from it.
    lines = [f"work {i}" for i in range(1000)]
    lines[100] = "ERROR: could not resolve host registry.internal"
    for i in range(400, 850, 3):
        lines[i] = f"ERROR: module {i} failed to load"
    out = tools.read_log(_write(tmp_path, lines))
    text = out["log_text"]
    assert "could not resolve host" in text
    assert out["lines_shown"] <= 260


def test_ansi_and_timestamps_are_stripped(tmp_path):
    lines = [f"2026-10-31T22:00:{i % 60:02d}.123Z \x1b[32mok {i}\x1b[0m" for i in range(10)]
    lines.append("[2026-10-31 22:01:00] \x1b[31mERROR: boom\x1b[0m")
    text = tools.read_log(_write(tmp_path, lines))["log_text"]
    assert "\x1b[" not in text
    assert "2026-10-31" not in text
    assert "ERROR: boom" in text
