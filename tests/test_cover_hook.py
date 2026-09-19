"""The cover hook must stay short enough to render big, and say something.

The first page is the only slide most people ever see, so its title carries
two obligations at once: it has to make a concrete point, and it has to be
legible in a feed. Those pull against each other - every extra word forces
the renderer to shrink the type - so the word budget is a readability rule,
not a style preference, and it is asserted here rather than left to prose in
three separate prompt files that used to disagree with one another.
"""
from pathlib import Path

import pytest

from app.agents import first_page_visual
from app.design_limits import (
    HOOK_MAX_CHARS,
    HOOK_MAX_WORDS,
    HOOK_MIN_READABLE_TITLE_SIZE,
)
from app.schemas import CarouselDesign
from app.tools import media_tools

REPO = Path(__file__).resolve().parents[1]

# A real hook at the budget: named subject, the change, a sourced number.
GOOD_HOOK = "GOOGLE AI READS A BOOK IN 30s"
# Seven words, so the word count passes - but wide enough that the renderer
# shrinks it anyway. This is the case word-counting alone cannot catch.
WIDE_HOOK = "META JUST OPEN SOURCED ITS BEST MODEL"
# What the old nine-word budget invited: abstract, unnamed, and far too long.
LONG_HOOK = "THE NEW MODEL UPDATE COULD CHANGE HOW EVERY DEVELOPER WRITES SOFTWARE TODAY"


def test_a_hook_within_the_budget_renders_at_the_full_cover_title_size():
    design = CarouselDesign()
    assert len(GOOD_HOOK.split()) == HOOK_MAX_WORDS
    assert len(GOOD_HOOK) <= HOOK_MAX_CHARS
    assert media_tools.fitted_title_size(GOOD_HOOK, design) == design.cover.title_size


def test_an_overlong_hook_is_shrunk_below_the_readable_floor():
    design = CarouselDesign()
    fitted = media_tools.fitted_title_size(LONG_HOOK, design)
    assert fitted < HOOK_MIN_READABLE_TITLE_SIZE


def test_a_wide_hook_inside_the_word_budget_is_still_shrunk():
    """Why the fitted size is the real gate and the word count is only a guide."""
    assert len(WIDE_HOOK.split()) <= HOOK_MAX_WORDS
    assert len(WIDE_HOOK) > HOOK_MAX_CHARS
    assert media_tools.fitted_title_size(WIDE_HOOK, CarouselDesign()) < HOOK_MIN_READABLE_TITLE_SIZE


def test_a_wide_hook_is_flagged_even_though_its_word_count_passes():
    warnings = first_page_visual.hook_warnings(WIDE_HOOK, "", CarouselDesign())
    assert any(str(HOOK_MIN_READABLE_TITLE_SIZE) in w for w in warnings)
    assert not any(f"{HOOK_MAX_WORDS} word" in w for w in warnings)


def test_an_overlong_hook_still_renders_rather_than_failing_the_run():
    block = media_tools._render_title_block(LONG_HOOK, "", CarouselDesign())
    assert block.size == (1080, 1350)
    assert block.getbbox() is not None  # something was actually painted


def test_a_hook_over_the_word_budget_is_flagged():
    warnings = first_page_visual.hook_warnings(LONG_HOOK, "", CarouselDesign())
    assert any(f"{HOOK_MAX_WORDS} word" in w for w in warnings)


def test_a_hook_within_budget_draws_no_complaints():
    assert first_page_visual.hook_warnings(GOOD_HOOK, "IN 30s", CarouselDesign()) == []


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
def test_every_hook_rule_source_states_the_same_word_budget(source):
    """Three files used to claim authority over the hook and all said "9".

    The planner obediently filled that budget, and the renderer then shrank
    the result. Whatever the cap is, every place that states it must agree.
    """
    text = (REPO / source).read_text(encoding="utf-8")
    assert f"{HOOK_MAX_WORDS} words" in text
    assert "9 words" not in text
    assert "nine" not in text.lower()


def test_the_character_target_reaches_the_planner_through_cover_style():
    cover_style = (REPO / "skills/cover-style.md").read_text(encoding="utf-8")
    assert f"{HOOK_MAX_CHARS} characters" in cover_style


def test_the_shared_writing_standard_asks_for_a_concrete_cover_not_just_a_safe_one():
    """The standard only ever said what to avoid, so the hooks came out flat."""
    from app.editorial_voice import WRITING_STANDARD

    standard = WRITING_STANDARD.lower()
    assert "name the real subject" in standard   # the do
    assert "mystery hook" in standard            # the don't, still enforced
