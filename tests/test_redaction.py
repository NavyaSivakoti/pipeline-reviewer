"""
Security tests: prove that secrets are redacted BEFORE any text reaches the model.

Covers the redaction function directly, and both model-input paths (read_log and
parse_junit_results). Run with:  pytest
"""

import tools

RAW_SECRETS = {
    "google_api_key": "AIzaSyA1234567890abcdefghijABCDEF12",
    "openai_key": "sk-abcdef1234567890abcdef",
    "aws_key": "AKIAIOSFODNN7EXAMPLE",
    "github_token": "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "bearer_token": "Bearer abcdef1234567890abcdeftokenvalue",
    "password": "password=SuperSecretPassw0rd",
}

# The raw values that must NEVER survive redaction.
LEAKED_MARKERS = [
    "AIzaSyA1234567890",
    "AKIAIOSFODNN7EXAMPLE",
    "ghp_abcdefghijklmnopqrstuvwxyz",
    "SuperSecretPassw0rd",
    "abcdeftokenvalue",
]


def test_each_secret_type_is_redacted():
    for name, secret in RAW_SECRETS.items():
        out = tools.redact_secrets(f"log line with {secret} embedded")
        assert "[REDACTED]" in out, f"{name} was not redacted"


def test_no_raw_secret_survives():
    out = tools.redact_secrets(" ".join(RAW_SECRETS.values()))
    for leaked in LEAKED_MARKERS:
        assert leaked not in out, f"secret leaked through redaction: {leaked}"


def test_read_log_redacts_before_model_input():
    """read_log is a model-input path — its output must be clean."""
    text = tools.read_log("sample_data/log_with_secret.log")["log_text"]
    assert "[REDACTED]" in text
    for leaked in LEAKED_MARKERS:
        assert leaked not in text, f"{leaked} would have reached the model"


def test_parse_junit_redacts_before_model_input(tmp_path):
    """parse_junit_results is a model-input path — its output must be clean."""
    xml = (
        '<?xml version="1.0"?>'
        '<testsuites><testsuite name="s" tests="1" failures="1">'
        '<testcase classname="c" name="t">'
        '<failure message="auth failed with token ghp_abcdefghijklmnopqrstuvwxyz0123456789">'
        "traceback shows password=SuperSecretPassw0rd here"
        "</failure></testcase></testsuite></testsuites>"
    )
    p = tmp_path / "junit.xml"
    p.write_text(xml)
    ft = tools.parse_junit_results(str(p))["failed_tests"][0]
    assert "[REDACTED]" in ft["message"]
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in ft["message"]
    assert "SuperSecretPassw0rd" not in ft["trace"]
