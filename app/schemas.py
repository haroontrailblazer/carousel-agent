"""Shared Pydantic schemas - the data contract between all agents.

Every agent reads/writes these shapes through session state (see app/state.py).
Keep field names stable: the orchestrator, tools and review API all rely on them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.text_rules import require_no_em_dash, require_readable_text

CarouselStyle = Literal["points", "prose"]
CTAType = Literal["follow", "comment", "redirect"]
VerdictStatus = Literal["approved", "rejected"]
Severity = Literal["critical", "major", "minor"]


class PublishedTextModel(BaseModel):
    """Base model that rejects forbidden or unreadable published text."""

    @model_validator(mode="after")
    def validate_no_em_dash(self) -> "PublishedTextModel":
        published = self.model_dump(mode="python")
        require_no_em_dash(published, self.__class__.__name__)
        require_readable_text(published, self.__class__.__name__)
        return self


class NewsItem(BaseModel):
    """One update/news piece queued by the fetcher."""

    id: str
    title: str
    summary: str = ""
    body: str = ""
    source_name: str = ""
    source_url: str = ""
    media_urls: list[str] = Field(default_factory=list)
    published_at: Optional[datetime] = None
    tags: list[str] = Field(default_factory=list)


class ResearchFact(BaseModel):
    """One verified fact gathered by the Research agent."""

    fact: str  # the fact itself, with exact numbers/names/dates
    source_url: str = ""  # where it was verified (empty only for facts from the news item itself)


class ResearchBrief(BaseModel):
    """Output of the Research agent - the fact base the plan is built on."""

    summary: str  # 3-6 sentence briefing on what happened and why it matters
    key_facts: list[ResearchFact] = Field(default_factory=list)
    suggested_angle: str = ""  # one line: the most compelling hook angle found
    media_candidates: list[str] = Field(default_factory=list)  # official video/image URLs for the cover
    sources: list[str] = Field(default_factory=list)  # all URLs consulted


class SlidePlan(PublishedTextModel):
    """What one body slide should say (decided by the planner)."""

    index: int  # 1-based; slide 1 is the cover, so body slides start at 2
    purpose: str
    key_points: list[str] = Field(default_factory=list)


class CarouselPlan(PublishedTextModel):
    """The Editorial Planner's decision for the whole carousel."""

    style: CarouselStyle
    slide_count: int  # total including cover and CTA slide (Instagram max 10)
    max_lines_per_slide: int = 4
    hook_title: str  # cover title, <= 9 words, uppercase on render
    hook_highlight: str = ""  # the phrase inside hook_title rendered in orange
    cta_hint: CTAType = "follow"
    caption_seed: str = ""
    slides: list[SlidePlan] = Field(default_factory=list)  # body slides only


class CoverSpec(PublishedTextModel):
    """Output of the First-Page Visual agent."""

    video_artifact: str = ""  # artifact filename of the final 4-8 s cover video
    poster_artifact: str = ""  # first-frame PNG (used as IG fallback / preview)
    source_media_url: str = ""
    title: str = ""
    highlight: str = ""
    duration_s: float = 0.0
    used_fallback_image: bool = False  # True when no clip found; static cover built


class SlideCopy(PublishedTextModel):
    index: int
    lines: list[str] = Field(default_factory=list)  # <= plan.max_lines_per_slide


class CopySet(PublishedTextModel):
    """Output of the Content Phrasing agent."""

    slides: list[SlideCopy] = Field(default_factory=list)
    caption: str = ""


class RenderedSlide(BaseModel):
    index: int
    artifact: str  # artifact filename (PNG 1080x1350)
    template_used: str = ""


class CTASlide(BaseModel):
    cta_type: CTAType
    artifact: str = ""
    link_url: str = ""


class QAIssue(BaseModel):
    severity: Severity
    slide_index: Optional[int] = None
    message: str


class QAReport(BaseModel):
    passed: bool
    issues: list[QAIssue] = Field(default_factory=list)


class Bundle(PublishedTextModel):
    """The assembled carousel, ready for review/publish."""

    cover: CoverSpec
    slides: list[RenderedSlide] = Field(default_factory=list)
    cta: CTASlide
    caption: str = ""
    ordered_artifacts: list[str] = Field(default_factory=list)  # cover video first


class Verdict(BaseModel):
    """What the human decided in the review mail."""

    status: VerdictStatus
    feedback: str = ""  # optional on approve, compulsory on reject
    reviewer: str = ""
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReworkPlan(BaseModel):
    """Feedback Router output: which agents must re-run and why."""

    targets: list[str] = Field(default_factory=list)  # values from state.AGENT_* names
    reasons: dict[str, str] = Field(default_factory=dict)  # target -> reason
    feedback: str = ""


class FeedbackRecord(BaseModel):
    """Row stored for every piece of feedback (memory + learning)."""

    run_id: str
    verdict: VerdictStatus
    feedback: str
    targets: list[str] = Field(default_factory=list)
    news_title: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
