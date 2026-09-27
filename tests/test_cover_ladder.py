"""The cover's sourcing ladder: what build_cover may build from, and in what order.

Two AI-model launch runs shipped a plain black cover with the title: the
companies' own artwork was judged "AI art", every chart scored below 3, and
nothing sat between "all rejected" and the drawn background. A BBC run never
built a cover at all, because find_source_clip was called about 21 times.
These tests pin the ladder offline: the judge, the Wikimedia lookup, the
downloads and the renderer are fakes, and nothing touches the network.
"""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.adk.sessions.state import State

from app.agents import first_page_visual as fpv
from app.schemas import CarouselPlan, CoverSpec, NewsItem, ResearchBrief, ResearchFact
from app.state import K_COVER, K_NEWS_ITEM, K_PLAN, K_RESEARCH, K_TIME_WINDOW, set_model
from app.tools import cover_vision, media_tools, reference_photos

TITLE = "ROBOT LEARNS TO WALK"
CREDIT = "Jane Roe / Wikimedia Commons, CC BY 4.0"
STEP_KEYS = (
    "temp:cover_fsc_calls", "temp:cover_fsc_cache", "temp:cover_page_cache", "temp:cover_failed_urls",
    "temp:cover_reference_tried", "temp:cover_reference_calls", "temp:cover_reference_urls",
    "temp:cover_reference_files", "temp:cover_reference_inspected", "temp:cover_official_sites",
)


class _Ctx:
    """The slice of ToolContext the cover tools use, over a real ADK State."""

    def __init__(self):
        self.state = State({}, {})
        self.saved: list[str] = []

    async def save_artifact(self, filename, part):
        self.saved.append(filename)
        return len(self.saved)


def _verdict(kind="real_photo", verdict="use", score=7, problems=()):
    return {
        "best_frame": 1, "score": score, "verdict": verdict, "problems": list(problems),
        "usable_frames": 1, "focus": None, "clean": None, "reason": "test", "kind": kind,
    }


def _ref(url, subject="Anthony Albanese"):
    return {
        "url": url, "origin": "wikimedia", "score": 7, "reason": "Wikidata image",
        "context_url": "https://commons.wikimedia.org/wiki/File:Albanese_2022.jpg",
        "credit": CREDIT, "licence": "CC BY 4.0", "subject": subject, "role": "subject",
        "width": 1600, "height": 2000,
    }


def _clip_result(url="", candidates=(), found=True, note="picked"):
    """The full media_tools.find_source_clip contract, with image candidates."""
    images = [
        {"url": u, "origin": "source_page", "score": 50 - i, "reason": "og:image",
         "context_url": "https://news.example/story", "width": 1600, "height": 900}
        for i, u in enumerate(candidates)
    ]
    return {
        "found": found, "url": url, "is_video": False, "duration_s": 0.0,
        "origin": "source_page" if url else "", "image_url": url,
        "image_origin": "source_page" if url else "", "image_candidates": images,
        "image_first": False, "video_url": "", "video_duration_s": 0.0, "video_origin": "",
        "trend_search": "ok", "note": note,
    }


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    """Fake judge, sheet, renderer, downloads and Wikimedia lookup (no network)."""
    calls = SimpleNamespace(judge=[], compose=[], answer=_verdict(), answers={},
                            refs=[], lookups=[], downloads={}, searches=[], clip=_clip_result())
    monkeypatch.setattr(fpv, "_run_workdir", lambda _ctx: str(tmp_path))

    def sheet(media_path, is_video, _workdir=""):
        return SimpleNamespace(path=tmp_path / "sheet.jpg", timestamps=[0.0], media=media_path)

    def judge(sheet, story, hook, client=None, **kwargs):
        calls.judge.append(kwargs)
        return dict(calls.answers.get(Path(sheet.media).name, calls.answer))

    def compose(media_path, title, highlight, is_video, _workdir, _design):
        calls.compose.append(media_path)
        video, poster = tmp_path / "cover.mp4", tmp_path / "cover.png"
        video.write_bytes(b"mp4")
        poster.write_bytes(b"png")
        return {"video_path": str(video), "poster_path": str(poster), "duration_s": 6.0}

    def download(url, workdir=""):
        if url not in calls.downloads:
            raise RuntimeError(f"image is unsuitable for a 1080x1350 cover: {url}")
        path = tmp_path / calls.downloads[url]
        path.write_bytes(b"img")
        return str(path)

    def lookup(subjects, **_kwargs):
        calls.lookups.append(list(subjects))
        return [dict(ref) for ref in calls.refs]

    def search(news, query="", sources=None, media=None, **kwargs):
        calls.searches.append({"query": query, **kwargs})
        return dict(calls.clip)

    monkeypatch.setattr(cover_vision, "contact_sheet", sheet)
    monkeypatch.setattr(cover_vision, "judge_cover_media", judge)
    monkeypatch.setattr(cover_vision, "official_source", lambda *_a, **_k: False)
    monkeypatch.setattr(media_tools, "compose_cover", compose)
    monkeypatch.setattr(media_tools, "download_image", download)
    monkeypatch.setattr(media_tools, "find_source_clip", search)
    monkeypatch.setattr(reference_photos, "reference_candidates", lookup)
    monkeypatch.setattr(reference_photos, "official_sites", lambda *_a, **_k: [])
    monkeypatch.setattr(reference_photos, "story_subjects",
                        lambda *_a, **_k: ["Anthony Albanese", "Canberra"])
    monkeypatch.setattr(fpv, "hook_warnings", lambda *_a, **_k: [])
    return calls


def _run(coro):
    return asyncio.run(coro)


def _file(tmp_path, name) -> Path:
    path = tmp_path / name
    path.write_bytes(b"img")
    return path


def _inspect(ctx, path, is_video=False) -> dict:
    result = _run(fpv.inspect_cover_media(str(path), is_video, tool_context=ctx))
    assert result["ok"], result
    return result


def _build(ctx, path, is_video=False, **kwargs) -> dict:
    return _run(fpv.build_cover(str(path), is_video, title=TITLE, tool_context=ctx, **kwargs))


