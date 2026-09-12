"""The live agent builders must retain the shared voice and output contracts."""
from pathlib import Path

import pytest

from app.agents import cta, phrasing, planner
from app.editorial_voice import WRITING_STANDARD
from app.schemas import CopySet
from app.state import K_COPY


@pytest.mark.parametrize("custom", ["", "Workspace instructions: preserve dates and prices."])
def test_writing_standard_reaches_all_copy_producing_agents(monkeypatch, custom):
    for module in (planner, phrasing, cta):
        monkeypatch.setattr(module, "agent_instructions", lambda _: custom)
        monkeypatch.setattr(module, "resolve_role_model", lambda _: "offline-test-model")
    monkeypatch.setattr(planner, "_ensure_default_instruction_file", lambda: None)
    monkeypatch.setattr(cta, "_ensure_default_instruction_file", lambda: None)
    plan = planner._build_instruction(5)
    writer = phrasing.build_phrasing_agent()
    closing = cta.build_cta_agent()
    for instruction in (plan, writer.instruction, closing.instruction):
        assert instruction.endswith(WRITING_STANDARD)
        if custom:
            assert custom in instruction
    assert "must be <= 5" in plan
    assert writer.output_schema is CopySet and writer.output_key == K_COPY
    assert not writer.tools
    assert len(closing.tools) == 1
    assert "Design-specific destinations" in closing.instruction


@pytest.mark.parametrize("module,filename,constant", [
    (planner, "planner.md", "_DEFAULT_INSTRUCTION"),
    (phrasing, "phrasing.md", "DEFAULT_INSTRUCTION"),
    (cta, "cta.md", "_DEFAULT_INSTRUCTION"),
])
def test_fallback_prompts_match_the_shipped_instructions(module, filename, constant):
    path = Path(__file__).resolve().parents[1] / "skills/agents" / filename
    assert getattr(module, constant).strip() == path.read_text(encoding="utf-8").strip()
