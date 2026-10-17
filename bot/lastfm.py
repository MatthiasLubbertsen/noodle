import datetime
import logging

import config
from bot import features
from bot.httpjson import get_json
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")

API_URL = "https://ws.audioscrobbler.com/2.0/"


def configured() -> bool:
    return features.enabled("lastfm")


def _recent(limit: int, from_ts: int | None = None) -> dict:
    params = {
        "method": "user.getrecenttracks",
        "user": config.LASTFM_USER,
        "api_key": config.LASTFM_API_KEY,
        "format": "json",
        "limit": limit,
    }
    if from_ts:
        params["from"] = from_ts
    return get_json(API_URL, params)["recenttracks"]


def _track_line(t: dict) -> str:
    artist = (t.get("artist") or {}).get("#text", "?")
    album = (t.get("album") or {}).get("#text", "")
    line = f"{t.get('name', '?')} by {artist}"
    return f"{line} ({album})" if album else line


def now_playing() -> str:
    """what matthias is listening to right now (or last played), via last.fm
    scrobbles - spotify can scrobble to last.fm, which needs no oauth app."""
    if not configured():
        return "last.fm isn't configured (LASTFM_API_KEY / LASTFM_USER missing)"
    try:
        tracks = _recent(limit=5)["track"]
    except Exception:  # noqa: BLE001
        logger.exception("last.fm lookup failed")
        return "last.fm lookup failed, check logs"
    if isinstance(tracks, dict):  # last.fm returns a bare object for one track
        tracks = [tracks]
    if not tracks:
        return "no scrobbles found on last.fm"
    first = tracks[0]
    if (first.get("@attr") or {}).get("nowplaying") == "true":
        head = f"playing right now: {_track_line(first)}"
        rest = tracks[1:]
    else:
        head = "nothing playing right now"
        rest = tracks
    recent = [f"- {_track_line(t)}" for t in rest[:4] if not (t.get("@attr") or {}).get("nowplaying")]
    return head + ("\nrecently played:\n" + "\n".join(recent) if recent else "")


def scrobbles_today() -> int | None:
    """how many tracks played today (amsterdam), or None if unavailable."""
    if not configured():
        return None
    midnight = datetime.datetime.now(AMSTERDAM_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        return int(_recent(limit=1, from_ts=int(midnight.timestamp()))["@attr"]["total"])
    except Exception:  # noqa: BLE001
        logger.exception("last.fm scrobble count failed")
        return None


PERIODS = ("7day", "1month", "3month", "6month", "12month", "overall")
_TOP_METHODS = {"artists": "user.gettopartists", "tracks": "user.gettoptracks", "albums": "user.gettopalbums"}


def top(kind: str, period: str = "7day", limit: int = 10) -> list[str] | None:
    """top artists/tracks/albums for a period as 'name (N plays)' lines, or
    None if last.fm isn't configured / the call failed."""
    if not configured() or kind not in _TOP_METHODS or period not in PERIODS:
        return None
    try:
        data = get_json(
            API_URL,
            {
                "method": _TOP_METHODS[kind],
                "user": config.LASTFM_USER,
                "api_key": config.LASTFM_API_KEY,
                "format": "json",
                "period": period,
                "limit": limit,
            },
        )
        items = data[f"top{kind}"][kind[:-1]]
    except Exception:  # noqa: BLE001
        logger.exception("last.fm top %s failed", kind)
        return None
    lines = []
    for i in items:
        name = i.get("name", "?")
        if kind != "artists":
            name += f" by {(i.get('artist') or {}).get('name', '?')}"
        lines.append(f"{name} ({i.get('playcount', '?')} plays)")
    return lines


def scrobbles_between(start_ts: int, end_ts: int) -> int | None:
    """tracks played in [start_ts, end_ts) (unix seconds), or None."""
    if not configured():
        return None
    try:
        data = get_json(
            API_URL,
            {
                "method": "user.getrecenttracks",
                "user": config.LASTFM_USER,
                "api_key": config.LASTFM_API_KEY,
                "format": "json",
                "limit": 1,
                "from": start_ts,
                "to": end_ts,
            },
        )
        return int(data["recenttracks"]["@attr"]["total"])
    except Exception:  # noqa: BLE001
        logger.exception("last.fm scrobble range failed")
        return None
