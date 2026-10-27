import datetime
import unittest
from unittest import mock

from bot import sky, weather
from bot.tz import AMSTERDAM_TZ


def sunset_on(day: datetime.date) -> datetime.datetime:
    noon = datetime.datetime.combine(day, datetime.time(12), AMSTERDAM_TZ)
    return sky.next_sunset(noon)


class Sky(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(weather, "home_coords", return_value=(52.1, 5.3))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_saturn_opposition_2026_10_04(self):
        facts = sky._planet_events(sky._observer(), sunset_on(datetime.date(2026, 10, 4)))
        self.assertTrue(any("saturn is at opposition" in f for f in facts), facts)

    def test_no_opposition_on_an_ordinary_night(self):
        facts = sky._planet_events(sky._observer(), sunset_on(datetime.date(2026, 11, 20)))
        self.assertFalse(any("opposition" in f for f in facts), facts)

    def test_partial_lunar_eclipse_2026_08_28(self):
        facts = sky._lunar_eclipse(sky._observer(), sunset_on(datetime.date(2026, 8, 27)))
        self.assertIsInstance(facts, list)
        self.assertEqual(sky._lunar_eclipse(sky._observer(), sunset_on(datetime.date(2026, 6, 1))), [])

    def test_meteor_shower_only_on_peak_night(self):
        self.assertTrue(sky._meteor_shower(sunset_on(datetime.date(2026, 12, 14))))
        self.assertFalse(sky._meteor_shower(sunset_on(datetime.date(2026, 12, 20))))

    def test_night_belongs_to_the_date_it_started_on(self):
        at = lambda d, h: datetime.datetime(2026, 10, d, h, tzinfo=AMSTERDAM_TZ)  # noqa: E731
        self.assertEqual(sky.night_of(at(5, 22)), datetime.date(2026, 10, 5))
        self.assertEqual(sky.night_of(at(6, 1)), datetime.date(2026, 10, 5))
        self.assertEqual(sky.night_of(at(6, 20)), datetime.date(2026, 10, 6))

    def test_aurora_facts_are_recognised(self):
        with mock.patch.object(sky, "_kp_forecast", return_value=(7.0, datetime.datetime(2026, 10, 5, 23, tzinfo=AMSTERDAM_TZ))):
            facts = sky._aurora_forecast(sunset_on(datetime.date(2026, 10, 5)))
        self.assertTrue(sky.has_aurora(facts))
        self.assertFalse(sky.has_aurora(["saturn is at opposition"]))


if __name__ == "__main__":
    unittest.main()
