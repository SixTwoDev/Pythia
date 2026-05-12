import re
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


def format_thread(messages: list[dict[str, Any]], bot_user_id: str) -> str:
    lines: list[str] = []
    for message in messages:
        text = _strip_self_mention((message.get("text") or "").strip(), bot_user_id)
        files = message.get("files") or []
        if not text and not files:
            continue
        user = message.get("user") or message.get("bot_id") or "unknown"
        speaker = "pythia" if user == bot_user_id else f"<@{user}>"
        line = f"{speaker}: {text}".rstrip()
        if files:
            names = ", ".join(str(f.get("name") or "?") for f in files)
            line = f"{line} [attached: {names}]" if text else f"{speaker}: [attached: {names}]"
        lines.append(line)
    return "\n".join(lines)
