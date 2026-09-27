"""The warnings every review surface shows about a cover's picture.

A cover built on the drawn plain background used to reach the reviewer
looking like any finished carousel, so nobody noticed until it was public;
a benchmark chart the picture check scored 1 went out the same way. The
Telegram review message, the console's review screen, the account-free
progress line and the QA gate all read this module, so they say the same
thing.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

COVER_WARNING = (
    "Cover warning: no picture could be found for this story, so the cover is "
    "a plain background. Reject with an image link to replace it."
)
PICTURE_WARNING = (
    "Cover warning: the automatic picture check did not approve the cover "
    "picture ({reason}). Check it, or reject with an image link to replace it."
)
SHARE_ALIKE_NOTE = (
    "Cover note: {count} free Wikimedia photo(s) of the story were left out "
    "because share-alike (CC BY-SA) licences are off. Set COVER_ALLOW_SHARE_ALIKE "
    "if the post may carry that licence."
)
# A text card or chart scored below this makes a weak cover (as in the
# cover agent's ladder).
_MIN_GRAPHIC_SCORE = 3


def weak_picture_reason(cover: Optional[Mapping[str, Any]]) -> str:
    """Why the cover's picture needs a person's eye, or '' when it does not.

    Only covers that record the picture check count: a cover built before
    those fields existed (``picture_verdict`` empty) is never flagged. The
    reviewer's own image link is never second-guessed.

    Args:
        cover: The Bundle's cover (a ``CoverSpec`` dump), or None.

    Returns:
        '' for a picture the check approved, the drawn background (see
        :data:`COVER_WARNING`) and old covers; else a short reason.
    """
    if not isinstance(cover, Mapping) or cover.get("drawn_background"):
        return ""
    if cover.get("picture_from_reviewer"):
        return ""
    verdict = str(cover.get("picture_verdict") or "")
    kind = str(cover.get("picture_kind") or "").replace("_", " ")
    try:
        score = int(cover.get("picture_score") or 0)
    except (TypeError, ValueError):
        score = 0
    if verdict == "unchecked":
        return "it was never checked"
    if kind == "text graphic" and score < _MIN_GRAPHIC_SCORE:
        return f"a text card or chart that scored {score} of 10"
    if verdict == "reject":
        kind = kind or "picture"
        article = "an" if kind[:1] in "aeiou" else "a"
        return f"it was rejected as {article} {kind} that scored {score} of 10"
    return ""


def cover_notice_lines(cover: Optional[Mapping[str, Any]]) -> list[str]:
    """Lines to show a reviewer about the cover, empty when nothing is wrong.

    The Wikimedia credit is not repeated here: it is already in the caption.

    Args:
        cover: The Bundle's cover (a ``CoverSpec`` dump), or None.

    Returns:
        ``[COVER_WARNING]`` for the drawn background, the picture warning for
        a picture the check did not approve (:func:`weak_picture_reason`),
        and with either, the share-alike note when such photos were left
        out; else ``[]``.
    """
    if not isinstance(cover, Mapping):
        return []
    lines: list[str] = []
    reason = weak_picture_reason(cover)
    if cover.get("drawn_background"):
        lines.append(COVER_WARNING)
    elif reason:
        lines.append(PICTURE_WARNING.format(reason=reason))
    try:
        skipped = int(cover.get("share_alike_skipped") or 0)
    except (TypeError, ValueError):
        skipped = 0
    if lines and skipped > 0:
        lines.append(SHARE_ALIKE_NOTE.format(count=skipped))
    return lines


__all__ = [
    "COVER_WARNING", "PICTURE_WARNING", "SHARE_ALIKE_NOTE", "cover_notice_lines",
    "weak_picture_reason",
]
