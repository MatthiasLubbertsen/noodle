import datetime
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import config
from bot import uptime_watch as u

T0 = datetime.datetime(2026, 11, 10, 12, 0, tzinfo=datetime.timezone.utc)
URL = "https://a.example.com"


def step(state, reason, minutes):
    return u.update(state, {URL: reason}, T0 + datetime.timedelta(minutes=minutes))


class StateTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(config, "UPTIME_FAILS", 2)
        p.start()
        self.addCleanup(p.stop)

    def test_needs_two_failures_in_a_row(self):
        s = step({}, "http 502", 0)
        self.assertEqual(u.pending(s), ([], []))
        s = step(s, "http 502", 2)
        self.assertEqual(u.pending(s), ([URL], []))

    def test_a_blip_never_alerts(self):
        s = step(step(step({}, "timed out", 0), "", 2), "timed out", 4)
        self.assertEqual(u.pending(s), ([], []))

    def test_recovery_only_announced_if_the_outage_was(self):
        s = step(step({}, "http 500", 0), "http 500", 2)
        s = step(s, "", 10)  # up again before anyone was told: silent
        self.assertEqual(u.pending(s), ([], []))
        s = step(step({}, "http 500", 0), "http 500", 2)
        s[URL]["alerted"] = True
        s = step(s, "", 30)
        self.assertEqual(u.pending(s), ([], [URL]))

    def test_flap_inside_an_outage_stays_one_outage(self):
        s = step(step({}, "http 500", 0), "http 500", 2)
        s[URL]["alerted"] = True
        s = step(s, "", 4)
        s = step(s, "http 500", 6)
        self.assertEqual(u.pending(s), ([], []))  # already alerted, and not recovered
        self.assertEqual(s[URL]["down_since"], (T0 + datetime.timedelta(minutes=2)).isoformat())


class ProbeTests(unittest.TestCase):
    def _probe(self, exc):
        with mock.patch("urllib.request.urlopen", side_effect=exc):
            return u.probe(URL)

    def test_4xx_means_something_answered(self):
        self.assertEqual(self._probe(urllib.error.HTTPError(URL, 404, "nf", {}, None)), "")

    def test_5xx_is_down(self):
        self.assertEqual(self._probe(urllib.error.HTTPError(URL, 502, "bad", {}, None)), "http 502")

    def test_timeout_and_dns(self):
        import socket
        self.assertEqual(self._probe(urllib.error.URLError(socket.timeout())), "timed out")
        self.assertEqual(self._probe(urllib.error.URLError(socket.gaierror())), "dns lookup failed")


class PollTests(unittest.TestCase):
    def test_poll_persists_and_mark_sent_clears(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.multiple(config, DATA_DIR=Path(tmp), UPTIME_STATE_PATH=Path(tmp) / "u.json",
                                 UPTIME_URLS=["a.example.com"], UPTIME_FAILS=1), \
             mock.patch("bot.uptime_watch.probe", return_value="http 503"):
            down, up = u.poll()
            self.assertEqual((down, up), ([URL], []))
            u.mark_sent(down, up)
            self.assertEqual(u.poll(), ([], []))  # already announced
            with mock.patch("bot.uptime_watch.probe", return_value=""):
                self.assertEqual(u.poll(), ([], [URL]))
                self.assertIn("was down", u.format_facts([], [URL]))


if __name__ == "__main__":
    unittest.main()
