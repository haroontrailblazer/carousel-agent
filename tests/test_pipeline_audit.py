"""Offline end-to-end orchestration and recovery regressions (no paid or live posts)."""
import asyncio
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from PIL import Image
from google.genai import types

from app import orchestrator as orch
from app import state as s
from app.agents import publisher, stitch_verify
from app.pipeline_outputs import OUTPUT_KEYS, validate_output
from app.review import eligibility, verdict
from app.schemas import CarouselDesign
from app.services import db, publish_receipts
from app.tools import image_gen, instagram_tools
from web_api import routes_runs
from web_api.auth import Identity


def outputs():
    return {
        s.K_RESEARCH: {"summary": "A verified release adds faster local processing.", "key_facts": [{"fact": "The release supports local processing.", "source_url": "https://example.com/release"}]},
        s.K_PLAN: {"style": "points", "slide_count": 3, "hook_title": "A faster release", "slides": [{"index": 2, "purpose": "Explain the change", "key_points": ["Local processing is supported."]}]},
        s.K_COVER: {"title": "A faster release", "poster_artifact": "cover.png", "video_artifact": "cover.mp4", "duration_s": 6},
        s.K_COPY: {"slides": [{"index": 2, "lines": ["Local processing", "The new release supports local processing."]}], "caption": "The facts behind the release."},
        s.K_BODY_SLIDES: [{"index": 2, "artifact": "slide_2.png"}],
        s.K_CTA_SLIDE: {"cta_type": "follow", "artifact": "cta.png"},
    }


@pytest.fixture
def receipt_store(monkeypatch):
    store = {}
    async def update(key, fn):
        store[key] = deepcopy(fn(deepcopy(store.get(key, {}))))
        await asyncio.sleep(0)
        return deepcopy(store[key])
    async def set_value(key, value):
        store[key] = deepcopy(value)
    monkeypatch.setattr(db, "update_config", update)
    monkeypatch.setattr(db, "set_config", set_value)
    return store


class Pipeline:
    def __init__(self, account="account-a"):
        self.state = {s.K_RUN_ID: "audit-run", s.K_PHASE: "generate", s.K_ACCOUNT_ID: account, s.K_NEWS_ITEM: {"id": "news", "title": "A release"}, s.K_DESIGN: {"max_slides": 3}}
        self.ctx = SimpleNamespace(session=SimpleNamespace(state=self.state), invocation_id="audit", branch=None)
        self.calls = []
        self.fail = {}
        self.qa_failures = 0
        self.stale_qa = False
        self.agent = orch.CarouselOrchestrator(name="audit")

    async def drive(self, agent, child, ctx, holder):
        self.calls.append(child)
        if self.fail.get(child, 0):
            self.fail[child] -= 1
            return
        if child in OUTPUT_KEYS:
            yield agent._progress(ctx, child, {OUTPUT_KEYS[child]: deepcopy(outputs()[OUTPUT_KEYS[child]])})
        elif child == s.AGENT_STITCH_VERIFY:
            if self.stale_qa:
                return
            with patch.object(stitch_verify, "_existing_artifacts", AsyncMock(return_value={"cover.png", "cover.mp4", "slide_2.png", "cta.png"})), patch.object(stitch_verify, "_verify_rendered_png", AsyncMock(return_value=None)), patch.object(stitch_verify, "_verify_brand_padding", AsyncMock(return_value=None)):
                await stitch_verify.assemble_and_verify(SimpleNamespace(state=self.state))
            if self.qa_failures:
                self.qa_failures -= 1
                self.state[s.K_QA_REPORT] = {"passed": False, "issues": [{"severity": "critical", "message": "cta must render again"}]}
                self.state[s.K_REWORK_PLAN] = {"targets": ["cta"], "feedback": "Fix CTA"}
            yield agent._progress(ctx, "qa", {s.K_QA_REPORT: self.state[s.K_QA_REPORT]})
        elif child == s.AGENT_REVIEW_DISPATCHER:
            holder["paused"] = True
            yield agent._progress(ctx, "Waiting for review")
        elif child == s.AGENT_FEEDBACK_ROUTER:
            yield agent._progress(ctx, "route", {s.K_REWORK_PLAN: {"targets": ["phrasing"], "feedback": "Clarify copy"}})
        elif child == s.AGENT_PUBLISHER:
            assert self.state[s.K_VERDICT]["status"] == "approved"
            assert self.state[s.K_BUNDLE]["ordered_artifacts"] == ["cover.mp4", "slide_2.png", "cta.png"]
            yield agent._progress(ctx, "published", {s.K_PUBLISH_RESULT: {"status": "published", "media_id": "published-test-id"}})

    async def run(self):
        async def drive(agent, child, ctx, holder):
            async for event in self.drive(agent, child, ctx, holder):
                yield event
        with patch.object(orch.CarouselOrchestrator, "_child", side_effect=lambda name: name), patch.object(orch.CarouselOrchestrator, "_drive", drive), patch.object(orch.CarouselOrchestrator, "_record_phase_quietly", AsyncMock()), patch.object(orch.CarouselOrchestrator, "_name_run_quietly", AsyncMock()):
            async for event in self.agent._run_async_impl(self.ctx):
                self.state.update(event.actions.state_delta)


