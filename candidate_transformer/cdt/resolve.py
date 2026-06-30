"""Identity resolution -- CONSERVATIVE (design doc section 5).

Tiered match keys:
  A: email / E.164 phone / github|linkedin handle -- any ONE shared key merges.
  B: name + {company | school | location} -- needs TWO B attributes (or a fuzzy
     name + two attributes). Name alone (Tier C) NEVER merges.

Two entities that BOTH carry Tier-A keys but share NONE are assumed to be
distinct people and are blocked from merging even if their Tier-B attributes
line up ("a false merge is as dangerous as wrong-but-confident").

Union-find over high-confidence edges. Tier-B candidate pairs are generated only
(a) within *selective* blocks (company / school -- never a low-cardinality city
or country block) and (b) where at least one endpoint has NO Tier-A key. Two
both-keyed entities can never merge in Tier B anyway -- they are either already
unioned by a shared Tier-A key or kept apart by the conflicting-Tier-A guard --
so skipping those pairs is exact, and it keeps the common case (everyone has an
email/phone) strictly linear instead of O(n^2). A per-block cap degrades
gracefully on pathological input.
"""
from __future__ import annotations

import difflib
import uuid
from dataclasses import dataclass, field

from .model import NormalizedClaim

# Fixed namespace so candidate_id (UUIDv5) is deterministic, never random.
_NAMESPACE = uuid.UUID("6f0b8d2e-1c3a-5b7d-9e11-2a4c6e8f0a13")
_FUZZY_NAME = 0.90

# Tier-B blocking is generated ONLY on selective (higher-cardinality) attributes.
# Low-cardinality attributes (city / country) are confirming-only: they still
# count toward the >=2 shared-attribute rule, but a single shared city must never
# create an O(n^2) candidate-pair block. See resolve() for the full rationale.
_SELECTIVE_BLOCK_ATTRS = ("company", "school")

# Hard safety cap: if a single selective block is larger than this we skip its
# Tier-B pairing and log a "degraded" note instead of going quadratic. With the
# keyless-only rule below this almost never triggers on real data; it is a
# graceful-degradation backstop, never a crash (design: robust + scale).
_MAX_BLOCK = 2000


def effective_path(nc: NormalizedClaim) -> str:
    return nc.meta.get("resolved_path", nc.claim.path)


@dataclass
class Entity:
    record_id: str
    claims: list = field(default_factory=list)
    name_key: str | None = None
    name_display: str | None = None
    emails: set = field(default_factory=set)
    phones: set = field(default_factory=set)
    handles: set = field(default_factory=set)   # ("github"|"linkedin", handle)
    companies: set = field(default_factory=set)
    schools: set = field(default_factory=set)
    countries: set = field(default_factory=set)
    cities: set = field(default_factory=set)

    @property
    def tier_a(self) -> set:
        return set(self.emails) | set(self.phones) | {f"{k}:{v}" for k, v in self.handles}

    def b_attrs(self) -> set:
        attrs = set()
        attrs |= {("company", c) for c in self.companies if c}
        attrs |= {("school", s) for s in self.schools if s}
        attrs |= {("country", c) for c in self.countries if c}
        attrs |= {("city", c) for c in self.cities if c}
        return attrs


def build_entities(claims: list[NormalizedClaim]) -> dict[str, Entity]:
    ents: dict[str, Entity] = {}
    for nc in claims:
        rid = nc.claim.record_id
        ent = ents.setdefault(rid, Entity(record_id=rid))
        ent.claims.append(nc)
        if not nc.ok:
            continue
        path = effective_path(nc)
        if path == "full_name":
            if ent.name_key is None or len(str(nc.value)) > len(str(ent.name_display or "")):
                ent.name_key = nc.match_key
                ent.name_display = nc.value
        elif path == "emails":
            ent.emails.add(nc.match_key)
        elif path == "phones":
            ent.phones.add(nc.match_key)
        elif path in ("links.github", "links.linkedin"):
            h = nc.meta.get("handle")
            if h:
                ent.handles.add((path.split(".")[1], h.casefold()))
        elif path == "location.country":
            ent.countries.add(nc.match_key)
        elif path == "location.city":
            ent.cities.add(nc.match_key)
        elif path == "experience":
            ck = nc.meta.get("company_key")
            if ck:
                ent.companies.add(ck)
        elif path == "education":
            ik = nc.meta.get("institution_key")
            if ik:
                ent.schools.add(ik)
    return ents


