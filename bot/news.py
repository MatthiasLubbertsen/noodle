import json
import logging
import urllib.request
import xml.etree.ElementTree as ET

import config

logger = logging.getLogger("noodle")

FEED_URL = "https://news.hackclub.com/feed.xml"
HEADERS = {"User-Agent": "noodle-slack-bot/1.0 (matthiaslubbertsen@gmail.com)"}


def _fetch_items() -> list[dict]:
    req = urllib.request.Request(FEED_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=10) as resp:
        root = ET.fromstring(resp.read())
    items = []
    for item in root.findall("./channel/item"):
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or link).strip()
        title = (item.findtext("title") or "").strip()
        if guid:
            items.append({"guid": guid, "title": title, "link": link})
    return items


def _load_seen():
    """returns the saved set of seen article guids, or None if there is no
    saved state yet (i.e. this is the very first run)."""
    if not config.NEWS_SEEN_PATH.exists():
        return None
    try:
        return set(json.loads(config.NEWS_SEEN_PATH.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        logger.exception("failed to load hackclub news read-state, starting fresh")
        return None


def _save_seen(seen: set) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.NEWS_SEEN_PATH.write_text(
        json.dumps(sorted(seen), ensure_ascii=False), encoding="utf-8"
    )


def unread_articles() -> list[dict]:
    """return articles from the hack club news rss feed not seen before.

    on the very first run ever (no saved state file yet) every article
    currently in the feed is marked as already seen and an empty list is
    returned, since matthias has already read everything available so far -
    that's the intended starting point, not a backlog to report.
    """
    try:
        items = _fetch_items()
    except Exception:  # noqa: BLE001
        logger.exception("failed to fetch hack club news feed")
        return []

    seen = _load_seen()
    if seen is None:
        _save_seen({it["guid"] for it in items})
        logger.info("seeded hack club news read-state with %d existing articles", len(items))
        return []

    unread = [it for it in items if it["guid"] not in seen]
    if unread:
        _save_seen(seen | {it["guid"] for it in unread})
    return unread
