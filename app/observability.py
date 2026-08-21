"""Token/cost traceability - Langfuse tracing + process-level image usage.

Two complementary layers (both optional, both fail-soft):

1. **Langfuse tracing.** :func:`init_observability` activates the
   OpenInference Google-ADK instrumentor so EVERY agent step and LLM call is
   exported to Langfuse as a trace with input/output/total token counts (and
   cost, computed by Langfuse from the model id). Requires
   ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` in the environment
   (loaded from .env by ``app.config``); without them it is a logged no-op.
   Call it once per process, BEFORE the first model call - ``app.agent``
   (imported by ``adk web``), the fetcher CLI and the review API all do.
2. **In-run totals.** The orchestrator aggregates ``Event.usage_metadata``
   from every child event into session state under
   :data:`app.state.K_TOKEN_USAGE`, so each run carries its own cumulative
   prompt/output/total counts even with Langfuse disabled. gpt-image-2 calls
   report their token usage here via :func:`record_image_usage` (the Images
   API responses carry ``usage`` too); the orchestrator drains the
   accumulator with :func:`pop_image_usage` when committing state.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from app.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_init_done = False
_langfuse_client: Optional[Any] = None

#: Process-level accumulator for OpenAI Images API token usage. Image tools
#: run outside any ADK model call, so their usage cannot ride on
#: ``Event.usage_metadata`` - they deposit here and the orchestrator drains it.
_image_usage: dict[str, int] = {
    "image_input_tokens": 0,
    "image_output_tokens": 0,
    "image_total_tokens": 0,
    "image_calls": 0,
}


def init_observability() -> bool:
    """Activate Langfuse tracing for this process (idempotent, fail-soft).

    Returns:
        True when Langfuse tracing is active, False when unconfigured or the
        setup failed (the pipeline runs fine either way).
    """
    global _init_done, _langfuse_client
    with _lock:
        if _init_done:
            return _langfuse_client is not None
        _init_done = True

        if not (settings.langfuse_public_key and settings.langfuse_secret_key):
            logger.info(
                "Langfuse not configured (LANGFUSE_PUBLIC_KEY / "
                "LANGFUSE_SECRET_KEY unset) - per-call traces disabled; run "
                "token totals still accumulate in session state."
            )
            return False
        try:
            from langfuse import get_client
            from openinference.instrumentation.google_adk import (
                GoogleADKInstrumentor,
            )

            _langfuse_client = get_client()
            GoogleADKInstrumentor().instrument()
            logger.info(
                "Langfuse tracing active -> %s", settings.langfuse_base_url
            )
            return True
        except Exception as exc:
            _langfuse_client = None
            logger.warning(
                "Langfuse init failed (%s) - continuing without tracing.", exc
            )
            return False


def shutdown_observability() -> None:
    """Flush buffered Langfuse spans (call before short-lived CLIs exit)."""
    if _langfuse_client is None:
        return
    try:
        _langfuse_client.flush()
    except Exception as exc:  # pragma: no cover - network dependent
        logger.debug("Langfuse flush failed (%s).", exc)


def record_image_usage(
    model: str, endpoint: str, usage: Any, prompt: str = ""
) -> None:
    """Record one Images API call: accumulate tokens + emit a Langfuse span.

    Args:
        model: Image model id (e.g. ``gpt-image-2``).
        endpoint: Which API was used (``images.edit`` / ``images.generate``).
        usage: The response's ``usage`` object (``input_tokens`` /
            ``output_tokens`` / ``total_tokens``), or None when absent.
        prompt: The rendering prompt (truncated into the trace input).
    """
    input_t = int(getattr(usage, "input_tokens", 0) or 0)
    output_t = int(getattr(usage, "output_tokens", 0) or 0)
    total_t = int(getattr(usage, "total_tokens", 0) or 0) or (input_t + output_t)
    with _lock:
        _image_usage["image_input_tokens"] += input_t
        _image_usage["image_output_tokens"] += output_t
        _image_usage["image_total_tokens"] += total_t
        _image_usage["image_calls"] += 1
    logger.info(
        "[tokens] %s %s: input=%d output=%d total=%d",
        model,
        endpoint,
        input_t,
        output_t,
        total_t,
    )

    if _langfuse_client is None:
        return
    try:
        generation = _langfuse_client.start_generation(
            name=endpoint,
            model=model,
            input=prompt[:2000] if prompt else None,
            metadata={"endpoint": endpoint},
        )
        generation.update(
            usage_details={
                "input": input_t,
                "output": output_t,
                "total": total_t,
            }
        )
        generation.end()
    except Exception as exc:  # never let telemetry break rendering
        logger.debug("Langfuse image-generation span failed (%s).", exc)


def pop_image_usage() -> dict[str, int]:
    """Drain the image-usage accumulator (returns zeros when nothing new)."""
    with _lock:
        snapshot = dict(_image_usage)
        for key in _image_usage:
            _image_usage[key] = 0
    return snapshot


__all__ = [
    "init_observability",
    "pop_image_usage",
    "record_image_usage",
    "shutdown_observability",
]
