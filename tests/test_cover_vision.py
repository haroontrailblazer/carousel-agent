"""The cover agent looks at candidate media before using it.

No network or API calls: the vision model is replaced by a fake client.
"""
import asyncio
import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from app.agents import first_page_visual
from app.config import settings
from app.tools import cover_vision


def _fake_client(answer: dict, calls: list):
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(answer)))],
            usage=SimpleNamespace(prompt_tokens=900, completion_tokens=60, total_tokens=960),
        )

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _still(tmp_path, size=(1600, 900)):
    path = tmp_path / "still.png"
    image = Image.new("RGB", size, "#20304a")
    ImageDraw.Draw(image).ellipse((1100, 500, 1400, 800), fill="#f09854")
    image.save(path)
    return path


def _needs_ffmpeg():
    if not shutil.which(settings.ffmpeg_bin) or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and ffprobe are required")


def test_a_still_is_sent_as_one_numbered_frame(tmp_path):
    sheet = cover_vision.contact_sheet(str(_still(tmp_path)), False, str(tmp_path))
    assert sheet.timestamps == [0.0]
    with Image.open(sheet.path) as image:
        assert max(image.size) <= 1024


def test_a_video_is_sampled_across_its_whole_length(tmp_path):
    _needs_ffmpeg()
    source = tmp_path / "source.mp4"
    subprocess.run(
        [settings.ffmpeg_bin, "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24",
         "-t", "10", "-an", "-c:v", "libx264", str(source)],
        check=True, capture_output=True, timeout=60,
    )
    sheet = cover_vision.contact_sheet(str(source), True, str(tmp_path))
    assert len(sheet.timestamps) == 6
    # Spread across the footage, but only over moments a clip of the minimum
    # length can still START on - a frame from the last seconds used to be
    # approved and then silently replaced when the clip was cut.
    assert sheet.timestamps[0] < 1.0
    assert 4.0 < sheet.timestamps[-1] <= 10.0 - settings.cover_clip_min_s


def test_the_judge_sends_the_sheet_and_parses_a_clean_verdict(tmp_path):
    sheet = cover_vision.contact_sheet(str(_still(tmp_path)), False, str(tmp_path))
    calls: list = []
    answer = {
        "best_frame": 7,  # out of range: clamped to the one frame there is
        "score": 14,
        "verdict": "USE",
        "problems": ["talking_head", "made_up_problem"],
        "focus": {"x": 0.6, "y": 0.5, "w": 0.3, "h": 1.7},
        "reason": "clear subject",
    }
    verdict = cover_vision.judge_cover_media(sheet, "Robot arm test", "HOOK", _fake_client(answer, calls))
    assert verdict["best_frame"] == 1
    assert verdict["score"] == 10
    assert verdict["verdict"] == "use"
    assert verdict["problems"] == ["talking_head"]
    assert verdict["focus"]["h"] == 0.5  # clipped to the frame: y 0.5 + h 0.5
    content = calls[0]["messages"][1]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_an_unreadable_answer_raises_so_the_agent_falls_back(tmp_path):
    sheet = cover_vision.contact_sheet(str(_still(tmp_path)), False, str(tmp_path))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **_: SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))], usage=None
        )
    )))
    with pytest.raises(RuntimeError):
        cover_vision.judge_cover_media(sheet, "story", "hook", client)


def test_focus_keeps_the_subject_above_the_title_shadow():
    """A subject in the middle of a wide frame lands in the upper part of the crop.

    Centring it, as a plain crop would, puts its lower half under the shadow.
    """
    focus = {"x": 0.6, "y": 0.4, "w": 0.2, "h": 0.3}
    left, top, width, height = cover_vision.focus_crop_box(1920, 1080, focus)
    assert abs(width / height - 0.8) < 0.01
    subject_top = focus["y"] * 1080 - top
    subject_bottom = subject_top + focus["h"] * 1080
    assert subject_top >= 0
    assert subject_bottom <= height * 0.66  # the shadow is solid black below 66%
    subject_mid_x = (focus["x"] + focus["w"] / 2) * 1920
    assert left <= subject_mid_x <= left + width


