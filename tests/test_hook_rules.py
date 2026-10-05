"""Cover hooks are drafted as candidates, and a flat report line goes back once.

The planner's rules already asked for a stake and a contrast, yet real runs
shipped "ANTHROPIC RUNS A WET LAB" and "AI MODELS NOW COMPETE ON COST AND
SPEED": a subject, a verb, an object, nothing for the reader. The research
brief's suggested angle for those stories held the contrast. These tests pin
the check that catches that shape, the drafting it requires, and the escape
hatches that keep it from ever overruling a reviewer or stopping a run.
"""
from pathlib import Path

import pytest

from app import state as s
from app.hook_rules import hook_problems, is_report_line
from app.pipeline_outputs import validate_output
from app.schemas import CarouselDesign, CarouselPlan
from web_api.routes_runs import _hook_options

REPO = Path(__file__).resolve().parents[1]

# Hooks real runs shipped, each a bare report line.
REPORT_LINES = [
    "ANTHROPIC RUNS A WET LAB",
    "ANTHROPIC SETS UP A WET LAB",
    "GOOGLE OPENS NEW AI TO CYBER TEAMS FIRST",
    "AI MODELS NOW COMPETE ON COST AND SPEED",
    "AI MODELS RACE ON SPEED AND OPEN WEIGHTS",
    "OPENAI AGENTS PUT USER IMAGES ONLINE",
    "OPENAI AGENT GOT AROUND MEDICARE BLOCKS",
    "CLAUDE HELPED BUG HUNTERS HACK OPENAI",
]
# Hooks that already carry a stake, a second beat, a number or a question.
GOOD_HOOKS = [
    "THE CHATBOT SAID NO. THE ROBOT DIDN'T.",
    "DEFENDERS GET GOOGLE'S NEW AI. YOU WAIT.",
    "53 CHATGPT PHOTOS LEAKED. NO HACKER NEEDED.",
    "AN AI TEST BROKE IN. NOBODY NOTICED FOR WEEKS.",
    "YOUR APP DOESN'T NEED GPT FOR YES OR NO",
    "OPENAI DOTS KEEP WORKING AFTER YOU LEAVE",
    "Amazon blocks shopping agents. Shopify just let them finish checkout.",
    "WOULD YOU LET AN AI PAY FOR YOU?",
    "XIAOMI CHARGES 10X MORE FOR FASTER AI",
]


@pytest.mark.parametrize("hook", REPORT_LINES)
def test_a_report_line_is_caught(hook):
    assert is_report_line(hook)


@pytest.mark.parametrize("hook", GOOD_HOOKS)
def test_a_hook_with_a_stake_or_a_second_beat_is_left_alone(hook):
    assert not is_report_line(hook)


def _plan(title, candidates=None, highlight=""):
    if candidates is None:
        candidates = [
            {"text": title, "highlight": highlight, "lever": "two_beat_contrast"},
            {"text": "YOUR APP DOESN'T NEED GPT FOR YES OR NO", "highlight": "YES OR NO", "lever": "reader_stake"},
            {"text": "53 CHATGPT PHOTOS LEAKED. NO HACKER NEEDED.", "highlight": "NO HACKER", "lever": "number_with_meaning"},
            {"text": "DEFENDERS GET GOOGLE'S NEW AI. YOU WAIT.", "highlight": "YOU WAIT", "lever": "winner_loser"},
        ]
    return CarouselPlan.model_validate({
        "style": "prose", "slide_count": 3, "hook_title": title, "hook_highlight": highlight,
        "hook_candidates": candidates,
        "slides": [{"index": 2, "purpose": "p", "key_points": ["k"]}],
    })


def _saved_design():
    design = CarouselDesign()
    design.cover.title_size = 100
    return design


def test_a_drafted_two_beat_hook_passes():
    plan = _plan("THE CHATBOT SAID NO. THE ROBOT DIDN'T.", highlight="THE ROBOT DIDN'T")
    assert hook_problems(plan, _saved_design()) == []


def test_a_report_line_is_sent_back_with_the_way_to_fix_it():
    problems = hook_problems(_plan("ANTHROPIC RUNS A WET LAB"), _saved_design())
    assert any("news report" in p and "suggested_angle" in p for p in problems)


