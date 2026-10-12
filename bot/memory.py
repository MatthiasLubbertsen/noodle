import config
from bot.slack_text import _is_dm
from bot.state import MEMORY


def dm_conv_key() -> str:
    # stable regardless of slack's (occasionally recycled) dm channel id -
    # there's only ever one authorized dm partner (USER_ID)
    return f"dm:{config.USER_ID}"


def conv_key(event: dict) -> str:
    if _is_dm(event):
        return dm_conv_key()
    thread_ts = event.get("thread_ts")
    if thread_ts:
        return f"thread:{thread_ts}"
    return f"chan:{event.get('channel')}"


def trim_memory(history: list, max_groups: int = 12) -> None:
    # keep history grouped by user turns so tool_call/tool pairs stay intact
    groups: list = []
    current = None
    for m in history:
        if m["role"] == "user":
            current = [m]
            groups.append(current)
        elif current is not None:
            current.append(m)
        else:
            groups.append([m])
    while len(groups) > max_groups:
        groups.pop(0)
    history[:] = [m for g in groups for m in g]
