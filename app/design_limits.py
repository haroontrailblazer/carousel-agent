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
