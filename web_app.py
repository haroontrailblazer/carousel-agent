"""The deployed web console: SPA, JSON API, review pages and the ADK dev UI.

One ASGI app, one process, one Render service. The layout:

===================  ====================================================
``/``                the React console (static bundle, SPA history fallback)
``/api/...``         the console's JSON API and SSE stream (signed in)
``/review-api/...``  the Telegram Approve/Reject pages (deliberately open)
``/dev/...``         Google's ADK dev UI, for debugging a misbehaving run
``/healthz``         platform health probe
===================  ====================================================

**Why our app is the root and ADK is mounted.** ADK's app claims ``/``,
``/run``, ``/run_sse``, ``/list-apps``, ``/apps/*`` and ``/dev-ui/*`` at the top
level, so it cannot host an SPA underneath it. ``get_fast_api_app`` accepts a
``url_prefix``, which it writes into the dev UI's runtime config, so relocating
it to ``/dev`` is a supported move rather than a hack.

**Why the review API is included, not mounted.** Starlette does not run a
mounted sub-app's lifespan. Mounting ``review_api.main:app`` would silently skip
its shutdown drain, so a redeploy could cut a resume short with nothing
restoring the pending review. Including the router keeps one app with one
lifespan. The ADK app has to stay mounted (there is no router to extract), so
its lifespan is chained explicitly below - the thing Starlette declines to do.

**Deployment requirement.** Run uvicorn with ``--proxy-headers
--forwarded-allow-ips='*'``. ADK's origin check reads ``x-forwarded-proto`` and
``x-forwarded-host``; without those flags it computes an ``http://`` origin
behind Render's TLS terminator and 403s every dev-UI request.

Run locally::

    python -m uvicorn web_app:app --port 8000 --proxy-headers
"""

from __future__ import annotations

import logging
import os
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.cli.service_registry import get_service_registry
from starlette.types import ASGIApp

from app import runtime
from app.config import PROJECT_ROOT, settings
from app.observability import init_observability, shutdown_observability
from app.review.resume import drain_resume_tasks
from app.runs.recovery import reconcile_on_startup, release_stuck_queue_items
from app.runs.service import drain_run_tasks
from app.services import db
from review_api.routes import router as review_router
from web_api.auth import AuthMiddleware, build_verifier, validate_session_secret
from web_api.routes_auth import router as auth_router
from web_api.routes_runs import router as runs_router
from web_api.spa import SPAStaticFiles

logger = logging.getLogger(__name__)

#: Custom URI scheme the service factories are registered under. The URI itself
#: carries no configuration - the factories read app.config.settings - it only
#: has to parse as a URI and match the registered scheme.
SERVICE_SCHEME = "supabase"
SERVICE_URI = f"{SERVICE_SCHEME}://carousel"

#: Where the review pages live. Set REVIEW_API_BASE_URL to this service's public
#: URL plus this prefix, e.g. https://carousel.onrender.com/review-api - the
#: channel tools build "{base}/review/{run_id}/approve" on top of it.
REVIEW_API_MOUNT = "/review-api"

#: Where the ADK dev UI lives.
DEV_UI_MOUNT = "/dev"

#: The built SPA. Absent until `npm run build` has run; SPAStaticFiles then
#: serves an explanatory page instead of failing.
SPA_DIST = PROJECT_ROOT / "frontend" / "dist"


