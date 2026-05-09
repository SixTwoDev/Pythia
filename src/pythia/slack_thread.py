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
        if not text:
            continue
        user = message.get("user") or message.get("bot_id") or "unknown"
        speaker = "pythia" if user == bot_user_id else f"<@{user}>"
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines)
