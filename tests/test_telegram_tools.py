"""Telegram review-channel tools.

Covers the parts that can be wrong without anyone noticing until a review is
sitting unsent: preview resolution, the album cap, the Approve/Reject links,
and the error envelope Telegram returns with a 200-looking body.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app.tools import telegram_tools as tg


def _settings(token: str = "bot-token", chat: str = "12345"):
    """Stand-in for the frozen Settings singleton (it cannot be mutated)."""
    return SimpleNamespace(
        telegram_bot_token=token,
        telegram_chat_id=chat,
        review_api_base_url="https://review.example/",
    )


class ConfigGuardTests(unittest.TestCase):
    def test_missing_token_names_botfather(self) -> None:
        with patch.object(tg, "settings", _settings(token="")):
            with self.assertRaises(RuntimeError) as ctx:
                tg._api_base()
        self.assertIn("BotFather", str(ctx.exception))

    def test_missing_chat_id_explains_how_to_find_it(self) -> None:
        with patch.object(tg, "settings", _settings(chat="")):
            with self.assertRaises(RuntimeError) as ctx:
                tg._chat_id()
        self.assertIn("getUpdates", str(ctx.exception))

    def test_api_base_embeds_the_token(self) -> None:
        with patch.object(tg, "settings", _settings()):
            self.assertEqual(tg._api_base(), "https://api.telegram.org/botbot-token")


class ReviewUrlTests(unittest.TestCase):
    def test_links_match_the_review_api_routes(self) -> None:
        with patch.object(tg, "settings", _settings()):
            approve, reject = tg._review_urls("run-abc")
        # Trailing slash on the base must not produce a double slash.
        self.assertEqual(approve, "https://review.example/review/run-abc/approve")
        self.assertEqual(reject, "https://review.example/review/run-abc/reject")


class PreviewPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _make(self, name: str) -> str:
        path = self.root / name
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return str(path)

    def test_mapping_shape_puts_poster_first(self) -> None:
        poster, s1, s2 = self._make("p.png"), self._make("s1.png"), self._make("s2.png")
        paths = tg._preview_paths(
            {"preview_paths": {"poster": poster, "slides": [s1, s2]}}
        )
        self.assertEqual([p.name for p in paths], ["p.png", "s1.png", "s2.png"])

    def test_list_shape_is_accepted(self) -> None:
        paths = tg._preview_paths(
            {"preview_paths": [self._make("a.png"), self._make("b.png")]}
        )
        self.assertEqual([p.name for p in paths], ["a.png", "b.png"])

    def test_cover_is_an_accepted_alias_for_poster(self) -> None:
        paths = tg._preview_paths({"preview_paths": {"cover": self._make("c.png")}})
        self.assertEqual([p.name for p in paths], ["c.png"])

    def test_missing_files_are_skipped_not_fatal(self) -> None:
        """A half-materialized preview should still send what exists."""
        paths = tg._preview_paths(
            {
                "preview_paths": {
                    "poster": str(self.root / "gone.png"),
                    "slides": [self._make("s1.png")],
                }
            }
        )
        self.assertEqual([p.name for p in paths], ["s1.png"])

    def test_no_previews_is_empty_not_an_error(self) -> None:
        self.assertEqual(tg._preview_paths({}), [])


class RequestEnvelopeTests(unittest.TestCase):
    """Telegram reports failure in the body, so the body must be checked."""

    def _client(self, handler) -> httpx.Client:
        return httpx.Client(
            base_url="https://api.telegram.org/botX",
            transport=httpx.MockTransport(handler),
        )

    def test_ok_false_raises_with_the_description(self) -> None:
        def handler(_request):
            return httpx.Response(
                400,
                json={"ok": False, "description": "chat not found", "error_code": 400},
            )

        with self._client(handler) as client:
            with self.assertRaises(RuntimeError) as ctx:
                tg._request(client, "sendMessage", data={})
        self.assertIn("chat not found", str(ctx.exception))
        self.assertIn("error_code=400", str(ctx.exception))

    def test_non_json_body_raises(self) -> None:
        def handler(_request):
            return httpx.Response(200, text="<html>gateway</html>")

        with self._client(handler) as client:
            with self.assertRaises(RuntimeError) as ctx:
                tg._request(client, "sendMessage", data={})
        self.assertIn("non-JSON", str(ctx.exception))

    def test_success_returns_the_result_object(self) -> None:
        def handler(_request):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

        with self._client(handler) as client:
            result = tg._request(client, "sendMessage", data={})
        self.assertEqual(result["message_id"], 42)


class SendReviewMessageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.calls: list[tuple[str, dict]] = []

    def _previews(self, count: int) -> list[str]:
        made = []
        for i in range(count):
            path = self.root / f"s{i}.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n")
            made.append(str(path))
        return made

    def _run(self, bundle: dict, round_no: int = 1) -> dict:
        def handler(request: httpx.Request) -> httpx.Response:
            method = request.url.path.rsplit("/", 1)[-1]
            self.calls.append((method, {"content_len": len(request.content)}))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

        transport = httpx.MockTransport(handler)
        real_client = httpx.Client

        def fake_client(*_args, **kwargs):
            return real_client(
                base_url=kwargs.get("base_url", ""), transport=transport
            )

        with patch.object(tg, "settings", _settings()), patch.object(
            tg.httpx, "Client", fake_client
        ):
            return tg.send_review_message("run-abc", bundle, round_no)

    def test_album_then_message(self) -> None:
        bundle = {
            "news_title": "A headline",
            "caption": "some caption",
            "preview_paths": self._previews(3),
        }
        result = self._run(bundle)
        self.assertEqual([m for m, _ in self.calls], ["sendMediaGroup", "sendMessage"])
        self.assertEqual(result["previews_sent"], 3)
        self.assertEqual(result["message_id"], "7")

    def test_album_is_capped_at_the_telegram_limit(self) -> None:
        """A full carousel exceeds the cap; it must trim, not fail."""
        bundle = {"news_title": "T", "preview_paths": self._previews(14)}
        result = self._run(bundle)
        self.assertEqual(result["previews_sent"], tg.MEDIA_GROUP_LIMIT)

    def test_no_previews_still_sends_the_decision_message(self) -> None:
        """Previews are a convenience; the Approve/Reject links are the point."""
        result = self._run({"news_title": "T", "preview_paths": []})
        self.assertEqual([m for m, _ in self.calls], ["sendMessage"])
        self.assertEqual(result["previews_sent"], 0)

    def test_title_falls_back_to_the_cover_when_news_title_is_absent(self) -> None:
        body = self._capture_message({"cover": {"title": "Cover title"}}, round_no=2)
        self.assertIn("Cover title", body["text"][0])
        self.assertIn("round 2", body["text"][0])

    def test_keyboard_carries_both_review_links(self) -> None:
        body = self._capture_message({"news_title": "T"})
        keyboard = json.loads(body["reply_markup"][0])
        urls = [row[0]["url"] for row in keyboard["inline_keyboard"]]
        self.assertEqual(
            urls,
            [
                "https://review.example/review/run-abc/approve",
                "https://review.example/review/run-abc/reject",
            ],
        )

    def _capture_message(self, bundle: dict, round_no: int = 1) -> dict:
        """Run a send and return the parsed form body of the sendMessage call."""
        from urllib.parse import parse_qs

        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("sendMessage"):
                captured.update(parse_qs(request.content.decode()))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

        transport = httpx.MockTransport(handler)
        real_client = httpx.Client
        with patch.object(tg, "settings", _settings()), patch.object(
            tg.httpx,
            "Client",
            lambda *a, **k: real_client(
                base_url="https://api.telegram.org/botX", transport=transport
            ),
        ):
            tg.send_review_message("run-abc", bundle, round_no)
        return captured


class ButtonFallbackTests(unittest.TestCase):
    """Telegram refuses non-public URLs in buttons; that must not fail the send.

    Discovered the hard way: with REVIEW_API_BASE_URL=http://localhost:8080 the
    API answers "Bad Request: ... Wrong HTTP URL" and the whole review message
    fails - which would leave a paused run with nobody notified.
    """

    def test_classifies_local_and_public_urls(self) -> None:
        for url in (
            "http://localhost:8080/x",
            "http://127.0.0.1:8080/x",
            "http://[::1]:8080/x",
            "ftp://example.com/x",
            "",
        ):
            self.assertFalse(tg._buttons_supported(url), url)
        for url in ("https://abc.ngrok-free.app/x", "https://review.example/x"):
            self.assertTrue(tg._buttons_supported(url), url)

    def _send_with_base(self, base_url: str) -> dict:
        from urllib.parse import parse_qs

        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("sendMessage"):
                captured.update(parse_qs(request.content.decode()))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

        transport = httpx.MockTransport(handler)
        real_client = httpx.Client
        stub = SimpleNamespace(
            telegram_bot_token="t", telegram_chat_id="1", review_api_base_url=base_url
        )
        with patch.object(tg, "settings", stub), patch.object(
            tg.httpx,
            "Client",
            lambda *a, **k: real_client(
                base_url="https://api.telegram.org/botX", transport=transport
            ),
        ):
            tg.send_review_message("run-abc", {"news_title": "T"}, 1)
        return captured

    def test_local_base_url_sends_links_in_the_body_not_buttons(self) -> None:
        body = self._send_with_base("http://localhost:8080")
        self.assertNotIn("reply_markup", body)
        text = body["text"][0]
        self.assertIn("http://localhost:8080/review/run-abc/approve", text)
        self.assertIn("http://localhost:8080/review/run-abc/reject", text)

    def test_public_base_url_still_uses_buttons(self) -> None:
        body = self._send_with_base("https://review.example")
        self.assertIn("reply_markup", body)
        keyboard = json.loads(body["reply_markup"][0])
        self.assertEqual(
            [row[0]["text"] for row in keyboard["inline_keyboard"]],
            ["APPROVE", "REJECT"],
        )


if __name__ == "__main__":
    unittest.main()
