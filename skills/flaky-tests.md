---
name: flaky-tests
description: Decide whether a failing test is really flaky or a real regression (timeouts, intermittent failures, passed-on-rerun, order or time dependence).
---

# Flaky vs real failure

Calling a real regression "flaky" hides a bug; calling a flaky test real wastes
someone's afternoon. Decide on evidence, not on the word "timeout".

## Evidence ladder
**Strong evidence it is flaky** (enough for `flaky`, High confidence):
- It already passed on a rerun in the same run: `flaky_tests` from
  parse_junit_results (Surefire `<flakyFailure>` / `<flakyError>`), pytest-rerunfailures
  `RERUN` lines, Jest / Playwright "retry #1 passed".
- The log or test name marks it as known-flaky or quarantined.
- check_recurrence shows the same fingerprint recurring across unrelated changes,
  AND the PR did not touch the code under test.

**Weak evidence** (on its own, NOT enough for `flaky`):
- A timeout or connection error with no rerun signal. A timeout can be a real
  performance regression, a deadlock, or an infra problem.
- "It passes locally."

**Evidence it is real** (do NOT call it flaky):
- The PR changed the code under test or the test itself (`focus_files_changed`).
- The assertion compares wrong VALUES (`expected 200 but was 401`,
  `assert 'INR' == 'USD'`): that is deterministic until proven otherwise.
- Several tests in the same area fail the same way.

With only weak evidence, use `test_failure` (or `infra_error` if the environment
broke), say "possibly flaky", set Medium or Low confidence, and suggest the rerun
check below.

## flaky vs infra_error
- `flaky`: one test is intermittent while the environment is fine.
- `infra_error`: the environment failed for everything (runner OOM / `Killed`, disk
  full, DNS down for the whole job, a service container that never started).

## Common causes and the fix for each
| Signal in the log | Likely cause | Fix |
|---|---|---|
| `sleep(...)` then assert, timeouts on async work | timing | wait for the condition (poll / await), not a fixed sleep |
| Passes alone, fails in the full suite | shared state / test order | isolate fixtures, reset globals, no module-level state |
| Fails near midnight, month end, DST, in CI's UTC | time / timezone | freeze or inject the clock; avoid `now()` in assertions |
| Different result each run, no seed | randomness | seed the RNG in the test |
| `Address already in use`, port bind errors | leaked resources | random free ports, close servers in teardown |
| Order of items differs | unordered collections, SQL without ORDER BY | sort before comparing; add ORDER BY |
| Connection refused / reset to an external service | real network in a unit test | mock it, or wait-for the service in integration tests |

## Recommending a fix
- Short term: rerun or quarantine ONLY with a tracking ticket. Never suggest blanket
  automatic retries for the whole suite; they hide real failures.
- Long term: the root-cause fix from the table above, with a diff when it is small.

## Verify
Run the single test many times; one failure is enough to prove flakiness.
- pytest: `for i in $(seq 20); do pytest -q "tests/test_x.py::test_y" || break; done`
- Order dependence (pytest): run the test alone, then the whole file, and compare.
- Maven: `mvn -Dtest=ClassName#methodName -Dsurefire.rerunFailingTestsCount=3 test`
- Gradle: `./gradlew test --tests 'pkg.ClassName.methodName' --rerun-tasks`
