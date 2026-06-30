"""Email normalisation (design doc section 4).

Policy: lower + NFC, validate. The *canonical form* (strip +tag and, for dot-
insensitive providers, dots) is the match key used for identity & dedupe; the
*original* (lightly cleaned) is stored as the truth in the profile.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

from .text import clean_text

# Bounded, deterministic email regex. Precision over recall (design doc 3).
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)

# Providers where dots in the local part are not significant.
_DOT_INSENSITIVE = {"gmail.com", "googlemail.com"}


def normalize_email(raw) -> Tuple[Optional[str], Optional[str], float, str]:
    """Return ``(stored_value, match_key, quality, reason)``.

    ``stored_value`` is the truth we keep; ``match_key`` is the canonicalised
    key for dedupe/identity. On invalid input returns ``(None, None, 0.0, why)``
    -- never raises, never fabricates.
    """
    s = clean_text(raw)
    if not s:
        return None, None, 0.0, "empty"
    s = s.strip().strip("<>").strip()
    s = s.replace(" ", "")
    s = s.lower()
    if not _EMAIL_RE.match(s):
        return None, None, 0.0, f"invalid_email_format:{s!r}"
    local, _, domain = s.partition("@")
    # Canonical key: drop +tag; drop dots for dot-insensitive providers.
    key_local = local.split("+", 1)[0]
    if domain in _DOT_INSENSITIVE:
        key_local = key_local.replace(".", "")
    match_key = f"{key_local}@{domain}"
    return s, match_key, 1.0, ""


def extract_emails(text: str) -> list[str]:
    """Find candidate emails in free text (bounded regex scan)."""
    found = re.findall(
        r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+",
        text or "",
    )
    # de-dup while preserving order
    seen, out = set(), []
    for f in found:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out
