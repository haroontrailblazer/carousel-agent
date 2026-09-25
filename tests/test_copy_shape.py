"""Copy shape, repair notes, paragraph spacing, Visual parsing and quotes.

Offline only: fake agents, the real typesetter and a no-image CTA render.

- Prose slides that read as telegrams (fragments, or unlinked one-liners) go
  back to phrasing once; a real value-first paragraph passes.
- A step's repair note never reaches a later step, even across a restart.
- The saved design's text budget is in state before the planner runs too.
- Paragraphs are separated by half a body line, shared by renderer and gate.
- A plan purpose's Visual part is read up to its first clause break.
- Cover and CTA text get the same straight quotes as slide copy.
"""
from copy import deepcopy

import pytest
from PIL import Image

from app import state as s
from app.agents import template_design
from app.copy_budget import copy_budget_note, copy_problems
from app.pipeline_outputs import validate_output
from app.schemas import CarouselDesign, CarouselPlan, CopySet, CoverSpec, SlideCopy, SlidePlan
from app.tools import brand_layout as bl
from app.tools import image_gen

from test_pipeline_audit import Pipeline, outputs

# The user's saved Studio Light inside design (100% line height).
STUDIO_LIGHT = {"inside": {
    "background": "#F6F4F0", "text_color": "#252420", "title_size": 88, "font_family": "sans",
    "line_height": 100, "safe_margin": 88, "title_align": "left", "letter_spacing": -1.0,
    "word_spacing": 2.0, "title_position": "top-left", "highlight_text_color": "#6a9e00",
    "title_transform": {"x": 8.0, "y": 12.0, "width": 84.0, "height": 32.0, "locked": False},
}}
# A box the budget note tells to hold one short sentence.
TINY_BOX = {"inside": {"title_size": 60, "title_transform": {"x": 8, "y": 8, "width": 60, "height": 12}}}

# The copy_set of real run run-01239e778f58, written under the old prompt: every
# entry fits and ends cleanly, and none repeats, so only the shape is wrong.
RUN_01239E778F58 = [
    {"index": 2, "lines": ["This was OpenAI bug-bounty work", "Harsh Jaiswal worked with Mohan Pedhapati",
                           "Rahul Maini was also on the team", "Repository access took under 72 hours"]},
    {"index": 3, "lines": ["The path started in Discourse", "OpenAI's forum used libheif for images",
                           "A heap overflow could run server code", "OpenAI single sign-on was also weak"]},
    {"index": 4, "lines": ["Employee accounts were reached", "That included ChatGPT and Codex",
                           "Codex opened a harmless pull request", "It appeared in the internal openai/openai repo"]},
    {"index": 5, "lines": ["OpenAI confirmed a fix", "It came about 14 hours after reporting",
                           "OpenAI paid $6,500 on September 1, 2026", "Hacktron published it on September 13, 2026"]},
    {"index": 6, "lines": ["Claude did not act alone", "Hacktron says skilled humans guided it",
                           "Claude made exploit development faster", "Under $3,000 covered HEIF Heist, not just OpenAI"]},
]

# Neutral value-first slides: a detail, then what it means, in paragraphs.
PARAGRAPHS = [
    {"index": 2, "lines": [
        "The maker says battery life doubles",
        "In the maker's own tests, a phone with the new chip played 20 hours of video on one charge, about twice as long as last year's model.",
        "The chip is built on a 3nm process, which means its parts are smaller and waste less power."]},
    {"index": 3, "lines": [
        "It reaches shops in March",
        "The first phones with the chip go on sale in March at the same price as today's models, so the longer battery life costs nothing extra."]},
]

# Full sentences, each on its own line, with nothing that says what they mean.
ONE_LINERS = {"index": 2, "lines": [
    "The phone goes on sale in March",
    "The first phones with the new chip reach shops in early March.",
    "Each phone costs the same as the model it replaces this year.",
    "The maker plans a cheaper version with a smaller screen later."]}


