"""Account boundaries across requests, worker threads, caches and media."""
import asyncio
from contextlib import ExitStack
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from app import tenancy
from app.services import ai_config, instagram_accounts, telegram_config, source_config, workspace_rules
from app.services.read_cache import RevisionCache
from app.services.supabase_db import SupabaseDatabase
from app.services.tenant_storage import TenantStorageClient
from app.runs.bus import RunBus, RunEvent
from web_api.auth import AuthMiddleware, Identity, issue_session_token

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"


@pytest.mark.asyncio
async def test_missing_scope_never_sends_database_or_storage_requests():
    sent = Mock()
    client = SupabaseDatabase(url="https://example.test", key="server", transport=httpx.MockTransport(sent))
    try:
        with pytest.raises(PermissionError):
            await client.rpc("carousel_session_list", {"app":"app"})
        sent.assert_not_called()
        with pytest.raises(PermissionError):
            TenantStorageClient(sent).get_object(Bucket="corousel-media", Key="file")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_concurrent_database_requests_carry_only_the_bound_owner():
    requests=[]
    def respond(request):
        requests.append(json.loads(request.content))
        assert request.url.path.endswith("/carousel_tenant_rpc")
        return httpx.Response(200,json={})
    client=SupabaseDatabase(url="https://example.test",key="server",transport=httpx.MockTransport(respond))
    async def invoke(owner):
        with tenancy.bind(owner):
            await asyncio.sleep(0)
            await client.rpc("carousel_session_list",{"app":owner})
            assert await asyncio.to_thread(tenancy.require)==owner
    try:
        await asyncio.gather(invoke(A),invoke(B))
        assert {r["workspace"] for r in requests}=={A,B}
        assert all(r["arguments"]["app"]==r["workspace"] for r in requests)
        assert not tenancy.current()
    finally: await client.close()


def test_storage_uses_one_private_folder_for_every_operation():
    raw=Mock()
    raw.get_paginator.return_value.paginate.return_value=[{"Contents":[{"Key":f"users/{A}/file"},{"Key":f"users/{B}/secret"}]}]
    raw.delete_objects.return_value={"Deleted":[{"Key":f"users/{A}/file"}]}
    client=TenantStorageClient(raw)
    for owner in (A,B):
        with tenancy.bind(owner):
            for method in ("put_object","get_object","head_object","delete_object"):
                getattr(client,method)(Bucket="corousel-media",Key="file")
                assert getattr(raw,method).call_args.kwargs["Key"]==f"users/{owner}/file"
            client.generate_presigned_url("get_object",Params={"Bucket":"corousel-media","Key":"file"})
            assert raw.generate_presigned_url.call_args.kwargs["Params"]["Key"]==f"users/{owner}/file"
    with tenancy.bind(A):
        assert list(client.get_paginator("list_objects_v2").paginate(Bucket="corousel-media"))[0]["Contents"]==[{"Key":"file"}]
        assert client.delete_objects(Bucket="corousel-media",Delete={"Objects":[{"Key":"file"}]})["Deleted"]==[{"Key":"file"}]
        for bad in ("../secret","/file",f"users/{B}/file","a/../b","a\\b"):
            with pytest.raises((PermissionError,ValueError)):
                client.get_object(Bucket="corousel-media",Key=bad)


@pytest.mark.asyncio
async def test_settings_and_revision_caches_never_reuse_another_account(monkeypatch):
    monkeypatch.setattr(ai_config,"_decode",lambda value:SimpleNamespace(api_key=value.get("key")))
    async def config(key,default=None): return {"key":tenancy.require()}
    monkeypatch.setattr(ai_config.db,"get_config",config)
    cache=RevisionCache()
    for owner in (A,B,A):
        with tenancy.bind(owner):
            await ai_config.load()
            assert ai_config.current().api_key==owner
            telegram_config._store_cache({owner:{"bot_token":owner}})
            assert telegram_config.all_credentials()==[{"bot_token":owner}]
            instagram_accounts._cache["same-account"]=owner
            assert instagram_accounts.get("same-account")==owner
            result=await cache.read("same-session",AsyncMock(return_value="same-revision"),AsyncMock(return_value=owner))
            assert result==owner
    with tenancy.bind(B): assert instagram_accounts.get("same-account")==B


