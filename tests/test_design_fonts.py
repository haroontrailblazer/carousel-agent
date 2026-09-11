"""Saved font choices must resolve identically on Windows, Linux and the web."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image, ImageFont

from app.schemas import CarouselDesign
from app.tools import brand_layout, image_gen, media_tools


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "frontend/public/fonts/carousel/manifest.json").read_text())


@pytest.mark.parametrize("family", MANIFEST)
@pytest.mark.parametrize("bold", [False, True])
def test_bundled_face_and_weight_match_browser_font_declarations(family, bold):
    face = MANIFEST[family]
    filename = face["bold" if bold else "regular"]
    font = brand_layout.design_font(76, family, bold=bold)
    assert Path(font.path).name == filename
    assert font.getname()[1] == ("Bold" if bold else "Regular")
    css = (ROOT / "frontend/src/routes/design-fonts.css").read_text()
    declaration = next(line for line in css.splitlines() if filename in line)
    assert f'font-family: "{face["family"]}"' in declaration
    assert f'font-weight: {700 if bold else 400}' in declaration


@pytest.mark.parametrize("family", MANIFEST)
def test_cover_headline_and_body_text_keep_selected_family(family):
    layout = brand_layout._fit_typography_layout(
        "A CLEAR IDEA", ["A short supporting thought."], 904, font_family=family,
    )
    assert Path(layout.head_font.path).name == MANIFEST[family]["bold"]
    assert Path(layout.body_font.path).name == MANIFEST[family]["regular"]
    assert Path(media_tools._load_title_font(76, family).path).name == MANIFEST[family]["bold"]


@pytest.mark.parametrize("family", MANIFEST)
def test_cover_compositor_uses_saved_cover_font(monkeypatch, family):
    resolver = Mock(wraps=media_tools.design_font)
    monkeypatch.setattr(media_tools, "design_font", resolver)
    design = CarouselDesign(cover={"font_family": family})
    rendered = media_tools._render_title_block("A CLEAR IDEA", "IDEA", design)
    assert rendered.getbbox() is not None
    assert all(call.args[1] == family and call.kwargs["bold"] for call in resolver.call_args_list)


def test_missing_bundled_font_fails_instead_of_substituting(monkeypatch, tmp_path):
    monkeypatch.setattr(brand_layout, "_FONT_DIRECTORY", tmp_path)
    fallback = Mock(side_effect=AssertionError("Silent font substitution"))
    monkeypatch.setattr(ImageFont, "load_default", fallback)
    with pytest.raises(OSError):
        brand_layout.design_font(76, "sans", bold=True)
    fallback.assert_not_called()


def test_generated_body_and_cta_use_their_own_saved_fonts(monkeypatch, tmp_path):
    design = CarouselDesign(
        logo_visible=False, handle_visible=False, handle_text="@fonttest",
        inside={"image_type": "none", "font_family": "serif"},
        cta={"image_type": "none", "font_family": "condensed"},
    )
    paid = Mock(side_effect=AssertionError("No paid image generation in this test"))
    monkeypatch.setattr(image_gen, "_call_images_api", paid)
    resolver = Mock(wraps=brand_layout.design_font)
    monkeypatch.setattr(brand_layout, "design_font", resolver)
    for kind, expected in [("body", "serif"), ("cta", "condensed")]:
        resolver.reset_mock()
        output = str(tmp_path / f"{kind}.png")
        if kind == "body":
            image_gen.generate_slide_image("", ["A supporting thought."], "A CLEAR IDEA", 1, output, design=design)
        else:
            image_gen.generate_cta_image("follow", "STAY CURIOUS", ["Follow for more."], "", "", output, design=design)
        calls = resolver.call_args_list
        assert any(call.args[1] == expected and call.kwargs.get("bold") for call in calls)
        assert any(call.args[1] == expected and not call.kwargs.get("bold") for call in calls)
        assert not any(call.args[1] in {"serif", "condensed"} - {expected} for call in calls)
        with Image.open(output) as image:
            assert image.size == (1080, 1350)
    paid.assert_not_called()
