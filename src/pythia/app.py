import logging
import tempfile
from pathlib import Path
from typing import Any

from pydantic_ai import Agent
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_bolt.context.ack.async_ack import AsyncAck
from slack_bolt.context.say.async_say import AsyncSay
from slack_sdk.web.async_client import AsyncWebClient

from pythia.agent import ToolCall, answer, build_agent
from pythia.codebase import (
    build_codebase_tools,
    clone_all,
    parse_repos,
    read_grounding_docs,
    require_binaries,
)
from pythia.config import load
from pythia.slack_format import format_tool_trace, to_slack_mrkdwn
from pythia.slack_thread import fetch_thread, format_thread

logger = logging.getLogger(__name__)

PLACEHOLDER_REPLY = "_Pythia is thinking…_"
ERROR_REPLY = "Sorry — I hit an error. Check the bot logs."

ACTION_SHOW_TOOL_TRACE = "show_tool_trace"
ACTION_HIDE_TOOL_TRACE = "hide_tool_trace"
TRACE_BLOCK_ID = "pythia_tool_trace"
TRACE_ACTIONS_BLOCK_ID = "pythia_tool_trace_actions"

# Slack action `value` field is capped at 2000 chars; leave a little headroom.
_MAX_TRACE_VALUE = 1990

# Slack `section` block text is capped at 3000 chars. We split long answers
# into multiple section blocks; each chunk gets a little headroom under the
# limit so a stray prefix/suffix doesn't push us over.
_SECTION_CHUNK_LIMIT = 2900


def _truncate_trace(trace: str) -> str:
    if len(trace) <= _MAX_TRACE_VALUE:
        return trace
    return trace[: _MAX_TRACE_VALUE - 1] + "…"


def _chunk_for_sections(text: str, limit: int = _SECTION_CHUNK_LIMIT) -> list[str]:
    """Split text into chunks each ≤ `limit` chars, preferring paragraph,
    then line, then word boundaries before falling back to a hard slice.

    The boundary character(s) we cut at are treated as delimiters and dropped
    (a `\\n\\n` between paragraphs, the `\\n` ending a line, the space between
    words). On a hard slice we keep every character, so significant whitespace
    in code blocks or indented markdown is never silently eaten.
    """
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    min_acceptable_cut = limit // 4
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n\n", 0, limit)
        if cut > min_acceptable_cut:
            chunks.append(remaining[:cut])
            remaining = remaining[cut + 2 :]
            continue
        cut = remaining.rfind("\n", 0, limit)
        if cut > min_acceptable_cut:
            chunks.append(remaining[:cut])
            remaining = remaining[cut + 1 :]
            continue
        cut = remaining.rfind(" ", 0, limit)
        if cut > min_acceptable_cut:
            chunks.append(remaining[:cut])
            remaining = remaining[cut + 1 :]
            continue
        # No usable whitespace boundary — slice without stripping.
        chunks.append(remaining[:limit])
        remaining = remaining[limit:]
    return chunks


def _show_button(trace: str, count: int) -> dict[str, Any]:
    return {
        "type": "actions",
        "block_id": TRACE_ACTIONS_BLOCK_ID,
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": f"Show {count} tool call(s)"},
                "action_id": ACTION_SHOW_TOOL_TRACE,
                "value": trace,
            }
        ],
    }


def _hide_button(trace: str) -> dict[str, Any]:
    return {
        "type": "actions",
        "block_id": TRACE_ACTIONS_BLOCK_ID,
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Hide tool calls"},
                "action_id": ACTION_HIDE_TOOL_TRACE,
                "value": trace,
            }
        ],
    }


def _trace_block(trace: str) -> dict[str, Any]:
    return {
        "type": "section",
        "block_id": TRACE_BLOCK_ID,
        "text": {"type": "mrkdwn", "text": f"```\n{trace}\n```"},
    }


def reply_blocks(text: str, tool_calls: list[ToolCall]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": chunk}}
        for chunk in _chunk_for_sections(text)
    ]
    if tool_calls:
        trace = _truncate_trace(format_tool_trace(tool_calls))
        blocks.append(_show_button(trace, len(tool_calls)))
    return blocks


