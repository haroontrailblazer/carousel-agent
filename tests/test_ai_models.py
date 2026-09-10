"""Model discovery uses only the supplied credential and exposes safe metadata."""

import unittest
from unittest.mock import patch

import httpx
from openai import AsyncOpenAI

from app.services import ai_models


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_lists_models_with_explicit_key_and_filters_modalities(self):
        requests = []
        key = "sk-fake-discovery-only"
        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={"object": "list", "data": [
                {"id": name, "object": "model", "created": 1, "owned_by": "openai"}
                for name in ("gpt-5.6-sol", "gpt-5.4-mini", "gpt-image-2", "gpt-image-2", "whisper-1",
                             "text-embedding-3-small", "gpt-realtime", "gpt-4o-audio-preview", "dall-e-3",
                             "gpt-4o-transcribe", "sora-2", "gpt-4o-mini-search-preview", "gpt-5.1-codex",
                             "ft:gpt-4o-mini:org:custom:id", "gpt-6-astra")
            ]})
        sdk = AsyncOpenAI(api_key=key, http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        with patch.object(ai_models, "AsyncOpenAI", return_value=sdk) as constructor:
            catalog = await ai_models.discover(key)
        constructor.assert_called_once_with(api_key=key, timeout=15.0, max_retries=0)
        self.assertEqual(requests[0].headers["authorization"], "Bearer " + key)
        self.assertEqual(requests[0].url.path, "/v1/models")
        self.assertEqual(catalog["image_models"], ["gpt-image-2"])
        self.assertEqual(catalog["text_models"], ["openai/ft:gpt-4o-mini:org:custom:id", "openai/gpt-5.4-mini", "openai/gpt-5.6-sol", "openai/gpt-6-astra"])
        self.assertTrue(sdk.is_closed())

    async def test_provider_errors_do_not_expose_the_key(self):
        key = "sk-private-fake-value"
        for code, expected in ((401, 422), (403, 403), (429, 429), (500, 502)):
            with self.subTest(status=code):
                sdk = AsyncOpenAI(api_key=key, max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda request: httpx.Response(code, json={"error": {"message": "invalid " + key}})
                )))
                with patch.object(ai_models, "AsyncOpenAI", return_value=sdk):
                    with self.assertRaises(ai_models.ModelDiscoveryError) as raised:
                        await ai_models.discover(key)
                self.assertEqual(raised.exception.status, expected)
                self.assertNotIn(key, str(raised.exception))
                self.assertTrue(sdk.is_closed())

    async def test_empty_key_and_empty_list(self):
        with patch.object(ai_models, "AsyncOpenAI") as sdk:
            with self.assertRaises(ai_models.ModelDiscoveryError):
                await ai_models.discover("")
            sdk.assert_not_called()
        self.assertEqual(ai_models.group_models([]), {"text_models": [], "image_models": []})
