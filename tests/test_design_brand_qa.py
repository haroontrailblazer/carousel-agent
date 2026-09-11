"""Saved branding must render where the editor places it and pass real QA."""
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from google.genai import types
from PIL import Image, ImageDraw

from app import state as s
from app.agents import stitch_verify
from app.schemas import CarouselDesign
from app.tools import brand_identity, brand_layout as layout, image_gen, media_tools
from test_design_branding import logo_url
from test_pipeline_audit import outputs


def png(image):
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def design_with_boxes(**changes):
    slide = {
        "background": "#000000", "text_color": "#ffffff", "image_type": "none",
        "logo_transform": {"x": 8, "y": 88, "width": 6, "height": 5},
        "handle_transform": {"x": 17, "y": 88, "width": 32, "height": 5},
    }
    return CarouselDesign.model_validate({
        "handle_text": "@independent.studio", "handle_size": 30,
        "cover": slide, "inside": slide, "cta": slide, **changes,
    })


@pytest.mark.parametrize("kind", ["body", "cta"])
@pytest.mark.parametrize("logo", ["", logo_url((220, 20, 80, 255)), logo_url((0, 0, 0, 255))])
def test_custom_monogram_and_uploaded_logos_pass_without_legacy_favicon(kind, logo):
    design = design_with_boxes(logo_data_url=logo)
    canvas = Image.new("RGB", (1080, 1350))
    render = layout.apply_body_brand_rail if kind == "body" else layout.apply_cta_brand_rail
    result = render(canvas, "@wrong.account", design=design)
    assert layout.validate_footer_padding(png(result), kind, design=design) == []


@pytest.mark.parametrize("logo,handle", [(False, False), (False, True), (True, False)])
def test_cta_visibility_is_independent_of_body(logo, handle):
    design = design_with_boxes()
    design.cta.logo_visible = logo
    design.cta.handle_visible = handle
    design.cta.handle_transform.y = 70
    design.cta.logo_transform.y = 70
    result = layout.apply_cta_brand_rail(Image.new("RGB", (1080, 1350)), "", design)
    assert layout.validate_footer_padding(png(result), "cta", design=design) == []
    assert result.crop((0, 1188, 1080, 1275)).getbbox() is None


def test_unbranded_run_is_valid():
    with brand_identity.use(brand_identity.BrandIdentity("", b"")):
        result = layout.apply_cta_brand_rail(Image.new("RGB", (1080, 1350)), "")
        assert layout.validate_footer_padding(png(result), "cta") == []


@pytest.mark.parametrize("element", ["logo", "handle"])
def test_erased_or_misplaced_branding_still_fails_for_the_correct_mark(element):
    design = design_with_boxes(logo_data_url=logo_url())
    result = layout.apply_cta_brand_rail(Image.new("RGB", (1080, 1350)), "", design)
    transform = getattr(design.cta, element + "_transform")
    x, y = round(transform.x * 10.8), round(transform.y * 13.5)
    width, height = round(transform.width * 10.8), round(transform.height * 13.5)
    ImageDraw.Draw(result).rectangle((x, y, x + width, y + height), fill="black")
    errors = layout.validate_footer_padding(png(result), "cta", design=design)
    assert errors == [f"selected {element} is missing or does not match its configured position"]


@pytest.mark.parametrize("surface", ["cover", "inside", "cta"])
def test_handle_ink_is_centered_in_saved_box_on_every_surface(surface, tmp_path):
    design = design_with_boxes(logo_visible=False)
    slide = getattr(design, surface)
    slide.handle_transform.x = 21
    slide.handle_transform.y = 89
    slide.handle_transform.height = 8
    if surface == "cover":
        result = Image.open(media_tools._build_overlay_png("A useful idea", "idea", tmp_path, design)).convert("RGB")
    else:
        renderer = layout.apply_body_brand_rail if surface == "inside" else layout.apply_cta_brand_rail
        result = renderer(Image.new("RGB", (1080, 1350)), "", design=design)
    x, y, width, height = 227, 1202, 346, 108
    ink = result.crop((x, y, x + width, y + height)).getbbox()
    assert ink is not None
    assert ink[0] <= 1  # The saved left edge, without a safe-margin nudge.
    assert abs((ink[1] + ink[3]) / 2 - height / 2) <= 1


def test_rectangular_logo_contains_source_in_entire_editor_box():
    design = design_with_boxes(logo_data_url=logo_url(), handle_visible=False)
    design.cta.logo_transform.width = 20
    design.cta.logo_transform.height = 4
    result = layout.apply_cta_brand_rail(Image.new("RGB", (1080, 1350)), "", design)
    # A 2:1 source inside a 216x54 box becomes 108x54, centered horizontally.
    crop = result.crop((86, 1188, 302, 1242))
    assert crop.getbbox() == (54, 0, 162, 54)
    assert layout.validate_footer_padding(png(result), "cta", design=design) == []


@pytest.mark.parametrize("bad", [b"not a png", png(Image.new("RGB", (100, 100)))])
def test_corrupt_or_wrong_size_output_still_fails(bad):
    assert layout.validate_footer_padding(bad, "cta", design=design_with_boxes())


def test_branding_does_not_hide_missing_divider_or_number():
    design = design_with_boxes()
    result = layout.apply_body_brand_rail(Image.new("RGB", (1080, 1350)), "", design=design)
    ImageDraw.Draw(result).rectangle((0, 1159, 1080, 1162), fill="black")
    assert layout.validate_footer_padding(png(result), "body", True, design=design) == [
        "footer divider is missing", "slide number is missing from the fixed top-left anchor",
    ]


@pytest.mark.asyncio
async def test_real_pngs_pass_qa_and_clear_obsolete_brand_rework(tmp_path):
    design = design_with_boxes()
    design.cta.handle_transform.y = 80
    state = outputs()
    state[s.K_DESIGN] = design.model_dump(mode="json")
    state[s.K_REWORK_PLAN] = {"targets": ["cta"], "feedback": "Old brand rule"}
    state[s.K_REWORK_FEEDBACK] = "Automated QA: the old brand favicon is missing"
    with patch.object(image_gen, "_call_images_api") as paid:
        path = image_gen.generate_cta_image(
            "comment", "Share your view", ["What would you change about this release?"],
            "", "", str(tmp_path / "cta.png"), design,
        )
    paid.assert_not_called()
    body = layout.apply_slide_typography(
        Image.new("RGB", (1080, 1350)), "Local processing",
        ["The new release supports local processing."], design=design,
    )
    body = layout.apply_body_brand_rail(body, design.handle_text, 1, design)
    artifacts = {"cta.png": Path(path).read_bytes(), "slide_2.png": png(body), "cover.png": png(body), "cover.mp4": b"video"}
    context = SimpleNamespace(
        state=state,
        load_artifact=AsyncMock(side_effect=lambda name: types.Part.from_bytes(data=artifacts[name], mime_type="image/png")),
        list_artifacts=AsyncMock(return_value=list(artifacts)),
    )
    result = await stitch_verify.assemble_and_verify(context)
    assert result["passed"], result["issues"]
    assert result["critical_targets"] == []
    assert state[s.K_REWORK_PLAN] is None
    assert state[s.K_REWORK_FEEDBACK] == ""


def test_qa_summary_uses_current_verdict_instead_of_repeating_obsolete_brand_claim():
    prompt = stitch_verify._instruction_provider(SimpleNamespace(state={s.K_REWORK_FEEDBACK: "old automated failure"}))
    assert "Do not repeat obsolete failures" in prompt
    assert "current tool result" in prompt
    assert "describe automated QA as a human rejection" in prompt
