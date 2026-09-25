"""Text budget and pre-render checks for inside-slide copy.

Code typesets the phrasing agent's copy verbatim onto the saved design
(brand_layout.apply_slide_typography), after the slide's image has already
been paid for. Copy that did not fit used to surface only there, and it
stopped the run. This module measures the same typesetter before rendering:

- copy_budget_note: one plain sentence for the phrasing prompt that says how
  much text the saved design holds at full size.
- copy_problems: repair instructions for copy that cannot render on the saved
  design, plus narrow shape checks (a sentence split across entries, a
  sentence repeated from another slide, and on prose slides, fragments or a
  list of unlinked one-liners instead of paragraphs).

Both go through brand_layout.fit_inside_copy at fixed size pairs, so they are
fast and can never disagree with the renderer.
"""
from __future__ import annotations

import math
import re
from functools import lru_cache
from typing import Any

from app.schemas import CarouselDesign, CopySet, SlideCopy, SlideDesign
from app.tools import brand_layout

# Neutral measuring text with ordinary English word lengths. It is never shown
# to a model, so it only has to wrap like real copy does.
_SAMPLE_WORDS = (
    "The company says the new model runs on a normal laptop, which means people "
    "can use it without sending their files to a server. That matters for anyone "
    "who works with private data, because the files never leave the machine. It "
    "is slower than the cloud version, so long jobs still take a while, but the "
    "team says most everyday tasks finish in a few seconds on a recent computer "
    "and the price stays the same for every plan they sell today."
).split() * 3
_SAMPLE_HEADLINE = "The new phone chip lasts twice as long on one charge in tests".split()

# Share of the full-size ceiling the prompt asks for. Real copy carries long
# names and terms that wrap worse than the sample, so the gate stays the
# guarantee and this only keeps a first draft comfortably inside it.
_TARGET_SHARE = 0.8
# A roomy box could hold a 60-character headline, but a headline that long
# stops reading as one quick statement. The body is still measured against
# the full two-line headline, so the cap only makes the advice stricter.
_HEADLINE_CAP = 50

# An entry ending like this is half a sentence: the rest was put in the next
# entry, which the renderer draws as a separate block.
_SPLIT_ENDING = re.compile(r"(?:[,;:]|\b(?:and|or|but|the|a|an))$")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")
_MIN_REPEATED_WORDS = 4
# A prose slide explains: a body entry shorter than this is a label or half
# a thought on its own line, the telegram shape the phrasing prompt replaced.
# One short entry after a real paragraph is fine (a plain caveat, or the
# sentence that says what it means); the shape is a slide of them.
_MIN_PARAGRAPH_WORDS = 10
# Entries that are each one sentence this short, with nothing linking them,
# read as a list of separate statements even when none is a fragment.
_SHORT_SENTENCE_WORDS = 15
_LINK_WORDS = re.compile(
    r"\b(?:because|so|which|means?|meaning|but|since|although|though|while|unless|if|therefore)\b",
    re.IGNORECASE,
)


def _full_sizes(design: CarouselDesign) -> list[tuple[int, int]]:
    return [(design.inside.title_size, brand_layout.BODY_FONT_SIZE)]


def _smallest_sizes(design: CarouselDesign) -> list[tuple[int, int]]:
    # The most compact pair the renderer can choose: copy that fails here
    # fails at every size it would try.
    return [(brand_layout.minimum_headline_size(design.inside.title_size), brand_layout.BODY_MIN_FONT_SIZE)]


def _fits(design: CarouselDesign, headline: str, body: list[str], sizes) -> bool:
    try:
        brand_layout.fit_inside_copy(design, headline, body, sizes=sizes)
        return True
    except ValueError:
        return False


