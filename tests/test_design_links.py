"""Per-design CTA destinations survive persistence and cannot cross designs."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agents import cta
from app.schemas import CarouselDesign
from app.state import K_DESIGN
from web_api.auth import Identity
from web_api.deps import current_identity
from web_api.routes_designs import router
from web_api import routes_runs
from app.runs.service import StartedRun


@pytest.mark.parametrize("field", ["substack_url", "youtube_url"])
@pytest.mark.parametrize("value", ["javascript:alert(1)", "ftp://example.com", "not a url", "https://name:password@example.com", "https://example.com/a\nb"])
def test_invalid_destination_is_rejected(field, value):
    with pytest.raises(ValidationError):
        CarouselDesign(**{field:value})


def test_design_links_survive_api_save_and_reload():
    app = FastAPI()
    app.include_router(router,prefix='/api')
    identity = Identity(email='designer@example.com',subject='designer@example.com')
    app.dependency_overrides[current_identity] = lambda: identity
    saved = []
    async def replace(owner, items):
        assert owner == identity.email
        saved[:] = items
    async def listed(owner):
        assert owner == identity.email
        return saved
    with patch('web_api.routes_designs.db.replace_carousel_designs',replace), patch('web_api.routes_designs.db.list_carousel_designs',listed):
        client = TestClient(app)
        first = CarouselDesign(id='one',substack_url=' https://first.substack.com ',youtube_url='https://youtube.com/@first').model_dump(mode='json')
        second = CarouselDesign(id='two',youtube_url='https://youtube.com/@second').model_dump(mode='json')
        assert client.put('/api/designs',json={'items':[first,second]}).status_code==200
        reloaded = client.get('/api/designs').json()['items']
        assert reloaded[0]['substack_url']=='https://first.substack.com/'
        assert reloaded[1]['substack_url']==''
        assert reloaded[1]['youtube_url']=='https://youtube.com/@second'
        first['substack_url']=''
        assert client.put('/api/designs',json={'items':[first,second]}).status_code==200
        assert client.get('/api/designs').json()['items'][0]['substack_url']==''


def test_redirect_uses_only_selected_design_links():
    first = CarouselDesign(substack_url='https://first.substack.com',youtube_url='https://youtube.com/@first')
    second = CarouselDesign(youtube_url='https://youtube.com/@second')
    assert cta._resolve_link('redirect','substack',first)==('https://first.substack.com/','first.substack.com')
    assert cta._resolve_link('redirect','youtube',first)[0]=='https://youtube.com/@first'
    assert cta._resolve_link('redirect','substack',second)[0]=='https://youtube.com/@second'
    assert cta._resolve_link('follow','youtube',CarouselDesign(handle_text='brand'))==('@brand','@brand')


@pytest.mark.asyncio
async def test_links_are_frozen_in_new_run_contract():
    design = CarouselDesign(id='selected',youtube_url='https://youtube.com/@chosen')
    identity=Identity(email='designer@example.com',subject='designer@example.com')
    with patch.object(routes_runs.db,'upsert_carousel_design',AsyncMock()), patch.object(routes_runs,'start_run',AsyncMock(return_value=StartedRun('run','news','Topic'))) as started:
        await routes_runs.create_run(routes_runs.StartRunRequest(source='topic',topic='A useful topic',design_id=design.id,design=design),identity)
    assert started.await_args.kwargs['design']['youtube_url']=='https://youtube.com/@chosen'


@pytest.mark.asyncio
async def test_empty_legacy_design_does_not_render_a_false_redirect():
    result = await cta.render_cta_slide(cta_type='redirect', headline='READ THE FULL STORY', supporting_lines=[], tool_context=SimpleNamespace(state={K_DESIGN:{}}))
    assert result['status']=='error'
    assert 'follow or comment' in result['message']
