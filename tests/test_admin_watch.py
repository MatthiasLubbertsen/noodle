import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from bot import admin_watch


class AdminWatch(unittest.TestCase):
    def run_with(self, users):
        roles = [admin_watch._role(u) for u in users]
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(config, "ADMIN_SEEN_PATH", Path(d) / "a.json"), \
                mock.patch.object(config, "DATA_DIR", Path(d)), \
                mock.patch.object(admin_watch, "current_role", side_effect=roles):
            return [admin_watch.check() for _ in users]

    def test_first_run_only_baselines(self):
        self.assertEqual(self.run_with([{}, {"is_admin": True}]), [None, ("member", "admin")])

    def test_promotion_fires_once(self):
        out = self.run_with([{}, {"is_admin": True}, {"is_admin": True}])
        self.assertEqual(out, [None, ("member", "admin"), None])

    def test_demotion_is_silent_and_repromotion_fires(self):
        out = self.run_with([{"is_admin": True}, {}, {"is_admin": True}])
        self.assertEqual(out, [None, None, ("member", "admin")])

    def test_owner_beats_admin(self):
        self.assertEqual(admin_watch._role({"is_admin": True, "is_owner": True}), "owner")


if __name__ == "__main__":
    unittest.main()
