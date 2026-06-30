"""Skill canonicalisation against a versioned vocabulary (design doc section 4).

Order: exact alias match -> deterministic fuzzy fallback -> keep-as-unknown.
Unknown skills are NOT dropped: they are kept with ``canonical=False`` and low
confidence (prefer-empty-over-wrong means flag uncertainty, not invent or
silently discard). Fully deterministic: fuzzy ties break lexicographically.
"""
from __future__ import annotations

import difflib
from functools import lru_cache
from typing import Optional, Tuple

from ..vocab import skills_vocab
from .text import clean_text


@lru_cache(maxsize=1)
def _alias_index() -> dict:
    """alias(casefolded) -> canonical display name. Includes canonical names."""
    idx: dict[str, str] = {}
    for canonical, aliases in skills_vocab().get("skills", {}).items():
        idx[canonical.casefold()] = canonical
        for a in aliases:
            idx[clean_text(a).casefold()] = canonical
    return idx


@lru_cache(maxsize=1)
def _alias_keys() -> tuple:
    return tuple(sorted(_alias_index().keys()))


def normalize_skill(raw) -> Tuple[Optional[str], bool, float, str]:
    """Return ``(name, canonical, quality, reason)``.

    ``canonical`` True when matched to the vocabulary. Empty -> (None,...).
    """
    s = clean_text(raw)
    if not s:
        return None, False, 0.0, "empty"
    key = s.casefold()
    idx = _alias_index()
    if key in idx:
        return idx[key], True, 1.0, ""

    # Deterministic fuzzy fallback over alias keys.
    threshold = float(skills_vocab().get("fuzzy_threshold", 0.9))
    best_key, best_ratio = None, 0.0
    for cand in _alias_keys():
        r = difflib.SequenceMatcher(None, key, cand).ratio()
        # strictly-greater keeps the first (lexicographically smallest) on ties
        if r > best_ratio:
            best_ratio, best_key = r, cand
    if best_key is not None and best_ratio >= threshold:
        return idx[best_key], True, round(best_ratio, 3), f"fuzzy:{best_ratio:.3f}"

    # Unknown: keep, but flag. Title-case for a tidy display form.
    display = s if any(c.isupper() for c in s) else s.title()
    return display, False, 0.3, "unknown_skill_kept"
