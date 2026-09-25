"""Cover sourcing: picture identity, research-page media and the video hold-back.

Every network call is faked (page scrapes, trend search, yt-dlp probes and
the video web search), so these tests run offline.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any, Optional
from unittest.mock import patch

from app.config import settings
from app.tools import media_tools

# find_source_clip's result keys (shared contract C1 with the cover agent).
CONTRACT_KEYS = {
    "found", "url", "is_video", "duration_s", "origin", "image_url", "image_origin",
    "image_candidates", "image_first", "video_url", "video_duration_s", "video_origin",
    "trend_search", "note",
}

PAGE = "https://news.example.com/2026/widget-agent-launch"
PAGE_PHOTO = "https://cdn.news.example.com/widget-agent-launch-photo.jpg"
COVERAGE = "https://other.example.org/tech/widget-agent-launch-coverage"
COVERAGE_PHOTO = "https://cdn.other.example.org/widget-agent-launch-stage.jpg"
EXPLAINER = {
    "url": "https://www.youtube.com/watch?v=explainer01",
    "duration": 48.0,
    "title": "Widget Agent launch explained",
}


def _run(
    news: dict,
    research: Optional[list[str]] = None,
    pages: Optional[dict[str, list[tuple[str, str, int]]]] = None,
    trend: tuple[str, ...] = (),
    probes: Optional[dict[str, dict[str, Any]]] = None,
    video: Optional[dict[str, Any]] = None,
    fetched: Optional[list[str]] = None,
    limit: Optional[int] = None,
    research_media: Optional[list[str]] = None,
) -> dict:
    """find_source_clip with every network call replaced by the given fakes."""
    pages = pages or {}
    probes = probes or {}

    def scrape(url: str) -> list[tuple[str, str, int]]:
        if fetched is not None:
            fetched.append(url)
        return list(pages.get(url, []))

    with (
        patch.object(media_tools, "_scrape_page_media", side_effect=scrape),
        patch.object(
            media_tools, "_search_trending_pages",
            return_value=(list(trend), f"live trend search checked {len(trend)} page(s)"),
        ),
        patch.object(media_tools, "_probe_with_ytdlp", side_effect=lambda url: probes.get(url)),
        patch.object(media_tools, "_search_video_online", return_value=video),
        patch.object(
            media_tools, "_IMAGE_CANDIDATE_LIMIT",
            limit or media_tools._IMAGE_CANDIDATE_LIMIT,
        ),
    ):
        return media_tools.find_source_clip(news, "", research, research_media)


def _urls(result: dict) -> list[str]:
    return [candidate["url"] for candidate in result["image_candidates"]]


class ImageIdentityTests(unittest.TestCase):
    def test_size_variants_of_one_picture_merge(self) -> None:
        groups = [
            # The docstring's variants, plus WordPress "-scaled" and retina.
            [
                "https://cdn.example.com/2026/09/photo-1200-80.jpg",
                "https://cdn.example.com/2026/09/photo-1920-80.jpg.webp",
                "https://cdn.example.com/2026/09/photo_1280X720.webp",
                "https://cdn.example.com/2026/09/photo.jpg?w=640",
                "https://cdn.example.com/2026/09/photo-1024x576.jpg",
                "https://cdn.example.com/2026/09/photo-scaled.jpg",
                "https://cdn.example.com/2026/09/photo@2x.jpg",
            ],
            # A descriptive name identifies the picture in any folder.
            [
                "https://cdn.example.com/a/openai-launch-1200-80.jpg",
                "https://cdn.example.com/b/openai-launch-800w.jpg",
                "https://cdn.example.com/openai-launch.jpg?w=640",
            ],
            # Real run data: Future, Contentful, TechCrunch, www/no-www.
            [
                "https://cdn.mos.cms.futurecdn.net/SSrgDUXsJwUVxtvheg4YCM.jpg",
                "https://cdn.mos.cms.futurecdn.net/SSrgDUXsJwUVxtvheg4YCM-2560-80.jpg",
                "https://cdn.mos.cms.futurecdn.net/SSrgDUXsJwUVxtvheg4YCM-1920-80.jpg.webp",
            ],
            [
                "https://images.ctfassets.net/kftzwdyauwt9/33edbc89/c998eedd/bug-bounty-program.png?w=640&q=90&fm=webp",
                "https://images.ctfassets.net/kftzwdyauwt9/33edbc89/c998eedd/bug-bounty-program.png?w=2048&q=90&fm=webp",
            ],
            [
                "https://techcrunch.com/wp-content/uploads/2025/04/GettyImages-1979406539.jpg",
                "https://techcrunch.com/wp-content/uploads/2025/04/GettyImages-1979406539.jpg?resize=1200",
            ],
            [
                "https://www.hacktron.ai/_astro/DlrLdX-c_ZAKAfc.webp?dpl=a",
                "https://hacktron.ai/_astro/DlrLdX-c_ZAKAfc.webp?dpl=b",
            ],
            # Camera names merge with their own size variants only.
            [
                "https://site.com/2026/09/IMG_4567.jpg",
                "https://site.com/2026/09/IMG_4567-1024x683.jpg",
            ],
            [
                "https://i.guim.co.uk/img/media/aaa111/0_0_5000_3000/master/5000.jpg?width=1200",
                "https://i.guim.co.uk/img/media/aaa111/0_0_5000_3000/master/5000.jpg?width=445",
            ],
            # A picture served by a script is the picture its url= names.
            [
                "https://site.com/_next/image?url=%2Fimages%2Fhero-shot.png&w=1920&q=75",
                "https://site.com/images/hero-shot.png",
            ],
        ]
        for group in groups:
            with self.subTest(group=group[0]):
                self.assertEqual(len({media_tools._image_identity(u) for u in group}), 1)

    def test_different_pictures_keep_different_keys(self) -> None:
        substack = (
            "https://substackcdn.com/image/fetch/w_1456,c_limit,f_webp/"
            "https%3A%2F%2Fsubstack-post-media.s3.amazonaws.com%2Fpublic%2Fimages%2F{}_1456x816.png"
        )
        dims = (
            "https://dims.apnews.com/dims4/default/aaa/2147483647/strip/true/crop/"
            "3000x2000+0+0/resize/1440x960!/quality/90/?url=https%3A%2F%2Fassets.apnews.com%2F{}.jpg"
        )
        pairs = [
            (substack.format("1a2b3c4d-aaaa"), substack.format("9f8e7d6c-bbbb")),
            (
                "https://site.com/wp-content/uploads/2026/09/image.png",
                "https://site.com/wp-content/uploads/2026/03/image-1024x576.png",
            ),
            ("https://site.com/2026/09/IMG_0042.jpg", "https://site.com/2026/09/IMG_5731.jpg"),
            ("https://site.com/2026/09/IMG_4567.jpg", "https://site.com/2025/01/IMG_4567.jpg"),
            ("https://site.com/DSC_0123-1024x576.jpg", "https://site.com/DSC_0456.jpg"),
            (
                "https://site.com/u/openai-hackers-2026-09-18.jpg",
                "https://site.com/u/openai-hackers-2025-01-02.jpg",
            ),
            (
                "https://cdn.mos.cms.futurecdn.net/nvidia-rtx-5090.jpg",
                "https://cdn.mos.cms.futurecdn.net/nvidia-rtx-4090.jpg",
            ),
            (
                "https://site.com/u/Screenshot%202026-09-18%20at%2010.32.11.png",
                "https://site.com/u/Screenshot%202026-09-18%20at%2010.45.02.png",
            ),
            (
                "https://i.ytimg.com/vi/AAAAAAAAAAA/maxresdefault.jpg",
                "https://i.ytimg.com/vi/BBBBBBBBBBB/maxresdefault.jpg",
            ),
            (
                "https://i.guim.co.uk/img/media/aaa111/0_0_5000_3000/master/5000.jpg",
                "https://i.guim.co.uk/img/media/bbb222/0_0_5000_3000/master/5000.jpg",
            ),
            (
                "https://www.anthropic.com/api/opengraph-illustration?name=Hand%20Lock",
                "https://www.anthropic.com/api/opengraph-illustration?name=Brain",
            ),
            (dims.format("photo1"), dims.format("photo2")),
            ("https://site.com/keynote-001.jpg", "https://site.com/keynote-002.jpg"),
        ]
        for first, second in pairs:
            with self.subTest(first=first):
                self.assertNotEqual(
                    media_tools._image_identity(first), media_tools._image_identity(second)
                )

    def test_size_hint_reads_only_real_size_tokens(self) -> None:
        hints = {
            # Years, dates and camera counters are not widths.
            "https://indianexpress.com/wp-content/uploads/2026/09/ChatGPT-Image-Sep-19-2026.png": 0,
            "https://site.com/2026/09/IMG_2345.jpg": 0,
            "https://site.com/nvidia-rtx-5090.jpg": 0,
            "https://cdn.mos.cms.futurecdn.net/SSrgDUXsJwUVxtvheg4YCM-1920-80.jpg": 1920,
            "https://media.ptcnews.tv/2026/09/0f29c59e5c0c596d_1280X720.webp": 1280,
            "https://cdn.example.com/photo-800w.jpg": 800,
            "https://images.indianexpress.com/2026/09/photo.png?resize=600,400": 600,
            "https://images.ctfassets.net/x/y/z/card.png?w=1600&h=900&fit=fill": 1600,
        }
        for url, width in hints.items():
            with self.subTest(url=url):
                self.assertEqual(media_tools._image_size_hint(url), width)
        self.assertTrue(media_tools._is_thumbnail("https://x.com/p.png?resize=600,400"))
        self.assertTrue(media_tools._is_thumbnail("https://x.com/p-320-80.jpg"))
        self.assertFalse(media_tools._is_thumbnail("https://x.com/p-1024x576.jpg"))
        self.assertFalse(media_tools._is_thumbnail("https://x.com/IMG_0320.jpg"))

    def test_distinct_images_never_swaps_in_a_different_picture(self) -> None:
        def candidate(url: str, reason: str) -> media_tools._MediaCandidate:
            return media_tools._MediaCandidate(url, "image", 66, "source_page", PAGE, reason)

        lead = "https://site.com/2026/09/IMG_1234.jpg"
        other = "https://site.com/2026/09/IMG_2345.jpg"
        kept = media_tools._distinct_images([candidate(lead, "lead"), candidate(other, "second")])
        self.assertEqual([(c.url, c.reason) for c in kept], [(lead, "lead"), (other, "second")])

        original = "https://site.com/2026/09/stage-photo.jpg"
        variants = [
            candidate(original, "og"),
            candidate(original + "?w=320", "thumb"),
            candidate("https://site.com/2026/09/stage-photo-1920x1080.jpg", "big"),
        ]
        kept = media_tools._distinct_images(variants)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].url, "https://site.com/2026/09/stage-photo-1920x1080.jpg")
        self.assertEqual(kept[0].reason, "og")
        # An unsized original is never traded for a thumbnail of itself.
        kept = media_tools._distinct_images(variants[:2])
        self.assertEqual(kept[0].url, original)


class LeadMediaTests(unittest.TestCase):
    def test_chrome_and_thumbnails_do_not_use_up_lead_slots(self) -> None:
        og = "https://images.example.com/2026/09/story-hero.jpg"
        second = "https://images.example.com/2026/09/story-second.jpg"
        page = [
            (og, "image", 45),
            ("https://media.example.com/uploads/2023/06/small-facebook.png", "image", 48),
            ("https://media.example.com/uploads/2023/06/small-instagram.png", "image", 48),
            ("https://example.com/wp-content/themes/site/images/default-ie.jpg", "image", 48),
            ("https://data.example.com/election2019/track_1x1.jpg", "image", 48),
            ("https://example.com/authors/writer.jpg", "image", 48),
            ("https://cdn.mos.cms.futurecdn.net/flexiimages/a0ldfjnzhe1774272054.png", "image", 48),
            ("https://example.com/assets/site-logo.png", "image", 48),
            ("https://images.example.com/2026/09/ChatGPT-Image-Sep-19-2026.png", "image", 48),
            (second + "?resize=600,400", "image", 48),
            ("https://images.example.com/2026/09/tiny-promo.jpg?w=320", "image", 48),
            (second + "?w=1600", "image", 48),
            ("https://www.youtube.com/embed/abc123", "video", 80),
        ]
        with patch.object(media_tools, "_scrape_page_media", return_value=page):
            found = media_tools._scrape_lead_media(PAGE)

        self.assertEqual(
            [url for url, kind, _ in found if kind == "image"], [og, second + "?w=1600"]
        )
        self.assertIn(("https://www.youtube.com/embed/abc123", "video", 80), found)

    def test_lead_images_are_capped_after_filtering(self) -> None:
        page = [("https://example.com/assets/small-twitter.png", "image", 48)] + [
            (f"https://images.example.com/2026/09/story-photo-{i}.jpg", "image", 48)
            for i in range(1, 7)
        ]
        with patch.object(media_tools, "_scrape_page_media", return_value=page):
            found = media_tools._scrape_lead_media(PAGE)

        self.assertEqual(
            [url for url, _, _ in found],
            [f"https://images.example.com/2026/09/story-photo-{i}.jpg" for i in range(1, 5)],
        )

    def test_chrome_filter_reads_the_image_path_not_the_story(self) -> None:
        for url in [
            "https://cdn.example.com/google-pixel-10-pro.jpg",
            "https://cdn.example.com/youtube-ceo-neal-mohan.jpg",
            "https://cdn.example.com/facebook-hq-menlo-park.jpg",
            "https://cdn.example.com/silicon-valley-iconic-office.jpg",
            "https://images.indianexpress.com/2026/09/OPEN-AI.jpg",
            # Brightspot CDNs (AP, NPR, Politico) put /dimsN/default/ in every photo.
            "https://dims.apnews.com/dims4/default/b7b5c3e/2147483647/strip/true/crop/"
            "5760x3240+0+0/resize/1440x810!/quality/90/?url=https%3A%2F%2Fassets.apnews.com"
            "%2F4d%2F8b%2Fa1c2%2Fe2d4f0.jpg",
            "https://npr.brightspotcdn.com/dims3/default/strip/false/crop/4000x2667+0+0/"
            "resize/1100/quality/85/format/jpeg/?url=http%3A%2F%2Fnpr-brightspot.s3.amazonaws"
            ".com%2Fab%2Fcd%2F1234%2Fprotest-photo.jpg",
            # A story slug that names a logo or an icon is still a photo.
            "https://cdn.example.com/2026/09/meta-logo-lawsuit-zuckerberg.jpg",
            "https://cdn.example.com/apple-icon-event.jpg",
        ]:
            with self.subTest(url=url):
                self.assertFalse(media_tools._is_page_chrome(url))
        for url in [
            "https://example.com/assets/site-logo.png",
            "https://media.ptcnews.tv/wp-content/uploads/2025/assests/ptcnews-logo.jpg",
            "https://example.com/wp-content/themes/site/images/default-ie.jpg",
            "https://example.com/authors/writer.jpg",
            "https://example.com/static/apple-touch-icon.png",
            "https://example.com/img/default-avatar-2x.png",
            "https://example.com/_next/image?url=%2Fimages%2Flogo-dark.png&w=256",
        ]:
            with self.subTest(url=url):
                self.assertTrue(media_tools._is_page_chrome(url))

    def test_ai_generated_file_names_are_recognised(self) -> None:
        for url, expected in [
            ("https://x.com/2026/09/ChatGPT-Image-Sep-19-2026-02_38_14-AM.png", True),
            ("https://x.com/DALL%C2%B7E%202024-01-01%2010.00.00.png", True),
            ("https://x.com/dall-e-hacker-illustration.png", True),
            ("https://x.com/Gemini_Generated_Image_abc123.png", True),
            ("https://x.com/_next/image?url=%2Fuploads%2FChatGPT%20Image%20x.png&w=1080", True),
            ("https://x.com/the-dalles-dam.jpg", False),
            ("https://midjourney.com/news/david-holz-portrait.jpg", False),
        ]:
            with self.subTest(url=url):
                self.assertEqual(media_tools._looks_ai_generated(url), expected)


class PageKeyTests(unittest.TestCase):
    def test_page_key_ignores_how_the_link_was_written(self) -> None:
        base = "https://www.ptcnews.tv/trending/openai-hack-4429611"
        for variant in [
            base + "?utm_source=whatsapp&utm_medium=share",
            base + "/",
            base + "#comments",
            "http://ptcnews.tv/trending/openai-hack-4429611",
            "https://WWW.PTCNEWS.TV/trending/openai-hack-4429611?fbclid=abc",
        ]:
            with self.subTest(variant=variant):
                self.assertEqual(media_tools._page_key(variant), media_tools._page_key(base))
        self.assertNotEqual(
            media_tools._page_key(base + "?id=3"), media_tools._page_key(base + "?id=4")
        )


class ResultContractTests(unittest.TestCase):
    def test_every_branch_returns_the_contract_keys(self) -> None:
        news = {"title": "Widget Agent launch", "tags": ["widget", "agent"]}
        with_photo = {COVERAGE: [(COVERAGE_PHOTO, "image", 45)]}
        results = {
            "nothing": _run(news),
            "image fallback": _run(news, [COVERAGE], with_photo),
            "held-back video": _run(news, [COVERAGE], with_photo, video=EXPLAINER),
            "video pick": _run(news, video=EXPLAINER),
        }
        for label, result in results.items():
            with self.subTest(branch=label):
                self.assertEqual(set(result), CONTRACT_KEYS)
                self.assertIsInstance(result["video_duration_s"], float)
        self.assertFalse(results["nothing"]["found"])
        for label in ("nothing", "image fallback", "video pick"):
            self.assertEqual(
                (results[label]["video_url"], results[label]["video_duration_s"],
                 results[label]["video_origin"], results[label]["image_first"]),
                ("", 0.0, "", False),
            )


class VideoHoldBackTests(unittest.TestCase):
    news = {"title": "Widget Agent launch", "tags": ["widget", "agent"]}

    def test_web_search_video_is_held_back_behind_the_story_photo(self) -> None:
        result = _run(self.news, [COVERAGE], {COVERAGE: [(COVERAGE_PHOTO, "image", 45)]},
                      video=EXPLAINER)

        self.assertTrue(result["found"])
        self.assertEqual(result["url"], COVERAGE_PHOTO)
        self.assertEqual(result["url"], result["image_url"])
        self.assertFalse(result["is_video"])
        self.assertEqual(result["duration_s"], 0.0)
        self.assertEqual(result["origin"], "research_page")
        self.assertTrue(result["image_first"])
        self.assertEqual(result["video_url"], EXPLAINER["url"])
        self.assertEqual(result["video_duration_s"], 48.0)
        self.assertEqual(result["video_origin"], "web_search")

    def test_web_search_video_stays_the_pick_without_a_story_photo(self) -> None:
        trend_page = "https://coverage.example.net/widget-agent-launch"
        result = _run(
            self.news,
            pages={trend_page: [("https://cdn.example.net/widget-agent-launch.jpg", "image", 45)]},
            trend=(trend_page,),
            video=EXPLAINER,
        )

        self.assertEqual(result["image_origin"], "trend_search")
        self.assertEqual(result["url"], EXPLAINER["url"])
        self.assertTrue(result["is_video"])
        self.assertEqual(result["origin"], "web_search")
        self.assertFalse(result["image_first"])
        self.assertEqual(result["video_url"], "")

    def test_story_video_is_the_pick_even_with_a_photo(self) -> None:
        clip = "https://news.example.com/media/widget-agent-launch.mp4"
        news = dict(self.news, source_url=PAGE)
        result = _run(
            news,
            pages={PAGE: [(clip, "video", 90), (PAGE_PHOTO, "image", 45)]},
            probes={clip: {"duration": 21.0, "title": "Widget Agent launch"}},
            video=EXPLAINER,
        )

        self.assertEqual(result["url"], clip)
        self.assertTrue(result["is_video"])
        self.assertEqual(result["origin"], "source_page")
        self.assertFalse(result["image_first"])
        self.assertEqual(result["video_url"], "")

    def test_research_page_video_needs_a_topic_title(self) -> None:
        embed = "https://www.youtube.com/embed/UNRELATED01"
        pages = {COVERAGE: [(COVERAGE_PHOTO, "image", 45), (embed, "video", 80)]}

        off_topic = _run(
            self.news, [COVERAGE], pages,
            probes={embed: {"duration": 60.0, "title": "Top 10 headlines of the day | Newsroom"}},
        )
        self.assertEqual(off_topic["url"], COVERAGE_PHOTO)
        self.assertFalse(off_topic["is_video"])
        self.assertEqual(off_topic["video_url"], "")

        on_topic = _run(
            self.news, [COVERAGE], pages,
            probes={embed: {"duration": 60.0, "title": "Widget Agent launch keynote recap"}},
        )
        self.assertEqual(on_topic["url"], COVERAGE_PHOTO)
        self.assertTrue(on_topic["image_first"])
        self.assertEqual(on_topic["video_url"], embed)
        self.assertEqual(on_topic["video_origin"], "research_page")
        self.assertEqual(on_topic["video_duration_s"], 60.0)

    def test_video_link_in_research_sources_is_held_back(self) -> None:
        link = "https://www.youtube.com/watch?v=DelLsAFp7wA"
        result = _run(
            self.news, [COVERAGE, link], {COVERAGE: [(COVERAGE_PHOTO, "image", 45)]},
            probes={link: {"duration": 182.0, "title": "Widget Agent launch: how it works"}},
        )

        self.assertEqual(result["url"], COVERAGE_PHOTO)
        self.assertTrue(result["image_first"])
        self.assertEqual(result["video_url"], link)
        self.assertEqual(result["video_origin"], "research_page")

    def test_cited_page_that_is_also_a_trend_hit_keeps_the_title_check(self) -> None:
        player = "https://player.example.org/newsroom/latest.mp4"
        pages = {COVERAGE: [(COVERAGE_PHOTO, "image", 45), (player, "video", 90)]}
        result = _run(
            self.news, [COVERAGE], pages, trend=("https://unknown.example/p", COVERAGE),
            probes={player: {"duration": 30.0, "title": "Top 10 headlines of the day"}},
        )

        self.assertEqual(result["url"], COVERAGE_PHOTO)
        self.assertEqual(result["video_url"], "")

    def test_video_the_research_agent_added_to_media_urls_is_a_cited_articles(self) -> None:
        """save_research_brief merges media_candidates into media_urls; they
        still get the title check and the hold-back, not a story video's pass."""
        video = "https://www.youtube.com/watch?v=explainer01"
        news = dict(self.news, source_url=PAGE, media_urls=[video])
        pages = {PAGE: [(PAGE_PHOTO, "image", 45)]}

        off_topic = _run(
            news, [], pages, research_media=[video],
            probes={video: {"duration": 90.0, "title": "Top 10 anime openings"}},
        )
        self.assertEqual(off_topic["url"], PAGE_PHOTO)
        self.assertFalse(off_topic["is_video"])
        self.assertEqual(off_topic["video_url"], "")

        on_topic = _run(
            news, [], pages, research_media=[video],
            probes={video: {"duration": 90.0, "title": "Widget Agent launch explained"}},
        )
        self.assertEqual(on_topic["url"], PAGE_PHOTO)
        self.assertEqual(on_topic["origin"], "source_page")
        self.assertTrue(on_topic["image_first"])
        self.assertEqual(on_topic["video_url"], video)
        self.assertEqual(on_topic["video_origin"], "research_page")

        # Without the brief's list it is media the story came with.
        attached = _run(
            news, [], pages,
            probes={video: {"duration": 90.0, "title": "Top 10 anime openings"}},
        )
        self.assertEqual(attached["url"], video)
        self.assertEqual(attached["origin"], "media_urls")

    def test_story_video_found_after_a_third_party_one_wins(self) -> None:
        embed = "https://www.youtube.com/embed/coverage01"
        linked = "https://vimeo.com/123456"
        news = dict(self.news, body=f"Widget Agent launch. Watch: {linked}")
        result = _run(
            news, [COVERAGE],
            {COVERAGE: [(COVERAGE_PHOTO, "image", 45), (embed, "video", 90)]},
            probes={
                embed: {"duration": 40.0, "title": "Widget Agent launch analysis"},
                linked: {"duration": 25.0, "title": "Widget Agent launch"},
            },
        )

        self.assertEqual(result["url"], linked)
        self.assertTrue(result["is_video"])
        self.assertEqual(result["origin"], "body_url")
        self.assertFalse(result["image_first"])


