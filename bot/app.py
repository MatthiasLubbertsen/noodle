import logging

from openai import OpenAI
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

import config
from bot import state
from bot.handlers import handle_message
from bot.log import setup_logging

logger = logging.getLogger("noodle")


def build_app() -> App:
    setup_logging()

    application = App(token=config.SLACK_USER_TOKEN)
    client = OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.AI_ENDPOINT)

    # resolve our own identity so we never reply to ourselves
    auth = application.client.auth_test()
    state.AUTH_USER_ID = auth.get("user_id")
    state.BOT_ID = auth.get("bot_id")
    state.MENTION_IDS = {mid for mid in (state.AUTH_USER_ID, state.BOT_ID) if mid}
    state.app = application
    state.client = client
    state.SYSTEM_PROMPT = config.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")

    # wire the message event handler
    application.event("message")(handle_message)

    logger.info(
        "noodle online as user=%s bot=%s", state.AUTH_USER_ID, state.BOT_ID
    )
    return application


def start_bot() -> None:
    application = build_app()
    logger.info("starting noodle in socket mode...")
    SocketModeHandler(application, config.SLACK_APP_TOKEN).start()
