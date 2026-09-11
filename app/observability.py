"""Local token totals, independent of optional Langfuse export.

ADK usage_metadata feeds the orchestrator's persisted totals. Images API calls
accumulate per run here and may also export a span using UI-saved tracing settings.
"""

from __future__ import annotations

import contextvars
import logging
import threading
from typing import Any

from app.langfuse_tracing import record_image_span

logger = logging.getLogger(__name__)

_lock = threading.Lock()

#: Which run the current task is working on. Image tools sit several layers
#: below anything that knows a run id, so threading it through every call
#: signature would touch a lot of code for one number. A ContextVar reaches
#: them for free: asyncio tasks inherit the context they were created in, and
#: ``asyncio.to_thread`` copies it, so a blocking image call made inside a
#: worker thread still reports against the right run.
_current_run: contextvars.ContextVar[str] = contextvars.ContextVar(
    "carousel_run_id", default=""
)


def bind_run(run_id: str) -> None:
    """Attribute everything this task does from here on to ``run_id``.

    Call once at the top of the task driving a run. Nothing needs to unbind:
    each run's task has its own context, so the value cannot leak sideways.
    """
    _current_run.set(str(run_id or ""))


def current_run() -> str:
    """The run this task is working on ('' outside a run)."""
    return _current_run.get()


#: Per-run accumulators for OpenAI Images API token usage. Image tools
#: run outside any ADK model call, so their usage cannot ride on
#: ``Event.usage_metadata`` - they deposit here and the orchestrator drains it.
#: the LLM event's usage_metadata the way every other token count does.
#:
#: Keyed by run id, because several carousels can be in flight at once. A
#: single shared bucket meant whichever run happened to reach a phase
#: transition first drained everyone's image tokens into its own total - one
#: run billed for another's images, the other reporting none.
_image_usage: dict[str, dict[str, int]] = {}


def _empty_bucket() -> dict[str, int]:
    return {
        "image_input_tokens": 0,
        "image_output_tokens": 0,
        "image_total_tokens": 0,
        "image_calls": 0,
    }


def init_observability() -> bool:
    """Legacy lifecycle hook. Optional export now starts inside each saved run."""
    return False


def shutdown_observability() -> None:
    """Legacy lifecycle hook; run-scoped exporters flush when their run exits."""


def record_image_usage(
    model: str, endpoint: str, usage: Any, prompt: str = ""
) -> None:
    """Record one Images API call: accumulate tokens + emit a Langfuse span.

    Args:
        model: Image model id (e.g. ``gpt-image-2``).
        endpoint: Which API was used (``images.edit`` / ``images.generate``).
        usage: The response's ``usage`` object (``input_tokens`` /
            ``output_tokens`` / ``total_tokens``), or None when absent.
        prompt: Accepted for caller compatibility; prompt contents are not exported.
    """
    input_t = int(getattr(usage, "input_tokens", 0) or 0)
    output_t = int(getattr(usage, "output_tokens", 0) or 0)
    total_t = int(getattr(usage, "total_tokens", 0) or 0) or (input_t + output_t)
    run_id = _current_run.get()
    with _lock:
        bucket = _image_usage.setdefault(run_id, _empty_bucket())
        bucket["image_input_tokens"] += input_t
        bucket["image_output_tokens"] += output_t
        bucket["image_total_tokens"] += total_t
        bucket["image_calls"] += 1
    logger.info(
        "[tokens] %s %s: input=%d output=%d total=%d",
        model,
        endpoint,
        input_t,
        output_t,
        total_t,
    )

    record_image_span(model, endpoint, input_t, output_t, total_t)


def pop_image_usage(run_id: str = "") -> dict[str, int]:
    """Drain one run's image-usage accumulator.

    Args:
        run_id: Whose tokens to take. Defaults to the current task's run, so
            callers inside a run need not pass anything. Draining a run that
            recorded nothing returns zeros.

    Returns:
        The counts recorded since the last drain, and forgets them.
    """
    key = str(run_id or "") or _current_run.get()
    with _lock:
        bucket = _image_usage.pop(key, None)
    return bucket if bucket is not None else _empty_bucket()


__all__ = [
    "init_observability",
    "shutdown_observability",
    "bind_run",
    "current_run",
    "pop_image_usage",
    "record_image_usage",
]
