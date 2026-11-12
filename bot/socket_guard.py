import json
import logging

logger = logging.getLogger("noodle")

# slack replaces the socket mode connection every few hours. slack_sdk's built-in client reads the
# socket 1024 bytes at a time, and when the old connection is closed while a longer message is only
# partly in, it hands that partial text on as if it were a whole message (builtin/internals.py,
# _fetch_messages: "no more bytes" appends the unfinished message). the json parse then fails and
# bolt logs "Failed to process a message: Unterminated string starting at: line 1 column 1012" with
# a traceback: once per reconnect, 65 times in a month of real logs, always in the same millisecond
# as the reconnect and always cut just under 1024 characters.
#
# the other half of that message is gone with the connection, no client can recover it, so the only
# sane thing is to drop it. slack was never sent an acknowledgement for it.
#
# why not bolt's websocket-client adapter instead: tried against a local fake slack. it has no json
# error, but it closes the old connection BEFORE the new one is up (a gap with no connection),
# reconnects twice per refresh, and failed to acknowledge an event during the swap (an event slack
# may then send again = a possible double reply). the built-in client opens the new connection first.


def drop_half_messages(client) -> None:
    """make a slack_sdk socket mode client ignore incoming text that is not complete json."""
    enqueue = client.enqueue_message

    def enqueue_complete(message: str) -> None:
        # only "{..." is ever parsed by the client (see BaseSocketModeClient.process_message)
        if isinstance(message, str) and message.startswith("{"):
            try:
                json.loads(message)
            except ValueError:
                logger.info(
                    "slack socket: dropped a half-received message (%d chars) from a closing connection",
                    len(message),
                )
                return
        enqueue(message)

    client.enqueue_message = enqueue_complete
