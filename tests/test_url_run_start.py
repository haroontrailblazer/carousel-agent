"""Exercise pasted URLs through the API without launching paid agent work."""

import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.runs import service
from app.schemas import CarouselDesign
from app.services import db
from app.state import K_DESIGN, K_NEWS_ITEM
from web_api.auth import Identity
from web_api.deps import current_identity
from web_api.routes_runs import router


class PastedUrlTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.page = Mock(
            text='<title>Launch &amp; research</title><p>' + 'Useful article details. ' * 20
            + '</p><img src="https://example.test/cover.jpg">',
            url='https://example.test/news/story',
        )
        self.fetch = self.stack.enter_context(patch('requests.get', return_value=self.page))
        self.session = AsyncMock()
        self.stack.enter_context(patch('app.agent.build_configured_runner', AsyncMock(
            return_value=SimpleNamespace(session_service=SimpleNamespace(create_session=self.session)))))
        self.spawn = self.stack.enter_context(patch.object(service, 'spawn_run'))
        self.stack.enter_context(patch.object(service, '_check_limits', AsyncMock()))
        self.stack.enter_context(patch.object(service.instagram_accounts, 'resolve', return_value=None))
        for name in ('upsert_carousel_design', 'create_run', 'set_run_meta', 'set_run_status'):
            self.stack.enter_context(patch.object(db, name, AsyncMock()))
        self.stack.enter_context(patch.object(db, 'claim_news_by_url_hash', AsyncMock(return_value=None)))
        app = FastAPI()
        app.include_router(router, prefix='/api')
        app.dependency_overrides[current_identity] = lambda: Identity(
            email='reader@example.test', subject='reader@example.test')
        self.client = self.stack.enter_context(TestClient(app, raise_server_exceptions=False))
        self.design = CarouselDesign(id='selected-design', name='My design').model_dump(mode='json')

    def submit(self):
        return self.client.post('/api/runs', json={
            'source': 'url', 'url': 'https://example.test/story', 'design': self.design,
        })

    def test_article_starts_with_selected_design_text_and_media(self):
        response = self.submit()
        self.assertEqual(response.status_code, 202, response.text)
        state = self.session.await_args.kwargs['state']
        self.assertEqual(state[K_DESIGN], self.design)
        self.assertEqual(state[K_NEWS_ITEM]['title'], 'Launch & research')
        self.assertIn('Useful article details.', state[K_NEWS_ITEM]['body'])
        self.assertEqual(state[K_NEWS_ITEM]['media_urls'], ['https://example.test/cover.jpg'])
        self.spawn.assert_called_once()

    def test_article_without_media_still_starts(self):
        self.page.text = '<p>' + 'Readable article. ' * 30 + '</p>'
        self.assertEqual(self.submit().status_code, 202)
        self.assertEqual(self.session.await_args.kwargs['state'][K_NEWS_ITEM]['media_urls'], [])

    def test_media_resolves_against_redirect_and_filters_duplicates_and_tracking(self):
        self.page.text += '''
            <img src="../cover.jpg?size=large&amp;format=webp">
            <img src="https://example.test/cover.jpg">
            <img src="/tracking.gif"><img src="data:image/png;base64,abc">
            <a href="/clip.mp4">Video</a><a href="/about">About</a>
        '''
        response = self.submit()
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(self.session.await_args.kwargs['state'][K_NEWS_ITEM]['media_urls'], [
            'https://example.test/cover.jpg',
            'https://example.test/cover.jpg?size=large&format=webp',
            'https://example.test/clip.mp4',
        ])

    def test_media_payload_is_capped(self):
        self.page.text += ''.join(f'<img src="/{index}.jpg">' for index in range(20))
        self.assertEqual(self.submit().status_code, 202)
        self.assertEqual(len(self.session.await_args.kwargs['state'][K_NEWS_ITEM]['media_urls']), 8)

    def test_unreadable_page_returns_actionable_error_without_starting(self):
        self.page.text = '<div id="root"></div><script>loadApplication()</script>'
        response = self.submit()
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()['detail']['code'], 'url_has_no_text')
        self.session.assert_not_awaited()
        self.spawn.assert_not_called()

    def test_unreachable_page_returns_actionable_error_without_starting(self):
        self.fetch.side_effect = requests.Timeout('Timed out')
        response = self.submit()
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()['detail']['code'], 'url_unreachable')
        self.session.assert_not_awaited()
        self.spawn.assert_not_called()
