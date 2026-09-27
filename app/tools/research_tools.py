"""Web research tools for the Research agent.

Two ``FunctionTool``-wrapped functions:

- :func:`search_web` - one web search via the OpenAI Responses API
  ``web_search`` built-in tool (verified working on this key), returning the
  synthesized answer plus its citation URLs. Its instructions carry today's
  UTC date and the run's time window (see :mod:`app.time_window`), because
  the search model's own "now" is its training cutoff.
- :func:`save_research_brief` - validates the gathered material as a
  :class:`app.schemas.ResearchBrief`, checks its fact dates against the time
  window, writes it to session state under ``K_RESEARCH``, and merges any
  discovered official media URLs into the news item's ``media_urls`` so the
  First-Page Visual agent can clip them without any change on its side.

The search model is the utility model's bare OpenAI id (cheap; the Research
agent itself does the judgment). Search is best-effort: failures return an
``error`` status result instead of raising, so a flaky network can never
kill the pipeline - the agent falls back to the news item's own text.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping
from datetime import date
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from google.adk.tools import ToolContext
from openai import OpenAI, OpenAIError

from app import time_window
from app.services import ai_config
from app.llm import OPENAI_REASONING_EFFORT
from app.schemas import ResearchBrief
from app.state import K_NEWS_ITEM, K_RESEARCH, K_RESEARCH_RELAXED, K_TIME_WINDOW

logger = logging.getLogger(__name__)

_SEARCH_TIMEOUT_S: float = 120.0
_RETRY_DELAY_S: float = 4.0
_MAX_QUERY_CHARS = 400
_MAX_MEDIA_URLS = 8  # cap on news_item.media_urls after merging candidates

_client_singleton: Optional[OpenAI] = None

_TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
}


def _field(value: Any, name: str, default: Any = None) -> Any:
    """Read one field from an SDK model or a mapping response fixture."""
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _normalize_http_url(value: Any) -> str:
    """Return a stable http(s) URL, stripping fragments/tracking parameters."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return ""
    query = urlencode(
        [
            (key, item)
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in _TRACKING_QUERY_KEYS
        ],
        doseq=True,
    )
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, query, ""))


def _unique_urls(values: list[Any]) -> list[str]:
    """Normalize and de-duplicate URLs while preserving their first order."""
    result: list[str] = []
    for value in values:
        url = _normalize_http_url(value)
        if url and url not in result:
            result.append(url)
    return result


def _response_sources(response: Any) -> list[str]:
    """Parse cited and consulted URLs from a Responses API result robustly."""
    found: list[Any] = []
    for item in _field(response, "output", []) or []:
        item_type = str(_field(item, "type", ""))
        if item_type == "web_search_call":
            action = _field(item, "action", {}) or {}
            for source in _field(action, "sources", []) or []:
                found.append(_field(source, "url", ""))
            action_url = _field(action, "url", "")
            if action_url:
                found.append(action_url)
        if item_type != "message":
            continue
        for part in _field(item, "content", []) or []:
            for annotation in _field(part, "annotations", []) or []:
                url = _field(annotation, "url", "")
                if url:
                    found.append(url)
    return _unique_urls(found)


def _client() -> OpenAI:
    """Use this run's credential; tests can inject a client without network calls."""
    if _client_singleton is not None:
        return _client_singleton
    return ai_config.current().client.with_options(timeout=_SEARCH_TIMEOUT_S)


def _search_model_id() -> str:
    """Bare OpenAI model id used for web search (utility model, unprefixed)."""
    model = ai_config.current().utility_model
    return model.split("/", 1)[-1] if model.startswith("openai/") else model


def _search_instructions(window: Optional[dict]) -> str:
    """The search model's instructions, built per call around today's date.

    The search model's own sense of "now" is its training cutoff, so "this
    week's launches" found launches from months earlier (run-3c4107e390df).
    """
    today = time_window.today_utc()
    parts = [
        f"Today's date is {time_window.format_day(today)} (UTC); your training data is "
        "older, so resolve every relative time word (this week, latest, new, "
        "today, recent) against this date."
    ]
    hint = time_window.search_hint(window)
    if hint:
        parts.append(hint)
    parts.append(
        "Research one focused claim. Prefer primary/official sources, "
        "state exact dates and figures, separate confirmed facts from "
        "inference, cite every factual paragraph, and state the publication "
        "or announcement date of every fact."
    )
    return " ".join(parts)


