import datetime
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from bot import ci_watch, github, github_watch


def _run(id, actor="MatthiasLubbertsen", age_h=1, **kw):
    made = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=age_h)
    return {"id": id, "name": "CI", "head_branch": "main", "head_sha": "abcdef123", "actor": {"login": actor},
            "head_commit": {"message": "fix stuff\n\nlong body"}, "html_url": f"https://github.com/a/b/actions/runs/{id}",
            "created_at": made.isoformat().replace("+00:00", "Z"), "conclusion": "failure", **kw}


class CiWatchTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = mock.patch.multiple(config, DATA_DIR=Path(tmp.name), CI_SEEN_PATH=Path(tmp.name) / "ci.json")
        p.start()
        self.addCleanup(p.stop)

    def _poll(self, runs):
        with mock.patch("bot.github.recent_repos", return_value=["a/b"]), \
             mock.patch("bot.github.failed_runs", return_value=runs):
            return ci_watch.new_failures()

    def test_first_run_baselines_then_reports_only_new(self):
        self.assertEqual(self._poll([_run(1)]), [])
        self.assertEqual(self._poll([_run(1)]), [])
        new = self._poll([_run(1), _run(2)])
        self.assertEqual([f["id"] for f in new], [2])
        self.assertEqual(new[0]["message"], "fix stuff")
        ci_watch.mark_seen(new)
        self.assertEqual(self._poll([_run(1), _run(2)]), [])

    def test_bots_and_old_runs_are_skipped(self):
        self._poll([])
        self.assertEqual(self._poll([_run(3, actor="dependabot[bot]"), _run(4, age_h=48)]), [])

    def test_one_broken_repo_does_not_stop_the_rest(self):
        self._poll([])
        def runs(repo):
            if repo == "a/x":
                raise OSError("404")
            return [_run(5)]
        with mock.patch("bot.github.recent_repos", return_value=["a/x", "a/b"]), \
             mock.patch("bot.github.failed_runs", side_effect=runs):
            self.assertEqual([f["id"] for f in ci_watch.new_failures()], [5])

    def test_message_falls_back_to_facts_when_model_drops_a_link(self):
        item = {"id": 9, "repo": "a/b", "workflow": "CI", "branch": "main", "sha": "abcdef1",
                "message": "", "actor": "me", "url": "https://github.com/a/b/actions/runs/9"}
        with mock.patch("bot.github.failed_step", return_value='job "test", step "pytest"'), \
             mock.patch("bot.ci_watch.complete", return_value="it broke lol"), \
             mock.patch("bot.ci_watch.remember_facts"):
            msg = ci_watch.compose_message([item])
        self.assertIn("https://github.com/a/b/actions/runs/9", msg)
        self.assertIn('step "pytest"', msg)

    def test_ci_notifications_are_left_to_the_ci_watcher(self):
        n = {"id": "1", "reason": "ci_activity", "updated_at": "t"}
        with mock.patch("bot.github.notifications", return_value=[n]), \
             mock.patch("bot.github_watch._load_seen", return_value={}):
            self.assertEqual(github_watch.new_notifications(), [])


class FailedStepTests(unittest.TestCase):
    def test_names_job_and_step(self):
        jobs = {"jobs": [{"name": "lint", "conclusion": "success"},
                         {"name": "test", "conclusion": "failure", "steps": [
                             {"name": "setup", "conclusion": "success"}, {"name": "pytest", "conclusion": "failure"}]}]}
        with mock.patch("bot.github._get", return_value=jobs):
            self.assertEqual(github.failed_step("a/b", 1), 'job "test", step "pytest"')

    def test_error_is_empty(self):
        with mock.patch("bot.github._get", side_effect=OSError):
            self.assertEqual(github.failed_step("a/b", 1), "")


if __name__ == "__main__":
    unittest.main()
