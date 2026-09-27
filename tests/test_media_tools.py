from __future__ import annotations

import os
import tempfile
import unittest
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from app.schemas import CarouselDesign
from app.tools import media_tools
from app.tools.brand_layout import ACCENT_GREEN, HEADLINE_FONT_SIZE, headline_font


class FindSourceClipTests(unittest.TestCase):
    def test_article_photo_outranks_text_only_social_banner(self) -> None:
        news = {
            "title": "OpenAI Broadcom Jalapeno inference chip",
            "source_url": "https://openai.com/index/jalapeno-chip/",
        }
        banner = media_tools._rank_candidate(
            media_tools._MediaCandidate(
                "https://images.example/openai-jalapeno-image-16_9.png",
                "image",
                58,
                "source_page",
                news["source_url"],
            ),
            news,
            news["source_url"],
        )
        photo = media_tools._rank_candidate(
            media_tools._MediaCandidate(
                "https://images.example/openai-jalapeno-chip-photo.png",
                "image",
                58,
                "source_page",
                news["source_url"],
            ),
            news,
            news["source_url"],
        )

        self.assertGreater(photo.score, banner.score)
        self.assertIn("banner/social card", banner.reason)

    def test_page_scrape_includes_real_article_images_not_only_og_card(self) -> None:
        page = "https://official.example.com/jalapeno"
        banner = "https://cdn.example.com/jalapeno-16_9.png"
        photo = "https://cdn.example.com/sam-hock-jalapeno.png"
        response = _StreamedResponse(
            (
                f'<meta property="og:image" content="{banner}">'
                f'<picture><source srcset="{photo}?w=1024 1024w, {photo}?w=2048 2048w">'
                f'<img data-src="{photo}" alt="Sam Altman holding the unveiled chip"></picture>'
            ).encode(),
            status=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
        )

        with patch.object(media_tools.requests, "get", return_value=response) as get:
            found = media_tools._scrape_page_media(page)
        self.assertTrue(get.call_args.kwargs["stream"])

        urls = [item[0] for item in found]
        self.assertIn(banner, urls)
        self.assertIn(photo, urls)
        self.assertIn(f"{photo}?w=2048", urls)

    def test_video_search_skips_unrelated_playable_anime(self) -> None:
        ydl = Mock()
        ydl.extract_info.return_value = {
            "entries": [
                {
                    "webpage_url": "https://video.example/anime",
                    "duration": 30,
                    "title": "OpenAI Sora trending anime fight scene",
                },
                {
                    "webpage_url": "https://video.example/jalapeno",
                    "duration": 20,
                    "title": "OpenAI and Broadcom unveil Jalapeno chip",
                },
            ]
        }
        ydl_context = Mock()
        ydl_context.__enter__ = Mock(return_value=ydl)
        ydl_context.__exit__ = Mock(return_value=False)

        with patch.object(media_tools, "YoutubeDL", return_value=ydl_context):
            result = media_tools._search_video_online(
                "OpenAI Broadcom Jalapeno official launch"
            )

        self.assertIsNotNone(result)
        self.assertEqual(result["url"], "https://video.example/jalapeno")

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
            patch.object(media_tools, "_probe_image_sizes", return_value={}),
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
            patch.object(media_tools, "_probe_image_sizes", return_value={}),
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
            patch.object(media_tools, "_probe_image_sizes", return_value={}),
        ):
            result = media_tools.find_source_clip(news)

        self.assertEqual(
            result["image_url"],
            "https://cdn.example.com/widget-agent-launch-demo.jpg",
        )


def _image_bytes(size: tuple[int, int], fmt: str, noise: bool = False, **save: object) -> bytes:
    """Encoded image bytes; ``noise`` makes a WebP too big to parse from 64 KB."""
    if noise:
        image = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
    else:
        image = Image.new("RGB", size, (90, 120, 150))
    payload = BytesIO()
    image.save(payload, format=fmt, **save)
    return payload.getvalue()


_camera = Image.Exif()
_camera[0x010F] = "Camera"  # Make; any EXIF block makes Pillow write a VP8X WebP
_EXIF = _camera.tobytes()


