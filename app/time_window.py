"""The dates a typed request asks about, and the dates research facts carry.

Every model in the pipeline knows the date only from its training data, which
is months old. A topic typed on 26 September 2026 asking for "this week major
ai updates like new model launches" came back with March launches presented
as this week's news (run-3c4107e390df): no prompt or search call carried the
real date, and nothing read "this week" as dates.

This module turns the words the person typed into a UTC date range once, when
the run starts, and checks the research brief against it:

- :func:`resolve` reads "today", "this week", "last month", an explicit date
  or a recency word ("latest", "trending") from a typed topic and returns the
  window dict stored under ``K_TIME_WINDOW``. Only typed topic runs
  (``source_name == "adhoc"``) get a window: an RSS or URL story is about its
  own article, whatever words its headline happens to use.
- :func:`fact_dates` finds the dates a fact's text or ``date`` field names.
- :func:`window_problems` lists what is wrong with a brief's dates; research
  saves and ``validate_output`` both use it.
- :func:`context_note` is the "today is ..." note injected as
  ``{time_context?}`` into research, planner and phrasing, and
  :data:`TIME_SECTION` the prompt section that shows it with the time-word
  rule for the two writing agents.

Pure: no I/O. :func:`today_utc` is the one place the clock is read, so tests
pin "today" by patching it.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

#: At least this share of a strict window's facts not marked background must
#: be dated inside it.
_MIN_FRESH_SHARE = 0.5
#: A recency word ("latest", "trending") prefers the last two weeks...
_SOFT_DAYS = 13
#: ...and, for a news-shaped request, calls anything older than this stale.
_SOFT_OLDEST_DAYS = 30
#: Dates this far past the window's end are schedules ("pricing changes on
#: 1 January 2027"), not when the fact happened. The day of slack covers a
#: source in a time zone already on tomorrow.
_SCHEDULE_SLACK = timedelta(days=1)

# English names, not calendar.month_name: that follows the process locale.
_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
)
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS = {name.lower(): number for number, name in enumerate(_MONTH_NAMES, 1)}
_MONTHS.update({name[:3].lower(): number for number, name in enumerate(_MONTH_NAMES, 1)})
_MONTHS["sept"] = 9

_MON = (
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?![a-z])\.?"
)
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
_ISO_DAY = re.compile(r"(?<!\d)(20\d\d)-(\d\d)-(\d\d)(?!\d)")
_ISO_MONTH = re.compile(r"(?<![\d-])(20\d\d)-(\d\d)(?!-?\d)")
_MDY = re.compile(rf"\b{_MON}\s+{_DAY}(?:,\s*|\s+)(20\d\d)\b", re.I)
_DMY = re.compile(rf"\b{_DAY}\s+(?:of\s+)?{_MON},?\s+(20\d\d)\b", re.I)
_MY = re.compile(rf"\b{_MON}\s+(20\d\d)\b", re.I)
_MD = re.compile(rf"\b{_MON}\s+{_DAY}\b", re.I)
_DM = re.compile(rf"\b{_DAY}\s+(?:of\s+)?{_MON}", re.I)
#: A whole month named without a year, only in a request and only after a
#: word that makes it a date ("stories of September", "since August"):
#: "March Madness" is no month.
_BARE_MONTH = re.compile(
    r"\b(?:of|in|from|since|during|for|after|through|throughout|until)\s+(?:the\s+month\s+of\s+)?"
    r"(january|february|march|april|may|june|july|august|september|october|november|december)\b",
    re.I,
)
#: In a request, a yearless "5 august" is a date only after one of these
#: words or with an ordinal ("25th"): "GPT 5 august launch recap", "android
#: 16 march security patch" and "top 10 march madness moments" name versions
#: and ranks, not days.
_DATE_CUES = frozenset({
    "on", "since", "from", "the", "of", "after", "before", "until", "till", "to", "through",
    "and", "between", "by", "starting", "dated",
})
_ORDINAL_DAY = re.compile(r"\d(?:st|nd|rd|th)\b", re.I)
#: A single date these words introduce (or "onwards" follows) opens the
#: window up to the request day: "since September 20" is 20..26 September,
#: not the week before it.
_SINCE_BEFORE = re.compile(
    r"\b(?:since|after|from|starting(?:\s+(?:from|on))?|beginning(?:\s+(?:from|on))?)\s+(?:the\s+)?$",
    re.I,
)
_SINCE_AFTER = re.compile(r"^\s*(?:onwards?|and\s+(?:later|after)|(?:to|till|until)\s+(?:now|today|date))\b", re.I)

_NEWS_WORD = (
    r"(?:news|headlines?|updates?|launch(?:es|ed)?|releases?|released|announcements?"
    r"|announced|stories|happen(?:ed|ing)|unveiled|revealed|shipped|dropped|reported|going\s+on)"
)
#: "today" is a date only next to a news word or event verb ("what happened
#: today", "today's AI news"); "AI will change healthcare today" means "now".
_TODAY_NEWS = re.compile(
    rf"\b{_NEWS_WORD}\W+(?:\w+\W+){{0,2}}?(?:today|tonight)\b"
    rf"|\b(?:today|tonight)(?:['\u2019]s)?\W+(?:\w+\W+){{0,2}}?{_NEWS_WORD}\b",
    re.I,
)
_TODAY = re.compile(r"\b(?:today|tonight)\b", re.I)
_RECENT = re.compile(
    r"\b(?:latest|newest|recent(?:ly)?|trending|viral|breaking|what['\u2019]?s\s+new"
    r"|current(?:ly)?|right\s+now|weekly|what['’]?s\s+happening"
    r"|(?:just|newly)\s+(?:launched|released|announced|dropped|out))\b"
    r"|\bnew\s+(?:\w+\s+){0,2}?(?:launch|release|update|feature|version|news)(?:e?s)?\b",
    re.I,
)
#: A recency request about news gets a hard age limit; "latest iPhone camera
#: features" is an explainer, where an older fact is still the current one.
_NEWSY = re.compile(
    r"\b(?:news|headlines?|updates?|launch(?:es)?|releases?|announcements?|models?|drops?|stories"
    r"|roundups?|happening|breaking|just\s+(?:launched|released|announced|dropped))\b",
    re.I,
)
#: A period word is a hard date limit unless the request is advice with no
#: word about events: "5 habits to build this month" and "productivity tools
#: to try this week" have facts with no dates, and a strict window stopped
#: them for good. "AI this week" stays strict: news is what this pipeline
#: makes, and a soft window would let March launches pass as this week's.
_EVENT_REQUEST = re.compile(rf"{_NEWSY.pattern}|\b{_NEWS_WORD}\b|{_RECENT.pattern}", re.I)
_EVERGREEN = re.compile(
    r"\b(?:how\s+to|tips?|tricks?|habits?|ideas?|ways\s+to|guides?|tutorials?|recipes?"
    r"|routines?|workouts?|exercises?|lessons?|things\s+to"
    r"|to\s+(?:try|build|read|watch|learn|use|do|make|cook|visit|practi[cs]e))\b",
    re.I,
)


def _this_month(today: date) -> date:
    return min(today.replace(day=1), today - timedelta(days=6))


def _last_month(today: date) -> date:
    return (today.replace(day=1) - timedelta(days=1)).replace(day=1)


def _this_weekend(today: date) -> date:
    """The Friday before the latest Saturday on or before today."""
    return today - timedelta(days=(today.weekday() - 5) % 7 + 1)


_COUNT_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fourteen": 14, "thirty": 30,
    "few": 3, "a few": 3, "couple": 2, "couple of": 2, "a couple": 2, "a couple of": 2,
    "several": 5,
}
#: "past 24 hours", "last 3 days", "the past two weeks", "over the past few days".
_SPAN = re.compile(
    r"\b(?:last|past|previous)\s+(\d{1,3}|" + "|".join(
        sorted((re.escape(w).replace(r"\ ", r"\s+") for w in _COUNT_WORDS), key=len, reverse=True)
    ) + r")\s+(hours?|days?|weeks?|months?)\b",
    re.I,
)


def _span_start(match: re.Match, today: date) -> date:
    """The first day of a "last N days" span, counted as "last 7 days" always was."""
    word = " ".join(match[1].lower().split())
    count = int(word) if word.isdigit() else _COUNT_WORDS.get(word, 1)
    unit = match[2].lower().rstrip("s")
    if unit == "hour":
        days = max(-(-count // 24), 1)
    elif unit == "day":
        days = max(count - 1, 1)
    elif unit == "week":
        days = 7 * count - 1
    else:
        days = 30 * count
    return today - timedelta(days=days)


#: Relative periods a person types, each with the first day it covers.
_PERIODS: tuple[tuple[re.Pattern, Any], ...] = (
    (re.compile(r"\byesterday\b", re.I), lambda d: d - timedelta(days=2)),
    (re.compile(r"\bthis\s+morning\b|\blast\s+night\b", re.I), lambda d: d - timedelta(days=1)),
    (re.compile(r"\bthis\s+weekend\b", re.I), _this_weekend),
    (re.compile(
        r"\bthis\s+(?:past\s+)?week\b|\b(?:the\s+)?past\s+week\b"
        r"|\b(?:the|this)\s+week\s+in\b|\bof\s+the\s+week\b|\bweek\s+in\s+review\b"
        r"|\b(?:last|past)\s+(?:7|seven)\s+days\b", re.I),
     lambda d: d - timedelta(days=6)),
    (re.compile(r"\blast\s+week\b", re.I), lambda d: d - timedelta(days=13)),
    (re.compile(r"\bthis\s+month\b", re.I), _this_month),
    (re.compile(r"\b(?:the\s+)?past\s+month\b|\b(?:last|past)\s+(?:30|thirty)\s+days\b", re.I),
     lambda d: d - timedelta(days=30)),
    (re.compile(r"\blast\s+month\b", re.I), _last_month),
)


def today_utc() -> date:
    """Today's date in UTC: the one place this pipeline reads the clock."""
    return datetime.now(timezone.utc).date()


