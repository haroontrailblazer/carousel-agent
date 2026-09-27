"""A cover is built before the step runs out of time, and after it if need be.

run-34fdc2b246c7 (BBC, an OpenAI system in Australia's health system) called
find_source_clip about 15 times, 1.5-2.7 minutes each, hit the 20-minute step
timeout with no cover, resumed and looped again. Nothing bounded the step and
nothing built a cover once it failed. These tests pin the step's clock
(K_COVER_DEADLINE), the tools' behaviour near it, the model-free salvage
(first_page_visual.ensure_cover) and the orchestrator calling it. Offline:
fakes only, no network, no billed call.
"""
import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from google.adk.sessions.state import State

from app import orchestrator as orch
from app import state as s
from app.agents import first_page_visual as fpv
from app.schemas import CarouselPlan
from app.state import set_model
from app.tools import media_tools
from test_cover_ladder import (  # noqa: F401 - fakes is a fixture
    TITLE, _Ctx, _file, _inspect, _news_state, _ref, _run, _spec, _verdict, fakes,
)

PLAN = CarouselPlan(style="points", slide_count=3, hook_title=TITLE).model_dump(mode="json")


def _late(ctx, seconds_left=60.0):
    ctx.state[s.K_COVER_DEADLINE] = time.time() + seconds_left


def _planned() -> _Ctx:
    """A context whose plan carries the hook, as by the time the cover runs."""
    ctx = _Ctx()
    ctx.state[s.K_PLAN] = dict(PLAN)
    return ctx


# ---------------------------------------------------------------------------
# The tools near the deadline
# ---------------------------------------------------------------------------


def test_near_the_deadline_the_slow_tools_stop_and_say_build(fakes, tmp_path):
    ctx = _Ctx()
    _news_state(ctx)
    _late(ctx)

    searched = _run(fpv.find_source_clip("anything", tool_context=ctx))
    assert fakes.searches == [] and "build the cover now" in searched["note"]
    looked = _run(fpv.find_reference_photo(tool_context=ctx))
    assert fakes.lookups == [] and "time is nearly up" in looked["note"]
    assert ctx.state.get("temp:cover_reference_tried") is True
    trimmed = _run(fpv.download_and_trim("https://v.example/a.mp4", tool_context=ctx))
    assert trimmed["ok"] is False and "build the cover now" in trimmed["error"]


def test_near_the_deadline_the_ladder_stops_waiting_for_the_reference_rung(fakes, tmp_path):
    ctx = _Ctx()
    chart = _file(tmp_path, "chart.png")
    fakes.answer = _verdict("text_graphic", "reject", 1, ["document_or_slide"])
    _inspect(ctx, chart)
    assert fpv._cover_refusal(ctx, str(chart))  # waits for find_reference_photo
    _late(ctx)
    assert fpv._cover_refusal(ctx, str(chart)) is None

    empty = _Ctx()
    background = _run(fpv.create_placeholder_background(tool_context=empty))["path"]
    assert fpv._cover_refusal(empty, background)
    _late(empty)
    assert fpv._cover_refusal(empty, background) is None


def test_a_hanging_search_is_abandoned_at_its_cap(fakes, monkeypatch):
    ctx = _Ctx()
    _news_state(ctx)
    ctx.state[s.K_COVER_DEADLINE] = time.time() + fpv._BUILD_RESERVE_S + 1.5

    def hangs(*_a, **_k):
        time.sleep(5)
        return {}

    monkeypatch.setattr(media_tools, "find_source_clip", hangs)
    monkeypatch.setattr(fpv, "_SEARCH_CAP_S", 0.5)
    monkeypatch.setattr(fpv, "_SEARCH_SLACK_S", 0.5)
    async def timed():
        # Timed inside the loop: asyncio.run waits for the abandoned thread
        # at shutdown, a long-lived server loop does not.
        started = time.monotonic()
        result = await fpv.find_source_clip("slow", tool_context=ctx)
        return result, time.monotonic() - started

    result, took = _run(timed())
    assert took < 3.0
    assert result["found"] is False and "ran out of time" in result["note"]


def test_the_search_gets_its_budget_the_subjects_and_the_page_cache(fakes):
    ctx = _Ctx()
    _news_state(ctx)
    ctx.state[s.K_COVER_DEADLINE] = time.time() + 900
    _run(fpv.find_source_clip("first", tool_context=ctx))
    kwargs = fakes.searches[-1]
    assert 0 < kwargs["budget_s"] <= fpv._SEARCH_CAP_S
    assert kwargs["subjects"] == ["Anthony Albanese", "Canberra"]
    assert kwargs["page_cache"] is ctx.state[fpv._K_PAGE_CACHE]


# ---------------------------------------------------------------------------
# ensure_cover: the salvage without the model
# ---------------------------------------------------------------------------


