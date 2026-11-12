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


# monday the 16th, as zermelo really returned it: hour 1 scheikunde cancelled, hour 3 duits
# cancelled and geography moved in, hour 7 physics in another room
MONDAY = [
    lesson(1, at(16, 8, 15), "sk", 1, cancelled=True, why="Les vervalt"),
    lesson(2, at(16, 9, 55), "du", 3, cancelled=True, why="Les vervalt"),
    lesson(3, at(16, 9, 55), "ak", 3, why="De les is verplaatst (komt van ma 8:15u)."),
    lesson(4, at(16, 14, 5), "na", 7, why="Het lokaal is gewijzigd (was 201)."),
]


class SubjectNames(unittest.TestCase):
    def test_codes_become_real_names(self):
        self.assertEqual(zermelo.subject_name("ak"), "aardrijkskunde")
        self.assertEqual(zermelo.subject_name("LU"), "leesuur")
        self.assertEqual(zermelo.subject_name("ttswi"), "toets wiskunde")

    def test_unknown_code_is_kept(self):
        self.assertEqual(zermelo.subject_name("xy"), "xy")

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
            "- hour 1, 08:15-09:05: scheikunde [zermelo: Les vervalt]",
            "- hour 3, 09:55-10:45: duits [zermelo: Les vervalt]",
            "still on that day: hour 3, 09:55-10:45: aardrijkskunde; hour 7, 14:05-14:55: natuurkunde",
        ])

    def test_whole_day_gone(self):
        rows = [lesson(1, at(16, 8, 15), "wi", 1, cancelled=True, kind="exam")]
        facts = zermelo_watch.format_facts(rows, rows, self.now)
        self.assertIn("wiskunde (exam)", facts)
        self.assertIn("the whole day is free", facts)

    def test_model_text_is_one_slack_message(self):
        with mock.patch.object(zermelo_watch, "complete", return_value="scheikunde is off tomorrow\n\nand duits too"):
            text = zermelo_watch.compose_message(self.items, MONDAY, self.now)
        self.assertEqual(text, "scheikunde is off tomorrow\nand duits too")

    def test_plain_fallback_when_the_model_fails_or_drops_a_subject(self):
        for reply in (None, "scheikunde is off tomorrow"):
            with mock.patch.object(zermelo_watch, "complete", return_value=reply):
                text = zermelo_watch.compose_message(self.items, MONDAY, self.now)
            self.assertEqual(text.splitlines(), [
                "scheikunde dropped out tomorrow (hour 1, 08:15-09:05)",
                "duits dropped out tomorrow (hour 3, 09:55-10:45)",
            ])


class TimetableTool(unittest.TestCase):
    def ask(self, args, now, rows=MONDAY):
        with mock.patch.object(zermelo, "appointments", return_value=rows):
            return zermelo.query(args, now)

    def test_day_in_full(self):
        rows = MONDAY[:3] + [dict(MONDAY[3], locations=["222"], teachers=["abc"], modified=True)]
        self.assertEqual(self.ask({"action": "day", "date": "tomorrow"}, at(15, 17, 30), rows).splitlines(), [
            "tomorrow, monday 16 november: starts 09:55, ends 14:55, 2 on, free in between: hour 4, 5, 6, 2 dropped out",
            "- hour 1 08:15-09:05: scheikunde [DROPPED OUT: Les vervalt]",
            "- hour 3 09:55-10:45: aardrijkskunde",
            "- hour 3 09:55-10:45: duits [DROPPED OUT: Les vervalt]",
            "- hour 7 14:05-14:55: natuurkunde, room 222, teacher abc [CHANGED: Het lokaal is gewijzigd (was 201).]",
        ])

    def test_free_day(self):
        self.assertEqual(self.ask({"action": "day"}, at(14, 12), []), "today, saturday 14 november: nothing on: a free day")

    def test_next_says_now_next_and_when_school_is_out(self):
        self.assertEqual(self.ask({"action": "next"}, at(16, 10, 20)).splitlines(), [
            "it is monday 10:20",
            "now: hour 3 09:55-10:45: aardrijkskunde (ends in 25 min)",
            "next (today, monday 16 november): hour 7 14:05-14:55: natuurkunde (starts in 3h 45min)",
            "school is out today at 14:55 (in 4h 35min), 2 still to go, the current one included",
        ])

    def test_changes_lists_only_what_changed(self):
        text = self.ask({"action": "changes"}, at(15, 17, 30))
        self.assertIn("scheikunde [DROPPED OUT", text)
        self.assertNotIn("natuurkunde", text)  # no changed flag on it in this fixture

    def test_exams_include_tts_activities(self):
        rows = MONDAY + [lesson(7, at(18, 11, 5), "ttswi", 4, kind="activity"),
                         lesson(8, at(19, 9, 5), "gs", 2, kind="exam", cancelled=True)]
        self.assertEqual(self.ask({"action": "exams"}, at(15, 17, 30), rows),
                         "- wednesday 18 november (in 2 days): hour 4 11:05-11:55: toets wiskunde (activity)")

    def test_stats_per_subject(self):
        lines = self.ask({"action": "stats", "start_date": "2026-11-16", "end_date": "2026-11-16"}, at(20, 12)).splitlines()
        self.assertEqual(lines[0], "2026-11-16 to 2026-11-16: 4 lessons planned, 2 dropped out (50%), 2 hours of lessons that went on")
        self.assertIn("- duits: 1 planned, 1 dropped out (100%), 0h on", lines)

    def test_bad_input_and_dead_api_are_plain_sentences(self):
        self.assertIn("YYYY-MM-DD", self.ask({"action": "day", "date": "friday"}, at(15, 12)))
        self.assertIn("unknown action", self.ask({"action": "homework"}, at(15, 12)))
        with mock.patch.object(zermelo, "appointments", side_effect=TimeoutError):
            self.assertEqual(zermelo.query({"action": "next"}, at(15, 12)), "zermelo could not be read right now (TimeoutError)")

    def test_only_matthias_and_only_with_credentials(self):
        from bot import features, tools
        with mock.patch.object(config, "USER_ID", "U1"), mock.patch.object(zermelo, "appointments", return_value=MONDAY):
            self.assertEqual(tools.run_tool("timetable", {"action": "day"}, user_id="U2"), "only matthias can see his timetable.")
            self.assertIn("monday", tools.run_tool("timetable", {"action": "day", "date": "2026-11-16"}, user_id="U1"))
        names = lambda: [t["function"]["name"] for t in tools.available_tools()]  # noqa: E731
        with mock.patch.object(config, "ZERMELO_TOKEN", ""):
            self.assertFalse(features.enabled("zermelo"))
            self.assertNotIn("timetable", names())
        with mock.patch.object(config, "ZERMELO_TOKEN", "t"), mock.patch.object(config, "ZERMELO_SCHOOL", "school"):
            self.assertIn("timetable", names())


if __name__ == "__main__":
    unittest.main()
