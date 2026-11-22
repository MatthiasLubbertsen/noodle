import datetime
import unittest
from unittest import mock

import config
from bot import matthias_day, stats
from scripts import fix_late_recaps

# 2026-11-22 00:23:39 amsterdam (sunday): saturday's recap, posted late
LATE_TS = "1795303419.239739"
# 2026-11-20 22:14:48 amsterdam (friday): a normal evening recap
EVENING_TS = "1795209288.489399"


class RecapDate(unittest.TestCase):
    def test_after_midnight_is_the_day_before(self):
        self.assertEqual(matthias_day.recap_date(LATE_TS), datetime.date(2026, 11, 21))
        self.assertTrue(matthias_day.is_late(LATE_TS))

    def test_evening_is_the_same_day(self):
        self.assertEqual(matthias_day.recap_date(EVENING_TS), datetime.date(2026, 11, 20))
        self.assertFalse(matthias_day.is_late(EVENING_TS))

    def test_cutoff_is_configurable(self):
        with mock.patch.object(config, "RECAP_DAY_CUTOFF", "00:00"):
            self.assertEqual(matthias_day.recap_date(LATE_TS), datetime.date(2026, 11, 22))

    def test_journal_date_of_a_late_recap(self):
        self.assertTrue(matthias_day.recap_created_at(LATE_TS).startswith("2026-11-21T23:59:59"))
        self.assertTrue(matthias_day.recap_created_at(EVENING_TS).startswith("2026-11-20T22:14:48"))


class DailySummary(unittest.TestCase):
    def run_it(self, ts):
        with mock.patch.object(stats, "messages_sent_on", return_value=95) as count, \
             mock.patch.object(stats, "average_fact", return_value=None), \
             mock.patch.object(stats, "complete", return_value="ok") as complete:
            stats.build_daily_summary("U1", ts)
        return count.call_args.args[1], complete.call_args.args[0]

    def test_late_recap_counts_the_day_it_is_about(self):
        day, prompt = self.run_it(LATE_TS)
        self.assertEqual(day, "2026-11-21")
        self.assertIn("Saturday", prompt)
        self.assertNotIn("today is Sunday", prompt)

    def test_evening_recap_counts_today(self):
        day, prompt = self.run_it(EVENING_TS)
        self.assertEqual(day, "2026-11-20")
        self.assertIn("today is Friday", prompt)


class FixScript(unittest.TestCase):
    def test_finds_late_pings_and_the_count_reply(self):
        with mock.patch.object(config, "USER_ID", "UM"):
            msgs = [
                {"ts": EVENING_TS, "user": "UM", "text": "<!subteam^S0AK8LYJ8FR> today:\n• x"},
                {"ts": LATE_TS, "user": "UM", "text": "<!subteam^S0AK8LYJ8FR> today:\n• work"},
                {"ts": "1795303419.428909", "user": "UM", "text": ":thread: or don't thread"},
                {"ts": "1795303445.075869", "user": "UN", "text": "zero messages"},
            ]
            late = fix_late_recaps.late_pings(msgs)
        self.assertEqual([m["ts"] for m in late], [LATE_TS])
        self.assertEqual(fix_late_recaps.count_reply(msgs, LATE_TS, "UN")["ts"], "1795303445.075869")

    def test_wrong_50_days_post_is_found(self):
        client = mock.Mock()
        client.conversations_replies.return_value = {"messages": [
            {"user": "UN", "ts": "1.1", "text": ":50days: Day 11/50\n<https://hackclub.slack.com/archives/C0A9NJT2BCJ/p1795303419239739>"},
            {"user": "UN", "ts": "1.2", "text": ":50days: Day 9/50\n<https://hackclub.slack.com/archives/C0A9NJT2BCJ/p1795209288489399>"},
            {"user": "UX", "ts": "1.3", "text": ":50days: Day 11/50\n<https://hackclub.slack.com/archives/C0A9NJT2BCJ/p1795303419239739>"},
        ]}
        wrong = fix_late_recaps.wrong_day_posts(client, ("C09SWT5DCGY", "1.0"), "UN")
        self.assertEqual([(m["ts"], said, right) for m, said, right in wrong], [("1.1", 11, 10)])

    def test_fix_edits_the_post_in_place(self):
        client = mock.Mock()
        client.conversations_replies.return_value = {"messages": [
            {"user": "UN", "ts": "1.1", "text": ":50days: Day 11/50\n<https://hackclub.slack.com/archives/C0A9NJT2BCJ/p1795303419239739>"},
        ]}
        with mock.patch.object(config, "FIFTY_DAYS_THREAD", "C09SWT5DCGY:1.0"):
            self.assertEqual(fix_late_recaps.fix_fifty_days(client, "UN", dry=False), 1)
        client.chat_update.assert_called_once_with(
            channel="C09SWT5DCGY", ts="1.1",
            text=":50days: Day 10/50\n<https://hackclub.slack.com/archives/C0A9NJT2BCJ/p1795303419239739>",
        )
        self.assertFalse(client.chat_postMessage.called)


if __name__ == "__main__":
    unittest.main()
