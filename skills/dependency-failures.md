---
name: dependency-failures
description: Diagnose packages that fail to install or resolve (pip, Poetry, Maven, Gradle, npm): typos and typosquats, missing versions, version conflicts, lockfile drift, registry auth.
---

# Dependency and supply-chain failures

## 1. Name the exact error class first
**Python (pip / Poetry)**
- `No matching distribution found for X` / `Could not find a version that satisfies`:
  a misspelled name, a version that doesn't exist, or a package that doesn't support
  this Python version (`Requires-Python`) or platform. Call check_package.
- `ResolutionImpossible` / `conflicting dependencies` / `Cannot install A and B`:
  a version conflict. Name the two requirements that clash.
- `error: subprocess-exited-with-error` while building a wheel, `gcc` / `pg_config`
  / `Python.h` not found: the package resolved fine but failed to COMPILE. That is
  `build_error` (missing system libraries), not a dependency failure.

**Java (Maven / Gradle)**
- Maven `Could not find artifact g:a:v` / Gradle `Could not find g:a:v.`: a typo,
  a version that doesn't exist, or a repository missing from the build.
- `401 Unauthorized` / `403 Forbidden` / `Not authorized` from Artifactory or Nexus:
  credentials or permissions. That is `config_error`.
- `PKIX path building failed` / certificate errors: TLS / proxy trust. That is
  `infra_error` (or `config_error` if a truststore is misconfigured in the repo).
- `Non-resolvable parent POM`: the parent's coordinates or repository are wrong.

**JavaScript (npm)**
- `ERESOLVE unable to resolve dependency tree`: a peer dependency conflict.
- `E404 Not Found`: a misspelled or private package. check_package covers PyPI and
  Maven only, so for npm reason from the error itself: compare the name with the
  package the code imports, and treat a near-miss of a popular name as suspicious.
- `ETARGET No matching version`: the requested version doesn't exist.
- `npm ci` saying package.json and package-lock.json are not in sync: lockfile drift.

## 2. Supply-chain judgement
For PyPI and Maven names, call check_package on the offending name. Then:
- `exists: false` with `possible_typosquat_of`: **flag it** as a supply-chain risk
  (likely typosquat of the named package). Say not to install the look-alike.
- The package exists but the VERSION doesn't: a version problem, not a supply-chain
  risk. security_flag is "none".
- A version conflict, a lockfile mismatch or a registry 401: not a supply-chain risk.
  security_flag is "none".
- `lookup_error` (registry unreachable): say the check could not be done; do NOT flag.
- A name that looks internal (company prefix) but resolves on the PUBLIC registry:
  flag possible dependency confusion.

## 3. Fixes
- Typo: correct the name, as a diff of the requirements / pom / package.json line.
- Missing version: pin to a version that exists (check_package's latest_version is a
  hint, not a recommendation to jump major versions).
- Conflict: pin the shared transitive dependency to a version both sides accept, or
  upgrade the side that holds the old constraint. Show which constraint changes.
- Lockfile drift: regenerate the lockfile from the manifest and commit both.
- Registry auth: point to the credential / settings.xml / .npmrc / CI secret that is
  missing, never to a hard-coded token.

## 4. Verify
- pip: `pip install -r requirements.txt && pip check`
- Poetry: `poetry install` (fails if poetry.lock is out of date with pyproject.toml)
- Maven: `mvn -U dependency:tree -Dincludes=<groupId>:<artifactId>`
- Gradle: `./gradlew dependencyInsight --dependency <name> --configuration compileClasspath`
- npm: `npm ci && npm ls <package>`
