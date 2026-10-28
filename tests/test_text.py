import logging
import types
import unittest
from unittest import mock

import config
from bot import slack_text, state
from bot.chunk import chunk_response


class Chunking(unittest.TestCase):
    def test_one_message_per_paragraph(self):
        self.assertEqual(chunk_response("a\n\nb\n  \nc"), ["a", "b", "c"])

    def test_never_cuts_a_long_paragraph(self):
        long = "word " * 5000
        self.assertEqual(chunk_response(long), [long.strip()])

    def test_single_newlines_stay_inside_one_message(self):
        self.assertEqual(chunk_response("line1\nline2"), ["line1\nline2"])

    def test_empty_reply_is_one_empty_message_not_zero(self):
        self.assertEqual(chunk_response("  "), [""])


class CleanReply(unittest.TestCase):
    clean = staticmethod(slack_text._clean_reply)

    def test_reasoning_is_never_forwarded(self):
        self.assertEqual(self.clean("<think>secret\nplan</think>hi"), "hi")
        self.assertEqual(self.clean("a<reasoning>x</reasoning>b"), "ab")
        self.assertEqual(self.clean(None), "")

    def test_em_dashes(self):
        self.assertEqual(self.clean("open 9–15"), "open 9-15")
        self.assertEqual(self.clean("well — maybe"), "well, maybe")
        self.assertEqual(self.clean("trailing —\nnext"), "trailing\nnext")
        self.assertEqual(self.clean("a\n\n— b"), "a\n\nb")

    def test_bare_urls_become_slack_links_without_the_scheme(self):
        self.assertEqual(self.clean("see https://go.example.com/x/ ok"),
                         "see <https://go.example.com/x/|go.example.com/x> ok")

    def test_trailing_punctuation_stays_outside_the_link(self):
        self.assertEqual(self.clean("try https://a.dev/b."), "try <https://a.dev/b|a.dev/b>.")

    def test_existing_labels_and_code_spans_are_kept(self):
        self.assertEqual(self.clean("<https://a.dev|docs>"), "<https://a.dev|docs>")
        self.assertEqual(self.clean("run `curl https://a.dev`"), "run `curl https://a.dev`")

    def test_space_before_punctuation_after_a_link_is_closed(self):
        self.assertEqual(self.clean("<https://a.dev|a.dev> . ok"), "<https://a.dev|a.dev>. ok")


class IncomingText(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(state, "MENTION_IDS", {"UNOODLE"})
        p.start()
        self.addCleanup(p.stop)

    def test_own_mention_is_dropped_others_are_kept_verbatim(self):
        self.assertEqual(slack_text._clean_text("<@UNOODLE> tell <@U123|bob> about <#C1|hq>"),
                         "tell <@U123|bob> about <#C1|hq>")

    def test_dm_detection(self):
        self.assertTrue(slack_text._is_dm({"channel_type": "im"}))
        self.assertTrue(slack_text._is_dm({"channel": "D123"}))
        self.assertFalse(slack_text._is_dm({"channel": "C123", "channel_type": "channel"}))

    def test_matthias_day_ping_only_counts_at_the_start(self):
        ping = slack_text._matthias_day_pinged
        self.assertTrue(ping(f"<!subteam^{config.MATTHIAS_DAY_GROUP_ID}> today:"))
        self.assertTrue(ping("  @matthias-day today"))
        self.assertTrue(ping("@… today i did"))
        self.assertFalse(ping("can i copy ur matthias-day idea"))
        self.assertFalse(ping("@… hello"))
        self.assertFalse(ping(""))


class SlackRefs(unittest.TestCase):
    parse = staticmethod(slack_text._parse_slack_ref)

    def test_permalink(self):
        self.assertEqual(self.parse("https://x.slack.com/archives/C0ABC/p1700000000123456"),
                         ("C0ABC", "1700000000.123456"))

    def test_other_forms(self):
        self.assertEqual(self.parse("<#C0ABC|hq>"), ("C0ABC", None))
        self.assertEqual(self.parse("C0ABC"), ("C0ABC", None))
        self.assertEqual(self.parse("C0ABC:1700.5"), ("C0ABC", "1700.5"))
        self.assertEqual(self.parse("?thread_ts=1700.5&cid=C0ABC"), ("C0ABC", "1700.5"))
        self.assertEqual(self.parse("https://x.slack.com/archives/C0ABC"), ("C0ABC", None))
        self.assertEqual(self.parse("nonsense"), (None, None))


class ChannelLink(unittest.TestCase):
    def client(self, **info):
        c = mock.MagicMock()
        c.conversations_info.return_value = info
        state.app = types.SimpleNamespace(client=c)

    def test_uses_slacks_own_name(self):
        self.client(ok=True, channel={"name": "hq"})
        self.assertEqual(slack_text._channel_link("C1"), "<#C1|hq>")

    def test_dms_are_just_dm(self):
        self.client(ok=True, channel={"is_im": True})
        self.assertEqual(slack_text._channel_link("D1"), "dm")

    def test_falls_back_to_the_directory_when_slack_fails(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        # regression: this used to call a directory function that didn't exist
        state.app = types.SimpleNamespace(client=mock.MagicMock(
            conversations_info=mock.MagicMock(side_effect=RuntimeError("down"))))
        with mock.patch("bot.directory._get_json", return_value={"data": {"channel": {"name": "hq"}}}):
            self.assertEqual(slack_text._channel_link("C1"), "<#C1|hq>")

    def test_plain_id_when_nothing_knows_the_channel(self):
        self.client(ok=False)
        with mock.patch("bot.directory._get_json", return_value=None):
            self.assertEqual(slack_text._channel_link("C1"), "<#C1>")


if __name__ == "__main__":
    unittest.main()
