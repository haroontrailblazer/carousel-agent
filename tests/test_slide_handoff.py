"""Real ADK copywriter -> zero-argument tool -> PNG handoff, without API calls."""
import json
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from google.adk.agents import SequentialAgent
from google.adk.artifacts import InMemoryArtifactService
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.genai import types
from PIL import Image

from app.agents import phrasing, template_design
from app import state as s
from app.schemas import CarouselDesign
from app.tools import image_gen


class ScriptedModel(BaseLlm):
    """Replace only the paid model; keep ADK state and tool execution real."""
    responses: list[types.Content]

    async def generate_content_async(self, llm_request, stream=False):
        assert self.responses, "The agent unexpectedly requested another model turn"
        yield LlmResponse(content=self.responses.pop(0))


@pytest.mark.asyncio
async def test_copywriter_output_reaches_real_slide_renderer_with_empty_arguments(monkeypatch, tmp_path):
    copy = {"slides": [
        {"index": 2, "lines": ["Faster local tools", "The release supports local processing."]},
        {"index": 3, "lines": ["More control", "Teams choose where their data stays."]},
    ], "caption": "Local tools give teams more control."}
    design = CarouselDesign(
        id="handoff-design", name="Handoff studio", handle_text="@handoff",
        max_slides=4, logo_visible=False,
        inside={"background": "#123456", "image_type": "none", "title_size": 52},
    )
    copy_model = ScriptedModel(model="offline-copy", responses=[types.Content(
        role="model", parts=[types.Part(text=json.dumps(copy))],
    )])
    slide_model = ScriptedModel(model="offline-slides", responses=[
        types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(
            id="render-body", name="render_body_slides", args={},
        ))]),
        types.Content(role="model", parts=[types.Part(text="Rendered both body slides.")]),
    ])
    monkeypatch.setattr(phrasing, "resolve_role_model", lambda role: copy_model)
    monkeypatch.setattr(template_design, "resolve_role_model", lambda role: slide_model)
    monkeypatch.setattr(template_design, "settings", replace(template_design.settings, workdir=tmp_path))
    monkeypatch.setattr(template_design, "_discover_template_ref", lambda section: "")
    paid_images = Mock(side_effect=AssertionError("No paid image generation in this test"))
    monkeypatch.setattr(image_gen, "_call_images_api", paid_images)
    renderer = Mock(wraps=image_gen.generate_slide_image)
    monkeypatch.setattr(image_gen, "generate_slide_image", renderer)

    sessions = InMemorySessionService()
    artifacts = InMemoryArtifactService()
    runner = Runner(
        app_name="handoff_test",
        agent=SequentialAgent(name="handoff", sub_agents=[
            phrasing.build_phrasing_agent(), template_design.build_template_design_agent(),
        ]),
        session_service=sessions, artifact_service=artifacts,
    )
    await sessions.create_session(app_name="handoff_test", user_id="test", session_id="run", state={
        s.K_RUN_ID: "run", s.K_DESIGN: design.model_dump(mode="json"),
        s.K_NEWS_ITEM: {"id": "news", "title": "A new local release"},
        s.K_RESEARCH: {"summary": "The release supports local processing.", "key_facts": []},
        s.K_PLAN: {"style": "points", "slide_count": 4, "hook_title": "A new local release", "slides": [
            {"index": entry["index"], "purpose": "Explain the benefit", "key_points": [entry["lines"][1]]}
            for entry in copy["slides"]
        ]},
    })
    try:
        events = [event async for event in runner.run_async(
            user_id="test", session_id="run",
            new_message=types.Content(role="user", parts=[types.Part(text="Write and render the planned slides.")]),
        )]
        session = await sessions.get_session(app_name="handoff_test", user_id="test", session_id="run")
        assert session.state[s.K_COPY] == copy
        assert [slide["index"] for slide in session.state[s.K_BODY_SLIDES]] == [2, 3]
        assert renderer.call_count == 2
        for position, call in enumerate(renderer.call_args_list):
            assert call.args[1] == copy["slides"][position]["lines"][1:]
            assert call.args[2] == copy["slides"][position]["lines"][0]
            assert call.args[3] == position + 1
            assert "The release supports local processing." in call.args[6]
            assert call.args[8] == design
        for slide in session.state[s.K_BODY_SLIDES]:
            part = await artifacts.load_artifact(app_name="handoff_test", user_id="test", session_id="run", filename=slide["artifact"])
            with Image.open(BytesIO(part.inline_data.data)) as png:
                assert png.size == (1080, 1350)
                assert png.getpixel((0, 0)) == (18, 52, 86)
        tool_result = next(
            part.function_response.response for event in events
            for part in (event.content.parts if event.content else [])
            if part.function_response and part.function_response.name == "render_body_slides"
        )
        assert tool_result["status"] == "ok"
        assert tool_result["design_used"] == {"id": design.id, "name": design.name}
        assert tool_result["slide_indices"] == [2, 3]
        paid_images.assert_not_called()
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_empty_arguments_without_saved_copy_fail_before_rendering(monkeypatch):
    render = Mock()
    monkeypatch.setattr(image_gen, "generate_slide_image", render)
    context = SimpleNamespace(state={}, save_artifact=AsyncMock())
    result = await FunctionTool(template_design.render_body_slides).run_async(args={}, tool_context=context)
    assert result["status"] == "error"
    assert "phrasing agent must run" in result["message"]
    render.assert_not_called()
    context.save_artifact.assert_not_awaited()
