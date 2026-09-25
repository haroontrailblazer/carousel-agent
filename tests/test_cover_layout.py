"""Where the cover crop lands, given the judge's subject and clean boxes.

No network or API calls: the vision model is replaced by a fake client and
every image or clip is drawn locally.
"""
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from app.config import settings
from app.tools import cover_vision

_PERSON = (236, 150, 96)


def _px(source_w, source_h, box):
    """A 0-1 box as (x0, y0, x1, y1) source pixels."""
    return (
        box["x"] * source_w, box["y"] * source_h,
        (box["x"] + box["w"]) * source_w, (box["y"] + box["h"]) * source_h,
    )


def _holds(crop, subject):
    left, top, width, height = crop
    x0, y0, x1, y1 = subject
    return left <= x0 and top <= y0 and x1 <= left + width and y1 <= top + height


def _needs_ffmpeg():
    if not shutil.which(settings.ffmpeg_bin) or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and ffprobe are required")


def _fake_client(answer: dict):
    def create(**_kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(answer)))],
            usage=None,
        )

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


# --- the subject box places the crop, even when the crop is full height -----


def test_a_tall_subject_in_a_wide_frame_is_placed_by_its_box():
    """A keynote frame: slide on the left, the speaker on the right.

    The 4:5 crop is full height, so only its horizontal position matters. It
    used to count as "the whole frame" and was dropped, and the renderer's
    saliency crop then kept the busy slide and none of the speaker.
    """
    focus = {"x": 0.65, "y": 0.14, "w": 0.25, "h": 0.86}
    layout = cover_vision.plan_layout(1920, 1080, focus)
    assert layout is not None and layout.mode == "crop"
    assert layout.box[3] == 1080 and abs(layout.box[2] / layout.box[3] - 0.8) < 0.01
    x0, _, x1, _ = _px(1920, 1080, focus)
    assert layout.box[0] <= x0 and x1 <= layout.box[0] + layout.box[2]


def test_the_speaker_not_the_slide_ends_up_on_the_cover(tmp_path):
    source = tmp_path / "keynote.png"
    image = Image.new("RGB", (1920, 1080), "white")
    draw = ImageDraw.Draw(image)
    for row in range(120, 960, 48):  # a text-heavy slide on the left
        draw.rectangle((80, row, 1100, row + 20), fill="black")
    draw.ellipse((1400, 160, 1640, 420), fill=_PERSON)  # head
    draw.rectangle((1330, 420, 1710, 1080), fill=_PERSON)  # body
    image.save(source)

    out = cover_vision.apply_focus(
        str(source), False, {"x": 0.68, "y": 0.14, "w": 0.23, "h": 0.86}, str(tmp_path)
    )
    assert out != str(source)
    with Image.open(out) as cover:
        total = cover.width * cover.height
        colors = dict((color, count) for count, color in cover.convert("RGB").getcolors(total))
    assert colors.get(_PERSON, 0) / total > 0.25


def test_a_480p_source_is_still_cropped_to_its_subject():
    """Under 600 px tall the smallest crop is the full height, which used to
    make every focus a no-op on 480p and 360p clips."""
    focus = {"x": 0.7, "y": 0.1, "w": 0.15, "h": 0.3}
    for size in ((854, 480), (640, 360)):
        box = cover_vision.focus_crop_box(*size, focus)
        assert box is not None, size
        assert box[3] == size[1] // 2 * 2
        mid_x = (focus["x"] + focus["w"] / 2) * size[0]
        assert box[0] <= mid_x <= box[0] + box[2]


def test_a_clean_area_under_600_px_still_honours_the_subject():
    """A 720p frame minus its lower-third leaves a 576 px tall clean area."""
    focus = {"x": 0.7, "y": 0.1, "w": 0.15, "h": 0.3}
    clean = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.8}
    layout = cover_vision.plan_layout(1280, 720, focus, clean)
    assert layout is not None and layout.mode == "crop"
    left, top, width, height = layout.box
    assert top + height <= 576
    assert _holds(layout.box, _px(1280, 720, focus))
    assert width < 1280  # a placed crop, not the whole clean strip


