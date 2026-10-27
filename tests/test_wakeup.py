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
    def run_online(self, quiet: bool, state_path: Path) -> mock.Mock:
        send = mock.Mock()
        with mock.patch.object(config, "MORNING_STATE_PATH", state_path), \
                mock.patch.object(config, "DATA_DIR", state_path.parent), \
                mock.patch("bot.proactive.in_quiet_hours", return_value=quiet), \
                mock.patch("bot.scheduler._send_morning_dm", send):
            wakeup.on_online()
        return send

    def test_first_online_sends_once(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sent.json"
            self.assertEqual(self.run_online(False, path).call_count, 1)
            self.assertEqual(self.run_online(False, path).call_count, 0)

    def test_quiet_hours_do_not_count(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sent.json"
            self.assertEqual(self.run_online(True, path).call_count, 0)
            self.assertFalse(path.exists())
            self.assertEqual(self.run_online(False, path).call_count, 1)

    def test_yesterday_does_not_block(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sent.json"
            path.write_text(json.dumps({"day": "2000-01-01"}))
            self.assertEqual(self.run_online(False, path).call_count, 1)


if __name__ == "__main__":
    unittest.main()
