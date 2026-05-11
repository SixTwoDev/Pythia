<p align="center">
  <img src="slack-app-icon.png" alt="Pythia" width="200">
</p>

# Pythia

[![CI](https://github.com/SixTwoDev/Pythia/actions/workflows/ci.yml/badge.svg)](https://github.com/SixTwoDev/Pythia/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

An open-source Slack bot that connects an LLM to arbitrary [MCP](https://modelcontextprotocol.io) servers. Mention Pythia in Slack and it answers using whatever tools you've configured. Stateless, single container, configured by environment variables.

## Who's Pythia?

The Pythia was the priestess at the Oracle of Delphi &mdash; engineers and kings would walk up the mountain with hard questions and come back down with answers drawn from sources they couldn't reach themselves. This bot does the same job in Slack: ask in plain English and Pythia goes off to your codebase, your Jira, your Datadog, your GitHub &mdash; whatever MCP servers you've configured &mdash; and comes back with an answer.

(The name also nods to Python, the serpent slain at Delphi that gave the Pythia her title &mdash; and the language Pythia is written in.)

## Quickstart

Get Pythia running in your Slack workspace in about five minutes. You need a Slack workspace where you can install apps, an API key for an LLM (anything OpenAI-compatible &mdash; OpenAI, OpenRouter, Azure, Anthropic via OpenAI compat, a local Ollama, &hellip;), and [`uv`](https://docs.astral.sh/uv/) installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`).

### 1. Create the Slack app

1. Go to <https://api.slack.com/apps> &rarr; **Create New App** &rarr; **From a manifest** &rarr; pick your workspace.
2. Paste the contents of [`slack-app-manifest.json`](slack-app-manifest.json) &rarr; **Next** &rarr; **Create**.
3. **Basic Information** &rarr; **App-Level Tokens** &rarr; **Generate Token and Scopes** &rarr; name it anything, add scope `connections:write`, **Generate**. Copy the `xapp-...` token.
4. **Install App** &rarr; **Install to Workspace** &rarr; **Allow**. Copy the **Bot User OAuth Token** (`xoxb-...`).
5. *Optional:* **Basic Information** &rarr; **Display Information** &rarr; **App icon** &rarr; upload [`slack-app-icon.png`](slack-app-icon.png) from this repo for the Pythia avatar.

### 2. Get an LLM API key

Use whichever provider you already have. The `.env` block below assumes [OpenRouter](https://openrouter.ai/keys) (one key, hundreds of models, usually a few dollars of free credit) but anything OpenAI-compatible works &mdash; just point `OPENAI_BASE_URL` and `OPENAI_MODEL` at your provider. See [Configuration](#configuration) for the common combinations.

### 3. Clone, configure, run

```sh
git clone git@github.com:SixTwoDev/Pythia.git
cd Pythia
uv sync

cat > .env <<'EOF'
SLACK_BOT_TOKEN=xoxb-PASTE_YOURS_HERE
SLACK_APP_TOKEN=xapp-PASTE_YOURS_HERE
OPENAI_API_KEY=PASTE_YOURS_HERE
OPENAI_MODEL=anthropic/claude-sonnet-4.5
EOF

uv run pythia
```

In Slack, invite Pythia to a channel (`/invite @Pythia`) and mention it: `@Pythia hello?` &mdash; you should get a reply within a few seconds. You can also drop an image, log file, or source file into your mention and Pythia will read it (images go to vision-capable models; text/code files are inlined into the prompt).

That's the whole bot. To make it useful, plug in [MCP servers](#mcp-servers) (Jira, Datadog, GitHub, &hellip;) and let it [read your codebase](#codebase-access).

## Why

If you've configured Jira, Datadog, GitHub, or any other MCP server somewhere else (e.g. Claude Desktop, Cursor), you should be able to point a Slack bot at the same servers and start asking questions about your stack. Pythia is that bot.

Use any LLM via [OpenRouter](https://openrouter.ai) or any OpenAI-compatible endpoint, point it at any MCP servers, run it as a single container.

## Deploy

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
| `CODEBASE_REFRESH_INTERVAL_SECONDS` | no | `3600` | How often to fetch + hard-reset each cloned repo against its remote. Set to `0` to disable; the clones then stay frozen until the pod restarts. |
| `PYTHIA_ALLOWED_CHANNELS` | no | &mdash; | Comma-separated Slack channel IDs Pythia will reply in. Mentions elsewhere are silently ignored. Leave unset for "any channel". Set to `""` to mute the bot. |
| `LLM_TIMEOUT_SECONDS` | no | `60` | Per-attempt cap on `agent.run`. Hung providers don't hold the Slack thread open forever. |
| `LLM_MAX_ATTEMPTS` | no | `4` | Total tries (initial + retries) for the LLM call. Backoff is exponential with jitter, capped at 30s. After all attempts fail the placeholder reply is updated with a friendly error. |
| `PYTHIA_HEARTBEAT_PATH` | no | `/tmp/pythia/heartbeat` | File the bot touches periodically so the K8s liveness probe (`pythia-healthcheck`) can detect a wedged process. |
| `PYTHIA_HEARTBEAT_INTERVAL_SECONDS` | no | `30` | How often to touch the heartbeat file. The Helm chart's liveness probe declares the bot dead if mtime is older than `3 * interval`. |

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

Drop-in configs are in [`examples/mcp-servers/`](examples/mcp-servers/) — `minimal.json` (time + filesystem, zero auth) is a good smoke-test starting point.

### Authentication

MCP servers vary widely in how they expect to be authenticated. Pythia handles this with no special machinery &mdash; the JSON config supports `${VAR}` expansion, the bot inherits its environment to subprocess servers, and the MCP server reads its own credentials. That covers two of the three real auth patterns cleanly:

- **Static API tokens / API keys** &mdash; Datadog, Linear, GitHub PATs, OpenAI keys, internal services. Generate the token once, drop it in your env / Secret, reference it from the JSON: `"env": { "DD_API_KEY": "${DD_API_KEY}" }`. Roughly 80% of useful MCP servers fall here.
- **Service accounts** &mdash; GitHub App installation tokens, Atlassian API tokens, Google Workspace service accounts, "bot" identities in your SaaS. Same pattern: provision a "Pythia bot" identity in the upstream system, generate credentials, env-var them in. Trade-off: every query runs as the bot identity &mdash; audit logs show "Pythia did this" rather than the asking user, and the bot sees what the bot has access to. Usually fine for an internal team bot.

The third pattern Pythia does **not** support today:

- **Per-user OAuth flows** &mdash; Atlassian's hosted MCP server, Notion's MCP, the official Google Drive MCP for personal accounts. These expect "open a browser, log in as the human, get a per-user token, use it for that human's queries." A Slack bot in Socket Mode has no public callback URL, no per-user token store, and no obvious way to surface a "click here to authorize" prompt. Implementing this properly would mean an HTTP server, a database, per-MCP OAuth dances, and Slack-side auth UX &mdash; that's a different product, not a feature.

If you need an OAuth-only MCP, the realistic options are:

1. **Use a service-account variant if one exists.** Atlassian, GitHub, Notion, and Google Workspace all offer non-OAuth alternatives. Almost always the right answer for an internal team bot.
2. **Run Pythia locally first** to complete the browser OAuth interactively, then deploy with the cached token. Works until the refresh window closes.
3. **Use a hosted MCP gateway** like [Composio](https://composio.dev) or [Pipedream Connect](https://pipedream.com/connect). They manage the OAuth flows centrally and expose a single bearer-token MCP endpoint that Pythia connects to like any other HTTP MCP server.
4. **Run a small MCP proxy of your own** as a separate service. Proxy holds the OAuth tokens, exposes HTTP MCP endpoints to Pythia, manages refresh. Keep it as its own repo &mdash; the proxy needs an HTTP server and persistent storage, both of which are explicitly off-limits in Pythia itself.

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

The clones live in a tempdir for the lifetime of the process and are deleted on shutdown.

By default Pythia re-fetches every repo against its remote default branch every hour and hard-resets the local clone to match (`git fetch --depth 1 origin && git reset --hard FETCH_HEAD`). Tune the cadence with `CODEBASE_REFRESH_INTERVAL_SECONDS` or set it to `0` to freeze the clones at boot. Hard-reset is intentional &mdash; Pythia never makes local commits, so "match remote exactly" is both the desired and the only reliably convergent behaviour (a `pull` would fail on remote force-pushes or branch renames). Grounding docs (`CLAUDE.md` / `AGENTS.md`) are still only loaded once at startup; restart the bot to pick up changes to those.

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
