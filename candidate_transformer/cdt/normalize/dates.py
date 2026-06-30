"""Date normalisation (design doc section 4 & edge case "Ambiguous date").

Output canonical form is ``YYYY-MM`` (or ``YYYY`` when only the year is known),
with a ``precision`` of year|month|day and an ``is_current`` flag.

Rules:
  * "present"/"current"/"now" -> value=None, is_current=True (never a fake date).
  * Ambiguous numeric dates (e.g. 03/04/21): use a locale hint if given; if a
    component is > 12 the order is forced; otherwise DOWN-PRECISION to year --
    we never guess the month.
  * Two-digit years use a FIXED pivot (deterministic, no wall-clock).
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

from .text import clean_text

_PIVOT = 50  # 00..49 -> 20xx ; 50..99 -> 19xx  (fixed, deterministic)

_PRESENT = {"present", "current", "now", "ongoing", "till date", "to date"}

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _expand_year(y: int) -> int:
    if y >= 100:
        return y
    return (2000 + y) if y < _PIVOT else (1900 + y)


def _fmt(year: int, month: Optional[int]) -> Tuple[str, str]:
    if month is None:
        return f"{year:04d}", "year"
    return f"{year:04d}-{month:02d}", "month"


def normalize_date(raw, locale_hint: Optional[str] = None) -> Tuple[Optional[str], float, str, dict]:
    """Return ``(value, quality, reason, meta)``.

    ``value`` is ``YYYY-MM`` / ``YYYY`` / ``None``. ``meta`` carries
    ``{"precision": ..., "is_current": bool}``. ``locale_hint`` in {"US","EU"}
    disambiguates day/month order. Never raises.
    """
    s = clean_text(raw).lower().strip().strip(".")
    meta = {"precision": None, "is_current": False}
    if not s:
        return None, 0.0, "empty", meta
    if s in _PRESENT:
        meta["is_current"] = True
        return None, 1.0, "present_marker", meta

    # ISO-ish: YYYY-MM-DD / YYYY-MM / YYYY  (also accepts / or .)
    m = re.match(r"^(\d{4})(?:[-/.](\d{1,2}))?(?:[-/.](\d{1,2}))?$", s)
    if m:
        year = int(m.group(1))
        month = int(m.group(2)) if m.group(2) else None
        if month is not None and not (1 <= month <= 12):
            return None, 0.0, f"bad_month:{month}", meta
        val, prec = _fmt(year, month)
        meta["precision"] = prec
        return val, 1.0, "", meta

    # "Mon YYYY" / "Month YYYY" / "Mon-YYYY"
    m = re.match(r"^([a-z]{3,9})[\s\-/,]+(\d{2,4})$", s)
    if m and m.group(1) in _MONTHS:
        month = _MONTHS[m.group(1)]
        year = _expand_year(int(m.group(2)))
        val, prec = _fmt(year, month)
        meta["precision"] = prec
        return val, 1.0, "", meta

    # "YYYY Mon"
    m = re.match(r"^(\d{4})[\s\-/,]+([a-z]{3,9})$", s)
    if m and m.group(2) in _MONTHS:
        year = int(m.group(1))
        month = _MONTHS[m.group(2)]
        val, prec = _fmt(year, month)
        meta["precision"] = prec
        return val, 1.0, "", meta

    # Numeric A/B/C with separators / - .
    m = re.match(r"^(\d{1,4})[\-/.](\d{1,2})(?:[\-/.](\d{1,4}))?$", s)
    if m:
        a, b, c = m.group(1), m.group(2), m.group(3)
        # MM/YYYY or YYYY/MM (no day component)
        if c is None:
            ai, bi = int(a), int(b)
            if len(a) == 4:                       # YYYY/MM
                year, month = ai, bi
            elif len(b) == 4:                     # MM/YYYY
                year, month = bi, ai
            else:                                  # MM/YY -> month + 2-digit year
                year, month = _expand_year(bi), ai
            if not (1 <= month <= 12):
                # could not be a month -> keep just the year, down-precision
                meta["precision"] = "year"
                return f"{year:04d}", 0.6, "ambiguous_down_to_year", meta
            val, prec = _fmt(year, month)
            meta["precision"] = prec
            return val, 1.0, "", meta

        # Three components -> day is involved and may be ambiguous.
        ai, bi, ci = int(a), int(b), int(c)
        if len(a) == 4:                            # YYYY-MM-DD
            year, month = ai, bi
            val, prec = _fmt(year, month)
            meta["precision"] = prec
            return val, 1.0, "", meta
        year = _expand_year(ci)
        # Decide month vs day order for the first two components.
        if ai > 12 and bi <= 12:                   # first is day -> EU order
            month = bi
        elif bi > 12 and ai <= 12:                 # second is day -> US order
            month = ai
        elif ai <= 12 and bi <= 12:                # genuinely ambiguous
            if locale_hint == "US":
                month = ai
            elif locale_hint in ("EU", "UK", "IN"):
                month = bi
            else:
                meta["precision"] = "year"
                return f"{year:04d}", 0.6, "ambiguous_date_down_to_year", meta
        else:
            meta["precision"] = "year"
            return f"{year:04d}", 0.5, "implausible_components_down_to_year", meta
        if not (1 <= month <= 12):
            meta["precision"] = "year"
            return f"{year:04d}", 0.6, "bad_month_down_to_year", meta
        val, prec = _fmt(year, month)
        meta["precision"] = prec
        return val, 1.0, "", meta

    # Bare year, possibly 2-digit.
    m = re.match(r"^(\d{2,4})$", s)
    if m:
        year = _expand_year(int(m.group(1)))
        meta["precision"] = "year"
        return f"{year:04d}", 0.9, "year_only", meta

    return None, 0.0, f"unparseable_date:{s!r}", meta
