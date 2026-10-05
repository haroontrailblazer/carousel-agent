"""A reviewer can put any hook on the cover in seconds, and it teaches the planner.

The cover is the saved untitled picture with the title composed on top, so a
new hook only needs that composition again: no model, no rework. Every hook
the reviewer picks or writes is kept as an example of what this client
approves, and the planner sees those examples on the next run.
"""
import asyncio
import shutil
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from google.genai import types
from PIL import Image

from app import cover_retitle, hook_examples
from app import state as s
from app.config import settings
from app.schemas import CarouselDesign
from web_api import routes_runs


class _Artifacts:
    """The slice of the artifact service retitling uses, in memory."""

    def __init__(self, files=None):
        self.files = dict(files or {})
        self.saved: list[str] = []

    async def load_artifact(self, *, app_name, user_id, session_id, filename, version=None):
        data = self.files.get(filename)
        return None if data is None else types.Part.from_bytes(data=data, mime_type="application/octet-stream")

    async def save_artifact(self, *, app_name, user_id, session_id, filename, artifact):
        self.files[filename] = artifact.inline_data.data
        self.saved.append(filename)
        return len(self.saved)


def _png_bytes(tmp_path) -> bytes:
    path = tmp_path / "base.png"
    Image.new("RGB", (1080, 1350), (40, 90, 140)).save(path)
    return path.read_bytes()


def _state(base_artifact="cover-base.png"):
    design = CarouselDesign(logo_visible=False, handle_visible=False)
    return {
        s.K_COVER: {"title": "OLD FLAT HOOK", "highlight": "FLAT HOOK", "video_artifact": "cover.mp4",
                    "poster_artifact": "cover-poster.png", "base_artifact": base_artifact,
                    "used_fallback_image": True, "duration_s": 6.0},
        s.K_PLAN: {"hook_title": "OLD FLAT HOOK", "hook_highlight": "FLAT HOOK", "style": "prose",
                   "slide_count": 3, "slides": [{"index": 2, "purpose": "p", "key_points": ["k"]}]},
        s.K_BUNDLE: {"cover": {"title": "OLD FLAT HOOK", "video_artifact": "cover.mp4"},
                     "ordered_artifacts": ["cover.mp4"]},
        s.K_DESIGN: design.model_dump(mode="json"),
    }


def test_a_highlight_that_is_not_in_the_hook_is_dropped():
    assert cover_retitle.clean_hook("  53 CHATGPT PHOTOS LEAKED.  NOBODY HACKED IN. ", "NOT THERE") == (
        "53 CHATGPT PHOTOS LEAKED. NOBODY HACKED IN.", "")


@pytest.mark.parametrize("bad", ["", "   ", "AN EM DASH — IS NOT ALLOWED"])
def test_text_the_cover_cannot_publish_is_refused(bad):
    with pytest.raises(ValueError):
        cover_retitle.clean_hook(bad, "")


def test_a_new_hook_is_drawn_on_the_saved_picture_and_every_copy_of_the_title_moves(tmp_path):
    if not shutil.which(settings.ffmpeg_bin):
        pytest.skip("FFmpeg is required")
    artifacts = _Artifacts({"cover-base.png": _png_bytes(tmp_path)})
    result = asyncio.run(cover_retitle.retitle_cover(
        "run-x", _state(), "53 CHATGPT PHOTOS LEAKED. NOBODY HACKED IN.", "NOBODY HACKED IN", artifacts))

    assert sorted(artifacts.saved) == ["cover-poster.png", "cover.mp4"]
    with Image.open(__import__("io").BytesIO(artifacts.files["cover-poster.png"])) as poster:
        assert poster.size == (1080, 1350)
    updates = result["state"]
    assert updates[s.K_COVER]["title"] == "53 CHATGPT PHOTOS LEAKED. NOBODY HACKED IN."
    assert updates[s.K_COVER]["highlight"] == "NOBODY HACKED IN"
    assert updates[s.K_COVER]["base_artifact"] == "cover-base.png"  # the next swap still works
    assert updates[s.K_PLAN]["hook_title"] == updates[s.K_COVER]["title"]
    assert updates[s.K_BUNDLE]["cover"]["title"] == updates[s.K_COVER]["title"]


