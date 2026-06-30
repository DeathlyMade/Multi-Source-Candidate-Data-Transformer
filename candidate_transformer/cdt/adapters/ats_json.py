"""ATS JSON adapter -- STRUCTURED (semi-structured) source (design doc section 3).

The ATS uses its OWN field names that do NOT match ours, so this adapter owns a
foreign-key mapping. It is defensive: unknown shapes, missing keys, list-or-
scalar contacts, and nested objects are all tolerated (best-effort, never throws
-- isolation is still provided by ``safe_ingest``).
"""
from __future__ import annotations

import json
from typing import Iterable

from ..model import Claim, Source
from ..normalize.text import decode_bytes
from .base import Adapter, make_claim


def _first(d: dict, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, "", []):
            return d.get(k)
    return default


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


class AtsJsonAdapter(Adapter):
    source_type = "ats_json"

    def parse(self, data: bytes, source: Source) -> Iterable[Claim]:
        text = decode_bytes(data)
        obj = json.loads(text)  # JSONDecodeError -> isolated by safe_ingest
        if isinstance(obj, dict):
            records = _first(obj, "applicants", "candidates", "records", "people", "data")
            records = records if isinstance(records, list) else [obj]
        elif isinstance(obj, list):
            records = obj
        else:
            return []

        claims: list[Claim] = []
        for i, rec in enumerate(records):
            if not isinstance(rec, dict):
                continue
            rid = f"{source.source_id}#rec={i}"
            claims.extend(self._record(rec, source, rid, i))
        return claims

    def _record(self, rec: dict, source: Source, rid: str, i: int) -> list[Claim]:
        out: list[Claim] = []

        def add(path, raw, sub):
            out.append(make_claim(source, rid, path, raw, f"ats_json.{sub}", f"rec={i};{sub}"))

        # name (single field or given/family split)
        name = _first(rec, "name", "full_name", "candidate_name", "display_name")
        if not name:
            gn = _first(rec, "given_name", "first_name", "firstName")
            fn = _first(rec, "family_name", "last_name", "lastName", "surname")
            name = " ".join(x for x in (gn, fn) if x) or None
        if name:
            add("full_name", name, "name")

        contact = _first(rec, "contact", "contact_info", "contacts", default={}) or {}
        if not isinstance(contact, dict):
            contact = {}

        emails = (_as_list(_first(rec, "email", "emails", "email_address"))
                  + _as_list(_first(contact, "email", "emails", "email_addresses", "email_address")))
        for e in emails:
            add("emails", e, "email")

        phones = (_as_list(_first(rec, "phone", "phones", "telephone", "mobile"))
                  + _as_list(_first(contact, "phone", "phones", "telephone", "mobile")))
        for p in phones:
            add("phones", p, "phone")

        org = _first(rec, "org", "organization", "company", "current_company", "employer")
        position = _first(rec, "position", "title", "role", "job_title")
        if org or position:
            add("experience", {"company": org, "title": position, "start": None,
                               "end": "present", "summary": None}, "current_role")
        headline = _first(rec, "headline", "summary", "about", "tagline")
        if headline:
            add("headline", headline, "headline")

        geo = _first(rec, "geo", "location", "address", default={}) or {}
        if isinstance(geo, str):
            add("location.city", geo, "location")
        elif isinstance(geo, dict):
            city = _first(geo, "city", "town", "locality")
            region = _first(geo, "region", "state", "province")
            country = _first(geo, "country", "country_name", "country_code", "nation")
            if city:
                add("location.city", city, "geo.city")
            if region:
                add("location.region", region, "geo.region")
            if country:
                add("location.country", country, "geo.country")

        for s in _as_list(_first(rec, "skills", "competencies", "tags", "skill_set")):
            if isinstance(s, dict):
                s = _first(s, "name", "skill", "label")
            if s:
                add("skills", s, "skill")

        for w in _as_list(_first(rec, "work_history", "experience", "employment", "jobs")):
            if not isinstance(w, dict):
                continue
            add("experience", {
                "company": _first(w, "organization", "company", "employer", "org"),
                "title": _first(w, "role", "title", "position"),
                "start": _first(w, "from", "start", "start_date", "began"),
                "end": _first(w, "to", "end", "end_date", "until"),
                "summary": _first(w, "summary", "description", "notes"),
            }, "work_history")

        for s in _as_list(_first(rec, "schools", "education", "qualifications")):
            if not isinstance(s, dict):
                continue
            add("education", {
                "institution": _first(s, "school", "institution", "university", "college"),
                "degree": _first(s, "qualification", "degree", "level"),
                "field": _first(s, "major", "field", "field_of_study", "subject"),
                "end_year": _first(s, "graduation_year", "end_year", "year", "completed"),
            }, "school")

        social = _first(rec, "social", "links", "profiles", default={}) or {}
        if isinstance(social, dict):
            for raw in (_first(social, "linkedin_url", "linkedin"),
                        _first(social, "git", "github", "github_url"),
                        _first(social, "website", "portfolio", "site")):
                if raw:
                    add("links", raw, "social")
        yoe = _first(rec, "years_experience", "yoe", "total_experience")
        if yoe is not None:
            add("years_experience", yoe, "yoe")
        return out