@pytest.mark.asyncio
async def test_full_pipeline_research_to_review_rework_approval_and_done():
    p = Pipeline()
    await p.run()
    assert p.calls[:6] == list(orch.GENERATE_ORDER)
    assert p.state[s.K_PHASE] == "review"
    assert s.AGENT_PUBLISHER not in p.calls
    assert p.state[s.K_BUNDLE]["ordered_artifacts"] == ["cover.mp4", "slide_2.png", "cta.png"]
    p.calls.clear()
    p.state[s.K_VERDICT] = {"status": "rejected", "feedback": "Clarify copy"}
    await p.run()
    assert p.state[s.K_PHASE] == "review"
    assert [c for c in p.calls if c in OUTPUT_KEYS] == ["phrasing", "template_design", "cta"]
    assert p.state[s.K_REWORK_ROUND] == 1
    p.state[s.K_VERDICT] = {"status": "approved"}
    await p.run()
    assert p.state[s.K_PHASE] == "done"
    assert p.calls.count(s.AGENT_PUBLISHER) == 1


@pytest.mark.asyncio
async def test_qa_repairs_only_failed_output_before_showing_review():
    p = Pipeline()
    p.qa_failures = 1
    await p.run()
    assert p.state[s.K_PHASE] == "review"
    assert p.state[s.K_QA_ROUND] == 1
    assert p.calls.count("cta") == 2
    assert p.calls.count("research") == 1
    assert p.calls.index("review_dispatcher") > len(orch.GENERATE_ORDER)


@pytest.mark.asyncio
async def test_missing_output_retries_once_then_resumes_at_failed_step():
    p = Pipeline()
    p.state[s.K_COPY] = outputs()[s.K_COPY]  # old output must not hide the failure
    p.fail["phrasing"] = 2
    with pytest.raises(RuntimeError, match="phrasing could not finish"):
        await p.run()
    assert p.state["generation_completed"] == ["research", "planner", "first_page_visual"]
    assert p.state[s.K_COPY] is None
    assert "template_design" not in p.calls
    p.calls.clear()
    await p.run()
    assert p.calls[0] == "phrasing"
    assert "first_page_visual" not in p.calls
    assert p.state[s.K_PHASE] == "review"


@pytest.mark.asyncio
async def test_interrupted_rework_does_not_spend_another_review_round():
    p = Pipeline()
    await p.run()
    p.state[s.K_VERDICT] = {"status": "rejected", "feedback": "Clarify copy"}
    p.fail["template_design"] = 2
    with pytest.raises(RuntimeError):
        await p.run()
    assert p.state[s.K_REWORK_ROUND] == 1
    p.calls.clear()
    await p.run()
    assert p.calls[0] == "template_design"
    assert "phrasing" not in p.calls and "feedback_router" not in p.calls
    assert p.state[s.K_REWORK_ROUND] == 1
    assert p.state["rework_active"] is None


@pytest.mark.asyncio
async def test_download_only_run_pauses_without_review_actions_or_delivery():
    p = Pipeline(account="")
    await p.run()
    assert p.state[s.K_PHASE] == "review"
    assert s.K_BUNDLE in p.state
    assert "review_dispatcher" not in p.calls and "publisher" not in p.calls
    p.state[s.K_VERDICT] = {"status": "approved"}
    await p.run()
    assert p.state[s.K_VERDICT] is None and p.state[s.K_PHASE] == "review"


