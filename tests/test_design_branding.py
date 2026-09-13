"""Design-owned branding survives persistence and reaches every renderer."""
import base64
import io
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from pydantic import ValidationError

from app.schemas import CarouselDesign
from app.tools import brand_identity, brand_layout, media_tools, image_gen
from app.agents import cta
from web_api.auth import Identity
from web_api.deps import current_identity
from web_api.routes_designs import router


def logo_url(color=(245, 0, 210, 255), size=(120, 60)):
    buffer = io.BytesIO()
    Image.new("RGBA", size, color).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def branded_design(**changes):
    return CarouselDesign(handle_text="design.studio", logo_data_url=logo_url(), **changes)


def test_branding_survives_api_save_and_load_for_its_owner():
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[current_identity] = lambda: Identity(email="designer@example.com", subject="designer@example.com")
    client = TestClient(app)
    payload = branded_design(logo_background="#123456").model_dump(mode="json")
    with patch("web_api.routes_designs.db.replace_carousel_designs", AsyncMock()) as saved:
        response = client.put("/api/designs", json={"items": [payload]})
    assert response.status_code == 200
    assert saved.await_args.args == ("designer@example.com", [payload])
    with patch("web_api.routes_designs.db.list_carousel_designs", AsyncMock(return_value=[payload])):
        assert client.get("/api/designs").json()["items"] == [payload]


@pytest.mark.parametrize("value", ["a space", "name/other", "<script>", "x" * 32])
def test_invalid_handle_is_rejected(value):
    with pytest.raises(ValidationError):
        CarouselDesign(handle_text=value)


@pytest.mark.parametrize("value", ["https://example.com/logo.png", "file:///private.png", "data:image/svg+xml;base64,PHN2Zz4=", "data:image/png;base64,bm90IGFuIGltYWdl", logo_url(size=(513, 1))])
def test_invalid_logo_is_rejected(value):
    with pytest.raises(ValidationError):
        CarouselDesign(logo_data_url=value)


@pytest.mark.parametrize("color", ["red", "#fff", "#12345678", "url(example)"])
def test_logo_background_requires_solid_hex_color(color):
    with pytest.raises(ValidationError):
        CarouselDesign(logo_background=color)


@pytest.mark.parametrize("surface", ["cover", "inside", "cta"])
@pytest.mark.parametrize("color", ["", "#123456"])
def test_transparent_logo_background_reaches_every_surface(surface, color, tmp_path):
    mark = Image.new("RGBA", (128, 128))
    ImageDraw.Draw(mark).rectangle((48, 48, 80, 80), fill=(245, 0, 210, 255))
    buffer = io.BytesIO()
    mark.save(buffer, format="PNG")
    layout = {"background": "#000000", "logo_visible": True,
              "logo_transform": {"x": 8, "y": 88, "width": 6, "height": 5}}
    design = CarouselDesign(
        logo_data_url="data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(),
        logo_background=color, handle_visible=False, cover=layout, inside=layout, cta=layout,
    )
    original = design.logo_data_url
    if surface == "cover":
        with Image.open(media_tools._build_overlay_png("Your next idea", "idea", tmp_path, design)) as image:
            result = image.convert("RGB")
    else:
        renderer = brand_layout.apply_cta_brand_rail if surface == "cta" else brand_layout.apply_body_brand_rail
        result = renderer(Image.new("RGB", (1080, 1350)), "", design=design)
    assert result.getpixel((118, 1203)) == ((18, 52, 86) if color else (0, 0, 0))
    assert result.getpixel((118, 1221)) == (245, 0, 210)
    assert result.getpixel((86, 1189)) == (0, 0, 0)
    assert design.logo_data_url == original
    # Removing the background restores the original alpha, without re-uploading.
    cleared = design.model_copy(update={"logo_background": ""})
    assert brand_identity.require_favicon(128, cleared).getpixel((64, 20))[3] == 0


def test_design_override_does_not_change_account_or_leak_to_another_design():
    account = brand_identity.BrandIdentity(handle="@account", favicon_png=b"", account_id="account-id")
    with brand_identity.use(account):
        first = branded_design()
        second = CarouselDesign(handle_text="another.design")
        assert image_gen._current_handle(first) == "@design.studio"
        assert image_gen._current_handle(second) == "@another.design"
        assert brand_identity.require_handle() == "@account"
        assert brand_identity.current(first).account_id == "account-id"
        assert brand_identity.current() is account
        assert cta._resolve_link("follow", "", first) == ("@design.studio", "@design.studio")


