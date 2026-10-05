"""The cover hooks this workspace's reviewer chose, shown to the planner.

Twelve rounds of rules about what a good hook is kept missing what the client
actually approves. The reviewer's own choices are the ground truth: every hook
picked from the drafted alternatives or typed on the review screen is kept
here, and the planner sees the most recent ones as examples to match.
Stored like the learned rules: one workspace-scoped config value.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.services import db

logger = logging.getLogger(__name__)

CONFIG_KEY = "hook_examples"
MAX_KEPT = 30
MAX_SHOWN = 12


async def record(hook: str, highlight: str, story: str, source: str) -> None:
    """Remember a hook the reviewer chose (source: 'picked' or 'typed').

    Best effort: a failed write must never undo the cover change itself.
    """
    hook = " ".join((hook or "").split())
    if not hook:
        return
    entry = {
        "hook": hook,
        "highlight": " ".join((highlight or "").split()),
        "story": " ".join((story or "").split())[:160],
        "source": source,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    def update(previous):
        items = [e for e in (previous or {}).get("items", []) if isinstance(e, dict)]
        items = [e for e in items if " ".join(str(e.get("hook", "")).split()).upper() != hook.upper()]
        return {"items": (items + [entry])[-MAX_KEPT:]}

    try:
        await db.update_config(CONFIG_KEY, update)
    except Exception as exc:  # noqa: BLE001 - a lost example is not worth an error
        logger.warning("Could not save the chosen hook as an example: %s", exc)


async def load() -> list[dict]:
    try:
        value = await db.get_config(CONFIG_KEY, {})
    except Exception as exc:  # noqa: BLE001 - examples are optional context
        logger.warning("Could not read hook examples: %s", exc)
        return []
    return [e for e in (value or {}).get("items", []) if isinstance(e, dict) and e.get("hook")]


def prompt_note(items: list[dict]) -> str:
    """The planner's view: the newest choices first, as plain lines."""
    if not items:
        return ""
    lines = []
    for entry in reversed(items[-MAX_SHOWN:]):
        how = "wrote" if entry.get("source") == "typed" else "picked"
        story = f" (story: {entry['story']})" if entry.get("story") else ""
        lines.append(f"- {entry['hook']}  [reviewer {how}]{story}")
    return (
        "Hooks this account's reviewer chose for earlier carousels, newest first. "
        "They are the best evidence of what gets approved: match their voice, length "
        "and shape, and draft at least one candidate the way these are built. Never "
        "copy their facts into this story.\n" + "\n".join(lines)
    )
