import asyncio
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServer
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel

from pythia.agent import (
    DEFAULT_SYSTEM_PROMPT,
    RetryPolicy,
    _log_event,
    _system_prompt,
    _truncate,
    answer,
    build_agent,
)
from pythia.config import Settings


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "slack_bot_token": "xoxb-test",
        "slack_app_token": "xapp-test",
        "openai_api_key": "sk-test",
        "openai_model": "openai/gpt-4o-mini",
    }
    base.update(overrides)
    return Settings.model_validate(base)


def test_default_system_prompt_used_when_no_file_configured() -> None:
    assert _system_prompt(_settings()) == DEFAULT_SYSTEM_PROMPT


def test_system_prompt_loaded_from_file_when_configured(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("custom prompt body", encoding="utf-8")
    settings = _settings(pythia_system_prompt_file=str(prompt_file))
    assert _system_prompt(settings) == "custom prompt body"


def test_build_agent_returns_an_agent_using_the_configured_model() -> None:
    agent = build_agent(_settings(openai_model="openai/gpt-4o"))
    assert isinstance(agent, Agent)
    assert isinstance(agent.model, OpenAIChatModel)
    assert agent.model.model_name == "openai/gpt-4o"


def _mcp_toolsets(agent: Agent[None, str]) -> list[MCPServer]:
    return [t for t in agent.toolsets if isinstance(t, MCPServer)]


def test_build_agent_attaches_mcp_servers_from_the_configured_file(tmp_path: Path) -> None:
    config_file = tmp_path / "mcp.json"
    config_file.write_text(
        '{"mcpServers": {"time": {"command": "uvx", "args": ["mcp-server-time"]}}}',
        encoding="utf-8",
    )
    agent = build_agent(_settings(mcp_servers_config=str(config_file)))
    assert len(_mcp_toolsets(agent)) == 1


def test_build_agent_attaches_no_mcp_servers_when_no_config_set() -> None:
    agent = build_agent(_settings())
    assert _mcp_toolsets(agent) == []


def test_build_agent_appends_grounding_docs_to_the_system_prompt() -> None:
    agent = build_agent(_settings(), grounding_docs="## api (CLAUDE.md)\n\nuse tabs")
    instructions = list(agent._system_prompts)
    assert any("Codebase context" in p for p in instructions)
    assert any("use tabs" in p for p in instructions)


def test_build_agent_does_not_add_grounding_section_when_empty() -> None:
    agent = build_agent(_settings(), grounding_docs="")
    instructions = list(agent._system_prompts)
    assert not any("Codebase context" in p for p in instructions)


@pytest.mark.asyncio
async def test_answer_runs_the_agent_and_returns_text_with_tool_call_list() -> None:
    agent = build_agent(_settings())
    with agent.override(model=TestModel(custom_output_text="hello from pythia")):
        reply = await answer(agent, "any prompt")
    assert reply.text == "hello from pythia"
    assert reply.tool_calls == []


def test_truncate_returns_short_values_unchanged() -> None:
    assert _truncate("short") == "short"


def test_truncate_clips_long_values_with_ellipsis() -> None:
    assert _truncate("x" * 500) == "x" * 199 + "…"


def test_log_event_logs_tool_call_with_name_and_args(
    caplog: pytest.LogCaptureFixture,
) -> None:
    event = FunctionToolCallEvent(
        part=ToolCallPart(
            tool_name="search_code",
            args={"repo": "pythia", "query": "load_mcp"},
            tool_call_id="call-1",
        )
    )
    with caplog.at_level("INFO", logger="pythia.agent"):
        _log_event(event)
    assert "tool call → search_code" in caplog.text
    assert "load_mcp" in caplog.text


def test_log_event_logs_tool_result_with_outcome_and_elapsed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    call = FunctionToolCallEvent(
        part=ToolCallPart(tool_name="read_file", args={"path": "x.py"}, tool_call_id="call-2")
    )
    result = FunctionToolResultEvent(
        part=ToolReturnPart(tool_name="read_file", content="file contents", tool_call_id="call-2")
    )
    with caplog.at_level("INFO", logger="pythia.agent"):
        _log_event(call)
        _log_event(result)
    assert "tool result ← read_file [success]" in caplog.text
    assert "file contents" in caplog.text
    assert "ms)" in caplog.text


def test_log_event_handles_orphan_result_without_recorded_start(
    caplog: pytest.LogCaptureFixture,
) -> None:
    result = FunctionToolResultEvent(
        part=ToolReturnPart(tool_name="rogue", content="anything", tool_call_id="never-seen-before")
    )
    with caplog.at_level("INFO", logger="pythia.agent"):
        _log_event(result)
    assert "tool result ← rogue" in caplog.text
    assert "(?)" in caplog.text


# --- MCP env scoping --------------------------------------------------------


def test_scoped_mcp_env_inherits_only_allowlisted_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    from pythia.agent import _MCP_INHERITED_ENV_VARS, _scoped_mcp_env

    monkeypatch.setenv("PATH", "/custom/path")
    monkeypatch.setenv("HOME", "/home/pythia")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-VERY-SECRET")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-also-secret")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA-NOPE")

    env = _scoped_mcp_env(None)

    assert env["PATH"] == "/custom/path"
    assert env["HOME"] == "/home/pythia"
    assert "SLACK_BOT_TOKEN" not in env, "Pythia secrets must not leak into MCP subprocesses"
    assert "OPENAI_API_KEY" not in env
    assert "AWS_ACCESS_KEY_ID" not in env
    # Belt-and-braces: nothing outside the allowlist made it in.
    assert set(env).issubset(set(_MCP_INHERITED_ENV_VARS))