def test_focus_never_zooms_into_mush():
    box = cover_vision.focus_crop_box(1920, 1080, {"x": 0.5, "y": 0.1, "w": 0.02, "h": 0.03})
    assert box is not None and box[2] >= 480


def test_focus_on_the_whole_frame_is_left_to_the_normal_crop():
    assert cover_vision.focus_crop_box(1920, 1080, {"x": 0, "y": 0, "w": 1, "h": 1}) is None


def test_a_focused_still_is_cropped_to_portrait(tmp_path):
    out = cover_vision.apply_focus(
        str(_still(tmp_path)), False, {"x": 0.68, "y": 0.55, "w": 0.2, "h": 0.33}, str(tmp_path)
    )
    with Image.open(out) as image:
        assert abs(image.width / image.height - 0.8) < 0.01


class _Ctx:
    def __init__(self):
        self.state = {}


def test_inspections_are_capped_per_run(tmp_path, monkeypatch):
    still = _still(tmp_path)
    monkeypatch.setattr(first_page_visual, "_run_workdir", lambda _ctx: str(tmp_path))
    calls: list = []
    answer = {"best_frame": 1, "score": 3, "verdict": "reject", "problems": ["document_or_slide"],
              "focus": None, "reason": "a pdf page"}
    def judge(sheet, story, hook, **_kw):
        calls.append(1)
        return cover_vision._parse_verdict(json.dumps(answer), 1)

    monkeypatch.setattr(cover_vision, "judge_cover_media", judge)
    ctx = _Ctx()
    results = [
        asyncio.run(first_page_visual.inspect_cover_media(str(still), False, tool_context=ctx))
        for _ in range(first_page_visual._MAX_INSPECTIONS + 1)
    ]
    assert all(r["ok"] and r["verdict"] == "reject" for r in results[:-1])
    assert results[0]["focus_w"] == 0
    assert results[-1]["ok"] is False and results[-1]["inspections_left"] == 0
    assert len(calls) == first_page_visual._MAX_INSPECTIONS


def test_a_failed_check_reports_instead_of_crashing(tmp_path, monkeypatch):
    monkeypatch.setattr(first_page_visual, "_run_workdir", lambda _ctx: str(tmp_path))

    def boom(*_a, **_kw):
        raise RuntimeError("vision check failed: no key")

    monkeypatch.setattr(cover_vision, "judge_cover_media", boom)
    result = asyncio.run(first_page_visual.inspect_cover_media(
        str(_still(tmp_path)), False, tool_context=_Ctx()
    ))
    assert result["ok"] is False and "no key" in result["error"]


def test_the_agent_is_told_to_look_before_it_uses_media():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "skills/agents/first_page_visual.md").read_text(encoding="utf-8")
    assert "inspect_cover_media" in text
    # An approved video is retrimmed at the judged frame, so its crop applies;
    # the agent no longer copies a subject box by hand.
    assert "retrim_from" in text and "best_start_s" in text
    assert "use_inspection" in text
    assert "ai_illustration" in text


# --- what kind of picture it is --------------------------------------------------


def _verdict(official=False, **answer):
    base = {"best_frame": 1, "score": 7, "verdict": "use", "usable_frames": 1,
            "problems": [], "focus": None, "reason": "r"}
    return cover_vision._parse_verdict(json.dumps({**base, **answer}), 1, official=official)


def test_the_judge_is_asked_for_the_kind_of_picture():
    prompt = " ".join(cover_vision._JUDGE_SYSTEM.split())
    for kind in cover_vision.KINDS:
        assert kind in prompt
    assert '"kind"' in prompt
    # Narrowed: third-party AI art only, and another outlet's marks only.
    assert "made by a third party" in prompt
    assert "own logo" in prompt and "is not publisher_branding" in prompt
    assert "about a third" in prompt  # a photo inside a slide is judged as that region


