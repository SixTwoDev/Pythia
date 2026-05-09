import json
from pathlib import Path

from pydantic_ai.mcp import MCPServer, MCPServerConfig


def load_mcp_servers(path: str | None) -> list[MCPServer]:
    if not path:
        return []
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not data.get("mcpServers"):
        return []
    config = MCPServerConfig.model_validate(data)
    return list(config.mcp_servers.values())
