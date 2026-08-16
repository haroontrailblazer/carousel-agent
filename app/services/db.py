"""Postgres access layer for the Carousel Factory (asyncpg).

A single lazily-created connection pool over ``settings.database_url`` backs
the operational tables defined in ``db/schema.sql``:

- ``news_queue``      — fetched news items waiting for a pipeline run
- ``runs``            — one row per pipeline run (phase tracking)
- ``feedback``        — every human verdict + feedback text (learning input)
- ``pending_reviews`` — the paused review invocation (session + call id)

Nothing here touches the network at import time: the pool is created on the
first call that needs it. When ``DATABASE_URL`` is unset every public function
raises a clear ``RuntimeError`` telling the operator what to configure.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from typing import Any, Optional

import asyncpg

from app.config import settings

# Statuses used in news_queue.status.
STATUS_QUEUED = "queued"
STATUS_PROCESSING = "processing"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

# Explicit timeouts (seconds) for every network interaction with Postgres.
_ACQUIRE_TIMEOUT_S = 15.0
_COMMAND_TIMEOUT_S = 30.0

_pool: Optional[asyncpg.Pool] = None
_pool_lock = asyncio.Lock()


def _dsn() -> str:
    """Return a plain-postgres DSN, or raise if DATABASE_URL is not set.

    ``settings.database_url`` may carry the SQLAlchemy-style
    ``postgresql+asyncpg://`` scheme (used by ADK's DatabaseSessionService
    config); asyncpg itself wants the bare ``postgresql://`` scheme, so the
    ``+asyncpg`` marker is stripped here.
    """
    url = settings.database_url
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Point it at your Supabase/Postgres "
            "instance (e.g. postgresql+asyncpg://user:pass@host:5432/postgres) "
            "in .env before using app.services.db."
        )
    return url.replace("+asyncpg", "", 1)


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Per-connection setup: encode/decode json & jsonb as Python objects."""
    for typename in ("json", "jsonb"):
        await conn.set_type_codec(
            typename,
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )


async def get_pool() -> asyncpg.Pool:
    """Return the shared connection pool, creating it on first use.

    Raises:
        RuntimeError: if ``DATABASE_URL`` is unset (clear operator message).
    """
    global _pool
    if _pool is not None:
        return _pool
    dsn = _dsn()  # raise early, before taking the lock
    async with _pool_lock:
        if _pool is None:
            _pool = await asyncpg.create_pool(
                dsn,
                min_size=1,
                max_size=5,
                init=_init_connection,
                timeout=_ACQUIRE_TIMEOUT_S,
                command_timeout=_COMMAND_TIMEOUT_S,
            )
    return _pool


async def close_pool() -> None:
    """Close the shared pool (call on process shutdown). Safe if never opened."""
    global _pool
    async with _pool_lock:
        if _pool is not None:
            await _pool.close()
            _pool = None


def url_hash(url: str) -> str:
    """Stable sha256 hex digest of a URL — the news_queue dedupe key."""
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()


def _item_url_hash(item: dict) -> str:
    """Derive the dedupe hash for a news item dict.

    Prefers an explicit ``url_hash`` key, then the source URL, then the id or
    title so that even URL-less items still dedupe deterministically.
    """
    explicit = item.get("url_hash")
    if explicit:
        return str(explicit)
    basis = (
        item.get("source_url")
        or item.get("url")
        or item.get("id")
        or item.get("title")
        or ""
    )
    if not basis:
        raise ValueError(
            "news item has no source_url/url/id/title to derive a dedupe hash from"
        )
    return url_hash(str(basis))


# ---------------------------------------------------------------------------
# news_queue
# ---------------------------------------------------------------------------


async def enqueue_news(item: dict) -> dict:
    """Insert a news item into ``news_queue``, deduping on its URL hash.

    Args:
        item: a JSON-serializable dict (typically ``NewsItem.model_dump``).
            An ``id`` is assigned if missing; ``url_hash`` may be supplied
            explicitly, otherwise it is derived from source_url/url/id/title.

    Returns:
        ``{"id": str, "url_hash": str, "enqueued": bool}`` — ``enqueued`` is
        False when an item with the same hash already exists (its stored id
        is returned instead).
    """
    payload = dict(item)
    payload.setdefault("id", uuid.uuid4().hex)
    h = _item_url_hash(payload)
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO news_queue (id, url_hash, payload, status)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (url_hash) DO NOTHING
        RETURNING id
        """,
        str(payload["id"]),
        h,
        payload,
        STATUS_QUEUED,
    )
    if row is not None:
        return {"id": row["id"], "url_hash": h, "enqueued": True}
    existing = await pool.fetchrow(
        "SELECT id FROM news_queue WHERE url_hash = $1", h
    )
    return {
        "id": existing["id"] if existing else str(payload["id"]),
        "url_hash": h,
        "enqueued": False,
    }


async def next_queued_news() -> Optional[dict]:
    """Pop the oldest queued news item and mark it ``processing``.

    Uses ``FOR UPDATE SKIP LOCKED`` so concurrent workers never grab the same
    row.

    Returns:
        The item's payload dict (with ``id`` guaranteed to match the queue
        row), or ``None`` when the queue is empty.
    """
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        WITH next AS (
            SELECT id
            FROM news_queue
            WHERE status = $1
            ORDER BY created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        UPDATE news_queue q
        SET status = $2
        FROM next
        WHERE q.id = next.id
        RETURNING q.id, q.payload
        """,
        STATUS_QUEUED,
        STATUS_PROCESSING,
    )
    if row is None:
        return None
    payload: dict[str, Any] = dict(row["payload"] or {})
    payload["id"] = row["id"]
    return payload