class ResearchRankingTests(unittest.TestCase):
    news = {"title": "Widget Agent launch", "tags": ["widget", "agent"]}

    def test_url_run_keeps_the_source_page_photo_first(self) -> None:
        news = dict(self.news, source_url=PAGE)
        pages = {PAGE: [(PAGE_PHOTO, "image", 45)], COVERAGE: [(COVERAGE_PHOTO, "image", 48)]}
        result = _run(news, [COVERAGE], pages)

        self.assertEqual(result["image_candidates"][0]["url"], PAGE_PHOTO)
        self.assertEqual(result["image_candidates"][0]["origin"], "source_page")
        research = result["image_candidates"][1]
        self.assertEqual(research["origin"], "research_page")
        self.assertNotIn("+8", research["reason"])

    def test_headline_run_lifts_the_cited_article_photo(self) -> None:
        trend_page = "https://coverage.example.net/widget-agent-launch"
        pages = {
            COVERAGE: [(COVERAGE_PHOTO, "image", 45)],
            trend_page: [("https://cdn.example.net/widget-agent-launch-poster.jpg", "image", 45)],
        }
        result = _run(self.news, [COVERAGE], pages, trend=(trend_page,))

        top = result["image_candidates"][0]
        self.assertEqual(top["url"], COVERAGE_PHOTO)
        self.assertIn("article cited by the research brief +8", top["reason"])

    def test_source_body_and_media_pages_are_not_rescraped_as_research(self) -> None:
        body_page = "https://blog.example.com/widget-agent-notes"
        media_page = "https://gallery.example.com/widget-agent"
        news = dict(
            self.news,
            source_url=PAGE + "?utm_source=whatsapp",
            body=f"More at {body_page}",
            media_urls=[media_page],
        )
        pages = {
            PAGE + "?utm_source=whatsapp": [(PAGE_PHOTO, "image", 45)],
            body_page: [("https://blog.example.com/img/widget-agent-desk.jpg", "image", 45)],
            media_page: [("https://gallery.example.com/widget-agent-stage.jpg", "image", 45)],
        }
        fetched: list[str] = []
        research = [PAGE, body_page + "/", "http://gallery.example.com/widget-agent#top"]
        result = _run(news, research, pages, fetched=fetched)

        self.assertEqual(len(fetched), 3)
        origins = {c["url"]: c["origin"] for c in result["image_candidates"]}
        self.assertEqual(origins[PAGE_PHOTO], "source_page")
        self.assertEqual(origins["https://blog.example.com/img/widget-agent-desk.jpg"], "body_page")
        self.assertEqual(origins["https://gallery.example.com/widget-agent-stage.jpg"], "media_page")

    def test_ai_generated_images_are_never_candidates(self) -> None:
        trend_page = "https://coverage.example.net/widget-agent-launch"
        news = dict(
            self.news,
            source_url=PAGE,
            media_urls=["https://cdn.example.com/ChatGPT-Image-Sep-19-2026.png"],
        )
        pages = {
            PAGE: [
                ("https://cdn.news.example.com/Gemini_Generated_Image_x1.png", "image", 45),
                (PAGE_PHOTO, "image", 48),
            ],
            COVERAGE: [("https://cdn.other.example.org/dall-e-widget-agent.png", "image", 45)],
            trend_page: [("https://cdn.example.net/midjourney-widget-agent.png", "image", 45)],
            "https://midjourney.com/news/widget-agent": [
                ("https://cdn.midjourney.com/david-holz-portrait.jpg", "image", 45),
            ],
        }
        result = _run(
            news, [COVERAGE, "https://midjourney.com/news/widget-agent"], pages,
            trend=(trend_page,), limit=40,
        )

        self.assertEqual(
            sorted(_urls(result)),
            sorted([PAGE_PHOTO, "https://cdn.midjourney.com/david-holz-portrait.jpg"]),
        )

    def test_no_research_sources_means_no_hold_back(self) -> None:
        trend_page = "https://coverage.example.net/widget-agent-launch"
        result = _run(
            self.news, None,
            {trend_page: [("https://cdn.example.net/widget-agent-launch.jpg", "image", 45)]},
            trend=(trend_page,), video=EXPLAINER,
        )

        self.assertFalse(result["image_first"])
        self.assertEqual(result["url"], EXPLAINER["url"])