def test_logo_preserves_transparency_and_aspect_ratio_without_account():
    with brand_identity.use(brand_identity.BrandIdentity(handle="", favicon_png=b"")):
        design = branded_design()
        mark = brand_identity.require_favicon(64, design)
        assert mark.getbbox() == (0, 16, 64, 48)
        assert mark.getpixel((32, 32)) == (245, 0, 210, 255)
        assert brand_identity.require_handle(design) == "@design.studio"
        assert brand_identity.require_handle() == ""


@pytest.mark.parametrize("renderer", [brand_layout.apply_body_brand_rail, brand_layout.apply_cta_brand_rail])
def test_body_and_cta_use_design_logo_and_handle(renderer):
    design = branded_design(inside={"logo_transform": {"x": 8, "y": 88, "width": 6, "height": 5}})
    with brand_identity.use(brand_identity.BrandIdentity(handle="", favicon_png=b"")):
        with patch.object(brand_layout, "_draw_handle_positioned", wraps=brand_layout._draw_handle_positioned) as drawn:
            result = renderer(Image.new("RGB", (1080, 1350), "white"), "@wrong", design=design)
    assert drawn.call_args.args[1] == "@design.studio"
    assert result.getpixel((round(1080 * .08) + 28, round(1350 * .88) + 28)) == (245, 0, 210)


def test_cover_uses_uploaded_logo_and_custom_handle():
    design = branded_design(cover={"handle_visible": True, "shadow_visible": False, "logo_transform": {"x": 8, "y": 88, "width": 6, "height": 5}})
    with tempfile.TemporaryDirectory() as folder, brand_identity.use(None):
        with patch.object(brand_identity, "require_handle", wraps=brand_identity.require_handle) as handle:
            path = media_tools._build_overlay_png("A useful idea", "idea", Path(folder), design)
        assert handle.call_args.args == (design,)
        with Image.open(path) as image:
            assert image.getpixel((round(1080 * .08) + 28, round(1350 * .88) + 28)) == (245, 0, 210, 255)


def test_logo_only_design_can_render_without_inventing_a_handle():
    design = CarouselDesign(logo_data_url=logo_url())
    with brand_identity.use(brand_identity.BrandIdentity(handle="", favicon_png=b"")):
        assert brand_identity.require_handle(design) == ""
        assert not brand_identity.current(design).unbranded
        brand_layout.apply_body_brand_rail(Image.new("RGB", (1080, 1350), "white"), "", design=design)
        brand_layout.apply_cta_brand_rail(Image.new("RGB", (1080, 1350), "white"), "", design=design)


@pytest.mark.parametrize("surface", ["cover", "inside", "cta"])
def test_circular_crop_keeps_transparent_corners_in_generated_slides(surface, tmp_path):
    mark = Image.new("RGBA", (512, 512))
    ImageDraw.Draw(mark).ellipse((0, 0, 511, 511), fill=(245, 0, 210, 255))
    buffer = io.BytesIO()
    mark.save(buffer, format="PNG")
    layout = {"background": "#000000", "logo_visible": True,
              "logo_transform": {"x": 8, "y": 88, "width": 6, "height": 5}}
    design = CarouselDesign(
        logo_data_url="data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(),
        handle_visible=False, cover=layout, inside=layout, cta=layout,
    )
    if surface == "cover":
        with Image.open(media_tools._build_overlay_png("Your next idea", "idea", tmp_path, design)) as image:
            result = image.convert("RGB")
    else:
        renderer = brand_layout.apply_cta_brand_rail if surface == "cta" else brand_layout.apply_body_brand_rail
        result = renderer(Image.new("RGB", (1080, 1350)), "", design=design)
    # The 65px circle is contained in the saved 65x68 box. Its corners remain
    # the slide background; no renderer fills them or distorts it to an oval.
    assert result.getpixel((86, 1189)) == (0, 0, 0)
    assert result.getpixel((150, 1253)) == (0, 0, 0)
    assert result.getpixel((118, 1221)) == (245, 0, 210)