# ---------------------------------------------------------------------------
# Service wiring
# ---------------------------------------------------------------------------
def _register_services() -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Register custom service schemes; return the URIs to hand ADK.

    ``get_fast_api_app`` accepts service *URIs*, not instances, so the shared
    singletons in ``app.runtime`` are exposed through a registered scheme. That
    matters beyond tidiness: without it ADK would build a SECOND session
    service with its own connection pool, and runs started from the console
    would be invisible to the dev UI's session inspector.

    A service whose configuration is missing returns ``None`` so a
    partially-configured environment degrades to ADK's in-memory default with a
    warning, rather than failing to boot.
    """
    registry = get_service_registry()
    artifact_uri: Optional[str] = None
    memory_uri: Optional[str] = None
    session_uri: Optional[str] = None

    if settings.s3_access_key and settings.s3_secret_key and settings.s3_endpoint:
        registry.register_artifact_service(
            SERVICE_SCHEME, lambda uri, **_kw: runtime.artifact_service()
        )
        artifact_uri = SERVICE_URI
    else:
        logger.warning(
            "Supabase S3 settings missing - the console will use IN-MEMORY "
            "artifacts. Slides generated here will not persist and cannot be "
            "published."
        )

    if settings.database_url:
        registry.register_memory_service(
            SERVICE_SCHEME, lambda uri, **_kw: runtime.memory_service()
        )
        memory_uri = SERVICE_URI
        try:
            registry.register_session_service(
                SERVICE_SCHEME, lambda uri, **_kw: runtime.session_service()
            )
            session_uri = SERVICE_URI
        except Exception as exc:
            # Older ADK builds may not expose session registration; falling back
            # to the raw DSN still works, it just means a second pool.
            logger.warning(
                "Could not register the shared session service (%s); ADK will "
                "build its own from DATABASE_URL.",
                exc,
            )
            session_uri = settings.database_url
    else:
        logger.warning("DATABASE_URL missing - sessions and memory are in-process only.")

    return artifact_uri, memory_uri, session_uri


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
def _build_lifespan(adk_app: Any):
    """Root lifespan, chaining the mounted ADK app's own."""

    @asynccontextmanager
    async def lifespan(_root: FastAPI) -> AsyncIterator[None]:
        init_observability()

        for problem in validate_session_secret(settings.session_secret):
            logger.error("AUTH CONFIG: %s", problem)

        async with AsyncExitStack() as stack:
            # Starlette will not run a mounted app's lifespan, so do it here.
            # Without this ADK's own startup and shutdown hooks never fire.
            await stack.enter_async_context(
                adk_app.router.lifespan_context(adk_app)
            )

            if settings.database_url:
                # Order matters: reconcile first (which demotes killed runs out
                # of "running"), THEN release queue items, so an item is only
                # freed once nothing live still claims it.
                await reconcile_on_startup()
                await release_stuck_queue_items()
                try:
                    seeded = await db.seed_app_users(
                        list(settings.auth_bootstrap_emails)
                    )
                    if seeded:
                        logger.warning(
                            "Seeded %d bootstrap user(s) into an empty allowlist.",
                            seeded,
                        )
                except Exception as exc:
                    logger.warning("Could not seed the user allowlist: %s", exc)

            try:
                yield
            finally:
                # Not a drain to completion: a generate phase runs for minutes
                # and no platform grace period covers it, so waiting only
                # guarantees being SIGKILLed mid-write. Cancel instead - the
                # resume path restores its pending review and reconcile marks
                # the rest interrupted with a Resume button.
                await drain_resume_tasks(timeout=10.0)
                await drain_run_tasks(timeout=10.0)
                await runtime.close_services()
                try:
                    await db.close_pool()
                except Exception as exc:  # pragma: no cover
                    logger.warning("Closing the database pool failed: %s", exc)
                shutdown_observability()

    return lifespan


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
def build_app() -> ASGIApp:
    """Assemble the console, the API, the review pages and the dev UI."""
    artifact_uri, memory_uri, session_uri = _register_services()

    # agents_dir points AT the agent package, which puts ADK in single-agent
    # mode: the app name is pinned to "app" (matching settings.app_name, which
    # is derived from the same directory), and /list-apps stops walking the
    # repo - which would otherwise mean walking frontend/node_modules.
    adk_app = get_fast_api_app(
        agents_dir=str(PROJECT_ROOT / "app"),
        web=True,
        url_prefix=DEV_UI_MOUNT,
        session_service_uri=session_uri,
        artifact_service_uri=artifact_uri,
        memory_service_uri=memory_uri,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
    )

    root = FastAPI(
        title="Carousel Factory",
        lifespan=_build_lifespan(adk_app),
        docs_url=None,
        redoc_url=None,
    )

    @root.get("/healthz", include_in_schema=False)
    async def healthz() -> dict:
        """Liveness probe. Touches no dependencies, so it cannot restart-loop."""
        return {"status": "ok"}

    root.include_router(auth_router, prefix="/api/auth", tags=["auth"])
    root.include_router(runs_router, prefix="/api", tags=["runs"])
    root.include_router(review_router, prefix=REVIEW_API_MOUNT, tags=["review"])

    root.mount(DEV_UI_MOUNT, adk_app)
    # Registered LAST: it is the catch-all, and anything mounted after it would
    # never be reached.
    root.mount("/", SPAStaticFiles(directory=SPA_DIST), name="spa")

    logger.info(
        "Console assembled: SPA at /, API at /api, review at %s, dev UI at %s. "
        "Set REVIEW_API_BASE_URL to <public url>%s.",
        REVIEW_API_MOUNT,
        DEV_UI_MOUNT,
        REVIEW_API_MOUNT,
    )

    return AuthMiddleware(
        root,
        verifier=build_verifier(),
        secret=settings.session_secret,
        secure_cookies=_secure_cookies_default(),
    )


def _secure_cookies_default() -> bool:
    """Mark cookies Secure unless this is plainly a local dev run.

    A Secure cookie is silently dropped over plain HTTP, which would make local
    development look like a broken login with no error anywhere.
    """
    base = (settings.public_base_url or "").lower()
    if base.startswith("https://"):
        return True
    if base.startswith("http://"):
        return False
    return os.getenv("ENV", "").lower() in ("prod", "production")


app = build_app()


__all__ = ["DEV_UI_MOUNT", "REVIEW_API_MOUNT", "app", "build_app"]
