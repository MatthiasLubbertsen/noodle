import datetime
import unittest
from unittest import mock

import config
from bot import almanac, weather
from bot.interests import is_avoided
from bot.tz import AMSTERDAM_TZ


class AvoidedTests(unittest.TestCase):
    def test_matches(self):
        for text in ("Halloween", "happy HALLOWEEN!", "Day of the Dead", "All Souls' Day", "Memorial Day"):
            self.assertTrue(is_avoided(text), text)

    def test_no_false_positives(self):
        for text in ("Roald Dahl Day", "Watermelon Day", "deadline day", "Pizza Day"):
            self.assertFalse(is_avoided(text), text)

    def test_almanac_drops_them(self):
        data = {"holidays": [{"text": "Halloween"}, {"text": "Cat Day"}, {"text": "Day of the Dead"}]}
        with mock.patch.object(almanac, "_get_json", return_value=data):
            self.assertEqual(almanac.fun_holidays_today(), ["Cat Day"])


class WeekendWeatherTests(unittest.TestCase):
    saturday = datetime.datetime(2026, 10, 31, 8, 0, tzinfo=AMSTERDAM_TZ)
    monday = datetime.datetime(2026, 11, 2, 8, 0, tzinfo=AMSTERDAM_TZ)

    def test_weekend_uses_only_the_primary_place(self):
        with mock.patch.multiple(
            config, WEATHER_LOCATIONS=["a", "b"], WEATHER_DEFAULT_LOCATION="a", WEEKEND_WEATHER_LOCATIONS=[]
        ):
            self.assertEqual(weather.morning_places(self.saturday), ["a"])
            self.assertEqual(weather.morning_places(self.monday), ["a", "b"])

    def test_weekend_override_and_fallback(self):
        with mock.patch.multiple(
            config, WEATHER_LOCATIONS=["a", "b"], WEATHER_DEFAULT_LOCATION="", WEEKEND_WEATHER_LOCATIONS=["b"]
        ):
            self.assertEqual(weather.morning_places(self.saturday), ["b"])
        with mock.patch.multiple(
            config, WEATHER_LOCATIONS=["a", "b"], WEATHER_DEFAULT_LOCATION="", WEEKEND_WEATHER_LOCATIONS=[]
        ):
            self.assertEqual(weather.morning_places(self.saturday), ["a"])


if __name__ == "__main__":
    unittest.main()