async def mark_news_done(id: str, status: str = STATUS_DONE) -> None:
    """Set the final status of a news_queue row (``done`` or ``failed``).

    Args:
        id: the news_queue row id (as returned by enqueue/next_queued_news).
        status: one of ``STATUS_DONE`` / ``STATUS_FAILED`` (free-form allowed
            but stick to the module constants).
    """
    pool = await get_pool()
    await pool.execute(
        "UPDATE news_queue SET status = $2 WHERE id = $1", str(id), status
    )


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------


async def create_run(run_id: str, news_id: str) -> None:
    """Insert a run row (phase ``generate``, review_round 0). Idempotent."""
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO runs (run_id, news_id, phase, review_round)
        VALUES ($1, $2, 'generate', 0)
        ON CONFLICT (run_id) DO UPDATE
        SET news_id = EXCLUDED.news_id, updated_at = now()
        """,
        str(run_id),
        str(news_id),
    )


async def update_run_phase(
    run_id: str, phase: str, review_round: Optional[int] = None
) -> None:
    """Record a phase transition for a run (and optionally its review round).

    Args:
        run_id: the run to update.
        phase: one of the ``PHASE_*`` constants from ``app.state``.
        review_round: when given, also updates ``runs.review_round``.
    """
    pool = await get_pool()
    if review_round is None:
        await pool.execute(
            "UPDATE runs SET phase = $2, updated_at = now() WHERE run_id = $1",
            str(run_id),
            phase,
        )
    else:
        await pool.execute(
            """
            UPDATE runs
            SET phase = $2, review_round = $3, updated_at = now()
            WHERE run_id = $1
            """,
            str(run_id),
            phase,
            int(review_round),
        )


# ---------------------------------------------------------------------------
# pending_reviews — the paused LongRunningFunctionTool call
# ---------------------------------------------------------------------------


async def save_pending_review(
    run_id: str, session_id: str, function_call_id: str
) -> None:
    """Persist the paused review invocation so the review API can resume it.

    Upserts, since a rework loop sends a fresh review mail (and a fresh
    pending function call) for the same run.
    """
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO pending_reviews (run_id, session_id, function_call_id)
        VALUES ($1, $2, $3)
        ON CONFLICT (run_id) DO UPDATE
        SET session_id = EXCLUDED.session_id,
            function_call_id = EXCLUDED.function_call_id,
            created_at = now()
        """,
        str(run_id),
        str(session_id),
        str(function_call_id),
    )


async def load_pending_review(run_id: str) -> Optional[dict]:
    """Load the pending review for a run.

    Returns:
        ``{"run_id", "session_id", "function_call_id", "created_at"}`` (with
        ``created_at`` as an ISO-8601 string) or ``None`` if nothing pends.
    """
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT run_id, session_id, function_call_id, created_at
        FROM pending_reviews
        WHERE run_id = $1
        """,
        str(run_id),
    )
    if row is None:
        return None
    return {
        "run_id": row["run_id"],
        "session_id": row["session_id"],
        "function_call_id": row["function_call_id"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


async def clear_pending_review(run_id: str) -> None:
    """Delete the pending review row once the run has been resumed."""
    pool = await get_pool()
    await pool.execute(
        "DELETE FROM pending_reviews WHERE run_id = $1", str(run_id)
    )


# ---------------------------------------------------------------------------
# feedback
# ---------------------------------------------------------------------------


async def record_verdict(
    run_id: str,
    status: str,
    feedback: str,
    targets: Optional[list[str]] = None,
    news_title: str = "",
) -> int:
    """Store a human verdict in the ``feedback`` table.

    Args:
        run_id: the run the verdict belongs to.
        status: ``"approved"`` or ``"rejected"``.
        feedback: reviewer text (may be empty on approve).
        targets: optional rework targets (agent names) the router derived.
        news_title: title of the news item, for readable feedback history.

    Returns:
        The new feedback row id.
    """
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO feedback (run_id, verdict, feedback, targets, news_title)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id
        """,
        str(run_id),
        status,
        feedback or "",
        list(targets or []),
        news_title or "",
    )
    return int(row["id"])
