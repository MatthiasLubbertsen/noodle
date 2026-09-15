from zoneinfo import ZoneInfo

# matthias is in the netherlands - every scheduled/dated thing noodle does
# (daily DMs, "today"'s date for stats/news) runs on this timezone, not
# configurable per the "everything is ams time" rule.
AMSTERDAM_TZ = ZoneInfo("Europe/Amsterdam")