def test_the_kind_is_read_from_the_answer():
    assert _verdict(kind="official_visual")["kind"] == "official_visual"
    assert _verdict(kind="REAL_PHOTO")["kind"] == "real_photo"
    verdict = _verdict(kind="text_graphic", verdict="reject", score=4, problems=["text_heavy"])
    assert (verdict["kind"], verdict["verdict"], verdict["score"]) == ("text_graphic", "reject", 4)


@pytest.mark.parametrize("problems, kind", [
    # A rejection that names no problem says nothing: not a tier-3 photo.
    ([], "unusable"),
    (["publisher_branding"], "real_photo"),
    (["generic_stock"], "real_photo"),
    (["ai_illustration"], "ai_art"),
    (["document_or_slide"], "text_graphic"),
    (["text_heavy"], "text_graphic"),
    (["screen_recording_or_webpage"], "text_graphic"),
    (["logo_only"], "unusable"),
    (["talking_head"], "unusable"),
    (["unrelated"], "unusable"),
    (["low_quality"], "unusable"),
])
def test_an_answer_without_a_kind_gets_one_from_its_problems(problems, kind):
    assert cover_vision.kind_from_problems(problems) == kind
    assert _verdict(verdict="reject", problems=problems)["kind"] == kind
    assert _verdict(verdict="reject", problems=problems, kind="banana")["kind"] == kind


def test_an_approved_picture_keeps_its_kind_whatever_the_problems():
    """A photo the judge approved with a presenter in a corner is still a photo."""
    assert cover_vision.kind_from_problems([], "use") == "real_photo"
    assert cover_vision.kind_from_problems(["talking_head"], "use") == "real_photo"
    assert _verdict(verdict="use", problems=["talking_head"])["kind"] == "real_photo"


@pytest.mark.parametrize("problem, kind", [
    ("talking_head", "unusable"), ("unrelated", "unusable"), ("logo_only", "unusable"),
    ("low_quality", "unusable"), ("text_heavy", "text_graphic"),
    ("document_or_slide", "text_graphic"), ("screen_recording_or_webpage", "text_graphic"),
])
def test_a_rejection_takes_its_kind_from_its_problems_not_the_label(problem, kind):
    """A "real_photo" rejected as a presenter was tier 3, and the ladder built it."""
    for label in ("real_photo", "official_visual"):
        verdict = _verdict(kind=label, verdict="reject", score=6, problems=[problem])
        assert verdict["kind"] == kind


def test_generic_stock_ranks_with_the_text_graphics():
    stock = _verdict(kind="real_photo", verdict="reject", score=6, problems=["generic_stock"])
    assert stock["kind"] == "real_photo"
    assert cover_vision.tier_for(stock["kind"], "source_page", stock["problems"]) == 1


def test_the_companys_own_key_art_is_an_official_visual():
    """Mistral's cover art on mistral.ai: not a news site's AI illustration."""
    answer = {"kind": "ai_art", "verdict": "reject", "score": 6,
              "problems": ["ai_illustration", "publisher_branding"]}
    for official in (True, cover_vision.OFFICIAL_EXACT):
        verdict = _verdict(official=official, **answer)
        assert verdict["kind"] == "official_visual"
        assert verdict["problems"] == []
        assert cover_vision.tier_for(verdict["kind"], official=official) == 2


def test_a_site_that_only_resembles_the_company_gets_no_override():
    """xiaomitoday.it-style fan sites carry AI art in the company's style."""
    answer = {"kind": "ai_art", "verdict": "reject", "score": 6, "problems": ["ai_illustration"]}
    verdict = _verdict(official=cover_vision.OFFICIAL_FUZZY, **answer)
    assert verdict["kind"] == "ai_art" and "ai_illustration" in verdict["problems"]
    art = _verdict(official=cover_vision.OFFICIAL_FUZZY, kind="official_visual", verdict="reject")
    assert cover_vision.tier_for(art["kind"], official=cover_vision.OFFICIAL_FUZZY) == 1
    assert cover_vision.tier_for("official_visual", official="") == 1