@pytest.mark.asyncio
async def test_old_passed_qa_cannot_survive_failed_verification():
    p = Pipeline()
    p.state.update(outputs())
    p.state[s.K_PHASE] = "qa"
    p.state[s.K_QA_REPORT] = {"passed": True}
    p.stale_qa = True
    await p.run()
    assert p.state[s.K_PHASE] == "qa" and p.state[s.K_QA_REPORT] is None
    assert "review_dispatcher" not in p.calls


@pytest.mark.asyncio
async def test_storage_outage_pauses_qa_without_regenerating():
    state = outputs()
    with patch.object(stitch_verify, "_existing_artifacts", AsyncMock(return_value=None)):
        result = await stitch_verify.assemble_and_verify(SimpleNamespace(state=state))
    assert result["retryable"] and not result["passed"]
    assert state[s.K_QA_REPORT] is None and s.K_REWORK_PLAN not in state


@pytest.mark.parametrize("name,key,bad", [
    ("planner", s.K_PLAN, {"style": "points", "slide_count": 4, "hook_title": "Title", "slides": [{"index": 2, "purpose": "one", "key_points": ["fact"]}, {"index": 2, "purpose": "two", "key_points": ["fact"]}]}),
    ("phrasing", s.K_COPY, {"slides": [{"index": 3, "lines": ["Wrong index"]}], "caption": "Caption"}),
    ("first_page_visual", s.K_COVER, {"title": "Title", "video_artifact": "cover.mp4"}),
    ("template_design", s.K_BODY_SLIDES, []),
    ("cta", s.K_CTA_SLIDE, {"cta_type": "redirect", "artifact": "cta.png", "link_url": ""}),
])
def test_incomplete_or_mismatched_handoffs_are_rejected(name, key, bad):
    state = outputs(); state[key] = bad
    with pytest.raises(ValueError):
        validate_output(name, state)


def test_cta_migrates_legacy_design_and_persists_separately():
    design = CarouselDesign.model_validate({"inside": {"background": "#010203", "title_size": 70}})
    assert design.cta.background == "#010203"
    design.cta.background = "#121314"
    restored = CarouselDesign.model_validate_json(design.model_dump_json())
    assert restored.inside.background == "#010203" and restored.cta.background == "#121314"


def test_cta_no_image_uses_its_saved_layout_without_billed_generation(tmp_path):
    design = CarouselDesign(handle_text="@test", logo_visible=False, handle_visible=False,
        inside={"background": "#ff0000"}, cta={"background": "#123456", "image_type": "none", "title_size": 52})
    path = tmp_path / "cta.png"
    with patch.object(image_gen, "_call_images_api") as generate:
        image_gen.generate_cta_image("follow", "Follow for more", ["The next story awaits."], "@test", "", str(path), design)
    generate.assert_not_called()
    with Image.open(path) as rendered:
        assert rendered.size == (1080, 1350)
        assert rendered.getpixel((0, 0))[:3] == (18, 52, 86)
    assert design.inside.background == "#ff0000"


@pytest.mark.asyncio
async def test_corrupt_cta_or_cover_png_fails_qa_for_correct_agent():
    for artifact, owner in [("cta.png", "cta"), ("cover.png", "first_page_visual")]:
        state = outputs()
        context = SimpleNamespace(state=state, load_artifact=AsyncMock(return_value=types.Part.from_bytes(data=b"broken", mime_type="image/png")))
        with patch.object(stitch_verify, "_existing_artifacts", AsyncMock(return_value={artifact})), patch.object(stitch_verify, "_verify_brand_padding", AsyncMock(return_value=None)):
            result = await stitch_verify.assemble_and_verify(context)
        assert not result["passed"]
        assert any(owner in issue["message"] and artifact in issue["message"] for issue in result["issues"])


