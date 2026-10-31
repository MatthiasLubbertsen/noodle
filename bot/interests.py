import re

# things matthias doesn't want to hear about (he doesn't do halloween: it's about
# death). any silly/named day or holiday matching one of these is dropped before the
# model ever sees it, and a greeting that still mentions one anyway is thrown away.
# whole words/phrases, case-insensitive. add to this list to avoid more.
AVOIDED_TERMS = (
    # halloween and its cousins
    "halloween", "hallowe'en", "all hallows", "samhain", "trick or treat",
    "day of the dead", "dia de los muertos", "día de los muertos", "all souls",
    # remembering the dead
    "memorial day", "funeral", "cemetery",
    "graveyard", "grave", "death", "dead", "dying", "murder", "suicide",
    # spooky/horror themes
    "ghost", "zombie", "vampire", "skeleton", "witch", "witches", "haunted",
    "horror", "grim reaper", "friday the 13th", "walpurgis", "obon",
)

# important days that are always allowed, even though they contain an avoided word
# (e.g. "remembrance of the dead")
ALLOWED_TERMS = ("dodenherdenking", "remembrance of the dead", "national remembrance")

_ALLOWED = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(t) for t in ALLOWED_TERMS) + r")(?!\w)", re.IGNORECASE
)

_AVOIDED = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(t) for t in AVOIDED_TERMS) + r")(?!\w)",
    re.IGNORECASE,
)


def is_avoided(text: str) -> bool:
    """True if the text is about something matthias doesn't want to hear about."""
    text = text or ""
    return not _ALLOWED.search(text) and bool(_AVOIDED.search(text))
