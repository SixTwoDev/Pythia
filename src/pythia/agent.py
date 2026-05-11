import json
import logging
import os
import time
from collections.abc import AsyncIterable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServer, MCPServerStdio, load_mcp_servers
from pydantic_ai.messages import (
    AgentStreamEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    HandleResponseEvent,
    ToolCallPart,
    UserContent,
)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from pythia.config import Settings

logger = logging.getLogger(__name__)

_LOG_VALUE_LIMIT = 200
_tool_call_started_at: dict[str, float] = {}

DEFAULT_SYSTEM_PROMPT = """\
You are Pythia, an assistant in a Slack workspace. You help engineers investigate questions \
by reasoning over the conversation and any tools available to you.

You will receive the contents of a Slack thread. The most recent message is the one that \
mentioned you; earlier messages are context. Reply concisely in Slack-flavoured markdown.

When you cite a fact (a Jira ticket, a metric, a code path, a log line), include the source \
so the reader can verify. When you are uncertain, say so plainly.\
"""


def _system_prompt(settings: Settings) -> str:
    if settings.pythia_system_prompt_file:
        return Path(settings.pythia_system_prompt_file).read_text(encoding="utf-8")
    return DEFAULT_SYSTEM_PROMPT


# Env vars passed through to every stdio MCP subprocess. Anything NOT on this
# list (notably SLACK_*, OPENAI_*, GITHUB_*, AWS_*, …) stays inside Pythia,
# so a compromised MCP dep can't read or exfiltrate the bot's secrets just
# by inspecting os.environ. Operators that legitimately need a specific var
# in their MCP can pass it through explicitly via `"env": {"FOO": "${FOO}"}`
# in the mcpServers JSON — load_mcp_servers expands that before launch.
_MCP_INHERITED_ENV_VARS = (
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TEMP",
    "TMP",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "UV_CACHE_DIR",
    "NPM_CONFIG_CACHE",
)


def _scoped_mcp_env(config_env: dict[str, str] | None) -> dict[str, str]:
    inherited = {
        var: value for var in _MCP_INHERITED_ENV_VARS if (value := os.environ.get(var)) is not None
    }
    return {**inherited, **(config_env or {})}


def _mcp_servers(settings: Settings) -> list[MCPServer]:
    if not settings.mcp_servers_config:
        return []
    servers: list[MCPServer] = list(load_mcp_servers(settings.mcp_servers_config))
    for server in servers:
        if isinstance(server, MCPServerStdio):
            server.env = _scoped_mcp_env(server.env)
    return servers


def _truncate(value: object, limit: int = _LOG_VALUE_LIMIT) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _log_event(event: AgentStreamEvent | HandleResponseEvent) -> None:
    if isinstance(event, FunctionToolCallEvent):
        _tool_call_started_at[event.part.tool_call_id] = time.monotonic()
        logger.info("tool call → %s(%s)", event.part.tool_name, _truncate(event.part.args))
    elif isinstance(event, FunctionToolResultEvent):
        part = event.part
        started = _tool_call_started_at.pop(part.tool_call_id, None)
        elapsed = f"{(time.monotonic() - started) * 1000:.0f}ms" if started else "?"
        outcome = getattr(part, "outcome", "retry")
        logger.info(
            "tool result ← %s [%s] %s (%s)",
            part.tool_name or "?",
            outcome,
            _truncate(part.content),
            elapsed,
        )


async def _log_events(
    _ctx: Any,
    events: AsyncIterable[AgentStreamEvent | HandleResponseEvent],
) -> None:
    async for event in events:
        _log_event(event)


_GROUNDING_PREFIX = (
    "# Codebase context\n\n"
    "The following grounding docs were loaded from each codebase you can search. "
    "Treat them as authoritative project conventions; cite specific files when you "
    "reference behaviour described here.\n\n"
)


def _system_prompts(settings: Settings, grounding_docs: str) -> list[str]:
    prompts = [_system_prompt(settings)]
    if grounding_docs:
        prompts.append(_GROUNDING_PREFIX + grounding_docs)
    return prompts


def build_agent(
    settings: Settings,
    *,
    extra_tools: Sequence[Callable[..., object]] = (),
    grounding_docs: str = "",
) -> Agent[None, str]:
    provider = OpenAIProvider(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
    model = OpenAIChatModel(settings.openai_model, provider=provider)
    return Agent(
        model,
        system_prompt=_system_prompts(settings, grounding_docs),
        toolsets=_mcp_servers(settings),
        tools=list(extra_tools),
        event_stream_handler=_log_events,
    )


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: str  # already-formatted for display


@dataclass(frozen=True)
class AgentReply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


def _format_args(args: object) -> str:
    if args is None:
        return ""
    if isinstance(args, str):
        return args
    try:
        return json.dumps(args, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(args)


async def answer(agent: Agent[None, str], prompt: str | Sequence[UserContent]) -> AgentReply:
    result = await agent.run(prompt)
    calls: list[ToolCall] = []
    for message in result.all_messages():
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolCallPart):
                calls.append(ToolCall(name=part.tool_name, args=_format_args(part.args)))
    return AgentReply(text=str(result.output), tool_calls=calls)
