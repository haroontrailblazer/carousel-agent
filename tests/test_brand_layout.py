from __future__ import annotations

import unittest

from PIL import Image, ImageDraw
from pydantic import ValidationError

from app.schemas import CopySet
from app.agents.template_design import _body_display_numbers
from app.tools.brand_layout import (
    INK,
    PAPER,
    RAIL_DIVIDER_Y,
    SLIDE_NUMBER_LEFT,
    SLIDE_NUMBER_TOP,
    TEXT_PANEL_BOTTOM,
    anchor_dominant_visual_to_divider,
    apply_cta_brand_rail,
    apply_slide_typography,
)


class BrandLayoutTests(unittest.TestCase):
    def test_body_counter_starts_at_one_and_ignores_cover_index(self) -> None:
        copy = CopySet.model_validate(
            {
                "slides": [
                    {"index": 4, "lines": ["Third body slide"]},
                    {"index": 2, "lines": ["First body slide"]},
                    {"index": 3, "lines": ["Second body slide"]},
                ],
                "caption": "",
            }
        )
        self.assertEqual(_body_display_numbers(copy.slides), {2: 1, 3: 2, 4: 3})

    def test_cta_rail_leaves_top_counter_zone_unnumbered(self) -> None:
        image = Image.new("RGB", (1080, 1350), INK)
        rendered = apply_cta_brand_rail(image, "@baskaranbuilds")
        number_zone = rendered.crop(
            (
                SLIDE_NUMBER_LEFT,
                SLIDE_NUMBER_TOP,
                SLIDE_NUMBER_LEFT + 72,
                SLIDE_NUMBER_TOP + 48,
            )
        )
        self.assertEqual(number_zone.getcolors(maxcolors=2), [(72 * 48, INK)])

    def test_typography_paints_one_coherent_top_background(self) -> None:
        image = Image.new("RGB", (1080, 1350), PAPER)
        ImageDraw.Draw(image).rectangle((0, 132, 1080, 620), fill=(22, 24, 17))
        rendered = apply_slide_typography(
            image,
            "A MOTHER-SON TEAM MADE IT",
            ["A short supporting line."],
        )
        self.assertEqual(rendered.getpixel((20, 20)), PAPER)
        self.assertEqual(rendered.getpixel((20, 300)), PAPER)

    def test_dominant_visual_is_extended_to_footer_divider(self) -> None:
        image = Image.new("RGB", (1080, 1350), PAPER)
        ImageDraw.Draw(image).rectangle(
            (0, TEXT_PANEL_BOTTOM, 1079, RAIL_DIVIDER_Y - 70),
            fill=(30, 30, 30),
        )
        rendered = anchor_dominant_visual_to_divider(image)
        self.assertLess(sum(rendered.getpixel((540, RAIL_DIVIDER_Y - 1))), 200)

    def test_slide_copy_rejects_alternate_script_parentheticals(self) -> None:
        with self.assertRaises(ValidationError):
            CopySet.model_validate(
                {
                    "slides": [
                        {
                            "index": 4,
                            "lines": ["Xin Yumeng (\u4fe1\u96e8\u840c) directed it."],
                        }
                    ],
                    "caption": "",
                }
            )


if __name__ == "__main__":
    unittest.main()
