import logging
from typing import Any

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_bolt.context.say.async_say import AsyncSay

from pythia.config import load

logger = logging.getLogger(__name__)


def build_app(bot_token: str) -> AsyncApp:
    app = AsyncApp(token=bot_token)

    @app.event("app_mention")
    async def handle_mention(event: dict[str, Any], say: AsyncSay) -> None:
        thread_ts: str = event.get("thread_ts") or event["ts"]
        await say(text="Pythia is awake. Agent loop not implemented yet.", thread_ts=thread_ts)

    return app


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = load()
    app = build_app(settings.slack_bot_token)
    handler = AsyncSocketModeHandler(app, settings.slack_app_token)
    logger.info("Starting Pythia in Socket Mode")
    await handler.start_async()