def test_the_salvage_builds_best_so_far(fakes, tmp_path):
    ctx = _planned()
    photo = _file(tmp_path, "photo.jpg")
    fakes.answer = _verdict("real_photo", "reject", 5, ["publisher_branding"])
    _inspect(ctx, photo)
    built = _run(fpv.ensure_cover(ctx, budget_s=60))
    assert built["ok"] and built["salvaged_from"] == str(photo)
    spec = _spec(ctx)
    assert (spec.title, spec.video_artifact, spec.picture_verdict) == (TITLE, fpv.COVER_VIDEO_ARTIFACT, "reject")


def test_the_salvage_uses_a_downloaded_picture_before_the_background(fakes, tmp_path):
    """Nothing inspected, a reference photo downloaded: the photo, not the plain background."""
    ctx = _planned()
    url = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Hq.jpg"
    fakes.refs = [_ref(url)]
    fakes.downloads = {url: "hq.jpg"}
    _run(fpv.find_reference_photo(tool_context=ctx))
    path = _run(fpv.download_image(url, tool_context=ctx))["path"]
    built = _run(fpv.ensure_cover(ctx, budget_s=60))
    assert built["ok"] and Path(built["salvaged_from"]) == Path(path)
    assert _spec(ctx).drawn_background is False


def test_the_salvage_tries_a_reference_photo_then_the_background(fakes, tmp_path):
    ctx = _planned()
    _news_state(ctx)
    url = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Canberra.jpg"
    fakes.refs = [_ref(url, subject="Canberra")]
    fakes.downloads = {url: "canberra.jpg"}
    built = _run(fpv.ensure_cover(ctx, budget_s=120))
    assert built["ok"] and Path(built["salvaged_from"]).name == "canberra.jpg"
    assert _spec(ctx).source_origin == "wikimedia"

    empty = _planned()
    fakes.refs = []
    built = _run(fpv.ensure_cover(empty, budget_s=120))
    assert built["ok"] and built["salvaged_from"] == "drawn background"
    assert _spec(empty).drawn_background is True


def test_the_salvage_never_uses_third_party_ai_art(fakes, tmp_path):
    ctx = _planned()
    art = _file(tmp_path, "art.png")
    fakes.answer = _verdict("ai_art", "reject", 9, ["ai_illustration"])
    _inspect(ctx, art)
    built = _run(fpv.ensure_cover(ctx, budget_s=60))
    assert built["ok"] and built["salvaged_from"] == "drawn background"
    assert str(art) not in fakes.compose


# ---------------------------------------------------------------------------
# The orchestrator: deadline stamped, salvage on failure
# ---------------------------------------------------------------------------


class _CoverPipeline:
    """test_pipeline_audit.Pipeline with a cover step that can time out."""

    def __init__(self, cover_fails: int, salvage_ok: bool = True):
        import test_pipeline_audit as audit

        self.inner = audit.Pipeline()
        self.cover_fails = cover_fails
        self.salvage_ok = salvage_ok
        self.salvages: list[str] = []
        self.deadlines: list[float] = []
        self.audit = audit

    async def run(self):
        inner = self.inner
        real_drive = inner.drive

        async def drive(agent, child, ctx, holder):
            if child == s.AGENT_FIRST_PAGE_VISUAL:
                self.deadlines.append(inner.state.get(s.K_COVER_DEADLINE))
                if self.cover_fails:
                    self.cover_fails -= 1
                    inner.calls.append(child)
                    raise orch.StepTimeoutError("first_page_visual did not finish within 20 minutes.")
            async for event in real_drive(agent, child, ctx, holder):
                yield event

        async def salvage(agent, ctx, reason):
            self.salvages.append(reason)
            delta = {s.K_COVER: self.audit.outputs()[s.K_COVER]} if self.salvage_ok else {}
            yield agent._progress(ctx, "[recovery] salvaged", delta)

        with patch.object(orch.CarouselOrchestrator, "_child", side_effect=lambda name: name), \
                patch.object(orch.CarouselOrchestrator, "_drive", drive), \
                patch.object(orch.CarouselOrchestrator, "_salvage_cover", salvage), \
                patch.object(orch.CarouselOrchestrator, "_record_phase_quietly", AsyncMock()), \
                patch.object(orch.CarouselOrchestrator, "_name_run_quietly", AsyncMock()):
            async for event in inner.agent._run_async_impl(inner.ctx):
                inner.state.update(event.actions.state_delta)


