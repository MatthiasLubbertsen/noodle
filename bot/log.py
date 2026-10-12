import logging

import config


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
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    # these libraries log their own verbose per-request lines (method, url,
    # status) at INFO - noisy and redundant with bot/netlog.py's concise
    # "called hcai ..." / "called tool ..." lines, so quiet them down.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
