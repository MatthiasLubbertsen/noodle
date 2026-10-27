"""bot/rtm.py against a fake slack web api + web client websocket (tests/fake_slack.py)."""
import functools
import logging
import threading
import unittest
from unittest import mock

import config
from bot import features, oneshot, rtm
from tests.fake_slack import FakeSlack, wait_for


class _Stop(BaseException):
    """ends rtm._loop from inside a test (it catches Exception, so this goes through)."""


class RtmTestCase(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)  # expected failures log tracebacks
        self.addCleanup(logging.disable, logging.NOTSET)
        self.fake = FakeSlack().start()
        self.addCleanup(self.fake.stop)
        self._patches = [
            mock.patch.object(config, "SLACK_WEB_TOKEN", self.fake.token),
            mock.patch.object(config, "SLACK_WEB_COOKIE", self.fake.cookie),
            mock.patch.object(rtm, "PING_SECONDS", 0.2),
            mock.patch.object(rtm, "TYPING_REFRESH_SECONDS", 0.05),
            mock.patch.object(rtm, "RECONNECT_DELAY_SECONDS", 0.05),
            mock.patch.object(rtm, "EXPIRED_RETRY_SECONDS", 0.05),
            # the real WebClient, pointed at the fake instead of slack.com
            mock.patch.object(rtm, "WebClient",
                              functools.partial(rtm.WebClient, base_url=self.fake.api_url)),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)
        rtm._ws = None
        rtm._started = False
        rtm._expired_reported = False

    def run_once(self, url=None):
        """rtm._run_once in a thread; returns (thread, result dict)."""
        out = {}

        def target():
            out["reconnect"] = rtm._run_once(url or self.fake.ws_url)

        t = threading.Thread(target=target, daemon=True)
        t.start()
        self.addCleanup(self.fake.drop)
        return t, out

    def run_loop(self, max_url_fetches=None):
        """rtm._loop in a thread. stops (via _Stop) once getWebSocketURL was asked
        `max_url_fetches` times, if given."""
        real = rtm._websocket_url
        count = {"n": 0}
        stop = threading.Event()

        def limited():
            if stop.is_set() or (max_url_fetches is not None and count["n"] >= max_url_fetches):
                raise _Stop()
            count["n"] += 1
            return real()

        def target():
            try:
                rtm._loop()
            except _Stop:
                pass

        p = mock.patch.object(rtm, "_websocket_url", limited)
        p.start()
        self.addCleanup(p.stop)
        t = threading.Thread(target=target, daemon=True)
        t.start()

        def shutdown():
            # the loop must not outlive the test (it would go on to talk to the real slack.com)
            stop.set()
            self.fake.drop()
            t.join(3)

        self.addCleanup(shutdown)
        return t


class Connecting(RtmTestCase):
    def test_websocket_url_asks_the_web_api_with_the_cookie(self):
        self.assertEqual(rtm._websocket_url(), self.fake.ws_url)
        method, params, headers = self.fake.web_calls[0]
        self.assertEqual(method, "client.getWebSocketURL")
        self.assertIn(f"d={self.fake.cookie}", headers.get("Cookie", ""))
        self.assertIn(self.fake.token, headers.get("Authorization", "") + str(params))

    def test_connects_with_token_and_cookie_and_goes_online(self):
        self.run_once()
        self.assertTrue(wait_for(self.fake.connected))
        self.assertIn(f"token={self.fake.token}", self.fake.ws_paths[0])
        self.assertIn(f"d={self.fake.cookie}", self.fake.ws_headers[0]["Cookie"])
        self.assertTrue(wait_for(lambda: rtm._ws is not None))

    def test_existing_query_string_gets_the_token_appended_with_ampersand(self):
        self.run_once(self.fake.ws_url + "?ticket=abc")
        self.assertTrue(wait_for(self.fake.connected))
        self.assertIn("ticket=abc&token=", self.fake.ws_paths[0])

    def test_wrong_cookie_is_refused(self):
        with mock.patch.object(config, "SLACK_WEB_COOKIE", "stale"):
            with self.assertRaises(Exception) as ctx:
                rtm._run_once(self.fake.ws_url)
        self.assertTrue(rtm._is_auth_failure(ctx.exception))
        self.assertIsNone(rtm._ws)


class Pinging(RtmTestCase):
    def test_pings_keep_coming_with_increasing_ids(self):
        self.run_once()
        self.assertTrue(wait_for(lambda: len(self.fake.frames_of("ping")) >= 3))
        ids = [f["id"] for f in self.fake.frames_of("ping")]
        self.assertEqual(ids, sorted(set(ids)))

    def test_pinger_stops_after_disconnect(self):
        t, _ = self.run_once()
        self.assertTrue(wait_for(lambda: self.fake.frames_of("ping")))
        self.fake.drop()
        t.join(3)
        self.assertFalse(t.is_alive())
        n = len(self.fake.frames_of("ping"))
        import time
        time.sleep(0.5)
        self.assertEqual(len(self.fake.frames_of("ping")), n)


