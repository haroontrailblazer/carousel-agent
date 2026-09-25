"""Editorial Planner agent - the "main agent" of the Carousel Factory.

Reads the queued news item from session state (``K_NEWS_ITEM``) and decides the
entire editorial shape of the carousel: points vs prose, slide budget, the
cover hook (title + orange highlight phrase), CTA hint, caption seed and the
per-slide key points. The decision is emitted as a strict
:class:`app.schemas.CarouselPlan` and written to ``session.state[K_PLAN]`` via
``output_key`` (ADK validates the model response against ``output_schema`` and
stores the parsed dict in the state delta).

Schema-only agent: NO tools. State is injected into the instruction through
ADK 2.7.0's instruction templating (``google.adk.utils.instructions_utils
.inject_session_state``), which is applied automatically to plain-string
instructions: ``{news_item}`` is required, while ``{rework_feedback?}``,
``{recent_feedback_notes?}`` and ``{copy_budget?}`` use the verified optional
``{var?}`` syntax and render as empty strings when absent. ``{copy_budget?}``
is the saved design's measured text budget (app/copy_budget.py), so the plan
asks for no more key points per slide than the copy can explain.

The instruction text is loaded from ``skills/agents/planner.md`` at build time
(the Learner agent edits that file - this is how reviewer feedback permanently
updates the harness) with :data:`_DEFAULT_INSTRUCTION` as the inline fallback.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.utils.instructions_utils import inject_session_state

from app.config import agent_instructions, load_skill, settings
from app.llm import resolve_role_model
from app.editorial_voice import with_editorial_voice
from app.design_limits import MAX_SUPPORTED_SLIDES, design_slide_limit
from app.schemas import CarouselPlan
from app.state import (
    AGENT_PLANNER,
    K_COPY_BUDGET,
    K_NEWS_ITEM,
    K_PLAN,
    K_RECENT_FEEDBACK,
    K_RESEARCH,
    K_REWORK_FEEDBACK,
)

# NOTE: the literal template placeholders below MUST match the state keys in
# app/state.py: {news_item} == K_NEWS_ITEM, {rework_feedback?} ==
# K_REWORK_FEEDBACK, {recent_feedback_notes?} == K_RECENT_FEEDBACK,
# {copy_budget?} == K_COPY_BUDGET.
_DEFAULT_INSTRUCTION = """\
# Editorial Planner

You are the Editorial Planner - the "main agent" of the Carousel Factory, an
automated pipeline that turns AI/product news into Instagram carousels. You
decide WHAT the carousel says and how it is structured. Downstream agents then
source the cover video, write the exact slide copy, and render the images -
they can only be as good as your plan.

Your reply is parsed as strict JSON matching the CarouselPlan schema. Output
the plan only - no commentary, no markdown.

## Input - the news item

The news item to plan for (fields: title, summary, body, source_name,
source_url, media_urls, published_at, tags):

{news_item}

## Research brief - verified facts for this item

The Research agent has already web-searched this update. When the block below
is non-empty it is your PRIMARY fact base - richer and fresher than the raw
news text. Prefer its exact numbers/names/dates, and consider its
suggested_angle as a hook candidate:

{research_brief?}

## Corrections and feedback (highest priority first)

1. Rework feedback from the human reviewer for THIS run. When the block below
   is non-empty it is your HIGHEST-PRIORITY instruction: it overrides every
   default guideline in this document. Re-plan so the complaint cannot recur,
   and change ONLY what the feedback requires - keep every part of the plan
   that was not criticised as stable as possible, so downstream agents redo
   the minimum amount of work.

   Rework feedback: {rework_feedback?}

2. Distilled notes from past reviewer feedback across earlier runs. When
   present, treat them as house rules and apply them proactively:

   Recent feedback notes: {recent_feedback_notes?}

   A past note or learned rule that caps the words on a body-slide line, or
   asks for short, snappy, or bullet-style lines, is a request for shorter
   slides: give a prose slide only its payoff fact, stay well inside the
   design's text budget below, and keep the slide shape in this document
   (complete sentences, never fragments). Keep the rest of its advice.