def format_day(value: date) -> str:
    """A date as every prompt and message writes it: "26 September 2026"."""
    return f"{value.day} {_MONTH_NAMES[value.month - 1]} {value.year}"


def _as_date(value: Any) -> Optional[date]:
    """A window field or ``published_at`` value as a date (None when unset)."""
    if isinstance(value, datetime):
        return (value.astimezone(timezone.utc) if value.tzinfo else value).date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if len(text) > 10:
            return _as_date(datetime.fromisoformat(text.replace("Z", "+00:00")))
        return date.fromisoformat(text)
    except ValueError:
        return None


def _span_text(span: tuple[date, date]) -> str:
    first, last = span
    if first == last:
        return format_day(first)
    if (first.month, first.day, last.month, last.day) == (1, 1, 12, 31) and first.year == last.year:
        return str(first.year)
    if first.day == 1 and last.day == calendar.monthrange(last.year, last.month)[1] and first.month == last.month:
        return f"{_MONTH_NAMES[first.month - 1]} {first.year}"
    return f"{format_day(first)} to {format_day(last)}"


def _date_matches(
    text: str, anchor: date, *, request: bool = False,
) -> list[tuple[int, str, date, date]]:
    """Every date in *text* as ``(position, matched text, first day, last day)``.

    A day or month with no year takes *anchor*'s year, or the year before
    when that would put it more than the schedule slack after *anchor*
    ("December 5" on 26 September 2026 is December 2025). ``request`` reads
    a typed request: a yearless "5 august" counts only after a date word or
    with an ordinal (see :data:`_DATE_CUES`), and a month named alone after
    one ("stories of September") counts too.
    """
    found: list[tuple[int, str, date, date]] = []
    taken: list[tuple[int, int]] = []

    def free(match: re.Match) -> bool:
        return all(match.end() <= a or match.start() >= b for a, b in taken)

    def add(match: re.Match, first: date, last: date) -> None:
        found.append((match.start(), match.group(0).strip(), first, last))
        taken.append(match.span())

    def add_month(match: re.Match, year: int, month: int) -> None:
        if 1 <= month <= 12:
            add(match, date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1]))

    def cued(match: re.Match) -> bool:
        words = text[:match.start()].lower().split()
        return bool(words) and words[-1].strip("(,") in _DATE_CUES or bool(_ORDINAL_DAY.search(match[0]))

    for match in _ISO_DAY.finditer(text):
        try:
            day = date(int(match[1]), int(match[2]), int(match[3]))
        except ValueError:
            continue
        add(match, day, day)
    # Full dates first, then months with a year, then yearless days, so
    # "September 2026" is never read as "September 20" of the anchor's year.
    for pattern, month_first, yearless in ((_MDY, True, False), (_DMY, False, False),
                                           (_MY, True, None), (_ISO_MONTH, True, None),
                                           (_MD, True, True), (_DM, False, True)):
        for match in pattern.finditer(text):
            if not free(match):
                continue
            if yearless is None:  # a whole month
                if pattern is _MY:
                    add_month(match, int(match[2]), _MONTHS[match[1].lower()])
                else:
                    add_month(match, int(match[1]), int(match[2]))
                continue
            month, day = (match[1], match[2]) if month_first else (match[2], match[1])
            if yearless and month == "may":
                continue  # "they may 5 times": only a capitalised May is a month
            if yearless and request and not month_first and not cued(match):
                continue  # "GPT 5 august launch recap"
            year = anchor.year if yearless else int(match[3])
            try:
                value = date(year, _MONTHS[month.lower()], int(day))
                if yearless and value > anchor + _SCHEDULE_SLACK:
                    value = value.replace(year=year - 1)
            except (ValueError, KeyError):
                continue
            add(match, value, value)
    if request:
        for match in _BARE_MONTH.finditer(text):
            if not free(match) or match[1] == "may":
                continue
            month = _MONTHS[match[1].lower()]
            year = anchor.year if month <= anchor.month else anchor.year - 1
            found.append((match.start(1), match[1], date(year, month, 1),
                          date(year, month, calendar.monthrange(year, month)[1])))
            taken.append(match.span())
    return sorted(found)


