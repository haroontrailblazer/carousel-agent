"""Encrypted UI-only Langfuse settings and isolated, optional trace export."""
import asyncio
import base64
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import langfuse_tracing as tracing, observability
from app.services import tracing_config as config, secret_box
from web_api.auth import Identity
from web_api.deps import current_identity
from web_api.routes_tracing_settings import router

PUBLIC = "pk-lf-test-project-0001"
SECRET = "sk-lf-test-secret-0001"
PAYLOAD = {"enabled": True, "base_url": config.DEFAULT_BASE_URL, "public_key": PUBLIC, "secret_key": SECRET}


@pytest.fixture
def api(monkeypatch):
    store = {}
    async def read(key, default=None):
        assert key == config.CONFIG_KEY
        return deepcopy(store)
    async def update(key, merge):
        assert key == config.CONFIG_KEY
        updated = merge(deepcopy(store)); store.clear(); store.update(updated)
        return deepcopy(store)
    monkeypatch.setattr(config.db, "get_config", AsyncMock(side_effect=read))
    monkeypatch.setattr(config.db, "update_config", AsyncMock(side_effect=update))
    monkeypatch.setattr(secret_box, "settings", SimpleNamespace(secrets_key=secret_box.generate_key()))
    monkeypatch.setattr(config, "verify", AsyncMock())
    app = FastAPI(); app.include_router(router, prefix="/api")
    identity = Identity(email="admin@example.test", subject="admin", role="admin")
    app.dependency_overrides[current_identity] = lambda: identity
    return SimpleNamespace(client=TestClient(app), app=app, store=store, identity=identity)


def test_environment_is_ignored_and_keys_are_write_only(api, monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", PUBLIC)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", SECRET)
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://environment.invalid")
    response = api.client.get("/api/settings/tracing")
    assert not response.json()["key_configured"] and not response.json()["enabled"]
    assert response.json()["base_url"] == config.DEFAULT_BASE_URL
    assert response.headers["cache-control"] == "no-store"
    assert observability.init_observability() is False
    response = api.client.post("/api/settings/tracing", json=PAYLOAD)
    assert response.status_code == 200 and response.json()["enabled"]
    assert SECRET not in response.text and PUBLIC not in response.text
    assert SECRET not in json.dumps(api.store) and PUBLIC not in json.dumps(api.store)
    saved = asyncio.run(config.load())
    assert saved.secret_key == SECRET and saved.public_key == PUBLIC
    assert SECRET not in repr(saved) and PUBLIC not in repr(saved)
    assert api.store["secret_key_encrypted"].startswith(secret_box.PREFIX)
    assert config.verify.await_args.args[0] == saved


def test_disable_preserves_keys_and_disconnect_removes_them(api):
    api.client.post("/api/settings/tracing", json=PAYLOAD)
    ciphertext = api.store["secret_key_encrypted"]
    config.verify.reset_mock()
    response = api.client.post("/api/settings/tracing", json={"enabled": False, "base_url": config.DEFAULT_BASE_URL})
    assert response.status_code == 200 and not response.json()["enabled"]
    assert response.json()["key_configured"]
    assert api.store["secret_key_encrypted"] == ciphertext
    config.verify.assert_not_called()
    response = api.client.delete("/api/settings/tracing")
    assert response.status_code == 200 and not response.json()["key_configured"]
    assert not api.store["secret_key_encrypted"] and not api.store["public_key_encrypted"]


@pytest.mark.parametrize("change", [{"base_url": "https://us.cloud.langfuse.com"}, {"public_key": "pk-lf-another-project"}])
def test_changing_destination_requires_both_keys(api, change):
    api.client.post("/api/settings/tracing", json=PAYLOAD)
    before = deepcopy(api.store); config.verify.reset_mock()
    response = api.client.post("/api/settings/tracing", json={"enabled": True, "base_url": config.DEFAULT_BASE_URL} | change)
    assert response.status_code == 422 and api.store == before
    config.verify.assert_not_called()


