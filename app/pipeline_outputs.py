"""Validate each agent's hand-off before downstream work spends time on it."""
from typing import Any
from app import state as s
from app.design_limits import design_slide_limit
from app.schemas import ResearchBrief, CarouselPlan, CoverSpec, CopySet, RenderedSlide, CTASlide

OUTPUT_KEYS = {
    s.AGENT_RESEARCH: s.K_RESEARCH, s.AGENT_PLANNER: s.K_PLAN,
    s.AGENT_FIRST_PAGE_VISUAL: s.K_COVER, s.AGENT_PHRASING: s.K_COPY,
    s.AGENT_TEMPLATE_DESIGN: s.K_BODY_SLIDES, s.AGENT_CTA: s.K_CTA_SLIDE,
}


def validate_output(name: str, state: Any) -> None:
    """Raise a repairable error for missing, empty or inconsistent output."""
    raw = state.get(OUTPUT_KEYS[name])
    if raw is None:
        raise ValueError(f"{name} did not save its required output")
    if name == s.AGENT_RESEARCH:
        brief = ResearchBrief.model_validate(raw)
        if not brief.summary.strip() or not brief.key_facts:
            raise ValueError("Research must save a summary and at least one supported fact")
    elif name == s.AGENT_PLANNER:
        plan = CarouselPlan.model_validate(raw)
        if not 3 <= plan.slide_count <= design_slide_limit(state):
            raise ValueError("Plan slide count exceeds this design's limit (including cover and CTA)")
        if sorted(slide.index for slide in plan.slides) != list(range(2, plan.slide_count)):
            raise ValueError("Plan must contain exactly one body slide at every index from 2 to slide_count - 1")
        if not plan.hook_title.strip() or any(not slide.key_points for slide in plan.slides):
            raise ValueError("Plan needs a cover title and researched points for every slide")
    elif name == s.AGENT_FIRST_PAGE_VISUAL:
        cover = CoverSpec.model_validate(raw)
        if not cover.poster_artifact or not cover.video_artifact or not cover.title.strip():
            raise ValueError("Cover must save both the poster PNG and MP4, with its title")
    elif name == s.AGENT_PHRASING:
        copy = CopySet.model_validate(raw)
        plan = CarouselPlan.model_validate(state.get(s.K_PLAN))
        if sorted(slide.index for slide in copy.slides) != sorted(slide.index for slide in plan.slides):
            raise ValueError("Copy must match every planned body slide, without missing or duplicate indexes")
        if not copy.caption.strip() or any(not slide.lines or any(not line.strip() for line in slide.lines) or len(slide.lines) > plan.max_lines_per_slide for slide in copy.slides):
            raise ValueError("Copy needs a caption and nonempty lines within the plan's line limit")
    elif name == s.AGENT_TEMPLATE_DESIGN:
        slides = [RenderedSlide.model_validate(slide) for slide in raw]
        copy = CopySet.model_validate(state.get(s.K_COPY))
        if sorted(slide.index for slide in slides) != sorted(slide.index for slide in copy.slides):
            raise ValueError("Rendered slides must match every copy index, without missing or duplicate slides")
        artifacts = [slide.artifact for slide in slides]
        if any(not artifact for artifact in artifacts) or len(set(artifacts)) != len(artifacts):
            raise ValueError("Each body slide must save its own PNG artifact")
    elif name == s.AGENT_CTA:
        cta = CTASlide.model_validate(raw)
        if not cta.artifact:
            raise ValueError("CTA did not save its closing-slide artifact")
        if cta.cta_type == "redirect" and not cta.link_url.strip():
            raise ValueError("A redirect CTA must include its destination")
