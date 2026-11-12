import logging
import sys
import unittest

from bot.log import _CompactFormatter, _SlackSocketFilter

SID = "16da32fb-2707-493c-b088-c286384a3a37"
NEW = "5e14f54d-321d-460b-a88a-195e358f2605"


def _record(msg, exc=None, name="noodle", level=logging.ERROR):
    rec = logging.LogRecord(name, level, __file__, 1, msg, None, None)
    if exc is not None:
        try:
            raise exc
        except Exception:  # noqa: BLE001
            rec.exc_info = sys.exc_info()
    return rec


class CompactFormatter(unittest.TestCase):
    def test_failure_is_one_line_without_traceback(self):
        rec = _record("embedding request failed", ValueError("boom\nsecond line"))
        out = _CompactFormatter("%(levelname)s %(message)s", full_tracebacks=False).format(rec)
        self.assertNotIn("\n", out)
        self.assertIn("ERROR embedding request failed", out)
        self.assertIn("[ValueError: boom second line] at test_log.py:", out)
        self.assertIn(" in _record", out)  # where it was raised

    def test_error_text_already_in_the_message_is_not_repeated(self):
        # slack_bolt: "Failed to process a message: <the json error>" + the same error as exc_info
        rec = _record("Failed to process a message: ValueError: bad json", ValueError("bad json"))
        out = _CompactFormatter("%(message)s", full_tracebacks=False).format(rec)
        self.assertEqual(out.count("bad json"), 1)
        self.assertIn(" at test_log.py:", out)

    def test_full_traceback_when_debugging(self):
        rec = _record("oops", KeyError("k"))
        out = _CompactFormatter("%(message)s", full_tracebacks=True).format(rec)
        self.assertIn("Traceback (most recent call last)", out)
        self.assertIn("KeyError", out)

    def test_plain_records_untouched_and_record_restored(self):
        fmt = _CompactFormatter("%(message)s", full_tracebacks=False)
        self.assertEqual(fmt.format(_record("hello")), "hello")
        rec = _record("failed", RuntimeError("x"))
        fmt.format(rec)
        self.assertIsNotNone(rec.exc_info)  # other handlers still see the exception


class SlackSocketFilter(unittest.TestCase):
    def _run(self, lines):
        """feed bolt's lines through the filter the way two handlers would, return what is kept."""
        f, kept = _SlackSocketFilter(), []
        for level, text in lines:
            rec = _record(text, name="slack_bolt.App", level=level)
            first, second = f.filter(rec), f.filter(rec)
            self.assertEqual(first, second)  # console and file handler agree
            if first:
                kept.append((rec.levelname, rec.getMessage()))
        return kept

    def test_a_dead_socket_and_its_reconnect_are_two_lines(self):
        broken = f"Failed to check the state of sock (session id: {SID}, error: BrokenPipeError, message: [Errno 32] Broken pipe)"
        kept = self._run(
            [(logging.ERROR, broken)] * 5
            + [
                (logging.INFO, f"The connection has been closed (session id: {SID})"),
                (logging.INFO, f"The session seems to be already closed. Reconnecting... (session id: {SID})"),
                (logging.INFO, "Connecting to a new endpoint..."),
                (logging.INFO, f"The connection has been closed (session id: {SID})"),
                (logging.INFO, f"A new session has been established (session id: {NEW})"),
                (logging.INFO, "Connected to a new endpoint..."),
                (logging.INFO, f"Stopped receiving messages from a connection (session id: {SID})"),
                (logging.INFO, f"Starting to receive messages from a new connection (session id: {NEW})"),
            ]
        )
        self.assertEqual(kept, [
            ("WARNING", "slack socket lost (BrokenPipeError: Broken pipe), reconnecting"),
            ("INFO", "slack socket connected (session 5e14f54d)"),
        ])

    def test_a_routine_reconnect_is_one_line_and_never_removed(self):
        kept = self._run([
            (logging.INFO, "Connecting to a new endpoint..."),
            (logging.INFO, f"A new session has been established (session id: {NEW})"),
            (logging.INFO, "Connected to a new endpoint..."),
        ])
        self.assertEqual(kept, [("INFO", "slack socket connected (session 5e14f54d)")])

    def test_the_next_outage_is_reported_again(self):
        def lost(sid):
            return (logging.ERROR, f"on_error invoked (session id: {sid}, error: ConnectionResetError, message: [Errno 104] Connection reset by peer)")
        kept = self._run([lost(SID), lost(SID), (logging.INFO, f"A new session has been established (session id: {NEW})"), lost(NEW)])
        self.assertEqual([m for _, m in kept], [
            "slack socket lost (ConnectionResetError: Connection reset by peer), reconnecting",
            "slack socket connected (session 5e14f54d)",
            "slack socket lost (ConnectionResetError: Connection reset by peer), reconnecting",
        ])

    def test_everything_else_passes_untouched(self):
        lines = [
            (logging.INFO, "⚡️ Bolt app is running!"),
            (logging.WARNING, "Unhandled request ({'type': 'app_rate_limited'})"),
            (logging.ERROR, "Failed to send a request to Slack API server: <urlopen error>"),
        ]
        self.assertEqual([m for _, m in self._run(lines)], [t for _, t in lines])
        rec = _record("The connection has been closed", name="noodle")
        self.assertTrue(_SlackSocketFilter().filter(rec))  # only slack_bolt's own lines


if __name__ == "__main__":
    unittest.main()
