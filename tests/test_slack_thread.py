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


def test_format_thread_pulls_text_from_legacy_attachments_when_top_level_text_is_empty() -> None:
    # Webhooks (CI bots, monitoring integrations) routinely post a parent
    # message with an empty `text` field and their real content in
    # `attachments`. Without an attachments fallback, Pythia silently drops
    # the parent and the @-mention reply has no context to answer against.
    messages = [
        {
            "bot_id": "BCIBOT",
            "text": "",
            "attachments": [
                {
                    "fallback": "Deploy #42 failed on main",
                    "title": "Deploy #42 failed",
                    "text": "Build broke at step `pytest`",
                    "fields": [
                        {"title": "Branch", "value": "main"},
                        {"title": "Duration", "value": "2m31s"},
                    ],
                }
            ],
        },
        {"user": "UALICE", "text": f"<@{BOT_USER_ID}> why?"},
    ]
    formatted = format_thread(messages, BOT_USER_ID)
    assert "Deploy #42 failed" in formatted
    assert "Build broke at step `pytest`" in formatted
    assert "Branch: main" in formatted
    assert "<@UALICE>: why?" in formatted


def test_format_thread_pulls_text_from_block_kit_blocks_when_top_level_text_is_empty() -> None:
    # Modern apps (GitHub, PagerDuty, etc.) post via Block Kit and leave
    # `text` empty. We need to walk the block tree and rebuild a readable
    # message from any `text` nodes we find.
    messages = [
        {
            "bot_id": "BGITHUB",
            "text": "",
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": "PR #99 opened"}},
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "*Add new feature* by @alice"},
                },
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": "Repository: example/repo"}],
                },
            ],
        },
        {"user": "UBOB", "text": f"<@{BOT_USER_ID}> summarise"},
    ]
    formatted = format_thread(messages, BOT_USER_ID)
    assert "PR #99 opened" in formatted
    assert "Add new feature" in formatted
    assert "Repository: example/repo" in formatted
    assert "<@UBOB>: summarise" in formatted


def test_format_thread_still_skips_message_with_no_text_no_blocks_no_attachments() -> None:
    messages = [
        {"user": "UALICE", "text": "kept"},
        {"user": "UBOB"},  # truly content-less
        {"user": "UCAROL", "text": "kept too"},
    ]
    assert format_thread(messages, BOT_USER_ID) == "<@UALICE>: kept\n<@UCAROL>: kept too"


def test_format_thread_dedups_when_top_level_text_exactly_matches_an_attachment_fallback() -> None:
    # Many integrations set BOTH `text` and `attachments.fallback` to the
    # same summary string. Render it once, not twice.
    messages = [
        {
            "bot_id": "BCIBOT",
            "text": "Deploy #42 failed on main",
            "attachments": [{"fallback": "Deploy #42 failed on main"}],
        }
    ]
    assert format_thread(messages, BOT_USER_ID) == "<@BCIBOT>: Deploy #42 failed on main"


def test_format_thread_concatenates_text_blocks_and_attachments_when_each_adds_signal() -> None:
    # An integration can put a short headline in `text`, rich rendering
    # in `blocks`, and a structured fallback in `attachments` — each
    # carries information the others don't, so render all three.
    messages = [
        {
            "bot_id": "BCIBOT",
            "text": "Deploy failed",
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "Step `pytest` exited 1"},
                },
            ],
            "attachments": [
                {
                    "fallback": "Deploy #42 on main",
                    "fields": [{"title": "Duration", "value": "2m31s"}],
                },
            ],
        }
    ]
    formatted = format_thread(messages, BOT_USER_ID)
    assert "Deploy failed" in formatted
    assert "Step `pytest` exited 1" in formatted
    assert "Deploy #42 on main" in formatted
    assert "Duration: 2m31s" in formatted


def test_format_thread_prefixes_each_line_with_the_messages_iso_timestamp() -> None:
    # Real Slack messages always carry `ts` (seconds since epoch). Surface
    # it to the model in ISO-8601 UTC so triage questions like "what happened
    # right before the deploy at 14:00 UTC?" can use timestamps as anchors.
    messages = [
        {"user": "UALICE", "text": "deploy started", "ts": "1747166400.000100"},
        {"user": "UALICE", "text": "deploy failed", "ts": "1747166460.000200"},
    ]
    assert format_thread(messages, BOT_USER_ID) == (
        "[2025-05-13T20:00:00Z] <@UALICE>: deploy started\n"
        "[2025-05-13T20:01:00Z] <@UALICE>: deploy failed"
    )


def test_format_thread_skips_timestamp_prefix_when_ts_is_missing_or_malformed() -> None:
    # Synthetic test data can omit `ts`; production never does. Tolerate it
    # by rendering the line without a prefix rather than producing a
    # nonsense `[invalid] <@user>:` line. Out-of-range numeric ts values
    # (e.g. "1e30") parse cleanly through float() but blow up datetime —
    # they take the same skip-prefix path.
    messages = [
        {"user": "UALICE", "text": "no ts"},
        {"user": "UBOB", "text": "garbage ts", "ts": "not-a-number"},
        {"user": "UDAVE", "text": "overflow ts", "ts": "1e30"},
        {"user": "UCAROL", "text": "good ts", "ts": "1747166400"},
    ]
    formatted = format_thread(messages, BOT_USER_ID)
    assert "<@UALICE>: no ts" in formatted
    assert "<@UBOB>: garbage ts" in formatted
    assert "<@UDAVE>: overflow ts" in formatted
    assert "[2025-05-13T20:00:00Z] <@UCAROL>: good ts" in formatted


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
