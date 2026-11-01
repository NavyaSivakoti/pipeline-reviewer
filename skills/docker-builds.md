---
name: docker-builds
description: Diagnose container image failures (docker build / buildx / pull / push): base image pulls and rate limits, apt and wheel build errors, COPY / build-context mistakes, platform mismatches, registry auth.
---

# Docker / image build failures

## 1. Find the phase that failed: it decides the failure type
| Phase and signal | failure_type |
|---|---|
| Pulling the base image: `toomanyrequests: You have reached your pull rate limit` (Docker Hub 429) | `infra_error` |
| Pulling: `manifest unknown` / `not found` for a tag | `config_error` (the tag doesn't exist) |
| Pulling: `no matching manifest for linux/arm64` (or amd64) | `config_error` (platform mismatch) |
| A `RUN` step: compiler / headers missing, `Unable to locate package` | `build_error` |
| A `RUN` step: `Temporary failure resolving`, timeouts reaching mirrors | `infra_error` |
| `COPY failed: file not found in build context` / `failed to compute cache key: ... not found` | `build_error` |
| `exec format error` when a step runs a binary | `build_error` (wrong-architecture binary) |
| `no space left on device`, `Killed`, exit code 137 | `infra_error` |
| Push: `denied: requested access to the resource is denied`, `unauthorized: authentication required` | `config_error` (credentials / permissions) |

A Docker step failing does not make everything a build error: check WHAT failed.

## 2. Common root causes
- **Slim / Alpine images missing system packages.** `python:*-slim` has no compiler
  or libpq, so `psycopg2`, `mysqlclient`, `cryptography` (old versions) fail to build.
  Alpine uses musl, so many manylinux wheels don't apply and packages build from source.
- **`apt-get install` without `apt-get update` in the same `RUN`.** A cached old
  layer has a stale index, so packages "can't be located".
- **The base image tag moved.** The Dockerfile didn't change but a previously green
  build breaks: the tag (e.g. `python:3.11-slim`) now points to a newer OS release.
  Pin the base image by digest.
- **Build context.** The file is outside the context directory or excluded by
  `.dockerignore`.
- **Docker Hub rate limits** on unauthenticated pulls from shared CI runners.

## 3. Fixes (show the Dockerfile diff when small)
```dockerfile
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libpq-dev \
 && rm -rf /var/lib/apt/lists/*
```
- Or avoid compiling: use the prebuilt wheel (`psycopg2-binary`) where acceptable.
- Pin the base image: `FROM python:3.11-slim@sha256:<digest>`.
- Rate limit: `docker login` with a Docker Hub token in CI, or pull from a mirror /
  another registry (GHCR, ECR Public), and cache layers.
- Context: fix the `COPY` path relative to the context, or the `.dockerignore` rule.
- Platform: build with `--platform linux/amd64` or use a multi-arch base image.

## 4. Verify
- `docker build --progress=plain --no-cache -t review-check .`
- Platform: `docker buildx build --platform linux/amd64 -t review-check .`
- Base image / rate limit: `docker pull <image:tag>` after `docker login`
