"""Design-owned branding survives persistence and reaches every renderer."""
import base64
import io
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
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
    payload = branded_design().model_dump(mode="json")
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
