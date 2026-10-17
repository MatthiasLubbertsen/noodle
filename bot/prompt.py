import re

import config
from bot import features
from bot.tools import available_tools


def load_prompt() -> str:
    """the persona prompt without the parts about features that are off. those parts are
    wrapped in <!-- if:NAME --> ... <!-- endif:NAME --> in prompts/system_prompt.md (NAME is a
    feature from bot/features.py); the markers themselves never reach the model."""
    text = config.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")

    def section(m: re.Match) -> str:
        return m.group(2) if features.enabled(m.group(1)) else ""

    text = re.sub(r"<!-- if:(\w+) -->\n?(.*?)<!-- endif:\1 -->\n?", section, text, flags=re.DOTALL)
    # the "never write a tool's name" rule lists only the tools that exist right now
    return text.replace("{{TOOL_NAMES}}", ", ".join(t["function"]["name"] for t in available_tools()))