def _spec(ctx) -> CoverSpec:
    return CoverSpec.model_validate(ctx.state[K_COVER])


def _news_state(ctx):
    set_model(ctx.state, K_NEWS_ITEM, NewsItem(
        id="n1", title="Albanese pledges hospital funding", summary="A health-system story.",
        source_name="BBC News", source_url="https://www.bbc.co.uk/news/articles/abc",
    ))
    set_model(ctx.state, K_RESEARCH, ResearchBrief(
        summary="Research summary.", key_facts=[ResearchFact(fact="Mistral released a model.")],
    ))


# ---------------------------------------------------------------------------
# What may be built from
# ---------------------------------------------------------------------------


def _official(monkeypatch, level=cover_vision.OFFICIAL_EXACT):
    """The picture's page is the story company's own (mistral.ai for Mistral)."""
    monkeypatch.setattr(cover_vision, "official_source", lambda *_a, **_k: level)


def _downloaded(ctx, path, url="https://mistral.ai/img/key-art.png"):
    """Record where a test file was downloaded from, as download_image does."""
    fpv._record_download(ctx, url, [str(path)])
    return path


def test_the_companys_own_artwork_is_built_even_when_the_judge_rejected_it(fakes, tmp_path, monkeypatch):
    """Mistral's key art, rejected with a score of 2, beats a plain background."""
    _official(monkeypatch)
    ctx = _Ctx()
    art = _downloaded(ctx, _file(tmp_path, "mistral-key-art.png"))
    fakes.answer = _verdict("official_visual", "reject", 2, ["publisher_branding"])
    inspected = _inspect(ctx, art)
    assert inspected["kind"] == "official_visual" and inspected["tier"] == 2

    built = _build(ctx, art)
    assert built["ok"], built
    assert built["drawn_background"] is False and fakes.compose


def test_a_weak_text_graphic_waits_for_the_reference_rung_then_beats_the_plain_background(
    fakes, tmp_path
):
    ctx = _Ctx()
    chart = _file(tmp_path, "benchmark-chart.png")
    fakes.answer = _verdict("text_graphic", "reject", 1, ["document_or_slide"])
    _inspect(ctx, chart)

    refused = _build(ctx, chart)
    assert refused["ok"] is False and "find_reference_photo" in refused["error"]
    assert "create_placeholder_background" not in refused["error"]

    looked = _run(fpv.find_reference_photo(tool_context=ctx))  # Wikimedia has nothing
    assert looked["found"] is False and ctx.state.get("temp:cover_reference_tried") is True

    background = _run(fpv.create_placeholder_background(tool_context=ctx))["path"]
    refused = _build(ctx, background)
    assert refused["ok"] is False and "best_so_far" in refused["error"]
    assert "benchmark-chart.png" in refused["error"]

    built = _build(ctx, chart)
    assert built["ok"], built
    assert built["drawn_background"] is False and _spec(ctx).drawn_background is False


def test_a_weak_text_graphic_is_refused_while_a_real_or_official_picture_exists(fakes, tmp_path, monkeypatch):
    _official(monkeypatch)
    ctx = _Ctx()
    chart, art = _file(tmp_path, "chart.png"), _downloaded(ctx, _file(tmp_path, "art.png"))
    fakes.answers = {
        "chart.png": _verdict("text_graphic", "reject", 1),
        "art.png": _verdict("official_visual", "reject", 4),
    }
    _inspect(ctx, chart)
    _inspect(ctx, art)
    _run(fpv.find_reference_photo(tool_context=ctx))

    refused = _build(ctx, chart)
    assert refused["ok"] is False and "art.png" in refused["error"]
    assert "create_placeholder_background" not in refused["error"]


def test_the_plain_background_is_refused_until_the_reference_rung_found_nothing(fakes, tmp_path):
    ctx = _Ctx()
    background = _run(fpv.create_placeholder_background(tool_context=ctx))["path"]
    assert Path(background).name == media_tools.PLACEHOLDER_NAME

    refused = _build(ctx, background)
    assert refused["ok"] is False and "find_reference_photo" in refused["error"]
    assert fakes.compose == []

    assert _run(fpv.find_reference_photo(tool_context=ctx))["found"] is False
    built = _build(ctx, background, source_media_url=str(background))
    assert built["ok"], built
    assert built["drawn_background"] is True
    assert any("no picture" in warning for warning in built["warnings"])
    spec = _spec(ctx)
    assert spec.drawn_background is True
    assert spec.source_media_url == "" and spec.source_origin == "" and spec.source_credit == ""
    texts = [value for value in spec.model_dump().values() if isinstance(value, str)]
    assert not any("placeholder" in text.lower() for text in texts)


@pytest.mark.parametrize("kind,problems,expected", [
    ("ai_art", ["ai_illustration"], "the cover is never AI-generated"),
    ("unusable", ["low_quality"], "judged unusable (low_quality)"),
])
def test_tier_zero_media_is_never_built(fakes, tmp_path, kind, problems, expected):
    ctx = _Ctx()
    picture = _file(tmp_path, "picture.png")
    fakes.answer = _verdict(kind, "reject", 6, problems)
    assert _inspect(ctx, picture)["tier"] == 0
    _run(fpv.find_reference_photo(tool_context=ctx))

    for use_inspection in (True, False):
        refused = _build(ctx, picture, use_inspection=use_inspection)
        assert refused["ok"] is False and expected in refused["error"]
    if kind == "ai_art":
        assert "ai_illustration" in refused["error"]
    # Tier 0 is not a picture: the plain background is allowed next to it.
    background = _run(fpv.create_placeholder_background(tool_context=ctx))["path"]
    assert _build(ctx, background)["ok"]


def test_media_that_was_never_inspected_stays_buildable(fakes, tmp_path):
    ctx = _Ctx()
    assert _build(ctx, _file(tmp_path, "unseen.jpg"))["ok"]