def test_an_official_page_card_is_a_text_graphic_not_official_art():
    """The text guard runs after the override and counts text_heavy."""
    card = _verdict(official=True, kind="ai_art", verdict="reject", score=5,
                    problems=["ai_illustration", "text_heavy"])
    assert card["kind"] == "text_graphic"
    assert cover_vision.tier_for(card["kind"], official=True) == 1


def test_ai_art_by_anyone_else_is_still_refused_and_flagged():
    for answer in ({"kind": "ai_art"}, {"kind": "official_visual", "problems": ["ai_illustration"]},
                   {"problems": ["ai_illustration"]}):
        verdict = _verdict(**answer)
        assert verdict["kind"] == "ai_art"
        assert verdict["verdict"] == "reject"
        assert "ai_illustration" in verdict["problems"]
        assert verdict["reason"].startswith("not a real photo or footage")
        assert cover_vision.tier_for(verdict["kind"]) == 0


def test_only_ai_art_is_hard_rejected():
    """An official visual or a chart the model approved keeps its verdict."""
    assert _verdict(kind="official_visual")["verdict"] == "use"
    assert _verdict(kind="text_graphic")["verdict"] == "use"


def test_a_rejected_page_or_slide_is_a_text_graphic_whatever_it_was_called():
    """A vendor's OG text card must not pass as official art."""
    for problem in ("document_or_slide", "screen_recording_or_webpage"):
        for kind in ("real_photo", "official_visual"):
            verdict = _verdict(official=True, kind=kind, verdict="reject", problems=[problem])
            assert verdict["kind"] == "text_graphic"
    # Nor through the official override of "AI art".
    card = _verdict(official=True, kind="ai_art", verdict="reject",
                    problems=["ai_illustration", "document_or_slide"])
    assert card["kind"] == "text_graphic" and "ai_illustration" not in card["problems"]
    # Approved (a photo cropped out of a page), it keeps its kind.
    kept = _verdict(kind="real_photo", verdict="use", problems=["screen_recording_or_webpage"])
    assert kept["kind"] == "real_photo"
    # A screen recording the model approved on one lucky frame is still a page.
    answer = {"best_frame": 2, "score": 6, "verdict": "use", "kind": "official_visual",
              "problems": ["screen_recording_or_webpage"], "usable_frames": 1, "reason": "demo"}
    video = cover_vision._parse_verdict(json.dumps(answer), 4, official=True)
    assert (video["verdict"], video["kind"]) == ("reject", "text_graphic")


def test_tiers_follow_the_sourcing_ladder():
    assert cover_vision.tier_for("real_photo") == 3
    assert cover_vision.tier_for("real_photo", "wikimedia") == 2
    assert cover_vision.tier_for("official_visual", official=cover_vision.OFFICIAL_EXACT) == 2
    assert cover_vision.tier_for("official_visual", "wikimedia") == 1
    assert cover_vision.tier_for("text_graphic") == 1
    assert [cover_vision.tier_for(k) for k in ("ai_art", "unusable", "", "whatever")] == [0, 0, 0, 0]