@pytest.mark.parametrize("payload", [
    PAYLOAD | {"secret_key": [SECRET]}, PAYLOAD | {"base_url": "http://example.com"},
    PAYLOAD | {"base_url": "https://user:password@example.com"},
    PAYLOAD | {"base_url": "https://example.com?secret=value"},
    PAYLOAD | {"secret_key": "bad-" + SECRET}, {"enabled": True, "base_url": config.DEFAULT_BASE_URL},
])
def test_invalid_input_is_rejected_without_echoing_keys(api, payload):
    response = api.client.post("/api/settings/tracing", json=payload)
    assert response.status_code == 422 and api.store == {}
    assert SECRET not in response.text


def test_connection_failure_keeps_existing_settings(api):
    api.client.post("/api/settings/tracing", json=PAYLOAD)
    before = deepcopy(api.store)
    config.verify.side_effect = ValueError("Could not reach Langfuse.")
    response = api.client.post("/api/settings/tracing", json=PAYLOAD | {"secret_key": "sk-lf-replacement-key"})
    assert response.status_code == 422 and api.store == before


def test_auth_and_admin_permissions(api):
    api.app.dependency_overrides[current_identity] = lambda: replace(api.identity, role="reviewer")
    assert not api.client.get("/api/settings/tracing").json()["can_edit"]
    assert api.client.post("/api/settings/tracing", json=PAYLOAD).status_code == 403
    assert api.client.delete("/api/settings/tracing").status_code == 403
    api.app.dependency_overrides.clear()
    assert api.client.get("/api/settings/tracing").status_code == 401


def test_unavailable_encryption_and_storage_errors_never_expose_credentials(api, monkeypatch):
    monkeypatch.setattr(secret_box, "settings", SimpleNamespace(secrets_key=""))
    response = api.client.post("/api/settings/tracing", json=PAYLOAD)
    assert response.status_code == 503 and api.store == {}
    assert SECRET not in response.text
    monkeypatch.setattr(config.db, "get_config", AsyncMock(side_effect=RuntimeError(SECRET)))
    response = api.client.get("/api/settings/tracing")
    assert response.status_code == 503 and SECRET not in response.text
    assert asyncio.run(config.load_for_run()) == config.TracingSettings()


def test_unreadable_keys_do_not_fall_back_to_environment(api, monkeypatch):
    api.store.update(enabled=True, public_key_encrypted="fernet:broken", secret_key_encrypted="fernet:broken")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", SECRET)
    response = api.client.get("/api/settings/tracing")
    assert response.json()["key_error"] and not response.json()["key_configured"]


@pytest.mark.asyncio
async def test_probe_uses_explicit_auth_and_never_follows_redirects(monkeypatch):
    original = httpx.AsyncClient
    seen = []
    def respond(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "https://other.invalid"})
    monkeypatch.setattr(config.httpx, "AsyncClient", lambda **kw: original(**kw, transport=httpx.MockTransport(respond)))
    with pytest.raises(ValueError):
        await config.verify(config.TracingSettings(True, "https://example.test", PUBLIC, SECRET))
    assert len(seen) == 1
    assert seen[0].url == "https://example.test/api/public/projects"
    assert seen[0].headers["authorization"] == "Basic " + base64.b64encode(f"{PUBLIC}:{SECRET}".encode()).decode()


def memory_provider(run_id):
    provider = TracerProvider(shutdown_on_exit=False)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(tracing._RunAttributes(run_id))
    provider.add_span_processor(SimpleSpanProcessor(tracing._MetadataOnlyExporter(exporter)))
    return provider, exporter


@pytest.mark.asyncio
async def test_disabled_or_failed_export_never_disables_local_tokens(monkeypatch):
    create = Mock(side_effect=RuntimeError("export unavailable"))
    monkeypatch.setattr(tracing, "_make_provider", create)
    monkeypatch.setattr(tracing, "_instrument", Mock())
    for enabled in (False, True):
        observability.bind_run("local-tokens")
        async with tracing.run_tracing(config.TracingSettings(enabled, config.DEFAULT_BASE_URL, PUBLIC, SECRET), "local-tokens"):
            observability.record_image_usage("image-model", "images.generate", SimpleNamespace(input_tokens=7, output_tokens=11, total_tokens=18))
        assert observability.pop_image_usage("local-tokens")["image_total_tokens"] == 18
    assert create.call_count == 1


