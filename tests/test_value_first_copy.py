"""Inside slides read as explained paragraphs, sized to the saved design.

The phrasing prompt used to cap every body line at 8 words and 48 characters,
one thought per line, so slides read like telegrams. The copy is now a
headline plus paragraphs that pair a detail with what it means, and its budget
is measured from the saved design. Longer copy must never cost a failed run,
so copy that cannot render is sent back to phrasing before any image is paid
for. Everything here is offline: fake models, the real typesetter.
"""
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
from google.adk.agents import SequentialAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.lite_llm import _to_litellm_response_format
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import Field

from app import state as s
from app.agents import feedback_router, phrasing, planner, template_design
from app.copy_budget import copy_budget_note, copy_problems
from app.editorial_voice import WRITING_STANDARD
from app.pipeline_outputs import validate_output
from app.schemas import CarouselDesign, CarouselPlan, CopySet, SlideCopy, SlidePlan
from app.tools import brand_layout as bl

from test_pipeline_audit import Pipeline, outputs
from test_rework_plan_schema import _violations

REPO = Path(__file__).resolve().parents[1]

# The user's saved Studio Light inside design (run-91de85848f6f).
STUDIO_LIGHT = {"inside": {
    "background": "#F6F4F0", "text_color": "#252420", "title_size": 88, "font_family": "sans",
    "line_height": 100, "safe_margin": 88, "title_align": "left", "letter_spacing": -1.0,
    "word_spacing": 2.0, "title_position": "top-left", "highlight_text_color": "#6a9e00",
    "title_transform": {"x": 8.0, "y": 12.0, "width": 84.0, "height": 32.0, "locked": False},
}}
# The editor's fallback title box: the smallest text area a saved design has.
SMALL_BOX = {"inside": {"title_size": 76, "font_family": "condensed",
                        "title_transform": {"x": 8, "y": 8, "width": 76, "height": 20}}}

# That run's body slides rewritten from its own plan under the tightened
# rules: a detail and what it means on every slide, at most two explained
# terms per slide, and the one source named on the first body slide and again
# only in the caveat on the last slide. Its later numbers (the $6,500) sit in
# a body, where the first slide's attribution covers them, so no headline
# repeats its name. The least important facts (the July dates, one outlet's
# description of the researchers) are left to the caption so the meaning
# sentences fit.
VALUE_FIRST = [
    {"index": 2, "lines": [
        "Hacktron says access took under 72 hours",
        "Its three researchers, Harsh Jaiswal, Mohan Pedhapati and Rahul Maini, went from their first bug to OpenAI staff accounts and then to the company's internal code.",
        "They had permission, because this was bug-bounty work, where a company pays outsiders to find its flaws."]},
    {"index": 3, "lines": [
        "The way in began with an image bug",
        "The researchers linked a memory bug in the software that handles uploaded images to a weak spot in OpenAI's single sign-on, the one login for many apps.",
        "So a weak spot in that login can reach well beyond one app. Claude helped them write the code that used both flaws."]},
    {"index": 4, "lines": [
        "The proof was a harmless code change",
        "The staff accounts were on ChatGPT and Codex, OpenAI's coding tool, and one of them led into the company's main code store.",
        "There they opened a pull request, a change that waits for review, so they proved access without touching live code."]},
    {"index": 5, "lines": [
        "The team reported the flaws privately",
        "Once they had shown what the flaws could do, the researchers reported them through Bugcrowd, a bug-bounty site. OpenAI fixed them and paid a $6,500 bounty.",
        "Reporting privately matters because the company can fix a flaw before attackers find it."]},
    {"index": 6, "lines": [
        "Two small bugs made one big hole",
        "Each bug sounds minor alone, but linked together they reached staff accounts and company code, which means a small bug can matter more than it looks.",
        "No OpenAI report on this case turned up, so the 72 hours and the $6,500 are Hacktron's word alone."]},
]

# Phrases that say whose claim a sentence is. One per slide is enough; the
# earlier fixture carried about nine on five slides.
ATTRIBUTION = re.compile(
    r"\b(?:says?|said|claims?|according to|reportedly|by (?:their|its|his|her) account|"
    r"in (?:their|its) own (?:words|tests|write-up)|(?:word|account) alone|comes from)\b", re.I)
