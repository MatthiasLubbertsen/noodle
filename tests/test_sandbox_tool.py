import unittest
from unittest import mock

from bot import sandbox, tools


class ShellTool(unittest.TestCase):
    def test_bash_is_offered(self):
        run_code = next(t for t in tools.TOOLS if t["function"]["name"] == "run_code")
        props = run_code["function"]["parameters"]["properties"]
        self.assertIn("bash", props["language"]["enum"])
        self.assertIn("files", props)
        self.assertIn("reset", props)

    def test_missing_command_hint(self):
        hints = tools._code_hints({"stderr": "bash: git: command not found", "error": "exit code 127"}, "bash")
        self.assertTrue(any("simulated shell" in h for h in hints), hints)

    def test_no_hint_for_other_languages(self):
        hints = tools._code_hints({"stderr": "command not found", "error": None}, "python")
        self.assertFalse(any("simulated shell" in h for h in hints), hints)

    def _call(self, args, result):
        with mock.patch.object(tools.sandbox, "configured", return_value=True), mock.patch.object(
            tools.sandbox, "run", return_value=result
        ) as run, mock.patch.object(tools, "_is_matthias", return_value=True):
            out = tools._run_code(args, "U1", {"is_dm": True, "channel": "D1"})
        return out, run

    def test_files_and_reset_only_go_to_bash(self):
        ok = {"ok": True, "stdout": "hi", "files": [], "duration_ms": 5}
        _, run = self._call({"language": "bash", "code": "cat a", "files": {"a": "x"}, "reset": True}, ok)
        run.assert_called_once_with("bash", "cat a", 20, files={"a": "x"}, reset=True)
        _, run = self._call({"language": "python", "code": "1", "files": {"a": "x"}}, ok)
        self.assertIsNone(run.call_args.kwargs["files"])

    def test_bad_files_are_refused(self):
        out, run = self._call({"language": "bash", "code": "ls", "files": {"a": 1}}, {})
        self.assertIn("files must be", out)
        run.assert_not_called()

    def test_nonzero_exit_is_visible_to_the_model(self):
        res = {"ok": False, "stdout": "", "stderr": "", "error": "exit code 1", "exit_code": 1, "files": [], "duration_ms": 3}
        out, _ = self._call({"language": "bash", "code": "grep x y"}, res)
        self.assertIn("exit code 1", out)


class ClientPayload(unittest.TestCase):
    def test_bash_payload_has_workspace_files_reset(self):
        captured = {}

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"ok": true}'

        def fake_urlopen(req, timeout):
            captured["body"] = req.data
            return Resp()

        with mock.patch.object(sandbox.urllib.request, "urlopen", fake_urlopen):
            sandbox.run("bash", "ls", 5, files={"a": "b"}, reset=True)
            import json

            body = json.loads(captured["body"])
            self.assertEqual(body["workspace"], "matthias")
            self.assertEqual(body["files"], {"a": "b"})
            self.assertTrue(body["reset"])
            sandbox.run("python", "1", 5, files={"a": "b"})
            self.assertNotIn("files", json.loads(captured["body"]))


if __name__ == "__main__":
    unittest.main()
