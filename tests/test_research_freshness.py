"""Research, the search model and the writers all know today's date and the window.

On 26 September 2026 a topic asking for "this week" got March launches
(run-3c4107e390df): no prompt or search call carried the date, and a brief of
stale facts sailed through to the carousel. These tests pin "today", replace
the paid search and LLM calls with fakes, and check that the window reaches
every step that searches or writes, and that a stale brief is sent back.
"""
import re
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from google.adk.agents import SequentialAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.genai import types
from openai import OpenAIError
from pydantic import Field

from app import orchestrator as orch
from app import state as s
from app import time_window
from app.agents import phrasing, planner, research
from app.cover_notice import COVER_WARNING
from app.pipeline_outputs import OUTPUT_KEYS, validate_output
from app.schemas import CarouselPlan, CopySet
from app.services import db
from app.tools import research_tools

REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 26, 6, 24, tzinfo=timezone.utc)
WEEK_TOPIC = "this week major ai updates like new model launches and the comparative open weight models"
WEEK = time_window.resolve({"source_name": "adhoc", "title": WEEK_TOPIC, "body": WEEK_TOPIC}, NOW)
RANGE = "20 September 2026 to 26 September 2026"

STALE = {"fact": "Mistral announced Mistral Small 4 on March 16, 2026.",
         "source_url": "https://mistral.ai/news/mistral-small-4/"}
FRESH = {"fact": "Xiaomi released and open-sourced MiMo-V2.6.",
         "source_url": "https://mimo.mi.com/docs/en-US/news/latest/v2-6", "date": "2026-09-22"}


@pytest.fixture(autouse=True)
def saturday(monkeypatch):
    """Every test runs on the day of the stale run: Saturday 26 September 2026."""
    monkeypatch.setattr(time_window, "today_utc", lambda: date(2026, 9, 26))


# --- save_research_brief --------------------------------------------------


def _tool_context(window=None):
    state = {s.K_NEWS_ITEM: {"id": "n", "title": WEEK_TOPIC, "media_urls": []}}
    if window is not None:
        state[s.K_TIME_WINDOW] = window
    return SimpleNamespace(state=state)


def _save(ctx, facts, media=("https://mimo.mi.com/static/v2-6-cover.png",)):
    return research_tools.save_research_brief(
        summary="What launched.", key_facts=deepcopy(facts), suggested_angle="Open weights got fast.",
        media_candidates=list(media), sources=[], tool_context=ctx)


def test_a_stale_brief_is_stored_but_rejected_and_keeps_its_media_from_the_cover():
    ctx = _tool_context(WEEK)
    result = _save(ctx, [STALE])
    assert result["status"] == "error"
    assert f'Facts must be dated inside {RANGE} ("this week", UTC)' in result["message"]
    assert "fact 1 is dated 16 March 2026" in result["message"]
    assert "Do not change dates." in result["message"]
    assert ctx.state[s.K_RESEARCH]["key_facts"][0]["fact"] == STALE["fact"]
    assert ctx.state[s.K_NEWS_ITEM]["media_urls"] == []


def test_in_window_facts_and_labelled_background_are_saved_with_their_media():
    ctx = _tool_context(WEEK)
    fresh = dict(FRESH, date="2026-09-22T09:00:00Z")
    result = _save(ctx, [fresh, dict(STALE, background="true")])
    assert result == {"status": "saved", "fact_count": 2, "media_added": 1}
    saved = ctx.state[s.K_RESEARCH]["key_facts"]
    assert [(f["date"], f["background"]) for f in saved] == [("2026-09-22", False), ("", True)]
    assert ctx.state[s.K_NEWS_ITEM]["media_urls"] == ["https://mimo.mi.com/static/v2-6-cover.png"]


def test_without_a_window_undated_facts_save_as_before():
    ctx = _tool_context()
    assert _save(ctx, [{"fact": "Transformers use attention.", "source_url": ""}])["status"] == "saved"


