"""Complete Telegram review delivery without sending real messages."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.agents import review_dispatcher as rd
from app.state import K_BUNDLE, K_NEWS_ITEM, K_REVIEW_ROUND, K_RUN_ID
from app.tools import telegram_tools as tg


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["https://example.test/pasted?ref=original", "https://news.example.test/rss-story"])
async def test_dispatch_materializes_video_poster_slides_and_cta_before_pause(monkeypatch, tmp_path, source):
    monkeypatch.setattr(rd, "settings", SimpleNamespace(workdir=tmp_path))
    state = {K_RUN_ID: "run-preview", K_NEWS_ITEM: {"title": "Actual story", "source_url": source}, K_BUNDLE: {
        "cover": {"video_artifact": "cover.mp4", "poster_artifact": "cover.png"},
        "slides": [{"index": 3, "artifact": "slide3.png"}, {"index": 2, "artifact": "slide2.png"}],
        "cta": {"artifact": "cta.png", "cta_type": "follow"}, "caption": "The full caption.",
    }}
    context = SimpleNamespace(state=state, load_artifact=AsyncMock(return_value=SimpleNamespace(inline_data=SimpleNamespace(data=b"preview"))))
    sent = Mock(return_value={"message_id": "123"})
    monkeypatch.setattr(tg, "send_review_message", sent)
    result = await rd.send_review_request(context)
    assert result["status"] == "sent"
    payload = sent.call_args.args[1]
    paths = tg._preview_paths(payload)
    assert [path.name for path in paths] == ["cover.mp4", "cover.png", "slide2.png", "slide3.png", "cta.png"]
    assert payload["source_url"] == source
    assert payload["caption"] == "The full caption."
    assert [payload["preview_labels"][str(path)] for path in paths] == ["Video cover", "Image cover", "Slide 2", "Slide 3", "CTA"]
    assert result["previews_attached"] == 5
    assert state[K_REVIEW_ROUND] == state[rd._SENT_KEY] == 1
    # A later round cannot claim successful delivery if a preview is missing.
    context.load_artifact.return_value = None
    sent.reset_mock()
    result = await rd.send_review_request(context)
    assert result["status"] == "error"
    sent.assert_not_called()
    assert state[K_REVIEW_ROUND] == 1


def test_mixed_album_keeps_video_and_sends_single_leftover_cta(monkeypatch, tmp_path):
    paths = [tmp_path / "cover.mp4"] + [tmp_path / f"slide{i}.png" for i in range(10)]
    for path in paths:
        path.write_bytes(b"media")
    calls = []
    def request(client, method, *, data, files):
        calls.append((method, data, list(files)))
        assert all(file[1].read() == b"media" for file in files.values())
        return {"message_id": 1}
    monkeypatch.setattr(tg, "_request", request)
    monkeypatch.setattr(tg.time, "sleep", lambda _: None)
    assert tg._send_album(None, "123", paths) == 11
    assert [call[0] for call in calls] == ["sendMediaGroup", "sendPhoto"]
    media = json.loads(calls[0][1]["media"])
    assert len(media) == 10 and media[0]["type"] == "video"
    assert media[0]["supports_streaming"] is True
    assert all(item["type"] == "photo" for item in media[1:])


def test_caption_source_and_button_are_preserved_after_all_media(monkeypatch, tmp_path):
    path = tmp_path / "cover.mp4"
    path.write_bytes(b"media")
    calls = []
    def request(client, method, *, data, files=None):
        calls.append((method, data))
        return {"message_id": len(calls)}
    monkeypatch.setattr(tg, "_request", request)
    monkeypatch.setattr(tg.time, "sleep", lambda _: None)
    monkeypatch.setattr(tg, "settings", SimpleNamespace(public_base_url="https://carousell.up.railway.app"))
    caption = "Full caption " + "🚀" * 2200
    source = "https://news.example.test/article?ref=original&x=1"
    tg._send_review_message("run-preview", {"caption": caption, "source_url": source, "preview_paths": [str(path)]}, 1,
                            creds={"bot_token": "local-test-only", "chat_id": "123"})
    assert calls[0][0] == "sendVideo"
    messages = [data for method, data in calls if method == "sendMessage"]
    text = "".join(message["text"] for message in messages)
    assert caption in text and source in text
    assert all(len(message["text"].encode("utf-16-le")) // 2 <= tg.MESSAGE_LIMIT for message in messages)
    assert all("reply_markup" not in message for message in messages[:-1])
    assert json.loads(messages[-1]["reply_markup"])["inline_keyboard"] == [[{
        "text": "Review carousel", "url": "https://carousell.up.railway.app/tasks/run-preview?tab=review",
    }]]


def test_review_data_requires_login_and_the_runs_owner(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import tenancy
    from web_api import auth, routes_runs
    owner = "11111111-1111-4111-8111-111111111111"
    other = "22222222-2222-4222-8222-222222222222"
    secret = "local-review-test-secret-not-used-in-production-0123456789"
    monkeypatch.setattr(auth, "authorize_email", AsyncMock(side_effect=lambda email, subject: auth.Identity(email, subject, "admin")))
    monkeypatch.setattr(routes_runs.instagram_accounts, "load", AsyncMock())
    monkeypatch.setattr(tg.telegram_config, "load", AsyncMock())
    async def get_run(run_id):
        return {"run_id": run_id, "phase": "review"} if tenancy.require() == owner else None
    monkeypatch.setattr(routes_runs.db, "get_run", get_run)
    monkeypatch.setattr(routes_runs, "_session_state", AsyncMock(return_value={}))
    monkeypatch.setattr(routes_runs.db, "load_pending_review", AsyncMock(return_value={}))
    monkeypatch.setattr(routes_runs, "_trace_length", AsyncMock(return_value=0))
    app = FastAPI()
    app.include_router(routes_runs.router, prefix="/api")
    client = TestClient(auth.AuthMiddleware(app, verifier=Mock(), secret=secret))
    assert client.get("/api/runs/run-preview").status_code == 401
    assert client.get("/api/runs/run-preview/artifacts").status_code == 401
    for subject, expected in [(owner, 200), (other, 404)]:
        token = auth.issue_session_token(auth.Identity("person@example.test", subject, "admin"), ttl_s=60, secret=secret)
        response = client.get("/api/runs/run-preview", headers={"Cookie": auth.COOKIE_NAME + "=" + token})
        assert response.status_code == expected
