from typing import Any
from unittest.mock import AsyncMock

import pytest

from pythia.slack_thread import fetch_thread, format_thread

BOT_USER_ID = "UBOT123"


def test_format_thread_marks_bot_messages_as_pythia() -> None:
    messages = [{"user": BOT_USER_ID, "text": "previous answer"}]
    assert format_thread(messages, BOT_USER_ID) == "pythia: previous answer"


def test_format_thread_marks_human_messages_with_their_user_mention() -> None:
    messages = [{"user": "UALICE", "text": "why is the api slow?"}]
    assert format_thread(messages, BOT_USER_ID) == "<@UALICE>: why is the api slow?"


def test_format_thread_skips_messages_with_empty_or_whitespace_text() -> None:
    messages = [
        {"user": "UALICE", "text": "first"},
        {"user": "UBOB", "text": "   "},
        {"user": "UCAROL", "text": ""},
        {"user": "UDAVE", "text": "second"},
    ]
    assert format_thread(messages, BOT_USER_ID) == "<@UALICE>: first\n<@UDAVE>: second"


def test_format_thread_falls_back_to_bot_id_then_unknown_when_user_field_absent() -> None:
    messages = [
        {"bot_id": "BOTHER", "text": "from another bot"},
        {"text": "ghost message"},
    ]
    assert (
        format_thread(messages, BOT_USER_ID)
        == "<@BOTHER>: from another bot\n<@unknown>: ghost message"
    )


def test_format_thread_strips_bots_own_user_id_mention_from_message_text() -> None:
    # When a user @-mentions Pythia in a thread reply, the raw text contains
    # the literal `<@UBOT123>` token. The model has no way to know that ID
    # refers to itself, so leaving it in makes the question read like it's
    # addressed to some unknown third party — and the parent message stops
    # feeling like the context for *that* question. Strip it.
    messages = [
        {"user": "UALICE", "text": "the deploy failed"},
        {"user": "UALICE", "text": f"<@{BOT_USER_ID}> why?"},
    ]
    formatted = format_thread(messages, BOT_USER_ID)
    assert f"<@{BOT_USER_ID}>" not in formatted
    assert formatted == "<@UALICE>: the deploy failed\n<@UALICE>: why?"


def test_format_thread_strips_self_mention_even_when_not_at_start() -> None:
    messages = [{"user": "UALICE", "text": f"hey <@{BOT_USER_ID}> can you explain?"}]
    assert format_thread(messages, BOT_USER_ID) == "<@UALICE>: hey can you explain?"


def test_format_thread_skips_message_that_is_only_a_bare_self_mention() -> None:
    # A message containing nothing but `<@UBOT>` has no content to feed the
    # model once the mention is stripped, so it should be dropped like any
    # other empty-text message.
    messages = [
        {"user": "UALICE", "text": "first"},
        {"user": "UBOB", "text": f"<@{BOT_USER_ID}>"},
        {"user": "UCAROL", "text": "second"},
    ]
    assert format_thread(messages, BOT_USER_ID) == "<@UALICE>: first\n<@UCAROL>: second"


def test_format_thread_preserves_message_order() -> None:
    messages = [
        {"user": "UALICE", "text": "1"},
        {"user": BOT_USER_ID, "text": "2"},
        {"user": "UALICE", "text": "3"},
    ]
    assert format_thread(messages, BOT_USER_ID) == "<@UALICE>: 1\npythia: 2\n<@UALICE>: 3"


@pytest.mark.asyncio
async def test_fetch_thread_calls_conversations_replies_and_returns_messages_list() -> None:
    expected: list[dict[str, Any]] = [
        {"user": "UALICE", "text": "hello"},
        {"user": BOT_USER_ID, "text": "hi"},
    ]
    client = AsyncMock()
    client.conversations_replies.return_value = {"messages": expected}
    result = await fetch_thread(client, channel="C123", thread_ts="1234.0001")
    client.conversations_replies.assert_awaited_once_with(channel="C123", ts="1234.0001", limit=200)
    assert result == expected


@pytest.mark.asyncio
async def test_fetch_thread_returns_empty_list_when_response_has_no_messages() -> None:
    client = AsyncMock()
    client.conversations_replies.return_value = {}
    assert await fetch_thread(client, channel="C123", thread_ts="1234.0001") == []