@pytest.mark.parametrize("raw, stored", [
    ("September 22, 2026", "2026-09-22"),
    ("2026-09", "2026-09"),
    ("2026-09-24 14:08", "2026-09-24"),
    ("", ""),
    ("2017", "2017"),
    ("N/A", ""),
    ("unknown", ""),
    ("22/09/2026", "2026-09-22"),
    ("2026-9-22", "2026-09-22"),
    ("Q3 2026", "2026"),
    ("early 2026", "2026"),
    ("December 5", "2025-12-05"),
])
def test_fact_dates_are_normalised(raw, stored):
    ctx = _tool_context()
    assert _save(ctx, [dict(FRESH, date=raw, background="false")])["status"] == "saved"
    assert ctx.state[s.K_RESEARCH]["key_facts"][0]["date"] == stored
    assert ctx.state[s.K_RESEARCH]["key_facts"][0]["background"] is False


def test_an_unreadable_date_is_an_error_only_under_a_strict_window():
    """An evergreen brief with a date like 'soon' was refused whole, though
    nothing reads its dates."""
    ctx = _tool_context()
    assert _save(ctx, [dict(FRESH, date="soon")])["status"] == "saved"
    assert ctx.state[s.K_RESEARCH]["key_facts"][0]["date"] == ""

    ctx = _tool_context(WEEK)
    result = _save(ctx, [dict(FRESH, date="soon"), dict(STALE, date="05/09/2026")])
    assert result["status"] == "error" and "YYYY-MM-DD, YYYY-MM or YYYY" in result["message"]
    # Every unreadable value in one message.
    assert "fact 1: 'soon'" in result["message"] and "fact 2: '05/09/2026'" in result["message"]
    assert s.K_RESEARCH not in ctx.state


def test_the_save_tool_applies_the_rule_validate_output_will():
    """A brief the pipeline accepts on its last attempt lost its media to the
    cover, and the model was told to search again for nothing."""
    mixed = [FRESH, dict(STALE, date="2026-03-16"), dict(STALE, date="2026-03-17")]
    ctx = _tool_context(WEEK)
    result = _save(ctx, mixed)
    assert result["status"] == "error" and "only 1 of the 3 facts" in result["message"]
    # One fact is current, so its media reaches the cover even now.
    assert ctx.state[s.K_NEWS_ITEM]["media_urls"] == ["https://mimo.mi.com/static/v2-6-cover.png"]

    ctx = _tool_context(WEEK)
    ctx.state[s.K_RESEARCH_RELAXED] = True
    assert _save(ctx, mixed) == {"status": "saved", "fact_count": 3, "media_added": 1}
    validate_output(s.AGENT_RESEARCH, ctx.state, final_attempt=True)

    # With no current fact at all, nothing passes and no media moves.
    ctx = _tool_context(WEEK)
    ctx.state[s.K_RESEARCH_RELAXED] = True
    assert _save(ctx, [STALE])["status"] == "error"
    assert ctx.state[s.K_NEWS_ITEM]["media_urls"] == []


def test_comparison_facts_marked_background_do_not_fail_the_brief():
    """run-3c41 asked for launches "and the comparative open weight models"."""
    ctx = _tool_context(WEEK)
    fresh = [dict(FRESH, date=day) for day in ("2026-09-22", "2026-09-22", "2026-09-24")]
    background = [dict(STALE, date="2026-03-16", background=True)] * 4
    assert _save(ctx, fresh + background)["status"] == "saved"
    validate_output(s.AGENT_RESEARCH, ctx.state)


# --- validate_output ------------------------------------------------------


def _brief_state(facts, window=WEEK):
    state = {s.K_RESEARCH: {"summary": "What launched.", "key_facts": facts}}
    if window is not None:
        state[s.K_TIME_WINDOW] = window
    return state


def test_validate_output_sends_a_stale_brief_back_naming_the_window():
    with pytest.raises(ValueError) as caught:
        validate_output(s.AGENT_RESEARCH, _brief_state([STALE]))
    assert str(caught.value).startswith(f'Research must use facts dated inside {RANGE} ("this week", UTC): ')
    assert "name exact dates / drop the time word" in str(caught.value)
    # A checkpoint was accepted when it was saved; a resume never re-bills it.
    validate_output(s.AGENT_RESEARCH, _brief_state([STALE]), checkpoint=True)


