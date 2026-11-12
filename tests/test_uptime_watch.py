import datetime
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from bot import uptime_watch as u
from tests.fake_kuma import FakeKuma

T0 = datetime.datetime(2026, 11, 10, 12, 0, tzinfo=datetime.timezone.utc)
NAME = "blog"

def step(state, reason, minutes):
    return u.update(state, {NAME: reason}, T0 + datetime.timedelta(minutes=minutes))


MONITORS = {
    1: {"id": 1, "name": "blog", "type": "http", "url": "https://blog.example.com", "hostname": None, "port": None, "active": True},
    2: {"id": 2, "name": "db", "type": "port", "url": "https://", "hostname": "db.example.com", "port": 5432, "active": True},
    3: {"id": 3, "name": "old", "type": "http", "url": "https://old.example.com", "active": False},
    4: {"id": 4, "name": "ping", "type": "ping", "url": "https://", "hostname": "1.1.1.1", "active": True},
}
BEATS = {
    1: [{"status": 1, "msg": "200 - OK"}, {"status": 0, "msg": "Request failed with  status code 502"}],
    2: [{"status": 1, "msg": ""}],
    3: [{"status": 0, "msg": "paused one"}],
    # monitor 4 has no heartbeat yet
}


class SnapshotTests(unittest.TestCase):
    def test_newest_beat_and_target_per_type(self):
        got = u.snapshot(MONITORS, {i: b[-1] for i, b in BEATS.items()})
        self.assertEqual(set(got), {"blog", "db"})  # paused and beat-less monitors are skipped
        self.assertEqual(got["blog"], {"status": 0, "type": "http", "target": "https://blog.example.com",
                                       "msg": "Request failed with status code 502"})
        self.assertEqual(got["db"]["target"], "db.example.com:5432")

    def test_duplicate_names_do_not_overwrite(self):
        mons = {1: {"name": "x", "type": "http", "url": "https://a"}, 2: {"name": "x", "type": "http", "url": "https://b"}}
        got = u.snapshot(mons, {1: {"status": 1}, 2: {"status": 0}})
        self.assertEqual(set(got), {"x", "x #2"})

    def test_message_is_flattened_and_capped(self):
        got = u.snapshot({1: {"name": "x"}}, {1: {"status": 0, "msg": "a\n\nb " + "c" * 500}})
        self.assertNotIn("\n", got["x"]["msg"])
        self.assertLessEqual(len(got["x"]["msg"]), 160)


class FetchTests(unittest.TestCase):
    """the real client against a socket.io server that talks like kuma."""

    def setUp(self):
        p = mock.patch.multiple(config, KUMA_USERNAME="", KUMA_PASSWORD="")
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(u, "TIMEOUT", 2)
        p.start()
        self.addCleanup(p.stop)

    def _kuma(self, **kw):
        k = FakeKuma(MONITORS, BEATS, **kw)
        self.addCleanup(k.close)
        return k

    def _fetch(self, k):
        with mock.patch.object(config, "KUMA_URL", k.url):
            return u.fetch(transports=["polling"])

    def test_auth_disabled_needs_no_login(self):
        got = self._fetch(self._kuma())
        self.assertEqual(got["blog"]["status"], 0)
        self.assertEqual(got["blog"]["msg"], "Request failed with status code 502")
        self.assertEqual(got["db"]["status"], 1)

    def test_login_when_auth_is_on(self):
        k = self._kuma(login=("me", "pw"))
        with mock.patch.multiple(config, KUMA_USERNAME="me", KUMA_PASSWORD="pw"):
            self.assertIn("blog", self._fetch(k))

    def test_wrong_password_raises(self):
        k = self._kuma(login=("me", "pw"))
        with mock.patch.multiple(config, KUMA_USERNAME="me", KUMA_PASSWORD="nope"):
            with self.assertRaisesRegex(RuntimeError, "login failed"):
                self._fetch(k)

    def test_auth_on_without_credentials_says_so(self):
        k = self._kuma(login=("me", "pw"))
        with self.assertRaisesRegex(RuntimeError, "KUMA_USERNAME"):
            self._fetch(k)

    def test_unreachable_kuma_raises(self):
        k = FakeKuma({}, {})
        url = k.url
        k.close()
        with mock.patch.object(config, "KUMA_URL", url), self.assertRaises(Exception):
            u.fetch(transports=["polling"])


class StateTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(config, "UPTIME_DOWN_MINUTES", 5)
        p.start()
        self.addCleanup(p.stop)

    def _down_for_5(self):
        s = {}
        for minute in range(6):
            s = step(s, "down", minute)
        return s

    def test_needs_five_minutes_down_in_a_row(self):
        s = {}
        for minute in range(5):
            s = step(s, "down", minute)
            self.assertEqual(u.pending(s), ([], []), minute)
        s = step(s, "down", 5)
        self.assertEqual(u.pending(s), ([NAME], []))
        # "since" is when it actually went down, not when the dm became due
        self.assertEqual(s[NAME]["down_since"], T0.isoformat())

    def test_a_short_outage_is_never_announced_either_way(self):
        s = step(step(step({}, "down", 0), "down", 1), "", 2)
        self.assertEqual(u.pending(s), ([], []))
        # and the clock starts over: 4 more minutes down is still not 5 in a row
        for minute in range(3, 8):
            s = step(s, "down", minute)
        self.assertEqual(u.pending(s), ([], []))
        self.assertEqual(u.pending(step(s, "down", 8)), ([NAME], []))

    def test_recovery_only_announced_if_the_outage_was(self):
        s = step(self._down_for_5(), "", 10)  # up again before anyone was told: silent
        self.assertEqual(u.pending(s), ([], []))
        s = self._down_for_5()
        s[NAME]["alerted"] = True
        s = step(s, "", 30)
        self.assertEqual(u.pending(s), ([], [NAME]))

    def test_flap_inside_an_outage_stays_one_outage(self):
        s = self._down_for_5()
        s[NAME]["alerted"] = True
        s = step(s, "", 7)
        s = step(s, "down", 8)
        self.assertEqual(u.pending(s), ([], []))
        self.assertEqual(s[NAME]["down_since"], T0.isoformat())

    def test_pending_or_maintenance_changes_nothing(self):
        s = u.update(self._down_for_5(), {NAME: None}, T0 + datetime.timedelta(minutes=7))
        self.assertEqual(u.pending(s), ([NAME], []))

    def test_state_saved_by_an_older_version_still_works(self):
        old = {NAME: {"fails": 3, "down_since": T0.isoformat(), "reason": "502", "alerted": True, "recovered_at": None}}
        self.assertEqual(u.pending(step(old, "", 9)), ([], [NAME]))
        old = {NAME: {"fails": 0, "down_since": None, "reason": "", "alerted": False, "recovered_at": None}}
        self.assertEqual(u.pending(step(old, "down", 0)), ([], []))


def _mon(status, msg="", type="http", target="https://blog.example.com"):
    return {"status": status, "type": type, "target": target, "msg": msg}


class PollTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = mock.patch.multiple(config, DATA_DIR=Path(tmp.name), UPTIME_STATE_PATH=Path(tmp.name) / "u.json",
                                UPTIME_DOWN_MINUTES=0, KUMA_URL="http://k")
        p.start()
        self.addCleanup(p.stop)

    def test_poll_announces_once_with_the_real_error_then_recovery(self):
        with mock.patch("bot.uptime_watch.fetch", return_value={"blog": _mon(0, "Request failed with status code 502")}):
            d, up = u.poll()
            self.assertEqual((d, up), (["blog"], []))
            facts = u.format_facts(d, up)
            self.assertIn("DOWN blog (http, https://blog.example.com)", facts)
            self.assertIn("error: Request failed with status code 502", facts)
            u.mark_sent(d, up)
            self.assertEqual(u.poll(), ([], []))  # already announced
        with mock.patch("bot.uptime_watch.fetch", return_value={"blog": _mon(1)}):
            d, up = u.poll()
            self.assertEqual((d, up), ([], ["blog"]))
            self.assertIn("was down", u.format_facts(d, up))

    def test_the_dm_always_says_what_is_still_down(self):
        with mock.patch("bot.uptime_watch.fetch", return_value={
                "blog": _mon(0, "502"), "db": _mon(1, type="port", target="db.example.com:5432")}):
            d, up = u.poll()
            u.mark_sent(d, up)
        with mock.patch("bot.uptime_watch.fetch", return_value={
                "blog": _mon(0, "502"), "db": _mon(0, "connect ECONNREFUSED", "port", "db.example.com:5432")}):
            d, up = u.poll()
            self.assertEqual(d, ["db"])
            facts = u.format_facts(d, up)
        self.assertIn("DOWN db (port, db.example.com:5432)", facts)
        self.assertIn("STILL DOWN blog", facts)

    def test_unreachable_kuma_raises_and_changes_nothing(self):
        with mock.patch("bot.uptime_watch.fetch", return_value={"blog": _mon(0, "502")}):
            u.poll()
        with mock.patch("bot.uptime_watch.fetch", side_effect=OSError("kuma down")):
            with self.assertRaises(OSError):
                u.poll()
        with mock.patch("bot.uptime_watch.fetch", return_value={"blog": _mon(0, "502")}):
            self.assertEqual(u.poll(), (["blog"], []))

    def test_message_names_monitors_or_falls_back_to_the_facts(self):
        with mock.patch("bot.uptime_watch.fetch", return_value={"blog": _mon(0, "502")}):
            d, up = u.poll()
        with mock.patch("bot.uptime_watch.complete", return_value="something broke"), \
             mock.patch("bot.uptime_watch.remember_facts"):
            msg = u.compose_message(d, up)
        self.assertIn("blog (http, https://blog.example.com) went down", msg)  # the model dropped the monitor name -> plain facts

    def test_several_monitors_are_one_slack_message(self):
        from bot.chunk import chunk_response
        both = {"blog": _mon(0, "502"), "db": _mon(0, "refused", "port", "db.example.com:5432")}
        with mock.patch("bot.uptime_watch.fetch", return_value=both):
            d, up = u.poll()
        with mock.patch("bot.uptime_watch.remember_facts"):
            with mock.patch("bot.uptime_watch.complete", return_value=None):  # model call failed
                self.assertEqual(len(chunk_response(u.compose_message(d, up))), 1)
            with mock.patch("bot.uptime_watch.complete", return_value="blog is down.\n\ndb is down too."):
                self.assertEqual(len(chunk_response(u.compose_message(d, up))), 1)


if __name__ == "__main__":
    unittest.main()
