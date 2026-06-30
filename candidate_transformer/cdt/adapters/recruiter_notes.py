"""Recruiter notes adapter -- UNSTRUCTURED free text (design doc section 3 & 9).

Closed-vocabulary gazetteer for skills + bounded regex for emails/phones/links/
dates, plus a few explicit labels ("Phone:", "Company:"). Precision over recall:
free text is low-trust corroboration (the reliability matrix encodes that), and a
genuinely garbage note recognises NOTHING and yields 0 claims (no guessing).
"""
from __future__ import annotations

import re
from typing import Iterable

from ..model import Claim, Source
from ..normalize.emails import extract_emails
from ..normalize.phones import extract_phones
from ..normalize.skills import _alias_index
from ..normalize.text import clean_text, decode_bytes
from .base import Adapter, make_claim

# Minimum alias length scanned in free text: avoids matching ambiguous short
# forms ("c", "r", "go", "js") inside ordinary prose -> precision-first.
_MIN_GAZETTEER_LEN = 3

_LABELS = {
    "name": "full_name", "candidate": "full_name",
    "email": "emails", "e-mail": "emails",
    "phone": "phones", "mobile": "phones", "cell": "phones", "tel": "phones",
    "company": "_company", "employer": "_company", "current company": "_company",
    "title": "_title", "role": "_title",
    "location": "location.city", "based in": "location.city",
    "linkedin": "links", "github": "links",
}
_LINK_RE = re.compile(r"((?:https?://)?(?:www\.)?(?:linkedin\.com/\S+|github\.com/\S+))", re.I)


class RecruiterNotesAdapter(Adapter):
    source_type = "recruiter_notes"

    def parse(self, data: bytes, source: Source) -> Iterable[Claim]:
        text = decode_bytes(data)
        rid = f"{source.source_id}#notes=0"
        out: list[Claim] = []

        def add(path, raw, sub, loc):
            out.append(make_claim(source, rid, path, raw, f"recruiter_notes.{sub}", loc))

        # --- explicit "Label: value" lines --------------------------------
        company = title = None
        for i, ln in enumerate(text.splitlines()):
            s = clean_text(ln)
            if ":" not in s:
                continue
            label, _, value = s.partition(":")
            key = label.strip().lower()
            value = value.strip()
            if key in _LABELS and value:
                path = _LABELS[key]
                if path == "_company":
                    company = value
                elif path == "_title":
                    title = value
                else:
                    add(path, value, "label", f"line={i};label={key}")
        if company or title:
            add("experience", {"company": company, "title": title, "start": None,
                               "end": "present", "summary": None}, "labeled_role", "labels")

        # --- contact details (bounded regex, anywhere) --------------------
        for e in extract_emails(text):
            add("emails", e, "email", "regex.email")
        for p in extract_phones(text):
            add("phones", p, "phone", "regex.phone")
        for m in _LINK_RE.finditer(text):
            add("links", m.group(1), "link", "regex.link")

        # --- skills via closed-vocab gazetteer ----------------------------
        for skill, loc in self._gazetteer_skills(text):
            add("skills", skill, "gazetteer", loc)
        return out

    @staticmethod
    def _gazetteer_skills(text: str):
        idx = _alias_index()
        cleaned = clean_text(text).casefold()
        # word tokens with positions; build 1..3-grams and match alias keys.
        tokens = re.findall(r"[a-z0-9+#.]+", cleaned)
        seen_canonical = set()
        out = []
        n = len(tokens)
        for i in range(n):
            for span in (3, 2, 1):
                if i + span > n:
                    continue
                gram = " ".join(tokens[i:i + span])
                if len(gram) < _MIN_GAZETTEER_LEN:
                    continue
                canonical = idx.get(gram)
                if canonical and canonical not in seen_canonical:
                    seen_canonical.add(canonical)
                    out.append((canonical, f"gazetteer:{gram}"))
        return out
