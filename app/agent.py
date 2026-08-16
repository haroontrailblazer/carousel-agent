"""Root agent + runner wiring for the Carousel Factory.

Exposes:

* ``root_agent`` — the module-level :class:`app.orchestrator.CarouselOrchestrator`
  instance that ``adk web`` / ``adk run`` discover (they import
  ``app.agent`` and read ``root_agent``), with all ten pipeline agents as
  ``sub_agents`` so the agent graph renders.
* ``build_root_agent()`` — builds a FRESH agent tree. Agent builders re-read
  their ``skills/agents/<name>.md`` instruction files, so a fresh tree picks
  up rules the Learner agent appended since the last build.
* ``build_runner()`` — a :class:`google.adk.runners.Runner` wired to the
  production services (``DatabaseSessionService`` on
  ``settings.database_url``, ``SupabaseArtifactService`` on Supabase S3
  storage, ``PostgresMemoryService``) with graceful in-memory fallbacks
  (``InMemorySessionService`` / ``InMemoryArtifactService`` /
  ``InMemoryMemoryService`` — names verified against installed google-adk
  2.7.0) whenever the environment is not configured, so ``adk web`` works out
  of the box.

Runner constructor verified against installed google-adk 2.7.0
(``google/adk/runners.py``): all-keyword ``Runner(*, app=None, app_name=None,
agent=None, node=None, plugins=None, artifact_service=None, session_service,
memory_service=None, credential_service=None, plugin_close_timeout=5.0,
auto_create_session=False)`` — ``session_service`` is required; passing
``agent`` requires ``app_name``.
"""

from __future__ import annotations

import logging
from typing import Optional

from google.adk.agents import BaseAgent
from google.adk.artifacts import BaseArtifactService
from google.adk.memory import BaseMemoryService
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService

from app.agents.cta import build_cta_agent
from app.agents.feedback_router import build_feedback_router_agent
from app.agents.first_page_visual import build_first_page_visual_agent
from app.agents.learner import build_learner_agent
from app.agents.phrasing import build_phrasing_agent
from app.agents.planner import build_planner_agent
from app.agents.publisher import build_publisher_agent
from app.agents.review_dispatcher import build_review_dispatcher_agent
from app.agents.stitch_verify import build_stitch_verify_agent
from app.agents.template_design import build_template_design_agent
from app.config import settings
from app.orchestrator import ORCHESTRATOR_NAME, CarouselOrchestrator
from app.services.artifact_service import SupabaseArtifactService
from app.services.memory_service import PostgresMemoryService

logger = logging.getLogger(__name__)

_ORCHESTRATOR_DESCRIPTION = (
    "Carousel Factory orchestrator: turns one queued AI/product news item "
    "into a human-reviewed Instagram carousel. Re-entrant phase machine: "
    "generate (plan, cover video, copy, slides, CTA) -> qa (stitch + verify) "
    "-> review (mail + pause for the human verdict) -> publish or rework -> "
    "done."
)


def build_sub_agents() -> list[BaseAgent]:
    """Build fresh instances of all ten pipeline agents.

    Order matters only for how ``adk web`` draws the graph: generation
    pipeline first, then QA, the review loop, and the terminal agents.

    Returns:
        New, parentless agent instances (each named by its ``app.state``
        constant), ready to be attached to one orchestrator.
    """
    return [
        build_planner_agent(),
        build_first_page_visual_agent(),
        build_phrasing_agent(),
        build_template_design_agent(),
        build_cta_agent(),
        build_stitch_verify_agent(),
        build_review_dispatcher_agent(),
        build_feedback_router_agent(),
        build_publisher_agent(),
        build_learner_agent(),
    ]


def build_root_agent() -> CarouselOrchestrator:
    """Build a fresh orchestrator with a fresh sub-agent tree.

    A new tree re-reads every ``skills/agents/<name>.md`` instruction file,
    so Learner-appended rules take effect on the next build without a code
    change.

    Returns:
        The configured :class:`CarouselOrchestrator` root agent.
    """
    return CarouselOrchestrator(
        name=ORCHESTRATOR_NAME,
        description=_ORCHESTRATOR_DESCRIPTION,
        sub_agents=build_sub_agents(),
    )


