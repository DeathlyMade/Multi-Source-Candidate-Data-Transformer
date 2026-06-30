"""Adapter registry + source-type detection.

The CLI accepts files and optionally an explicit ``type=path`` prefix; otherwise
the type is auto-detected from extension and a light content peek.
"""
from __future__ import annotations

import json

from ..normalize.text import decode_bytes
from .ats_json import AtsJsonAdapter
from .base import Adapter, safe_ingest
from .github_api import GithubApiAdapter
from .recruiter_csv import RecruiterCsvAdapter
from .recruiter_notes import RecruiterNotesAdapter
from .resume_file import ResumeAdapter

REGISTRY: dict[str, Adapter] = {
    "recruiter_csv": RecruiterCsvAdapter(),
    "ats_json": AtsJsonAdapter(),
    "github": GithubApiAdapter(),
    "resume": ResumeAdapter(),
    "recruiter_notes": RecruiterNotesAdapter(),
}

STRUCTURED = {"recruiter_csv", "ats_json"}
UNSTRUCTURED = {"github", "resume", "recruiter_notes"}


def get_adapter(source_type: str) -> Adapter:
    if source_type not in REGISTRY:
        raise KeyError(f"unknown source type: {source_type!r} (have {sorted(REGISTRY)})")
    return REGISTRY[source_type]


def _looks_like_github(obj) -> bool:
    if isinstance(obj, dict):
        if "user" in obj or "repos" in obj:
            return True
        if obj.get("login") and ("public_repos" in obj or "html_url" in obj):
            return True
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        if "language" in obj[0] or "languages" in obj[0]:
            return True
    return False


def detect_source_type(origin: str, data: bytes) -> str:
    """Best-effort source-type detection from filename + content peek."""
    low = (origin or "").lower()
    if low.endswith(".csv"):
        return "recruiter_csv"
    if low.endswith((".pdf", ".docx", ".doc")):
        return "resume"
    if low.endswith(".json"):
        try:
            obj = json.loads(decode_bytes(data))
            return "github" if _looks_like_github(obj) else "ats_json"
        except Exception:
            return "ats_json"
    if low.endswith(".txt"):
        text = decode_bytes(data).lower()
        if any(h in text for h in ("\nexperience", "\neducation", "summary\n", "skills\n", "work experience")):
            return "resume"
        return "recruiter_notes"
    # filename keyword fallbacks
    for kw, t in (("resume", "resume"), ("cv", "resume"), ("notes", "recruiter_notes"),
                  ("github", "github"), ("ats", "ats_json"), ("csv", "recruiter_csv")):
        if kw in low:
            return t
    return "recruiter_notes"


__all__ = ["REGISTRY", "STRUCTURED", "UNSTRUCTURED", "get_adapter",
           "detect_source_type", "safe_ingest"]
