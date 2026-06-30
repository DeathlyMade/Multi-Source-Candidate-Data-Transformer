"""Core data model for the Multi-Source Candidate Data Transformer.

Design doc section 2 (ARCHITECTURE - EVIDENCE LEDGER + PROJECTION):

  * Sources are content-addressed.
  * Each adapter appends typed, source-located ``Claim`` records to an
    append-only Evidence Ledger.
  * Every canonical value is a tracked envelope
    ``Value<T> = {value, confidence, sources[], method, alternatives[]}`` so
    provenance, confidence and conflicts are uniform on *every* field.

These objects are intentionally small, frozen, and free of any wall-clock or
random state -- the whole engine is a deterministic reduce over them.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

# Rounding is fixed and centralised so confidence numbers are byte-for-byte
# reproducible across runs and machines (design doc section 8: Deterministic).
CONFIDENCE_NDIGITS = 3


def round_conf(x: float) -> float:
    """Deterministic, fixed-precision rounding for every confidence value."""
    return round(float(x), CONFIDENCE_NDIGITS)


@dataclass(frozen=True)
class Source:
    """A content-addressed input source.

    ``source_id = hash(bytes + type)`` is computed once per source so identical
    sources dedupe and runs are reproducible & cacheable (design doc section 2).
    """

    source_id: str          # content address, e.g. "sha256:ab12...".
    source_type: str        # recruiter_csv | ats_json | github | resume | recruiter_notes
    origin: str             # human-facing origin (filename / url), for logs only
    ok: bool = True         # False if the adapter failed to ingest it
    error: Optional[str] = None
    ts: Optional[str] = None  # optional source-provided timestamp (YYYY-MM-DD), used for recency

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Claim:
    """A typed, source-located assertion about one canonical field.

    ``{path, raw, source_id, type, extractor, locator}`` exactly as specified in
    the design doc, plus a ``record_id`` so identity resolution can group the
    claims that belong to the same source-record (e.g. one CSV row) before it
    merges records across sources.
    """

    path: str               # canonical field path, e.g. "emails", "location.country"
    raw: Any                # raw extracted value (pre-normalisation)
    source_id: str          # content address of the originating source
    source_type: str        # source group/type
    extractor: str          # which extractor produced it, e.g. "regex.email"
    locator: str            # exact traceability: CSV row / JSON pointer / line / span
    record_id: str          # which source-record this claim belongs to
    ts: Optional[str] = None  # source timestamp inherited from Source, for recency

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class NormalizedClaim:
    """A claim after the (total, pure) Normalize + Canonicalize phase.

    Normalizers never throw: they return ``value | None`` plus a ``reason``.
    ``quality`` in [0,1] feeds the q(s,v,f) term of the reconciliation vote.
    ``match_key`` is the canonical key used for equivalence-collapse and dedupe
    (e.g. the dot/plus-stripped email, the E.164 phone, the canonical skill).
    """

    claim: Claim
    value: Any                 # normalized value (None if it could not be normalized)
    match_key: Any             # canonical equivalence key (== value for most fields)
    quality: float             # normalization quality in [0,1]
    method: str                # normalization/canonicalization method used
    reason: str = ""           # why the value is null / down-precisioned, etc.
    meta: dict = field(default_factory=dict)  # extra (date precision, is_current, ext...)

    @property
    def ok(self) -> bool:
        return self.value is not None


@dataclass
class Value:
    """The canonical envelope ``Value<T>`` carried by every reconciled field."""

    value: Any
    confidence: float
    sources: list[str] = field(default_factory=list)   # source_ids that support the winner
    method: str = ""                                    # how the value was decided
    alternatives: list[dict] = field(default_factory=list)  # recorded dissent

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "confidence": round_conf(self.confidence),
            "sources": list(self.sources),
            "method": self.method,
            "alternatives": list(self.alternatives),
        }


@dataclass
class Conflict:
    """A demoted/nulled field, kept visible (design doc: recorded dissent)."""

    field: str
    reason: str                 # "honesty_gate_null" | "demoted" | ...
    winner: Any
    alternatives: list[dict] = field(default_factory=list)
    margin: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "reason": self.reason,
            "winner": self.winner,
            "alternatives": list(self.alternatives),
            "margin": None if self.margin is None else round_conf(self.margin),
        }


# The fixed canonical schema (design doc image 2 "Default output schema").
# Single-valued (scalar/object) fields vs multi-valued (list) fields drive
# whether reconciliation uses the honesty-gated vote or the union+dedupe path.
SCALAR_FIELDS = [
    "full_name",
    "headline",
    "years_experience",
    "location.city",
    "location.region",
    "location.country",
    "links.linkedin",
    "links.github",
    "links.portfolio",
]
LIST_FIELDS = ["emails", "phones", "links.other", "skills", "experience", "education"]

# High-stakes identity/contact fields: on a true tie we emit *null* rather than
# guess (design doc section 6 honesty gate). Lower-stakes fields demote instead.
HIGH_STAKES_FIELDS = {
    "full_name",
    "emails",
    "phones",
    "location.country",
    "links.linkedin",
    "links.github",
}

# Importance weights for the overall-confidence rollup (design doc section 6:
# "overall = importance-weighted mean").
FIELD_IMPORTANCE = {
    "full_name": 3.0,
    "emails": 3.0,
    "phones": 2.0,
    "location.country": 1.5,
    "headline": 1.0,
    "years_experience": 1.0,
    "skills": 2.0,
    "experience": 2.0,
    "education": 1.5,
    "links.linkedin": 1.0,
    "links.github": 1.0,
}