def fact_dates(text: str, anchor: date) -> list[tuple[date, date]]:
    """The dates *text* names, as ``(first day, last day)`` spans in text order.

    Reads ISO dates and months (``2026-09-22``, ``2026-09``), "March 17,
    2026", "17 March 2026", "Sep. 2, 2026" and "September 2026" (the whole
    month). A day and month with no year ("13 July") takes *anchor*'s year
    (the year before when that is still to come). A text that is only a
    year (a fact's ``date`` of ``2017``, when the source gives no more) is
    that whole year.
    """
    text = str(text or "")
    year = _ONLY_YEAR.fullmatch(text.strip())
    if year:
        return [(date(int(year[1]), 1, 1), date(int(year[1]), 12, 31))]
    return [(first, last) for _pos, _text, first, last in _date_matches(text, anchor)]


_BARE_YEAR = re.compile(r"(?<![\d-])(20\d\d)(?![\d-])")
_ONLY_YEAR = re.compile(r"((?:19|20)\d\d)")


def names_only_earlier_dates(text: str, window: Any) -> bool:
    """True when *text* names dates or years and every one ends before the window.

    For a search query: "Mistral launch March 2026" against a September week
    is looking in the wrong month.
    """
    dates = _window_dates(window)
    if dates is None:
        return False
    text = str(text or "")
    matches = _date_matches(text, _as_date(window.get("requested_on")) or dates[1])
    spans = [(first, last) for _pos, _text, first, last in matches]
    for year in _BARE_YEAR.finditer(text):
        if not any(pos <= year.start() < pos + len(found) for pos, found, _f, _l in matches):
            spans.append((date(int(year[1]), 1, 1), date(int(year[1]), 12, 31)))
    return bool(spans) and all(last < dates[0] for _first, last in spans)


