"""
state.py — Persists seen tweet IDs to a JSON file to avoid re-sending
"""
import json
import logging
from pathlib import Path

from config import STATE_FILE

logger = logging.getLogger(__name__)

# Maximum number of stored IDs (prevents the file from growing indefinitely)
MAX_SEEN = 5_000


def load_seen_ids() -> set[str]:
    """Loads the set of already-seen tweet IDs from disk."""
    if not STATE_FILE.exists():
        logger.info("State file not found, starting fresh: %s", STATE_FILE)
        return set()
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        ids = set(data.get("seen_ids", []))
        logger.info("Loaded %d known tweet IDs", len(ids))
        return ids
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load state: %s — resetting", exc)
        return set()


def save_seen_ids(seen_ids: set[str]) -> None:
    """Saves the set of IDs to disk. Enforces the MAX_SEEN limit."""
    # If too many — keep only the newest (sort numerically)
    ids_list = sorted(seen_ids, key=lambda x: int(x) if x.isdigit() else 0, reverse=True)
    if len(ids_list) > MAX_SEEN:
        ids_list = ids_list[:MAX_SEEN]

    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps({"seen_ids": ids_list}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.debug("Saved %d IDs to %s", len(ids_list), STATE_FILE)
    except OSError as exc:
        logger.error("Failed to save state: %s", exc)


def add_seen_ids(seen_ids: set[str], new_ids: set[str]) -> set[str]:
    """Adds new IDs to the set, saves to disk, and returns the updated set."""
    updated = seen_ids | new_ids
    save_seen_ids(updated)
    return updated
