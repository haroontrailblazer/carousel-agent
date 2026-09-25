"""The cover agent's tools, wired together the way the agent calls them.

inspect_cover_media stores a verdict, retrim_clip and download_and_trim record
which clip was cut from which file (and where), and build_cover applies the
stored crop by itself. These tests run that chain offline: the vision judge,
the contact sheet, compose_cover and the artifact service are fakes, and the
media is generated with local ffmpeg (skipped when it is missing).
"""
import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.adk.sessions.state import State

from app.agents import first_page_visual as fpv
from app.config import settings
from app.tools import cover_vision, media_tools

REPO = Path(__file__).resolve().parents[1]
MIN_S = float(settings.cover_clip_min_s)
FOCUS = {"x": 0.6, "y": 0.2, "w": 0.2, "h": 0.3}
CLEAN = {"x": 0.0, "y": 0.0, "w": 0.85, "h": 1.0}


class _Ctx:
    """The slice of ToolContext the cover tools use, over a real ADK State."""

    def __init__(self):
        self.state = State({}, {})
        self.saved: list[str] = []

    async def save_artifact(self, filename, part):
        self.saved.append(filename)
        return len(self.saved)


def _needs_ffmpeg():
    if not shutil.which(settings.ffmpeg_bin) or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and ffprobe are required")


