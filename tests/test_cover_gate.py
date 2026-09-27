"""The cover-picture gate: a cover with no picture never ships silently.

Covers for AI-model launches (run-3c4107e390df, run-aa99162e2fe0) ended as the
plain black drawn background with the title, and QA passed them with no issue
while the review message said nothing. These tests pin the schema marker, the
one automatic cover retry in QA, the caption credit for Wikimedia photos and
the warning on every review surface. Offline: fakes only, no network.
"""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import ValidationError

from app import orchestrator as orch
from app import state as s
from app.agents import stitch_verify
from app.cover_notice import COVER_WARNING, PICTURE_WARNING, SHARE_ALIKE_NOTE, cover_notice_lines
from app.schemas import Bundle, CoverSpec, CTASlide
from app.tools import telegram_tools as tg
from web_api import routes_runs
from web_api.auth import Identity

# The real source image that lost a finished cover in the sweep
# (adk-run-39935790c7ff): an em dash in the file name failed the
# published-text rule although nobody ever reads the URL.
TECHCRUNCH_URL = (
    "https://techcrunch.com/wp-content/uploads/2021/08/"
    "DNA-\u2014-Xero-2.jpg?resize=1200,750"
)
CREDIT = "Jane Doe / Wikimedia Commons, CC BY 4.0"
ARTIFACTS = {"cover.png", "cover.mp4", "slide_2.png", "cta.png"}


def _state(**cover) -> dict:
    """A finished carousel in session state, with the given cover fields."""
    return {
        s.K_NEWS_ITEM: {"id": "news", "title": "A release"},
        s.K_PLAN: {"style": "points", "slide_count": 3, "hook_title": "A faster release",
                   "slides": [{"index": 2, "purpose": "Explain", "key_points": ["Local."]}]},
        s.K_COVER: {"title": "A faster release", "poster_artifact": "cover.png",
                    "video_artifact": "cover.mp4", "duration_s": 6, **cover},
        s.K_COPY: {"slides": [{"index": 2, "lines": ["Local", "The release runs locally."]}],
                   "caption": "The facts behind the release."},
        s.K_BODY_SLIDES: [{"index": 2, "artifact": "slide_2.png"}],
        s.K_CTA_SLIDE: {"cta_type": "follow", "artifact": "cta.png"},
    }


async def _verify(state: dict, existing=ARTIFACTS) -> dict:
    with patch.object(stitch_verify, "_existing_artifacts", AsyncMock(return_value=existing)), \
            patch.object(stitch_verify, "_verify_rendered_png", AsyncMock(return_value=None)), \
            patch.object(stitch_verify, "_verify_brand_padding", AsyncMock(return_value=None)):
        return await stitch_verify.assemble_and_verify(SimpleNamespace(state=state))


# ----------------------------------------------------------------- schema --
def test_a_source_url_with_an_em_dash_no_longer_fails_the_cover():
    cover = CoverSpec(title="A faster release", source_media_url=TECHCRUNCH_URL,
                      poster_artifact="cover-poster.png", video_artifact="cover.mp4")
    assert cover.source_media_url == TECHCRUNCH_URL

    # Both ways a Bundle is built: from the model, and back from state.
    bundle = Bundle(cover=cover, cta=CTASlide(cta_type="follow"))
    restored = Bundle.model_validate(bundle.model_dump(mode="json"))
    assert restored.cover.source_media_url == TECHCRUNCH_URL


def test_an_em_dash_in_published_cover_text_still_fails():
    with pytest.raises(ValidationError, match="em dash"):
        CoverSpec(title="Launch \u2014 today")
    with pytest.raises(ValidationError, match="em dash"):
        Bundle.model_validate({"cover": {"title": "Launch \u2014 today"},
                               "cta": {"cta_type": "follow"}})


def test_the_drawn_background_marker_is_a_bool_because_text_saying_placeholder_fails():
    """Writing "placeholder" into any text field is rejected as model filler,
    so the only safe marker for the plain background is the bool."""
    with pytest.raises(ValidationError, match="placeholder"):
        CoverSpec(title="A faster release", source_credit="placeholder")
    assert CoverSpec(title="A faster release", drawn_background=True).drawn_background


