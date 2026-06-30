"""Link canonicalisation (design doc section 4).

Force https, strip tracking params, classify into linkedin/github/portfolio/
other, and extract a canonical handle where one exists. Total & deterministic.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from .text import clean_text

_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|mc_|ref$|ref_src$|trk$|original_referer$)", re.I)


def _strip_tracking(query: str) -> str:
    if not query:
        return ""
    kept = []
    for part in query.split("&"):
        if not part:
            continue
        key = part.split("=", 1)[0]
        if _TRACKING.match(key):
            continue
        kept.append(part)
    return "&".join(kept)


def _ensure_scheme(url: str) -> str:
    if "://" not in url:
        return "https://" + url
    return url


def classify_link(raw) -> Tuple[Optional[str], str, Optional[str], float, str]:
    """Return ``(canonical_url, kind, handle, quality, reason)``.

    ``kind`` in {linkedin, github, portfolio, other}. ``handle`` is the profile
    handle for linkedin/github when present.
    """
    s = clean_text(raw)
    if not s:
        return None, "other", None, 0.0, "empty"
    s = _ensure_scheme(s.strip())
    try:
        parts = urlsplit(s)
    except Exception:
        return None, "other", None, 0.0, "unparseable_url"
    host = (parts.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None, "other", None, 0.0, "no_host"
    path = parts.path.rstrip("/")
    query = _strip_tracking(parts.query)
    scheme = "https"

    if "linkedin.com" in host:
        m = re.search(r"/in/([^/]+)", path)
        handle = m.group(1) if m else None
        canon = urlunsplit((scheme, "linkedin.com", f"/in/{handle}" if handle else path, "", ""))
        return canon, "linkedin", handle, 1.0 if handle else 0.7, ""
    if "github.com" in host:
        m = re.search(r"^/([^/]+)", path)
        handle = m.group(1) if m else None
        canon = urlunsplit((scheme, "github.com", f"/{handle}" if handle else path, "", ""))
        return canon, "github", handle, 1.0 if handle else 0.7, ""

    canon = urlunsplit((scheme, host, path, query, ""))
    # A bare domain / personal site is treated as a portfolio; anything else "other".
    kind = "portfolio" if (path in ("", "/") and not query) else "other"
    return canon, kind, None, 0.8, ""