# The words that turn two facts into an explanation.
MEANING_LINK = re.compile(r"\b(?:because|so|which means|that means)\b", re.I)
PRONOUNS = {"they", "them", "their", "it", "its", "he", "him", "his", "she", "her"}
_NOT_A_NAME = PRONOUNS | {"the", "a", "an", "this", "that", "these", "those"}


def _headline_problems(headline: str) -> list[str]:
    """A headline opens on its subject and names it (a capitalized name) before any pronoun."""
    words = [word.strip(".,:;!?\"'") for word in headline.split()]
    problems = []
    if words and words[0].lower() in PRONOUNS:
        problems.append(f"{headline!r} opens on a pronoun")
    for position, word in enumerate(words[1:], start=1):
        named = any(w[:1].isupper() and w.lower() not in _NOT_A_NAME for w in words[:position])
        if word.lower() in PRONOUNS and not named:
            problems.append(f"{headline!r} uses {word!r} before naming who it means")
    return problems


# The same run's real telegram copy: slide 2 splits one sentence across three
# entries, and slide 6 repeats slide 3's sentence word for word.
TELEGRAM = [
    {"index": 2, "lines": ["Indian-origin researchers named", "Harsh Jaiswal, Mohan Pedhapati,",
                           "and Rahul Maini did bug-bounty research.", "Hacktron says they reached code under 72 hours."]},
    {"index": 3, "lines": ["Two flaws were chained", "Review began July 23 with Discourse uploads.",
                           "On July 25, libheif heap overflow met OpenAI SSO.", "Claude helped build the exploit."]},
    {"index": 6, "lines": ["Small flaws can stack", "Claude helped build the exploit.",
                           "Hacktron says access took under 72 hours.", "No OpenAI report confirms timeline or bounty."]},
]


def _state(design, slides, max_lines=3):
    plan = {"style": "prose", "slide_count": max(slide["index"] for slide in slides) + 1,
            "hook_title": "Claude helped researchers get into OpenAI", "max_lines_per_slide": max_lines,
            "slides": [{"index": slide["index"], "purpose": "p", "key_points": ["k"]} for slide in slides]}
    return {s.K_PLAN: plan, s.K_DESIGN: design,
            s.K_COPY: {"slides": deepcopy(slides), "caption": "caption #a #b #c"}}


@lru_cache(maxsize=None)
def _renders_cached(design_json: str, lines: tuple) -> bool:
    # The renderer's full size sweep takes seconds for copy that never fits,
    # so each (design, slide) verdict is measured once for all tests.
    try:
        bl.fit_inside_copy(CarouselDesign.model_validate_json(design_json), lines[0], list(lines[1:]))
        return True
    except ValueError:
        return False


def _renders(design, slide) -> bool:
    """What the real renderer decides, trying every size it would try."""
    return _renders_cached(design.model_dump_json(), tuple(slide["lines"]))


def test_value_first_slides_render_at_full_size_on_the_saved_design():
    design = CarouselDesign.model_validate(STUDIO_LIGHT)
    for slide in VALUE_FIRST:
        layout = bl.fit_inside_copy(design, slide["lines"][0], slide["lines"][1:])
        assert (layout.headline_size, layout.body_size) == (88, 36)
        assert len(layout.headline_lines) <= 2
        assert 35 <= sum(len(line.split()) for line in slide["lines"][1:]) <= 60
        assert slide["lines"][0].split()[-1] not in {"it", "fix", "now", "linked", "chained"}
    validate_output(s.AGENT_PHRASING, _state(STUDIO_LIGHT, VALUE_FIRST))


