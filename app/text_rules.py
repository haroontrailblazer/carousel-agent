"""Hard text rules shared by planning, writing, rendering, and QA."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


EM_DASH = "\u2014"


def find_em_dash(value: Any, path: str = "text") -> str | None:
    """Return the first field path containing an em dash, if one exists."""
    if isinstance(value, str):
        return path if EM_DASH in value else None
    if isinstance(value, Mapping):
        for key, item in value.items():
            found = find_em_dash(item, f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for index, item in enumerate(value):
            found = find_em_dash(item, f"{path}[{index}]")
            if found:
                return found
    return None


def require_no_em_dash(value: Any, context: str = "text") -> None:
    """Raise when published-facing text contains the forbidden character."""
    found = find_em_dash(value, context)
    if found:
        raise ValueError(
            f"{found} contains a forbidden em dash. Use a period, comma, "
            "colon, or parentheses instead."
        )


__all__ = ["EM_DASH", "find_em_dash", "require_no_em_dash"]