def _strip_trace_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [b for b in blocks if b.get("block_id") not in (TRACE_BLOCK_ID, TRACE_ACTIONS_BLOCK_ID)]


async def _update_with_blocks(
    client: AsyncWebClient, body: dict[str, Any], new_blocks: list[dict[str, Any]]
) -> None:
    await client.chat_update(
        channel=body["channel"]["id"],
        ts=body["message"]["ts"],
        text=body["message"].get("text", ""),
        blocks=new_blocks,
    )


async def expand_tool_trace(client: AsyncWebClient, body: dict[str, Any]) -> None:
    trace = body["actions"][0]["value"]
    base = _strip_trace_blocks(body["message"].get("blocks", []))
    await _update_with_blocks(client, body, [*base, _trace_block(trace), _hide_button(trace)])


async def collapse_tool_trace(client: AsyncWebClient, body: dict[str, Any]) -> None:
    trace = body["actions"][0]["value"]
    count = len(trace.splitlines()) if trace else 0
    base = _strip_trace_blocks(body["message"].get("blocks", []))
    await _update_with_blocks(client, body, [*base, _show_button(trace, count)])


async def respond_to_mention(
    agent: Agent[None, str],
    client: AsyncWebClient,
    say: AsyncSay,
    bot_user_id: str,
    event: dict[str, Any],
) -> None:
    thread_ts: str = event.get("thread_ts") or event["ts"]
    channel: str = event["channel"]

    try:
        placeholder = await say(text=PLACEHOLDER_REPLY, thread_ts=thread_ts)
    except Exception:
        logger.exception("failed to post placeholder reply")
        return

    placeholder_ts = str(placeholder["ts"])

    try:
        messages = await fetch_thread(client, channel, thread_ts)
        prompt = format_thread(messages, bot_user_id)
        reply = await answer(agent, prompt)
        text = to_slack_mrkdwn(reply.text)
        await client.chat_update(
            channel=channel,
            ts=placeholder_ts,
            text=text,
            blocks=reply_blocks(text, reply.tool_calls),
        )
    except Exception:
        logger.exception("agent run failed")
        await client.chat_update(channel=channel, ts=placeholder_ts, text=ERROR_REPLY)


def register_handlers(app: AsyncApp, agent: Agent[None, str], bot_user_id: str) -> None:
    @app.event("app_mention")
    async def handle_mention(event: dict[str, Any], client: AsyncWebClient, say: AsyncSay) -> None:
        await respond_to_mention(agent, client, say, bot_user_id, event)

    @app.action(ACTION_SHOW_TOOL_TRACE)
    async def handle_show(ack: AsyncAck, body: dict[str, Any], client: AsyncWebClient) -> None:
        await ack()
        await expand_tool_trace(client, body)

    @app.action(ACTION_HIDE_TOOL_TRACE)
    async def handle_hide(ack: AsyncAck, body: dict[str, Any], client: AsyncWebClient) -> None:
        await ack()
        await collapse_tool_trace(client, body)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = load()
    repo_specs = parse_repos(settings.codebase_repos)
    if repo_specs:
        require_binaries("git", "rg")

    with tempfile.TemporaryDirectory(prefix="pythia-repos-") as tmp:
        repos = await clone_all(repo_specs, Path(tmp))
        agent = build_agent(
            settings,
            extra_tools=build_codebase_tools(repos),
            grounding_docs=read_grounding_docs(repos),
        )
        app = AsyncApp(token=settings.slack_bot_token)
        auth = await app.client.auth_test()
        bot_user_id = str(auth["user_id"])
        register_handlers(app, agent, bot_user_id)
        handler = AsyncSocketModeHandler(app, settings.slack_app_token)
        logger.info(
            "Starting Pythia in Socket Mode as user %s with %d repo(s)",
            bot_user_id,
            len(repos),
        )
        async with agent.run_mcp_servers():
            await handler.start_async()