def search_web(
    query: str,
    window: Optional[dict] = None,
    *,
    timeout_s: Optional[float] = None,
    attempts: int = 2,
) -> dict:
    """Search the live web and return a synthesized, cited answer.

    Uses the OpenAI Responses API ``web_search`` tool. Ask focused questions
    (one topic per call) and make several calls rather than one broad one.

    Args:
        query: The search question, e.g. "gpt-image-2 pricing and token
            costs official announcement".
        window: The run's ``K_TIME_WINDOW``; its dates go into the search
            instructions next to today's date.
        timeout_s: Per-attempt timeout (default 120 s).
        attempts: Tries before giving up; 1 means no retry and no sleep.

    Returns:
        ``{"status": "ok", "answer": str, "sources": [url, ...]}`` on
        success, or ``{"status": "error", "message": str}`` - on error,
        continue with the facts already gathered instead of retrying forever.
    """
    query = (query or "").strip()[:_MAX_QUERY_CHARS]
    if not query:
        return {"status": "error", "message": "Empty query."}

    instructions = _search_instructions(window)
    timeout = timeout_s or _SEARCH_TIMEOUT_S
    tries = max(1, int(attempts))
    last_exc: Optional[Exception] = None
    for attempt in range(tries):
        try:
            response = _client().responses.create(
                model=_search_model_id(),
                tools=[{"type": "web_search"}],
                include=["web_search_call.action.sources"],
                instructions=instructions,
                input=query,
                reasoning={"effort": OPENAI_REASONING_EFFORT},
                store=False,
                timeout=timeout,
            )
            sources = _response_sources(response)
            answer = (response.output_text or "").strip()
            if not answer:
                return {
                    "status": "error",
                    "message": "Search returned no text.",
                }
            logger.info(
                "[research] search %r -> %d chars, %d source(s)",
                query[:80],
                len(answer),
                len(sources),
            )
            return {"status": "ok", "answer": answer, "sources": sources}
        except OpenAIError as exc:
            last_exc = exc
            if attempt + 1 < tries:
                time.sleep(_RETRY_DELAY_S)
                continue
    return {"status": "error", "message": f"Search failed: {last_exc}"}


_ISO_FACT_DATE = re.compile(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?(?=$|[T ])")
_NUMERIC_FACT_DATE = re.compile(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})")
_YEAR_FACT_DATE = re.compile(r"(?:19|20)\d\d")
_YEAR_IN_TEXT = re.compile(r"\b((?:19|20)\d\d)\b")
# What a model writes when the source gives no date: stored as undated.
_UNKNOWN_DATES = frozenset({
    "n/a", "na", "n.a", "none", "unknown", "undated", "no date", "not stated", "not given",
    "unspecified", "tbd", "-", "?",
})


def _normalize_fact_date(value: Any) -> str:
    """A fact's date as ``YYYY-MM-DD``, ``YYYY-MM`` or ``YYYY`` (``""`` when unset).

    A datetime string is cut to its day, a written date ("September 22,
    2026") is read, an unambiguous "22/09/2026" too, and "N/A" or "unknown"
    is no date. A bare year is kept, and so is the one year a vague value
    names ("Q3 2026", "early 2026"): background facts often have no more
    ("introduced in 2017"). Anything else raises; the caller decides whether
    that matters (only under a strict time window).
    """
    text = str(value or "").strip()
    if not text or text.lower().strip(" .") in _UNKNOWN_DATES:
        return ""
    match = _ISO_FACT_DATE.match(text)
    if match:
        year, month, day = match.groups()
        try:
            if day:
                return date(int(year), int(month), int(day)).isoformat()
            return date(int(year), int(month), 1).isoformat()[:7]
        except ValueError:
            pass
        raise ValueError(text)
    numeric = _NUMERIC_FACT_DATE.fullmatch(text)
    if numeric:
        first, second, year = (int(part) for part in numeric.groups())
        # Day first when only that reading works (22/09), month first likewise.
        if first > 12 >= second or first == second:
            day, month = first, second
        elif second > 12 >= first:
            day, month = second, first
        else:
            raise ValueError(text)  # 05/09/2026: May or September
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            raise ValueError(text) from None
    if _YEAR_FACT_DATE.fullmatch(text):
        return text
    spans = time_window.fact_dates(text, time_window.today_utc())
    if len(spans) == 1:
        first, last = spans[0]
        if first == last:
            return first.isoformat()
        if (last - first).days <= 31:
            return first.isoformat()[:7]
    years = set(_YEAR_IN_TEXT.findall(text))
    if not spans and len(years) == 1:
        return years.pop()
    raise ValueError(text)


def _is_true(value: Any) -> bool:
    """A boolean argument that a model may send as the string "false"."""
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return bool(value)


