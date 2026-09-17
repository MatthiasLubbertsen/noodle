import logging

from bot import state

logger = logging.getLogger("noodle")


def get_members(group_id: str) -> list[str]:
    try:
        resp = state.app.client.usergroups_users_list(usergroup=group_id)
        if not resp.get("ok"):
            logger.warning("usergroups_users_list failed: %s", resp.get("error"))
            return []
        return resp.get("users", [])
    except Exception:  # noqa: BLE001
        logger.exception("usergroups_users_list failed")
        return []


def _set_members(group_id: str, members: list[str]) -> bool:
    try:
        resp = state.app.client.usergroups_users_update(
            usergroup=group_id, users=",".join(members)
        )
        if not resp.get("ok"):
            logger.warning("usergroups_users_update failed: %s", resp.get("error"))
            return False
        return True
    except Exception:  # noqa: BLE001
        logger.exception("usergroups_users_update failed")
        return False


def ensure_member(group_id: str, user_id: str) -> str:
    """add user_id to the usergroup if they aren't already in it.

    returns "added", "already", or "failed".
    """
    members = get_members(group_id)
    if user_id in members:
        return "already"
    return "added" if _set_members(group_id, members + [user_id]) else "failed"


def ensure_not_member(group_id: str, user_id: str) -> str:
    """remove user_id from the usergroup if they're in it.

    returns "removed", "not_member", or "failed".
    """
    members = get_members(group_id)
    if user_id not in members:
        return "not_member"
    remaining = [m for m in members if m != user_id]
    if not remaining:
        # slack usergroups can't be emptied out via this api - leave it alone
        logger.warning("refusing to remove the last member of usergroup %s", group_id)
        return "failed"
    return "removed" if _set_members(group_id, remaining) else "failed"
