"""Newsroom images and authenticated, persistent story deletion."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.news_media import feed_thumbnail, news_thumbnail, safe_image_url
from app.services import db
from app.schemas import CarouselDesign
from app.runs.service import StartedRun
from fetcher.fetch_news import _entry_payload
from web_api import routes_runs
from web_api.routes_runs import router
from web_api.deps import current_identity
from web_api.auth import Identity

def client(auth=True):
    app = FastAPI()
    app.include_router(router, prefix="/api")
    if auth:
        app.dependency_overrides[current_identity] = lambda: Identity(email="reader@example.com", subject="reader")
    return TestClient(app)

@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:image/png;base64,foo", "file:///tmp/pic", "https://user:pass@example.com/a.png", "https://[invalid"])
def test_rejects_non_web_or_malformed_thumbnail(url):
    assert safe_image_url(url) == ""

def test_extracts_image_from_atom_summary_and_skips_tracking_pixel():
    entry = {"title": "A story", "link": "https://example.com/news/story",
             "summary": '<img width="1" src="/track.gif"><img src="/images/cover.webp?x=1&amp;y=2">'}
    result = _entry_payload(entry, "Example", ["rss"], "rss")
    assert result["thumbnail_url"] == "https://example.com/images/cover.webp?x=1&y=2"
    assert result["source_url"] == entry["link"]

def test_media_thumbnail_precedes_html_and_video_is_not_an_image():
    assert feed_thumbnail({"media_thumbnail": [{"url": "https://example.com/thumb"}],
                           "summary": '<img src="https://example.com/other.png">'}) == "https://example.com/thumb"
    assert news_thumbnail({"media_urls": ["https://example.com/clip.mp4", "https://example.com/pic.jpg"]}) == "https://example.com/pic.jpg"
    assert news_thumbnail({"media_urls": ["https://youtube.com/watch?v=x"]}) == ""

def test_delete_requires_authentication():
    with patch.object(db, "delete_queued_news", AsyncMock()) as delete:
        assert client(False).delete("/api/queue/story").status_code == 401
        delete.assert_not_awaited()

def test_delete_waits_for_database_success():
    with patch.object(db, "delete_queued_news", AsyncMock(return_value=True)) as delete:
        response = client().delete("/api/queue/story")
    assert response.status_code == 200
    assert response.json() == {"result": "deleted", "news_id": "story"}
    delete.assert_awaited_once_with("story")

def test_claimed_or_missing_story_cannot_be_deleted():
    with patch.object(db, "delete_queued_news", AsyncMock(return_value=False)):
        response = client().delete("/api/queue/claimed")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "queue_item_gone"

def test_database_error_does_not_claim_success():
    with patch.object(db, "delete_queued_news", AsyncMock(side_effect=RuntimeError("offline"))):
        with pytest.raises(RuntimeError):
            client().delete("/api/queue/story")

def test_queue_exposes_existing_media_and_publication_time():
    pool = AsyncMock()
    pool.fetch.return_value = [{"id": "one", "payload": {"title": "Story", "media_urls": ["https://example.com/p.png"], "published_at": "2026-09-10T10:00:00Z"}, "created_at": datetime.now(timezone.utc)}]
    with patch.object(db, "get_pool", AsyncMock(return_value=pool)):
        rows = asyncio.run(db.list_queued_news())
    assert rows[0]["thumbnail_url"] == "https://example.com/p.png"
    assert rows[0]["published_at"] == "2026-09-10T10:00:00Z"

def test_deleted_payloads_are_unavailable_to_reruns():
    pool = AsyncMock()
    pool.fetchrow.return_value = None
    with patch.object(db, "get_pool", AsyncMock(return_value=pool)):
        assert asyncio.run(db.news_payload("deleted")) is None
    assert pool.fetchrow.await_args.args[-1] == db.STATUS_DELETED


def test_newsroom_requires_design_before_claiming_story_or_starting_agents():
    with (
        patch.object(db, "next_queued_news_by_id", AsyncMock()) as claim,
        patch.object(routes_runs, "start_run", AsyncMock()) as start,
    ):
        response = client().post("/api/runs", json={"source": "queue", "news_id": "story"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "design_required"
    claim.assert_not_awaited()
    start.assert_not_awaited()


def test_unavailable_design_does_not_claim_newsroom_story():
    with (
        patch.object(db, "get_carousel_design", AsyncMock(return_value=None)) as lookup,
        patch.object(db, "next_queued_news_by_id", AsyncMock()) as claim,
        patch.object(routes_runs, "start_run", AsyncMock()) as start,
    ):
        response = client().post("/api/runs", json={
            "source": "queue", "news_id": "story", "design_id": "deleted-design",
        })
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "design_not_found"
    lookup.assert_awaited_once_with("reader@example.com", "deleted-design")
    claim.assert_not_awaited()
    start.assert_not_awaited()


@pytest.mark.parametrize("send_contract", [True, False], ids=["picker-contract", "saved-design-id"])
def test_newsroom_freezes_selected_design_branding_cta_and_slide_limit(send_contract):
    design = CarouselDesign(
        id="newsroom-test", name="My newsroom style", max_slides=6,
        handle_text="@testbrand", cta={"background": "#102030"},
    ).model_dump(mode="json")
    story = {"id": "story", "title": "A new story"}
    payload = {"source": "queue", "news_id": "story", "design_id": design["id"]}
    if send_contract:
        payload["design"] = design
    with (
        patch.object(db, "get_carousel_design", AsyncMock(return_value=design)),
        patch.object(db, "upsert_carousel_design", AsyncMock()) as save,
        patch.object(db, "next_queued_news_by_id", AsyncMock(return_value=story)) as claim,
        patch.object(routes_runs, "start_run", AsyncMock(return_value=StartedRun("run-test", "story", "A new story"))) as start,
    ):
        response = client().post("/api/runs", json=payload)
    assert response.status_code == 202
    claim.assert_awaited_once_with("story")
    assert start.await_args.kwargs["news"] == story
    assert start.await_args.kwargs["design"] == design
    if send_contract:
        save.assert_awaited_once_with("reader@example.com", design)
    else:
        save.assert_not_awaited()