def test_a_480p_clip_is_cropped_by_ffmpeg(tmp_path):
    _needs_ffmpeg()
    source = tmp_path / "source.mp4"
    subprocess.run(
        [settings.ffmpeg_bin, "-y", "-f", "lavfi", "-i", "testsrc2=size=854x480:rate=24",
         "-t", "2", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
        check=True, capture_output=True, timeout=60,
    )
    out = cover_vision.apply_focus(
        str(source), True, {"x": 0.7, "y": 0.1, "w": 0.15, "h": 0.3}, str(tmp_path)
    )
    assert out != str(source)
    assert cover_vision._video_size(Path(out)) == (384, 480)


def test_a_portrait_source_keeps_the_face():
    """A 9:16 frame: a face at the top, a busy caption card below it.

    The crop is full width, so its vertical position decides everything.
    """
    face = {"x": 0.185, "y": 0.047, "w": 0.63, "h": 0.25}  # y 90-570 px
    for focus in (face, {**face, "h": 0.4}):  # the face alone, or face and shoulders
        layout = cover_vision.plan_layout(1080, 1920, focus)
        assert layout is not None and layout.mode == "crop"
        left, top, width, height = layout.box
        assert top <= 90  # the top of the head is kept
        assert 90 + 480 <= top + height * 0.56  # the face reads above the title shadow
        assert abs(width / height - 0.8) < 0.01


def test_a_subject_filling_a_portrait_source_is_left_to_the_normal_crop():
    assert cover_vision.focus_crop_box(1080, 1350, {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8}) is None
    assert cover_vision.plan_layout(1080, 1920, {"x": 0, "y": 0, "w": 1, "h": 1}) is None


# --- wide subjects and the null box -----------------------------------------


def test_a_wide_collage_with_an_explicit_box_becomes_a_band(tmp_path):
    """Three researchers side by side: all of them, across the top."""
    focus = {"x": 0.03, "y": 0.08, "w": 0.94, "h": 0.85}  # what run 01239e778f58 got
    layout = cover_vision.plan_layout(1280, 720, focus)
    assert layout is not None and layout.mode == "band"
    x0, _, x1, _ = _px(1280, 720, focus)
    assert layout.box[0] <= x0 and x1 <= layout.box[0] + layout.box[2]
    assert 0 < layout.band_h < settings.slide_height

    source = tmp_path / "collage.png"
    image = Image.new("RGB", (1280, 720), "#203040")
    draw = ImageDraw.Draw(image)
    for left in (80, 480, 880):
        draw.ellipse((left, 120, left + 320, 600), fill=_PERSON)
    image.save(source)
    out = cover_vision.apply_focus(str(source), False, focus, str(tmp_path))
    with Image.open(out) as cover:
        assert cover.size == (settings.slide_width, settings.slide_height)
        assert cover.convert("RGB").getpixel((settings.slide_width // 2, settings.slide_height - 20)) == (0, 0, 0)


def test_a_group_too_wide_for_any_4_5_crop_becomes_a_band_even_when_not_very_wide():
    """Two or three people standing: under 1.2 wide for their height, but
    wider than an 864 px crop of a 1080 px tall frame."""
    for focus in ({"x": 0.2, "y": 0.05, "w": 0.6, "h": 0.9}, {"x": 0.22, "y": 0.05, "w": 0.56, "h": 0.9}):
        layout = cover_vision.plan_layout(1920, 1080, focus)
        assert layout is not None and layout.mode == "band", focus
        x0, _, x1, _ = _px(1920, 1080, focus)
        assert layout.box[0] <= x0 and x1 <= layout.box[0] + layout.box[2]
    # One tall person still gets a crop.
    layout = cover_vision.plan_layout(1920, 1080, {"x": 0.4, "y": 0.05, "w": 0.3, "h": 0.9})
    assert layout is not None and layout.mode == "crop"


def test_no_box_leaves_the_renderer_crop_alone():
    """null still means "any 4:5 part works": a landscape scene is not banded."""
    assert cover_vision.plan_layout(1280, 720, None) is None
    assert cover_vision.plan_layout(1920, 1080, None, None) is None


def test_the_judge_must_box_group_shots_even_when_they_fill_the_frame():
    prompt = cover_vision._JUDGE_SYSTEM
    assert "group shot" in prompt
    assert "even when it covers nearly the whole frame" in prompt
    assert "Use null only" in prompt


def test_the_judge_may_trim_a_corner_bug_off_the_subject_as_the_code_allows():
    """A bug touching a group is cropped away, not a reason to reject the photo."""
    prompt = " ".join(cover_vision._JUDGE_SYSTEM.split())
    assert "must contain the whole focus" not in prompt
    assert "up to about a tenth" in prompt and "never its top" in prompt
    focus = {"x": 0.03, "y": 0.08, "w": 0.94, "h": 0.85}
    # The PTC collage: its bug sits in the top right corner, over the third
    # researcher's hair. Trimming that side keeps all three faces.
    clean = {"x": 0.0, "y": 0.0, "w": 0.9, "h": 1.0}
    assert cover_vision._trusted_clean(focus, clean) == clean


# --- the clean box keeps logos out, but never at the subject's expense ------


def test_a_clean_box_keeps_a_corner_logo_out():
    """A channel bug in the top-right corner, the subject just left of it."""
    logo = (0.86 * 1920, 0.03 * 1080, 0.97 * 1920, 0.12 * 1080)
    focus = {"x": 0.62, "y": 0.25, "w": 0.2, "h": 0.7}
    unclean = cover_vision.plan_layout(1920, 1080, focus)
    assert unclean.box[0] + unclean.box[2] > logo[0]  # the plain crop would show it

    layout = cover_vision.plan_layout(1920, 1080, focus, {"x": 0.0, "y": 0.0, "w": 0.85, "h": 1.0})
    assert layout is not None and layout.mode == "crop"
    assert layout.box[0] + layout.box[2] <= 0.85 * 1920
    assert _holds(layout.box, _px(1920, 1080, focus))


def test_a_band_starts_below_a_logo_above_the_group():
    focus = {"x": 0.03, "y": 0.13, "w": 0.94, "h": 0.8}
    layout = cover_vision.plan_layout(1280, 720, focus, {"x": 0.0, "y": 0.12, "w": 1.0, "h": 0.88})
    assert layout is not None and layout.mode == "band"
    assert layout.box[1] >= int(0.12 * 720)


def test_a_clean_box_that_starts_below_the_heads_is_ignored():
    """A ticker above a group: leaving it out would cut 14% off the top of the heads."""
    focus = {"x": 0.03, "y": 0.02, "w": 0.94, "h": 0.9}
    layout = cover_vision.plan_layout(1920, 1080, focus, {"x": 0.0, "y": 0.15, "w": 1.0, "h": 0.85})
    assert layout is not None and layout.mode == "band"
    assert layout.box[1] <= focus["y"] * 1080


def test_a_clean_box_may_trim_a_little_off_the_bottom_of_the_subject():
    """A lower-third over the speaker's waist stays out; the face stays in."""
    focus = {"x": 0.3, "y": 0.1, "w": 0.3, "h": 0.8}
    layout = cover_vision.plan_layout(1920, 1080, focus, {"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.84})
    assert layout is not None and layout.mode == "crop"
    left, top, width, height = layout.box
    assert top <= focus["y"] * 1080 and top + height <= 0.84 * 1080


def test_a_clean_box_that_misses_the_subject_is_ignored():
    """Subject on the right, "clean" on the left: the old crop had none of it."""
    focus = {"x": 0.6, "y": 0.1, "w": 0.3, "h": 0.6}
    layout = cover_vision.plan_layout(1920, 1080, focus, {"x": 0.0, "y": 0.0, "w": 0.55, "h": 1.0})
    assert layout is not None
    assert _holds(layout.box, _px(1920, 1080, focus))


def test_a_clean_box_that_would_halve_the_subject_is_ignored():
    focus = {"x": 0.35, "y": 0.1, "w": 0.3, "h": 0.8}
    layout = cover_vision.plan_layout(1920, 1080, focus, {"x": 0.0, "y": 0.0, "w": 0.5, "h": 1.0})
    x0, _, x1, _ = _px(1920, 1080, focus)
    assert layout.box[0] <= x0 and x1 <= layout.box[0] + layout.box[2]


def test_a_tiny_clean_box_is_ignored():
    """The model boxed the logo itself: that used to become a 152x64 cover."""
    tiny = {"x": 0.9, "y": 0.02, "w": 0.08, "h": 0.06}
    assert cover_vision.plan_layout(1920, 1080, None, tiny) is None
    focus = {"x": 0.3, "y": 0.1, "w": 0.3, "h": 0.8}
    layout = cover_vision.plan_layout(1920, 1080, focus, tiny)
    assert layout is not None and layout.box[2] >= 480
    assert _holds(layout.box, _px(1920, 1080, focus))


# --- AI pictures are never a cover -------------------------------------------


def test_ai_illustration_forces_a_reject():
    assert "ai_illustration" in cover_vision.PROBLEMS
    assert "ai_illustration" in cover_vision._JUDGE_SYSTEM
    answer = {"best_frame": 1, "score": 8, "verdict": "use", "usable_frames": 1,
              "problems": ["AI_Illustration"], "focus": None, "reason": "striking art"}
    verdict = cover_vision._parse_verdict(json.dumps(answer), 1)
    assert verdict["verdict"] == "reject"
    assert verdict["problems"] == ["ai_illustration"]
    assert "striking art" in verdict["reason"]


def test_the_judge_rejects_an_ai_picture_it_liked(tmp_path):
    still = tmp_path / "chatgpt-image.png"
    Image.new("RGB", (1200, 800), "#8040c0").save(still)
    sheet = cover_vision.contact_sheet(str(still), False, str(tmp_path))
    answer = {"best_frame": 1, "score": 9, "verdict": "use",
              "problems": "ai_illustration", "focus": None, "reason": "clear subject"}
    verdict = cover_vision.judge_cover_media(sheet, "story", "HOOK", _fake_client(answer))
    assert verdict["verdict"] == "reject" and verdict["problems"] == ["ai_illustration"]
    assert set(verdict) >= {"best_frame", "score", "verdict", "problems", "usable_frames",
                            "focus", "clean", "reason"}
