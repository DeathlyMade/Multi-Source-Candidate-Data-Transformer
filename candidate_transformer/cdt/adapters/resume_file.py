"""Resume adapter -- UNSTRUCTURED source: PDF / DOCX / TXT prose (design doc 3).

Section-aware parse: detect headers (SUMMARY/EXPERIENCE/EDUCATION/SKILLS),
then extract within each section. Contact details (email/phone/links) are found
with bounded regex anywhere. Precision over recall: anything we cannot parse
confidently is dropped, never guessed.
"""
from __future__ import annotations

import re
from typing import Iterable

from ..model import Claim, Source
from ..normalize.country import normalize_country, parse_location
from ..normalize.emails import extract_emails
from ..normalize.phones import extract_phones
from ..normalize.text import clean_text, decode_bytes
from .base import Adapter, make_claim

_SECTIONS = {
    "summary": "summary", "objective": "summary", "profile": "summary", "about": "summary",
    "experience": "experience", "work experience": "experience", "employment": "experience",
    "professional experience": "experience", "work history": "experience",
    "education": "education", "academics": "education",
    "skills": "skills", "technical skills": "skills", "core skills": "skills",
    "projects": "projects", "certifications": "certifications",
}
_DATE_SPLIT = re.compile(r"\s*(?:-|\u2013|\u2014|\bto\b)\s*", re.IGNORECASE)
_LINK_RE = re.compile(r"((?:https?://)?(?:www\.)?(?:linkedin\.com/\S+|github\.com/\S+|[\w.-]+\.[a-z]{2,}/?\S*))", re.I)


def _looks_binary(data: bytes) -> bool:
    head = data[:8]
    return head.startswith(b"%PDF") or head.startswith(b"PK\x03\x04")


def _extract_text(data: bytes, origin: str) -> str:
    """Extract prose from PDF/DOCX/TXT.

    Precision-first & graceful: if a binary document cannot be parsed (e.g. the
    parser library is unavailable) we return "" -> 0 claims, rather than decoding
    raw bytes into garbage that would pollute the profile.
    """
    low = (origin or "").lower()
    if low.endswith(".pdf") or data[:4] == b"%PDF":
        try:
            import io
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception:
            return ""
    if low.endswith(".docx") or data[:4] == b"PK\x03\x04":
        try:
            import io
            from docx import Document
            doc = Document(io.BytesIO(data))
            return "\n".join(p.text for p in doc.paragraphs)
        except Exception:
            return ""
    text = decode_bytes(data)
    if _looks_binary(data):   # mis-detected binary as text -> refuse to guess
        return ""
    return text


def _looks_like_name(line: str) -> bool:
    s = clean_text(line)
    if not s or "@" in s or any(ch.isdigit() for ch in s):
        return False
    words = s.split()
    if not (1 < len(words) <= 4):
        return False
    return all(w[:1].isupper() for w in words if w[:1].isalpha())


