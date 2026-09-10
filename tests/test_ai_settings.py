"""Encrypted AI settings, authorization, and consistent settings per invocation."""

import asyncio
import copy
import json
import os
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI

from app.services import ai_config, ai_models, secret_box
from web_api.auth import Identity
from web_api.deps import current_identity
from web_api.routes_ai_settings import router

KEY = "sk-project-fake-key-for-tests-only"
ENV_KEY = "sk-environment-fake-key-for-tests-only"
MODELS = {
    "planner_model": "openai/gpt-5.6-sol",
    "utility_model": "openai/gpt-5.4-mini",
    "phrasing_model": "openai/gpt-5.6-sol",
    "image_model": "gpt-image-2",
}


class AISettingsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.row = {}
        self.identity = Identity(email="admin@example.com", subject="admin", role="admin")
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api")
        self.app.dependency_overrides[current_identity] = lambda: self.identity

        async def read(key, default=None):
            self.assertEqual(key, "ai")
            return copy.deepcopy(self.row)

        async def update(key, merge):
            self.assertEqual(key, "ai")
            self.row = merge(copy.deepcopy(self.row))
            return copy.deepcopy(self.row)

        for patcher in (
            patch.object(ai_config, "_cache", None),
            patch.object(secret_box, "settings", SimpleNamespace(secrets_key=secret_box.generate_key())),
            patch.object(ai_config.db, "get_config", AsyncMock(side_effect=read)),
            patch.object(ai_config.db, "update_config", AsyncMock(side_effect=update)),
            patch.object(ai_models, "discover", AsyncMock(return_value={
                "text_models": sorted(set(MODELS[name] for name in MODELS if name != "image_model")),
                "image_models": [MODELS["image_model"]],
            })),
            patch.dict(os.environ, {"OPENAI_API_KEY": ENV_KEY}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def request(self, method="GET", payload=None, path="/api/settings/ai"):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test") as client:
            return await client.request(method, path, json=payload)

    async def test_environment_key_is_ignored_and_never_returned(self):
        response = await self.request()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()["key_source"], "unset")
        self.assertFalse(response.json()["key_configured"])
        self.assertNotIn(ENV_KEY, response.text)
        self.assertNotIn(ENV_KEY, repr(ai_config.current()))

    async def test_environment_models_are_ignored(self):
        with patch.dict(os.environ, {name.upper(): "ignored-environment-model" for name in MODELS}):
            response = await self.request()
            for name, expected in MODELS.items():
                self.assertEqual(response.json()[name], expected)
            await ai_config.save(models=MODELS | {"image_model": "saved-image"}, api_key=KEY)
            self.assertEqual((await ai_config.load()).image_model, "saved-image")

    async def test_missing_saved_key_blocks_all_sdk_calls_even_with_environment_key(self):
        from app import agent
        from app.llm import resolve_role_model
        from google.adk.models.lite_llm import LiteLlm

        await ai_config.load()
        with patch.object(agent, "build_runner") as build:
            with self.assertRaisesRegex(ai_config.AISettingsNotConfigured, "Save your OpenAI API key"):
                await agent.build_configured_runner()
            build.assert_not_called()
        model = resolve_role_model("planner")
        with patch.object(LiteLlm, "generate_content_async") as invoke:
            with self.assertRaises(ai_config.AISettingsNotConfigured):
                async for _ in model.generate_content_async(None):
                    pass
            invoke.assert_not_called()
        with patch("openai.OpenAI") as sdk:
            with self.assertRaises(ai_config.AISettingsNotConfigured):
                _ = ai_config.current().client
            sdk.assert_not_called()

    async def test_saved_key_is_forwarded_to_the_text_sdk(self):
        from app.llm import resolve_role_model
        from google.adk.models.lite_llm import LiteLlm

        await ai_config.save(models=MODELS, api_key=KEY)
        model = resolve_role_model("planner")
        async def respond(instance, request, stream=False):
            self.assertEqual(instance._additional_args["api_key"], KEY)
            yield "response"
        with patch.object(LiteLlm, "generate_content_async", respond):
            self.assertEqual([event async for event in model.generate_content_async(None)], ["response"])

    async def test_save_encrypts_key_and_preserves_environment(self):
        response = await self.request("POST", MODELS | {"api_key": KEY})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.row["api_key_encrypted"].startswith(secret_box.PREFIX))
        self.assertNotIn(KEY, json.dumps(self.row))
        self.assertNotIn(KEY, response.text)
        self.assertEqual(secret_box.decrypt(self.row["api_key_encrypted"]), KEY)
        self.assertEqual(os.environ["OPENAI_API_KEY"], ENV_KEY)
        self.assertEqual(response.json()["key_source"], "saved")

    async def test_blank_key_preserves_saved_key_and_reload_persists_models(self):
        await self.request("POST", MODELS | {"api_key": KEY})
        encrypted = self.row["api_key_encrypted"]
        self.row["future_setting"] = "keep"
        response = await self.request("POST", MODELS | {"api_key": "  ", "utility_model": "openai/gpt-5.6-sol"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.row["api_key_encrypted"], encrypted)
        self.assertEqual(self.row["future_setting"], "keep")
        ai_config._cache = None
        reloaded = await ai_config.load()
        self.assertEqual(reloaded.api_key, KEY)
        self.assertEqual(reloaded.utility_model, "openai/gpt-5.6-sol")

    async def test_missing_encryption_refuses_key_and_models_require_saved_key(self):
        with patch.object(secret_box, "settings", SimpleNamespace(secrets_key="")):
            response = await self.request("POST", MODELS | {"api_key": KEY})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(KEY, response.text)
            self.assertEqual(self.row, {})
            response = await self.request("POST", MODELS)
            self.assertEqual(response.status_code, 409)

    async def test_model_list_uses_saved_key_without_returning_it(self):
        await ai_config.save(models=MODELS, api_key=KEY)
        response = await self.request(path="/api/settings/ai/models")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        ai_models.discover.assert_awaited_once_with(KEY)
        self.assertNotIn(KEY, response.text)
        self.assertIn(MODELS["planner_model"], response.json()["text_models"])

    async def test_preview_uses_draft_key_without_saving_it(self):
        response = await self.request("POST", {"api_key": KEY}, "/api/settings/ai/models")
        self.assertEqual(response.status_code, 200)
        ai_models.discover.assert_awaited_once_with(KEY)
        self.assertEqual(self.row, {})
        self.assertNotIn(KEY, response.text)
        self.assertEqual(os.environ["OPENAI_API_KEY"], ENV_KEY)

    async def test_model_list_without_saved_key_never_uses_environment(self):
        response = await self.request(path="/api/settings/ai/models")
        self.assertEqual(response.status_code, 409)
        ai_models.discover.assert_not_awaited()

    async def test_unavailable_model_is_not_saved(self):
        response = await self.request("POST", MODELS | {"api_key": KEY, "planner_model": "openai/not-accessible"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.row, {})

    async def test_discovery_failure_does_not_replace_saved_key(self):
        await ai_config.save(models=MODELS, api_key=KEY)
        before = copy.deepcopy(self.row)
        ai_models.discover.side_effect = ai_models.ModelDiscoveryError("Cannot load models", 502)
        response = await self.request("POST", MODELS | {"api_key": "sk-replacement-fake-key"})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.row, before)

    async def test_preview_validation_and_authorization(self):
        response = await self.request("POST", {"api_key": [KEY]}, "/api/settings/ai/models")
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(KEY, response.text)
        self.identity = replace(self.identity, role="reviewer")
        self.assertEqual((await self.request("POST", {"api_key": KEY}, "/api/settings/ai/models")).status_code, 403)
        self.app.dependency_overrides.clear()
        self.assertEqual((await self.request(path="/api/settings/ai/models")).status_code, 401)

    async def test_bad_ciphertext_does_not_fall_back_to_environment(self):
        self.row = MODELS | {"api_key_encrypted": "fernet:broken"}
        response = await self.request()
        self.assertTrue(response.json()["key_error"])
        self.assertFalse(response.json()["key_configured"])
        with self.assertRaisesRegex(RuntimeError, "cannot be decrypted"):
            _ = ai_config.current().client

    async def test_validation_never_echoes_submitted_secret(self):
        for payload in (
            MODELS | {"api_key": [KEY]},
            {"api_key": KEY},
            MODELS | {"api_key": KEY, "planner_model": "https://example.com"},
            MODELS | {"api_key": KEY, "image_model": "openai/gpt-image-2"},
            MODELS | {"api_key": "invalid-" + KEY},
        ):
            with self.subTest(payload_type=type(payload["api_key"]).__name__):
                response = await self.request("POST", payload)
                self.assertEqual(response.status_code, 422)
                self.assertNotIn(KEY, response.text)
                self.assertEqual(self.row, {})

    async def test_reviewer_cannot_change_shared_settings(self):
        self.identity = replace(self.identity, role="reviewer")
        self.assertFalse((await self.request()).json()["can_edit"])
        response = await self.request("POST", MODELS | {"api_key": KEY})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.row, {})

    async def test_anonymous_access_is_refused(self):
        self.app.dependency_overrides.clear()
        self.assertEqual((await self.request()).status_code, 401)
        self.assertEqual((await self.request("POST", MODELS)).status_code, 401)

    async def test_storage_failure_is_generic_and_does_not_overwrite_cache(self):
        before = await ai_config.load()
        with patch.object(ai_config.db, "update_config", AsyncMock(side_effect=RuntimeError(KEY))):
            response = await self.request("POST", MODELS | {"api_key": KEY})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(KEY, response.text)
        self.assertIs(ai_config.current(), before)

    async def test_run_snapshot_survives_edits_in_async_tasks_and_threads(self):
        from app.llm import resolve_role_model
        from app.tools import image_gen, research_tools

        original = await ai_config.save(models=MODELS, api_key="sk-old-workspace-key-for-tests")
        with ai_config.bind(original):
            await ai_config.save(models=MODELS | {"utility_model": "openai/new-utility"}, api_key=KEY)
            with patch("app.workspace_model.WorkspaceModel") as llm:
                resolve_role_model("planner")
                self.assertEqual(llm.call_args.kwargs["model"], "openai/responses/gpt-5.6-sol")
                self.assertEqual(llm.call_args.kwargs["api_key"], "sk-old-workspace-key-for-tests")
                self.assertEqual(llm.call_args.kwargs["reasoning_effort"], "high")
            self.assertEqual(await asyncio.to_thread(research_tools._search_model_id), "gpt-5.4-mini")
            fake_client = Mock()
            with patch("openai.OpenAI", return_value=fake_client) as constructor:
                image_gen._client()
                research_tools._client()
                constructor.assert_called_once_with(api_key="sk-old-workspace-key-for-tests", max_retries=0)
        self.assertEqual(ai_config.current().api_key, KEY)
        self.assertEqual(research_tools._search_model_id(), "new-utility")

    async def test_runner_binds_snapshot_and_cleans_up_after_failure(self):
        from app.agent import ConfiguredRunner, Runner
        from google.adk.agents import BaseAgent
        from google.adk.sessions import InMemorySessionService

        snapshot = await ai_config.save(models=MODELS, api_key=KEY)
        runner = ConfiguredRunner(app_name="app", agent=BaseAgent(name="test"), session_service=InMemorySessionService())
        runner.ai_settings = snapshot
        await ai_config.save(models=MODELS | {"image_model": "new-image"}, api_key=KEY)
        async def drive(_runner, **kwargs):
            self.assertIs(ai_config.current(), snapshot)
            yield "event"
            raise RuntimeError("test failure")

        with patch.object(Runner, "run_async", drive):
            with self.assertRaisesRegex(RuntimeError, "test failure"):
                async for _ in runner.run_async():
                    pass
        self.assertEqual(ai_config.current().image_model, "new-image")

    async def test_configured_builder_refreshes_storage_before_building(self):
        from app import agent
        self.row = MODELS | {"planner_model": "openai/new-planner", "api_key_encrypted": secret_box.encrypt(KEY)}
        def build():
            self.assertEqual(ai_config.current().planner_model, "openai/new-planner")
            return "runner"
        with patch.object(agent, "build_runner", side_effect=build):
            self.assertEqual(await agent.build_configured_runner(), "runner")

    async def test_runner_builds_every_agent_from_saved_roles_without_serializing_key(self):
        from app import agent
        from google.adk.sessions import InMemorySessionService

        await ai_config.save(models=MODELS, api_key=KEY)
        with patch.object(agent.runtime, "session_service", return_value=InMemorySessionService()), \
             patch.object(agent.runtime, "artifact_service", return_value=None), \
             patch.object(agent.runtime, "memory_service", return_value=None):
            runner = agent.build_runner()
        self.assertIsNot(runner.ai_settings, ai_config.current())
        self.assertEqual(len(runner.agent.sub_agents), 11)
        for child in runner.agent.sub_agents:
            expected = "openai/responses/gpt-5.6-sol" if child.name in ("research", "planner", "phrasing") else "openai/gpt-5.4-mini"
            self.assertEqual(child.model.model, expected, child.name)
            self.assertEqual(child.model._additional_args["api_key"], KEY)
            self.assertNotIn(KEY, child.model.model_dump_json())
