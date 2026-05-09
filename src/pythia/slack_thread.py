from typing import Any

from slack_sdk.web.async_client import AsyncWebClient


async def fetch_thread(
    client: AsyncWebClient, channel: str, thread_ts: str
) -> list[dict[str, Any]]:
    response = await client.conversations_replies(channel=channel, ts=thread_ts, limit=200)
    messages = response.get("messages") or []
    return list(messages)


def format_thread(messages: list[dict[str, Any]], bot_user_id: str) -> str:
    lines: list[str] = []
    for message in messages:
        text = (message.get("text") or "").strip()
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