def _explicit_range(text: str, today: date) -> Optional[tuple[str, date, date]]:
    """The dates a request names, up to today, as ``(phrase, start, end)``."""
    matches = [m for m in _date_matches(text, today, request=True) if m[2] <= today]
    if not matches:
        return None
    spans = sorted({(first, min(last, today)) for _pos, _text, first, last in matches})
    phrase = ", ".join(dict.fromkeys(m[1] for m in matches))
    if len(spans) == 1:
        position, found = matches[0][0], matches[0][1]
        before = _SINCE_BEFORE.search(text[:position])
        after = _SINCE_AFTER.match(text[position + len(found):])
        if before or after:
            # "since September 20", "from August onwards": up to the request day.
            words = [before[0] if before else "", phrase, after[0] if after else ""]
            return " ".join(" ".join(words).lower().split()), spans[0][0], today
    if len(spans) == 1 and spans[0][0] == spans[0][1]:
        # A named day ("on this 25th September 2026") means the news up to it.
        return phrase, spans[0][0] - timedelta(days=6), spans[0][0]
    return phrase, min(s[0] for s in spans), max(s[1] for s in spans)


def resolve(news: Mapping[str, Any] | None, now: datetime) -> dict:
    """Resolve the time window a news item's request asks about.

    Args:
        news: The ``NewsItem`` dict. Only ``source_name == "adhoc"`` (a typed
            topic) reads its title and body for time words.
        now: When the request was made; its UTC date is "today" (D).

    Returns:
        ``{"requested_on", "phrase", "start", "end", "strict", "oldest"}`` plus
        ``story_published_on`` when the item has ``published_at``. Dates are
        ISO strings. No window means ``start`` and ``oldest`` are both ``""``.
        A strict window ("this week" = D-6..D, "yesterday" = D-2..D, "past 24
        hours" = D-1..D, "last 3 days" = D-2..D, a named day = the week up to
        it, "since" a day or month = from it up to D, a named month = that
        month up to D, the latest one when no year is given) needs its facts
        dated inside it. A period word in a request that reads as advice
        (habits, tips, how to, things to try ...) and names no event (news,
        launches, what happened, latest ...) is only preferred: "5 habits to
        build this month" gets the period as a soft window with no limit. A
        recency word ("latest") makes a soft window of D-13..D, with
        ``oldest`` D-30 for a news-shaped request, else no limit.
    """
    today = _as_date(now) or today_utc()
    news = news if isinstance(news, Mapping) else {}
    window: dict[str, Any] = {
        "requested_on": today.isoformat(), "phrase": "", "start": "", "end": "",
        "strict": False, "oldest": "",
    }
    published = _as_date(news.get("published_at"))
    if published:
        window["story_published_on"] = published.isoformat()
    if news.get("source_name") != "adhoc":
        return window
    text = f"{news.get('title') or ''}\n{news.get('body') or ''}"

    phrases: list[tuple[int, str]] = []
    starts: list[date] = []
    for pattern, first_day in _PERIODS:
        match = pattern.search(text)
        if match:
            phrases.append((match.start(), " ".join(match.group(0).lower().split())))
            starts.append(first_day(today))
    for match in _SPAN.finditer(text):
        phrases.append((match.start(), " ".join(match.group(0).lower().split())))
        starts.append(_span_start(match, today))
    today_match = _TODAY_NEWS.search(text)
    if today_match:
        phrases.append((today_match.start(), "today"))
        starts.append(today - timedelta(days=1))
    period = (min(starts), today) if starts else None
    period_phrase = " ".join(dict.fromkeys(p for _pos, p in sorted(set(phrases))))

    explicit = _explicit_range(text, today)
    strict: Optional[tuple[str, date, date]] = None
    if period and explicit:
        # "this week September 2026": the days both name. When they do not
        # overlap, the explicit dates are what the person meant.
        start, end = max(period[0], explicit[1]), min(period[1], explicit[2])
        if start <= end:
            strict = (f"{period_phrase} {explicit[0]}", start, end)
        else:
            strict = explicit
    elif period and (_EVENT_REQUEST.search(text) or not _EVERGREEN.search(text)):
        strict = (period_phrase, period[0], period[1])
    elif explicit:
        strict = explicit
    if strict:
        phrase, start, end = strict
        window.update(phrase=phrase, start=start.isoformat(), end=end.isoformat(),
                      strict=True, oldest=start.isoformat())
        return window
    if period:
        # "5 habits to build this month": prefer those dates, check none.
        # A strict window stopped such runs, since their facts carry no dates.
        window.update(phrase=period_phrase, start=period[0].isoformat(), end=today.isoformat())
        return window

    recent = _RECENT.search(text) or _TODAY.search(text)
    if recent:
        window.update(
            phrase=re.sub(r"\s+", " ", recent.group(0).lower()),
            start=(today - timedelta(days=_SOFT_DAYS)).isoformat(),
            end=today.isoformat(),
            oldest=(today - timedelta(days=_SOFT_OLDEST_DAYS)).isoformat() if _NEWSY.search(text) else "",
        )
    return window


