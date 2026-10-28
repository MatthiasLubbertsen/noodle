"""what's worth going outside for tonight.

two kinds of dm (sent by bot/scheduler.py, both through the proactive guardrails):
- at sunset, when tonight has something: the moon and a planet (or two planets)
  close together, a planet at opposition, mercury/venus at their best in the evening,
  a lunar eclipse, a big meteor shower's peak, or noaa forecasting a strong aurora.
- live, when the aurora is actually up (noaa's kp index) and it's dark.
both are skipped on overcast nights (open-meteo cloud cover). the sky itself is
computed locally with astronomy-engine: no api, no key (nasa has no "what's visible
tonight" api). the place is matthias's own, so never name it in a message.
"""
import datetime
import logging

import astronomy as ast

from bot import weather
from bot.httpjson import get_json
from bot.oneshot import complete, remember_facts
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")

KP_NOW_URL = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
KP_FORECAST_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json"
# kp 6 (a g2 storm) is about where the aurora starts showing on phone cameras at
# dutch latitudes (~52°n); kp 7+ is often naked-eye
AURORA_KP = 6
# mean cloud cover (%) at or above which nothing up there is worth a dm
OVERCAST = 75
MIN_ALT = 10  # degrees above the horizon for something to count as visible
CONJUNCTION_DEG = 3
# "tonight" = from 45 minutes to 4 hours after sunset, checked every 15 minutes
EVENING_START, EVENING_END, STEP = 45, 240, 15

B = ast.Body
PLANETS = [B.Mercury, B.Venus, B.Mars, B.Jupiter, B.Saturn]
# (month, day, name, note): peak nights drift by a day or so between years
METEOR_SHOWERS = [
    (1, 3, "quadrantids", "up to ~100 meteors an hour at the peak, best after midnight"),
    (4, 22, "lyrids", "~20 an hour, best before dawn"),
    (8, 12, "perseids", "the big summer one, ~100 an hour, best after midnight"),
    (10, 21, "orionids", "~20 an hour, best after midnight"),
    (12, 14, "geminids", "the best of the year, ~120 an hour, good all night"),
]
AURORA_FACT_PREFIX = "noaa forecasts a geomagnetic storm"
_COMPASS = ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"]
_J2000 = datetime.datetime(2000, 1, 1, 12, tzinfo=datetime.timezone.utc)


def _t(dt: datetime.datetime) -> ast.Time:
    return ast.Time((dt - _J2000).total_seconds() / 86400)


def _local(t: ast.Time) -> datetime.datetime:
    return t.Utc().replace(tzinfo=datetime.timezone.utc).astimezone(AMSTERDAM_TZ)


def _observer() -> ast.Observer:
    return ast.Observer(*weather.home_coords())


def _sky(body, t: ast.Time, obs: ast.Observer):
    """(equatorial vector, altitude, azimuth) of a body as seen from obs."""
    eq = ast.Equator(body, t, obs, True, True)
    hor = ast.Horizon(t, obs, eq.ra, eq.dec, ast.Refraction.Normal)
    return eq.vec, hor.altitude, hor.azimuth


def _where(alt: float, az: float) -> str:
    return f"in the {_COMPASS[round(az / 45) % 8]}, about {round(alt)}° up"


def _hhmm(dt: datetime.datetime) -> str:
    return dt.strftime("%H:%M")


def _name(body) -> str:
    return "the moon" if body == B.Moon else body.name.lower()


def next_sunset(after: datetime.datetime | None = None) -> datetime.datetime:
    after = after or datetime.datetime.now(AMSTERDAM_TZ)
    return _local(ast.SearchRiseSet(B.Sun, _observer(), ast.Direction.Set, _t(after), 2))


def _next_sunrise(after: datetime.datetime) -> datetime.datetime:
    return _local(ast.SearchRiseSet(B.Sun, _observer(), ast.Direction.Rise, _t(after), 2))


def _closest_pairs(obs: ast.Observer, sunset: datetime.datetime) -> dict:
    """{(a, b): (separation, when, alt, az)} at the evening moment two bodies are
    closest together while both are up."""
    bodies = [B.Moon, *PLANETS]
    samples = []
    for minutes in range(EVENING_START, EVENING_END + 1, STEP):
        when = sunset + datetime.timedelta(minutes=minutes)
        t = _t(when)
        samples.append((when, {b: _sky(b, t, obs) for b in bodies}))
    pairs = {}
    for i, a in enumerate(bodies):
        for b in bodies[i + 1:]:
            for when, pos in samples:
                (va, alt_a, az_a), (vb, alt_b, _) = pos[a], pos[b]
                if min(alt_a, alt_b) < MIN_ALT:
                    continue
                sep = ast.AngleBetween(va, vb)
                if (a, b) not in pairs or sep < pairs[(a, b)][0]:
                    pairs[(a, b)] = (sep, when, alt_a, az_a)
    return pairs


