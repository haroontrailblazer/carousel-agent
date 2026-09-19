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
# a style preference. Measured against the shipped condensed face at the
# default 128 px cover: a hook of 30 characters or fewer reliably holds full
# size on two lines, and past roughly 36 it drops under 100 px. Word count
# alone does not predict this (a wide 7-word hook shrinks while a narrow
# 8-word one does not), so the character target and the fitted-size floor
# below are what actually protect legibility.
HOOK_MAX_WORDS = 7
HOOK_MAX_CHARS = 30
# A hook fitted below this has lost enough size to stop reading in a feed.
HOOK_MIN_READABLE_TITLE_SIZE = 112