def test_research_facts_carry_a_date_and_a_background_flag():
    from app.schemas import ResearchFact

    fact = ResearchFact(fact="Mistral released a model.", date="2026-09-24", background=True)
    assert (fact.date, fact.background) == ("2026-09-24", True)
    assert ResearchFact(fact="Old context.").model_dump()["date"] == ""


# ---------------------------------------------------------------- QA gate --
@pytest.mark.asyncio
async def test_first_drawn_cover_sends_only_the_cover_back_for_a_real_photo():
    state = _state(drawn_background=True)
    result = await _verify(state)

    assert not result["passed"]
    assert result["critical_targets"] == [s.AGENT_FIRST_PAGE_VISUAL]
    assert state[s.K_REWORK_PLAN]["targets"] == [s.AGENT_FIRST_PAGE_VISUAL]
    # No dependents: the copy and slides are fine and must not be redrawn.
    assert orch._expand_rework_targets(state[s.K_REWORK_PLAN]["targets"]) == [s.AGENT_FIRST_PAGE_VISUAL]
    assert state[stitch_verify.K_COVER_PICTURE_RETRIED] is True

    [issue] = [i for i in result["issues"] if i["severity"] == "critical"]
    assert issue["slide_index"] == 1
    assert "first_page_visual" in issue["message"] and "find_reference_photo" in issue["message"]
    # _target_for_issue routes to the FIRST agent name it finds.
    assert "research" not in issue["message"] and "planner" not in issue["message"]


@pytest.mark.asyncio
async def test_after_the_one_retry_a_drawn_cover_is_a_major_note_and_qa_passes():
    state = _state(drawn_background=True)
    await _verify(state)
    result = await _verify(state)

    assert result["passed"] and result["critical_targets"] == []
    assert state[s.K_REWORK_PLAN] is None
    majors = [i for i in result["issues"] if i["severity"] == "major"]
    assert any("after an automatic retry" in i["message"] for i in majors)


@pytest.mark.asyncio
async def test_a_storage_outage_does_not_spend_the_cover_retry():
    state = _state(drawn_background=True)
    outage = await _verify(state, existing=None)
    assert outage["retryable"]
    assert not state.get(stitch_verify.K_COVER_PICTURE_RETRIED)

    result = await _verify(state)
    assert result["critical_targets"] == [s.AGENT_FIRST_PAGE_VISUAL]


@pytest.mark.asyncio
async def test_a_cover_with_a_picture_raises_no_cover_issue():
    state = _state()
    result = await _verify(state)
    assert result["passed"] and result["issues"] == []
    assert stitch_verify.K_COVER_PICTURE_RETRIED not in state


# --------------------------------------------------------- caption credit --
@pytest.mark.asyncio
async def test_a_wikimedia_credit_is_appended_to_the_bundle_caption_once():
    state = _state(source_origin="wikimedia", source_credit=CREDIT,
                   source_media_url="https://upload.wikimedia.org/a.jpg")
    copy_before = deepcopy(state[s.K_COPY])

    for _ in range(2):  # a QA re-run must not add it twice
        result = await _verify(state)
        assert result["passed"]
        caption = state[s.K_BUNDLE]["caption"]
        assert caption == f"The facts behind the release.\n\nCover photo: {CREDIT}"
        assert caption.count("Cover photo:") == 1

    assert Bundle.model_validate(state[s.K_BUNDLE]).caption == caption
    assert state[s.K_COPY] == copy_before  # the phrasing agent's copy is untouched


@pytest.mark.asyncio
async def test_no_credit_for_other_origins_or_a_caption_that_already_has_one():
    state = _state(source_origin="source_page", source_credit=CREDIT)
    await _verify(state)
    assert "Cover photo:" not in state[s.K_BUNDLE]["caption"]

    state = _state(source_origin="wikimedia", source_credit=CREDIT)
    state[s.K_COPY]["caption"] = f"Written by hand.\n\nCover photo: {CREDIT}"
    await _verify(state)
    assert state[s.K_BUNDLE]["caption"].count("Cover photo:") == 1


