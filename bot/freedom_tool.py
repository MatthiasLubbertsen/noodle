"""the `freedom` tool: everything matthias's freedom api (and its mcp) can do -
sessions, schedules, devices, blocklists - exposed to the model as one tool.

safety rules, enforced HERE in code (never left to the model), because the
freedom account is SHARED with matthias's dad:
- matthias only (checked against the real slack sender id).
- every session/schedule noodle starts or creates targets ONLY devices on the
  FREEDOM_DEVICE_NAMES allowlist - never `all_devices`. a request for any other
  device is refused, not silently widened or dropped.
- noodle only lists, ends, edits or deletes sessions/schedules whose devices
  are ALL on that allowlist, and "end all" ends them one by one instead of
  calling freedom's account-wide end-all.
- session stats are not exposed: freedom only gives them account-wide.
"""
import logging
import re

import config
from bot import freedom
from bot.netlog import log_tool

logger = logging.getLogger("noodle")

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
ACTIONS = [
    "auth_status", "list_devices", "refresh_devices", "list_blocklists",
    "list_sessions", "session_history", "start_session", "end_session",
    "end_all_sessions", "list_schedules", "create_schedule", "update_schedule",
    "delete_schedule",
]
_HHMM = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


def error_message(error: dict) -> str:
    code = error.get("code")
    if code == "freedom_reauth_required":
        return "freedom's login expired on the server - matthias needs to run `freedomd login` again before this will work."
    if code == "premium_required":
        return "that needs a freedom premium feature that isn't available on this account."
    if code == "no_devices":
        return "freedom says none of matthias's devices are registered to block on."
    if code == "schedule_active":
        return "that schedule is running right now, so only its name, blocklists, devices and block flags can change."
    if code == "confirmation_required":
        return "freedom wants confirmation for that (a permanent 24h x 7 days block under Locked Mode) - ask matthias, then retry with confirm_permanent."
    return f"freedom rejected the request: {error.get('message') or code}"


def my_devices() -> dict[int, dict] | str:
    """{id: device} for matthias's OWN devices (FREEDOM_DEVICE_NAMES), or an
    error string. refuses outright if none match."""
    devices = freedom.list_devices()
    if devices is None:
        return "couldn't reach freedom to look up devices, check logs."
    wanted = {n.lower() for n in config.FREEDOM_DEVICE_NAMES}
    mine = {d["id"]: d for d in devices if (d.get("name") or "").strip().lower() in wanted}
    if not mine:
        return (
            "couldn't find matthias's own devices in freedom by name "
            f"({', '.join(config.FREEDOM_DEVICE_NAMES)}) - not doing anything, "
            "since the account is shared and guessing could block "
            "someone else's device."
        )
    return mine


def my_schedules(device_ids) -> tuple[dict[int, dict], str]:
    """matthias's sessions + schedules (running, upcoming, recurring): every
    device on them must be one of his. returns ({id: schedule}, timezone)."""
    mine = set(device_ids)
    found: dict = {}
    tz = ""
    for data, keys in (
        (freedom.list_schedules(), ("schedules",)),
        (freedom.list_sessions(), ("active", "upcoming")),
    ):
        if not data or data.get("error"):
            continue
        tz = tz or data.get("timezone") or ""
        for k in keys:
            for s in data.get(k) or []:
                ids = s.get("device_ids") or []
                if ids and set(ids) <= mine:
                    found[s["id"]] = s
    return found, tz


def _resolve_devices(requested, mine: dict[int, dict]) -> list[int] | str:
    """map device names/ids the user asked for onto the allowlist. nothing
    requested = all of matthias's devices. anything outside the allowlist is
    refused."""
    if not requested:
        return list(mine)
    ids = []
    for item in requested:
        key = str(item).strip().lower()
        match = next(
            (i for i, d in mine.items() if str(i) == key or (d.get("name") or "").strip().lower() == key),
            None,
        )
        if match is None:
            names = ", ".join(d.get("name", "?") for d in mine.values())
            return (
                f"{item!r} isn't one of matthias's devices (the account is shared, so i only "
                f"touch these: {names}). not doing anything."
            )
        if match not in ids:
            ids.append(match)
    return ids


