import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from bot import rtm, wakeup


class OnlineEventTests(unittest.TestCase):
    def setUp(self):
        self.patch = mock.patch.object(config, "USER_ID", "U1")
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_single_and_batched_shapes(self):
        self.assertTrue(rtm._is_online_event({"type": "presence_change", "user": "U1", "presence": "active"}))
        self.assertTrue(rtm._is_online_event({"type": "presence_change", "users": ["U2", "U1"], "presence": "active"}))

    def test_ignores_others_and_away(self):
        self.assertFalse(rtm._is_online_event({"type": "presence_change", "user": "U2", "presence": "active"}))
        self.assertFalse(rtm._is_online_event({"type": "presence_change", "user": "U1", "presence": "away"}))
        self.assertFalse(rtm._is_online_event({"type": "message", "user": "U1", "presence": "active"}))


class OnceADayTests(unittest.TestCase):
    def run_it(self, fn_name: str, state_path: Path) -> mock.Mock:
        send = mock.Mock()
        with mock.patch.object(config, "MORNING_STATE_PATH", state_path), \
                mock.patch.object(config, "DATA_DIR", state_path.parent), \
                mock.patch("bot.scheduler._send_morning_dm", send):
            getattr(wakeup, fn_name)()
        return send

    def test_first_online_sends_once(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sent.json"
            self.assertEqual(self.run_it("on_online", path).call_count, 1)
            self.assertEqual(self.run_it("on_online", path).call_count, 0)

    def test_quiet_hours_are_not_used(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sent.json"
            with mock.patch("bot.proactive.in_quiet_hours", return_value=True):
                self.assertEqual(self.run_it("on_online", path).call_count, 1)

    def test_fallback_sends_only_if_not_sent(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sent.json"
            self.assertEqual(self.run_it("send_fallback", path).call_count, 1)
            self.assertEqual(self.run_it("on_online", path).call_count, 0)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sent.json"
            self.assertEqual(self.run_it("on_online", path).call_count, 1)
            self.assertEqual(self.run_it("send_fallback", path).call_count, 0)

    def test_yesterday_does_not_block(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sent.json"
            path.write_text(json.dumps({"day": "2000-01-01"}))
            self.assertEqual(self.run_it("on_online", path).call_count, 1)


class DayPartTests(unittest.TestCase):
    def test_greeting_fits_the_hour(self):
        from bot import morning

        def at(h):
            return datetime.datetime(2026, 10, 27, h, 0)

        self.assertEqual(morning._greeting_word(at(8)), "good morning")
        self.assertEqual(morning._greeting_word(at(12)), "good afternoon")
        self.assertEqual(morning._greeting_word(at(19)), "good evening")
        self.assertEqual(morning._greeting_word(at(3)), "hey")


if __name__ == "__main__":
    unittest.main()
