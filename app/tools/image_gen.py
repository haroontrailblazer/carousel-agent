"""Slide and CTA image generation via the OpenAI Images API (gpt-image-2).

Exposes plain, type-hinted functions (wrapped in ``FunctionTool`` by the
Template Design and CTA agents):

- :func:`generate_slide_image` - renders one body slide PNG.
- :func:`generate_cta_image` - renders the closing CTA slide PNG.

Rendering contract (see docs/CONTRACTS.md + skills/design-skill.md):

- When a template reference image exists, the ``images.edit`` endpoint is used
  so the model reproduces the template layout and only swaps the text; the
  prompt demands the text VERBATIM, character for character (the
  Stitch & Verify agent rejects slides whose rendered text drifts).
- Without a template, ``images.generate`` is used with a detailed style prompt
  built from skills/design-skill.md.
- gpt-image-2 arbitrary sizes must be divisible by 16, so generation happens
  at 1088x1360 (exact 4:5) and the result is downscaled with Pillow LANCZOS to
  1080x1350 before being written to ``out_path``.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import time
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple

import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    OpenAI,
    OpenAIError,
)
from PIL import Image

from app import observability
from app.config import load_skill, settings
from app.text_rules import require_no_em_dash, require_readable_text
from app.tools.brand_layout import (
    HEADLINE_FONT_SIZE,
    HEADLINE_STYLE,
    apply_body_brand_rail,
    apply_cta_brand_rail,
    normalize_accent_green,
)

logger = logging.getLogger(__name__)

# gpt-image-2 requires width/height divisible by 16 → generate at exact 4:5
# then downscale to the Instagram slide size from settings (1080x1350).
_GEN_WIDTH: int = 1088
_GEN_HEIGHT: int = 1360
_GEN_SIZE: str = f"{_GEN_WIDTH}x{_GEN_HEIGHT}"

# Image generation is slow; give the API a generous but explicit budget.
_REQUEST_TIMEOUT_S: float = 300.0
# Downloading a result URL (rare fallback path) is fast by comparison.
_DOWNLOAD_TIMEOUT_S: float = 60.0
# One retry on transient failure, after a short pause.
_RETRY_DELAY_S: float = 5.0

_client_singleton: Optional[OpenAI] = None

# Inline fallback used only when skills/design-skill.md is missing on disk.
_FALLBACK_STYLE = """
Design system: 1080x1350 (4:5) editorial social slide. Alternate ink (#161811)
and paper (#F7F7F5) backgrounds. Use warm white (#E8E4D6) or ink (#1A1A18)
text and exactly #B8EF43 green for exactly ONE emphasized element. Never use
another green shade, tint, gradient, glow, or color variation for highlights.
Bricolage-style bold grotesk headlines, clean Instrument-style body text,
generous safe margins, one dominant explanatory visual, and a compact bottom
brand rail. Choose an editorial explainer, data proof, process line, comparison,
dark technical proof, or statement-pause layout based on the content. Never use
a repeated card grid. A large lower-half visual must meet the footer divider;
only a compact centered visual may float above it with a tight gap. CTA slide:
same family, one clear action, no swipe arrow.
""".strip()

_VERBATIM_RULE = (
    "CRITICAL TEXT RULE: every quoted string below must appear in the image "
    "VERBATIM - matching character for character, including capitalization, "
    "punctuation, digits and spacing. Do NOT paraphrase, translate, correct "
    "spelling, abbreviate, drop words, or add any words, labels or watermarks "
    "that are not listed. Render no other text anywhere in the image. Use "
    "clear, correctly formed letters for every word. Do not create fake "
    "words, garbled letters, pseudo-text, tiny decorative text, or symbols "
    "that resemble writing. If a decorative visual would normally contain "
    "labels, use an unlabeled shape instead."
)


def _client() -> OpenAI:
    """Return a lazily created OpenAI client.

    The API key comes from the ``OPENAI_API_KEY`` environment variable, which
    ``app.config`` loads from ``.env`` on import (no key is ever hard-coded).
    SDK auto-retries are disabled so this module's own single-retry policy is
    the only retry in play.
    """
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = OpenAI(timeout=_REQUEST_TIMEOUT_S, max_retries=0)
    return _client_singleton


def _style_prompt() -> str:
    """Load the design system text from skills/design-skill.md (with fallback)."""
    skill = load_skill("design-skill.md").strip()
    return skill if skill else _FALLBACK_STYLE


def _template_file(template_ref: str) -> Optional[Tuple[str, bytes, str]]:
    """Resolve ``template_ref`` to an upload tuple, or ``None`` if unusable.

    Accepts an absolute path or a path relative to the project skills dir /
    workdir. Returns ``(filename, content_bytes, mime_type)`` suitable for the
    OpenAI SDK ``image`` parameter.
    """
    if not template_ref or not template_ref.strip():
        return None
    candidates = [
        Path(template_ref),
        settings.skills_dir / template_ref,
        settings.workdir / template_ref,
    ]
    for candidate in candidates:
        if candidate.is_file():
            mime, _ = mimetypes.guess_type(candidate.name)
            if mime not in ("image/png", "image/jpeg", "image/webp"):
                mime = "image/png"
            return (candidate.name, candidate.read_bytes(), mime)
    logger.warning("Template reference %r not found; using style-prompt fallback.", template_ref)
    return None


def _is_transient(exc: Exception) -> bool:
    """True for failures worth one retry (timeouts, connection drops, 429/5xx)."""
    if isinstance(exc, APIConnectionError):  # includes APITimeoutError
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    if isinstance(exc, httpx.TimeoutException) or isinstance(exc, httpx.TransportError):
        return True
    return False


def _call_images_api(prompt: str, template: Optional[Tuple[str, bytes, str]]) -> bytes:
    """Call images.edit (with template) or images.generate, returning PNG bytes.

    Handles the ``b64_json`` response (default for gpt-image models) with a
    URL-download fallback, and performs exactly one retry on transient failure.
    """
    client = _client()
    last_exc: Optional[Exception] = None
    for attempt in range(2):
        try:
            if template is not None:
                response = client.images.edit(
                    model=settings.image_model,
                    image=template,
                    prompt=prompt,
                    size=_GEN_SIZE,
                    n=1,
                    quality="high",
                    output_format="png",
                    timeout=_REQUEST_TIMEOUT_S,
                )
            else:
                response = client.images.generate(
                    model=settings.image_model,
                    prompt=prompt,
                    size=_GEN_SIZE,
                    n=1,
                    quality="high",
                    output_format="png",
                    timeout=_REQUEST_TIMEOUT_S,
                )
            observability.record_image_usage(
                model=settings.image_model,
                endpoint="images.edit" if template is not None else "images.generate",
                usage=getattr(response, "usage", None),
                prompt=prompt,
            )
            if not response.data:
                raise RuntimeError("OpenAI Images API returned an empty data list.")
            item = response.data[0]
            if item.b64_json:
                return base64.b64decode(item.b64_json)
            if item.url:
                download = httpx.get(item.url, timeout=_DOWNLOAD_TIMEOUT_S)
                download.raise_for_status()
                return download.content
            raise RuntimeError("OpenAI Images API returned neither b64_json nor url.")
        except (OpenAIError, httpx.HTTPError, RuntimeError) as exc:
            last_exc = exc
            if attempt == 0 and _is_transient(exc):
                logger.warning(
                    "Transient image API failure (%s); retrying once in %.0fs.",
                    exc,
                    _RETRY_DELAY_S,
                )
                time.sleep(_RETRY_DELAY_S)
                continue
            raise
    raise RuntimeError(f"Image generation failed after retry: {last_exc}")  # pragma: no cover


def _finalize(png_bytes: bytes, out_path: str) -> str:
    """Downscale to slide size with LANCZOS and write the PNG to ``out_path``."""
    target = (settings.slide_width, settings.slide_height)  # 1080x1350
    destination = Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(BytesIO(png_bytes)) as img:
        rgb = img.convert("RGB")
        if rgb.size != target:
            rgb = rgb.resize(target, Image.Resampling.LANCZOS)
        rgb.save(destination, format="PNG")
    return str(destination)


def _quoted_block(label: str, lines: list[str]) -> str:
    """Format text lines as an explicitly quoted, numbered block for the prompt."""
    numbered = "\n".join(f'  {i}. "{line}"' for i, line in enumerate(lines, start=1))
    return f"{label}:\n{numbered}" if numbered else f"{label}: (none)"


def generate_slide_image(
    template_ref: str,
    copy_lines: list[str],
    headline: str,
    slide_no: int,
    out_path: str,
    layout_hint: str = "editorial explainer",
) -> str:
    """Render one body slide (1080x1350 PNG) with gpt-image-2.

    Args:
        template_ref: Path to the template reference image. When the file
            exists the images.edit endpoint reproduces its layout with the new
            text; when empty/missing, images.generate is used with the full
            style prompt from skills/design-skill.md.
        copy_lines: The approved body copy lines (rendered verbatim, one
            thought per line, in order).
        headline: The slide headline (rendered verbatim, uppercase condensed
            per the design system).
        slide_no: 1-based slide number in the carousel (slide 1 is the cover),
            shown as a small quiet number tag, e.g. "02".
        out_path: Destination PNG path; parent directories are created.
        layout_hint: Content-aware archetype chosen by the template agent.

    Returns:
        The absolute/normalized path of the written PNG as a string.
    """
    require_no_em_dash([headline, *copy_lines], "body slide copy")
    require_readable_text([headline, *copy_lines], "body slide copy")
    tag = f"{slide_no:02d}"
    allowed = [
        _VERBATIM_RULE,
        "",
        f'Use the "{layout_hint}" layout archetype from the design system.',
        (
            "Match slide 1 headline typography: use a "
            f"{HEADLINE_FONT_SIZE}px-equivalent {HEADLINE_STYLE} headline at "
            "1080px canvas width, with tight editorial line-height and no "
            "shadow, outline, glow, or alternate display font."
        ),
        _quoted_block("Headline (bold, with ONE lime word or phrase)", [headline]),
        _quoted_block(
            "Body lines (left-aligned, in this exact order, one per line)",
            copy_lines,
        ),
        (
            "Keep all editorial text inside x=88..992 and y=140..1110. Leave "
            "x=88..200 and y=76..130 blank for the deterministic slide number. "
            "For a dominant image or illustration that occupies most of the lower "
            "content area, extend its lowest visible edge to y=1160 so it touches "
            "the footer divider with no empty band. Do not stop a large visual above "
            "the line. A compact, clearly centered visual that does not fill most of "
            "the width or lower area may float above the divider with a tight gap. "
            "Never cross below y=1160. Leave y=1161..1350 completely blank for the "
            "deterministic brand rail; do not draw a logo, handle, divider, arrow, "
            "slide number, footer text, or footer decoration."
        ),
    ]
    text_spec = "\n".join(allowed)
    template = _template_file(template_ref)
    if template is not None:
        prompt = (
            "The attached image is the slide layout template. Reproduce its "
            "layout, typography, colors, spacing and composition EXACTLY - "
            "change nothing about the design. Replace ONLY the text content "
            "with the text specified below.\n\n" + text_spec
        )
    else:
        prompt = (
            "Design a single Instagram carousel body slide, portrait 4:5.\n\n"
            f"{_style_prompt()}\n\n{text_spec}"
        )
    png = _call_images_api(prompt, template)
    result = _finalize(png, out_path)
    with Image.open(result) as rendered:
        normalized = normalize_accent_green(rendered)
        branded = apply_body_brand_rail(normalized, settings.ig_handle, slide_no)
    branded.save(result, format="PNG")
    logger.info("Rendered body slide %s -> %s", tag, result)
    return result


def generate_cta_image(
    cta_type: str,
    headline: str,
    lines: list[str],
    link_text: str,
    template_ref: str,
    out_path: str,
    slide_no: int | None = None,
) -> str:
    """Render the closing CTA slide (1080x1350 PNG) with gpt-image-2.

    Args:
        cta_type: One of ``"follow"``, ``"comment"``, ``"redirect"`` - picks
            the CTA variant per skills/design-skill.md.
        headline: The big centered CTA headline (rendered verbatim, e.g.
            "FOLLOW FOR MORE").
        lines: Supporting lines (question, emphasis line, etc.), rendered
            verbatim in order.
        link_text: A redirect destination or configured handle. The handle is
            composited deterministically in the final brand rail; a distinct
            redirect URL remains part of the generated CTA content.
        template_ref: Path to the CTA template reference image; falls back to
            the style prompt when empty/missing.
        out_path: Destination PNG path; parent directories are created.

    Returns:
        The absolute/normalized path of the written PNG as a string.
    """
    require_no_em_dash([headline, *lines, link_text], "CTA image copy")
    require_readable_text([headline, *lines, link_text], "CTA image copy")
    variant_hints = {
        "follow": "Follow CTA: make the value promise and action unmistakable.",
        "comment": "Comment CTA: a question line with a bold 'drop a comment' emphasis.",
        "redirect": "Redirect CTA: point to the full breakdown, with a short link line.",
    }
    hint = variant_hints.get(cta_type, variant_hints["follow"])
    all_lines = [line for line in lines if line and line.strip()]
    text_parts = [
        _VERBATIM_RULE,
        "",
        f"CTA variant: {cta_type}. {hint}",
        _quoted_block("CTA headline (big, centered)", [headline]),
        _quoted_block("Supporting lines (in this exact order)", all_lines),
    ]
    normalized_handle = settings.ig_handle.strip().lstrip("@").lower()
    normalized_link = link_text.strip().lstrip("@").lower()
    if link_text and normalized_link != normalized_handle:
        text_parts.append(
            f'Destination link (inside the CTA content area): "{link_text.strip()}"'
        )
    text_parts.append(
        "Keep all CTA text and visuals inside x=88..992 and y=140..1110. "
        "Leave x=88..200 and y=76..130 blank for the deterministic slide number. "
        "Leave y=1161..1350 completely blank for the deterministic favicon "
        "and handle rail. Do not draw a handle, footer portrait, logo, divider, "
        "footer decoration, slide number, or swipe arrow in that reserved area."
    )
    text_spec = "\n".join(text_parts)

    template = _template_file(template_ref)
    if template is not None:
        prompt = (
            "The attached image is the CTA slide layout template. Reproduce "
            "its layout, typography, colors, spacing and composition EXACTLY "
            "- change nothing about the design. Replace ONLY the text content "
            "with the text specified below.\n\n" + text_spec
        )
    else:
        prompt = (
            "Design the final call-to-action slide of an Instagram carousel, "
            "portrait 4:5.\n\n"
            f"{_style_prompt()}\n\n{text_spec}"
        )
    png = _call_images_api(prompt, template)
    result = _finalize(png, out_path)
    with Image.open(result) as rendered:
        normalized = normalize_accent_green(rendered)
        branded = apply_cta_brand_rail(
            normalized,
            settings.ig_handle,
            slide_no,
        )
    branded.save(result, format="PNG")
    logger.info("Rendered CTA slide (%s) -> %s", cta_type, result)
    return result
