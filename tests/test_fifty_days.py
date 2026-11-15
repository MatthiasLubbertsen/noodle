import datetime
import unittest
from unittest import mock

import config
from bot import fifty_days

LINK = "https://hackclub.slack.com/archives/C09SWT5DCGY/p1794600000000100"
# 2026-11-13 14:50 amsterdam
TS = "1794577854.332879"


class DayNumberTests(unittest.TestCase):
    def test_bounds(self):
        self.assertEqual(fifty_days.day_number(datetime.date(2026, 11, 12)), 1)
        self.assertEqual(fifty_days.day_number(datetime.date(2026, 12, 31)), 50)
        self.assertIsNone(fifty_days.day_number(datetime.date(2026, 11, 11)))
        self.assertIsNone(fifty_days.day_number(datetime.date(2027, 1, 1)))


class CrosspostTests(unittest.TestCase):
    def run_it(self, text: str, thread: str = LINK) -> mock.Mock:
        client = mock.Mock()
        client.chat_getPermalink.return_value = {"permalink": "https://x/recap"}
        with mock.patch.object(config, "FIFTY_DAYS_THREAD", thread):
            fifty_days.crosspost(client, "C0A9NJT2BCJ", TS, text)
        return client

    def test_links_recap_into_goal_thread(self):
        client = self.run_it("<!subteam^S0AK8LYJ8FR> today:\n• shipped a thing")
        client.chat_postMessage.assert_called_once_with(
            channel="C09SWT5DCGY",
            thread_ts="1794600000.000100",
            reply_broadcast=True,
            text=":50days: Day 2/50\n<https://x/recap>",
            unfurl_links=True,
        )

    def test_reping_without_bullets_posts_nothing(self):
        self.assertFalse(self.run_it("<!subteam^S0AK8LYJ8FR> ^^").chat_postMessage.called)

    def test_off_when_unset(self):
        client = self.run_it("<!subteam^S0AK8LYJ8FR> today:\n• x", thread="")
        self.assertFalse(client.chat_postMessage.called)

    def test_slack_failure_is_swallowed(self):
        client = mock.Mock()
        client.chat_getPermalink.side_effect = RuntimeError("boom")
        with mock.patch.object(config, "FIFTY_DAYS_THREAD", LINK):
            fifty_days.crosspost(client, "C0A9NJT2BCJ", TS, "<!subteam^S0AK8LYJ8FR>\n• x")


if __name__ == "__main__":
    unittest.main()