def test_a_cover_built_before_retitling_existed_says_so():
    with pytest.raises(cover_retitle.RetitleUnavailable):
        asyncio.run(cover_retitle.retitle_cover("run-x", _state(base_artifact=""), "NEW HOOK HERE", "", _Artifacts()))


def test_the_endpoint_writes_the_cover_and_remembers_the_choice(monkeypatch):
    state = _state()
    state.update({"phase": "review", "qa_report": {"passed": True}, s.K_NEWS_ITEM: {"title": "Story"}})
    writes, examples = {}, []

    async def session_state(_run_id):
        return state

    async def pending(_run_id):
        return {"run_id": "run-x"}

    async def retitle(run_id, st, title, highlight, artifacts):
        cover = {**st[s.K_COVER], "title": title, "highlight": highlight}
        return {"state": {s.K_COVER: cover}, "warnings": [], "cover": cover}

    async def write(run_id, key, value):
        writes[key] = value

    async def record(hook, highlight, story, source):
        examples.append((hook, highlight, story, source))

    monkeypatch.setattr(routes_runs, "_session_state", session_state)
    monkeypatch.setattr(routes_runs.db, "load_pending_review", pending)
    monkeypatch.setattr(cover_retitle, "retitle_cover", retitle)
    monkeypatch.setattr(routes_runs, "_write_state_key", write)
    monkeypatch.setattr(hook_examples, "record", record)
    monkeypatch.setattr(routes_runs.runtime, "artifact_service", lambda: None)

    body = routes_runs.CoverTitleRequest(title="YOUR HOOK, YOUR WORDS", highlight="YOUR WORDS", source="typed")
    out = asyncio.run(routes_runs.set_cover_title("run-x", body, None))
    assert out["title"] == "YOUR HOOK, YOUR WORDS"
    assert writes[s.K_COVER]["title"] == "YOUR HOOK, YOUR WORDS"
    assert examples == [("YOUR HOOK, YOUR WORDS", "YOUR WORDS", "Story", "typed")]


def test_the_endpoint_refuses_a_carousel_that_is_not_waiting_for_review(monkeypatch):
    async def session_state(_run_id):
        return {"phase": "generate"}

    monkeypatch.setattr(routes_runs, "_session_state", session_state)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(routes_runs.set_cover_title(
            "run-x", routes_runs.CoverTitleRequest(title="ANY HOOK"), None))
    assert caught.value.status_code == 409


def test_the_review_screen_knows_which_covers_can_be_retitled():
    assert routes_runs._hook_options({}, "") == []
    # can_retitle is read straight from the CoverSpec's base_artifact.
    assert bool(_state()[s.K_COVER]["base_artifact"]) is True
    assert bool(_state(base_artifact="")[s.K_COVER]["base_artifact"]) is False


def test_chosen_hooks_are_kept_newest_last_without_duplicates(monkeypatch):
    store = {}

    async def update_config(key, fn):
        store[key] = fn(store.get(key, {}))
        return store[key]

    monkeypatch.setattr(hook_examples.db, "update_config", update_config)
    for hook in ["FIRST HOOK. A TWIST.", "SECOND HOOK", "first hook. a twist."]:
        asyncio.run(hook_examples.record(hook, "", "story", "picked"))
    items = store[hook_examples.CONFIG_KEY]["items"]
    assert [i["hook"] for i in items] == ["SECOND HOOK", "first hook. a twist."]

    for n in range(hook_examples.MAX_KEPT + 5):
        asyncio.run(hook_examples.record(f"HOOK NUMBER {n}", "", "", "typed"))
    assert len(store[hook_examples.CONFIG_KEY]["items"]) == hook_examples.MAX_KEPT


def test_the_planner_sees_the_newest_choices_first():
    note = hook_examples.prompt_note([
        {"hook": "OLDER PICK", "source": "picked", "story": "a"},
        {"hook": "NEWEST TYPED. WITH A TWIST.", "source": "typed", "story": "b"},
    ])
    assert note.index("NEWEST TYPED") < note.index("OLDER PICK")
    assert "[reviewer wrote]" in note and "[reviewer picked]" in note
    assert "Never copy their facts" in note
    assert hook_examples.prompt_note([]) == ""
