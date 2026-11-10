import unittest
from unittest import mock

from bot import github, github_watch


def _n(**subject):
    return {"id": "1", "reason": "state_change", "updated_at": "t",
            "repository": {"full_name": "a/b"},
            "subject": {"title": "fix it", "type": "PullRequest",
                        "url": "https://api.github.com/repos/a/b/pulls/7", **subject}}


class SubjectDetailTests(unittest.TestCase):
    def test_merged_pr_says_merged(self):
        with mock.patch("bot.github._get", return_value={"state": "closed", "merged": True}):
            self.assertEqual(github.subject_detail(_n()), "merged")

    def test_closed_unmerged_pr(self):
        with mock.patch("bot.github._get", return_value={"state": "closed", "merged": False}):
            self.assertEqual(github.subject_detail(_n()), "closed (not merged)")

    def test_failure_is_empty_not_an_error(self):
        with mock.patch("bot.github._get", side_effect=OSError("down")):
            self.assertEqual(github.subject_detail(_n()), "")

    def test_facts_include_the_state(self):
        with mock.patch("bot.github.subject_detail", return_value="merged"):
            self.assertIn("now merged", github_watch.format_facts([_n()]))


if __name__ == "__main__":
    unittest.main()