def test_candidates_that_were_never_drafted_are_sent_back():
    lone = [{"text": "THE CHATBOT SAID NO. THE ROBOT DIDN'T.", "lever": "two_beat_contrast"}]
    problems = hook_problems(_plan("THE CHATBOT SAID NO. THE ROBOT DIDN'T.", lone), _saved_design())
    assert any("at least 4 drafted hooks" in p for p in problems)


def test_the_title_must_be_one_of_the_candidates():
    plan = _plan("THE CHATBOT SAID NO. THE ROBOT DIDN'T.")
    plan.hook_title = "A LINE NOBODY DRAFTED. IT JUST APPEARED."
    assert any("one of the hook_candidates" in p for p in hook_problems(plan, _saved_design()))


def test_a_hook_too_long_for_the_design_is_sent_back():
    long_hook = "THE NEW MODEL UPDATE COULD CHANGE HOW EVERY DEVELOPER WRITES SOFTWARE"
    problems = hook_problems(_plan(long_hook), _saved_design())
    assert any("words; the budget is" in p for p in problems)


def test_a_title_the_request_dictated_is_never_second_guessed():
    dictated = "ANTHROPIC RUNS A WET LAB"
    plan = _plan(dictated, [{"text": dictated, "lever": "user_title"}])
    assert hook_problems(plan, _saved_design()) == []


def _state(plan: CarouselPlan) -> dict:
    return {s.K_PLAN: plan.model_dump(mode="json"), s.K_DESIGN: _saved_design().model_dump(mode="json")}


def test_the_planner_output_check_sends_a_flat_hook_back_once():
    state = _state(_plan("ANTHROPIC RUNS A WET LAB"))
    with pytest.raises(ValueError, match="Rewrite the cover hook"):
        validate_output(s.AGENT_PLANNER, state)


@pytest.mark.parametrize("flag", ["checkpoint", "final_attempt", "under_rework"])
def test_the_hook_check_never_blocks_a_resume_a_last_attempt_or_a_reviewer(flag):
    state = _state(_plan("ANTHROPIC RUNS A WET LAB"))
    validate_output(s.AGENT_PLANNER, state, **{flag: True})  # no raise


def test_the_hook_is_written_after_the_slides_and_the_candidates_before_it():
    """Structured output is written in field order: plan the story, draft, then pick."""
    order = list(CarouselPlan.model_json_schema()["properties"])
    assert order.index("slides") < order.index("hook_candidates") < order.index("hook_title")


def test_the_review_screen_gets_the_other_drafted_hooks():
    plan = _plan("THE CHATBOT SAID NO. THE ROBOT DIDN'T.", highlight="THE ROBOT DIDN'T").model_dump(mode="json")
    plan["hook_candidates"].append({"text": "AN AI TEST BROKE IN.", "highlight": "NOT IN THE TEXT", "lever": "consequence"})
    options = _hook_options(plan, "the chatbot said no. the robot didn't.")
    texts = [o["text"] for o in options]
    assert "THE CHATBOT SAID NO. THE ROBOT DIDN'T." not in texts  # already on the cover
    assert "DEFENDERS GET GOOGLE'S NEW AI. YOU WAIT." in texts
    broken = next(o for o in options if o["text"] == "AN AI TEST BROKE IN.")
    assert broken["highlight"] == ""  # a highlight that is not in the text is dropped


def test_the_prompts_teach_the_method():
    planner_md = (REPO / "skills/agents/planner.md").read_text(encoding="utf-8")
    cover_style = (REPO / "skills/cover-style.md").read_text(encoding="utf-8")
    for lever in ("curiosity_gap", "two_beat_contrast", "reader_stake", "number_with_meaning", "winner_loser", "consequence", "question"):
        assert lever in planner_md and lever in cover_style
    assert "suggested_angle" in planner_md and "suggested_angle" in cover_style
    assert "{carousel_plan?}" in planner_md
    assert "{hook_examples?}" in planner_md  # the reviewer's own choices reach the planner
    assert "Never ship a report line" in planner_md and "Never a report line" in cover_style