def test_best_so_far_prefers_an_approved_official_visual_over_a_rejected_photo(fakes, tmp_path, monkeypatch):
    _official(monkeypatch)
    ctx = _Ctx()
    photo, art = _file(tmp_path, "photo.jpg"), _downloaded(ctx, _file(tmp_path, "art.png"))
    fakes.answers = {
        "photo.jpg": _verdict("real_photo", "reject", 6, ["publisher_branding"]),
        "art.png": _verdict("official_visual", "use", 5),
    }
    assert _inspect(ctx, photo)["best_so_far"]["path"] == str(photo)
    best = _inspect(ctx, art)["best_so_far"]
    assert best["path"] == str(art)
    assert (best["kind"], best["tier"], best["verdict"], best["score"]) == ("official_visual", 2, "use", 5)

    built = _build(ctx, photo)
    assert built["ok"] and any("best_so_far ranks above" in w for w in built["warnings"])


def test_best_so_far_skips_deleted_files_and_tier_zero(fakes, tmp_path):
    ctx = _Ctx()
    gone, ai = _file(tmp_path, "gone.jpg"), _file(tmp_path, "ai.png")
    fakes.answers = {"gone.jpg": _verdict(), "ai.png": _verdict("ai_art", "reject", 9)}
    _inspect(ctx, gone)
    _inspect(ctx, ai)
    gone.unlink()
    assert fpv._best_so_far(ctx) is None


def test_best_so_far_skips_a_clip_cut_from_ai_art(fakes, tmp_path):
    """build_cover refuses it, so offering it would also block the last rung."""
    ctx = _Ctx()
    source, clip = _file(tmp_path, "ai-source.mp4"), _file(tmp_path, "clip.mp4")
    fakes.answers = {"ai-source.mp4": _verdict("ai_art", "reject", 4, ["ai_illustration"]),
                     "clip.mp4": _verdict("real_photo", "use", 7)}
    _inspect(ctx, source, is_video=True)
    fpv._record_lineage(ctx, clip, source, 0.0)
    _inspect(ctx, clip, is_video=True)
    assert fpv._best_so_far(ctx) is None
    assert "ai_illustration" in _build(ctx, clip, is_video=True)["error"]

    _run(fpv.find_reference_photo(tool_context=ctx))
    background = _run(fpv.create_placeholder_background(tool_context=ctx))["path"]
    assert _build(ctx, background)["ok"]


# ---------------------------------------------------------------------------
# Reference photos and provenance
# ---------------------------------------------------------------------------


def test_two_inspections_are_kept_for_reference_photos(fakes, tmp_path):
    ctx = _Ctx()
    fakes.answer = _verdict("text_graphic", "reject", 2)
    for index in range(fpv._MAX_INSPECTIONS):
        _inspect(ctx, _file(tmp_path, f"still-{index}.png"))
    refused = _run(fpv.inspect_cover_media(str(_file(tmp_path, "extra.png")), False, tool_context=ctx))
    assert refused["ok"] is False and "budget" in refused["error"]

    urls = [f"https://upload.wikimedia.org/thumb/ref-{n}.jpg" for n in range(3)]
    fakes.refs = [_ref(url) for url in urls]
    fakes.downloads = {url: f"ref-{n}.jpg" for n, url in enumerate(urls)}
    assert _run(fpv.find_reference_photo(["Anthony Albanese"], tool_context=ctx))["found"]
    paths = [_run(fpv.download_image(url, tool_context=ctx))["path"] for url in urls]

    fakes.answer = _verdict("real_photo", "reject", 5)
    first = _inspect(ctx, paths[0])
    assert first["tier"] == 2  # a Wikimedia photo ranks with official visuals
    _inspect(ctx, paths[1])
    third = _run(fpv.inspect_cover_media(paths[2], False, tool_context=ctx))
    assert third["ok"] is False and "budget" in third["error"]


def test_a_reference_photos_credit_reaches_the_judge_and_the_cover_record(fakes, tmp_path):
    ctx = _Ctx()
    _news_state(ctx)
    url = "https://upload.wikimedia.org/thumb/Albanese_2022.jpg/1280px-Albanese_2022.jpg"
    fakes.refs = [_ref(url)]
    fakes.downloads = {url: "albanese.jpg"}
    found = _run(fpv.find_reference_photo(tool_context=ctx))
    assert found["found"] and found["subjects"] == ["Anthony Albanese", "Canberra"]
    path = _run(fpv.download_image(url, tool_context=ctx))["path"]

    _inspect(ctx, path)
    provenance = fakes.judge[-1]["provenance"]
    assert provenance.startswith("commons.wikimedia.org/wiki/File:Albanese_2022.jpg (wikimedia)")
    assert provenance.endswith(", Wikimedia Commons photo of Anthony Albanese")
    assert fakes.judge[-1]["official"] == ""

    # The agent passes the article's URL; the picture's own URL wins.
    built = _build(ctx, path, source_media_url="https://www.bbc.co.uk/news/articles/abc")
    assert built["ok"], built
    assert (built["source_media_url"], built["source_credit"], built["source_origin"]) == (
        url, CREDIT, "wikimedia")
    spec = _spec(ctx)
    assert (spec.source_media_url, spec.source_credit, spec.source_origin) == (url, CREDIT, "wikimedia")


def test_the_official_flag_is_computed_from_the_pictures_own_pages(fakes, tmp_path, monkeypatch):
    ctx = _Ctx()
    _news_state(ctx)
    seen = []
    monkeypatch.setattr(cover_vision, "official_source",
                        lambda urls, subjects, publishers=(): seen.append((urls, subjects, publishers)) or True)
    art = "https://mistral.ai/img/medium-3-key-art.png"
    fakes.clip = _clip_result(art, [art])
    fakes.downloads = {art: "key-art.png"}
    _run(fpv.find_source_clip(tool_context=ctx))
    path = _run(fpv.download_image(art, tool_context=ctx))["path"]
    fakes.answer = _verdict("official_visual", "use", 8)

    inspected = _inspect(ctx, path)
    assert inspected["official"] is True
    assert fakes.judge[-1] == {"provenance": "news.example/story (source_page)", "official": "exact"}
    urls, subjects, publishers = seen[-1]
    assert urls == [art, "https://news.example/story"]
    # The story's subject NAMES, never every capitalised word of its pages.
    assert subjects == ["Anthony Albanese", "Canberra"]
    assert list(publishers) == ["https://www.bbc.co.uk/news/articles/abc", "BBC News"]
    stored = ctx.state[fpv._K_VERDICTS][fpv._media_key(path)]
    assert (stored["kind"], stored["tier"], stored["official"], stored["origin"], stored["url"]) == (
        "official_visual", 2, "exact", "source_page", art)


