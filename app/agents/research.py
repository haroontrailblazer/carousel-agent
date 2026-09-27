"""Research agent - gathers verified facts BEFORE anything is planned.

First agent of the generate phase. A tool-using ``LlmAgent`` on
``settings.planner_model`` (facts shape everything downstream, so it gets the
strong model) with two tools from :mod:`app.tools.research_tools`:

- ``search_web`` - live web search (OpenAI Responses ``web_search``) with
  citations; called several times with focused queries. Every call carries
  today's UTC date and the run's time window (``K_TIME_WINDOW``).
- ``save_research_brief`` - validates and stores the
  :class:`app.schemas.ResearchBrief` under ``K_RESEARCH``, rejects fact dates
  outside the requested time window, and merges official media URLs it found
  into the news item's ``media_urls`` (so the First-Page Visual agent can clip
  the announcement footage without any change).

The prompt shows ``{time_context?}`` (today's date and the window, see
:mod:`app.time_window`) because the model's own sense of "now" is its
training cutoff: asked for "this week" on 26 September 2026 it searched for
March launches (run-3c4107e390df).

Hand-off contract: the planner and phrasing agents receive the brief through
``{research_brief?}`` instruction templating - research NEVER writes copy or
plans slides itself. On a "facts are wrong/outdated" rejection the Feedback
Router targets ``research``, which forces a full re-plan (see the
``_REWORK_DEPENDENTS`` map in app/orchestrator.py).

Instruction lives in ``skills/agents/research.md`` (Learner-editable) with
:data:`DEFAULT_INSTRUCTION` as the seed/fallback.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools import FunctionTool, ToolContext
from google.genai import types

from app import time_window
from app.config import agent_instructions, settings
from app.llm import resolve_role_model
from app.state import (
    AGENT_RESEARCH,
    K_NEWS_ITEM,
    K_RECENT_FEEDBACK,
    K_REWORK_FEEDBACK,
    K_TIME_CONTEXT,
    K_TIME_WINDOW,
)
from app.tools import research_tools
from app.tools.research_tools import save_research_brief


def _window_note(query: str, window: object) -> str:
    """A hint when a query names only dates from before the requested window.

    Advice, never a refusal: an older date is right for a background search.
    """
    if not time_window.names_only_earlier_dates(query, window):
        return ""
    return (
        f"This query names only dates before the requested window "
        f"({time_window.describe_range(window)}). For recent events, search with "
        "the window's dates instead."
    )


#: Billed searches per research attempt: the prompt's 5, plus 2 after a date
#: rejection. Only the prompt limited them, and a date rejection repeated on
#: every save could turn into search after search.
_MAX_SEARCHES = 7
_K_SEARCHES = "temp:research_searches"


async def search_web(query: str, *, tool_context: ToolContext) -> dict:
    """Search the live web for one focused question and return cited facts.

    For recent events, put the requested window's dates in the query. At
    most 7 searches per research step; after that the result is an error:
    save the brief with what you have.
    """
    # The run's time window goes into the search instructions with today's
    # date. The synchronous provider client and its retry sleeps must not run
    # on the API event loop. to_thread also carries this run's credentials and
    # tenant context into the worker without blocking navigation, Stop, or
    # heartbeats. A context without state (tests) searches with no window.
    state = getattr(tool_context, "state", None)
    if state is None:
        state = {}
    used = int(state.get(_K_SEARCHES) or 0)
    if used >= _MAX_SEARCHES:
        return {
            "status": "error",
            "message": (
                f"Search budget used ({_MAX_SEARCHES} searches this step): save the "
                "brief with the facts you have."
            ),
        }
    state[_K_SEARCHES] = used + 1
    window = state.get(K_TIME_WINDOW)
    result = await asyncio.to_thread(research_tools.search_web, query, window)
    note = _window_note(query, window)
    if note and isinstance(result, dict):
        result = {**result, "window_note": note}
    return result

# NOTE: the literal template placeholders below MUST match the state keys in
# app/state.py: {news_item} == K_NEWS_ITEM, {time_context?} == K_TIME_CONTEXT,
# {rework_feedback?} == K_REWORK_FEEDBACK, {recent_feedback_notes?} ==
# K_RECENT_FEEDBACK.
DEFAULT_INSTRUCTION = """\
# Research agent

You are the Research agent of the Carousel Factory - the FIRST agent to touch
a news item. Everything downstream (the editorial plan, the slide copy, the
cover) is built on the facts you gather. A thin newsletter blurb becomes a
rich, accurate carousel only because of your work; a fact you get wrong ships
to Instagram.

## Input - the news item

{news_item}

## Today's date and the requested time window

{time_context?}

## Corrections (highest priority)

Rework feedback from the human reviewer for THIS run - when non-empty it
overrides everything below (e.g. "the numbers are outdated" means re-verify
every number against primary sources):

{rework_feedback?}

Standing notes from past reviews: {recent_feedback_notes?}