def _conjunctions(obs: ast.Observer, sunset: datetime.datetime) -> list[str]:
    tonight = {k: v for k, v in _closest_pairs(obs, sunset).items() if v[0] < CONJUNCTION_DEG}
    if not tonight:
        return []
    # two planets stay close for days: only tell him on the closest evening
    day = datetime.timedelta(days=1)
    neighbours = [_closest_pairs(obs, sunset - day), _closest_pairs(obs, sunset + day)]
    facts = []
    for (a, b), (sep, when, alt, az) in tonight.items():
        if any(sep > n[(a, b)][0] for n in neighbours if (a, b) in n):
            continue
        facts.append(f"{_name(a)} and {_name(b)} are only {sep:.1f}° apart, {_where(alt, az)} at {_hhmm(when)}")
    return facts


def _planet_events(obs: ast.Observer, sunset: datetime.datetime) -> list[str]:
    facts = []
    midnight = sunset + datetime.timedelta(hours=6)  # roughly the middle of the night
    for body in (B.Mars, B.Jupiter, B.Saturn):
        opposition = _local(ast.SearchRelativeLongitude(body, 0, _t(midnight - datetime.timedelta(hours=12))))
        if abs(opposition - midnight) <= datetime.timedelta(hours=12):
            when = sunset + datetime.timedelta(hours=2)
            _, alt, az = _sky(body, _t(when), obs)
            facts.append(
                f"{_name(body)} is at opposition: the closest and brightest it gets this year, up all "
                f"night, {_where(alt, az)} at {_hhmm(when)}"
            )
    for body in (B.Mercury, B.Venus):
        event = ast.SearchMaxElongation(body, _t(sunset - datetime.timedelta(days=1)))
        if not event or event.visibility != ast.Visibility.Evening:
            continue
        if abs(_local(event.time) - sunset) > datetime.timedelta(hours=12):
            continue
        when = sunset + datetime.timedelta(minutes=30)
        _, alt, az = _sky(body, _t(when), obs)
        if alt >= 5:
            facts.append(
                f"{_name(body)} is at its greatest distance from the sun in the evening sky "
                f"({event.elongation:.0f}°), the best time to see it: low {_where(alt, az)} at {_hhmm(when)}"
            )
    return facts


def _lunar_eclipse(obs: ast.Observer, sunset: datetime.datetime) -> list[str]:
    eclipse = ast.SearchLunarEclipse(_t(sunset - datetime.timedelta(hours=12)))
    if eclipse.kind not in (ast.EclipseKind.Partial, ast.EclipseKind.Total):
        return []  # a penumbral one is barely noticeable
    peak = _local(eclipse.peak)
    sunrise = _next_sunrise(sunset)
    if not sunset <= peak <= sunrise:
        return []
    # the moon can be low at the peak but fine earlier on, so check the whole partial phase
    half = datetime.timedelta(minutes=eclipse.sd_partial)
    visible = []
    when = max(peak - half, sunset)
    while when <= min(peak + half, sunrise):
        _, alt, az = _sky(B.Moon, _t(when), obs)
        if alt >= 5:
            visible.append((when, alt, az))
        when += datetime.timedelta(minutes=10)
    if not visible:
        return []
    when, alt, az = max(visible, key=lambda v: v[1])
    return [
        f"{eclipse.kind.name.lower()} lunar eclipse tonight: the partial phase runs "
        f"{_hhmm(peak - half)}-{_hhmm(peak + half)} with the peak at {_hhmm(peak)}. the moon is "
        f"highest during it at {_hhmm(when)}, {_where(alt, az)}, and is visible from "
        f"{_hhmm(visible[0][0])} to {_hhmm(visible[-1][0])}"
    ]


def _meteor_shower(sunset: datetime.datetime) -> list[str]:
    for month, day, name, note in METEOR_SHOWERS:
        if (sunset.month, sunset.day) == (month, day):
            moon = ast.Illumination(B.Moon, _t(sunset + datetime.timedelta(hours=6)))
            lit = round(moon.phase_fraction * 100)
            moon_note = "a bright moon will wash out the fainter ones" if lit > 50 else "the moon won't get in the way much"
            return [f"the {name} meteor shower peaks tonight: {note}. the moon is {lit}% lit, {moon_note}"]
    return []