class ResumeAdapter(Adapter):
    source_type = "resume"

    def parse(self, data: bytes, source: Source) -> Iterable[Claim]:
        text = _extract_text(data, source.origin)
        lines = [ln.rstrip() for ln in text.splitlines()]
        rid = f"{source.source_id}#resume=0"
        out: list[Claim] = []

        def add(path, raw, sub, loc):
            out.append(make_claim(source, rid, path, raw, f"resume.{sub}", loc))

        # --- header block: name + headline ---------------------------------
        non_empty = [(i, clean_text(ln)) for i, ln in enumerate(lines) if clean_text(ln)]
        if non_empty:
            first_i, first = non_empty[0]
            if _looks_like_name(first):
                add("full_name", first, "name", f"line={first_i}")
                # the line right after the name is often a headline/title
                if len(non_empty) > 1:
                    j, second = non_empty[1]
                    low = second.lower().split(":")[0].strip()
                    if low not in _SECTIONS and "@" not in second and not _LINK_RE.search(second):
                        add("headline", second, "subtitle", f"line={j}")

        # --- location in the header block (e.g. "Berlin, Germany") --------
        header_lines = self._header_lines(lines)
        for li, hl in header_lines:
            if "@" in hl or "|" in hl or "http" in hl.lower() or "," not in hl:
                continue
            parts = parse_location(hl)
            country_tok = parts.get("country")
            if country_tok and normalize_country(country_tok)[1]:  # canonical country
                if parts.get("city"):
                    add("location.city", parts["city"], "location", f"line={li}")
                if parts.get("region"):
                    add("location.region", parts["region"], "location", f"line={li}")
                add("location.country", country_tok, "location", f"line={li}")
                break

        # --- contact details anywhere -------------------------------------
        for e in extract_emails(text):
            add("emails", e, "email", "regex.email")
        for p in extract_phones(text):
            add("phones", p, "phone", "regex.phone")
        for m in _LINK_RE.finditer(text):
            url = m.group(1)
            if "linkedin.com" in url.lower() or "github.com" in url.lower():
                add("links", url, "link", "regex.link")

        # --- sections ------------------------------------------------------
        sections = self._split_sections(lines)
        if sections.get("summary"):
            body = clean_text(" ".join(sections["summary"]))
            if body:
                add("headline", body, "summary", "section=summary")
        for entry, loc in self._experience_entries(sections.get("experience", [])):
            add("experience", entry, "experience", loc)
        for entry, loc in self._education_entries(sections.get("education", [])):
            add("education", entry, "education", loc)
        for skill, loc in self._skill_tokens(sections.get("skills", [])):
            add("skills", skill, "skill", loc)
        return out

    @staticmethod
    def _header_lines(lines: list[str]):
        """Non-empty (index, text) lines before the first recognised section."""
        out = []
        for i, ln in enumerate(lines):
            s = clean_text(ln)
            if not s:
                continue
            if s.lower().rstrip(":") in _SECTIONS:
                break
            out.append((i, s))
        return out

    @staticmethod
    def _split_sections(lines: list[str]) -> dict:
        sections: dict[str, list[str]] = {}
        current = None
        for ln in lines:
            head = clean_text(ln).lower().rstrip(":")
            if head in _SECTIONS and len(head) <= 30:
                current = _SECTIONS[head]
                sections.setdefault(current, [])
                continue
            if current:
                sections[current].append(ln)
        return sections

    @staticmethod
    def _experience_entries(block: list[str]):
        entries = []
        cur, idx = None, 0
        for ln in block:
            s = clean_text(ln)
            if not s:
                if cur:
                    entries.append(cur)
                    cur = None
                continue
            # header line: "Title, Company <dash> start - end"
            m = re.match(r"^(.*?)\s[\u2013\u2014-]\s(.*)$", s)
            if m and ("," in m.group(1)) and re.search(r"\d{4}|present|current", m.group(2), re.I):
                if cur:
                    entries.append(cur)
                left, right = m.group(1), m.group(2)
                title, _, company = left.rpartition(",")
                parts = _DATE_SPLIT.split(right, maxsplit=1)
                start = parts[0].strip() if parts else None
                end = parts[1].strip() if len(parts) > 1 else None
                cur = ({"company": clean_text(company), "title": clean_text(title),
                        "start": start, "end": end, "summary": None},
                       f"section=experience;entry={idx}")
                idx += 1
            elif cur:
                d = cur[0]
                d["summary"] = clean_text((d.get("summary") or "") + " " + s).strip()
        if cur:
            entries.append(cur)
        return entries

    @staticmethod
    def _education_entries(block: list[str]):
        entries = []
        idx = 0
        for ln in block:
            s = clean_text(ln)
            if not s:
                continue
            m = re.match(r"^(.*?)\s[\u2013\u2014-]\s(.*)$", s)
            year = None
            if m:
                left = m.group(1)
                ym = re.search(r"(19|20)\d{2}", m.group(2))
                year = ym.group(0) if ym else None
            else:
                left = s
            bits = [b.strip() for b in left.split(",") if b.strip()]
            if not bits:
                continue
            degree = bits[0]
            institution = bits[-1] if len(bits) > 1 else None
            field = bits[1] if len(bits) > 2 else None
            entries.append(({"institution": institution, "degree": degree,
                             "field": field, "end_year": year},
                            f"section=education;entry={idx}"))
            idx += 1
        return entries

    @staticmethod
    def _skill_tokens(block: list[str]):
        out = []
        for ln in block:
            s = clean_text(ln)
            if not s:
                continue
            for tok in re.split(r"[,;|\u2022\u00b7]", s):
                tok = tok.strip(" .")
                if tok:
                    out.append((tok, "section=skills"))
        return out
