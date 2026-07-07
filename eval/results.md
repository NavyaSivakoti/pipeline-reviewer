# Evaluation results

## Per-case (deterministic)

| Case | Type | Security | Fix | Sections | No-secret |
|------|:----:|:--------:|:---:|:--------:|:---------:|
| gha_dependency | PASS | PASS | PASS | PASS | PASS |
| payments_test | PASS | PASS | PASS | PASS | PASS |
| jenkins_auth | PASS | PASS | PASS | PASS | PASS |
| flaky_test | PASS | PASS | PASS | PASS | PASS |
| lint_only | PASS | PASS | PASS | PASS | PASS |
| docker_build | PASS | PASS | PASS | PASS | PASS |
| integration_db | FAIL | PASS | PASS | PASS | PASS |
| maven_dependency | PASS | PASS | PASS | PASS | PASS |
| deploy_readiness | PASS | PASS | PASS | PASS | PASS |
| config_missing_env | PASS | PASS | PASS | PASS | PASS |
| ambiguous_unknown | PASS | PASS | PASS | PASS | PASS |

## Per-case (Claude Haiku judge)

| Case | Root cause | Fix | Notes |
|------|:----------:|:---:|-------|
| gha_dependency | 2/2 | 2/2 | The agent correctly identified the root cause: a typo in requirements.txt where `requests` was misspelled as `reqests`, causing pip to fail with 'No matching distribution found'. |
| payments_test | 2/2 | 2/2 | The agent correctly identified that the mock payment gateway does not support the 'currency' field and that this causes the GatewayError 500 when test_checkout calls create_charge with that parameter. |
| jenkins_auth | 1/2 | 1/2 | The agent correctly identifies that the auth token was rejected (401 response), but frames it ambiguously as "token validation logic or test setup" when the reference clearly indicates the root cause is the token validation/auth handling in the application itself, not the test setup. |
| flaky_test | 2/2 | 1/2 | The agent correctly identified that the test is a known flaky test that times out connecting to search-service, matching the reference cause of a timing/environmental issue rather than a code defect. |
| lint_only | 2/2 | 2/2 | The agent correctly identified that ruff detected an unused import 'os' on line 12 and a line-length violation on line 40, matching the reference root cause exactly. |
| docker_build | 2/2 | 2/2 | The agent correctly identified that the python:3.11-slim base image lacks libpq-dev and gcc/build tools needed to compile psycopg2 C extensions, matching the reference root cause exactly. |
| integration_db | 2/2 | 2/2 | The agent correctly identified that integration tests ran immediately after docker-compose started without waiting for the database to finish initialization, causing a connection refused error due to the missing healthcheck/wait mechanism. |
| maven_dependency | 2/2 | 2/2 | The agent correctly identified that the artifact ID is misspelled as `jackson-databin` instead of `jackson-databind`, which prevents Maven Central from resolving the dependency. |
| deploy_readiness | 2/2 | 1/2 | The agent correctly identifies that the readiness probe failed to pass within the timeout period, causing the deployment rollback, and correctly locates the failure in the application health/startup rather than the build process. |
| config_missing_env | 2/2 | 2/2 | The agent correctly identified that the staging environment is missing the required DATABASE_URL and REDIS_HOST environment variables, matching the reference root cause exactly. |
| ambiguous_unknown | 2/2 | 2/2 | The agent correctly identifies that the log lacks sufficient detail to determine a specific root cause, matching the reference assessment that evidence is insufficient. |

## Scorecard (metric vs threshold)

| Metric | Score | Threshold | Result |
|--------|:-----:|:---------:|:------:|
| Failure-type accuracy | 10/11 | >= 9 | PASS |
| Security-flag accuracy | 11/11 | >= 10 | PASS |
| Fix suggested | 11/11 | >= 10 | PASS |
| All sections present | 11/11 | >= 11 | PASS |
| No secret leaked | 11/11 | >= 11 | PASS |
| Root-cause correctness | 21/22 | >= 16 | PASS |
| Fix quality | 19/22 | >= 14 | PASS |

**OVERALL: PASS**