def _state(slides, design=None, style="prose", max_lines=4):
    plan = {"style": style, "slide_count": max(slide["index"] for slide in slides) + 1,
            "hook_title": "A faster phone chip", "max_lines_per_slide": max_lines,
            "slides": [{"index": slide["index"], "purpose": "p", "key_points": ["k"]} for slide in slides]}
    return {s.K_PLAN: plan, s.K_DESIGN: design or {},
            s.K_COPY: {"slides": deepcopy(slides), "caption": "caption #a #b #c"}}


def _copy(*slides):
    return CopySet(slides=list(slides), caption="c")


# --- P3: telegram copy on prose slides goes back once -----------------------------


def test_the_real_telegram_run_is_sent_back_slide_by_slide():
    with pytest.raises(ValueError) as caught:
        validate_output(s.AGENT_PHRASING, _state(RUN_01239E778F58))
    message = str(caught.value)
    for slide in RUN_01239E778F58:
        assert f"slide {slide['index']} reads as separate fragments (lines 2, 3 and 4 are under 10 words)" in message
    assert "say what it means" in message and "keep every other slide as it was" in message
    # Nothing else is wrong with it: the shape check alone sends it back.
    assert copy_problems(CopySet(slides=RUN_01239E778F58, caption="c"), CarouselDesign(), style="") == []


def test_the_shape_check_needs_prose_a_first_attempt_and_room_for_a_paragraph():
    # Resume re-checks only fit, so copy that already rendered is never re-billed.
    validate_output(s.AGENT_PHRASING, _state(RUN_01239E778F58), checkpoint=True)
    # A points plan asks for one-line items.
    validate_output(s.AGENT_PHRASING, _state(RUN_01239E778F58, style="points"))
    # The last try before a run stops: the shape is advice, never a stop.
    validate_output(s.AGENT_PHRASING, _state(RUN_01239E778F58), final_attempt=True)
    # A box whose budget asks for one short sentence gets one.
    short = {"index": 2, "lines": ["Battery life doubles", "It lasts 20 hours."]}
    assert copy_problems(_copy(short), CarouselDesign.model_validate(TINY_BOX), style="prose") == []
    assert copy_problems(_copy(short), CarouselDesign(), style="prose") == [
        "slide 2 reads as separate fragments (line 2 is under 10 words). Join its lines into one or "
        "two paragraphs that give the detail and say what it means"]


def test_one_short_caveat_after_a_real_paragraph_is_not_a_telegram():
    """Rules 2, 12 and 15 allow a short meaning or caveat sentence; two short
    entries, or a body of nothing but short ones, is still the telegram."""
    caveat = {"index": 6, "lines": [
        "Two small bugs made one big hole",
        "Each bug looked minor alone, but linked together they reached staff accounts and the "
        "company's own code, which is why security teams fix small flaws fast.",
        "Only the researchers have reported these numbers."]}
    assert copy_problems(_copy(caveat), None, style="prose") == []
    two_short = deepcopy(caveat)
    two_short["lines"].append("The company has not commented.")
    assert copy_problems(_copy(two_short), None, style="prose") == [
        "slide 6 reads as separate fragments (lines 3 and 4 are under 10 words). Join its lines "
        "into one or two paragraphs that give the detail and say what it means"]


def test_the_budget_note_gives_both_shapes_one_total():
    """The planner reads it before choosing a style; points share the same total."""
    note = copy_budget_note(STUDIO_LIGHT)
    assert "characters in total" in note
    assert "as prose, in one paragraph" in note and "as points, up to three items" in note


def test_the_final_attempt_still_refuses_copy_that_is_broken_not_just_short():
    split = deepcopy(RUN_01239E778F58)
    split[0]["lines"][1] = "Harsh Jaiswal worked with Mohan Pedhapati and"
    with pytest.raises(ValueError, match="slide 2 line 2 stops mid-sentence"):
        validate_output(s.AGENT_PHRASING, _state(split), final_attempt=True)


def test_value_first_paragraphs_pass_on_the_saved_design():
    validate_output(s.AGENT_PHRASING, _state(PARAGRAPHS, design=STUDIO_LIGHT, max_lines=3))
    assert copy_problems(_copy(*PARAGRAPHS), None, style="prose") == []