def test_a_clip_finds_its_provenance_through_the_file_it_was_cut_from(tmp_path):
    ctx = _Ctx()
    source, clip = str(tmp_path / "src-a.mp4"), str(tmp_path / "retrim-a.mp4")
    fpv._record_candidates(ctx, [{"url": "https://v.example/a", "origin": "media_urls"}])
    fpv._record_download(ctx, "https://v.example/a", [source])
    fpv._record_lineage(ctx, clip, source, 2.0)
    assert fpv._provenance(ctx, clip)["origin"] == "media_urls"
    assert fpv._provenance(ctx, str(tmp_path / "other.mp4")) == {}


def test_find_reference_photo_is_capped_and_always_marks_the_rung_tried(fakes, monkeypatch):
    ctx = _Ctx()
    default = fpv.find_reference_photo.__defaults__
    first = _run(fpv.find_reference_photo(
        [" Anthony  Albanese ", "anthony albanese", "Canberra", "Australia", "Medicare", "Health"],
        tool_context=ctx))
    assert first["subjects"] == ["Anthony Albanese", "Canberra", "Australia", "Medicare"]
    assert "broader subjects" in first["note"]
    _run(fpv.find_reference_photo(tool_context=ctx))
    _run(fpv.find_reference_photo(tool_context=ctx))
    over = _run(fpv.find_reference_photo(["Sydney"], tool_context=ctx))
    assert over["found"] is False and "budget" in over["note"]
    assert len(fakes.lookups) == 3
    assert fpv.find_reference_photo.__defaults__ == default == ([],)

    def boom(*_a, **_k):
        raise RuntimeError("wikimedia down")

    monkeypatch.setattr(reference_photos, "reference_candidates", boom)
    fresh = _Ctx()
    failed = _run(fpv.find_reference_photo(["Canberra"], tool_context=fresh))
    assert failed["found"] is False and fresh.state.get("temp:cover_reference_tried") is True


# ---------------------------------------------------------------------------
# find_source_clip: budget, cache, failed URLs, automatic reference photos
# ---------------------------------------------------------------------------


def test_the_fourth_search_returns_the_last_result_and_a_repeated_query_is_free(fakes, monkeypatch):
    ctx = _Ctx()
    _news_state(ctx)

    def search(news, query="", sources=None, media=None, **kwargs):
        fakes.searches.append(query)
        url = f"https://img.example/{len(fakes.searches)}.jpg"
        return _clip_result(url, [url])

    monkeypatch.setattr(media_tools, "find_source_clip", search)
    first = _run(fpv.find_source_clip("Albanese hospitals", tool_context=ctx))
    assert first["calls_left"] == 2
    again = _run(fpv.find_source_clip("  albanese   HOSPITALS ", tool_context=ctx))
    assert again["url"] == first["url"] and again["calls_left"] == 2
    assert fakes.searches == ["Albanese hospitals"]

    _run(fpv.find_source_clip("Albanese Medicare clinic", tool_context=ctx))
    third = _run(fpv.find_source_clip("Canberra parliament photo", tool_context=ctx))
    assert third["calls_left"] == 0
    fourth = _run(fpv.find_source_clip("Australia hospital ward", tool_context=ctx))
    assert len(fakes.searches) == 3
    assert fourth["url"] == third["url"] and fourth["calls_left"] == 0
    assert fourth["note"] == (
        "search budget used (3 calls this step): call find_reference_photo, or build from best_so_far"
    )


def test_found_false_fetches_reference_photos_once(fakes):
    ctx = _Ctx()
    _news_state(ctx)
    fakes.clip = _clip_result(found=False, note="nothing found; call find_reference_photo")
    urls = ["https://upload.wikimedia.org/a.jpg", "https://upload.wikimedia.org/b.jpg"]
    fakes.refs = [_ref(url) for url in urls]

    result = _run(fpv.find_source_clip(tool_context=ctx))
    assert result["found"] is True and result["is_video"] is False
    assert result["url"] == result["image_url"] == urls[0]
    assert result["origin"] == result["image_origin"] == "wikimedia"
    assert [c["url"] for c in result["image_candidates"]] == urls
    assert result["note"].startswith("no story media found; free-licensed reference photos of "
                                     "Anthony Albanese, Canberra from Wikimedia Commons")
    assert set(result) >= {"found", "url", "image_candidates", "note", "calls_left"}
    assert ctx.state["temp:cover_reference_urls"] == [fpv._url_key(url) for url in urls]
    assert fpv._known_candidate(ctx, urls[0])["credit"] == CREDIT
    # Finding them for the agent is not the agent trying the rung itself.
    assert not ctx.state.get("temp:cover_reference_tried")

    later = _run(fpv.find_source_clip("a sharper query", tool_context=ctx))
    assert later["found"] is False and len(fakes.lookups) == 1


def test_a_result_without_a_found_key_does_not_fetch_reference_photos(fakes):
    ctx = _Ctx()
    _news_state(ctx)
    fakes.clip = {"video_url": ""}
    fakes.refs = [_ref("https://upload.wikimedia.org/a.jpg")]
    _run(fpv.find_source_clip(tool_context=ctx))
    assert fakes.lookups == []