def test_the_last_attempt_and_rework_need_only_one_current_fact():
    mixed = _brief_state([FRESH, STALE, STALE])
    with pytest.raises(ValueError, match="only 1 of the 3 facts"):
        validate_output(s.AGENT_RESEARCH, mixed)
    validate_output(s.AGENT_RESEARCH, mixed, final_attempt=True)
    validate_output(s.AGENT_RESEARCH, mixed, under_rework=True)
    with pytest.raises(ValueError, match="no fact is dated inside"):
        validate_output(s.AGENT_RESEARCH, _brief_state([STALE]), final_attempt=True)


@pytest.mark.parametrize("window", [
    None,
    time_window.resolve({"source_name": "adhoc", "title": "how transformers work"}, NOW),
])
def test_evergreen_topics_are_not_date_checked(window):
    validate_output(s.AGENT_RESEARCH, _brief_state([{"fact": "Transformers use attention."}], window))


# --- search_web -----------------------------------------------------------


class FakeResponses:
    """Stands in for the paid OpenAI Responses call and records its arguments."""

    def __init__(self, failures=0):
        self.calls, self.failures = [], failures

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.failures:
            self.failures -= 1
            raise OpenAIError("flaky network")
        return SimpleNamespace(output_text="Xiaomi released MiMo-V2.6 on 22 September 2026.", output=[])


@pytest.fixture
def search(monkeypatch):
    responses = FakeResponses()
    sleeps = []
    monkeypatch.setattr(research_tools, "_client", lambda: SimpleNamespace(responses=responses))
    monkeypatch.setattr(research_tools, "_search_model_id", lambda: "offline-search")
    monkeypatch.setattr(research_tools.time, "sleep", sleeps.append)
    return SimpleNamespace(responses=responses, sleeps=sleeps)


def test_search_instructions_carry_today_and_the_window(search):
    assert research_tools.search_web("open-weight model launches", WEEK)["status"] == "ok"
    call = search.responses.calls[0]
    assert "Today's date is 26 September 2026 (UTC)" in call["instructions"]
    assert RANGE in call["instructions"]
    assert "state the publication or announcement date of every fact" in call["instructions"]
    assert call["timeout"] == 120.0
    research_tools.search_web("how attention works")
    assert "26 September 2026 (UTC)" in search.responses.calls[1]["instructions"]
    assert RANGE not in search.responses.calls[1]["instructions"]


def test_one_attempt_means_no_retry_and_no_sleep(search):
    search.responses.failures = 1
    result = research_tools.search_web("cover trend", WEEK, timeout_s=45, attempts=1)
    assert result["status"] == "error"
    assert [c["timeout"] for c in search.responses.calls] == [45]
    assert search.sleeps == []
    search.responses.failures = 1
    assert research_tools.search_web("cover trend")["status"] == "ok"
    assert len(search.responses.calls) == 3 and len(search.sleeps) == 1


@pytest.mark.asyncio
async def test_searches_are_capped_per_research_attempt(search):
    """Only the prompt limited billed searches; date rejections could repeat them."""
    tool = FunctionTool(research.search_web)
    ctx = SimpleNamespace(state={s.K_TIME_WINDOW: WEEK})
    results = [await tool.run_async(args={"query": f"MiMo launch {n}"}, tool_context=ctx)
               for n in range(research._MAX_SEARCHES + 1)]
    assert [r["status"] for r in results] == ["ok"] * research._MAX_SEARCHES + ["error"]
    assert "Search budget used" in results[-1]["message"]
    assert len(search.responses.calls) == research._MAX_SEARCHES
    # The repair retry starts with a fresh budget.
    callback = SimpleNamespace(state=ctx.state)
    assert research._reset_search_budget(callback) is None
    assert (await tool.run_async(args={"query": "again"}, tool_context=ctx))["status"] == "ok"
    assert research.build_research_agent().before_agent_callback is research._reset_search_budget