def test_unlinked_one_liners_read_as_a_list_but_a_linked_pair_passes():
    assert copy_problems(_copy(ONE_LINERS), None, style="prose") == [
        "slide 2 reads as a list of separate statements. Join them into one paragraph that links "
        "the detail to what it means (because, so, which means)"]
    linked = deepcopy(ONE_LINERS)
    linked["lines"][2] = "Each phone costs the same as the model it replaces, so the upgrade is free."
    assert copy_problems(_copy(linked), None, style="prose") == []
    pair = {"index": 2, "lines": ["Testing began on July 23",
                                  "On July 23 they started testing how the forum handled uploaded images.",
                                  "That mattered because the image software had a memory bug in it."]}
    assert copy_problems(_copy(pair), None, style="prose") == []
    one_sentence = {"index": 2, "lines": ["It ships in March", "The phone with the new chip reaches shops in early March."]}
    assert copy_problems(_copy(one_sentence), None, style="prose") == []


# --- P2 and C3: repair notes stay with the step they repair ------------------------

LONG_COPY = {"slides": [{"index": 2, "lines": [
    "Local processing", " ".join(["The new release supports local processing."] * 40)]}], "caption": "Facts."}


def _recording(copies):
    """Drive phrasing with *copies* in turn; record the feedback each step reads."""
    seen = {}
    original = Pipeline.drive

    async def drive(self, agent, child, ctx, holder):
        seen.setdefault(child, []).append(
            (str(self.state.get(s.K_REWORK_FEEDBACK) or ""), self.state.get(s.K_COPY_BUDGET)))
        if child == s.AGENT_PHRASING:
            self.calls.append(child)
            yield agent._progress(ctx, child, {s.K_COPY: deepcopy(copies.pop(0))})
            return
        async for event in original(self, agent, child, ctx, holder):
            yield event

    return seen, drive


@pytest.mark.asyncio
async def test_a_step_that_fails_twice_leaves_no_repair_note_for_the_resume(monkeypatch):
    p = Pipeline()
    p.state[s.K_REWORK_FEEDBACK] = "Clarify copy"  # the reviewer's words stay exactly as written
    seen, drive = _recording([LONG_COPY, LONG_COPY, outputs()[s.K_COPY]])
    monkeypatch.setattr(Pipeline, "drive", drive)
    with pytest.raises(RuntimeError, match="phrasing could not finish"):
        await p.run()
    assert "Repair the phrasing output" in seen[s.AGENT_PHRASING][1][0]
    assert p.state[s.K_REWORK_FEEDBACK] == "Clarify copy"
    await p.run()  # Resume
    assert [feedback for feedback, _budget in seen[s.AGENT_PHRASING]][2] == "Clarify copy"
    assert [feedback for feedback, _budget in seen[s.AGENT_TEMPLATE_DESIGN]] == ["Clarify copy"]
    assert p.calls.count(s.AGENT_TEMPLATE_DESIGN) == 1
    assert p.state[s.K_PHASE] == "review"


@pytest.mark.asyncio
async def test_a_note_left_by_a_stopped_run_is_stripped_before_any_step_reads_it(monkeypatch):
    p = Pipeline()
    p.state.update({key: value for key, value in outputs().items() if key in (s.K_RESEARCH, s.K_PLAN, s.K_COVER)})
    p.state["generation_completed"] = ["research", "planner", "first_page_visual"]
    # State saved by a run that stopped mid-retry (or before this fix existed).
    p.state[s.K_REWORK_FEEDBACK] = (
        "\nRepair the phrasing output: Copy needs these fixes before any slide is rendered: slide 4 does "
        "not fit this design's text box even at the smallest readable size.\nCut about 40 characters. "
        "Save the complete result with your output tool.")
    seen, drive = _recording([outputs()[s.K_COPY]])
    monkeypatch.setattr(Pipeline, "drive", drive)
    await p.run()
    assert seen[s.AGENT_PHRASING][0][0] == ""
    assert seen[s.AGENT_TEMPLATE_DESIGN][0][0] == ""
    assert p.state[s.K_PHASE] == "review"


