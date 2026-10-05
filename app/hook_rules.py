"""Checks that send a flat cover hook back to the planner once.

The planner's prompt already asked for a stake and a contrast, and it still
shipped news-report lines: "ANTHROPIC RUNS A WET LAB", "GOOGLE OPENS NEW AI TO
CYBER TEAMS FIRST", "AI MODELS NOW COMPETE ON COST AND SPEED". Each names a
subject and a verb and stops; nothing in it is for the reader and nothing is
left open, while the research brief's suggested angle for the same story held
exactly the contrast the cover needed.

These checks are deliberately narrow. They catch that one shape, a hook too
long to stay large on the saved design, and candidates that were never really
drafted. They are advice to the model, enforced once through the normal repair
note: the last attempt and a reviewer's rework skip them, because a reviewer
may dictate a title and a style rule must never stop a run.
"""
from __future__ import annotations

import re
from typing import Optional

from app.design_limits import HOOK_MAX_WORDS, hook_min_readable_size
from app.schemas import CarouselDesign, CarouselPlan

# Verbs that turn "SUBJECT VERB OBJECT" into a press-release line.
_REPORT_VERBS = {
    "ADDS", "ADDED", "ANNOUNCES", "ANNOUNCED", "BRINGS", "BROUGHT", "BUILDS",
    "BUILT", "COMPETE", "COMPETES", "CONFIRMS", "CONFIRMED", "DEBUTS", "DROPS",
    "DROPPED", "EXPANDS", "GETS", "GOT", "HAS", "HELPED", "HELPS",
    "INTRODUCES", "LAUNCHES", "LAUNCHED", "MAKES", "MADE", "MOVES", "NOW",
    "OPENS", "OPENED", "PLANS", "PUT", "PUTS", "RACE", "RACES", "RELEASES", "RELEASED",
    "REVEALS", "ROLLS", "RUNS", "SAYS", "SET", "SETS", "SHIPS", "SHIPPED",
    "TESTS", "UNVEILS", "UPDATES", "UPGRADES", "USED", "USES",
}
# Words that already give the reader a stake or leave a question open.
_STAKE_WORDS = {
    "YOU", "YOUR", "YOURS", "BUT", "NOT", "NO", "NEVER", "ONLY", "STILL",
    "WITHOUT", "INSTEAD", "EVEN", "YET", "NOBODY", "NOTHING", "WHY", "HOW",
    "WHAT", "STOP", "DON'T", "DOESN'T", "CAN'T", "WON'T", "ISN'T",
}
_WORD = re.compile(r"[A-Z0-9][A-Z0-9'\-.]*", re.I)
# A lever the planner uses when the request itself dictates the exact title.
USER_TITLE_LEVER = "user_title"
MIN_CANDIDATES = 4
MIN_LEVERS = 3


def _words(text: str) -> list[str]:
    return [w.strip(".'").upper() for w in _WORD.findall(text or "")]


def _norm(text: str) -> str:
    return " ".join((text or "").split()).upper()


def is_report_line(hook: str) -> bool:
    """True for a bare "SUBJECT VERB OBJECT" line with no stake for the reader.

    A second beat (a full stop inside the hook), a number, a question, or any
    word that speaks to the reader or sets up a contrast exempts it: those are
    the shapes the cover rules ask for.
    """
    text = (hook or "").strip()
    words = _words(text)
    if len(words) < 3:
        return False
    body = text.rstrip(".!? ")
    if "." in re.sub(r"\d\.\d", "", body) or "?" in text:
        return False
    if any(ch.isdigit() for ch in text):
        return False
    if any(word in _STAKE_WORDS for word in words):
        return False
    # The report verb sits right after a short subject (one to three words).
    return any(word in _REPORT_VERBS for word in words[1:4])


def hook_problems(plan: CarouselPlan, design: Optional[CarouselDesign]) -> list[str]:
    """Everything that should send this plan's hook back for one rewrite."""
    # Local import: media_tools pulls in Pillow fonts and ffmpeg helpers.
    from app.tools import media_tools

    hook = " ".join((plan.hook_title or "").split())
    if not hook:
        return []
    candidates = plan.hook_candidates
    chosen = next((c for c in candidates if _norm(c.text) == _norm(hook)), None)
    if chosen is not None and chosen.lever.strip().lower() == USER_TITLE_LEVER:
        return []  # the request dictated this exact title

    problems: list[str] = []
    levers = {c.lever.strip().lower() for c in candidates if c.lever.strip()}
    if len(candidates) < MIN_CANDIDATES or len(levers) < MIN_LEVERS:
        problems.append(
            f"hook_candidates must hold at least {MIN_CANDIDATES} drafted hooks on at least "
            f"{MIN_LEVERS} different levers (it has {len(candidates)} on {len(levers)})"
        )
    if chosen is None:
        problems.append("hook_title must be one of the hook_candidates, copied exactly")
    if is_report_line(hook):
        problems.append(
            f"the hook {hook!r} reads as a news report (a subject, a verb, an object) with "
            "nothing for the reader and nothing left open; rebuild it from the research "
            "brief's suggested_angle with a two-beat contrast, the reader's own stake, or "
            "a number with its meaning"
        )
    words = len(hook.split())
    if words > HOOK_MAX_WORDS:
        problems.append(f"the hook has {words} words; the budget is {HOOK_MAX_WORDS}")
    else:
        size = media_tools.fitted_title_size(hook, design)
        title_size = design.cover.title_size if design is not None else media_tools.COVER_TITLE_FONT_SIZE
        floor = hook_min_readable_size(title_size)
        if size < floor:
            problems.append(
                f"the hook renders at only {size}px on this design (readable floor {floor}px); "
                "cut filler words, keep the stake"
            )
    return problems