@pytest.mark.asyncio
async def test_the_retry_and_rework_tell_the_save_tool_to_relax():
    mixed = {"summary": "One new launch, two old ones.", "key_facts": [deepcopy(FRESH), deepcopy(STALE),
                                                                         deepcopy(STALE)]}
    run = Run(research_briefs=[deepcopy(mixed), deepcopy(mixed)])
    relaxed = []
    drive = run.drive

    async def recording(agent, child, ctx, holder):
        if child == s.AGENT_RESEARCH:
            relaxed.append(run.state.get(s.K_RESEARCH_RELAXED))
        async for event in drive(agent, child, ctx, holder):
            yield event

    run.drive = recording
    await run.run()
    assert relaxed == [False, True]


@pytest.mark.asyncio
async def test_the_agent_tool_reads_the_window_from_state(search):
    tool = FunctionTool(research.search_web)
    result = await tool.run_async(args={"query": "Xiaomi MiMo launch 20 to 26 September 2026"},
                                  tool_context=SimpleNamespace(state={s.K_TIME_WINDOW: WEEK}))
    assert result["status"] == "ok" and "window_note" not in result
    assert RANGE in search.responses.calls[-1]["instructions"]
    stale = await tool.run_async(args={"query": "Mistral model launch March 2026"},
                                 tool_context=SimpleNamespace(state={s.K_TIME_WINDOW: WEEK}))
    assert stale["status"] == "ok" and RANGE in stale["window_note"]
    # A context without state (the responsiveness test's) still searches.
    bare = await tool.run_async(args={"query": "Test announcement"}, tool_context=SimpleNamespace())
    assert bare["status"] == "ok"
    assert RANGE not in search.responses.calls[-1]["instructions"]


# --- prompts --------------------------------------------------------------


def test_research_skill_file_matches_the_default_and_its_placeholders():
    text = (REPO / "skills" / "agents" / "research.md").read_text(encoding="utf-8")
    assert text == research.DEFAULT_INSTRUCTION
    groups = re.findall(r"{+([^{}]*)}+", text)
    names = {g for g in groups if re.fullmatch(r"\w+\??", g)}
    assert names == {"news_item", "time_context?", "rework_feedback?", "recent_feedback_notes?"}
    # The only other brace group is the key_facts example, which ADK leaves alone.
    assert [g for g in groups if g not in names] == [
        '"fact": "...", "source_url": "...", "date": "YYYY-MM-DD",\n     "background": false']
    assert research._TEMPLATED_STATE_KEYS[1] == s.K_TIME_CONTEXT


class RecordingModel(BaseLlm):
    """Replace only the paid model and keep the instruction ADK really sends."""
    reply: str
    instructions: list = Field(default_factory=list)

    async def generate_content_async(self, llm_request, stream=False):
        self.instructions.append(str(llm_request.config.system_instruction))
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=self.reply)]))


async def _instruction_sent(agent, state: dict) -> None:
    sessions = InMemorySessionService()
    runner = Runner(app_name="freshness_test", agent=SequentialAgent(name="step", sub_agents=[agent]),
                    session_service=sessions)
    await sessions.create_session(app_name="freshness_test", user_id="test", session_id="run", state=state)
    try:
        async for _event in runner.run_async(user_id="test", session_id="run", new_message=types.Content(
                role="user", parts=[types.Part(text="Do your step.")])):
            pass
    finally:
        await runner.close()


PLAN = {"style": "points", "slide_count": 3, "hook_title": "A faster release",
        "slides": [{"index": 2, "purpose": "Explain the change", "key_points": ["Local processing is supported."]}]}
COPY = {"slides": [{"index": 2, "lines": ["Local processing", "The new release supports local processing."]}],
        "caption": "The facts behind the release. #ai #models #launch"}


@pytest.mark.asyncio
@pytest.mark.parametrize("module, build, reply", [
    (research, research.build_research_agent, "Saved 1 fact."),
    (planner, planner.build_planner_agent, CarouselPlan.model_validate(PLAN).model_dump_json()),
    (phrasing, phrasing.build_phrasing_agent, CopySet.model_validate(COPY).model_dump_json()),
])
async def test_each_searching_or_writing_agent_is_told_the_date(monkeypatch, module, build, reply):
    model = RecordingModel(model="offline", reply=reply)
    monkeypatch.setattr(module, "resolve_role_model", lambda role: model)
    note = time_window.context_note(WEEK, date(2026, 9, 26))
    await _instruction_sent(build(), {
        s.K_NEWS_ITEM: {"id": "news", "title": WEEK_TOPIC}, s.K_PLAN: PLAN, s.K_TIME_CONTEXT: note})
    instruction = model.instructions[0]
    assert note in instruction
    assert "{time_context?}" not in instruction
    assert "## Today's date and the requested time window" in instruction