@pytest.mark.asyncio
async def test_a_cover_step_that_times_out_is_salvaged_and_the_run_goes_on():
    p = _CoverPipeline(cover_fails=1)
    started = time.time()
    await p.run()
    assert p.salvages and "20 minutes" in p.salvages[0]
    assert p.inner.state[s.K_PHASE] == "review"
    assert p.inner.calls.count(s.AGENT_FIRST_PAGE_VISUAL) == 1
    # The agent's own deadline sits well inside the 20-minute step limit.
    [deadline] = p.deadlines
    assert started + orch._COVER_AGENT_BUDGET_S - 5 <= deadline <= time.time() + orch._COVER_AGENT_BUDGET_S
    assert orch._COVER_AGENT_BUDGET_S + fpv._BUILD_RESERVE_S <= orch._STEP_TIMEOUT_S


@pytest.mark.asyncio
async def test_a_cover_step_that_ends_twice_without_a_cover_is_salvaged():
    p = _CoverPipeline(cover_fails=0)
    p.inner.fail[s.AGENT_FIRST_PAGE_VISUAL] = 2
    await p.run()
    assert len(p.salvages) == 1
    assert p.inner.state[s.K_PHASE] == "review"
    assert p.inner.calls.count(s.AGENT_FIRST_PAGE_VISUAL) == 2


@pytest.mark.asyncio
async def test_when_even_the_salvage_fails_the_step_fails_as_before():
    p = _CoverPipeline(cover_fails=1, salvage_ok=False)
    with pytest.raises(orch.StepTimeoutError):
        await p.run()
    assert p.inner.state["generation_completed"] == ["research", "planner"]


@pytest.mark.asyncio
async def test_other_steps_still_fail_on_a_timeout():
    import test_pipeline_audit as audit

    inner = audit.Pipeline()

    async def drive(agent, child, ctx, holder):
        if child == s.AGENT_PHRASING:
            raise orch.StepTimeoutError("phrasing did not finish within 20 minutes.")
        async for event in inner.drive(agent, child, ctx, holder):
            yield event

    with patch.object(orch.CarouselOrchestrator, "_child", side_effect=lambda name: name), \
            patch.object(orch.CarouselOrchestrator, "_drive", drive), \
            patch.object(orch.CarouselOrchestrator, "_record_phase_quietly", AsyncMock()), \
            patch.object(orch.CarouselOrchestrator, "_name_run_quietly", AsyncMock()):
        with pytest.raises(orch.StepTimeoutError):
            async for event in inner.agent._run_async_impl(inner.ctx):
                inner.state.update(event.actions.state_delta)


@pytest.mark.asyncio
async def test_the_real_salvage_event_carries_the_cover_and_its_artifacts(fakes, tmp_path):
    """_salvage_cover runs ensure_cover on a tool context and yields its changes."""
    tool_ctx = SimpleNamespace(actions=SimpleNamespace(state_delta={}, artifact_delta={}))
    tool_ctx.state = State({s.K_PLAN: dict(PLAN)}, tool_ctx.actions.state_delta)
    saved: list[str] = []

    async def save_artifact(filename, part):
        saved.append(filename)
        tool_ctx.actions.artifact_delta[filename] = len(saved)
        return len(saved)

    tool_ctx.save_artifact = save_artifact
    agent = orch.CarouselOrchestrator(name="audit")
    ctx = SimpleNamespace(invocation_id="i", branch=None, session=SimpleNamespace(state={}))
    with patch.object(orch.CarouselOrchestrator, "_cover_tool_context", return_value=tool_ctx):
        events = [event async for event in agent._salvage_cover(ctx, "timed out")]
    [event] = events
    assert event.actions.state_delta[s.K_COVER]["drawn_background"] is True
    assert event.actions.artifact_delta == {fpv.COVER_VIDEO_ARTIFACT: 1, fpv.COVER_POSTER_ARTIFACT: 2}
    assert "built the cover from" in event.content.parts[0].text
    assert fakes.compose  # the fakes' renderer composed it


def test_a_download_with_no_time_left_is_not_tried_or_remembered_as_failed(fakes):
    ctx = _Ctx()
    url = "https://img.example/photo.jpg"
    fakes.downloads = {url: "photo.jpg"}
    _late(ctx, seconds_left=30)
    refused = _run(fpv.download_image(url, tool_context=ctx))
    assert refused["ok"] is False and "build the cover now" in refused["error"]
    assert not ctx.state.get("temp:cover_failed_urls")


def test_a_tiny_search_budget_is_not_read_as_no_budget():
    """budget_s=0 once meant the default 150 s."""
    news = {"title": "Widget Agent launch", "source_url": "https://news.example.com/a"}

    def slow(url):
        time.sleep(2)
        return []

    with patch.object(media_tools, "_scrape_page_media", side_effect=slow), \
            patch.object(media_tools, "_search_trending_pages", return_value=([], "x")), \
            patch.object(media_tools, "_probe_image_sizes", return_value={}), \
            patch.object(media_tools, "_search_video_online", return_value=None):
        started = time.monotonic()
        media_tools.find_source_clip(news, budget_s=0)
    assert time.monotonic() - started < 1.9
