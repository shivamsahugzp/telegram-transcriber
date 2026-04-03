"""Access control — allowlist-based with owner approval flow."""
import os
import logging

logger = logging.getLogger(__name__)

# Loaded once at startup from env vars
_approved: set[int] = set()
_pending: dict[int, dict] = {}  # user_id -> {name, username}


def _owner_id() -> int | None:
    val = os.environ.get("OWNER_TELEGRAM_ID", "").strip()
    return int(val) if val.isdigit() else None


def _load_allowed_from_env() -> set[int]:
    """Parse ALLOWED_USERS env var: comma-separated list of Telegram user IDs."""
    raw = os.environ.get("ALLOWED_USERS", "")
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


def is_configured() -> bool:
    """Returns False if OWNER_TELEGRAM_ID is not set — auth is disabled in this case."""
    return bool(os.environ.get("OWNER_TELEGRAM_ID", "").strip())


def init() -> None:
    """Call once on startup to load persisted approvals."""
    global _approved
    _approved = _load_allowed_from_env()
    owner = _owner_id()
    if owner:
        _approved.add(owner)
    if not is_configured():
        logger.warning("OWNER_TELEGRAM_ID not set — auth disabled, all users allowed")
    logger.info("Auth initialised — %d approved user(s)", len(_approved))


def is_approved(user_id: int) -> bool:
    return user_id in _approved


def is_owner(user_id: int) -> bool:
    return user_id == _owner_id()


def add_pending(user_id: int, name: str, username: str | None) -> None:
    _pending[user_id] = {"name": name, "username": username}


def is_pending(user_id: int) -> bool:
    return user_id in _pending


def approve(user_id: int) -> bool:
    """Approve a user. Returns True if they were pending, False if unknown."""
    _approved.add(user_id)
    was_pending = user_id in _pending
    _pending.pop(user_id, None)
    return was_pending


def deny(user_id: int) -> bool:
    was_pending = user_id in _pending
    _pending.pop(user_id, None)
    return was_pending


def pending_info(user_id: int) -> dict | None:
    return _pending.get(user_id)


def approved_ids() -> set[int]:
    return set(_approved)