# --- orchestrator ---------------------------------------------------------


def _outputs():
    return {
        s.K_RESEARCH: {"summary": "Xiaomi opened MiMo-V2.6.", "key_facts": [deepcopy(FRESH)]},
        s.K_PLAN: deepcopy(PLAN),
        s.K_COVER: {"title": "A faster release", "poster_artifact": "cover.png", "video_artifact": "cover.mp4",
                    "duration_s": 6},
        s.K_COPY: deepcopy(COPY),
        s.K_BODY_SLIDES: [{"index": 2, "artifact": "slide_2.png"}],
        s.K_CTA_SLIDE: {"cta_type": "follow", "artifact": "cta.png"},
    }


class Run:
    """The orchestrator with every child agent replaced by its saved output.

    Account-free, so the run stops at the review screen with no reviewer.
    """

    def __init__(self, title=WEEK_TOPIC, *, research_briefs=None, drawn_cover=False):
        self.state = {s.K_NEWS_ITEM: {"id": "news", "title": title, "body": title, "source_name": "adhoc"},
                      s.K_DESIGN: {"max_slides": 3}}
        self.ctx = SimpleNamespace(session=SimpleNamespace(state=self.state), invocation_id="fresh",
                                   branch=None, user_content=None, memory_service=None)
        self.briefs = list(research_briefs or [])
        self.drawn_cover = drawn_cover
        self.calls, self.seen, self.texts = [], {}, []
        self.agent = orch.CarouselOrchestrator(name="freshness")

    async def drive(self, agent, child, ctx, holder):
        self.calls.append(child)
        self.seen.setdefault(child, []).append(
            (self.state.get(s.K_TIME_CONTEXT), self.state.get(s.K_REWORK_FEEDBACK)))
        if child == s.AGENT_RESEARCH and self.briefs:
            yield agent._progress(ctx, child, {s.K_RESEARCH: self.briefs.pop(0)})
        elif child in OUTPUT_KEYS:
            yield agent._progress(ctx, child, {OUTPUT_KEYS[child]: _outputs()[OUTPUT_KEYS[child]]})
        elif child == s.AGENT_STITCH_VERIFY:
            cover = dict(_outputs()[s.K_COVER], drawn_background=self.drawn_cover)
            yield agent._progress(ctx, "qa", {s.K_QA_REPORT: {"passed": True, "issues": []},
                                              s.K_BUNDLE: {"cover": cover}})

    async def run(self):
        async def drive(agent, child, ctx, holder):
            async for event in self.drive(agent, child, ctx, holder):
                yield event
        with patch.object(orch.CarouselOrchestrator, "_child", side_effect=lambda name: name), \
                patch.object(orch.CarouselOrchestrator, "_drive", drive), \
                patch.object(orch.CarouselOrchestrator, "_record_phase_quietly", AsyncMock()), \
                patch.object(orch.CarouselOrchestrator, "_name_run_quietly", AsyncMock()), \
                patch.object(db, "create_run", AsyncMock()):
            async for event in self.agent._run_async_impl(self.ctx):
                self.texts.extend(part.text for part in (event.content.parts if event.content else []) if part.text)
                self.state.update(event.actions.state_delta)


@pytest.mark.asyncio
async def test_the_window_is_stamped_once_and_reaches_research_planner_and_phrasing():
    run = Run()
    await run.run()
    assert run.state[s.K_PHASE] == s.PHASE_REVIEW
    window = run.state[s.K_TIME_WINDOW]
    assert (window["requested_on"], window["start"], window["end"], window["strict"]) == (
        "2026-09-26", "2026-09-20", "2026-09-26", True)
    for agent in (s.AGENT_RESEARCH, s.AGENT_PLANNER, s.AGENT_PHRASING):
        note = run.seen[agent][0][0]
        assert note.startswith("Today is Saturday, 26 September 2026 (UTC).") and RANGE in note
    assert f'[generate] preparing research (dates {RANGE} for "this week", UTC)' in run.texts
    assert not any(COVER_WARNING in text for text in run.texts)