def test_urls_that_failed_to_download_are_not_offered_again(fakes):
    ctx = _Ctx()
    _news_state(ctx)
    small, large = "https://ichef.example/news/240/a.jpg", "https://img.example/b.jpg"
    fakes.clip = _clip_result(small, [small, large])
    fakes.downloads = {large: "b.jpg"}
    _run(fpv.find_source_clip(tool_context=ctx))
    assert _run(fpv.download_image(small, tool_context=ctx))["ok"] is False

    result = _run(fpv.find_source_clip("sharper", tool_context=ctx))
    assert [c["url"] for c in result["image_candidates"]] == [large]
    assert result["url"] == result["image_url"] == large
    # A cached result is filtered too, once more URLs have failed.
    fakes.downloads = {}
    assert _run(fpv.download_image(large, tool_context=ctx))["ok"] is False
    cached = _run(fpv.find_source_clip("sharper", tool_context=ctx))
    assert cached["image_candidates"] == [] and cached["url"] == "" and cached["found"] is False


def test_the_time_window_reaches_the_search_only_when_it_has_dates(fakes):
    ctx = _Ctx()
    _news_state(ctx)
    window = {"requested_on": "2026-09-26", "phrase": "this week", "start": "2026-09-21",
              "end": "2026-09-26", "strict": True, "oldest": ""}
    ctx.state[K_TIME_WINDOW] = window
    _run(fpv.find_source_clip(tool_context=ctx))
    assert fakes.searches[-1]["time_window"] == window

    for empty in (None, {"requested_on": "2026-09-26", "phrase": "", "start": "", "end": "",
                         "strict": False, "oldest": ""}):
        other = _Ctx()
        _news_state(other)
        other.state[K_TIME_WINDOW] = empty
        _run(fpv.find_source_clip(tool_context=other))
        assert "time_window" not in fakes.searches[-1]


# ---------------------------------------------------------------------------
# Robustness and wiring
# ---------------------------------------------------------------------------


def test_an_em_dash_in_the_image_url_builds_without_raising(fakes):
    """Sweep N1: two runs crashed on an em dash inside an image URL."""
    ctx = _Ctx()
    _news_state(ctx)
    url = "https://cdn.example/launch—photo.jpg"
    fakes.clip = _clip_result(url, [url])
    fakes.downloads = {url: "launch.jpg"}
    _run(fpv.find_source_clip(tool_context=ctx))
    path = _run(fpv.download_image(url, tool_context=ctx))["path"]
    _inspect(ctx, path)

    built = _build(ctx, path)
    assert built["ok"], built
    assert _spec(ctx).source_media_url == url


def test_a_cover_record_that_cannot_be_validated_is_an_error_not_a_crash(fakes, tmp_path, monkeypatch):
    class _Rejecting:
        def __init__(self, **_fields):
            raise ValueError("CoverSpec.title contains a forbidden em dash")

    monkeypatch.setattr(fpv, "CoverSpec", _Rejecting)
    ctx = _Ctx()
    result = _build(ctx, _file(tmp_path, "photo.jpg"))
    assert result["ok"] is False and "forbidden em dash" in result["error"]
    assert fakes.compose == [] and ctx.saved == []


def test_an_unpublishable_credit_is_dropped_rather_than_the_cover(fakes, tmp_path):
    ctx = _Ctx()
    url = "https://upload.wikimedia.org/c.jpg"
    fakes.refs = [dict(_ref(url), credit="placeholder / Wikimedia Commons, CC BY 4.0")]
    fakes.downloads = {url: "c.jpg"}
    _run(fpv.find_reference_photo(tool_context=ctx))
    path = _run(fpv.download_image(url, tool_context=ctx))["path"]

    built = _build(ctx, path)
    assert built["ok"], built
    assert built["source_credit"] == "" and _spec(ctx).source_credit == ""
    assert any("credit was dropped" in w for w in built["warnings"])


def test_reset_step_state_clears_the_per_step_keys():
    ctx = SimpleNamespace(state=State({}, {}))
    assert set(fpv._STEP_STATE) == set(STEP_KEYS)
    for key in STEP_KEYS:
        ctx.state[key] = 3 if key.endswith(("calls", "inspected")) else ["x"]
    ctx.state[fpv._K_INSPECTIONS] = 4  # the vision budget stays per invocation

    assert fpv._reset_step_state(ctx) is None
    assert all(not ctx.state.get(key) for key in STEP_KEYS)
    assert ctx.state[fpv._K_INSPECTIONS] == 4

    untouched = SimpleNamespace(state=State({}, {}))
    fpv._reset_step_state(untouched)
    assert not untouched.state.has_delta()  # a first step adds no event


def test_the_agent_offers_the_reference_rung_and_resets_its_budgets():
    agent = fpv.build_first_page_visual_agent()
    names = [tool.name for tool in agent.tools]
    assert names.index("find_reference_photo") < names.index("create_placeholder_background")
    assert agent.before_agent_callback is fpv._reset_step_state


# ---------------------------------------------------------------------------
# Always-real-image critique: rank, reviewer, provenance, picture record
# ---------------------------------------------------------------------------

_REAL_OFFICIAL_SOURCE = cover_vision.official_source


def test_a_low_scoring_rejected_photo_no_longer_outranks_a_decent_chart(fakes, tmp_path):
    """Rank is (approved, score of 3 or more, tier, score): tier alone let a
    rejected photo scored 1 beat a chart scored 5."""
    ctx = _Ctx()
    photo, chart = _file(tmp_path, "photo.jpg"), _file(tmp_path, "chart.png")
    fakes.answers = {
        "photo.jpg": _verdict("real_photo", "reject", 1, ["publisher_branding"]),
        "chart.png": _verdict("text_graphic", "reject", 5, ["document_or_slide"]),
    }
    _inspect(ctx, photo)
    best = _inspect(ctx, chart)["best_so_far"]
    assert best["path"] == str(chart)


def test_a_rejected_presenter_labelled_real_photo_is_never_built(fakes, tmp_path):
    """The judge's kind is not trusted over its problems for a rejection."""
    ctx = _Ctx()
    anchor = _file(tmp_path, "anchor.jpg")
    fakes.answer = cover_vision._parse_verdict(
        '{"best_frame": 1, "score": 6, "verdict": "reject", "kind": "real_photo", '
        '"problems": ["talking_head"], "reason": "a presenter"}', 1)
    assert _inspect(ctx, anchor)["tier"] == 0
    assert _build(ctx, anchor)["ok"] is False
    assert fpv._best_so_far(ctx) is None