@pytest.mark.asyncio
async def test_event_bus_does_not_mix_identical_run_ids():
    bus=RunBus()
    with tenancy.bind(A):
        async with bus.subscribe("same-run") as a:
            with tenancy.bind(B):
                async with bus.subscribe("same-run") as b:
                    await bus.publish(RunEvent(run_id="same-run",seq=1,kind="progress",text="B only"))
                    assert a.empty()
                    assert (await b.get()).text=="B only"


def test_cancel_another_users_run_never_touches_the_worker(monkeypatch):
    from web_api.routes_runs import router
    from web_api.deps import current_identity
    from web_api import routes_runs
    app=FastAPI(); app.include_router(router,prefix="/api")
    app.dependency_overrides[current_identity]=lambda:Identity("b@example.test",B,"admin")
    monkeypatch.setattr(routes_runs.db,"get_run",AsyncMock(return_value=None))
    cancel=AsyncMock(); monkeypatch.setattr(routes_runs,"cancel_run",cancel)
    assert TestClient(app).post("/api/runs/another-users-run/cancel").status_code==404
    cancel.assert_not_called()


def test_middleware_binds_verified_id_and_resets_after_request(monkeypatch):
    from web_api import auth
    monkeypatch.setattr(instagram_accounts,"load",AsyncMock())
    monkeypatch.setattr(telegram_config,"load",AsyncMock())
    async def authorize(email,subject):return Identity(email,subject,"admin")
    monkeypatch.setattr(auth,"authorize_email",authorize)
    app=FastAPI()
    @app.get("/api/scope")
    async def scope():return {"owner":tenancy.require()}
    secret="s"*48
    client=TestClient(AuthMiddleware(app,verifier=Mock(),secret=secret))
    for owner in (A,B):
        token=issue_session_token(Identity("account@example.test",owner,"admin"),ttl_s=60,secret=secret)
        response=client.get("/api/scope",headers={"Cookie":"carousel_session="+token})
        assert response.json()=={"owner":owner}
        assert not tenancy.current()
        mismatch=client.get("/api/scope",headers={"Cookie":"carousel_session="+token,"X-Workspace-ID":B if owner==A else A})
        assert mismatch.status_code==401
        assert mismatch.json()["code"]=="account_changed"


@pytest.mark.asyncio
async def test_learned_rules_are_saved_per_account_without_editing_source(monkeypatch):
    rows={}
    async def update(key,merge):
        owner=tenancy.require(); rows[owner]=merge(rows.get(owner,{})); return rows[owner]
    monkeypatch.setattr(workspace_rules.db,"update_config",update)
    with tenancy.bind(A):
        await workspace_rules.append("agents/writer.md","A private feedback")
        assert "A private feedback" in workspace_rules.content("agents/writer.md","base")
    with tenancy.bind(B):
        assert "A private feedback" not in workspace_rules.content("agents/writer.md","base")
        assert "legacy secret" not in workspace_rules.content("agents/writer.md","base\n## Learned rules\nlegacy secret")


@pytest.mark.asyncio
async def test_news_sources_are_personal_and_validate_destinations(monkeypatch):
    from app.services import source_config
    values={A:{"rss_feeds":["https://example.com/a"],"youtube_channels":[]},B:{"rss_feeds":[],"youtube_channels":[]}}
    async def read(key, default=None): return values[tenancy.require()]
    monkeypatch.setattr(source_config.db,"get_config",read)
    with tenancy.bind(A):
        await source_config.load()
        assert source_config.current()["rss_feeds"]==["https://example.com/a"]
        with tenancy.bind(B):
            assert "https://example.com/a" not in source_config.current()["rss_feeds"]
            await source_config.load()
            assert source_config.current()["rss_feeds"]==[]
    for url in ("http://example.com/feed","https://127.0.0.1/feed","https://localhost/feed","https://user:pass@example.com/feed"):
        with pytest.raises(ValueError):source_config.validate({"rss_feeds":[url]})
    with pytest.raises(ValueError):source_config.validate({"youtube_channels":["https://youtube.com/@unsupported"]})
