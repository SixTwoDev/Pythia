import logging
from typing import Any

from pydantic_ai import Agent
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_bolt.context.say.async_say import AsyncSay
from slack_sdk.web.async_client import AsyncWebClient

from pythia.agent import answer, build_agent
from pythia.config import load
from pythia.slack_thread import fetch_thread, format_thread

logger = logging.getLogger(__name__)

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
        messages = await fetch_thread(client, channel, thread_ts)
        prompt = format_thread(messages, bot_user_id)
        reply = await answer(agent, prompt)
        await say(text=reply, thread_ts=thread_ts)
    except Exception:
        logger.exception("agent run failed")
        await say(text=ERROR_REPLY, thread_ts=thread_ts)


def register_handlers(app: AsyncApp, agent: Agent[None, str], bot_user_id: str) -> None:
    @app.event("app_mention")
    async def handle_mention(event: dict[str, Any], client: AsyncWebClient, say: AsyncSay) -> None:
        await respond_to_mention(agent, client, say, bot_user_id, event)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = load()
    agent = build_agent(settings)
    app = AsyncApp(token=settings.slack_bot_token)
    auth = await app.client.auth_test()
    bot_user_id = str(auth["user_id"])
    register_handlers(app, agent, bot_user_id)
    handler = AsyncSocketModeHandler(app, settings.slack_app_token)
    logger.info("Starting Pythia in Socket Mode as user %s", bot_user_id)
    await handler.start_async()
