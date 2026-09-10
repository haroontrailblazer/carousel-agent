"""Every source and saved design gets the same readable cover structure."""
from pathlib import Path
from unittest.mock import patch
import pytest
from PIL import Image
from app.schemas import CarouselDesign
from app.tools import media_tools

def test_legacy_cover_cannot_remove_media_or_black_shadow():
    design = CarouselDesign(
        handle_text="@my.studio",
        cover={
            "image_type": "none", "shadow_visible": False,
            "shadow_color": "#ff0000", "shadow_opacity": 0,
            "title_transform": {"x": 8, "y": 8, "width": 84, "height": 32},
            "text_color": "#252420",
        },
        inside={"image_type": "none", "shadow_visible": False, "text_color": "#252420"},
    )
    assert design.cover.image_type == "editorial"
    assert design.cover.image_transform.model_dump() == dict(x=0, y=0, width=100, height=100, locked=True)
    assert design.cover.shadow_visible
    assert design.cover.shadow_color == "#000000"
    assert design.cover.shadow_opacity == 100
    assert design.cover.title_transform.y == 62
    assert design.cover.title_transform.height == 24
    assert design.cover.text_color == "#252420"
    assert design.inside.image_type == "none"
    assert not design.inside.shadow_visible
    assert design.inside.text_color == "#252420"
    assert design.handle_text == "@my.studio"
    assert CarouselDesign.model_validate(design.model_dump()) == design

@pytest.mark.parametrize("with_design", [False, True])
def test_shadow_has_clear_media_soft_fade_and_solid_black_title_base(tmp_path, with_design):
    design = CarouselDesign(logo_visible=False, handle_visible=False) if with_design else None
    if design:
        design.cover.shadow_visible = False
        design.cover.shadow_opacity = 0
        design.cover.shadow_color = "#ff0000"
    transparent = Image.new("RGBA", (1080, 1350))
    with patch.object(media_tools, "_render_title_block", return_value=transparent), patch.object(
        media_tools, "_load_scrubbed_template", side_effect=AssertionError("No extra template layers")
    ):
        output = media_tools._build_overlay_png("Sample title", "", Path(tmp_path), design)
    with Image.open(output) as overlay:
        for x in (0, 540, 1079):
            assert overlay.getpixel((x, 600)) == (0, 0, 0, 0)
            assert overlay.getpixel((x, 770)) == (0, 0, 0, 128)
            assert overlay.getpixel((x, 891)) == (0, 0, 0, 255)
            assert overlay.getpixel((x, 1349)) == (0, 0, 0, 255)

def test_cover_title_stays_above_branding_and_inside_edits_are_preserved():
    design = CarouselDesign(
        cover={"title_transform": {"x": 12, "y": 80, "width": 70, "height": 15}, "title_align": "right"},
        inside={"title_transform": {"x": 12, "y": 8, "width": 70, "height": 50}},
    )
    assert design.cover.title_transform.y == 71
    assert design.cover.title_transform.x == 12
    assert design.cover.title_position == "bottom-right"
    assert design.inside.title_transform.y == 8
    assert design.inside.title_transform.height == 50


def test_rendered_title_fits_lower_box_without_overlapping_branding():
    design = CarouselDesign(logo_visible=False, cover={
        "title_size": 160,
        "title_transform": {"x": 8, "y": 62, "width": 84, "height": 24},
    })
    title = media_tools._render_title_block("AGENTS ARE CHANGING HOW WE BUILD", "HOW WE BUILD", design)
    bounds = title.getbbox()
    assert bounds is not None
    assert bounds[1] >= round(1350 * .62)
    assert bounds[3] <= round(1350 * .86)
    assert bounds[0] >= round(1080 * .08)
    assert bounds[2] <= round(1080 * .92)