def test_the_judge_sees_where_the_picture_was_found(tmp_path):
    sheet = cover_vision.contact_sheet(str(_still(tmp_path)), False, str(tmp_path))
    calls: list = []
    answer = {"best_frame": 1, "score": 7, "verdict": "reject", "kind": "ai_art",
              "problems": ["ai_illustration"], "focus": None, "reason": "key art"}
    verdict = cover_vision.judge_cover_media(
        sheet, "Mistral Small 4", "HOOK", _fake_client(answer, calls),
        provenance="https://mistral.ai/news/mistral-small-4/ (source_page)", official=True,
    )
    text = calls[0]["messages"][1]["content"][0]["text"]
    assert "Found on: https://mistral.ai/news/mistral-small-4/ (source_page)" in text
    assert "Published by the story's own organisation: yes" in text
    assert verdict["kind"] == "official_visual"

    calls.clear()
    verdict = cover_vision.judge_cover_media(sheet, "story", "HOOK", _fake_client(answer, calls))
    text = calls[0]["messages"][1]["content"][0]["text"]
    assert "Found on:" not in text
    assert "Published by the story's own organisation: no" in text
    assert verdict["kind"] == "ai_art" and verdict["verdict"] == "reject"


EXACT, FUZZY = cover_vision.OFFICIAL_EXACT, cover_vision.OFFICIAL_FUZZY


@pytest.mark.parametrize("urls, subjects, publishers, expected", [
    # The name part and the domain spell the whole name (an .ai domain the
    # company form: Wikidata's "Mistral" is a warship, so P856 cannot help).
    (["https://cms.mistral.ai/assets/cover.webp"], ["Mistral AI"], (), EXACT),
    (["https://www.liquid.ai/blog/x"], ["Liquid AI"], (), EXACT),
    (["https://mistral.ai/news/mistral-small-4/"], ["Mistral Small 4"], (), EXACT),
    # The name part alone, on any domain, is only a resemblance until the
    # subject's Wikidata website confirms it (xiaomi.eu is a community ROM
    # site, liquid.com a crypto exchange, medicare.gov the US scheme).
    (["https://www.xiaomi.com/global/news"], ["MiMo", "Xiaomi"], (), FUZZY),
    (["https://huggingface.co/mistralai"], ["Hugging Face"], (), FUZZY),
    (["https://www.openai.com/index/x"], ["OpenAI"], (), FUZZY),
    (["https://www.servicesaustralia.gov.au/x"], ["Services Australia"], (), FUZZY),
    (["https://www.apple.com/newsroom/x"], ["Apple", "iPhone"], (), FUZZY),
    (["https://xiaomi.eu/community/attachments/ai-render.jpg"], ["Xiaomi", "MiMo", "Liquid AI", "UltraSpeed"],
     (), FUZZY),
    (["https://liquid.com/x.png"], ["Xiaomi", "MiMo", "Liquid AI", "UltraSpeed"], (), FUZZY),
    (["https://mimo.today/x.png"], ["Xiaomi", "MiMo", "Liquid AI", "UltraSpeed"], (), FUZZY),
    (["https://ultraspeed.io/x.png"], ["Xiaomi", "MiMo", "Liquid AI", "UltraSpeed"], (), FUZZY),
    (["https://www.medicare.gov/x.jpg"], ["OpenAI", "Medicare", "Hugging Face", "Australia"], (), FUZZY),
    # Two subjects run together, or a subject plus a CDN word: only resembles.
    (["https://aistudio-cdn.xiaomimimo.com/assets/mimo.png"], ["MiMo-V2.6", "Xiaomi"], (), FUZZY),
    (["https://static.openaicdn.com/x.png"], ["OpenAI"], (), FUZZY),
    (["https://www.albanese.com/x.png"], ["Anthony Albanese"], (), FUZZY),
    # Fan and news sites named after a brand (the critique's real hosts).
    (["https://xiaomitoday.it/x.jpg"], ["Xiaomi", "MiMo"], (), ""),
    (["https://www.xiaomitime.com/x.jpg"], ["Xiaomi", "MiMo"], (), ""),
    (["https://xiaomiui.net/wp-content/uploads/x.jpg"], ["Xiaomi", "MiMo"], (), ""),
    (["https://openaimaster.com/wp-content/uploads/x.jpg"], ["OpenAI"], (), ""),
    (["https://speedtest.net/x.png"], ["UltraSpeed", "Xiaomi"], (), ""),
    (["https://openweights.org/x.png"], ["Mistral Small", "OpenAI"], (), ""),
    (["https://www.standard.co.uk/news/tech/x.jpg"], ["OpenAI", "Hugging Face"], (), ""),
    (["https://www.securityweek.com/x.jpg"], ["OpenAI", "Services Australia"], (), ""),
    (["https://appleinsider.com/articles/x"], ["Apple"], (), ""),
    (["https://lh3.googleusercontent.com/x.png"], ["Google"], (), ""),
    (["https://mybucket.s3.amazonaws.com/x.png"], ["Amazon"], (), ""),
    (["https://www.pm.gov.au/media/x"], ["Anthony Albanese"], (), ""),
    (["https://ichef.bbci.co.uk/news/1024/x.jpg", "https://www.bbc.co.uk/news/x"],
     ["OpenAI", "Hugging Face", "Australia"], ("https://www.bbc.com/news/articles/cw24", "BBC News"), ""),
    (["https://www.bbc.co.uk/news/x"], ["BBC"], ("BBC News",), ""),
    (["https://upload.wikimedia.org/wikipedia/commons/a/b.jpg"], ["Anthony Albanese"], (), ""),
    (["https://techcrunch.com/2026/09/mistral"], ["Mistral Small 4"], (), ""),
    # Page words are never subjects: the old rule matched "SPEED" and "Standard".
    (["https://mistral.ai/x"], [], (), ""),
    ([], ["Mistral"], (), ""),
])
def test_official_source(urls, subjects, publishers, expected):
    assert cover_vision.official_source(urls, subjects, publishers) == expected


