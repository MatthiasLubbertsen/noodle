import logging

logger = logging.getLogger("noodle")


def log_hcai(endpoint: str, result: str) -> None:
    """one line per outbound call to a hack club ai proxy endpoint."""
    logger.info("called hcai %s: %s", endpoint, result)


def log_tool(name: str, detail: str, result: str) -> None:
    """one line per tool call the model makes, summarizing input + outcome."""
    if detail:
        logger.info("called tool %s: %s: %s", name, detail, result)
    else:
        logger.info("called tool %s: %s", name, result)