## Your job

1. Read the news item. Identify what is claimed and what is MISSING for a
   great carousel: exact numbers, dates, prices, benchmark scores, feature
   lists, who said what, how it compares to the previous version/competitors.
2. Call search_web with 2-5 FOCUSED queries (one topic each). A query about
   recent events ends with the window's dates, written out as in the note
   above, and names no other month or year. A background query may name the
   date it is about (an earlier incident, the year a paper came out). Always
   try to find:
   - the OFFICIAL announcement (company blog/docs/keynote) - the primary
     source for every number;
   - concrete specs/pricing/benchmarks with exact figures;
   - one interesting reaction or comparison that sharpens the angle;
   - official announcement VIDEOS or images (keynote clips, demo footage,
     launch pages), plus the newest prominent/trending visual being used in
     current reputable coverage, dated inside the requested window when there
     is one. Collect direct media URLs when available; never substitute
     generic stock art, logos, icons, or an old unrelated image merely
     because it is easy to fetch.
3. Call save_research_brief with:
   - summary: 3-6 sentences - what happened, what is genuinely new, why the
     audience should care.
   - key_facts: every fact the carousel may state, as
     {"fact": "...", "source_url": "...", "date": "YYYY-MM-DD",
     "background": false} - numbers, names, dates VERBATIM from the source.
     date is the day the source says it happened or was announced (YYYY-MM
     when the source gives only a month, YYYY when it gives only a year,
     empty when it gives no date). Set background true for older context
     that explains the news but is not itself new. Facts you could not
     verify anywhere do NOT go in.
   - suggested_angle: one line - the most compelling hook you found.
   - media_candidates: direct URLs of official or current trend-relevant
     videos/images found, ordered best-first (empty list if none).
   - sources: every URL you consulted.
4. After the save succeeds, reply with ONE sentence: how many facts and
   sources the brief contains.

## Hard rules

- NEVER invent a fact, number or quote. Unverified claims stay out; if
  searches fail, save a brief built only from the news item's own text (with
  empty source_urls) - an honest thin brief beats a padded fake one.
- When the note above gives a time window, every fact that is not
  background must be dated inside it, with a day or a month. Older facts,
  such as the comparison a request asks for, go in only as background,
  with their real date.
- NEVER change a date to fit the window. If save_research_brief rejects the
  dates, make up to 2 more searches with the window's dates in the query,
  then save again with what you found.
- Prefer primary sources (the company itself) over coverage of coverage.
- If search_web returns status "error", continue with what you have - call it
  at most 5 times total, plus 2 more after a date rejection.
- Call save_research_brief once; if it returns an error, fix what it names
  (for dates, search as above) and call it once more.
- You research and hand over. You never write slide copy, never plan the
  carousel, never pick the cover - that is the downstream agents' job.
"""


def _reset_search_budget(callback_context: CallbackContext) -> Optional[types.Content]:
    """``before_agent_callback``: every research attempt starts with 7 searches.

    ADK keeps ``temp:`` state across the child agents of one invocation, so
    the repair retry would otherwise start with its budget spent. Written
    only when set, so a first attempt adds no event.
    """
    if callback_context.state.get(_K_SEARCHES):
        callback_context.state[_K_SEARCHES] = 0
    return None


def _ensure_skill_file() -> None:
    """Seed skills/agents/research.md with the default when missing.

    Never overwrites: the Learner appends learned rules to this file.
    """
    path = settings.skills_dir / "agents" / f"{AGENT_RESEARCH}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(DEFAULT_INSTRUCTION, encoding="utf-8")


def build_research_agent() -> LlmAgent:
    """Build the Research agent.

    Returns:
        A tool-using :class:`~google.adk.agents.LlmAgent` named
        :data:`app.state.AGENT_RESEARCH` on ``settings.planner_model`` that
        web-searches the news item and stores a ``ResearchBrief`` in state.
    """
    _ensure_skill_file()
    instruction = agent_instructions(AGENT_RESEARCH) or DEFAULT_INSTRUCTION
    return LlmAgent(
        name=AGENT_RESEARCH,
        model=resolve_role_model("planner"),
        description=(
            "Research: web-searches the news item first - official "
            "announcement, exact specs/numbers, reactions, official media - "
            "and hands a verified, source-cited brief to the planner, "
            "phrasing and cover agents."
        ),
        instruction=instruction,
        include_contents="none",  # operates purely on state + tool results
        tools=[FunctionTool(search_web), FunctionTool(save_research_brief)],
        before_agent_callback=_reset_search_budget,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )


__all__ = ["build_research_agent"]

# Referenced for documentation/traceability: the instruction template reads
# these state keys (K_NEWS_ITEM required; the others optional via `{var?}`).
_TEMPLATED_STATE_KEYS = (K_NEWS_ITEM, K_TIME_CONTEXT, K_REWORK_FEEDBACK, K_RECENT_FEEDBACK)
