"""bot/handlers.py end to end against the fake slack web api: a real slack_sdk WebClient posts
to tests/fake_slack.py, only the model (ask_noodle) is stubbed."""
import logging
import threading
import types
import unittest
from unittest import mock

import config
from bot import handlers, state
from slack_sdk import WebClient
from tests.fake_slack import FakeSlack, wait_for

ME = config.USER_ID          # matthias
BOT = "UNOODLE"
DM = "D111"
CHAN = "C222"


class HandlerTestCase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSlack().start()
        self.addCleanup(self.fake.stop)
        state.app = types.SimpleNamespace(client=WebClient(token="xoxp-fake", base_url=self.fake.api_url))
        state.AUTH_USER_ID = BOT
        state.MENTION_IDS = {BOT}
        for name in ("MEMORY", "LAST_UNPROMPTED_REPLY", "CONV_LOCKS", "LAST_REPLY"):
            setattr(state, name, {})
        state.PARTICIPATING_THREADS = set()
        self.asked = []
        self._baseline = set(threading.enumerate())  # the fake's own server threads
        logging.disable(logging.CRITICAL)  # the failure tests log tracebacks on purpose
        self.addCleanup(logging.disable, logging.NOTSET)

        def fake_ask(key, prompt, **kw):
            self.asked.append((key, prompt, kw))
            return "hello there"

        patches = [
            mock.patch.object(handlers, "ask_noodle", side_effect=fake_ask),
            mock.patch.object(handlers, "typing", lambda *a, **k: mock.MagicMock()),
            mock.patch.object(config, "CHUNK_DELAY_SECONDS", 0),
            mock.patch.object(config, "ALLOWED_CHANNELS", [CHAN]),
            mock.patch.object(config, "ALLOW_CHANNEL_WILDCARD", False),
            mock.patch.object(config, "UNPROMPTED_COOLDOWN_SECONDS", 300),
            mock.patch.object(handlers, "should_reply_unprompted", return_value=False),
            # never reach the real flaron directory
            mock.patch("bot.directory._get_json", return_value=None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.gate = handlers.should_reply_unprompted

    def send(self, **event):
        event.setdefault("ts", "1700000000.000100")
        event.setdefault("text", "hi")
        handlers.handle_message(event)

    def settle(self):
        """wait for the worker threads handle_message starts."""
        for t in threading.enumerate():
            if t not in self._baseline and t is not threading.current_thread():
                t.join(2)

    def quiet(self):
        self.settle()
        return not self.fake.posted() and not self.asked


class Dms(HandlerTestCase):
    def test_dm_from_matthias_gets_a_reply_in_the_dm(self):
        self.send(user=ME, channel=DM, channel_type="im", text="hey noodle")
        self.assertTrue(wait_for(self.fake.posted))
        self.assertEqual(self.fake.posted(), [(DM, "hello there", None)])
        self.assertEqual(self.asked[0][2]["user_id"], ME)
        self.assertTrue(self.asked[0][2]["origin"]["is_dm"])

    def test_dm_from_anyone_else_is_ignored(self):
        self.send(user="USTRANGER", channel=DM, channel_type="im")
        self.assertTrue(self.quiet())

    def test_hash_space_prefix_is_always_ignored(self):
        self.send(user=ME, channel=DM, channel_type="im", text="# note to self")
        self.assertTrue(self.quiet())

    def test_noodles_own_messages_never_loop(self):
        self.send(user=BOT, channel=DM, channel_type="im")
        self.send(user=ME, channel=DM, channel_type="im", bot_id="B1")
        self.send(user=ME, channel=DM, channel_type="im", subtype="message_changed")
        self.assertTrue(self.quiet())

    def test_multi_paragraph_reply_is_split_and_capped_at_8(self):
        with mock.patch.object(handlers, "ask_noodle", return_value="\n\n".join(f"p{i}" for i in range(12))):
            self.send(user=ME, channel=DM, channel_type="im")
            self.assertTrue(wait_for(lambda: len(self.fake.posted()) >= 8))
            self.settle()
        self.assertEqual([t for _, t, _ in self.fake.posted()], [f"p{i}" for i in range(8)])


class Channels(HandlerTestCase):
    def test_mention_in_allowed_channel_replies_top_level_and_joins_the_thread(self):
        self.send(user="UOTHER", channel=CHAN, text=f"<@{BOT}> what's up", ts="1700000001.000100")
        self.assertTrue(wait_for(self.fake.posted))
        self.assertEqual(self.fake.posted()[0][0::2], (CHAN, None))
        # slack's reply ts becomes a thread anyone can reply in
        posted_ts = "1700000000.000001"
        self.assertIn(posted_ts, state.PARTICIPATING_THREADS)
        # our own mention was stripped before the model saw it
        self.assertNotIn(BOT, self.asked[0][1])

    def test_plain_word_noodle_counts_as_a_mention(self):
        self.send(user="UOTHER", channel=CHAN, text="Noodle, are you there?")
        self.assertTrue(wait_for(self.fake.posted))

    def test_mention_in_a_channel_that_is_not_allowed_is_ignored(self):
        self.send(user="UOTHER", channel="C999", text=f"<@{BOT}> hi")
        self.assertTrue(self.quiet())

    def test_wildcard_allows_every_channel(self):
        with mock.patch.object(config, "ALLOW_CHANNEL_WILDCARD", True):
            self.send(user="UOTHER", channel="C999", text="noodle?")
            self.assertTrue(wait_for(self.fake.posted))

    def test_reply_to_a_thread_noodle_is_in_is_answered_without_a_mention(self):
        state.PARTICIPATING_THREADS.add("1700.5")
        self.send(user="UOTHER", channel="C999", text="and then?", thread_ts="1700.5")
        self.assertTrue(wait_for(self.fake.posted))
        self.assertEqual(self.fake.posted()[0], ("C999", "hello there", "1700.5"))

    def test_reply_in_a_thread_noodle_is_not_in_is_not_answered(self):
        self.send(user="UOTHER", channel=CHAN, text="lol", thread_ts="1700.6")
        self.assertTrue(self.quiet())

    def test_mention_in_a_thread_replies_in_that_thread_and_remembers_it(self):
        self.send(user="UOTHER", channel=CHAN, text="noodle help", thread_ts="1700.7")
        self.assertTrue(wait_for(self.fake.posted))
        self.assertEqual(self.fake.posted()[0][2], "1700.7")
        self.assertIn("1700.7", state.PARTICIPATING_THREADS)


class Gate(HandlerTestCase):
    def test_gate_yes_replies_unprompted_without_persisting(self):
        with mock.patch.object(handlers, "should_reply_unprompted", return_value=True):
            self.send(user="UOTHER", channel=CHAN, text="anyone know the wifi password")
            self.assertTrue(wait_for(self.fake.posted))
        self.assertFalse(self.asked[0][2]["persist"])

    def test_gate_no_stays_quiet(self):
        self.send(user="UOTHER", channel=CHAN, text="anyone know the wifi password")
        self.assertTrue(self.quiet())

    def test_cooldown_blocks_a_second_unprompted_reply_but_not_a_mention(self):
        with mock.patch.object(handlers, "should_reply_unprompted", return_value=True) as gate:
            self.send(user="UOTHER", channel=CHAN, text="first", ts="1.1")
            self.assertTrue(wait_for(self.fake.posted))
            self.settle()
            self.send(user="UOTHER", channel=CHAN, text="second", ts="1.2")
            self.settle()
            self.assertEqual(gate.call_count, 1)  # the cooldown stopped it before asking the gate
            self.assertEqual(len(self.fake.posted()), 1)
            self.send(user="UOTHER", channel=CHAN, text="noodle, third", ts="1.3")
            self.assertTrue(wait_for(lambda: len(self.fake.posted()) == 2))

    def test_gate_is_not_asked_in_channels_that_are_not_allowed(self):
        with mock.patch.object(handlers, "should_reply_unprompted", return_value=True) as gate:
            self.send(user="UOTHER", channel="C999", text="hello")
            self.assertTrue(self.quiet())
        gate.assert_not_called()


class MatthiasDayPing(HandlerTestCase):
    def setUp(self):
        super().setUp()
        self.persisted = []
        p = mock.patch.object(handlers.matthias_day, "persist_ping",
                              side_effect=lambda *a: self.persisted.append(a))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(handlers, "build_daily_summary", return_value="you sent 42 messages")
        p.start()
        self.addCleanup(p.stop)
        self.ping = f"<!subteam^{config.MATTHIAS_DAY_GROUP_ID}> today:\n• did a thing"

    def test_ping_gets_the_recap_as_a_top_level_message_and_is_persisted(self):
        self.send(user=ME, channel="C333", text=self.ping, ts="1700.9", thread_ts="1700.1")
        self.assertTrue(wait_for(self.fake.posted))
        self.assertEqual(self.fake.posted(), [("C333", "you sent 42 messages", None)])
        self.assertTrue(wait_for(lambda: self.persisted))
        self.assertEqual(self.persisted[0][1:3], ("C333", "1700.9"))
        self.assertEqual(self.asked, [])  # never goes through the chat model

    def test_ping_posted_bot_style_by_matthias_is_still_answered(self):
        self.send(user=ME, channel="C333", text=self.ping, bot_id="B1", subtype="bot_message")
        self.assertTrue(wait_for(self.fake.posted))

    def test_ping_with_no_user_in_the_ping_channel_is_answered(self):
        self.send(channel=config.MATTHIAS_DAY_CHANNEL_ID, text=self.ping, bot_id="B1")
        self.assertTrue(wait_for(self.fake.posted))

    def test_someone_elses_ping_is_not_answered(self):
        self.send(user="UOTHER", channel="C333", text=self.ping)
        self.assertTrue(self.quiet())
        self.assertEqual(self.persisted, [])

    def test_ping_with_hash_prefix_is_ignored(self):
        self.send(user=ME, channel="C333", text="# " + self.ping)
        self.assertTrue(self.quiet())


class Failures(HandlerTestCase):
    def test_model_crash_posts_an_apology_instead_of_going_silent(self):
        with mock.patch.object(handlers, "ask_noodle", side_effect=RuntimeError("boom")):
            self.send(user=ME, channel=DM, channel_type="im")
            self.assertTrue(wait_for(self.fake.posted))
        self.assertIn("something went wrong", self.fake.posted()[0][1])

    def test_unreachable_model_gets_its_own_message(self):
        from openai import APIConnectionError
        err = APIConnectionError(request=mock.MagicMock())
        with mock.patch.object(handlers, "ask_noodle", side_effect=err):
            self.send(user=ME, channel=DM, channel_type="im")
            self.assertTrue(wait_for(self.fake.posted))
        self.assertIn("can't reach my brain", self.fake.posted()[0][1])

    def test_slack_api_error_while_posting_does_not_kill_the_handler(self):
        self.fake.api_overrides["chat.postMessage"] = {"ok": False, "error": "channel_not_found"}
        self.send(user=ME, channel=DM, channel_type="im")
        self.assertTrue(wait_for(lambda: len(self.fake.posted()) >= 2))  # the reply, then the apology


class Ordering(HandlerTestCase):
    def test_messages_in_one_conversation_are_handled_one_at_a_time(self):
        active, overlap = [0], [False]

        def slow(key, prompt, **kw):
            active[0] += 1
            overlap[0] = overlap[0] or active[0] > 1
            threading.Event().wait(0.1)
            active[0] -= 1
            return prompt

        with mock.patch.object(handlers, "ask_noodle", side_effect=slow):
            for i in range(3):
                self.send(user=ME, channel=DM, channel_type="im", text=f"m{i}", ts=f"1.{i}")
            self.assertTrue(wait_for(lambda: len(self.fake.posted()) == 3, timeout=5))
        self.assertFalse(overlap[0])


if __name__ == "__main__":
    unittest.main()
