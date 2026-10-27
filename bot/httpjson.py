import json
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "noodle-slack-bot/1.0 (matthiaslubbertsen@gmail.com)"


def get_json(url: str, params: dict | None = None, headers: dict | None = None, timeout: float = 10):
    """GET a json endpoint and return the parsed body. raises on any failure -
    callers decide how to degrade (every caller here is optional/best-effort)."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    for attempt in (1, 2):  # one retry: these are all flaky-network best-effort calls
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                # noaa sometimes serves its json padded with NUL bytes (seen live)
                return json.loads(resp.read().rstrip(b"\x00").decode("utf-8"))
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
