"""Deployable ADK web UI - the hosted equivalent of running ``adk web``.

``adk web`` is a CLI dev server; this is the same thing as an importable ASGI
app so Render (or any container host) can serve it. It gives you the agent
graph, the live event stream with the orchestrator's ``[phase] generate -> qa``
heartbeat lines, and the session-state inspector.

Two things this does that a bare ``adk web`` deployment would not:

1. **Wires the project's real services.** ``get_fast_api_app`` only accepts
   service *URIs*, not instances, so a naive deployment would silently fall
   back to in-memory artifacts - runs would generate slides that vanish, and
   the review previews and Instagram publish would both fail. ADK's service
   registry accepts custom URI schemes, so the Supabase artifact store and the
   Postgres memory service are registered under a ``supabase://`` scheme before
   the app is built.

2. **Puts a lock on the door.** ADK's web UI has no authentication of its own:
   anyone who reaches it can run agents (spending real OpenAI credits), read
   every session's full state, and watch the event stream. Basic auth is
   applied whenever ``ADK_WEB_USER``/``ADK_WEB_PASSWORD`` are set, and the app
   logs a loud warning when they are not.

Run locally::

    python -m uvicorn web_app:app --port 8000
"""

from __future__ import annotations

import base64
import logging
import os
import secrets as pysecrets
from pathlib import Path
from typing import Any, Optional

from google.adk.cli.fast_api import get_fast_api_app
from google.adk.cli.service_registry import get_service_registry
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import PROJECT_ROOT, settings
from app.observability import init_observability
from app.services.artifact_service import SupabaseArtifactService
from app.services.memory_service import PostgresMemoryService

logger = logging.getLogger(__name__)

#: Custom URI scheme the factories below are registered under. The URI itself
#: carries no configuration - both services read app.config.settings - it only
#: has to parse as a URI and match the registered scheme.
SERVICE_SCHEME = "supabase"
SERVICE_URI = f"{SERVICE_SCHEME}://carousel"

#: Paths that must stay reachable without credentials, or the platform's health
#: probe would fail and Render would restart the service forever.
_OPEN_PATHS = ("/healthz", "/health")


# ---------------------------------------------------------------------------
# Service wiring
# ---------------------------------------------------------------------------
def _artifact_factory(uri: str, **_kwargs: Any) -> SupabaseArtifactService:
    """Build the project's Supabase-backed artifact store."""
    return SupabaseArtifactService()


def _memory_factory(uri: str, **_kwargs: Any) -> PostgresMemoryService:
    """Build the project's Postgres-backed memory service."""
    return PostgresMemoryService()


def _register_services() -> tuple[Optional[str], Optional[str]]:
    """Register the custom schemes; return the URIs to hand ADK.

    Returns ``(None, None)`` for a service whose configuration is missing, so a
    partially-configured environment degrades to ADK's in-memory default with a
    warning rather than failing to boot.
    """
    registry = get_service_registry()
    artifact_uri: Optional[str] = None
    memory_uri: Optional[str] = None

    if settings.s3_access_key and settings.s3_secret_key and settings.s3_endpoint:
        registry.register_artifact_service(SERVICE_SCHEME, _artifact_factory)
        artifact_uri = SERVICE_URI
    else:
        logger.warning(
            "Supabase S3 settings missing - the web UI will use IN-MEMORY "
            "artifacts. Slides generated here will not persist and cannot be "
            "published."
        )

    if settings.database_url:
        registry.register_memory_service(SERVICE_SCHEME, _memory_factory)
        memory_uri = SERVICE_URI
    else:
        logger.warning("DATABASE_URL missing - memory service falls back to in-memory.")

    return artifact_uri, memory_uri


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class BasicAuthMiddleware:
    """Pure-ASGI HTTP basic auth.

    Written as raw ASGI rather than BaseHTTPMiddleware because the ADK web UI
    streams agent events; BaseHTTPMiddleware buffers response bodies and would
    turn the live event stream into a page that only appears when a run
    finishes.
    """

    def __init__(self, app: ASGIApp, username: str, password: str) -> None:
        self.app = app
        self._expected = base64.b64encode(
            f"{username}:{password}".encode("utf-8")
        ).decode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in _OPEN_PATHS:
            await self.app(scope, receive, send)
            return

        header = ""
        for key, value in scope.get("headers") or []:
            if key == b"authorization":
                header = value.decode("latin-1")
                break

        supplied = header[6:].strip() if header.lower().startswith("basic ") else ""
        # compare_digest to keep the check constant-time.
        if supplied and pysecrets.compare_digest(supplied, self._expected):
            await self.app(scope, receive, send)
            return

        response = PlainTextResponse(
            "Authentication required.",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Carousel Factory"'},
        )
        await response(scope, receive, send)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
def build_app() -> Any:
    """Build the ADK web app with this project's services and auth."""
    init_observability()
    artifact_uri, memory_uri = _register_services()

    # agents_dir is the repo root: ADK discovers the `app/` package through its
    # module-level `root_agent`, exactly as `adk web` does from here.
    adk_app = get_fast_api_app(
        agents_dir=str(PROJECT_ROOT),
        web=True,
        session_service_uri=settings.database_url or None,
        artifact_service_uri=artifact_uri,
        memory_service_uri=memory_uri,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
    )

    # ADK's app has no health route, so a platform health check on /healthz
    # would 404 and restart-loop the service. Added here, and listed in
    # _OPEN_PATHS so the probe is not asked for credentials.
    @adk_app.get("/healthz", include_in_schema=False)
    async def _healthz() -> dict:
        return {"status": "ok"}

    username = os.getenv("ADK_WEB_USER", "")
    password = os.getenv("ADK_WEB_PASSWORD", "")
    if username and password:
        logger.info("ADK web UI protected by basic auth (user %r).", username)
        return BasicAuthMiddleware(adk_app, username, password)

    logger.warning(
        "ADK_WEB_USER/ADK_WEB_PASSWORD are not set: the ADK web UI is being "
        "served WITHOUT AUTHENTICATION. Anyone who reaches this URL can run "
        "agents, spend API credits and read every session's state."
    )
    return adk_app


app = build_app()


__all__ = ["BasicAuthMiddleware", "app", "build_app"]