# ------------------------------------------------------- whole pipeline --
@pytest.mark.asyncio
async def test_pipeline_retries_only_the_cover_then_reaches_review(monkeypatch):
    """Through the real orchestrator: one cover-only QA round, then review."""
    import test_pipeline_audit as audit

    real_outputs = audit.outputs

    def drawn_outputs():
        out = real_outputs()
        out[s.K_COVER] = {**out[s.K_COVER], "drawn_background": True}
        return out

    monkeypatch.setattr(audit, "outputs", drawn_outputs)
    p = audit.Pipeline()
    await p.run()

    assert p.state[s.K_PHASE] == "review"
    assert p.state[s.K_QA_ROUND] == 1
    assert p.calls.count(s.AGENT_FIRST_PAGE_VISUAL) == 2
    for agent in (s.AGENT_RESEARCH, s.AGENT_PLANNER, s.AGENT_PHRASING, s.AGENT_TEMPLATE_DESIGN, s.AGENT_CTA):
        assert p.calls.count(agent) == 1
    report = p.state[s.K_QA_REPORT]
    assert report["passed"]
    assert [i["severity"] for i in report["issues"]] == ["major"]


# -------------------------------------------------------- review surfaces --
def test_cover_notice_lines_warn_only_for_the_drawn_background():
    assert cover_notice_lines({"drawn_background": True}) == [COVER_WARNING]
    assert cover_notice_lines({"drawn_background": False, "source_credit": CREDIT}) == []
    assert cover_notice_lines(None) == []
    assert COVER_WARNING.startswith("Cover warning")
    assert "\u2014" not in COVER_WARNING


def _telegram_texts(monkeypatch, bundle: dict) -> list[str]:
    """Send one review through a fake Bot API and return the message texts."""
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/sendMessage")
        sent.append(parse_qs(request.content.decode())["text"][0])
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(sent)}})

    real_client = httpx.Client
    monkeypatch.setattr(
        tg.httpx, "Client",
        lambda *a, **kw: real_client(*a, transport=httpx.MockTransport(handler), **kw),
    )
    monkeypatch.setattr(tg.time, "sleep", lambda _s: None)
    monkeypatch.setattr(tg, "settings", SimpleNamespace(public_base_url="https://console.example.test"))
    tg._send_review_message("run-cover", bundle, 1, creds={"bot_token": "offline", "chat_id": "123"})
    return sent


def test_telegram_review_message_warns_about_a_cover_with_no_picture(monkeypatch):
    bundle = {"news_title": "A release", "caption": "The facts.", "preview_paths": [],
              "cover": {"title": "A faster release", "drawn_background": True}}
    [text] = _telegram_texts(monkeypatch, bundle)
    lines = text.splitlines()
    assert lines[1] == "A release"
    assert lines[2] == COVER_WARNING
    assert lines.index(COVER_WARNING) < lines.index("Caption:")


def test_telegram_review_message_is_unchanged_for_a_real_picture(monkeypatch):
    bundle = {"news_title": "A release", "caption": "The facts.", "preview_paths": [],
              "cover": {"title": "A faster release"}}
    [text] = _telegram_texts(monkeypatch, bundle)
    assert COVER_WARNING not in text
    assert text.splitlines()[:4] == ["Carousel review needed - round 1", "A release", "", "Caption:"]


@pytest.mark.asyncio
async def test_console_artifacts_payload_carries_the_warning_and_credit():
    service = SimpleNamespace(
        latest_versions_async=AsyncMock(return_value={}),
        public_url_async=AsyncMock(side_effect=lambda **kw: f"https://signed.test/{kw['filename']}"),
    )
    identity = Identity(email="reviewer@example.com", subject="reviewer")

    async def payload(**cover):
        state = _state(**cover)
        state[s.K_BUNDLE] = {"cover": state[s.K_COVER], "slides": state[s.K_BODY_SLIDES],
                             "cta": state[s.K_CTA_SLIDE], "caption": "Caption"}
        with patch.object(routes_runs, "_session_state", AsyncMock(return_value=state)), \
                patch.object(routes_runs.runtime, "artifact_service", return_value=service):
            return (await routes_runs.run_artifacts("run-cover", identity))["cover"]

    drawn = await payload(drawn_background=True)
    assert drawn["drawn_background"] is True
    assert drawn["notices"] == [COVER_WARNING]
    assert drawn["credit"] == ""

    credited = await payload(source_origin="wikimedia", source_credit=CREDIT)
    assert credited["drawn_background"] is False
    assert credited["notices"] == []
    assert credited["credit"] == CREDIT