class _DSU:
    def __init__(self, ids):
        self.parent = {i: i for i in ids}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        # union by stable id ordering for determinism
        if ra < rb:
            self.parent[rb] = ra
        else:
            self.parent[ra] = rb


def _fuzzy_name(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def resolve(entities: dict[str, Entity], log=None) -> list[list[str]]:
    """Return groups of record_ids, each group = one candidate.

    Deterministic: ids are sorted everywhere and union-by-min keeps roots stable.
    ``log`` is an optional ``callable(str)`` (e.g. ``ledger.log``) used to surface
    graceful-degradation notes; resolution itself never raises.
    """
    _log = log if callable(log) else (lambda _msg: None)
    ids = sorted(entities.keys())
    dsu = _DSU(ids)

    # --- Tier A: union within each shared high-confidence key block --------
    a_index: dict[str, list[str]] = {}
    for rid in ids:
        for key in entities[rid].tier_a:
            a_index.setdefault(key, []).append(rid)
    for key, members in a_index.items():
        members.sort()
        for other in members[1:]:
            dsu.union(members[0], other)

    # --- Tier B: conservative fuzzy merge, near-linear by construction ------
    # Only entities WITHOUT a Tier-A key can be merged here (see module docs), so
    # we generate candidate pairs only when at least one endpoint is keyless. When
    # every record carries an email/phone/handle this set is empty and Tier B
    # costs nothing -- the realistic case is strictly linear.
    keyless = {rid for rid in ids if not entities[rid].tier_a}
    candidate_pairs: set[tuple[str, str]] = set()
    if keyless:
        # Block ONLY on selective attributes; city/country stay confirming-only.
        b_index: dict[tuple, list[str]] = {}
        for rid in ids:
            for attr in entities[rid].b_attrs():
                if attr[0] in _SELECTIVE_BLOCK_ATTRS:
                    b_index.setdefault(attr, []).append(rid)
        for attr, members in b_index.items():
            members = sorted(set(members))
            if len(members) > _MAX_BLOCK:
                _log(f"resolve: selective block {attr!r} has {len(members)} members "
                     f"(> cap {_MAX_BLOCK}); Tier-B pairing skipped for it (degraded, no crash)")
                continue
            block_keyless = [m for m in members if m in keyless]
            for i, a in enumerate(block_keyless):
                for b in block_keyless[i + 1:]:           # keyless x keyless
                    candidate_pairs.add((a, b))
                for b in members:                          # keyless x keyed
                    if b not in keyless:
                        candidate_pairs.add((a, b) if a < b else (b, a))

    for a, b in sorted(candidate_pairs):
        ea, eb = entities[a], entities[b]
        # Belt-and-suspenders: at least one endpoint is keyless by construction,
        # so this conflicting-Tier-A guard is now an invariant check, not a filter.
        if ea.tier_a and eb.tier_a and not (ea.tier_a & eb.tier_a):
            continue
        shared_b = ea.b_attrs() & eb.b_attrs()
        if len(shared_b) < 2:
            continue
        if ea.name_key and eb.name_key:
            if ea.name_key == eb.name_key or _fuzzy_name(ea.name_key, eb.name_key) >= _FUZZY_NAME:
                dsu.union(a, b)

    # --- collect groups ----------------------------------------------------
    groups: dict[str, list[str]] = {}
    for rid in ids:
        groups.setdefault(dsu.find(rid), []).append(rid)
    out = [sorted(v) for v in groups.values()]
    out.sort(key=lambda g: g[0])
    return out


def candidate_id(entities: dict[str, Entity], group: list[str]) -> str:
    """Deterministic UUIDv5 over canonical identity keys for the merged group."""
    a_keys, names, battrs = set(), set(), set()
    for rid in group:
        e = entities[rid]
        a_keys |= e.tier_a
        if e.name_key:
            names.add(e.name_key)
        battrs |= {f"{k}={v}" for k, v in e.b_attrs()}
    if a_keys:
        ident = "|".join(sorted(a_keys))
    elif names:
        ident = "name:" + "|".join(sorted(names)) + ";b:" + "|".join(sorted(battrs))
    else:
        ident = "records:" + "|".join(sorted(group))
    return str(uuid.uuid5(_NAMESPACE, ident))
