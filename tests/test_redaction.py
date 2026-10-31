"""
Security tests: prove that secrets are redacted BEFORE any text reaches the model.

Covers the redaction function directly, and both model-input paths (read_log and
parse_junit_results). Run with:  pytest

NOTE: the secrets below are FAKE, made-up test values - not real credentials.
`AKIAIOSF••••••••••••` is AWS's public documentation example key. They exist only so
the test can feed secret-shaped strings into the redactor and assert that none survive.
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


def test_redaction_does_not_span_lines():
    """A benign 'password:' in a code diff must not swallow the next line,
    while a real single-line secret assignment is still redacted."""
    code = "if user == password:\n    return True"
    out = tools.redact_secrets(code)
    assert "return True" in out, "redaction over-matched across the newline"

    leak = "password=hunter2secretvalue"
    assert "[REDACTED]" in tools.redact_secrets(leak), "real secret not redacted"


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


# --------------------------------------------------------------------------
# Wider coverage. Fake values are assembled at runtime ("x" + "y") so this file
# never contains a literal token-shaped string that secret scanners would flag.
# --------------------------------------------------------------------------
_FILL = "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv1Wx2Yz3"   # 40 chars of fake token body

NEW_FORMATS = {
    "github_fine_grained": "github" + "_pat_" + "11ABCDEFG0" + _FILL[:30] + "_" + _FILL[:20],
    "github_oauth": "gh" + "o_" + _FILL[:36],
    "github_app": "gh" + "s_" + _FILL[:36],
    "gitlab": "gl" + "pat-" + _FILL[:20],
    "slack_bot": "xo" + "xb-1234567890-" + _FILL[:24],
    "stripe": "sk" + "_live_" + _FILL[:24],
    "npm": "np" + "m_" + _FILL[:36],
    "pypi": "py" + "pi-" + "AgEIcHlwaS5vcmc" + _FILL + _FILL[:10],
    "docker_hub": "dc" + "kr_pat_" + _FILL[:27],
    "anthropic": "sk" + "-ant-api03-" + _FILL[:32],
    "openai_project": "sk" + "-proj-" + _FILL[:20] + "_" + _FILL[:20],
    "aws_session": "AS" + "IA" + "ABCDEFGHIJ234567",
    "google_oauth": "ya" + "29." + _FILL[:30],
    "jwt": "ey" + "JhbGciOiJIUzI1NiJ9." + "ey" + "JzdWIiOiIxMjM0NTY3ODkwIn0." + "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV",
}


def test_new_token_formats_are_redacted():
    for name, secret in NEW_FORMATS.items():
        out = tools.redact_secrets(f"using {secret} for auth")
        assert secret not in out, f"{name} leaked"
        assert "[REDACTED]" in out, f"{name} was not redacted"


def test_pem_private_key_block_is_redacted():
    body = "\n".join(_FILL + _FILL for _ in range(5))
    pem = "-----BEGIN " + "RSA PRIVATE KEY-----\n" + body + "\n-----END " + "RSA PRIVATE KEY-----"
    out = tools.redact_secrets(f"writing key:\n{pem}\nnext step")
    assert _FILL not in out
    assert "next step" in out, "redaction must stop at the END line"


def test_truncated_pem_is_still_redacted():
    pem = "-----BEGIN " + "OPENSSH PRIVATE KEY-----\n" + _FILL + _FILL
    assert _FILL not in tools.redact_secrets(pem)


def test_url_credentials_keep_scheme_and_host():
    url = "postgres://app_user:" + "Sup3rS3cret" + "@db.internal:5432/orders"
    out = tools.redact_secrets(f"connecting to {url}")
    assert "Sup3rS3cret" not in out and "app_user" not in out
    assert "postgres://[REDACTED]@db.internal:5432/orders" in out


def test_basic_auth_and_azure_keys():
    basic = "Authorization: Basic " + "dXNlcjpwYXNzd29yZDEyMzQ="
    azure = "DefaultEndpointsProtocol=https;AccountName=acct;AccountKey=" + _FILL + "==;EndpointSuffix=core"
    sas = "https://acct.blob.core.windows.net/c/f?sv=2022&sig=" + _FILL + "%3D&se=2026"
    out = tools.redact_secrets("\n".join([basic, azure, sas]))
    assert "dXNlcjpw" not in out
    assert _FILL not in out
    assert "AccountName=acct" in out and "AccountKey=[REDACTED]" in out
    assert "sig=[REDACTED]&se=2026" in out


def test_assignment_keeps_key_name():
    out = tools.redact_secrets("DB_PASSWORD=hunter2value CLIENT_SECRET: abc123def")
    assert "hunter2value" not in out and "abc123def" not in out
    assert "DB_PASSWORD=[REDACTED]" in out
    assert "CLIENT_SECRET: [REDACTED]" in out


def test_json_secret_value_is_redacted():
    out = tools.redact_secrets('{"user": "svc", "api_key": "' + _FILL + '", "region": "us-west-2"}')
    assert _FILL not in out
    assert '"api_key": "[REDACTED]"' in out and '"region": "us-west-2"' in out


def test_masked_references_and_empty_values_are_kept():
    # These are useful evidence (e.g. "the secret is empty" IS the bug) and
    # contain no secret, so they must pass through untouched.
    lines = [
        "API_TOKEN=***",
        "password: ${{ secrets.DB_PASSWORD }}",
        "SECRET_KEY=${SECRET_KEY}",
        '"token": ""',
        "Error: input 'api-key' is required but was empty",
    ]
    for line in lines:
        assert tools.redact_secrets(line) == line, f"over-redacted: {line}"


def test_redaction_is_fast_on_hostile_input():
    # Fork-PR logs are attacker-controlled; long tokens used to trigger
    # quadratic regex backtracking (16s for this input).
    import time
    start = time.time()
    tools.redact_secrets("a." * 10000 + "\n" + "a-" * 10000)
    assert time.time() - start < 1.0
