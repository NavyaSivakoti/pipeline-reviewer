---
name: pipeline-failure-review
description: Review a CI/CD pipeline failure — classify it, find the root cause, suggest a fix, and flag security risks. Use when a pipeline has failed.
---

# Pipeline Failure Review Skill

## When to use
When a CI/CD pipeline has failed and you have log output and/or test results.

## 1. Failure type (pick exactly one)
- **test_failure** — a test assertion failed or errored.
- **build_error** — a compile / **image build** step failed (the build itself).
- **dependency_failure** — a package failed to install or resolve.
- **lint_error** — only the linter/formatter failed.
- **deploy_failure** — a deploy / release / rollout step failed (readiness or liveness
  probe, rollback, `kubectl apply`, image pull / `ImagePullBackOff`). The build may have
  SUCCEEDED — the failure is in *shipping* it, not building it.
- **config_error** — misconfiguration: a missing or invalid env var, secret, or manifest,
  or config drift between environments.
- **infra_error** — the environment/runner itself failed, not the code (OOM / "Killed",
  disk full, network / DNS, an unready dependency service).
- **flaky** — fails intermittently, passes on rerun (timeouts, "flaky", "quarantined").
- **unknown** — evidence is insufficient.

Do NOT label a **deploy / config / infra** failure as `build_error` just because it ran
in a Docker or CI step — check *what actually failed*: the build, the release, or the
environment.

## 2. Root cause
One sentence, citing the specific error line. Never invent details.
State whether the failure is in code that CHANGED (likely introduced by this
change) or in UNCHANGED code (likely pre-existing, flaky, or environmental —
e.g. a dependency update or an unready service). Do not assume the change caused it.

## 3. Suggested fix
Give a concrete fix. When it's a small change, show it as a **patch/diff**:
```diff
- reqests==2.31.0
+ requests==2.31.0
```

## 4. Security / supply-chain
For a dependency failure, call `check_package` on the package name. It covers
both **Python (PyPI)** and **Java (Maven Central)** — pass the name as written
(e.g. `reqests==2.31.0` or `com.google.guava:guava`); the ecosystem is
auto-detected. If it is a typosquat or missing from the registry, add a clear
**⚠️ supply-chain risk** warning.

## 5. Confidence + verify
State confidence (High / Medium / Low) and the exact command to verify the fix
(e.g. `pytest tests/test_payments.py::test_checkout`).
