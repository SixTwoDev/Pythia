# Pythia

[![CI](https://github.com/SixTwoDev/Pythia/actions/workflows/ci.yml/badge.svg)](https://github.com/SixTwoDev/Pythia/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

An open-source Slack bot that connects an LLM to arbitrary [MCP](https://modelcontextprotocol.io) servers. Mention Pythia in Slack and it answers using whatever tools you've configured. Stateless, single container, configured by environment variables.

## Who's Pythia?

The Pythia was the priestess at the Oracle of Delphi &mdash; engineers and kings would walk up the mountain with hard questions and come back down with answers drawn from sources they couldn't reach themselves. This bot does the same job in Slack: ask in plain English and Pythia goes off to your codebase, your Jira, your Datadog, your GitHub &mdash; whatever MCP servers you've configured &mdash; and comes back with an answer.

(The name also nods to Python, the serpent slain at Delphi that gave the Pythia her title &mdash; and the language Pythia is written in.)

## Why

If you've configured Jira, Datadog, GitHub, or any other MCP server somewhere else (e.g. Claude Desktop, Cursor), you should be able to point a Slack bot at the same servers and start asking questions about your stack. Pythia is that bot.

Use any LLM via [OpenRouter](https://openrouter.ai) or any OpenAI-compatible endpoint, point it at any MCP servers, run it as a single container.

## Install

### Local

Requires Python 3.12+ and [`uv`](https://docs.astral.sh/uv/).

```sh
git clone git@github.com:SixTwoDev/Pythia.git
cd Pythia
uv sync
cp .env.example .env  # then fill in your tokens
uv run pythia
```

### Docker

```sh
docker run --rm \
  -e SLACK_BOT_TOKEN=xoxb-... \
  -e SLACK_APP_TOKEN=xapp-... \
  ghcr.io/sixtwodev/pythia:latest
```

### Helm

Minimal install (LLM-only, no MCP, no codebase):

```sh
helm install pythia ./deploy/helm/pythia \
  --set slack.botToken=xoxb-... \
  --set slack.appToken=xapp-... \
  --set llm.apiKey=sk-... \
  --set llm.model=anthropic/claude-sonnet-4.5
```

Production-friendlier: keep secrets out of `helm install` args by creating a `Secret` with `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `OPENAI_API_KEY` (and optionally `CODEBASE_REPOS`) and reference it:

```sh
kubectl create secret generic pythia-secrets \
  --from-literal=SLACK_BOT_TOKEN=xoxb-... \
  --from-literal=SLACK_APP_TOKEN=xapp-... \
  --from-literal=OPENAI_API_KEY=sk-...

helm install pythia ./deploy/helm/pythia \
  --set existingSecret=pythia-secrets \
  --set llm.model=anthropic/claude-sonnet-4.5
```

To enable MCP servers, codebase access, or a custom system prompt, create the corresponding K8s objects and reference them:

```sh
# MCP: ConfigMap-shaped JSON in a Secret (under key `mcp-servers.json`)
kubectl create secret generic pythia-mcp --from-file=mcp-servers.json=./my-mcp.json

# System prompt: ConfigMap (key `system-prompt.md`)
kubectl create configmap pythia-prompt --from-file=system-prompt.md=./my-prompt.md

# SSH for git@ clone URLs: Secret with key `id_rsa`
kubectl create secret generic pythia-ssh --from-file=id_rsa=$HOME/.ssh/pythia_deploy_key

helm install pythia ./deploy/helm/pythia \
  --set existingSecret=pythia-secrets \
  --set llm.model=anthropic/claude-sonnet-4.5 \
  --set mcp.existingSecret=pythia-mcp \
  --set prompt.existingConfigMap=pythia-prompt \
  --set codebase.repos="api=git@github.com:acme/api.git,web=git@github.com:acme/web.git" \
  --set codebase.sshExistingSecret=pythia-ssh
```

The chart mounts an `emptyDir` at `/tmp` so the codebase clone (and any stdio MCP server that writes scratch state) works under `readOnlyRootFilesystem: true`.

## Configuration

All configuration is via environment variables.

| Variable | Required | Default | Description |
|---|---|---|---|
| `SLACK_BOT_TOKEN` | yes | &mdash; | Bot token (`xoxb-...`) from your Slack app's *OAuth & Permissions* page. |
| `SLACK_APP_TOKEN` | yes | &mdash; | App-level token (`xapp-...`) with `connections:write`, from *Basic Information*. |
| `OPENAI_API_KEY` | yes | &mdash; | API key for your LLM provider. With the default `OPENAI_BASE_URL`, this is your OpenRouter key. |
| `OPENAI_BASE_URL` | no | `https://openrouter.ai/api/v1` | Any OpenAI-compatible endpoint &mdash; OpenAI, Azure OpenAI, Ollama, vLLM, LM Studio, etc. |
| `OPENAI_MODEL` | yes | &mdash; | Model identifier as exposed by your endpoint (e.g. `anthropic/claude-sonnet-4.5` on OpenRouter, `gpt-4o` on OpenAI). |
| `PYTHIA_SYSTEM_PROMPT_FILE` | no | &mdash; | Path to a file whose contents replace the built-in system prompt. |
| `MCP_SERVERS_CONFIG` | no | &mdash; | Path to a JSON file declaring MCP servers (see below). Without it, Pythia runs with no tools and just answers from the conversation. |
| `CODEBASE_REPOS` | no | &mdash; | Comma-separated list of git repos to clone on startup &mdash; either `NAME=URL` or just `URL`. Pythia exposes `search_code` and `read_file` tools over them. Needs `git` and `ripgrep` on PATH (the published Docker image bundles both). |

## MCP servers

Pythia loads MCP servers from a JSON file in [Claude Desktop's `mcpServers` shape](https://modelcontextprotocol.io/quickstart/user), so you can copy-paste the same config you use elsewhere. Both stdio (subprocess) and HTTP transports are supported:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/var/lib/pythia/repo"]
    },
    "datadog": {
      "command": "uvx",
      "args": ["mcp-server-datadog"],
      "env": {
        "DD_API_KEY": "...",
        "DD_APP_KEY": "..."
      }
    },
    "atlassian": {
      "url": "https://mcp.atlassian.com/v1/sse",
      "headers": { "Authorization": "Bearer ..." }
    }
  }
}
```

Point `MCP_SERVERS_CONFIG` at the file, mount it into the container, and the agent will start the servers on boot and expose their tools to the LLM.

`${VAR_NAME}` and `${VAR_NAME:-default}` references inside the JSON are expanded from Pythia's environment at load time, so you can keep secrets out of the file:

```json
"datadog": {
  "command": "uvx",
  "args": ["mcp-server-datadog"],
  "env": { "DD_API_KEY": "${DD_API_KEY}", "DD_APP_KEY": "${DD_APP_KEY}" }
}
```

The dict key (e.g. `"datadog"`) becomes the server's `tool_prefix` automatically, so `search` from `datadog` and `search` from another server won't collide.

## Codebase access

Set `CODEBASE_REPOS` to a comma-separated list of git URLs (or `name=url` pairs) and Pythia will shallow-clone each into a tempdir on startup, then expose two tools to the LLM:

- `search_code(repo, query)` &mdash; ripgrep regex search across the named repo, capped at 50 results (10 per file).
- `read_file(repo, path, start_line, end_line)` &mdash; reads a file from the named repo. Path traversal is blocked; only files inside the cloned repo are readable.

Examples:

```sh
# one repo (name auto-derived as "api")
CODEBASE_REPOS=git@github.com:acme/api.git

# multiple repos with explicit names
CODEBASE_REPOS=api=git@github.com:acme/api.git,web=https://github.com/acme/web.git
```

The clones live in a tempdir for the lifetime of the process and are deleted on shutdown. To refresh, restart the bot.

### Auth for private repos

Pythia shells out to `git clone` with the parent process's environment fully inherited &mdash; whatever lets *you* `git clone <url>` from a terminal will work for Pythia.

**SSH (recommended for K8s).** `git@github.com:owner/repo.git` URLs use `~/.ssh/` and `ssh-agent`. Generate a per-bot **deploy key** (one keypair per repo, `Settings → Deploy keys → Add` on each), mount the private half into the container, and Pythia's clones are scoped to exactly those repos. The Helm chart's `codebase.sshExistingSecret` does the mount + `GIT_SSH_COMMAND` wiring for you.

**Personal Access Token over HTTPS.** Embed a fine-grained PAT (or a GitHub App installation token) in the URL:

```sh
export GH_TOKEN=github_pat_...
CODEBASE_REPOS="api=https://x-access-token:${GH_TOKEN}@github.com/owner/api.git"
```

A **fine-grained PAT** scoped to "Contents: read" on just the repos Pythia needs is the safest version &mdash; classic tokens grant much broader access than this use case wants. To rotate, update the token in the Secret and restart the bot.

When using the chart, put the whole `CODEBASE_REPOS` string (with the token embedded) into your `existingSecret` rather than passing it via `--set`, so the token never appears in `kubectl describe pod` output.

**`gh` as credential helper (local dev only).** If you've run `gh auth login` and `gh auth setup-git` on the host, plain `https://github.com/owner/repo.git` URLs authenticate transparently via the `gh` binary &mdash; no token in your config. Doesn't apply inside containers (no `gh` installed).

GitLab, Bitbucket, and self-hosted Git work the same way: SSH key, or token-in-URL with whatever username convention your host uses (`oauth2:`, `gitlab-ci-token:`, etc.).

## Slack app setup

Pythia uses Socket Mode, so no public URL or ingress is needed.

1. Go to <https://api.slack.com/apps> and click **Create New App** &rarr; **From an app manifest**.
2. Pick your workspace, then paste the contents of [`slack-app-manifest.json`](./slack-app-manifest.json).
3. Under **Basic Information**, generate an **App-Level Token** with the `connections:write` scope &mdash; this is your `SLACK_APP_TOKEN` (`xapp-...`).
4. Under **Install App**, install to your workspace and copy the **Bot User OAuth Token** &mdash; this is your `SLACK_BOT_TOKEN` (`xoxb-...`).
5. Drop both tokens into your `.env` (or your Helm values / Kubernetes Secret) and start Pythia.

## Development

```sh
uv sync                   # install all deps including dev
uv run ruff check .       # lint
uv run ruff format .      # format
uv run pyright            # type-check
uv run pytest             # tests
```

See [CLAUDE.md](CLAUDE.md) for the project conventions.

## License

MIT — see [LICENSE](LICENSE).