class _StreamedResponse:
    """A streamed requests response: a context manager yielding chunks."""

    def __init__(
        self,
        data: bytes,
        status: int = 206,
        error: Exception | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.data = data
        self.status_code = status
        self.error = error
        self.sent = 0
        self.headers = headers or {}
        content_type = self.headers.get("Content-Type", "")
        self.encoding = content_type.split("charset=", 1)[1] if "charset=" in content_type else None
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "_StreamedResponse":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def raise_for_status(self) -> None:
        if self.error is not None:
            raise self.error

    def iter_content(self, chunk_size: int = 1) -> Iterator[bytes]:
        for start in range(0, len(self.data), chunk_size):
            chunk = self.data[start:start + chunk_size]
            self.sent += len(chunk)
            yield chunk


def _download_response(data: bytes, error: Exception | None = None) -> "_StreamedResponse":
    return _StreamedResponse(data, status=200, error=error, headers={"Content-Type": "image/jpeg"})


class ImageVariantUpgradeTests(unittest.TestCase):
    def test_small_cdn_variants_are_raised_to_cover_size(self) -> None:
        ichef = "https://ichef.bbci.co.uk/{}/cpsprodpb/c694/live/dc86c7a0.jpg.webp"
        abc = (
            "https://live-production.wcms.abc-cdn.net.au/a1898572?impolicy={}"
            "&cropH=1080&cropW=1920&xPos=0&yPos=0&width={}&height={}&imformat=generic"
        )
        guardian = "https://i.guim.co.uk/img/media/aaa/0_0_5000_3000/master/5000.jpg?{}"
        cases = {
            # BBC ichef serves only fixed widths; 2048 is the largest.
            ichef.format("news/240"): ichef.format("news/2048"),
            ichef.format("news/480"): ichef.format("news/2048"),
            ichef.format("news/1024"): ichef.format("news/2048"),
            ichef.format("news/1536"): ichef.format("news/2048"),
            ichef.format("ace/standard/976"): ichef.format("ace/standard/2048"),
            ichef.format("ace/ws/640"): ichef.format("ace/ws/2048"),
            # The og:image's branded path has "BBC NEWS" burned in and stops at 1024.
            "https://ichef.bbci.co.uk/news/1024/branded_news/c694/live/dc86c7a0.jpg":
                "https://ichef.bbci.co.uk/news/2048/cpsprodpb/c694/live/dc86c7a0.jpg",
            # ABC: the clean policy, width and height scaled together.
            abc.format("wcms_watermark_news", 862, 485): abc.format("wcms_crop_resize", 1600, 900),
            abc.format("wcms_watermark_news", 1600, 900): abc.format("wcms_crop_resize", 1600, 900),
            # The Guardian: unsigned width+dpr form, signed share card or not.
            guardian.format("width=445&quality=85&s=5e1ebb"):
                guardian.format("width=1600&dpr=1&s=none&crop=none"),
            guardian.format(
                "width=1200&height=630&quality=85&overlay-align=bottom%2Cleft&overlay-base64="
                "L2ltZy9zdGF0aWMvb3ZlcmxheXMvdGctb3BpbmlvbnMucG5n&s=5e1e"
            ): guardian.format("width=1600&dpr=1&s=none&crop=none"),
            guardian.format("width=220&dpr=2&s=none"): guardian.format("width=1600&dpr=1&s=none&crop=none"),
            "https://images.ctfassets.net/x/y/z/card.png?w=800&q=90&fm=webp":
                "https://images.ctfassets.net/x/y/z/card.png?w=1600&q=90&fm=webp",
            "https://images.example.com/2026/09/photo.png?resize=600,400":
                "https://images.example.com/2026/09/photo.png?resize=1600,1067",
            "https://i0.wp.com/site.com/photo.jpg?resize=600%2C400&ssl=1":
                "https://i0.wp.com/site.com/photo.jpg?resize=1600%2C1067&ssl=1",
            # Next.js serves only its configured widths; 1920 is a default one.
            "https://mistral.ai/_next/image?url=%2Fimg%2Fhero.png&w=640&q=75":
                "https://mistral.ai/_next/image?url=%2Fimg%2Fhero.png&w=1920&q=75",
        }
        for small, large in cases.items():
            with self.subTest(url=small):
                self.assertEqual(media_tools.upgrade_image_variant(small), large)
                self.assertEqual(media_tools.upgrade_image_variant(large), large)

    def test_signed_large_and_unsized_urls_are_left_alone(self) -> None:
        for url in [
            "https://cdn.example.com/p.jpg?w=800&X-Amz-Signature=abc123",
            "https://cdn.example.com/p.jpg?w=800&expires=1790000000&token=abc",
            # Google Cloud Storage V4, Akamai tokens and a bare auth token.
            "https://storage.googleapis.com/b/photo.jpg?width=800&X-Goog-Algorithm=GOOG4-RSA-SHA256"
            "&X-Goog-Credential=a%2F20260926&X-Goog-Signature=abc123",
            "https://cdn.example.com/p.jpg?w=800&hdnts=exp=1790000000~hmac=abc",
            "https://cdn.example.com/p.jpg?w=800&__token__=exp=1790000000~hmac=abc",
            "https://cdn.example.com/p.jpg?w=800&auth=abc123",
            "https://cdn.example.com/p.jpg?w=2000",
            # width=700&dpr=2 is already 1400 wide.
            "https://cdn.example.com/p.jpg?width=700&dpr=2",
            "https://ichef.bbci.co.uk/news/2048/cpsprodpb/c694/live/x.jpg.webp",
            # /news/raw/ is the original upload, whatever its size.
            "https://ichef.bbci.co.uk/news/raw/cpsprodpb/c694/live/x.jpg",
            # An ABC width without a height comes back 1600x100: left alone.
            "https://live-production.wcms.abc-cdn.net.au/a1?impolicy=wcms_watermark_news&width=862",
            "https://www.example.com/news/240/story-photo.jpg",
            "https://cdn.example.com/2026/09/photo-320x180.jpg",
            "https://cdn.example.com/2026/09/photo.jpg",
        ]:
            with self.subTest(url=url):
                self.assertEqual(media_tools.upgrade_image_variant(url), url)

    def test_the_size_a_url_delivers_counts_dpr_and_the_ichef_path(self) -> None:
        cases = {
            "https://i.guim.co.uk/img/media/a/master/5000.jpg?width=700&dpr=2&s=none": (1400, 0),
            "https://cdn.example.com/p.jpg?w=800&h=450&dpr=2": (1600, 900),
            "https://ichef.bbci.co.uk/news/240/cpsprodpb/c694/live/x.jpg": (240, 0),
            "https://ichef.bbci.co.uk/news/2048/cpsprodpb/c694/live/x.jpg": (2048, 0),
        }
        for url, size in cases.items():
            with self.subTest(url=url):
                self.assertEqual(media_tools._advertised_size(url), size)
        self.assertTrue(media_tools._is_thumbnail("https://ichef.bbci.co.uk/news/240/cpsprodpb/a/x.jpg"))
        # The clean 2048 variant outranks the branded 1024 one it replaces.
        self.assertGreater(
            media_tools._variant_rank("https://ichef.bbci.co.uk/news/2048/cpsprodpb/a/x.jpg"),
            media_tools._variant_rank("https://ichef.bbci.co.uk/news/1024/branded_news/a/x.jpg"),
        )
        self.assertTrue(media_tools._is_page_chrome(
            "https://i.guim.co.uk/img/uploads/2020/01/01/Jane_Doe.png?width=180&dpr=2&s=none"
        ))

    def test_cover_fit_uses_the_download_limits(self) -> None:
        self.assertTrue(media_tools.image_fits_cover(1024, 576))
        self.assertTrue(media_tools.image_fits_cover(1600, 900))
        for size in [(192, 192), (258, 258), (862, 485), (3000, 900), (0, 900), (1200, 300)]:
            with self.subTest(size=size):
                self.assertFalse(media_tools.image_fits_cover(*size))

    def test_wikimedia_gets_the_named_agent_without_an_email(self) -> None:
        agent = media_tools.WIKIMEDIA_USER_AGENT
        self.assertNotIn("@", agent)
        self.assertIn("https://", agent)
        for url in (
            "https://upload.wikimedia.org/wikipedia/commons/a/ab/Portrait.jpg",
            "https://commons.wikimedia.org/w/api.php",
            "https://www.wikidata.org/w/api.php",
            "https://en.wikipedia.org/wiki/Anthony_Albanese",
        ):
            with self.subTest(url=url):
                self.assertEqual(media_tools._headers_for(url), {"User-Agent": agent})
        for url in ("https://notwikipedia.org/a.jpg", "https://cdn.example.com/a.jpg"):
            with self.subTest(url=url):
                self.assertEqual(media_tools._headers_for(url), media_tools._HTTP_HEADERS)

    def test_placeholder_keeps_its_recognisable_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(media_tools.placeholder_background(temp_dir))
            self.assertEqual(path.name, media_tools.PLACEHOLDER_NAME)
            self.assertEqual(path.parent.name, "cover")
            self.assertTrue(path.is_file())


class ImageSizeProbeTests(unittest.TestCase):
    def _probe(self, data: bytes, **response: object) -> tuple[tuple[int, int], dict, int]:
        fake = _StreamedResponse(data, **response)
        with patch.object(media_tools.requests, "get", return_value=fake) as get:
            size = media_tools.probe_image_size("https://cdn.example.com/photo")
        return size, get.call_args.kwargs, fake.sent

    def test_reads_the_size_from_the_first_bytes(self) -> None:
        cases = {
            "jpeg": (_image_bytes((1600, 900), "JPEG"), (1600, 900)),
            "png": (_image_bytes((1024, 576), "PNG"), (1024, 576)),
            "small webp": (_image_bytes((1200, 800), "WEBP"), (1200, 800)),
            # Pillow needs a whole WebP; a big one is read from its RIFF header.
            "lossy webp": (_image_bytes((900, 600), "WEBP", noise=True, quality=95), (900, 600)),
            "lossless webp": (_image_bytes((700, 500), "WEBP", noise=True, lossless=True),
                              (700, 500)),
            "extended webp": (_image_bytes((640, 480), "WEBP", noise=True, exif=_EXIF),
                              (640, 480)),
        }
        self.assertEqual(cases["extended webp"][0][12:16], b"VP8X")
        for label, (data, expected) in cases.items():
            with self.subTest(label=label):
                size, kwargs, sent = self._probe(data)
                self.assertEqual(size, expected)
                self.assertTrue(kwargs["stream"])
                self.assertEqual(kwargs["headers"]["Range"], "bytes=0-65535")
                self.assertLessEqual(sent, media_tools._SIZE_PROBE_BYTES)
        self.assertGreater(len(cases["lossy webp"][0]), media_tools._SIZE_PROBE_BYTES)

    def test_any_failure_means_unknown(self) -> None:
        with patch.object(
            media_tools.requests, "get", side_effect=media_tools.requests.ConnectionError("dns")
        ):
            self.assertEqual(media_tools.probe_image_size("https://cdn.example.com/a.jpg"), (0, 0))
        error = media_tools.requests.HTTPError("404 Client Error")
        self.assertEqual(self._probe(b"", status=404, error=error)[0], (0, 0))
        self.assertEqual(self._probe(b"<!doctype html><title>Not found</title>" * 50)[0], (0, 0))

    def test_probes_run_together_and_report_each_url(self) -> None:
        urls = [f"https://cdn.example.com/{i}.jpg" for i in range(10)]
        sizes = {url: (100 * (i + 1), 100) for i, url in enumerate(urls)}
        with patch.object(media_tools, "probe_image_size", side_effect=sizes.get) as probe:
            found = media_tools._probe_image_sizes(urls + urls[:2])

        self.assertEqual(probe.call_count, media_tools._SIZE_PROBE_LIMIT)
        self.assertEqual(found, {url: sizes[url] for url in urls[:media_tools._SIZE_PROBE_LIMIT]})
        self.assertEqual(media_tools._probe_image_sizes([]), {})


class DownloadImageTests(unittest.TestCase):
    SMALL = "https://ichef.bbci.co.uk/news/240/cpsprodpb/c694/live/dc86c7a0.jpg.webp"
    LARGE = "https://ichef.bbci.co.uk/news/2048/cpsprodpb/c694/live/dc86c7a0.jpg.webp"
    # A width under cover size that is not a thumbnail: the original may still fit.
    NARROW = "https://cdn.example.com/2026/09/photo.jpg?w=800"
    WIDE = "https://cdn.example.com/2026/09/photo.jpg?w=1600"

    def _download(self, responses: dict[str, Mock], url: str = SMALL) -> tuple[str, list[str]]:
        calls: list[str] = []

        def get(url: str, **_kwargs: object) -> Mock:
            calls.append(url)
            return responses[url]

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(media_tools.requests, "get", side_effect=get),
        ):
            try:
                path = media_tools.download_image(url, temp_dir)
            finally:
                self.calls = calls
            with Image.open(path) as image:
                size = image.size
        return f"{size[0]}x{size[1]}", calls

    def test_the_cover_sized_variant_is_fetched_first(self) -> None:
        size, calls = self._download(
            {self.LARGE: _download_response(_image_bytes((2048, 1152), "JPEG"))}
        )
        self.assertEqual(calls, [self.LARGE])
        self.assertEqual(size, "2048x1152")

    def test_the_original_is_tried_when_the_variant_fails(self) -> None:
        missing = media_tools.requests.HTTPError("404 Client Error: Not Found")
        size, calls = self._download({
            self.WIDE: _download_response(b"", error=missing),
            self.NARROW: _download_response(_image_bytes((1200, 800), "JPEG")),
        }, url=self.NARROW)
        self.assertEqual(calls, [self.WIDE, self.NARROW])
        self.assertEqual(size, "1200x800")

    def test_a_thumbnail_original_is_not_fetched_after_its_variant_fails(self) -> None:
        """The ABC 862x485 original cost a second slow GET that could only fail."""
        missing = media_tools.requests.HTTPError("404 Client Error: Not Found")
        with self.assertRaisesRegex(
            RuntimeError, "unsuitable.*404.*try the next ranked image_candidates entry"
        ):
            self._download({
                self.LARGE: _download_response(b"", error=missing),
                self.SMALL: _download_response(_image_bytes((240, 135), "JPEG")),
            })
        self.assertEqual(self.calls, [self.LARGE])

    def test_an_endless_or_huge_body_is_cut_off(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(media_tools, "_IMAGE_MAX_BYTES", 1000),
            patch.object(media_tools.requests, "get",
                         return_value=_download_response(_image_bytes((1600, 900), "BMP"))),
        ):
            with self.assertRaisesRegex(RuntimeError, "larger than"):
                media_tools.download_image("https://cdn.example.com/huge.bmp", temp_dir)
            self.assertEqual(list(Path(temp_dir).rglob("img-*")), [])

    def test_wikimedia_downloads_send_the_named_agent(self) -> None:
        url = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Portrait.jpg"
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                media_tools.requests, "get",
                return_value=_download_response(_image_bytes((1200, 1500), "JPEG")),
            ) as get,
        ):
            media_tools.download_image(url, temp_dir)
        self.assertEqual(
            get.call_args.kwargs["headers"], {"User-Agent": media_tools.WIKIMEDIA_USER_AGENT}
        )


