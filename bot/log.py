import logging
import os
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


class _QuietLibraryFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name.startswith(_NOISY_PREFIXES) and record.levelno < logging.WARNING:
            return False
        return True


def _raise_site(tb) -> str:
    """'file.py:123 in func' for the innermost frame of a traceback."""
    frames = traceback.extract_tb(tb)
    if not frames:
        return "unknown location"
    last = frames[-1]
    return f"{os.path.basename(last.filename)}:{last.lineno} in {last.name}"


class _CompactFormatter(logging.Formatter):
    """one line per record, even for failures.

    the stock formatter appends the full python traceback to every
    logger.exception() call, which buries the actual story. by default a
    failure becomes one line: the message, then `[ExceptionType: text] at
    file.py:123 in func`. set LOG_LEVEL=DEBUG to get the full traceback back
    when you actually need it.
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
                line += "\n" + "".join(traceback.format_exception(*exc_info)).rstrip()
            else:
                exc_type, exc_value, tb = exc_info
                message = str(exc_value).strip().splitlines()[0] if str(exc_value).strip() else ""
                detail = f"{exc_type.__name__}: {message}" if message else exc_type.__name__
                line += f"  [{detail}] at {_raise_site(tb)}"
        return line


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
    for handler in handlers:
        handler.addFilter(quiet_filter)
        handler.setFormatter(formatter)
    logging.basicConfig(level=level, handlers=handlers)
    # slack_bolt logs every socket reconnect at INFO ("Connecting to a new
    # endpoint...", "A new session has been established"): a few per hour is
    # normal and just buries the lines that matter. warnings and errors still show.
    logging.getLogger("slack_bolt").setLevel(logging.WARNING)
    # belt-and-suspenders for the common exact names, in case something logs
    # through them at WARNING or above with content we still don't want.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