class Typing(RtmTestCase):
    def test_typing_frames_for_channel_and_thread(self):
        self.run_once()
        self.assertTrue(wait_for(self.fake.connected))
        with rtm.typing("D123", "1700.1"):
            self.assertTrue(wait_for(lambda: len(self.fake.frames_of("typing")) >= 2))
        frame = self.fake.frames_of("typing")[0]
        self.assertEqual(frame["channel"], "D123")
        self.assertEqual(frame["thread_ts"], "1700.1")
        self.assertIn("id", frame)

    def test_no_thread_ts_key_outside_threads(self):
        self.run_once()
        self.assertTrue(wait_for(self.fake.connected))
        self.assertTrue(rtm.send_typing("D123"))
        frame = wait_for(lambda: (self.fake.frames_of("typing") or [None])[0])
        self.assertNotIn("thread_ts", frame)

    def test_typing_stops_when_the_block_ends(self):
        self.run_once()
        self.assertTrue(wait_for(self.fake.connected))
        with rtm.typing("D123"):
            self.assertTrue(wait_for(lambda: self.fake.frames_of("typing")))
        import time
        time.sleep(0.15)  # let a refresh already in flight land
        n = len(self.fake.frames_of("typing"))
        time.sleep(0.4)
        self.assertEqual(len(self.fake.frames_of("typing")), n)

    def test_typing_stops_even_if_the_block_raises(self):
        self.run_once()
        self.assertTrue(wait_for(self.fake.connected))
        with self.assertRaises(ValueError):
            with rtm.typing("D123"):
                self.assertTrue(wait_for(lambda: self.fake.frames_of("typing")))
                raise ValueError("boom")
        import time
        time.sleep(0.15)
        n = len(self.fake.frames_of("typing"))
        time.sleep(0.4)
        self.assertEqual(len(self.fake.frames_of("typing")), n)

    def test_typing_while_offline_is_harmless(self):
        self.assertFalse(rtm.send_typing("D123"))
        with rtm.typing("D123"):
            pass  # must not raise or hang

    def test_typing_is_a_noop_without_web_credentials(self):
        self.run_once()
        self.assertTrue(wait_for(self.fake.connected))
        with mock.patch.object(config, "SLACK_WEB_TOKEN", ""):
            self.assertFalse(features.enabled("typing"))
            with rtm.typing("D123"):
                import time
                time.sleep(0.2)
        self.assertEqual(self.fake.frames_of("typing"), [])


class Frames(RtmTestCase):
    def test_garbage_frames_do_not_kill_the_connection(self):
        t, _ = self.run_once()
        self.assertTrue(wait_for(self.fake.connected))
        self.fake.push("this is not json")
        self.fake.push({"type": "message", "text": "hi"})
        self.assertTrue(rtm.send_typing("D1"))
        self.assertTrue(wait_for(lambda: self.fake.frames_of("typing")))
        self.assertTrue(t.is_alive())

    def test_reconnect_url_is_returned_when_the_connection_drops(self):
        t, out = self.run_once()
        self.assertTrue(wait_for(self.fake.connected))
        self.fake.push({"type": "reconnect_url", "url": "ws://example.invalid/next"})
        self.fake.push({"type": "reconnect_url", "url": "ws://example.invalid/latest"})
        import time
        time.sleep(0.1)
        self.fake.drop()
        t.join(3)
        self.assertEqual(out["reconnect"], "ws://example.invalid/latest")
        self.assertIsNone(rtm._ws)

    def test_no_reconnect_url_means_none(self):
        t, out = self.run_once()
        self.assertTrue(wait_for(self.fake.connected))
        self.fake.drop()
        t.join(3)
        self.assertIsNone(out["reconnect"])


