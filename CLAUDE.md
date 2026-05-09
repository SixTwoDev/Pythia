# CLAUDE.md — Pythia

Pythia is an open-source Slack bot that connects an LLM to arbitrary MCP servers. Mention it in Slack and it answers using whatever tools you've configured. The bot is connector-agnostic — it makes no assumption about which MCPs you plug in. It must be trivially deployable for a stranger.

This file encodes the non-negotiables. When in doubt, re-read it.

## First principles

1. **OSS-first.** Every change is judged against: *"Could a stranger clone this, set env vars, and have it running in 10 minutes?"* If a change makes that harder, it needs a strong reason in the PR description.
2. **No deployment-specific assumptions in committed code.** Provider choice, MCP server endpoints, organization IDs, repo paths — every such value is configurable via env vars. Never hardcode anything specific to a single user or organization. Site-specific behavior belongs in an operator's private values file, not in the repo.
3. **Single container, no required dependencies.** No DB, no sidecar, no broker, no external service beyond what the user explicitly configures (Slack + LLM + their MCP servers).
4. **Stateless by default.** Add state only with a concrete justification documented in the PR.

## Code

- Small files, small functions. If a file exceeds ~300 lines or a function ~40, split it.
- Type hints everywhere. `pyright` clean is a merge requirement, not a nice-to-have.
- Comments only explain non-obvious *why*. Never restate what the code does.
- No dead code, no commented-out code, no `# TODO: maybe later`. Delete it; git remembers.
- No premature abstractions. Three similar lines is fine; abstract on the fourth, not the second.
- Boring Python. Clever beats simple only when there's a measurable reason.
- No backwards-compatibility shims pre-1.0. We move fast and break things; users pin versions.

## Tidy as you work

- Leave every file you touch cleaner than you found it. Unused imports, stale comments, dead branches — fix them in the same change.
- Tests, README, `.env.example`, and config docs update in the same PR as the code. Never "I'll come back to it."
- Commit small and often. Each commit reviewable in under 5 minutes.
- Run formatter (`ruff format`) and linter (`ruff check`) before declaring work done. CI runs the same.
- New file? It must have a single, nameable purpose. If you can't name it in one word, it's doing too much.

## Deployment

- All config via env vars (12-factor). An optional config file *may* augment env vars but never replace them.
- `docker run -e ... ghcr.io/<org>/pythia` must be the entire deployment for a basic user. No init containers, no required volumes.
- Helm chart defaults: a single-replica `Deployment` + a `Secret`. Anything else (PVC, ConfigMap, ServiceMonitor) is opt-in via `values.yaml` and disabled by default.
- Every env var lives in a single table in the README. If it's not in the table, it doesn't exist. `.env.example` matches the table exactly.
- Image must be small. Multi-stage build, no compilers in the runtime layer.

## Tests

- Always write tests for new behaviour in the same change. No "I'll add tests later."
- Aim for ~90% line coverage, but never inflate coverage with tests that don't assert real behaviour. Cover behaviours, not lines. A trivial getter doesn't need a test; a branch that decides what the bot says does.
- `uv run pytest` runs the whole suite with no other setup. CI runs the same command.
- No tests hit real Slack, LLM, or third-party MCP APIs. Mock at the boundary.
- Full suite under 30s. Slow tests are bugs.

## What we never do

- No database. (If we eventually must: SQLite on a PVC, never Postgres.)
- No inbound HTTP server. Socket Mode means no ingress, no TLS, no public URL. Don't add one.
- No LiteLLM or other heavy provider routers. A modern agent framework's native providers plus an OpenAI-compatible `base_url` cover the common cases.
- No invented config schemas where conventions exist. MCP server config matches Claude Desktop's `mcpServers` shape so users can copy-paste.
- No speculative features. Build for users that exist; reject "but what if someone wants…" without a concrete request.
- No emoji in code, commits, or docs unless a user explicitly asks.

## Workflow

- Before writing code, state in one sentence what you're about to do.
- Make small, focused commits. Don't bundle unrelated changes.
- When you touch a file, scan for nearby cruft and clean it in the same change.
- If you find yourself adding a workaround, surface it — open an issue or refuse the workaround. Never hide it behind a vague comment.
- If a decision is reversible and low blast radius, just do it. If it's not, ask first.
