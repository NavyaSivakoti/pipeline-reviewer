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
| gha_dependency | 2/2 | 2/2 | The agent correctly identified that the root cause is a typo in requirements.txt where `reqests` should be `requests`, and that pip cannot find the misspelled package on PyPI. |
| payments_test | 2/2 | 1/2 | The agent correctly identified that create_charge calls payment_gateway.charge with a 'currency' field that the mocked gateway v1.2 does not support, causing the GatewayError 500. |
| jenkins_auth | 1/2 | 0/2 | The agent correctly identifies that the auth token was rejected causing a 401 instead of 200, but frames it ambiguously as "an issue with the authentication token or the authentication service" rather than clearly pinpointing that the token validation/auth handling logic is broken. |
| flaky_test | 1/2 | 0/2 | The agent correctly identifies the timeout symptom and flaky nature, but misses the key insight that this is a known environmental/timing issue, not a code defect requiring investigation of service performance. |
| lint_only | 2/2 | 2/2 | The agent correctly identified both linting failures: unused import 'os' on line 12 and line 40 exceeding 88 characters, matching the reference root cause exactly. |
| docker_build | 2/2 | 2/2 | The agent correctly identified that psycopg2 failed to build due to missing libpq-dev and build tools (gcc) in the python:3.11-slim image, matching the reference cause exactly. |
| integration_db | 2/2 | 2/2 | The agent correctly identified that the Postgres container had not finished starting and was not accepting connections before the tests ran, matching the reference root cause exactly. |
| maven_dependency | 2/2 | 2/2 | The agent correctly identified that the artifact id is misspelled as `jackson-databin` instead of `jackson-databind`, which is the exact root cause of the build failure. |
| deploy_readiness | 2/2 | 1/2 | The agent correctly identified that the /healthz endpoint returned 503 and the readiness probe failed to pass, which is the actual root cause of the deployment failure. |
| config_missing_env | 2/2 | 2/2 | The agent correctly identified that DATABASE_URL and REDIS_HOST environment variables were not set in staging, causing config validation failure due to config drift, matching the reference root cause exactly. |
| ambiguous_unknown | 1/2 | 1/2 | The agent correctly identifies that the specific root cause cannot be determined from the log, but frames it as "exit code 1 is not captured" rather than the more precise "evidence is insufficient to determine a specific cause." |

## Scorecard (metric vs threshold)

| Metric | Score | Threshold | Result |
|--------|:-----:|:---------:|:------:|
| Failure-type accuracy | 10/11 | >= 9 | PASS |
| Security-flag accuracy | 11/11 | >= 10 | PASS |
| Fix suggested | 11/11 | >= 10 | PASS |
| All sections present | 11/11 | >= 11 | PASS |
| No secret leaked | 11/11 | >= 11 | PASS |
| Root-cause correctness | 19/22 | >= 16 | PASS |
| Fix quality | 15/22 | >= 14 | PASS |

**OVERALL: PASS**
