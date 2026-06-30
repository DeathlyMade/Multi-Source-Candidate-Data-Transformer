"""Person-name normalisation (format only -- never semantics).

We keep the cleaned display form as the value and a casefolded, punctuation-light
key for equivalence-collapse so "Jane Doe", "jane  doe" and "Jane Doe" all share
one match key and corroborate instead of conflicting (design doc section 6).
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

from .text import clean_text


def normalize_name(raw) -> Tuple[Optional[str], Optional[str], float, str]:
    """Return ``(display, match_key, quality, reason)``."""
    s = clean_text(raw)
    if not s:
        return None, None, 0.0, "empty"
    # strip surrounding quotes and trailing role noise like "(she/her)"
    s = re.sub(r"\((?:[^)]*)\)", "", s).strip()
    s = s.strip(" .,-")
    if not s:
        return None, None, 0.0, "empty_after_clean"
    # key: casefold, drop punctuation, collapse spaces
    key = re.sub(r"[^\w\s]", "", s.casefold())
    key = " ".join(key.split())
    if not key:
        return None, None, 0.0, "no_alpha"
    return s, key, 1.0, ""