# ------------------------------------------- pictures the check did not approve --
# The AI-model launch runs could also have shipped a benchmark chart the judge
# scored 1: nothing flagged it (always-real-image critique, item 3).


@pytest.mark.parametrize("fields, reason", [
    ({"picture_verdict": "reject", "picture_kind": "text_graphic", "picture_score": 1},
     "a text card or chart that scored 1 of 10"),
    ({"picture_verdict": "use", "picture_kind": "text_graphic", "picture_score": 2},
     "a text card or chart that scored 2 of 10"),
    ({"picture_verdict": "reject", "picture_kind": "real_photo", "picture_score": 6},
     "it was rejected as a real photo that scored 6 of 10"),
    ({"picture_verdict": "reject", "picture_kind": "official_visual", "picture_score": 2},
     "it was rejected as an official visual that scored 2 of 10"),
    ({"picture_verdict": "unchecked"}, "it was never checked"),
])
@pytest.mark.asyncio
async def test_a_weak_picture_is_sent_back_once_then_noted(fields, reason):
    state = _state(**fields)
    first = await _verify(state)
    assert first["critical_targets"] == [s.AGENT_FIRST_PAGE_VISUAL]
    [issue] = [i for i in first["issues"] if i["severity"] == "critical"]
    assert reason in issue["message"] and "find_reference_photo" in issue["message"]
    assert "research" not in issue["message"] and "planner" not in issue["message"]

    second = await _verify(state)
    assert second["passed"]
    assert [i["severity"] for i in second["issues"]] == ["major"]
    assert cover_notice_lines(state[s.K_COVER]) == [PICTURE_WARNING.format(reason=reason)]


@pytest.mark.parametrize("fields", [
    {"picture_verdict": "use", "picture_kind": "real_photo", "picture_score": 7},
    {"picture_verdict": "use", "picture_kind": "text_graphic", "picture_score": 5},
    # The reviewer's own image link is never second-guessed.
    {"picture_verdict": "reject", "picture_kind": "text_graphic", "picture_score": 1,
     "picture_from_reviewer": True},
    # A cover from before the picture fields existed.
    {},
])
@pytest.mark.asyncio
async def test_an_approved_or_reviewer_picture_raises_nothing(fields):
    state = _state(**fields)
    result = await _verify(state)
    assert result["passed"] and result["issues"] == []
    assert cover_notice_lines(state[s.K_COVER]) == []


@pytest.mark.asyncio
async def test_with_no_qa_round_left_the_cover_is_a_major_note_not_a_hard_stop():
    """A critical issue with every QA round spent ends the run in HARD STOP."""
    from app.config import settings

    for fields in ({"drawn_background": True}, {"picture_verdict": "unchecked"}):
        state = _state(**fields)
        state[s.K_QA_ROUND] = settings.max_qa_rounds
        result = await _verify(state)
        assert result["passed"] and result["critical_targets"] == []
        [issue] = result["issues"]
        assert issue["severity"] == "major" and "no automatic QA round is left" in issue["message"]
        assert not state.get(stitch_verify.K_COVER_PICTURE_RETRIED)


def test_the_share_alike_choice_is_surfaced_only_when_the_cover_is_weak():
    note = SHARE_ALIKE_NOTE.format(count=2)
    assert cover_notice_lines({"drawn_background": True, "share_alike_skipped": 2}) == [COVER_WARNING, note]
    weak = {"picture_verdict": "unchecked", "share_alike_skipped": 2}
    assert cover_notice_lines(weak)[1:] == [note]
    fine = {"picture_verdict": "use", "picture_kind": "real_photo", "picture_score": 8,
            "share_alike_skipped": 2}
    assert cover_notice_lines(fine) == []
    assert "COVER_ALLOW_SHARE_ALIKE" in note and "—" not in note
