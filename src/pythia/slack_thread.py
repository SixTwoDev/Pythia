import re
from datetime import UTC, datetime
from typing import Any

from slack_sdk.web.async_client import AsyncWebClient


async def fetch_thread(
    client: AsyncWebClient, channel: str, thread_ts: str
) -> list[dict[str, Any]]:
    response = await client.conversations_replies(channel=channel, ts=thread_ts, limit=200)
    messages = response.get("messages") or []
    return list(messages)


def _strip_self_mention(text: str, bot_user_id: str) -> str:
    """Remove every `<@BOT_ID>` reference from a message's text.

    The model has no way to know its own Slack user-ID, so leaving the raw
    `<@UBOT123>` token in the prompt makes the question read as if it were
    addressed to some unknown third user — and the parent message stops
    feeling like context *for that question*. Stripping it lets the @-mention
    reply read naturally, e.g. "<@UALICE>: why?" instead of
    "<@UALICE>: <@UBOT123> why?".
    """
    pattern = rf"<@{re.escape(bot_user_id)}>\s*"
    return re.sub(pattern, "", text).strip()


def _extract_blocks_text(blocks: list[Any]) -> str:
    """Pull rendered text out of Slack Block Kit blocks.

    App and webhook messages routinely leave the top-level `text` field
    empty and put the real content in `blocks` — without a fallback we
    silently drop the entire message before the model sees it. We walk
    the block tree and collect every string under any `text` key, which
    covers section/header/context/rich_text and the common nested shapes
    without needing to enumerate every block type Slack might add.
    """
    parts: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "text":
                    if isinstance(value, str):
                        parts.append(value)
                    elif isinstance(value, dict):
                        inner = value.get("text")
                        if isinstance(inner, str):
                            parts.append(inner)
                        else:
                            walk(value)
                    else:
                        walk(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(blocks)
    return " ".join(p.strip() for p in parts if p and p.strip())


def _extract_attachments_text(attachments: list[Any]) -> str:
    """Pull readable text out of legacy `attachments` blobs.

    `fallback` is Slack's own "best-effort plain-text rendering" of an
    attachment and is set by virtually every integration, so we try it
    first; we then fold in pretext/title/text/fields for any extra
    signal the integration provided.
    """
    parts: list[str] = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        for key in ("pretext", "title", "text", "fallback"):
            value = attachment.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
        for field in attachment.get("fields") or []:
            if not isinstance(field, dict):
                continue
            title = (field.get("title") or "").strip()
            value = (field.get("value") or "").strip()
            if title and value:
                parts.append(f"{title}: {value}")
            elif value:
                parts.append(value)
    return "\n".join(parts)


def _message_text(message: dict[str, Any], bot_user_id: str) -> str:
    """Build a full readable rendering of a Slack message.

    Pulls text from every source the message exposes — top-level `text`,
    Block Kit `blocks`, and legacy `attachments` — and concatenates them.
    Integrations vary wildly: a CI bot might put a one-line summary in
    `text` and the real detail in `attachments`; another might leave
    `text` empty and put everything in `blocks`. Reading every source
    means we never silently drop content the user can see in Slack.
    Exact duplicates between sources (e.g. `text` matching an
    attachment's `fallback`) are de-duplicated so we don't render the
    same line twice.
    """
    parts: list[str] = []
    raw_text = (message.get("text") or "").strip()
    if raw_text:
        parts.append(raw_text)
    blocks_text = _extract_blocks_text(message.get("blocks") or [])
    if blocks_text:
        parts.append(blocks_text)
    attachments_text = _extract_attachments_text(message.get("attachments") or [])
    if attachments_text:
        parts.append(attachments_text)
    seen: set[str] = set()
    deduped: list[str] = []
    for part in parts:
        if part not in seen:
            seen.add(part)
            deduped.append(part)
    return _strip_self_mention("\n".join(deduped), bot_user_id)


def _ts_to_iso(ts: Any) -> str:
    """Convert a Slack message `ts` ("1234567890.123456") to ISO-8601 UTC.

    Returns "" for missing or malformed values so callers can render
    timestamp-prefixed lines unconditionally when present and skip the
    prefix when absent (only really happens in synthetic test data —
    real Slack messages always carry `ts`).
    """
    if not isinstance(ts, str) or not ts:
        return ""
    try:
        seconds = float(ts)
    except ValueError:
        return ""
    return datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_thread(messages: list[dict[str, Any]], bot_user_id: str) -> str:
    lines: list[str] = []
    for message in messages:
        text = _message_text(message, bot_user_id)
        files = message.get("files") or []
        if not text and not files:
            continue
        user = message.get("user") or message.get("bot_id") or "unknown"
        speaker = "pythia" if user == bot_user_id else f"<@{user}>"
        when = _ts_to_iso(message.get("ts"))
        prefix = f"[{when}] " if when else ""
        line = f"{prefix}{speaker}: {text}".rstrip()
        if files:
            names = ", ".join(str(f.get("name") or "?") for f in files)
            line = (
                f"{line} [attached: {names}]" if text else f"{prefix}{speaker}: [attached: {names}]"
            )
        lines.append(line)
    return "\n".join(lines)