def test_the_cover_record_says_what_the_picture_check_said(fakes, tmp_path):
    ctx = _Ctx()
    photo, unseen = _file(tmp_path, "photo.jpg"), _file(tmp_path, "unseen.jpg")
    fakes.answer = _verdict("real_photo", "reject", 6, ["publisher_branding"])
    _inspect(ctx, photo)
    built = _build(ctx, photo)
    assert built["picture_verdict"] == "reject"
    assert any("did not approve" in w for w in built["warnings"])
    spec = _spec(ctx)
    assert (spec.picture_verdict, spec.picture_kind, spec.picture_tier, spec.picture_score) == (
        "reject", "real_photo", 3, 6)

    assert _build(ctx, unseen)["picture_verdict"] == "unchecked"
    assert _spec(ctx).picture_verdict == "unchecked"

    _run(fpv.find_reference_photo(tool_context=ctx))
    background = _run(fpv.create_placeholder_background(tool_context=ctx))["path"]
    Path(photo).unlink()
    Path(unseen).unlink()
    assert _build(ctx, background)["ok"]
    assert _spec(ctx).picture_verdict == ""


def test_the_reviewers_own_image_is_never_refused(fakes, tmp_path):
    """A reviewer's image the judge rejected as a chart was refused: a dead end."""
    ctx = _Ctx()
    url = "https://images.example.com/their-pick.png"
    ctx.state[fpv.K_REWORK_FEEDBACK] = f"Use this picture instead: {url}."
    fakes.downloads = {url: "their-pick.png"}
    path = _run(fpv.download_image(url, tool_context=ctx))["path"]
    fakes.answer = _verdict("text_graphic", "reject", 1, ["text_heavy"])
    _inspect(ctx, path)

    built = _build(ctx, path)
    assert built["ok"], built
    spec = _spec(ctx)
    assert spec.picture_from_reviewer is True
    assert not any("did not approve" in w for w in built["warnings"])


def test_a_picture_the_reviewer_turned_down_leaves_best_so_far(fakes, tmp_path):
    ctx = _Ctx()
    photo, other = _file(tmp_path, "photo.jpg"), _file(tmp_path, "other.jpg")
    fakes.answers = {"photo.jpg": _verdict("real_photo", "use", 8),
                     "other.jpg": _verdict("real_photo", "reject", 4, ["publisher_branding"])}
    _inspect(ctx, photo)
    _inspect(ctx, other)
    assert _build(ctx, photo)["ok"]

    # A title-only rejection keeps the picture.
    ctx.state[fpv.K_VERDICT] = {"status": "rejected", "feedback": "The title is too long."}
    fpv._reset_step_state(ctx)
    assert fpv._best_so_far(ctx)["path"] == str(photo)

    ctx.state[fpv.K_VERDICT] = {"status": "rejected", "feedback": "Wrong photo, it shows a queue."}
    fpv._reset_step_state(ctx)
    assert fpv._best_so_far(ctx)["path"] == str(other)
    rebuilt = _build(ctx, photo)
    assert rebuilt["ok"] and any("reviewer rejected" in w for w in rebuilt["warnings"])


def test_the_plain_background_waits_for_a_downloaded_reference_photo(fakes, tmp_path):
    """A downloaded but uninspected reference photo left the placeholder buildable."""
    ctx = _Ctx()
    url = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Hq.jpg"
    fakes.refs = [_ref(url)]
    fakes.downloads = {url: "hq.jpg"}
    _run(fpv.find_reference_photo(tool_context=ctx))
    path = _run(fpv.download_image(url, tool_context=ctx))["path"]

    background = _run(fpv.create_placeholder_background(tool_context=ctx))["path"]
    refused = _build(ctx, background)
    assert refused["ok"] is False and path in refused["error"]


def test_the_credit_survives_the_agent_retyping_the_url(fakes, tmp_path):
    ctx = _Ctx()
    listed = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Xiaomi_HQ_%28Beijing%29.jpg"
    typed = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Xiaomi_HQ_(Beijing).jpg?utm_source=x"
    fakes.refs = [_ref(listed)]
    fakes.downloads = {typed: "hq.jpg"}
    _run(fpv.find_reference_photo(tool_context=ctx))
    path = _run(fpv.download_image(typed, tool_context=ctx))["path"]
    built = _build(ctx, path)
    assert (built["source_credit"], built["source_origin"]) == (CREDIT, "wikimedia")


def test_a_wikimedia_file_found_on_a_page_is_credited_or_refused(fakes, tmp_path, monkeypatch):
    """A research page's upload.wikimedia.org picture reached the cover with no credit."""
    ctx = _Ctx()
    free = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Xiaomi_HQ.jpg"
    logo = "https://upload.wikimedia.org/wikipedia/en/4/4d/Xiaomi_logo.png"
    fakes.clip = _clip_result(free, [free, logo])
    fakes.downloads = {free: "hq.jpg", logo: "logo.png"}
    answers = {
        free: {"reusable": True, "credit": CREDIT, "licence": "CC BY 4.0",
               "context_url": "https://commons.wikimedia.org/wiki/File:Xiaomi_HQ.jpg",
               "title": "File:Xiaomi_HQ.jpg"},
        logo: {"reusable": False, "credit": "", "licence": "Fair use", "context_url": "", "title": ""},
    }
    monkeypatch.setattr(reference_photos, "file_credit", lambda url, **_k: answers[url])
    _news_state(ctx)
    _run(fpv.find_source_clip(tool_context=ctx))

    refused = _run(fpv.download_image(logo, tool_context=ctx))
    assert refused["ok"] is False and "not free to reuse" in refused["error"]
    path = _run(fpv.download_image(free, tool_context=ctx))["path"]
    built = _build(ctx, path)
    assert (built["source_credit"], built["source_origin"]) == (CREDIT, "wikimedia")