def _window_dates(window: Any) -> Optional[tuple[date, date, Optional[date], bool]]:
    """``(start, end, oldest, strict)`` for a window that exists, else None."""
    if not isinstance(window, Mapping):
        return None
    start, end = _as_date(window.get("start")), _as_date(window.get("end"))
    if start is None or end is None:
        return None
    return start, end, _as_date(window.get("oldest")), bool(window.get("strict"))


def describe_range(window: Any) -> str:
    """The window's dates in words ("20 September 2026 to 26 September 2026")."""
    dates = _window_dates(window)
    if dates is None:
        return ""
    start, end = dates[0], dates[1]
    return format_day(start) if start == end else f"{format_day(start)} to {format_day(end)}"


def window_rule(window: Any) -> str:
    """The date rule a window sets, for error messages.

    'inside 20 September 2026 to 26 September 2026 ("this week", UTC)', or for
    a soft window 'on or after 27 August 2026 ("trending", UTC)'; empty when
    no rule applies.
    """
    dates = _window_dates(window)
    if dates is None or not (dates[3] or dates[2]):
        return ""
    where = f"inside {describe_range(window)}" if dates[3] else f"on or after {format_day(dates[2])}"
    return f'{where} ("{window.get("phrase") or ""}", UTC)'


def _fact_field(fact: Any, name: str, default: Any = "") -> Any:
    if isinstance(fact, Mapping):
        return fact.get(name, default)
    return getattr(fact, name, default)


