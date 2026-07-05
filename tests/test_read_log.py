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


def test_short_log_is_not_truncated(tmp_path):
    p = tmp_path / "small.log"
    p.write_text("line one\nERROR here\nline three")
    out = tools.read_log(str(p))["log_text"]
    assert "ERROR here" in out
    assert "omitted" not in out  # no truncation marker for short logs
