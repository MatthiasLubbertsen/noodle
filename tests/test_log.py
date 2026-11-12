import logging
import sys
import unittest

from bot.log import _CompactFormatter


def _record(msg, exc=None):
    rec = logging.LogRecord("noodle", logging.ERROR, __file__, 1, msg, None, None)
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
        self.assertIn("[ValueError: boom] at test_log.py:", out)
        self.assertIn(" in _record", out)  # innermost frame, where it was raised

    def test_full_traceback_when_debugging(self):
        rec = _record("oops", KeyError("k"))
        out = _CompactFormatter("%(message)s", full_tracebacks=True).format(rec)
        self.assertIn("Traceback (most recent call last)", out)
        self.assertIn("KeyError", out)

    def test_plain_records_untouched_and_record_restored(self):
        fmt = _CompactFormatter("%(message)s", full_tracebacks=False)
        rec = _record("hello")
        self.assertEqual(fmt.format(rec), "hello")
        rec2 = _record("failed", RuntimeError("x"))
        fmt.format(rec2)
        self.assertIsNotNone(rec2.exc_info)  # other handlers still see the exception


if __name__ == "__main__":
    unittest.main()
