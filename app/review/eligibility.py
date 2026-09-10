"""The review UI and server use the run's own Instagram connection."""
import json
from app.config import settings
from app.review.resume import PIPELINE_USER_ID
from app.services import db, instagram_accounts


def account_message(state: dict) -> str:
    account_id = str(state.get("account_id") or "")
    if not account_id:
        return "This carousel was created for download. Start a new carousel with an Instagram account selected to approve and publish."
    account = instagram_accounts.get(account_id)
    if not account or not account.usable:
        return "Reconnect this carousel's Instagram account in Profile before approving or rejecting it."
    return ""


class ReviewNotReady(ValueError):
    pass


def validate_review_state(state: dict) -> None:
    if state.get("phase") != "review" or (state.get("qa_report") or {}).get("passed") is not True or not (state.get("bundle") or {}).get("ordered_artifacts"):
        raise ReviewNotReady("This carousel has not finished verification or is no longer waiting for review. Refresh its progress before deciding.")


async def review_account_message(run_id: str) -> str:
    pool = await db.get_pool()
    row = await pool.fetchrow(
        "SELECT state FROM sessions WHERE app_name = $1 AND user_id = $2 AND id = $3",
        settings.app_name, PIPELINE_USER_ID, str(run_id),
    )
    state = row["state"] if row else {}
    if isinstance(state, str):
        state = json.loads(state or "{}")
    state = dict(state or {})
    blocked = account_message(state)
    if blocked:
        return blocked
    validate_review_state(state)
    return ""
