import pytest
from PIL import Image, ImageChops
from pydantic import ValidationError

from app.schemas import CarouselDesign, SlideDesign
from app.text_spacing import text_width
from app.tools.brand_layout import _balanced_wrap, _greedy_wrap, _fit_typography_layout, apply_slide_typography, design_font
from app.tools.media_tools import _render_title_block, _wrap_title


def test_spacing_round_trip_and_legacy_defaults():
    design = CarouselDesign(cover={"letter_spacing": -1, "word_spacing": 8, "line_height": 150})
    saved = CarouselDesign.model_validate_json(design.model_dump_json())
    assert (saved.cover.letter_spacing, saved.cover.word_spacing, saved.cover.line_height) == (-1, 8, 150)
    assert (saved.inside.letter_spacing, saved.inside.word_spacing, saved.inside.line_height) == (0, 0, 120)


@pytest.mark.parametrize("field,value", [("letter_spacing", -3), ("letter_spacing", 13), ("word_spacing", -1), ("word_spacing", 33), ("line_height", 99), ("line_height", 201)])
def test_spacing_limits(field, value):
    with pytest.raises(ValidationError):
        SlideDesign(**{field: value})


@pytest.mark.parametrize("wrap", [lambda text, font, width, letter, word: _balanced_wrap(text, font, width, 3, letter, word), _greedy_wrap, _wrap_title])
def test_wrapping_measures_added_spacing(wrap):
    font = design_font(76, "sans", bold=True)
    title = "CLEAR IDEAS"
    width = text_width(font, title) + 1
    assert wrap(title, font, width, 0, 0) == [title]
    assert wrap(title, font, width, 4, 12) == ["CLEAR", "IDEAS"]
    assert text_width(font, title, 4, 12) - text_width(font, title) == pytest.approx(4 * (len(title) - 1) + 12)


def test_line_height_changes_vertical_fit():
    small = _fit_typography_layout("CLEAR IDEAS", ["One supporting line."], 904, line_height=120)
    large = _fit_typography_layout("CLEAR IDEAS", ["One supporting line."], 904, line_height=160)
    assert large.total_height > small.total_height
    assert large.head_line_height == round(large.headline_size * 1.6)
    assert large.body_line_height == round(large.body_size * 1.6)


@pytest.mark.parametrize("field,value", [("letter_spacing", 8), ("word_spacing", 24), ("line_height", 170)])
def test_cover_and_body_pixels_use_each_spacing_control(field, value):
    base = CarouselDesign(cover={"title_size": 76}, inside={"title_size": 76})
    changed = base.model_copy(deep=True)
    setattr(changed.cover, field, value)
    setattr(changed.inside, field, value)
    cover_before = _render_title_block("A CLEAR IDEA", "IDEA", base)
    cover_after = _render_title_block("A CLEAR IDEA", "IDEA", changed)
    assert ImageChops.difference(cover_before, cover_after).getbbox() is not None
    image = Image.new("RGB", (1080, 1350), "white")
    body_before = apply_slide_typography(image, "A CLEAR IDEA", ["A supporting thought."], design=base)
    body_after = apply_slide_typography(image, "A CLEAR IDEA", ["A supporting thought."], design=changed)
    assert ImageChops.difference(body_before, body_after).getbbox() is not None