def test_wikidata_official_websites_make_a_match_exact():
    """Xiaomi's P856 is mi.com: its MiMo pages match no subject name."""
    urls = ["https://mimo.mi.com/static/chart.png"]
    assert cover_vision.official_source(urls, ["Xiaomi", "MiMo"]) == ""
    assert cover_vision.official_source(
        urls, ["Xiaomi", "MiMo"], sites=["https://www.mi.com/", "http://www.xiaomi.com/"]
    ) == EXACT
    # A name match on its own becomes exact only on the subject's own website.
    assert cover_vision.official_source(
        ["https://openai.com/index/introducing-gpt-5-4-mini-and-nano/"], ["OpenAI"],
        sites=["https://openai.com/"],
    ) == EXACT
    assert cover_vision.official_source(
        ["https://www.medicare.gov/x.jpg"], ["Medicare"], sites=["https://www.medicare.gov/"],
    ) == EXACT


def test_an_official_website_elsewhere_rules_out_a_namesake_host():
    """Liquid AI's P856 is liquid.ai, so liquid.com (a crypto exchange) is nobody's."""
    subjects = ["Xiaomi", "MiMo", "Liquid AI", "UltraSpeed"]
    sites = ["https://www.liquid.ai/", "https://www.mi.com/"]
    assert cover_vision.official_source(["https://liquid.com/x.png"], subjects, sites=sites) == ""
    assert cover_vision.official_source(["https://www.liquid.ai/x.png"], subjects, sites=sites) == EXACT
    # xiaomi.eu is not mi.com: still only a resemblance.
    assert cover_vision.official_source(["https://xiaomi.eu/x.jpg"], subjects, sites=sites) == FUZZY
    # A whole-name domain is exact unless a known website of that name sits elsewhere.
    assert cover_vision.official_source(["https://liquid.ai/x.png"], ["Liquid AI"]) == EXACT
    assert cover_vision.official_source(
        ["https://liquid.ai/x.png"], ["Liquid AI"], sites=["https://www.liquid.com/"]
    ) == ""
    assert cover_vision.official_source(["https://apple.ai/x.png"], ["Apple"]) == EXACT
    assert cover_vision.official_source(
        ["https://apple.ai/x.png"], ["Apple"], sites=["https://www.apple.com/"]
    ) == ""