@pytest.mark.asyncio
async def test_the_planner_and_phrasing_both_see_the_measured_budget(monkeypatch):
    p = Pipeline()
    seen, drive = _recording([outputs()[s.K_COPY]])
    monkeypatch.setattr(Pipeline, "drive", drive)
    await p.run()
    for step in (s.AGENT_PLANNER, s.AGENT_PHRASING):
        budget = seen[step][0][1]
        assert budget and "characters" in budget, step
    assert seen[s.AGENT_RESEARCH][0][1] is None


@pytest.mark.asyncio
async def test_a_second_telegram_draft_is_accepted_rather_than_stopping_the_run(monkeypatch):
    p = Pipeline()
    telegram = {"slides": RUN_01239E778F58[:1], "caption": "Facts. #a #b #c"}
    seen, drive = _recording([telegram, telegram])

    async def prose_plan(self, agent, child, ctx, holder):
        if child == s.AGENT_PLANNER:
            self.calls.append(child)
            plan = dict(outputs()[s.K_PLAN], style="prose", max_lines_per_slide=4)
            yield agent._progress(ctx, child, {s.K_PLAN: plan})
            return
        async for event in drive(self, agent, child, ctx, holder):
            yield event

    monkeypatch.setattr(Pipeline, "drive", prose_plan)
    await p.run()
    assert "slide 2 reads as separate fragments" in seen[s.AGENT_PHRASING][1][0]
    # The reviewer sees it instead: rework feedback may ask for short lines.
    assert p.state[s.K_COPY] == telegram and p.state[s.K_PHASE] == "review"
    assert [feedback for feedback, _budget in seen[s.AGENT_TEMPLATE_DESIGN]] == [""]


@pytest.mark.asyncio
async def test_a_reviewer_who_asks_for_shorter_copy_is_not_overruled(monkeypatch):
    """Under human rework the prose shape is not checked: its repair note would
    reach phrasing as the reviewer's words and tell it to lengthen the slide."""
    reviewer = "Slide 2 is too wordy. Cut it to one short sentence."
    short = {"slides": [{"index": 2, "lines": ["The release now runs offline",
                                               "Your files stay on your laptop."]}],
             "caption": "Facts. #a #b #c"}
    p = Pipeline()
    seen, drive = _recording([PARAGRAPHS_COPY, short, short])

    async def rework(self, agent, child, ctx, holder):
        if child == s.AGENT_PLANNER:
            self.calls.append(child)
            plan = dict(outputs()[s.K_PLAN], style="prose", max_lines_per_slide=3)
            yield agent._progress(ctx, child, {s.K_PLAN: plan})
            return
        if child == s.AGENT_FEEDBACK_ROUTER:
            self.calls.append(child)
            yield agent._progress(ctx, "route", {
                s.K_REWORK_PLAN: {"targets": ["phrasing"], "feedback": reviewer}})
            return
        async for event in drive(self, agent, child, ctx, holder):
            yield event

    monkeypatch.setattr(Pipeline, "drive", rework)
    await p.run()
    assert p.state[s.K_PHASE] == "review"
    p.calls.clear()
    p.state[s.K_VERDICT] = {"status": "rejected", "feedback": reviewer}
    await p.run()

    assert p.calls.count(s.AGENT_PHRASING) == 1
    feedback = [text for text, _budget in seen[s.AGENT_PHRASING]]
    assert feedback[1] == reviewer
    assert not any("separate fragments" in text or "Repair the" in text for text in feedback)
    assert p.state[s.K_COPY] == short and p.state[s.K_PHASE] == "review"


PARAGRAPHS_COPY = {"slides": [{"index": 2, "lines": [
    "The release now runs offline",
    "The new release supports local processing, which means your files stay on your own "
    "laptop instead of a server."]}], "caption": "Facts. #a #b #c"}


