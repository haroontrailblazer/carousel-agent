"""Shared ADK services; database persistence uses Supabase HTTPS only.

Agent trees are rebuilt independently so edited agent rules take effect.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from google.adk.artifacts import BaseArtifactService
from google.adk.memory import BaseMemoryService
from google.adk.sessions import BaseSessionService

from app.config import settings
from app.services.artifact_service import SupabaseArtifactService
from app.services.memory_service import PostgresMemoryService

logger = logging.getLogger(__name__)

# Built lazily on first use so importing this module performs no I/O and needs
# no configuration - the CLI, the tests and the web app all import it freely.
_lock = threading.Lock()
_session_service: Optional[BaseSessionService] = None
_artifact_service: Optional[BaseArtifactService] = None
_memory_service: Optional[BaseMemoryService] = None


def _build_session_service() -> BaseSessionService:
    """Persist sessions through the same Supabase HTTPS API as app data."""
    if settings.supabase_url and settings.supabase_storage_key:
        from app.services.session_service import SupabaseSessionService
        return SupabaseSessionService()
    logger.warning("Supabase server settings missing; using temporary in-memory sessions.")
    from google.adk.sessions import InMemorySessionService
    return InMemorySessionService()


def _build_artifact_service() -> BaseArtifactService:
    """Prefer native Supabase Storage; retain existing S3 installations."""
    native = bool(settings.supabase_storage_key)
    legacy = settings.s3_endpoint and settings.s3_access_key and settings.s3_secret_key
    if native or legacy:
        try:
            from app.services.tenant_storage import TenantStorageClient
            service = SupabaseArtifactService()
            service._client = TenantStorageClient(service._client)
            return service
        except Exception as exc:
            if native:
                raise RuntimeError(
                    "Native Supabase Storage configuration is invalid; check "
                    "SUPABASE_URL and the server-only Storage key."
                ) from exc
            logger.warning(
                "SupabaseArtifactService unavailable (%s); falling back to "
                "InMemoryArtifactService.",
                exc,
            )
    else:
        logger.warning(
            "Supabase Storage settings missing (SUPABASE_URL and "
            "SUPABASE_SECRET_KEY or SUPABASE_SERVICE_ROLE_KEY); using "
            "InMemoryArtifactService - artifacts stay local to this process, "
            "so slides vanish on exit and none can be signed for publishing."
        )
    from google.adk.artifacts import InMemoryArtifactService

    return InMemoryArtifactService()


def _build_memory_service() -> BaseMemoryService:
    """Build the memory service: Postgres-backed, else in-memory.

    ``PostgresMemoryService`` uses the shared HTTPS client lazily, so constructing it here
    performs no I/O; runtime database errors degrade gracefully inside the
    orchestrator and learner, which treat memory as best-effort.
    """
    if settings.supabase_url and settings.supabase_storage_key:
        return PostgresMemoryService()
    logger.warning(
        "Supabase server settings missing; using InMemoryMemoryService - recent-feedback "
        "injection and permanent feedback storage are disabled."
    )
    from google.adk.memory import InMemoryMemoryService

    return InMemoryMemoryService()


def session_service() -> BaseSessionService:
    """The shared session service (built on first use)."""
    global _session_service
    if _session_service is None:
        with _lock:
            if _session_service is None:
                _session_service = _build_session_service()
    return _session_service


def artifact_service() -> BaseArtifactService:
    """The shared artifact service (built on first use)."""
    global _artifact_service
    if _artifact_service is None:
        with _lock:
            if _artifact_service is None:
                _artifact_service = _build_artifact_service()
    return _artifact_service


def memory_service() -> BaseMemoryService:
    """The shared memory service (built on first use)."""
    global _memory_service
    if _memory_service is None:
        with _lock:
            if _memory_service is None:
                _memory_service = _build_memory_service()
    return _memory_service


async def close_services() -> None:
    """Release the shared services on process shutdown.

    Best effort by design: this runs while the process is going down, and a
    service that cannot be closed cleanly must not stop the others from
    trying.
    """
    global _session_service, _artifact_service, _memory_service
    for name, service in (
        ("session", _session_service),
        ("artifact", _artifact_service),
        ("memory", _memory_service),
    ):
        closer = getattr(service, "close", None)
        if closer is None:
            continue
        try:
            result = closer()
            if hasattr(result, "__await__"):
                await result
        except Exception as exc:  # pragma: no cover - shutdown best effort
            logger.warning("Closing the %s service failed: %s", name, exc)
    _session_service = _artifact_service = _memory_service = None


def reset_services() -> None:
    """Drop the cached services without closing them (tests only)."""
    global _session_service, _artifact_service, _memory_service
    _session_service = _artifact_service = _memory_service = None


__all__ = [
    "artifact_service",
    "close_services",
    "memory_service",
    "reset_services",
    "session_service",
]