def test_a_site_that_only_resembles_the_company_is_checked_on_wikidata_once(fakes, tmp_path, monkeypatch):
    """xiaomimimo.com is fuzzy; Xiaomi's P856 (mi.com) makes mimo.mi.com exact."""
    ctx = _Ctx()
    _news_state(ctx)
    monkeypatch.setattr(cover_vision, "official_source", _REAL_OFFICIAL_SOURCE)
    lookups = []
    monkeypatch.setattr(reference_photos, "official_sites",
                        lambda subjects, **_k: lookups.append(list(subjects)) or ["https://www.mi.com/"])
    monkeypatch.setattr(reference_photos, "story_subjects", lambda *_a, **_k: ["Xiaomi", "MiMo"])
    level = _run(fpv._official_level(ctx, ["https://aistudio-cdn.xiaomimimo.com/v.mov"], None))
    assert level == cover_vision.OFFICIAL_FUZZY
    assert _run(fpv._official_level(ctx, ["https://mimo.mi.com/chart.png"], None)) == cover_vision.OFFICIAL_EXACT
    assert lookups == [["Xiaomi", "MiMo"]]


def test_share_alike_photos_left_out_reach_the_cover_record(fakes, tmp_path, monkeypatch):
    ctx = _Ctx()

    def lookup(subjects, report=None, **_kwargs):
        if report is not None:
            report["share_alike_skipped"] = 2
        return []

    monkeypatch.setattr(reference_photos, "reference_candidates", lookup)
    _run(fpv.find_reference_photo(tool_context=ctx))
    background = _run(fpv.create_placeholder_background(tool_context=ctx))["path"]
    assert _build(ctx, background)["ok"]
    assert _spec(ctx).share_alike_skipped == 2


# ---------------------------------------------------------------------------
# A tier-0 verdict covers the whole download
# ---------------------------------------------------------------------------


def _trimmed(ctx, tmp_path, monkeypatch, url="https://news.example/ai-explainer.mp4"):
    """download_and_trim as the agent calls it: a clip plus the untrimmed source it kept."""
    clip, src = tmp_path / "clip-abc.mp4", tmp_path / "src-abc.mp4"

    def trim(_url, _max_s, _min_s, _workdir):
        clip.write_bytes(b"mp4")
        src.write_bytes(b"mp4")
        return str(clip)

    monkeypatch.setattr(media_tools, "download_and_trim", trim)
    got = _run(fpv.download_and_trim(url, tool_context=ctx))
    assert got["ok"] and got["source_path"] == str(src), got
    return clip, src


def test_ai_art_found_in_a_clip_rules_out_the_video_it_was_cut_from(fakes, tmp_path, monkeypatch):
    """The agent inspected clip_path; the source it was cut from stayed buildable,
    the plain-background refusal pointed at it, and the salvage built from it."""
    ctx = _Ctx()
    clip, src = _trimmed(ctx, tmp_path, monkeypatch)
    fakes.answer = _verdict("ai_art", "reject", 2, ["ai_illustration"])
    _inspect(ctx, clip, is_video=True)

    for media in (clip, src):
        refused = _build(ctx, media, is_video=True)
        assert refused["ok"] is False and "ai_illustration" in refused["error"], media
    assert fpv._downloaded_pictures(ctx) == [] and fpv._best_so_far(ctx) is None
    again = _run(fpv.download_and_trim("https://news.example/ai-explainer.mp4", tool_context=ctx))
    assert again["ok"] is False and "ai_illustration" in again["error"]

    ctx.state[K_PLAN] = CarouselPlan(style="points", slide_count=3, hook_title=TITLE).model_dump(mode="json")
    salvaged = _run(fpv.ensure_cover(ctx, budget_s=200))
    assert salvaged["ok"] and salvaged["salvaged_from"] == "drawn background", salvaged
    assert fakes.compose and all("abc" not in Path(media).name for media in fakes.compose)


def test_a_picture_judged_ai_art_is_not_downloaded_offered_or_built_again(fakes, tmp_path):
    """A second download of the same URL made a new file with no verdict."""
    ctx = _Ctx()
    _news_state(ctx)
    url = "https://technews.example/wp-content/uploads/2026/09/header.jpg"
    other = "https://img.example/stage.jpg"
    fakes.clip = _clip_result(url, [url, other])
    fakes.downloads = {url: "img-111.jpg", other: "stage.jpg"}
    _run(fpv.find_source_clip(tool_context=ctx))
    first = _run(fpv.download_image(url, tool_context=ctx))["path"]
    fakes.answer = _verdict("ai_art", "reject", 2, ["ai_illustration"])
    _inspect(ctx, first)

    again = _run(fpv.download_image(url + "?utm_source=x", tool_context=ctx))
    assert again["ok"] is False and "ai_illustration" in again["error"]
    copy = _file(tmp_path, "img-222.jpg")  # the same URL, fetched some other way
    fpv._record_download(ctx, url, [str(copy)])
    assert "ai_illustration" in _build(ctx, copy)["error"]

    result = _run(fpv.find_source_clip("a sharper query", tool_context=ctx))
    assert [c["url"] for c in result["image_candidates"]] == [other]
    assert result["url"] == result["image_url"] == other


def test_an_unusable_moment_leaves_the_rest_of_the_video(fakes, tmp_path, monkeypatch):
    """A presenter at 12 s says nothing about the other footage; download_and_trim's
    own cut is the same footage as its source."""
    ctx = _Ctx()
    clip, src = _trimmed(ctx, tmp_path, monkeypatch)
    moment = _file(tmp_path, "retrim-a.mp4")
    fpv._record_lineage(ctx, moment, src, 12.0)
    fakes.answers = {"retrim-a.mp4": _verdict("unusable", "reject", 2, ["talking_head"])}
    _inspect(ctx, moment, is_video=True)
    assert "judged unusable" in _build(ctx, moment, is_video=True)["error"]
    assert fpv._cover_refusal(ctx, str(src)) is None
    assert fpv._judged_url(ctx, "https://news.example/ai-explainer.mp4") is None

    fakes.answers = {"clip-abc.mp4": _verdict("unusable", "reject", 2, ["talking_head"])}
    _inspect(ctx, clip, is_video=True)
    assert "judged unusable (talking_head)" in fpv._cover_refusal(ctx, str(src))