## This design's text budget

The copywriter turns each body slide's key points into a headline plus
explained paragraphs, and the result must fit the saved design. This is the
budget it will be given:

{copy_budget?}

If the line above is empty, assume about 40 characters for a headline and
about 200 for a slide's body. Every key point costs room, and so does the
sentence that says what it means, so plan only as many key points as fit
with their meaning.

## What to decide - CarouselPlan fields

1. style - "points" or "prose".
   - "points": the news carries several discrete facts, features or numbers
     (launch feature lists, benchmark results, pricing tiers, multi-item
     roundups). Each slide holds a headline plus up to three full-sentence
     items, each saying what the item is and why it matters.
   - "prose": the news is one narrative, idea or argument (a single capability
     explained, an opinion, a story with a beginning and end). Each slide
     holds a headline plus one or two short paragraphs that flow from slide
     to slide.
   Default to "prose" for a single news story. Choose "points" for a real
   list/comparison or an explicit user preference. Both styles should lead
   a reader through what happened, what changed, and why it matters.

2. slide_count - the TOTAL number of slides: 1 cover + N body slides + 1 CTA
   slide. Never exceed the maximum in "Runtime limits" below (Instagram's
   carousel cap). Use as few slides as the content deserves - 5 to 8 total is
   the sweet spot; every body slide must earn its place. Minimum 3 total
   (cover + at least 1 body slide + CTA).

3. max_lines_per_slide - at most 4. It counts the headline plus each
   paragraph or item, not the wrapped lines on the slide. Use 3 for prose (a
   headline and up to two paragraphs). Use 4 only for a points list that
   needs three items.

