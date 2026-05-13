from typing import Any
from unittest.mock import AsyncMock

import pytest

from pythia.agent import ToolCall
from pythia.app import (
    ACTION_HIDE_TOOL_TRACE,
    ACTION_SHOW_TOOL_TRACE,
    DISCLAIMER_BLOCK_ID,
    DISCLAIMER_TEXT,
    ERROR_REPLY,
    PLACEHOLDER_REPLY,
    TRACE_ACTIONS_BLOCK_ID,
    TRACE_BLOCK_ID,
    _is_handleable_im,
    collapse_tool_trace,
    expand_tool_trace,
    parse_allowed_channels,
    reply_blocks,
    respond_to_mention,
)

BOT_USER_ID = "UBOT123"


class FakeAgentResult:
    def __init__(self, output: str) -> None:
        self.output = output

    def all_messages(self) -> list[object]:
        return []  # tool-call paths are exercised in reply_blocks tests directly


def _fake_agent(output: str = "the answer") -> Any:
    agent = AsyncMock()
    agent.run.return_value = FakeAgentResult(output)
    return agent


def _fake_client(messages: list[dict[str, Any]] | None = None) -> AsyncMock:
    client = AsyncMock()
    client.conversations_replies.return_value = {"messages": messages or []}
    return client


def _fake_say(placeholder_ts: str = "200.0") -> AsyncMock:
    say = AsyncMock()
    say.return_value = {"ts": placeholder_ts}
    return say


# --- respond_to_mention -----------------------------------------------------


@pytest.mark.asyncio
async def test_respond_posts_placeholder_then_updates_with_the_real_answer() -> None:
    client = _fake_client(
        [
            {"user": "UALICE", "text": "why is the api slow?"},
            {"user": BOT_USER_ID, "text": "looking..."},
        ]
    )
    agent = _fake_agent("found it: **PROD-123**")
    say = _fake_say(placeholder_ts="200.5")
    event = {"channel": "C9", "ts": "100.0", "thread_ts": "99.0"}

    await respond_to_mention(agent, client, say, BOT_USER_ID, "xoxb-test", event)

    say.assert_awaited_once_with(text=PLACEHOLDER_REPLY, thread_ts="99.0")
    client.conversations_replies.assert_awaited_once_with(channel="C9", ts="99.0", limit=200)
    prompt = agent.run.await_args.args[0]
    assert "why is the api slow?" in prompt
    assert "pythia: looking..." in prompt
    client.chat_update.assert_awaited_once()
    update_kwargs = client.chat_update.await_args.kwargs
    assert update_kwargs["channel"] == "C9"
    assert update_kwargs["ts"] == "200.5"
    assert update_kwargs["text"] == "found it: *PROD-123*"  # mrkdwn-converted
    # No tool calls -> section block for the answer + context block for the disclaimer.
    blocks = update_kwargs["blocks"]
    assert len(blocks) == 2
    assert blocks[0]["type"] == "section"
    assert blocks[1]["block_id"] == DISCLAIMER_BLOCK_ID


@pytest.mark.asyncio
async def test_respond_falls_back_to_event_ts_when_no_thread_ts_present() -> None:
    client = _fake_client([{"user": "UALICE", "text": "hi"}])
    say = _fake_say()
    event = {"channel": "C9", "ts": "100.0"}

    await respond_to_mention(_fake_agent(), client, say, BOT_USER_ID, "xoxb-test", event)

    client.conversations_replies.assert_awaited_once_with(channel="C9", ts="100.0", limit=200)
    assert say.await_args is not None
    assert say.await_args.kwargs["thread_ts"] == "100.0"


@pytest.mark.asyncio
async def test_respond_updates_placeholder_with_error_when_agent_raises() -> None:
    client = _fake_client([{"user": "UALICE", "text": "hi"}])
    agent = AsyncMock()
    agent.run.side_effect = RuntimeError("boom")
    say = _fake_say(placeholder_ts="200.0")
    event = {"channel": "C9", "ts": "100.0"}

    await respond_to_mention(agent, client, say, BOT_USER_ID, "xoxb-test", event)

    client.chat_update.assert_awaited_once_with(channel="C9", ts="200.0", text=ERROR_REPLY)


@pytest.mark.asyncio
async def test_respond_updates_placeholder_with_error_when_slack_fetch_raises() -> None:
    client = AsyncMock()
    client.conversations_replies.side_effect = RuntimeError("slack down")
    say = _fake_say(placeholder_ts="200.0")
    event = {"channel": "C9", "ts": "100.0"}

    await respond_to_mention(_fake_agent(), client, say, BOT_USER_ID, "xoxb-test", event)

    client.chat_update.assert_awaited_once_with(channel="C9", ts="200.0", text=ERROR_REPLY)


@pytest.mark.asyncio
async def test_respond_aborts_silently_when_placeholder_post_fails() -> None:
    client = _fake_client([{"user": "UALICE", "text": "hi"}])
    say = AsyncMock(side_effect=RuntimeError("permission denied"))
    event = {"channel": "C9", "ts": "100.0"}

    await respond_to_mention(_fake_agent(), client, say, BOT_USER_ID, "xoxb-test", event)

    client.chat_update.assert_not_awaited()
    client.conversations_replies.assert_not_awaited()