def test_value_first_slides_name_the_source_once_and_say_what_each_fact_means():
    for slide in VALUE_FIRST:
        headline, body = slide["lines"][0], " ".join(slide["lines"][1:])
        assert len(ATTRIBUTION.findall(" ".join(slide["lines"]))) <= 1, slide["index"]
        assert _headline_problems(headline) == [], slide["index"]
        assert 4 <= len(headline.split()) <= 8 and len(headline) <= 40, slide["index"]
        assert MEANING_LINK.search(body), slide["index"]
    # The one source is named on the first body slide, and the last slide
    # repeats that nobody else has confirmed its numbers. In between, not even
    # a headline with one of its numbers names it again.
    assert ATTRIBUTION.search(" ".join(VALUE_FIRST[0]["lines"]))
    assert "Hacktron's word alone" in VALUE_FIRST[-1]["lines"][-1]
    naming = [slide["index"] for slide in VALUE_FIRST if "Hacktron" in " ".join(slide["lines"])]
    assert naming == [VALUE_FIRST[0]["index"], VALUE_FIRST[-1]["index"]]
    assert not any(ATTRIBUTION.search(slide["lines"][0]) for slide in VALUE_FIRST[1:])
    # The checks catch the habit they replace.
    assert _headline_problems("They say it took under 72 hours") == [
        "'They say it took under 72 hours' opens on a pronoun",
        "'They say it took under 72 hours' uses 'it' before naming who it means"]
    assert not _headline_problems("Hacktron says its team earned $6,500")
    assert len(ATTRIBUTION.findall("They say it took 72 hours. Hacktron says that's how long, by their account.")) == 3


def test_copy_that_cannot_render_is_sent_back_naming_only_those_slides():
    design = CarouselDesign.model_validate(SMALL_BOX)
    failing = [slide["index"] for slide in VALUE_FIRST if not _renders(design, slide)]
    assert failing and len(failing) < len(VALUE_FIRST)
    with pytest.raises(ValueError) as caught:
        validate_output(s.AGENT_PHRASING, _state(SMALL_BOX, VALUE_FIRST))
    message = str(caught.value)
    for slide in VALUE_FIRST:
        assert (f"slide {slide['index']} does not fit" in message) == (slide["index"] in failing)
    assert "Cut about" in message and "keep every other slide as it was" in message


@pytest.mark.parametrize("raw", [STUDIO_LIGHT, SMALL_BOX, {}, {"inside": {"title_size": 51}}])
def test_the_fit_gate_agrees_with_the_renderer(raw):
    design = CarouselDesign.model_validate(raw)
    # Roomy designs fit every rewrite, so doubled paragraphs supply failures.
    stretched = [] if raw is SMALL_BOX else [
        dict(slide, lines=slide["lines"][:2] + [" ".join([slide["lines"][2]] * 2)]) for slide in VALUE_FIRST]
    for slide in VALUE_FIRST + stretched:
        gate_passes = not copy_problems(CopySet(slides=[slide], caption="c"), design, fit_only=True)
        assert gate_passes == _renders(design, slide), (raw, slide["index"])


def test_the_gate_checks_the_smallest_size_the_renderer_really_tries():
    # The renderer steps down 2px at a time, so an odd title size stops at 45,
    # one pixel above the 44px floor; checking 44 would pass copy that fails.
    assert [bl.minimum_headline_size(size) for size in (76, 88, 51, 44)] == [60, 70, 45, 44]


def test_the_budget_note_follows_the_saved_design():
    def body_target(note):
        return int(re.search(r"body to about (\d+) characters", note).group(1))
    roomy = copy_budget_note(STUDIO_LIGHT)
    small = copy_budget_note(CarouselDesign.model_validate(SMALL_BOX))
    assert 200 <= body_target(roomy) <= 320
    assert body_target(small) < body_target(roomy)
    assert "{" not in roomy + small and "}" not in roomy + small
    assert copy_budget_note({"inside": {"title_size": 5}}) == ""  # unmeasurable: prompt falls back


def test_split_and_repeated_sentences_are_sent_back_but_a_resume_keeps_saved_copy():
    state = _state({}, TELEGRAM, max_lines=4)
    with pytest.raises(ValueError) as caught:
        validate_output(s.AGENT_PHRASING, state)
    message = str(caught.value)
    assert "slide 2 line 2 stops mid-sentence" in message
    assert "slide 6 repeats a sentence from slide 3" in message
    assert "slide 3 repeats" not in message and "slide 2 line 3" not in message
    # A finished step is re-checked only for fit: a newer style rule must never
    # re-run (and re-bill) copy that already rendered.
    validate_output(s.AGENT_PHRASING, state, checkpoint=True)


