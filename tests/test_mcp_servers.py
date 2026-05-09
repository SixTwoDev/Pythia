import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_ai.mcp import MCPServerStdio, MCPServerStreamableHTTP

from pythia.mcp_servers import load_mcp_servers


def _write(tmp_path: Path, payload: dict[str, object]) -> str:
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_load_returns_empty_list_when_path_is_none() -> None:
    assert load_mcp_servers(None) == []


def test_load_returns_empty_list_when_mcp_servers_dict_is_empty(tmp_path: Path) -> None:
    path = _write(tmp_path, {"mcpServers": {}})
    assert load_mcp_servers(path) == []


def test_load_returns_empty_list_when_mcp_servers_key_is_missing(tmp_path: Path) -> None:
    path = _write(tmp_path, {})
    assert load_mcp_servers(path) == []


def test_load_parses_a_stdio_server_with_command_args_and_env(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        {
            "mcpServers": {
                "time": {
                    "command": "uvx",
                    "args": ["mcp-server-time", "--utc"],
                    "env": {"TZ": "UTC"},
                }
            }
        },
    )
    servers = load_mcp_servers(path)
    assert len(servers) == 1
    server = servers[0]
    assert isinstance(server, MCPServerStdio)
    assert server.command == "uvx"
    assert server.args == ["mcp-server-time", "--utc"]
    assert server.env == {"TZ": "UTC"}


def test_load_parses_an_http_server_with_url_and_headers(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        {
            "mcpServers": {
                "remote": {
                    "url": "https://example.com/mcp",
                    "headers": {"Authorization": "Bearer secret"},
                }
            }
        },
    )
    servers = load_mcp_servers(path)
    assert len(servers) == 1
    server = servers[0]
    assert isinstance(server, MCPServerStreamableHTTP)
    assert server.url == "https://example.com/mcp"


def test_load_parses_multiple_servers_of_mixed_transport(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        {
            "mcpServers": {
                "time": {"command": "uvx", "args": ["mcp-server-time"]},
                "remote": {"url": "https://example.com/mcp"},
            }
        },
    )
    servers = load_mcp_servers(path)
    assert len(servers) == 2
    transports = {type(s).__name__ for s in servers}
    assert transports == {"MCPServerStdio", "MCPServerStreamableHTTP"}


def test_load_raises_when_path_does_not_exist(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_mcp_servers(str(tmp_path / "absent.json"))


def test_load_raises_when_file_is_not_valid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("not json at all", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_mcp_servers(str(path))


def test_load_raises_validation_error_on_unknown_server_shape(tmp_path: Path) -> None:
    path = _write(tmp_path, {"mcpServers": {"weird": {"unknown_field": 1}}})
    with pytest.raises(ValidationError):
        load_mcp_servers(path)