def _sample_paragraphs(words: int, count: int) -> list[str]:
    per = max(1, words // count)
    return [" ".join(_SAMPLE_WORDS[i * per:(i + 1) * per]) + "." for i in range(count)]


def _body_ceiling(design: CarouselDesign, headline: str, count: int, sizes) -> int:
    """Most body characters (in *count* paragraphs) that still fit at *sizes*."""
    low, high = 0, len(_SAMPLE_WORDS) // count
    while low < high:
        middle = (low + high + 1) // 2
        if _fits(design, headline, _sample_paragraphs(middle, count), sizes):
            low = middle
        else:
            high = middle - 1
    return sum(len(paragraph) for paragraph in _sample_paragraphs(low, count)) if low else 0


def _headline_capacity(design: CarouselDesign, max_lines: int) -> str:
    """Longest sample headline that wraps into *max_lines* at full size."""
    best = ""
    for count in range(1, len(_SAMPLE_HEADLINE) + 1):
        text = " ".join(_SAMPLE_HEADLINE[:count])
        try:
            layout = brand_layout.fit_inside_copy(design, text, [], sizes=_full_sizes(design))
        except ValueError:
            break
        if len(layout.headline_lines) > max_lines:
            break
        best = text
    return best


@lru_cache(maxsize=32)
def _budget_for(inside_json: str) -> tuple[int, int, int, int, int] | None:
    """(headline characters, headline lines, body target, paragraphs, hard body limit).

    None when the box is too small for a paragraph of about 100 characters
    at full size. Keyed on the inside-slide design only: nothing else
    changes the layout.
    """
    design = CarouselDesign(inside=SlideDesign.model_validate_json(inside_json))
    for headline_lines in (2, 1):
        headline = _headline_capacity(design, headline_lines) or _SAMPLE_HEADLINE[0]
        for paragraphs in (2, 1):
            target = int(_body_ceiling(design, headline, paragraphs, _full_sizes(design)) * _TARGET_SHARE) // 10 * 10
            # Two paragraphs only when each has room for about 100 characters,
            # a detail or its meaning rather than a fragment (the prose check
            # wants 10 words). Otherwise one paragraph holds more: every break
            # costs a gap and a partly filled line.
            if target < 100 * paragraphs:
                continue
            hard = _body_ceiling(design, headline, paragraphs, _smallest_sizes(design))
            return min(len(headline), _HEADLINE_CAP), headline_lines, target, paragraphs, hard
    return None


def _note_for(inside_json: str) -> str:
    budget = _budget_for(inside_json)
    if budget is None:
        return (
            "This design has a small text box: keep each headline to a few words and "
            "each slide's body to one short sentence."
        )
    headline, headline_lines, target, paragraphs, hard = budget
    lines_text = "two lines" if headline_lines == 2 else "one line"
    shape = "one or two paragraphs" if paragraphs == 2 else "one paragraph"
    # The planner reads this note before it picks the style, and phrasing
    # gets both shapes too: the total is the same either way.
    return (
        f"On this design, keep each headline to about {headline} characters "
        f"({lines_text} at full size) and each slide's body to about {target} "
        f"characters in total, roughly {target // 6} words: as prose, in {shape}; "
        "as points, up to three items that share that total. That keeps the "
        f"type at full size. Past about {hard} characters of body text the "
        "slide cannot render at all, and it will be sent back to you."
    )


def copy_budget_note(design: Any) -> str:
    """Plain-English text budget of the saved design for the phrasing prompt.

    *design* is a CarouselDesign, its stored dict, or None for the default.
    Returns an empty string when it cannot be measured, so a measuring
    problem never blocks a run; the prompt then falls back to fixed numbers.
    """
    try:
        model = CarouselDesign.model_validate(design) if design is not None else CarouselDesign()
        return _note_for(model.inside.model_dump_json())
    except Exception:  # noqa: BLE001 - the note is advice; the gate is the guarantee
        return ""


def _fit_problem(design: CarouselDesign, slide: SlideCopy) -> str | None:
    """None when *slide* can render on *design*; otherwise how to repair it."""
    lines = [line.strip() for line in slide.lines if line and line.strip()]
    if not lines:
        return None
    headline, body = lines[0], lines[1:]
    sizes = _smallest_sizes(design)
    if _fits(design, headline, body, sizes):
        return None
    top, bottom = brand_layout.inside_text_box(design.inside)[3:]
    unbounded = 10 ** 6
    try:
        head_height = brand_layout.fit_inside_copy(design, headline, [], sizes=sizes, panel_bottom=unbounded).total_height
    except ValueError:
        return (
            f"slide {slide.index}'s headline does not fit this design's text box in three "
            "lines. Shorten the headline"
        )
    if head_height > bottom - top:
        return f"slide {slide.index}'s headline alone is taller than this design's text box. Shorten the headline"
    try:
        layout = brand_layout.fit_inside_copy(design, headline, body, sizes=sizes, panel_bottom=unbounded)
    except ValueError:
        return (
            f"slide {slide.index} has a word wider than this design's text box. Break it up "
            "or use a shorter word"
        )
    # Characters scale with the pixels the body text itself uses; the 10%
    # margin covers the partly filled last line of each paragraph.
    body_height = max(1, layout.total_height - head_height - layout.section_gap)
    overflow = layout.total_height - (bottom - top)
    body_chars = sum(len(line) for line in body)
    cut = max(20, math.ceil(body_chars * overflow / body_height * 1.1 / 10) * 10)
    return (
        f"slide {slide.index} does not fit this design's text box even at the smallest "
        f"readable size. Cut about {cut} characters from slide {slide.index} and keep its "
        "concrete detail and what it means"
    )


def _split_problems(slide: SlideCopy) -> list[str]:
    problems = []
    for position, line in enumerate(slide.lines, start=1):
        text = line.strip().rstrip("\"')]").rstrip()
        if text and _SPLIT_ENDING.search(text):
            problems.append(
                f"slide {slide.index} line {position} stops mid-sentence (it ends with "
                f"{text.split()[-1]!r}). Finish each sentence inside one line"
            )
    return problems


def _position_list(positions: list[int]) -> str:
    if len(positions) == 1:
        return f"line {positions[0]} is"
    return "lines " + ", ".join(map(str, positions[:-1])) + f" and {positions[-1]} are"


def _prose_problems(slide: SlideCopy) -> list[str]:
    """Telegram-shaped body text on a prose slide; empty for real paragraphs."""
    body = [(position, line.strip()) for position, line in enumerate(slide.lines, start=1)
            if position > 1 and line.strip()]
    if not body:
        return []
    short = [position for position, line in body if len(line.split()) < _MIN_PARAGRAPH_WORDS]
    if len(short) >= 2 or len(short) == len(body):
        return [
            f"slide {slide.index} reads as separate fragments ({_position_list(short)} under "
            f"{_MIN_PARAGRAPH_WORDS} words). Join its lines into one or two paragraphs that "
            "give the detail and say what it means"
        ]
    one_liners = all(
        len(_SENTENCE_BREAK.split(line)) == 1 and len(line.split()) < _SHORT_SENTENCE_WORDS
        for _position, line in body
    )
    if len(body) > 1 and one_liners and not any(_LINK_WORDS.search(line) for _position, line in body):
        return [
            f"slide {slide.index} reads as a list of separate statements. Join them into one "
            "paragraph that links the detail to what it means (because, so, which means)"
        ]
    return []


def _has_paragraph_room(design: CarouselDesign | None) -> bool:
    """False only for a box the budget note tells to hold one short sentence."""
    if design is None:
        return True
    try:
        return _budget_for(design.inside.model_dump_json()) is not None
    except Exception:  # noqa: BLE001 - unmeasurable: the prompt's fixed budget asks for paragraphs
        return True


def _sentence_key(sentence: str) -> str:
    return " ".join(re.sub(r"[^\w\s$%]", " ", sentence.lower()).split())


def _repeat_problems(copy: CopySet) -> list[str]:
    first_seen: dict[str, int] = {}
    problems = []
    for slide in sorted(copy.slides, key=lambda item: item.index):
        for line in slide.lines:
            for sentence in _SENTENCE_BREAK.split(line.strip()):
                key = _sentence_key(sentence)
                if len(key.split()) < _MIN_REPEATED_WORDS:
                    continue
                earlier = first_seen.setdefault(key, slide.index)
                if earlier != slide.index:
                    problems.append(
                        f"slide {slide.index} repeats a sentence from slide {earlier} "
                        f"({sentence.strip()!r}). Replace it with something the reader has not seen yet"
                    )
    return problems


def copy_problems(
    copy: CopySet, design: CarouselDesign | None, *, fit_only: bool = False, style: str = "",
) -> list[str]:
    """Everything that must change before *copy* can be rendered.

    ``design`` None skips the fit check (a design that cannot be read is
    template_design's error to report). ``fit_only`` keeps only the fit check,
    for re-checking a finished step on resume: a shape rule added later must
    never re-run, and re-bill, copy that already rendered. ``style`` is the
    plan's style; "prose" also sends back telegram-shaped slides (fragments
    or a list of unlinked one-liners), except on a box too small for a
    paragraph, where the budget note itself asks for one short sentence.
    """
    slides = sorted(copy.slides, key=lambda item: item.index)
    problems = [] if fit_only else [problem for slide in slides for problem in _split_problems(slide)]
    if not fit_only and style == "prose" and _has_paragraph_room(design):
        problems += [problem for slide in slides for problem in _prose_problems(slide)]
    if design is not None:
        problems += [problem for problem in (_fit_problem(design, slide) for slide in slides) if problem]
    if not fit_only:
        problems += _repeat_problems(copy)
    return problems
