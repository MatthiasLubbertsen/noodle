import logging

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


def setup_logging() -> None:
    """configure the noodle logger: console + a resilient file handler."""
    config.LOG_DIR.mkdir(exist_ok=True)
    handlers = [logging.StreamHandler()]
    try:
        handlers.append(
            logging.FileHandler(config.LOG_DIR / "noodle.log", encoding="utf-8")
        )
    except OSError as exc:
        print(f"warning: cannot write log file, logging to console only: {exc}")
    quiet_filter = _QuietLibraryFilter()
    for handler in handlers:
        handler.addFilter(quiet_filter)
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    # belt-and-suspenders for the common exact names, in case something logs
    # through them at WARNING or above with content we still don't want.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
