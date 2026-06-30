"""Normalize + Canonicalize phase (design doc section 4).

Two distinct ops behind one dispatcher:
  * Normalize   -- format (dates, phones, emails, links, text).
  * Canonicalize-- semantics (skills, country).

``normalize_claim`` is TOTAL: every branch returns a ``NormalizedClaim`` whose
``value`` may be ``None`` with a ``reason``. It never raises. ``meta`` may carry
``resolved_path`` (e.g. a generic "links" claim resolved to "links.github").
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..model import Claim, NormalizedClaim
from . import country as _country
from . import dates as _dates
from . import emails as _emails
from . import links as _links
from . import names as _names
from . import phones as _phones
from . import skills as _skills
from .text import clean_text


@dataclass(frozen=True)
class NormalizeContext:
    locale_hint: Optional[str] = None       # "US" | "EU" | "UK" | "IN" | None
    default_country: Optional[str] = None   # ISO alpha-2 fallback for phones
    candidate_country: Optional[str] = None  # known only after resolution


def _num(raw):
    try:
        if isinstance(raw, bool):
            return None
        if isinstance(raw, (int, float)):
            return float(raw)
        s = clean_text(raw)
        return float(s) if s else None
    except (TypeError, ValueError):
        return None


def normalize_claim(claim: Claim, ctx: NormalizeContext) -> NormalizedClaim:
    path = claim.path

    if path == "full_name":
        val, key, q, why = _names.normalize_name(claim.raw)
        return NormalizedClaim(claim, val, key, q, "normalize_name", why)

    if path == "emails":
        val, key, q, why = _emails.normalize_email(claim.raw)
        return NormalizedClaim(claim, val, key, q, "normalize_email", why)

    if path == "phones":
        val, key, q, why, meta = _phones.normalize_phone(
            claim.raw,
            candidate_country=ctx.candidate_country,
            default_country=ctx.default_country,
        )
        return NormalizedClaim(claim, val, key, q, "normalize_phone_e164", why, meta)

    if path == "location.country":
        val, canonical, q, why = _country.normalize_country(claim.raw)
        return NormalizedClaim(
            claim, val, (val.casefold() if isinstance(val, str) else val),
            q, "canonicalize_country", why, {"canonical": canonical},
        )

    if path in ("location.city", "location.region", "headline"):
        val = clean_text(claim.raw) or None
        key = val.casefold() if isinstance(val, str) else val
        q = 1.0 if val else 0.0
        return NormalizedClaim(claim, val, key, q, "normalize_text", "" if val else "empty")

    if path == "years_experience":
        n = _num(claim.raw)
        if n is None or n < 0 or n > 80:
            return NormalizedClaim(claim, None, None, 0.0, "parse_number", f"bad_years:{claim.raw!r}")
        return NormalizedClaim(claim, n, n, 1.0, "parse_number", "")

    if path == "skills":
        name, canonical, q, why = _skills.normalize_skill(claim.raw)
        return NormalizedClaim(
            claim, name, (name.casefold() if isinstance(name, str) else name),
            q, "canonicalize_skill", why, {"canonical": canonical},
        )

    if path == "links" or path.startswith("links."):
        canon, kind, handle, q, why = _links.classify_link(claim.raw)
        resolved = f"links.{kind}"
        meta = {"resolved_path": resolved, "kind": kind, "handle": handle}
        return NormalizedClaim(claim, canon, canon, q, "canonicalize_link", why, meta)

    if path == "experience":
        return _normalize_experience(claim, ctx)

    if path == "education":
        return _normalize_education(claim, ctx)

    # Identity-only claims (e.g. github/linkedin handles used purely for matching)
    if path.startswith("_identity."):
        val = clean_text(claim.raw) or None
        return NormalizedClaim(claim, val, val.casefold() if val else None,
                               1.0 if val else 0.0, "identity", "")

    # Unknown path -> keep raw text, low quality (never crash).
    val = clean_text(claim.raw) or None
    return NormalizedClaim(claim, val, val, 0.3 if val else 0.0, "passthrough", "unknown_path")


def _normalize_experience(claim: Claim, ctx: NormalizeContext) -> NormalizedClaim:
    raw = claim.raw if isinstance(claim.raw, dict) else {}
    company = clean_text(raw.get("company")) or None
    title = clean_text(raw.get("title")) or None
    summary = clean_text(raw.get("summary")) or None
    if summary and len(summary) > 400:
        summary = summary[:400].rstrip() + "..."
    start, sq, _, smeta = _dates.normalize_date(raw.get("start"), ctx.locale_hint)
    end, eq, _, emeta = _dates.normalize_date(raw.get("end"), ctx.locale_hint)
    is_current = bool(emeta.get("is_current"))
    if company is None and title is None:
        return NormalizedClaim(claim, None, None, 0.0, "normalize_experience", "empty_experience")
    value = {
        "company": company, "title": title,
        "start": start, "end": end,
        "summary": summary, "is_current": is_current,
    }
    company_key = (company or "").casefold()
    title_key = (title or "").casefold()
    match_key = (company_key, start or "", end or "", title_key)
    quals = [q for q in (sq, eq) if q] or [0.0]
    quality = round(min(1.0, 0.6 + 0.4 * (sum(quals) / len(quals))), 3)
    return NormalizedClaim(claim, value, match_key, quality, "normalize_experience", "",
                           {"company_key": company_key, "title_key": title_key,
                            "start": start, "end": end, "is_current": is_current})


def _normalize_education(claim: Claim, ctx: NormalizeContext) -> NormalizedClaim:
    raw = claim.raw if isinstance(claim.raw, dict) else {}
    institution = clean_text(raw.get("institution")) or None
    degree = clean_text(raw.get("degree")) or None
    field_ = clean_text(raw.get("field")) or None
    end_year_val, eyq, _, _ = _dates.normalize_date(raw.get("end_year"), ctx.locale_hint)
    end_year = None
    if end_year_val:
        end_year = int(end_year_val[:4])  # schema wants end_year as a number
    if institution is None and degree is None:
        return NormalizedClaim(claim, None, None, 0.0, "normalize_education", "empty_education")
    value = {"institution": institution, "degree": degree, "field": field_, "end_year": end_year}
    inst_key = (institution or "").casefold()
    match_key = (inst_key, degree or "", end_year or "")
    quality = 0.9 if institution else 0.5
    return NormalizedClaim(claim, value, match_key, quality, "normalize_education", "",
                           {"institution_key": inst_key})
