import logging
import tempfile
from pathlib import Path
from typing import Any

from pydantic_ai import Agent
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_bolt.context.say.async_say import AsyncSay
from slack_sdk.web.async_client import AsyncWebClient

from pythia.agent import answer, build_agent
from pythia.codebase import (
    build_codebase_tools,
    clone_all,
    parse_repos,
    read_grounding_docs,
    require_binaries,
)
from pythia.config import load
from pythia.slack_format import to_slack_mrkdwn
from pythia.slack_thread import fetch_thread, format_thread

logger = logging.getLogger(__name__)

PLACEHOLDER_REPLY = "_Pythia is thinking…_"
ERROR_REPLY = "Sorry — I hit an error. Check the bot logs."


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
        reply = to_slack_mrkdwn(await answer(agent, prompt))
        await client.chat_update(channel=channel, ts=placeholder_ts, text=reply)
    except Exception:
        logger.exception("agent run failed")
        await client.chat_update(channel=channel, ts=placeholder_ts, text=ERROR_REPLY)


def register_handlers(app: AsyncApp, agent: Agent[None, str], bot_user_id: str) -> None:
    @app.event("app_mention")
    async def handle_mention(event: dict[str, Any], client: AsyncWebClient, say: AsyncSay) -> None:
        await respond_to_mention(agent, client, say, bot_user_id, event)


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