def window_problems(facts: list, window: Any, *, relaxed: bool = False) -> list[str]:
    """What is wrong with a research brief's fact dates for this window.

    Args:
        facts: ``key_facts`` entries (dicts or ``ResearchFact`` models) with
            ``fact``, ``date`` and ``background``.
        window: The ``K_TIME_WINDOW`` dict, or None.
        relaxed: Only require one fact dated inside the window. Used on a
            step's last attempt and under human rework, so a quiet week or a
            misread time word never stops a run that did find current news.

    Returns:
        Problem sentences, empty when the dates are fine. Empty with no
        window, and for a soft window with no age limit ("latest iPhone
        camera features" is an explainer, not news).

    Strict windows need every non-background fact dated, flag a fact whose
    dates all fall before the window (or, for a window that ended before
    the request day, after it), and need at least half of the facts not
    marked background inside it. Background facts are never counted: the
    comparison context a request asks for ("and the comparative open weight
    models") made a good brief fail. A year alone never places a fact inside
    a window, only before it. A soft window with an age limit flags facts
    older than that limit. Dates more than a day after the window (or after
    the request day) are schedules and ignored.
    """
    dates = _window_dates(window)
    if dates is None:
        return []
    start, end, oldest, strict = dates
    if not strict and oldest is None:
        return []
    anchor = _as_date(window.get("requested_on")) or end
    limit = end + _SCHEDULE_SLACK
    # A window that ended before the request day ("the week of 14 September",
    # asked on the 26th): a date between its end and the request day is when
    # the fact happened, just too late, not a schedule.
    after_limit = anchor + _SCHEDULE_SLACK if end < anchor else limit
    floor = start if strict else oldest

    fresh, total, stale, after, vague, undated = 0, 0, [], [], [], []
    for number, fact in enumerate(facts or [], 1):
        if _is_background(_fact_field(fact, "background", False)):
            continue  # older context by its own label; never counted as news
        total += 1
        spans = fact_dates(str(_fact_field(fact, "date") or ""), anchor)
        spans += fact_dates(str(_fact_field(fact, "fact") or ""), anchor)
        past = [span for span in spans if span[0] <= limit]
        # A day or a month says when; a whole year only says "not before".
        exact = [span for span in past if (span[1] - span[0]).days <= 31]
        known = exact or past
        later = sorted(span for span in spans if limit < span[0] <= after_limit)
        if any(last >= floor for _first, last in exact):
            fresh += 1
        elif later:
            after.append(f"fact {number} is dated {_span_text(later[0])}, after {format_day(end)}")
        elif known and all(last < floor for _first, last in known):
            newest = max(known, key=lambda span: span[1])
            stale.append(f"fact {number} is dated {_span_text(newest)}, before {format_day(floor)}")
        elif strict:
            (vague if past else undated).append(number)

    problems: list[str] = []
    if not fresh:
        problems.append(
            f"no fact is dated inside {describe_range(window)}" if strict
            else f"no fact is dated on or after {format_day(floor)}"
        )
    if relaxed:
        return problems
    if strict and fresh and fresh < _MIN_FRESH_SHARE * total:
        problems.append(
            f"only {fresh} of the {total} facts not marked background are dated inside "
            f"{describe_range(window)}; at least half must be"
        )
    problems += stale + after
    for numbers, one, many in ((undated, "has no date", "have no date"),
                               (vague, "gives only a year", "give only a year")):
        if numbers:
            listed = ", ".join(str(n) for n in numbers)
            problems.append(f"fact {listed} {one}" if len(numbers) == 1 else f"facts {listed} {many}")
    return problems


