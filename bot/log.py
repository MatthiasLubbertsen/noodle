import logging
import os
import re
import sys
import traceback

import config

# prefixes used by http-client libraries (httpx, httpcore) and the openai
# sdk for their own verbose per-request logs (method, url, status) at INFO -
# noisy and redundant with bot/netlog.py's concise "called hcai ..." /
# "called tool ..." lines. some environments register these under a
# suffixed name (e.g. "httpx2" instead of "httpx" - seen in production,
# likely from a proxy/wrapper creating its own named instance), so
# `logging.getLogger("httpx").setLevel(...)` alone misses it: that only
# configures the exact "httpx" logger object, not a differently-named one.
# a filter on the handler itself catches every record by prefix, regardless
# of which exact logger name it was issued under.
_NOISY_PREFIXES = ("httpx", "httpcore", "openai")

_APP_DIR = str(config.BASE_DIR)


class _QuietLibraryFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name.startswith(_NOISY_PREFIXES) and record.levelno < logging.WARNING:
            return False
        return True


# --- slack socket mode reconnects -------------------------------------------------------------
# slack closes the socket mode connection every few hours and bolt reconnects on its own. that is
# normal, but bolt logs it as 6-8 lines, and when the old socket died first, as the same ERROR every
# 10 seconds until it notices (real log: five "Failed to check the state of sock ... Broken pipe" in
# a row, then eight INFO lines). all of it becomes: one line when the socket is lost, one line when
# the new session is up. nothing is hidden that says something the kept lines don't.
_SOCKET_CHATTER = (
    "Connecting to a new endpoint",
    "Connected to a new endpoint",
    "The connection has been closed",
    "The session seems to be already closed",
    "Stopped receiving messages from a connection",
    "Starting to receive messages from a new connection",
)
_NEW_SESSION = re.compile(r"A new session has been established \(session id: ([^)\s]+)\)")
_SOCKET_ERROR = re.compile(
    r"(?:Failed to check the state of sock|on_error invoked) "
    r"\(session id: ([^,]+), error: ([^,]+), message: (.*)\)$",
    re.S,
)


class _SlackSocketFilter(logging.Filter):
    """shortens slack_bolt's socket mode reconnect chatter (see above). it sits on every handler, so
    one record passes through it more than once: the verdict is kept on the record."""

    def __init__(self) -> None:
        super().__init__()
        self._lost: set[str] = set()  # sessions whose loss was already logged

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.name.startswith("slack_bolt"):
            return True
        verdict = getattr(record, "_noodle_keep", None)
        if verdict is None:
            verdict = record._noodle_keep = self._shorten(record)
        return verdict

    def _shorten(self, record: logging.LogRecord) -> bool:
        try:
            text = record.getMessage()
        except Exception:  # noqa: BLE001 - never let a log filter break logging
            return True
        if text.startswith(_SOCKET_CHATTER):
            return False
        m = _NEW_SESSION.match(text)
        if m:
            self._lost.clear()
            record.msg, record.args = f"slack socket connected (session {m.group(1)[:8]})", None
            return True
        m = _SOCKET_ERROR.match(text)
        if m:
            session, error, message = m.groups()
            if session in self._lost:
                return False  # the same dead socket, reported again every 10 seconds
            self._lost.add(session)
            message = re.sub(r"^\[Errno -?\d+\] ", "", " ".join(message.split()))
            record.msg, record.args = f"slack socket lost ({error}: {message}), reconnecting", None
            record.levelno, record.levelname = logging.WARNING, "WARNING"  # bolt recovers on its own
        return True


# --- one-line errors --------------------------------------------------------------------------
def _raise_site(tb) -> str:
    """'file.py:123 in func' for where it went wrong: the deepest frame in noodle's own code if
    there is one (a line in bot/ says more than one inside a library), else the deepest frame."""
    frames = traceback.extract_tb(tb)
    if not frames:
        return "unknown location"
    own = [f for f in frames if f.filename.startswith(_APP_DIR) and "site-packages" not in f.filename]
    frame = (own or frames)[-1]
    return f"{os.path.basename(frame.filename)}:{frame.lineno} in {frame.name}"


class _CompactFormatter(logging.Formatter):
    """one line per record, even for failures.

    the stock formatter appends the whole python traceback to every logger.exception() call (20-30
    lines for one dropped websocket or one 502 from the ai endpoint), which buries the lines around
    it. a failure is now one line: the message, then `[ExceptionType: text] at file.py:123 in func`.
    LOG_LEVEL=DEBUG brings the full traceback back for when it is actually needed.
    """

    def __init__(self, fmt: str, full_tracebacks: bool) -> None:
        super().__init__(fmt)
        self.full_tracebacks = full_tracebacks

    def format(self, record: logging.LogRecord) -> str:
        exc_info, exc_text, stack_info = record.exc_info, record.exc_text, record.stack_info
        record.exc_info = record.exc_text = record.stack_info = None
        try:
            line = super().format(record)
        finally:
            record.exc_info, record.exc_text, record.stack_info = exc_info, exc_text, stack_info
        if exc_info and exc_info[0] is not None:
            if self.full_tracebacks:
                return line + "\n" + "".join(traceback.format_exception(*exc_info)).rstrip()
            exc_type, exc_value, tb = exc_info
            message = " ".join(str(exc_value).split())
            if len(message) > 300:
                message = message[:300] + "..."
            detail = f"{exc_type.__name__}: {message}" if message else exc_type.__name__
            if detail not in line:  # some callers already put the error text in their message
                line += f"  [{detail}]"
            line += f" at {_raise_site(tb)}"
        return line.replace("\n", " | ")


def setup_logging() -> None:
    """configure the noodle logger: console + a resilient file handler."""
    config.LOG_DIR.mkdir(exist_ok=True)
    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    formatter = _CompactFormatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        full_tracebacks=level <= logging.DEBUG,
    )
    handlers = [logging.StreamHandler()]
    try:
        handlers.append(
            logging.FileHandler(config.LOG_DIR / "noodle.log", encoding="utf-8")
        )
    except OSError as exc:
        # logging isn't configured yet, so this goes straight to stderr
        print(f"warning: cannot write log file, logging to console only: {exc}", file=sys.stderr)
    quiet_filter = _QuietLibraryFilter()
    socket_filter = _SlackSocketFilter()
    for handler in handlers:
        handler.addFilter(quiet_filter)
        handler.addFilter(socket_filter)
        handler.setFormatter(formatter)
    logging.basicConfig(level=level, handlers=handlers)
    # belt-and-suspenders for the common exact names, in case something logs
    # through them at WARNING or above with content we still don't want.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