def test_ordinary_endings_and_short_repeats_are_not_flagged():
    clean = [
        {"index": 2, "lines": ["It ships as Plan A", "The price is $20 a month. It works offline.", "Updates are free (for now)."]},
        {"index": 3, "lines": ["The price is fair", "It works offline."]},
    ]
    assert copy_problems(CopySet(slides=clean, caption="c"), None) == []
    split = deepcopy(clean)
    split[1]["lines"].append("It costs less than the old plan, and")
    assert copy_problems(CopySet(slides=split, caption="c"), None) == [
        "slide 3 line 3 stops mid-sentence (it ends with 'and'). Finish each sentence inside one line"]


def test_copy_straightens_curly_quotes_and_states_the_line_contract():
    copy = CopySet.model_validate({"slides": [{"index": 2, "lines": ["It\u2019s \u201cfast\u201d", "OK."]}],
                                   "caption": "OpenAI\u2019s \u2018Codex\u2019"})
    assert copy.slides[0].lines[0] == "It's \"fast\"" and copy.caption == "OpenAI's 'Codex'"
    payload = _to_litellm_response_format(CopySet, "openai/gpt-4.1-mini")
    schema = payload["json_schema"]["schema"]
    assert payload["json_schema"]["strict"] and _violations(schema) == []
    description = schema["$defs"]["SlideCopy"]["properties"]["lines"]["description"]
    assert "lines[0] is the slide headline" in description and "Never split one sentence" in description


@pytest.mark.asyncio
async def test_the_orchestrator_hands_the_budget_to_phrasing_and_repairs_overflow(monkeypatch):
    long_copy = {"slides": [{"index": 2, "lines": [
        "Local processing", " ".join(["The new release supports local processing."] * 40)]}], "caption": "Facts."}
    p = Pipeline()
    seen, rendered_with, planned_with = [], [], []
    original = Pipeline.drive

    async def drive(self, agent, child, ctx, holder):
        if child == s.AGENT_PLANNER:
            planned_with.append(self.state.get(s.K_COPY_BUDGET))
        if child == s.AGENT_TEMPLATE_DESIGN:
            rendered_with.append(str(self.state.get(s.K_REWORK_FEEDBACK) or ""))
        if child == s.AGENT_PHRASING:
            seen.append((self.state.get(s.K_COPY_BUDGET), str(self.state.get(s.K_REWORK_FEEDBACK) or "")))
            self.calls.append(child)
            copy = long_copy if len(seen) == 1 else outputs()[s.K_COPY]
            yield agent._progress(ctx, child, {s.K_COPY: deepcopy(copy)})
            return
        async for event in original(self, agent, child, ctx, holder):
            yield event

    monkeypatch.setattr(Pipeline, "drive", drive)
    await p.run()
    # The planner sizes its key points to the same budget the copywriter gets.
    assert len(planned_with) == 1 and planned_with[0] == seen[0][0]
    assert len(seen) == 2
    assert "characters" in seen[0][0] and "does not fit" not in seen[0][1]
    assert "Repair the phrasing output" in seen[1][1] and "slide 2 does not fit" in seen[1][1]
    assert p.calls.count(s.AGENT_TEMPLATE_DESIGN) == 1  # no image was paid for the copy that failed
    # The repair note was for phrasing; the renderer must not read it as a
    # reviewer asking to re-render only slide 2.
    assert rendered_with == [""]
    assert p.state[s.K_PHASE] == "review"


@pytest.mark.asyncio
async def test_resuming_after_phrasing_does_not_rewrite_copy_for_a_newer_style_rule():
    p = Pipeline()
    p.state.update(outputs())
    p.state[s.K_COPY] = {"slides": [{"index": 2, "lines": [
        "Local processing and", "The new release supports local processing."]}], "caption": "Facts."}
    p.state["generation_completed"] = ["research", "planner", "first_page_visual", "phrasing"]
    await p.run()
    assert s.AGENT_PHRASING not in p.calls and p.calls[0] == s.AGENT_TEMPLATE_DESIGN


class RecordingModel(BaseLlm):
    """Replace only the paid model and keep the instruction ADK really sends."""
    reply: str
    instructions: list = Field(default_factory=list)

    async def generate_content_async(self, llm_request, stream=False):
        self.instructions.append(str(llm_request.config.system_instruction))
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=self.reply)]))