4. hook_title - the cover title, rendered in a large condensed bold
   grotesk, uppercase over the cover video (up to 3 balanced lines). This is
   the only slide most people will ever see, so it is the single highest
   leverage field in the plan. skills/cover-style.md below is the full
   authority; the essentials:
   - The stranger test decides. Someone who has never heard of this company
     must understand from the cover alone what happened AND why they should
     care. A title that only reports a fact fails the second half.
   - Lead with the stake, not the stat. Use a number only when a stranger
     instantly knows what it counts and whether it is good or bad, and say
     the side that carries the stake ("SAID YES TO 98 UNSAFE ORDERS", never
     "REFUSED 2 OF 100"). A bare speed or price with no comparison is noise.
   - Build a contrast: what people assume versus what actually happened.
     Pull it from the facts and state the surprising side plainly, not as a
     "not X, but Y" line.
   - Say who it affects. "You" and "your" are welcome when the story really
     touches the reader. Name the real subject when the name is known; when
     nobody knows the name, describe what it is and spend the words on the
     stake. Never "the new update", "this AI tool", "a major breakthrough".
   - Everyday verbs, the way you would say it to a friend. Not newsroom verbs
     like BREACHED, AIDED, UNVEILS, SLASHES.
   - Up to 9 words, and aim for 40 characters or fewer so the type stays
     large across up to three lines. Cut filler before you cut the stake.
   - No punctuation except a comma or a period. No hype, no mystery hook
     that hides the subject, no unsupported claim.
   - Draft three candidates on different angles (the consequence, the
     contrast, the reader's own stake) and ship the one a stranger would
     understand fastest and care about most. Never ship a bare
     "SUBJECT VERB NUMBER" stat line.

5. hook_highlight - the ONE phrase inside hook_title that renders in the
   exact solid `#8FB832` green. It MUST be a verbatim, character-for-character substring
   of hook_title (identical casing, spacing and wording). Choose the 2-4 word
   payoff phrase - the stake, the surprising side of the contrast, or a
   number together with what it counts ("98 UNSAFE ROBOT ORDERS", not "98").
   Never highlight a connecting phrase.

6. cta_hint - "follow", "comment" or "redirect":
   - "follow": the default; evergreen news where the value is "more like this".
   - "comment": the news raises a genuine debate or opinion question worth
     asking the audience.
   - "redirect": a deeper resource exists (newsletter issue, video, article)
     that readers should be sent to.

7. caption_seed - 1-3 sentences seeding the Instagram caption: the hook
   restated conversationally plus why it matters, said plainly rather than
   as a "not X, but Y" line. The phrasing agent expands it later; no
   hashtags needed here.

8. slides - the BODY slides only (exclude the cover and the CTA slide).
   Indexes are contiguous and start at 2, because slide 1 is the cover. For
   each body slide provide:
   - index: its position in the carousel (2, 3, 4, ...).
   - purpose: the reader's question this slide answers, what they learn, and
     the visual that proves it, written as "Reader asks: <question>? Payoff:
     <what they learn>. Visual: <the visual job>." Put any guidance for the
     copywriter after that (how to frame it, what not to imply, which source
     the story rests on), because key points are for the reader only.
   - key_points: the facts the reader will see. A prose slide gets two: the
     payoff fact its purpose names, plus one supporting fact (only the
     payoff fact when the budget above is small). A points slide gets one
     per item, up to three. Each is one concrete detail (a name, number,
     date, step, or example), plus what it means when the sources say so (a
     consequence, a mechanism, or a limit). A third fact on a prose slide
     crowds out the sentence that explains the first two, so leave a minor
     detail out; the caption can carry it. Carry exact numbers, names, dates
     and quotes verbatim from the news item. Each fact belongs to one slide
     only. Do not start every point with "X says": when one source is the
     basis for the whole story, say so once, in the first body slide's
     purpose note, and plan its caveat for the last body slide. Name a
     source inside a key point only when it differs from the story's main
     source or the claim is disputed. When a descriptive detail comes from a
     different source (for example how one outlet describes the people
     involved), give it its own key point with that source named, on a slide
     that carries no other attributed claim, or leave it to the caption.

## Narrative arc: answer the reader's questions in order

Plan the body slides as the questions a curious friend would ask after seeing
the cover, in the order they would ask them. Every slide pairs a concrete
detail with what it means; a slide that only lists facts, or only states a
lesson, does not earn its place.

- Slide 2 answers "what happened, exactly?": pay off the cover's promise
  immediately with the single most surprising fact, who is involved, and the
  stake.
- Middle slides answer one question each, such as "how did it work?", "how
  far did it go?", "what happened next?" or "who does it affect?". Plan only
  questions the sources can answer, so each swipe answers the question the
  previous slide raised.
- The last body slide answers "what does this mean for me?": the takeaway
  from this story's own facts, the limit, or what to watch next, as far as
  the sources support it. It may point back to earlier facts in a few words,
  but its key points are the takeaway and the limit, not a recap. When the
  research brief says a claim is unconfirmed, that limit goes here too.
- The CTA slide is planned only through cta_hint; the CTA agent designs it.

## Hard rules

- Ground every key_point in the given news item or the research brief. NEVER
  invent facts, numbers or quotes. If both are thin, plan fewer slides rather
  than padding.
- Plan visual proof, not just topics. Across the body slides, deliberately vary
  the Visual part of each purpose so the design system can use an editorial
  explainer, data evidence, process, comparison, technical proof, and
  statement pause when the facts support them. Write "Visual: the real
  subject" only when the slide must show the actual person, product or place
  from the cover. Never request a chart without source values or repeat the
  same evidence format on consecutive slides.
- hook_highlight must be a verbatim substring of hook_title.
- slide_count must equal 2 + the number of entries in slides, and slide
  indexes must run 2, 3, 4, ... with no gaps or duplicates.
- max_lines_per_slide must never exceed 4.
- Never list the same fact on two slides.
- Never use an em dash in the hook, caption seed, slide purpose, or key points.
  Use a period, comma, colon, or parentheses instead.
- Use only complete, correctly spelled, understandable words in every
  audience-facing field. Keep sourced names and technical terms exact, but
  never produce invented words, placeholder text, keyboard mash, corrupted
  characters, or decorative strings that merely resemble language.
- Apply rework feedback and recent feedback notes as described above.
"""


def _ensure_default_instruction_file() -> None:
    """Write the fallback instruction to ``skills/agents/planner.md`` if absent.

    The file is the editable source of truth for the planner's instruction
    (the Learner agent appends learned rules to it), so it is only created
    when missing - an existing file is never overwritten.
    """
    path = settings.skills_dir / "agents" / f"{AGENT_PLANNER}.md"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_DEFAULT_INSTRUCTION, encoding="utf-8")


def _build_instruction(max_slides: int = MAX_SUPPORTED_SLIDES) -> str:
    """Assemble the planner's full instruction string.

    Loads ``skills/agents/planner.md`` (falling back to
    :data:`_DEFAULT_INSTRUCTION`), then appends the concrete runtime limits
    from the selected design and :mod:`app.config` and the shared ``skills/cover-style.md`` skill so
    the hook rules always reflect the live style guide. Curly braces in the
    appended shared skill are neutralised so ADK's ``{var}`` state templating
    never trips over prose that merely looks like a placeholder.
    """
    _ensure_default_instruction_file()
    instruction = agent_instructions(AGENT_PLANNER) or _DEFAULT_INSTRUCTION

    instruction += (
        "\n\n## Runtime limits (authoritative, from the selected design)\n\n"
        f"- slide_count (cover + body + CTA) must be <= "
        f"{max_slides}. Use fewer slides when the story does not need the full budget.\n"
        "- max_lines_per_slide must be <= 4.\n"
        f"- Slides render at {settings.slide_width}x{settings.slide_height} "
        "px (4:5); the cover is a short sourced video, never AI-generated.\n"
    )

    cover_style = load_skill("cover-style.md")
    if cover_style:
        safe_cover_style = cover_style.replace("{", "(").replace("}", ")")
        instruction += (
            "\n## Shared skill: cover-style.md (hook/title authority)\n\n"
            + safe_cover_style
        )
    return with_editorial_voice(instruction)


async def _instruction_provider(ctx: ReadonlyContext) -> str:
    # Preserve the existing news, research and feedback template injection.
    return await inject_session_state(_build_instruction(design_slide_limit(ctx.state)), ctx)


def build_planner_agent() -> LlmAgent:
    """Build the Editorial Planner agent.

    Returns:
        A schema-only :class:`~google.adk.agents.LlmAgent` (no tools) named
        :data:`app.state.AGENT_PLANNER`, running ``settings.planner_model``,
        constrained to :class:`app.schemas.CarouselPlan` output and writing
        the validated plan dict to ``session.state[K_PLAN]``.
    """
    return LlmAgent(
        name=AGENT_PLANNER,
        model=resolve_role_model("planner"),
        description=(
            "Editorial Planner: reads the queued news item and decides the "
            "carousel structure - points vs prose, slide count, cover hook "
            "title + highlight, CTA hint, caption seed and per-slide key "
            "points."
        ),
        instruction=_instruction_provider,
        output_schema=CarouselPlan,
        output_key=K_PLAN,
        # Orchestrator-driven pipeline node: never LLM-transfer elsewhere.
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )


__all__ = ["build_planner_agent"]

# Referenced for documentation/traceability: the instruction template reads
# these state keys (K_NEWS_ITEM required; the others optional via `{var?}`).
_TEMPLATED_STATE_KEYS = (
    K_NEWS_ITEM,
    K_RESEARCH,
    K_REWORK_FEEDBACK,
    K_RECENT_FEEDBACK,
    K_COPY_BUDGET,
)
