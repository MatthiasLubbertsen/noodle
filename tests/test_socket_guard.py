import json
import logging
import time
import unittest

from slack_sdk import WebClient
from slack_sdk.socket_mode.builtin import SocketModeClient

from bot.socket_guard import drop_half_messages
from tests.fake_slack import wait_for

EVENT = json.dumps({"type": "events_api", "envelope_id": "e1", "accepts_response_payload": False,
                    "payload": {"event": {"type": "message", "text": "x" * 1500}}})


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append((record.levelname, record.getMessage()))


class DropHalfMessages(unittest.TestCase):
    """slack_sdk's real built-in client (never connected), fed the way its socket reader feeds it."""

    def setUp(self):
        self.log = _Capture()
        self.sdk_logger = logging.getLogger("test.socket_guard")
        self.sdk_logger.addHandler(self.log)
        self.sdk_logger.propagate = False
        self.noodle_logger = logging.getLogger("noodle")
        self.noodle_logger.addHandler(self.log)
        self.addCleanup(self.noodle_logger.removeHandler, self.log)
        self.addCleanup(self.noodle_logger.setLevel, self.noodle_logger.level)
        self.noodle_logger.setLevel(logging.INFO)
        self.client = SocketModeClient(app_token="xapp-fake", logger=self.sdk_logger, web_client=WebClient())
        self.addCleanup(self.client.close)
        self.seen = []
        self.client.message_listeners.append(lambda client, message, raw: self.seen.append(message))

    def _errors(self):
        return [m for level, m in self.log.lines if level == "ERROR"]

    def test_without_the_guard_a_cut_off_message_is_an_error(self):
        # documents the library behaviour this guards against; if it stops failing, drop the guard
        self.client._on_message(EVENT[:1020])
        self.assertTrue(wait_for(self._errors))
        self.assertIn("Failed to process a message", self._errors()[0])

    def test_cut_off_message_is_dropped_with_one_line_and_whole_ones_pass(self):
        drop_half_messages(self.client)
        self.client._on_message(EVENT[:1020])  # what a closing connection leaves behind
        self.client._on_message(EVENT)
        self.client._on_message("")  # pings and other non-json text are not ours to judge
        self.assertTrue(wait_for(lambda: self.seen))
        time.sleep(0.1)
        self.assertEqual([m["envelope_id"] for m in self.seen if m], ["e1"])
        self.assertEqual(self._errors(), [])
        dropped = [m for _, m in self.log.lines if "dropped a half-received message" in m]
        self.assertEqual(dropped, ["slack socket: dropped a half-received message (1020 chars) from a closing connection"])


if __name__ == "__main__":
    unittest.main()
