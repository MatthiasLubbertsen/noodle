from bot.state import MEMORY


def conv_key(event: dict) -> str:
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
