import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from bot import hackatime, hackatime_watch


class FiveHourMark(unittest.TestCase):
    def test_marks(self):
        self.assertIsNone(hackatime_watch.five_hour_mark(0, 4))
        self.assertEqual(hackatime_watch.five_hour_mark(4, 5), 5)
        self.assertEqual(hackatime_watch.five_hour_mark(5, 9), None)
        self.assertEqual(hackatime_watch.five_hour_mark(3, 12), 10)  # a jump: highest mark only


class Milestones(unittest.TestCase):
    def run_with(self, totals_seq):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "seen.json"
            with mock.patch.object(config, "HACKATIME_SEEN_PATH", path), \
                    mock.patch.object(config, "DATA_DIR", Path(d)), \
                    mock.patch.object(hackatime, "project_totals", side_effect=totals_seq):
                return [hackatime_watch.new_milestones() for _ in totals_seq]

    def test_first_run_only_baselines(self):
        out = self.run_with([{"a": 300 * 3600}, {"a": 300 * 3600 + 3700}])
        self.assertEqual(out[0], [])
        self.assertEqual(out[1], [("a", 300, 301)])

    def test_recount_lower_never_reannounces(self):
        out = self.run_with([{"a": 3600 * 2}, {"a": 3600 * 3}, {"a": 3600 * 2 + 5}, {"a": 3600 * 3}])
        self.assertEqual(out[1], [("a", 2, 3)])
        self.assertEqual(out[2], [])
        self.assertEqual(out[3], [])


class OwnerOnly(unittest.TestCase):
    def test_days_and_sessions_need_matthias(self):
        with mock.patch.object(hackatime, "configured", return_value=True):
            for action in ("days", "sessions"):
                self.assertIn("only matthias", hackatime.query({"action": action}))


if __name__ == "__main__":
    unittest.main()