#: Module-level root agent for ``adk web`` / ``adk run`` discovery.
root_agent: CarouselOrchestrator = build_root_agent()


# ---------------------------------------------------------------------------
# Service wiring (production services with graceful in-memory fallbacks)
# ---------------------------------------------------------------------------
def _build_session_service() -> BaseSessionService:
    """Build the session service: Postgres-backed, else in-memory.

    ``DatabaseSessionService`` (google-adk 2.7.0: ``DatabaseSessionService
    (db_url)``, SQLAlchemy async engine) persists sessions so the review
    pause survives process restarts and the review API can resume runs.
    Without ``DATABASE_URL`` (or with the sqlalchemy extra missing) the
    in-memory service keeps local ``adk web`` fully functional.
    """
    if settings.database_url:
        try:
            from google.adk.sessions import DatabaseSessionService

            return DatabaseSessionService(settings.database_url)
        except Exception as exc:
            logger.warning(
                "DatabaseSessionService unavailable (%s); falling back to "
                "InMemorySessionService.",
                exc,
            )
    else:
        logger.warning(
            "DATABASE_URL not set; using InMemorySessionService — sessions "
            "will not survive a restart and the review API cannot resume "
            "runs from another process."
        )
    from google.adk.sessions import InMemorySessionService

    return InMemorySessionService()


def _build_artifact_service() -> BaseArtifactService:
    """Build the artifact service: Supabase S3-backed, else in-memory."""
    if settings.s3_endpoint and settings.s3_access_key and settings.s3_secret_key:
        try:
            return SupabaseArtifactService()
        except Exception as exc:
            logger.warning(
                "SupabaseArtifactService unavailable (%s); falling back to "
                "InMemoryArtifactService.",
                exc,
            )
    else:
        logger.warning(
            "Supabase S3 settings missing (SUPABASE_S3_ENDPOINT / "
            "SUPABASE_S3_ACCESS_KEY / SUPABASE_S3_SECRET_KEY); using "
            "InMemoryArtifactService — artifacts stay local to this process "
            "and no public URLs can be signed for publishing."
        )
    from google.adk.artifacts import InMemoryArtifactService

    return InMemoryArtifactService()


def _build_memory_service() -> BaseMemoryService:
    """Build the memory service: Postgres-backed, else in-memory.

    ``PostgresMemoryService`` opens its asyncpg pool lazily, so constructing
    it here performs no I/O; runtime DB errors degrade gracefully inside the
    orchestrator/learner (best-effort feedback features).
    """
    if settings.database_url:
        return PostgresMemoryService()
    logger.warning(
        "DATABASE_URL not set; using InMemoryMemoryService — recent-feedback "
        "injection and permanent feedback storage are disabled."
    )
    from google.adk.memory import InMemoryMemoryService

    return InMemoryMemoryService()


def build_runner(agent: Optional[BaseAgent] = None) -> Runner:
    """Build a Runner wired to the configured (or fallback) services.

    Used by the fetcher to start pipeline runs and by the review API to
    resume paused ones — both must share the same ``settings.app_name`` and
    session database so they address the same sessions.

    Args:
        agent: Optional pre-built root agent. Defaults to a FRESH tree from
            :func:`build_root_agent` so Learner-updated instruction files are
            picked up per runner.

    Returns:
        A ready :class:`google.adk.runners.Runner`.
    """
    return Runner(
        app_name=settings.app_name,
        agent=agent if agent is not None else build_root_agent(),
        session_service=_build_session_service(),
        artifact_service=_build_artifact_service(),
        memory_service=_build_memory_service(),
    )


__all__ = [
    "build_root_agent",
    "build_runner",
    "build_sub_agents",
    "root_agent",
]
