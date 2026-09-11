"""The in-process scheduler that keeps the news queue topped up.

**It fetches. It does not run.** Polling RSS and YouTube costs nothing; turning
a story into a carousel costs image and reasoning credits, so that decision
stays with a person. The scheduler's whole job is to make sure there is always
something worth choosing from when someone opens the console.

Deliberately in-process rather than a separate Render cron service. One web
service is the whole deployment, and a cron job would be a second one - which
also means a second process that could reclaim this one's runs at startup (see
app/runs/recovery.py on why exactly one instance may exist).

The cadence lives in the ``app_config`` table, not in an environment variable,
because ``settings`` is a frozen dataclass read once at import: changing an env
var needs a redeploy, whereas changing a row does not.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from app.services import db, instagram_accounts, instagram_oauth
from app import tenancy
from app.services.job_lease import job_lease

logger = logging.getLogger(__name__)

#: Config key holding the schedule.
CONFIG_KEY = "schedule"

#: Default: top the queue up hourly. Cheap - it is a handful of HTTP GETs.
DEFAULT_SCHEDULE: dict = {
    "enabled": True,
    "fetch_cron": "0 * * * *",
}

#: When to renew Instagram tokens. Daily, in the small hours - a token has a
#: fortnight of slack before it matters, so this never needs to be prompt.
IG_REFRESH_CRON = "17 4 * * *"

_scheduler: Any = None

#: How many fetches are in flight right now.
#:
#: A counter rather than a flag: a manual "check now" can overlap the cron
#: tick, and a flag would be cleared by whichever finished first while the
#: other was still working - so the console would stop showing "checking"
#: while a check was still running.
_fetch_counts = tenancy.ScopedDict()


def fetch_in_progress() -> bool:
    """Whether a feed check is running right now, from any trigger.

    Exposed for the console: the newsroom shows a live dot while the sources
    are being polled. Note this is NOT ``scheduler_state()["running"]``, which
    says whether the timer itself is alive - a very different question.
    """
    return _fetch_counts.get("active", 0) > 0


async def load_schedule() -> dict:
    """The current schedule, falling back to the default."""
    try:
        stored = await db.get_config(CONFIG_KEY)
    except Exception as exc:
        logger.warning("Could not read the schedule config (%s); using defaults.", exc)
        return dict(DEFAULT_SCHEDULE)
    if not isinstance(stored, dict):
        return dict(DEFAULT_SCHEDULE)
    return {**DEFAULT_SCHEDULE, **stored}


async def save_schedule(schedule: dict) -> dict:
    """Persist a new schedule and apply it without a restart."""
    merged = {**DEFAULT_SCHEDULE, **(schedule or {})}
    await db.set_config(CONFIG_KEY, merged)
    await reschedule()
    return merged


async def run_fetch_once() -> dict:
    """Poll the configured sources into ``news_queue``.

    Returns a summary rather than raising: this runs on a timer with nobody
    watching, and a feed being down for an hour is not an incident.
    """
    _fetch_counts["active"] = _fetch_counts.get("active", 0) + 1
    try:
        async with job_lease("news-fetch") as acquired:
            if not acquired:
                return {"skipped": "locked"}
            # Deferred import: the fetcher pulls in feedparser and the agent
            # stack. The scheduler module itself must stay cheap to import.
            #
            # Two calls, not one: fetch_all() only POLLS the sources and returns
            # payloads - enqueue_items() is what writes them to news_queue and
            # dedupes. Calling fetch_all alone would poll every hour and quietly
            # discard everything it found.
            from fetcher.fetch_news import enqueue_items, fetch_all

            from app.services import source_config
            await source_config.load()
            payloads = await asyncio.to_thread(fetch_all)
            enqueued, skipped = await enqueue_items(payloads)
            summary = {"fetched": len(payloads), "enqueued": enqueued, "duplicates": skipped}
            logger.info(
                "Scheduled fetch: %d fetched, %d new, %d duplicate(s).",
                len(payloads),
                enqueued,
                skipped,
            )
            return summary
    except Exception as exc:
        logger.exception("Scheduled fetch failed: %s", exc)
        return {"error": str(exc)}
    finally:
        _fetch_counts["active"] -= 1


async def refresh_instagram_tokens() -> dict:
    """Renew every long-lived Instagram token that is close to expiring.

    Meta's long-lived tokens last 60 days and can be extended at any point
    after their first 24 hours - but ONLY while still valid. A lapsed token
    has no recovery call; the account has to be connected again by hand. So
    this runs daily with a fortnight of slack, and a failure has two weeks of
    further attempts before it becomes somebody's problem.

    Each account is refreshed independently: one failing token must not stop
    the others, because they are unrelated credentials that merely happen to
    be renewed by the same job.

    Returns:
        ``{"refreshed": n, "failed": n}`` - for the log and for tests.
    """
    due = instagram_accounts.due_for_refresh()
    if not due:
        return {"refreshed": 0, "failed": 0}

    refreshed = 0
    failed = 0
    for account in due:
        try:
            token, expires_in = await asyncio.to_thread(
                instagram_oauth.refresh_long_lived, account.token
            )
            await instagram_accounts.record_refreshed(account.id, token, expires_in)
            refreshed += 1
            logger.info(
                "Refreshed the Instagram token for %s (%d days were left).",
                account.handle,
                account.expires_in_days or 0,
            )
        except Exception as exc:  # noqa: BLE001 - network, Meta, or storage
            failed += 1
            logger.error(
                "Could not refresh the Instagram token for %s: %s. It expires "
                "in %s days; reconnect the account if this keeps failing.",
                account.handle,
                exc,
                account.expires_in_days,
            )
    return {"refreshed": refreshed, "failed": failed}


async def _refresh_instagram_tokens_locked() -> None:
    """Refresh under a renewable lease shared across application instances."""
    try:
        async with job_lease("instagram-refresh") as acquired:
            if acquired:
                await refresh_instagram_tokens()
    except Exception:
        logger.exception("Scheduled Instagram refresh failed.")


async def _for_owner(owner: str, action: str):
    with tenancy.bind(owner):
        if action == "fetch":
            await run_fetch_once()
        else:
            await instagram_accounts.load()
            await _refresh_instagram_tokens_locked()


async def refresh_workspace_jobs():
    if _scheduler is None:
        return
    try:
        owners = await tenancy.owners()
        for owner in owners:
            with tenancy.bind(owner):
                await reschedule()
        valid = {f"{kind}:{owner}" for owner in owners for kind in ("fetch_news", "refresh_instagram")}
        for job in _scheduler.get_jobs():
            if job.id != "workspaces" and job.id not in valid:
                _scheduler.remove_job(job.id)
    except Exception:
        logger.exception("Could not refresh workspace schedules")


async def start_scheduler():
    global _scheduler
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    _scheduler = AsyncIOScheduler(job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60})
    _scheduler.add_job(refresh_workspace_jobs, "interval", minutes=1, id="workspaces")
    _scheduler.start()
    await refresh_workspace_jobs()
    return _scheduler


async def reschedule():
    if _scheduler is None:
        return
    from apscheduler.triggers.cron import CronTrigger
    owner = tenancy.require()
    schedule = await load_schedule()
    job_id = f"fetch_news:{owner}"
    previous = _scheduler.get_job(job_id)
    cron = str(schedule["fetch_cron"])
    trigger = CronTrigger.from_crontab(cron)
    if not schedule.get("enabled", True):
        if previous:
            _scheduler.remove_job(job_id)
    elif previous is None or str(previous.trigger) != str(trigger):
        _scheduler.add_job(_for_owner, trigger, args=[owner, "fetch"], id=job_id, replace_existing=True)
    refresh_id = f"refresh_instagram:{owner}"
    if not _scheduler.get_job(refresh_id):
        _scheduler.add_job(_for_owner, CronTrigger.from_crontab(IG_REFRESH_CRON), args=[owner, "instagram"], id=refresh_id)


def scheduler_state():
    if _scheduler is None:
        return {"running": False, "next_run": None}
    job = _scheduler.get_job(f"fetch_news:{tenancy.current()}")
    next_run = getattr(job, "next_run_time", None) if job else None
    return {"running": bool(_scheduler.running), "next_run": next_run.isoformat() if next_run else None}


def shutdown_scheduler() -> None:
    """Stop the scheduler without waiting for a running job."""
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:  # pragma: no cover - shutdown best effort
            pass
        _scheduler = None


__all__ = [
    "CONFIG_KEY",
    "DEFAULT_SCHEDULE",
    "fetch_in_progress",
    "load_schedule",
    "reschedule",
    "run_fetch_once",
    "save_schedule",
    "scheduler_state",
    "shutdown_scheduler",
    "start_scheduler",
]
