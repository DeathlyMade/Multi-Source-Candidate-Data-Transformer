"""Country canonicalisation to ISO-3166-1 alpha-2 (design doc section 4).

Alias map (USA->US, UK->GB, ...). If we cannot confidently map a string we KEEP
it as free text with canonical=False rather than guess. Also provides a small,
precision-first location splitter ("City, Region, Country").
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

from ..vocab import country_alias
from .text import clean_text


def _key(s: str) -> str:
    s = clean_text(s).casefold()
    s = re.sub(r"[\.]", ".", s)
    return s.strip().strip(".").strip()


def normalize_country(raw) -> Tuple[Optional[str], bool, float, str]:
    """Return ``(value, canonical, quality, reason)``.

    ``value`` is an alpha-2 code when recognised (canonical=True); otherwise the
    cleaned free-text country (canonical=False, lower quality). Empty -> None.
    """
    s = clean_text(raw)
    if not s:
        return None, False, 0.0, "empty"
    aliases = country_alias().get("aliases", {})
    iso = set(country_alias().get("iso_alpha2", []))
    k = _key(s)
    # already an alpha-2 code?
    if len(s.strip()) == 2 and s.strip().upper() in iso:
        return s.strip().upper(), True, 1.0, ""
    if k in aliases:
        return aliases[k], True, 1.0, ""
    # last token (e.g. "Berlin, Germany" -> "germany")
    last = k.split(",")[-1].strip()
    if last in aliases:
        return aliases[last], True, 0.95, "matched_last_token"
    return s, False, 0.4, "unknown_country_kept_as_text"


def parse_location(raw) -> dict:
    """Split a location string into raw ``{city, region, country}`` parts.

    Precision-first: only fields we can plausibly identify are filled. The
    country part is left as the raw token here; canonicalisation happens via
    ``normalize_country`` in the adapter/normaliser so provenance stays clean.
    """
    s = clean_text(raw)
    out = {"city": None, "region": None, "country": None}
    if not s:
        return out
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if len(parts) == 1:
        # Could be a country on its own, else treat as city.
        val, canonical, _, _ = normalize_country(parts[0])
        if canonical:
            out["country"] = parts[0]
        else:
            out["city"] = parts[0]
    elif len(parts) == 2:
        out["city"], out["country"] = parts[0], parts[1]
    else:
        out["city"], out["region"], out["country"] = parts[0], parts[1], parts[-1]
    return out
