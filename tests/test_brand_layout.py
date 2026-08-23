from __future__ import annotations

import unittest

from PIL import Image, ImageDraw
from pydantic import ValidationError

from app.schemas import CopySet
from app.tools.brand_layout import (
    PAPER,
    RAIL_DIVIDER_Y,
    TEXT_PANEL_BOTTOM,
    anchor_dominant_visual_to_divider,
    apply_slide_typography,
)


class BrandLayoutTests(unittest.TestCase):
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
