"""Standalone ASGI app for the review pages.

The routes and their HTML live in :mod:`review_api.routes`; the decision logic
lives in :mod:`app.review`. What is left here is a thin FastAPI wrapper so the
review surface can still be run on its own::

    python -m uvicorn review_api.main:app --port 8080

In the deployed web console this module is NOT used. ``web_app.py`` includes
``review_api.routes.router`` directly instead of mounting this app, because
Starlette does not run a mounted sub-app's lifespan - mounting it would silently
skip the resume drain below and let a redeploy cut a resume short. Including the
router keeps one app with one lifespan.

The Review Dispatcher pauses each run on a ``LongRunningFunctionTool``
(``await_human_review``) after sending Approve/Reject links that point at these
routes. Resume addressing (must match ``fetcher.fetch_news``):
``app_name = settings.app_name``, ``user_id = PIPELINE_USER_ID``,
``session_id = run_id`` - the stored ``pending_reviews.session_id`` is
authoritative.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.observability import init_observability, shutdown_observability
from app.review.resume import drain_resume_tasks
from app.services import db
from review_api.routes import router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Tracing at startup; drain in-flight resumes and close the pool at exit."""
    init_observability()
    yield
    await drain_resume_tasks(timeout=30.0)
    try:
        await db.close_pool()
    except Exception as exc:  # pragma: no cover - shutdown best effort
        logger.warning("Closing the database pool failed: %s", exc)
    shutdown_observability()


app = FastAPI(
    title="Carousel Factory - Review API",
    description="Approve/reject pages that resume a paused pipeline run.",
    lifespan=_lifespan,
)
app.include_router(router)


def main() -> None:
    """Run the review API with uvicorn (reads $PORT; default 8080)."""
    import uvicorn

    uvicorn.run(
        "review_api.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
    )


if __name__ == "__main__":
    main()


__all__ = ["app", "main"]