class ImageQualityTests(unittest.TestCase):
    def test_download_rejects_tiny_image(self) -> None:
        payload = BytesIO()
        Image.new("RGB", (200, 200), (20, 20, 20)).save(payload, format="PNG")
        response = _StreamedResponse(
            payload.getvalue(), status=200, headers={"Content-Type": "image/png"}
        )

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

    def test_smart_crop_moves_toward_off_center_subject(self) -> None:
        source = Image.new("RGB", (1600, 900), (25, 45, 75))
        source.paste((215, 135, 90), (1200, 170, 1480, 760))
        source.paste((20, 20, 20), (1260, 260, 1300, 310))
        source.paste((235, 235, 220), (1350, 430, 1460, 520))

        target_aspect = media_tools.settings.slide_width / media_tools.settings.slide_height
        left, _top, right, _bottom = media_tools._smart_crop_box(
            source,
            target_aspect,
        )
        anchor_x, _anchor_y = media_tools._crop_anchor(source, target_aspect)

        self.assertGreater(left, 400)
        self.assertGreaterEqual(right, 1480)
        self.assertGreater(anchor_x, 0.5)

    def test_prepared_still_fills_the_full_cover_without_black_footer(self) -> None:
        source = Image.new("RGB", (1600, 900), (210, 120, 50))
        source.paste((30, 70, 160), (500, 0, 1100, source.height))

        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "wide.png"
            source.save(source_path)
            result_path = media_tools._prepare_still_cover(source_path, Path(temp_dir))
            with Image.open(result_path) as fitted:
                fitted_size = fitted.size
                top_left = fitted.getpixel((0, 50))
                top_right = fitted.getpixel((fitted.width - 1, 50))
                lower = fitted.getpixel((fitted.width // 2, fitted.height - 50))

        self.assertEqual(
            fitted_size,
            (media_tools.settings.slide_width * 2, media_tools.settings.slide_height * 2),
        )
        self.assertNotEqual(top_left, (0, 0, 0))
        self.assertNotEqual(top_right, (0, 0, 0))
        self.assertNotEqual(lower, (0, 0, 0))


class CoverTypographyTests(unittest.TestCase):
    def test_default_design_omits_handle_from_cover_only(self) -> None:
        design = CarouselDesign(logo_visible=False)
        self.assertFalse(design.cover.handle_visible)
        self.assertTrue(design.inside.handle_visible)
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(media_tools, "_load_scrubbed_template", return_value=None),
            patch.object(
                media_tools.brand_identity,
                "require_handle",
                side_effect=AssertionError("cover should not request a handle"),
            ),
        ):
            path = media_tools._build_overlay_png(
                "TITLE",
                "",
                Path(temp_dir),
                design,
            )
            self.assertTrue(path.is_file())

    def test_cover_shadow_is_a_separate_mandatory_overlay_layer(self) -> None:
        design = CarouselDesign(logo_visible=False)
        transparent = Image.new("RGBA", (1080, 1350), (0, 0, 0, 0))
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(media_tools, "_load_scrubbed_template", return_value=None),
            patch.object(media_tools, "_render_title_block", return_value=transparent),
        ):
            path = media_tools._build_overlay_png("TITLE", "", Path(temp_dir), design)
            with Image.open(path) as overlay:
                top_alpha = overlay.getpixel((540, 100))[3]
                bottom_alpha = overlay.getpixel((540, 1349))[3]

        self.assertEqual(top_alpha, 0)
        self.assertGreaterEqual(bottom_alpha, 180)

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
        # This asserts a property OF THE BRAND ASSET, not of our code: that
        # scrubbing clears the template's baked-in example headline. The asset
        # is optional (a missing one falls back to a plain gradient, and
        # _build_overlay_png is covered for that path above), so skip rather
        # than fail when it is not present in this checkout.
        template = media_tools.settings.cover_overlay_template
        # None when COVER_OVERLAY_TEMPLATE is unset, which is now the default -
        # the overlay is optional and this checkout has no brand asset.
        if template is None or not template.is_file():
            self.skipTest(f"cover overlay template not present: {template}")
        with Image.open(template) as source:
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

    def test_cover_uses_saved_highlight_text_color_not_accent_shades(self) -> None:
        design = CarouselDesign(
            logo_visible=False,
            cover={
                "text_color": "#fefefe",
                "highlight_text_color": "#123456",
                "accent_color": "#abcdef",
            },
        )
        rendered = media_tools._render_title_block(
            "AGENTS FAIL IN PRODUCTION",
            "IN PRODUCTION",
            design,
        ).convert("RGBA")
        colors = set(rendered.getdata())
        self.assertIn((0x12, 0x34, 0x56, 255), colors)
        self.assertNotIn((0xAB, 0xCD, 0xEF, 255), colors)


if __name__ == "__main__":
    unittest.main()