# --- reply_blocks -----------------------------------------------------------


def _section_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [b for b in blocks if b["type"] == "section"]


def test_reply_blocks_omits_button_when_no_tool_calls_were_made() -> None:
    blocks = reply_blocks("the answer", [])
    # one section for the answer + one context block for the disclaimer
    assert len(blocks) == 2
    assert blocks[0]["type"] == "section"
    assert blocks[1]["block_id"] == DISCLAIMER_BLOCK_ID


def test_reply_blocks_always_appends_disclaimer_context_block() -> None:
    blocks = reply_blocks("the answer", [])
    disclaimer = next(b for b in blocks if b.get("block_id") == DISCLAIMER_BLOCK_ID)
    assert disclaimer["type"] == "context"
    assert disclaimer["elements"][0]["text"] == DISCLAIMER_TEXT


def test_reply_blocks_chunks_long_text_under_the_3000_char_section_limit() -> None:
    text = (
        ("paragraph one. " * 250 + "\n\n")
        + ("paragraph two. " * 250 + "\n\n")
        + ("paragraph three. " * 250)
    )
    blocks = reply_blocks(text, [])
    sections = _section_blocks(blocks)
    assert len(sections) > 1, "long text should be split across multiple section blocks"
    for block in sections:
        assert len(block["text"]["text"]) <= 3000, "every section must fit Slack's 3000-char cap"


def test_reply_blocks_preserves_significant_whitespace_when_hard_splitting() -> None:
    # A long unbroken token (no whitespace) forces a hard slice — every
    # original character must survive the split.
    text = "a" * 5000 + "\n    indented_code_should_keep_its_leading_spaces"
    blocks = reply_blocks(text, [])
    rejoined = "".join(b["text"]["text"] for b in _section_blocks(blocks))
    assert rejoined == text, "hard-cut chunks must concatenate back to the original"


def test_reply_blocks_drops_only_the_paragraph_delimiter_at_a_clean_split() -> None:
    # Two paragraphs each under the limit, joined by a blank line. The split
    # should land cleanly on the \n\n; the blank line is a delimiter (dropped)
    # but each paragraph's contents survive byte-for-byte.
    para_a = "alpha-paragraph-content-without-spaces" * 40  # one solid token, ~1520 chars
    para_b = "beta-paragraph-content-without-spaces" * 40
    text = para_a + "\n\n" + para_b
    blocks = reply_blocks(text, [])
    sections = _section_blocks(blocks)
    assert len(sections) == 2
    assert sections[0]["text"]["text"] == para_a
    assert sections[1]["text"]["text"] == para_b


def test_reply_blocks_appends_show_button_when_tool_calls_present() -> None:
    calls = [
        ToolCall(name="search_code", args='{"q": "load_mcp"}'),
        ToolCall(name="read_file", args='{"path": "agent.py"}'),
    ]
    blocks = reply_blocks("the answer", calls)
    # section + disclaimer + actions (show button)
    assert len(blocks) == 3
    assert blocks[0]["type"] == "section"
    assert blocks[1]["block_id"] == DISCLAIMER_BLOCK_ID
    assert blocks[2]["type"] == "actions"
    button = blocks[2]["elements"][0]
    assert button["action_id"] == ACTION_SHOW_TOOL_TRACE
    assert button["text"]["text"] == "Show 2 tool call(s)"
    # Trace text contains both calls, one per line.
    assert "search_code(" in button["value"]
    assert "read_file(" in button["value"]


# --- expand / collapse ------------------------------------------------------


def _click_body(action_id: str, value: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "channel": {"id": "C9"},
        "message": {"ts": "200.0", "text": "the answer", "blocks": blocks},
        "actions": [{"action_id": action_id, "value": value}],
        "trigger_id": "trig-1",
    }


@pytest.mark.asyncio
async def test_expand_appends_trace_section_and_swaps_button_to_hide() -> None:
    initial = reply_blocks("the answer", [ToolCall(name="search_code", args='{"q": "x"}')])
    show_button = next(b for b in initial if b.get("block_id") == TRACE_ACTIONS_BLOCK_ID)
    trace_value = show_button["elements"][0]["value"]
    body = _click_body(ACTION_SHOW_TOOL_TRACE, trace_value, initial)
    client = AsyncMock()

    await expand_tool_trace(client, body)

    client.chat_update.assert_awaited_once()
    new_blocks = client.chat_update.await_args.kwargs["blocks"]
    block_ids = [b.get("block_id") for b in new_blocks]
    assert TRACE_BLOCK_ID in block_ids
    assert TRACE_ACTIONS_BLOCK_ID in block_ids
    assert DISCLAIMER_BLOCK_ID in block_ids, "disclaimer must survive expand/collapse"
    trace_block = next(b for b in new_blocks if b.get("block_id") == TRACE_BLOCK_ID)
    assert "search_code(" in trace_block["text"]["text"]
    actions_block = next(b for b in new_blocks if b.get("block_id") == TRACE_ACTIONS_BLOCK_ID)
    assert actions_block["elements"][0]["action_id"] == ACTION_HIDE_TOOL_TRACE


