import unittest
from unittest import mock

from bot import matthias_day

TS = "1794774962.974129"
PING = "<!subteam^S0AK8LYJ8FR> today:"


def client(history=None):
    c = mock.Mock()
    c.chat_getPermalink.return_value = {"ok": True, "permalink": "https://x/p"}
    c.conversations_history.return_value = {"ok": True, "messages": history or []}
    return c


class PersistPingAlerts(unittest.TestCase):
    def run_ping(self, text, *, stored=True, blocks=None, history=None, notify=True):
        with mock.patch.object(matthias_day, "append_message"), \
             mock.patch.object(matthias_day, "has_permalink", return_value=stored), \
             mock.patch.object(matthias_day, "alert") as alert:
            matthias_day.persist_ping(client(history), "C1", TS, text, blocks, notify)
        return alert

    def test_saved_recap_is_silent(self):
        self.assertFalse(self.run_ping(PING + "\n• did a thing").called)

    def test_recap_that_could_not_be_saved_alerts(self):
        self.assertTrue(self.run_ping(PING + "\n• did a thing", stored=False).called)

    def test_short_reping_is_silent(self):
        self.assertFalse(self.run_ping("<!subteam^S0AK8LYJ8FR> ^^").called)

    def test_recap_without_bullets_alerts(self):
        self.assertTrue(self.run_ping(PING + " i did a lot of things today but forgot the bullets").called)

    def test_backfill_does_not_alert(self):
        self.assertFalse(self.run_ping(PING + "\n• x", stored=False, notify=False).called)

    def test_blocks_only_recap_is_saved(self):
        blocks = [{"type": "rich_text", "elements": [{"type": "rich_text_list", "style": "bullet",
                   "elements": [{"type": "rich_text_section", "elements": [{"type": "text", "text": "x"}]}]}]}]
        self.assertFalse(self.run_ping(PING, blocks=blocks).called)


if __name__ == "__main__":
    unittest.main()
