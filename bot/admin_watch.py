import json
import logging

import config
from bot import state
from bot.oneshot import complete, remember_facts

logger = logging.getLogger("noodle")

# matthias's workspace role, polled with users.info on noodle's own token (users:read, which
# noodle already needs for display names). a role going UP (member -> admin, admin -> owner...)
# is a rare, one-off event, so the dm skips the proactive guardrails (quiet hours, daily cap).
RANKS = {"member": 0, "admin": 1, "owner": 2, "primary_owner": 3}


def _role(user: dict) -> str:
    if user.get("is_primary_owner"):
        return "primary_owner"
    if user.get("is_owner"):
        return "owner"
    if user.get("is_admin"):
        return "admin"
    return "member"


def current_role() -> str:
    info = state.app.client.users_info(user=config.USER_ID)
    return _role(info["user"])


def _load() -> str | None:
    """the role last seen, or None on the very first run (no state yet)."""
    try:
        role = json.loads(config.ADMIN_SEEN_PATH.read_text(encoding="utf-8")).get("role")
    except FileNotFoundError:
        return None
    except Exception:
        logger.exception("failed to load admin seen-state, starting fresh")
        return None
    return role if role in RANKS else None


def _save(role: str) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.ADMIN_SEEN_PATH.write_text(json.dumps({"role": role}), encoding="utf-8")


def check() -> tuple[str, str] | None:
    """(old role, new role) if his role went up since the last check, else None. the new
    role is saved first (like the other watchers), so a crash never sends it twice. the very
    first run only baselines; a demotion is recorded silently."""
    role = current_role()
    seen = _load()
    if seen == role:
        return None
    _save(role)
    if seen is None:
        logger.info("admin watcher: baselined matthias as %s", role)
        return None
    if RANKS[role] > RANKS[seen]:
        return seen, role
    logger.info("admin watcher: matthias went from %s to %s", seen, role)
    return None


def compose_message(old: str, new: str) -> str:
    label = new.replace("_", " ")
    facts = f"matthias's slack workspace role just changed from {old.replace('_', ' ')} to {label}"
    remember_facts("slack role change", facts)
    text = complete(
        f"matthias was just promoted to workspace {label} on slack. tell him in your own "
        "voice: one or two short sentences, impressed but teasing, with a nudge about "
        "great power. use ONLY these facts.\n\nfacts:\n" + facts,
        max_tokens=100,
    )
    return text or f"you just got promoted to {label} on slack. try not to ban anyone."