@pytest.mark.asyncio
async def test_collapse_removes_trace_section_and_swaps_button_to_show() -> None:
    initial = reply_blocks("the answer", [ToolCall(name="search_code", args='{"q": "x"}')])
    show_button = next(b for b in initial if b.get("block_id") == TRACE_ACTIONS_BLOCK_ID)
    show_value = show_button["elements"][0]["value"]
    disclaimer = next(b for b in initial if b.get("block_id") == DISCLAIMER_BLOCK_ID)
    # Simulate the post-expand state: answer section, disclaimer, trace section, hide button.
    expanded_blocks = [
        initial[0],
        disclaimer,
        {
            "type": "section",
            "block_id": TRACE_BLOCK_ID,
            "text": {"type": "mrkdwn", "text": f"```\n{show_value}\n```"},
        },
        {
            "type": "actions",
            "block_id": TRACE_ACTIONS_BLOCK_ID,
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Hide tool calls"},
                    "action_id": ACTION_HIDE_TOOL_TRACE,
                    "value": show_value,
                }
            ],
        },
    ]
    body = _click_body(ACTION_HIDE_TOOL_TRACE, show_value, expanded_blocks)
    client = AsyncMock()

    await collapse_tool_trace(client, body)

    client.chat_update.assert_awaited_once()
    new_blocks = client.chat_update.await_args.kwargs["blocks"]
    block_ids = [b.get("block_id") for b in new_blocks]
    assert TRACE_BLOCK_ID not in block_ids
    assert TRACE_ACTIONS_BLOCK_ID in block_ids
    assert DISCLAIMER_BLOCK_ID in block_ids, "disclaimer must survive expand/collapse"
    actions_block = next(b for b in new_blocks if b.get("block_id") == TRACE_ACTIONS_BLOCK_ID)
    assert actions_block["elements"][0]["action_id"] == ACTION_SHOW_TOOL_TRACE


# --- channel allowlist ------------------------------------------------------


def test_parse_allowed_channels_returns_none_when_unset() -> None:
    assert parse_allowed_channels(None) is None


def test_parse_allowed_channels_splits_and_trims() -> None:
    assert parse_allowed_channels("C1, C2 ,  C3") == frozenset({"C1", "C2", "C3"})


def test_parse_allowed_channels_returns_empty_set_for_empty_string() -> None:
    # Empty string is "explicitly mute the bot" — different from None (unset).
    assert parse_allowed_channels("") == frozenset()


@pytest.mark.asyncio
async def test_respond_ignores_mention_in_disallowed_channel() -> None:
    client = AsyncMock()
    say = AsyncMock()
    event = {"channel": "C-OFFLIMITS", "ts": "100.0"}

    await respond_to_mention(
        AsyncMock(),
        client,
        say,
        BOT_USER_ID,
        "xoxb-test",
        event,
        allowed_channels=frozenset({"C-OK"}),
    )

    say.assert_not_awaited()
    client.chat_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_respond_responds_normally_when_channel_is_in_the_allowlist() -> None:
    client = _fake_client([{"user": "UALICE", "text": "hi"}])
    say = _fake_say()
    event = {"channel": "C-OK", "ts": "100.0"}

    await respond_to_mention(
        _fake_agent(),
        client,
        say,
        BOT_USER_ID,
        "xoxb-test",
        event,
        allowed_channels=frozenset({"C-OK"}),
    )

    say.assert_awaited_once()
    client.chat_update.assert_awaited_once()


# --- DM (message.im) handling ----------------------------------------------


def test_is_handleable_im_accepts_a_user_message_in_an_im_channel() -> None:
    event = {"channel_type": "im", "user": "UALICE", "text": "hi"}
    assert _is_handleable_im(event, BOT_USER_ID) is True


def test_is_handleable_im_rejects_messages_in_non_im_channels() -> None:
    event = {"channel_type": "channel", "user": "UALICE", "text": "hi"}
    assert _is_handleable_im(event, BOT_USER_ID) is False


def test_is_handleable_im_rejects_message_subtypes_like_edits_and_joins() -> None:
    event = {
        "channel_type": "im",
        "user": "UALICE",
        "subtype": "message_changed",
        "text": "edited",
    }
    assert _is_handleable_im(event, BOT_USER_ID) is False


def test_is_handleable_im_rejects_messages_from_the_bot_itself() -> None:
    event = {"channel_type": "im", "user": BOT_USER_ID, "text": "hi"}
    assert _is_handleable_im(event, BOT_USER_ID) is False


def test_is_handleable_im_rejects_messages_from_other_bots() -> None:
    event = {"channel_type": "im", "bot_id": "BSOMETHING", "text": "ping"}
    assert _is_handleable_im(event, BOT_USER_ID) is False