async def _instruction_sent(agent, state: dict) -> None:
    """Run *agent* once on *state* so its recording model keeps the instruction."""
    sessions = InMemorySessionService()
    runner = Runner(app_name="budget_test", agent=SequentialAgent(name="step", sub_agents=[agent]),
                    session_service=sessions)
    await sessions.create_session(app_name="budget_test", user_id="test", session_id="run", state=state)
    try:
        async for _event in runner.run_async(user_id="test", session_id="run", new_message=types.Content(
                role="user", parts=[types.Part(text="Do your step.")])):
            pass
    finally:
        await runner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("with_budget", [True, False])
async def test_the_phrasing_agent_is_told_the_measured_budget(monkeypatch, with_budget):
    budget = copy_budget_note(STUDIO_LIGHT)
    copy = {"slides": VALUE_FIRST[:1], "caption": "Caption #a #b #c"}
    model = RecordingModel(model="offline-copy", reply=CopySet.model_validate(copy).model_dump_json())
    monkeypatch.setattr(phrasing, "resolve_role_model", lambda role: model)
    state = {s.K_PLAN: _state(STUDIO_LIGHT, VALUE_FIRST[:1])[s.K_PLAN], s.K_NEWS_ITEM: {"id": "news", "title": "A release"}}
    if with_budget:
        state[s.K_COPY_BUDGET] = budget
    await _instruction_sent(phrasing.build_phrasing_agent(), state)
    instruction = model.instructions[0]
    assert (budget in instruction) == with_budget
    assert "{copy_budget?}" not in instruction
    assert "If the line above is empty, keep each headline to about 40 characters" in instruction
    assert WRITING_STANDARD.strip() in instruction


@pytest.mark.asyncio
@pytest.mark.parametrize("with_budget", [True, False])
async def test_the_planner_is_told_the_same_budget(monkeypatch, with_budget):
    # Three facts per slide used to fill the box before any sentence could
    # say what they meant, so the planner sizes its key points to the budget.
    budget = copy_budget_note(STUDIO_LIGHT)
    plan = CarouselPlan.model_validate(_state(STUDIO_LIGHT, VALUE_FIRST[:1])[s.K_PLAN])
    model = RecordingModel(model="offline-plan", reply=plan.model_dump_json())
    monkeypatch.setattr(planner, "resolve_role_model", lambda role: model)
    state = {s.K_NEWS_ITEM: {"id": "news", "title": "A release"}}
    if with_budget:
        state[s.K_COPY_BUDGET] = budget
    await _instruction_sent(planner.build_planner_agent(), state)
    instruction = model.instructions[0]
    assert (budget in instruction) == with_budget
    assert "{copy_budget?}" not in instruction
    assert "If the line above is empty, assume about 40 characters for a headline" in instruction
    assert "A prose slide gets two" in instruction


@pytest.mark.parametrize("path,allowed", [
    ("skills/agents/phrasing.md", {"rework_feedback?", "recent_feedback_notes?", "carousel_plan?", "news_item?",
                                   "research_brief?", "copy_budget?"}),
    ("skills/agents/planner.md", {"news_item", "research_brief?", "rework_feedback?", "recent_feedback_notes?",
                                  "copy_budget?"}),
])
def test_prompt_placeholders_are_known_state_keys(path, allowed):
    # ADK raises KeyError on an unknown non-optional {name}; examples must not add one.
    text = (REPO / path).read_text(encoding="utf-8")
    assert set(re.findall(r"{+([^{}]*)}+", text)) == allowed
    assert "{" not in WRITING_STANDARD and "}" not in WRITING_STANDARD


