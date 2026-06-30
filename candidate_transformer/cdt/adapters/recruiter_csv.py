"""Recruiter CSV adapter -- STRUCTURED source (design doc section 3).

Rows: name, email, phone, current_company, title (+ optional location/links).
Each row is its own source-record (record_id carries the row number) so identity
resolution can later merge or keep rows apart.
"""
from __future__ import annotations

import csv
import io
from typing import Iterable

from ..model import Claim, Source
from ..normalize.text import decode_bytes
from .base import Adapter, make_claim

# header alias -> canonical claim path
_HEADER_MAP = {
    "name": "full_name", "full_name": "full_name", "candidate": "full_name", "candidate_name": "full_name",
    "email": "emails", "emails": "emails", "email_address": "emails",
    "phone": "phones", "phones": "phones", "phone_number": "phones", "mobile": "phones",
    "current_company": "_company", "company": "_company", "employer": "_company",
    "title": "_title", "job_title": "_title", "role": "_title",
    "location": "_location", "city": "location.city", "country": "location.country",
    "region": "location.region", "state": "location.region",
    "linkedin": "links", "github": "links", "website": "links", "portfolio": "links",
    "years_experience": "years_experience", "yoe": "years_experience",
}


class RecruiterCsvAdapter(Adapter):
    source_type = "recruiter_csv"

    def parse(self, data: bytes, source: Source) -> Iterable[Claim]:
        text = decode_bytes(data)
        # Sniff delimiter; fall back to comma.
        sample = text[:2048]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        claims: list[Claim] = []
        for i, row in enumerate(reader):
            if row is None:
                continue
            record_id = f"{source.source_id}#row={i}"
            company = title = None
            for header, value in row.items():
                if header is None or value is None:
                    continue
                key = str(header).strip().lower().replace(" ", "_")
                path = _HEADER_MAP.get(key)
                if not path or str(value).strip() == "":
                    continue
                loc = f"row={i};col={header}"
                if path == "_company":
                    company = str(value)
                elif path == "_title":
                    title = str(value)
                elif path == "_location":
                    claims.append(make_claim(source, record_id, "location.city", value,
                                             "recruiter_csv.location", loc))
                else:
                    claims.append(make_claim(source, record_id, path, value,
                                             "recruiter_csv.field", loc))
            # current company + title -> a current experience entry
            if company or title:
                claims.append(make_claim(
                    source, record_id, "experience",
                    {"company": company, "title": title, "start": None,
                     "end": "present", "summary": None},
                    "recruiter_csv.current_role", f"row={i};current_role",
                ))
        return claims
