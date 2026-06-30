"""Reconcile + Confidence with the Honesty Gate (design doc section 6).

For SINGLE-valued fields: an evidence-weighted vote. Normalized-equivalent
values collapse first, so the deterministic tie-break only ever picks a stable
representative AMONG EQUIVALENTS -- never between materially-different values.
When materially-different values remain within the tie margin, the honesty gate
fires: NULL for high-stakes identity/contact fields, otherwise demote to a floor
confidence. Losers are always kept in ``alternatives`` (recorded dissent).

For MULTI-valued fields: union + entity-level dedupe (emails/phones by canonical
key; skills by canonical name; experience/education by canonical org + date
overlap + title similarity).

Pure and deterministic: stable sorts, fixed rounding, data-derived recency.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from .calibrate import (contribution, field_confidence, noisy_or,
                        overall_confidence, recency_map)
from .model import (HIGH_STAKES_FIELDS, Conflict, Source, Value, round_conf)
from .normalize import NormalizeContext, phones as _phones
from .resolve import effective_path
from .vocab import reliability

TIE_MARGIN = 0.10   # relative margin below which two distinct values "tie"


def _label(source_type: str, source_id: str) -> str:
    return f"{source_type}:{source_id}"


@dataclass
class Assembled:
    candidate_id: str
    view: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    conflicts: list = field(default_factory=list)
    provenance: list = field(default_factory=list)
    field_confs: dict = field(default_factory=dict)
    present: set = field(default_factory=set)
    overall_confidence: float = 0.0


class Reconciler:
    def __init__(self, ctx: NormalizeContext, tie_margin: float = TIE_MARGIN):
        self.ctx = ctx
        self.tie_margin = tie_margin

    # ------------------------------------------------------------------ #
    # single-valued evidence vote
    # ------------------------------------------------------------------ #
    def _groups(self, field_path: str, ncs: list):
        """Collapse claims into normalized-equivalent groups with scores."""
        groups: dict = {}
        for nc in ncs:
            if not nc.ok:
                continue
            w = reliability(nc.claim.source_type, field_path)
            c = contribution(w, nc.quality, self._rec.get(nc.claim.source_id, 1.0))
            g = groups.setdefault(nc.match_key, {
                "reps": [], "contribs": {}, "labels": set(), "source_ids": set()})
            # source-origin dedupe: keep the MAX contribution per source_id
            if c > g["contribs"].get(nc.claim.source_id, -1.0):
                g["contribs"][nc.claim.source_id] = c
            g["reps"].append((c, w, nc.claim.source_id, nc.value))
            g["labels"].add(_label(nc.claim.source_type, nc.claim.source_id))
            g["source_ids"].add(nc.claim.source_id)
        out = []
        for key, g in groups.items():
            score = noisy_or(list(g["contribs"].values()))
            # representative: highest (contribution, reliability), tie -> source_id, value
            rep = sorted(g["reps"], key=lambda t: (-t[0], -t[1], t[2], str(t[3])))[0]
            out.append({
                "key": key, "display": rep[3], "score": score,
                "labels": sorted(g["labels"]), "source_ids": sorted(g["source_ids"]),
            })
        # rank: score desc, then stable lexicographic tie-break
        out.sort(key=lambda d: (-d["score"], str(d["display"]), d["labels"]))
        return out

    def _vote_scalar(self, field_path: str, ncs: list):
        groups = self._groups(field_path, ncs)
        if not groups:
            return None, None
        top = groups[0]
        total = sum(g["score"] for g in groups) or 1.0
        agreement = top["score"] / total
        high_stakes = field_path in HIGH_STAKES_FIELDS

        if len(groups) == 1:
            conf = field_confidence(top["score"], agreement, demoted=False)
            method = "single_source" if len(top["source_ids"]) == 1 else "corroborated"
            val = Value(top["display"], conf, top["labels"], method, [])
            return val, None

        second = groups[1]
        margin = (top["score"] - second["score"]) / top["score"] if top["score"] > 0 else 0.0
        alts_all = [{"value": g["display"], "score": round_conf(g["score"]),
                     "sources": g["labels"]} for g in groups]

        if margin < self.tie_margin:
            if high_stakes:
                # prefer-empty-over-wrong: emit null, keep every value visible.
                val = Value(None, 0.0, [], "honesty_gate_null", alts_all)
                conflict = Conflict(field_path, "honesty_gate_null", None, alts_all, margin)
                return val, conflict
            conf = field_confidence(top["score"], agreement, demoted=True)
            val = Value(top["display"], conf, top["labels"], "demoted_conflict", alts_all[1:])
            conflict = Conflict(field_path, "demoted", top["display"], alts_all[1:], margin)
            return val, conflict

        # clear winner; losers recorded as (non-conflicting) dissent
        conf = field_confidence(top["score"], agreement, demoted=False)
        val = Value(top["display"], conf, top["labels"], "evidence_vote", alts_all[1:])
        return val, None

    # ------------------------------------------------------------------ #
    # public entry
    # ------------------------------------------------------------------ #
    def reconcile(self, claims: list, sources: list[Source]) -> Assembled:
        self._rec = recency_map(sources)
        self._src_country_default = self.ctx.default_country
        by_path: dict[str, list] = {}
        for nc in claims:
            by_path.setdefault(effective_path(nc), []).append(nc)

        view: dict = {}
        meta: dict = {}
        conflicts: list = []
        provenance: list = []
        field_confs: dict = {}
        present: set = set()

        def record(field_path, val: Value):
            meta[field_path] = val.to_dict()
            if val.value is not None:
                field_confs[field_path] = val.confidence
                present.add(field_path)
                for lbl in val.sources:
                    provenance.append({"field": field_path, "source": lbl, "method": val.method})

        # ---- scalar identity/headline fields --------------------------
        for fp in ["full_name", "headline"]:
            if fp in by_path:
                val, conf = self._vote_scalar(fp, by_path[fp])
                if val:
                    record(fp, val)
                    if conf:
                        conflicts.append(conf)
        view["full_name"] = meta.get("full_name", {}).get("value")
        view["headline"] = meta.get("headline", {}).get("value")

        # ---- location (object of scalars) -----------------------------
        location = {"city": None, "region": None, "country": None}
        for sub in ["location.city", "location.region", "location.country"]:
            if sub in by_path:
                val, conf = self._vote_scalar(sub, by_path[sub])
                if val:
                    record(sub, val)
                    location[sub.split(".")[1]] = val.value
                    if conf:
                        conflicts.append(conf)
        view["location"] = location
        candidate_country = location["country"] if isinstance(location["country"], str) and len(location["country"]) == 2 else None

        # ---- links (object) -------------------------------------------
        links = {"linkedin": None, "github": None, "portfolio": None, "other": []}
        for sub in ["links.linkedin", "links.github", "links.portfolio"]:
            if sub in by_path:
                val, conf = self._vote_scalar(sub, by_path[sub])
                if val:
                    record(sub, val)
                    links[sub.split(".")[1]] = val.value
                    if conf:
                        conflicts.append(conf)
        if "links.other" in by_path:
            others, lbls = self._dedupe_simple(by_path["links.other"])
            links["other"] = others
            if others:
                meta["links.other"] = {"value": others, "confidence": None,
                                       "sources": lbls, "method": "union", "alternatives": []}
        view["links"] = links

        # ---- emails / phones (multi, high-stakes per item) ------------
        view["emails"] = self._list_contacts("emails", by_path.get("emails", []),
                                              meta, field_confs, present, provenance)
        view["phones"] = self._list_phones(by_path.get("phones", []), candidate_country,
                                            meta, field_confs, present, provenance)

        # ---- skills (multi, canonical) --------------------------------
        view["skills"] = self._list_skills(by_path.get("skills", []), meta, field_confs,
                                            present, provenance)

        # ---- experience / education (multi, entity dedupe) ------------
        view["experience"] = self._list_experience(by_path.get("experience", []), meta,
                                                    field_confs, present, provenance)
        view["education"] = self._list_education(by_path.get("education", []), meta,
                                                 field_confs, present, provenance)

        # ---- years_experience (derived vs stated) ---------------------
        ye = self._years_experience(by_path.get("years_experience", []), view["experience"])
        if ye is not None:
            val, conf = ye
            record("years_experience", val)
            view["years_experience"] = val.value
            if conf:
                conflicts.append(conf)
        else:
            view["years_experience"] = None

        overall = overall_confidence(field_confs, present, len(conflicts))
        return Assembled(candidate_id="", view=view, meta=meta, conflicts=conflicts,
                         provenance=provenance, field_confs=field_confs, present=present,
                         overall_confidence=overall)

    # ------------------------------------------------------------------ #
    # multi-valued helpers
    # ------------------------------------------------------------------ #
    def _dedupe_simple(self, ncs: list):
        groups = self._groups("links.other", ncs)
        return [g["display"] for g in groups], sorted({l for g in groups for l in g["labels"]})

    def _list_contacts(self, field_path, ncs, meta, field_confs, present, provenance):
        groups = self._groups(field_path, ncs)
        if not groups:
            return []
        values = [g["display"] for g in groups]   # already ranked by score
        best = groups[0]["score"]
        all_labels = sorted({l for g in groups for l in g["labels"]})
        meta[field_path] = {"value": values, "confidence": round_conf(best),
                            "sources": all_labels, "method": "union_dedupe",
                            "alternatives": []}
        field_confs[field_path] = round_conf(best)
        present.add(field_path)
        for g in groups:
            for lbl in g["labels"]:
                provenance.append({"field": field_path, "source": lbl, "method": "union_dedupe"})
        return values

    def _list_phones(self, ncs, candidate_country, meta, field_confs, present, provenance):
        # Re-normalise raw phones with candidate country (region priority order).
        renorm = []
        for nc in ncs:
            raw = nc.claim.raw
            val, key, q, why, m = _phones.normalize_phone(
                raw, candidate_country=candidate_country,
                default_country=self._src_country_default)
            if val is None:
                continue
            from .model import NormalizedClaim
            renorm.append(NormalizedClaim(nc.claim, val, key, q, "normalize_phone_e164", why, m))
        return self._list_contacts("phones", renorm, meta, field_confs, present, provenance)

    def _list_skills(self, ncs, meta, field_confs, present, provenance):
        agg: dict = {}
        for nc in ncs:
            if not nc.ok:
                continue
            w = reliability(nc.claim.source_type, "skills")
            c = contribution(w, nc.quality, self._rec.get(nc.claim.source_id, 1.0))
            a = agg.setdefault(nc.match_key, {
                "name": nc.value, "contribs": {}, "labels": set(),
                "canonical": bool(nc.meta.get("canonical"))})
            if c > a["contribs"].get(nc.claim.source_id, -1.0):
                a["contribs"][nc.claim.source_id] = c
            a["labels"].add(_label(nc.claim.source_type, nc.claim.source_id))
            a["canonical"] = a["canonical"] or bool(nc.meta.get("canonical"))
        skills = []
        for key, a in agg.items():
            conf = round_conf(noisy_or(list(a["contribs"].values())))
            skills.append({"name": a["name"], "confidence": conf,
                           "sources": sorted(a["labels"]), "canonical": a["canonical"]})
        skills.sort(key=lambda s: (-s["confidence"], s["name"].casefold()))
        if skills:
            best = max(s["confidence"] for s in skills)
            meta["skills"] = {"value": [s["name"] for s in skills], "confidence": best,
                              "sources": sorted({l for s in skills for l in s["sources"]}),
                              "method": "canonical_union", "alternatives": []}
            field_confs["skills"] = best
            present.add("skills")
            for s in skills:
                for lbl in s["sources"]:
                    provenance.append({"field": "skills", "source": lbl,
                                       "method": "canonical_union"})
        return skills

    def _list_experience(self, ncs, meta, field_confs, present, provenance):
        entries = []
        for nc in ncs:
            if not nc.ok:
                continue
            w = reliability(nc.claim.source_type, "experience")
            c = contribution(w, nc.quality, self._rec.get(nc.claim.source_id, 1.0))
            v = dict(nc.value)
            v["_contrib"] = {nc.claim.source_id: c}
            v["_labels"] = {_label(nc.claim.source_type, nc.claim.source_id)}
            v["_w"] = c
            entries.append(v)
        merged = _merge_experience(entries)
        out = []
        for e in merged:
            conf = round_conf(noisy_or(list(e["_contrib"].values())))
            out.append({"company": e.get("company"), "title": e.get("title"),
                        "start": e.get("start"), "end": e.get("end"),
                        "summary": e.get("summary"), "_confidence": conf,
                        "_sources": sorted(e["_labels"])})
        out.sort(key=lambda e: (e["start"] or "", e["end"] or ""), reverse=True)
        clean = [{k: v for k, v in e.items() if not k.startswith("_")} for e in out]
        if clean:
            best = max(e["_confidence"] for e in out)
            meta["experience"] = {"value": clean, "confidence": best,
                                  "sources": sorted({l for e in out for l in e["_sources"]}),
                                  "method": "entity_dedupe", "alternatives": []}
            field_confs["experience"] = best
            present.add("experience")
            for e in out:
                for lbl in e["_sources"]:
                    provenance.append({"field": "experience", "source": lbl,
                                       "method": "entity_dedupe"})
        return clean

    def _list_education(self, ncs, meta, field_confs, present, provenance):
        groups: dict = {}
        for nc in ncs:
            if not nc.ok:
                continue
            w = reliability(nc.claim.source_type, "education")
            c = contribution(w, nc.quality, self._rec.get(nc.claim.source_id, 1.0))
            g = groups.setdefault(nc.match_key, {"v": dict(nc.value), "contribs": {}, "labels": set()})
            if c > g["contribs"].get(nc.claim.source_id, -1.0):
                g["contribs"][nc.claim.source_id] = c
            g["labels"].add(_label(nc.claim.source_type, nc.claim.source_id))
            # merge: prefer the most complete field values
            for k in ("institution", "degree", "field", "end_year"):
                if not g["v"].get(k) and nc.value.get(k):
                    g["v"][k] = nc.value[k]
        out = []
        for key, g in groups.items():
            conf = round_conf(noisy_or(list(g["contribs"].values())))
            out.append({**g["v"], "_confidence": conf, "_sources": sorted(g["labels"])})
        out.sort(key=lambda e: (e.get("end_year") or 0), reverse=True)
        clean = [{"institution": e.get("institution"), "degree": e.get("degree"),
                  "field": e.get("field"), "end_year": e.get("end_year")} for e in out]
        if clean:
            best = max(e["_confidence"] for e in out)
            meta["education"] = {"value": clean, "confidence": best,
                                 "sources": sorted({l for e in out for l in e["_sources"]}),
                                 "method": "entity_dedupe", "alternatives": []}
            field_confs["education"] = best
            present.add("education")
            for e in out:
                for lbl in e["_sources"]:
                    provenance.append({"field": "education", "source": lbl, "method": "entity_dedupe"})
        return clean

    def _years_experience(self, ncs, experience):
        stated_val, stated_conf = self._vote_scalar("years_experience", ncs)
        derived = _derive_years(experience)
        if stated_val and stated_val.value is not None:
            v = stated_val
            if derived is not None and abs(derived - v.value) > 2:
                alt = [{"value": derived, "score": None, "sources": ["derived:spans"]}]
                demoted = Value(v.value, round_conf(min(v.confidence, 0.5)),
                                v.sources, "stated_conflicts_derived", alt)
                return demoted, Conflict("years_experience", "demoted", v.value, alt, None)
            return v, stated_conf
        if derived is not None:
            return Value(derived, 0.6, ["derived:spans"], "derived_from_spans", []), None
        return None


# ---------------------------------------------------------------------- #
# module-level pure helpers for experience merge / years derivation
# ---------------------------------------------------------------------- #
def _month_idx(date_str, end=False):
    if not date_str:
        return None
    y = int(date_str[:4])
    if len(date_str) >= 7 and date_str[5:7].isdigit():
        m = int(date_str[5:7])
    else:
        m = 12 if end else 1
    return y * 12 + (m - 1)


def _overlaps(a, b):
    a0 = _month_idx(a.get("start")) or -10**9
    a1 = _month_idx(a.get("end"), end=True)
    a1 = 10**9 if (a1 is None or a.get("is_current")) else a1
    b0 = _month_idx(b.get("start")) or -10**9
    b1 = _month_idx(b.get("end"), end=True)
    b1 = 10**9 if (b1 is None or b.get("is_current")) else b1
    return a0 <= b1 and b0 <= a1


def _same_job(a, b, company_key):
    """Two same-company entries are the same job if their spans overlap and
    titles are compatible (similar, substring, empty, or both current)."""
    if not company_key or not _overlaps(a, b):
        return False
    ta = (a.get("title") or "").casefold()
    tb = (b.get("title") or "").casefold()
    if not ta or not tb:
        return True
    if ta in tb or tb in ta:
        return True
    if a.get("is_current") and b.get("is_current"):
        return True   # cannot hold two concurrent current roles at one company
    return difflib.SequenceMatcher(None, ta, tb).ratio() >= 0.6


def _merge_experience(entries):
    by_company: dict = {}
    for e in entries:
        by_company.setdefault((e.get("company") or "").casefold(), []).append(e)
    clusters: list = []
    for key, group in by_company.items():
        local: list = []
        for e in group:
            for cl in local:
                if _same_job(cl[0], e, key):
                    cl.append(e)
                    break
            else:
                local.append([e])
        clusters.extend(local)
    return [_reduce_cluster(c) for c in clusters]


def _reduce_cluster(cluster):
    """Reduce a cluster of same-job entries to one, choosing fields by source weight."""
    def best(field_name):
        cands = [(e.get("_w", 0.0), len(str(e.get(field_name) or "")), e.get(field_name))
                 for e in cluster if e.get(field_name)]
        return sorted(cands, key=lambda t: (-t[0], -t[1]))[0][2] if cands else None

    starts = [e.get("start") for e in cluster if e.get("start")]
    is_current = any(e.get("is_current") for e in cluster)
    ends = [e.get("end") for e in cluster if e.get("end") and e.get("end") != "present"]
    contrib: dict = {}
    labels: set = set()
    for e in cluster:
        contrib.update(e.get("_contrib", {}))
        labels |= e.get("_labels", set())
    return {
        "company": best("company"),
        "title": best("title"),
        "start": min(starts) if starts else None,
        "end": None if is_current else (max(ends) if ends else None),
        "summary": best("summary"),
        "is_current": is_current,
        "_contrib": contrib,
        "_labels": labels,
    }


def _derive_years(experience):
    intervals = []
    for e in experience:
        s = _month_idx(e.get("start"))
        en = _month_idx(e.get("end"), end=True)
        if s is None or en is None:   # open/current or unknown -> cannot measure (no wall-clock)
            continue
        if en >= s:
            intervals.append((s, en))
    if not intervals:
        return None
    intervals.sort()
    total, cur_s, cur_e = 0, *intervals[0]
    for s, e in intervals[1:]:
        if s <= cur_e + 1:
            cur_e = max(cur_e, e)
        else:
            total += (cur_e - cur_s + 1)
            cur_s, cur_e = s, e
    total += (cur_e - cur_s + 1)
    return round(total / 12.0)