def test_a_lookalike_host_never_turns_third_party_ai_art_official():
    answer = '{"score":6,"verdict":"use","kind":"ai_art","problems":["ai_illustration"]}'
    for url, subjects in (
        ("https://xiaomi.eu/community/attachments/ai-render.jpg", ["Xiaomi", "MiMo"]),
        ("https://www.medicare.gov/x.jpg", ["OpenAI", "Medicare", "Hugging Face", "Australia"]),
    ):
        level = cover_vision.official_source([url], subjects)
        verdict = cover_vision._parse_verdict(answer, 1, official=level)
        assert verdict["kind"] == "ai_art" and verdict["verdict"] == "reject"
        assert cover_vision.tier_for(verdict["kind"], "", verdict["problems"], level) == 0


def test_a_publisher_is_excluded_unless_it_is_the_subject():
    """Only the publisher's host counts, not the slug of its article."""
    assert cover_vision.official_source(
        ["https://mistral.ai/news/x"], ["Mistral AI"],
        publishers=["https://techcrunch.com/2026/09/26/mistral-small-4/"],
    ) == EXACT
    # A press release on the company's own site is the company's own.
    assert cover_vision.official_source(
        ["https://mistral.ai/news/x"], ["Mistral"], publishers=["https://mistral.ai/news/x"],
    ) == EXACT
    # A reporting outlet that only resembles a subject never is.
    nine = ["https://www.nine.com.au/x.jpg"]
    assert cover_vision.official_source(nine, ["Nine Entertainment"]) == FUZZY
    assert cover_vision.official_source(
        nine, ["Nine Entertainment"], publishers=["https://www.nine.com.au/story"],
    ) == ""


def test_the_judge_is_told_when_a_site_only_resembles_the_company(tmp_path):
    sheet = cover_vision.contact_sheet(str(_still(tmp_path)), False, str(tmp_path))
    calls: list = []
    answer = {"best_frame": 1, "score": 7, "verdict": "reject", "kind": "ai_art",
              "problems": ["ai_illustration"], "focus": None, "reason": "key art"}
    verdict = cover_vision.judge_cover_media(
        sheet, "Xiaomi MiMo", "HOOK", _fake_client(answer, calls), official=FUZZY,
    )
    text = calls[0]["messages"][1]["content"][0]["text"]
    assert "Published by the story's own organisation: unverified" in text
    assert verdict["kind"] == "ai_art" and "ai_illustration" in verdict["problems"]


def test_the_companys_own_art_rejected_only_for_looking_ai_made_is_approved():
    """Mistral's key art on mistral.ai stayed 'reject', and the QA gate re-ran the step."""
    raw = '{"best_frame":1,"score":2,"verdict":"reject","problems":["ai_illustration"]}'
    verdict = cover_vision._parse_verdict(raw, 1, official=EXACT)
    assert (verdict["verdict"], verdict["kind"], verdict["problems"]) == ("use", "official_visual", [])
    assert cover_vision.tier_for(verdict["kind"], "", verdict["problems"], EXACT) == 2
    # Any other fault still rejects it.
    raw = '{"best_frame":1,"score":2,"verdict":"reject","problems":["ai_illustration","text_heavy"]}'
    assert cover_vision._parse_verdict(raw, 1, official=EXACT)["verdict"] == "reject"
    # A video still needs enough frames that work as a cover.
    raw = ('{"best_frame":1,"score":2,"verdict":"reject","kind":"ai_art","problems":["ai_illustration"],'
           '"usable_frames":1}')
    assert cover_vision._parse_verdict(raw, 6, official=EXACT)["verdict"] == "reject"
    # Only an exact match approves it.
    raw = '{"best_frame":1,"score":2,"verdict":"reject","problems":["ai_illustration"]}'
    assert cover_vision._parse_verdict(raw, 1, official=FUZZY)["verdict"] == "reject"