def _is_background(value: Any) -> bool:
    """A fact's ``background`` flag, which a model may send as the string "false"."""
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return bool(value)


def context_note(window: Any, today: date) -> str:
    """The "today is ..." note injected as ``{time_context?}``.

    Holds no braces, so ADK's instruction templating never reads the note
    itself as a placeholder.
    """
    lines = [
        f"Today is {_WEEKDAYS[today.weekday()]}, {format_day(today)} (UTC). Your own sense of "
        "the current date comes from training data and is months out of date, so "
        "resolve every relative time word (today, this week, latest, new) against "
        "this date, in your searches and in your writing."
    ]
    window = window if isinstance(window, Mapping) else {}
    requested = _as_date(window.get("requested_on"))
    if requested and requested != today:
        lines.append(f"The request was made on {format_day(requested)}.")
    published = _as_date(window.get("story_published_on"))
    if published:
        lines.append(f"The story was published on {format_day(published)}.")
    dates = _window_dates(window)
    phrase = str(window.get("phrase") or "")
    if dates and dates[3]:
        lines.append(
            f'The request says "{phrase}", which means {describe_range(window)} (UTC, '
            "inclusive). Every fact presented as news must be dated inside those dates. "
            "Older facts may appear only as background, with their date, never as new."
        )
    elif dates:
        tail = (f" Facts dated before {format_day(dates[2])} are too old to present as news."
                if dates[2] else "")
        lines.append(
            f'The request asks for "{phrase}" things: prefer facts dated '
            f"{describe_range(window)}, confirm nothing newer exists, and date every fact.{tail}"
        )
    else:
        lines.append(
            "The request names no time period, so no date window applies; still give "
            "each fact the date its source states."
        )
    return " ".join(lines).replace("{", "(").replace("}", ")")


def search_hint(window: Any) -> str:
    """One sentence telling the web search which dates the request covers."""
    dates = _window_dates(window)
    if dates is None:
        return ""
    phrase = str(window.get("phrase") or "")
    if dates[3]:
        return (
            f'The request covers {describe_range(window)} ("{phrase}"). Look for facts '
            "published or announced in that range, and say plainly if you find none."
        )
    tail = f" Anything before {format_day(dates[2])} is too old to call new." if dates[2] else ""
    return (
        f'The request asks for the newest information ("{phrase}"). Prefer sources '
        f"from {describe_range(window)}, and check that nothing newer exists.{tail}"
    )


#: Prompt section for the writing agents. The note fills ``{time_context?}``;
#: the rule below it keeps "this week" off a March launch.
TIME_SECTION = """\
## Today's date and the requested time window

{time_context?}

Time words (today, this week, this month, new, latest, just launched, now) may
describe only a fact whose date in the research brief is inside the requested
window or, when no window applies, within 14 days of today. Give any other
fact its date or no time word at all, and never call a background fact new.
"""


def with_time_context(instruction: str) -> str:
    """Append :data:`TIME_SECTION` to an agent instruction."""
    return instruction.rstrip() + "\n\n" + TIME_SECTION


__all__ = [
    "TIME_SECTION",
    "context_note",
    "describe_range",
    "fact_dates",
    "format_day",
    "names_only_earlier_dates",
    "resolve",
    "search_hint",
    "today_utc",
    "window_problems",
    "window_rule",
    "with_time_context",
]