@pytest.mark.asyncio
async def test_durable_publish_claim_allows_only_one_worker(receipt_store):
    results = await asyncio.gather(publish_receipts.claim("run", "account"), publish_receipts.claim("run", "account"))
    assert sum(won for won, _ in results) == 1
    assert not (await publish_receipts.claim("run", "account"))[0]
    receipt = receipt_store[publish_receipts.key("run")]
    await publish_receipts.finish("run", receipt, {"status": "published", "media_id": "live"})
    won, recovered = await publish_receipts.claim("run", "account")
    assert not won and recovered["media_id"] == "live"


@pytest.mark.asyncio
async def test_uncertain_publish_stays_blocked_after_session_state_is_lost(receipt_store):
    from test_publish_account_routing import _account
    state = {s.K_RUN_ID: "journal-test", s.K_ACCOUNT_ID: "account", s.K_VERDICT: {"status": "approved"}, s.K_BUNDLE: {"cover": outputs()[s.K_COVER], "cta": outputs()[s.K_CTA_SLIDE], "ordered_artifacts": ["cover.mp4", "slide_2.png", "cta.png"]}}
    context = SimpleNamespace(state=state, session=SimpleNamespace(app_name="test", user_id="test", id="journal-test"))
    with patch.object(publisher, "_account_for_run", return_value=_account()), patch.object(publisher, "_resolve_artifact_service", return_value=SimpleNamespace(public_url_async=AsyncMock(return_value="https://example.com/slide.png"))), patch.object(instagram_tools, "publish_carousel", side_effect=instagram_tools.PublishUncertain("creation-test", "lost response")) as publish:
        first = await publisher.publish_approved_carousel(context)
        assert first["retryable"] is False
        state.pop(s.K_PUBLISH_RESULT)  # simulate crash before ADK event commit
        second = await publisher.publish_approved_carousel(context)
        assert second["retryable"] is False
        assert publish.call_count == 1


def test_permalink_failure_keeps_known_published_media_id():
    from test_publish_account_routing import _account
    def graph(client, method, path, **kwargs):
        if path.endswith("/media_publish"):
            return {"id": "live-media"}
        if path == "/live-media":
            raise httpx.ReadTimeout("permalink timed out")
        if path.endswith("/media"):
            return {"id": "container"}
        return {"status_code": "FINISHED"}
    with patch.object(instagram_tools, "_graph_request", graph):
        result = instagram_tools.publish_carousel({"caption": "Caption"}, ["https://example.com/1.png", "https://example.com/2.png", "https://example.com/3.png"], account=_account())
    assert result["media_id"] == "live-media" and result["permalink"] == ""


@pytest.mark.asyncio
async def test_review_rejects_other_account_and_download_only_runs_before_claim():
    for account_id in ("", "expired"):
        pool = SimpleNamespace(fetchrow=AsyncMock(return_value={"state": {"account_id": account_id}}))
        with patch.object(db, "get_pool", AsyncMock(return_value=pool)), patch.object(eligibility.instagram_accounts, "get", return_value=None), patch.object(db, "claim_pending_review", AsyncMock()) as claim:
            result = await verdict.submit_verdict("run", "approved", "")
        assert result.result == "instagram_required"
        claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_api_rejects_download_approval_before_modifying_cover():
    from fastapi import HTTPException
    with patch.object(routes_runs, "_session_state", AsyncMock(return_value={})), patch.object(routes_runs, "_apply_cover_choice", AsyncMock()) as cover:
        with pytest.raises(HTTPException) as caught:
            await routes_runs.post_verdict("run", routes_runs.VerdictRequest(status="approved", cover="video"), Identity(email="test@example.com", subject="test"))
    assert caught.value.status_code == 409
    cover.assert_not_awaited()

@pytest.mark.asyncio
async def test_competing_cover_choices_travel_only_with_the_winning_verdict():
    from test_review_verdict import _SerialisingPool
    from app.review.resume import build_resume_content
    from app.agents.review_dispatcher import _verdict_from_payload
    spawned = []
    with patch.object(db, "get_pool", AsyncMock(return_value=_SerialisingPool())), patch.object(eligibility.instagram_accounts, "get", return_value=SimpleNamespace(usable=True)), patch.object(verdict, "spawn_resume", lambda *args, **kwargs: spawned.append((args, kwargs))):
        results = await asyncio.gather(verdict.submit_verdict("run", "approved", "", cover_choice="image"), verdict.submit_verdict("run", "approved", "", cover_choice="video"))
    assert sorted(r.result for r in results) == ["accepted", "not_pending"]
    assert len(spawned) == 1 and spawned[0][1]["cover_choice"] == "image"
    message = build_resume_content("call", "approved", "", cover_choice=spawned[0][1]["cover_choice"])
    parsed = _verdict_from_payload(message.parts[0].function_response.response)
    assert parsed.cover_choice == "image"