def test_scoped_mcp_env_layers_explicit_config_on_top_of_inherited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pythia.agent import _scoped_mcp_env

    monkeypatch.setenv("PATH", "/custom/path")
    env = _scoped_mcp_env({"DD_API_KEY": "expanded-by-load-mcp", "PATH": "/override"})

    assert env["DD_API_KEY"] == "expanded-by-load-mcp"
    # Config-supplied env wins over inherited (operator's explicit choice).
    assert env["PATH"] == "/override"


# --- retry / timeout --------------------------------------------------------


class _StubResult:
    def __init__(self, output: str) -> None:
        self.output = output

    def all_messages(self) -> list[object]:
        return []


class _ScriptedAgent:
    """Mimics enough of Agent to drive `answer`'s retry loop. Each call to
    `run` consumes the next item in the script: an exception type to raise,
    or a string to return as the agent's final output."""

    def __init__(self, script: list[object]) -> None:
        self.script = list(script)
        self.calls = 0

    async def run(self, _prompt: object) -> _StubResult:
        self.calls += 1
        nxt = self.script.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return _StubResult(str(nxt))


def _zero_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Eliminate retry sleep so tests don't take seconds. We collapse the
    jitter range, not asyncio.sleep itself — patching sleep globally would
    also no-op any sleeps inside the agent under test."""
    monkeypatch.setattr("pythia.agent.random.uniform", lambda _a, _b: 0.0)


@pytest.mark.asyncio
async def test_answer_returns_immediately_when_first_attempt_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _zero_backoff(monkeypatch)
    agent = _ScriptedAgent(["the answer"])

    reply = await answer(
        cast(Any, agent), "hi", retry=RetryPolicy(max_attempts=4, timeout_seconds=10)
    )

    assert reply.text == "the answer"
    assert agent.calls == 1


@pytest.mark.asyncio
async def test_answer_retries_transient_failures_and_eventually_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _zero_backoff(monkeypatch)
    agent = _ScriptedAgent([RuntimeError("blip"), RuntimeError("still bad"), "third time lucky"])
    reply = await answer(
        cast(Any, agent), "hi", retry=RetryPolicy(max_attempts=4, timeout_seconds=10)
    )

    assert reply.text == "third time lucky"
    assert agent.calls == 3


@pytest.mark.asyncio
async def test_answer_re_raises_after_max_attempts_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _zero_backoff(monkeypatch)
    agent = _ScriptedAgent([RuntimeError("a"), RuntimeError("b"), RuntimeError("c")])
    with pytest.raises(RuntimeError, match="c"):
        await answer(cast(Any, agent), "hi", retry=RetryPolicy(max_attempts=3, timeout_seconds=10))
    assert agent.calls == 3


@pytest.mark.asyncio
async def test_answer_treats_per_attempt_timeout_as_a_retryable_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _zero_backoff(monkeypatch)

    class _SlowAgent:
        def __init__(self) -> None:
            self.calls = 0

        async def run(self, _prompt: object) -> _StubResult:
            self.calls += 1
            if self.calls < 3:
                # wait_for cancels this sleep after the policy's timeout.
                await asyncio.sleep(10)
            return _StubResult("got there")

    agent = _SlowAgent()
    reply = await answer(
        cast(Any, agent), "hi", retry=RetryPolicy(max_attempts=4, timeout_seconds=0.05)
    )
    assert reply.text == "got there"
    assert agent.calls == 3
