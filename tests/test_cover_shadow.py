"""Saved shadow controls and browser/render parity."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from pydantic import ValidationError

from app.cover_shadow import cover_shadow_mask
from app.schemas import CarouselDesign


def test_shadow_controls_survive_saved_design_round_trip():
    design = CarouselDesign(cover={'shadow_height': 64, 'shadow_softness': 23, 'shadow_curve': 78})
    saved = CarouselDesign.model_validate_json(design.model_dump_json())
    assert (saved.cover.shadow_height, saved.cover.shadow_softness, saved.cover.shadow_curve) == (64, 23, 78)
    assert saved.cover.shadow_visible and saved.cover.shadow_color == '#000000'
    assert saved.cover.shadow_opacity == 100


def test_missing_controls_keep_the_previous_cover_appearance():
    for design in (CarouselDesign(), CarouselDesign(cover={})):
        assert (design.cover.shadow_height, design.cover.shadow_softness, design.cover.shadow_curve) == (52, 65, 0)


@pytest.mark.parametrize('field,value', [('shadow_curve', -1), ('shadow_curve', 101), ('shadow_softness', 101), ('shadow_height', 73)])
def test_invalid_controls_are_rejected(field, value):
    with pytest.raises(ValidationError):
        CarouselDesign(cover={field: value})


def test_each_control_changes_its_intended_part_of_the_shadow():
    default = cover_shadow_mask(101, 100)
    tall = cover_shadow_mask(101, 100, coverage=72)
    sharp = cover_shadow_mask(101, 100, blur=0)
    curved = cover_shadow_mask(101, 100, curve=100)
    assert tall.getpixel((50, 40)) > default.getpixel((50, 40))
    assert set(sharp.tobytes()) == {0, 255}
    assert 0 < default.getpixel((50, 56)) < 255
    assert curved.getpixel((0, 40)) == curved.getpixel((100, 40)) > curved.getpixel((50, 40))
    for mask in (default, tall, sharp, curved):
        assert all(mask.getpixel((x, 99)) == 255 for x in range(101))


def test_browser_mask_matches_generated_cover_pixels():
    root = Path(__file__).resolve().parents[1]
    node = shutil.which('node')
    if not node or not (root / 'frontend/node_modules/typescript').exists():
        pytest.skip('Frontend dependencies and Node are required for cross-runtime parity')
    script = r'''
const ts = require('./frontend/node_modules/typescript');
const fs = require('fs');
const source = fs.readFileSync('frontend/src/lib/cover-shadow.ts', 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText;
const moduleShim = {exports: {}};
new Function('exports', 'module', compiled)(moduleShim.exports, moduleShim);
const cases = [[52,65,0], [72,0,100], [18,100,100], [60,25,55]];
console.log(JSON.stringify(cases.map(args => ({args, pixels: Array.from(moduleShim.exports.coverShadowPixels(64,80,...args)).filter((_,i) => i%4===3)}))));
'''
    result = subprocess.run([node, '-e', script], cwd=root, check=True, capture_output=True, text=True, timeout=30)
    for case in json.loads(result.stdout):
        coverage, blur, curve = case['args']
        rendered = cover_shadow_mask(64, 80, coverage=coverage, blur=blur, curve=curve)
        assert list(rendered.tobytes()) == case['pixels'], case['args']