def save_research_brief(
    summary: str,
    key_facts: list[dict],
    suggested_angle: str,
    media_candidates: list[str],
    sources: list[str],
    tool_context: ToolContext,
) -> dict:
    """Validate and store the research brief; feed media finds to the cover.

    Call this once, after searching. The brief lands in session state under
    ``research_brief`` (read by the planner and phrasing agents), and
    ``media_candidates`` are appended to the news item's ``media_urls`` so
    the First-Page Visual agent can source the cover clip from them.

    When the request named a time window ("this week", "latest"), facts must
    be dated inside it: a brief whose dates miss the window is still stored,
    but the call returns an error naming the window and the stale or undated
    facts. The rule is the one ``validate_output`` applies: on research's
    last attempt and under a reviewer's rework one fact inside the window is
    enough (``K_RESEARCH_RELAXED``). Its media reaches the cover whenever
    that relaxed rule passes, so a brief the pipeline goes on to accept
    never loses the in-window media it found.

    Args:
        summary: 3-6 sentence briefing on what happened and why it matters.
        key_facts: List of ``{"fact": str, "source_url": str, "date": str,
            "background": bool}`` entries - exact numbers/names/dates, each
            with the URL it was verified at (empty source_url only for facts
            taken from the news item text), the date the source says it
            happened or was announced (``YYYY-MM-DD``, ``YYYY-MM`` when the
            source gives only a month, ``YYYY`` when only a year, empty when
            none), and ``background`` true for older context that is not
            part of the news. A date that cannot be read is stored as empty,
            and is an error only under a strict time window.
        suggested_angle: One line - the most compelling hook angle found.
        media_candidates: Direct URLs of OFFICIAL announcement videos/images
            (event clips, launch videos, demo footage) usable for the cover.
        sources: All URLs consulted.
        tool_context: Injected by ADK.

    Returns:
        ``{"status": "saved", "fact_count": int, "media_added": int}`` or
        ``{"status": "error", "message": str}``. For bad arguments fix them
        and call again once; for dates outside the window search again with
        the window's dates, then save again. Never change a date to pass.
    """
    state = tool_context.state
    window = state.get(K_TIME_WINDOW)
    try:
        normalized_sources = _unique_urls(list(sources or []))
        normalized_media = _unique_urls(list(media_candidates or []))
        normalized_facts: list[dict] = []
        unreadable: list[str] = []
        for item in key_facts or []:
            fact = str(_field(item, "fact", "")).strip()
            raw_source_url = str(_field(item, "source_url", "") or "").strip()
            source_url = _normalize_http_url(raw_source_url)
            if not fact:
                continue
            if raw_source_url and not source_url:
                raise ValueError(
                    f"Fact source URL is not a valid http(s) URL: {raw_source_url!r}"
                )
            raw_date = _field(item, "date", "")
            try:
                fact_date = _normalize_fact_date(raw_date)
            except ValueError:
                # Stored undated; a run with no strict window never reads it.
                unreadable.append(f"fact {len(normalized_facts) + 1}: {str(raw_date).strip()!r}")
                fact_date = ""
            normalized_facts.append({
                "fact": fact,
                "source_url": source_url,
                "date": fact_date,
                "background": _is_true(_field(item, "background", False)),
            })
            if source_url and source_url not in normalized_sources:
                normalized_sources.append(source_url)
        if unreadable and isinstance(window, Mapping) and window.get("strict") and window.get("start"):
            raise ValueError(
                "Fact dates must be YYYY-MM-DD, YYYY-MM or YYYY, or empty when the "
                "source gives none; unreadable: " + "; ".join(unreadable)
            )

        brief = ResearchBrief(
            summary=summary,
            key_facts=normalized_facts,
            suggested_angle=suggested_angle,
            media_candidates=normalized_media,
            sources=normalized_sources,
        )
    except Exception as exc:
        return {"status": "error", "message": f"Invalid brief: {exc}"}

    state[K_RESEARCH] = brief.model_dump(mode="json")

    # Stored either way, so a run that cannot do better still has a brief for
    # validate_output's relaxed last-attempt check. The rule is validate_output's:
    # relaxed on the last attempt and under rework. The media of a brief with
    # no current fact at all is an old launch's artwork, so it stays away from
    # the cover; one with a current fact keeps it even while the strict rule
    # still asks for a better brief.
    relaxed = _is_true(state.get(K_RESEARCH_RELAXED))
    problems = time_window.window_problems(normalized_facts, window, relaxed=relaxed)
    current = not problems or (
        not relaxed and not time_window.window_problems(normalized_facts, window, relaxed=True)
    )

    # Merge discovered official media into the news item for the cover agent.
    media_added = 0
    news = state.get(K_NEWS_ITEM)
    if current and isinstance(news, dict):
        existing = [u for u in (news.get("media_urls") or []) if u]
        for url in brief.media_candidates:
            if url and url not in existing and len(existing) < _MAX_MEDIA_URLS:
                existing.append(url)
                media_added += 1
        if media_added:
            news = dict(news)
            news["media_urls"] = existing
            state[K_NEWS_ITEM] = news

    if problems:
        logger.info("[research] brief stored outside the time window: %s", "; ".join(problems))
        return {
            "status": "error",
            "message": (
                f"Facts must be dated {time_window.window_rule(window)}: "
                + "; ".join(problems)
                + ". Search again with these dates in the query, or mark genuine "
                "older context background=true. Do not change dates."
            ),
            "media_added": media_added,
        }

    logger.info(
        "[research] brief saved: %d fact(s), %d source(s), %d media added",
        len(brief.key_facts),
        len(brief.sources),
        media_added,
    )
    return {
        "status": "saved",
        "fact_count": len(brief.key_facts),
        "media_added": media_added,
    }


__all__ = ["save_research_brief", "search_web"]
