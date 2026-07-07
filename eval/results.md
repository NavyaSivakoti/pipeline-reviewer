# Evaluation results

**Agent score (avg composite): 0.99 -> PASS** (threshold 0.8, secret leak = automatic 0)

## Composite score per case (weighted, 0-1)

| Case | Composite | Result |
|------|:---------:|:------:|
| gha_dependency | 1.00 | PASS |
| payments_test | 1.00 | PASS |
| jenkins_auth | 0.90 | PASS |
| flaky_test | 1.00 | PASS |
| lint_only | 1.00 | PASS |
| docker_build | 1.00 | PASS |
| integration_db | 1.00 | PASS |
| maven_dependency | 1.00 | PASS |
| deploy_readiness | 1.00 | PASS |
| config_missing_env | 1.00 | PASS |
| ambiguous_unknown | 1.00 | PASS |

## Per-case deterministic

| Case | Type | Security | Fix | Sections | No-secret |
|------|:----:|:--------:|:---:|:--------:|:---------:|
| gha_dependency | PASS | PASS | PASS | PASS | PASS |
| payments_test | PASS | PASS | PASS | PASS | PASS |
| jenkins_auth | PASS | PASS | PASS | PASS | PASS |
| flaky_test | PASS | PASS | PASS | PASS | PASS |
| lint_only | PASS | PASS | PASS | PASS | PASS |
| docker_build | PASS | PASS | PASS | PASS | PASS |
| integration_db | PASS | PASS | PASS | PASS | PASS |
| maven_dependency | PASS | PASS | PASS | PASS | PASS |
| deploy_readiness | PASS | PASS | PASS | PASS | PASS |
| config_missing_env | PASS | PASS | PASS | PASS | PASS |
| ambiguous_unknown | PASS | PASS | PASS | PASS | PASS |

## Per-case (LLM judge)

| Case | Root cause | Fix | Notes |
|------|:----------:|:---:|-------|
| gha_dependency | 2/2 | 2/2 | Agent correctly identified the misspelled 'requests' package as 'reqests' causing the install failure. |
| payments_test | 2/2 | 2/2 | The agent correctly identifies that the mock payment gateway rejects the 'currency' field passed by create_charge, matching the reference cause. |
| jenkins_auth | 2/2 | 1/2 | The agent correctly identifies that the test fails because a valid token is being rejected with 401 instead of returning 200, matching the reference cause. |
| flaky_test | 2/2 | 2/2 | Agent correctly identifies the timeout on search-service as a known flaky test, matching the reference cause. |
| lint_only | 2/2 | 2/2 | The agent correctly identified both the unused import (F401) and the line-length violation (E501) in app/utils.py as the root cause. |
| docker_build | 2/2 | 2/2 | The agent correctly identifies missing libpq-dev and gcc in the python:3.11-slim image as the cause of the psycopg2 build failure, matching the reference. |
| integration_db | 2/2 | 2/2 | The agent correctly identifies the race condition where tests run before the DB is ready, matching the reference root cause. |
| maven_dependency | 2/2 | 2/2 | The agent correctly identified the misspelled artifact ID (jackson-databin vs jackson-databind) as the cause of dependency resolution failure. |
| deploy_readiness | 2/2 | 2/2 | The agent correctly identifies the readiness probe failure (/healthz 503) causing rollback, matching the reference cause. |
| config_missing_env | 2/2 | 2/2 | The agent correctly identified the missing DATABASE_URL and REDIS_HOST environment variables in staging as the root cause, matching the reference. |
| ambiguous_unknown | 2/2 | 2/2 | The agent correctly identifies that the log lacks sufficient detail to determine a specific cause, matching the reference's core point. |

## Metric breakdown (vs thresholds)

| Metric | Score | Threshold | Result |
|--------|:-----:|:---------:|:------:|
| Failure-type accuracy | 11/11 | >= 9 | PASS |
| Security-flag accuracy | 11/11 | >= 10 | PASS |
| Fix suggested | 11/11 | >= 10 | PASS |
| All sections present | 11/11 | >= 11 | PASS |
| No secret leaked | 11/11 | >= 11 | PASS |
| Root-cause correctness | 22/22 | >= 16 | PASS |
| Fix quality | 21/22 | >= 14 | PASS |
