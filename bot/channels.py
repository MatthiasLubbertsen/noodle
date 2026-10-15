import logging

from slack_sdk.errors import SlackApiError

import config
from bot import state

logger = logging.getLogger("noodle")

# errors that mean "noodle isn't in that channel" (a private channel it can't
# see reports channel_not_found, which joining can't fix - that just fails)
_NOT_IN = {"not_in_channel", "channel_not_found"}


def join(channel_id: str) -> str:
    """join a public channel as noodle's own account. posts nothing (slack
    itself may show its standard "noodle joined" line). returns "joined" or
    the slack error code, e.g. missing_scope (needs channels:join),
    method_not_supported_for_channel_type (private channel / dm: needs an
    invite) or is_archived."""
    try:
        resp = state.app.client.conversations_join(channel=channel_id)
        return "joined" if resp.get("ok") else str(resp.get("error"))
    except SlackApiError as exc:
        return str(exc.response.get("error"))
    except Exception as exc:  # noqa: BLE001
        logger.exception("conversations_join failed for %s", channel_id)
        return str(exc)


def with_join(channel_id: str, allow: bool, call):
    """run call(); if it fails because noodle isn't in the channel and `allow`
    is set, join it (silently) and run it once more. `allow` should only be true
    when matthias himself asked - anyone could otherwise make noodle join
    arbitrary channels."""
    try:
        return call()
    except SlackApiError as exc:
        if not allow or exc.response.get("error") not in _NOT_IN:
            raise
        result = join(channel_id)
        logger.info("auto-join %s before retrying: %s", channel_id, result)
        if result != "joined":
            raise
        return call()


def configured_channel_ids() -> list[str]:
    """channels noodle's own config says it works in: the daily-recap
    channel, the watched and logs channels, and any explicit allowed
    channels (not the "*" wildcard)."""
    ids = [
        config.MATTHIAS_DAY_CHANNEL_ID,
        config.WATCHED_CHANNEL_ID,
        config.LOGS_CHANNEL_ID,
        *config.ALLOWED_CHANNELS,
    ]
    seen: list[str] = []
    for cid in ids:
        if cid and cid != "*" and cid.startswith("C") and cid not in seen:
            seen.append(cid)
    return seen


def ensure_joined() -> None:
    """join every configured channel noodle isn't in yet. sends no messages.
    conversations.join is a no-op for a channel it's already in. failures
    (private channel, missing scope) are just logged."""
    for cid in configured_channel_ids():
        result = join(cid)
        if result == "joined":
            logger.info("channel %s: noodle is in (or just joined)", cid)
        else:
            logger.warning("couldn't join channel %s: %s", cid, result)