def test_a_videos_page_reaches_the_official_check(fakes, tmp_path, monkeypatch):
    """Xiaomi's demo video was judged on its CDN URL alone: 'unverified', so AI art."""
    ctx = _Ctx()
    _news_state(ctx)
    video = "https://aistudio-cdn.xiaomimimo.com/xiaomimimo-static/model-v2.6-pro-video1.mov"
    page = "https://mimo.mi.com/models/en-US/mimo-v2.6-pro"

    def search(news, query="", sources=None, media=None, provenance=None, **_kwargs):
        provenance[video] = {"context_url": page, "original": ""}
        return dict(_clip_result(), url=video, is_video=True, duration_s=20.0, origin="research_page")

    monkeypatch.setattr(media_tools, "find_source_clip", search)
    monkeypatch.setattr(cover_vision, "official_source", _REAL_OFFICIAL_SOURCE)
    monkeypatch.setattr(reference_photos, "story_subjects", lambda *_a, **_k: ["Xiaomi", "MiMo"])
    monkeypatch.setattr(reference_photos, "official_sites", lambda *_a, **_k: ["https://www.mi.com/"])
    _run(fpv.find_source_clip(tool_context=ctx))
    assert fpv._known_candidate(ctx, video)["context_url"] == page
    _clip, src = _trimmed(ctx, tmp_path, monkeypatch, url=video)
    fakes.answer = _verdict("official_visual", "use", 7)
    inspected = _inspect(ctx, src, is_video=True)
    assert fakes.judge[-1]["official"] == cover_vision.OFFICIAL_EXACT
    assert inspected["official"] is True and inspected["tier"] == 2


def test_download_image_falls_back_to_the_url_as_found(fakes, tmp_path, monkeypatch):
    """The rewritten width may be one the CDN refuses; the original was big enough."""
    ctx = _Ctx()
    _news_state(ctx)
    original = "https://cdn.example.com/photos/launch-stage.jpg?w=900&h=700"
    offered = "https://cdn.example.com/photos/launch-stage.jpg?w=1600&h=1244"

    def search(news, query="", sources=None, media=None, provenance=None, **_kwargs):
        provenance[offered] = {"context_url": "https://news.example/story", "original": original}
        return _clip_result(offered, [offered])

    fetched = []

    def download(url, workdir=""):
        fetched.append(url)
        return str(_file(tmp_path, "stage.jpg"))

    monkeypatch.setattr(media_tools, "find_source_clip", search)
    monkeypatch.setattr(media_tools, "download_image", download)
    _run(fpv.find_source_clip(tool_context=ctx))
    got = _run(fpv.download_image(offered, tool_context=ctx))
    assert got["ok"] and fetched == [original]
    # The provenance is still the URL the agent was offered.
    assert fpv._provenance(ctx, got["path"])["url"] == offered


def test_the_official_check_reads_more_names_and_looks_up_every_miss(fakes, monkeypatch):
    """OpenAI came fifth in run-3c4107e390df, behind 'Mistral Small' and 'Mistral',
    and a company whose sites resemble no name (Xiaomi: appmifile.com, mi.com)
    was never looked up."""
    ctx = _Ctx()
    _news_state(ctx)
    monkeypatch.setattr(cover_vision, "official_source", _REAL_OFFICIAL_SOURCE)
    names = ["Mistral Small", "Mistral", "GPT", "Voxtral TTS", "OpenAI", "Hugging Face"]
    monkeypatch.setattr(reference_photos, "story_subjects", lambda *_a, limit=4, **_k: names[:limit])
    assert fpv._official_subjects(ctx) == ["Mistral", "GPT", "Voxtral TTS", "OpenAI", "Hugging Face"]

    lookups = []

    def sites(subjects, **kwargs):
        lookups.append((list(subjects), kwargs.get("limit")))
        return ["https://openai.com/", "https://www.mi.com/"]

    monkeypatch.setattr(reference_photos, "official_sites", sites)
    hero = ["https://images.ctfassets.net/kftzwdyauwt9/x/hero.png",
            "https://openai.com/index/introducing-gpt-5-4-mini-and-nano/"]
    assert _run(fpv._official_level(ctx, hero, None)) == cover_vision.OFFICIAL_EXACT
    renders = ["https://i02.appmifile.com/x/render.png", "https://www.mi.com/global/product/xiaomi-17/"]
    assert _run(fpv._official_level(ctx, renders, None)) == cover_vision.OFFICIAL_EXACT
    assert lookups == [(["Mistral", "GPT", "Voxtral TTS", "OpenAI", "Hugging Face"], 8)]


def test_praise_for_the_picture_keeps_it(fakes, tmp_path):
    """Any picture word used to mark the picture rejected, even 'Love the photo'."""
    ctx = _Ctx()
    photo = _file(tmp_path, "photo.jpg")
    fakes.answer = _verdict("real_photo", "use", 8)
    _inspect(ctx, photo)
    assert _build(ctx, photo)["ok"]
    for feedback in (
        "Love the photo, keep it. Just shorten the headline to 5 words.",
        "Love the photo! Just make the hook shorter.",
        "The image is fine but the title is wrong.",
        "The photo is cropped wrong, show both people.",
        "Wrong moment in the video, use the handshake.",
    ):
        ctx.state[fpv.K_VERDICT] = {"status": "rejected", "feedback": feedback}
        fpv._reset_step_state(ctx)
        assert not ctx.state.get(fpv._K_REVIEWER_REJECTED), feedback
        assert fpv._best_so_far(ctx)["path"] == str(photo)
    for feedback in ("The image is not good.", "Use a different photo.", "Bad image, the text is great.",
                     "I don't like the picture", "Use this one: https://img.example/x.jpg"):
        assert fpv._rejects_picture(feedback), feedback
