"""Phone normalisation to E.164 (design doc section 4).

Region resolution order: in-number (leading +CC) > candidate country >
source/config default. Invalid numbers become null -- we NEVER fabricate a
country code. Extensions are kept aside. Deterministic, dependency-free, total.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

from ..vocab import country_alias
from .text import clean_text

# E.164 allows up to 15 digits, country code 1-3 digits, national number >= 1.
_MIN_NSN = 7   # minimum plausible national-number length we will accept
_MAX_E164 = 15

# National trunk-prefix length per calling code, used to strip a leading "0"
# when we already know the country (e.g. UK/IN national format).
_TRUNK_PREFIX = {"44": "0", "91": "0", "33": "0", "49": "0", "61": "0"}


def _calling_code_for(country: Optional[str]) -> Optional[str]:
    if not country:
        return None
    return country_alias().get("calling_codes", {}).get(country.upper())


def _split_extension(s: str) -> Tuple[str, Optional[str]]:
    m = re.search(r"(?:\b(?:ext|x|extension)\.?\s*)(\d{1,6})\s*$", s, re.IGNORECASE)
    if m:
        return s[: m.start()].strip(), m.group(1)
    return s, None


def _all_codes() -> set[str]:
    return set(country_alias().get("calling_codes", {}).values())


def normalize_phone(
    raw,
    candidate_country: Optional[str] = None,
    default_country: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], float, str, dict]:
    """Return ``(e164, match_key, quality, reason, meta)``.

    ``meta`` may carry ``{"ext": "123"}``. ``match_key`` == ``e164`` (used for
    dedupe). On anything invalid returns ``(None, None, 0.0, reason, meta)``.
    """
    meta: dict = {}
    s = clean_text(raw)
    if not s:
        return None, None, 0.0, "empty", meta
    s, ext = _split_extension(s)
    if ext:
        meta["ext"] = ext

    has_plus = "+" in s
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None, None, 0.0, "no_digits", meta

    codes = _all_codes()

    # 1) In-number country code wins.
    if has_plus:
        if len(digits) < _MIN_NSN or len(digits) > _MAX_E164:
            return None, None, 0.0, f"e164_length_out_of_range:{len(digits)}", meta
        e164 = "+" + digits
        return e164, e164, 1.0, "", meta

    # 2) US/Canada style 11 digits starting with 1, or NANP-knowable.
    if len(digits) == 11 and digits.startswith("1"):
        e164 = "+" + digits
        return e164, e164, 1.0, "nanp_leading_1", meta

    # 3) Use candidate country, then config default, to supply the code.
    for country in (candidate_country, default_country):
        cc = _calling_code_for(country)
        if not cc:
            continue
        nsn = digits
        trunk = _TRUNK_PREFIX.get(cc)
        if trunk and nsn.startswith(trunk):
            nsn = nsn[len(trunk):]
        if len(nsn) < _MIN_NSN:
            continue
        e164 = "+" + cc + nsn
        if len(re.sub(r"\D", "", e164)) > _MAX_E164:
            continue
        return e164, e164, 0.9, f"region_from:{country}", meta

    # 4) No way to know the country code without guessing -> refuse.
    return (
        None,
        None,
        0.0,
        "no_country_code_and_no_region_hint",
        meta,
    )


def extract_phones(text: str) -> list[str]:
    """Find phone-like substrings in free text (bounded scan)."""
    out = []
    for m in re.findall(r"(?:\+?\d[\d\s().-]{6,}\d)(?:\s*(?:ext|x)\.?\s*\d{1,6})?", text or ""):
        m = m.strip()
        if sum(c.isdigit() for c in m) >= _MIN_NSN:
            out.append(m)
    return out
