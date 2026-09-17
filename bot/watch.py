import logging

import config
from bot import state
from bot.usergroups import ensure_member, ensure_not_member

logger = logging.getLogger("noodle")


def _log(text: str) -> None:
    if not config.LOGS_CHANNEL_ID:
        return
    try:
        state.app.client.chat_postMessage(channel=config.LOGS_CHANNEL_ID, text=text)
    except Exception:  # noqa: BLE001
        logger.exception("failed to post to logs channel")


def handle_member_joined_channel(event: dict) -> None:
    channel = event.get("channel")
    user = event.get("user")
    if not config.WATCHED_CHANNEL_ID or channel != config.WATCHED_CHANNEL_ID:
        return
    if not user or user == state.AUTH_USER_ID:
        return

    group_note = ""
    if config.MATTHIAS_DAY_GROUP_ID:
        result = ensure_member(config.MATTHIAS_DAY_GROUP_ID, user)
        if result == "added":
            group_note = " and got added to the @matthias-day ping group"
        elif result == "already":
            group_note = " (already in the @matthias-day ping group)"
        elif result == "failed":
            group_note = " (couldn't add them to the @matthias-day ping group, check logs)"

    _log(f"<@{user}> joined <#{channel}>{group_note}.")

    if config.JOIN_EPHEMERAL_MESSAGE:
        text = config.JOIN_EPHEMERAL_MESSAGE.format(user=f"<@{user}>")
        try:
            state.app.client.chat_postEphemeral(
                channel=channel, user=config.USER_ID, text=text
            )
        except Exception:  # noqa: BLE001
            logger.exception("failed to post join ephemeral message")


def handle_member_left_channel(event: dict) -> None:
    channel = event.get("channel")
    user = event.get("user")
    if not config.WATCHED_CHANNEL_ID or channel != config.WATCHED_CHANNEL_ID:
        return
    if not user or user == state.AUTH_USER_ID:
        return

    group_note = ""
    if config.MATTHIAS_DAY_GROUP_ID:
        result = ensure_not_member(config.MATTHIAS_DAY_GROUP_ID, user)
        if result == "removed":
            group_note = " and got removed from the @matthias-day ping group"
        elif result == "failed":
            group_note = " (they were in the @matthias-day ping group but removing them failed, check logs)"
        # "not_member" -> they weren't in the group, nothing to note

    _log(f"<@{user}> left <#{channel}>{group_note}.")
