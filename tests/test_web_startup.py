"""Exercise the deployed ASGI lifespan with the current Settings schema."""
import ast
from dataclasses import fields, replace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import PROJECT_ROOT, Settings, settings
from app.design_limits import MAX_SUPPORTED_SLIDES
from web_api.auth import Identity
from web_api.deps import current_identity


@pytest.mark.parametrize(
    "url,key,configured",
    [
        ("https://startup-test.supabase.co", "server-test-key", True),
        ("", "", False),
        ("https://startup-test.supabase.co", "", False),
        ("", "server-test-key", False),
    ],
    ids=["supabase-https", "no-database", "missing-server-key", "missing-url"],
)
def test_deployed_app_starts_and_shuts_down(monkeypatch, url, key, configured):
    # Keep the real Settings class: a permissive Mock would hide deleted fields.
    # Suppress tracing before importing the agent tree to avoid external traffic.
    monkeypatch.setattr("app.observability.init_observability", Mock())
    import web_app

    monkeypatch.setattr(web_app, "settings", replace(
        settings, supabase_url=url, supabase_storage_key=key,
        session_secret="startup-test-session-secret-48-characters-long-enough",
        auth_bootstrap_emails=("startup@example.test",),
    ))
    monkeypatch.setattr(web_app, "init_observability", Mock())
    monkeypatch.setattr(web_app, "shutdown_observability", Mock())
    calls = Mock()
    operations = [
        (web_app, "reconcile_on_startup", "reconcile", []),
        (web_app, "release_stuck_queue_items", "release", 0),
        (web_app.telegram_config, "load", "telegram", {}),
        (web_app.instagram_accounts, "load", "instagram", []),
        (web_app.db, "seed_app_users", "seed", 0),
        (web_app, "start_scheduler", "start_scheduler", object()),
        (web_app, "drain_resume_tasks", "drain_resume", None),
        (web_app, "drain_run_tasks", "drain_runs", None),
        (web_app.runtime, "close_services", "close_services", None),
        (web_app.db, "close_pool", "close_db", None),
    ]
    for target, attribute, name, result in operations:
        operation = AsyncMock(return_value=result)
        calls.attach_mock(operation, name)
        monkeypatch.setattr(target, attribute, operation)
    stop_scheduler = Mock()
    calls.attach_mock(stop_scheduler, "stop_scheduler")
    monkeypatch.setattr(web_app, "shutdown_scheduler", stop_scheduler)

    # Entering TestClient is essential: requests alone do not run startup.
    with TestClient(web_app.build_app()) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        expected_boot = ["reconcile", "release", "telegram", "instagram", "seed", "start_scheduler"] if configured else []
        assert [call[0] for call in calls.mock_calls] == expected_boot
        if configured:
            calls.seed.assert_awaited_once_with(["startup@example.test"])

    expected_shutdown = (["stop_scheduler"] if configured else []) + [
        "drain_resume", "drain_runs", "close_services", "close_db",
    ]
    assert [call[0] for call in calls.mock_calls] == expected_boot + expected_shutdown
    web_app.shutdown_observability.assert_called_once_with()


def test_metadata_uses_supported_slide_limit_without_legacy_env_setting(monkeypatch):
    from web_api import routes_runs

    monkeypatch.setattr(routes_runs.instagram_accounts, "configured", lambda: False)
    monkeypatch.setattr(routes_runs.instagram_accounts, "listing", lambda: [])
    app = FastAPI()
    app.include_router(routes_runs.router, prefix="/api")
    app.dependency_overrides[current_identity] = lambda: Identity(
        email="startup@example.test", subject="startup-test",
    )
    with TestClient(app) as client:
        response = client.get("/api/meta")
    assert response.status_code == 200
    assert response.json()["max_slides"] == MAX_SUPPORTED_SLIDES
    assert response.json()["publish_configured"] is False


def test_runtime_settings_references_match_the_config_schema():
    """Removed configuration must not leave failures in rarely executed paths."""
    known = {field.name for field in fields(Settings)}
    paths = [PROJECT_ROOT / "web_app.py"]
    for directory in ("app", "web_api", "fetcher"):
        paths.extend((PROJECT_ROOT / directory).rglob("*.py"))
    missing = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "settings"
                    and node.attr not in known):
                missing.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}: {node.attr}")
    assert not missing, "Undefined settings: " + ", ".join(missing)
