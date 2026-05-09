# Pythia

[![CI](https://github.com/SixTwoDev/Pythia/actions/workflows/ci.yml/badge.svg)](https://github.com/SixTwoDev/Pythia/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

An open-source Slack bot that connects an LLM to arbitrary [MCP](https://modelcontextprotocol.io) servers. Mention Pythia in Slack and it answers using whatever tools you've configured. Stateless, single container, configured by environment variables.

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

```sh
helm install pythia ./deploy/helm/pythia \
  --set slack.botToken=xoxb-... \
  --set slack.appToken=xapp-...
```

Or reference an existing Secret containing `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN`:

```sh
helm install pythia ./deploy/helm/pythia --set existingSecret=my-pythia-secret
```

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

More variables (MCP servers) will be added as those features land.

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
