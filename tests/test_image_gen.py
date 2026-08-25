from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from app.tools import image_gen
from app.tools.brand_layout import PAPER, RAIL_DIVIDER_Y, TEXT_PANEL_BOTTOM


def _png_bytes(image: Image.Image) -> bytes:
    payload = BytesIO()
    image.save(payload, format="PNG")
    return payload.getvalue()


class VisualMergeTests(unittest.TestCase):
    def test_generation_size_matches_visual_slot_aspect_ratio(self) -> None:
        self.assertEqual(image_gen._GEN_SIZE, "1088x544")
        self.assertEqual(image_gen._GEN_WIDTH / image_gen._GEN_HEIGHT, 2.0)

    def test_generated_visual_is_merged_into_exact_lower_slot(self) -> None:
        visual = Image.new("RGB", (1088, 544), (20, 30, 40))
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "slide.png"
            image_gen._finalize(_png_bytes(visual), str(output), theme="paper")
            with Image.open(output) as rendered:
                self.assertEqual(rendered.size, (1080, 1350))
                self.assertEqual(rendered.getpixel((540, TEXT_PANEL_BOTTOM - 1)), PAPER)
                self.assertEqual(rendered.getpixel((540, TEXT_PANEL_BOTTOM)), (20, 30, 40))
                self.assertEqual(rendered.getpixel((540, RAIL_DIVIDER_Y - 1)), (20, 30, 40))
                self.assertEqual(rendered.getpixel((540, RAIL_DIVIDER_Y)), PAPER)

    def test_unexpected_aspect_is_contained_without_crop_or_stretch(self) -> None:
        visual = Image.new("RGB", (100, 200), (70, 80, 90))
        ImageDraw.Draw(visual).rectangle((0, 0, 99, 199), outline=(220, 30, 30), width=4)
        panel = image_gen._contain_without_crop(visual, (1080, 540), PAPER)
        background = Image.new("RGB", panel.size, PAPER)
        self.assertEqual(ImageChops.difference(panel, background).getbbox(), (405, 0, 675, 540))
        self.assertGreater(panel.getpixel((405, 0))[0], 180)
        self.assertGreater(panel.getpixel((674, 539))[0], 180)
        self.assertEqual(panel.getpixel((404, 270)), PAPER)
        self.assertEqual(panel.getpixel((675, 270)), PAPER)

    def test_sourced_subject_is_contained_and_bottom_aligned(self) -> None:
        source = Image.new("RGB", (100, 200), (40, 50, 60))
        ImageDraw.Draw(source).rectangle((0, 0, 99, 199), outline=(210, 40, 40), width=4)
        base = Image.new("RGB", (1080, 1350), PAPER)
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "subject.png"
            source.save(source_path)
            merged = image_gen._composite_subject_reference(base, str(source_path))

        self.assertGreater(merged.getpixel((405, TEXT_PANEL_BOTTOM))[0], 180)
        self.assertGreater(merged.getpixel((674, RAIL_DIVIDER_Y - 1))[0], 180)
        self.assertEqual(merged.getpixel((404, RAIL_DIVIDER_Y - 1)), PAPER)
        self.assertEqual(merged.getpixel((675, RAIL_DIVIDER_Y - 1)), PAPER)


if __name__ == "__main__":
    unittest.main()
