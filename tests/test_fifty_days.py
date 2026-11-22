import datetime
import unittest
from unittest import mock

import config
from bot import fifty_days

LINK = "https://hackclub.slack.com/archives/C09SWT5DCGY/p1794600000000100"
# 2026-11-13 14:50 amsterdam
TS = "1794577854.332879"
# 2026-11-22 00:23 amsterdam: saturday's recap, posted late
LATE_TS = "1795303419.239739"


class DayNumberTests(unittest.TestCase):
    def test_bounds(self):
        self.assertEqual(fifty_days.day_number(datetime.date(2026, 11, 12)), 1)
        self.assertEqual(fifty_days.day_number(datetime.date(2026, 12, 31)), 50)
        self.assertIsNone(fifty_days.day_number(datetime.date(2026, 11, 11)))
        self.assertIsNone(fifty_days.day_number(datetime.date(2027, 1, 1)))


class CrosspostTests(unittest.TestCase):
    def run_it(self, text: str, thread: str = LINK, ts: str = TS, replies=None) -> mock.Mock:
        client = mock.Mock()
        client.chat_getPermalink.return_value = {"permalink": "https://x/recap"}
        client.conversations_history.return_value = {"ok": True, "messages": []}
        client.conversations_replies.return_value = {"messages": replies or []}
        with mock.patch.object(config, "FIFTY_DAYS_THREAD", thread):
            fifty_days.crosspost(client, "C0A9NJT2BCJ", ts, text)
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

    def test_recap_after_midnight_is_the_day_before(self):
        # real incident: saturday nov 21's recap at 00:23 on sunday was posted as day 11
        client = self.run_it("<!subteam^S0AK8LYJ8FR> today:\n• work", ts=LATE_TS)
        self.assertTrue(client.chat_postMessage.call_args.kwargs["text"].startswith(":50days: Day 10/50\n"))

    def test_day_already_in_thread_is_not_posted_again(self):
        client = self.run_it("<!subteam^S0AK8LYJ8FR> today:\n• x", replies=[{"text": ":50days: Day 2/50\n<https://x>"}])
        self.assertFalse(client.chat_postMessage.called)

    def test_reping_without_bullets_posts_nothing(self):
        self.assertFalse(self.run_it("<!subteam^S0AK8LYJ8FR> ^^").chat_postMessage.called)

    def test_event_text_without_bullets_falls_back_to_stored_message(self):
        # real incident: the event text cleaned to "" although the message had bullets
        client = mock.Mock()
        client.chat_getPermalink.return_value = {"permalink": "https://x/recap"}
        client.conversations_replies.return_value = {"messages": []}
        client.conversations_history.return_value = {
            "ok": True,
            "messages": [{"ts": TS, "text": "<!subteam^S0AK8LYJ8FR> today:\n• real recap"}],
        }
        with mock.patch.object(config, "FIFTY_DAYS_THREAD", LINK):
            fifty_days.crosspost(client, "C0A9NJT2BCJ", TS, "<!subteam^S0AK8LYJ8FR> today:")
        self.assertTrue(client.chat_postMessage.called)

    def test_bullets_only_in_blocks_still_post(self):
        blocks = [{"type": "rich_text", "elements": [{"type": "rich_text_list", "style": "bullet",
                   "elements": [{"type": "rich_text_section", "elements": [{"type": "text", "text": "x"}]}]}]}]
        client = mock.Mock()
        client.chat_getPermalink.return_value = {"permalink": "https://x/recap"}
        client.conversations_replies.return_value = {"messages": []}
        with mock.patch.object(config, "FIFTY_DAYS_THREAD", LINK):
            fifty_days.crosspost(client, "C0A9NJT2BCJ", TS, "<!subteam^S0AK8LYJ8FR> today:", blocks)
        self.assertTrue(client.chat_postMessage.called)

    def test_off_when_unset(self):
        client = self.run_it("<!subteam^S0AK8LYJ8FR> today:\n• x", thread="")
        self.assertFalse(client.chat_postMessage.called)

    def test_slack_failure_is_swallowed(self):
        client = mock.Mock()
        client.chat_getPermalink.side_effect = RuntimeError("boom")
        client.conversations_history.return_value = {"ok": True, "messages": []}
        with mock.patch.object(config, "FIFTY_DAYS_THREAD", LINK):
            fifty_days.crosspost(client, "C0A9NJT2BCJ", TS, "<!subteam^S0AK8LYJ8FR>\n• x")


class BackfillHelpers(unittest.TestCase):
    def test_days_already_posted_reads_the_first_line_only(self):
        client = mock.Mock()
        client.conversations_replies.return_value = {"messages": [
            {"text": ":50days: Day 2/50\n<https://x>"},
            {"text": "nice! :50days: Day 3/50"},   # not a day post
            {"text": ":50days: Day 4/50"},
            {"text": "hello"},
        ]}
        self.assertEqual(fifty_days.days_already_posted(client, ("C1", "1.1")), {2, 4})

    def test_backfill_post_is_not_broadcast(self):
        client = mock.Mock()
        fifty_days.post_day(client, ("C1", "1.1"), 2, "https://x", broadcast=False)
        self.assertFalse(client.chat_postMessage.call_args.kwargs["reply_broadcast"])
        fifty_days.post_day(client, ("C1", "1.1"), 2, "https://x")
        self.assertTrue(client.chat_postMessage.call_args.kwargs["reply_broadcast"])

    def test_day_text_first_line_is_exact(self):
        self.assertEqual(fifty_days.day_text(5, "https://x"), ":50days: Day 5/50\n<https://x>")


if __name__ == "__main__":
    unittest.main()
