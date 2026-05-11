# Examples

Drop-in configs you can copy or `--from-file` into K8s. Edit paths, tokens, and
model IDs to match your setup.

## MCP servers

Point `MCP_SERVERS_CONFIG` at one of these JSON files (or your own).
All examples use `${VAR_NAME}` expansion so secrets stay out of the file —
just export the named env vars before starting Pythia.

| File | What it gives the bot | Setup |
|---|---|---|
| [`mcp-servers/minimal.json`](mcp-servers/minimal.json) | Wall-clock time + a sandboxed filesystem under `/tmp` | None — both subprocesses install themselves on demand via `uvx` / `npx` |
| [`mcp-servers/github.json`](mcp-servers/github.json) | The official GitHub MCP server | Set `GITHUB_PERSONAL_ACCESS_TOKEN` in Pythia's environment |
| [`mcp-servers/kitchen-sink.json`](mcp-servers/kitchen-sink.json) | Time, filesystem, fetch, git, GitHub — all the official MCP servers | Set `GITHUB_PERSONAL_ACCESS_TOKEN`; the rest are unauthenticated |

`minimal.json` is the right starting point for a smoke test; once that works
end-to-end, swap in whichever real connectors you need.

### Pin versions in production

The example configs use bare package names (`uvx mcp-server-time`,
`npx -y @modelcontextprotocol/server-filesystem`) so they always pull the
latest version — convenient for trying things out, bad for supply-chain
hygiene. In production, **pin to specific versions** so a compromised release
of an MCP server can't get auto-installed on your next pod restart:

```json
"time": {
  "command": "uvx",
  "args": ["mcp-server-time==1.2.3"]
},
"fs": {
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem@2024.10.10"]
}
```

For maximum paranoia, host the packages in your own registry mirror and
point `UV_INDEX_URL` / `NPM_CONFIG_REGISTRY` at it.

## System prompt

[`system-prompt.example.md`](system-prompt.example.md) is a triage-focused
prompt for engineering Slack channels — adjust to your team's norms and
point `PYTHIA_SYSTEM_PROMPT_FILE` at it (or mount it via the chart's
`prompt.existingConfigMap`).
