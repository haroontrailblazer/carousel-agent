"""Deliver the finished carousel files without creating a human review."""

from __future__ import annotations

import asyncio

from google.adk.tools import ToolContext

from app.agents.review_dispatcher import _materialize_artifact
from app.config import settings
from app.runs import cancellation
from app.schemas import Bundle
from app.state import K_BUNDLE, K_PUBLISH_RESULT, K_RUN_ID, get_model
from app.tools import telegram_tools


async def deliver_carousel(tool_context: ToolContext) -> dict:
    """Load every final artifact, then send originals and the caption to Telegram.

    The orchestrator persists the returned receipt before completing the run.
    A missing artifact or failed send raises so an incomplete delivery is never
    reported as successful. This path has no publishing credentials or verdict.
    """
    state = tool_context.state
    existing = state.get(K_PUBLISH_RESULT) or {}
    if existing.get("status") == "delivered":
        return existing
    run_id = str(state.get(K_RUN_ID) or "")
    bundle = get_model(state, K_BUNDLE, Bundle)
    if not run_id or bundle is None or not bundle.ordered_artifacts:
        raise ValueError("A finished carousel bundle is required for Telegram delivery.")
    paths: list[str] = []
    for index, filename in enumerate(bundle.ordered_artifacts):
        cancellation.raise_if_cancelled(run_id)
        path = await _materialize_artifact(
            tool_context, filename,
            settings.workdir / "telegram_delivery" / run_id / str(index),
        )
        if not path:
            raise ValueError(f"Could not load finished artifact: {filename}")
        paths.append(path)
    result = await asyncio.to_thread(
        telegram_tools.send_completed_carousel,
        run_id, bundle.cover.title, bundle.caption, paths,
        should_continue=lambda: not cancellation.is_requested(run_id),
    )
    return {"status": "delivered", "destination": "telegram", **result}