def _blocklist_map() -> dict[int, dict] | str:
    data = freedom.list_blocklists()
    if data is None:
        return "couldn't reach freedom to look up blocklists, check logs."
    if data.get("error"):
        return error_message(data["error"])
    return {b["id"]: b for b in data.get("blocklists") or []}


def _resolve_blocklists(requested) -> list[int] | str:
    lists = _blocklist_map()
    if isinstance(lists, str):
        return lists
    ids = []
    for item in requested:
        key = str(item).strip().lower()
        match = next(
            (i for i, b in lists.items() if str(i) == key or (b.get("name") or "").strip().lower() == key),
            None,
        )
        if match is None:
            names = ", ".join(b.get("name", "?") for b in lists.values())
            return f"no blocklist called {item!r} in freedom (available: {names})."
        ids.append(match)
    return ids


def _fmt_schedule(s: dict, tz: str, devices: dict[int, dict], lists: dict) -> str:
    mins = s.get("duration_minutes") or (s.get("duration_seconds") or 0) // 60
    parts = [f"freedom:{s['id']} \"{s.get('name') or 'session'}\" {mins}min"]
    parts.append("repeats " + ",".join(s.get("days") or []) if s.get("recurring") else "one-off")
    parts.append(f"starts {s.get('start_time')} ({tz or 'freedom tz'})")
    if s.get("active"):
        left = s.get("seconds_left")
        parts.append("RUNNING" + (f", {int(left) // 60} min left" if left is not None else ""))
    elif s.get("seconds_until_start") is not None:
        parts.append(f"in ~{int(s['seconds_until_start']) // 60} min")
    what = []
    if s.get("block_everything"):
        what.append("everything")
    if s.get("block_apps"):
        what.append("apps")
    what += [lists[i]["name"] if isinstance(lists, dict) and i in lists else f"blocklist {i}" for i in s.get("blocklist_ids") or []]
    parts.append("blocks " + (", ".join(what) or "nothing?"))
    parts.append("on " + ", ".join(devices[i]["name"] if i in devices else str(i) for i in s.get("device_ids") or []))
    return " | ".join(parts)


def _list_lines(items, tz: str, devices, lists) -> str:
    return "\n".join(_fmt_schedule(s, tz, devices, lists) for s in items) or "none"


def _targets(args: dict, mine: dict[int, dict], partial: bool) -> dict | str:
    """devices + what-to-block part of a start/create/update payload.
    partial (update) only includes what was actually given."""
    payload: dict = {}
    if not partial or args.get("devices"):
        ids = _resolve_devices(args.get("devices"), mine)
        if isinstance(ids, str):
            return ids
        payload["device_ids"] = ids  # never all_devices
    blocklists = args.get("blocklists")
    if blocklists:
        bl = _resolve_blocklists(blocklists)
        if isinstance(bl, str):
            return bl
        payload["blocklist_ids"] = bl
    elif partial and blocklists is not None:
        payload["blocklist_ids"] = []  # explicitly emptied
    for flag in ("block_everything", "block_apps"):
        if args.get(flag) is not None:
            payload[flag] = bool(args[flag])
    if not partial and not (payload.get("blocklist_ids") or payload.get("block_everything") or payload.get("block_apps")):
        payload["block_everything"] = True  # plain "block me" = block everything
    return payload


def _check_times(args: dict) -> str | None:
    for key in ("start_time", "end_time"):
        if args.get(key) and not _HHMM.match(str(args[key])):
            return f"{key} must be HH:MM (24h, freedom account timezone)."
    if args.get("duration_minutes") is not None:
        try:
            if not 1 <= int(args["duration_minutes"]) <= 1440:
                raise ValueError
        except (TypeError, ValueError):
            return "duration_minutes must be between 1 and 1440."
    days = args.get("days")
    if days is not None and (not days or any(d not in DAYS for d in days)):
        return f"days must be a non-empty list from {', '.join(DAYS)}."
    return None