def _shots(path: Path, *shots: tuple[str, float], size: str = "320x180") -> Path:
    """A 25 fps video of solid-colour shots with hard cuts between them."""
    inputs, labels = [], []
    for index, (colour, seconds) in enumerate(shots):
        inputs += ["-f", "lavfi", "-i", f"color=c={colour}:s={size}:r=25:d={seconds}"]
        labels.append(f"[{index}:v]")
    subprocess.run(
        [settings.ffmpeg_bin, "-v", "error", "-y", *inputs, "-filter_complex",
         f"{''.join(labels)}concat=n={len(shots)}:v=1[v]", "-map", "[v]",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True, timeout=120,
    )
    return path


def _moving(path: Path, seconds: float) -> Path:
    """A video whose picture changes every frame but never cuts."""
    subprocess.run(
        [settings.ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i",
         f"testsrc2=size=320x180:rate=25:d={seconds}", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True, timeout=120,
    )
    return path


def _frames(path: str, width: int = 8, height: int = 8, fmt: str = "rgb24") -> list[bytes]:
    raw = subprocess.run(
        [settings.ffmpeg_bin, "-v", "error", "-i", path, "-vf", f"scale={width}:{height}",
         "-f", "rawvideo", "-pix_fmt", fmt, "-"],
        check=True, capture_output=True, timeout=60,
    ).stdout
    size = width * height * (3 if fmt == "rgb24" else 1)
    return [raw[i:i + size] for i in range(0, len(raw), size)]


def _colour(frame: bytes) -> str:
    r, g, b = (sum(frame[i::3]) / (len(frame) / 3) for i in range(3))
    if r > 150 and g < 90 and b < 90:
        return "red"
    if b > 150 and r < 90 and g < 90:
        return "blue"
    return "other"


def _frame_at(path: str, seconds: float) -> bytes:
    return subprocess.run(
        [settings.ffmpeg_bin, "-v", "error", "-ss", f"{seconds:.3f}", "-i", path,
         "-frames:v", "1", "-vf", "scale=32:18", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        check=True, capture_output=True, timeout=60,
    ).stdout


def _mean_diff(a: bytes, b: bytes) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / max(len(a), 1)


def _retrim(ctx, path, start, length=6.0) -> dict:
    result = asyncio.run(fpv.retrim_clip(str(path), start, length, tool_context=ctx))
    assert result["ok"], result
    return result


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.setattr(fpv, "_run_workdir", lambda _ctx: str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Lineage and verdict lookup (no ffmpeg)
# ---------------------------------------------------------------------------


def test_a_verdict_is_found_whatever_way_the_path_is_spelled(tmp_path):
    ctx = _Ctx()
    source = tmp_path / "src-abc.mp4"
    clip = tmp_path / "clips" / "retrim-1.mp4"
    ctx.state[fpv._K_VERDICTS] = {fpv._media_key(source): {"verdict": "use", "focus": FOCUS}}
    # The agent handed retrim a forward-slash spelling of the source path.
    fpv._record_lineage(ctx, str(clip), source.as_posix(), 2.0)

    verdict, offset = fpv._verdict_for(ctx, str(clip))
    assert verdict["focus"] == FOCUS and offset == 2.0
    verdict, offset = fpv._verdict_for(ctx, clip.as_posix())
    assert verdict is not None and offset == 2.0
    if os.name == "nt":
        verdict, _ = fpv._verdict_for(ctx, str(clip).upper())
        assert verdict is not None


def test_start_offsets_add_up_along_the_lineage_and_an_unknown_start_breaks_them(tmp_path):
    ctx = _Ctx()
    source, first, second, default = (str(tmp_path / n) for n in ("src", "a", "b", "clip"))
    ctx.state[fpv._K_VERDICTS] = {fpv._media_key(source): {"verdict": "use"}}
    fpv._record_lineage(ctx, first, source, 3.0)
    fpv._record_lineage(ctx, second, first, 1.5)  # a retrim of a retrim
    fpv._record_lineage(ctx, default, source, None)  # download_and_trim's own pick

    assert fpv._verdict_for(ctx, second)[1] == pytest.approx(4.5)
    assert fpv._verdict_for(ctx, default) == ({"verdict": "use"}, None)
    assert fpv._verdict_for(ctx, str(tmp_path / "never-seen")) == (None, None)


def test_the_nearest_inspection_wins(tmp_path):
    ctx = _Ctx()
    source, clip = str(tmp_path / "src"), str(tmp_path / "clip")
    ctx.state[fpv._K_VERDICTS] = {
        fpv._media_key(source): {"verdict": "reject"},
        fpv._media_key(clip): {"verdict": "use"},
    }
    fpv._record_lineage(ctx, clip, source, 7.0)
    assert fpv._verdict_for(ctx, clip) == ({"verdict": "use"}, 0.0)


def test_find_source_clip_always_returns_the_full_result_shape():
    """Contract C1: the agent can read every key even when nothing ran."""
    result = asyncio.run(fpv.find_source_clip(tool_context=_Ctx()))
    assert result["found"] is False
    assert {
        "found", "url", "is_video", "duration_s", "origin", "image_url", "image_origin",
        "image_candidates", "image_first", "video_url", "video_duration_s", "video_origin",
        "trend_search", "note",
    } <= set(result)
    assert result["video_url"] == "" and result["image_first"] is False


# ---------------------------------------------------------------------------
# retrim_clip
# ---------------------------------------------------------------------------


def test_retrim_ends_just_before_the_next_shot(workdir):
    _needs_ffmpeg()
    source = _shots(workdir / "two.mp4", ("red", 6), ("blue", 6))
    ctx = _Ctx()
    result = _retrim(ctx, source, 0.5, 15)

    assert result["start_s"] == 0.5 and result["start_moved"] is False
    assert result["cut_s"] == pytest.approx(5.5, abs=0.1)
    assert result["held_s"] == 0 and result["looped"] is False
    assert MIN_S <= result["length_s"] < result["cut_s"]
    colours = [_colour(f) for f in _frames(result["clip_path"])]
    assert colours and set(colours) == {"red"}  # not even one frame of the next shot
    assert media_tools._media_duration(result["clip_path"]) == pytest.approx(result["length_s"], abs=0.1)


def test_a_shot_shorter_than_the_minimum_is_held_not_run_into_the_next(workdir):
    _needs_ffmpeg()
    source = _shots(workdir / "three.mp4", ("red", 2.5), ("blue", 4), ("green", 6))
    ctx = _Ctx()
    result = _retrim(ctx, source, 0.6, 6)

    assert result["start_s"] == 0.6 and result["start_moved"] is False
    assert result["cut_s"] == pytest.approx(1.9, abs=0.1)
    assert result["held_s"] > 1.5
    assert result["length_s"] == pytest.approx(MIN_S, abs=0.01)
    # Long enough for stitch_verify's 4-15 s window, and only the approved shot.
    assert media_tools._media_duration(result["clip_path"]) == pytest.approx(MIN_S, abs=0.1)
    assert set(_colour(f) for f in _frames(result["clip_path"])) == {"red"}


def test_a_frame_sampled_just_before_a_cut_does_not_ship_the_next_shot(workdir):
    """The contact sheet can land 0.1 s before a hard cut; that cut still counts."""
    _needs_ffmpeg()
    source = _shots(workdir / "cut.mp4", ("red", 4.6), ("blue", 7.4))
    ctx = _Ctx()
    result = _retrim(ctx, source, 4.48, 6)

    assert result["start_s"] == 4.48
    assert result["cut_s"] == pytest.approx(0.12, abs=0.05)
    assert result["held_s"] > 3
    assert set(_colour(f) for f in _frames(result["clip_path"])) == {"red"}


def test_a_source_shorter_than_the_minimum_is_looped_from_the_requested_frame(workdir):
    _needs_ffmpeg()
    source = _moving(workdir / "short.mp4", 2.5)
    ctx = _Ctx()
    result = _retrim(ctx, source, 1.2, 6)

    assert result["looped"] is True and result["held_s"] == 0
    assert result["start_s"] == 1.2 and result["start_moved"] is False
    assert media_tools._media_duration(result["clip_path"]) == pytest.approx(MIN_S, abs=0.1)
    # It opens on the requested frame, not on the file's first frame.
    first = _frame_at(result["clip_path"], 0.0)
    assert _mean_diff(first, _frame_at(str(source), 1.2)) < 2
    assert _mean_diff(first, _frame_at(str(source), 0.0)) > 5


def test_retrim_keeps_the_start_and_shortens_the_clip_instead(workdir):
    _needs_ffmpeg()
    source = _shots(workdir / "ten.mp4", ("red", 10))
    ctx = _Ctx()

    kept = _retrim(ctx, source, 5.0, 15)
    assert kept["start_s"] == 5.0 and kept["start_moved"] is False
    assert kept["length_s"] == pytest.approx(5.0, abs=0.1)
    assert kept["cut_s"] is None

    moved = _retrim(ctx, source, 8.0, 6)
    assert moved["start_moved"] is True
    assert moved["length_s"] >= MIN_S - 0.05
    assert fpv._lineage_chain(ctx, moved["clip_path"])[1][1] == pytest.approx(moved["start_s"])


# ---------------------------------------------------------------------------
# inspect -> retrim -> build
# ---------------------------------------------------------------------------


def _verdict(verdict="use", focus=FOCUS, clean=CLEAN, problems=(), best_frame=3):
    return {
        "best_frame": best_frame, "score": 8 if verdict == "use" else 3,
        "verdict": verdict, "problems": list(problems), "usable_frames": 5,
        "focus": focus, "clean": clean, "reason": "test",
    }


@pytest.fixture
def cover_fakes(workdir, monkeypatch):
    """Fake judge + contact sheet, and spies on apply_focus / compose_cover."""
    calls = SimpleNamespace(apply_focus=[], compose=[], answer=_verdict())
    times = [0.5, 1.5, 2.5, 3.5, 4.5, 5.5]

    def sheet(media_path, is_video, _workdir=""):
        return SimpleNamespace(path=workdir / "sheet.jpg", timestamps=times if is_video else [0.0])

    def judge(_sheet, _story, _hook, _client=None):
        return dict(calls.answer)

    def apply_focus(media_path, is_video, focus, _workdir="", clean=None):
        calls.apply_focus.append({"media": media_path, "focus": focus, "clean": clean})
        return str(workdir / "focused.mp4")

    def compose(media_path, title, highlight, is_video, _workdir, _design):
        calls.compose.append(media_path)
        video, poster = workdir / "cover.mp4", workdir / "cover.png"
        video.write_bytes(b"mp4")
        poster.write_bytes(b"png")
        return {"video_path": str(video), "poster_path": str(poster), "duration_s": 5.0}

    monkeypatch.setattr(cover_vision, "contact_sheet", sheet)
    monkeypatch.setattr(cover_vision, "judge_cover_media", judge)
    monkeypatch.setattr(cover_vision, "apply_focus", apply_focus)
    monkeypatch.setattr(media_tools, "compose_cover", compose)
    monkeypatch.setattr(fpv, "hook_warnings", lambda *_a, **_k: [])
    return calls


def _inspect(ctx, path, is_video=True) -> dict:
    result = asyncio.run(fpv.inspect_cover_media(str(path), is_video, tool_context=ctx))
    assert result["ok"], result
    return result


def _build(ctx, path, is_video=True, **kwargs) -> dict:
    return asyncio.run(fpv.build_cover(str(path), is_video, title="ROBOT LEARNS TO WALK",
                                       tool_context=ctx, **kwargs))


def test_the_inspection_stores_the_moment_and_the_problems(cover_fakes, workdir):
    _needs_ffmpeg()
    source = _shots(workdir / "src.mp4", ("red", 12))
    ctx = _Ctx()
    cover_fakes.answer = _verdict(problems=["publisher_branding"])
    result = _inspect(ctx, source)

    assert result["best_start_s"] == 2.5 and result["retrim_from"] == str(source)
    stored = ctx.state[fpv._K_VERDICTS][fpv._media_key(source)]
    assert stored["best_start_s"] == 2.5
    assert stored["problems"] == ["publisher_branding"]
    assert stored["focus"] == FOCUS and stored["clean"] == CLEAN


def test_an_approved_clip_retrimmed_at_the_judged_frame_gets_the_crop(cover_fakes, workdir):
    _needs_ffmpeg()
    source = _shots(workdir / "src.mp4", ("red", 12))
    ctx = _Ctx()
    inspected = _inspect(ctx, source)
    # The agent re-types the path with forward slashes: the verdict still applies.
    clip = _retrim(ctx, Path(inspected["retrim_from"]).as_posix(), inspected["best_start_s"], 6)

    result = _build(ctx, clip["clip_path"])
    assert result["ok"], result
    assert cover_fakes.apply_focus == [
        {"media": clip["clip_path"], "focus": FOCUS, "clean": CLEAN}
    ]
    assert cover_fakes.compose == [str(workdir / "focused.mp4")]
    assert ctx.saved == [fpv.COVER_VIDEO_ARTIFACT, fpv.COVER_POSTER_ARTIFACT]


def test_a_clip_from_another_moment_keeps_only_the_logo_free_area(cover_fakes, workdir):
    """Rework retrims elsewhere; the box judged at 2.5 s would crop empty background."""
    _needs_ffmpeg()
    source = _shots(workdir / "src.mp4", ("red", 12))
    ctx = _Ctx()
    _inspect(ctx, source)
    clip = _retrim(ctx, source, 7.0, 4)

    result = _build(ctx, clip["clip_path"])
    assert result["ok"], result
    assert cover_fakes.apply_focus == [{"media": clip["clip_path"], "focus": None, "clean": CLEAN}]
    assert any("does not open on the inspected frame" in w for w in result["warnings"])


def test_download_and_trims_own_clip_gets_no_subject_crop(cover_fakes, workdir, monkeypatch):
    """Its start is picked inside media_tools, so it cannot be matched to the frame."""
    _needs_ffmpeg()
    source = _shots(workdir / "src-abc.mp4", ("red", 12))
    clip = workdir / "clip-abc.mp4"
    shutil.copy(source, clip)
    monkeypatch.setattr(media_tools, "download_and_trim", lambda *_a: str(clip))
    ctx = _Ctx()
    downloaded = asyncio.run(fpv.download_and_trim("https://example.com/v", tool_context=ctx))
    assert downloaded["source_path"] == str(source)
    _inspect(ctx, downloaded["source_path"])

    assert _build(ctx, downloaded["clip_path"])["ok"]
    assert cover_fakes.apply_focus[0]["focus"] is None
    assert cover_fakes.apply_focus[0]["clean"] == CLEAN


def test_a_rejected_verdict_gives_no_subject_crop(cover_fakes, workdir):
    _needs_ffmpeg()
    source = _shots(workdir / "src.mp4", ("red", 12))
    ctx = _Ctx()
    cover_fakes.answer = _verdict(verdict="reject", problems=["talking_head"])
    inspected = _inspect(ctx, source)
    clip = _retrim(ctx, source, inspected["best_start_s"], 6)

    assert _build(ctx, clip["clip_path"])["ok"]
    assert cover_fakes.apply_focus == [{"media": clip["clip_path"], "focus": None, "clean": CLEAN}]


def test_use_inspection_false_skips_the_stored_crop(cover_fakes, workdir):
    _needs_ffmpeg()
    source = _shots(workdir / "src.mp4", ("red", 12))
    ctx = _Ctx()
    inspected = _inspect(ctx, source)
    clip = _retrim(ctx, source, inspected["best_start_s"], 6)

    result = _build(ctx, clip["clip_path"], use_inspection=False)
    assert result["ok"], result
    assert cover_fakes.apply_focus == []
    assert cover_fakes.compose == [clip["clip_path"]]


def test_an_explicit_subject_box_still_wins(cover_fakes, workdir):
    _needs_ffmpeg()
    source = _shots(workdir / "src.mp4", ("red", 12))
    ctx = _Ctx()
    _inspect(ctx, source)
    clip = _retrim(ctx, source, 7.0, 4)
    box = {"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.4}

    assert _build(ctx, clip["clip_path"], focus_x=0.1, focus_y=0.1, focus_w=0.3, focus_h=0.4)["ok"]
    assert cover_fakes.apply_focus[0]["focus"] == box


def test_an_approved_image_gets_its_crop(cover_fakes, workdir):
    image = workdir / "photo.png"
    image.write_bytes(b"not decoded: the sheet and the crop are fakes")
    ctx = _Ctx()
    _inspect(ctx, image, is_video=False)

    assert _build(ctx, image, is_video=False)["ok"]
    assert cover_fakes.apply_focus == [{"media": str(image), "focus": FOCUS, "clean": CLEAN}]


def test_a_rejected_image_still_gets_its_subject_box(cover_fakes, workdir):
    """The PTC collage: rejected for a corner bug, then built from as the
    best-scoring candidate. A still is the judged frame, so the group box is
    applied instead of the renderer's one-face crop."""
    image = workdir / "group.png"
    image.write_bytes(b"not decoded: the sheet and the crop are fakes")
    group = {"x": 0.03, "y": 0.08, "w": 0.94, "h": 0.85}
    ctx = _Ctx()
    cover_fakes.answer = _verdict(verdict="reject", focus=group, clean=None,
                                  problems=["publisher_branding"])
    _inspect(ctx, image, is_video=False)

    result = _build(ctx, image, is_video=False)
    assert result["ok"], result
    assert cover_fakes.apply_focus == [{"media": str(image), "focus": group, "clean": None}]


def _news_state(ctx, media_urls=(), research_media=()):
    from app.schemas import NewsItem, ResearchBrief
    from app.state import K_NEWS_ITEM, K_RESEARCH, set_model

    set_model(ctx.state, K_NEWS_ITEM, NewsItem(id="n1", title="Widget Agent launch",
                                                 media_urls=list(media_urls)))
    set_model(ctx.state, K_RESEARCH, ResearchBrief(
        summary="s", media_candidates=list(research_media), sources=["https://a.example/x"],
    ))


def test_find_source_clip_passes_the_research_media_through(workdir, monkeypatch):
    """save_research_brief merged them into media_urls; media_tools must know."""
    video = "https://www.youtube.com/watch?v=explainer01"
    calls = []
    monkeypatch.setattr(media_tools, "find_source_clip",
                        lambda *args: calls.append(args) or {"video_url": ""})
    ctx = _Ctx()
    _news_state(ctx, media_urls=[video], research_media=[video])

    asyncio.run(fpv.find_source_clip(tool_context=ctx))
    _news, _query, sources, research_media = calls[0]
    assert sources == ["https://a.example/x"] and research_media == [video]


def test_a_held_back_video_is_inspected_after_the_stills_use_up_the_budget(
    cover_fakes, workdir, monkeypatch
):
    """Up to five stills come first; the one video behind them still gets a look."""
    held = "https://www.youtube.com/watch?v=held01"
    monkeypatch.setattr(media_tools, "find_source_clip",
                        lambda *_a: {"image_first": True, "video_url": held})
    source = workdir / "src-held.mp4"
    clip = workdir / "clip-held.mp4"
    source.write_bytes(b"mp4")
    clip.write_bytes(b"mp4")
    monkeypatch.setattr(media_tools, "download_and_trim", lambda *_a: str(clip))
    ctx = _Ctx()
    _news_state(ctx)
    asyncio.run(fpv.find_source_clip(tool_context=ctx))

    cover_fakes.answer = _verdict(verdict="reject", problems=["stock"])
    for index in range(fpv._MAX_INSPECTIONS):
        image = workdir / f"still-{index}.png"
        image.write_bytes(b"png")
        assert _inspect(ctx, image, is_video=False)["inspections_left"] == fpv._MAX_INSPECTIONS - index - 1
    extra = workdir / "still-extra.png"
    extra.write_bytes(b"png")
    refused = asyncio.run(fpv.inspect_cover_media(str(extra), False, tool_context=ctx))
    assert refused["ok"] is False and "budget" in refused["error"]

    downloaded = asyncio.run(fpv.download_and_trim(held, tool_context=ctx))
    assert downloaded["ok"] and downloaded["source_path"] == str(source)
    cover_fakes.answer = _verdict()
    assert _inspect(ctx, downloaded["source_path"])["verdict"] == "use"
    # The reserve is one look, not a way around the cap.
    again = asyncio.run(fpv.inspect_cover_media(str(clip), True, tool_context=ctx))
    assert again["ok"] is False and "budget" in again["error"]


def test_an_ai_illustration_is_refused_and_so_is_any_clip_cut_from_it(cover_fakes, workdir):
    _needs_ffmpeg()
    source = _shots(workdir / "src.mp4", ("red", 12))
    image = workdir / "ChatGPT-Image.png"
    image.write_bytes(b"png")
    ctx = _Ctx()
    cover_fakes.answer = _verdict(verdict="reject", problems=["ai_illustration"])
    _inspect(ctx, image, is_video=False)
    _inspect(ctx, source)
    clip = _retrim(ctx, source, 2.5, 6)

    for media, is_video in ((image, False), (source, True), (clip["clip_path"], True)):
        for use_inspection in (True, False):
            result = _build(ctx, media, is_video, use_inspection=use_inspection)
            assert result["ok"] is False and "ai_illustration" in result["error"]
    assert cover_fakes.compose == [] and ctx.saved == []


def test_the_real_crop_reaches_the_renderer(workdir, monkeypatch):
    """End to end through cover_vision.apply_focus: a portrait crop, not the raw clip."""
    _needs_ffmpeg()
    source = _shots(workdir / "src.mp4", ("red", 12), size="1280x720")
    ctx = _Ctx()
    compose_calls: list[str] = []
    monkeypatch.setattr(cover_vision, "contact_sheet",
                        lambda *_a: SimpleNamespace(path=workdir / "s.jpg", timestamps=[0.5, 2.5]))
    monkeypatch.setattr(cover_vision, "judge_cover_media",
                        lambda *_a: _verdict(clean=None, best_frame=2))

    def compose(media_path, *_rest):
        compose_calls.append(media_path)
        (workdir / "c.mp4").write_bytes(b"x")
        (workdir / "c.png").write_bytes(b"x")
        return {"video_path": str(workdir / "c.mp4"), "poster_path": str(workdir / "c.png"),
                "duration_s": 5.0}

    monkeypatch.setattr(media_tools, "compose_cover", compose)
    monkeypatch.setattr(fpv, "hook_warnings", lambda *_a, **_k: [])
    inspected = _inspect(ctx, source)
    clip = _retrim(ctx, inspected["retrim_from"], inspected["best_start_s"], 6)

    assert _build(ctx, clip["clip_path"])["ok"]
    assert compose_calls and compose_calls[0] != clip["clip_path"]
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0:s=x", compose_calls[0]],
        check=True, capture_output=True, text=True, timeout=60,
    ).stdout.strip()
    width, height = (int(v) for v in out.split("x")[:2])
    assert width < 1280 and abs(width / height - 0.8) < 0.05


# ---------------------------------------------------------------------------
# The instruction the agent reads
# ---------------------------------------------------------------------------


def test_the_skill_file_and_its_fallback_copy_match():
    text = (REPO / "skills/agents/first_page_visual.md").read_text(encoding="utf-8")
    assert text == fpv.DEFAULT_INSTRUCTION


def test_the_instruction_follows_the_tools():
    text = (REPO / "skills/agents/first_page_visual.md").read_text(encoding="utf-8")
    ladder = text[text.index("## Workflow"):text.index("## Failure handling")]
    # Rung 2 only downloads what find_source_clip picked as the video.
    assert "If it returned a video (is_video true)" in ladder
    # A held-back third-party video is the last resort after the images.
    assert "video_url" in ladder and "every image you inspect is rejected" in ladder
    assert "image_first=true" not in ladder
    assert "retrim_from" in ladder and "held_s" in ladder
    assert "ai_illustration" in ladder and "use_inspection" in ladder
    rework = text[text.index("## Rework"):]
    assert "inspect_cover_media on the new" in rework and "use_inspection=false" in rework


def test_inspecting_a_url_or_missing_file_costs_no_inspection(cover_fakes, workdir):
    """A real run inspected the image URL before downloading it and lost one
    of its four looks; the error now says what to do and charges nothing."""
    ctx = _Ctx()
    for bad in ("https://images.example.com/photo.png", str(workdir / "missing.png")):
        result = asyncio.run(fpv.inspect_cover_media(bad, False, tool_context=ctx))
        assert result["ok"] is False and "Download it first" in result["error"]
        assert result["inspections_left"] == fpv._MAX_INSPECTIONS
    assert int(ctx.state.get(fpv._K_INSPECTIONS) or 0) == 0


def test_a_candidate_rejected_below_the_floor_is_not_built_from(cover_fakes, workdir):
    """Every candidate was rejected and the agent built from a text card that
    scored 1: the cover was a blurred banner. The placeholder is better."""
    card = workdir / "text-card.png"
    card.write_bytes(b"png")
    ctx = _Ctx()
    weak = _verdict(verdict="reject", problems=["text_heavy"])
    weak["score"] = 1
    cover_fakes.answer = weak
    _inspect(ctx, card, is_video=False)

    refused = _build(ctx, card, is_video=False)
    assert refused["ok"] is False and "create_placeholder_background" in refused["error"]
    assert cover_fakes.compose == []


def test_a_rejected_candidate_at_the_floor_can_still_be_the_fallback(cover_fakes, workdir):
    image = workdir / "ok-ish.png"
    image.write_bytes(b"png")
    ctx = _Ctx()
    cover_fakes.answer = _verdict(verdict="reject", problems=["publisher_branding"])  # score 3
    _inspect(ctx, image, is_video=False)
    assert _build(ctx, image, is_video=False)["ok"]
