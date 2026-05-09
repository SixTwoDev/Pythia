from typing import Any
from unittest.mock import AsyncMock

import pytest

from pythia.app import ERROR_REPLY, respond_to_mention

BOT_USER_ID = "UBOT123"


class FakeAgentResult:
    def __init__(self, output: str) -> None:
        self.output = output


def _fake_agent(output: str = "the answer") -> Any:
    agent = AsyncMock()
    agent.run.return_value = FakeAgentResult(output)
    return agent


def _fake_client(messages: list[dict[str, Any]] | None = None) -> AsyncMock:
    client = AsyncMock()
    client.conversations_replies.return_value = {"messages": messages or []}
    return client


@pytest.mark.asyncio
async def test_respond_fetches_thread_runs_agent_and_replies_in_thread() -> None:
    client = _fake_client(
        [
            {"user": "UALICE", "text": "why is the api slow?"},
            {"user": BOT_USER_ID, "text": "looking..."},
        ]
    )
    agent = _fake_agent("found it: PROD-123")
    say = AsyncMock()
    event = {"channel": "C9", "ts": "100.0", "thread_ts": "99.0"}

    await respond_to_mention(agent, client, say, BOT_USER_ID, event)

    client.conversations_replies.assert_awaited_once_with(channel="C9", ts="99.0", limit=200)
    prompt = agent.run.await_args.args[0]
    assert "why is the api slow?" in prompt
    assert "pythia: looking..." in prompt
    say.assert_awaited_once_with(text="found it: PROD-123", thread_ts="99.0")


@pytest.mark.asyncio
async def test_respond_falls_back_to_event_ts_when_no_thread_ts_present() -> None:
    client = _fake_client([{"user": "UALICE", "text": "hi"}])
    say = AsyncMock()
    event = {"channel": "C9", "ts": "100.0"}

    await respond_to_mention(_fake_agent(), client, say, BOT_USER_ID, event)

    client.conversations_replies.assert_awaited_once_with(channel="C9", ts="100.0", limit=200)
    say.assert_awaited_once_with(text="the answer", thread_ts="100.0")


@pytest.mark.asyncio
async def test_respond_posts_friendly_error_when_agent_raises() -> None:
    client = _fake_client([{"user": "UALICE", "text": "hi"}])
    agent = AsyncMock()
    agent.run.side_effect = RuntimeError("boom")
    say = AsyncMock()
    event = {"channel": "C9", "ts": "100.0"}

    await respond_to_mention(agent, client, say, BOT_USER_ID, event)

    say.assert_awaited_once_with(text=ERROR_REPLY, thread_ts="100.0")


@pytest.mark.asyncio
async def test_respond_posts_friendly_error_when_slack_fetch_raises() -> None:
    client = AsyncMock()
    client.conversations_replies.side_effect = RuntimeError("slack down")
    say = AsyncMock()
    event = {"channel": "C9", "ts": "100.0"}

    await respond_to_mention(_fake_agent(), client, say, BOT_USER_ID, event)

    say.assert_awaited_once_with(text=ERROR_REPLY, thread_ts="100.0")
