import base64
import io
import tarfile
import unittest
from unittest import mock

from bot import repo_fetch


def make_tar(entries: dict, extra=None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
        for info in extra or []:
            tar.addfile(info)
    return buf.getvalue()


class ParseSpec(unittest.TestCase):
    def test_accepted_forms(self):
        self.assertEqual(repo_fetch.parse_spec("a/b"), ("a", "b", None))
        self.assertEqual(repo_fetch.parse_spec("a/b@main"), ("a", "b", "main"))
        self.assertEqual(repo_fetch.parse_spec("https://github.com/a/b.git"), ("a", "b", None))
        self.assertEqual(repo_fetch.parse_spec("https://github.com/a/b/tree/feat/x"), ("a", "b", "feat/x"))

    def test_rejected_forms(self):
        for bad in ["https://evil.com/a/b", "a/..", "../b", "a/b@x y", "a", "", "a/b/c/d", "http://github.com.evil.com/a/b"]:
            self.assertIsNone(repo_fetch.parse_spec(bad), bad)


class Unpack(unittest.TestCase):
    def test_strips_top_folder_and_skips_git_links_and_traversal(self):
        link = tarfile.TarInfo("top/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        data = make_tar(
            {"top/a.txt": b"A", "top/sub/b.txt": b"B", "top/.git/config": b"x", "top/../evil": b"x", "top/./c": b"C"},
            extra=[link],
        )
        files, stats = repo_fetch._unpack(data, "repo")
        paths = sorted(f["path"] for f in files)
        self.assertEqual(paths, ["repo/a.txt", "repo/c", "repo/sub/b.txt"])
        self.assertEqual(base64.b64decode(files[0]["data"]), b"A")
        self.assertEqual(stats["files"], 3)

    def test_size_caps(self):
        with mock.patch.object(repo_fetch, "MAX_FILE_BYTES", 10), mock.patch.object(repo_fetch, "MAX_TOTAL_BYTES", 12):
            data = make_tar({"t/big": b"x" * 11, "t/a": b"123456", "t/b": b"123456", "t/c": b"1"})
            files, stats = repo_fetch._unpack(data, "r")
        self.assertEqual(stats["skipped_big"], 1)
        self.assertEqual(stats["skipped_cap"], 1)  # "c" would be the 13th byte
        self.assertEqual(sorted(f["path"] for f in files), ["r/a", "r/b"])


class Redirects(unittest.TestCase):
    def test_only_github_hosts_are_followed(self):
        handler = repo_fetch._GithubOnly()
        req = repo_fetch.urllib.request.Request("https://api.github.com/x")
        with self.assertRaises(repo_fetch.urllib.error.URLError):
            handler.redirect_request(req, None, 302, "Found", {}, "https://evil.example.com/x")
        ok = handler.redirect_request(req, None, 302, "Found", {}, "https://codeload.github.com/x")
        self.assertIsNotNone(ok)


class Fetch(unittest.TestCase):
    def test_fetch_returns_files_and_summary(self):
        data = make_tar({"o-r-sha/README.md": b"hi"})
        with mock.patch.object(repo_fetch, "_download", return_value=data):
            out = repo_fetch.fetch("o/r@dev")
        self.assertEqual(out["dir"], "r")
        self.assertIn("cloned o/r@dev into /work/r", out["summary"])

    def test_errors_become_strings(self):
        err = repo_fetch.urllib.error.HTTPError("u", 404, "nf", {}, None)
        with mock.patch.object(repo_fetch, "_download", side_effect=err):
            self.assertIn("not found, private", repo_fetch.fetch("o/r"))
        self.assertIn("repo must look like", repo_fetch.fetch("nonsense"))


if __name__ == "__main__":
    unittest.main()