@unittest.skipUnless(
    shutil.which(settings.ffmpeg_bin), "FFmpeg is required to build a two-shot clip"
)
class SceneCutTests(unittest.TestCase):
    def test_next_scene_cut_finds_the_hard_cut(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            clip = Path(temp_dir) / "two-shots.mp4"
            subprocess.run(
                [settings.ffmpeg_bin, "-y", "-v", "error",
                 "-f", "lavfi", "-i", "color=c=red:s=320x240:d=2:r=25",
                 "-f", "lavfi", "-i", "testsrc2=s=320x240:d=2:r=25",
                 "-filter_complex", "[0][1]concat=n=2:v=1[v]", "-map", "[v]",
                 "-pix_fmt", "yuv420p", str(clip)],
                check=True, capture_output=True, timeout=60,
            )
            self.assertAlmostEqual(media_tools.next_scene_cut(clip, 0.5, 3.0), 1.5, delta=0.1)
            self.assertIsNone(media_tools.next_scene_cut(clip, 2.2, 1.5))
            # A contact-sheet frame just before the cut: the cut 0.1 s in counts.
            self.assertAlmostEqual(media_tools.next_scene_cut(clip, 1.9, 3.0), 0.1, delta=0.05)


# ---------------------------------------------------------------------------
# Replays of real runs. Page lists are the real scrapes saved from run A
# (run-91de85848f6f, started from a headline) and run B (run-01239e778f58,
# the PTC article URL). The saved lists were sorted by score and cut to 8,
# which dropped the 45-point meta entries; _scrape_page_media emits meta tags
# first, so each page's known og:image is put back in front.
# ---------------------------------------------------------------------------

_DPL = "?dpl=dpl_HpKkemvEGp8oEEWX4u9gyqhXPaMM"
HACKTRON = "https://www.hacktron.ai/blog/hacking-openai"
INDIAN_EXPRESS = (
    "https://indianexpress.com/article/world/"
    "indian-origin-researchers-use-claude-ai-breach-openai-systems-hacktron-10884340/"
)
ANTHROPIC = "https://www.anthropic.com/news/investigating-incidents-cybersecurity-evals"
OPENAI_BOUNTY = "https://openai.com/index/bug-bounty-program/"
TOMS_HARDWARE = (
    "https://www.tomshardware.com/tech-industry/cyber-security/"
    "hackers-breach-openai-using-claude-tools-gaining-access-to-employee-accounts-and-the-"
    "companys-internal-codebase-initiating-a-harmless-pull-request-as-proof-of-the-hack"
)
TECHRADAR = (
    "https://www.techradar.com/pro/security/"
    "white-hat-hackers-just-breached-openai-using-anthropics-claude-in-less-than-72-hours-"
    "and-it-is-a-case-study-in-just-how-fast-ai-is-advancing"
)
PTC = (
    "https://www.ptcnews.tv/trending/openai-hack-in-72-hours-who-are-three-indians-who-"
    "used-claude-ai-to-hack-openai-shook-ai-world-4429611"
)
RUN_A_SOURCES = [HACKTRON, INDIAN_EXPRESS, ANTHROPIC, OPENAI_BOUNTY, TOMS_HARDWARE, TECHRADAR]
RUN_A_TITLE = (
    "OpenAI hack in 72 hours: Who are three Indians who used Claude AI to hack OpenAI "
    "and shook AI world"
)

PTC_PHOTO = (
    "https://media.ptcnews.tv/wp-content/uploads/2026/09/"
    "0f29c59e5c0c596df53b71f38a30167f_1280X720.webp"
)
IE_HERO = "https://images.indianexpress.com/2026/09/OPEN-AI.jpg"
HACKTRON_LEAD = "https://www.hacktron.ai/_astro/CwjuS_y3.png"
TECHRADAR_LOGO_PHOTO = "https://cdn.mos.cms.futurecdn.net/SSrgDUXsJwUVxtvheg4YCM"
_BOUNTY = (
    "https://images.ctfassets.net/kftzwdyauwt9/33edbc89-4974-4992-74b6aa90d27c/"
    "c998eedd39aa16653a7a0ac3e77ca9f1/bug-bounty-program.png?w={}&q=90&fm=webp"
)
_PTC_ICONS = [
    f"https://media.ptcnews.tv/wp-content/uploads/{path}"
    for path in (
        "2023/06/small-facebook.png", "2023/06/small-instagram.png",
        "2023/06/small-linkedin.png", "2023/06/small-pinterest.png",
        "2023/06/small-tele.png", "2023/08/small-twitter-new.png",
        "2023/06/small-youtube.png", "2025/assests/ptcnews-logo.jpg",
    )
]
_IE_CHATGPT = "ChatGPT-Image-Sep-19-2026-02_38_14-AM.png"

REAL_PAGES: dict[str, list[tuple[str, str, int]]] = {
    HACKTRON: [
        (HACKTRON_LEAD, "image", 45),
        ("https://www.hacktron.ai/authors/iamnoooob.jpg" + _DPL, "image", 48),
        ("https://www.hacktron.ai/_astro/DlrLdX-c_ZAKAfc.webp" + _DPL, "image", 48),
        ("https://www.hacktron.ai/_astro/kQ1s5GLp_Z1EcfJK.webp" + _DPL, "image", 48),
        ("https://www.hacktron.ai/static/soc2.png", "image", 48),
    ],
    INDIAN_EXPRESS: [
        (IE_HERO, "image", 45),
        (IE_HERO + "?w=1024", "image", 48),
        ("https://indianexpress.com/wp-content/themes/indianexpress/images/default-ie.jpg",
         "image", 48),
        ("https://data.indianexpress.com/election2019/track_1x1.jpg", "image", 48),
        (f"https://images.indianexpress.com/2026/09/{_IE_CHATGPT}?resize=600,400", "image", 48),
    ] + [
        (f"https://indianexpress.com/wp-content/uploads/2026/09/{_IE_CHATGPT}{query}",
         "image", 48)
        for query in ("", "?resize=450", "?resize=600", "?resize=768")
    ],
    ANTHROPIC: [(
        "https://www.anthropic.com/api/opengraph-illustration"
        "?name=Hand%20Lock&backgroundColor=heather", "image", 45,
    )],
    OPENAI_BOUNTY: [
        (_BOUNTY.format(width), "image", 48)
        for width in (3840, 640, 750, 828, 1080, 1200, 1920, 2048)
    ],
    TOMS_HARDWARE: [
        (f"https://cdn.mos.cms.futurecdn.net/rqNPxisBCHvtJVFXGZqfnD{size}.jpg", "image", 53)
        for size in ("", "-840-80", "-650-80", "-500-80", "-450-80", "-320-80")
    ] + [
        ("https://cdn.mos.cms.futurecdn.net/flexiimages/a0ldfjnzhe1774272054.png", "image", 48),
        ("https://cdn.mos.cms.futurecdn.net/flexiimages/lkowbgkkbw1774425638.png", "image", 48),
    ],
    TECHRADAR: [(f"{TECHRADAR_LOGO_PHOTO}-1200-80.jpg", "image", 45)] + [
        (f"{TECHRADAR_LOGO_PHOTO}{size}.jpg", "image", 53)
        for size in ("", "-1920-80", "-1600-80", "-1200-80", "-1024-80", "-970-80",
                     "-750-80", "-650-80")
    ],
    PTC: [(PTC_PHOTO, "image", 45)] + [(icon, "image", 48) for icon in _PTC_ICONS],
}
# Run A's live trend search returned TechRadar; its logo photo was every one
# of the five candidates the agent saw, and the cover shipped a web video.
REAL_TREND = ("https://unknown.example/p", TECHRADAR)
MINT_EXPLAINER = {
    "url": "https://www.youtube.com/watch?v=DelLsAFp7wA",
    "duration": 182.0,
    "title": "OpenAI Hacked Using Rival AI Claude: 3 Indian-Origin Researchers Reveal How",
}


class RealRunReplayTests(unittest.TestCase):
    def _replay(self, news: dict, research: list[str], limit: int = 40) -> dict:
        return _run(
            news, research, REAL_PAGES, trend=REAL_TREND, video=MINT_EXPLAINER, limit=limit
        )

    def _rank(self, urls: list[str], prefix: str) -> int:
        return next(i for i, url in enumerate(urls) if url.startswith(prefix))

    def _assert_no_ai_or_chrome(self, urls: list[str]) -> None:
        for url in urls:
            self.assertNotIn("chatgpt-image", url.lower())
            self.assertFalse(media_tools._is_page_chrome(url), url)

    def test_run_a_story_photos_outrank_the_techradar_logo_photo(self) -> None:
        news = {"title": RUN_A_TITLE, "body": RUN_A_TITLE, "tags": [], "source_url": ""}
        full = _urls(self._replay(news, RUN_A_SOURCES))
        # Every cited article's lead picture is a candidate. Which of them is
        # a real photo of the story and which a stock logo shot is for the
        # vision check to say: story-first page order puts TechRadar's article
        # (and its ChatGPT-logo photo) among the scraped pages, so ranking
        # alone no longer pushes that photo below Indian Express's hero.
        for photo in (IE_HERO, HACKTRON_LEAD, TECHRADAR_LOGO_PHOTO):
            self._rank(full, photo)
        self.assertEqual(sum(url.startswith(TECHRADAR_LOGO_PHOTO) for url in full), 1)
        self._assert_no_ai_or_chrome(full)

        shortlist = self._replay(news, RUN_A_SOURCES, limit=media_tools._IMAGE_CANDIDATE_LIMIT)
        self.assertIn(IE_HERO, _urls(shortlist))
        self.assertIn(HACKTRON_LEAD, _urls(shortlist))
        self.assertEqual(shortlist["url"], shortlist["image_url"])
        self.assertFalse(shortlist["is_video"])
        self.assertTrue(shortlist["image_first"])
        self.assertEqual(shortlist["video_url"], MINT_EXPLAINER["url"])

    def test_run_a_citing_ptc_puts_the_researchers_photo_first(self) -> None:
        news = {"title": RUN_A_TITLE, "body": RUN_A_TITLE, "tags": [], "source_url": ""}
        result = self._replay(news, [PTC] + RUN_A_SOURCES)
        full = _urls(result)

        self.assertEqual(result["image_url"], PTC_PHOTO)
        logo = self._rank(full, TECHRADAR_LOGO_PHOTO)
        self.assertLess(self._rank(full, PTC_PHOTO), logo)
        for photo in (IE_HERO, HACKTRON_LEAD):
            self._rank(full, photo)  # still candidates for the vision check
        self._assert_no_ai_or_chrome(full)

    def test_run_b_url_run_keeps_the_ptc_photo_first(self) -> None:
        news = {
            "title": RUN_A_TITLE + " | Trending - PTC News",
            "tags": ["url"],
            "source_url": PTC + "?utm_source=whatsapp",
            "media_urls": list(_PTC_ICONS),
        }
        pages = dict(REAL_PAGES)
        pages[news["source_url"]] = REAL_PAGES[PTC]
        fetched: list[str] = []
        result = _run(
            news, [PTC, HACKTRON, OPENAI_BOUNTY, TECHRADAR], pages, trend=REAL_TREND,
            video=MINT_EXPLAINER, fetched=fetched,
        )

        self.assertEqual(result["image_url"], PTC_PHOTO)
        self.assertEqual(result["image_origin"], "source_page")
        self.assertEqual(sum("ptcnews.tv/trending" in url for url in fetched), 1)
        self.assertTrue(result["image_first"])
        self.assertEqual(result["video_origin"], "web_search")


if __name__ == "__main__":
    unittest.main()


def test_story_articles_are_scraped_before_background_pages():
    """The brief for a real headline run cited a short link, a team page, a
    code advisory and two program pages before the five news articles. Only
    the first five pages are scraped, so the photo of the people in the story
    was never seen. Pages whose address repeats the story's words go first."""
    news = {"title": "OpenAI hack in 72 hours: Who are three Indians who used Claude AI "
                     "to hack OpenAI and shook AI world", "tags": []}
    cited = [
        "https://t.co/kmrW9TRq9Y",
        "https://www.hacktron.ai/team",
        "https://github.com/discourse/discourse/security/advisories/GHSA-vhm9-85gw-x335",
        "https://openai.com/index/bug-bounty-program/",
        "https://openai.com/index/safety-bug-bounty/",
        "https://openai.com/policies/coordinated-vulnerability-disclosure-policy/",
        "https://www.techradar.com/pro/security/white-hat-hackers-just-breached-openai-using-anthropics-claude-in-less-than-72-hours",
        "https://indianexpress.com/article/world/indian-origin-researchers-use-claude-ai-breach-openai-systems-hacktron-10884340/",
        "https://www.ndtv.com/feature/openai-hack-in-under-72-hours-how-3-indian-origin-researchers-used-claude-12068192",
        "https://www.indiatoday.in/technology/news/story/meet-3-indians-who-hacked-openai-using-claude-and-shook-up-ai-world-2998815-2026-09-20",
    ]
    ordered = media_tools._story_pages_first(cited, news)
    assert "https://t.co/kmrW9TRq9Y" not in ordered
    # The four articles about the story come first; background pages only
    # fill whatever scrape slots are left, in the brief's own order.
    assert set(ordered[:4]) == set(cited[6:])
    assert ordered[4:] == cited[1:6]


def test_story_page_order_keeps_the_briefs_order_on_ties():
    news = {"title": "Widget Agent launch", "tags": []}
    pages = ["https://a.example/one", "https://b.example/two", "https://c.example/widget-agent"]
    assert media_tools._story_pages_first(pages, news) == [pages[2], pages[0], pages[1]]
