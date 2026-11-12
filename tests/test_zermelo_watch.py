import datetime
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from bot import zermelo, zermelo_watch
from bot.tz import AMSTERDAM_TZ


def at(day, hour, minute=0):
    return datetime.datetime(2026, 11, day, hour, minute, tzinfo=AMSTERDAM_TZ)


def lesson(instance, start, subject, slot, cancelled=False, valid=True, why="", kind="lesson"):
    """shaped like a row of zermelo's /appointments (the fields noodle asks for)."""
    return {
        "appointmentInstance": instance, "start": int(start.timestamp()),
        "end": int((start + datetime.timedelta(minutes=50)).timestamp()), "startTimeSlot": slot,
        "subjects": [subject], "type": kind, "cancelled": cancelled, "valid": valid, "hidden": False,
        "changeDescription": why, "remark": "",
    }


# monday the 16th, as zermelo really returned it: hour 1 chemistry cancelled, hour 3 german
# cancelled and geography moved in, hour 7 physics in another room
MONDAY = [
    lesson(1, at(16, 8, 15), "sk", 1, cancelled=True, why="Les vervalt"),
    lesson(2, at(16, 9, 55), "du", 3, cancelled=True, why="Les vervalt"),
    lesson(3, at(16, 9, 55), "ak", 3, why="De les is verplaatst (komt van ma 8:15u)."),
    lesson(4, at(16, 14, 5), "na", 7, why="Het lokaal is gewijzigd (was 201)."),
]


class SubjectNames(unittest.TestCase):
    def test_codes_become_real_names(self):
        self.assertEqual(zermelo.subject_name("ak"), "geography")
        self.assertEqual(zermelo.subject_name("Act"), "activity")
        self.assertEqual(zermelo.subject_name("ttswi"), "maths (tts)")

    def test_unknown_code_is_kept(self):
        self.assertEqual(zermelo.subject_name("LU"), "LU")

    def test_window_is_today_and_tomorrow(self):
        start, end = zermelo.window(at(15, 17, 30))
        self.assertEqual((start, end), (at(15, 0), at(17, 0)))


class Dropouts(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        for name, value in (("ZERMELO_SEEN_PATH", Path(d.name) / "z.json"), ("DATA_DIR", Path(d.name))):
            p = mock.patch.object(config, name, value)
            p.start()
            self.addCleanup(p.stop)

    def poll(self, rows, now):
        with mock.patch.object(zermelo, "appointments", return_value=rows):
            return zermelo_watch.new_dropouts(now)[0]

    def test_only_cancelled_lessons_and_only_once(self):
        now = at(15, 17, 30)  # sunday: monday is "tomorrow"
        items = self.poll(MONDAY, now)
        self.assertEqual([r["subjects"][0] for r in items], ["sk", "du"])
        self.assertEqual(self.poll(MONDAY, now), items)  # held (quiet hours, cap): still pending
        zermelo_watch.mark_reported(items, now)
        self.assertEqual(self.poll(MONDAY, now), [])

    def test_a_later_cancellation_is_reported_on_its_own(self):
        now = at(15, 17, 30)
        zermelo_watch.mark_reported(self.poll(MONDAY, now), now)
        later = MONDAY[:3] + [lesson(4, at(16, 14, 5), "na", 7, cancelled=True, why="Les vervalt")]
        self.assertEqual([r["subjects"][0] for r in self.poll(later, at(16, 7, 45))], ["na"])

    def test_a_lesson_that_is_already_over_is_not_news(self):
        self.assertEqual([r["subjects"][0] for r in self.poll(MONDAY, at(16, 9, 30))], ["du"])

    def test_old_version_rows_are_ignored(self):
        rows = [lesson(9, at(16, 8, 15), "gd", 1, cancelled=True, valid=False)]
        self.assertEqual(self.poll(rows, at(15, 17, 30)), [])

    def test_past_days_are_pruned_from_the_state(self):
        zermelo_watch.mark_reported(self.poll(MONDAY, at(15, 17, 30)), at(15, 17, 30))
        zermelo_watch.mark_reported([], at(17, 8, 0))
        self.assertEqual(zermelo_watch._load(), {})

    def test_unreadable_state_starts_fresh(self):
        config.ZERMELO_SEEN_PATH.write_text("", encoding="utf-8")
        with self.assertLogs("noodle", "ERROR"):
            self.assertEqual(len(self.poll(MONDAY, at(15, 17, 30))), 2)


class Message(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(zermelo_watch, "remember_facts")
        p.start()
        self.addCleanup(p.stop)
        self.now = at(15, 17, 30)
        self.items = zermelo_watch.dropouts(MONDAY, self.now, {})

    def test_facts_say_what_dropped_and_what_is_left(self):
        facts = zermelo_watch.format_facts(self.items, MONDAY, self.now)
        self.assertEqual(facts.splitlines(), [
            "tomorrow (monday 16 november), dropped out:",
            "- hour 1, 08:15-09:05: chemistry [zermelo: Les vervalt]",
            "- hour 3, 09:55-10:45: german [zermelo: Les vervalt]",
            "still on that day: hour 3, 09:55-10:45: geography; hour 7, 14:05-14:55: physics",
        ])

    def test_whole_day_gone(self):
        rows = [lesson(1, at(16, 8, 15), "wi", 1, cancelled=True, kind="exam")]
        facts = zermelo_watch.format_facts(rows, rows, self.now)
        self.assertIn("maths (exam)", facts)
        self.assertIn("the whole day is free", facts)

    def test_model_text_is_one_slack_message(self):
        with mock.patch.object(zermelo_watch, "complete", return_value="chemistry is off tomorrow\n\nand german too"):
            text = zermelo_watch.compose_message(self.items, MONDAY, self.now)
        self.assertEqual(text, "chemistry is off tomorrow\nand german too")

    def test_plain_fallback_when_the_model_fails_or_drops_a_subject(self):
        for reply in (None, "chemistry is off tomorrow"):
            with mock.patch.object(zermelo_watch, "complete", return_value=reply):
                text = zermelo_watch.compose_message(self.items, MONDAY, self.now)
            self.assertEqual(text.splitlines(), [
                "chemistry dropped out tomorrow (hour 1, 08:15-09:05)",
                "german dropped out tomorrow (hour 3, 09:55-10:45)",
            ])


if __name__ == "__main__":
    unittest.main()
