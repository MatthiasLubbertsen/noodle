import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from bot import release_watch

A, B, C = "a" * 40, "b" * 40, "c" * 40


class Check(unittest.TestCase):
    def run_seq(self, shas):
        out = []
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(config, "RELEASE_SEEN_PATH", Path(d) / "seen.json"), \
                mock.patch.object(config, "DATA_DIR", Path(d)):
            for sha in shas:
                with mock.patch.dict("os.environ", {"NOODLE_SHA": sha}):
                    out.append(release_watch.check())
        return out

    def test_first_run_baselines_then_announces_once(self):
        self.assertEqual(self.run_seq([A, A, B, B, B, C]), [None, None, (A, B), None, None, (B, C)])

    def test_no_sha_never_announces(self):
        self.assertEqual(self.run_seq(["", "unknown", ""]), [None, None, None])


class Compose(unittest.TestCase):
    def test_prompt_gets_the_commits_and_fact_is_saved(self):
        data = {"commits": [{"commit": {"message": "add release notes\n\nbody"}}], "files": [{"filename": "bot/x.py"}]}
        with mock.patch.object(release_watch, "_get", return_value=data), \
                mock.patch.object(release_watch, "remember_facts") as facts, \
                mock.patch.object(release_watch, "complete", return_value="hi") as comp:
            self.assertEqual(release_watch.compose_message(A, B), "hi")
        prompt = comp.call_args[0][0]
        self.assertIn("add release notes", prompt)
        self.assertIn("bot/x.py", prompt)
        facts.assert_called_once()

    def test_nothing_readable_means_no_dm(self):
        with mock.patch.object(release_watch, "_get", side_effect=OSError("down")), \
                mock.patch.object(release_watch, "complete") as comp:
            self.assertIsNone(release_watch.compose_message(A, B))
        comp.assert_not_called()

    def test_falls_back_to_the_newest_commit(self):
        def get(path):
            if "compare" in path:
                raise OSError("unknown sha")
            return {"commit": {"message": "fix a thing"}}

        with mock.patch.object(release_watch, "_get", side_effect=get):
            self.assertIn("fix a thing", release_watch.changes(A, B))


if __name__ == "__main__":
    unittest.main()
