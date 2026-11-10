import datetime
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from bot import uptime_watch as u

T0 = datetime.datetime(2026, 11, 10, 12, 0, tzinfo=datetime.timezone.utc)
NAME = "blog"

METRICS = '''# HELP monitor_status Monitor Status (1 = UP, 0= DOWN, 2= PENDING, 3= MAINTENANCE)
# TYPE monitor_status gauge
monitor_status{monitor_name="blog",monitor_type="http",monitor_url="https://blog.example.com",monitor_hostname="null",monitor_port="null"} 1
monitor_status{monitor_name="my \\"api\\"",monitor_type="http",monitor_url="https://api.example.com",monitor_hostname="null",monitor_port="null"} 0
monitor_status{monitor_name="db",monitor_type="port",monitor_url="https://",monitor_hostname="db.example.com",monitor_port="5432"} 2
monitor_response_time{monitor_name="blog",monitor_type="http",monitor_url="https://blog.example.com"} 123
'''


def step(state, reason, minutes):
    return u.update(state, {NAME: reason}, T0 + datetime.timedelta(minutes=minutes))


class ParseTests(unittest.TestCase):
    def test_reads_status_lines_only(self):
        got = u.parse_metrics(METRICS)
        self.assertEqual(got["blog"], (1, "https://blog.example.com"))
        self.assertEqual(got['my \\"api\\"'][0], 0)
        self.assertEqual(got["db"][0], 2)
        self.assertEqual(len(got), 3)

    def test_null_url_is_empty(self):
        got = u.parse_metrics('monitor_status{monitor_name="x",monitor_url="null"} 1\n')
        self.assertEqual(got["x"], (1, ""))


class StateTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(config, "UPTIME_FAILS", 2)
        p.start()
        self.addCleanup(p.stop)

    def test_needs_n_failures_in_a_row(self):
        s = step({}, "down", 0)
        self.assertEqual(u.pending(s), ([], []))
        s = step(s, "down", 2)
        self.assertEqual(u.pending(s), ([NAME], []))

    def test_a_blip_never_alerts(self):
        s = step(step(step({}, "down", 0), "", 2), "down", 4)
        self.assertEqual(u.pending(s), ([], []))

    def test_recovery_only_announced_if_the_outage_was(self):
        s = step(step({}, "down", 0), "down", 2)
        s = step(s, "", 10)  # up again before anyone was told: silent
        self.assertEqual(u.pending(s), ([], []))
        s = step(step({}, "down", 0), "down", 2)
        s[NAME]["alerted"] = True
        s = step(s, "", 30)
        self.assertEqual(u.pending(s), ([], [NAME]))

    def test_flap_inside_an_outage_stays_one_outage(self):
        s = step(step({}, "down", 0), "down", 2)
        s[NAME]["alerted"] = True
        s = step(s, "", 4)
        s = step(s, "down", 6)
        self.assertEqual(u.pending(s), ([], []))
        self.assertEqual(s[NAME]["down_since"], (T0 + datetime.timedelta(minutes=2)).isoformat())

    def test_pending_or_maintenance_changes_nothing(self):
        s = step(step({}, "down", 0), "down", 2)
        s = u.update(s, {NAME: None}, T0 + datetime.timedelta(minutes=4))
        self.assertEqual(u.pending(s), ([NAME], []))


class PollTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = mock.patch.multiple(config, DATA_DIR=Path(tmp.name), UPTIME_STATE_PATH=Path(tmp.name) / "u.json",
                                UPTIME_FAILS=1, KUMA_URL="https://k.example.com", KUMA_API_KEY="uk1_x")
        p.start()
        self.addCleanup(p.stop)

    def test_poll_announces_once_then_recovery(self):
        down = {"blog": (0, "https://blog.example.com")}
        with mock.patch("bot.uptime_watch.fetch", return_value=down):
            d, up = u.poll()
            self.assertEqual((d, up), (["blog"], []))
            self.assertIn("https://blog.example.com", u.format_facts(d, up))
            u.mark_sent(d, up)
            self.assertEqual(u.poll(), ([], []))
        with mock.patch("bot.uptime_watch.fetch", return_value={"blog": (1, "https://blog.example.com")}):
            d, up = u.poll()
            self.assertEqual((d, up), ([], ["blog"]))
            self.assertIn("was down", u.format_facts(d, up))

    def test_unreachable_kuma_raises_and_changes_nothing(self):
        with mock.patch("bot.uptime_watch.fetch", return_value={"blog": (0, "")}):
            u.poll()
        with mock.patch("bot.uptime_watch.fetch", side_effect=OSError("kuma down")):
            with self.assertRaises(OSError):
                u.poll()
        with mock.patch("bot.uptime_watch.fetch", return_value={"blog": (0, "")}):
            self.assertEqual(u.poll(), (["blog"], []))

    def test_fetch_uses_basic_auth_with_the_key(self):
        class R:
            def read(self): return b"monitor_status{monitor_name=\"a\",monitor_url=\"https://a\"} 1\n"
            def __enter__(self): return self
            def __exit__(self, *a): return False
        with mock.patch("urllib.request.urlopen", return_value=R()) as op:
            self.assertEqual(u.fetch(), {"a": (1, "https://a")})
        req = op.call_args[0][0]
        self.assertEqual(req.full_url, "https://k.example.com/metrics")
        import base64
        self.assertEqual(base64.b64decode(req.get_header("Authorization").split()[1]), b":uk1_x")


if __name__ == "__main__":
    unittest.main()
