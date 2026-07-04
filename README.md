# 🚦 CI/CD Pipeline Reviewer Agent

An AI agent (built with **Google ADK + Gemini**) that reviews a failed CI/CD
pipeline and tells you **what broke, why, who owns it, and how to fix it** — and
runs **automatically as a GitHub Action**, commenting the review on your commit/PR.

Built for the **Kaggle × Google "AI Agents: Intensive Vibe Coding" capstone.**

> **See it run live:** the [demo-app](https://github.com/NavyaSivakoti/demo-app)
> has an intentionally-failing test. On every push, CI fails and this agent
> **auto-comments the review** on the commit — root cause + a fix diff.

---

## What it does
Give it a failed pipeline's artifacts (a log from any CI tool and/or JUnit/pytest
results) and it produces one review:
- **Failure type** (test · build · dependency · lint · flaky) + key evidence
- **Root cause**
- **Responsible team** (ownership map)
- **Suggested fix** (as a patch/diff)
- **Security / supply-chain flag** (typosquatted or missing package — via a *live* PyPI check)
- **Confidence** + **how to verify**

## Why it's a real agent (not a ChatGPT wrapper)
- **Tools on data a chatbot can't see:** a live **PyPI lookup** (`check_package`) + your **ownership map**.
- **Autonomy:** it runs *inside your pipeline* as a GitHub Action and comments automatically — no one pastes anything.
- **Measured:** an evaluation harness scores it against labelled cases.
- **Guarded:** secrets are redacted before any text reaches the model.

## Architecture
```
 failure artifacts (log / junit xml, any CI tool)
        |
        v
 [ Pipeline Reviewer agent ]  tools: read_log, parse_junit_results,
   loads the review skill      lookup_owner, check_package (live PyPI)
        |
        v
   review  ->  printed locally, OR posted as a PR/commit comment by the GitHub Action
```

## Run it locally
```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # paste your free Gemini key (aistudio.google.com/apikey)

.venv/bin/python run.py                                     # default sample
.venv/bin/python run.py sample_data/github_actions_failure.log
.venv/bin/python eval/run_eval.py                           # evaluation
```
> Free-tier note: ~5 req/min and ~20/day per model; the runner auto-retries 429/503.

## Run it in CI (autonomy)
See [`demo-app/.github/workflows/ci.yml`](https://github.com/NavyaSivakoti/demo-app/blob/main/.github/workflows/ci.yml):
on a failed pipeline it clones this repo, runs `ci_review.py`, and posts the
review as a commit comment (needs a `GEMINI_API_KEY` Actions secret).

## Project structure
```
tools.py         # tools: parsers, ownership lookup, live PyPI check, secret redaction
skills/review.md # the review skill (Day 3), loaded by the agent
agent.py         # the Pipeline Reviewer agent
agent_runner.py  # harness with retry/backoff
run.py           # CLI
ci_review.py     # CI entry point (prints only the review, for the comment)
eval/            # evaluation dataset + harness
sample_data/     # sample logs (GitHub Actions, Jenkins, dependency, test, flaky, lint)
```

## Whitepaper concepts
| Day | Concept | Where |
|-----|---------|-------|
| 1 | Agent + context engineering + harness | clean parsed evidence; the GitHub Action is the harness |
| 2 | Tools + interoperability | 4 tools incl. live PyPI; GitHub integration |
| 3 | Agent Skills | `skills/review.md` loaded by the agent |
| 4 | Security + evaluation | secret redaction + supply-chain flag + `eval/` |
| 5 | Spec-driven + human-in-the-loop | `spec.md` first; agent suggests, human applies |

## Team
- **Mohan (DevOps):** the agent, tools, and GitHub Action.
- **Navya (AI build):** the evaluation harness + the security-flag logic.

## Future work
- Failure memory / recurrence detection
- Expose the tools as an **MCP server** (usable from Cursor / Claude)
- Live GitHub Actions log fetch; PR-comment mode