@pytest.mark.asyncio
async def test_concurrent_runs_and_threads_keep_separate_destinations(monkeypatch):
    providers = {}; exports = {}
    def create(settings, run_id):
        providers[run_id], exports[run_id] = memory_provider(run_id)
        return providers[run_id]
    monkeypatch.setattr(tracing, "_make_provider", create)
    monkeypatch.setattr(tracing, "_instrument", Mock())
    proxy = tracing._RunTracerProvider().get_tracer("test-agent")
    async def run(run_id):
        async with tracing.run_tracing(config.TracingSettings(True, config.DEFAULT_BASE_URL, PUBLIC + run_id, SECRET), run_id):
            with proxy.start_as_current_span(run_id):
                await asyncio.sleep(0)
                await asyncio.to_thread(tracing.record_image_span, "image-model", "images.generate", 3, 4, 7)
    await asyncio.gather(run("one"), run("two"))
    for run_id, exporter in exports.items():
        spans = exporter.get_finished_spans()
        assert len(spans) == 2
        assert all(span.attributes["langfuse.session.id"] == run_id for span in spans)
        assert {span.name for span in spans} == {run_id, "images.generate"}
    assert not proxy.start_span("after-run").is_recording()


def test_exporter_uses_only_saved_endpoint_and_keys(monkeypatch):
    from opentelemetry.exporter.otlp.proto.http import trace_exporter
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://ignored.invalid")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "https://ignored.invalid")
    exporter = Mock()
    factory = Mock(return_value=exporter)
    monkeypatch.setattr(trace_exporter, "OTLPSpanExporter", factory)
    provider = tracing._make_provider(config.TracingSettings(True, "https://saved.test", PUBLIC, SECRET), "run")
    assert factory.call_args.kwargs["endpoint"] == "https://saved.test/api/public/otel/v1/traces"
    assert factory.call_args.kwargs["headers"]["Authorization"] == "Basic " + base64.b64encode(f"{PUBLIC}:{SECRET}".encode()).decode()
    provider.shutdown()


@pytest.mark.asyncio
async def test_real_adk_model_exports_tokens_without_prompts(monkeypatch):
    from google.adk.agents import LlmAgent
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    from openinference.instrumentation.google_adk import GoogleADKInstrumentor

    class FakeModel(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="private response")]),
                              usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=13, candidates_token_count=5, total_token_count=18))
    provider, exporter = memory_provider("adk-test")
    monkeypatch.setattr(tracing, "_make_provider", lambda settings, run_id: provider)
    sessions = InMemorySessionService()
    await sessions.create_session(app_name="trace_test", user_id="test", session_id="adk-test")
    runner = Runner(app_name="trace_test", agent=LlmAgent(name="writer", model=FakeModel(model="offline-test")), session_service=sessions)
    try:
        async with tracing.run_tracing(config.TracingSettings(True, config.DEFAULT_BASE_URL, PUBLIC, SECRET), "adk-test"):
            events = [event async for event in runner.run_async(user_id="test", session_id="adk-test", new_message=types.Content(role="user", parts=[types.Part(text="private prompt")]))]
        assert any(event.usage_metadata and event.usage_metadata.total_token_count == 18 for event in events)
        spans = exporter.get_finished_spans()
        assert any(span.attributes.get("llm.token_count.total") == 18 for span in spans)
        assert all(span.attributes["langfuse.session.id"] == "adk-test" for span in spans)
        attributes = str([dict(span.attributes) for span in spans])
        assert "private prompt" not in attributes and "private response" not in attributes
    finally:
        GoogleADKInstrumentor().uninstrument()
        tracing._instrumented = False
        await runner.close()
