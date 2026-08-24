from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from app.tools import media_tools
from app.tools.brand_layout import ACCENT_GREEN, HEADLINE_FONT_SIZE, headline_font


class FindSourceClipTests(unittest.TestCase):
    def test_attached_photo_page_is_scraped_for_its_real_image(self) -> None:
        page = "https://movie.douban.com/photos/photo/2934982707/"
        image = "https://img.doubanio.com/view/photo/l/public/p2934982707.jpg"
        news = {"title": "Niu Lai animated film", "media_urls": [page]}

        def scrape(url: str) -> list[tuple[str, str, int]]:
            return [(image, "image", 45)] if url == page else []

        with (
            patch.object(media_tools, "_scrape_page_media", side_effect=scrape),
            patch.object(
                media_tools,
                "_search_trending_pages",
                return_value=([], "live trend search checked 0 page(s)"),
            ),
            patch.object(media_tools, "_probe_with_ytdlp", return_value=None),
            patch.object(media_tools, "_search_video_online", return_value=None),
        ):
            result = media_tools.find_source_clip(news)

        self.assertEqual(result["image_url"], image)
        self.assertEqual(result["image_origin"], "media_page")

    def test_url_only_news_title_becomes_subject_query(self) -> None:
        query = media_tools._default_visual_query(
            {
                "title": "https://en.wikipedia.org/wiki/Niu_Lai",
                "body": "here is a new Chinese movie getting viral",
            }
        )
        self.assertIn("Niu Lai", query)
        self.assertNotIn("https://", query)

    def test_trusted_catalog_image_outranks_unverified_blog_og(self) -> None:
        news = {"title": "Niu Lai animated film", "tags": []}
        trusted = media_tools._rank_candidate(
            media_tools._MediaCandidate(
                "https://img.doubanio.com/niu-lai.jpg",
                "image",
                58,
                "media_page",
                "https://movie.douban.com/photos/photo/2934982707/",
            ),
            news,
            "",
        )
        blog = media_tools._rank_candidate(
            media_tools._MediaCandidate(
                "https://niulai.blog/og.png",
                "image",
                58,
                "trend_search",
                "https://niulai.blog/",
            ),
            news,
            "",
        )
        self.assertGreater(trusted.score, blog.score)

    def test_live_trend_visual_can_outrank_first_available_image(self) -> None:
        news = {
            "title": "Widget Agent launch",
            "source_url": "https://official.example.com/news/widget-agent",
            "media_urls": ["https://cdn.example.com/available.jpg"],
            "tags": ["widget", "agent"],
        }

        def scrape(page_url: str) -> list[tuple[str, str, int]]:
            if page_url == "https://coverage.example.com/widget-agent-launch":
                return [
                    (
                        "https://images.example.com/widget-agent-launch-hero.jpg",
                        "image",
                        45,
                    )
                ]
            return []

        with (
            patch.object(
                media_tools,
                "_search_trending_pages",
                return_value=(
                    ["https://coverage.example.com/widget-agent-launch"],
                    "live trend search checked 1 page(s)",
                ),
            ),
            patch.object(media_tools, "_scrape_page_media", side_effect=scrape),
            patch.object(media_tools, "_probe_with_ytdlp", return_value=None),
            patch.object(media_tools, "_search_video_online", return_value=None),
        ):
            result = media_tools.find_source_clip(news)

        self.assertEqual(
            result["image_url"],
            "https://images.example.com/widget-agent-launch-hero.jpg",
        )
        self.assertEqual(result["image_origin"], "trend_search")
        self.assertEqual(len(result["image_candidates"]), 2)
        self.assertIn("live trend search checked", result["trend_search"])

    def test_generic_logo_is_demoted_below_relevant_visual(self) -> None:
        news = {
            "title": "Widget Agent launch",
            "source_url": "",
            "media_urls": [
                "https://cdn.example.com/widget-agent-logo-icon.png",
                "https://cdn.example.com/widget-agent-launch-demo.jpg",
            ],
            "tags": ["widget", "agent"],
        }
        with (
            patch.object(
                media_tools,
                "_search_trending_pages",
                return_value=([], "live trend search checked 0 page(s)"),
            ),
            patch.object(media_tools, "_probe_with_ytdlp", return_value=None),
            patch.object(media_tools, "_search_video_online", return_value=None),
        ):
            result = media_tools.find_source_clip(news)

        self.assertEqual(
            result["image_url"],
            "https://cdn.example.com/widget-agent-launch-demo.jpg",
        )


class ImageQualityTests(unittest.TestCase):
    def test_download_rejects_tiny_image(self) -> None:
        payload = BytesIO()
        Image.new("RGB", (200, 200), (20, 20, 20)).save(payload, format="PNG")
        response = Mock()
        response.content = payload.getvalue()
        response.headers = {"Content-Type": "image/png"}
        response.raise_for_status.return_value = None

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            media_tools.requests,
            "get",
            return_value=response,
        ):
            with self.assertRaisesRegex(RuntimeError, "unsuitable"):
                media_tools.download_image(
                    "https://cdn.example.com/tiny.png",
                    temp_dir,
                )
            self.assertEqual(list(Path(temp_dir).rglob("img-*")), [])


class CoverTypographyTests(unittest.TestCase):
    def test_cover_overlay_leaves_counter_zone_empty(self) -> None:
        transparent = Image.new("RGBA", (1080, 1350), (0, 0, 0, 0))
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(media_tools, "_load_scrubbed_template", return_value=None),
            patch.object(media_tools, "_render_title_block", return_value=transparent),
        ):
            path = media_tools._build_overlay_png("TITLE", "", Path(temp_dir))
            with Image.open(path) as overlay:
                counter_zone = overlay.crop((88, 76, 160, 124))
                self.assertIsNone(counter_zone.getbbox())

    def test_template_example_title_reservation_is_fully_scrubbed(self) -> None:
        with Image.open(media_tools.settings.cover_overlay_template) as source:
            scrubbed = media_tools._scrub_template_text(source.convert("RGBA"))
        x0 = int(scrubbed.width * media_tools._TEMPLATE_TEXT_BOX[0])
        y0 = int(scrubbed.height * media_tools._TEMPLATE_TEXT_BOX[1])
        x1 = int(scrubbed.width * media_tools._TEMPLATE_TEXT_BOX[2])
        y1 = int(scrubbed.height * media_tools._TEMPLATE_TEXT_BOX[3])
        reservation = scrubbed.crop((x0, y0, x1, y1))
        red, green, blue, _alpha = reservation.getextrema()
        self.assertEqual(red[1], 0)
        self.assertEqual(green[1], 0)
        self.assertEqual(blue[1], 0)

    def test_cover_wrap_uses_shared_headline_scale_and_max_three_lines(self) -> None:
        font = headline_font(HEADLINE_FONT_SIZE)
        lines = media_tools._wrap_title(
            "YOUR AGENT LOOKS SMART UNTIL REALITY HITS TODAY",
            font,
            1080 * media_tools._TITLE_MAX_WIDTH_FRAC,
        )
        self.assertLessEqual(len(lines), 3)
        self.assertGreaterEqual(len(lines), 2)

    def test_cover_title_contains_exact_brand_accent(self) -> None:
        rendered = media_tools._render_title_block(
            "AGENTS FAIL IN PRODUCTION",
            "IN PRODUCTION",
        ).convert("RGBA")
        accent = (*ACCENT_GREEN, 255)
        self.assertIn(accent, rendered.getdata())


if __name__ == "__main__":
    unittest.main()