class Loop(RtmTestCase):
    def test_reconnects_after_a_drop_with_a_fresh_url(self):
        self.run_loop()
        self.assertTrue(wait_for(self.fake.connected))
        self.fake.drop()
        self.assertTrue(wait_for(lambda: len(self.fake.ws_paths) >= 2))
        self.assertTrue(wait_for(self.fake.connected))
        self.assertEqual(len(self.fake.calls("client.getWebSocketURL")), 2)

    def test_follows_reconnect_url_without_asking_the_api_again(self):
        self.run_loop()
        self.assertTrue(wait_for(self.fake.connected))
        self.fake.push({"type": "reconnect_url", "url": self.fake.ws_url + "?reconnect=1"})
        import time
        time.sleep(0.1)
        self.fake.drop()
        self.assertTrue(wait_for(lambda: len(self.fake.ws_paths) >= 2))
        self.assertIn("reconnect=1", self.fake.ws_paths[1])
        self.assertEqual(len(self.fake.calls("client.getWebSocketURL")), 1)

    def test_typing_works_again_after_a_reconnect(self):
        self.run_loop()
        self.assertTrue(wait_for(self.fake.connected))
        self.fake.drop()
        self.assertTrue(wait_for(lambda: len(self.fake.ws_paths) >= 2 and self.fake.connected()))
        self.assertTrue(wait_for(lambda: rtm._ws is not None))
        self.assertTrue(rtm.send_typing("D1"))
        self.assertTrue(wait_for(lambda: self.fake.frames_of("typing")))

    def test_transient_api_failure_retries_without_dming(self):
        self.fake.api_overrides["client.getWebSocketURL"] = [
            {"ok": False, "error": "ratelimited"},
            {"ok": False, "error": "internal_error"},
        ]
        with mock.patch.object(rtm, "_report_expired") as report:
            self.run_loop()
            self.assertTrue(wait_for(self.fake.connected))
        report.assert_not_called()
        self.assertEqual(len(self.fake.calls("client.getWebSocketURL")), 3)

    def test_server_error_status_on_the_handshake_is_not_an_auth_failure(self):
        self.fake.ws_status = 500
        with mock.patch.object(rtm, "_report_expired") as report:
            self.run_loop()
            self.assertTrue(wait_for(lambda: len(self.fake.ws_paths) >= 3))
        report.assert_not_called()
        self.fake.ws_status = None
        self.assertTrue(wait_for(self.fake.connected))


class ExpiredSession(RtmTestCase):
    def test_every_auth_error_code_counts(self):
        from slack_sdk.errors import SlackApiError
        for code in ("invalid_auth", "token_expired", "token_revoked", "not_authed"):
            exc = SlackApiError("x", {"ok": False, "error": code})
            self.assertTrue(rtm._is_auth_failure(exc), code)
        self.assertFalse(rtm._is_auth_failure(SlackApiError("x", {"ok": False, "error": "ratelimited"})))
        self.assertFalse(rtm._is_auth_failure(RuntimeError("nope")))

    def test_expired_api_session_dms_once_and_keeps_retrying_slowly(self):
        self.fake.api_overrides["client.getWebSocketURL"] = {"ok": False, "error": "invalid_auth"}
        with mock.patch.object(rtm, "_report_expired", wraps=rtm._report_expired) as report, \
                mock.patch.object(oneshot, "complete", return_value="my session expired") as complete, \
                mock.patch("bot.scheduler._post_dm") as post_dm:
            self.run_loop()
            self.assertTrue(wait_for(lambda: len(self.fake.calls("client.getWebSocketURL")) >= 4))
        self.assertGreaterEqual(report.call_count, 3)   # asked every retry...
        post_dm.assert_called_once_with("my session expired")  # ...but only one dm
        complete.assert_called_once()

    def test_handshake_401_counts_as_expired(self):
        self.fake.ws_status = 401
        with mock.patch.object(rtm, "_report_expired") as report:
            self.run_loop()
            self.assertTrue(wait_for(lambda: report.called))

    def test_dm_falls_back_to_plain_text_when_the_model_is_down(self):
        with mock.patch.object(oneshot, "complete", return_value=None), \
                mock.patch("bot.scheduler._post_dm") as post_dm:
            rtm._report_expired()
        text = post_dm.call_args.args[0]
        self.assertIn("SLACK_WEB_TOKEN", text)

    def test_a_failing_dm_never_raises(self):
        with mock.patch.object(oneshot, "complete", side_effect=RuntimeError("down")):
            rtm._report_expired()  # must swallow

    def test_a_good_connection_resets_the_report_so_the_next_outage_dms_again(self):
        with mock.patch.object(oneshot, "complete", return_value="expired"), \
                mock.patch("bot.scheduler._post_dm") as post_dm:
            rtm._report_expired()
            rtm._report_expired()
            self.assertEqual(post_dm.call_count, 1)
            self.run_once()
            self.assertTrue(wait_for(self.fake.connected))
            self.assertTrue(wait_for(lambda: not rtm._expired_reported))
            rtm._report_expired()
            self.assertEqual(post_dm.call_count, 2)


class Start(RtmTestCase):
    def test_start_without_credentials_does_nothing(self):
        with mock.patch.object(config, "SLACK_WEB_COOKIE", ""), \
                mock.patch("threading.Thread") as thread:
            rtm.start()
        thread.assert_not_called()
        self.assertFalse(rtm._started)

    def test_start_runs_only_once(self):
        with mock.patch("threading.Thread") as thread:
            rtm.start()
            rtm.start()
        self.assertEqual(thread.call_count, 1)


if __name__ == "__main__":
    unittest.main()