# --- P7: paragraphs are half a body line apart --------------------------------


def _ink_rows(image, top, bottom, background):
    rows = []
    for y in range(top, bottom):
        if any(sum(abs(a - b) for a, b in zip(image.getpixel((x, y)), background)) > 120 for x in range(80, 1000, 3)):
            rows.append(y)
    return rows


def _blank_runs(rows):
    return [after - before - 1 for before, after in zip(rows, rows[1:]) if after - before > 1]


@pytest.mark.parametrize("raw,line_height", [(STUDIO_LIGHT, 36), ({}, 43)])
def test_paragraph_gap_scales_with_the_body_line_and_the_renderer_draws_it(raw, line_height):
    design = CarouselDesign.model_validate(raw)
    headline, first, second = PARAGRAPHS[0]["lines"]
    layout = bl.fit_inside_copy(design, headline, [first, second])
    assert layout.body_line_height == line_height
    assert layout.thought_gap == round(line_height * 0.5) > 10
    # The gate and the budget measure through fit_inside_copy, so the gap
    # costs them exactly what it costs the renderer: two paragraphs are the
    # two measured apart plus one gap.
    heights = {name: bl.fit_inside_copy(design, headline, body).total_height
               for name, body in (("none", []), ("first", [first]), ("second", [second]))}
    assert layout.total_height == (heights["first"] + heights["second"] - heights["none"]
                                   - layout.section_gap + layout.thought_gap)
    background = bl.hex_color(design.inside.background, bl.PAPER)
    image = bl.apply_slide_typography(Image.new("RGB", (1080, 1350), background), headline, [first, second], design=design)
    top = bl.inside_text_box(design.inside)[3]
    head_bottom = top + len(layout.headline_lines) * layout.head_line_height + layout.section_gap - 4
    runs = _blank_runs(_ink_rows(image, head_bottom, top + layout.total_height + 20, background))
    # The widest blank run between body rows is the paragraph break: the
    # ordinary space between two lines plus the paragraph gap.
    assert max(runs) - sorted(runs)[len(runs) // 2] >= layout.thought_gap - 3


def test_the_budget_offers_two_paragraphs_only_when_each_has_room():
    roomy, studio = copy_budget_note(CarouselDesign()), copy_budget_note(STUDIO_LIGHT)
    assert "in one or two paragraphs" in roomy
    # Studio Light's box holds six 36px body lines; a paragraph break costs one
    # of them, so a single paragraph carries more.
    assert "in one paragraph" in studio
    assert "small text box" in copy_budget_note(TINY_BOX)


# --- P8: the Visual part of a plan purpose -------------------------------------


def _plan_slide(purpose):
    return SlidePlan(index=2, purpose=purpose, key_points=["k"])


DATED = SlideCopy(index=2, lines=["Testing began in July", "On July 23, 2026 they began testing how the forum handled images."])


def test_the_real_subject_is_an_editorial_explainer_with_the_cover_subject():
    subject = _plan_slide("Reader asks: who are they? Payoff: three researchers. Visual: the real subject.")
    assert template_design._layout_hint(DATED, subject) == "editorial explainer"
    assert template_design._needs_subject_reference(subject)
    for purpose in ("Visual: a photo of the product.", "Visual: the person behind it"):
        assert template_design._layout_hint(DATED, _plan_slide(purpose)) == "editorial explainer"


def test_a_long_scraped_summary_never_pushes_the_slide_out_of_its_image_prompt():
    """The PTC run: a 2000-character news summary, a research summary and six
    facts filled the 2800-character block before any slide's purpose."""
    from app.schemas import NewsItem, ResearchBrief

    news = NewsItem(id="n", title="Researchers used Claude to get into OpenAI", summary="word " * 400)
    research = ResearchBrief(summary="brief " * 160, key_facts=[{"fact": "fact " * 20}] * 6)
    slide = SlidePlan(index=3, purpose="Reader asks: how did they get in? Payoff: two bugs. Visual: process.",
                      key_points=["A memory bug in image uploads", "A weak single sign-on"])
    context = template_design._visual_context(news, research, slide)

    assert len(context) <= 2800
    assert context.startswith("Exact news item: Researchers used Claude")
    assert context.endswith("This slide's approved points: A memory bug in image uploads | A weak single sign-on")
    assert "This slide's purpose: Reader asks: how did they get in?" in context
    short = template_design._visual_context(NewsItem(id="n", title="T", summary="S"), None, slide)
    assert short == ("Exact news item: T\nNews summary: S\nThis slide's purpose: " + slide.purpose
                     + "\nThis slide's approved points: A memory bug in image uploads | A weak single sign-on")


def test_a_note_after_the_visual_part_is_not_a_visual_instruction():
    for purpose in ("Reader asks: how? Payoff: two flaws. Visual: technical proof, identify Hacktron as the source.",
                    "Visual: technical proof - identify Hacktron as the source.",
                    "Visual: technical proof (identify Hacktron as the source)."):
        slide = _plan_slide(purpose)
        assert not template_design._needs_subject_reference(slide), purpose
        assert template_design._layout_hint(DATED, slide) == "dark proof", purpose
    # A period or comma inside a name or number does not end the Visual part.
    assert template_design._layout_hint(DATED, _plan_slide("Visual: the Node.js interface.")) == "dark proof"
    assert template_design._layout_hint(DATED, _plan_slide("Visual: $6,500 chart; keep it quiet.")) == "data evidence"


def test_technical_outranks_evidence_but_data_evidence_is_still_data():
    hint = lambda purpose: template_design._layout_hint(DATED, _plan_slide(purpose))  # noqa: E731
    assert hint("Visual: technical evidence of how far the access went.") == "dark proof"
    assert hint("Visual: evidence from the dark admin interface.") == "dark proof"
    assert hint("Visual: data evidence.") == "data evidence"
    assert hint("Visual: evidence.") == "data evidence"


def test_a_date_in_the_copy_is_not_a_statistic():
    plain = _plan_slide("Explain when it began")
    assert template_design._layout_hint(DATED, plain) != "data evidence"
    day_first = SlideCopy(index=2, lines=["It began", "The work began on 23 July."])
    assert template_design._layout_hint(day_first, plain) != "data evidence"
    for figures in ("They were paid $6,500.", "It took 72 hours.", "Three flaws were found 14 hours apart."):
        slide = SlideCopy(index=2, lines=["The result", f"It started on July 23. {figures}"])
        assert template_design._layout_hint(slide, plain) == "data evidence", figures


# --- P10: cover and CTA text get straight quotes too ---------------------------


def test_cover_text_is_straightened_where_it_is_planned_and_recorded():
    plan = CarouselPlan(style="prose", slide_count=3, hook_title="It’s “free” now",
                        hook_highlight="“free” now", slides=[])
    assert plan.hook_title == "It's \"free\" now" and plan.hook_highlight == "\"free\" now"
    assert plan.hook_highlight in plan.hook_title
    cover = CoverSpec(title="OpenAI’s ‘Codex’", highlight="‘Codex’")
    assert (cover.title, cover.highlight) == ("OpenAI's 'Codex'", "'Codex'")


def test_the_cta_renders_straight_quotes(monkeypatch, tmp_path):
    design = CarouselDesign(handle_text="@test", logo_visible=False, handle_visible=False,
                            cta={"image_type": "none"})
    monkeypatch.setattr(image_gen, "_call_images_api", lambda *a, **k: pytest.fail("no paid image generation"))
    typeset = []
    original = image_gen.apply_slide_typography

    def record(image, headline, lines, **kwargs):
        typeset.append((headline, list(lines)))
        return original(image, headline, lines, **kwargs)

    monkeypatch.setattr(image_gen, "apply_slide_typography", record)
    image_gen.generate_cta_image("comment", "What’s next?", ["Tell us “yes” or “no”."],
                                 "@test", "", str(tmp_path / "cta.png"), design)
    assert typeset == [("What's next?", ["Tell us \"yes\" or \"no\"."])]
