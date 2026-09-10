"""The saved slide limit controls planning, QA and publishing per run."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError
from google.adk.agents.readonly_context import ReadonlyContext

from app.agents import planner, publisher, stitch_verify
from app.design_limits import design_slide_limit
from app.schemas import CarouselDesign
from app.state import K_DESIGN, K_NEWS_ITEM, K_REWORK_FEEDBACK, K_BODY_SLIDES, K_QA_REPORT
from web_api.routes_designs import DesignLibraryRequest
from web_api import routes_runs
from web_api.auth import Identity
from app.runs.service import StartedRun


@pytest.mark.parametrize('value',[2,11,0,-1,4.5,True,'6'])
def test_invalid_limits_are_rejected(value):
    with pytest.raises(ValidationError):
        CarouselDesign(max_slides=value)


def test_legacy_default_and_independent_saved_designs():
    assert design_slide_limit({})==10
    assert design_slide_limit({K_DESIGN:{'id':'legacy'}})==10
    library=DesignLibraryRequest(items=[CarouselDesign(id='short',max_slides=3),CarouselDesign(id='long',max_slides=8)])
    loaded=DesignLibraryRequest.model_validate_json(library.model_dump_json())
    assert [design_slide_limit({K_DESIGN:item}) for item in loaded.items]==[3,8]


@pytest.mark.asyncio
async def test_planner_uses_run_limit_and_preserves_template_context():
    state={K_DESIGN:{'max_slides':5},K_NEWS_ITEM:{'title':'A specific news story'},K_REWORK_FEEDBACK:'Keep the source dates'}
    context=ReadonlyContext(SimpleNamespace(session=SimpleNamespace(state=state)))
    text=await planner._instruction_provider(context)
    assert 'must be <= 5' in text
    assert 'A specific news story' in text
    assert 'Keep the source dates' in text
    assert '{news_item}' not in text
    assert '<= 5 (selected design limit)' in stitch_verify._instruction_provider(context)


@pytest.mark.asyncio
@pytest.mark.parametrize('limit,exceeded',[(3,True),(4,False)])
async def test_qa_flags_design_limit_including_cover_and_cta(limit,exceeded):
    state={K_DESIGN:{'max_slides':limit},K_BODY_SLIDES:[{'index':2,'artifact':'two.png'},{'index':3,'artifact':'three.png'}]}
    with patch.object(stitch_verify,'_existing_artifacts',AsyncMock(return_value=set())), patch.object(stitch_verify,'_verify_rendered_png',AsyncMock(return_value=None)), patch.object(stitch_verify,'_verify_brand_padding',AsyncMock(return_value=None)):
        await stitch_verify.assemble_and_verify(SimpleNamespace(state=state))
    issues=state[K_QA_REPORT]['issues']
    assert any('exceeds the design cap' in issue['message'] for issue in issues)==exceeded


@pytest.mark.asyncio
async def test_publish_stops_before_external_calls_when_design_limit_is_exceeded():
    state={K_DESIGN:{'max_slides':3},'review_verdict':{'status':'approved'},'bundle':{
        'cover':{'video_artifact':'cover.mp4','title':'Title'},'slides':[],
        'cta':{'cta_type':'follow','artifact':'cta.png'},'caption':'Caption',
        'ordered_artifacts':['cover.mp4','two.png','three.png','cta.png'],
    }}
    with patch.object(publisher,'_account_for_run') as account:
        result=await publisher.publish_approved_carousel(SimpleNamespace(state=state))
    assert result['status']=='error' and 'at most 3 slides' in result['message']
    account.assert_not_called()


@pytest.mark.asyncio
async def test_new_run_snapshots_limit():
    design=CarouselDesign(id='selected',max_slides=6)
    identity=Identity(email='designer@example.com',subject='designer@example.com')
    with patch.object(routes_runs.db,'upsert_carousel_design',AsyncMock()) as saved, patch.object(routes_runs,'start_run',AsyncMock(return_value=StartedRun('run','news','Topic'))) as started:
        await routes_runs.create_run(routes_runs.StartRunRequest(source='topic',topic='A useful topic',design=design,design_id=design.id),identity)
    assert saved.await_args.args[1]['max_slides']==6
    assert started.await_args.kwargs['design']['max_slides']==6