def test_prompts_ask_for_explained_paragraphs_not_telegram_lines():
    phrasing_md = (REPO / "skills/agents/phrasing.md").read_text(encoding="utf-8")
    planner_md = (REPO / "skills/agents/planner.md").read_text(encoding="utf-8")
    phrasing_flat, planner_flat = " ".join(phrasing_md.split()), " ".join(planner_md.split())
    for gone in ("48 characters", "150 visible", "One thought per line", "adds visual bullets"):
        assert gone not in phrasing_md
    # The old 8-word cap on every line is gone; only the headline range remains.
    assert phrasing_flat.count("8 words") == phrasing_flat.count("4 to 8 words") == 1
    for kept in ("A detail and what it means", "at most two new terms", "A headline never settles a claim",
                 "Say whose claim it is", "what does this mean for me?", "last two words of the headline"):
        assert kept in phrasing_md
    for leak in ("Hacktron", "OpenAI", "libheif", "Bugcrowd"):  # style examples stay neutral
        assert leak not in phrasing_md and leak not in WRITING_STANDARD
    assert "punchy" not in planner_md and "Reader asks:" in planner_md and "Visual:" in planner_md
    assert "Never list the same fact on two slides" in planner_md
    # Attribution: named once, repeated only where it earns its place.
    for gone in ("carry it lightly on every slide", "The body of the same slide always says whose claim",
                 "shorten the explanation before you drop a fact", "4 to 10 words"):
        assert gone not in phrasing_flat
    for kept in ("at most one attribution per slide", "name it once on the first body slide",
                 'never on a bare "They say"', "give any pronoun in a headline its noun on the same slide",
                 "keep the payoff fact and the sentence that says what it means",
                 "The last body slide may point back to earlier facts in a few words",
                 "4 to 8 words, and within the headline length above",
                 "is a request for shorter slides",
                 "the first slide's attribution covers them, so do not name the source again",
                 "In between, do not name it again, not even in a headline"):
        assert kept in phrasing_flat, kept
    # The example headline stays inside the fallback budget it teaches.
    friend_headline = re.search(r'Friend \(.*?\):\s*"([^"]+)"', phrasing_md, re.S).group(1)
    assert len(friend_headline) <= 40
    assert "two or three facts" not in planner_flat
    for kept in ("{copy_budget?}", "A prose slide gets two", "is a request for shorter slides",
                 "may point back to earlier facts in a few words"):
        assert kept in planner_flat, kept
    assert "not X, but Y" in WRITING_STANDARD and "straight quotes" in WRITING_STANDARD
    assert "a concrete detail and what it means" in " ".join(WRITING_STANDARD.split())
    # It comes last, after any learned rules appended to a skill file, and
    # reads a learned "keep it short" as shorter slides, not as nothing.
    assert "past preference or learned rule" in " ".join(WRITING_STANDARD.split())
    assert "as a request for shorter slides" in " ".join(WRITING_STANDARD.split())
    assert "replaces any past preference" not in " ".join(WRITING_STANDARD.split())
    assert "punchy" not in feedback_router.DEFAULT_INSTRUCTION
    assert "punchy" not in (REPO / "skills/agents/feedback_router.md").read_text(encoding="utf-8")
    assert "punchy" not in planner._DEFAULT_INSTRUCTION


def _plan_slide(purpose):
    return SlidePlan(index=2, purpose=purpose, key_points=["k"])


def test_reader_questions_in_a_purpose_do_not_pull_in_the_cover_subject():
    copy = SlideCopy(index=2, lines=["The new tool", "It replaced the old one after a week."])
    asks = _plan_slide("Reader asks: who are the researchers? Payoff: three named people. Visual: process.")
    assert not template_design._needs_subject_reference(asks)
    assert not template_design._needs_subject_reference(_plan_slide("Reader asks: who are they? Payoff: three researchers."))
    assert not template_design._needs_subject_reference(_plan_slide("Reader asks: who are they. Payoff: three researchers."))
    assert template_design._layout_hint(copy, asks) == "process line"
    subject = _plan_slide("Reader asks: what is Codex? Payoff: OpenAI's coding tool. Visual: the real subject.")
    assert template_design._needs_subject_reference(subject)
    note = _plan_slide("Reader asks: how? Payoff: two flaws. Visual: statement pause. Note: identify the source.")
    assert not template_design._needs_subject_reference(note)
    assert template_design._layout_hint(copy, note) == "statement pause"
    # Older free-text purposes behave exactly as before.
    assert template_design._needs_subject_reference(_plan_slide("Introduce the subject and who is behind it"))
    assert not template_design._needs_subject_reference(_plan_slide("Show the attack chain as a clear process."))
    assert template_design._layout_hint(copy, _plan_slide("Show the chain.")) == "comparison"
    assert template_design._layout_hint(copy) == "comparison"
