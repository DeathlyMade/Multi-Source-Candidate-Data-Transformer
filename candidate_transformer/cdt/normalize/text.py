"""Text decoding and Unicode hygiene (design doc sections 4 & 9).

Edge case "Encoding / mojibake / RTL": decode bytes defensively with chardet,
normalise to NFC, and never crash. All helpers are total.
"""
from __future__ import annotations

import unicodedata

try:
    import chardet  # available in the sandbox
except Exception:  # pragma: no cover - defensive
    chardet = None


def decode_bytes(data: bytes) -> str:
    """Best-effort decode of arbitrary bytes to ``str`` -- never raises.

    Tries UTF-8 (with BOM), then chardet's guess, then latin-1 as a last resort
    (latin-1 maps every byte, so it cannot fail).
    """
    if data is None:
        return ""
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            pass
    if chardet is not None:
        try:
            guess = chardet.detect(data) or {}
            enc = guess.get("encoding")
            if enc:
                return data.decode(enc, errors="replace")
        except Exception:
            pass
    return data.decode("latin-1", errors="replace")


def clean_text(s) -> str:
    """NFC-normalise, strip control/zero-width/RTL marks, collapse whitespace."""
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    s = unicodedata.normalize("NFC", s)
    out = []
    for ch in s:
        cat = unicodedata.category(ch)
        # Drop control chars and bidirectional/zero-width formatting marks
        # (RTL/LTR overrides, ZWSP, BOM) -- they are invisible and break matching.
        if cat in ("Cc", "Cf") and ch not in ("\n", "\t"):
            continue
        out.append(ch)
    s = "".join(out)
    # collapse internal whitespace runs to single spaces, trim ends
    s = " ".join(s.split())
    return s


def squish(s) -> str:
    """clean_text + casefold, for building case-insensitive match keys."""
    return clean_text(s).casefold()