@pytest.mark.asyncio
async def test_a_stale_brief_gets_a_repair_note_naming_the_window():
    stale = {"summary": "Mistral launched Small 4.", "key_facts": [deepcopy(STALE)]}
    run = Run(research_briefs=[stale, {"summary": "Xiaomi opened MiMo-V2.6.", "key_facts": [deepcopy(FRESH)]}])
    await run.run()
    assert run.calls.count(s.AGENT_RESEARCH) == 2
    retry_feedback = run.seen[s.AGENT_RESEARCH][1][1]
    assert f'Research must use facts dated inside {RANGE} ("this week", UTC)' in retry_feedback
    assert run.state[s.K_PHASE] == s.PHASE_REVIEW
    assert "Repair the research output" not in (run.state.get(s.K_REWORK_FEEDBACK) or "")


@pytest.mark.asyncio
async def test_the_retry_passes_with_one_current_fact_among_older_ones():
    mixed = {"summary": "One new launch, two old ones.", "key_facts": [deepcopy(FRESH), deepcopy(STALE),
                                                                         deepcopy(STALE)]}
    run = Run(research_briefs=[deepcopy(mixed), deepcopy(mixed)])
    await run.run()
    assert run.calls.count(s.AGENT_RESEARCH) == 2
    assert run.state[s.K_PHASE] == s.PHASE_REVIEW


@pytest.mark.asyncio
async def test_two_stale_briefs_stop_the_run_and_a_later_resume_keeps_the_request_day(monkeypatch):
    stale = {"summary": "Mistral launched Small 4.", "key_facts": [deepcopy(STALE)]}
    run = Run(research_briefs=[deepcopy(stale), deepcopy(stale)])
    with pytest.raises(RuntimeError, match="research could not finish: .*drop the time word.*"
                       "The requested dates are fixed for this run.*start a new run"):
        await run.run()
    assert s.AGENT_PLANNER not in run.calls

    monkeypatch.setattr(time_window, "today_utc", lambda: date(2026, 9, 28))
    run.briefs = [{"summary": "Xiaomi opened MiMo-V2.6.", "key_facts": [deepcopy(FRESH)]}]
    await run.run()
    assert run.state[s.K_TIME_WINDOW]["requested_on"] == "2026-09-26"
    assert run.state[s.K_TIME_WINDOW]["start"] == "2026-09-20"
    note = run.seen[s.AGENT_RESEARCH][-1][0]
    assert "Today is Monday, 28 September 2026 (UTC)." in note
    assert "The request was made on 26 September 2026." in note and RANGE in note
    assert run.state[s.K_PHASE] == s.PHASE_REVIEW


@pytest.mark.asyncio
async def test_a_run_from_before_the_window_existed_resolves_it_at_research():
    run = Run()
    run.state.update({s.K_RUN_ID: "old-run", s.K_PHASE: s.PHASE_GENERATE})
    await run.run()
    assert run.state[s.K_TIME_WINDOW]["start"] == "2026-09-20"
    assert RANGE in run.seen[s.AGENT_RESEARCH][0][0]


@pytest.mark.asyncio
async def test_an_evergreen_topic_is_told_the_date_but_no_window():
    run = Run("how transformers work")
    await run.run()
    assert run.state[s.K_TIME_WINDOW]["start"] == ""
    assert "no date window applies" in run.seen[s.AGENT_RESEARCH][0][0]
    assert "[generate] preparing research" in run.texts


@pytest.mark.asyncio
async def test_the_account_free_review_line_warns_about_a_drawn_cover():
    run = Run(drawn_cover=True)
    await run.run()
    review = [text for text in run.texts if text.startswith("[review]")]
    assert len(review) == 1 and review[0].endswith(" " + COVER_WARNING)
