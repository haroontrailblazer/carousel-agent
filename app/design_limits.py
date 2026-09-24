"""Supported carousel size and the limit frozen into a task's design."""
from typing import Any

# The current renderer supports a cover, at least one body slide, and a CTA.
MIN_CAROUSEL_SLIDES = 3
MAX_SUPPORTED_SLIDES = 10


def design_slide_limit(state: Any) -> int:
    from app.schemas import CarouselDesign
    from app.state import K_DESIGN, get_model

    design = get_model(state, K_DESIGN, CarouselDesign)
    return design.max_slides if design else MAX_SUPPORTED_SLIDES


# --- Cover hook -------------------------------------------------------------
# The cover title renders at the design's title_size and the renderer shrinks
# it until it fits the saved title box, so length is a readability budget, not
# a style preference. Measured against the shipped condensed face: a hook of
# 40 characters or fewer holds at least 80% of the design's title size (three
# lines on the 100 px saved designs, two on the 128 px default), and a hook
# past roughly 45 characters starts to read small.
#
# The old budget was 7 words and 30 characters. That was too tight to carry
# the stake of a story, so hooks came out as bare stat lines ("GPT-6 ASTRA
# REFUSED 2 OF 100") that a stranger could not decode. Nine words leaves room
# for who, what happened, and why it matters.
HOOK_MAX_WORDS = 9
HOOK_MAX_CHARS = 40
# A hook fitted below this share of the design's own title size has lost
# enough size to stop reading in a feed. It is relative on purpose: the saved
# designs use a 100 px title, and a fixed pixel floor above that flagged every
# hook as "too wide", however short.
HOOK_MIN_READABLE_FRACTION = 0.8


def hook_min_readable_size(title_size: int) -> int:
    """The smallest fitted size at which a cover hook still reads in a feed."""
    return round(title_size * HOOK_MIN_READABLE_FRACTION)