def _outcome(result, what: str, key: str | None = None) -> str:
    if result is None:
        return f"couldn't reach freedom to {what}, check logs."
    if result.get("error"):
        return error_message(result["error"])
    return ""


def _run(action: str, args: dict) -> str:
    if action == "auth_status":
        data = freedom.auth_status()
        if data is None:
            return "couldn't reach the freedom api, check logs."
        return "\n".join(f"{k}: {v}" for k, v in data.items())

    mine = my_devices()
    if isinstance(mine, str):
        return mine

    if action == "list_devices":
        return "\n".join(
            f"- {d['name']} (id {i}, {d.get('os')}, last seen {d.get('last_seen')})" for i, d in mine.items()
        )
    if action == "refresh_devices":
        fresh = freedom.get_devices_refreshed()
        if fresh is None:
            return "couldn't refresh freedom's devices, check logs."
        wanted = {n.lower() for n in config.FREEDOM_DEVICE_NAMES}
        return "refreshed. " + "\n".join(
            f"- {d['name']} (id {d['id']}, last seen {d.get('last_seen')})"
            for d in fresh if (d.get("name") or "").strip().lower() in wanted
        )
    if action == "list_blocklists":
        lists = _blocklist_map()
        if isinstance(lists, str):
            return lists
        return "\n".join(
            f"- {b['name']} (id {i}): {b.get('website_count', 0)} sites"
            + (", blocks all websites" if b.get("block_all_websites") else "")
            + (", blocks apps" if b.get("block_apps") else "")
            + (f", domains: {', '.join(b['custom_domains'][:15])}" if b.get("custom_domains") else "")
            + (f", filters: {', '.join(b['curated_filters'])}" if b.get("curated_filters") else "")
            for i, b in lists.items()
        ) or "no blocklists"

    found, tz = my_schedules(mine)
    lists = _blocklist_map()  # only for names in the output; fine if it fails
    lists = lists if isinstance(lists, dict) else {}

    if action == "list_sessions":
        sessions = list(found.values())
        active = [s for s in sessions if s.get("active")]
        upcoming = [s for s in sessions if not s.get("active") and not s.get("recurring")]
        return f"running:\n{_list_lines(active, tz, mine, lists)}\nupcoming one-offs:\n{_list_lines(upcoming, tz, mine, lists)}"
    if action == "list_schedules":
        return _list_lines([s for s in found.values() if s.get("recurring")], tz, mine, lists)
    if action == "session_history":
        data = freedom.session_history(int(args.get("page") or 1))
        if data is None or data.get("error"):
            return error_message(data["error"]) if data else "couldn't reach freedom, check logs."
        items = [h for h in data.get("sessions") or [] if h.get("device_ids") and set(h["device_ids"]) <= set(mine)]
        meta = data.get("meta") or {}
        lines = [
            f"- {h.get('name') or h.get('type')}: {h.get('start_at')} -> {h.get('end_at')} "
            f"({(h.get('duration_seconds') or 0) // 60}min) | {h.get('blocklists_summary')} | {h.get('devices_summary')}"
            for h in items
        ]
        return "\n".join(lines) + f"\n(page {meta.get('current_page')}/{meta.get('last_page')}; only matthias's devices shown)" if lines else "no matching history on this page"

    bad = _check_times(args)
    if bad:
        return bad

    if action == "start_session":
        payload = _targets(args, mine, partial=False)
        if isinstance(payload, str):
            return payload
        if args.get("start_time"):
            payload["start_time"] = args["start_time"]
            if args.get("name"):
                payload["name"] = args["name"]
            if args.get("end_time"):
                payload["end_time"] = args["end_time"]
        if args.get("duration_minutes") is not None:
            payload["duration_minutes"] = int(args["duration_minutes"])
        if not payload.get("duration_minutes") and not payload.get("end_time"):
            return "need duration_minutes (or start_time + end_time) to start a session."
        result = freedom.start_session_raw(payload)
        err = _outcome(result, "start the session")
        if err:
            return err
        s = result.get("session") or {}
        return "started: " + (_fmt_schedule(s, tz, mine, lists) if s.get("id") else "freedom accepted it")

    if action == "create_schedule":
        if not args.get("name") or not args.get("start_time") or not args.get("days"):
            return "need name, start_time and days to create a recurring schedule."
        payload = _targets(args, mine, partial=False)
        if isinstance(payload, str):
            return payload
        for key in ("name", "start_time", "end_time", "days", "confirm_permanent"):
            if args.get(key) is not None:
                payload[key] = args[key]
        if args.get("duration_minutes") is not None:
            payload["duration_minutes"] = int(args["duration_minutes"])
        if not payload.get("duration_minutes") and not payload.get("end_time"):
            return "need duration_minutes or end_time for the schedule."
        result = freedom.create_schedule_raw(payload)
        err = _outcome(result, "create the schedule")
        if err:
            return err
        s = result.get("schedule") or {}
        return "created: " + (_fmt_schedule(s, tz, mine, lists) if s.get("id") else "freedom accepted it")

    # everything below acts on an existing session/schedule by id
    if action == "end_all_sessions":
        running = [s for s in found.values() if s.get("active")]
        if not running:
            return "none of matthias's sessions are running."
        out = []
        for s in running:
            res = freedom.end_session(s["id"], bool(args.get("use_end_session_credit")))
            out.append(f"freedom:{s['id']}: " + (_outcome(res, "end it") or "ended"))
        return "\n".join(out)

    try:
        sid = int(str(args.get("id", "")).lower().replace("freedom:", "").lstrip("#"))
    except ValueError:
        return "need the id of the session/schedule (like freedom:123) - list them first if unsure."
    target = found.get(sid)
    if target is None:
        return f"no session/schedule freedom:{sid} among matthias's own (the account is shared, so i only touch his)."

    if action == "end_session":
        if not target.get("active"):
            return f"freedom:{sid} isn't running right now."
        res = freedom.end_session(sid, bool(args.get("use_end_session_credit")))
        return _outcome(res, "end the session") or f"ended freedom:{sid}."
    if action == "delete_schedule":
        if target.get("active"):
            return f"freedom:{sid} is running right now - end it first, then delete it."
        res = freedom.delete_schedule(sid)
        return _outcome(res, "delete it") or f"deleted freedom:{sid}."
    if action == "update_schedule":
        changes = _targets(args, mine, partial=True)
        if isinstance(changes, str):
            return changes
        for key in ("name", "start_time", "end_time", "days", "confirm_permanent"):
            if args.get(key) is not None:
                changes[key] = args[key]
        if args.get("duration_minutes") is not None:
            changes["duration_minutes"] = int(args["duration_minutes"])
        if not changes:
            return "nothing to change - say what to update."
        res = freedom.update_schedule(sid, changes)
        err = _outcome(res, "update it")
        if err:
            return err
        s = res.get("schedule") or {}
        return "updated: " + (_fmt_schedule(s, tz, mine, lists) if s.get("id") else f"freedom:{sid}")
    return f"unknown freedom action: {action}"


def run(args: dict, user_id: str | None) -> str:
    action = args.get("action", "")
    if user_id != config.USER_ID:
        log_tool("freedom", action, "unauthorized")
        return "only matthias can use freedom - not doing this for anyone else."
    if not config.FREEDOM_API_KEY:
        log_tool("freedom", action, "not configured")
        return "freedom isn't configured on noodle's end (no FREEDOM_API_KEY set)."
    if action not in ACTIONS:
        return f"unknown freedom action: {action}"
    try:
        result = _run(action, args)
    except Exception as exc:  # noqa: BLE001
        logger.exception("freedom %s failed", action)
        log_tool("freedom", action, "failed")
        return f"freedom {action} failed: {exc}"
    log_tool("freedom", action, "done")
    return result
