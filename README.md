# Pythia

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

| Variable | Required | Description |
|---|---|---|
| `SLACK_BOT_TOKEN` | yes | Bot token (`xoxb-...`) from your Slack app's *OAuth & Permissions* page. |
| `SLACK_APP_TOKEN` | yes | App-level token (`xapp-...`) with `connections:write`, from *Basic Information*. |

More variables (LLM provider, MCP servers) will be added as those features land.

## Slack app setup

Pythia uses Socket Mode, so no public URL or ingress is needed.

1. Create a Slack app at <https://api.slack.com/apps> (from manifest or scratch).
2. Enable **Socket Mode**, generate an app-level token with `connections:write`.
3. Under **OAuth & Permissions**, add bot scopes: `app_mentions:read`, `chat:write`, `channels:history`, `groups:history`.
4. Subscribe to bot events: `app_mention`.
5. Install the app to your workspace; copy the bot and app-level tokens into your env.

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
