"""Real FFmpeg smoke tests with synthetic local sources; no network or API calls."""
import json
import shutil
import subprocess

import pytest
from PIL import Image, ImageDraw

from app.config import settings
from app.schemas import CarouselDesign
from app.tools import media_tools


@pytest.mark.parametrize("video", [False, True], ids=["image-to-mp4", "video-to-mp4"])
def test_cover_is_full_bleed_h264_with_black_title_base(tmp_path, video):
    if not shutil.which(settings.ffmpeg_bin) or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and ffprobe are required for the real cover render smoke test")
    if video:
        source = tmp_path / "source.mp4"
        subprocess.run([settings.ffmpeg_bin, "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24", "-t", "4", "-an", "-c:v", "libx264", str(source)], check=True, capture_output=True, timeout=30)
    else:
        source = tmp_path / "source.png"
        image = Image.new("RGB", (1200, 700), "#507ba5")
        ImageDraw.Draw(image).ellipse((450, 100, 800, 450), fill="#f09854")
        image.save(source)
    design = CarouselDesign(handle_text="@audit", logo_visible=False, handle_visible=False)
    result = media_tools.compose_cover(str(source), "A clearer story", "clearer", video, str(tmp_path / "render"), design)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", result["video_path"]], check=True, capture_output=True, timeout=15)
    metadata = json.loads(probe.stdout)
    streams = metadata["streams"]
    assert len(streams) == 1 and streams[0]["codec_name"] == "h264"
    assert (streams[0]["width"], streams[0]["height"]) == (1080, 1350)
    assert 3.9 <= float(metadata["format"]["duration"]) <= 6.1
    with Image.open(result["poster_path"]) as poster:
        assert poster.size == (1080, 1350)
        # Full source media reaches both upper edges; the title base is black.
        assert max(poster.convert("RGB").getpixel((0, 100))) > 20
        assert max(poster.convert("RGB").getpixel((1079, 100))) > 20
        assert max(poster.convert("RGB").getpixel((540, 1320))) < 15
