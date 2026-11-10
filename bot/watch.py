import logging

import config
from bot import state
from bot.usergroups import ensure_member, ensure_not_member

logger = logging.getLogger("noodle")


def _log(text: str) -> None:
    if not config.LOGS_CHANNEL_ID:
        logger.warning("not logging %r: LOGS_CHANNEL_ID is empty", text)
        return
    try:
        state.app.client.chat_postMessage(channel=config.LOGS_CHANNEL_ID, text=text)
        logger.info("logged to %s: %s", config.LOGS_CHANNEL_ID, text)
    except Exception:  # noqa: BLE001
        logger.exception("failed to post to logs channel %s", config.LOGS_CHANNEL_ID)


def handle_member_joined_channel(event: dict) -> None:
    channel = event.get("channel")
    user = event.get("user")
    if not config.WATCHED_CHANNEL_ID or channel != config.WATCHED_CHANNEL_ID:
        # slack delivers these for every channel the user token can see; ignore the rest quietly
        logger.debug("ignoring member_joined_channel for unwatched channel %s", channel)
        return
    logger.info("member_joined_channel event: user=%s channel=%s", user, channel)
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

    _log(f"<@{user}> joined <#{channel}>{group_note} :yay:")

    if config.JOIN_EPHEMERAL_MESSAGE:
        text = config.JOIN_EPHEMERAL_MESSAGE.format(user=f"<@{user}>")
        try:
            state.app.client.chat_postEphemeral(
                channel=channel, user=user, text=text
            )
        except Exception:  # noqa: BLE001
            logger.exception("failed to post join ephemeral message")


def handle_member_left_channel(event: dict) -> None:
    channel = event.get("channel")
    user = event.get("user")
    if not config.WATCHED_CHANNEL_ID or channel != config.WATCHED_CHANNEL_ID:
        # slack delivers these for every channel the user token can see; ignore the rest quietly
        logger.debug("ignoring member_left_channel for unwatched channel %s", channel)
        return
    logger.info("member_left_channel event: user=%s channel=%s", user, channel)
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
