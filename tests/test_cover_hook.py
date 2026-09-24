"""The cover hook must stay short enough to render big, and say something.

The first page is the only slide most people ever see, so its title carries
two obligations at once: it has to make a point a stranger cares about, and
it has to be legible in a feed. Those pull against each other - every extra
word forces the renderer to shrink the type - so the budget is a readability
rule, measured against the rendered size, and asserted here rather than left
to prose in several prompt files that used to disagree with one another.
"""
from pathlib import Path

import pytest

from app.agents import first_page_visual
from app.design_limits import (
    HOOK_MAX_CHARS,
    HOOK_MAX_WORDS,
    hook_min_readable_size,
)
from app.schemas import CarouselDesign
from app.tools import media_tools

REPO = Path(__file__).resolve().parents[1]

# A hook at the budget that carries a stake, not just a stat.
GOOD_HOOK = "GPT-6 SAID YES TO 98 UNSAFE ROBOT ORDERS"
# Under the word budget, but wide enough that the renderer shrinks it anyway.
# This is the case word-counting alone cannot catch.
WIDE_HOOK = "ANTHROPIC'S INTERPRETABILITY BREAKTHROUGH EXPLAINED"
# Abstract, unnamed, and far too long.
LONG_HOOK = "THE NEW MODEL UPDATE COULD CHANGE HOW EVERY DEVELOPER WRITES SOFTWARE TODAY"


def _saved_design() -> CarouselDesign:
    """The saved workspace designs render the cover title at 100 px."""
    design = CarouselDesign()
    design.cover.title_size = 100
    return design


@pytest.mark.parametrize("design", [CarouselDesign(), _saved_design()], ids=["default", "saved"])
def test_a_hook_within_the_budget_stays_above_the_readable_floor(design):
    assert len(GOOD_HOOK.split()) <= HOOK_MAX_WORDS
    assert len(GOOD_HOOK) <= HOOK_MAX_CHARS
    fitted = media_tools.fitted_title_size(GOOD_HOOK, design)
    assert fitted >= hook_min_readable_size(design.cover.title_size)


def test_the_readable_floor_follows_the_design_title_size():
    """A fixed 112 px floor flagged every hook on the 100 px saved designs.

    That constant warning told the agents their hooks were too wide however
    short they were, which pushed the copy toward cryptic stat lines.
    """
    design = _saved_design()
    short = "LAYA ANSWERS LOCALLY IN 33 MS"
    assert media_tools.fitted_title_size(short, design) == 100
    assert first_page_visual.hook_warnings(short, "33 MS", design) == []


def test_an_overlong_hook_is_shrunk_below_the_readable_floor():
    design = CarouselDesign()
    fitted = media_tools.fitted_title_size(LONG_HOOK, design)
    assert fitted < hook_min_readable_size(design.cover.title_size)


def test_a_wide_hook_is_flagged_even_though_its_word_count_passes():
    design = CarouselDesign()
    assert len(WIDE_HOOK.split()) <= HOOK_MAX_WORDS
    assert media_tools.fitted_title_size(WIDE_HOOK, design) < hook_min_readable_size(
        design.cover.title_size
    )
    warnings = first_page_visual.hook_warnings(WIDE_HOOK, "", design)
    assert any("readable floor" in w for w in warnings)
    assert not any(f"{HOOK_MAX_WORDS} word" in w for w in warnings)


def test_an_overlong_hook_still_renders_rather_than_failing_the_run():
    block = media_tools._render_title_block(LONG_HOOK, "", CarouselDesign())
    assert block.size == (1080, 1350)
    assert block.getbbox() is not None  # something was actually painted


def test_a_hook_over_the_word_budget_is_flagged():
    warnings = first_page_visual.hook_warnings(LONG_HOOK, "", CarouselDesign())
    assert any(f"{HOOK_MAX_WORDS} word" in w for w in warnings)


def test_a_hook_within_budget_draws_no_complaints():
    assert first_page_visual.hook_warnings(
        GOOD_HOOK, "98 UNSAFE ROBOT ORDERS", CarouselDesign()
    ) == []


def test_a_highlight_that_is_not_in_the_title_is_still_reported():
    warnings = first_page_visual.hook_warnings(GOOD_HOOK, "NOT PRESENT", CarouselDesign())
    assert any("verbatim substring" in w for w in warnings)


@pytest.mark.parametrize("source", [
    "skills/cover-style.md",
    "skills/agents/planner.md",
    "skills/agents/first_page_visual.md",
    "app/agents/planner.py",
    "app/agents/first_page_visual.py",
])
def test_every_hook_rule_source_states_the_same_budget(source):
    """Several files claim authority over the hook; they must agree."""
    text = (REPO / source).read_text(encoding="utf-8")
    assert f"{HOOK_MAX_WORDS} words" in text
    assert f"{HOOK_MAX_CHARS} characters" in text
    assert "7 words" not in text
    assert "30 characters" not in text


@pytest.mark.parametrize("source", ["skills/cover-style.md", "skills/agents/planner.md"])
def test_the_hook_rules_ask_for_a_stake_not_a_bare_stat(source):
    """The 7-word, name-plus-number rules produced lines like GPT-6 ASTRA
    REFUSED 2 OF 100: accurate, and meaningless to a stranger."""
    text = (REPO / source).read_text(encoding="utf-8").lower()
    assert "stranger test" in text
    assert "stake" in text
    assert "contrast" in text


def test_the_shared_writing_standard_asks_for_a_concrete_cover_not_just_a_safe_one():
    """The standard only ever said what to avoid, so the hooks came out flat."""
    from app.editorial_voice import WRITING_STANDARD

    standard = WRITING_STANDARD.lower()
    assert "name the real subject" in standard   # the do
    assert "stake" in standard                    # and why it matters
    assert "mystery hook" in standard            # the don't, still enforced
