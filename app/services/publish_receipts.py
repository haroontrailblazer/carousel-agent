"""Durable publish receipts, independent of the agent event commit boundary."""
from datetime import datetime, timezone
from uuid import uuid4
from app.services import db


def key(run_id: str) -> str:
    if not run_id:
        raise ValueError("A run ID is required before publishing")
    return "publish_receipt:" + run_id


async def claim(run_id: str, account_id: str) -> tuple[bool, dict]:
    attempt = uuid4().hex
    def reserve(current):
        if current.get("status") in ("publishing", "uncertain", "published") or current.get("media_id"):
            return current
        return {"status": "publishing", "attempt_id": attempt, "account_id": account_id,
                "retryable": False, "started_at": datetime.now(timezone.utc).isoformat(),
                "message": "A publish attempt is in progress or was interrupted. Check Instagram before any further publishing; automatic retries are blocked."}
    receipt = await db.update_config(key(run_id), reserve)
    return receipt.get("attempt_id") == attempt, receipt


async def finish(run_id: str, receipt: dict, result: dict) -> dict:
    updated = {**receipt, **result}
    await db.set_config(key(run_id), updated)
    return updated
