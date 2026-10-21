"""which optional features are on, decided ONLY by whether their credentials are set.

no credential = the feature is off everywhere: its tools are not shown to the model
(tools.available_tools()), the part of the system prompt about them is removed
(prompt.load_prompt()), and the background jobs that need it don't run. nothing about a
missing key reaches the model, so it can't offer or promise something that isn't there.
"""
import config

# tool name -> the feature (credential) it needs. tools not listed here need nothing.
TOOL_FEATURE = {
    "run_code": "sandbox",
    "browser": "browser",
    "github": "github",
    "links": "links",
    "now_playing": "lastfm",
    "music_stats": "lastfm",
    "freedom": "freedom",
    "start_freedom_session": "freedom",
    "schedule_freedom_session": "freedom",
}


def enabled(feature: str) -> bool:
    if feature == "sandbox":
        # 16+ chars: the sandbox service refuses shorter tokens, so don't offer the tool for one
        return len(config.SANDBOX_TOKEN) >= 16 and bool(config.SANDBOX_URL)
    if feature == "browser":
        return len(config.BROWSER_TOKEN) >= 16 and bool(config.BROWSER_URL)
    if feature == "typing":
        return bool(config.SLACK_WEB_TOKEN and config.SLACK_WEB_COOKIE)
    if feature == "github":
        return bool(config.GITHUB_TOKEN)
    if feature == "lastfm":
        return bool(config.LASTFM_API_KEY and config.LASTFM_USER)
    if feature == "freedom":
        return bool(config.FREEDOM_API_KEY)
    if feature == "links":
        return bool(config.LINKS_API_KEY and config.LINKS_API_BASE)
    raise KeyError(feature)


def tool_enabled(tool_name: str) -> bool:
    feature = TOOL_FEATURE.get(tool_name)
    return feature is None or enabled(feature)
