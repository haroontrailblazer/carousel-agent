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
    assert sheet.timestamps[0] < 1.0 and sheet.timestamps[-1] > 8.0


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
    assert verdict["focus"]["h"] == 1.0
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
    def judge(sheet, story, hook):
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

    def boom(*_args):
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
    assert "focus_x" in text