def _kp_forecast(start: datetime.datetime, end: datetime.datetime) -> tuple[float, datetime.datetime] | None:
    """highest noaa kp (observed or forecast) in the 3-hour blocks overlapping start..end."""
    best = None
    for row in get_json(KP_FORECAST_URL):
        block = datetime.datetime.fromisoformat(row["time_tag"]).replace(tzinfo=datetime.timezone.utc)
        if block + datetime.timedelta(hours=3) <= start or block >= end or row.get("kp") is None:
            continue
        kp = float(row["kp"])
        if best is None or kp > best[0]:
            best = (kp, block.astimezone(AMSTERDAM_TZ))
    return best


def _aurora_forecast(sunset: datetime.datetime) -> list[str]:
    start, end = sunset + datetime.timedelta(hours=1), _next_sunrise(sunset) - datetime.timedelta(hours=1)
    try:
        best = _kp_forecast(start, end)
    except Exception:  # noqa: BLE001
        logger.exception("noaa kp forecast failed")
        return []
    if not best or best[0] < AURORA_KP:
        return []
    kp, when = best
    return [
        f"{AURORA_FACT_PREFIX} tonight (kp {kp:.1f}, strongest from around {_hhmm(when)}): "
        "the aurora might show up low over the northern horizon, best seen away from city lights, "
        "and phone cameras pick it up better than eyes"
    ]


def night_of(now: datetime.datetime | None = None) -> datetime.date:
    """a night belongs to the date it started on, so 01:00 is still "last night"."""
    now = now or datetime.datetime.now(AMSTERDAM_TZ)
    return (now - datetime.timedelta(hours=12)).date()


def has_aurora(facts: list[str]) -> bool:
    return any(f.startswith(AURORA_FACT_PREFIX) for f in facts)


def tonight_facts(sunset: datetime.datetime) -> list[str]:
    """everything worth looking up at tonight, as plain-text facts (empty = nothing)."""
    obs = _observer()
    return [
        *_lunar_eclipse(obs, sunset),
        *_planet_events(obs, sunset),
        *_conjunctions(obs, sunset),
        *_meteor_shower(sunset),
        *_aurora_forecast(sunset),
    ]


def overcast(start: datetime.datetime, end: datetime.datetime) -> bool:
    """will it be too cloudy to see anything? unknown counts as no, so a forecast
    hiccup never swallows something rare."""
    try:
        cover = weather.cloud_cover(*weather.home_coords(), start, end)
    except Exception:  # noqa: BLE001
        logger.exception("cloud cover lookup failed")
        return False
    return cover is not None and cover >= OVERCAST


def compose_tonight(facts: list[str]) -> str:
    text = "\n".join(f"- {f}" for f in facts)
    remember_facts("tonight's sky", text)
    message = complete(
        "the sun just set and there's something worth seeing in the sky tonight. tell matthias in "
        "your own voice, short and excited but not over the top, ONE paragraph: what it is, when "
        "and where to look (direction and height in plain words). don't name or hint at his town. "
        "use ONLY these facts.\n\nfacts:\n" + text,
        max_tokens=250,
    )
    return message or "tonight's sky:\n" + text


def aurora_now() -> str | None:
    """facts for a "go look now" dm when the aurora is likely up right now: it's dark,
    noaa's live kp is at least AURORA_KP and it isn't overcast. None otherwise."""
    now = datetime.datetime.now(AMSTERDAM_TZ)
    obs = _observer()
    _, sun_alt, _ = _sky(B.Sun, _t(now), obs)
    if sun_alt > -12:  # not dark enough yet (nautical twilight)
        return None
    latest = get_json(KP_NOW_URL)[-1]
    kp = float(latest.get("estimated_kp") or latest.get("kp_index") or 0)
    if kp < AURORA_KP:
        return None
    if overcast(now, now + datetime.timedelta(hours=1)):
        logger.info("aurora: kp %.1f but overcast, not sending", kp)
        return None
    return (
        f"noaa's live kp index is {kp:.1f} right now (a geomagnetic storm), it's dark and not "
        "overcast: the aurora could be visible right now, low over the northern horizon. best away "
        "from city lights, and a phone camera (night mode) shows it much better than eyes."
    )


def compose_aurora(facts: str) -> str:
    remember_facts("aurora alert", facts)
    message = complete(
        "the northern lights might be visible right now. tell matthias to go look, in your own "
        "voice, one or two short excited sentences: where to look and the phone camera tip. "
        "don't name or hint at his town. use ONLY these facts.\n\nfacts:\n" + facts,
        max_tokens=150,
    )
    return message or "aurora alert: " + facts
