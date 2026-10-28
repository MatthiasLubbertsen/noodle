import datetime
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from bot import dice, reminders
from bot.tz import AMSTERDAM_TZ


class Dice(unittest.TestCase):
    def test_total_matches_the_rolls(self):
        with mock.patch("bot.dice.random.randint", side_effect=[3, 4]):
            self.assertEqual(dice.roll("2d6+3"), "rolled 2d6+3: [3, 4] +3 = 10")

    def test_default_one_die_and_negative_modifier(self):
        with mock.patch("bot.dice.random.randint", return_value=5):
            self.assertEqual(dice.roll(" D20 - 2".replace(" - ", "-")), "rolled 1d20-2: [5] -2 = 3")

    def test_rejects_garbage_and_extremes_without_raising(self):
        for bad in ("", None, "hello", "2d", "d1", "21d6", "0d6", "d1001"):
            self.assertNotIn("rolled", dice.roll(bad), bad)

    def test_rolls_stay_in_range(self):
        for _ in range(200):
            n = int(dice.roll("d6").split("= ")[1])
            self.assertTrue(1 <= n <= 6)


class Reminders(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for name, value in (("DATA_DIR", Path(tmp.name)), ("MEMORY_DB_PATH", Path(tmp.name) / "m.db")):
            p = mock.patch.object(config, name, value)
            p.start()
            self.addCleanup(p.stop)
        reminders.init_table()
        self.now = datetime.datetime.now(AMSTERDAM_TZ)

    def add(self, **kw):
        args = dict(text="stretch", channel="D1", thread_ts=None, repeat="none",
                    interval_minutes=None, first=self.now + datetime.timedelta(hours=1),
                    created_by=config.USER_ID)
        args.update(kw)
        return reminders.add_reminder(**args)

    def test_one_off_fires_once_then_deactivates(self):
        r = self.add()
        self.assertEqual(reminders.pop_due(self.now.timestamp()), [])
        due = reminders.pop_due(r["next_run"].timestamp() + 1)
        self.assertEqual([d["text"] for d in due], ["stretch"])
        self.assertEqual(reminders.pop_due(r["next_run"].timestamp() + 99999), [])
        self.assertEqual(reminders.list_active(), [])

    def test_daily_skips_occurrences_missed_while_offline(self):
        r = self.add(repeat="daily")
        late = r["next_run"] + datetime.timedelta(days=3, minutes=1)
        self.assertEqual(len(reminders.pop_due(late.timestamp())), 1)  # one firing, not four
        nxt = reminders.list_active()[0]["next_run_ts"]
        self.assertGreater(nxt, late.timestamp())
        self.assertLessEqual(nxt - late.timestamp(), 86400)

    def test_every_n_minutes_repeats(self):
        r = self.add(repeat="every_n_minutes", interval_minutes=10)
        t = r["next_run"].timestamp()
        self.assertEqual(len(reminders.pop_due(t + 1)), 1)
        self.assertEqual(reminders.pop_due(t + 60), [])
        self.assertEqual(len(reminders.pop_due(t + 601)), 1)

    def test_weekdays_never_lands_on_a_weekend(self):
        friday = datetime.datetime(2026, 10, 30, 9, 0, tzinfo=AMSTERDAM_TZ)
        nxt = reminders.next_after(friday, "weekdays", None, friday + datetime.timedelta(minutes=1))
        self.assertEqual((nxt.weekday(), nxt.hour), (0, 9))

    def test_monthly_clamps_to_the_end_of_short_months(self):
        jan31 = datetime.datetime(2027, 1, 31, 8, 0, tzinfo=AMSTERDAM_TZ)
        nxt = reminders.next_after(jan31, "monthly", None, jan31 + datetime.timedelta(days=1))
        self.assertEqual((nxt.month, nxt.day), (2, 28))

    def test_cap_on_active_reminders(self):
        with mock.patch.object(reminders, "MAX_ACTIVE_REMINDERS", 2):
            self.assertTrue(self.add() and self.add())
            self.assertIsNone(self.add())

    def test_delete(self):
        r = self.add()
        self.assertTrue(reminders.delete_reminder(r["id"]))
        self.assertFalse(reminders.delete_reminder(r["id"]))

    def test_parse_local(self):
        self.assertEqual(reminders.parse_local("2026-10-30 09:15").hour, 9)
        self.assertIsNone(reminders.parse_local("tomorrow-ish"))


if __name__ == "__main__":
    unittest.main()