@pytest.mark.asyncio
async def test_spawn_resume_forwards_the_selected_cover():
    from app.review import resume
    seen = []
    async def fake_pipeline(*args):
        seen.append(args)
    with patch.object(resume, "resume_pipeline", fake_pipeline):
        resume.spawn_resume("cover-forward-test", "session", "call", "approved", "", cover_choice="image")
        await asyncio.gather(*list(resume._resume_tasks))
    assert seen[0][-1] == "image"


@pytest.mark.asyncio
async def test_approved_image_cover_is_committed_before_publishing():
    p = Pipeline()
    await p.run()
    p.state[s.K_PHASE] = "publish"
    p.state[s.K_VERDICT] = {"status": "approved", "cover_choice": "image"}
    async def drive(agent, child, ctx, holder):
        if child == "publisher":
            assert p.state[s.K_BUNDLE]["ordered_artifacts"] == ["cover.png", "slide_2.png", "cta.png"]
            yield agent._progress(ctx, "published", {s.K_PUBLISH_RESULT: {"media_id": "live"}})
    with patch.object(orch.CarouselOrchestrator, "_child", side_effect=lambda name: name), patch.object(orch.CarouselOrchestrator, "_drive", drive), patch.object(orch.CarouselOrchestrator, "_record_phase_quietly", AsyncMock()):
        async for event in p.agent._phase_publish(p.ctx, p.state, {"paused": False}):
            p.state.update(event.actions.state_delta)
    assert p.state[s.K_PHASE] == "done"


@pytest.mark.asyncio
async def test_stale_pending_review_cannot_approve_failed_qa_or_rework():
    for phase, passed in [("rework", True), ("review", False), ("qa", True)]:
        pool = SimpleNamespace(fetchrow=AsyncMock(return_value={"state": {"account_id": "connected", "phase": phase, "qa_report": {"passed": passed}, "bundle": {"ordered_artifacts": ["cover.png"]}}}))
        with patch.object(db, "get_pool", AsyncMock(return_value=pool)), patch.object(eligibility.instagram_accounts, "get", return_value=SimpleNamespace(usable=True)), patch.object(db, "claim_pending_review", AsyncMock()) as claim:
            result = await verdict.submit_verdict("run", "approved", "")
        assert result.result == "not_pending"
        claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_notification_failure_cannot_erase_publish_receipt(receipt_store):
    from test_publish_account_routing import _account
    state = {s.K_RUN_ID: "notice-test", s.K_ACCOUNT_ID: "account", s.K_VERDICT: {"status": "approved"}, s.K_BUNDLE: {"cover": outputs()[s.K_COVER], "cta": outputs()[s.K_CTA_SLIDE], "ordered_artifacts": ["cover.mp4", "slide_2.png", "cta.png"]}}
    context = SimpleNamespace(state=state, session=SimpleNamespace(app_name="test", user_id="test", id="notice-test"))
    def failed_notice(*args):
        assert receipt_store[publish_receipts.key("notice-test")]["media_id"] == "live"
        raise RuntimeError("Notification unavailable")
    with patch.object(publisher, "_account_for_run", return_value=_account()), patch.object(publisher, "_resolve_artifact_service", return_value=SimpleNamespace(public_url_async=AsyncMock(return_value="https://example.com/slide.png"))), patch.object(instagram_tools, "publish_carousel", return_value={"media_id": "live"}) as publish, patch.object(publisher.telegram_tools, "send_confirmation_message", failed_notice), patch.object(db, "update_run_phase", AsyncMock()):
        first = await publisher.publish_approved_carousel(context)
        assert first["status"] == "published" and first["mail_error"]
        state.pop(s.K_PUBLISH_RESULT)
        second = await publisher.publish_approved_carousel(context)
    assert second["status"] == "already_published" and publish.call_count == 1
