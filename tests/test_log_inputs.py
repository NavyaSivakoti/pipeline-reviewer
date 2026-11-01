"""
Awkward log inputs: giant single lines, gzip and zip (GitHub's log download),
compression bombs, UTF-16 text and real binaries.
"""

import gzip
import io
import json
import time
import types
import zipfile

import tools

TOKEN = "gh" + "p_" + "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv1Wx2Yz3"


# --------------------------------------------------------------------------
# Giant lines
# --------------------------------------------------------------------------
def test_giant_single_line_is_cut_but_keeps_both_ends(tmp_path):
    line = "START " + ("x" * 500_000) + " ERROR: the real failure at the end"
    p = tmp_path / "one-line.log"
    p.write_text(line)
    start = time.time()
    out = tools.read_log(str(p))
    assert time.time() - start < 2
    text = out["log_text"]
    assert "START" in text and "the real failure at the end" in text
    assert "chars cut" in text and len(text) < 3000


def test_secret_at_the_cut_boundary_is_still_redacted(tmp_path):
    # Place the token exactly where the cut happens; redaction runs first.
    line = ("y" * (tools._LINE_HEAD - 10)) + f" {TOKEN} " + ("z" * 5000)
    p = tmp_path / "boundary.log"
    p.write_text(line)
    text = tools.read_log(str(p))["log_text"]
    assert TOKEN not in text and TOKEN[:12] not in text


def test_github_actions_lines_are_cut_too(monkeypatch):
    long_line = "job\tstep\t2026-10-31T00:00:00Z " + "E " + ("q" * 100_000)
    ok = types.SimpleNamespace(returncode=0, stdout=long_line, stderr="")
    monkeypatch.setattr(tools.subprocess, "run", lambda *a, **k: ok)
    out = tools.fetch_github_actions_log("o/r", "1")
    assert "chars cut" in out["log_text"] and len(out["log_text"]) < 4000


# --------------------------------------------------------------------------
# gzip
# --------------------------------------------------------------------------
def test_gzip_log_is_decompressed(tmp_path):
    p = tmp_path / "build.log.gz"
    p.write_bytes(gzip.compress(b"step 1 ok\nERROR: No matching distribution found for reqests\n"))
    out = tools.read_log(str(p))
    assert "No matching distribution found for reqests" in out["log_text"]
    assert out["fingerprint"]


def test_gzip_bomb_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_MAX_LOG_BYTES", 1_000_000)
    p = tmp_path / "bomb.log.gz"
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as g:
        chunk = b"A" * 1_000_000
        for _ in range(200):          # 200 MB decompressed, ~200 KB on disk
            g.write(chunk)
    p.write_bytes(buf.getvalue())
    start = time.time()
    out = tools.read_log(str(p))
    assert time.time() - start < 5
    assert "truncated at the size limit" in out["log_text"]


def test_corrupt_gzip_is_an_error(tmp_path):
    p = tmp_path / "bad.gz"
    p.write_bytes(b"\x1f\x8b\x08\x00garbage-not-really-gzip")
    assert "could not decompress" in tools.read_log(str(p))["error"]


# --------------------------------------------------------------------------
# zip (GitHub's "Download log archive")
# --------------------------------------------------------------------------
def _zip(path, members):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)


def test_github_log_archive_is_read_member_by_member(tmp_path):
    p = tmp_path / "logs_123.zip"
    _zip(p, {
        "1_Set up job.txt": "Runner image: ubuntu-24.04\n",
        "test/3_Run tests.txt": "collected 10 items\nE   AssertionError: assert 1 == 2\n",
        "screenshot.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
    })
    out = tools.read_log(str(p))
    text = out["log_text"]
    assert "===== 1_Set up job.txt =====" in text
    assert "===== test/3_Run tests.txt =====" in text
    assert "AssertionError: assert 1 == 2" in text
    assert "screenshot.png" not in text, "binary members are skipped"


def test_zip_without_text_logs_is_an_error(tmp_path):
    p = tmp_path / "images.zip"
    _zip(p, {"a.png": b"\x89PNG" + b"\x00" * 10})
    assert "no text logs" in tools.read_log(str(p))["error"]


def test_zip_bomb_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_MAX_LOG_BYTES", 500_000)
    p = tmp_path / "bomb.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(3):
            z.writestr(f"{i}.txt", b"B" * 50_000_000)    # 150 MB decompressed
    start = time.time()
    out = tools.read_log(str(p))
    assert time.time() - start < 5
    assert "size limit" in out["log_text"]


def test_corrupt_zip_is_an_error(tmp_path):
    p = tmp_path / "bad.zip"
    p.write_bytes(b"PK\x03\x04" + b"not a zip at all")
    assert "could not read zip" in tools.read_log(str(p))["error"]


def test_archives_still_respect_the_artifact_allowlist(tmp_path):
    allowed, other = tmp_path / "ok.log", tmp_path / "other.gz"
    allowed.write_text("x")
    other.write_bytes(gzip.compress(b"secret stuff"))
    tools.set_allowed_artifacts([str(allowed)])
    try:
        assert "refused" in tools.read_log(str(other))["error"]
    finally:
        tools.set_allowed_artifacts(None)


# --------------------------------------------------------------------------
# Encodings and binaries
# --------------------------------------------------------------------------
def test_utf16_log_is_text(tmp_path):
    p = tmp_path / "powershell.log"
    p.write_bytes("Build started\r\nERROR: MSBuild failed with exit code 1\r\n".encode("utf-16"))
    out = tools.read_log(str(p))
    assert "MSBuild failed" in out["log_text"]


def test_binary_file_is_refused(tmp_path):
    p = tmp_path / "artifact.bin"
    p.write_bytes(b"\x7fELF" + bytes(range(256)) * 40)
    out = tools.read_log(str(p))
    assert out["error"] == "not a text log (binary content)"


def test_text_with_an_occasional_nul_is_still_text(tmp_path):
    p = tmp_path / "mostly-text.log"
    p.write_bytes(b"line one\n" * 500 + b"stray\x00byte\n" + b"ERROR: boom\n")
    assert "ERROR: boom" in tools.read_log(str(p))["log_text"]


def test_tool_output_stays_json_serializable_for_odd_inputs(tmp_path):
    p = tmp_path / "weird.log"
    p.write_bytes(b"\xff\xfeE\x00R\x00R\x00")   # tiny UTF-16
    json.dumps(tools.read_log(str(p)))
