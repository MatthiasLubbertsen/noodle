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
    def setUp(self):
        p = mock.patch("bot.wakeup._too_early", return_value=False)
        p.start()
        self.addCleanup(p.stop)

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


class StateFileTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch("bot.wakeup._too_early", return_value=False)
        p.start()
        self.addCleanup(p.stop)

    def patched(self, path: Path):
        return mock.patch.object(config, "MORNING_STATE_PATH", path), mock.patch.object(config, "DATA_DIR", path.parent)

    def test_empty_file_is_not_sent_and_is_rewritten_whole(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sent.json"
            path.write_text("")
            a, b = self.patched(path)
            with a, b:
                self.assertFalse(wakeup._sent_today())
                wakeup._mark_sent()
                self.assertTrue(wakeup._sent_today())
                self.assertEqual(json.loads(path.read_text())["day"], wakeup._today())
                self.assertFalse(path.with_name("sent.json.tmp").exists())

    def test_check_presence_sends_when_active_and_skips_when_sent(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sent.json"
            a, b = self.patched(path)
            client = mock.Mock()
            client.users_getPresence.return_value = {"presence": "active"}
            send = mock.Mock()
            with a, b, mock.patch("bot.state.app", mock.Mock(client=client)), \
                    mock.patch("bot.scheduler._send_morning_dm", send):
                wakeup.check_presence()
                wakeup.check_presence()
            self.assertEqual(send.call_count, 1)
            self.assertEqual(client.users_getPresence.call_count, 1)

    def test_check_presence_away_sends_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sent.json"
            a, b = self.patched(path)
            client = mock.Mock()
            client.users_getPresence.return_value = {"presence": "away"}
            send = mock.Mock()
            with a, b, mock.patch("bot.state.app", mock.Mock(client=client)), \
                    mock.patch("bot.scheduler._send_morning_dm", send):
                wakeup.check_presence()
            send.assert_not_called()


class PresenceLoggingTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch("bot.wakeup._too_early", return_value=False)
        p.start()
        self.addCleanup(p.stop)

    def test_online_is_logged_without_the_user_id_and_poll_is_30s(self):
        self.assertEqual(wakeup.PRESENCE_POLL_SECONDS, 30)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sent.json"
            with mock.patch.object(config, "MORNING_STATE_PATH", path), \
                    mock.patch.object(config, "DATA_DIR", path.parent), \
                    mock.patch.object(config, "USER_ID", "U1SECRET"), \
                    mock.patch("bot.scheduler._send_morning_dm"), \
                    self.assertLogs("noodle", level="INFO") as logs:
                wakeup.on_online()
        text = "\n".join(logs.output)
        self.assertIn("matthias came online (presence)", text)
        self.assertNotIn("U1SECRET", text)


class TooEarlyTests(unittest.TestCase):
    def at(self, h, m=0):
        fake = datetime.datetime(2026, 11, 8, h, m, tzinfo=wakeup.AMSTERDAM_TZ)
        dt = mock.Mock(wraps=datetime.datetime)
        dt.now.return_value = fake
        return mock.patch("bot.wakeup.datetime.datetime", dt)

    def test_night_is_too_early_morning_is_not(self):
        with mock.patch.object(config, "MORNING_EARLIEST_DM", "06:00"):
            with self.at(3):
                self.assertTrue(wakeup._too_early())
            with self.at(5, 59):
                self.assertTrue(wakeup._too_early())
            with self.at(6):
                self.assertFalse(wakeup._too_early())

    def test_3am_online_blip_sends_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sent.json"
            send = mock.Mock()
            with mock.patch.object(config, "MORNING_STATE_PATH", path), \
                    mock.patch.object(config, "DATA_DIR", path.parent), \
                    mock.patch("bot.wakeup._too_early", return_value=True), \
                    mock.patch("bot.scheduler._send_morning_dm", send):
                wakeup.on_online()
                wakeup.check_presence()
            send.assert_not_called()
            self.assertFalse(path.exists())
